import base64
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


class StreamParser:
    """Parse Ollama streaming text, splitting <think>...</think> from normal output."""

    def __init__(self) -> None:
        self.in_think = False
        self._buf = ""

    def feed(self, chunk: str) -> list[dict]:
        events: list[dict] = []
        self._buf += chunk

        while True:
            if not self.in_think:
                idx = self._buf.find("<think>")
                if idx == -1:
                    safe = self._buf[: max(0, len(self._buf) - 7)]
                    if safe:
                        events.append({"type": "token", "text": safe})
                        self._buf = self._buf[len(safe):]
                    break
                else:
                    if idx > 0:
                        events.append({"type": "token", "text": self._buf[:idx]})
                    self._buf = self._buf[idx + len("<think>"):]
                    self.in_think = True
            else:
                idx = self._buf.find("</think>")
                if idx == -1:
                    safe = self._buf[: max(0, len(self._buf) - 9)]
                    if safe:
                        events.append({"type": "think", "text": safe})
                        self._buf = self._buf[len(safe):]
                    break
                else:
                    if idx > 0:
                        events.append({"type": "think", "text": self._buf[:idx]})
                    self._buf = self._buf[idx + len("</think>"):]
                    self.in_think = False

        return events

    def flush(self) -> list[dict]:
        if not self._buf:
            return []
        event_type = "think" if self.in_think else "token"
        events = [{"type": event_type, "text": self._buf}]
        self._buf = ""
        return events


class OllamaClient:
    def __init__(self, resource=None, base_url: str | None = None) -> None:
        """resource: optional spooler Resource (remote-ollama). When set, every
        request acquires its semaphore for the duration of the HTTP call only —
        no job ever holds it across a pause checkpoint, so lane pauses cannot
        deadlock, and total server concurrency is capped across ALL lanes."""
        self._client = httpx.AsyncClient(timeout=settings.ollama_timeout_sec)
        self._resource = resource
        self.base_url = (base_url or settings.ollama_url).rstrip("/")

    def set_base_url(self, url: str) -> None:
        self.base_url = (url or settings.ollama_url).rstrip("/")

    def set_resource(self, resource) -> None:
        self._resource = resource

    @asynccontextmanager
    async def _acquire(self):
        if self._resource is None:
            yield
        else:
            async with self._resource.acquire():
                yield

    def _raise_with_body(self, r) -> None:
        """Like raise_for_status() but logs and re-raises with the Ollama error body."""
        if r.is_error:
            try:
                body = r.json()
                msg = body.get("error") or str(body)
            except Exception:
                msg = r.text[:500]
            logger.error("[ollama] %s %s — %s", r.status_code, r.url, msg)
            r.raise_for_status()

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        async with self._acquire():
            r = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": model or settings.embed_model, "input": text},
            )
        r.raise_for_status()
        return r.json()["embeddings"][0]

    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Embed multiple texts in a single Ollama API call."""
        async with self._acquire():
            r = await self._client.post(
                f"{self.base_url}/api/embed",
                json={"model": model or settings.embed_model, "input": texts},
            )
        r.raise_for_status()
        return r.json()["embeddings"]

    @staticmethod
    def _with_think(payload: dict, think: bool | str | None) -> dict:
        """Attach Ollama native `think` when requested (Gemma 4 / reasoning models)."""
        if think is not None:
            payload["think"] = think
        return payload

    @staticmethod
    def _stream_chunk_events(parser: StreamParser, data: dict) -> list[dict]:
        """Yield think/token events from one Ollama stream JSON object."""
        events: list[dict] = []
        thinking = data.get("thinking") or ""
        if thinking:
            events.append({"type": "think", "text": thinking})
        chunk = data.get("response", "")
        if chunk:
            events.extend(parser.feed(chunk))
        return events

    async def generate_vlm(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> str:
        images_b64 = [base64.b64encode(b).decode() for b in image_bytes_list]
        payload = self._with_think(
            {
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "images": images_b64,
                "stream": False,
                "options": options or {},
            },
            think,
        )
        async with self._acquire():
            r = await self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
        self._raise_with_body(r)
        return r.json()["response"]

    async def generate_vlm_stream(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[dict, None]:
        images_b64 = [base64.b64encode(b).decode() for b in image_bytes_list]
        parser = StreamParser()
        payload = self._with_think(
            {
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "images": images_b64,
                "stream": True,
                "options": options or {},
            },
            think,
        )

        async with self._acquire(), self._client.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=settings.ollama_timeout_sec,
        ) as resp:
            if resp.is_error:
                body = await resp.aread()
                try:
                    msg = json.loads(body).get("error") or body[:500].decode()
                except Exception:
                    msg = body[:500].decode()
                logger.error("[ollama] %s %s — %s", resp.status_code, resp.url, msg)
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                for event in self._stream_chunk_events(parser, data):
                    yield event
                if data.get("done"):
                    for event in parser.flush():
                        yield event
                    return

        for event in parser.flush():
            yield event

    async def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
    ) -> str:
        """Generate text without vision inputs (text-only LLM call)."""
        # num_predict=-1 means unlimited; callers can override via options.
        # Without this, Ollama uses the model Modelfile default (often 128–512
        # tokens) and will silently truncate structured JSON responses.
        merged_options = {"num_predict": -1, **(options or {})}
        payload: dict = self._with_think(
            {
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "stream": False,
                "options": merged_options,
            },
            think,
        )
        if fmt:
            payload["format"] = fmt
        async with self._acquire():
            r = await self._client.post(f"{self.base_url}/api/generate", json=payload)
        self._raise_with_body(r)
        data = r.json()
        text = str(data.get("response") or "")
        if "<think>" in text.lower():
            text = _THINK_BLOCK_RE.sub("", text)
        text = text.strip()
        if not text:
            # Qwen3.5 / reasoning models sometimes leave response empty while
            # still emitting usable JSON in the thinking channel.
            thinking = str(data.get("thinking") or "").strip()
            if thinking and "{" in thinking:
                logger.warning(
                    "[ollama] empty response from %s; recovering JSON from thinking",
                    payload.get("model"),
                )
                text = thinking
        return text

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream text generation without vision inputs."""
        parser = StreamParser()
        payload = self._with_think(
            {
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "stream": True,
                "options": {"num_predict": -1, **(options or {})},
            },
            think,
        )
        async with self._acquire(), self._client.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=settings.ollama_timeout_sec,
        ) as resp:
            if resp.is_error:
                body = await resp.aread()
                try:
                    msg = json.loads(body).get("error") or body[:500].decode()
                except Exception:
                    msg = body[:500].decode()
                logger.error("[ollama] %s %s — %s", resp.status_code, resp.url, msg)
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                for event in self._stream_chunk_events(parser, data):
                    yield event
                if data.get("done"):
                    for event in parser.flush():
                        yield event
                    return
        for event in parser.flush():
            yield event

    async def health(self, url: str | None = None) -> bool:
        base = (url or self.base_url).rstrip("/")
        try:
            r = await self._client.get(f"{base}/api/tags", timeout=5.0)
            if r.status_code != 200:
                return False
            return "models" in r.json()
        except Exception:
            return False

    async def list_models(self, url: str | None = None) -> list[str]:
        base = (url or self.base_url).rstrip("/")
        try:
            r = await self._client.get(f"{base}/api/tags", timeout=5.0)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    async def close(self) -> None:
        await self._client.aclose()

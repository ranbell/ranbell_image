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

    async def _generate_stream(
        self, payload: dict, *, model: str | None = None
    ) -> AsyncGenerator[dict, None]:
        """Shared /api/generate streaming loop for the text and vision callers.

        A reasoning model that runs out of token budget mid-thought returns an
        empty ``response`` with the whole answer stranded in the thinking
        channel. Callers keep only ``token`` events, so they would end up with
        nothing at all and go on to build a prompt out of an empty string.
        When the stream produced no token, the thinking text is re-emitted as
        one token event; ``_extract_generate_text`` already does the same for
        the non-streaming path.
        """
        parser = StreamParser()
        thinking_parts: list[str] = []
        saw_token = False

        def _track(events: list[dict]) -> list[dict]:
            nonlocal saw_token
            for ev in events:
                if ev["type"] == "token" and ev["text"]:
                    saw_token = True
                elif ev["type"] == "think":
                    thinking_parts.append(ev["text"])
            return events

        def _fallback() -> list[dict]:
            if saw_token:
                return []
            recovered = "".join(thinking_parts).strip()
            if not recovered:
                return []
            logger.warning(
                "[ollama] %s streamed no response text; recovering %d chars "
                "from the thinking channel",
                model or payload.get("model"),
                len(recovered),
            )
            return [{"type": "token", "text": recovered}]

        async with self._acquire(), self._client.stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=settings.ollama_timeout_sec,
        ) as resp:
            if resp.is_error:
                body = await resp.aread()
                try:
                    msg = json.loads(body).get("error") or body[:500].decode("utf-8", "replace")
                except Exception:
                    msg = body[:500].decode("utf-8", "replace")
                logger.error("[ollama] %s %s — %s", resp.status_code, resp.url, msg)
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                for event in _track(self._stream_chunk_events(parser, data)):
                    yield event
                if data.get("done"):
                    for event in _track(parser.flush()):
                        yield event
                    for event in _fallback():
                        yield event
                    return
        for event in _track(parser.flush()):
            yield event
        for event in _fallback():
            yield event

    async def generate_vlm(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
        system: str | None = None,
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
        if system:
            payload["system"] = system
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
        system: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        images_b64 = [base64.b64encode(b).decode() for b in image_bytes_list]
        model_name = model or settings.vlm_model
        # num_predict=-1 for the same reason generate_text sets it: without it a
        # Modelfile default (often 128–512) truncates the answer, and a model
        # that thinks first can spend the whole budget before writing a word.
        payload = self._with_think(
            {
                "model": model_name,
                "prompt": prompt,
                "images": images_b64,
                "stream": True,
                "options": {"num_predict": -1, **(options or {})},
                **({"system": system} if system else {}),
            },
            think,
        )
        async for event in self._generate_stream(payload, model=model_name):
            yield event

    @staticmethod
    def _extract_generate_text(data: dict, *, model: str | None) -> str:
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
                    model,
                )
                text = thinking
        return text

    @staticmethod
    def _extract_chat_text(data: dict, *, model: str | None) -> str:
        message = data.get("message") or {}
        text = str(message.get("content") or "")
        if "<think>" in text.lower():
            text = _THINK_BLOCK_RE.sub("", text)
        text = text.strip()
        if not text:
            thinking = str(message.get("thinking") or data.get("thinking") or "").strip()
            if thinking and "{" in thinking:
                logger.warning(
                    "[ollama] empty chat content from %s; recovering JSON from thinking",
                    model,
                )
                text = thinking
        return text

    async def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
        system: str | None = None,
    ) -> str:
        """Generate text without vision inputs (text-only LLM call)."""
        # num_predict=-1 means unlimited; callers can override via options.
        # Without this, Ollama uses the model Modelfile default (often 128–512
        # tokens) and will silently truncate structured JSON responses.
        merged_options = {"num_predict": -1, **(options or {})}
        model_name = model or settings.vlm_model
        payload: dict = self._with_think(
            {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": merged_options,
            },
            think,
        )
        if fmt:
            payload["format"] = fmt
        if system:
            payload["system"] = system
        async with self._acquire():
            r = await self._client.post(f"{self.base_url}/api/generate", json=payload)
        self._raise_with_body(r)
        return self._extract_generate_text(r.json(), model=model_name)

    async def unload(self, model: str | None = None) -> None:
        """Drop a model from VRAM now instead of waiting out its keep_alive.

        On a single 16GB card a 26B MoE holds ~13GB, which leaves ComfyUI too
        little to allocate a multi-image latent — the LLM and the checkpoint
        cannot both be resident. Callers that hand straight over to a render
        call this at the seam.
        """
        try:
            async with self._acquire():
                await self._client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model or settings.vlm_model, "keep_alive": 0},
                    timeout=30.0,
                )
        except Exception as exc:
            logger.warning("[ollama] unload of %s failed: %s", model, exc)

    async def chat_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
        *,
        messages: list[dict] | None = None,
    ) -> str:
        """Chat completion via ``/api/chat`` (required for native think on bonsai/gemma).

        Prefer this for long creative calls with ``num_ctx`` ≥ 16384.
        ``prompt`` alone becomes a single user message; pass ``messages`` to override.
        """
        merged_options = {"num_predict": -1, **(options or {})}
        model_name = model or settings.vlm_model
        msgs = messages or [{"role": "user", "content": prompt}]
        payload: dict = self._with_think(
            {
                "model": model_name,
                "messages": msgs,
                "stream": False,
                "options": merged_options,
            },
            think,
        )
        if fmt:
            payload["format"] = fmt
        async with self._acquire():
            r = await self._client.post(f"{self.base_url}/api/chat", json=payload)
        self._raise_with_body(r)
        return self._extract_chat_text(r.json(), model=model_name)

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
        system: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream text generation without vision inputs."""
        model_name = model or settings.vlm_model
        payload = self._with_think(
            {
                "model": model_name,
                "prompt": prompt,
                "stream": True,
                "options": {"num_predict": -1, **(options or {})},
                **({"system": system} if system else {}),
            },
            think,
        )
        async for event in self._generate_stream(payload, model=model_name):
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

    async def vision_models(self, url: str | None = None) -> list[str]:
        """Subset of installed models that can actually accept images.

        A text-only model given images does not fail — Ollama drops them and
        answers from the prompt alone, so a reference-image pipeline silently
        degrades into guesswork. Callers use this to say so up front.
        """
        base = (url or self.base_url).rstrip("/")
        out: list[str] = []
        for name in await self.list_models(url):
            try:
                r = await self._client.post(
                    f"{base}/api/show", json={"model": name}, timeout=5.0
                )
                r.raise_for_status()
                if "vision" in (r.json().get("capabilities") or []):
                    out.append(name)
            except Exception:
                continue
        return out

    async def close(self) -> None:
        await self._client.aclose()

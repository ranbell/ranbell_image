import base64
import json
import logging
from typing import AsyncGenerator

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


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
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=300.0)

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        r = await self._client.post(
            f"{settings.ollama_url}/api/embed",
            json={"model": model or settings.embed_model, "input": text},
        )
        r.raise_for_status()
        return r.json()["embeddings"][0]

    async def generate_vlm(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
    ) -> str:
        images_b64 = [base64.b64encode(b).decode() for b in image_bytes_list]
        r = await self._client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "images": images_b64,
                "stream": False,
                "options": options or {},
            },
        )
        r.raise_for_status()
        return r.json()["response"]

    async def generate_vlm_stream(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        images_b64 = [base64.b64encode(b).decode() for b in image_bytes_list]
        parser = StreamParser()

        async with self._client.stream(
            "POST",
            f"{settings.ollama_url}/api/generate",
            json={
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "images": images_b64,
                "stream": True,
                "options": options or {},
            },
            timeout=300.0,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                chunk = data.get("response", "")
                if chunk:
                    for event in parser.feed(chunk):
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
    ) -> str:
        """Generate text without vision inputs (text-only LLM call)."""
        r = await self._client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": model or settings.vlm_model,
                "prompt": prompt,
                "stream": False,
                "options": options or {},
            },
        )
        r.raise_for_status()
        return r.json()["response"]

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream text generation without vision inputs."""
        parser = StreamParser()
        async with self._client.stream(
            "POST",
            f"{settings.ollama_url}/api/generate",
            json={"model": model or settings.vlm_model, "prompt": prompt, "stream": True, "options": options or {}},
            timeout=300.0,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                chunk = data.get("response", "")
                if chunk:
                    for event in parser.feed(chunk):
                        yield event
                if data.get("done"):
                    for event in parser.flush():
                        yield event
                    return
        for event in parser.flush():
            yield event

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{settings.ollama_url}/api/tags", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            r = await self._client.get(f"{settings.ollama_url}/api/tags", timeout=5.0)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    async def close(self) -> None:
        await self._client.aclose()

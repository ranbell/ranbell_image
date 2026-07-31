"""OpenAI-compatible chat client (llama.cpp / Bonsai / vLLM / LM Studio / …).

Implements the same generate_* surface as OllamaClient so features and the
rest of the app can switch providers without call-site changes.

Embeddings stay on Ollama — this client is text + vision only.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def _normalize_base_url(url: str) -> str:
    """Accept ``http://host:8080`` or ``…/v1``; always end with ``/v1``."""
    base = (url or "").rstrip("/")
    if not base:
        return "http://host.docker.internal:8080/v1"
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def map_openai_options(
    options: dict | None,
    *,
    fmt: str | None = None,
    think: bool | str | None = None,
) -> dict[str, Any]:
    """Translate Ollama-style options into OpenAI chat.completions fields."""
    opts = dict(options or {})
    payload: dict[str, Any] = {}

    if "temperature" in opts and opts["temperature"] is not None:
        payload["temperature"] = float(opts["temperature"])
    if "top_p" in opts and opts["top_p"] is not None:
        payload["top_p"] = float(opts["top_p"])
    # llama.cpp accepts top_k; official OpenAI ignores unknown fields on some
    # proxies — harmless to pass for Bonsai / llama-server.
    if "top_k" in opts and opts["top_k"] is not None:
        payload["top_k"] = int(opts["top_k"])
    if "seed" in opts and opts["seed"] is not None:
        payload["seed"] = int(opts["seed"])

    num_predict = opts.get("num_predict")
    if num_predict is not None and int(num_predict) >= 0:
        payload["max_tokens"] = int(num_predict)
    # num_ctx is server-side for llama.cpp; ignored here.

    if fmt == "json":
        payload["response_format"] = {"type": "json_object"}

    # Bonsai / llama.cpp reasoning: thinking_budget_tokens (0=off, -1=unlimited).
    if think is True or (isinstance(think, str) and think.lower() in ("true", "high", "medium", "low")):
        payload["thinking_budget_tokens"] = -1
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    elif think is False or think == 0 or (isinstance(think, str) and think.lower() in ("false", "off", "none")):
        payload["thinking_budget_tokens"] = 0
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    return payload


def build_user_message(prompt: str, image_bytes_list: list[bytes] | None = None) -> dict:
    images = image_bytes_list or []
    if not images:
        return {"role": "user", "content": prompt}
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for raw in images:
        b64 = base64.b64encode(raw).decode()
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    return {"role": "user", "content": parts}


def extract_message_text(message: dict | None) -> str:
    """Prefer final content; fall back to reasoning if content is empty JSON-ish."""
    if not message:
        return ""
    text = str(message.get("content") or "")
    if "<think>" in text.lower():
        text = _THINK_BLOCK_RE.sub("", text)
    text = text.strip()
    if text:
        return text
    for key in ("reasoning_content", "reasoning", "thinking"):
        thinking = str(message.get(key) or "").strip()
        if thinking and "{" in thinking:
            return thinking
    return ""


def stream_delta_events(delta: dict) -> list[dict]:
    """Map one OpenAI stream delta into Ollama-style {type,text} events."""
    events: list[dict] = []
    for key in ("reasoning_content", "reasoning", "thinking"):
        chunk = delta.get(key) or ""
        if chunk:
            events.append({"type": "think", "text": chunk})
    content = delta.get("content") or ""
    if content:
        # Strip think tags if a server inlines them into content.
        if "<think>" in content.lower() or "</think>" in content.lower():
            # Emit as token after stripping closed blocks; partial tags are rare in deltas.
            cleaned = _THINK_BLOCK_RE.sub("", content)
            if cleaned:
                events.append({"type": "token", "text": cleaned})
        else:
            events.append({"type": "token", "text": content})
    return events


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        resource=None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url or settings.openai_base_url)
        self.api_key = api_key if api_key is not None else settings.openai_api_key
        self.default_model = default_model or settings.vlm_model
        self._client = httpx.AsyncClient(timeout=settings.ollama_timeout_sec)
        self._resource = resource

    def configure(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        if base_url is not None:
            self.base_url = _normalize_base_url(base_url)
        if api_key is not None:
            self.api_key = api_key
        if default_model is not None:
            self.default_model = default_model

    def set_resource(self, resource) -> None:
        self._resource = resource

    @asynccontextmanager
    async def _acquire(self):
        if self._resource is None:
            yield
        else:
            async with self._resource.acquire():
                yield

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = (self.api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _raise_with_body(self, r: httpx.Response) -> None:
        if r.is_error:
            try:
                body = r.json()
                msg = body.get("error") or str(body)
                if isinstance(msg, dict):
                    msg = msg.get("message") or str(msg)
            except Exception:
                msg = r.text[:500]
            logger.error("[openai] %s %s — %s", r.status_code, r.url, msg)
            r.raise_for_status()

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _models_url(self) -> str:
        return f"{self.base_url}/models"

    async def _chat(
        self,
        *,
        prompt: str,
        image_bytes_list: list[bytes] | None = None,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        payload: dict[str, Any] = {
            "model": model or self.default_model or settings.vlm_model,
            "messages": [build_user_message(prompt, image_bytes_list)],
            "stream": stream,
        }
        payload.update(map_openai_options(options, fmt=fmt, think=think))
        return await self._client.post(
            self._chat_url(),
            headers=self._headers(),
            json=payload,
            timeout=settings.ollama_timeout_sec,
        )

    async def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
    ) -> str:
        merged = {"num_predict": -1, **(options or {})}
        async with self._acquire():
            r = await self._chat(
                prompt=prompt,
                model=model,
                options=merged,
                fmt=fmt,
                think=think,
                stream=False,
            )
        self._raise_with_body(r)
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        text = extract_message_text(choice.get("message") or {})
        if not text:
            logger.warning(
                "[openai] empty content from %s",
                model or self.default_model,
            )
        return text

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
        """OpenAI path is already chat; ``messages`` overrides the single-user prompt."""
        if messages:
            merged = {"num_predict": -1, **(options or {})}
            async with self._acquire():
                r = await self._client.post(
                    self._chat_url(),
                    headers=self._headers(),
                    json={
                        "model": model or self.default_model or settings.vlm_model,
                        "messages": messages,
                        "stream": False,
                        **map_openai_options(merged, fmt=fmt, think=think),
                    },
                    timeout=settings.ollama_timeout_sec,
                )
            self._raise_with_body(r)
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            return extract_message_text(choice.get("message") or {})
        return await self.generate_text(
            prompt, model=model, options=options, fmt=fmt, think=think
        )

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[dict, None]:
        merged = {"num_predict": -1, **(options or {})}
        async with self._acquire(), self._client.stream(
            "POST",
            self._chat_url(),
            headers=self._headers(),
            json={
                "model": model or self.default_model or settings.vlm_model,
                "messages": [build_user_message(prompt)],
                "stream": True,
                **map_openai_options(merged, think=think),
            },
            timeout=settings.ollama_timeout_sec,
        ) as resp:
            if resp.is_error:
                body = await resp.aread()
                try:
                    msg = json.loads(body).get("error") or body[:500].decode()
                    if isinstance(msg, dict):
                        msg = msg.get("message") or str(msg)
                except Exception:
                    msg = body[:500].decode()
                logger.error("[openai] %s %s — %s", resp.status_code, resp.url, msg)
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    continue  # SSE comment / keepalive
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    return
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                delta = ((data.get("choices") or [{}])[0]).get("delta") or {}
                for event in stream_delta_events(delta):
                    yield event

    async def generate_vlm(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> str:
        async with self._acquire():
            r = await self._chat(
                prompt=prompt,
                image_bytes_list=image_bytes_list,
                model=model,
                options=options or {},
                think=think,
                stream=False,
            )
        self._raise_with_body(r)
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        return extract_message_text(choice.get("message") or {})

    async def generate_vlm_stream(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[dict, None]:
        payload = {
            "model": model or self.default_model or settings.vlm_model,
            "messages": [build_user_message(prompt, image_bytes_list)],
            "stream": True,
            **map_openai_options(options, think=think),
        }
        async with self._acquire(), self._client.stream(
            "POST",
            self._chat_url(),
            headers=self._headers(),
            json=payload,
            timeout=settings.ollama_timeout_sec,
        ) as resp:
            if resp.is_error:
                body = await resp.aread()
                try:
                    msg = json.loads(body).get("error") or body[:500].decode()
                    if isinstance(msg, dict):
                        msg = msg.get("message") or str(msg)
                except Exception:
                    msg = body[:500].decode()
                logger.error("[openai] %s %s — %s", resp.status_code, resp.url, msg)
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    return
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                delta = ((data.get("choices") or [{}])[0]).get("delta") or {}
                for event in stream_delta_events(delta):
                    yield event

    async def health(self, url: str | None = None) -> bool:
        base = _normalize_base_url(url) if url else self.base_url
        try:
            r = await self._client.get(
                f"{base}/models",
                headers=self._headers(),
                timeout=5.0,
            )
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self, url: str | None = None) -> list[str]:
        base = _normalize_base_url(url) if url else self.base_url
        try:
            r = await self._client.get(
                f"{base}/models",
                headers=self._headers(),
                timeout=5.0,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("data") or data.get("models") or []
            names: list[str] = []
            for m in items:
                if isinstance(m, str):
                    names.append(m)
                elif isinstance(m, dict):
                    name = m.get("id") or m.get("name")
                    if name:
                        names.append(str(name))
            return names
        except Exception:
            return []

    async def close(self) -> None:
        await self._client.aclose()

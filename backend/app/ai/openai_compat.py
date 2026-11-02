"""Client for an external OpenAI-compatible server (Lemonade, llama.cpp
server, vLLM, LM Studio, ...).

It speaks ``/v1/chat/completions`` and exposes the same text / vision
methods as ``OllamaClient``, so a feature can be pointed at it without its
call sites changing (see ``LlmGateway.for_feature``). Embeddings are not
offered: they stay on Ollama so the Qdrant vector width never changes.

Mapping from the Ollama-shaped arguments the callers already pass:

- ``options``: only the sampling keys every server understands are sent
  (temperature, top_p, seed, stop, penalties). ``num_predict`` > 0 becomes
  ``max_tokens``; -1 (unlimited) sends nothing. ``num_ctx``, ``top_k`` and the
  rest are Ollama/llama.cpp specific and a strict server rejects unknown
  keys, so they are dropped — the window is the server's own setting.
- ``think=False`` sends ``chat_template_kwargs.enable_thinking=false``; any
  other value leaves the server's default alone.
- ``fmt="json"`` → ``response_format: json_object``; a dict → ``json_schema``.
- The thinking channel arrives as ``reasoning_content`` (or ``reasoning``)
  and is emitted as ``think`` events, the same as Ollama's ``thinking``.
- ``finish_reason: "length"`` is reported as ``done_reason: "length"``.

Not routed through the spooler's remote-ollama resource: the server is not
on this machine's card, so it neither competes with ComfyUI nor needs
unloading before a render.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import re
import time
from typing import AsyncGenerator

import httpx

from ..config import settings
from .ollama import _THINK_BLOCK_RE, StreamParser

logger = logging.getLogger(__name__)

_VERSION_SUFFIX_RE = re.compile(r"/v\d+$")

# Sampling keys passed through unchanged. Everything else in `options` is dropped.
_PASSTHROUGH_OPTIONS = ("temperature", "top_p", "seed", "stop", "presence_penalty", "frequency_penalty")


def normalize_base_url(url: str) -> str:
    """``http://host:port`` → ``http://host:port/v1``; a URL already ending in
    ``/v1`` (or ``/api/v1``) is kept as is."""
    base = (url or "").strip().rstrip("/")
    if base and not _VERSION_SUFFIX_RE.search(base):
        base += "/v1"
    return base


_LLAMA_SAMPLING_FLAGS = {
    "--temp": "temperature", "--temperature": "temperature",
    "--top-p": "top_p", "--top-k": "top_k", "--min-p": "min_p",
}


def _parse_llama_args(args: str) -> dict:
    """``--temp 1.0 --top-p 0.95 --top-k 64`` → {"temperature": 1.0, ...}."""
    out: dict = {}
    tokens = args.split()
    for flag, value in zip(tokens, tokens[1:]):
        key = _LLAMA_SAMPLING_FLAGS.get(flag)
        if not key:
            continue
        try:
            out[key] = int(value) if key == "top_k" else float(value)
        except ValueError:
            continue
    return out


def _image_data_url(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class OpenAICompatClient:
    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        self._client = httpx.AsyncClient(timeout=settings.ollama_timeout_sec)
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key or ""
        self._context_cache: dict[tuple[str, str], tuple[float, int | None]] = {}
        self._sampling_cache: dict[tuple[str, str], tuple[float, dict]] = {}

    def configure(self, *, base_url: str | None = None, api_key: str | None = None) -> None:
        if base_url is not None:
            self.base_url = normalize_base_url(base_url)
        if api_key is not None:
            self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self, api_key: str | None = None) -> dict:
        key = self.api_key if api_key is None else api_key
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _require_url(self) -> str:
        if not self.base_url:
            raise RuntimeError("External LLM server URL is not set (Admin → External LLM server)")
        return self.base_url

    # ── Payload ────────────────────────────────────────────────────────────────

    @staticmethod
    def _messages(prompt: str, system: str | None, images: list[bytes] | None = None) -> list[dict]:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        if images:
            content: list[dict] = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": _image_data_url(b)}} for b in images]
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({"role": "user", "content": prompt})
        return msgs

    @staticmethod
    def _payload(
        messages: list[dict],
        model: str,
        options: dict | None,
        fmt: str | dict | None,
        think: bool | str | None,
        stream: bool,
    ) -> dict:
        payload: dict = {"model": model, "messages": messages, "stream": stream}
        opts = options or {}
        for key in _PASSTHROUGH_OPTIONS:
            if opts.get(key) is not None:
                payload[key] = opts[key]
        num_predict = opts.get("num_predict")
        if isinstance(num_predict, int) and num_predict > 0:
            payload["max_tokens"] = num_predict
        if think is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if fmt == "json":
            payload["response_format"] = {"type": "json_object"}
        elif isinstance(fmt, dict):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": fmt},
            }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _raise_with_body(self, status: int, url, body: bytes) -> None:
        try:
            data = json.loads(body)
            err = data.get("error")
            msg = (err.get("message") if isinstance(err, dict) else err) or str(data)
        except Exception:
            msg = body[:500].decode("utf-8", "replace")
        logger.error("[openai] %s %s — %s", status, url, msg)
        raise RuntimeError(f"External LLM server error {status}: {msg}")

    # ── Calls ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(data: dict, *, model: str) -> str:
        message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
        text = str(message.get("content") or "")
        if "<think>" in text.lower():
            text = _THINK_BLOCK_RE.sub("", text)
        text = text.strip()
        if not text:
            # Same recovery as OllamaClient: a reasoning model that spent its
            # budget thinking may have left usable JSON in the thinking channel.
            thinking = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
            if thinking and "{" in thinking:
                logger.warning("[openai] empty content from %s; recovering JSON from reasoning", model)
                text = thinking
        return text

    @staticmethod
    def _unreachable(url: str, exc: Exception) -> RuntimeError:
        # httpx says only "All connection attempts failed"; without the URL a
        # stale saved address looks the same as a server that is down.
        return RuntimeError(f"External LLM server {url} is unreachable: {exc}")

    async def _complete(self, payload: dict) -> str:
        url = f"{self._require_url()}/chat/completions"
        try:
            r = await self._client.post(url, json=payload, headers=self._headers())
        except httpx.TransportError as exc:
            raise self._unreachable(self.base_url, exc) from exc
        if r.is_error:
            self._raise_with_body(r.status_code, r.url, r.content)
        return self._extract_text(r.json(), model=payload["model"])

    async def _stream(self, payload: dict, *, with_done: bool) -> AsyncGenerator[dict, None]:
        """Server-sent events → think/token events (and one done event).

        As in ``OllamaClient._generate_stream``: when no answer text came at
        all, the thinking text is re-emitted as one token so the caller does
        not build on an empty string.
        """
        url = f"{self._require_url()}/chat/completions"
        model = payload["model"]
        parser = StreamParser()
        thinking_parts: list[str] = []
        saw_token = False
        finish_reason = ""
        usage: dict = {}

        def _track(events: list[dict]) -> list[dict]:
            nonlocal saw_token
            for ev in events:
                if ev["type"] == "token" and ev["text"]:
                    saw_token = True
                elif ev["type"] == "think":
                    thinking_parts.append(ev["text"])
            return events

        async with contextlib.AsyncExitStack() as stack:
            try:
                resp = await stack.enter_async_context(self._client.stream(
                    "POST", url, json=payload, headers=self._headers(),
                    timeout=settings.ollama_timeout_sec,
                ))
            except httpx.TransportError as exc:
                raise self._unreachable(self.base_url, exc) from exc
            if resp.is_error:
                self._raise_with_body(resp.status_code, resp.url, await resp.aread())
            async for line in resp.aiter_lines():
                # Blank separators and `: ping` keep-alive comments carry nothing.
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except Exception:
                    continue
                if data.get("usage"):
                    usage = data["usage"]
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    events: list[dict] = []
                    if thinking:
                        events.append({"type": "think", "text": thinking})
                    if delta.get("content"):
                        events.extend(parser.feed(delta["content"]))
                    for ev in _track(events):
                        yield ev
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])

        for ev in _track(parser.flush()):
            yield ev
        if not saw_token:
            recovered = "".join(thinking_parts).strip()
            if recovered:
                logger.warning(
                    "[openai] %s streamed no content; recovering %d chars from reasoning",
                    model, len(recovered),
                )
                yield {"type": "token", "text": recovered}
        if finish_reason == "length":
            logger.warning(
                "[openai] %s was cut by max_tokens / the server window "
                "(prompt %s tok + output %s tok)",
                model, usage.get("prompt_tokens"), usage.get("completion_tokens"),
            )
        if with_done:
            yield {
                "type": "done",
                "reason": "length" if finish_reason == "length" else finish_reason,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "eval_tokens": int(usage.get("completion_tokens") or 0),
            }

    async def generate_text(
        self, prompt: str, model: str, options: dict | None = None,
        fmt: str | dict | None = None, think: bool | str | None = None,
        system: str | None = None,
    ) -> str:
        return await self._complete(self._payload(
            self._messages(prompt, system), model, options, fmt, think, stream=False,
        ))

    async def chat_text(
        self, prompt: str, model: str, options: dict | None = None,
        fmt: str | None = None, think: bool | str | None = None,
        *, messages: list[dict] | None = None,
    ) -> str:
        msgs = messages or [{"role": "user", "content": prompt}]
        return await self._complete(self._payload(msgs, model, options, fmt, think, stream=False))

    async def generate_vlm(
        self, prompt: str, image_bytes_list: list[bytes], model: str,
        options: dict | None = None, think: bool | str | None = None,
        system: str | None = None,
    ) -> str:
        return await self._complete(self._payload(
            self._messages(prompt, system, image_bytes_list), model, options, None, think,
            stream=False,
        ))

    async def generate_text_stream(
        self, prompt: str, model: str, options: dict | None = None,
        think: bool | str | None = None, system: str | None = None,
        with_done: bool = False,
    ) -> AsyncGenerator[dict, None]:
        payload = self._payload(self._messages(prompt, system), model, options, None, think, stream=True)
        async for ev in self._stream(payload, with_done=with_done):
            yield ev

    async def generate_vlm_stream(
        self, prompt: str, image_bytes_list: list[bytes], model: str,
        options: dict | None = None, think: bool | str | None = None,
        system: str | None = None, with_done: bool = False,
    ) -> AsyncGenerator[dict, None]:
        payload = self._payload(
            self._messages(prompt, system, image_bytes_list), model, options, None, think,
            stream=True,
        )
        async for ev in self._stream(payload, with_done=with_done):
            yield ev

    # ── Models ─────────────────────────────────────────────────────────────────

    async def context_window(self, model: str) -> int | None:
        """The context window the server runs ``model`` with, when it says.

        Unlike Ollama's ``num_ctx`` this cannot be set per request: llama.cpp
        fixes it when the model is loaded (Lemonade starts llama-server with
        ``--ctx-size``). Lemonade reports it in ``/models`` as
        ``context_length``; a server that does not gives None. Cached for a
        minute, since the screen asks on every status refresh.
        """
        if not (self.base_url and model):
            return None
        now = time.monotonic()
        key = (self.base_url, model)
        hit = self._context_cache.get(key)
        if hit and now - hit[0] < 60.0:
            return hit[1]
        value: int | None = None
        try:
            r = await self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=3.0)
            r.raise_for_status()
            for m in r.json().get("data") or []:
                if m.get("id") == model:
                    raw = m.get("context_length") or m.get("max_model_len")
                    value = int(raw) if raw else None
                    break
        except Exception as exc:
            logger.debug("[openai] context window of %s unknown: %s", model, exc)
        self._context_cache[key] = (now, value)
        return value

    async def sampling_defaults(self, model: str) -> dict:
        """The sampling the server applies to ``model`` when a request sends none.

        Not part of the OpenAI API, so it is read where servers expose it:
        Lemonade lists each loaded model's llama-server arguments in
        ``/api/v1/health`` (``--temp 1.0 --top-p 0.95 --top-k 64``), and a bare
        llama.cpp server answers ``/props``. Anything else gives {} — Refine
        then sends no temperature, so the server's own default still applies.
        Cached for a minute, like the context window.
        """
        if not (self.base_url and model):
            return {}
        now = time.monotonic()
        key = (self.base_url, model)
        hit = self._sampling_cache.get(key)
        if hit and now - hit[0] < 60.0:
            return dict(hit[1])
        root = re.sub(r"(/api)?/v\d+$", "", self.base_url)
        found: dict = {}
        try:
            r = await self._client.get(f"{root}/api/v1/health", headers=self._headers(), timeout=3.0)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                for m in r.json().get("all_models_loaded") or []:
                    if m.get("model_name") == model:
                        args = (m.get("recipe_options") or {}).get("llamacpp_args") or ""
                        if not args:
                            args = " ".join(str(a) for a in m.get("launch_command") or [])
                        found = _parse_llama_args(args)
                        break
            if not found:
                r = await self._client.get(f"{root}/props", headers=self._headers(), timeout=3.0)
                if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                    gen = r.json().get("default_generation_settings") or {}
                    params = gen.get("params") or gen
                    found = {k: params[k] for k in ("temperature", "top_p", "top_k", "min_p")
                             if isinstance(params.get(k), (int, float))}
        except Exception as exc:
            logger.debug("[openai] sampling defaults of %s unknown: %s", model, exc)
        self._sampling_cache[key] = (now, found)
        return dict(found)

    async def list_models(self, url: str | None = None, api_key: str | None = None) -> list[str]:
        """Model ids from ``/models``. Raises on a connection or HTTP error so
        the Admin connection test can say what went wrong."""
        base = normalize_base_url(url) if url else self._require_url()
        r = await self._client.get(f"{base}/models", headers=self._headers(api_key), timeout=10.0)
        r.raise_for_status()
        return [str(m.get("id")) for m in (r.json().get("data") or []) if m.get("id")]

    async def close(self) -> None:
        await self._client.aclose()

"""Thin LLM gateway wrapping the Ollama client.

``app.state.ollama`` is this gateway. It used to also front a runtime-selectable
OpenAI-compatible backend, but nothing ever selected it — the shared gateway
always defaulted to Ollama, and no feature ever opted a call into the
alternative — so that layer was removed. This class stays so the many
``app.state.ollama`` call sites keep a stable interface rather than reaching
into ``OllamaClient`` directly.

Embeddings always go through Ollama so Qdrant vector dimensions stay stable.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from .ollama import OllamaClient

logger = logging.getLogger(__name__)


def apply_llm_runtime_config(llm: "LlmGateway", cfg: dict) -> None:
    """Push Admin / env connection settings onto the live gateway."""
    llm.configure(ollama_url=cfg.get("ollama_url"))


class LlmGateway:
    def __init__(self, ollama: OllamaClient) -> None:
        self._ollama = ollama

    @property
    def ollama(self) -> OllamaClient:
        return self._ollama

    def set_resource(self, resource) -> None:
        self._ollama.set_resource(resource)

    def configure(self, *, ollama_url: str | None = None) -> None:
        if ollama_url is not None:
            self._ollama.set_base_url(ollama_url)
        logger.info("[llm] ollama=%s", getattr(self._ollama, "base_url", "?"))

    # ── Embeddings ─────────────────────────────────────────────────────────────

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        return await self._ollama.embed(text, model=model)

    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return await self._ollama.embed_batch(texts, model=model)

    # ── Text / VLM ─────────────────────────────────────────────────────────────

    async def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
        system: str | None = None,
    ) -> str:
        return await self._ollama.generate_text(
            prompt, model=model, options=options, fmt=fmt, think=think,
            system=system,
        )

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
        return await self._ollama.chat_text(
            prompt, model=model, options=options, fmt=fmt, think=think,
            messages=messages,
        )

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
        system: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._ollama.generate_text_stream(
            prompt, model=model, options=options, think=think, system=system,
        ):
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
        return await self._ollama.generate_vlm(
            prompt, image_bytes_list, model=model, options=options, think=think,
            system=system,
        )

    async def generate_vlm_stream(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
        system: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._ollama.generate_vlm_stream(
            prompt, image_bytes_list, model=model, options=options, think=think,
            system=system,
        ):
            yield event

    # ── Health / models ───────────────────────────────────────────────────────

    async def health(self, url: str | None = None) -> bool:
        return await self._ollama.health(url)

    async def health_ollama(self, url: str | None = None) -> bool:
        return await self._ollama.health(url)

    async def list_models(self, url: str | None = None) -> list[str]:
        return await self._ollama.list_models(url)

    async def list_ollama_models(self, url: str | None = None) -> list[str]:
        return await self._ollama.list_models(url)

    async def vision_ollama_models(self, url: str | None = None) -> list[str]:
        return await self._ollama.vision_models(url)

    async def unload(self, model: str | None = None) -> None:
        """Free the card before handing over to a renderer."""
        await self._ollama.unload(model)

    async def close(self) -> None:
        await self._ollama.close()

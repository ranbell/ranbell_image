"""Unified LLM gateway: Ollama embeddings + selectable text/VLM provider.

``app.state.ollama`` remains this gateway for backward compatibility. Call sites
keep using ``generate_text`` / ``embed`` / etc. Embeddings always go through
Ollama so Qdrant vector dimensions stay stable. Text and vision generation
route to Ollama or an OpenAI-compatible server (Bonsai / llama.cpp / …)
based on ``llm_provider``.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Literal

from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)

Provider = Literal["ollama", "openai"]


def apply_llm_runtime_config(llm: "LlmGateway", cfg: dict) -> None:
    """Push Admin / env runtime settings onto the live gateway."""
    from ..config import settings

    llm.configure(
        provider=cfg.get("llm_provider") or settings.llm_provider,
        ollama_url=cfg.get("ollama_url") or settings.ollama_url,
        openai_base_url=cfg.get("openai_base_url") or settings.openai_base_url,
        openai_api_key=(
            cfg.get("openai_api_key")
            if cfg.get("openai_api_key") is not None
            else settings.openai_api_key
        ),
        vlm_model=cfg.get("vlm_model") or settings.vlm_model,
    )


class LlmGateway:
    def __init__(
        self,
        ollama: OllamaClient,
        openai: OpenAICompatClient,
        *,
        provider: Provider = "ollama",
    ) -> None:
        self._ollama = ollama
        self._openai = openai
        self.provider: Provider = provider if provider in ("ollama", "openai") else "ollama"

    @property
    def ollama(self) -> OllamaClient:
        return self._ollama

    @property
    def openai(self) -> OpenAICompatClient:
        return self._openai

    def set_resource(self, resource) -> None:
        """Share the remote-ollama semaphore across both backends when set."""
        self._ollama.set_resource(resource)
        self._openai.set_resource(resource)

    def configure(
        self,
        *,
        provider: str | None = None,
        ollama_url: str | None = None,
        openai_base_url: str | None = None,
        openai_api_key: str | None = None,
        vlm_model: str | None = None,
    ) -> None:
        if provider in ("ollama", "openai"):
            self.provider = provider  # type: ignore[assignment]
        if ollama_url is not None:
            self._ollama.set_base_url(ollama_url)
        self._openai.configure(
            base_url=openai_base_url,
            api_key=openai_api_key,
            default_model=vlm_model,
        )
        logger.info(
            "[llm] provider=%s ollama=%s openai=%s model=%s",
            self.provider,
            getattr(self._ollama, "base_url", "?"),
            getattr(self._openai, "base_url", "?"),
            getattr(self._openai, "default_model", "?"),
        )

    def _gen(self):
        return self._openai if self.provider == "openai" else self._ollama

    # ── Embeddings: always Ollama ─────────────────────────────────────────────

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        return await self._ollama.embed(text, model=model)

    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return await self._ollama.embed_batch(texts, model=model)

    # ── Text / VLM: routed ────────────────────────────────────────────────────

    async def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
    ) -> str:
        return await self._gen().generate_text(
            prompt, model=model, options=options, fmt=fmt, think=think
        )

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._gen().generate_text_stream(
            prompt, model=model, options=options, think=think
        ):
            yield event

    async def generate_vlm(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> str:
        return await self._gen().generate_vlm(
            prompt, image_bytes_list, model=model, options=options, think=think
        )

    async def generate_vlm_stream(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._gen().generate_vlm_stream(
            prompt, image_bytes_list, model=model, options=options, think=think
        ):
            yield event

    # ── Health / models ───────────────────────────────────────────────────────

    async def health(self, url: str | None = None) -> bool:
        """Health of the active text/VLM provider (legacy callers)."""
        if self.provider == "openai":
            return await self._openai.health(url)
        return await self._ollama.health(url)

    async def health_ollama(self, url: str | None = None) -> bool:
        return await self._ollama.health(url)

    async def health_openai(self, url: str | None = None) -> bool:
        return await self._openai.health(url)

    async def list_models(self, url: str | None = None) -> list[str]:
        """Models for the active text/VLM provider."""
        return await self._gen().list_models(url)

    async def list_ollama_models(self, url: str | None = None) -> list[str]:
        return await self._ollama.list_models(url)

    async def list_openai_models(self, url: str | None = None) -> list[str]:
        return await self._openai.list_models(url)

    async def close(self) -> None:
        await self._ollama.close()
        await self._openai.close()

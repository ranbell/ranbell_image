"""Unified LLM gateway: Ollama embeddings + selectable text/VLM provider.

``app.state.ollama`` is this gateway. By default, ``generate_*`` always uses
Ollama so Invoke / Refine / Inspire keep working unchanged. Callers that want
an OpenAI-compatible backend (Chronicles) must pass ``provider=`` or use
``bind("openai")``.

Embeddings always go through Ollama so Qdrant vector dimensions stay stable.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Literal

from ..config import settings
from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)

Provider = Literal["ollama", "openai"]


def apply_llm_runtime_config(llm: "LlmGateway", cfg: dict) -> None:
    """Push Admin / env connection settings onto the live gateway.

    Does **not** change the default text/VLM route — that stays Ollama unless a
    caller explicitly binds/overrides (Chronicles).
    """
    llm.configure(
        # Keep the shared gateway on Ollama; feature-level bind() chooses OpenAI.
        provider="ollama",
        ollama_url=cfg.get("ollama_url") or settings.ollama_url,
        openai_base_url=cfg.get("openai_base_url") or settings.openai_base_url,
        openai_api_key=(
            cfg.get("openai_api_key")
            if cfg.get("openai_api_key") is not None
            else settings.openai_api_key
        ),
        vlm_model=cfg.get("openai_model") or cfg.get("vlm_model") or settings.vlm_model,
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

    def bind(self, provider: str | None) -> "LlmGateway":
        """Return a view that forces text/VLM onto ``provider`` (default ollama).

        Shares the same underlying HTTP clients / config. Embeddings still hit
        Ollama. Use from Chronicles so the rest of the app stays on Ollama.
        """
        p: Provider = "openai" if provider == "openai" else "ollama"
        if p == self.provider:
            return self
        return LlmGateway(self._ollama, self._openai, provider=p)

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
            "[llm] default_route=%s ollama=%s openai=%s openai_model=%s",
            self.provider,
            getattr(self._ollama, "base_url", "?"),
            getattr(self._openai, "base_url", "?"),
            getattr(self._openai, "default_model", "?"),
        )

    def _gen(self, provider: str | None = None):
        p = provider if provider in ("ollama", "openai") else self.provider
        return self._openai if p == "openai" else self._ollama

    # ── Embeddings: always Ollama ─────────────────────────────────────────────

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        return await self._ollama.embed(text, model=model)

    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return await self._ollama.embed_batch(texts, model=model)

    # ── Text / VLM: routed (default = this.provider, usually ollama) ─────────

    async def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        fmt: str | None = None,
        think: bool | str | None = None,
        provider: str | None = None,
    ) -> str:
        return await self._gen(provider).generate_text(
            prompt, model=model, options=options, fmt=fmt, think=think
        )

    async def generate_text_stream(
        self,
        prompt: str,
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
        provider: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._gen(provider).generate_text_stream(
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
        provider: str | None = None,
    ) -> str:
        return await self._gen(provider).generate_vlm(
            prompt, image_bytes_list, model=model, options=options, think=think
        )

    async def generate_vlm_stream(
        self,
        prompt: str,
        image_bytes_list: list[bytes],
        model: str | None = None,
        options: dict | None = None,
        think: bool | str | None = None,
        provider: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._gen(provider).generate_vlm_stream(
            prompt, image_bytes_list, model=model, options=options, think=think
        ):
            yield event

    # ── Health / models ───────────────────────────────────────────────────────

    async def health(self, url: str | None = None) -> bool:
        """Health of the active default text/VLM provider (legacy callers)."""
        if self.provider == "openai":
            return await self._openai.health(url)
        return await self._ollama.health(url)

    async def health_ollama(self, url: str | None = None) -> bool:
        return await self._ollama.health(url)

    async def health_openai(self, url: str | None = None) -> bool:
        return await self._openai.health(url)

    async def list_models(self, url: str | None = None) -> list[str]:
        """Models for the default text/VLM provider."""
        return await self._gen().list_models(url)

    async def list_ollama_models(self, url: str | None = None) -> list[str]:
        return await self._ollama.list_models(url)

    async def list_openai_models(self, url: str | None = None) -> list[str]:
        return await self._openai.list_models(url)

    async def close(self) -> None:
        await self._ollama.close()
        await self._openai.close()

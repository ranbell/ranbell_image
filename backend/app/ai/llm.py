"""Thin LLM gateway wrapping the Ollama client.

``app.state.ollama`` is this gateway. This class stays so the many
``app.state.ollama`` call sites keep a stable interface rather than reaching
into ``OllamaClient`` directly.

Inspire and Refine can be pointed at an external OpenAI-compatible server
instead (Admin → External LLM server). They ask for their gateway with
``for_feature(...)``, which returns either this gateway or an
``ExternalLlm`` with the same methods. Everything else keeps using Ollama.

Embeddings always go through Ollama so Qdrant vector dimensions stay stable.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)

# Features that may use the external server, and the config key that selects it.
LLM_BACKEND_KEYS = {
    "inspire": "inspire_llm_backend",
    "refine":  "refine_llm_backend",
}

# Config keys that change the live gateway when saved from Admin.
LLM_RUNTIME_KEYS = frozenset({
    "ollama_url", "vlm_model",
    "openai_base_url", "openai_api_key", "openai_model", "openai_vlm_model",
    *LLM_BACKEND_KEYS.values(),
})


def apply_llm_runtime_config(llm: "LlmGateway", cfg: dict) -> None:
    """Push Admin / env connection settings onto the live gateway."""
    llm.configure(ollama_url=cfg.get("ollama_url"))
    llm.configure_external(cfg)


class ExternalLlm:
    """Text / vision calls go to the external server, with the configured
    external model in place of whatever (Ollama) model name the caller
    passed. Embeddings and everything else fall through to the Ollama gateway.
    """

    def __init__(self, gateway: "LlmGateway", feature: str) -> None:
        self._gateway = gateway
        self._client = gateway.external
        self.feature = feature
        self._text_model = gateway.external_text_model
        self._vision_model = gateway.external_vision_model

    def __getattr__(self, name):
        return getattr(self._gateway, name)

    def _model(self, *, vision: bool) -> str:
        model = self._vision_model if vision else self._text_model
        if not model:
            raise RuntimeError(
                f"{self.feature}: no model is set for the external LLM server "
                "(Admin → External LLM server)"
            )
        return model

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        return await self._gateway.embed(text, model=model)

    async def embed_batch(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return await self._gateway.embed_batch(texts, model=model)

    async def generate_text(self, prompt, model=None, options=None, fmt=None, think=None, system=None) -> str:
        return await self._client.generate_text(
            prompt, self._model(vision=False), options=options, fmt=fmt, think=think, system=system,
        )

    async def chat_text(self, prompt, model=None, options=None, fmt=None, think=None, *, messages=None) -> str:
        return await self._client.chat_text(
            prompt, self._model(vision=False), options=options, fmt=fmt, think=think, messages=messages,
        )

    async def generate_text_stream(
        self, prompt, model=None, options=None, think=None, system=None, with_done=False,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._client.generate_text_stream(
            prompt, self._model(vision=False), options=options, think=think, system=system,
            with_done=with_done,
        ):
            yield event

    async def generate_vlm(self, prompt, image_bytes_list, model=None, options=None, think=None, system=None) -> str:
        return await self._client.generate_vlm(
            prompt, image_bytes_list, self._model(vision=True), options=options, think=think,
            system=system,
        )

    async def generate_vlm_stream(
        self, prompt, image_bytes_list, model=None, options=None, think=None, system=None,
        with_done=False,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._client.generate_vlm_stream(
            prompt, image_bytes_list, self._model(vision=True), options=options, think=think,
            system=system, with_done=with_done,
        ):
            yield event


class LlmGateway:
    def __init__(self, ollama: OllamaClient, external: OpenAICompatClient | None = None) -> None:
        self._ollama = ollama
        self._external = external or OpenAICompatClient()
        self._backends: dict[str, str] = {}
        self.external_text_model = ""
        self.external_vision_model = ""

    @property
    def ollama(self) -> OllamaClient:
        return self._ollama

    @property
    def external(self) -> OpenAICompatClient:
        return self._external

    def set_resource(self, resource) -> None:
        self._ollama.set_resource(resource)

    def configure(self, *, ollama_url: str | None = None) -> None:
        if ollama_url is not None:
            self._ollama.set_base_url(ollama_url)
        logger.info("[llm] ollama=%s", getattr(self._ollama, "base_url", "?"))

    def configure_external(self, cfg: dict) -> None:
        self._external.configure(
            base_url=cfg.get("openai_base_url") or "",
            api_key=cfg.get("openai_api_key") or "",
        )
        text_model = str(cfg.get("openai_model") or "")
        vision_model = str(cfg.get("openai_vlm_model") or "")
        # One model set → it serves both kinds of call.
        self.external_text_model = text_model or vision_model
        self.external_vision_model = vision_model or text_model
        self._backends = {
            feature: str(cfg.get(key) or "ollama") for feature, key in LLM_BACKEND_KEYS.items()
        }
        routed = [f for f, b in self._backends.items() if b == "openai"]
        if routed:
            logger.info(
                "[llm] external=%s text=%s vision=%s for %s",
                self._external.base_url or "(unset)", self.external_text_model or "(unset)",
                self.external_vision_model or "(unset)", ", ".join(routed),
            )

    def for_feature(self, feature: str) -> "LlmGateway | ExternalLlm":
        """The gateway this feature's text / vision calls should use.

        Choosing the external server without filling in its URL or model is
        not quietly sent to Ollama instead — the call fails and says what is
        missing.
        """
        if self._backends.get(feature) == "openai":
            return ExternalLlm(self, feature)
        return self

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
        fmt: str | dict | None = None,
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
        with_done: bool = False,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._ollama.generate_text_stream(
            prompt, model=model, options=options, think=think, system=system,
            with_done=with_done,
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
        with_done: bool = False,
    ) -> AsyncGenerator[dict, None]:
        async for event in self._ollama.generate_vlm_stream(
            prompt, image_bytes_list, model=model, options=options, think=think,
            system=system, with_done=with_done,
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

    async def loaded(self) -> list[dict]:
        return await self._ollama.loaded()

    async def unload_all(self) -> list[str]:
        return await self._ollama.unload_all()

    async def unload(self, model: str | None = None) -> None:
        """Free the card before handing over to a renderer."""
        await self._ollama.unload(model)

    async def close(self) -> None:
        await self._ollama.close()
        await self._external.close()

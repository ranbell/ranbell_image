"""Unit tests for OpenAI-compatible LLM client helpers and gateway routing."""

from __future__ import annotations

import pytest

from app.ai.openai_compat import (
    build_user_message,
    extract_message_text,
    map_openai_options,
    stream_delta_events,
    _normalize_base_url,
)
from app.ai.llm import LlmGateway


def test_normalize_base_url_adds_v1():
    assert _normalize_base_url("http://localhost:8080") == "http://localhost:8080/v1"
    assert _normalize_base_url("http://localhost:8080/") == "http://localhost:8080/v1"
    assert _normalize_base_url("http://localhost:8080/v1") == "http://localhost:8080/v1"
    assert _normalize_base_url("http://localhost:8080/v1/") == "http://localhost:8080/v1"


def test_map_openai_options_think_and_json():
    payload = map_openai_options(
        {"temperature": 0.5, "top_p": 0.85, "top_k": 20, "num_predict": 256, "num_ctx": 16384},
        fmt="json",
        think=True,
    )
    assert payload["temperature"] == 0.5
    assert payload["top_p"] == 0.85
    assert payload["top_k"] == 20
    assert payload["max_tokens"] == 256
    assert "num_ctx" not in payload
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking_budget_tokens"] == -1
    assert payload["chat_template_kwargs"]["enable_thinking"] is True

    off = map_openai_options({}, think=False)
    assert off["thinking_budget_tokens"] == 0
    assert off["chat_template_kwargs"]["enable_thinking"] is False

    none = map_openai_options({"num_predict": -1})
    assert "max_tokens" not in none
    assert "thinking_budget_tokens" not in none


def test_build_user_message_with_images():
    plain = build_user_message("hello")
    assert plain == {"role": "user", "content": "hello"}

    msg = build_user_message("describe", [b"\xff\xd8fake"])
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert msg["content"][0] == {"type": "text", "text": "describe"}
    assert msg["content"][1]["type"] == "image_url"
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_extract_message_text_strips_think_and_falls_back():
    assert extract_message_text({"content": "ok"}) == "ok"
    assert extract_message_text({"content": "<think>secret</think>\n{\"a\":1}"}) == '{"a":1}'
    assert extract_message_text({
        "content": "",
        "reasoning_content": 'noise {"x": 1}',
    }) == 'noise {"x": 1}'


def test_stream_delta_events():
    events = stream_delta_events({"reasoning_content": "hmm", "content": "hi"})
    assert events == [
        {"type": "think", "text": "hmm"},
        {"type": "token", "text": "hi"},
    ]


class _FakeBackend:
    def __init__(self, name: str):
        self.name = name
        self.calls: list[str] = []

    async def embed(self, text, model=None):
        self.calls.append(f"embed:{self.name}")
        return [0.1, 0.2]

    async def embed_batch(self, texts, model=None):
        self.calls.append(f"embed_batch:{self.name}")
        return [[0.1]] * len(texts)

    async def generate_text(self, prompt, model=None, options=None, fmt=None, think=None):
        self.calls.append(f"gen:{self.name}:{think}:{fmt}")
        return f"from-{self.name}"

    async def generate_text_stream(self, prompt, model=None, options=None, think=None):
        self.calls.append(f"stream:{self.name}")
        yield {"type": "token", "text": self.name}

    async def generate_vlm(self, prompt, image_bytes_list, model=None, options=None, think=None):
        self.calls.append(f"vlm:{self.name}")
        return f"vlm-{self.name}"

    async def generate_vlm_stream(self, prompt, image_bytes_list, model=None, options=None, think=None):
        self.calls.append(f"vlm_stream:{self.name}")
        yield {"type": "token", "text": self.name}

    def set_base_url(self, url: str) -> None:
        self.base_url = url

    def configure(self, **kwargs) -> None:
        self.configured = kwargs
        if kwargs.get("base_url") is not None:
            self.base_url = kwargs["base_url"]
        if kwargs.get("default_model") is not None:
            self.default_model = kwargs["default_model"]

    def set_resource(self, resource) -> None:
        self.resource = resource


@pytest.mark.asyncio
async def test_gateway_defaults_to_ollama_bind_switches():
    ollama = _FakeBackend("ollama")
    openai = _FakeBackend("openai")
    gw = LlmGateway(ollama, openai, provider="ollama")

    assert await gw.generate_text("p") == "from-ollama"
    assert await gw.embed("x") == [0.1, 0.2]

    # Global configure must not flip the default route away from Ollama.
    from app.ai.llm import apply_llm_runtime_config
    apply_llm_runtime_config(gw, {
        "llm_provider": "openai",
        "openai_base_url": "http://x:8080",
        "openai_model": "bonsai",
        "vlm_model": "gemma4:e2b",
    })
    assert gw.provider == "ollama"
    assert await gw.generate_text("p") == "from-ollama"

    bound = gw.bind("openai")
    assert bound is not gw
    assert await bound.generate_text("p", fmt="json", think=True) == "from-openai"
    # Embeddings must still hit Ollama even on a bound openai view.
    assert await bound.embed("y") == [0.1, 0.2]
    assert any(c.startswith("embed:ollama") for c in ollama.calls)
    assert not any(c.startswith("embed:") for c in openai.calls)
    assert any(c.startswith("gen:openai") for c in openai.calls)

"""Inspire / Refine on an external OpenAI-compatible server.

What matters: a feature switched to the external server sends its text and
image calls there with the external model name (not the Ollama one it was
handed), embeddings stay on Ollama, and a half-filled setting fails loudly
instead of quietly running on Ollama.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.ai.llm import ExternalLlm, LlmGateway, apply_llm_runtime_config
from backend.app.ai.openai_compat import OpenAICompatClient, normalize_base_url


class FakeOllama:
    base_url = "http://ollama"

    def __init__(self):
        self.calls: list[str] = []

    def set_base_url(self, url):
        self.base_url = url

    async def embed(self, text, model=None):
        self.calls.append("embed")
        return [0.0, 1.0]

    async def generate_text(self, prompt, **kw):
        self.calls.append("generate_text")
        return "from ollama"

    async def close(self):
        pass


def _gateway(handler, **cfg) -> tuple[LlmGateway, FakeOllama]:
    ollama = FakeOllama()
    external = OpenAICompatClient()
    external._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = LlmGateway(ollama, external)
    apply_llm_runtime_config(gw, {"ollama_url": "http://ollama", **cfg})
    return gw, ollama


def test_normalize_base_url():
    assert normalize_base_url("http://h:13305") == "http://h:13305/v1"
    assert normalize_base_url("http://h:13305/") == "http://h:13305/v1"
    assert normalize_base_url("http://h:13305/api/v1") == "http://h:13305/api/v1"
    assert normalize_base_url("") == ""


def test_payload_keeps_only_portable_options():
    p = OpenAICompatClient._payload(
        [{"role": "user", "content": "x"}], "m",
        {"temperature": 0.7, "top_p": 0.9, "top_k": 64, "num_ctx": 16384, "num_predict": -1},
        "json", False, stream=False,
    )
    assert p["temperature"] == 0.7 and p["top_p"] == 0.9
    assert "top_k" not in p and "num_ctx" not in p and "max_tokens" not in p
    assert p["response_format"] == {"type": "json_object"}
    assert p["chat_template_kwargs"] == {"enable_thinking": False}
    p = OpenAICompatClient._payload([], "m", {"num_predict": 200}, None, None, stream=True)
    assert p["max_tokens"] == 200 and "chat_template_kwargs" not in p


def test_ollama_by_default():
    gw, _ = _gateway(lambda r: httpx.Response(500))
    assert gw.for_feature("inspire") is gw
    assert gw.for_feature("refine") is gw


@pytest.mark.asyncio
async def test_routed_feature_uses_external_model_and_keeps_embeddings_on_ollama():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    gw, ollama = _gateway(
        handler, openai_base_url="http://ext", openai_api_key="k",
        openai_model="text-m", openai_vlm_model="vision-m", inspire_llm_backend="openai",
    )
    ins = gw.for_feature("inspire")
    assert isinstance(ins, ExternalLlm)
    assert gw.for_feature("refine") is gw

    assert await ins.generate_text("p", model="gemma4:26b") == "hi"
    assert await ins.generate_vlm("p", [b"\x89PNG\r\n\x1a\n...."], model="gemma4:26b") == "hi"
    assert [b["model"] for b in seen] == ["text-m", "vision-m"]
    image = seen[1]["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")

    assert await ins.embed("q") == [0.0, 1.0]
    assert ollama.calls == ["embed"]


@pytest.mark.asyncio
async def test_stream_splits_reasoning_and_reports_length():
    chunks = [
        ": ping",
        {"choices": [{"delta": {"role": "assistant", "content": None}}]},
        {"choices": [{"delta": {"reasoning_content": "thinking..."}}]},
        {"choices": [{"delta": {"content": "Hello world, this is long"}}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}},
        "[DONE]",
    ]
    text = "".join(
        (c if c.startswith(":") else f"data: {c}") + "\n\n" if isinstance(c, str)
        else f"data: {json.dumps(c)}\n\n"
        for c in chunks
    )
    gw, _ = _gateway(
        lambda r: httpx.Response(200, text=text, headers={"content-type": "text/event-stream"}),
        openai_base_url="http://ext", openai_model="m", refine_llm_backend="openai",
    )
    events = [e async for e in gw.for_feature("refine").generate_text_stream("p", with_done=True)]
    assert {"type": "think", "text": "thinking..."} in events
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hello world, this is long"
    assert events[-1] == {"type": "done", "reason": "length", "prompt_tokens": 5, "eval_tokens": 9}


@pytest.mark.asyncio
async def test_stream_recovers_answer_left_in_reasoning():
    text = (
        f"data: {json.dumps({'choices': [{'delta': {'reasoning_content': 'only thoughts'}}]})}\n\n"
        "data: [DONE]\n\n"
    )
    gw, _ = _gateway(
        lambda r: httpx.Response(200, text=text),
        openai_base_url="http://ext", openai_model="m", refine_llm_backend="openai",
    )
    tokens = [e["text"] async for e in gw.for_feature("refine").generate_text_stream("p") if e["type"] == "token"]
    assert tokens == ["only thoughts"]


@pytest.mark.asyncio
async def test_half_filled_setting_fails_loudly():
    gw, ollama = _gateway(
        lambda r: httpx.Response(500), openai_model="m", inspire_llm_backend="openai",
    )
    with pytest.raises(RuntimeError, match="External LLM server URL is not set"):
        await gw.for_feature("inspire").generate_text("p")
    gw, ollama = _gateway(
        lambda r: httpx.Response(500), openai_base_url="http://ext", inspire_llm_backend="openai",
    )
    with pytest.raises(RuntimeError, match="no model is set"):
        await gw.for_feature("inspire").generate_text("p")
    assert ollama.calls == []

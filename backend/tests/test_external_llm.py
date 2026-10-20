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
        handler, external_llm_url="http://ext", external_llm_api_key="k",
        external_llm_model="text-m", external_llm_vlm_model="vision-m", inspire_llm_backend="openai",
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
        external_llm_url="http://ext", external_llm_model="m", refine_llm_backend="openai",
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
        external_llm_url="http://ext", external_llm_model="m", refine_llm_backend="openai",
    )
    tokens = [e["text"] async for e in gw.for_feature("refine").generate_text_stream("p") if e["type"] == "token"]
    assert tokens == ["only thoughts"]


@pytest.mark.asyncio
async def test_half_filled_setting_fails_loudly():
    gw, ollama = _gateway(
        lambda r: httpx.Response(500), external_llm_model="m", inspire_llm_backend="openai",
    )
    with pytest.raises(RuntimeError, match="its URL is empty"):
        await gw.for_feature("inspire").generate_text("p")
    gw, ollama = _gateway(
        lambda r: httpx.Response(500), external_llm_url="http://ext", inspire_llm_backend="openai",
    )
    with pytest.raises(RuntimeError, match="its model is empty"):
        await gw.for_feature("inspire").generate_text("p")
    assert ollama.calls == []


@pytest.mark.asyncio
async def test_an_unreachable_server_is_named_in_the_error():
    def refuse(request):
        raise httpx.ConnectError("All connection attempts failed", request=request)

    gw, _ = _gateway(
        refuse, external_llm_url="http://stale:8080/v1", external_llm_model="m",
        inspire_llm_backend="openai",
    )
    ins = gw.for_feature("inspire")
    with pytest.raises(RuntimeError, match=r"http://stale:8080/v1 is unreachable"):
        await ins.generate_text("p")
    with pytest.raises(RuntimeError, match=r"http://stale:8080/v1 is unreachable"):
        async for _ in ins.generate_text_stream("p"):
            pass


def test_the_removed_providers_settings_are_not_read():
    # v0.4.0 removed an OpenAI provider that saved these names; their values
    # are still in configs and must not come back as the external server.
    gw, _ = _gateway(
        lambda r: httpx.Response(500),
        openai_base_url="http://host.docker.internal:8080/v1", openai_model="bonsai",
        openai_api_key="old", inspire_llm_backend="openai",
    )
    assert gw.external.base_url == ""
    assert gw.external.api_key == ""
    assert gw.external_text_model == ""


@pytest.mark.asyncio
async def test_routes_report_the_servers_fixed_window():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "text-m", "context_length": 131072},
            {"id": "vision-m", "context_length": 16429},
        ]})

    gw, _ = _gateway(
        handler, external_llm_url="http://ext", external_llm_model="text-m",
        external_llm_vlm_model="vision-m", refine_llm_backend="openai",
    )
    routes = await gw.routes()
    assert routes["inspire"] == {"backend": "ollama"}
    assert routes["refine"]["backend"] == "openai"
    assert routes["refine"]["context"] == 131072
    assert routes["refine"]["vision_context"] == 16429


@pytest.mark.asyncio
async def test_a_server_that_does_not_say_its_window_gives_none():
    gw, _ = _gateway(
        lambda r: httpx.Response(200, json={"data": [{"id": "m"}]}),
        external_llm_url="http://ext", external_llm_model="m", refine_llm_backend="openai",
    )
    assert (await gw.routes())["refine"]["context"] is None


@pytest.mark.asyncio
async def test_saving_settings_deletes_the_removed_providers_keys():
    from types import SimpleNamespace

    from backend.app.api import admin

    class FakeDB:
        def __init__(self):
            self.doc = {"openai_model": "bonsai", "openai_base_url": "http://old",
                        "umap_computed_at": 1.0, "vlm_model": "g"}
            self.deleted: list[str] = []

        async def get_config(self):
            return dict(self.doc)

        async def put_config(self, data):
            self.doc.update(data)  # merges, like set_payload

        async def delete_config_keys(self, keys):
            self.deleted += keys
            for k in keys:
                self.doc.pop(k, None)

    db = FakeDB()
    gw, _ = _gateway(lambda r: httpx.Response(500))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, ollama=gw, spooler=None)))
    await admin.update_config(admin.ConfigBody(external_llm_model="m"), request)
    assert sorted(db.deleted) == ["openai_base_url", "openai_model"]
    assert "openai_model" not in db.doc and "openai_base_url" not in db.doc
    assert db.doc["umap_computed_at"] == 1.0 and db.doc["external_llm_model"] == "m"


@pytest.mark.asyncio
async def test_a_blank_setting_names_everything_missing_and_both_ways_out():
    # The state the settings rename left behind: switched to the external
    # server, URL and model both empty.
    gw, ollama = _gateway(lambda r: httpx.Response(500), inspire_llm_backend="openai")
    with pytest.raises(RuntimeError) as err:
        async for _ in gw.for_feature("inspire").generate_text_stream("p"):
            pass
    msg = str(err.value)
    assert "URL and model are empty" in msg
    assert "switch inspire back to Ollama" in msg
    assert ollama.calls == []


def test_llama_server_arguments_give_the_sampling():
    from backend.app.ai.openai_compat import _parse_llama_args
    assert _parse_llama_args("--temp 1.0 --top-p 0.95 --top-k 64 --parallel 1") == {
        "temperature": 1.0, "top_p": 0.95, "top_k": 64,
    }
    assert _parse_llama_args("") == {}


@pytest.mark.asyncio
async def test_lemonade_reports_the_loaded_models_sampling():
    def handler(request):
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"all_models_loaded": [
                {"model_name": "other", "recipe_options": {"llamacpp_args": "--temp 0.2"}},
                {"model_name": "m", "recipe_options": {"llamacpp_args": "--temp 1.0 --top-k 64"}},
            ]})
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m", "context_length": 16429}]})
        return httpx.Response(404)

    gw, _ = _gateway(
        handler, external_llm_url="http://lemonade:13305/api/v1", external_llm_model="m",
        refine_llm_backend="openai",
    )
    route = (await gw.routes())["refine"]
    assert route["sampling"] == {"temperature": 1.0, "top_k": 64}


@pytest.mark.asyncio
async def test_a_server_that_hides_its_sampling_gives_nothing():
    # The Lemonade web app answers /props with HTML; that is not a default.
    def handler(request):
        if request.url.path == "/props":
            return httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    gw, _ = _gateway(handler, external_llm_url="http://ext", external_llm_model="m",
                     refine_llm_backend="openai")
    assert await gw.external.sampling_defaults("m") == {}


def test_no_temperature_means_none_is_sent():
    from backend.app.ai.openai_compat import OpenAICompatClient
    p = OpenAICompatClient._payload([], "m", {"num_ctx": 16384}, None, None, stream=True)
    assert "temperature" not in p

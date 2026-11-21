"""**A Muse session's routes: one press, one render, and the model is out of VRAM first.** (2026-11-21)

The final shoot is the expensive moment — a full-size render on a card that has
just held a 26B model — and the panel's button can be pressed twice before the
first answer arrives. These drive the routes with a session set up the way the
panel leaves it after an approved test shot.

- A shoot is queued only after a test shot has pictures; a second press while
  it is rendering is refused, and the queue holds one render.
- The VLM is unloaded before the render is queued (CLAUDE.md: without it the
  model keeps its memory while ComfyUI places the latent).
- A session id that is not there is a 404 on every route, never a 500, and a
  delete takes that session only.
"""
from __future__ import annotations

import pytest

from backend.app.muse import ledger as ledger_mod
from backend.app.muse import session_db

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]


class _Ollama:
    """Records unloads; anything else an API test should not need."""

    def __init__(self, calls):
        self.calls = calls

    async def unload(self, model=None):
        self.calls.append(("unload", model))

    def __getattr__(self, name):
        raise AssertionError(f"the shoot reached ollama.{name}")


@pytest.fixture()
def ollama_calls(api):
    from backend.app.main import app
    calls: list = []
    app.state.ollama = _Ollama(calls)
    return calls


async def _session(api, **inputs) -> str:
    r = await api.http.post("/api/muse/sessions",
                            json={"workflow": "wf.json", "model": "vlm", "theme": "rain", **inputs})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _with_approved_board(api, sid: str) -> None:
    """As the panel leaves it: a test shot rendered, nothing changed since."""
    s = await session_db.load(api.db, sid)
    led = {**ledger_mod.blank(), **(s.get("refine_ledger") or {})}
    s["board"] = {
        "prompt": "1girl, rain", "status": "done", "pending": False, "seed": 1234,
        "images": [{"sha256": "e" * 64, "seed": 1234}],
        "ledger_fp": "|".join(str(led.get(k) or "") for k in ledger_mod.LEDGER_KEYS),
    }
    s.setdefault("inputs", {})["unload_vlm"] = True
    await session_db.save(api.db, s)


def _renders(api) -> list:
    return [j for j in api.spooler.jobs if j.title in ("muse_board", "muse_shoot", "muse_final")]


async def test_no_shoot_before_a_test_shot(api, ollama_calls):
    sid = await _session(api)
    r = await api.http.post(f"/api/muse/sessions/{sid}/shoot")
    assert r.status_code == 400
    assert _renders(api) == []


async def test_an_approved_board_queues_one_shoot_on_its_seed_after_unloading(api, ollama_calls):
    sid = await _session(api)
    await _with_approved_board(api, sid)

    r = await api.http.post(f"/api/muse/sessions/{sid}/approve")

    assert r.status_code == 200, r.text
    [job] = _renders(api)
    assert job.kwargs["session_id"] == sid
    assert ("unload", "vlm") in ollama_calls, "the model leaves VRAM before the render"
    s = await session_db.load(api.db, sid)
    assert s["shoot"]["pending"] is True and s["shoot"]["seed"] == 1234
    assert s["shoot"]["prompt"] == "1girl, rain", "the approved prompt, not a rebuilt one"


async def test_a_second_press_while_rendering_is_refused(api, ollama_calls):
    sid = await _session(api)
    await _with_approved_board(api, sid)
    assert (await api.http.post(f"/api/muse/sessions/{sid}/shoot")).status_code == 200

    r = await api.http.post(f"/api/muse/sessions/{sid}/shoot")

    assert r.status_code == 400
    assert len(_renders(api)) == 1


async def test_a_board_while_the_shoot_renders_is_refused(api, ollama_calls):
    sid = await _session(api)
    await _with_approved_board(api, sid)
    await api.http.post(f"/api/muse/sessions/{sid}/shoot")
    r = await api.http.post(f"/api/muse/sessions/{sid}/board")
    assert r.status_code == 400
    assert len(_renders(api)) == 1


# ── sessions ─────────────────────────────────────────────────────────────────

async def test_deleting_a_session_leaves_the_others(api):
    gone = await _session(api, theme="one")
    kept = await _session(api, theme="two")

    assert (await api.http.delete(f"/api/muse/sessions/{gone}")).status_code == 200

    assert (await api.http.get(f"/api/muse/sessions/{gone}")).status_code == 404
    assert (await api.http.get(f"/api/muse/sessions/{kept}")).status_code == 200


@pytest.mark.parametrize("bad", ["no-such-session", "00000000-0000-0000-0000-000000000000", "..", "x" * 300])
@pytest.mark.parametrize("method,tail", [
    ("GET", ""), ("GET", "/pipeline"), ("GET", "/debug"), ("DELETE", ""),
    ("POST", "/board"), ("POST", "/shoot"), ("POST", "/finish"), ("POST", "/chat"),
])
async def test_a_session_that_is_not_there_is_a_404(api, bad, method, tail):
    kept = await _session(api)
    body = {"message": "hi"} if tail == "/chat" else None
    r = await api.http.request(method, f"/api/muse/sessions/{bad}{tail}", json=body)
    assert r.status_code == 404, (method, tail, r.status_code, r.text[:120])
    assert (await api.http.get(f"/api/muse/sessions/{kept}")).status_code == 200
    assert _renders(api) == []

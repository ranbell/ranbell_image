"""**The family's numbers actually reach ComfyUI.** (2026-09-20)

A table nobody reads is a table that lies. What is checked here is the arguments
`run_render` is called with — steps, cfg and the negative — for a krea2 workflow
and for an anima one, from the two jobs that render anything in Muse
(`runner.run_board_job`, `runner.run_shoot_job`).

The anima half is the anti-regression pin: 20/4.0 for the board, 30/4.5 for the
shoot, and a negative that still carries what the Showrunner refused in chat.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import runner  # noqa: E402
from app.muse.defaults import ALL_DEFAULTS  # noqa: E402


def _session(workflow: str) -> dict:
    return {
        "session_id": "sess_fam",
        "inputs": {**ALL_DEFAULTS, "workflow": workflow, "draft_count": 1},
        "board": {"prompt": "1girl", "seed": 7},
        "shoot": {"prompt": "1girl", "seed": 7},
        # Something the Showrunner refused, so the negative is not empty by
        # accident on the anima side.
        "banned": ["straw_hat"],
    }


class _Db:
    def __init__(self, session: dict):
        self._qc = MagicMock()
        self._qc.upsert = AsyncMock()
        self._qc.retrieve = AsyncMock(
            return_value=[MagicMock(payload=session)],
        )


@pytest.fixture()
def render(monkeypatch):
    """`run_render` stands in for ComfyUI; its kwargs are the evidence."""
    module = MagicMock()
    module.run_render = AsyncMock(return_value={"shas": ["sha"]})
    monkeypatch.setitem(sys.modules, "app.jobs.render", module)
    return module.run_render


async def _board(db, comfy):
    return await runner.run_board_job(
        MagicMock(), MagicMock(), db=db, comfy=comfy, session_id="sess_fam")


async def _shoot(db, comfy):
    return await runner.run_shoot_job(
        MagicMock(), MagicMock(), db=db, comfy=comfy, session_id="sess_fam")


@pytest.mark.asyncio
async def test_a_krea2_workflow_renders_at_four_and_eight_with_no_negative(render):
    """His numbers, and **no negative at all** — `patch_workflow` leaves the
    graph's own negative untouched when the string is empty."""
    db = _Db(_session("krea2_flux.json"))
    comfy = MagicMock()

    await _board(db, comfy)
    kw = render.await_args.kwargs
    assert kw["steps"] == 4
    assert "cfg" not in kw          # the workflow keeps its own
    assert kw["negative"] == ""

    await _shoot(db, comfy)
    kw = render.await_args.kwargs
    assert kw["steps"] == 8 and "cfg" not in kw and kw["negative"] == ""
    # **The canvas stays the workflow's.** `run_render` defaults width/height to
    # None, and `patch_workflow` then leaves the latent alone.
    assert "width" not in kw and "height" not in kw


@pytest.mark.asyncio
async def test_an_anima_workflow_keeps_its_steps_and_its_negative(render):
    """Anima's 20/30 and its negative are the half that did not change.

    Its cfg and its canvas are the workflow's own since 2026-09-20 — sent as
    nothing, so `patch_workflow` writes neither.
    """
    db = _Db(_session("API_Anima_Hakushi_Fast.json"))
    comfy = MagicMock()

    await _board(db, comfy)
    kw = render.await_args.kwargs
    assert kw["steps"] == 20
    assert "cfg" not in kw and "width" not in kw and "height" not in kw
    assert "straw_hat" in kw["negative"]

    await _shoot(db, comfy)
    kw = render.await_args.kwargs
    assert kw["steps"] == 30
    assert "cfg" not in kw
    assert "straw_hat" in kw["negative"]


@pytest.mark.asyncio
async def test_a_workflow_that_says_nothing_is_anima(render):
    """Every workflow that exists today says nothing."""
    db = _Db(_session("my_workflow.json"))
    await _board(db, MagicMock())
    kw = render.await_args.kwargs
    assert kw["steps"] == 20 and "cfg" not in kw


@pytest.mark.asyncio
async def test_the_marker_in_the_graph_is_read_at_render_time(render):
    """A krea2 graph under an anima-looking filename still renders as krea2."""
    db = _Db(_session("my_workflow.json"))
    comfy = MagicMock()
    comfy.load_workflow.return_value = {
        "3": {"class_type": "KSampler", "_meta": {"title": "muse:family=krea2"}},
    }
    await _board(db, comfy)
    kw = render.await_args.kwargs
    assert kw["steps"] == 4 and kw["negative"] == ""


@pytest.mark.asyncio
async def test_a_size_he_set_himself_reaches_a_krea2_render(render):
    """The override half of "both ways"."""
    session = _session("krea2_flux.json")
    session["inputs"]["width"] = 1024
    session["inputs"]["height"] = 1536
    db = _Db(session)
    await _shoot(db, MagicMock())
    kw = render.await_args.kwargs
    assert (kw["width"], kw["height"]) == (1024, 1536)


@pytest.mark.asyncio
async def test_steps_he_set_himself_survive_the_family(render):
    """"4/8 as the default, and let the user change it after that.\""""
    session = _session("krea2_flux.json")
    session["inputs"]["draft_steps"] = 6
    db = _Db(session)
    await _board(db, MagicMock())
    assert render.await_args.kwargs["steps"] == 6

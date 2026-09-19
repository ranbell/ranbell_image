"""**The LLM comes out of VRAM immediately before the render.** (2026-09-07)

The Showrunner: "there is no ollama GPU VRAM unload, so comfyui falls over. Please
implement unload the same as in Muse."

One step above queueing the render, Refine's `rebuild_craft` uses the model. Hand
over to ComfyUI without giving it back and the 26B keeps ~13 GB, leaving nowhere to
put the latent.

What is watched is only **the order** — unload, then queue. Muse's `_maybe_unload`
is used as it is, so the switch (`unload_vlm`) has only one meaning.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.muse import assemble, service


class _Ollama:
    def __init__(self, log: list[str]):
        self.log = log
        self.unloaded: list[str | None] = []

    async def unload(self, model=None):
        self.log.append("unload")
        self.unloaded.append(model)


class _Spooler:
    def __init__(self, log: list[str]):
        self.log = log
        self.jobs: list[str] = []

    def submit(self, lane, name, fn, **kw):
        self.log.append(f"submit:{name}")
        self.jobs.append(name)
        return f"job-{len(self.jobs)}"


class _Db:
    def __init__(self):
        self.saved: list[dict[str, Any]] = []


async def _save(db, session, publish=True):
    session["updated_at"] = 1.0
    db.saved.append(dict(session))
    return session


@pytest.fixture
def rig(monkeypatch):
    log: list[str] = []
    ollama = _Ollama(log)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        ollama=ollama, spooler=_Spooler(log), comfy=object(),
    )))
    monkeypatch.setattr(service.session_db, "save", _save)

    async def _rebuild(db, oll, session):
        log.append("rebuild")
        session.setdefault("craft", {})["prompt"] = "1girl, solo, Mio,"
        return session

    monkeypatch.setattr(assemble, "rebuild_craft", _rebuild)
    return SimpleNamespace(log=log, ollama=ollama, request=request, db=_Db())


def _session(**over) -> dict[str, Any]:
    s = service.new_session({"workflow": "w.json", "model": "m"})
    s.update(over)
    return s


@pytest.mark.asyncio
async def test_the_model_is_dropped_before_the_board_is_queued(rig):
    await service.start_board(rig.db, rig.request, _session())

    assert rig.log == ["rebuild", "unload", "submit:muse_board"]
    assert rig.ollama.unloaded == ["m"]


@pytest.mark.asyncio
async def test_the_model_is_dropped_before_the_shoot_is_queued(rig):
    session = _session(
        board={"images": [{"image_id": "a"}], "pending": False,
               "prompt": "1girl, solo, Mio,", "ledger_fp": "", "seed": 3},
        craft={"prompt": "1girl, solo, Mio,"},
    )
    await service.start_shoot(rig.db, rig.request, session)

    assert rig.log[-2:] == ["unload", "submit:muse_shoot"]
    assert "unload" in rig.log


@pytest.mark.asyncio
async def test_the_switch_is_muses_switch(rig):
    """Turn `unload_vlm` off and nothing is unloaded — the same single decision as
    Muse's."""
    session = _session()
    session["inputs"] = {**session["inputs"], "unload_vlm": False}

    await service.start_board(rig.db, rig.request, session)

    assert "unload" not in rig.log
    assert rig.log == ["rebuild", "submit:muse_board"]

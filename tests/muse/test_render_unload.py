"""**描画の直前に LLM を VRAM から落とす。**（2026-09-07）

総監督「ollama の GPU VRAM アンロードがないので comfyui がコケてしまいます。
unload を Muse と同じく実装お願い」。

Refine は描画を積む一つ上で `rebuild_craft` がモデルを使う。返さないまま
ComfyUI に渡すと、26B が ~13GB を握ったままで latent が置けない。

見るのは**順番**だけ —— 落としてから積むこと。Muse の `_maybe_unload` を
そのまま使うので、切り替え（`unload_vlm`）の意味も一つしかない。
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
    """`unload_vlm` を切れば落とさない —— 判定は Muse と同じ一つ。"""
    session = _session()
    session["inputs"] = {**session["inputs"], "unload_vlm": False}

    await service.start_board(rig.db, rig.request, session)

    assert "unload" not in rig.log
    assert rig.log == ["rebuild", "submit:muse_board"]

"""**A draft draws a new seed; the final keeps that seed.** (2026-09-12)

The Showrunner:

    "the final right after a draft should use the same seed. The seed changing
     each time you press draft is the same as taking several photographs on a
     shoot and picking the good scene. When a draft gives you a good scene, you
     take the high-quality image with the same prompt and that seed unchanged."

Before the fix **both of them redrew**. Checked live, all six sessions holding
both a draft and a final had different seeds:

    6b0946fc  draft 3881726702135899670   final 6812618535693203985
    d07fa770  draft 13803908969248640288  final 5777939410761258192
    (6/6 mismatched)

The machinery asked with `board["seed"] = 0` (meaning "draw again"), never wrote
the seed back into the field when the render finished, and the final read
`board["seed"]`, received 0, and `runner` folded it with `0 or None` and drew
again — a swing at nothing. The seed itself had always been in each frame's meta.

The canvas is the same for a draft and a final; only steps and cfg change
(12/4.0 → 30/4.5). So matching the seed gives **the finished version of the very
picture that was approved**.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.muse import assemble, service, session_db


class _Ollama:
    async def unload(self, model=None):
        return None


class _Spooler:
    def submit(self, lane, name, fn, **kw):
        return f"job-{name}"


@pytest.fixture
def rig(monkeypatch):
    rows: dict[str, dict[str, Any]] = {}

    async def _save(db, session, publish=True):
        rows[session["session_id"]] = session
        return session

    async def _load(db, session_id):
        return rows.get(session_id)

    monkeypatch.setattr(service.session_db, "save", _save)
    monkeypatch.setattr(session_db, "save", _save)
    monkeypatch.setattr(session_db, "load", _load)
    monkeypatch.setattr(session_db.events, "publish", lambda *a, **k: None)

    async def _rebuild(db, oll, session):
        session.setdefault("craft", {})["prompt"] = "1girl, solo, Mio,"
        return session

    monkeypatch.setattr(assemble, "rebuild_craft", _rebuild)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        ollama=_Ollama(), spooler=_Spooler(), comfy=object(),
    )))
    return SimpleNamespace(request=request, db=object(), rows=rows)


def _session(**over) -> dict[str, Any]:
    s = service.new_session({"workflow": "w.json", "model": "m"})
    s.update(over)
    return s


@pytest.mark.asyncio
async def test_asking_for_a_test_shot_draws_a_new_seed_every_time(rig):
    """**0 means "draw again".** This is where the frames you choose between come
    from."""
    session = await service.start_board(rig.db, rig.request, _session())
    assert session["board"]["seed"] == 0

    # One frame is taken and the seed is settled; pressing again draws afresh from 0
    await session_db.attach_board_image(
        rig.db, session["session_id"], "sha-a", {"seed": 111, "job": "p1"},
    )
    assert rig.rows[session["session_id"]]["board"]["seed"] == 111
    await session_db.finish_board(rig.db, session["session_id"])

    again = await service.start_board(rig.db, rig.request, rig.rows[session["session_id"]])
    assert again["board"]["seed"] == 0, "セッションに一つの種は持たせない"


@pytest.mark.asyncio
async def test_the_seed_the_test_shot_used_is_written_back_to_its_own_field(rig):
    """The field was lying. Once the render finishes, the seed it used goes up into
    it."""
    session = _session(board={"seed": 0, "images": [], "pending": True})
    await session_db.save(rig.db, session)

    await session_db.attach_board_image(
        rig.db, session["session_id"], "sha-a", {"seed": 4242, "job": "p1"},
    )
    board = rig.rows[session["session_id"]]["board"]
    assert board["seed"] == 4242
    assert board["images"][0]["seed"] == 4242

    # The second frame of a batch shares the seed (the index separates the pictures).
    # Nothing is overwritten or added.
    await session_db.attach_board_image(
        rig.db, session["session_id"], "sha-b", {"seed": 4242, "job": "p1"},
    )
    assert rig.rows[session["session_id"]]["board"]["seed"] == 4242


@pytest.mark.asyncio
async def test_the_final_shoot_uses_the_seed_of_the_approved_test_shot(rig):
    session = _session(
        board={"images": [{"image_id": "a", "seed": 98765}], "pending": False,
               "prompt": "1girl, solo, Mio,", "ledger_fp": "", "seed": 98765},
        craft={"prompt": "1girl, solo, Mio,"},
    )
    out = await service.start_shoot(rig.db, rig.request, session)

    assert out["shoot"]["seed"] == 98765, "OK を出した絵と同じ種で撮ること"
    assert out["shoot"]["prompt"] == session["board"]["prompt"]


@pytest.mark.asyncio
async def test_an_older_row_still_finds_its_seed_in_the_photographs(rig):
    """A row from when there was no road up into the field. **The seed survives on
    the photograph side.**"""
    session = _session(
        board={"images": [{"image_id": "a", "seed": 0},
                          {"image_id": "b", "seed": 55555}],
               "pending": False, "prompt": "1girl, solo, Mio,",
               "ledger_fp": "", "seed": 0},
        craft={"prompt": "1girl, solo, Mio,"},
    )
    out = await service.start_shoot(rig.db, rig.request, session)

    assert out["shoot"]["seed"] == 55555


def test_zero_means_draw_again_all_the_way_down():
    """**The seam where `0` folds into `None`.** Let it through to the final and the
    picture silently becomes another one.

    `runner` passes `int(... or 0) or None`, so 0 becomes "unspecified" and
    `jobs.render` draws with `random.randint`. A draft wants that and a final does
    not — one expression carrying two meanings, so it is pinned by shape.
    """
    import inspect

    from app.muse import runner

    for fn in (runner.run_board_job, runner.run_shoot_job):
        src = inspect.getsource(fn)
        code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
        assert 'or 0) or None' in code, fn.__name__

    # `app.jobs.render` is **read from the file**. A sibling directory replaces
    # `app.*` during collection, so importing it and passing it to
    # `inspect.getsource` hits the stand-in and fails (found running the whole
    # suite).
    render_src = Path("backend/app/jobs/render.py").read_text(encoding="utf-8")
    body = render_src[render_src.index("async def run_render("):]
    assert "if seed is None:" in body
    assert "random.randint" in body

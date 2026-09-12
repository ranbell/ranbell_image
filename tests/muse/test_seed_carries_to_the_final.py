"""**試し撮りは引き直し、本番はその種のまま。**（2026-09-12）

総監督:

    「試し撮り直後の本番は同一シードにして。試し撮りを押すと seed が変わるのは、
      撮影の際に何枚も写真を取っていいシーンを選び出すのと同じ。試し撮りで
      いいシーンがあったら、そのシードを変更せず同じプロンプトで高画質の画像を
      取得するという設計です」

直す前は**両方が引き直し**だった。実機で確かめたところ、試し撮りと本番の両方を
持つ6セッションすべてで種が違っていた:

    6b0946fc  試し撮り 3881726702135899670  本番 6812618535693203985
    d07fa770  試し撮り 13803908969248640288 本番 5777939410761258192
    （6/6 不一致）

仕掛けは `board["seed"] = 0`（＝引き直して）で頼み、描き終わっても欄へ書き戻さず、
本番が `board["seed"]` を読んで 0 を受け取り、`runner` が `0 or None` で畳んで
もう一度引く、という空振りだった。種そのものは昔から一枚ごとの meta にあった。

canvas は試し撮りと本番で同じで、変わるのは steps と cfg だけ（12/4.0 → 30/4.5）。
だから種を揃えると**OK を出したのと同じ絵の仕上げ版**になる。
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
    """**0 は「引き直して」。** 選ぶための枚数がここから出てくる。"""
    session = await service.start_board(rig.db, rig.request, _session())
    assert session["board"]["seed"] == 0

    # 一枚撮れて種が決まり、もう一度押すと、また 0 から引き直す
    await session_db.attach_board_image(
        rig.db, session["session_id"], "sha-a", {"seed": 111, "job": "p1"},
    )
    assert rig.rows[session["session_id"]]["board"]["seed"] == 111
    await session_db.finish_board(rig.db, session["session_id"])

    again = await service.start_board(rig.db, rig.request, rig.rows[session["session_id"]])
    assert again["board"]["seed"] == 0, "セッションに一つの種は持たせない"


@pytest.mark.asyncio
async def test_the_seed_the_test_shot_used_is_written_back_to_its_own_field(rig):
    """欄が嘘をついていた。描き終わったら、使った種をそこへ上げる。"""
    session = _session(board={"seed": 0, "images": [], "pending": True})
    await session_db.save(rig.db, session)

    await session_db.attach_board_image(
        rig.db, session["session_id"], "sha-a", {"seed": 4242, "job": "p1"},
    )
    board = rig.rows[session["session_id"]]["board"]
    assert board["seed"] == 4242
    assert board["images"][0]["seed"] == 4242

    # batch の二枚目は同じ種（添字で絵が分かれる）。上書きして増やさない。
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
    """欄へ上げる道が無かった頃の行。**種は写真の側に残っている。**"""
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
    """**`0` が `None` に畳まれる繋ぎ。** ここが本番まで通ると黙って別の絵になる。

    `runner` は `int(... or 0) or None` で渡すので、0 は「指定なし」になり
    `jobs.render` が `random.randint` を引く。試し撮りはそれを望んでいて、
    本番は望んでいない —— 同じ式が二つの意味を持つので、形で押さえておく。
    """
    import inspect

    from app.muse import runner

    for fn in (runner.run_board_job, runner.run_shoot_job):
        src = inspect.getsource(fn)
        code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
        assert 'or 0) or None' in code, fn.__name__

    # `app.jobs.render` は**ファイルから読む**。兄弟のディレクトリが収集中に
    # `app.*` を差し替えるので、import して `inspect.getsource` に渡すと
    # 影武者に当たって落ちる（通しで走らせて踏んだ）。
    render_src = Path("backend/app/jobs/render.py").read_text(encoding="utf-8")
    body = render_src[render_src.index("async def run_render("):]
    assert "if seed is None:" in body
    assert "random.randint" in body

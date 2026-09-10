"""**二人写っている絵は、二人ぶんで読む。**（2026-09-10）

総監督「日記も混濁しています」。実機（`83d31174`）で二人の日記が食い違った:

    みおの日記   「ピンクの、あさひさんとは対照的なリボン」「あさひさんは黄色いリボン」
    あさひの日記 「アタシは赤色のリボン…みおちゃんは金色のリボン」

総監督「画像を見てコメントしてるから発明しているわけじゃないですよ」。そのとおりで、
`_read_the_photo` が本番写真を VLM に読ませている。問題は二つ:

    読ませ方が一人ぶんの文面（where **she** is, what **she** is wearing…）
    その同じ一つの説明が、二人ぶんの日記の**両方**に渡る

どちらも絵を見て言っているのに、**どっちが自分かを教わっていない**。

**一人の撮影は一字も変えない。**
"""
from __future__ import annotations

import inspect

import pytest

from app.muse import service as muse_service


def _code(fn) -> str:
    """コメントと文字列を落とした、実際に走る行だけ。

    **自分が書いた説明に引っかからないため** —— `_read_the_photo` の説明文には
    「`is_duet` は `mode` しか見ず」と書いてあるので、素の `getsource` を
    検索すると必ず当たる。
    """
    import io, tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(inspect.getsource(fn)).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)

A = {"character_id": "a", "name_ja": "各務 みお",
     "identity_tags": ["silver_hair", "bob_cut", "flat_chest"]}
B = {"character_id": "b", "name_ja": "倉田 あさひ",
     "identity_tags": ["light_green_hair", "hair_up", "medium_breasts"]}
DESC = "Two girls in a cafe."


def test_one_person_gets_nothing_added():
    """**一人の撮影を壊さない。** 写真の説明はそのまま。"""
    s = {"character": dict(A), "partner_character": {}}
    assert muse_service._which_one_is_me(s, "a", DESC) == DESC


def test_the_lead_is_told_she_is_on_the_left():
    s = {"character": dict(A), "partner_character": dict(B)}
    got = muse_service._which_one_is_me(s, "a", DESC)
    head = got.splitlines()[0]
    assert "左" in head
    assert "silver_hair" in head
    assert "倉田 あさひ" in head
    assert got.endswith(DESC)


def test_the_partner_is_told_she_is_on_the_right():
    s = {"character": dict(A), "partner_character": dict(B)}
    got = muse_service._which_one_is_me(s, "b", DESC).splitlines()[0]
    assert "右" in got
    assert "light_green_hair" in got
    assert "各務 みお" in got


def test_each_is_told_not_to_borrow_the_other():
    s = {"character": dict(A), "partner_character": dict(B)}
    for cid in ("a", "b"):
        assert "自分のものとして書かないこと" in muse_service._which_one_is_me(s, cid, DESC)


def test_an_empty_photo_read_is_left_alone():
    s = {"character": dict(A), "partner_character": dict(B)}
    assert muse_service._which_one_is_me(s, "a", "") == ""


class _Seeing:
    """絵を渡されたら、その指示文を控えて返す（模型は呼ばない）。

    形は `tests/muse/test_learning.py` の `_SeeingOllama` に合わせる ——
    この repo の写真読みの試験はこの作法で書かれている。
    """

    def __init__(self):
        self.system = ""

    def generate_vlm_stream(self, prompt, images, **kw):
        self.system = str(kw.get("system") or "")

        async def _stream():
            yield {"type": "token", "text": "described"}
        return _stream()


class _ImageDb:
    async def get_by_sha256s(self, shas):
        return [{"path": "/nonexistent.png"}]


async def _fake_images(db, shas):
    return [b"jpeg-bytes"]


def _shot_session(*, partner: bool):
    return {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "shoot": {"prompt": "1girl, cafe", "images": []},
        "character": dict(A),
        "partner_character": dict(B) if partner else {},
    }


@pytest.mark.asyncio
async def test_two_in_frame_are_read_separately(monkeypatch):
    monkeypatch.setattr(muse_service, "images_by_sha", _fake_images)
    seeing = _Seeing()
    await muse_service._read_the_photo(
        _ImageDb(), seeing, _shot_session(partner=True), "aaa",
    )
    assert "TWO girls" in seeing.system
    assert "SEPARATELY" in seeing.system
    assert "left" in seeing.system and "right" in seeing.system


@pytest.mark.asyncio
async def test_one_in_frame_is_read_exactly_as_before(monkeypatch):
    """**一人の撮影を壊さない。** 文面は改修前と一字も変わらない。"""
    monkeypatch.setattr(muse_service, "images_by_sha", _fake_images)
    seeing = _Seeing()
    await muse_service._read_the_photo(
        _ImageDb(), seeing, _shot_session(partner=False), "aaa",
    )
    assert seeing.system == (
        "You are looking at one photograph. Say what is in it, plainly "
        "and concretely, in 3\u20135 English sentences: where she is, what "
        "she is wearing, what her body is doing, and \u2014 this above all "
        "\u2014 what her face is doing. Describe only what the picture "
        "shows. Do not guess at intent, do not praise it, do not "
        "mention prompts or tags."
    )


def test_the_gate_is_never_the_mode():
    """`is_duet` は `mode` しか見ない —— 実機は一人の回も `mode: duet`。"""
    for fn in (muse_service._read_the_photo, muse_service._which_one_is_me):
        assert "is_duet" not in _code(fn), fn.__name__


def test_the_diary_job_hands_it_over():
    src = inspect.getsource(muse_service.run_generate_actress_diary_job)
    assert "_which_one_is_me(session, character_id, photo_desc)" in src

"""A refusal has to change state, not add a sentence.

Reported by the Showrunner: after talking about cleaning, the art department
added a rag; saying "don't use that" never removed it. And a garment they had
said they were finished with kept coming up in the crew's comments.

Three separate causes, all pulling the same way:

- the refusal was stored as a standing order in the Showrunner's own words and
  re-read by every seat on every turn, so the refused noun stayed in front of
  everybody forever and the crew kept discussing it
- nothing could delete a prop the art department had added — the only removal
  paths were the planner dropping its own ledger item, and Wardrobe replacing
  the outfit
- the sampler never heard about it: the negative prompt is built from settings,
  never from what the Showrunner said

So saying "no" made the thing *more* present. These tests pin the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import brief, chain, runtime, shared


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)


# ── the closed list ─────────────────────────────────────────────────────────
def test_the_clerk_can_only_pick_tags_that_are_already_there():
    """The whole reason this is its own turn. A wrong answer can only ever be a
    smaller answer — never an invented noun."""
    present = ["cleaning_rag", "bucket", "wooden_floor"]
    removed, restored = chain.parse_strike(
        "REMOVE: cleaning_rag, mop, broom\nRESTORE: none", present, [],
    )
    assert removed == ["cleaning_rag"], "mop and broom are not in the script"
    assert restored == []


def test_an_answer_in_the_wrong_language_removes_nothing():
    """The note is Japanese and the tags are English. A clerk that answers in
    Japanese has not named anything the script contains."""
    removed, _ = chain.parse_strike(
        "REMOVE: 雑巾\nRESTORE: none", ["cleaning_rag", "bucket"], [],
    )
    assert removed == []


def test_a_note_that_removes_nothing_is_the_normal_answer():
    assert chain.parse_strike("REMOVE: none\nRESTORE: none", ["a", "b"], []) == ([], [])


def test_something_can_be_asked_back():
    _, restored = chain.parse_strike(
        "REMOVE: none\nRESTORE: cleaning_rag", ["bucket"], ["cleaning_rag"],
    )
    assert restored == ["cleaning_rag"]


# ── carrying it out ─────────────────────────────────────────────────────────
def _session(tags: str) -> dict:
    return {
        "session_id": "s",
        "inputs": {"locale": "ja", "crew_preset": "standard", "framing": "auto"},
        "character": {"identity_tags": ["1girl", "silver_hair"]},
        "craft": {"tags": tags, "scene": "She stands.", "prompt": ""},
        "notes": [], "banned": [], "ledger": [],
    }


def test_the_sampler_finally_hears_the_word_no():
    """The negative prompt is the only place in the pipeline where "do not draw
    this" is a mechanism rather than a request."""
    session = _session("standing")
    session["banned"] = ["cleaning_rag", "mop"]
    negative = runtime.negative_for(session)
    assert "cleaning_rag" in negative and "mop" in negative


# ── what the crew is told ───────────────────────────────────────────────────
def test_the_refused_noun_is_named_once_and_then_never_again():
    """The seats answering the note need to know why something vanished, or they
    put it straight back. Nobody after them is told the noun at all."""
    notes = ["掃除してる感じで", "雑巾は使わないで"]
    now = brief.orders_block(notes, carried_out=[1], removed_now=["cleaning_rag"])
    assert "cleaning_rag" in now
    assert "雑巾は使わないで" not in now, "the note's own words are dropped"

    later = brief.orders_block(notes, carried_out=[1])
    assert "cleaning_rag" not in later
    assert "雑巾" not in later
    assert "掃除してる感じで" in later, "the rest of the direction survives"


def test_the_newest_order_wins_and_says_so():
    block = brief.orders_block(["冬にして", "やっぱり夏で"])
    assert block.index("やっぱり夏で") < block.index("冬にして")
    assert "NEWEST FIRST" in block


def test_every_seat_is_told_not_to_name_it_even_to_deny_it():
    from app.muse import crew
    for mid in ("propshop:takarabako", "wardrobe:shiwa", "beat:ichibyou"):
        text = crew.system_prompt_for(mid)
        assert "WHAT THE SHOWRUNNER HAS REFUSED" in text, mid
        assert "NOT IN SAY EITHER" in text, mid




def test_the_contract_lets_her_decide_when_she_is_asked_to():
    """出演契約に「**選んでよい**」がある。

    総監督（2026-08-30）「『どうしたい』『したいことしてみて』などの自主的な
    行動を求められた場合、**自らの希望する行動をとっていい**ことにしないと
    いけない。これがないと、どうしようどうしようと何も決まらない状態となり、
    回答の判断が難しくなる」。

    契約には「降りてよい」（五条）はあったが「選んでよい」は無かった。断る
    権利だけがあって、選ぶ権利が無い。

    実測（`ask_decide.py`・5件×3回）では条項だけでは足りない —— 具体
    9/15 対 9/15、迷い 5/15 → 3/15。「決めていい」は伝わるが「決めなさい」
    までは押せない。中身は変わった:

        条項なし 「総監督の好きなように指示してください。」
        条項あり 「公園のベンチに座って、ふとした瞬間の、
                 どこか寂しそうな顔を撮ってください。」

    害は無く、押しの強い提案が出る側に振れるので入れる。**訊かれたターンだけ
    彼女に決めさせる**仕組みは別に組む。
    """
    from app.muse import crew

    # **条文は英語へ戻した（2026-09-04）。** もともと英語で、最近まるごと
    # 日本語にしたものだった。総監督「英文のほうが誤解釈されにくい」。
    # 判定する語だけ差し替える —— 上の理由はそのまま効いている。
    import re

    contract = re.sub(r"\s+", " ", crew.production_contract())
    assert "Not choosing is not an answer" in contract
    # **迷いは禁じない。** 彼女らしさはそこにあるので、消すと別人になる。
    assert "One line of hesitation at most" in contract
    # **訊かれた範囲で。** ポーズを訊かれて撮影ごと動かさない。
    assert "Within what was asked" in contract
    # 決められない理由はたいてい「間違えたらどうしよう」。
    assert "Whatever you decide, he can change it later" in contract
    # 断る権利（五条）は残っている —— 選ぶ権利はその隣であって、代わりでは
    # ない。
    assert "you may step down" in contract

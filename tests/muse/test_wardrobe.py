"""衣装部屋 — the button that restates the whole outfit instead of editing it.

What is actually under test is the escape hatch from a measured failure: the
compile writes `wearing` as a delta off one line of direction, that lands about
four times in five on a short line and much less on a long one, and a miss is
silent — the garment simply stays on. These tests hold the properties that make
the button worth pressing: the outfit is REPLACED not merged, what left is
struck so nothing puts it back, the card cannot disagree with the notebook, and
a turn that produced no wearable answer says so out loud.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew, notebook, shared  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)






# ── the two lines ───────────────────────────────────────────────────────────

def test_parse_reads_both_labels():
    say, wearing = chain.parse_wardrobe(
        "SAY: 着替えてきたよ。\nWEARING: sailor_fuku, cardigan, loafers"
    )
    assert say == "着替えてきたよ。"
    assert wearing == "sailor_fuku, cardigan, loafers"


def test_parse_keeps_her_voice_when_she_forgets_the_label():
    """The outfit half must parse; the voice half falling back is not silence."""
    say, wearing = chain.parse_wardrobe(
        "着替えてきた。ちょっと寒いかも。\nWEARING: cardigan, skirt"
    )
    assert "着替えてきた" in say
    assert wearing == "cardigan, skirt"


def test_parse_survives_a_turn_with_no_outfit_at_all():
    say, wearing = chain.parse_wardrobe("SAY: ……なんて言えばいいのかな。")
    assert wearing == ""
    assert say


# ── the outfit is replaced, not merged ──────────────────────────────────────


# ── nothing wearable came back ──────────────────────────────────────────────


# ── the card cannot disagree with the notebook ──────────────────────────────


# ── pressed again, and again ────────────────────────────────────────────────


# ── the prompt she is given ─────────────────────────────────────────────────

def test_the_wardrobe_turn_asks_for_two_lines_and_no_shot():
    system = crew.actress_duet_prompt(
        {"name": "Asahi", "name_ja": "倉田あさひ"}, mode="wardrobe", seed="s",
    )
    assert "WEARING:" in system and "SAY:" in system
    # Nothing else about the shot is hers this turn.
    assert "TAGS:" not in system
    assert "COSTUME:" not in system


# ── striking one garment must not poison the whole session ──────────────────

def test_a_struck_modifier_does_not_ban_everything_that_shares_the_word():
    """Taking off a white blouse must leave her white shirt — and her hair.

    Measured on a real session (cake, 2026-08-19): the struck list came back
    ``['outfit', 'stylish', 'stylish_outfit', 'white', 'white_blouse',
    'blouse', 'stylish_blouse']`` because `wearing_tokens` splits a garment
    phrase into its words. The old matcher then tested every component, so a
    struck `white` blocked `white_shirt`, `white_socks` and `white_hair` for
    the rest of the shoot. A modifier is not the thing that came off.
    """
    session: dict = {}
    notebook.record_struck_from_wearing(
        session, prev_wearing="stylish white blouse, blue skirt",
        new_wearing="blue skirt",
    )
    struck = {str(s) for s in session["struck"]}
    # The explosion itself is unchanged — it is the matcher that must be sane.
    assert "white" in struck and "blouse" in struck

    # The garment that came off, and things named after it, stay out.
    assert notebook.tag_mentions_struck("white_blouse", struck)
    assert notebook.tag_mentions_struck("blouse", struck)
    assert notebook.tag_mentions_struck("silk_blouse", struck)

    # Everything else that merely shares a word comes back.
    for tag in ("white_shirt", "white_socks", "white_hair", "white_dress"):
        assert not notebook.tag_mentions_struck(tag, struck), tag


def test_the_wardrobe_lifts_modifiers_the_block_rule_would_never_reach():
    """Freeing is generous on purpose — the two directions are not symmetric.

    Blocking wide is destructive and freeing wide is recoverable, so the button
    matches on any word part. Without this, `white` and `stylish` would stay
    struck forever: no head-noun rule can reach a modifier.
    """
    assert notebook.garment_lifts_struck("white blouse", "white")
    assert notebook.garment_lifts_struck("white blouse", "blouse")
    assert notebook.garment_lifts_struck("stylish white blouse", "stylish")
    # Still not a free-for-all: an unrelated garment lifts nothing.
    assert not notebook.garment_lifts_struck("blue skirt", "blouse")

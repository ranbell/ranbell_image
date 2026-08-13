"""Lounge share/reaction parsing and friends_of ranking."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import compat
from backend.app.muse import lounge


def test_parse_labelled_share_and_normalize():
    raw = """
TEXT_JA: 今日は屋上で撮ったよ！監督が振り返りの表情いいって
TEXT_EN: Shot on the rooftop — director liked my looking-back face
POSE: looking back
OUTFIT: sailor uniform
EXPRESSION: soft smile
PLACE: rooftop
VIBE: windy dusk
"""
    parsed = lounge.parse_labelled(raw)
    fields = lounge.normalize_share(parsed)
    assert "屋上" in fields["text_ja"]
    assert fields["tags"]["pose"] == "looking back"
    assert fields["tags"]["place"] == "rooftop"


def test_normalize_reactions_maps_friends():
    friends = [
        {"id": "a", "name_ja": "アヤ", "name": "Aya", "tier": "best_friend", "score": 0.9},
        {"id": "b", "name_ja": "ミオ", "name": "Mio", "tier": "close", "score": 0.5},
    ]
    raw = """
REACTOR_1_REACTION: 💕
REACTOR_1_JA: いいねそれ！私も振り返りやってみたい
REACTOR_1_EN: Love that — I want to try looking back too
REACTOR_1_STANCE: try
REACTOR_2_REACTION: ✨
REACTOR_2_JA: 私なら前髪を見せるほうが好きかも
REACTOR_2_EN: I'd rather show my bangs though
REACTOR_2_STANCE: twist
REACTOR_2_TWIST: 前髪チラ見せ
"""
    reacts = lounge.normalize_reactions(lounge.parse_labelled(raw), friends)
    assert len(reacts) == 2
    assert reacts[0]["character_id"] == "a"
    assert reacts[0]["stance"] == "try"
    assert reacts[1]["stance"] == "twist"
    assert reacts[1]["twist"] == "前髪チラ見せ"


def test_pitch_chance_boosts_for_outgoing_traits():
    shy = lounge.pitch_chance({"personality": {"traits": ["shy", "quiet"]}})
    bold = lounge.pitch_chance({"personality": {"traits": ["bold", "curious", "creative"]}})
    assert 0.1 <= shy <= 0.2
    assert bold > shy
    assert bold <= 0.45


def test_should_write_habit_needs_notes():
    rng = __import__("random").Random(0)
    assert lounge.should_write_habit(notes=[], rng=rng) is False
    # Fixed seed: eventually true within a few rolls when notes exist.
    hits = sum(
        1 for i in range(40)
        if lounge.should_write_habit(notes=["逆光が好き"], rng=__import__("random").Random(i))
    )
    assert hits >= 1


@pytest.mark.asyncio
async def test_lounge_summary_dedupes_new_open_pitch(monkeypatch):
    async def fake_list(_db, *, limit=100, kind=""):
        return [
            {"id": "p1", "kind": "pitch", "status": "open", "created_at": 200.0},
            {"id": "w1", "kind": "wrap_share", "created_at": 150.0},
            {"id": "old", "kind": "wrap_share", "created_at": 10.0},
            # Reaction bump after last peek — should count as new via updated_at.
            {"id": "w2", "kind": "wrap_share", "created_at": 50.0, "updated_at": 180.0},
        ]

    from backend.app.muse import lounge_db
    monkeypatch.setattr(lounge_db, "list_threads", fake_list)
    out = await lounge_db.summary(object(), since=100.0)
    assert out["new_threads"] == 3
    assert out["open_pitches"] == 1
    assert out["unread"] == 3  # p1, w1, w2 — p1 not double-counted


def test_normalize_pitch_and_habit():
    pitch = lounge.normalize_pitch(lounge.parse_labelled(
        "TEXT_JA: 次は窓辺でどうでしょう？\nTEXT_EN: How about by the window next?"
    ))
    assert "窓辺" in pitch["text_ja"]
    habit = lounge.normalize_habit(lounge.parse_labelled(
        "TITLE_JA: 逆光好き\nTITLE_EN: Backlight fan\n"
        "BODY_JA: 監督は逆光にこだわりがち。\nBODY_EN: They linger on backlight."
    ))
    assert habit["title"] == "逆光好き"
    assert "逆光" in habit["body_ja"]


@pytest.mark.asyncio
async def test_friends_of_ranks_best_friend_first(monkeypatch):
    class FakeDB:
        pass

    async def fake_matrix(_db):
        return {
            "characters": [
                {"id": "me", "name": "Me", "name_ja": "私", "board": {}},
                {"id": "best", "name": "Best", "name_ja": "親友", "board": {}},
                {"id": "close", "name": "Close", "name_ja": "仲良し", "board": {}},
                {"id": "acq", "name": "Acq", "name_ja": "顔見知り", "board": {}},
            ],
            "pairs": [
                {"a": "me", "b": "acq", "score": 0.2, "tier": "acquaintance", "co_appearances": 0},
                {"a": "me", "b": "close", "score": 0.5, "tier": "close", "co_appearances": 1},
                {"a": "me", "b": "best", "score": 0.8, "tier": "best_friend", "co_appearances": 3},
            ],
        }

    monkeypatch.setattr(compat, "compat_matrix", fake_matrix)
    friends = await compat.friends_of(FakeDB(), "me", min_tier="close", limit=5)
    assert [f["id"] for f in friends] == ["best", "close"]

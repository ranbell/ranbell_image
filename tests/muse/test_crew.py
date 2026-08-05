"""Fictional Muse roster — cast presets and table-read voice."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import crew


def test_resolve_crew_always_ends_with_finisher():
    ids = crew.resolve_crew(preset="lightning")
    assert ids[-1] == "finisher"
    assert "beat" in ids
    assert "wardrobe" in ids
    assert "actress" in ids
    assert ids.index("actress") < ids.index("faces")


def test_resolve_crew_honours_explicit_ids():
    ids = crew.resolve_crew(crew_ids=["lens", "wardrobe", "unknown"])
    assert ids == ["lens", "wardrobe", "actress", "finisher"]


def test_actress_prompt_pulls_selected_character_personality():
    character = {
        "name": "The Tank Guide",
        "name_ja": "水族館ガイド",
        "personality": {
            "traits": ["enthusiastic", "sincere"],
            "summary_ja": "同じ魚の話を日に四十回する。",
            "inner_ja": ["一人になると大水槽の前で黙る"],
            "likes": ["feeding time"],
            "dislikes": ["tapping on glass"],
        },
        "expression_vocab": ["smile", "open_mouth"],
        "gesture_vocab": ["walking", "looking_up"],
    }
    text = crew.actress_system_prompt(character)
    assert "水族館ガイド" in text
    assert "enthusiastic" in text
    assert "一人になると大水槽の前で黙る" in text
    assert "smile" in text
    assert "FIRST PERSON" in text or "一人称" in text
    assert "never props" in text.lower() or "Never draw likes" in text


def test_system_prompt_keeps_say_tags_scene_and_english_craft():
    text = crew.system_prompt_for("beat")
    assert "OUTPUT FORMAT" in text
    assert "SAY:" in text
    assert "TAGS:" in text
    assert "SCENE:" in text
    assert "English only" in text
    assert "You are Beat" in text
    assert "口調 (JA)" in text
    assert "EXAMPLE SAY" in text
    assert "conversation" in text.lower() or "RECENT TABLE TALK" in text
    assert crew.MUSES["beat"]["say_example"]
    assert crew.MUSES["spine"]["voice_ja"] != crew.MUSES["faces"]["voice_ja"]


def test_banter_prompt_is_say_only():
    text = crew.banter_system_prompt_for("hook")
    assert "SAY:" in text
    assert "TAGS" not in text or "Do NOT output TAGS" in text
    assert "heckling" in text.lower() or "SIDE COMMENT" in text


def test_public_roster_has_no_real_creator_names():
    roster = crew.public_roster()
    names = " ".join(m["name"] for m in roster["muses"]).lower()
    # Guard against accidentally shipping real creator shout-outs.
    for banned in ("greg", "rutkowski", "artis", "wlop", "mucha"):
        assert banned not in names
    assert roster["default_preset"] == "gallery"
    assert "lightning" in roster["presets"]

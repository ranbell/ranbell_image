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


def test_resolve_crew_honours_explicit_ids():
    ids = crew.resolve_crew(crew_ids=["lens", "wardrobe", "unknown"])
    assert ids == ["lens", "wardrobe", "finisher"]


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

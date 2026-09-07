"""Conversation-driven atmosphere / look cues (no UI buttons)."""
from __future__ import annotations

from app.muse_refine import assemble, talk


def test_cue_emo_and_cel():
    emo = talk.cue_atmosphere_look("もうちょっとエモく、切ない感じで")
    assert "wistful" in emo.get("atmosphere", "").lower() or "melanchol" in emo.get("atmosphere", "").lower()

    cel = talk.cue_atmosphere_look("カチッとしたセル画でお願い")
    assert "cel" in cel.get("look", "").lower() or "lineart" in cel.get("look", "").lower()


def test_cue_fantasy():
    patch = talk.cue_atmosphere_look("ファンタジーっぽい魔法の空気で")
    assert "fantasy" in patch.get("look", "").lower()


def test_cue_reset():
    patch = talk.cue_atmosphere_look("画風リセット")
    assert patch.get("look") == ""
    assert patch.get("atmosphere") == ""


def test_merge_cue_writer_wins():
    writer = {"atmosphere": "tense, sharp"}
    cue = {"atmosphere": "cozy, warm", "look": "watercolor, soft edges"}
    merged = talk.merge_cue_into_patch(writer, cue)
    assert merged["atmosphere"] == "tense, sharp"
    assert merged["look"] == "watercolor, soft edges"


def test_assemble_includes_look_and_atmosphere():
    session = {
        "character": {
            "identity_tags": ["1girl", "silver_hair", "blue_eyes"],
            "character_id": "mio",
            "name": "Mio",
        },
        "inputs": {"framing": "cowboy", "style": "", "enhance_quality": False},
    }
    led = {
        "wearing": "white dress",
        "beat": "standing, looking at viewer",
        "expression": "soft smile",
        "scene": "moonlit lake",
        "light": "cool blue moonlight",
        "bg": "mist over water",
        "frame": "cowboy shot",
        "atmosphere": "wistful, melancholic, tender ache",
        "look": "fantasy illustration, magical aura, glowing particles",
        "lettering": "",
        "wearing_b": "",
        "beat_b": "",
    }
    prompt = assemble.assemble_prompt(session, led, enhance_quality=False)
    low = prompt.lower()
    assert "wistful" in low or "melanchol" in low
    assert "fantasy" in low or "magical" in low
    assert "\n\n" in prompt
    # Longer cinematic prose
    assert len(prompt) > 280
    prose = assemble.scene_prose(led, name_a="Mio")
    assert "air feels" in prose.lower() or "wistful" in prose.lower()
    assert "render" in prose.lower() or "fantasy" in prose.lower()

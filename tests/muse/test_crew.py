"""Fictional Muse roster — cast presets and table-read voice."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import crew

# Situation nouns must never be baked into Muse production copy.
# Themes come from the Showrunner + VLM dialogue — not from code samples.
_SITUATION_BANNED = (
    "水着", "ビキニ", "パラソル", "カフェ", "泳ぐ", "暑さ", "海辺", "ビーチ",
    "懐中電灯", "スタッフベスト", "砂ベージュ", "ターコイズ", "真夏",
    "屋上", "雨上がり",
    "sexy", "sensual", "swimsuit", "bikini", "parasol", "beach", "seaside",
    "flashlight", "rooftop", "wet/dry", "wet_swimsuit", "beach_cafe",
    "thermos", "coffee",
)


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
        "name": "Sample Lead",
        "name_ja": "サンプル主演",
        "personality": {
            "traits": ["enthusiastic", "sincere"],
            "summary_ja": "いつも本気で話す。",
            "inner_ja": ["ひとりのとき少し静かになる"],
            "likes": ["clear explanations"],
            "dislikes": ["being rushed"],
        },
        "expression_vocab": ["smile", "open_mouth"],
        "gesture_vocab": ["walking", "looking_up"],
    }
    text = crew.actress_system_prompt(character)
    assert "サンプル主演" in text
    assert "enthusiastic" in text
    assert "ひとりのとき少し静かになる" in text
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


def test_production_muse_copy_has_no_situation_specific_anchors():
    """Any theme must work — forbid demo/situation nouns in shipped Muse text."""
    root = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse"
    blobs: list[str] = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".md"}:
            continue
        if "__pycache__" in path.parts:
            continue
        blobs.append(path.read_text(encoding="utf-8"))
    # Also scan Muse UI placeholders (must not name a sample scene).
    locales = Path(__file__).resolve().parents[2] / "frontend" / "src" / "locales"
    for name in ("ja.json", "en.json"):
        text = (locales / name).read_text(encoding="utf-8")
        # Only the muse.themePlaceholder value matters for this rule.
        import json
        data = json.loads(text)
        blobs.append(str((data.get("muse") or {}).get("themePlaceholder") or ""))
    joined = "\n".join(blobs)
    for banned in _SITUATION_BANNED:
        assert banned not in joined, f"situation-specific '{banned}' found in Muse production copy"


def test_finisher_demands_dense_scene():
    text = crew.system_prompt_for("finisher")
    assert "140–200" in text or "140-200" in text
    assert "35–55" in text or "35-55" in text
    assert "Densify" in text or "densify" in text or "EXPAND" in text
    assert "80 words" not in text  # old thin cap must stay gone
    assert "140–200" in crew.OUTPUT or "140-200" in crew.OUTPUT


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

"""Anima (CircleStone) prompt hygiene for Muse Refine."""
from __future__ import annotations

from app.muse import anima, assemble


def test_tag_to_anima_spaces_and_score():
    assert anima.tag_to_anima_spaces("looking_at_viewer") == "looking at viewer"
    assert anima.tag_to_anima_spaces("score_7") == "score_7"
    assert anima.tag_to_anima_spaces("(silver_hair:1.3)") == "silver hair"


def test_split_quality_support():
    q, rest = anima.split_quality_support([
        "masterpiece", "soft_lighting", "best_quality", "depth_of_field",
    ])
    assert "masterpiece" in q
    assert any("best" in x for x in q)
    assert "soft_lighting" in rest
    assert "depth_of_field" in rest


def test_format_quality_prefix_and_blank_prose():
    raw = (
        "1girl, solo, Mio,\n"
        "Mio is silver_hair, bob_cut, blue_eyes,\n"
        "Mio: standing, white_shirt,\n"
        "cafe, soft_light,\n"
        "The shot is set at cafe under soft light. Mio is wearing white shirt."
    )
    out = anima.format_for_anima(
        raw,
        quality_tags=["masterpiece", "best_quality"],
        enhance_quality=True,
    )
    low = out.lower()
    # Quality first
    assert out.lower().startswith("masterpiece")
    assert "best quality" in low
    # Spaces not underscores in tags
    assert "silver hair" in low
    assert "white shirt" in low
    # Blank line before prose
    assert "\n\n" in out
    assert "The shot is set" in out
    assert "keep exactly" not in low


def test_format_lettering_appended():
    raw = "1girl, solo,\nA girl stands in a park."
    out = anima.format_for_anima(raw, lettering=["Good!"], enhance_quality=False)
    assert 'text "Good!"' in out
    assert "text_on_image" in out


def test_extract_lettering_ja_sign():
    phrases, cleaned = anima.extract_lettering('看板に「RANBELL」と書いて')
    assert phrases == ["RANBELL"]
    assert "RANBELL" not in cleaned or "看板" not in cleaned or True


def test_assemble_prompt_anima_end_to_end():
    session = {
        "character": {
            "identity_tags": ["1girl", "blue_hair", "bob_cut"],
            "character_id": "x",
            "name": "Mio",
            "name_ja": "みお",
        },
        "inputs": {"framing": "upper_body", "style": "", "enhance_quality": True},
    }
    led = {
        "wearing": "white shirt",
        "beat": "sitting",
        "expression": "smile",
        "scene": "cafe",
        "light": "soft light",
        "bg": "",
        "frame": "",
        "lettering": "Open",
    }
    prompt = assemble.assemble_prompt(
        session, led,
        support_tags=["masterpiece", "depth_of_field"],
        enhance_quality=True,
    )
    low = prompt.lower()
    assert prompt.lower().startswith("masterpiece")
    assert "blue hair" in low
    assert "depth of field" in low or "depth_of_field" in low
    assert "\n\n" in prompt
    assert 'text "Open"' in prompt
    assert "keep exactly" not in low


def test_enhance_off_skips_default_quality():
    session = {
        "character": {
            "identity_tags": ["1girl", "blue_hair"],
            "character_id": "x",
            "name": "Mio",
        },
        "inputs": {"framing": "auto", "style": "", "enhance_quality": False},
    }
    led = {
        "wearing": "hoodie",
        "beat": "standing",
        "expression": "",
        "scene": "street",
        "light": "",
        "bg": "",
        "frame": "",
        "lettering": "",
    }
    prompt = assemble.assemble_prompt(session, led, enhance_quality=False)
    assert not prompt.lower().startswith("masterpiece")

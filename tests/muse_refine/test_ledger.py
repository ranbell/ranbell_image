"""Unit tests for Muse Refine ledger + assemble (no live LLM)."""
from __future__ import annotations

from app.muse_refine import assemble, ledger


def test_apply_patch_absolute_and_independent():
    led = ledger.blank()
    led = ledger.apply_patch(led, {
        "wearing": "white shirt, blue skirt",
        "scene": "rooftop at dusk",
        "beat": "standing",
    })
    assert led["wearing"].startswith("white shirt")
    assert "rooftop" in led["scene"]

    # Clothes change must not clear scene.
    led = ledger.apply_patch(led, {"wearing": "red dress"})
    assert led["wearing"] == "red dress"
    assert "rooftop" in led["scene"]
    assert led["beat"] == "standing"


def test_simultaneous_clothes_and_scene():
    led = ledger.apply_patch(ledger.blank(), {
        "wearing": "sailor uniform",
        "scene": "classroom",
    })
    led = ledger.apply_patch(led, {
        "wearing": "hoodie",
        "scene": "park",
    })
    assert led["wearing"] == "hoodie"
    assert led["scene"] == "park"


def test_wearing_drop():
    led = ledger.apply_patch(ledger.blank(), {
        "wearing": "white shirt, hat, blue skirt",
    })
    led = ledger.apply_patch(led, {"wearing_drop": "hat"})
    assert "hat" not in led["wearing"].lower()
    assert "shirt" in led["wearing"].lower()


def test_empty_patch_is_noop_touch():
    assert not ledger.touched_picture({})
    assert ledger.touched_picture({"wearing": "coat"})


def test_merge_support_keeps_authority_first():
    base = ["white_shirt", "rooftop"]
    merged = assemble.merge_support_tags(
        base,
        ["cinematic_lighting", "depth_of_field", "white_shirt"],
        authority=base,
        cap=10,
    )
    assert merged[0] == "white_shirt"
    assert "cinematic_lighting" in merged
    assert merged.count("white_shirt") == 1


def test_assemble_prompt_includes_ledger(monkeypatch):
    session = {
        "character": {"identity_tags": ["1girl", "blue_hair"], "character_id": "x"},
        "inputs": {"framing": "upper_body", "style": ""},
    }
    led = {
        "wearing": "white shirt",
        "beat": "sitting",
        "expression": "smile",
        "scene": "cafe",
        "light": "soft light",
        "bg": "",
        "frame": "",
    }
    prompt = assemble.assemble_prompt(session, led, support_tags=["depth_of_field"])
    low = prompt.lower()
    assert "blue_hair" in low or "1girl" in low
    assert "white" in low or "shirt" in low
    assert "cafe" in low or "sitting" in low

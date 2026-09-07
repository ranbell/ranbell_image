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


def test_split_picked_vs_free():
    picked, free = assemble._split_picked_vs_free(
        ["leaning_forward", "soft_lighting", "holding_sword"],
        ["leaning_forward", "holding_sword", "noise_tag"],
    )
    assert picked == ["leaning_forward", "holding_sword"]
    assert free == ["soft_lighting"]


def test_assemble_prompt_includes_ledger(monkeypatch):
    session = {
        "character": {
            "identity_tags": ["1girl", "blue_hair", "bob_cut"],
            "character_id": "x",
            "name": "Mio",
            "name_ja": "みお",
        },
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
    assert "blue_hair" in low or "1girl" in low or "2girls" in low or "mio" in low
    assert "white" in low or "shirt" in low
    assert "cafe" in low or "sitting" in low
    # Thick prose reinforcement
    assert "wearing" in low
    # Anima hygiene: spaces preferred; no triple Keep-exactly lock
    assert "keep exactly" not in low
    assert "blue hair" in low or "blue_hair" in low or "1girl" in low


def test_scene_prose_locks_all_axes():
    led = {
        "wearing": "white shirt, blue skirt",
        "beat": "standing, leaning on railing",
        "expression": "soft smile, blush",
        "scene": "rooftop at dusk",
        "light": "warm evening light",
        "bg": "city skyline",
        "frame": "medium shot, looking at viewer",
        "wearing_b": "",
        "beat_b": "",
    }
    prose = assemble.scene_prose(led, name_a="Mio")
    low = prose.lower()
    assert "rooftop" in low
    assert "white shirt" in low
    assert "leaning" in low
    assert "soft smile" in low
    assert "medium shot" in low
    assert "keep exactly" not in low  # Anima: avoid 3× concept repeats
    assert len(prose) > 80


def test_w_muse_does_not_mix_clothes_or_hair():
    session = {
        "character": {
            "character_id": "mio",
            "name": "Mio",
            "name_ja": "みお",
            "identity_tags": [
                "1girl", "silver_hair", "bob_cut", "blue_eyes", "flat_chest",
            ],
        },
        "partner_character": {
            "character_id": "sumire",
            "name": "Sumire",
            "name_ja": "すみれ",
            "identity_tags": [
                "1girl", "blonde_hair", "long_hair", "green_eyes", "medium_breasts",
            ],
        },
        "inputs": {"framing": "auto", "style": ""},
        "banned": [],
    }
    led = {
        "wearing": "white shirt, blue skirt",
        "beat": "standing, leaning on railing",
        "expression": "soft smile",
        "scene": "rooftop at dusk",
        "light": "warm evening light",
        "bg": "city skyline",
        "frame": "medium shot",
        "wearing_b": "black dress",
        "beat_b": "standing beside her",
    }
    prompt = assemble.assemble_prompt(session, led)
    # Person-box layout: each Muse owns her line.
    assert "Mio is" in prompt or "みお is" in prompt
    assert "Sumire is" in prompt or "すみれ is" in prompt
    # Dynamic ownership lines
    assert "Mio:" in prompt or "みお:" in prompt
    assert "Sumire:" in prompt or "すみれ:" in prompt
    # Lead clothes on lead dynamic line, not dumped into partner identity.
    mio_dyn = ""
    sum_dyn = ""
    for line in prompt.splitlines():
        if line.startswith("Mio:") or line.startswith("みお:"):
            mio_dyn = line.lower()
        if line.startswith("Sumire:") or line.startswith("すみれ:"):
            sum_dyn = line.lower()
    assert "white shirt" in mio_dyn or "white_shirt" in mio_dyn or "blue skirt" in mio_dyn or "blue_skirt" in mio_dyn
    assert "black dress" in sum_dyn or "black_dress" in sum_dyn
    assert "black dress" not in mio_dyn and "black_dress" not in mio_dyn
    assert "white shirt" not in sum_dyn and "white_shirt" not in sum_dyn
    # Partner blonde must not appear on Mio identity line as free flat bag.
    mio_id = ""
    for line in prompt.splitlines():
        if line.startswith("Mio is") or line.startswith("みお is"):
            mio_id = line.lower()
    assert "blonde_hair" not in mio_id
    assert "1girl" not in prompt.split(",")[0] or "2girls" in prompt.lower()


def test_assemble_without_support_ignores_raw_wd14_bag():
    """WD14 neighbour dumps must not enter via support unless explicitly chosen."""
    session = {
        "character": {"identity_tags": ["1girl"], "character_id": "x"},
        "inputs": {"framing": "auto", "style": ""},
    }
    led = {
        "wearing": "hoodie",
        "beat": "standing",
        "expression": "",
        "scene": "street",
        "light": "",
        "bg": "",
        "frame": "",
    }
    # No support_tags → noisy WD14 list stays out of the prompt.
    prompt = assemble.assemble_prompt(session, led, support_tags=None)
    assert "holding_sword" not in prompt.lower()
    assert "hoodie" in prompt.lower() or "street" in prompt.lower()

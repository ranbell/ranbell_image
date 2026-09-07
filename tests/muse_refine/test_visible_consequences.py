"""Visible consequence expansion for Muse Refine assemble (craft-only)."""
from app.muse_refine import assemble, ledger as ledger_mod


def test_wind_behind_implies_nape_and_floating_hair():
    led = {
        **ledger_mod.blank(),
        "beat": "standing, hair blowing in the wind",
        "frame": "from behind",
        "atmosphere": "strong wind on the rooftop",
        "wearing": "white shirt",
        "scene": "rooftop at dusk",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "floating_hair" in cues["tags"]
    assert "nape" in cues["tags"]
    assert "from_behind" in cues["tags"]
    assert "wind" in cues["causes"]
    assert "from_behind" in cues["causes"]
    assert cues["needs_dense"]
    assert any("nape" in h.lower() for h in cues["hints"])


def test_scene_prose_includes_consequence_without_mutating_ledger():
    led = {
        **ledger_mod.blank(),
        "beat": "standing, hair streaming in the wind",
        "frame": "from behind, looking back",
        "wearing": "school blouse",
        "scene": "school rooftop",
        "atmosphere": "windy evening",
    }
    before = dict(led)
    prose = assemble.scene_prose(led, name_a="Aoi")
    assert "nape" in prose.lower() or "neck" in prose.lower()
    assert "wind" in prose.lower() or "hair" in prose.lower()
    assert led == before  # craft-only


def test_assemble_prompt_puts_consequence_tags_on_lead_beat():
    session = {
        "inputs": {"framing": "auto", "enhance_quality": False, "style": ""},
        "character": {
            "character_id": "c1",
            "name": "Aoi",
            "identity_tags": ["1girl", "black_hair", "bob_cut"],
        },
        "partner_character": {},
        "banned": [],
    }
    led = {
        **ledger_mod.blank(),
        "wearing": "white shirt",
        "beat": "standing, hair blown by the wind",
        "frame": "from behind",
        "scene": "rooftop",
    }
    prompt = assemble.assemble_prompt(session, led)
    low = prompt.lower().replace("_", " ")
    assert "floating hair" in low or "floating_hair" in prompt.lower()
    assert "nape" in low
    # ledger unchanged
    assert "nape" not in (led.get("beat") or "").lower()


def test_looking_back_from_behind_adds_looking_back_tag():
    led = {
        **ledger_mod.blank(),
        "beat": "standing, looking back over her shoulder",
        "frame": "from behind",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "looking_back" in cues["tags"]
    assert "nape" in cues["tags"]


def test_arms_up_implies_fabric_stretch():
    led = {
        **ledger_mod.blank(),
        "beat": "arms raised overhead, stretching",
        "wearing": "tank top",
        "scene": "gym",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "arms_up" in cues["tags"]
    assert any("hem" in h.lower() or "under" in h.lower() for h in cues["hints"])


def test_backlight_implies_rim_light():
    led = {
        **ledger_mod.blank(),
        "beat": "standing still",
        "light": "strong backlight from the window",
        "scene": "classroom",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "backlighting" in cues["tags"]
    assert "rim_light" in cues["tags"]
    assert any("rim" in h.lower() or "outline" in h.lower() for h in cues["hints"])


def test_sitting_holding_implies_folds_and_grip():
    led = {
        **ledger_mod.blank(),
        "beat": "sitting on a bench, holding a paper cup",
        "wearing": "hoodie",
        "scene": "park",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "sitting" in cues["tags"]
    assert "holding" in cues["tags"]
    assert any("knee" in h.lower() or "fold" in h.lower() for h in cues["hints"])
    assert any("knuckle" in h.lower() or "finger" in h.lower() for h in cues["hints"])


def test_tears_looking_down_implies_wet_tracks():
    led = {
        **ledger_mod.blank(),
        "beat": "looking down",
        "expression": "tears welling, crying quietly",
        "scene": "hallway",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "looking_down" in cues["tags"]
    assert "tearing_up" in cues["tags"]
    assert any("wet" in h.lower() or "gloss" in h.lower() for h in cues["hints"])


def test_wet_rain_implies_clinging_fabric_not_hair_only():
    led = {
        **ledger_mod.blank(),
        "beat": "standing under the awning",
        "atmosphere": "caught in the rain, soaked",
        "wearing": "white blouse",
        "scene": "street at night",
    }
    cues = assemble.visible_consequence_cues(led)
    assert "wet_skin" in cues["tags"] or "wet_hair" in cues["tags"]
    assert any("cling" in h.lower() or "dampen" in h.lower() or "darken" in h.lower()
               for h in cues["hints"])

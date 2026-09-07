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

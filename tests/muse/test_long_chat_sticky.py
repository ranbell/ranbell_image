"""Long-chat durability: sticky atmosphere/look and accidental-clear resistance."""
from __future__ import annotations

from app.muse import assemble, ledger, talk


def _base_led(**extra: str) -> dict[str, str]:
    led = ledger.blank()
    led = ledger.apply_patch(led, {
        "wearing": "navy school uniform",
        "beat": "standing, looking at viewer",
        "expression": "gentle smile",
        "scene": "cherry blossom park",
        "light": "golden hour",
        "bg": "falling petals",
        "frame": "cowboy shot",
        "atmosphere": "wistful, melancholic, tender ache",
        "look": "anime screenshot, cel shading, clean lineart",
        **extra,
    })
    return led


def test_scrub_blocks_accidental_empty_clears():
    led = _base_led()
    scrubbed = ledger.scrub_patch(
        {
            "wearing": "red dress",
            "atmosphere": "",
            "look": "",
            "scene": "",
        },
        led,
        allow_clear=set(),
    )
    assert scrubbed["wearing"] == "red dress"
    assert "atmosphere" not in scrubbed
    assert "look" not in scrubbed
    assert "scene" not in scrubbed
    after = ledger.apply_patch(led, scrubbed)
    assert "wistful" in after["atmosphere"]
    assert "cel" in after["look"]
    assert after["scene"] == "cherry blossom park"


def test_scrub_allows_explicit_reset():
    led = _base_led()
    scrubbed = ledger.scrub_patch(
        {"atmosphere": "", "look": "watercolor, soft edges"},
        led,
        allow_clear={"atmosphere"},
    )
    after = ledger.apply_patch(led, scrubbed)
    assert after["atmosphere"] == ""
    assert "watercolor" in after["look"]


def test_guard_sticky_blocks_muse_invention():
    propose = {
        "wearing": "hoodie",
        "atmosphere": "cozy, warm",  # muse inventing mood change
        "look": "oil painting",
    }
    guarded = ledger.guard_sticky_writes(propose, allowed=set())
    assert guarded == {"wearing": "hoodie"}


def test_guard_sticky_allows_director_touched():
    propose = {"atmosphere": "cozy, warm", "wearing": "hoodie"}
    guarded = ledger.guard_sticky_writes(propose, allowed={"atmosphere"})
    assert guarded["atmosphere"] == "cozy, warm"
    assert guarded["wearing"] == "hoodie"


def test_multi_turn_atmosphere_survives_clothes_and_banter():
    """Simulate: set emo → change clothes → banter → look-only → still emo."""
    led = ledger.blank()
    # Turn 1: open shot
    led = ledger.apply_patch(led, {
        "wearing": "navy school uniform",
        "beat": "standing",
        "expression": "smile",
        "scene": "park",
        "light": "sunset",
    })
    # Turn 2: emo (director only)
    cue = talk.cue_atmosphere_look("もうちょっとエモく、切ない感じで")
    patch = ledger.scrub_patch(
        talk.merge_cue_into_patch({}, cue), led,
        allow_clear=talk.cue_allow_clear(cue),
    )
    led = ledger.apply_patch(led, patch)
    # Muse tries to overwrite sticky on the same turn — blocked
    muse = ledger.guard_sticky_writes(
        {"atmosphere": "HALLUCINATED cozy", "expression": "grin"},
        allowed=set(),
    )
    led = ledger.apply_patch(led, muse)
    emo = led["atmosphere"]
    assert "wistful" in emo or "melanchol" in emo
    assert "HALLUCINATED" not in emo
    assert led["expression"] == "grin"

    # Turn 3: clothes only (writer would send wearing; maybe hallucinate empty mood)
    clothes = ledger.scrub_patch(
        {"wearing": "red dress", "atmosphere": "", "look": ""},
        led,
        allow_clear=set(),
    )
    led = ledger.apply_patch(led, clothes)
    led = ledger.apply_patch(
        led,
        ledger.guard_sticky_writes(
            {"atmosphere": "cozy, warm"}, allowed=set(),
        ),
    )
    assert led["wearing"] == "red dress"
    assert led["atmosphere"] == emo

    # Turn 4: banter must not cue-fire
    assert talk.cue_atmosphere_look("今日もありがとう、好きだよ") == {}

    # Turn 5: look-only cel — mood stays
    cue2 = talk.cue_atmosphere_look("画風はカチッとしたセル画でお願い")
    patch2 = ledger.scrub_patch(
        talk.merge_cue_into_patch({}, cue2), led,
        allow_clear=talk.cue_allow_clear(cue2),
    )
    led = ledger.apply_patch(led, patch2)
    # Muse again blocked
    led = ledger.apply_patch(
        led,
        ledger.guard_sticky_writes({"look": "HALLUCINATED oil"}, allowed=set()),
    )
    assert led["atmosphere"] == emo
    assert "cel" in led["look"] or "lineart" in led["look"]
    assert "HALLUCINATED" not in led["look"]

    # Prompt still carries sticky mood
    session = {
        "character": {
            "identity_tags": ["1girl", "silver_hair"],
            "character_id": "mio",
            "name": "Mio",
        },
        "inputs": {"framing": "auto", "style": ""},
    }
    prompt = assemble.assemble_prompt(session, led)
    assert "wistful" in prompt.lower() or "melanchol" in prompt.lower()
    assert "red dress" in prompt.lower() or "red_dress" in prompt.lower()


def test_guard_muse_propose_fill_empty_only():
    led = _base_led()
    # Settled wearing must not be overwritten by muse.
    guarded = ledger.guard_muse_propose(
        {"wearing": "muse invents coat", "beat_b": "standing aside"},
        led,
        director_keys=set(),
    )
    assert "wearing" not in guarded
    assert guarded.get("beat_b") == "standing aside"  # was empty


def test_guard_muse_propose_never_overwrites_director_same_turn():
    """Regression: director_keys must NOT let muse replace red dress with armor."""
    led = _base_led()
    led = ledger.apply_patch(led, {"wearing": "red dress"})
    guarded = ledger.guard_muse_propose(
        {"wearing": "black armor", "scene": "mars"},
        led,
        director_keys={"wearing", "scene"},
    )
    assert guarded == {}


def test_guard_muse_propose_expression_fill_empty():
    led = _base_led()
    led = ledger.apply_patch(led, {"expression": ""})
    guarded = ledger.guard_muse_propose(
        {"expression": "wistful soft eyes"},
        led,
        director_keys={"scene", "atmosphere"},
    )
    assert guarded.get("expression") == "wistful soft eyes"


def test_guard_muse_propose_expression_refreshes_when_scene_moves():
    """Actress owns face when mood/place moved and director did not name face."""
    led = _base_led()  # expression = gentle smile
    guarded = ledger.guard_muse_propose(
        {"expression": "wistful downturned eyes"},
        led,
        director_keys={"atmosphere", "scene"},
    )
    assert guarded.get("expression") == "wistful downturned eyes"


def test_guard_muse_propose_expression_keeps_director_named_face():
    led = _base_led()
    led = ledger.apply_patch(led, {"expression": "angry glare"})
    guarded = ledger.guard_muse_propose(
        {"expression": "cute smile"},
        led,
        director_keys={"expression", "beat"},
    )
    assert "expression" not in guarded


def test_guard_muse_propose_expression_no_refresh_on_banter():
    """Settled face stays when director did not move scene-ish axes."""
    led = _base_led()
    guarded = ledger.guard_muse_propose(
        {"expression": "teasing grin"},
        led,
        director_keys=set(),
    )
    assert "expression" not in guarded


def test_look_reset_keeps_atmosphere():
    led = _base_led()
    cue = talk.cue_atmosphere_look("画風リセット")
    patch = ledger.scrub_patch(
        talk.merge_cue_into_patch({}, cue), led,
        allow_clear=talk.cue_allow_clear(cue),
    )
    after = ledger.apply_patch(led, patch)
    assert after["look"] == ""
    assert "wistful" in after["atmosphere"]

"""Long-chat durability: sticky atmosphere/look and accidental-clear resistance."""
from __future__ import annotations

from app.muse_refine import assemble, ledger, talk


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

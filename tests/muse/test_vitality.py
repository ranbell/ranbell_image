"""Conversation vitality helpers — fun without sampling knobs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, vitality


def test_silence_whisper_is_sensory_template():
    assert "つば" in vitality.silence_whisper("帽子外して煽って")
    assert "mm" in vitality.silence_whisper("hat off", locale="en").lower() or "…" in vitality.silence_whisper("hat off", locale="en")


def test_notebook_flash_key():
    assert vitality.notebook_flash_key("麦わら帽子かぶって") == "wearing"
    assert vitality.notebook_flash_key("煽って") == "frame"


def test_taste_chips_short():
    chips = vitality.taste_chips({"prefers": "ローアングル、近い距離", "avoids": "足を映す"})
    assert any("ローアングル" in c for c in chips)
    assert any("足" in c for c in chips)


def test_open_ignore_fades_after_two_turns():
    s: dict = {}
    assert vitality.tick_open_ignore(s, "かき氷どう？", open_text="靴脱ぎ") is False
    assert vitality.tick_open_ignore(s, "いちごがいいかな", open_text="靴脱ぎ") is True


def test_open_ignore_resets_on_affirm():
    s: dict = {}
    vitality.tick_open_ignore(s, "雑談1", open_text="靴脱ぎ")
    assert vitality.tick_open_ignore(s, "いいね", open_text="靴脱ぎ") is False
    assert s["open_ignore"]["count"] == 0


def test_prop_age_hints_on_repeat():
    s: dict = {}
    nb = notebook.blank()
    notebook.apply_patch(nb, {"scene": "rooftop", "wearing": "sailor", "beat": "lean"})
    assert vitality.prop_fingerprint(nb)
    assert vitality.tick_prop_age(s, nb) == ""  # first sighting
    hint = ""
    for _ in range(5):
        hint = vitality.tick_prop_age(s, nb)
        if hint:
            break
    assert "時間" in hint or "小物" in hint


def test_b_leads_every_third_talk():
    s = {"talk_turn_count": 3}
    assert vitality.should_b_lead(s, partner=True) is True
    assert vitality.should_b_lead(s, partner=False) is False


def test_shot_compile_cleanup_every_15():
    s: dict = {}
    for i in range(14):
        assert vitality.bump_shot_compile(s) is False
    assert vitality.bump_shot_compile(s) is True


def test_reunion_and_again_hints():
    s = {
        "reunion_turn": True,
        "bond": {"last": "堤防 / セーラー", "inside": "うち解けてきた"},
        "memories": ["堤防で夕焼け"],
    }
    block = vitality.reunion_block(s)
    assert "再会" in block
    assert "堤防" in block
    assert "堤防" in vitality.again_that_feel_hint(s)

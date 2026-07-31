"""The merge dial must change the shot without losing the character.

The failure this guards against is the one the previous pipeline died of: the
character's hair and eye colour quietly falling out of the prompt, so panel 2
is a different person from panel 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.merge import merge_tracks

BACKGROUND = [
    {"tag": "rooftop", "score": 0.95},
    {"tag": "cloudy_sky", "score": 0.90},
    {"tag": "puddle", "score": 0.82},
    {"tag": "chain-link_fence", "score": 0.75},
    {"tag": "city", "score": 0.70},
    {"tag": "wet_ground", "score": 0.61},
    {"tag": "evening", "score": 0.55},
    {"tag": "power_lines", "score": 0.40},
]
PERSON = [
    {"tag": "1girl", "score": 0.99},
    {"tag": "purple_hair", "score": 0.96},
    {"tag": "green_eyes", "score": 0.93},
    {"tag": "school_uniform", "score": 0.88},
    {"tag": "long_hair", "score": 0.80},
    {"tag": "standing", "score": 0.66},
    {"tag": "hair_ribbon", "score": 0.42},
]
FOLDED = {"background": BACKGROUND, "person": PERSON}
IDENTITY = ["1girl", "purple_hair", "green_eyes"]


def test_merge_returns_tags_from_both_tracks():
    out = merge_tracks(FOLDED, character_weight=0.5)
    assert "rooftop" in out["tags"]
    assert "school_uniform" in out["tags"]


def test_weight_dial_shifts_the_balance():
    wide = merge_tracks(FOLDED, character_weight=0.1)["tags"]
    close = merge_tracks(FOLDED, character_weight=0.9)["tags"]
    bg = {t["tag"] for t in BACKGROUND}
    person = {t["tag"] for t in PERSON}
    wide_ratio = len(bg & set(wide)) / max(len(person & set(wide)), 1)
    close_ratio = len(bg & set(close)) / max(len(person & set(close)), 1)
    assert wide_ratio > close_ratio, "background weight must yield more scene tags"


def test_identity_survives_an_extreme_background_weight():
    out = merge_tracks(FOLDED, character_weight=0.02, protected_tags=IDENTITY)
    for tag in IDENTITY:
        assert tag in out["tags"], f"{tag} was dropped by the budget"
    assert out["protected"] == IDENTITY


def test_protected_tags_lead_the_prompt():
    out = merge_tracks(FOLDED, character_weight=0.5, protected_tags=IDENTITY)
    head = out["tags"][: len(IDENTITY)]
    assert head == IDENTITY


def test_removal_list_is_applied_but_never_to_a_protected_tag():
    out = merge_tracks(
        FOLDED, character_weight=0.5,
        protected_tags=IDENTITY,
        removal={"power_lines", "purple_hair"},
    )
    assert "power_lines" not in out["tags"]
    assert "power_lines" in out["removed"]
    assert "purple_hair" in out["tags"], "an Admin exclusion must not erase the character"
    assert "purple_hair" not in out["removed"]


def test_no_duplicates_after_protection():
    out = merge_tracks(FOLDED, character_weight=0.7, protected_tags=IDENTITY)
    lowered = [t.lower() for t in out["tags"]]
    assert len(lowered) == len(set(lowered))


def test_weights_are_reported():
    out = merge_tracks(FOLDED, character_weight=0.75)
    assert out["weights"]["person"] > out["weights"]["background"]
    assert abs(out["weights"]["person"] + out["weights"]["background"] - 1.0) < 1e-6

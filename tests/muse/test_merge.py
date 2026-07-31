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


def test_protected_tags_evict_what_contradicts_them():
    """Leading with the locked eye colour does nothing while a rival one is
    still listed — the model sees both and picks one. This is the defect that
    produced a prompt containing brown_eyes, blue_eyes, 1girl and
    multiple_girls all at once."""
    folded = {
        "background": BACKGROUND,
        "person": PERSON + [{"tag": "blue_eyes", "score": 0.91},
                            {"tag": "blue_hair", "score": 0.87}],
    }
    out = merge_tracks(folded, character_weight=0.5, protected_tags=IDENTITY)
    assert "green_eyes" in out["tags"], "the protected eye colour itself stays"
    assert "purple_hair" in out["tags"]
    assert "blue_eyes" not in out["tags"]
    assert "blue_hair" not in out["tags"]
    assert set(out["evicted"]) == {"blue_eyes", "blue_hair"}


def test_eviction_does_not_touch_unrelated_tags():
    folded = {"background": BACKGROUND, "person": PERSON}
    out = merge_tracks(folded, character_weight=0.5, protected_tags=IDENTITY)
    for tag in ("rooftop", "school_uniform", "puddle"):
        assert tag not in out["evicted"]


def test_no_protection_means_no_eviction():
    out = merge_tracks({"background": BACKGROUND, "person": PERSON}, character_weight=0.5)
    assert out["evicted"] == []


def test_junk_never_survives_the_merge():
    folded = {
        "background": BACKGROUND + [{"tag": "no_humans", "score": 0.8},
                                    {"tag": "black_border", "score": 0.7}],
        "person": PERSON,
    }
    out = merge_tracks(folded, character_weight=0.5, protected_tags=IDENTITY)
    assert "no_humans" not in out["tags"]
    assert "black_border" not in out["tags"]


def test_forced_tags_rank_with_the_character_and_survive_everything():
    """`solo` was in a prompt and lost anyway to a poolside scene a checkpoint
    knows is full of people. The user needs a slot that cannot be outvoted."""
    out = merge_tracks(
        FOLDED, character_weight=0.5,
        protected_tags=IDENTITY, must_tags=["solo"],
        removal={"solo", "rooftop"},
    )
    assert out["tags"][0] == "solo", "forced tags lead"
    assert out["forced"] == ["solo"]
    assert "solo" not in out["removed"], "an Admin exclusion must not erase it"
    assert "rooftop" in out["removed"], "ordinary tags still obey the list"


def test_forced_tags_evict_their_contradictions_too():
    folded = {**FOLDED, "person": PERSON + [{"tag": "2girls", "score": 0.8}]}
    out = merge_tracks(folded, character_weight=0.5, must_tags=["solo"])
    assert "2girls" not in out["tags"]
    assert "2girls" in out["evicted"]


def test_a_chosen_shot_removes_the_framings_that_fight_it():
    folded = {
        "background": BACKGROUND + [{"tag": "close-up", "score": 0.6}],
        "person": PERSON + [{"tag": "full_body", "score": 0.7}],
    }
    out = merge_tracks(folded, character_weight=0.5, shot="wide_shot")
    assert "close-up" not in out["tags"]
    assert "close-up" in out["framing_dropped"]
    assert "wide_shot" in out["tags"]
    assert out["shot"] == "wide_shot"


def test_auto_leaves_the_framing_to_the_drafts():
    folded = {**FOLDED, "person": PERSON + [{"tag": "close-up", "score": 0.6}]}
    out = merge_tracks(folded, character_weight=0.5, shot="auto")
    assert out["framing_dropped"] == []


def test_a_girl_never_keeps_menswear():
    """`male_swimwear` reached a board for a 1girl character and rendered
    trunks over a bikini top. Gender contradiction needs no shared head noun."""
    folded = {**FOLDED, "person": PERSON + [{"tag": "male_swimwear", "score": 0.9}]}
    out = merge_tracks(folded, character_weight=0.5, protected_tags=IDENTITY)
    assert "male_swimwear" not in out["tags"]
    assert "male_swimwear" in out["evicted"]


def test_reinforcements_are_appended_and_reported():
    out = merge_tracks(FOLDED, character_weight=0.5, reinforcements=["puddle_reflection"])
    assert "puddle_reflection" in out["tags"]
    assert out["reinforcements"] == ["puddle_reflection"]

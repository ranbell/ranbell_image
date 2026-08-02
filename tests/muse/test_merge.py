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


def test_reinforcements_are_placed_and_reported():
    out = merge_tracks(FOLDED, character_weight=0.5, reinforcements=["neon_sign"])
    assert "neon_sign" in out["tags"]
    assert out["reinforcements"] == ["neon_sign"]


def test_the_character_is_never_named_twice():
    """`1girl` and `pink_hair` are claimed by no routable slot, because
    Character is locked and excluded from routing. Without a guard they fell
    into Object and the prompt described her all over again."""
    out = merge_tracks(FOLDED, character_weight=0.5, protected_tags=IDENTITY)
    for tag in IDENTITY:
        assert tag in (out["slots"].get("character") or [])
        assert tag not in (out["slots"].get("object") or [])
    assert out["tags"].count("1girl") == 1


def test_description_survives_the_merge():
    """Nothing harvests a sentence back off a canvas, so it has to be carried
    across or it vanishes from the prompt where it matters most."""
    out = merge_tracks(
        FOLDED, character_weight=0.5,
        composed_slots={"description": ["A girl stands on a rooftop."],
                        "outfit": ["cardigan"]},
    )
    assert out["slots"]["description"] == ["A girl stands on a rooftop."]
    assert "Description: A girl stands on a rooftop." in out["positive"]


def test_what_the_drafts_showed_beats_what_was_composed():
    """The image is the source of truth; the composed slot is only a fallback
    for aspects the canvas cannot report."""
    out = merge_tracks(
        FOLDED, character_weight=0.5,
        composed_slots={"outfit": ["swimsuit"]},
    )
    assert "school_uniform" in out["slots"]["outfit"]
    assert "swimsuit" not in out["slots"]["outfit"]


def test_the_themes_verb_survives_what_the_drafts_failed_to_draw():
    """A bakery theme composed `kneading_dough`; the cheap drafts drew her
    sitting and eating, harvest overwrote Action wholesale, and the finished
    prompt had the character eating bread instead of baking it. A 512px sketch
    is not evidence about a verb."""
    folded = {
        "background": BACKGROUND,
        "person": PERSON + [{"tag": "sitting", "score": 0.9},
                            {"tag": "eating", "score": 0.85}],
    }
    out = merge_tracks(
        folded, character_weight=0.5,
        composed_slots={"action": ["kneading_dough", "brushing_flour"]},
    )
    assert "kneading_dough" in out["slots"]["action"]
    assert out["slots"]["action"][0] == "kneading_dough", "the verb leads"


def test_the_drafts_keep_half_of_an_intent_slot():
    """The overwrite was the failure, not the observation."""
    folded = {
        "background": BACKGROUND,
        "person": PERSON + [{"tag": "sitting", "score": 0.9}],
    }
    out = merge_tracks(
        folded, character_weight=0.5,
        composed_slots={"action": ["kneading_dough", "brushing_flour",
                                   "standing", "kneeling"]},
    )
    action = out["slots"]["action"]
    assert "sitting" in action, "the canvas still gets its share"
    assert len([t for t in action if t in ("kneading_dough", "brushing_flour")]) == 2


def test_a_body_word_is_never_said_twice():
    """`toned` is in identity_tags, so protection led Character with it while
    the preset had already placed it in Body."""
    out = merge_tracks(
        FOLDED, character_weight=0.5,
        protected_tags=IDENTITY + ["toned"],
        composed_slots={"body": ["toned"]},
    )
    assert "toned" in out["slots"]["body"]
    assert "toned" not in out["slots"]["character"]
    assert out["tags"].count("toned") == 1


def test_a_composed_slot_faces_the_junk_filter_too():
    """The junk pass ran over the harvested tags only, so `white_background` —
    composed into Light, never rendered, never harvested — walked past it into
    the finished prompt and told the render to throw the kitchen away."""
    out = merge_tracks(
        FOLDED, character_weight=0.5,
        composed_slots={"light": ["warm_glow", "white_background"]},
    )
    assert out["slots"]["light"] == ["warm_glow"]


def test_a_framing_tag_lands_on_the_shot_line_not_among_the_furniture():
    """Shot is user-owned so `place_tag` never targets it, and the Object
    fallback took the tag instead — a top-up picked `pov` and the prompt
    announced that a point of view was in the room."""
    folded = {**FOLDED, "person": PERSON + [{"tag": "pov", "score": 0.8}]}
    out = merge_tracks(folded, character_weight=0.5, reinforcements=["pov"])
    assert "pov" not in (out["slots"].get("object") or [])
    assert "pov" in (out["slots"].get("shot") or [])


def test_a_shot_the_user_chose_still_owns_the_line():
    folded = {**FOLDED, "person": PERSON + [{"tag": "from_above", "score": 0.8}]}
    out = merge_tracks(folded, character_weight=0.5,
                       user_slots={"shot": ["wide_shot", "eye_level"]})
    assert out["slots"]["shot"] == ["wide_shot", "eye_level"]


# ── agreement ───────────────────────────────────────────────────────────────
def _worn(tag, agreement, score=0.8):
    return {"tag": tag, "score": score, "agreement": agreement}


def test_a_tag_only_one_draft_showed_does_not_spend_the_budget():
    """Outfit's cap of four used to hide the disagreement by only letting the
    top few through. Widened to eight, the three drafts' private opinions —
    a sweater, a jacket, a skirt none of the others saw — walk straight in."""
    folded = {
        "background": BACKGROUND,
        "person": PERSON + [_worn("blue_coat", 0.67), _worn("pantyhose", 0.67),
                            _worn("sweater", 0.33), _worn("jacket", 0.33)],
    }
    out = merge_tracks(folded, character_weight=0.5)
    outfit = out["slots"].get("outfit") or []
    assert "blue_coat" in outfit and "pantyhose" in outfit
    assert "sweater" not in outfit and "jacket" not in outfit
    assert set(out["outvoted"]) >= {"sweater", "jacket"}


def test_a_single_draft_cannot_disagree_with_itself():
    """With one board image per track everything scores 1.0 and the floor is a
    no-op — which is right, not a lucky accident."""
    folded = {"background": BACKGROUND,
              "person": PERSON + [_worn("sweater", 1.0)]}
    out = merge_tracks(folded, character_weight=0.5)
    assert "sweater" in (out["slots"].get("outfit") or [])
    assert out["outvoted"] == []


def test_the_character_is_never_outvoted():
    folded = {"background": BACKGROUND,
              "person": [{**r, "agreement": 0.33} for r in PERSON]}
    out = merge_tracks(folded, character_weight=0.5, protected_tags=IDENTITY,
                       must_tags=["solo"])
    for tag in IDENTITY + ["solo"]:
        assert tag in out["tags"]


def test_a_harvested_body_part_lands_in_body_rather_than_being_deleted():
    """`place_tag` returned None for every body part, so merge dropped them —
    a theme about wet legs reached the render with no legs in it."""
    folded = {
        "background": BACKGROUND,
        "person": PERSON + [_worn("wet_legs", 1.0), _worn("wet_clothes", 1.0),
                            _worn("medium_breasts", 1.0)],
    }
    out = merge_tracks(folded, character_weight=0.5)
    body = out["slots"].get("body") or []
    assert {"wet_legs", "wet_clothes", "medium_breasts"} <= set(body)
    assert not ({"wet_legs", "wet_clothes"} & set(out["slots"].get("object") or []))


def test_the_scene_being_wet_does_not_delete_her_wet_legs():
    """Refine's conflict rule is any shared word of three letters or more,
    which is right for six photographs of one subject and wrong for a place
    merged with a person. `wet_ground` on the pavement deleted `wet_legs` on
    the girl, in the one theme where the wet legs are the whole point."""
    folded = {
        "background": BACKGROUND,   # carries `wet_ground`
        "person": PERSON + [_worn("wet_legs", 1.0), _worn("wet_clothes", 1.0)],
    }
    out = merge_tracks(folded, character_weight=0.5)
    assert {"wet_legs", "wet_clothes"} <= set(out["slots"].get("body") or [])
    assert "wet_ground" in out["tags"], "and the pavement is still wet"


def test_a_tag_appears_on_exactly_one_line():
    """`bus_stop` is in no catalog, so the harvested copy landed in Object
    while the composed one led Place, and the prompt listed it twice."""
    folded = {"background": BACKGROUND + [{"tag": "bus_stop", "score": 0.9}],
              "person": PERSON}
    out = merge_tracks(folded, character_weight=0.5,
                       composed_slots={"place": ["bus_stop", "raining"]})
    assert "bus_stop" in out["slots"]["place"]
    assert "bus_stop" not in (out["slots"].get("object") or [])
    assert out["positive"].count("bus_stop") == 1


def test_the_theme_names_the_place_and_the_character_owns_the_mood():
    """Place filled with `outdoors, scenery` — the two vaguest tags in the
    picture — while the one word the theme gave went to Object. And the drafts'
    `blush` overwrote the mood the character block had just produced, undoing
    the whole point of reading her personality."""
    folded = {"background": BACKGROUND + [{"tag": "outdoors", "score": 0.95}],
              "person": PERSON + [{"tag": "blush", "score": 0.95}]}
    out = merge_tracks(folded, character_weight=0.5,
                       composed_slots={"place": ["bus_stop"], "emotion": ["patient"]})
    assert out["slots"]["place"][0] == "bus_stop"
    assert out["slots"]["emotion"][0] == "patient"
    assert "blush" in out["slots"]["emotion"], "the canvas keeps its share"

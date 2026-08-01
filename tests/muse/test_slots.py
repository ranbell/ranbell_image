"""Slot budgets, routing, and the labelled prompt they render to."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.slots import (
    BY_KEY,
    COMPOSED,
    SLOTS,
    USER,
    dedupe_slot,
    flatten,
    is_thing,
    place_tag,
    render_prompt,
    restates,
    slots_for,
)


# ── the budget ──────────────────────────────────────────────────────────────
def test_a_slot_is_trimmed_to_its_cap():
    assert dedupe_slot(["a_one", "b_two", "c_three", "d_four"], 2) == ["a_one", "b_two"]


def test_restatements_lose_their_place_to_a_second_fact():
    """`swimwear, black_bikini, bikini` is one fact spent three times."""
    kept = dedupe_slot(["bikini", "black_bikini", "swimwear", "sun_hat"], 3)
    assert "sun_hat" in kept
    assert len([t for t in kept if "bikini" in t]) == 1


def test_the_more_specific_of_two_restatements_wins():
    """Order comes from the harvest ranking and a generic tag always wins it —
    `shirt` is on far more images than `white_shirt`. A first-wins rule dropped
    every colour the drafts had shown, and clothing with no colour renders
    white."""
    assert dedupe_slot(["shirt", "white_shirt"], 4) == ["white_shirt"]
    assert dedupe_slot(["skirt", "navy_pleated_skirt"], 4) == ["navy_pleated_skirt"]


def test_a_specific_tag_already_in_place_is_not_replaced_by_a_vaguer_one():
    assert dedupe_slot(["white_shirt", "shirt"], 4) == ["white_shirt"]


def test_replacing_does_not_free_up_budget():
    """A restatement takes the place it restates, it does not buy another."""
    kept = dedupe_slot(["shirt", "white_shirt", "hat", "scarf", "boots"], 3)
    assert kept == ["white_shirt", "hat", "scarf"]


def test_overlapping_tags_that_are_not_refinements_both_survive():
    """`sleeves_past_wrists` is not `long_sleeves` said better, and sharing a
    word is not enough to call it one. Collapsing on any shared word is what
    destroyed the character — see below."""
    assert dedupe_slot(["long_sleeves", "sleeves_past_wrists"], 4) == [
        "long_sleeves", "sleeves_past_wrists",
    ]


def test_the_character_survives_her_own_slot():
    """Given `1girl, blue_hair, very_long_hair, straight_hair, blue_eyes, slim`
    the shared-word rule kept `1girl, blue_hair, slim`: hair length and hair
    style share "hair" with hair colour, and `blue_eyes` shares "blue". A girl
    may have very long straight blue hair and blue eyes. Identity drift is what
    the previous pipeline was abandoned over."""
    identity = ["1girl", "blue_hair", "very_long_hair", "straight_hair",
                "blue_eyes", "slim"]
    assert dedupe_slot(identity, BY_KEY["character"].cap) == identity


def test_one_feature_still_takes_one_value():
    """The point of collapsing at all: a girl has one hair colour."""
    assert dedupe_slot(["blue_hair", "black_hair"], 4) == ["blue_hair"]
    assert dedupe_slot(["blue_eyes", "green_eyes"], 4) == ["blue_eyes"]


def test_the_outfit_slot_asks_for_a_colour():
    assert "COLOUR" in BY_KEY["outfit"].guidance
    assert "renders white" in BY_KEY["outfit"].guidance


def test_short_tokens_do_not_collapse_unrelated_tags():
    """Two-letter overlaps are noise; the test only looks at real words."""
    kept = dedupe_slot(["a_cat", "a_dog"], 3)
    assert kept == ["a_cat", "a_dog"]


def test_exact_duplicates_collapse_regardless_of_case():
    assert dedupe_slot(["Bikini", "bikini"], 3) == ["Bikini"]


def test_restates_matches_across_a_list():
    assert restates("puddle_reflection", ["rooftop", "puddle"])
    assert not restates("neon_sign", ["rooftop", "puddle"])
    assert not restates("", ["puddle"])


# ── routing ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tag,slot", [
    ("smile", "emotion"),
    ("school_uniform", "outfit"),
    ("library", "place"),
    ("standing", "action"),
])
def test_tags_land_in_the_aspect_they_describe(tag, slot):
    assert place_tag(tag) == slot


def test_a_tag_nothing_claims_is_left_unplaced():
    assert place_tag("qwertyuiop_thing") is None


def test_routing_never_targets_a_locked_or_user_slot():
    for tag in ("1girl", "wide_shot", "kodak_color"):
        assert place_tag(tag) not in {"character", "body", "style", "shot", "effect", "theme"}


# ── the shape ───────────────────────────────────────────────────────────────
def test_the_prompt_is_labelled_line_by_line():
    text = render_prompt({
        "description": ["A girl swims in a pool."],
        "character": ["1girl", "brown_hair"], "outfit": ["swimsuit"],
        "place": ["pool"], "effect": ["kodak color"],
    })
    assert "Description: A girl swims in a pool." in text
    assert "Character: 1girl, brown_hair" in text
    assert "Outfit: swimsuit" in text
    assert "Effect: kodak color" in text


def test_empty_aspects_are_left_out_rather_than_left_blank():
    text = render_prompt({"outfit": [], "place": ["pool"]})
    assert "Outfit" not in text
    assert "Place: pool" in text


def test_the_prompt_carries_literal_text_and_closing_prose():
    text = render_prompt(
        {"place": ["runway"]},
        texts=[{"text": "34L", "where": "runway"}, {"text": "Bunny Air"}],
        prose="A pilot stands by her plane.",
    )
    assert 'text "34L" on runway' in text
    assert 'text "Bunny Air"' in text and "on " not in text.split('"Bunny Air"')[1][:4]
    assert text.rstrip().endswith("A pilot stands by her plane.")


def test_nothing_extra_is_added_when_there_is_nothing_extra():
    text = render_prompt({"place": ["runway"]})
    assert text == "Place: runway"


def test_the_prompt_keeps_the_declared_order():
    text = render_prompt({"effect": ["grain"], "character": ["1girl"], "place": ["pool"]})
    lines = [ln.split(":")[0] for ln in text.splitlines()]
    assert lines == ["Character", "Place", "Effect"]


def test_flatten_walks_the_slots_in_order_and_dedupes():
    flat = flatten({"character": ["1girl"], "outfit": ["swimsuit", "1girl"], "place": ["pool"]})
    assert flat == ["1girl", "swimsuit", "pool"]


# ── the table itself ────────────────────────────────────────────────────────
def test_the_hand_written_format_is_covered():
    labels = {s.label for s in SLOTS}
    assert {"Style", "Description", "Character", "Emotion", "Outfit", "Body",
            "Action", "Accessories", "Shot", "Place", "Object", "Effect"} <= labels


def test_description_leads_the_tags():
    """One sentence naming the subject, before any tag list, so the tags read
    as details of a thing rather than competing suggestions."""
    keys = [s.key for s in SLOTS]
    assert keys.index("description") < keys.index("character")
    assert keys.index("description") < keys.index("place")


def test_the_user_owns_the_aesthetic_and_the_framing():
    assert {s.key for s in USER} == {"style", "shot", "effect"}


def test_every_composed_slot_is_explained_to_the_model():
    for slot in COMPOSED:
        assert slot.guidance, f"{slot.key} has nothing to tell the model"


def test_every_tag_slot_can_be_searched_for():
    """Description is prose, so it has nothing to look up. Everything else does."""
    for slot in COMPOSED:
        if slot.key == "description":
            continue
        assert slot.query, f"{slot.key} has nothing to search the vocabulary for"


def test_every_slot_has_a_positive_budget():
    for slot in SLOTS:
        assert slot.cap > 0


def test_a_board_render_gets_its_own_track_plus_the_global_slots():
    keys = {s.key for s in slots_for("background")}
    assert "place" in keys and "object" in keys
    assert "outfit" not in keys
    assert "style" in keys and "shot" in keys


def test_character_and_body_are_separate_budgets():
    """Body words in the Character line crowd out hair and eye colour."""
    assert BY_KEY["character"].locked and BY_KEY["body"].locked
    assert BY_KEY["character"].cap >= 6


@pytest.mark.parametrize("tag", ["desk_lamp", "cooking_pot", "neon_sign", "bread_slice"])
def test_an_unrouted_noun_is_still_a_thing(tag):
    """The catalog knows no compound nouns, so Object has to keep taking them."""
    assert is_thing(tag)


@pytest.mark.parametrize("tag", ["glowing", "sweat", "shining", "thighs"])
def test_a_quality_is_not_a_thing(tag):
    """`Object: glowing, sweat, cooking_pot` asserted that a glow and a sweat
    were sitting on the counter."""
    assert not is_thing(tag)


@pytest.mark.parametrize("tag", ["medium_breasts", "bare_thighs", "closed_eyes"])
def test_a_modified_body_part_is_still_a_body_part(tag):
    """The catalog lists `thighs` and not `medium_breasts`, and Object took
    the latter happily. The head noun is what decides the category."""
    assert not is_thing(tag)


def test_a_compound_gerund_is_not_caught_by_the_bare_gerund_rule():
    assert is_thing("steaming_kettle") and is_thing("glowing_mushroom")


def test_the_description_does_not_go_to_a_background_board():
    """It names who is in the picture, so a background board that carries it
    renders her — one read "A girl with blue hair is looking through a
    telescope" in the positive while the negative said `1girl, solo, person`,
    and the sentence won."""
    keys = {s.key for s in slots_for("background")}
    assert "description" not in keys
    assert "description" in {s.key for s in slots_for("person")}

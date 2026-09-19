"""**One body holds only one answer.** (2026-09-12)

The Showrunner: "let's polish beat". Live (replaying a record, 18 seats) `beat`
grew to 13 words with the weight in two places, the hips in two directions and the
hands tripled:

    weight on right leg       ↔  weight on back foot
    hips jutting out sharply  ↔  hips pushed forward
    hands_clutching_tray_edge ↔  hugging tray ↔ hands gripping tray_edge …

The original session behind that same record had 8 words, **one per body part**.
The cause was eighteen seats writing into one field in turn, and a contract that
said "add alongside".

`tags.conflict.SLOTS` cannot be used — that is a table of **exact danbooru
words**, and what arrives here is free text. So it is read **by axis** (body part
plus direction). Only the second answer on an axis is dropped, whether it is the
opposite answer (a contradiction) or the same one (a restatement). **The first
answer wins**, the same rule as `facets._resolve_self_slot_conflicts`.

Not over-trimming is the real work — see the head of `tags/conflict.py`:
"over-trimming costs more". Run against 52 live `beat` values, six were touched,
and every one of them rightly.
"""
from __future__ import annotations

from app.muse import ledger as L


#: The values observed live, exactly as they were (2026-09-12, reproducing 「今日も
#: メイドさんで」 — "the maid outfit again today").
BLOATED = (
    "standing, weight on right leg, hips jutting out sharply, left hand on hip, "
    "arms_stiff, hands_clutching_tray_edge, weight on back foot, hips pushed "
    "forward, elbows flared outward, hands gripping tray_edge with knuckles "
    "white, forearms tensed, hugging tray, hand on hip"
)
#: The values from the original session, where a person shot the same script.
#: **Not one word here may be dropped.**
CLEAN = (
    "standing, weight_on_front_foot, one_hand_on_hip, "
    "other_arm_holding_tray_at_waist, chin_lifted, chest_pushed_forward, "
    "hips_thrust_forward, elbow_out"
)


def test_two_answers_for_the_weight_keep_the_first():
    kept, dropped = L.one_body(BLOATED)
    assert "weight on right leg" in kept
    assert "weight on back foot" in dropped


def test_the_same_thing_said_twice_is_said_once():
    kept, dropped = L.one_body(BLOATED)
    # The hips face one way. A paraphrase (jutting / pushed forward) is dropped as
    # the second answer too.
    assert "hips jutting out sharply" in kept
    assert "hips pushed forward" in dropped
    # There is one tray in her hands. Of three phrasings only the first survives.
    assert "hands_clutching_tray_edge" in kept
    assert "hugging tray" in dropped
    assert "hands gripping tray_edge with knuckles white" in dropped


def test_a_phrase_already_contained_in_another_is_dropped():
    kept, dropped = L.one_body(BLOATED)
    assert "left hand on hip" in kept
    assert "hand on hip" in dropped, "`left hand on hip` の中にある"


def test_the_directors_body_survives():
    """The director said: "hold the tray, hand on your hip, and look confident"."""
    kept, _ = L.one_body(BLOATED)
    assert len([t for t in kept.split(",") if t.strip()]) == 8
    for must in ("hands_clutching_tray_edge", "left hand on hip",
                 "hips jutting out sharply", "standing"):
        assert must in kept


def test_a_body_that_is_already_one_body_is_untouched():
    """**Do not over-trim.** Not one word of a value a person shot is touched."""
    kept, dropped = L.one_body(CLEAN)
    assert dropped == []
    assert kept == CLEAN


def test_two_hands_hold_two_different_things():
    """There are two hands. A tray and a cup are **different objects**, so both
    stand.

    A live value (`looking at viewer, posing, holding tray, holding coffee cup`).
    The first shape, which grouped by "holding" without looking at the object,
    threw the cup away here.
    """
    kept, dropped = L.one_body("looking at viewer, posing, holding tray, holding coffee cup")
    assert dropped == []
    assert "holding tray" in kept and "holding coffee cup" in kept


def test_the_same_thing_in_her_hands_is_one_thing():
    kept, dropped = L.one_body("hands gripping tray_edge, holding order_tray")
    assert "hands gripping tray_edge" in kept
    assert "holding order_tray" in dropped, "同じトレイ"


def test_letting_go_and_holding_the_same_thing_cannot_both_be_true():
    kept, dropped = L.one_body(
        "hands releasing tray_edge, hands_clutching_tray_edge, hands steadying tray_edge"
    )
    assert "hands releasing tray_edge" in kept
    assert len(dropped) == 2


def test_the_throw_is_not_eaten():
    """A bowling delivery. `releasing ball` is a restatement of
    `releasing bowling ball` and goes, but **the throw itself stays**."""
    kept, dropped = L.one_body(
        "releasing bowling ball, profile view, side view, releasing ball, low angle"
    )
    assert "releasing bowling ball" in kept
    assert dropped == ["releasing ball"]


def test_a_part_named_without_a_direction_is_never_dropped():
    """A phrase with no direction word is untouched — `arms_stiff` and
    `forearms tensed` are both about the arms, and each says something different
    about the body."""
    kept, dropped = L.one_body("arms_stiff, forearms tensed, elbows flared outward, shoulders hunched")
    assert dropped == []
    assert kept == "arms_stiff, forearms tensed, elbows flared outward, shoulders hunched"


def test_the_entrance_folds_the_body_and_says_what_it_dropped():
    """**There is one door: `normalize_patch`.** It bites on the seat road and the
    card road alike."""
    report: dict[str, list[str]] = {}
    out = L.normalize_patch({"beat": BLOATED, "scene": "cafe open terrace"}, report=report)
    assert out["beat"] == L.one_body(BLOATED)[0]
    assert out["scene"] == "cafe open terrace", "体以外の欄は畳まない"
    assert report["beat"] == L.one_body(BLOATED)[1], "落としたものを呼び元へ渡す"


def test_the_partner_has_a_body_too():
    report: dict[str, list[str]] = {}
    out = L.normalize_patch({"beat_b": BLOATED}, report=report)
    assert len([t for t in out["beat_b"].split(",") if t.strip()]) == 8
    assert report["beat_b"]


def test_only_the_body_fields_are_folded():
    """`bg` lists objects and has no notion of an axis. Even with a holding word
    mixed in, it is untouched."""
    out = L.normalize_patch({
        "bg": "hot_coffee, coffee_cup, steam, cheese_cake, crumbs",
        "light": "backlighting, rim_light",
    })
    assert out["bg"] == "hot_coffee, coffee_cup, steam, cheese_cake, crumbs"
    assert out["light"] == "backlighting, rim_light"


def test_the_contract_tells_the_writer_the_body_is_one():
    """The contract was fixed too — dropping alone pays for the same cleanup every
    turn."""
    from app.muse import crew_room, writer

    assert "ONE BODY, ONE INSTANT" in writer.WRITER_SYSTEM
    block = crew_room.craft_block([{"field": "beat", "craft": "weight on back foot"}])
    assert "FOLD IN ONLY WHAT IS NOT THERE YET" in block


def test_a_body_that_forgot_to_say_she_is_standing_gets_it_back():
    """**The field is rewritten whole, so anything not written disappears.**

    A/B on the bench (2026-09-12): adding the "one body" clause removed the
    contradictions and in the same runs `standing` vanished — the writer left it
    out as "something already known". No wording of the contract brought it back,
    so it is protected here.
    """
    before = {**L.blank(), "beat": "standing, weight on back foot, hips retracted"}
    out = L.scrub_patch({"beat": "weight on back foot, hips jutting out sharply, hugging tray"}, before)
    assert out["beat"].startswith("standing, ")


def test_a_body_that_names_its_own_posture_is_left_alone():
    """A turn told 「座って」 ("sit down") does not get stood up."""
    before = {**L.blank(), "beat": "standing, weight on back foot"}
    out = L.scrub_patch({"beat": "sitting on the floor, legs tucked to the side"}, before)
    assert out["beat"] == "sitting on the floor, legs tucked to the side"
    out = L.scrub_patch({"beat": "kneeling, hands on knees"}, {**L.blank(), "beat": "standing"})
    assert out["beat"] == "kneeling, hands on knees"


def test_nothing_is_invented_when_the_old_body_had_no_posture_either():
    before = {**L.blank(), "beat": "hands in her pockets"}
    out = L.scrub_patch({"beat": "looking over her shoulder"}, before)
    assert out["beat"] == "looking over her shoulder"


def test_the_posture_words_come_from_the_one_list_we_already_have():
    """No second word table — `tags.conflict`'s `posture` slot is used as it
    stands."""
    import inspect

    src = inspect.getsource(L._posture_of)
    assert "conflict.slot_of" in src and "posture" in src


def test_supporting_a_thing_is_holding_it():
    """A duplicate left live (2026-09-12, second replay).

        right arm holding tray, forearm_supporting_tray

    `supporting` was not among the holding words, so it never sat on an axis. The
    split of **left hand on the hip, right hand on the tray** must survive; only
    the doubled tray goes.
    """
    kept, dropped = L.one_body(
        "standing, weight on back leg, left hand on hip, right arm holding tray, "
        "elbows flared outward, forearm_supporting_tray"
    )
    assert dropped == ["forearm_supporting_tray"]
    assert "left hand on hip" in kept and "right arm holding tray" in kept
    assert kept.startswith("standing, ")

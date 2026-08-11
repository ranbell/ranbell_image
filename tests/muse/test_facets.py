"""The shot in parts, and the one rule that makes a camera move actually move.

Two failures were reported from real sessions and both are here as assertions.
A shot moved from a high angle to a low one kept `looking_up` and the picture
broke. A jacket the Showrunner took off came back for several turns.

Neither is fixed by removing tags better. They are fixed by there being a place
a tag lives: nothing removes `from_above`, the camera facet is overwritten and
`from_above` was only ever in it. The tests that matter most here are the ones
asserting a facet nobody wrote is *untouched* — that is the whole thesis, and it
is the thing a full-rewrite turn could never promise.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import brief as brief_mod
from app.muse import facets, identity, schema, service


def _session() -> dict:
    s = schema.new_session({
        "theme": "お題", "character_id": "c1", "workflow": "w.json", "model": "m",
    })
    s["character"] = {"identity_tags": ["1girl", "blue_hair"],
                      "outfit_tags": [], "personality": {}, "palette": [],
                      "signature_prop": ""}
    return s


# ── replacement, not removal ────────────────────────────────────────────────

def test_writing_a_facet_replaces_it_whole():
    """Nothing removed the old tags. The part they lived in was overwritten."""
    s = _session()
    facets.write(s, "camera", tags="from_above, high_angle, looking_up",
                 nl="Shot from high above her.", by="actress:x")
    facets.write(s, "camera", tags="from_below, low_angle, looking_down",
                 nl="Shot from below.", by="actress:x")

    tags = facets.table_of(s)["camera"]["tags"]
    assert "from_below" in tags and "looking_down" in tags
    assert "from_above" not in tags
    assert "looking_up" not in tags
    assert "high above" not in facets.table_of(s)["camera"]["nl"]


def test_a_camera_move_leaves_every_other_part_of_the_shot_alone():
    """The thesis. A full-rewrite turn could only ever promise this; a scoped
    write cannot do anything else."""
    s = _session()
    facets.write(s, "place", tags="classroom, window", nl="A classroom.")
    facets.write(s, "costume", tags="jacket, pleated_skirt", nl="A jacket.")
    facets.write(s, "camera", tags="from_above")
    before = {n: dict(facets.table_of(s)[n]) for n in ("place", "costume")}

    facets.write(s, "camera", tags="from_below")

    for name, was in before.items():
        now = facets.table_of(s)[name]
        assert now["rev"] == was["rev"], f"{name} was rewritten by a camera move"
        assert now["tags"] == was["tags"]
        assert now["nl"] == was["nl"]


def test_a_stale_gaze_in_another_facet_is_evicted_by_a_camera_move():
    """`looking_up` that leaked into the pose facet is exactly what survived a
    camera move before — the facet it was in was never the one rewritten."""
    s = _session()
    facets.write(s, "pose", tags="standing, looking_up, hand_on_own_hip")
    # The gaze tag does not belong to pose at all, so it never lands there.
    assert "looking_up" not in facets.table_of(s)["pose"]["tags"]
    assert "standing" in facets.table_of(s)["pose"]["tags"]

    # But a session migrated from the old flat bag can carry one anyway.
    facets.table_of(s)["pose"]["tags"].append("looking_up")
    facets.write(s, "camera", tags="from_below, low_angle")
    assert "looking_up" not in facets.table_of(s)["pose"]["tags"]
    assert "standing" in facets.table_of(s)["pose"]["tags"]


def test_an_angle_keeps_the_gaze_that_belongs_to_it():
    """The check that catches an over-eager eviction."""
    s = _session()
    facets.table_of(s)["pose"]["tags"].append("looking_up")
    facets.write(s, "camera", tags="from_above, high_angle")
    assert "looking_up" in facets.table_of(s)["pose"]["tags"]


# ── who owns which tag ──────────────────────────────────────────────────────

def test_a_facet_cannot_write_a_tag_another_facet_owns():
    s = _session()
    report = facets.write(s, "expression", tags="smile, looking_at_viewer, blush")
    kept = facets.table_of(s)["expression"]["tags"]
    assert "smile" in kept and "blush" in kept
    assert "looking_at_viewer" not in kept, "the lens decides where she looks"
    assert "looking_at_viewer" in report["rejected"]


def test_a_back_view_and_eye_contact_need_a_turned_head():
    """`conflict.contradicts` is pairwise and can never see the third tag, so
    this lives where the whole camera facet is visible at once."""
    s = _session()
    facets.write(s, "camera", tags="from_behind, looking_at_viewer")
    assert "looking_at_viewer" not in facets.table_of(s)["camera"]["tags"]

    facets.write(s, "camera", tags="from_behind, looking_back, looking_at_viewer")
    kept = facets.table_of(s)["camera"]["tags"]
    assert "looking_at_viewer" in kept and "looking_back" in kept


def test_a_refused_tag_never_enters_a_facet():
    s = _session()
    s["banned"] = ["jacket"]
    facets.write(s, "costume", tags="jacket, pleated_skirt")
    assert facets.table_of(s)["costume"]["tags"] == ["pleated_skirt"]


def test_a_tag_that_fights_the_locked_body_never_enters_a_facet():
    s = _session()
    s["character"]["identity_tags"] = ["1girl", "small_breasts"]
    facets.write(s, "pose", tags="standing, huge_breasts")
    assert "huge_breasts" not in facets.table_of(s)["pose"]["tags"]


# ── locking ─────────────────────────────────────────────────────────────────

def test_a_refusal_reaches_the_state_and_not_just_the_view_of_it():
    """`craft` is derived here, so striking it would last exactly until the next
    reassemble put the tag back from the table."""
    s = _session()
    s["mode"] = "duet"
    facets.write(s, "props", tags="desk, glasses", nl="A desk, and her glasses.")
    facets.write(s, "place", tags="classroom", nl="A classroom.")
    service.apply_removals(s, ["glasses"], [])
    service._reassemble(s)

    assert "glasses" not in facets.table_of(s)["props"]["tags"]
    assert "glasses" not in s["craft"]["tags"]
    assert "glasses" not in s["craft"]["prompt"]
    # The sentence named it too, and a sentence is half the prompt.
    assert "glasses" not in s["craft"]["scene"]
    # Parts that lost nothing keep their prose.
    assert facets.table_of(s)["place"]["nl"] == "A classroom."


def test_a_refusal_queues_the_part_it_emptied_for_rewrite():
    s = _session()
    s["mode"] = "duet"
    facets.write(s, "costume", tags="jacket, skirt", nl="A jacket over a skirt.",
                 fields={"hero": "the jacket"})
    service.apply_removals(s, ["jacket"], [])

    assert facets.table_of(s)["costume"]["nl"] == ""
    assert facets.table_of(s)["costume"]["fields"] == {}
    assert "costume" in s["routed"]


def test_a_refusal_outranks_a_lock():
    """A pin says "do not rewrite this". It does not say "keep something the
    Showrunner has taken out of the picture"."""
    s = _session()
    s["mode"] = "duet"
    facets.write(s, "props", tags="desk, glasses")
    facets.set_lock(s, "props", True)
    service.apply_removals(s, ["glasses"], [])
    assert "glasses" not in facets.table_of(s)["props"]["tags"]


def test_a_locked_facet_is_never_written():
    s = _session()
    facets.write(s, "costume", tags="jacket", nl="A jacket.")
    facets.set_lock(s, "costume", True)
    report = facets.write(s, "costume", tags="swimsuit", nl="Something else.")

    assert report["written"] is False
    assert report["blocked"] == ["costume"]
    assert facets.table_of(s)["costume"]["tags"] == ["jacket"]
    assert facets.table_of(s)["costume"]["nl"] == "A jacket."


def test_a_locked_facet_is_not_evicted_and_the_disagreement_is_recorded():
    """The Showrunner pinned it. A conflicting write does not get to quietly
    win — the panel has to be able to say the two disagree."""
    s = _session()
    facets.write(s, "pose", tags="standing")
    facets.set_lock(s, "pose", True)
    report = facets.write(s, "camera", tags="from_below")
    facets.table_of(s)["pose"]["tags"].append("looking_up")
    report = facets.write(s, "camera", tags="from_below, low_angle")

    assert "looking_up" in facets.table_of(s)["pose"]["tags"]
    assert "pose" in report["blocked"]


# ── assembly ────────────────────────────────────────────────────────────────

def test_all_tags_follows_the_prompt_order_not_the_panel_order():
    s = _session()
    facets.write(s, "place", tags="classroom")
    facets.write(s, "camera", tags="from_below")
    facets.write(s, "pose", tags="standing")
    assert facets.all_tags(facets.table_of(s)) == "from_below, standing, classroom"


def test_emphasis_cannot_ride_in_beside_the_bare_tag():
    s = _session()
    facets.write(s, "costume", tags="pleated_skirt")
    facets.write(s, "props", tags="(pleated_skirt:1.2), desk")
    assert facets.all_tags(facets.table_of(s)) == "pleated_skirt, desk"


def test_emphasis_is_clamped_on_the_way_in():
    s = _session()
    facets.write(s, "pose", tags="(standing:1.9)")
    assert facets.table_of(s)["pose"]["tags"] == [
        f"(standing:{identity.MAX_TAG_WEIGHT:g})",
    ]


def test_nl_join_is_a_usable_scene_with_no_model_call():
    """A panel that waits on a model to show the Showrunner what he just asked
    for looks broken every time he types."""
    s = _session()
    for name, _ in facets.FACETS:
        facets.write(s, name, nl=(
            "Twenty five words of perfectly ordinary prose describing exactly "
            "one part of the picture and nothing else at all here now"
        ))
    scene = facets.nl_join(facets.table_of(s))
    assert not identity.craft_is_thin("", scene)
    assert scene.count(".") == len(facets.FACETS)


def test_table_rev_moves_only_when_the_shot_does():
    s = _session()
    facets.write(s, "camera", tags="from_below")
    rev = facets.table_rev(facets.table_of(s))
    facets.set_lock(s, "pose", True)
    assert facets.table_rev(facets.table_of(s)) == rev
    facets.write(s, "camera", tags="from_above")
    assert facets.table_rev(facets.table_of(s)) == rev + 1


# ── projections onto what the rest of Muse already reads ────────────────────

def test_the_table_projects_onto_the_plan_block():
    s = _session()
    facets.write(s, "place", nl="A classroom, by the window.")
    facets.write(s, "hour", nl="Late afternoon, autumn.")
    facets.write(s, "light", nl="Low sun through the glass.")
    facets.write(s, "pose", nl="She is leaning on the sill.")
    facets.write(s, "props", tags="desk, chalkboard, curtain")

    plan = facets.to_plan(facets.table_of(s))
    block = brief_mod.plan_block(plan)
    assert "PLACE: A classroom, by the window." in block
    assert "ACTION: She is leaning on the sill." in block
    assert "MUST APPEAR: desk, chalkboard, curtain" in block


def test_the_table_projects_onto_the_costume_block():
    s = _session()
    facets.write(
        s, "costume", tags="pleated_skirt, cardigan",
        fields={"silhouette": "soft A-line", "hero": "the cardigan",
                "garments": "top=cardigan / bottom=pleated_skirt"},
    )
    costume = facets.to_costume(facets.table_of(s))
    block = brief_mod.costume_block(costume)
    assert "COSTUME (LOCKED" in block
    assert "SILHOUETTE: soft A-line" in block
    assert costume["tags"] == ["pleated_skirt", "cardigan"]


def test_an_unwritten_costume_renders_no_locked_header():
    """`{}` is "Wardrobe has not spoken". Eight blank lines under a LOCKED
    header is a different and much worse statement."""
    s = _session()
    assert facets.to_costume(facets.table_of(s)) == {}
    assert brief_mod.costume_block(facets.to_costume(facets.table_of(s))) == ""


# ── migration ───────────────────────────────────────────────────────────────

def _legacy_session() -> dict:
    s = _session()
    s.pop("facets")
    s.pop("directives")
    s.pop("standing")
    s.pop("digest")
    s.pop("composed")
    s["plan"] = {
        "place": "A classroom", "hour": "Late afternoon", "light": "Low sun",
        "action": "Leaning on the sill", "must_appear": ["desk", "chalkboard"],
    }
    s["costume"] = {
        "silhouette": "soft A-line", "hero": "the cardigan",
        "garments": "top=cardigan / bottom=pleated_skirt",
        "tags": ["cardigan", "pleated_skirt"],
    }
    s["craft"] = {
        "prompt": "", "pose_intent": "She leans on the sill, weight on one hip.",
        "tags": ("from_above, looking_up, standing, smile, desk, chalkboard, "
                 "cardigan, warm_sunlight, potted_plant"),
        "scene": "A long paragraph that was already rendering.",
    }
    service._reassemble(s)
    return s


def test_migration_seeds_the_composed_prose_so_it_is_not_replaced_by_a_join():
    """A session that was already rendering keeps its paragraph. `composed` is
    seeded at the table's own revision, so the next reassemble reproduces the
    prose the crew wrote instead of the facet sentences run together."""
    s = _legacy_session()
    was = s["craft"]["prompt"]
    facets.migrate(s)
    assert s["composed"]["scene"] == "A long paragraph that was already rendering."
    assert s["composed"]["rev"] == facets.table_rev(facets.table_of(s))
    assert s["craft"]["prompt"] == was

    # Every tag that was in the flat bag is still in the shot, somewhere.
    table_tags = set(identity.tag_names(facets.all_tags(facets.table_of(s))))
    assert set(identity.tag_names(s["craft"]["tags"])) <= table_tags
    # The only thing it may have gained is a garment the COSTUME block named and
    # the tag list had lost — the outfit is authoritative, and putting it back is
    # what `_ensure_garments` was doing legitimately.
    gained = table_tags - set(identity.tag_names(s["craft"]["tags"]))
    assert gained <= set(brief_mod.garment_tags(s["costume"]))


def test_migration_files_each_tag_under_the_part_it_was_always_about():
    s = _legacy_session()
    facets.migrate(s)
    table = facets.table_of(s)
    assert "from_above" in table["camera"]["tags"]
    assert "looking_up" in table["camera"]["tags"]
    assert "standing" in table["pose"]["tags"]
    assert "smile" in table["expression"]["tags"]
    assert "warm_sunlight" in table["light"]["tags"]
    assert "cardigan" in table["costume"]["tags"]
    # Nothing else claims it, and the frame is where a stray noun belongs.
    assert "potted_plant" in table["props"]["tags"]
    assert table["place"]["nl"] == "A classroom"
    assert table["pose"]["nl"] == "She leans on the sill, weight on one hip."


def test_migration_is_idempotent():
    s = _legacy_session()
    facets.migrate(s)
    first = facets.table_of(s)["camera"]["tags"]
    rev = facets.table_rev(facets.table_of(s))
    facets.migrate(s)
    assert facets.table_of(s)["camera"]["tags"] == first
    assert facets.table_rev(facets.table_of(s)) == rev


def test_migration_is_non_destructive():
    """Rolling the code back has to restore the old behaviour with no data
    loss, so nothing it read is removed."""
    s = _legacy_session()
    facets.migrate(s)
    assert s["plan"]["place"] == "A classroom"
    assert s["costume"]["tags"] == ["cardigan", "pleated_skirt"]
    assert s["craft"]["scene"] == "A long paragraph that was already rendering."


def test_migration_starts_the_digest_empty():
    """A session that predates the digest has no decisions recorded yet — an
    empty digest is correct, not a gap to backfill from old chat."""
    s = _legacy_session()
    facets.migrate(s)
    assert s["digest"] == ""


# ── the derived craft ───────────────────────────────────────────────────────

def test_a_migrated_duet_session_keeps_its_whole_shot():
    """What migration guarantees, exactly.

    The prose is kept and every tag is kept. What is NOT kept is the order they
    were written in: `TAG_ORDER` regroups them by part, deliberately, so
    composition and the acting lead. A render already in flight is unaffected
    either way — `runner.py` reads the frozen `board["prompt"]` snapshot, not
    the craft — so the reordering lands on the next board and nowhere else.
    """
    s = _legacy_session()
    s["mode"] = "duet"
    was = s["craft"]["prompt"]
    facets.migrate(s)
    service._reassemble(s)

    assert s["craft"]["scene"] == "A long paragraph that was already rendering."
    assert s["craft"]["scene"] in s["craft"]["prompt"]
    assert set(identity.tag_names(was)) <= set(identity.tag_names(s["craft"]["prompt"]))


def test_the_derived_craft_falls_back_to_the_joined_sentences():
    """`craft["scene"]` is never blocked on a model call — the moment a facet
    lands, the shot is a valid prompt."""
    s = _session()
    s["mode"] = "duet"
    facets.write(s, "place", tags="classroom", nl="An empty classroom.")
    facets.write(s, "camera", tags="from_below", nl="Shot from below her.")
    service._reassemble(s)

    assert s["composed"]["scene"] == ""      # nothing has been composed
    assert s["craft"]["scene"] == "An empty classroom. Shot from below her."
    assert s["craft"]["tags"] == "from_below, classroom"
    assert "from_below" in s["craft"]["prompt"]


def test_a_stale_composition_is_not_used():
    s = _session()
    s["mode"] = "duet"
    facets.write(s, "place", nl="An empty classroom.")
    s["composed"] = {"scene": "Composed prose.", "rev":
                     facets.table_rev(facets.table_of(s)), "at": 0.0}
    service._reassemble(s)
    assert s["craft"]["scene"] == "Composed prose."

    facets.write(s, "camera", nl="Shot from below her.")
    service._reassemble(s)
    assert s["craft"]["scene"] == "An empty classroom. Shot from below her."


def test_the_brief_blocks_follow_the_table():
    s = _session()
    s["mode"] = "duet"
    facets.write(s, "place", nl="An empty classroom.")
    facets.write(s, "costume", tags="cardigan", fields={"hero": "the cardigan"})
    service._reassemble(s)
    assert s["plan"]["place"] == "An empty classroom."
    assert s["costume"]["tags"] == ["cardigan"]


def test_the_crewed_studio_is_not_on_the_facet_path():
    """Every facet code path is gated, and this is the gate. The crewed studio
    keeps its own machinery until each seat declares the parts it owns."""
    s = _session()
    assert service.on_facets(s) is False
    s["mode"] = "duet"
    assert service.on_facets(s) is True
    # W-Muse: one costume facet cannot say whose clothes those are.
    s["inputs"]["partner_preset"] = "someone"
    assert service.on_facets(s) is False


def test_a_new_session_already_has_the_table():
    s = _session()
    assert set(s["facets"]) == facets.FACET_NAMES
    assert s["directives"] == {} and s["standing"] == [] and s["digest"] == ""
    assert facets.table_rev(s["facets"]) == 0


def test_an_unknown_facet_is_refused():
    s = _session()
    with pytest.raises(ValueError):
        facets.write(s, "vibes", tags="x")


# ── the compose post-check ──────────────────────────────────────────────────

def test_compose_naming_a_refused_thing_is_never_usable():
    s = _session()
    facets.write(s, "costume", tags="pleated_skirt")
    usable, _ = facets.warn_invented_nouns(
        facets.table_of(s), "She wears a pleated skirt and a jacket.",
        banned=["jacket"],
    )
    assert usable is False


def test_compose_may_make_prose_out_of_what_the_table_says():
    s = _session()
    facets.write(s, "place", tags="classroom", nl="An empty classroom.")
    facets.write(s, "light", nl="Low sun through the glass.")
    usable, invented = facets.warn_invented_nouns(
        facets.table_of(s),
        "An empty classroom, low sun through the glass.",
    )
    assert usable is True
    assert invented == []


def test_compose_writing_its_own_scene_is_caught():
    s = _session()
    facets.write(s, "place", nl="A classroom.")
    usable, invented = facets.warn_invented_nouns(
        facets.table_of(s),
        "A harbour crowded with trawlers, gulls wheeling above rusted derricks, "
        "nets heaped on wet cobbles beside stacked lobster creels and buoys.",
    )
    assert usable is False
    assert len(invented) > facets.INVENTION_LIMIT

"""The locked COSTUME slot, and the garment tags that are its whole point.

Regression cover for two rounds of the same bug. First: the camera, writing into
an empty craft in the opening, authored the clothes, and a garment the theme
named ended up layered under the character's default outfit. Wardrobe owns the
outfit now, in a locked block.

Then the lock held and the clothes were still wrong, because the outfit was only
ever prose. `costume["tags"]` was the ledger diff of Wardrobe's turn — every tag
that seat added — so the pool she stood beside was filed as part of what she had
on, and the character's default outfit still reached the one seat that decides
clothes as a bare tag list that beat the theme. GARMENTS is the outfit as tags,
in coverage slots; the default rail is Wardrobe's alone and carries its own
discard rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import brief as brief_mod
from app.muse import chain, crew, schema, service

GARMENTS = "top=school_swimsuit / bottom=covered_by_top / feet=barefoot / extras=goggles"


def _session() -> dict:
    s = schema.new_session({
        "theme": "泳ぐ話", "character_id": "c1", "workflow": "w.json", "model": "m",
        "crew_preset": "standard",
    })
    s["character"] = {"identity_tags": ["1girl", "blue_hair"],
                      "outfit_tags": ["uniform", "collared_shirt", "work_shoes"],
                      "personality": {}, "palette": [], "signature_prop": ""}
    s["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    return s


def _costume(**over) -> dict:
    cos = {"silhouette": "sporty one-piece", "layers": "swimsuit", "colourway": "navy",
           "pattern": "solid", "fabric": "nylon", "condition": "damp",
           "hero": "goggles", "garments": GARMENTS}
    cos.update(over)
    return cos


def _turn(muse_id: str, tags: str, *, costume=None) -> chain.MuseTurn:
    return chain.MuseTurn(
        muse_id=muse_id, say="", prompt=f"1girl, {tags}", pose_intent="",
        tags=tags, scene="she is here.", raw="", costume=costume,
    )


# ── GARMENTS: the coverage axis ─────────────────────────────────────────────
def test_garments_parses_off_the_turn_and_renders_back_into_the_brief():
    raw = ("SAY: ok\n\nTAGS: 1girl, school_swimsuit\n\nSCENE: at the pool.\n\n"
           "COSTUME:\nSILHOUETTE: one-piece\nLAYERS: swimsuit\nCOLOURWAY: navy\n"
           "PATTERN: solid\nFABRIC: nylon\nCONDITION: damp\nHERO: goggles\n"
           f"GARMENTS: {GARMENTS}")
    w = chain._finish_turn(raw, muse_id="wardrobe:shiwa", identity_tags=["1girl"],
                           framing="auto", brief="")
    assert w.costume["garments"] == GARMENTS
    assert "COSTUME" not in w.scene                # stripped, not left in the prose
    assert "GARMENTS: top=school_swimsuit" in brief_mod.costume_block(w.costume)

    lens = chain._finish_turn(raw, muse_id="lens:pinto", identity_tags=["1girl"],
                              framing="auto", brief="")
    assert lens.costume is None                    # only Wardrobe's tail is parsed


def test_garment_tags_reads_the_slots():
    top_bottom = {"garments": "top=white_shirt, tucked_shirt / bottom=black_pants "
                              "/ feet=loafers / extras=wristwatch"}
    assert brief_mod.garment_tags(top_bottom) == [
        "white_shirt", "tucked_shirt", "black_pants", "loafers", "wristwatch",
    ]
    # A one-piece covers both halves; no phantom skirt is invented for the slot.
    assert brief_mod.garment_tags({"garments": GARMENTS}) == [
        "school_swimsuit", "barefoot", "goggles",
    ]
    # Unslotted, and `n/a` written out — neither should yield junk tags.
    assert brief_mod.garment_tags({"garments": "swimsuit, goggles"}) == [
        "swimsuit", "goggles",
    ]
    assert brief_mod.garment_tags({"garments": "top=gym_shirt / bottom=n/a"}) == [
        "gym_shirt",
    ]
    assert brief_mod.garment_tags({}) == []
    assert brief_mod.garment_tags({"garments": "   "}) == []


def test_garment_tags_survives_how_the_models_actually_write_the_slots():
    """Every one of these came off a real run. Splitting on the separator put
    the literal string `bottom=covered_by_top` into the craft as a tag, because
    half the models comma-separate the slots instead."""
    shapes = {
        "top=school_swimsuit,bottom=covered_by_top,feet=barefoot,extras=none":
            ["school_swimsuit", "barefoot"],
        "top=cotton_gym_shirt, bottom=cotton_shorts, feet=worn_shoes, extras=none":
            ["cotton_gym_shirt", "cotton_shorts", "worn_shoes"],
        "top=white_shirt / bottom=black_pants / feet=not_visible / extras=None":
            ["white_shirt", "black_pants"],
        "TOP: gym_shirt | BOTTOM: buruma | FEET: sneakers":
            ["gym_shirt", "buruma", "sneakers"],
    }
    for raw, want in shapes.items():
        assert brief_mod.garment_tags({"garments": raw}) == want, raw
        assert not any("=" in t for t in brief_mod.garment_tags({"garments": raw}))


# ── the costume tag set is clothes, and only clothes ────────────────────────
def test_costume_tags_are_the_garments_not_everything_wardrobe_added():
    """The bug this replaced: `costume["tags"]` was the turn's ledger diff, so a
    real session recorded the entire pool set as part of her outfit."""
    s = _session()
    service._apply_turn(s, _turn(
        "wardrobe:shiwa",
        "school_swimsuit, goggles, barefoot, poolside, blue_tile_coping, "
        "drain_grate, midday",
        costume=_costume(),
    ))
    assert s["costume"]["tags"] == ["school_swimsuit", "barefoot", "goggles"]
    assert "drain_grate" not in s["costume"]["tags"]
    assert "COSTUME (LOCKED" in s["brief"]         # re-stated for the next seat

    # The camera cannot own the outfit: a lens turn never sets costume.
    s2 = _session()
    service._apply_turn(s2, _turn("lens:pinto", "school_swimsuit", costume=None))
    assert s2["costume"] == {}


def test_a_garment_named_in_costume_but_missing_from_tags_is_put_back():
    s = _session()
    service._apply_turn(s, _turn("wardrobe:shiwa", "poolside, midday",
                                 costume=_costume()))
    tags = [t.strip() for t in s["craft"]["tags"].split(",")]
    assert "school_swimsuit" in tags and "goggles" in tags
    assert "school_swimsuit" in s["craft"]["prompt"]   # positive rebuilt too


def test_a_turn_without_garments_keeps_the_outfit_and_strikes_nothing():
    s = _session()
    service._apply_turn(s, _turn("wardrobe:shiwa", "school_swimsuit",
                                 costume=_costume()))
    before = list(s["costume"]["tags"])
    service._apply_turn(s, _turn("wardrobe:shiwa", "school_swimsuit, sunlight",
                                 costume=_costume(garments="")))
    assert s["costume"]["tags"] == before          # last known outfit held
    assert "school_swimsuit" in s["craft"]["tags"]
    assert not s.get("struck")                     # nothing removed on a blank


def test_showrunner_change_strikes_the_old_outfit_keeps_the_room():
    """§2-5: when Wardrobe rebuilds COSTUME, last outfit's garments are struck
    from the craft, but the room's props are not."""
    s = _session()
    s["craft"] = {"tags": "rush_guard, poolside, lane_rope", "scene": "at the pool.",
                  "prompt": "", "pose_intent": ""}
    s["costume"] = {"tags": ["school_swimsuit"]}   # Wardrobe just rebuilt it
    struck = service.strike_dropped_costume(s, {"tags": ["rush_guard"]})
    assert struck == ["rush_guard"]
    tags = [t.strip() for t in s["craft"]["tags"].split(",")]
    assert "rush_guard" not in tags                # old garment gone
    assert "poolside" in tags and "lane_rope" in tags   # room kept
    assert "rush_guard" in (s.get("struck") or [])      # surfaced to later seats


def test_changing_clothes_no_longer_strikes_the_location():
    """The pool used to ride in `costume["tags"]`, so a change of clothes took
    the set down with it."""
    s = _session()
    service._apply_turn(s, _turn(
        "wardrobe:shiwa", "rash_guard, poolside, blue_tile_coping, drain_grate",
        costume=_costume(garments="top=rash_guard / bottom=swim_briefs"),
    ))
    service._apply_turn(s, _turn(
        "wardrobe:shiwa", "poolside, blue_tile_coping, drain_grate",
        costume=_costume(),
    ))
    tags = [t.strip() for t in s["craft"]["tags"].split(",")]
    assert "rash_guard" not in tags and "swim_briefs" not in tags
    assert "blue_tile_coping" in tags and "drain_grate" in tags
    assert "school_swimsuit" in tags


def test_a_renamed_garment_is_not_struck():
    s = _session()
    s["craft"] = {"tags": "skirt, bench", "scene": "x", "prompt": "",
                  "pose_intent": ""}
    s["costume"] = {"tags": ["pleated_skirt"]}
    struck = service.strike_dropped_costume(s, {"tags": ["skirt"]})
    assert struck == []                            # skirt → pleated_skirt is a rename
    assert "skirt" in s["craft"]["tags"]


# ── hold the clothes, move the scene ────────────────────────────────────────
def test_moving_the_scene_does_not_undress_her():
    s = _session()
    s["craft"] = {"tags": "school_swimsuit, poolside, lane_rope", "scene": "x",
                  "prompt": "", "pose_intent": ""}
    s["costume"] = {"garments": GARMENTS, "tags": ["school_swimsuit"]}
    # The planner slipped a garment into MUST APPEAR and then moved the shoot.
    s["plan"] = {"must_appear": ["rooftop railing"]}
    struck = service.strike_dropped_props(
        s, {"must_appear": ["poolside", "lane_rope", "school_swimsuit"]},
    )
    assert "school_swimsuit" not in struck
    assert "school_swimsuit" in s["craft"]["tags"]
    assert "poolside" in struck and "lane_rope" in struck


# ── the seat that owns clothes has to be reachable ──────────────────────────
def test_wardrobe_answers_every_note_and_dresses_her_first():
    cast = crew.resolve_crew(preset="standard")
    responders = service._pick_responders("水着にして", cast)
    dresser = service._cast_in_role(cast, "wardrobe")
    assert dresser and responders[0] == dresser    # dress her, then frame her
    assert len(responders) == len(set(responders))
    # A cast with no wardrobe seat still answers.
    trio = crew.resolve_crew(preset="trio")
    assert service._pick_responders("x", trio)


# ── the default outfit reaches Wardrobe alone, with its discard rule ────────
def test_the_default_outfit_is_not_in_anybody_else_brief():
    s = _session()
    service._rebuild_brief(s)
    for text in (s["brief"], s["brief_lite"]):
        assert "collared_shirt" not in text
        assert "Outfit:" not in text


def test_the_rail_is_wardrobe_only_and_only_until_the_outfit_is_set():
    s = _session()
    rail = service._wardrobe_rail(s, "wardrobe:shiwa")
    assert "DEFAULT RAIL" in rail and "collared_shirt" in rail
    # The discard rule is read before the garments it discards.
    assert rail.index("If it names a garment") < rail.index("DEFAULT RAIL")
    assert service._wardrobe_rail(s, "lens:pinto") == ""
    s["costume"] = {"garments": GARMENTS, "tags": ["school_swimsuit"]}
    assert service._wardrobe_rail(s, "wardrobe:shiwa") == ""

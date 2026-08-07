"""The locked COSTUME slot.

Regression cover for the fix where the camera, writing first into an empty craft
in the opening, authored the clothes — and a garment the theme named ended up
layered under the character's default outfit. Wardrobe owns the outfit now, in a
locked block; the camera cannot; a Showrunner change strikes the old clothes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, schema, service


def _session() -> dict:
    s = schema.new_session({
        "theme": "泳ぐ話", "character_id": "c1", "workflow": "w.json", "model": "m",
        "crew_preset": "standard",
    })
    s["character"] = {"identity_tags": ["1girl", "blue_hair"],
                      "personality": {}, "palette": [], "signature_prop": ""}
    s["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    return s


def _turn(muse_id: str, tags: str, *, costume=None) -> chain.MuseTurn:
    return chain.MuseTurn(
        muse_id=muse_id, say="", prompt=f"1girl, {tags}", pose_intent="",
        tags=tags, scene="she is here.", raw="", costume=costume,
    )


def test_apply_turn_captures_costume_for_wardrobe_only():
    s = _session()
    cos = {"silhouette": "sporty one-piece", "layers": "-", "colourway": "navy",
           "pattern": "solid", "fabric": "nylon", "condition": "damp",
           "hero": "goggles"}
    service._apply_turn(s, _turn("wardrobe:shiwa", "school_swimsuit, goggles",
                                 costume=cos))
    assert s["costume"]["silhouette"] == "sporty one-piece"
    assert "school_swimsuit" in s["costume"]["tags"]
    # The rebuilt brief re-states the locked outfit for the next seat to read.
    assert "COSTUME (LOCKED" in s["brief"]

    # The camera cannot own the outfit: a lens turn never sets costume.
    s2 = _session()
    service._apply_turn(s2, _turn("lens:pinto", "school_swimsuit", costume=None))
    assert s2["costume"] == {}


def test_showrunner_change_strikes_the_old_outfit_keeps_the_room():
    """§2-5: when Wardrobe rebuilds COSTUME, last outfit's garments are struck
    from the craft, but the room's props are not."""
    s = _session()
    s["craft"] = {"tags": "rush_guard, poolside, lane_rope", "scene": "at the pool.",
                  "prompt": "", "pose_intent": ""}
    s["costume"] = {"tags": ["school_swimsuit"]}  # Wardrobe just rebuilt it
    struck = service.strike_dropped_costume(s, {"tags": ["rush_guard"]})
    assert struck == ["rush_guard"]
    tags = [t.strip() for t in s["craft"]["tags"].split(",")]
    assert "rush_guard" not in tags               # old garment gone
    assert "poolside" in tags and "lane_rope" in tags  # room kept
    assert "rush_guard" in (s.get("struck") or [])     # surfaced to later seats


def test_a_renamed_garment_is_not_struck():
    s = _session()
    s["craft"] = {"tags": "skirt, bench", "scene": "x", "prompt": "",
                  "pose_intent": ""}
    s["costume"] = {"tags": ["pleated_skirt"]}
    struck = service.strike_dropped_costume(s, {"tags": ["skirt"]})
    assert struck == []                           # skirt → pleated_skirt is a rename
    assert "skirt" in s["craft"]["tags"]


def test_finish_turn_attaches_costume_only_for_wardrobe():
    raw = ("SAY: ok\n\nTAGS: 1girl, school_swimsuit\n\nSCENE: at the pool.\n\n"
           "COSTUME:\nSILHOUETTE: one-piece\nLAYERS: -\nCOLOURWAY: navy\n"
           "PATTERN: solid\nFABRIC: nylon\nCONDITION: damp\nHERO: goggles")
    w = chain._finish_turn(raw, muse_id="wardrobe:shiwa", identity_tags=["1girl"],
                           framing="auto", brief="")
    assert w.costume and w.costume["silhouette"] == "one-piece"
    assert "COSTUME" not in w.scene               # stripped, not left in the prose

    lens = chain._finish_turn(raw, muse_id="lens:pinto", identity_tags=["1girl"],
                              framing="auto", brief="")
    assert lens.costume is None                   # only Wardrobe's tail is parsed

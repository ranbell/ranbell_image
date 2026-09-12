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

from tests.muse import _shape
from app.muse import brief as brief_mod
from app.muse import chain, notebook

GARMENTS = "top=school_swimsuit / bottom=covered_by_top / feet=barefoot / extras=goggles"


def _session() -> dict:
    s = _shape.new_session({
        "theme": "泳ぐ話", "character_id": "c1", "workflow": "w.json", "model": "m",
        "crew_preset": "standard",
    })
    s["character"] = {"identity_tags": ["1girl", "blue_hair"],
                      "outfit_tags": ["uniform", "collared_shirt", "work_shoes"],
                      "personality": {}, "palette": [], "signature_prop": ""}
    s["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    return s




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


# ── one garment, one name ──────────────────────────────────────────────────
# Every string below was lifted out of a live notebook. Twelve of twenty-two
# ran the same garment twice under two names, which is what made 「脱いで」
# unanswerable: with two coats listed, the request has no single referent.
LIVE_WEARING = [
    (
        "charcoal_grey_heavy_coat, turtleneck, knit_sweater, "
        "heavy_wool_coat + dark_tights, off-white_turtleneck, dark_pleated_skirt",
        ["coat", "turtleneck"],          # one coat, one turtleneck
    ),
    ("indigo_yukata, yukata, undergarment (sumizome), covered_by_top, none",
     ["yukata"]),
    ("navy_blazer, white_shirt, necktie, blazer, pleated_skirt, loafers",
     ["blazer"]),
    ("chunky_grey_cardigan, white_shirt, grey_cardigan + school_skirt, "
     "grey_cardigan, navy_pleated_skirt",
     ["cardigan", "skirt"]),
    ("black_silk_dress, sleeveless_dress, none", ["dress"]),
]


def test_the_outfit_does_not_list_the_same_garment_twice():
    for raw, heads in LIVE_WEARING:
        out = brief_mod.tidy_wearing(raw)
        items = [p.strip() for p in out.split(",") if p.strip()]
        assert len(items) <= brief_mod.WEARING_MAX_ITEMS
        for head in heads:
            named = [i for i in items if brief_mod.garment_head(i) == head]
            assert len(named) == 1, f"{head!r} listed {len(named)}× in {out!r}"
        # The junk tokens the wardrobe seat emits are not clothes.
        assert "none" not in out and "covered_by_top" not in out
        # `+` glues two garments into one token; unglued, both survive.
        assert "+" not in out


def test_tidying_the_outfit_keeps_the_more_precise_name():
    # The qualifier is the information. Dropping it to keep the bare noun
    # would undress her in the picture even though the list still reads full.
    assert brief_mod.tidy_wearing("indigo_yukata, yukata") == "indigo_yukata"
    assert brief_mod.tidy_wearing("blazer, navy_blazer") == "navy_blazer"


def test_the_outfit_is_never_emptied_by_tidying():
    # `none` alone still means nothing, but a line of unreadable garments is
    # not a reason to strip her.
    assert brief_mod.tidy_wearing("none, covered_by_top") == ""
    assert brief_mod.tidy_wearing("coat") == "coat"
    assert brief_mod.tidy_wearing("sailor uniform, straw hat, cardigan") == (
        "sailor uniform, straw hat, cardigan"
    )


def test_a_garment_worn_on_a_body_part_is_the_garment():
    # `blanket on shoulders` is a blanket. Reading the last word alone would
    # file it under shoulders and collide with anything else worn there.
    assert brief_mod.garment_head("blanket on shoulders") == "blanket"
    assert brief_mod.garment_head("the_black_silk_dress") == "dress"
    assert brief_mod.garment_head("undergarment (sumizome)") == "undergarment"


def test_wearing_nothing_is_a_real_answer():
    # `none` in a slot never meant she is naked — it meant "no separate
    # garment here", the one-piece case. Nudity is said with words that are
    # not junk, and those survive untouched.
    assert brief_mod.tidy_wearing("sundress, bottom=covered_by_top, feet=barefoot") == (
        "sundress, barefoot"
    )
    for said in ("nude", "naked, wet_skin", "bare_shoulders, barefoot"):
        assert brief_mod.tidy_wearing(said) == said
    # An outfit that is nothing but slot-filler clears the field rather than
    # inventing a garment out of the filler. `n/a` splits into two single
    # letters, and the "never undress her" fallback must not resurrect one.
    for junk in ("none", "n/a", "none, n/a, covered_by_top"):
        assert brief_mod.tidy_wearing(junk) == ""


def test_clearing_the_outfit_still_clears_it():
    nb = notebook.blank()
    nb["wearing"] = "sailor uniform, cardigan"
    notebook.apply_patch(nb, {"wearing": ""})
    assert nb["wearing"] == ""


def test_a_garment_written_as_a_sentence_is_still_one_garment():
    # Live WEARING, from a wardrobe seat that writes prose:
    #   The navy pleated skirt that holds its shape even in motion,
    #   White short-sleeve sailor top, navy pleated skirt, undershirt
    # Two skirts. The long one's last word is `motion`, so nothing could see
    # they were the same garment, and 「スカート脱いで」 had two referents.
    out = brief_mod.tidy_wearing(
        "The navy pleated skirt that holds its shape even in motion, "
        "White short-sleeve sailor top, navy pleated skirt, undershirt"
    )
    assert out == "navy pleated skirt, White short-sleeve sailor top, undershirt"
    assert len(notebook.garment_matches(out, "skirt")) == 1


def test_where_a_garment_sits_is_kept():
    # The relative clause is description and goes; a preposition says where the
    # thing is on her, which is part of the picture and stays.
    assert brief_mod.garment_core("blanket on shoulders") == "blanket on shoulders"
    assert brief_mod.garment_core("The stiff collar that never softens") == "stiff collar"
    assert brief_mod.garment_core("Navy blue collar and trim") == "Navy blue collar"
    assert brief_mod.garment_core("cardigan") == "cardigan"

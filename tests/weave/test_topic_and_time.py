"""Regressions for the two field failures: the topic losing to the character,
and three panels compiling to the same picture.

Measured on the reported case (topic 真夏の海辺の花火大会 + shy_bookworm preset):
before these fixes the compiler kept `cardigan / loafers / long_sleeves` and
dropped `fireworks`, and the three panels shared 13 tags with only 3 story-driven
differences in total — panel_1 had none at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.topic_anchors import topic_anchor_groups
from app.weave.character.presets import load_seed_presets, preset_to_character
from app.weave.compile.budget import cap_positive_tags
from app.weave.compile.cameras import CAMERA_FORCE_ADD
from app.weave.compile.layers import compile_all_panels
from app.weave.schema import new_session_payload
from app.weave.state_machine import gates, next_cta
from app.weave.story.recreate import chips_to_constraints
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.causality import lint_causality
from app.weave.validate.must_show_resolve import _place_tags
from app.weave.validate.story_lint import lint_story_bundle

CAMERA_TAGS = {t for v in CAMERA_FORCE_ADD.values() for t in v}
WINTER = {"cardigan", "loafers", "long_sleeves"}


def _beach_session():
    session = new_session_payload(topic="真夏の海辺の花火大会", personality_text="x")
    preset = next(p for p in load_seed_presets() if p["id"] == "shy_bookworm")
    session["character"].update(preset_to_character(preset))
    session["character"]["identity_locked"] = True
    return session


def _beach_bundle():
    return normalize_story_bundle({
        "title": "t",
        "world": {
            "setting": "summer beach fireworks festival",
            "core_conflict": "c", "ending_intent": "quiet hope",
            "throughline_place": "beach", "throughline_prop": "book",
            "time_scale": "hours",
            "place_tags": ["beach", "night_sky", "fireworks"],
            "outfit_tags": ["swimsuit", "sandals"],
            "causality_one_liner": "a → b → c",
        },
        "panels": [
            {"visible_change": "stands on the sand", "camera": "long_shot",
             "gesture": "holding_book", "emotion": "blush",
             "time_marker": "late_afternoon", "state_tags": ["dry_sand", "closed_book"],
             "must_show": ["throughline_prop", "throughline_place"],
             "narrative_en": "standing on the beach"},
            {"visible_change": "the wind turns a page", "camera": "medium_shot",
             "gesture": "adjusting_glasses", "emotion": "closed_mouth",
             "time_marker": "dusk", "state_tags": ["wind", "turning_pages", "wet_sand"],
             "must_show": ["throughline_prop", "throughline_place"],
             "narrative_en": "the wind turns a page"},
            {"visible_change": "she closes it and looks up", "camera": "close_up",
             "gesture": "holding_book", "emotion": "blush",
             "time_marker": "night", "state_tags": ["fireworks_reflection", "closed_book"],
             "must_show": ["throughline_prop", "throughline_place"],
             "narrative_en": "she looks up"},
        ],
    })


def _compiled(session, bundle):
    lint = lint_story_bundle(bundle, session["character"])
    assert lint["pass"], lint["defects"]
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    return compile_all_panels(session)


# ── Symptom 1: the topic must dress her ───────────────────────────────────────
def test_preset_keeps_clothing_out_of_identity():
    preset = next(p for p in load_seed_presets() if p["id"] == "shy_bookworm")
    ch = preset_to_character(preset)
    assert "cardigan" not in ch["identity_tags"]
    assert "loafers" not in ch["identity_tags"]
    assert {"cardigan", "loafers"} <= set(ch["outfit_tags"])
    # Body identity survives — she is still the same person.
    assert {"1girl", "black_hair", "brown_eyes"} <= set(ch["identity_tags"])


def test_story_wardrobe_replaces_the_default_one():
    session = _beach_session()
    assert WINTER & set(session["character"]["outfit_tags"])  # her usual clothes

    out = _compiled(session, _beach_bundle())
    for key, panel in out.items():
        tags = set(panel["positive"].split(", "))
        assert not (WINTER & tags), f"{key} still dresses her for a bookshop"
        assert "swimsuit" in tags, key
    assert out["panel_1"]["layers"]["outfit"] == ["swimsuit", "sandals"]


def test_topic_survives_the_tag_budget():
    """Over budget it used to drop the topic and keep the cardigan."""
    body = ["1girl", "black_hair", "long_hair", "brown_eyes"]
    state = ["fireworks", "wet_sand", "crowd_silhouette"]
    wardrobe = ["cardigan", "loafers", "long_sleeves"]
    filler = [f"filler_{i}" for i in range(30)]
    kept = set(cap_positive_tags(
        body + wardrobe + state + filler, priority=body + state, max_tags=8,
    ))
    # The topic is priority now, so it is never what gets cut.
    assert set(state) <= kept, "the topic was dropped again"
    assert len(set(wardrobe) - kept) >= 2, "wardrobe should yield before the topic"


def test_japanese_topics_expand_to_english_anchors():
    groups = topic_anchor_groups("水着を着てでビーチの人気者")
    flat = {t for g in groups for t in g}
    assert {"swimsuit", "beach"} <= flat


# ── Symptom 2: three panels must not be one moment ────────────────────────────
def test_panels_carry_their_own_story_tags():
    out = _compiled(_beach_session(), _beach_bundle())
    sets = {k: set(v["positive"].split(", ")) for k, v in out.items()}

    unique_per_panel = {}
    for key, tags in sets.items():
        others = set().union(*(v for k, v in sets.items() if k != key))
        unique_per_panel[key] = {t for t in tags - others if t not in CAMERA_TAGS}

    # Before: 3 story-driven differences in total and panel_1 had zero.
    assert sum(len(v) for v in unique_per_panel.values()) >= 8, unique_per_panel
    for key, uniq in unique_per_panel.items():
        assert len(uniq) >= 2, f"{key} is indistinguishable from the others: {uniq}"

    # state_tags are the per-panel signal and must actually land in the prompt.
    assert "turning_pages" in sets["panel_2"]
    assert "fireworks_reflection" in sets["panel_3"]
    assert out["panel_2"]["layers"]["state"] == ["wind", "turning_pages", "wet_sand"]


def test_flat_time_markers_are_a_defect():
    bundle = _beach_bundle()
    for panel in bundle["panels"]:
        panel["time_marker"] = "afternoon"
    codes = {d["code"] for d in lint_causality(bundle)}
    assert "TIME_MARKER_FLAT" in codes

    for panel in bundle["panels"]:
        panel["time_marker"] = ""
    assert "TIME_MARKER_FLAT" in {d["code"] for d in lint_causality(bundle)}


def test_place_tags_never_emit_japanese_or_guess_indoors():
    assert _place_tags("花火大会") == []
    assert "indoors" not in _place_tags("海辺")
    # Latin place names still resolve.
    assert "beach" in _place_tags("beach")
    assert "bookshelf" in _place_tags("bookstore")


def test_story_place_tags_beat_the_hardcoded_lexicon():
    """The 9-word lexicon knew nothing about beaches; the story does."""
    out = _compiled(_beach_session(), _beach_bundle())
    env = out["panel_1"]["layers"]["environment"]
    assert {"beach", "fireworks", "night_sky"} <= set(env)


# ── Surfacing the failure when it happens anyway ──────────────────────────────
def test_low_topic_fit_routes_to_recreate():
    session = _beach_session()
    out_bundle = _beach_bundle()
    lint = lint_story_bundle(out_bundle, session["character"])
    apply_story_to_session(session, out_bundle)
    session["last_lint"] = lint
    session["status"] = "story"
    session.setdefault("cross_panel_qa", {})["weave_score"] = {
        "dimensions": {"topic_fit": 0.25},
    }
    g = gates(session)
    assert g["G2"]["pass"] is False
    assert g["G2"]["topic_off"] is True

    cta = next_cta(session)
    assert cta["code"] == "recreate_story"
    assert cta["suggest_chips"] == ["off_topic"]


def test_unrendered_board_offers_render_not_accept():
    """Accept refuses an empty board, so offering accept was a dead end."""
    session = _beach_session()
    bundle = _beach_bundle()
    lint = lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session["status"] = "lookdev"
    # Past the sample and framing gates, so the board is what is left.
    session["panels"][0]["sample"] = {"image_id": "img-s", "job_id": None}
    session["panels"][0]["qa"]["framing"] = "pass"
    board = session["character"]["board"]

    # Nothing queued yet (the lock-time render failed or never ran).
    assert next_cta(session)["code"] == "render_board"

    # Jobs in flight → wait, do not offer a second render.
    board["images"] = [
        {"slot": s, "image_id": None, "job_id": f"gen-{s}", "pending": True}
        for s in ("portrait", "full", "prop")
    ]
    cta = next_cta(session)
    assert cta["code"] == "wait_board"
    assert cta["enabled"] is False

    # Rendered → accept becomes the real next step.
    for img in board["images"]:
        img["image_id"] = f"img-{img['slot']}"
        img["pending"] = False
    assert next_cta(session)["code"] == "accept_board"


def test_off_topic_chip_has_an_imperative():
    constraints = chips_to_constraints(["off_topic", "same_moment"])
    assert len(constraints) == 2
    assert "USER TOPIC" in constraints[0]
    assert "time_scale" in constraints[1]

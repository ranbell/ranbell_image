"""Hardening: framing unknown, seal finals, rollback lint, G2 coverage."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.schema import new_session_payload
from app.weave.state_machine import gates, next_cta
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.service import rollback_story
from app.weave.validate.story_lint import lint_story_bundle
from app.weave.verify.heuristics import evaluate_long_shot_framing
from app.weave.verify.seal import evaluate_seal_rubric


def _ok_bundle():
    return normalize_story_bundle({
        "title": "雨のしおり",
        "world": {
            "setting": "rainy bookstore",
            "core_conflict": "閉店間際の注文票",
            "ending_intent": "静かな希望",
            "throughline_place": "bookstore counter",
            "throughline_prop": "cloth_bookmark",
            "time_scale": "hours",
            "causality_one_liner": "レジで見つけ、雨漏りで濡れ、色がにじむ",
        },
        "panels": [
            {
                "key": "panel_1",
                "narrative_ja": "レジで布のしおりを見つめる",
                "visible_change": "しおりを手に取る",
                "camera": "long_shot",
                "gesture": "reaching",
                "emotion": "serious",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_2",
                "narrative_ja": "雨漏りで棚が濡れる",
                "visible_change": "棚が濡れる",
                "camera": "medium_shot",
                "gesture": "holding wet book",
                "emotion": "surprised",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_3",
                "narrative_ja": "にじんだしおりを見る",
                "visible_change": "しおりの色がにじむ",
                "camera": "close_up",
                "gesture": "looking at bookmark",
                "emotion": "soft smile",
                "must_show": ["throughline_prop", "throughline_place"],
            },
        ],
    })


def _ready_session():
    session = new_session_payload(topic="雨")
    session["session_id"] = "test-session"
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    session["character"]["board"] = {
        "accepted": True,
        "images": [
            {"slot": "portrait", "image_id": "a" * 64},
            {"slot": "full", "image_id": "b" * 64},
            {"slot": "prop", "image_id": "c" * 64},
        ],
    }
    bundle = _ok_bundle()
    lint = lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session["status"] = "lookdev"
    for p in session["panels"]:
        p["sample"] = {"image_id": "s" * 64}
        if p["intent"]["camera"] == "long_shot":
            p.setdefault("qa", {})["framing"] = "pass"
    return session


def test_g4_unknown_blocks():
    session = _ready_session()
    for p in session["panels"]:
        if p["intent"]["camera"] == "long_shot":
            p["qa"]["framing"] = "unknown"
    g = gates(session)
    assert g["G4"]["pass"] is False
    assert g["G4"].get("pending") is True
    assert next_cta(session)["code"] == "reeval_framing"


def test_seal_requires_finals():
    session = _ready_session()
    rubric = evaluate_seal_rubric(session)
    assert rubric["checks"]["has_finals"] is False
    assert rubric["pass"] is False
    for p in session["panels"]:
        p["final"] = {"image_id": ("f" + p["key"][-1]) * 64}
    rubric = evaluate_seal_rubric(session)
    assert rubric["checks"]["has_finals"]
    assert rubric["core_pass"]
    assert rubric["pass"]
    assert next_cta(session)["code"] == "seal"


def test_g2_throughline_ratio():
    session = _ready_session()
    g = gates(session)
    assert g["G2"]["pass"] is True


def test_rollback_restores_lint():
    session = _ready_session()
    v1 = session["story_version"]
    # Push history then apply a broken bundle as current
    session.setdefault("story_history", []).append({
        "version": v1,
        "bundle": session["story_bundle"],
        "reasons": [],
        "constraints": [],
        "at": 0,
    })
    broken = _ok_bundle()
    broken["world"]["causality_one_liner"] = ""
    lint = lint_story_bundle(broken, session["character"])
    apply_story_to_session(session, broken)
    session["last_lint"] = lint
    assert gates(session)["G1"]["pass"] is False
    rollback_story(session, v1)
    assert session["last_lint"]["pass"] is True
    assert gates(session)["G1"]["pass"] is True
    assert next_cta(session)["code"] == "enter_lookdev"


def test_framing_empty_is_unknown():
    assert evaluate_long_shot_framing([]) == "unknown"
    assert evaluate_long_shot_framing(None) == "unknown"

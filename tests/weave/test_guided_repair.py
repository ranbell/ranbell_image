"""Guided repair chips: too_close place inject, missing_prop, wrong_person."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.schema import new_session_payload
from app.weave.service import rate_sample
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle


def _session():
    session = new_session_payload(topic="雨の書店")
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    session["character"]["gallery_spice"] = ["cinematic_lighting", "bokeh"]
    bundle = normalize_story_bundle({
        "title": "t",
        "world": {
            "setting": "rainy bookstore",
            "core_conflict": "c",
            "ending_intent": "hope",
            "throughline_place": "bookstore counter",
            "throughline_prop": "cloth_bookmark",
            "time_scale": "hours",
            "causality_one_liner": "a then b then c",
        },
        "panels": [
            {
                "key": "panel_1",
                "narrative_ja": "n1",
                "visible_change": "v1",
                "camera": "long_shot",
                "gesture": "looking_at_viewer",
                "emotion": "expressionless",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_2",
                "narrative_ja": "n2",
                "visible_change": "v2",
                "camera": "medium_shot",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_3",
                "narrative_ja": "n3",
                "visible_change": "v3",
                "camera": "close_up",
                "must_show": ["throughline_prop", "throughline_place"],
            },
        ],
    })
    lint = lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session["status"] = "lookdev"
    session["panels"][0]["sample"] = {"image_id": "s" * 64}
    return session


def test_too_close_injects_place_into_must_show_resolved():
    session = _session()
    rate_sample(session, panel_key="panel_1", chips=["too_close"])
    resolved = session["panels"][0]["intent"]["must_show_resolved"]
    assert "bookstore counter" in resolved
    assert "rainy bookstore" in resolved
    assert session["panels"][0]["qa"]["framing"] == "fail"
    pos = session["panels"][0]["compile"]["positive"]
    assert "bookstore" in pos.lower() or "counter" in pos.lower()


def test_missing_prop_double_injects():
    session = _session()
    rate_sample(session, panel_key="panel_1", chips=["missing_prop"])
    intent = session["panels"][0]["intent"]
    assert intent["focus"] == "cloth_bookmark"
    assert "cloth_bookmark" in intent["must_show_resolved"]


def test_wrong_person_clears_state_noise():
    session = _session()
    rate_sample(session, panel_key="panel_1", chips=["wrong_person"])
    assert session["suggest_reinfer"] is True
    intent = session["panels"][0]["intent"]
    assert intent.get("gesture") in ("", None)
    assert intent.get("emotion") in ("", None)
    assert session["character"]["gallery_spice"] == []

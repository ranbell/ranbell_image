from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.compile.layers import compile_panel
from app.weave.schema import new_session_payload
from app.weave.state_machine import gates, next_cta
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle


def _bundle():
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
                "gesture": "reaching for bookmark",
                "emotion": "serious",
                "time_marker": "overcast",
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


def test_lint_and_compile_keeps_identity_and_prop_layers():
    session = new_session_payload(topic="雨の日の書店", personality_text="慎重な店員")
    session["character"]["identity_tags"] = ["1girl", "brown_hair", "cardigan"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    bundle = _bundle()
    lint = lint_story_bundle(bundle, session["character"])
    assert lint["pass"], lint["defects"]
    apply_story_to_session(session, bundle)
    for p in session["panels"]:
        p["intent"]["must_show_resolved"] = (
            next(x for x in bundle["panels"] if x["key"] == p["key"])["must_show_resolved"]
        )
    out = compile_panel(session, "panel_1")
    assert "brown_hair" in out["positive"]
    assert "cloth_bookmark" in out["positive"]
    assert "long_shot" in out["positive"]
    assert "close-up" not in out["layers"]["camera"]
    assert "cloth_bookmark" not in out["layers"]["identity"]


def test_cta_story_before_board():
    session = new_session_payload(topic="雨の日の書店", author_style="静かな観察者の文体")
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    session["character"]["identity_locked"] = True
    g = gates(session)
    assert g["G0_soft"]["pass"]
    assert not g["G0_hard"]["pass"]
    cta = next_cta(session)
    assert cta["code"] == "generate_story"
    assert cta["enabled"] is True

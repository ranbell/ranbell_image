"""Service-level flow without LLM / Qdrant."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.schema import new_session_payload
from app.weave import service
from app.weave.state_machine import next_cta
from app.weave.story.storywright import normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle
from app.weave.story.storywright import apply_story_to_session


def test_lock_enter_lookdev_sample_rate_override():
    session = new_session_payload(topic="雨の日の書店", personality_text="慎重な店員")
    session["character"]["identity_tags"] = ["1girl", "brown_hair", "cardigan", "cloth_bookmark"]
    session["character"]["prop_tags"] = []
    session["character"]["signature_prop"] = "cloth_bookmark"
    service.lock_identity(session)
    assert session["character"]["identity_locked"]
    assert "cloth_bookmark" not in session["character"]["identity_tags"]
    assert "cloth_bookmark" in session["character"]["prop_tags"]

    bundle = normalize_story_bundle({
        "title": "t",
        "world": {
            "setting": "rainy bookstore",
            "core_conflict": "c",
            "ending_intent": "hope",
            "throughline_place": "bookstore",
            "throughline_prop": "cloth_bookmark",
            "time_scale": "hours",
            "causality_one_liner": "a then b then c",
        },
        "panels": [
            {"visible_change": "finds bookmark", "camera": "long_shot",
             "must_show": ["throughline_prop", "throughline_place"],
             "narrative_ja": "しおりを見つける"},
            {"visible_change": "shelf gets wet", "camera": "medium_shot",
             "must_show": ["throughline_prop", "throughline_place"],
             "narrative_ja": "棚が濡れる"},
            {"visible_change": "dye runs", "camera": "close_up",
             "must_show": ["throughline_prop", "throughline_place"],
             "narrative_ja": "色がにじむ"},
        ],
    })
    lint = lint_story_bundle(bundle, session["character"])
    assert lint["pass"], lint
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    service.enter_lookdev(session)
    assert session["status"] == "lookdev"
    assert session["panels"][0]["compile"]["positive"]

    service.mark_sample_placeholder(session, "panel_1")
    service.rate_sample(session, panel_key="panel_1", chips=["too_close"])
    assert session["panels"][0]["framing_fail_count"] == 1
    service.rate_sample(session, panel_key="panel_1", chips=["too_close"])
    assert session["panels"][0]["framing_fail_count"] == 2
    service.override_framing(session, panel_key="panel_1", reason="acceptable wide enough")
    cta = next_cta(session)
    # board not accepted yet
    assert cta["code"] in ("accept_board", "render_final", "fix_framing_or_override", "sample_panel")

    session["character"]["board_briefs"] = [
        {"slot": "portrait"}, {"slot": "full"}, {"slot": "prop"},
    ]
    service.accept_board(session)
    assert session["character"]["board"]["accepted"]

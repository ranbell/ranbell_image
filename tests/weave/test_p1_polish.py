"""P1 polish: narrative patch, topic fit, critic fallback, unlock."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.character.topic_fit import topic_outfit_warnings
from app.weave.schema import new_session_payload
from app.weave.service import unlock_identity, WeaveError
from app.weave.story.critic import code_critic_fallback, normalize_critic_report
from app.weave.story.narrative_patch import NarrativePatchError, apply_narrative_typo_patch
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle


def test_topic_outfit_clash():
    warns = topic_outfit_warnings(
        topic="雨の日の小さな書店",
        identity_tags=["1girl", "armor", "sword", "cardigan"],
    )
    assert any(w["code"] == "TOPIC_OUTFIT_CLASH" for w in warns)


def test_narrative_typo_ok_and_reject_rewrite():
    session = new_session_payload(topic="雨")
    session["character"]["identity_tags"] = ["1girl"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    bundle = normalize_story_bundle({
        "title": "t",
        "world": {
            "setting": "bookstore",
            "core_conflict": "c",
            "ending_intent": "hope",
            "throughline_place": "counter",
            "throughline_prop": "cloth_bookmark",
            "time_scale": "hours",
            "causality_one_liner": "a then b then c",
        },
        "panels": [
            {
                "key": "panel_1",
                "narrative_ja": "しおりを手に取る",
                "visible_change": "取る",
                "camera": "long_shot",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_2",
                "narrative_ja": "棚が濡れる",
                "visible_change": "濡れる",
                "camera": "medium_shot",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_3",
                "narrative_ja": "色がにじむ",
                "visible_change": "にじむ",
                "camera": "close_up",
                "must_show": ["throughline_prop", "throughline_place"],
            },
        ],
    })
    lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    apply_narrative_typo_patch(
        session, panel_key="panel_1", narrative_ja="しおりを手にとる",
    )
    assert session["panels"][0]["intent"]["narrative_ja"] == "しおりを手にとる"
    try:
        apply_narrative_typo_patch(
            session,
            panel_key="panel_1",
            narrative_ja="まったく別の長い物語で altogether different plot rewrite here",
        )
        assert False, "expected NarrativePatchError"
    except NarrativePatchError:
        pass


def test_unlock_requires_confirm():
    session = new_session_payload()
    session["character"]["identity_locked"] = True
    session["story_version"] = 1
    session["story_bundle"] = {"world": {"setting": "x"}}
    try:
        unlock_identity(session, confirm=False)
        assert False
    except WeaveError:
        pass
    unlock_identity(session, confirm=True)
    assert session["character"]["identity_locked"] is False
    assert session["story_version"] == 0


def test_critic_fallback():
    report = code_critic_fallback([
        {"code": "WORLD_MISSING", "problem": "empty", "fix": "fill"},
    ])
    assert report["priority_defects"]
    assert report["recreate_hint"] == "unclear_story"
    norm = normalize_critic_report(
        {"summary_ja": "壊れている", "recreate_hint": "weak_plot", "priority_defects": []},
        [{"code": "X", "problem": "p", "fix": "f"}],
    )
    assert norm["summary_ja"] == "壊れている"
    assert norm["priority_defects"][0]["code"] == "X"

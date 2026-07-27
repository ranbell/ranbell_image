"""P2: WeaveScore, VLM fixed-4Q, cross-panel, SSE events."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.events import publish, subscribe, unsubscribe, subscriber_count
from app.weave.schema import new_session_payload
from app.weave.service import mark_sample_placeholder, recompute_scores
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle
from app.weave.verify.cross_panel import refresh_cross_panel_qa
from app.weave.verify.score import apply_weave_scores, compute_weave_score
from app.weave.verify.vlm_assist import (
    VLM_QUESTIONS,
    heuristic_vlm_answers,
    normalize_vlm_answers,
    build_vlm_assist_prompt,
)


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


def _session_with_story():
    session = new_session_payload(topic="雨の日の小さな書店")
    session["character"]["identity_tags"] = ["1girl", "brown_hair", "green_eyes"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    bundle = _ok_bundle()
    lint = lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    # Seed compile positives so richness/identity can score
    for p in session["panels"]:
        intent = p["intent"]
        p["compile"] = {
            "positive": (
                f"1girl, brown_hair, green_eyes, cloth_bookmark, "
                f"{intent['camera']}, {intent['emotion']}, bookstore, "
                f"holding, reaching, window, rain"
            ),
            "negative": "",
            "layers": {},
        }
    return session


def test_vlm_questions_fixed_four():
    assert len(VLM_QUESTIONS) == 4
    assert "same_person" in VLM_QUESTIONS
    assert "prop_visible" in VLM_QUESTIONS


def test_normalize_vlm_answers():
    ans = normalize_vlm_answers({
        "same_person": "yes",
        "prop_visible": False,
        "framing_ok": True,
        "expression_alive": "no",
        "extra": 1,
    })
    assert ans["same_person"] is True
    assert ans["prop_visible"] is False
    assert ans["framing_ok"] is True
    assert ans["expression_alive"] is False


def test_heuristic_vlm_framing_fail():
    ans = heuristic_vlm_answers(
        wd14_tags=["portrait", "close-up", "1girl", "brown_hair", "smile"],
        identity_tags=["1girl", "brown_hair", "green_eyes"],
        signature_prop="cloth_bookmark",
        prop_tags=["cloth_bookmark"],
        camera="long_shot",
    )
    assert ans["framing_ok"] is False
    assert ans["same_person"] is True  # brown_hair hit
    assert ans["prop_visible"] is False
    assert ans["expression_alive"] is True


def test_heuristic_vlm_pass():
    ans = heuristic_vlm_answers(
        wd14_tags=[
            "1girl", "brown_hair", "green_eyes", "cloth_bookmark",
            "full_body", "bookstore", "smile", "holding",
        ],
        identity_tags=["1girl", "brown_hair", "green_eyes"],
        signature_prop="cloth_bookmark",
        camera="long_shot",
    )
    assert ans["same_person"] is True
    assert ans["prop_visible"] is True
    assert ans["framing_ok"] is True
    assert ans["expression_alive"] is True


def test_vlm_prompt_mentions_questions():
    prompt = build_vlm_assist_prompt(
        identity_tags=["brown_hair"],
        signature_prop="bookmark",
        prop_tags=[],
        camera="long_shot",
    )
    for q in VLM_QUESTIONS:
        assert q in prompt


def test_weave_score_computes():
    session = _session_with_story()
    score = compute_weave_score(session)
    assert score["ok"] is True
    assert score["overall"] is not None
    assert 0.0 <= float(score["overall"]) <= 1.0
    dims = score["dimensions"]
    assert "topic_fit" in dims
    assert "identity" in dims


def test_apply_weave_scores_on_panels():
    session = _session_with_story()
    apply_weave_scores(session)
    assert session["cross_panel_qa"]["weave_score"]["overall"] is not None
    for p in session["panels"]:
        assert p["qa"]["weave_score"] is not None


def test_placeholder_sample_scores():
    session = _session_with_story()
    session["status"] = "lookdev"
    mark_sample_placeholder(session, "panel_1")
    assert session["panels"][0]["sample"]["image_id"].startswith("placeholder:")
    assert session["cross_panel_qa"].get("weave_score")
    assert session["cross_panel_qa"].get("lookdev_ready") is True


def test_cross_panel_refresh():
    session = _session_with_story()
    session["panels"][0]["sample"] = {"image_id": "x"}
    session["panels"][0]["qa"] = {
        "framing": "pass",
        "vlm": {"answers": {"same_person": False}},
    }
    qa = refresh_cross_panel_qa(session)
    assert qa["camera_diversity"] == 1.0
    assert qa["identity_drift_risk"] == 1.0
    assert qa["lookdev_ready"] is True


def test_recompute_scores_service():
    session = _session_with_story()
    score = recompute_scores(session)
    assert score["overall"] is not None


def test_sse_publish_subscribe():
    async def _run():
        sid = "test-sse-session"
        q = await subscribe(sid)
        assert subscriber_count(sid) == 1
        publish(sid, {"type": "hello"})
        item = await asyncio.wait_for(q.get(), timeout=1.0)
        assert item["type"] == "hello"
        assert item["session_id"] == sid
        await unsubscribe(sid, q)
        assert subscriber_count(sid) == 0

    asyncio.run(_run())

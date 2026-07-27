"""P0 gate / repairer / framing / reference-mix / seal tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.character.reference_mix import mix_reference_hair_eyes
from app.weave.compile.layers import compile_panel
from app.weave.schema import new_session_payload
from app.weave.state_machine import gates, next_cta
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle
from app.weave.verify.heuristics import evaluate_long_shot_framing, evaluate_sample_framing
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


def test_g1_requires_lint_pass():
    session = new_session_payload(topic="雨")
    session["character"]["identity_tags"] = ["1girl"]
    session["character"]["identity_locked"] = True
    bundle = _ok_bundle()
    # break lint
    bundle["world"]["causality_one_liner"] = ""
    lint = lint_story_bundle(bundle, session["character"])
    assert not lint["pass"]
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    g = gates(session)
    assert g["G1"]["pass"] is False
    cta = next_cta(session)
    assert cta["code"] == "recreate_story"
    assert cta["defects"]


def test_g1_pass_allows_lookdev_cta():
    session = new_session_payload(topic="雨")
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    bundle = _ok_bundle()
    lint = lint_story_bundle(bundle, session["character"])
    assert lint["pass"], lint["defects"]
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    g = gates(session)
    assert g["G1"]["pass"]
    assert next_cta(session)["code"] == "enter_lookdev"


def test_framing_heuristics_fail_close_long_shot():
    assert evaluate_long_shot_framing(["close-up", "portrait", "1girl"]) == "fail"
    assert evaluate_long_shot_framing(["full_body", "scenery", "outdoors"]) == "pass"
    assert evaluate_long_shot_framing([]) == "unknown"
    assert evaluate_sample_framing("medium_shot", ["close-up"]) == "pass"


def test_reference_mix_overrides_hair_eyes():
    identity, added = mix_reference_hair_eyes(
        ["1girl", "blonde_hair", "blue_eyes", "cardigan"],
        ["1girl", "brown_hair", "green_eyes", "smile", "bookstore"],
    )
    assert "brown_hair" in identity
    assert "green_eyes" in identity
    assert "blonde_hair" not in identity
    assert "blue_eyes" not in identity
    assert "cardigan" in identity
    assert set(added) >= {"brown_hair", "green_eyes"}


def test_do_not_goes_to_negative():
    session = new_session_payload()
    session["character"]["identity_tags"] = ["1girl"]
    session["character"]["do_not"] = ["blood", "weapon"]
    session["panels"][0]["intent"]["camera"] = "medium_shot"
    out = compile_panel(session, "panel_1")
    assert "blood" in out["negative"]
    assert "weapon" in out["negative"]


def test_seal_rubric_core():
    session = new_session_payload(topic="雨")
    session["character"]["identity_tags"] = ["1girl"]
    session["character"]["identity_locked"] = True
    session["character"]["board"] = {
        "accepted": True,
        "images": [
            {"slot": "portrait", "image_id": "a" * 64},
            {"slot": "full", "image_id": "b" * 64},
        ],
    }
    bundle = _ok_bundle()
    lint = lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    for p in session["panels"]:
        if p["intent"]["camera"] == "long_shot":
            p.setdefault("qa", {})["framing"] = "pass"
        p["sample"] = {"image_id": "s" * 64}
        p["final"] = {"image_id": ("f" + p["key"][-1]) * 64}
    rubric = evaluate_seal_rubric(session)
    assert rubric["checks"]["has_finals"]
    assert rubric["core_pass"]
    assert rubric["pass"]

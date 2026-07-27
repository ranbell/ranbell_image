"""P4 polish: causality lint, framing density, structured hints, story prompt."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from PIL import Image, ImageDraw

from app.weave.character.personalitywright import build_personality_prompt
from app.weave.story.storywright import build_story_prompt
from app.weave.validate.causality import causality_report, lint_causality
from app.weave.validate.story_lint import lint_story_bundle
from app.weave.verify.heuristics import (
    center_edge_density,
    evaluate_long_shot_framing,
    face_tag_density,
)


def test_causality_lint_catches_weak_and_dup():
    bundle = {
        "world": {"causality_one_liner": "短い"},
        "panels": [
            {"key": "panel_1", "visible_change": "同じ"},
            {"key": "panel_2", "visible_change": "同じ"},
            {"key": "panel_3", "visible_change": ""},
        ],
    }
    defects = lint_causality(bundle)
    codes = {d["code"] for d in defects}
    assert "CAUSALITY_TOO_SHORT" in codes
    assert "VISIBLE_CHANGE_DUP" in codes
    assert "VISIBLE_CHANGE_EMPTY" in codes
    assert causality_report(bundle)["ok"] is False


def test_causality_ok_chain():
    bundle = {
        "world": {
            "causality_one_liner": "しおりを見つけ、雨で濡れ、色がにじむ",
        },
        "panels": [
            {"key": "panel_1", "visible_change": "しおりを手に取る", "time_marker": "afternoon"},
            {"key": "panel_2", "visible_change": "棚が濡れる", "time_marker": "dusk"},
            {"key": "panel_3", "visible_change": "色がにじむ", "time_marker": "night"},
        ],
    }
    assert lint_causality(bundle) == []
    assert causality_report(bundle)["ok"] is True


def test_story_lint_includes_causality():
    character = {
        "identity_tags": ["1girl"],
        "signature_prop": "cloth_bookmark",
        "prop_tags": ["cloth_bookmark"],
    }
    bundle = {
        "world": {
            "setting": "bookstore",
            "core_conflict": "c",
            "ending_intent": "hope",
            "throughline_place": "counter",
            "throughline_prop": "cloth_bookmark",
            "causality_one_liner": "短い",
        },
        "panels": [
            {
                "key": "panel_1",
                "camera": "long_shot",
                "time_marker": "afternoon",
                "visible_change": "a",
                "must_show": ["throughline_prop", "throughline_place"],
                "narrative_ja": "n1",
            },
            {
                "key": "panel_2",
                "camera": "medium_shot",
                "time_marker": "dusk",
                "visible_change": "b",
                "must_show": ["throughline_prop", "throughline_place"],
                "narrative_ja": "n2",
            },
            {
                "key": "panel_3",
                "camera": "close_up",
                "time_marker": "night",
                "visible_change": "c",
                "must_show": ["throughline_prop", "throughline_place"],
                "narrative_ja": "n3",
            },
        ],
    }
    lint = lint_story_bundle(bundle, character)
    assert lint["pass"] is False
    assert any(d["code"] == "CAUSALITY_TOO_SHORT" for d in lint["defects"])


def test_face_tag_density_and_framing():
    assert face_tag_density(["close-up", "portrait", "face"]) >= 0.9
    assert evaluate_long_shot_framing(["close-up", "portrait"]) == "fail"
    assert evaluate_long_shot_framing(["full_body", "scenery", "outdoors"]) == "pass"
    # High face share without wide → fail via density rule
    assert evaluate_long_shot_framing(
        ["face", "detailed_face", "looking_at_viewer", "solo_focus"],
    ) == "fail"


def test_center_edge_density_hot_center():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "hot.png"
        img = Image.new("RGB", (128, 128), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        # Busy center (many lines) vs quiet border
        for i in range(20, 100, 3):
            draw.line([(40, i), (90, i)], fill=(255, 255, 255))
            draw.line([(i, 30), (i, 95)], fill=(200, 200, 200))
        img.save(path)
        d = center_edge_density(path)
        assert d is not None
        assert d > 0.2


def test_personality_prompt_includes_structured_hints():
    prompt = build_personality_prompt(
        personality_text="慎重な店員",
        topic="雨の書店",
        age_band="20代",
        gender_hint="女性",
        occupation_hint="書店員",
    )
    assert "20代" in prompt
    assert "女性" in prompt
    assert "書店員" in prompt


def test_story_prompt_includes_identity_tags():
    prompt = build_story_prompt(
        topic="雨",
        character={
            "personality": {"summary_ja": "店員"},
            "identity_tags": ["1girl", "brown_hair", "cardigan"],
            "signature_prop": "cloth_bookmark",
            "prop_tags": ["cloth_bookmark"],
        },
        author_style="静か",
    )
    assert "brown_hair" in prompt
    assert "identity_tags" in prompt
    assert "Do NOT invent appearance" in prompt or "continuity" in prompt.lower() or "HARD RULE" in prompt

"""Unit tests for Stage1 failure handling and Stage2 R0 locks."""
from __future__ import annotations

from app.story.stage1_storyboard import (
    apply_stage1_failure_handling,
    character_profile_from_tags,
    parse_stage1_json,
)
from app.story.stage2_enhance import enforce_r0_locks
from app.story.prompt_assets import stage1_system_prompt, fill_stage2


def test_stage1_system_prompt_loads():
    text = stage1_system_prompt()
    assert "HARD RULES" in text
    assert "looking at viewer" in text


def test_stage2_fill_input():
    out = fill_stage2('{"camera":"close_up"}')
    assert "close_up" in out
    assert "<<INPUT>>" not in out


def test_parse_and_failure_handling():
    raw = """
    {
      "title": "テスト",
      "core_conflict": "衝突",
      "structure_type": "omen_event_afterglow",
      "include_happening": false,
      "happening_summary": "",
      "happening_category": "該当なし",
      "consistency_tags": ["wrong_hair"],
      "panels": [
        {"camera": "close_up", "danbooru_tags": ["smile", "desk"], "narrative_ja": "a"},
        {"camera": "close_up", "danbooru_tags": ["looking_at_viewer"], "narrative_ja": "b"},
        {"camera": "close_up", "danbooru_tags": ["rain"], "narrative_ja": "c"}
      ]
    }
    """
    data = parse_stage1_json(raw)
    assert data is not None
    profile = {
        "hair_color": "brown_hair",
        "hairstyle": "long_hair",
        "eye_color": "green_eyes",
        "base_outfit": "school_uniform",
    }
    fixed = apply_stage1_failure_handling(
        data,
        character_profile=profile,
        custom_tags={"panel_2": ["rain"], "panel_1": [], "panel_3": []},
        include_happening=False,
    )
    cams = [p["camera"] for p in fixed["panels"]]
    assert len(set(cams)) == 3
    assert "brown_hair" in fixed["consistency_tags"]
    assert "smile" not in fixed["panels"][0]["danbooru_tags"]
    assert "rain" in fixed["panels"][1]["danbooru_tags"]


def test_r0_enforces_missing_consistency():
    text = enforce_r0_locks(
        "anime illustration of a girl at a desk",
        consistency_tags=["brown_hair", "green_eyes"],
        custom_tags=["rain"],
        camera="close_up",
        character_state_diff="",
    )
    assert "brown_hair" in text
    assert "green_eyes" in text
    assert "rain" in text
    assert "close_up" in text or "close-up" in text


def test_character_profile_from_user_tags():
    p = character_profile_from_tags(
        "blue_hair, long_hair, red_eyes, school_uniform",
        [],
    )
    assert "blue" in p["hair_color"] or "blue_hair" in p["hair_color"]
    assert "red" in p["eye_color"] or "red_eyes" in p["eye_color"]


def test_chronicle_request_has_no_base_time_axis():
    from app.story.api import ChronicleRequest
    fields = ChronicleRequest.model_fields
    assert "base_time_axis" not in fields
    assert "include_happening" in fields
    assert "panel_1" in __import__("app.story.db", fromlist=["AXES"]).AXES

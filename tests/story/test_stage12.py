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
    assert "UNEXPECTEDNESS RULES" in text
    assert "SINGLE-FRAME RENDERABILITY" in text
    assert "time_scale" in text
    assert "panel_time_labels" in text


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
    assert text.startswith("brown_hair, green_eyes, rain, close_up")
    assert "anime illustration" in text


def test_r0_always_prepends_even_when_present():
    text = enforce_r0_locks(
        "brown_hair, green_eyes, close_up, anime girl at a desk",
        consistency_tags=["brown_hair", "green_eyes"],
        custom_tags=[],
        camera="close_up",
        character_state_diff="",
    )
    # Lock line is always normalized to the front (duplicates allowed).
    assert text.startswith("brown_hair, green_eyes, close_up,")


def test_r0_skips_non_ascii_state_diff():
    text = enforce_r0_locks(
        "anime illustration",
        consistency_tags=["brown_hair"],
        custom_tags=[],
        camera="medium_shot",
        character_state_diff="目が潤んでいる",
    )
    assert "目が潤んでいる" not in text
    assert text.startswith("brown_hair, medium_shot")


def test_r0_english_state_diff_and_gesture():
    text = enforce_r0_locks(
        "anime illustration of a girl",
        consistency_tags=["brown_hair"],
        custom_tags=[],
        camera="long_shot",
        character_state_diff="teary_eyes",
        gesture="arms stretched overhead",
    )
    assert text.startswith("brown_hair, long_shot, teary_eyes")
    assert "arms stretched overhead" in text


def test_r0_skips_japanese_gesture():
    text = enforce_r0_locks(
        "anime illustration",
        consistency_tags=["brown_hair"],
        custom_tags=[],
        camera="close_up",
        character_state_diff="",
        gesture="背伸び",
    )
    assert "背伸び" not in text


def test_character_profile_from_user_tags():
    p = character_profile_from_tags(
        "blue_hair, long_hair, red_eyes, school_uniform",
        [],
    )
    assert "blue" in p["hair_color"] or "blue_hair" in p["hair_color"]
    assert "red" in p["eye_color"] or "red_eyes" in p["eye_color"]


def test_chronicle_request_has_no_base_time_axis():
    import ast
    from pathlib import Path

    from app.story.stage1_storyboard import PANELS

    src = Path(__file__).resolve().parents[2] / "backend" / "app" / "story" / "api.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fields: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ChronicleRequest":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
            break
    assert "base_time_axis" not in fields
    assert "include_happening" in fields
    assert "time_scale" in fields
    assert "vlm_model" in fields
    assert "num_ctx" in fields
    assert PANELS == ("panel_1", "panel_2", "panel_3")


def test_panel_time_labels_hours_ja():
    from app.story.stage1_storyboard import panel_time_labels, build_stage1_user_input

    labels = panel_time_labels("hours", locale="ja")
    assert labels["panel_1"] == "スタート"
    assert labels["panel_2"] == "数時間後"
    assert "半日" in labels["panel_3"]

    labels_en = panel_time_labels("hours", locale="en")
    assert labels_en["panel_1"] == "Start"
    assert "later" in labels_en["panel_2"].lower()

    ui = build_stage1_user_input(
        theme="雨",
        character_profile={
            "hair_color": "black",
            "hairstyle": "long_hair",
            "eye_color": "brown_eyes",
            "base_outfit": "school_uniform",
        },
        time_scale="hours",
        locale="ja",
    )
    assert ui["time_scale"] == "hours"
    assert ui["panel_time_labels"]["panel_1"] == "スタート"


def test_stage1_fewshots_block_has_examples_only():
    from app.story.prompt_assets import stage1_fewshots_block, clear_prompt_cache

    clear_prompt_cache()
    block = stage1_fewshots_block()
    assert "FEW-SHOT EXAMPLE" in block
    assert "FEW-SHOT EXAMPLE 2" in block
    assert "FEW-SHOT EXAMPLE 3" in block
    assert "FAILURE HANDLING" not in block
    assert "INTEGRATION NOTES" not in block
    assert "エージェント側の実装ロジック" not in block

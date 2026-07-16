"""Tests for the single-call story-arc core (prompt / parser / validator)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (  # noqa: E402
    arc_feedback_block,
    arc_needs_retry,
    build_acts_polish_prompt,
    build_story_arc_prompt,
    candidate_acts,
    default_act_labels,
    deterministic_shot_plan,
    parse_acts_polish_json,
    parse_story_arc_json,
    repair_act_labels,
    select_best_candidates,
    synthesize_acts_from_flat,
    validate_story_arc,
)


def _arc_json(acts_past="She waits under an umbrella on the station platform",
              acts_present="She spots her friend across the rainy station square",
              acts_future="She runs to her friend, waving, umbrella tilting",
              label_past="2 hours earlier", label_future="1 hour later",
              cid="A") -> dict:
    return {
        "id": cid, "title": "Rain Check", "dramatic_mode": "reunion",
        "motif": "clear vinyl umbrella", "turn": "The friend arrives early.",
        "personality_hint": "Patient; hums while waiting.",
        "acts": {
            "past": {"label": label_past, "activity": acts_past,
                     "place": "station platform", "feeling": "restless",
                     "motif_use": "umbrella closed at her side"},
            "present": {"label": "now", "activity": acts_present,
                        "place": "station square in the rain", "feeling": "hopeful",
                        "motif_use": "umbrella open overhead"},
            "future": {"label": label_future, "activity": acts_future,
                       "place": "station square", "feeling": "joyful",
                       "motif_use": "umbrella swinging in her hand"},
        },
    }


# ── prompt ────────────────────────────────────────────────────────────────────

def test_arc_prompt_contains_contract_topic_modes():
    p = build_story_arc_prompt(
        character_desc="1girl, brown_hair",
        scene_desc="a rainy station square",
        user_topic="雨の駅で待ち合わせ",
        base_axis="present",
        time_scale="hours",
        candidate_modes={"A": "reunion", "B": "irony", "C": "discovery"},
        locale="ja",
    )
    assert "time_scale=hours" in p
    assert "雨の駅で待ち合わせ" in p
    assert "mode:reunion" in p and "mode:irony" in p
    assert "TIME AXIS" in p or "時間軸" in p
    assert "personality_hint" in p
    assert '"acts"' in p  # few-shot skeleton present


def test_arc_prompt_feedback_block_included_on_retry():
    fb = arc_feedback_block(
        [{"candidate_id": "B", "code": "off_topic",
          "detail": "topic anchors missing: 駅"}]
    )
    p = build_story_arc_prompt(
        character_desc="1girl", scene_desc="scene", feedback=fb,
    )
    assert "Candidate B" in p and "off_topic" in p


# ── parser ────────────────────────────────────────────────────────────────────

def test_parse_arc_full_shape_and_flat_superset():
    raw = json.dumps({"candidates": [_arc_json()]})
    got = parse_story_arc_json(raw)
    assert len(got) == 1
    c = got[0]
    assert c["acts"]["past"]["activity"].startswith("She waits")
    assert c["acts"]["future"]["feeling"] == "joyful"
    assert c["personality_hint"]
    # flat legacy fields synthesized for candidate cards / stored drafts
    assert "She waits" in c["past"] and "station platform" in c["past"]
    assert c["summary"]


def test_parse_arc_fenced_json():
    raw = "```json\n" + json.dumps({"candidates": [_arc_json()]}) + "\n```"
    got = parse_story_arc_json(raw)
    assert got and got[0]["acts"]["present"]["activity"]


def test_parse_arc_legacy_flat_candidate():
    raw = json.dumps({"candidates": [{
        "id": "A", "title": "Old", "motif": "cup", "turn": "t",
        "past": "she waits", "present": "she meets", "future": "she leaves",
    }]})
    got = parse_story_arc_json(raw)
    acts = candidate_acts(got[0])
    assert acts["past"]["activity"] == "she waits"
    assert acts["future"]["activity"] == "she leaves"


def test_synthesize_acts_from_flat():
    acts = synthesize_acts_from_flat({"past": "a", "present": "b", "future": ""})
    assert acts["present"]["activity"] == "b"
    assert acts["future"]["activity"] == ""


# ── validator ─────────────────────────────────────────────────────────────────

def test_validate_ja_topic_bridges_to_en_acts():
    # お題 in JA, acts in EN — alias bridge (駅→station, 雨→rain) must pass
    cands = parse_story_arc_json(json.dumps({"candidates": [_arc_json()]}))
    problems = validate_story_arc(
        cands, user_topic="雨の駅で待ち合わせ", time_scale="hours",
        base_axis="present",
    )
    assert not [p for p in problems if p["code"] == "off_topic"]


def test_validate_off_topic_detected():
    off = _arc_json(
        acts_past="She bakes bread in the kitchen",
        acts_present="She kneads dough at the counter",
        acts_future="She pulls a loaf from the oven",
    )
    off["title"] = "Bread"
    off["motif"] = "flour sack"
    off["turn"] = "The loaf burns."
    for a in off["acts"].values():
        a["place"] = "bakery kitchen"
    cands = parse_story_arc_json(json.dumps({"candidates": [off]}))
    problems = validate_story_arc(
        cands, user_topic="雨の駅で待ち合わせ", time_scale="hours",
        base_axis="present",
    )
    assert any(p["code"] == "off_topic" for p in problems)


def test_validate_time_collapse_at_hours_but_ok_at_minutes():
    same = _arc_json(
        acts_past="She waits under an umbrella on the station platform",
        acts_present="She waits under an umbrella on the station platform",
        acts_future="She waits under an umbrella on the station platforms",
    )
    cands = parse_story_arc_json(json.dumps({"candidates": [same]}))
    at_hours = validate_story_arc(cands, time_scale="hours", base_axis="present")
    assert any(p["code"] == "time_collapse" for p in at_hours)
    at_minutes = validate_story_arc(cands, time_scale="minutes", base_axis="present")
    assert not any(p["code"] == "time_collapse" for p in at_minutes)


def test_validate_bad_scale_labels():
    bad = _arc_json(label_past="3 years earlier", label_future="a decade later")
    cands = parse_story_arc_json(json.dumps({"candidates": [bad]}))
    problems = validate_story_arc(cands, time_scale="hours", base_axis="present")
    codes = [p["code"] for p in problems]
    assert "bad_scale_labels" in codes
    # labels alone never trigger the LLM retry
    assert not arc_needs_retry([p for p in problems if p["code"] == "bad_scale_labels"])


def test_validate_structure_missing_act():
    broken = _arc_json()
    broken["acts"]["future"]["activity"] = ""
    broken["future"] = ""
    cands = parse_story_arc_json(json.dumps({"candidates": [broken]}))
    problems = validate_story_arc(cands, time_scale="hours", base_axis="present")
    assert any(p["code"] == "structure" for p in problems)
    assert arc_needs_retry(problems)


def test_repair_act_labels_overwrites_only_bad():
    bad = _arc_json(label_past="3 years earlier")
    cands = parse_story_arc_json(json.dumps({"candidates": [bad]}))
    repair_act_labels(cands[0], base_axis="present", time_scale="hours")
    acts = cands[0]["acts"]
    assert "year" not in acts["past"]["label"]
    assert acts["future"]["label"] == "1 hour later"  # good label kept


def test_default_act_labels_ja():
    labels = default_act_labels("present", "hours", locale="ja")
    assert labels["present"] == "いま"
    assert labels["past"].endswith("前")
    assert labels["future"].endswith("後")


def test_select_best_candidates_prefers_clean():
    a, b = _arc_json(cid="A"), _arc_json(cid="B")
    cands = parse_story_arc_json(json.dumps({"candidates": [a, b]}))
    problems = [{"candidate_id": "A", "code": "off_topic", "detail": "x"}]
    best = select_best_candidates(cands, problems, n=1)
    assert best[0]["id"] == "B"


# ── polish + shot plan ────────────────────────────────────────────────────────

def test_polish_prompt_authoritative_and_parser():
    acts = candidate_acts(
        parse_story_arc_json(json.dumps({"candidates": [_arc_json()]}))[0]
    )
    p = build_acts_polish_prompt(
        title="Rain Check", acts=acts,
        tag_lines={"past": "holding_umbrella, standing",
                   "present": "waving", "future": "running"},
        identity_tags=["brown_hair", "blue_eyes"],
    )
    assert "at most 60 words" in p
    assert "Rephrase, never invent" in p
    assert "holding_umbrella" in p and "brown_hair" in p
    out = parse_acts_polish_json(
        '{"past": "p", "present": "n", "future": "f"}'
    )
    assert out == {"past": "p", "present": "n", "future": "f"}


def test_deterministic_shot_plan_rotates_and_reacts_to_feeling():
    past = deterministic_shot_plan("past")
    future = deterministic_shot_plan("future")
    assert past["shot"] != future["shot"]
    lonely = deterministic_shot_plan("present", feeling="lonely")
    assert lonely.get("camera_angle") == "from_behind"

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
    # Deterministic script scaffold with scale-derived labels.
    assert "SCRIPT — TIME AXIS" in p
    assert "a few hours" in p  # hours-scale label composed from _ELAPSED_UNIT
    assert "雨の駅で待ち合わせ" in p
    assert "mode:reunion" in p and "mode:irony" in p
    assert "personality_hint" in p
    assert '"acts"' in p  # few-shot skeleton present
    assert "EXACTLY 3 items" in p
    # Format hazards measured on bonsai: no key=value checklist, no old rules.
    assert "PRIORITY (do in order" not in p
    assert "must match the base image's scene" not in p
    assert "time_delta:" not in p


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


# ── script scaffold / base act grounding ─────────────────────────────────────

from app.story.generator import (  # noqa: E402
    base_act_from_image,
    build_script_scaffold,
    enforce_base_act,
    ensure_face_tags,
    expression_tag_for_feeling,
)
from app.tags.catalog import EXPRESSION_TAGS, scene_vocab_subset  # noqa: E402

_WD14 = ["1girl", "solo", "brown_hair", "blue_eyes", "long_hair",
         "school_uniform", "sitting", "holding_book", "reading", "smile",
         "park", "day", "outdoors", "tree"]
_SCENE = ("A girl sits on a wooden park bench beneath a large tree, reading "
          "a paperback book. Dappled afternoon sunlight falls across the path.")


def test_base_act_from_image_from_wd14():
    act = base_act_from_image(_WD14, _SCENE)
    assert "sitting" in act["activity"] and "reading" in act["activity"]
    assert "park" in act["place"]
    assert act["feeling"] == "warm"  # smile → warm


def test_base_act_from_image_scene_fallback():
    act = base_act_from_image(["1girl", "blue_eyes"], _SCENE, emotion="serenity")
    assert len(act["activity"].split()) <= 15
    assert act["activity"].startswith("A girl sits")
    assert act["feeling"] == "serenity"
    empty = base_act_from_image([], "Short scene.", emotion="")
    assert empty["feeling"] == "calm"


def test_build_script_scaffold_fixed_and_blanks():
    fixed = {"activity": "sitting, holding book", "place": "park, day",
             "feeling": "warm"}
    s = build_script_scaffold(base_axis="present", time_scale="hours",
                              base_act_fixed=fixed)
    assert "SCRIPT — TIME AXIS" in s
    assert "THIS IS THE BASE IMAGE. FIXED" in s
    assert 'activity = "sitting, holding book"' in s
    assert s.count("= ____") == 6  # 2 blank acts × 3 slots
    assert 'label="a few hours earlier"' in s
    assert 'label="now"' in s
    # scene delta from the existing table
    from app.story.generator import _scale_delta_line
    assert _scale_delta_line("hours") in s


def test_build_script_scaffold_topic_only_all_blanks():
    s = build_script_scaffold(base_axis="present", time_scale="years",
                              base_act_fixed=None)
    assert "FIXED]" not in s
    assert s.count("= ____") == 9
    assert "t=0 base act" in s


def test_arc_prompt_carries_fixed_base_act():
    fixed = {"activity": "sitting, holding book", "place": "park, day",
             "feeling": "warm"}
    p = build_story_arc_prompt(
        character_desc="1girl, brown_hair", scene_desc=_SCENE,
        base_axis="present", time_scale="hours", base_act_fixed=fixed,
    )
    assert 'activity = "sitting, holding book"' in p
    assert "BASE IMAGE scene (context):" in p
    assert "natural ENGLISH" in p


def test_enforce_base_act_overwrites_and_rebuilds_flat():
    cands = parse_story_arc_json(json.dumps({"candidates": [_arc_json()]}))
    fixed = {"activity": "sitting, holding book", "place": "park, day",
             "feeling": "warm"}
    enforce_base_act(cands[0], base_axis="present", base_act_fixed=fixed)
    act = cands[0]["acts"]["present"]
    assert act["activity"] == "sitting, holding book"
    assert act["place"] == "park, day"
    assert act["feeling"] == "warm"
    assert act["label"] == "now"  # label untouched
    assert cands[0]["present"] == "sitting, holding book (park, day)"


def test_enforce_base_act_noop_without_fixed():
    cands = parse_story_arc_json(json.dumps({"candidates": [_arc_json()]}))
    before = dict(cands[0]["acts"]["present"])
    enforce_base_act(cands[0], base_axis="present", base_act_fixed=None)
    assert cands[0]["acts"]["present"] == before


# ── validator: candidate count + CJK anchor groups ───────────────────────────

def test_validate_requires_three_candidates():
    cands = parse_story_arc_json(json.dumps({"candidates": [_arc_json()]}))
    problems = validate_story_arc(cands, time_scale="hours", base_axis="present")
    assert any(p["code"] == "structure" and "expected 3" in p["detail"]
               for p in problems)
    assert arc_needs_retry(problems)


def test_validate_skips_all_cjk_anchor_groups():
    # EN-authored acts vs a ja topic whose tokens have no EN aliases: the
    # all-CJK groups must not fail the gate.
    cands = parse_story_arc_json(json.dumps({"candidates": [
        _arc_json(cid="A"), _arc_json(cid="B"), _arc_json(cid="C"),
    ]}))
    problems = validate_story_arc(
        cands, user_topic="謎の骨董品店で古い万華鏡を磨く",
        time_scale="hours", base_axis="present",
    )
    assert not any(p["code"] == "off_topic" for p in problems)


def test_validate_ja_topic_with_alias_still_enforced():
    # 交換日記 now has EN aliases → EN acts about a notebook pass...
    diary = _arc_json()
    diary["acts"]["past"]["activity"] = "She writes a page in the exchange diary"
    diary["title"] = "The Notebook"
    cands = parse_story_arc_json(json.dumps({"candidates": [diary] * 3}))
    problems = validate_story_arc(
        cands, user_topic="交換日記", time_scale="hours", base_axis="present",
    )
    assert not any(p["code"] == "off_topic" for p in problems)
    # ...and unrelated EN acts fail.
    plain = parse_story_arc_json(json.dumps({"candidates": [
        _arc_json(cid="A"), _arc_json(cid="B"), _arc_json(cid="C"),
    ]}))
    problems2 = validate_story_arc(
        plain, user_topic="交換日記", time_scale="hours", base_axis="present",
    )
    assert any(p["code"] == "off_topic" for p in problems2)


# ── face guarantee ────────────────────────────────────────────────────────────

def test_expression_tag_for_feeling_always_valid():
    from app.story.generator import (
        _EMOTION_EXPRESSION_MAP, _FEELING_EXPRESSION_MAP, _EMOTION_REGISTER,
    )
    for v in _FEELING_EXPRESSION_MAP.values():
        assert v in EXPRESSION_TAGS, v
    for v in _EMOTION_EXPRESSION_MAP.values():
        assert v in EXPRESSION_TAGS, v
    for key in _EMOTION_REGISTER:
        assert expression_tag_for_feeling("", emotion=key) in EXPRESSION_TAGS
    assert expression_tag_for_feeling("joyful") == "joyful"  # already a member
    assert expression_tag_for_feeling("hopeful") == "smile"  # mapped
    assert expression_tag_for_feeling("lonely") == "lonely"
    assert expression_tag_for_feeling("悲しい") == "sad"
    assert expression_tag_for_feeling("completely-unknown-word") == "smile"
    assert expression_tag_for_feeling("crying") == "crying"  # already valid


def test_ensure_face_tags_reinjects_after_cap():
    head = ", ".join(["1girl"] + [f"tag_{i}" for i in range(19)])
    positive = head + "\n\nShe reads quietly."
    out = ensure_face_tags(
        positive, expression_tag="smile", lock_tags=["blue_eyes", "brown_hair"],
        priority_tags=["1girl"],
    )
    head_out = out.split("\n\n")[0]
    parts = [t.strip() for t in head_out.split(",")]
    assert "blue_eyes" in parts and "smile" in parts
    assert len(parts) <= 20
    assert out.endswith("She reads quietly.")


def test_ensure_face_tags_prose_only_passthrough():
    prose = "She reads a book. The light is warm."
    assert ensure_face_tags(
        prose, expression_tag="smile", lock_tags=["blue_eyes"],
    ) == prose


def test_ensure_face_tags_noop_when_present():
    positive = "1girl, blue_eyes, smile, reading"
    assert ensure_face_tags(
        positive, expression_tag="smile", lock_tags=["blue_eyes"],
    ) == positive


# ── scene vocab subset ────────────────────────────────────────────────────────

def test_scene_vocab_subset_membership():
    names = ["classroom", "park", "dusk", "rain", "sunlight", "witch_hat",
             "maid_headdress", "school_uniform", "1girl", "night", "indoors"]
    got = set(scene_vocab_subset(names))
    assert {"classroom", "park", "dusk", "rain", "night", "indoors"} <= got
    assert "witch_hat" not in got and "maid_headdress" not in got
    assert "school_uniform" not in got and "1girl" not in got

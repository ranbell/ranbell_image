"""Lab extras: Spicer, mood board slot, multi-seed."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.character.board_slots import resolve_board_slots, set_mood_slot, sync_board_briefs
from app.weave.character.spicer import harvest_spice_tags, is_spicer_enabled, run_spicer, set_spicer_enabled
from app.weave.compile.layers import compile_panel
from app.weave.render.prompts import compile_board_slot
from app.weave.render.submit import _multi_seed_count, submit_sample_job
from app.weave.schema import new_session_payload
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle


def _session_rainy():
    session = new_session_payload(topic="雨の日の小さな書店")
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["palette"] = ["warm cream"]
    session["character"]["identity_locked"] = True
    bundle = normalize_story_bundle({
        "title": "雨",
        "world": {
            "setting": "rainy bookstore",
            "core_conflict": "c",
            "ending_intent": "e",
            "throughline_place": "counter",
            "throughline_prop": "cloth_bookmark",
            "time_scale": "hours",
            "causality_one_liner": "a then b then c",
        },
        "panels": [
            {
                "key": "panel_1",
                "narrative_ja": "n1",
                "visible_change": "v1",
                "camera": "long_shot",
                "time_marker": "afternoon",
                "gesture": "standing",
                "emotion": "calm",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_2",
                "narrative_ja": "n2",
                "visible_change": "v2",
                "camera": "medium_shot",
                "time_marker": "dusk",
                "gesture": "holding",
                "emotion": "surprised",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_3",
                "narrative_ja": "n3",
                "visible_change": "v3",
                "camera": "close_up",
                "time_marker": "night",
                "gesture": "looking",
                "emotion": "soft smile",
                "must_show": ["throughline_prop", "throughline_place"],
            },
        ],
    })
    lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    for p in session["panels"]:
        src = next(x for x in bundle["panels"] if x["key"] == p["key"])
        p["intent"]["must_show_resolved"] = src.get("must_show_resolved") or [
            "cloth_bookmark", "counter",
        ]
    return session


def test_spicer_default_off():
    session = new_session_payload(topic="雨の書店")
    assert is_spicer_enabled(session) is False
    assert run_spicer(session) == []
    assert session["character"]["lab_spice"] == []


def test_spicer_harvest_and_compile_spice_only():
    session = _session_rainy()
    set_spicer_enabled(session, True)
    tags = run_spicer(session)
    assert tags
    assert any("rain" in t or "overcast" in t or "cozy" in t for t in tags)
    # identity must not contain lab spice
    assert not set(tags) & set(session["character"]["identity_tags"])
    out = compile_panel(session, "panel_1")
    spice = out["layers"]["spice"]
    for t in tags:
        assert t in spice
    for t in tags:
        assert t not in out["layers"]["identity"]


def test_spicer_off_clears_and_excludes_from_compile():
    session = _session_rainy()
    set_spicer_enabled(session, True)
    tags = run_spicer(session)
    assert tags
    set_spicer_enabled(session, False)
    run_spicer(session)
    assert session["character"]["lab_spice"] == []
    out = compile_panel(session, "panel_1")
    for t in tags:
        assert t not in out["layers"]["spice"]


def test_mood_slot_sync_briefs():
    session = new_session_payload()
    # The character sheet ships on by default.
    assert resolve_board_slots(session) == ["portrait", "full", "prop", "mood"]
    slots = set_mood_slot(session, True)
    assert "mood" in slots
    briefs = session["character"]["board_briefs"]
    assert [b["slot"] for b in briefs] == ["portrait", "full", "prop", "mood"]
    set_mood_slot(session, False)
    assert "mood" not in resolve_board_slots(session)
    assert all(b["slot"] != "mood" for b in session["character"]["board_briefs"])


def test_mood_board_is_a_multi_view_sheet():
    """Mood is one sheet of several views, not another single atmospheric shot."""
    session = _session_rainy()
    set_spicer_enabled(session, True)
    run_spicer(session)
    sync_board_briefs(session)
    set_mood_slot(session, True)
    compiled = compile_board_slot(session, "mood")
    pos = compiled["positive"]
    assert "multiple_views" in pos
    assert "** Chronicles of Character **" in pos
    assert len([l for l in pos.splitlines() if l.startswith(" - ")]) == 4
    assert compiled["camera"] == "long_shot"


def test_multi_seed_clamp():
    session = new_session_payload()
    session["quality_policy"]["multi_seed"] = 99
    assert _multi_seed_count(session) == 3
    session["quality_policy"]["multi_seed"] = 0
    assert _multi_seed_count(session) == 1
    session["quality_policy"]["multi_seed"] = 2
    assert _multi_seed_count(session) == 2


def test_submit_sample_multi_seed():
    session = _session_rainy()
    session["quality_policy"]["multi_seed"] = 3
    session["inputs"]["workflow_sample"] = "test_wf.json"
    # minimal compile so prompts work
    compile_panel(session, "panel_1")

    ids = iter(["j1", "j2", "j3"])
    spooler = MagicMock()
    spooler.submit = MagicMock(side_effect=lambda *a, **k: next(ids))
    app = SimpleNamespace(state=SimpleNamespace(spooler=spooler, db=None, comfy=None, ollama=None))

    out = submit_sample_job(app, "sid", session, "panel_1")
    assert out["multi_seed"] == 3
    assert len(out["jobs"]) == 3
    assert spooler.submit.call_count == 3
    panel = next(p for p in session["panels"] if p["key"] == "panel_1")
    assert panel["sample"]["job_id"] == "j1"
    assert len(panel["sample_history"]) == 3
    assert all(h.get("pending") for h in panel["sample_history"])


def test_harvest_generic_when_no_lexicon_hit():
    session = new_session_payload(topic="平凡な火曜日")
    tags = harvest_spice_tags(session)
    assert "atmospheric" in tags or "depth_of_field" in tags

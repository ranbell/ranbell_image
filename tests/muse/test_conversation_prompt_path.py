"""Conversation → final prompt path: boxes authority, atmosphere, VERIFY guard, pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity, notebook, pipeline_view, schema, service


def _solo_cast():
    return [{
        "name": "Mio",
        "identity_tags": ["silver_hair", "bob_cut", "blue_eyes"],
        "subject_tag": "1girl",
    }]


def _session_with_shot(**nb_patch):
    s = schema.new_session({
        "theme": "t", "character_id": "c", "workflow": "w.json", "model": "m",
    })
    s["mode"] = "duet"
    s["character"] = {
        "name": "Mio",
        "identity_tags": ["silver_hair", "bob_cut", "blue_eyes"],
        "subject_tag": "1girl",
    }
    s["notebook"] = notebook.blank()
    notebook.apply_patch(notebook.of(s), nb_patch or {
        "atmosphere": "quiet, still",
        "scene": "school library, afternoon",
        "bg": "tall bookshelves, dusty sunlight",
        "light": "backlit from the window",
        "wearing": "light blue dress",
        "beat": "standing, holding a book",
        "expression": "soft smile",
    })
    s["craft"] = {}
    s["inputs"]["framing"] = "auto"
    return s


def test_atmosphere_reaches_frame_wide():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "scene": "classroom",
        "bg": "chalkboard",
        "light": "soft window light",
        "atmosphere": "quiet, expectant",
    })
    wide = notebook.frame_wide_phrases(nb)
    assert any("quiet" in p for p in wide)
    assert any("chalkboard" in p for p in wide)
    assert any("window" in p.lower() or "soft" in p for p in wide)


def test_fight_craft_scene_drops_conflicting_prose():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "scene": "school library",
        "wearing": "light blue dress",
        "beat": "standing",
    })
    # Mostly invents a different place/outfit — should drop.
    bad = (
        "She lounges on a neon rooftop in a red leather jacket under strobe lights, "
        "crowds cheering, fireworks exploding over the harbor skyline."
    )
    assert notebook.fight_craft_scene(nb, bad) == ""
    # Aligned prose survives.
    good = "Standing in the school library in a light blue dress."
    assert "library" in notebook.fight_craft_scene(nb, good).lower()


def test_solo_assemble_uses_person_boxes():
    """箱が無いと書けない — solo も boxes 経路で最終を組む。"""
    out = identity.assemble_from_boxes(
        cast=_solo_cast(),
        people=notebook.mint_person_box(_session_with_shot()["notebook"]),
        frame_wide=notebook.frame_wide_phrases(_session_with_shot()["notebook"]),
        style="", framing="auto", scene="",
    )
    assert out
    assert "standing" in out.lower()
    assert "light blue dress" in out.lower() or "dress" in out.lower()
    assert "quiet" in out.lower() or "still" in out.lower()


def test_apply_compiled_craft_prefers_boxes_and_keeps_notebook_phrases():
    session = _session_with_shot()
    # Weave bag uses a garment alias; boxes must still carry notebook wearing.
    ok = service._apply_compiled_craft(
        session,
        "1girl, blue_dress, standing, library",
        "Crowds cheer on a neon rooftop in red leather under strobes and fireworks.",
    )
    assert ok
    prompt = str((session.get("craft") or {}).get("prompt") or "")
    assert prompt
    assert "people" in (session.get("craft") or {})
    # Notebook wearing phrase survives via boxes.
    assert "dress" in prompt.lower()
    # Conflicting craft_scene should not dominate (fight drops or boxes win).
    assert "neon rooftop" not in prompt.lower()
    assert "red leather" not in prompt.lower()


def test_reassemble_does_not_flatten_to_positive_only():
    session = _session_with_shot()
    assert service._apply_compiled_craft(
        session, "1girl, standing, dress", "In the library.",
    )
    before = str(session["craft"]["prompt"])
    service._reassemble(session)
    after = str(session["craft"]["prompt"])
    assert after
    assert "people" in session["craft"]
    # Still box-shaped (named dynamic line), not a pure flat bag collapse.
    assert "Mio:" in after or "standing" in after.lower()
    assert "dress" in after.lower()
    assert before  # sanity


def test_pipeline_view_on_public_view():
    session = _session_with_shot()
    session["scripter_intent"] = "shot"
    session["asked_fields"] = ["beat", "atmosphere"]
    session["craft_route"] = [
        {"hop": "1 weave（生）", "added": ["standing"], "dropped": []},
        {"hop": "2 scrub_craft_tags", "added": [], "dropped": ["socks"]},
        {"hop": "9 人ごとの箱", "sides": ("standing", "")},
    ]
    session["turn_trace"] = [{
        "at": 1, "line": "立って、静かな空気で",
        "asked": ["beat", "atmosphere"], "moved": {"beat": "∅ → standing"},
        "missed": [],
    }]
    service._apply_compiled_craft(
        session, "1girl, standing", "quiet library",
    )
    view = schema.public_view(session)
    pipe = view.get("pipeline") or {}
    assert pipe.get("schema") == pipeline_view.PIPELINE_SCHEMA
    ids = [s["id"] for s in pipe.get("stages") or []]
    assert ids == [
        "classify", "clerks", "notebook", "weave", "scrub",
        "boxes", "prompt", "board",
    ]
    assert "craft_route" in view
    assert "turn_trace" in view
    # Atmosphere in notebook should be detectable if missing from prompt.
    assert isinstance(pipe.get("divergences"), list)


def test_classify_fields_include_atmosphere():
    from app.muse import chain
    assert "atmosphere" in chain.CLASSIFY_FIELDS
    assert "bg" in chain.FIELD_CLERK_KINDS
    assert "light" in chain.FIELD_CLERK_KINDS
    assert "expression" in chain.FIELD_CLERK_KINDS
    assert "atmosphere" in chain._PER_PERSON


def test_verify_guard_filters_settled_fields_in_source():
    """確定欄を VERIFY が置換しないガードがソースにあること。"""
    import inspect
    src = inspect.getsource(service._run_duet_scripter)
    assert "settled_shot" in src
    assert "k not in settled_shot" in src or "not in settled_shot" in src


def test_joke_skip_still_gates_notebook_writers():
    """PR #30: skip_picture が画経路を塞いだままであること。"""
    import inspect
    src = inspect.getsource(service)
    assert "if not skip_picture" in src

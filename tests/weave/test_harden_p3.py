"""P3 harden: reinfer lock, wipe, budget, framing count, adopt, placeholder gate."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.weave.compile.budget import WEAVE_MAX_TAGS, cap_positive_tags
from app.weave.compile.layers import compile_panel
from app.weave.schema import new_session_payload
from app.weave.service import (
    WeaveError,
    adopt_sample,
    infer_character,
    lock_identity,
    mark_sample_placeholder,
    unlock_identity,
)
from app.weave.story.storywright import apply_story_to_session, normalize_story_bundle
from app.weave.validate.story_lint import lint_story_bundle
from app.weave.verify.heuristics import apply_framing_to_panel


def _ok_bundle():
    return normalize_story_bundle({
        "title": "t",
        "world": {
            "setting": "rainy bookstore",
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
                "narrative_ja": "n1",
                "visible_change": "v1",
                "camera": "long_shot",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_2",
                "narrative_ja": "n2",
                "visible_change": "v2",
                "camera": "medium_shot",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_3",
                "narrative_ja": "n3",
                "visible_change": "v3",
                "camera": "close_up",
                "must_show": ["throughline_prop", "throughline_place"],
            },
        ],
    })


def _session_with_story():
    session = new_session_payload(topic="雨の日の小さな書店", personality_text="店員")
    session["character"]["identity_tags"] = ["1girl", "brown_hair", "green_eyes"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    session["character"]["identity_locked"] = True
    bundle = _ok_bundle()
    lint_story_bundle(bundle, session["character"])
    apply_story_to_session(session, bundle)
    session["story_version"] = 1
    session["panels"][0]["sample"] = {"image_id": "old-sample", "job_id": "j0"}
    session["panels"][0]["sample_history"] = [
        {"job_id": "j0", "image_id": "old-sample", "pending": False},
        {"job_id": "j1", "image_id": "alt-sample", "pending": False},
    ]
    return session


def test_infer_rejects_when_locked():
    session = new_session_payload(personality_text="x")
    session["character"]["identity_locked"] = True

    class _Fake:
        async def chat_text(self, *a, **k):
            raise AssertionError("should not call LLM")

    with pytest.raises(WeaveError, match="locked"):
        asyncio.run(infer_character(session, _Fake(), model="m"))


def test_unlock_wipes_stale_samples():
    session = _session_with_story()
    unlock_identity(session, confirm=True)
    assert session["character"]["identity_locked"] is False
    assert session["story_version"] == 0
    assert session["story_bundle"] == {}
    for p in session["panels"]:
        assert not (p.get("sample") or {}).get("image_id")
        assert p.get("sample_history") == []
        assert (p.get("intent") or {}).get("narrative_ja") == ""


def test_apply_story_clears_sample_history():
    session = _session_with_story()
    assert session["panels"][0]["sample_history"]
    apply_story_to_session(session, _ok_bundle())
    assert session["panels"][0]["sample_history"] == []
    assert session["panels"][0]["sample"]["image_id"] is None


def test_tag_budget_keeps_identity_priority():
    tags = ["1girl"] + [f"filler_{i}" for i in range(40)] + ["brown_hair", "cloth_bookmark"]
    capped = cap_positive_tags(tags, priority=["1girl", "brown_hair", "cloth_bookmark"])
    assert len(capped) <= WEAVE_MAX_TAGS
    assert "1girl" in capped
    assert "brown_hair" in capped
    assert "cloth_bookmark" in capped


def test_compile_applies_budget():
    session = _session_with_story()
    session["character"]["identity_tags"] = ["1girl"] + [f"id_{i}" for i in range(30)]
    session["character"]["gallery_spice"] = [f"sp_{i}" for i in range(20)]
    session["quality_policy"]["spicer"] = False
    out = compile_panel(session, "panel_1")
    tag_part = out["positive"].split(". ")[0]  # before prose
    n = len([t for t in tag_part.split(",") if t.strip()])
    assert n <= WEAVE_MAX_TAGS + 2  # tiny slack if prose glued oddly


def test_sparse_boost_lexicon():
    session = _session_with_story()
    out = compile_panel(session, "panel_1", env_boost=True)
    env = out["layers"]["environment"]
    assert "bookshelf" in env or "rain" in env
    assert "lamp" in env or "crowd_silhouette" in env or "window_light" in env


def test_framing_fail_count_once_per_image():
    panel = {
        "intent": {"camera": "long_shot"},
        "sample": {"image_id": "img-a"},
        "qa": {},
        "framing_fail_count": 0,
    }
    tags = ["close-up", "portrait", "face"]
    apply_framing_to_panel(panel, tags, image_id="img-a")
    assert panel["framing_fail_count"] == 1
    apply_framing_to_panel(panel, tags, image_id="img-a")
    assert panel["framing_fail_count"] == 1  # re-eval same image
    panel["sample"]["image_id"] = "img-b"
    apply_framing_to_panel(panel, tags, image_id="img-b")
    assert panel["framing_fail_count"] == 2


def test_placeholder_gated():
    session = new_session_payload()
    session["panels"][0]["intent"]["camera"] = "long_shot"
    with pytest.raises(WeaveError, match="placeholder"):
        mark_sample_placeholder(session, "panel_1")
    session["quality_policy"]["mode"] = "lab"
    mark_sample_placeholder(session, "panel_1")
    assert session["panels"][0]["sample"]["image_id"].startswith("placeholder:")


def test_adopt_sample():
    session = _session_with_story()
    adopt_sample(session, panel_key="panel_1", image_id="alt-sample")
    assert session["panels"][0]["sample"]["image_id"] == "alt-sample"
    assert session["panels"][0]["sample"].get("adopted") is True

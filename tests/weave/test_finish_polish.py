"""Finishing polish: author_style gate, memory helpers, CTA needs."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.weave.memory import add_constraint, deactivate_constraints, log_rating
from app.weave.schema import new_session_payload
from app.weave.service import WeaveError, generate_story
from app.weave.state_machine import next_cta


def test_generate_story_requires_author_style():
    session = new_session_payload(topic="雨")
    session["character"]["identity_locked"] = True
    session["character"]["identity_tags"] = ["1girl"]
    session["inputs"]["author_style"] = ""

    class _Fake:
        async def chat_text(self, *a, **k):
            raise AssertionError("should not call LLM")

    with pytest.raises(WeaveError, match="author_style"):
        asyncio.run(generate_story(session, _Fake(), model="m", options={}))


def test_cta_needs_author_style():
    session = new_session_payload(topic="雨")
    session["character"]["identity_locked"] = True
    session["character"]["identity_tags"] = ["1girl"]
    session["inputs"]["author_style"] = ""
    cta = next_cta(session)
    assert cta["code"] == "generate_story"
    assert cta["enabled"] is False
    assert "author_style" in cta["needs"]


def test_memory_constraint_helpers():
    session = new_session_payload()
    add_constraint(session, id="c1", text="env_boost", scope="panel_1")
    assert len(session["constraints"]) == 1
    assert session["constraints"][0]["active"] is True
    n = deactivate_constraints(session, text="env_boost", scope="panel_1")
    assert n == 1
    assert session["constraints"][0]["active"] is False
    row = log_rating(session, panel_key="panel_1", chips=["good"])
    assert row["positive"] is True
    assert session["preference_log"][-1]["chips"] == ["good"]

"""Tests for similar-image WD14 tag harvest helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.similar_tags import (
    harvest_wd14_from_docs,
    sample_tags_by_ratio,
    situation_embed_query,
)


def test_sample_tags_by_ratio_thirty_percent():
    tags = [f"t{i}" for i in range(20)]
    got = sample_tags_by_ratio(tags, 0.3, budget=20)
    assert len(got) == 6
    assert got == tags[:6]


def test_sample_tags_by_ratio_off():
    assert sample_tags_by_ratio(["a", "b"], 0.0, budget=20) == []
    assert sample_tags_by_ratio([], 0.3, budget=20) == []


def test_harvest_wd14_from_docs_dedupes_and_excludes():
    docs = [
        {"wd14_tags": ["running", "beach", "blonde_hair"]},
        {"wd14_tags": ["running", "ocean", "sparkle"]},
    ]
    got = harvest_wd14_from_docs(docs, exclude={"blonde_hair"}, cap=10)
    assert got == ["running", "beach", "ocean", "sparkle"]


def test_situation_embed_query_joins_parts():
    q = situation_embed_query(
        situation="pouring espresso",
        gesture="both hands on portafilter",
        focal=["pouring", "steam"],
        user_topic="cafe morning",
        shot="close-up",
    )
    assert "pouring espresso" in q
    assert "steam" in q
    assert "cafe morning" in q

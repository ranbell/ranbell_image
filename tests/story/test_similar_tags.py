"""Tests for similar-image WD14 harvest helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.similar_tags import (
    assemble_with_similar_budget,
    exclude_near_fixed_tags,
    harvest_wd14_from_docs,
    pick_near_but_different,
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
        mood="warmth",
    )
    assert "pouring espresso" in q
    assert "steam" in q
    assert "warmth" in q


def test_exclude_near_fixed_drops_hair_eye_keeps_pose():
    fixed = ["blonde_hair", "blue_eyes"]
    cands = [
        "blonde_hair",
        "silver_hair",
        "brown_eyes",
        "reaching",
        "outstretched_arm",
        "cafe",
        "steam",
    ]
    got = exclude_near_fixed_tags(cands, fixed)
    assert "blonde_hair" not in got
    assert "silver_hair" not in got
    assert "brown_eyes" not in got
    assert "reaching" in got
    assert "outstretched_arm" in got
    assert "cafe" in got


def test_assemble_with_similar_budget_reserves_slots():
    lock = ["blonde_hair", "blue_eyes", "1girl", "solo"]
    focal = ["reaching"]
    similar = [
        "steam", "cafe", "counter", "cup", "apron", "evening",
        "rim_light", "window",
    ]
    # Fill lock to crowd the budget — similar must still get ~30% of 20 = 6
    pad_lock = lock + [f"acc_{i}" for i in range(12)]
    line, kept = assemble_with_similar_budget(
        lock_tags=pad_lock,
        focal=focal,
        similar_tags=similar,
        other_tags=["noise_a", "noise_b"],
        mix_ratio=0.3,
        budget=20,
    )
    parts = [t.strip() for t in line.split(",") if t.strip()]
    assert len(parts) <= 20
    assert len(kept) >= 1
    assert set(kept) <= set(similar)
    # At least some similar tags appear in the line
    assert any(t in parts for t in similar)


def test_pick_near_but_different_skips_too_close():
    docs = [
        ({"sha256": "a", "wd14_tags": ["cafe"]}, 0.95),
        ({"sha256": "b", "wd14_tags": ["steam", "cup"]}, 0.62),
        ({"sha256": "c", "wd14_tags": ["night", "lantern"]}, 0.55),
        ({"sha256": "d", "wd14_tags": ["beach"]}, 0.40),
        ({"sha256": "e", "wd14_tags": ["rooftop"]}, 0.58),
    ]
    picked = pick_near_but_different(docs, n=3, too_close=0.80)
    shas = {d["sha256"] for d in picked}
    assert "a" not in shas  # too close
    assert len(picked) <= 3
    assert len(picked) >= 1

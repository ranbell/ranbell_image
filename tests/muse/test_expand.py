"""Track queries and tag rejection — the parts of S1/S2 that need no model."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.expand import TRACK_SECTIONS, apply_rejections, track_query

SPLIT = {
    "character": "1girl, school_uniform",
    "background": "rooftop, cloudy_sky",
    "props": "umbrella",
    "action": "standing",
    "mood": "soft_lighting",
    "camera": "wide_shot",
}


def test_each_track_owns_disjoint_sections():
    person = set(TRACK_SECTIONS["person"])
    background = set(TRACK_SECTIONS["background"])
    assert not person & background
    assert person | background == set(SPLIT)


def test_camera_and_mood_ride_with_the_background():
    """On the character track they would turn every board into a portrait."""
    assert "camera" in TRACK_SECTIONS["background"]
    assert "mood" in TRACK_SECTIONS["background"]


def test_track_query_mixes_theme_with_its_own_sections():
    query = track_query("雨上がりの屋上", SPLIT, "background")
    assert "雨上がりの屋上" in query
    assert "rooftop" in query
    assert "wide_shot" in query
    assert "school_uniform" not in query


def test_track_query_bridges_a_japanese_theme_to_english():
    """wd14_vocab is English, so a Japanese theme needs anchor words."""
    query = track_query("屋上で雨を待つ", SPLIT, "background")
    assert "rooftop" in query or "roof" in query
    assert "rain" in query or "rainy" in query


def test_rejections_drop_user_clicks_and_admin_exclusions():
    rows = [
        {"tag": "rooftop", "source": "split"},
        {"tag": "watermark", "source": "topic"},
        {"tag": "unicorn", "source": "lunatic"},
    ]
    kept, dropped = apply_rejections(rows, ["unicorn"], {"watermark"})
    assert [r["tag"] for r in kept] == ["rooftop"]
    assert set(dropped) == {"watermark", "unicorn"}


def test_rejection_matching_ignores_case_and_spacing():
    rows = [{"tag": "Cloudy_Sky", "source": "topic"}]
    kept, dropped = apply_rejections(rows, ["cloudy sky"], set())
    assert kept == []
    assert dropped == ["Cloudy_Sky"]


def test_nothing_rejected_changes_nothing():
    rows = [{"tag": "rooftop", "source": "split"}]
    kept, dropped = apply_rejections(rows, [], set())
    assert kept == rows
    assert dropped == []

"""Scene tags should resolve via frozenset (shared tags.catalog)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.tags.catalog import TAG_TO_AXIS, get_tag_axis


# Tags from cafe / rain-station / rooftop / festival / beach scenes.
_SCENE_TAGS = {
    # cafe
    "cafe": "location",
    "apron": "clothing",
    "counter": "always_fixed",
    "coffee_cup": "always_fixed",
    "latte_art": "always_fixed",
    "espresso_machine": "always_fixed",
    "pitcher": "always_fixed",
    "steam": "always_fixed",
    "pouring": "action",
    "spilling": "action",
    "wiping": "action",
    "guiding": "action",
    "teaching": "action",
    "concentrating": "emotion",
    # rain / station
    "rain": "time_weather",
    "umbrella": "always_fixed",
    "train_station": "location",
    "platform": "location",
    "sharing": "action",
    "puddle": "time_weather",
    "coat": "clothing",
    "waving": "action",
    # rooftop / stars
    "rooftop": "location",
    "star": "time_weather",
    "constellation": "time_weather",
    "night": "time_weather",
    "pointing": "action",
    "looking_up": "action",
    "wonder": "emotion",
    "starlight": "visual",
    "notebook": "always_fixed",
    "school_uniform": "clothing",
    # festival
    "festival": "location",
    "yukata": "clothing",
    "paper_lantern": "always_fixed",
    "food_stall": "location",
    "fireworks": "time_weather",
    "candy_apple": "always_fixed",
    "3girls": "always_fixed",
    "multiple_girls": "always_fixed",
    "running": "action",
    "laughing": "emotion",
    "sparkle": "visual",
    # beach
    "beach": "location",
    "ocean": "location",
    "seaside": "location",
    "swimsuit": "clothing",
    "sand": "location",
    "wave": "time_weather",
    "splash": "time_weather",
    # lighting / drawability pads
    "cinematic_lighting": "visual",
    "warm_light": "visual",
    "daylight": "visual",
    "detailed_background": "visual",
    "depth_of_field": "visual",
    "reaching": "action",
    "looking_at_another": "emotion",
}


@pytest.mark.parametrize("tag,expected", sorted(_SCENE_TAGS.items()))
def test_scene_tags_are_frozenset_classified(tag, expected):
    assert get_tag_axis(tag) == expected, f"{tag} → {get_tag_axis(tag)}"


def test_tag_catalog_grew_and_loads_json():
    assert len(TAG_TO_AXIS) >= 900
    assert get_tag_axis("festival") == "location"
    assert get_tag_axis("pouring") == "action"
    assert get_tag_axis("cinematic_lighting") == "visual"
    assert get_tag_axis("yukata") == "clothing"
    assert get_tag_axis("smile") == "emotion"
    assert get_tag_axis("chartreuse_eyes") == "always_fixed"  # unknown colour
    assert get_tag_axis("teary_eyes") == "emotion"  # listed expression


def test_subject_anchors_insert_and_ensure():
    from app.tags.subject_anchors import ensure_subject_anchor, insert_after_anchors

    line = insert_after_anchors("1girl, solo, cafe", ["silver_hair", "1girl"])
    assert line.startswith("1girl, solo, silver_hair")
    assert line.count("1girl") == 1

    docs = [({"wd14_tags": ["1girl", "cafe"], "wd14_tags_scores": [0.9, 0.5]}, 0)]
    assert ensure_subject_anchor("cafe, smile", docs).startswith("1girl,")

"""Chronicle / scene tags should resolve via frozenset, not only Phase B LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

_JSON = Path(__file__).resolve().parents[2] / "backend" / "app" / "static" / "tag_categories.json"


def _build_axis_map() -> dict[str, str]:
    data = json.loads(_JSON.read_text(encoding="utf-8"))
    m: dict[str, str] = {}
    fixed = data["always_fixed"]
    for key in ("count", "eye_shapes", "body", "skin_face", "race", "composition", "props"):
        for tag in fixed[key]:
            m[tag] = "always_fixed"
    for tag in data["axis_hair"]:
        m[tag] = "hair"
    for tag in data["axis_emotion"]:
        m[tag] = "emotion"
    for tag in data["axis_action"]:
        m[tag] = "action"
    for tag in data["axis_clothing_explicit"]:
        m[tag] = "clothing"
    for tag in data["axis_accessories"]:
        m[tag] = "clothing"
    for tag in data["axis_parts"]:
        m[tag] = "parts"
    for tag in data["axis_art_style"]["always_fixed"]:
        m[tag] = "always_fixed"
    for tag in data["axis_art_style"]["volatile"]:
        m[tag] = "style"
    for tag in data["axis_environment"]["visual_lighting"]:
        m[tag] = "visual"
    for tag in data["axis_environment"]["time_weather"]:
        m[tag] = "time_weather"
    for tag in data["axis_background"]["abstract"]:
        m[tag] = "visual"
    for tag in data["axis_background"]["location"]:
        m[tag] = "location"
    return m


_AXIS = _build_axis_map()
_CLOTHING_SUFFIXES = tuple(
    json.loads(_JSON.read_text(encoding="utf-8"))["patterns"]["clothing_suffixes"]
)
_ACTION_KEYWORDS = tuple(
    json.loads(_JSON.read_text(encoding="utf-8"))["patterns"]["action_keywords"]
)


def _get_tag_axis(tag: str) -> str | None:
    """Mirror inspire._get_tag_axis without importing FastAPI-bound module."""
    mapped = _AXIS.get(tag)
    if mapped is not None:
        return mapped
    if tag.endswith("_hair"):
        return "hair"
    if tag.endswith("_eyes"):
        return "always_fixed"
    if any(tag.endswith(s) for s in _CLOTHING_SUFFIXES):
        return "clothing"
    if any(kw in tag for kw in _ACTION_KEYWORDS):
        return "action"
    return None


# Tags from cafe / rain-station / rooftop / festival / beach Chronicle sims.
_CHRONICLE_SCENE_TAGS = {
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


@pytest.mark.parametrize("tag,expected", sorted(_CHRONICLE_SCENE_TAGS.items()))
def test_chronicle_scene_tags_are_frozenset_classified(tag, expected):
    assert _get_tag_axis(tag) == expected, f"{tag} → {_get_tag_axis(tag)}"


def test_tag_categories_json_is_valid_and_grew():
    data = json.loads(_JSON.read_text(encoding="utf-8"))
    assert len(_AXIS) >= 900
    assert "festival" in data["axis_background"]["location"]
    assert "pouring" in data["axis_action"]
    assert "cinematic_lighting" in data["axis_environment"]["visual_lighting"]
    assert "yukata" in data["axis_clothing_explicit"]

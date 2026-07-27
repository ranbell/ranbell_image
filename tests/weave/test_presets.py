"""Character preset asset integrity + deterministic mapping.

The asset is authored by hand, so these tests are its quality gate: a preset
that regresses to template filler (python reprs in prose, nested inner lists,
appearance that only echoes the tags) fails here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.weave.character.presets import (
    load_seed_presets,
    personality_text_from_preset,
    preset_point_id,
    preset_summary,
    preset_to_character,
)
from app.weave.character.split_tags import soft_normalize_tag

PRESETS = load_seed_presets()

_JA = re.compile(r"[ぁ-んァ-ヶ一-龥]")
IDENTITY_BUCKETS = (
    "hair_color", "hair_style", "eyes", "body",
    "ears_tails_wings", "favorite_clothes", "footwear",
)
REQUIRED_TAG_BUCKETS = IDENTITY_BUCKETS + (
    "expression", "headwear_accessory", "hobby_actions",
)


def test_asset_loads_with_unique_ids():
    assert len(PRESETS) >= 100
    keys = [p["id"] for p in PRESETS]
    assert len(set(keys)) == len(keys)
    assert len({preset_point_id(k) for k in keys}) == len(keys)


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_preset_schema(preset):
    for field in (
        "id", "name", "name_ja", "summary", "summary_ja",
        "gender", "subject_tag", "signature_prop",
    ):
        assert str(preset.get(field) or "").strip(), f"{preset['id']}: {field} is empty"

    # English is the primary text; *_ja carries the Japanese.
    assert not _JA.search(preset["name"]), f"{preset['id']}: name must be English"
    assert not _JA.search(preset["summary"]), f"{preset['id']}: summary must be English"
    assert _JA.search(preset["name_ja"]), f"{preset['id']}: name_ja must be Japanese"
    assert _JA.search(preset["summary_ja"]), f"{preset['id']}: summary_ja must be Japanese"

    # Template filler that broke the previous asset.
    for field in ("summary", "summary_ja"):
        text = preset[field]
        assert "['" not in text and "']" not in text, f"{preset['id']}: {field} leaks a repr"
    assert "性格は" not in preset["summary_ja"], f"{preset['id']}: templated summary_ja"

    for field in ("personality", "inner", "inner_ja"):
        values = preset.get(field)
        assert isinstance(values, list) and values, f"{preset['id']}: {field} empty"
        assert all(isinstance(v, str) and v.strip() for v in values), (
            f"{preset['id']}: {field} must be a flat list of non-empty strings"
        )
    assert len(preset["personality"]) >= 3
    assert len(preset["inner"]) >= 2
    assert len(preset["inner"]) == len(preset["inner_ja"])
    assert all(not _JA.search(v) for v in preset["inner"]), f"{preset['id']}: inner must be English"
    assert all(_JA.search(v) for v in preset["inner_ja"]), f"{preset['id']}: inner_ja must be Japanese"

    appearance = preset.get("appearance") or {}
    for key in ("hair", "eyes", "expression", "body"):
        text = str(appearance.get(key) or "").strip()
        assert text, f"{preset['id']}: appearance.{key} is empty"
        assert not _JA.search(text), f"{preset['id']}: appearance.{key} must be English"
    # Prose must add something the tags do not already say.
    hair_tag = (preset["tags"].get("hair_color") or [""])[0].replace("_", " ")
    assert not appearance["hair"].strip().lower().startswith(hair_tag.lower()), (
        f"{preset['id']}: appearance.hair only echoes the tag"
    )

    tags = preset.get("tags") or {}
    for bucket in REQUIRED_TAG_BUCKETS:
        assert bucket in tags, f"{preset['id']}: tags.{bucket} missing"
        assert isinstance(tags[bucket], list)
        assert all(isinstance(t, str) and t.strip() for t in tags[bucket])
    for bucket in ("hair_color", "eyes", "favorite_clothes", "expression", "hobby_actions"):
        assert tags[bucket], f"{preset['id']}: tags.{bucket} must not be empty"

    prefs = preset.get("preferences") or {}
    for key in ("likes", "dislikes", "favorite_colors"):
        assert prefs.get(key), f"{preset['id']}: preferences.{key} is empty"
        assert all(isinstance(v, str) for v in prefs[key])

    scene = preset.get("default_scene") or {}
    assert str(scene.get("outfit_style") or "").strip()
    assert scene.get("vibe_keywords")


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p["id"])
def test_preset_maps_to_character(preset):
    ch = preset_to_character(preset)

    assert ch["identity_tags"], preset["id"]
    assert ch["source"] == "preset"
    assert soft_normalize_tag(preset["subject_tag"]) in ch["identity_tags"]

    # Expression and gesture are per-panel performance — never baked into identity.
    for tag in (preset["tags"].get("expression") or []):
        assert soft_normalize_tag(tag) not in ch["identity_tags"], (
            f"{preset['id']}: expression {tag} leaked into identity"
        )
    for tag in (preset["tags"].get("hobby_actions") or []):
        assert soft_normalize_tag(tag) not in ch["identity_tags"], (
            f"{preset['id']}: gesture {tag} leaked into identity"
        )
    assert ch["expression_vocab"], preset["id"]
    assert ch["gesture_vocab"], preset["id"]

    # P5 invariant: identity and prop layers stay disjoint.
    assert not (set(ch["identity_tags"]) & set(ch["prop_tags"])), preset["id"]
    # The authored carry prop is what the story threads through the panels.
    assert ch["signature_prop"] == soft_normalize_tag(preset["signature_prop"]), preset["id"]
    assert ch["signature_prop"] in ch["prop_tags"]
    assert ch["signature_prop"] not in ch["identity_tags"]

    personality = ch["personality"]
    assert personality["summary"] and personality["summary_ja"]
    assert personality["inner"] and personality["traits"]
    assert personality["preset_key"] == preset["id"]
    assert all(isinstance(v, str) for v in personality["inner"])


def test_personality_text_prefers_locale():
    preset = PRESETS[0]
    ja = personality_text_from_preset(preset, locale="ja")
    en = personality_text_from_preset(preset, locale="en")
    assert preset["summary_ja"] in ja
    assert preset["summary"] in en
    # Appearance prose is English in both.
    assert preset["appearance"]["hair"] in ja


def test_summary_row_is_light():
    row = preset_summary(PRESETS[0])
    assert set(row) == {
        "id", "preset_key", "name", "name_ja", "summary", "summary_ja",
        "gender", "subject_tag", "traits", "tag_count",
    }
    assert row["tag_count"] > 0

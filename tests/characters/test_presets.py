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

from app.characters.presets import (
    GALLERY_LIMIT,
    _with_candidate,
    load_seed_presets,
    normalise_gallery,
    personality_text_from_preset,
    preset_point_id,
    preset_summary,
    preset_to_character,
)
from app.tags.body import AGE_TAGS, filter_body_tags
from app.tags.split_tags import soft_normalize_tag

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
    # Count is a fact about the scene, not about her. Locked into identity it
    # said "one girl" in every prompt and made a second character impossible.
    subject = soft_normalize_tag(preset["subject_tag"])
    assert subject not in ch["identity_tags"], (
        f"{preset['id']}: {subject} must not be locked to the character"
    )
    assert ch["subject_tag"] == subject, preset["id"]

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
        "gender", "subject_tag", "traits", "tag_count", "board", "gallery",
        "hair_color", "eye_color", "user_created",
    }
    assert row["tag_count"] > 0


def test_bundled_preset_has_an_empty_board():
    """A shipped character has no reference images until someone draws them."""
    row = preset_summary(PRESETS[0])
    assert row["board"] == {"sheet": "", "portrait": ""}
    assert row["user_created"] is False


def test_summary_carries_board_images_once_drawn():
    row = preset_summary({**PRESETS[0], "board": {"sheet": "abc123"}, "user_created": True})
    assert row["board"] == {"sheet": "abc123", "portrait": ""}
    assert row["user_created"] is True


# ── the artwork survives a re-seed ──────────────────────────────────────────
def test_a_preset_id_is_stable_across_reseeds():
    """This is what lets the asset be re-read without dropping the collection.
    Dropping it took every character's portrait with it — one run left all 100
    with no face, because a preset's pictures live on the preset."""
    for preset in PRESETS[:5]:
        assert preset_point_id(preset["id"]) == preset_point_id(preset["id"])
    ids = {preset_point_id(p["id"]) for p in PRESETS}
    assert len(ids) == len(PRESETS), "two characters sharing a row would overwrite"


def test_a_re_roll_keeps_the_earlier_candidates():
    """The fifth attempt is not automatically better than the second, and only
    the user can say which face is hers."""
    kept = []
    for sha in ("aaa", "bbb", "ccc"):
        kept = _with_candidate(kept, sha, "W.json")
    assert [c["sha"] for c in kept] == ["ccc", "bbb", "aaa"], "newest first"


def test_a_candidate_remembers_which_checkpoint_drew_it():
    """Drawing one character on two models to compare them only works if you
    can tell the results apart."""
    kept = _with_candidate([], "aaa", "API_Anima_RIN.json")
    kept = _with_candidate(kept, "bbb", "API_Anima_Ribeya.json")
    assert [c["workflow"] for c in kept] == ["API_Anima_Ribeya.json", "API_Anima_RIN.json"]
    assert kept[0]["at"] >= kept[1]["at"]


def test_drawing_the_same_image_twice_does_not_duplicate_it():
    kept = _with_candidate([{"sha": "aaa", "workflow": "", "at": 1.0},
                            {"sha": "bbb", "workflow": "", "at": 0.0}], "bbb", "W.json")
    assert [c["sha"] for c in kept] == ["bbb", "aaa"]


def test_the_candidate_list_has_an_end():
    kept = []
    for i in range(GALLERY_LIMIT + 5):
        kept = _with_candidate(kept, f"sha{i}", "W.json")
    assert len(kept) == GALLERY_LIMIT
    assert kept[0]["sha"] == f"sha{GALLERY_LIMIT + 4}", "the newest is kept"


def test_the_old_flat_portrait_list_still_reads():
    """The first version could not say which checkpoint drew a candidate."""
    out = normalise_gallery(["aaa", "bbb"])
    assert [c["sha"] for c in out["portrait"]] == ["aaa", "bbb"]
    assert out["sheet"] == []


def test_candidates_are_kept_per_slot():
    out = normalise_gallery({"sheet": [{"sha": "s1", "workflow": "W.json", "at": 3}],
                             "portrait": [{"sha": "p1", "workflow": "V.json", "at": 4}]})
    assert out["sheet"][0]["workflow"] == "W.json"
    assert out["portrait"][0]["sha"] == "p1"


def test_the_summary_carries_the_colours_the_gallery_filters_by():
    """Hair and eye colour are what a person searching a hundred characters
    has in mind, and they were buried inside `tags`."""
    for preset in PRESETS:
        row = preset_summary(preset)
        assert row["hair_color"], preset["id"]
        assert row["eye_color"], preset["id"]


def test_no_age_tag_reaches_a_character():
    """Age is written setting, never a tag.

    `mature_female` on a character written as a student rendered a woman two
    decades older, in every picture she was ever in, with nothing downstream
    able to remove it. The bucket is allowlisted now — this is the guard that
    says so out loud.
    """
    for preset in PRESETS:
        identity = preset_to_character(preset)["identity_tags"]
        leaked = sorted(set(identity) & AGE_TAGS)
        assert not leaked, f"{preset['id']}: age tag in identity: {leaked}"


def test_the_asset_itself_names_no_age():
    """Refusing the tag at the mapping is not enough — the asset should not
    carry one either, or the next reader copies it into a new preset."""
    for preset in PRESETS:
        for bucket, values in (preset.get("tags") or {}).items():
            found = sorted({soft_normalize_tag(str(v)) for v in values or []} & AGE_TAGS)
            assert not found, f"{preset['id']}: tags.{bucket} carries {found}"


def test_body_bucket_is_allowlisted_not_denylisted():
    """A build the vocabulary does not know is dropped, not locked in."""
    kept, refused = filter_body_tags(["tall", "mature_female", "wearing_a_hat"])
    assert kept == ["tall"]
    assert refused == ["mature_female", "wearing_a_hat"]

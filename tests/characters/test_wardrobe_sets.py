"""The clothes a character owns.

The Showrunner (2026-08-29): "there is no default outfit or belongings, so right
after a conversation starts things can suddenly go strange". The notebook's
`wearing` starts empty, so **being told "take it off" from a state of no clothes
left her hanging in mid-air.**

Two things are guarded here — **that the signature outfit never changes**, and
**that a character with nothing written still does not break**.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir / "backend") not in sys.path:
    sys.path.insert(0, str(root_dir / "backend"))

from app.characters.presets import (  # noqa: E402
    WARDROBE_KEYS, preset_to_character, wardrobe_sets,
)

_ASSET = (root_dir / "backend" / "app" / "characters" / "assets"
          / "personality_presets.json")


def _presets() -> list[dict]:
    return json.loads(_ASSET.read_text(encoding="utf-8"))


def test_a_preset_without_a_wardrobe_still_gets_one():
    """**Migration.** Nothing breaks while the writing is unfinished, nor for a new
    character added later."""
    got = wardrobe_sets({}, outfit=["blouse", "loafers"], props=["tote_bag"])
    assert [r["key"] for r in got] == ["signature"]
    assert got[0]["tags"] == ["blouse", "loafers"]
    assert got[0]["props"] == ["tote_bag"]


def test_nothing_to_wear_stays_nothing():
    """No outfit is conjured for a preset that has none."""
    assert wardrobe_sets({}, outfit=[], props=[]) == []


def test_the_signature_never_changes():
    """The profile page and the reference board read `outfit_tags`. **That does not
    move.**"""
    for preset in _presets():
        char = preset_to_character(preset)
        sets = char["wardrobe_sets"]
        if not sets:
            continue
        assert sets[0]["key"] == "signature", preset.get("name_ja")
        assert sets[0]["tags"] == char["outfit_tags"], preset.get("name_ja")


def test_every_character_can_get_dressed():
    """**Everyone owns at least one outfit.** Empty here and the conversation starts
    in mid-air."""
    naked = [
        str(p.get("name_ja") or p.get("name"))
        for p in _presets() if not preset_to_character(p)["wardrobe_sets"]
    ]
    assert not naked, f"服の無い子がいる: {naked}"


def test_written_sets_are_ordered_and_named():
    """For a written character `signature` comes first and the keys follow a fixed
    order."""
    written = [p for p in _presets() if p.get("wardrobe")]
    assert written, "まだ一人も書かれていない"
    for preset in written:
        sets = preset_to_character(preset)["wardrobe_sets"]
        keys = [r["key"] for r in sets]
        assert keys[0] == "signature"
        assert keys == sorted(keys, key=lambda k: WARDROBE_KEYS.index(k))
        for row in sets:
            assert row["name_ja"], preset.get("name_ja")
            assert row["tags"], (preset.get("name_ja"), row["key"])


def test_every_preset_is_new_enough_to_reach_qdrant():
    """**Without raising `version` it never reaches Qdrant.**

    `sync_muse_presets_from_asset` writes only when `preset_version(seed) >
    stored`. The Showrunner (2026-08-29): "are you raising the rev on each item in
    the json? the data on qdrant is not being updated" — wardrobe sets had been
    written for 30 characters and, as it stood, not one of them landed.

    **Edit an asset, raise its `version`.** This pins that step as "a character
    with wardrobe sets is at version 2 or above", so forgetting it is noticed.
    """
    from app.characters.presets import preset_version

    stale = [
        str(p.get("name_ja") or p.get("name"))
        for p in _presets()
        if p.get("wardrobe") and preset_version(p) < 2
    ]
    assert not stale, f"wardrobe を書いたのに version が上がっていない: {stale}"


def test_versions_are_readable_numbers():
    for preset in _presets():
        from app.characters.presets import preset_version
        assert preset_version(preset) >= 1, preset.get("name_ja")

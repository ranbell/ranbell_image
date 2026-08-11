import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

JA_JSON_PATH = Path(__file__).parent.parent.parent / "frontend/src/locales/ja.json"
EN_JSON_PATH = Path(__file__).parent.parent.parent / "frontend/src/locales/en.json"


def test_i18n_ja_en_keys_match_and_complete():
    """Verify all keys in ja.json and en.json for muse section match 100% with no missing translations."""
    assert JA_JSON_PATH.exists(), "ja.json does not exist!"
    assert EN_JSON_PATH.exists(), "en.json does not exist!"

    with JA_JSON_PATH.open(encoding="utf-8") as f:
        ja_data = json.load(f)
    with EN_JSON_PATH.open(encoding="utf-8") as f:
        en_data = json.load(f)

    ja_muse = ja_data.get("muse", {})
    en_muse = en_data.get("muse", {})

    required_new_keys = [
        "partnerCharacter",
        "pickPartnerCharacter",
        "noPartner",
        "wMuseMode",
        "firstPerson",
        "userAddress",
        "talkQuirks",
        "sayExamples",
        "wMuseSessionActive",
        "chemistryActive",
        "defaultActressName",
    ]

    for key in required_new_keys:
        assert key in ja_muse, f"Key '{key}' missing from ja.json muse section!"
        assert key in en_muse, f"Key '{key}' missing from en.json muse section!"
        assert isinstance(ja_muse[key], str) and len(ja_muse[key]) > 0
        assert isinstance(en_muse[key], str) and len(en_muse[key]) > 0

    required_new_quick_keys = [
        "backToBack",
        "backToBackPrompt",
        "handInHand",
        "handInHandPrompt",
        "secretTalk",
        "secretTalkPrompt",
    ]
    ja_quick = ja_muse.get("quick", {})
    en_quick = en_muse.get("quick", {})
    for key in required_new_quick_keys:
        assert key in ja_quick, f"Key 'quick.{key}' missing from ja.json muse section!"
        assert key in en_quick, f"Key 'quick.{key}' missing from en.json muse section!"
        assert isinstance(ja_quick[key], str) and len(ja_quick[key]) > 0
        assert isinstance(en_quick[key], str) and len(en_quick[key]) > 0


def _flat(data: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in data.items():
        if isinstance(value, dict):
            out |= _flat(value, f"{prefix}{key}.")
        else:
            out.add(prefix + key)
    return out


def test_every_muse_key_exists_in_both_locales():
    """The hardcoded list above only guards the keys somebody remembered to add
    to it, so a new panel string could ship translated in one language and blank
    in the other. This covers the whole section, nested keys included."""
    with JA_JSON_PATH.open(encoding="utf-8") as f:
        ja = _flat(json.load(f).get("muse", {}))
    with EN_JSON_PATH.open(encoding="utf-8") as f:
        en = _flat(json.load(f).get("muse", {}))

    assert not ja - en, f"muse keys in ja.json but not en.json: {sorted(ja - en)}"
    assert not en - ja, f"muse keys in en.json but not ja.json: {sorted(en - ja)}"


def test_the_facet_panel_is_translated():
    """The shot is shown in parts, and every part needs a name in both."""
    with JA_JSON_PATH.open(encoding="utf-8") as f:
        ja = json.load(f)["muse"]
    with EN_JSON_PATH.open(encoding="utf-8") as f:
        en = json.load(f)["muse"]

    for key in ("facets", "facetsHint", "facetLock", "facetLocked",
                "facetEmpty", "facetBy", "facetConflict", "digest", "digestHint"):
        for name, data in (("ja", ja), ("en", en)):
            assert isinstance(data.get(key), str) and data[key], \
                f"muse.{key} missing from {name}.json"
    for part in ("place", "hour", "light", "props", "costume", "pose",
                 "expression", "camera"):
        for name, data in (("ja", ja), ("en", en)):
            assert (data.get("facetNames") or {}).get(part), \
                f"muse.facetNames.{part} missing from {name}.json"


def test_i18n_all_top_level_keys_symmetry():
    """Ensure top level sections in ja.json and en.json are symmetrical."""
    with JA_JSON_PATH.open(encoding="utf-8") as f:
        ja_data = json.load(f)
    with EN_JSON_PATH.open(encoding="utf-8") as f:
        en_data = json.load(f)

    ja_keys = set(ja_data.keys())
    en_keys = set(en_data.keys())

    missing_in_en = ja_keys - en_keys
    missing_in_ja = en_keys - ja_keys

    assert not missing_in_en, f"Keys in ja.json but missing in en.json: {missing_in_en}"
    assert not missing_in_ja, f"Keys in en.json but missing in ja.json: {missing_in_ja}"

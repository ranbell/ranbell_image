import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

JA_JSON_PATH = Path(__file__).parent.parent.parent / "frontend/src/locales/ja.json"
EN_JSON_PATH = Path(__file__).parent.parent.parent / "frontend/src/locales/en.json"


def test_every_key_the_panel_asks_for_exists_in_both_locales():
    """**画面が引く鍵を、画面から読む。**

    元は手で並べた一覧だった（`partnerCharacter` / `wMuseMode` / `quick.*` …）。
    あれは classic の画面の契約で、画面ごと退役した（2026-09-12）ので、
    並べ直す代わりに**撮影室の画面そのものから抜く**ことにした。手で足すのを
    忘れても落ちる。

    組み立てる鍵（`muse.` に変数を継ぐ書き方）は写らない —— 画風（`muse.looks.*`）と
    欄（`muse.fields.*`）と姿勢の下絵（`muse.poseSketch.*`）がそれで、
    下の鍵の左右対称の試験が受け持つ。
    """
    import re

    ja, en = _locales()
    panels = [Path(__file__).parent.parent.parent / "frontend/src/components" / name
              for name in ("MusePanel.vue", "CharacterGallery.vue")]
    asked: set[str] = set()
    for panel in panels:
        src = panel.read_text(encoding="utf-8")
        asked |= set(re.findall(r"""t\(\s*['"]muse\.([A-Za-z0-9_.]+)['"]""", src))
    assert len(asked) > 60, f"画面から鍵が読めていない（{len(asked)}本）"

    for key in sorted(asked):
        for name, data in (("ja", ja), ("en", en)):
            node = data
            for part in key.split("."):
                assert isinstance(node, dict) and part in node, \
                    f"muse.{key} が {name}.json に無い（画面は引いている）"
                node = node[part]
            assert isinstance(node, str) and node, f"muse.{key} が空（{name}）"


def _locales() -> tuple[dict, dict]:
    with JA_JSON_PATH.open(encoding="utf-8") as f:
        ja = json.load(f)["muse"]
    with EN_JSON_PATH.open(encoding="utf-8") as f:
        en = json.load(f)["muse"]
    return ja, en


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

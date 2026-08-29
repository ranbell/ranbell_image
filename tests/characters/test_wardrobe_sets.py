"""その子が持っている服。

総監督（2026-08-29）「default の衣装や持ち物がないので、会話開始後にいきなり
おかしな状態に陥ることがあります」。手帖の `wearing` は空で始まるので、**服が
無い状態から「脱いで」と言われて宙に浮いていた。**

ここで守るのは二つ —— **代表服が変わらないこと**と、**書いていない子でも
壊れないこと**。
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
    """**移行。** 書き終わるまでも、将来の新しい子でも壊れない。"""
    got = wardrobe_sets({}, outfit=["blouse", "loafers"], props=["tote_bag"])
    assert [r["key"] for r in got] == ["signature"]
    assert got[0]["tags"] == ["blouse", "loafers"]
    assert got[0]["props"] == ["tote_bag"]


def test_nothing_to_wear_stays_nothing():
    """服の無いプリセットに、勝手な一着を生やさない。"""
    assert wardrobe_sets({}, outfit=[], props=[]) == []


def test_the_signature_never_changes():
    """紹介ページと参照ボードが見ているのは `outfit_tags`。**そこは動かさない。**"""
    for preset in _presets():
        char = preset_to_character(preset)
        sets = char["wardrobe_sets"]
        if not sets:
            continue
        assert sets[0]["key"] == "signature", preset.get("name_ja")
        assert sets[0]["tags"] == char["outfit_tags"], preset.get("name_ja")


def test_every_character_can_get_dressed():
    """**全員が最低でも一着持っていること。** ここが空だと会話開始で宙に浮く。"""
    naked = [
        str(p.get("name_ja") or p.get("name"))
        for p in _presets() if not preset_to_character(p)["wardrobe_sets"]
    ]
    assert not naked, f"服の無い子がいる: {naked}"


def test_written_sets_are_ordered_and_named():
    """書いた子は `signature` が先頭で、鍵は決まった並びに載る。"""
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
    """**`version` を上げないと Qdrant に届かない。**

    `sync_muse_presets_from_asset` は `preset_version(seed) > stored` の
    ときだけ書き込む。総監督（2026-08-29）「json の各項目の rev 上げてくれて
    る？ qdrant 上のデータが更新されないよ」—— 衣装セットを 30人ぶん書いた
    のに、そのままでは一件も反映されなかった。

    **アセットを編集したら `version` を上げる。** ここは「衣装セットを持つ子は
    version 2 以上」という形で、その手順を落としたことに気づけるようにする。
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

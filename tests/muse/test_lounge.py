"""楽屋のお出かけ —— 友達と撮った一枚。

**この機能は削らない。** 軽量化の対象外（総監督の指示）。ここで見るのは、
出来上がるプロンプトが「休みの日にスマホで撮った写真」になっているか。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir / "backend") not in sys.path:
    sys.path.insert(0, str(root_dir / "backend"))

from app.muse import lounge  # noqa: E402


def test_the_outing_photo_says_what_they_are_wearing():
    """総監督（2026-08-29）「友達とのお出かけ写真がみんな水着に……」。

    プロンプトに**服が一つも無かった**ので、サンプラーが埋めていた。人数が
    複数・屋外・服の指定なしという並びだと、行き先に関わらず同じ既定へ寄る。
    季節の一語だけ置いて、あとは決めすぎない。
    """
    cast = [{"subject_tag": "1girl"}] * 3
    for season, expect in (("冬", "coat"), ("夏", "short_sleeves"),
                           ("春", "cardigan"), ("秋", "jacket")):
        out = lounge.snapshot_prompt(
            cast, identity_tags=[[]], occasion="street", season=season,
            rng=random.Random(0),
        )
        assert "casual clothes" in out
        assert expect in out, (season, out)
    # 季節が読めなくても、服はある
    assert "casual clothes" in lounge.snapshot_prompt(
        cast, identity_tags=[[]], occasion="street", rng=random.Random(0))


def test_indoors_is_not_dressed_for_outdoors():
    """図書館でマフラーを巻いている絵は、それだけで嘘になる。"""
    cast = [{"subject_tag": "1girl"}] * 3
    inside = lounge.snapshot_prompt(
        cast, identity_tags=[[]], occasion="library", season="冬",
        rng=random.Random(0))
    outside = lounge.snapshot_prompt(
        cast, identity_tags=[[]], occasion="snowy street", season="冬",
        rng=random.Random(0))
    assert "indoors" in inside and "coat" not in inside and "scarf" not in inside
    assert "outdoors" in outside and "coat" in outside


def test_the_outing_photo_is_not_a_group_portrait():
    """総監督「集合写真みたいになってなんだか変」。

    もとは `standing together, looking at viewer` —— 並んでレンズを見る絵で、
    それは集合写真そのものだった。遊んでいる最中を撮る。
    """
    cast = [{"subject_tag": "1girl"}] * 3
    seen = set()
    for i in range(12):
        out = lounge.snapshot_prompt(
            cast, identity_tags=[[]], occasion="street", season="春",
            rng=random.Random(i))
        assert "standing together" not in out
        assert "looking at viewer" not in out
        assert "candid photo" in out
        seen.add(out)
    assert len(seen) > 1, "毎回まったく同じ絵になっている"

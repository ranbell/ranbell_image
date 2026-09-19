"""The green room's outings — a photo taken with a friend.

**This feature is never cut.** It is out of scope for any slimming down (the
Showrunner's instruction). What is checked here is whether the prompt that comes
out reads as "a photo taken on a phone on a day off".
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
    """The Showrunner (2026-08-29): "the outing photos with friends all come out in
    swimsuits……".

    The prompt had **no clothing at all**, so the sampler filled it in. Several
    people, outdoors, no clothing named — that combination drifts to the same
    default whatever the destination. One word for the season is placed and nothing
    more is decided.
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
    """A picture with a scarf wrapped on in a library is a lie on that alone."""
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
    """The Showrunner: "it comes out like a group photo, which is somehow odd".

    It used to be `standing together, looking at viewer` — lined up looking into
    the lens, which is a group photo exactly. Shoot them mid-play instead.
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

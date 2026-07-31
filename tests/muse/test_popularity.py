"""Popularity is about "can the model draw this", not "is this a good tag".

A tag can sit right next to the theme in embedding space and still have forty
examples in the training data, in which case the checkpoint has never really
learned it. The Danbooru post count is the cheapest available proxy — and it is
deliberately kept away from the surprise layers, where rarity is the point.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.invoke.vocab_bank import popularity_score


def test_more_posts_scores_higher():
    assert popularity_score(100_000) > popularity_score(1_000) > popularity_score(10)


def test_unknown_or_absent_count_scores_zero():
    assert popularity_score(0) == 0.0
    assert popularity_score(-5) == 0.0


def test_score_is_bounded():
    assert 0.0 <= popularity_score(1) <= 1.0
    assert popularity_score(5_000_000) == 1.0


def test_the_curve_is_logarithmic():
    """The interesting gap is 40 vs 40,000, not 40,000 vs 400,000."""
    low = popularity_score(40_000) - popularity_score(40)
    high = popularity_score(400_000) - popularity_score(40_000)
    assert low > high


def test_a_rare_tag_can_still_win_on_semantics():
    """The weight is a nudge, not an override — 0.35 cannot flip a big gap."""
    weight = 0.35
    rare_but_perfect = 0.95 + weight * popularity_score(50)
    common_but_vague = 0.55 + weight * popularity_score(500_000)
    assert rare_but_perfect > common_but_vague

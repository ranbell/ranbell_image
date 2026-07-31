"""Folding three board images into one ranked tag list."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.harvest import fold_track


def _row(tag, score, category=0):
    return {"tag": tag, "score": score, "category": category}


def test_agreement_beats_a_single_confident_hit():
    """A tag on every board is real; a tag on one board is one seed wandering."""
    folded = fold_track([
        [_row("rooftop", 0.6), _row("unicorn", 0.99)],
        [_row("rooftop", 0.6)],
        [_row("rooftop", 0.6)],
    ])
    order = [r["tag"] for r in folded]
    assert order.index("rooftop") < order.index("unicorn")


def test_counts_and_agreement_are_reported():
    folded = fold_track([
        [_row("rain", 0.5)],
        [_row("rain", 0.8)],
    ])
    rain = folded[0]
    assert rain["count"] == 2
    assert rain["agreement"] == 1.0
    assert rain["score"] == 0.8, "the best confidence across images wins"


def test_theme_tags_are_nudged_up():
    without = fold_track([[_row("puddle", 0.4)], [_row("lantern", 0.4)]])
    with_seed = fold_track(
        [[_row("puddle", 0.4)], [_row("lantern", 0.4)]],
        seed_tags=["lantern"],
    )
    assert [r["tag"] for r in with_seed][0] == "lantern"
    assert {r["tag"] for r in without} == {r["tag"] for r in with_seed}


def test_rerank_is_off_unless_asked_for():
    """The weak tail is the point of a 0.15 threshold; it is not pruned by default."""
    per_image = [[_row("very_rare_thing", 0.2), _row("1girl", 0.99)]]
    frequency = {"very_rare_thing": 0.0001, "1girl": 0.99}
    plain = {r["tag"] for r in fold_track(per_image, frequency=frequency)}
    assert "very_rare_thing" in plain

    ranked = fold_track(per_image, frequency=frequency, rerank=True)
    assert {r["tag"] for r in ranked} == plain, "re-rank reorders, it never drops"
    order = [r["tag"] for r in ranked]
    assert order.index("1girl") < order.index("very_rare_thing")


def test_mid_band_frequency_is_promoted_over_both_extremes():
    per_image = [[_row("common", 0.5), _row("midband", 0.5), _row("ultrarare", 0.5)]]
    frequency = {"common": 0.95, "midband": 0.2, "ultrarare": 0.0002}
    order = [r["tag"] for r in fold_track(per_image, frequency=frequency, rerank=True)]
    assert order[0] == "midband"


def test_empty_board_folds_to_nothing():
    assert fold_track([]) == []
    assert fold_track([[], []]) == []

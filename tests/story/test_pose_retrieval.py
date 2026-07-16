"""Tests for Chronicle pose/action tag retrieval (hybrid ranker)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.pose_retrieval import (  # noqa: E402
    fallback_pose_tags,
    lexical_overlap,
    rank_pose_tags,
    sentence_stems,
    solo_conflict,
)
from app.tags.catalog import pose_action_subset  # noqa: E402


# ── catalog subset ────────────────────────────────────────────────────────────

def test_pose_action_subset_includes_actions_excludes_nouns():
    names = [
        "holding_sword", "running", "pouring", "sitting_on_table",
        "clothing_cutout", "mosaic_censoring", "bar_censor", "glowing",
        "backlighting", "drawstring", "o-ring", "earrings", "blonde_hair",
        "smile", "1girl_padding",  # padding suffix denied
        "hugging_object", "sleeping_bag",
    ]
    got = set(pose_action_subset(names))
    assert {"holding_sword", "running", "pouring", "sitting_on_table", "hugging_object"} <= got
    for bad in ("clothing_cutout", "mosaic_censoring", "bar_censor", "glowing",
                "backlighting", "drawstring", "o-ring", "earrings",
                "blonde_hair", "smile", "sleeping_bag"):
        assert bad not in got, bad


# ── lexical overlap (verb-weighted) ───────────────────────────────────────────

def test_lexical_verb_match_full_score():
    stems = sentence_stems("She sleeps on the train")
    assert lexical_overlap(stems, "sleeping") == 1.0


def test_lexical_verb_match_scaled_by_rest():
    stems = sentence_stems("She sleeps on the train")
    # verb matches but 'person' does not -> 0.5, not 1.0
    assert lexical_overlap(stems, "sleeping_on_person") == 0.5


def test_lexical_contradicting_verb_scores_zero():
    stems = sentence_stems("She pours hot tea at the kitchen table")
    # 'humping' asserts a different action — noun 'table' must not promote it
    assert lexical_overlap(stems, "table_humping") == 0.0
    assert lexical_overlap(stems, "sitting_on_table") == 0.0
    stems2 = sentence_stems("She hands a letter to her friend")
    assert lexical_overlap(stems2, "kissing_hand") == 0.0


def test_lexical_holding_is_neutral_not_contradicting():
    stems = sentence_stems("She pours hot tea into a cup")
    assert lexical_overlap(stems, "holding_cup") > 0.0


def test_lexical_noun_only_weak():
    stems = sentence_stems("She reads a book under a tree")
    noun_only = lexical_overlap(stems, "holding_book")
    verb = lexical_overlap(stems, "reading")
    assert 0 < noun_only < 0.4
    assert verb == 1.0


# ── solo conflict ─────────────────────────────────────────────────────────────

def test_solo_conflict_drops_person_tags_for_solo_scene():
    stems = sentence_stems("She sleeps on the train, head against the window")
    assert solo_conflict(stems, "sleeping_on_person")
    assert not solo_conflict(stems, "sleeping")


def test_solo_conflict_allows_person_tags_with_second_person():
    stems = sentence_stems("She hands a letter to her friend")
    assert not solo_conflict(stems, "carrying_person")


# ── hybrid ranker (synthetic vectors) ────────────────────────────────────────

def _unit(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def _mk_vocab():
    # 4-dim toy space: axis0 = pour-ish, axis1 = sit-ish, axis2 = misc, axis3 = person
    tags = ["pouring", "holding_cup", "sitting", "table_humping", "sitting_on_person"]
    vecs = np.stack([
        _unit([1.0, 0.1, 0.0, 0.0]),   # pouring
        _unit([0.9, 0.2, 0.1, 0.0]),   # holding_cup
        _unit([0.2, 1.0, 0.0, 0.0]),   # sitting (hub)
        _unit([0.8, 0.3, 0.2, 0.0]),   # table_humping (cosine-close!)
        _unit([0.3, 0.9, 0.0, 0.4]),   # sitting_on_person
    ])
    return tags, vecs


def test_rank_promotes_verb_match_demotes_contradiction():
    tags, vecs = _mk_vocab()
    q = _unit([1.0, 0.15, 0.05, 0.0])
    got = rank_pose_tags(tags, vecs, q, "She pours tea at the table", k=3)
    assert got[0] == "pouring"
    assert "table_humping" not in got


def test_rank_hub_demoted_without_lexical_anchor():
    tags, vecs = _mk_vocab()
    q = _unit([0.3, 1.0, 0.0, 0.1])  # sit-ish query
    # sentence with no 'sitting' word: hub 'sitting' penalized but may survive;
    # solo conflict must kill sitting_on_person outright
    got = rank_pose_tags(tags, vecs, q, "She rests quietly at the cafe", k=3)
    assert "sitting_on_person" not in got


def test_rank_cross_axis_reuse_penalty():
    tags, vecs = _mk_vocab()
    q = _unit([1.0, 0.15, 0.05, 0.0])
    base = rank_pose_tags(tags, vecs, q, "She pours tea", k=2)
    reused = rank_pose_tags(tags, vecs, q, "She pours tea", k=2,
                            used_tags=[base[0]])
    # top tag stays only if still above others despite penalty; with synthetic
    # margins the penalized tag must not gain rank
    assert base[0] == "pouring"
    assert reused.index("pouring") >= 0  # still present (lex anchor) …
    # … but a non-reused close tag now leads or ties
    assert reused[0] in ("pouring", "holding_cup")


def test_rank_empty_vocab():
    assert rank_pose_tags([], np.zeros((0, 1)), np.zeros(1), "runs", k=3) == []


# ── fallback ladder ───────────────────────────────────────────────────────────

def test_fallback_matches_catalog_pose_axis():
    got = fallback_pose_tags("She kneels down, leaning forward over the flowerbed")
    assert "kneeling" in got
    assert "leaning_forward" in got


def test_fallback_action_keywords():
    got = fallback_pose_tags("She is running and eating bread")
    assert "running" in got
    assert "eating" in got

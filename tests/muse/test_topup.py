"""Retrieval demoted to naming what the picture is missing.

As the first step the vocabulary search had to invent a picture out of a phrase
and was bad at it. Here the picture already exists and has been read back off
the canvas, so the question is small: of the tags the theme suggests, which are
absent, and would any of them help?
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.topup import (
    DEFAULT_MIN_SCORE,
    collect_candidates,
    pick_reinforcements,
    track_for,
)

HITS = [
    {"name": "library", "score": 0.71, "count": 40000},   # already in the picture
    {"name": "desk_lamp", "score": 0.55, "count": 9000},
    {"name": "dust_motes", "score": 0.41, "count": 300},
    {"name": "black_border", "score": 0.38, "count": 5000},   # junk
    {"name": "vending_machine", "score": 0.22, "count": 7000},  # below the cutoff
]


class FakeDB:
    def __init__(self, hits):
        self.hits = hits

    async def search_wd14_vocab(self, vec, **kw):
        return list(self.hits)


class FakeLLM:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {"add": []}
        self.prompt = ""

    async def embed(self, text):
        return [1.0, 0.0, 0.0]

    async def generate_text(self, prompt, model=None, options=None, fmt=None):
        self.prompt = prompt
        return json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload


class BrokenLLM(FakeLLM):
    async def embed(self, text):
        raise RuntimeError("ollama is down")


def _candidates(present=("library",), hits=HITS, **kw):
    return asyncio.run(collect_candidates(
        FakeDB(hits), FakeLLM(), theme="雨の日の図書室", present=set(present), **kw,
    ))


def test_candidates_exclude_what_the_picture_already_has():
    assert "library" not in [c["tag"] for c in _candidates()]


def test_candidates_respect_the_score_cutoff():
    names = [c["tag"] for c in _candidates()]
    assert "desk_lamp" in names and "dust_motes" in names
    assert "vending_machine" not in names, f"below {DEFAULT_MIN_SCORE}"


def test_candidates_drop_junk():
    assert "black_border" not in [c["tag"] for c in _candidates()]


def test_body_parts_are_never_offered():
    """The model was told not to pick these and picked `legs` and `thighs`
    anyway, reasoning that detail on the body enhances the beauty."""
    hits = HITS + [{"name": "legs", "score": 0.6, "count": 9000},
                   {"name": "thighs", "score": 0.55, "count": 8000}]
    names = [c["tag"] for c in _candidates(hits=hits)]
    assert "legs" not in names and "thighs" not in names


def test_candidates_carry_the_post_count_for_display():
    lamp = next(c for c in _candidates() if c["tag"] == "desk_lamp")
    assert lamp["count"] == 9000


def test_a_blank_theme_asks_for_nothing():
    assert _candidates() and asyncio.run(collect_candidates(
        FakeDB(HITS), FakeLLM(), theme="  ", present=set(),
    )) == []


def test_a_dead_embedder_returns_nothing_rather_than_raising():
    assert asyncio.run(collect_candidates(
        FakeDB(HITS), BrokenLLM(), theme="t", present=set(),
    )) == []


# ── the pick ────────────────────────────────────────────────────────────────
CANDS = [{"tag": "desk_lamp", "score": 0.55, "count": 9000},
         {"tag": "dust_motes", "score": 0.41, "count": 300}]


def _pick(payload, picks=5):
    llm = FakeLLM(payload)
    out = asyncio.run(pick_reinforcements(
        CANDS, llm, theme="t", present=["library", "rain"], picks=picks,
    ))
    return out, llm


def test_the_model_picks_from_the_offered_list():
    out, _ = _pick({"add": [{"tag": "desk_lamp", "why": "the room has no light source"}]})
    assert out == [{"tag": "desk_lamp", "why": "the room has no light source"}]


def test_invented_tags_are_refused():
    """Choosing is the one job this step has."""
    out, _ = _pick({"add": [{"tag": "helicopter", "why": "why not"}]})
    assert out == []


def test_the_pick_is_capped():
    out, _ = _pick({"add": [{"tag": "desk_lamp"}, {"tag": "dust_motes"}]}, picks=1)
    assert len(out) == 1


def test_choosing_nothing_is_allowed():
    assert _pick({"add": []})[0] == []


def test_no_candidates_means_no_call():
    assert asyncio.run(pick_reinforcements([], FakeLLM(), theme="t", present=[])) == []


def test_unparseable_output_adds_nothing():
    assert _pick("not json")[0] == []


def test_the_prompt_shows_what_the_picture_already_has():
    _, llm = _pick({"add": []})
    assert "library, rain" in llm.prompt
    assert "Choosing nothing is a valid answer" in llm.prompt


def test_reinforcements_are_routed_to_a_track():
    assert track_for("desk_lamp") == "background"
    assert track_for("closed_eyes") == "person"

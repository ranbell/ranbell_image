"""The model writes the board prompts; the filter catches what it still slips.

This replaced a vector search over `wd14_vocab`. The search kept answering a
question it was bad at — neighbours of "library, rain" include `closed_eyes`,
because people get photographed in libraries — and the board rendered whatever
came back. A model asked for thirty danbooru tags for a rainy library is
answering a much easier question.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.compose import TAGS_PER_TRACK, compose_tracks

CHARACTER = {
    "identity_tags": ["1girl", "black_hair", "very_long_hair", "brown_eyes"],
    "outfit_tags": ["cardigan", "long_skirt"],
    "prop_tags": ["glasses"],
}


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""

    async def generate_text(self, prompt, model=None, options=None, fmt=None):
        self.prompt = prompt
        return json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload


def _compose(payload, character=CHARACTER):
    llm = FakeLLM(payload)
    return asyncio.run(compose_tracks("雨の日の図書室", character, llm, model="m")), llm


CLEAN = {
    "background": "library, rain, stained_glass, bookshelf, wet_window, dim_lighting",
    "person": "cardigan, long_skirt, holding_book, looking_down, standing",
}


def test_both_tracks_come_back_tagged():
    out, _ = _compose(CLEAN)
    assert [r["tag"] for r in out["background"]][:2] == ["library", "rain"]
    assert all(r["source"] == "compose" for r in out["person"])


def test_a_person_tag_in_the_background_list_is_dropped():
    """The prompt forbids it and the model does it anyway often enough that one
    `1girl` put a figure into a room that was meant to be empty."""
    out, _ = _compose({**CLEAN, "background": "library, 1girl, closed_eyes, bookshelf"})
    assert [r["tag"] for r in out["background"]] == ["library", "bookshelf"]


def test_a_backdrop_tag_in_the_person_list_is_dropped():
    out, _ = _compose({**CLEAN, "person": "cardigan, simple_background, library, holding_book"})
    names = [r["tag"] for r in out["person"]]
    assert "simple_background" not in names and "library" not in names
    assert "cardigan" in names


def test_appearance_contradicting_the_locked_character_is_dropped():
    out, _ = _compose({**CLEAN, "person": "blue_hair, green_eyes, cardigan"})
    assert [r["tag"] for r in out["person"]] == ["cardigan"]


def test_junk_never_survives():
    out, _ = _compose({**CLEAN, "background": "library, no_humans, black_border, fisheye"})
    assert [r["tag"] for r in out["background"]] == ["library"]


def test_duplicates_are_collapsed():
    out, _ = _compose({**CLEAN, "background": "library, Library, library "})
    assert len(out["background"]) == 1


def test_a_list_instead_of_a_string_is_accepted():
    out, _ = _compose({**CLEAN, "background": ["library", "rain"]})
    assert [r["tag"] for r in out["background"]] == ["library", "rain"]


def test_the_prompt_locks_the_character_and_asks_for_the_right_count():
    _, llm = _compose(CLEAN)
    assert "FIXED" in llm.prompt
    assert "black_hair" in llm.prompt
    assert str(TAGS_PER_TRACK) in llm.prompt
    assert "Never write a hair" in llm.prompt


def test_without_a_character_the_model_is_asked_to_invent_one():
    _, llm = _compose(CLEAN, character={})
    assert "Give the character a hair colour" in llm.prompt
    assert "FIXED" not in llm.prompt


def test_the_prompt_keeps_the_tracks_apart():
    _, llm = _compose(CLEAN)
    assert "NO people" in llm.prompt
    assert "NO location" in llm.prompt

"""The LLM cleanup must remove foreign tags without curating away the surprise.

Reading the board back at threshold 0.15 exists to pick up a weak, strange tail
that nobody asked for — that tail is where the interesting images come from. A
model told to "clean up" a tag list will happily delete exactly that, so every
guard here is about the cleanup overreaching rather than under-reaching.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.cleanup import clean_track

BACKGROUND = [
    {"tag": "library", "score": 0.97},
    {"tag": "rain", "score": 0.93},
    {"tag": "bookshelf", "score": 0.91},
    {"tag": "zettai_ryouiki", "score": 0.55},
    {"tag": "pokemon_(creature)", "score": 0.44},
    {"tag": "chandelier_of_bone", "score": 0.18},
]


class FakeLLM:
    """Returns a canned removal list and records the prompt it was given."""

    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""
        self.model = None

    async def generate_text(self, prompt, model=None, options=None, fmt=None):
        self.prompt = prompt
        self.model = model
        return json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload


class BrokenLLM:
    async def generate_text(self, *a, **k):
        raise RuntimeError("ollama is down")


def _run(coro):
    return asyncio.run(coro)


def test_foreign_and_franchise_tags_are_removed():
    llm = FakeLLM({"remove": [
        {"tag": "zettai_ryouiki", "reason": "wrong_track"},
        {"tag": "pokemon_(creature)", "reason": "franchise"},
    ]})
    kept, removed = _run(clean_track(BACKGROUND, "background", llm))
    names = [r["tag"] for r in kept]
    assert "zettai_ryouiki" not in names
    assert "pokemon_(creature)" not in names
    assert {r["reason"] for r in removed} == {"wrong_track", "franchise"}


def test_the_strange_tail_survives():
    """`chandelier_of_bone` is exactly what the 0.15 threshold is for."""
    llm = FakeLLM({"remove": [{"tag": "zettai_ryouiki", "reason": "wrong_track"}]})
    kept, _ = _run(clean_track(BACKGROUND, "background", llm))
    assert "chandelier_of_bone" in [r["tag"] for r in kept]


def test_a_request_to_gut_the_list_is_discarded_whole():
    """Past half the list the model has stopped reviewing and started curating."""
    llm = FakeLLM({"remove": [{"tag": r["tag"], "reason": "wrong_track"}
                              for r in BACKGROUND[:5]]})
    kept, removed = _run(clean_track(BACKGROUND, "background", llm))
    assert kept == BACKGROUND
    assert removed == []


def test_tags_the_model_invented_are_ignored():
    llm = FakeLLM({"remove": [{"tag": "something_never_offered", "reason": "artifact"}]})
    kept, removed = _run(clean_track(BACKGROUND, "background", llm))
    assert kept == BACKGROUND
    assert removed == []


def test_a_bare_string_list_is_accepted():
    llm = FakeLLM({"remove": ["zettai_ryouiki"]})
    kept, removed = _run(clean_track(BACKGROUND, "background", llm))
    assert "zettai_ryouiki" not in [r["tag"] for r in kept]
    assert removed[0]["reason"] == "wrong_track"


def test_an_unknown_reason_is_normalised():
    llm = FakeLLM({"remove": [{"tag": "rain", "reason": "i just do not like it"}]})
    _, removed = _run(clean_track(BACKGROUND, "background", llm))
    assert removed[0]["reason"] == "wrong_track"


def test_a_dead_model_loses_nothing():
    kept, removed = _run(clean_track(BACKGROUND, "background", BrokenLLM()))
    assert kept == BACKGROUND
    assert removed == []


def test_unparseable_output_loses_nothing():
    kept, removed = _run(clean_track(BACKGROUND, "background", FakeLLM("not json at all")))
    assert kept == BACKGROUND
    assert removed == []


def test_an_empty_track_is_left_alone():
    assert _run(clean_track([], "background", FakeLLM({"remove": []}))) == ([], [])


def test_the_prompt_states_the_track_and_forbids_over_removal():
    llm = FakeLLM({"remove": []})
    _run(clean_track(BACKGROUND, "background", llm, theme="雨の日の図書室"))
    assert "SETTING" in llm.prompt
    assert "雨の日の図書室" in llm.prompt
    assert "Those tags are wanted." in llm.prompt
    assert "Removing too much is a worse mistake" in llm.prompt
    assert "zettai_ryouiki" in llm.prompt


def test_the_locked_character_reaches_the_prompt():
    llm = FakeLLM({"remove": []})
    _run(clean_track(BACKGROUND, "person", llm, identity_tags=["black_hair", "brown_eyes"]))
    assert "Fixed character" in llm.prompt
    assert "black_hair" in llm.prompt


def test_each_track_is_told_what_foreign_means_for_it():
    bg, person = FakeLLM({"remove": []}), FakeLLM({"remove": []})
    _run(clean_track(BACKGROUND, "background", bg))
    _run(clean_track(BACKGROUND, "person", person))
    assert "CHARACTER" in person.prompt and "simple_background" in person.prompt
    assert "SETTING" in bg.prompt and "zettai_ryouiki" in bg.prompt

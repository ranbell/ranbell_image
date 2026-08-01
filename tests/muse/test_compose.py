"""The prompt is filled slot by slot, and each slot has a budget.

Asked for "thirty tags" the model padded with synonyms of whatever it found
most interesting — a pool theme came back with `swimwear`, `black_bikini` and
`bikini`, one fact spent three times, and the render weighted it three times.
A budget per aspect makes that impossible: outfit gets its cap whether or not
outfit is the interesting part, and place gets its own.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.compose import compose_slots, locked_slots
from app.muse.slots import BY_KEY

CHARACTER = {
    "identity_tags": ["1girl", "black_hair", "very_long_hair", "brown_eyes", "petite"],
    "outfit_tags": ["cardigan", "long_skirt"],
    "prop_tags": ["glasses"],
}

WRITTEN = {
    "emotion": "melancholic, tired",
    "outfit": "cardigan, long_skirt",
    "action": "pushing_cart, looking_down",
    "accessories": "glasses",
    "place": "library, bookshelf, rain",
    "object": "book, book_cart, lamp",
    "light": "dim_lighting",
}


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""

    async def generate_text(self, prompt, model=None, options=None, fmt=None):
        self.prompt = prompt
        return json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload

    async def embed(self, text):
        return [1.0, 0.0, 0.0]


class FakeDB:
    def __init__(self, hits=()):
        self.hits = list(hits)

    async def search_wd14_vocab(self, vec, **kw):
        return list(self.hits)


def _compose(payload=WRITTEN, character=CHARACTER, db=None, supplement=False):
    llm = FakeLLM(payload)
    out = asyncio.run(compose_slots(
        "雨の日の図書室", character, llm, model="m", db=db, supplement=supplement,
    ))
    return out, llm


def _tags(out, slot):
    return [r["tag"] for r in out.get(slot) or []]


def test_every_composed_slot_comes_back():
    out, _ = _compose()
    for key in ("emotion", "outfit", "action", "accessories", "place", "object"):
        assert _tags(out, key), f"{key} is empty"


def test_no_slot_may_exceed_its_cap():
    """This is the whole mechanism. A model that writes ten outfit tags gets
    the cap, and the other aspects keep their own budget regardless."""
    flooded = {**WRITTEN, "outfit": ", ".join(
        f"garment_{i}" for i in range(20)
    )}
    out, _ = _compose(flooded)
    assert len(_tags(out, "outfit")) <= BY_KEY["outfit"].cap


def test_restatements_inside_a_slot_are_dropped():
    """`swimwear, black_bikini, bikini` is one fact written three times."""
    out, _ = _compose({**WRITTEN, "outfit": "swimwear, black_bikini, bikini, sun_hat"})
    kept = _tags(out, "outfit")
    assert "sun_hat" in kept, "the budget should reach a second fact"
    assert len([t for t in kept if "bikini" in t]) <= 1


def test_appearance_contradicting_the_locked_character_is_dropped():
    out, _ = _compose({**WRITTEN, "outfit": "blue_hair, cardigan"})
    assert "blue_hair" not in _tags(out, "outfit")
    assert "cardigan" in _tags(out, "outfit")


def test_junk_never_survives():
    out, _ = _compose({**WRITTEN, "place": "library, no_humans, black_border"})
    assert _tags(out, "place") == ["library"]


def test_the_prompt_names_every_aspect_and_its_limit():
    _, llm = _compose()
    for key in ("emotion", "outfit", "place", "object"):
        assert BY_KEY[key].label in llm.prompt
    assert f"at most {BY_KEY['outfit'].cap}" in llm.prompt
    assert "one fact spent three times" in llm.prompt or "swimwear" in llm.prompt


def test_the_prompt_locks_the_character():
    _, llm = _compose()
    assert "FIXED" in llm.prompt and "black_hair" in llm.prompt


def test_the_prompt_leaves_framing_to_somebody_else():
    _, llm = _compose()
    assert "no framing words" in llm.prompt


# ── vocabulary supplement ───────────────────────────────────────────────────
def test_a_short_slot_is_topped_up_from_the_vocabulary():
    db = FakeDB([
        {"name": "hair_ribbon", "score": 0.6},   # an accessory
        {"name": "library", "score": 0.5},       # wrong slot, must be refused
    ])
    out, _ = _compose({**WRITTEN, "accessories": "glasses"}, db=db, supplement=True)
    tags = _tags(out, "accessories")
    assert "hair_ribbon" in tags
    assert "library" not in tags, "retrieval may only fill the slot it was asked about"
    assert any(r["source"] == "vocab" for r in out["accessories"])


def test_a_full_slot_is_left_alone():
    db = FakeDB([{"name": "hair_ribbon", "score": 0.9}])
    full = ", ".join(f"item_{i}" for i in range(BY_KEY["accessories"].cap))
    out, _ = _compose({**WRITTEN, "accessories": full}, db=db, supplement=True)
    assert all(r["source"] == "compose" for r in out["accessories"])


def test_the_supplement_can_be_turned_off():
    db = FakeDB([{"name": "hair_ribbon", "score": 0.9}])
    out, _ = _compose({**WRITTEN, "accessories": "glasses"}, db=db, supplement=False)
    assert _tags(out, "accessories") == ["glasses"]


def test_a_dead_model_still_returns_the_slot_keys():
    out, _ = _compose("not json at all")
    assert set(out) >= {"emotion", "outfit", "place"}


# ── the locked slots ────────────────────────────────────────────────────────
def test_the_character_slots_come_off_the_preset():
    locked = locked_slots(CHARACTER)
    character = [r["tag"] for r in locked["character"]]
    body = [r["tag"] for r in locked["body"]]
    assert "1girl" in character and "black_hair" in character
    assert "petite" in body, "body words belong in Body, not Character"
    assert "petite" not in character


def test_description_stays_a_sentence():
    """Splitting it on commas and underscoring the pieces turned a sentence
    into one enormous tag, which is neither."""
    out, _ = _compose({**WRITTEN,
                       "description": "A pink haired girl walks along a row of cherry trees."})
    assert _tags(out, "description") == [
        "A pink haired girl walks along a row of cherry trees."
    ]


def test_an_exclusive_slot_the_model_answered_is_left_alone():
    """One face has one expression. Retrieval topping this up put
    `expressionless` next to `happy` — a second answer, not a top-up."""
    db = FakeDB([{"name": "expressionless", "score": 0.7}])
    out, _ = _compose({**WRITTEN, "emotion": "happy"}, db=db, supplement=True)
    assert _tags(out, "emotion") == ["happy"]


def test_an_exclusive_slot_the_model_left_empty_is_still_filled():
    db = FakeDB([{"name": "smile", "score": 0.7}])
    out, _ = _compose({**WRITTEN, "emotion": ""}, db=db, supplement=True)
    assert _tags(out, "emotion") == ["smile"]


def test_the_supplement_refuses_what_contradicts_the_character():
    db = FakeDB([{"name": "blue_hair", "score": 0.9}, {"name": "hair_ribbon", "score": 0.8}])
    out, _ = _compose({**WRITTEN, "accessories": ""}, db=db, supplement=True)
    assert "blue_hair" not in _tags(out, "accessories")

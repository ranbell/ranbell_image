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
    # Outfit is full, so Accessories is the first short slot that accepts it.
    full_outfit = ", ".join(f"garment_{i}" for i in range(BY_KEY["outfit"].cap))
    out, _ = _compose({**WRITTEN, "outfit": full_outfit, "accessories": "glasses"},
                      db=db, supplement=True)
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


# ── the vocabulary band ─────────────────────────────────────────────────────
def test_the_supplement_refuses_a_weak_hit_even_when_the_slot_accepts_it():
    """This step takes the first thing its slot accepts, however far down the
    list. A stargazing theme reached rank 22, 31 and 37 of 40 to find `katana`,
    `umbrella` and `sword` — the only PROPS common enough to clear the old
    frequency floor, all at 0.37 while the real hits sat at 0.45."""
    db = FakeDB([{"name": "katana", "score": 0.38}, {"name": "telescope", "score": 0.46}])
    # Accessories claims both words too, so fill it and let Object be the short one.
    full = ", ".join(f"item_{i}" for i in range(BY_KEY["accessories"].cap))
    out, _ = _compose({**WRITTEN, "accessories": full, "object": ""},
                      db=db, supplement=True)
    tags = _tags(out, "object")
    assert "telescope" in tags
    assert "katana" not in tags


def test_the_supplement_asks_for_a_band_that_contains_specific_nouns():
    """`min_freq=0.01` is 51,000 posts: it excluded `telescope` (1,319) and
    `oven` (653) while admitting `sword` (235,273). The floor was selecting for
    genericness, which is how a bakery got a sword."""
    from app.muse import vocab

    class Recording(FakeDB):
        def __init__(self):
            super().__init__([])
            self.kw = {}

        async def search_wd14_vocab(self, vec, **kw):
            self.kw = kw
            return []

    db = Recording()
    _compose({**WRITTEN, "object": ""}, db=db, supplement=True)
    assert db.kw["min_freq"] == vocab.MIN_FREQ < 0.001


# ── filing and settling ─────────────────────────────────────────────────────
def test_a_prop_written_as_an_action_is_moved_to_where_it_belongs():
    """A stargazing theme put `binoculars` under Action, spending a pose on a
    prop and leaving the pose unwritten."""
    out, _ = _compose({**WRITTEN, "action": "standing, binoculars"})
    assert "binoculars" not in _tags(out, "action")
    assert "binoculars" in _tags(out, "accessories")


def test_a_tag_its_own_slot_accepts_is_left_where_it_was_put():
    """`PROPS` belongs to Accessories and Object both, on purpose. Preferring
    the first claimant would drag every prop out of the room and into her
    hands."""
    out, _ = _compose({**WRITTEN, "object": "book, lamp"})
    assert "book" in _tags(out, "object")


def test_a_later_aspect_may_not_reopen_a_settled_one():
    """Place said `night` and Light said `twilight`, and the render averaged
    two hours that cannot both be true. Contradiction was checked inside a slot
    and against the character, never across slots."""
    out, _ = _compose({**WRITTEN, "place": "hill, night", "light": "twilight, starlight"})
    assert "night" in _tags(out, "place")
    assert "twilight" not in _tags(out, "light")
    assert "starlight" in _tags(out, "light")


def test_a_description_written_as_a_tag_is_put_back_into_prose():
    """Told "underscore_format" at the top of the rules, the model obeys it for
    the sentence too."""
    out, _ = _compose({**WRITTEN,
                       "description": "a_slim_girl_is_looking_through_a_telescope"})
    assert _tags(out, "description") == ["a slim girl is looking through a telescope"]


def test_one_fact_is_not_spent_across_three_aspects():
    """The budgets exist to stop this and only ever stopped it inside a slot.
    A stargazing theme wrote `looking_through_telescope`, `telescope` and
    `large_telescope` into three aspects, and the render weighted the telescope
    three times."""
    out, _ = _compose({**WRITTEN,
                       "action": "looking_through_telescope",
                       "accessories": "telescope",
                       "object": "large_telescope, cardboard_box"})
    named = [t for rows in out.values() for r in [rows] for t in
             [x["tag"] for x in r] if "telescope" in t]
    assert named == ["looking_through_telescope"], "the first aspect keeps it"
    assert "cardboard_box" in _tags(out, "object"), "the budget reaches a second fact"


def test_the_vocabulary_spends_a_hit_once():
    """Slots share catalog sets on purpose, so the same tag is acceptable to
    several of them and every short one took a copy — `hair_ribbon` landed in
    Outfit and Accessories both, one fact holding two budgets."""
    db = FakeDB([{"name": "hair_ribbon", "score": 0.9}])
    out, _ = _compose({**WRITTEN, "outfit": "cardigan", "accessories": "glasses"},
                      db=db, supplement=True)
    homes = [k for k in ("outfit", "accessories") if "hair_ribbon" in _tags(out, k)]
    assert len(homes) == 1, f"spent in {homes}"


# ── the person, not just the face ───────────────────────────────────────────
PERSONALITY = {
    **CHARACTER,
    "personality": {
        "traits": ["patient", "solitary"],
        "summary": "Waits hours for one clear minute.",
        "inner": ["prefers things that take a long time to arrive"],
        "likes": ["thermos coffee", "clear cold nights"],
        "dislikes": ["city glow"],
        "outfit_style": "long coat over layers",
    },
    "expression_vocab": ["parted_lips"],
    "gesture_vocab": ["looking_up"],
    "palette": ["indigo", "silver"],
}


def test_the_prompt_says_who_she_is_and_not_only_what_she_looks_like():
    """Everything below was already on the preset and nothing asked for it, so
    the patient solitary observer got `Emotion: blush` and her thermos never
    reached a cold hilltop."""
    _, llm = _compose(character=PERSONALITY)
    for fragment in ("patient, solitary", "Waits hours for one clear minute.",
                     "thermos coffee", "city glow", "parted_lips",
                     "looking_up", "indigo, silver", "long coat over layers"):
        assert fragment in llm.prompt, fragment


def test_a_character_with_no_personality_still_composes():
    _, llm = _compose(character=CHARACTER)
    assert "THE CHARACTER" in llm.prompt


def test_the_prompt_asks_for_parts_but_keeps_the_build_fixed():
    _, llm = _compose(character=PERSONALITY)
    assert "build word like slim" in llm.prompt
    assert "name the legs and name them wet" in llm.prompt

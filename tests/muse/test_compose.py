"""Composing the parts into one paragraph, with no memory of anything.

The composer is the one turn allowed to write flowing prose, and the whole
reason it is trustworthy is what it is NOT given. It sees the shot as it stands
and nothing else — no conversation, no theme, no brief, no previous prompt, no
board image. Composing was never the thing that went wrong. Being handed twenty
turns of contradicting history was, and a composer with no history cannot be
confused by one.

The first test in this file is the load-bearing one: it asserts on the prompt
string itself, because "we do not pass the transcript" is a claim that quietly
stops being true the first time somebody adds a helpful block.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, facets, service, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402

COMPOSED = (
    "SCENE: An empty classroom in late afternoon, low sun through the glass, "
    "desks in rows and a chalkboard behind her, a white blouse and a pleated "
    "skirt, standing with her weight on one hip, a small closed smile, shot "
    "from below."
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


class ComposingOllama(FakeOllama):
    def __init__(self, answer: str = COMPOSED):
        super().__init__()
        self.answer = answer
        self.compose_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        text = "SAY: はい。"
        if "script supervisor writing the shot up" in str(kw.get("system") or ""):
            self.compose_prompts.append(str(prompt))
            text = self.answer

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


async def _shot(db) -> dict:
    s = await _duet_session(db)
    s["mode"] = "duet"
    facets.write(s, "place", tags="classroom, window", nl="An empty classroom.")
    facets.write(s, "hour", tags="late_afternoon", nl="Late afternoon.")
    facets.write(s, "light", tags="sunlight", nl="Low sun through the glass.")
    facets.write(s, "props", tags="desk, chalkboard", nl="Desks and a chalkboard.")
    facets.write(s, "costume", tags="white_blouse, pleated_skirt",
                 nl="A white blouse and a pleated skirt.")
    facets.write(s, "pose", tags="standing", nl="Standing, weight on one hip.")
    facets.write(s, "expression", tags="smile", nl="A small closed smile.")
    facets.write(s, "camera", tags="from_below", nl="Shot from below.")
    service._reassemble(s)
    await session_db.save(db, s)
    return s


# ── the prompt is the table and nothing else ────────────────────────────────

@pytest.mark.asyncio
async def test_the_composer_is_given_the_shot_and_no_history_at_all():
    db, ollama = FakeDb(), ComposingOllama()
    s = await _shot(db)
    s["chat"].append({"id": "x", "role": "user", "name": "総監督",
                      "text": "膝のあいだの隙間がいいね", "at": 0})
    s["notes"] = ["冬にして", "やっぱり夏"]
    s["inputs"]["theme"] = "深夜のカラオケで一人"
    s["brief"] = "REFERENCE ONLY block and a character sheet"

    await service.compose_scene_if_needed(db, ollama, s)

    prompt = ollama.compose_prompts[-1]
    for leak in ("膝のあいだ", "冬にして", "やっぱり夏", "カラオケ",
                 "REFERENCE", "ここまでの会話", "STANDING ORDERS"):
        assert leak not in prompt, f"{leak!r} reached the composer"
    # What it IS given.
    assert "An empty classroom." in prompt
    assert "CAMERA TAGS:" in prompt and "from_below" in prompt


@pytest.mark.asyncio
async def test_the_standing_rules_are_the_one_thing_carried_in():
    """They are not history — they are true of the picture being written."""
    db, ollama = FakeDb(), ComposingOllama()
    s = await _shot(db)
    s["standing"] = ["足は絶対に映さない"]
    await service.compose_scene_if_needed(db, ollama, s)
    assert "足は絶対に映さない" in ollama.compose_prompts[-1]


# ── the result ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_composed_paragraph_becomes_the_scene():
    db, ollama = FakeDb(), ComposingOllama()
    s = await _shot(db)
    assert s["craft"]["scene"] == facets.nl_join(facets.table_of(s))

    await service.compose_scene_if_needed(db, ollama, s)
    assert s["craft"]["scene"].startswith("An empty classroom in late afternoon")
    assert s["craft"]["scene"] in s["craft"]["prompt"]
    assert s["composed"]["rev"] == facets.table_rev(facets.table_of(s))


@pytest.mark.asyncio
async def test_an_unchanged_shot_is_never_composed_twice():
    db, ollama = FakeDb(), ComposingOllama()
    s = await _shot(db)
    await service.compose_scene_if_needed(db, ollama, s)
    await service.compose_scene_if_needed(db, ollama, s)
    assert len(ollama.compose_prompts) == 1

    facets.write(s, "camera", tags="from_above", nl="Shot from above.")
    await service.compose_scene_if_needed(db, ollama, s)
    assert len(ollama.compose_prompts) == 2


@pytest.mark.asyncio
async def test_a_composition_naming_a_refused_thing_is_thrown_away():
    """The one list where a leak actually reaches a render."""
    db = FakeDb()
    ollama = ComposingOllama("SCENE: An empty classroom, and her jacket.")
    s = await _shot(db)
    s["banned"] = ["jacket"]
    joined = s["craft"]["scene"]

    await service.compose_scene_if_needed(db, ollama, s)
    assert s["craft"]["scene"] == joined, "the shot falls back to the joined parts"
    assert "jacket" not in s["craft"]["prompt"]


@pytest.mark.asyncio
async def test_a_composer_writing_its_own_scene_is_thrown_away():
    db = FakeDb()
    ollama = ComposingOllama(
        "SCENE: A harbour crowded with trawlers, gulls wheeling above rusted "
        "derricks, nets heaped on wet cobbles beside stacked lobster creels, "
        "buoys, tarpaulins, winches and a listing dinghy."
    )
    s = await _shot(db)
    joined = s["craft"]["scene"]
    await service.compose_scene_if_needed(db, ollama, s)
    assert s["craft"]["scene"] == joined


@pytest.mark.asyncio
async def test_a_composer_that_cannot_answer_leaves_the_shot_renderable():
    class MuteOllama(FakeOllama):
        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": ""}
            return _stream()

    db = FakeDb()
    s = await _shot(db)
    joined = s["craft"]["scene"]
    await service.compose_scene_if_needed(db, MuteOllama(), s)
    assert s["craft"]["scene"] == joined
    assert s["craft"]["prompt"]


@pytest.mark.asyncio
async def test_the_crewed_studio_still_densifies_instead():
    db, ollama = FakeDb(), ComposingOllama()
    s = await _shot(db)
    s["mode"] = ""
    await service.compose_scene_if_needed(db, ollama, s)
    assert ollama.compose_prompts == []


# ── the parser ──────────────────────────────────────────────────────────────

def test_a_paragraph_that_arrived_in_pieces_is_flattened():
    """"No headings" is a request; this is what happens when it is ignored."""
    assert chain.parse_compose("SCENE: one line\n  and\n\nanother") == \
        "one line and another"


def test_a_bare_paragraph_with_no_label_is_still_read():
    assert chain.parse_compose("An empty classroom.") == "An empty classroom."


def test_nothing_composes_to_nothing():
    assert chain.parse_compose("") == ""


# ── W-Muse: compose knows there are two of them ─────────────────────────────
# 2026-08-11's real-session report found compose actively harmful for W-Muse:
# `facets._vocabulary()` only read the eight A-side facets, so any legitimate
# mention of the second Muse read as an "invented" word and the composition
# was discarded — every W-Muse render fell back to `nl_join`'s raw
# concatenation, which is what actually produced the "one Muse dominant, the
# other barely present, prose incoherent" images.

async def _w_duet_session(db, **over):
    session = await _duet_session(db, partner_preset="c2", **over)
    session["character"]["name_ja"] = "倉田 あさひ"
    session["partner_character"] = {
        "character_id": "c2", "name_ja": "みなも",
        "identity_tags": ["1girl", "black_hair"],
        "personality": {}, "palette": [], "signature_prop": "",
    }
    await session_db.save(db, session)
    return session


async def _w_shot(db) -> dict:
    s = await _w_duet_session(db)
    s["mode"] = "duet"
    facets.write(s, "place", tags="rooftop", nl="A rooftop laundry area.")
    facets.write(s, "hour", tags="morning", nl="Saturday morning.")
    facets.write(s, "light", tags="sunlight", nl="Bright direct sun.")
    facets.write(s, "props", tags="clothesline", nl="A clothesline.")
    facets.write(s, "costume", tags="t_shirt", nl="Asahi wears a plain white t-shirt.")
    facets.write(s, "costume_b", tags="straw_hat, sundress",
                 nl="Minamo wears a pale sundress and a straw hat.")
    facets.write(s, "pose", tags="standing", nl="Asahi stands by the line.")
    facets.write(s, "pose_b", tags="standing", nl="Minamo stands beside her.")
    facets.write(s, "expression", tags="smile", nl="Asahi is smiling.")
    facets.write(s, "expression_b", tags="smile", nl="Minamo is smiling too.")
    facets.write(s, "camera", tags="wide_shot, standing_side_by_side, looking_at_each_other",
                 nl="A wide shot of the two of them standing side by side, looking at each other.")
    service._reassemble(s)
    await session_db.save(db, s)
    return s


@pytest.mark.asyncio
async def test_w_muse_composer_is_told_both_names_and_to_write_both():
    db, ollama = FakeDb(), ComposingOllama("SCENE: The two of them share the rooftop light.")
    s = await _w_shot(db)
    await service.compose_scene_if_needed(db, ollama, s)

    system = str(ollama.calls[-1].get("system") or "")
    assert "倉田 あさひ" in system and "みなも" in system
    assert "genuinely comparable share" in system
    assert "as something that happens between" in system


def test_solo_compose_system_is_unchanged():
    """`COMPOSE_SYSTEM` (no partner) stays exactly what a solo session always
    saw — the constant every solo call site still reads directly."""
    assert chain.COMPOSE_SYSTEM == chain.compose_system()
    assert "みなも" not in chain.COMPOSE_SYSTEM
    assert "BOTH" not in chain.COMPOSE_SYSTEM


@pytest.mark.asyncio
async def test_the_vocabulary_check_knows_the_second_muses_facets():
    """The bug: `_vocabulary` only read the eight A-side facets, so any real
    mention of B's own words (her straw hat, her sundress) counted as
    'invented' and a correct composition was thrown away for describing her
    at all."""
    db = FakeDb()
    ollama = ComposingOllama(
        "SCENE: Asahi stands by the line in her white t-shirt, smiling, "
        "while Minamo, in her pale sundress and straw hat, stands close "
        "beside her and smiles too, the two of them looking at each other "
        "in the bright morning light."
    )
    s = await _w_shot(db)
    await service.compose_scene_if_needed(db, ollama, s)
    # The composition must have been ACCEPTED, not thrown away for inventing B.
    assert s["craft"]["scene"].startswith("Asahi stands by the line")
    assert "sundress" in s["craft"]["scene"] and "Minamo" in s["craft"]["scene"]

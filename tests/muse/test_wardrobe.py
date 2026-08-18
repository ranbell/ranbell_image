"""衣装部屋 — the button that restates the whole outfit instead of editing it.

What is actually under test is the escape hatch from a measured failure: the
compile writes `wearing` as a delta off one line of direction, that lands about
four times in five on a short line and much less on a long one, and a miss is
silent — the garment simply stays on. These tests hold the properties that make
the button worth pressing: the outfit is REPLACED not merged, what left is
struck so nothing puts it back, the card cannot disagree with the notebook, and
a turn that produced no wearable answer says so out loud.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew, notebook, service, session_db  # noqa: E402
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


class WardrobeOllama:
    """Answers the wardrobe turn; anything else would be a different test."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.prompts.append(prompt)
        reply = self.reply

        async def _stream():
            yield {"type": "token", "text": reply}
        return _stream()

    def unload(self, *a, **kw):
        return None


async def _wardrobe_session(db, wearing: str, **over):
    session = await _duet_session(db, **over)
    nb = notebook.of(session)
    notebook.apply_patch(nb, {
        "wearing": wearing,
        "scene": "school rooftop, late afternoon",
        "beat": "standing by the fence",
    })
    session["notebook"] = nb
    await session_db.save(db, session)
    return session


# ── the two lines ───────────────────────────────────────────────────────────

def test_parse_reads_both_labels():
    say, wearing = chain.parse_wardrobe(
        "SAY: 着替えてきたよ。\nWEARING: sailor_fuku, cardigan, loafers"
    )
    assert say == "着替えてきたよ。"
    assert wearing == "sailor_fuku, cardigan, loafers"


def test_parse_keeps_her_voice_when_she_forgets_the_label():
    """The outfit half must parse; the voice half falling back is not silence."""
    say, wearing = chain.parse_wardrobe(
        "着替えてきた。ちょっと寒いかも。\nWEARING: cardigan, skirt"
    )
    assert "着替えてきた" in say
    assert wearing == "cardigan, skirt"


def test_parse_survives_a_turn_with_no_outfit_at_all():
    say, wearing = chain.parse_wardrobe("SAY: ……なんて言えばいいのかな。")
    assert wearing == ""
    assert say


# ── the outfit is replaced, not merged ──────────────────────────────────────

@pytest.mark.asyncio
async def test_the_whole_outfit_is_replaced_and_the_coat_stays_off():
    db = FakeDb()
    session = await _wardrobe_session(db, "sailor_fuku, coat, loafers")
    ollama = WardrobeOllama(
        "SAY: コート、置いてきたよ。\nWEARING: sailor_fuku, loafers"
    )

    session = await service.wardrobe_stage(db, ollama, session)

    wearing = str(notebook.of(session).get("wearing") or "")
    assert "coat" not in wearing
    assert "sailor_fuku" in wearing and "loafers" in wearing
    # What keeps the coat out of the next take is the notebook, not a memory of
    # the removal: the weave builds the bag from `wearing`, and
    # `drop_garments_not_in_wearing` drops what is no longer in it. Banishing
    # words here is what turned a rewording into a permanent ban.
    bag = notebook.drop_garments_not_in_wearing(
        "sailor_fuku, coat, loafers", wearing=wearing,
    )
    assert "coat" not in bag
    assert session["status"] == "chat"


@pytest.mark.asyncio
async def test_her_line_reaches_the_room():
    db = FakeDb()
    session = await _wardrobe_session(db, "sailor_fuku")
    ollama = WardrobeOllama("SAY: カーディガン、羽織ってきた。\nWEARING: sailor_fuku, cardigan")

    session = await service.wardrobe_stage(db, ollama, session)

    said = [m for m in session["chat"] if m.get("role") == "muse"]
    assert said and "カーディガン" in said[-1]["text"]


@pytest.mark.asyncio
async def test_the_stale_notebook_line_is_offered_but_the_conversation_is_too():
    """She is handed both, and told which one is authoritative."""
    db = FakeDb()
    session = await _wardrobe_session(db, "sailor_fuku, coat")
    session["chat"] = [
        {"id": "1", "role": "user", "name": "総監督", "text": "コート脱いで", "turns": []},
    ]
    ollama = WardrobeOllama("SAY: 脱いだよ。\nWEARING: sailor_fuku")

    await service.wardrobe_stage(db, ollama, session)

    prompt = ollama.prompts[0]
    assert "NOTEBOOK WEARING" in prompt
    assert "コート脱いで" in prompt


# ── nothing wearable came back ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unreadable_answer_says_so_and_changes_nothing():
    db = FakeDb()
    session = await _wardrobe_session(db, "sailor_fuku, coat")
    ollama = WardrobeOllama("SAY: ……えっと。\nWEARING: none")

    session = await service.wardrobe_stage(db, ollama, session)

    assert str(notebook.of(session).get("wearing") or "") == "sailor_fuku, coat"
    system = [m for m in session["chat"] if m.get("kind") == "system"]
    assert system, "a button that did nothing has to say it did nothing"


# ── the card cannot disagree with the notebook ──────────────────────────────

def test_card_and_notebook_say_the_same_outfit():
    """A stale HERO/LAYERS is the coat waiting to be seeded back in."""
    session = {"costume": {
        "hero": "trench coat",
        "layers": "sailor fuku under a trench coat",
        "garments": "top=sailor_fuku, coat / bottom=navy_skirt / feet=loafers",
        "tags": ["sailor_fuku", "coat", "navy_skirt", "loafers"],
        "fabric": "heavy wool",
    }}

    service._sync_costume_wearing(session, "sailor_fuku, navy_skirt, loafers")

    costume = session["costume"]
    assert "coat" not in costume["garments"]
    assert "coat" not in costume["layers"]
    assert "coat" not in str(costume["hero"])
    assert "coat" not in " ".join(costume["tags"])
    # Texture is not an outfit and is nobody's business here.
    assert costume["fabric"] == "heavy wool"


def test_a_hero_still_being_worn_is_left_alone():
    session = {"costume": {"hero": "sailor_fuku", "garments": "top=sailor_fuku"}}
    service._sync_costume_wearing(session, "sailor_fuku, loafers")
    assert session["costume"]["hero"] == "sailor_fuku"


def test_a_room_with_no_card_is_not_given_one():
    """主演撮り keeps no COSTUME card — the notebook is the whole outfit."""
    session = {}
    service._sync_costume_wearing(session, "sailor_fuku")
    assert not session.get("costume")


# ── pressed again, and again ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_button_frees_a_garment_someone_banished():
    """It is the way out of a wardrobe that has gone wrong, so it un-banishes.

    Measured live: a rephrase nobody asked for had struck `blouse` and `white`,
    and because the match walks word parts that also blocked white_shirt,
    white_dress and white_hair. She could name a white blouse here all day and
    the weave would drop it straight back out.
    """
    db = FakeDb()
    session = await _wardrobe_session(db, "sailor_fuku")
    session["struck"] = ["blouse", "white", "empty_can"]
    ollama = WardrobeOllama(
        "SAY: 白いブラウスに着替えたよ。\nWEARING: white_blouse, navy_skirt"
    )

    session = await service.wardrobe_stage(db, ollama, session)

    struck = [str(s) for s in session.get("struck") or []]
    assert "blouse" not in struck and "white" not in struck
    assert "empty_can" in struck, "only what she is now wearing is freed"


@pytest.mark.asyncio
async def test_repeated_presses_do_not_grow_struck_without_limit():
    """The button invites being pressed until the outfit is right."""
    db = FakeDb()
    session = await _wardrobe_session(db, "sailor_fuku, coat, loafers")
    outfits = [
        "sailor_fuku, loafers",
        "sailor_fuku, cardigan, loafers",
        "sailor_fuku, coat, loafers",
        "sailor_fuku, loafers",
    ]
    for outfit in outfits:
        session = await service.wardrobe_stage(
            db, WardrobeOllama(f"SAY: これでどう？\nWEARING: {outfit}"), session,
        )

    struck = [str(s) for s in session.get("struck") or []]
    assert len(struck) <= 40
    # Nothing she is wearing may be on the never-restore list, however many
    # times the button is pressed. That is what made 「やっぱりコート着て」
    # impossible to obey after a removal.
    worn = str(notebook.of(session).get("wearing") or "")
    assert not any(s in worn for s in struck)


# ── the prompt she is given ─────────────────────────────────────────────────

def test_the_wardrobe_turn_asks_for_two_lines_and_no_shot():
    system = crew.actress_duet_prompt(
        {"name": "Asahi", "name_ja": "倉田あさひ"}, mode="wardrobe", seed="s",
    )
    assert "WEARING:" in system and "SAY:" in system
    # Nothing else about the shot is hers this turn.
    assert "TAGS:" not in system
    assert "COSTUME:" not in system

"""A refusal has to change state, not add a sentence.

Reported by the Showrunner: after talking about cleaning, the art department
added a rag; saying "don't use that" never removed it. And a garment they had
said they were finished with kept coming up in the crew's comments.

Three separate causes, all pulling the same way:

- the refusal was stored as a standing order in the Showrunner's own words and
  re-read by every seat on every turn, so the refused noun stayed in front of
  everybody forever and the crew kept discussing it
- nothing could delete a prop the art department had added — the only removal
  paths were the planner dropping its own ledger item, and Wardrobe replacing
  the outfit
- the sampler never heard about it: the negative prompt is built from settings,
  never from what the Showrunner said

So saying "no" made the thing *more* present. These tests pin the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import brief, chain, runtime, service, session_db
from tests.muse.test_service import (  # noqa: E402
    FakeComfy, FakeDb, FakeOllama, FakeSpooler,
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


# ── the closed list ─────────────────────────────────────────────────────────
def test_the_clerk_can_only_pick_tags_that_are_already_there():
    """The whole reason this is its own turn. A wrong answer can only ever be a
    smaller answer — never an invented noun."""
    present = ["cleaning_rag", "bucket", "wooden_floor"]
    removed, restored = chain.parse_strike(
        "REMOVE: cleaning_rag, mop, broom\nRESTORE: none", present, [],
    )
    assert removed == ["cleaning_rag"], "mop and broom are not in the script"
    assert restored == []


def test_an_answer_in_the_wrong_language_removes_nothing():
    """The note is Japanese and the tags are English. A clerk that answers in
    Japanese has not named anything the script contains."""
    removed, _ = chain.parse_strike(
        "REMOVE: 雑巾\nRESTORE: none", ["cleaning_rag", "bucket"], [],
    )
    assert removed == []


def test_a_note_that_removes_nothing_is_the_normal_answer():
    assert chain.parse_strike("REMOVE: none\nRESTORE: none", ["a", "b"], []) == ([], [])


def test_something_can_be_asked_back():
    _, restored = chain.parse_strike(
        "REMOVE: none\nRESTORE: cleaning_rag", ["bucket"], ["cleaning_rag"],
    )
    assert restored == ["cleaning_rag"]


# ── carrying it out ─────────────────────────────────────────────────────────
def _session(tags: str) -> dict:
    return {
        "session_id": "s",
        "inputs": {"locale": "ja", "crew_preset": "standard", "framing": "auto"},
        "character": {"identity_tags": ["1girl", "silver_hair"]},
        "craft": {"tags": tags, "scene": "She stands.", "prompt": ""},
        "notes": [], "banned": [], "ledger": [],
    }


def test_a_refusal_takes_it_out_now_and_keeps_it_out():
    session = _session("standing, cleaning_rag, bucket, wooden_floor")
    removed, _ = service.apply_removals(session, ["cleaning_rag"], [])

    assert removed == ["cleaning_rag"]
    assert "cleaning_rag" not in session["craft"]["tags"]
    assert "bucket" in session["craft"]["tags"]
    assert session["banned"] == ["cleaning_rag"]
    # And the prompt was rebuilt, not left stale.
    assert "cleaning_rag" not in session["craft"]["prompt"]


def test_a_seat_reaching_for_it_again_is_filtered_not_argued_with():
    """Enforcement is a filter. Telling seats not to reintroduce something means
    naming it in their prompt every turn, which is the original disease."""
    session = _session("standing")
    service.apply_removals(session, [], [])
    session["banned"] = ["cleaning_rag"]

    kept = service.drop_banned(session, "standing, cleaning_rag, mop, bucket")
    assert "cleaning_rag" not in kept
    assert "mop" in kept and "bucket" in kept


def test_emphasis_cannot_smuggle_a_refused_tag_back_in():
    session = _session("standing")
    session["banned"] = ["cleaning_rag"]
    assert "cleaning_rag" not in service.drop_banned(
        session, "standing, (cleaning_rag:1.3)")


def test_the_sampler_finally_hears_the_word_no():
    """The negative prompt is the only place in the pipeline where "do not draw
    this" is a mechanism rather than a request."""
    session = _session("standing")
    session["banned"] = ["cleaning_rag", "mop"]
    negative = runtime.negative_for(session)
    assert "cleaning_rag" in negative and "mop" in negative


def test_the_render_path_and_the_service_build_the_same_negative():
    """`service` had a copy nothing called while the runner kept its own, so
    anything added to the service version reached no render at all."""
    assert service.negative_for is runtime.negative_for


def test_asking_for_it_back_un_bans_it():
    session = _session("standing, bucket")
    session["banned"] = ["cleaning_rag"]
    removed, restored = service.apply_removals(session, [], ["cleaning_rag"])
    assert restored == ["cleaning_rag"] and removed == []
    assert session["banned"] == []
    assert service.drop_banned(session, "cleaning_rag") == "cleaning_rag"


# ── what the crew is told ───────────────────────────────────────────────────
def test_the_refused_noun_is_named_once_and_then_never_again():
    """The seats answering the note need to know why something vanished, or they
    put it straight back. Nobody after them is told the noun at all."""
    notes = ["掃除してる感じで", "雑巾は使わないで"]
    now = brief.orders_block(notes, carried_out=[1], removed_now=["cleaning_rag"])
    assert "cleaning_rag" in now
    assert "雑巾は使わないで" not in now, "the note's own words are dropped"

    later = brief.orders_block(notes, carried_out=[1])
    assert "cleaning_rag" not in later
    assert "雑巾" not in later
    assert "掃除してる感じで" in later, "the rest of the direction survives"


def test_the_newest_order_wins_and_says_so():
    block = brief.orders_block(["冬にして", "やっぱり夏で"])
    assert block.index("やっぱり夏で") < block.index("冬にして")
    assert "NEWEST FIRST" in block


def test_every_seat_is_told_not_to_name_it_even_to_deny_it():
    from app.muse import crew
    for mid in ("propshop:takarabako", "wardrobe:shiwa", "beat:ichibyou"):
        text = crew.system_prompt_for(mid)
        assert "WHAT THE SHOWRUNNER HAS REFUSED" in text, mid
        assert "NOT IN SAY EITHER" in text, mid


# ── end to end through a note ───────────────────────────────────────────────
class StrikingOllama(FakeOllama):
    """Answers the clerk's question; everything else is an ordinary craft turn."""

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        if "CURRENT TAGS:" in str(prompt):
            text = "REMOVE: cleaning_rag\nRESTORE: none"
        else:
            text = ("SAY: はい。\n\nTAGS: standing, bucket, cleaning_rag\n\n"
                    "SCENE: She stands in the room.")

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


@pytest.mark.asyncio
async def test_a_note_removes_the_prop_and_the_next_seat_cannot_put_it_back():
    db, ollama = FakeDb(), StrikingOllama()
    session = await service.create_session(db, {
        "theme": "掃除している", "character_id": "c1",
        "workflow": "w.json", "model": "m",
    })
    session["character"] = {"identity_tags": ["1girl"], "personality": {},
                            "palette": [], "signature_prop": ""}
    session["craft"] = {"tags": "standing, cleaning_rag, bucket",
                        "scene": "x", "prompt": "", "pose_intent": ""}
    await session_db.save(db, session)

    removed, _ = await service.take_note(
        db, ollama, session, "雑巾は使わないで", cfg={},
    )

    assert removed == ["cleaning_rag"]
    assert "cleaning_rag" not in session["craft"]["tags"]
    # The note's index is marked, so its words leave the standing orders.
    assert session["carried_out"] == [0]
    assert "cleaning_rag" in runtime.negative_for(session)
    # A seat writing it again gets it stripped on the way in.
    turn = chain.MuseTurn(
        muse_id="propshop:takarabako", say="はい", prompt="p",
        pose_intent="", tags="standing, bucket, cleaning_rag", scene="x", raw="",
    )
    service._apply_turn(session, turn)
    assert "cleaning_rag" not in session["craft"]["tags"]
    assert "bucket" in session["craft"]["tags"]

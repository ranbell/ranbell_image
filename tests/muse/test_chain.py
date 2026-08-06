"""The chain's contract: labelled lines, and nobody retyping anyone's work.

Two of these guard failures that cost a whole run before they were understood.
A model given no explicit `think` spends its budget reasoning and returns an
empty string; a text-only model given images is not an error in Ollama, it
simply returns nothing. Both look like "the crew stopped improving".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import chain, crew

PLAN_ANSWER = (
    "SAY: 場所と時間を決めます。窓際で、午後の光がまっすぐ入る形に。総監督、これで進めます。\n\n"
    "PLACE: a corner seat by a large window\n"
    "HOUR: mid-afternoon, late summer\n"
    "LIGHT: even daylight through the glass, normal exposure\n"
    "ACTION: resting with a cold drink\n"
    "MUST APPEAR: wooden table, chair, glass mug, napkin, menu, spoon\n"
)


class FakeOllama:
    """Streams like the real client: reasoning and answer on separate channels."""

    def __init__(self, reply=PLAN_ANSWER, thinking=""):
        self.reply = reply
        self.thinking = thinking
        self.calls: list[dict] = []

    async def _stream(self):
        if self.thinking:
            yield {"type": "think", "text": self.thinking}
        yield {"type": "token", "text": self.reply}

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({"kind": "text", "prompt": prompt, **kw})
        return self._stream()

    def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append({"kind": "vlm", "prompt": prompt, "images": images, **kw})
        return self._stream()


def test_labelled_lines_parse_into_fields_and_say():
    say, fields = chain.parse_seat(PLAN_ANSWER, crew.FIELDS["plan"], crew.LIST_FIELDS)
    assert say.startswith("場所と時間")
    assert fields["PLACE"] == "a corner seat by a large window"
    assert fields["MUST APPEAR"][0] == "wooden table"
    assert len(fields["MUST APPEAR"]) == 6


def test_the_parser_tolerates_sloppy_labels():
    _, fields = chain.parse_seat(
        "- **PLACE** : a stairwell\nHOUR：dawn\nmust appear: railing, step, bulb\n",
        crew.FIELDS["plan"], crew.LIST_FIELDS,
    )
    assert fields["PLACE"] == "a stairwell"
    assert fields["HOUR"] == "dawn"
    assert fields["MUST APPEAR"] == ["railing", "step", "bulb"]


def test_a_blank_line_means_nothing_to_say_not_a_failed_turn():
    """Reduce cutting nothing is a real answer."""
    say, fields = chain.parse_seat(
        "SAY: 今回は切るものがありません。\nREMOVE:\n",
        crew.FIELDS["reduce"], crew.LIST_FIELDS,
    )
    assert say.startswith("今回は")
    assert fields == {}
    _, fields2 = chain.parse_seat(
        "SAY: なし。\nREMOVE: (none)\n", crew.FIELDS["reduce"], crew.LIST_FIELDS,
    )
    assert fields2 == {}


def test_a_seat_may_only_write_its_own_slots():
    """The drift this whole shape exists to stop."""
    turn = chain.SeatTurn(seat="enrich", say="", fields={
        "CAMERA": "medium shot", "WARDROBE": "linen", "LIGHT": "MUCH DARKER",
    })
    shot = chain.apply_turn({"light": "even daylight"}, turn)
    assert shot["camera"] == "medium shot"
    assert shot["light"] == "even daylight", "enrich must not reach the light"


def test_enrich_appends_while_the_planner_settles():
    shot = {"objects": ["table"], "place": "a hallway"}
    shot = chain.apply_turn(shot, chain.SeatTurn(
        seat="enrich", say="", fields={"OBJECTS": ["lamp"]}))
    assert shot["objects"] == ["table", "lamp"]

    # A note that moves the scene must replace the room, not add a second one.
    shot = chain.apply_turn(shot, chain.SeatTurn(
        seat="plan", say="", fields={"PLACE": "a stairwell", "MUST APPEAR": ["bench"]}))
    assert shot["place"] == "a stairwell"
    assert shot["objects"] == ["bench"]


def test_reduce_can_cut_from_a_slot_it_does_not_own():
    """Otherwise the light only ever goes one way — which is exactly how a board
    came back 66% black with nobody able to undo it."""
    shot = {"light": "even daylight, deep shadows", "camera": "medium shot"}
    shot = chain.apply_turn(shot, chain.SeatTurn(
        seat="reduce", say="", fields={"REMOVE": ["deep shadows"]}))
    assert "deep shadows" not in shot["light"]
    assert "even daylight" in shot["light"]


@pytest.mark.asyncio
async def test_run_seat_sends_the_seat_prompt_and_returns_parsed_fields():
    llm = FakeOllama()
    turn = await chain.run_seat(llm, seat="plan", user_prompt="BRIEF", model="m")
    assert turn.seat == "plan"
    assert turn.fields["HOUR"] == "mid-afternoon, late summer"
    call = llm.calls[0]
    assert call["kind"] == "text"
    assert call["think"] is False, "thinking is opt-in; it costs ~8x the wall clock"
    assert call["options"]["num_predict"] == -1
    assert "構成" in call["system"]


@pytest.mark.asyncio
async def test_reasoning_is_not_mistaken_for_the_answer():
    llm = FakeOllama(thinking="a" * 5000)
    turn = await chain.run_seat(llm, seat="plan", user_prompt="B", model="m")
    assert turn.fields["PLACE"] == "a corner seat by a large window"


@pytest.mark.asyncio
async def test_an_empty_answer_is_an_error_rather_than_an_empty_prompt():
    llm = FakeOllama(reply="   ")
    with pytest.raises(chain.ChainError):
        await chain.run_seat(llm, seat="plan", user_prompt="B", model="m")


@pytest.mark.asyncio
async def test_a_model_that_cannot_read_the_render_retries_blind_and_says_so():
    """Ollama does not error for a text-only model handed an image — it returns
    nothing, indistinguishable from a bad turn unless we flag it."""

    class Blind(FakeOllama):
        def generate_vlm_stream(self, prompt, images, **kw):
            self.calls.append({"kind": "vlm", "prompt": prompt, "images": images})

            async def _empty():
                yield {"type": "token", "text": "   "}
            return _empty()

    llm = Blind()
    turn = await chain.run_seat(
        llm, seat="plan", user_prompt="B", model="m", images=[b"jpeg"],
    )
    assert turn.blind is True
    assert turn.fields["PLACE"]
    assert [c["kind"] for c in llm.calls] == ["vlm", "text"]


@pytest.mark.asyncio
async def test_a_seeing_turn_sends_the_render_and_is_not_flagged_blind():
    llm = FakeOllama()
    turn = await chain.run_seat(
        llm, seat="check", user_prompt="B", model="m", images=[b"jpeg"],
    )
    assert turn.blind is False
    assert llm.calls[0]["kind"] == "vlm"
    assert llm.calls[0]["images"] == [b"jpeg"]

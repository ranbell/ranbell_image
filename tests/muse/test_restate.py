"""She reads the notebook at the end of her turn and says what is wrong.

From a live session the showrunner ran. The notebook held two answers to one
question and nothing in the machinery could see it:

    frame: close-up, facing camera
    beat:  sitting, eating cake, looking at cake

He asked three separate times for her to look at the camera. Four repairs
fired. The notebook did not change once, and `looking_at_cake` was still in the
tag bag on the last take of the shoot.

A field that has accreted stops being movable by a delta — the compile edits
inside it instead of replacing it. Saying it over from the start is the move
衣装部屋 already makes for the outfit, and this is that move for the rest of
the notebook, with her deciding which parts need it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew, notebook, service  # noqa: E402


# ── she may only point at real parts ────────────────────────────────────────

def test_she_names_parts_in_notebook_order():
    assert chain.parse_notebook_review("REWRITE: beat, frame") == ["frame", "beat"]


def test_naming_nothing_is_a_complete_answer():
    assert chain.parse_notebook_review("REWRITE: none") == []
    assert chain.parse_notebook_review("") == []


def test_a_part_that_is_not_a_part_falls_on_the_floor():
    """Closed vocabulary: she can point at a slot, never invent one."""
    assert chain.parse_notebook_review("REWRITE: vibe, mood, everything") == []


def test_atmosphere_is_not_hers_to_reopen():
    """Mood is the one field nobody directs turn by turn.

    Letting her reopen it every turn is how a shoot wanders.
    """
    assert "atmosphere" not in chain.RESTATE_FIELDS
    assert chain.parse_notebook_review("REWRITE: atmosphere, beat") == ["beat"]


def test_the_contract_tells_her_when_not_to_speak():
    contract = chain.NOTEBOOK_REVIEW_SYSTEM
    assert "Naming nothing is the normal answer" in contract
    assert "latest line is the authority" in contract
    assert "merely thin" in contract, "a wording she dislikes is not a fault"


# ── one part, said over ─────────────────────────────────────────────────────

def test_a_restatement_reads_back_as_three_lines():
    say, value, why = chain.parse_restate(
        "SAY: カメラのほう見ますね。\nFRAME: wide shot, looking into the lens\n"
        "WHY_FRAME: 『カメラ目線で』と言われたので視線を frame に置いた",
        "frame",
    )
    assert say == "カメラのほう見ますね。"
    assert value == "wide shot, looking into the lens"
    # The reason rides along so the showrunner can see where his line landed.
    assert "カメラ目線" in why


def test_a_restatement_without_a_reason_still_lands():
    """The value is the deliverable; the reason is instrumentation."""
    say, value, why = chain.parse_restate(
        "SAY: はい。\nFRAME: wide shot, looking into the lens", "frame",
    )
    assert value == "wide shot, looking into the lens"
    assert why == ""


def test_her_voice_survives_a_missing_label():
    say, value, _ = chain.parse_restate(
        "座り直しました。\nBEAT: sitting, hands in lap", "beat",
    )
    assert "座り直し" in say
    assert value == "sitting, hands in lap"


def test_each_field_gets_its_own_contract_and_only_its_own():
    beat = crew.actress_duet_prompt({"name": "Mio"}, mode="restate:beat", seed="s")
    assert "BEAT:" in beat
    assert "WEARING:" not in beat and "FRAME:" not in beat
    assert "posture stem" in beat
    assert "that is the frame" in beat, "the gaze belongs to frame, and she is told"

    frame = crew.actress_duet_prompt({"name": "Mio"}, mode="restate:frame", seed="s")
    assert "FRAME:" in frame and "BEAT:" not in frame
    assert "gaze" in frame or "eyes are pointed" in frame


def test_light_may_not_be_restated_as_a_direction_of_change():
    contract = crew.actress_duet_prompt(
        {"name": "Mio"}, mode="restate:light", seed="s",
    )
    assert "Never a direction of change" in contract


# ── the turn ────────────────────────────────────────────────────────────────

class _TurnOllama:
    """Answers the review, then each restatement."""

    def __init__(self, review: str, values: dict[str, str]):
        self.review = review
        self.values = values
        self.asked: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        system = str(kw.get("system") or "")
        if "REWRITE:" in system:
            reply = self.review
        else:
            field = next(
                (f for f in chain.RESTATE_FIELDS if f"{f.upper()}:" in system), "",
            )
            self.asked.append(field)
            reply = f"SAY: はい。\n{field.upper()}: {self.values.get(field, '')}"

        async def _stream():
            yield {"type": "token", "text": reply}
        return _stream()


def _cake_session():
    session = {
        "session_id": "s1", "mode": "duet", "inputs": {"locale": "ja"},
        "character": {"name": "Mio", "name_ja": "各務 みお"},
        "scripter_intent": "shot", "chat": [],
    }
    nb = notebook.of(session)
    notebook.apply_patch(nb, {
        "scene": "cafe, day",
        "frame": "close-up, facing camera",
        "beat": "sitting, eating cake, looking at cake",
        "wearing": "blue skirt",
    })
    session["notebook"] = nb
    return session


@pytest.mark.asyncio
async def test_the_stuck_beat_is_said_over(monkeypatch):
    """The measured case, end to end."""
    async def _no_partner(db, s):
        return {}
    monkeypatch.setattr(service, "_partner_character", _no_partner)

    session = _cake_session()
    ollama = _TurnOllama("REWRITE: beat", {"beat": "sitting, eating cake"})

    await service._muse_checks_the_notebook(
        None, ollama, session, cfg={}, note="ケーキ見ないでカメラ見てね",
    )

    assert notebook.of(session)["beat"] == "sitting, eating cake"
    assert ollama.asked == ["beat"]
    assert session["craft_dirty"] is True


@pytest.mark.asyncio
async def test_a_notebook_she_is_happy_with_is_left_alone(monkeypatch):
    async def _no_partner(db, s):
        return {}
    monkeypatch.setattr(service, "_partner_character", _no_partner)

    session = _cake_session()
    before = dict(notebook.of(session))
    ollama = _TurnOllama("REWRITE: none", {})

    await service._muse_checks_the_notebook(
        None, ollama, session, cfg={}, note="いいね",
    )

    assert notebook.of(session)["beat"] == before["beat"]
    assert ollama.asked == []
    assert not session.get("craft_dirty")


@pytest.mark.asyncio
async def test_she_cannot_rewrite_half_the_shot_in_one_turn(monkeypatch):
    async def _no_partner(db, s):
        return {}
    monkeypatch.setattr(service, "_partner_character", _no_partner)

    session = _cake_session()
    ollama = _TurnOllama(
        "REWRITE: scene, light, frame, wearing, beat",
        {f: "x" for f in chain.RESTATE_FIELDS},
    )

    await service._muse_checks_the_notebook(
        None, ollama, session, cfg={}, note="うん",
    )

    assert len(ollama.asked) <= 2, "losing the shoot's place is the failure here"


@pytest.mark.asyncio
async def test_chit_chat_does_not_reopen_the_shot(monkeypatch):
    async def _no_partner(db, s):
        return {}
    monkeypatch.setattr(service, "_partner_character", _no_partner)

    session = _cake_session()
    session["scripter_intent"] = "casual"
    ollama = _TurnOllama("REWRITE: beat", {"beat": "standing"})

    await service._muse_checks_the_notebook(
        None, ollama, session, cfg={}, note="今日は暑いね",
    )

    assert notebook.of(session)["beat"] == "sitting, eating cake, looking at cake"


@pytest.mark.asyncio
async def test_an_empty_restatement_changes_nothing(monkeypatch):
    async def _no_partner(db, s):
        return {}
    monkeypatch.setattr(service, "_partner_character", _no_partner)

    session = _cake_session()
    ollama = _TurnOllama("REWRITE: beat", {"beat": ""})

    await service._muse_checks_the_notebook(
        None, ollama, session, cfg={}, note="カメラ見て",
    )

    assert notebook.of(session)["beat"] == "sitting, eating cake, looking at cake"


@pytest.mark.asyncio
async def test_she_says_nothing_in_the_room(monkeypatch):
    """She has just spoken. A second line explaining the studio's bookkeeping
    is not part of the picture they are making together."""
    async def _no_partner(db, s):
        return {}
    monkeypatch.setattr(service, "_partner_character", _no_partner)

    session = _cake_session()
    ollama = _TurnOllama("REWRITE: beat", {"beat": "sitting, eating cake"})

    await service._muse_checks_the_notebook(
        None, ollama, session, cfg={}, note="カメラ見て",
    )

    assert not session["chat"]
    assert any(
        e.get("source") == "restate" for e in (session.get("rewrite_log") or [])
    ), "the panel still has to be able to see it"

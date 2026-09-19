"""**After a test shot, she is shown the picture.** (2026-09-10)

The Showrunner: "after a test shot, let us have Muse look at the image".

The ledger is "shoot it like this"; the picture is "this is how it came out". With
only the ledger in view she cannot talk about the picture itself. From the test shot
onward, classic hands over the board every turn.

**A model that cannot read pictures returns empty rather than refusing.** Left
alone that reads only as "she is quiet today", so it retries once without the
picture and says so out loud.
"""
from __future__ import annotations

import asyncio

from app.muse import ledger as L, service, writer


class _Ollama:
    """Can also be built to return empty when called with a picture (a model that cannot
    read pictures)."""

    def __init__(self, *, can_see: bool = True, raw: str = "SAY: 見えてます。\n"):
        self.can_see, self.raw = can_see, raw
        self.calls: list[str] = []

    async def generate_text(self, prompt, **kw):
        self.calls.append("text")
        return self.raw

    async def generate_vlm(self, prompt, images, **kw):
        self.calls.append(f"vlm:{len(images)}")
        return self.raw if self.can_see else ""

    async def generate_text_stream(self, prompt, **kw):
        self.calls.append("text_stream")
        for ch in self.raw:
            yield {"type": "token", "text": ch}

    async def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append(f"vlm_stream:{len(images)}")
        for ch in (self.raw if self.can_see else ""):
            yield {"type": "token", "text": ch}


def _turn(o, **kw):
    return asyncio.run(writer.actress_turn(
        o, model="m", locale="ja", name="澪", now="NOW", ledger=L.blank(),
        identity_blurb="", user_line="これでいい？", director_tail="", **kw,
    ))


def test_no_board_means_no_picture_call():
    o = _Ollama()
    out = _turn(o)
    assert o.calls == ["text"]
    assert out["blind"] is False


def test_a_board_is_handed_over():
    o = _Ollama()
    out = _turn(o, images=[b"jpeg-bytes"])
    assert o.calls == ["vlm:1"]
    assert "見えてます" in out["say"]
    assert out["blind"] is False


def test_a_blind_model_retries_without_the_picture():
    """On an empty reply it does not give up silently — once more without the picture."""
    o = _Ollama(can_see=False)
    out = _turn(o, images=[b"jpeg-bytes"])
    assert o.calls == ["vlm:1", "text"]
    assert out["blind"] is True
    assert "見えてます" in out["say"]      # the second time it comes back


def test_streaming_also_carries_the_picture():
    o = _Ollama()
    seen: list[str] = []
    out = _turn(o, images=[b"jpeg-bytes"], on_token=seen.append)
    assert o.calls == ["vlm_stream:1"]
    assert "見えてます" in out["say"]
    assert seen


def test_the_studio_says_so_once_when_she_cannot_see():
    session: dict = {"chat": [], "inputs": {"locale": "ja"}}
    service._note_blind(session, locale="ja")
    service._note_blind(session, locale="ja")   # the second time it says nothing
    rows = [r for r in session["chat"] if r.get("role") == "system"]
    assert len(rows) == 1
    assert "vision_model" in rows[0]["text"]


def test_the_vision_model_is_only_swapped_in_for_picture_turns():
    import inspect
    src = inspect.getsource(service.chat)
    assert 'vision_model' in src
    # With no board it stays on `model`, as before
    assert 'if board_shots else model' in src

"""**No silent bubble is shown.** (2026-09-10)

The Showrunner: "the processing failed and it has gone silent". Live (`cdf8d4f7`
23:45:56), on a turn where the board was shown, **only ASIDE came back and SAY was
empty**; the mutter appeared while the line left nothing but a name and an empty
bubble.

    23:45:56  actress_saw_board  one test shot shown
    23:45:56  actress            (empty)

The first retry looked only for "the whole reply is empty". A model that cannot
read goes quiet — but **a model that can read also drops the format** sometimes.
What is watched is "did she speak".
"""
from __future__ import annotations

import asyncio

from app.muse import ledger as L, talk, writer

#: The shape that happened live — the model returned something, and there was
#: nothing in it that could be taken out as a line.
NO_LINE = "   \n"
FULL = "SAY: はい、総監督。\nASIDE: （どきどき）\n"


class _Ollama:
    """Drops the format the first time and answers properly the second."""

    def __init__(self, first: str, second: str = FULL):
        self.replies = [first, second]
        self.calls: list[bool] = []      # whether it was called with a picture

    async def generate_vlm(self, prompt, images, **kw):
        self.calls.append(True)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]

    async def generate_text(self, prompt, **kw):
        self.calls.append(False)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def _turn(o, images=None):
    return asyncio.run(writer.actress_turn(
        o, model="m", locale="ja", name="澪", now="", ledger=L.blank(),
        identity_blurb="", user_line="おしまいね", director_tail="", images=images,
    ))


def test_an_empty_say_on_an_image_turn_is_retried():
    o = _Ollama(NO_LINE)
    out = _turn(o, images=[b"jpeg"])
    assert o.calls == [True, False], "絵つき → 絵抜きで撮り直していない"
    assert out["blind"] is True
    assert "はい、総監督" in out["say"]


def test_a_good_first_answer_is_not_retried():
    o = _Ollama(FULL)
    out = _turn(o, images=[b"jpeg"])
    assert o.calls == [True]
    assert out["blind"] is False


def test_a_turn_with_no_picture_is_left_alone():
    """A turn with no picture shown is not retried (a different failure, so it is not
    called twice on its own)."""
    o = _Ollama(NO_LINE)
    out = _turn(o)
    assert o.calls == [False]
    assert not out["say"].strip()
    # On a silent turn, what came back is carried home (so it can be read next time)
    assert "raw" in out


def _publish(say, aside=""):
    s = {"session_id": "s", "character": {"character_id": "a", "name_ja": "澪"},
         "partner_character": {}, "chat": [], "inputs": {"locale": "ja"}}
    talk.publish_actress_turn(
        s, {"say": say, "aside": aside, "propose": {}, "my_feel": "", "pitch": ""},
        locale="ja", lead_name="澪",
    )
    return s


def test_an_empty_line_never_becomes_a_row():
    s = _publish("", aside="（どきどき）")
    kinds = [(r.get("meta") or {}).get("kind") for r in s["chat"]]
    assert "say" not in kinds
    # The mutter still comes out as before
    assert "banter" in kinds


def test_the_silence_is_written_down():
    """It does not fail silently — what happened goes into the record."""
    s = _publish("", aside="（どきどき）")
    kinds = [row.get("kind") for row in (s.get("refine_log") or [])]
    assert "actress_said_nothing" in kinds


def test_a_real_line_still_becomes_a_row():
    s = _publish("はい、総監督。", aside="（どきどき）")
    says = [r for r in s["chat"] if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 1 and says[0]["text"] == "はい、総監督。"

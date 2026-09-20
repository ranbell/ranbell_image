"""**The minor-check reader writes its reason.** (2026-09-09)

The Showrunner: "and as we have done before, maybe have it output a WHY".

From the day the same was done for the clerk: writing the reason first is **not
only for observation but for the quality of the judgement**. Here, in addition,
**making it say who is in the picture** bites against the accompaniment trick
(putting a child beside the actress).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain  # noqa: E402


def test_the_answer_line_decides_not_the_reason():
    """**The judgement is not made on words inside the reason.** The same shape as the
    hole hit at the first stage."""
    assert chain.parse_abuse("WHY: no child is named here\nANSWER: none") == (
        False, "no child is named here")
    # `child` may appear twice in the reason; the `ANSWER:` line decides.
    hit, why = chain.parse_abuse(
        "WHY: no child in the costume sense — but a 12-year-old is undressed\n"
        "ANSWER: child")
    assert hit is True
    assert "12-year-old" in why


def test_the_answer_word_carries_its_own_meaning():
    """**`yes` / `no` are not used.** Measured, the answer alone flipped:

        WHY: No child was mentioned; an adult actress stands by a window.
        ANSWER: yes            <- the opposite

    The reason was right every time, which is how we knew the broken part was the
    word.
    """
    assert "yes" not in chain.ABUSE_LOOK_SYSTEM.split("ANSWER:")[-1]
    assert chain.parse_abuse("ANSWER: child")[0] is True
    assert chain.parse_abuse("ANSWER: none")[0] is False


def test_nothing_readable_passes():
    """When both the connection and the shape are lost it passes — matching the first
    stage (it does not stop every turn)."""
    assert chain.parse_abuse("なにも") == (False, "")
    assert chain.parse_abuse("") == (False, "")


def test_the_contract_asks_who_is_in_the_picture():
    """The accompaniment trick is found by making it say who is in the picture."""
    text = chain.ABUSE_LOOK_SYSTEM
    assert "WHY:" in text and "ANSWER:" in text
    assert "who is in the picture" in text

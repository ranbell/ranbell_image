"""**"missing" must not lie.** (2026-09-10)

The Showrunner: "I am still testing, but it keeps saying missing — why?"

The values were in the picture all along. Only the comparison was misaligned:

    ledger        casual_clothes      <- the writer writes danbooru-style, with
                                         underscores
    craft.prompt  casual clothes      <- `anima.format_for_anima` turns underscores
                                         back into spaces at the end
    overlap       (none) -> "in ledger, missing in craft.prompt"

And on a conversation turn `craft.prompt` is **one move old** (`assemble.
touch_craft` waits until the shot to rebuild it). Comparing there lines up lies
every turn.
"""
from __future__ import annotations

from app.muse import ledger as L, pipeline_view as P


def _session(led: dict, prompt: str, *, stale: bool = False, board: str = ""):
    return {
        "session_id": "s",
        "refine_ledger": {**L.blank(), **led},
        "craft": {"prompt": prompt, "stale": stale},
        "board": {"prompt": board} if board else {},
    }


def test_an_underscored_value_counts_as_present():
    """The ledger's `casual_clothes` and the picture's `casual clothes` are the same
    thing."""
    s = _session({"wearing": "casual_clothes"},
                 "2girls, Mio: casual clothes, big smile,")
    assert P._divergences(s) == []


def test_a_hyphenated_value_counts_too():
    s = _session({"light": "High-key_natural_light"},
                 "bowling alley, High-key natural light, bowling lane")
    assert P._divergences(s) == []


def test_an_exact_underscored_match_still_counts():
    """Picked up even when the picture side keeps the underscore (both spellings are
    returned)."""
    s = _session({"wearing": "maid_outfit"}, "1girl, maid_outfit, cafe")
    assert P._divergences(s) == []


def test_a_value_that_really_is_missing_is_still_reported():
    """**No hole is opened.** A field that really is missing is still reported."""
    s = _session({"wearing": "sailor uniform"}, "1girl, cardigan, rooftop")
    got = [d["field"] for d in P._divergences(s)]
    assert "wearing" in got


def test_a_conversation_turn_is_not_compared_at_all():
    """Mid-conversation the picture is one move old. Comparing is meaningless, so it
    says nothing."""
    s = _session({"wearing": "sailor uniform"}, "1girl, cardigan, rooftop",
                 stale=True)
    assert P._divergences(s) == []


def test_the_board_is_still_compared_while_stale():
    """A board that has been shot does not go stale — its divergences are reported even
    mid-conversation."""
    s = _session({"wearing": "sailor uniform"}, "1girl, cardigan",
                 stale=True, board="1girl, cardigan, rooftop")
    got = [(d["kind"], d["field"]) for d in P._divergences(s)]
    assert ("board_vs_ledger", "wearing") in got
    assert not any(k == "prompt_vs_ledger" for k, _ in got)


def test_the_partner_fields_are_checked_the_same_way():
    s = _session({"wearing_b": "maid_outfit, lace_apron"},
                 "Asahi: maid outfit, lace apron,")
    assert P._divergences(s) == []

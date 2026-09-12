"""One seed per shoot, no gaze gate, and the Muse gets a look at the bag.

All three come out of one live session (`9e0522c9`) where the conversation was
flawless and not one instruction reached the picture:

- Every ② drew a fresh random seed, so nine test shots of a frozen script were
  nine different pictures. The showrunner read those differences as the studio
  answering him. They were noise.
- A gate refused the whole weave whenever the bag held `low_angle` with
  `looking_up` — which is exactly how you ask for a low camera and a face
  tilted up into the light. Seven weaves in a row went in the bin, expression
  and shadows and atmosphere with them.
- Nobody in the picture ever saw the tag list before it was rendered.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew  # noqa: E402


# ── one seed for the whole shoot ────────────────────────────────────────────


# ── the gaze gate is gone ───────────────────────────────────────────────────


# ── she may point, and only at what is there ────────────────────────────────

def test_she_can_only_name_tags_that_are_in_the_bag():
    """The safety property: a wrong answer makes the bag smaller, not stranger."""
    bag = "sailor_fuku, straw_hat, low_angle, sitting"

    assert chain.parse_weave_review("WRONG: straw_hat", bag) == ["straw_hat"]
    assert chain.parse_weave_review("WRONG: tiara, dragon, cardigan", bag) == []
    assert chain.parse_weave_review("WRONG: none", bag) == []
    assert chain.parse_weave_review("", bag) == []


def test_she_is_matched_on_the_bare_tag_not_the_spelling():
    bag = "(straw_hat:1.2), sitting"
    assert chain.parse_weave_review("WRONG: straw_hat", bag) == ["(straw_hat:1.2)"]


def test_the_same_tag_twice_is_named_once():
    bag = "straw_hat, sitting"
    assert chain.parse_weave_review("WRONG: straw_hat, straw_hat", bag) == ["straw_hat"]


def test_her_review_prompt_carries_her_voice_and_not_the_contract():
    """Who is looking comes from her; the shape of the answer comes from chain."""
    system = crew.actress_duet_prompt(
        {"name": "Mio", "name_ja": "各務 みお"}, mode="review", seed="s",
    )
    assert "各務 みお" in system or "Mio" in system
    assert "WRONG:" not in system, "one copy of the output contract, in chain"
    assert "TAGS:" not in system


def test_the_contract_tells_her_the_two_things_that_burned_us():
    """A low camera with a lifted face, and 'naming nothing is a full answer'."""
    contract = chain.WEAVE_REVIEW_SYSTEM
    assert "low camera and a lifted face" in contract
    assert "normal answer" in contract


# ── the subtraction the caller does ─────────────────────────────────────────



def _session():
    return {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "character": {"name": "Mio", "name_ja": "各務 みお"},
        "chat": [],
    }



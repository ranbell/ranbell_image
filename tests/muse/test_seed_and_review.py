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

from app.muse import chain, crew, service  # noqa: E402


# ── one seed for the whole shoot ────────────────────────────────────────────

def test_the_seed_is_drawn_once_and_then_held():
    """Two takes of one picture must differ only by the words."""
    session = {}
    first = service.session_seed(session)

    assert service.session_seed(session) == first
    assert service.session_seed(session) == first
    assert session["seed"] == first


def test_a_seed_already_on_the_session_is_not_redrawn():
    session = {"seed": 12345}
    assert service.session_seed(session) == 12345


def test_two_shoots_do_not_share_a_seed():
    a, b = {}, {}
    assert service.session_seed(a) != service.session_seed(b)


# ── the gaze gate is gone ───────────────────────────────────────────────────

def test_a_low_camera_and_a_lifted_face_are_allowed():
    """「ローアングル気味に。顔はもう少し撮りたいな」 — a real shot.

    The pianist tilts her face up into the last of the resonance and the camera
    is low. Both tags belong. This used to bin the whole weave.
    """
    session = {"session_id": "s1", "craft": {"tags": "OLD", "scene": "OLD"},
               "notebook": {}, "inputs": {}}
    tags = "sitting, piano, low_angle, from_below, looking_up, sad, deep_shadows"

    assert service._apply_compiled_craft(session, tags, "A low angle holds her face.")
    assert "looking_up" in session["craft"]["tags"]
    assert "sad" in session["craft"]["tags"], "the expression must survive too"


def test_the_mirror_case_is_allowed_as_well():
    """`high_angle` + `looking_down` was already let through. Same rule now."""
    session = {"session_id": "s1", "craft": {}, "notebook": {}, "inputs": {}}
    tags = "standing, high_angle, from_above, looking_down"

    assert service._apply_compiled_craft(session, tags, "From above.")


def test_an_empty_compile_is_still_refused():
    """The one wholesale refusal that was always right stays."""
    session = {"session_id": "s1", "craft": {}, "notebook": {}, "inputs": {}}
    assert not service._apply_compiled_craft(session, "", "")


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

class _ReviewOllama:
    def __init__(self, reply):
        self.reply = reply

    def generate_text_stream(self, prompt, **kw):
        reply = self.reply

        async def _stream():
            yield {"type": "token", "text": reply}
        return _stream()


def _session():
    return {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "character": {"name": "Mio", "name_ja": "各務 みお"},
        "chat": [],
    }


@pytest.mark.asyncio
async def test_what_she_disowns_leaves_the_bag():
    bag = "sailor_fuku, straw_hat, sitting, low_angle"
    out = await service._muse_reviews_weave(
        _ReviewOllama("WRONG: straw_hat"), _session(), bag,
        cfg={}, name_a="各務 みお", name_b="", partner=False,
    )
    assert "straw_hat" not in out
    assert "sailor_fuku" in out and "low_angle" in out


@pytest.mark.asyncio
async def test_a_review_that_wants_to_gut_the_bag_is_ignored():
    """Her own contract says two or three; a dozen means she misread it."""
    bag = "a_one, b_two, c_three, d_four, e_five, f_six, g_seven"
    out = await service._muse_reviews_weave(
        _ReviewOllama("WRONG: a_one, b_two, c_three, d_four, e_five"),
        _session(), bag, cfg={}, name_a="各務 みお", name_b="", partner=False,
    )
    assert out == bag


@pytest.mark.asyncio
async def test_a_review_that_cannot_run_leaves_the_bag_alone():
    class _Dead:
        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": ""}
            return _stream()

    bag = "sailor_fuku, sitting"
    out = await service._muse_reviews_weave(
        _Dead(), _session(), bag,
        cfg={}, name_a="各務 みお", name_b="", partner=False,
    )
    assert out == bag


@pytest.mark.asyncio
async def test_she_cannot_empty_the_bag():
    """Subtracting everything would be a blank picture, so it is refused."""
    bag = "sailor_fuku"
    out = await service._muse_reviews_weave(
        _ReviewOllama("WRONG: sailor_fuku"), _session(), bag,
        cfg={}, name_a="各務 みお", name_b="", partner=False,
    )
    assert out == bag

"""**A picture with two people is read as two people.** (2026-09-10)

The Showrunner: "the diaries are muddled too". Live (`83d31174`) the two diaries
contradicted each other:

    Mio's diary    "a pink ribbon, the opposite of Asahi's", "Asahi's is yellow"
    Asahi's diary  "mine is the red ribbon… Mio's is gold"

The Showrunner: "they are commenting on the image, so they are not inventing it".
Quite so — `_read_the_photo` has a VLM read the final photograph. Two things were
wrong:

    it is read with wording for one person (where **she** is, what **she** is
    wearing…)
    and that one description is handed to **both** diaries

Both are speaking from the picture, and **neither is told which one she is**.

**Not one character of the solo shoot changes.**
"""
from __future__ import annotations

import inspect

import pytest

from app.muse import identity, shared as muse_service, shared

#: Which side the lead and the partner are on. **Never written out here** (change
#: `identity.LEAD_SIDE` and this follows).
LEAD_JA = identity.side_of(lead=True)[1]
PART_JA = identity.side_of(lead=False)[1]


def _code(fn) -> str:
    """Only the lines that actually run, with comments and strings removed.

    **So the test does not catch our own prose** — `_read_the_photo`'s docstring
    says "`is_duet` only looks at `mode`", so searching a raw `getsource` always
    matches.
    """
    import io, tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(inspect.getsource(fn)).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)

A = {"character_id": "a", "name_ja": "各務 みお",
     "identity_tags": ["silver_hair", "bob_cut", "flat_chest"]}
B = {"character_id": "b", "name_ja": "倉田 あさひ",
     "identity_tags": ["light_green_hair", "hair_up", "medium_breasts"]}
DESC = "Two girls in a cafe."


def test_one_person_gets_nothing_added():
    """**Do not break the solo shoot.** The photo description is unchanged."""
    s = {"character": dict(A), "partner_character": {}}
    assert muse_service._which_one_is_me(s, "a", DESC) == DESC


def test_the_lead_is_told_which_side_she_is_on():
    s = {"character": dict(A), "partner_character": dict(B)}
    got = muse_service._which_one_is_me(s, "a", DESC)
    head = got.splitlines()[0]
    assert f"あなたは{LEAD_JA}" in head
    assert f"倉田 あさひは{PART_JA}" in head
    assert got.endswith(DESC)


def test_the_partner_is_told_the_other_side():
    s = {"character": dict(A), "partner_character": dict(B)}
    got = muse_service._which_one_is_me(s, "b", DESC).splitlines()[0]
    assert f"あなたは{PART_JA}" in got
    assert f"各務 みおは{LEAD_JA}" in got


def test_the_hint_says_it_is_not_material_to_copy():
    """The Showrunner: "the diary entries have started describing the contents of
    the photo in detail as well".

    The first version handed over the telling words (silver_hair, bob_cut) and the
    diary transcribed them. Say that they are a hint, and do not hand the words
    themselves over.
    """
    s = {"character": dict(A), "partner_character": dict(B)}
    got = muse_service._which_one_is_me(s, "a", DESC)
    assert "書き写さない" in got
    assert "silver_hair" not in got
    assert "bob_cut" not in got


def test_the_diary_and_the_picture_agree():
    """**Read the same document of record.** Keep two and the picture and her own
    memory disagree."""
    from app.muse import assemble, ledger as L

    led = {**L.blank(), "wearing": "maid outfit", "beat": "holding tray",
           "scene": "cafe", "wearing_b": "maid outfit", "beat_b": "holding menu"}
    sess = {"session_id": "s", "character": dict(A), "partner_character": dict(B),
            "inputs": {"locale": "ja"}, "refine_ledger": led, "banned": []}
    prose = assemble.scene_prose(led, partner=True, name_a="Mio", name_b="Asahi")
    en_lead, en_part = identity.side_of(lead=True)[0], identity.side_of(lead=False)[0]
    # The side the lead is placed on in the picture matches the side she is told in
    # the diary
    left_name, right_name = (
        ("Asahi", "Mio") if identity.LEAD_SIDE == "right" else ("Mio", "Asahi")
    )
    left_en, right_en = identity.SIDE_WORDS["left"][0], identity.SIDE_WORDS["right"][0]
    assert f"{left_name} stands {left_en} of the frame; {right_name} {right_en}." in prose
    assert en_lead and en_part
    assert f"あなたは{LEAD_JA}" in muse_service._which_one_is_me(sess, "a", DESC)
    assert f"あなたは{PART_JA}" in muse_service._which_one_is_me(sess, "b", DESC)


def test_each_is_told_not_to_borrow_the_other():
    s = {"character": dict(A), "partner_character": dict(B)}
    for cid in ("a", "b"):
        assert "自分のものとして書かないこと" in muse_service._which_one_is_me(s, cid, DESC)


def test_an_empty_photo_read_is_left_alone():
    s = {"character": dict(A), "partner_character": dict(B)}
    assert muse_service._which_one_is_me(s, "a", "") == ""


class _Seeing:
    """Given a picture, record the instruction text and return (no model is
    called).

    Shaped after `_SeeingOllama` in `tests/muse/test_learning.py` — the photo-reading
    tests in this repo are written in that manner.
    """

    def __init__(self):
        self.system = ""

    def generate_vlm_stream(self, prompt, images, **kw):
        self.system = str(kw.get("system") or "")

        async def _stream():
            yield {"type": "token", "text": "described"}
        return _stream()


class _ImageDb:
    async def get_by_sha256s(self, shas):
        return [{"path": "/nonexistent.png"}]


async def _fake_images(db, shas):
    return [b"jpeg-bytes"]


def _shot_session(*, partner: bool):
    return {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "shoot": {"prompt": "1girl, cafe", "images": []},
        "character": dict(A),
        "partner_character": dict(B) if partner else {},
    }


@pytest.mark.asyncio
async def test_two_in_frame_are_read_separately(monkeypatch):
    monkeypatch.setattr(shared, "images_by_sha", _fake_images)
    seeing = _Seeing()
    await muse_service._read_the_photo(
        _ImageDb(), seeing, _shot_session(partner=True), "aaa",
    )
    assert "TWO girls" in seeing.system
    assert "left" in seeing.system and "right" in seeing.system
    # **Short.** Held to the same length as the single-person reading, so it does not
    # become a catalogue.
    assert "ONE short English sentence each" in seeing.system


@pytest.mark.asyncio
async def test_one_in_frame_is_read_exactly_as_before(monkeypatch):
    """**Do not break the solo shoot.** The wording is character for character what
    it was before the change."""
    monkeypatch.setattr(shared, "images_by_sha", _fake_images)
    seeing = _Seeing()
    await muse_service._read_the_photo(
        _ImageDb(), seeing, _shot_session(partner=False), "aaa",
    )
    assert seeing.system == (
        "You are looking at one photograph. Say what is in it, plainly "
        "and concretely, in 3\u20135 English sentences: where she is, what "
        "she is wearing, what her body is doing, and \u2014 this above all "
        "\u2014 what her face is doing. Describe only what the picture "
        "shows. Do not guess at intent, do not praise it, do not "
        "mention prompts or tags."
    )


def test_the_gate_is_never_the_mode():
    """`is_duet` only looks at `mode` — live, even solo turns carry
    `mode: duet`."""
    for fn in (muse_service._read_the_photo, muse_service._which_one_is_me):
        assert "is_duet" not in _code(fn), fn.__name__


def test_the_diary_job_hands_it_over():
    src = inspect.getsource(muse_service.run_generate_actress_diary_job)
    assert "_which_one_is_me(session, character_id, photo_desc)" in src

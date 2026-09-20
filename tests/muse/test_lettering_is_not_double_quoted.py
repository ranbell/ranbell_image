"""**The sign's words, without the quotes around them.** (2026-09-20)

The Showrunner's announcement picture came out with 「Imge Muse」 — a letter
dropped from the sign. Read back, the live ledger held the phrase **with its
quotes**:

    refine_ledger["lettering"] == '"Ranbell Image Muse"'

and `anima.append_lettering` wraps it again, so the tag reaching the checkpoint
was

    text ""Ranbell Image Muse"", text_on_image

The tag's own quotes are what mark where the phrase starts and ends, so a second
pair inside them is noise in exactly the place the model is weakest. Stripped at
both doors — the ledger's, so the screen shows what the render gets, and
`append_lettering`, so the sessions already stored with the quotes render right.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import anima, ledger as L


def test_the_live_value_renders_with_one_pair_of_quotes():
    """The defect itself, with the string as it was actually stored."""
    out = anima.append_lettering("1girl, standing", ['"Ranbell Image Muse"'])

    assert out.endswith('text "Ranbell Image Muse", text_on_image')
    assert '""' not in out


def test_the_ledger_door_drops_them_too():
    """So the panel shows the same words the picture gets."""
    patch = L.normalize_patch({"lettering": '"Ranbell Image Muse"'})

    assert patch["lettering"] == "Ranbell Image Muse"


@pytest.mark.parametrize("raw, want", [
    ('"Ranbell Image Muse"', "Ranbell Image Muse"),
    ("'Laugh!'", "Laugh!"),
    ("「ランベル」", "ランベル"),
    ('“Smile”', "Smile"),
    ('""Twice""', "Twice"),
    ("  Ranbell Image Muse  ", "Ranbell Image Muse"),
])
def test_every_wrapping_comes_off(raw, want):
    assert anima.clean_lettering(raw) == want


@pytest.mark.parametrize("raw", [
    'Ranbell "Muse" Image',   # quotes inside stay — they are part of the sign
    "don't stop",             # a lone apostrophe is a letter, not a wrapper
    "5\" floppy",
])
def test_quotes_that_are_part_of_the_words_stay(raw):
    assert anima.clean_lettering(raw) == raw


def test_a_value_that_was_only_quotes_writes_no_tag():
    """Nothing to print — the tag is left off rather than printed empty."""
    assert anima.append_lettering("1girl", ['""']) == "1girl"

"""Reference-board prompts for a character — deterministic, no LLM.

The board answers one question: does this character's tag list actually render
as the person the user has in mind? So the prompt is nothing but her own tags
plus a neutral framing. Anything invented here would be testing the inventor
instead of the tags.
"""
from __future__ import annotations

from typing import Any

from ..tags.subject_anchors import insert_after_anchors
from .presets import BOARD_SLOTS, preset_to_character

# Framing per slot. Kept plain on purpose: a busy pose or a scene would hide
# exactly the details the board exists to show.
_SLOT_FRAMING: dict[str, list[str]] = {
    "sheet": [
        "full_body", "standing", "looking_at_viewer",
        "simple_background", "white_background", "arms_at_sides",
    ],
    "portrait": [
        "upper_body", "looking_at_viewer", "simple_background",
        "grey_background", "closed_mouth",
    ],
}

# A board is a character study, not a picture. These would all pull it toward
# being a picture.
_NEGATIVE = (
    "multiple_views, reference_sheet, character_sheet, collage, split_screen, "
    "multiple_girls, multiple_boys, 2girls, 2boys, text, watermark, signature, "
    "cropped, out_of_frame, blurry, lowres, bad_anatomy, bad_hands, "
    "extra_fingers, missing_fingers"
)


def compile_board_slot(preset: dict[str, Any], slot: str) -> tuple[str, str]:
    """``(positive, negative)`` for one board slot of one character."""
    if slot not in BOARD_SLOTS:
        raise ValueError(f"unknown board slot: {slot}")

    character = preset_to_character(preset)
    identity = list(character.get("identity_tags") or [])
    outfit = list(character.get("outfit_tags") or [])
    props = list(character.get("prop_tags") or [])

    # Identity first and framing last: the head of the prompt is where a
    # diffusion model pays attention, and identity is the part that must not
    # drift between the two slots.
    ordered: list[str] = []
    for group in (identity, outfit, props, _SLOT_FRAMING[slot]):
        for tag in group:
            if tag and tag not in ordered:
                ordered.append(tag)

    positive = insert_after_anchors(", ".join(ordered), [])
    return positive, _NEGATIVE

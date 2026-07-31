"""Reference-board prompts for a character — deterministic, no LLM.

The board answers one question: does this character's tag list actually render
as the person the user has in mind? So the prompt is nothing but her own tags
plus a framing. Anything invented here would be testing the inventor instead of
the tags.

The **sheet** is the format Chronicle arrived at and it is worth keeping: one
image showing the same person across four lives — a centre figure with four
polaroid-framed vignettes around it. A plain ``full_body, standing`` prompt
produces a shop-mannequin shot that tells you almost nothing, and it comes out
indistinguishable from the portrait slot. The composite tells you whether she
still reads as herself in sportswear, holding food, at work.

Two details of that format are load-bearing. It is written as **labelled lines**
rather than a flat tag list, because that is what makes the model lay the frames
out instead of blending them; and ``multiple_views`` is in the *positive*, where
every other prompt in this codebase bans it.

The **portrait** is the opposite: a close-up, identity only, so a human can
judge the face at full size. It carries no wardrobe and no props — a long skirt
and a floor-standing prop are both votes for showing the whole body, which is
how this slot used to come back as a second full-body render.
"""
from __future__ import annotations

from typing import Any

from ..tags.split_tags import soft_normalize_tag
from .presets import BOARD_SLOTS, preset_to_character

# Canvas per slot. The sheet needs room for five frames; the portrait needs a
# shape with nowhere to put the legs.
SLOT_SIZE: dict[str, tuple[int, int]] = {
    "sheet": (1024, 1344),
    "portrait": (512, 512),
}

# What a close-up is, stated firmly enough to beat the character's own tags.
_PORTRAIT_FRAMING = ["close-up", "upper_body", "detailed_face", "looking_at_viewer"]

# Four life slices. Used when the character herself does not supply one.
_VIGNETTE_FALLBACK = {
    "hobby": ("reading book", "casual clothes"),
    "active": ("tennis", "sportswear"),
    "food": ("eating", "crepe"),
    "work": ("cafe staff", "working"),
}
# Likes are written as prose ("tea gone cold"), so the food vignette needs the
# food word out of the sentence rather than the sentence.
_FOOD_HINTS = (
    "coffee", "tea", "cake", "popsicle", "ice cream", "bread", "candy",
    "chocolate", "ramen", "curry", "snack", "sweets", "drink", "soda", "juice",
    "crepe", "parfait", "donut", "cookie", "sandwich", "bento",
)
_ACTIVE_HINTS = ("running", "swimming", "stretching", "walking", "cycling", "surf",
                 "tennis", "dancing", "climbing", "skating")

_NEGATIVE = (
    "lowres, worst quality, low quality, bad anatomy, bad hands, "
    "missing fingers, extra digits, fewer digits, malformed limbs, "
    "extra limbs, deformed, mutated, disfigured, bad proportions, "
    "jpeg artifacts, signature, watermark, text"
)
# The portrait is a single face. The sheet deliberately wants the opposite, so
# this is per-slot rather than shared.
_PORTRAIT_NEGATIVE = _NEGATIVE + (
    ", multiple_views, reference_sheet, character_sheet, collage, split_screen, "
    "full_body, wide_shot, long_shot, multiple_girls, multiple_boys"
)


def _first(values: Any, limit: int = 1) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()][:limit]


def sheet_vignettes(character: dict[str, Any]) -> list[str]:
    """Four life slices, drawn from the character wherever she supplies one."""
    personality = character.get("personality") or {}
    gestures = [str(g) for g in (character.get("gesture_vocab") or []) if g]
    outfit = _first(character.get("outfit_tags"), 2)
    likes = [str(x).lower() for x in (personality.get("likes") or [])]

    hobby_act = gestures[0] if gestures else _VIGNETTE_FALLBACK["hobby"][0]
    hobby_wear = ", ".join(outfit) if outfit else _VIGNETTE_FALLBACK["hobby"][1]

    # The sheet is about range: an active slice identical to the hobby slice
    # wastes one of the four frames.
    active = next(
        (g for g in gestures if any(h in g for h in _ACTIVE_HINTS) and g != hobby_act),
        _VIGNETTE_FALLBACK["active"][0],
    )
    food = next(
        (h for like in likes for h in _FOOD_HINTS if h in like),
        _VIGNETTE_FALLBACK["food"][1],
    )
    # Only a real occupation. `outfit_style` is prose about her wardrobe
    # ("long cardigan over a shirt, skirt to the ankle") and reads as nonsense
    # in a slot that is supposed to say what she does for a living.
    job = str(personality.get("occupation") or "").strip() or _VIGNETTE_FALLBACK["work"][0]

    return [
        f"{hobby_act}, {hobby_wear}",
        f"{active}, {_VIGNETTE_FALLBACK['active'][1]}",
        f"{_VIGNETTE_FALLBACK['food'][0]}, {food}",
        f"{job}, {_VIGNETTE_FALLBACK['work'][1]}",
    ]


def _compile_sheet(character: dict[str, Any]) -> tuple[str, str]:
    identity = [str(t) for t in (character.get("identity_tags") or []) if t]
    outfit = [str(t) for t in (character.get("outfit_tags") or []) if t]
    props = [str(t) for t in (character.get("prop_tags") or []) if t]
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))
    if sig and sig not in props:
        props.insert(0, sig)

    # The centre frame carries the sheet, so it gets an open expression rather
    # than the closed_mouth end of her repertoire.
    expressions = [str(e) for e in (character.get("expression_vocab") or []) if e]
    warm = next(
        (e for e in expressions if any(w in e for w in ("smile", "grin", "blush"))),
        "smile",
    )
    centre = ", ".join(
        ["casual", "leaning_forward", "dynamic posture", warm]
        + ([f"holding {sig}"] if sig else [])
    )
    vignettes = "\n".join(f" - {v}" for v in sheet_vignettes(character))

    positive = (
        f"Character: {', '.join(identity + outfit)},\n"
        f"Accessories: {', '.join(props) if props else 'none'}\n"
        "\n"
        "** Chronicles of Character **\n"
        f"Center/Main : {centre}\n"
        "Around 4 chronicles with polaroid frame ** same hair and eye color **:\n"
        f"{vignettes}\n"
        "Shot: wide_shot, full_body,\n"
        "Effect: cinematic, kodak color, film_grain, blurry_background, hdr, "
        "bokeh, multiple_views, cute,"
    )
    return positive, _NEGATIVE


def _compile_portrait(character: dict[str, Any]) -> tuple[str, str]:
    identity = [str(t) for t in (character.get("identity_tags") or []) if t]
    # Worn-on-the-head accessories are part of the face; everything else she
    # carries argues for a wider shot.
    worn = [t for t in (character.get("prop_tags") or []) if _is_head_prop(str(t))]

    ordered: list[str] = []
    for group in (identity, worn, _PORTRAIT_FRAMING):
        for tag in group:
            if tag and tag not in ordered:
                ordered.append(tag)
    return ", ".join(ordered), _PORTRAIT_NEGATIVE


_HEAD_PROP_HINTS = (
    "glasses", "hair", "headband", "headphones", "hat", "cap", "beret",
    "earring", "choker", "necklace", "eyepatch", "mask", "ribbon", "bow",
    "scrunchie", "barrette", "veil", "crown", "tiara",
)


def _is_head_prop(tag: str) -> bool:
    """Worn at or above the shoulders, so it belongs in a bust shot.

    "hair" covers hairclip, hair_tie, hair_ornament and the rest without an
    enumeration that goes stale every time a preset invents a new one.
    """
    name = tag.lower()
    return any(h in name for h in _HEAD_PROP_HINTS)


def compile_board_slot(preset: dict[str, Any], slot: str) -> tuple[str, str]:
    """``(positive, negative)`` for one board slot of one character."""
    if slot not in BOARD_SLOTS:
        raise ValueError(f"unknown board slot: {slot}")
    character = preset_to_character(preset)
    return _compile_sheet(character) if slot == "sheet" else _compile_portrait(character)

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

The **portrait** is the opposite: a close-up so a human can judge the face at
full size. It keeps her identity and the clothes on her top half, and drops
everything that argues for showing the legs — a long skirt, footwear, a
floor-standing prop. Keeping those is how this slot used to come back as a
second full-body render; dropping the wardrobe entirely is how it came back
bare-shouldered.
"""
from __future__ import annotations

import logging
from typing import Any

from ..tags.split_tags import soft_normalize_tag
from .presets import BOARD_SLOTS, preset_to_character

logger = logging.getLogger(__name__)

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


_PLAN_PROMPT = """\
# ROLE
You are casting five frames of a character reference sheet. You decide what she
is doing in each one.

# THE CHARACTER
{name}
{summary}
Personality: {traits}
Likes: {likes}
Habits and hobbies: {gestures}
Usual clothes: {outfit}
Carries: {props}

# WHAT TO WRITE
A centre pose, and four moments from her life.

- The centre is the biggest frame and it must read as a POSE, not an activity.
  Give it a body posture, a facial expression, and the thing she carries
  ({signature}). It is her standing there being herself, in her usual clothes.
  Shape: "casual, leaning_forward, dynamic posture, smile, holding {signature}".
- The four moments must be four *different* lives — different activity,
  different clothes, different place. Two frames of her reading are one frame
  wasted, and none of them should repeat the centre. Draw them from her
  personality, not from a generic list.
- Each moment is short: a few danbooru-style tags, an action plus what she
  wears or holds. Shape: "tennis, sportswear, headband".

# RULES
- Never mention hair colour, eye colour, body type or age. Those are fixed
  elsewhere and repeating them here can only contradict them.
- Actions and clothes only. No lighting, no camera, no background scenery.
- English only, lowercase, comma-separated.

# OUTPUT (JSON only)
{{"center": "<pose and expression>", "vignettes": ["<1>", "<2>", "<3>", "<4>"]}}"""


def _first(values: Any, limit: int = 1) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()][:limit]


def _joined(values: Any, limit: int = 8) -> str:
    out = [str(v).strip() for v in (values or []) if str(v).strip()][:limit]
    return ", ".join(out) if out else "(unknown)"


async def plan_sheet(
    preset: dict[str, Any],
    ollama,
    *,
    model: str = "",
    num_ctx: int | None = None,
) -> dict[str, Any] | None:
    """Ask a model what she should be doing in the five frames.

    The deterministic version picks from four fixed slots — hobby, something
    active, eating, working — which is fine and always the same. A character
    who repairs clocks and hates crowds gets the same tennis-and-crepe sheet as
    everyone else. This reads her personality instead.

    Returns None on any failure; the caller falls back to the fixed slots, since
    a board that renders something is worth more than a board that renders
    nothing.
    """
    from ..ai.json_util import parse_json_object
    from ..ai.llm_options import llm_options

    character = preset_to_character(preset)
    personality = character.get("personality") or {}
    signature = soft_normalize_tag(str(character.get("signature_prop") or "")) or "her usual thing"
    prompt = _PLAN_PROMPT.format(
        signature=signature,
        name=str(preset.get("name") or preset.get("name_ja") or "").strip() or "(unnamed)",
        summary=str(personality.get("summary") or "").strip() or "(no summary)",
        traits=_joined(personality.get("traits")),
        likes=_joined(personality.get("likes"), 6),
        gestures=_joined(character.get("gesture_vocab"), 8),
        outfit=_joined(character.get("outfit_tags"), 6),
        props=_joined(character.get("prop_tags"), 6),
    )
    try:
        raw = await ollama.generate_text(
            prompt,
            model=model or None,
            options=llm_options(model=model, num_ctx=num_ctx),
            fmt="json",
        )
        parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
    except Exception as exc:
        logger.warning("[characters] sheet plan failed: %s", exc)
        return None

    centre = _clean_line(parsed.get("center"))
    vignettes = [_clean_line(v) for v in (parsed.get("vignettes") or [])]
    vignettes = [v for v in vignettes if v]
    # Four distinct frames or nothing: a plan that repeats itself is worse than
    # the fixed slots, which at least vary by construction.
    if not centre or len(vignettes) < 4 or len({v.lower() for v in vignettes[:4]}) < 4:
        logger.info("[characters] sheet plan unusable, falling back")
        return None
    return {"center": centre, "vignettes": vignettes[:4]}


def _clean_line(value: Any) -> str:
    """One vignette line: comma-separated, no stray quoting or numbering."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    text = str(value or "").strip().strip('"').strip()
    parts = [p.strip().strip("-").strip() for p in text.split(",")]
    return ", ".join(p for p in parts if p)[:160]


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


def _compile_sheet(
    character: dict[str, Any], plan: dict[str, Any] | None = None,
) -> tuple[str, str]:
    identity = [str(t) for t in (character.get("identity_tags") or []) if t]
    outfit = [str(t) for t in (character.get("outfit_tags") or []) if t]
    props = [str(t) for t in (character.get("prop_tags") or []) if t]
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))
    if sig and sig not in props:
        props.insert(0, sig)

    if plan:
        centre = str(plan.get("center") or "")
        lines = list(plan.get("vignettes") or [])
    else:
        # The centre frame carries the sheet, so it gets an open expression
        # rather than the closed_mouth end of her repertoire.
        expressions = [str(e) for e in (character.get("expression_vocab") or []) if e]
        warm = next(
            (e for e in expressions if any(w in e for w in ("smile", "grin", "blush"))),
            "smile",
        )
        centre = ", ".join(
            ["casual", "leaning_forward", "dynamic posture", warm]
            + ([f"holding {sig}"] if sig else [])
        )
        lines = sheet_vignettes(character)
    vignettes = "\n".join(f" - {v}" for v in lines)

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


_LOWER_BODY_HINTS = (
    "skirt", "pants", "trousers", "shorts", "jeans", "legwear", "pantyhose",
    "thighhighs", "kneehighs", "socks", "stockings", "shoes", "boots",
    "loafers", "sandals", "sneakers", "heels", "footwear",
)


def _shows_the_legs(tag: str) -> bool:
    name = tag.lower()
    return any(h in name for h in _LOWER_BODY_HINTS)


def _compile_portrait(character: dict[str, Any]) -> tuple[str, str]:
    identity = [str(t) for t in (character.get("identity_tags") or []) if t]
    # Her top half still needs clothes — dropping the wardrobe wholesale left
    # one render bare-shouldered. Only garments that argue for showing the legs
    # come out.
    upper = [t for t in (character.get("outfit_tags") or []) if not _shows_the_legs(str(t))]
    # Worn-on-the-head accessories are part of the face; everything else she
    # carries argues for a wider shot.
    worn = [t for t in (character.get("prop_tags") or []) if _is_head_prop(str(t))]

    ordered: list[str] = []
    for group in (identity, upper, worn, _PORTRAIT_FRAMING):
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


def compile_board_slot(
    preset: dict[str, Any], slot: str, plan: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """``(positive, negative)`` for one board slot of one character.

    ``plan`` is ``plan_sheet``'s output. Without one the sheet falls back to the
    fixed four slots, so this stays a pure function and the board still renders
    when no model is reachable.
    """
    if slot not in BOARD_SLOTS:
        raise ValueError(f"unknown board slot: {slot}")
    character = preset_to_character(preset)
    if slot == "sheet":
        return _compile_sheet(character, plan)
    return _compile_portrait(character)

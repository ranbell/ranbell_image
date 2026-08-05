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

# A bust shot, not a face crop. `close-up` + `detailed_face` framed so tightly
# that it cut above the collarbone, and the cardigan it had been handed never
# entered the picture — the render came back bare-shouldered.
_PORTRAIT_FRAMING = [
    "upper_body", "portrait", "detailed_face", "looking_at_viewer", "cowboy_shot",
]

# Four life slices. Used when the character herself does not supply one.
# Used only where the character supplies nothing at all. Each one is chosen to
# be neutral rather than specific: guessing "tennis" for someone who repairs
# clocks is worse than saying she walks, because a wrong specific renders.
_VIGNETTE_FALLBACK = {
    "hobby": ("relaxing", "casual clothes"),
    "active": ("walking", "sportswear"),
    "food": ("eating", "snack"),
    "work": ("working", "working"),
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
    "full_body, wide_shot, long_shot, multiple_girls, multiple_boys, "
    "nude, topless, bare_shoulders, face_focus, extreme_close-up"
)


_PLAN_PROMPT = """\
# ROLE
You are casting five frames of a character reference sheet. You decide what she
is doing in each one.

# THE CHARACTER
{name} — known for: {title}
{summary}
Personality: {traits}
Likes: {likes}
Habits and hobbies: {gestures}
Usual clothes: {outfit}
Carries: {props}
The moment that is most her: {moment}
Where she usually is: {vibe}

# WHAT TO WRITE
A centre pose, a scene, and four moments from her life.

- The centre is the biggest frame and it must read as a POSE, not an activity.
  Give it a body posture, a facial expression, and the thing she carries
  ({signature}). It is her being herself, in her usual clothes.
  Build the posture out of HER habits above and the moment that is most her.
  Do NOT reach for a stock pose — "leaning_forward", "dynamic posture" and
  "hand on hip" are what every character gets when nobody thinks, and thirty
  sheets of that are thirty of the same picture.
  Shape: "<her posture>, <her expression>, holding {signature}".
- The scene is where the centre happens: her place and her hour, three or four
  tags. Take it from where she usually is, above.
- The four moments fill four fixed roles, in this order:
    1. what she does for a living, or the thing she is known for
    2. resting or off duty — somewhere that is NOT her workplace
    3. moving her body: sport, walking, anything physical
    4. eating or drinking something
  Fill each role from HER — her habits, her tastes — but keep the roles. Four
  frames of her at work is three frames wasted, and none should repeat the
  centre. Different clothes and a different place in every one.
- Each moment is TAGS, not a sentence: three to five danbooru-style tags, an
  action plus what she wears or holds. Write "tennis, sportswear, headband",
  never "she plays tennis on a summer afternoon". A sentence makes the frame
  render as a landscape instead of a portrait of her.

# RULES
- Never mention hair colour, eye colour, body type or age. Those are fixed
  elsewhere and repeating them here can only contradict them.
- Actions and clothes only. No lighting, no camera, no background scenery.
- English only, lowercase, comma-separated.

# OUTPUT (JSON only)
{{"center": "<pose and expression>", "scene": "<where the centre happens>",
 "vignettes": ["<1>", "<2>", "<3>", "<4>"]}}"""


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
        title=str(personality.get("title") or "").strip() or "(nothing in particular)",
        summary=str(personality.get("summary") or "").strip() or "(no summary)",
        traits=_joined(personality.get("traits")),
        likes=_joined(personality.get("likes"), 6),
        gestures=_joined(character.get("gesture_vocab"), 8),
        outfit=_joined(character.get("outfit_tags"), 6),
        props=_joined(character.get("prop_tags"), 6),
        moment=str(personality.get("signature_moment") or "").strip() or "(none recorded)",
        vibe=_joined(personality.get("vibe_keywords"), 4),
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
    scene = _clean_line(parsed.get("scene"))
    vignettes = [_clean_line(v) for v in (parsed.get("vignettes") or [])]
    vignettes = [v for v in vignettes if v]
    # Four distinct frames or nothing: a plan that repeats itself is worse than
    # the fixed slots, which at least vary by construction.
    if not centre or len(vignettes) < 4 or len({v.lower() for v in vignettes[:4]}) < 4:
        logger.info("[characters] sheet plan unusable, falling back")
        return None
    return {"center": centre, "scene": scene, "vignettes": vignettes[:4]}


def _clean_line(value: Any) -> str:
    """One vignette line: comma-separated, no stray quoting or numbering."""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    text = str(value or "").strip().strip('"').strip()
    parts = [p.strip().strip("-").strip() for p in text.split(",")]
    return ", ".join(p for p in parts if p)[:160]


# Postures that only read at full length. A centre frame is a full-body shot,
# so a gesture that happens sitting on a floor needs the rest of the sentence.
_SEATED = ("sitting", "kneeling", "lying", "crouching", "seiza", "head_on_arms")


def centre_pose(character: dict[str, Any]) -> str:
    """The pose the centre frame puts her in — hers, not the house one.

    Every character used to be handed `casual, leaning_forward, dynamic
    posture`, which is a stock pose, so thirty reference sheets came back with
    thirty people leaning at the viewer in different clothes. The point of the
    sheet is to check that *this* character renders, and she has her own
    posture written down: her gestures, her expression, the thing she carries.
    """
    gestures = [str(g).strip() for g in (character.get("gesture_vocab") or []) if str(g).strip()]
    expressions = [str(e) for e in (character.get("expression_vocab") or []) if e]
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))

    # The centre frame carries the sheet, so it gets an open expression rather
    # than the closed_mouth end of her repertoire.
    warm = next(
        (e for e in expressions if any(w in e for w in ("smile", "grin", "blush", "happy"))),
        "smile",
    )
    # A bare posture is the least informative thing she does. `holding_book`
    # tells you who she is; `sitting` tells you she has a chair.
    plain = ("sitting", "standing", "walking", "kneeling", "crouching")
    pose = next((g for g in gestures if g not in plain), "") or (
        gestures[0] if gestures else "standing"
    )
    parts = [pose]
    second = next(
        (g for g in gestures[1:] if not any(w in g for w in _SEATED) and g != pose), "",
    )
    if second:
        parts.append(second)
    if not any(w in pose for w in _SEATED):
        parts.append("full_body, standing" if pose == "standing" else "full_body")
    parts.append(warm)
    if sig:
        parts.append(f"holding {sig}")
    out: list[str] = []
    for part in parts:
        if part and part not in out:
            out.append(part)
    return ", ".join(out)


def scene_line(character: dict[str, Any]) -> str:
    """Where she is. Her own place and hour, not a grey studio.

    A reference board with no setting renders against seamless white, which
    tells you the tags are fine and nothing about whether she belongs anywhere.
    `vibe_keywords` is exactly this — the place, the light, the hour — and was
    sitting unused.
    """
    personality = character.get("personality") or {}
    vibe = [str(v).strip() for v in (personality.get("vibe_keywords") or []) if str(v).strip()]
    return ", ".join(vibe[:4])


def _pick(values, *, avoid=(), match=(), default: str = "") -> str:
    """First value that matches (if given) and is not already used."""
    for v in values:
        text = str(v).strip()
        if not text or text in avoid:
            continue
        if match and not any(m in text.lower() for m in match):
            continue
        return text
    return default


def sheet_vignettes(character: dict[str, Any]) -> list[str]:
    """Four life slices, drawn from the character wherever she supplies one.

    The four roles are fixed — what she does, off duty, moving, eating — because
    four frames of her at work is three frames wasted. What fills them is not:
    this used to fall through to `tennis, sportswear` and `cafe staff` for
    anyone whose tags did not happen to match a hint list, which was almost
    everyone, so the sheets differed only in the clothes.
    """
    personality = character.get("personality") or {}
    gestures = [str(g) for g in (character.get("gesture_vocab") or []) if g]
    outfit = _first(character.get("outfit_tags"), 2)
    likes = [str(x).strip() for x in (personality.get("likes") or []) if str(x).strip()]
    vibe = [str(v).strip() for v in (personality.get("vibe_keywords") or []) if str(v).strip()]
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))

    usual = ", ".join(outfit) if outfit else _VIGNETTE_FALLBACK["hobby"][1]

    # 1. What she is known for: her prop and her posture, where she does it.
    work_act = gestures[0] if gestures else _VIGNETTE_FALLBACK["work"][0]
    work = ", ".join(x for x in (work_act, f"holding {sig}" if sig else "", usual) if x)

    # 2. Moving, picked BEFORE off-duty so it gets first claim on the athletic
    #    gesture. Taking them in frame order let the off-duty slot swallow the
    #    only sport she had, and the moving frame then fell back to walking.
    #    Only claim a sport if one of her own gestures is one — a character who
    #    repairs clocks does not play tennis, and saying she does is worse than
    #    saying she walks.
    hers = _pick(gestures, match=_ACTIVE_HINTS, avoid={work_act})
    active = hers or _VIGNETTE_FALLBACK["active"][0]
    # Dress her for sport only when the sport is hers. Testing the *word*
    # against the hint list put the fallback in sportswear too, because
    # "walking" is on that list, so twenty-nine of thirty went jogging.
    if hers:
        moving = f"{active}, {_VIGNETTE_FALLBACK['active'][1]}"
    else:
        # Not an athlete. Then it is her moving through her own day, which at
        # least differs per character instead of being one stock frame.
        where = vibe[-1] if vibe else "outdoors"
        moving = f"{active}, casual clothes, {where}"

    # 3. Off duty: another posture of hers, in her own clothes, explicitly
    #    somewhere other than the place she works.
    off_act = _pick(gestures[1:], avoid={work_act, active},
                    default=_VIGNETTE_FALLBACK["hobby"][0])
    off = f"{off_act}, casual clothes, outdoors"

    # 4. Eating or drinking, from what she actually likes.
    food = _pick(
        (h for like in likes for h in _FOOD_HINTS if h in like.lower()),
        default=_VIGNETTE_FALLBACK["food"][1],
    )
    eating = f"{_VIGNETTE_FALLBACK['food'][0]}, {food}, casual clothes"

    lines = [work, off, moving, eating]
    # The frames have to differ or one of them is wasted. Where two collided,
    # separate them with her own scenery rather than dropping to a stock word.
    seen: set[str] = set()
    for i, line in enumerate(lines):
        key = line.lower()
        if key in seen and vibe:
            lines[i] = f"{line}, {vibe[i % len(vibe)]}"
        seen.add(key)
    return lines


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
        scene = str(plan.get("scene") or "") or scene_line(character)
    else:
        centre = centre_pose(character)
        lines = sheet_vignettes(character)
        scene = scene_line(character)
    vignettes = "\n".join(f" - {v}" for v in lines)

    positive = (
        f"Character: {', '.join(identity + outfit)},\n"
        f"Accessories: {', '.join(props) if props else 'none'}\n"
        "\n"
        "** Chronicles of Character **\n"
        f"Center/Main : {centre}\n"
        + (f"Scene: {scene}\n" if scene else "")
        + "Around 4 chronicles with polaroid frame ** same hair and eye color **:\n"
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

    # Her face, not a passport photo. The bust shot used to carry no expression
    # at all, so thirty characters came back with the same neutral stare and the
    # slot could not tell you whether the personality reads.
    expressions = [str(e) for e in (character.get("expression_vocab") or []) if e]
    face = expressions[:2]
    scene = scene_line(character)
    background = ["blurry_background", "depth_of_field"] if scene else []

    ordered: list[str] = []
    for group in (identity, upper, worn, face, _PORTRAIT_FRAMING, background):
        for tag in group:
            if tag and tag not in ordered:
                ordered.append(tag)
    positive = ", ".join(ordered)
    if scene:
        positive = f"{positive},\nScene: {scene}"
    return positive, _PORTRAIT_NEGATIVE


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

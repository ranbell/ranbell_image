"""Board / sample / final prompt builders for Weave renders."""
from __future__ import annotations

from typing import Any

from ..character.split_tags import soft_normalize_tag
from ..compile.cameras import CAMERA_FORCE_ADD, strip_framing_conflicts
from ..compile.layers import WEAVE_NEGATIVE, compile_panel


# Only two board renders exist: the sheet (fed to panel generation as the
# reference) and a close-up so a human can judge the face at full size.
_PORTRAIT_PURPOSE = ["close-up", "upper_body", "detailed_face", "looking_away"]


# The sheet shows the same person across four lives in one image —
# `multiple_views` plus polaroid framing does the work.
_SHEET_FALLBACK = {
    "hobby": ("reading book", "casual clothes"),
    "active": ("tennis", "sportswear"),
    "food": ("eating", "crepe"),
    "work": ("cafe staff", "working"),
}
# Rough food words so a like ("cheap popsicles") can fill the eating vignette.
_FOOD_HINTS = (
    "coffee", "tea", "cake", "popsicle", "ice cream", "bread", "candy",
    "chocolate", "ramen", "curry", "snack", "sweets", "drink", "soda", "juice",
    "crepe", "parfait", "donut", "cookie", "sandwich", "bento",
)
_ACTIVE_HINTS = ("running", "swimming", "stretching", "walking", "cycling", "surf")


def _first(values: Any, limit: int = 1) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()][:limit]


def _sheet_vignettes(character: dict[str, Any]) -> list[str]:
    """Four life slices for the sheet, drawn from the character where possible."""
    personality = character.get("personality") or {}
    gestures = [str(g) for g in (character.get("gesture_vocab") or []) if g]
    outfit = _first(character.get("outfit_tags"), 2)
    likes = [str(x).lower() for x in (personality.get("likes") or [])]

    hobby_act = gestures[0] if gestures else _SHEET_FALLBACK["hobby"][0]
    hobby_wear = ", ".join(outfit) if outfit else _SHEET_FALLBACK["hobby"][1]

    # The sheet is about range: an active slice identical to the hobby slice
    # wastes one of the four frames.
    active = next(
        (
            g for g in gestures
            if any(h in g for h in _ACTIVE_HINTS) and g != hobby_act
        ),
        _SHEET_FALLBACK["active"][0],
    )
    # Likes are prose ("tea gone cold") — take the food word, not the sentence.
    food = next(
        (h for like in likes for h in _FOOD_HINTS if h in like),
        _SHEET_FALLBACK["food"][1],
    )
    job = str(
        personality.get("occupation") or personality.get("occupation_hint") or ""
    ).strip() or _SHEET_FALLBACK["work"][0]

    return [
        f"{hobby_act}, {hobby_wear}",
        f"{active}, {_SHEET_FALLBACK['active'][1]}",
        f"{_SHEET_FALLBACK['food'][0]}, {food}",
        f"{job}, {_SHEET_FALLBACK['work'][1]}",
    ]


def compile_character_sheet(session: dict[str, Any]) -> dict[str, str]:
    """One image, several views: centre pose plus four polaroid vignettes."""
    character = session.get("character") or {}
    identity = [str(t) for t in (character.get("identity_tags") or []) if t]
    outfit = [str(t) for t in (character.get("outfit_tags") or []) if t]
    props = [str(t) for t in (character.get("prop_tags") or []) if t]
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))
    if sig and sig not in props:
        props.insert(0, sig)
    expressions = [str(e) for e in (character.get("expression_vocab") or []) if e]
    # The centre pose carries the sheet — prefer an open expression over the
    # closed_mouth/serious end of her repertoire.
    warm = next(
        (e for e in expressions if any(w in e for w in ("smile", "grin", "blush"))),
        "smile",
    )
    # The centre also carries the throughline prop — there is no prop slot to
    # confirm it separately any more.
    centre = ", ".join(
        ["casual", "leaning_forward", "dynamic posture", warm]
        + ([f"holding {sig}"] if sig else [])
    )
    vignettes = "\n".join(f" - {v}" for v in _sheet_vignettes(character))
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
    neg_bits = [WEAVE_NEGATIVE]
    for raw in character.get("do_not") or []:
        t = soft_normalize_tag(str(raw)) if str(raw).isascii() else str(raw).strip()
        if t:
            neg_bits.append(t)
    return {
        "positive": positive,
        "negative": ", ".join(neg_bits),
        "camera": "long_shot",
        "slot": "sheet",
    }


def compile_board_slot(session: dict[str, Any], slot: str) -> dict[str, str]:
    if slot == "sheet":
        return compile_character_sheet(session)

    character = session.get("character") or {}
    # Board shows who she is in her own clothes — the story's per-topic wardrobe
    # belongs to the panels, not to the reference sheet.
    identity = list(character.get("identity_tags") or []) + list(
        character.get("outfit_tags") or []
    )
    props = list(character.get("prop_tags") or [])
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))
    if sig and sig not in props:
        props.append(sig)

    camera = "close_up"
    cam_tags = list(CAMERA_FORCE_ADD.get(camera, _PORTRAIT_PURPOSE))
    tags = identity + cam_tags
    # Strip framing conflicts for the intended camera
    tags = strip_framing_conflicts(tags, camera if camera != "close_up" else "close_up")
    # Dedup
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        k = soft_normalize_tag(t) if str(t).isascii() else str(t)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    positive = ", ".join(out)
    neg_bits = [WEAVE_NEGATIVE]
    for raw in character.get("do_not") or []:
        t = soft_normalize_tag(str(raw)) if str(raw).isascii() else str(raw).strip()
        if t:
            neg_bits.append(t)
    return {
        "positive": positive,
        "negative": ", ".join(neg_bits),
        "camera": camera,
        "slot": slot,
    }


def compile_panel_render(
    session: dict[str, Any],
    panel_key: str,
    *,
    env_boost: bool = False,
) -> dict[str, str]:
    # Prefer existing compile if present
    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if panel and (panel.get("compile") or {}).get("positive") and not env_boost:
        c = panel["compile"]
        return {
            "positive": c.get("positive") or "",
            "negative": c.get("negative") or WEAVE_NEGATIVE,
            "camera": (panel.get("intent") or {}).get("camera") or "",
            "panel_key": panel_key,
        }
    compiled = compile_panel(session, panel_key, env_boost=env_boost)
    return {
        "positive": compiled.get("positive") or "",
        "negative": compiled.get("negative") or WEAVE_NEGATIVE,
        "camera": compiled.get("camera") or "",
        "panel_key": panel_key,
    }

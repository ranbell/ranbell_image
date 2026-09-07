"""Deterministic assemble + optional WD14 reference + optional quality pass.

WD14 vector hits are **reference only**. They are never dumped into the prompt.
When enhance_quality is on, the enrich pass may pick from the closed SUGGESTED
set (same spirit as Muse review MISSING:) — noise stays noise unless chosen.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ..muse import identity
from . import anima
from . import debug as debug_mod
from . import ledger as ledger_mod
from . import talk

logger = logging.getLogger(__name__)

_QUALITY_SYSTEM = """You enrich an image-generation prompt's atmosphere ONLY.
Keep every fact about clothes, pose, and place from LEDGER unchanged.
Add mood, air, color temperature, subtle environmental detail, and quality
boosters as comma-separated tags (spaces preferred — Anima style).
Do NOT rename garments. Do NOT move the location. Do NOT change the pose stem.
Do NOT use (tag:weight) emphasis. Do NOT repeat LEDGER facts.

If SUGGESTED is present, those words are a closed vocabulary from the studio
WD14 bank (vector neighbours — often noisy). You may pick useful ones from
SUGGESTED only. Never invent clothes/place words that fight LEDGER.
You may also add ordinary quality/atmosphere tags not in SUGGESTED
(masterpiece, best quality, soft lighting, depth of field, etc.) as long as
they do not change clothes, pose, or place. Prefer "masterpiece, best quality"
over score_* tags.

Output ONE line of tags only. No labels. No prose.
"""


def _phrase_to_tags(phrase: str) -> list[str]:
    text = (phrase or "").strip()
    if not text:
        return []
    if "," in text or ";" in text:
        parts = re.split(r"[,;]+", text)
        return [p.strip().replace(" ", "_") for p in parts if p.strip()]
    return [text.replace(" ", "_")]


def ledger_tag_bag(ledger: dict[str, str]) -> list[str]:
    bag: list[str] = []
    for key in (
        "wearing", "beat", "expression", "scene", "light", "bg", "frame",
        "wearing_b", "beat_b", "atmosphere", "look",
    ):
        bag.extend(_phrase_to_tags(ledger.get(key) or ""))
    seen: set[str] = set()
    out: list[str] = []
    for t in bag:
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out


def scene_prose(
    ledger: dict[str, str],
    *,
    partner: bool = False,
    name_a: str = "",
    name_b: str = "",
) -> str:
    """Cinematic English SCENE paragraph for Anima (tags + longer NL).

    Ownership stays split: lead clothes/pose never attributed to the partner.
    Atmosphere / look thicken mood and render without triple-locking facts.
    """
    wearing = (ledger.get("wearing") or "").strip()
    beat = (ledger.get("beat") or "").strip()
    expression = (ledger.get("expression") or "").strip()
    scene = (ledger.get("scene") or "").strip()
    light = (ledger.get("light") or "").strip()
    bg = (ledger.get("bg") or "").strip()
    frame = (ledger.get("frame") or "").strip()
    wearing_b = (ledger.get("wearing_b") or "").strip()
    beat_b = (ledger.get("beat_b") or "").strip()
    atmosphere = (ledger.get("atmosphere") or "").strip()
    look = (ledger.get("look") or "").strip()
    lead = (name_a or "She").strip() or "She"
    other = (name_b or "Her partner").strip() or "Her partner"

    if not any((
        wearing, beat, expression, scene, light, bg, frame,
        wearing_b, beat_b, atmosphere, look,
    )):
        return ""

    parts: list[str] = []

    # Opening stage — one flowing sentence when possible.
    stage: list[str] = []
    if scene:
        if scene.lower().startswith(("at ", "in ", "on ", "inside ", "outside ")):
            stage.append(scene)
        else:
            stage.append(f"at {scene}")
    if bg and bg.lower() not in (scene or "").lower():
        stage.append(
            f"{bg} stretching behind them" if partner else f"{bg} stretching behind her"
        )
    if light:
        if any(w in light.lower() for w in ("light", "sun", "glow", "lamp", "neon", "rim")):
            stage.append(f"bathed in {light}")
        else:
            stage.append(f"lit by {light}")
    if stage:
        parts.append("The frame opens " + ", ".join(stage) + ".")
    else:
        parts.append(
            "The frame holds them in a quiet beat."
            if partner else
            "The frame holds her in a quiet beat."
        )

    # Lead — clothes + body + face as readable prose (not telegraphic labels).
    lead_bits: list[str] = []
    if wearing:
        lead_bits.append(f"wearing {wearing}")
    if beat:
        lead_bits.append(beat)
    if expression:
        lead_bits.append(f"with {expression} on her face")
    if lead_bits:
        # Prefer named subject for Anima multi-char guidance.
        if wearing and beat and expression:
            parts.append(
                f"{lead} is {lead_bits[0]}, {lead_bits[1]}, {lead_bits[2]}."
            )
        elif wearing and beat:
            parts.append(f"{lead} is {lead_bits[0]}, {lead_bits[1]}.")
        else:
            parts.append(f"{lead} is " + ", ".join(lead_bits) + ".")

    if partner or wearing_b or beat_b:
        other_bits: list[str] = []
        if wearing_b:
            other_bits.append(f"wearing {wearing_b}")
        if beat_b:
            other_bits.append(beat_b)
        if other_bits:
            parts.append(f"{other} is " + ", ".join(other_bits) + ".")
        parts.append(
            f"Do not swap clothes or hairstyles between {lead} and {other}; "
            "they share one place and one moment."
        )

    if atmosphere:
        parts.append(
            f"The air feels {atmosphere} — mood first, not a new wardrobe."
        )
    if look:
        parts.append(f"Render the picture as {look}.")
    if frame:
        parts.append(f"Camera stays {frame}.")

    # Deterministic visible consequences (craft-only — never written back to ledger).
    cues = visible_consequence_cues(ledger)
    for hint in cues.get("hints") or []:
        parts.append(hint)

    return " ".join(parts)


# Physical state → what the camera would actually see (craft-only expansion).
_WIND_RE = re.compile(
    r"\b(wind|breeze|gust|blown|blowing|floating\s*hair|hair\s*(?:blown|blowing|streaming|whipping))\b"
    r"|風|靡|なび|そよ風|強風",
    re.I,
)
_BEHIND_RE = re.compile(
    r"\b(from\s*behind|rear\s*view|back\s*view|from\s*the\s*back|seen\s*from\s*behind|"
    r"back\s*to\s*(?:the\s*)?(?:camera|viewer)|facing\s*away)\b"
    r"|後ろ|背面|うしろ|後ろ姿|背中向|背面から",
    re.I,
)
_LOOK_BACK_RE = re.compile(
    r"\b(looking\s*back|looks?\s*back|over\s*(?:her|the)\s*shoulder|"
    r"glance\s*back|turned\s*(?:her\s*)?head)\b"
    r"|振り返|振り向き|肩越し|後ろを見",
    re.I,
)
_SIDE_RE = re.compile(
    r"\b(from\s*side|side\s*view|profile|three[- ]?quarter)\b"
    r"|横顔|横から|横向き|プロフィール",
    re.I,
)
_WET_RE = re.compile(
    r"\b(rain|wet|soaked|drenched|sweat(?:y|ing)?)\b"
    r"|雨|濡れ|びしょ|汗",
    re.I,
)
_SIT_RE = re.compile(
    r"\b(sitting|seated|crouch(?:ing|ed)?|kneel(?:ing|ed)?)\b"
    r"|座|しゃが|膝立ち|跪",
    re.I,
)


def _ledger_sight_text(ledger: dict[str, str]) -> str:
    return " ".join(
        str(ledger.get(k) or "")
        for k in (
            "beat", "beat_b", "expression", "atmosphere", "frame",
            "light", "scene", "bg", "wearing",
        )
    )


def visible_consequence_cues(ledger: dict[str, str]) -> dict[str, Any]:
    """Infer camera-visible effects from ledger state — craft-only, not ledger writes.

    Example: wind-blown hair → floating hair motion and often a visible nape when
    the view is rear/side; from_behind → nape / shoulder line rather than a full face.
    """
    text = _ledger_sight_text(ledger)
    tags: list[str] = []
    hints: list[str] = []
    wind = bool(_WIND_RE.search(text))
    behind = bool(_BEHIND_RE.search(text) or _BEHIND_RE.search(str(ledger.get("frame") or "")))
    look_back = bool(_LOOK_BACK_RE.search(text))
    side = bool(_SIDE_RE.search(text))
    wet = bool(_WET_RE.search(text))
    sitting = bool(_SIT_RE.search(text))

    if wind:
        tags.append("floating_hair")
        if behind or side or look_back:
            tags.append("nape")
            hints.append(
                "Wind pulls her hair forward and aside, so the nape of her neck "
                "and the line of her throat stay visible — strands stream across "
                "her shoulders without inventing a new haircut."
            )
        else:
            hints.append(
                "Wind lifts and streams her hair; flyaways catch the light and "
                "brush her cheeks and collar — motion you can see, not a new style."
            )

    if behind:
        if "nape" not in tags:
            tags.append("nape")
        tags.append("from_behind")
        if look_back:
            tags.append("looking_back")
            hints.append(
                "Seen from behind, her shoulders and nape lead the frame; she "
                "glances back over one shoulder so only a sliver of her face returns."
            )
        elif not any("from behind" in h.lower() or "Seen from behind" in h for h in hints):
            hints.append(
                "The camera reads her from behind — shoulder blades, nape, and "
                "the fall of her hair — not a frontal portrait."
            )

    if wet and not wind:
        tags.append("wet_hair")
        hints.append(
            "Damp strands cling along her neck and temples; fabric darkens where "
            "it touches skin — wetness as a visible surface, not new clothes."
        )

    if sitting and ("lap" in text.lower() or "膝" in text):
        hints.append(
            "Seated weight settles through her hips and thighs; folds gather at "
            "the knees and the seat edge where cloth meets the surface."
        )

    # Dedupe tags preserving order
    seen: set[str] = set()
    uniq_tags: list[str] = []
    for t in tags:
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        uniq_tags.append(t)

    return {
        "tags": uniq_tags,
        "hints": hints[:3],
        "needs_dense": bool(uniq_tags or hints or wind or behind),
    }


def _person_box(
    session: dict[str, Any],
    *,
    wearing: str,
    beat: str,
    expression: str = "",
    extra_beat_tags: list[str] | None = None,
) -> dict[str, list[str]]:
    """One Muse's dynamic tags — clothes / pose / face only."""
    wear = talk.filter_banned_tags(session, _phrase_to_tags(wearing))
    pose = _phrase_to_tags(beat)
    for t in extra_beat_tags or []:
        tag = str(t or "").strip().replace(" ", "_")
        if tag and tag.lower() not in {p.lower() for p in pose}:
            pose.append(tag)
    face = _phrase_to_tags(expression)
    return {"wearing": wear, "beat": pose, "face": face}


def _frame_wide_tags(ledger: dict[str, str]) -> list[str]:
    """Shared picture tags — place / light / bg / camera / mood. Never clothes or hair."""
    bag: list[str] = []
    for key in ("scene", "light", "bg", "frame", "atmosphere"):
        bag.extend(_phrase_to_tags(ledger.get(key) or ""))
    seen: set[str] = set()
    out: list[str] = []
    for t in bag:
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out


def _combined_style(session: dict[str, Any], ledger: dict[str, str]) -> str:
    """Panel style input + conversation-driven look (look wins as append)."""
    inputs = session.get("inputs") or {}
    base = str(inputs.get("style") or "").strip()
    look = str(ledger.get("look") or "").strip()
    if base and look:
        return f"{base}, {look}"
    return look or base


def assemble_prompt(
    session: dict[str, Any],
    ledger: dict[str, str],
    *,
    support_tags: list[str] | None = None,
    scene_override: str | None = None,
    enhance_quality: bool | None = None,
) -> str:
    """Identity-first positive with per-person ownership (Muse box path).

    Lead clothes/pose/face never share a flat bag with the partner's — same
    rule as classic Muse ``assemble_from_boxes`` so hair and outfits do not swap.
    Final string is Anima-hygiened (spaces, quality prefix, blank-line prose).
    """
    char = session.get("character") or {}
    partner = session.get("partner_character") or {}
    has_partner = bool(partner and str(partner.get("character_id") or "").strip())
    name_a = str(char.get("name_ja") or char.get("name") or "Lead")
    name_b = str(partner.get("name_ja") or partner.get("name") or "Partner")
    inputs = session.get("inputs") or {}
    framing = str(inputs.get("framing") or "auto")
    style = _combined_style(session, ledger)
    quality_on = (
        bool(enhance_quality) if enhance_quality is not None
        else bool(inputs.get("enhance_quality"))
    )
    prose = (
        scene_override if scene_override is not None
        else scene_prose(
            ledger, partner=has_partner, name_a=name_a, name_b=name_b,
        )
    )
    raw_support = talk.filter_banned_tags(
        session,
        [str(t).strip().replace(" ", "_") for t in (support_tags or []) if str(t).strip()],
    )
    quality_tags, atmosphere = anima.split_quality_support(raw_support)

    cast = [char]
    cues = visible_consequence_cues(ledger)
    # Consequence tags ride the lead beat box (hair/body visibility), never
    # the shared frame-wide mood bag — ownership stays with the actress.
    lead_extra = list(cues.get("tags") or [])
    people = [
        _person_box(
            session,
            wearing=str(ledger.get("wearing") or ""),
            beat=str(ledger.get("beat") or ""),
            expression=str(ledger.get("expression") or ""),
            extra_beat_tags=lead_extra,
        ),
    ]
    if has_partner:
        cast.append(partner)
        people.append(
            _person_box(
                session,
                wearing=str(ledger.get("wearing_b") or ""),
                beat=str(ledger.get("beat_b") or ""),
                expression="",  # no expression_b — do not copy lead's face onto B
            ),
        )

    boxed = identity.assemble_from_boxes(
        cast=cast,
        people=people,
        frame_wide=_frame_wide_tags(ledger),
        style=style,
        framing=framing,
        scene=prose,
        support=atmosphere,
    )
    if not boxed:
        # Fallback (no usable identity tags): flat path, still without mixing bags.
        identity_tags = [
            t for t in (char.get("identity_tags") or [])
            if str(t).strip() and str(t).strip().lower() not in {"1girl", "solo"}
        ]
        bag = talk.filter_banned_tags(
            session,
            _phrase_to_tags(str(ledger.get("wearing") or ""))
            + _phrase_to_tags(str(ledger.get("beat") or ""))
            + _phrase_to_tags(str(ledger.get("expression") or ""))
            + _frame_wide_tags(ledger),
        )
        if atmosphere:
            bag = merge_support_tags(bag, atmosphere, authority=bag)
        boxed = identity.assemble_positive(
            ["1girl", *identity_tags] if identity_tags else ["1girl"],
            ", ".join(bag),
            prose,
            framing=framing,
            style=style,
            cast=[char] if char else None,
        )

    lettering_raw = str(ledger.get("lettering") or "").strip()
    lettering = [lettering_raw] if lettering_raw else []
    return anima.format_for_anima(
        boxed or "",
        quality_tags=quality_tags,
        lettering=lettering,
        enhance_quality=quality_on,
    )


_PROSE_DENSIFY = """You densify a shot SCENE paragraph for Anima / FLUX-natural.
Keep EVERY fact from LEDGER and BASE PROSE unchanged — clothes, pose, face,
place, light, background, camera, atmosphere, look. Do not rename garments.
Do not move the place. Do not invent props that fight the ledger.
If two people are present, NEVER swap clothes, hairstyles, or body traits
between them — keep each person's ownership exact.
Lean into ATMOSPHERE and LOOK when present: sensory mood and render medium
(cel, fantasy glow, watercolor bleed, etc.) without adding new wardrobe.

VISIBLE CONSEQUENCES (required when state implies them):
Read beat / atmosphere / frame / light as a photograph — name what the camera
would actually SEE because of that state, not abstract feelings alone.
Examples:
- Hair blown by wind → streaming strands AND often a visible nape / neck line
  when the view is rear, side, or looking-back (do not invent a new haircut).
- from_behind / rear view → nape, shoulder blades, hair fall — not a frontal face
  unless looking_back is already in the ledger.
- Wet / rain → clinging strands, darkened fabric where it touches skin.
- Seated weight → cloth folds at knees/hips where body meets the seat.
Do NOT invent new garments, places, or props. Do NOT write consequences back as
new ledger fields — only render them in the prose.

Write 3–5 flowing English sentences (about 90–180 words). Name each person,
then their appearance — do not list bare names alone.
Do NOT restate the same fact three times. Do NOT dump a "Keep exactly" list.
No (tag:weight). No comma-tag lists. Output the paragraph only.
"""


async def densify_scene_prose(
    ollama,
    *,
    model: str,
    ledger: dict[str, str],
    base_prose: str,
) -> str:
    """Optional LLM thicken — ledger facts stay absolute; consequences are craft-only."""
    if not base_prose.strip() or ollama is None:
        return base_prose
    cues = visible_consequence_cues(ledger)
    hint_block = ""
    if cues.get("hints") or cues.get("tags"):
        hint_block = (
            "\nVISIBLE HINTS (must appear naturally if compatible with ledger):\n"
            + "\n".join(f"- {h}" for h in (cues.get("hints") or []))
            + (
                "\n- Prefer sampler cues already implied: "
                + ", ".join(cues.get("tags") or [])
                if cues.get("tags") else ""
            )
            + "\n"
        )
    prompt = (
        f"{_PROSE_DENSIFY}\n\n"
        f"LEDGER:\n"
        f"wearing: {ledger.get('wearing')}\n"
        f"beat: {ledger.get('beat')}\n"
        f"expression: {ledger.get('expression')}\n"
        f"scene: {ledger.get('scene')}\n"
        f"light: {ledger.get('light')}\n"
        f"bg: {ledger.get('bg')}\n"
        f"frame: {ledger.get('frame')}\n"
        f"wearing_b: {ledger.get('wearing_b')}\n"
        f"beat_b: {ledger.get('beat_b')}\n"
        f"lettering: {ledger.get('lettering')}\n"
        f"atmosphere: {ledger.get('atmosphere')}\n"
        f"look: {ledger.get('look')}\n"
        f"{hint_block}\n"
        f"BASE PROSE:\n{base_prose}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] prose densify failed")
        return base_prose
    text = " ".join((raw or "").strip().split())
    if len(text) < 40:
        return base_prose
    # Soft guard: require scene or wearing stem to survive.
    must = []
    for key in ("scene", "wearing", "beat"):
        phrase = (ledger.get(key) or "").strip().lower()
        if phrase:
            # first token-ish chunk
            must.append(phrase.split(",")[0].strip().split()[0])
    low = text.lower()
    if must and not any(m and m in low for m in must):
        return base_prose
    return text[:900]


async def fetch_wd14_suggestions(
    db,
    ollama,
    ledger: dict[str, str],
    *,
    limit_per_axis: int = 8,
) -> list[str]:
    """Reference-only WD14 vocab near beat/expression/scene. May be noisy."""
    try:
        from ..invoke import vocab_bank
    except Exception:
        logger.exception("[muse_refine] vocab_bank import failed")
        return []

    axes: dict[str, str] = {}
    if (ledger.get("beat") or "").strip():
        axes["pose"] = ledger["beat"]
    if (ledger.get("expression") or "").strip():
        axes["expression"] = ledger["expression"]
    if (ledger.get("scene") or "").strip():
        axes["scene"] = ledger["scene"]
    if (ledger.get("light") or "").strip():
        axes["lighting"] = ledger["light"]
    if not axes:
        topic = " ".join(
            p for p in (
                ledger.get("wearing"),
                ledger.get("beat"),
                ledger.get("scene"),
            ) if p
        ).strip()
        if not topic:
            return []
        try:
            return list(await vocab_bank.get_topic_tags(
                db, ollama, topic, limit=limit_per_axis * 2,
            ))[: limit_per_axis * 2]
        except Exception:
            logger.exception("[muse_refine] get_topic_tags failed")
            return []

    try:
        tags = await vocab_bank.get_axis_semantic_tags(
            db, ollama, axes, limit=limit_per_axis,
        )
        return list(tags or [])
    except Exception:
        logger.exception("[muse_refine] get_axis_semantic_tags failed")
        return []


def merge_support_tags(
    base: list[str],
    support: Iterable[str],
    *,
    authority: Iterable[str],
    cap: int = 24,
) -> list[str]:
    """Append chosen support tags that do not collide with authority stems."""
    auth = {a.lower() for a in authority}
    auth_stems = {a.split("_")[0] for a in auth if a}
    out = list(base)
    seen = {t.lower() for t in out}
    for raw in support:
        tag = str(raw or "").strip().replace(" ", "_")
        if not tag:
            continue
        low = tag.lower()
        if low in seen or low in auth:
            continue
        stem = low.split("_")[0]
        if stem and stem in auth_stems and low not in auth:
            if stem not in {"1girl", "solo", "looking", "open", "closed"}:
                if any(stem == a.split("_")[0] for a in auth if "_" in a):
                    continue
        out.append(tag)
        seen.add(low)
        if len(out) - len(base) >= cap:
            break
    return out


def _split_picked_vs_free(
    parts: list[str],
    suggested: list[str],
) -> tuple[list[str], list[str]]:
    """When SUGGESTED was given, tags in that set are picks; others are free atmosphere."""
    sug = {s.lower() for s in suggested}
    picked: list[str] = []
    free: list[str] = []
    for p in parts:
        if p.lower() in sug:
            picked.append(p)
        else:
            free.append(p)
    return picked, free


async def quality_enrich(
    ollama,
    *,
    model: str,
    ledger: dict[str, str],
    base_tags: list[str],
    suggested: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Returns (picked_from_suggested, free_quality_tags)."""
    sug = [str(s).strip() for s in (suggested or []) if str(s).strip()]
    sug_block = (
        f"SUGGESTED (closed vocab — pick useful only, ignore noise):\n"
        f"{', '.join(sug)}\n\n"
        if sug else
        "SUGGESTED: (none)\n\n"
    )
    prompt = (
        f"{_QUALITY_SYSTEM}\n\n"
        f"LEDGER:\n"
        f"wearing: {ledger.get('wearing')}\n"
        f"beat: {ledger.get('beat')}\n"
        f"scene: {ledger.get('scene')}\n"
        f"light: {ledger.get('light')}\n"
        f"bg: {ledger.get('bg')}\n\n"
        f"{sug_block}"
        f"BASE TAGS:\n{', '.join(base_tags)}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] quality enrich failed")
        return [], []
    line = (raw or "").strip().splitlines()[0] if raw else ""
    parts = [p.strip().replace(" ", "_") for p in line.split(",") if p.strip()]
    auth_phrases = {
        (ledger.get(k) or "").strip().lower().replace(" ", "_")
        for k in ("wearing", "beat", "scene", "bg")
        if (ledger.get(k) or "").strip()
    }
    parts = [p for p in parts if p.lower() not in auth_phrases]
    if sug:
        return _split_picked_vs_free(parts, sug)
    return [], parts


async def rebuild_craft(
    db,
    ollama,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Refresh craft from ledger. WD14 hits stay reference-only unless picked."""
    import time

    inputs = session.get("inputs") or {}
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    wd14: list[str] = []
    picked_wd14: list[str] = []
    quality_tags: list[str] = []

    if bool(inputs.get("use_wd14")) and ollama is not None:
        t0 = time.monotonic()
        wd14 = await fetch_wd14_suggestions(db, ollama, led)
        debug_mod.stage(session, "wd14_suggest", t0)
        debug_mod.note(
            session, "wd14_suggestions",
            detail=f"{len(wd14)} refs (not auto-injected)",
            tags=wd14[:40],
        )

    base_bag = talk.filter_banned_tags(session, ledger_tag_bag(led))
    if bool(inputs.get("enhance_quality")) and ollama is not None:
        t0 = time.monotonic()
        model = str(inputs.get("model") or "")
        # Pass WD14 only as closed SUGGESTED — never dump the whole neighbour set.
        picked_wd14, quality_tags = await quality_enrich(
            ollama,
            model=model,
            ledger=led,
            base_tags=base_bag,
            suggested=wd14 if bool(inputs.get("use_wd14")) else None,
        )
        debug_mod.stage(session, "quality_enrich", t0)
        debug_mod.note(
            session, "quality_enrich",
            detail="picked from SUGGESTED + free atmosphere",
            picked_wd14=picked_wd14[:40],
            quality_tags=quality_tags[:40],
        )
    elif bool(inputs.get("use_wd14")) and wd14:
        # WD14 alone: reference display only — do not inject into the prompt.
        debug_mod.note(
            session, "wd14_reference_only",
            detail="use_wd14 on, enhance_quality off → suggestions not injected",
            tags=wd14[:40],
        )

    chosen = list(picked_wd14) + list(quality_tags)
    partner = session.get("partner_character") or {}
    has_partner = bool(partner.get("character_id"))
    char = session.get("character") or {}
    name_a = str(char.get("name_ja") or char.get("name") or "Lead")
    name_b = str(partner.get("name_ja") or partner.get("name") or "Partner")
    prose = scene_prose(
        led, partner=has_partner, name_a=name_a, name_b=name_b,
    )
    cues = visible_consequence_cues(led)
    # Densify when quality/mood/look is on, OR when state implies visible
    # physical consequences (wind, rear view, etc.).
    want_dense = bool(inputs.get("enhance_quality")) or bool(
        (led.get("atmosphere") or "").strip() or (led.get("look") or "").strip()
    ) or bool(cues.get("needs_dense"))
    if want_dense and ollama is not None and prose:
        t0 = time.monotonic()
        model = str(inputs.get("model") or "")
        denser = await densify_scene_prose(
            ollama, model=model, ledger=led, base_prose=prose,
        )
        debug_mod.stage(session, "prose_densify", t0)
        if denser and denser != prose:
            debug_mod.note(session, "prose_densify", detail=denser[:240])
            prose = denser
    if cues.get("tags") or cues.get("hints"):
        debug_mod.note(
            session, "visible_consequences",
            detail=", ".join(cues.get("tags") or [])[:120],
            tags=list(cues.get("tags") or [])[:20],
            hints=list(cues.get("hints") or [])[:3],
        )
    prompt = assemble_prompt(
        session, led,
        support_tags=chosen or None,
        scene_override=prose,
        enhance_quality=bool(inputs.get("enhance_quality")),
    )
    locale = str(inputs.get("locale") or "ja")
    craft = dict(session.get("craft") or {})
    craft["prompt"] = prompt
    craft["now"] = ledger_mod.now_line(led, locale=locale)
    craft["tags"] = ", ".join(ledger_tag_bag(led))
    craft["scene"] = prose
    # Keep names clear in the panel / debug.
    craft["wd14_suggestions"] = ", ".join(wd14)
    craft["picked_wd14"] = ", ".join(picked_wd14)
    craft["quality_tags"] = ", ".join(quality_tags)
    craft["support_tags"] = ", ".join(chosen)
    session["craft"] = craft
    session["refine_ledger"] = led
    return session

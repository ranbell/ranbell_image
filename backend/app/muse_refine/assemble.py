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
        "wearing_b", "beat_b",
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
    """Thick English SCENE paragraph — locks every ledger axis into prose.

    Tags alone drift; this paragraph is the firm reinforcement that restates
    clothes, pose, face, place, light, and camera as one readable moment.
    Deterministic: no invention beyond ledger phrases.
    Ownership stays split: lead clothes/pose never attributed to the partner.
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
    lead = (name_a or "She").strip() or "She"
    other = (name_b or "Her partner").strip() or "Her partner"

    if not any((wearing, beat, expression, scene, light, bg, frame, wearing_b, beat_b)):
        return ""

    parts: list[str] = []

    # Place + light + background as the stage (no pose assumption).
    stage_bits: list[str] = []
    if scene:
        if scene.lower().startswith(("at ", "in ", "on ", "inside ", "outside ")):
            stage_bits.append(scene)
        else:
            stage_bits.append(f"at {scene}")
    if bg and bg.lower() not in (scene or "").lower():
        stage_bits.append(f"with {bg} behind them" if partner else f"with {bg} behind her")
    if light:
        if any(w in light.lower() for w in ("light", "sun", "glow", "lamp", "neon")):
            stage_bits.append(f"under {light}")
        else:
            stage_bits.append(f"lit by {light}")
    if stage_bits:
        parts.append("The shot is set " + ", ".join(stage_bits) + ".")
    else:
        parts.append("The shot holds them in frame." if partner else "The shot holds her in frame.")

    if wearing:
        parts.append(f"{lead} is wearing {wearing}.")
    if beat:
        parts.append(f"{lead}'s body: {beat}.")
    if expression:
        parts.append(f"{lead}'s face: {expression}.")

    if partner or wearing_b or beat_b:
        if wearing_b:
            parts.append(f"{other} is wearing {wearing_b}.")
        if beat_b:
            parts.append(f"{other}'s body: {beat_b}.")
        parts.append(
            f"Do not swap clothes or hairstyles between {lead} and {other}. "
            "Both share the same place and moment."
        )

    if frame:
        parts.append(f"Camera: {frame}.")

    # Anima / Qwen: do NOT restate the same facts a third time ("Keep exactly"
    # used to triple-lock and the community guide flags 3× concept repeats).
    # Ownership already lives once in tags + once in the sentences above.

    return " ".join(parts)


def _person_box(
    session: dict[str, Any],
    *,
    wearing: str,
    beat: str,
    expression: str = "",
) -> dict[str, list[str]]:
    """One Muse's dynamic tags — clothes / pose / face only."""
    wear = talk.filter_banned_tags(session, _phrase_to_tags(wearing))
    pose = _phrase_to_tags(beat)
    face = _phrase_to_tags(expression)
    return {"wearing": wear, "beat": pose, "face": face}


def _frame_wide_tags(ledger: dict[str, str]) -> list[str]:
    """Shared picture tags — place / light / bg / camera. Never clothes or hair."""
    bag: list[str] = []
    for key in ("scene", "light", "bg", "frame"):
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
    style = str(inputs.get("style") or "")
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
    people = [
        _person_box(
            session,
            wearing=str(ledger.get("wearing") or ""),
            beat=str(ledger.get("beat") or ""),
            expression=str(ledger.get("expression") or ""),
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
place, light, background, camera. Do not rename garments. Do not move the place.
Do not invent props that fight the ledger.
If two people are present, NEVER swap clothes, hairstyles, or body traits
between them — keep each person's ownership exact.
Write 2–4 flowing English sentences (about 60–140 words). Name each person,
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
    """Optional LLM thicken — ledger facts stay absolute."""
    if not base_prose.strip() or ollama is None:
        return base_prose
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
        f"lettering: {ledger.get('lettering')}\n\n"
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
    if bool(inputs.get("enhance_quality")) and ollama is not None and prose:
        t0 = time.monotonic()
        model = str(inputs.get("model") or "")
        denser = await densify_scene_prose(
            ollama, model=model, ledger=led, base_prose=prose,
        )
        debug_mod.stage(session, "prose_densify", t0)
        if denser and denser != prose:
            debug_mod.note(session, "prose_densify", detail=denser[:240])
            prose = denser
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

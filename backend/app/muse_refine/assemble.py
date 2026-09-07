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
from . import debug as debug_mod
from . import ledger as ledger_mod

logger = logging.getLogger(__name__)

_QUALITY_SYSTEM = """You enrich an image-generation prompt's atmosphere ONLY.
Keep every fact about clothes, pose, and place from LEDGER unchanged.
Add mood, air, color temperature, subtle environmental detail, and quality
boosters as comma-separated danbooru-style tags (underscores ok).
Do NOT rename garments. Do NOT move the location. Do NOT change the pose stem.

If SUGGESTED is present, those words are a closed vocabulary from the studio
WD14 bank (vector neighbours — often noisy). You may pick useful ones from
SUGGESTED only. Never invent clothes/place words that fight LEDGER.
You may also add ordinary quality/atmosphere tags not in SUGGESTED
(masterpiece, soft_lighting, depth_of_field, etc.) as long as they do not
change clothes, pose, or place.

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


def scene_prose(ledger: dict[str, str]) -> str:
    bits = [
        (ledger.get("scene") or "").strip(),
        (ledger.get("bg") or "").strip(),
        (ledger.get("light") or "").strip(),
    ]
    return ". ".join(b for b in bits if b)


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


def assemble_prompt(
    session: dict[str, Any],
    ledger: dict[str, str],
    *,
    support_tags: list[str] | None = None,
) -> str:
    """Identity-first positive from ledger (+ optional *chosen* support tags)."""
    char = session.get("character") or {}
    partner = session.get("partner_character") or {}
    identity_tags = list(char.get("identity_tags") or [])
    bag = ledger_tag_bag(ledger)
    if partner and str(partner.get("character_id") or "").strip():
        # W-Muse: keep both girls visible without dumping partner wardrobe into lead.
        if not any(t.lower() in {"2girls", "multiple_girls"} for t in identity_tags + bag):
            bag = ["2girls", *bag]
        for t in list(partner.get("identity_tags") or [])[:8]:
            if str(t).strip() and str(t).strip().lower() not in {
                x.lower() for x in identity_tags
            }:
                bag.append(str(t).strip())
    if support_tags:
        bag = merge_support_tags(bag, support_tags, authority=bag)
    tags = ", ".join(bag)
    scene = scene_prose(ledger)
    framing = str((session.get("inputs") or {}).get("framing") or "auto")
    style = str((session.get("inputs") or {}).get("style") or "")
    cast = [c for c in (char, partner) if c and str(c.get("character_id") or "").strip()]
    return identity.assemble_positive(
        identity_tags,
        tags,
        scene,
        framing=framing,
        style=style,
        cast=cast or ([char] if char else None),
    )


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

    base_bag = ledger_tag_bag(led)
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
    prompt = assemble_prompt(session, led, support_tags=chosen or None)
    locale = str(inputs.get("locale") or "ja")
    craft = dict(session.get("craft") or {})
    craft["prompt"] = prompt
    craft["now"] = ledger_mod.now_line(led, locale=locale)
    craft["tags"] = ", ".join(ledger_tag_bag(led))
    craft["scene"] = scene_prose(led)
    # Keep names clear in the panel / debug.
    craft["wd14_suggestions"] = ", ".join(wd14)
    craft["picked_wd14"] = ", ".join(picked_wd14)
    craft["quality_tags"] = ", ".join(quality_tags)
    craft["support_tags"] = ", ".join(chosen)
    session["craft"] = craft
    session["refine_ledger"] = led
    return session

"""Deterministic assemble + optional WD14 reference + optional quality pass."""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ..muse import identity
from . import ledger as ledger_mod

logger = logging.getLogger(__name__)

# Soft support tags — never override ledger authority fields.
_QUALITY_SYSTEM = """You enrich an image-generation prompt's atmosphere ONLY.
Keep every fact about clothes, pose, and place from LEDGER unchanged.
Add mood, air, color temperature, subtle environmental detail, and quality
boosters as comma-separated danbooru-style tags (underscores ok).
Do NOT rename garments. Do NOT move the location. Do NOT change the pose stem.
Output ONE line of tags only. No labels. No prose.
"""


def _phrase_to_tags(phrase: str) -> list[str]:
    text = (phrase or "").strip()
    if not text:
        return []
    # Prefer comma/semicolon splits; otherwise keep as underscored phrase.
    if "," in text or ";" in text:
        parts = re.split(r"[,;]+", text)
        return [p.strip().replace(" ", "_") for p in parts if p.strip()]
    return [text.replace(" ", "_")]


def ledger_tag_bag(ledger: dict[str, str]) -> list[str]:
    bag: list[str] = []
    for key in ("wearing", "beat", "expression", "scene", "light", "bg", "frame"):
        bag.extend(_phrase_to_tags(ledger.get(key) or ""))
    # De-dupe, keep order.
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
    """Reference-only WD14 vocab near beat/expression/scene. May be empty."""
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
    """Append support tags that do not collide with authority stems."""
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
        # Soft collision: same stem as an authority garment/place word → skip.
        if stem and stem in auth_stems and low not in auth:
            # still allow smile/light boosters etc. when stem is generic
            if stem in {"1girl", "solo", "looking", "open", "closed"}:
                pass
            elif any(stem == a.split("_")[0] for a in auth if "_" in a):
                continue
        out.append(tag)
        seen.add(low)
        if len(out) - len(base) >= cap:
            break
    return out


async def quality_enrich(
    ollama,
    *,
    model: str,
    ledger: dict[str, str],
    base_tags: list[str],
) -> list[str]:
    prompt = (
        f"{_QUALITY_SYSTEM}\n\n"
        f"LEDGER:\n"
        f"wearing: {ledger.get('wearing')}\n"
        f"beat: {ledger.get('beat')}\n"
        f"scene: {ledger.get('scene')}\n"
        f"light: {ledger.get('light')}\n"
        f"bg: {ledger.get('bg')}\n\n"
        f"BASE TAGS:\n{', '.join(base_tags)}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] quality enrich failed")
        return []
    line = (raw or "").strip().splitlines()[0] if raw else ""
    parts = [p.strip().replace(" ", "_") for p in line.split(",") if p.strip()]
    # Drop anything that rewrites authority phrases wholesale.
    auth_phrases = {
        (ledger.get(k) or "").strip().lower().replace(" ", "_")
        for k in ("wearing", "beat", "scene", "bg")
        if (ledger.get(k) or "").strip()
    }
    return [p for p in parts if p.lower() not in auth_phrases]


def assemble_prompt(
    session: dict[str, Any],
    ledger: dict[str, str],
    *,
    support_tags: list[str] | None = None,
) -> str:
    """Identity-first positive from ledger (+ optional support tags)."""
    char = session.get("character") or {}
    identity_tags = list(char.get("identity_tags") or [])
    bag = ledger_tag_bag(ledger)
    if support_tags:
        bag = merge_support_tags(bag, support_tags, authority=bag)
    tags = ", ".join(bag)
    scene = scene_prose(ledger)
    framing = str((session.get("inputs") or {}).get("framing") or "auto")
    style = str((session.get("inputs") or {}).get("style") or "")
    return identity.assemble_positive(
        identity_tags,
        tags,
        scene,
        framing=framing,
        style=style,
        cast=[char] if char else None,
    )


async def rebuild_craft(
    db,
    ollama,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Refresh craft.prompt / craft.now from ledger and options."""
    inputs = session.get("inputs") or {}
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    support: list[str] = []

    if bool(inputs.get("use_wd14")):
        support.extend(await fetch_wd14_suggestions(db, ollama, led))

    base_bag = ledger_tag_bag(led)
    if bool(inputs.get("enhance_quality")) and ollama is not None:
        model = str(inputs.get("model") or "")
        support.extend(await quality_enrich(
            ollama, model=model, ledger=led, base_tags=base_bag + support,
        ))

    # Pin authority tags at the front of support merge via assemble_prompt.
    prompt = assemble_prompt(session, led, support_tags=support)
    locale = str(inputs.get("locale") or "ja")
    craft = dict(session.get("craft") or {})
    craft["prompt"] = prompt
    craft["now"] = ledger_mod.now_line(led, locale=locale)
    craft["tags"] = ", ".join(ledger_tag_bag(led))
    craft["scene"] = scene_prose(led)
    craft["support_tags"] = ", ".join(support)
    session["craft"] = craft
    session["refine_ledger"] = led
    return session

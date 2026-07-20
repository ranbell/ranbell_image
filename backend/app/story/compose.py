"""Allowlist compose + labeled positive formatting for Chronicle Phase 2.

Permission lists are catalog ∩ selected_tags (hyphen/underscore normalized).
Soft-normalization strips color / school_ / student_ prefixes before filter.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import settings
from ..tags import catalog as cat
from .generator import expression_tag_for_feeling

logger = logging.getLogger(__name__)

AXES = ("past", "present", "future")

COSTUME_EXTRA: frozenset[str] = frozenset({
    "wrist_cuffs", "detached_collar", "fishnet_pantyhose", "playboy_bunny",
    "leotard", "bowtie", "necktie",
})

PRESENT_EXCLUSIVE_DEFAULT: frozenset[str] = frozenset({
    "pouring", "coffee_cup", "steam",
})

_COLOR_PREFIXES = (
    "white_", "black_", "grey_", "gray_", "red_", "blue_", "green_", "pink_",
    "brown_", "yellow_", "orange_", "purple_", "silver_", "gold_", "dark_", "light_",
)

_SOFT_MAP = {
    "school_blazer": "blazer",
    "dark_jeans": "jeans",
    "student_cardigan": "cardigan",
    "pleated_skirt": "skirt",
    "community_gallery": "indoors",
    "blue_sky": "sky",
    "close_up": "close-up",
}

_FEELING_ALIASES = {
    "focused": "serious",
    "concentrating": "serious",
    "overwhelmed": "worried",
    "confident": "smile",
    "proud": "smile",
    "relieved": "smile",
    "stressed": "worried",
    "uncertain": "nervous",
    "careful": "serious",
    "serenity": "closed_eyes",
}


def _hyph(s: str) -> str:
    return s.strip().lower().replace(" ", "_").replace("-", "_")


def _vocab_path() -> Path:
    p = Path(settings.wd14_model_dir) / "selected_tags.csv"
    if p.is_file():
        return p
    alt = Path(__file__).resolve().parents[3] / "private" / "selected_tags.csv"
    return alt


@lru_cache(maxsize=1)
def load_selected_vocab() -> frozenset[str]:
    path = _vocab_path()
    if not path.is_file():
        logger.warning("[compose] selected_tags.csv missing at %s", path)
        return frozenset()
    names: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip().lower()
            if name:
                names.add(name)
                names.add(name.replace("-", "_"))
                names.add(name.replace("_", "-"))
    return frozenset(names)


def _intersect(catalog_tags: frozenset[str], vocab: frozenset[str]) -> list[str]:
    out: set[str] = set()
    for t in catalog_tags:
        key = t.lower()
        if key in vocab or key.replace("-", "_") in vocab or key.replace("_", "-") in vocab:
            # Prefer underscore form except close-up
            if key == "close-up" or key == "close_up":
                out.add("close-up")
            else:
                out.add(key.replace("-", "_") if key != "close-up" else key)
    return sorted(out)


@lru_cache(maxsize=1)
def chronicle_allowlists() -> dict[str, list[str]]:
    vocab = load_selected_vocab()
    outfit = set(_intersect(cat.CLOTHING_EXPLICIT | cat.ACCESSORIES | cat.RACE, vocab))
    outfit |= {t for t in COSTUME_EXTRA if t in vocab or t.replace("-", "_") in vocab}
    effect_src = (
        cat.PROPS | cat.VISUAL_LIGHTING | cat.ENVIRONMENT | cat.BACKGROUND
    )
    return {
        "pose": _intersect(cat.POSE, vocab),
        "emo": _intersect(cat.EXPRESSION, vocab),
        "shot": _intersect(cat.COMPOSITION, vocab),
        "outfit": sorted(outfit),
        "effect": _intersect(effect_src, vocab),
    }


def soft_normalize_tag(tag: str) -> str:
    t = _hyph(tag)
    if t in _SOFT_MAP:
        t = _SOFT_MAP[t]
    if t == "close_up":
        return "close-up"
    for c in _COLOR_PREFIXES:
        if t.startswith(c) and t[len(c):]:
            return soft_normalize_tag(t[len(c):])
    if t.startswith("school_") and len(t) > 7:
        return soft_normalize_tag(t[7:])
    if t.startswith("student_") and len(t) > 8:
        return soft_normalize_tag(t[8:])
    if t.replace("_", "-") == "close-up":
        return "close-up"
    return t


def parse_tag_field(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        parts = [str(x) for x in val]
    else:
        parts = re.split(r"[,，、]", str(val))
    return [soft_normalize_tag(p) for p in parts if str(p).strip()]


def filter_to_allowlist(
    tags: list[str],
    allowed: set[str] | frozenset[str] | list[str],
    *,
    ban: set[str] | frozenset[str] | None = None,
    limit: int = 8,
) -> list[str]:
    allow = set(allowed)
    # also accept hyphen variants in shot
    allow_hyphen = {a.replace("_", "-") for a in allow} | allow
    ban = set(ban or ())
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        cands = [t, t.replace("-", "_"), t.replace("_", "-")]
        hit = next((c for c in cands if c in allow_hyphen), None)
        if not hit or hit in ban or hit in seen:
            continue
        # Canonical: prefer underscore except close-up
        canon = "close-up" if hit in ("close-up", "close_up") else hit.replace("-", "_")
        if canon == "close_up":
            canon = "close-up"
        if canon not in allow and hit in allow:
            canon = hit
        if canon in ban or canon in seen:
            continue
        # If only hyphen form in allow (close-up)
        if canon not in allow and hit in allow_hyphen:
            canon = hit if hit in allow else canon
        seen.add(canon)
        out.append(canon)
        if len(out) >= limit:
            break
    return out


def map_expression(feeling: str, *, emo_allow: set[str] | list[str]) -> str:
    emo = set(emo_allow)
    raw = expression_tag_for_feeling(feeling or "")
    f = (feeling or "").lower()
    t = _FEELING_ALIASES.get(raw, _FEELING_ALIASES.get(f, raw))
    for k, v in _FEELING_ALIASES.items():
        if k in f:
            t = v
            break
    t = soft_normalize_tag(t)
    if t in emo and t != "looking_at_viewer":
        return t
    if "worried" in emo and any(x in f for x in ("stress", "overwhelm", "anxious")):
        return "worried"
    if "serious" in emo:
        return "serious"
    if "smile" in emo:
        return "smile"
    return next(iter(sorted(emo)), "smile")


def format_summary(label: str, activity: str, place: str) -> str:
    lab = (label or "").strip()
    act = (activity or "").strip()
    pl = (place or "").strip()
    if pl.lower().startswith(("at ", "in ", "on ", "behind ", "outside ", "near ")):
        body = f"{act}, {pl}" if act else pl
    elif pl:
        body = f"{act}, {pl}" if act else pl
    else:
        body = act
    return f"{lab} — {body}" if lab else body


def format_labeled_positive(
    *,
    summary: str,
    character: list[str],
    outfit: list[str],
    pose: list[str],
    shot: list[str],
    effect: list[str],
) -> str:
    return "\n".join([
        f"Summary: {summary}",
        f"Character: {', '.join(character)}",
        f"Outfit: {', '.join(outfit)}",
        f"Pose: {', '.join(pose)}",
        f"Shot: {', '.join(shot)}",
        f"Effect: {', '.join(effect)}",
    ])


def build_compose_prompt(
    *,
    title: str,
    throughline: str,
    acts: dict[str, dict],
    time_scale: str,
    identity_hint: str = "grey_hair, red_eyes",
    present_exclusive: frozenset[str] | None = None,
) -> str:
    allow = chronicle_allowlists()
    excl = ", ".join(sorted(present_exclusive or PRESENT_EXCLUSIVE_DEFAULT))

    def _fmt(name: str, items: list[str], n: int = 90) -> str:
        body = ", ".join(items[:n])
        return f"{name} ({len(items)}): {body}" + (" ..." if len(items) > n else "")

    acts_json = json.dumps(acts, ensure_ascii=False, indent=2)
    return (
        "Compose Danbooru tags for a 3-panel anime chronicle. "
        "Use ONLY tags from the allowlists. Return JSON.\n\n"
        f"IDENTITY (do NOT invent hair/eye colors; injected later): {identity_hint}\n"
        f"TIME SCALE: {time_scale}\n"
        f"TITLE: {title}\n"
        f"THROUGHLINE: {throughline}\n\n"
        f"ACTS:\n{acts_json}\n\n"
        "ALLOWLISTS:\n"
        f"{_fmt('POSE', allow['pose'])}\n"
        f"{_fmt('OUTFIT', allow['outfit'], 110)}\n"
        f"{_fmt('SHOT', allow['shot'], 30)}\n"
        f"{_fmt('EFFECT', allow['effect'], 130)}\n\n"
        "FEW-SHOT (style only — do not copy):\n"
        'present: Pose=["pouring"] Outfit=["apron","blouse"] Shot=["close-up"] '
        'Effect=["cafe","counter","coffee_cup","steam"]\n'
        'past: Pose=["sitting","hand_on_own_chin"] Outfit=["school_uniform"] '
        'Shot=["upper_body"] Effect=["classroom","desk","notebook"]\n'
        'future: Pose=["standing"] Outfit=["cardigan","skirt"] Shot=["cowboy_shot"] '
        'Effect=["indoors","light_rays"]\n\n'
        "HARD RULES:\n"
        "- Keys: past, present, future. Each: pose(1-2), outfit(2-4), shot(1), effect(3-6).\n"
        "- Arrays of strings only. No color prefixes (blouse not white_blouse).\n"
        "- present must match FIXED act; include pouring/cafe/counter when present is cafe pour.\n"
        f"- past/future MUST NOT use: {excl}\n"
        "- Three DIFFERENT shot values. Outfits differ across acts when scale≥days.\n"
        "- Exhibition/presentation poses: prefer standing (not spread_arms/hands_up).\n\n"
        'Return ONLY JSON: {"past":{"pose":[],"outfit":[],"shot":[],"effect":[]},'
        '"present":{...},"future":{...}}\n'
    )


def parse_compose_json(raw: str) -> dict[str, dict]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for ax in AXES:
        block = data.get(ax)
        if isinstance(block, dict):
            out[ax] = block
    return out


def filter_compose_result(
    composed: dict[str, dict],
    acts: dict[str, dict],
    *,
    identity: list[str],
    base_axis: str = "present",
    present_exclusive: frozenset[str] | None = None,
) -> dict[str, dict]:
    """Soft-filter compose JSON → per-axis labeled fields + positive string."""
    allow = chronicle_allowlists()
    pose_a, emo_a, shot_a = set(allow["pose"]), set(allow["emo"]), set(allow["shot"])
    outfit_a, effect_a = set(allow["outfit"]), set(allow["effect"])
    excl = set(present_exclusive or PRESENT_EXCLUSIVE_DEFAULT)
    used_shots: set[str] = set()
    result: dict[str, dict] = {}

    for ax in AXES:
        block = composed.get(ax) or {}
        act = acts.get(ax) or {}
        ban = set() if ax == base_axis else excl

        pose = filter_to_allowlist(parse_tag_field(block.get("pose")), pose_a, ban=ban, limit=2)
        if not pose:
            pose = ["standing"] if "standing" in pose_a else []
        if ax == base_axis and "pouring" in pose_a and "pouring" in (
            (act.get("activity") or "") + " " + " ".join(parse_tag_field(block.get("pose")))
        ).lower():
            pose = ["pouring"] + [p for p in pose if p != "pouring"]
            pose = pose[:2]

        outfit = filter_to_allowlist(parse_tag_field(block.get("outfit")), outfit_a, ban=ban, limit=4)
        for t in filter_to_allowlist(parse_tag_field(act.get("outfit")), outfit_a, ban=ban, limit=4):
            if t not in outfit:
                outfit.append(t)
        outfit = outfit[:4]
        if ax == base_axis:
            for req in parse_tag_field(act.get("outfit")):
                r = soft_normalize_tag(req)
                if r in outfit_a and r not in outfit:
                    outfit.insert(0, r)
            outfit = outfit[:4]
        if not outfit:
            outfit = ["shirt"] if "shirt" in outfit_a else []

        shot_raw = block.get("shot")
        if isinstance(shot_raw, str):
            shot_raw = [shot_raw]
        shot = filter_to_allowlist(parse_tag_field(shot_raw), shot_a, limit=1)
        if not shot or shot[0] in used_shots:
            for s in ("close-up", "upper_body", "cowboy_shot", "from_side", "full_body", "wide_shot"):
                if s in shot_a and s not in used_shots:
                    shot = [s]
                    break
        if shot:
            used_shots.add(shot[0])

        effect = filter_to_allowlist(parse_tag_field(block.get("effect")), effect_a, ban=ban, limit=6)
        for t in filter_to_allowlist(parse_tag_field(act.get("place")), effect_a, ban=ban, limit=2):
            if t not in effect:
                effect.append(t)
        if ax == base_axis:
            act_blob = f"{act.get('activity', '')} {act.get('place', '')}".lower()
            force_cafe = "cafe" in act_blob or "pour" in act_blob
            for req in ("cafe", "counter", "coffee_cup", "steam"):
                if req not in effect_a or req in effect:
                    continue
                if req in ("coffee_cup", "steam") and "pour" not in act_blob:
                    continue
                if force_cafe or req in act_blob.replace(" ", "_"):
                    effect.append(req)
            effect = effect[:6]

        expr = map_expression(str(act.get("feeling") or ""), emo_allow=emo_a)
        char = list(dict.fromkeys([*identity, expr]))
        summary = format_summary(
            str(act.get("label") or ax),
            str(act.get("activity") or ""),
            str(act.get("place") or ""),
        )
        positive = format_labeled_positive(
            summary=summary,
            character=char,
            outfit=outfit,
            pose=pose,
            shot=shot,
            effect=effect,
        )
        result[ax] = {
            "summary": summary,
            "character": char,
            "outfit": outfit,
            "pose": pose,
            "shot": shot,
            "effect": effect,
            "expression": expr,
            "positive": positive,
            "visual_script": summary,
        }
    return result

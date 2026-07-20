"""Chronicle Phase 2: LLM compose → light filter → labeled positives.

Optional allowlist (catalog ∩ selected_tags). Soft-normalization strips color /
school_ / student_ prefixes. Code injects identity, feeling→expression, and
user forced keywords — not cafe/pose hardcodes.
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
    "calmly_focused": "serious",
    "proud/calmly focused": "serious",
    "stressed/chaotic": "worried",
}


def _hyph(s: str) -> str:
    return s.strip().lower().replace(" ", "_").replace("-", "_")


def parse_csv_tags(text: str, *, strip_colors: bool = True) -> list[str]:
    """Parse comma-separated user tags (keep order, dedupe)."""
    if text is None:
        return []
    parts = re.split(r"[,，、]", str(text))
    out: list[str] = []
    for p in parts:
        if not str(p).strip():
            continue
        tag = soft_normalize_tag(p) if strip_colors else _hyph(p)
        if tag:
            out.append(tag)
    return list(dict.fromkeys(out))


def parse_identity_tags(text: str) -> list[str]:
    """Appearance tags: keep color prefixes (blue_hair, red_eyes)."""
    return parse_csv_tags(text, strip_colors=False)


def axis_keywords_from_body(body) -> dict[str, list[str]]:
    """Read per-axis forced keywords from a ChronicleRequest-like object."""
    return {
        "past": parse_csv_tags(getattr(body, "keywords_past", "") or ""),
        "present": parse_csv_tags(getattr(body, "keywords_present", "") or ""),
        "future": parse_csv_tags(getattr(body, "keywords_future", "") or ""),
    }


def strip_expression_tags(tags: list[str], *, emo: set[str] | frozenset[str] | None = None) -> list[str]:
    """Remove expression/emotion tags from an appearance list."""
    emo_set = set(emo) if emo is not None else set(chronicle_allowlists().get("emo", []))
    # Always strip common face expressions even if allowlist empty
    emo_set |= {
        "smile", "smirk", "grin", "laughing", "sad", "angry", "worried", "nervous",
        "serious", "expressionless", "blush", "closed_eyes", "open_mouth", "frown",
        "looking_at_viewer", "looking_away",
    }
    out: list[str] = []
    for t in tags:
        key = soft_normalize_tag(t)
        if key in emo_set or key.replace("-", "_") in emo_set:
            continue
        out.append(t if t else key)
    return out


def ensure_subject_anchors(tags: list[str]) -> list[str]:
    out = list(tags)
    lower = {t.lower().replace(" ", "_") for t in out}
    for anchor in ("1girl", "solo"):
        if anchor not in lower:
            out.insert(0, anchor)
            lower.add(anchor)
    return out


def identity_candidates_from_wd14(wd14_tags: list[str], *, multi_character: bool = False) -> list[str]:
    """Hair color/style, eyes, accessory pool for identity (no expressions)."""
    from .generator import classify_identity_tag

    allowed = {"hair_color", "hair_style", "eyes", "accessory"}
    if multi_character:
        allowed -= {"hair_color", "eyes"}
    out: list[str] = []
    for tag in wd14_tags or []:
        if classify_identity_tag(tag) in allowed:
            out.append(tag)
    return list(dict.fromkeys(out))


def _bucket_identity(tags: list[str]) -> dict[str, list[str]]:
    from .generator import classify_identity_tag

    buckets: dict[str, list[str]] = {
        "hair_color": [],
        "hair_style": [],
        "eyes": [],
        "accessory": [],
        "face": [],
        "other": [],
    }
    for tag in tags or []:
        cat = classify_identity_tag(tag) or "other"
        buckets.setdefault(cat, []).append(tag)
    return buckets


def resolve_chronicle_identity(
    character_tags_user: str,
    wd14_tags: list[str],
    *,
    rng=None,
    multi_character: bool = False,
    limit: int = 8,
) -> list[str]:
    """User appearance tags win; else WD14 identity with hair/eyes/style kept.

    Random sampling must NOT drop hair_color / eyes / hair_style when present
    in the WD14 pool — those are the traits users notice vanishing.
    """
    import random as _random

    rng = rng or _random
    user = strip_expression_tags(parse_identity_tags(character_tags_user or ""))
    pool = identity_candidates_from_wd14(wd14_tags, multi_character=multi_character)
    pb = _bucket_identity(pool)

    def _pick_required(from_buckets: dict[str, list[str]], existing: list[str]) -> list[str]:
        """Ensure hair_color, eyes, hair_style are present when available."""
        have = _bucket_identity(existing)
        out = list(existing)
        for cat in ("hair_color", "eyes", "hair_style"):
            if have.get(cat):
                continue
            cands = from_buckets.get(cat) or []
            if not cands:
                continue
            # Prefer first WD14 hit (usually highest confidence order); slight
            # randomness only when several options exist.
            chosen = cands[0] if len(cands) == 1 else rng.choice(cands)
            if chosen not in out:
                out.append(chosen)
        return out

    if user:
        # Keep every user tag; backfill missing hair/eyes/style from WD14.
        filled = _pick_required(pb, user)
        return ensure_subject_anchors(filled[: max(limit + 2, len(filled))])

    if not pool:
        return ensure_subject_anchors([])

    # Always take all hair_color + eyes from pool (usually 1 each), plus one
    # hair_style, then fill with accessories / extra styles up to limit.
    picked: list[str] = []
    for cat in ("hair_color", "eyes"):
        for t in pb.get(cat) or []:
            if t not in picked:
                picked.append(t)
    styles = list(pb.get("hair_style") or [])
    if styles:
        picked.append(styles[0] if len(styles) == 1 else rng.choice(styles))
    rest = [
        t for t in pool
        if t not in picked
    ]
    rng.shuffle(rest)
    for t in rest:
        if len(picked) >= limit:
            break
        picked.append(t)
    # Final safety: if shuffle somehow skipped required cats, re-add.
    picked = _pick_required(pb, picked)
    return ensure_subject_anchors(picked)


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
    """Map act feeling → expression. Prefer story fit; smile is last resort."""
    emo = set(emo_allow)
    f = (feeling or "").lower().replace(" ", "_")
    # Alias table first (story feelings often aren't danbooru tags)
    t = ""
    for k, v in _FEELING_ALIASES.items():
        if k.replace(" ", "_") in f or k in (feeling or "").lower():
            t = v
            break
    if not t:
        raw = expression_tag_for_feeling(feeling or "", emotion="")
        # Ignore smile fallback from generator until we try better options
        t = "" if raw == "smile" and f and "smil" not in f and "proud" not in f and "happy" not in f and "joy" not in f else raw
        if not t:
            t = _FEELING_ALIASES.get(f, "")
    t = soft_normalize_tag(t) if t else ""
    if t in emo and t != "looking_at_viewer":
        return t
    if any(x in f for x in ("stress", "overwhelm", "anxious", "worry", "fear")) and "worried" in emo:
        return "worried"
    if any(x in f for x in ("focus", "serious", "determin", "calm", "concentrat")) and "serious" in emo:
        return "serious"
    if any(x in f for x in ("sad", "lonely", "melanchol")) and "sad" in emo:
        return "sad"
    if any(x in f for x in ("nervous", "uncertain", "shy")) and "nervous" in emo:
        return "nervous"
    if "serious" in emo:
        return "serious"
    if "expressionless" in emo:
        return "expressionless"
    if "smile" in emo:
        return "smile"
    return next(iter(sorted(emo)), "serious")


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
    identity_hint: str = "",
    present_exclusive: frozenset[str] | None = None,
    forced_keywords: dict[str, list[str]] | None = None,
    use_allowlist: bool = False,
) -> str:
    allow = chronicle_allowlists()
    kw = forced_keywords or {}
    kw_block = "\n".join(
        f"  {ax}: {', '.join(kw.get(ax) or []) or '(none)'}" for ax in AXES
    )

    def _fmt(name: str, items: list[str], n: int = 90) -> str:
        body = ", ".join(items[:n])
        return f"{name} ({len(items)}): {body}" + (" ..." if len(items) > n else "")

    acts_json = json.dumps(acts, ensure_ascii=False, indent=2)
    allow_rules = (
        "Prefer tags from these allowlists when possible (not mandatory copies):\n"
        f"{_fmt('POSE', allow['pose'])}\n"
        f"{_fmt('OUTFIT', allow['outfit'], 110)}\n"
        f"{_fmt('SHOT', allow['shot'], 30)}\n"
        f"{_fmt('EFFECT', allow['effect'], 130)}\n\n"
        if use_allowlist
        else (
            "No allowlist — invent fitting Danbooru-style tags from the acts. "
            "Vary pose/shot/effect across the three axes; do not reuse one stock pose.\n\n"
        )
    )
    excl = present_exclusive or PRESENT_EXCLUSIVE_DEFAULT
    excl_hint = ""
    if excl:
        excl_hint = (
            f"- Avoid leaking base-only props into other axes when irrelevant: "
            f"{', '.join(sorted(excl))}.\n"
        )
    return (
        "Compose Danbooru tags for a 3-panel anime chronicle. Return JSON.\n\n"
        f"{allow_rules}"
        f"IDENTITY (appearance only; expression comes from feeling later): "
        f"{identity_hint or '(injected later)'}\n"
        f"TIME SCALE: {time_scale}\n"
        f"TITLE: {title}\n"
        f"THROUGHLINE: {throughline}\n\n"
        f"FORCED KEYWORDS (must appear somewhere in that axis — prefer Effect):\n"
        f"{kw_block}\n\n"
        f"ACTS (derive pose/outfit/shot/effect from these; do not ignore them):\n"
        f"{acts_json}\n\n"
        "OUTPUT SHAPE (example keys only — do NOT copy these tag values):\n"
        '{"past":{"pose":["..."],"outfit":["..."],"shot":["..."],"effect":["..."]},'
        '"present":{"pose":["..."],"outfit":["..."],"shot":["..."],"effect":["..."]},'
        '"future":{"pose":["..."],"outfit":["..."],"shot":["..."],"effect":["..."]}}\n\n'
        "HARD RULES:\n"
        "- Keys: past, present, future.\n"
        "- pose: 1–3 tags matching that act's activity (diverse across axes).\n"
        "- outfit: 2–5 garment tags matching that act's outfit field.\n"
        "- shot: exactly 1 composition tag; prefer different shots per axis.\n"
        "- effect: 3–8 place/prop/lighting tags matching place + keywords.\n"
        "- Arrays of strings only. No color prefixes on clothes (blouse not white_blouse).\n"
        "- present should reflect the FIXED present act when one is given.\n"
        f"{excl_hint}"
        "- Include every forced keyword for that axis (usually in effect).\n"
        "- Do NOT put expression tags in pose/outfit/effect; feeling drives expression later.\n"
        "- Do NOT default every axis to sitting / standing / hand_on_own_chin — "
        "pick poses from the activity text.\n"
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


def _pass_tags(
    tags: list[str],
    allowed: set[str],
    *,
    ban: set[str],
    limit: int,
    use_allowlist: bool,
) -> list[str]:
    if use_allowlist:
        return filter_to_allowlist(tags, allowed, ban=ban, limit=limit)
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        key = soft_normalize_tag(t)
        if not key or key in ban or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= limit:
            break
    return out


def _inject_forced(tags: list[str], forced: list[str], *, limit: int = 12) -> list[str]:
    out = list(tags)
    for t in forced:
        key = soft_normalize_tag(t)
        if key and key not in {soft_normalize_tag(x) for x in out}:
            out.append(key)
    return out[:limit]


def filter_compose_result(
    composed: dict[str, dict],
    acts: dict[str, dict],
    *,
    identity: list[str],
    base_axis: str = "present",
    present_exclusive: frozenset[str] | None = None,
    use_allowlist: bool = False,
    forced_keywords: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """Light post-process of LLM compose; prefer model tags over hardcodes.

    Code still: identity + feeling→expression, user forced keywords, optional
    allowlist. No pouring/cafe/standing/shot-list overrides.
    """
    allow = chronicle_allowlists()
    pose_a, emo_a, shot_a = set(allow["pose"]), set(allow["emo"]), set(allow["shot"])
    outfit_a, effect_a = set(allow["outfit"]), set(allow["effect"])
    # Soft exclusive: only strip if present act actually uses those props
    base_act = acts.get(base_axis) or {}
    base_blob = f"{base_act.get('activity', '')} {base_act.get('place', '')}".lower()
    excl_src = present_exclusive if present_exclusive is not None else PRESENT_EXCLUSIVE_DEFAULT
    use_excl = any(x in base_blob for x in ("pour", "coffee", "cafe", "steam"))
    excl = set(excl_src) if use_excl else set()
    result: dict[str, dict] = {}
    ident = strip_expression_tags(list(identity), emo=emo_a)
    ident = ensure_subject_anchors(ident)
    forced_keywords = forced_keywords or {}

    for ax in AXES:
        block = composed.get(ax) or {}
        act = acts.get(ax) or {}
        ban = set() if ax == base_axis else excl
        forced = [soft_normalize_tag(t) for t in (forced_keywords.get(ax) or []) if t]

        pose = _pass_tags(
            parse_tag_field(block.get("pose")), pose_a, ban=ban, limit=3,
            use_allowlist=use_allowlist,
        )
        # Trust LLM; empty pose stays empty (no standing default)

        outfit = _pass_tags(
            parse_tag_field(block.get("outfit")),
            outfit_a, ban=ban, limit=5, use_allowlist=use_allowlist,
        )
        # Prefer LLM outfit; only if empty, fall back to act outfit text
        if not outfit:
            outfit = _pass_tags(
                parse_tag_field(act.get("outfit")),
                outfit_a, ban=ban, limit=5, use_allowlist=use_allowlist,
            )

        shot_raw = block.get("shot")
        if isinstance(shot_raw, str):
            shot_raw = [shot_raw]
        shot = _pass_tags(
            parse_tag_field(shot_raw), shot_a, ban=set(), limit=1,
            use_allowlist=use_allowlist,
        )
        # Do not rewrite duplicate shots to a fixed rotation list

        effect = _pass_tags(
            parse_tag_field(block.get("effect")),
            effect_a, ban=ban, limit=8, use_allowlist=use_allowlist,
        )
        if not effect:
            effect = _pass_tags(
                parse_tag_field(act.get("place")),
                effect_a, ban=ban, limit=4, use_allowlist=use_allowlist,
            )

        # User forced keywords only (not cafe/pouring machinery)
        effect = _inject_forced(effect, forced, limit=12)

        expr = map_expression(str(act.get("feeling") or ""), emo_allow=emo_a)
        char = list(dict.fromkeys([*ident, expr]))
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
        missing = [
            t for t in forced
            if t and t not in positive.replace("-", "_") and t not in positive
        ]
        if missing:
            effect = _inject_forced(effect, missing, limit=14)
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

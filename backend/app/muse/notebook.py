"""Shot notebook — plain-language source of truth (not facets).

Used by 主演撮り and 制作スタッフ. Conversation revises this notebook; craft
TAGS/SCENE are woven from it (and replaced whole) just before a take. Muse talk
may read it; Script writes it. Crew also mirrors PLAN/COSTUME into the notebook.
"""
from __future__ import annotations

import re
import time
from typing import Any

SHOT_KEYS = (
    "atmosphere",
    "scene",
    "frame",
    "wearing",
    "beat",
    "wearing_b",
    "beat_b",
)

META_KEYS = ("vibe", "open", "standing", "open_choices")

_ALL_KEYS = SHOT_KEYS + META_KEYS
REWRITE_LOG_MAX = 12
_REWRITE_FIELDS = SHOT_KEYS + ("vibe", "open")


def blank(partner: bool = False) -> dict[str, Any]:
    nb = {
        "atmosphere": "",
        "scene": "",
        "frame": "",
        "wearing": "",
        "beat": "",
        "wearing_b": "",
        "beat_b": "",
        "vibe": "",
        "open": "",
        "open_choices": [],
        "standing": [],
        "rev": 0,
        "updated_at": 0.0,
    }
    if not partner:
        nb["wearing_b"] = ""
        nb["beat_b"] = ""
    return nb


def of(session: dict[str, Any]) -> dict[str, Any]:
    nb = session.get("notebook")
    if not isinstance(nb, dict) or not nb:
        nb = blank(partner=bool(str(
            (session.get("inputs") or {}).get("partner_preset") or ""
        ).strip()))
        session["notebook"] = nb
    for key in _ALL_KEYS:
        if key in ("standing", "open_choices"):
            nb.setdefault(key, [])
        else:
            nb.setdefault(key, "")
    nb.setdefault("rev", 0)
    nb.setdefault("updated_at", 0.0)
    return nb


def has_shot(nb: dict[str, Any]) -> bool:
    return any(str(nb.get(k) or "").strip() for k in (
        "scene", "frame", "wearing", "beat", "atmosphere",
    ))


def render(nb: dict[str, Any], *, name_a: str = "", name_b: str = "") -> str:
    """Human / model facing dump."""
    a = name_a or "Muse A"
    lines = [
        f"ATMOSPHERE:\n{str(nb.get('atmosphere') or '').strip() or '(empty)'}",
        f"SCENE:\n{str(nb.get('scene') or '').strip() or '(empty)'}",
        f"FRAME:\n{str(nb.get('frame') or '').strip() or '(empty)'}",
        f"{a} WEARING:\n{str(nb.get('wearing') or '').strip() or '(empty)'}",
        f"{a} BEAT:\n{str(nb.get('beat') or '').strip() or '(empty)'}",
    ]
    if name_b or str(nb.get("wearing_b") or "").strip() or str(nb.get("beat_b") or "").strip():
        b = name_b or "Muse B"
        lines += [
            f"{b} WEARING:\n{str(nb.get('wearing_b') or '').strip() or '(empty)'}",
            f"{b} BEAT:\n{str(nb.get('beat_b') or '').strip() or '(empty)'}",
        ]
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        lines.append(f"VIBE:\n{vibe}")
    open_ = str(nb.get("open") or "").strip()
    if open_:
        lines.append(f"OPEN:\n{open_}")
    standing = [str(s).strip() for s in (nb.get("standing") or []) if str(s).strip()]
    if standing:
        lines.append("STANDING:\n" + "\n".join(f"- {s}" for s in standing[:5]))
    return "\n\n".join(lines)


def summary_for_muse(nb: dict[str, Any], *, name_a: str = "", name_b: str = "") -> str:
    """Shorter block for talk context (English labels; values may be EN)."""
    parts: list[str] = []
    for label, key in (
        ("Atmosphere", "atmosphere"),
        ("Place", "scene"),
        ("Camera", "frame"),
    ):
        val = str(nb.get(key) or "").strip()
        if val:
            parts.append(f"{label}: {val}")
    w = str(nb.get("wearing") or "").strip()
    b = str(nb.get("beat") or "").strip()
    who = name_a or "Lead"
    if w or b:
        parts.append(f"{who} wearing: {w or '(unset)'}")
        parts.append(f"{who} beat: {b or '(unset)'}")
    wb = str(nb.get("wearing_b") or "").strip()
    bb = str(nb.get("beat_b") or "").strip()
    if name_b and (wb or bb):
        parts.append(f"{name_b} wearing: {wb or '(unset)'}")
        parts.append(f"{name_b} beat: {bb or '(unset)'}")
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        parts.append(f"Vibe: {vibe}")
    open_ = str(nb.get("open") or "").strip()
    if open_:
        parts.append(f"Open proposal (not locked): {open_}")
    return "\n".join(parts)


# Longevity caps (plan: VIBE≤5 lines, OPEN≤2, STANDING≤5).
VIBE_MAX_LINES = 5
VIBE_MAX_CHARS = 400
OPEN_MAX_LINES = 2
OPEN_MAX_CHARS = 240

# SHOT field contracts — short absolute phrases. Long densify prose belongs
# only in craft_scene. Polluted SCENE fields were how place changes froze:
# the model would rewrite tags/craft but leave a 60-word park paragraph in
# SCENE, and the next turn's Muse digest pulled the shoot back.
SCENE_MAX_CHARS = 120
ATMOSPHERE_MAX_CHARS = 100
FRAME_MAX_CHARS = 160
WEARING_MAX_CHARS = 240
BEAT_MAX_CHARS = 240

_SHOT_FIELD_CAPS: dict[str, int] = {
    "scene": SCENE_MAX_CHARS,
    "atmosphere": ATMOSPHERE_MAX_CHARS,
    "frame": FRAME_MAX_CHARS,
    "wearing": WEARING_MAX_CHARS,
    "wearing_b": WEARING_MAX_CHARS,
    "beat": BEAT_MAX_CHARS,
    "beat_b": BEAT_MAX_CHARS,
}

# Gaze used to be scrubbed out of BEAT here with a keyword regex. It is a rule
# in SCRIPTER_SYSTEM now: the scripter reads the conversation and writes the
# frame as one camera story. A word list cannot tell "見上げる" the pose from
# "見上げる" the lens, and every phrase it missed shipped anyway.

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


def _cap_lines(text: str, *, max_lines: int, max_chars: int) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    body = "\n".join(lines[:max_lines]).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip()
    return body


def _cap_phrase(text: str, *, max_chars: int) -> str:
    """Keep a short absolute phrase; cut on a word boundary when possible."""
    body = re.sub(r"\s+", " ", str(text or "").strip())
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.;:")


_TIME_TOKEN_RE = re.compile(
    r"\b(dawn|dusk|sunrise|sunset|twilight|noon|midnight|"
    r"morning|evening|afternoon|night)\b",
    re.I,
)
_NO_ITEM_RE = re.compile(r"\b(?:no|without)\s+[a-z][a-z0-9_]*\b", re.I)


def coerce_plain_phrase(val: Any) -> str:
    """Notebook fields are short English phrases. Lists join; dicts/reprs drop."""
    if val is None or isinstance(val, bool):
        return ""
    if isinstance(val, dict):
        return ""
    if isinstance(val, (list, tuple)):
        parts = [coerce_plain_phrase(x) for x in val]
        return ", ".join(p for p in parts if p)
    text = str(val).strip()
    if not text:
        return ""
    low = text.lower()
    if low in ("unchanged", "変更なし", "同じ", "そのまま", "-", "none", "なし"):
        return ""
    if text.startswith("{") and text.endswith("}"):
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return ""
        if inner.startswith(("'", '"')) and inner.endswith(("'", '"')):
            return inner[1:-1].strip()
        bits = [coerce_plain_phrase(p.strip().strip("'\"")) for p in inner.split(",")]
        return ", ".join(p for p in bits if p)
    return text


def wearing_tokens(text: str) -> set[str]:
    """English-ish tokens from a wearing/beat phrase (for craft consistency).

    Built from the field itself — not from a situation vocabulary list.
    ``no hat`` / ``without hat`` do not keep the noun (danbooru ``no_hat`` is
    not a removal). Comma-separated items are tokenised separately so
    ``straw hat, cardigan`` does not mint ``hat_cardigan``.
    """
    out: set[str] = set()
    for chunk in re.split(r"[,，、]", str(text or "")):
        raw = _NO_ITEM_RE.sub(" ", chunk.lower())
        if not raw.strip():
            continue
        out |= set(_TOKEN_RE.findall(raw))
        words = re.findall(r"[a-z][a-z0-9]+", raw)
        for i in range(len(words) - 1):
            pair = (words[i], words[i + 1])
            # `straw hat` is a garment; `the metal`, `while staring`, `on the`
            # are grammar. Pairing across a function word never names a thing,
            # and these are read back as items that must never return.
            if pair[0] in _STRUCK_NOISE or pair[1] in _STRUCK_NOISE:
                continue
            out.add(f"{pair[0]}_{pair[1]}")
    return out


def split_atmosphere_time(atmosphere: str, scene: str) -> tuple[str, str]:
    """Move clock words out of mood into scene. Mood stays feeling-only."""
    atm = str(atmosphere or "").strip()
    sc = str(scene or "").strip()
    found = [m.group(0).lower() for m in _TIME_TOKEN_RE.finditer(atm)]
    if not found:
        return atm, sc
    mood = _TIME_TOKEN_RE.sub(" ", atm)
    mood = re.sub(r"[\s,;]+", " ", mood).strip(" ,;.")
    hour = found[-1]
    if hour and hour not in sc.lower():
        sc = f"{sc} at {hour}".strip() if sc else hour
    return mood, sc


def parse_pitch_choices(pitch: str) -> list[str]:
    raw = str(pitch or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"\s*\|\s*", raw) if p.strip()]
    return parts[:2]


def set_open_choices(nb: dict[str, Any], choices: list[str]) -> dict[str, Any]:
    """Muse PITCH → chips. Do not wait for the next Script turn."""
    cleaned = [str(c).strip() for c in (choices or []) if str(c).strip()][:2]
    prev = list(nb.get("open_choices") or [])
    prev_open = str(nb.get("open") or "").strip()
    nb["open_choices"] = cleaned
    nb["open"] = " | ".join(cleaned)
    if cleaned != prev or str(nb.get("open") or "") != prev_open:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return nb


def shot_tokens(nb: dict[str, Any]) -> set[str]:
    """Everything the shot currently says, as tokens."""
    out: set[str] = set()
    for key in SHOT_KEYS:
        out |= wearing_tokens(str(nb.get(key) or ""))
    return out


def struck_tokens(session: dict[str, Any]) -> set[str]:
    """What must not come back — minus whatever the shot now says.

    `struck` is append-only, and it is read as "never restore this". That is
    right for a garment the showrunner took off and wrong for everything that
    legitimately comes and goes: stand up and `sitting` is struck, so the next
    「座って」 is fighting a filter, and `filter_weave_tags` strips the very
    tag the notebook just asked for. The notebook is the shot — anything it
    currently names is by definition not struck.
    """
    live = shot_tokens(of(session)) if isinstance(session, dict) else set()
    out: set[str] = set()
    for item in session.get("struck") or []:
        s = str(item or "").strip()
        if not s:
            continue
        out.add(s.lower().replace(" ", "_"))
        out |= wearing_tokens(s)
    return {t for t in out if len(t) >= 3 and t not in live}


def live_struck(session: dict[str, Any]) -> list[str]:
    """The struck list as shown to a model: same pruning, original wording."""
    live = shot_tokens(of(session)) if isinstance(session, dict) else set()
    out: list[str] = []
    for item in session.get("struck") or []:
        s = str(item or "").strip()
        if not s:
            continue
        key = s.lower().replace(" ", "_")
        if key in live or (wearing_tokens(s) & live):
            continue
        out.append(s)
    return out


_STRUCK_NOISE = {
    "and", "with", "the", "her", "his", "she", "for", "from", "over", "under",
    "on", "at", "in", "of", "to", "a", "an", "while", "nothing", "into",
    "onto", "that", "this", "its", "out", "off", "up", "down", "by",
}


def record_struck_from_wearing(
    session: dict[str, Any], *, prev_wearing: str, new_wearing: str,
) -> list[str]:
    """Tokens dropped from wearing stay struck so still-read / weave cannot restore them."""
    return record_struck_tokens(
        session, prev=prev_wearing, new=new_wearing, min_len=3,
    )


def record_struck_tokens(
    session: dict[str, Any], *, prev: str, new: str, min_len: int = 3,
) -> list[str]:
    """Tokens that left a shot phrase stay struck (clothes, place, hour, pose, crop)."""
    dropped = wearing_tokens(prev) - wearing_tokens(new)
    added = sorted(
        t for t in dropped if t not in _STRUCK_NOISE and len(t) >= min_len
    )
    if not added:
        return []
    prior = [str(s) for s in (session.get("struck") or []) if str(s).strip()]
    have = {s.lower().replace(" ", "_") for s in prior}
    for t in added:
        if t not in have:
            prior.append(t)
            have.add(t)
    session["struck"] = prior
    return added


def tag_mentions_struck(tag: str, struck: set[str]) -> bool:
    from .identity import bare_tag

    bare = bare_tag(tag)
    if not bare:
        return False
    if bare in struck:
        return True
    for s in struck:
        if len(s) < 3:
            continue
        if bare == f"no_{s}" or bare.endswith(f"_{s}") or s in bare.split("_"):
            return True
    return False


_QUALITY_TAG_KEEP = {
    "knit", "drape", "folds", "fabric", "grain", "bokeh", "depth",
    "cinematic", "soft", "light", "shadow", "texture", "skin", "air",
}


def filter_weave_tags(
    tags: str, *, wearing: str, scene: str, beat: str, struck: set[str],
    wearing_b: str = "", beat_b: str = "", frame: str = "",
) -> str:
    """Drop struck tokens and shot nouns that left the notebook. Quality tags stay."""
    kept: list[str] = []
    seen: set[str] = set()
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        from .identity import bare_tag
        key = bare_tag(tok)
        if not key or key in seen:
            continue
        if struck and tag_mentions_struck(tok, struck):
            continue
        seen.add(key)
        kept.append(tok)
    return ", ".join(kept)


def stale_wearing_tags(
    *, prev_wearing: str, new_wearing: str, tags: str,
) -> list[str]:
    """Tag tokens dropped from wearing that still appear in the craft bag."""
    dropped = wearing_tokens(prev_wearing) - wearing_tokens(new_wearing)
    if not dropped:
        return []
    have = wearing_tokens(tags.replace(",", " "))
    noise = {"and", "with", "the", "her", "his", "she", "for", "from"}
    return sorted(t for t in dropped if t in have and t not in noise and len(t) >= 4)


_WIDE_CROP_TAGS = {
    "wide_shot", "wide_view", "long_shot", "full_body", "establishing_shot",
}
_CLOSE_CROP_TAGS = {
    "close_up", "closeup", "face_focus", "extreme_close-up", "extreme_closeup",
}


def drop_crops_not_in_frame(tags: str, *, frame: str) -> str:
    """Keep one crop family. Zoom must not keep wide_shot; wide must not keep close_up."""
    from .identity import bare_tag, framing_from_phrase

    crop = framing_from_phrase(frame)
    if crop == "auto":
        have = {bare_tag(p) for p in str(tags or "").split(",") if p.strip()}
        if have & _WIDE_CROP_TAGS and have & _CLOSE_CROP_TAGS:
            drop = _WIDE_CROP_TAGS | _CLOSE_CROP_TAGS
        else:
            return tags
    elif crop in ("face_closeup", "upper_body", "from_behind"):
        drop = set(_WIDE_CROP_TAGS)
        if crop == "face_closeup":
            drop.add("full_body")
    elif crop == "full_body":
        drop = set(_CLOSE_CROP_TAGS)
    else:
        return tags
    kept: list[str] = []
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        if bare_tag(tok) in drop:
            continue
        kept.append(tok)
    return ", ".join(kept)


def drop_garments_not_in_wearing(tags: str, *, wearing: str, wearing_b: str = "") -> str:
    """Drop leftover garment tags whose last token left wearing."""
    allowed = wearing_tokens(wearing) | wearing_tokens(wearing_b)
    if not allowed:
        return tags
    leftover = {
        "hat", "cardigan", "coat", "jacket", "hoodie", "cape",
        "umbrella", "scarf", "glasses", "sunglasses",
    }
    kept: list[str] = []
    from .identity import bare_tag
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = bare_tag(tok)
        last = key.split("_")[-1] if key else ""
        if last in _QUALITY_TAG_KEEP:
            kept.append(tok)
            continue
        if last in leftover and last not in allowed and key not in allowed:
            continue
        kept.append(tok)
    return ", ".join(kept)


def scrub_craft_tags(
    tags: str, *, wearing: str, scene: str, beat: str, struck: set[str],
    wearing_b: str = "", beat_b: str = "", frame: str = "",
) -> str:
    """Struck, leftover garments, and the opposite crop family — keep quality tags."""
    tags = filter_weave_tags(
        tags, wearing=wearing, scene=scene, beat=beat, struck=struck,
        wearing_b=wearing_b, beat_b=beat_b, frame=frame,
    )
    tags = drop_garments_not_in_wearing(tags, wearing=wearing, wearing_b=wearing_b)
    return drop_crops_not_in_frame(tags, frame=frame)


def strip_shot_keys(patch: dict[str, Any]) -> dict[str, Any]:
    """Densify must thicken tags/craft_scene only — never rewrite SHOT fields."""
    out = dict(patch or {})
    for key in SHOT_KEYS:
        out.pop(key, None)
    out.pop("standing", None)
    out.pop("clear_open", None)
    return out


def shot_snapshot(nb: dict[str, Any]) -> dict[str, Any]:
    """Plain values used to diff who rewrote the notebook."""
    out: dict[str, Any] = {
        k: str((nb or {}).get(k) or "") for k in _REWRITE_FIELDS
    }
    out["standing"] = [
        str(s).strip() for s in ((nb or {}).get("standing") or []) if str(s).strip()
    ]
    return out


def shot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    keys = set(before or {}) | set(after or {})
    for key in keys:
        old, new = (before or {}).get(key), (after or {}).get(key)
        if old != new:
            changed[key] = {"before": old, "after": new}
    return changed


def record_rewrite(
    session: dict[str, Any], source: str, *,
    before: dict[str, Any], after: dict[str, Any],
    intent: str = "", extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Append a short rewrite to the session ring. Returns the entry or None."""
    changed = shot_diff(before, after)
    if extra:
        changed.update(extra)
    if not changed:
        return None
    entry = {
        "at": time.time(),
        "source": str(source or ""),
        "intent": str(intent or ""),
        "changed": changed,
    }
    log = list(session.get("rewrite_log") or [])
    log.append(entry)
    session["rewrite_log"] = log[-REWRITE_LOG_MAX:]
    return entry


def apply_patch(nb: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply absolute section replacements. Empty string in patch = clear.
    Missing key = unchanged. `standing` is a list (replace whole when provided).
    """
    changed = False
    for key in SHOT_KEYS + ("vibe", "open"):
        if key not in patch:
            continue
        raw = patch.get(key)
        if isinstance(raw, dict):
            continue
        val = coerce_plain_phrase(raw)
        if val.startswith(("{", "[")):
            continue
        if key in _SHOT_FIELD_CAPS and val:
            val = _cap_phrase(val, max_chars=_SHOT_FIELD_CAPS[key])
        if key == "vibe" and val:
            val = _cap_lines(val, max_lines=VIBE_MAX_LINES, max_chars=VIBE_MAX_CHARS)
        if key == "open" and val:
            val = _cap_lines(val, max_lines=OPEN_MAX_LINES, max_chars=OPEN_MAX_CHARS)
        if val != str(nb.get(key) or "").strip():
            nb[key] = val
            changed = True
    if "atmosphere" in patch or "scene" in patch:
        mood, place = split_atmosphere_time(
            str(nb.get("atmosphere") or ""), str(nb.get("scene") or ""),
        )
        if mood != str(nb.get("atmosphere") or "").strip():
            nb["atmosphere"] = mood
            changed = True
        if place != str(nb.get("scene") or "").strip():
            nb["scene"] = _cap_phrase(place, max_chars=SCENE_MAX_CHARS) if place else ""
            changed = True
    if "open_choices" in patch:
        raw = patch.get("open_choices")
        if isinstance(raw, str):
            items = parse_pitch_choices(raw)
        elif isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw if str(x).strip()][:2]
        else:
            items = []
        if items != list(nb.get("open_choices") or []):
            nb["open_choices"] = items
            if items:
                nb["open"] = " | ".join(items)
            changed = True
    if "standing" in patch:
        raw = patch.get("standing")
        if isinstance(raw, str):
            items = [ln.strip().lstrip("-•").strip()
                     for ln in raw.splitlines() if ln.strip()]
        elif isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw if str(x).strip()]
        else:
            items = []
        items = items[:5]
        if items != list(nb.get("standing") or []):
            nb["standing"] = items
            changed = True
    if patch.get("clear_open"):
        if nb.get("open") or nb.get("open_choices"):
            nb["open"] = ""
            nb["open_choices"] = []
            changed = True
    if changed:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return nb


_CARD_LINE_RE = re.compile(
    r"(?im)^\s*(PLACE|HOUR|WEARING_B|BEAT_B|WEARING|BEAT|FRAME)\s*[:：]\s*(.*)$"
)
_CARD_KEY = {
    "WEARING": "wearing",
    "BEAT": "beat",
    "FRAME": "frame",
    "WEARING_B": "wearing_b",
    "BEAT_B": "beat_b",
}
POSE_CARD_KEYS = ("beat", "beat_b")
FOLD_PATCH_KEYS = ("beat", "beat_b")


def parse_muse_card(card: str) -> dict[str, str]:
    """Muse CARD labelled fields → notebook keys. PLACE/HOUR are scene, skipped."""
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal key, buf
        if key is not None:
            val = " ".join(x.strip() for x in buf if str(x).strip()).strip()
            if val:
                out[key] = val
        key, buf = None, []

    for line in str(card or "").splitlines():
        m = _CARD_LINE_RE.match(line)
        if m:
            flush()
            key = _CARD_KEY.get(m.group(1).upper())
            buf = [m.group(2)]
            continue
        if key is not None:
            buf.append(line)
    flush()
    return out


def absorb_muse_card(
    nb: dict[str, Any], card: str, *, keys: tuple[str, ...] = POSE_CARD_KEYS,
) -> dict[str, str]:
    """Fold this turn's Muse CARD pose into the notebook.

    Script runs before she talks, so her acted beat would otherwise wait until
    the next compile. Clothes stay with Script; only body action is absorbed.
    """
    parsed = parse_muse_card(card)
    patch = {
        k: parsed[k] for k in keys
        if str(parsed.get(k) or "").strip()
    }
    if not patch:
        return {}
    apply_patch(nb, patch)
    return patch


# `promote_open` used to fold an affirmed OPEN into the shot here, guessing
# from a noun list (持|手に|花|缶|傘|…) whether the thing was handheld (→ BEAT)
# or worn (→ WEARING). The scripter reads the conversation now, sees the
# affirmation itself, and writes the absolute value into the right section.


def migrate(session: dict[str, Any]) -> dict[str, Any]:
    """Ensure notebook exists; seed from digest/craft when empty."""
    from . import facets as facets_mod

    facets_mod.migrate(session)
    partner = bool(str((session.get("inputs") or {}).get("partner_preset") or "").strip())
    nb = of(session)
    if has_shot(nb) or str(nb.get("vibe") or "").strip():
        return session

    digest = str(session.get("digest") or "").strip()
    craft = session.get("craft") or {}
    scene = str(craft.get("scene") or "").strip()
    if digest:
        nb["scene"] = digest[:800]
        nb["vibe"] = digest[:400]
    if scene and not nb.get("scene"):
        nb["scene"] = scene[:800]
    table = facets_mod.table_of(session)
    if table:
        if not nb.get("wearing"):
            nb["wearing"] = str((table.get("costume") or {}).get("nl") or "")[:400]
        if not nb.get("beat"):
            pose = str((table.get("pose") or {}).get("nl") or "")
            expr = str((table.get("expression") or {}).get("nl") or "")
            nb["beat"] = " ".join(x for x in (pose, expr) if x)[:400]
        if not nb.get("frame"):
            nb["frame"] = str((table.get("camera") or {}).get("nl") or "")[:400]
        if partner:
            if not nb.get("wearing_b"):
                nb["wearing_b"] = str((table.get("costume_b") or {}).get("nl") or "")[:400]
            if not nb.get("beat_b"):
                pose_b = str((table.get("pose_b") or {}).get("nl") or "")
                expr_b = str((table.get("expression_b") or {}).get("nl") or "")
                nb["beat_b"] = " ".join(x for x in (pose_b, expr_b) if x)[:400]
    standing = [str(s).strip() for s in (session.get("standing") or []) if str(s).strip()]
    if standing and not nb.get("standing"):
        nb["standing"] = standing[:5]
    if has_shot(nb) or nb.get("vibe") or nb.get("standing"):
        nb["rev"] = max(1, int(nb.get("rev") or 0))
        nb["updated_at"] = time.time()
    session["notebook"] = nb
    return session


# ── Scripter output parse / validate ────────────────────────────────────────

_INTENT_RE = re.compile(
    r"(?im)^[\s>*_-]*INTENT\s*[:：]\s*(casual|shot|mixed|recall)\s*$"
)
_FIELD_RE = re.compile(
    r"(?im)^[\s>*_-]*("
    r"ATMOSPHERE|SCENE|FRAME|WEARING|BEAT|WEARING_B|BEAT_B|"
    r"VIBE|OPEN|STANDING|TAGS|TAGS_SHARED|TAGS_A|TAGS_B|"
    r"CRAFT_SCENE|CLEAR_OPEN|UNCHANGED"
    r")\s*[:：]\s*(.*)$"
)

VALID_INTENTS = frozenset({"casual", "shot", "mixed", "recall"})

# Shallow JSON Schema for Ollama `format` (non-stream scripter).
SCRIPTER_FORMAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["casual", "shot", "mixed", "recall"]},
        "atmosphere": {"type": "string"},
        "scene": {"type": "string"},
        "frame": {"type": "string"},
        "wearing": {"type": "string"},
        "beat": {"type": "string"},
        "wearing_b": {"type": "string"},
        "beat_b": {"type": "string"},
        "vibe": {"type": "string"},
        "open": {"type": "string"},
        "standing": {"type": "string"},
        "clear_open": {"type": "boolean"},
        "unchanged": {"type": "string"},
        "tags": {"type": "string"},
        "tags_shared": {"type": "string"},
        "tags_a": {"type": "string"},
        "tags_b": {"type": "string"},
        "craft_scene": {"type": "string"},
    },
    "required": ["intent"],
}


def merge_tag_bags(
    *, tags: str = "", tags_shared: str = "", tags_a: str = "", tags_b: str = "",
) -> str:
    """Join SHARED/A/B (or flat tags) into one craft tag string, de-duped."""
    parts: list[str] = []
    seen: set[str] = set()
    for bag in (tags_shared, tags_a, tags_b, tags):
        for t in str(bag or "").split(","):
            tok = t.strip()
            if not tok:
                continue
            key = tok.lower().replace(" ", "_")
            if key in seen or key in ("none", "なし", "-"):
                continue
            seen.add(key)
            parts.append(tok)
    return ", ".join(parts)


def guard_partner_patch(
    patch: dict[str, Any], *, partner: bool = False,
) -> dict[str, Any]:
    """Drop the partner's sections on a solo shoot — nobody is standing there.

    This is the whole guard now. Deciding *which* Muse an edit was addressed to
    used to happen here too, off「だけ|のみ|ばっかり」and「二人|ふたり|一緒」;
    it dropped the other card's edits on any line that named one Muse without
    one of those words, which is most lines. The scripter is handed the
    conversation and the speakers, and decides that itself.
    """
    if not partner:
        patch.pop("wearing_b", None)
        patch.pop("beat_b", None)
    return patch


def _blank_result(raw: str = "") -> dict[str, Any]:
    return {
        "intent": "casual",
        "patch": {},
        "tags": "",
        "craft_scene": "",
        "raw": raw,
        "valid": False,
    }


def parse_scripter_json(raw: str) -> dict[str, Any] | None:
    """Parse Ollama JSON-format scripter output. None if not JSON.

    Uses ``ai.json_util.parse_json_object`` so missing commas / truncated
    tails can still salvage a usable object before falling back to labelled.
    """
    from ..ai.json_util import parse_json_object

    text = (raw or "").strip()
    if not text:
        return None
    # Fast reject for clearly labelled (non-JSON) blocks.
    head = text.lstrip()[:200]
    if (
        not head.startswith(("{", "[", "`"))
        and "INTENT" in head.upper()
        and "{" not in head
    ):
        return None
    try:
        data = parse_json_object(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "casual").strip().lower()
    if intent not in VALID_INTENTS:
        intent = "casual"
    unchanged = {
        x.strip().lower()
        for x in re.split(r"[,，、\s]+", str(data.get("unchanged") or ""))
        if x.strip() and x.strip().lower() not in ("none", "なし", "-", "")
    }
    patch: dict[str, Any] = {}
    for key in (
        "atmosphere", "scene", "frame", "wearing", "beat",
        "wearing_b", "beat_b", "vibe", "open",
    ):
        if key in unchanged:
            continue
        if key not in data:
            continue
        val = coerce_plain_phrase(data.get(key))
        if not val:
            continue
        patch[key] = val
    if "standing" in data and "standing" not in unchanged:
        val = str(data.get("standing") or "").strip()
        if val.lower() not in ("none", "なし", "unchanged", "-", "無し", ""):
            patch["standing"] = val
    if data.get("clear_open") in (True, "yes", "true", "1", "クリア", "clear", "y"):
        patch["clear_open"] = True
    tags = str(data.get("tags") or "").strip()
    tags_shared = str(data.get("tags_shared") or "").strip()
    tags_a = str(data.get("tags_a") or "").strip()
    tags_b = str(data.get("tags_b") or "").strip()
    for bag_name, bag in (
        ("tags", tags), ("tags_shared", tags_shared),
        ("tags_a", tags_a), ("tags_b", tags_b),
    ):
        if bag.lower() in ("none", "なし", "-"):
            if bag_name == "tags":
                tags = ""
            elif bag_name == "tags_shared":
                tags_shared = ""
            elif bag_name == "tags_a":
                tags_a = ""
            else:
                tags_b = ""
    merged = merge_tag_bags(
        tags=tags, tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    craft_scene = str(data.get("craft_scene") or "").strip()
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""
    return {
        "intent": intent,
        "patch": patch,
        "tags": merged,
        "tags_shared": tags_shared,
        "tags_a": tags_a,
        "tags_b": tags_b,
        "craft_scene": craft_scene,
        "raw": raw,
        "valid": True,
    }


def parse_scripter_labelled(raw: str) -> dict[str, Any]:
    """Parse labelled scripter output into intent + patch + optional craft."""
    text = raw or ""
    intent = "casual"
    m = _INTENT_RE.search(text)
    if m:
        intent = m.group(1).lower()

    fields: dict[str, str] = {}
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        fm = _FIELD_RE.match(line)
        if fm:
            current = fm.group(1).upper()
            bodies.setdefault(current, [])
            inline = (fm.group(2) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies.setdefault(current, []).append(line)
    for key, parts in bodies.items():
        fields[key] = "\n".join(parts).strip()

    unchanged = {
        x.strip().lower()
        for x in re.split(r"[,，、\s]+", fields.get("UNCHANGED", ""))
        if x.strip() and x.strip().lower() not in ("none", "なし", "-")
    }

    key_map = {
        "atmosphere": "ATMOSPHERE",
        "scene": "SCENE",
        "frame": "FRAME",
        "wearing": "WEARING",
        "beat": "BEAT",
        "wearing_b": "WEARING_B",
        "beat_b": "BEAT_B",
        "vibe": "VIBE",
        "open": "OPEN",
    }
    patch: dict[str, Any] = {}
    for dest, src in key_map.items():
        if dest in unchanged or src.lower() in unchanged:
            continue
        if src in fields:
            val = fields[src]
            if val.lower() in ("unchanged", "変更なし", "同じ", "そのまま", "-"):
                continue
            patch[dest] = val

    if "STANDING" in fields and "standing" not in unchanged:
        val = fields["STANDING"]
        if val.lower() not in ("none", "なし", "unchanged", "-", "無し"):
            patch["standing"] = val

    clear = str(fields.get("CLEAR_OPEN") or "").strip().lower()
    if clear in ("yes", "true", "1", "クリア", "clear", "y"):
        patch["clear_open"] = True

    def _clean_bag(key: str) -> str:
        val = str(fields.get(key) or "").strip()
        return "" if val.lower() in ("none", "なし", "-", "") else val

    tags = _clean_bag("TAGS")
    tags_shared = _clean_bag("TAGS_SHARED")
    tags_a = _clean_bag("TAGS_A")
    tags_b = _clean_bag("TAGS_B")
    merged = merge_tag_bags(
        tags=tags, tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    craft_scene = str(fields.get("CRAFT_SCENE") or "").strip()
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""

    # Labelled output counts as valid when INTENT was present or we got a patch.
    valid = bool(m or patch or merged or craft_scene)
    return {
        "intent": intent if intent in VALID_INTENTS else "casual",
        "patch": patch,
        "tags": merged,
        "tags_shared": tags_shared,
        "tags_a": tags_a,
        "tags_b": tags_b,
        "craft_scene": craft_scene,
        "raw": text,
        "valid": valid,
    }


def parse_scripter(raw: str) -> dict[str, Any]:
    """Parse scripter output — JSON schema first, labelled fallback."""
    text = raw or ""
    if not str(text).strip():
        return _blank_result(text)
    parsed = parse_scripter_json(text)
    if parsed is not None:
        return parsed
    return parse_scripter_labelled(text)


def validate_scripter(
    result: dict[str, Any], *, partner: bool = False, mode: str = "",
) -> dict[str, Any]:
    """Validate-first gate. On failure: no craft fields, mark invalid.

    ``compile`` writes notebook only — tags are ignored even on shot/mixed.
    ``weave`` writes tags/craft_scene and must not rewrite SHOT.
    """
    out = dict(result or {})
    intent = str(out.get("intent") or "casual").strip().lower()
    if intent not in VALID_INTENTS:
        out["intent"] = "casual"
        out["valid"] = False
        out["tags"] = ""
        out["craft_scene"] = ""
        return out
    out["intent"] = intent
    tags_shared = str(out.get("tags_shared") or "").strip()
    tags_a = str(out.get("tags_a") or "").strip()
    tags_b = str(out.get("tags_b") or "").strip()
    tags = str(out.get("tags") or "").strip() or merge_tag_bags(
        tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    scene = str(out.get("craft_scene") or "").strip()
    if mode == "compile":
        out["patch"] = dict(out.get("patch") or {})
        out["tags"] = ""
        out["tags_shared"] = ""
        out["tags_a"] = ""
        out["tags_b"] = ""
        out["craft_scene"] = ""
        out.setdefault("valid", True)
        return out
    if mode == "weave":
        out["patch"] = strip_shot_keys(dict(out.get("patch") or {}))
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        if partner and not (tags_a and tags_b):
            out["refuse_reason"] = "w_muse_tags_unsplit"
        elif partner:
            tags = merge_tag_bags(
                tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
            )
        out["tags"] = tags
        out["tags_shared"] = tags_shared
        out["tags_a"] = tags_a
        out["tags_b"] = tags_b
        out["craft_scene"] = scene
        out.setdefault("valid", True)
        return out
    if intent in ("shot", "mixed"):
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        if partner and not (tags_a and tags_b):
            out["refuse_reason"] = "w_muse_tags_unsplit"
        elif partner:
            tags = merge_tag_bags(
                tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
            )
    out["patch"] = dict(out.get("patch") or {})
    out["tags"] = tags
    out["tags_shared"] = tags_shared
    out["tags_a"] = tags_a
    out["tags_b"] = tags_b
    out["craft_scene"] = scene
    out.setdefault("valid", True)
    return out

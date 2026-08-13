"""Duet shot notebook — plain-language source of truth (not facets).

Conversation revises this notebook; craft TAGS/SCENE are compiled from it
(and replaced whole). Muse talk may read it; Scripter writes it.
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

META_KEYS = ("vibe", "open", "standing")

_ALL_KEYS = SHOT_KEYS + META_KEYS


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
        if key == "standing":
            nb.setdefault("standing", [])
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


def wearing_tokens(text: str) -> set[str]:
    """English-ish tokens from a wearing/beat phrase (for craft consistency).

    Built from the field itself — not from a situation vocabulary list.
    """
    raw = str(text or "").lower()
    if not raw.strip():
        return set()
    out: set[str] = set(_TOKEN_RE.findall(raw))
    # Also accept space-joined pairs as underscore tags (straw hat → straw_hat).
    words = re.findall(r"[a-z][a-z0-9]+", raw)
    for i in range(len(words) - 1):
        out.add(f"{words[i]}_{words[i + 1]}")
    return out


def stale_wearing_tags(
    *, prev_wearing: str, new_wearing: str, tags: str,
) -> list[str]:
    """Tag tokens dropped from wearing that still appear in the craft bag."""
    dropped = wearing_tokens(prev_wearing) - wearing_tokens(new_wearing)
    if not dropped:
        return []
    have = wearing_tokens(tags.replace(",", " "))
    # Ignore ultra-generic leftovers that are not garment-like on their own.
    noise = {"and", "with", "the", "her", "his", "she", "for", "from"}
    return sorted(t for t in dropped if t in have and t not in noise and len(t) >= 4)


def strip_shot_keys(patch: dict[str, Any]) -> dict[str, Any]:
    """Densify must thicken tags/craft_scene only — never rewrite SHOT fields."""
    out = dict(patch or {})
    for key in SHOT_KEYS:
        out.pop(key, None)
    out.pop("standing", None)
    out.pop("clear_open", None)
    return out


def apply_patch(nb: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply absolute section replacements. Empty string in patch = clear.
    Missing key = unchanged. `standing` is a list (replace whole when provided).
    """
    changed = False
    for key in SHOT_KEYS + ("vibe", "open"):
        if key not in patch:
            continue
        val = str(patch.get(key) or "").strip()
        if key in _SHOT_FIELD_CAPS and val:
            val = _cap_phrase(val, max_chars=_SHOT_FIELD_CAPS[key])
        if key == "vibe" and val:
            val = _cap_lines(val, max_lines=VIBE_MAX_LINES, max_chars=VIBE_MAX_CHARS)
        if key == "open" and val:
            val = _cap_lines(val, max_lines=OPEN_MAX_LINES, max_chars=OPEN_MAX_CHARS)
        if val != str(nb.get(key) or "").strip():
            nb[key] = val
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
        if nb.get("open"):
            nb["open"] = ""
            changed = True
    if changed:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return nb


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
        val = str(data.get(key) or "").strip()
        if val.lower() in ("unchanged", "変更なし", "同じ", "そのまま", "-", "none", "なし"):
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
    result: dict[str, Any], *, partner: bool = False,
) -> dict[str, Any]:
    """Validate-first gate. On failure: no craft fields, mark invalid.

    Notebook patch may still be usable when intent/patch look coherent; craft
    tags/scene are cleared so callers refuse to overwrite live craft.
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
    if intent in ("shot", "mixed"):
        # Must compile something concrete; otherwise keep prior craft. This is
        # the only hard refusal left — genuinely nothing to write down.
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        # W-Muse wants separated bags so attributes bind to the right Muse. An
        # unsplit bag used to be thrown away whole, which took the wardrobe and
        # location changes riding along with it and froze the picture for the
        # rest of the session. Flag it for the one repair pass instead; if the
        # repair still comes back flat, ship it flat. A muddled attribution is
        # visible on the board and fixable in the next line — last week's
        # outfit, silently kept, is neither.
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

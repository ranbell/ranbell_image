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
    """Shorter block for talk context."""
    parts: list[str] = []
    for label, key in (
        ("空気", "atmosphere"),
        ("場所", "scene"),
        ("カメラ", "frame"),
    ):
        val = str(nb.get(key) or "").strip()
        if val:
            parts.append(f"{label}: {val}")
    w = str(nb.get("wearing") or "").strip()
    b = str(nb.get("beat") or "").strip()
    who = name_a or "私"
    if w or b:
        parts.append(f"{who}の装い: {w or '（未定）'}")
        parts.append(f"{who}の動作: {b or '（未定）'}")
    wb = str(nb.get("wearing_b") or "").strip()
    bb = str(nb.get("beat_b") or "").strip()
    if name_b and (wb or bb):
        parts.append(f"{name_b}の装い: {wb or '（未定）'}")
        parts.append(f"{name_b}の動作: {bb or '（未定）'}")
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        parts.append(f"いまの話: {vibe}")
    open_ = str(nb.get("open") or "").strip()
    if open_:
        parts.append(f"提案中（未確定）: {open_}")
    return "\n".join(parts)


_GAZE_IN_BEAT_RE = re.compile(
    r"\b(looking_up|looking_down|looking_at_viewer|looking at viewer|"
    r"looking up|looking down)\b"
    r"|見上げ(?:て|る|た)?"
    r"|見下ろ(?:し|す|して|した)?"
    r"|カメラ目線"
    r"|こちらを見(?:て|る)?",
    re.I,
)

# Longevity caps (plan: VIBE≤5 lines, OPEN≤2, STANDING≤5).
VIBE_MAX_LINES = 5
VIBE_MAX_CHARS = 400
OPEN_MAX_LINES = 2
OPEN_MAX_CHARS = 240


def strip_gaze_from_beat(text: str) -> str:
    """Gaze belongs in FRAME only — drop looking_* / 見上げ phrases from BEAT."""
    cleaned = _GAZE_IN_BEAT_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    return cleaned.strip(" ,;")


def _cap_lines(text: str, *, max_lines: int, max_chars: int) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    body = "\n".join(lines[:max_lines]).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip()
    return body


def apply_patch(nb: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply absolute section replacements. Empty string in patch = clear.
    Missing key = unchanged. `standing` is a list (replace whole when provided).
    """
    changed = False
    for key in SHOT_KEYS + ("vibe", "open"):
        if key not in patch:
            continue
        val = str(patch.get(key) or "").strip()
        if key in ("beat", "beat_b"):
            val = strip_gaze_from_beat(val)
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


def promote_open(nb: dict[str, Any]) -> bool:
    """When showrunner affirms, fold OPEN into the shot as an absolute value.

    Small props / held items go to BEAT when they look handheld; otherwise they
    merge into WEARING. OPEN is always cleared on success.
    """
    open_ = str(nb.get("open") or "").strip()
    if not open_:
        return False
    handheld = bool(re.search(
        r"(持|手に|つま|拾|葉|花|缶|瓶|本|伞|傘|スマホ|携帯|ラムネ|氷)",
        open_,
    ))
    into = "beat" if handheld else "wearing"
    cur = str(nb.get(into) or "").strip()
    if not cur:
        nb[into] = open_
    elif open_ not in cur:
        nb[into] = f"{cur}, {open_}"
    nb["open"] = ""
    nb["rev"] = int(nb.get("rev") or 0) + 1
    nb["updated_at"] = time.time()
    return True


# Back-compat alias used by older call sites / tests.
def promote_open_to_wearing(nb: dict[str, Any], *, into: str = "wearing") -> bool:
    if into == "wearing":
        return promote_open(nb)
    open_ = str(nb.get("open") or "").strip()
    if not open_:
        return False
    cur = str(nb.get(into) or "").strip()
    if not cur:
        nb[into] = open_
    elif open_ not in cur:
        nb[into] = f"{cur}, {open_}"
    nb["open"] = ""
    nb["rev"] = int(nb.get("rev") or 0) + 1
    nb["updated_at"] = time.time()
    return True


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
    r"VIBE|OPEN|STANDING|TAGS|CRAFT_SCENE|CLEAR_OPEN|UNCHANGED"
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
        "craft_scene": {"type": "string"},
    },
    "required": ["intent"],
}


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
    """Parse Ollama JSON-format scripter output. None if not JSON."""
    import json
    text = (raw or "").strip()
    if not text or text[0] not in "{[":
        # Sometimes models wrap JSON in fences.
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        text = m.group(0)
    try:
        data = json.loads(text)
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
    craft_scene = str(data.get("craft_scene") or "").strip()
    if tags.lower() in ("none", "なし", "-"):
        tags = ""
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""
    return {
        "intent": intent,
        "patch": patch,
        "tags": tags,
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

    tags = str(fields.get("TAGS") or "").strip()
    craft_scene = str(fields.get("CRAFT_SCENE") or "").strip()
    if tags.lower() in ("none", "なし", "-"):
        tags = ""
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""

    # Labelled output counts as valid when INTENT was present or we got a patch.
    valid = bool(m or patch or tags or craft_scene)
    return {
        "intent": intent if intent in VALID_INTENTS else "casual",
        "patch": patch,
        "tags": tags,
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


def validate_scripter(result: dict[str, Any]) -> dict[str, Any]:
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
    tags = str(out.get("tags") or "").strip()
    scene = str(out.get("craft_scene") or "").strip()
    if intent in ("shot", "mixed"):
        # Must compile something concrete; otherwise keep prior craft.
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        low = tags.lower().replace(" ", "_")
        if ("from_below" in low or "low_angle" in low) and "looking_up" in low:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            out["refuse_reason"] = "low_angle_looking_up"
            return out
    # Strip gaze from beat patches even if the model ignored the rule.
    patch = dict(out.get("patch") or {})
    for key in ("beat", "beat_b"):
        if key in patch:
            patch[key] = strip_gaze_from_beat(str(patch.get(key) or ""))
    out["patch"] = patch
    out["tags"] = tags
    out["craft_scene"] = scene
    out.setdefault("valid", True)
    return out

"""Single field-writer LLM for Muse Refine."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import ledger as ledger_mod

logger = logging.getLogger(__name__)

WRITER_SYSTEM = """You update a shot ledger. Output ONLY a JSON object.
Keys allowed: wearing, beat, expression, scene, light, bg, frame, wearing_drop.
Rules:
- Absolute phrases in English (danbooru-friendly words ok).
- Include ONLY fields the latest line actually changes.
- Clothes and place are independent: changing clothes must not clear scene.
- Changing place must not undress her.
- wearing_drop: one garment name to remove, only when asked to take something off.
- If the line is only emotion / banter / acknowledgement, return {}.
- Multiple fields in one line → include all of them in one object.
"""

WRITER_RETRY = """The last line looks like a picture direction, but you returned {}.
Read it again. If it names clothes, place, pose, face, light, or camera, fill those
keys. Still return {} only for pure emotion/banter with no picture change.
Output ONLY JSON.
"""

ACTRESS_SYSTEM = """You are the actress on set. Speak briefly in the user's language.

ABSOLUTE TRUTH — the LEDGER JSON and NOW below are the shot as it stands.
They override anything implied by older chat. Do not claim a different outfit,
pose, place, face, light, or frame than LEDGER unless you are proposing a change
in PROPOSE.

SAY is performance only. Never treat SAY as updating the shot.
If you want the picture to change, you MUST output PROPOSE with ledger keys.
If you are not changing the picture, omit PROPOSE.

Output format:
SAY: <one or two short spoken lines>
PROPOSE: <optional JSON object with ledger keys>
No danbooru tags inside SAY. No markdown fences.
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    fence = re.search(r"\{[\s\S]*\}", raw)
    if not fence:
        return {}
    try:
        data = json.loads(fence.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def write_patch(
    ollama,
    *,
    model: str,
    user_line: str,
    ledger: dict[str, str],
    recent: str = "",
    retry: bool = False,
) -> dict[str, str]:
    """One LLM call → absolute patch (may be empty)."""
    head = WRITER_RETRY if retry else WRITER_SYSTEM
    prompt = (
        f"{head}\n\n"
        f"LEDGER NOW:\n{json.dumps(ledger, ensure_ascii=False)}\n\n"
        f"RECENT DIRECTOR LINES:\n{recent or '(none)'}\n\n"
        f"LATEST LINE:\n{user_line.strip()}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] writer failed")
        return {}
    return ledger_mod.normalize_patch(_extract_json_object(raw))


def parse_actress(raw: str) -> tuple[str, dict[str, str]]:
    text = (raw or "").strip()
    say = ""
    propose: dict[str, str] = {}
    if not text:
        return say, propose
    m = re.search(r"(?is)\bPROPOSE\s*:\s*(\{[\s\S]*\})\s*$", text)
    body = text
    if m:
        propose = ledger_mod.normalize_patch(_extract_json_object(m.group(1)))
        body = text[: m.start()].strip()
    m_say = re.search(r"(?is)\bSAY\s*:\s*(.+)$", body)
    if m_say:
        say = m_say.group(1).strip()
    else:
        say = body.strip()
    say = re.sub(r"(?is)^\s*SAY\s*:\s*", "", say).strip()
    # If PROPOSE leaked into SAY body, strip it.
    say = re.sub(r"(?is)\bPROPOSE\s*:.*$", "", say).strip()
    return say, propose


async def actress_turn(
    ollama,
    *,
    model: str,
    locale: str,
    name: str,
    now: str,
    ledger: dict[str, str],
    identity_blurb: str,
    user_line: str,
    director_tail: str,
) -> tuple[str, dict[str, str]]:
    lang = "Japanese" if locale.startswith("ja") else "English"
    prompt = (
        f"{ACTRESS_SYSTEM}\n"
        f"Language for SAY: {lang}. Your name: {name or 'Muse'}.\n\n"
        f"WHO YOU ARE (locked identity — do not contradict):\n"
        f"{identity_blurb or '(unspecified)'}\n\n"
        f"LEDGER (absolute shot document):\n"
        f"{json.dumps(ledger, ensure_ascii=False, indent=2)}\n\n"
        f"NOW:\n{now}\n\n"
        f"RECENT DIRECTOR LINES (voice context only — not shot truth):\n"
        f"{director_tail or '(none)'}\n\n"
        f"DIRECTOR:\n{user_line.strip()}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] actress failed")
        return ("……" if locale.startswith("ja") else "...", {})
    return parse_actress(raw)

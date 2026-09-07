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

VERIFY_SYSTEM = """You check whether the shot LEDGER matches the director's latest intent.

Compare DIRECTOR line to LEDGER. Ignore pure emotion/banter — those need no picture change.

Output exactly:
OK: yes
COMMENT: <one short spoken line in the requested language confirming the shot is right>
or
OK: no
COMMENT: <one short spoken line: admit the miss and that you will fix it yourself>
REPAIR: <JSON object with ledger keys to fix — absolute English phrases, only wrong fields>

Rules:
- Clothes and place are independent.
- REPAIR only when OK: no. Empty {} is not allowed when OK: no if the director named a picture change.
- Do not invent unrelated wardrobe. Fix only what the director asked.
- COMMENT is in-character, one sentence, no danbooru tags.
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


def parse_verify(raw: str) -> tuple[bool, str, dict[str, str]]:
    """Returns (ok, comment, repair_patch)."""
    text = (raw or "").strip()
    if not text:
        return True, "", {}
    ok_m = re.search(r"(?im)^\s*OK\s*:\s*(yes|no|true|false|ok|ng)\s*$", text)
    # Also allow inline OK: yes
    if not ok_m:
        ok_m = re.search(r"(?i)\bOK\s*:\s*(yes|no|true|false|ok|ng)\b", text)
    ok_tok = (ok_m.group(1).lower() if ok_m else "yes")
    ok = ok_tok in {"yes", "true", "ok"}

    comment = ""
    c_m = re.search(r"(?is)\bCOMMENT\s*:\s*(.+?)(?=\n\s*REPAIR\s*:|\Z)", text)
    if c_m:
        comment = c_m.group(1).strip()
        comment = re.sub(r"(?is)\bREPAIR\s*:.*$", "", comment).strip()
        # first line only
        comment = comment.splitlines()[0].strip() if comment else ""

    repair: dict[str, str] = {}
    r_m = re.search(r"(?is)\bREPAIR\s*:\s*(\{[\s\S]*\})\s*$", text)
    if r_m:
        repair = ledger_mod.normalize_patch(_extract_json_object(r_m.group(1)))
    elif not ok:
        # Sometimes model dumps JSON alone after COMMENT.
        repair = ledger_mod.normalize_patch(_extract_json_object(text))

    if ok:
        repair = {}
    return ok, comment, repair


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


async def verify_and_repair(
    ollama,
    *,
    model: str,
    locale: str,
    name: str,
    user_line: str,
    ledger: dict[str, str],
    now: str,
    force_repair_hint: bool = False,
) -> tuple[bool, str, dict[str, str]]:
    """After the turn: confirm intent match, or return a self-repair patch."""
    lang = "Japanese" if locale.startswith("ja") else "English"
    hint = (
        "\nNOTE: A picture direction may have been missed earlier — look carefully.\n"
        if force_repair_hint else ""
    )
    prompt = (
        f"{VERIFY_SYSTEM}\n"
        f"Language for COMMENT: {lang}. Speaker name: {name or 'Muse'}.\n"
        f"{hint}\n"
        f"DIRECTOR:\n{user_line.strip()}\n\n"
        f"LEDGER:\n{json.dumps(ledger, ensure_ascii=False, indent=2)}\n\n"
        f"NOW:\n{now}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] verify failed")
        fallback = (
            "うん、この画で合ってる。"
            if locale.startswith("ja") else
            "Yeah — this shot matches."
        )
        return True, fallback, {}
    return parse_verify(raw)

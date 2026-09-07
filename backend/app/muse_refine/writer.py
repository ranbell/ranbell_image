"""Single field-writer LLM for Muse Refine."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import ledger as ledger_mod

logger = logging.getLogger(__name__)


def _voice_block(character: dict[str, Any] | None, *, locale: str) -> str:
    """Reuse Muse duet voice contract so Refine matches her individual speech."""
    if not character:
        return ""
    try:
        from ..muse import crew
        return crew._voice_block(character, locale=locale)
    except Exception:
        logger.exception("[muse_refine] voice_block failed")
        return ""

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
ASIDE is your inner mutter (内心) — required every turn. Whispered, cute, same
language as SAY. Chat-visible. Not shot truth. Do not dump wardrobe status into ASIDE.
If you want the picture to change, you MUST output PROPOSE with ledger keys.
If you are not changing the picture, omit PROPOSE.

VOICE is mandatory. Every SAY and ASIDE must sound like THIS girl only — first
person, address, talk quirks, and example rhythm from the VOICE block. A generic
soft polite line that any other Muse could say is a failure; rewrite until only
she would say it.

Output format:
SAY: <one or two short spoken lines in HER voice>
ASIDE: <1–2 sentences of inner mutter / 内心, whispered, her voice>
PROPOSE: <optional JSON object with ledger keys>
No danbooru tags inside SAY or ASIDE. No markdown fences.
"""

VERIFY_SYSTEM = """You check whether the shot LEDGER matches the director's latest intent.

Compare DIRECTOR line to LEDGER. Ignore pure emotion/banter — those need no picture change.

COMMENT must be spoken IN CHARACTER using the VOICE block (first person, address,
talk quirks, example rhythm). Generic announcer lines like "確認しました" without
her quirks are a failure.

Output exactly:
OK: yes
COMMENT: <one short spoken line in HER voice confirming the shot is right>
or
OK: no
COMMENT: <one short spoken line in HER voice: admit the miss and that YOU will fix it>
REPAIR: <JSON object with ledger keys to fix — absolute English phrases, only wrong fields>

Rules:
- Clothes and place are independent.
- REPAIR only when OK: no. Empty {} is not allowed when OK: no if the director named a picture change.
- Do not invent unrelated wardrobe. Fix only what the director asked.
- COMMENT: no danbooru tags, no system jargon.
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


def parse_actress(raw: str) -> tuple[str, str, dict[str, str]]:
    """Returns (say, aside, propose)."""
    text = (raw or "").strip()
    say = ""
    aside = ""
    propose: dict[str, str] = {}
    if not text:
        return say, aside, propose

    m = re.search(r"(?is)\bPROPOSE\s*:\s*(\{[\s\S]*\})\s*$", text)
    body = text
    if m:
        propose = ledger_mod.normalize_patch(_extract_json_object(m.group(1)))
        body = text[: m.start()].strip()

    # Split ASIDE (may appear after SAY).
    aside_m = re.search(
        r"(?is)\bASIDE\s*:\s*(.+?)(?=\n\s*(?:PROPOSE|CARD)\s*:|\Z)",
        body,
    )
    if aside_m:
        aside = aside_m.group(1).strip()
        aside = re.sub(r"(?is)\b(?:PROPOSE|CARD)\s*:.*$", "", aside).strip()
        aside = aside.splitlines()[0].strip() if aside else aside
        body = (body[: aside_m.start()] + body[aside_m.end():]).strip()

    m_say = re.search(r"(?is)\bSAY\s*:\s*(.+)$", body)
    if m_say:
        say = m_say.group(1).strip()
    else:
        say = body.strip()
    say = re.sub(r"(?is)^\s*SAY\s*:\s*", "", say).strip()
    say = re.sub(r"(?is)\b(?:ASIDE|PROPOSE)\s*:.*$", "", say).strip()
    return say, aside, propose


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
    character: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    lang = "Japanese" if locale.startswith("ja") else "English"
    voice = _voice_block(character, locale=("en" if lang == "English" else "ja"))
    prompt = (
        f"{ACTRESS_SYSTEM}\n"
        f"Language for SAY: {lang}. Your name: {name or 'Muse'}.\n\n"
        f"{voice}\n\n"
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
        return ("……" if locale.startswith("ja") else "...", "", {})
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
    character: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, str]]:
    """After the turn: confirm intent match, or return a self-repair patch."""
    lang = "Japanese" if locale.startswith("ja") else "English"
    voice = _voice_block(character, locale=("en" if lang == "English" else "ja"))
    hint = (
        "\nNOTE: A picture direction may have been missed earlier — look carefully.\n"
        if force_repair_hint else ""
    )
    prompt = (
        f"{VERIFY_SYSTEM}\n"
        f"Language for COMMENT: {lang}. Speaker name: {name or 'Muse'}.\n"
        f"{hint}\n"
        f"{voice}\n\n"
        f"DIRECTOR:\n{user_line.strip()}\n\n"
        f"LEDGER:\n{json.dumps(ledger, ensure_ascii=False, indent=2)}\n\n"
        f"NOW:\n{now}\n"
    )
    try:
        raw = await ollama.generate_text(prompt, model=model or None)
    except Exception:
        logger.exception("[muse_refine] verify failed")
        # Soft fallback still tries her address/first person if present.
        first = ""
        addr = ""
        if character:
            first = str(
                character.get("first_person_ja")
                or (character.get("personality") or {}).get("first_person_ja")
                or "私"
            )
            addr = str(
                character.get("user_address_ja")
                or (character.get("personality") or {}).get("user_address_ja")
                or "総監督"
            )
        if locale.startswith("ja"):
            fallback = f"{first}、この画で合ってると思うよ、{addr}。"
        else:
            fallback = "Yeah — this shot matches."
        return True, fallback, {}
    return parse_verify(raw)

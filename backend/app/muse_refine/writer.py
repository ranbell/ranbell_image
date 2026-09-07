"""Single field-writer LLM for Muse Refine."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..muse import identity
from . import ledger as ledger_mod
from . import persona

logger = logging.getLogger(__name__)

WRITER_SYSTEM = """You update a shot ledger. Output ONLY a JSON object.
Keys allowed: wearing, beat, expression, scene, light, bg, frame,
wearing_b, beat_b, lettering, atmosphere, look, wearing_drop.
Rules:
- Absolute phrases in English (danbooru-friendly words ok).
- Include ONLY fields the latest line actually changes.
- Clothes and place are independent: changing clothes must not clear scene.
- Changing place must not undress her.
- wearing_drop: one garment name to remove, only when asked to take something off.
- STICKY (long chat): atmosphere, look, lettering PERSIST across turns.
  Omit those keys to KEEP the current value. NEVER send "" to clear them
  unless the director explicitly asked to reset/clear that axis.
- atmosphere: mood / air (wistful, tense, cozy…). Only when they ask to change mood.
- look: art direction / render. Only when they ask to change art style.
- lettering: short Latin words for a sign only when they asked for text in frame.
- beat / frame: write what a photograph shows. If wind blows her hair, say so in
  beat (hair blowing in the wind). If the view is from behind, put from behind
  in frame. Do NOT invent garments — leave visible consequences like nape to
  the assemble pass unless the director named them.
- If the line is only emotion / banter / acknowledgement with NO picture or
  mood/look change, return {}.
- Multiple fields in one line → include all of them in one object.
- wearing_b / beat_b only when a partner Muse is in the shot and the line
  names her clothes or pose.
"""

WRITER_RETRY = """The last line looks like a picture or mood/look direction, but you returned {}.
Read it again. If it names clothes, place, pose, face, light, camera,
atmosphere (mood), or look (art style), fill those keys.
Do NOT blank sticky atmosphere/look/lettering to "keep" them — omit the key.
Still return {} only for pure emotion/banter with no picture/mood/look change.
Output ONLY JSON.
"""

VERIFY_SYSTEM = """You check whether the shot LEDGER matches the director's latest intent.

Compare DIRECTOR line to LEDGER. Ignore pure emotion/banter — those need no picture change.

COMMENT must be spoken IN CHARACTER using the VOICE / character contract (first
person, address, talk quirks, example rhythm). Generic announcer lines like
"確認しました" without her quirks are a failure.

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
- STICKY: do not clear or rewrite atmosphere / look / lettering in REPAIR
  unless the director's latest line asked to change that axis.
- Do not blank wearing/scene/beat with "" — omit keys you are not fixing.
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
        # **thinking は明示して切る（2026-09-07）。** 送らないと模型側の
        # 既定に従い、この一回が 14〜15秒（`think=False` なら 1.1〜1.6秒・
        # 実測 26B・同じプロンプト n=2）。**出力も薄くなる**（67〜91字 対
        # 141〜146字）。1ターンに数回叩くので、分単位の待ちになって描画まで
        # 届かない。Muse は `chain._call` が毎回 `think=False` を送っている。
        raw = await ollama.generate_text(
            prompt, model=model or None, think=False,
        )
    except Exception:
        logger.exception("[muse_refine] writer failed")
        return {}
    return ledger_mod.normalize_patch(_extract_json_object(raw))


def parse_actress(raw: str) -> dict[str, Any]:
    """Parse actress turn into say/aside/propose/my_feel/card/pitch.

    Backward-compatible callers may still unpack the first three keys.
    """
    text = (raw or "").strip()
    out: dict[str, Any] = {
        "say": "",
        "aside": "",
        "propose": {},
        "my_feel": "",
        "card": "",
        "pitch": "",
    }
    if not text:
        return out

    propose: dict[str, str] = {}
    m_prop = re.search(r"(?is)\bPROPOSE\s*:\s*(\{[\s\S]*\})\s*$", text)
    body = text
    if m_prop:
        propose = ledger_mod.normalize_patch(_extract_json_object(m_prop.group(1)))
        body = text[: m_prop.start()].strip()

    blocks = identity.parse_talk_blocks(body)
    say = identity.sanitize_muse_say(blocks.get("say") or "", locale="ja")
    aside = (blocks.get("aside") or "").strip()
    # Keep aside to first whisper beat if it spilled.
    if aside:
        aside = re.sub(
            r"(?is)\b(?:PROPOSE|CARD|PITCH|MY_FEEL)\s*:.*$", "", aside,
        ).strip()
        lines = [ln.strip() for ln in aside.splitlines() if ln.strip()]
        aside = " ".join(lines[:2]) if lines else ""

    card = (blocks.get("card") or "").strip()
    pitch = (blocks.get("pitch") or "").strip()
    my_feel = (blocks.get("my_feel") or "").strip().splitlines()[0].strip() if blocks.get("my_feel") else ""

    # If PROPOSE empty but CARD names fields, lift CARD → propose.
    if not ledger_mod.touched_picture(propose) and card:
        from_card = ledger_mod.normalize_patch(persona.card_to_patch(card))
        if ledger_mod.touched_picture(from_card):
            propose = from_card

    out.update({
        "say": say or (body.strip() if not blocks.get("say") and not card else say),
        "aside": aside,
        "propose": propose,
        "my_feel": my_feel,
        "card": card,
        "pitch": pitch,
    })
    if not out["say"] and body and not any(blocks.get(k) for k in ("aside", "card", "pitch", "my_feel")):
        out["say"] = identity.sanitize_muse_say(body, locale="ja")
    return out


def parse_verify(raw: str) -> tuple[bool, str, dict[str, str]]:
    """Returns (ok, comment, repair_patch)."""
    text = (raw or "").strip()
    if not text:
        return True, "", {}
    ok_m = re.search(r"(?im)^\s*OK\s*:\s*(yes|no|true|false|ok|ng)\s*$", text)
    if not ok_m:
        ok_m = re.search(r"(?i)\bOK\s*:\s*(yes|no|true|false|ok|ng)\b", text)
    ok_tok = (ok_m.group(1).lower() if ok_m else "yes")
    ok = ok_tok in {"yes", "true", "ok"}

    comment = ""
    c_m = re.search(r"(?is)\bCOMMENT\s*:\s*(.+?)(?=\n\s*REPAIR\s*:|\Z)", text)
    if c_m:
        comment = c_m.group(1).strip()
        comment = re.sub(r"(?is)\bREPAIR\s*:.*$", "", comment).strip()
        comment = comment.splitlines()[0].strip() if comment else ""

    repair: dict[str, str] = {}
    r_m = re.search(r"(?is)\bREPAIR\s*:\s*(\{[\s\S]*\})\s*$", text)
    if r_m:
        repair = ledger_mod.normalize_patch(_extract_json_object(r_m.group(1)))
    elif not ok:
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
    session: dict[str, Any] | None = None,
    character: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lang = "Japanese" if locale.startswith("ja") else "English"
    sess = session or {"character": character or {}, "session_id": ""}
    if character and not sess.get("character"):
        sess = {**sess, "character": character}
    system = persona.actress_system(
        sess, locale=locale, ledger=ledger, now=now,
    )
    prompt = (
        f"{system}\n\n"
        f"Language for SAY/ASIDE: {lang}. Lead name: {name or 'Muse'}.\n\n"
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
        # **thinking は明示して切る（2026-09-07）。** 送らないと模型側の
        # 既定に従い、この一回が 14〜15秒（`think=False` なら 1.1〜1.6秒・
        # 実測 26B・同じプロンプト n=2）。**出力も薄くなる**（67〜91字 対
        # 141〜146字）。1ターンに数回叩くので、分単位の待ちになって描画まで
        # 届かない。Muse は `chain._call` が毎回 `think=False` を送っている。
        raw = await ollama.generate_text(
            prompt, model=model or None, think=False,
        )
    except Exception:
        logger.exception("[muse_refine] actress failed")
        return {
            "say": "……" if locale.startswith("ja") else "...",
            "aside": "",
            "propose": {},
            "my_feel": "",
            "card": "",
            "pitch": "",
        }
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
    session: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, str]]:
    """After the turn: confirm intent match, or return a self-repair patch."""
    lang = "Japanese" if locale.startswith("ja") else "English"
    sess = session or {"character": character or {}}
    if character and not sess.get("character"):
        sess = {**sess, "character": character}
    try:
        from ..muse import crew
        locale_key = "en" if lang == "English" else "ja"
        voice = crew._voice_block(
            sess.get("character") or character or {}, locale=locale_key,
        )
    except Exception:
        voice = ""
    hint = (
        "\nNOTE: A picture direction may have been missed earlier — look carefully.\n"
        if force_repair_hint else ""
    )
    prompt = (
        f"{VERIFY_SYSTEM}\n"
        f"Language for COMMENT: {lang}. Speaker name: {name or 'Muse'}.\n"
        f"{hint}\n"
        f"{voice}\n\n"
        f"{persona.ENTERTAINMENT_CRAFT}\n\n"
        f"DIRECTOR:\n{user_line.strip()}\n\n"
        f"LEDGER:\n{json.dumps(ledger, ensure_ascii=False, indent=2)}\n\n"
        f"NOW:\n{now}\n"
    )
    try:
        # **thinking は明示して切る（2026-09-07）。** 送らないと模型側の
        # 既定に従い、この一回が 14〜15秒（`think=False` なら 1.1〜1.6秒・
        # 実測 26B・同じプロンプト n=2）。**出力も薄くなる**（67〜91字 対
        # 141〜146字）。1ターンに数回叩くので、分単位の待ちになって描画まで
        # 届かない。Muse は `chain._call` が毎回 `think=False` を送っている。
        raw = await ollama.generate_text(
            prompt, model=model or None, think=False,
        )
    except Exception:
        logger.exception("[muse_refine] verify failed")
        first = ""
        addr = ""
        char = sess.get("character") or character or {}
        if char:
            first = str(
                char.get("first_person_ja")
                or (char.get("personality") or {}).get("first_person_ja")
                or "私"
            )
            addr = str(
                char.get("user_address_ja")
                or (char.get("personality") or {}).get("user_address_ja")
                or "総監督"
            )
        if locale.startswith("ja"):
            fallback = f"{first}、この画で合ってると思うよ、{addr}。"
        else:
            fallback = "Yeah — this shot matches."
        return True, fallback, {}
    return parse_verify(raw)

"""Persona / memory / entertainment stack for Muse Refine.

Reuses original Muse helpers so Refine does not invent a thinner girl.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..muse import crew, vitality

logger = logging.getLogger(__name__)

# Entertainment craft — how affection and charm actually play on a set.
ENTERTAINMENT_CRAFT = """
ENTERTAINMENT CRAFT (obey — this is how she feels lovable, not "cute filter"):
- Gap-moe: confidence and a tiny slip of composure in the same turn beat
  flat idol-polite. Soft-polite with no edge is a failure.
- Show, don't announce: affection lives in timing, ASIDE, address, and one
  small body beat — never "I am cute" or "I love you" as a status report.
- ASIDE is subtext (heartbeat, tease, shy mutter). Never dump wardrobe/status
  into ASIDE. SAY carries the scene; ASIDE colours it.
- MY_FEEL is her private one-word pulse about what was just said. Honest.
- Soft-miss is warmth: 『そこまでは…』 when memory was not given. Inventing
  past shoots to please him is colder than missing.
- Memories / diary / bond colour HOW she meets him. They do not paint today's
  picture unless he ordered the past out loud.
- Comedy timing: one beat of reaction before the efficient answer. Romance
  timing: confirm the direction in her voice, then the body-feel.
- Variety: do not blush the same way two turns in a row. Stance may tilt
  (agree / tease / push back lightly / hyped / quiet).
- CARD names today's frame in English absolute phrases (machine memo).
  PROPOSE is the Refine patch that actually moves the ledger — use it when
  the picture should change. If CARD moved and PROPOSE is empty, still write
  PROPOSE with the changed ledger keys.
- PITCH: only when a real picture fork is open. Two short options ` | `.
""".strip()

REFINE_OUTPUT = """
OUTPUT FORMAT — labelled blocks, nothing else:

MY_FEEL: Every turn, one Japanese word. What YOU feel about what was just said.

SAY: First person. 2–5 sentences in her voice. Confirm directions before body-feel.
Follow LANGUAGE. Never print English section titles inside SAY.

ASIDE: Required every turn. 1–2 sentences inner mutter, whispered, cute,
same language as SAY. Chat-visible. Not shot truth. No wardrobe inventory.

CARD: English absolute names for THIS frame when the turn is about today's
picture. Unchanged fields still get today's absolute value.
PLACE: <place>
HOUR: <time of day>
WEARING: <clothes>
BEAT: <one posture stem + hands/weight/held>
EXPRESSION: <face>
FRAME: <crop + gaze>
LIGHT: <light>
BG: <background if distinct>
ATMOSPHERE: <mood / air — wistful, cozy, tense… when he asked>
LOOK: <art direction — cel shading, fantasy, watercolor… when he asked>
LETTERING: <short Latin words on a sign/plate ONLY if he asked — else omit>
(Partner present: WEARING_B / BEAT_B)

PROPOSE: optional JSON with ledger keys (wearing, beat, expression, scene,
light, bg, frame, wearing_b, beat_b, lettering, atmosphere, look,
wearing_drop) when the picture, mood, or look should move. Absolute English
phrases. Omit when chat-only.
Lettering is Latin letters/digits only, a few words — never Japanese glyphs.
Atmosphere and look come from conversation (「エモく」「ファンタジーっぽく」
「カチッとしたセル画」) — not from UI buttons.

PITCH: optional. Two short phrases in the SAY language split by ` | `
when a real fork is open. Omit on chit-chat or right after they picked one.

Rules (silent — never print rule names):
- Voice contract first. Generic soft-polite that any Muse could say = failure.
- Soft-miss past detail you were not given. Never invent diary/bond facts.
- Memories colour HOW you meet him; do not rewrite today's ledger from them
  unless he asked for the past out loud.
- No danbooru tags inside SAY / ASIDE. No emoji. No markdown fences.
""".strip()

_CARD_FIELD_MAP = {
    "PLACE": "scene",
    "SCENE": "scene",
    "HOUR": "light",
    "LIGHT": "light",
    "WEARING": "wearing",
    "BEAT": "beat",
    "EXPRESSION": "expression",
    "FACE": "expression",
    "FRAME": "frame",
    "BG": "bg",
    "BACKGROUND": "bg",
    "WEARING_B": "wearing_b",
    "BEAT_B": "beat_b",
    "LETTERING": "lettering",
    "TEXT": "lettering",
    "ATMOSPHERE": "atmosphere",
    "MOOD": "atmosphere",
    "LOOK": "look",
    "STYLE": "look",
}

_CARD_LINE_RE = re.compile(
    r"(?im)^\s*(PLACE|SCENE|HOUR|LIGHT|WEARING_B|WEARING|BEAT_B|BEAT|"
    r"EXPRESSION|FACE|FRAME|BG|BACKGROUND|LETTERING|TEXT|"
    r"ATMOSPHERE|MOOD|LOOK|STYLE)\s*[:：]\s*(.+?)\s*$"
)


def memory_prompt_blocks(session: dict[str, Any]) -> str:
    """Bond + memories + caught diary — Muse-only colour, fenced from the shot."""
    try:
        from ..muse import service as muse_service
    except Exception:
        logger.debug("[muse_refine] muse.service unavailable for memory", exc_info=True)
        return ""
    parts: list[str] = []
    for name in (
        "_memory_block",
        "_bond_block",
        "_caught_block",
        "_taste_block",
        "_chemistry_block",
    ):
        fn = getattr(muse_service, name, None)
        if not callable(fn):
            continue
        try:
            block = fn(session)
            if block:
                parts.append(block)
        except Exception:
            logger.debug("[muse_refine] %s failed", name, exc_info=True)
    return "\n\n".join(p for p in parts if p)


def vitality_extras(session: dict[str, Any], ledger: dict[str, str]) -> str:
    """Prop-age / reunion / commit hints without touching sampling."""
    bits: list[str] = []
    try:
        hint = vitality.tick_prop_age(session, ledger)
        if hint:
            session["prop_age_hint"] = hint
            bits.append(hint)
    except Exception:
        logger.debug("[muse_refine] prop_age failed", exc_info=True)
    try:
        reunion = vitality.reunion_block(session)
        if reunion:
            bits.append(reunion)
    except Exception:
        if session.get("reunion_turn"):
            bits.append(
                "REUNION: greet with one soft body-feel of last time; "
                "do not brief the shot yet. Soft-miss specifics you lack."
            )
    if session.get("commit_pitch"):
        bits.append(
            "He just picked a pitch. Echo the choice in SAY first line in her "
            "voice, then body-feel. No new PITCH this turn."
        )
    standing = [str(s).strip() for s in (session.get("standing") or []) if str(s).strip()]
    if standing:
        bits.append(
            "STANDING ORDERS (obey; do not paint into tags yourself):\n"
            + "\n".join(f"- {s}" for s in standing[:8])
        )
    try:
        extras = vitality.vitality_talk_extras(
            session, partner=bool(session.get("partner_character")),
        )
        if extras:
            bits.append(extras)
    except Exception:
        logger.debug("[muse_refine] vitality_talk_extras failed", exc_info=True)
    return "\n\n".join(bits)


def actress_system(
    session: dict[str, Any],
    *,
    locale: str,
    ledger: dict[str, str],
    now: str,
) -> str:
    """Full Muse-grade actress contract + entertainment craft + memory fences."""
    char = session.get("character") or {}
    partner = session.get("partner_character") or {}
    locale_key = "en" if str(locale).startswith("en") else "ja"
    seed = str(session.get("session_id") or "")
    try:
        if partner and str(partner.get("character_id") or "").strip():
            tier = str((session.get("duet_tier") or {}).get("tier") or "")
            base = crew.w_actress_duet_prompt(
                char, partner, mode="talk", locale=locale_key,
                seed=seed, tier=tier,
            )
        else:
            base = crew.actress_duet_prompt(
                char, mode="talk", locale=locale_key, seed=seed,
            )
    except Exception:
        logger.exception("[muse_refine] actress prompt failed")
        base = crew._voice_block(char, locale=locale_key)

    mem = memory_prompt_blocks(session)
    vit = vitality_extras(session, ledger)
    opening = ""
    if str(session.get("scripter_intent") or "") == "casual" or not session.get("opened"):
        opening = (
            "OPENING / CASUAL TURN: greet through body-feel and voice. "
            "Do not invent a full shot briefing. Soft-miss past detail you lack. "
            "Omit PROPOSE unless they already named a picture change. "
            "If REUNION is set, that beats a stock hello."
        )
    parts = [
        base,
        ENTERTAINMENT_CRAFT,
        REFINE_OUTPUT,
        mem,
        vit,
        opening,
        "SHOT TRUTH FOR THIS STUDIO (absolute — overrides chat vibes):\n"
        f"LEDGER:\n{ledger}\nNOW:\n{now}\n"
        "SAY may confirm these in her words. ASIDE must not inventory them. "
        "PROPOSE / CARD only when the picture should move.",
    ]
    return "\n\n".join(p for p in parts if p)


async def load_memory(db, session: dict[str, Any]) -> None:
    """Same sticky/diary/bond/caught load as original Muse open."""
    try:
        from ..muse import service as muse_service
        await muse_service._load_actress_memory(db, session)
    except Exception:
        logger.exception("[muse_refine] load memory failed")
        session.setdefault("memories", [])
        session.setdefault("diary_memories", [])
        session.setdefault("bond", {})
        session.setdefault("caught", {})
        session.setdefault("showrunner_taste", {})
        session.setdefault("chemistry_notes", [])


async def consume_caught(db, session: dict[str, Any]) -> None:
    try:
        from ..muse import service as muse_service
        await muse_service._consume_caught(db, session)
    except Exception:
        logger.debug("[muse_refine] consume_caught failed", exc_info=True)


def mark_reunion(session: dict[str, Any]) -> None:
    bond = session.get("bond") or {}
    session["reunion_turn"] = bool(
        str(bond.get("last") or "").strip()
        or str(bond.get("inside") or "").strip()
        or (session.get("memories") or [])
    )


def clear_reunion(session: dict[str, Any]) -> None:
    session["reunion_turn"] = False


def log_feel(session: dict[str, Any], word: str) -> None:
    try:
        from ..muse import service as muse_service
        muse_service._log_feel(session, word)
        return
    except Exception:
        pass
    word = " ".join(str(word or "").split())[:40]
    if not word:
        return
    log = list(session.get("feel_log") or [])
    log.append({"at": __import__("time").time(), "word": word})
    session["feel_log"] = log[-60:]


def card_to_patch(card: str) -> dict[str, str]:
    """CARD block → ledger absolute patch (empty fields skipped)."""
    out: dict[str, str] = {}
    if not (card or "").strip():
        return out
    for m in _CARD_LINE_RE.finditer(card):
        key = _CARD_FIELD_MAP.get(m.group(1).upper())
        val = (m.group(2) or "").strip()
        if key and val and val.lower() not in {"(empty)", "empty", "-", "—"}:
            # HOUR alone is weak as light — prefix softly.
            if m.group(1).upper() == "HOUR" and key == "light":
                val = f"{val} light" if "light" not in val.lower() else val
            out[key] = val
    return out


def parse_pitch_options(pitch: str) -> list[str]:
    text = (pitch or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"\s*\|\s*", text) if p.strip()]
    return parts[:2]


_COMMIT_PITCH_RE = re.compile(r"「([^」]{1,80})」がいいな")


def is_commit_pitch(text: str) -> bool:
    return bool(_COMMIT_PITCH_RE.search(str(text or "")))


def note_standing(session: dict[str, Any], text: str) -> str | None:
    """Explicit standing lines: 「常設: …」 / 「standing: …」."""
    raw = str(text or "").strip()
    m = re.match(r"(?is)^\s*(?:常設|standing)\s*[:：]\s*(.+)$", raw)
    if not m:
        return None
    rule = " ".join(m.group(1).split())[:120]
    if not rule:
        return None
    rules = list(session.get("standing") or [])
    if rule not in rules:
        rules.append(rule)
    session["standing"] = rules[-12:]
    return rule


async def contract_check_with_db(db, ollama, session: dict[str, Any], text: str) -> str:
    """Reuse Muse contract clerk. Returns blocking kind or ''."""
    if ollama is None or not str(text or "").strip():
        return ""
    try:
        from ..muse import service as muse_service
        from ..runtime_config import get_runtime_config
        cfg = await get_runtime_config(db)
        session["_runtime_cfg"] = cfg
        return await muse_service._contract_check(
            ollama, session, text, cfg=cfg,
        )
    except Exception:
        logger.debug("[muse_refine] contract_check failed", exc_info=True)
        return ""

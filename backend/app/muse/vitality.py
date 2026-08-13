"""Conversation vitality helpers — fun without touching model sampling.

All of this is prompt flags, session counters, and template UI whispers.
Sampling stays on the model card via ``llm_options``.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# These two used to guess from the showrunner's wording which notebook row was
# about to move and what she would murmur about it — a keyword table that was
# wrong whenever the wording was ordinary. The whisper is a plain hold now, and
# the flash key comes from the patch the scripter actually returned.
_ROW_PRIORITY = ("wearing", "wearing_b", "scene", "beat", "beat_b", "frame",
                 "atmosphere")


def silence_whisper(*, locale: str = "ja") -> str:
    """One short body-line while craft updates — template only."""
    return "…ん。" if str(locale).startswith("ja") else "…mm."


def notebook_flash_key(patch: dict[str, Any] | None) -> str:
    """Which notebook row to pulse in the UI, from the scripter's own patch."""
    keys = {k for k, v in (patch or {}).items() if str(v or "").strip()}
    for key in _ROW_PRIORITY:
        if key in keys:
            return key
    return "vibe"


def taste_chips(taste: dict[str, Any] | None, *, locale: str = "ja") -> list[str]:
    """Short insertable lines from showrunner_taste — Muse UI chips."""
    if not isinstance(taste, dict):
        return []
    ja = str(locale).startswith("ja")
    out: list[str] = []
    prefers = str(taste.get("prefers") or "").strip()
    avoids = str(taste.get("avoids") or "").strip()
    if prefers:
        # Take first clause only.
        bit = re.split(r"[、,/]| and ", prefers)[0].strip()[:40]
        if bit:
            out.append(f"また{bit}？" if ja else f"Again: {bit}?")
    if avoids:
        bit = re.split(r"[、,/]| and ", avoids)[0].strip()[:40]
        if bit:
            out.append(f"{bit}は避けて" if ja else f"Skip {bit}")
    return out[:3]


def prop_fingerprint(nb: dict[str, Any]) -> str:
    blob = "|".join(
        str(nb.get(k) or "").strip().lower()
        for k in ("scene", "wearing", "wearing_b", "beat")
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12] if blob.strip("|") else ""


def tick_prop_age(session: dict[str, Any], nb: dict[str, Any]) -> str:
    """Advance prop-age counter; return a soft aging hint for Muse (or '')."""
    fp = prop_fingerprint(nb)
    if not fp:
        return ""
    age = session.setdefault("prop_age", {"fp": "", "turns": 0})
    if age.get("fp") != fp:
        age["fp"] = fp
        age["turns"] = 1
        return ""
    age["turns"] = int(age.get("turns") or 0) + 1
    if age["turns"] in (4, 8):
        return (
            "Same props/outfit have lingered. You may colour one soft beat of "
            "time passing in Japanese SAY (e.g. a warm bottle, longer shadows). "
            "Do not change picture tags yourself."
        )
    return ""


def tick_open_ignore(session: dict[str, Any], *, open_text: str) -> bool:
    """Return True if OPEN should fade (untouched ~2 turns).

    Call this *after* the scripter has run: the scripter reads the conversation
    and either clears OPEN, replaces it, or leaves it alone, so an unchanged
    OPEN is the evidence that nobody engaged with the proposal. This used to
    sniff the showrunner's line for「いいね|うん|いらない」before the scripter
    got a say, and reset the counter on any sentence that happened to contain
    one of those strings.
    """
    open_ = str(open_text or "").strip()
    state = session.setdefault("open_ignore", {"text": "", "count": 0})
    # Cleared or rewritten by the scripter → it was engaged with. Start over.
    if not open_ or state.get("text") != open_:
        state["text"] = open_
        state["count"] = 0
        return False
    state["count"] = int(state.get("count") or 0) + 1
    if state["count"] >= 2:
        state["count"] = 0
        state["text"] = ""
        return True
    return False


def should_b_lead(session: dict[str, Any], *, partner: bool) -> bool:
    """Every few W-Muse talk turns, let B interrupt first."""
    if not partner:
        return False
    n = int(session.get("talk_turn_count") or 0)
    return n > 0 and n % 3 == 0


def bump_talk_turn(session: dict[str, Any]) -> None:
    session["talk_turn_count"] = int(session.get("talk_turn_count") or 0) + 1


def bump_shot_compile(session: dict[str, Any]) -> bool:
    """True when Muse should gently propose notebook cleanup."""
    n = int(session.get("shot_compile_count") or 0) + 1
    session["shot_compile_count"] = n
    return n >= 15 and n % 15 == 0


def again_that_feel_hint(session: dict[str, Any]) -> str:
    """Last sticky recap / memory line for『またあの感じ』."""
    for m in list(session.get("memories") or [])[:1]:
        if str(m).strip():
            return str(m).strip()[:240]
    for r in session.get("cited_memories") or []:
        if isinstance(r, dict):
            when = str(r.get("when") or "").strip()
            shot = str(r.get("shot") or "").strip()
            feel = str(r.get("feel") or "").strip()
            bit = " / ".join(x for x in (when, feel, shot) if x)
            if bit:
                return bit[:240]
    last = str((session.get("bond") or {}).get("last") or "").strip()
    return last[:240]


def reunion_block(session: dict[str, Any]) -> str:
    if not session.get("reunion_turn"):
        return ""
    bond = session.get("bond") or {}
    last = str(bond.get("last") or "").strip()
    inside = str(bond.get("inside") or "").strip()
    if not last and not inside:
        return (
            "REUNION opener: greet briefly through body-feel in Japanese SAY. "
            "Soft-miss past detail outside the memories you were given."
        )
    return "\n".join([
        "REUNION opener (this turn only):",
        f"- One body-feel beat of last time: {last or inside}",
        "- Do not touch picture direction yet. Do not invent detail.",
        "- Soft-miss in Japanese SAY with wind/temperature metaphors, "
        "never a stiff refusal.",
    ])


def vitality_talk_extras(session: dict[str, Any], *, partner: bool = False) -> str:
    """Extra Muse-only instructions assembled for this turn."""
    parts: list[str] = []
    reunion = reunion_block(session)
    if reunion:
        parts.append(reunion)
    if session.get("open_faded"):
        parts.append(
            "The last unlocked OPEN proposal fades naturally — do not cling. "
            "At most one new small proposal."
        )
    age = str(session.get("prop_age_hint") or "").strip()
    if age:
        parts.append(age)
    if session.get("cleanup_nudge"):
        parts.append(
            "Many shots have stacked. You may softly offer in Japanese SAY to "
            "tidy an old prop or conflict (may go in OPEN). Do not delete from "
            "the picture yourself."
        )
    if session.get("w_b_leads") and partner:
        parts.append(
            "This turn Partner (B) may lead first; A rides or teases. "
            "No parallel report-reading."
        )
    again = str(session.get("again_feel_hint") or "").strip()
    if again:
        parts.append(
            "Showrunner is asking for that earlier feel again.\n"
            f"Clue in hand: {again}\n"
            "Recall it through the body in Japanese SAY. Missing detail → "
            "cute soft-miss『そこまでは…』."
        )
    return "\n\n".join(parts)

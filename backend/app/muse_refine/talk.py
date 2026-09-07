"""Talk / cast helpers for Muse Refine — opening, W-bubbles, dress, bans."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..muse import events, identity, vitality
from . import ledger as ledger_mod

logger = logging.getLogger(__name__)

_AGAIN_FEEL_RE = re.compile(
    r"(またあの感じ|あの感じでもう一度|again\s+that\s+feel|that\s+same\s+feel)",
    re.I,
)


def dress_from_signature(session: dict[str, Any]) -> dict[str, str]:
    """Seed ledger wearing / wearing_b from signature wardrobe sets (no LLM)."""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    patch: dict[str, str] = {}

    def _worn(who: dict[str, Any]) -> str:
        sets = list(who.get("wardrobe_sets") or [])
        if not sets:
            # Fallback: board costume / wearing string on character.
            return str(
                (who.get("board") or {}).get("wearing")
                or who.get("wearing")
                or ""
            ).strip()
        row = next((s for s in sets if str(s.get("key")) == "signature"), sets[0])
        tags = [str(t) for t in (row.get("tags") or []) if str(t).strip()]
        props = [str(t) for t in (row.get("props") or []) if str(t).strip()]
        return ", ".join(tags + props)

    lead = session.get("character") or {}
    if not (led.get("wearing") or "").strip():
        w = _worn(lead)
        if w:
            patch["wearing"] = w
    partner = session.get("partner_character") or {}
    if partner and str(partner.get("character_id") or "").strip():
        if not (led.get("wearing_b") or "").strip():
            wb = _worn(partner)
            if wb:
                patch["wearing_b"] = wb
    if patch:
        led = ledger_mod.apply_patch(led, patch)
        session["refine_ledger"] = led
    return patch


def prepare_vitality_flags(session: dict[str, Any], *, user_line: str = "") -> None:
    """Bump counters / set soft hints for this turn."""
    partner = bool(
        (session.get("partner_character") or {}).get("character_id")
    )
    vitality.bump_talk_turn(session)
    session["w_b_leads"] = vitality.should_b_lead(session, partner=partner)
    if _AGAIN_FEEL_RE.search(str(user_line or "")):
        session["again_feel_hint"] = vitality.again_that_feel_hint(session)
    elif not session.get("again_feel_hint"):
        # Keep empty unless explicitly asked; open() may seed once.
        pass


def note_picture_compile(session: dict[str, Any]) -> None:
    if vitality.bump_shot_compile(session):
        session["cleanup_nudge"] = True


def clear_turn_flags(session: dict[str, Any]) -> None:
    session["commit_pitch"] = False
    session["cleanup_nudge"] = False
    session["w_b_leads"] = False
    # again_feel is one-shot after she answered
    session["again_feel_hint"] = ""


def ban_tag(session: dict[str, Any], tag: str) -> None:
    word = " ".join(str(tag or "").split()).strip().lower()
    if not word:
        return
    banned = [str(t) for t in (session.get("banned") or []) if str(t).strip()]
    if word not in {b.lower() for b in banned}:
        banned.append(word)
    session["banned"] = banned[-40:]


def restore_tag(session: dict[str, Any], tag: str) -> bool:
    word = " ".join(str(tag or "").split()).strip().lower()
    before = list(session.get("banned") or [])
    after = [t for t in before if str(t).strip().lower() != word]
    session["banned"] = after
    return len(after) < len(before)


def filter_banned_tags(session: dict[str, Any], tags: list[str]) -> list[str]:
    gone = {str(t).strip().lower() for t in (session.get("banned") or []) if str(t).strip()}
    if not gone:
        return tags
    out: list[str] = []
    for tag in tags:
        low = tag.lower()
        if low in gone:
            continue
        if any(g and g in low for g in gone):
            continue
        out.append(tag)
    return out


def strike_last_user(session: dict[str, Any], *, why: str = "") -> None:
    chat = list(session.get("chat") or [])
    for i in range(len(chat) - 1, -1, -1):
        if chat[i].get("role") == "user":
            meta = dict(chat[i].get("meta") or {})
            meta["struck"] = True
            if why:
                meta["blocked"] = why
            chat[i] = {**chat[i], "meta": meta}
            session["chat"] = chat
            return


def _append_chat(
    session: dict[str, Any],
    *,
    role: str,
    name: str,
    text: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "role": role,
        "name": name,
        "text": text,
        "at": __import__("time").time(),
        **({"meta": meta} if meta else {}),
    }
    chat = list(session.get("chat") or [])
    chat.append(row)
    session["chat"] = chat[-80:]
    return row


def publish_actress_turn(
    session: dict[str, Any],
    actress: dict[str, Any],
    *,
    locale: str,
    lead_name: str,
) -> None:
    """Append SAY (possibly A:/B: split), ASIDE, PITCH to chat."""
    from . import persona

    say = actress.get("say") or ""
    aside = actress.get("aside") or ""
    my_feel = actress.get("my_feel") or ""
    pitch = actress.get("pitch") or ""
    if my_feel:
        persona.log_feel(session, my_feel)

    partner = session.get("partner_character") or {}
    lead = session.get("character") or {}
    name_a = lead.get("name_ja") or lead.get("name") or lead_name or "A"
    name_b = partner.get("name_ja") or partner.get("name") or "B"
    sid = session.get("session_id") or ""

    turns = None
    if partner and str(partner.get("character_id") or "").strip():
        try:
            turns = identity.parse_duet_speakers(
                say, name_a=name_a, name_b=name_b, locale=locale,
            )
        except Exception:
            logger.debug("[muse_refine] duet parse failed", exc_info=True)
            turns = None

    if turns:
        for t in turns:
            who = str(t.get("speaker") or "A").upper()
            text = str(t.get("text") or "").strip()
            if not text:
                continue
            if who == "B":
                cid = str(partner.get("character_id") or "")
                cname = name_b
            else:
                cid = str(lead.get("character_id") or "")
                cname = name_a
            _append_chat(
                session,
                role="assistant",
                name=cname,
                text=text,
                meta={
                    "kind": "say",
                    "speaker": who,
                    "speaker_id": cid,
                    "my_feel": my_feel or None if who == "A" else None,
                },
            )
            events.publish(sid, {
                "type": "chat", "role": "assistant", "name": cname, "text": text,
            })
    else:
        _append_chat(
            session, role="assistant", name=lead_name, text=say,
            meta={"kind": "say", "my_feel": my_feel or None},
        )
        events.publish(sid, {
            "type": "chat", "role": "assistant", "name": lead_name, "text": say,
        })

    if aside:
        # W-aside may be prefixed A:/B:
        speaker, body = "", aside
        try:
            speaker, body = identity.parse_aside_speaker(
                aside, name_a=name_a, name_b=name_b,
            )
        except Exception:
            body = aside
        aside_name = name_b if speaker == "B" else lead_name
        _append_chat(
            session,
            role="assistant",
            name=aside_name,
            text=body or aside,
            meta={"kind": "banter", "speaker": speaker or "A"},
        )
        events.publish(sid, {
            "type": "chat", "role": "assistant", "name": aside_name,
            "text": body or aside, "kind": "banter",
        })

    pitch_opts = persona.parse_pitch_options(pitch)
    if pitch_opts:
        session["last_pitch"] = pitch_opts
        _append_chat(
            session,
            role="assistant",
            name=lead_name,
            text=" ｜ ".join(pitch_opts) if locale.startswith("ja") else " | ".join(pitch_opts),
            meta={"kind": "pitch", "options": pitch_opts},
        )


RESTATE_FIELDS = (
    "wearing", "beat", "expression", "scene", "light", "bg", "frame",
    "wearing_b", "beat_b",
)

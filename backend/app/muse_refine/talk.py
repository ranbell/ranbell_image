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
    "wearing_b", "beat_b", "atmosphere", "look", "lettering",
)

# Gate: mood/look cues only when the line is about the picture, not banter.
_ATM_LOOK_GATE = re.compile(
    r"("
    r"絵|画|ショット|写真|雰囲気|空気|ムード|画風|タッチ|塗り|質感|"
    r"感じで|っぽ|にして|でお願い|でいこう|にしてほしい|にしてくれ|"
    r"エモ|切ない|ほのぼの|ファンタジー|セル|水彩|厚塗り|キラキラ|"
    r"リセット|クリア|"
    r"style|mood|look|atmosphere|render|fantasy|watercolor|cel\b|anime\s*screenshot|"
    r"painterly|gothic|cozy|wistful|melanchol|romantic\s*mood|reset\s+(look|mood|style)"
    r")",
    re.I,
)

# Conversation → atmosphere / look (no UI buttons). First match wins per key;
# later rules can still fill the other key. Absolute English for the ledger.
_ATM_LOOK_RULES: tuple[tuple[re.Pattern[str], dict[str, str]], ...] = (
    # Clear / reset — separate axes when possible
    (re.compile(r"雰囲気.*(リセット|なし|戻|クリア)|ムード.*(リセット|クリア)|reset\s+mood|clear\s+atmosphere", re.I),
     {"atmosphere": ""}),
    (re.compile(r"画風.*(リセット|なし|戻|クリア)|タッチ.*(リセット|クリア)|reset\s+(look|style)|clear\s+look", re.I),
     {"look": ""}),
    (re.compile(r"(画風|雰囲気|ムード|タッチ).*(全部|どちらも|両方).*(リセット|クリア)|全部リセット|reset\s+all\s+(look|mood|style)", re.I),
     {"atmosphere": "", "look": ""}),
    (re.compile(r"(文字|看板|レタリング).*(消|抜|なし|クリア|リセット)|clear\s+lettering|no\s+text\s+in\s+(frame|image)", re.I),
     {"lettering": ""}),
    # Look / render
    (re.compile(r"(カチッ|かっちり|クリーン|くっきり|セル画|セル塗り|シャープな線|clean\s*line|cel[\s-]?shad)", re.I),
     {"look": "anime screenshot, cel shading, clean lineart, flat color, sharp lines"}),
    (re.compile(r"(劇場版|キービジュ|key\s*visual|劇伴っぽ|cinematic\s*anime)", re.I),
     {"look": "anime key visual, dramatic lighting, polished anime illustration, depth of field"}),
    (re.compile(r"(水彩|water\s*colou?r|にじみ)", re.I),
     {"look": "watercolor, soft edges, paper texture, delicate washes"}),
    (re.compile(r"(厚塗り|油絵|oil\s*paint|painterly)", re.I),
     {"look": "painterly, oil painting, thick brush strokes, rich texture"}),
    (re.compile(r"(ラフ|スケッチ|線画|rough\s*sketch|line\s*art\s*only)", re.I),
     {"look": "sketch, lineart, rough lines, unfinished"}),
    (re.compile(r"(ピクセル|ドット|pixel\s*art)", re.I),
     {"look": "pixel art, limited palette"}),
    (re.compile(r"(暗(い)?ファンタジー|ダークファンタジー|dark\s*fantasy|ゴシック)", re.I),
     {"look": "dark fantasy illustration, gothic, ornate shadows",
      "atmosphere": "ominous, heavy air, quiet dread"}),
    (re.compile(r"(ファンタジー|魔法|魔導|ファンタジーっぽ|fantasy)", re.I),
     {"look": "fantasy illustration, magical aura, glowing particles, ornate detail",
      "atmosphere": "wondrous, hush of magic, soft sparkle in the air"}),
    (re.compile(r"(SF|近未来|サイバー|cyberpunk|sci-?fi)", re.I),
     {"look": "sci-fi illustration, neon accents, sleek tech"}),
    (re.compile(r"(レトロ|昭和|90年代|90s\s*anime|retro\s*anime)", re.I),
     {"look": "1990s anime style, retro anime screencap, soft film grain"}),
    # Atmosphere / mood (emo) — avoid bare 恋 / 癒 that fire on banter
    (re.compile(r"(エモ|切ない|寂しい|物憂|哀愁|melanchol|wistful|bittersweet|泣きそう)", re.I),
     {"atmosphere": "wistful, melancholic, tender ache, soft focus, emotional"}),
    (re.compile(r"(ほのぼの|あったかい空気|優しい空気|癒し系|癒やされる空気|cozy|warm\s*and\s*gentle)", re.I),
     {"atmosphere": "cozy, warm, gentle, soft air, comforting"}),
    (re.compile(r"(緊張|ピンと|ピリ|tense|suspense|緊迫)", re.I),
     {"atmosphere": "tense, taut silence, sharp focus"}),
    (re.compile(r"(ロマンチック|甘い空気|恋の空気|romantic\s*mood|intimate\s*mood)", re.I),
     {"atmosphere": "romantic, intimate, soft blush in the air"}),
    (re.compile(r"(派手|キラキラ|きらめ|華やか|sparkle|glitter|耀)", re.I),
     {"atmosphere": "sparkling, glittering light, lively shimmer"}),
    (re.compile(r"(静か|しっとり|静謐|quiet\s*mood|still\s*air|閑)", re.I),
     {"atmosphere": "quiet, still air, hushed, contemplative"}),
    (re.compile(r"(夢|夢幻|幻想的|dreamy|ethereal|霞)", re.I),
     {"atmosphere": "dreamy, ethereal haze, soft glow"}),
    (re.compile(r"(荒涼|寂しい景色|lonely\s*landscape|empty\s*air)", re.I),
     {"atmosphere": "lonely, empty air, distant"}),
)


def cue_atmosphere_look(text: str) -> dict[str, str]:
    """Map director chat → atmosphere/look/lettering (conversation only).

    Banter without picture/mood vocabulary does not fire — protects sticky mood
    across long chats.
    """
    raw = str(text or "").strip()
    if not raw:
        return {}
    if not _ATM_LOOK_GATE.search(raw):
        return {}
    out: dict[str, str] = {}
    for pat, patch in _ATM_LOOK_RULES:
        if not pat.search(raw):
            continue
        for key, val in patch.items():
            # Explicit clear ("") always applies; first concrete fill wins.
            if key in out and out[key] and val:
                continue
            out[key] = val
    return out


def merge_cue_into_patch(patch: dict[str, str], cue: dict[str, str]) -> dict[str, str]:
    """Writer wins on conflict; cue fills gaps (and allows explicit clears)."""
    merged = dict(patch or {})
    for key, val in (cue or {}).items():
        if key not in merged:
            merged[key] = val
    return merged


def cue_allow_clear(cue: dict[str, str] | None) -> set[str]:
    """Keys the director explicitly cleared this turn."""
    return {k for k, v in (cue or {}).items() if v == ""}


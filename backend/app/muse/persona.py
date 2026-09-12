"""Persona / memory / entertainment stack for Muse Refine.

Reuses original Muse helpers so Refine does not invent a thinner girl.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import crew, vitality

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

SAY: First person. 5–10 sentences in her voice. Confirm directions before body-feel.
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
ATMOSPHERE: <mood / air — ONLY if he changed mood this turn; else OMIT>
LOOK: <art direction — ONLY if he changed look this turn; else OMIT>
LETTERING: <short Latin words on a sign ONLY if he asked — else OMIT>
(Partner present: WEARING_B / BEAT_B / EXPRESSION_B — the partner's, not yours)

PROPOSE: optional JSON with ledger keys (wearing, beat, expression, scene,
light, bg, frame, wearing_b, beat_b, expression_b, lettering, atmosphere,
look, wearing_drop) when the picture, mood, or look should move. Absolute English
phrases. Omit when chat-only.
STICKY long-chat rule: atmosphere / look / lettering already on the ledger
KEEP unless he changed them this turn. Never blank them with "" to "keep".
Never invent a new mood/look he did not ask for.
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
- FACE OWNERSHIP (expression): you control the face for the photograph.
  When expression is empty, or place/mood/pose/light just moved and he did NOT
  name a face this turn, PROPOSE an expression that matches THIS scene —
  atmosphere, beat, and light (e.g. wistful mood + low light → soft downturned
  eyes; warm light + a settled beat → gentle smile; looking down + tears →
  glossy lids).
  Prefer your expression_vocab when it fits. Do NOT invent clothes or place.
  If he named a face this turn, keep his face — do not fight it.
- No danbooru tags inside SAY / ASIDE. No emoji. No markdown fences.
""".strip()

_CARD_FIELD_MAP = {
    "PLACE": "scene",
    "SCENE": "scene",
    # HOUR is intentionally omitted — classic Muse keeps time inside scene;
    # mapping HOUR→light hijacked the lighting axis.
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
    "EXPRESSION_B": "expression_b",
    "FACE_B": "expression_b",
    "LETTERING": "lettering",
    "TEXT": "lettering",
    "ATMOSPHERE": "atmosphere",
    "MOOD": "atmosphere",
    "LOOK": "look",
    "STYLE": "look",
}

_CARD_LINE_RE = re.compile(
    r"(?im)^\s*(PLACE|SCENE|LIGHT|WEARING_B|WEARING|BEAT_B|BEAT|"
    r"EXPRESSION_B|EXPRESSION|FACE_B|FACE|FRAME|BG|BACKGROUND|LETTERING|TEXT|"
    r"ATMOSPHERE|MOOD|LOOK|STYLE)\s*[:：]\s*(.+?)\s*$"
)


def memory_prompt_blocks(session: dict[str, Any]) -> str:
    """Bond + memories + caught diary — Muse-only colour, fenced from the shot."""
    try:
        from . import shared as muse_service
    except Exception:
        logger.debug("[muse] muse.service unavailable for memory", exc_info=True)
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
            logger.debug("[muse] %s failed", name, exc_info=True)
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
        logger.debug("[muse] prop_age failed", exc_info=True)
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
        logger.debug("[muse] vitality_talk_extras failed", exc_info=True)
    return "\n\n".join(bits)


#: classic の女優条文が末尾に持つ出力書式の始まり。ここから後ろを落とす。
_CLASSIC_OUTPUT_MARK = "OUTPUT FORMAT — labelled blocks, nothing else:"


def _without_classic_output(base: str) -> str:
    """classic 側の出力書式を落とす。**声と人格と契約はそのまま残す。**

    見つからなければ何もしない —— classic 側の文言が変わっても、黙って
    人格まで削らないため。
    """
    text = str(base or "")
    i = text.find(_CLASSIC_OUTPUT_MARK)
    return text[:i].rstrip() if i > 0 else text


def _first_person(who: dict[str, Any], fallback: str) -> str:
    p = who.get("personality") or {}
    return str(
        who.get("first_person_ja") or p.get("first_person_ja") or fallback
    ).strip() or fallback


def w_output_block(
    lead: dict[str, Any], partner: dict[str, Any], *, locale: str,
) -> str:
    """W撮りのときだけ足す出力の形。**一人のときは一度も呼ばない。**（2026-09-10）

    総監督「Muse Refine で2人で会話しているときに会話分離ができてないですね。
    Muse Classic を参考に修正お願い」「内心を話すときもどちらかランダムで」。

    実機（`83d31174`）では全25行が主演名義の1行に潰れ、本文に私（みお）と
    アタシ（あさひ）が同居していた。分ける側（`identity.parse_duet_speakers`）は
    最初から呼ばれていて、**接頭辞を書けという指示だけが届いていなかった** ——
    それは classic の出力書式の末尾にしか無く、`_without_classic_output` が
    落としている（残す 7,588字に該当行 0本／落とす 2,718字に 4本）。

    **落とした 2,718字を戻さない。** 戻すと `OUTPUT FORMAT` が二つ・`MY_FEEL`
    が四つ並ぶ状態に逆戻りする（それを直したのが `_without_classic_output`）。
    ここでは Refine の書式に合わせて、W に要る規則だけを書く。

    声の条文（`--- MUSE A VOICE ---` / `--- MUSE B VOICE ---` と各々の一人称）は
    残っている側に入っているので、ここでは繰り返さず**名指しで結びつける**だけ。
    """
    a = str(lead.get("name_ja") or lead.get("name") or "A").strip()
    b = str(partner.get("name_ja") or partner.get("name") or "B").strip()
    ja = str(locale).startswith("ja")
    fp_a = _first_person(lead, "私" if ja else "I")
    fp_b = _first_person(partner, "私" if ja else "I")
    return f"""
W-MUSE (two in frame) — this REPLACES the solo shape of SAY / ASIDE above.

SAY: 2–6 lines of live conversation. You play BOTH {a} and {b}.
Prefix EVERY line with exactly `A:` or `B:` — never a name as the prefix:
A: <{a}'s line in her own voice>
B: <{b}'s line in her own voice>

ASIDE: **only ONE of them mutters this turn** — whoever the turn belongs to.
One line, prefixed `A:` or `B:` exactly as in SAY. Never both.

- A is {a}（一人称「{fp_a}」）. B is {b}（一人称「{fp_b}」）.
  Each uses her OWN first person for herself and NEVER says her own name in
  the third person. Do not put {fp_b} in A's mouth, or {fp_a} in B's.
- CONTRAST VOICES: if A's line and B's line could be swapped without anyone
  noticing, rewrite both.
- They talk to each other, not only to the Showrunner. React, tease, ride.
- CARD / PROPOSE stay ONE shared frame with two wardrobes:
  WEARING_B / BEAT_B / EXPRESSION_B are {b}'s, never {a}'s.
""".strip()


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
    # **門はここ一つ。** `muse.service.is_duet` は `mode` しか見ず、実機では
    # 一人の回も `mode: duet`（全109件中85件が相方なし）。相方の実体で切る。
    has_partner = bool(partner and str(partner.get("character_id") or "").strip())
    try:
        if has_partner:
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
        logger.exception("[muse] actress prompt failed")
        base = crew._voice_block(char, locale=locale_key)

    # **書式は一つでいい（2026-09-10）。** classic の女優条文は自前の出力書式
    # （SAY / ASIDE / CARD / PITCH / MY_FEEL）を末尾に持っていて、その上に
    # `REFINE_OUTPUT` を重ねていた。実測で `OUTPUT FORMAT` が2回、`MY_FEEL`
    # が4回、`PITCH:` が3回入っていた。
    #
    # 入力は 300 tok/s しか出ない（LLM が VRAM に 7.4GB しか載らない）ので、
    # 3,540字＝約1,180tok＝**毎ターン約4秒**を二度読みに払っていた。
    #
    # 読む側にも良くない —— 二つの書式が並ぶと、どちらに従うか決めさせる
    # ことになる。Refine が解釈するのは `REFINE_OUTPUT` のほうだけ。
    base = _without_classic_output(base)

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
        # 二人のときだけ、SAY / ASIDE の形を W 用に差し替える。**一人のときは
        # 空文字なので `parts` から落ちて、条文は一字も変わらない。**
        w_output_block(char, partner, locale=locale) if has_partner else "",
        mem,
        vit,
        opening,
        # **台帳と NOW は一箇所だけ（2026-09-10）。** ここと
        # `writer.actress_turn` の尾に二度入っていて、実測で 688字を余計に
        # 読ませていた（≈229tok・約0.8秒／ターン）。実体は writer 側の JSON に
        # 置く —— そちらは `ledger.for_model` を通っていて、一人のときに
        # 二人目の欄が出ない始末までできている。
        "SHOT TRUTH FOR THIS STUDIO (absolute — overrides chat vibes):\n"
        "The LEDGER and NOW below this contract are that truth. "
        "SAY may confirm these in her words. ASIDE must not inventory them. "
        "PROPOSE / CARD only when the picture should move. "
        "EXPRESSION is your performance — match the scene when face is empty "
        "or the shot mood moved without a named face.",
    ]
    return "\n\n".join(p for p in parts if p)


async def load_memory(db, session: dict[str, Any]) -> None:
    """Same sticky/diary/bond/caught load as original Muse open."""
    try:
        from . import shared as muse_service
        await muse_service._load_actress_memory(db, session)
    except Exception:
        logger.exception("[muse] load memory failed")
        session.setdefault("memories", [])
        session.setdefault("diary_memories", [])
        session.setdefault("bond", {})
        session.setdefault("caught", {})
        session.setdefault("showrunner_taste", {})
        session.setdefault("chemistry_notes", [])


async def consume_caught(db, session: dict[str, Any]) -> None:
    try:
        from . import shared as muse_service
        await muse_service._consume_caught(db, session)
    except Exception:
        logger.debug("[muse] consume_caught failed", exc_info=True)


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
        from . import shared as muse_service
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
        from . import shared as muse_service
        from ..runtime_config import get_runtime_config
        cfg = await get_runtime_config(db)
        session["_runtime_cfg"] = cfg
        return await muse_service._contract_check(
            ollama, session, text, cfg=cfg,
        )
    except Exception:
        logger.debug("[muse] contract_check failed", exc_info=True)
        return ""

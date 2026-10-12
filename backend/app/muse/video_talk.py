"""The video studio: talk a still into a video with an actress.

Two ways in (the Showrunner, 2026-10-10):

- a still from a Muse shoot — the talk carries on from that shoot (its theme
  and ledger are read in, and she remembers it when she was in it);
- any other photograph — an actress is called in to look at it with the
  Showrunner, and the two of them riff on what video it could become.

Each talk is its own record (`video_db`), not a Muse session: the session list,
chemistry and the lounge read sessions as shoots, and a talk is not a shoot.

The ideas come from `video_expand` (one call per kind, grounded in the
photograph). Her persona is in how she talks about them, not in what happens
in them: handed her whole persona, the idea calls turned every idea into a
re-enactment of her charm line (`private/muse_video_probe/runs/
compare-20261010-200140/report.md`). The people in a photograph are not her
unless the photograph says so.
"""
from __future__ import annotations

import logging
import random
import re
import time
import unicodedata
import uuid
from typing import Any

from ..characters import presets as presets_db
from . import chain, crew, identity, persona, session_db, video_db
from . import ledger as ledger_mod
from . import video_expand as ve
from .shared import images_by_sha

logger = logging.getLogger(__name__)


class VideoTalkError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _locale(talk: dict[str, Any]) -> str:
    return str((talk.get("inputs") or {}).get("locale") or "ja")


def _ja(talk: dict[str, Any]) -> bool:
    return _locale(talk).startswith("ja")


def _say(talk: dict[str, Any], ja: str, en: str) -> str:
    return ja if _ja(talk) else en


def _has_partner(talk: dict[str, Any]) -> bool:
    return bool(str((talk.get("partner_character") or {}).get("character_id") or "").strip())


def _cast_ids(talk: dict[str, Any]) -> set[str]:
    return {
        str((talk.get(k) or {}).get("character_id") or "")
        for k in ("character", "partner_character")
    } - {""}


def _name(char: dict[str, Any], *, ja: bool) -> str:
    return str((char.get("name_ja") if ja else char.get("name")) or char.get("name") or char.get("name_ja") or "")


# ── Who and what is in the photograph ────────────────────────────────────────

async def _character(db, character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise KeyError(character_id)
    return {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }


async def source_of(db, sha: str) -> dict[str, Any]:
    """What the picture's own record says: its Muse session and who is in it."""
    docs = await db.get_by_sha256s([sha])
    meta = docs[0] if docs else {}
    sid = str(meta.get("muse_session_id") or "")
    session = await session_db.load(db, sid) if sid else None
    return {
        "session": session,
        "session_id": sid if session else "",
        "character_id": str(meta.get("character_id") or ""),
        "partner_character_id": str(meta.get("partner_character_id") or ""),
    }


async def photo_people(db, src: dict[str, Any]) -> list[dict[str, str]]:
    """The people in the photograph, named when its record says who they are."""
    if src.get("session"):
        return ve.cast(src["session"])
    people = []
    for key in ("character_id", "partner_character_id"):
        cid = src.get(key)
        if cid:
            try:
                person = ve._person(await _character(db, cid))
            except KeyError:
                continue
            if person:
                people.append(person)
    return people


def shoot_context(session: dict[str, Any]) -> str:
    """What a Muse shoot was about, for ideas that carry on from it."""
    lines: list[str] = []
    theme = str((session.get("inputs") or {}).get("theme") or "").strip()
    if theme:
        lines.append(f"theme: {theme}")
    partner = session.get("partner_character") or {}
    led = ledger_mod.for_model(
        session.get("refine_ledger") or ledger_mod.blank(),
        partner=bool(str(partner.get("character_id") or "").strip()),
    )
    lines += [f"{k}: {v}" for k, v in led.items() if str(v or "").strip()]
    return "\n".join(lines)[:1500]


# ── Her side of the conversation ─────────────────────────────────────────────

# **Made together, in two stages** (the Showrunner, 2026-10-11: "it is only
# instructions to Muse, not a conversation — we cannot build it up while
# talking"). She used to carry the writers' cards to the director and stop
# at "let's go with that"; nothing was built between them. Now, as in the
# photo shoot (talk -> ledger -> board), there is something the two of them
# grow: first the direction, then the timetable, second by second.
#
#   direction — what the clip is about and how it feels. The writers' ideas are
#               her material; a clerk call notices when the director settles it.
#   timeline  — the timetable on screen. Each turn its keeper writes in what the
#               director asked for or accepted, and she talks over the new one.
#
# One job per call: she only talks; the clerks only judge; the keeper only
# keeps the timetable; the prompt is polished from the timetable on request.
# She once decided when to write as a field of her reply, and wrote on "make
# her smile at the end" — two runs of three (2026-10-11): judging is not her job.

_TALK = """You are {who}, at the studio with the Showrunner — the person who directs every shoot here. You are not shooting stills now: the two of you are making a short video ({seconds} seconds) together from a photograph, which the video model will use as its first frame.

{where}

You make it together, and making it should be fun — for you and for the Showrunner. You are not taking orders or reporting back. When the Showrunner says something, react first as yourself — pleased, doubtful, amused, teasing, caught off guard, whatever you really feel — and get to work after that. Bring ideas of your own, and push back when you would do it differently. Who you are shows in what you notice and what you want, and in how you say it.

{phase}

Answer in these labelled lines and nothing else:
SAY: what you say, in {lang} — as much as you would really say here, the way you talk (your VOICE decides: a girl of few words says little); never more than six sentences.{duet}
ASIDE: one short line you mutter to yourself, in {lang} — about something other than what you muttered before. Leave it out when nothing comes.
PITCH: when there is a real choice to make next, the two options as two short phrases in {lang}, separated by " | ". Leave it out otherwise."""

_PHASE_DIRECTION = (
    "Right now you are settling the direction: what this clip is about and how it should "
    "feel. Talk it through until the Showrunner settles it. The writers' notes below are "
    "material for you — when they fit, bring one or two of them in your own words, or "
    "bring your own idea instead. How it is shot is part of it too — a quiet camera, one "
    "shot that keeps moving round them, or cutting between angles. Do not go into "
    "second-by-second detail yet; that comes "
    "once the direction is settled."
)
_PHASE_TIMELINE = (
    "The direction is settled:\n{direction}\n\n"
    "Now the two of you are working through the timetable below, moment by moment, while "
    "looking at it. Most of what you say is about the moments on it — what happens at that "
    "second, how the face changes, how it ends — but not every line has to be: it is a talk, "
    "not a meeting. The camera is part of it too — whether it circles round them, follows, "
    "rises or closes in is yours and the Showrunner's to talk about. Suggest one change at a time. "
    "The timetable is kept by someone else: it changes only when the Showrunner asks for "
    "something or says yes to it."
)
_DUET = (
    "\nYou voice both {a} and {b}. Every SAY line starts with A: ({a}) or B: ({b}). "
    "They talk to each other as well as to the Showrunner."
)

_WHERE_OWN_SHOOT = (
    "The photograph came out of your own shoot with the Showrunner, and you "
    "remember it. The video carries on from that shoot."
)
_WHERE_OTHERS_SHOOT = (
    "The photograph came out of a shoot the studio did with {names}, not with you. "
    "The video carries on from that shoot; talk about them as the people in it."
)
_WHERE_YOU = (
    "The photograph is of you, though not from a shoot you did with the Showrunner."
)
_WHERE_PHOTO = (
    "The photograph is not from one of your shoots — the Showrunner brought it to "
    "show you. The people in it are not you; talk about them as the people in the "
    "photo."
)


def _where(talk: dict[str, Any]) -> str:
    in_photo = set(talk.get("people_ids") or [])
    with_her = bool(in_photo & _cast_ids(talk))
    if talk.get("source_session_id"):
        if with_her:
            return _WHERE_OWN_SHOOT
        names = ", ".join(p["name"] for p in talk.get("people") or []) or "other people"
        return _WHERE_OTHERS_SHOOT.format(names=names)
    return _WHERE_YOU if with_her else _WHERE_PHOTO


def _voices(talk: dict[str, Any]) -> str:
    key = "ja" if _ja(talk) else "en"
    seed = str(talk.get("talk_id") or "")
    lead = talk.get("character") or {}
    if not _has_partner(talk):
        return crew._voice_block(lead, locale=key, seed=seed)
    partner = talk.get("partner_character") or {}
    return (
        f"A = {_name(lead, ja=True)}\n{crew._voice_block(lead, locale=key, seed=seed)}\n\n"
        f"B = {_name(partner, ja=True)}\n{crew._voice_block(partner, locale=key, seed=seed)}"
    )


def _phase(talk: dict[str, Any]) -> str:
    return "timeline" if talk.get("phase") == "timeline" and talk.get("board") else "direction"


def talk_system(talk: dict[str, Any]) -> str:
    a = _name(talk.get("character") or {}, ja=True) or "A"
    b = _name(talk.get("partner_character") or {}, ja=True) or "B"
    duet = _has_partner(talk)
    timeline = _phase(talk) == "timeline"
    head = _TALK.format(
        who=f"{a} and {b}" if duet else a,
        seconds=talk.get("duration_s") or 5,
        where=_where(talk),
        phase=(
            _PHASE_TIMELINE.format(direction=talk.get("direction") or "")
            if timeline else _PHASE_DIRECTION
        ),
        lang="Japanese" if _ja(talk) else "English",
        duet=_DUET.format(a=a, b=b) if duet else "",
    )
    # Who she is, fenced: as influence on what she notices and wants, not as
    # words to repeat. Both of them in a duet.
    ja = _ja(talk)
    own = [own_block(talk.get("character"), ja=ja)]
    if duet:
        own.append(own_block(talk.get("partner_character"), ja=ja))
    return "\n\n".join(p for p in [head, _voices(talk), *own, relationship_blocks(talk)] if p)


def own_block(char: dict[str, Any] | None, *, ja: bool) -> str:
    """Who she is, for the talk — her work, her life, her taste.

    The talk once had the acting stage's block, headed "how she moves and
    reacts in this clip": read as being about the people in the photo, it
    left her own opinions generic — a deadpan night-shift worker talking
    in long, lyrical lines (Akari, 2026-10-11).
    """
    char = char or {}
    pers = char.get("personality") if isinstance(char.get("personality"), dict) else {}

    def pick(key: str) -> Any:
        return (pers.get(f"{key}_ja") if ja else None) or pers.get(key)

    inner = pick("inner")
    lines = [
        ("work", pick("occupation")),
        ("who you are", pick("summary")),
        ("what you feel and never say", " / ".join(inner) if isinstance(inner, list) else inner),
        ("likes", ", ".join(str(x) for x in pers.get("likes") or [])),
        ("dislikes", ", ".join(str(x) for x in pers.get("dislikes") or [])),
        ("dream", pick("dream")),
    ]
    body = "\n".join(f"{k}: {str(v).strip()}" for k, v in lines if str(v or "").strip())
    if not body:
        return ""
    name = _name(char, ja=True) or "you"
    return (
        f"** This is you, {name} — not the people in the photograph. Let it decide what "
        "you notice in the photo, what you like in an idea and what you would never do, "
        "and what from your own work or life it brings to mind; say that, in your own "
        "words. Do not recite these lines or explain yourself with them. **\n"
        f"</start REFERENCE ONLY>\n{body}\n</end REFERENCE ONLY>"
    )


def relationship_blocks(talk: dict[str, Any]) -> str:
    """How she stands with the Showrunner — and nothing she would retell.

    Not `persona.memory_prompt_blocks`: that carries the shoot-room's memory —
    diary bodies, recaps of past shoots, outings, and the one-off "the
    Showrunner read your diary" line, which a shoot consumes after she has
    raised it. Handed to every studio reply, Asahi answered "how should it
    look?" with "ふふっ……見ちゃいました？" and came back to her diary's
    木漏れ日の道 again two turns later (2026-10-11). A still's own shoot reaches
    the talk through `context` (its theme and ledger), not through her diary.
    """
    from . import shared

    blocks = [shared._bond_block(talk), shared._taste_block(talk)]
    if _has_partner(talk):
        blocks.append(shared._chemistry_block(talk))
    return "\n\n".join(b for b in blocks if b)[:1500]


def _notes_block(talk: dict[str, Any]) -> str:
    if not talk.get("ideas"):
        return "(none yet — the writers pitch once the Showrunner says what they want)"
    return "\n".join(
        f"- {i.get('title', '')}: {i.get('story', '')}"
        for i in talk.get("ideas") or [] if "beats" in i
    )


def _board_text(talk: dict[str, Any]) -> str:
    return "\n".join(
        f"{ve._clock(s['start'])}-{ve._clock(s['end'])} {s.get('title', '')}: {s['action']}"
        + (f" [camera: {s['camera']}]" if s.get("camera") else "")
        for s in talk.get("board") or []
    )


def _recent(talk: dict[str, Any], n: int = 10) -> str:
    rows = [r for r in talk.get("chat") or [] if r.get("role") != "system" and r.get("text")][-n:]

    def who(r: dict[str, Any]) -> str:
        if r.get("role") == "user":
            return "Showrunner"
        # Marked: listed like speech, three mutters about her knees read as
        # a topic she had been on, and she stayed on it (Hazuki, 2026-10-12).
        name = r.get("name") or "you"
        return f"{name} (muttering to herself)" if r.get("kind") == "aside" else name

    return "\n".join(f"{who(r)}: {r['text']}" for r in rows)


def talk_prompt(talk: dict[str, Any], line: str) -> str:
    context = (
        f"THE SHOOT IT CAME OUT OF:\n{talk['context']}\n\n" if talk.get("context") else ""
    )
    material = (
        f"THE TIMETABLE (what the screen shows now):\n{_board_text(talk)}\n\n"
        if _phase(talk) == "timeline" else
        f"THE WRITERS' NOTES:\n{_notes_block(talk)}\n\n"
    )
    # "Hazuki" in the reading came out as 羽月さん — a guessed kanji (Kanade,
    # 2026-10-11): she gets the names as the studio writes them.
    names = [
        f"{p['name']} = {p['name_ja']}" for p in talk.get("people") or []
        if p.get("name_ja") and _ja(talk)
    ]
    names_block = (
        "THE PEOPLE IN IT, as their names are written in Japanese:\n" + "\n".join(names) + "\n\n"
        if names else ""
    )
    return (
        f"PHOTOGRAPH (second 0):\n{talk.get('still', '')}\n\n"
        f"{names_block}{context}{material}"
        f"RECENT TALK:\n{_recent(talk) or '(none)'}\n\n"
        f"SHOWRUNNER:\n{line}"
    )


# A writers' key that slips into SAY reads as code (「[stay]みたいに」): it
# becomes that idea's title.
_KEY_IN_SAY = re.compile(r"\[(" + "|".join(ve.KINDS) + r")\]\s*", re.IGNORECASE)
# "A: (緒方 りん) うわ…" — the prefix already says who is talking.
_NAME_AFTER_PREFIX = re.compile(r"^([AB]:\s*)[(（][^)）]{1,20}[)）]\s*", re.MULTILINE)
# "…いくよ！ B:ふふ、張り切りすぎ" — the second speaker on the first one's line.
# "…入っちゃいそう。B: 待って" — or straight after the full stop.
_INLINE_SPEAKER = re.compile(r"(?:[ \t　]+|(?<=[。！？!?…」』）)]))(?=[AB][:：])")
# "**ASIDE:** ふふ" — the bold can close after the colon.
_QUOTED_LINE = re.compile(r"^((?:[AB][:：]\s*)?)「([^「」]*)」\s*$")
_FIELD = re.compile(r"^\s*\**\s*(SAY|ASIDE|PITCH|WRITE)\s*\**\s*[:：]\s*\**\s*(.*)$", re.IGNORECASE)


def parse_reply(raw: str) -> dict[str, str]:
    """Her labelled lines. SAY may run over several lines (a duet's A:/B:)."""
    out: dict[str, str] = {}
    # Lines before any label are what she said: in a duet gemma often starts
    # straight in with "A: …" and only labels the rest (2026-10-10, three turns
    # out of three in one run came back as "……" before this).
    field = "say"
    for line in (raw or "").splitlines():
        # "***" and "(The Showrunner looks at the screen…)": a script's
        # stage directions, written in (2026-10-12).
        if re.fullmatch(r"\s*[*_\-=]{3,}\s*", line) or re.match(r"\s*[(（]\s*The Showrunner\b", line):
            continue
        if m := _FIELD.match(line):
            field = m.group(1).lower()
            out[field] = m.group(2).strip()
        elif field in ("aside", "pitch") and line.strip():
            # ASIDE and PITCH are one line each. What follows is speech — the
            # duet's next line, with or without its "B:" ("ASIDE: …\nB: よーし",
            # "PITCH: a | b\n無理やりだなぁ"; the latter became part of a pitch
            # button, 2026-10-12) — unless it is bracketed like a mutter.
            if field == "aside" and re.match(r"\s*[(（]", line):
                out["aside"] = f"{out.get('aside', '')}\n{line.strip()}".strip()
            else:
                out["say"] = f"{out.get('say', '')}\n{line.strip()}".strip()
        elif line.strip():
            out[field] = f"{out.get(field, '')}\n{line.strip()}".strip()
    out["write"] = "yes" if (out.get("write") or "").strip().lower().startswith("y") else ""
    return out


def _clean(talk: dict[str, Any], text: str) -> str:
    titles = {i["kind"]: i.get("title", "") for i in talk.get("ideas") or []}
    text = _KEY_IN_SAY.sub(
        lambda m: f"「{titles[m.group(1).lower()]}」" if titles.get(m.group(1).lower()) else "",
        text or "",
    )
    # "…間違ってないって！<br>B: ふふ…" — gemma sometimes breaks lines with HTML.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # "…すごく好みです。 /" — a line-separator slash left at the end (seen on e4b).
    text = re.sub(r"[ \t　]+/[ \t　]*$", "", text, flags=re.MULTILINE)
    if _has_partner(talk):
        text = _INLINE_SPEAKER.sub("\n", text)
    text = _NAME_AFTER_PREFIX.sub(r"\1", text)
    # 「……クスッと、ですか。」 — a whole line quoted, as in a script (2026-10-12).
    return "\n".join(
        (m.group(1) + m.group(2)) if (m := _QUOTED_LINE.match(ln)) else ln
        for ln in text.split("\n")
    )


async def _call(ollama, talk: dict[str, Any], system: str, prompt: str) -> str:
    inputs = talk.get("inputs") or {}
    try:
        return await chain._call(
            ollama, system=system, prompt=prompt, model=str(inputs.get("model") or ""),
            images=None, num_ctx=inputs.get("num_ctx") or None, think=False,
        )
    except chain.ChainError:
        return ""


# A little something drawn for some of her replies. Asked in the brief to
# bring her own life in "now and then" or to be curious about the
# Showrunner, she did neither in 18 replies of 18 — but whatever field the
# answer has, she fills every time (a two-way choice on 18 of 18). So the
# brief stays general and the something is drawn per reply, about half the
# time and never the same twice running, the way the ideas draw a hook.
SPICES: dict[str, str] = {
    "life": "Something from your own life or work comes to mind here; tell it in a sentence or two, the way you would.",
    "curious": "Ask the Showrunner something about themselves that this makes you curious about.",
    "tease": "Tease the Showrunner a little, or make a small joke, the way you would.",
    "feeling": "Say honestly how this bit makes you feel, before anything else.",
}
_DUET_SPICE = "The two of you don't quite agree about this; have it out between you, then settle it."


def draw_spice(talk: dict[str, Any], rng: random.Random | None = None) -> str:
    """The key of this reply's something, or "" for a plain reply."""
    rng = rng or random
    keys = [k for k in [*SPICES, *(["duet"] if _has_partner(talk) else [])] if k != talk.get("last_spice")]
    pick = rng.choice(keys) if rng.random() < 0.5 else ""
    talk["last_spice"] = pick
    return pick


def _spice_line(key: str) -> str:
    text = _DUET_SPICE if key == "duet" else SPICES.get(key, "")
    return f"\n\nTHIS TIME, ALSO: {text}" if text else ""


# "わかりました。" once is her; at the head of reply after reply it reads like
# a clerk taking orders (Akari, three replies of four, 2026-10-12). Dropped
# only when her last reply opened the same way.
_ACK = re.compile(
    r"^([AB][:：]\s*)?[「…\s]*(わかりました|わかってます|わかっています|承知(いた)?しました|了解(です|しました)?)[。、！!…\s]*"
)


def _drop_repeated_ack(talk: dict[str, Any], say: str) -> str:
    last = next(
        (r["text"] for r in reversed(talk.get("chat") or [])
         if r.get("role") == "assistant" and not r.get("kind") and r.get("text")),
        "",
    )
    m = _ACK.match(say)
    if not (m and _ACK.match(last)) or not say[m.end():].strip():
        return say
    return (m.group(1) or "") + say[m.end():]


def _mutterer(talk: dict[str, Any], rng: random.Random | None = None) -> str:
    """In a duet, who mutters this time ("A" or "B"). Left to her, it was the
    lead every time (2026-10-12)."""
    if not _has_partner(talk):
        return ""
    talk["aside_by"] = (rng or random).choice("AB")
    who = talk["partner_character"] if talk["aside_by"] == "B" else talk.get("character") or {}
    return f"\n\nIf anyone mutters this time, it is {talk['aside_by']} ({_name(who, ja=True)}): start the ASIDE with {talk['aside_by']}:."


async def _reply(ollama, talk: dict[str, Any], line: str, *, spice: bool = False) -> dict[str, str]:
    prompt = talk_prompt(talk, line) + (_spice_line(draw_spice(talk)) if spice else "") + _mutterer(talk)
    raw = await _call(ollama, talk, talk_system(talk), prompt)
    reply = parse_reply(raw)
    reply["say"] = identity.sanitize_muse_say(_clean(talk, reply.get("say") or ""), locale=_locale(talk))
    reply["say"] = _drop_repeated_ack(talk, reply["say"])
    reply["aside"] = _clean(talk, reply.get("aside") or "").strip()
    if not reply["say"]:
        logger.warning("[muse.video] she said nothing: %r", (raw or "")[:300])
        reply["say"] = "……" if _ja(talk) else "..."
    return reply


def _row(talk: dict[str, Any], role: str, text: str, **meta: Any) -> None:
    row = {"role": role, "text": text, "at": time.time()}
    row.update({k: v for k, v in meta.items() if v not in (None, "", [])})
    talk.setdefault("chat", []).append(row)
    talk["chat"] = talk["chat"][-120:]


def _post_say(talk: dict[str, Any], say: str) -> None:
    lead = talk.get("character") or {}
    partner = talk.get("partner_character") or {}
    ja = _ja(talk)
    turns = None
    if _has_partner(talk):
        turns = identity.parse_duet_speakers(
            say, name_a=_name(lead, ja=True), name_b=_name(partner, ja=True), locale=_locale(talk),
        )
    if not turns:
        _row(talk, "assistant", say, name=_name(lead, ja=ja), character_id=lead.get("character_id"))
        return
    for t in turns:
        who = partner if str(t.get("speaker") or "A").upper() == "B" else lead
        text = str(t.get("text") or "").strip()
        if text:
            _row(talk, "assistant", text, name=_name(who, ja=ja), character_id=who.get("character_id"))


_QUOTES = {"「": "」", "『": "』", '"': '"', "'": "'", "“": "”"}


def _unquote(option: str) -> str:
    """Quotes around a whole option go; quotes inside it stay.

    Stripping every bracket off both ends turned 「覚悟」にする into
    覚悟」にする (Akari, 2026-10-11)."""
    o = option.strip()
    while len(o) >= 2 and _QUOTES.get(o[0]) == o[-1] and o[0] not in o[1:-1]:
        o = o[1:-1].strip()
    return o


def _unwrap_aside(text: str) -> str:
    """"(A: 嘘じゃないけど……)" — the brackets the screen adds, written in."""
    text = text.strip()
    if re.fullmatch(r"[(（].*[)）]", text, re.DOTALL) and not re.search(r"[)）].*[(（]", text[1:-1]):
        text = text[1:-1].strip()
    # "（重たいなぁ……。" — opened and never closed.
    if text[:1] in "(（" and not re.search(r"[)）]", text):
        text = text[1:].strip()
    return text


def _post_reply(talk: dict[str, Any], reply: dict[str, str]) -> None:
    """Her lines, then her mutter, then the fork she offers (as buttons)."""
    _post_say(talk, reply["say"])
    lead = talk.get("character") or {}
    partner = talk.get("partner_character") or {}
    aside = _unwrap_aside(reply.get("aside") or "")
    if aside:
        who = partner if talk.get("aside_by") == "B" and _has_partner(talk) else lead
        if m := re.match(r"^([AB])[:：]\s*", aside):
            who = partner if m.group(1) == "B" and _has_partner(talk) else lead
            aside = aside[m.end():]
        # "東野 はづき)" — the opening bracket lost: a name, not a mutter.
        names = {_name(c, ja=True).replace(" ", "") for c in (lead, partner) if c}
        aside = "\n".join(
            ln for ln in aside.split("\n")
            if re.sub(r"[\s()（）]", "", ln) not in names
        ).strip()
        aside = _unwrap_aside(aside)
        # 「……髪、変な方向に跳ねてないかな。」 — quoted inside the brackets
        # the screen draws anyway (2026-10-12).
        if m := _QUOTED_LINE.match(aside):
            aside = m.group(1) + m.group(2)
        # "(東野 はづき) ……" — her name in front, as on a script; once
        # misspelt ("(東野 はづキ)"), so any short bracketed tag goes.
        if m := re.match(r"^[(（]([^)）。、！？…]{1,12})[)）]\s*(?=\S)", aside):
            named = m.group(1).replace(" ", "")
            if named == _name(partner, ja=True).replace(" ", "") and _has_partner(talk):
                who = partner
            aside = aside[m.end():]
        _row(talk, "assistant", aside, kind="aside", name=_name(who, ja=_ja(talk)),
             character_id=who.get("character_id"))
    options = [_unquote(o) for o in (reply.get("pitch") or "").split("|")]
    options = [o for o in options if o][:2]
    if len(options) == 2:
        _row(talk, "assistant", "", kind="pitch", options=options)


def model_inputs(cfg: dict[str, Any], locale: str, model: str = "") -> dict[str, Any]:
    """The studio's chosen model, else the admin screen's Muse default.

    The chosen one reads the picture too: the studio offers only models that
    can see. The admin default was a small e4b while the shoots ran on the 26B
    (2026-10-11), so the screen chooses rather than inheriting it silently.
    """
    chosen = model.strip()
    fallback = str(cfg.get("muse_model") or "").strip() or str(cfg.get("vlm_model") or "").strip()
    return {
        "locale": locale,
        "model": chosen or fallback,
        "vision_model": chosen or str(cfg.get("muse_vision_model") or "").strip() or fallback,
        "num_ctx": int(cfg.get("ollama_num_ctx") or 0) or None,
    }


# ── The clerk: has the director settled the direction? ───────────────────────

_SETTLE_SYSTEM = """You keep the notes of a talk between a director (the Showrunner) and an actress who are planning a short video together. Read the talk. Has the Showrunner settled the direction — what the clip is about and how it should feel — so that the two of them can go on to work out the timing? Taking up one of the actress's suggestions counts. Asking a question, or still weighing options, does not.

Answer in three lines and nothing else:
SETTLED: yes or no
STYLE: how it is to be shot — calm (one shot, a quiet camera), flow (one shot, the camera moving round them all the time) or cuts (cut between two or three angles). Take what the talk said; when it said nothing, the one that suits the clip.
DIRECTION: when yes, the settled direction in two or three sentences in {lang}, keeping the Showrunner's own words where they gave them. What the Showrunner wants is the heart of the clip and comes first; what was added on top of it comes after, as how it is done. Write it as a plain note about the clip, in nobody's voice — not as the actress or the Showrunner talking. When no, leave it empty."""

_FORCE_SETTLE = (
    "\n\nThe Showrunner has pressed the button to settle the direction now: answer "
    "SETTLED: yes, and write the direction as the talk stands."
)


async def settle_direction(ollama, talk: dict[str, Any], *, force: bool = False) -> str:
    """The settled direction, or "" when the talk has not settled it yet.

    The heart first: "eat a burger as if it is delicious" was settled along
    with her prank and leaves on an equal footing, and the timetable opened on
    the prank and reached the bite at 8 s of 10 (Kanade, 2026-10-11)."""
    system = _SETTLE_SYSTEM.format(lang="Japanese" if _ja(talk) else "English")
    # The note typed at the start is the Showrunner's first wish. Left out,
    # a talk settled on the note alone came back with no direction, and the
    # note's bullet ("- ベッドで…") became it (2026-10-12).
    note = (talk.get("short") or "").strip()
    prompt = (
        (f"THE SHOWRUNNER'S NOTE, written before the talk:\n{note}\n\n" if note else "")
        + f"THE WRITERS' NOTES:\n{_notes_block(talk)}\n\n"
        f"THE TALK:\n{_recent(talk, 16) or '(none)'}"
        + (_FORCE_SETTLE if force else "")
    )
    raw = await _call(ollama, talk, system, prompt)
    style = re.search(r"STYLE\s*\**\s*[:：]\s*\**\s*(calm|flow|cuts)", raw or "", re.IGNORECASE)
    settled = re.search(r"SETTLED\s*[:：]\s*(\w+)", raw or "", re.IGNORECASE)
    direction = re.search(r"DIRECTION\s*[:：]\s*(.+)", raw or "", re.IGNORECASE | re.DOTALL)
    text = (direction.group(1).strip() if direction else "").strip()
    if (settled and settled.group(1).lower().startswith("y")) or force:
        # A style the Showrunner picked on screen stands.
        if style and talk.get("style_by") != "user":
            talk["style"] = style.group(1).lower()
        text = re.split(r"\n\s*\**\s*STYLE\s*[:：]", text, flags=re.IGNORECASE)[0].strip()
        if not text:
            # The Showrunner's own words, as a note rather than a list.
            text = " ".join(ln.lstrip("- ").strip() for ln in intent_of(talk).splitlines() if ln.strip())
        return text
    return ""



_ASKS_WRITE_SYSTEM = """You keep the notes of a talk between a director (the Showrunner) and an actress who are planning a short video together. Read the Showrunner's latest line. Does it ask to finish now and write the video prompt from the timetable? Asking for a change, agreeing to a suggestion, or asking a question does not — only a request to write it up now.

Answer with one word and nothing else: yes or no"""


async def asks_to_write(ollama, talk: dict[str, Any], message: str) -> bool:
    raw = await _call(ollama, talk, _ASKS_WRITE_SYSTEM, (
        f"THE TALK SO FAR:\n{_recent(talk, 6) or '(none)'}\n\n"
        f"THE SHOWRUNNER'S LATEST LINE:\n{message}"
    ))
    return bool(re.match(r"\W*yes\b", raw or "", re.IGNORECASE))


# ── The moves ────────────────────────────────────────────────────────────────
async def create(
    db, ollama, *, sha: str, character_id: str, partner_id: str = "",
    duration_s: int = 5, short: str = "", locale: str = "ja",
    cfg: dict[str, Any] | None = None, reuse: dict[str, Any] | None = None,
    model: str = "", opener: bool = True,
) -> dict[str, Any]:
    """Bring a photograph to the table: look, and let her open the talk.

    `reuse` is an earlier talk about the same picture: its reading is kept, so
    calling a different actress in does not look at the picture again.
    `opener=False` is "another video from this still": she has already seen
    the picture, so the talk starts blank and waits for the Showrunner's first
    line — no model call at all when the reading is reused. Starting one used
    to run her opening line for 10–25 s behind the old talk still on screen.
    """
    inputs = model_inputs(cfg or {}, locale, model)
    talk: dict[str, Any] = {
        "talk_id": str(uuid.uuid4()), "created_at": time.time(), "sha256": sha,
        "inputs": inputs, "duration_s": duration_s, "short": short.strip(),
        "chat": [], "ideas": [], "prompts": [],
        "phase": "direction", "direction": "", "board": [],
    }
    if not inputs["model"]:
        raise VideoTalkError(_say(
            talk, "管理画面で Muse のモデルを決めてください", "Set the Muse model on the admin screen",
        ))
    try:
        talk["character"] = await _character(db, character_id)
        talk["partner_character"] = (
            await _character(db, partner_id) if partner_id and partner_id != character_id else {}
        )
    except KeyError:
        raise VideoTalkError(_say(talk, "女優が見つかりません", "That actress is not on the roster"))
    src = await source_of(db, sha)
    people = await photo_people(db, src)
    shoot = src.get("session")
    ids = (
        [str((shoot.get(k) or {}).get("character_id") or "") for k in ("character", "partner_character")]
        if shoot else [src["character_id"], src["partner_character_id"]]
    )
    talk.update({
        "source_session_id": src["session_id"],
        "people": people,
        "people_ids": [c for c in ids if c],
        "context": shoot_context(shoot) if shoot else "",
    })
    if reuse and reuse.get("sha256") == sha and reuse.get("still"):
        talk["still"], talk["blind"] = reuse["still"], bool(reuse.get("blind"))
    else:
        images = await images_by_sha(db, [sha])
        if not images:
            raise VideoTalkError(_say(talk, "画像を読めません", "Could not read the image"))
        fallback = str(((src.get("session") or {}).get("shoot") or {}).get("prompt") or "")
        talk["still"], talk["blind"] = await ve.look(
            ollama, image=images[0], people=people, model=inputs["vision_model"],
            num_ctx=inputs["num_ctx"], fallback=fallback,
        )
    if not talk["still"]:
        raise VideoTalkError(_say(
            talk, "画像を読めるモデルが要ります（vision_model）",
            "An image-capable model is needed (vision_model)",
        ))
    if talk["blind"]:
        _row(talk, "system", _say(
            talk,
            "このモデルは絵を読めなかったので、撮影のプロンプトから考えました。画像を読めるモデルを管理画面で指定してください。",
            "This model could not read the picture, so the ideas start from the shoot's prompt. Set an image-capable model on the admin screen.",
        ), kind="blind")
    # Her standing with the Showrunner (bond, his taste, a duet's chemistry)
    # colours how she talks; see `relationship_blocks` for what is left out.
    # The loader reads who she is from the inputs, as a session carries them.
    inputs["character_id"] = character_id
    inputs["partner_preset"] = talk["partner_character"].get("character_id", "")
    await persona.load_memory(db, talk)
    if not opener:
        _row(talk, "system", _say(
            talk, "🆕 新しい動画の相談です。どんな動画にしたいか話しかけてください",
            "🆕 A new video from this still. Say what kind of video you want",
        ), kind="new")
    elif talk.get("short"):
        await _pitch(ollama, talk, why="open")
    else:
        await _ask_first(ollama, talk)
    await video_db.save(db, talk)
    return talk


_PITCH_LINES = {
    "open": "(The Showrunner has just put the photograph down, with the note above for what "
            "they want. React to the photograph and start working out the direction with them.)",
    "wish": "(The Showrunner has just said what they want — their line is the last one in the "
            "talk. Answer it, and start working out the direction with them.)",
    "fresh": "(The Showrunner asked the writers for fresh notes; they are the ones listed now. "
             "React to them.)",
}


async def _ideas(ollama, talk: dict[str, Any]) -> None:
    inputs = talk["inputs"]
    talk["ideas"] = await ve.ideas(
        ollama, still=talk["still"], people=talk.get("people") or [], short=intent_of(talk),
        duration_s=int(talk.get("duration_s") or 5), locale=_locale(talk),
        model=inputs["model"], num_ctx=inputs["num_ctx"], context=talk.get("context", ""),
    )


async def _pitch(ollama, talk: dict[str, Any], *, why: str) -> None:
    """The writers' notes for what the director wants so far, then her take."""
    await _ideas(ollama, talk)
    note = f"The Showrunner's note: {talk['short']}\n" if talk.get("short") else ""
    _post_reply(talk, await _reply(ollama, talk, note + _PITCH_LINES[why]))


async def _ask_first(ollama, talk: dict[str, Any]) -> None:
    """No wish yet: she reacts to the photograph and asks what they want.

    Pitched before the director had said anything, the ideas were written
    blind — "sleepy and cute" came back as a magic meadow (Asahi, 2026-10-11).
    The writers wait for the first thing the director says (or the note).
    """
    _post_reply(talk, await _reply(ollama, talk, (
        "(The Showrunner has just put the photograph down. React to it in a line or two, "
        "then ask what kind of video they want to make of it.)"
    )))


async def repitch(db, ollama, talk: dict[str, Any]) -> dict[str, Any]:
    """Fresh notes from the writers, for the direction talk."""
    _row(talk, "system", _say(talk, "新しい案を頼みました", "Asked for fresh ideas"), kind="repitch")
    talk["phase"] = "direction"
    await _pitch(ollama, talk, why="fresh")
    await video_db.save(db, talk)
    return talk


def _context(talk: dict[str, Any], *, persona: bool = False) -> str:
    return ve.write_context(
        still=talk.get("still", ""), people=talk.get("people") or [],
        duration_s=int(talk.get("duration_s") or 5), intent=intent_of(talk),
        direction=talk.get("direction", ""), persona=persona_of(talk) if persona else "",
        avoid=talk.get("avoid", ""), style=style_of(talk),
    )


def style_of(talk: dict[str, Any]) -> str:
    """How the clip is shot; talks from before styles were quiet ones."""
    return talk.get("style") if talk.get("style") in ve.STYLES else "calm"


async def set_style(db, talk: dict[str, Any], style: str) -> dict[str, Any]:
    """The Showrunner's pick on screen. The timetable stays; the polish follows it."""
    if style not in ve.STYLES:
        raise VideoTalkError(f"unknown style: {style}")
    talk["style"], talk["style_by"] = style, "user"
    names = {"calm": ("じっくり", "Quiet"), "flow": ("回り込み", "Moving"), "cuts": ("カット割り", "Cuts")}[style]
    _row(talk, "system", _say(talk, f"🎥 撮り方：{names[0]}", f"🎥 Shot as: {names[1]}"), kind="style")
    await video_db.save(db, talk)
    return talk


async def _start_timeline(ollama, talk: dict[str, Any], direction: str) -> None:
    """The direction is settled: draft the timetable and move on to it."""
    inputs = talk["inputs"]
    talk["direction"] = direction
    try:
        talk["board"], _ = await ve.draft(
            ollama, context=_context(talk), duration_s=int(talk.get("duration_s") or 5),
            model=inputs["model"], num_ctx=inputs["num_ctx"], locale=_locale(talk),
        )
    except ve.VideoError as exc:
        talk["last_write_failure"] = {"at": time.time(), "idea": "board", "raw": (exc.raw or "")[:3000]}
        raise VideoTalkError(exc.message) from exc
    talk["phase"] = "timeline"
    _row(talk, "system", _say(
        talk, f"🎯 方針が決まりました：{direction}\n🗒 時間割を作りました",
        f"🎯 Direction settled: {direction}\n🗒 Drafted the timetable",
    ), kind="settled")


async def chat(db, ollama, talk: dict[str, Any], message: str) -> dict[str, Any]:
    """One line from the Showrunner."""
    _row(talk, "user", message)
    try:
        if _phase(talk) == "direction":
            await _direction_turn(ollama, talk, message)
        else:
            await _timeline_turn(ollama, talk, message)
    except VideoTalkError as exc:
        # Said in the talk, not lost with it.
        _row(talk, "system", exc.message, kind="write_failed")
    await video_db.save(db, talk)
    return talk


async def _direction_turn(ollama, talk: dict[str, Any], message: str) -> None:
    if not talk.get("ideas"):
        # The first thing the director says is what they want: the writers
        # pitch with it, and she takes it from there.
        await _pitch(ollama, talk, why="wish")
        return
    direction = await settle_direction(ollama, talk)
    if not direction:
        _post_reply(talk, await _reply(ollama, talk, message, spice=True))
        return
    await _start_timeline(ollama, talk, direction)
    _post_reply(talk, await _reply(ollama, talk, (
        f"{message}\n(The direction is settled and the timetable is on the screen now. "
        "Say so in your own way, and bring up the first moment you would work on.)"
    )))


def _same_board(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    def key(segs):
        return [
            (s.get("start"), s.get("end"), " ".join(str(s.get("action") or "").split()),
             " ".join(str(s.get("camera") or "").split()))
            for s in segs
        ]
    return key(a) == key(b)


async def _timeline_turn(ollama, talk: dict[str, Any], message: str) -> None:
    inputs = talk["inputs"]
    her_last = next(
        (r["text"] for r in reversed(talk.get("chat") or [])
         if r.get("role") == "assistant" and r.get("text") and r.get("kind") != "aside"),
        "",
    )
    wants_write = await asks_to_write(ollama, talk, message)
    exchange = f"What the director just said:\n{message}" + (
        f"\n\nWhat the actress had said just before (it goes in only if the director "
        f"says yes to it):\n{her_last}" if her_last else ""
    )
    board, raw = await ve.keep_board(
        ollama, context=_context(talk), segments=talk["board"], exchange=exchange,
        duration_s=int(talk.get("duration_s") or 5), model=inputs["model"], num_ctx=inputs["num_ctx"],
        avoid=talk.get("avoid", ""),
    )
    avoid = ve.parse_avoid(raw)
    if board is not None and avoid is not None and avoid != talk.get("avoid", ""):
        talk["avoid"] = avoid
    if board and not _same_board(board, talk["board"]):
        talk["board"] = board
        _row(talk, "system", _say(talk, "🗒 時間割を書き直しました", "🗒 Updated the timetable"), kind="board")
    if not wants_write:
        _post_reply(talk, await _reply(ollama, talk, message, spice=True))
        return
    await _write(ollama, talk)
    reply = await _reply(ollama, talk, (
        f"{message}\n(The prompt has just been written from this timetable and is on the "
        "screen. Say so briefly in your own way.)"
    ))
    reply["pitch"] = ""  # nothing left to choose on this one
    _post_reply(talk, reply)


async def settle(db, ollama, talk: dict[str, Any]) -> dict[str, Any]:
    """The button: settle the direction as the talk stands, and draft the timetable."""
    if not talk.get("ideas") and not intent_of(talk):
        raise VideoTalkError(_say(
            talk, "どんな動画にしたいか、まずひとこと伝えてください",
            "Say what kind of video you want first",
        ))
    direction = await settle_direction(ollama, talk, force=True)
    await _start_timeline(ollama, talk, direction)
    _post_reply(talk, await _reply(ollama, talk, (
        "(The Showrunner settled the direction and the timetable is on the screen now. "
        "Say so in your own way, and bring up the first moment you would work on.)"
    )))
    await video_db.save(db, talk)
    return talk


async def back_to_direction(db, talk: dict[str, Any]) -> dict[str, Any]:
    """Reopen the direction. The timetable stays until a new one is drafted."""
    talk["phase"] = "direction"
    _row(talk, "system", _say(talk, "🎯 方針の相談に戻りました", "🎯 Back to the direction"), kind="reopened")
    await video_db.save(db, talk)
    return talk


async def write(db, ollama, talk: dict[str, Any]) -> dict[str, Any]:
    """The button: polish the timetable into the prompt."""
    try:
        await _write(ollama, talk)
    except VideoTalkError:
        await video_db.save(db, talk)  # keeps `last_write_failure` to read later
        raise
    await video_db.save(db, talk)
    return talk


def intent_of(talk: dict[str, Any]) -> str:
    """What the Showrunner asked for: the note, then their lines in the talk.

    The writer once had only the idea, and a talk that had settled on "sleepy
    and cute" came back as a ghost story (Asahi, 2026-10-11).
    """
    lines = [talk.get("short", "")] + [
        r.get("text", "") for r in talk.get("chat") or [] if r.get("role") == "user"
    ][-6:]
    return "\n".join(f"- {x.strip()}" for x in lines if str(x or "").strip())


def persona_of(talk: dict[str, Any]) -> str:
    """Her (and her partner's) persona, fenced, for the acting stage — only for
    whoever of the cast is actually in the picture. A photograph of someone
    else gets none: those people are not her."""
    in_photo = set(talk.get("people_ids") or [])
    blocks = [
        ve.reference_block(talk.get(k))
        for k in ("character", "partner_character")
        if str((talk.get(k) or {}).get("character_id") or "") in in_photo
    ]
    return "\n\n".join(b for b in blocks if b)


async def _write(ollama, talk: dict[str, Any]) -> None:
    """Polish the timetable (drafting one first if the talk never settled)."""
    inputs = talk["inputs"]
    if not talk.get("board"):
        await _start_timeline(ollama, talk, await settle_direction(ollama, talk, force=True))
    title = talk["board"][0].get("title", "") if talk.get("board") else ""
    try:
        out = await ve.write(
            ollama, still=talk.get("still", ""), people=talk.get("people") or [], idea={},
            duration_s=int(talk.get("duration_s") or 5), model=inputs["model"],
            num_ctx=inputs["num_ctx"], locale=_locale(talk), intent=intent_of(talk),
            persona=persona_of(talk), direction=talk.get("direction", ""),
            segments=talk["board"], avoid=talk.get("avoid", ""), style=style_of(talk),
        )
    except ve.VideoError as exc:
        # What the model returned, kept on the record: the screen only says it failed.
        talk["last_write_failure"] = {"at": time.time(), "idea": "write", "raw": (exc.raw or "")[:3000]}
        raise VideoTalkError(exc.message) from exc
    label = (talk.get("direction") or title or "").strip().splitlines()[0][:40] if (talk.get("direction") or title) else ""
    talk.setdefault("prompts", []).append({
        "title": label, "prompt": out["prompt"],
        "instrument": out.get("instrument", ""), "duration_s": talk.get("duration_s"),
        "at": time.time(),
        # What each writing stage returned, to read when a prompt disappoints.
        "stages": {k: str(v)[:4000] for k, v in (out.get("stages") or {}).items()},
    })
    _row(talk, "system", _say(talk, "📝 撮影文にしました", "📝 Wrote the prompt"), kind="written")


def _who(char: dict[str, Any]) -> dict[str, Any]:
    if not char:
        return {}
    board = char.get("board") or {}
    return {
        "character_id": char.get("character_id", ""),
        "name": char.get("name", ""), "name_ja": char.get("name_ja", ""),
        "portrait": board.get("portrait") or board.get("sheet") or "",
    }


def view(talk: dict[str, Any]) -> dict[str, Any]:
    """What the studio screen needs."""
    return {
        "talk_id": talk.get("talk_id"),
        "sha256": talk.get("sha256"),
        "created_at": talk.get("created_at"),
        "updated_at": talk.get("updated_at"),
        "duration_s": talk.get("duration_s", 5),
        "short": talk.get("short", ""),
        "model": (talk.get("inputs") or {}).get("model", ""),
        "character": _who(talk.get("character") or {}),
        "partner_character": _who(talk.get("partner_character") or {}),
        "source_session_id": talk.get("source_session_id", ""),
        "people": [p.get("name") for p in talk.get("people") or []],
        "blind": bool(talk.get("blind")),
        "ideas": [
            {k: i[k] for k in ("kind", "label", "title", "story", "error", "revised") if k in i}
            for i in talk.get("ideas") or []
        ],
        "phase": _phase(talk),
        "direction": talk.get("direction", ""),
        "avoid": talk.get("avoid", ""),
        "style": style_of(talk),
        "board": [
            {k: s.get(k) for k in ("start", "end", "title", "action", "camera")}
            for s in talk.get("board") or []
        ],
        "prompts": [
            {k: v for k, v in p.items() if k != "stages"} for p in talk.get("prompts") or []
        ],
        "chat": list(talk.get("chat") or [])[-60:],
    }


# ── Text search ─────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    # Full-width and half-width, upper and lower case all match each other.
    return unicodedata.normalize("NFKC", str(text or "")).lower()


def _snippet(text: str, term: str, width: int = 40) -> str:
    flat = " ".join(str(text or "").split())
    at = _norm(flat).find(term)
    if at < 0:
        return flat[: width * 2]
    start, end = max(0, at - width), min(len(flat), at + len(term) + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def _fields(talk: dict[str, Any]) -> list[dict[str, Any]]:
    """Every searchable piece of a talk, with where it came from."""
    out: list[dict[str, Any]] = []
    for i, p in enumerate(talk.get("prompts") or []):
        out.append({"field": "prompt", "text": p.get("prompt", ""), "title": p.get("title", ""), "prompt_index": i})
        out.append({"field": "title", "text": p.get("title", ""), "title": p.get("title", ""), "prompt_index": i})
    for idea in talk.get("ideas") or []:
        out.append({"field": "title", "text": idea.get("title", ""), "title": idea.get("title", "")})
        out.append({"field": "story", "text": idea.get("story", ""), "title": idea.get("title", "")})
    out.append({"field": "story", "text": talk.get("direction", "")})
    for seg in talk.get("board") or []:
        out.append({"field": "story", "text": seg.get("action", ""), "title": seg.get("title", "")})
    for key in ("character", "partner_character"):
        c = talk.get(key) or {}
        out.append({"field": "name", "text": f"{c.get('name', '')} {c.get('name_ja', '')}"})
    out.append({"field": "note", "text": talk.get("short", "")})
    for row in talk.get("chat") or []:
        if row.get("role") in ("user", "assistant"):
            out.append({"field": "chat", "text": row.get("text", ""), "name": row.get("name", "")})
    return [f for f in out if str(f["text"]).strip()]


# Which hit to show first when a talk matches in several places.
_FIELD_ORDER = {"prompt": 0, "title": 1, "story": 2, "note": 3, "name": 4, "chat": 5}


def search(talks: list[dict[str, Any]], query: str, *, limit: int = 30) -> list[dict[str, Any]]:
    """Talks containing every word of the query, newest first.

    Words are split on spaces (full-width too). Each result carries its hits —
    which field, a snippet around the word, and for a prompt its index — so
    the screen can say where it matched and open that prompt.
    """
    terms = [t for t in _norm(query).split() if t]
    if not terms:
        return []
    out = []
    for talk in talks:
        fields = _fields(talk)
        normed = [_norm(f["text"]) for f in fields]
        whole = "\n".join(normed)
        if not all(t in whole for t in terms):
            continue
        hits = []
        for f, n in zip(fields, normed):
            term = next((t for t in terms if t in n), None)
            if term is None:
                continue
            hit = {"field": f["field"], "snippet": _snippet(f["text"], term)}
            for k in ("title", "prompt_index", "name"):
                if f.get(k) not in (None, ""):
                    hit[k] = f[k]
            hits.append(hit)
        hits.sort(key=lambda h: _FIELD_ORDER.get(h["field"], 9))
        out.append({**summary(talk), "hits": hits[:4]})
    out.sort(key=lambda r: r.get("updated_at") or 0.0, reverse=True)
    return out[:limit]


def summary(row: dict[str, Any]) -> dict[str, Any]:
    """One line of the talk list."""
    return {
        "talk_id": row.get("talk_id"),
        "sha256": row.get("sha256"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
        "character": _who(row.get("character") or {}),
        "partner_character": _who(row.get("partner_character") or {}),
        "chosen_title": row.get("chosen_title", ""),
        "prompts_count": row.get("prompts_count", 0),
    }

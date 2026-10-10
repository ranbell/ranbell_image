"""Muse's video mode: talk a still into a video with the actress.

Two ways in (the Showrunner, 2026-10-10):

- a still from her own shoot — she carries on from that shoot;
- any other photograph — she is called in to look at it with the Showrunner,
  and the two of them riff on what video it could become.

The ideas come from `video_expand` (one call per kind, grounded in the
photograph). Her persona is in how she talks about them, not in what happens
in them: handed her whole persona, the idea calls turned every idea into a
re-enactment of her charm line (`private/muse_video_probe/runs/
compare-20261010-200140/report.md`). In a photograph that is not her shoot,
the people in it are not her either.

The session keeps `video`: the still, who is in it, the ideas, the one being
talked about, and the last prompt written. The chat log carries her lines,
the idea cards (`meta.kind == "video_ideas"`) and the written prompt
(`meta.kind == "video_prompt"`).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..characters import presets as presets_db
from . import chain, crew, identity, persona, session_db, talk
from . import ledger as ledger_mod
from . import video_expand as ve
from .ctx import refine_num_ctx
from .shared import _vision_model, images_by_sha

logger = logging.getLogger(__name__)


class VideoTalkError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _locale(session: dict[str, Any]) -> str:
    return str(_inputs(session).get("locale") or "ja")


def _ja(session: dict[str, Any]) -> bool:
    return _locale(session).startswith("ja")


def _say(session: dict[str, Any], ja: str, en: str) -> str:
    return ja if _ja(session) else en


def state(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("video") or {}


def _has_partner(session: dict[str, Any]) -> bool:
    partner = session.get("partner_character") or {}
    return bool(str(partner.get("character_id") or "").strip())


def _lead_name(session: dict[str, Any]) -> str:
    lead = session.get("character") or {}
    return str(lead.get("name_ja") or lead.get("name") or "Muse")


# ── Where the photograph came from ───────────────────────────────────────────

async def photo_people(
    db, session: dict[str, Any], sha: str,
) -> tuple[str, list[dict[str, str]]]:
    """("shoot", her cast) for a still from this session; ("photo", …) otherwise.

    A photograph from elsewhere may still record who was in it (`character_id`
    on images rendered for a character); then those people are named. Otherwise
    nobody is, and the ideas describe them by how they look.
    """
    docs = await db.get_by_sha256s([sha]) if db is not None else []
    meta = docs[0] if docs else {}
    sid = str(session.get("session_id") or "")
    board = [
        str(i.get("image_id") or "") for i in (session.get("board") or {}).get("images") or []
        if isinstance(i, dict)
    ]
    if (sid and meta.get("muse_session_id") == sid) or sha in ve.shoot_shas(session) or sha in board:
        return "shoot", ve.cast(session)
    people: list[dict[str, str]] = []
    for key in ("character_id", "partner_character_id"):
        cid = str(meta.get(key) or "").strip()
        if not cid:
            continue
        preset = await presets_db.get_preset(db, cid)
        if preset:
            person = ve._person({
                **presets_db.preset_to_character(preset),
                "name": preset.get("name") or preset.get("name_ja") or "",
            })
            if person:
                people.append(person)
    return "photo", people


def shoot_context(session: dict[str, Any]) -> str:
    """What the shoot was about, for ideas that carry on from it."""
    lines: list[str] = []
    theme = str(_inputs(session).get("theme") or "").strip()
    if theme:
        lines.append(f"theme: {theme}")
    led = ledger_mod.for_model(
        session.get("refine_ledger") or ledger_mod.blank(), partner=_has_partner(session),
    )
    lines += [f"{k}: {v}" for k, v in led.items() if str(v or "").strip()]
    return "\n".join(lines)[:1500]


# ── Her side of the conversation ─────────────────────────────────────────────

_TALK = """You are {who}, at the studio with the Showrunner — the person who directs every shoot here. You are not shooting stills now. The two of you have a photograph and are deciding what short video ({seconds} seconds) it could become; the video model will start from that photograph as its first frame.

{where}

The studio's writers have pitched ideas, each a different kind of scene, listed below. The key in brackets is for the PICK line only; in SAY call an idea by what happens in it, since the Showrunner never sees the keys. Your part is the conversation: talk about the photograph and the ideas the way you would, say which one you would want and why, answer what the Showrunner says, and push back when you disagree. When the Showrunner asks for a change, change that idea. The ideas belong to the writers — who you are shows in how you talk about them, not in turning them into scenes about yourself.

Answer in these labelled lines and nothing else:
SAY: what you say, one to four short lines, in {lang}.{duet}
PICK: the key of the idea the conversation is on now. Leave it out when none is.
STORY: only when you changed an idea — two or three sentences in {lang} on what the viewer now sees.
BEATS: only when you changed an idea — the whole changed idea as plain physical actions in English, separated by " / ".
WRITE: yes — only when the Showrunner's latest line accepts an idea as it stands. Asking for a change, or asking which one you like, is not that: change it, show it, and wait for the answer."""

_DUET = (
    "\nYou voice both {a} and {b}. Every SAY line starts with A: ({a}) or B: ({b}). "
    "They talk to each other as well as to the Showrunner."
)

_WHERE_SHOOT = (
    "The photograph came out of your own shoot with the Showrunner, and you "
    "remember it. The video carries on from that shoot."
)
_WHERE_PHOTO = (
    "The photograph is not from one of your shoots — the Showrunner brought it to "
    "show you. The people in it are not you; talk about them as the people in the "
    "photo."
)


def _voices(session: dict[str, Any]) -> str:
    key = "en" if not _ja(session) else "ja"
    seed = str(session.get("session_id") or "")
    lead = session.get("character") or {}
    blocks = [crew._voice_block(lead, locale=key, seed=seed)]
    if _has_partner(session):
        partner = session.get("partner_character") or {}
        blocks = [
            f"A = {lead.get('name_ja') or lead.get('name') or 'A'}\n{blocks[0]}",
            f"B = {partner.get('name_ja') or partner.get('name') or 'B'}\n"
            + crew._voice_block(partner, locale=key, seed=seed),
        ]
    return "\n\n".join(blocks)


def talk_system(session: dict[str, Any]) -> str:
    v = state(session)
    lead = session.get("character") or {}
    partner = session.get("partner_character") or {}
    a = str(lead.get("name_ja") or lead.get("name") or "A")
    b = str(partner.get("name_ja") or partner.get("name") or "B")
    duet = _has_partner(session)
    head = _TALK.format(
        who=f"{a} and {b}" if duet else a,
        seconds=v.get("duration_s") or 5,
        where=_WHERE_SHOOT if v.get("source") == "shoot" else _WHERE_PHOTO,
        lang="Japanese" if _ja(session) else "English",
        duet=_DUET.format(a=a, b=b) if duet else "",
    )
    parts = [head, _voices(session)]
    if v.get("source") == "shoot":
        mem = persona.memory_prompt_blocks(session)
        if mem:
            parts.append(mem[:1500])
    return "\n\n".join(parts)


def _ideas_block(v: dict[str, Any]) -> str:
    out = []
    for idea in v.get("ideas") or []:
        if "beats" not in idea:
            continue
        out.append(
            f"[{idea['kind']}] {idea.get('title', '')} — {idea.get('story', '')}\n"
            f"  beats: {' / '.join(idea['beats'])}"
        )
    return "\n".join(out)


def _recent(session: dict[str, Any], since: float) -> str:
    rows = [
        r for r in session.get("chat") or []
        if float(r.get("at") or 0) >= since and str(r.get("text") or "").strip()
    ][-10:]
    return "\n".join(
        f"{'Showrunner' if r.get('role') == 'user' else r.get('name') or 'you'}: {r['text']}"
        for r in rows
    )


def talk_prompt(session: dict[str, Any], line: str) -> str:
    v = state(session)
    return (
        f"PHOTOGRAPH (second 0):\n{v.get('still', '')}\n\n"
        f"IDEAS:\n{_ideas_block(v)}\n\n"
        f"ON THE TABLE: {v.get('chosen') or 'none yet'}\n\n"
        f"RECENT TALK:\n{_recent(session, float(v.get('started_at') or 0)) or '(none)'}\n\n"
        f"SHOWRUNNER:\n{line}"
    )


# The keys are for PICK; in her lines they read as code (「[stay]みたいに」),
# so a key that slips into SAY becomes that idea's title.
_KEY_IN_SAY = re.compile(r"\[(" + "|".join(ve.KINDS) + r")\]\s*", re.IGNORECASE)
# "A: (緒方 りん) うわ…" — the prefix already says who is talking.
_NAME_AFTER_PREFIX = re.compile(r"^([AB]:\s*)[(（][^)）]{1,20}[)）]\s*", re.MULTILINE)
# "…いくよ！ B:ふふ、張り切りすぎ" — the second speaker on the first one's line.
_INLINE_SPEAKER = re.compile(r"[ \t　]+(?=[AB][:：])")
_FIELD = re.compile(r"^\s*\**\s*(SAY|PICK|STORY|BEATS|WRITE)\s*\**\s*:\s*(.*)$", re.IGNORECASE)


def parse_reply(raw: str) -> dict[str, str]:
    """Her labelled lines. SAY may run over several lines (a duet's A:/B:)."""
    out: dict[str, str] = {}
    # Lines before any label are what she said: in a duet gemma often starts
    # straight in with "A: …" and only labels PICK / WRITE (2026-10-10, three
    # turns out of three in one run came back as "……" before this).
    field = "say"
    for line in (raw or "").splitlines():
        if m := _FIELD.match(line):
            field = m.group(1).lower()
            out[field] = m.group(2).strip()
        elif line.strip():
            out[field] = f"{out.get(field, '')}\n{line.strip()}".strip()
    out["pick"] = re.sub(r"[^a-z]", "", (out.get("pick") or "").lower())
    out["write"] = "yes" if (out.get("write") or "").strip().lower().startswith("y") else ""
    return out


async def _reply(ollama, session: dict[str, Any], line: str) -> dict[str, str]:
    inputs = _inputs(session)
    try:
        raw = await chain._call(
            ollama, system=talk_system(session), prompt=talk_prompt(session, line),
            model=str(inputs.get("model") or ""), images=None,
            num_ctx=refine_num_ctx(session), think=False,
        )
    except chain.ChainError:
        raw = ""
    reply = parse_reply(raw)
    titles = {i["kind"]: i.get("title", "") for i in state(session).get("ideas") or []}
    say = _KEY_IN_SAY.sub(
        lambda m: f"「{titles[m.group(1).lower()]}」" if titles.get(m.group(1).lower()) else "",
        reply.get("say") or "",
    )
    if _has_partner(session):
        say = _INLINE_SPEAKER.sub("\n", say)
    say = _NAME_AFTER_PREFIX.sub(r"\1", say)
    reply["say"] = identity.sanitize_muse_say(say, locale=_locale(session))
    if not reply["say"]:
        logger.warning("[muse.video] she said nothing: %r", (raw or "")[:300])
        reply["say"] = "……" if _ja(session) else "..."
    return reply


def _publish_say(session: dict[str, Any], say: str) -> None:
    talk.publish_actress_turn(
        session, {"say": say}, locale=_locale(session), lead_name=_lead_name(session),
    )


def _card(idea: dict[str, Any]) -> dict[str, Any]:
    return {k: idea[k] for k in ("kind", "label", "title", "story", "error") if k in idea}


def _post_cards(session: dict[str, Any], ideas: list[dict[str, Any]], *, revised: bool = False) -> None:
    talk._append_chat(
        session, role="assistant", name="", text="",
        meta={"kind": "video_ideas", "ideas": [_card(i) for i in ideas], "revised": revised},
    )


def _apply(session: dict[str, Any], reply: dict[str, str]) -> dict[str, Any] | None:
    """Take her PICK / STORY / BEATS onto the ideas. Returns the changed idea."""
    v = state(session)
    keys = {i["kind"] for i in v.get("ideas") or [] if "beats" in i}
    if reply.get("pick") in keys:
        v["chosen"] = reply["pick"]
    beats = [b.strip() for b in (reply.get("beats") or "").split("/") if b.strip()]
    target = v.get("chosen")
    if not beats or target not in keys:
        return None
    for idea in v["ideas"]:
        # The same beats sent back are not a change (she restates the idea
        # when told to go ahead).
        if idea["kind"] == target and [b.lower() for b in beats] != [b.lower() for b in idea["beats"]]:
            idea["beats"] = beats
            if reply.get("story"):
                idea["story"] = reply["story"]
            idea["revised"] = True
            return idea
    return None


# ── The three moves ──────────────────────────────────────────────────────────

async def start(
    db, ollama, session: dict[str, Any], *, sha: str, short: str = "", duration_s: int = 5,
) -> dict[str, Any]:
    """Bring a photograph to the table: look, pitch, and let her open the talk."""
    images = await images_by_sha(db, [sha])
    if not images:
        raise VideoTalkError(_say(session, "画像を読めません", "Could not read the image"))
    source, people = await photo_people(db, session, sha)
    inputs = _inputs(session)
    num_ctx = refine_num_ctx(session)
    still, blind = await ve.look(
        ollama, image=images[0], people=people, model=_vision_model(inputs),
        num_ctx=num_ctx,
        fallback=str((session.get("shoot") or {}).get("prompt") or "") if source == "shoot" else "",
    )
    if not still:
        raise VideoTalkError(_say(
            session, "画像を読めるモデルが要ります（vision_model）",
            "An image-capable model is needed (vision_model)",
        ))
    ideas = await ve.ideas(
        ollama, still=still, people=people, short=short, duration_s=duration_s,
        locale=_locale(session), model=str(inputs.get("model") or ""), num_ctx=num_ctx,
        context=shoot_context(session) if source == "shoot" else "",
    )
    session["video"] = {
        "active": True, "sha256": sha, "source": source, "people": people,
        "still": still, "blind": blind, "ideas": ideas, "chosen": "", "prompt": "",
        "duration_s": duration_s, "short": short, "started_at": time.time(),
    }
    # The panel shows the chat once a session is opened; a video talk opens it.
    session["opened"] = True
    talk._append_chat(
        session, role="system", name="",
        text=_say(session, "🎬 この絵から動画を考えます", "🎬 Turning this still into a video"),
        meta={"kind": "video_start", "sha256": sha, "source": source},
    )
    if blind:
        talk._append_chat(
            session, role="system", name="",
            text=_say(
                session,
                "このモデルは絵を読めなかったので、本番のプロンプトから考えました。vision_model に画像を読めるモデルを指定してください。",
                "This model could not read the picture, so the ideas start from the final prompt. Set vision_model to an image-capable model.",
            ),
            meta={"kind": "video_blind"},
        )
    opener = (
        (f"The Showrunner's note for this video: {short.strip()}\n" if short.strip() else "")
        + "(The Showrunner has just put the photograph down and asks what video it could "
        "become. Open the conversation: react to the photograph and pitch the ideas.)"
    )
    reply = await _reply(ollama, session, opener)
    _publish_say(session, reply["say"])
    _apply(session, reply)
    _post_cards(session, ideas)
    await session_db.save(db, session)
    return session


async def chat(db, ollama, session: dict[str, Any], message: str) -> dict[str, Any]:
    """One line from the Showrunner while talking a video through."""
    v = state(session)
    if not v.get("active"):
        raise VideoTalkError(_say(session, "動画の相談は始まっていません", "No video talk is open"))
    talk._append_chat(session, role="user", name="Director", text=message, meta={"kind": "video"})
    on_table = v.get("chosen") or ""
    reply = await _reply(ollama, session, message)
    _publish_say(session, reply["say"])
    changed = _apply(session, reply)
    if changed:
        _post_cards(session, [changed], revised=True)
    # Only the idea that was on the table when the Showrunner spoke is written.
    # One she has just changed, or just switched to, is shown first — live,
    # "make it rain", "more movement" and "which do you two like?" were each
    # written in the same turn, before the Showrunner had seen what was meant.
    elif reply.get("write") and on_table and v.get("chosen") == on_table:
        await _write(ollama, session, on_table)
    await session_db.save(db, session)
    return session


async def write(db, ollama, session: dict[str, Any], kind: str) -> dict[str, Any]:
    """The card's button: write that idea as an H3 prompt."""
    await _write(ollama, session, kind)
    await session_db.save(db, session)
    return session


async def _write(ollama, session: dict[str, Any], kind: str) -> None:
    v = state(session)
    idea = next((i for i in v.get("ideas") or [] if i.get("kind") == kind and "beats" in i), None)
    if idea is None:
        raise VideoTalkError(_say(session, "案を選んでください", "Pick an idea first"))
    inputs = _inputs(session)
    try:
        out = await ve.write(
            ollama, still=v.get("still", ""), people=v.get("people") or [], idea=idea,
            duration_s=int(v.get("duration_s") or 5), model=str(inputs.get("model") or ""),
            num_ctx=refine_num_ctx(session), locale=_locale(session),
        )
    except ve.VideoError as exc:
        raise VideoTalkError(exc.message) from exc
    v["chosen"] = kind
    v["prompt"] = out["prompt"]
    talk._append_chat(
        session, role="assistant", name="", text="",
        meta={
            "kind": "video_prompt", "prompt": out["prompt"], "idea": kind,
            "title": idea.get("title", ""), "instrument": out.get("instrument", ""),
        },
    )


async def stop(db, session: dict[str, Any]) -> dict[str, Any]:
    v = state(session)
    if v:
        v["active"] = False
        talk._append_chat(
            session, role="system", name="",
            text=_say(session, "📷 写真の撮影に戻ります", "📷 Back to shooting stills"),
            meta={"kind": "video_stop"},
        )
    await session_db.save(db, session)
    return session


def public(session: dict[str, Any]) -> dict[str, Any]:
    """What the panel needs to know about the video talk."""
    v = state(session)
    if not v:
        return {"active": False}
    return {
        "active": bool(v.get("active")),
        "sha256": v.get("sha256", ""),
        "source": v.get("source", ""),
        "chosen": v.get("chosen", ""),
        "duration_s": v.get("duration_s", 5),
        "blind": bool(v.get("blind")),
        "has_prompt": bool(v.get("prompt")),
    }

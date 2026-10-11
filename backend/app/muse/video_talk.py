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


def talk_system(talk: dict[str, Any]) -> str:
    a = _name(talk.get("character") or {}, ja=True) or "A"
    b = _name(talk.get("partner_character") or {}, ja=True) or "B"
    duet = _has_partner(talk)
    head = _TALK.format(
        who=f"{a} and {b}" if duet else a,
        seconds=talk.get("duration_s") or 5,
        where=_where(talk),
        lang="Japanese" if _ja(talk) else "English",
        duet=_DUET.format(a=a, b=b) if duet else "",
    )
    return "\n\n".join(p for p in [head, _voices(talk), relationship_blocks(talk)] if p)


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


def _ideas_block(talk: dict[str, Any]) -> str:
    if not talk.get("ideas"):
        return "(none yet — the writers pitch once the Showrunner says what they want)"
    return "\n".join(
        f"[{i['kind']}] {i.get('title', '')} — {i.get('story', '')}\n"
        f"  beats: {' / '.join(i['beats'])}"
        for i in talk.get("ideas") or [] if "beats" in i
    )


def _recent(talk: dict[str, Any]) -> str:
    rows = [r for r in talk.get("chat") or [] if r.get("role") != "system" and r.get("text")][-10:]
    return "\n".join(
        f"{'Showrunner' if r.get('role') == 'user' else r.get('name') or 'you'}: {r['text']}"
        for r in rows
    )


def talk_prompt(talk: dict[str, Any], line: str) -> str:
    context = (
        f"THE SHOOT IT CAME OUT OF:\n{talk['context']}\n\n" if talk.get("context") else ""
    )
    return (
        f"PHOTOGRAPH (second 0):\n{talk.get('still', '')}\n\n"
        f"{context}"
        f"IDEAS:\n{_ideas_block(talk)}\n\n"
        f"ON THE TABLE: {talk.get('chosen') or 'none yet'}\n\n"
        f"RECENT TALK:\n{_recent(talk) or '(none)'}\n\n"
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


async def _reply(ollama, talk: dict[str, Any], line: str) -> dict[str, str]:
    inputs = talk.get("inputs") or {}
    try:
        raw = await chain._call(
            ollama, system=talk_system(talk), prompt=talk_prompt(talk, line),
            model=str(inputs.get("model") or ""), images=None,
            num_ctx=inputs.get("num_ctx") or None, think=False,
        )
    except chain.ChainError:
        raw = ""
    reply = parse_reply(raw)
    titles = {i["kind"]: i.get("title", "") for i in talk.get("ideas") or []}
    say = _KEY_IN_SAY.sub(
        lambda m: f"「{titles[m.group(1).lower()]}」" if titles.get(m.group(1).lower()) else "",
        reply.get("say") or "",
    )
    # "…間違ってないって！<br>B: ふふ…" — gemma sometimes breaks lines with HTML.
    say = re.sub(r"<br\s*/?>", "\n", say, flags=re.IGNORECASE)
    # "…すごく好みです。 /" — a line-separator slash left at the end (seen on e4b).
    say = re.sub(r"[ \t　]+/[ \t　]*$", "", say, flags=re.MULTILINE)
    if _has_partner(talk):
        say = _INLINE_SPEAKER.sub("\n", say)
    say = _NAME_AFTER_PREFIX.sub(r"\1", say)
    reply["say"] = identity.sanitize_muse_say(say, locale=_locale(talk))
    if not reply["say"]:
        logger.warning("[muse.video] she said nothing: %r", (raw or "")[:300])
        reply["say"] = "……" if _ja(talk) else "..."
    return reply


def _row(talk: dict[str, Any], role: str, text: str, **meta: Any) -> None:
    row = {"role": role, "text": text, "at": time.time()}
    row.update({k: v for k, v in meta.items() if v not in (None, "")})
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


def _apply(talk: dict[str, Any], reply: dict[str, str]) -> dict[str, Any] | None:
    """Take her PICK / STORY / BEATS onto the ideas. Returns the changed idea."""
    keys = {i["kind"] for i in talk.get("ideas") or [] if "beats" in i}
    if reply.get("pick") in keys:
        talk["chosen"] = reply["pick"]
    beats = [b.strip() for b in (reply.get("beats") or "").split("/") if b.strip()]
    target = talk.get("chosen")
    if not beats or target not in keys:
        return None
    for idea in talk["ideas"]:
        # The same beats sent back are not a change (she restates the idea
        # when told to go ahead).
        if idea["kind"] == target and [b.lower() for b in beats] != [b.lower() for b in idea["beats"]]:
            idea["beats"] = beats
            if reply.get("story"):
                idea["story"] = reply["story"]
            idea["revised"] = int(idea.get("revised") or 0) + 1
            return idea
    return None


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


# ── The moves ────────────────────────────────────────────────────────────────

async def create(
    db, ollama, *, sha: str, character_id: str, partner_id: str = "",
    duration_s: int = 5, short: str = "", locale: str = "ja",
    cfg: dict[str, Any] | None = None, reuse: dict[str, Any] | None = None,
    model: str = "",
) -> dict[str, Any]:
    """Bring a photograph to the table: look, pitch, and let her open the talk.

    `reuse` is an earlier talk about the same picture: its reading is kept, so
    calling a different actress in does not look at the picture again.
    """
    inputs = model_inputs(cfg or {}, locale, model)
    talk: dict[str, Any] = {
        "talk_id": str(uuid.uuid4()), "created_at": time.time(), "sha256": sha,
        "inputs": inputs, "duration_s": duration_s, "short": short.strip(),
        "chat": [], "ideas": [], "chosen": "", "prompts": [],
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
    if talk.get("short"):
        await _pitch(ollama, talk, why="open")
    else:
        await _ask_first(ollama, talk)
    await video_db.save(db, talk)
    return talk


_PITCH_LINES = {
    "open": "(The Showrunner has just put the photograph down and asks what video it could "
            "become. Open the conversation: react to the photograph and pitch the ideas.)",
    "wish": "(The Showrunner has just said what they want — their line is the last one in "
            "the talk. The writers pitched with it; the ideas listed now are theirs. Answer "
            "the Showrunner and pitch the ideas.)",
    "fresh": "(The Showrunner asked the writers for a fresh set of ideas; they are the ones "
             "listed now. React to them.)",
}


async def _pitch(ollama, talk: dict[str, Any], *, why: str) -> None:
    """Ideas for what the director wants so far, then her pitch of them."""
    inputs = talk["inputs"]
    talk["ideas"] = await ve.ideas(
        ollama, still=talk["still"], people=talk.get("people") or [], short=intent_of(talk),
        duration_s=int(talk.get("duration_s") or 5), locale=_locale(talk),
        model=inputs["model"], num_ctx=inputs["num_ctx"], context=talk.get("context", ""),
    )
    talk["chosen"] = ""
    reply = await _reply(ollama, talk, _PITCH_LINES[why])
    _post_say(talk, reply["say"])
    _apply(talk, reply)


async def _ask_first(ollama, talk: dict[str, Any]) -> None:
    """No wish yet: she reacts to the photograph and asks what they want.

    Pitched before the director had said anything, the ideas were written
    blind, and only the one idea she later reworked ever heard the wish —
    "sleepy and cute" came back as a magic meadow (Asahi, 2026-10-11). The
    ideas now wait for the first thing the director says (or the note).
    """
    reply = await _reply(ollama, talk, (
        "(The Showrunner has just put the photograph down. The writers have not pitched "
        "yet. React to the photograph in a line or two, then ask what kind of video "
        "they want to make of it.)"
    ))
    _post_say(talk, reply["say"])


async def repitch(db, ollama, talk: dict[str, Any]) -> dict[str, Any]:
    """A fresh set of ideas for the same picture and the same actress."""
    _row(talk, "system", _say(talk, "新しい案を頼みました", "Asked for fresh ideas"), kind="repitch")
    await _pitch(ollama, talk, why="fresh")
    await video_db.save(db, talk)
    return talk


async def chat(db, ollama, talk: dict[str, Any], message: str) -> dict[str, Any]:
    """One line from the Showrunner."""
    _row(talk, "user", message)
    if not talk.get("ideas"):
        # The first thing the director says is what they want: pitch with it.
        await _pitch(ollama, talk, why="wish")
        await video_db.save(db, talk)
        return talk
    on_table = talk.get("chosen") or ""
    reply = await _reply(ollama, talk, message)
    _post_say(talk, reply["say"])
    changed = _apply(talk, reply)
    if changed:
        _row(talk, "system", _say(
            talk, f"✎ 「{changed.get('title', '')}」を直しました",
            f"✎ Changed \u201c{changed.get('title', '')}\u201d",
        ), kind="revised", idea=changed["kind"])
    # Only the idea that was on the table when the Showrunner spoke is written.
    # One she has just changed, or just switched to, is shown first — live,
    # "make it rain", "more movement" and "which do you two like?" were each
    # written in the same turn, before the Showrunner had seen what was meant.
    elif reply.get("write") and on_table and talk.get("chosen") == on_table:
        try:
            await _write(ollama, talk, on_table)
        except VideoTalkError as exc:
            # Her reply stands; the failed write is said in the talk, not lost with it.
            _row(talk, "system", exc.message, kind="write_failed")
    await video_db.save(db, talk)
    return talk


async def write(db, ollama, talk: dict[str, Any], kind: str) -> dict[str, Any]:
    """The card's button: write that idea as an H3 prompt."""
    try:
        await _write(ollama, talk, kind)
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


async def _write(ollama, talk: dict[str, Any], kind: str) -> None:
    idea = next((i for i in talk.get("ideas") or [] if i.get("kind") == kind and "beats" in i), None)
    if idea is None:
        raise VideoTalkError(_say(talk, "案を選んでください", "Pick an idea first"))
    inputs = talk["inputs"]
    try:
        out = await ve.write(
            ollama, still=talk.get("still", ""), people=talk.get("people") or [], idea=idea,
            duration_s=int(talk.get("duration_s") or 5), model=inputs["model"],
            num_ctx=inputs["num_ctx"], locale=_locale(talk), intent=intent_of(talk),
            persona=persona_of(talk),
        )
    except ve.VideoError as exc:
        # What the model returned, kept on the record: the screen only says it failed.
        talk["last_write_failure"] = {"at": time.time(), "idea": kind, "raw": (exc.raw or "")[:3000]}
        raise VideoTalkError(exc.message) from exc
    talk["chosen"] = kind
    talk.setdefault("prompts", []).append({
        "kind": kind, "title": idea.get("title", ""), "prompt": out["prompt"],
        "instrument": out.get("instrument", ""), "duration_s": talk.get("duration_s"),
        "at": time.time(),
        # What each writing stage returned, to read when a prompt disappoints.
        "stages": {k: str(v)[:4000] for k, v in (out.get("stages") or {}).items()},
    })
    _row(talk, "system", _say(
        talk, f"📝 「{idea.get('title', '')}」を撮影文にしました",
        f"📝 Wrote \u201c{idea.get('title', '')}\u201d as a prompt",
    ), kind="written", idea=kind)


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
        "chosen": talk.get("chosen", ""),
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

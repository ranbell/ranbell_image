"""Muse studio — showrunner chat, crew table-read, board, approve, shoot.

The user is 総監督. Muses discuss in character until a board is shown and the
showrunner presses approve. Board and approve are explicit actions, not words
matched out of chat text. There is no B/C/D pickup chain anymore.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..characters import compat as compat_mod
from ..characters import presets as presets_db
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
from . import brief as brief_mod
from . import chain, crew, diary as diary_mod, events, facets, identity, runner
from . import memories_db, notebook as notebook_mod
from . import session_db
from . import handpost_db, lounge as lounge_mod, lounge_db
from .runtime import negative_for as runtime_negative_for
from .runtime import render_settings
from .schema import missing_inputs, new_session

logger = logging.getLogger(__name__)

# One lock per session so two concurrent `finish_session` calls (double-click,
# a second tab, a retried request) cannot both pass the "already queued" guard
# before either has written `queued_at`.
_finish_locks: dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)

class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _locale(session: dict[str, Any]) -> str:
    return str(_inputs(session).get("locale") or "ja")


def _msg(session: dict[str, Any], *, ja: str, en: str) -> str:
    """Pick the Showrunner-facing error text for the session's locale.

    `MuseError` messages go straight to the user, same as the chat text
    elsewhere in this module — they follow the same locale branch everyone
    else does instead of being the one place stuck in one language.
    """
    return ja if _locale(session).startswith("ja") else en


def _identity_tags(session: dict[str, Any]) -> list[str]:
    # Each character's tags stay contiguous (A fully before B) — that much
    # this already did. What it does not do, and what a flat comma-joined tag
    # stream cannot do on its own, is bind an attribute to a subject: two
    # different hair-colour tokens sitting next to each other with nothing
    # marking the boundary is a known cause of cross-binding on a 2-subject
    # render. A1111-style `BREAK` chunking would fix this properly, but that
    # depends on the live ComfyUI graph actually honouring it — no workflow
    # JSON ships in this repo to check against (they live on the render host),
    # so it is not safe to bake in unverified. `runtime.negative_for` at least
    # gives the partner the same opposing-negative protection the lead always
    # had, which was a real asymmetry and a real (if partial) fix.
    character_a = session.get("character") or {}
    partner_character = session.get("partner_character") or {}
    tags_a = [str(t) for t in (character_a.get("identity_tags") or []) if str(t).strip()]
    if partner_character:
        tags_b = [str(t) for t in (partner_character.get("identity_tags") or []) if str(t).strip()]
        combined = ["2girls"]
        for t in tags_a + tags_b:
            if t not in ("1girl", "solo") and t not in combined:
                combined.append(t)
        return combined
    return tags_a


def _framing(inputs: dict[str, Any]) -> str:
    return identity.normalize_framing(str(inputs.get("framing") or "auto"))


def _cast(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Everyone in frame. Single Actress or W-Muse pair."""
    character_a = session.get("character") or {}
    partner_character = session.get("partner_character") or {}
    res = []
    if character_a:
        res.append(character_a)
    if partner_character:
        res.append(partner_character)
    return res


def _style(session: dict[str, Any]) -> str:
    """The look everything downstream obeys.

    The Showrunner's Style box wins when it has a word in it. When it is empty
    the cast decides: a room of lighting, colour and the producer pulls vivid, a
    room of the animation director and the supervisor pulls flat, and swapping
    one person moves the picture. That is the reason to let people pick a crew.
    """
    return crew.base_style_for(_crew_ids(session), _inputs(session).get("style") or "")


def _text_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("model") or "")


def _num_ctx(inputs: dict[str, Any], cfg: dict[str, Any]) -> int | None:
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None


def _chat_append(
    session: dict[str, Any], *, role: str, text: str,
    muse_id: str = "", name: str = "", kind: str = "",
    turns: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if not kind:
        if role == "muse":
            kind = "craft"
        elif role == "system":
            kind = "system"
        else:
            kind = "user"
    msg = {
        "id": str(uuid.uuid4()),
        "role": role,
        "muse_id": muse_id,
        "name": name,
        "kind": kind,
        "text": text,
        "at": time.time(),
        # Per-speaker split of a duet turn — see identity.parse_duet_speakers.
        # Empty for every non-duet message; the frontend falls back to `text`.
        "turns": turns or [],
    }
    session.setdefault("chat", []).append(msg)
    return msg


def _duet_speaker_label(session: dict[str, Any], speaker: str) -> tuple[str, str]:
    """`'A'`/`'B'` (from identity.parse_duet_speakers) -> (character_id, display name)."""
    char = (session.get("partner_character") if speaker == "B" else session.get("character")) or {}
    return str(char.get("character_id") or ""), str(char.get("name_ja") or char.get("name") or "")


def _resolve_duet_turns(session: dict[str, Any], raw_turns) -> list[dict[str, str]]:
    if not raw_turns:
        return []
    out: list[dict[str, str]] = []
    for t in raw_turns:
        cid, cname = _duet_speaker_label(session, str((t or {}).get("speaker") or ""))
        out.append({
            "speaker_id": cid, "speaker_name": cname, "text": str((t or {}).get("text") or ""),
        })
    return out


async def _duet_tier(db, session: dict[str, Any], partner_character: dict[str, Any] | None) -> str:
    """Cached on the session so a chat turn does not re-scroll every duet
    session in the collection (co_appearance_count) on every single message.
    """
    if not partner_character:
        session.pop("duet_tier", None)
        return ""
    lead_id = str((session.get("character") or {}).get("character_id") or "")
    partner_id = str(partner_character.get("character_id") or "")
    if not lead_id or not partner_id:
        return ""
    cached = session.get("duet_tier") or {}
    if cached.get("partner_id") == partner_id:
        return str(cached.get("tier") or "")
    compat = await compat_mod.compatibility(db, lead_id, partner_id)
    tier = str(compat.get("tier") or "")
    session["duet_tier"] = {"partner_id": partner_id, "tier": tier}
    return tier


def _publish_chat(session_id: str, msg: dict[str, Any]) -> None:
    events.publish(session_id, {"type": "chat_message", **msg})


def _token_publisher(session_id: str, muse_id: str):
    def _pub(text: str) -> None:
        events.publish(session_id, {
            "type": "chat_delta", "muse_id": muse_id, "text": text,
        })
    return _pub


# Kept as a name anything outside may have imported. The body lives in
# `runtime` because the GEN-lane runner needs it and cannot import this module.
negative_for = runtime_negative_for


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    inputs = {**_inputs(session), **{k: v for k, v in patch.items() if v is not None}}
    # The mode lives on the session, not in inputs — `is_duet` reads it there and
    # so does the panel, which has to know before anything starts whether to
    # show a casting drawer at all.
    if patch.get("mode") is not None:
        session["mode"] = str(patch["mode"])
    # Resolve crew when preset or ids change. Which one asked decides who wins:
    # picking a preset means "give me that crew", so the stored ids are rebuilt
    # from it. Toggling a seat means "keep mine with this change", so the ids
    # stand. Reading both from the merged inputs made the ids win every time,
    # and since a new session already carries ids, choosing a preset did nothing
    # at all after the first one.
    if "crew_preset" in patch or "crew_ids" in patch:
        chose_preset = patch.get("crew_preset") is not None
        ids = crew.resolve_crew(
            preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
            crew_ids=None if chose_preset else (list(inputs.get("crew_ids") or []) or None),
        )
        inputs["crew_ids"] = [
            i for i in ids if crew.role_of(i) not in ("finisher", "actress")
        ]
        if chose_preset:
            inputs["crew_preset"] = str(inputs.get("crew_preset") or crew.DEFAULT_PRESET)
        else:
            # A hand-toggled seat can drift from every named preset's roster —
            # the pill used to keep showing whichever preset was picked last,
            # forever, because nothing here ever noticed the ids no longer
            # matched it. "custom" is a real value here, not just a frontend
            # label, so any client sees the same answer.
            current = set(inputs["crew_ids"])
            matched = next(
                (
                    name for name in crew.PRESETS
                    if {
                        i for i in crew.resolve_crew(preset=name, crew_ids=None)
                        if crew.role_of(i) not in ("finisher", "actress")
                    } == current
                ),
                "",
            )
            inputs["crew_preset"] = matched or "custom"
    session["inputs"] = inputs
    _rebuild_brief(session)
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise MuseError(_msg(session, ja="キャラクターが見つかりません。", en="character not found"))
    session["character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "character_id": character_id}
    _rebuild_brief(session)
    session_db.log(session, "character", session["character"].get("name", ""))
    await session_db.save(db, session)
    return session


async def pick_partner(db, session: dict[str, Any], preset_id: str) -> dict[str, Any]:
    """The second Muse in 主演撮り (lead shoot). Empty string casts nobody.

    Resolved here rather than on her first line. Storing only the id meant the
    panel — which reads `partner_character` — showed "no partner" until she
    happened to speak, so picking somebody looked like it had not worked.
    """
    preset_id = (preset_id or "").strip()
    if not preset_id:
        session["partner_character"] = {}
        session["inputs"] = {**_inputs(session), "partner_preset": ""}
        await session_db.save(db, session)
        return session
    if preset_id == str(_inputs(session).get("character_id") or ""):
        raise MuseError(_msg(
            session,
            ja="主演とは異なる Muse をパートナーに選んでください。",
            en="Pick a Muse other than the lead as your partner.",
        ))
    preset = await presets_db.get_preset(db, preset_id)
    if preset is None:
        raise MuseError(_msg(session, ja="キャラクターが見つかりません。", en="character not found"))
    session["partner_character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": preset_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "partner_preset": preset_id}
    session_db.log(session, "partner", session["partner_character"].get("name", ""))
    await session_db.save(db, session)
    return session


async def _partner_character(db, session: dict[str, Any]) -> dict[str, Any] | None:
    """Whoever is cast opposite her, resolving and caching once if need be.

    `pick_partner` fills this in at the moment of casting. The lookup stays here
    for sessions whose id was set some other way (an inputs patch, an older
    session): it used to be copy-pasted into both duet turns, which is why the
    panel and the prompt could disagree about who was in the room.
    """
    preset_id = str(_inputs(session).get("partner_preset") or "").strip()
    if not preset_id:
        session.pop("partner_character", None)
        return None
    cached = session.get("partner_character") or {}
    if str(cached.get("character_id") or "") == preset_id or (
        (cached.get("personality") or {}).get("preset_key") == preset_id
    ):
        return cached
    try:
        preset = await presets_db.get_preset(db, preset_id)
    except Exception:
        logger.debug("[muse] partner lookup failed", exc_info=True)
        return None
    if not preset:
        return None
    session["partner_character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": preset_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    return session["partner_character"]


def _rebuild_brief(session: dict[str, Any]) -> None:
    """Two briefs: the full sheet for the seats that act, a digest for the rest.

    Lighting, colour and the audit desk were being handed her summary, inner
    life and tastes on every call. None of it is their craft, and it was the
    most evocative text in their context — so it became the language the whole
    script was written in, and the theme lost.
    """
    inputs = _inputs(session)
    character = session.get("character") or {}
    if not character or not str(inputs.get("theme") or "").strip():
        session["brief"] = ""
        session["brief_lite"] = ""
        return
    common = dict(
        theme=str(inputs.get("theme") or ""),
        style=_style(session),
        framing=_framing(inputs),
        plan=session.get("plan") or {},
        costume=session.get("costume") or {},
        notes=list(session.get("notes") or []),
        # Refusals already carried out drop out of the orders — they are
        # enforced by `drop_banned` and the negative prompt now, and leaving
        # the words in is what kept the crew talking about them.
        carried_out=list(session.get("carried_out") or []),
        removed_now=list(session.get("just_banned") or []),
        restored_now=list(session.get("just_restored") or []),
    )
    # COSTUME is locked craft, not inner life — both the full and digest briefs
    # carry it so every seat (acting or not) re-reads the same outfit.
    session["brief"] = brief_mod.build(
        character, common["theme"], common["style"],
        framing=common["framing"], plan=common["plan"],
        costume=common["costume"], notes=common["notes"],
        carried_out=common["carried_out"], removed_now=common["removed_now"],
        restored_now=common["restored_now"], reference="full",
    )
    session["brief_lite"] = brief_mod.build(
        character, common["theme"], common["style"],
        framing=common["framing"], plan=common["plan"],
        costume=common["costume"], notes=common["notes"],
        carried_out=common["carried_out"], removed_now=common["removed_now"],
        restored_now=common["restored_now"], reference="digest",
    )


# The seats whose craft IS the performance. Only these read her inner life.
_ACTING_ROLES = ("actress", "faces")


def _brief_for(session: dict[str, Any], muse_id: str = "") -> str:
    if crew.role_of(muse_id) in _ACTING_ROLES:
        return str(session.get("brief") or "")
    return str(session.get("brief_lite") or session.get("brief") or "")


def _crew_ids(session: dict[str, Any]) -> list[str]:
    inputs = _inputs(session)
    return crew.resolve_crew(
        preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
        crew_ids=list(inputs.get("crew_ids") or []) or None,
    )


# Long edge the board is scaled to before the VLM sees it. The 300px thumbnail
# is too small to judge composition on, and the full 896x1152 render is a lot of
# tokens to spend once per seat.
_VLM_LONG_EDGE = 768
# One decode per board round, not one per seat.
_BOARD_CACHE: dict[tuple[str, int, str], bytes] = {}


def _vision_model(inputs: dict[str, Any]) -> str:
    """A vision-capable model when one is configured, else the text model."""
    return str(inputs.get("vision_model") or "") or str(inputs.get("model") or "")


def _downscale(raw: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(raw)) as img:
        img = img.convert("RGB")
        img.thumbnail((_VLM_LONG_EDGE, _VLM_LONG_EDGE), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


async def board_images(db, session: dict[str, Any], *, limit: int = 1) -> list[bytes]:
    """The board the crew is being asked about, small enough to hand to a VLM.

    Empty when there is no board yet, when the store cannot resolve the image,
    or when anything about reading it fails — a screening that cannot load is a
    reason to keep talking, never a reason to stop the table.
    """
    board = session.get("board") or {}
    shots = [
        str(i.get("image_id") or "") for i in (board.get("images") or [])
        if isinstance(i, dict) and i.get("image_id")
    ][:max(1, int(limit))]
    if not shots or board.get("pending"):
        return []
    sid = str(session.get("session_id") or "")
    rnd = int(board.get("round") or 0)

    out: list[bytes] = []
    for sha in shots:
        key = (sid, rnd, sha)
        if key in _BOARD_CACHE:
            out.append(_BOARD_CACHE[key])
            continue
        try:
            docs = await db.get_by_sha256s([sha])
            path = Path(str((docs or [{}])[0].get("path") or ""))
            if not path.is_file():
                continue
            data = await asyncio.to_thread(
                lambda p=path: _downscale(p.read_bytes()),
            )
        except Exception:
            logger.debug("[muse] board image %s unreadable", sha[:8], exc_info=True)
            continue
        _BOARD_CACHE[key] = data
        out.append(data)

    # Keep the map from growing for the life of the process.
    if len(_BOARD_CACHE) > 24:
        for stale in list(_BOARD_CACHE)[:-8]:
            _BOARD_CACHE.pop(stale, None)
    return out


SCREENING_JA = (
    "あなたはいま、実際に上がったボードを見ている。\n"
    "- まず絵に写っているものを一つ挙げ、台本との差を言う。\n"
    "- 露出は絶対値で判定する（明るすぎ／暗すぎ／ちょうどよい）。"
    "ちょうどよければ光には一切触らない。\n"
    "- 台本に書いたのに写っていないものがあれば、それを直すのが最優先。\n"
    "- 写っているものを褒めるだけの発言はしない。"
)
SCREENING_EN = (
    "You are looking at the board that actually came back.\n"
    "- Name one thing that IS in the picture, and say how it differs from the craft.\n"
    "- Judge exposure in absolutes (too bright / too dark / correct). If it is "
    "correct, do not touch the light at all.\n"
    "- Anything the craft asked for that is not in the frame is the first fix.\n"
    "- Do not spend the turn praising what is already there."
)


def _screening_note(session: dict[str, Any]) -> str:
    locale = str(_inputs(session).get("locale") or "ja")
    return SCREENING_JA if locale.startswith("ja") else SCREENING_EN


async def _run_muse_turn(
    ollama, session: dict[str, Any], muse_id: str, user_prompt: str,
    *, cfg: dict[str, Any], images: list[bytes] | None = None,
) -> tuple[chain.MuseTurn, int]:
    inputs = _inputs(session)
    sid = session["session_id"]
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": muse_id,
        "name": _muse_display_name(session, muse_id),
    })
    started = time.monotonic()
    turn = await chain.run_muse(
        ollama, muse_id=muse_id, user_prompt=user_prompt,
        model=_vision_model(inputs) if images else _text_model(inputs),
        num_ctx=_num_ctx(inputs, cfg),
        identity_tags=_identity_tags(session),
        framing=_framing(inputs),
        # Leak detection always reads the full sheet, even when the seat was
        # only handed the digest — narrowing it would narrow what counts as a leak.
        brief=str(session.get("brief") or ""),
        think=False,
        images=images or None,
        character=session.get("character") or {},
        style=_style(session),
        cast=_cast(session),
        seed=str(session.get("session_id") or ""),
        on_token=_token_publisher(sid, muse_id),
    )
    return turn, int((time.monotonic() - started) * 1000)


def _muse_display_name(session: dict[str, Any], muse_id: str) -> str:
    if crew.role_of(muse_id) == "actress":
        ch = session.get("character") or {}
        p = ch.get("personality") or {}
        locale = str(_inputs(session).get("locale") or "ja")
        if locale.startswith("ja"):
            return str(
                ch.get("name_ja") or p.get("preset_name_ja")
                or ch.get("name") or p.get("preset_name") or "女優"
            )
        return str(ch.get("name") or p.get("preset_name") or "Actress")
    m = crew.MUSES[crew.resolve_member(muse_id)]
    locale = str(_inputs(session).get("locale") or "ja")
    if locale.startswith("ja"):
        # Job plus nickname: two people share 照明, and the log has to say which.
        return f"{m['name_ja']}「{m['nick_ja']}」"
    return f"{m['name']} ({m['nick']})"


def record_ledger(
    session: dict[str, Any], *, muse_id: str, name: str,
    before: str, after: str, ms: int = 0,
) -> dict[str, Any] | None:
    """Note which tags one seat put in, which it took out, and what it cost.

    The session only ever kept the final craft, so a frame carrying tags nobody
    asked for had no way to name the seat that asked. A run that ended in
    `(neck_tension:1.4)`, a school blazer and an extreme close-up could be read
    off the chat only by guessing which speaker meant which tag.

    `ms` is what the turn took. Paired with how much of a seat's work is still
    in the finished prompt, it is the only honest way to decide which jobs are
    worth their wall clock and which two could be one.
    """
    was = identity.tag_names(before)
    now = identity.tag_names(after)
    added = [t for t in now if t not in set(was)]
    dropped = [t for t in was if t not in set(now)]
    if not added and not dropped and not ms:
        return None
    entry = {
        "muse_id": muse_id, "name": name,
        "added": added, "dropped": dropped,
        "ms": int(ms), "at": time.time(),
    }
    session.setdefault("ledger", []).append(entry)
    return entry


def banned_tags(session: dict[str, Any]) -> list[str]:
    """Everything the Showrunner has taken out of this picture."""
    return [str(t) for t in (session.get("banned") or []) if str(t).strip()]


def drop_banned(session: dict[str, Any], tags: str) -> str:
    """Strip anything the Showrunner has refused, whoever just wrote it.

    This is the enforcement. Telling seats not to reintroduce something means
    naming it in their prompt every turn, which is what kept a refused prop
    alive in the conversation for the rest of the session. A filter needs to
    say nothing at all.
    """
    gone = set(banned_tags(session))
    if not gone or not str(tags or "").strip():
        return tags
    return ", ".join(
        p.strip() for p in str(tags).split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    )


def _apply_turn(
    session: dict[str, Any], turn: chain.MuseTurn, *, ms: int = 0,
) -> dict[str, Any]:
    craft = session.setdefault("craft", {})
    # Filter before the ledger reads it, so a seat that keeps reaching for a
    # refused tag shows up as never having added it rather than as a fight.
    kept = drop_banned(session, turn.tags)
    record_ledger(
        session, muse_id=turn.muse_id,
        name=_muse_display_name(session, turn.muse_id),
        before=str(craft.get("tags") or ""), after=kept, ms=ms,
    )
    craft["prompt"] = turn.prompt
    craft["tags"] = kept
    craft["scene"] = turn.scene
    if kept != turn.tags:
        _reassemble(session)
    # Wardrobe owns the locked COSTUME in the crewed studio; in a duet she is
    # the only seat, so her own prep turns own it instead. Capture it, take
    # the old outfit out of the craft, make sure the new one is actually in
    # the tags, and re-inject COSTUME so the NEXT turn re-reads it.
    if turn.costume is not None and (
        crew.role_of(turn.muse_id) == "wardrobe" or is_duet(session)
    ):
        prev = session.get("costume") or {}
        costume = dict(turn.costume)
        # The outfit's tags are the GARMENTS slots and nothing else. They used to
        # be the ledger diff of this turn — every tag Wardrobe added — which is
        # not clothing: one session filed the whole pool set under the costume,
        # so a change of clothes would have struck the location.
        garments = brief_mod.garment_tags(costume)
        # A turn that dropped the GARMENTS line tells us nothing about what she
        # has on. Keep the outfit already settled and strike nothing; an empty
        # set here reads as "she is wearing nothing now" and would take the whole
        # outfit out of the craft.
        costume["tags"] = garments or list(prev.get("tags") or [])
        session["costume"] = costume
        if garments:
            strike_dropped_costume(session, prev)
            _ensure_garments(session, garments)
        _rebuild_brief(session)
        craft = session.setdefault("craft", {})
    # Seats can be swapped mid-session. One brought in after the read-through
    # has never seen the script, and answering a note is not a substitute for
    # a first pass over it.
    spoken = session.setdefault("spoken", [])
    if turn.muse_id not in spoken:
        spoken.append(turn.muse_id)
    if crew.role_of(turn.muse_id) in ("beat", "spine") or not craft.get("pose_intent"):
        craft["pose_intent"] = turn.pose_intent
    name = _muse_display_name(session, turn.muse_id)
    say = turn.say or f"（{name}が台本を更新した。）"
    msg = _chat_append(
        session, role="muse", text=say,
        muse_id=turn.muse_id, name=name, kind="craft",
        turns=_resolve_duet_turns(session, turn.turns),
    )
    _publish_chat(session["session_id"], msg)
    events.publish(session["session_id"], {
        "type": "craft_updated", "prompt": turn.prompt, "muse_id": turn.muse_id,
    })
    return msg


def _recent_talk(
    session: dict[str, Any], *, limit: int = 6,
    kinds: tuple[str, ...] | None = None,
) -> str:
    lines: list[str] = []
    for m in (session.get("chat") or [])[-limit:]:
        if m.get("role") != "muse":
            continue
        if kinds and m.get("kind") not in kinds:
            continue
        name = m.get("name") or m.get("muse_id") or "?"
        mark = "（つぶやき）" if m.get("kind") == "banter" else ""
        lines.append(f"- {name}{mark}: {m.get('text')}")
    return "\n".join(lines)


def _wardrobe_rail(session: dict[str, Any], muse_id: str) -> str:
    """The character's usual clothes, for Wardrobe's first turn only.

    This used to be a bare `Outfit: <tags>` line in the brief itself, which every
    seat saw until COSTUME was set — and COSTUME is unset for exactly the turn
    that decides the clothes. A concrete ASCII tag list near the top of the
    prompt beat the garment the theme named, in Japanese, on the unfenced last
    line: her default clothes shipped instead, on every model tried, seven runs
    out of seven.

    So the rail is handed to the one seat it belongs to, once, and the theme is
    asked for first. Order is the fix — the discard rule has to be read before
    the garments it discards, or the tag list wins again.
    """
    if crew.role_of(muse_id) != "wardrobe" or (session.get("costume") or {}):
        return ""
    outfit = [
        str(t) for t in ((session.get("character") or {}).get("outfit_tags") or [])
        if str(t).strip()
    ]
    lines = [
        "WHAT SHE WEARS — settle this before anything else.",
        "1. Read the theme — the final line of the brief above. If it names a "
        "garment, THAT is the outfit. Write it into GARMENTS and stop "
        "reconsidering it.",
        "2. Only if the theme names no clothing, dress her for this place and "
        "hour, starting from the rail below.",
    ]
    if outfit:
        lines.append(
            f"DEFAULT RAIL (what she usually wears — DISCARD IT ENTIRELY if the "
            f"theme named a garment; do not layer it under the new one): "
            f"{', '.join(outfit)}"
        )
    return "\n".join(lines)


def _table_user_prompt(
    session: dict[str, Any], *, muse_id: str = "", note: str = "",
    screening: str = "",
) -> str:
    craft = session.get("craft") or {}
    previous = str(craft.get("prompt") or "")
    pose = str(craft.get("pose_intent") or "")
    base = brief_mod.with_previous(
        _brief_for(session, muse_id), previous, pose=pose, analysis=screening,
    )
    rail = _wardrobe_rail(session, muse_id)
    if rail:
        base = f"{base}\n\n{rail}"
    # Her diary is hers. It goes to the seat she is sitting in and nowhere else —
    # in the brief, the whole table would be reading it.
    if crew.role_of(muse_id) == "actress":
        for block in (
            _memory_block(session),
            _social_block(session),
            _handpost_block(session),
            _caught_block(session),
        ):
            if block:
                base = f"{base}\n\n{block}"
    # The planner already pulled these out of the tag list. The prose is the
    # other half of the prompt and only the seats writing it can clear that.
    struck = [str(s) for s in (session.get("struck") or []) if str(s).strip()]
    if struck:
        base = (
            f"{base}\n\n"
            f"STRUCK FROM THE SET (the plan no longer has these — they belong to "
            f"a place or a moment we have left). Delete them from TAGS and from "
            f"SCENE. Do not describe them, and do not replace them with synonyms:"
            f"\n{', '.join(struck)}"
        )
    # Craft turns only, and only a few. Banter carries no craft and every seat is
    # told to be charming in it, so feeding it back here was a loop with nothing
    # damping it: one image ("the gap between her knees") got restated by six
    # consecutive speakers until it was what the picture was about.
    talk = _recent_talk(session, limit=3, kinds=("craft",))
    if talk:
        base = (
            f"{base}\n\n"
            f"RECENT TABLE TALK (for SAY only — do NOT carry their nouns, "
            f"metaphors, or light/colour adjustments into TAGS/SCENE):\n"
            f"{talk}"
        )
    if note.strip():
        return (
            f"{base}\n\n"
            f"SHOW RUNNER NOTE (総監督 — treat as absolute creative direction):\n"
            f"{note.strip()}\n"
            f"Answer their note. Revise TAGS/SCENE to satisfy it without breaking Carry."
        )
    return base


def _times_spoken(session: dict[str, Any], muse_id: str) -> int:
    return sum(
        1 for m in (session.get("chat") or [])
        if m.get("muse_id") == muse_id and m.get("kind") == "banter"
    )


def _banter_prompt(
    session: dict[str, Any], *, speaker_id: str, about_id: str, about_text: str,
) -> str:
    locale = str(_inputs(session).get("locale") or "ja")
    about_name = _muse_display_name(session, about_id)
    self_name = _muse_display_name(session, speaker_id)
    talk = _recent_talk(session, limit=4)
    # The Lead gets a different move each time. Left alone the model gave her
    # one — a soft「……しちゃいそう」— and every line she had ended the same way.
    stance = ""
    if crew.role_of(speaker_id) == "actress":
        stance = crew.actress_stance(_times_spoken(session, speaker_id))
    if locale.startswith("ja"):
        return (
            f"あなたは{self_name}。いま{about_name}がこう言った:\n"
            f"「{about_text}」\n\n"
            f"直近の会話:\n{talk or '（まだ少ない）'}\n\n"
            + (f"今回の返し方: {stance}\n\n" if stance else "")
            + "口調どおりに1〜2文で反応して。"
            "台本のTAGS/SCENEは書き換えない。会話だけ。"
        )
    return (
        f"You are {self_name}. {about_name} just said:\n"
        f"\"{about_text}\"\n\n"
        f"Recent talk:\n{talk or '(thin)'}\n\n"
        f"React in 1–2 sentences in voice. Agree, push back, or pile on. "
        f"Chat only — do not rewrite craft."
    )


async def _run_banter(
    ollama, session: dict[str, Any], muse_id: str, *,
    about_id: str, about_text: str, cfg: dict[str, Any],
) -> dict[str, Any]:
    inputs = _inputs(session)
    sid = session["session_id"]
    name = _muse_display_name(session, muse_id)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": muse_id, "name": name,
    })
    try:
        say = await chain.run_banter(
            ollama, muse_id=muse_id,
            user_prompt=_banter_prompt(
                session, speaker_id=muse_id,
                about_id=about_id, about_text=about_text,
            ),
            model=_text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            character=session.get("character") or {},
            on_token=_token_publisher(sid, muse_id),
        )
    except chain.ChainError:
        logger.debug("[muse] banter skipped for %s", muse_id, exc_info=True)
        return {}
    msg = _chat_append(
        session, role="muse", text=say,
        muse_id=muse_id, name=name, kind="banter",
    )
    _publish_chat(sid, msg)
    return msg


def _plan_user_prompt(session: dict[str, Any], *, note: str = "") -> str:
    """Theme + standing orders + whatever place was already settled."""
    inputs = _inputs(session)
    parts = [
        f"Style: {_style(session)}",
        f"Framing: {_framing(inputs)}",
        f"THEME (the situation to plan — this is the whole assignment):\n"
        f"{str(inputs.get('theme') or '').strip()}",
    ]
    orders = brief_mod.orders_block(list(session.get("notes") or []))
    if orders:
        parts.append(orders)
    previous = brief_mod.plan_block(session.get("plan") or {})
    if previous:
        parts.append(
            "PREVIOUS PLAN (keep what still holds; change only what the orders "
            "or the board force you to change):\n" + previous
        )
    if note.strip():
        parts.append(
            "SHOW RUNNER NOTE (総監督 — treat as absolute creative direction):\n"
            f"{note.strip()}\n"
            "Re-settle PLACE / HOUR / LIGHT / ACTION / MUST APPEAR so this note "
            "is simply true. If they asked for a different place, move there — do "
            "not keep the old one alongside it."
        )
    return "\n\n".join(parts)


def _ledger_items(plan: dict[str, Any] | None) -> list[str]:
    return [
        identity.bare_tag(x)
        for x in ((plan or {}).get("must_appear") or [])
        if identity.bare_tag(x)
    ]


def _still_meant(old: str, new_items: list[str]) -> bool:
    """True when a new ledger entry is plainly the same thing renamed.

    `microphone` → `wireless_microphone` is the planner being more specific, not
    the planner throwing the microphone away. Without this, a re-spelled ledger
    would strike its own contents.
    """
    return any(old in new or new in old for new in new_items)


def uses_notebook(session: dict[str, Any]) -> bool:
    """Duet (主演撮り) keeps the shot in the living notebook, not facets."""
    return is_duet(session)


def on_facets(session: dict[str, Any]) -> bool:
    """True when facet-table helpers are available (duet sessions).

    Live chat/prep for duet go through `uses_notebook` + the scripter. The
    facet router and scoped prep remain callable for migration and unit tests;
    they are simply not invoked from `post_duet_chat` / notebook prep.
    """
    return is_duet(session)


def _reassemble(session: dict[str, Any]) -> None:
    """Rebuild the Comfy positive from the current shot.

    On the facet path the tags and the prose are *derived* — the facet table is
    the shot and `craft` is the view of it that the render, the ledger, the
    report and the panel all read without knowing the difference. The prose is
    the composed paragraph when one was composed from exactly this table, and
    the facet sentences joined otherwise, so the positive is never blocked on a
    model call: the panel can show what was just asked for the moment it lands.

    On the notebook path, craft tags/scene are owned by the scripter compile —
    only the positive string is refreshed from those fields.
    """
    craft = session.setdefault("craft", {})
    if uses_notebook(session) and int((session.get("notebook") or {}).get("rev") or 0) > 0:
        craft["prompt"] = identity.assemble_positive(
            _identity_tags(session), str(craft.get("tags") or ""),
            str(craft.get("scene") or ""),
            framing=_framing(_inputs(session)), style=_style(session),
            subject=identity.subject_tags(_cast(session)),
        )
        return
    table = facets.table_of(session) if on_facets(session) else None
    # An empty table is not an empty shot — it is a shot nobody has written into
    # the table yet. Deriving from it would blank a craft that a turn had just
    # filled in the old shape, so the projection only takes over once there is
    # something in it to project.
    if table is not None and facets.table_rev(table):
        composed = session.get("composed") or {}
        scene = str(composed.get("scene") or "").strip()
        if not scene or int(composed.get("rev") or -1) != facets.table_rev(table):
            scene = facets.nl_join(table)
        craft["tags"] = facets.all_tags(table)
        craft["scene"] = scene
        craft["pose_intent"] = str(table["pose"].get("nl") or "")
        # PLAN and COSTUME are not a second source of truth any more; they are
        # this table in the shape `brief.plan_block` / `costume_block` expect,
        # refreshed here so the brief cannot fall behind the shot.
        session["plan"] = facets.to_plan(table)
        session["costume"] = facets.to_costume(table)
    craft["prompt"] = identity.assemble_positive(
        _identity_tags(session), str(craft.get("tags") or ""),
        str(craft.get("scene") or ""),
        framing=_framing(_inputs(session)), style=_style(session),
        subject=identity.subject_tags(_cast(session)),
    )


def garment_tags(session: dict[str, Any]) -> list[str]:
    """What she currently has on, as tags. The outfit's owner is Wardrobe alone,
    so this is the set every other removal path has to leave standing."""
    return brief_mod.garment_tags(session.get("costume") or {})


def _ensure_garments(session: dict[str, Any], garments: list[str]) -> list[str]:
    """Put back any garment COSTUME names that the turn forgot to write in TAGS.

    COSTUME is prose the seats re-read; the render only ever sees tags. A garment
    that exists in one and not the other is the outfit being left to the
    checkpoint, which is the failure the COSTUME block was built to end.

    Refused garments are not put back. This was the one way past ``drop_banned``:
    a Showrunner who said「上着脱いで」had the jacket struck from the craft, and
    then the next wardrobe turn re-read a COSTUME block that still named it and
    stapled it straight back on. It came back as many times as she asked for it
    to go.
    """
    craft = session.setdefault("craft", {})
    gone = set(banned_tags(session))
    have = set(identity.tag_names(str(craft.get("tags") or "")))
    missing = [t for t in garments if t not in have and t not in gone]
    if not missing:
        return []
    parts = [p.strip() for p in str(craft.get("tags") or "").split(",") if p.strip()]
    craft["tags"] = ", ".join(parts + missing)
    _reassemble(session)
    return missing


def apply_removals(
    session: dict[str, Any], remove: list[str], restore: list[str],
) -> tuple[list[str], list[str]]:
    """Carry out a refusal: take it out, and keep it out.

    A refusal used to be the one instruction the studio could not perform. It
    was stored as a standing order in the Showrunner's own words and re-read by
    every seat on every turn, so the refused noun stayed in front of everyone
    forever and the crew kept discussing it; no code path could delete a prop
    the art department had added; and the sampler never heard about it at all,
    because the negative prompt is built from settings and never from what the
    Showrunner said. Saying "no" made the thing more present, not less.

    So a refusal changes state instead of adding text. The tag comes out now,
    ``drop_banned`` keeps it out however many times a seat reaches for it, and
    ``negative_for`` hands it to the sampler — the one place in the pipeline
    where "not this" actually works.
    """
    gone = set(banned_tags(session))
    freed = [t for t in restore if t in gone]
    added = [t for t in remove if t not in gone]
    if not freed and not added:
        return [], []

    gone.update(added)
    gone.difference_update(freed)
    # Ordered for a stable negative prompt and a readable panel.
    session["banned"] = sorted(gone)
    # Only this turn's refusals are shown to the crew, and only on this turn —
    # the seats answering the note need to know why something vanished, and
    # nobody after them needs the noun at all.
    session["just_banned"] = list(added)
    session["just_restored"] = list(freed)

    craft = session.setdefault("craft", {})
    before = str(craft.get("tags") or "")
    if on_facets(session):
        # The craft is derived here, so striking it would last exactly until the
        # next reassemble put the tag back from the table. A refusal has to
        # reach the state, not the view of it — otherwise the refused thing goes
        # on being handed to every turn as part of the shot.
        table = facets.table_of(session)
        stale = facets.strike(session, gone)
        # A part whose tags just changed is a part whose sentence is now wrong,
        # and the sentence is half the prompt. The old code told the next seats
        # outright ("STRUCK FROM THE SET") and hoped; here the stale prose is
        # dropped and the part is queued for rewrite, so nothing downstream is
        # ever handed a sentence naming a thing that is no longer in the shot.
        if stale:
            routed = session.setdefault("routed", [])
            routed.extend(n for n in stale if n not in routed)
        _reassemble(session)
    else:
        craft["tags"] = drop_banned(session, before)
        if craft["tags"] != before:
            _reassemble(session)
    if craft.get("tags") != before:
        record_ledger(
            session, muse_id="showrunner", name="総監督",
            before=before, after=str(craft.get("tags") or ""),
        )
    return added, freed


def directives_block(session: dict[str, Any], *, only: list[str] | None = None) -> str:
    """The Showrunner's direction, one line per part, newest at the bottom.

    This is the whole of what the standing orders used to be, and it does not
    grow. `orders_block` rendered every note ever said into every brief, newest
    first, and left the crew to work out which of seventeen absolute
    instructions won. Here a second camera order simply replaces the first, so a
    twenty-turn session hands over the same eight lines a two-turn session does.
    """
    data = session.get("directives") or {}
    lines: list[str] = []
    for name, label in facets.FACETS:
        if only is not None and name not in only:
            continue
        text = str((data.get(name) or {}).get("text") or "").strip()
        if text:
            lines.append(f"- {label}: {text}")
    if not lines:
        return ""
    return "\n".join([
        "SHOWRUNNER DIRECTION (総監督 said these and they stand until they are "
        "said again):",
        *lines,
    ])


async def set_facet_lock(
    db, session: dict[str, Any], facet: str, locked: bool,
) -> dict[str, Any]:
    """Pin one part of the shot, or let it move again."""
    if not on_facets(session):
        raise MuseError(_msg(
            session,
            ja="この撮影では固定できません（主演撮りだけの機能です）。",
            en="Parts can only be pinned in a lead shoot.",
        ))
    try:
        facets.set_lock(session, facet, locked)
    except ValueError as exc:
        raise MuseError(f"unknown facet: {facet}") from exc
    await session_db.save(db, session)
    return session


async def route_note(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> tuple[list[str], str]:
    """Work out which parts of the shot a note changes, and record it.

    Returns (every part the note is ABOUT — locked or not — and the standing
    rule it added). An empty list is the normal answer for「いい感じ」and means
    the shot is untouched.

    The caller uses this return value to decide whether the note is a
    REPLACEMENT (skip the strike clerk) or an unroutable REFUSAL (run it). A
    locked part still has to come back in this list even though nothing about
    it is written: a note the router recognised as being about the camera is
    replacement-shaped whether or not the camera happens to be pinned, and
    routing it to the refusal clerk instead — because the pin quietly emptied
    the list — was itself a bug (see 2026-08-11 e2e run, turn 15: 「真横から
    撮って」while the camera was locked fell through to the clerk, which read
    the note as retiring `from_front` and struck it out of the locked camera
    facet anyway, because a refusal is allowed to override a pin. The note was
    never a refusal; the lock only looked like one to the branch that decides).
    """
    if not on_facets(session) or ollama is None or not text.strip():
        return [], ""
    inputs = _inputs(session)
    table = facets.table_of(session)
    partner_character = await _partner_character(db, session)
    char_a = session.get("character") or {}
    name_a = str(char_a.get("name_ja") or char_a.get("name") or "")
    name_b = ""
    label_names: dict[str, str] | None = None
    if partner_character:
        name_b = str(partner_character.get("name_ja") or partner_character.get("name") or "")
        label_names = {"costume_b": name_b, "pose_b": name_b, "expression_b": name_b}
    try:
        named, lines, standing, digest = await chain.run_route(
            ollama, note=text, table_block=facets.table_block(table, names=label_names),
            current_digest=str(session.get("digest") or ""),
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            name_a=name_a, name_b=name_b,
        )
    except Exception:
        logger.warning("[muse] route turn failed; nothing routed", exc_info=True)
        return [], ""

    writable = [n for n in named if not table[n].get("locked")]
    session["locked_conflicts"] = [n for n in named if n not in writable]
    directives = session.setdefault("directives", {})
    now = time.time()
    for name in writable:
        # The clerk was asked for the finished value; when it gave none, the
        # note's own words stand in. Worst case is today's behaviour, scoped to
        # the part it is about.
        directives[name] = {"text": lines.get(name) or text.strip(), "at": now}
    if standing:
        rules = session.setdefault("standing", [])
        if standing not in rules:
            rules.append(standing)
    if digest:
        # Rewritten, not appended — "added, then decided against" collapses to
        # one line instead of surviving as two contradictory facts. `digest`
        # is "" whenever the model left it unchanged, so the old value stands.
        # A malformed revision (a change-annotation baked in, a bare tag list
        # standing in for a sentence) is treated the same way: this is the
        # one thing every future turn is told to prioritise over the
        # conversation itself, so a bad rewrite here does more damage than a
        # bad rewrite anywhere else in the session.
        cleaned = identity.sane_prose(digest)
        if cleaned:
            session["digest"] = cleaned
        else:
            logger.warning(
                "[muse] refused malformed digest, kept prior value: %r",
                digest[:120],
            )
    session["routed"] = writable
    return named, standing


async def take_note(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Record a Showrunner note, and carry out whatever it refuses.

    The strike turn runs on every note rather than behind a "does this look
    like a refusal?" pattern. Patterns miss the phrasings nobody thought of,
    and this cannot: a note that removes nothing simply comes back empty.
    """
    notes = session.setdefault("notes", [])
    notes.append(text)
    index = len(notes) - 1
    session["just_banned"] = []
    session["just_restored"] = []

    removed: list[str] = []
    restored: list[str] = []
    if ollama is not None:
        inputs = _inputs(session)
        try:
            picked, back = await chain.run_strike(
                ollama, note=text,
                tags=identity.tag_names(str((session.get("craft") or {}).get("tags") or "")),
                removed=banned_tags(session),
                model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            )
        except Exception:
            logger.warning("[muse] strike turn failed; nothing removed", exc_info=True)
            picked, back = [], []
        removed, restored = apply_removals(session, picked, back)

    if removed:
        # The note's own words drop out of the standing orders from the next
        # turn on. Its effect is a filter and a negative prompt now, and leaving
        # the refused noun in front of every seat is what kept them talking
        # about it for the rest of the session.
        session.setdefault("carried_out", []).append(index)
        locale = str(_inputs(session).get("locale") or "ja")
        msg = _chat_append(
            session, role="system", name="Studio",
            text=(
                f"（外しました: {'、'.join(removed)}。以降は書き戻されません）"
                if locale.startswith("ja") else
                f"(Removed: {', '.join(removed)} — and kept out from here on.)"
            ),
        )
        _publish_chat(session["session_id"], msg)
    if restored:
        locale = str(_inputs(session).get("locale") or "ja")
        msg = _chat_append(
            session, role="system", name="Studio",
            text=(
                f"（戻しました: {'、'.join(restored)}）"
                if locale.startswith("ja") else
                f"(Restored: {', '.join(restored)}.)"
            ),
        )
        _publish_chat(session["session_id"], msg)

    _rebuild_brief(session)
    return removed, restored


def strike_dropped_props(
    session: dict[str, Any], previous_plan: dict[str, Any] | None,
) -> list[str]:
    """Take the old ledger's props out of the craft when the planner drops them.

    CARRY tells every seat to KEEP setting objects once they exist, which is a
    ratchet with no release: a note that moved the shoot somewhere else left the
    previous location's props sitting in the craft, and clearing them out was
    manual work for the Showrunner on every single note.

    Only what the *planner* listed and then dropped is struck. Anything the art
    department added on top of the ledger belongs to the room it dressed and
    survives — that dressing is the part of the picture that works.
    """
    was = _ledger_items(previous_plan)
    now = _ledger_items(session.get("plan"))
    if not was:
        return []
    # Clothes are not the planner's to drop. A garment that reaches MUST APPEAR
    # is a mistake the COSTUME header already warns about ("a garment word in
    # MUST APPEAR is an object on the floor"), and it must not become a way for
    # a change of scene to undress her — holding the outfit while the place moves
    # is a thing the Showrunner does on purpose.
    worn = set(garment_tags(session))
    struck = [
        t for t in was
        if t not in now and t not in worn and not _still_meant(t, now)
    ]
    if not struck:
        return []

    craft = session.setdefault("craft", {})
    gone = set(struck)
    kept = [
        p.strip() for p in str(craft.get("tags") or "").split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    ]
    before = str(craft.get("tags") or "")
    craft["tags"] = ", ".join(kept)
    _reassemble(session)
    # The prose still names them, and the tag list is only half the prompt. The
    # seats that write next are told outright, which is the only thing that gets
    # them out of SCENE.
    session["struck"] = struck
    record_ledger(
        session, muse_id=_cast_in_role(_crew_ids(session), "plan") or "plan",
        name=_muse_display_name(session, "plan"),
        before=before, after=craft["tags"],
    )
    return struck


def strike_dropped_costume(
    session: dict[str, Any], previous_costume: dict[str, Any] | None,
) -> list[str]:
    """Take the old outfit's tags out of the craft when Wardrobe rebuilds COSTUME.

    The §2-5 release valve: when the Showrunner says "change the clothes",
    Wardrobe writes a new COSTUME and last outfit's garments must not ride
    alongside the new ones. Mirrors ``strike_dropped_props`` — only the PREVIOUS
    costume's own tag set is struck (a known set, no dictionary), and
    ``_still_meant`` protects a rename (skirt → pleated_skirt).

    That tag set is the GARMENTS slots now, so this strikes clothes and only
    clothes. It was the ledger diff of Wardrobe's turn, which meant the pool she
    happened to be standing beside was filed as part of her outfit and came off
    with it.
    """
    was = [
        identity.bare_tag(t)
        for t in (previous_costume or {}).get("tags", [])
        if identity.bare_tag(t)
    ]
    now = (session.get("costume") or {}).get("tags", [])
    if not was:
        return []
    struck = [t for t in was if t not in now and not _still_meant(t, now)]
    if not struck:
        return []

    craft = session.setdefault("craft", {})
    gone = set(struck)
    kept = [
        p.strip() for p in str(craft.get("tags") or "").split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    ]
    before = str(craft.get("tags") or "")
    craft["tags"] = ", ".join(kept)
    _reassemble(session)
    prior = list(session.get("struck") or [])
    session["struck"] = prior + [t for t in struck if t not in prior]
    record_ledger(
        session, muse_id=_cast_in_role(_crew_ids(session), "wardrobe") or "wardrobe",
        name=_muse_display_name(session, "wardrobe"),
        before=before, after=craft["tags"],
    )
    return struck


async def _run_plan_turn(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any], note: str = "",
) -> bool:
    """Settle the situation. Returns True when the plan changed.

    Runs before anyone describes anything, and again whenever the Showrunner
    says something — a note that only reached the turn answering it was outvoted
    by the original theme on every call after that, so「make it X」never became
    the thing the render was of.
    """
    if ollama is None:
        return False
    inputs = _inputs(session)
    sid = session["session_id"]
    mid = _cast_in_role(_crew_ids(session), "plan") or crew.DEFAULT_MEMBER["plan"]
    images = await board_images(db, session)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": mid,
        "name": _muse_display_name(session, mid),
    })
    try:
        plan = await chain.run_plan(
            ollama, muse_id=mid,
            user_prompt=_plan_user_prompt(session, note=note),
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            images=images or None,
            seed=str(sid),
            on_token=_token_publisher(sid, mid),
        )
    except chain.ChainError:
        logger.warning("[muse] plan turn produced nothing", exc_info=True)
        return False
    if not plan:
        logger.info("[muse] planner answered without labelled lines; keeping plan")
        return False

    blind = bool(plan.pop("blind", False))
    say = str(plan.pop("say", "") or "")
    previous_plan = session.get("plan") or {}
    # A planner answering PLACE / HOUR / LIGHT / ACTION and no ledger is a line
    # it did not retype, not a room that has been emptied. Read as an empty
    # ledger it struck all twelve props from a karaoke booth — including the
    # wireless microphone the Showrunner had just asked for by name — and left
    # every later seat with nothing to be audited against.
    if not plan.get("must_appear") and previous_plan.get("must_appear"):
        plan["must_appear"] = list(previous_plan["must_appear"])
    # The planner no longer has a clothing line; what she wears lives in COSTUME,
    # owned by Wardrobe. A stray `wearing` from an old session is dropped.
    plan.pop("wearing", None)
    session["plan"] = plan
    session.pop("struck", None)
    struck = strike_dropped_props(session, previous_plan)
    _rebuild_brief(session)
    if struck:
        locale = str(inputs.get("locale") or "ja")
        tidied = _chat_append(
            session, role="system", name="Studio",
            text=(
                f"（{_muse_display_name(session, mid)}が片付けました: "
                f"{'、'.join(struck)}）"
                if locale.startswith("ja") else
                f"(Struck from the set by "
                f"{_muse_display_name(session, mid)}: {', '.join(struck)})"
            ),
        )
        _publish_chat(sid, tidied)
    if say:
        msg = _chat_append(
            session, role="muse", text=say, muse_id=mid,
            name=_muse_display_name(session, mid), kind="craft",
        )
        _publish_chat(sid, msg)
    if blind and images:
        _note_blind(session)
    session_db.log(session, "plan", str(plan.get("place") or ""))
    return True


def _note_blind(session: dict[str, Any]) -> None:
    """Say out loud that the board did not reach the model."""
    if session.get("_blind_said"):
        return
    session["_blind_said"] = True
    locale = str(_inputs(session).get("locale") or "ja")
    msg = _chat_append(
        session, role="system", name="Studio",
        text=(
            "このモデルは絵を読めないようなので、ボードは渡さずテキストだけで進めます。"
            "vision_model に画像を読めるモデルを指定すると、班が実際の絵を見て話せます。"
            if locale.startswith("ja") else
            "This model could not read the board — continuing on text alone. "
            "Set vision_model to an image-capable model so the crew can see it."
        ),
    )
    _publish_chat(session["session_id"], msg)


def _banter_mode(session: dict[str, Any]) -> str:
    mode = str(_inputs(session).get("banter_mode") or "light").strip().lower()
    return mode if mode in ("light", "full", "off") else "light"


def _cast_in_role(crew_ids: list[str], role: str) -> str | None:
    """Whoever is doing that job in this cast, if anyone is."""
    return next((m for m in crew_ids if crew.role_of(m) == role), None)


def _pick_banter_reactor(
    session: dict[str, Any], crew_ids: list[str], *,
    current: str, previous: str | None, index: int,
) -> str | None:
    """Who heckles after a craft pass. light mode keeps Ollama call counts sane."""
    mode = _banter_mode(session)
    if mode == "off":
        return None
    # light: every other pass, or always after the actress (personality beat).
    if mode == "light" and crew.role_of(current) != "actress" and index % 2 == 0:
        return None
    # The Lead gets a fixed share rather than third place in a fallback list
    # that almost never ran — `previous` took nearly every heckle, and she came
    # out of a full eighteen-seat session with three lines.
    lead = _cast_in_role(crew_ids, "actress")
    if lead and lead != current and index % 4 == 1:
        return lead
    if previous and previous != current and previous in crew_ids:
        return previous
    for role in ("hook", "actress", "faces", "spine", "beat"):
        mid = _cast_in_role(crew_ids, role)
        if mid and mid != current:
            return mid
    return None


def _pick_extra_heckler(
    session: dict[str, Any], crew_ids: list[str], *,
    current: str, reactor: str | None, index: int,
) -> str | None:
    """Second heckler — full mode only (too expensive for local Ollama otherwise)."""
    if _banter_mode(session) != "full":
        return None
    if index % 3 != 2:
        return None
    for role in ("actress", "hook", "faces", "cutout", "propshop"):
        mid = _cast_in_role(crew_ids, role)
        if mid and mid not in (current, reactor):
            return mid
    return None


# ── open the table ──────────────────────────────────────────────────────────
# The seats that meet before anything is drawn: someone to settle where and
# when, someone to dress her, her, and a camera. Everyone else waits for a frame
# to argue with.
#
# The table used to open with all eighteen and no picture anywhere, and the
# result was a run where「カラオケボックスで歌っている」became a live house: twenty
# turns of prose agreeing with each other, and the Showrunner then spent the
# session deleting props that had accumulated in the dark. A real studio shoots
# a still first and talks about the still.
#
# Wardrobe joined this set because the outfit had no owner in the opening: the
# camera, writing first into an empty craft, authored the clothes, and a garment
# the theme named ended up layered under the character's default clothes. It runs
# FIRST now (dress her, then frame her) — see OPENING_SEQUENCE. Being here takes
# it OUT of act two (`without=OPENING_ROLES`), which is fine: the COSTUME it
# sets is locked, so act two re-reads it rather than re-deriving it.
OPENING_ROLES: tuple[str, ...] = ("wardrobe", "actress", "lens")
# Dressing order for the opening: Wardrobe dresses her, the camera frames the
# dressed figure, she acts last. `_writing_seats(only=...)` returns cast order
# (ROLE_ORDER: lens before wardrobe), so the opening sorts by this explicitly.
OPENING_SEQUENCE: tuple[str, ...] = ("wardrobe", "lens", "actress")


async def _craft_pass(
    db, ollama, session: dict[str, Any], cast: list[str], seats: list[str], *,
    cfg: dict[str, Any], images: list[bytes] | None = None,
    screening: str = "", note: str = "", first_index: int = 0,
) -> str:
    """Run these seats in order, with the banter that goes between them."""
    previous: str | None = None
    last_say = ""
    for offset, muse_id in enumerate(seats):
        index = first_index + offset
        turn, ms = await _run_muse_turn(
            ollama, session, muse_id,
            _table_user_prompt(
                session, muse_id=muse_id, note=note, screening=screening,
            ),
            cfg=cfg, images=images,
        )
        msg = _apply_turn(session, turn, ms=ms)
        last_say = str(msg.get("text") or "")
        if turn.blind and images:
            _note_blind(session)
            images = []
            screening = ""
        if crew.role_of(muse_id) == "actress":
            await _after_actress_spoke(db, session)
        await session_db.save(db, session, publish=False)

        reactor = _pick_banter_reactor(
            session, cast, current=muse_id, previous=previous, index=index,
        )
        if reactor and last_say:
            await _run_banter(
                ollama, session, reactor,
                about_id=muse_id, about_text=last_say, cfg=cfg,
            )
            await session_db.save(db, session, publish=False)

        heckler = _pick_extra_heckler(
            session, cast, current=muse_id, reactor=reactor, index=index,
        )
        if heckler and last_say:
            await _run_banter(
                ollama, session, heckler,
                about_id=muse_id, about_text=last_say, cfg=cfg,
            )
            await session_db.save(db, session, publish=False)

        previous = muse_id
    return last_say


def _writing_seats(cast: list[str], *, only: tuple[str, ...] = (),
                   without: tuple[str, ...] = ()) -> list[str]:
    """Cast members who hold a pen, in table order."""
    out = []
    for mid in cast:
        role = crew.role_of(mid)
        if role == "plan" or role in crew.BANTER_ONLY or role in without:
            continue
        if only and role not in only:
            continue
        out.append(mid)
    return out


def _opening_seats(cast: list[str]) -> list[str]:
    """The opening writing seats in DRESSING order, not cast order.

    Wardrobe dresses her before the camera frames her, so the outfit is owned
    before anyone else writes. ROLE_ORDER puts lens before wardrobe, so the
    opening is sorted explicitly by OPENING_SEQUENCE rather than table order.
    """
    rank = {r: i for i, r in enumerate(OPENING_SEQUENCE)}
    seats = _writing_seats(cast, only=OPENING_ROLES)
    return sorted(seats, key=lambda m: rank.get(crew.role_of(m), 99))


async def start_table(
    db, ollama, session: dict[str, Any], *, comfy=None, spooler=None,
) -> dict[str, Any]:
    """Read-through, act one: place, her, a camera — then a still to argue with.

    With no renderer wired (`comfy`/`spooler` omitted) there is no still to wait
    for, so the whole table meets at once as it used to.
    """
    missing = missing_inputs(session)
    if missing:
        raise MuseError(_msg(
            session,
            ja=f"入力が不足しています: {', '.join(missing)}",
            en=f"missing: {', '.join(missing)}",
        ))

    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    session["status"] = "discussing"
    session["chat"] = []
    session["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    session["ledger"] = []
    session["banned"] = []
    session["carried_out"] = []
    session["spoken"] = []
    session["board"] = {}
    session["shoot"] = {}
    session["plan"] = {}
    session["costume"] = {}
    session.pop("_blind_said", None)
    session.pop("struck", None)
    # The table gets the same memory the two-hander does. It reaches her seat
    # only — see `_table_user_prompt`; in the shared brief all eighteen seats
    # would be reading her diary.
    await _load_actress_memory(db, session)
    still_first = comfy is not None and spooler is not None
    session["table_stage"] = "brief" if still_first else "full"
    await session_db.save(db, session)

    locale = str(_inputs(session).get("locale") or "ja")
    if still_first:
        open_ja = (
            "総監督、まず場所と芝居だけ決めます。構成・主演・撮影の三人で当たりを付けて、"
            "スチールを一枚撮ります。それを見てから「こういう絵が欲しい」を聞かせてください。"
            "そこから全班で詰めます。"
        )
        open_en = (
            "Showrunner — place and performance first. The planner, the Lead and "
            "the camera will rough it in, then we shoot one still. Tell us what "
            "picture you want off that, and the full crew takes it from there."
        )
    else:
        open_ja = (
            "総監督、打ち合わせを始めます。班が台本を継ぎつつ、お互いにちょこちょこ口を挟みます。"
            "無理難題歓迎です。途中でイメージボードを出しますので、コメントをください。"
        )
        open_en = (
            "Showrunner, table read is open. The crew will pass the craft and heckle "
            "each other along the way. Hard notes welcome. Board coming — leave a comment."
        )
    sys_msg = _chat_append(
        session, role="system",
        text=open_ja if locale.startswith("ja") else open_en,
        name="Studio",
    )
    _publish_chat(sid, sys_msg)

    cast = _crew_ids(session)
    # Where and when, before anyone starts describing it.
    if _cast_in_role(cast, "plan"):
        await _run_plan_turn(db, ollama, session, cfg=cfg)
        await session_db.save(db, session, publish=False)

    seats = (_opening_seats(cast) if still_first
             else _writing_seats(cast))
    await _craft_pass(db, ollama, session, cast, seats, cfg=cfg)

    if still_first:
        session_db.log(session, "table", f"brief · {len(seats)} seats")
        return await request_board(
            db, comfy, spooler, session, ollama=ollama, still=True,
        )

    ask_ja = (
        "一通り集まりました。「②試し撮り」でイメージボード、「③本番」でこの台本のまま"
        "本番撮影です。まだならコメントをください — 班が答えます。"
    )
    ask_en = (
        "First pass done. Use \"test shot\" for an image board, \"final\" to "
        "shoot this craft, or leave a note and the crew will answer."
    )
    ask = _chat_append(
        session, role="system",
        text=ask_ja if locale.startswith("ja") else ask_en,
        name="Studio",
    )
    _publish_chat(sid, ask)
    session["status"] = "chat"
    session_db.log(session, "table", f"{len(cast)} muses")
    await session_db.save(db, session)
    return session


async def run_full_table(
    db, ollama, session: dict[str, Any], *, note: str = "",
) -> dict[str, Any]:
    """Act two: everyone else joins, looking at the still that came back."""
    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    cast = _crew_ids(session)
    locale = str(_inputs(session).get("locale") or "ja")
    session["status"] = "discussing"

    seats = _writing_seats(cast, without=OPENING_ROLES)
    if not seats:
        session["table_stage"] = "full"
        return session

    msg = _chat_append(
        session, role="system", name="Studio",
        text=(
            "全班入ります。スチールを見ながら詰めます。"
            if locale.startswith("ja") else
            "Full crew joining — they are working from the still."
        ),
    )
    _publish_chat(sid, msg)
    await session_db.save(db, session, publish=False)

    images = await board_images(db, session)
    await _craft_pass(
        db, ollama, session, cast, seats, cfg=cfg, images=images,
        screening=_screening_note(session) if images else "", note=note,
        # Offset so the banter rota does not restart on the same seats.
        first_index=len(OPENING_ROLES),
    )
    session["table_stage"] = "full"
    session_db.log(session, "table", f"full · {len(seats)} seats")
    await session_db.save(db, session, publish=False)
    return session


# ── 主演撮り (lead shoot) — one or two Muses, no crew ─────────────────────────
# Prep, test shot and approve are their own buttons (`duet_prep_stage`,
# `request_board`, `approve_and_shoot`). Everything typed here is
# conversation, which is the point: the eighteen-seat table is a production
# meeting you watch, and this is being in the room with her.


def is_duet(session: dict[str, Any]) -> bool:
    return str(session.get("mode") or "") == "duet"


def _memory_block(session: dict[str, Any]) -> str:
    """What she remembers of the last few shoots (sticky recaps + diary).

    Labelled with what it is not, for the reason REFERENCE is fenced: material
    handed over as plain text becomes something the picture has to contain, and
    last month's umbrella turns up in today's frame. It is here to colour how
    she meets the Showrunner, not to be described. Never handed to the scripter.
    """
    lines = [str(m).strip() for m in (session.get("memories") or []) if str(m).strip()]
    if not lines:
        return ""
    return "\n".join([
        "前に総監督と撮ったときのこと（あなたの手元メモ／日記から。覚えている、というだけ。"
        "今日の画に写すものではないし、SCENE に書くものでもない）:",
        *(f"- {m}" for m in lines[:3]),
    ])


def _cited_memories_block(session: dict[str, Any]) -> str:
    """Older shoot summaries retrieved for a recall turn — Muse only."""
    rows = [r for r in (session.get("cited_memories") or []) if isinstance(r, dict)]
    if not rows:
        return ""
    lines = []
    for r in rows[:3]:
        mid = str(r.get("id") or "")[:8]
        when = str(r.get("when") or "").strip()
        text = str(r.get("text") or memories_db.format_recap_text(r)).strip()
        if not text:
            continue
        label = f"[{mid}] {when} — {text}" if when else f"[{mid}] {text}"
        lines.append(f"- {label}")
    if not lines:
        return ""
    return "\n".join([
        "CITED_MEMORIES（このターンだけ。ここに無い細部は覚えていないと言う。"
        "捏造しない。今日の画の材料にしない）:",
        *lines,
    ])


_AFFIRM_RE = re.compile(
    r"^(いいね|それで|うん|よし|おｋ|ok|okay|yes|yeah|いいよ|それいい|"
    r"採用|それでいこう|その感じ)[!！。\.〜～\s]*$",
    re.I,
)
_RECALL_HINT_RE = re.compile(
    r"(この間|前回|前に|覚えてる|どうだった|あのとき|あの回|ずっと前)",
)


def _looks_like_affirm(text: str) -> bool:
    return bool(_AFFIRM_RE.match(str(text or "").strip()))


def _looks_like_recall(text: str) -> bool:
    return bool(_RECALL_HINT_RE.search(str(text or "")))


def _muse_names(session: dict[str, Any], partner_character: dict | None = None) -> tuple[str, str]:
    char_a = session.get("character") or {}
    name_a = str(char_a.get("name_ja") or char_a.get("name") or "私")
    name_b = ""
    if partner_character:
        name_b = str(
            partner_character.get("name_ja") or partner_character.get("name") or ""
        )
    return name_a, name_b


def _apply_compiled_craft(
    session: dict[str, Any], tags: str, craft_scene: str,
) -> bool:
    """Full-replace craft from a scripter compile. Returns False if refused."""
    tags = str(tags or "").strip()
    scene = str(craft_scene or "").strip()
    if not tags and not scene:
        return False
    # Refuse obviously broken gaze+angle stacks that mean the model merged
    # instead of rewriting FRAME as one story.
    low = tags.lower().replace(" ", "_")
    if ("from_below" in low or "low_angle" in low) and "looking_up" in low:
        logger.info("[muse] refusing compile with low-angle + looking_up")
        return False
    if ("from_above" in low or "high_angle" in low) and "looking_down" in low:
        # looking_down can be ok with high angle sometimes; keep soft — only
        # hard-refuse the known low+up failure mode above.
        pass
    craft = session.setdefault("craft", {})
    before = str(craft.get("tags") or "")
    craft["tags"] = tags
    craft["scene"] = scene
    craft["pose_intent"] = str((notebook_mod.of(session).get("beat") or ""))[:240]
    craft["prompt"] = identity.assemble_positive(
        _identity_tags(session), tags, scene,
        framing=_framing(_inputs(session)), style=_style(session),
        subject=identity.subject_tags(_cast(session)),
    )
    session["craft_dirty"] = identity.craft_is_thin(
        str(craft.get("prompt") or ""), scene,
    )
    session["notebook_rev_compiled"] = int(notebook_mod.of(session).get("rev") or 0)
    lead = crew.DEFAULT_MEMBER["actress"]
    record_ledger(
        session, muse_id="scripter", name="Scripter",
        before=before, after=tags, ms=0,
    )
    events.publish(session["session_id"], {
        "type": "craft_updated",
        "prompt": str(craft.get("prompt") or ""),
        "muse_id": lead,
    })
    return True


async def _run_duet_scripter(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """INTENT + absolute notebook patch + optional full craft compile."""
    notebook_mod.migrate(session)
    nb = notebook_mod.of(session)
    inputs = _inputs(session)
    partner_character = await _partner_character(db, session)
    name_a, name_b = _muse_names(session, partner_character)
    block = notebook_mod.render(nb, name_a=name_a, name_b=name_b)
    sid = session["session_id"]
    events.publish(sid, {
        "type": "scripter_working",
        "status": "updating",
    })
    result = await chain.run_scripter(
        ollama,
        notebook_block=block,
        note=text,
        theme=str(inputs.get("theme") or ""),
        style=_style(session),
        framing=_framing(inputs),
        partner=bool(partner_character),
        model=_text_model(inputs),
        num_ctx=_num_ctx(inputs, cfg),
    )
    intent = str(result.get("intent") or "casual")
    patch = dict(result.get("patch") or {})

    if _looks_like_affirm(text) and str(nb.get("open") or "").strip():
        notebook_mod.promote_open_to_wearing(nb)
        if intent == "casual":
            intent = "mixed"
        # Affirmation should compile even if the model only cleared OPEN.
        if "clear_open" not in patch:
            patch["clear_open"] = True

    notebook_mod.apply_patch(nb, patch)
    session["notebook"] = nb
    session["standing"] = list(nb.get("standing") or [])
    session["digest"] = notebook_mod.summary_for_muse(nb, name_a=name_a, name_b=name_b)

    want_recall = intent == "recall" or _looks_like_recall(text)
    session["cited_memories"] = []
    if want_recall:
        char_id = str(inputs.get("character_id") or "")
        try:
            session["cited_memories"] = await memories_db.search(
                db, ollama, character_id=char_id, query=text, limit=3,
            )
        except Exception:
            logger.debug("[muse] recall search failed", exc_info=True)
        intent = "recall" if intent == "casual" else intent

    compiled = False
    if intent in ("shot", "mixed"):
        tags = str(result.get("tags") or "")
        scene = str(result.get("craft_scene") or "")
        if tags or scene:
            compiled = _apply_compiled_craft(session, tags, scene)
            if not compiled:
                session["craft_dirty"] = True
        else:
            # Notebook moved but compile missing — keep prior craft, mark dirty.
            session["craft_dirty"] = True
        session.setdefault("notes", []).append(text)
    else:
        session["just_banned"] = []
        session["just_restored"] = []

    session["scripter_intent"] = intent
    events.publish(sid, {
        "type": "scripter_done",
        "intent": intent,
        "compiled": compiled,
        "notebook_rev": int(nb.get("rev") or 0),
    })
    return result


def _social_block(session: dict[str, Any]) -> str:
    """Lounge whispers — trends and friend feedback. Soft hints only."""
    lines = [str(m).strip() for m in (session.get("social_seeds") or []) if str(m).strip()]
    if not lines:
        return ""
    return "\n".join([
        "【なかまから聞いたこと（状況が合うときだけ）】",
        "楽屋で友達と話したことの覚え。お題やシチュエーションが合うときだけ、"
        "「今回はこれを試そうかな」程度に滲ませてよい。合わなければ無視。"
        "画の材料に無理に足さない。小道具の単語を増やさない。",
        *(f"- {m}" for m in lines[:5]),
    ])


def _handpost_block(session: dict[str, Any]) -> str:
    """Pinned studio handpost notices — short standing guidance."""
    lines = [str(m).strip() for m in (session.get("handpost_notices") or []) if str(m).strip()]
    if not lines:
        return ""
    return "\n".join([
        "【スタジオ手帖の周知（短く守る。画に無理に写さない）】",
        *(f"- {m}" for m in lines[:3]),
    ])


def _caught_block(session: dict[str, Any]) -> str:
    """The one-off line about her diary having been read, if one is owed."""
    caught = session.get("caught") or {}
    if not caught.get("ids"):
        return ""
    return crew.caught_block(str(caught.get("summary") or ""))


async def _consume_caught(db, session: dict[str, Any]) -> None:
    """She has said it. Never again for those entries.

    Called after the turn that carried the block, not before it: a turn that
    fell over must not spend the moment.
    """
    caught = session.get("caught") or {}
    ids = [str(i) for i in (caught.get("ids") or []) if i]
    if not ids:
        return
    session["caught"] = {}
    char_id = str(_inputs(session).get("character_id") or "")
    if char_id:
        try:
            await presets_db.mark_secret_banter_fired(db, char_id, ids)
        except Exception:
            logger.warning("[muse] could not mark diaries acknowledged", exc_info=True)


async def _after_actress_spoke(db, session: dict[str, Any]) -> None:
    """Spend one-shot memory that rode on the turn that just landed."""
    await _consume_caught(db, session)
    await _consume_social_seeds(db, session)


def _duet_user_prompt(session: dict[str, Any], text: str, *, prep: bool) -> str:
    """What she is handed. Muse-only context (never the scripter's inputs)."""
    inputs = _inputs(session)
    theme = str(inputs.get("theme") or "").strip()
    talk = "\n".join(
        f"- {'総監督' if m.get('role') == 'user' else '私'}: {m.get('text')}"
        for m in (session.get("chat") or [])[-12:]
        if m.get("role") in ("user", "muse")
    )
    parts = [f"お題（総監督が最初に言ったこと）:\n{theme}" if theme else ""]
    memories = _memory_block(session)
    if memories:
        parts.append(memories)
    cited = _cited_memories_block(session)
    if cited:
        parts.append(cited)
    social = _social_block(session)
    if social:
        parts.append(social)
    handpost = _handpost_block(session)
    if handpost:
        parts.append(handpost)
    caught = _caught_block(session)
    if caught:
        parts.append(caught)

    if uses_notebook(session):
        nb = notebook_mod.of(session)
        name_a = str(
            (session.get("character") or {}).get("name_ja")
            or (session.get("character") or {}).get("name") or "私"
        )
        summary = notebook_mod.summary_for_muse(nb, name_a=name_a)
        if summary:
            parts.append(
                "いまのショットノート（会話用の要約。タグではない。"
                "復唱チェックリストにしない）:\n" + summary
            )
        standing = notebook_mod.of(session).get("standing") or session.get("standing") or []
        if standing:
            parts.append(
                "STANDING（守ること）:\n"
                + "\n".join(f"- {s}" for s in standing if str(s).strip())
            )
    elif on_facets(session):
        orders = "\n\n".join(b for b in [
            directives_block(session),
            facets.standing_block(list(session.get("standing") or [])),
        ] if b)
        if orders:
            parts.append(orders)
    else:
        orders = brief_mod.orders_block(
            list(session.get("notes") or []),
            carried_out=list(session.get("carried_out") or []),
            removed_now=list(session.get("just_banned") or []),
            restored_now=list(session.get("just_restored") or []),
        )
        if orders:
            parts.append(orders)

    if talk:
        parts.append(f"ここまでの会話:\n{talk}")
    if text.strip():
        parts.append(f"総監督がいま言ったこと:\n{text.strip()}")

    if not prep:
        parts.append(
            "このターンの話し方:\n"
            "- 感覚・身体・相手の反応が先。変更点の事務報告は禁止。\n"
            "- 総監督のいちばん新しい発言が勝つ。"
            "言い直したら前の案は捨てる。\n"
            "- OPEN の提案はセリフで試してよい（未確定のまま）。\n"
            "- まだ開いている軸だけ、自分から具体案を一つ出す。\n"
            "- 過去の撮影は渡された記憶だけ。無いものは覚えてないと言う。\n"
            "- ボード画像があっても、それは古いテイク。文言の最新指示を優先。\n"
            "- 準備できた・用意して・get ready とは言わない。"
            "英語の見出しやルール名をセリフに出さない。"
        )
        return "\n\n".join(p for p in parts if p)

    # Prep on the notebook path is a densify readout, not a second compile.
    previous = str((session.get("craft") or {}).get("prompt") or "")
    if previous:
        parts.append(
            "いま載っている台本（仕上げのあと、感覚で読み上げる。タグの点呼は禁止）:\n"
            + previous
        )
    parts.append(
        "撮影準備の仕上げターンです。画の中身はすでにノートから載っています。"
        "SAY だけで、場所の空気・体の感触・カメラの距離を自分の言葉で伝えて。"
        "小物の在庫読み上げや「変更しました」報告はしない。"
    )
    return "\n\n".join(p for p in parts if p)


def _facet_prep_prompt(
    session: dict[str, Any], names: list[str],
    *, partner_character: dict[str, Any] | None = None,
) -> str:
    """What she is handed to rewrite some parts of the shot.

    Deliberately short, and deliberately the same length on turn twenty as on
    turn three. What is NOT here is the point: no transcript, no previous
    assembled positive, no append-only order list. Those are what a long session
    drowned in — the prep turn was handed twelve raw chat turns, every standing
    order ever given, and the whole of the last prompt, and asked to work out
    which parts of that were still true.

    The shot is state now. The table says what the picture is; the direction
    says what the Showrunner last asked of each part; the rest is conversation
    and belongs to talk mode.
    """
    inputs = _inputs(session)
    theme = str(inputs.get("theme") or "").strip()
    table = facets.table_of(session)
    opening = not facets.table_rev(table)

    char_a = session.get("character") or {}
    name_a = str(char_a.get("name_ja") or char_a.get("name") or "")
    name_b = ""
    label_names: dict[str, str] | None = None
    if partner_character:
        name_b = str(partner_character.get("name_ja") or partner_character.get("name") or "")
        label_names = {"costume_b": name_b, "pose_b": name_b, "expression_b": name_b}

    parts = [
        f"お題（総監督が最初に言ったこと）:\n{theme}" if theme else "",
        f"Style: {_style(session)}",
        f"Framing: {_framing(inputs)}",
    ]
    digest = str(session.get("digest") or "").strip()
    if digest:
        # Shown to EVERY facet-writing turn, whether or not the router named
        # this facet today — this is what closes the gap a routed directive
        # alone cannot: the part being rewritten right now may hold a stale
        # duplicate of something decided against on some earlier, unrelated
        # turn, and only a standing reminder like this one reaches it. Placed
        # ahead of "いまの画" on purpose: read this first, then the snapshot.
        parts.append("ここまでの決定（会話の細部より、これを優先して読むこと）:\n"
                     + digest)
    if not opening:
        parts.append("いまの画（すでに決まっている部分）:\n"
                     + facets.table_block(table, names=label_names))
    orders = directives_block(session, only=names if not opening else None)
    if orders:
        parts.append(orders)
    standing = facets.standing_block(list(session.get("standing") or []))
    if standing:
        parts.append(standing)

    def _label(n: str) -> str:
        if n == "costume_b":
            return f"{name_b}の衣装"
        if n == "pose_b":
            return f"{name_b}のポーズ"
        if n == "expression_b":
            return f"{name_b}の表情"
        return facets.FACET_LABELS[n]

    labels = "・".join(_label(n) for n in names)
    if opening and partner_character:
        parts.append(
            f"この一枚を、{name_a}と{name_b}の二人で全部決めて。場所・時間・光・"
            "小物・カメラは二人共通、衣装・ポーズ・表情はそれぞれ自分の分だけ"
            f"（{name_a}は{name_a}の、{name_b}は{name_b}の）を決めて。"
            "決めたら SAY でフレームに何が入っているかをそれぞれ自分の言葉で"
            "読み上げて。小物は名前で。隠さないこと。"
        )
    elif opening:
        parts.append(
            "この一枚を、全部あなたが決めて。場所・時間・光・小物・衣装・ポーズ・"
            "表情・カメラ。決めたら SAY でフレームに何が入っているかを自分の言葉で"
            "読み上げて。小物は名前で。隠さないこと。"
        )
    else:
        parts.append(
            f"総監督の指示で変わるのは {labels} だけ。そこだけ書き直して。\n"
            "ほかの部分はもう決まっている。書き直さないし、触れない"
            "（書いても捨てられる）。\n"
            "SAY では、何をどう変えたか、捨てたものも含めて自分の言葉で言って。"
        )
    return "\n\n".join(p for p in parts if p)


async def _duet_talk(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
    prep: bool = False,
) -> dict[str, Any]:
    """Conversation only — Muse writes SAY; craft comes from the scripter."""
    inputs = _inputs(session)
    sid = session["session_id"]
    lead = crew.DEFAULT_MEMBER["actress"]
    name = _muse_display_name(session, lead)
    events.publish(sid, {"type": "muse_speaking", "muse_id": lead, "name": name})
    images = await board_images(db, session)

    partner_character = await _partner_character(db, session)
    tier = await _duet_tier(db, session, partner_character)

    try:
        say, raw_turns, blind = await chain.run_duet_talk(
            ollama,
            user_prompt=_duet_user_prompt(session, text, prep=prep),
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            character=session.get("character") or {},
            partner_character=partner_character,
            images=images or None, seed=str(sid),
            on_token=_token_publisher(sid, lead),
            tier=tier,
        )
    except chain.ChainError as exc:
        raise MuseError(_msg(
            session,
            ja="うまく言葉が出てこないみたいです。もう一度話しかけてください。",
            en="The words didn't come out right. Try talking to her again.",
        )) from exc
    if blind and images:
        _note_blind(session)
    msg = _chat_append(session, role="muse", text=say, muse_id=lead,
                       name=name, kind="craft", turns=_resolve_duet_turns(session, raw_turns))
    _publish_chat(sid, msg)
    session["status"] = "chat"
    await _after_actress_spoke(db, session)
    await session_db.save(db, session)
    return session


def _facets_to_write(session: dict[str, Any]) -> list[str]:
    """Which parts this prep turn rewrites.

    Everything unlocked on the opening — there is no shot yet. After that, only
    what the Showrunner's direction has actually touched since the last prep,
    which is what makes an untouched part untouched by construction rather than
    by the model remembering to leave it alone.
    """
    table = facets.table_of(session)
    partner = bool(str(_inputs(session).get("partner_preset") or "").strip())
    opening_set = facets.ALL_FACETS if partner else facets.FACETS
    unlocked = [n for n, _ in opening_set if not table[n].get("locked")]
    if not facets.table_rev(table):
        return unlocked
    routed = [n for n in (session.get("routed") or []) if n in unlocked]
    return routed


def _apply_facet_turn(
    session: dict[str, Any], written: dict[str, dict[str, Any]], *,
    say: str, muse_id: str, ms: int = 0,
    turns: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    """Write the parts this turn rewrote, and nothing else.

    The ledger still records a before/after over the whole tag list, so
    `report.py` and the panel are unaffected — from outside this looks like any
    other turn that changed the craft.
    """
    before = str((session.get("craft") or {}).get("tags") or "")
    blocked: list[str] = []
    for name, slot in written.items():
        report = facets.write(
            session, name,
            tags=slot.get("tags"), nl=slot.get("nl"),
            fields=slot.get("fields"), by=muse_id,
        )
        blocked.extend(n for n in report["blocked"] if n not in blocked)
    # Two parts of the shot disagreeing, where the one that would have yielded
    # is pinned. The pin wins and the panel gets to say so — a change that
    # silently did not take is the thing the Showrunner cannot debug.
    session["facet_conflicts"] = blocked
    _reassemble(session)
    record_ledger(
        session, muse_id=muse_id, name=_muse_display_name(session, muse_id),
        before=before, after=str(session["craft"].get("tags") or ""), ms=ms,
    )
    spoken = session.setdefault("spoken", [])
    if muse_id not in spoken:
        spoken.append(muse_id)
    # The direction has been carried out. Leaving it on the list is how a note
    # answered three turns ago went on being answered.
    session["routed"] = []
    _rebuild_brief(session)

    name = _muse_display_name(session, muse_id)
    msg = _chat_append(
        session, role="muse", text=say or f"（{name}が台本を更新した。）",
        muse_id=muse_id, name=name, kind="craft",
        turns=_resolve_duet_turns(session, turns),
    )
    _publish_chat(session["session_id"], msg)
    events.publish(session["session_id"], {
        "type": "craft_updated",
        "prompt": str(session["craft"].get("prompt") or ""),
        "muse_id": muse_id,
    })
    return msg


async def _duet_prep_facets(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """The prep turn, scoped to the parts the Showrunner actually changed."""
    inputs = _inputs(session)
    sid = session["session_id"]
    lead = crew.DEFAULT_MEMBER["actress"]
    names = _facets_to_write(session)
    if not names:
        # Nothing was asked for, so nothing is rewritten. Saying "she rebuilt
        # the shot" here is how an untouched part got touched.
        session["status"] = "chat"
        await session_db.save(db, session)
        return session

    session["status"] = "discussing"
    await session_db.save(db, session)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": lead,
        "name": _muse_display_name(session, lead),
    })
    images = await board_images(db, session)
    started = time.monotonic()
    opening = not facets.table_rev(facets.table_of(session))
    partner_character = await _partner_character(db, session)
    if partner_character:
        tier = await _duet_tier(db, session, partner_character)
        system = crew.w_actress_duet_prompt(
            session.get("character") or {}, partner_character, mode="prep",
            base_style=_style(session), seed=str(sid), tier=tier,
            facets=names, opening=opening,
        )
    else:
        system = crew.actress_duet_prompt(
            session.get("character") or {}, mode="prep",
            base_style=_style(session), seed=str(sid),
            facets=names, opening=opening,
        )

    try:
        say, written, blind = await chain.run_duet_facets(
            ollama,
            user_prompt=_facet_prep_prompt(session, names, partner_character=partner_character),
            system=system,
            allowed=names,
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            images=images or None,
            on_token=_token_publisher(sid, lead),
        )
    except chain.ChainError as exc:
        session["status"] = "chat"
        await session_db.save(db, session)
        raise MuseError(_msg(
            session,
            ja="台本がうまく組めませんでした。もう少し話してから試してください。",
            en="Couldn't put the shot together. Talk it through a bit more and try again.",
        )) from exc
    if blind and images:
        _note_blind(session)
    _apply_facet_turn(
        session, written, say=say, muse_id=lead,
        ms=int((time.monotonic() - started) * 1000),
    )
    session["status"] = "chat"
    await _after_actress_spoke(db, session)
    await session_db.save(db, session)
    return session


async def _duet_prep_notebook(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """①撮影準備 — densify polish + sensory readout. Not the gate for craft."""
    notebook_mod.migrate(session)
    craft = session.get("craft") or {}
    if not str(craft.get("prompt") or "").strip():
        # Nothing live yet — ask the scripter once from the notebook/theme.
        theme = str(_inputs(session).get("theme") or "").strip()
        seed_note = theme or "お題から撮る画のたたきを組んで"
        await _run_duet_scripter(db, ollama, session, seed_note, cfg=cfg)

    session["status"] = "discussing"
    await session_db.save(db, session)
    session = await densify_craft_if_needed(db, ollama, session)
    session["craft_dirty"] = False

    # Sensory SAY only — muse must not rewrite tags on prep anymore.
    return await _duet_talk(db, ollama, session, "", cfg=cfg, prep=True)


async def _duet_prep(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """Prep button: notebook densify path for duet; legacy paths otherwise."""
    if uses_notebook(session):
        return await _duet_prep_notebook(db, ollama, session, cfg=cfg)
    if on_facets(session):
        return await _duet_prep_facets(db, ollama, session, cfg=cfg)
    inputs = _inputs(session)
    sid = session["session_id"]
    lead = crew.DEFAULT_MEMBER["actress"]
    session["status"] = "discussing"
    await session_db.save(db, session)

    events.publish(sid, {
        "type": "muse_speaking", "muse_id": lead,
        "name": _muse_display_name(session, lead),
    })
    images = await board_images(db, session)
    started = time.monotonic()

    partner_character = await _partner_character(db, session)
    tier = await _duet_tier(db, session, partner_character)

    try:
        turn = await chain.run_duet_prep(
            ollama,
            user_prompt=_duet_user_prompt(session, text, prep=True),
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            identity_tags=_identity_tags(session),
            framing=_framing(inputs),
            brief=str(session.get("brief") or ""),
            character=session.get("character") or {},
            partner_character=partner_character,
            style=_style(session), cast=_cast(session),
            images=images or None, seed=str(sid),
            on_token=_token_publisher(sid, lead),
            tier=tier,
        )
    except chain.ChainError as exc:
        session["status"] = "chat"
        await session_db.save(db, session)
        raise MuseError(_msg(
            session,
            ja="台本がうまく組めませんでした。もう少し話してから試してください。",
            en="Couldn't put the shot together. Talk it through a bit more and try again.",
        )) from exc
    if turn.blind and images:
        _note_blind(session)
    _apply_turn(session, turn, ms=int((time.monotonic() - started) * 1000))
    session["status"] = "chat"
    await _after_actress_spoke(db, session)
    await session_db.save(db, session)
    return session


async def duet_prep_stage(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """①撮影準備 — densify / readout. Live craft already comes from chat."""
    cfg = await get_runtime_config(db)
    return await _duet_prep(db, ollama, session, "", cfg=cfg)


async def post_duet_chat(
    db, ollama, session: dict[str, Any], text: str,
) -> dict[str, Any]:
    """One turn of the two-hander: scripter updates the notebook, then she talks.

    Board / prep / shoot stay their own buttons. Picture changes compile live —
    prep is not the gate.
    """
    sid = session["session_id"]
    user_msg = _chat_append(session, role="user", text=text, name="総監督")
    _publish_chat(sid, user_msg)
    await session_db.save(db, session)

    cfg = await get_runtime_config(db)
    if uses_notebook(session):
        try:
            await _run_duet_scripter(db, ollama, session, text, cfg=cfg)
        except Exception:
            logger.warning("[muse] scripter failed; muse still talks", exc_info=True)
            session["craft_dirty"] = True
        await session_db.save(db, session)
        return await _duet_talk(db, ollama, session, text, cfg=cfg)

    # Legacy non-notebook path (should be rare).
    named, _ = await route_note(db, ollama, session, text, cfg=cfg)
    if not named:
        await take_note(db, ollama, session, text, cfg=cfg)
    else:
        session.setdefault("notes", []).append(text)
        session["just_banned"] = []
        session["just_restored"] = []
    await session_db.save(db, session)
    return await _duet_talk(db, ollama, session, text, cfg=cfg)


async def start_duet(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """Open the two-hander. She speaks first, about the theme, and that is all."""
    missing = [m for m in missing_inputs(session) if m != "workflow"]
    if missing:
        raise MuseError(_msg(
            session,
            ja=f"入力が不足しています: {', '.join(missing)}",
            en=f"missing: {', '.join(missing)}",
        ))
    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    session["mode"] = "duet"
    session["status"] = "discussing"
    session["chat"] = []
    session["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    session["ledger"] = []
    session["banned"] = []
    session["carried_out"] = []
    session["spoken"] = []
    session["board"] = {}
    session["shoot"] = {}
    session["plan"] = {}
    session["costume"] = {}
    session["notes"] = []
    session["notebook"] = notebook_mod.blank(
        partner=bool(str(_inputs(session).get("partner_preset") or "").strip())
    )
    session["craft_dirty"] = False
    session["cited_memories"] = []
    session.pop("_blind_said", None)
    await _load_actress_memory(db, session)
    await session_db.save(db, session)
    session_db.log(session, "duet", "opened")
    return await _duet_talk(db, ollama, session, "", cfg=cfg)


async def _recent_memories(db, session: dict[str, Any], limit: int = 3) -> list[str]:
    """Sticky shoot recaps first, then diary summaries — Muse prompt only."""
    inputs = _inputs(session)
    char_id = str(inputs.get("character_id") or "")
    if not char_id:
        return []
    out: list[str] = []
    try:
        for recap in await presets_db.get_shoot_recaps(db, char_id, limit=limit):
            text = memories_db.format_recap_text(recap)
            if text:
                out.append(text)
    except Exception:
        logger.debug("[muse] shoot_recaps load failed", exc_info=True)
    if len(out) >= limit:
        return out[:limit]
    entries = await presets_db.get_recent_diary_summaries(
        db, char_id, limit=limit - len(out),
    )
    ja = str(inputs.get("locale") or "ja").startswith("ja")
    for e in entries:
        text = str(
            (e.get("summary_ja") if ja else e.get("summary_en"))
            or e.get("summary") or ""
        ).strip()
        if text:
            out.append(text)
    return out[:limit]


def _recap_from_snapshot(session: dict[str, Any]) -> dict[str, Any]:
    snap = session.get("continuity_snapshot") or {}
    nb = snap.get("notebook") or {}
    theme = str(snap.get("theme") or _inputs(session).get("theme") or "").strip()
    when = str(nb.get("atmosphere") or nb.get("scene") or theme or "").strip()[:160]
    feel = str(nb.get("vibe") or "").strip()[:200]
    shot = " / ".join(
        p for p in (
            str(nb.get("wearing") or "").strip(),
            str(nb.get("beat") or "").strip(),
            str(nb.get("frame") or "").strip(),
        ) if p
    )[:280]
    liked = str(nb.get("open") or "").strip()[:160]
    return {
        "when": when or theme or "撮影",
        "feel": feel,
        "liked": liked,
        "shot": shot or str(snap.get("craft_tags") or "")[:200],
        "session_id": str(session.get("session_id") or ""),
        "timestamp": time.time(),
    }


async def record_shoot_continuity(db, session: dict[str, Any], ollama=None) -> None:
    """After a successful ③ take: sticky recap + embed overflow into muse_memories."""
    if not uses_notebook(session) and not session.get("continuity_snapshot"):
        # Still record a light recap for duet even if snapshot missing.
        if not is_duet(session):
            return
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    if (session.get("continuity") or {}).get("written_at"):
        return
    recap = _recap_from_snapshot(session)
    try:
        overflow = await presets_db.push_shoot_recap(db, char_id, recap)
    except Exception:
        logger.warning("[muse] sticky recap failed", exc_info=True)
        overflow = None
    if overflow is not None and ollama is not None:
        try:
            await memories_db.upsert_summary(
                db, ollama, character_id=char_id, recap=overflow,
                session_id=str(overflow.get("session_id") or ""),
            )
        except Exception:
            logger.warning("[muse] embed overflow recap failed", exc_info=True)
    session["continuity"] = {"written_at": time.time()}
    await session_db.save(db, session, publish=False)


async def _load_actress_memory(db, session: dict[str, Any]) -> None:
    """Read sticky recaps / diary once per session — Muse only, never scripter.

    Once, at the open, rather than on every turn: it is a Qdrant round trip and
    neither answer can change mid-session. Both the two-hander and the
    eighteen-seat table call this; the table only ever fed the crew the brief,
    so she used to walk into it having forgotten every shoot she had written
    about.
    """
    session["memories"] = await _recent_memories(db, session)
    session["caught"] = {}
    await _load_social_seeds(db, session)
    await _load_handpost_notices(db, session)
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    try:
        caught = await presets_db.get_unacknowledged_read_diaries(db, char_id)
    except Exception:
        logger.debug("[muse] could not read diary acknowledgements", exc_info=True)
        return
    if not caught:
        return
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    newest = caught[0]
    session["caught"] = {
        "ids": [str(d.get("id") or "") for d in caught if d.get("id")],
        "summary": str(
            (newest.get("summary_ja") if ja else newest.get("summary_en"))
            or newest.get("summary") or ""
        ).strip(),
    }


async def _load_social_seeds(db, session: dict[str, Any]) -> None:
    """Lounge whispers for this open. Uses are spent after the first actress turn."""
    session["social_seeds"] = []
    session["social_seed_ids"] = []
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    try:
        seeds = await presets_db.get_social_seeds(db, char_id)
    except Exception:
        logger.debug("[muse] could not load social seeds", exc_info=True)
        return
    lines: list[str] = []
    ids: list[str] = []
    for seed in seeds:
        text = str(
            (seed.get("summary_ja") if ja else seed.get("summary_en"))
            or seed.get("summary_ja") or seed.get("summary_en") or ""
        ).strip()
        if not text:
            continue
        stance = str(seed.get("stance") or "try")
        if stance == "twist":
            text = f"{text}（自分なりにアレンジしてもいい）"
        elif stance == "skip":
            text = f"{text}（無理ならパスしてよい）"
        lines.append(text)
        if seed.get("id"):
            ids.append(str(seed["id"]))
    session["social_seeds"] = lines
    session["social_seed_ids"] = ids


async def _consume_social_seeds(db, session: dict[str, Any]) -> None:
    """Spend the whispers that coloured this session — once, after she speaks."""
    ids = [str(i) for i in (session.get("social_seed_ids") or []) if i]
    if not ids:
        return
    session["social_seed_ids"] = []
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    try:
        await presets_db.consume_social_seeds(db, char_id, ids)
    except Exception:
        logger.debug("[muse] could not consume social seeds", exc_info=True)


async def _load_handpost_notices(db, session: dict[str, Any]) -> None:
    session["handpost_notices"] = []
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    try:
        session["handpost_notices"] = await handpost_db.pinned_notice_lines(db, ja=ja, limit=3)
    except Exception:
        logger.debug("[muse] could not load handpost notices", exc_info=True)


# ── showrunner message ──────────────────────────────────────────────────────
async def post_chat(
    db, ollama, comfy, spooler, session: dict[str, Any], text: str,
) -> dict[str, Any]:
    """Showrunner speaks — always creative direction. Board and shoot are their
    own buttons (`request_board` / `approve_and_shoot`), not words in here."""
    text = (text or "").strip()
    if not text:
        raise MuseError(_msg(session, ja="メッセージが空です。", en="empty message"))
    if missing_inputs(session):
        raise MuseError(_msg(
            session,
            ja=f"入力が不足しています: {', '.join(missing_inputs(session))}",
            en=f"missing: {', '.join(missing_inputs(session))}",
        ))
    if is_duet(session):
        return await post_duet_chat(db, ollama, session, text)
    if not (session.get("craft") or {}).get("prompt"):
        # Auto-open table if they chat first.
        session = await start_table(
            db, ollama, session, comfy=comfy, spooler=spooler,
        )

    sid = session["session_id"]
    user_msg = _chat_append(session, role="user", text=text, name="総監督")
    _publish_chat(sid, user_msg)
    await session_db.save(db, session)

    # The still is up and only three seats have spoken: this is the note the
    # rest of the crew has been waiting for. Whatever it says, the full table
    # meets once, first — otherwise a note lands after a prompt three seats
    # already wrote, unanswered.
    if str(session.get("table_stage") or "full") == "brief":
        cfg = await get_runtime_config(db)
        await take_note(db, ollama, session, text, cfg=cfg)
        await session_db.save(db, session)
        if _cast_in_role(_crew_ids(session), "plan"):
            await _run_plan_turn(db, ollama, session, cfg=cfg, note=text)
            await session_db.save(db, session, publish=False)
        session = await run_full_table(db, ollama, session, note=text)
        locale = str(_inputs(session).get("locale") or "ja")
        wrap = _chat_append(
            session, role="system", name="Studio",
            text=(
                "全班そろいました。「②試し撮り」でイメージボード、「③本番」で撮影に"
                "入れます。まだ詰めるならコメントをどうぞ。"
                if locale.startswith("ja") else
                "Full crew is in. Use \"test shot\" for a screening, \"final\" to "
                "shoot, or keep the notes coming."
            ),
        )
        _publish_chat(sid, wrap)
        session["status"] = "chat"
        await session_db.save(db, session)
        return session

    # Crew answers the hard note — pick specialists by keyword, else core desk.
    cast = _crew_ids(session)
    # Anyone brought in since the read-through has never seen the script. They
    # go first, and they get the note too — a seat cast halfway through is
    # usually cast *because* of the note.
    fresh = newcomers(session, cast)
    responders = fresh + [m for m in _pick_responders(text, cast) if m not in fresh]
    session["status"] = "discussing"
    cfg = await get_runtime_config(db)
    # The note is standing direction from here on, not a remark about one turn —
    # and whatever it refuses comes out of the picture before anyone answers it.
    await take_note(db, ollama, session, text, cfg=cfg)
    await session_db.save(db, session)

    # Re-settle where and when first: a note like "make it somewhere else" has
    # to move the locked place, or the original theme keeps winning downstream.
    if _cast_in_role(cast, "plan"):
        await _run_plan_turn(db, ollama, session, cfg=cfg, note=text)
        await session_db.save(db, session, publish=False)

    # Once a board exists the crew answers while looking at it, which is the
    # only thing in the loop that can tell them the frame is already too dark or
    # that the place they wrote never made it into the picture.
    images = await board_images(db, session)
    screening = _screening_note(session) if images else ""

    last_responder = ""
    last_say = ""
    for muse_id in responders:
        turn, ms = await _run_muse_turn(
            ollama, session, muse_id,
            _table_user_prompt(
                session, muse_id=muse_id, note=text, screening=screening,
            ),
            cfg=cfg, images=images,
        )
        msg = _apply_turn(session, turn, ms=ms)
        if turn.blind and images:
            _note_blind(session)
            images = []
            screening = ""
        last_responder = muse_id
        last_say = str(msg.get("text") or "")
        if crew.role_of(muse_id) == "actress":
            await _after_actress_spoke(db, session)
        await session_db.save(db, session, publish=False)

    # One heckle after the note — prefer the actress so personality stays in chat.
    if last_responder and last_say and _banter_mode(session) != "off":
        heckler = None
        if "actress" in cast and "actress" not in responders:
            heckler = "actress"
        else:
            heckler = _pick_banter_reactor(
                session, cast, current=last_responder,
                previous=responders[0] if responders else None, index=1,
            )
        if heckler and heckler != last_responder:
            await _run_banter(
                ollama, session, heckler,
                about_id=last_responder, about_text=last_say, cfg=cfg,
            )
            await session_db.save(db, session, publish=False)

    locale = str(_inputs(session).get("locale") or "ja")
    wrap = _chat_append(
        session, role="system",
        name="Studio",
        text=(
            "反映しました。「②試し撮り」でイメージボード、「③本番」で撮影に入れます。"
            "まだ詰めるなら続けてどうぞ。"
            if locale.startswith("ja") else
            "Applied. Use \"test shot\" for a screening, \"final\" to shoot, "
            "or keep notes coming."
        ),
    )
    _publish_chat(sid, wrap)
    session["status"] = "chat"
    await session_db.save(db, session)
    return session


# How many never-spoken seats can catch up on one note. The cast is editable at
# any time, so somebody swapping a preset mid-session could otherwise queue a
# dozen turns behind one remark.
MAX_CATCHUP = 4


def newcomers(session: dict[str, Any], crew_ids: list[str]) -> list[str]:
    """Cast members who write craft and have not written any yet, in table order.

    The casting drawer used to be frozen the moment the table opened, so this
    could not happen. Now that「今日は照明いいや」works mid-session, the reverse is
    also true: bring lighting back and it has never read the script. Letting it
    answer a note without a first pass gives an opinion on a scene it has not
    been told about.
    """
    already = set(session.get("spoken") or [])
    return [m for m in _writing_seats(crew_ids) if m not in already][:MAX_CATCHUP]


def _pick_responders(note: str, crew_ids: list[str]) -> list[str]:
    """Fixed short desk for every showrunner note.

    Do NOT branch on mood or situation keywords in Python.
    The note is already injected into the VLM user prompt — specialists read it
    in dialogue and revise craft. Python only caps turn count for local Ollama.
    """
    _ = note  # intentional: routing ignores note text; VLM interprets it
    # Stable priority. Wardrobe leads, for the same reason it opens the
    # read-through: dress her, then frame her (OPENING_SEQUENCE). It used to sit
    # fifth against a cap of four, so on the standard preset the one seat that
    # owns clothes never answered a note — and COSTUME tells every other seat the
    # outfit is locked and only the Showrunner may change it. The Showrunner's
    # note then reached a table with nobody who could act on it: the lock
    # swallowed the instruction and `strike_dropped_costume` could never fire.
    #
    # Lens gives up the seat rather than the desk growing, because the cap is
    # what keeps a note affordable on local Ollama. The camera is the cheaper
    # loss — its tags are writable by any seat, while COSTUME is locked to this
    # one, so nobody else can stand in for Wardrobe.
    #
    # Banter-only seats are not here: the Producer answered every note by
    # restating the beat with `dynamic_composition` on it.
    priority = tuple(
        r for r in (
            "wardrobe", "actress", "beat", "spine", "lens",
            "faces", "gaffer", "propshop",
        ) if r not in crew.BANTER_ONLY
    )
    ordered = [m for m in (_cast_in_role(crew_ids, r) for r in priority) if m][:4]
    closer = _cast_in_role(crew_ids, "finisher")
    if closer:
        ordered.append(closer)
    if ordered:
        return ordered
    head = [crew_ids[0]] if crew_ids else []
    if closer and closer not in head:
        head.append(closer)
    return head


# ── image board ─────────────────────────────────────────────────────────────
async def _maybe_unload(ollama, session: dict[str, Any]) -> None:
    inputs = _inputs(session)
    if ollama is None or not bool(inputs.get("unload_vlm")):
        return
    model = str(inputs.get("model") or "") or None
    try:
        await ollama.unload(model)
    except Exception:
        logger.debug("[muse] unload_vlm failed", exc_info=True)


def _densify_user_prompt(session: dict[str, Any], *, screening: str = "") -> str:
    """Force Finisher to thicken a thin craft before Comfy sees it."""
    craft = session.get("craft") or {}
    closer = crew.DEFAULT_MEMBER["finisher"]
    base = _table_user_prompt(session, muse_id=closer, screening=screening)
    must = [str(m) for m in ((session.get("plan") or {}).get("must_appear") or [])]
    ledger = (
        "- Every one of these must be in SCENE: " + ", ".join(must) + "\n"
        if must else
        "- ≥10 place objects implied by THIS theme.\n"
    )
    return (
        f"{base}\n\n"
        "DENSITY PACK (mandatory — SCENE was thin):\n"
        "- Expand SCENE to 140–200 English words covering pose, cloth, place objects,\n"
        "  light/atmosphere, camera, personality in eyes/hands.\n"
        f"{ledger}"
        "- TAGS: 35–55 strong tags. Do not shrink.\n"
        "- Keep the same moment and theme. Densify, do not restart or relocate.\n"
        "- Do not inject props/outfits from a different situation.\n"
        "- Do not change the light level while densifying.\n"
        f"- Current SCENE word count: {identity.word_count(str(craft.get('scene') or ''))}.\n"
        f"- Current positive word count: {identity.word_count(str(craft.get('prompt') or ''))}."
    )


async def compose_scene_if_needed(
    db, ollama, session: dict[str, Any],
) -> dict[str, Any]:
    """Render the facet table into prose, once per version of the shot.

    This is the step that makes the parts read as one picture. It is a pure
    function of the table: the prompt is the table and the standing rules and
    nothing else — no chat, no theme, no brief, no previous prompt. Composing
    was never what went wrong; being handed twenty turns of contradicting
    history was, and a composer with no history cannot be confused by one.

    Skipped when the table has not moved since the last composition, so an
    unchanged shot costs nothing. Falls back to the joined facet sentences
    whenever the composition cannot be trusted — the shot still renders.
    """
    if ollama is None or not on_facets(session):
        return session
    table = facets.table_of(session)
    rev = facets.table_rev(table)
    if not rev:
        return session
    composed = session.get("composed") or {}
    if int(composed.get("rev") or -1) == rev and str(composed.get("scene") or ""):
        return session

    cfg = await get_runtime_config(db)
    inputs = _inputs(session)
    partner_character = await _partner_character(db, session)
    name_a = ""
    name_b = ""
    if partner_character:
        char_a = session.get("character") or {}
        name_a = str(char_a.get("name_ja") or char_a.get("name") or "")
        name_b = str(partner_character.get("name_ja") or partner_character.get("name") or "")
    try:
        scene = await chain.run_compose(
            ollama,
            table_block=facets.table_block(table),
            standing=facets.standing_block(list(session.get("standing") or [])),
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            name_a=name_a, name_b=name_b,
        )
    except chain.ChainError:
        logger.warning("[muse] compose failed; rendering the joined parts",
                       exc_info=True)
        return session

    usable, _ = facets.warn_invented_nouns(
        table, scene, banned=banned_tags(session),
        extra=[_style(session), str((session.get("character") or {}).get("name") or "")],
    )
    if scene and usable:
        session["composed"] = {"scene": scene, "rev": rev, "at": time.time()}
        _reassemble(session)
    await session_db.save(db, session, publish=False)
    return session


async def densify_craft_if_needed(
    db, ollama, session: dict[str, Any],
) -> dict[str, Any]:
    """Run Finisher once when craft is too thin (or notebook-marked dirty)."""
    if ollama is None:
        return session
    # Legacy facet path composes instead of densifying. Notebook-primary duet
    # keeps draft tags/scene from the scripter and thickens via Finisher.
    if on_facets(session) and not uses_notebook(session):
        return await compose_scene_if_needed(db, ollama, session)
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    scene = str(craft.get("scene") or "")
    if not prompt:
        return session
    dirty = bool(session.get("craft_dirty"))
    if not dirty and not identity.craft_is_thin(prompt, scene):
        return session
    cfg = await get_runtime_config(db)
    # Notebook path: ask the scripter to thicken from the notebook. A Finisher
    # seat turn that returns only SAY would wipe a live compile — never do that.
    if uses_notebook(session):
        before_tags = str(craft.get("tags") or "")
        try:
            result = await chain.run_scripter(
                ollama,
                notebook_block=notebook_mod.render(notebook_mod.of(session)),
                note=(
                    "DENSIFY: expand TAGS (35–55) and CRAFT_SCENE (140–200 words) "
                    "from the WHOLE notebook. Keep absolute values. INTENT: shot."
                ),
                theme=str(_inputs(session).get("theme") or ""),
                style=_style(session),
                framing=_framing(_inputs(session)),
                partner=bool(str(_inputs(session).get("partner_preset") or "").strip()),
                model=_text_model(_inputs(session)),
                num_ctx=_num_ctx(_inputs(session), cfg),
            )
            tags = str(result.get("tags") or "")
            scene_out = str(result.get("craft_scene") or "")
            if tags or scene_out:
                ok = _apply_compiled_craft(
                    session, tags or before_tags, scene_out or scene,
                )
                if ok:
                    session["craft_dirty"] = False
        except Exception:
            logger.warning("[muse] notebook densify failed; keeping draft",
                           exc_info=True)
        await session_db.save(db, session, publish=False)
        return session

    locale = str(_inputs(session).get("locale") or "ja")
    note = _chat_append(
        session, role="system", name="Studio",
        text=(
            "台本が薄いのでフィニッシャーが密度を上げます（のっぺり防止）。"
            if locale.startswith("ja") else
            "Craft is thin — Finisher is densifying before render."
        ),
    )
    _publish_chat(session["session_id"], note)
    images = await board_images(db, session)
    try:
        turn, ms = await _run_muse_turn(
            ollama, session, "finisher",
            _densify_user_prompt(
                session, screening=_screening_note(session) if images else "",
            ),
            cfg=cfg, images=images,
        )
        # Refuse a wipe: densify must not replace a real compile with empty tags.
        if str(turn.tags or "").strip() or str(turn.scene or "").strip():
            _apply_turn(session, turn, ms=ms)
        if turn.blind and images:
            _note_blind(session)
    except chain.ChainError:
        logger.warning("[muse] densify failed; rendering thin craft", exc_info=True)
    await session_db.save(db, session, publish=False)
    return session


async def request_board(
    db, comfy, spooler, session: dict[str, Any], ollama=None, *, still: bool = False,
) -> dict[str, Any]:
    """One render for the crew and the Showrunner to look at.

    `still` is the opening frame, shot off three seats before the rest of the
    crew has said anything: one image rather than four, because at that point
    there is not enough craft for four to differ, and the whole point is to get
    something on the wall fast.
    """
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    if not prompt:
        # Live compile usually fills this from chat; prep can also seed it.
        raise MuseError(_msg(
            session,
            ja="まだ台本がありません。服や場所など画の指示を会話で出してください。",
            en="No script yet — describe the shot in chat (clothes, place, camera).",
        ))

    if not still:
        session = await densify_craft_if_needed(db, ollama, session)
        session["craft_dirty"] = False
        craft = session.get("craft") or {}
        prompt = str(craft.get("prompt") or "")

    inputs = _inputs(session)
    sid = session["session_id"]
    seed = random.randint(0, (1 << 64) - 1)
    locale = str(inputs.get("locale") or "ja")

    await _maybe_unload(ollama, session)

    if still:
        ask_text = (
            "当たりを一枚撮ります。少し待ってください。"
            if locale.startswith("ja") else
            "Taking one still. One moment."
        )
    else:
        ask_text = (
            "総監督、イメージボード上げます。これでいい？良ければ「③本番」、"
            "ダメなら指摘ください。"
            if locale.startswith("ja") else
            "Showrunner — image board going up. Good? Press \"final\" to shoot, "
            "or note what to fix."
        )
    ask = _chat_append(
        session, role="muse", muse_id="lens",
        name=_muse_display_name(session, "lens"), text=ask_text,
    )
    _publish_chat(sid, ask)

    session["board"] = {
        "prompt": prompt,
        "seed": seed,
        "job_id": "",
        "images": [],
        "pending": True,
        "still": bool(still),
        "round": int((session.get("board") or {}).get("round") or 0) + 1,
    }
    session["status"] = "boarding"
    await session_db.save(db, session)

    session["board"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_board",
        runner.run_board_job,
        meta={"session_id": sid, "step": "board"},
        db=db, comfy=comfy, session_id=sid,
    )
    session_db.log(session, "board", f"round {session['board']['round']}")
    await session_db.save(db, session)
    return session


async def approve_and_shoot(
    db, comfy, spooler, session: dict[str, Any], ollama=None,
) -> dict[str, Any]:
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    if not prompt:
        # In 主演撮り an "OK" with no craft used to fall through to another line
        # of conversation, so pressing the button looked like nothing happened.
        raise MuseError(_msg(
            session,
            ja="まだ台本がありません。服や場所など画の指示を会話で出してください。",
            en="No script yet — describe the shot in chat (clothes, place, camera).",
        ))

    session = await densify_craft_if_needed(db, ollama, session)
    session["craft_dirty"] = False
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")

    inputs = _inputs(session)
    sid = session["session_id"]
    seed = int((session.get("board") or {}).get("seed") or 0) or random.randint(0, (1 << 64) - 1)
    locale = str(inputs.get("locale") or "ja")

    await _maybe_unload(ollama, session)

    msg = _chat_append(
        session, role="system", name="Studio",
        text=(
            "承認を受け付けました。本番撮影に入ります。"
            if locale.startswith("ja") else
            "Approved. Going to final shoot."
        ),
    )
    _publish_chat(sid, msg)

    # Continuity snapshot at the moment they commit to a take — not after.
    nb = notebook_mod.of(session) if uses_notebook(session) else {}
    session["continuity_snapshot"] = {
        "at": time.time(),
        "theme": str(inputs.get("theme") or ""),
        "notebook": {
            k: nb.get(k) for k in (
                "atmosphere", "scene", "frame", "wearing", "beat",
                "wearing_b", "beat_b", "vibe", "open",
            )
        } if nb else {},
        "craft_tags": str(craft.get("tags") or ""),
        "craft_scene": str(craft.get("scene") or ""),
    }

    session["shoot"] = {
        "prompt": prompt,
        "seed": seed,
        "job_id": "",
        "images": [],
        "pending": True,
    }
    session["status"] = "shooting"
    await session_db.save(db, session)

    session["shoot"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_shoot",
        runner.run_shoot_job,
        meta={"session_id": sid, "step": "shoot"},
        db=db, comfy=comfy, session_id=sid,
    )
    session_db.log(session, "shoot", f"seed {seed}")
    await session_db.save(db, session)
    return session


def _has_shot(session: dict[str, Any]) -> bool:
    """Did the final shoot actually produce something?

    Deliberately looser than `schema.shoot_images`: older sessions store bare
    sha strings here, and the diary job already reads both shapes.
    """
    return bool((session.get("shoot") or {}).get("images"))


async def finish_session(
    db, spooler, session: dict[str, Any], ollama=None
) -> dict[str, Any]:
    """Wrap up session, mark as finished, and queue background post-shoot secret diary job.

    Two guards, both of which used to be missing and both of which the
    Showrunner could trip from the panel: wrapping twice wrote two diaries for
    one shoot, and wrapping before the shoot asked her to write about a picture
    that does not exist.
    """
    sid = session["session_id"]
    # A caller's `session` can be a stale snapshot — two concurrent requests
    # (double-click, a second tab, a retry) each load their own copy before
    # either writes `queued_at`, and both would otherwise pass the guard
    # below. Re-read the authoritative state while holding the session's
    # lock so only one caller ever gets past it.
    async with _finish_locks[sid]:
        fresh = await session_db.load(db, sid)
        if fresh is not None:
            session = fresh
        if (session.get("diary") or {}).get("queued_at") or session.get("status") == "finished":
            return session
        if not _has_shot(session):
            raise MuseError(_msg(
                session,
                ja="本番撮影が終わってから終了してください。",
                en="Finish the final shoot before wrapping up.",
            ))

        session["status"] = "finished"
        session_db.log(session, "finish", "session wrapped up")

        char_id = str((session.get("inputs") or {}).get("character_id") or "")
        partner_id = ""
        if is_duet(session):
            partner_char = await _partner_character(db, session)
            partner_id = str((partner_char or {}).get("character_id") or "")
        seen: set[str] = set()
        char_ids = [
            cid for cid in (char_id, partner_id)
            if cid and not (cid in seen or seen.add(cid))
        ]

        if char_ids and spooler:
            cfg = await get_runtime_config(db)
            inputs = _inputs(session)
            session["diary"] = {
                "status": "writing",
                "queued_at": time.time(),
                "entries": {cid: {"status": "writing"} for cid in char_ids},
            }
            await session_db.save(db, session)
            events.publish(sid, {"type": "diary_status", "status": "writing"})
            model = _text_model(inputs) or str(cfg.get("vlm_model") or "")
            num_ctx = _num_ctx(inputs, cfg)
            for cid in char_ids:
                spooler.submit(
                    # Every Ollama call in the app goes through PROMPT, and that lane is
                    # the one bound to the GPU resource when Ollama is local. There is no
                    # UTILITY lane — naming one raised AttributeError inside the request,
                    # so the diary job was never queued at all.
                    JobLane.PROMPT,
                    "generate_actress_diary",
                    run_generate_actress_diary_job,
                    meta={"session_id": sid, "character_id": cid},
                    db=db,
                    ollama=ollama,
                    session=session,
                    character_id=cid,
                    model=model,
                    num_ctx=num_ctx,
                    # Passed through so the second diary to land in a duet can
                    # queue the chemistry job itself — see _record_diary_result.
                    spooler=spooler,
                )
                # Lounge share is friend-facing (not the secret diary). Same
                # PROMPT lane; reactions are queued from inside the share job.
                spooler.submit(
                    JobLane.PROMPT,
                    "generate_lounge_share",
                    run_generate_lounge_share_job,
                    meta={"session_id": sid, "character_id": cid},
                    db=db,
                    ollama=ollama,
                    session=session,
                    character_id=cid,
                    model=model,
                    num_ctx=num_ctx,
                    spooler=spooler,
                )
            # Pitch / habit are independent of share succeeding — queue them
            # for the lead only so a failed wrap post does not silence ideas.
            lead_id = char_id
            if lead_id:
                lead_preset = await presets_db.get_preset(db, lead_id)
                lead_char = _character_for_id(session, lead_preset or {}, lead_id) if lead_preset else {}
                if lounge_mod.should_pitch(lead_char, lead_preset):
                    spooler.submit(
                        JobLane.PROMPT,
                        "generate_lounge_pitch",
                        run_generate_lounge_pitch_job,
                        meta={"session_id": sid, "character_id": lead_id},
                        db=db,
                        ollama=ollama,
                        session=session,
                        character_id=lead_id,
                        model=model,
                        num_ctx=num_ctx,
                    )
                if lounge_mod.should_write_habit(notes=list(session.get("notes") or [])):
                    spooler.submit(
                        JobLane.PROMPT,
                        "generate_handpost_habit",
                        run_generate_handpost_habit_job,
                        meta={"session_id": sid, "character_id": lead_id},
                        db=db,
                        ollama=ollama,
                        session=session,
                        character_id=lead_id,
                        model=model,
                        num_ctx=num_ctx,
                    )
        else:
            await session_db.save(db, session)
        return session


async def run_generate_actress_diary_job(
    reporter,
    cancel,
    *,
    db,
    ollama,
    session: dict[str, Any],
    character_id: str,
    model: str = "",
    num_ctx: int | None = None,
    spooler=None,
):
    """Background runner job that invokes LLM for dual-language (JA & EN) secret diary.

    Two positional arguments, like every other job: the spooler calls
    ``job._func(reporter, cancel_token, **kwargs)``.
    """
    sid = str(session.get("session_id") or "")
    _report(reporter, 0.05, "日記を書いてもらっています")
    preset = await presets_db.get_preset(db, character_id)
    if not preset:
        await _record_diary_result(
            db, sid, character_id=character_id, status="failed", error="character not found",
        )
        return {"status": "skipped", "reason": "character not found"}
    # Session may already hold the converted character; otherwise map the preset.
    # Raw presets keep `personality` as a trait list — never pass that straight
    # into actress_diary_prompt without normalizing. A duet has two of these
    # cached on the session (`character` for the lead, `partner_character` for
    # her partner) — matching only against the lead used to narrate the
    # partner's diary in the lead's voice.
    def _cached_match(candidate: dict[str, Any]) -> bool:
        return str(candidate.get("character_id") or "") == character_id and (
            isinstance(candidate.get("personality"), dict) or candidate.get("reasoning_ja")
        )

    session_char = session.get("character") or {}
    partner_char = session.get("partner_character") or {}
    if _cached_match(session_char):
        char = session_char
    elif _cached_match(partner_char):
        char = partner_char
    else:
        char = presets_db.preset_to_character(preset)

    # Extract session logs
    chat_list = session.get("chat") or []
    session_log_lines = []
    for m in chat_list[-15:]:
        if not isinstance(m, dict):
            continue
        session_log_lines.append(f"{m.get('name')}: {m.get('text')}")
    session_log = "\n".join(session_log_lines)

    # Extract photo description from shoot prompt
    photo_desc = str((session.get("shoot") or {}).get("prompt") or "")
    shot = (session.get("shoot") or {}).get("images") or []
    if shot and isinstance(shot[0], dict):
        image_id = str(shot[0].get("image_id") or "")
    else:
        image_id = str(shot[0]) if shot else ""

    # The prompt carries her voice, the material and the output contract, so it
    # is the system side; the user turn only has to ask for the thing.
    system = crew.actress_diary_prompt(
        char, session_log=session_log, photo_desc=photo_desc,
    )
    _report(reporter, 0.2, "日記を書いてもらっています")

    fields: dict[str, str] = {}
    for attempt, ask in enumerate(_DIARY_ASKS):
        raise_if_cancelled = getattr(cancel, "raise_if_set", None)
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        try:
            raw_resp = await chain._call(
                ollama, system=system, prompt=ask,
                model=model, images=None, num_ctx=num_ctx, think=False,
            )
        except Exception as exc:
            logger.warning("[muse] diary generation failed: %s", exc)
            await _record_diary_result(
                db, sid, character_id=character_id, status="failed", error=str(exc),
            )
            return {"status": "failed", "reason": str(exc)}
        fields = diary_mod.normalize(
            diary_mod.parse_diary(raw_resp), fallback_ja="本番撮影の思い出",
        )
        if fields.get("content_ja"):
            break
        # One retry, with the contract restated. The diary is a background job
        # on a model that is already resident, so trying twice is cheap — and
        # what the old code did instead was save the broken response as her
        # writing, which is how a JSON object ended up on the page.
        logger.info("[muse] diary output unusable (attempt %d), retrying", attempt + 1)
        _report(reporter, 0.5, "書き直してもらっています")

    if not fields.get("content_ja"):
        # Nothing survived that is safe to show. A missing diary is recoverable;
        # scaffolding printed in her handwriting is not.
        await _record_diary_result(
            db, sid, character_id=character_id, status="failed",
            error="unreadable diary output",
        )
        return {"status": "failed", "reason": "unreadable diary output"}

    _report(reporter, 0.9, "日記をしまっています")
    inputs = _inputs(session)
    diary_entry = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "summary_ja": fields["summary_ja"],
        "summary_en": fields["summary_en"],
        "summary": fields["summary_ja"],
        "content_ja": fields["content_ja"],
        "content_en": fields["content_en"],
        "content": fields["content_ja"],
        "image_id": image_id,
        # Which shoot this was, so the entry can lead back to it.
        "session_id": sid,
        "character_id": character_id,
        "theme": str(inputs.get("theme") or ""),
        "read": False,
    }

    await presets_db.add_preset_diary(db, character_id, diary_entry)
    chemistry_pair = await _record_diary_result(
        db, sid, character_id=character_id, status="ok", diary_id=diary_entry["id"],
    )
    if chemistry_pair and spooler is not None:
        (char_a_id, diary_a_id), (char_b_id, diary_b_id) = chemistry_pair
        spooler.submit(
            JobLane.PROMPT,
            "generate_actress_chemistry",
            run_generate_chemistry_job,
            meta={"session_id": sid},
            db=db,
            ollama=ollama,
            session_id=sid,
            character_a_id=char_a_id,
            character_b_id=char_b_id,
            diary_id_a=diary_a_id,
            diary_id_b=diary_b_id,
            model=model,
            num_ctx=num_ctx,
        )
    _report(reporter, 1.0, "日記が書き上がりました")
    return {"status": "ok", "diary_id": diary_entry["id"]}


# The second ask restates the contract. Models that wandered off it once tend to
# come back when told plainly what shape failed.
_DIARY_ASKS: tuple[str, ...] = (
    "今日の撮影の秘密の日記を書いて。SUMMARY_JA / SUMMARY_EN / CONTENT_JA / CONTENT_EN "
    "の4つの見出しだけを使うこと。",
    "さっきの出力は読み取れませんでした。もう一度、日記だけを書いてください。"
    "1行目は必ず `SUMMARY_JA: ` で始め、続けて SUMMARY_EN / CONTENT_JA / CONTENT_EN。"
    "JSON にしない。コードフェンスも使わない。",
)


async def run_generate_chemistry_job(
    reporter,
    cancel,
    *,
    db,
    ollama,
    session_id: str,
    character_a_id: str,
    character_b_id: str,
    diary_id_a: str,
    diary_id_b: str,
    model: str = "",
    num_ctx: int | None = None,
):
    """Runs once per duet, right after both actors' diaries from the same shoot
    have landed (queued from _record_diary_result, never twice for one shoot).

    Reads the two fresh entries and asks for a short relationship note,
    informed by them and by where the pair's compatibility vectors + shared
    history currently sit, then stores it on both characters via
    `presets_db.add_chemistry_record` — no session-side state to track once
    this returns; the dossier reads it straight off the character payload.
    """
    _report(reporter, 0.1, "二人の相性を読み解いています")
    preset_a = await presets_db.get_preset(db, character_a_id)
    preset_b = await presets_db.get_preset(db, character_b_id)
    if not preset_a or not preset_b:
        return {"status": "skipped", "reason": "character not found"}

    diaries_a = await presets_db.get_preset_diaries(db, character_a_id)
    diaries_b = await presets_db.get_preset_diaries(db, character_b_id)
    diary_a = next((d for d in diaries_a if str(d.get("id") or "") == diary_id_a), None)
    diary_b = next((d for d in diaries_b if str(d.get("id") or "") == diary_id_b), None)
    if not diary_a or not diary_b:
        return {"status": "skipped", "reason": "diary not found"}

    compat = await compat_mod.compatibility(db, character_a_id, character_b_id)
    system = crew.actress_chemistry_prompt(
        presets_db.preset_to_character(preset_a),
        presets_db.preset_to_character(preset_b),
        diary_a, diary_b, tier=compat["tier"],
    )
    _report(reporter, 0.4, "二人の相性を読み解いています")

    fields: dict[str, str] = {}
    for attempt, ask in enumerate(_CHEMISTRY_ASKS):
        raise_if_cancelled = getattr(cancel, "raise_if_set", None)
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        try:
            raw_resp = await chain._call(
                ollama, system=system, prompt=ask,
                model=model, images=None, num_ctx=num_ctx, think=False,
            )
        except Exception as exc:
            logger.warning("[muse] chemistry generation failed: %s", exc)
            return {"status": "failed", "reason": str(exc)}
        fields = diary_mod.normalize(
            diary_mod.parse_diary(raw_resp), fallback_ja="いい雰囲気で撮影していた",
        )
        if fields.get("content_ja"):
            break
        logger.info("[muse] chemistry output unusable (attempt %d), retrying", attempt + 1)
        _report(reporter, 0.6, "書き直してもらっています")

    if not fields.get("content_ja"):
        return {"status": "failed", "reason": "unreadable chemistry output"}

    record = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "session_id": session_id,
        "summary_ja": fields["summary_ja"],
        "summary_en": fields["summary_en"],
        "content_ja": fields["content_ja"],
        "content_en": fields["content_en"],
        "tier": compat["tier"],
        "score": compat["score"],
        "sources": [
            {
                "diary_id": diary_a.get("id"), "character_id": character_a_id,
                "summary_ja": diary_a.get("summary_ja"), "summary_en": diary_a.get("summary_en"),
                "timestamp": diary_a.get("timestamp"),
            },
            {
                "diary_id": diary_b.get("id"), "character_id": character_b_id,
                "summary_ja": diary_b.get("summary_ja"), "summary_en": diary_b.get("summary_en"),
                "timestamp": diary_b.get("timestamp"),
            },
        ],
    }
    await presets_db.add_chemistry_record(db, character_a_id, character_b_id, record)
    _report(reporter, 1.0, "相性メモができました")
    events.publish(session_id, {"type": "chemistry_ready", "tier": compat["tier"]})
    return {"status": "ok"}


_CHEMISTRY_ASKS: tuple[str, ...] = (
    "二人の日記を読んで、相性についての短いメモを書いて。SUMMARY_JA / SUMMARY_EN / "
    "CONTENT_JA / CONTENT_EN の4つの見出しだけを使うこと。",
    "さっきの出力は読み取れませんでした。もう一度、メモだけを書いてください。"
    "1行目は必ず `SUMMARY_JA: ` で始め、続けて SUMMARY_EN / CONTENT_JA / CONTENT_EN。"
    "JSON にしない。コードフェンスも使わない。",
)


def _report(reporter, progress: float, message: str) -> None:
    """Progress for the jobs panel. The diary job used to report nothing at all."""
    update = getattr(reporter, "update", None)
    if update is None:
        return
    try:
        update(progress, message)
    except Exception:
        logger.debug("[muse] diary reporter failed", exc_info=True)


async def _record_diary_result(
    db, session_id: str, *, character_id: str, status: str, diary_id: str = "", error: str = "",
) -> list[tuple[str, str]] | None:
    """Write one actor's outcome back onto the session and tell the panel.

    Nothing announced the diary before this: the Showrunner wrapped the session
    and the entry appeared on the character, minutes later, unmentioned. A duet
    queues two of these jobs, so the session tracks one entry per character_id
    and only reports "done" once every entry has landed — a fast lead diary
    used to flip the aggregate to "ok" while her partner's was still writing.

    Returns ``[(character_id, diary_id), (character_id, diary_id)]`` exactly
    once per duet — to whichever of the two jobs happens to be the one that
    completes the pair — as the signal to queue chemistry generation. A
    `chemistry_queued` flag on the session stops the other job (or a retry)
    from queueing it twice; `None` means "not your job to queue it."
    """
    if not session_id:
        return None
    events.publish(session_id, {
        "type": "diary_status", "status": status, "character_id": character_id,
        "diary_id": diary_id, "error": error,
    })
    try:
        stored = await session_db.load(db, session_id)
    except Exception:
        stored = None
    if stored is None:
        return None
    diary = dict(stored.get("diary") or {})
    entries = dict(diary.get("entries") or {})
    entries[character_id] = {
        "status": status, "diary_id": diary_id, "error": error, "at": time.time(),
    }
    diary["entries"] = entries
    statuses = [str(e.get("status") or "") for e in entries.values()]
    if any(s == "writing" for s in statuses):
        aggregate = "writing"
    elif any(s == "ok" for s in statuses):
        aggregate = "ok"
    else:
        aggregate = "failed"
    diary.update({
        "status": aggregate,
        "diary_id": diary_id if status == "ok" else diary.get("diary_id", ""),
        "error": error,
        "at": time.time(),
    })

    chemistry_pair: list[tuple[str, str]] | None = None
    if is_duet(stored) and not diary.get("chemistry_queued"):
        all_settled = not any(s == "writing" for s in statuses)
        ok_pairs = [
            (cid, str(e.get("diary_id") or ""))
            for cid, e in entries.items()
            if e.get("status") == "ok" and e.get("diary_id")
        ]
        if all_settled and len(entries) >= 2 and len(ok_pairs) >= 2:
            diary["chemistry_queued"] = True
            chemistry_pair = ok_pairs[:2]

    stored["diary"] = diary
    await session_db.save(db, stored, publish=False)
    return chemistry_pair



def _session_chat_log(session: dict[str, Any], *, limit: int = 15) -> str:
    lines = []
    for m in (session.get("chat") or [])[-limit:]:
        if not isinstance(m, dict):
            continue
        lines.append(f"{m.get('name')}: {m.get('text')}")
    return "\n".join(lines)


def _director_highlights(session: dict[str, Any]) -> str:
    notes = [str(n).strip() for n in (session.get("notes") or []) if str(n).strip()]
    return "\n".join(f"- {n}" for n in notes[-8:])


def _shoot_image_id(session: dict[str, Any]) -> str:
    shot = (session.get("shoot") or {}).get("images") or []
    if shot and isinstance(shot[0], dict):
        return str(shot[0].get("image_id") or "")
    return str(shot[0]) if shot else ""


def _character_for_id(session: dict[str, Any], preset: dict[str, Any], character_id: str) -> dict[str, Any]:
    def _cached_match(candidate: dict[str, Any]) -> bool:
        return str(candidate.get("character_id") or "") == character_id and (
            isinstance(candidate.get("personality"), dict) or candidate.get("reasoning_ja")
        )

    session_char = session.get("character") or {}
    partner_char = session.get("partner_character") or {}
    if _cached_match(session_char):
        return session_char
    if _cached_match(partner_char):
        return partner_char
    return presets_db.preset_to_character(preset)


async def run_generate_lounge_share_job(
    reporter, cancel, *, db, ollama, session: dict[str, Any], character_id: str,
    model: str = "", num_ctx: int | None = None, spooler=None,
):
    """Friend-facing wrap post to the lounge (not the secret diary)."""
    sid = str(session.get("session_id") or "")
    _report(reporter, 0.05, "楽屋に書き込んでいます")
    preset = await presets_db.get_preset(db, character_id)
    if not preset:
        return {"status": "skipped", "reason": "character not found"}
    char = _character_for_id(session, preset, character_id)
    template = lounge_mod.pick_share_template()
    system = crew.lounge_share_prompt(
        char,
        session_log=_session_chat_log(session),
        photo_desc=str((session.get("shoot") or {}).get("prompt") or ""),
        template=template,
        director_highlights=_director_highlights(session),
    )
    ask = (
        "楽屋への投稿を書いて。TEXT_JA / TEXT_EN と任意の POSE/OUTFIT/EXPRESSION/PLACE/VIBE。"
        "秘密の日記の本音は書かない。"
    )
    try:
        raw = await chain._call(
            ollama, system=system, prompt=ask,
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] lounge share failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}
    fields = lounge_mod.normalize_share(lounge_mod.parse_labelled(raw))
    if not fields.get("text_ja"):
        logger.info("[muse] lounge share empty for %s", character_id)
        return {"status": "failed", "reason": "empty lounge share"}

    inputs = _inputs(session)
    thread = {
        "id": str(uuid.uuid4()),
        "kind": "wrap_share",
        "author_character_id": character_id,
        "author_role": "muse",
        "author_name_ja": str(char.get("name_ja") or preset.get("name_ja") or ""),
        "author_name": str(char.get("name") or preset.get("name") or ""),
        "session_id": sid,
        "image_id": _shoot_image_id(session),
        "theme": str(inputs.get("theme") or ""),
        "template": template,
        "text_ja": fields["text_ja"],
        "text_en": fields["text_en"],
        "tags": fields.get("tags") or {},
        "messages": [{
            "id": str(uuid.uuid4()),
            "turn": 0,
            "character_id": character_id,
            "name_ja": str(char.get("name_ja") or ""),
            "name": str(char.get("name") or ""),
            "text_ja": fields["text_ja"],
            "text_en": fields["text_en"],
            "reaction": "",
        }],
        "created_at": time.time(),
    }
    await lounge_db.save_thread(db, thread)
    events.publish(sid, {"type": "lounge_status", "status": "shared", "thread_id": thread["id"]})
    _report(reporter, 0.6, "親友の反応を待っています")
    if spooler is not None:
        spooler.submit(
            JobLane.PROMPT,
            "generate_lounge_reactions",
            run_generate_lounge_reactions_job,
            meta={"session_id": sid, "thread_id": thread["id"]},
            db=db,
            ollama=ollama,
            thread_id=thread["id"],
            model=model,
            num_ctx=num_ctx,
        )
    _report(reporter, 1.0, "楽屋に投稿しました")
    return {"status": "ok", "thread_id": thread["id"]}


async def run_generate_lounge_reactions_job(
    reporter, cancel, *, db, ollama, thread_id: str,
    model: str = "", num_ctx: int | None = None,
):
    """Close friends like + 1–2 short comments; seeds trend/feedback memories."""
    _report(reporter, 0.1, "楽屋の反応を集めています")
    thread = await lounge_db.get_thread(db, thread_id)
    if not thread:
        return {"status": "skipped", "reason": "thread not found"}
    author_id = str(thread.get("author_character_id") or "")
    if not author_id:
        return {"status": "skipped", "reason": "no author"}
    friends = await compat_mod.friends_of(db, author_id, min_tier="close", limit=2)
    if not friends:
        # Soft fallback: any acquaintance neighbour so the lounge still breathes
        # when chemistry vectors are thin.
        friends = await compat_mod.friends_of(db, author_id, min_tier="acquaintance", limit=2)
    if not friends:
        return {"status": "skipped", "reason": "no friends"}

    author_preset = await presets_db.get_preset(db, author_id) or {}
    author = {
        "name_ja": thread.get("author_name_ja") or author_preset.get("name_ja") or "",
        "name": thread.get("author_name") or author_preset.get("name") or "",
    }
    system = crew.lounge_reactions_prompt(
        author,
        str(thread.get("text_ja") or ""),
        friends,
        tags=dict(thread.get("tags") or {}),
    )
    ask = "親友のリアクションを書いて。REACTOR_1_*（と必要なら REACTOR_2_*）。"
    try:
        raw = await chain._call(
            ollama, system=system, prompt=ask,
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] lounge reactions failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}

    reactions = lounge_mod.normalize_reactions(lounge_mod.parse_labelled(raw), friends)
    if not reactions:
        return {"status": "failed", "reason": "empty reactions"}

    messages = list(thread.get("messages") or [])
    for turn, react in enumerate(reactions, start=1):
        messages.append({
            "id": str(uuid.uuid4()),
            "turn": turn,
            "character_id": react["character_id"],
            "name_ja": react["name_ja"],
            "name": react["name"],
            "text_ja": react["text_ja"],
            "text_en": react["text_en"],
            "reaction": react["reaction"],
            "stance": react["stance"],
            "twist": react.get("twist") or "",
        })
        # Friend keeps a trend tip (what they might try next).
        tip_ja = react["text_ja"]
        if react["stance"] == "twist" and react.get("twist"):
            tip_ja = f"{react['twist']}（{author.get('name_ja') or '彼女'}の話を聞いて）"
        await presets_db.add_social_seed(db, react["character_id"], {
            "source_thread_id": thread_id,
            "kind": "trend",
            "summary_ja": tip_ja[:160],
            "summary_en": (react["text_en"] or tip_ja)[:160],
            "stance": react["stance"],
            "uses_left": 3,
        })
        # Author keeps friend feedback.
        await presets_db.add_social_seed(db, author_id, {
            "source_thread_id": thread_id,
            "kind": "friend_feedback",
            "summary_ja": f"{react['name_ja'] or react['name']}: {react['text_ja']}"[:160],
            "summary_en": f"{react['name'] or react['name_ja']}: {react['text_en']}"[:160],
            "stance": "try",
            "uses_left": 3,
        })

    thread["messages"] = messages
    thread["reaction_count"] = len(reactions)
    await lounge_db.save_thread(db, thread)

    tags = thread.get("tags") or {}
    trend_bits = [v for k, v in tags.items() if v and k in ("pose", "outfit", "expression", "vibe")]
    twists = [
        {
            "character_id": r["character_id"],
            "name_ja": r["name_ja"],
            "name": r["name"],
            "stance": r["stance"],
            "twist": r.get("twist") or "",
            "text_ja": r["text_ja"],
            "text_en": r["text_en"],
        }
        for r in reactions
        if r.get("stance") in ("twist", "try")
    ]
    if trend_bits or twists:
        await lounge_db.push_trend(db, {
            "from_character_id": author_id,
            "from_name_ja": author.get("name_ja") or "",
            "from_name": author.get("name") or "",
            "thread_id": thread_id,
            "summary_ja": (" / ".join(trend_bits) if trend_bits else (reactions[0]["text_ja"][:80]))[:120],
            "summary_en": (" / ".join(trend_bits) if trend_bits else (reactions[0]["text_en"][:80]))[:120],
            "tags": tags,
            "twists": twists,
        })

    sid = str(thread.get("session_id") or "")
    if sid:
        events.publish(sid, {
            "type": "lounge_status", "status": "reacted", "thread_id": thread_id,
        })
    _report(reporter, 1.0, "楽屋の反応が付きました")
    return {"status": "ok", "thread_id": thread_id, "reactions": len(reactions)}


async def run_generate_lounge_pitch_job(
    reporter, cancel, *, db, ollama, session: dict[str, Any], character_id: str,
    model: str = "", num_ctx: int | None = None,
):
    """Occasional 'how about this?' pitch visible to the showrunner in the lounge."""
    sid = str(session.get("session_id") or "")
    _report(reporter, 0.1, "提案を楽屋に書いています")
    preset = await presets_db.get_preset(db, character_id)
    if not preset:
        return {"status": "skipped", "reason": "character not found"}
    char = _character_for_id(session, preset, character_id)
    system = crew.lounge_pitch_prompt(
        char,
        session_log=_session_chat_log(session),
        photo_desc=str((session.get("shoot") or {}).get("prompt") or ""),
        director_highlights=_director_highlights(session),
    )
    try:
        raw = await chain._call(
            ollama, system=system, prompt="提案を書いて。TEXT_JA / TEXT_EN だけ。",
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] lounge pitch failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}
    fields = lounge_mod.normalize_pitch(lounge_mod.parse_labelled(raw))
    if not fields.get("text_ja"):
        return {"status": "failed", "reason": "empty pitch"}

    inputs = _inputs(session)
    thread = {
        "id": str(uuid.uuid4()),
        "kind": "pitch",
        "status": "open",
        "author_character_id": character_id,
        "author_role": "muse",
        "author_name_ja": str(char.get("name_ja") or preset.get("name_ja") or ""),
        "author_name": str(char.get("name") or preset.get("name") or ""),
        "session_id": sid,
        "image_id": _shoot_image_id(session),
        "theme": str(inputs.get("theme") or ""),
        "text_ja": fields["text_ja"],
        "text_en": fields["text_en"],
        "tags": {},
        "messages": [{
            "id": str(uuid.uuid4()),
            "turn": 0,
            "character_id": character_id,
            "role": "muse",
            "name_ja": str(char.get("name_ja") or ""),
            "name": str(char.get("name") or ""),
            "text_ja": fields["text_ja"],
            "text_en": fields["text_en"],
        }],
        "created_at": time.time(),
    }
    await lounge_db.save_thread(db, thread)
    if sid:
        events.publish(sid, {"type": "lounge_status", "status": "pitch", "thread_id": thread["id"]})
    _report(reporter, 1.0, "提案を楽屋に出しました")
    return {"status": "ok", "thread_id": thread["id"]}


async def run_generate_handpost_habit_job(
    reporter, cancel, *, db, ollama, session: dict[str, Any], character_id: str,
    model: str = "", num_ctx: int | None = None,
):
    """Rare handpost line about the showrunner's taste (not a how-to wiki)."""
    _report(reporter, 0.1, "手帖に癖を書き留めています")
    preset = await presets_db.get_preset(db, character_id) or {}
    notes = _director_highlights(session)
    if not notes.strip():
        return {"status": "skipped", "reason": "no notes"}
    name_ja = str(preset.get("name_ja") or preset.get("name") or "")
    system = crew.showrunner_habit_prompt(
        notes=notes,
        session_log=_session_chat_log(session, limit=10),
        muse_name=name_ja,
    )
    try:
        raw = await chain._call(
            ollama, system=system,
            prompt="手帖の一文を書いて。TITLE_JA / TITLE_EN / BODY_JA / BODY_EN。",
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] handpost habit failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}
    fields = lounge_mod.normalize_habit(lounge_mod.parse_labelled(raw))
    if not fields.get("body_ja"):
        return {"status": "failed", "reason": "empty habit"}
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    title = fields["title"] or ("総監督の癖" if ja else "Showrunner habits")
    if not ja and fields.get("title_en"):
        title = fields["title_en"]
    page = await handpost_db.save_page(db, {
        "title": title,
        "title_ja": fields["title"] or "総監督の癖",
        "title_en": fields.get("title_en") or fields["title"] or "Showrunner habits",
        "body_ja": fields["body_ja"],
        "body_en": fields["body_en"],
        "pinned": False,
        "author": "system",
        "kind": "habit",
        "source_session_id": str(session.get("session_id") or ""),
        "source_character_id": character_id,
    })
    sid = str(session.get("session_id") or "")
    if sid:
        events.publish(sid, {"type": "lounge_status", "status": "habit", "page_id": page["id"]})
    _report(reporter, 1.0, "手帖に書き留めました")
    return {"status": "ok", "page_id": page["id"]}


async def reply_lounge_thread(
    db, thread_id: str, *, text: str, locale: str = "ja",
) -> dict[str, Any]:
    """Showrunner replies on a lounge thread (pitch or wrap)."""
    text = (text or "").strip()
    if not text:
        raise MuseError("empty reply" if not str(locale).startswith("ja") else "返信が空です。")
    thread = await lounge_db.get_thread(db, thread_id)
    if thread is None:
        raise MuseError("thread not found" if not str(locale).startswith("ja") else "スレが見つかりません。")
    msg = {
        "id": str(uuid.uuid4()),
        "role": "director",
        "character_id": "",
        "name_ja": "総監督",
        "name": "Showrunner",
        "text_ja": text,
        "text_en": text,
        "reaction": "",
    }
    updated = await lounge_db.append_message(db, thread_id, msg)
    if updated is None:
        raise MuseError("thread not found" if not str(locale).startswith("ja") else "スレが見つかりません。")
    # Don't clobber "promoted" — a later reply used to revive the promote button.
    if (
        str(updated.get("kind") or "") == "pitch"
        and str(updated.get("status") or "open") == "open"
    ):
        updated["status"] = "answered"
        updated = await lounge_db.save_thread(db, updated)
    return updated


async def promote_lounge_pitch(db, thread_id: str, *, locale: str = "ja") -> dict[str, Any]:
    """Turn a pitch (+ director reply if any) into a pinned handpost notice."""
    thread = await lounge_db.get_thread(db, thread_id)
    if thread is None:
        raise MuseError("thread not found" if not str(locale).startswith("ja") else "スレが見つかりません。")
    if str(thread.get("kind") or "") != "pitch":
        raise MuseError(
            "only pitches can be promoted" if not str(locale).startswith("ja")
            else "提案スレだけ周知に載せられます。"
        )
    # Idempotent: a second click must not mint another handpost page.
    if str(thread.get("status") or "") == "promoted" and thread.get("promoted_page_id"):
        page = await handpost_db.get_page(db, str(thread["promoted_page_id"]))
        if page is not None:
            return {"thread": thread, "page": page}
    author_ja = str(thread.get("author_name_ja") or thread.get("author_name") or "Muse")
    author_en = str(thread.get("author_name") or thread.get("author_name_ja") or "Muse")
    pitch_ja = str(thread.get("text_ja") or "")
    pitch_en = str(thread.get("text_en") or pitch_ja)
    director_bits_ja: list[str] = []
    director_bits_en: list[str] = []
    for m in thread.get("messages") or []:
        if str(m.get("role") or "") != "director":
            continue
        director_bits_ja.append(str(m.get("text_ja") or m.get("text_en") or "").strip())
        director_bits_en.append(str(m.get("text_en") or m.get("text_ja") or "").strip())
    body_ja = f"{author_ja}の提案:\n{pitch_ja}"
    body_en = f"Pitch from {author_en}:\n{pitch_en}"
    if director_bits_ja:
        body_ja += "\n\n総監督:\n" + "\n".join(b for b in director_bits_ja if b)
        body_en += "\n\nShowrunner:\n" + "\n".join(b for b in director_bits_en if b)
    ja = str(locale or "ja").startswith("ja")
    title_ja = f"提案採用 — {author_ja}"
    title_en = f"Adopted pitch — {author_en}"
    page = await handpost_db.save_page(db, {
        "title": title_ja if ja else title_en,
        "title_ja": title_ja,
        "title_en": title_en,
        "body_ja": body_ja,
        "body_en": body_en,
        "pinned": True,
        "author": "director",
        "kind": "promoted_pitch",
        "source_thread_id": thread_id,
    })
    thread["status"] = "promoted"
    thread["promoted_page_id"] = page["id"]
    await lounge_db.save_thread(db, thread)
    return {"thread": thread, "page": page}


async def cancel_board(db, spooler, session: dict[str, Any]) -> dict[str, Any]:
    job_id = str((session.get("board") or {}).get("job_id") or "")
    if job_id:
        await spooler.cancel(job_id)
    session["board"] = {}
    session["status"] = "chat"
    session_db.log(session, "board", "cancelled")
    await session_db.save(db, session)
    return session

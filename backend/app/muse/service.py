"""Muse studio — showrunner chat, crew table-read, board, OK, shoot.

The user is 総監督. Muses discuss in character until a board is shown and the
showrunner says OK. There is no B/C/D pickup chain anymore.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..characters import presets as presets_db
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
from . import brief as brief_mod
from . import chain, crew, events, identity, runner, session_db
from .runtime import render_settings
from .schema import missing_inputs, new_session

logger = logging.getLogger(__name__)

# Showrunner approval — Japanese / English.
# Exact one-liners, plus short natural phrases like「Ok 本番よろしく」.
_OK_RE = re.compile(
    r"^\s*(ok|okay|lgtm|ship\s*it|go|yes|yep|approved?|いいよ|よし|おｋ|おけ|"
    r"OK|これでいい|それでいい|撮って|撮影|本番|決定|確定|ゴー)\s*[!！.。]*\s*$",
    re.I,
)
_OK_NATURAL_RE = re.compile(
    r"(?i)\b(ok|okay|lgtm|go)\b|お[ｋkけ]|いいよ|よし|ゴー|"
    r"これでいい|それでいい|撮って|撮影|本番|決定|確定|approved?"
)
_BOARD_RE = re.compile(r"ボード|board|試写|イメージ", re.I)
_OK_DENY_RE = re.compile(
    r"じゃない|じゃなく|ではなく|だめ|ダメ|もっと|やめて|待って|まだ|"
    r"not\s*ok|don't|dont|wait|more|nope",
    re.I,
)


def _is_approve(text: str) -> bool:
    """True when the showrunner is green-lighting the shoot."""
    t = (text or "").strip()
    if not t:
        return False
    if _OK_RE.match(t):
        return True
    # Short approval with fluff —「Ok 本番よろしく」「よし撮って！」
    if len(t) <= 48 and _OK_NATURAL_RE.search(t) and not _OK_DENY_RE.search(t):
        return True
    return False


class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _identity_tags(session: dict[str, Any]) -> list[str]:
    character = session.get("character") or {}
    return [str(t) for t in (character.get("identity_tags") or []) if str(t).strip()]


def _framing(inputs: dict[str, Any]) -> str:
    return identity.normalize_framing(str(inputs.get("framing") or "auto"))


def _cast(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Everyone in frame. One seat today; the shape is ready for more."""
    character = session.get("character") or {}
    return [character] if character else []


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
    }
    session.setdefault("chat", []).append(msg)
    return msg


def _publish_chat(session_id: str, msg: dict[str, Any]) -> None:
    events.publish(session_id, {"type": "chat_message", **msg})


def _token_publisher(session_id: str, muse_id: str):
    def _pub(text: str) -> None:
        events.publish(session_id, {
            "type": "chat_delta", "muse_id": muse_id, "text": text,
        })
    return _pub


def negative_for(session: dict[str, Any]) -> str:
    inputs = _inputs(session)
    return identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.opposing_negative(_identity_tags(session)),
        identity.framing_negative(_framing(inputs)),
    )


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    inputs = {**_inputs(session), **{k: v for k, v in patch.items() if v is not None}}
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
        inputs["crew_preset"] = str(inputs.get("crew_preset") or crew.DEFAULT_PRESET)
    session["inputs"] = inputs
    _rebuild_brief(session)
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise MuseError("character not found")
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
        notes=list(session.get("notes") or []),
    )
    session["brief"] = brief_mod.build(
        character, common["theme"], common["style"],
        framing=common["framing"], plan=common["plan"], notes=common["notes"],
        reference="full",
    )
    session["brief_lite"] = brief_mod.build(
        character, common["theme"], common["style"],
        framing=common["framing"], plan=common["plan"], notes=common["notes"],
        reference="digest",
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


def _apply_turn(
    session: dict[str, Any], turn: chain.MuseTurn, *, ms: int = 0,
) -> dict[str, Any]:
    craft = session.setdefault("craft", {})
    record_ledger(
        session, muse_id=turn.muse_id,
        name=_muse_display_name(session, turn.muse_id),
        before=str(craft.get("tags") or ""), after=turn.tags, ms=ms,
    )
    craft["prompt"] = turn.prompt
    craft["tags"] = turn.tags
    craft["scene"] = turn.scene
    if crew.role_of(turn.muse_id) in ("beat", "spine") or not craft.get("pose_intent"):
        craft["pose_intent"] = turn.pose_intent
    name = _muse_display_name(session, turn.muse_id)
    say = turn.say or f"（{name}が台本を更新した。）"
    msg = _chat_append(
        session, role="muse", text=say,
        muse_id=turn.muse_id, name=name, kind="craft",
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
    craft["prompt"] = identity.assemble_positive(
        _identity_tags(session), craft["tags"], str(craft.get("scene") or ""),
        framing=_framing(_inputs(session)), style=_style(session),
        subject=identity.subject_tags(_cast(session)),
    )
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
# The three seats that meet before anything is drawn: someone to settle where
# and when, her, and a camera. Everyone else waits for a frame to argue with.
#
# The table used to open with all eighteen and no picture anywhere, and the
# result was a run where「カラオケボックスで歌っている」became a live house: twenty
# turns of prose agreeing with each other, and the Showrunner then spent the
# session deleting props that had accumulated in the dark. A real studio shoots
# a still first and talks about the still.
OPENING_ROLES: tuple[str, ...] = ("actress", "lens")


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


async def start_table(
    db, ollama, session: dict[str, Any], *, comfy=None, spooler=None,
) -> dict[str, Any]:
    """Read-through, act one: place, her, a camera — then a still to argue with.

    With no renderer wired (`comfy`/`spooler` omitted) there is no still to wait
    for, so the whole table meets at once as it used to.
    """
    missing = missing_inputs(session)
    if missing:
        raise MuseError(f"missing: {', '.join(missing)}")

    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    session["status"] = "discussing"
    session["chat"] = []
    session["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    session["ledger"] = []
    session["board"] = {}
    session["shoot"] = {}
    session["plan"] = {}
    session.pop("_blind_said", None)
    session.pop("struck", None)
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
            "無理難題歓迎です。途中でイメージボードを出しますので、OKかコメントをください。"
        )
        open_en = (
            "Showrunner, table read is open. The crew will pass the craft and heckle "
            "each other along the way. Hard notes welcome. Board coming — OK or comment."
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

    seats = (_writing_seats(cast, only=OPENING_ROLES) if still_first
             else _writing_seats(cast))
    await _craft_pass(db, ollama, session, cast, seats, cfg=cfg)

    if still_first:
        session_db.log(session, "table", f"brief · {len(seats)} seats")
        return await request_board(
            db, comfy, spooler, session, ollama=ollama, still=True,
        )

    ask_ja = (
        "一通り集まりました。イメージボードを出して確認しますか？"
        "「ボード」で試写、「OK」でこの台本のまま本番撮影です。"
        "まだならコメントをください — 班が答えます。"
    )
    ask_en = (
        "First pass done. Want an image board? Say \"board\" for a screening, "
        "\"OK\" to shoot this craft, or leave a note and the crew will answer."
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


# ── showrunner message ──────────────────────────────────────────────────────
async def post_chat(
    db, ollama, comfy, spooler, session: dict[str, Any], text: str,
) -> dict[str, Any]:
    """Showrunner speaks. OK → shoot. board → image board. else → crew replies."""
    text = (text or "").strip()
    if not text:
        raise MuseError("empty message")
    if missing_inputs(session):
        raise MuseError(f"missing: {', '.join(missing_inputs(session))}")
    if not (session.get("craft") or {}).get("prompt"):
        # Auto-open table if they chat first.
        session = await start_table(
            db, ollama, session, comfy=comfy, spooler=spooler,
        )

    sid = session["session_id"]
    user_msg = _chat_append(session, role="user", text=text, name="総監督")
    _publish_chat(sid, user_msg)
    await session_db.save(db, session)

    approving = _is_approve(text)
    wants_board = bool(_BOARD_RE.search(text))
    # Anything that is not one of those two words is creative direction, and it
    # stands from here on — a note used to live only in the turn that answered it.
    direction = "" if (approving or wants_board) else text

    # The still is up and only three seats have spoken: this is the note the
    # rest of the crew has been waiting for. Whatever it says, the full table
    # meets once, first — otherwise a bare OK ships a prompt three seats wrote.
    if str(session.get("table_stage") or "full") == "brief":
        if direction:
            session.setdefault("notes", []).append(direction)
            _rebuild_brief(session)
            await session_db.save(db, session)
            cfg = await get_runtime_config(db)
            if _cast_in_role(_crew_ids(session), "plan"):
                await _run_plan_turn(db, ollama, session, cfg=cfg, note=direction)
                await session_db.save(db, session, publish=False)
        session = await run_full_table(db, ollama, session, note=direction)
        if direction:
            locale = str(_inputs(session).get("locale") or "ja")
            wrap = _chat_append(
                session, role="system", name="Studio",
                text=(
                    "全班そろいました。イメージボードを見る？「ボード」／本番なら「OK」／"
                    "まだ詰めるなら続けてどうぞ。"
                    if locale.startswith("ja") else
                    "Full crew is in. \"board\" for a screening, \"OK\" to shoot, "
                    "or keep the notes coming."
                ),
            )
            _publish_chat(sid, wrap)
            session["status"] = "chat"
            await session_db.save(db, session)
            return session

    if approving:
        return await approve_and_shoot(db, comfy, spooler, session, ollama=ollama)

    if wants_board:
        return await request_board(db, comfy, spooler, session, ollama=ollama)

    # Crew answers the hard note — pick specialists by keyword, else core desk.
    cast = _crew_ids(session)
    responders = _pick_responders(text, cast)
    session["status"] = "discussing"
    # The note is standing direction from here on, not a remark about one turn.
    session.setdefault("notes", []).append(text)
    _rebuild_brief(session)
    await session_db.save(db, session)
    cfg = await get_runtime_config(db)

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
            "反映しました。イメージボードを見る？「ボード」／本番なら「OK」／まだ詰めるなら続けてどうぞ。"
            if locale.startswith("ja") else
            "Applied. \"board\" for a screening, \"OK\" to shoot, or keep notes coming."
        ),
    )
    _publish_chat(sid, wrap)
    session["status"] = "chat"
    await session_db.save(db, session)
    return session


def _pick_responders(note: str, crew_ids: list[str]) -> list[str]:
    """Fixed short desk for every showrunner note.

    Do NOT branch on mood or situation keywords in Python.
    The note is already injected into the VLM user prompt — specialists read it
    in dialogue and revise craft. Python only caps turn count for local Ollama.
    """
    _ = note  # intentional: routing ignores note text; VLM interprets it
    # Stable priority — actress first so personality can answer any note.
    # Banter-only seats are not here: the Producer answered every note by
    # restating the beat with `dynamic_composition` on it.
    priority = tuple(
        r for r in (
            "actress", "beat", "spine", "lens", "wardrobe",
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


async def densify_craft_if_needed(
    db, ollama, session: dict[str, Any],
) -> dict[str, Any]:
    """Run Finisher once when craft is too thin for a rich render."""
    if ollama is None:
        return session
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    scene = str(craft.get("scene") or "")
    if not prompt:
        return session
    if not identity.craft_is_thin(prompt, scene):
        return session
    cfg = await get_runtime_config(db)
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
        raise MuseError("no craft yet — start the table first")

    if not still:
        session = await densify_craft_if_needed(db, ollama, session)
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
            "総監督、イメージボード上げます。これでいい？OKなら本番、ダメなら指摘ください。"
            if locale.startswith("ja") else
            "Showrunner — image board going up. Good? OK to shoot, or note what to fix."
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
        raise MuseError("nothing to shoot yet")

    session = await densify_craft_if_needed(db, ollama, session)
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
            "OK受領。本番撮影に入ります。"
            if locale.startswith("ja") else
            "OK received. Going to final shoot."
        ),
    )
    _publish_chat(sid, msg)

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


# ── legacy entry points (tests / old clients) ───────────────────────────────
async def run_draft(db, ollama, comfy, spooler, session: dict[str, Any]) -> dict[str, Any]:
    """Compatibility: open table then immediately request a board."""
    session = await start_table(db, ollama, session)
    return await request_board(db, comfy, spooler, session, ollama=ollama)


async def cancel_draft(db, spooler, session: dict[str, Any]) -> dict[str, Any]:
    job_id = str((session.get("board") or {}).get("job_id") or "")
    if job_id:
        await spooler.cancel(job_id)
    session["board"] = {}
    session["status"] = "chat"
    session_db.log(session, "board", "cancelled")
    await session_db.save(db, session)
    return session


async def run_refine(
    db, ollama, comfy, spooler, session: dict[str, Any], indices: list[int],
) -> dict[str, Any]:
    """BCD removed — approving the board shoots instead."""
    raise MuseError(
        "描き直しチェーンは廃止しました。チャットで指示するか、OKで本番撮影してください。"
    )

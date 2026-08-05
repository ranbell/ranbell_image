"""Muse studio — showrunner chat, crew table-read, board, OK, shoot.

The user is 総監督. Muses discuss in character until a board is shown and the
showrunner says OK. There is no B/C/D pickup chain anymore.
"""
from __future__ import annotations

import logging
import random
import re
import time
import uuid
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
_OK_RE = re.compile(
    r"^\s*(ok|okay|lgtm|ship\s*it|go|yes|yep|approved?|いいよ|よし|おｋ|おけ|"
    r"OK|これでいい|それでいい|撮って|撮影|本番|決定|確定|ゴー)\s*[!！.。]*\s*$",
    re.I,
)


class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _identity_tags(session: dict[str, Any]) -> list[str]:
    character = session.get("character") or {}
    return [str(t) for t in (character.get("identity_tags") or []) if str(t).strip()]


def _framing(inputs: dict[str, Any]) -> str:
    return identity.normalize_framing(str(inputs.get("framing") or "auto"))


def _text_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("model") or "")


def _num_ctx(inputs: dict[str, Any], cfg: dict[str, Any]) -> int | None:
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None


def _chat_append(session: dict[str, Any], *, role: str, text: str,
                 muse_id: str = "", name: str = "") -> dict[str, Any]:
    msg = {
        "id": str(uuid.uuid4()),
        "role": role,
        "muse_id": muse_id,
        "name": name,
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
    # Resolve crew when preset or ids change.
    if "crew_preset" in patch or "crew_ids" in patch:
        ids = crew.resolve_crew(
            preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
            crew_ids=list(inputs.get("crew_ids") or []) or None,
        )
        inputs["crew_ids"] = [i for i in ids if i != "finisher"]
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
    inputs = _inputs(session)
    character = session.get("character") or {}
    if not character or not str(inputs.get("theme") or "").strip():
        session["brief"] = ""
        return
    session["brief"] = brief_mod.build(
        character,
        str(inputs.get("theme") or ""),
        str(inputs.get("style") or ""),
        framing=_framing(inputs),
    )


def _crew_ids(session: dict[str, Any]) -> list[str]:
    inputs = _inputs(session)
    return crew.resolve_crew(
        preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
        crew_ids=list(inputs.get("crew_ids") or []) or None,
    )


async def _run_muse_turn(
    ollama, session: dict[str, Any], muse_id: str, user_prompt: str,
    *, cfg: dict[str, Any],
) -> chain.MuseTurn:
    inputs = _inputs(session)
    sid = session["session_id"]
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": muse_id,
        "name": _muse_display_name(session, muse_id),
    })
    return await chain.run_muse(
        ollama, muse_id=muse_id, user_prompt=user_prompt,
        model=_text_model(inputs),
        num_ctx=_num_ctx(inputs, cfg),
        identity_tags=_identity_tags(session),
        framing=_framing(inputs),
        brief=str(session.get("brief") or ""),
        think=False,
        on_token=_token_publisher(sid, muse_id),
    )


def _muse_display_name(session: dict[str, Any], muse_id: str) -> str:
    m = crew.MUSES[muse_id]
    locale = str(_inputs(session).get("locale") or "ja")
    if locale.startswith("ja"):
        return str(m.get("name_ja") or m["name"])
    return str(m["name"])


def _apply_turn(session: dict[str, Any], turn: chain.MuseTurn) -> dict[str, Any]:
    m = crew.MUSES[turn.muse_id]
    craft = session.setdefault("craft", {})
    craft["prompt"] = turn.prompt
    craft["tags"] = turn.tags
    craft["scene"] = turn.scene
    if turn.muse_id in ("beat", "spine") or not craft.get("pose_intent"):
        craft["pose_intent"] = turn.pose_intent
    name = _muse_display_name(session, turn.muse_id)
    say = turn.say or f"（{name}が台本を更新した。）"
    msg = _chat_append(
        session, role="muse", text=say,
        muse_id=turn.muse_id, name=name,
    )
    _publish_chat(session["session_id"], msg)
    events.publish(session["session_id"], {
        "type": "muse_speaking", "muse_id": turn.muse_id, "name": name,
    })
    events.publish(session["session_id"], {
        "type": "craft_updated", "prompt": turn.prompt, "muse_id": turn.muse_id,
    })
    return msg


def _table_user_prompt(session: dict[str, Any], *, note: str = "") -> str:
    craft = session.get("craft") or {}
    previous = str(craft.get("prompt") or "")
    pose = str(craft.get("pose_intent") or "")
    base = brief_mod.with_previous(
        str(session.get("brief") or ""), previous, pose=pose,
    )
    if note.strip():
        return (
            f"{base}\n\n"
            f"SHOW RUNNER NOTE (総監督 — treat as absolute creative direction):\n"
            f"{note.strip()}\n"
            f"Answer their note. Revise TAGS/SCENE to satisfy it without breaking Carry."
        )
    return base


# ── open the table ──────────────────────────────────────────────────────────
async def start_table(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """Cast crew opens: full table-read, then offer an image board."""
    missing = missing_inputs(session)
    if missing:
        raise MuseError(f"missing: {', '.join(missing)}")

    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    session["status"] = "discussing"
    session["chat"] = []
    session["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": ""}
    session["board"] = {}
    session["shoot"] = {}
    await session_db.save(db, session)

    locale = str(_inputs(session).get("locale") or "ja")
    open_ja = (
        "総監督、打ち合わせを始めます。テーマに合わせて班が台本を継いでいきます。"
        "無理難題歓迎です。途中でイメージボードを出しますので、OKかコメントをください。"
    )
    open_en = (
        "Showrunner, table read is open. The crew will pass the craft down the "
        "line. Hard notes welcome. We will put up an image board — OK or comment."
    )
    sys_msg = _chat_append(
        session, role="system",
        text=open_ja if locale.startswith("ja") else open_en,
        name="Studio",
    )
    _publish_chat(sid, sys_msg)

    for muse_id in _crew_ids(session):
        turn = await _run_muse_turn(
            ollama, session, muse_id, _table_user_prompt(session), cfg=cfg,
        )
        _apply_turn(session, turn)
        await session_db.save(db, session, publish=False)

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
    session_db.log(session, "table", f"{len(_crew_ids(session))} muses")
    await session_db.save(db, session)
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
        session = await start_table(db, ollama, session)

    sid = session["session_id"]
    user_msg = _chat_append(session, role="user", text=text, name="総監督")
    _publish_chat(sid, user_msg)
    await session_db.save(db, session)

    if _OK_RE.match(text) or text.strip() in ("OK", "ok", "ＯＫ"):
        return await approve_and_shoot(db, comfy, spooler, session, ollama=ollama)

    if re.search(r"ボード|board|試写|イメージ", text, re.I):
        return await request_board(db, comfy, spooler, session, ollama=ollama)

    # Crew answers the hard note — pick specialists by keyword, else core desk.
    responders = _pick_responders(text, _crew_ids(session))
    session["status"] = "discussing"
    await session_db.save(db, session)
    cfg = await get_runtime_config(db)

    for muse_id in responders:
        turn = await _run_muse_turn(
            ollama, session, muse_id,
            _table_user_prompt(session, note=text), cfg=cfg,
        )
        _apply_turn(session, turn)
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
    """Heuristic cast for a showrunner note — always ends with finisher if cast."""
    n = note.lower()
    want: list[str] = []
    pairs = [
        (r"服|衣装|outfit|dress|cloth|ウェア|コーデ", "wardrobe"),
        (r"カメラ|画角|アングル|寄り|引き|lens|camera|構図", "lens"),
        (r"光|照明|影|逆光|light", "gaffer"),
        (r"背景|場所|物|セット|prop|background", "propshop"),
        (r"ポーズ|動き|姿勢|pose", "spine"),
        (r"顔|表情|目線|face|expression", "faces"),
        (r"色|カラー|color", "palette"),
        (r"天気|霧|雨|空気|atmosphere", "weather"),
        (r"画風|スタイル|style", "ink"),
    ]
    for pat, mid in pairs:
        if re.search(pat, n) and mid in crew_ids:
            want.append(mid)
    if not want:
        for mid in ("beat", "spine", "lens", "wardrobe", "hook"):
            if mid in crew_ids:
                want.append(mid)
    # Unique, catalog order, cap 4, always finisher last if in crew.
    ordered = [m for m in crew_ids if m in set(want) and m != "finisher"][:4]
    if "finisher" in crew_ids:
        ordered.append("finisher")
    return ordered or [crew_ids[0], "finisher"]


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


async def request_board(
    db, comfy, spooler, session: dict[str, Any], ollama=None,
) -> dict[str, Any]:
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    if not prompt:
        raise MuseError("no craft yet — start the table first")

    inputs = _inputs(session)
    sid = session["session_id"]
    seed = random.randint(0, (1 << 64) - 1)
    locale = str(inputs.get("locale") or "ja")

    await _maybe_unload(ollama, session)

    ask = _chat_append(
        session, role="muse", muse_id="lens",
        name=_muse_display_name(session, "lens"),
        text=(
            "総監督、イメージボード上げます。これでいい？OKなら本番、ダメなら指摘ください。"
            if locale.startswith("ja") else
            "Showrunner — image board going up. Good? OK to shoot, or note what to fix."
        ),
    )
    _publish_chat(sid, ask)

    session["board"] = {
        "prompt": prompt,
        "seed": seed,
        "job_id": "",
        "images": [],
        "pending": True,
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

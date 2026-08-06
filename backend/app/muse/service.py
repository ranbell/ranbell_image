"""Muse studio — brief, look, ask, refine, shoot.

The shape here is the point. An earlier version ran eighteen seats to completion
before the Showrunner saw anything: 513 seconds, of which 200 was the model
loading, and if the direction was wrong you found out eight minutes late. Worse,
the seats were arguing about a picture none of them had seen, so a board came
back 66% pure black and the next round of notes was answered by a crew still
talking about gravity and gaze.

So:

  Act 1  the planner settles where and when; the lead settles the performance
  Act 2  two cheap 512px probes — her on white, the room with nobody in it
  Act 3  the Showrunner looks at both and says which is wrong
  Act 4  enrich adds, reduce cuts, probe, measure, repeat until the numbers pass
  Act 5  full board, OK, final shoot

Splitting the probe is not presentation. It is the only way the measurements
mean anything: one merged frame cannot say whether the light or the character
made it dark, and WD14 cannot check the object ledger while a girl fills the
frame.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from ..characters import presets as presets_db
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
from . import brief as brief_mod
from . import chain, crew, critique, events, harvest, identity, probe, runner, session_db
from .schema import missing_inputs, new_session

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _nullcontext():
    yield

# The last probe frames, per session. Deliberately NOT on the session dict —
# that is serialised into Qdrant, and a few hundred KB of PNG per round has no
# business in the document store. Bounded so a long-lived process cannot grow
# without limit.
_PROBE_FRAMES: dict[str, dict[str, bytes]] = {}
_PROBE_FRAME_SESSIONS = 8


def _keep_frame(session_id: str, kind: str, data: bytes) -> None:
    frames = _PROBE_FRAMES.setdefault(session_id, {})
    frames[kind] = data
    while len(_PROBE_FRAMES) > _PROBE_FRAME_SESSIONS:
        _PROBE_FRAMES.pop(next(iter(_PROBE_FRAMES)), None)


def _frames(session: dict) -> dict[str, bytes]:
    return _PROBE_FRAMES.get(str(session.get("session_id") or ""), {})

# Showrunner approval — Japanese / English.
_OK_RE = re.compile(
    r"^\s*(ok|okay|lgtm|ship\s*it|go|yes|yep|approved?|いいよ|よし|おｋ|おけ|"
    r"OK|これでいい|それでいい|撮って|撮影|本番|決定|確定|ゴー|進めて)\s*[!！.。]*\s*$",
    re.I,
)
_OK_NATURAL_RE = re.compile(
    r"(?i)\b(ok|okay|lgtm|go)\b|お[ｋkけ]|いいよ|よし|ゴー|"
    r"これでいい|それでいい|撮って|撮影|本番|決定|確定|進めて|approved?"
)
_OK_DENY_RE = re.compile(
    r"じゃない|じゃなく|ではなく|だめ|ダメ|もっと|やめて|待って|まだ|"
    r"not\s*ok|don't|dont|wait|more|nope",
    re.I,
)


def _is_approve(text: str) -> bool:
    """True when the Showrunner is green-lighting the next stage."""
    t = (text or "").strip()
    if not t:
        return False
    if _OK_RE.match(t):
        return True
    if len(t) <= 48 and _OK_NATURAL_RE.search(t) and not _OK_DENY_RE.search(t):
        return True
    return False


class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


# ── small readers ───────────────────────────────────────────────────────────
def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _identity_tags(session: dict[str, Any]) -> list[str]:
    character = session.get("character") or {}
    return [str(t) for t in (character.get("identity_tags") or []) if str(t).strip()]


def _framing(inputs: dict[str, Any]) -> str:
    return identity.normalize_framing(str(inputs.get("framing") or "auto"))


def _subject_tags(session: dict[str, Any]) -> list[str]:
    character = session.get("character") or {}
    return identity.subject_tags([character] if character else [])


def _style(session: dict[str, Any]) -> str:
    return str(_inputs(session).get("style") or "").strip()


def _text_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("model") or "")


def _vision_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("vision_model") or "") or str(inputs.get("model") or "")


def _num_ctx(inputs: dict[str, Any], cfg: dict[str, Any]) -> int | None:
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None


def _locale_ja(session: dict[str, Any]) -> bool:
    return str(_inputs(session).get("locale") or "ja").startswith("ja")


def negative_for(session: dict[str, Any]) -> str:
    inputs = _inputs(session)
    return identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.opposing_negative(_identity_tags(session)),
        identity.framing_negative(_framing(inputs)),
    )


# ── chat plumbing ───────────────────────────────────────────────────────────
def _chat_append(
    session: dict[str, Any], *, role: str, text: str,
    muse_id: str = "", name: str = "", kind: str = "",
) -> dict[str, Any]:
    msg = {
        "id": str(uuid.uuid4()), "role": role, "muse_id": muse_id, "name": name,
        "kind": kind or ("craft" if role == "muse" else role),
        "text": text, "at": time.time(),
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


def _seat_name(session: dict[str, Any], seat: str) -> str:
    if seat == "actress":
        ch = session.get("character") or {}
        p = ch.get("personality") or {}
        if _locale_ja(session):
            return str(ch.get("name_ja") or p.get("preset_name_ja")
                       or ch.get("name") or "主演")
        return str(ch.get("name") or p.get("preset_name") or "Lead")
    r = crew.ROLES[seat]
    return r["name_ja"] if _locale_ja(session) else r["name"]


def _say(session: dict[str, Any], seat: str, text: str) -> None:
    if not (text or "").strip():
        return
    msg = _chat_append(
        session, role="muse", text=text.strip(),
        muse_id=seat, name=_seat_name(session, seat), kind="craft",
    )
    _publish_chat(session["session_id"], msg)


def _studio(session: dict[str, Any], ja: str, en: str) -> None:
    msg = _chat_append(
        session, role="system", name="Studio", text=ja if _locale_ja(session) else en,
    )
    _publish_chat(session["session_id"], msg)


# ── session lifecycle ───────────────────────────────────────────────────────
async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    session["inputs"] = {**_inputs(session), **{k: v for k, v in patch.items() if v is not None}}
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

    Lighting and colour used to be handed her summary, inner life and tastes on
    every call. None of it is their craft, and it was the most evocative text in
    their context — so it became the language the whole script was written in.
    """
    inputs = _inputs(session)
    character = session.get("character") or {}
    if not character or not str(inputs.get("theme") or "").strip():
        session["brief"] = session["brief_lite"] = ""
        return
    common = dict(
        theme=str(inputs.get("theme") or ""), style=_style(session),
        framing=_framing(inputs), plan=session.get("plan") or {},
        notes=list(session.get("notes") or []),
    )
    session["brief"] = brief_mod.build(character, reference="full", **common)
    session["brief_lite"] = brief_mod.build(character, reference="digest", **common)


_ACTING = ("actress",)


def _brief_for(session: dict[str, Any], seat: str) -> str:
    if seat in _ACTING:
        return str(session.get("brief") or "")
    return str(session.get("brief_lite") or session.get("brief") or "")


# ── the shot sheet ──────────────────────────────────────────────────────────
def _shot(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("shot") or {}


def _render_prompt(session: dict[str, Any]) -> str:
    return identity.render_shot(
        _shot(session),
        identity_tags=_identity_tags(session),
        subject=_subject_tags(session),
        style=_style(session),
        framing=_framing(_inputs(session)),
        slot_order=crew.SLOT_ORDER,
    )


def _sheet_note(session: dict[str, Any]) -> str:
    """The sheet as the crew reads it — who wrote what, so nobody retypes it."""
    shot = _shot(session)
    if not shot:
        return ""
    lines = ["SHOT SHEET SO FAR (do not retype it — only write your own lines):"]
    for slot in crew.SLOT_ORDER:
        value = shot.get(slot)
        text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")
        if text.strip():
            lines.append(f"- {slot}: {text}")
    return "\n".join(lines)


def _recent_talk(session: dict[str, Any], *, limit: int = 3) -> str:
    lines = [
        f"- {m.get('name')}: {m.get('text')}"
        for m in (session.get("chat") or [])[-limit * 2:]
        if m.get("role") == "muse"
    ]
    return "\n".join(lines[-limit:])


def _seat_prompt(
    session: dict[str, Any], seat: str, *, note: str = "", screening: str = "",
) -> str:
    parts = [_brief_for(session, seat)]
    sheet = _sheet_note(session)
    if sheet:
        parts.append(sheet)
    if screening.strip():
        parts.append(screening.strip())
    talk = _recent_talk(session)
    if talk:
        parts.append(
            "JUST SAID (answer it in SAY — do not carry their wording into your "
            "own lines):\n" + talk
        )
    if note.strip():
        parts.append(
            "SHOW RUNNER NOTE (総監督 — absolute creative direction):\n"
            f"{note.strip()}\nAnswer it. Change your lines so the note is simply true."
        )
    return "\n\n".join(p for p in parts if p.strip())


# ── one seat's turn ─────────────────────────────────────────────────────────
async def _run_seat(
    ollama, session: dict[str, Any], seat: str, *, cfg: dict[str, Any],
    note: str = "", screening: str = "", images: list[bytes] | None = None,
) -> chain.SeatTurn | None:
    inputs = _inputs(session)
    sid = session["session_id"]
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": seat, "name": _seat_name(session, seat),
    })
    try:
        turn = await chain.run_seat(
            ollama, seat=seat,
            user_prompt=_seat_prompt(session, seat, note=note, screening=screening),
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            images=images or None,
            character=session.get("character") or {},
            style=_style(session),
            on_token=_token_publisher(sid, seat),
        )
    except chain.ChainError:
        logger.warning("[muse] %s produced nothing", seat, exc_info=True)
        return None

    session["shot"] = chain.apply_turn(_shot(session), turn)
    if seat == "plan":
        session["plan"] = {
            k.lower().replace(" ", "_"): v for k, v in turn.fields.items()
        }
        _rebuild_brief(session)
    _say(session, seat, turn.say)
    if turn.blind and images:
        _note_blind(session)
    events.publish(sid, {"type": "craft_updated", "muse_id": seat,
                         "shot": session["shot"]})
    return turn


def _note_blind(session: dict[str, Any]) -> None:
    if session.get("blind_model"):
        return
    session["blind_model"] = True
    _studio(
        session,
        "このモデルは絵を読めないようなので、以降は測定値だけで進めます。"
        "vision_model に画像を読めるモデルを指定すると、班が実際の絵を見て話せます。",
        "This model cannot read the render — continuing on measurements alone. "
        "Set vision_model to an image-capable model so the crew can see it.",
    )


# ── probes ──────────────────────────────────────────────────────────────────
def _probe_seed(session: dict[str, Any]) -> int:
    """One seed for the whole session.

    If it moved between rounds there would be no way to tell whether a change
    helped or the dice did.
    """
    seed = int(session.get("probe_seed") or 0)
    if not seed:
        seed = random.randint(0, (1 << 63) - 1)
        session["probe_seed"] = seed
    return seed


async def _wd14(db, data: bytes, session: dict[str, Any]) -> list[str]:
    """What actually rendered, according to a tagger rather than to the crew."""
    inputs = _inputs(session)
    try:
        cfg = await get_runtime_config(db)
        tags = await harvest.read_tags(
            data,
            threshold=float(inputs.get("wd14_threshold") or 0.2),
            model_dir=str(cfg.get("wd14_model_dir") or "") or None,
            drop_rating_tags=bool(inputs.get("drop_rating_tags")),
            drop_character_tags=bool(inputs.get("drop_character_tags", True)),
            identity_tags=_identity_tags(session),
        )
    except Exception:
        logger.debug("[muse] wd14 read failed", exc_info=True)
        return []
    return [t.strip() for t in tags.split(",") if t.strip()]


async def _take_probe(
    db, comfy, session: dict[str, Any], shot: probe.ProbeShot,
    *, spooler=None, ollama=None,
) -> critique.Reading | None:
    inputs = _inputs(session)
    # A probe is a render like any other. It is awaited inline rather than
    # submitted, so it has to take the GPU resource by hand — otherwise it can
    # put a second graph on the card while a board is being drawn.
    getter = getattr(spooler, "resource_for", None) if spooler else None
    gpu = getter(JobLane.GENERATION) if getter else None
    ctx = gpu.acquire() if gpu is not None else _nullcontext()
    async with ctx:
        await _free_the_card(ollama, session)
        data = await probe.render(
            comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=shot.positive,
            negative=identity.merge_negative(negative_for(session), shot.negative),
            seed=_probe_seed(session),
            size=int(inputs.get("probe_size") or 512),
            steps=int(inputs.get("probe_steps") or 12),
            cfg=float(inputs.get("draft_cfg") or 4.0),
        )
    if not data:
        return None

    ledger = [str(o) for o in (_shot(session).get("objects") or [])]
    reading = critique.measure(
        data,
        must_appear=ledger if shot.kind != probe.POSE else [],
        seen_tags=await _wd14(db, data, session),
        # A pose probe is rendered on white on purpose; its brightness says
        # nothing about the picture being built.
        check_exposure=shot.kind != probe.POSE,
    )
    events.publish(session["session_id"], {
        "type": "probe", "kind": shot.kind,
        "image": base64.b64encode(data).decode(),
        "ok": reading.ok, "failures": list(reading.failures),
        "mean_luma": round(reading.mean_luma, 1),
        "dead_frac": round(reading.dead_frac, 3),
    })
    session.setdefault("probes", {})[shot.kind] = {
        "ok": reading.ok, "failures": list(reading.failures),
        "mean_luma": round(reading.mean_luma, 1),
        "dead_frac": round(reading.dead_frac, 3),
        "missing": list(reading.missing),
        "at": time.time(),
    }
    _keep_frame(session["session_id"], shot.kind, data)
    return reading


async def _probe_split(
    db, comfy, session: dict[str, Any], *, spooler=None, ollama=None,
) -> dict[str, critique.Reading]:
    """Her on white, the room with nobody in it. Rendered concurrently."""
    shots = probe.split_prompts(
        _shot(session),
        identity_tags=_identity_tags(session),
        subject=_subject_tags(session),
        style=_style(session),
        framing=_framing(_inputs(session)),
        negative=negative_for(session),
        slot_order=crew.SLOT_ORDER,
    )
    # One at a time — see probe.SEQUENTIAL. Running the pair through
    # asyncio.gather hung the session outright: both renders share one ComfyUI
    # websocket client id, so the second connection swallowed the first's
    # completion message and that probe waited forever.
    out: dict[str, critique.Reading] = {}
    for s in shots:
        try:
            reading = await _take_probe(db, comfy, session, s,
                                        spooler=spooler, ollama=ollama)
        except Exception:
            logger.warning("[muse] %s probe failed", s.kind, exc_info=True)
            continue
        if reading is not None:
            out[s.kind] = reading
    return out


async def _probe_merged(
    db, comfy, session: dict[str, Any], *, spooler=None, ollama=None,
) -> critique.Reading | None:
    shot = probe.ProbeShot(
        kind=probe.MERGED, positive=_render_prompt(session), negative="",
    )
    return await _take_probe(db, comfy, session, shot, spooler=spooler, ollama=ollama)


def _screening_note(readings: dict[str, critique.Reading]) -> str:
    blocks = []
    for kind, r in readings.items():
        blocks.append(f"[{kind.upper()} PROBE]\n{r.as_note()}")
    return "\n\n".join(blocks)


# ── Act 1 + 2: brief, then look ─────────────────────────────────────────────
async def start_table(
    db, ollama, session: dict[str, Any], comfy=None, spooler=None,
) -> dict[str, Any]:
    """Settle the situation and the performance, then show two probes."""
    missing = missing_inputs(session)
    if missing:
        raise MuseError(f"missing: {', '.join(missing)}")

    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    session.update({
        "status": "discussing", "chat": [], "shot": {}, "plan": {}, "probes": {},
        "craft": {"prompt": "", "round": 0}, "board": {}, "shoot": {},
    })
    session.pop("blind_model", None)
    _rebuild_brief(session)
    await session_db.save(db, session)

    _studio(
        session,
        "総監督、まず場所と芝居だけ決めます。人物と背景を別々に描いて出しますので、"
        "方向が違っていたらそこで言ってください。",
        "Showrunner — settling the place and the performance first. You will get "
        "the character and the setting as two separate sketches; say if either is wrong.",
    )

    for seat in ("plan", "actress"):
        await _run_seat(ollama, session, seat, cfg=cfg)
        await session_db.save(db, session, publish=False)

    await _show_probes(db, ollama, comfy, session, cfg=cfg, spooler=spooler)
    session["status"] = "chat"
    session_db.log(session, "brief", str((session.get("plan") or {}).get("place", "")))
    await session_db.save(db, session)
    return session


async def _show_probes(
    db, ollama, comfy, session: dict[str, Any], *, cfg: dict, spooler=None,
) -> None:
    """Render the split probes, measure them, and let the checker say one thing."""
    if comfy is None:
        _studio(session, "（ComfyUI に接続していないので試写は省略します）",
                "(no ComfyUI connection — skipping the probe)")
        return
    readings = await _probe_split(db, comfy, session, spooler=spooler, ollama=ollama)
    if not readings:
        _studio(session, "試写が撮れませんでした。台本のまま進めます。",
                "Could not take the probe. Continuing from the script.")
        return

    await _run_seat(
        ollama, session, "check", cfg=cfg,
        screening=_screening_note(readings),
        images=list(_frames(session).values())[:2],
    )
    _studio(
        session,
        "人物と背景を出しました。どちらか直すならコメントを、"
        "このまま詰めるなら「OK」をください。",
        "Character and setting are up. Comment on either, or say OK to start "
        "tightening.",
    )


# ── Act 3: the Showrunner speaks ────────────────────────────────────────────
async def post_chat(
    db, ollama, comfy, spooler, session: dict[str, Any], text: str,
) -> dict[str, Any]:
    """A note re-settles the sheet; OK moves to the next stage."""
    text = (text or "").strip()
    if not text:
        raise MuseError("empty message")
    if missing_inputs(session):
        raise MuseError(f"missing: {', '.join(missing_inputs(session))}")
    if not _shot(session):
        session = await start_table(db, ollama, session, comfy=comfy, spooler=spooler)

    sid = session["session_id"]
    _publish_chat(sid, _chat_append(session, role="user", text=text, name="総監督"))
    await session_db.save(db, session)

    if _is_approve(text):
        # OK means "next stage": tighten if we have not yet, otherwise shoot.
        if (session.get("board") or {}).get("images"):
            return await approve_and_shoot(db, comfy, spooler, session, ollama=ollama)
        return await refine(db, ollama, comfy, spooler, session)

    if re.search(r"ボード|board|試写|イメージ", text, re.I):
        return await request_board(db, comfy, spooler, session, ollama=ollama)

    session["status"] = "discussing"
    session.setdefault("notes", []).append(text)
    _rebuild_brief(session)
    await session_db.save(db, session)
    cfg = await get_runtime_config(db)

    # The planner owns place and light; the lead owns the performance. Both hear
    # the note — which one it was about is theirs to work out, not Python's.
    for seat in ("plan", "actress"):
        await _run_seat(ollama, session, seat, cfg=cfg, note=text)
        await session_db.save(db, session, publish=False)

    await _show_probes(db, ollama, comfy, session, cfg=cfg, spooler=spooler)
    session["status"] = "chat"
    await session_db.save(db, session)
    return session


# ── Act 4: tighten until the numbers pass ───────────────────────────────────
async def refine(
    db, ollama, comfy, spooler, session: dict[str, Any],
) -> dict[str, Any]:
    """Enrich adds, reduce cuts, probe, measure. Stop on pass or on the cap."""
    cfg = await get_runtime_config(db)
    inputs = _inputs(session)
    rounds = max(1, int(inputs.get("probe_max_rounds") or 3))
    session["status"] = "discussing"
    _studio(session, "では詰めます。何度か試写して、数値が通ったらボードを出します。",
            "Tightening now. A few probes, then the board once the numbers pass.")

    reading: critique.Reading | None = None
    for i in range(rounds):
        screening = reading.as_note() if reading else ""
        for seat in ("enrich", "reduce"):
            await _run_seat(ollama, session, seat, cfg=cfg, screening=screening)
            await session_db.save(db, session, publish=False)

        if comfy is None:
            break
        reading = await _probe_merged(db, comfy, session, spooler=spooler, ollama=ollama)
        session["craft"]["round"] = i + 1
        await session_db.save(db, session, publish=False)
        if reading is None:
            break
        if reading.ok:
            _studio(session, f"通りました（{i + 1}周）。ボードを出します。",
                    f"Passed on round {i + 1}. Putting up the board.")
            break

        await _run_seat(
            ollama, session, "check", cfg=cfg, screening=reading.as_note(),
            images=[f] if (f := _frames(session).get(probe.MERGED)) else None,
        )
        await session_db.save(db, session, publish=False)
    else:
        if reading is not None and not reading.ok:
            # Never ship a quiet failure: say exactly what did not come right.
            _studio(
                session,
                f"{rounds}周してもここが直りませんでした: {'; '.join(reading.failures)}。"
                "このままボードを出しますので、指示をください。",
                f"Still failing after {rounds} rounds: {'; '.join(reading.failures)}. "
                "Putting the board up anyway — tell me how you want it fixed.",
            )

    return await request_board(db, comfy, spooler, session, ollama=ollama)


# ── Act 5: board and shoot ──────────────────────────────────────────────────
async def _free_the_card(ollama, session: dict[str, Any]) -> None:
    """Take the language model off the GPU before anything renders.

    Measured on the box this runs on: a 15.6GB card, and the 26B MoE holding
    12.5GB of it. ComfyUI was left 0.8GB and ComfyUI died. They do not share —
    the LLM and the sampler have to take turns, and the seam is here.

    `unload_vlm` is the escape hatch for a card big enough to hold both, not the
    switch that enables this. It used to default to off on the strength of a
    claim nobody had measured.
    """
    if ollama is None or _inputs(session).get("unload_vlm") is False:
        return
    try:
        await ollama.unload(str(_inputs(session).get("model") or "") or None)
    except Exception:
        logger.debug("[muse] could not unload before render", exc_info=True)


async def request_board(
    db, comfy, spooler, session: dict[str, Any], ollama=None,
) -> dict[str, Any]:
    prompt = _render_prompt(session)
    if not prompt.strip():
        raise MuseError("no shot sheet yet — start the table first")
    session["craft"]["prompt"] = prompt

    sid = session["session_id"]
    await _free_the_card(ollama, session)
    _studio(session, "ボードを上げます。これでいい？OKなら本番、ダメなら指摘ください。",
            "Board going up. Good? OK to shoot, or say what to fix.")

    session["board"] = {
        "prompt": prompt, "seed": random.randint(0, (1 << 64) - 1), "job_id": "",
        "images": [], "pending": True,
        "round": int((session.get("board") or {}).get("round") or 0) + 1,
    }
    session["status"] = "boarding"
    await session_db.save(db, session)

    session["board"]["job_id"] = spooler.submit(
        JobLane.GENERATION, "muse_board", runner.run_board_job,
        meta={"session_id": sid, "step": "board"},
        db=db, comfy=comfy, session_id=sid,
    )
    session_db.log(session, "board", f"round {session['board']['round']}")
    await session_db.save(db, session)
    return session


async def approve_and_shoot(
    db, comfy, spooler, session: dict[str, Any], ollama=None,
) -> dict[str, Any]:
    prompt = str((session.get("craft") or {}).get("prompt") or "") or _render_prompt(session)
    if not prompt.strip():
        raise MuseError("nothing to shoot yet")

    sid = session["session_id"]
    seed = int((session.get("board") or {}).get("seed") or 0) or random.randint(0, (1 << 64) - 1)
    await _free_the_card(ollama, session)
    _studio(session, "OK受領。本番撮影に入ります。", "OK received. Going to final shoot.")

    session["shoot"] = {"prompt": prompt, "seed": seed, "job_id": "",
                        "images": [], "pending": True}
    session["status"] = "shooting"
    await session_db.save(db, session)

    session["shoot"]["job_id"] = spooler.submit(
        JobLane.GENERATION, "muse_shoot", runner.run_shoot_job,
        meta={"session_id": sid, "step": "shoot"},
        db=db, comfy=comfy, session_id=sid,
    )
    session_db.log(session, "shoot", f"seed {seed}")
    await session_db.save(db, session)
    return session


async def cancel_draft(db, spooler, session: dict[str, Any]) -> dict[str, Any]:
    job_id = str((session.get("board") or {}).get("job_id") or "")
    if job_id:
        await spooler.cancel(job_id)
    session["board"] = {}
    session["status"] = "chat"
    session_db.log(session, "board", "cancelled")
    await session_db.save(db, session)
    return session


# ── legacy entry points (older clients) ─────────────────────────────────────
async def run_draft(db, ollama, comfy, spooler, session: dict[str, Any]) -> dict[str, Any]:
    session = await start_table(db, ollama, session, comfy=comfy, spooler=spooler)
    return await refine(db, ollama, comfy, spooler, session)


async def run_refine(db, ollama, comfy, spooler, session: dict[str, Any], indices: list[int]):
    raise MuseError(
        "描き直しチェーンは廃止しました。チャットで指示するか、OKで先へ進めてください。"
    )


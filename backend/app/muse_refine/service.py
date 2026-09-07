"""Muse Refine session orchestration — independent of muse.service."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..characters import presets_db
from ..muse import events, session_db
from ..muse.defaults import ALL_DEFAULTS
from ..muse.notebook import blank as notebook_blank
from . import assemble, debug as debug_mod, ledger as ledger_mod, writer

logger = logging.getLogger(__name__)

STUDIO = "muse_refine"


class RefineError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return dict(session.get("inputs") or {})


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    """Panel payload — keep it small and stable."""
    inputs = _inputs(session)
    craft = session.get("craft") or {}
    char = session.get("character") or {}
    return {
        "session_id": session.get("session_id"),
        "studio": STUDIO,
        "status": session.get("status"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "inputs": {
            "theme": inputs.get("theme", ""),
            "character_id": inputs.get("character_id", ""),
            "workflow": inputs.get("workflow", ""),
            "model": inputs.get("model", ""),
            "locale": inputs.get("locale", "ja"),
            "style": inputs.get("style", ""),
            "framing": inputs.get("framing", "auto"),
            "negative_prompt": inputs.get("negative_prompt", ""),
            "draft_count": inputs.get("draft_count", 1),
            "draft_steps": inputs.get("draft_steps", 20),
            "final_steps": inputs.get("final_steps", 30),
            "use_wd14": bool(inputs.get("use_wd14")),
            "enhance_quality": bool(inputs.get("enhance_quality")),
            "width": inputs.get("width"),
            "height": inputs.get("height"),
        },
        "character": {
            "character_id": char.get("character_id", ""),
            "name": char.get("name_ja") or char.get("name") or "",
        },
        "refine_ledger": session.get("refine_ledger") or ledger_mod.blank(),
        "craft": {
            "prompt": craft.get("prompt", ""),
            "now": craft.get("now", ""),
            "tags": craft.get("tags", ""),
            "scene": craft.get("scene", ""),
            "wd14_suggestions": craft.get("wd14_suggestions", ""),
            "picked_wd14": craft.get("picked_wd14", ""),
            "quality_tags": craft.get("quality_tags", ""),
            "support_tags": craft.get("support_tags", ""),
        },
        "chat": list(session.get("chat") or [])[-40:],
        "board": {
            "images": list((session.get("board") or {}).get("images") or [])[-4:],
            "status": (session.get("board") or {}).get("status", ""),
            "error": (session.get("board") or {}).get("error", ""),
        },
        "shoot": {
            "images": list((session.get("shoot") or {}).get("images") or [])[-4:],
            "status": (session.get("shoot") or {}).get("status", ""),
            "error": (session.get("shoot") or {}).get("error", ""),
        },
        # Observability only — UI debug pane. Never used for decisions.
        "refine_log": list(session.get("refine_log") or [])[-40:],
        "stage_ms": list(session.get("stage_ms") or [])[-20:],
        "turn_trace": list(session.get("turn_trace") or [])[-12:],
    }


def new_session(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        **ALL_DEFAULTS,
        "theme": "",
        "character_id": "",
        "workflow": "",
        "model": "",
        "locale": "ja",
        "use_wd14": False,
        "enhance_quality": False,
    }
    merged = {**base, **(inputs or {})}
    merged["use_wd14"] = bool(merged.get("use_wd14"))
    merged["enhance_quality"] = bool(merged.get("enhance_quality"))
    return {
        "session_id": str(uuid.uuid4()),
        "studio": STUDIO,
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "chat",
        "mode": "duet",
        "inputs": merged,
        "character": {},
        "refine_ledger": ledger_mod.blank(),
        "craft": {
            "prompt": "", "now": "", "tags": "", "scene": "",
            "wd14_suggestions": "", "picked_wd14": "", "quality_tags": "",
            "support_tags": "",
        },
        # Keep a blank notebook so muse.session_db.load → notebook.migrate is safe
        # when board/shoot runner reloads the row.
        "notebook": notebook_blank(partner=False),
        "chat": [],
        "board": {},
        "shoot": {},
        "banned": [],
        "struck": [],
        "notes": [],
        "standing": [],
        "refine_log": [],
        "stage_ms": [],
        "turn_trace": [],
    }


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def load_refine(db, session_id: str) -> dict[str, Any]:
    session = await session_db.load(db, session_id)
    if session is None:
        raise RefineError("session not found")
    if str(session.get("studio") or "") != STUDIO:
        raise RefineError("not a muse refine session")
    # Ensure ledger key exists after migrate.
    session.setdefault("refine_ledger", ledger_mod.blank())
    session.setdefault("craft", {})
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in patch.items() if v is not None}
    for flag in ("use_wd14", "enhance_quality"):
        if flag in clean:
            clean[flag] = bool(clean[flag])
    session["inputs"] = {**_inputs(session), **clean}
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise RefineError("character not found")
    session["character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "character_id": character_id}
    # Seed wearing from preset costume if ledger empty.
    led = dict(session.get("refine_ledger") or ledger_mod.blank())
    if not (led.get("wearing") or "").strip():
        costume = (preset.get("board") or {}).get("wearing") or ""
        # Common preset fields.
        wearing = (
            costume
            or preset.get("wearing")
            or ""
        )
        if not wearing:
            tags = preset.get("identity_tags") or []
            # Do not dump identity into wearing.
            wearing = ""
        if wearing:
            led["wearing"] = str(wearing).strip()
            session["refine_ledger"] = led
    await assemble.rebuild_craft(db, None, session)
    await session_db.save(db, session)
    return session


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
        "at": time.time(),
        **({"meta": meta} if meta else {}),
    }
    chat = list(session.get("chat") or [])
    chat.append(row)
    session["chat"] = chat[-80:]
    return row


def _chat_tail(session: dict[str, Any], n: int = 6) -> str:
    rows = list(session.get("chat") or [])[-n:]
    lines = []
    for r in rows:
        who = r.get("name") or r.get("role") or ""
        lines.append(f"{who}: {r.get('text') or ''}")
    return "\n".join(lines)


async def chat(
    db,
    ollama,
    session: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    import time

    text = (message or "").strip()
    if not text:
        raise RefineError("empty message")
    inputs = _inputs(session)
    model = str(inputs.get("model") or "")
    locale = str(inputs.get("locale") or "ja")
    char = session.get("character") or {}
    name = char.get("name_ja") or char.get("name") or "Muse"
    before = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}

    _append_chat(session, role="user", name="Director", text=text)
    events.publish(session["session_id"], {"type": "chat", "role": "user", "text": text})
    debug_mod.note(session, "director_line", detail=text[:240])

    led = dict(before)
    t0 = time.monotonic()
    patch = await writer.write_patch(
        ollama,
        model=model,
        user_line=text,
        ledger=led,
        recent=_chat_tail(session),
    )
    debug_mod.stage(session, "writer", t0)
    debug_mod.note(session, "writer_patch", detail=str(patch), patch=patch)

    if ledger_mod.touched_picture(patch):
        led = ledger_mod.apply_patch(led, patch)
        session["refine_ledger"] = led
        _append_chat(
            session,
            role="system",
            name="Ledger",
            text=f"patch {patch}",
            meta={"patch": patch},
        )

    t0 = time.monotonic()
    await assemble.rebuild_craft(db, ollama, session)
    debug_mod.stage(session, "assemble_pre_actress", t0)
    now = str((session.get("craft") or {}).get("now") or "")

    t0 = time.monotonic()
    say, propose = await writer.actress_turn(
        ollama,
        model=model,
        locale=locale,
        name=name,
        now=now,
        user_line=text,
        chat_tail=_chat_tail(session),
    )
    debug_mod.stage(session, "actress", t0)
    debug_mod.note(
        session, "actress",
        detail=(say or "")[:240],
        propose=propose or {},
    )
    _append_chat(session, role="assistant", name=name, text=say)
    events.publish(session["session_id"], {
        "type": "chat", "role": "assistant", "name": name, "text": say,
    })

    if ledger_mod.touched_picture(propose):
        led = ledger_mod.apply_patch(
            {**ledger_mod.blank(), **(session.get("refine_ledger") or {})},
            propose,
        )
        session["refine_ledger"] = led
        _append_chat(
            session,
            role="system",
            name="Ledger",
            text=f"muse propose {propose}",
            meta={"patch": propose, "source": "muse"},
        )
        t0 = time.monotonic()
        await assemble.rebuild_craft(db, ollama, session)
        debug_mod.stage(session, "assemble_after_propose", t0)

    craft = session.get("craft") or {}
    after = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    debug_mod.turn_trace(
        session,
        line=text,
        patch=patch,
        propose=propose,
        before=before,
        after=after,
        wd14=[t for t in str(craft.get("wd14_suggestions") or "").split(",") if t.strip()],
        picked_wd14=[t for t in str(craft.get("picked_wd14") or "").split(",") if t.strip()],
        quality=[t for t in str(craft.get("quality_tags") or "").split(",") if t.strip()],
    )

    session["status"] = "chat"
    await session_db.save(db, session)
    return session


async def rebuild(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    await assemble.rebuild_craft(db, ollama, session)
    await session_db.save(db, session)
    return session


async def start_board(db, request, session: dict[str, Any]) -> dict[str, Any]:
    """Enqueue a board render using muse runner (prompt from refine craft)."""
    from ..muse import runner
    from ..spooler.models import JobLane

    await assemble.rebuild_craft(db, request.app.state.ollama, session)
    prompt = str((session.get("craft") or {}).get("prompt") or "").strip()
    if not prompt:
        raise RefineError("prompt is empty")
    if not _inputs(session).get("workflow"):
        raise RefineError("workflow required")
    session["board"] = {
        "prompt": prompt,
        "status": "queued",
        "error": "",
        "images": [],
        "pending": True,
        "seed": 0,
        "job_id": "",
        "round": int((session.get("board") or {}).get("round") or 0) + 1,
    }
    session["status"] = "boarding"
    await session_db.save(db, session)

    spooler = request.app.state.spooler
    session["board"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_refine_board",
        runner.run_board_job,
        db=db,
        comfy=request.app.state.comfy,
        session_id=session["session_id"],
        ollama=request.app.state.ollama,
    )
    await session_db.save(db, session)
    return session


async def start_shoot(db, request, session: dict[str, Any]) -> dict[str, Any]:
    from ..muse import runner
    from ..spooler.models import JobLane

    await assemble.rebuild_craft(db, request.app.state.ollama, session)
    prompt = str((session.get("craft") or {}).get("prompt") or "").strip()
    if not prompt:
        raise RefineError("prompt is empty")
    if not _inputs(session).get("workflow"):
        raise RefineError("workflow required")
    session["shoot"] = {
        "prompt": prompt,
        "status": "queued",
        "error": "",
        "images": [],
        "pending": True,
        "seed": 0,
        "job_id": "",
    }
    session["status"] = "shooting"
    await session_db.save(db, session)

    spooler = request.app.state.spooler
    session["shoot"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_refine_shoot",
        runner.run_shoot_job,
        db=db,
        comfy=request.app.state.comfy,
        session_id=session["session_id"],
        ollama=request.app.state.ollama,
    )
    await session_db.save(db, session)
    return session


async def list_refine_sessions(db, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = await session_db.list_recent(db, limit=max(limit * 3, 40))
    # list_recent does not include studio; load lightly by id filter via scroll
    # is heavy — instead retrieve payloads for candidates.
    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("session_id")
        if not sid:
            continue
        full = await session_db.load(db, sid)
        if not full or str(full.get("studio") or "") != STUDIO:
            continue
        out.append({
            "session_id": sid,
            "status": full.get("status"),
            "theme": (_inputs(full).get("theme") or ""),
            "created_at": full.get("created_at"),
            "character": (full.get("character") or {}).get("name_ja")
            or (full.get("character") or {}).get("name")
            or "",
        })
        if len(out) >= limit:
            break
    return out

"""Muse session shape — chat studio with the user as showrunner.

Flow: cast a crew → table-read chat → image board ("これでいい？") →
showrunner OK → final shoot. Pickup refine (B/C/D) is gone; discussion and
boards replace it.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from . import crew
from .defaults import ALL_DEFAULTS

# Acts the panel rails through. "refine" removed — boards + OK replace it.
STEPS: tuple[str, ...] = (
    "setup",   # theme, character, cast
    "chat",    # table read + showrunner notes
    "board",   # image board awaiting OK
    "shoot",   # final render after OK
)


def new_session(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    preset = str((inputs or {}).get("crew_preset") or crew.DEFAULT_PRESET)
    crew_ids = crew.resolve_crew(preset=preset)
    return {
        "session_id": str(uuid.uuid4()),
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "setup",
        "inputs": {**ALL_DEFAULTS, **{
            "theme": "",
            "character_id": "",
            "workflow": "",
            "model": "",
            "llm_provider": "ollama",
            "locale": "ja",
            "crew_preset": preset,
            "crew_ids": [i for i in crew_ids if i != "finisher"],
        }, **(inputs or {})},
        "character": {},
        "brief": "",
        # Working craft the crew is building toward the board / shoot.
        "craft": {"prompt": "", "pose_intent": "", "tags": "", "scene": ""},
        "chat": [],           # [{id, role, muse_id, name, text, at}]
        "board": {},          # image board round
        "shoot": {},          # final images after OK
        # Legacy keys kept empty so older clients/tests do not explode.
        "draft": {},
        "selected": [],
        "chains": [],
        "timeline": [],
        "warnings": [],
    }


def board_images(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in ((session.get("board") or {}).get("images") or [])
            if isinstance(i, dict) and i.get("image_id")]


def shoot_images(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in ((session.get("shoot") or {}).get("images") or [])
            if isinstance(i, dict) and i.get("image_id")]


def step_state(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    status = str(session.get("status") or "setup")
    board = session.get("board") or {}
    shoot = session.get("shoot") or {}
    chat = session.get("chat") or []
    boarded = board_images(session)
    shot = shoot_images(session)

    return {
        "setup": {
            "done": status != "setup" or bool(session.get("brief")),
            "pending": False,
            "detail": "",
        },
        "chat": {
            "done": status in ("awaiting_ok", "shooting", "done") or bool(boarded),
            "pending": status == "discussing",
            "detail": str(len(chat)),
        },
        "board": {
            "done": bool(boarded) and not board.get("pending"),
            "pending": bool(board.get("pending")),
            "detail": str(len(boarded)) if boarded else "",
        },
        "shoot": {
            "done": status == "done" and bool(shot),
            "pending": bool(shoot.get("pending")) or status == "shooting",
            "detail": str(len(shot)) if shot else "",
        },
    }


def next_step(session: dict[str, Any]) -> str:
    status = str(session.get("status") or "setup")
    if status in ("setup",):
        return "setup"
    if status in ("chat", "discussing"):
        return "chat"
    if status in ("boarding", "awaiting_ok"):
        return "board"
    if status in ("shooting",):
        return "shoot"
    if status == "done":
        return "done"
    return "chat"


def missing_inputs(session: dict[str, Any]) -> list[str]:
    inputs = session.get("inputs") or {}
    needs: list[str] = []
    if not str(inputs.get("theme") or "").strip():
        needs.append("theme")
    if not str(inputs.get("character_id") or "").strip():
        needs.append("character")
    if not str(inputs.get("workflow") or "").strip():
        needs.append("workflow")
    if not str(inputs.get("model") or "").strip():
        needs.append("model")
    return needs


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    from . import crew as crew_mod
    return {
        **session,
        "steps": list(STEPS),
        "step_state": step_state(session),
        "next_step": next_step(session),
        "needs": missing_inputs(session),
        "roster": crew_mod.public_roster(),
    }

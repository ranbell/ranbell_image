"""Muse session shape — chat studio with the user as showrunner.

Flow: cast a crew → table-read chat → image board ("これでいい？") →
showrunner OK → final shoot. Pickup refine (B/C/D) is gone; discussion and
boards replace it.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from . import crew, facets, notebook
from . import pipeline_view as pipeline_view_mod
from .defaults import ALL_DEFAULTS

# Re-exported so callers have one obvious place to reach for it. Duet now
# migrates through notebook (which still runs facets.migrate for legacy rows).
migrate = notebook.migrate

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
            "locale": "ja",
            "crew_preset": preset,
            "crew_ids": [i for i in crew_ids if i not in ("finisher", "actress")],
        }, **(inputs or {})},
        "character": {},
        # "" is the crewed studio. "duet" is 主演撮り (lead shoot) — one or two
        # Muses, no crew; craft is written only when she is asked to get ready.
        "mode": str((inputs or {}).get("mode") or ""),
        "brief": "",
        # The same brief with the reference block cut down to traits. Every seat
        # that is not acting reads this one.
        "brief_lite": "",
        # Where, when, how lit, and the object ledger — settled by the plan seat
        # and re-stated in every brief so a chain of rewrites cannot relocate the
        # picture.
        "plan": {},
        # The locked outfit. Only Wardrobe writes it; costume_block re-states it in
        # every brief so no other seat can re-dress her. {} until Wardrobe speaks.
        "costume": {},
        # Everything the Showrunner has said, kept forever. A note used to live
        # only in the turn that answered it.
        "notes": [],
        # Working craft toward the board / shoot. On duet this is compiled from
        # `notebook` by the scripter (full replace). Crewed studio still builds
        # it seat-by-seat. Downstream (render, ledger, panel) always reads craft.
        "craft": {"prompt": "", "pose_intent": "", "tags": "", "scene": ""},
        # Living shot notebook (plain language). Duet source of truth.
        "notebook": notebook.blank(
            partner=bool(str((inputs or {}).get("partner_preset") or "").strip())
        ),
        # Legacy facet table — migration / older helpers only. Not duet truth.
        "facets": facets.blank_table(),
        # The Showrunner's direction, reconciled instead of stacked: one entry
        # per facet, and a new camera order REPLACES the previous camera order.
        # `notes` below is still appended for the chat log and the diary, but
        # on the facet path nothing renders it into a prompt — that append-only
        # list re-read in full on every turn is what made long sessions drift.
        "directives": {},     # facet -> {"text": str, "at": float}
        # Rules that belong to no single facet ("never show her feet").
        "standing": [],
        # The third memory — neither the chat (kept for voice) nor a facet's
        # current tags (the result). A short, plain-language record of what has
        # actually been decided, rewritten rather than appended to every time
        # the router reads a note: "added, then decided against" collapses to
        # one line instead of surviving as two contradictory facts. Handed to
        # every facet-writing turn, whether or not the router named that facet
        # this turn — this is what lets a LATER, unrelated rewrite of a facet
        # that quietly holds a stale duplicate correctly leave it out, because
        # the model was told the truth, not because a filter stripped it after
        # the fact. See `chain.ROUTE_SYSTEM`'s DIGEST field.
        "digest": "",
        # The composed prose, and the table revision it was composed from. An
        # unchanged shot is never composed twice.
        "composed": {"scene": "", "rev": 0, "at": 0.0},
        # Who added which tag, in order. Without it a bad frame can only be
        # traced back to a seat by guessing from the chat.
        "ledger": [],
        # What the Showrunner has refused. A refusal is state, not a sentence:
        # these are filtered out of every seat's answer and handed to the
        # sampler as a negative, because saying "do not draw X" in a positive
        # prompt makes X more likely, not less.
        "banned": [],
        # Indices of notes whose refusal has been carried out. Their text drops
        # out of the standing orders so the refused noun stops being re-read by
        # every seat on every turn.
        "carried_out": [],
        # Seats that have written craft. The cast is editable mid-session, so
        # one brought in late has to read the script before it gets opinions.
        "spoken": [],
        "chat": [],           # [{id, role, muse_id, name, text, at}]
        "board": {},          # image board round
        "shoot": {},          # the final take being made right now
        "shoots": [],         # finished takes before it — every ③ of the day
        # Drawn on the first render and held for the rest of the shoot, so the
        # only thing that changes between two takes is the script.
        "seed": 0,
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
    inputs = session.get("inputs") or {}
    # The roster carries this session's own direction, so the panel can show
    # what the current cast is pulling toward as seats are toggled.
    cast = crew_mod.resolve_crew(
        preset=str(inputs.get("crew_preset") or crew_mod.DEFAULT_PRESET),
        crew_ids=list(inputs.get("crew_ids") or []) or None,
    )
    still = session.get("direction_still") if isinstance(session.get("direction_still"), dict) else {}
    # Never ship the JPEG blob to the panel — only a presence flag + size.
    public_still = None
    if still and still.get("jpeg_b64"):
        public_still = {
            "at": still.get("at"),
            "bytes": still.get("bytes") or 0,
            "ready": True,
        }
    view = {
        **session,
        "steps": list(STEPS),
        "step_state": step_state(session),
        "next_step": next_step(session),
        "needs": missing_inputs(session),
        "roster": crew_mod.public_roster(session.get("character") or {}, cast),
        "style_in_use": crew_mod.base_style_for(
            cast, inputs.get("style") or "", inputs.get("look") or "",
        ),
        "looks": sorted(crew_mod.LOOKS),
        "direction_still": public_still,
        # Aggregate classify→…→board for MusePanel + external Claude eval.
        # Raw turn_trace / craft_route / rewrite_log remain on **session.
        "pipeline": pipeline_view_mod.build_pipeline_view(session),
    }
    return view

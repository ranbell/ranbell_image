"""Pipeline view for session debug — aggregate existing writers, no second log.

Attached to ``public_view`` so GET /api/muse/sessions/{id} carries a stable
``pipeline`` summary Claude and MusePanel both read.
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import notebook as notebook_mod

PIPELINE_SCHEMA = "muse.pipeline.v1"

_STAGE_IDS = (
    "classify",
    "clerks",
    "notebook",
    "weave",
    "scrub",
    "boxes",
    "prompt",
    "board",
)


def _tokens(text: str) -> set[str]:
    return {
        t.replace("-", "_")
        for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or "").lower())
    }


def _hop_matching(route: list[dict[str, Any]], *needles: str) -> dict[str, Any] | None:
    for row in reversed(route or []):
        hop = str(row.get("hop") or "")
        low = hop.lower()
        if any(n.lower() in low for n in needles):
            return row
    return None


def _divergences(session: dict[str, Any]) -> list[dict[str, str]]:
    """Notebook SHOT phrases that never reached craft.prompt / board.prompt."""
    nb = notebook_mod.of(session)
    craft = session.get("craft") or {}
    board = session.get("board") or {}
    prompt = str(craft.get("prompt") or "")
    board_prompt = str(board.get("prompt") or "")
    prompt_tok = _tokens(prompt)
    board_tok = _tokens(board_prompt) if board_prompt else set()
    out: list[dict[str, str]] = []
    for key in (
        "atmosphere", "scene", "bg", "light", "wearing", "beat", "expression",
        "wearing_b", "beat_b", "expression_b",
    ):
        raw = str(nb.get(key) or "").strip()
        if not raw:
            continue
        # Use distinctive tokens (len>=4) from the field.
        field_tok = {t for t in _tokens(raw) if len(t) >= 4}
        if not field_tok:
            continue
        if prompt and not (field_tok & prompt_tok):
            out.append({
                "kind": "prompt_vs_notebook",
                "field": key,
                "detail": "in notebook, missing in craft.prompt",
            })
        if board_prompt and not (field_tok & board_tok):
            out.append({
                "kind": "board_vs_notebook",
                "field": key,
                "detail": "in notebook, missing in board.prompt",
            })
    return out[:24]


def build_pipeline_view(session: dict[str, Any]) -> dict[str, Any]:
    """Aggregate classify → clerks → notebook → weave → … → board."""
    route = list(session.get("craft_route") or [])
    trace = list(session.get("turn_trace") or [])
    last_trace = trace[-1] if trace else {}
    asked = list(session.get("asked_fields") or last_trace.get("asked") or [])
    missed = list(last_trace.get("missed") or [])
    moved = dict(last_trace.get("moved") or {})
    intent = str(session.get("scripter_intent") or "") or None

    rewrite = list(session.get("rewrite_log") or [])
    clerk_sources = [
        str(e.get("source") or "")
        for e in rewrite
        if "係" in str(e.get("source") or "")
    ]
    wrote_fields: list[str] = []
    for e in rewrite:
        if "係" not in str(e.get("source") or ""):
            continue
        changed = e.get("changed") or {}
        wrote_fields.extend(str(k) for k in changed)

    nb = notebook_mod.of(session)
    craft = session.get("craft") or {}
    board = session.get("board") or {}
    prompt = str(craft.get("prompt") or "").strip()
    people = list(craft.get("people") or [])

    weave_hop = _hop_matching(route, "1 weave", "weave（生）", "weave")
    scrub_hop = _hop_matching(route, "2 scrub", "scrub_craft")
    box_hop = _hop_matching(route, "9 人ごと", "人ごとの箱", "boxes")

    nb_rev = int(nb.get("rev") or 0)
    board_rev = int(board.get("rev") or -1) if board else -1
    rev_match = bool(board) and board_rev == nb_rev and bool(board.get("prompt"))

    stages: list[dict[str, Any]] = [
        {
            "id": "classify",
            "status": "ok" if (intent or asked) else "empty",
            "intent": intent or "",
            "asked": asked,
        },
        {
            "id": "clerks",
            "status": "ok" if wrote_fields else ("empty" if not asked else "missed"),
            "wrote": sorted(set(wrote_fields)),
            "missed": missed,
            "sources": clerk_sources[-8:],
        },
        {
            "id": "notebook",
            "status": "ok" if nb_rev > 0 else "empty",
            "rev": nb_rev,
            "moved": sorted(moved),
        },
        {
            "id": "weave",
            "status": (
                "refused"
                if (last_trace.get("picture") or {}).get("weave_refused")
                else ("ok" if weave_hop else "empty")
            ),
            "hop": (weave_hop or {}).get("hop") or "",
        },
        {
            "id": "scrub",
            "status": "ok" if scrub_hop else "empty",
            "dropped": list((scrub_hop or {}).get("dropped") or []),
            "added": list((scrub_hop or {}).get("added") or []),
        },
        {
            "id": "boxes",
            "status": "ok" if people else ("ok" if box_hop else "empty"),
            "people": len(people),
            "hop": (box_hop or {}).get("hop") or "",
        },
        {
            "id": "prompt",
            "status": "ok" if prompt else "empty",
            "chars": len(prompt),
        },
        {
            "id": "board",
            "status": "frozen" if rev_match else ("stale" if board else "empty"),
            "rev_match": rev_match,
            "board_rev": board_rev if board else None,
            "notebook_rev": nb_rev,
        },
    ]

    # Keep stage order stable even if we filter later.
    by_id = {s["id"]: s for s in stages}
    ordered = [by_id[i] for i in _STAGE_IDS if i in by_id]

    return {
        "schema": PIPELINE_SCHEMA,
        "at": time.time(),
        "stages": ordered,
        "divergences": _divergences(session),
    }

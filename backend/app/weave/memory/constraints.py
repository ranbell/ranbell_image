"""Active look-dev / compile constraints on a Weave session."""
from __future__ import annotations

import time
from typing import Any


def add_constraint(
    session: dict[str, Any],
    *,
    id: str,
    text: str,
    scope: str,
    source: str = "user_comment",
    active: bool = True,
) -> dict[str, Any]:
    row = {
        "id": id,
        "source": source,
        "scope": scope,
        "text": text,
        "active": active,
        "at": time.time(),
    }
    session.setdefault("constraints", []).append(row)
    return row


def deactivate_constraints(
    session: dict[str, Any],
    *,
    text: str | None = None,
    scope: str | None = None,
) -> int:
    n = 0
    for c in session.get("constraints") or []:
        if text is not None and c.get("text") != text:
            continue
        if scope is not None and c.get("scope") != scope:
            continue
        if c.get("active"):
            c["active"] = False
            n += 1
    return n


def active_constraint_texts(session: dict[str, Any], panel_key: str | None = None) -> list[str]:
    out: list[str] = []
    for c in session.get("constraints") or []:
        if not c.get("active"):
            continue
        scope = c.get("scope")
        if panel_key and scope not in (panel_key, "session", None, ""):
            continue
        out.append(str(c.get("text") or ""))
    return out

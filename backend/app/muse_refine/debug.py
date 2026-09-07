"""Observability for Muse Refine — read-only logs, never used for decisions."""
from __future__ import annotations

import time
from typing import Any

from ..muse import events

REFINE_LOG_MAX = 80
STAGE_LOG_MAX = 40
TURN_TRACE_MAX = 40
REWRITE_LOG_MAX = 80


def note(
    session: dict[str, Any],
    kind: str,
    detail: str = "",
    **extra: Any,
) -> None:
    """Append one observable event. Judgment must not read this."""
    row = {
        "at": time.time(),
        "kind": kind,
        "detail": detail,
        **{k: v for k, v in extra.items() if v is not None},
    }
    log = list(session.get("refine_log") or [])
    log.append(row)
    session["refine_log"] = log[-REFINE_LOG_MAX:]


def stage(session: dict[str, Any], name: str, started: float) -> None:
    ms = int((time.monotonic() - started) * 1000)
    log = list(session.get("stage_ms") or [])
    log.append({"at": time.time(), "stage": name, "ms": ms})
    session["stage_ms"] = log[-STAGE_LOG_MAX:]


def turn_trace(
    session: dict[str, Any],
    *,
    line: str,
    patch: dict[str, str] | None = None,
    propose: dict[str, str] | None = None,
    before: dict[str, str] | None = None,
    after: dict[str, str] | None = None,
    wd14: list[str] | None = None,
    picked_wd14: list[str] | None = None,
    quality: list[str] | None = None,
) -> None:
    moved: dict[str, str] = {}
    if before is not None and after is not None:
        keys = set(before) | set(after)
        for k in sorted(keys):
            b = str((before or {}).get(k) or "")
            a = str((after or {}).get(k) or "")
            if b != a:
                moved[k] = f"{b!r} → {a!r}"
    row = {
        "at": time.time(),
        "line": (line or "")[:200],
        "patch": patch or {},
        "propose": propose or {},
        "moved": moved,
        "wd14_suggestions": list(wd14 or [])[:40],
        "picked_wd14": list(picked_wd14 or [])[:40],
        "quality_tags": list(quality or [])[:40],
    }
    log = list(session.get("turn_trace") or [])
    log.append(row)
    session["turn_trace"] = log[-TURN_TRACE_MAX:]


def record_rewrite(
    session: dict[str, Any],
    source: str,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    intent: str = "",
    why: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Append a Muse-shaped rewrite_log entry and publish SSE for the debug pane."""
    changed: dict[str, dict[str, str]] = {}
    keys = set(before or {}) | set(after or {})
    for key in sorted(keys):
        b = str((before or {}).get(key) or "")
        a = str((after or {}).get(key) or "")
        if b == a:
            continue
        pair: dict[str, str] = {"before": b, "after": a}
        reason = str((why or {}).get(key) or "").strip()
        if reason:
            pair["why"] = reason
        changed[str(key)] = pair
    if not changed:
        return None
    entry = {
        "at": time.time(),
        "source": str(source or ""),
        "intent": str(intent or ""),
        "changed": changed,
    }
    log = list(session.get("rewrite_log") or [])
    log.append(entry)
    session["rewrite_log"] = log[-REWRITE_LOG_MAX:]
    sid = str(session.get("session_id") or "")
    if sid:
        # Muse-compatible event name for external debug clients / panel habits.
        events.publish(sid, {"type": "notebook_rewrite", **entry})
    return entry

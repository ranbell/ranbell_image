"""Observability for Muse Refine — read-only logs, never used for decisions."""
from __future__ import annotations

import time
from typing import Any

REFINE_LOG_MAX = 80
STAGE_LOG_MAX = 40
TURN_TRACE_MAX = 40


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

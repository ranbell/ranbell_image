"""Preference log helpers (positive / negative sample ratings)."""
from __future__ import annotations

import time
from typing import Any


def log_rating(
    session: dict[str, Any],
    *,
    panel_key: str,
    chips: list[str],
) -> dict[str, Any]:
    row = {
        "at": time.time(),
        "panel_key": panel_key,
        "chips": list(chips),
        "positive": "good" in chips or "良い" in chips,
    }
    session.setdefault("preference_log", []).append(row)
    return row


def recent_positives(session: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    rows = [
        r for r in (session.get("preference_log") or [])
        if r.get("positive")
    ]
    return rows[-limit:]

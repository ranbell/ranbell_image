"""Board slot resolution (portrait/full/prop + optional mood)."""
from __future__ import annotations

from typing import Any

from ..schema import DEFAULT_BOARD_SLOTS

_SLOT_META: dict[str, dict[str, str]] = {
    "portrait": {"camera": "close_up", "purpose": "face_lock"},
    "full": {"camera": "long_shot", "purpose": "silhouette_outfit"},
    "prop": {"camera": "medium_shot", "purpose": "signature_prop"},
    "mood": {"camera": "medium_shot", "purpose": "atmosphere"},
}


def resolve_board_slots(session: dict[str, Any]) -> list[str]:
    policy = session.get("quality_policy") or {}
    raw = policy.get("board_slots")
    if isinstance(raw, list) and raw:
        slots = [str(s).strip() for s in raw if str(s).strip()]
        # Keep known order, drop unknowns except mood
        allowed = set(DEFAULT_BOARD_SLOTS) | {"mood"}
        out = [s for s in slots if s in allowed]
        return out or list(DEFAULT_BOARD_SLOTS)
    return list(DEFAULT_BOARD_SLOTS)


def set_mood_slot(session: dict[str, Any], enabled: bool) -> list[str]:
    """Toggle mood in board_slots; sync briefs."""
    slots = resolve_board_slots(session)
    if enabled and "mood" not in slots:
        slots = list(slots) + ["mood"]
    if not enabled:
        slots = [s for s in slots if s != "mood"]
    session.setdefault("quality_policy", {})["board_slots"] = slots
    return sync_board_briefs(session)


def sync_board_briefs(session: dict[str, Any]) -> list[str]:
    """Rebuild character.board_briefs from quality_policy.board_slots."""
    slots = resolve_board_slots(session)
    briefs = []
    for slot in slots:
        meta = _SLOT_META.get(slot, {"camera": "medium_shot", "purpose": slot})
        briefs.append({
            "slot": slot,
            "camera": meta["camera"],
            "purpose": meta["purpose"],
        })
    session.setdefault("character", {})["board_briefs"] = briefs
    session.setdefault("quality_policy", {})["board_slots"] = slots
    return slots

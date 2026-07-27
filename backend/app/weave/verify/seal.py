"""Seal rubric (G6)."""
from __future__ import annotations

from typing import Any


def _finals_ready(panels: list[dict[str, Any]]) -> bool:
    ids = []
    for p in panels:
        iid = str((p.get("final") or {}).get("image_id") or "")
        if not iid or iid.startswith(("pending:", "placeholder:")):
            return False
        ids.append(iid)
    return len(ids) >= 3


def evaluate_seal_rubric(session: dict[str, Any]) -> dict[str, Any]:
    """Rubric: same person / same story / framing / causality / finals×3."""
    character = session.get("character") or {}
    board = character.get("board") or {}
    images = board.get("images") or []
    slots = {img.get("slot") for img in images if img.get("image_id")}
    world = (session.get("story_bundle") or {}).get("world") or {}
    lint = session.get("last_lint") or {}
    panels = session.get("panels") or []
    overrides = {
        o.get("panel_key") for o in (session.get("framing_overrides") or [])
    }

    framing_ok = True
    for p in panels:
        cam = ((p.get("intent") or {}).get("camera") or "")
        if cam != "long_shot":
            continue
        fr = (p.get("qa") or {}).get("framing")
        if p.get("key") in overrides:
            continue
        # Only explicit pass counts — unknown/None/fail block seal framing.
        if fr != "pass":
            framing_ok = False

    samples = [bool((p.get("sample") or {}).get("image_id")) for p in panels]
    cams = [str((p.get("intent") or {}).get("camera") or "") for p in panels]
    filled = [c for c in cams if c]
    unique_cams = len(filled) >= 3 and len(set(filled)) == len(filled)
    has_finals = _finals_ready(panels)

    checks = {
        "identity_locked": bool(character.get("identity_locked")),
        "board_accepted": bool(board.get("accepted")) and (
            "portrait" in slots and "full" in slots
        ),
        "story_lint": bool(lint.get("pass")),
        "framing_ok": framing_ok,
        "causality": bool(str(world.get("causality_one_liner") or "").strip()),
        "camera_unique": unique_cams,
        "has_finals": has_finals,
        "has_samples": sum(1 for s in samples if s) >= 1,
    }
    # Finals are always required to seal (product exit).
    core_ok = all([
        checks["identity_locked"],
        checks["board_accepted"],
        checks["story_lint"],
        checks["framing_ok"],
        checks["causality"],
        checks["has_finals"],
    ])
    full_ok = core_ok and checks["camera_unique"] and checks["has_samples"]
    policy = session.get("quality_policy") or {}
    strict = bool(policy.get("strict_seal"))
    return {
        "pass": full_ok if strict else core_ok,
        "full_pass": full_ok,
        "core_pass": core_ok,
        "strict": strict,
        "checks": checks,
    }

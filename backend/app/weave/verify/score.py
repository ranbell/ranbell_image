"""WeaveScore — rule-based quality dimensions (from chronicle quality.py).

Stores a compact scorecard on ``panel.qa.weave_score`` and session
``cross_panel_qa.weave_score``. No LLM required.
"""
from __future__ import annotations

import time
from typing import Any

from ...story.quality import evaluate_chronicle_quality


def _panel_prompts(session: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in session.get("panels") or []:
        key = str(p.get("key") or "")
        if not key:
            continue
        compiled = p.get("compile") or {}
        pos = str(compiled.get("positive") or "").strip()
        out[key] = pos
    return out


def _panel_stories(session: dict[str, Any]) -> dict[str, str]:
    return {
        str(p.get("key") or ""): str((p.get("intent") or {}).get("narrative_ja") or "")
        for p in (session.get("panels") or [])
        if p.get("key")
    }


def _panel_activities(session: dict[str, Any]) -> dict[str, str]:
    return {
        str(p.get("key") or ""): str((p.get("intent") or {}).get("visible_change") or "")
        for p in (session.get("panels") or [])
        if p.get("key")
    }


def compute_weave_score(session: dict[str, Any]) -> dict[str, Any]:
    """Session-level WeaveScore (0..1 dimensions + overall)."""
    inputs = session.get("inputs") or {}
    bundle = session.get("story_bundle") or {}
    world = bundle.get("world") or {}
    character = session.get("character") or {}
    prompts = _panel_prompts(session)
    stories = _panel_stories(session)
    activities = _panel_activities(session)
    scored = [k for k, v in prompts.items() if v.strip()] or list(stories.keys())

    raw = evaluate_chronicle_quality(
        user_topic=str(inputs.get("topic") or ""),
        title=str(bundle.get("title") or ""),
        overall=str(world.get("causality_one_liner") or ""),
        stories=stories,
        activities=activities,
        prompts=prompts,
        time_scale=str(world.get("time_scale") or "hours"),
        lock_tags=list(character.get("identity_tags") or []),
        method="weave_rules",
        scored_axes=scored,
    )
    return {
        "version": 1,
        "evaluated_at": time.time(),
        "method": "rules",
        "ok": bool(raw.get("ok", True)),
        "overall": raw.get("overall"),
        "dimensions": raw.get("dimensions") or {},
        "notes": raw.get("notes") or {},
        "scored_axes": list(raw.get("scored_axes") or scored),
    }


def score_panel_slice(
    session_score: dict[str, Any],
    panel: dict[str, Any],
) -> dict[str, Any]:
    """Per-panel view of WeaveScore (overall + local framing/vlm hints)."""
    key = str(panel.get("key") or "")
    dims = dict(session_score.get("dimensions") or {})
    qa = panel.get("qa") or {}
    framing = qa.get("framing")
    vlm = qa.get("vlm") or {}
    answers = (vlm.get("answers") or {}) if isinstance(vlm, dict) else {}
    # Soft local adjustments (do not mutate session overall).
    local = float(session_score.get("overall") or 0.5)
    if framing == "fail":
        local = min(local, 0.35)
    elif framing == "pass":
        local = min(1.0, local + 0.05)
    fails = sum(1 for v in answers.values() if v is False)
    if fails:
        local = max(0.0, local - 0.08 * fails)
    return {
        "overall": round(local, 3),
        "session_overall": session_score.get("overall"),
        "dimensions": dims,
        "panel_key": key,
        "framing": framing,
        "vlm_fails": fails,
        "method": "rules",
        "evaluated_at": session_score.get("evaluated_at") or time.time(),
    }


def apply_weave_scores(session: dict[str, Any]) -> dict[str, Any]:
    """Compute and attach WeaveScore to session + each panel.qa."""
    score = compute_weave_score(session)
    session.setdefault("cross_panel_qa", {})["weave_score"] = score
    for panel in session.get("panels") or []:
        panel.setdefault("qa", {})["weave_score"] = score_panel_slice(score, panel)
    return score

"""Cross-panel QA aggregates for Weave sessions."""
from __future__ import annotations

from typing import Any


def _camera_diversity(panels: list[dict[str, Any]]) -> float | None:
    cams = [
        str((p.get("intent") or {}).get("camera") or "").strip()
        for p in panels
    ]
    filled = [c for c in cams if c]
    if len(filled) < 2:
        return None
    return round(len(set(filled)) / len(filled), 3)


def _identity_drift_risk(panels: list[dict[str, Any]]) -> float | None:
    """0 = safe, 1 = high risk — from VLM same_person fails."""
    scores: list[float] = []
    for p in panels:
        vlm = (p.get("qa") or {}).get("vlm") or {}
        answers = vlm.get("answers") or {}
        if "same_person" not in answers:
            continue
        sp = answers.get("same_person")
        if sp is None:
            continue
        scores.append(0.0 if sp else 1.0)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 3)


def _motif_repetition(session: dict[str, Any]) -> float | None:
    """Crude repetition from visible_change / gesture overlap (0..1)."""
    texts: list[str] = []
    for p in session.get("panels") or []:
        intent = p.get("intent") or {}
        blob = f"{intent.get('visible_change') or ''} {intent.get('gesture') or ''}"
        texts.append(blob.lower().strip())
    filled = [t for t in texts if t]
    if len(filled) < 2:
        return None
    # token Jaccard mean of pairs
    toks = [set(t.replace(",", " ").split()) for t in filled]
    pairs = 0
    total = 0.0
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            a, b = toks[i], toks[j]
            if not a or not b:
                continue
            inter = len(a & b)
            union = len(a | b) or 1
            total += inter / union
            pairs += 1
    if not pairs:
        return None
    return round(total / pairs, 3)


def refresh_cross_panel_qa(session: dict[str, Any]) -> dict[str, Any]:
    """Update cross_panel_qa metrics.

    ``ready_for_final`` / ``lookdev_ready`` = samples + framing (G5).
    ``finals_ready`` is owned by attach/seal path and left untouched here.
    """
    panels = list(session.get("panels") or [])
    qa = session.setdefault("cross_panel_qa", {})
    world = (session.get("story_bundle") or {}).get("world") or {}
    if world.get("causality_one_liner"):
        qa["causality_one_liner"] = str(world["causality_one_liner"])

    lint = session.get("last_lint") or {}
    if lint.get("throughline_coverage") is not None:
        qa["throughline_coverage"] = lint["throughline_coverage"]

    qa["camera_diversity"] = _camera_diversity(panels)
    qa["identity_drift_risk"] = _identity_drift_risk(panels)
    qa["motif_repetition"] = _motif_repetition(session)

    samples = sum(
        1 for p in panels
        if (p.get("sample") or {}).get("image_id")
    )
    framing_ok = True
    for p in panels:
        cam = ((p.get("intent") or {}).get("camera") or "")
        if cam != "long_shot":
            continue
        fr = (p.get("qa") or {}).get("framing")
        overridden = any(
            o.get("panel_key") == p.get("key")
            for o in (session.get("framing_overrides") or [])
        )
        if overridden:
            continue
        if fr != "pass":
            framing_ok = False
    policy = session.get("quality_policy") or {}
    min_samples = int(policy.get("min_sample_panels") or 1)
    lookdev_ready = samples >= min_samples and framing_ok
    qa["lookdev_ready"] = lookdev_ready
    # Always sync — do not sticky-True when lookdev regresses.
    qa["ready_for_final"] = lookdev_ready
    return qa

"""Typo-only narrative PATCH (beat/camera/must_show/world forbidden)."""
from __future__ import annotations

import difflib
from typing import Any


ALLOWED_FIELDS = frozenset({"narrative_ja", "narrative_en"})


class NarrativePatchError(ValueError):
    pass


def _similar(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def apply_narrative_typo_patch(
    session: dict[str, Any],
    *,
    panel_key: str,
    narrative_ja: str | None = None,
    narrative_en: str | None = None,
    min_similarity: float = 0.55,
) -> dict[str, Any]:
    """Patch narrative text only. Rejects large rewrites (force Recreate)."""
    if narrative_ja is None and narrative_en is None:
        raise NarrativePatchError("narrative_ja or narrative_en required")

    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if not panel:
        raise NarrativePatchError(f"unknown panel {panel_key}")
    intent = panel.setdefault("intent", {})

    # Also sync story_bundle panel if present
    bundle = session.get("story_bundle") or {}
    bpanel = next(
        (p for p in (bundle.get("panels") or []) if isinstance(p, dict) and p.get("key") == panel_key),
        None,
    )

    changes: dict[str, str] = {}
    if narrative_ja is not None:
        old = str(intent.get("narrative_ja") or "")
        new = str(narrative_ja)
        if _similar(old, new) < min_similarity and old.strip():
            raise NarrativePatchError(
                "narrative_ja change too large — use Recreate instead of free edit"
            )
        intent["narrative_ja"] = new
        changes["narrative_ja"] = new
        if bpanel is not None:
            bpanel["narrative_ja"] = new
    if narrative_en is not None:
        old = str(intent.get("narrative_en") or "")
        new = str(narrative_en)
        if _similar(old, new) < min_similarity and old.strip():
            raise NarrativePatchError(
                "narrative_en change too large — use Recreate instead of free edit"
            )
        intent["narrative_en"] = new
        changes["narrative_en"] = new
        if bpanel is not None:
            bpanel["narrative_en"] = new

    return {"panel_key": panel_key, "changed": changes}

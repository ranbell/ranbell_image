"""Notebook field contracts + wearing/tag consistency helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook


def test_scene_cap_rejects_densify_prose():
    nb = notebook.blank()
    long_park = (
        "A sun-drenched public park with winding gravel paths, green benches "
        "under maple trees, soft afternoon light filtering through leaves, "
        "distant children's laughter, and a quiet fountain near the lawn."
    )
    notebook.apply_patch(nb, {"scene": long_park, "wearing": "sailor uniform"})
    assert len(nb["scene"]) <= notebook.SCENE_MAX_CHARS
    assert "park" in nb["scene"].lower()
    assert nb["wearing"] == "sailor uniform"


def test_stale_wearing_tags_detects_lingering_hat():
    stale = notebook.stale_wearing_tags(
        prev_wearing="sailor uniform, straw hat",
        new_wearing="sailor uniform",
        tags="rooftop, sailor_collar, straw_hat, leaning",
    )
    assert "straw_hat" in stale or "straw" in stale


def test_strip_shot_keys_keeps_meta_only():
    patch = {
        "scene": "beach",
        "wearing": "yukata",
        "vibe": "warm",
        "open": "fan?",
    }
    out = notebook.strip_shot_keys(patch)
    assert "scene" not in out and "wearing" not in out
    assert out.get("vibe") == "warm"
    assert out.get("open") == "fan?"

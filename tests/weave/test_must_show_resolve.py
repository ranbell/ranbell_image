from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.validate.must_show_resolve import resolve_must_show, apply_must_show_resolution


def test_resolve_throughline_keys():
    resolved, unresolved = resolve_must_show(
        ["throughline_prop", "throughline_place"],
        world={
            "throughline_prop": "cloth_bookmark",
            "throughline_place": "bookstore counter",
        },
        character={"signature_prop": "cloth_bookmark"},
    )
    assert unresolved == []
    assert "cloth_bookmark" in resolved
    assert any("bookstore" in t or "counter" in t for t in resolved)


def test_unresolved_when_empty_world():
    resolved, unresolved = resolve_must_show(
        ["throughline_prop"],
        world={},
        character={},
    )
    assert resolved == []
    assert "throughline_prop" in unresolved


def test_apply_on_bundle():
    bundle = {
        "world": {
            "throughline_prop": "cloth_bookmark",
            "throughline_place": "書店のレジ",
        },
        "panels": [
            {"key": "panel_1", "must_show": ["throughline_prop", "throughline_place"]},
        ],
    }
    bad = apply_must_show_resolution(bundle, {"signature_prop": "cloth_bookmark"})
    assert bad == []
    assert bundle["panels"][0]["must_show_resolved"]

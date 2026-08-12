"""Pure unit tests for the shot notebook helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook


def test_promote_open_clears_proposal():
    nb = notebook.blank()
    nb["open"] = "落ち葉を一枚だけ手に"
    assert notebook.promote_open_to_wearing(nb)
    assert "落ち葉" in nb["wearing"]
    assert nb["open"] == ""


def test_migrate_seeds_from_digest():
    session = {
        "inputs": {},
        "digest": "rooftop at dusk with a sailor uniform",
        "craft": {"scene": "", "tags": "", "prompt": ""},
        "facets": {},
        "standing": ["no feet"],
    }
    notebook.migrate(session)
    assert session["notebook"]["scene"]
    assert "no feet" in session["notebook"]["standing"]


def test_summary_for_muse_is_short():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "atmosphere": "切ない夕暮れ",
        "wearing": "薄いカーディガン",
        "beat": "ベンチに座る",
        "open": "落ち葉",
    })
    text = notebook.summary_for_muse(nb, name_a="あさひ")
    assert "カーディガン" in text
    assert "提案中" in text

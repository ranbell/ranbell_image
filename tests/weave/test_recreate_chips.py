from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.story.recreate import chips_to_constraints


def test_chips_become_imperatives():
    out = chips_to_constraints(["weak_plot", "place_scatters"])
    assert len(out) == 2
    assert out[0].startswith("Put one visible")
    assert "throughline_place" in out[1]


def test_japanese_aliases():
    out = chips_to_constraints(["ありきたり", "展開が弱い"])
    assert len(out) == 2
    assert any("Avoid" in s or "avoid" in s.lower() or "stock" in s.lower() or "motifs" in s for s in out)

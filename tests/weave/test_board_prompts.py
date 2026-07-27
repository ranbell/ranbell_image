from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.render.prompts import compile_board_slot
from app.weave.schema import new_session_payload


def test_board_portrait_close_full_long():
    session = new_session_payload()
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    # Board shows her in her own clothes; the story's per-topic wardrobe does not
    # reach the reference sheet.
    session["character"]["outfit_tags"] = ["cardigan"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    portrait = compile_board_slot(session, "portrait")
    full = compile_board_slot(session, "full")
    prop = compile_board_slot(session, "prop")
    assert "brown_hair" in portrait["positive"]
    assert "cardigan" in portrait["positive"]
    assert "close-up" in portrait["positive"] or "close_up" in portrait["positive"]
    assert "long_shot" in full["positive"]
    assert "full_body" in full["positive"]
    assert "cloth_bookmark" in prop["positive"]
    # prop should not dominate the full-body identity lock frame
    assert "holding" not in full["positive"]

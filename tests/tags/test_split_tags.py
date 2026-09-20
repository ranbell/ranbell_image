"""identity / prop separation"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.tags.split_tags import enforce_identity_prop_split


def test_prop_removed_from_identity():
    ident, props, sig = enforce_identity_prop_split(
        ["1girl", "brown_hair", "cardigan", "cloth_bookmark"],
        [],
        signature_prop="cloth_bookmark",
    )
    assert "cloth_bookmark" not in ident
    assert "cloth_bookmark" in props
    assert sig == "cloth_bookmark"
    assert "brown_hair" in ident
    assert "cardigan" in ident


def test_prop_tags_merged():
    ident, props, sig = enforce_identity_prop_split(
        ["1girl", "black_hair"],
        ["umbrella"],
        signature_prop="",
    )
    assert props[0] == "umbrella"
    assert sig == "umbrella"
    assert "umbrella" not in ident

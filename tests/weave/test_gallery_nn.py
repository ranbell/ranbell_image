"""Unit tests for optional Qdrant gallery NN merge (no live Qdrant)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.character.gallery_nn import (
    is_gallery_nn_enabled,
    merge_gallery_tags,
    set_gallery_nn_enabled,
)
from app.weave.compile.layers import compile_panel
from app.weave.schema import new_session_payload


def test_gallery_nn_default_off():
    s = new_session_payload(personality_text="x")
    assert s["quality_policy"]["gallery_nn"] is False
    assert is_gallery_nn_enabled(s) is False
    set_gallery_nn_enabled(s, True)
    assert is_gallery_nn_enabled(s) is True


def test_merge_adds_voted_identity_and_spice():
    neighbors = [
        {
            "sha256": "a" * 64,
            "name": "a.png",
            "_score": 0.9,
            "wd14_tags": [
                "1girl", "brown_hair", "green_eyes", "cardigan",
                "bookstore", "bookshelf", "indoors", "bookmark",
            ],
        },
        {
            "sha256": "b" * 64,
            "name": "b.png",
            "_score": 0.8,
            "wd14_tags": [
                "1girl", "brown_hair", "green_eyes", "cardigan",
                "bookstore", "rain", "indoors",
            ],
        },
        {
            "sha256": "c" * 64,
            "name": "c.png",
            "_score": 0.7,
            "wd14_tags": ["1girl", "blonde_hair", "bookstore", "indoors"],
        },
    ]
    merged = merge_gallery_tags(
        ["1girl", "long_hair"],
        [],
        signature_prop="cloth_bookmark",
        neighbor_docs=neighbors,
    )
    # Hair/eyes missing → single-vote fill allowed for brown_hair / green_eyes
    assert "brown_hair" in merged["identity_tags"]
    assert "green_eyes" in merged["identity_tags"]
    # Clothing from a neighbour must NOT become identity: the story dresses her
    # per topic, and a locked cardigan would follow her to the beach.
    assert "cardigan" not in merged["identity_tags"]
    assert "cardigan" not in merged["gallery_spice"]
    # Atmosphere spice (not identity)
    assert "bookstore" in merged["gallery_spice"] or "indoors" in merged["gallery_spice"]
    # Prop vote only thickens when signature/prop exists — bookmark may land in props
    assert merged["signature_prop"] == "cloth_bookmark"
    assert len(merged["gallery_refs"]) >= 2


def test_compile_includes_gallery_spice():
    s = new_session_payload()
    s["character"]["identity_tags"] = ["1girl", "brown_hair"]
    s["character"]["prop_tags"] = ["cloth_bookmark"]
    s["character"]["signature_prop"] = "cloth_bookmark"
    s["character"]["gallery_spice"] = ["bookstore", "overcast"]
    s["story_bundle"] = {
        "world": {
            "setting": "rainy bookstore",
            "throughline_prop": "cloth_bookmark",
            "throughline_place": "bookstore",
        },
        "panels": [],
    }
    s["panels"][0]["intent"].update({
        "camera": "medium_shot",
        "gesture": "holding bookmark",
        "emotion": "serious",
        "must_show_resolved": ["cloth_bookmark"],
    })
    compiled = compile_panel(s, "panel_1")
    assert "bookstore" in compiled["layers"]["spice"]
    assert "bookstore" in compiled["positive"]
    assert "brown_hair" in compiled["positive"]

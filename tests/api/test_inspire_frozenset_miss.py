"""What happens when a tag falls outside frozenset classification.

Covers:
  - _get_tag_axis → None (true miss)
  - suffix fallbacks still catch *_hair / *_eyes / clothing
  - expression tags IN frozenset stay emotion (not frozen)
  - _apply_frozenset_corrections keeps LLM choice for misses
  - _group_volatile_by_axis puts misses in other (or LLM override)
  - _step1 Phase B: LLM classify / omit→always_fixed / exception→always_fixed
  - frozenset_enabled=False sends everything to Phase B
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.api.inspire import (
    _apply_frozenset_corrections,
    _get_tag_axis,
    _group_volatile_by_axis,
    _step1_dynamic_separator,
)


# Tags deliberately absent from tag_categories.json frozensets
_MISS_TAGS = (
    "neon_lantern_glow",
    "lantern_street_blur",
    "shared_candy_apple",
    "festival_yukata_trio_pose",
    "xyzzy_totally_unknown",
)


def test_frozenset_miss_returns_none():
    for tag in _MISS_TAGS:
        assert _get_tag_axis(tag) is None, f"{tag} should be outside frozenset"


def test_suffix_fallback_still_catches_unknown_colour_eyes_and_hair():
    # Not listed in emotion frozenset → endswith(_eyes) → always_fixed
    assert _get_tag_axis("chartreuse_eyes") == "always_fixed"
    assert _get_tag_axis("magenta_eyes") == "always_fixed"
    # Expression states that ARE in frozenset stay emotion
    assert _get_tag_axis("teary_eyes") == "emotion"
    assert _get_tag_axis("closed_eyes") == "emotion"
    # Unknown *_hair still → hair axis
    assert _get_tag_axis("galaxy_streak_hair") == "hair"


def test_known_expression_not_frozen_as_always_fixed():
    for tag in ("smile", "blush", "looking_at_viewer", "teary_eyes"):
        axis = _get_tag_axis(tag)
        assert axis == "emotion", tag
        assert axis != "always_fixed"


def test_apply_corrections_keeps_llm_bucket_for_misses():
    """Outside frozenset: stay in whichever bucket the LLM chose."""
    fixed, vol = _apply_frozenset_corrections(
        fixed=["blue_eyes", "neon_lantern_glow"],  # miss was in fixed
        volatile=["smile", "lantern_street_blur"],  # miss was in volatile
        change_targets={"emotion", "action", "location"},
    )
    assert "blue_eyes" in fixed          # frozenset → always_fixed
    assert "neon_lantern_glow" in fixed  # miss kept fixed
    assert "smile" in vol                # emotion + in change_targets
    assert "lantern_street_blur" in vol  # miss kept volatile


def test_apply_corrections_moves_known_axis_out_of_wrong_bucket():
    # LLM put smile in fixed, but frozenset says emotion and emotion is targeted
    fixed, vol = _apply_frozenset_corrections(
        fixed=["smile", "1girl"],
        volatile=[],
        change_targets={"emotion"},
    )
    assert "smile" in vol
    assert "1girl" in fixed  # count → always_fixed


def test_group_volatile_miss_goes_to_other_without_override():
    groups = _group_volatile_by_axis(
        ["smile", "neon_lantern_glow", "running"],
        ["emotion", "action", "location"],
    )
    assert "smile" in groups["emotion"]
    assert "running" in groups["action"]
    assert "neon_lantern_glow" in groups["other"]


def test_group_volatile_miss_uses_llm_override():
    groups = _group_volatile_by_axis(
        ["neon_lantern_glow", "shared_candy_apple"],
        ["emotion", "action", "location", "time_weather"],
        axis_override={
            "neon_lantern_glow": "location",
            "shared_candy_apple": "action",
        },
    )
    assert "neon_lantern_glow" in groups["location"]
    assert "shared_candy_apple" in groups["action"]
    assert not groups.get("other")


def test_step1_miss_llm_classifies_into_axis():
    ollama = MagicMock()
    ollama.generate_text = AsyncMock(
        return_value='{"neon_lantern_glow": "location", "xyzzy_totally_unknown": "action"}'
    )

    async def _run():
        return await _step1_dynamic_separator(
            base_tags=["smile", "blue_eyes", "neon_lantern_glow", "xyzzy_totally_unknown"],
            ollama=ollama,
            model="stub",
            frozenset_enabled=True,
        )

    always_fixed, by_axis, llm = asyncio.run(_run())
    assert "blue_eyes" in always_fixed
    assert "smile" in by_axis.get("emotion", [])
    assert "neon_lantern_glow" in by_axis.get("location", [])
    assert "xyzzy_totally_unknown" in by_axis.get("action", [])
    assert llm["neon_lantern_glow"] == "location"
    ollama.generate_text.assert_awaited()


def test_step1_miss_omitted_by_llm_defaults_to_always_fixed():
    ollama = MagicMock()
    ollama.generate_text = AsyncMock(
        return_value='{"neon_lantern_glow": "location"}'
    )

    async def _run():
        return await _step1_dynamic_separator(
            base_tags=["neon_lantern_glow", "lantern_street_blur"],
            ollama=ollama,
            model="stub",
            frozenset_enabled=True,
        )

    always_fixed, by_axis, llm = asyncio.run(_run())
    assert "neon_lantern_glow" in by_axis.get("location", [])
    assert "lantern_street_blur" in always_fixed  # safe fallback
    assert "lantern_street_blur" not in llm


def test_step1_miss_llm_exception_all_unknowns_always_fixed():
    ollama = MagicMock()
    ollama.generate_text = AsyncMock(side_effect=RuntimeError("ollama down"))

    async def _run():
        return await _step1_dynamic_separator(
            base_tags=["smile", "xyzzy_totally_unknown"],
            ollama=ollama,
            model="stub",
            frozenset_enabled=True,
        )

    always_fixed, by_axis, llm = asyncio.run(_run())
    assert "smile" in by_axis.get("emotion", [])  # known via frozenset
    assert "xyzzy_totally_unknown" in always_fixed
    assert llm == {}


def test_step1_frozenset_disabled_sends_all_to_llm():
    ollama = MagicMock()
    ollama.generate_text = AsyncMock(
        return_value='{"smile": "emotion", "blue_eyes": "fixed"}'
    )

    async def _run():
        return await _step1_dynamic_separator(
            base_tags=["smile", "blue_eyes"],
            ollama=ollama,
            model="stub",
            frozenset_enabled=False,
        )

    always_fixed, by_axis, llm = asyncio.run(_run())
    assert "smile" in by_axis.get("emotion", [])
    assert "blue_eyes" in always_fixed
    assert set(llm) == {"smile", "blue_eyes"}

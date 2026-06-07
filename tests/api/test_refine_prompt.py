"""Tests for the alchemy/refine prompt pipeline.

Covers:
  - _build_vlm_prompt(): style × instruction_framing combinations
  - _extract_literal_directives(): regex extraction
  - _inject_literal_directives(): prompt injection
  - _parse_detailed_output(): bold and plain-header fallback
  - _translate_instruction(): mock Ollama
  - _translate_and_classify(): mock Ollama + JSON parsing
  - Full pipeline VLM prompt verification: instruction_mode × prompt_style
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
# conftest.py in this directory installs all FastAPI/Pydantic stubs before import

from app.api.ai import (
    _STYLE_INSTRUCTIONS,
    _build_vlm_prompt,
    _extract_literal_directives,
    _inject_literal_directives,
    _parse_detailed_output,
    _remove_forced_tags,
    _translate_and_classify,
    _translate_instruction,
)


# ── _build_vlm_prompt ─────────────────────────────────────────────────────────

class TestBuildVlmPrompt:
    CONTEXT = "[Image 1 — influence weight: 100%]\nPrompt: 1girl, blue hair"

    def test_natural_no_framing_uses_raw_instruction(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "make it foggy", "natural",
            with_negative=False, instruction_framing=False,
        )
        assert "make it foggy" in prompt
        assert "PROMPT ENGINEERING DIRECTIVE" not in prompt

    def test_natural_with_framing_wraps_instruction(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "make it foggy", "natural",
            with_negative=False, instruction_framing=True,
        )
        assert "PROMPT ENGINEERING DIRECTIVE" in prompt
        assert "make it foggy" in prompt
        assert "DO NOT incorporate it as scene description" in prompt

    def test_empty_instruction_uses_default(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "natural",
            with_negative=False, instruction_framing=False,
        )
        assert "Create a refined, high-quality image generation prompt" in prompt

    def test_danbooru_style_included(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "danbooru",
            with_negative=False, instruction_framing=False,
        )
        assert _STYLE_INSTRUCTIONS["danbooru"][:40] in prompt

    def test_detailed_style_included(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "detailed",
            with_negative=False, instruction_framing=False,
        )
        assert "Core Subject" in prompt
        assert "Refinements" in prompt

    def test_detailed_style_forbids_tag_list(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "detailed",
            with_negative=False, instruction_framing=False,
        )
        assert "Do NOT output a comma-separated tag list" in prompt

    def test_with_negative_adds_sections(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "natural",
            with_negative=True, instruction_framing=False,
        )
        assert "POSITIVE:" in prompt
        assert "NEGATIVE:" in prompt

    def test_without_negative_no_negative_section(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "natural",
            with_negative=False, instruction_framing=False,
        )
        assert "NEGATIVE:" not in prompt

    def test_context_included(self):
        prompt = _build_vlm_prompt(
            self.CONTEXT, "", "natural",
            with_negative=False, instruction_framing=False,
        )
        assert "1girl, blue hair" in prompt


# ── _extract_literal_directives ───────────────────────────────────────────────

class TestExtractLiteralDirectives:
    def test_basic_text_at_top(self):
        cleaned, lits = _extract_literal_directives("add text 'Ranbell Image' at top")
        assert len(lits) == 1
        assert lits[0]["text"] == "Ranbell Image"
        assert lits[0]["position"] == "top"
        assert "Ranbell Image" not in cleaned

    def test_basic_text_at_bottom(self):
        _, lits = _extract_literal_directives('display text "Hello" at bottom')
        assert lits[0]["position"] == "bottom"

    def test_text_without_position_defaults_top(self):
        _, lits = _extract_literal_directives("add watermark 'Copyright 2025'")
        assert lits[0]["position"] == "top"

    def test_no_directive_returns_unchanged(self):
        cleaned, lits = _extract_literal_directives("make it foggy and dramatic")
        assert lits == []
        assert cleaned == "make it foggy and dramatic"

    def test_mixed_instruction_extracts_only_literal(self):
        cleaned, lits = _extract_literal_directives(
            "oil painting style, add text 'Ranbell Image' at top"
        )
        assert len(lits) == 1
        assert lits[0]["text"] == "Ranbell Image"
        assert "oil painting" in cleaned
        assert "Ranbell Image" not in cleaned

    def test_multiple_text_directives(self):
        _, lits = _extract_literal_directives(
            "insert label 'Top Label' at top, add caption 'Bottom Label' at bottom"
        )
        assert len(lits) == 2
        texts = {d["text"] for d in lits}
        assert "Top Label" in texts
        assert "Bottom Label" in texts

    def test_case_insensitive(self):
        _, lits = _extract_literal_directives("ADD TEXT 'HELLO' AT TOP")
        assert len(lits) == 1

    def test_unicode_text_content(self):
        _, lits = _extract_literal_directives("add text 'Ranbell画像' at top")
        assert lits[0]["text"] == "Ranbell画像"


# ── _inject_literal_directives ────────────────────────────────────────────────

class TestInjectLiteralDirectives:
    def test_top_text_prepended(self):
        lits = [{"text": "Ranbell Image", "position": "top"}]
        result = _inject_literal_directives("1girl, blue hair", lits)
        assert result.startswith('text "Ranbell Image", top_text, text_on_image,')
        assert "1girl" in result

    def test_bottom_text_uses_bottom_tag(self):
        lits = [{"text": "Footer", "position": "bottom"}]
        result = _inject_literal_directives("1girl", lits)
        assert "bottom_text" in result

    def test_unknown_position_uses_overlay(self):
        lits = [{"text": "X", "position": "center"}]
        result = _inject_literal_directives("1girl", lits)
        assert "overlay_text" in result

    def test_empty_literals_unchanged(self):
        result = _inject_literal_directives("1girl, blue hair", [])
        assert result == "1girl, blue hair"

    def test_multiple_literals_all_prepended(self):
        lits = [
            {"text": "Title", "position": "top"},
            {"text": "Footer", "position": "bottom"},
        ]
        result = _inject_literal_directives("1girl", lits)
        assert 'text "Title"' in result
        assert 'text "Footer"' in result


# ── _parse_detailed_output ────────────────────────────────────────────────────

DETAILED_BOLD = """\
**Core Subject & Scene Setting:** An ethereal anime girl in starry setting.
**Characters & Composition:** 1girl, solo, long blue hair, portrait composition.
**Lighting & Atmosphere:** Soft moonlight, cool blue tones, mystical ambience.
**Style & Artistic Influence:** High-detail anime illustration, digital art.
**Details & Textures:** Porcelain skin, silky hair, constellation accessories.
**Color Palette:** Deep blues, purples, silver highlights.
**Camera & Lens Effects:** Close-up, shallow depth of field, bokeh background.
**Refinements & Modifiers:** masterpiece, best_quality, ultra-detailed, 8k"""

DETAILED_PLAIN = """\
Core Subject & Scene Setting: An ethereal anime girl in starry setting.
Characters & Composition: 1girl, solo, long blue hair, portrait composition.
Lighting & Atmosphere: Soft moonlight, cool blue tones, mystical ambience.
Style & Artistic Influence: High-detail anime illustration, digital art.
Details & Textures: Porcelain skin, silky hair, constellation accessories.
Color Palette: Deep blues, purples, silver highlights.
Camera & Lens Effects: Close-up, shallow depth of field, bokeh background.
Refinements & Modifiers: masterpiece, best_quality, ultra-detailed, 8k"""

# Gemini-identified bug: sections 1-7 without colon, section 8 with colon
# The old fallback regex required ":" in each header, so only Refinements was extracted.
DETAILED_NO_COLON = """\
Core Subject & Scene Setting
An ethereal anime girl in starry setting.
Characters & Composition
1girl, solo, long blue hair, portrait composition.
Lighting & Atmosphere
Soft moonlight, cool blue tones, mystical ambience.
Style & Artistic Influence
High-detail anime illustration, digital art.
Details & Textures
Porcelain skin, silky hair, constellation accessories.
Color Palette
Deep blues, purples, silver highlights.
Camera & Lens Effects
Close-up, shallow depth of field, bokeh background.
Refinements & Modifiers: masterpiece, best_quality, ultra-detailed, 8k"""

DETAILED_ATX = """\
### Core Subject & Scene Setting
An ethereal anime girl in starry setting.
### Characters & Composition
1girl, solo, long blue hair, portrait composition.
### Refinements & Modifiers
masterpiece, best_quality, ultra-detailed, 8k"""

# VLM outputs all 8 sections in bold form AND then appends a POSITIVE: block
DETAILED_WITH_APPENDED_POSITIVE = """\
**Core Subject & Scene Setting:** An ethereal anime girl in starry setting.
**Characters & Composition:** 1girl, solo, long blue hair, portrait composition.
**Lighting & Atmosphere:** Soft moonlight, cool blue tones, mystical ambience.
**Style & Artistic Influence:** High-detail anime illustration, digital art.
**Details & Textures:** Porcelain skin, silky hair, constellation accessories.
**Color Palette:** Deep blues, purples, silver highlights.
**Camera & Lens Effects:** Close-up, shallow depth of field, bokeh background.
**Refinements & Modifiers:** masterpiece, best_quality, ultra-detailed, 8k

POSITIVE:
1girl, blue_hair, masterpiece"""

# VLM outputs bold sections AND appends both POSITIVE: and NEGATIVE: blocks
DETAILED_WITH_APPENDED_POS_NEG = """\
**Core Subject & Scene Setting:** An ethereal anime girl in starry setting.
**Refinements & Modifiers:** masterpiece, best_quality

POSITIVE:
1girl, blue_hair

NEGATIVE:
lowres, bad_anatomy"""


class TestParseDetailedOutput:
    def test_bold_headers_parsed(self):
        result = _parse_detailed_output(DETAILED_BOLD)
        assert "An ethereal anime girl" in result
        assert "masterpiece" in result
        assert "**" not in result

    def test_bold_returns_8_sections(self):
        result = _parse_detailed_output(DETAILED_BOLD)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 8

    def test_plain_headers_fallback(self):
        result = _parse_detailed_output(DETAILED_PLAIN)
        assert result != ""
        assert "An ethereal anime girl" in result
        assert "masterpiece" in result

    def test_empty_string_returns_empty(self):
        result = _parse_detailed_output("")
        assert result == ""

    def test_tag_only_output_returns_empty(self):
        result = _parse_detailed_output("1girl, blue hair, masterpiece, best_quality")
        assert result == ""

    def test_no_colon_headers_fallback(self):
        """Bug repro: sections 1-7 without colon, section 8 with colon — all must be extracted."""
        result = _parse_detailed_output(DETAILED_NO_COLON)
        assert result != ""
        assert "An ethereal anime girl" in result
        assert "1girl" in result          # section 2 — would be missing with old regex
        assert "masterpiece" in result    # section 8

    def test_atx_headers_fallback(self):
        """ATX-style (### Header) handled by line-by-line fallback."""
        result = _parse_detailed_output(DETAILED_ATX)
        assert "An ethereal anime girl" in result
        assert "1girl" in result
        assert "masterpiece" in result

    def test_strips_appended_positive_section(self):
        """Spurious POSITIVE: block appended after bold sections must not contaminate output."""
        result = _parse_detailed_output(DETAILED_WITH_APPENDED_POSITIVE)
        assert "An ethereal anime girl" in result
        assert "masterpiece" in result
        assert "POSITIVE" not in result

    def test_strips_appended_pos_neg_sections(self):
        """Both POSITIVE: and NEGATIVE: blocks appended after bold sections must be stripped."""
        result = _parse_detailed_output(DETAILED_WITH_APPENDED_POS_NEG)
        assert "An ethereal anime girl" in result
        assert "masterpiece" in result
        assert "POSITIVE" not in result
        assert "NEGATIVE" not in result
        assert "lowres" not in result


# ── _remove_forced_tags ───────────────────────────────────────────────────────

class TestRemoveForcedTags:
    REMOVAL = {"masterpiece", "best_quality"}

    def test_natural_style_first_line_only(self):
        """natural/danbooru: only the leading tag line is filtered."""
        prompt = "1girl, masterpiece, blue_hair\nAn ethereal girl under moonlight."
        result, removed = _remove_forced_tags(prompt, self.REMOVAL)
        assert "masterpiece" not in result.split('\n')[0]
        assert "An ethereal girl under moonlight." in result
        assert removed == ["masterpiece"]

    def test_natural_style_prose_untouched(self):
        """Prose line must not be modified even if it contains a matching word."""
        prompt = "1girl, blue_hair\nmasterpiece of art under the sky."
        result, _ = _remove_forced_tags(prompt, self.REMOVAL)
        # prose line is left because break fires after first non-empty line
        assert "masterpiece of art under the sky." in result

    def test_detailed_style_all_lines(self):
        """detailed style: forbidden tags in any section line must be removed."""
        prompt = (
            "An ethereal anime girl in starry setting.\n"
            "1girl, solo, long blue hair, portrait composition.\n"
            "masterpiece, best_quality, ultra-detailed, 8k"
        )
        result, removed = _remove_forced_tags(prompt, self.REMOVAL, all_lines=True)
        assert "masterpiece" not in result
        assert "best_quality" not in result
        assert "An ethereal anime girl" in result
        assert "1girl" in result
        assert set(removed) == {"masterpiece", "best_quality"}

    def test_danbooru_multiline_all_lines(self):
        """danbooru: VLM wraps tags across lines — forbidden tag on line 2 must be removed."""
        prompt = "1girl, solo, blue_hair,\nlooking_at_viewer, portrait, masterpiece"
        removal = {"looking_at_viewer", "masterpiece"}
        result, removed = _remove_forced_tags(prompt, removal, all_lines=True)
        assert "looking_at_viewer" not in result
        assert "masterpiece" not in result
        assert "1girl" in result
        assert "blue_hair" in result
        assert set(removed) == {"looking_at_viewer", "masterpiece"}

    def test_empty_removal_set_noop(self):
        prompt = "1girl, masterpiece"
        result, removed = _remove_forced_tags(prompt, set())
        assert result == prompt
        assert removed == []


# ── _translate_instruction (async, mock Ollama) ───────────────────────────────

class TestTranslateInstruction:
    def _make_ollama(self, response: str):
        mock = AsyncMock()
        mock.generate_text = AsyncMock(return_value=response)
        return mock

    def test_translates_japanese(self):
        ollama = self._make_ollama("add text 'Ranbell Image' at top")
        result = asyncio.run(
            _translate_instruction(
                "'Ranbell Image' というテキストを上部に追加して",
                ollama, model="gemma3:4b",
            )
        )
        assert result == "add text 'Ranbell Image' at top"
        ollama.generate_text.assert_called_once()
        call_prompt = ollama.generate_text.call_args[0][0]
        assert "Ranbell Image" in call_prompt

    def test_empty_instruction_skips_llm(self):
        ollama = self._make_ollama("should not be called")
        result = asyncio.run(
            _translate_instruction("", ollama, model="gemma3:4b")
        )
        assert result == ""
        ollama.generate_text.assert_not_called()


# ── _translate_and_classify (async, mock Ollama) ─────────────────────────────

class TestTranslateAndClassify:
    def _make_ollama(self, json_response: str):
        mock = AsyncMock()
        mock.generate_text = AsyncMock(return_value=json_response)
        return mock

    def test_extracts_literal_and_nl(self):
        response = """{
            "instruction_en": "oil painting style, add text 'Ranbell Image' at top",
            "literals": [{"type": "literal_text", "text": "Ranbell Image", "position": "top"}],
            "nl_instruction": "oil painting style"
        }"""
        ollama = self._make_ollama(response)
        en, nl, lits = asyncio.run(
            _translate_and_classify(
                "油絵風、上部に 'Ranbell Image' を追加",
                ollama, model="gemma3:4b",
            )
        )
        assert "Ranbell Image" in en
        assert nl == "oil painting style"
        assert len(lits) == 1
        assert lits[0]["text"] == "Ranbell Image"
        assert lits[0]["position"] == "top"

    def test_no_literals_returns_full_instruction(self):
        response = """{
            "instruction_en": "oil painting style",
            "literals": [],
            "nl_instruction": "oil painting style"
        }"""
        ollama = self._make_ollama(response)
        _, nl, lits = asyncio.run(
            _translate_and_classify("油絵風", ollama, model="gemma3:4b")
        )
        assert lits == []
        assert nl == "oil painting style"

    def test_malformed_json_falls_back(self):
        ollama = self._make_ollama("this is not json")
        en, nl, lits = asyncio.run(
            _translate_and_classify("some instruction", ollama, model="gemma3:4b")
        )
        assert en == "some instruction"
        assert nl == "some instruction"
        assert lits == []


# ── VLM prompt integration: instruction_mode × prompt_style ──────────────────

DUMMY_CONTEXT = "[Image 1 — influence weight: 100%]\nPrompt: 1girl, blue hair\nMust include: blue_hair"


class TestVlmPromptCombinations:
    """Verify the correct vlm_prompt is built for every mode × style combination.
    Tests _build_vlm_prompt() with the pre-processed instruction to simulate
    what run_refine_prompt() does.
    """

    INSTRUCTION_JP = "'Ranbell Image' というテキストを上部に表示して、油絵風にして"
    INSTRUCTION_EN_TRANSLATED = "add text 'Ranbell Image' at top, oil painting style"

    @pytest.mark.parametrize("style", ["natural", "danbooru", "detailed"])
    def test_none_mode_passes_raw_instruction(self, style):
        """mode=none: instruction sent to VLM unchanged, no framing."""
        prompt = _build_vlm_prompt(
            DUMMY_CONTEXT, self.INSTRUCTION_JP, style,
            with_negative=False, instruction_framing=False,
        )
        assert self.INSTRUCTION_JP in prompt
        assert "PROMPT ENGINEERING DIRECTIVE" not in prompt

    @pytest.mark.parametrize("style", ["natural", "danbooru", "detailed"])
    def test_basic_mode_removes_literal_before_vlm(self, style):
        """mode=basic: literal text extracted BEFORE VLM call.
        VLM receives: cleaned NL instruction + framing, NOT the literal text part."""
        cleaned, lits = _extract_literal_directives(self.INSTRUCTION_EN_TRANSLATED)
        assert len(lits) == 1, "literal should be extracted"
        prompt = _build_vlm_prompt(
            DUMMY_CONTEXT, cleaned, style,
            with_negative=False, instruction_framing=True,
        )
        # literal text must NOT appear in VLM prompt
        assert "Ranbell Image" not in prompt
        # NL part should be present
        assert "oil painting" in prompt
        # Framing must be applied
        assert "PROMPT ENGINEERING DIRECTIVE" in prompt

    @pytest.mark.parametrize("style", ["natural", "danbooru", "detailed"])
    def test_enhanced_mode_removes_literal_before_vlm(self, style):
        """mode=enhanced: simulate LLM classification output.
        VLM receives: nl_instruction only + framing."""
        nl_instruction = "oil painting style"  # simulate classified NL part
        prompt = _build_vlm_prompt(
            DUMMY_CONTEXT, nl_instruction, style,
            with_negative=False, instruction_framing=True,
        )
        assert "Ranbell Image" not in prompt
        assert "oil painting" in prompt
        assert "PROMPT ENGINEERING DIRECTIVE" in prompt

    @pytest.mark.parametrize("style", ["natural", "danbooru", "detailed"])
    def test_style_instructions_present_in_prompt(self, style):
        """Style directive is always included in the VLM prompt."""
        prompt = _build_vlm_prompt(
            DUMMY_CONTEXT, "", style,
            with_negative=False, instruction_framing=False,
        )
        # Key phrase unique to each style
        style_marker = {
            "natural": "BLOCK 1 (tags)",
            "danbooru": "80–120 comma-separated English tags",
            "detailed": "Core Subject",
        }[style]
        assert style_marker in prompt

    def test_detailed_style_contains_all_8_headers(self):
        """detailed style instruction must list all 8 section headers."""
        prompt = _build_vlm_prompt(
            DUMMY_CONTEXT, "", "detailed",
            with_negative=False, instruction_framing=False,
        )
        for header in [
            "Core Subject", "Characters", "Lighting",
            "Style", "Details", "Color Palette", "Camera", "Refinements",
        ]:
            assert header in prompt, f"Missing section header: {header}"

    def test_basic_mode_literal_injected_into_positive(self):
        """End-to-end: literal extracted → not in VLM prompt → injected into positive."""
        cleaned, lits = _extract_literal_directives(
            "add text 'Ranbell Image' at top, oil painting style"
        )
        # Simulate VLM output (no literal in it)
        fake_vlm_output = "oil_painting, masterpiece, 1girl, blue_hair"
        positive = _inject_literal_directives(fake_vlm_output, lits)
        assert positive.startswith('text "Ranbell Image"')
        assert "top_text" in positive
        assert "text_on_image" in positive
        assert "oil_painting" in positive

    def test_none_mode_empty_literals_no_injection(self):
        """mode=none: no extraction, no injection."""
        lits: list = []
        fake_vlm_output = "1girl, blue_hair, masterpiece"
        result = _inject_literal_directives(fake_vlm_output, lits)
        assert result == fake_vlm_output

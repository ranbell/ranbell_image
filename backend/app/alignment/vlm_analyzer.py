from __future__ import annotations

import json
import logging
import re

from ..ai.ollama import OllamaClient
from .categories import CATEGORIES

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are an AI image generation evaluation assistant.
Evaluate how well the image follows the given prompt.

[Prompt (author's original input)]
{positive_prompt}

[Tags extracted from the image]
{image_tags}

[Overall score]
{score}

[Evaluation categories]
{categories}

Respond ONLY with a JSON object in exactly this format — no markdown, no code fences, no explanation:

{{"summary_ja": "Concise description of mismatches in Japanese (1-2 sentences)", "summary_en": "Brief description of mismatches in English (1-2 sentences)", "matched_elements_ja": ["Prompt elements that are reflected in the image, in Japanese"], "matched_elements_en": ["Elements from the prompt reflected in the image, in English"], "unmatched_elements_ja": ["Prompt elements missing from the image, in Japanese"], "unmatched_elements_en": ["Elements from the prompt missing in the image, in English"], "categories": ["mismatch categories chosen from the list above"]}}\
"""

_TRANSLATE_TEMPLATE = """\
Translate the following image evaluation data into {lang_name}.
Return ONLY a JSON object with exactly these keys — no markdown, no code fences, no explanation:

{{"summary": "translated summary (1-2 sentences)", "matched_elements": ["translated matched element list"], "unmatched_elements": ["translated unmatched element list"]}}

Source data (Japanese):
summary: {summary_ja}
matched_elements: {matched_ja}
unmatched_elements: {unmatched_ja}\
"""

_JP_RE = re.compile(r"[぀-ヿ一-鿿㐀-䶿]")


def _is_japanese(text: str | list) -> bool:
    """Return True if text (or any element of a list) contains Japanese characters."""
    if isinstance(text, list):
        return any(_JP_RE.search(s) for s in text if isinstance(s, str))
    return bool(_JP_RE.search(text))


_LANG_NAMES: dict[str, str] = {
    "zh": "Simplified Chinese (简体中文)",
    "ko": "Korean (한국어)",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
}


def _normalize_categories(cats: list) -> list[str]:
    return [c if c in CATEGORIES else "other" for c in cats if isinstance(c, str)]


def _extract_json(text: str) -> dict:
    # Strip opening/closing markdown fences (handles unclosed fences too)
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip())
    stripped = re.sub(r"\s*```\s*$", "", stripped)

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError(f"No JSON object found in LLM output (len={len(text)}): {text[:200]!r}")


def _build_i18n_result(raw_result: dict) -> dict:
    """Convert bilingual LLM output into i18n-structured dicts, keeping legacy fields (ja)."""
    summary_ja = raw_result.get("summary_ja", "")
    summary_en = raw_result.get("summary_en", "")
    matched_ja = raw_result.get("matched_elements_ja", [])
    matched_en = raw_result.get("matched_elements_en", [])
    unmatched_ja = raw_result.get("unmatched_elements_ja", [])
    unmatched_en = raw_result.get("unmatched_elements_en", [])
    categories = _normalize_categories(raw_result.get("categories", []))

    summary_i18n: dict[str, str] = {}
    matched_i18n: dict[str, list[str]] = {}
    unmatched_i18n: dict[str, list[str]] = {}

    if summary_ja:
        summary_i18n["ja"] = summary_ja
    if summary_en and not _is_japanese(summary_en):
        summary_i18n["en"] = summary_en
    if matched_ja:
        matched_i18n["ja"] = matched_ja
    if matched_en and not _is_japanese(matched_en):
        matched_i18n["en"] = matched_en
    if unmatched_ja:
        unmatched_i18n["ja"] = unmatched_ja
    if unmatched_en and not _is_japanese(unmatched_en):
        unmatched_i18n["en"] = unmatched_en

    return {
        # Legacy fields — always Japanese (retained for anime-culture compatibility)
        "summary": summary_ja,
        "matched_elements": matched_ja,
        "unmatched_elements": unmatched_ja,
        "categories": categories,
        "summary_i18n": summary_i18n,
        "matched_elements_i18n": matched_i18n,
        "unmatched_elements_i18n": unmatched_i18n,
    }


async def analyze_with_llm(
    positive_prompt: str,
    image_tags: list[str],
    score: float,
    ollama: OllamaClient,
    model: str | None = None,
    max_retries: int = 3,
) -> dict:
    tags_text = ", ".join(image_tags)
    categories_text = ", ".join(CATEGORIES)
    prompt = _PROMPT_TEMPLATE.format(
        positive_prompt=positive_prompt,
        image_tags=tags_text,
        score=f"{score:.4f}",
        categories=categories_text,
    )

    _options = {"num_predict": 8192}
    last_raw = ""
    for attempt in range(max_retries):
        try:
            raw = await ollama.generate_text(prompt, model=model, options=_options, fmt="json")
            last_raw = raw
            result = _extract_json(raw)
            return _build_i18n_result(result)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "LLM JSON parse failure (attempt %d/%d): %s",
                attempt + 1,
                max_retries,
                exc,
            )

    logger.error("All %d LLM retries failed; storing raw text", max_retries)
    return {
        "summary": last_raw[:2000],
        "matched_elements": [],
        "unmatched_elements": [],
        "categories": [],
        "summary_i18n": {},
        "matched_elements_i18n": {},
        "unmatched_elements_i18n": {},
    }


async def translate_to_lang(
    summary_ja: str,
    matched_ja: list[str],
    unmatched_ja: list[str],
    lang: str,
    ollama: OllamaClient,
    model: str | None = None,
    max_retries: int = 3,
) -> dict[str, str | list[str]] | None:
    """Translate existing Japanese evaluation text into a new language via Ollama.

    Returns {"summary": str, "matched_elements": list, "unmatched_elements": list}
    or None if all retries fail.
    """
    lang_name = _LANG_NAMES.get(lang, lang)
    prompt = _TRANSLATE_TEMPLATE.format(
        lang_name=lang_name,
        summary_ja=summary_ja,
        matched_ja=json.dumps(matched_ja, ensure_ascii=False),
        unmatched_ja=json.dumps(unmatched_ja, ensure_ascii=False),
    )

    _options = {"num_predict": 8192}
    for attempt in range(max_retries):
        try:
            raw = await ollama.generate_text(prompt, model=model, options=_options, fmt="json")
            result = _extract_json(raw)
            return {
                "summary": result.get("summary", ""),
                "matched_elements": result.get("matched_elements", []),
                "unmatched_elements": result.get("unmatched_elements", []),
            }
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Translation JSON parse failure (attempt %d/%d, lang=%s): %s",
                attempt + 1,
                max_retries,
                lang,
                exc,
            )

    logger.error("All %d translation retries failed for lang=%s", max_retries, lang)
    return None

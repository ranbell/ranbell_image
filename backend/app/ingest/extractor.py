"""Main entry point for extracting metadata from image files."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image

from .a1111_parser import parse_a1111
from .comfyui_parser import (
    detect_format,
    extract_model_from_visual_workflow,
    extract_params_from_workflow,
)
from .schema import ExtractionInfo, ExtractionResult, ModelInfo, RawMetadata
from .trace import direct_search_prompts, trace_prompts
from ._utils import basename, compute_hash, now_iso

logger = logging.getLogger(__name__)


def extract_from_image(path: Path) -> ExtractionResult:
    """Build ExtractionResult from an image file (with fallback chain)."""
    try:
        png_info = _read_png_info(path)
    except Exception as exc:
        logger.warning("metadata read failed: %s — %s", path, exc)
        return _failed_result()

    fmt = detect_format(png_info)

    # 1. A1111 parameters (highest priority)
    parameters = png_info.get("parameters", "")
    _a1111_short: ExtractionResult | None = None
    if parameters and isinstance(parameters, str):
        result = parse_a1111(parameters)
        if result.positive_prompt:
            if _word_count(result.positive_prompt) > 2:
                logger.debug("A1111 extraction succeeded: %s", path.name)
                return result
            # 2 words or fewer → try ComfyUI fallback
            logger.debug(
                "A1111 prompt too short (%d words), trying ComfyUI fallback: %s",
                _word_count(result.positive_prompt), path.name,
            )
            _a1111_short = result

    # 2. ComfyUI workflow: KSampler reverse-lookup
    prompt_json: dict | None = None
    prompt_raw = png_info.get("prompt", "")
    if prompt_raw:
        try:
            prompt_json = json.loads(prompt_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    workflow_json: dict | None = None
    workflow_raw = png_info.get("workflow", "")
    if workflow_raw:
        try:
            workflow_json = json.loads(workflow_raw)
        except (json.JSONDecodeError, TypeError):
            pass

    # KSampler reverse-lookup using ComfyUI API prompt
    if prompt_json:
        pos, neg, warnings = trace_prompts(prompt_json, workflow_json)
        if pos:
            params = extract_params_from_workflow(prompt_json)
            model_info = _build_model_info(params, workflow_json)
            rescued = "rescued_from_widget" in warnings
            method = "rescued_from_widget" if rescued else "ksampler_trace"
            logger.debug("%s: %s", method, path.name)
            return ExtractionResult(
                positive_prompt=pos,
                negative_prompt=neg,
                model_info=model_info,
                extraction=ExtractionInfo(
                    method=method,
                    confidence="high",
                    warnings=warnings,
                    extracted_at=now_iso(),
                ),
                raw_metadata=RawMetadata(
                    format="comfyui",
                    content=prompt_json,
                    hash=compute_hash(prompt_json),
                ),
                params=params,
            )

        # 3. CLIPTextEncode direct search
        pos, neg, warnings = direct_search_prompts(prompt_json)
        if pos:
            params = extract_params_from_workflow(prompt_json)
            model_info = _build_model_info(params, workflow_json)
            logger.debug("direct search succeeded (confidence=low): %s", path.name)
            return ExtractionResult(
                positive_prompt=pos,
                negative_prompt=neg,
                model_info=model_info,
                extraction=ExtractionInfo(
                    method="direct_search",
                    confidence="low",
                    warnings=warnings,
                    extracted_at=now_iso(),
                ),
                raw_metadata=RawMetadata(
                    format="comfyui",
                    content=prompt_json,
                    hash=compute_hash(prompt_json),
                ),
                params=params,
            )

        # 4. Use the longest text field as the positive prompt
        longest = _find_longest_text(prompt_json)
        if longest:
            params = extract_params_from_workflow(prompt_json)
            model_info = _build_model_info(params, workflow_json)
            logger.debug("longest text fallback (confidence=low): %s", path.name)
            return ExtractionResult(
                positive_prompt=longest,
                negative_prompt=None,
                model_info=model_info,
                extraction=ExtractionInfo(
                    method="longest_text",
                    confidence="low",
                    warnings=["CLIPTextEncode not found; using longest text field as positive prompt"],
                    extracted_at=now_iso(),
                ),
                raw_metadata=RawMetadata(
                    format=fmt,
                    content=prompt_json,
                    hash=compute_hash(prompt_json),
                ),
                params=params,
            )

    # 5. Fall back to the short A1111 prompt if ComfyUI also failed
    if _a1111_short is not None:
        wc = _word_count(_a1111_short.positive_prompt)
        _a1111_short.extraction.warnings.append(
            f"A1111 prompt too short ({wc} words); "
            "no alternative from ComfyUI, using A1111"
        )
        logger.debug("A1111 short fallback adopted: %s", path.name)
        return _a1111_short

    # 6. Extraction failed
    logger.debug("metadata extraction failed: %s", path.name)
    raw_content: dict | str = prompt_json or parameters or {}
    return ExtractionResult(
        positive_prompt=None,
        negative_prompt=None,
        model_info=ModelInfo(),
        extraction=ExtractionInfo(
            method="failed",
            confidence="low",
            warnings=["Could not extract prompt from metadata"],
            extracted_at=now_iso(),
        ),
        raw_metadata=RawMetadata(
            format=fmt,
            content=raw_content,
            hash=compute_hash(raw_content),
        ),
    )


def _read_png_info(path: Path) -> dict:
    with Image.open(path) as img:
        return img.info or {}


def _failed_result() -> ExtractionResult:
    return ExtractionResult(
        positive_prompt=None,
        negative_prompt=None,
        model_info=ModelInfo(),
        extraction=ExtractionInfo(
            method="failed",
            confidence="low",
            warnings=["Failed to read metadata"],
            extracted_at=now_iso(),
        ),
        raw_metadata=RawMetadata(format="unknown", content={}, hash=""),
    )


def _build_model_info(params: dict, workflow_json: dict | None) -> ModelInfo:
    model_name = params.get("Model")
    if not model_name and workflow_json:
        model_name = extract_model_from_visual_workflow(workflow_json) or None

    return ModelInfo(
        model_name=model_name or None,
        sampler=params.get("Sampler") or None,
        steps=_to_int(params.get("Steps")),
        cfg_scale=_to_float(params.get("CFG scale")),
        seed=_to_int(params.get("Seed")),
    )


def _word_count(text: str) -> int:
    return len(text.split())


def _find_longest_text(workflow: dict) -> str | None:
    """Return the longest text string found across all node text fields."""
    longest: str | None = None
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        for val in inputs.values():
            if isinstance(val, str) and val.strip():
                if longest is None or len(val) > len(longest):
                    longest = val
    return longest


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

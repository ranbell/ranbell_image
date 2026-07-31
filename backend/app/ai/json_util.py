"""Best-effort JSON extraction from LLM text.

Ollama is asked for ``format: json``, but local models still emit a missing
comma or a truncated tail often enough that losing a whole generation to one
character is not acceptable. The repairs below are deliberately conservative:
each is tried in turn and kept only if the result parses.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# A value directly followed by the next key on a new line — the comma the model
# forgot. Anchored on a real `"key":` so prose inside a string is left alone.
_MISSING_COMMA = re.compile(
    r'([\]\}"]|\d|true|false|null)(\s*\n\s*)("(?:[^"\\]|\\.)*"\s*:)'
)


def _strip_trailing_commas(text: str) -> str:
    return _TRAILING_COMMA.sub(r"\1", text)


def _insert_missing_commas(text: str) -> str:
    return _MISSING_COMMA.sub(r"\1,\2\3", text)


def _close_truncated(text: str) -> str:
    """Shut an output that stopped mid-object (token limit)."""
    out = text.rstrip()
    # An unterminated string cannot be guessed at — cut back to the last comma
    # or opening bracket so the closers below land on a clean boundary.
    if out.count('"') % 2:
        cut = max(out.rfind(","), out.rfind("["), out.rfind("{"))
        if cut < 0:
            return text
        out = out[:cut] if out[cut] == "," else out[: cut + 1]
    out = out.rstrip().rstrip(",")
    depth: list[str] = []
    in_string = False
    escaped = False
    for ch in out:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth.append(ch)
        elif ch in "}]" and depth:
            depth.pop()
    return out + "".join("}" if b == "{" else "]" for b in reversed(depth))


_REPAIRS = (
    ("trailing_comma", _strip_trailing_commas),
    ("missing_comma", _insert_missing_commas),
    ("truncated", _close_truncated),
)


def _parse_repaired(candidate: str, first_error: json.JSONDecodeError) -> Any:
    applied: list[str] = []
    patched = candidate
    for name, repair in _REPAIRS:
        patched = repair(patched)
        applied.append(name)
        try:
            data = json.loads(patched)
        except json.JSONDecodeError:
            continue
        logger.info("[json_util] repaired malformed LLM JSON via %s", "+".join(applied))
        return data
    raise first_error


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    # Strip fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as first_error:
        start = text.find("{")
        if start < 0:
            raise
        end = text.rfind("}")
        candidate = text[start : end + 1] if end > start else text[start:]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = _parse_repaired(candidate, first_error)
    if not isinstance(data, dict):
        raise ValueError("LLM JSON root must be an object")
    return data

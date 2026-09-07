"""Anima (CircleStone) prompt hygiene for Muse Refine.

Sources (generation-time, not LoRA training captions):
- Official model card: https://huggingface.co/circlestone-labs/Anima
  - lowercase tags; spaces not underscores (score_* excepted)
  - order: quality/meta/safety → count → character → … → general
  - tags ↔ natural language: period + space (documented example)
  - quality prefix: masterpiece, best quality (, score_7, safe) — Aesthetic
    prefers omitting score_*
  - multi-char: name then appearance; do not list bare names alone
  - in-image text is weak; keep short Latin phrases if any
- Community (Civitai “How Your Prompt Breaks ANIMA”):
  - avoid (tag:weight) emphasis — Qwen reads it literally
  - do not restating the same concept 3+ times
  - one spatial directive; stay concise

Muse ownership newlines (`Mio is` / `Mio:`) stay — studio-measured for W
shots. This module only finalises the string Anima / FLUX-natural expects.
"""
from __future__ import annotations

import re
from typing import Iterable

# Official-ish quality / meta / safety that belong in the prefix slot.
_QUALITY_META = {
    "masterpiece", "best quality", "best_quality", "good quality", "good_quality",
    "highres", "absurdres", "ultra detailed", "ultra-detailed", "ultra_detailed",
    "highly detailed", "highly_detailed", "newest", "recent", "mid", "early", "old",
    "safe", "sensitive", "nsfw", "explicit",
    "score_9", "score_8", "score_7", "score_6", "score_5", "score_4",
    "year 2024", "year 2025", "year_2024", "year_2025",
}

# Aesthetic-safe default when enhance_quality is on but enrich returned nothing.
# Omit score_* — official Aesthetic note says score tags can push into slop.
DEFAULT_QUALITY_PREFIX: tuple[str, ...] = ("masterpiece", "best quality")

_SCORE_TAG_RE = re.compile(r"^score_\d+$", re.I)
_WEIGHT_PAREN_RE = re.compile(
    r"^\(\s*(?P<body>.+?)\s*:\s*(?P<w>\d+(?:\.\d+)?)\s*\)$"
)
_WEIGHT_BARE_RE = re.compile(
    r"^(?P<body>.+?)\s*:\s*(?P<w>\d+(?:\.\d+)?)$"
)
_PROSE_START_RE = re.compile(
    r"^(The shot\b|Keep exactly\b|Do not swap\b|[A-Z][a-z]+ (?:is|stands|sits|wears|holds)\b)",
)


def is_quality_meta(tag: str) -> bool:
    raw = (tag or "").strip().strip(",").lower().replace("_", " ")
    if not raw:
        return False
    underscored = raw.replace(" ", "_")
    if underscored in _QUALITY_META or raw in _QUALITY_META:
        return True
    if _SCORE_TAG_RE.match(underscored):
        return True
    if raw.startswith("year ") or underscored.startswith("year_"):
        return True
    return False


def split_quality_support(tags: Iterable[str]) -> tuple[list[str], list[str]]:
    """Partition support tags into (quality_prefix, atmosphere_rest)."""
    quality: list[str] = []
    rest: list[str] = []
    seen_q: set[str] = set()
    seen_r: set[str] = set()
    for raw in tags:
        tag = str(raw or "").strip()
        if not tag:
            continue
        if is_quality_meta(tag):
            key = tag.lower().replace("_", " ")
            if key not in seen_q:
                quality.append(tag)
                seen_q.add(key)
        else:
            key = tag.lower()
            if key not in seen_r:
                rest.append(tag)
                seen_r.add(key)
    return quality, rest


def strip_weight(token: str) -> str:
    """Drop (tag:1.2) / tag:1.2 — Anima's Qwen encoder treats weights literally."""
    text = (token or "").strip()
    if not text:
        return ""
    m = _WEIGHT_PAREN_RE.match(text)
    if m:
        return m.group("body").strip()
    m = _WEIGHT_BARE_RE.match(text)
    if m and not text.lower().startswith("http"):
        # Avoid stripping ordinary phrases that use a colon casually.
        body = m.group("body").strip()
        if " " not in body or "_" in body:
            return body
    return text.strip("()[]")


def tag_to_anima_spaces(token: str) -> str:
    """Spaces instead of underscores; keep score_* underscored."""
    text = strip_weight(token).strip().strip(",")
    if not text:
        return ""
    # Preserve literal-text directives exactly.
    if text.lower().startswith("text ") or text.lower() == "text_on_image":
        return text if text.lower() != "text_on_image" else "text_on_image"
    low = text.lower().replace(" ", "_")
    if _SCORE_TAG_RE.match(low):
        return low
    # Keep @artist prefix intact; only flatten underscores in the name.
    if text.startswith("@"):
        return "@" + text[1:].replace("_", " ").strip()
    return text.replace("_", " ")


def _format_tag_line(line: str) -> str:
    """Rewrite one ownership / tag line to Anima spacing, no weights."""
    text = (line or "").rstrip()
    if not text:
        return ""
    # Preserve structural prefixes: "2girls, Mio and Sumire," / "Mio is …," / "Mio: …,"
    trailing_comma = text.endswith(",")
    body = text[:-1] if trailing_comma else text

    # Named dynamic / identity lines keep the label, rewrite the tag run.
    for sep in (": ", " is "):
        if sep in body:
            head, _, rest = body.partition(sep)
            # Only treat as Muse ownership when head looks like a short name.
            if 0 < len(head.split()) <= 4 and "," not in head:
                parts = [tag_to_anima_spaces(p) for p in rest.split(",")]
                parts = [p for p in parts if p]
                rebuilt = f"{head}{sep}" + ", ".join(parts)
                return rebuilt + ("," if trailing_comma else "")

    parts = [tag_to_anima_spaces(p) for p in body.split(",")]
    parts = [p for p in parts if p]
    rebuilt = ", ".join(parts)
    return rebuilt + ("," if trailing_comma and rebuilt else "")


def _looks_like_prose_line(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False
    if _PROSE_START_RE.match(text):
        return True
    words = text.split()
    if len(words) < 8:
        return False
    comma_density = text.count(",") / max(len(words), 1)
    # Tag lines are comma-heavy; prose is sentence-like.
    return comma_density < 0.35 and ("." in text or len(words) >= 12)


def split_tags_and_prose(prompt: str) -> tuple[str, str]:
    """Split Muse boxed output into tag block + prose paragraph."""
    text = (prompt or "").strip()
    if not text:
        return "", ""
    lines = text.splitlines()
    # Find the first prose line from the end; everything before is tags.
    prose_start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if _looks_like_prose_line(lines[i]):
            prose_start = i
        else:
            if prose_start < len(lines):
                break
    if prose_start >= len(lines):
        # No prose detected — whole prompt is tags.
        return text, ""
    tag_block = "\n".join(lines[:prose_start]).strip()
    prose = " ".join(l.strip() for l in lines[prose_start:] if l.strip())
    return tag_block, prose


def extract_lettering(text: str) -> tuple[list[str], str]:
    """Pull short in-image text asks out of a director line.

    Returns (phrases, cleaned_text). Reuses alchemy's Anima `text "…"` spirit
    without importing the heavy ai module.
    """
    raw = text or ""
    phrases: list[str] = []
    seen: set[str] = set()

    patterns = [
        # text:"X" / text: X / /text X
        re.compile(
            r"(?i)(?:^|(?<=[\s,]))(?:/text\s+|text\s*:\s*)['\"「]?([^'\"」\n,]+?)['\"」]?(?=\s*[,\n]|$)"
        ),
        # English write/add "X"
        re.compile(
            r"(?i)(?:add|insert|put|place|show|write|display|include|render)\s+"
            r"(?:(?:the\s+)?(?:text|word|words|label|sign|banner|title)\s+)?"
            r"['\"「]([^'\"」\n]+)['\"」]"
        ),
        # Japanese 「X」を看板/文字に
        re.compile(
            r"['\"「『]([^'\"」』\n]{1,40})['\"」』]\s*(?:という|との|と|の)?\s*"
            r"(?:文字|テキスト|タイトル|文章|ラベル|キャプション)?\s*を?\s*"
            r"(?:に|で)?\s*(?:入れ|描画|追加|表示|書い|記載)"
        ),
        # 看板/ボードに「X」
        re.compile(
            r"(?i)(?:textboard|sign|banner|label|board|ボード|看板|テキストボード)\s*"
            r"(?:に|へ|で)?\s*['\"「『]([^'\"」』\n]{1,40})['\"」』]"
        ),
        # LETTERING: X
        re.compile(r"(?im)^\s*LETTERING\s*[:：]\s*(.+?)\s*$"),
    ]

    cleaned = raw
    for pat in patterns:
        for m in list(pat.finditer(cleaned)):
            phrase = (m.group(1) or "").strip().strip("\"'「」『』")
            if not phrase or len(phrase) > 48:
                continue
            # Prefer Latin/digits for Anima legibility (Muse LETTERING rule).
            key = phrase.lower()
            if key not in seen:
                phrases.append(phrase)
                seen.add(key)
        cleaned = pat.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,")
    return phrases[:1], cleaned  # one sign per picture


def append_lettering(positive: str, phrases: Iterable[str]) -> str:
    """Append Anima/alchemy literal tags at the very end."""
    texts = [str(t).strip() for t in phrases if str(t).strip()]
    if not texts:
        return positive
    # One sign — Muse LETTERING + Anima weakness on long text.
    tags = [f'text "{texts[0]}"', "text_on_image"]
    base = (positive or "").rstrip().rstrip(",").rstrip()
    if not base:
        return ", ".join(tags)
    return base + "\n" + ", ".join(tags)


def format_for_anima(
    prompt: str,
    *,
    quality_tags: Iterable[str] | None = None,
    lettering: Iterable[str] | None = None,
    enhance_quality: bool = False,
) -> str:
    """Final Anima-facing positive: spaces, quality prefix, blank-line prose."""
    tag_block, prose = split_tags_and_prose(prompt)
    tag_lines = [ln for ln in tag_block.splitlines() if ln.strip()] if tag_block else []

    # Collect quality tags embedded in the tag block + caller list.
    collected_quality: list[str] = []
    rewritten_lines: list[str] = []
    for line in tag_lines:
        # Flatten line → tokens for quality harvest, then rewrite.
        trailing = line.rstrip().endswith(",")
        body = line.rstrip()[:-1] if trailing else line.rstrip()
        # Ownership lines: only harvest from the tag run.
        run = body
        prefix = ""
        for sep in (": ", " is "):
            if sep in body:
                head, _, rest = body.partition(sep)
                if 0 < len(head.split()) <= 4 and "," not in head:
                    prefix = head + sep
                    run = rest
                    break
        kept: list[str] = []
        for part in run.split(","):
            tok = strip_weight(part)
            if not tok:
                continue
            if is_quality_meta(tok):
                collected_quality.append(tok)
            else:
                kept.append(tok)
        if prefix:
            rebuilt = prefix + ", ".join(tag_to_anima_spaces(t) for t in kept if t)
            if rebuilt.endswith((" is", ":")):
                # Empty dynamic run — drop the orphan label line.
                continue
            rewritten_lines.append(rebuilt + ("," if trailing else ""))
        else:
            parts = [tag_to_anima_spaces(t) for t in kept if t]
            if parts:
                rewritten_lines.append(", ".join(parts) + ("," if trailing else ""))

    for q in (quality_tags or []):
        if str(q).strip():
            collected_quality.append(str(q).strip())

    # Dedupe quality; Aesthetic-safe — drop score_* unless user/enrich put them.
    quality_out: list[str] = []
    seen_q: set[str] = set()
    for q in collected_quality:
        spaced = tag_to_anima_spaces(q)
        if not spaced:
            continue
        key = spaced.lower()
        if key in seen_q:
            continue
        # Prefer human quality words; keep score only if explicitly present.
        seen_q.add(key)
        quality_out.append(spaced)

    if enhance_quality:
        # Official card: "masterpiece, best quality, …" — fill gaps, keep order.
        # score_* only if enrich/user already supplied them (Aesthetic prefers omit).
        merged: list[str] = []
        seen_m: set[str] = set()
        for q in (*DEFAULT_QUALITY_PREFIX, *quality_out):
            key = q.lower()
            if key in seen_m:
                continue
            seen_m.add(key)
            merged.append(q)
        quality_out = merged

    if quality_out and rewritten_lines:
        # Official order: quality/meta first, then count / ownership.
        first = rewritten_lines[0]
        # Avoid duplicating if already prefixed.
        head_low = first.lower()
        if not any(q.lower() in head_low[:80] for q in quality_out[:2]):
            rewritten_lines[0] = ", ".join(quality_out) + ", " + first.lstrip(", ")
    elif quality_out and not rewritten_lines:
        rewritten_lines = [", ".join(quality_out) + ","]

    tags_text = "\n".join(rewritten_lines).strip()
    # Soft period on the last tag line before prose (model-card example).
    if tags_text and prose:
        lines = tags_text.splitlines()
        last = lines[-1].rstrip().rstrip(",").rstrip()
        if last and not last.endswith("."):
            last = last + "."
        lines[-1] = last
        tags_text = "\n".join(lines)

    if tags_text and prose:
        out = tags_text + "\n\n" + prose.strip()
    elif tags_text:
        out = tags_text
    else:
        out = (prose or "").strip()

    letter_list = [str(x).strip() for x in (lettering or []) if str(x).strip()]
    if not letter_list:
        # Ledger may store a single phrase.
        pass
    return append_lettering(out, letter_list)

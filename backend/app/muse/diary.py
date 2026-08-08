"""Reading her diary back out of whatever the model actually returned.

The diary is the worst-shaped payload in the app: two languages, several
paragraphs each, in a voice full of 「」 and ellipses and line breaks. Asked for
that as JSON, a local model breaks the JSON — an unescaped newline inside a
string, a quote in the middle of a line, or a tail that simply stops when the
context window runs out. The old reader was one `re.search(r"\\{.*\\}")` and one
`json.loads`, and when it failed it stored the raw response as her diary, so the
Showrunner opened the panel and read a JSON object back.

So the contract is labelled blocks now — the same idiom the table read uses for
SAY / TAGS / SCENE — because a label on its own line cannot be broken by the
prose that follows it. JSON is still *accepted*: models that ignore the new
contract, and every entry written before it, have to keep working.

Order of attempts: blocks, then `ai.json_util.parse_json_object` (which already
knows how to strip fences, patch commas and close a truncated tail), then a
field-level salvage that reads through a string that was never terminated.
Whatever comes out, `looks_like_json` gets the last word: scaffolding must never
reach the page.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..ai.json_util import parse_json_object

logger = logging.getLogger(__name__)

FIELDS: tuple[str, ...] = ("summary_ja", "summary_en", "content_ja", "content_en")

# A label owns everything up to the next label, so the body can hold anything.
_LABEL_RE = re.compile(
    r"^[ \t]*[#*\-]*[ \t]*(SUMMARY_JA|SUMMARY_EN|CONTENT_JA|CONTENT_EN)[ \t]*[:：]?[ \t]*$"
    r"|^[ \t]*[#*\-]*[ \t]*(SUMMARY_JA|SUMMARY_EN|CONTENT_JA|CONTENT_EN)[ \t]*[:：][ \t]*(.*)$",
    re.IGNORECASE,
)
# Reads a JSON string value that may never have been closed — the truncated
# tail is exactly the case where the whole object is unparseable.
_SALVAGE_TMPL = r'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)'
_KEY_LINE_RE = re.compile(r'^\s*[{\[]?\s*"[a-z_]+"\s*:\s*"?', re.IGNORECASE)
_BRACE_LINE_RE = re.compile(r"^\s*[{}\[\],]+\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def parse_diary(raw: str) -> dict[str, str]:
    """Her four fields, by whichever route survives. Missing keys are absent."""
    text = (raw or "").strip()
    if not text:
        return {}
    blocks = _parse_blocks(text)
    if blocks:
        return blocks
    try:
        data = parse_json_object(text)
    except Exception:
        logger.debug("[muse.diary] not JSON either; salvaging fields", exc_info=True)
    else:
        out = _from_mapping(data)
        if out:
            return out
    return _salvage(text)


def _parse_blocks(text: str) -> dict[str, str]:
    """Split on labels sitting at the start of a line."""
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            current = (m.group(1) or m.group(2) or "").lower()
            bodies.setdefault(current, [])
            inline = (m.group(3) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies[current].append(line)
    out = {k: "\n".join(v).strip() for k, v in bodies.items() if k in FIELDS}
    return {k: v for k, v in out.items() if v}


def _from_mapping(data: dict[str, Any]) -> dict[str, str]:
    """Pull the four fields out of a parsed object, plain keys included."""
    out: dict[str, str] = {}
    for key in FIELDS:
        value = data.get(key)
        if value is None:
            # `summary` / `content` are what the older prompt asked for, and are
            # still the Japanese side.
            value = data.get(key.rsplit("_", 1)[0]) if key.endswith("_ja") else None
        text = str(value or "").strip()
        if text:
            out[key] = text
    return out


def _salvage(text: str) -> dict[str, str]:
    """Last resort: read each field off broken JSON, unterminated tail included."""
    out: dict[str, str] = {}
    for key in FIELDS:
        m = re.search(_SALVAGE_TMPL.format(key=key), text, re.DOTALL)
        if not m:
            continue
        value = _unescape(m.group(1)).strip()
        if value:
            out[key] = value
    if out:
        return out
    # No recognisable keys at all — she ignored the contract and simply wrote.
    # That is still her diary; it is only the scaffolding we refuse to keep.
    body = strip_scaffolding(text)
    if not body or looks_like_json(body):
        return {}
    return {"content_ja": body}


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}


def _unescape(value: str) -> str:
    """Undo JSON string escapes only.

    Deliberately not `unicode_escape`: that codec goes through latin-1 and
    either raises or mangles every Japanese character in the body.
    """
    def _sub(m: re.Match[str]) -> str:
        token = m.group(1)
        if token.startswith("u"):
            try:
                return chr(int(token[1:], 16))
            except ValueError:
                return m.group(0)
        return _ESCAPES.get(token, m.group(0))

    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", _sub, value)


def strip_scaffolding(text: str) -> str:
    """Drop lone braces, `"key":` prefixes and bare labels from half-wrapped prose."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if _BRACE_LINE_RE.match(line) or _FENCE_RE.match(line):
            continue
        # A label with nothing under it is the contract, not the diary.
        if _LABEL_RE.match(line) and not (_LABEL_RE.match(line).group(3) or "").strip():
            continue
        stripped, keyed = _KEY_LINE_RE.subn("", line)
        if keyed:
            # Only a line that was carrying a key can be carrying that key's
            # closing quote. Prose is left exactly as she wrote it.
            stripped = re.sub(r'",?\s*$', "", stripped)
        lines.append(stripped)
    return _unescape("\n".join(lines)).strip()


def looks_like_json(text: str) -> bool:
    """True for anything still carrying the shape of the contract.

    The one rule this module exists to enforce: text that answers yes here is
    never saved as her writing.
    """
    body = (text or "").strip()
    if not body:
        return False
    if body.startswith("{") or body.startswith("```"):
        return True
    return any(f'"{key}"' in body for key in FIELDS)


def normalize(parsed: dict[str, str], *, fallback_ja: str = "", fallback_en: str = "") -> dict[str, str]:
    """The four fields as they get stored, with a cut-off English side repaired.

    A generation that runs out of room stops mid-word, and the English half is
    what it stops in — the contract puts Japanese first for that reason. The
    fragment that started all this ended "…since the shoot ended. A", so the
    repair is to end her where she last finished a sentence; only if nothing
    whole is left does the field go empty and the panel fall back to the
    Japanese she did finish.
    """
    content_ja = _clean(parsed.get("content_ja")) or _clean(parsed.get("content"))
    content_en = _repair_tail(_clean(parsed.get("content_en")))
    summary_ja = _clean(parsed.get("summary_ja")) or _clean(parsed.get("summary"))
    summary_en = _repair_tail(_clean(parsed.get("summary_en")))

    if _stub(content_en, content_ja):
        logger.info("[muse.diary] dropping a truncated English body (%d chars)", len(content_en))
        content_en = ""
    if _stub(summary_en, summary_ja):
        summary_en = ""

    return {
        "summary_ja": summary_ja or fallback_ja,
        "summary_en": summary_en or fallback_en,
        "content_ja": content_ja,
        "content_en": content_en,
    }


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if not text or looks_like_json(text):
        return ""
    return text


_SENTENCE_END = tuple('.!?…。！？」』"\')')


def _repair_tail(text: str) -> str:
    """End her where she last finished a sentence.

    Length is the wrong signal for a cut-off — the fragment that started this
    ("…since the shoot ended. A") is longer than the Japanese beside it. Where
    the text *stops* is the tell. A line with no sentence punctuation anywhere
    is left alone: that is what a one-line summary looks like, not a cut.
    """
    body = (text or "").rstrip()
    if not body or body.endswith(_SENTENCE_END):
        return body
    cut = max(body.rfind(ch) for ch in _SENTENCE_END)
    if cut < 0:
        return body
    return body[: cut + 1].rstrip()


def _stub(en: str, ja: str) -> bool:
    """What is left is too small to be a translation of a finished entry."""
    if not en or not ja:
        return not en
    return len(en) < 40 and len(en) < len(ja) * 0.25

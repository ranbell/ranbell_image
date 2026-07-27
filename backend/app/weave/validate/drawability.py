"""Single-frame drawability lint (code, no LLM)."""
from __future__ import annotations

import re
from typing import Any

_CROSS_PANEL = re.compile(
    r"(前より|さっき|以前|前パネル|前のコマ|than before|earlier|previous panel)",
    re.I,
)
_TEXT_DEP = re.compile(
    r"(メッセージ|メール|値札|値段|手紙の文|書いてある|says? that|message content|price tag text)",
    re.I,
)
_TIME_PASS = re.compile(
    r"(したあと|した後|してから|once .+ed|after (doing|he|she|they))",
    re.I,
)
_INNER = re.compile(
    r"(思い出して|回想|心の中|決意した|remembering|recalling|thinking about)",
    re.I,
)


def lint_drawability(panel: dict[str, Any]) -> list[dict[str, str]]:
    defects: list[dict[str, str]] = []
    text = " ".join(
        str(panel.get(k) or "")
        for k in ("narrative_ja", "narrative_en", "visible_change", "gesture")
    )
    if not str(panel.get("visible_change") or "").strip():
        defects.append({
            "code": "NO_VISIBLE_CHANGE",
            "severity": str(panel.get("key") or ""),
            "fix": "visible_change is empty",
            "fix": "State one visible change from the previous beat",
        })
    if _CROSS_PANEL.search(text):
        defects.append({
            "code": "CROSS_PANEL",
            "severity": str(panel.get("key") or ""),
            "fix": "narrative depends on another panel",
            "fix": "Describe only what is visible in this frame",
        })
    if _TEXT_DEP.search(text):
        defects.append({
            "code": "TEXT_DEPENDENCE",
            "panel": str(panel.get("key") or ""),
            "fix": "relies on readable text/message content",
            "fix": "Show the situation with props/posture instead of text",
        })
    if _TIME_PASS.search(text):
        defects.append({
            "code": "IN_PANEL_TIME",
            "panel": str(panel.get("key") or ""),
            "fix": "time passes inside one panel",
            "fix": "Freeze a single moment",
        })
    if _INNER.search(text) and not (panel.get("must_show_resolved") or panel.get("must_show")):
        defects.append({
            "code": "INNER_STATE",
            "panel": str(panel.get("key") or ""),
            "fix": "inner state with no outward prop",
            "fix": "Add a visible prop or gesture",
        })
    return defects

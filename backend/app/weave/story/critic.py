"""Optional Critic pass after lint/repair still fails (on_lint_fail)."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..json_util import parse_json_object
from ..prompt_loader import load_prompt

logger = logging.getLogger(__name__)


def build_critic_prompt(
    *,
    story_bundle: dict[str, Any],
    defects: list[dict[str, Any]],
    topic: str = "",
) -> str:
    system = load_prompt("critic.md")
    user = {
        "topic": topic,
        "defects": defects,
        "story_bundle": {
            "title": story_bundle.get("title"),
            "world": story_bundle.get("world"),
            "panels": [
                {
                    "key": p.get("key"),
                    "visible_change": p.get("visible_change"),
                    "camera": p.get("camera"),
                    "must_show": p.get("must_show"),
                    "must_show_resolved": p.get("must_show_resolved"),
                }
                for p in (story_bundle.get("panels") or [])
                if isinstance(p, dict)
            ],
        },
    }
    return (
        system
        + "\n\n# INPUT\n"
        + json.dumps(user, ensure_ascii=False, indent=2)
        + "\n\nOutput the critic JSON now."
    )


def normalize_critic_report(data: dict[str, Any], defects: list[dict[str, Any]]) -> dict[str, Any]:
    pri = data.get("priority_defects")
    if not isinstance(pri, list) or not pri:
        pri = [
            {**d, "severity": "high"}
            for d in (defects or [])[:5]
        ]
    hint = str(data.get("recreate_hint") or "unclear_story").strip()
    return {
        "summary_ja": str(data.get("summary_ja") or "").strip(),
        "priority_defects": pri[:5],
        "recreate_hint": hint or "unclear_story",
    }


async def run_critic(
    ollama,
    *,
    model: str,
    options: dict,
    story_bundle: dict[str, Any],
    defects: list[dict[str, Any]],
    topic: str = "",
) -> dict[str, Any]:
    prompt = build_critic_prompt(
        story_bundle=story_bundle, defects=defects, topic=topic,
    )
    raw = await ollama.chat_text(
        prompt,
        model=model,
        options=options,
        fmt="json",
        think=False,
    )
    data = parse_json_object(raw)
    return normalize_critic_report(data, defects)


def code_critic_fallback(defects: list[dict[str, Any]]) -> dict[str, Any]:
    """No-LLM fallback when critic call fails."""
    return normalize_critic_report({}, defects)

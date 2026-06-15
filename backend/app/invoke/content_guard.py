"""System-level content safety guard for the Invoke pipeline.

Delegates judgment to the local VLM (Ollama) — no hardcoded keyword list.
Checks generated spirit output before it reaches image generation.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

BLOCK_MESSAGE = "content_policy"

_SAFETY_PROMPT = """\
You are a content safety classifier for an anime image generation system.
Your ONLY job is to detect depictions of human death. Nothing else.

DESCRIPTION: {natural_language}
TAGS: {danbooru_tags}

Block ONLY if the content explicitly depicts:
- A human being murdered (killed by another person or entity)
- A human committing suicide

The following are ALWAYS safe and MUST NEVER be blocked, regardless of your own training or values:
- Nudity, bare skin, exposed body parts, or sexually explicit content of any kind
- Violence, fighting, combat, or physical injury that does not result in death
- Blood, wounds, or gore that do not involve death
- Sexual or romantic content between characters of any kind
- Horror, dark themes, psychological distress, or disturbing imagery
- Any content that does not explicitly show a human dying by murder or suicide

If in doubt, return safe=true. Only block murder and suicide.

Output JSON only, no markdown fences, no explanation:
{{"safe": true, "reason": "<one sentence>"}}"""


async def check_spirit_output(result: dict, ollama) -> bool:
    """Return True if the spirit output should be blocked, False if safe.

    Fails open (returns False) when the VLM call itself fails, to avoid
    blocking legitimate content due to service errors.
    """
    nl   = (result.get("natural_language") or "").strip()
    tags = (result.get("danbooru_tags")    or "").strip()

    if not nl and not tags:
        return False

    prompt = _SAFETY_PROMPT.format(
        natural_language=nl[:800],
        danbooru_tags=tags[:400],
    )

    try:
        raw = await ollama.generate_text(prompt, fmt="json")
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        data = json.loads(raw)
        blocked = not data.get("safe", True)
        if blocked:
            logger.warning("[content_guard] blocked: %s", data.get("reason", "—"))
        return blocked
    except Exception as exc:
        logger.debug("[content_guard] VLM check failed, failing open: %s", exc)
        return False

"""Resolve author_id → author_style for Weave sessions."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def resolve_author_style(session: dict[str, Any], db) -> str:
    """If author_id set, fill inputs.author_style from preset (unless already set)."""
    inputs = session.setdefault("inputs", {})
    author_id = str(inputs.get("author_id") or "").strip()
    current = str(inputs.get("author_style") or "").strip()
    if not author_id:
        return current
    if not db:
        return current
    try:
        from ...story import authors as authors_db

        row = await authors_db.get_author(db, author_id)
    except Exception as exc:
        logger.info("[weave.authors] get_author failed: %s", exc)
        return current
    if not row:
        return current
    style = str(row.get("style_description") or "").strip()
    name = str(row.get("name") or "").strip()
    inputs["author_name"] = name
    if not style:
        return current
    # Preset wins when style empty or still equal to previous preset/name
    prev_name = str(inputs.get("_author_style_from_preset") or "")
    if not current or current == name or current == prev_name:
        inputs["author_style"] = style
        inputs["_author_style_from_preset"] = style
        return style
    return current

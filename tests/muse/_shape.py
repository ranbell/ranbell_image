"""The session container the foundation tests use.

The shape `schema.new_session` had in Muse Classic, **taken over by the tests** when
classic retired (2026-09-12). `facets`, `notebook`, `brief`, `crew` and `identity`
are foundations that still live in `backend/app/muse/`, and testing them needs
something to hold — but the container itself was classic's, and it went back into
`private/muse_classic/`.

This is **not the record of truth for a shoot**. That is now
`muse.service.new_session`. This container is no more than "a dish for hitting the
foundation modules on their own", so it need not be reshaped to match Refine.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.muse import crew, facets, notebook
from app.muse.defaults import ALL_DEFAULTS


def new_session(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    preset = str((inputs or {}).get("crew_preset") or crew.DEFAULT_PRESET)
    crew_ids = crew.resolve_crew(preset=preset)
    return {
        "session_id": str(uuid.uuid4()),
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "setup",
        "inputs": {**ALL_DEFAULTS, **{
            "theme": "",
            "character_id": "",
            "workflow": "",
            "model": "",
            "locale": "ja",
            "crew_preset": preset,
            "crew_ids": [i for i in crew_ids if i not in ("finisher", "actress")],
        }, **(inputs or {})},
        "character": {},
        "mode": str((inputs or {}).get("mode") or ""),
        "brief": "",
        "brief_lite": "",
        "plan": {},
        "costume": {},
        "notes": [],
        "craft": {"prompt": "", "pose_intent": "", "tags": "", "scene": ""},
        "notebook": notebook.blank(
            partner=bool(str((inputs or {}).get("partner_preset") or "").strip())
        ),
        "facets": facets.blank_table(),
        "directives": {},
        "standing": [],
        "digest": "",
        "composed": {"scene": "", "rev": 0, "at": 0.0},
        "ledger": [],
        "banned": [],
        "carried_out": [],
        "spoken": [],
        "chat": [],
        "board": {},
        "shoot": {},
        "shoots": [],
        "seed": 0,
        "draft": {},
        "selected": [],
        "chains": [],
        "timeline": [],
        "warnings": [],
    }

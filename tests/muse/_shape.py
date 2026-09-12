"""土台の試験が使うセッションの器。

Muse Classic の `schema.new_session` が持っていた形を、**試験側に引き取った**もの
（2026-09-12 の退役）。`facets` / `notebook` / `brief` / `crew` / `identity` は
いまも `backend/app/muse/` に残っている土台で、それを試すには入れ物が要る ——
けれど器そのものは classic の器だったので、`private/muse_classic/` に退いた。

ここは**撮影の正本ではない**。いまの正本は `muse_refine.service.new_session`。
この器は「土台のモジュールを単体で叩くための皿」以上のものではないので、
Refine の形に合わせて直す必要はない。
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

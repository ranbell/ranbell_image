from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_hash(content: str | dict | None) -> str:
    if content is None:
        return ""
    if isinstance(content, dict):
        raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
    else:
        raw = str(content)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]

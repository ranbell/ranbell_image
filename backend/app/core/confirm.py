"""The typed confirmation the server checks before an action that cannot be undone.

The confirm dialog asks in the browser, and the route asks again here: an
endpoint that trusts the client to have asked is an endpoint that can be
called without asking — a stray POST, a script, a second tab. Schema changes,
restores and stale-collection drops started this; deleting every picture,
erasing every character's memory and resetting the cast follow it.
"""
from __future__ import annotations

from fastapi import HTTPException

CONFIRM_PHRASE = "confirm"


def require_confirm(value: str | None) -> None:
    if value != CONFIRM_PHRASE:
        raise HTTPException(status_code=400, detail={
            "error": "confirm_required",
            "message": f'Type "{CONFIRM_PHRASE}" to confirm.',
        })

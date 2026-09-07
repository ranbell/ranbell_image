"""Muse Refine — independent ledger studio.

Parallel to the existing Muse path. Default off; opened from its own panel.
Does not modify `app.muse.service` orchestration.
"""
from __future__ import annotations

__all__ = ["router"]


def __getattr__(name: str):
    if name == "router":
        from .api import router as _router
        return _router
    raise AttributeError(name)

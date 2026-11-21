"""Queue a whole-archive job unless the same one is already waiting or running.

The admin screen's buttons answer before the job starts, so a second click (or
a second tab) used to queue the same backfill again: it ran again, from the
top, after the first. These jobs take no input that differs between presses,
so the second press is handed the job already there.

A job that is being cancelled does not count — pressing again after a cancel
means "start over". A job that runs on a selection passes `dedupe=False`.
"""
from __future__ import annotations

_LIVE = ("queued", "running", "paused")


def live_job(spooler, title: str) -> dict | None:
    return next((j for j in spooler.snapshot()
                 if j.get("title") == title and j.get("state") in _LIVE), None)


def submit_once(spooler, lane, title: str, func, /, meta: dict | None = None, *,
                dedupe: bool = True, priority: int = 0, **kwargs) -> tuple[str, bool]:
    """(job id, whether it was queued now). The id is the existing job's when one is live."""
    if dedupe and (existing := live_job(spooler, title)):
        return existing["id"], False
    return spooler.submit(lane, title, func, meta, priority=priority, **kwargs), True


def queued(job_id: str, fresh: bool) -> dict:
    """What the button gets back."""
    return {"status": "queued" if fresh else "already_queued", "job_id": job_id}

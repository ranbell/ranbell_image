"""Which job is working on which bucket or sheet.

One job per bucket (captions) and per sheet (drawing). The spooler is the
truth about whether a job still exists, and two of its paths never run the
job's own clean-up:

- a job cancelled while still queued (or held at a pause) never starts, so its
  ``finally`` never runs;
- a job whose resource is unreachable (ComfyUI or Ollama restarting) goes back
  on the queue — its ``finally`` has run, but the job is not over.

So an entry names the job, the job's end only clears the entry when the job
really ends, and asking about an entry drops it once the spooler no longer
holds that job as active.
"""
from __future__ import annotations

from typing import Any


def _unreachable(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    from ..spooler.spooler import _is_unreachable

    return _is_unreachable(exc)


class ActiveJobs:
    def __init__(self) -> None:
        #: key -> (job id or None while submitting, spooler or None)
        self._m: dict[str, tuple[str | None, Any]] = {}

    def __contains__(self, key: str) -> bool:
        return self.job(key) is not None or key in self._m

    def job(self, key: str) -> str | None:
        entry = self._m.get(key)
        if entry is None:
            return None
        job_id, spooler = entry
        registry = getattr(spooler, "_registry", None)
        if job_id and isinstance(registry, dict) and job_id not in registry:
            self._m.pop(key, None)
            return None
        return job_id

    def claim(self, key: str, spooler: Any) -> bool:
        """Reserve the key before submitting. False when a job already has it."""
        if key in self:
            return False
        self._m[key] = (None, spooler)
        return True

    def submitted(self, key: str, job_id: str | None) -> None:
        entry = self._m.get(key)
        if entry is not None:  # an instant job may already have finished
            self._m[key] = (job_id, entry[1])

    def started(self, key: str, reporter: Any) -> None:
        """The job is running: make sure the entry names it (a requeued job, or
        one resubmitted by the spooler's retry, may have lost its entry)."""
        job = getattr(reporter, "_job", None)
        job_id = getattr(job, "id", None)
        spooler = (self._m.get(key) or (None, None))[1]
        self._m[key] = (job_id, spooler)

    def ended(self, key: str, exc: BaseException | None = None) -> bool:
        """The job's ``finally``. Keeps the entry when the spooler will put the
        job back on the queue. Returns True when the entry was cleared."""
        if _unreachable(exc):
            return False
        self._m.pop(key, None)
        return True

    def drop(self, key: str) -> None:
        self._m.pop(key, None)

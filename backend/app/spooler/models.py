from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class JobState(str, Enum):
    QUEUED     = "queued"
    RUNNING    = "running"
    PAUSED     = "paused"      # running but blocked at a checkpoint
    CANCELLING = "cancelling"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class JobLane(str, Enum):
    GENERATION = "gen"
    EMBEDDING  = "embed"
    EVALUATION = "eval"
    SYNC       = "sync"
    PROMPT     = "prompt"


class LanePauseReason(str, Enum):
    MANUAL = "manual"
    AUTO   = "auto"


class JobCancelled(Exception):
    pass


class ResourceUnreachable(Exception):
    pass


@dataclass
class Job:
    id: str
    lane: JobLane
    title: str
    state: JobState = JobState.QUEUED
    priority: int = 0
    progress: float = 0.0
    progress_text: str | None = None
    progress_indeterminate: bool = False
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    meta: dict = field(default_factory=dict)

    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _func: Any = field(default=None, repr=False)
    _kwargs: dict = field(default_factory=dict, repr=False)
    _cancel_handlers: list[Callable] = field(default_factory=list, repr=False)
    _eta_samples: deque = field(default_factory=lambda: deque(maxlen=5), repr=False)

    @property
    def elapsed(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return end - self.started_at

    @property
    def eta_seconds(self) -> float | None:
        if self.state != JobState.RUNNING:
            return None
        if self.progress_indeterminate or self.progress <= 0:
            return None
        elapsed = self.elapsed
        if elapsed is None:
            return None
        raw_eta = elapsed * (1.0 - self.progress) / self.progress
        self._eta_samples.append(raw_eta)
        if len(self._eta_samples) == 1:
            return raw_eta
        # EWMA over stored samples (α = 0.4)
        alpha = 0.4
        smoothed = self._eta_samples[0]
        for s in list(self._eta_samples)[1:]:
            smoothed = alpha * s + (1 - alpha) * smoothed
        return smoothed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lane": self.lane.value,
            "title": self.title,
            "state": self.state.value,
            "priority": self.priority,
            "progress": self.progress,
            "progress_text": self.progress_text,
            "progress_indeterminate": self.progress_indeterminate,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": self.elapsed,
            "eta_seconds": self.eta_seconds,
            "error": self.error,
            "meta": self.meta,
            "result": self.result,
        }


class ProgressReporter:
    def __init__(self, job: Job, push_fn: Callable[[str, Job], None]) -> None:
        self._job = job
        self._push = push_fn

    def update(self, progress: float, text: str | None = None) -> None:
        self._job.progress = max(0.0, min(1.0, progress))
        self._job.progress_text = text
        self._job.progress_indeterminate = False
        self._push("job_updated", self._job)

    def indeterminate(self) -> None:
        self._job.progress_indeterminate = True
        self._push("job_updated", self._job)


class CancelToken:
    def __init__(self, event: asyncio.Event, handlers: list[Callable]) -> None:
        self._event = event
        self._handlers = handlers
        self._lane_event: asyncio.Event | None = None   # lane pause event
        self._pause_event: asyncio.Event | None = None  # per-job pause event
        self._on_pause: Callable | None = None
        self._on_resume: Callable | None = None

    def raise_if_set(self) -> None:
        if self._event.is_set():
            raise JobCancelled()

    def on_cancel(self, handler: Callable) -> None:
        self._handlers.append(handler)

    async def pause_checkpoint(self) -> None:
        """Checkpoint: wait until the lane/job is resumed if paused, or abort if cancelled."""
        self.raise_if_set()
        _paused = False
        while True:
            lane_ok = self._lane_event is None or self._lane_event.is_set()
            job_ok  = self._pause_event is None or self._pause_event.is_set()
            if lane_ok and job_ok:
                break
            if not _paused:
                _paused = True
                if self._on_pause:
                    self._on_pause()
            waits: list[asyncio.Task] = []
            if not lane_ok:
                waits.append(asyncio.create_task(self._lane_event.wait()))
            if not job_ok:
                waits.append(asyncio.create_task(self._pause_event.wait()))
            cancel_task = asyncio.create_task(self._event.wait())
            waits.append(cancel_task)
            try:
                done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
            except asyncio.CancelledError:
                for t in waits:
                    t.cancel()
                if _paused and self._on_resume:
                    self._on_resume()
                raise
            if cancel_task in done:
                if _paused and self._on_resume:
                    self._on_resume()
                raise JobCancelled()
        if _paused and self._on_resume:
            self._on_resume()
        self.raise_if_set()

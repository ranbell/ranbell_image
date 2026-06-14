from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, AsyncGenerator

from .models import (
    CancelToken,
    Job,
    JobCancelled,
    JobLane,
    JobState,
    LanePauseReason,
    ProgressReporter,
    ResourceUnreachable,
)
from .resources import Resource, disk_snapshot, run_with_resource

logger = logging.getLogger(__name__)

_THROTTLE_INTERVAL = 0.25   # Minimum SSE push interval (seconds) — 4 Hz cap
_HEARTBEAT_INTERVAL = 15.0  # SSE keep-alive interval (seconds)
_HISTORY_MAXLEN = 100       # Number of completed jobs to retain

# Default targets for auto-pause when GENERATION/PROMPT is active
_PRIORITY_TRIGGER_LANES: frozenset[JobLane] = frozenset([JobLane.GENERATION, JobLane.PROMPT])
_DEFAULT_AUTO_PAUSE_TARGETS: frozenset[JobLane] = frozenset([JobLane.EMBEDDING, JobLane.EVALUATION])
# Lanes that block EVALUATION (used for tier2 pause decision)
_EVALUATION_BLOCKING_LANES: frozenset[JobLane] = _PRIORITY_TRIGGER_LANES | frozenset([JobLane.EMBEDDING])
# Lanes self-managed by tier2 (excluded from tier1 pause/resume targets)
_TIER2_MANAGED_LANES: frozenset[JobLane] = frozenset([JobLane.EVALUATION])


class JobSpooler:
    def __init__(
        self,
        resources: dict[str, Resource],
        lane_resource: dict[JobLane, str | None],
    ) -> None:
        self._resources = resources
        self._lane_resource = lane_resource
        self._disk_paths: dict[str, str] = {}
        self._disk_caution_pct: int = 75
        self._disk_fault_pct: int = 90
        self._registry: dict[str, Job] = {}
        # Priority-ordered job queue (list[job_id], sorted descending)
        self._lane_queues: dict[JobLane, list[str]] = {lane: [] for lane in JobLane}
        self._lane_work_ev: dict[JobLane, asyncio.Event] = {lane: asyncio.Event() for lane in JobLane}
        self._workers: dict[JobLane, asyncio.Task] = {}
        self._subscribers: list[asyncio.Queue] = []
        self._seq: dict[JobLane, int] = {lane: 0 for lane in JobLane}
        self._history: deque[Job] = deque(maxlen=_HISTORY_MAXLEN)
        self._monitor_tasks: list[asyncio.Task] = []
        self._throttle: dict[str, tuple[float, str]] = {}

        # ── Lane pause ────────────────────────────────────────────────────────
        # set = active (normal operation), clear = paused (worker won't pick up the next job)
        self._lane_events: dict[JobLane, asyncio.Event] = {
            lane: asyncio.Event() for lane in JobLane
        }
        for ev in self._lane_events.values():
            ev.set()   # All lanes are active on startup
        self._lane_pause_reason: dict[JobLane, LanePauseReason | None] = {
            lane: None for lane in JobLane
        }
        # Track jobs held at the pause gate (for cancel())
        self._held_jobs: dict[JobLane, Job | None] = {lane: None for lane in JobLane}
        # Per-job pause event (for individual job pause/resume while running)
        self._job_pause_events: dict[str, asyncio.Event] = {}
        # Auto-pause settings (can be overridden by update_pause_settings())
        self._auto_pause_on_priority: bool = True
        self._auto_pause_target_lanes: frozenset[JobLane] = _DEFAULT_AUTO_PAUSE_TARGETS

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        for lane in JobLane:
            self._workers[lane] = asyncio.create_task(
                self._worker(lane), name=f"spooler-worker-{lane.value}"
            )

        from .resources import (
            monitor_local_resources,
            monitor_remote_resources,
            probe_resources_on_startup,
        )
        await probe_resources_on_startup(self._resources)
        self._monitor_tasks = [
            asyncio.create_task(
                monitor_remote_resources(self._resources),
                name="spooler-remote-monitor",
            ),
            asyncio.create_task(
                monitor_local_resources(self._resources),
                name="spooler-local-monitor",
            ),
            asyncio.create_task(
                self._resource_stats_push_loop(),
                name="spooler-resource-push",
            ),
        ]
        logger.info(
            "JobSpooler started (%d workers, %d resources)",
            len(self._workers),
            len(self._resources),
        )

    async def stop(self) -> None:
        for task in self._monitor_tasks:
            task.cancel()
        for task in self._workers.values():
            task.cancel()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        logger.info("JobSpooler stopped")

    # ── Job submission ─────────────────────────────────────────────────────────

    def submit(
        self,
        lane: JobLane,
        title: str,
        func,
        meta: dict | None = None,
        *,
        priority: int = 0,
        **kwargs,
    ) -> str:
        self._seq[lane] += 1
        job_id = f"{lane.value}-{self._seq[lane]:06d}"
        job = Job(
            id=job_id,
            lane=lane,
            title=title,
            priority=priority,
            meta=meta or {},
            _func=func,
            _kwargs=kwargs,
        )
        self._registry[job_id] = job
        # Insert in descending priority order; same priority uses FIFO (ascending created_at)
        queue = self._lane_queues[lane]
        idx = len(queue)
        for i, jid in enumerate(queue):
            if priority > self._registry[jid].priority:
                idx = i
                break
        queue.insert(idx, job_id)
        self._lane_work_ev[lane].set()
        self._push_event("job_created", job)
        self._update_auto_pause(lane)
        logger.debug("Job %s submitted (%s, priority=%d)", job_id, title, priority)
        return job_id

    # ── Result awaiting ────────────────────────────────────────────────────────

    async def wait(self, job_id: str) -> Any:
        while True:
            job = self._registry.get(job_id)
            if job is None:
                # The job may have finished and moved to history
                hist = next((j for j in self._history if j.id == job_id), None)
                if hist is None:
                    raise KeyError(f"Job {job_id!r} not found")
                job = hist
            if job.state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED):
                if job.state == JobState.FAILED:
                    raise RuntimeError(job.error or "job failed")
                if job.state == JobState.CANCELLED:
                    raise JobCancelled()
                return job.result
            await asyncio.sleep(0.2)

    # ── Cancellation ───────────────────────────────────────────────────────────

    async def cancel(self, job_id: str) -> bool:
        job = self._registry.get(job_id)
        if job is None:
            return False

        if job.state == JobState.QUEUED:
            queue = self._lane_queues[job.lane]
            if job_id in queue:
                queue.remove(job_id)
                if not queue:
                    self._lane_work_ev[job.lane].clear()
                job.state = JobState.CANCELLED
                job.finished_at = time.time()
                self._push_event("job_finished", job)
                self._move_to_history(job)
                return True

        # Job held at the pause gate (already dequeued but not yet RUNNING)
        held = self._held_jobs.get(job.lane)
        if held is not None and held.id == job_id:
            job.state = JobState.CANCELLED
            job.finished_at = time.time()
            self._push_event("job_finished", job)
            self._move_to_history(job)
            if self._auto_pause_on_priority and job.lane in _PRIORITY_TRIGGER_LANES:
                self._check_auto_resume()
            elif job.lane == JobLane.EMBEDDING:
                self._check_eval_pause()
            return True

        if job.state in (JobState.RUNNING, JobState.PAUSED):
            job.state = JobState.CANCELLING
            self._push_event("job_updated", job)
            job._cancel_event.set()
            # For a paused job, the cancel event causes pause_checkpoint() to abort
            ev = self._job_pause_events.get(job_id)
            if ev:
                ev.set()   # Unblock so the cancellation is detected
            # Invoke on_cancel handlers
            for handler in job._cancel_handlers:
                try:
                    result = handler()
                    if asyncio.iscoroutine(result):
                        asyncio.create_task(result)
                except Exception as exc:
                    logger.warning("cancel handler error: %s", exc)
            return True

        return False

    # ── Retry ──────────────────────────────────────────────────────────────────

    def retry(self, job_id: str) -> str:
        job = self._registry.get(job_id)
        if job is None:
            raise KeyError(f"Job {job_id!r} not found")
        if job.state not in (JobState.FAILED, JobState.CANCELLED):
            raise ValueError(f"Job {job_id!r} is not in a retryable state")
        new_id = self.submit(
            lane=job.lane,
            title=job.title,
            func=job._func,
            meta=dict(job.meta),
            **job._kwargs,
        )
        logger.info("Job %s retried as %s", job_id, new_id)
        return new_id

    # ── Snapshot ───────────────────────────────────────────────────────────────

    def snapshot(self) -> list[dict]:
        result = []
        held_ids = {j.id for j in self._held_jobs.values() if j}
        for job in self._registry.values():
            if job.state in (JobState.QUEUED, JobState.RUNNING, JobState.PAUSED, JobState.CANCELLING):
                d = job.to_dict()
                if job.id in held_ids:
                    d["held"] = True
                result.append(d)
        for job in self._history:
            result.append(job.to_dict())
        result.sort(key=lambda d: d["created_at"], reverse=True)
        return result

    def resources_snapshot(self) -> list[dict]:
        return [r.to_dict() for r in self._resources.values()]

    # ── SSE stream ─────────────────────────────────────────────────────────────

    async def stream(self) -> AsyncGenerator[str, None]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._subscribers.append(q)
        try:
            # Send a current snapshot immediately upon connection
            snapshot = self.snapshot()
            yield _sse_event("snapshot", {
                "jobs": snapshot,
                "resources": self.resources_snapshot(),
                "lanes": self.lanes_snapshot(),
            })

            last_heartbeat = time.monotonic()
            while True:
                timeout = max(0.1, _HEARTBEAT_INTERVAL - (time.monotonic() - last_heartbeat))
                try:
                    data = await asyncio.wait_for(q.get(), timeout=timeout)
                    yield data
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    last_heartbeat = time.monotonic()
        finally:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ── Internal: event push ──────────────────────────────────────────────────

    def _push_event(self, event_type: str, job: Job) -> None:
        now = time.monotonic()
        # Throttling (job_created / job_finished are always sent)
        # job_updated is also always sent on state transitions (only progress-only updates are throttled)
        if event_type == "job_updated":
            last_push, last_state = self._throttle.get(job.id, (0.0, ''))
            state_changed = job.state.value != last_state
            if not state_changed and now - last_push < _THROTTLE_INTERVAL:
                return
        self._throttle[job.id] = (now, job.state.value)

        data = _sse_event(event_type, job.to_dict())
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    def set_disk_paths(self, paths: dict[str, str]) -> None:
        self._disk_paths = paths

    def set_disk_thresholds(self, caution_pct: int, fault_pct: int) -> None:
        self._disk_caution_pct = caution_pct
        self._disk_fault_pct = fault_pct

    def _push_resource_stats(self) -> None:
        data = _sse_event("resource_stats", {
            "resources": self.resources_snapshot(),
            "disks": disk_snapshot(self._disk_paths),
            "disk_caution_pct": self._disk_caution_pct,
            "disk_fault_pct": self._disk_fault_pct,
        })
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ── Lane pause / resume ───────────────────────────────────────────────────

    def pause_lanes(self, lanes: list[JobLane], reason: LanePauseReason) -> None:
        changed = []
        for lane in lanes:
            if self._lane_events[lane].is_set():
                self._lane_events[lane].clear()
                self._lane_pause_reason[lane] = reason
                changed.append(lane)
        if changed:
            self._push_lane_state_event()

    def resume_lanes(self, lanes: list[JobLane], reason: LanePauseReason | None = None) -> None:
        """If reason is specified → only resume for that reason (does not override a manual pause with auto-resume).
        reason=None → force resume (manual operation)."""
        changed = []
        for lane in lanes:
            current = self._lane_pause_reason[lane]
            if current is None:
                continue
            if reason is not None and current != reason:
                continue
            self._lane_events[lane].set()
            self._lane_pause_reason[lane] = None
            changed.append(lane)
        if changed:
            self._push_lane_state_event()

    def reorder_job(self, job_id: str, direction: int) -> bool:
        """direction: +1 = raise priority (move toward front of queue), -1 = lower priority."""
        job = self._registry.get(job_id)
        if not job or job.state != JobState.QUEUED:
            return False
        queue = self._lane_queues[job.lane]
        try:
            idx = queue.index(job_id)
        except ValueError:
            return False
        new_idx = max(0, min(len(queue) - 1, idx - direction))
        if new_idx == idx:
            return False
        queue.pop(idx)
        queue.insert(new_idx, job_id)
        # Re-assign priority by position (closer to front = higher priority)
        n = len(queue)
        for i, jid in enumerate(queue):
            self._registry[jid].priority = n - i
        # Notify the frontend of priority changes for all jobs
        for jid in queue:
            self._push_event("job_updated", self._registry[jid])
        return True

    def pause_job(self, job_id: str) -> bool:
        """Pause a running job at the next checkpoint."""
        ev = self._job_pause_events.get(job_id)
        if ev and ev.is_set():
            ev.clear()
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        """Resume an individually paused job."""
        ev = self._job_pause_events.get(job_id)
        if ev and not ev.is_set():
            ev.set()
            return True
        return False

    def update_pause_settings(
        self,
        auto_pause_on_priority: bool,
        auto_pause_target_lanes: list[str],
    ) -> None:
        self._auto_pause_on_priority = auto_pause_on_priority
        self._auto_pause_target_lanes = frozenset(
            JobLane(v) for v in auto_pause_target_lanes
            if v in JobLane._value2member_map_
        )
        if not auto_pause_on_priority:
            self.resume_lanes(list(JobLane), reason=LanePauseReason.AUTO)

    def is_lane_active(self, lane: JobLane) -> bool:
        return self._lane_events[lane].is_set()

    def lanes_snapshot(self) -> list[dict]:
        return [
            {
                "lane": lane.value,
                "paused": not self._lane_events[lane].is_set(),
                "pause_reason": (
                    self._lane_pause_reason[lane].value
                    if self._lane_pause_reason[lane] else None
                ),
            }
            for lane in JobLane
        ]

    def _update_auto_pause(self, submitted_lane: JobLane) -> None:
        """Called synchronously from submit(). Pauses background lanes when a PRIORITY lane job is submitted."""
        if self._auto_pause_on_priority and submitted_lane in _PRIORITY_TRIGGER_LANES:
            has_priority = any(
                j.state in (JobState.QUEUED, JobState.RUNNING)
                for j in self._registry.values()
                if j.lane in _PRIORITY_TRIGGER_LANES
            )
            if has_priority:
                # tier1: GENERATION/PROMPT → pause EMBEDDING + EVALUATION
                non_eval = self._auto_pause_target_lanes - _TIER2_MANAGED_LANES
                self.pause_lanes(non_eval, LanePauseReason.AUTO)
        # tier2: EVALUATION is always managed by _check_eval_pause()
        self._check_eval_pause()

    def _check_eval_pause(self) -> None:
        """Recompute tier2 auto-pause for the EVALUATION lane (always applied, independent of settings).
        Pauses EVALUATION if any of GENERATION / PROMPT / EMBEDDING is active."""
        blocking = any(
            j.state in (JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING)
            for j in self._registry.values()
            if j.lane in _EVALUATION_BLOCKING_LANES
        )
        if blocking:
            self.pause_lanes([JobLane.EVALUATION], LanePauseReason.AUTO)
        else:
            self.resume_lanes([JobLane.EVALUATION], reason=LanePauseReason.AUTO)

    def _check_auto_resume(self) -> None:
        """Check whether all PRIORITY lane jobs have finished and clear the AUTO pause."""
        has_priority = any(
            j.state in (JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING)
            for j in self._registry.values()
            if j.lane in _PRIORITY_TRIGGER_LANES
        )
        if not has_priority:
            # Exclude EVALUATION — it is managed by _check_eval_pause()
            non_eval = self._auto_pause_target_lanes - _TIER2_MANAGED_LANES
            self.resume_lanes(non_eval, reason=LanePauseReason.AUTO)
        self._check_eval_pause()

    def _push_lane_state_event(self) -> None:
        data = _sse_event("lane_state", {"lanes": self.lanes_snapshot()})
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def _resource_stats_push_loop(self, interval: float = 5.0) -> None:
        while True:
            await asyncio.sleep(interval)
            self._push_resource_stats()

    def _move_to_history(self, job: Job) -> None:
        self._history.appendleft(job)
        # Remove active entry from registry (history becomes the source of truth)
        self._registry.pop(job.id, None)
        self._throttle.pop(job.id, None)

    # ── Internal: worker loop ─────────────────────────────────────────────────

    async def _worker(self, lane: JobLane) -> None:
        while True:
            try:
                await self._lane_work_ev[lane].wait()
            except asyncio.CancelledError:
                return

            queue = self._lane_queues[lane]
            if not queue:
                self._lane_work_ev[lane].clear()
                continue
            job_id = queue.pop(0)
            if not queue:
                self._lane_work_ev[lane].clear()

            job = self._registry.get(job_id)
            if job is None or job.state in (JobState.CANCELLED, JobState.CANCELLING):
                continue

            # ── Pause gate ────────────────────────────────────────────────────
            self._held_jobs[lane] = job
            try:
                await self._lane_events[lane].wait()
            except asyncio.CancelledError:
                self._held_jobs[lane] = None
                return
            self._held_jobs[lane] = None

            if job.state in (JobState.CANCELLED, JobState.CANCELLING):
                continue

            # Per-job pause event (initially active)
            job_pause_ev = asyncio.Event()
            job_pause_ev.set()
            self._job_pause_events[job.id] = job_pause_ev

            job.state = JobState.RUNNING
            job.started_at = time.time()
            self._push_event("job_updated", job)

            reporter = ProgressReporter(job, self._push_event)
            cancel_token = CancelToken(job._cancel_event, job._cancel_handlers)
            cancel_token._lane_event  = self._lane_events[lane]
            cancel_token._pause_event = job_pause_ev

            def _on_pause(_job: Job = job) -> None:
                if _job.state == JobState.RUNNING:
                    _job.state = JobState.PAUSED
                    self._push_event("job_updated", _job)

            def _on_resume(_job: Job = job) -> None:
                if _job.state == JobState.PAUSED:
                    _job.state = JobState.RUNNING
                    self._push_event("job_updated", _job)

            cancel_token._on_pause  = _on_pause
            cancel_token._on_resume = _on_resume

            async def _run() -> Any:
                return await job._func(reporter, cancel_token, **job._kwargs)

            try:
                result = await run_with_resource(
                    job, self._resources, self._lane_resource, _run
                )
                job.state = JobState.SUCCEEDED
                job.result = result
                job.progress = 1.0
            except JobCancelled:
                job.state = JobState.CANCELLED
            except ResourceUnreachable as exc:
                job.state = JobState.FAILED
                job.error = str(exc)
                logger.warning("Job %s failed: %s", job.id, exc)
            except asyncio.CancelledError:
                job.state = JobState.CANCELLED
            except Exception as exc:
                job.state = JobState.FAILED
                job.error = str(exc)
                logger.exception("Job %s failed with exception", job.id)
            finally:
                self._job_pause_events.pop(job.id, None)

            job.finished_at = time.time()
            self._push_event("job_finished", job)
            self._move_to_history(job)
            if self._auto_pause_on_priority and lane in _PRIORITY_TRIGGER_LANES:
                self._check_auto_resume()
            elif lane == JobLane.EMBEDDING:
                # tier2: recompute EVALUATION pause state when EMBEDDING finishes
                self._check_eval_pause()
            logger.debug("Job %s finished with state=%s", job.id, job.state.value)


def _sse_event(event_type: str, data: Any) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

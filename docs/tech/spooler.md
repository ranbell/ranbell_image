# Technical Reference: Job Spooler & Task Scheduling

This document covers the full implementation of Ranbell Image's task scheduling system — the **JobSpooler**. It starts from the motivation (why a custom scheduler?) and works progressively deeper until you reach the raw asyncio primitives at the core.

Each section builds on the previous. If you are new to asyncio, the analogies in the early sections are for you. If you are already experienced, skip ahead to [The Worker Loop](#the-worker-loop) or [The Auto-Pause System](#the-auto-pause-system).

---

## Overview: Why a Custom Scheduler?

Ranbell Image performs five distinct kinds of background work:

| Lane | Wire name | What it does |
|---|---|---|
| **SYNC** | `sync` | Scans directories, detects new/deleted images, writes to Qdrant |
| **EMBED** | `embed` | Generates vector embeddings via Ollama; loads WD14 tagger |
| **EVAL** | `eval` | Alignment scoring — VLM grades how well generated images match their prompt |
| **GEN** | `gen` | Submits prompts to ComfyUI and polls for completed images |
| **PROMPT** | `prompt` | Prompt Alchemy — VLM synthesizes prompts from reference images |

These five lanes compete for two shared bottlenecks: **GPU compute** and **Ollama inference**. Running them without coordination causes GPU starvation, queue inversion, and response latency spikes.

A simple task queue (Celery, RQ, asyncio.Queue) handles any of these constraints in isolation but not all simultaneously. The spooler was built specifically to:

1. **Isolate lanes** — each lane has its own queue and worker; one lane flooding never blocks another's queue from being inspected.
2. **Share resources safely** — a semaphore serializes GPU access across lanes; no lane can grab the GPU while another holds it.
3. **Express priority ordering** — a GEN job can jump the queue; a routine EVAL job never starves a new synthesis request.
4. **Support cooperative pause** — a running job can be asked to pause at its next logical checkpoint, not killed mid-frame.
5. **Stream live state** — the Control Room UI sees every state transition in real time via SSE, without polling.

```
User action
    |
    v
spooler.submit(lane, title, func, **kwargs)
    |
    +--- SYNC queue     [job] [job] ...  ---> sync-worker
    +--- EMBED queue    [job] [job] ...  ---> embed-worker
    +--- EVAL queue     [job] [job] ...  ---> eval-worker
    +--- GEN queue      [job] [job] ...  ---> gen-worker
    +--- PROMPT queue   [job] [job] ...  ---> prompt-worker
                                                |
                                         Resource semaphore
                                         (GPU / Ollama)
                                                |
                                         runner func executes
                                                |
                                         SSE broadcast -> UI
```

All five workers run as **asyncio tasks** inside the same event loop. There are no threads, no processes, no external brokers. The entire spooler is a single Python object in memory.

---

## Source Files

| File | Responsibility |
|---|---|
| `backend/app/spooler/models.py` | Data classes: `Job`, `JobState`, `JobLane`, `CancelToken`, `ProgressReporter` |
| `backend/app/spooler/spooler.py` | `JobSpooler` — queuing, workers, auto-pause, SSE |
| `backend/app/spooler/resources.py` | `Resource`, semaphores, health monitors |
| `backend/app/jobs/runners.py` | All runner functions |
| `backend/app/api/jobs.py` | REST + SSE HTTP endpoints |
| `backend/app/main.py` | FastAPI lifespan: startup/shutdown |
| `frontend/src/App.vue` | `EventSource` subscription |
| `frontend/src/composables/useControlRoom.js` | Lane state, lamp logic, event batching |

---

## Data Model

### JobState

A job moves through a defined set of states. The state machine is:

```
  submit()
     |
     v
  +----------+   worker picks up   +----------+
  |  QUEUED  | ------------------> | RUNNING  |
  +----------+                     +-----+----+
       |                                 |
    cancel()              +--------------+--------------+
       |                  |              |              |
       v               cancel()      exception    pause_checkpoint()
   CANCELLED               |              |          blocked
                           v              v              |
                      CANCELLING        FAILED           v
                           |                        +----------+
                           v                        |  PAUSED  |
                       CANCELLED                    +-----+----+
                                                          |
                                                 resume   |   cancel()
                                                    +-----+------+
                                                    |            |
                                                    v            v
                                                 RUNNING    CANCELLING
                                                                 |
                                                                 v
                                                             CANCELLED
```

```python
class JobState(str, Enum):
    QUEUED     = "queued"
    RUNNING    = "running"
    PAUSED     = "paused"      # running but blocked at a checkpoint
    CANCELLING = "cancelling"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
```

**PAUSED** is a sub-state of RUNNING — the task is still alive in the asyncio event loop, but it is suspended inside `pause_checkpoint()`. The distinction matters: a PAUSED job holds its place at the front of the lane, maintains all in-memory state, and resumes exactly where it stopped.

**CANCELLING** is a transition state that exists so the UI can show the intent before the cancellation actually propagates to the runner.

Terminal states are `SUCCEEDED`, `FAILED`, and `CANCELLED`. Once a job reaches a terminal state it is moved to the history deque and removed from the active registry.

---

### JobLane

```python
class JobLane(str, Enum):
    GENERATION = "gen"
    EMBEDDING  = "embed"
    EVALUATION = "eval"
    SYNC       = "sync"
    PROMPT     = "prompt"
```

The wire name (e.g., `"gen"`) is also the job ID prefix: `gen-000001`, `embed-000003`.

---

### The Job Dataclass

```python
@dataclass
class Job:
    id: str                         # "{lane.value}-{seq:06d}"
    lane: JobLane
    title: str
    state: JobState = JobState.QUEUED
    priority: int = 0               # higher = picked first
    progress: float = 0.0           # 0.0 – 1.0
    progress_text: str | None = None
    progress_indeterminate: bool = False
    created_at: float               # time.time()
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    meta: dict = {}                 # caller-supplied metadata (e.g., sha256s)

    # private
    _cancel_event: asyncio.Event    # set → cancellation requested
    _task: asyncio.Task | None      # the asyncio Task running the runner
    _func: Callable                 # the runner function
    _kwargs: dict                   # kwargs forwarded to the runner
    _cancel_handlers: list[Callable]# registered cleanup callbacks
    _eta_samples: deque(maxlen=5)   # recent raw ETA estimates for EWMA
```

**Job ID format**: `{lane.value}-{seq:06d}` where `seq` is a per-lane monotonic counter. Example: the third prompt job is `prompt-000003`.

**ETA calculation** uses EWMA (exponential weighted moving average) over the last 5 raw estimates:

```
raw_eta = elapsed × (1 − progress) / progress

smoothed = α × raw_eta[n] + (1 − α) × smoothed[n−1]   (α = 0.4)
```

This smooths the ETA against bursty progress reports (e.g., a WD14 job that jumps from 10% to 40% in one step). A pure linear estimate would oscillate wildly; EWMA dampens it.

---

### CancelToken

`CancelToken` is the interface between the spooler and a running job. Think of it as a flag on a pole — the spooler raises the flag to request cancellation; the runner periodically glances at it.

```python
class CancelToken:
    def raise_if_set(self) -> None:
        """Raise JobCancelled if cancellation has been requested."""

    def on_cancel(self, handler: Callable) -> None:
        """Register a callback to be invoked when cancel() is called.
        Use for external engine cleanup (e.g., cancelling a ComfyUI job)."""

    async def pause_checkpoint(self) -> None:
        """Suspend here until both lane and job are resumed, or abort if cancelled."""
```

The token carries three asyncio events:
- `_event` — the cancel signal (set when `spooler.cancel()` is called)
- `_lane_event` — the lane's global pause event (clear = lane paused)
- `_pause_event` — the per-job pause event (clear = this job individually paused)

A runner that calls `pause_checkpoint()` in its inner loop supports both lane-level and job-level pause with a single call.

---

### ProgressReporter

```python
class ProgressReporter:
    def update(self, progress: float, text: str | None = None) -> None:
        """Report deterministic progress (0.0 – 1.0) with optional status text."""

    def indeterminate(self) -> None:
        """Signal that work is in progress but percentage is not known."""
```

Internally, `update()` writes to the `Job` object and calls `_push_event("job_updated", job)` on the spooler. The spooler's throttle logic (see [SSE Streaming](#sse-streaming)) decides whether to actually broadcast the event immediately or defer it.

---

## Startup and Shutdown

The spooler is created and started in the FastAPI lifespan context manager (`main.py`):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    resources, lane_resource = build_resources(settings)
    spooler = JobSpooler(resources=resources, lane_resource=lane_resource)
    app.state.spooler = spooler
    await spooler.start()
    # ... other startup

    yield  # app runs here

    await spooler.stop()
```

**`spooler.start()`** does three things in sequence:
1. Creates one `asyncio.Task` per lane (5 worker tasks total, named `spooler-worker-{lane}`).
2. Calls `probe_resources_on_startup()` to verify Ollama and Qdrant are reachable before accepting jobs.
3. Launches three background monitor tasks:
   - `spooler-remote-monitor` — polls Ollama/Qdrant health every 15 seconds
   - `spooler-local-monitor` — polls GPU/CPU/RAM stats every 5 seconds
   - `spooler-resource-push` — pushes `resource_stats` SSE events every 5 seconds

**`spooler.stop()`** cancels all monitor tasks and worker tasks, then awaits their completion.

---

## Submitting a Job

```python
job_id = spooler.submit(
    lane=JobLane.EMBEDDING,
    title="Generate embeddings",
    func=run_pipeline,
    meta={"source": "scan"},
    priority=0,
    db=db,
    ollama=ollama,
    spooler=spooler,
)
```

Inside `submit()`:

```python
def submit(self, lane, title, func, meta=None, *, priority=0, **kwargs) -> str:
    self._seq[lane] += 1
    job_id = f"{lane.value}-{self._seq[lane]:06d}"
    job = Job(id=job_id, lane=lane, title=title, priority=priority,
              meta=meta or {}, _func=func, _kwargs=kwargs)
    self._registry[job_id] = job

    # Insert in descending priority order; same priority → FIFO
    queue = self._lane_queues[lane]
    idx = len(queue)
    for i, jid in enumerate(queue):
        if priority > self._registry[jid].priority:
            idx = i
            break
    queue.insert(idx, job_id)

    self._lane_work_ev[lane].set()   # wake the worker
    self._push_event("job_created", job)
    self._update_auto_pause(lane)    # may pause other lanes
    return job_id
```

Key points:
- The job queue is a plain `list[str]` of job IDs (not a `heapq`), sorted at insertion time. This makes `O(n)` insertion but `O(1)` pop from the front, which is fine for typical queue depths.
- `_lane_work_ev[lane].set()` wakes the worker coroutine, which is waiting on `await work_ev.wait()`.
- `_update_auto_pause()` is synchronous and runs immediately — if this GEN submission should cause EMBED to pause, it happens before `submit()` returns.

---

## The Worker Loop

This is the heart of the scheduler. Each lane has exactly one worker coroutine, running in an infinite loop:

```python
async def _worker(self, lane: JobLane) -> None:
    while True:
        # 1. Sleep until there is work to do
        await self._lane_work_ev[lane].wait()

        queue = self._lane_queues[lane]
        if not queue:
            self._lane_work_ev[lane].clear()
            continue

        # 2. Dequeue the highest-priority job (front of list)
        job_id = queue.pop(0)
        if not queue:
            self._lane_work_ev[lane].clear()

        job = self._registry.get(job_id)
        if job is None or job.state in (CANCELLED, CANCELLING):
            continue   # job was cancelled while queued — skip it

        # 3. Pause gate — hold here if the lane is paused
        self._held_jobs[lane] = job
        await self._lane_events[lane].wait()
        self._held_jobs[lane] = None

        if job.state in (CANCELLED, CANCELLING):
            continue   # job was cancelled while held at the gate

        # 4. Create per-job pause event (initially "set" = not paused)
        job_pause_ev = asyncio.Event()
        job_pause_ev.set()
        self._job_pause_events[job.id] = job_pause_ev

        # 5. Transition to RUNNING
        job.state = JobState.RUNNING
        job.started_at = time.time()
        self._push_event("job_updated", job)

        # 6. Wire up the CancelToken with pause callbacks
        reporter = ProgressReporter(job, self._push_event)
        cancel_token = CancelToken(job._cancel_event, job._cancel_handlers)
        cancel_token._lane_event  = self._lane_events[lane]
        cancel_token._pause_event = job_pause_ev
        cancel_token._on_pause  = lambda: (set job.state=PAUSED, push event)
        cancel_token._on_resume = lambda: (set job.state=RUNNING, push event)

        # 7. Execute the runner inside the resource semaphore
        try:
            result = await run_with_resource(job, resources, lane_resource, _run)
            job.state = SUCCEEDED
            job.result = result
            job.progress = 1.0
        except JobCancelled:
            job.state = CANCELLED
        except ResourceUnreachable as exc:
            job.state = FAILED
            job.error = str(exc)
        except asyncio.CancelledError:
            job.state = CANCELLED
        except Exception as exc:
            job.state = FAILED
            job.error = str(exc)
        finally:
            self._job_pause_events.pop(job.id, None)

        # 8. Finalize
        job.finished_at = time.time()
        self._push_event("job_finished", job)
        self._move_to_history(job)
        # Recompute auto-pause state
        if lane in _PRIORITY_TRIGGER_LANES:
            self._check_auto_resume()
        elif lane == JobLane.EMBEDDING:
            self._check_eval_pause()
```

**Step-by-step walk-through for one job:**

```
  work_ev.wait() --blocked--+
                             | submit() calls work_ev.set()
  <-------------- wakes -----+
  pop job from queue
  job.state == QUEUED -> proceed

  -- PAUSE GATE ----------------------------------
  held_jobs[lane] = job
  lane_events[lane].wait()  <-- may block here if lane is paused
                              (another task can cancel the job here
                               via cancel() -> we check state after unblocking)
  held_jobs[lane] = None
  ------------------------------------------------

  job.state = RUNNING

  runner(reporter, cancel, **kwargs)
    +-- reporter.update(0.1, "processing")  -> push job_updated (throttled)
    +-- cancel.raise_if_set()               -> if cancel requested -> JobCancelled
    +-- cancel.pause_checkpoint()           -> if paused -> suspend here
    +-- return result

  job.state = SUCCEEDED
  push job_finished
  move to history deque (appendleft -> LIFO)
```

**The held_jobs mechanism**: When a job is dequeued but waiting at the pause gate, `_held_jobs[lane]` tracks it. This allows `cancel()` to find and terminate jobs that are stuck at the gate — they have been removed from the queue but have not yet started running.

---

## Resource Management

### The Resource Dataclass

```python
@dataclass
class Resource:
    name: str
    kind: Literal["local", "remote"]
    concurrency: int = 1       # semaphore capacity
    endpoint: str | None = None
    health_path: str = "/"

    reachable: bool = True
    last_ok: float | None = None
    latency_ms: float | None = None
    version: str | None = None

    # local kind only
    gpu_util_pct: float | None = None
    temp_c: float | None = None
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    cpu_pct: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None

    _sem: asyncio.Semaphore   # created in __post_init__ from concurrency
```

### Default Lane → Resource Mapping

```python
DEFAULT_LANE_RESOURCE = {
    JobLane.GENERATION: "local-gpu0",   # GPU semaphore
    JobLane.EMBEDDING:  "local-gpu0",   # GPU semaphore (or "remote-ollama" if configured)
    JobLane.EVALUATION: None,           # no resource semaphore — lane pause handles it
    JobLane.SYNC:       None,
    JobLane.PROMPT:     None,
}
```

`EVALUATION` uses `None` deliberately. Holding the Ollama semaphore while sitting in a `pause_checkpoint()` would deadlock other lanes that also need Ollama. EVAL's throughput control is handled entirely through lane-level pausing (Tier 2), not semaphores.

If `remote-ollama` is configured, EMBEDDING delegates to that resource instead of `local-gpu0`.

### run_with_resource()

```python
async def run_with_resource(job, resources, lane_resource, func) -> Any:
    res_name = lane_resource.get(job.lane)
    if res_name is None:
        return await func()   # no resource constraint

    res = resources[res_name]

    if res.kind == "remote" and not res.reachable:
        raise ResourceUnreachable(f"Resource {res.name!r} is unreachable")

    async with res._sem:
        return await func()
```

`async with res._sem` acquires the semaphore, runs the function, and releases on any exit — whether the function returns normally, raises, or the task is cancelled. The `asyncio.Semaphore` context manager guarantees the release happens in the `__aexit__`.

If `concurrency=1` (the default), this ensures only one runner holds the GPU at a time across all lanes that map to the same resource.

### Health Monitoring

Two background loops run after `spooler.start()`:

**Remote monitor** (every 15 seconds):
```python
async def monitor_remote_resources(resources, interval=15):
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            for res in remote_resources:
                resp = await client.get(f"{res.endpoint}{res.health_path}")
                res.reachable = resp.status_code < 500
                res.latency_ms = elapsed_ms
                if "version" in resp.json():
                    res.version = resp.json()["version"]
            await asyncio.sleep(interval)
```

- Ollama: `GET /api/version` → parses `{"version": "0.x.y"}`
- Qdrant: `GET /healthz` → parses status

**Local monitor** (every 5 seconds):
```python
async def monitor_local_resources(resources, interval=5):
    while True:
        stats = await asyncio.to_thread(_poll_all)  # blocking I/O off the event loop
        for res in local_resources:
            res.gpu_util_pct = stats["gpu_util_pct"]
            res.vram_used_gb = stats["vram_used_gb"]
            # ... etc.
        await asyncio.sleep(interval)
```

`_poll_all()` reads from:
- `pynvml` (nvidia-ml-py) → GPU utilization, VRAM, temperature. Falls back to `nvidia-smi` subprocess if pynvml is unavailable.
- `/proc/stat` → CPU utilization percentage
- `/proc/meminfo` → RAM used / total
- `/sys/class/hwmon/hwmon*` or `/sys/class/thermal/thermal_zone*` → CPU temperature

All blocking filesystem reads are dispatched via `asyncio.to_thread()` to avoid stalling the event loop.

---

## The Auto-Pause System

This is the most nuanced part of the spooler. Understanding it requires knowing three things:

1. The two tier constants
2. When each tier is evaluated
3. The difference between `LanePauseReason.MANUAL` and `LanePauseReason.AUTO`

### Tier 1: Configurable Priority Pause

**Trigger**: A job is submitted to GEN or PROMPT (the "priority trigger lanes").  
**Effect**: Pauses EMBED and EVAL (the "auto-pause target lanes").  
**When disabled**: The user can turn off Tier 1 in the Control Room settings.

```python
_PRIORITY_TRIGGER_LANES: frozenset = {JobLane.GENERATION, JobLane.PROMPT}
_DEFAULT_AUTO_PAUSE_TARGETS: frozenset = {JobLane.EMBEDDING, JobLane.EVALUATION}
```

The check runs synchronously in `_update_auto_pause()`, called from `submit()`:

```python
def _update_auto_pause(self, submitted_lane: JobLane) -> None:
    if self._auto_pause_on_priority and submitted_lane in _PRIORITY_TRIGGER_LANES:
        has_priority = any(
            j.state in (QUEUED, RUNNING)
            for j in self._registry.values()
            if j.lane in _PRIORITY_TRIGGER_LANES
        )
        if has_priority:
            non_eval = self._auto_pause_target_lanes - _TIER2_MANAGED_LANES
            self.pause_lanes(non_eval, LanePauseReason.AUTO)
    self._check_eval_pause()  # always run tier 2
```

Note that EVALUATION is excluded from the Tier 1 targets (`- _TIER2_MANAGED_LANES`) because EVAL is entirely managed by Tier 2.

Auto-pause is cleared in `_check_auto_resume()`, called when a GEN/PROMPT job finishes:

```python
def _check_auto_resume(self) -> None:
    has_priority = any(
        j.state in (QUEUED, RUNNING, CANCELLING)
        for j in self._registry.values()
        if j.lane in _PRIORITY_TRIGGER_LANES
    )
    if not has_priority:
        non_eval = self._auto_pause_target_lanes - _TIER2_MANAGED_LANES
        self.resume_lanes(non_eval, reason=LanePauseReason.AUTO)
    self._check_eval_pause()
```

### Tier 2: Hardcoded EVAL Pause

**Trigger**: Any job exists in GEN, PROMPT, or EMBED lanes (the "evaluation blocking lanes").  
**Effect**: Always pauses EVAL regardless of Tier 1 settings.  
**Cannot be disabled** — this is a hardcoded priority rule.

```python
_EVALUATION_BLOCKING_LANES: frozenset = {
    JobLane.GENERATION,
    JobLane.PROMPT,
    JobLane.EMBEDDING,
}

def _check_eval_pause(self) -> None:
    blocking = any(
        j.state in (QUEUED, RUNNING, CANCELLING)
        for j in self._registry.values()
        if j.lane in _EVALUATION_BLOCKING_LANES
    )
    if blocking:
        self.pause_lanes([JobLane.EVALUATION], LanePauseReason.AUTO)
    else:
        self.resume_lanes([JobLane.EVALUATION], reason=LanePauseReason.AUTO)
```

`_check_eval_pause()` is called from every code path that changes the active job count: `submit()`, `_update_auto_pause()`, `_check_auto_resume()`, and after EMBEDDING finishes.

**Priority ordering summary:**

```
GEN / PROMPT   ──► highest priority (blocks EMBED and EVAL via tier 1)
EMBED          ──► blocks EVAL via tier 2
EVAL           ──► lowest priority (runs only when nothing else is active)
SYNC           ──► independent (no resource semaphore, never blocked)
```

### Manual vs. Auto Pause

`LanePauseReason` prevents the wrong party from resuming a lane:

```python
def resume_lanes(self, lanes, reason=None):
    for lane in lanes:
        current = self._lane_pause_reason[lane]
        if current is None:
            continue
        if reason is not None and current != reason:
            continue   # don't auto-resume a manually paused lane
        self._lane_events[lane].set()
        self._lane_pause_reason[lane] = None
```

If the user manually pauses EMBED (`reason=MANUAL`), a GEN job finishing will call `_check_auto_resume()` → `resume_lanes(reason=LanePauseReason.AUTO)`. Since the current reason is `MANUAL != AUTO`, the lane stays paused. The user's intent is respected.

`reason=None` (force resume) is only used by the manual "resume" button in the UI, which calls `resume_lane` via `POST /api/jobs/lanes/{lane}/resume`.

### The Pause Gate vs. CancelToken.pause_checkpoint()

These are two separate pause mechanisms operating at different points in a job's lifecycle:

**Pause gate** (in the worker loop):
```python
self._held_jobs[lane] = job
await self._lane_events[lane].wait()   # ← gate
self._held_jobs[lane] = None
```
This applies between dequeue and job start. A job sitting at the gate has not started running yet — its runner function has not been called. It consumes no resources and no cancel token exists. The spooler can mark it `CANCELLED` here without any cooperative protocol.

**`pause_checkpoint()`** (inside the runner function):
```python
async def pause_checkpoint(self) -> None:
    self.raise_if_set()
    while True:
        lane_ok = self._lane_event is None or self._lane_event.is_set()
        job_ok  = self._pause_event is None or self._pause_event.is_set()
        if lane_ok and job_ok:
            break
        # signal PAUSED state to UI
        if not _paused:
            self._on_pause()
        # wait for any of: lane resume, job resume, cancel
        done, pending = await asyncio.wait(
            [lane_task, job_task, cancel_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done:
            raise JobCancelled()
    self.raise_if_set()
```
This applies to a running job that has already acquired the GPU semaphore. The job suspends without releasing the semaphore — it is still logically "running" and holds its resource slot. This is the correct behavior for a mid-generation pause (e.g., a long embedding batch) where releasing the semaphore and re-acquiring it would cause unnecessary context loss.

---

## SSE Streaming

### Connection Lifecycle

Every browser that opens the Control Room connects to `GET /api/jobs/stream`. The spooler maintains a list of subscribers — one `asyncio.Queue` per connected client.

```python
async def stream(self) -> AsyncGenerator[str, None]:
    q = asyncio.Queue()
    self._subscribers.append(q)
    try:
        # Immediately send full state (avoids blank screen on connect / reconnect)
        yield _sse_event("snapshot", {
            "jobs": self.snapshot(),
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
                yield ": ping\n\n"   # SSE comment line keeps connection alive
                last_heartbeat = time.monotonic()
    finally:
        self._subscribers.remove(q)
```

The snapshot is sent before yielding control back to the event loop, so the client cannot miss an event between connection and first message.

### Event Types

| Event | When sent | Data |
|---|---|---|
| `snapshot` | On connect | Full jobs + resources + lane states |
| `job_created` | `submit()` | Full job dict |
| `job_updated` | Progress/state change | Full job dict (throttled — see below) |
| `job_finished` | Job reaches terminal state | Full job dict |
| `resource_stats` | Every 5 seconds | `{resources: [...]}` |
| `lane_state` | Lane paused/resumed | `{lanes: [{lane, paused, pause_reason}]}` |
| `ping` | Every 15 seconds (comment) | none — keeps connection alive |

### Throttle Logic

High-frequency jobs (embedding 10,000 images) emit a `job_updated` event on every `reporter.update()` call. Without throttling, this floods the client with thousands of events per minute.

```python
_THROTTLE_INTERVAL = 0.25   # 4 Hz cap on job_updated

def _push_event(self, event_type: str, job: Job) -> None:
    now = time.monotonic()
    if event_type == "job_updated":
        last_push, last_state = self._throttle.get(job.id, (0.0, ''))
        state_changed = job.state.value != last_state
        if not state_changed and now - last_push < _THROTTLE_INTERVAL:
            return   # drop this update
    self._throttle[job.id] = (now, job.state.value)

    data = _sse_event(event_type, job.to_dict())
    for q in self._subscribers:
        q.put_nowait(data)   # non-blocking; drops if queue is full
```

State transitions (`RUNNING → PAUSED`, `RUNNING → CANCELLING`) always pass through — only pure progress updates are rate-limited. This ensures the UI immediately reflects pause/cancel even under heavy load.

### SSE Wire Format

The SSE protocol is plain text over HTTP. Each event is:

```
event: job_updated
data: {"id":"embed-000003","state":"running","progress":0.45,...}

```

Two newlines terminate the event. The browser's `EventSource` API parses this automatically, firing `addEventListener('job_updated', handler)`.

---

## REST API Reference

All endpoints are under `/api/jobs` (see `backend/app/api/jobs.py`):

| Method | Path | Action |
|---|---|---|
| `GET` | `/api/jobs/stream` | SSE stream — see above |
| `GET` | `/api/jobs` | Current snapshot (JSON, non-streaming) |
| `POST` | `/api/jobs/{id}/cancel` | Request cancellation |
| `POST` | `/api/jobs/{id}/pause` | Pause at next checkpoint |
| `POST` | `/api/jobs/{id}/resume` | Resume an individually paused job |
| `POST` | `/api/jobs/{id}/reorder` | Move job up/down in queue (`{"direction": +1}`) |
| `POST` | `/api/jobs/{id}/retry` | Resubmit a FAILED or CANCELLED job |
| `GET` | `/api/jobs/lanes` | Lane pause states |
| `POST` | `/api/jobs/lanes/{lane}/pause` | Manually pause a lane |
| `POST` | `/api/jobs/lanes/{lane}/resume` | Force-resume a lane |

**Retry** creates a brand-new job with a new ID, same function and kwargs:
```python
def retry(self, job_id: str) -> str:
    job = self._registry[job_id]
    new_id = self.submit(
        lane=job.lane, title=job.title, func=job._func,
        meta=dict(job.meta), **job._kwargs,
    )
    return new_id
```

**Reorder** reassigns numerical priorities by position — position 0 gets the highest number, position n-1 gets 1. This keeps the priority field consistent with visual queue order:

```python
n = len(queue)
for i, jid in enumerate(queue):
    self._registry[jid].priority = n - i
```

---

## Frontend Integration

### EventSource Setup

```javascript
// App.vue
function startJobStream() {
  const es = new EventSource(`/api/jobs/stream?token=${encodeURIComponent(getToken())}`)

  es.addEventListener('snapshot', (e) => {
    const { jobs, resources, lanes } = JSON.parse(e.data)
    jobsMap.value = new Map(jobs.map(j => [j.id, j]))  // replace entire map
    resourcesRef.value = resources
    crIngestEvent('snapshot', data)
  })

  es.addEventListener('job_created', upsert)
  es.addEventListener('job_finished', upsert)   // immediate map update

  es.addEventListener('job_updated', (e) => {
    const job = JSON.parse(e.data)
    crIngestEvent('job_updated', job)
    _pendingJobUpdates.set(job.id, job)          // accumulate
    if (!_pendingJobUpdatesTimer) {
      _pendingJobUpdatesTimer = setTimeout(() => {
        // batch flush: apply all accumulated updates as one Vue reactive write
        const newMap = new Map(jobsMap.value)
        for (const [id, j] of _pendingJobUpdates) newMap.set(id, j)
        jobsMap.value = newMap
        _pendingJobUpdates = null
        _pendingJobUpdatesTimer = null
      }, 250)                                    // 250 ms batch window
    }
  })

  es.onerror = () => {
    es.close()
    setTimeout(startJobStream, 3000)  // reconnect after 3 s
  }
}
```

**Why 250ms batching?** Vue's reactivity system re-renders the entire active job list on every `jobsMap.value` write. A fast embedding job can emit 4 `job_updated` SSE events per second (the server-side throttle rate). Without batching, this is 4 full list re-renders per second. With batching, it is at most 4 per second on the server side but coalesced into ≤4 per second on the client as well — but adjacent updates within 250ms merge into a single render.

**Why a new Map on each update?** Vue's reactivity requires that the reference itself changes for computed properties and watchers to re-trigger. Mutating an existing Map in place would be invisible to Vue.

### Control Room: Lane and System State

`useControlRoom.js` computes ISA-101 lamp states from the live job list:

```javascript
// Each lane → one of: NOMINAL, ACTIVE, CAUTION, FAULT, PAUSED, STANDBY
const systemStatus = computed(() => {
  return SYSTEMS.reduce((acc, sys) => {
    const laneJobs = jobs.filter(j => j.id.startsWith(sys.lane + '-'))
    const failed  = laneJobs.filter(j => j.state === 'failed')
    const running = laneJobs.filter(j => j.state === 'running' || j.state === 'cancelling')
    const queued  = laneJobs.filter(j => j.state === 'queued')
    const ls = laneStates.value[sys.lane]

    if (failed.length > 0)                      return FAULT
    if (running.length > 0)                     return ACTIVE
    if (ls?.paused && queued.length > 0)        return CAUTION  // backlog behind pause
    if (ls?.paused)                             return PAUSED
    if (queued.length >= 3)                     return CAUTION
    if (queued.length > 0)                      return ACTIVE
    return NOMINAL
  }, {})
})
```

The master status light (`STANDBY / RUNNING / CAUTION / FAULT / STARTING`) aggregates all system states and the `last_ok` field of remote resources (if `last_ok === null`, the resource has never successfully connected — shown as STARTING).

---

## The Runner Contract

Every runner function must follow this signature:

```python
async def run_X(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    # deps injected as keyword arguments at submit() time
    db,
    ollama,
    spooler=None,
) -> Any:
```

The `reporter` and `cancel` are injected by the spooler; everything else comes from the `**kwargs` passed to `submit()`.

**Minimal compliant runner:**

```python
async def run_example(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    items: list,
    db,
) -> dict:
    reporter.indeterminate()   # signal: "working, no ETA yet"

    results = []
    for i, item in enumerate(items):
        cancel.raise_if_set()          # cooperative cancellation check
        await cancel.pause_checkpoint() # support pause (lane or per-job)

        result = await process(item, db)
        results.append(result)

        reporter.update(
            (i + 1) / len(items),      # 0.0 – 1.0
            f"{i + 1}/{len(items)} processed",
        )

    return {"count": len(results)}
```

**Registering an abort handler** (for external engines):

```python
async def run_comfy_generate(reporter, cancel, *, comfy, prompt_id, ...):
    task = asyncio.create_task(comfy.wait_for_image(prompt_id))
    cancel.on_cancel(task.cancel)   # if cancelled, also cancel the comfy polling task

    result = await task
    return result
```

`on_cancel` handlers are called synchronously inside `spooler.cancel()`. If the handler returns a coroutine, the spooler wraps it in `asyncio.create_task()` automatically.

**Calling conventions:**
- `reporter.update()` expects `progress` in `[0.0, 1.0]`. Values outside this range are clamped.
- `cancel.raise_if_set()` should appear at the beginning of every significant loop iteration. It is a cheap flag check — not a blocking call.
- `cancel.pause_checkpoint()` is an `await` — it must be called from async code. Use it in inner loops of long-running jobs where the pause granularity matters.

---

## Deep Dive: asyncio Internals

### Why asyncio.Event for Lane Pausing

The simplest way to pause a worker would be a flag:

```python
if self._paused:
    await asyncio.sleep(0.1)
    continue
```

This works but introduces up to 100ms of latency on resume and wastes CPU on polling. The spooler uses `asyncio.Event` instead:

```python
# pause: event is "clear" (not set)
self._lane_events[lane].clear()

# resume: event is "set"
self._lane_events[lane].set()

# worker waits with zero CPU overhead
await self._lane_events[lane].wait()
```

`asyncio.Event.wait()` suspends the coroutine and places it in the event loop's internal callback queue. The coroutine is not rescheduled until `event.set()` is called. Resume latency is a single event loop tick — typically under 1ms.

The same design applies to per-job pausing (`_job_pause_events`) and the cancel signal (`_cancel_event` on Job). Every pause/resume/cancel operation is an `asyncio.Event` set/clear, not a polling loop.

### pause_checkpoint() Mechanics

The full implementation of `pause_checkpoint()` demonstrates a common asyncio pattern: waiting on **multiple events simultaneously** and responding to whichever fires first.

```python
async def pause_checkpoint(self) -> None:
    self.raise_if_set()
    _paused = False
    while True:
        lane_ok = self._lane_event is None or self._lane_event.is_set()
        job_ok  = self._pause_event is None or self._pause_event.is_set()
        if lane_ok and job_ok:
            break   # both events are set → all clear

        if not _paused:
            _paused = True
            if self._on_pause:
                self._on_pause()   # job.state = PAUSED, push SSE event

        # Build a list of tasks to wait on
        waits = []
        if not lane_ok:
            waits.append(asyncio.create_task(self._lane_event.wait()))
        if not job_ok:
            waits.append(asyncio.create_task(self._pause_event.wait()))
        cancel_task = asyncio.create_task(self._event.wait())
        waits.append(cancel_task)

        done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()   # cancel losing tasks to avoid Task leaks

        if cancel_task in done:
            if _paused and self._on_resume:
                self._on_resume()
            raise JobCancelled()
        # Otherwise: lane or job was resumed → loop again to check both conditions
```

Why the `while True` loop? Because `asyncio.wait` returns as soon as *any* event fires. If both lane and job are paused, resuming the lane wakes the wait — but the job is still individually paused. The loop re-evaluates both conditions before proceeding.

Why `asyncio.create_task()` for each event? `asyncio.wait()` requires awaitables, not coroutines. Wrapping `event.wait()` in a task makes it cancellable (necessary for the `for t in pending: t.cancel()` cleanup).

Failing to cancel the losing tasks would leave dangling asyncio Tasks that stay alive until the event fires, consuming memory and potentially triggering `Task was destroyed but it is pending!` warnings.

### EWMA-Based ETA

The raw ETA estimate from linear extrapolation is:

```
raw_eta = elapsed × (1 − p) / p
```

where `p` is the current progress in [0, 1]. This formula assumes constant throughput — if the job processes 100 items and is 10% done after 5 seconds, it estimates 45 more seconds.

The problem is that progress is not constant. A WD14 batch might process the first 10% (small images) in 5s, then stall on large images. The raw ETA would oscillate: first 45s, then 20s, then 60s.

EWMA smooths this by combining each new estimate with a weighted history:

```
smoothed[0] = raw_eta[0]
smoothed[n] = α × raw_eta[n] + (1 − α) × smoothed[n−1]   (α = 0.4)
```

With α = 0.4, each new sample contributes 40% and the prior history 60%. The last 5 samples are retained in a `deque(maxlen=5)`.

Effect: if throughput suddenly slows, the ETA increases gradually rather than jumping. If it speeds up, it decreases gradually. The displayed ETA is stable enough to read.

The samples are only computed on `eta_seconds` property access — they are not pushed proactively. The property is called when `job.to_dict()` is called inside `_push_event()`.

### History: LIFO via appendleft

Completed jobs are moved to `self._history: deque(maxlen=100)` with:

```python
def _move_to_history(self, job: Job) -> None:
    self._history.appendleft(job)   # prepend: most recent first
    self._registry.pop(job.id, None)
    self._throttle.pop(job.id, None)
```

`deque.appendleft()` inserts at position 0 (the left end). Combined with the `maxlen=100` cap, this creates a LIFO structure where the 100 most recent completed jobs are retained in most-recent-first order.

`snapshot()` sorts the combined active + history list by `created_at` descending before returning — so even though history is stored LIFO internally, the API consistently returns most-recent-first regardless of which combination of active and historical jobs is present.

---

## Putting It Together: A Complete Job Lifecycle

From button click to Control Room lamp change:

```
1. User clicks "Generate" in the UI
   -> POST /api/comfy/... -> spooler.submit(JobLane.GENERATION, ...)

2. submit():
   -> job_id = "gen-000007"
   -> job.state = QUEUED
   -> push SSE: job_created
   -> _update_auto_pause() -> Tier 1: pause EMBED lane
   -> push SSE: lane_state (embed: paused, reason=auto)

3. gen-worker wakes (work_event.set())
   -> pops gen-000007 from queue
   -> lane_events[GEN].is_set() = True -> passes gate immediately
   -> run_with_resource() acquires local-gpu0 semaphore
   -> job.state = RUNNING
   -> push SSE: job_updated

4. Runner executes:
   -> cancel.raise_if_set() at each frame
   -> reporter.update(0.3, "frame 30/100") -> push SSE: job_updated (throttled)
   -> ...

5. User clicks "Pause Job"
   -> POST /api/jobs/gen-000007/pause
   -> job_pause_ev.clear()
   -> next cancel.pause_checkpoint() in runner:
      -> job.state = PAUSED
      -> push SSE: job_updated (state=paused)
      -> suspends inside asyncio.wait([lane_task, job_task, cancel_task])

6. User clicks "Resume Job"
   -> POST /api/jobs/gen-000007/resume
   -> job_pause_ev.set()
   -> asyncio.wait() returns (job_task done)
   -> job.state = RUNNING
   -> push SSE: job_updated (state=running)
   -> runner continues

7. Runner returns result
   -> job.state = SUCCEEDED, job.progress = 1.0
   -> release GPU semaphore
   -> push SSE: job_finished
   -> _move_to_history()
   -> _check_auto_resume() -> Tier 1: resume EMBED lane
   -> push SSE: lane_state (embed: not paused)

8. Frontend:
   -> job_finished event -> handleJobFinished() -> scan triggered
   -> lane_state event -> EMBED lamp -> NOMINAL
```

---

## Appendix: Key Constants

| Constant | Value | Effect |
|---|---|---|
| `_THROTTLE_INTERVAL` | 0.25 s | Minimum interval between `job_updated` SSE pushes (progress-only) |
| `_HEARTBEAT_INTERVAL` | 15.0 s | SSE `: ping` comment interval |
| `_HISTORY_MAXLEN` | 100 | Maximum completed jobs retained in history deque |
| Resource stats push interval | 5 s | How often `resource_stats` SSE events are sent |
| Remote health check interval | 15 s | How often Ollama/Qdrant liveness is probed |
| Local stats poll interval | 5 s | How often GPU/CPU/RAM metrics are read |
| EWMA α | 0.4 | Weight of newest ETA sample vs. historical average |
| ETA sample window | 5 samples | `deque(maxlen=5)` on `Job._eta_samples` |
| Frontend SSE batch window | 250 ms | `job_updated` events coalesced before Vue re-render |
| Frontend reconnect delay | 3 s | Delay before re-opening `EventSource` on error |

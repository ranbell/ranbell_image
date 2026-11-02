"""**A browser reload must not lose the forging run.** (2026-11-02)

The Showrunner: "make it so a browser reload does not lose track of the task —
that is what the spooler is for."
Right — the job lived on in the spooler; the screen was the thing that let go.

Three faults stacked:
- the stream popped the token queue out of the registry the moment its reader
  went away, so reconnecting got a 404 and there was no way back in;
- a client gone for 45 seconds cancelled the job outright;
- and the browser kept the job id only in memory.

So the queue now survives a reader leaving, a dropped socket no longer cancels
anything, and one forging run carries a `group_id` — a work package label — that
the render it queues carries too. **The label is metadata and never a parent
job**: a job waiting on another would hold its lane for the whole render.
"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.jobs.sse_stream import iter_queue_sse


class _Spooler:
    def __init__(self):
        self.cancelled = []

    async def cancel(self, job_id):
        self.cancelled.append(job_id)


class _Req:
    """A request whose client is present or gone, as the case needs."""

    def __init__(self, *, disconnected: bool, spooler=None):
        self.disconnected = disconnected
        self.app = SimpleNamespace(state=SimpleNamespace(spooler=spooler or _Spooler()))

    async def is_disconnected(self):
        return self.disconnected


async def _drain(agen):
    return [frame async for frame in agen]


def _queue(items):
    q = asyncio.Queue()
    for item in items:
        q.put_nowait(item)
    return q


# ── The stream, when its reader goes away ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reader_that_goes_away_leaves_the_queue_to_be_rejoined():
    q = _queue([{"i": 1}, {"i": 2}, {"i": 3}])
    registry = {"prompt-1": q}
    sp = _Spooler()
    req = _Req(disconnected=True, spooler=sp)

    await _drain(iter_queue_sse(
        req, q, job_id="prompt-1", registry=registry,
        disconnect_grace_seconds=0, cancel_on_disconnect=False,
        keep_queue_on_disconnect=True,
    ))

    # The door is still open — this is what used to 404.
    assert "prompt-1" in registry
    # Nothing was cancelled by a dropped socket.
    assert sp.cancelled == []


@pytest.mark.asyncio
async def test_what_was_taken_while_nobody_listened_goes_back_in_order():
    # Anything pulled off the queue while the client was gone cannot be shown
    # to have arrived, so it is put back — in front of what is still queued.
    q = _queue([{"i": 1}, {"i": 2}, {"i": 3}])
    registry = {"prompt-1": q}
    req = _Req(disconnected=True)

    frames = await _drain(iter_queue_sse(
        req, q, job_id="prompt-1", registry=registry,
        disconnect_grace_seconds=0, cancel_on_disconnect=False,
        keep_queue_on_disconnect=True,
    ))

    assert frames == []        # nothing was talked into a dead socket
    left = []
    while not q.empty():
        left.append(q.get_nowait())
    assert left == [{"i": 1}, {"i": 2}, {"i": 3}]


@pytest.mark.asyncio
async def test_the_rejoining_reader_gets_the_backlog_then_the_tail():
    # The queue is unbounded, so the run piles up while nobody listens and the
    # next reader simply reads it. That is the whole replay mechanism.
    q = _queue([{"i": 1}, {"i": 2}, None])
    registry = {"prompt-1": q}
    req = _Req(disconnected=False)

    frames = await _drain(iter_queue_sse(
        req, q, job_id="prompt-1", registry=registry,
        cancel_on_disconnect=False, keep_queue_on_disconnect=True,
    ))

    assert [f for f in frames if f.startswith("data: ")] == [
        'data: {"i": 1}\n\n', 'data: {"i": 2}\n\n',
    ]
    # `None` is the job's real end of life, so now the door closes.
    assert "prompt-1" not in registry


@pytest.mark.asyncio
async def test_the_other_streams_are_unchanged():
    """Inspire and Invoke share this helper and must keep the old behaviour."""
    q = _queue([{"i": 1}])
    registry = {"prompt-9": q}
    sp = _Spooler()
    req = _Req(disconnected=True, spooler=sp)

    await _drain(iter_queue_sse(
        req, q, job_id="prompt-9", registry=registry,
        disconnect_grace_seconds=0,
        # the defaults: cancel_on_disconnect=True, keep_queue_on_disconnect=False
    ))

    assert "prompt-9" not in registry
    assert sp.cancelled == ["prompt-9"]


# ── Collecting the queues nobody rejoins ─────────────────────────────────────


def _reap_with(rows, queues):
    from backend.app.api.ai import _reap_refine_queues
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        refine_token_queues=queues,
        spooler=SimpleNamespace(snapshot=lambda: rows),
    )))
    return _reap_refine_queues(req)


def test_a_finished_run_nobody_came_back_for_is_collected():
    from backend.app.api.ai import _REFINE_QUEUE_KEEP_SECONDS
    queues = {"prompt-1": asyncio.Queue()}
    rows = [{"id": "prompt-1", "state": "succeeded",
             "finished_at": time.time() - _REFINE_QUEUE_KEEP_SECONDS - 60}]
    assert _reap_with(rows, queues) == 1
    assert queues == {}


def test_a_run_that_just_finished_is_kept_so_a_reload_can_still_read_it():
    queues = {"prompt-1": asyncio.Queue()}
    rows = [{"id": "prompt-1", "state": "succeeded", "finished_at": time.time()}]
    assert _reap_with(rows, queues) == 0
    assert "prompt-1" in queues


def test_a_running_runs_queue_is_never_collected():
    queues = {"prompt-1": asyncio.Queue()}
    rows = [{"id": "prompt-1", "state": "running", "finished_at": None}]
    assert _reap_with(rows, queues) == 0
    assert "prompt-1" in queues


def test_a_queue_whose_job_fell_out_of_the_history_is_collected():
    queues = {"prompt-1": asyncio.Queue()}
    assert _reap_with([], queues) == 1
    assert queues == {}


# ── The work package ─────────────────────────────────────────────────────────


def test_the_package_label_reaches_the_render():
    from backend.app.api.ai import RefineRequest
    from backend.app.jobs.runners import _submit_gen_direct

    seen = {}

    class _Sp:
        def submit(self, lane, title, fn, meta=None, **kw):
            seen["lane"], seen["meta"] = lane, meta
            return "gen-1"

    body = RefineRequest(sha256s=["a"], workflow_name="wf")
    _submit_gen_direct(_Sp(), None, None, body, "p", "", group_id="refine:abc123")
    assert seen["meta"]["group_id"] == "refine:abc123"


def test_a_render_without_a_package_carries_no_label():
    from backend.app.api.ai import RefineRequest
    from backend.app.jobs.runners import _submit_gen_direct

    seen = {}

    class _Sp:
        def submit(self, lane, title, fn, meta=None, **kw):
            seen["meta"] = meta
            return "gen-1"

    _submit_gen_direct(_Sp(), None, None, RefineRequest(sha256s=["a"]), "p", "")
    assert "group_id" not in seen["meta"]


def test_the_runner_queues_the_render_and_does_not_wait_on_it():
    """The package is a label, not a parent.

    A job that waited on another would hold its lane — PROMPT and GENERATION
    have one worker each — so the forging would own the prompt lane for the
    whole render and starve everything behind it. This pins that the render is
    queued and the runner moves on.
    """
    import inspect

    from backend.app.jobs import runners

    src = inspect.getsource(runners.run_refine_prompt)
    assert "_submit_gen_direct(" in src
    # No awaiting another job's completion from inside this one.
    assert "spooler.wait(" not in src
    assert "await spooler.wait" not in src

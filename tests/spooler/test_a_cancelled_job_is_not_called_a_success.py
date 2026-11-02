"""**Cancelling is cooperative, so the answer has to be honest.** (2026-11-02)

The Showrunner: "check whether there is any way a task cannot be cancelled
after it has been submitted to the spooler console — across every submission
path."

`cancel()` on a running job sets `CANCELLING` and the cancel event and calls the
`on_cancel` handlers. It does **not** cancel the asyncio task — that is
deliberate, since killing a task mid-write would leave the library half
updated. So a runner that never looks at the token runs to the end.

The audit found 11 of 47 runners that never looked, and worse: the worker set
`SUCCEEDED` on any normal return, with no thought for the cancel event. So the
Showrunner pressed cancel, the work ran to completion, and the console told her
it had succeeded. That is the hardest kind of "it does not work" to see.

The work cannot be unwound from the worker, but the state can be truthful — and
a job that ends CANCELLED can be dismissed, where one left at "cancelling…" had
no button at all.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.spooler.models import JobLane, JobState
from app.spooler.resources import Resource
from app.spooler.spooler import JobSpooler


def _spooler() -> JobSpooler:
    return JobSpooler(
        resources={"local-gpu0": Resource(name="local-gpu0", kind="local", concurrency=1)},
        lane_resource={lane: None for lane in JobLane},
    )


async def _cooperative(reporter, cancel, **kw):
    for _ in range(40):
        cancel.raise_if_set()
        await asyncio.sleep(0.02)
    return {"finished": "fully"}


async def _never_looks(reporter, cancel, **kw):
    for _ in range(40):
        await asyncio.sleep(0.02)
    return {"finished": "fully"}


def _cancel_midway(fn) -> tuple[str, str, object]:
    """Submit, cancel once it is running, and report (mid state, final state, result)."""
    out: dict = {}

    async def _t():
        sp = _spooler()
        await sp.start()
        try:
            jid = sp.submit(JobLane.SYNC, "audit", fn)
            await asyncio.sleep(0.25)
            assert await sp.cancel(jid) is True, "cancel が受理されない"

            def _find():
                return sp._registry.get(jid) or next(
                    (x for x in sp._history if x.id == jid), None)

            out["mid"] = _find().state
            for _ in range(60):
                await asyncio.sleep(0.1)
                if _find().state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED):
                    break
            job = _find()
            out["final"], out["result"] = job.state, job.result
        finally:
            await sp.stop()

    asyncio.run(_t())
    return out["mid"], out["final"], out["result"]


def test_a_runner_that_checks_the_token_stops_and_reports_cancelled():
    mid, final, result = _cancel_midway(_cooperative)
    assert mid is JobState.CANCELLING
    assert final is JobState.CANCELLED
    assert result is None, "途中で止まったのに結果が残っている"


def test_a_runner_that_never_checks_is_still_not_called_a_success():
    """It runs to the end — nothing can stop it — but it is not a success.

    This is the one that used to read SUCCEEDED.
    """
    mid, final, result = _cancel_midway(_never_looks)
    assert mid is JobState.CANCELLING
    assert final is JobState.CANCELLED, (
        "キャンセルしたジョブが成功扱いになっている"
    )
    # What it managed to produce is kept, so nothing is silently lost.
    assert result == {"finished": "fully"}


def test_a_job_nobody_cancelled_still_succeeds():
    """The honesty must not cost the ordinary case."""
    out: dict = {}

    async def _t():
        sp = _spooler()
        await sp.start()
        try:
            jid = sp.submit(JobLane.SYNC, "audit", _cooperative)
            for _ in range(60):
                await asyncio.sleep(0.05)
                job = sp._registry.get(jid) or next(
                    (x for x in sp._history if x.id == jid), None)
                if job.state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED):
                    break
            out["final"], out["result"] = job.state, job.result
        finally:
            await sp.stop()

    asyncio.run(_t())
    assert out["final"] is JobState.SUCCEEDED
    assert out["result"] == {"finished": "fully"}

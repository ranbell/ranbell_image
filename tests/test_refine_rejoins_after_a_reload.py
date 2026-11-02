"""**The screen has to let the spooler remember the run.** (2026-11-02)

The Showrunner: "make it so a browser reload does not lose track of the task —
that is what the spooler is for."

The server side keeps the token queue alive and no longer cancels a job when a
socket drops. That has a sharp edge: **Cancel used to work by dropping the
socket.** `cancelRefine()` only called `abort()` on the local reader and let the
stream cancel the job on disconnect. With that gone, Cancel has to ask the
server — which is also the first time it can stop the render, because aborting
a reader never reached a queued ComfyUI job.

So these pin the two halves that would silently stop working: Cancel asking the
server, and the reload rejoining from the snapshot rather than from anything
remembered in the browser.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend/src/App.vue").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """The source of one top-level function in App.vue."""
    m = re.search(rf"\nasync function {name}\(|\nfunction {name}\(", APP)
    assert m, f"{name} が見つからない"
    start = m.start() + 1
    # up to the next top-level declaration
    nxt = re.search(r"\n(?:async function |function |const |onMounted)", APP[start + 10:])
    return APP[start: start + 10 + (nxt.start() if nxt else len(APP))]


def test_cancel_asks_the_server_to_call_off_the_whole_package():
    src = _fn("cancelRefine")
    assert "/api/jobs/groups/" in src and "cancel" in src, (
        "Cancel がサーバに頼んでいない。切断ではもうジョブは止まらない"
    )
    assert "method: 'POST'" in src
    # The local reader is still aborted, but it is no longer the mechanism.
    assert "refineAbortController?.abort()" in src


def test_cancel_falls_back_to_the_single_job_without_a_package():
    src = _fn("cancelRefine")
    assert "/api/jobs/" in src and "refinePromptJobId" in src


def test_the_snapshot_rejoins_a_running_forging_run():
    assert "resumeRefinePackage(" in APP, "再読込の復帰処理が無い"
    # It is driven by the snapshot's job list, not by anything stored here.
    snap = APP[APP.index("addEventListener('snapshot'"):]
    snap = snap[: snap.index("addEventListener('job_updated'")]
    assert "refine:" in snap and "resumeRefinePackage(" in snap


def test_the_run_is_not_remembered_in_the_browser():
    """The spooler is the one that remembers — that is the point."""
    for name in ("refineGroupId", "refinePromptJobId", "refineGenJobId"):
        for store in ("localStorage", "sessionStorage"):
            assert not re.search(rf"{store}\.(set|get)Item\(\s*['\"][^'\"]*{name}", APP), (
                f"{name} を {store} に持っている"
            )


def test_rejoining_reads_the_same_stream_a_fresh_run_does():
    """One reader, used by both paths: a backlog and a live tail look the same."""
    assert "async function readRefineStream(" in APP
    assert "await readRefineStream(" in _fn("resumeRefinePackage")
    assert "await readRefineStream(" in _fn("runRefine")


def test_a_fresh_run_keeps_the_package_label_it_is_given():
    src = _fn("runRefine")
    assert "group_id" in src and "refineGroupId.value = group_id" in src

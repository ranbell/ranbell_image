"""**An event nobody listens to is a button that does nothing.** (2026-09-21)

The ✕ on a failed job reaches the server — `cancel` falls through to
`spooler.dismiss`, the history drops it, and `job_dismissed` goes out on the
stream. But the panel only listened for `job_created` / `job_finished` /
`job_updated`, and terminal jobs live in `jobsMap` for the whole session, so the
row stayed on screen until a reload. The server was right and the screen was
stale, which is the hardest kind of "it does not work" to see.

So: every job event the spooler pushes has to be handled by the screen.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPOOLER = ROOT / "backend/app/spooler/spooler.py"
APP = ROOT / "frontend/src/App.vue"


def _pushed() -> set[str]:
    src = SPOOLER.read_text(encoding="utf-8")
    return set(re.findall(r'_push_event\(\s*"([a-z_]+)"', src))


def _listened() -> set[str]:
    src = APP.read_text(encoding="utf-8")
    return set(re.findall(r"addEventListener\(\s*'([a-z_]+)'", src))


def test_every_job_event_the_spooler_pushes_is_handled():
    pushed = {e for e in _pushed() if e.startswith("job_")}
    assert len(pushed) >= 4, f"押し出しが読めていない（{sorted(pushed)}）"

    missing = sorted(pushed - _listened())
    assert not missing, f"送っているのに画面が聞いていない: {missing}"


def test_dismissing_takes_the_job_out_of_the_map():
    """Not just heard — removed. Leaving it in the map is the original defect."""
    src = APP.read_text(encoding="utf-8")
    at = src.find("addEventListener('job_dismissed'")
    assert at > 0
    handler = src[at:at + 700]
    assert ".delete(job.id)" in handler, "一覧から取り除いていない"

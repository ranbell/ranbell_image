"""**The note under a job's title travels as a key.** (2026-09-20)

`progress_text` is written by whoever reports, in whatever language that file was
written in — so the Showrunner, who runs the console in English, read
「日記を書いてもらっています」 there while a Japanese reader got "Waiting in the
ComfyUI queue…". The report now names a line in `jobProgress.*` and carries its
placeholders; the text stays as the fallback for a job that names no key.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.spooler.models import Job, JobLane, ProgressReporter


def _job_and_reporter():
    pushed: list[str] = []
    job = Job(id="prompt-000001", lane=JobLane.PROMPT, title="generate_actress_diary")
    return job, ProgressReporter(job, lambda ev, j: pushed.append(ev)), pushed


def test_the_key_and_its_holes_reach_the_screen():
    job, reporter, pushed = _job_and_reporter()

    reporter.update(0.3, "3/9 files", key="files", done=3, total=9)

    assert job.progress_key == "files"
    assert job.progress_params == {"done": 3, "total": 9}
    out = job.to_dict()
    assert out["progress_key"] == "files"
    assert out["progress_params"] == {"done": 3, "total": 9}
    assert pushed == ["job_updated"]


def test_the_text_survives_as_the_fallback():
    """A client that does not know the key still reads something."""
    job, reporter, _ = _job_and_reporter()

    reporter.update(0.3, "3/9 files", key="files", done=3, total=9)

    assert job.progress_text == "3/9 files"


def test_a_report_with_no_key_is_still_a_report():
    """Nothing is required to name one — the older shape keeps working."""
    job, reporter, _ = _job_and_reporter()

    reporter.update(0.5, "halfway")

    assert job.progress_key == ""
    assert job.progress_params == {}
    assert job.progress_text == "halfway"


def test_a_new_report_clears_the_previous_key():
    """Otherwise a keyless report would render the note before it."""
    job, reporter, _ = _job_and_reporter()

    reporter.update(0.3, "3/9 files", key="files", done=3, total=9)
    reporter.update(0.9, "almost")

    assert job.progress_key == ""
    assert job.progress_params == {}

"""**Every job the spooler takes has to be stoppable.** (2026-11-02)

The Showrunner: "check whether there is any way a task cannot be cancelled
after it has been submitted to the spooler console — across every submission
path."

Cancelling is cooperative by design — `spooler.cancel()` sets the cancel event
and calls the `on_cancel` handlers, and never cancels the asyncio task, because
killing a task mid-write would leave the library half updated. The price is that
a runner which never looks at the token cannot be stopped at all: the audit found
11 of 47 like that, including four backfills that walk the whole library.

So this reads every runner handed to `spooler.submit` and requires that it
either watches the token itself or hands the work to something that does. A job
added later without a way to stop it fails here rather than reaching the console
as a row that ignores its own cancel button.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend/app"

#: Watching the token directly, in any of the shapes the codebase uses.
WATCHES = re.compile(
    r"raise_if_set|_cancel_event|cancel\._event|on_cancel|pause_checkpoint"
    r"|cancel_fn|cancel=cancel|should_stop|_abort\b|JobCancelled"
)

#: Runners that hand the whole job to a helper which watches for them.
#: `_drive_scan` goes further and cancels the scanner task outright.
DELEGATES: dict[str, str] = {
    "run_scan_full": "_drive_scan",
    "run_scan_heal": "_drive_scan",
    "run_scan_sync": "_drive_scan",
    "run_board_job": "run_render",
    "run_shoot_job": "run_render",
}


def _sources() -> dict[str, str]:
    """Every function in the backend, by name, as source."""
    out: dict[str, str] = {}
    for path in sorted(APP.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(src, node)
                if body:
                    out.setdefault(node.name, body)
    return out


def _submitted() -> set[str]:
    """Every runner passed as the third argument of a `.submit(...)` call."""
    names: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 3:
                continue
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if called != "submit":
                continue
            third = node.args[2]
            if isinstance(third, ast.Name):
                names.add(third.id)
            elif isinstance(third, ast.Attribute):
                names.add(third.attr)
    return names


SOURCES = _sources()
SUBMITTED = sorted(_submitted())


def test_the_submitted_runners_are_readable():
    assert len(SUBMITTED) > 40, f"submit が読めていない（{len(SUBMITTED)}本）"


@pytest.mark.parametrize("runner", SUBMITTED)
def test_the_runner_can_be_stopped(runner):
    body = SOURCES.get(runner)
    assert body, f"{runner} の本体が見つからない"

    if WATCHES.search(body):
        return

    helper = DELEGATES.get(runner)
    assert helper, (
        f"{runner} はキャンセルトークンを見ていない。"
        "cancel.raise_if_set() を足すか、見てくれるヘルパーに委譲して "
        "DELEGATES に登録してください"
    )
    assert helper in body, f"{runner} が {helper} に委譲していない"
    assert WATCHES.search(SOURCES.get(helper, "")), (
        f"{runner} の委譲先 {helper} もキャンセルを見ていない"
    )


def test_the_four_library_wide_backfills_stop_between_pages():
    """They used to be one `await` over the whole library, with no way out."""
    qdrant = (APP / "db/qdrant_client.py").read_text(encoding="utf-8")
    for name in ("backfill_small_embeddings", "backfill_model_name",
                 "backfill_batch_category", "backfill_is_reference"):
        m = re.search(rf"async def {name}\(self, should_stop=None\)", qdrant)
        assert m, f"{name} が should_stop を取っていない"
        after = qdrant[m.end(): m.end() + 1200]
        assert "if should_stop is not None and should_stop():" in after, (
            f"{name} がページごとに停止を見ていない"
        )


def test_a_cancel_is_not_swallowed_by_a_broad_handler():
    """`JobCancelled` is an `Exception`, so `except Exception` eats it.

    The lounge jobs wrap their LLM call in `except Exception: return failed`,
    which turned a cancel into a failure and let the job carry on.
    """
    shared = (APP / "muse/shared.py").read_text(encoding="utf-8")
    guards = shared.count("cancel.raise_if_set()")
    assert guards >= 6, f"楽屋ジョブのキャンセル確認が足りない（{guards}）"
    assert shared.count("except JobCancelled:") >= guards, (
        "広い except が JobCancelled を飲み込む"
    )

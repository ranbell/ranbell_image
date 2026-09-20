"""ImageDirectoryWatcher must not re-trigger scan_heal for files it already
registered synchronously (save_generated_image() already did that), but must
still catch anything it didn't: manual copies, registration failures, and
deletes/moves under generated_images_dir (previously silently dropped —
event_type wasn't checked at all for anything but "created").
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

for _name in ("sklearn", "sklearn.cluster"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["sklearn.cluster"].KMeans = object

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest


def _install_fake_job_modules(monkeypatch):
    """_dispatch_loop imports these lazily; stub them so importing the real
    jobs.runners module (heavy, many transitive deps) isn't required."""
    runners_mod = types.ModuleType("app.jobs.runners")
    runners_mod.run_pipeline_tagging = object()
    runners_mod.run_scan_heal = object()
    monkeypatch.setitem(sys.modules, "app.jobs.runners", runners_mod)

    models_mod = types.ModuleType("app.spooler.models")

    class JobLane:
        SYNC = "sync"
        TAGGING = "tagging"

    models_mod.JobLane = JobLane
    monkeypatch.setitem(sys.modules, "app.spooler.models", models_mod)


class FakeSpooler:
    def __init__(self):
        self.submitted: list[tuple] = []

    def submit(self, lane, title, func, **kw):
        self.submitted.append((lane, title))


def _make_watcher(spooler, *, debounce_seconds: float = 0.05):
    from app.scanner.watcher import ImageDirectoryWatcher

    w = ImageDirectoryWatcher(db=object(), ollama=object(), spooler=spooler,
                               debounce_seconds=debounce_seconds)
    w._generated_dir = Path("/fake/generated")
    return w


async def _run_dispatch_briefly(watcher, events: list[tuple[str, Path]], *, wait: float):
    for evt in events:
        watcher._event_queue.put_nowait(evt)
    task = asyncio.create_task(watcher._dispatch_loop())
    await asyncio.sleep(wait)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_self_registered_success_does_not_trigger_scan_heal(monkeypatch):
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    path = Path("/fake/generated/muse_board_1.png")
    scanner._self_registered.add(path)

    await _run_dispatch_briefly(watcher, [("created", path)], wait=3.2)

    titles = [t for _, t in spooler.submitted]
    assert "scan_heal" not in titles
    assert "ai_tagging_auto" in titles


@pytest.mark.asyncio
async def test_unregistered_created_file_triggers_scan_heal(monkeypatch):
    """A path with no self-registration record — manual copy or a failed
    register_image() call — must still fall back to scan_heal."""
    _install_fake_job_modules(monkeypatch)

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    path = Path("/fake/generated/manually_copied.png")  # never self-registered

    await _run_dispatch_briefly(watcher, [("created", path)], wait=3.2)

    titles = [t for _, t in spooler.submitted]
    assert "scan_heal" in titles


@pytest.mark.asyncio
async def test_mixed_batch_triggers_scan_heal_for_the_unregistered_one(monkeypatch):
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    registered = Path("/fake/generated/self_registered.png")
    unregistered = Path("/fake/generated/manual_copy.png")
    scanner._self_registered.add(registered)

    await _run_dispatch_briefly(
        watcher, [("created", registered), ("created", unregistered)], wait=3.2,
    )

    titles = [t for _, t in spooler.submitted]
    assert "scan_heal" in titles
    assert not scanner.consume_self_registered(registered), "mark should be consumed"


@pytest.mark.asyncio
async def test_deleted_event_under_generated_dir_triggers_scan_heal(monkeypatch):
    """Previously silently dropped: only event_type == "created" was handled
    for generated_dir, so a manual delete/rename there never reached scan_heal
    on its own."""
    _install_fake_job_modules(monkeypatch)

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    path = Path("/fake/generated/removed.png")

    await _run_dispatch_briefly(watcher, [("deleted", path)], wait=3.2)

    titles = [t for _, t in spooler.submitted]
    assert "scan_heal" in titles
    # A pure deletion shouldn't trigger tagging.
    assert "ai_tagging_auto" not in titles


@pytest.mark.asyncio
async def test_moved_event_under_generated_dir_triggers_scan_heal(monkeypatch):
    _install_fake_job_modules(monkeypatch)

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    path = Path("/fake/generated/renamed_to.png")

    await _run_dispatch_briefly(watcher, [("moved", path)], wait=3.2)

    titles = [t for _, t in spooler.submitted]
    assert "scan_heal" in titles


@pytest.mark.asyncio
async def test_source_dir_events_are_unaffected_by_the_generated_dir_change(monkeypatch):
    """Regression guard: source_images_dir keeps triggering scan_heal on any
    event type after its own (independent) debounce, unconditionally."""
    _install_fake_job_modules(monkeypatch)

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler, debounce_seconds=0.05)
    path = Path("/fake/source/reference.png")  # not under generated_dir

    # The dispatch loop's deadline check only runs once per 1s poll tick
    # (inside the asyncio.wait_for timeout branch), regardless of how short
    # debounce_seconds is — so even a near-zero debounce needs >1s of margin.
    await _run_dispatch_briefly(watcher, [("deleted", path)], wait=1.3)

    titles = [t for _, t in spooler.submitted]
    assert "scan_heal" in titles

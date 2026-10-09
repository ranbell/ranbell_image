"""ImageDirectoryWatcher hands changed directories to scan_sync — never a
whole-library heal — and leaves out files it already registered synchronously
(save_generated_image() did that), while still catching what it didn't: manual
copies, registration failures, deletes/moves, folders moved in or out.
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
    runners_mod.run_scan_sync = object()
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


@pytest.fixture(autouse=True)
def _fresh_pending(monkeypatch):
    from app.scanner import scanner
    scanner._pending_dirs.clear()
    monkeypatch.setattr(scanner, "_sync_job_waiting", False)
    yield
    scanner._pending_dirs.clear()


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
async def test_self_registered_success_does_not_trigger_a_sync(monkeypatch):
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    path = Path("/fake/generated/muse_board_1.png")
    scanner._self_registered.add(path)

    await _run_dispatch_briefly(watcher, [("created", path)], wait=3.2)

    titles = [t for _, t in spooler.submitted]
    assert "scan_sync" not in titles
    assert "scan_heal" not in titles
    assert "ai_tagging_auto" in titles


@pytest.mark.asyncio
async def test_unregistered_created_file_syncs_its_folder(monkeypatch):
    """A path with no self-registration record — manual copy or a failed
    register_image() call — still gets its folder reconciled."""
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    path = Path("/fake/generated/sub/manually_copied.png")  # never self-registered

    await _run_dispatch_briefly(watcher, [("created", path)], wait=3.2)

    assert [t for _, t in spooler.submitted].count("scan_sync") == 1
    assert scanner._pending_dirs == {"/fake/generated/sub"}


@pytest.mark.asyncio
async def test_mixed_batch_syncs_only_for_the_unregistered_one(monkeypatch):
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)
    registered = Path("/fake/generated/a/self_registered.png")
    unregistered = Path("/fake/generated/b/manual_copy.png")
    scanner._self_registered.add(registered)

    await _run_dispatch_briefly(
        watcher, [("created", registered), ("created", unregistered)], wait=3.2,
    )

    assert "scan_sync" in [t for _, t in spooler.submitted]
    assert scanner._pending_dirs == {"/fake/generated/b"}
    assert not scanner.consume_self_registered(registered), "mark should be consumed"


@pytest.mark.asyncio
async def test_folder_change_under_generated_dir_syncs_without_tagging(monkeypatch):
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler)

    await _run_dispatch_briefly(watcher, [("dir", Path("/fake/generated/x"))], wait=3.2)

    titles = [t for _, t in spooler.submitted]
    assert "scan_sync" in titles
    # A pure deletion/move shouldn't trigger tagging.
    assert "ai_tagging_auto" not in titles
    assert scanner._pending_dirs == {"/fake/generated/x"}


@pytest.mark.asyncio
async def test_source_dir_events_sync_after_their_own_debounce(monkeypatch):
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler, debounce_seconds=0.05)

    # The dispatch loop's deadline check only runs once per 1s poll tick
    # (inside the asyncio.wait_for timeout branch), regardless of how short
    # debounce_seconds is — so even a near-zero debounce needs >1s of margin.
    await _run_dispatch_briefly(watcher, [
        ("dir", Path("/fake/source/artworks")),
        ("created", Path("/fake/source/photos/new.png")),
    ], wait=1.3)

    assert [t for _, t in spooler.submitted] == ["scan_sync"]
    assert scanner._pending_dirs == {"/fake/source/artworks", "/fake/source/photos"}


@pytest.mark.asyncio
async def test_a_waiting_sync_absorbs_later_changes(monkeypatch):
    """Not a second job: the one still waiting to start picks these up."""
    _install_fake_job_modules(monkeypatch)
    from app.scanner import scanner

    spooler = FakeSpooler()
    watcher = _make_watcher(spooler, debounce_seconds=0.05)
    await _run_dispatch_briefly(watcher, [("dir", Path("/fake/source/a"))], wait=1.3)
    await _run_dispatch_briefly(watcher, [("dir", Path("/fake/source/b"))], wait=1.3)

    assert [t for _, t in spooler.submitted] == ["scan_sync"]
    assert scanner._pending_dirs == {"/fake/source/a", "/fake/source/b"}


def test_handler_turns_every_structural_change_into_its_parent_folder():
    from app.scanner.watcher import _ImageEventHandler

    got: list = []

    class Loop:
        def call_soon_threadsafe(self, fn, item):
            fn(item)

    h = _ImageEventHandler(Loop(), types.SimpleNamespace(put_nowait=got.append))
    ev = lambda **kw: types.SimpleNamespace(**{"is_directory": False, "dest_path": "", **kw})  # noqa: E731

    h.on_closed(ev(src_path="/r/a/1.png"))
    h.on_closed(ev(src_path="/r/a/notes.txt"))
    h.on_deleted(ev(src_path="/r/a/2.png"))
    h.on_moved(ev(src_path="/r/a/.syncthing.3.png.tmp", dest_path="/r/b/3.png"))
    h.on_created(types.SimpleNamespace(is_directory=True, src_path="/r/new"))
    h.on_deleted(types.SimpleNamespace(is_directory=True, src_path="/r/old", dest_path=""))

    assert got == [
        ("created", Path("/r/a/1.png")),
        ("dir", Path("/r/a")),
        ("dir", Path("/r/a")), ("dir", Path("/r/b")),
        ("dir", Path("/r")),
        ("dir", Path("/r")),
    ]

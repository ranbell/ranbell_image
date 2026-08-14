"""run_heal() should be near-instant when nothing changed since the last check.

Before this fix it unconditionally paid two full Qdrant scrolls (dedup check +
path/mtime index) plus a full filesystem walk on every call, regardless of
whether anything needed healing. The short-circuit compares (disk_count,
db_count) against the *previous* check rather than against each other
directly — an absolute disk==db comparison would never match (and never
short-circuit) once the library has any content-duplicate file (same sha256
at two paths), since Qdrant collapses those to one point.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# scanner.py -> api.images -> ai.color_extractor wants scikit-learn, which
# isn't on this test path; stub it rather than install a clustering library.
for _name in ("sklearn", "sklearn.cluster"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["sklearn.cluster"].KMeans = object

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.scanner import scanner


class FakeDb:
    def __init__(self, *, total_count: int = 0):
        self._total_count = total_count
        self.dedup_calls = 0
        self.index_calls = 0
        self.docs: dict[str, dict] = {}

    async def total_count(self, *, exclude_drafts: bool = False) -> int:
        return self._total_count

    async def find_duplicate_path_sha256s(self) -> dict:
        self.dedup_calls += 1
        return {}

    async def find_path_mtime_index(self) -> dict:
        self.index_calls += 1
        return {}

    async def get(self, sha256: str):
        return self.docs.get(sha256)

    async def set_payload(self, sha256: str, updates: dict) -> None:
        self.docs.setdefault(sha256, {}).update(updates)

    async def delete(self, sha256: str) -> None:
        self.docs.pop(sha256, None)

    async def upsert_new(self, sha256: str, payload: dict) -> None:
        self.docs[sha256] = payload


@pytest.fixture(autouse=True)
def _reset_heal_state(monkeypatch):
    # run_heal()/scan_state are module-level singletons; isolate each test.
    monkeypatch.setattr(scanner, "_last_heal_counts", None)
    monkeypatch.setattr(scanner, "_legacy_shoot_reclass_done", False)
    scanner.scan_state.reset("heal")
    scanner.scan_state.running = False
    monkeypatch.setattr(scanner, "invalidate_image_caches", lambda: None)


def _make_files(tmp_path: Path, n: int) -> list[Path]:
    files = []
    for i in range(n):
        p = tmp_path / f"file_{i}.png"
        p.write_bytes(b"\x89PNG\r\n")
        files.append(p)
    return files


async def _noop_process_image(path, db) -> None:
    # run_heal() itself increments scan_state.processed/added/updated around
    # the call site — this stand-in only needs to skip the real hash/extract/
    # thumbnail work, not touch scan_state.
    pass


@pytest.mark.asyncio
async def test_first_call_never_short_circuits(tmp_path, monkeypatch):
    files = _make_files(tmp_path, 3)
    monkeypatch.setattr(scanner, "_collect_all_files", lambda: files)
    monkeypatch.setattr(scanner, "_process_image", _noop_process_image)
    db = FakeDb(total_count=3)

    await scanner.run_heal(db)

    assert db.dedup_calls == 1
    assert db.index_calls == 1
    assert scanner._last_heal_counts == (3, 3)


@pytest.mark.asyncio
async def test_unchanged_counts_short_circuit_on_second_call(tmp_path, monkeypatch):
    files = _make_files(tmp_path, 5)
    monkeypatch.setattr(scanner, "_collect_all_files", lambda: files)
    monkeypatch.setattr(scanner, "_process_image", _noop_process_image)
    db = FakeDb(total_count=5)

    await scanner.run_heal(db)
    assert db.dedup_calls == 1
    assert db.index_calls == 1

    await scanner.run_heal(db)

    assert db.dedup_calls == 1, "second call must not re-scroll for dedup"
    assert db.index_calls == 1, "second call must not re-scroll for mtime index"
    assert scanner.scan_state.skipped == 5


@pytest.mark.asyncio
async def test_content_duplicate_skew_still_short_circuits(tmp_path, monkeypatch):
    """disk has 2 more files than Qdrant (a duplicate-content pair collapsed to
    one point) and that gap never closes — an absolute disk==db comparison
    would never short-circuit here. The differential comparison should, once
    the skewed pair repeats.
    """
    files = _make_files(tmp_path, 7)
    monkeypatch.setattr(scanner, "_collect_all_files", lambda: files)
    monkeypatch.setattr(scanner, "_process_image", _noop_process_image)
    db = FakeDb(total_count=5)  # permanently 2 less than disk_count

    await scanner.run_heal(db)  # first call: never short-circuits
    assert db.dedup_calls == 1
    assert db.index_calls == 1

    await scanner.run_heal(db)  # second call: same skewed pair as before

    assert db.dedup_calls == 1, "stable duplicate skew must still short-circuit"
    assert db.index_calls == 1
    assert scanner.scan_state.skipped == 7


@pytest.mark.asyncio
async def test_count_change_forces_full_processing(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "_process_image", _noop_process_image)
    files3 = _make_files(tmp_path, 3)
    monkeypatch.setattr(scanner, "_collect_all_files", lambda: files3)
    db = FakeDb(total_count=3)
    await scanner.run_heal(db)
    assert db.index_calls == 1

    # A file was added: disk_count moves relative to the last check.
    files4 = files3 + _make_files(tmp_path, 1)
    monkeypatch.setattr(scanner, "_collect_all_files", lambda: files4)

    await scanner.run_heal(db)

    assert db.index_calls == 2, "count change must not be short-circuited"
    assert scanner.scan_state.added == 4  # none of the fake paths are "known"


@pytest.mark.asyncio
async def test_short_circuit_reports_zero_added_so_tagging_chain_stays_quiet(
    tmp_path, monkeypatch,
):
    """run_scan_heal only auto-chains AI tagging when scan_state.added > 0
    (jobs/runners.py) — a short-circuited heal must leave that at 0.
    """
    files = _make_files(tmp_path, 2)
    monkeypatch.setattr(scanner, "_collect_all_files", lambda: files)
    monkeypatch.setattr(scanner, "_process_image", _noop_process_image)
    db = FakeDb(total_count=2)

    await scanner.run_heal(db)  # baseline
    await scanner.run_heal(db)  # short-circuits

    assert scanner.scan_state.added == 0
    assert scanner.scan_state.updated == 0
    assert scanner.scan_state.deleted == 0

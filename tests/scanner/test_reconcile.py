"""The scanner reads a file only when it has to, and lists a folder only when
its mtime moved.

Real files in tmp_path; Qdrant is a dict that behaves like it does for the
calls the scanner makes (set_payload replaces the top-level keys it names).
Reads, listings and folder stats are counted by wrapping walk's functions as
scanner sees them.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import types
from pathlib import Path

# scanner.py -> api.images -> ai.color_extractor wants scikit-learn, which
# isn't on this test path; stub it rather than install a clustering library.
for _name in ("sklearn", "sklearn.cluster"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["sklearn.cluster"].KMeans = object

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from PIL import Image

from app.config import settings
from app.scanner import scanner, walk

PAST = 1_600_000_000  # an mtime well outside walk.RACY_NS


class FakeDb:
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.dirs: dict[str, dict] = {}
        self.meta: dict = {}
        self.scrolls = 0
        self.barriers = 0

    # images
    async def total_count(self, *, exclude_drafts: bool = False) -> int:
        return len(self.docs)

    async def find_by_dirs(self, dirs):
        self.scrolls += 1
        want = set(dirs)
        return [
            dict(d) for d in self.docs.values()
            if d.get("dir") in want or want & set(d.get("dup_dirs") or ())
        ]

    async def get_locations(self, shas):
        return {s: dict(self.docs[s]) for s in shas if s in self.docs}

    async def get(self, sha):
        return self.docs.get(sha)

    async def upsert_new_batch(self, items, *, wait=True):
        for sha, payload in items:
            self.docs[sha] = dict(payload)

    async def set_payload_batch(self, items, *, wait=True):
        for sha, upd in items:
            if sha in self.docs:
                self.docs[sha].update(upd)

    async def delete_batch(self, shas, *, wait=True):
        for s in shas:
            self.docs.pop(s, None)

    async def images_barrier(self):
        self.barriers += 1

    async def backfill_image_dirs(self):
        n = 0
        for d in self.docs.values():
            if "dir" not in d:
                d["dir"] = os.path.dirname(d.get("path", ""))
                n += 1
        return n

    async def distinct_image_dirs(self):
        out = set()
        for d in self.docs.values():
            out.add(d.get("dir"))
            out.update(d.get("dup_dirs") or ())
        return out

    # scan_dirs
    async def load_scan_dirs(self):
        return {k: dict(v) for k, v in self.dirs.items()}, dict(self.meta)

    async def put_scan_dirs(self, entries):
        for e in entries:
            self.dirs[e["path"]] = dict(e)

    async def delete_scan_dirs(self, paths):
        for p in paths:
            self.dirs.pop(p, None)

    async def put_scan_meta(self, meta):
        self.meta = dict(meta)

    async def clear_scan_dirs(self):
        self.dirs.clear()

    # by path, for assertions
    def at(self, path) -> dict | None:
        path = str(path)
        hits = [d for d in self.docs.values() if d["path"] == path]
        assert len(hits) <= 1
        return hits[0] if hits else None


class Counter:
    def __init__(self):
        self.reads: list[str] = []
        self.listed: list[str] = []
        self.dir_stats = 0


@pytest.fixture
def lib(tmp_path, monkeypatch):
    src = tmp_path / "source"
    gen = tmp_path / "generated"
    for d in (src, gen):
        d.mkdir()
    monkeypatch.setattr(settings, "source_images_dir", src)
    monkeypatch.setattr(settings, "generated_images_dir", gen)
    monkeypatch.setattr(settings, "thumbnails_dir", tmp_path / "thumbs")
    monkeypatch.setattr(scanner, "invalidate_image_caches", lambda: None)
    monkeypatch.setattr(scanner, "_legacy_shoot_reclass_done", True)
    monkeypatch.setattr(scanner, "_points_delta", 0)
    scanner._pending_dirs.clear()
    monkeypatch.setattr(scanner, "_sync_job_waiting", False)

    c = Counter()
    real_read, real_list, real_stat = walk.read_and_hash, walk.list_dir, walk.dir_mtime_ns

    def read(path):
        c.reads.append(path)
        return real_read(path)

    def listing(path):
        c.listed.append(path)
        return real_list(path)

    def dstat(path):
        c.dir_stats += 1
        return real_stat(path)

    monkeypatch.setattr(scanner, "read_and_hash", read)
    monkeypatch.setattr(scanner, "list_dir", listing)
    monkeypatch.setattr(scanner, "dir_mtime_ns", dstat)
    return types.SimpleNamespace(src=src, gen=gen, tmp=tmp_path, c=c, db=FakeDb())


def png(path: Path, color: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (color % 256, (color // 256) % 256, 7)).save(path, "PNG")
    os.utime(path, ns=(PAST * 10**9 + color, PAST * 10**9 + color))
    return path


_settles = iter(range(1, 10**6))


def settle(*roots: Path) -> None:
    """Age the folders touched since the last settle out of walk.RACY_NS.

    Each call moves them to a different past second, so a folder that changed
    still reads as changed — as it would on a real disk, just not "too recent".
    """
    when = PAST + next(_settles)
    recent = time.time() - 60
    for root in roots:
        for d, _, _ in os.walk(root):
            if os.stat(d).st_mtime > recent:
                os.utime(d, (when, when))


def reset(c: Counter) -> None:
    c.reads.clear()
    c.listed.clear()
    c.dir_stats = 0


async def heal(lib, **kw):
    reset(lib.c)
    await scanner.run_heal(lib.db, **kw)
    return scanner.scan_state


# ── The cheap path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_heal_lists_nothing_and_reads_nothing(lib):
    png(lib.src / "a" / "1.png", 1)
    png(lib.src / "a" / "2.png", 2)
    png(lib.src / "b" / "3.png", 3)
    settle(lib.src, lib.gen)

    st = await heal(lib)
    assert st.added == 3
    assert sorted(lib.c.reads) == sorted(str(p) for p in lib.src.rglob("*.png"))

    st = await heal(lib)
    assert lib.c.reads == []
    assert lib.c.listed == [], "unchanged folders must not be listed"
    assert lib.c.dir_stats == len(lib.db.dirs)
    assert (st.added, st.updated, st.deleted) == (0, 0, 0)


@pytest.mark.asyncio
async def test_new_folder_lists_only_its_parent_and_itself(lib):
    png(lib.src / "a" / "1.png", 1)
    png(lib.src / "b" / "2.png", 2)
    settle(lib.src, lib.gen)
    await heal(lib)

    png(lib.src / "new" / "x.png", 10)
    png(lib.src / "new" / "deep" / "y.png", 11)
    settle(lib.src)
    st = await heal(lib)

    assert sorted(lib.c.listed) == sorted([
        str(lib.src), str(lib.src / "new"), str(lib.src / "new" / "deep"),
    ])
    assert sorted(lib.c.reads) == sorted([str(lib.src / "new" / "x.png"),
                                          str(lib.src / "new" / "deep" / "y.png")])
    assert st.added == 2


@pytest.mark.asyncio
async def test_each_new_file_is_read_exactly_once(lib, monkeypatch):
    opened: list[str] = []
    real_open = open

    def counting_open(path, *a, **kw):
        if str(path).endswith(".png"):
            opened.append(str(path))
        return real_open(path, *a, **kw)

    png(lib.src / "a" / "1.png", 1)
    settle(lib.src, lib.gen)
    monkeypatch.setattr("builtins.open", counting_open)
    await heal(lib)
    assert opened == [str(lib.src / "a" / "1.png")]
    doc = lib.db.at(lib.src / "a" / "1.png")
    assert doc["dir"] == str(lib.src / "a")
    assert doc["mtime_ns"] == os.stat(lib.src / "a" / "1.png").st_mtime_ns
    assert (settings.thumbnails_dir / doc["sha256"][:2] / doc["sha256"][2:4]
            / f"{doc['sha256']}.webp").exists()


# ── Without reading ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rename_and_folder_move_are_not_read(lib):
    png(lib.src / "a" / "1.png", 1)
    png(lib.src / "a" / "sub" / "2.png", 2)
    settle(lib.src, lib.gen)
    await heal(lib)
    sha1 = lib.db.at(lib.src / "a" / "1.png")["sha256"]
    lib.db.docs[sha1]["star_rating"] = 4

    os.rename(lib.src / "a" / "1.png", lib.src / "a" / "renamed.png")
    (lib.src / "b").mkdir()
    shutil.move(str(lib.src / "a" / "sub"), str(lib.src / "b" / "sub"))
    settle(lib.src)
    st = await heal(lib)

    assert lib.c.reads == []
    assert st.moved == 2
    assert lib.db.at(lib.src / "a" / "renamed.png")["sha256"] == sha1
    assert lib.db.docs[sha1]["star_rating"] == 4
    assert lib.db.at(lib.src / "b" / "sub" / "2.png")["dir"] == str(lib.src / "b" / "sub")
    assert str(lib.src / "a" / "sub") not in lib.db.dirs
    assert len(lib.db.docs) == 2


@pytest.mark.asyncio
async def test_deleted_folder_drops_its_images_without_reading(lib):
    png(lib.src / "lib" / "a" / "1.png", 1)
    png(lib.src / "lib" / "gone" / "x" / "2.png", 2)
    settle(lib.src, lib.gen)
    await heal(lib)

    shutil.rmtree(lib.src / "lib" / "gone")
    settle(lib.src)
    st = await heal(lib)
    assert lib.c.reads == []
    assert st.deleted == 1
    assert lib.db.at(lib.src / "lib" / "gone" / "x" / "2.png") is None
    assert not any(p.startswith(str(lib.src / "lib" / "gone")) for p in lib.db.dirs)


# ── Same bytes at two paths ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_copies_settle_and_stop_being_read(lib):
    first = png(lib.src / "a" / "1.png", 1)
    copy = lib.src / "b" / "copy.png"
    copy.parent.mkdir()
    shutil.copy2(first, copy)
    settle(lib.src, lib.gen)

    await heal(lib)
    assert len(lib.db.docs) == 1
    doc = next(iter(lib.db.docs.values()))
    assert {doc["path"], *(d["path"] for d in doc["dup_paths"])} == {str(first), str(copy)}
    primary = doc["path"]

    for _ in range(2):
        await heal(lib, strict=True)
        assert lib.c.reads == [], "a known copy must not be re-read"
        assert next(iter(lib.db.docs.values()))["path"] == primary, "path must not flip"


@pytest.mark.asyncio
async def test_removing_the_primary_promotes_the_copy(lib):
    first = png(lib.src / "lib" / "a" / "1.png", 1)
    copy = lib.src / "lib" / "b" / "copy.png"
    copy.parent.mkdir()
    shutil.copy2(first, copy)
    settle(lib.src, lib.gen)
    await heal(lib)
    doc = next(iter(lib.db.docs.values()))
    primary, other = doc["path"], doc["dup_paths"][0]["path"]

    os.remove(primary)
    settle(lib.src)
    st = await heal(lib)
    doc = next(iter(lib.db.docs.values()))
    assert doc["path"] == other
    assert doc["dup_paths"] == [] and doc["dup_dirs"] == []
    assert st.deleted == 0 and lib.c.reads == []


@pytest.mark.asyncio
async def test_content_replaced_at_same_path_carries_the_star(lib):
    p = png(lib.src / "a" / "1.png", 1)
    settle(lib.src, lib.gen)
    await heal(lib)
    old = lib.db.at(p)["sha256"]
    lib.db.docs[old]["star_rating"] = 5

    os.remove(p)
    png(p, 999)
    settle(lib.src)
    await heal(lib)
    assert lib.c.reads == [str(p)]
    new = lib.db.at(p)
    assert new["sha256"] != old and old not in lib.db.docs
    assert new["star_rating"] == 5


# ── Safety nets ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_points_removed_behind_the_scanner_turn_the_next_heal_strict(lib):
    png(lib.src / "a" / "1.png", 1)
    png(lib.src / "a" / "2.png", 2)
    settle(lib.src, lib.gen)
    await heal(lib)

    # e.g. delete_daily_oracle: the point goes, the file stays, no mtime moves
    victim = lib.db.at(lib.src / "a" / "2.png")["sha256"]
    del lib.db.docs[victim]
    st = await heal(lib)
    assert lib.c.reads == [str(lib.src / "a" / "2.png")]
    assert st.added == 1


@pytest.mark.asyncio
async def test_stale_points_in_vanished_folders_are_dropped_by_strict(lib):
    png(lib.src / "a" / "1.png", 1)
    settle(lib.src, lib.gen)
    await heal(lib)
    # A point Qdrant has for a folder the snapshot never saw (deleted while
    # the app was down, before the snapshot existed).
    lib.db.docs["f" * 64] = {"sha256": "f" * 64, "path": str(lib.src / "old" / "z.png"),
                             "dir": str(lib.src / "old"), "size": 1, "mtime": ""}
    lib.db.dirs.clear()
    await heal(lib)
    assert "f" * 64 not in lib.db.docs


@pytest.mark.asyncio
async def test_unreadable_file_keeps_its_folder_coming_back(lib, monkeypatch):
    png(lib.src / "a" / "1.png", 1)
    bad = lib.src / "a" / "broken.png"
    bad.write_bytes(b"not a png")
    os.utime(bad, (PAST, PAST))
    settle(lib.src, lib.gen)
    st = await heal(lib)
    assert st.errors == 1
    assert lib.db.dirs[str(lib.src / "a")]["mtime_ns"] == -1

    await heal(lib)
    assert str(lib.src / "a") in lib.c.listed
    assert lib.c.reads == [str(bad)], "only the failing file is tried again"


@pytest.mark.asyncio
async def test_folder_touched_while_listing_is_listed_again(lib):
    png(lib.src / "a" / "1.png", 1)
    settle(lib.src, lib.gen)
    os.utime(lib.src / "a")  # now — inside walk.RACY_NS
    await heal(lib)
    assert lib.db.dirs[str(lib.src / "a")]["mtime_ns"] == -1
    await heal(lib)
    assert str(lib.src / "a") in lib.c.listed


@pytest.mark.asyncio
async def test_old_points_without_mtime_ns_match_without_reading_or_writing(lib):
    p = png(lib.src / "a" / "1.png", 1)
    settle(lib.src, lib.gen)
    await heal(lib)
    doc = lib.db.at(p)
    del doc["mtime_ns"]
    lib.db.dirs.clear()

    writes = []
    real = lib.db.set_payload_batch

    async def counting(items, **kw):
        writes.extend(items)
        await real(items, **kw)

    lib.db.set_payload_batch = counting
    st = await heal(lib)
    assert lib.c.reads == []
    assert writes == [] and st.updated == 0


# ── Watcher-driven sync ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_touches_only_the_requested_folder(lib):
    png(lib.src / "a" / "1.png", 1)
    png(lib.src / "b" / "2.png", 2)
    settle(lib.src, lib.gen)
    await heal(lib)

    png(lib.src / "b" / "3.png", 3)
    assert scanner.request_sync({str(lib.src / "b")}) is True
    assert scanner.request_sync({str(lib.src / "b")}) is False, "one waiting job is enough"
    reset(lib.c)
    await scanner.run_sync(lib.db)
    assert lib.c.listed == [str(lib.src / "b")]
    assert lib.c.reads == [str(lib.src / "b" / "3.png")]
    assert lib.c.dir_stats == 0
    assert scanner.request_sync({str(lib.src / "a")}) is True, "a new job after the last one started"


@pytest.mark.asyncio
async def test_register_image_is_seen_as_unchanged_by_the_next_pass(lib):
    settle(lib.src, lib.gen)
    await heal(lib)
    p = lib.gen / "muse" / "shot.png"
    png(p, 42)
    data = p.read_bytes()
    reset(lib.c)
    sha = await scanner.register_image(p, lib.db, data=data)
    assert lib.c.reads == [], "the saver's bytes are used, the file is not read back"
    assert scanner.consume_self_registered(p)
    assert lib.db.docs[sha]["path"] == str(p)

    settle(lib.gen)
    st = await heal(lib)
    assert lib.c.reads == []
    assert st.added == 0


# ── A mount that is not there right now ────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_emptied_mount_keeps_its_images_until_a_strict_check(lib):
    """docker creates a missing bind-mount source as an empty folder; a NAS
    can be late at boot. Neither is "every image in it was deleted"."""
    png(lib.src / "nas" / "1.png", 1)
    png(lib.src / "nas" / "sub" / "2.png", 2)
    settle(lib.src, lib.gen)
    await heal(lib)

    shutil.rmtree(lib.src / "nas")
    (lib.src / "nas").mkdir()          # comes back empty
    settle(lib.src)
    st = await heal(lib)
    assert st.deleted == 0 and len(lib.db.docs) == 2

    (lib.src / "nas").rmdir()          # or not at all
    settle(lib.src)
    st = await heal(lib)
    assert st.deleted == 0 and len(lib.db.docs) == 2

    # A count change behind the scanner turns heal strict on its own — still kept.
    lib.db.meta["points"] = 99
    st = await heal(lib)
    assert st.deleted == 0 and len(lib.db.docs) == 2

    st = await heal(lib, strict=True)  # the admin screen's strict check
    assert st.deleted == 2 and lib.db.docs == {}


@pytest.mark.asyncio
async def test_a_missing_root_keeps_its_images(lib):
    png(lib.gen / "muse" / "1.png", 1)
    settle(lib.src, lib.gen)
    await heal(lib)

    shutil.rmtree(lib.gen)
    st = await heal(lib)
    assert st.deleted == 0 and len(lib.db.docs) == 1


@pytest.mark.asyncio
async def test_an_ordinary_folder_emptied_is_still_cleaned(lib):
    png(lib.src / "nas" / "album" / "1.png", 1)
    settle(lib.src, lib.gen)
    await heal(lib)

    os.remove(lib.src / "nas" / "album" / "1.png")
    settle(lib.src)
    st = await heal(lib)
    assert st.deleted == 1

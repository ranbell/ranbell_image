"""The lineage ledger: what it keeps, and what it refuses to overwrite.

The ledger exists for one failure mode. A heal scan deletes points whose files
are missing, so remounting the image directory somewhere else and scanning will
legitimately empty out most of the collection. A backup that mirrors the current
state copies that loss faithfully. This one is append-only, so it does not.

The other property that matters is that importing never overwrites. A value in
Qdrant is the live one; a value in the ledger is a record of an older one. That
ordering is what makes the import safe to run at any time without thinking.
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.backup.ledger import Ledger
from backend.app.backup.service import (
    _physical, collect_lineage, import_lineage, list_backups,
    run_lineage_backup, run_snapshots,
)
from backend.app.db.id_utils import sha256_to_point_id


class FakeQdrant:
    def __init__(self, images=None):
        self.points = {"images": dict(images or {})}
        self.snapshots: dict[str, list] = {}
        self.snapshot_seq = 0
        self.fail_snapshot_for: set[str] = set()
        self.recovered: list[tuple[str, str]] = []
        self.aliases: dict[str, str] = {}

    async def collection_exists(self, name):
        return (self.aliases.get(name, name) in self.points
                or name in self.points or name in self.snapshots)

    async def get_aliases(self):
        return SimpleNamespace(aliases=[
            SimpleNamespace(alias_name=a, collection_name=c)
            for a, c in self.aliases.items()
        ])

    async def scroll(self, collection_name, limit=500, with_payload=True,
                     with_vectors=False, offset=None):
        items = sorted(self.points.get(collection_name, {}).items())
        start = offset or 0
        page = items[start:start + limit]
        pts = [SimpleNamespace(id=pid, payload=dict(pl)) for pid, pl in page]
        nxt = start + limit if start + limit < len(items) else None
        return pts, nxt

    async def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        out = []
        for pid in ids:
            pl = self.points.get(collection_name, {}).get(pid)
            if pl is not None:
                out.append(SimpleNamespace(id=pid, payload=dict(pl)))
        return out

    async def set_payload(self, collection_name, payload, points, wait=True):
        for pid in points:
            self.points.setdefault(collection_name, {}).setdefault(pid, {}).update(payload)

    async def create_snapshot(self, collection_name, wait=True):
        if collection_name in self.fail_snapshot_for:
            raise RuntimeError("permission denied: /qdrant/snapshots")
        self.snapshot_seq += 1
        desc = SimpleNamespace(
            name=f"{collection_name}-{self.snapshot_seq:04d}.snapshot",
            creation_time=f"2026-08-{self.snapshot_seq:02d}T00:00:00Z",
            size=1024,
        )
        self.snapshots.setdefault(collection_name, []).append(desc)
        return desc

    async def list_snapshots(self, collection_name):
        return list(self.snapshots.get(collection_name, []))

    async def delete_snapshot(self, collection_name, snapshot_name, wait=True):
        self.snapshots[collection_name] = [
            s for s in self.snapshots.get(collection_name, []) if s.name != snapshot_name
        ]
        return True


class FakeDB:
    def __init__(self, fake):
        self._qc = fake


def sha_for(i: int) -> str:
    # Point ids come from the *first* 32 hex chars, so the digits have to vary
    # at the front or every fixture image collapses onto one point.
    return hashlib.sha256(str(i).encode()).hexdigest()


def image_point(i, *, genesis=True, rating=None):
    sha = sha_for(i)
    payload = {"sha256": sha, "path": f"/mnt/image/generated/{i}.png"}
    if genesis:
        payload["genesis"] = {"spirit": f"s{i}"}
        payload["creation_record"] = {"run": i}
    if rating is not None:
        payload["star_rating"] = rating
    return sha256_to_point_id(sha), payload


def seed_images(fake, n=3, **kw):
    for i in range(n):
        pid, payload = image_point(i, **kw)
        fake.points["images"][pid] = payload


# ── the ledger keeps what the database drops ────────────────────────────────

@pytest.mark.asyncio
async def test_lineage_survives_points_being_deleted(tmp_path):
    """The whole reason this is append-only.

    Remount the image directory somewhere else, run a heal scan, and the points
    go — correctly, as far as the scan is concerned. The ledger still has them.
    """
    fake = FakeQdrant()
    seed_images(fake, 3, rating=4)
    db = FakeDB(fake)

    await run_lineage_backup(db, tmp_path)

    fake.points["images"].clear()          # heal scan after a remount
    await run_lineage_backup(db, tmp_path)  # a later backup sees nothing

    entries = Ledger(tmp_path).read_all()
    assert len([k for k in entries if k[0] == "images"]) == 3
    sha0 = sha_for(0)
    assert entries[("images", sha0)]["payload"]["genesis"] == {"spirit": "s0"}
    assert entries[("images", sha0)]["payload"]["star_rating"] == 4


@pytest.mark.asyncio
async def test_only_changes_are_appended(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 3)
    db = FakeDB(fake)

    first = await run_lineage_backup(db, tmp_path)
    assert first["appended"] == 3

    second = await run_lineage_backup(db, tmp_path)
    assert second["appended"] == 0        # nothing moved

    pid, _ = image_point(1)
    fake.points["images"][pid]["star_rating"] = 5
    third = await run_lineage_backup(db, tmp_path)
    assert third["appended"] == 1         # just the one that changed


@pytest.mark.asyncio
async def test_images_without_lineage_are_not_recorded(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2, genesis=False)
    db = FakeDB(fake)
    assert (await run_lineage_backup(db, tmp_path))["appended"] == 0


@pytest.mark.asyncio
async def test_ledger_is_compressed_and_readable(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2)
    await run_lineage_backup(FakeDB(fake), tmp_path)

    files = Ledger(tmp_path).files()
    assert files and files[0].suffixes[-2:] == [".jsonl", ".gz"]
    with gzip.open(files[0], "rt", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert len(rows) == 2
    assert rows[0]["kind"] == "images"


@pytest.mark.asyncio
async def test_unwritable_directory_fails_loudly(tmp_path):
    """A backup nobody can write must not look like a backup that ran."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        with pytest.raises(RuntimeError, match="not writable"):
            await run_lineage_backup(FakeDB(FakeQdrant()), blocked)
    finally:
        blocked.chmod(0o700)


@pytest.mark.asyncio
async def test_a_torn_line_does_not_cost_the_rest_of_the_history(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2)
    await run_lineage_backup(FakeDB(fake), tmp_path)

    path = Ledger(tmp_path).files()[0]
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write('{"kind": "images", "id": "trunc')   # interrupted write

    entries = Ledger(tmp_path).read_all()
    assert len(entries) == 2


# ── importing never overwrites ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_fills_gaps_and_leaves_live_values_alone(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2, rating=3)
    db = FakeDB(fake)
    await run_lineage_backup(db, tmp_path)

    # The images come back from a heal scan without their provenance, and one
    # of them has been re-rated since.
    for pid in list(fake.points["images"]):
        payload = fake.points["images"][pid]
        fake.points["images"][pid] = {"sha256": payload["sha256"], "path": payload["path"]}
    pid1, _ = image_point(1)
    fake.points["images"][pid1]["star_rating"] = 5

    result = await import_lineage(db, tmp_path)

    assert result["restored"] == 2
    pid0, _ = image_point(0)
    assert fake.points["images"][pid0]["genesis"] == {"spirit": "s0"}
    assert fake.points["images"][pid0]["star_rating"] == 3
    # The newer rating is the live one and stays.
    assert fake.points["images"][pid1]["star_rating"] == 5


@pytest.mark.asyncio
async def test_import_skips_images_that_are_not_back_yet(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2)
    db = FakeDB(fake)
    await run_lineage_backup(db, tmp_path)
    fake.points["images"].clear()

    result = await import_lineage(db, tmp_path)

    assert result["restored"] == 0
    assert result["image_missing"] == 2


@pytest.mark.asyncio
async def test_import_is_idempotent(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2)
    db = FakeDB(fake)
    await run_lineage_backup(db, tmp_path)
    for pid in list(fake.points["images"]):
        payload = fake.points["images"][pid]
        fake.points["images"][pid] = {"sha256": payload["sha256"]}

    assert (await import_lineage(db, tmp_path))["restored"] == 2
    again = await import_lineage(db, tmp_path)
    assert again["restored"] == 0
    assert again["already_present"] == 2


# ── snapshots ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshots_rotate_to_the_configured_depth():
    fake = FakeQdrant()
    fake.points["images"] = {}
    db = FakeDB(fake)

    for _ in range(9):
        await run_snapshots(db, keep=7)

    assert len(fake.snapshots["images"]) == 7
    # The two oldest went, the newest stayed.
    assert fake.snapshots["images"][0].name.endswith("0003.snapshot")


@pytest.mark.asyncio
async def test_a_failing_snapshot_is_reported_not_swallowed():
    fake = FakeQdrant()
    fake.points["images"] = {}
    fake.points["app_config"] = {}
    fake.fail_snapshot_for = {"images"}

    result = await run_snapshots(FakeDB(fake), keep=7)

    assert "images" in result["failures"]
    assert "permission denied" in result["failures"]["images"]
    # The others still ran.
    assert any(s.startswith("app_config/") for s in result["created"])


@pytest.mark.asyncio
async def test_snapshots_are_keyed_by_the_physical_collection():
    """Qdrant files snapshots under the physical name, not the alias.

    Snapshotting through the `images` alias works, but the file lands in
    `snapshots/images_v3/`. Qdrant does create an empty `snapshots/images/`
    directory, so a restore path built from the alias points at somewhere that
    exists and is empty — which fails without looking like it should.
    """
    fake = FakeQdrant()
    fake.points["images_v3"] = {}
    fake.aliases = {"images": "images_v3"}
    db = FakeDB(fake)

    result = await run_snapshots(db, keep=7)
    listing = await list_backups(db, "/tmp")

    assert any(s.startswith("images_v3/") for s in result["created"]), result["created"]
    assert not any(s.startswith("images/") for s in result["created"])
    assert "images_v3" in listing["snapshots"]
    assert "images" not in listing["snapshots"]


@pytest.mark.asyncio
async def test_physical_passes_through_a_plain_collection_name():
    fake = FakeQdrant()
    fake.points["images"] = {}
    assert await _physical(FakeDB(fake), "images") == "images"


@pytest.mark.asyncio
async def test_whole_collections_are_kept_too(tmp_path):
    fake = FakeQdrant()
    fake.points["muse_sessions"] = {"s1": {"session_id": "s1", "mode": "duet"}}
    fake.points["character_presets"] = {"c1": {"name_ja": "みなも"}}
    db = FakeDB(fake)

    await run_lineage_backup(db, tmp_path)

    entries = Ledger(tmp_path).read_all()
    assert entries[("muse_sessions", "s1")]["payload"]["mode"] == "duet"
    assert entries[("character_presets", "c1")]["payload"]["name_ja"] == "みなも"


@pytest.mark.asyncio
async def test_collect_reports_a_manifest_that_suppresses_the_next_run(tmp_path):
    fake = FakeQdrant()
    seed_images(fake, 2)
    db = FakeDB(fake)
    ledger = Ledger(tmp_path)

    records, manifest = await collect_lineage(db, ledger)
    assert len(records) == 2
    ledger.append(records)
    ledger.save_manifest(manifest)

    records2, _ = await collect_lineage(db, ledger)
    assert records2 == []

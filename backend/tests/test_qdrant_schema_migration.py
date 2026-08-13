"""Schema handling for the images collection.

Two properties are load-bearing here, and both exist because a dimension change
in Qdrant means moving every point in the collection:

  1. **Startup never migrates.** It records what it found and serves. A process
     that connects to a database — any database, including one it was pointed
     at by mistake — must not rewrite the schema it finds there. Dimension
     changes are a deliberate admin action instead.

  2. **A migration builds beside the live data, never over it.** The new
     collection is filled and counted before the `images` alias moves to it, so
     an interrupted migration leaves the alias resolving to exactly the data it
     resolved to before.

Also covered: the recorded schema is seeded from the *live collection*, so
upgrading an existing install is a no-op rather than an invitation to migrate.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.config import settings
from backend.app.db.qdrant_client import (
    CONFIG_COLLECTION, CONFIG_POINT_ID, IMAGES_COLLECTION,
    IMAGES_COLOR_COLLECTION, SCHEMA_KEY, QdrantDBClient,
)


# ── in-memory Qdrant ────────────────────────────────────────────────────────

class Boom(Exception):
    """Stands in for SIGKILL: the process stops mid-migration."""


class FakeQdrant:
    """Collections, aliases and points, with enough of the client surface."""

    def __init__(self):
        self.collections: dict[str, dict] = {}   # name -> vectors_config
        self.points: dict[str, dict] = {}        # name -> {id: (payload, vectors)}
        self.aliases: dict[str, str] = {}        # alias -> collection
        self.indexes: dict[str, set] = {}
        self.die_on: str | None = None           # method name to raise Boom in
        self.calls: list[str] = []

    # -- helpers used by tests
    def seed(self, name: str, vectors_config: dict, points: dict) -> None:
        self.collections[name] = vectors_config
        self.points[name] = dict(points)
        self.indexes[name] = set()

    def resolve(self, name: str) -> str:
        return self.aliases.get(name, name)

    def _tick(self, method: str) -> None:
        self.calls.append(method)
        if self.die_on == method:
            raise Boom(method)

    # -- client surface
    async def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self.collections]
        )

    async def collection_exists(self, name):
        return self.resolve(name) in self.collections

    async def get_collection(self, name):
        cfg = self.collections[self.resolve(name)]
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=cfg)))

    async def create_collection(self, collection_name, vectors_config, on_disk_payload=True):
        self._tick("create_collection")
        self.collections[collection_name] = vectors_config
        self.points.setdefault(collection_name, {})
        self.indexes.setdefault(collection_name, set())

    async def delete_collection(self, collection_name):
        self._tick("delete_collection")
        name = self.resolve(collection_name)
        self.collections.pop(name, None)
        self.points.pop(name, None)
        self.indexes.pop(name, None)

    async def get_aliases(self):
        return SimpleNamespace(aliases=[
            SimpleNamespace(alias_name=a, collection_name=c)
            for a, c in self.aliases.items()
        ])

    async def update_collection_aliases(self, change_aliases_operations):
        self._tick("update_collection_aliases")
        for op in change_aliases_operations:
            if getattr(op, "delete_alias", None) is not None:
                self.aliases.pop(op.delete_alias.alias_name, None)
            elif getattr(op, "create_alias", None) is not None:
                ca = op.create_alias
                self.aliases[ca.alias_name] = ca.collection_name

    async def scroll(self, collection_name, limit=200, with_payload=True,
                     with_vectors=None, offset=None):
        name = self.resolve(collection_name)
        items = sorted(self.points.get(name, {}).items())
        start = offset or 0
        page = items[start:start + limit]
        pts = []
        for pid, (payload, vectors) in page:
            keep = {k: v for k, v in vectors.items()
                    if not with_vectors or k in with_vectors}
            pts.append(SimpleNamespace(id=pid, payload=dict(payload), vector=keep))
        nxt = start + limit if start + limit < len(items) else None
        return pts, nxt

    async def upsert(self, collection_name, points):
        self._tick("upsert")
        name = self.resolve(collection_name)
        for p in points:
            self.points.setdefault(name, {})[p.id] = (dict(p.payload), dict(p.vector))

    async def retrieve(self, collection_name, ids, with_payload=True, with_vectors=False):
        name = self.resolve(collection_name)
        out = []
        for pid in ids:
            hit = self.points.get(name, {}).get(pid)
            if hit is not None:
                out.append(SimpleNamespace(id=pid, payload=dict(hit[0])))
        return out

    async def count(self, collection_name, count_filter=None, exact=True):
        return SimpleNamespace(count=len(self.points.get(self.resolve(collection_name), {})))

    async def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes.setdefault(self.resolve(collection_name), set()).add(field_name)

    async def delete_payload_index(self, collection_name, field_name):
        self.indexes.setdefault(self.resolve(collection_name), set()).discard(field_name)

    async def update_collection(self, collection_name, vectors_config):
        pass

    async def set_payload(self, collection_name, payload, points, wait=True):
        name = self.resolve(collection_name)
        for pid in points:
            cur = self.points.get(name, {}).get(pid)
            if cur:
                cur[0].update(payload)


def vec(size, quant=True):
    return SimpleNamespace(size=size, quantization_config=object() if quant else None)


def full_schema(small=256, colour=True):
    cfg = {"embedding": vec(768), "embedding_small": vec(small)}
    if colour:
        cfg["color_vector"] = vec(3)
    return cfg


def sample_points(n=5, colour=False):
    out = {}
    for i in range(n):
        payload = {
            "sha256": f"sha{i}", "path": f"/mnt/image/generated/{i}.png",
            "genesis": {"novelty": i}, "creation_record": {"run": i},
            "star_rating": i % 5,
        }
        vectors = {"embedding": [0.1] * 768, "embedding_small": [0.1] * 256}
        if colour:
            payload |= {"color_lab": [50.0, 1.0, 2.0], "avg_saturation": 0.4}
            vectors["color_vector"] = [50.0, 1.0, 2.0]
        out[f"id-{i}"] = (payload, vectors)
    return out


def make_db(fake) -> QdrantDBClient:
    db = QdrantDBClient.__new__(QdrantDBClient)
    db._qc = fake
    db.has_mrl = False
    db.has_color_vector = False
    db._small_dim = settings.embed_dim_small
    db._embed_dim = settings.embed_dim
    db.schema_state = {}
    db.obsolete_env = []
    return db


def recorded_schema(fake) -> dict:
    payload, _ = fake.points.get(CONFIG_COLLECTION, {}).get(CONFIG_POINT_ID, ({}, {}))
    return payload.get(SCHEMA_KEY) or {}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "embed_dim", 768)
    monkeypatch.setattr(settings, "embed_dim_small", 256)
    monkeypatch.setattr(settings, "embed_model", "embeddinggemma:300m")


async def boot(fake) -> QdrantDBClient:
    db = make_db(fake)
    await db._start_images(await db._load_or_seed_schema())
    return db


# ── 1. seeding the recorded schema ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_existing_install_is_seeded_from_the_collection_not_the_env(monkeypatch):
    """Upgrading must be a no-op for anyone already running.

    The environment here disagrees with the collection — which is an ordinary
    state for a working install. Seeding from the environment would invent a
    mismatch and ask this user to migrate for nothing.
    """
    monkeypatch.setattr(settings, "embed_dim", 1024)      # disagrees on purpose
    monkeypatch.setattr(settings, "embed_dim_small", 128)
    fake = FakeQdrant()
    fake.seed(IMAGES_COLLECTION, full_schema(small=256, colour=False), sample_points())

    db = await boot(fake)

    assert recorded_schema(fake)["embed_dim"] == 768
    assert recorded_schema(fake)["embed_dim_small"] == 256
    assert db.embed_dim == 768 and db.embed_dim_small == 256
    # Nothing to report: the schema was taken from what is actually there.
    assert db.schema_state["matches"] is True
    assert db.schema_state["reasons"] == []
    assert "delete_collection" not in fake.calls


@pytest.mark.asyncio
async def test_obsolete_env_vars_are_named():
    fake = FakeQdrant()
    fake.seed(IMAGES_COLLECTION, full_schema(small=128, colour=False), sample_points())

    db = await boot(fake)

    assert db.embed_dim_small == 128
    assert "EMBED_DIM_SMALL" in db.obsolete_env
    assert "EMBED_DIM" not in db.obsolete_env  # this one agrees


@pytest.mark.asyncio
async def test_fresh_install_records_schema_before_creating_the_collection():
    """The collection is built from these numbers, so they must land first."""
    fake = FakeQdrant()
    db = await boot(fake)

    schema = recorded_schema(fake)
    assert schema["embed_dim"] == 768
    assert schema["seeded_from"] == "env"
    assert fake.aliases[IMAGES_COLLECTION] == "images_v1"
    created = fake.collections["images_v1"]
    assert created["embedding"].size == 768
    assert created["embedding_small"].size == 256
    assert db.schema_state["matches"] is True


@pytest.mark.asyncio
async def test_recorded_schema_wins_on_later_boots(monkeypatch):
    fake = FakeQdrant()
    await boot(fake)
    monkeypatch.setattr(settings, "embed_dim_small", 64)  # someone edits the env

    db2 = await boot(fake)

    assert db2.embed_dim_small == 256          # unchanged
    assert "EMBED_DIM_SMALL" in db2.obsolete_env


# ── 2. startup never migrates ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_serves_a_mismatched_collection_without_touching_it():
    """A process pointed at a database it did not expect must start, report,
    and change nothing."""
    fake = FakeQdrant()
    fake.seed(IMAGES_COLLECTION, full_schema(small=256, colour=False), sample_points())
    # Pretend a schema was recorded earlier that wants a different width.
    fake.seed(CONFIG_COLLECTION, {}, {CONFIG_POINT_ID: ({SCHEMA_KEY: {
        "embed_dim": 768, "embed_dim_small": 128, "embed_model": "x",
    }}, {})})

    db = make_db(fake)
    await db._start_images(await db._load_or_seed_schema())

    assert db.schema_state["matches"] is False
    assert any("embedding_small" in r for r in db.schema_state["reasons"])
    assert len(fake.points[IMAGES_COLLECTION]) == 5
    assert "delete_collection" not in fake.calls
    assert IMAGES_COLLECTION in fake.collections   # not rebuilt, not aliased away


@pytest.mark.asyncio
async def test_plain_collection_is_left_alone_and_not_flagged_as_broken():
    """A pre-alias install is fine. It must not be nagged on every boot.

    Adopting the alias only makes a *future* dimension change safer, so it is
    offered, not demanded — `matches` stays true and nothing is logged as a
    problem.
    """
    fake = FakeQdrant()
    fake.seed(IMAGES_COLLECTION, full_schema(colour=False), sample_points())

    db = await boot(fake)

    assert db.schema_state["is_alias"] is False
    assert db.schema_state["matches"] is True
    assert db.schema_state["reasons"] == []
    assert fake.points[IMAGES_COLLECTION]          # untouched
    assert "delete_collection" not in fake.calls


# ── 3. the migration, when an operator asks for one ─────────────────────────

@pytest.mark.asyncio
async def test_migration_carries_payload_and_switches_alias():
    fake = FakeQdrant()
    fake.seed("images_v1", full_schema(small=256, colour=False), sample_points())
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    db = await boot(fake)

    await db._rebuild_images(
        source="images_v1", small_dim=128,
        transform=db._transform_small_dim(128),
        with_vectors=["embedding"], reason="test",
    )

    assert fake.aliases[IMAGES_COLLECTION] == "images_v2"
    assert len(fake.points["images_v2"]) == 5
    payload, vectors = fake.points["images_v2"]["id-3"]
    # The irreplaceable payload rides across untouched.
    assert payload["genesis"] == {"novelty": 3}
    assert payload["creation_record"] == {"run": 3}
    assert payload["star_rating"] == 3
    assert len(vectors["embedding_small"]) == 128
    # The source is left where it is, for an operator to check before removing.
    assert len(fake.points["images_v1"]) == 5


@pytest.mark.asyncio
async def test_incomplete_copy_never_gets_the_alias(monkeypatch):
    fake = FakeQdrant()
    fake.seed("images_v1", full_schema(colour=False), sample_points())
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    db = await boot(fake)

    real_upsert = fake.upsert

    async def lossy(collection_name, points):
        await real_upsert(collection_name, points[:-1])
    monkeypatch.setattr(fake, "upsert", lossy)

    with pytest.raises(RuntimeError, match="migration aborted"):
        await db._rebuild_images(
            source="images_v1", small_dim=128,
            transform=db._transform_small_dim(128),
            with_vectors=["embedding"], reason="test",
        )

    assert fake.aliases[IMAGES_COLLECTION] == "images_v1"
    assert len(fake.points["images_v1"]) == 5
    assert "images_v2" not in fake.collections


@pytest.mark.asyncio
async def test_death_during_copy_leaves_live_data_alone():
    fake = FakeQdrant()
    fake.seed("images_v1", full_schema(colour=False), sample_points())
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    db = await boot(fake)
    fake.die_on = "upsert"

    with pytest.raises(Boom):
        await db._rebuild_images(
            source="images_v1", small_dim=128,
            transform=db._transform_small_dim(128),
            with_vectors=["embedding"], reason="test",
        )

    assert fake.aliases[IMAGES_COLLECTION] == "images_v1"
    assert len(fake.points["images_v1"]) == 5


@pytest.mark.asyncio
async def test_interrupted_adoption_is_healed_on_the_next_boot():
    """The one window where `images` briefly resolves to nothing.

    On adoption the plain collection has to go before an alias can take its
    name. By then every point is in the new collection, so the cost of dying
    here is an alias — which the next startup puts back, without needing
    anyone's permission, because doing so destroys nothing.
    """
    fake = FakeQdrant()
    fake.seed(IMAGES_COLLECTION, full_schema(colour=False), sample_points())
    db = await boot(fake)
    fake.die_on = "update_collection_aliases"

    with pytest.raises(Boom):
        await db._rebuild_images(
            source=IMAGES_COLLECTION, small_dim=256,
            transform=db._transform_small_dim(256),
            with_vectors=["embedding"], reason="adopt",
        )

    assert IMAGES_COLLECTION not in fake.collections
    assert IMAGES_COLLECTION not in fake.aliases
    assert len(fake.points["images_v1"]) == 5

    fake.die_on = None
    db2 = await boot(fake)
    assert fake.aliases[IMAGES_COLLECTION] == "images_v1"
    assert len(fake.points["images_v1"]) == 5
    assert db2.schema_state["matches"] is True


@pytest.mark.asyncio
async def test_points_without_a_usable_embedding_are_reset_not_dropped():
    fake = FakeQdrant()
    pts = sample_points(3)
    pts["id-1"] = ({"sha256": "sha1", "wd14_tags": ["a"], "genesis": {"n": 1}}, {})
    fake.seed("images_v1", full_schema(colour=False), pts)
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    db = await boot(fake)

    await db._rebuild_images(
        source="images_v1", small_dim=128,
        transform=db._transform_small_dim(128),
        with_vectors=["embedding"], reason="test",
    )

    payload, vectors = fake.points["images_v2"]["id-1"]
    assert payload["embedding_status"] == "pending"
    assert "wd14_tags" not in payload
    assert payload["genesis"] == {"n": 1}       # provenance still survives
    assert vectors == {}
    assert len(fake.points["images_v2"]) == 3


# ── 4. colour moves to its own collection ───────────────────────────────────

@pytest.mark.asyncio
async def test_colour_is_copied_out_without_touching_images():
    fake = FakeQdrant()
    fake.seed("images_v1", full_schema(colour=True), sample_points(colour=True))
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    before = {k: (dict(p), dict(v)) for k, (p, v) in fake.points["images_v1"].items()}

    db = await boot(fake)

    assert db.has_color_vector is True
    assert len(fake.points[IMAGES_COLOR_COLLECTION]) == 5
    colour_payload, colour_vectors = fake.points[IMAGES_COLOR_COLLECTION]["id-2"]
    assert colour_vectors["color_vector"] == [50.0, 1.0, 2.0]
    assert colour_payload["avg_saturation"] == 0.4
    # images is not modified — not the payload, not the vectors, not the schema.
    assert fake.points["images_v1"] == before
    assert "color_vector" in fake.collections["images_v1"]


@pytest.mark.asyncio
async def test_failed_colour_copy_drops_the_new_collection_and_retries_later(monkeypatch):
    fake = FakeQdrant()
    fake.seed("images_v1", full_schema(colour=True), sample_points(colour=True))
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    before = {k: (dict(p), dict(v)) for k, (p, v) in fake.points["images_v1"].items()}

    db = make_db(fake)
    schema = await db._load_or_seed_schema()
    real_upsert = fake.upsert

    async def lossy(collection_name, points):
        await real_upsert(collection_name, points[:-1])
    monkeypatch.setattr(fake, "upsert", lossy)

    await db._start_images(schema)

    assert db.has_color_vector is False
    assert IMAGES_COLOR_COLLECTION not in fake.collections
    assert fake.points["images_v1"] == before

    # Next boot, with the copy working, completes it.
    monkeypatch.setattr(fake, "upsert", real_upsert)
    db2 = await boot(fake)
    assert db2.has_color_vector is True
    assert len(fake.points[IMAGES_COLOR_COLLECTION]) == 5


@pytest.mark.asyncio
async def test_colour_search_joins_the_two_collections():
    fake = FakeQdrant()
    fake.seed("images_v1", full_schema(colour=True), sample_points(colour=True))
    fake.aliases[IMAGES_COLLECTION] = "images_v1"
    db = await boot(fake)

    async def fake_query(collection_name, **kw):
        assert collection_name == IMAGES_COLOR_COLLECTION
        return SimpleNamespace(points=[
            SimpleNamespace(id="id-1", score=2.0),
            SimpleNamespace(id="id-0", score=1.0),
        ])
    fake.query_points = fake_query

    docs = await db.search_by_color_vector(lab=[50.0, 1.0, 2.0], distance=10.0)

    # Payload comes from images; ordering follows the colour distance.
    assert [d["sha256"] for d in docs] == ["sha0", "sha1"]
    assert docs[0]["_color_distance"] == 1.0
    assert docs[0]["genesis"] == {"novelty": 0}


# ── 5. the abort path ───────────────────────────────────────────────────────

def test_abort_holds_the_process_instead_of_crashing_it():
    """`sleep(inf)` raises OverflowError where time_t cannot hold it, which
    crashes the process and restart-loops the container it meant to park."""
    import inspect
    from backend.app import main
    src = inspect.getsource(main._abort)
    assert 'float("inf")' not in src
    assert "while True" in src

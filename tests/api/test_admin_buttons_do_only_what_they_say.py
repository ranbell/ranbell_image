"""**The admin screen's buttons do what they say, to what they name, and no more.** (2026-11-21)

Every route here acts on the whole archive or on the server itself, from one
click. The DB methods behind them are tested in `tests/db/`; these test the
door — what the route accepts, what it refuses before anything is touched, and
what it hands back to the browser.

Found while writing them:

- `migrate-paths` with an old prefix of `/` stripped it to nothing and then
  matched every absolute path: one click moved the entire library.
- `backup/restore` put the snapshot name straight into
  `file:///qdrant/snapshots/<collection>/<name>`, so `../` could point the
  server's restore at any file its container can read. Only a snapshot the
  collection actually has is accepted now.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from backend.app.api.admin import CONFIRM_PHRASE
from tests.memdb import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]

A, B = sha("a"), sha("b")
SECRET = "saved-key-that-must-stay-on-the-server"  # not key-shaped: gitleaks would stop it


async def _picture(api, s: str, path: str, **extra) -> None:
    await api.db.upsert_new(s, {"sha256": s, "path": path, **extra})


# ── settings ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def llm_applied(monkeypatch):
    """Saving an LLM setting re-points the live client; record it instead."""
    calls = []
    monkeypatch.setattr("backend.app.ai.llm.apply_llm_runtime_config",
                        lambda client, cfg: calls.append(cfg))
    return calls


async def test_the_api_key_is_saved_but_never_sent_back(api, llm_applied):
    r = await api.http.put("/api/admin/config", json={"external_llm_api_key": SECRET})
    assert r.status_code == 200
    assert SECRET not in r.text and r.json()["external_llm_api_key_set"] is True

    r = await api.http.get("/api/admin/config")
    assert SECRET not in r.text and r.json()["external_llm_api_key_set"] is True
    assert (await api.db.get_config())["external_llm_api_key"] == SECRET


async def test_an_empty_key_keeps_the_saved_one_and_clear_removes_it(api, llm_applied):
    await api.http.put("/api/admin/config", json={"external_llm_api_key": SECRET})

    await api.http.put("/api/admin/config", json={"external_llm_api_key": "", "external_llm_url": "http://x"})
    assert (await api.db.get_config())["external_llm_api_key"] == SECRET

    r = await api.http.put("/api/admin/config", json={"external_llm_api_key_clear": True})
    assert r.json()["external_llm_api_key_set"] is False
    assert not (await api.db.get_config()).get("external_llm_api_key")


async def test_the_key_appears_in_no_read_route(api, llm_applied):
    """Every GET without a path parameter, read once with a key saved."""
    import asyncio

    from backend.app.main import app

    await api.http.put("/api/admin/config", json={"external_llm_api_key": SECRET})
    paths = sorted(p for p, ops in app.openapi()["paths"].items()
                   if "get" in ops and "{" not in p and p.startswith("/api")
                   and "stream" not in p and not p.endswith("/events"))
    assert len(paths) > 30
    for p in paths:
        try:
            r = await asyncio.wait_for(api.http.get(p), timeout=3)
        except (TimeoutError, AssertionError):
            continue   # a long poll, or a route that wants a model: neither is a leak
        assert SECRET not in r.text, p


async def test_a_setting_out_of_range_is_refused_and_nothing_is_written(api):
    await api.http.put("/api/admin/config", json={"disk_fault_pct": 90})
    r = await api.http.put("/api/admin/config", json={"disk_fault_pct": 150})
    assert r.status_code == 422
    assert (await api.db.get_config())["disk_fault_pct"] == 90


async def test_a_save_merges_with_what_other_writers_keep_there(api):
    """UMAP, schema and Invoke stats write their own keys into the same document."""
    await api.db.put_config({"umap_params": {"n": 15}})
    await api.http.put("/api/admin/config", json={"semantic_search_limit": 40})
    cfg = await api.db.get_config()
    assert cfg["umap_params"] == {"n": 15} and cfg["semantic_search_limit"] == 40


# ── migrate-paths ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("old,new", [("", "/new"), ("/mnt/a", ""), ("/", "/new"), ("//", "/new"),
                                     ("/mnt/a", "/")])
async def test_a_prefix_that_names_no_folder_is_refused(api, old, new):
    await _picture(api, A, "/mnt/a/x.png")
    await _picture(api, B, "/mnt/b/y.png")
    r = await api.http.post("/api/admin/migrate-paths", json={"old_prefix": old, "new_prefix": new})
    assert r.status_code == 422
    assert (await api.db.get(A))["path"] == "/mnt/a/x.png"
    assert (await api.db.get(B))["path"] == "/mnt/b/y.png"


async def test_migrate_paths_moves_the_named_folder_only(api):
    await _picture(api, A, "/mnt/a/x.png")
    await _picture(api, B, "/mnt/ab/y.png")
    r = await api.http.post("/api/admin/migrate-paths", json={"old_prefix": "/mnt/a/", "new_prefix": "/new"})
    assert r.json() == {"updated": 1}
    assert (await api.db.get(A))["path"] == "/new/x.png"
    assert (await api.db.get(B))["path"] == "/mnt/ab/y.png"


# ── AI reset ─────────────────────────────────────────────────────────────────

async def test_clearing_ai_results_keeps_the_picture_and_its_stars(api):
    await _picture(api, A, "/x/a.png", embedding_status="done", wd14_tags=["1girl"], star_rating=5)
    r = await api.http.post("/api/admin/ai/clear", json={"scope": "done"})
    assert r.json() == {"cleared": 1, "scope": "done"}
    got = await api.db.get(A)
    assert got["wd14_tags"] == [] and got["embedding_status"] == "pending"
    assert got["star_rating"] == 5 and got["path"] == "/x/a.png"


async def test_an_unknown_scope_clears_nothing(api):
    await _picture(api, A, "/x/a.png", embedding_status="done", wd14_tags=["t"])
    r = await api.http.post("/api/admin/ai/clear", json={"scope": "everything"})
    assert r.status_code == 422
    assert (await api.db.get(A))["wd14_tags"] == ["t"]


# ── thumbnails ───────────────────────────────────────────────────────────────

async def test_clearing_thumbnails_deletes_webp_in_the_thumbnail_folder_only(api):
    thumbs = api.settings.thumbnails_dir
    (thumbs / "aa" / "bb").mkdir(parents=True)
    (thumbs / "aa" / "bb" / "t.webp").write_bytes(b"x")
    (thumbs / "keep.txt").write_text("x")
    outside = api.tmp / "elsewhere"
    outside.mkdir()
    (outside / "photo.webp").write_bytes(b"x")
    (thumbs / "link").symlink_to(outside, target_is_directory=True)

    r = await api.http.post("/api/admin/thumbnails/clear")

    assert r.json() == {"deleted": 1}
    assert not (thumbs / "aa" / "bb" / "t.webp").exists()
    assert (thumbs / "keep.txt").exists()
    assert (outside / "photo.webp").exists(), "a link out of the folder is not followed"


# ── confirmation-gated ───────────────────────────────────────────────────────

class _Snapshots:
    """What the server would say about snapshots; the local engine has none."""

    def __init__(self, qc, names: dict[str, list[str]]):
        self._qc, self._names, self.recovered = qc, names, []

    def __getattr__(self, name):
        return getattr(self._qc, name)

    async def list_snapshots(self, collection_name):
        from types import SimpleNamespace
        if collection_name not in self._names:
            raise RuntimeError(f"Collection `{collection_name}` doesn't exist!")
        return [SimpleNamespace(name=n) for n in self._names[collection_name]]

    async def recover_snapshot(self, collection_name, location, **kw):
        self.recovered.append((collection_name, location))
        return True


@pytest_asyncio.fixture()
async def snapshots(api):
    from backend.app.backup.service import _physical
    physical = await _physical(api.db, "images")
    fake = _Snapshots(api.db._qc, {physical: ["images-1-2026-11-20.snapshot"]})
    api.db._qc = fake
    return fake


async def test_a_restore_needs_the_phrase(api, snapshots):
    r = await api.http.post("/api/admin/backup/restore",
                            json={"collection": "images", "snapshot": "images-1-2026-11-20.snapshot"})
    assert r.status_code == 400 and snapshots.recovered == []


async def test_a_restore_takes_a_snapshot_the_collection_has(api, snapshots):
    r = await api.http.post("/api/admin/backup/restore", json={
        "collection": "images", "snapshot": "images-1-2026-11-20.snapshot", "confirm": CONFIRM_PHRASE})
    assert r.status_code == 200
    [(_, location)] = snapshots.recovered
    assert location.endswith("/images-1-2026-11-20.snapshot")


@pytest.mark.parametrize("name", ["../../../etc/passwd", "..", "x/../../y.snapshot",
                                  "images-9-2026-01-01.snapshot"])
async def test_a_restore_refuses_any_other_name(api, snapshots, name):
    r = await api.http.post("/api/admin/backup/restore", json={
        "collection": "images", "snapshot": name, "confirm": CONFIRM_PHRASE})
    assert r.status_code == 404
    assert snapshots.recovered == []


async def test_a_restore_of_a_collection_that_is_not_there_is_refused(api, snapshots):
    r = await api.http.post("/api/admin/backup/restore", json={
        "collection": "../x", "snapshot": "images-1-2026-11-20.snapshot", "confirm": CONFIRM_PHRASE})
    assert r.status_code == 404
    assert snapshots.recovered == []


@pytest.mark.parametrize("route,body", [
    ("/api/admin/schema/apply", {"embed_dim": 1024, "embed_dim_small": 256}),
    ("/api/admin/schema/stale/drop", {"collection": "images_v1"}),
])
async def test_the_destructive_schema_routes_need_the_phrase(api, route, body):
    r = await api.http.post(route, json={**body, "confirm": "yes"})
    assert r.status_code in (400, 422)
    assert api.spooler.jobs == []


async def test_the_live_collection_cannot_be_dropped_as_stale(api):
    from backend.app.backup.service import _physical
    live = await _physical(api.db, "images")
    await _picture(api, A, "/x/a.png")
    r = await api.http.post("/api/admin/schema/stale/drop", json={"collection": live, "confirm": CONFIRM_PHRASE})
    assert r.status_code == 409
    assert await api.db.total_count() == 1


# ── the daily oracle ─────────────────────────────────────────────────────────

async def test_deleting_a_days_oracle_leaves_other_days(api):
    await _picture(api, A, "/x/a.png", genesis={"input_mode": "daily_oracle", "daily_oracle_date": "2026-11-20"})
    await _picture(api, B, "/x/b.png", genesis={"input_mode": "daily_oracle", "daily_oracle_date": "2026-11-21"})
    r = await api.http.delete("/api/admin/invoke/daily-oracle", params={"date": "2026-11-20"})
    assert r.status_code == 200
    assert await api.db.get(A) is None
    assert await api.db.get(B) is not None

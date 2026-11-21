"""**A LoRA bucket's routes change that bucket, and leave the pictures and the other buckets be.** (2026-11-21)

A bucket is the Showrunner's hand-picked training set: which pictures, and the
captions corrected one by one. Losing one is hours of work; an export written
over another bucket's folder can be worse — a dataset already trained on.
`tests/lora/` covers the bucket logic as functions; these drive it through the
routes the LoRA panel calls, with the archive and an export folder behind them.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from PIL import Image

from tests.memdb import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]

A, B, C = sha("a"), sha("b"), sha("c")


async def _picture(api, s: str) -> None:
    path = api.settings.source_images_dir / f"{s[:6]}.png"
    Image.new("RGB", (8, 8), "red").save(path)
    await api.db.upsert_new(s, {"sha256": s, "path": str(path), "name": s[:6], "ext": ".png"})


@pytest.fixture()
def export_dir(api):
    d = api.tmp / "lora_export"
    d.mkdir()
    return d


async def _bucket(api, name: str, shas: list[str]) -> dict:
    r = await api.http.post("/api/lora/buckets", json={"name": name, "sha256s": shas})
    assert r.status_code == 200, r.text
    return r.json()


async def _ready(api, bucket: dict) -> None:
    for it in bucket["items"]:
        r = await api.http.put(f"/api/lora/buckets/{bucket['id']}/images/{it['sha256']}/caption",
                               json={"caption": f"a photo of {it['sha256'][:4]}"})
        assert r.status_code == 200


async def _get(api, bucket_id: str):
    return await api.http.get(f"/api/lora/buckets/{bucket_id}")


# ── create / delete ──────────────────────────────────────────────────────────

async def test_a_bad_picture_id_makes_no_bucket(api):
    r = await api.http.post("/api/lora/buckets", json={"name": "x", "sha256s": ["not-a-sha"]})
    assert r.status_code == 400
    assert (await api.http.get("/api/lora/buckets")).json()["buckets"] == []


async def test_deleting_a_bucket_leaves_the_others_and_the_pictures(api):
    await _picture(api, A)
    one = await _bucket(api, "one", [A])
    two = await _bucket(api, "two", [A])

    caption_job = api.spooler.jobs[0].id

    assert (await api.http.delete(f"/api/lora/buckets/{one['id']}")).status_code == 200

    assert api.spooler.cancelled == [caption_job], "its caption job is stopped, or its save brings it back"

    assert (await _get(api, one["id"])).status_code == 404
    assert [it["sha256"] for it in (await _get(api, two["id"])).json()["items"]] == [A]
    assert await api.db.get(A) is not None


async def test_deleting_a_bucket_that_is_not_there_is_a_404(api):
    r = await api.http.delete("/api/lora/buckets/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_a_name_that_is_taken_by_a_folder_gets_its_own_slug(api, export_dir):
    """A deleted bucket's dataset keeps its folder; a new bucket must not export over it."""
    await api.db.put_config({"lora_export_dir": str(export_dir)})
    (export_dir / "portrait").mkdir()
    b = await _bucket(api, "portrait", [])
    assert b["slug"] != "portrait"


async def test_a_name_cannot_make_a_slug_that_leaves_the_export_folder(api):
    b = await _bucket(api, "../../etc", [])
    assert "/" not in b["slug"] and ".." not in b["slug"] and b["slug"]


# ── pictures and captions ────────────────────────────────────────────────────

async def test_removing_a_picture_takes_it_from_this_bucket_only(api):
    one = await _bucket(api, "one", [A, B])
    two = await _bucket(api, "two", [A])

    r = await api.http.delete(f"/api/lora/buckets/{one['id']}/images/{A}")

    assert [it["sha256"] for it in r.json()["items"]] == [B]
    assert [it["sha256"] for it in (await _get(api, two["id"])).json()["items"]] == [A]


async def test_a_caption_changes_its_own_picture_only(api):
    b = await _bucket(api, "one", [A, B])
    r = await api.http.put(f"/api/lora/buckets/{b['id']}/images/{A}/caption", json={"caption": "mine"})
    items = {it["sha256"]: it for it in r.json()["items"]}
    assert items[A]["caption"] == "mine" and items[A]["edited"] is True
    assert items[B]["caption"] == "" and items[B]["edited"] is False


async def test_a_caption_for_a_picture_not_in_the_bucket_is_a_404(api):
    b = await _bucket(api, "one", [A])
    r = await api.http.put(f"/api/lora/buckets/{b['id']}/images/{C}/caption", json={"caption": "x"})
    assert r.status_code == 404
    assert [it["sha256"] for it in (await _get(api, b["id"])).json()["items"]] == [A]


async def test_adding_pictures_queues_their_captions_once(api):
    """A job already on the bucket picks the new pictures up; a second would race it."""
    b = await _bucket(api, "one", [A])
    r = await api.http.post(f"/api/lora/buckets/{b['id']}/images", json={"sha256s": [A, B]})
    assert r.json()["added"] == 1
    captions = [j for j in api.spooler.jobs if j.title == "lora_caption"]
    assert len(captions) == 1 and captions[0].kwargs["bucket_id"] == b["id"]
    assert all(j.title != "lora_export" for j in api.spooler.jobs)


# ── export ───────────────────────────────────────────────────────────────────

async def test_an_unfinished_bucket_is_not_exported(api, export_dir):
    await api.db.put_config({"lora_export_dir": str(export_dir)})
    b = await _bucket(api, "one", [A])
    r = await api.http.post(f"/api/lora/buckets/{b['id']}/export")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_ready"
    assert all(j.title != "lora_export" for j in api.spooler.jobs)


async def test_no_export_folder_means_no_export_job(api):
    await api.db.put_config({"lora_export_dir": str(api.tmp / "not-mounted")})
    await _picture(api, A)
    b = await _bucket(api, "one", [A])
    await _ready(api, b)
    r = await api.http.post(f"/api/lora/buckets/{b['id']}/export")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "no_export_dir"
    assert all(j.title != "lora_export" for j in api.spooler.jobs)


async def test_a_ready_bucket_queues_one_export_of_itself_into_its_own_folder(api, export_dir):
    await api.db.put_config({"lora_export_dir": str(export_dir)})
    await _picture(api, A)
    b = await _bucket(api, "one", [A])
    await _ready(api, b)

    r = await api.http.post(f"/api/lora/buckets/{b['id']}/export")

    assert r.status_code == 200
    [job] = [j for j in api.spooler.jobs if j.title == "lora_export"]
    assert job.kwargs["bucket_id"] == b["id"] and job.kwargs["export_dir"] == str(export_dir)
    assert r.json()["path"] == str(export_dir / b["slug"])


async def test_the_zip_holds_this_bucket_and_nothing_else(api):
    for s in (A, B, C):
        await _picture(api, s)
    one = await _bucket(api, "one", [A, B])
    other = await _bucket(api, "other", [C])
    await _ready(api, one)
    await _ready(api, other)

    r = await api.http.get(f"/api/lora/buckets/{one['id']}/export.zip")

    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    slug = one["slug"]
    assert sorted(names) == sorted([f"{slug}/{A}.png", f"{slug}/{A}.txt",
                                    f"{slug}/{B}.png", f"{slug}/{B}.txt", f"{slug}/meta.json"])
    assert all(not n.startswith("/") and ".." not in n for n in names)


async def test_a_zip_of_a_bucket_whose_picture_file_is_gone_is_refused(api):
    await _picture(api, A)
    b = await _bucket(api, "one", [A])
    await _ready(api, b)
    (api.settings.source_images_dir / f"{A[:6]}.png").unlink()
    r = await api.http.get(f"/api/lora/buckets/{b['id']}/export.zip")
    assert r.status_code == 409

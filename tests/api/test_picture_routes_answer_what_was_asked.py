"""**The picture routes answer for the picture named, and a bad name is a 404.** (2026-11-21)

These are the routes every screen leans on — the grid's thumbnails, the viewer's
original, the download button, the star rating. Driven over HTTP against the
real app and an in-memory Qdrant (the `api` fixture), so what is checked is what
the panel gets back and what is left in the archive afterwards.

When these were written, any id that was not hex — a typo, a truncated link, a
stale URL — came back as a 500 from every one of them, because the id went
straight into `uuid.UUID`. And a rating sent for a picture that is not there
was written first and checked after, so it failed inside Qdrant: a 500 again.
"""
from __future__ import annotations

import pytest

from tests.memdb import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]

A, B, NOWHERE = sha("a"), sha("b"), sha("c")


async def _picture(api, s: str, *, name: str = "pic", ext: str = ".png",
                   on_disk: bool = True, **payload) -> None:
    path = api.settings.source_images_dir / f"{name}{ext}"
    if on_disk:
        path.write_bytes(b"\x89PNG fake " + s.encode())
    await api.db.upsert_new(s, {"sha256": s, "path": str(path), "name": name, "ext": ext, **payload})


# ── the star rating ──────────────────────────────────────────────────────────

async def test_a_rating_is_set_and_the_rest_of_the_picture_stays(api):
    await _picture(api, A, tags=["cat"])
    await _picture(api, B)

    r = await api.http.put(f"/api/images/{A}/rating", json={"rating": 4})

    assert r.status_code == 200 and r.json()["star_rating"] == 4
    got = await api.db.get(A)
    assert got["star_rating"] == 4 and got["tags"] == ["cat"]
    assert "star_rating" not in await api.db.get(B)


async def test_a_null_rating_takes_the_stars_away(api):
    await _picture(api, A, star_rating=5)
    r = await api.http.put(f"/api/images/{A}/rating", json={"rating": None})
    assert r.status_code == 200
    assert "star_rating" not in await api.db.get(A)


@pytest.mark.parametrize("rating", [0, 6, -1])
async def test_a_rating_out_of_range_is_refused_and_nothing_is_written(api, rating):
    await _picture(api, A, star_rating=3)
    r = await api.http.put(f"/api/images/{A}/rating", json={"rating": rating})
    assert r.status_code == 422
    assert (await api.db.get(A))["star_rating"] == 3


async def test_rating_a_picture_that_is_not_there_is_a_404_and_makes_no_row(api):
    r = await api.http.put(f"/api/images/{NOWHERE}/rating", json={"rating": 3})
    assert r.status_code == 404
    assert await api.db.total_count() == 0


# ── the files ────────────────────────────────────────────────────────────────

async def test_the_original_is_the_pictures_own_file(api):
    await _picture(api, A, name="a")
    await _picture(api, B, name="b")
    r = await api.http.get(f"/api/originals/{A}")
    assert r.status_code == 200
    assert r.content.endswith(A.encode())
    assert r.headers["content-type"] == "image/png"


async def test_a_picture_whose_file_is_gone_is_a_404(api):
    await _picture(api, A, on_disk=False)
    for url in (f"/api/originals/{A}", f"/api/download/{A}"):
        assert (await api.http.get(url)).status_code == 404, url


async def test_the_download_is_an_attachment_named_after_the_picture(api):
    await _picture(api, A, name="sunset")
    r = await api.http.get(f"/api/download/{A}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment") and "sunset.png" in cd


async def test_a_name_with_a_line_break_cannot_split_the_download_header(api):
    await _picture(api, A, name="a\r\nSet-Cookie: x=1")
    r = await api.http.get(f"/api/download/{A}")
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert "\n" not in r.headers["content-disposition"]


async def test_a_thumbnail_on_disk_is_served_as_is(api):
    await _picture(api, A)
    thumb = api.settings.thumbnails_dir / A[:2] / A[2:4] / f"{A}.webp"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"RIFF thumb")
    r = await api.http.get(f"/api/thumbnails/{A}.webp")
    assert r.status_code == 200 and r.content == b"RIFF thumb"


# ── a bad name ───────────────────────────────────────────────────────────────

BAD = ["zz", "not-a-sha", "a" * 10, "g" * 64, "...."]
ROUTES = [
    ("GET", "/api/images/{}"),
    ("GET", "/api/images/{}/raw-metadata"),
    ("GET", "/api/originals/{}"),
    ("GET", "/api/download/{}"),
    ("GET", "/api/thumbnails/{}.webp"),
    ("GET", "/api/images/color-like/{}"),
    ("PUT", "/api/images/{}/rating"),
]


@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("method,route", ROUTES)
async def test_a_name_that_is_not_a_picture_id_is_a_404_not_a_500(api, method, route, bad):
    await _picture(api, A)
    r = await api.http.request(method, route.format(bad), json={"rating": 3} if method == "PUT" else None)
    assert r.status_code == 404, (route.format(bad), r.status_code, r.text[:120])
    assert await api.db.total_count() == 1


async def test_an_unknown_picture_is_a_404_on_every_route(api):
    for method, route in ROUTES:
        r = await api.http.request(method, route.format(NOWHERE),
                                   json={"rating": 3} if method == "PUT" else None)
        if route.endswith(".webp") or "color-like" in route:
            assert r.status_code in (200, 404), route   # may answer with nothing found
        else:
            assert r.status_code == 404, (route, r.status_code)


async def test_a_thumbnail_id_cannot_walk_out_of_the_thumbnail_folder(api):
    """The id is cut into the path (`ab/cd/abcd….webp`): `..xx` would climb one level."""
    outside = api.settings.thumbnails_dir.parent / "xx" / "..xx.webp"
    outside.parent.mkdir()
    outside.write_bytes(b"not a thumbnail")
    r = await api.http.get("/api/thumbnails/..xx.webp")
    assert r.status_code == 404
    assert b"not a thumbnail" not in r.content

"""**Each job button queues its own job; each read route answers, empty or not.** (2026-11-21)

Two ways the screens break quietly, neither of which a function test sees:

- A button wired to the wrong runner — or to the right runner on the wrong lane
  — answers 200 with a job id and does something else, or waits behind the
  wrong queue. Each one here is pinned to the runner and lane it starts.
- A read route that 500s on an archive with nothing in it yet (a fresh install,
  a reset library) leaves a screen blank. Every GET without a path parameter is
  read against an empty archive, and the main ones against a filled one for the
  keys their panels read.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.memdb import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]


# ── job buttons ──────────────────────────────────────────────────────────────

BUTTONS = [
    # route,                                   job title,                   runner,                          lane
    ("/api/admin/batch-category/backfill",     "batch_category_backfill",   "run_batch_category_backfill",   "sync"),
    ("/api/admin/character-compat/backfill",   "character_compat_backfill", "run_character_compat_backfill", "embed"),
    ("/api/admin/colors/backfill",             "color_extract",             "run_color_backfill",            "sync"),
    ("/api/admin/is-reference/backfill",       "is_reference_backfill",     "run_is_reference_backfill",     "sync"),
    ("/api/admin/model-name/backfill",         "model_name_backfill",       "run_model_name_backfill",       "sync"),
    ("/api/admin/mrl/backfill",                "mrl_backfill",              "run_mrl_backfill",              "embed"),
    ("/api/admin/orphans/sweep",               "orphan_sweep",              "run_orphan_sweep",              "sync"),
    ("/api/admin/invoke/import-wd14-vocab",    "import_wd14_vocab",         "run_import_wd14_vocab",         "sync"),
    ("/api/ai/pipeline",                       "ai_tagging",                "run_pipeline_tagging",          "tagging"),
    ("/api/alignment/evaluate",                "alignment_evaluate",        "run_alignment_evaluate",        "eval"),
    ("/api/analyzer/tag-taxonomy",             "tag_taxonomy",              "run_tag_taxonomy",              "eval"),
    ("/api/analyzer/umap/analyze",             "umap_analyze",              "run_analyze_umap",              "sync"),
    ("/api/scan",                              "scan_heal",                 "run_scan_heal",                 "sync"),
    ("/api/scan/full",                         "scan_full",                 "run_scan_full",                 "sync"),
    ("/api/scan/strict",                       "scan_strict",               "run_scan_heal",                 "sync"),
    ("/api/scan/refresh-metadata",             "meta_update",               "run_scan_refresh_metadata",     "sync"),
]


@pytest.mark.parametrize("route,title,runner,lane", BUTTONS, ids=[b[0] for b in BUTTONS])
async def test_a_button_queues_its_own_runner_once_and_touches_nothing(api, route, title, runner, lane):
    await api.db.upsert_new(sha("a"), {"sha256": sha("a"), "path": "/x/a.png"})

    r = await api.http.post(route, json={})

    assert r.status_code == 200, r.text
    [job] = api.spooler.jobs
    assert (job.title, job.func.__name__, job.lane.value) == (title, runner, lane)
    assert r.json()["job_id"] == job.id
    assert await api.db.total_count() == 1, "queueing is all a button does; the job does the work"


# ── reads on an empty archive ────────────────────────────────────────────────

#: These reach Ollama, ComfyUI or the live spooler's internals, which an API
#: test does not have; they are not about the archive being empty.
NEEDS_A_SERVICE = {
    "/api/ai/status", "/api/comfy/status", "/api/comfy/workflows",
    "/api/health/detail", "/api/jobs", "/api/jobs/lanes",
}


def _read_routes() -> list[str]:
    from backend.app.main import app
    return sorted(p for p, ops in app.openapi()["paths"].items()
                  if "get" in ops and "{" not in p and p.startswith("/api")
                  and "stream" not in p and not p.endswith("/events") and p not in NEEDS_A_SERVICE)


def test_the_read_routes_are_found():
    assert len(_read_routes()) > 40


@pytest.mark.parametrize("route", _read_routes())
async def test_an_empty_archive_is_answered_without_a_500(api, route):
    try:
        r = await asyncio.wait_for(api.http.get(route), timeout=5)
    except TimeoutError:
        pytest.skip("a long poll")
    assert r.status_code < 500, (route, r.status_code)


# ── reads on a filled archive ────────────────────────────────────────────────

async def _three(api) -> None:
    for i, c in enumerate("abc"):
        await api.db.upsert_new(sha(c), {
            "sha256": sha(c), "path": f"/lib/day/{c}.png", "name": c, "dir": "/lib/day",
            "wd14_tags": ["1girl", "rain"] if c != "c" else ["1girl"],
            "mtime": 1_700_000_000 + i, "embedding_status": "done", "avg_saturation": 0.5,
        })
    await api.db.set_color_vector(sha("a"), [50.0, 10.0, -5.0], {"palette_hex": ["#ff0000"]})


async def test_the_grid_lists_newest_first_with_what_a_card_needs(api):
    await _three(api)
    body = (await api.http.get("/api/images")).json()
    assert body["total"] == 3
    assert [i["sha256"] for i in body["images"]] == [sha("c"), sha("b"), sha("a")]
    assert {"sha256", "name", "path"} <= set(body["images"][0])


async def test_the_grid_pages_with_its_cursor_and_loses_no_picture(api):
    await _three(api)
    seen, cursor = [], None
    for _ in range(5):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = (await api.http.get("/api/images", params=params)).json()
        seen += [i["sha256"] for i in body["images"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted([sha("a"), sha("b"), sha("c")])
    assert len(seen) == 3, "no picture twice"


async def test_the_date_range_spans_the_archive(api):
    await _three(api)
    assert (await api.http.get("/api/images/date-range")).json() == {
        "min_mtime": 1_700_000_000, "max_mtime": 1_700_000_002}


async def test_the_tag_list_counts_each_tag(api):
    await _three(api)
    counts = {t["tag"]: t["count"] for t in (await api.http.get("/api/tags")).json()}
    assert counts["1girl"] == 3 and counts["rain"] == 2


async def test_the_colour_space_shows_the_pictures_that_have_colour(api):
    """The regression of 2026-11-21: this route answered 500 on every call."""
    await _three(api)
    body = (await api.http.get("/api/analyzer/color-3d")).json()
    assert [(p["sha256"], p["L"], p["hex"]) for p in body["points"]] == [(sha("a"), 50.0, "#ff0000")]
    assert body["pending_count"] == 0


async def test_the_tag_network_links_tags_that_share_pictures(api):
    await _three(api)
    body = (await api.http.get("/api/analyzer/tag-network")).json()
    edges = {frozenset((e["source"], e["target"])): e["weight"] for e in body["edges"]}
    assert edges[frozenset(("1girl", "rain"))] == 2


# ── a malformed body ─────────────────────────────────────────────────────────

def _body_routes() -> list[tuple[str, str, bool]]:
    """Every route that reads a body: (method, path, whether the body is required)."""
    from backend.app.main import app
    out = []
    for p, ops in app.openapi()["paths"].items():
        for m, op in ops.items():
            if m in ("post", "put", "patch") and "requestBody" in op and p.startswith("/api"):
                out.append((m.upper(), p, bool(op["requestBody"].get("required"))))
    return sorted(out)


def test_the_body_routes_are_found():
    assert len(_body_routes()) > 40


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b"null", b'"text"'], ids=["broken", "list", "null", "string"])
@pytest.mark.parametrize("method,route,required", _body_routes(), ids=[f"{m} {p}" for m, p, _ in _body_routes()])
async def test_a_malformed_body_is_refused_and_starts_nothing(api, method, route, required, body):
    if body == b"null" and not required:
        pytest.skip("an optional body: null is the same as sending none")
    url = route.replace("{", "").replace("}", "")
    r = await api.http.request(method, url, content=body, headers={"content-type": "application/json"})
    assert 400 <= r.status_code < 500, (route, r.status_code, r.text[:120])
    assert api.spooler.jobs == []
    assert await api.db.total_count() == 0


@pytest.mark.parametrize("body", [b"{not json", b"[1, 2]", b'{"tags": "rain"}'])
async def test_tag_taxonomy_refuses_a_body_that_is_not_a_tag_list(api, body):
    """It read `request.json()` by hand: a 500 for these, or a job for a string."""
    r = await api.http.post("/api/analyzer/tag-taxonomy", content=body,
                            headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert api.spooler.jobs == []


# ── a second press ───────────────────────────────────────────────────────────

#: tag-taxonomy carries the tags to classify, so each press is its own job.
WHOLE_ARCHIVE = [b for b in BUTTONS if b[0] != "/api/analyzer/tag-taxonomy"]


@pytest.mark.parametrize("route,title,runner,lane", WHOLE_ARCHIVE, ids=[b[0] for b in WHOLE_ARCHIVE])
async def test_a_second_press_is_handed_the_job_already_there(api, route, title, runner, lane):
    first = (await api.http.post(route, json={})).json()
    second = (await api.http.post(route, json={})).json()
    assert len(api.spooler.jobs) == 1
    assert second == {"status": "already_queued", "job_id": first["job_id"]}


@pytest.mark.parametrize("state,again", [("running", False), ("paused", False),
                                         ("cancelling", True), ("done", True), ("failed", True)])
async def test_only_a_live_job_holds_the_button(api, state, again):
    """Pressing after a cancel, or after it finished, means "start over"."""
    await api.http.post("/api/admin/colors/backfill")
    api.spooler.jobs[0].state = state
    await api.http.post("/api/admin/colors/backfill")
    assert len(api.spooler.jobs) == (2 if again else 1)


@pytest.mark.parametrize("route,body", [
    ("/api/ai/pipeline", {"sha256s": [sha("a")]}),
    ("/api/alignment/evaluate", {"sha256s": [sha("a")]}),
    ("/api/scan/refresh-metadata?methods=longest_text", {}),
])
async def test_a_job_on_a_selection_is_queued_every_time(api, route, body):
    await api.http.post(route, json=body)
    await api.http.post(route, json=body)
    assert len(api.spooler.jobs) == 2

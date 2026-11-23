"""**Every `/api` call shows the token, or it stops at the door.** (2026-11-21)

`verify_api_token` in main.py is the only thing between the network and every
route — the admin's deletes included — and nothing tested it. These pin what it
lets through and what it turns away, including the ways a path can be spelled to
walk around a prefix check.

The app is driven without its lifespan: the gate is middleware and runs before
any route, so no Qdrant, Ollama or ComfyUI is needed. A request that gets past
the gate lands on a route that does not exist and comes back 404; one that does
not comes back 401.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.config import settings  # noqa: E402
from backend.app.main import app  # noqa: E402

PAST_THE_GATE = 404   # no such route — but the gate let it through
STOPPED = 401

NOWHERE = "/api/__no_such_route__"


@pytest.fixture()
def client():
    return TestClient(app)


def test_no_token_is_turned_away(client):
    r = client.get(NOWHERE)
    assert r.status_code == STOPPED
    assert r.json() == {"detail": "Unauthorized"}


def test_a_wrong_token_is_turned_away(client):
    assert client.get(NOWHERE, headers={"X-API-Token": settings.api_token + "x"}).status_code == STOPPED
    assert client.get(NOWHERE, headers={"X-API-Token": ""}).status_code == STOPPED


def test_a_post_is_held_to_the_same_rule(client):
    assert client.post(NOWHERE, json={}).status_code == STOPPED


@pytest.mark.parametrize("how", ["header", "bearer", "cookie"])
def test_each_way_of_showing_the_token_is_accepted(client, how):
    """The screen uses the header; EventSource and <img> cannot, so the cookie exists."""
    tok = settings.api_token
    if how == "header":
        r = client.get(NOWHERE, headers={"X-API-Token": tok})
    elif how == "bearer":
        r = client.get(NOWHERE, headers={"Authorization": f"Bearer {tok}"})
    else:
        client.cookies.set("api_token", tok)
        r = client.get(NOWHERE)
    assert r.status_code == PAST_THE_GATE


def test_the_token_is_not_taken_from_the_query_string(client):
    """A URL is kept in proxy and access logs and in browser history (2026-11-24)."""
    assert client.get(NOWHERE, params={"token": settings.api_token}).status_code == STOPPED


def test_a_wrong_header_is_not_rescued_by_nothing_else(client):
    """A bad header with no other token is still a bad token."""
    r = client.get(NOWHERE, headers={"Authorization": "Bearer nope"})
    assert r.status_code == STOPPED


def test_only_the_probe_is_open_to_anyone(client):
    assert client.get("/api/health").status_code == 200


# ── the token endpoint and other pages (2026-11-22) ──────────────────────────
#
# /api/token handed the token to anyone, and CORS was "*": any page the browser
# opened could read the token and call anything with it. The token goes to this
# app's own page only now, CORS is off, and the cookie — the one credential a
# browser attaches by itself — counts only on the app's own requests.

def test_the_token_is_handed_to_the_apps_own_page(client):
    r = client.get("/api/token", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.json() == {"token": settings.api_token}


@pytest.mark.parametrize("site", [None, "cross-site", "same-site", "none"])
def test_the_token_is_not_handed_to_anything_else(client, site):
    """None is a script; same-site is another service on this host (the port does not count)."""
    headers = {"Sec-Fetch-Site": site} if site else {}
    r = client.get("/api/token", headers=headers)
    assert r.status_code == 403
    assert settings.api_token not in r.text


# Browsers send Sec-Fetch-Site only to HTTPS and localhost, so the app opened
# as http://<LAN address>:3100 got 403 on its own page (2026-11-24). Without
# the header, the Referer says whose page it is.

def test_the_apps_own_page_over_plain_http_gets_the_token(client):
    r = client.get("/api/token", headers={"Referer": "http://testserver/lora"})
    assert r.status_code == 200 and r.json() == {"token": settings.api_token}


@pytest.mark.parametrize("referer", [
    "http://evil.example/",            # another page
    "http://testserver:8188/",         # another service on this host
    "not a url",
])
def test_a_referer_from_elsewhere_gets_no_token(client, referer):
    r = client.get("/api/token", headers={"Referer": referer})
    assert r.status_code == 403
    assert settings.api_token not in r.text


def test_the_header_outranks_the_referer(client):
    """Where the browser does say, what it says decides."""
    r = client.get("/api/token", headers={"Sec-Fetch-Site": "cross-site",
                                          "Referer": "http://testserver/"})
    assert r.status_code == 403


def test_another_page_gets_no_cors_answer(client):
    r = client.options("/api/admin/config", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("site,expected", [
    ("same-origin", PAST_THE_GATE),   # the app's own page, an <img> or EventSource
    (None, PAST_THE_GATE),            # not a browser: the cookie is just a credential
    ("none", PAST_THE_GATE),          # typed into the address bar
    ("same-site", STOPPED),           # another service on this host
    ("cross-site", STOPPED),          # any other page
])
def test_the_cookie_counts_only_on_the_apps_own_requests(client, site, expected):
    client.cookies.set("api_token", settings.api_token)
    headers = {"Sec-Fetch-Site": site} if site else {}
    assert client.post(NOWHERE, headers=headers).status_code == expected


def test_a_header_token_works_from_anywhere(client):
    """Another origin that knows the token (a configured tool) is not refused."""
    r = client.get(NOWHERE, headers={"X-API-Token": settings.api_token, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == PAST_THE_GATE


@pytest.mark.parametrize("path", [
    "/api/health/",          # the exemption is exact, not a prefix
    "/api/healthz",
    "/api/token/../admin/config",
    "/api/token/",
    "/api/admin/config",
    "/api/images",
])
def test_paths_next_to_the_open_ones_stay_closed(client, path):
    assert client.get(path).status_code == STOPPED


@pytest.mark.parametrize("path", [
    "//api/admin/config",     # doubled slash
    "/%61pi/admin/config",    # percent-encoded "a"
    "/API/admin/config",      # case
])
def test_spelling_the_path_differently_does_not_reach_a_route(client, path):
    """Whatever slips past the prefix check must not land on an `/api` route either."""
    r = client.get(path)
    assert r.status_code in (STOPPED, 404), r.status_code
    assert r.status_code != 200

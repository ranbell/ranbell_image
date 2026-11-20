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


@pytest.mark.parametrize("how", ["header", "bearer", "query", "cookie"])
def test_each_way_of_showing_the_token_is_accepted(client, how):
    """The screen uses the header; EventSource and <img> cannot, so query and cookie exist."""
    tok = settings.api_token
    if how == "header":
        r = client.get(NOWHERE, headers={"X-API-Token": tok})
    elif how == "bearer":
        r = client.get(NOWHERE, headers={"Authorization": f"Bearer {tok}"})
    elif how == "query":
        r = client.get(NOWHERE, params={"token": tok})
    else:
        client.cookies.set("api_token", tok)
        r = client.get(NOWHERE)
    assert r.status_code == PAST_THE_GATE


def test_a_wrong_header_is_not_rescued_by_nothing_else(client):
    """A bad header with no other token is still a bad token."""
    r = client.get(NOWHERE, headers={"Authorization": "Bearer nope"})
    assert r.status_code == STOPPED


@pytest.mark.parametrize("path", ["/api/health", "/api/token"])
def test_only_the_probe_and_the_token_endpoint_are_open(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", [
    "/api/health/",          # the exemption is exact, not a prefix
    "/api/healthz",
    "/api/token/../admin/config",
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

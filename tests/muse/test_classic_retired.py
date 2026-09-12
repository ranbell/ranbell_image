"""**Muse Classic を退役させても、楽屋は絶対に残す。**（2026-09-12）

総監督のご判断で Muse Refine を正規の Muse にした。撮影室は一つになったが、
**楽屋と手帖だけは classic のルーターに同居していた** —— `/api/muse` の下に
撮影室の23本と楽屋の6本が並んでいた。

撮影室は退役、楽屋は据え置き（[[project-muse-circle-must-stay]]）。
URL は変えない —— 画面（`CharacterGallery` / `LoungePanel`）がそう叩いている。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from fastapi.routing import APIRoute  # noqa: E402


def _paths(router) -> set[str]:
    return {r.path for r in router.routes if isinstance(r, APIRoute)}


def test_the_lounge_is_still_served():
    from app.muse import lounge_api

    got = _paths(lounge_api.router)
    for path in (
        "/api/muse/lounge/threads",
        "/api/muse/lounge/threads/{thread_id}",
        "/api/muse/lounge/trends",
        "/api/muse/lounge/threads/{thread_id}/like",
        "/api/muse/lounge/summary",
        "/api/muse/handpost",
    ):
        assert path in got, path


def test_the_lounge_urls_did_not_move():
    """画面が叩いている URL をそのまま出すこと。移すと楽屋が黙って消える。"""
    from app.muse import lounge_api

    served = _paths(lounge_api.router)
    for panel in ("CharacterGallery.vue", "muse/LoungePanel.vue"):
        src = Path(f"frontend/src/components/{panel}").read_text(encoding="utf-8")
        for call in ("/api/muse/lounge/threads", "/api/muse/lounge/trends",
                     "/api/muse/handpost", "/api/muse/lounge/summary"):
            if call in src:
                assert call in served, f"{panel} が叩く {call} が出ていない"


def test_the_studio_routes_are_gone():
    """撮影室は Muse Refine 一つ。classic の口は main に載せない。"""
    from app.main import app

    src = Path("backend/app/main.py").read_text(encoding="utf-8")
    assert "from .muse.api import router" not in src
    assert "muse_lounge_router" in src
    assert "muse_refine_router" in src
    assert app is not None


@pytest.mark.parametrize("path", [
    "/api/muse/sessions", "/api/muse/report", "/api/muse/steps", "/api/muse/roster",
])
def test_a_retired_route_is_not_served(path):
    from app.muse import lounge_api
    from app.muse_refine import api as refine_api

    assert path not in _paths(lounge_api.router)
    assert path not in _paths(refine_api.router)


def test_the_lounge_router_does_not_drag_the_turn_engine_in():
    """楽屋の口が classic の撮影室を import したら、退役の意味がない。"""
    import ast

    tree = ast.parse(Path("backend/app/muse/lounge_api.py").read_text(encoding="utf-8"))
    imported = {
        a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names
    }
    # 撮影室（`service`）と手帖（`notebook`）は引かない。引いたら退役の意味がない。
    for engine in ("service", "notebook", "brief", "facets", "chain", "crew"):
        assert engine not in imported, engine
    assert imported <= {
        "annotations", "APIRouter", "HTTPException", "Request", "BaseModel",
        "handpost_db", "lounge_db", "lounge as lounge_mod", "lounge",
        "presets as presets_db", "presets",
    }, imported

"""**Retiring Muse Classic never touches the lounge.** (2026-09-12)

At the Showrunner's decision Muse Refine became the Muse. There is one studio.
Classic's turn engine and panel withdrew to `private/muse_classic/`, and the
studio folded into `backend/app/muse/`, served from `/api/muse`.

The lounge stays where it is ([[project-muse-circle-must-stay]]). Its URLs do not
move either — the panel (`CharacterGallery` / `LoungePanel`) calls them. The
studio's doors moved to `/api/muse` as well, so what matters here is **that the
routes do not collide under the same roof as the lounge**.
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
    """Serve exactly the URLs the panel calls. Move one and the lounge silently
    disappears."""
    from app.muse import lounge_api

    served = _paths(lounge_api.router)
    for panel in ("CharacterGallery.vue", "muse/LoungePanel.vue"):
        src = Path(f"frontend/src/components/{panel}").read_text(encoding="utf-8")
        for call in ("/api/muse/lounge/threads", "/api/muse/lounge/trends",
                     "/api/muse/handpost", "/api/muse/lounge/summary"):
            if call in src:
                assert call in served, f"{panel} が叩く {call} が出ていない"


def test_there_is_one_studio_and_it_answers_under_api_muse():
    """One studio. Classic's doors are not mounted; Refine's folded into `muse`."""
    from app.main import app

    src = Path("backend/app/main.py").read_text(encoding="utf-8")
    # classic の router も、畳む前の名前も、もう出てこない
    assert "muse_refine_router" not in src
    assert "from .muse_refine" not in src
    assert "muse_lounge_router" in src
    assert "from .muse.api import router as muse_router" in src
    assert app is not None


def test_the_two_routers_under_api_muse_do_not_collide():
    """The studio and the lounge now share a prefix. **Serve one route twice and
    the later one wins.**"""
    from app.muse import lounge_api
    from app.muse import api as studio_api

    studio = _paths(studio_api.router)
    lounge = _paths(lounge_api.router)
    assert studio and lounge
    assert not (studio & lounge), sorted(studio & lounge)
    for path in studio | lounge:
        assert path.startswith("/api/muse/"), path


#: classic だけが出していた経路。**`/api/muse/sessions` は入れない** ——
#: あれは畳んだ撮影室が正しく出している（2026-09-12）。
@pytest.mark.parametrize("path", [
    "/api/muse/report", "/api/muse/steps", "/api/muse/roster",
])
def test_a_retired_route_is_not_served(path):
    from app.muse import api as studio_api
    from app.muse import lounge_api

    assert path not in _paths(lounge_api.router)
    assert path not in _paths(studio_api.router)


def test_the_lounge_router_does_not_drag_the_turn_engine_in():
    """If the lounge's doors imported classic's studio, retiring it would mean
    nothing."""
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


#: `private/muse_classic/` へ退いたもの。`backend/app/` の側からは、もう引けない。
#:
#: **`service` / `api` / `pipeline_view` は一覧から外した。** 撮影室を `muse` に
#: 畳んだとき（2026-09-12）、同じ名前が**撮影室自身のモジュール**として戻ってきた
#: ので、名前で見分けられなくなった。残っているのは classic にしか無かった三つ。
RETIRED = ("schema", "report", "harvest")


def test_nothing_living_reaches_for_a_retired_module():
    """That the foundations never reach for a retired module.

    **Actually hit.** `facets.write_facet` did `from .service import drop_banned`
    inside the function, so importing the module noticed nothing and only the tests
    that walked that line failed with `ModuleNotFoundError`. An import hidden
    inside a function is visible only in the AST — so this reads **the tree**, not
    the lines.
    """
    import ast

    bad: list[str] = []
    for path in sorted(Path("backend/app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pkg_muse = path.parts[2:3] == ("muse",)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                # `from .service import x` / `from ..muse.service import x`
                if mod.split(".")[-1] in RETIRED and (
                    mod.endswith("muse." + mod.split(".")[-1])
                    or (pkg_muse and node.level and "." not in mod)
                ):
                    bad.append(f"{path}:{node.lineno} from {'.' * node.level}{mod}")
                # `from .muse import service` / `from . import service`
                if pkg_muse and node.level and not mod:
                    for a in node.names:
                        if a.name in RETIRED:
                            bad.append(f"{path}:{node.lineno} import {a.name}")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    parts = a.name.split(".")
                    if len(parts) > 1 and parts[-2] == "muse" and parts[-1] in RETIRED:
                        bad.append(f"{path}:{node.lineno} import {a.name}")
    assert not bad, "退役したモジュールを引いている:\n" + "\n".join(bad)


def test_the_studio_marker_on_the_saved_rows_is_not_tidied_up():
    """**The `STUDIO` string is a stored value. However old the name looks, it does
    not move.**

    When the studio folded from `muse_refine` into `muse` (2026-09-12) this one
    word stayed — it is written into the session rows, and `_require_studio` uses
    it to tell its own rows apart. Tidy it for consistency and **every existing row
    stops opening**.
    """
    from app.muse import service

    assert service.STUDIO == "muse_refine"
    src = Path("backend/app/muse/service.py").read_text(encoding="utf-8")
    head = src[:src.index("STUDIO =")]
    assert "保存されている値なので変えない" in head, "理由を添えずに置かない"


"""**Muse Classic を退役させても、楽屋は絶対に残す。**（2026-09-12）

総監督のご判断で Muse Refine を正規の Muse にした。撮影室は一つ。classic の
ターンエンジンと画面は `private/muse_classic/` へ退き、撮影室は
`backend/app/muse/` に畳んで `/api/muse` から出している。

楽屋は据え置き（[[project-muse-circle-must-stay]]）。URL も変えない ——
画面（`CharacterGallery` / `LoungePanel`）がそう叩いている。撮影室の口も
`/api/muse` に来たので、**楽屋と同じ屋根の下で経路がぶつからないこと**が
ここの見どころになった。
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


def test_there_is_one_studio_and_it_answers_under_api_muse():
    """撮影室は一つ。classic の口は載せず、Refine の口は `muse` に畳んだ。"""
    from app.main import app

    src = Path("backend/app/main.py").read_text(encoding="utf-8")
    # classic の router も、畳む前の名前も、もう出てこない
    assert "muse_refine_router" not in src
    assert "from .muse_refine" not in src
    assert "muse_lounge_router" in src
    assert "from .muse.api import router as muse_router" in src
    assert app is not None


def test_the_two_routers_under_api_muse_do_not_collide():
    """撮影室と楽屋が同じ接頭辞に並んだ。**同じ経路を二度出したら後が勝つ。**"""
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


#: `private/muse_classic/` へ退いたもの。`backend/app/` の側からは、もう引けない。
#:
#: **`service` / `api` / `pipeline_view` は一覧から外した。** 撮影室を `muse` に
#: 畳んだとき（2026-09-12）、同じ名前が**撮影室自身のモジュール**として戻ってきた
#: ので、名前で見分けられなくなった。残っているのは classic にしか無かった三つ。
RETIRED = ("schema", "report", "harvest")


def test_nothing_living_reaches_for_a_retired_module():
    """土台が退役したモジュールを引いていないこと。

    **実際に踏んだ。** `facets.write_facet` が `from .service import drop_banned`
    を関数の中でやっていたので、import では気付かず、その行を通る試験だけが
    `ModuleNotFoundError` で落ちた。関数の中に隠れた import は AST でしか見えない
    —— だから行ごとではなく**木で**見る。
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
    """**`STUDIO` の文字列は保存値。名前が古く見えても動かさない。**

    撮影室を `muse_refine` から `muse` に畳んだ（2026-09-12）ときも、この一語は
    据え置いた —— セッションの行に書いてあり、`_require_studio` が「自分の行か」を
    これで見分ける。揃えたくなって直すと、**これまでの行が全部開かなくなる**。
    """
    from app.muse import service

    assert service.STUDIO == "muse_refine"
    src = Path("backend/app/muse/service.py").read_text(encoding="utf-8")
    head = src[:src.index("STUDIO =")]
    assert "保存されている値なので変えない" in head, "理由を添えずに置かない"


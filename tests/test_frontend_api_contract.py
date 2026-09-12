"""**画面が叩く口が、裏に実在すること。**（2026-09-13）

移行を三段重ねた（classic 退役 → `muse` へ改名 → 未使用関数の切り取り）ので、
総監督のご指示で「管理画面から呼び出す機能が動かなくなっていないか」を確かめる。

**試験で落ちない壊れ方**がある —— 画面が `/api/muse-refine/…` を叩き続けていても、
Python の試験は一本も落ちない。誰も踏まないから。実際にそういう壊れ方を
一度している（`_ollama(request)` という存在しない補助関数で 500）。

だから**画面の文字列と、ルータが出す経路を突き合わせる**。いま 134本が通る。

読み方:

    画面側   `'/api/…'` `"/api/…"` `` `/api/…` `` の文字列を集め、
             `${…}` は一つの区間として `{x}` に畳む
    裏側     `main.py` が繋いでいる15本のルータの `APIRoute`
             （`/api/health` と `/api/token` は `main` に直付けなので足す）
"""
from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

#: `main.py` に直に生えている口（ルータ経由ではない）。
DIRECT = ("/api/health", "/api/token")


def _served() -> set[str]:
    from fastapi.routing import APIRoute

    src = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    mods = re.findall(r"^from \.([\w.]+) import router as \w+", src, re.M)
    assert len(mods) >= 10, f"ルータの読み取りが崩れている（{len(mods)}本）"
    paths = set(DIRECT)
    for mod in mods:
        router = importlib.import_module(f"app.{mod}").router
        paths |= {r.path for r in router.routes if isinstance(r, APIRoute)}
    return paths


def _called() -> dict[str, set[str]]:
    rx = re.compile(r"""['"`](/api/[A-Za-z0-9/_{}$().?=&:*-]+)['"`]""")
    out: dict[str, set[str]] = {}
    files = subprocess.run(
        ["git", "ls-files", "frontend/src"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    for rel in files:
        if not rel.endswith((".vue", ".js")):
            continue
        for m in rx.finditer((ROOT / rel).read_text(encoding="utf-8")):
            url = re.sub(r"\$\{[^}]*\}", "{x}", m.group(1).split("?")[0]).rstrip("/")
            out.setdefault(url, set()).add(rel)
    return out


def _matches(url: str, served: set[str]) -> bool:
    for path in served:
        if re.match("^" + re.sub(r"\{[^}]+\}", "[^/]+", path).rstrip("/") + "$", url):
            return True
    return False


#: 画面が `${変数}` で口そのものを組む所。経路名が実行時に決まるので、
#: 文字列からは当てられない。**中身は別の試験が見ている**。
BUILT_AT_RUNTIME = {
    "/api/invoke/{x}",              # useInvokeSession: evolve / breed などを変数で
    "/api/muse/sessions/{x}/{x}",   # MusePanel: open|table, board|approve を変数で
}


def test_every_url_the_screen_calls_is_served():
    served = _served()
    called = _called()
    assert len(called) > 100, f"画面から URL が読めていない（{len(called)}本）"
    missing = {
        u: v for u, v in called.items()
        if u not in BUILT_AT_RUNTIME and not _matches(u, served)
    }
    assert not missing, "画面が叩く口が裏に無い:\n" + "\n".join(
        f"  {u}  ← {sorted(v)[0]}" for u, v in sorted(missing.items())
    )


@pytest.mark.parametrize("url", [
    # 管理画面の背骨。消えると「押しても何も起きない」になる
    "/api/admin/stats", "/api/admin/config", "/api/admin/schema/status",
    "/api/admin/backup/status", "/api/admin/vectors/rebuild",
    "/api/admin/character-compat/matrix",
    "/api/characters/erase-memory", "/api/characters/sync-muse",
    # Muse の背骨
    "/api/muse/catalog", "/api/muse/sessions", "/api/muse/lounge/threads",
    "/api/muse/handpost",
])
def test_the_named_ones_are_still_there(url: str):
    """名前で押さえておく口。一覧の照合が緩んでも、ここは落ちる。"""
    assert _matches(url, _served()), url


def test_the_retired_studio_has_no_door_left():
    """classic の口も、改名前の `muse-refine` も出していないこと。"""
    served = _served()
    assert not any(p.startswith("/api/muse-refine") for p in served)
    for gone in ("/api/muse/report", "/api/muse/steps", "/api/muse/roster"):
        assert gone not in served, gone


# ── 画面が送る欄を、裏が受け取れること ────────────────────────────────────

def test_every_input_the_screen_patches_is_accepted():
    """**URL が在っても、欄が無ければ黙って捨てられる。**（2026-09-13）

    実際に踏んだ: 画面には撮影班のプリセット（`muse.crewPreset`）の選択が
    前から出ていたのに、`InputsPatch` に `crew_preset` の欄が無かった。
    pydantic は知らない欄を黙って落とすので、`{"crew_preset": "photoreal"}` が
    `{}` になり、**スタジオ撮りは常に `standard` の18席**で回っていた
    （`photoreal` なら13席、`flat` なら12席で済む）。

    URL の照合だけでは見つからない壊れ方なので、**送る欄の名前**も突き合わせる。
    """
    from app.muse.api import InputsPatch

    panel = (ROOT / "frontend/src/components/MusePanel.vue").read_text(encoding="utf-8")
    sent: set[str] = set()
    for m in re.finditer(r"patchInputs\(\s*\{([^}]*)\}", panel):
        sent |= set(re.findall(r"([A-Za-z_][\w]*)\s*:", m.group(1)))
    assert sent, "画面から patchInputs の欄が読めていない"

    known = set(InputsPatch.model_fields)
    lost = sorted(sent - known)
    assert not lost, f"画面が送っているのに裏が受け取らない欄: {lost}"

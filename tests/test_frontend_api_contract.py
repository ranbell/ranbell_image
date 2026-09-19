"""**Every door the panel knocks on exists on the back side.** (2026-09-13)

Three migrations were stacked (retiring classic → renaming to `muse` → cutting
unused functions), so at the Showrunner's instruction we check that "nothing
called from the admin screen stopped working".

**There is a way to break that no test catches** — the panel can keep calling
`/api/muse-refine/…` and not one Python test fails, because nothing walks that
road. It has broken that way once already (a 500 from a helper
`_ollama(request)` that does not exist).

So **the panel's strings are matched against the routes the routers serve**. 134
of them pass today.

How to read it:

    panel side   collect the strings `'/api/…'`, `"/api/…"` and `` `/api/…` ``,
                 folding `${…}` into a single `{x}` segment
    back side    the `APIRoute`s of the fifteen routers `main.py` mounts
                 (`/api/health` and `/api/token` are attached to `main` itself,
                 so they are added)
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
    """Doors pinned by name. Even if the listing check loosens, these fail."""
    assert _matches(url, _served()), url


def test_the_retired_studio_has_no_door_left():
    """Neither classic's doors nor the pre-rename `muse-refine` are served."""
    served = _served()
    assert not any(p.startswith("/api/muse-refine") for p in served)
    for gone in ("/api/muse/report", "/api/muse/steps", "/api/muse/roster"):
        assert gone not in served, gone


# ── 画面が送る欄を、裏が受け取れること ────────────────────────────────────

def test_every_input_the_screen_patches_is_accepted():
    """**The URL can exist and the field still be dropped in silence.**
    (2026-09-13)

    Actually hit: the panel had shown the crew preset selector (`muse.crewPreset`)
    for some time, and `InputsPatch` had no `crew_preset` field. pydantic drops
    unknown fields silently, so `{"crew_preset": "photoreal"}` became `{}` and
    **the studio shoot always ran `standard`'s eighteen seats** (`photoreal` needs
    thirteen, `flat` twelve).

    Matching URLs alone never finds that, so **the names of the fields sent** are
    matched too.
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


def test_every_input_the_screen_reads_is_returned():
    """**Being able to send it is not enough; if it does not come back, the screen
    lies.** (2026-09-13)

    `crew_preset` / `banter_mode` were added to `InputsPatch` the day before so
    they could be sent, and `service.public_view` did not return them. The panel
    reads `inputs.crew_preset || 'standard'`, so **choosing and saving still shows
    `standard` after reopening** — live, the seat count really was switching
    18→14→13 while the display alone told a lie.

    One way (sending) and the round trip (coming back) need separate tests.
    """
    from app.muse.service import new_session, public_view

    panel = (ROOT / "frontend/src/components/MusePanel.vue").read_text(encoding="utf-8")
    read = set(re.findall(r"inputs\.([A-Za-z_]\w*)", panel))
    read -= {"value"}          # `$event.target.value` の誤検出

    session = new_session({})
    session["inputs"] = {**session["inputs"],
                         **{k: "x" for k in read if k not in session["inputs"]}}
    got = public_view(session)["inputs"]

    missing = sorted(k for k in read if k not in got)
    assert not missing, f"画面が読むのに返っていない欄: {missing}"


def test_the_crew_preset_survives_the_round_trip():
    """The chosen crew is stored and comes back — and the seat count changes with
    it."""
    from app.muse import crew, crew_room
    from app.muse.api import InputsPatch
    from app.muse.service import new_session, public_view

    sent = InputsPatch(crew_preset="flat").model_dump(exclude_none=True)
    assert sent == {"crew_preset": "flat"}, sent

    session = new_session({})
    session["inputs"] = {**session["inputs"], **sent,
                         "crew_ids": list(crew.resolve_crew(preset="flat"))}
    assert public_view(session)["inputs"]["crew_preset"] == "flat"
    session[crew_room.TABLE_OPEN] = True
    assert len(crew_room.cast_of(session)) == 12, "flat は12席"

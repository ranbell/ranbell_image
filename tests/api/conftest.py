"""Stub heavy dependencies (FastAPI, Pydantic, etc.) so pure-logic tests
can run in a minimal venv without installing the full application stack.

The third-party stubs are process-wide, so they are only installed when the
real package is missing — otherwise a full-suite run would hand the mocks to
later test modules that need the genuine pydantic.
"""
import importlib.util
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name: str) -> ModuleType:
    m = ModuleType(name)
    sys.modules[name] = m
    return m


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ── FastAPI stubs ─────────────────────────────────────────────────────────────
if not _installed("fastapi"):
    _fa = _stub("fastapi")
    _fa.APIRouter = MagicMock(return_value=MagicMock())
    _fa.HTTPException = Exception
    _fa.Request = object
    _fa.Depends = lambda f: f

    _far = _stub("fastapi.responses")
    _far.StreamingResponse = object

# ── Pydantic stubs ────────────────────────────────────────────────────────────
if not _installed("pydantic"):
    _pd = _stub("pydantic")

    class _BaseModel:
        def __init_subclass__(cls, **kw): pass
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
        def model_dump(self): return self.__dict__.copy()

    class _Field:
        def __call__(self, *a, **kw): return None
        def __getattr__(self, name): return self

    _pd.BaseModel = _BaseModel
    _pd.Field = _Field()

# ── App-internal stubs ────────────────────────────────────────────────────────
for _mod in (
    "app.config",
    "app.ai.tile_image",
    "app.runtime_config",
    "app.scanner.scanner",
    "app.spooler.models",
    "app.api.sort_utils",
):
    _stub(_mod)

# Provide minimal attribute shapes expected by the module
sys.modules["app.config"].settings = MagicMock()
sys.modules["app.runtime_config"].get_runtime_config = MagicMock()
sys.modules["app.ai.tile_image"].create_tile_image = MagicMock()
sys.modules["app.scanner.scanner"].register_image = MagicMock()
sys.modules["app.spooler.models"].JobLane = MagicMock()
sys.modules["app.api.sort_utils"].sort_docs = MagicMock()


# ── The real app, driven through its real routes ──────────────────────────────
#
# `api` puts an in-memory Qdrant behind `backend.app.main.app` and talks to it over
# HTTP (httpx + ASGI, no lifespan — so no boot, no watcher, no schedulers). What a
# test sees is what the panel sees: status codes, bodies, and the rows left behind.
#
# The spooler records what was queued and runs nothing; what a job does is tested
# where the job is. Ollama and ComfyUI are tripwires: an API test that reaches
# either fails, so none of these quietly depends on a model being up.

from types import SimpleNamespace  # noqa: E402

import pytest_asyncio  # noqa: E402


class RecordingSpooler:
    def __init__(self):
        self.jobs: list[SimpleNamespace] = []
        self.cancelled: list[str] = []

    def submit(self, lane, title, func, meta=None, *, priority=0, **kwargs):
        job_id = f"{getattr(lane, 'value', lane)}-{len(self.jobs) + 1:06d}"
        self.jobs.append(SimpleNamespace(id=job_id, lane=lane, title=title, func=func,
                                         meta=meta or {}, kwargs=kwargs, state="queued"))
        return job_id

    async def cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True

    def snapshot(self) -> list[dict]:
        return [{"id": j.id, "title": j.title, "state": j.state, "meta": j.meta,
                 "lane": getattr(j.lane, "value", j.lane)}
                for j in reversed(self.jobs)]


class Tripwire:
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr):
        raise AssertionError(f"an API test reached {self._name}.{attr}")


@pytest_asyncio.fixture()
async def api(tmp_path, monkeypatch):
    import httpx

    from backend.app.config import settings
    from backend.app.main import app
    from tests.memdb import memory_db

    for name in ("source_images_dir", "generated_images_dir", "thumbnails_dir"):
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setattr(settings, name, d)

    from backend.app.ai import tag_analyzer
    from backend.app.api.images import invalidate_image_caches
    invalidate_image_caches()
    monkeypatch.setattr(tag_analyzer, "_cooccurrence_cache", None)   # 5 minutes, by design

    saved = dict(app.state._state)
    async with memory_db() as db:
        spooler = RecordingSpooler()
        app.state.db = db
        app.state.spooler = spooler
        app.state.ollama = Tripwire("ollama")
        app.state.comfy = Tripwire("comfy")
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://api",
                                     headers={"X-API-Token": settings.api_token}) as http:
            yield SimpleNamespace(http=http, db=db, spooler=spooler, settings=settings, tmp=tmp_path)
    app.state._state.clear()
    app.state._state.update(saved)
    invalidate_image_caches()

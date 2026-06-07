"""Stub heavy dependencies (FastAPI, Pydantic, etc.) so pure-logic tests
can run in a minimal venv without installing the full application stack."""
import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name: str) -> ModuleType:
    m = ModuleType(name)
    sys.modules[name] = m
    return m


# ── FastAPI stubs ─────────────────────────────────────────────────────────────
_fa = _stub("fastapi")
_fa.APIRouter = MagicMock(return_value=MagicMock())
_fa.HTTPException = Exception
_fa.Request = object
_fa.Depends = lambda f: f

_far = _stub("fastapi.responses")
_far.StreamingResponse = object

# ── Pydantic stubs ────────────────────────────────────────────────────────────
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

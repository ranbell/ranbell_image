"""Tests for the stories-collection CRUD wrapper."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

# qdrant_client is not installed in the minimal test venv. db.py only needs
# `models` for PointStruct/PointIdsList (the fake db below never inspects them)
# and STORIES_COLLECTION from db.qdrant_client, which imports the real client.
if "qdrant_client" not in sys.modules:
    _qcm = ModuleType("qdrant_client.models")
    _qcm.PointStruct = lambda **kw: SimpleNamespace(**kw)
    _qcm.PointIdsList = MagicMock()
    _qcm.OrderBy = MagicMock()
    _qcm.Direction = MagicMock()
    _qcm.PointVectors = MagicMock()
    sys.modules["qdrant_client.models"] = _qcm

    _qc = ModuleType("qdrant_client")
    _qc.models = _qcm
    _qc.AsyncQdrantClient = MagicMock()
    _qc.QdrantClient = MagicMock()
    sys.modules["qdrant_client"] = _qc

    _qch = ModuleType("qdrant_client.http")
    _qche = ModuleType("qdrant_client.http.exceptions")
    _qche.UnexpectedResponse = type("UnexpectedResponse", (Exception,), {})
    sys.modules["qdrant_client.http"] = _qch
    sys.modules["qdrant_client.http.exceptions"] = _qche

# db.py imports STORIES_COLLECTION from app.db.qdrant_client, which pulls in
# app.config → pydantic_settings. Stub the one constant it actually needs
# rather than the whole settings stack.
if "app.db.qdrant_client" not in sys.modules:
    _appdb = ModuleType("app.db")
    _appdb.__path__ = []
    sys.modules.setdefault("app.db", _appdb)
    _stub_qc = ModuleType("app.db.qdrant_client")
    _stub_qc.STORIES_COLLECTION = "stories"
    sys.modules["app.db.qdrant_client"] = _stub_qc

from app.story.db import fork_draft  # noqa: E402


class _FakeQdrant:
    def __init__(self):
        self.upserted: list = []

    async def upsert(self, *, collection_name, points):
        self.upserted.append(points[0])


def _fake_db():
    return SimpleNamespace(_qc=_FakeQdrant())


def _finalized_story(**extra):
    return {
        "story_id": "old",
        "base_image_id": "sha-1",
        "base_time_axis": "present",
        "worldview": "w",
        "workflow_name": "wf.json",
        "group_id": "g",
        "time_scale": "hours",
        "user_topic": "topic",
        "locale": "ja",
        "status": "final",
        "candidates": [{"id": "A"}],
        "context": {"character_desc": "1girl"},
        **extra,
    }


def test_fork_draft_inherits_pinups():
    """The pinup belongs to the base IMAGE, not to one telling of the story.

    Without this the portrait vanished from the Storybook on every respin: the
    fork started with no pinups, and the expand runner would not rebuild one
    because the image doc already had it.
    """
    db = _fake_db()
    story = _finalized_story(pinups=["pin-a", "pin-b"], pinup_image_id="pin-b")
    asyncio.run(fork_draft(db, story))
    payload = db._qc.upserted[0].payload
    assert payload["pinups"] == ["pin-a", "pin-b"]
    assert payload["pinup_image_id"] == "pin-b"
    # ...but the fork is still a fresh draft that Phase 2 writes into.
    assert payload["status"] == "draft"
    assert payload["respin_history"] == []
    assert payload["base_image_id"] == "sha-1"


def test_fork_draft_without_pinups_omits_the_keys():
    db = _fake_db()
    asyncio.run(fork_draft(db, _finalized_story()))
    payload = db._qc.upserted[0].payload
    assert not payload.get("pinups")
    assert not payload.get("pinup_image_id")


def test_fork_draft_legacy_single_pinup_field():
    """Records written before `pinups[]` existed carry only pinup_image_id."""
    db = _fake_db()
    asyncio.run(fork_draft(db, _finalized_story(pinup_image_id="legacy")))
    payload = db._qc.upserted[0].payload
    assert payload["pinup_image_id"] == "legacy"

"""Unit tests for the pieces added on top of the Muse duet flow:

- finish_session queuing a diary job for *both* actors, not just the lead.
- the second diary to land queuing chemistry generation exactly once.
- the chemistry job writing a symmetric record onto both characters.
- identity.parse_duet_speakers, the A:/B: dialogue parser.
"""
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import compat as compat_mod
from backend.app.characters import presets as presets_db
from backend.app.muse import chain as muse_chain
from backend.app.muse import identity as muse_identity
from backend.app.muse import service as muse_service


class FakeSessionQC:
    """Just enough of the Qdrant client for session_db.save/load to round-trip
    a session — _record_diary_result reloads the session it just saved to
    decide whether both actors' diaries have landed, so (unlike the plain
    MagicMock in test_actress_diary.py) this needs an actual store."""
    def __init__(self):
        self.store: dict[str, dict] = {}

    async def upsert(self, *, collection_name, points):
        for p in points:
            self.store[p.id] = dict(p.payload or {})

    async def retrieve(self, *, collection_name, ids, with_payload=True):
        return [types.SimpleNamespace(payload=self.store[i]) for i in ids if i in self.store]


class FakeDB:
    """Enough of the db for session_db.save/load and get_runtime_config."""
    def __init__(self, config=None):
        self._config = config or {}
        self._qc = FakeSessionQC()

    async def get_config(self):
        return dict(self._config)


class FakeSpooler:
    def __init__(self):
        self.calls = []

    def submit(self, lane, title, func, meta=None, **kwargs):
        self.calls.append({"lane": lane, "title": title, "func": func,
                           "meta": meta or {}, "kwargs": kwargs})
        return f"{lane.value}-000001"


def fake_multi_preset_store(monkeypatch, presets):
    """``presets``: ``{preset_id: preset_dict}``. Reverts after the test."""
    async def fake_get_preset(db, preset_id):
        return presets.get(preset_id)

    async def fake_update_preset(db, preset_id, patch):
        presets[preset_id].update(patch)
        return presets[preset_id]

    monkeypatch.setattr(presets_db, "get_preset", fake_get_preset)
    monkeypatch.setattr(presets_db, "update_preset", fake_update_preset)
    return presets


def _duet_session(**over):
    s = {
        "session_id": "s-duet-1",
        "mode": "duet",
        "inputs": {
            "character_id": "c001", "partner_preset": "c002",
            "model": "test-model", "num_ctx": 8192, "locale": "ja",
        },
        "character": {"character_id": "c001", "name_ja": "アリス"},
        "chat": [],
        "shoot": {"prompt": "2girls, studio", "images": ["sha-abc"]},
    }
    s.update(over)
    return s


# ── finish_session queues one job per actor in a duet ───────────────────────
def diaries(spooler):
    """spool された**日記のジョブだけ**。

    総数で縛ると、撮影の後ろで走る別のジョブ（お出かけの生成など）が増える
    たびに落ちる ―― 実際に6件が落ちた。数えたいのは日記なので、日記で数える。
    """
    return [c for c in spooler.calls
            if c["title"] == "generate_actress_diary"]


@pytest.mark.asyncio
async def test_finish_session_duet_spools_two_diary_jobs(monkeypatch):
    fake_multi_preset_store(monkeypatch, {
        "c001": {"id": "c001", "name_ja": "アリス", "diaries": []},
        "c002": {"id": "c002", "name_ja": "ベル", "diaries": []},
    })
    db, spooler = FakeDB({"vlm_model": "cfg-model"}), FakeSpooler()
    session = await muse_service.finish_session(db, spooler, _duet_session(), ollama="OLL")

    assert session["status"] == "finished"
    assert len(diaries(spooler)) == 2
    ids = {c["kwargs"]["character_id"] for c in diaries(spooler)}
    assert ids == {"c001", "c002"}
    for c in diaries(spooler):
        assert c["func"] is muse_service.run_generate_actress_diary_job
        assert c["kwargs"]["spooler"] is spooler
    assert session["diary"]["entries"].keys() == {"c001", "c002"}


@pytest.mark.asyncio
async def test_wrapping_a_duet_twice_still_writes_one_diary_each(monkeypatch):
    fake_multi_preset_store(monkeypatch, {
        "c001": {"id": "c001", "diaries": []}, "c002": {"id": "c002", "diaries": []},
    })
    db, spooler = FakeDB(), FakeSpooler()
    session = _duet_session()
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    assert len(diaries(spooler)) == 2


# ── the second landing diary queues chemistry, exactly once ────────────────
@pytest.mark.asyncio
async def test_second_duet_diary_landing_queues_chemistry(monkeypatch):
    fake_multi_preset_store(monkeypatch, {
        "c001": {"id": "c001", "name_ja": "アリス", "diaries": []},
        "c002": {"id": "c002", "name_ja": "ベル", "diaries": []},
    })
    monkeypatch.setattr(
        compat_mod, "compatibility",
        AsyncMock(return_value={"base": 0.5, "co_appearances": 1, "score": 0.53, "tier": "close"}),
    )

    async def fake_call(ollama, *, system, prompt, model, images, num_ctx, think):
        return ("SUMMARY_JA: 楽しかった\nSUMMARY_EN: Fun\n"
                "CONTENT_JA:\n今日は二人で撮影した。\n"
                "CONTENT_EN:\nWe shot together today.")

    monkeypatch.setattr(muse_chain, "_call", fake_call)

    db, spooler = FakeDB({"vlm_model": "cfg-model"}), FakeSpooler()
    session = _duet_session()
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    diary_calls = diaries(spooler)
    assert len(diary_calls) == 2

    for call in diary_calls:
        result = await call["func"]("R", "C", **call["kwargs"])
        assert result["status"] == "ok"

    chemistry_calls = [c for c in spooler.calls if c["title"] == "generate_actress_chemistry"]
    assert len(chemistry_calls) == 1
    kwargs = chemistry_calls[0]["kwargs"]
    assert {kwargs["character_a_id"], kwargs["character_b_id"]} == {"c001", "c002"}


@pytest.mark.asyncio
async def test_chemistry_is_not_queued_when_only_one_diary_succeeds(monkeypatch):
    fake_multi_preset_store(monkeypatch, {
        "c001": {"id": "c001", "diaries": []}, "c002": {"id": "c002", "diaries": []},
    })
    calls = {"n": 0}

    async def flaky_call(ollama, *, system, prompt, model, images, num_ctx, think):
        calls["n"] += 1
        # The first job gets both of its attempts (the retry included) back as
        # an unparseable fragment (unlabelled plain text gets salvaged as her
        # diary — see diary.py — so this has to actually fail parsing, not
        # just look messy) and fails outright; the second job's first attempt
        # succeeds.
        if calls["n"] <= 2:
            return '{"summary_ja":'
        return "SUMMARY_JA: ok\nSUMMARY_EN: ok\nCONTENT_JA:\n本文\nCONTENT_EN:\nBody"

    monkeypatch.setattr(muse_chain, "_call", flaky_call)
    db, spooler = FakeDB(), FakeSpooler()
    session = _duet_session()
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    for call in list(spooler.calls):
        await call["func"]("R", "C", **call["kwargs"])

    assert not [c for c in spooler.calls if c["title"] == "generate_actress_chemistry"]


# ── the chemistry job writes a symmetric record ─────────────────────────────
@pytest.mark.asyncio
async def test_chemistry_job_writes_a_record_on_both_characters(monkeypatch):
    presets = fake_multi_preset_store(monkeypatch, {
        "c001": {"id": "c001", "name_ja": "アリス", "diaries": [
            {"id": "d1", "content_ja": "楽しかった", "summary_ja": "楽しかった", "timestamp": 1.0},
        ]},
        "c002": {"id": "c002", "name_ja": "ベル", "diaries": [
            {"id": "d2", "content_ja": "嬉しかった", "summary_ja": "嬉しかった", "timestamp": 1.0},
        ]},
    })
    monkeypatch.setattr(
        compat_mod, "compatibility",
        AsyncMock(return_value={"base": 0.5, "co_appearances": 1, "score": 0.53, "tier": "close"}),
    )
    monkeypatch.setattr(
        muse_chain, "_call",
        AsyncMock(return_value=(
            "SUMMARY_JA: 息が合ってきた\nSUMMARY_EN: Getting in sync\n"
            "CONTENT_JA:\n二人の距離が近づいてきた。\n"
            "CONTENT_EN:\nThe two of them are getting closer."
        )),
    )
    db = FakeDB()
    result = await muse_service.run_generate_chemistry_job(
        "R", "C", db=db, ollama="OLL", session_id="s-duet-1",
        character_a_id="c001", character_b_id="c002",
        diary_id_a="d1", diary_id_b="d2", model="m", num_ctx=None,
    )
    assert result["status"] == "ok"
    assert len(presets["c001"]["chemistry"]) == 1
    assert len(presets["c002"]["chemistry"]) == 1
    rec_a = presets["c001"]["chemistry"][0]
    assert rec_a["partner_character_id"] == "c002"
    assert rec_a["partner_name_ja"] == "ベル"
    assert rec_a["tier"] == "close"
    assert len(rec_a["sources"]) == 2
    rec_b = presets["c002"]["chemistry"][0]
    assert rec_b["partner_character_id"] == "c001"
    assert rec_b["partner_name_ja"] == "アリス"


# ── identity.parse_duet_speakers, the A:/B: dialogue parser ─────────────────
def test_parse_duet_speakers_splits_fixed_markers():
    raw = "A: 今日は楽しかったね\nB: うん、また撮ろう"
    turns = muse_identity.parse_duet_speakers(raw)
    assert turns == [
        {"speaker": "A", "text": "今日は楽しかったね"},
        {"speaker": "B", "text": "うん、また撮ろう"},
    ]


def test_parse_duet_speakers_strips_leading_say_label():
    raw = "SAY: A: こんにちは\nB: どうも"
    turns = muse_identity.parse_duet_speakers(raw)
    assert turns[0] == {"speaker": "A", "text": "こんにちは"}


def test_parse_duet_speakers_never_invents_a_fake_speaker():
    """The reported bug: a leaked label like "System A:" must not become a
    trusted speaker — only the literal A:/B: markers are trusted."""
    raw = "System A: hello\nSystem B: hi there"
    assert muse_identity.parse_duet_speakers(raw) is None


def test_parse_duet_speakers_folds_wrapped_lines_into_the_same_speaker():
    raw = "A: 一行目\nまだ続き\nB: 相手の台詞"
    turns = muse_identity.parse_duet_speakers(raw)
    assert turns[0]["text"] == "一行目 まだ続き"
    assert turns[1]["text"] == "相手の台詞"


def test_parse_duet_speakers_empty_input_returns_none():
    assert muse_identity.parse_duet_speakers("") is None
    assert muse_identity.parse_duet_speakers("   ") is None

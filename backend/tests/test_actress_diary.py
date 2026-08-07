"""Unit tests for Actress Secret Diary and JobSpooler Integration."""
import sys
import time
import types
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import api as characters_api
from backend.app.characters import presets as presets_db
from backend.app.muse import chain as muse_chain
from backend.app.muse import service as muse_service
from backend.app.muse import crew as muse_crew
from backend.app.spooler.models import JobLane


def fake_preset_store(monkeypatch, preset):
    """Point presets_db at one in-memory character. Reverts after the test —
    assigning onto the module directly leaks the fake into every later test."""
    async def fake_get_preset(db, preset_id):
        return preset if preset_id == preset.get("id") else None

    async def fake_update_preset(db, preset_id, patch):
        preset.update(patch)
        return preset

    monkeypatch.setattr(presets_db, "get_preset", fake_get_preset)
    monkeypatch.setattr(presets_db, "update_preset", fake_update_preset)
    return preset


class FakeDB:
    """Enough of the db for session_db.save and get_runtime_config."""
    def __init__(self, config=None):
        self._config = config or {}
        self._qc = MagicMock()
        self._qc.upsert = AsyncMock()

    async def get_config(self):
        return dict(self._config)


class FakeSpooler:
    def __init__(self):
        self.calls = []

    def submit(self, lane, title, func, meta=None, **kwargs):
        self.calls.append({"lane": lane, "title": title, "func": func,
                           "meta": meta or {}, "kwargs": kwargs})
        return f"{lane.value}-000001"


def fake_request(db, ollama=None):
    state = types.SimpleNamespace(db=db, ollama=ollama)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_preset_diaries_crud(monkeypatch):
    """Test reading, writing, marking read, and retrieving diary summaries."""
    mock_db = MagicMock()

    fake_preset_store(monkeypatch, {
        "id": "c001",
        "slug": "test_actress",
        "name": "Test Actress",
        "diaries": []
    })

    # 1. Add diary
    diary_data = {
        "id": "diary-123",
        "timestamp": time.time(),
        "image_id": "img-sha256-abc",
        "summary": "暗室撮影で褒められて照れたこと",
        "content_ja": "今日は総監督と暗室で撮影した。褒められて少し顔が赤くなった。",
        "read": False,
        "secret_banter_fired": False
    }
    added = await presets_db.add_preset_diary(mock_db, "c001", diary_data)
    assert added["id"] == "diary-123"
    
    # 2. Get diaries
    diaries = await presets_db.get_preset_diaries(mock_db, "c001")
    assert len(diaries) == 1
    assert diaries[0]["read"] is False

    # 3. Mark read
    marked = await presets_db.mark_diary_read(mock_db, "c001", "diary-123")
    assert marked["read"] is True

    # 4. Get recent summaries
    summaries = await presets_db.get_recent_diary_summaries(mock_db, "c001", limit=3)
    assert len(summaries) == 1
    assert summaries[0]["summary"] == "暗室撮影で褒められて照れたこと"


@pytest.mark.asyncio
async def test_actress_diary_prompts():
    """Test actress diary system prompt creation for JA and EN."""
    char = {
        "name_ja": "アリス",
        "voice_ja": "丁寧でおしとやか",
        "personality": {
            "summary_ja": "素直になれない少女",
            "charm_ja": "耳がすぐ赤くなる",
            "inner_ja": ["総監督の言葉が嬉しい"]
        }
    }
    prompt = muse_crew.actress_diary_prompt(char, session_log="総監督: 素晴らしい表情だね", photo_desc="暗室での微笑み")
    assert "アリス" in prompt
    assert "秘密の非公開日記" in prompt
    assert "content_ja" in prompt
    assert "content_en" in prompt
    assert "総監督の言葉が嬉しい" in prompt



@pytest.mark.asyncio
async def test_secret_banter_prompt():
    """Test actress secret banter reaction prompt creation."""
    char = {
        "name_ja": "アリス",
        "personality": {"charm_ja": "耳がすぐ赤くなる"}
    }
    prompt = muse_crew.actress_secret_banter_prompt(char, diary_summary="褒められて照れたこと")
    assert "アリス" in prompt
    assert "見ちゃいました？" in prompt


# ── the write path: finish → spooler → job ──────────────────────────────────
def _session(**over):
    s = {
        "session_id": "s-1",
        "mode": "",
        "inputs": {"character_id": "c001", "model": "test-model",
                   "num_ctx": 8192, "locale": "ja"},
        "chat": [{"name": "総監督", "text": "いい表情だった"}],
        "shoot": {"prompt": "1girl, darkroom", "images": ["sha-abc"]},
    }
    s.update(over)
    return s


@pytest.mark.asyncio
async def test_finish_session_spools_the_diary_on_the_prompt_lane():
    """There is no UTILITY lane. Naming one raised AttributeError inside the
    request, so the job was never queued and the diary never existed."""
    db, spooler = FakeDB({"vlm_model": "cfg-model"}), FakeSpooler()
    session = await muse_service.finish_session(db, spooler, _session(), ollama="OLL")

    assert session["status"] == "finished"
    assert len(spooler.calls) == 1
    call = spooler.calls[0]
    assert call["lane"] is JobLane.PROMPT       # the lane bound to the GPU resource
    assert call["func"] is muse_service.run_generate_actress_diary_job
    assert call["kwargs"]["character_id"] == "c001"
    assert call["kwargs"]["model"] == "test-model"
    assert call["kwargs"]["num_ctx"] == 8192


@pytest.mark.asyncio
async def test_finish_session_without_a_character_spools_nothing():
    db, spooler = FakeDB(), FakeSpooler()
    session = _session(inputs={"model": "m"})
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    assert spooler.calls == []
    assert session["status"] == "finished"


@pytest.mark.asyncio
async def test_diary_job_runs_the_way_the_spooler_calls_it(monkeypatch):
    """The spooler does `func(reporter, cancel_token, **kwargs)` — two
    positional arguments. The job declared three and would have raised
    TypeError; the LLM helper it called did not exist at all."""
    fake_preset_store(monkeypatch, {"id": "c001", "name_ja": "アリス", "diaries": []})

    seen = {}

    async def fake_call(ollama, *, system, prompt, model, images, num_ctx, think):
        seen.update(system=system, model=model, images=images,
                    num_ctx=num_ctx, think=think)
        return ('{"summary_ja": "褒められた", "summary_en": "Praised",'
                ' "content_ja": "日本語の日記", "content_en": "English diary"}')

    monkeypatch.setattr(muse_chain, "_call", fake_call)

    db, spooler = FakeDB({"vlm_model": "cfg-model"}), FakeSpooler()
    await muse_service.finish_session(db, spooler, _session(), ollama="OLL")
    call = spooler.calls[0]

    # Exactly the spooler's own invocation, from spooler.py.
    result = await call["func"]("REPORTER", "CANCEL", **call["kwargs"])
    assert result["status"] == "ok"

    assert seen["images"] is None and seen["think"] is False
    assert seen["model"] == "test-model"
    assert "秘密の非公開日記" in seen["system"]      # her diary prompt, as the system side
    assert "1girl, darkroom" in seen["system"]      # the shoot reached her
    assert "総監督" in seen["system"]                # and so did the conversation

    diaries = await presets_db.get_preset_diaries(db, "c001")
    assert len(diaries) == 1
    entry = diaries[0]
    assert entry["content_ja"] == "日本語の日記"
    assert entry["content_en"] == "English diary"
    assert entry["summary_en"] == "Praised"
    assert entry["image_id"] == "sha-abc"
    assert entry["read"] is False


@pytest.mark.asyncio
async def test_diary_job_keeps_prose_when_she_ignores_the_json_contract(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": []})
    monkeypatch.setattr(
        muse_chain, "_call",
        AsyncMock(return_value="今日はとても楽しかった。ずっと心臓がうるさかった。"),
    )
    db, spooler = FakeDB(), FakeSpooler()
    await muse_service.finish_session(db, spooler, _session(), ollama="OLL")
    await spooler.calls[0]["func"]("R", "C", **spooler.calls[0]["kwargs"])

    diaries = await presets_db.get_preset_diaries(db, "c001")
    assert len(diaries) == 1                       # the memory is not thrown away
    assert "心臓がうるさかった" in diaries[0]["content_ja"]


@pytest.mark.asyncio
async def test_diary_job_skips_a_character_that_is_gone(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "other", "diaries": []})
    result = await muse_service.run_generate_actress_diary_job(
        "R", "C", db=FakeDB(), ollama=None, session=_session(),
        character_id="c001", model="m", num_ctx=None,
    )
    assert result["status"] == "skipped"


# ── the read path: the two routes the panel was already calling ─────────────
@pytest.mark.asyncio
async def test_list_diaries_is_newest_first(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": [
        {"id": "old", "timestamp": 100.0, "summary_ja": "むかし"},
        {"id": "new", "timestamp": 900.0, "summary_ja": "こないだ"},
    ]})
    res = await characters_api.list_character_diaries("c001", fake_request(FakeDB()))
    assert [d["id"] for d in res["diaries"]] == ["new", "old"]


@pytest.mark.asyncio
async def test_reading_a_diary_makes_her_react_exactly_once(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "name_ja": "アリス", "diaries": [
        {"id": "d1", "timestamp": 1.0, "summary_ja": "褒められた", "read": False},
    ]})
    monkeypatch.setattr(
        muse_chain, "_call", AsyncMock(return_value="SAY: ……み、見ちゃいました？"),
    )
    req = fake_request(FakeDB({"vlm_model": "cfg-model"}), ollama="OLL")

    first = await characters_api.read_character_diary("c001", "d1", req)
    assert first["diary"]["read"] is True
    assert first["banter"] == "……み、見ちゃいました？"   # SAY: stripped

    second = await characters_api.read_character_diary("c001", "d1", req)
    assert second["banter"] == ""                  # one-off, ever


@pytest.mark.asyncio
async def test_a_dead_model_still_returns_the_read_receipt(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": [
        {"id": "d1", "timestamp": 1.0, "read": False},
    ]})
    monkeypatch.setattr(muse_chain, "_call", AsyncMock(side_effect=RuntimeError("down")))
    res = await characters_api.read_character_diary(
        "c001", "d1", fake_request(FakeDB(), ollama="OLL"),
    )
    assert res["diary"]["read"] is True and res["banter"] == ""


@pytest.mark.asyncio
async def test_reading_a_diary_that_is_not_there_is_a_404(monkeypatch):
    from fastapi import HTTPException
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": []})
    with pytest.raises(HTTPException) as err:
        await characters_api.read_character_diary("c001", "nope", fake_request(FakeDB()))
    assert err.value.status_code == 404


# ── she remembers the last few shoots when they work alone ──────────────────
@pytest.mark.asyncio
async def test_duet_prompt_carries_her_recent_memories(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": [
        {"id": "d1", "timestamp": 1.0, "summary_ja": "暗室で褒められた"},
        {"id": "d2", "timestamp": 2.0, "summary_ja": "雨の日に笑った"},
    ]})
    session = _session()
    session["memories"] = await muse_service._recent_memories(FakeDB(), session)
    assert session["memories"] == ["雨の日に笑った", "暗室で褒められた"]   # newest first

    prompt = muse_service._duet_user_prompt(session, "はじめよう", prep=False)
    assert "暗室で褒められた" in prompt
    assert "今日の画に写すものではない" in prompt     # a memory is not a prop

    session["memories"] = []
    assert "日記から" not in muse_service._duet_user_prompt(session, "x", prep=False)

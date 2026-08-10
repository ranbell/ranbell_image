"""Unit tests for Actress Secret Diary and JobSpooler Integration."""
import asyncio
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
from backend.app.muse import diary as muse_diary
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
    """Enough of the db for session_db.save/load and get_runtime_config."""
    def __init__(self, config=None):
        self._config = config or {}
        self._qc = self
        self.rows: dict[str, dict] = {}

    async def get_config(self):
        return dict(self._config)

    async def upsert(self, collection_name, points):
        for p in points:
            self.rows[str(p.id)] = dict(p.payload)

    async def retrieve(self, collection_name, ids, with_payload=True):
        class _Point:
            def __init__(self, payload):
                self.payload = payload
                self.id = payload["session_id"]
        return [_Point(self.rows[i]) for i in ids if i in self.rows]


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
async def test_add_preset_diary_rejects_duplicate_session_and_character(monkeypatch):
    """Defense in depth against the `finish_session` race: two diary jobs for
    the same shoot must never file two entries."""
    mock_db = MagicMock()
    fake_preset_store(monkeypatch, {
        "id": "c001", "slug": "test_actress", "name": "Test Actress", "diaries": [],
    })

    first = {
        "id": "diary-1", "session_id": "s-1", "character_id": "c001",
        "timestamp": time.time(), "content_ja": "最初の日記",
    }
    second = {
        "id": "diary-2", "session_id": "s-1", "character_id": "c001",
        "timestamp": time.time(), "content_ja": "同じ撮影の二通目",
    }
    added_first = await presets_db.add_preset_diary(mock_db, "c001", first)
    added_second = await presets_db.add_preset_diary(mock_db, "c001", second)

    assert added_first["id"] == "diary-1"
    assert added_second["id"] == "diary-1", "the second write must return the existing entry"
    diaries = await presets_db.get_preset_diaries(mock_db, "c001")
    assert len(diaries) == 1


@pytest.mark.asyncio
async def test_find_preset_diary_by_image_reverse_lookup(monkeypatch):
    """The Creation Record panel's link back to her diary: given the shot's
    sha256, find whatever entry she wrote about it."""
    mock_db = MagicMock()
    fake_preset_store(monkeypatch, {
        "id": "c001", "slug": "test_actress", "name": "Test Actress", "diaries": [],
    })
    await presets_db.add_preset_diary(mock_db, "c001", {
        "id": "diary-1", "session_id": "s-1", "character_id": "c001",
        "image_id": "sha-of-the-shot", "timestamp": time.time(),
    })

    found = await presets_db.find_preset_diary_by_image(mock_db, "c001", "sha-of-the-shot")
    assert found is not None
    assert found["id"] == "diary-1"

    assert await presets_db.find_preset_diary_by_image(mock_db, "c001", "sha-of-some-other-shot") is None
    assert await presets_db.find_preset_diary_by_image(mock_db, "c001", "") is None


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
    # Labelled blocks, not JSON: her prose breaks JSON and a broken object used
    # to reach the panel as her diary.
    for label in ("SUMMARY_JA", "SUMMARY_EN", "CONTENT_JA", "CONTENT_EN"):
        assert label in prompt
    assert "JSON にはしない" in prompt
    assert "総監督の言葉が嬉しい" in prompt


def test_actress_diary_prompt_accepts_raw_preset_with_list_personality():
    """finish_session loads the preset row; personality there is a trait list."""
    preset = {
        "id": "c007",
        "name_ja": "各務 みお",
        "summary_ja": "放送部。マイクの前では動じない。",
        "charm_ja": "声で気づかれると口を押さえる。",
        "inner_ja": ["部屋ひとつ分だけ離れていれば勇気が出る"],
        "personality": ["articulate", "shy_offstage", "warm"],
        "appearance": {"voice": "低めで温かい"},
    }
    prompt = muse_crew.actress_diary_prompt(preset, session_log="総監督: いいね", photo_desc="broadcast")
    assert "各務 みお" in prompt
    assert "放送部" in prompt
    assert "口を押さえる" in prompt
    assert "部屋ひとつ分" in prompt



def test_caught_block_is_a_line_she_says_not_a_prompt_of_its_own():
    """Being read is raised in conversation now, so it rides on her turn."""
    block = muse_crew.caught_block("褒められて照れたこと")
    assert "見ちゃいました？" in block
    assert "褒められて照れたこと" in block
    assert "一度だけ" in block
    # Same fence as a memory: a topic, never something the picture must contain.
    assert "今日の画に写すものではない" in block


# ── the parser, which is the whole reason a diary can be trusted on screen ──
def test_parse_diary_reads_labelled_blocks_with_prose_that_breaks_json():
    raw = (
        "SUMMARY_JA: 夜のプールでの撮影\n"
        "SUMMARY_EN: Night pool shoot\n"
        "CONTENT_JA:\n"
        "……やっと一人になれた。「監督、こっち見ないで」なんて言えるわけない。\n"
        "\n"
        "心臓が持たなかった。\n"
        "CONTENT_EN:\n"
        "Finally, I am alone. My heart has not stopped."
    )
    out = muse_diary.normalize(muse_diary.parse_diary(raw))
    assert out["summary_ja"] == "夜のプールでの撮影"
    assert "「監督、こっち見ないで」" in out["content_ja"]
    assert out["content_ja"].endswith("心臓が持たなかった。")
    assert out["content_en"].endswith("has not stopped.")


def test_parse_diary_still_reads_json_including_a_fenced_one():
    raw = (
        "```json\n"
        '{"summary_ja": "褒められた", "summary_en": "Praised",\n'
        ' "content_ja": "日本語の日記です。", '
        '"content_en": "An English diary, long enough to keep."}\n'
        "```"
    )
    out = muse_diary.normalize(muse_diary.parse_diary(raw))
    assert out["content_ja"] == "日本語の日記です。"
    assert out["content_en"] == "An English diary, long enough to keep."


def test_a_cut_off_english_tail_is_repaired_not_shown_mid_word():
    """The reported failure: the generation stopped mid-word and the fragment
    was saved verbatim. Length does not catch it — where the text stops does."""
    raw = (
        '{\n  "summary_ja": "夜のプールでの撮影。",\n'
        '  "summary_en": "Night pool photoshoot. T",\n'
        '  "content_ja": "……やっと一人になれた。今日の撮影、本当に心臓が持たなかった。",\n'
        '  "content_en": "Finally, I am alone. My heart has not stopped racing since the shoot ended. A'
    )
    out = muse_diary.normalize(muse_diary.parse_diary(raw))
    assert "やっと一人になれた" in out["content_ja"]
    # She ends where she last finished a sentence; the dangling letter is gone.
    assert out["content_en"] == (
        "Finally, I am alone. My heart has not stopped racing since the shoot ended."
    )
    assert out["summary_en"] == "Night pool photoshoot."


def test_a_tail_with_nothing_whole_left_falls_back_to_japanese():
    out = muse_diary.normalize({
        "content_ja": "……やっと一人になれた。今日の撮影、本当に心臓が持たなかった。",
        "content_en": "Fin",
    })
    assert out["content_en"] == ""


def test_unescape_reassembles_a_surrogate_pair_emoji():
    """An emoji outside the BMP is JSON-escaped as two \\uXXXX halves. Undoing
    them one at a time used to leave two lone surrogates, which cannot be
    UTF-8 encoded — the "emoji shows as ?" bug."""
    out = muse_diary._unescape(r"嬉しい😊です")
    assert out == "嬉しい😊です"
    out.encode("utf-8")  # must not raise


def test_unescape_drops_a_truncated_lone_surrogate_instead_of_emitting_garbage():
    """A response cut off mid-emoji leaves only the high half — that must be
    dropped, not turned into an unencodable lone surrogate."""
    out = muse_diary._unescape(r"最後に\ud83d")
    assert out == "最後に"
    out.encode("utf-8")


def test_salvage_reassembles_a_surrogate_pair_emoji_from_broken_json():
    """End to end: a truncated, JSON-shaped diary response whose content
    contains an escaped emoji must still come out encodable and readable,
    even on the field-level salvage path the primary JSON parser cannot use."""
    raw = (
        '{"content_ja": "今日はとても嬉しかった\\ud83d\\ude0a、で、まだ続く'
    )
    out = muse_diary.parse_diary(raw)
    assert "😊" in out.get("content_ja", "")
    out["content_ja"].encode("utf-8")


def test_scaffolding_never_becomes_her_writing():
    """The bug this module exists for: the contract printed as her diary."""
    for raw in ('{"summary_ja":', "```json\n{\n", '{"content_ja": ',
                "SUMMARY_JA:", "SUMMARY_JA:\nCONTENT_JA:"):
        out = muse_diary.normalize(muse_diary.parse_diary(raw))
        assert out["content_ja"] == "", raw
        assert not muse_diary.looks_like_json(out["content_ja"])


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
async def test_wrapping_twice_writes_one_diary():
    """The panel's button could be pressed again; each press used to queue a
    whole second diary for the same shoot."""
    db, spooler = FakeDB(), FakeSpooler()
    session = _session()
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    await muse_service.finish_session(db, spooler, session, ollama="OLL")
    assert len(spooler.calls) == 1


@pytest.mark.asyncio
async def test_two_concurrent_requests_racing_finish_session_write_one_diary():
    """A double-click or a second tab loads its own snapshot before either
    write lands — two independent dicts, not the same mutated object like
    above. Only the lock-and-reload inside `finish_session` catches this."""
    db, spooler = FakeDB(), FakeSpooler()
    from backend.app.muse import session_db
    seed = _session()
    await session_db.save(db, seed)

    snap_a = await session_db.load(db, seed["session_id"])
    snap_b = await session_db.load(db, seed["session_id"])
    assert snap_a is not snap_b

    await asyncio.gather(
        muse_service.finish_session(db, spooler, snap_a, ollama="OLL"),
        muse_service.finish_session(db, spooler, snap_b, ollama="OLL"),
    )
    assert len(spooler.calls) == 1


@pytest.mark.asyncio
async def test_wrapping_before_the_shoot_is_refused():
    """She cannot write about a picture that was never taken."""
    db, spooler = FakeDB(), FakeSpooler()
    with pytest.raises(muse_service.MuseError):
        await muse_service.finish_session(
            db, spooler, _session(shoot={"prompt": "1girl", "images": []}), ollama="OLL",
        )
    assert spooler.calls == []


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
        return ("SUMMARY_JA: 褒められた\nSUMMARY_EN: Praised\n"
                "CONTENT_JA:\n日本語の日記。\n"
                "CONTENT_EN:\nEnglish diary, written out properly.")

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
    assert entry["content_ja"] == "日本語の日記。"
    assert entry["content_en"] == "English diary, written out properly."
    assert entry["summary_en"] == "Praised"
    assert entry["image_id"] == "sha-abc"
    assert entry["read"] is False
    # Which shoot it was, so the entry can lead back to it.
    assert entry["session_id"] == "s-1" and entry["character_id"] == "c001"


@pytest.mark.asyncio
async def test_diary_job_keeps_prose_when_she_ignores_the_contract(monkeypatch):
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
async def test_a_broken_object_is_retried_and_never_saved_as_her_diary(monkeypatch):
    """What the Showrunner used to find on the page: the JSON itself."""
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": []})
    calls = []

    async def fake_call(ollama, *, system, prompt, model, images, num_ctx, think):
        calls.append(prompt)
        if len(calls) == 1:
            return '{\n  "summary_ja": "夜のプール",\n  "content_ja": '
        return "SUMMARY_JA: 夜のプール\nCONTENT_JA:\n……やっと一人になれた。"

    monkeypatch.setattr(muse_chain, "_call", fake_call)
    db, spooler = FakeDB(), FakeSpooler()
    await muse_service.finish_session(db, spooler, _session(), ollama="OLL")
    result = await spooler.calls[0]["func"]("R", "C", **spooler.calls[0]["kwargs"])

    assert result["status"] == "ok"
    assert len(calls) == 2                          # one retry, contract restated
    entry = (await presets_db.get_preset_diaries(db, "c001"))[0]
    assert entry["content_ja"] == "……やっと一人になれた。"
    assert not muse_diary.looks_like_json(entry["content_ja"])


@pytest.mark.asyncio
async def test_nothing_is_saved_when_both_attempts_are_unreadable(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": []})
    monkeypatch.setattr(
        muse_chain, "_call", AsyncMock(return_value='{"summary_ja":'),
    )
    db, spooler = FakeDB(), FakeSpooler()
    await muse_service.finish_session(db, spooler, _session(), ollama="OLL")
    result = await spooler.calls[0]["func"]("R", "C", **spooler.calls[0]["kwargs"])

    assert result["status"] == "failed"
    assert await presets_db.get_preset_diaries(db, "c001") == []


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
async def test_turning_a_page_runs_no_model(monkeypatch):
    """Reading used to cost a cold model load, and had her answer a click
    nobody had told her about. The receipt is all this route does now."""
    fake_preset_store(monkeypatch, {"id": "c001", "name_ja": "アリス", "diaries": [
        {"id": "d1", "timestamp": 1.0, "summary_ja": "褒められた", "read": False},
    ]})
    called = AsyncMock(side_effect=AssertionError("no model on the read path"))
    monkeypatch.setattr(muse_chain, "_call", called)
    req = fake_request(FakeDB({"vlm_model": "cfg-model"}), ollama="OLL")

    res = await characters_api.read_character_diary("c001", "d1", req)
    assert res["diary"]["read"] is True
    assert res["banter"] == ""
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleting_a_diary(monkeypatch):
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": [
        {"id": "d1", "timestamp": 1.0}, {"id": "d2", "timestamp": 2.0},
    ]})
    db = FakeDB()
    await characters_api.delete_character_diary("c001", "d1", fake_request(db))
    assert [d["id"] for d in await presets_db.get_preset_diaries(db, "c001")] == ["d2"]


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


@pytest.mark.asyncio
async def test_the_table_gets_her_memory_too_but_only_at_her_seat(monkeypatch):
    """The eighteen-seat table never read her diary at all; putting it in the
    shared brief would hand it to all eighteen."""
    fake_preset_store(monkeypatch, {"id": "c001", "diaries": [
        {"id": "d1", "timestamp": 1.0, "summary_ja": "暗室で褒められた"},
    ]})
    session = _session()
    await muse_service._load_actress_memory(FakeDB(), session)
    assert session["memories"] == ["暗室で褒められた"]

    lead = muse_crew.DEFAULT_MEMBER["actress"]
    hers = muse_service._table_user_prompt(session, muse_id=lead)
    assert "暗室で褒められた" in hers
    other = muse_service._table_user_prompt(session, muse_id="light")
    assert "暗室で褒められた" not in other
    assert "暗室で褒められた" not in str(session.get("brief") or "")


@pytest.mark.asyncio
async def test_casting_a_partner_fills_the_card_on_the_click(monkeypatch):
    """Only the id used to be stored, and the panel reads `partner_character` —
    so picking somebody showed "no partner" until she happened to speak."""
    people = {
        "c001": {"id": "c001", "name_ja": "アリス"},
        "c002": {"id": "c002", "name_ja": "ベル", "name": "Bell"},
    }

    async def fake_get_preset(db, preset_id):
        return people.get(preset_id)

    monkeypatch.setattr(presets_db, "get_preset", fake_get_preset)
    db = FakeDB()
    session = await muse_service.pick_partner(db, _session(), "c002")
    assert session["inputs"]["partner_preset"] == "c002"
    assert session["partner_character"]["name_ja"] == "ベル"

    with pytest.raises(muse_service.MuseError):        # the lead is not her own partner
        await muse_service.pick_partner(db, session, "c001")

    cleared = await muse_service.pick_partner(db, session, "")
    assert cleared["inputs"]["partner_preset"] == ""
    assert await muse_service._partner_character(db, cleared) is None


@pytest.mark.asyncio
async def test_nothing_is_shot_before_there_is_a_prompt():
    """In 主演撮り an OK with no craft fell through to another line of talk, so
    the button looked like it did nothing."""
    db = FakeDB()
    with pytest.raises(muse_service.MuseError):
        await muse_service.approve_and_shoot(db, None, FakeSpooler(), _session(craft={}))
    with pytest.raises(muse_service.MuseError):
        await muse_service.request_board(db, None, FakeSpooler(), _session(craft={}))


# ── being caught: said once, in conversation, at the next session ───────────
@pytest.mark.asyncio
async def test_she_brings_up_a_read_diary_once_and_then_never_again(monkeypatch):
    preset = fake_preset_store(monkeypatch, {"id": "c001", "diaries": [
        {"id": "d1", "timestamp": 1.0, "summary_ja": "褒められた", "read": True},
        {"id": "d2", "timestamp": 2.0, "summary_ja": "雨の日", "read": True},
        {"id": "d3", "timestamp": 3.0, "summary_ja": "まだ見てない", "read": False},
    ]})
    db = FakeDB()
    session = _session()
    await muse_service._load_actress_memory(db, session)
    assert session["caught"]["ids"] == ["d2", "d1"]        # not the unread one
    assert "見ちゃいました？" in muse_service._duet_user_prompt(session, "", prep=False)

    await muse_service._consume_caught(db, session)
    assert session["caught"] == {}
    assert "見ちゃいました？" not in muse_service._duet_user_prompt(session, "", prep=False)
    fired = {d["id"] for d in preset["diaries"] if d.get("secret_banter_fired")}
    assert fired == {"d1", "d2"}

    # Next session: nothing owed until they read something new.
    await muse_service._load_actress_memory(db, session)
    assert session["caught"] == {}

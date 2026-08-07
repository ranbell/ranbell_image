"""Unit tests for Actress Secret Diary and JobSpooler Integration."""
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import presets as presets_db
from backend.app.muse import service as muse_service
from backend.app.muse import crew as muse_crew


@pytest.mark.asyncio
async def test_preset_diaries_crud():
    """Test reading, writing, marking read, and retrieving diary summaries."""
    mock_db = MagicMock()
    
    # Mock preset object
    fake_preset = {
        "id": "c001",
        "slug": "test_actress",
        "name": "Test Actress",
        "diaries": []
    }
    
    async def fake_get_preset(db, preset_id):
        return fake_preset if preset_id == "c001" else None
        
    async def fake_update_preset(db, preset_id, patch):
        fake_preset.update(patch)
        return fake_preset

    # Patch functions on presets_db for testing
    presets_db.get_preset = fake_get_preset
    presets_db.update_preset = fake_update_preset

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

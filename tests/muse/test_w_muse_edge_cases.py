import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from app.muse import crew
from tests.muse.test_service import FakeDb


@pytest.fixture
def fake_db():
    return FakeDb()


@pytest.fixture
def sample_session():
    return {
        "session_id": "test_sid",
        "mode": "duet",
        "status": "setup",
        "inputs": {"character_id": "c001", "partner_preset": ""},
        "character": {
            "name": "Minamo Shirase",
            "name_ja": "白瀬 みなも",
            "identity_tags": ["1girl", "black_hair", "short_hair", "solo"],
        },
        "chat": [],
    }


def test_edge_same_name_custom_character_prevention():
    """Case 3: If both Muse A & Muse B have identical names, W-Muse prompt differentiates them safely."""
    char_a = {"name_ja": "みなも", "personality": {"preset_name_ja": "みなも", "first_person_ja": "私"}}
    char_b = {"name_ja": "みなも", "personality": {"preset_name_ja": "みなも", "first_person_ja": "ボク"}}

    prompt = crew.w_actress_duet_prompt(char_a, char_b, mode="talk")
    assert "You are directing a W-MUSE" in prompt
    assert "みなも" in prompt



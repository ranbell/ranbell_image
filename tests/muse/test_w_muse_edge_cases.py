import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from app.muse import service, crew
from app.characters import presets
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


def test_edge_invalid_partner_preset_fallback(sample_session):
    """Case 1: An invalid partner_preset ('c999') must fail-safe to None without raising exceptions."""
    sample_session["inputs"]["partner_preset"] = "c999_invalid_id"
    
    tags = service._identity_tags(sample_session)
    assert "2girls" not in tags  # Invalid partner means fallback to solo
    assert sample_session.get("partner_character") is None


def test_edge_w_muse_to_solo_mode_switch(sample_session):
    """Case 2: Switching from W-Muse partner to solo (empty partner_preset) clears partner_character & 2girls tag."""
    p002 = {
        "id": "c002",
        "name": "Kaho Yanagi",
        "name_ja": "柳 かほ",
        "subject_tag": "1girl",
        "identity_tags": ["1girl", "green_hair", "glasses"],
    }
    sample_session["partner_character"] = presets.preset_to_character(p002)
    sample_session["inputs"]["partner_preset"] = "c002"

    tags_before = service._identity_tags(sample_session)
    assert "2girls" in tags_before
    assert sample_session.get("partner_character") is not None

    # Now clear partner_preset
    sample_session["inputs"]["partner_preset"] = ""
    partner_preset_id = str(sample_session["inputs"].get("partner_preset") or "").strip()
    if not partner_preset_id:
        sample_session.pop("partner_character", None)

    tags_after = service._identity_tags(sample_session)
    assert "2girls" not in tags_after
    assert sample_session.get("partner_character") is None


def test_edge_same_name_custom_character_prevention():
    """Case 3: If both Muse A & Muse B have identical names, W-Muse prompt differentiates them safely."""
    char_a = {"name_ja": "みなも", "personality": {"preset_name_ja": "みなも", "first_person_ja": "私"}}
    char_b = {"name_ja": "みなも", "personality": {"preset_name_ja": "みなも", "first_person_ja": "ボク"}}

    prompt = crew.w_actress_duet_prompt(char_a, char_b, mode="talk")
    assert "You are directing a W-MUSE" in prompt
    assert "みなも" in prompt


def test_edge_w_muse_identity_tag_merging_no_conflict():
    """Case 4: Merging Muse A (1girl, black_hair) and Muse B (1girl, blond_hair) strips '1girl' and injects '2girls'."""
    char_a = {"identity_tags": ["1girl", "black_hair", "short_hair", "solo"]}
    char_b = {"identity_tags": ["1girl", "blond_hair", "long_hair"]}
    session = {
        "character": char_a,
        "partner_character": char_b,
    }

    tags = service._identity_tags(session)
    assert tags[0] == "2girls"
    assert "1girl" not in tags
    assert "solo" not in tags
    assert "black_hair" in tags
    assert "blond_hair" in tags


def test_edge_w_muse_framing_closeup_guard(sample_session):
    """Case 5: W-Muse cast array must contain both characters for downstream 2girls composition."""
    p002 = {
        "id": "c002",
        "name": "Kaho Yanagi",
        "name_ja": "柳 かほ",
        "subject_tag": "1girl",
        "identity_tags": ["1girl", "green_hair"],
    }
    char_b = presets.preset_to_character(p002)
    char_b["name"] = "Kaho Yanagi"
    sample_session["partner_character"] = char_b

    cast = service._cast(sample_session)
    assert len(cast) == 2
    assert cast[0]["name"] == sample_session["character"]["name"]
    assert cast[1]["name"] == "Kaho Yanagi"

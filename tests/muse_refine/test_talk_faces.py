"""SAY / ASIDE rows carry speaker_id so the panel can show face thumbs."""
from app.muse_refine import talk


def test_solo_say_includes_speaker_id():
    session = {
        "session_id": "s1",
        "character": {
            "character_id": "lead-1",
            "name": "Aoi",
            "name_ja": "葵",
            "board": {"portrait": "abc"},
        },
        "partner_character": {},
        "chat": [],
    }
    talk.publish_actress_turn(
        session,
        {"say": "こんにちは。", "aside": "（ドキドキ）", "pitch": ""},
        locale="ja",
        lead_name="葵",
    )
    says = [r for r in session["chat"] if (r.get("meta") or {}).get("kind") == "say"]
    asides = [r for r in session["chat"] if (r.get("meta") or {}).get("kind") == "banter"]
    assert says and says[0]["meta"].get("speaker_id") == "lead-1"
    assert asides and asides[0]["meta"].get("speaker_id") == "lead-1"


def test_duet_say_speaker_ids():
    session = {
        "session_id": "s2",
        "character": {
            "character_id": "lead-1",
            "name": "Aoi",
            "name_ja": "葵",
        },
        "partner_character": {
            "character_id": "partner-2",
            "name": "Rin",
            "name_ja": "凛",
        },
        "chat": [],
    }
    talk.publish_actress_turn(
        session,
        {"say": "A: おはよう。\nB: うん、おはよう。", "aside": "", "pitch": ""},
        locale="ja",
        lead_name="葵",
    )
    says = [r for r in session["chat"] if (r.get("meta") or {}).get("kind") == "say"]
    ids = {r["meta"].get("speaker_id") for r in says}
    assert "lead-1" in ids
    assert "partner-2" in ids

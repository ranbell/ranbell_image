"""Persona / board-gate helpers (no live LLM)."""
from __future__ import annotations

import pytest

from app.muse import ledger, persona, service


def test_mark_reunion_from_bond():
    session = {"bond": {"last": "昨日の屋上"}, "memories": []}
    persona.mark_reunion(session)
    assert session["reunion_turn"] is True
    persona.clear_reunion(session)
    assert session["reunion_turn"] is False


def test_vitality_extras_standing():
    session = {
        "standing": ["always soft smile"],
        "reunion_turn": False,
        "prop_age": {"fp": "", "turns": 0},
    }
    led = ledger.blank()
    text = persona.vitality_extras(session, led)
    assert "STANDING" in text
    assert "soft smile" in text


def test_shoot_requires_board():
    session = service.new_session({"locale": "ja", "workflow": "wf"})
    session["craft"] = {"prompt": "1girl, hoodie"}
    session["board"] = {"images": [], "pending": False}

    class Req:
        class state:
            ollama = None
            spooler = None
            comfy = None

    with pytest.raises(service.RefineError) as ei:
        import asyncio
        asyncio.run(service.start_shoot(None, Req(), session))
    assert "試し撮り" in ei.value.message


def test_public_view_exposes_board_shoot_job_ids():
    session = service.new_session()
    session["board"] = {
        "images": [{"image_id": "a"}],
        "pending": False,
        "job_id": "job-board-1",
        "error": "",
        "status": "ready",
    }
    session["shoot"] = {
        "images": [],
        "pending": True,
        "job_id": "job-shoot-2",
        "error": "",
        "status": "queued",
    }
    view = service.public_view(session)
    assert view["board"]["job_id"] == "job-board-1"
    assert view["board"]["ready"] is True
    assert view["shoot"]["job_id"] == "job-shoot-2"
    assert view["shoot"]["pending"] is True


def test_public_view_exposes_partner_and_standing():
    session = service.new_session()
    session["partner_character"] = {
        "character_id": "p1", "name_ja": "相方", "board": {},
    }
    session["standing"] = ["keep soft light"]
    session["last_pitch"] = ["寄る", "引く"]
    session["bond"] = {"last": "屋上の風", "inside": ""}
    view = service.public_view(session)
    assert view["partner_character"]["name"] == "相方"
    assert view["standing"] == ["keep soft light"]
    assert view["last_pitch"] == ["寄る", "引く"]
    assert "屋上" in view["bond"]["last"]

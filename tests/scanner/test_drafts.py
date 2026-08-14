"""Draft vs finished Muse paths under generated_images_dir."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.config import settings
from app.scanner.drafts import is_draft_path


def test_playground_boards_and_stills_are_drafts(tmp_path, monkeypatch):
    gen = tmp_path / "generated"
    monkeypatch.setattr(settings, "generated_images_dir", str(gen))
    playground = gen / "playground"
    playground.mkdir(parents=True)
    board = playground / "muse_board_1.png"
    still = playground / "muse_still_1.png"
    board.touch()
    still.touch()
    assert is_draft_path(board) is True
    assert is_draft_path(still) is True


def test_muse_shoot_and_legacy_playground_shoot_are_not_drafts(tmp_path, monkeypatch):
    gen = tmp_path / "generated"
    monkeypatch.setattr(settings, "generated_images_dir", str(gen))
    shoot_dir = gen / "muse"
    shoot_dir.mkdir(parents=True)
    shoot = shoot_dir / "muse_shoot_1.png"
    shoot.touch()
    assert is_draft_path(shoot) is False

    playground = gen / "playground"
    playground.mkdir(parents=True, exist_ok=True)
    legacy = playground / "muse_shoot_old.png"
    legacy.touch()
    assert is_draft_path(legacy) is False

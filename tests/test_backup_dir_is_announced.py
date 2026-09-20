"""**A backup that is not happening has to say so.** (2026-09-21)

The daily backup has two layers — a Qdrant snapshot per collection, and the
lineage ledger the backend writes under `backup_dir`. Neither directory is
mounted by default, and an upgrade pulls new images while leaving the old
`docker-compose.yml` in place, so an instance ends up running new code with
yesterday's volumes. A backup that cannot be written looks exactly like one that
can, which is the worst kind, so the app announces it across the top of the
screen and names the fix: take the current compose file.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

LOCALES = ROOT / "frontend/src/locales"


def _check():
    # Imported inside the tests: `app.main` pulls in the whole application, and
    # the stubs some test packages install would answer instead.
    from app.main import _check_backup_dir

    return _check_backup_dir


def _run(cfg, monkeypatch) -> list:
    async def _cfg(db):
        return cfg

    monkeypatch.setattr("app.runtime_config.get_runtime_config", _cfg)
    warnings: list = []
    asyncio.run(_check()(object(), warnings))
    return warnings


def test_a_directory_that_is_not_there_is_announced(monkeypatch, tmp_path):
    gone = tmp_path / "not-mounted"

    warnings = _run({"backup_dir": str(gone)}, monkeypatch)

    assert len(warnings) == 1
    assert warnings[0]["key"] == "backupDirMissing"
    assert warnings[0]["params"]["path"] == str(gone)
    assert "docker-compose" in warnings[0]["text"], "直し方が書かれていない"


def test_a_directory_inside_the_container_is_announced(monkeypatch, tmp_path):
    """It exists and is writable, but it is not a mount — it dies with the container."""
    warnings = _run({"backup_dir": str(tmp_path)}, monkeypatch)

    assert len(warnings) == 1
    assert warnings[0]["key"] == "backupDirEphemeral"


def test_one_that_cannot_be_written_is_announced(monkeypatch, tmp_path):
    import os

    root = tmp_path / "ro"
    root.mkdir()
    os.chmod(root, 0o500)
    try:
        warnings = _run({"backup_dir": str(root)}, monkeypatch)
    finally:
        os.chmod(root, 0o700)

    assert len(warnings) == 1
    assert warnings[0]["key"] == "backupDirUnwritable"


def test_nothing_is_said_when_backups_are_switched_off(monkeypatch, tmp_path):
    warnings = _run({"backup_dir": str(tmp_path / "gone"), "backup_enabled": False}, monkeypatch)

    assert warnings == []


def test_a_database_that_will_not_answer_does_not_stop_the_boot(monkeypatch):
    async def _boom(db):
        raise RuntimeError("no config")

    monkeypatch.setattr("app.runtime_config.get_runtime_config", _boom)
    warnings: list = []
    asyncio.run(_check()(object(), warnings))

    # Falls back to the default path, which is not mounted in a test run.
    assert len(warnings) == 1
    assert warnings[0]["params"]["path"] == "/mnt/backup"


@pytest.mark.parametrize("key", ["backupDirMissing", "backupDirUnwritable", "backupDirEphemeral"])
def test_every_announcement_has_both_locales(key):
    for name in ("ja.json", "en.json"):
        table = json.loads((LOCALES / name).read_text(encoding="utf-8"))["startupWarning"]
        assert key in table, f"{key} が {name} に無い"
        assert "{path}" in table[key], f"{key}（{name}）が場所を出していない"
        assert "docker-compose" in table[key], f"{key}（{name}）が直し方を言っていない"

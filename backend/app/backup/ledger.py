"""Append-only ledger of the payload that cannot be rebuilt from the files.

Almost everything in the images collection is derived: paths, prompts, tags,
embeddings and colour all come back from a heal scan and the AI pipeline. Three
fields do not. ``genesis`` and ``creation_record`` record how an image was made,
and ``star_rating`` is what someone thought of it. Nothing on disk remembers
those.

The ledger is append-only, and that is the whole point rather than an
implementation detail. A heal scan deletes points whose files are missing, so
remounting the image directory at a different path and scanning will empty out
large parts of the collection quite legitimately. A backup that mirrors the
current state would faithfully copy that loss. An append-only ledger keeps
what it has already seen, so the lineage survives the scan that dropped it.

Each run writes only what changed since the last one, gzipped, one JSON object
per line. Restores read every file oldest-first and never overwrite a value
that is already in the database.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# The fields no scan can bring back.
LINEAGE_FIELDS = ("genesis", "creation_record", "star_rating")

# Collections small enough, and precious enough, to keep whole.
WHOLE_COLLECTIONS = (
    "character_presets",
    "muse_sessions",
    "muse_memories",
    "muse_lounge",
    "muse_handpost",
    "alignment",
)

MANIFEST_NAME = "manifest.json"


def _digest(value: Any) -> str:
    return hashlib.sha1(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()[:16]


class Ledger:
    """Reads and writes the gzipped JSONL ledger under ``root``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def dir(self) -> Path:
        return self.root / "lineage"

    def _manifest_path(self) -> Path:
        return self.dir / MANIFEST_NAME

    def load_manifest(self) -> dict[str, str]:
        try:
            return json.loads(self._manifest_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            # A corrupt manifest costs a full re-append, not data. Carry on.
            logger.warning("ledger manifest unreadable; treating everything as new",
                           exc_info=True)
            return {}

    def save_manifest(self, manifest: dict[str, str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._manifest_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._manifest_path())

    def files(self) -> list[Path]:
        """Ledger files, oldest first."""
        if not self.dir.is_dir():
            return []
        return sorted(self.dir.glob("*.jsonl.gz"))

    def writable(self) -> tuple[bool, str]:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True, ""
        except Exception as e:
            return False, str(e)

    def append(self, records: Iterable[dict], *, day: str | None = None) -> int:
        """Append records to today's file. Returns how many were written."""
        day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{day}.jsonl.gz"
        written = 0
        # Append mode on a gzip file produces a multi-member stream, which
        # gzip.open reads back as one continuous file. That keeps every run of
        # the day additive without rewriting what is already there.
        with gzip.open(path, "at", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                written += 1
        return written

    def read_all(self) -> dict[tuple[str, str], dict]:
        """Fold every ledger file into the latest record per (kind, id).

        Oldest first, so a later run's value wins — but only against another
        ledger entry. Nothing here decides anything about the live database.
        """
        out: dict[tuple[str, str], dict] = {}
        for path in self.files():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            # One torn line at the end of an interrupted write
                            # must not cost the rest of the history.
                            logger.warning("skipping unreadable line in %s", path.name)
                            continue
                        key = (rec.get("kind") or "", str(rec.get("id") or ""))
                        if key[1]:
                            out[key] = rec
            except OSError:
                logger.warning("could not read ledger file %s", path, exc_info=True)
        return out

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.files() if p.exists())

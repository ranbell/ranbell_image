"""Which generated subfolders hold drafts rather than finished work.

Draft images are still registered in Qdrant — thumbnails, sha lookup and search
all keep working for free — but they carry ``is_draft`` so the gallery can leave
them out by default. A Muse run produces six 512px board sketches per theme;
without this they would swamp every browse and skew the tag statistics toward
whatever the throwaways happened to contain.

Kept in its own module because both the writer (``scanner.save``) and the
reader (``scanner.scanner``) need it, and they already import each other.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings

PLAYGROUND_SUBDIR = "playground"
DRAFT_SUBDIRS: tuple[str, ...] = (PLAYGROUND_SUBDIR,)


def is_draft_path(path: Path) -> bool:
    """True when this file lives under a draft subfolder of the generated dir."""
    try:
        rel = Path(path).resolve().relative_to(Path(settings.generated_images_dir).resolve())
    except (ValueError, OSError):
        return False
    return bool(rel.parts) and rel.parts[0] in DRAFT_SUBDIRS

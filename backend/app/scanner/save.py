"""One place to land a generated image on disk and into the library.

There used to be three near-identical copies of this, one per feature, each
with its own subfolder and filename convention and its own slightly different
error handling — and every new feature grew a fourth. This is the one they all
call now.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from pathlib import Path

from ..config import settings
from .scanner import register_image

logger = logging.getLogger(__name__)


async def save_generated_image(
    img_bytes: bytes,
    original_name: str,
    db,
    *,
    subdir: str = "",
    prefix: str = "gen",
) -> str | None:
    """Write bytes under ``generated_images_dir/<subdir>`` and register them.

    Returns the sha256, or None when registration failed (the file is still on
    disk — a later scan heal will pick it up).
    """
    sha256 = hashlib.sha256(img_bytes).hexdigest()
    gen_dir = Path(settings.generated_images_dir)
    if subdir:
        gen_dir = gen_dir / subdir
    gen_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_name).suffix or ".png"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = gen_dir / f"{prefix}_{ts}_{sha256[:8]}{suffix}"

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_bytes, img_bytes)

    try:
        await register_image(path, db)
        return sha256
    except Exception as exc:
        logger.error("[save] register_image failed for %s: %s", path.name, exc)
        return None

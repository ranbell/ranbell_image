import asyncio
import io
import os
from pathlib import Path
from PIL import Image
from ..config import settings


def get_thumbnail_path(sha256: str) -> Path:
    return settings.thumbnails_dir / sha256[:2] / sha256[2:4] / f"{sha256}.webp"


def thumbnail_exists(sha256: str) -> bool:
    return get_thumbnail_path(sha256).exists()


def _generate_sync(image_path: Path, sha256: str, data: bytes | None = None) -> Path:
    out = get_thumbnail_path(sha256)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(data) if data is not None else image_path) as img:
        img.thumbnail((settings.thumbnail_size, settings.thumbnail_size), Image.LANCZOS)
        mode = "RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB"
        # Write beside it and rename: the thumbnail route may be serving this
        # same path while a scan writes it, and must never send half a file.
        tmp = out.with_name(f"{out.name}.{os.getpid()}.{id(img)}.tmp")
        img.convert(mode).save(tmp, "WEBP", quality=85, method=4)
    os.replace(tmp, out)
    return out


def generate_thumbnail_sync(image_path: Path, sha256: str, data: bytes | None = None) -> Path:
    """Make the thumbnail unless it is already there. Blocking — run it in an executor."""
    out = get_thumbnail_path(sha256)
    if out.exists():
        return out
    return _generate_sync(image_path, sha256, data)


async def ensure_thumbnail(image_path: Path, sha256: str, data: bytes | None = None) -> Path:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, generate_thumbnail_sync, image_path, sha256, data)

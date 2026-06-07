import asyncio
from pathlib import Path
from PIL import Image
from ..config import settings


def get_thumbnail_path(sha256: str) -> Path:
    return settings.thumbnails_dir / sha256[:2] / sha256[2:4] / f"{sha256}.webp"


def thumbnail_exists(sha256: str) -> bool:
    return get_thumbnail_path(sha256).exists()


def _generate_sync(image_path: Path, sha256: str) -> Path:
    out = get_thumbnail_path(sha256)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as img:
        img.thumbnail((settings.thumbnail_size, settings.thumbnail_size), Image.LANCZOS)
        mode = "RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB"
        img.convert(mode).save(out, "WEBP", quality=85, method=4)
    return out


async def ensure_thumbnail(image_path: Path, sha256: str) -> Path:
    out = get_thumbnail_path(sha256)
    if out.exists():
        return out
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _generate_sync, image_path, sha256)

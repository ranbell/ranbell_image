import asyncio
import os

from fastapi import APIRouter, Request
from ..config import settings
from ..scanner.scanner import SCAN_EXTENSIONS

router = APIRouter(prefix="/api")


def _source_subdirs() -> list[str]:
    src = settings.source_images_dir
    if not src.exists():
        return []
    with os.scandir(src) as it:
        return sorted(e.path for e in it if e.is_dir(follow_symlinks=False))


def _count_under(dirs: dict[str, dict], root: str) -> int:
    prefix = root.rstrip("/") + "/"
    return sum(int(e.get("n_images") or 0) for p, e in dirs.items() if p == root or p.startswith(prefix))


@router.get("/info")
async def get_info(request: Request):
    """Mounts and file counts — from what the last scan recorded, not by walking
    the trees again (that took the whole server with it on a large library)."""
    db = request.app.state.db
    thumbnails_dir = settings.thumbnails_dir
    roots = [str(settings.source_images_dir), str(settings.generated_images_dir)]

    try:
        db_count = await db.total_count()
        dirs, _ = await db.load_scan_dirs()
        db_ok = True
    except Exception:
        db_count = 0
        dirs = {}
        db_ok = False

    file_count = sum(_count_under(dirs, r) for r in roots)
    sample_files: list[str] = []
    if db_ok:
        try:
            docs, _ = await db.scroll_images(limit=5, exclude_drafts=False)
            for d in docs:
                p = d.get("path") or ""
                root = next((r for r in roots if p.startswith(r.rstrip("/") + "/")), None)
                if root:
                    sample_files.append(os.path.relpath(p, root))
        except Exception:
            pass

    src = settings.source_images_dir
    src_exists = src.exists()
    subdirs = await asyncio.get_running_loop().run_in_executor(None, _source_subdirs)
    source_mounts = [
        {"name": os.path.basename(d), "path": d, "file_count": _count_under(dirs, d)}
        for d in subdirs
    ]

    return {
        "source_images_dir": str(src),
        "source_images_dir_exists": src_exists,
        "source_mounts": source_mounts,
        "generated_images_dir": str(settings.generated_images_dir),
        "generated_images_dir_exists": settings.generated_images_dir.exists(),
        "image_files_found": file_count,
        "sample_files": sample_files,
        "scan_extensions": list(SCAN_EXTENSIONS),
        "thumbnails_dir": str(thumbnails_dir),
        "thumbnails_dir_exists": thumbnails_dir.exists(),
        "qdrant_ok": db_ok,
        "qdrant_doc_count": db_count,
    }

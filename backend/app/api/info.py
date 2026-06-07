from fastapi import APIRouter, Request
from ..config import settings
from ..scanner.scanner import SCAN_EXTENSIONS

router = APIRouter(prefix="/api")


@router.get("/info")
async def get_info(request: Request):
    db = request.app.state.db
    thumbnails_dir = settings.thumbnails_dir

    file_count = 0
    sample_files: list[str] = []
    for root in [settings.source_images_dir, settings.generated_images_dir]:
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in SCAN_EXTENSIONS:
                    file_count += 1
                    if len(sample_files) < 5:
                        sample_files.append(str(p.relative_to(root)))

    try:
        db_count = await db.total_count()
        db_ok = True
    except Exception:
        db_count = 0
        db_ok = False

    src = settings.source_images_dir
    src_exists = src.exists()
    source_mounts = []
    if src_exists:
        for d in sorted(src.iterdir()):
            if d.is_dir():
                count = sum(1 for p in d.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXTENSIONS)
                source_mounts.append({"name": d.name, "path": str(d), "file_count": count})

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

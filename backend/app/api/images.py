import asyncio
import colorsys
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)
from PIL import Image as _PILImage
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from ..ai.color_extractor import rgb_to_lab
from ..config import settings
from ..db.qdrant_client import QdrantDBClient
from ..thumbnails.generator import get_thumbnail_path
from .sort_utils import sort_docs
from .tag_categories import guess_category

router = APIRouter(prefix="/api")

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_tags_cache: dict = {"data": None, "ts": 0.0}
_TAGS_TTL = 60.0

_dirs_cache: dict = {"data": None, "ts": 0.0}
_DIRS_TTL = 60.0

_name_cache: dict = {"name_asc": None, "name_desc": None, "ts": 0.0}
_NAME_CACHE_TTL = 300.0  # 5 minutes

_align_sort_cache: dict = {"data": None, "ts": 0.0}
_ALIGN_SORT_TTL = 120.0  # 2 minutes

_facets_cache: dict = {"data": None, "ts": 0.0}
_FACETS_TTL = 120.0


async def _get_name_sorted(db, sort: str) -> list[str]:
    """Return sha256 list sorted by name (cached, built from minimal payload scroll)."""
    now = time.time()
    if _name_cache["name_asc"] is not None and now - _name_cache["ts"] < _NAME_CACHE_TTL:
        return _name_cache[sort]
    pairs = await db.scroll_name_index()
    pairs.sort(key=lambda x: x[0])
    asc = [sha for _, sha in pairs]
    _name_cache["name_asc"] = asc
    _name_cache["name_desc"] = list(reversed(asc))
    _name_cache["ts"] = now
    return _name_cache[sort]


async def _get_align_sorted(db) -> list[str]:
    """Return sha256 list sorted by alignment score DESC (cached)."""
    now = time.time()
    if _align_sort_cache["data"] is not None and now - _align_sort_cache["ts"] < _ALIGN_SORT_TTL:
        return _align_sort_cache["data"]
    shas = await db.get_alignment_sorted_sha256s()
    _align_sort_cache["data"] = shas
    _align_sort_cache["ts"] = now
    return shas


@router.get("/dirs")
async def list_dirs_route(request: Request):
    now = time.time()
    if _dirs_cache["data"] is not None and now - _dirs_cache["ts"] < _DIRS_TTL:
        return {"dirs": _dirs_cache["data"]}
    dirs = await _db(request).list_dirs(
        [str(settings.source_images_dir), str(settings.generated_images_dir)]
    )
    _dirs_cache["data"] = dirs
    _dirs_cache["ts"] = now
    return {"dirs": dirs}


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def _db(request: Request) -> QdrantDBClient:
    return request.app.state.db


def _hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    return rgb_to_lab(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _opposite_hue_ranges(hue_deg: float, arc: float = 60.0) -> list[tuple[float, float]]:
    """Return (lo, hi) range(s) centered ±arc/2 around the opposite hue, handling wrap."""
    opp = (hue_deg + 180.0) % 360.0
    lo, hi = opp - arc / 2.0, opp + arc / 2.0
    if lo < 0:
        return [(lo + 360.0, 360.0), (0.0, hi)]
    if hi > 360:
        return [(lo, 360.0), (0.0, hi - 360.0)]
    return [(lo, hi)]


@router.get("/images/facets")
async def get_image_facets(request: Request):
    """Return unique model names with image counts for use as a filter facet."""
    now = time.monotonic()
    if _facets_cache["data"] is not None and now - _facets_cache["ts"] < _FACETS_TTL:
        return _facets_cache["data"]
    db = _db(request)
    facets = await db.scroll_model_facets()
    result = {"models": facets}
    _facets_cache["data"] = result
    _facets_cache["ts"] = now
    return result


@router.get("/images")
async def list_images(
    request: Request,
    limit: int = 100,
    cursor: str = "",        # opaque cursor from previous response
    q: str = "",
    tags_include: str = "",
    tag_logic: str = "and",  # "and" | "or"
    sort: str = "newest",
    dir: str = "",           # folder view: relative path from IMAGES_DIR
    models: str = "",        # comma-separated model names, OR logic
    star_min: int | None = None,   # minimum star rating filter (1-5)
    category: str | None = None,   # "AI" | "NR" — batch_category filter
    align_min: float | None = None, # 0.0-1.0 — minimum alignment score filter
):
    import base64 as _b64

    db = _db(request)

    # Normalize new params
    category = category if category in ("AI", "NR") else None
    if align_min is not None:
        align_min = max(0.0, min(1.0, float(align_min)))

    # Pre-fetch alignment sha256s if align_min filter is requested
    align_sha256s: set[str] | None = None
    if align_min is not None:
        align_sha256s = await db.get_aligned_sha256s(align_min)

    # ── dir filter (folder view) ──────────────────────────────────────────────
    if dir:
        import os
        base = os.path.commonpath(
            [str(settings.source_images_dir), str(settings.generated_images_dir)]
        )
        def _in_dir(doc: dict) -> bool:
            p = doc.get("path", "")
            if not p:
                return False
            try:
                rel = os.path.relpath(os.path.dirname(p), base)
                return (rel if rel != "." else "") == dir
            except ValueError:
                return False
        dir_model_list = [m.strip() for m in models.split(",") if m.strip()] if models else []
        all_docs = await db.scroll_all(
            models=dir_model_list or None, star_min=star_min,
            category=category, sha256_ids=align_sha256s,
        )
        docs = sort_docs([d for d in all_docs if _in_dir(d)], sort)
        return {"total": len(docs), "next_cursor": None, "images": docs,
                "search_mode": True, "sort": sort}

    inc_list = [t.strip() for t in tags_include.split(",") if t.strip()] if tags_include else []
    keyword = q.strip() or None
    model_list = [m.strip() for m in models.split(",") if m.strip()] if models else []

    is_filter = bool(keyword or inc_list or model_list or star_min is not None
                     or category is not None or align_min is not None)

    # align_desc sort: pre-fetch alignment-ordered sha256 list, paginate with integer cursor
    if sort == "align_desc":
        sorted_shas = await _get_align_sorted(db)
        if is_filter:
            docs = await db.scroll_all(
                tags_include=inc_list or None,
                tag_logic=tag_logic,
                models=model_list or None,
                keyword=keyword,
                star_min=star_min,
                category=category,
                sha256_ids=align_sha256s,
            )
            sha_to_doc = {d["sha256"]: d for d in docs}
            # Order by alignment score, then append unscored docs at the end
            ordered = [sha_to_doc[s] for s in sorted_shas if s in sha_to_doc]
            scored_set = set(sorted_shas)
            ordered.extend(d for d in docs if d["sha256"] not in scored_set)
        else:
            # No filter: use ordered sha256 list + batch retrieve
            total = len(sorted_shas)
            offset_idx = 0
            if cursor:
                try:
                    offset_idx = int(_b64.b64decode(cursor.encode()).decode())
                except Exception:
                    offset_idx = 0
            page_shas = sorted_shas[offset_idx:offset_idx + limit]
            docs = await db.get_by_sha256s(page_shas)
            has_more = offset_idx + limit < total
            next_cur = _b64.b64encode(str(offset_idx + limit).encode()).decode() if has_more else None
            return {"total": total, "next_cursor": next_cur, "images": docs,
                    "search_mode": False, "sort": sort}

        total = len(ordered)
        offset_idx = 0
        if cursor:
            try:
                offset_idx = int(_b64.b64decode(cursor.encode()).decode())
            except Exception:
                offset_idx = 0
        page = ordered[offset_idx:offset_idx + limit]
        has_more = offset_idx + limit < total
        next_cur = _b64.b64encode(str(offset_idx + limit).encode()).decode() if has_more else None
        return {"total": total, "next_cursor": next_cur, "images": page,
                "search_mode": True, "sort": sort}

    # Name sorts: Qdrant OrderBy doesn't support Keyword fields.
    # Use cached name index (minimal payload) + offset cursor + batch retrieve.
    if sort in {"name_asc", "name_desc"} and not is_filter:
        sorted_shas = await _get_name_sorted(db, sort)
        total = len(sorted_shas)
        offset_idx = 0
        if cursor:
            try:
                offset_idx = int(_b64.b64decode(cursor.encode()).decode())
            except Exception:
                offset_idx = 0
        page_shas = sorted_shas[offset_idx:offset_idx + limit]
        docs = await db.get_by_sha256s(page_shas)
        has_more = offset_idx + limit < total
        next_cur = _b64.b64encode(str(offset_idx + limit).encode()).decode() if has_more else None
        return {"total": total, "next_cursor": next_cur, "images": docs,
                "search_mode": False, "sort": sort}

    if is_filter:
        available_tags: list[str] = []

        if inc_list and model_list:
            # 2-phase: parallel Qdrant calls to get model-only set (for tag universe) + full filtered set
            model_only_task = db.scroll_all(
                models=model_list, keyword=keyword, star_min=star_min,
                category=category, sha256_ids=align_sha256s,
            )
            full_task = db.scroll_all(
                tags_include=inc_list, tag_logic=tag_logic,
                models=model_list, keyword=keyword, star_min=star_min,
                category=category, sha256_ids=align_sha256s,
            )
            model_docs, docs = await asyncio.gather(model_only_task, full_task)
            tag_universe: set[str] = set()
            for d in model_docs:
                tag_universe.update(d.get("wd14_tags") or [])
            available_tags = sorted(tag_universe)
        else:
            docs = await db.scroll_all(
                tags_include=inc_list or None,
                tag_logic=tag_logic,
                models=model_list or None,
                keyword=keyword,
                star_min=star_min,
                category=category,
                sha256_ids=align_sha256s,
            )

        docs = sort_docs(docs, sort)

        active_filters = {
            "tags_include": inc_list,
            "tag_logic": tag_logic,
            "keyword": keyword or "",
            "models": model_list,
            "sort": sort,
        }

        return {
            "total": len(docs),
            "next_cursor": None,
            "images": docs,
            "search_mode": True,
            "sort": sort,
            "active_filters": active_filters,
            "available_tags": available_tags,
        }
    else:
        docs, next_cursor = await db.scroll_images(
            cursor=cursor or None,
            limit=limit,
            sort=sort,
        )
        total = await db.total_count()
        return {
            "total": total,
            "next_cursor": next_cursor,
            "images": docs,
            "search_mode": False,
            "sort": sort,
        }


@router.get("/images/color-pick")
async def color_pick(
    request: Request,
    hex_color: str,
    distance: float = 20.0,
    exclude_opposite: bool = False,
    limit: int = 100,
):
    """Return images whose dominant color (color_vector) is within CIE76 ΔE distance of hex_color."""
    db = _db(request)
    if not db.has_color_vector:
        raise HTTPException(status_code=503, detail="Color vector index not ready. Run color backfill first.")

    try:
        lab = _hex_to_lab(hex_color)
    except ValueError as e:
        logger.warning("Invalid hex color %r: %s", hex_color, e)
        raise HTTPException(status_code=422, detail="カラーコードの形式が正しくありません")

    distance = max(1.0, min(float(distance), 100.0))
    limit = max(1, min(int(limit), 200))

    exclude_ranges = None
    if exclude_opposite:
        hex_clean = hex_color.lstrip("#")
        r, g, b = int(hex_clean[0:2], 16), int(hex_clean[2:4], 16), int(hex_clean[4:6], 16)
        h, s, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s >= 0.15:  # achromatic query: skip opposite exclusion
            exclude_ranges = _opposite_hue_ranges(h * 360.0)

    docs = await db.search_by_color_vector(
        lab=list(lab),
        distance=distance,
        limit=limit,
        exclude_hue_ranges=exclude_ranges,
    )
    return {
        "hex_color": hex_color,
        "lab": list(lab),
        "distance": distance,
        "exclude_opposite": exclude_opposite,
        "results": docs,
    }


@router.get("/images/color-like/{sha256}")
async def color_like(sha256: str, request: Request, limit: int = 100, distance: float = 25.0):
    """Return images whose dominant color (L*a*b*) is similar to the given image."""
    db = _db(request)
    doc = await db.get(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found")
    color_lab: list[float] | None = doc.get("color_lab")
    if not color_lab:
        raise HTTPException(status_code=422, detail="This image has no color data. Run the color backfill.")
    docs = await db.search_by_color_vector(lab=color_lab, distance=distance, limit=limit + 1)
    docs = [d for d in docs if d.get("sha256") != sha256][:limit]
    return {"sha256": sha256, "results": docs}


@router.get("/images/random")
async def random_images_route(request: Request, n: int = 3, exclude: str = ""):
    n = max(1, min(n, 6))
    exclude_list = [s for s in exclude.split(",") if s] if exclude else []
    docs = await _db(request).random_sample(n, exclude_sha256s=exclude_list)
    return {"images": docs}


@router.get("/images/{sha256}")
async def get_image(sha256: str, request: Request):
    doc = await _db(request).get(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found")
    return doc


class RatingBody(BaseModel):
    rating: Optional[int] = None  # 1-5 or null to remove


@router.put("/images/{sha256}/rating")
async def set_image_rating(sha256: str, body: RatingBody, request: Request):
    db = _db(request)
    if body.rating is None:
        await db.delete_payload_keys(sha256, ["star_rating"])
    else:
        if not (1 <= body.rating <= 5):
            raise HTTPException(status_code=422, detail="rating must be 1-5 or null")
        await db.set_payload(sha256, {"star_rating": body.rating})
    doc = await db.get(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found")
    return doc


@router.get("/tags")
async def get_tags(request: Request, limit: int = 1000):
    now = time.monotonic()
    if _tags_cache["data"] is not None and now - _tags_cache["ts"] < _TAGS_TTL:
        return _tags_cache.get("data_filtered", _tags_cache["data"])[:limit]

    db = _db(request)
    docs = await db.scroll_tags()

    tag_count: dict[str, int] = {}
    for doc in docs:
        prompt = doc.get("positive_prompt", "")
        if isinstance(prompt, str) and prompt:
            for t in prompt.split(","):
                t = t.strip().lower()
                if 2 < len(t) < 60:
                    tag_count[t] = tag_count.get(t, 0) + 1
        for t in (doc.get("wd14_tags") or []):
            if isinstance(t, str) and 2 < len(t) < 60:
                tag_count[t] = tag_count.get(t, 0) + 1

    top = sorted(tag_count.items(), key=lambda x: -x[1])[:1000]
    data = [{"tag": t, "count": c, "category": guess_category(t)} for t, c in top]

    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    noise = set(cfg.get("graph_noise_tags", []))
    data_filtered = [d for d in data if d["tag"] not in noise]

    _tags_cache["data"] = data
    _tags_cache["data_filtered"] = data_filtered
    _tags_cache["ts"] = now
    return data_filtered[:limit]


@router.get("/tags/suggest")
async def suggest_tags(q: str = "", limit: int = 10):
    if not q or len(q) < 1:
        return []
    source = _tags_cache.get("data_filtered") or _tags_cache.get("data")
    if not source:
        return []
    q_lower = q.lower().strip()
    starts = [t for t in source if t["tag"].startswith(q_lower)]
    contains = [t for t in source if q_lower in t["tag"] and not t["tag"].startswith(q_lower)]
    return (starts + contains)[:limit]



@router.get("/thumbnails/{sha256}.webp")
async def serve_thumbnail(sha256: str, response: Response):
    thumb = get_thumbnail_path(sha256)
    if not thumb.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["ETag"] = f'"{sha256}"'
    return FileResponse(thumb, media_type="image/webp")


@router.get("/originals/{sha256}")
async def serve_original(sha256: str, request: Request, response: Response):
    doc = await _db(request).get(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found")
    file_path = Path(doc["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not on disk")
    media_type = MEDIA_TYPES.get(doc.get("ext", ""), "application/octet-stream")
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["ETag"] = f'"{sha256}"'
    return FileResponse(file_path, media_type=media_type)


@router.get("/download/{sha256}")
async def download_image(sha256: str, request: Request, response: Response):
    doc = await _db(request).get(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found")
    file_path = Path(doc["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not on disk")
    media_type = MEDIA_TYPES.get(doc.get("ext", ""), "application/octet-stream")
    filename = doc.get("name", sha256) + doc.get("ext", "")
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment",
    )


@router.get("/images/{sha256}/raw-metadata")
async def get_raw_metadata(sha256: str, request: Request):
    """Read PNG metadata chunks directly from the image file on disk."""
    doc = await _db(request).get(sha256)
    if not doc:
        raise HTTPException(status_code=404, detail="Image not found")
    file_path = Path(doc["path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not on disk")

    try:
        with _PILImage.open(file_path) as img:
            raw_info = img.info or {}
            fmt = img.format
    except Exception as exc:
        logger.error("Failed to read image metadata %s: %s", file_path, exc)
        raise HTTPException(status_code=500, detail="画像の読み込みに失敗しました")

    sections: list[dict] = []

    # A1111 parameters (plain text)
    if "parameters" in raw_info:
        sections.append({
            "key": "parameters",
            "label": "A1111 Parameters",
            "type": "text",
            "content": raw_info["parameters"],
        })

    # ComfyUI API prompt (JSON)
    if "prompt" in raw_info:
        try:
            parsed = json.loads(raw_info["prompt"])
            sections.append({
                "key": "prompt",
                "label": "ComfyUI Prompt (API)",
                "type": "json",
                "content": parsed,
            })
        except Exception:
            sections.append({
                "key": "prompt",
                "label": "ComfyUI Prompt (raw)",
                "type": "text",
                "content": raw_info["prompt"],
            })

    # ComfyUI visual workflow (JSON)
    if "workflow" in raw_info:
        try:
            parsed = json.loads(raw_info["workflow"])
            sections.append({
                "key": "workflow",
                "label": "ComfyUI Workflow",
                "type": "json",
                "content": parsed,
            })
        except Exception:
            sections.append({
                "key": "workflow",
                "label": "ComfyUI Workflow (raw)",
                "type": "text",
                "content": raw_info["workflow"],
            })

    # Any other keys
    for key, value in raw_info.items():
        if key in ("parameters", "prompt", "workflow"):
            continue
        if isinstance(value, (str, int, float)):
            sections.append({
                "key": key,
                "label": key,
                "type": "text",
                "content": str(value),
            })

    return {
        "sha256": sha256,
        "file_format": fmt,
        "sections": sections,
    }

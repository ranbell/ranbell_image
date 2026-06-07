import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..alignment.evaluator import AlignmentEvaluator
from ..alignment.vlm_analyzer import translate_to_lang
from ..alignment.schema import AlignmentRequest
from ..spooler.models import JobLane

_SUPPORTED_EXTRA_LANGS = {"zh", "ko", "fr", "de", "es", "pt"}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alignment")


class AlignmentBatchRequest(BaseModel):
    sha256s: list[str]


@router.post("/evaluate")
async def evaluate_alignment(body: AlignmentRequest, request: Request):
    from ..jobs.runners import _PRIORITY_ALIGNMENT, run_alignment_evaluate
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    sha256s = body.sha256s if body.sha256s else None
    job_id = spooler.submit(
        JobLane.EVALUATION,
        "alignment_evaluate",
        run_alignment_evaluate,
        meta={"sha256s": sha256s} if sha256s else {},
        db=db,
        ollama=ollama,
        sha256s=sha256s,
        spooler=spooler if sha256s is None else None,
        priority=_PRIORITY_ALIGNMENT,
    )
    return {"status": "queued", "job_id": job_id}


@router.post("/evaluate/single/{sha256}")
async def evaluate_single(sha256: str, request: Request):
    """Single-image evaluation: runs asynchronously via spooler. Returns job_id immediately."""
    from ..jobs.runners import _PRIORITY_ALIGNMENT, run_alignment_evaluate
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    job_id = spooler.submit(
        JobLane.EVALUATION,
        "alignment_evaluate_single",
        run_alignment_evaluate,
        meta={"sha256s": [sha256]},
        db=db,
        ollama=ollama,
        sha256s=[sha256],
        priority=_PRIORITY_ALIGNMENT,
    )
    return {"status": "queued", "job_id": job_id}


@router.post("/batch")
async def get_alignments_batch(body: AlignmentBatchRequest, request: Request):
    """Batch-fetch alignment records for multiple sha256s. Returns: {sha256: record}"""
    db = request.app.state.db
    return await db.get_alignments_batch(body.sha256s)


@router.post("/translate/{sha256}/{lang}")
async def translate_alignment(sha256: str, lang: str, request: Request):
    """Translate an existing alignment record into a new language and persist it.

    ja and en are always generated during evaluation; this endpoint handles
    additional languages (zh, ko, fr, de, es, pt) on demand.
    """
    if lang in ("ja", "en"):
        raise HTTPException(status_code=400, detail="ja and en are generated automatically during evaluation")
    if lang not in _SUPPORTED_EXTRA_LANGS:
        raise HTTPException(status_code=400, detail=f"Unsupported lang '{lang}'. Supported: {sorted(_SUPPORTED_EXTRA_LANGS)}")

    db = request.app.state.db
    ollama = request.app.state.ollama

    record = await db.get_alignment(sha256)
    if not record:
        raise HTTPException(status_code=404, detail="No alignment record found")

    # Return cached translation if already stored
    if record.get("summary_i18n", {}).get(lang):
        return {
            "sha256": sha256,
            "lang": lang,
            "cached": True,
            "summary": record["summary_i18n"][lang],
            "matched_elements": record.get("matched_elements_i18n", {}).get(lang, []),
            "unmatched_elements": record.get("unmatched_elements_i18n", {}).get(lang, []),
        }

    summary_ja = record.get("summary_i18n", {}).get("ja") or record.get("summary", "")
    matched_ja = record.get("matched_elements_i18n", {}).get("ja") or record.get("matched_elements", [])
    unmatched_ja = record.get("unmatched_elements_i18n", {}).get("ja") or record.get("unmatched_elements", [])

    translated = await translate_to_lang(
        summary_ja=summary_ja,
        matched_ja=matched_ja,
        unmatched_ja=unmatched_ja,
        lang=lang,
        ollama=ollama,
    )
    if not translated:
        raise HTTPException(status_code=502, detail="Translation failed after retries")

    # Merge into existing i18n dicts and persist
    summary_i18n = dict(record.get("summary_i18n") or {})
    matched_i18n = dict(record.get("matched_elements_i18n") or {})
    unmatched_i18n = dict(record.get("unmatched_elements_i18n") or {})

    summary_i18n[lang] = translated["summary"]
    matched_i18n[lang] = translated["matched_elements"]
    unmatched_i18n[lang] = translated["unmatched_elements"]

    updated_record = dict(record)
    updated_record["summary_i18n"] = summary_i18n
    updated_record["matched_elements_i18n"] = matched_i18n
    updated_record["unmatched_elements_i18n"] = unmatched_i18n
    await db.upsert_alignment(sha256, updated_record)

    return {
        "sha256": sha256,
        "lang": lang,
        "cached": False,
        "summary": translated["summary"],
        "matched_elements": translated["matched_elements"],
        "unmatched_elements": translated["unmatched_elements"],
    }


@router.get("/{sha256}")
async def get_alignment(sha256: str, request: Request):
    db = request.app.state.db
    record = await db.get_alignment(sha256)
    if not record:
        raise HTTPException(status_code=404, detail="No alignment record found")
    return record

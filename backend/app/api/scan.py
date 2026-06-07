from fastapi import APIRouter, Request

from ..spooler.models import JobLane

router = APIRouter(prefix="/api")


@router.post("/scan")
async def trigger_scan(request: Request):
    from ..jobs.runners import run_scan_heal
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    job_id = spooler.submit(JobLane.SYNC, "scan_heal", run_scan_heal,
                            db=db, ollama=ollama, spooler=spooler)
    return {"status": "queued", "job_id": job_id}


@router.post("/scan/full")
async def trigger_full_scan(request: Request):
    from ..jobs.runners import run_scan_full
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    job_id = spooler.submit(JobLane.SYNC, "scan_full", run_scan_full,
                            db=db, ollama=ollama, spooler=spooler)
    return {"status": "queued", "job_id": job_id}


@router.post("/scan/refresh-metadata")
async def trigger_refresh_metadata(request: Request):
    from ..jobs.runners import run_scan_refresh_metadata
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(
        JobLane.SYNC, "meta_update", run_scan_refresh_metadata, db=db
    )
    return {"status": "queued", "job_id": job_id}


@router.get("/scan/status")
async def get_scan_status(request: Request):
    """Return the current SYNC lane job state (backwards-compatible endpoint)."""
    spooler = request.app.state.spooler
    sync_jobs = [
        j for j in spooler.snapshot()
        if j["lane"] == "sync" and j["state"] in ("running", "cancelling", "queued")
    ]
    if sync_jobs:
        j = sync_jobs[0]
        return {
            "running": j["state"] == "running",
            "job_id": j["id"],
            "title": j["title"],
            "progress": j["progress"],
            "progress_text": j["progress_text"],
            "elapsed": j["elapsed"],
        }
    return {"running": False}

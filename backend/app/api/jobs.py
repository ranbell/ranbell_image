import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..spooler.models import JobLane, LanePauseReason

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs")


@router.get("/stream")
async def job_stream(request: Request):
    """SSE: stream real-time job status and progress."""
    spooler = request.app.state.spooler

    async def _generate():
        try:
            async for chunk in spooler.stream():
                if await request.is_disconnected():
                    break
                yield chunk
        except Exception:
            logger.exception("SSE stream error")

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("")
async def list_jobs(request: Request):
    """Return the current job list (running + queued + history)."""
    spooler = request.app.state.spooler
    return {
        "jobs": spooler.snapshot(),
        "resources": spooler.resources_snapshot(),
    }


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    spooler = request.app.state.spooler
    ok = await spooler.cancel(job_id)
    if not ok:
        raise HTTPException(404, f"Job {job_id!r} not found or not cancellable")
    return {"status": "cancel_requested", "job_id": job_id}


class ReorderBody(BaseModel):
    direction: int  # +1 = raise priority, -1 = lower priority


@router.post("/{job_id}/pause")
async def pause_job(job_id: str, request: Request):
    """Pause a running job at the next checkpoint."""
    ok = request.app.state.spooler.pause_job(job_id)
    return {"ok": ok, "job_id": job_id}


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, request: Request):
    """Resume an individually paused job."""
    ok = request.app.state.spooler.resume_job(job_id)
    return {"ok": ok, "job_id": job_id}


@router.post("/{job_id}/reorder")
async def reorder_job(job_id: str, body: ReorderBody, request: Request):
    """Change the order of a job in the queue (direction: +1=move up, -1=move down)."""
    ok = request.app.state.spooler.reorder_job(job_id, body.direction)
    return {"ok": ok, "job_id": job_id}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    spooler = request.app.state.spooler
    try:
        new_id = spooler.retry(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id!r} not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"status": "queued", "job_id": new_id, "retried_from": job_id}


# ── Lane pause / resume ───────────────────────────────────────────────────────

@router.get("/lanes")
async def get_lanes(request: Request):
    """Return the pause state of all lanes."""
    return {"lanes": request.app.state.spooler.lanes_snapshot()}


@router.post("/lanes/{lane}/pause")
async def pause_lane(lane: str, request: Request):
    """Manually pause the specified lane."""
    try:
        lane_enum = JobLane(lane)
    except ValueError:
        raise HTTPException(400, f"Unknown lane: {lane!r}")
    request.app.state.spooler.pause_lanes([lane_enum], LanePauseReason.MANUAL)
    return {
        "status": "paused",
        "lane": lane,
        "lanes": request.app.state.spooler.lanes_snapshot(),
    }


@router.post("/lanes/{lane}/resume")
async def resume_lane(lane: str, request: Request):
    """Force-resume the specified lane (clears both manual and auto pause)."""
    try:
        lane_enum = JobLane(lane)
    except ValueError:
        raise HTTPException(400, f"Unknown lane: {lane!r}")
    request.app.state.spooler.resume_lanes([lane_enum])   # reason=None → force resume
    return {
        "status": "resumed",
        "lane": lane,
        "lanes": request.app.state.spooler.lanes_snapshot(),
    }

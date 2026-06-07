from fastapi import APIRouter, Request

from ..config import settings

router = APIRouter(prefix="/api/comfy")


@router.get("/status")
async def comfy_status(request: Request):
    c = request.app.state.comfy
    return {"available": await c.is_available(), "url": settings.comfyui_url}


@router.get("/workflows")
async def list_workflows(request: Request):
    c = request.app.state.comfy
    return c.list_workflows()

"""HTTP API for LoRA comparison sheets."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import sheet as sh
from . import sheet_grid
from . import targets as target_models

router = APIRouter(prefix="/api/lora", tags=["lora"])

_MODEL_FOLDERS = {"loras": "loras", "unets": "diffusion_models",
                  "clips": "text_encoders", "vaes": "vae"}


class SheetRow(BaseModel):
    lora: str
    trigger: str = ""
    label: str = ""


class SheetCreate(BaseModel):
    name: str = ""
    target_model: str = target_models.DEFAULT_TARGET
    models: dict[str, str] = Field(default_factory=dict)
    prompt: str
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cfg: float | None = None
    sampler: str | None = None
    scheduler: str | None = None
    rows: list[SheetRow]
    strengths: list[float] | None = None
    no_trigger_column: bool = False
    bucket_id: str = ""
    start: bool = True


class DuplicateBody(BaseModel):
    seed: int | None = None  # None = a new random seed
    name: str = ""
    start: bool = True


def _db(request: Request):
    return request.app.state.db


def _view(sheet: dict) -> dict:
    return sh.summary(sheet) | {
        k: sheet[k] for k in ("models", "prompt", "width", "height", "steps", "cfg", "sampler",
                              "scheduler", "rows", "strengths", "no_trigger_column", "renders")
    } | {
        "columns_spec": sh.columns(sheet),
        "grid": sh.grid(sheet),
        "drawing": sh.is_drawing(sheet["id"]),
        "drawing_key": (sh.latest_preview(sheet["id"]) or {}).get("key", ""),
    }


async def _load(request: Request, sheet_id: str) -> dict:
    sheet = await sh.load(_db(request), sheet_id)
    if sheet is None:
        raise HTTPException(404, "sheet not found")
    return sheet


def _start(request: Request, sheet: dict) -> str | None:
    return sh.submit(request.app.state.spooler, db=_db(request),
                     comfy=request.app.state.comfy, sheet=sheet)


@router.get("/sheet-options")
async def sheet_options(request: Request):
    """What a sheet can be made of: ComfyUI's model files, samplers, and each
    target's defaults. ComfyUI being down is not an error here — the screen
    says so and the lists stay empty."""
    comfy = request.app.state.comfy
    out: dict[str, Any] = {k: [] for k in _MODEL_FOLDERS} | {"samplers": [], "schedulers": []}
    try:
        lists = await asyncio.gather(*(comfy.list_models(f) for f in _MODEL_FOLDERS.values()))
        out |= dict(zip(_MODEL_FOLDERS, lists))
        out |= await comfy.sampler_choices()
        out["comfy"] = True
    except Exception as exc:
        out["comfy"] = False
        out["comfy_error"] = str(exc) or exc.__class__.__name__
    out["targets"] = [
        {"id": t.id, "label": t.label, "defaults": t.sheet_defaults}
        for t in target_models.available() if t.build_graph is not None
    ]
    out["default_target"] = target_models.DEFAULT_TARGET
    out["default_strengths"] = sh.DEFAULT_STRENGTHS
    out["seconds_per_picture"] = sh.seconds_per_picture(await sh.list_all(_db(request)))
    return out


def _resolve(graph: dict, value: Any, depth: int = 0) -> Any:
    """A literal input, or the literal at the end of a wire (Primitive / Seed nodes)."""
    if not isinstance(value, list) or depth > 3:
        return value
    node = graph.get(str(value[0])) or {}
    for v in (node.get("inputs") or {}).values():
        if not isinstance(v, list):
            return v
    for v in (node.get("inputs") or {}).values():
        r = _resolve(graph, v, depth + 1)
        if r is not None and not isinstance(r, list):
            return r
    return None


def prefill_from_doc(doc: dict, image_size: tuple[int, int] | None = None,
                     clip_type: str = "") -> dict:
    """Sheet settings taken from a gallery picture: its prompt and seed, and —
    when its own ComfyUI graph is stored — the model files and sampling.

    ``image_size`` is the picture's own pixel size. It is what the sheet's
    aspect ratio follows (an upscaled or cropped picture still has the shape
    it was meant to have); the graph's latent size is only a fallback.

    ``clip_type`` is the sheet target's CLIPLoader type. When the picture's
    graph loads no text encoder of that type, it was drawn by another model
    (an Anima picture opened for a Krea2 sheet): its model files and sampling
    would build a graph that cannot run, so only the prompt, seed and shape
    are taken, and ``foreign`` says why the rest was left alone."""
    cr = doc.get("creation_record") or {}
    mi = doc.get("model_info") or {}
    out: dict[str, Any] = {
        "prompt": cr.get("positive_prompt_generated") or doc.get("positive_prompt") or "",
        "seed": cr.get("seed") if cr.get("seed") is not None else mi.get("seed"),
        "models": {},
    }
    graph = (doc.get("raw_metadata") or {}).get("content")
    if not isinstance(graph, dict):
        return _with_shape(out, image_size)
    if clip_type:
        types = [str((n.get("inputs") or {}).get("type") or "") for n in graph.values()
                 if isinstance(n, dict) and n.get("class_type") == "CLIPLoader"]
        if clip_type not in types:
            for node in graph.values():
                if isinstance(node, dict) and str(node.get("class_type", "")).startswith(
                        ("EmptyLatent", "EmptySD3")):
                    _latent_size(graph, node, out)
                elif (isinstance(node, dict) and out["seed"] is None
                      and node.get("class_type") in ("KSampler", "KSamplerAdvanced")):
                    inp = node.get("inputs") or {}
                    out["seed"] = _resolve(graph, inp.get("seed", inp.get("noise_seed")))
            out["foreign"] = {"clip_types": [t for t in types if t]}
            return _with_shape(out, image_size)
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ct, inp = node.get("class_type", ""), node.get("inputs") or {}
        if ct == "UNETLoader" and isinstance(inp.get("unet_name"), str):
            out["models"]["unet"] = inp["unet_name"]
        elif (ct == "CLIPLoader" and isinstance(inp.get("clip_name"), str)
              and (not clip_type or inp.get("type") == clip_type)):
            out["models"]["clip"] = inp["clip_name"]
        elif ct == "VAELoader" and isinstance(inp.get("vae_name"), str):
            out["models"]["vae"] = inp["vae_name"]
        elif ct in ("KSampler", "KSamplerAdvanced"):
            for src, dst in (("steps", "steps"), ("cfg", "cfg"), ("sampler_name", "sampler"),
                             ("scheduler", "scheduler")):
                v = _resolve(graph, inp.get(src))
                if v is not None:
                    out[dst] = v
            if out["seed"] is None:
                out["seed"] = _resolve(graph, inp.get("seed", inp.get("noise_seed")))
        elif ct.startswith(("EmptyLatent", "EmptySD3")):
            _latent_size(graph, node, out)
    return _with_shape(out, image_size)


def _latent_size(graph: dict, node: dict, out: dict) -> None:
    for side in ("width", "height"):
        v = _resolve(graph, (node.get("inputs") or {}).get(side))
        if isinstance(v, (int, float)):
            out[side] = int(v)


def _with_shape(out: dict, image_size: tuple[int, int] | None) -> dict:
    if image_size and image_size[0] > 0 and image_size[1] > 0:
        out["source_width"], out["source_height"] = image_size
    elif out.get("width") and out.get("height"):
        out["source_width"], out["source_height"] = out["width"], out["height"]
    return {k: v for k, v in out.items() if v not in (None, "")}


def _pixel_size(path: str) -> tuple[int, int] | None:
    """Width and height from the file header (no decode). EXIF-rotated photos
    report their stored size; ranbell's own pictures carry no rotation."""
    from PIL import Image

    from ..scanner.walk import PIL_FORMATS

    try:
        with Image.open(path, formats=PIL_FORMATS) as im:
            return im.size
    except Exception:
        return None


@router.get("/sheet-prefill/{sha256}")
async def sheet_prefill(sha256: str, request: Request, target: str = ""):
    doc = await _db(request).get(sha256)
    if not doc:
        raise HTTPException(404, "picture not found")
    size = None
    if doc.get("path"):
        size = await asyncio.get_running_loop().run_in_executor(None, _pixel_size, doc["path"])
    return prefill_from_doc(doc, size, target_models.get(target).clip_type)


@router.get("/sheets")
async def list_sheets(request: Request):
    return {"sheets": [sh.summary(s) | {"drawing": sh.is_drawing(s["id"])}
                       for s in await sh.list_all(_db(request))]}


@router.post("/sheets")
async def create_sheet(body: SheetCreate, request: Request):
    data = body.model_dump()
    data["rows"] = [r.model_dump() for r in body.rows]
    try:
        sheet = sh.new_sheet(data)
    except sh.SheetError as exc:
        raise HTTPException(400, str(exc))
    async with sh.lock(sheet["id"]):
        await sh.save(_db(request), sheet)
    job_id = _start(request, sheet) if body.start else None
    return _view(sheet) | {"job_id": job_id}


@router.get("/sheets/{sheet_id}")
async def get_sheet(sheet_id: str, request: Request):
    return _view(await _load(request, sheet_id))


@router.delete("/sheets/{sheet_id}")
async def delete_sheet(sheet_id: str, request: Request):
    """The sheet only; the pictures it drew stay in the gallery."""
    await _load(request, sheet_id)
    await sh.delete(_db(request), sheet_id)
    return {"ok": True}


@router.post("/sheets/{sheet_id}/render")
async def render_sheet(sheet_id: str, request: Request):
    """Draw whatever is not drawn yet (failed pictures included)."""
    async with sh.lock(sheet_id):
        sheet = await _load(request, sheet_id)
        if not sh.is_drawing(sheet_id):
            sh.request_rest(sheet)
            await sh.save(_db(request), sheet)
    return _view(sheet) | {"job_id": _start(request, sheet)}


@router.post("/sheets/{sheet_id}/cancel")
async def cancel_sheet(sheet_id: str, request: Request):
    """Stop drawing. The picture in flight is interrupted; what is drawn stays,
    and 'draw the rest' picks up from there."""
    job_id = sh.active_job(sheet_id)
    ok = bool(job_id) and await request.app.state.spooler.cancel(job_id)
    return {"ok": ok, "job_id": job_id}


@router.post("/sheets/{sheet_id}/duplicate")
async def duplicate_sheet(sheet_id: str, body: DuplicateBody, request: Request):
    try:
        copy = sh.duplicate(await _load(request, sheet_id), seed=body.seed, name=body.name)
    except sh.SheetError as exc:
        raise HTTPException(400, str(exc))
    async with sh.lock(copy["id"]):
        await sh.save(_db(request), copy)
    job_id = _start(request, copy) if body.start else None
    return _view(copy) | {"job_id": job_id}


@router.get("/sheets/{sheet_id}/preview")
async def sheet_preview(sheet_id: str):
    """The newest in-flight frame of the picture being drawn (a JPEG), with the
    cell it belongs to in ``X-Sheet-Key``; 204 when nothing is in flight."""
    p = sh.latest_preview(sheet_id)
    headers = {"Cache-Control": "no-store"}
    if not p:
        return Response(status_code=204, headers=headers)
    headers |= {"X-Sheet-Key": p["key"], "X-Preview-Seq": str(p["seq"])}
    if not p["jpeg"]:
        return Response(status_code=204, headers=headers)
    return Response(p["jpeg"], media_type="image/jpeg", headers=headers)


@router.get("/sheets/{sheet_id}/grid.png")
async def sheet_png(sheet_id: str, request: Request):
    sheet = await _load(request, sheet_id)
    db = _db(request)
    paths: dict[str, Path] = {}
    for r in sheet["renders"].values():
        if r.get("sha256"):
            doc = await db.get(r["sha256"]) or {}
            p = Path(doc.get("path") or "")
            if p.name and p.is_file():
                paths[r["sha256"]] = p

    def _open(sha: str):
        from PIL import Image

        p = paths.get(sha)
        if p is None:
            return None
        try:
            from ..scanner.walk import PIL_FORMATS

            return Image.open(io.BytesIO(p.read_bytes()), formats=PIL_FORMATS)
        except Exception:
            return None

    data = await asyncio.get_running_loop().run_in_executor(
        None, sheet_grid.compose, sheet, _open)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sheet["name"])[:60] or "sheet"
    return Response(data, media_type="image/png",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.png"'})

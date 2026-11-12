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
    seed: int | str | None = None  # text keeps seeds above 2^53 exact
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
    seed: int | str | None = None  # None = a new random seed
    name: str = ""
    start: bool = True


def _db(request: Request):
    return request.app.state.db


def _view(sheet: dict) -> dict:
    return sh.summary(sheet) | {"seed": str(sheet["seed"])} | {
        k: sheet[k] for k in ("models", "prompt", "width", "height", "steps", "cfg", "sampler",
                              "scheduler", "rows", "strengths", "no_trigger_column", "renders")
    } | {
        "columns_spec": sh.columns(sheet),
        "grid": sh.grid(sheet),
        "drawing": sh.is_drawing(sheet["id"]),
        "drawing_key": (sh.latest_preview(sheet["id"]) or {}).get("key", ""),
        "drawing_step": [(sh.latest_preview(sheet["id"]) or {}).get(k, 0) for k in ("step", "steps")],
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


#: Input names that carry the same value on helper nodes (Primitive, literals).
_ALIASES = {
    "steps": ("steps", "int", "value", "steps_total"),
    "cfg": ("cfg", "float", "value"),
    "sampler_name": ("sampler_name", "sampler", "value"),
    "scheduler": ("scheduler", "value"),
    "seed": ("seed", "noise_seed", "int", "value"),
    "width": ("width", "int", "value"),
    "height": ("height", "int", "value"),
    "denoise": ("denoise", "float", "value"),
}


def _resolve(graph: dict, value: Any, name: str = "", depth: int = 0) -> Any:
    """A literal input, or the literal at the end of a wire (Primitive / Seed /
    config nodes). On a node with several literals, the one whose input name
    matches is taken — never just the first (a config node holding steps, cfg
    and sampler would answer 30 for all of them). None when it cannot tell."""
    if not isinstance(value, list) or depth > 3:
        return value
    node = graph.get(str(value[0])) or {}
    inputs = node.get("inputs") or {}
    names = _ALIASES.get(name, (name,)) if name else ()
    for n in names:
        if n in inputs:
            return _resolve(graph, inputs[n], name, depth + 1)
    literals = [v for v in inputs.values() if not isinstance(v, list)]
    if len(literals) == 1:
        return literals[0]
    wires = [v for v in inputs.values() if isinstance(v, list)]
    if not literals and len(wires) == 1:
        return _resolve(graph, wires[0], name, depth + 1)
    return None


def _typed(value: Any, kind: type) -> Any:
    if kind is str:
        return value if isinstance(value, str) and value else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return kind(value)


def _seed_text(value: Any) -> str | None:
    """Seeds go out as text: the browser rounds numbers above 2^53."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    s = str(value).strip()
    return s if s.isdigit() else None


def _first_pass_sampler(graph: dict) -> dict | None:
    """The sampler that draws the picture from noise — not a hires-fix or
    refiner pass after it (those run at a lower denoise)."""
    samplers = [n for n in graph.values()
                if isinstance(n, dict) and n.get("class_type") in ("KSampler", "KSamplerAdvanced")]
    if not samplers:
        return None

    def from_empty(n: dict) -> bool:
        link = (n.get("inputs") or {}).get("latent_image")
        src = graph.get(str(link[0])) if isinstance(link, list) else None
        return bool(src) and str(src.get("class_type", "")).startswith(("EmptyLatent", "EmptySD3"))

    def full(n: dict) -> bool:
        d = _resolve(graph, (n.get("inputs") or {}).get("denoise", 1.0), "denoise")
        return not isinstance(d, (int, float)) or d >= 0.999

    return max(samplers, key=lambda n: (from_empty(n), full(n)))


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
    from_sheet = doc.get("lora_sheet") or {}
    # A sheet's own picture: its prompt without the row's trigger in front,
    # or a sheet made from it would write the trigger twice.
    prompt = (from_sheet.get("prompt") if isinstance(from_sheet, dict) else None) \
        or cr.get("positive_prompt_generated") or doc.get("positive_prompt") or ""
    source = "prompt"
    if not str(prompt).strip():
        # A picture with no prompt of its own (a photo, a drawing — "NR" in the
        # gallery): its WD14 tags as a starting point, to be edited on screen.
        prompt, source = wd14_prompt(doc.get("wd14_tags") or []), "wd14"
    out: dict[str, Any] = {
        "prompt": prompt,
        "prompt_source": source if prompt else "",
        "seed": _seed_text(cr.get("seed") if cr.get("seed") is not None else mi.get("seed")),
        "models": {},
    }
    graph = (doc.get("raw_metadata") or {}).get("content")
    if not isinstance(graph, dict):
        return _with_shape(out, image_size)
    sampler = _first_pass_sampler(graph)
    sinp = (sampler or {}).get("inputs") or {}
    if out["seed"] is None and sampler is not None:
        out["seed"] = _seed_text(_resolve(graph, sinp.get("seed", sinp.get("noise_seed")), "seed"))
    _latent_size(graph, out)
    if clip_type:
        types = [str((n.get("inputs") or {}).get("type") or "") for n in graph.values()
                 if isinstance(n, dict) and n.get("class_type") == "CLIPLoader"]
        if clip_type not in types:
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
    if sampler is not None:
        for src, dst, kind in (("steps", "steps", int), ("cfg", "cfg", float),
                               ("sampler_name", "sampler", str), ("scheduler", "scheduler", str)):
            v = _typed(_resolve(graph, sinp.get(src), src), kind)
            if v is not None:
                out[dst] = v
    return _with_shape(out, image_size)


WD14_RATINGS = {"general", "sensitive", "questionable", "explicit"}
WD14_MAX_TAGS = 40


def wd14_prompt(tags: list[str]) -> str:
    """WD14 tags as a prompt: the rating tags dropped, ``long_hair`` read as
    ``long hair`` (emoticons such as ``^_^`` left alone), at most 40."""
    out: list[str] = []
    for tag in tags:
        tag = str(tag or "").strip()
        if not tag or tag in WD14_RATINGS:
            continue
        if any(ch.isalnum() for ch in tag):
            tag = tag.replace("_", " ")
        if tag not in out:
            out.append(tag)
        if len(out) >= WD14_MAX_TAGS:
            break
    return ", ".join(out)


def _latent_size(graph: dict, out: dict) -> None:
    """The empty latent's size (the first one: a second is usually a pass
    after the picture was drawn)."""
    for node in graph.values():
        if isinstance(node, dict) and str(node.get("class_type", "")).startswith(
                ("EmptyLatent", "EmptySD3")):
            for side in ("width", "height"):
                v = _typed(_resolve(graph, (node.get("inputs") or {}).get(side), side), int)
                if v:
                    out[side] = v
            if out.get("width") and out.get("height"):
                return


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


class RenameBody(BaseModel):
    name: str


@router.patch("/sheets/{sheet_id}")
async def rename_sheet(sheet_id: str, body: RenameBody, request: Request):
    async with sh.lock(sheet_id):
        sheet = await _load(request, sheet_id)
        try:
            sh.rename(sheet, body.name)
        except sh.SheetError as exc:
            raise HTTPException(400, str(exc))
        await sh.save(_db(request), sheet)
    return _view(sheet)


@router.delete("/sheets/{sheet_id}")
async def delete_sheet(sheet_id: str, request: Request):
    """The sheet only; the pictures it drew stay in the gallery. A drawing job
    on it is stopped, and the delete waits for the job's save in flight so that
    save cannot bring the sheet back."""
    job_id = sh.active_job(sheet_id)
    if job_id:
        await request.app.state.spooler.cancel(job_id)
    async with sh.lock(sheet_id):
        await _load(request, sheet_id)
        await sh.delete(_db(request), sheet_id)
    sh.forget_lock(sheet_id)
    return {"ok": True}


@router.post("/sheets/{sheet_id}/render")
async def render_sheet(sheet_id: str, request: Request):
    """Draw whatever is not drawn yet (failed pictures included)."""
    async with sh.lock(sheet_id):
        sheet = await _load(request, sheet_id)
        if not sh.is_drawing(sheet_id):
            sh.request_rest(sheet)
            await sh.save(_db(request), sheet)
    job_id = _start(request, sheet)  # first: the view says whether it is drawing
    return _view(sheet) | {"job_id": job_id}


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
    headers |= {"X-Sheet-Key": p["key"], "X-Preview-Seq": str(p["seq"]),
                "X-Step": str(p.get("step", 0)), "X-Steps": str(p.get("steps", 0))}
    if not p["jpeg"]:
        return Response(status_code=204, headers=headers)
    return Response(p["jpeg"], media_type="image/jpeg", headers=headers)


async def _opener(request: Request, sheets: list[dict]):
    """``open_image(sha)`` over the sheets' drawn pictures (read in a worker)."""
    db = _db(request)
    paths: dict[str, Path] = {}
    for sheet in sheets:
        for r in sheet["renders"].values():
            if r.get("sha256") and r["sha256"] not in paths:
                doc = await db.get(r["sha256"]) or {}
                p = Path(doc.get("path") or "")
                if p.name and p.is_file():
                    paths[r["sha256"]] = p

    def _open(sha: str):
        from PIL import Image

        from ..scanner.walk import PIL_FORMATS

        p = paths.get(sha)
        if p is None:
            return None
        try:
            return Image.open(io.BytesIO(p.read_bytes()), formats=PIL_FORMATS)
        except Exception:
            return None

    return _open


def _png(data: bytes, name: str) -> Response:
    """Headers are latin-1: the plain filename is ASCII only, and the real name
    (Japanese and all) goes in filename* (RFC 5987)."""
    from urllib.parse import quote

    safe = "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in name)[:60]
    safe = safe.strip("_") or "sheet"
    full = quote(f"{name[:100]}.png", safe="")
    return Response(data, media_type="image/png", headers={
        "Content-Disposition": f"attachment; filename=\"{safe}.png\"; filename*=UTF-8''{full}"})


@router.get("/sheets/{sheet_id}/grid.png")
async def sheet_png(sheet_id: str, request: Request):
    sheet = await _load(request, sheet_id)
    data = await asyncio.get_running_loop().run_in_executor(
        None, sheet_grid.compose, sheet, await _opener(request, [sheet]))
    return _png(data, sheet["name"])


class StackBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=12)


@router.post("/sheets/stack.png")
async def stack_png(body: StackBody, request: Request):
    """Several sheets stacked on shared strength columns, as one picture."""
    sheets = [await _load(request, i) for i in dict.fromkeys(body.ids)]
    data = await asyncio.get_running_loop().run_in_executor(
        None, sheet_grid.compose_stack, sheets, await _opener(request, sheets))
    return _png(data, "lora_sheets_" + str(len(sheets)))

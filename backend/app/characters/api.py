"""Character registry API: pick one, edit one, draw its reference board."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import settings
from ..spooler.models import JobLane
from . import presets as presets_db
from .board import SLOT_SIZE, compile_board_slot, plan_sheet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/characters")

# Reference boards are keepers, not drafts: they are how the user recognises a
# character in the picker, so they go in their own folder and stay in the gallery.
CHARACTER_SUBDIR = "characters"


class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    name_ja: str = ""
    summary: str = ""
    summary_ja: str = ""
    gender: str = ""
    subject_tag: str = "1girl"
    signature_prop: str = ""
    personality: list[str] = []
    appearance: dict = {}
    tags: dict = {}
    preferences: dict = {}
    default_scene: dict = {}


class CharacterUpdate(BaseModel):
    name: str | None = None
    name_ja: str | None = None
    summary: str | None = None
    summary_ja: str | None = None
    gender: str | None = None
    subject_tag: str | None = None
    signature_prop: str | None = None
    personality: list[str] | None = None
    appearance: dict | None = None
    tags: dict | None = None
    preferences: dict | None = None
    default_scene: dict | None = None


class BoardRequest(BaseModel):
    workflow_name: str = Field(..., min_length=1)
    slots: list[str] = []          # empty → every slot
    # Model that decides what she is doing in the five frames. Empty (or
    # unreachable) falls back to the fixed hobby/active/food/work slots.
    plan_model: str = ""
    # None → each slot's own canvas (see board.SLOT_SIZE). A full-body sheet and
    # a bust shot want different aspect ratios; one size for both is why the
    # portrait slot used to render a second full-body image.
    width: int | None = None
    height: int | None = None
    steps: int | None = None       # None → the workflow's own
    cfg: float | None = None
    seed: int | None = None


@router.get("")
async def list_characters(request: Request):
    rows = await presets_db.list_presets(request.app.state.db, limit=500)
    return {"characters": rows, "count": len(rows), "board_slots": list(presets_db.BOARD_SLOTS)}


@router.post("/reset")
async def reset_characters(request: Request):
    """Re-seed the bundled presets from the shipped asset file.

    The presets live in Qdrant and are seeded once, when the collection is
    empty, so editing the asset file and deploying changes nothing on a running
    install — `petite` stayed on 71 characters for a whole release after it was
    removed from the file. This is the only way to pick the edits up.

    It drops the collection, so any user-authored character goes with it.
    """
    result = await presets_db.reset_presets_to_defaults(
        request.app.state.db, vector_dim=settings.embed_dim,
    )
    logger.info("[characters] reset to defaults: %s", result)
    return result


@router.get("/{character_id}")
async def get_character(character_id: str, request: Request):
    preset = await presets_db.get_preset(request.app.state.db, character_id)
    if preset is None:
        raise HTTPException(404, "character not found")
    return {
        "character": presets_db.preset_to_character(preset),
        "preset": preset,
        "summary": presets_db.preset_summary(preset, point_id=character_id),
    }


@router.post("")
async def create_character(body: CharacterCreate, request: Request):
    row = await presets_db.create_preset(
        request.app.state.db,
        body.model_dump(),
        vector_dim=settings.embed_dim,
    )
    return row


@router.put("/{character_id}")
async def update_character(character_id: str, body: CharacterUpdate, request: Request):
    row = await presets_db.update_preset(
        request.app.state.db, character_id, body.model_dump(exclude_none=True),
    )
    if row is None:
        raise HTTPException(404, "character not found")
    return row


@router.delete("/{character_id}")
async def delete_character(character_id: str, request: Request):
    if not await presets_db.delete_preset(request.app.state.db, character_id):
        raise HTTPException(404, "character not found")
    return {"ok": True}


class ThumbnailChoice(BaseModel):
    sha256: str = Field(..., min_length=8)


class BulkThumbnailRequest(BaseModel):
    workflow_name: str = Field(..., min_length=1)
    limit: int = Field(default=200, ge=1, le=500)
    steps: int | None = None
    cfg: float | None = None


@router.post("/{character_id}/thumbnail")
async def choose_character_thumbnail(
    character_id: str, body: ThumbnailChoice, request: Request,
):
    """Adopt one of her existing candidates as the face she is shown by.

    Re-rolling a portrait keeps the old ones (`presets.GALLERY_LIMIT` of them),
    because the fifth attempt is not automatically better than the second.
    """
    board = await presets_db.choose_thumbnail(
        request.app.state.db, character_id, body.sha256,
    )
    if board is None:
        raise HTTPException(404, "character or candidate not found")
    return {"ok": True, "board": board}


@router.post("/thumbnails/missing")
async def render_missing_thumbnails(body: BulkThumbnailRequest, request: Request):
    """Draw a portrait for every character who has none.

    A hundred characters shown as a hundred name labels is the list this was
    meant to replace, so the gallery is only worth having once they all have a
    face. Portraits only — the full sheet is 1024×1344 and this is a hundred of
    them.
    """
    db = request.app.state.db
    rows = await presets_db.list_presets(db, limit=500)
    pending = [r for r in rows if not (r.get("board") or {}).get("portrait")]
    pending = pending[: body.limit]

    queued: list[dict] = []
    for row in pending:
        preset = await presets_db.get_preset(db, row["id"])
        if preset is None:
            continue
        queued += await _queue_board_slots(
            request, preset, row["id"], ["portrait"],
            workflow_name=body.workflow_name, steps=body.steps, cfg=body.cfg,
        )
    logger.info("[characters] queued %d missing portraits", len(queued))
    return {"queued": len(queued), "remaining": max(0, len(
        [r for r in rows if not (r.get("board") or {}).get("portrait")]
    ) - len(queued)), "jobs": queued}


@router.post("/{character_id}/board")
async def render_character_board(character_id: str, body: BoardRequest, request: Request):
    """Queue one render per board slot; each attaches itself when it lands."""
    db = request.app.state.db

    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise HTTPException(404, "character not found")

    slots = [s for s in (body.slots or presets_db.BOARD_SLOTS) if s in presets_db.BOARD_SLOTS]
    if not slots:
        raise HTTPException(400, f"no valid slot in {body.slots}")

    # One small call, before any render: what she is doing in the five frames,
    # read off her personality rather than picked from four fixed slots.
    plan = None
    if "sheet" in slots:
        plan = await plan_sheet(
            preset, request.app.state.ollama, model=body.plan_model,
        )

    jobs = await _queue_board_slots(
        request, preset, character_id, slots,
        workflow_name=body.workflow_name, plan=plan,
        width=body.width, height=body.height,
        steps=body.steps, cfg=body.cfg, seed=body.seed,
    )
    return {"status": "queued", "jobs": jobs, "plan": plan}


async def _queue_board_slots(
    request: Request,
    preset: dict,
    character_id: str,
    slots: list[str],
    *,
    workflow_name: str,
    plan=None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    seed: int | None = None,
) -> list[dict]:
    """Queue a render per slot; each attaches itself to the preset when it lands."""
    db = request.app.state.db
    spooler = request.app.state.spooler
    comfy = request.app.state.comfy

    from ..jobs.render import run_render

    jobs: list[dict] = []
    for slot in slots:
        positive, negative = compile_board_slot(preset, slot, plan)
        default_w, default_h = SLOT_SIZE.get(slot, (1024, 1344))
        slot_w = width or default_w
        slot_h = height or default_h

        def _attach(slot_name: str):
            async def _inner(sha256: str, _meta: dict) -> None:
                await presets_db.attach_board_image(db, character_id, slot_name, sha256)
            return _inner

        job_id = spooler.submit(
            JobLane.GENERATION,
            f"character_board:{slot}",
            run_render,
            meta={"character_id": character_id, "slot": slot},
            db=db,
            comfy=comfy,
            workflow_name=workflow_name,
            positive=positive,
            negative=negative,
            width=slot_w,
            height=slot_h,
            steps=steps,
            cfg=cfg,
            seed=seed,
            subdir=CHARACTER_SUBDIR,
            prefix=f"char_{slot}",
            method="character_board",
            payload_extra={"character_id": character_id, "character_slot": slot},
            attach=_attach(slot),
        )
        jobs.append({"slot": slot, "job_id": job_id, "character_id": character_id,
                     "positive": positive, "size": [slot_w, slot_h]})

    return jobs

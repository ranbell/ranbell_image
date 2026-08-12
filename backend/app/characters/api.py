"""Character registry API: pick one, edit one, draw its reference board."""
from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import settings
from ..spooler.models import JobLane
from . import compat as compat_mod
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
    # A readable key beside the sequential id, so a log line and the search box
    # can still say which character this is.
    slug: str = ""
    # What she is known for, beside her name. The bundled roster carries it and
    # a hand-made character has no way to say it without this.
    title: str = ""
    title_ja: str = ""
    summary: str = ""
    summary_ja: str = ""
    # The gap between how she reads and what she is. It reaches the acting seat
    # by name and the board planner reads it, so a character authored without
    # one is drawn with less to go on than a bundled one.
    charm: str = ""
    charm_ja: str = ""
    inner: list[str] = []
    inner_ja: list[str] = []
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
    slug: str | None = None
    title: str | None = None
    title_ja: str | None = None
    summary: str | None = None
    summary_ja: str | None = None
    charm: str | None = None
    charm_ja: str | None = None
    inner: list[str] | None = None
    inner_ja: list[str] | None = None
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
    # Model that decides what she is doing in the five frames and what her face
    # is doing in the portrait. Empty (or unreachable) falls back to the fixed
    # slots, which are a complete answer built from her tags.
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


class ResetRequest(BaseModel):
    # Also delete user-authored characters — the only way back to a roster that
    # is exactly the shipped one.
    wipe: bool = False
    # Report the same numbers and change nothing, so the confirm dialog can say
    # what is about to be deleted instead of asking the user to trust it.
    dry_run: bool = False


@router.post("/reset")
async def reset_characters(request: Request, body: ResetRequest | None = None):
    """Make the roster say what the shipped asset file says.

    The presets live in Qdrant and are seeded once, when the collection is
    empty, so editing the asset file and deploying changes nothing on a running
    install — `petite` stayed on 71 characters for a whole release after it was
    removed from the file. This is the only way to pick the edits up.

    It writes the bundled rows over in place, carrying their artwork across, and
    deletes bundled rows the file has stopped claiming. That second half is new:
    without it, renumbering the roster left the previous hundred characters in
    the collection with no id the UI knew and no way to remove them, and reset
    could only ever add to the pile.

    User-authored characters survive unless `wipe`.
    """
    opts = body or ResetRequest()
    result = await presets_db.reset_presets_to_defaults(
        request.app.state.db, vector_dim=settings.embed_dim,
        wipe=opts.wipe, dry_run=opts.dry_run,
    )
    logger.info("[characters] reset (%s): %s",
                "preview" if opts.dry_run else "applied", result)
    return result


class MuseSyncRequest(BaseModel):
    """Preview or apply versioned Muse asset updates without touching diaries."""
    dry_run: bool = False


@router.post("/sync-muse")
async def sync_muse_characters(request: Request, body: MuseSyncRequest | None = None):
    """Update bundled Muse rows when the asset ``version`` is newer than Qdrant.

    Unlike ``/reset``, this never deletes roster rows. Diaries, boards, and
    galleries on each point are preserved; only fields present in
    ``personality_presets.json`` are rewritten.
    """
    opts = body or MuseSyncRequest()
    result = await presets_db.sync_muse_presets_from_asset(
        request.app.state.db, vector_dim=settings.embed_dim, dry_run=opts.dry_run,
    )
    logger.info("[characters] muse sync (%s): %s",
                "preview" if opts.dry_run else "applied", result)
    return result


class EraseMemoryRequest(BaseModel):
    # Report the same numbers and change nothing, so the confirm dialog can
    # say what is about to be deleted instead of asking the user to trust it.
    dry_run: bool = False


@router.post("/erase-memory")
async def erase_character_memory(request: Request, body: EraseMemoryRequest | None = None):
    """記憶の消去 — wipe every character's accrued memory, all at once.

    Clears diaries / chemistry notes / lounge whispers from every character's
    payload, deletes every Muse session, every lounge thread, every
    auto-generated handpost page, and every chemistry vector (rebuildable).
    The character sheet, board (bust-up + reference sheet) and gallery photos
    are never touched — nothing here deletes an image.

    Physical deletes throughout, not a flag: `presets_db.update_preset`
    overwrites the payload fields in place, and the sibling collections use a
    real Qdrant point delete.
    """
    db = request.app.state.db
    opts = body or EraseMemoryRequest()
    # Muse owns sessions/lounge/handpost; imported here rather than at module
    # scope since this is the only characters/ endpoint that needs them.
    from ..muse import handpost_db, lounge_db, memories_db, session_db

    if opts.dry_run:
        plan = await presets_db.plan_memory_erase(db)
        result = {
            "dry_run": True,
            **plan,
            "sessions": await session_db.count_all(db),
            "lounge_threads": await lounge_db.count_all(db),
            "handpost_pages": await handpost_db.count_generated_pages(db),
            "compat_vectors": await compat_mod.count_all(db),
            "muse_memories": True,
        }
    else:
        await memories_db.purge_all(db)
        result = {
            "dry_run": False,
            "characters": await presets_db.erase_all_memory_fields(db),
            "sessions": await session_db.delete_all(db),
            "lounge_threads": await lounge_db.delete_all(db),
            "handpost_pages": await handpost_db.purge_generated_pages(db),
            "compat_vectors": await compat_mod.delete_all(db),
            "muse_memories": True,
        }
    logger.info("[characters] erase-memory (%s): %s",
                "preview" if opts.dry_run else "applied", result)
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


def _queue_compat_embed(request: Request, character_id: str) -> None:
    """(Re)compute her chemistry vectors — best-effort, never blocks the response."""
    from .compat import run_character_compat_embed
    spooler = getattr(request.app.state, "spooler", None)
    ollama = getattr(request.app.state, "ollama", None)
    if spooler is None or ollama is None or not character_id:
        return
    spooler.submit(
        JobLane.EMBEDDING, "character_compat_embed", run_character_compat_embed,
        meta={"character_id": character_id},
        db=request.app.state.db, ollama=ollama, character_id=character_id,
    )


@router.post("")
async def create_character(body: CharacterCreate, request: Request):
    row = await presets_db.create_preset(
        request.app.state.db,
        body.model_dump(),
        vector_dim=settings.embed_dim,
    )
    _queue_compat_embed(request, str(row.get("id") or ""))
    return row


@router.put("/{character_id}")
async def update_character(character_id: str, body: CharacterUpdate, request: Request):
    row = await presets_db.update_preset(
        request.app.state.db, character_id, body.model_dump(exclude_none=True),
    )
    if row is None:
        raise HTTPException(404, "character not found")
    # Only her appearance/personality fields actually move the vectors, but
    # re-embedding on every edit is cheap and "did this patch touch a relevant
    # field" is not worth tracking separately.
    _queue_compat_embed(request, character_id)
    return row


@router.delete("/{character_id}")
async def delete_character(character_id: str, request: Request):
    if not await presets_db.delete_preset(request.app.state.db, character_id):
        raise HTTPException(404, "character not found")
    return {"ok": True}


# ── her secret diary ────────────────────────────────────────────────────────
# Written by the Muse diary job when the Showrunner wraps a session; read here.
# The persistence already lived in `presets`, on the character's own payload —
# these two routes are what the panel was calling all along and never got.

@router.get("/{character_id}/diaries")
async def list_character_diaries(character_id: str, request: Request):
    """Newest first — the panel opens on the top entry."""
    if await presets_db.get_preset(request.app.state.db, character_id) is None:
        raise HTTPException(404, "character not found")
    diaries = await presets_db.get_preset_diaries(request.app.state.db, character_id)
    diaries.sort(key=lambda d: d.get("timestamp") or 0.0, reverse=True)
    return {"diaries": diaries}


@router.get("/{character_id}/diaries/by-image/{image_id}")
async def find_character_diary_by_image(character_id: str, image_id: str, request: Request):
    """Reverse lookup used by the Creation Record panel: does a diary entry
    already exist for this specific shot?"""
    diary = await presets_db.find_preset_diary_by_image(
        request.app.state.db, character_id, image_id,
    )
    if diary is None:
        raise HTTPException(404, "diary not found")
    return {"diary": diary}


@router.post("/{character_id}/diaries/{diary_id}/read")
async def read_character_diary(character_id: str, diary_id: str, request: Request):
    """Mark one entry read. No model runs here.

    Her reaction to having been read used to be generated on this call, which
    made turning a page cost a cold model load, and had her answer a click
    nobody had told her about. She brings it up herself at the top of the next
    session now (`muse.service` reads `get_unacknowledged_read_diaries`), so all
    this route does is remember that the page was turned.
    """
    diary = await presets_db.mark_diary_read(request.app.state.db, character_id, diary_id)
    if diary is None:
        raise HTTPException(404, "diary not found")
    # `banter` stays in the response, always empty, for panels still reading it.
    return {"diary": diary, "banter": ""}


@router.delete("/{character_id}/diaries/{diary_id}")
async def delete_character_diary(character_id: str, diary_id: str, request: Request):
    if not await presets_db.delete_preset_diary(request.app.state.db, character_id, diary_id):
        raise HTTPException(404, "diary not found")
    return {"ok": True}


class BoardImageChoice(BaseModel):
    slot: str = Field(..., min_length=1)
    sha256: str = Field(..., min_length=8)


class BulkBoardRequest(BaseModel):
    workflow_name: str = Field(..., min_length=1)
    slots: list[str] = []          # empty → every slot
    limit: int = Field(default=500, ge=1, le=1000)
    steps: int | None = None
    cfg: float | None = None


@router.post("/{character_id}/board-image")
async def choose_character_board_image(
    character_id: str, body: BoardImageChoice, request: Request,
):
    """Adopt one of her candidates as the image that slot shows.

    Every render is kept (`presets.GALLERY_LIMIT` per slot) with the checkpoint
    that drew it, so the same character can be drawn on two models and compared.
    """
    board = await presets_db.choose_board_image(
        request.app.state.db, character_id, body.slot, body.sha256,
    )
    if board is None:
        raise HTTPException(404, "character, slot or candidate not found")
    return {"ok": True, "board": board}


@router.post("/boards/missing")
async def render_missing_boards(body: BulkBoardRequest, request: Request):
    """Draw whatever each character is missing.

    A hundred characters shown as a hundred name labels is the list the gallery
    is meant to replace, so it is only worth having once they all have a picture.
    Both slots by default: the sheet is what says who she is — a centre pose and
    four moments from her life — and the portrait is the face that identifies her
    at a glance.

    Every job carries one `group_id`, so two hundred renders can be called off
    from one button.
    """
    db = request.app.state.db
    slots = [s for s in (body.slots or presets_db.BOARD_SLOTS)
             if s in presets_db.BOARD_SLOTS]
    if not slots:
        raise HTTPException(400, f"no valid slot in {body.slots}")

    rows = await presets_db.list_presets(db, limit=500)
    group_id = f"character_boards:{uuid4().hex[:8]}"

    queued: list[dict] = []
    for row in rows:
        if len(queued) >= body.limit:
            break
        board = row.get("board") or {}
        wanted = [s for s in slots if not board.get(s)]
        if not wanted:
            continue
        preset = await presets_db.get_preset(db, row["id"])
        if preset is None:
            continue
        # Both slots read the plan now — the portrait takes its face and its
        # scene from it — so asking only when the sheet is wanted left a
        # portrait-only re-render on the deterministic path for no reason.
        plan = await plan_sheet(preset, request.app.state.ollama) if wanted else None
        queued += await _queue_board_slots(
            request, preset, row["id"], wanted,
            workflow_name=body.workflow_name, plan=plan,
            steps=body.steps, cfg=body.cfg, group_id=group_id,
        )
    logger.info("[characters] queued %d board renders as %s", len(queued), group_id)
    return {"queued": len(queued), "group_id": group_id, "jobs": queued}


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

    # One small call, before any render: what she is doing in the five frames
    # and what her face is doing in the sixth, read off her personality rather
    # than picked from fixed slots.
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
    group_id: str = "",
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
            # The checkpoint is closed over rather than read off the render's
            # meta, which carries only the seed and the prompt id. Without it a
            # candidate cannot say which model drew it, and comparing two models
            # is the reason for keeping more than one.
            async def _inner(sha256: str, _meta: dict) -> None:
                await presets_db.attach_board_image(
                    db, character_id, slot_name, sha256, workflow=workflow_name,
                )
            return _inner

        job_id = spooler.submit(
            JobLane.GENERATION,
            f"character_board:{slot}",
            run_render,
            meta={"character_id": character_id, "slot": slot,
                  **({"group_id": group_id} if group_id else {})},
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

"""A LoRA comparison sheet: LoRA files down, strengths across, one seed.

Rows are LoRA files — epochs of one run, or anyone's LoRAs from ComfyUI's
loras folder. Columns are strengths; 0 is the model with no LoRA, the baseline
the rest are read against. Seed, prompt, models and sampling are the same for
every cell, so the only thing that differs is the LoRA and how strongly it is
applied. Each row carries its own trigger word, written in front of the prompt.

Cells that would draw the very same picture (same final prompt, no LoRA) are
drawn once: rows sharing a trigger share their 0 column. ``renders`` holds one
entry per distinct picture, keyed by what it is; the grid maps cells onto them.

The job draws what is not drawn yet and saves after every picture, so a sheet
stopped half way keeps what it has and "draw the rest" picks up from there.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from typing import Any

import httpx
from qdrant_client import models as qm

from ..db.qdrant_client import LORA_SHEETS_COLLECTION
from ..spooler.models import CancelToken, JobCancelled, ProgressReporter
from . import targets as target_models
from .sheet_graph import Models, Sampling, SheetGraphError

logger = logging.getLogger(__name__)

PENDING = "pending"
DONE = "done"
ERROR = "error"

DEFAULT_STRENGTHS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]
MAX_ROWS = 16
MAX_STRENGTHS = 10
MAX_RENDERS = 160
SUBDIR = "lora_sheet"


class SheetError(ValueError):
    """Settings a sheet cannot be made from."""


# ── Shape ─────────────────────────────────────────────────────────────────────

def final_prompt(prompt: str, trigger: str, trigger_on: bool = True) -> str:
    prompt, trigger = (prompt or "").strip(), (trigger or "").strip()
    if not trigger_on or not trigger:
        return prompt
    return f"{trigger}, {prompt}" if prompt else trigger


def columns(sheet: dict) -> list[dict]:
    """The strengths, then the optional "full strength, no trigger" column."""
    cols = [{"strength": s, "trigger_on": True} for s in sheet["strengths"]]
    if sheet.get("no_trigger_column"):
        cols.append({"strength": 1.0, "trigger_on": False})
    return cols


def render_key(prompt: str, lora: str, strength: float) -> str:
    if not strength:
        lora = ""  # strength 0 is the bare model whatever the row
    raw = json.dumps([prompt, lora, round(float(strength), 4)], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _spec(sheet: dict, row: dict, col: dict) -> dict:
    prompt = final_prompt(sheet["prompt"], row.get("trigger", ""), col["trigger_on"])
    strength = float(col["strength"])
    lora = row["lora"] if strength else ""
    return {"key": render_key(prompt, lora, strength), "prompt": prompt, "lora": lora,
            "strength": strength, "trigger_on": col["trigger_on"]}


def grid(sheet: dict) -> list[list[str]]:
    """Render keys, one list per row, one key per column."""
    cols = columns(sheet)
    return [[_spec(sheet, row, col)["key"] for col in cols] for row in sheet["rows"]]


def sync_renders(sheet: dict) -> list[dict]:
    """Make ``renders`` match the grid. Returns the specs in drawing order:
    the baseline column first (it is shared), then row by row, so one LoRA
    stays loaded across its strengths."""
    cols = columns(sheet)
    specs: dict[str, dict] = {}
    order: list[str] = []
    for col in cols:  # strength 0 first
        if not col["strength"]:
            for row in sheet["rows"]:
                sp = _spec(sheet, row, col)
                if sp["key"] not in specs:
                    specs[sp["key"]] = sp
                    order.append(sp["key"])
    for row in sheet["rows"]:
        for col in cols:
            sp = _spec(sheet, row, col)
            if sp["key"] not in specs:
                specs[sp["key"]] = sp
                order.append(sp["key"])
    old = sheet.get("renders") or {}
    sheet["renders"] = {
        k: old.get(k) or {**specs[k], "sha256": "", "status": PENDING, "error": "", "seconds": None}
        for k in order
    }
    return [specs[k] for k in order]


def next_pending(sheet: dict) -> dict | None:
    return next((r for r in sheet["renders"].values() if r["status"] == PENDING), None)


def counts(sheet: dict) -> dict[str, int]:
    c = {PENDING: 0, DONE: 0, ERROR: 0}
    for r in sheet["renders"].values():
        c[r["status"]] = c.get(r["status"], 0) + 1
    return c


def _clean_rows(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows or []:
        lora = str(r.get("lora") or "").strip()
        trigger = str(r.get("trigger") or "").strip()
        if not lora or (lora, trigger) in seen:
            continue
        seen.add((lora, trigger))
        out.append({"lora": lora, "trigger": trigger,
                    "label": str(r.get("label") or "").strip()})
    return out


def _clean_strengths(values: list[float] | None) -> list[float]:
    out: list[float] = []
    for v in values if values is not None else DEFAULT_STRENGTHS:
        v = round(float(v), 4)
        if not -2.0 <= v <= 3.0:
            raise SheetError(f"strength out of range (-2 to 3): {v}")
        if v not in out:
            out.append(v)
    return out


MODEL_NAME_CHARS = 30


def file_stem(path: str) -> str:
    """``Krea2/flt_style-000010.safetensors`` -> ``flt_style-000010``."""
    name = (path or "").rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def short_model(path: str, limit: int = MODEL_NAME_CHARS) -> str:
    stem = file_stem(path)
    return stem if len(stem) <= limit else stem[: limit - 1] + "…"


def _default_name(rows: list[dict], unet: str = "") -> str:
    """'<LoRA> · <base model>'. The LoRA is its folder when several rows are
    epochs in one folder; one file (even inside a shared folder such as
    ``Krea2/``) is named by itself, so sheets do not all come out 'Krea2'."""
    first = rows[0]["lora"]
    folder = first.rsplit("/", 1)[0] if "/" in first else ""
    if len(rows) > 1 and folder and all(r["lora"].startswith(folder + "/") for r in rows):
        lora = folder.rsplit("/", 1)[-1]
    else:
        lora = file_stem(first)
        if len(rows) > 1:
            lora += f" +{len(rows) - 1}"
    return f"{lora} · {short_model(unet)}" if unet else lora


def rename(sheet: dict, name: str) -> dict:
    name = str(name or "").strip()
    if not name:
        raise SheetError("the name is empty")
    sheet["name"] = name[:120]
    return sheet


def new_sheet(body: dict[str, Any]) -> dict:
    """A sheet from the screen's settings. Raises SheetError when they do not make one."""
    target = target_models.get(body.get("target_model"))
    if target.build_graph is None:
        raise SheetError(f"sheets are not available for {target.label}")
    d = target.sheet_defaults
    rows = _clean_rows(body.get("rows") or [])
    if not rows:
        raise SheetError("add at least one LoRA")
    if len(rows) > MAX_ROWS:
        raise SheetError(f"at most {MAX_ROWS} LoRAs on one sheet")
    strengths = _clean_strengths(body.get("strengths"))
    if not strengths or len(strengths) > MAX_STRENGTHS:
        raise SheetError(f"1 to {MAX_STRENGTHS} strengths")
    seed = body.get("seed")
    seed = random.randint(0, (1 << 50) - 1) if seed in (None, "") else int(seed)
    if seed < 0:
        raise SheetError("seed must not be negative")
    models = body.get("models") or {}
    now = time.time()
    sheet = {
        "id": str(uuid.uuid4()),
        "name": str(body.get("name") or "").strip() or _default_name(rows, str(models.get("unet") or "")),
        "target_model": target.id,
        "models": {k: str(models.get(k) or "").strip() for k in ("unet", "clip", "vae")},
        "prompt": str(body.get("prompt") or "").strip(),
        "seed": seed,
        "width": int(body.get("width") or d.get("width", 1024)),
        "height": int(body.get("height") or d.get("height", 1024)),
        "steps": int(body.get("steps") or d.get("steps", 8)),
        "cfg": float(body.get("cfg") if body.get("cfg") not in (None, "") else d.get("cfg", 1.0)),
        "sampler": str(body.get("sampler") or d.get("sampler", "euler")),
        "scheduler": str(body.get("scheduler") or d.get("scheduler", "simple")),
        "rows": rows,
        "strengths": strengths,
        "no_trigger_column": bool(body.get("no_trigger_column")),
        "bucket_id": str(body.get("bucket_id") or ""),
        "renders": {},
        "created_at": now,
        "updated_at": now,
    }
    if not sheet["prompt"]:
        raise SheetError("the prompt is empty")
    specs = sync_renders(sheet)
    if len(specs) > MAX_RENDERS:
        raise SheetError(f"{len(specs)} pictures is too many for one sheet (max {MAX_RENDERS})")
    # Fail now, not in the job, when the graph cannot be built.
    try:
        build(sheet, specs[0])
    except SheetGraphError as exc:
        raise SheetError(str(exc)) from exc
    return sheet


def duplicate(sheet: dict, *, seed: int | None = None, name: str = "") -> dict:
    body = {k: sheet[k] for k in (
        "target_model", "models", "prompt", "width", "height", "steps", "cfg", "sampler",
        "scheduler", "rows", "strengths", "no_trigger_column", "bucket_id")}
    body["seed"] = seed
    body["name"] = name or sheet["name"]
    return new_sheet(body)


def build(sheet: dict, spec: dict) -> dict:
    """The ComfyUI graph for one picture of the sheet."""
    target = target_models.get(sheet["target_model"])
    m = sheet["models"]
    return target.build_graph(
        models=Models(unet=m["unet"], clip=m["clip"], vae=m["vae"]),
        sampling=Sampling(seed=sheet["seed"], width=sheet["width"], height=sheet["height"],
                          steps=sheet["steps"], cfg=sheet["cfg"], sampler=sheet["sampler"],
                          scheduler=sheet["scheduler"]),
        prompt=spec["prompt"], lora=spec["lora"] or None, strength=spec["strength"],
    )


def request_rest(sheet: dict) -> int:
    """"Draw the rest": failed pictures go back in line with the undrawn ones."""
    n = 0
    for r in sheet["renders"].values():
        if r["status"] == ERROR:
            r["status"], r["error"] = PENDING, ""
        if r["status"] == PENDING:
            n += 1
    return n


def summary(sheet: dict) -> dict:
    c = counts(sheet)
    first = next((r["sha256"] for r in sheet["renders"].values() if r["sha256"]), "")
    return {k: sheet.get(k) for k in (
        "id", "name", "target_model", "seed", "created_at", "updated_at", "bucket_id")} | {
        "rows": len(sheet["rows"]), "columns": len(columns(sheet)),
        "pictures": len(sheet["renders"]), "done": c[DONE], "pending": c[PENDING],
        "errors": c[ERROR], "cover": first,
        # for the list's search and grouping
        "unet": short_model(sheet["models"].get("unet", "")),
        "loras": sorted({file_stem(r["lora"]) for r in sheet["rows"]}),
        "prompt": (sheet.get("prompt") or "")[:80],
        "strengths": sheet["strengths"],
    }


def seconds_per_picture(sheets: list[dict]) -> float | None:
    """Median time one picture took on recent sheets — for the estimate."""
    times = sorted(
        r["seconds"] for s in sheets for r in s.get("renders", {}).values()
        if r.get("status") == DONE and r.get("seconds")
    )[-200:]
    if not times:
        return None
    return round(times[len(times) // 2], 1)


# ── Store ─────────────────────────────────────────────────────────────────────

_locks: dict[str, asyncio.Lock] = {}


def lock(sheet_id: str) -> asyncio.Lock:
    return _locks.setdefault(sheet_id, asyncio.Lock())


async def load(db, sheet_id: str) -> dict | None:
    try:
        points = await db._qc.retrieve(
            collection_name=LORA_SHEETS_COLLECTION, ids=[sheet_id], with_payload=True,
        )
    except Exception:
        return None
    return dict(points[0].payload or {}) if points else None


async def save(db, sheet: dict) -> dict:
    sheet["updated_at"] = time.time()
    await db._qc.upsert(
        collection_name=LORA_SHEETS_COLLECTION,
        points=[qm.PointStruct(id=sheet["id"], vector={}, payload=sheet)],
    )
    return sheet


async def list_all(db) -> list[dict]:
    rows: list[dict] = []
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=LORA_SHEETS_COLLECTION, limit=128, offset=offset, with_payload=True,
        )
        rows.extend(dict(p.payload or {}) for p in points)
        if offset is None or not points:
            break
    rows.sort(key=lambda s: s.get("created_at") or 0.0, reverse=True)
    return rows


async def delete(db, sheet_id: str) -> None:
    await db._qc.delete(
        collection_name=LORA_SHEETS_COLLECTION,
        points_selector=qm.PointIdsList(points=[sheet_id]),
    )
    _locks.pop(sheet_id, None)


# ── Job ───────────────────────────────────────────────────────────────────────

#: Sheets with a drawing job queued or running.
#: sheet id -> (job id, spooler) of the job drawing it.
_active: dict[str, tuple[str | None, Any]] = {}
#: The newest in-flight frame per sheet being drawn: {"key", "jpeg", "seq"}.
#: Memory only — a frame is worthless once the picture lands.
_previews: dict[str, dict] = {}


def is_drawing(sheet_id: str) -> bool:
    return active_job(sheet_id) is not None or (sheet_id in _active)


def active_job(sheet_id: str) -> str | None:
    """The job drawing this sheet. A job cancelled while still queued never
    runs, so nothing would clear it; one the spooler no longer holds as active
    is dropped here instead of leaving the sheet 'drawing' for good."""
    entry = _active.get(sheet_id)
    if entry is None:
        return None
    job_id, spooler = entry
    registry = getattr(spooler, "_registry", None)
    if job_id and isinstance(registry, dict) and job_id not in registry:
        _active.pop(sheet_id, None)
        _previews.pop(sheet_id, None)
        return None
    return job_id


def latest_preview(sheet_id: str) -> dict | None:
    return _previews.get(sheet_id)


class _StepReporter:
    """Hands run_render's per-step progress on as the sheet's: the job line
    reads 'pictures done/total · step v/m', and the cell being drawn keeps its
    step for the sheet screen."""

    def __init__(self, reporter, sheet_id: str, done: int, total: int):
        self._r, self._id, self._done, self._total = reporter, sheet_id, done, total

    def update(self, progress, text=None, *, key="", **params):
        if key != "step":
            return self._r.update(progress, text, key=key, **params)
        v, m = int(params.get("v", 0)), max(int(params.get("m", 1)), 1)
        live = _previews.get(self._id)
        if live is not None:
            live["step"], live["steps"] = v, m
        overall = (self._done + v / m) / self._total if self._total else 0.0
        self._r.update(overall, f"{self._done}/{self._total} · Step {v}/{m}", key="imagesStep",
                       done=self._done, total=self._total, v=v, m=m)

    def indeterminate(self):
        self._r.indeterminate()


def _preview_sink(sheet_id: str, key: str):
    async def _publish(jpeg: bytes) -> None:
        prev = _previews.get(sheet_id) or {}
        _previews[sheet_id] = prev | {"key": key, "jpeg": jpeg, "seq": prev.get("seq", 0) + 1}
    return _publish


def submit(spooler, *, db, comfy, sheet: dict) -> str | None:
    """Queue the drawing job unless one is already on this sheet."""
    from ..spooler.models import JobLane

    if is_drawing(sheet["id"]) or not counts(sheet)[PENDING]:
        return None
    _active[sheet["id"]] = (None, spooler)
    try:
        job_id = spooler.submit(
            JobLane.GENERATION, "lora_sheet", run_lora_sheet,
            meta={"sheet_id": sheet["id"], "sheet_name": sheet["name"]},
            db=db, comfy=comfy, sheet_id=sheet["id"],
        )
    except Exception:
        _active.pop(sheet["id"], None)
        raise
    if sheet["id"] in _active:  # an instant job may already have finished
        _active[sheet["id"]] = (job_id, spooler)
    return job_id


async def run_lora_sheet(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    comfy,
    sheet_id: str,
) -> dict:
    """Draw every picture of the sheet not drawn yet, saving after each."""
    from ..jobs.render import run_render

    drawn = errors = 0
    try:
        while True:
            cancel.raise_if_set()
            await cancel.pause_checkpoint()
            async with lock(sheet_id):
                sheet = await load(db, sheet_id)
                spec = next_pending(sheet) if sheet else None
                if spec is None:
                    break
                c = counts(sheet)
                total, done_before = len(sheet["renders"]), c[DONE] + c[ERROR]
            reporter.update(done_before / total if total else 0.0,
                            f"{done_before}/{total}", key="images", done=done_before, total=total)

            started = time.monotonic()
            _previews[sheet_id] = {"key": spec["key"], "jpeg": b"",
                                   "seq": (_previews.get(sheet_id) or {}).get("seq", 0) + 1}
            try:
                graph = build(sheet, spec)
                result = await run_render(
                    _StepReporter(reporter, sheet_id, done_before, total), cancel, db=db, comfy=comfy,
                    workflow_name=f"lora_sheet:{sheet['target_model']}", workflow=graph,
                    positive=spec["prompt"], seed=sheet["seed"],
                    width=sheet["width"], height=sheet["height"],
                    steps=sheet["steps"], cfg=sheet["cfg"],
                    subdir=SUBDIR, prefix="lora_sheet", method="lora_sheet",
                    preview=_preview_sink(sheet_id, spec["key"]),
                    payload_extra={"lora_sheet": {
                        "sheet_id": sheet_id, "lora": spec["lora"], "strength": spec["strength"],
                        "trigger_on": spec["trigger_on"],
                    }},
                )
                sha = (result.get("sha256s") or [""])[0]
                if not sha:
                    raise RuntimeError("ComfyUI returned no picture")
                outcome = {"sha256": sha, "status": DONE, "error": "",
                           "seconds": round(time.monotonic() - started, 1)}
                drawn += 1
            except JobCancelled:
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError):
                # ComfyUI is gone: every other picture would fail the same way.
                # Leave them waiting and let the job fail with the reason.
                raise
            except Exception as exc:
                errors += 1
                logger.warning("[lora_sheet] %s %s: %s", sheet_id[:8], spec["key"], exc)
                outcome = {"status": ERROR, "error": str(exc) or exc.__class__.__name__}

            async with lock(sheet_id):
                sheet = await load(db, sheet_id)
                if sheet is None:
                    break  # deleted meanwhile
                r = sheet["renders"].get(spec["key"])
                if r is not None:
                    r.update(outcome)
                    await save(db, sheet)
    finally:
        _active.pop(sheet_id, None)
        _previews.pop(sheet_id, None)

    reporter.update(1.0, f"{drawn}", key="imagesGenerated", n=drawn)
    return {"sheet_id": sheet_id, "drawn": drawn, "errors": errors}

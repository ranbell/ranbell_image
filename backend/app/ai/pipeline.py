import asyncio
import logging
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..api.images import invalidate_image_caches
from ..db.qdrant_client import QdrantDBClient, IMAGES_COLLECTION as _IMAGES_COLLECTION, PENDING_FILTER as _PENDING_FILTER
from ..runtime_config import get_runtime_config
from .ollama import OllamaClient
from . import wd14 as wd14_mod
from .color_extractor import extract_color_palette


async def _run_with_sem(sem: asyncio.Semaphore, fn, *args) -> None:
    async with sem:
        await fn(*args)

logger = logging.getLogger(__name__)


class PipelineState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    running: bool = False
    cancelled: bool = False
    total: int = 0
    processed: int = 0
    errors: int = 0
    active_wd14: int = 0
    active_embed: int = 0
    active_save: int = 0
    last_error: str | None = None
    mode: str | None = None
    start_time: float | None = None
    eta_seconds: int | None = None

    def reset(self, mode: str) -> None:
        self.running = True
        self.cancelled = False
        self.total = 0
        self.processed = 0
        self.errors = 0
        self.active_wd14 = 0
        self.active_embed = 0
        self.active_save = 0
        self.last_error = None
        self.mode = mode
        self.start_time = time.monotonic()
        self.eta_seconds = None

    def finish(self) -> None:
        self.running = False
        self.active_wd14 = 0
        self.active_embed = 0
        self.active_save = 0
        self.eta_seconds = None
        self.start_time = None

    def update_eta(self) -> None:
        if not self.start_time or self.processed == 0 or self.total == 0:
            self.eta_seconds = None
            return
        elapsed = time.monotonic() - self.start_time
        rate = self.processed / elapsed
        remaining = self.total - self.processed
        self.eta_seconds = round(remaining / rate) if rate > 0 else None


pipeline_state = PipelineState()
# Separate state for the CPU-only tagging stage so it can run concurrently
# with an embed-stage job (TAGGING lane vs EMBEDDING lane).
tagging_state = PipelineState()


async def run_ai_pipeline(
    db: QdrantDBClient,
    ollama: OllamaClient,
    sha256s: list[str] | None = None,
    pause_checkpoint=None,
    stage: str = "full",
) -> None:
    """Process pending docs.

    stage:
      "full"    — WD14 + colors + embed + UMAP per doc (invoke finalize, legacy path)
      "tagging" — CPU only: WD14 + colors; embedding_status stays pending
      "embed"   — embed + UMAP, reusing tags written by the tagging stage
                  (falls back to the full path per doc when tags are missing)
    """
    state = tagging_state if stage == "tagging" else pipeline_state
    if state.running:
        return

    mode = ("selected" if sha256s else "all_pending") if stage == "full" else f"{stage}:{'selected' if sha256s else 'all_pending'}"
    state.reset(mode)

    try:
        cfg = await get_runtime_config(db)
        threshold = float(cfg["wd14_threshold"])
        wd14_model_dir = cfg.get("wd14_model_dir") or None
        embed_model = cfg["embed_model"] or None
        concurrency = int(cfg.get("pipeline_concurrency", 4))

        async def _process_one(doc: dict) -> None:
            if state.cancelled:
                return
            if pause_checkpoint:
                await pause_checkpoint()
            try:
                if stage == "tagging":
                    await _tag_doc(doc, db, threshold, wd14_model_dir, state)
                elif stage == "embed":
                    await _embed_doc(doc, db, ollama, threshold, embed_model, wd14_model_dir, state)
                else:
                    await _process_doc(doc, db, ollama, threshold, embed_model, wd14_model_dir, state)
                state.processed += 1
                state.update_eta()
            except Exception as e:
                state.errors += 1
                state.last_error = f"{type(e).__name__}: {e}"
                logger.exception("Pipeline error for %s", doc.get("sha256"))

        if sha256s:
            docs = await db.get_by_sha256s(sha256s)
            state.total = len(docs)
            logger.info("AI pipeline [%s]: %d docs, concurrency=%d", mode, len(docs), concurrency)
            sem = asyncio.Semaphore(concurrency)
            await asyncio.gather(*(
                _run_with_sem(sem, _process_one, doc) for doc in docs
            ))
        else:
            # producer/consumer: stream pending docs through a bounded queue
            # to avoid loading all pending docs into memory at once
            state.total = await db.count_pending()
            logger.info(
                "AI pipeline [%s]: ~%d docs, concurrency=%d",
                mode, state.total, concurrency,
            )

            queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=concurrency * 4)

            async def _producer() -> None:
                offset = None
                while True:
                    points, next_offset = await db._qc.scroll(
                        collection_name=_IMAGES_COLLECTION,
                        scroll_filter=_PENDING_FILTER,
                        limit=min(200, concurrency * 8),
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    for p in points:
                        await queue.put(p.payload)
                    if next_offset is None:
                        break
                    offset = next_offset
                for _ in range(concurrency):
                    await queue.put(None)

            async def _worker() -> None:
                while True:
                    doc = await queue.get()
                    if doc is None:
                        break
                    await _process_one(doc)

            await asyncio.gather(
                _producer(),
                *[_worker() for _ in range(concurrency)],
            )

    finally:
        state.finish()
        invalidate_image_caches()
        logger.info(
            "AI pipeline [%s] done: %d processed, %d errors, cancelled=%s",
            mode, state.processed, state.errors, state.cancelled,
        )



def _build_embed_text(doc: dict, wd14_tags: list[str]) -> str:
    parts = []
    prompt = doc.get("positive_prompt", "")
    if prompt:
        if isinstance(prompt, list):
            text = ", ".join(x for x in prompt if isinstance(x, str))
            if text:
                parts.append(text)
        else:
            parts.append(str(prompt))
    if wd14_tags:
        parts.append(", ".join(wd14_tags))
    if doc.get("name"):
        parts.append(str(doc["name"]))
    return " ".join(parts)


async def _tag_doc(
    doc: dict,
    db: QdrantDBClient,
    threshold: float,
    wd14_model_dir: str | None = None,
    state: PipelineState = tagging_state,
) -> None:
    """CPU stage: WD14 tags + color palette. Leaves embedding_status pending."""
    sha256 = doc.get("sha256")
    if not sha256:
        return
    if doc.get("wd14_tags"):
        return  # already tagged (embed stage or a previous run got here first)
    file_path = Path(doc.get("path", ""))
    if not file_path.exists():
        return

    state.active_wd14 += 1
    try:
        scored = await wd14_mod.predict_tags_scored(file_path, threshold, wd14_model_dir)
    finally:
        state.active_wd14 -= 1

    color_data = await asyncio.get_event_loop().run_in_executor(
        None, extract_color_palette, file_path
    )

    state.active_save += 1
    try:
        payload: dict = {
            "wd14_tags": [
                str(tag).strip().replace(" ", "_") for tag, _ in scored
            ],
            "wd14_tags_scores": [round(score, 4) for _, score in scored],
        }
        color_lab: list[float] | None = None
        if color_data:
            payload.update(color_data)
            color_lab = color_data.get("color_lab")
        await db.set_payload(sha256, payload)
        if color_lab and db.has_color_vector:
            await db.set_color_vector(sha256, color_lab)
    finally:
        state.active_save -= 1


async def _embed_doc(
    doc: dict,
    db: QdrantDBClient,
    ollama: OllamaClient,
    threshold: float,
    embed_model: str | None = None,
    wd14_model_dir: str | None = None,
    state: PipelineState = pipeline_state,
) -> None:
    """GPU stage: embed + UMAP, reusing tags from the tagging stage.

    Re-fetches the doc so tags written after our scroll snapshot are seen;
    falls back to the full path when the doc was never tagged.
    """
    sha256 = doc.get("sha256")
    if not sha256:
        return

    fresh = await db.get(sha256) or doc
    wd14_tags = fresh.get("wd14_tags") or []
    if not wd14_tags:
        await _process_doc(fresh, db, ollama, threshold, embed_model, wd14_model_dir, state)
        return

    state.active_embed += 1
    try:
        embedding = await ollama.embed(_build_embed_text(fresh, wd14_tags), model=embed_model)
    finally:
        state.active_embed -= 1

    from .umap_reducer import umap_has_model, umap_transform_one_sync
    umap_xy: tuple[float, float] | None = None
    if umap_has_model():
        loop = asyncio.get_event_loop()
        umap_xy = await loop.run_in_executor(None, umap_transform_one_sync, embedding)

    state.active_save += 1
    try:
        await db.set_embedding(sha256, embedding)
        payload: dict = {"embedding_status": "done"}
        if umap_xy is not None:
            payload["umap_x"] = umap_xy[0]
            payload["umap_y"] = umap_xy[1]
        await db.set_payload(sha256, payload)
    finally:
        state.active_save -= 1


async def _process_doc(
    doc: dict,
    db: QdrantDBClient,
    ollama: OllamaClient,
    threshold: float,
    embed_model: str | None = None,
    wd14_model_dir: str | None = None,
    state: PipelineState = pipeline_state,
) -> None:
    sha256 = doc.get("sha256")
    if not sha256:
        return
    file_path = Path(doc.get("path", ""))
    if not file_path.exists():
        return

    state.active_wd14 += 1
    try:
        scored = await wd14_mod.predict_tags_scored(file_path, threshold, wd14_model_dir)
    finally:
        state.active_wd14 -= 1
    wd14_tags = [str(tag).strip().replace(" ", "_") for tag, _ in scored]
    wd14_tags_scores = [round(score, 4) for _, score in scored]

    embed_text = _build_embed_text(doc, wd14_tags)

    state.active_embed += 1
    try:
        embedding = await ollama.embed(embed_text, model=embed_model)
    finally:
        state.active_embed -= 1

    color_data = await asyncio.get_event_loop().run_in_executor(
        None, extract_color_palette, file_path
    )

    # UMAP transform (only if a model exists)
    from .umap_reducer import umap_has_model, umap_transform_one_sync
    umap_xy: tuple[float, float] | None = None
    if umap_has_model():
        loop = asyncio.get_event_loop()
        umap_xy = await loop.run_in_executor(None, umap_transform_one_sync, embedding)

    state.active_save += 1
    try:
        await db.set_embedding(sha256, embedding)
        payload: dict = {
            "wd14_tags": wd14_tags,
            "wd14_tags_scores": wd14_tags_scores,
            "embedding_status": "done",
        }
        color_lab: list[float] | None = None
        if color_data:
            payload.update(color_data)
            color_lab = color_data.get("color_lab")
        if umap_xy is not None:
            payload["umap_x"] = umap_xy[0]
            payload["umap_y"] = umap_xy[1]
        await db.set_payload(sha256, payload)
        if color_lab and db.has_color_vector:
            await db.set_color_vector(sha256, color_lab)
    finally:
        state.active_save -= 1

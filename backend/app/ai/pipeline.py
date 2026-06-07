import asyncio
import logging
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..db.qdrant_client import QdrantDBClient
from ..runtime_config import get_runtime_config
from .ollama import OllamaClient
from . import wd14 as wd14_mod
from .color_extractor import extract_color_palette

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


async def run_ai_pipeline(
    db: QdrantDBClient,
    ollama: OllamaClient,
    sha256s: list[str] | None = None,
    pause_checkpoint=None,
) -> None:
    if pipeline_state.running:
        return

    mode = "selected" if sha256s else "all_pending"
    pipeline_state.reset(mode)

    try:
        cfg = await get_runtime_config(db)
        threshold = float(cfg["wd14_threshold"])
        wd14_model_dir = cfg.get("wd14_model_dir") or None
        embed_model = cfg["embed_model"] or None
        concurrency = int(cfg.get("pipeline_concurrency", 4))

        if sha256s:
            docs = [d for d in [await db.get(s) for s in sha256s] if d]
        else:
            docs = await _fetch_pending(db, cfg["pipeline_batch_size"])

        pipeline_state.total = len(docs)
        logger.info("AI pipeline [%s]: %d docs, concurrency=%d", mode, len(docs), concurrency)

        sem = asyncio.Semaphore(concurrency)

        async def process_one(doc: dict) -> None:
            async with sem:
                if pipeline_state.cancelled:
                    return
                if pause_checkpoint:
                    await pause_checkpoint()
                try:
                    await _process_doc(doc, db, ollama, threshold, embed_model, wd14_model_dir)
                    pipeline_state.processed += 1
                    pipeline_state.update_eta()
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    logger.exception("Pipeline error for %s", doc.get("sha256"))
                    pipeline_state.errors += 1
                    pipeline_state.last_error = err_msg

        await asyncio.gather(*(process_one(doc) for doc in docs))

    finally:
        pipeline_state.finish()
        logger.info(
            "AI pipeline done: %d processed, %d errors, cancelled=%s",
            pipeline_state.processed,
            pipeline_state.errors,
            pipeline_state.cancelled,
        )


async def _fetch_pending(db: QdrantDBClient, limit: int) -> list[dict]:
    from ..db.qdrant_client import IMAGES_COLLECTION
    from qdrant_client import models as qm

    docs: list[dict] = []
    offset = None
    while len(docs) < limit:
        points, next_offset = await db._qc.scroll(
            collection_name=IMAGES_COLLECTION,
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="pending"))
            ]),
            limit=min(1000, limit - len(docs)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        docs.extend(p.payload for p in points)
        if next_offset is None:
            break
        offset = next_offset
    return docs


async def _process_doc(
    doc: dict,
    db: QdrantDBClient,
    ollama: OllamaClient,
    threshold: float,
    embed_model: str | None = None,
    wd14_model_dir: str | None = None,
) -> None:
    sha256 = doc.get("sha256")
    if not sha256:
        return
    file_path = Path(doc.get("path", ""))
    if not file_path.exists():
        return

    pipeline_state.active_wd14 += 1
    try:
        scored = await wd14_mod.predict_tags_scored(file_path, threshold, wd14_model_dir)
    finally:
        pipeline_state.active_wd14 -= 1
    wd14_tags = [tag for tag, _ in scored]
    wd14_tags_scores = [round(score, 4) for _, score in scored]

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
    embed_text = " ".join(parts)

    pipeline_state.active_embed += 1
    try:
        embedding = await ollama.embed(embed_text, model=embed_model)
    finally:
        pipeline_state.active_embed -= 1

    color_data = await asyncio.get_event_loop().run_in_executor(
        None, extract_color_palette, file_path
    )

    # UMAP transform (only if a model exists)
    from .umap_reducer import umap_has_model, umap_transform_one_sync
    umap_xy: tuple[float, float] | None = None
    if umap_has_model():
        loop = asyncio.get_event_loop()
        umap_xy = await loop.run_in_executor(None, umap_transform_one_sync, embedding)

    pipeline_state.active_save += 1
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
        pipeline_state.active_save -= 1

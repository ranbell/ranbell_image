from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from qdrant_client import models as qm

from ..ai.ollama import OllamaClient
from ..db.qdrant_client import QdrantDBClient, IMAGES_COLLECTION
from .bm25_matcher import compute_bm25_match, detect_prompt_style
from .embedding import compute_alignment_score
from .vlm_analyzer import analyze_with_llm
from .schema import AlignmentRecord, AlignmentResult

logger = logging.getLogger(__name__)

_MIN_PROMPT_LEN = 5


def _should_skip(doc: dict) -> bool:
    prompt = (doc.get("positive_prompt") or "").strip()
    tags = doc.get("wd14_tags") or []
    return not prompt or len(prompt) < _MIN_PROMPT_LEN or not tags


class AlignmentEvaluator:
    def __init__(self, db: QdrantDBClient, ollama: OllamaClient) -> None:
        self._db = db
        self._ollama = ollama

    async def evaluate_one(self, sha256: str) -> AlignmentResult:
        doc = await self._db.get(sha256)
        if not doc:
            return AlignmentResult(sha256=sha256, status="error", summary="Image not found")

        # Preserve any existing i18n translations so re-evaluation doesn't wipe them.
        existing = await self._db.get_alignment(sha256) or {}
        existing_i18n = {
            "summary_i18n":             existing.get("summary_i18n", {}),
            "matched_elements_i18n":    existing.get("matched_elements_i18n", {}),
            "unmatched_elements_i18n":  existing.get("unmatched_elements_i18n", {}),
        }

        if _should_skip(doc):
            record = AlignmentRecord(
                image_id=sha256,
                status="skipped",
                summary="Skipped: missing or too-short prompt, or empty tags",
                **existing_i18n,
            )
            await self._db.upsert_alignment(sha256, record.model_dump())
            return AlignmentResult(
                sha256=sha256, status="skipped", evaluated_at=record.evaluated_at
            )

        positive_prompt = doc["positive_prompt"].strip()
        tags: list[str] = doc.get("wd14_tags") or []
        tags_text = ", ".join(tags)

        prompt_style = detect_prompt_style(positive_prompt)
        bm25_rate, bm25_matched, bm25_unmatched = compute_bm25_match(positive_prompt, tags)

        try:
            embedding_score = await compute_alignment_score(positive_prompt, tags_text, self._ollama)
        except Exception as exc:
            logger.error("Embedding failure for %s: %s", sha256, exc)
            record = AlignmentRecord(
                image_id=sha256,
                score=None,
                bm25_score=bm25_rate,
                prompt_style=prompt_style,
                status="error",
                summary=f"Embedding error: {exc}",
                **existing_i18n,
            )
            await self._db.upsert_alignment(sha256, record.model_dump())
            return AlignmentResult(
                sha256=sha256, status="error", evaluated_at=record.evaluated_at
            )

        # Blend embedding (semantic) and BM25 (lexical) scores.
        # danbooru-style prompts map 1:1 to tags → BM25 is more reliable there.
        w_emb = 0.4 if prompt_style == "danbooru" else 0.7
        score = round(w_emb * embedding_score + (1.0 - w_emb) * bm25_rate, 4)

        try:
            analysis = await analyze_with_llm(
                positive_prompt,
                tags,
                score,
                self._ollama,
                bm25_matched=bm25_matched,
                bm25_unmatched=bm25_unmatched,
                prompt_style=prompt_style,
            )
        except Exception as exc:
            logger.error("LLM analysis failure for %s: %s", sha256, exc)
            analysis = {
                "summary": f"LLM error: {exc}",
                "matched_elements": [],
                "unmatched_elements": [],
                "categories": [],
            }

        record = AlignmentRecord(
            image_id=sha256,
            score=score,
            bm25_score=bm25_rate,
            prompt_style=prompt_style,
            status="done",
            summary=analysis.get("summary", ""),
            matched_elements=analysis.get("matched_elements", []),
            unmatched_elements=analysis.get("unmatched_elements", []),
            categories=analysis.get("categories", []),
            # LLM output takes priority; fall back to cached translations on re-evaluation.
            summary_i18n=analysis.get("summary_i18n") or existing_i18n["summary_i18n"],
            matched_elements_i18n=analysis.get("matched_elements_i18n") or existing_i18n["matched_elements_i18n"],
            unmatched_elements_i18n=analysis.get("unmatched_elements_i18n") or existing_i18n["unmatched_elements_i18n"],
        )
        await self._db.upsert_alignment(sha256, record.model_dump())

        return AlignmentResult(
            sha256=sha256,
            status="done",
            score=score,
            bm25_score=bm25_rate,
            prompt_style=prompt_style,
            summary=record.summary,
            matched_elements=record.matched_elements,
            unmatched_elements=record.unmatched_elements,
            categories=record.categories,
            summary_i18n=record.summary_i18n,
            matched_elements_i18n=record.matched_elements_i18n,
            unmatched_elements_i18n=record.unmatched_elements_i18n,
            evaluated_at=record.evaluated_at,
        )

    async def evaluate_batch(
        self,
        sha256s: list[str] | None,
        cancel_fn: Callable[[], bool] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        concurrency: int = 1,
        pause_checkpoint=None,
    ) -> list[AlignmentResult]:
        if sha256s:
            targets = list(sha256s)
        else:
            already_scored = await self._db.scroll_alignment_sha256s()
            targets = []
            offset = None
            while True:
                points, next_offset = await self._db._qc.scroll(
                    collection_name=IMAGES_COLLECTION,
                    limit=500,
                    offset=offset,
                    with_payload=qm.PayloadSelectorInclude(
                        include=["sha256", "positive_prompt", "wd14_tags"]
                    ),
                    with_vectors=False,
                )
                for p in points:
                    pl = p.payload or {}
                    sha = pl.get("sha256", "")
                    if (
                        sha
                        and sha not in already_scored
                        and (pl.get("positive_prompt") or "").strip()
                        and pl.get("wd14_tags")
                    ):
                        targets.append(sha)
                if next_offset is None:
                    break
                offset = next_offset

        total = len(targets)
        sem = asyncio.Semaphore(concurrency)
        counter = 0

        async def eval_one(sha256: str) -> AlignmentResult | None:
            nonlocal counter
            if cancel_fn and cancel_fn():
                return None
            async with sem:
                # Check checkpoint after acquiring semaphore (all tasks are launched
                # concurrently via gather; checking outside the semaphore would allow all
                # of them to pass the checkpoint simultaneously, making pause ineffective)
                if cancel_fn and cancel_fn():
                    return None
                if pause_checkpoint:
                    await pause_checkpoint()
                result = await self.evaluate_one(sha256)
                counter += 1
                if on_progress:
                    on_progress(counter, total)
                return result

        all_results = await asyncio.gather(*(eval_one(s) for s in targets))
        return [r for r in all_results if r is not None]

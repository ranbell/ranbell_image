import asyncio
import base64
import json
import logging
import random
import uuid


from qdrant_client import AsyncQdrantClient, models as qm

from .id_utils import sha256_to_point_id
from ..config import settings

logger = logging.getLogger(__name__)

IMAGES_COLLECTION = "images"
CONFIG_COLLECTION = "app_config"
CONFIG_POINT_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
ALIGNMENT_COLLECTION = "alignment"

_SORT_ORDER_BY = {
    "newest":      qm.OrderBy(key="mtime", direction=qm.Direction.DESC),
    "oldest":      qm.OrderBy(key="mtime", direction=qm.Direction.ASC),
    "name_asc":    qm.OrderBy(key="name", direction=qm.Direction.ASC),
    "name_desc":   qm.OrderBy(key="name", direction=qm.Direction.DESC),
    "size_desc":   qm.OrderBy(key="size", direction=qm.Direction.DESC),
    "size_asc":    qm.OrderBy(key="size", direction=qm.Direction.ASC),
    "rating_desc": qm.OrderBy(key="star_rating", direction=qm.Direction.DESC),
}


class QdrantDBClient:
    def __init__(self) -> None:
        self._qc = AsyncQdrantClient(url=settings.qdrant_url, timeout=30)
        self.has_mrl = False  # True when embedding_small vector is available
        self._small_dim: int = settings.embed_dim_small  # actual dim used in collection
        self.has_color_vector = False  # True when color_vector (3D L*a*b* Euclid) is available

    async def _wait_for_qdrant(self, timeout: int = 180) -> None:
        """Wait until Qdrant is ready, retrying with backoff."""
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 1.0
        while True:
            try:
                await self._qc.get_collections()
                return
            except Exception as e:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise RuntimeError(f"Qdrant did not become ready within {timeout}s") from e
                logger.info("Waiting for Qdrant... (%.0fs remaining)", remaining)
                await asyncio.sleep(min(delay, remaining))
                delay = min(delay * 1.5, 10.0)

    async def _migrate_small_dim(self, old_dim: int | None) -> None:
        """Recreate the collection preserving payloads + full embeddings, adding/resizing embedding_small.

        old_dim=None means embedding_small did not exist yet (first-time MRL setup).
        Qdrant does not support adding new named vectors to an existing collection via any API,
        so collection recreation is the only reliable approach.
        """
        new_dim = settings.embed_dim_small
        if old_dim is None:
            logger.warning(
                "embedding_small not found in collection. "
                "Recreating collection to add MRL vector (%d dims) — this may take a moment.", new_dim,
            )
        else:
            logger.warning(
                "embedding_small dim mismatch (collection=%d, EMBED_DIM_SMALL=%d). "
                "Recreating collection to apply new dim — this may take a moment.",
                old_dim, new_dim,
            )

        # Read all existing points: payload + full embedding only
        all_points: list[qm.PointStruct] = []
        reset_count = 0
        offset = None
        while True:
            pts, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                limit=200,
                with_payload=True,
                with_vectors=["embedding"],
                offset=offset,
            )
            for p in pts:
                payload = dict(p.payload or {})
                emb = p.vector.get("embedding") if isinstance(p.vector, dict) else None
                vectors: dict = {}
                if emb and len(emb) == settings.embed_dim:
                    vectors["embedding"] = emb
                    vectors["embedding_small"] = emb[:new_dim]
                else:
                    # Dimension mismatch or missing — reset to pending so pipeline re-embeds
                    payload["embedding_status"] = "pending"
                    payload.pop("wd14_tags", None)
                    reset_count += 1
                all_points.append(qm.PointStruct(id=p.id, payload=payload, vector=vectors))
            if next_offset is None:
                break
            offset = next_offset
        if reset_count:
            logger.warning("%d points had wrong-dim embeddings and were reset to pending", reset_count)

        logger.info("Read %d points; migrating to new collection (small_dim=%d)", len(all_points), new_dim)

        # Atomic migration: build into a temp collection first so original is safe if upsert fails
        tmp = f"{IMAGES_COLLECTION}_mrl_tmp"
        vec_cfg = {
            "embedding": qm.VectorParams(size=settings.embed_dim, distance=qm.Distance.COSINE, on_disk=True),
            "embedding_small": qm.VectorParams(size=new_dim, distance=qm.Distance.COSINE, on_disk=True),
        }

        if await self._qc.collection_exists(tmp):
            await self._qc.delete_collection(tmp)
        await self._qc.create_collection(collection_name=tmp, vectors_config=vec_cfg, on_disk_payload=True)

        try:
            for i in range(0, len(all_points), 200):
                await self._qc.upsert(tmp, points=all_points[i:i + 200])
        except Exception:
            await self._qc.delete_collection(tmp)
            raise  # original collection is still intact

        # Swap: only now delete original and recreate with migrated data
        await self._qc.delete_collection(IMAGES_COLLECTION)
        await self._qc.create_collection(
            collection_name=IMAGES_COLLECTION, vectors_config=vec_cfg, on_disk_payload=True,
        )
        await self._create_images_indexes()
        for i in range(0, len(all_points), 200):
            await self._qc.upsert(IMAGES_COLLECTION, points=all_points[i:i + 200])
        await self._qc.delete_collection(tmp)

        self._small_dim = new_dim
        self.has_mrl = True
        logger.info("Migration complete: %d points restored with small_dim=%d", len(all_points), new_dim)

    async def _migrate_color_vector(self) -> None:
        """Add color_vector (3D Euclidean) to the collection via atomic recreation.

        All existing color payload fields are cleared — the backfill job repopulates them.
        """
        logger.warning(
            "color_vector not found in collection. "
            "Recreating collection to add color_vector — this may take a moment."
        )

        all_points: list[qm.PointStruct] = []
        offset = None
        while True:
            pts, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                limit=200,
                with_payload=True,
                with_vectors=["embedding", "embedding_small"],
                offset=offset,
            )
            for p in pts:
                payload = dict(p.payload or {})
                for key in ("dominant_hues", "avg_saturation", "avg_value",
                            "color_lab", "palette_hues", "palette_hex"):
                    payload.pop(key, None)
                vectors: dict = {}
                emb = p.vector.get("embedding") if isinstance(p.vector, dict) else None
                small = p.vector.get("embedding_small") if isinstance(p.vector, dict) else None
                if emb:
                    vectors["embedding"] = emb
                if small:
                    vectors["embedding_small"] = small
                all_points.append(qm.PointStruct(id=p.id, payload=payload, vector=vectors))
            if next_offset is None:
                break
            offset = next_offset

        logger.info("Read %d points; adding color_vector to collection", len(all_points))

        vec_cfg = {
            "embedding": qm.VectorParams(size=settings.embed_dim, distance=qm.Distance.COSINE, on_disk=True),
            "embedding_small": qm.VectorParams(size=self._small_dim, distance=qm.Distance.COSINE, on_disk=True),
            "color_vector": qm.VectorParams(size=3, distance=qm.Distance.EUCLID, on_disk=True),
        }
        tmp = f"{IMAGES_COLLECTION}_color_tmp"
        if await self._qc.collection_exists(tmp):
            await self._qc.delete_collection(tmp)
        await self._qc.create_collection(collection_name=tmp, vectors_config=vec_cfg, on_disk_payload=True)

        try:
            for i in range(0, len(all_points), 200):
                await self._qc.upsert(tmp, points=all_points[i:i + 200])
        except Exception:
            await self._qc.delete_collection(tmp)
            raise

        await self._qc.delete_collection(IMAGES_COLLECTION)
        await self._qc.create_collection(
            collection_name=IMAGES_COLLECTION, vectors_config=vec_cfg, on_disk_payload=True,
        )
        await self._create_images_indexes()
        for i in range(0, len(all_points), 200):
            await self._qc.upsert(IMAGES_COLLECTION, points=all_points[i:i + 200])
        await self._qc.delete_collection(tmp)

        self.has_color_vector = True
        logger.info("color_vector migration complete: %d points restored", len(all_points))

    async def start(self) -> None:
        await self._wait_for_qdrant()
        if not await self._qc.collection_exists(IMAGES_COLLECTION):
            await self._qc.create_collection(
                collection_name=IMAGES_COLLECTION,
                vectors_config={
                    "embedding": qm.VectorParams(
                        size=settings.embed_dim,
                        distance=qm.Distance.COSINE,
                        on_disk=True,
                        quantization_config=qm.ScalarQuantization(
                            scalar=qm.ScalarQuantizationConfig(
                                type=qm.ScalarType.INT8,
                                quantile=0.99,
                                always_ram=True,
                            )
                        ),
                    ),
                    "embedding_small": qm.VectorParams(
                        size=settings.embed_dim_small,
                        distance=qm.Distance.COSINE,
                        on_disk=True,
                        quantization_config=qm.ScalarQuantization(
                            scalar=qm.ScalarQuantizationConfig(
                                type=qm.ScalarType.INT8,
                                quantile=0.99,
                                always_ram=True,
                            )
                        ),
                    ),
                    "color_vector": qm.VectorParams(
                        size=3,
                        distance=qm.Distance.EUCLID,
                        on_disk=True,
                    ),
                },
                on_disk_payload=True,
            )
            await self._create_images_indexes()
            self.has_mrl = True
            self.has_color_vector = True
            logger.info("Created collection: %s (embed_dim=%d, small=%d)",
                        IMAGES_COLLECTION, settings.embed_dim, settings.embed_dim_small)
        else:
            info = await self._qc.get_collection(IMAGES_COLLECTION)
            existing = info.config.params.vectors
            has_small = isinstance(existing, dict) and "embedding_small" in existing

            # Log actual collection dims so mismatches are always visible in startup logs
            if isinstance(existing, dict):
                dims = {k: v.size for k, v in existing.items()}
                logger.info("Collection vector dims: %s | EMBED_DIM=%d EMBED_DIM_SMALL=%d",
                            dims, settings.embed_dim, settings.embed_dim_small)

            # Check if the full embedding dim matches EMBED_DIM
            actual_embed_dim = (
                existing["embedding"].size
                if isinstance(existing, dict) and "embedding" in existing
                else None
            )
            embed_dim_mismatch = actual_embed_dim is not None and actual_embed_dim != settings.embed_dim
            if embed_dim_mismatch:
                logger.warning(
                    "embedding dim mismatch: collection=%d, EMBED_DIM=%d — recreating collection.",
                    actual_embed_dim, settings.embed_dim,
                )

            if not has_small or embed_dim_mismatch:
                # Qdrant does not support adding/resizing named vectors in existing collections —
                # recreate the collection.
                await self._migrate_small_dim(
                    existing["embedding_small"].size if has_small and not embed_dim_mismatch else None
                )
            else:
                actual_small = existing["embedding_small"].size
                if actual_small != settings.embed_dim_small:
                    await self._migrate_small_dim(actual_small)
                else:
                    self._small_dim = actual_small
                    self.has_mrl = True
            logger.info("Collection exists: %s (MRL=%s, small_dim=%d)",
                        IMAGES_COLLECTION, self.has_mrl, self._small_dim)

            # Check for color_vector named vector (added after initial MRL migration)
            info2 = await self._qc.get_collection(IMAGES_COLLECTION)
            existing2 = info2.config.params.vectors
            has_color = isinstance(existing2, dict) and "color_vector" in existing2
            if not has_color:
                await self._migrate_color_vector()
            else:
                self.has_color_vector = True
            logger.info("Collection color_vector=%s", self.has_color_vector)
            # Apply scalar quantization to existing collections (idempotent)
            await self._ensure_quantization()
            # Remove umap_x/y indexes if they exist (no longer needed, slows set_payload)
            await self._drop_umap_indexes()
            # Always re-apply indexes — idempotent, ensures new indexes are added to existing collections
            await self._create_images_indexes()

        if not await self._qc.collection_exists(CONFIG_COLLECTION):
            await self._qc.create_collection(
                collection_name=CONFIG_COLLECTION,
                vectors_config={},
                on_disk_payload=True,
            )
            logger.info("Created collection: %s", CONFIG_COLLECTION)

        if not await self._qc.collection_exists(ALIGNMENT_COLLECTION):
            await self._qc.create_collection(
                collection_name=ALIGNMENT_COLLECTION,
                vectors_config={},
                on_disk_payload=True,
            )
            logger.info("Created collection: %s", ALIGNMENT_COLLECTION)
        await self._create_alignment_indexes()

        count = await self.total_count()
        logger.info("Qdrant ready: %d images", count)

    async def _drop_umap_indexes(self) -> None:
        """Drop umap_x/y payload indexes — presence-only checks don't need indexes."""
        for field in ("umap_x", "umap_y"):
            try:
                await self._qc.delete_payload_index(
                    collection_name=IMAGES_COLLECTION,
                    field_name=field,
                )
                logger.info("Dropped payload index: %s", field)
            except Exception:
                pass  # already absent

    async def _ensure_quantization(self) -> None:
        """Apply INT8 scalar quantization to embedding vectors if not already set."""
        info = await self._qc.get_collection(IMAGES_COLLECTION)
        vec_cfg = info.config.params.vectors
        if not isinstance(vec_cfg, dict):
            return
        scalar_quant = qm.ScalarQuantization(
            scalar=qm.ScalarQuantizationConfig(
                type=qm.ScalarType.INT8,
                quantile=0.99,
                always_ram=True,
            )
        )
        needs_update = False
        vectors_config = {}
        for name in ("embedding", "embedding_small"):
            if name not in vec_cfg:
                continue
            if vec_cfg[name].quantization_config is None:
                vectors_config[name] = qm.VectorParamsDiff(
                    quantization_config=scalar_quant,
                )
                needs_update = True
        if needs_update:
            await self._qc.update_collection(
                collection_name=IMAGES_COLLECTION,
                vectors_config=vectors_config,
            )
            logger.info("Applied INT8 scalar quantization to: %s", list(vectors_config))

    async def _create_images_indexes(self) -> None:
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="mtime",
            field_schema=qm.PayloadSchemaType.DATETIME,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="size",
            field_schema=qm.PayloadSchemaType.INTEGER,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="name",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="embedding_status",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="wd14_tags",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="positive_prompt",
            field_schema=qm.TextIndexParams(
                type="text",
                tokenizer=qm.TokenizerType.WORD,
                min_token_len=2,
                max_token_len=30,
            ),
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="palette_hues",
            field_schema=qm.PayloadSchemaType.FLOAT,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="palette_hex",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="avg_saturation",
            field_schema=qm.PayloadSchemaType.FLOAT,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="avg_value",
            field_schema=qm.PayloadSchemaType.FLOAT,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="model_name",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        # umap_x / umap_y are only used for existence checks so no index is needed
        # (an index would trigger a rebuild on every set_payload call, slowing UMAP saves)
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="star_rating",
            field_schema=qm.PayloadSchemaType.INTEGER,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="batch_category",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="is_reference",
            field_schema=qm.PayloadSchemaType.BOOL,
        )
        await self._qc.create_payload_index(
            collection_name=IMAGES_COLLECTION,
            field_name="creation_record.method",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )

    async def close(self) -> None:
        await self._qc.close()

    # ── Image CRUD ───────────────────────────────────────────────────────────────

    async def get(self, sha256: str) -> dict | None:
        point_id = sha256_to_point_id(sha256)
        results = await self._qc.retrieve(
            collection_name=IMAGES_COLLECTION,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        return results[0].payload if results else None

    async def get_by_sha256s(self, sha256s: list[str]) -> list[dict]:
        """Fetch full payloads for a list of sha256s, preserving input order."""
        if not sha256s:
            return []
        ids = [sha256_to_point_id(s) for s in sha256s]
        points = await self._qc.retrieve(
            collection_name=IMAGES_COLLECTION,
            ids=ids,
            with_payload=True,
            with_vectors=False,
        )
        by_sha = {p.payload["sha256"]: p.payload for p in points if p.payload}
        return [by_sha[s] for s in sha256s if s in by_sha]

    async def scroll_name_index(self) -> list[tuple[str, str]]:
        """Return (name_lower, sha256) pairs using minimal payload — fast name sort index."""
        pairs: list[tuple[str, str]] = []
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["name", "sha256"]),
                with_vectors=False,
            )
            for p in points:
                pl = p.payload or {}
                sha256 = pl.get("sha256", "")
                if sha256:
                    pairs.append(((pl.get("name") or "").lower(), sha256))
            if next_offset is None:
                break
            offset = next_offset
        return pairs

    async def upsert_new(self, sha256: str, payload: dict) -> None:
        """Insert a new image point without a vector."""
        point_id = sha256_to_point_id(sha256)
        await self._qc.upsert(
            collection_name=IMAGES_COLLECTION,
            points=[qm.PointStruct(id=point_id, vector={}, payload=payload)],
        )

    async def set_payload(self, sha256: str, updates: dict) -> None:
        """Partial payload update — preserves vectors and unspecified fields."""
        point_id = sha256_to_point_id(sha256)
        await self._qc.set_payload(
            collection_name=IMAGES_COLLECTION,
            payload=updates,
            points=qm.PointIdsList(points=[point_id]),
        )

    async def set_embedding(self, sha256: str, embedding: list[float]) -> None:
        """Store full embedding + MRL-truncated small embedding."""
        if len(embedding) != settings.embed_dim:
            raise ValueError(
                f"EMBED_DIM mismatch: model '{settings.embed_model}' returned {len(embedding)} dimensions "
                f"but EMBED_DIM={settings.embed_dim} is configured.\n"
                f"  → Either update EMBED_DIM in .env to {len(embedding)}, or "
                f"set EMBED_MODEL to a model that outputs {settings.embed_dim} dimensions.\n"
                f"  → To check the model's actual dimensions: "
                f"curl -s http://localhost:11434/api/embed "
                f"-d '{{\"model\":\"{settings.embed_model}\",\"input\":\"test\"}}' | "
                f"python3 -c \"import sys,json; print(len(json.load(sys.stdin)['embeddings'][0]))\""
            )
        point_id = sha256_to_point_id(sha256)
        vectors: dict = {"embedding": embedding}
        if self.has_mrl:
            vectors["embedding_small"] = embedding[:self._small_dim]
        await self._qc.update_vectors(
            collection_name=IMAGES_COLLECTION,
            points=[qm.PointVectors(id=point_id, vector=vectors)],
        )

    async def delete_embedding(self, sha256: str) -> None:
        """Remove embedding vectors, keeping the point and payload."""
        point_id = sha256_to_point_id(sha256)
        await self._qc.delete_vectors(
            collection_name=IMAGES_COLLECTION,
            vectors=["embedding", "embedding_small"],
            points=qm.PointIdsList(points=[point_id]),
        )

    async def delete(self, sha256: str) -> None:
        point_id = sha256_to_point_id(sha256)
        await self._qc.delete(
            collection_name=IMAGES_COLLECTION,
            points_selector=qm.PointIdsList(points=[point_id]),
        )

    # ── Query / Scroll ───────────────────────────────────────────────────────────

    async def scroll_images(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
        sort: str = "newest",
    ) -> tuple[list[dict], str | None]:
        sort_def = _SORT_ORDER_BY.get(sort, _SORT_ORDER_BY["newest"])

        # Decode cursor: {start: <sort_field_value>, last_id: <sha256>}
        start_from = None
        last_id = None
        if cursor:
            try:
                c = json.loads(base64.b64decode(cursor.encode()))
                start_from = c.get("start")
                last_id = c.get("last_id")
            except Exception:
                pass

        order = qm.OrderBy(
            key=sort_def.key,
            direction=sort_def.direction,
            start_from=start_from,
        )

        # When resuming from a cursor, start_from is inclusive so the boundary
        # item will appear in results. Fetch +2 to still have +1 for has_more
        # detection after removing the boundary item.
        fetch_limit = limit + 2 if last_id else limit + 1
        points, _ = await self._qc.scroll(
            collection_name=IMAGES_COLLECTION,
            order_by=order,
            limit=fetch_limit,
            with_payload=True,
            with_vectors=False,
        )
        docs = [p.payload for p in points]

        # Remove the already-seen boundary item
        if last_id:
            docs = [d for d in docs if d.get("sha256") != last_id]

        # Use >= limit so that Qdrant under-delivery (high load, returns limit instead of limit+1)
        # doesn't falsely terminate pagination. True end emits one extra empty page.
        has_more = len(docs) >= limit
        docs = docs[:limit]

        if has_more and docs:
            last = docs[-1]
            next_cursor = base64.b64encode(json.dumps({
                "start": last.get(sort_def.key),
                "last_id": last.get("sha256"),
            }).encode()).decode()
        else:
            next_cursor = None

        return docs, next_cursor

    async def scroll_all(
        self,
        *,
        tags_include: list[str] | None = None,
        tag_logic: str = "and",
        keyword: str | None = None,
        models: list[str] | None = None,
        star_min: int | None = None,
        category: str | None = None,
        sha256_ids: set[str] | None = None,
    ) -> list[dict]:
        """Fetch all documents, optionally pre-filtered by tag/keyword/model conditions."""
        must: list = []
        if tags_include:
            if tag_logic == "or":
                must.append(qm.FieldCondition(key="wd14_tags", match=qm.MatchAny(any=tags_include)))
            else:
                for t in tags_include:
                    must.append(qm.FieldCondition(key="wd14_tags", match=qm.MatchValue(value=t)))
        if keyword:
            must.append(qm.FieldCondition(key="positive_prompt", match=qm.MatchText(text=keyword)))
        if models:
            must.append(qm.FieldCondition(key="model_name", match=qm.MatchAny(any=models)))
        if star_min is not None:
            must.append(qm.FieldCondition(key="star_rating", range=qm.Range(gte=star_min)))
        if category in ("AI", "NR"):
            must.append(qm.FieldCondition(key="batch_category", match=qm.MatchValue(value=category)))

        if sha256_ids is not None:
            ids_filter = qm.HasIdCondition(has_id=[sha256_to_point_id(s) for s in sha256_ids])
            if must:
                scroll_filter = qm.Filter(must=must + [ids_filter])
            else:
                scroll_filter = qm.Filter(must=[ids_filter])
        else:
            scroll_filter = qm.Filter(must=must) if must else None

        all_docs: list[dict] = []
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=scroll_filter,
                limit=1000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            all_docs.extend(p.payload for p in points)
            if next_offset is None:
                break
            offset = next_offset
        return all_docs

    async def scroll_model_facets(self) -> list[dict]:
        """Aggregate unique model names (from params.Model) with image counts."""
        model_count: dict[str, int] = {}
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["params"]),
                with_vectors=False,
            )
            for p in points:
                model = ((p.payload or {}).get("params") or {}).get("Model", "").strip()
                if model:
                    model_count[model] = model_count.get(model, 0) + 1
            if next_offset is None:
                break
            offset = next_offset
        return sorted(
            [{"model": m, "count": c} for m, c in model_count.items()],
            key=lambda x: -x["count"],
        )

    async def list_dirs(self, base_dirs: list[str]) -> list[dict]:
        """Scroll all docs and aggregate by parent directory.

        path_rel is computed relative to os.path.commonpath(base_dirs), so paths
        from different roots are distinguished by their root segment (e.g. "source/foo"
        vs "generated/bar").
        """
        import os
        base = os.path.commonpath([d.rstrip("/") for d in base_dirs]) if base_dirs else "/"
        dir_map: dict[str, dict] = {}
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "path", "mtime"]),
                with_vectors=False,
            )
            for p in points:
                pl = p.payload or {}
                path_str = pl.get("path", "")
                sha256 = pl.get("sha256", "")
                mtime = pl.get("mtime", "") or ""
                if not path_str or not sha256:
                    continue
                try:
                    rel = os.path.relpath(os.path.dirname(path_str), base)
                    if rel == ".":
                        rel = ""
                except ValueError:
                    rel = ""
                if rel not in dir_map:
                    dir_map[rel] = {
                        "name": os.path.basename(rel) if rel else "",
                        "path_rel": rel,
                        "count": 0,
                        "_all_sha256s": [],
                        "mtime": mtime,
                    }
                e = dir_map[rel]
                e["count"] += 1
                e["_all_sha256s"].append(sha256)
                if mtime > e["mtime"]:
                    e["mtime"] = mtime
            if next_offset is None:
                break
            offset = next_offset
        result = []
        for e in dir_map.values():
            shas = e.pop("_all_sha256s")
            e["preview_sha256s"] = random.sample(shas, min(4, len(shas)))
            result.append(e)
        return sorted(result, key=lambda d: (-d["count"], d["path_rel"]))

    async def set_color_vector(self, sha256: str, lab: list[float]) -> None:
        """Store the 3D L*a*b* color_vector for a single image point."""
        if not self.has_color_vector:
            return
        point_id = sha256_to_point_id(sha256)
        await self._qc.update_vectors(
            collection_name=IMAGES_COLLECTION,
            points=[qm.PointVectors(id=point_id, vector={"color_vector": lab})],
        )

    async def search_by_color_vector(
        self,
        lab: list[float],
        distance: float,
        limit: int = 100,
        exclude_hue_ranges: list[tuple[float, float]] | None = None,
    ) -> list[dict]:
        """Search images by L*a*b* Euclidean distance (CIE76 ΔE).

        exclude_hue_ranges: (lo, hi) degree pairs excluded via palette_hues payload filter.
        score_threshold acts as an upper bound for Euclidean distance.
        """
        must_not = [
            qm.FieldCondition(key="palette_hues", range=qm.Range(gte=lo, lte=hi))
            for lo, hi in (exclude_hue_ranges or [])
        ]
        results = await self._qc.query_points(
            collection_name=IMAGES_COLLECTION,
            query=lab,
            using="color_vector",
            limit=limit,
            score_threshold=distance,
            query_filter=qm.Filter(must_not=must_not) if must_not else None,
            with_payload=True,
            with_vectors=False,
        )
        return [{**r.payload, "_color_distance": round(r.score, 4)} for r in results.points]

    async def count_with_color_vector(self) -> int:
        """Count images whose color_vector is actually synced to Qdrant.

        Proxy: avg_saturation exists (color extraction done) AND color_lab absent
        (color_lab is removed by backfill after syncing; pipeline-only images retain it).
        """
        if not self.has_color_vector:
            return 0
        result = await self._qc.count(
            collection_name=IMAGES_COLLECTION,
            count_filter=qm.Filter(must=[
                qm.FieldCondition(key="avg_saturation", range=qm.Range(gte=0.0)),
                qm.IsEmptyCondition(is_empty=qm.PayloadField(key="color_lab")),
            ]),
            exact=True,
        )
        return result.count

    async def count_with_color_lab(self) -> int:
        """Count images that have color_lab payload (pipeline-processed, color_vector not yet synced)."""
        result = await self._qc.count(
            collection_name=IMAGES_COLLECTION,
            count_filter=qm.Filter(must_not=[
                qm.IsEmptyCondition(is_empty=qm.PayloadField(key="color_lab"))
            ]),
            exact=True,
        )
        return result.count

    async def scroll_color_lab_points(self, limit: int = 5000) -> list[dict]:
        """Return up to `limit` images with color data, reading Lab from color_vector named vector.

        Falls back to color_lab payload field for images in transition (not yet backfilled).
        """
        docs: list[dict] = []
        offset = None
        filt = qm.Filter(must=[
            qm.FieldCondition(key="avg_saturation", range=qm.Range(gte=0.0))
        ])
        while len(docs) < limit:
            batch_limit = min(500, limit - len(docs))
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=filt,
                limit=batch_limit,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(
                    include=["sha256", "palette_hex", "name", "color_lab"]
                ),
                with_vectors=["color_vector"] if self.has_color_vector else False,
            )
            for p in points:
                pl = p.payload or {}
                lab = None
                if self.has_color_vector and p.vector:
                    vec = p.vector.get("color_vector") if isinstance(p.vector, dict) else p.vector
                    if vec and len(vec) == 3:
                        lab = list(vec)
                if lab is None:
                    lab = pl.get("color_lab")
                if lab and len(lab) == 3:
                    docs.append({
                        "sha256": pl.get("sha256"),
                        "L": lab[0], "a": lab[1], "b": lab[2],
                        "hex": (pl.get("palette_hex") or ["#888888"])[0],
                        "name": pl.get("name", ""),
                    })
            if next_offset is None:
                break
            offset = next_offset
        return docs

    async def count_pending_color_extraction(self) -> int:
        """Count images that have not yet been through color extraction (avg_saturation absent)."""
        result = await self._qc.count(
            collection_name=IMAGES_COLLECTION,
            count_filter=qm.Filter(must=[
                qm.IsEmptyCondition(is_empty=qm.PayloadField(key="avg_saturation"))
            ]),
            exact=True,
        )
        return result.count

    async def recover_missing_color_vectors(self) -> int:
        """Heuristic recovery: images with avg_saturation set but color_vector all-zero are
        repaired by deriving Lab from palette_hex[0]. Returns count of recovered images.
        """
        if not self.has_color_vector:
            return 0
        from ..ai.color_extractor import hex_to_lab  # lazy import to avoid circular dependency
        count = 0
        offset = None
        filt = qm.Filter(must=[
            qm.FieldCondition(key="avg_saturation", range=qm.Range(gte=0.0)),
            qm.IsEmptyCondition(is_empty=qm.PayloadField(key="color_lab")),
        ])
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=filt,
                limit=200,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "palette_hex"]),
                with_vectors=["color_vector"],
            )
            to_fix: list[tuple[str, list[float]]] = []
            for p in points:
                vec = p.vector.get("color_vector") if isinstance(p.vector, dict) else p.vector
                if vec and len(vec) == 3 and any(v != 0.0 for v in vec):
                    continue  # already has a real vector
                pl = p.payload or {}
                sha256 = pl.get("sha256")
                palette = pl.get("palette_hex") or []
                if sha256 and palette:
                    try:
                        to_fix.append((sha256, [round(x, 2) for x in hex_to_lab(palette[0])]))
                    except Exception:
                        pass
            if to_fix:
                await self.set_color_vectors_batch(to_fix)
                count += len(to_fix)
            if next_offset is None:
                break
            offset = next_offset
        return count

    async def delete_payload_keys(self, sha256: str, keys: list[str]) -> None:
        """Remove specific payload keys from a document."""
        point_id = sha256_to_point_id(sha256)
        await self._qc.delete_payload(
            collection_name=IMAGES_COLLECTION,
            keys=keys,
            points=qm.PointIdsList(points=[point_id]),
        )

    async def set_color_vectors_batch(self, items: list[tuple[str, list[float]]]) -> None:
        """Bulk-update color_vector for multiple images in a single Qdrant call."""
        if not self.has_color_vector or not items:
            return
        points = [
            qm.PointVectors(id=sha256_to_point_id(sha256), vector={"color_vector": lab})
            for sha256, lab in items
        ]
        await self._qc.update_vectors(collection_name=IMAGES_COLLECTION, points=points)

    async def delete_payload_keys_batch(self, sha256s: list[str], keys: list[str]) -> None:
        """Remove payload keys from multiple documents in a single Qdrant call."""
        if not sha256s:
            return
        point_ids = [sha256_to_point_id(s) for s in sha256s]
        await self._qc.delete_payload(
            collection_name=IMAGES_COLLECTION,
            keys=keys,
            points=qm.PointIdsList(points=point_ids),
        )

    async def find_path_mtime_index(self) -> dict[str, dict]:
        """Return {path: {sha256, mtime}} index for incremental heal scan."""
        index: dict[str, dict] = {}
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "path", "mtime"]),
                with_vectors=False,
            )
            for p in points:
                path = p.payload.get("path")
                if path:
                    index[path] = {
                        "sha256": p.payload.get("sha256", ""),
                        "mtime": p.payload.get("mtime", ""),
                    }
            if next_offset is None:
                break
            offset = next_offset
        return index

    async def find_duplicate_path_sha256s(self) -> dict[str, list[str]]:
        """Return {path: [sha256, ...]} for paths with more than one Qdrant entry."""
        from collections import defaultdict
        path_map: defaultdict[str, list[str]] = defaultdict(list)
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "path"]),
                with_vectors=False,
            )
            for p in points:
                path = p.payload.get("path")
                sha256 = p.payload.get("sha256")
                if path and sha256:
                    path_map[path].append(sha256)
            if next_offset is None:
                break
            offset = next_offset
        return {p: shas for p, shas in path_map.items() if len(shas) > 1}

    async def scroll_tags(self) -> list[dict]:
        """Fetch all points with only prompt and wd14_tags fields (for tag counting)."""
        docs: list[dict] = []
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["positive_prompt", "wd14_tags"]),
                with_vectors=False,
            )
            docs.extend(p.payload for p in points)
            if next_offset is None:
                break
            offset = next_offset
        return docs

    async def total_count(self) -> int:
        result = await self._qc.count(collection_name=IMAGES_COLLECTION, exact=True)
        return result.count

    async def count_with_embedding(self) -> int:
        result = await self._qc.count(
            collection_name=IMAGES_COLLECTION,
            count_filter=qm.Filter(must=[
                qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="done"))
            ]),
            exact=True,
        )
        return result.count

    async def search_vector(
        self,
        embedding: list[float],
        n_results: int = 20,
        tag: str | None = None,
    ) -> list[dict]:
        """Two-phase MRL search when available, single-phase fallback otherwise."""
        query_filter = None
        if tag:
            query_filter = qm.Filter(must=[
                qm.FieldCondition(key="wd14_tags", match=qm.MatchAny(any=[tag]))
            ])

        if self.has_mrl:
            results = await self._qc.query_points(
                collection_name=IMAGES_COLLECTION,
                prefetch=[qm.Prefetch(
                    query=embedding[:settings.embed_dim_small],
                    using="embedding_small",
                    limit=n_results * 20,
                    filter=query_filter,
                )],
                query=embedding,
                using="embedding",
                limit=n_results,
                with_payload=True,
                with_vectors=False,
            )
        else:
            results = await self._qc.query_points(
                collection_name=IMAGES_COLLECTION,
                query=embedding,
                using="embedding",
                limit=n_results,
                query_filter=query_filter,
                with_payload=True,
                with_vectors=False,
            )
        return [{**r.payload, "_score": round(r.score, 4)} for r in results.points]

    async def search_vector_tag_or(
        self,
        embedding: list[float],
        tags_include: list[str],
        tags_exclude: list[str] | None = None,
        n_results: int = 300,
    ) -> list[dict]:
        """Vector search with OR tag filter. Returns payloads with _score field."""
        must = [qm.FieldCondition(key="wd14_tags", match=qm.MatchAny(any=tags_include))]
        must_not = [
            qm.FieldCondition(key="wd14_tags", match=qm.MatchValue(value=t))
            for t in (tags_exclude or [])
        ]
        query_filter = qm.Filter(
            must=must,
            must_not=must_not if must_not else None,
        )

        if self.has_mrl:
            results = await self._qc.query_points(
                collection_name=IMAGES_COLLECTION,
                prefetch=[qm.Prefetch(
                    query=embedding[:settings.embed_dim_small],
                    using="embedding_small",
                    limit=n_results * 20,
                    filter=query_filter,
                )],
                query=embedding,
                using="embedding",
                limit=n_results,
                with_payload=True,
                with_vectors=False,
            )
        else:
            results = await self._qc.query_points(
                collection_name=IMAGES_COLLECTION,
                query=embedding,
                using="embedding",
                limit=n_results,
                query_filter=query_filter,
                with_payload=True,
                with_vectors=False,
            )
        return [{**r.payload, "_score": round(r.score, 4)} for r in results.points]

    async def search_similar(self, sha256: str, n_results: int = 24) -> list[dict]:
        """Two-phase MRL similarity search excluding the source image."""
        point_id = sha256_to_point_id(sha256)
        points = await self._qc.retrieve(
            collection_name=IMAGES_COLLECTION,
            ids=[point_id],
            with_vectors=["embedding"],
            with_payload=False,
        )
        if not points:
            return []
        vec = points[0].vector
        if isinstance(vec, dict):
            vec = vec.get("embedding")
        if not vec:
            return []

        exclude_self = qm.Filter(must_not=[qm.HasIdCondition(has_id=[point_id])])

        if self.has_mrl:
            results = await self._qc.query_points(
                collection_name=IMAGES_COLLECTION,
                prefetch=[qm.Prefetch(
                    query=vec[:settings.embed_dim_small],
                    using="embedding_small",
                    limit=(n_results + 1) * 20,
                    filter=exclude_self,
                )],
                query=vec,
                using="embedding",
                limit=n_results,
                query_filter=exclude_self,
                with_payload=True,
                with_vectors=False,
            )
        else:
            results = await self._qc.query_points(
                collection_name=IMAGES_COLLECTION,
                query=vec,
                using="embedding",
                limit=n_results,
                query_filter=exclude_self,
                with_payload=True,
                with_vectors=False,
            )
        return [
            {**r.payload, "_score": round(r.score, 4)}
            for r in results.points
        ]

    async def build_similarity_graph(
        self,
        sha256: str,
        neighbors: int = 6,
        depth: int = 2,
        max_nodes: int = 50,
    ) -> dict:
        """BFS similarity graph around a root image."""
        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        queue: list[tuple[str, int]] = [(sha256, 0)]
        visited: set[str] = {sha256}

        root_doc = await self.get(sha256)
        if not root_doc:
            return {"nodes": [], "edges": []}
        nodes[sha256] = {"sha256": sha256, "name": root_doc.get("name", ""), "is_root": True}

        while queue and len(nodes) < max_nodes:
            current_sha, current_depth = queue.pop(0)
            if current_depth >= depth:
                continue
            results = await self.search_similar(current_sha, n_results=neighbors)
            for r in results:
                n_sha = r.get("sha256", "")
                score = r.get("_score", 0.0)
                if not n_sha:
                    continue
                if n_sha not in nodes and len(nodes) < max_nodes:
                    nodes[n_sha] = {"sha256": n_sha, "name": r.get("name", ""), "is_root": False}
                if n_sha not in visited:
                    visited.add(n_sha)
                    queue.append((n_sha, current_depth + 1))
                a, b = min(current_sha, n_sha), max(current_sha, n_sha)
                key = f"{a}_{b}"
                if key not in edges or edges[key]["score"] < score:
                    edges[key] = {"source": current_sha, "target": n_sha, "score": score}

        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    async def get_embedding(self, sha256: str) -> list[float] | None:
        """Retrieve the large embedding vector for a single image."""
        point_id = sha256_to_point_id(sha256)
        points = await self._qc.retrieve(
            collection_name=IMAGES_COLLECTION,
            ids=[point_id],
            with_vectors=["embedding"],
            with_payload=False,
        )
        if not points:
            return None
        vec = points[0].vector
        if isinstance(vec, dict):
            return vec.get("embedding")
        return None

    @staticmethod
    def _ref_exclude_cond() -> "qm.FieldCondition":
        return qm.FieldCondition(key="is_reference", match=qm.MatchValue(value=True))

    async def search_by_vector(
        self,
        vector: list[float],
        n_results: int = 12,
        exclude_sha256s: list[str] | None = None,
        exclude_reference: bool = False,
    ) -> list[dict]:
        """Search Qdrant with an arbitrary query vector, returning payloads."""
        must_not: list = []
        exclude_ids = [sha256_to_point_id(s) for s in (exclude_sha256s or [])]
        if exclude_ids:
            must_not.append(qm.HasIdCondition(has_id=exclude_ids))
        if exclude_reference:
            must_not.append(self._ref_exclude_cond())
        query_filter = qm.Filter(must_not=must_not) if must_not else None
        results = await self._qc.query_points(
            collection_name=IMAGES_COLLECTION,
            query=vector,
            using="embedding",
            limit=n_results,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
        return [r.payload for r in results.points]

    async def search_by_vector_scored(
        self,
        vector: list[float],
        n_results: int = 100,
        exclude_sha256s: list[str] | None = None,
        exclude_reference: bool = False,
    ) -> list[tuple[dict, float]]:
        """Search and return (payload, score) tuples for score-range filtering."""
        must_not: list = []
        exclude_ids = [sha256_to_point_id(s) for s in (exclude_sha256s or [])]
        if exclude_ids:
            must_not.append(qm.HasIdCondition(has_id=exclude_ids))
        if exclude_reference:
            must_not.append(self._ref_exclude_cond())
        query_filter = qm.Filter(must_not=must_not) if must_not else None
        results = await self._qc.query_points(
            collection_name=IMAGES_COLLECTION,
            query=vector,
            using="embedding",
            limit=n_results,
            query_filter=query_filter,
            with_payload=True,
            with_vectors=False,
        )
        return [(r.payload, r.score) for r in results.points]

    async def discover_images(
        self,
        target_sha256: str,
        context_pairs: list[tuple[str, str]],
        n_results: int = 20,
        exclude_reference: bool = False,
    ) -> list[dict]:
        """Discovery API: find images contextually close to target and matching context contrasts."""
        target_vec = await self.get_embedding(target_sha256)
        if not target_vec:
            return []
        context = [
            qm.ContextPair(
                positive=sha256_to_point_id(pos),
                negative=sha256_to_point_id(neg),
            )
            for pos, neg in context_pairs
        ]
        ref_filter = qm.Filter(must_not=[self._ref_exclude_cond()]) if exclude_reference else None
        batch = await self._qc.query_batch_points(
            collection_name=IMAGES_COLLECTION,
            requests=[qm.QueryRequest(
                query=qm.DiscoverQuery(
                    discover=qm.DiscoverInput(target=target_vec, context=context)
                ),
                using="embedding",
                limit=n_results,
                filter=ref_filter,
                with_payload=True,
            )],
        )
        points = batch[0].points if batch else []
        return [{**r.payload, "_score": round(r.score, 4)} for r in points]

    async def search_images_grouped(
        self,
        embedding: list[float],
        group_by: str,
        group_size: int = 3,
        limit: int = 10,
        exclude_reference: bool = False,
    ) -> list[dict]:
        """GroupBy search: return top images grouped by a payload field (e.g. model_name)."""
        ref_filter = qm.Filter(must_not=[self._ref_exclude_cond()]) if exclude_reference else None
        if self.has_mrl:
            results = await self._qc.query_points_groups(
                collection_name=IMAGES_COLLECTION,
                prefetch=[qm.Prefetch(
                    query=embedding[:settings.embed_dim_small],
                    using="embedding_small",
                    limit=limit * group_size * 20,
                )],
                query=embedding,
                using="embedding",
                group_by=group_by,
                group_size=group_size,
                limit=limit,
                query_filter=ref_filter,
                with_payload=True,
                with_vectors=False,
            )
        else:
            results = await self._qc.query_points_groups(
                collection_name=IMAGES_COLLECTION,
                query=embedding,
                using="embedding",
                group_by=group_by,
                group_size=group_size,
                limit=limit,
                query_filter=ref_filter,
                with_payload=True,
                with_vectors=False,
            )
        groups = []
        for g in results.groups:
            groups.append({
                "group_id": g.id,
                "hits": [{**r.payload, "_score": round(r.score, 4)} for r in g.hits],
            })
        return groups

    async def get_collection_embed_dim_small(self) -> int | None:
        """Return the actual embedding_small vector size stored in Qdrant collection config."""
        try:
            info = await self._qc.get_collection(IMAGES_COLLECTION)
            vec_cfg = info.config.params.vectors
            if isinstance(vec_cfg, dict) and "embedding_small" in vec_cfg:
                return vec_cfg["embedding_small"].size
        except Exception:
            pass
        return None

    async def count_small_embeddings(self) -> int:
        """Count points that have embedding_small stored."""
        try:
            info = await self._qc.get_collection(IMAGES_COLLECTION)
            vec_cfg = info.config.params.vectors
            if not isinstance(vec_cfg, dict) or "embedding_small" not in vec_cfg:
                return 0
        except Exception:
            return 0

        count = 0
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                limit=1000,
                with_payload=False,
                with_vectors=["embedding_small"],
                offset=offset,
            )
            count += sum(
                1 for p in points
                if p.vector and (
                    isinstance(p.vector, dict) and p.vector.get("embedding_small")
                    or isinstance(p.vector, list) and p.vector
                )
            )
            if next_offset is None:
                break
            offset = next_offset
        return count

    async def backfill_small_embeddings(self) -> int:
        """For all points with 'embedding' but missing 'embedding_small', add the truncated vector."""
        count = 0
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="done"))
                ]),
                limit=200,
                with_payload=False,
                with_vectors=True,
                offset=offset,
            )
            to_update: list[qm.PointVectors] = []
            for p in points:
                vec = p.vector
                if isinstance(vec, dict):
                    full = vec.get("embedding")
                    already = vec.get("embedding_small")
                else:
                    full = vec
                    already = None
                if not full or already:
                    continue
                to_update.append(qm.PointVectors(
                    id=p.id,
                    vector={"embedding_small": full[:settings.embed_dim_small]},
                ))
            if to_update:
                await self._qc.update_vectors(IMAGES_COLLECTION, points=to_update)
                count += len(to_update)
                logger.info("MRL backfill: %d points updated so far", count)
            if next_offset is None:
                break
            offset = next_offset
        return count

    async def backfill_model_name(self) -> int:
        """Populate model_name field from params.Model for existing documents that lack it."""
        count = 0
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "params", "model_name"]),
                with_vectors=False,
            )
            to_update: list[tuple[str, str]] = []
            for p in points:
                pl = p.payload or {}
                if pl.get("model_name") is not None:
                    continue
                model = ((pl.get("params") or {}).get("Model") or "").strip()
                to_update.append((p.id, model))
            if to_update:
                import asyncio as _asyncio
                await _asyncio.gather(*[
                    self._qc.set_payload(
                        collection_name=IMAGES_COLLECTION,
                        payload={"model_name": model},
                        points=qm.PointIdsList(points=[point_id]),
                    )
                    for point_id, model in to_update
                ])
                count += len(to_update)
                logger.info("model_name backfill: %d docs updated so far", count)
            if next_offset is None:
                break
            offset = next_offset
        return count

    async def backfill_batch_category(self) -> int:
        """Populate batch_category for documents that lack it.

        Uses raw_metadata.format to classify: "a1111"/"comfyui" → "AI", else → "NR".
        WD14 completion is tracked separately via embedding_status / wd14_tags.
        """
        count = 0
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(
                    include=["sha256", "batch_category", "raw_metadata"]
                ),
                with_vectors=False,
            )
            to_update: list[tuple[str, str]] = []
            for p in points:
                pl = p.payload or {}
                if pl.get("batch_category") is not None:
                    continue
                sha256 = pl.get("sha256")
                if not sha256:
                    continue
                fmt = (pl.get("raw_metadata") or {}).get("format", "unknown")
                category = "AI" if fmt in ("a1111", "comfyui") else "NR"
                to_update.append((sha256, category))
            if to_update:
                await asyncio.gather(*[
                    self._qc.set_payload(
                        collection_name=IMAGES_COLLECTION,
                        payload={"batch_category": cat},
                        points=qm.PointIdsList(points=[sha256_to_point_id(sha256)]),
                    )
                    for sha256, cat in to_update
                ])
                count += len(to_update)
                logger.info("batch_category backfill: %d docs updated so far", count)
            if next_offset is None:
                break
            offset = next_offset
        return count

    async def backfill_is_reference(self) -> int:
        """Populate is_reference for documents that lack it.

        Uses path prefix against settings.source_images_dir to classify.
        """
        source_prefix = str(settings.source_images_dir)
        count = 0
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "is_reference", "path"]),
                with_vectors=False,
            )
            to_update: list[tuple[str, bool]] = []
            for p in points:
                pl = p.payload or {}
                if pl.get("is_reference") is not None:
                    continue
                sha256 = pl.get("sha256")
                if not sha256:
                    continue
                path = pl.get("path", "")
                to_update.append((sha256, path.startswith(source_prefix)))
            if to_update:
                await asyncio.gather(*[
                    self._qc.set_payload(
                        collection_name=IMAGES_COLLECTION,
                        payload={"is_reference": is_ref},
                        points=qm.PointIdsList(points=[sha256_to_point_id(sha256)]),
                    )
                    for sha256, is_ref in to_update
                ])
                count += len(to_update)
                logger.info("is_reference backfill: %d docs updated so far", count)
            if next_offset is None:
                break
            offset = next_offset
        return count

    async def random_sample(
        self,
        n: int,
        exclude_sha256s: list[str] | None = None,
    ) -> list[dict]:
        """Return n random image documents, excluding the given sha256s."""
        n = max(1, min(n, 6))
        exclude_ids = [sha256_to_point_id(s) for s in (exclude_sha256s or [])]
        scroll_filter = (
            qm.Filter(must_not=[qm.HasIdCondition(has_id=exclude_ids)])
            if exclude_ids else None
        )
        all_ids: list = []
        offset = None
        while True:
            points, next_offset = await self._qc.scroll(
                IMAGES_COLLECTION,
                scroll_filter=scroll_filter,
                limit=1000,
                with_payload=False,
                with_vectors=False,
                offset=offset,
            )
            all_ids.extend(p.id for p in points)
            if next_offset is None:
                break
            offset = next_offset
        if not all_ids:
            return []
        sampled = random.sample(all_ids, min(n, len(all_ids)))
        retrieved = await self._qc.retrieve(
            IMAGES_COLLECTION,
            ids=sampled,
            with_payload=True,
            with_vectors=False,
        )
        return [p.payload for p in retrieved if p.payload]

    # ── Bulk admin operations ────────────────────────────────────────────────────

    async def reset_scope(self, scope: str) -> int:
        """Reset AI data for scope: 'all' | 'done' | 'pending'."""
        if scope == "done":
            filt = qm.Filter(must=[
                qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="done"))
            ])
        elif scope == "pending":
            filt = qm.Filter(must=[
                qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="pending"))
            ])
        else:  # all — match everything
            filt = qm.Filter()

        result = await self._qc.count(
            collection_name=IMAGES_COLLECTION,
            count_filter=filt,
            exact=True,
        )
        count = result.count

        if scope != "pending":
            await self._qc.delete_vectors(
                collection_name=IMAGES_COLLECTION,
                vectors=["embedding"],
                points=qm.FilterSelector(filter=filt),
            )

        payload = {"embedding_status": "pending", "wd14_tags": []} if scope != "pending" else {"wd14_tags": []}
        await self._qc.set_payload(
            collection_name=IMAGES_COLLECTION,
            payload=payload,
            points=qm.FilterSelector(filter=filt),
        )
        return count

    # ── Analyzer / UMAP ──────────────────────────────────────────────────────────

    async def get_all_embeddings(self) -> list[tuple[str, list[float]]]:
        """Return (sha256, embedding_vector) for all done images."""
        results: list[tuple[str, list[float]]] = []
        offset = None
        filt = qm.Filter(must=[
            qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="done"))
        ])
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=filt,
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256"]),
                with_vectors=["embedding"],
            )
            for p in points:
                sha256 = p.payload.get("sha256")
                vec = p.vector.get("embedding") if p.vector else None  # type: ignore[union-attr]
                if sha256 and vec:
                    results.append((sha256, vec))
            if next_offset is None:
                break
            offset = next_offset
        return results

    async def set_umap_coords(
        self,
        coords: dict[str, tuple[float, float]],
        on_progress: "Callable[[int], None] | None" = None,
    ) -> None:
        """Bulk-write umap_x / umap_y using concurrent set_payload (50 parallel)."""
        items = list(coords.items())
        sem = asyncio.Semaphore(50)
        saved = 0
        lock = asyncio.Lock()

        async def _one(sha256: str, xy: tuple[float, float]) -> None:
            nonlocal saved
            async with sem:
                point_id = sha256_to_point_id(sha256)
                await self._qc.set_payload(
                    collection_name=IMAGES_COLLECTION,
                    payload={"umap_x": xy[0], "umap_y": xy[1]},
                    points=qm.PointIdsList(points=[point_id]),
                    wait=False,
                )
            async with lock:
                saved += 1
                if on_progress:
                    on_progress(saved)

        # Gather in batches of 1000 — processing all at once would bloat WAL queue and coroutine memory
        for i in range(0, len(items), 1000):
            await asyncio.gather(*[_one(s, xy) for s, xy in items[i:i + 1000]])

    async def scroll_umap_points(self) -> list[dict]:
        """Return all points that have umap_x computed."""
        docs: list[dict] = []
        offset = None
        filt = qm.Filter(must=[
            qm.FieldCondition(key="umap_x", range=qm.Range(gte=-1e9))
        ])
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=filt,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(
                    include=["sha256", "umap_x", "umap_y", "palette_hex", "name"]
                ),
                with_vectors=False,
            )
            docs.extend(p.payload for p in points)
            if next_offset is None:
                break
            offset = next_offset
        return docs

    async def scroll_umap_points_with_tags(self) -> list[dict]:
        """Return all points with umap coordinates and wd14_tags for cluster analysis."""
        docs: list[dict] = []
        offset = None
        filt = qm.Filter(must=[
            qm.FieldCondition(key="umap_x", range=qm.Range(gte=-1e9))
        ])
        while True:
            points, next_offset = await self._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=filt,
                limit=1000,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(
                    include=["sha256", "umap_x", "umap_y", "wd14_tags"]
                ),
                with_vectors=False,
            )
            docs.extend(p.payload for p in points)
            if next_offset is None:
                break
            offset = next_offset
        return docs

    async def delete_all_images(self) -> int:
        count = await self.total_count()
        if count > 0:
            await self._qc.delete(
                collection_name=IMAGES_COLLECTION,
                points_selector=qm.FilterSelector(filter=qm.Filter()),
            )
        return count

    # ── Config ───────────────────────────────────────────────────────────────────

    async def get_config(self) -> dict:
        results = await self._qc.retrieve(
            collection_name=CONFIG_COLLECTION,
            ids=[CONFIG_POINT_ID],
            with_payload=True,
        )
        return results[0].payload if results else {}

    async def put_config(self, data: dict) -> None:
        exists = await self._qc.retrieve(
            collection_name=CONFIG_COLLECTION,
            ids=[CONFIG_POINT_ID],
            with_payload=False,
        )
        if not exists:
            await self._qc.upsert(
                collection_name=CONFIG_COLLECTION,
                points=[qm.PointStruct(id=CONFIG_POINT_ID, vector={}, payload=data)],
            )
        else:
            # set_payload merges with existing keys; overwrite_payload would destroy
            # fields written by other callers (e.g. umap_computed_at wipes saved config).
            await self._qc.set_payload(
                collection_name=CONFIG_COLLECTION,
                payload=data,
                points=qm.PointIdsList(points=[CONFIG_POINT_ID]),
            )

    # ── Alignment ─────────────────────────────────────────────────────────────────

    async def _create_alignment_indexes(self) -> None:
        await self._qc.create_payload_index(
            collection_name=ALIGNMENT_COLLECTION,
            field_name="image_id",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=ALIGNMENT_COLLECTION,
            field_name="status",
            field_schema=qm.PayloadSchemaType.KEYWORD,
        )
        await self._qc.create_payload_index(
            collection_name=ALIGNMENT_COLLECTION,
            field_name="evaluated_at",
            field_schema=qm.PayloadSchemaType.DATETIME,
        )
        await self._qc.create_payload_index(
            collection_name=ALIGNMENT_COLLECTION,
            field_name="score",
            field_schema=qm.PayloadSchemaType.FLOAT,
        )

    async def upsert_alignment(self, sha256: str, record: dict) -> None:
        point_id = sha256_to_point_id(sha256)
        await self._qc.upsert(
            collection_name=ALIGNMENT_COLLECTION,
            points=[qm.PointStruct(id=point_id, vector={}, payload=record)],
        )

    async def get_alignment(self, sha256: str) -> dict | None:
        point_id = sha256_to_point_id(sha256)
        results = await self._qc.retrieve(
            collection_name=ALIGNMENT_COLLECTION,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        return results[0].payload if results else None

    async def get_alignments_batch(self, sha256s: list[str]) -> dict[str, dict]:
        """Batch-fetch alignment records for multiple sha256s. sha256s with no record are omitted from the result."""
        if not sha256s:
            return {}
        id_to_sha256 = {sha256_to_point_id(s): s for s in sha256s}
        results = await self._qc.retrieve(
            collection_name=ALIGNMENT_COLLECTION,
            ids=list(id_to_sha256.keys()),
            with_payload=True,
            with_vectors=False,
        )
        return {
            id_to_sha256[str(pt.id)]: pt.payload
            for pt in results
            if pt.payload and str(pt.id) in id_to_sha256
        }

    async def scroll_alignment_sha256s(self) -> set[str]:
        """Return the set of image_id values in the alignment collection (for batch skip logic)."""
        sha256s: set[str] = set()
        offset = None
        while True:
            pts, next_offset = await self._qc.scroll(
                collection_name=ALIGNMENT_COLLECTION,
                limit=500,
                with_payload=["image_id"],
                with_vectors=False,
                offset=offset,
            )
            for p in pts:
                if p.payload and "image_id" in p.payload:
                    sha256s.add(p.payload["image_id"])
            if next_offset is None:
                break
            offset = next_offset
        return sha256s

    async def get_alignment_sorted_sha256s(self) -> list[str]:
        """Return image_ids sorted by alignment score DESC (for align_desc sort)."""
        pairs: list[tuple[float, str]] = []
        offset = None
        while True:
            pts, next_offset = await self._qc.scroll(
                collection_name=ALIGNMENT_COLLECTION,
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="status", match=qm.MatchValue(value="done")),
                    qm.FieldCondition(key="score", range=qm.Range(gte=0.0)),
                ]),
                limit=500,
                with_payload=["image_id", "score"],
                with_vectors=False,
                offset=offset,
            )
            for p in pts:
                if p.payload and "image_id" in p.payload and p.payload.get("score") is not None:
                    pairs.append((p.payload["score"], p.payload["image_id"]))
            if next_offset is None:
                break
            offset = next_offset
        pairs.sort(key=lambda x: -x[0])
        return [sha256 for _, sha256 in pairs]

    async def get_aligned_sha256s(self, min_score: float) -> set[str]:
        """Return image_ids whose alignment score >= min_score."""
        sha256s: set[str] = set()
        offset = None
        while True:
            pts, next_offset = await self._qc.scroll(
                collection_name=ALIGNMENT_COLLECTION,
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="status", match=qm.MatchValue(value="done")),
                    qm.FieldCondition(key="score", range=qm.Range(gte=min_score)),
                ]),
                limit=500,
                with_payload=["image_id"],
                with_vectors=False,
                offset=offset,
            )
            for p in pts:
                if p.payload and "image_id" in p.payload:
                    sha256s.add(p.payload["image_id"])
            if next_offset is None:
                break
            offset = next_offset
        return sha256s

import asyncio
import logging

from fastapi import APIRouter, Request
from qdrant_client import models as qm

from ..db.qdrant_client import IMAGES_COLLECTION
from ..ai.umap_reducer import analyzer_umap_state
from ..ai.tag_analyzer import build_tag_cooccurrence
from ..runtime_config import get_runtime_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyzer")


# ── Semantic Map (UMAP) ───────────────────────────────────────────────────────

@router.get("/umap/status")
async def umap_status(request: Request):
    db = request.app.state.db

    # restore from Qdrant config if in-memory state was lost after restart
    # fallback: check for points with umap_x if not in config
    if analyzer_umap_state.get("computed_at") is None and not analyzer_umap_state.get("running"):
        cfg = await db.get_config()
        computed_at = cfg.get("umap_computed_at")
        if not computed_at:
            count = await db._qc.count(
                collection_name=IMAGES_COLLECTION,
                count_filter=qm.Filter(must=[
                    qm.FieldCondition(key="umap_x", range=qm.Range(gte=-1e9))
                ]),
                exact=False,
            )
            if count.count > 0:
                computed_at = "unknown"
                await db.put_config({"umap_computed_at": computed_at})
        if computed_at:
            analyzer_umap_state["computed_at"] = computed_at

    total = await db.total_count()
    covered = await db.count_with_embedding()
    return {
        "computed": analyzer_umap_state.get("computed_at") is not None,
        "running": analyzer_umap_state.get("running", False),
        "total": total,
        "covered": covered,
        "done": analyzer_umap_state.get("done", 0),
        "computed_at": analyzer_umap_state.get("computed_at"),
        "error": analyzer_umap_state.get("error"),
    }


@router.post("/umap/analyze")
async def trigger_umap_analyze(request: Request):
    from ..jobs.runners import run_analyze_umap
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(JobLane.SYNC, "umap_analyze", run_analyze_umap, db=db)
    return {"status": "queued", "job_id": job_id}


@router.get("/umap/clusters")
async def get_umap_clusters(request: Request, k: int = 10):
    db = request.app.state.db
    docs = await db.scroll_umap_points_with_tags()
    if len(docs) < max(k, 2):
        return {"clusters": [], "point_clusters": {}}

    cfg = await get_runtime_config(db)
    noise_tags = set(cfg.get("graph_noise_tags", []))
    common_tags = set(cfg.get("cluster_common_tags", []))

    def _cluster(docs, noise_tags, common_tags):
        import math
        import numpy as np
        from sklearn.cluster import KMeans
        from collections import Counter

        sha256s = [d["sha256"] for d in docs]
        coords = np.array([[d["umap_x"], d["umap_y"]] for d in docs], dtype=np.float32)
        k_actual = min(k, len(docs))
        km = KMeans(n_clusters=k_actual, n_init=5, random_state=42)
        labels = km.fit_predict(coords)

        n_docs = len(docs)
        tag_doc_count: Counter = Counter()
        for doc in docs:
            for t in set(doc.get("wd14_tags") or []):
                tag_doc_count[t] += 1

        exclude = noise_tags | common_tags
        clusters = []
        for cid in range(k_actual):
            mask = labels == cid
            cluster_docs = [docs[i] for i in range(n_docs) if mask[i]]
            cluster_size = len(cluster_docs)
            tag_freq: Counter = Counter(
                t for d in cluster_docs for t in (d.get("wd14_tags") or [])
            )

            # Log-Odds Ratio: P(t|C) vs P(t|not-C) to extract cluster-specific tags
            # Monroe et al. (2008) approach — tags common to all clusters score ~0
            # Jeffreys prior (add-0.5) for smoothing
            size_out = n_docs - cluster_size
            min_doc_req = max(2, int(cluster_size * 0.15))
            log_odds: dict[str, float] = {}
            for t, cnt in tag_freq.items():
                if t in exclude or cnt < min_doc_req:
                    continue
                p_in  = (cnt + 0.5) / (cluster_size + 1)
                cnt_out = tag_doc_count[t] - cnt
                p_out = (cnt_out + 0.5) / (size_out + 1) if size_out > 0 else 1.0
                log_odds[t] = math.log(p_in / p_out)
            distinctive = sorted(log_odds, key=lambda t: -log_odds[t])[:5]

            cx, cy = km.cluster_centers_[cid]
            clusters.append({
                "id": cid,
                "centroid_x": float(cx),
                "centroid_y": float(cy),
                "distinctive_tags": distinctive,
                "count": int(mask.sum()),
            })
        return {
            "clusters": clusters,
            "point_clusters": {sha256s[i]: int(labels[i]) for i in range(n_docs)},
            "point_tags": {d["sha256"]: d.get("wd14_tags") or [] for d in docs},
        }

    return await asyncio.get_event_loop().run_in_executor(None, _cluster, docs, noise_tags, common_tags)


@router.get("/umap")
async def get_umap_points(request: Request):
    db = request.app.state.db
    points = await db.scroll_umap_points()
    return {
        "points": [
            {
                "sha256": p.get("sha256"),
                "x": p.get("umap_x"),
                "y": p.get("umap_y"),
                "hex": (p.get("palette_hex") or ["#888888"])[0],
                "name": p.get("name", ""),
            }
            for p in points
            if p.get("umap_x") is not None
        ],
        "total": len(points),
    }


# ── Color Space (3D Lab*) ─────────────────────────────────────────────────────

@router.get("/color-3d")
async def get_color_3d(request: Request, limit: int = 5000):
    db = request.app.state.db
    points = await db.scroll_color_lab_points(limit=limit)
    pending_count = await db.count_pending_color_extraction()
    total = await db.total_count()
    backfill_needed = pending_count > 50 or (total > 0 and len(points) == 0)
    return {
        "points": points,
        "total": len(points),
        "pending_count": pending_count,
        "backfill_needed": backfill_needed,
    }


# ── Tag Network ───────────────────────────────────────────────────────────────

@router.get("/tag-network")
async def get_tag_network(
    request: Request,
    min_count: int = 2,
    top_tags: int = 80,
):
    db = request.app.state.db
    result = await build_tag_cooccurrence(db, min_count=min_count, top_tags=top_tags)
    return result


@router.post("/tag-taxonomy")
async def tag_taxonomy_submit(request: Request):
    """Classify tags by category using an LLM. Submits a job to the EVALUATION lane."""
    from ..jobs.runners import run_tag_taxonomy
    from ..spooler.models import JobLane
    body = await request.json()
    tags: list[str] = body.get("tags", [])
    model: str | None = body.get("model")
    spooler = request.app.state.spooler
    ollama = request.app.state.ollama
    job_id = spooler.submit(
        JobLane.EVALUATION,
        "tag_taxonomy",
        run_tag_taxonomy,
        ollama=ollama,
        tags=tags,
        model=model,
        priority=0,
    )
    return {"status": "queued", "job_id": job_id}


# ── Dataset Health ────────────────────────────────────────────────────────────

@router.get("/health")
async def get_health(request: Request):
    db = request.app.state.db

    total, with_embedding, with_color, with_prompt, models = await asyncio.gather(
        db.total_count(),
        db.count_with_embedding(),
        db.count_with_color_vector(),
        db._qc.count(
            collection_name=IMAGES_COLLECTION,
            count_filter=qm.Filter(must_not=[
                qm.IsEmptyCondition(is_empty=qm.PayloadField(key="positive_prompt"))
            ]),
            exact=True,
        ),
        db.scroll_model_facets(),
    )

    return {
        "total": total,
        "with_embedding": with_embedding,
        "with_color": with_color,
        "with_prompt": with_prompt.count,
        "models": models,
        "coverage": {
            "embedding_pct": round(with_embedding / total * 100, 1) if total else 0,
            "color_pct": round(with_color / total * 100, 1) if total else 0,
            "prompt_pct": round(with_prompt.count / total * 100, 1) if total else 0,
        },
    }

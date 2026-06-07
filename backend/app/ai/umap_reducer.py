import asyncio
import logging
import pickle
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

UMAP_MODEL_PATH = Path("/models/umap_model.pkl")

# In-memory cache — populated after full recompute or lazy-loaded from disk
_umap_bundle: dict | None = None

# Numba's workqueue threading layer is not thread-safe; serialize all calls into it
_umap_lock = threading.Lock()

analyzer_umap_state: dict = {
    "running": False,
    "phase": None,   # "fetch" | "pca" | "umap" | "saving" | None
    "total": 0,
    "done": 0,
    "computed_at": None,
    "error": None,
}


def umap_has_model() -> bool:
    return _umap_bundle is not None or UMAP_MODEL_PATH.exists()


def _load_bundle() -> dict | None:
    global _umap_bundle
    if _umap_bundle is not None:
        return _umap_bundle
    if not UMAP_MODEL_PATH.exists():
        return None
    try:
        with UMAP_MODEL_PATH.open("rb") as f:
            _umap_bundle = pickle.load(f)
        return _umap_bundle
    except Exception as e:
        logger.warning("Failed to load UMAP model: %s", e)
        return None


def umap_transform_one_sync(embedding: list[float]) -> tuple[float, float] | None:
    """Project a single embedding into the existing UMAP space (sync, call via executor).

    Returns normalized (x, y) using the same min/max as the full-compute run.
    May return values slightly outside [0,1] for out-of-distribution points.
    """
    bundle = _load_bundle()
    if bundle is None:
        return None
    try:
        vec = np.array([embedding], dtype=np.float32)
        reduced = bundle["pca"].transform(vec)
        with _umap_lock:
            coords = bundle["umap"].transform(reduced)
        xy = (coords[0] - bundle["c_min"]) / bundle["c_range"]
        return float(xy[0]), float(xy[1])
    except Exception as e:
        logger.warning("umap_transform_one failed: %s", e)
        return None


async def run_umap_analysis(db, cancel_fn: Callable[[], bool]) -> None:
    global _umap_bundle
    analyzer_umap_state.update({"running": True, "phase": "fetch", "total": 0, "done": 0, "error": None})

    loop = asyncio.get_event_loop()

    try:
        # Phase 1: Fetch embedding vectors
        pairs = await db.get_all_embeddings()
        if not pairs:
            analyzer_umap_state["error"] = "No embeddings found"
            return

        sha256s = [p[0] for p in pairs]
        vectors = np.array([p[1] for p in pairs], dtype=np.float32)
        n = len(sha256s)
        analyzer_umap_state["total"] = n

        if n > 10_000:
            analyzer_umap_state["error"] = f"Datasets larger than 10,000 items are not yet supported. (current: {n:,} items)"
            return

        if cancel_fn():
            return

        # Phase 2 & 3: PCA → UMAP (executed in thread pool)
        def _compute(vecs: np.ndarray):
            from sklearn.decomposition import PCA
            import umap

            analyzer_umap_state["phase"] = "pca"
            n_components = min(50, vecs.shape[1], vecs.shape[0] - 1)
            pca = PCA(n_components=n_components)
            reduced = pca.fit_transform(vecs)

            analyzer_umap_state["phase"] = "umap"
            n_neighbors = min(15, len(reduced) - 1)
            umap_model = umap.UMAP(
                n_neighbors=n_neighbors,
                min_dist=0.1,
                metric="cosine",
                random_state=42,
            )
            with _umap_lock:
                coords = umap_model.fit_transform(reduced).astype(np.float32)

            c_min = coords.min(axis=0)
            c_max = coords.max(axis=0)
            c_range = np.where(c_max - c_min > 0, c_max - c_min, 1.0)

            return coords, pca, umap_model, c_min, c_range

        coords, pca, umap_model, c_min, c_range = await loop.run_in_executor(None, _compute, vectors)

        if cancel_fn():
            return

        # Normalize to [0, 1]
        coords_norm = (coords - c_min) / c_range

        # Save model to disk and memory
        bundle = {"pca": pca, "umap": umap_model, "c_min": c_min, "c_range": c_range}

        def _save_model():
            UMAP_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with UMAP_MODEL_PATH.open("wb") as f:
                pickle.dump(bundle, f)

        await loop.run_in_executor(None, _save_model)
        _umap_bundle = bundle

        # Phase 4: Save to DB (batched: batch_update_points in units of 200)
        analyzer_umap_state["phase"] = "saving"
        analyzer_umap_state["done"] = 0
        coords_map = {
            sha256s[i]: (float(coords_norm[i, 0]), float(coords_norm[i, 1]))
            for i in range(n)
        }
        def _on_progress(saved: int) -> None:
            analyzer_umap_state["done"] = saved

        await db.set_umap_coords(coords_map, on_progress=_on_progress)
        analyzer_umap_state["done"] = n

        computed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        analyzer_umap_state["computed_at"] = computed_at
        analyzer_umap_state["phase"] = None
        await db.put_config({"umap_computed_at": computed_at})

    except Exception as e:
        logger.exception("UMAP analysis failed")
        analyzer_umap_state["error"] = str(e)
        raise
    finally:
        analyzer_umap_state["running"] = False

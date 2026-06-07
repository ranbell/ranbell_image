from __future__ import annotations
from typing import TYPE_CHECKING

from .config import settings

if TYPE_CHECKING:
    from .core.runtime_cache import RuntimeConfigCache

CONFIG_ID = "app_config"

_defaults = {
    "embed_model":          settings.embed_model,
    "vlm_model":            settings.vlm_model,
    "wd14_threshold":       settings.wd14_threshold,
    "wd14_model_dir":       settings.wd14_model_dir,
    "ollama_url":           settings.ollama_url,
    "scan_extensions":      [".png", ".jpg", ".jpeg", ".webp"],
    "pipeline_batch_size":  5000,
    "pipeline_concurrency": 4,
    "tags_cache_ttl":       60,
    "graph_noise_tags": [
        "watermark", "text", "signature", "username", "artist name",
        "bad anatomy", "bad hands", "extra legs", "fewer legs",
        "extra arms", "fewer arms", "extra fingers", "missing fingers",
        "absurdres", "huge filesize", "lowres", "low quality",
        "score_4_up", "score_5_up", "score_6_up", "score_7_up", "score_8_up",
        "masterpiece", "best quality", "high quality", "highres",
        "worst quality", "normal quality",
    ],
    "prompt_removal_tags": [],
    "ollama_num_ctx":          16384,
    "frozenset_classification": True,
    # GPU priority control
    "auto_pause_on_generation": True,
    "auto_pause_lanes":         ["embed", "eval"],
    "auto_alignment_evaluate":  False,
    # Processing parallelism
    "alignment_concurrency":    1,
    "pipeline_auto_continue":   True,
}

_cache: RuntimeConfigCache | None = None


def set_cache(cache: RuntimeConfigCache) -> None:
    global _cache
    _cache = cache


def invalidate_cache() -> None:
    if _cache is not None:
        _cache.invalidate()


async def get_runtime_config(db) -> dict:
    if _cache is not None:
        return await _cache.get(db)
    doc = await db.get_config()
    return {k: doc.get(k, v) for k, v in _defaults.items()}

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
    "llm_provider":         settings.llm_provider,
    "openai_base_url":      settings.openai_base_url,
    "openai_api_key":       settings.openai_api_key,
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
    # Invoke / Genesis
    "invoke_gold_frame_threshold":     0.85,
    "invoke_show_monologue":           True,
    "invoke_daily_oracle_enabled":       False,
    "invoke_daily_oracle_workflow":      "",
    "invoke_daily_oracle_retain_days":   7,
    "invoke_daily_oracle_time":          "00:00",
    "invoke_daily_oracle_timezone":      "UTC",
    "invoke_daily_oracle_topic":         "",
    "invoke_daily_oracle_roulette":      False,
    "invoke_daily_oracle_min_free_gb":   5.0,
    # Disk gauge thresholds (used_pct %)
    "disk_caution_pct":                  75,
    "disk_fault_pct":                    90,
    # GPU priority control
    "auto_pause_on_generation": True,
    "auto_pause_lanes":         ["embed", "eval"],
    # tier2: pause EVALUATION while gen/prompt/embed are active.
    # Set False only when Ollama runs on a different GPU than ComfyUI.
    "eval_auto_pause":          True,
    "auto_alignment_evaluate":  False,
    # WD14 tag weighting for refine (common/unique decomposition)
    "wd14_common_ratio":         0.3,
    "wd14_unique_count":         20,
    # Processing parallelism
    "alignment_concurrency":    2,
    "pipeline_auto_continue":   True,
    "scan_concurrency":         8,
    "umap_max_points":          20_000,
    # Max results returned by natural-language semantic search
    "semantic_search_limit":    100,
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

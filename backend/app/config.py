from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    embed_dim: int = 768       # must match the embed model output dimension
    embed_dim_small: int = 256  # MRL truncation for phase-1 prefetch (must be <= embed_dim)

    # Storage (container-internal fixed paths — configure host mounts in docker-compose.override.yml)
    source_images_dir: Path = Path("/mnt/image/source")    # read-only, recursive scan
    generated_images_dir: Path = Path("/mnt/image/generated")  # writable, AI output
    thumbnails_dir: Path = Path("/mnt/thumbnails")
    thumbnail_size: int = 300

    # Ollama
    ollama_url: str = "http://host.docker.internal:11434"
    embed_model: str = "nomic-embed-text"
    vlm_model: str = "gemma3:4b"

    # WD14 tagger
    wd14_model_dir: str = "/mnt/models/wd14"
    wd14_threshold: float = 0.35

    # ComfyUI
    comfyui_url: str = "http://host.docker.internal:8188"
    comfyui_workflows_dir: str = "/mnt/comfy/workflows"

    # API authentication
    api_token: str = "RANBELL_IMAGE_API_TOKEN"

    # File watcher
    watch_debounce_seconds: float = 5.0
    auto_ai_pipeline: bool = True

    # Resource pool (GPU contention control)
    # Concurrency for local-gpu0 (normally no need to change from 1)
    resource_local_gpu0_concurrency: int = 1
    # Only set if offloading remote Ollama to a separate machine
    resource_remote_ollama_endpoint: str | None = None
    resource_remote_ollama_concurrency: int = 1
    resource_remote_ollama_health_path: str = "/api/version"
    resource_remote_qdrant_health_path: str = "/healthz"

    model_config = {"env_file": ".env"}


settings = Settings()

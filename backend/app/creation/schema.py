from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class SourceImageRef(BaseModel):
    sha256: str
    weight: float


class InspireContext(BaseModel):
    mode: str
    add_sha256s: list[str] = []
    sub_sha256s: list[str] = []
    sha256_a: str | None = None
    sha256_b: str | None = None
    axes: list[str] = []
    injected_tags: list[str] = []


class CreationRecord(BaseModel):
    version: str = "1.0"
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    method: str = "refine"
    instruction: str = ""
    instruction_mode: str = "none"
    prompt_style: str = ""
    temperature: float | None = None
    num_ctx: int | None = None
    workflow_name: str = ""
    batch_count: int = 1
    positive_prompt_generated: str = ""
    negative_prompt_generated: str = ""
    direct_prompt: bool = False
    source_images: list[SourceImageRef] = []
    inspire_context: InspireContext | None = None
    text_directives: list[dict] = []
    parent_job_id: str = ""
    seed: int | None = None

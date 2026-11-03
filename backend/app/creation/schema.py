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
    # Sampler settings, when the feature chose them rather than taking the
    # workflow's. A Muse board and its final render use the same workflow at
    # very different settings, so without these the record cannot tell a
    # 512px 16-step draft from the full-price image beside it.
    steps: int | None = None
    cfg: float | None = None
    width: int | None = None
    height: int | None = None
    # Detailed style (Krea 2): the scene sheet the prompt was written from,
    # so a later run can start from it (edit it, or breed from rated images).
    scene_sheet: dict | None = None
    # Detailed style: what was read, what each step changed, and the sheet the
    # script was finally written from ({"read", "steps", "current"}).
    scene_ledger: dict | None = None
    # MOSS-Image style: "caption" / "layout" / "tags" ("" for other styles).
    moss_format: str = ""
    # Detailed style: the references' top WD14 tags were handed to the sheet reader.
    wd14_hints: bool = False
    # Detailed style: how far the sheet was staged ("read" / "move" / "restage").
    staging: str = ""
    # Detailed style: what was done to the objects ("keep" / "simplify" / "elaborate").
    props_mode: str = ""

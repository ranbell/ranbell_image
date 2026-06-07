from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class AlignmentRecord(BaseModel):
    image_id: str
    evaluated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    evaluator_version: str = "2.0"
    score: float | None = None
    score_method: str = "embedding_similarity_v1"
    # Legacy single-language fields kept for backward compatibility (ja)
    summary: str = ""
    matched_elements: list[str] = []
    unmatched_elements: list[str] = []
    categories: list[str] = []
    # Multilingual content: {"ja": "...", "en": "...", "zh": "...", ...}
    summary_i18n: dict[str, str] = {}
    matched_elements_i18n: dict[str, list[str]] = {}
    unmatched_elements_i18n: dict[str, list[str]] = {}
    status: Literal["done", "skipped", "error"] = "done"


class AlignmentRequest(BaseModel):
    sha256s: list[str] = []


class AlignmentResult(BaseModel):
    sha256: str
    status: Literal["done", "skipped", "error"]
    score: float | None = None
    summary: str = ""
    matched_elements: list[str] = []
    unmatched_elements: list[str] = []
    categories: list[str] = []
    summary_i18n: dict[str, str] = {}
    matched_elements_i18n: dict[str, list[str]] = {}
    unmatched_elements_i18n: dict[str, list[str]] = {}
    evaluated_at: str | None = None

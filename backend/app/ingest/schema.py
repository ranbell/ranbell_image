from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ExtractionInfo:
    method: str
    """a1111 | ksampler_trace | direct_search | longest_text | failed"""
    confidence: str
    """high | medium | low"""
    warnings: list[str] = field(default_factory=list)
    extracted_at: str = ""
    extractor_version: str = "1"

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "extracted_at": self.extracted_at,
            "extractor_version": self.extractor_version,
        }


@dataclass
class ModelInfo:
    model_name: str | None = None
    model_hash: str | None = None
    sampler: str | None = None
    steps: int | None = None
    cfg_scale: float | None = None
    seed: int | None = None

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "model_hash": self.model_hash,
            "sampler": self.sampler,
            "steps": self.steps,
            "cfg_scale": self.cfg_scale,
            "seed": self.seed,
        }


@dataclass
class RawMetadata:
    format: str
    """comfyui | a1111 | unknown"""
    content: dict | str
    hash: str

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "content": self.content,
            "hash": self.hash,
        }


@dataclass
class ExtractionResult:
    positive_prompt: str | None
    negative_prompt: str | None
    model_info: ModelInfo
    extraction: ExtractionInfo
    raw_metadata: RawMetadata
    params: dict = field(default_factory=dict)
    """Additional generation parameters (sampler, steps, cfg, etc.)"""

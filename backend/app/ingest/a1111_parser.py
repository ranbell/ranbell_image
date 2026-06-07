from __future__ import annotations

from .schema import ExtractionInfo, ExtractionResult, ModelInfo, RawMetadata
from ._utils import compute_hash, now_iso


def parse_a1111(text: str) -> ExtractionResult:
    lines = text.strip().split("\n")
    positive: list[str] = []
    negative: list[str] = []
    params: dict[str, str] = {}
    mode = "positive"

    for line in lines:
        if line.startswith("Negative prompt:"):
            mode = "negative"
            rest = line[len("Negative prompt:"):].strip()
            if rest:
                negative.append(rest)
        elif mode in ("positive", "negative") and _is_param_line(line):
            mode = "params"
            _extract_params(line, params)
        elif mode == "params":
            _extract_params(line, params)
        elif mode == "positive":
            positive.append(line)
        else:
            negative.append(line)

    positive_prompt = "\n".join(positive).strip() or None
    negative_prompt = "\n".join(negative).strip() or None

    model_info = ModelInfo(
        model_name=params.get("Model") or None,
        model_hash=params.get("Model hash") or None,
        sampler=params.get("Sampler") or None,
        steps=_to_int(params.get("Steps")),
        cfg_scale=_to_float(params.get("CFG scale")),
        seed=_to_int(params.get("Seed")),
    )

    return ExtractionResult(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        model_info=model_info,
        extraction=ExtractionInfo(
            method="a1111",
            confidence="high",
            extracted_at=now_iso(),
        ),
        raw_metadata=RawMetadata(
            format="a1111",
            content=text,
            hash=compute_hash(text),
        ),
        params=params,
    )


def _is_param_line(line: str) -> bool:
    return any(k in line for k in ("Steps:", "Sampler:", "CFG scale:", "Size:"))


def _extract_params(line: str, params: dict) -> None:
    for item in line.split(","):
        item = item.strip()
        if ": " in item:
            key, _, val = item.partition(": ")
            params[key.strip()] = val.strip()


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

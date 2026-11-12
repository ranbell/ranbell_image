"""Several sheets read as one: what they share, what sets each apart, and the
columns they line up on.

Stacking sheets is for seeing one difference at a time — the base model, the
seed, the prompt — so each sheet's heading names only what differs from the
others, and what they all share is said once.
"""
from __future__ import annotations

from typing import Any

from . import sheet as sh

#: (field, label) in the order headings read them.
FIELDS: list[tuple[str, str]] = [
    ("unet", "model"), ("clip", "te"), ("vae", "vae"), ("seed", "seed"), ("size", "size"),
    ("steps", "steps"), ("cfg", "cfg"), ("sampling", "sampler"), ("prompt", "prompt"),
]


def field_value(sheet: dict, field: str) -> Any:
    if field in ("unet", "clip", "vae"):
        return (sheet.get("models") or {}).get(field, "")
    if field == "size":
        return f"{sheet['width']}x{sheet['height']}"
    if field == "sampling":
        return f"{sheet['sampler']}/{sheet['scheduler']}"
    return sheet.get(field)


def show_value(field: str, value: Any) -> str:
    if field in ("unet", "clip", "vae"):
        return sh.short_model(str(value))
    if field == "cfg":
        return f"{float(value):g}"
    return str(value)


def diff_fields(sheets: list[dict]) -> tuple[list[str], list[list[str]]]:
    """(the fields every sheet shares, the differing fields per sheet), as
    labelled text: ``(["model …", "seed 42"], [["seed 1"], ["seed 2"]])``."""
    common: list[str] = []
    per: list[list[str]] = [[] for _ in sheets]
    for field, label in FIELDS:
        values = [field_value(s, field) for s in sheets]
        if all(v == values[0] for v in values):
            if field != "prompt":  # the prompt is said on its own line
                common.append(f"{label} {show_value(field, values[0])}")
            continue
        for i, v in enumerate(values):
            per[i].append(f"{label} {show_value(field, v)}")
    return common, per


def column_id(col: dict) -> tuple[float, bool]:
    return (round(float(col["strength"]), 4), bool(col["trigger_on"]))


def merged_columns(sheets: list[dict]) -> list[dict]:
    """Every column any sheet has: by strength, the no-trigger ones last."""
    seen: dict[tuple[float, bool], dict] = {}
    for s in sheets:
        for col in sh.columns(s):
            seen.setdefault(column_id(col), {"strength": col["strength"],
                                             "trigger_on": col["trigger_on"]})
    return sorted(seen.values(), key=lambda c: (not c["trigger_on"], c["strength"]))


def aligned_grid(sheet: dict, cols: list[dict]) -> list[list[str]]:
    """The sheet's render keys laid on ``cols``; '' where it has no such column."""
    own = [column_id(c) for c in sh.columns(sheet)]
    rows = sh.grid(sheet)
    out = []
    for keys in rows:
        by_col = dict(zip(own, keys))
        out.append([by_col.get(column_id(c), "") for c in cols])
    return out

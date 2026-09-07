"""Absolute shot ledger for Muse Refine.

One document. Missing keys leave the previous value. Present keys replace.
"""
from __future__ import annotations

from typing import Any

LEDGER_KEYS: tuple[str, ...] = (
    "wearing",
    "beat",
    "expression",
    "scene",
    "light",
    "bg",
    "frame",
)

DROP_KEYS: tuple[str, ...] = ("wearing_drop",)


def blank() -> dict[str, str]:
    return {k: "" for k in LEDGER_KEYS}


def normalize_patch(raw: dict[str, Any] | None) -> dict[str, str]:
    """Keep only known keys; coerce to stripped strings."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in (*LEDGER_KEYS, *DROP_KEYS):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if key == "wearing_drop" and not text:
            continue
        out[key] = text
    return out


def apply_patch(ledger: dict[str, str], patch: dict[str, str]) -> dict[str, str]:
    """Absolute merge. Empty string clears the field."""
    next_ledger = {**blank(), **{k: str(ledger.get(k) or "") for k in LEDGER_KEYS}}
    drop = str(patch.get("wearing_drop") or "").strip().lower()
    if drop:
        wearing = next_ledger.get("wearing") or ""
        parts = [p.strip() for p in wearing.replace(";", ",").split(",") if p.strip()]
        kept = [p for p in parts if drop not in p.lower()]
        next_ledger["wearing"] = ", ".join(kept)
    for key in LEDGER_KEYS:
        if key not in patch:
            continue
        next_ledger[key] = str(patch[key]).strip()
    return next_ledger


def now_line(ledger: dict[str, str], *, locale: str = "ja") -> str:
    """Plain-language NOW for the actress and the panel."""
    wearing = (ledger.get("wearing") or "").strip()
    beat = (ledger.get("beat") or "").strip()
    scene = (ledger.get("scene") or "").strip()
    light = (ledger.get("light") or "").strip()
    bg = (ledger.get("bg") or "").strip()
    place = scene or bg
    bits: list[str] = []
    if locale.startswith("ja"):
        if wearing:
            bits.append(f"服装: {wearing}")
        if beat:
            bits.append(f"姿勢: {beat}")
        if place:
            bits.append(f"場所: {place}")
        if light:
            bits.append(f"光: {light}")
        return " / ".join(bits) if bits else "（まだ画は決まっていない）"
    if wearing:
        bits.append(f"wearing {wearing}")
    if beat:
        bits.append(f"doing {beat}")
    if place:
        bits.append(f"at {place}")
    if light:
        bits.append(f"light {light}")
    return "; ".join(bits) if bits else "(shot not set yet)"


def touched_picture(patch: dict[str, str]) -> bool:
    if any(str(patch.get(k) or "").strip() for k in LEDGER_KEYS):
        return True
    return bool(str(patch.get("wearing_drop") or "").strip())

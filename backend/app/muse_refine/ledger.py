"""Absolute shot ledger for Muse Refine.

One document. Missing keys leave the previous value. Present keys replace.
"""
from __future__ import annotations

import re
from typing import Any

LEDGER_KEYS: tuple[str, ...] = (
    "wearing",
    "beat",
    "expression",
    "scene",
    "light",
    "bg",
    "frame",
    "wearing_b",
    "beat_b",
    "lettering",  # short Latin words for Anima text "…" / text_on_image
    "atmosphere",  # mood / air — conversation-driven (wistful, tense, cozy…)
    "look",  # art direction / render — cel, fantasy, watercolor… (not UI buttons)
)

DROP_KEYS: tuple[str, ...] = ("wearing_drop",)

# UI chips — short label + icon glyph (not used for model judgment).
FIELD_CHIPS: dict[str, dict[str, str]] = {
    "wearing": {"icon": "👕", "ja": "服", "en": "Clothes"},
    "beat": {"icon": "🧍", "ja": "姿勢", "en": "Pose"},
    "expression": {"icon": "😊", "ja": "表情", "en": "Face"},
    "scene": {"icon": "📍", "ja": "場所", "en": "Place"},
    "light": {"icon": "💡", "ja": "光", "en": "Light"},
    "bg": {"icon": "🏞", "ja": "背景", "en": "BG"},
    "frame": {"icon": "📷", "ja": "構図", "en": "Frame"},
    "wearing_b": {"icon": "👗", "ja": "相方服", "en": "Partner clothes"},
    "beat_b": {"icon": "🤝", "ja": "相方姿勢", "en": "Partner pose"},
    "lettering": {"icon": "🔤", "ja": "文字", "en": "Lettering"},
    "atmosphere": {"icon": "🌫", "ja": "雰囲気", "en": "Mood"},
    "look": {"icon": "🎨", "ja": "画風", "en": "Look"},
    "wearing_drop": {"icon": "🗑", "ja": "脱ぐ", "en": "Drop"},
}

# Soft cue that the director line is about the picture (retry writer if empty).
_PICTURE_CUES = re.compile(
    r"("
    r"着|服|シャツ|スカート|ワンピース|制服|パーカー|コート|帽子|靴|脱|"
    r"立|座|跪|寝|ポーズ|姿勢|表情|笑|泣|"
    r"場所|屋上|教室|公園|海|部屋|背景|光|照明|カメラ|構図|寄|引き|"
    r"看板|文字|テキスト|ボード|"
    r"雰囲気|空気|ムード|画風|タッチ|塗り|ファンタジー|エモ|セル|"
    r"wear|shirt|skirt|dress|uniform|hoodie|coat|hat|pose|stand|sit|"
    r"rooftop|classroom|park|beach|room|background|light|camera|frame|outfit|"
    r"sign|banner|lettering|textboard|atmosphere|mood|fantasy|watercolor|cel"
    r")",
    re.I,
)


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


def changed_fields(before: dict[str, str], after: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key in LEDGER_KEYS:
        if str(before.get(key) or "").strip() != str(after.get(key) or "").strip():
            out.append(key)
    return out


def patch_fields(patch: dict[str, str]) -> list[str]:
    keys = [k for k in LEDGER_KEYS if str(patch.get(k) or "").strip() or k in patch]
    # Only keys actually present in patch.
    keys = [k for k in LEDGER_KEYS if k in patch]
    if str(patch.get("wearing_drop") or "").strip():
        keys.append("wearing_drop")
    return keys


def chips_for(fields: list[str], *, locale: str = "ja") -> list[dict[str, str]]:
    ja = locale.startswith("ja")
    chips: list[dict[str, str]] = []
    for key in fields:
        meta = FIELD_CHIPS.get(key)
        if not meta:
            continue
        chips.append({
            "key": key,
            "icon": meta["icon"],
            "label": meta["ja"] if ja else meta["en"],
        })
    return chips


def now_line(ledger: dict[str, str], *, locale: str = "ja") -> str:
    """Plain-language NOW — all shot axes the actress must respect."""
    wearing = (ledger.get("wearing") or "").strip()
    beat = (ledger.get("beat") or "").strip()
    expression = (ledger.get("expression") or "").strip()
    scene = (ledger.get("scene") or "").strip()
    light = (ledger.get("light") or "").strip()
    bg = (ledger.get("bg") or "").strip()
    frame = (ledger.get("frame") or "").strip()
    bits: list[str] = []
    if locale.startswith("ja"):
        if wearing:
            bits.append(f"服装: {wearing}")
        if beat:
            bits.append(f"姿勢: {beat}")
        if expression:
            bits.append(f"表情: {expression}")
        if scene:
            bits.append(f"場所: {scene}")
        if bg and bg != scene:
            bits.append(f"背景: {bg}")
        if light:
            bits.append(f"光: {light}")
        if frame:
            bits.append(f"構図: {frame}")
        if atm := (ledger.get("atmosphere") or "").strip():
            bits.append(f"雰囲気: {atm}")
        if look := (ledger.get("look") or "").strip():
            bits.append(f"画風: {look}")
        return " / ".join(bits) if bits else "（まだ画は決まっていない）"
    if wearing:
        bits.append(f"wearing {wearing}")
    if beat:
        bits.append(f"pose {beat}")
    if expression:
        bits.append(f"face {expression}")
    if scene:
        bits.append(f"place {scene}")
    if bg and bg != scene:
        bits.append(f"bg {bg}")
    if light:
        bits.append(f"light {light}")
    if frame:
        bits.append(f"frame {frame}")
    if atm := (ledger.get("atmosphere") or "").strip():
        bits.append(f"mood {atm}")
    if look := (ledger.get("look") or "").strip():
        bits.append(f"look {look}")
    return "; ".join(bits) if bits else "(shot not set yet)"


def touched_picture(patch: dict[str, str]) -> bool:
    if any(k in patch for k in LEDGER_KEYS):
        return True
    return bool(str(patch.get("wearing_drop") or "").strip())


def looks_like_picture_line(text: str) -> bool:
    """Heuristic only — used to retry an empty writer, not to apply patches."""
    return bool(_PICTURE_CUES.search(text or ""))

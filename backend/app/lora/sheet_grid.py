"""The sheet as one labelled picture, to keep or to post next to a LoRA.

Strengths across the top, LoRA files down the side, and above them what was
held fixed (models, seed, sampling, prompt). The PNG carries no metadata —
like the dataset export, nothing in it says where the files live.
"""
from __future__ import annotations

import io
import textwrap
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from . import sheet as sh

CELL_W = 320
LABEL_W = 230
HEAD_H = 34
PAD = 12
BG = (17, 17, 20)
FG = (235, 235, 235)
DIM = (150, 150, 160)
EMPTY = (40, 40, 46)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except Exception:  # Pillow without FreeType
        return ImageFont.load_default()


def column_label(col: dict) -> str:
    s = col["strength"]
    if not col["trigger_on"]:
        return f"{s:g} · no trigger"
    return "0 · base" if not s else f"{s:g}"


def row_label(row: dict) -> str:
    if row.get("label"):
        return row["label"]
    name = row["lora"].rsplit("/", 1)[-1]
    return name[:-12] if name.endswith(".safetensors") else name


def _wrap(draw, text: str, font, width: int, max_lines: int) -> list[str]:
    # Rough character budget from the font's average width, then trim.
    avg = max(1.0, draw.textlength("abcdefghijklmnopqrstuvwxyz", font=font) / 26)
    lines = textwrap.wrap(text, width=max(10, int(width / avg))) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, len(lines[-1]) - 1)] + "…"
    return lines


def compose(sheet: dict, open_image: Callable[[str], Image.Image | None]) -> bytes:
    """Build the PNG. ``open_image(sha256)`` returns the picture or None."""
    cols = sh.columns(sheet)
    keys = sh.grid(sheet)
    renders = sheet.get("renders") or {}
    cell_h = max(1, round(CELL_W * sheet["height"] / sheet["width"]))

    big, small = _font(20), _font(15)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    width = LABEL_W + CELL_W * len(cols) + PAD
    m = sheet["models"]
    info = (f"{m['unet']}  ·  seed {sheet['seed']}  ·  {sheet['steps']} steps  ·  cfg {sheet['cfg']:g}"
            f"  ·  {sheet['sampler']}/{sheet['scheduler']}  ·  {sheet['width']}x{sheet['height']}")
    prompt_lines = _wrap(probe, sheet["prompt"], small, width - 2 * PAD, 4)
    top = PAD + 26 + 22 + 20 * len(prompt_lines) + PAD
    height = top + HEAD_H + cell_h * len(keys) + PAD

    out = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(out)
    y = PAD
    d.text((PAD, y), sheet["name"], font=big, fill=FG)
    y += 26
    d.text((PAD, y), info, font=small, fill=DIM)
    y += 22
    for line in prompt_lines:
        d.text((PAD, y), line, font=small, fill=DIM)
        y += 20

    for ci, col in enumerate(cols):
        x = LABEL_W + ci * CELL_W
        d.text((x + CELL_W // 2, top + HEAD_H // 2), column_label(col), font=big, fill=FG,
               anchor="mm")

    for ri, (row, row_keys) in enumerate(zip(sheet["rows"], keys)):
        y0 = top + HEAD_H + ri * cell_h
        label = _wrap(probe, row_label(row), big, LABEL_W - 2 * PAD, 3)
        ty = y0 + PAD
        for line in label:
            d.text((PAD, ty), line, font=big, fill=FG)
            ty += 24
        if row.get("trigger"):
            for line in _wrap(probe, row["trigger"], small, LABEL_W - 2 * PAD, 2):
                d.text((PAD, ty + 4), line, font=small, fill=DIM)
                ty += 19
        for ci, key in enumerate(row_keys):
            x0 = LABEL_W + ci * CELL_W
            r = renders.get(key) or {}
            im = open_image(r["sha256"]) if r.get("sha256") else None
            if im is None:
                d.rectangle((x0 + 2, y0 + 2, x0 + CELL_W - 3, y0 + cell_h - 3), fill=EMPTY)
                mark = "error" if r.get("status") == sh.ERROR else "—"
                d.text((x0 + CELL_W // 2, y0 + cell_h // 2), mark, font=small, fill=DIM, anchor="mm")
                continue
            with im:
                tile = im.convert("RGB").resize((CELL_W - 4, cell_h - 4), Image.LANCZOS)
            out.paste(tile, (x0 + 2, y0 + 2))

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()

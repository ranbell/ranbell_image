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
ACCENT = (240, 190, 110)


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
    m = sheet["models"]
    info = (f"{m['unet']}  ·  seed {sheet['seed']}  ·  {sheet['steps']} steps  ·  cfg {sheet['cfg']:g}"
            f"  ·  {sheet['sampler']}/{sheet['scheduler']}  ·  {sheet['width']}x{sheet['height']}")
    return _draw(sheet["name"], info, sheet["prompt"], sh.columns(sheet),
                 [_section(sheet, "", sh.grid(sheet))], open_image)


def compose_stack(sheets: list[dict], open_image: Callable[[str], Image.Image | None]) -> bytes:
    """Several sheets stacked on shared strength columns. The header says what
    they all share; each section's heading says what sets that sheet apart."""
    from . import sheet_compare as cmp

    common, per = cmp.diff_fields(sheets)
    cols = cmp.merged_columns(sheets)
    prompts = {s["prompt"] for s in sheets}
    title = "  |  ".join(dict.fromkeys(s["name"] for s in sheets))
    sections = [_section(s, "  ·  ".join(d) or s["name"], cmp.aligned_grid(s, cols))
                for s, d in zip(sheets, per)]
    return _draw(title, "  ·  ".join(common), prompts.pop() if len(prompts) == 1 else "",
                 cols, sections, open_image)


def _section(sheet: dict, heading: str, keys: list[list[str]]) -> dict:
    return {"heading": heading, "rows": sheet["rows"], "keys": keys,
            "renders": sheet.get("renders") or {},
            "ratio": sheet["height"] / sheet["width"]}


SECTION_H = 30


def _draw(title: str, info: str, prompt: str, cols: list[dict], sections: list[dict],
          open_image: Callable[[str], Image.Image | None]) -> bytes:
    big, small = _font(20), _font(15)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    width = LABEL_W + CELL_W * len(cols) + PAD
    title_lines = _wrap(probe, title, big, width - 2 * PAD, 2)
    info_lines = _wrap(probe, info, small, width - 2 * PAD, 3) if info else []
    prompt_lines = _wrap(probe, prompt, small, width - 2 * PAD, 4) if prompt else []
    top = PAD + 26 * len(title_lines) + 20 * (len(info_lines) + len(prompt_lines)) + PAD
    for sec in sections:
        sec["cell_h"] = max(1, round(CELL_W * sec["ratio"]))
        sec["heading_lines"] = (_wrap(probe, sec["heading"], small, width - 2 * PAD, 2)
                                if sec["heading"] else [])
    body = sum((SECTION_H * bool(sec["heading_lines"]) + 18 * max(0, len(sec["heading_lines"]) - 1))
               + sec["cell_h"] * len(sec["keys"]) for sec in sections)
    height = top + HEAD_H + body + PAD

    out = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(out)
    y = PAD
    for line in title_lines:
        d.text((PAD, y), line, font=big, fill=FG)
        y += 26
    for line in info_lines + prompt_lines:
        d.text((PAD, y), line, font=small, fill=DIM)
        y += 20

    for ci, col in enumerate(cols):
        x = LABEL_W + ci * CELL_W
        d.text((x + CELL_W // 2, top + HEAD_H // 2), column_label(col), font=big, fill=FG,
               anchor="mm")

    y0 = top + HEAD_H
    for sec in sections:
        if sec["heading_lines"]:
            d.line((PAD, y0 + 4, width - PAD, y0 + 4), fill=EMPTY, width=1)
            hy = y0 + 9
            for line in sec["heading_lines"]:
                d.text((PAD, hy), line, font=small, fill=ACCENT)
                hy += 18
            y0 += SECTION_H + 18 * (len(sec["heading_lines"]) - 1)
        cell_h = sec["cell_h"]
        for row, row_keys in zip(sec["rows"], sec["keys"]):
            _draw_row(d, out, probe, row, row_keys, sec["renders"], y0, cell_h, open_image,
                      big, small)
            y0 += cell_h

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _draw_row(d, out, probe, row, row_keys, renders, y0, cell_h, open_image, big, small):
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
        if not key:  # this sheet has no such column
            continue
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

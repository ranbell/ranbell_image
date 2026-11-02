"""The "moss" Refine style: prompts for MOSS-Image (OpenMOSS, 2026-10-16).

MOSS-Image reads three kinds of prompt (OpenMOSS USERGUIDE):

- a caption — long English prose. Its training captions were written by VLMs
  (Gemma 4, Qwen 3.5) from a recaption prompt that asks for composition,
  subjects, actions, setting, colours, light and style, and for visible text
  kept exactly, with its line breaks, position, size, lettering and colour.
- a tag sequence in the order Quality → Score → Meta → Year → Character →
  Series → Artist → General, with spaces rather than underscores and "@"
  before an artist.
- a JSON layout: a summary caption plus one item per person, object or piece
  of text, each with a box.

The caption and the layout are written from the detailed style's scene sheet
(``scene_sheet``): the sheet already gives every person and every piece of
text a box of its own, which is exactly what a layout needs. The tags come
from the danbooru style's call and are only put in order here.

The layout string is built the way ComfyUI's own ``MOSSImageLayoutPrompt``
node builds it (comfy_extras/nodes_moss_image.py), so a prompt from here and
one from the node are the same text: the writer places boxes on a 0-1000 grid
and the code turns them into centre-origin pixels.
"""
from __future__ import annotations

import json
import math
import re

from .scene_sheet import clean_script

# The USERGUIDE's recommended negative without "nsfw" (the Showrunner,
# 2026-10-29). Fixed text: the writer is never asked for one.
MOSS_NEGATIVE = "worst quality, bad anatomy, watermark, jpeg artifact"

FORMATS = ("caption", "layout", "tags")

# MOSS-Image draws English and Chinese text. Kana is not in that list, so a
# Japanese line is let through with a note rather than stopped.
_KANA_RE = re.compile(r"[぀-ヿ]")


def kana_note(texts: list[str]) -> str:
    """A note when visible text holds kana, which MOSS-Image may not draw."""
    hits = [t for t in texts or [] if _KANA_RE.search(t)]
    if not hits:
        return ""
    quoted = ", ".join(f'"{t}"' for t in hits)
    return (f"MOSS-Image renders English and Chinese text; the kana in {quoted} "
            "may come out garbled")


# ── Layout ───────────────────────────────────────────────────────────────────

_GRID = 1000
# A box narrower than this (in grid units) is widened around its centre: the
# model has nothing to draw into a sliver.
_MIN_SIDE = 40
# Objects come from the sheet's one "props" string, so the writer splits it;
# past this many the boxes stop meaning anything.
_MAX_OBJECTS = 8


def _person_line(subject: dict) -> str:
    """One person from the sheet, as a caption the layout can fall back on."""
    # What they do first, how they look last (the Showrunner, 2026-11-01).
    parts = [subject.get(k, "") for k in
             ("who", "pose_action", "intent", "legs_feet", "hands", "motion", "expression", "gaze")]
    if subject.get("holding"):
        parts.append(f"holding {subject['holding']}")
    parts += [subject.get(k, "") for k in ("appearance", "outfit", "details")]
    return ", ".join(p for p in parts if p)


def layout_items(sheet: dict) -> list[dict]:
    """The boxes a layout must have, listed from the sheet by code.

    Every person and every piece of text gets one; the writer cannot drop
    them. Objects are not listed: "props" is one free string, so the writer
    splits it into boxes itself.
    """
    items: list[dict] = []
    for i, subject in enumerate(sheet.get("subjects") or [], start=1):
        items.append({
            "id": f"person {i}", "label": "character",
            "about": subject.get("who") or f"person {i}",
            "fallback": _person_line(subject),
        })
    for i, text in enumerate(sheet.get("text_in_image") or [], start=1):
        items.append({
            "id": f"text {i}", "label": "text", "text": text,
            "about": f'the words "{text}"',
            "fallback": f'the words "{text}"',
        })
    return items


def layout_prompt(
    sheet: dict, items: list[dict], *, width: int, height: int, instruction: str = "",
) -> str:
    """What the writer is asked: a summary, and where each box goes."""
    shape = "portrait" if height > width else "landscape" if width > height else "square"
    listed = [f'- "{it["id"]}": {it["about"]}' for it in items] or ["- (no people, no text)"]
    props = sheet.get("props") or ""
    lines = [
        "Plan a layout prompt for the MOSS-Image model from the scene sheet below.",
        "",
        "How MOSS-Image reads a layout: a summary caption describes the whole picture, "
        "and each item is a box with its own caption. It draws each box's caption inside "
        "that box, and only there. So:",
        "- Whatever has no box — the setting, its layers, effects, lighting, camera, "
        "rendering and mood — lives in the summary caption. Begin it with the medium, "
        f"\"{sheet.get('medium', '')}\", and write it as several full English sentences, "
        "using every one of those boxes of the sheet.",
        "- A person's box covers the whole of that person as far as the framing shows them, "
        "in the middle of what they are doing. Their caption begins with what they are doing "
        "and why, then the body — legs and feet, each hand, what they hold, the movement — "
        "then the face and what the eyes are on, then appearance, outfit and details.",
        "- The summary begins with what is happening in the scene; light and mood come last "
        "and short, and never stand in for the action.",
        "- A text box sits tightly around its words. Its caption says the lettering, size "
        "and colour; the words themselves are added by the program, exactly as in the sheet.",
        "- Boxes may overlap where things overlap in the picture. Put them where the "
        "sheet's positions and camera say they are.",
        "",
        f"The picture is {width}x{height} pixels ({shape}). Boxes are on a {_GRID}x{_GRID} "
        f"grid over the whole picture, whatever its shape: [x_min, y_min, x_max, y_max], "
        f"with 0,0 at the top left and {_GRID},{_GRID} at the bottom right.",
        "",
        "Give exactly one box for each of these, with this id:",
        *listed,
    ]
    if props:
        lines += [
            "",
            f"Also give one box for each object named in the sheet's \"props\" (at most "
            f"{_MAX_OBJECTS}), with the id \"object: \" and the object's name. Do not add "
            "objects the sheet does not name, and none that someone is holding.",
        ]
    lines += [
        "",
        "Plain English, no tags, no underscores, no quality words. Output only this JSON:",
        "{",
        '  "caption": "the summary caption",',
        '  "boxes": [',
        '    {"id": "an id from the list", "caption": "what is in this box", '
        '"box": [x_min, y_min, x_max, y_max]}',
        "  ]",
        "}",
    ]
    if instruction:
        lines += ["", "[User instruction — already applied in the sheet; keep it]", instruction]
    lines += ["", "[Scene sheet]", json.dumps(sheet, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def _key(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _clean_box(raw) -> tuple[list[float] | None, list[str]]:
    """A writer's box as [x0, y0, x1, y1] on the grid, and what was fixed."""
    fixes: list[str] = []
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None, fixes
    try:
        box = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None, fixes
    if all(0.0 <= v <= 1.0 for v in box) and any(v > 0 for v in box):
        box = [v * _GRID for v in box]
        fixes.append("written on a 0-1 scale")
    clamped = [min(max(v, 0.0), float(_GRID)) for v in box]
    if clamped != box:
        fixes.append("ran off the picture")
    x0, y0, x1, y1 = clamped
    if x0 > x1 or y0 > y1:
        fixes.append("corners swapped")
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
    for lo, hi, axis in ((0, 2, "x"), (1, 3, "y")):
        pair = [x0, y0, x1, y1]
        if pair[hi] - pair[lo] < _MIN_SIDE:
            mid = (pair[lo] + pair[hi]) / 2
            mid = min(max(mid, _MIN_SIDE / 2), _GRID - _MIN_SIDE / 2)
            pair[lo], pair[hi] = mid - _MIN_SIDE / 2, mid + _MIN_SIDE / 2
            x0, y0, x1, y1 = pair
            fixes.append(f"too thin in {axis}")
    return [x0, y0, x1, y1], fixes


def _default_box(item: dict, items: list[dict]) -> list[float]:
    """Where a box the writer left out goes: people side by side across the
    lower picture, text stacked along the top."""
    same = [it for it in items if it["label"] == item["label"]]
    n, i = len(same), same.index(item)
    if item["label"] == "text":
        band = 120
        return [100.0, 40.0 + i * band, 900.0, 40.0 + i * band + band - 20]
    step = _GRID / n
    return [i * step + 10, 150.0, (i + 1) * step - 10, 1000.0]


def to_pixels(box: list[float], width: int, height: int) -> list[int]:
    """Grid [x0, y0, x1, y1] → centre-origin pixel [y0, x0, y1, x1].

    The rounding is ComfyUI's MOSSImageLayoutPrompt: edges that enclose the box.
    """
    x0, y0, x1, y1 = box
    return [
        math.floor(round(y0 * height / _GRID, 9)) - height // 2,
        math.floor(round(x0 * width / _GRID, 9)) - width // 2,
        math.ceil(round(y1 * height / _GRID, 9)) - height // 2,
        math.ceil(round(x1 * width / _GRID, 9)) - width // 2,
    ]


def _summary(draft: dict, sheet: dict) -> tuple[str, list[str]]:
    notes: list[str] = []
    caption = clean_script(str(draft.get("caption") or ""))
    if not caption:
        parts = [sheet.get(k, "") for k in
                 ("setting", "layers", "effects", "lighting", "camera", "rendering", "palette_mood")]
        caption = ". ".join(p for p in parts if p)
        notes.append("the summary caption was missing — built from the sheet")
    medium = sheet.get("medium") or ""
    core = [w for w in re.findall(r"[a-z]+", medium.lower()) if len(w) > 2]
    if medium and core and not any(w in caption[:300].lower() for w in core):
        article = "An" if medium[:1].lower() in "aeiou" else "A"
        caption = f"{article} {medium}. {caption}".strip()
        notes.append(f'medium "{medium}" was missing from the summary — added')
    return caption, notes


def compile_layout(draft: dict, items: list[dict], sheet: dict, *, width: int, height: int,
                   ) -> tuple[str, list[str]]:
    """The writer's draft → the layout string MOSS-Image reads, and a note
    for every repair, so the screen can say what was changed."""
    notes: list[str] = []
    caption, summary_notes = _summary(draft if isinstance(draft, dict) else {}, sheet)
    notes += summary_notes
    raw_boxes = [b for b in (draft.get("boxes") if isinstance(draft, dict) else None) or []
                 if isinstance(b, dict)]
    by_id: dict[str, dict] = {}
    for b in raw_boxes:
        by_id.setdefault(_key(b.get("id")), b)

    out: list[dict] = []

    def _add(label: str, item_caption: str, box: list[float], text: str | None = None) -> None:
        entry: dict = {"label": label, "caption": item_caption}
        if label == "text":
            entry["text"] = text or ""
        entry["bbox"] = to_pixels(box, width, height)
        out.append(entry)

    for item in items:
        b = by_id.get(_key(item["id"]))
        box, fixes = _clean_box(b.get("box") if b else None)
        if box is None:
            box = _default_box(item, items)
            notes.append(f'{item["id"]} had no box — placed by default')
        elif fixes:
            notes.append(f'{item["id"]} box: {", ".join(fixes)}')
        item_caption = clean_script(str((b or {}).get("caption") or ""))
        if not item_caption:
            item_caption = item["fallback"]
            if b:
                notes.append(f'{item["id"]} had no caption — taken from the sheet')
        _add(item["label"], item_caption, box, item.get("text"))

    listed = {_key(it["id"]) for it in items}
    # A box under an id the list did not have is kept as an object box, not
    # thrown away (the Showrunner, 2026-10-31: nothing is dropped for want of a place).
    objects = [b for b in raw_boxes if _key(b.get("id")) not in listed]
    for b in objects:
        if not _key(b.get("id")).startswith("object"):
            notes.append(f'box "{b.get("id")}" was not on the list — kept as an object')
    if len(objects) > _MAX_OBJECTS:
        notes.append(f"{len(objects)} object boxes — kept the first {_MAX_OBJECTS}")
        objects = objects[:_MAX_OBJECTS]
    for b in objects:
        name = re.sub(r"^object\s*:?\s*", "", str(b.get("id") or ""), flags=re.I).strip()
        box, fixes = _clean_box(b.get("box"))
        if box is None:
            notes.append(f'object "{name}" had no usable box — left out')
            continue
        if fixes:
            notes.append(f'object "{name}" box: {", ".join(fixes)}')
        item_caption = clean_script(str(b.get("caption") or "")) or name
        if item_caption:
            _add("object", item_caption, box)

    # What the sheet holds outside any box — "other" and the unclassified tags —
    # has nowhere in the layout but the summary, so it is made sure of there.
    from .scene_sheet import _content, _stem
    stems = {_stem(w) for w in re.findall(r"[a-z]+", caption.lower())}
    extra = [t for t in ([sheet.get("other") or ""] + [
        re.sub(r"\s*\([^)]*\)", "", str(t)).strip() for t in sheet.get("unclassified") or []])
        if t and any(_stem(w) not in stems for w in _content(t, 3))]
    if extra:
        caption = f"{caption.rstrip()} Also in the picture: {'; '.join(extra)}."
        notes.append(f"added to the summary: {'; '.join(extra)}")
    if not out:
        # The node does the same: no boxes, no layout — just the caption.
        return caption, notes
    layout = {"summary": {"caption": caption}, "items": out,
              "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}
    return json.dumps(layout, ensure_ascii=False, separators=(",", ":")), notes


LAYOUT_RETRY_PREFIX = (
    "Your previous answer could not be read as the JSON asked for. Answer again with "
    "only the JSON object — no explanation, no markdown.\n\n"
)


# ── Tags ─────────────────────────────────────────────────────────────────────

_QUALITY = {
    "masterpiece", "best quality", "high quality", "good quality", "normal quality",
    "amazing quality", "very aesthetic", "highres", "absurdres", "ultra-detailed",
    "ultra detailed", "highly detailed", "extremely detailed", "8k", "4k", "sharp focus",
    "intricate details", "professional",
}
_QUALITY_PREFIX = ("best quality", "highres")
_SCORE_RE = re.compile(r"^score[ _]\d+(?:[ _]up)?$")
_RATING = {"general", "sensitive", "questionable", "explicit", "safe", "nsfw",
           "rating:general", "rating:sensitive", "rating:safe"}
_YEAR_RE = re.compile(r"^(?:year[ _])?(?:19|20)\d\d$|^(?:newest|recent|mid|early|old)$")
_ARTIST_RE = re.compile(r"^(?:@|artist:\s*|by\s+)(.+)$", re.I)
# Underscores between letters become spaces; emoticons like ^_^ keep theirs.
_UNDERSCORE_RE = re.compile(r"(?<=[0-9A-Za-z)])_(?=[0-9A-Za-z(])")


def _spaced(tag: str) -> str:
    return re.sub(r"\s+", " ", _UNDERSCORE_RE.sub(" ", tag)).strip()


def compile_tags(raw: str, *, characters: frozenset[str] | set[str] = frozenset(),
                 ) -> tuple[str, list[str]]:
    """A danbooru tag line → MOSS-Image's order and spelling.

    ``characters`` is the WD14 character-name list (category 4, underscore
    form). A name is only moved to its place, never added: putting a
    character's name into a prompt is the writer's call, not the code's.
    """
    notes: list[str] = []
    buckets: dict[str, list[str]] = {
        k: [] for k in ("quality", "score", "meta", "year", "character", "series", "artist", "general")
    }
    seen: set[str] = set()
    chars = {c.lower().replace(" ", "_") for c in characters}
    dropped_rating: list[str] = []
    for part in re.split(r"[,\n]", raw or ""):
        tag = part.strip().strip("()[]{}").strip()
        if not tag:
            continue
        m = _ARTIST_RE.match(tag)
        if m:
            name = _spaced(m.group(1))
            key = "@" + name.lower()
            if name and key not in seen:
                seen.add(key)
                buckets["artist"].append("@" + name)
            continue
        spaced = _spaced(tag)
        low = spaced.lower()
        if low in seen:
            continue
        seen.add(low)
        if low in _RATING:
            dropped_rating.append(spaced)
        elif low in _QUALITY:
            buckets["quality"].append(spaced)
        elif _SCORE_RE.match(low):
            buckets["score"].append(spaced)
        elif _YEAR_RE.match(low):
            buckets["year"].append(spaced)
        elif low.replace(" ", "_") in chars:
            buckets["character"].append(spaced)
        else:
            buckets["general"].append(spaced)
    if dropped_rating:
        notes.append(f"rating words left out: {', '.join(dropped_rating)}")
    missing = [q for q in _QUALITY_PREFIX if q not in {t.lower() for t in buckets["quality"]}]
    if missing:
        buckets["quality"] = list(_QUALITY_PREFIX) + [
            t for t in buckets["quality"] if t.lower() not in _QUALITY_PREFIX
        ]
    ordered = [t for k in buckets for t in buckets[k]]
    return ", ".join(ordered), notes

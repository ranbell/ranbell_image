"""Props: the scene sheet's objects, kept as read, simplified, or elaborated.

The Showrunner (2026-11-03): "match the items and objects to the scene and the
background — make them concrete, or add a few — and let me switch between as
read / simplified / elaborated."

Read faithfully, `props` says "a cup on the table, some books". The picture it
makes has a cup and some books that could be anywhere. The other way, a busy
reference hands over a dozen objects and the eye has nowhere to land. One text
call after the reading (after staging, which can move objects too) turns the
props one way or the other.

**Built the way the Muse ledger is built** (`muse/ledger.py`). What the model
writes is a proposal; what reaches the sheet is decided here, in code:

- **One entrance.** The answer goes on to the sheet through
  `scene_sheet.apply_patch` under scope locks — the same door the retake and
  staging use — so only `props` and each person's `holding` can move, and the
  user's locks hold.
- **What is not written keeps its value.** An item the model left out of an
  elaborate answer is kept as it was, never dropped. An empty value never
  clears a box (`RESIST_EMPTY_CLEAR`).
- **One object is named once** (the ledger's `one_body`, the holding axis
  keyed per object). An added object that is already on the sheet — in props
  or in someone's hands — is a second answer for the same thing and is dropped.
- **A rewrite must still be the same object.** Elaborating "a cup" into "a
  chipped enamel cup of barley tea" keeps its name; turning it into "a lamp" is
  a different object wearing the old one's slot, and is refused.
- **Simplify can only take away.** The model picks items by number and may
  shorten their wording, but a shortened item may not bring a word the
  original did not have. The box can shrink; nothing new comes in through it.
- **Nothing is thrown away silently.** Every refusal and drop is returned as a
  line, and the runner says it in the thinking pane.
"""
from __future__ import annotations

import json
import re

from . import scene_sheet

PROPS_MODES = ("keep", "simplify", "elaborate")

#: At most this many objects are left by "simplify".
SIMPLIFY_KEEP_MAX = 3
#: "A few": at most this many new objects come in with "elaborate".
ELABORATE_ADD_MAX = 3

# A comma starts a new item only when what follows opens like a noun phrase —
# "a mug, steaming, on the desk" is one item; "a mug on the desk, a lamp in the
# corner" is two.
_ITEM_OPENERS = (
    "a", "an", "the", "some", "several", "few", "many", "one", "two", "three", "four",
    "five", "six", "seven", "eight", "nine", "ten", "pair", "pairs", "stack", "stacks",
    "pile", "piles", "row", "rows", "set", "sets", "bunch", "scattered", "assorted",
    "various", "small", "large", "tall", "old", "open", "half",
)
_SPLIT_COMMA_RE = re.compile(
    r",\s*(?:and\s+)?(?=(?:" + "|".join(_ITEM_OPENERS) + r")\b)", re.I,
)
_SPLIT_AND_RE = re.compile(r"\s+and\s+(?=(?:a|an|the|some|two|three|several)\b)", re.I)

# Where an item stops naming the object and starts saying where it is.
_PLACE_RE = re.compile(
    r"\b(?:on|in|at|by|beside|near|under|underneath|beneath|behind|against|atop|"
    r"inside|along|across|above|below|over|next to|in front of|leaning|lying|"
    r"resting|sitting|standing|hanging|propped|placed)\b",
    re.I,
)

# Words that say nothing about which object this is.
_NOT_THE_OBJECT = frozenset({
    "a", "an", "the", "some", "several", "few", "many", "of", "and", "with", "its",
    "their", "her", "his", "one", "two", "three", "four", "five", "pair", "small",
    "large", "big", "little", "tiny", "tall", "short", "old", "new", "open", "closed",
    "half", "full", "empty", "white", "black", "red", "blue", "green", "yellow",
    "brown", "grey", "gray", "pink", "purple", "orange", "dark", "light", "bright",
    "pale", "worn", "scattered", "assorted", "various", "there", "left", "right",
    "both", "hand", "hands", "holding", "held", "holds",
})

# The hand an object is in. A rewrite of `holding` that loses this has moved the
# object to the other hand, which is the person's action, not the object.
_SIDE_RE = re.compile(r"\b(left|right|both)\b", re.I)


def split_items(text: str) -> list[str]:
    """`props` as one item per object. A sheet keeps it as one string."""
    text = str(text or "").strip().rstrip(".")
    if not text:
        return []
    out: list[str] = []
    for chunk in re.split(r"\s*;\s*|\.\s+|\n+", text):
        for piece in _SPLIT_COMMA_RE.split(chunk):
            for part in _SPLIT_AND_RE.split(piece):
                part = part.strip(" ,.")
                if part:
                    out.append(part)
    return out


def join_items(items: list[str]) -> str:
    return "; ".join(i.strip() for i in items if i and i.strip())


def _object_part(item: str) -> str:
    """The words naming the object — what comes before its place."""
    m = _PLACE_RE.search(item or "")
    head = item[: m.start()] if m and m.start() > 0 else item
    return head or ""


def _stems(text: str) -> set[str]:
    return {
        scene_sheet._stem(w) for w in re.findall(r"[a-z]+", (text or "").lower())
        if len(w) >= 3 and w not in _NOT_THE_OBJECT and w not in scene_sheet._STOP_WORDS
    }


def object_key(item: str) -> set[str]:
    """Which object this is, as word stems. Falls back to the whole item when
    the place came first ("on the desk, a mug")."""
    return _stems(_object_part(item)) or _stems(item)


def same_object(a: str, b: str) -> bool:
    ka, kb = object_key(a), object_key(b)
    return bool(ka and kb and ka & kb)


def _clean(value) -> str:
    return scene_sheet.clean_script(scene_sheet._text(value)).strip(" ;,.")


def held_items(sheet: dict) -> list[tuple[int, str]]:
    return [(i, s.get("holding") or "") for i, s in enumerate(sheet.get("subjects") or [])
            if (s.get("holding") or "").strip()]


# ── The prompt ───────────────────────────────────────────────────────────────

_HOW = {
    "simplify": (
        "Too many objects make a busy picture and give the eye nowhere to land. Keep only "
        f"the objects this scene cannot do without — at most {SIMPLIFY_KEEP_MAX}: the ones the "
        "people use, look at or react to, and the one that says most clearly where this is. "
        "Shorten each one you keep to its name, a word or two of how it looks, and its place. "
        "Use only words already in that item: you are taking away, never adding."
    ),
    "elaborate": (
        "Make every object belong to this place, this moment and these people. For each "
        "item, say exactly what kind it is, its material and colour, its state (open, "
        "steaming, half-full, folded, scattered, worn at the edges) and where it is, so a "
        "viewer could tell from it where and when this is. Keep each object's own name word: "
        "you are describing the same object better, not swapping it for another. Then add up "
        f"to {ELABORATE_ADD_MAX} objects this place would really have that the sheet does not "
        "name yet — each one with where it is, chosen for this scene and these people. An "
        "object any picture could be given is not worth adding. Objects in someone's hands "
        "may be described better the same way, staying in the same hand."
    ),
}


def props_prompt(sheet: dict, mode: str, *, instruction: str = "") -> str:
    """What the text model is asked. **Only the boxes it may write are shown as
    numbered lists**; the rest of the scene is given as read-only context (the
    ledger's `for_model`: a box the model is not shown is a box it cannot fill)."""
    items = split_items(sheet.get("props") or "")
    held = held_items(sheet) if mode == "elaborate" else []
    scene = {k: sheet.get(k) for k in ("medium", "story", "setting", "situation", "layers",
                                        "lighting", "palette_mood", "effects")
             if sheet.get(k)}
    people = [
        {k: s.get(k) for k in ("who", "pose_action", "intent", "outfit") if s.get(k)}
        for s in sheet.get("subjects") or []
    ]
    lines = [
        "Below are the objects of a scene a vision model read from a reference picture, "
        "and the scene they are in.",
        "",
        "[What to do]",
        _HOW[mode],
        "",
        "Rules:",
        "- One object is named once. An object already in someone's hands is not also a "
        "loose object somewhere.",
        "- Write plain English phrases, concrete and visible. No tags, no underscores.",
        "- The people, their clothes and the place are not yours to change.",
        "",
        "[Objects — numbered]",
    ]
    lines += [f"{n}. {item}" for n, item in enumerate(items)] or ["(none yet)"]
    if held:
        lines += ["", "[Held by the people — numbered by person]"]
        lines += [f"{i}. {h}" for i, h in held]
    lines += ["", "Output only JSON in this shape:"]
    if mode == "simplify":
        lines += ['{"keep": [{"index": 0, "item": "the shortened item"}]}']
    else:
        lines += [
            "{",
            '  "items": [{"index": 0, "item": "the same object, described for this scene, with its place"}],',
            '  "add": [{"item": "a new object", "where": "where it is", "why": "what it says about this scene"}],',
            '  "holding": [{"index": 0, "holding": "the same object, described better, in the same hand"}]',
            "}",
        ]
    if instruction:
        lines += ["", "[User instruction — the scene already follows it; keep it]", instruction]
    lines += [
        "",
        "[The scene — read only]",
        json.dumps(scene, ensure_ascii=False, indent=2),
        "[The people — read only]",
        json.dumps(people, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


# ── The gate ─────────────────────────────────────────────────────────────────

def _entries(answer: dict, key: str) -> list[dict]:
    raw = answer.get(key) if isinstance(answer, dict) else None
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def _index(entry: dict, size: int) -> int | None:
    try:
        i = int(entry.get("index"))
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < size else None


def _simplify(items: list[str], answer: dict, notes: list[str]) -> list[str] | None:
    picked: list[tuple[int, str]] = []
    seen: set[int] = set()
    for entry in _entries(answer, "keep"):
        i = _index(entry, len(items))
        if i is None or i in seen:
            continue
        seen.add(i)
        original = items[i]
        short = _clean(entry.get("item"))
        if not short:
            short = original
        elif _stems(short) - _stems(original):
            # A word the item never had: the box only shrinks.
            notes.append(f"new words in a simplified item, kept as read: {original}")
            short = original
        picked.append((i, short))
    if not picked:
        return None
    if len(picked) > SIMPLIFY_KEEP_MAX:
        for i, _ in picked[SIMPLIFY_KEEP_MAX:]:
            notes.append(f"over {SIMPLIFY_KEEP_MAX} kept, left out: {items[i]}")
        picked = picked[:SIMPLIFY_KEEP_MAX]
    for i, original in enumerate(items):
        if i not in {p[0] for p in picked}:
            notes.append(f"left out: {original}")
    # In the sheet's own order, not the order the model listed them.
    return [text for _, text in sorted(picked)]


def _elaborate(
    sheet: dict, items: list[str], answer: dict, notes: list[str],
) -> tuple[list[str], dict[int, str]]:
    out = list(items)
    for entry in _entries(answer, "items"):
        i = _index(entry, len(items))
        if i is None:
            continue
        new = _clean(entry.get("item"))
        if not new:
            continue  # an empty answer never clears an object
        if not same_object(new, items[i]):
            notes.append(f"not the same object, kept as read: {items[i]} (offered: {new})")
            continue
        out[i] = new

    held = dict(held_items(sheet))
    holding: dict[int, str] = {}
    for entry in _entries(answer, "holding"):
        i = _index(entry, len(sheet.get("subjects") or []))
        if i is None or i not in held:
            continue
        new = _clean(entry.get("holding"))
        if not new:
            continue
        before = held[i]
        if not same_object(new, before):
            notes.append(f"person {i + 1} would hold something else, kept: {before} (offered: {new})")
            continue
        sides = {s.lower() for s in _SIDE_RE.findall(before)}
        if sides - {s.lower() for s in _SIDE_RE.findall(new)}:
            notes.append(f"person {i + 1}'s object changed hands, kept: {before}")
            continue
        holding[i] = new

    # Everything already named, in props or in a hand: an added object that is
    # one of these is the same answer twice.
    named = list(out) + [holding.get(i, h) for i, h in held.items()]
    added = 0
    for entry in _entries(answer, "add"):
        item = _clean(entry.get("item"))
        where = _clean(entry.get("where"))
        if not item:
            continue
        if added >= ELABORATE_ADD_MAX:
            notes.append(f"over {ELABORATE_ADD_MAX} added, left out: {item}")
            continue
        if not where and not _PLACE_RE.search(item):
            # An object with no place goes to whoever is nearest (the sheet's
            # first rule), so it does not come in.
            notes.append(f"added with no place, left out: {item}")
            continue
        clash = next((n for n in named if same_object(item, n)), None)
        if clash:
            notes.append(f"already on the sheet, not added again: {item} (as: {clash})")
            continue
        full = f"{item} {where}".strip() if where and where.lower() not in item.lower() else item
        out.append(full)
        named.append(full)
        added += 1
    return out, holding


def apply_props(
    sheet: dict, answer: dict, mode: str, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str], list[str]]:
    """The model's answer → the sheet. Returns the new sheet, what changed,
    what the user's locks refused, and what the gate dropped (with why).

    The answer goes on through `scene_sheet._apply_scoped` held to `props` and
    each person's `holding` — the same door staging and the retake go through.
    """
    if mode not in ("simplify", "elaborate"):
        return sheet, [], [], []
    notes: list[str] = []
    items = split_items(sheet.get("props") or "")
    patch: dict = {}
    if mode == "simplify":
        kept = _simplify(items, answer, notes)
        if kept is None:
            return sheet, [], [], ["nothing could be read as a choice — the objects stay as read"]
        patch["set"] = {"props": join_items(kept)}
    else:
        new_items, holding = _elaborate(sheet, items, answer, notes)
        patch["set"] = {"props": join_items(new_items)}
        if holding:
            patch["subjects"] = [{"index": i, "set": {"holding": h}} for i, h in holding.items()]
    locks = scene_sheet.scope_locks(sheet, ("props",), ("holding",), locked)
    new, changed, refused, _outside = scene_sheet._apply_scoped(sheet, patch, locks, locked)
    return new, changed, refused, notes

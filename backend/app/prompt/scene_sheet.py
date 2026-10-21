"""The "detailed" Refine style: a Krea 2 visual script, written in two steps.

Krea 2 reads its prompt with Qwen3-VL and was trained mostly on long, dense,
natural-language captions; Krea's own expansion prompt asks for one faithful
paragraph with each subject grouped with its own attributes and actions, and
no tags, lists or markdown (krea-ai/krea-2 docs/expansion.txt).

What went wrong with the old 8-section style, on a five-girl idol stage
(2026-10-21):

- "2girls, solo, multiple_girls" for five people
- `drinking_straw, cup, closed_eyes, holding` in a tag line with no owner — the
  model hands such an object to whoever is nearest, and one girl ended up
  sipping through a straw
- tags standing in for verbs: "forcefully (microphone)"
- "anime_illustration" buried mid-list, so the medium was easy to lose; without
  it Krea 2 drifts to a photograph

So the work is split, one job per call:

1. **Sheet** — the vision model looks at the reference and fills a JSON sheet.
   Every object has a box to go in: a subject's hands, or `props` with a place.
   The medium has its own required box.
2. **Script** — a text call writes English paragraphs from the sheet only.

WD14 tags are not used: on these models they were the noise, not the signal.
The finished script is checked in code for what the model is most likely to
drop — the medium, the count, quoted text — and leftovers of tag syntax are
cleaned up.
"""
from __future__ import annotations

import json
import re

from .visual_spec import DEFAULT_PROSE_PARAGRAPHS, clamp_prose_paragraphs

# Fixed media the screen can choose. Empty means "read it from the reference".
MEDIUM_CHOICES = {
    "anime": "anime illustration",
    "photo": "photograph",
}

_NUMBER_WORDS = {
    2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten",
}

_SHEET_FIELDS = (
    "medium", "rendering", "subject_count", "subjects", "setting", "layers", "props",
    "effects", "lighting", "camera", "palette_mood", "text_in_image",
)
_SUBJECT_FIELDS = (
    "who", "position", "appearance", "outfit", "details", "expression", "pose_action", "holding",
)


def sheet_prompt(
    *,
    instruction: str = "",
    notes: list[str] | None = None,
    literal_texts: list[str] | None = None,
    medium: str = "",
    reference_count: int = 1,
    combine: str = "merge",
) -> str:
    """Step 1: what the vision model is asked to fill in.

    ``combine`` decides what happens to the people when several references
    are tiled together (the Showrunner, 2026-10-21): "merge" fuses them into
    one cast, "coexist" keeps every one of them, side by side.
    """
    medium_line = (
        f'The medium is fixed by the user: "{medium}". Write exactly that in "medium".'
        if medium else
        'Decide the medium from the reference: an anime or manga style picture is an '
        '"anime illustration", a camera picture is a "photograph"; name others plainly '
        '("3D render", "oil painting", "watercolor painting").'
    )
    refs = ""
    if reference_count > 1:
        refs = (
            "The reference is a tile of several images, in reading order (left to right, "
            "top to bottom). Describe ONE scene made from them — not a collage, not "
            "side-by-side panels.\n"
        )
        if combine == "coexist":
            refs += (
                "Keep every main person from every image: they all appear together in "
                "this one scene, each with their own looks and outfit, so the count is the "
                "sum of the images' people. In each \"who\", say which image they come from "
                "(first image, second image, ...). Place them so they share the space "
                "naturally.\n"
            )
        else:
            refs += (
                "Fuse the people: the user chose to merge the characters, so the main "
                "person of each image becomes ONE person who combines their most striking "
                "features (hair from one, outfit from another, and so on). If each image "
                "shows one person, \"subjects\" has exactly ONE entry — one entry per "
                "image is the wrong answer here, even when the people look different. Say "
                "in that entry's \"takes_from\" which features came from which image. "
                "The outfit is ONE outfit a person could actually wear: pick pieces that go "
                "together, or carry one image's colour or pattern into the other's garment. "
                "Both outfits worn on top of each other is not a merge. "
                "Setting, light and mood are blended the same way.\n"
            )
    lines = [
        "You are preparing a scene sheet that another writer will turn into a prompt for "
        "the Krea 2 image model. Look at the reference image and fill in the JSON below.",
        "",
        "Why the boxes matter:",
        "- The image model gives any object without an owner to whichever person is "
        "nearest. So every object goes either in one subject's \"holding\" (say which "
        "hand) or in \"props\" with where it is. An object you cannot place is left out.",
        "- Each person has their own entry. Their expression, eyes, outfit and action go "
        "in that entry, not in a shared list, so one person's closed eyes do not spread "
        "to everyone.",
        "- \"subject_count\" must equal the number of entries in \"subjects\". Count the "
        "people you can actually see.",
        "- Without a stated medium the image model drifts to a photograph. "
        + medium_line,
        "- Write plain English phrases. No booru tags, no underscores, no parentheses.",
        "- Describe what is visible. If you are unsure of a detail, leave it out rather "
        "than guess. Look at each face separately: eyes open or closed, mouth open or "
        "closed. Look at each pair of hands: what is in them.",
        "- Be generous with concrete, visible detail: the writer can only describe what "
        "you put here, and a long, dense description is what the image model draws best "
        "from. A box with nothing to say stays an empty string.",
        "",
        refs.rstrip("\n"),
        "Fill in this JSON and output only the JSON:",
        "{",
        '  "medium": "anime illustration / photograph / ...",',
        '  "rendering": "how it is drawn or shot: line work, shading, brushwork, grain",',
        '  "subject_count": 0,',
        '  "subjects": [',
        '    {',
        '      "who": "who this is: gender, apparent age, and role in the scene",',
        '      "position": "where in the frame: center front, back left, ...",',
        '      "appearance": "hair colour, length and style; eye colour; face; build",',
        '      "outfit": "clothing and accessories, with materials and colours",',
        '      "details": "small visible things on this person: hair ornaments, jewelry, '
        'straps, stitching, fur, fabric texture, how light catches them",',
        '      "expression": "this person\'s face: eyes open or closed, mouth, feeling",',
        '      "pose_action": "what the body is doing, as a verb phrase",',
        '      "holding": "objects in this person\'s hands and which hand"'
        + (',\n      "takes_from": "which features come from which image"'
           if reference_count > 1 and combine != "coexist" else ""),
        '    }',
        '  ],',
        '  "setting": "where the scene is, described concretely",',
        '  "layers": "what is in the foreground, the middle ground and the background",',
        '  "props": "objects nobody holds, each with its place — never one already in someone\'s hands",',
        '  "effects": "particles, weather, light rays, reflections, motion in the air",',
        '  "lighting": "light sources, direction, colour, highlights and shadows",',
        '  "camera": "shot size, angle, framing, depth of field",',
        '  "palette_mood": "dominant colours and the feeling of the picture",',
        '  "text_in_image": ["exact words visible in the image, if any"]',
        "}",
    ]
    if literal_texts:
        quoted = ", ".join(f'"{t}"' for t in literal_texts)
        lines += ["", f"The user wants this text visible in the image: {quoted}. "
                  "Put it in \"text_in_image\" exactly as written."]
    if instruction:
        lines += ["", "[User instruction — it overrides the reference where they differ]",
                  instruction]
    if notes:
        lines += ["", "[Notes]"] + [f"- {n}" for n in notes]
    return "\n".join(line for line in lines if line is not None)


# What models write into a box they had nothing for. Kept, it reaches the
# script as "holds nothing" / "the setting is empty of props".
_EMPTY_WORDS = {"empty", "none", "nothing", "n/a", "na", "null", "-", "no", "unknown"}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if _text(v))
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_text(v)}" for k, v in value.items() if _text(v))
    text = str(value).strip()
    # Also "None (all props are held)" — an empty box with an explanation.
    head = re.split(r"[\s(.,;:—-]", text.lower(), maxsplit=1)[0]
    return "" if text.lower().strip(" .") in _EMPTY_WORDS or head in _EMPTY_WORDS else text


def normalize_sheet(data: dict, *, medium: str = "", literal_texts: list[str] | None = None) -> dict:
    """Keep the known boxes, as plain strings, and make the count and the
    medium agree with what the screen and the subject list say."""
    sheet: dict = {k: "" for k in _SHEET_FIELDS}
    for key in ("medium", "rendering", "setting", "layers", "props", "effects",
                "lighting", "camera", "palette_mood"):
        sheet[key] = _text(data.get(key))
    subjects = []
    for entry in data.get("subjects") or []:
        if not isinstance(entry, dict):
            continue
        subject = {k: _text(entry.get(k)) for k in _SUBJECT_FIELDS}
        if any(subject.values()):
            subjects.append(subject)
    sheet["subjects"] = subjects
    # The list is what gets written out, so it decides the count.
    sheet["subject_count"] = len(subjects)
    texts = [t for t in (_text(x) for x in (data.get("text_in_image") or [])) if t]
    for t in literal_texts or []:
        if t not in texts:
            texts.append(t)
    sheet["text_in_image"] = texts
    if medium:
        sheet["medium"] = medium
    sheet["medium"] = clean_script(sheet["medium"]) or "illustration"
    return sheet


# ── Saying it again: a Japanese line edits the sheet, not the prose ─────────
#
# The Showrunner picks ① the sheet editor and ⑨ a spoken retake as the first
# step of the creativity plan (2026-10-21). A retake used to mean writing the
# whole prompt again, so things nobody mentioned moved too. Here the line
# becomes a patch on the sheet — only the boxes it names change — and the
# script is written again from the patched sheet.

_SCENE_BOXES = ("medium", "rendering", "setting", "layers", "props", "effects",
                "lighting", "camera", "palette_mood")


def patch_prompt(sheet: dict, instruction: str, locked: list[str] | None = None) -> str:
    """What the text model is asked: turn the line into a patch."""
    locked = list(locked or [])
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    lines = [
        "Below is the scene sheet for a picture, and a request to change it. "
        "Turn the request into a patch on the sheet.",
        "",
        "Why only a patch: every box that changes changes the picture. A box the "
        "request does not ask about must stay exactly as it is, or something nobody "
        "asked for moves.",
        "- Change only what the request asks for, and whatever must change with it "
        "for the scene to stay consistent (night outdoors also changes the lighting).",
        "- Write new values the way the sheet is written: plain English phrases, "
        "concrete and visible.",
        "- People are addressed by their \"index\". To add a person, give a full entry "
        "in \"add_subjects\"; to remove one, put the index in \"remove_subjects\".",
    ]
    if locked:
        lines.append(
            "- These are locked by the user and must not appear in the patch: "
            + ", ".join(locked) + " (\"subjects.N\" is the whole person N)."
        )
    lines += [
        "",
        "Output only JSON in this shape, leaving out any part you do not need:",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}],',
        '  "add_subjects": [{"who": "", "position": "", "appearance": "", "outfit": "", '
        '"details": "", "expression": "", "pose_action": "", "holding": ""}],',
        '  "remove_subjects": [0]',
        "}",
        "",
        "[Request]",
        instruction,
        "",
        "[Scene sheet]",
        json.dumps(indexed, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def _short(text: str, n: int = 60) -> str:
    text = text or "—"
    return text if len(text) <= n else text[: n - 1] + "…"


def apply_patch(
    sheet: dict, patch: dict, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str]]:
    """Apply a patch. Returns the new sheet, what changed (readable lines),
    and what was refused because it was locked.

    Indexes in the patch refer to the sheet as it was, so person edits go
    first, then removals, then additions. Unknown boxes are ignored.
    """
    locked_set = set(locked or [])
    changed: list[str] = []
    refused: list[str] = []
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in sheet.items()}
    subjects = [dict(s) for s in sheet.get("subjects") or []]

    for key, value in ((patch.get("set") or {}) if isinstance(patch.get("set"), dict) else {}).items():
        if key not in _SCENE_BOXES and key != "text_in_image":
            continue
        if key in locked_set:
            refused.append(key)
            continue
        if key == "text_in_image":
            new = [t for t in (_text(x) for x in (value if isinstance(value, list) else [value])) if t]
            if new != list(out.get("text_in_image") or []):
                changed.append(f"text_in_image: {out.get('text_in_image')} → {new}")
                out["text_in_image"] = new
            continue
        new = _text(value)
        if new != (out.get(key) or ""):
            changed.append(f"{key}: {_short(out.get(key))} → {_short(new)}")
            out[key] = new

    for entry in patch.get("subjects") or []:
        if not isinstance(entry, dict):
            continue
        try:
            i = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(subjects):
            continue
        for key, value in (entry.get("set") or {}).items():
            if key not in _SUBJECT_FIELDS:
                continue
            if f"subjects.{i}" in locked_set or f"subjects.{i}.{key}" in locked_set:
                refused.append(f"subjects.{i}.{key}")
                continue
            new = _text(value)
            if new != subjects[i].get(key, ""):
                changed.append(f"person {i + 1} {key}: {_short(subjects[i].get(key))} → {_short(new)}")
                subjects[i][key] = new

    removals = set()
    for raw in patch.get("remove_subjects") or []:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(subjects):
            continue
        if f"subjects.{i}" in locked_set:
            refused.append(f"subjects.{i} (remove)")
            continue
        removals.add(i)
    for i in sorted(removals, reverse=True):
        changed.append(f"person {i + 1} removed: {_short(subjects[i].get('who'))}")
        del subjects[i]

    for entry in patch.get("add_subjects") or []:
        if isinstance(entry, dict):
            subjects.append({k: _text(entry.get(k)) for k in _SUBJECT_FIELDS})
            changed.append(f"person added: {_short(subjects[-1].get('who'))}")

    out["subjects"] = subjects
    # Through normalize again: the count follows the list, empties are dropped.
    return normalize_sheet(out, medium=out.get("medium") or ""), changed, refused


def script_prompt(
    sheet: dict, *, instruction: str = "", paragraphs: int = DEFAULT_PROSE_PARAGRAPHS,
) -> str:
    """Step 2: what the writer is asked to turn the sheet into."""
    n = sheet.get("subject_count") or 0
    count_line = (
        f"There are exactly {n} people. Say \"{_NUMBER_WORDS.get(n, n)}\" early and never "
        "a different number. Describe each person once, in the order of the sheet."
        if n >= 2 else
        "There is one person; describe them once." if n == 1 else
        "There are no people; do not add any."
    )
    lines = [
        "Write the prompt for the Krea 2 image model from the scene sheet below.",
        "",
        "How Krea 2 reads: it was trained on long, detailed captions in natural English, "
        "and it attaches a detail to the noun it is written next to. So:",
        f"- Begin the first sentence with the medium: \"{sheet.get('medium', '')}\". "
        "It decides whether the picture is drawn or photographed.",
        f"- {count_line} Name them by what they are (women, girls, men, ...), "
        "not as \"people\".",
        "- Keep each person together: their position, appearance, outfit, expression, "
        "what they are doing and what they hold, in their own sentences. Use real verbs "
        "for actions.",
        "- Write it the way a good caption describes a picture: flowing, specific "
        "sentences, not the sheet read out box by box. Turn the sheet's comma-separated "
        "labels into natural noun phrases rather than copying them. A box that is empty "
        "is simply not mentioned.",
        "- Use every box of the sheet; nothing in it is filler. Each person's \"details\" "
        "box is where the picture gets its richness, so write all of it out for that person.",
        "- An object in someone's hands is written once, with that person. Repeating it "
        "elsewhere makes the image model draw a second one.",
        "- Spend the length on what can be seen: materials and textures, how the light "
        "falls on each surface, what sits in front of and behind whom.",
        "- Then the setting and where the props are, then the lighting, then the camera "
        "and composition, then the rendering style and colour mood.",
        "- Use only what the sheet says. Do not add people, objects or actions; an extra "
        "word becomes an extra thing in the picture.",
        "- Plain sentences only: no tags, no underscores, no parentheses, no headings, "
        "no lists. Leave out words like masterpiece, best quality, 8k — they describe "
        "nothing visible.",
        "- Write visible text in double quotes, exactly as in the sheet.",
        f"- {_length_line(paragraphs, n)} Output only the prompt.",
    ]
    if instruction:
        lines += ["", "[User instruction — already applied in the sheet; keep it]", instruction]
    lines += ["", "[Scene sheet]", json.dumps(sheet, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


# Sentences per paragraph. Higher than the natural style's: those sentences
# carry inline tags, these carry only description, and at 2-4 sentences the
# writer dropped most of the sheet's details to fit (381 words at 5
# paragraphs for five people).
_SCRIPT_SENTENCES = {3: "6-8", 4: "5-7", 5: "4-6", 6: "4-6", 7: "3-5"}


def _length_line(paragraphs: int, people: int) -> str:
    """How long, and where the length goes: people first, since they are
    what goes wrong when squeezed."""
    n = clamp_prose_paragraphs(paragraphs)
    room = (
        "Give the people about half of the paragraphs" if people >= 2 and n >= 5 else
        "Give the person at least two paragraphs" if people == 1 and n >= 5 else
        "Keep the people in the first paragraphs"
    )
    return (
        f"Write exactly {n} paragraphs of {_SCRIPT_SENTENCES.get(n, '4-6')} full sentences each. "
        f"{room}; the rest go to the setting and its layers, effects and light, then "
        "camera, composition and rendering."
    )


_PAREN_TAGS_RE = re.compile(r"\s*\(([^()]*_[^()]*)\)")
_MARKDOWN_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", re.M)
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")


def clean_script(text: str) -> str:
    """Remove tag syntax the writer let through: booru tags in parentheses,
    underscores between words, escaped underscores, markdown markers."""
    out = (text or "").replace("\\_", "_")
    out = _PAREN_TAGS_RE.sub("", out)
    out = _BOLD_RE.sub(r"\1", out)
    out = _MARKDOWN_RE.sub("", out)
    out = re.sub(r"(?<=\w)_(?=\w)", " ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def finish_script(text: str, sheet: dict) -> tuple[str, list[str]]:
    """Clean the script and put back what it dropped. Returns the script and
    a note for each repair, so the screen can say what was changed."""
    notes: list[str] = []
    script = clean_script(text)
    medium = sheet.get("medium") or ""
    if medium and script.lower().startswith(medium.lower()):
        # "anime illustration of five girls…" / "Anime illustration of…" —
        # the article fell off.
        script = ("An " if medium[:1].lower() in "aeiou" else "A ") + script[:1].lower() + script[1:]
    elif script[:1].islower():
        script = script[:1].upper() + script[1:]
    head = script[:300].lower()
    core = [w for w in re.findall(r"[a-z]+", medium.lower()) if len(w) > 2]
    if medium and core and not any(w in head for w in core):
        article = "An" if medium[:1].lower() in "aeiou" else "A"
        script = f"{article} {medium}. {script}"
        notes.append(f'medium "{medium}" was missing from the opening — added')
    n = sheet.get("subject_count") or 0
    if n >= 2:
        first = script.split("\n\n", 1)[0].lower()
        word = _NUMBER_WORDS.get(n, str(n))
        if not re.search(rf"\b({word}|{n})\b", first):
            notes.append(f"the opening does not say {word} people")
    for t in sheet.get("text_in_image") or []:
        if f'"{t}"' not in script:
            script += f'\n\nThe words "{t}" appear in the image.'
            notes.append(f'text "{t}" was missing — added')
    return script, notes


# ── Fusing worlds: WD14 tags as ingredients, not as description ─────────────
#
# The Showrunner (2026-10-21): mixing two pictures' atmosphere is not adding
# them up, and the Krea 2 style — having dropped WD14 — reads as especially
# monotonous. Read faithfully, a sheet only restates its references. So in
# "fuse" mode each reference's distinctive WD14 tags (the ones no other
# reference shares) become ingredients that must *transform* the scene, and
# a single reference gets foreign tags from the vocabulary as the other world.
# The chaos dial decides how many, and how hard the worlds collide.

_RATING_TAGS = {"general", "sensitive", "questionable", "explicit", "safe", "nsfw"}

# Person boxes a fusion must not touch: who they are and how they look.
_IDENTITY_BOXES = ("who", "appearance", "expression", "position")


# Which WD14 tags can change a world. Measured on two Moss pictures of one
# girl each: their distinctive tags were "hand on own hip", "sneakers",
# "looking at viewer", "blurry background" — pose and framing words that gave
# the fusion nothing to work with, and the scene came back unchanged. Places,
# weather, light effects and things go first; clothing fills in; expression,
# action, hair and framing words are left out.
#
# Only tags whose axis the catalog knows get through. An unknown tag can be
# anything — the outsider sample from the vocabulary once brought a sexual
# one — and this is a whitelist on purpose, so no list of what to keep out
# has to live in the code. Good unknown words (ice, water, bird) are lost to
# that; safe beats rich here.
_WORLD_AXES = ("location", "time_weather", "visual", "always_fixed")
_FILLER_AXES = ("clothing",)
_POSE_WORDS = ("hand", "arm", "leg", "finger", "looking", "head_rest", "pose", "view",
               "focus", "shot", "from_", "background", "lying", "sitting", "standing",
               "pov", "profile", "tilt", "lean", "crossed", "spread", "own_",
               "depth_of_field", "blurry")


def world_tags(tags: list[str]) -> list[str]:
    """The tags that can change a world, best first."""
    from ..tags.catalog import get_tag_axis
    world, filler = [], []
    for tag in tags:
        key = str(tag).strip().lower().replace(" ", "_")
        # Eye colour is filed with things; it is the person, not the world.
        if key.endswith("_eyes") or any(w in key for w in _POSE_WORDS):
            continue
        axis = get_tag_axis(key)
        if axis in _WORLD_AXES:
            world.append(tag)
        elif axis in _FILLER_AXES:
            filler.append(tag)
    return world + filler


def unused_ingredients(sheet: dict, ingredients: dict[str, list[str]]) -> list[str]:
    """Ingredients whose words appear nowhere in the fused sheet."""
    text = json.dumps(sheet, ensure_ascii=False).lower()
    out = []
    for words in ingredients.values():
        for w in words:
            core = [p for p in re.findall(r"[a-z]+", w.lower()) if len(p) > 2]
            if core and not any(p in text for p in core):
                out.append(w)
    return out


def pick_ingredients(
    pools: dict[str, list[str]], chaos: float, *, skip: set[str] | frozenset = frozenset(),
    rng=None,
) -> dict[str, list[str]]:
    """Draw ingredients from each source's tag pool, in plain words.

    Random on purpose: the same two pictures should not fuse the same way
    every run. More chaos, more ingredients per source (2 → 8).
    """
    import random
    rng = rng or random
    k = 2 + round(max(0.0, min(1.0, chaos)) * 6)
    out: dict[str, list[str]] = {}
    for source, tags in pools.items():
        clean = []
        ranked = world_tags(list(tags))
        for tag in ranked:
            word = str(tag).strip().lower()
            if not word or word in _RATING_TAGS or word in skip or word.replace(" ", "_") in skip:
                continue
            word = word.replace("_", " ")
            if word not in clean:
                clean.append(word)
        if clean:
            # Random within the best twice-k, so the world tags lead and runs differ.
            head = clean[: max(k * 2, k)]
            out[source] = rng.sample(head, min(k, len(head)))
    return out


def _chaos_line(chaos: float) -> str:
    if chaos < 0.34:
        return ("Chaos is low: the other world seeps in at the edges — materials, light and "
                "small things change, the scene still reads as one familiar place.")
    if chaos < 0.67:
        return ("Chaos is medium: the worlds are interwoven — the setting itself is built from "
                "both, and the strangeness is visible at first glance.")
    return ("Chaos is high: the worlds collide — scale, physics and seasons may break, but it "
            "is still ONE picture, described concretely, not a collage.")


def fuse_prompt(
    sheet: dict, ingredients: dict[str, list[str]], chaos: float,
    locked: list[str] | None = None,
) -> str:
    """What the text model is asked: fuse the ingredients into the scene, as a patch."""
    locked = list(locked or [])
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    lines = [
        "Below is the scene sheet for a picture, and ingredients from other worlds. "
        "Fuse them into the scene and return the result as a patch on the sheet.",
        "",
        "Why: a picture that only restates its references is monotonous. Fusion is not "
        "addition — an ingredient placed beside the scene as one more object is the "
        "dull answer. Each ingredient must transform something already there:",
        "- what a surface or the architecture is made of",
        "- the weather, the air, the light source or its colour",
        "- the material, pattern or texture of a garment, or what someone holds",
        "- the props and the layers of the scene",
        "One ingredient may take the place of an element of the other world. The result "
        "is one world where both are true at once.",
        f"- {_chaos_line(chaos)}",
        "- Use every ingredient. Write new values as plain English phrases, concrete and "
        "visible, in the sheet's own style.",
        "- The people stay who they are: do not change their who, appearance, expression "
        "or position, and do not add or remove anyone. Their outfit and what they hold "
        "may change.",
    ]
    if locked:
        lines.append("- Locked by the user, must not appear in the patch: " + ", ".join(locked)
                     + " (\"subjects.N\" is the whole person N).")
    lines += [
        "",
        "Output only JSON in this shape, leaving out any part you do not need:",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}]',
        "}",
        "",
        "[Ingredients]",
    ]
    for source, words in ingredients.items():
        lines.append(f"- from {source}: {', '.join(words)}")
    lines += ["", "[Scene sheet]", json.dumps(indexed, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def apply_fusion(
    sheet: dict, patch: dict, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str]]:
    """apply_patch with the people's identity held: no one added or removed,
    and who / appearance / expression / position kept."""
    patch = {k: v for k, v in (patch or {}).items() if k in ("set", "subjects")}
    held = list(locked or []) + [
        f"subjects.{i}.{box}"
        for i in range(len(sheet.get("subjects") or []))
        for box in _IDENTITY_BOXES
    ]
    new, changed, refused = apply_patch(sheet, patch, held)
    # Holding identity is the design, not the user's lock; only report theirs.
    user_locked = set(locked or [])
    refused = [r for r in refused
               if r in user_locked or r.split(".")[0] != "subjects"
               or ".".join(r.split(".")[:2]) in user_locked]
    return new, changed, refused

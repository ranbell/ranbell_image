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
    "medium", "rendering", "subject_count", "subjects", "setting", "props",
    "lighting", "camera", "palette_mood", "text_in_image",
)
_SUBJECT_FIELDS = (
    "who", "position", "appearance", "outfit", "expression", "pose_action", "holding",
)


def sheet_prompt(
    *,
    instruction: str = "",
    notes: list[str] | None = None,
    literal_texts: list[str] | None = None,
    medium: str = "",
    reference_count: int = 1,
) -> str:
    """Step 1: what the vision model is asked to fill in."""
    medium_line = (
        f'The medium is fixed by the user: "{medium}". Write exactly that in "medium".'
        if medium else
        'Decide the medium from the reference: an anime or manga style picture is an '
        '"anime illustration", a camera picture is a "photograph"; name others plainly '
        '("3D render", "oil painting", "watercolor painting").'
    )
    refs = (
        "The reference is a tile of several images. Treat them as one mood board and "
        "describe ONE scene that combines them — not a collage, not side-by-side panels.\n"
        if reference_count > 1 else ""
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
        "- A box with nothing to say stays an empty string.",
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
        '      "expression": "this person\'s face: eyes open or closed, mouth, feeling",',
        '      "pose_action": "what the body is doing, as a verb phrase",',
        '      "holding": "objects in this person\'s hands and which hand"',
        '    }',
        '  ],',
        '  "setting": "where the scene is, described concretely",',
        '  "props": "objects nobody holds, each with its place",',
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
    return "" if text.lower().strip(" .") in _EMPTY_WORDS else text


def normalize_sheet(data: dict, *, medium: str = "", literal_texts: list[str] | None = None) -> dict:
    """Keep the known boxes, as plain strings, and make the count and the
    medium agree with what the screen and the subject list say."""
    sheet: dict = {k: "" for k in _SHEET_FIELDS}
    for key in ("medium", "rendering", "setting", "props", "lighting", "camera", "palette_mood"):
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


def script_prompt(sheet: dict, *, instruction: str = "") -> str:
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
        "sentences, not the sheet read out box by box. A box that is empty is simply "
        "not mentioned.",
        "- Then the setting and where the props are, then the lighting, then the camera "
        "and composition, then the rendering style and colour mood.",
        "- Use only what the sheet says. Do not add people, objects or actions; an extra "
        "word becomes an extra thing in the picture.",
        "- Plain sentences only: no tags, no underscores, no parentheses, no headings, "
        "no lists. Leave out words like masterpiece, best quality, 8k — they describe "
        "nothing visible.",
        "- Write visible text in double quotes, exactly as in the sheet.",
        "- Three to five paragraphs. Output only the prompt.",
    ]
    if instruction:
        lines += ["", "[User instruction — already applied in the sheet; keep it]", instruction]
    lines += ["", "[Scene sheet]", json.dumps(sheet, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


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
    if script[:1].islower():
        # "anime illustration of five girls…" — the article fell off.
        if medium and script.lower().startswith(medium.lower()):
            script = ("An " if medium[:1].lower() in "aeiou" else "A ") + script
        else:
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

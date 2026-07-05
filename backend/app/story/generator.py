"""Chronicle 3-stage pipeline: prompt builders and output parsers.

Pure prompt/parse logic only, so it can be unit-tested with a mocked Ollama
client. Orchestration (job streaming, DB writes, ComfyUI submission) lives in
jobs/runners.py.

Stage 1 (VLM)  — visual vocabulary extraction (wd14_tags reused when available)
Stage 2 (LLM)  — three-act chronicle, streamed with [PAST]/[PRESENT]/[FUTURE]
                 markers (stream-friendly; parsed into per-axis text)
Stage 3 (LLM)  — per-axis positive/negative prompt as JSON
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

AXES = ("past", "present", "future")

# Tags that describe rating/quality metadata rather than appearance
_META_TAG_RE = re.compile(
    r"^(masterpiece|best_quality|high_quality|low_quality|worst_quality|"
    r"absurdres|highres|lowres|sensitive|explicit|questionable|general|"
    r"commentary.*|translated|.*_username|.*_commentary)$"
)


def character_tags_from_wd14(wd14_tags: list[str], limit: int = 40) -> list[str]:
    """Drop rating/quality meta tags; keep appearance-relevant wd14 tags."""
    return [t for t in wd14_tags if not _META_TAG_RE.match(t)][:limit]


# ── Stage 1: visual vocabulary extraction ─────────────────────────────────────

def build_vision_prompt(full_extraction: bool) -> str:
    """VLM prompt for the base image.

    full_extraction=True (external image without wd14_tags): describe everything.
    full_extraction=False: wd14_tags already cover appearance — only describe
    what tags cannot express (environment, mood).
    """
    if full_extraction:
        return (
            "Describe this image precisely for an illustrator, in English.\n"
            "Cover, in short labeled sections:\n"
            "CHARACTER: physical traits (hair color/length, eye color, body, age impression)\n"
            "OUTFIT: clothing and accessories\n"
            "SCENE: background, environment, time of day, weather\n"
            "MOOD: overall mood and emotional tone\n"
            "Be concrete and visual. No speculation about names or backstory."
        )
    return (
        "Describe this image for an illustrator, in English.\n"
        "Cover, in short labeled sections:\n"
        "SCENE: background, environment, time of day, weather\n"
        "MOOD: overall mood, atmosphere and emotional tone\n"
        "Do NOT describe the character's appearance or clothing. "
        "Be concrete and visual."
    )


# ── Stage 2: three-act chronicle ──────────────────────────────────────────────

_AXIS_MARKER = {axis: f"[{axis.upper()}]" for axis in AXES}


def build_story_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    base_axis: str,
    worldview: str,
    mutation_tags: list[str] | None = None,
) -> str:
    """LLM prompt producing a three-act story with [PAST]/[PRESENT]/[FUTURE] markers."""
    world_line = (
        f'Setting requested by the user: "{worldview}"'
        if worldview.strip()
        else "No setting was specified — invent a fitting, evocative world yourself."
    )
    mutation_block = ""
    if mutation_tags:
        mutation_block = (
            "\nUnexpected elements to weave into the PAST and FUTURE acts "
            f"(reinterpret them freely): {', '.join(mutation_tags)}\n"
        )
    return (
        "You are a storyteller. Write a three-act chronicle (past, present, future) "
        "of the single character below.\n\n"
        f"CHARACTER:\n{character_desc}\n\n"
        f"THE {base_axis.upper()} looks exactly like this scene:\n{scene_desc}\n\n"
        f"{world_line}\n"
        f"{mutation_block}\n"
        "Rules:\n"
        f"- The {base_axis} act must match the scene above faithfully.\n"
        "- The other two acts develop the character's journey emotionally and "
        "visually within the setting. Each act must describe a concrete, "
        "paintable scene (place, light, action, mood).\n"
        "- 3-6 sentences per act, in English.\n"
        "- Output exactly three sections, each starting with its marker on its "
        "own line: [PAST] then [PRESENT] then [FUTURE]. No other headings."
    )


def parse_story_sections(raw: str) -> dict[str, str]:
    """Split marker-delimited story output into {axis: text}. Missing axes → ''."""
    result = {axis: "" for axis in AXES}
    pattern = re.compile(r"\[(PAST|PRESENT|FUTURE)\]", re.IGNORECASE)
    parts = pattern.split(raw)
    # parts = [preamble, MARKER, text, MARKER, text, ...]
    for i in range(1, len(parts) - 1, 2):
        axis = parts[i].lower()
        if axis in result:
            result[axis] = parts[i + 1].strip()
    return result


# ── Stage 3: per-axis prompt refinement ───────────────────────────────────────

def build_axis_prompt(
    *,
    story_text: str,
    character_tags: list[str],
    character_desc: str,
    prompt_style: str,
) -> str:
    """LLM prompt producing {"positive": ..., "negative": ...} JSON for one axis.

    Character identity keywords are condensed and placed at the head of the
    positive prompt to survive attention dilution in long prompts.
    """
    if character_tags:
        identity_src = (
            "Character identity tags (danbooru): " + ", ".join(character_tags)
        )
    else:
        identity_src = "Character description:\n" + character_desc

    if prompt_style == "natural":
        format_rule = (
            "The positive prompt is 2-4 sentences of vivid natural-language "
            "description of the scene."
        )
    elif prompt_style == "danbooru":
        format_rule = (
            "The positive prompt is a single comma-separated danbooru tag list "
            "(20-35 tags)."
        )
    else:  # danbooru+natural
        format_rule = (
            "The positive prompt is a comma-separated danbooru tag line, then a "
            "blank line, then 2-3 sentences of natural-language description."
        )

    return (
        "Write an image-generation prompt for the scene below.\n\n"
        f"SCENE (one act of a story):\n{story_text}\n\n"
        f"{identity_src}\n\n"
        "Rules:\n"
        "- Condense the character's PHYSICAL identity (hair, eyes, notable "
        "features, signature outfit elements) into a SHORT keyword list and put "
        "it at the very START of the positive prompt, so the same character is "
        "recognizable. Do not pad it with scene words.\n"
        f"- {format_rule}\n"
        "- After the identity keywords, describe THIS scene: place, action, "
        "lighting, mood.\n"
        "- The negative prompt lists only things to avoid (artifacts, wrong "
        "elements for this scene). Keep it short.\n"
        '- Answer with JSON only: {"positive": "...", "negative": "..."}'
    )


def parse_prompt_json(raw: str) -> tuple[str, str]:
    """Parse Stage 3 output into (positive, negative). Tolerates wrapped JSON."""
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return "", ""
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return "", ""
    if not isinstance(data, dict):
        return "", ""
    positive = str(data.get("positive") or "").strip()
    negative = str(data.get("negative") or "").strip()
    return positive, negative


def remove_conflict_tags(positive: str, conflicts: set[str]) -> str:
    """Remove conflicting danbooru tags from the tag portion of a positive prompt.

    Only comma-separated segments are filtered (prose sentences are left as-is,
    matching how _find_conflict_tags reports plain tag names).
    """
    if not conflicts:
        return positive
    lines = positive.split("\n")
    cleaned: list[str] = []
    for line in lines:
        if "," in line and "." not in line:
            tags = [t.strip() for t in line.split(",")]
            kept = [t for t in tags if t and t.replace(" ", "_") not in conflicts]
            cleaned.append(", ".join(kept))
        else:
            cleaned.append(line)
    return "\n".join(cleaned)

"""Chronicle pipeline: prompt builders and output parsers.

Pure prompt/parse logic only, so it can be unit-tested with a mocked Ollama
client. Orchestration (job streaming, DB writes, ComfyUI submission) lives in
jobs/runners.py.

Stage 1 (VLM)  — visual vocabulary extraction (wd14_tags reused when available)
Stage 2 (LLM)  — title + overall summary + three acts, streamed with
                 [TITLE]/[OVERALL]/[PAST]/[PRESENT]/[FUTURE] markers
Stage 3 (LLM)  — per-axis image prompt in Refine's Visual Script format
                 (danbooru tag line + 5 prose paragraphs with inline tags),
                 output as POSITIVE:/NEGATIVE: labeled sections
Final  (LLM)   — Japanese translation of title/overall/acts (JSON)
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

AXES = ("past", "present", "future")
SECTIONS = ("title", "overall") + AXES

# Temporal spacing between the three acts, selected by the UI slider
TIME_SCALES = {
    "minutes": "a few minutes",
    "hours": "a few hours",
    "days": "a few days",
    "months": "a few months",
    "years": "a few years",
    "decades": "several decades",
}

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


# ── Stage 2: title + overall + three acts ─────────────────────────────────────

def build_story_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    base_axis: str,
    worldview: str,
    time_scale: str = "years",
    mutation_tags: list[str] | None = None,
) -> str:
    """LLM prompt producing [TITLE]/[OVERALL]/[PAST]/[PRESENT]/[FUTURE] sections."""
    world_line = (
        f'Setting requested by the user: "{worldview}"'
        if worldview.strip()
        else "No setting was specified — invent a fitting, evocative world yourself."
    )
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    span_line = (
        f"The three acts are spaced about {span} apart. Match the magnitude of "
        "change to that span: minutes means the same scene moments before and "
        "after (same outfit, same place, small shifts in action and light); "
        "decades means entirely different chapters of a life."
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
        f"{span_line}\n"
        f"{mutation_block}\n"
        "Rules:\n"
        f"- The {base_axis} act must match the scene above faithfully.\n"
        "- The other two acts develop the character's journey emotionally and "
        "visually within the setting. Each act must describe a concrete, "
        "paintable scene (place, light, action, mood).\n"
        "- 3-6 sentences per act, in English.\n"
        "- Output exactly these five sections, each starting with its marker on "
        "its own line, in this order:\n"
        "[TITLE] — a specific, evocative title (3-8 words) drawn from the "
        "story's concrete imagery (a place, object, or motif). NEVER generic "
        "titles like 'Untitled', 'A Chronicle' or 'A Story'.\n"
        "[OVERALL] — a 2-4 sentence summary of the arc connecting all three acts\n"
        "[PAST] then [PRESENT] then [FUTURE] — the acts themselves.\n"
        "No other headings."
    )


# Tolerant marker matching: [PAST], **[PAST]**, PAST:, **PAST:**, ## PAST: ...
# Bare bracketed markers match anywhere; colon forms must start a line so that
# prose words like "past" are never mistaken for markers.
_SECTION_MARKER_RE = re.compile(
    r"(?im)"
    r"(?:\[\s*(TITLE|OVERALL|PAST|PRESENT|FUTURE)\s*\]"
    r"|^[ \t>#]{0,4}\**(TITLE|OVERALL|PAST|PRESENT|FUTURE)\**[ \t]*:)"
)


def parse_story_sections(raw: str) -> dict[str, str]:
    """Split marker-delimited story output into {section: text}. Missing → ''."""
    result = {section: "" for section in SECTIONS}
    parts = _SECTION_MARKER_RE.split(raw)
    # split with 2 groups → [preamble, g1, g2, text, g1, g2, text, ...]
    for i in range(1, len(parts) - 2, 3):
        section = (parts[i] or parts[i + 1] or "").lower()
        text = (parts[i + 2] or "").strip().lstrip("*:] \t").strip()
        if section in result and not result[section]:
            result[section] = text
    # Title should be a single clean line
    if result["title"]:
        result["title"] = result["title"].splitlines()[0].strip().strip('*"「」')
    return result


def build_story_repair_prompt(raw_story: str) -> str:
    """Fallback when marker parsing fails: restructure the raw output as JSON."""
    return (
        "The text below contains a chronicle with a title, an overall summary, "
        "and three acts (past, present, future), but the formatting is broken.\n"
        "Extract the five parts. Keep the wording — do not rewrite or shorten.\n"
        "If the title or overall summary is genuinely absent, write a fitting "
        "one from the acts.\n\n"
        f"TEXT:\n{raw_story[:6000]}\n\n"
        "Answer with JSON only, using exactly these keys:\n"
        '{"title": "...", "overall": "...", "past": "...", '
        '"present": "...", "future": "..."}'
    )


def parse_story_json(raw: str) -> dict[str, str]:
    """Parse the repair-pass output. Missing/broken → empty strings per key."""
    empty = {k: "" for k in SECTIONS}
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return empty
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return empty
    if not isinstance(data, dict):
        return empty
    return {k: str(data.get(k) or "").strip() for k in SECTIONS}


def build_title_prompt(stories: dict[str, str]) -> str:
    """Last-resort title generation when Stage 2 produced none."""
    return (
        "Give this three-act chronicle a short, specific, evocative title "
        "(3-8 words). Draw on the story's concrete imagery — a place, an "
        "object, a motif. NEVER generic ('Untitled', 'A Chronicle', 'A Story').\n\n"
        f"PAST: {stories.get('past', '')}\n\n"
        f"PRESENT: {stories.get('present', '')}\n\n"
        f"FUTURE: {stories.get('future', '')}\n\n"
        "Return ONLY the title text — no quotes, no explanation."
    )


# ── Stage 3: per-axis Visual Script prompt ────────────────────────────────────

_VISUAL_SCRIPT_GUIDE = (
    "Write a VISUAL SCRIPT: flowing English prose where every concrete visual "
    "element is simultaneously named in danbooru vocabulary within ASCII "
    "parentheses. This text goes directly into an AI image generator.\n"
    "Write exactly 5 flowing paragraphs (2-4 sentences each). Do NOT label the "
    "paragraphs — this structure is an INTERNAL GUIDE ONLY:\n"
    "Paragraph 1 — APPEARANCE: subject count as very first tag (1girl/solo/...), "
    "then hair, eyes, face, expression, clothing, accessories.\n"
    "Paragraph 2 — ACTION: pose, gesture, body language, physical interactions "
    "from the scene.\n"
    "Paragraph 3 — ENVIRONMENT: location, background, setting, time of day.\n"
    "Paragraph 4 — DETAIL: textures, props, fine details, lighting direction "
    "and quality.\n"
    "Paragraph 5 — MOOD: color temperature, atmosphere, overall impression.\n"
    "Embed danbooru tags inline in ASCII parentheses right after each element, "
    'e.g. "A (1girl, solo) with (long_hair, silver_hair) grips a (sword, '
    'holding_sword) on a (rooftop) under (night_sky, full_moon)."\n'
    "At least 2 danbooru tags per sentence. English only. No vague phrases. "
    "NEVER add quality meta-tags (masterpiece, best_quality, highres etc.)."
)


def build_axis_prompt(
    *,
    story_text: str,
    character_tags: list[str],
    character_desc: str,
    prompt_style: str,
    wd14_context: str = "",
) -> str:
    """LLM prompt producing POSITIVE:/NEGATIVE: sections for one axis.

    Character identity keywords are condensed and placed at the head of the
    positive prompt so the same character survives across all three images.
    """
    if character_tags:
        identity_src = (
            "Character identity tags (danbooru): " + ", ".join(character_tags)
        )
    else:
        identity_src = "Character description:\n" + character_desc

    if prompt_style == "natural":
        format_rule = (
            "POSITIVE is the 5-paragraph Visual Script prose described above. "
            "Open paragraph 1 with the condensed identity keywords as inline tags."
        )
    elif prompt_style == "danbooru":
        format_rule = (
            "POSITIVE is a single comma-separated danbooru tag list (30-50 tags): "
            "the condensed identity keywords FIRST, then action, environment, "
            "detail and mood tags for this scene. No prose."
        )
    else:  # danbooru+natural
        format_rule = (
            "POSITIVE is two parts separated by a blank line:\n"
            "(a) a comma-separated danbooru tag line (30-50 tags) — condensed "
            "identity keywords FIRST, then action/environment/detail/mood tags;\n"
            "(b) the 5-paragraph Visual Script prose described above."
        )

    return (
        "You are an expert image generation prompt engineer.\n"
        "Turn ONE act of a story into an image prompt.\n\n"
        f"SCENE (this act of the story):\n{story_text}\n\n"
        f"{identity_src}\n"
        + (f"\n[WD14 tag analysis of the base image]\n{wd14_context}\n" if wd14_context else "")
        + "\n[Visual Script format]\n"
        f"{_VISUAL_SCRIPT_GUIDE}\n\n"
        "Rules:\n"
        "- Condense the character's PHYSICAL identity (hair, eyes, notable "
        "features, signature outfit elements) into a SHORT keyword list and put "
        "it at the very START of the positive prompt, so the same character is "
        "recognizable in every act.\n"
        f"- {format_rule}\n"
        "- Depict THIS act's scene: place, action, lighting, mood — all "
        "grounded in the story text.\n"
        "- NEGATIVE lists only things to avoid (artifacts, wrong elements for "
        "this scene). Short comma-separated tags.\n\n"
        "Output format (exactly these two labels, nothing else):\n"
        "POSITIVE:\n<the positive prompt>\n\n"
        "NEGATIVE:\n<the negative prompt>"
    )


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


# ── Final stage: Japanese translation ─────────────────────────────────────────

def build_translation_prompt(title: str, overall: str, stories: dict[str, str]) -> str:
    """LLM prompt translating the chronicle into natural literary Japanese."""
    return (
        "Translate this chronicle into natural, literary Japanese.\n"
        "Keep the tone and imagery. Do not add or remove content.\n\n"
        f"TITLE: {title}\n\n"
        f"OVERALL: {overall}\n\n"
        f"PAST: {stories.get('past', '')}\n\n"
        f"PRESENT: {stories.get('present', '')}\n\n"
        f"FUTURE: {stories.get('future', '')}\n\n"
        "Answer with JSON only, using exactly these keys:\n"
        '{"title_ja": "...", "overall_ja": "...", "past_ja": "...", '
        '"present_ja": "...", "future_ja": "..."}'
    )


_TRANSLATION_KEYS = ("title_ja", "overall_ja", "past_ja", "present_ja", "future_ja")


def parse_translation_json(raw: str) -> dict[str, str]:
    """Parse the translation output. Missing/broken → empty strings per key."""
    empty = {k: "" for k in _TRANSLATION_KEYS}
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return empty
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return empty
    if not isinstance(data, dict):
        return empty
    return {k: str(data.get(k) or "").strip() for k in _TRANSLATION_KEYS}

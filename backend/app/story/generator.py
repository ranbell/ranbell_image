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
    "tens_of_minutes": "tens of minutes",
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
        f'Setting atmosphere / inspiration: "{worldview}" — '
        "use this as backdrop and visual flavour only; the specific scene "
        "details in the base image above always take precedence."
        if worldview.strip()
        else "No setting was specified — invent a fitting, evocative world yourself."
    )
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    time_block = (
        "⚠️ ABSOLUTE TIME CONSTRAINT — NON-NEGOTIABLE ⚠️\n"
        f'TIME SCALE: {span} between acts (scale key: "{time_scale}").\n\n'
        f"  MUST stay the same: {rules['must_keep']}\n"
        f"  MAY change:         {rules['may_differ']}\n"
        f"  STRICTLY FORBIDDEN: {rules['forbidden']}\n\n"
        "Violating any FORBIDDEN item makes the story WRONG regardless of creativity."
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
        f"{time_block}\n\n"
        "CHARACTER (visual descriptor tags — interpret as appearance attributes, "
        "NOT as character names or story text):\n"
        f"{character_desc}\n\n"
        f"THE {base_axis.upper()} looks exactly like this scene:\n{scene_desc}\n\n"
        f"{world_line}\n"
        f"{mutation_block}\n"
        "Rules:\n"
        f"- The {base_axis} act must match the scene above faithfully.\n"
        "- Each act must show the character in a DISTINCT MOMENT with a DIFFERENT ACTIVITY:\n"
        "  • Never repeat the same pose or action across acts — each needs a specific\n"
        "    physical action (writing, reaching, running, crouching, pressing, lifting…).\n"
        "  • Vary the character's position in the environment: foreground vs background,\n"
        "    different corner, different angle, different relationship to objects.\n"
        "  • Think cinematically: PAST = approach / preparation / discovery;\n"
        "    PRESENT = the peak moment (matches the base image);\n"
        "    FUTURE = departure / aftermath / new beginning.\n"
        "- Never write an act as 'standing in the same place with different lighting'.\n"
        "- The visual distance between acts must strictly follow the TIME SCALE above.\n"
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


def build_overall_prompt(title: str, stories: dict[str, str]) -> str:
    """Last-resort overall-story generation when Stage 2 produced none."""
    return (
        "Write a 2-4 sentence overall arc summary connecting all three acts "
        "of this chronicle into a single journey. Be concrete — use the "
        "story's actual events, not abstract themes.\n\n"
        f"TITLE: {title}\n\n"
        f"PAST: {stories.get('past', '')}\n\n"
        f"PRESENT: {stories.get('present', '')}\n\n"
        f"FUTURE: {stories.get('future', '')}\n\n"
        "Return ONLY the summary text — no headings, no quotes."
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
    "Paragraph 2 — ACTION: INVENT a physically vivid, story-specific pose. "
    "Never default to 'standing' or 'sitting facing forward'. Name a concrete "
    "micro-action: hands pressing against cold glass, fingers tracing a map edge, "
    "body half-turned mid-step, weight shifted onto one knee. "
    "The pose must be emotionally legible at a glance and visually distinct "
    "from a neutral upright stance.\n"
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


# Per-scale visual invariants used in both story and image-prompt generation.
# Keys: must_keep (IDENTICAL to base), may_differ (allowed changes), forbidden.
_SCALE_VISUAL_RULES: dict[str, dict[str, str]] = {
    "minutes": {
        "must_keep": (
            "outfit (IDENTICAL), hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), exact location (SAME room/spot), "
            "season, time of day"
        ),
        "may_differ": "micro-pose, finger/hand position, expression, a gust of wind, what the character is doing with hands/body (writing, reaching, pressing, picking up, etc.)",
        "forbidden": "any outfit change, any location change, any passage of seasons, aging",
    },
    "tens_of_minutes": {
        "must_keep": (
            "outfit (IDENTICAL), hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), same room or immediate outdoor spot, "
            "season, time of day"
        ),
        "may_differ": "pose, expression, minor object placement, slight lighting shift, character's activity and what they are doing, object being interacted with",
        "forbidden": "any outfit change, any location change, any passage of seasons, aging",
    },
    "hours": {
        "must_keep": (
            "outfit (IDENTICAL), hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), same building or outdoor location, season"
        ),
        "may_differ": "light angle and shadow direction, expression, full pose and activity, props in hand, position within the location, slight fatigue",
        "forbidden": "outfit change, location change, season change, aging",
    },
    "days": {
        "must_keep": "hair color and style, core facial features, same general area",
        "may_differ": "outfit (may have changed), time of day, emotional state, minor details",
        "forbidden": "season change, significant aging, major location change",
    },
    "months": {
        "must_keep": "hair color, core facial features, recognizable character identity",
        "may_differ": "seasonal outfit, season, slight physical wear, environment",
        "forbidden": "significant aging, era-level fashion shift",
    },
    "years": {
        "must_keep": "recognizable as the same person",
        "may_differ": "outfit style, slight aging, hair style, environment, life stage",
        "forbidden": "complete transformation that makes the person unrecognizable",
    },
    "decades": {
        "must_keep": "any recognizable trait if plausible",
        "may_differ": "everything — age, fashion era, environment, world",
        "forbidden": "nothing is forbidden — show dramatic transformation",
    },
}


def build_axis_prompt(
    *,
    story_text: str,
    character_tags: list[str],
    character_desc: str,
    prompt_style: str,
    wd14_context: str = "",
    time_scale: str = "years",
    axis: str = "present",
    base_axis: str = "present",
    title: str = "",
    overall: str = "",
    all_stories: dict[str, str] | None = None,
) -> str:
    """LLM prompt producing POSITIVE:/NEGATIVE: sections for one axis.

    Character identity keywords are condensed and placed at the head of the
    positive prompt so the same character survives across all three images.
    """
    if all_stories:
        chronicle_ctx = (
            "FULL CHRONICLE CONTEXT:\n"
            "This image prompt depicts ONE scene from a three-act chronicle.\n\n"
            f"Title: {title}\n"
            f"Overall arc: {overall}\n\n"
            "The three acts (read these to understand the full journey):\n"
            f"  [PAST]:    {all_stories.get('past', '')}\n"
            f"  [PRESENT] ← base image: {all_stories.get('present', '')}\n"
            f"  [FUTURE]:  {all_stories.get('future', '')}\n\n"
            f"You are now generating the image prompt for: [{axis.upper()}]\n"
            f"The base image captures the [{base_axis.upper()}] act.\n\n"
        )
    else:
        chronicle_ctx = ""

    if character_tags:
        identity_src = "[visual tags] " + ", ".join(character_tags)
    else:
        identity_src = "Character description:\n" + character_desc

    # Temporal context block for non-base axes — absolute constraint
    if axis != base_axis:
        span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
        direction = "BEFORE" if axis == "past" else "AFTER"
        rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
        temporal_block = (
            f"\n⚠️ TEMPORAL CONSTRAINT — ABSOLUTE ⚠️\n"
            f"This [{axis.upper()}] scene is {span} {direction} the base scene ({base_axis}).\n"
            f'TIME SCALE: "{time_scale}"\n\n'
            f"Visual elements that MUST be IDENTICAL to the base image:\n"
            f"  {rules['must_keep']}\n"
            f"Visual elements that MAY differ:\n"
            f"  {rules['may_differ']}\n"
            f"STRICTLY FORBIDDEN in this image prompt:\n"
            f"  {rules['forbidden']}\n\n"
            "Do NOT generate anything in the positive prompt that violates the FORBIDDEN list.\n"
        )
    else:
        temporal_block = ""

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
        f"{chronicle_ctx}"
        f"SCENE (this act of the story):\n{story_text}\n"
        f"{temporal_block}\n"
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
        "- Depict THIS act's scene grounded in the story text: place, lighting, mood.\n"
        "- For ACTION/POSE: the story names the dramatic moment; YOU invent the most\n"
        "  visually striking physical instantiation. Choose the exact gesture, the\n"
        "  weight distribution, the prop interaction — be specific, not generic.\n"
        "  The pose must be emotionally distinct from the base image's pose.\n"
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

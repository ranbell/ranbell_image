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

# Interpretive sections appended to both vision prompt variants. They feed the
# story stage as inspiration; the literal sections above them stay the anchor
# for the base act (split apart again by split_vision_sections).
_VISION_NARRATIVE_SECTIONS = (
    "STORY HOOKS: 2-3 speculative one-liners — what might have JUST happened "
    "here, and what might be ABOUT to happen\n"
    "OFF-FRAME: what the world just outside this frame plausibly contains\n"
    "SYMBOLS: 1-3 objects or details in the image with narrative potential\n"
    "ESSENCE: the abstract role of this place/moment, in one phrase "
    '(e.g. "a threshold between two worlds")\n'
)


def build_vision_prompt(full_extraction: bool) -> str:
    """VLM prompt for the base image.

    full_extraction=True (external image without wd14_tags): describe everything.
    full_extraction=False: wd14_tags already cover appearance — only describe
    what tags cannot express (environment, mood, narrative reading).
    """
    if full_extraction:
        return (
            "Describe this image precisely for an illustrator, in English.\n"
            "Cover, in short labeled sections:\n"
            "CHARACTER: physical traits (hair color/length, eye color, body, age impression)\n"
            "OUTFIT: clothing and accessories\n"
            "SCENE: background, environment, time of day, weather\n"
            "MOOD: overall mood and emotional tone\n"
            f"{_VISION_NARRATIVE_SECTIONS}"
            "Keep CHARACTER/OUTFIT/SCENE/MOOD concrete and visual — no names, "
            "no guesses there. Speculation belongs ONLY in the last four sections."
        )
    return (
        "Describe this image for an illustrator, in English.\n"
        "Cover, in short labeled sections:\n"
        "SCENE: background, environment, time of day, weather\n"
        "MOOD: overall mood, atmosphere and emotional tone\n"
        f"{_VISION_NARRATIVE_SECTIONS}"
        "Do NOT describe the character's appearance or clothing. "
        "Keep SCENE/MOOD concrete and visual; speculation belongs ONLY in the "
        "last four sections."
    )


# Tolerant to markdown decoration: **STORY HOOKS:**, ## OFF-FRAME: , SYMBOLS —
_VISION_NARRATIVE_RE = re.compile(
    r"(?im)^[ \t>#*-]{0,4}\**\s*(?:STORY[ _]?HOOKS?|OFF[-_ ]?FRAME|SYMBOLS|ESSENCE)\s*\**\s*[:：]"
)


def split_vision_sections(text: str) -> tuple[str, str]:
    """Split VLM output into (literal_desc, narrative_hooks).

    Cuts at the first narrative label (STORY HOOKS/OFF-FRAME/SYMBOLS/ESSENCE).
    No labels found → (text, "") so older-style output keeps working.
    """
    m = _VISION_NARRATIVE_RE.search(text)
    if not m:
        return text.strip(), ""
    return text[: m.start()].strip(), text[m.start():].strip()


# ── Stage 2: title + overall + three acts ─────────────────────────────────────

def _boldness_line(divergence: float) -> str:
    """Story-boldness rule scaled by the Transmute divergence slider."""
    if divergence < 0.3:
        return (
            "- Surprise the reader quietly: prefer grounded, intimate turns "
            "over spectacle."
        )
    if divergence <= 0.6:
        return (
            "- Prefer the unexpected-but-plausible development over the obvious "
            "one; avoid clichés."
        )
    return (
        "- Take the boldest interpretation that still makes narrative sense. "
        "Avoid the first idea that comes to mind — subvert the obvious reading."
    )


def build_story_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    base_axis: str,
    worldview: str,
    time_scale: str = "years",
    mutation_tags: list[str] | None = None,
    story_hooks: str = "",
    divergence: float = 0.0,
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
    hooks_block = ""
    if story_hooks.strip():
        hooks_block = (
            "\nNARRATIVE SEEDS — an interpretive reading of the base image "
            "(inspiration only; you may extend or contradict it):\n"
            f"{story_hooks.strip()}\n"
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
        f"{hooks_block}"
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
        "- The arc must contain ONE turning point or reversal: a belief, plan or "
        "situation that flips.\n"
        "- Pick ONE concrete motif (an object or detail from the scene) that "
        "appears in all three acts and TRANSFORMS in meaning across them.\n"
        "- Link the acts by cause and effect (because of PAST, PRESENT; because "
        "of PRESENT, FUTURE) — never three disconnected vignettes.\n"
        "- Give each act a different dominant emotion.\n"
        "- Within the MAY-change list of the time constraint above, maximize "
        "difference: anything ALLOWED to change between acts SHOULD visibly "
        "change (location, outfit, weather — whichever the scale permits).\n"
        f"{_boldness_line(divergence)}\n"
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


# ── Identity tag scoping (chronicle-specific WD14 handling) ───────────────────
#
# Chronicle depicts a DIFFERENT moment in time, so unlike Refine we must never
# force the base image's scene/pose/background/time-of-day tags into an axis
# prompt. Only identity traits are anchored, and which categories count as
# "identity" shrinks with the time scale (outfit is identity at minutes, not at
# years). Whitelist polarity: an unrecognized tag is never injected.

_COLOR_WORDS = (
    "black|white|blonde?|brown|red|pink|purple|violet|blue|green|grey|gray|"
    "silver|orange|aqua|amber|yellow|golden?|platinum|crimson|multicolored|"
    "gradient|two-tone|streaked|dark|light"
)
_HAIR_COLOR_RE = re.compile(rf"^(?:\w+_)?(?:{_COLOR_WORDS})_hair$")
_EYE_COLOR_RE = re.compile(rf"^(?:\w+_)?(?:{_COLOR_WORDS})_eyes$")

_HAIR_STYLE_TAGS = frozenset({
    "long_hair", "short_hair", "medium_hair", "very_long_hair",
    "absurdly_long_hair", "wavy_hair", "curly_hair", "straight_hair",
    "messy_hair", "spiked_hair", "hair_bun", "double_bun", "hair_intakes",
    "hair_over_one_eye", "hair_between_eyes", "bob_cut", "hime_cut",
    "pixie_cut", "drill_hair", "blunt_bangs", "swept_bangs",
})
_HAIR_STYLE_TOKENS = frozenset({
    "braid", "braids", "twintails", "twintail", "ponytail", "bangs", "ahoge",
    "sidelocks",
})
_FACE_BODY_TOKENS = frozenset({
    "mole", "freckles", "fang", "fangs", "horn", "horns", "wing", "wings",
    "tail", "ears", "skin", "scar", "tattoo", "halo", "heterochromia",
    "elf", "tanned", "tanlines",
})
# Clothing/accessory half of vocab_bank._CHARACTER_KEYWORDS, token form
_OUTFIT_TOKENS = frozenset({
    "dress", "uniform", "outfit", "shirt", "skirt", "jacket", "coat",
    "blouse", "sweater", "hoodie", "kimono", "yukata", "leotard", "bikini",
    "swimsuit", "hat", "ribbon", "bow", "bowtie", "necktie", "glove",
    "gloves", "thighhighs", "pantyhose", "boots", "shoes", "socks", "scarf",
    "cape", "cloak", "armor", "necklace", "earring", "earrings", "glasses",
    "choker", "apron", "vest", "pants", "shorts", "belt", "corset",
})


def classify_identity_tag(tag: str) -> str | None:
    """'hair_color'|'hair_style'|'eyes'|'face'|'outfit'|None (= never inject)."""
    t = tag.strip().lower().replace(" ", "_")
    if _HAIR_COLOR_RE.match(t):
        return "hair_color"
    toks = set(t.split("_"))
    if t in _HAIR_STYLE_TAGS or toks & _HAIR_STYLE_TOKENS:
        return "hair_style"
    if _EYE_COLOR_RE.match(t) or t == "heterochromia":
        return "eyes"
    if toks & _FACE_BODY_TOKENS:
        return "face"
    if toks & _OUTFIT_TOKENS:
        return "outfit"
    return None


# Which identity categories are still anchored at each time scale
# (derived from _SCALE_VISUAL_RULES must_keep lists above).
_IDENTITY_CATEGORIES_BY_SCALE: dict[str, frozenset[str]] = {
    "minutes": frozenset({"hair_color", "hair_style", "eyes", "face", "outfit"}),
    "tens_of_minutes": frozenset({"hair_color", "hair_style", "eyes", "face", "outfit"}),
    "hours": frozenset({"hair_color", "hair_style", "eyes", "face", "outfit"}),
    "days": frozenset({"hair_color", "hair_style", "eyes", "face"}),
    "months": frozenset({"hair_color", "hair_style", "eyes", "face"}),
    "years": frozenset({"hair_color", "eyes", "face"}),
    "decades": frozenset(),
}


def identity_tags_for_scale(
    wd14_tags: list[str], time_scale: str, *, limit: int = 12
) -> list[str]:
    """Scale-gated identity subset of the base image's wd14 tags, order kept."""
    allowed = _IDENTITY_CATEGORIES_BY_SCALE.get(
        time_scale, _IDENTITY_CATEGORIES_BY_SCALE["years"]
    )
    if not allowed:
        return []
    result: list[str] = []
    for tag in wd14_tags:
        category = classify_identity_tag(tag)
        if category in allowed:
            result.append(tag)
            if len(result) >= limit:
                break
    return result


# Mirror of api.ai._SUBJECT_ANCHOR_TAGS (kept local so this module stays
# import-free / unit-testable) — update both together.
_SUBJECT_ANCHORS = frozenset({
    "1girl", "1boy", "2girls", "2boys", "3girls", "4girls", "6+girls",
    "solo", "couple", "multiple_girls", "multiple_boys", "multiple girls",
})


def inject_identity_tags(tag_line: str, identity: list[str]) -> str:
    """Insert identity tags after the last subject-anchor tag, dedup (ci)."""
    parts = [t.strip() for t in tag_line.split(",") if t.strip()]
    existing = {p.lower() for p in parts}
    new: list[str] = []
    for tag in identity:
        key = tag.lower()
        if key not in existing:
            new.append(tag)
            existing.add(key)
    if not new:
        return tag_line
    cut = max(
        (i + 1 for i, p in enumerate(parts) if p.lower() in _SUBJECT_ANCHORS),
        default=0,
    ) or len(parts)
    return ", ".join(parts[:cut] + new + parts[cut:])


_INLINE_TAG_GROUP_RE = re.compile(r"\(([^)]+)\)")


def collect_prompt_tags(positive: str) -> list[str]:
    """Harvest tag candidates from a positive prompt, underscore form, deduped.

    Comma-only lines contribute every segment (same heuristic as
    remove_conflict_tags); prose lines contribute their inline (tag, tag)
    groups, so natural-style prompts get conflict cleanup too.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(raw: str) -> None:
        tag = raw.strip().replace(" ", "_")
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)

    for line in positive.split("\n"):
        if "," in line and "." not in line:
            for seg in line.split(","):
                _add(seg)
        else:
            for m in _INLINE_TAG_GROUP_RE.finditer(line):
                for seg in m.group(1).split(","):
                    _add(seg)
    return result


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
            "COMPOSITION: choose a camera setup clearly DIFFERENT from the base "
            "image. Pick the shot that best dramatizes this act from: close-up, "
            "upper_body, cowboy_shot, full_body, wide_shot, from_above, "
            "from_below, from_behind, from_side, dutch_angle — and include it "
            "as danbooru tags.\n"
        )
    else:
        temporal_block = ""

    if not wd14_context:
        wd14_block = ""
    elif axis != base_axis:
        wd14_block = (
            f"\n[WD14 tags of the BASE image ({base_axis} act) — use ONLY for "
            "character identity and art style continuity; do NOT copy its "
            "scene, pose, background, or time-of-day tags into this act]\n"
            f"{wd14_context}\n"
        )
    else:
        wd14_block = f"\n[WD14 tag analysis of the base image]\n{wd14_context}\n"

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
        f"{wd14_block}"
        "\n[Visual Script format]\n"
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


def remove_conflict_tags(
    positive: str, conflicts: set[str], *, include_prose_groups: bool = False
) -> str:
    """Remove conflicting danbooru tags from the tag portion of a positive prompt.

    Comma-separated tag lines are always filtered (matching how
    _find_conflict_tags reports plain tag names). With include_prose_groups,
    inline (tag, tag) groups on prose lines are filtered too; a group whose
    tags are all removed disappears entirely (never an empty "()").
    """
    if not conflicts:
        return positive

    def _fix_group(m: re.Match) -> str:
        tags = [t.strip() for t in m.group(1).split(",")]
        kept = [t for t in tags if t and t.replace(" ", "_") not in conflicts]
        return f"({', '.join(kept)})" if kept else ""

    lines = positive.split("\n")
    cleaned: list[str] = []
    for line in lines:
        if "," in line and "." not in line:
            tags = [t.strip() for t in line.split(",")]
            kept = [t for t in tags if t and t.replace(" ", "_") not in conflicts]
            cleaned.append(", ".join(kept))
        elif include_prose_groups:
            fixed = _INLINE_TAG_GROUP_RE.sub(_fix_group, line)
            cleaned.append(re.sub(r"  +", " ", fixed))
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

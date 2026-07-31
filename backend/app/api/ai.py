import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..ai.tile_image import create_tile_image
from ..jobs.sse_stream import queue_sse_response
from ..prompt.visual_spec import (
    DEFAULT_PROSE_PARAGRAPHS,
    LABELED_TAG_FOOTER,
    REFINE_CAT_FIELDS as _REFINE_CAT_FIELDS,
    clamp_prose_paragraphs,
    parse_visual_script as _parse_visual_script_sections,
    strip_section_markers as _strip_visual_script_markers,
    visual_script_length_line,
)
from ..runtime_config import get_runtime_config
from ..scanner.scanner import register_image
from ..spooler.models import JobLane
from ..tags.subject_anchors import (
    SUBJECT_ANCHOR_TAGS as _SUBJECT_ANCHOR_TAGS,
    insert_after_anchors,
)
from .sort_utils import sort_docs

logger = logging.getLogger(__name__)

# Tag-merge helpers now live in prompt/tag_merge.py (they are pure functions and
# Muse needs them without dragging in the whole route module). Re-exported here
# because runners.py and inspire.py import them from this module.
from ..prompt.tag_merge import (  # noqa: E402
    _WD14_MUST_INCLUDE_THRESHOLD,
    _ROLE_CONTEXT_LABELS,
    _apply_must_replacements,
    _build_all_must,
    _build_weighted_wd14_context,
    _correct_prose_wd14_conflicts,
    _enforce_wd14_on_cat_tags,
    _filter_tags_for_role,
    _inject_wd14_must_tags,
    _resolve_weights,
    _tags_conflict,
    filter_tag_list,
    removal_tag_set,
)
router = APIRouter(prefix="/api/ai")


# ── Pydantic models ────────────────────────────────────────────────────────────

class PipelineRequest(BaseModel):
    sha256s: list[str] = []


class RefineRequest(BaseModel):
    sha256s: list[str]
    weights: list[float] = []
    instruction: str = ""
    instruction_mode: Literal["none", "basic", "enhanced"] = "basic"
    temperature: float = 0.7
    num_ctx: int = 16384
    prompt_style: Literal["natural", "danbooru", "detailed"] = "natural"
    negative_prompt: bool = False
    auto_submit: bool = False
    workflow_name: str = ""
    positive_node_id: str = ""
    negative_node_id: str = ""
    batch_count: int = 1
    direct_prompt: str | None = None
    direct_negative_prompt: str | None = None
    inspire_context: dict | None = None
    use_ref_seed: bool = False
    suppress_conflict_tags: bool = False
    wd14_common_ratio: float = 0.3
    wd14_unique_count: int = 20
    divergence: float = 0.0  # 0–1: mutate style/scene away from references (Transmute)
    variation_count: int = 1  # natural style only: run the prose pass N times (1–3) at rising temperatures
    # natural style Visual Script length (paragraphs 3–7). Models differ in
    # which length they handle cleanly — UI exposes this as a slider.
    prose_paragraphs: int = DEFAULT_PROSE_PARAGRAPHS
    roles: list[str] = []  # per-image role aligned with sha256s: 'both' | 'style' | 'content'
    emotion_shift: str = ""  # target emotion dimension (e.g. 'nostalgia') to rewrite the register toward


class SearchRequest(BaseModel):
    query: str
    n_results: int | None = None  # None → use the admin-configured semantic_search_limit
    tag: str = ""
    sort: str = "relevance"


class SimilarRequest(BaseModel):
    sha256: str
    n_results: int = 24


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


_STYLE_INSTRUCTIONS = {
    "danbooru": (
        "Generate an image generation prompt in Danbooru / Stable Diffusion tag style.\n"
        "SINGLE-IMAGE SYNTHESIS RULE: When multiple reference images are provided, "
        "synthesize their elements into ONE unified scene. Do NOT output tags that imply "
        "multiple panels or collage layouts. FORBIDDEN TAGS (never use): multiple_views, "
        "reference_sheet, character_sheet, split_image, collage, comparison, "
        "before_and_after, diptych, triptych, side-by-side, panel_layout. "
        "Instead, pick one dominant composition and weave the strongest visual elements "
        "from all references into that single image.\n\n"
        "CHARACTER-FIRST RULE: When characters are present, subject count (1girl, 1boy, "
        "solo, 2girls, etc.) MUST be the very first tag(s) in the output. Never omit it. "
        "If [User instruction] is a story, translate every concrete physical action "
        "(gripping, touching, running, hugging, reaching, kneeling…) into pose/action tags "
        "— these define the character's pose and MUST appear in the output.\n\n"
        "Target: 80–120 comma-separated English tags (approximately 150–200 words total).\n"
        "Analyze the reference image(s) and metadata exhaustively. "
        "Extract specific, concrete tags across EVERY applicable category below. "
        "Prefer specific tags over generic ones (e.g. 'twintails' over 'hair', "
        "'thighhighs' over 'socks'). Never skip a category if it is visible.\n\n"
        "REQUIRED CATEGORIES (fill as many tags per category as the image supports):\n"
        "- SUBJECT & COUNT: 1girl / 1boy / multiple_girls / solo / duo etc.\n"
        "- HAIR: color (blonde_hair, silver_hair, black_hair…), length (long_hair, short_hair, medium_hair), "
        "style (twintails, ponytail, braid, side_bun, bob_cut, ahoge, hair_over_one_eye), "
        "texture (wavy_hair, curly_hair, straight_hair), accessories (hair_ribbon, hairband, hairclip)\n"
        "- EYES: color (blue_eyes, red_eyes, heterochromia…), shape (tareme, tsurime, large_eyes), "
        "expression (half-closed_eyes, sparkling_eyes, teary_eyes, looking_at_viewer, looking_away)\n"
        "- FACE & EXPRESSION: smile, grin, blush, open_mouth, closed_mouth, serious, shy, angry, "
        "surprised, pout, frown, tears\n"
        "- BODY: build (slim, athletic, petite, curvy), skin (pale_skin, tan, dark_skin), "
        "distinctive features (pointy_ears, tail, wings, horns, large_breasts, flat_chest)\n"
        "- UPPER CLOTHING: specific garment names + colors + details "
        "(white_shirt, black_jacket, sailor_collar, off_shoulder, crop_top, frills, lace, collar)\n"
        "- LOWER CLOTHING: skirt length/style, pants type, legwear "
        "(pleated_skirt, miniskirt, shorts, thighhighs, pantyhose, knee_socks, leggings)\n"
        "- ACCESSORIES & DETAILS: bow, ribbon, hat, beret, glasses, sunglasses, "
        "earrings, necklace, choker, bracelet, gloves, cape, apron, wings, weapon, bag\n"
        "- FOOTWEAR: boots, heels, sneakers, loafers, mary_janes, sandals, barefoot\n"
        "- POSE & ACTION: standing, sitting, lying, kneeling, running, jumping, floating, "
        "arms_raised, hand_on_hip, arms_behind_back, reaching_out, fighting_stance, leaning_forward\n"
        "- COMPOSITION & FRAMING: upper_body, full_body, close-up, bust_shot, cowboy_shot, "
        "from_below, from_above, from_side, dutch_angle, profile, portrait\n"
        "- BACKGROUND & SETTING: specific location tags "
        "(forest, city_street, indoors, bedroom, school, ruins, sky, clouds, "
        "ocean, beach, mountains, fantasy_world, space, night_sky, starry_sky)\n"
        "- LIGHTING: sunlight, moonlight, golden_hour, backlight, rim_light, "
        "dramatic_lighting, soft_lighting, neon_lights, candlelight, dark, bright\n"
        "- ATMOSPHERE & COLOR PALETTE: dreamy, epic, romantic, dark_atmosphere, "
        "warm_colors, cool_colors, pastel_colors, vibrant, monochrome, bokeh\n"
        "- ART STYLE: anime, manga_style, illustration, oil_painting, watercolor, "
        "cel_shading, sketch, lineart, detailed, painterly, digital_art\n"
        "- QUALITY: masterpiece, best_quality, ultra-detailed, highres, 8k, "
        "absurdres, sharp_focus, intricate_details, professional\n\n"
        "Output ONLY the flat comma-separated tag list. No category labels, no line breaks between tags, "
        "no explanation. Aim to fill 150–200 words."
    ),
    "detailed": (
        "CRITICAL OUTPUT FORMAT RULES — violating any of these is an error:\n"
        "- Do NOT output a comma-separated tag list.\n"
        "- Do NOT output plain tags. Do NOT output a flat danbooru-style list.\n"
        "- You MUST output EXACTLY 8 sections using bold markdown headers (**Header:**).\n"
        "- Start your response directly with the first **bold header** — no preamble.\n\n"
        "Generate a structured, highly detailed image generation prompt.\n"
        "SINGLE-IMAGE SYNTHESIS RULE: When multiple reference images are provided, synthesize them "
        "into ONE unified scene — not a collage, not a diptych.\n\n"
        "CHARACTER-FIRST RULE: When characters are present, subject count (1girl, 1boy, solo, "
        "2girls, etc.) MUST appear first in **Characters & Composition**. If [User instruction] "
        "describes a story, translate every concrete physical action (gripping, touching, running, "
        "hugging, reaching, kneeling…) into specific pose/action descriptions — these MUST appear.\n\n"
        "Output EXACTLY these 8 sections with bold markdown headers:\n\n"
        "**Core Subject & Scene Setting:** [subject, genre, overall mood — 1-2 sentences]\n"
        "**Characters & Composition:** [count, hair color/style, eye color, pose, framing, clothing details]\n"
        "**Lighting & Atmosphere:** [light source, direction, color temperature, shadow quality, ambience]\n"
        "**Style & Artistic Influence:** [art style, medium, influences, rendering technique]\n"
        "**Details & Textures:** [skin, fabric, hair texture, surface materials, fine details]\n"
        "**Color Palette:** [dominant colors, accent colors, saturation, overall tone]\n"
        "**Camera & Lens Effects:** [shot type, angle, depth of field, bokeh, lens flare]\n"
        "**Refinements & Modifiers:** [comma-separated quality/detail keywords — e.g. masterpiece, volumetric lighting, hyperdetailed]\n\n"
        "Fill each section based SOLELY on the reference image. Be specific and concrete.\n\n"
        "After all 8 sections, output this JSON block — nothing else after it:\n\n"
        "```json\n"
        "{\n"
        '  "subject_tags": "subject count danbooru tags, e.g. 1girl,solo",\n'
        '  "hair_tags": "comma,separated,danbooru,tags",\n'
        '  "expression_tags": "comma,separated,danbooru,tags",\n'
        '  "clothing_tags": "comma,separated,danbooru,tags",\n'
        '  "accessory_tags": "comma,separated,danbooru,tags",\n'
        '  "pose_tags": "comma,separated,danbooru,tags",\n'
        '  "background_tags": "comma,separated,danbooru,tags",\n'
        '  "object_tags": "comma,separated,danbooru,tags",\n'
        '  "lighting_tags": "comma,separated,danbooru,tags"\n'
        "}\n"
        "```"
    ),
}

_NEGATIVE_INSTRUCTION = (
    "\n\nAlso generate a NEGATIVE PROMPT listing elements to avoid.\n"
    "You MUST use EXACTLY this output format — two labeled sections, nothing else:\n\n"
    "POSITIVE:\n"
    "[your positive prompt here — tags and prose based on the reference image]\n\n"
    "NEGATIVE:\n"
    "[comma-separated negative tags — elements to avoid, based on the reference context]\n\n"
    "CRITICAL RULES — violating any of these is an error:\n"
    "- The word POSITIVE: MUST appear alone on its own line at the very start.\n"
    "- Then your positive prompt (tags + prose for natural style, tags only for danbooru).\n"
    "- Then a blank line, then the word NEGATIVE: alone on its own line.\n"
    "- Then the negative tags — comma-separated, no prose.\n"
    "- BOTH sections are MANDATORY. A response without NEGATIVE: is wrong.\n"
    "- No other text, markdown, or explanation — only the two labeled sections."
)


def _format_instruction_block(instruction: str, instruction_framing: bool) -> str:
    if not instruction:
        return "Create a refined, high-quality image generation prompt."
    if instruction_framing:
        return (
            "[PROMPT ENGINEERING DIRECTIVE — NOT NARRATIVE CONTENT]\n"
            "Apply the following as a structural modification to the output prompt.\n"
            "DO NOT incorporate it as scene description. DO NOT ignore it.\n"
            "DO NOT turn text elements into character actions or props.\n\n"
            f"Directive: {instruction}"
        )
    return instruction


def _build_vlm_prompt(
    context: str,
    instruction: str,
    prompt_style: str,
    with_negative: bool,
    instruction_framing: bool = False,
) -> str:
    style_instr = _STYLE_INSTRUCTIONS.get(prompt_style, _STYLE_INSTRUCTIONS["danbooru"])
    neg_instr = _NEGATIVE_INSTRUCTION if with_negative else (
        "\n\nOutput the positive prompt only — no labels, no explanation, "
        "and do NOT include a negative prompt or any 'Negative:' section."
    )
    instr_block = _format_instruction_block(instruction, instruction_framing)

    return (
        "You are an expert image generation prompt engineer.\n"
        "Analyze the reference image(s) and the metadata below, then craft a superior prompt.\n"
        "PRIMARY SOURCE: Derive visual content (art style, palette, setting) from [Reference metadata]. "
        "Format examples in [Style directive] are for OUTPUT STRUCTURE ONLY — do not copy them. "
        "However, [User instruction] describes the story and characters: character identity, "
        "appearance, and concrete actions in the instruction TAKE PRIORITY and MUST be reflected.\n\n"
        "UNIFIED COMPOSITION MANDATE: Your output is a prompt for ONE SINGLE IMAGE. "
        "Regardless of how many reference images are provided, you must synthesize them "
        "into a single coherent scene — not a collage, not a diptych, not a reference sheet. "
        "Treat the reference images as a mood board: extract the subject, palette, style, "
        "and atmosphere, then compose them into one unified visual. "
        "If references conflict, let influence weights guide which elements take priority.\n\n"
        f"[Style directive]\n{style_instr}\n\n"
        f"[Reference metadata]\n{context}\n\n"
        f"[User instruction]\n{instr_block}"
        f"{neg_instr}"
    )


# ── Natural style: two-pass (tags → prose) prompt builders ────────────────────
#
# A small VLM (e.g. a 2B model) is unreliable at producing a tag line AND an
# 80-120 word prose paragraph in one response. Splitting into two focused,
# single-task calls — tags first, then a long descriptive paragraph seeded
# with those tags — is far more reliable and lets the prose call target a
# longer, more detailed result without competing with the tag-formatting task.

_NATURAL_TAGS_INSTRUCTION = (
    "Generate the TAG portion of an image generation prompt for FLUX / Anima models.\n"
    "SINGLE-IMAGE SYNTHESIS RULE: When multiple reference images are provided, "
    "you MUST synthesize and merge their elements into ONE unified, coherent scene. "
    "Do NOT describe separate panels, collage layouts, side-by-side views, diptychs, "
    "or reference sheets. Choose one primary subject and setting, then blend the most "
    "compelling visual elements from all references into that single composition. "
    "Avoid any tags or phrases implying multiple separate images (e.g. 'multiple_views', "
    "'split_image', 'comparison', 'collage').\n\n"
    "Output 40–60 comma-separated Danbooru-style tags on a single line, covering: "
    "subject & count, hair, eyes, face/expression, body, clothing, accessories, "
    "footwear, pose, composition, background, lighting, atmosphere, art style, quality.\n\n"
    "STRICT FORMATTING RULES — violating any of these is an error:\n"
    "- Output ONLY the tag line. No prose, no sentences, no markdown, no labels.\n"
    "- Do NOT write labels like 'Tags:', 'Prompt:', 'Output:' etc.\n"
    "- CHARACTER-FIRST RULE: When characters are present, subject count (1girl, 1boy, "
    "solo, 2girls, etc.) MUST be the very first tag(s). Never omit subject count.\n"
    "- ACTION RULE: All concrete physical actions from [User instruction] story MUST "
    "appear as pose/action tags (gripping, touching, running, hugging, kneeling, etc.).\n"
    "- EXPLICIT TAG RULE: NEVER use literary, vague, or euphemistic language for actions "
    "or body parts. Every body part, contact point, and action MUST be expressed as an "
    "explicit Danbooru tag. Wrong: 'intimate_gesture', 'tender_touch', 'closeness'. "
    "Correct: 'hand_on_another's_cheek', 'gripping', 'breast_grab', 'hand_on_ass', "
    "'lap_pillow', 'hair_grab', 'wrist_grab', 'finger_in_mouth' — use the real tag.\n"
    "- Start your response DIRECTLY with subject count if characters are present "
    "(e.g. '1girl, solo, gripping, ...' or '2girls, hug, ...'); "
    "otherwise start with the primary scene element."
)

_NATURAL_TAGS_NEGATIVE_INSTRUCTION = (
    "\n\nAlso generate a NEGATIVE PROMPT listing elements to avoid, as comma-separated tags.\n"
    "You MUST use EXACTLY this output format — two labeled sections, nothing else:\n\n"
    "POSITIVE:\n"
    "[your positive tag line — 40-60 comma-separated tags]\n\n"
    "NEGATIVE:\n"
    "[comma-separated negative tags — elements to avoid]\n\n"
    "CRITICAL RULES — violating any of these is an error:\n"
    "- The word POSITIVE: MUST appear alone on its own line at the very start.\n"
    "- Then the positive tag line (tags only, no prose, no headers).\n"
    "- Then a blank line, then the word NEGATIVE: alone on its own line.\n"
    "- Then the negative tags — comma-separated, no prose, no headers.\n"
    "- BOTH sections are MANDATORY. A response without NEGATIVE: is wrong.\n"
    "- No other text, markdown, or explanation — only the two labeled sections."
)

_NATURAL_PROSE_INSTRUCTION = (
    "You already extracted the tag list below from the reference image(s). "
    "Now write ONLY a long, vivid descriptive paragraph of the same single unified scene, "
    "for use as an image generation prompt.\n\n"
    "LENGTH: 150-250 words, written as 10-14 flowing sentences. This is a hard minimum — "
    "a short answer is an error.\n\n"
    "Cover ALL of the following, drawing only from the reference image and the tags below:\n"
    "- the subject's appearance, expression, and notable physical details\n"
    "- hair and eye details (color, length, style)\n"
    "- clothing materials, colors, textures, and how they fit or move\n"
    "- pose, gesture, and body language\n"
    "- the background environment, described concretely (not just named)\n"
    "- lighting: direction, color temperature, and quality "
    "(e.g. 'warm amber rim light', not 'nice lighting')\n"
    "- overall mood and atmosphere\n"
    "- artistic style or rendering medium\n\n"
    "Style example (tone only — do not reuse this content): "
    "\"She stands at the edge of a rain-slicked rooftop, her long silver hair whipping "
    "sideways in the wind as cool blue neon spills across her damp jacket, one hand "
    "braced on the railing while she looks back over her shoulder with a wary half-smile...\" "
    "— write in this kind of specific, sensory, multi-clause prose.\n\n"
    "STRICT RULES — violating any of these is an error:\n"
    "- Write ONLY flowing prose sentences. Do NOT output a comma-separated tag list.\n"
    "- Do NOT use asterisks, pound signs, or any markdown.\n"
    "- Do NOT write labels like 'Prose:', 'Description:', 'Paragraph:' etc.\n"
    "- Do NOT repeat the tag list verbatim — describe the scene in your own words.\n"
    "- Start your response DIRECTLY with the first sentence (no intro, no preamble)."
)

_NATURAL_PROSE_RETRY_PREFIX = (
    "Your previous attempt did not follow the instructions below — it was missing, too "
    "short, or looked like a tag list instead of prose. Try again, and this time follow "
    "the instructions exactly: write ONLY flowing narrative sentences, at least 150 words, "
    "describing the scene in vivid sensory detail. Do NOT output any comma-separated tag "
    "list.\n\n"
)

def _natural_visual_script_instruction(
    prose_paragraphs: int = DEFAULT_PROSE_PARAGRAPHS,
) -> str:
    n = clamp_prose_paragraphs(prose_paragraphs)
    length_line = visual_script_length_line(n)
    return (
        "Write a VISUAL SCRIPT for an AI image generator: flowing English prose where every concrete\n"
        "visual element is simultaneously named in danbooru vocabulary within ASCII parentheses.\n"
        "This text goes directly into an AI image generator — clarity and natural English are critical.\n\n"
        "# OUTPUT FORMAT\n"
        f"{length_line} "
        "Do NOT write [CHARACTER], [ACTION], [SCENE], [DETAIL], [MOOD] "
        "or any bracket markers in the output — those are INTERNAL STRUCTURAL GUIDES ONLY.\n"
        "Embed danbooru tags inline in ASCII parentheses immediately after each element:\n"
        "Example: \"A (1girl, solo) with (long_hair, brown_hair) grips a (sword, holding_sword) "
        "with both hands, knuckles white. Her (school_uniform) shifts as she stands in a (park, outdoor), "
        "bathed in (afternoon_sun, warm_light). The (green_trees, sky) frame her determined expression.\"\n\n"
        "# INTERNAL FOCUS GUIDE (do NOT echo these labels in output)\n"
        "Focus APPEARANCE: subject count as very first tag (1girl/solo/2girls/1boy…), "
        "then hair, eyes, face, expression, clothing, accessories.\n"
        "Focus ACTION: pose, gesture, body language, physical interactions from the story.\n"
        "Focus ENVIRONMENT: location, background, setting, time of day.\n"
        "Focus DETAIL: textures, props, fine details, lighting direction and quality.\n"
        "Focus MOOD: color temperature, atmosphere, overall impression.\n\n"
        "# RULES\n"
        "0. SUBJECT-FIRST: The VERY FIRST parenthetical MUST be the subject count: "
        "(1girl, solo) or (2girls) or (1boy), etc. Never start with a scene element.\n"
        "1. ACTION-ANCHOR: All concrete physical actions from [User instruction] MUST appear as "
        "danbooru action tags in the ACTION focus. "
        "Translate story verbs: 握る→(gripping,clenched_hand), 触れる→(touching,fingertips), "
        "抱きしめる→(hug,arms_around_another's_neck), 手を繋ぐ→(holding_hands), "
        "走る→(running,dynamic_pose), 跪く→(kneeling,one_knee), 手を伸ばす→(reaching,outstretched_arm).\n"
        "2. EXPLICIT TAG RULE: NEVER use euphemistic language for actions or body parts. "
        "WRONG: '(tender_touch)', '(intimate_gesture)', '(closeness)'. "
        "CORRECT: '(hand_on_another's_cheek)', '(breast_grab)', '(gripping)', '(lap_pillow)', "
        "'(hair_grab)', '(wrist_grab)', '(nape)', '(collarbone)', '(thigh_grab)'.\n"
        "3. English only — at least 2 danbooru tags per sentence.\n"
        "4. No vague phrases: no 'somehow', 'a sense of', 'filled with emotion'.\n"
        "5. NEVER add quality meta-tags (masterpiece, best_quality, highres etc.).\n\n"
        f"Write the {n}-paragraph prose now. Then, on a new line after the prose, output ONLY these "
        "labeled tag lines (no JSON, no code block, no extra text):\n\n"
        f"{LABELED_TAG_FOOTER}"
    )


# Default (5-paragraph) instruction kept for import/test back-compat.
_NATURAL_VISUAL_SCRIPT_INSTRUCTION = _natural_visual_script_instruction(
    DEFAULT_PROSE_PARAGRAPHS
)


def _build_natural_tags_prompt(
    context: str,
    instruction: str,
    with_negative: bool,
    instruction_framing: bool = False,
) -> str:
    neg_instr = _NATURAL_TAGS_NEGATIVE_INSTRUCTION if with_negative else (
        "\n\nOutput the positive tag line only — no labels, no explanation, "
        "and do NOT include a negative prompt or any 'Negative:' section."
    )
    instr_block = _format_instruction_block(instruction, instruction_framing)

    character_mandate = (
        "[CHARACTER & ACTION EXTRACTION MANDATE]\n"
        "If [User instruction] contains a story or scenario with characters:\n\n"
        "STEP 1 — Subject: Identify all characters present.\n"
        "  Output their count/gender as the VERY FIRST tags (1girl, 1boy, solo, 2girls, etc.).\n"
        "  Never omit the subject count when characters are present.\n\n"
        "STEP 2 — Actions: Translate every concrete physical action in the story into Danbooru tags.\n"
        "  Story verb → Danbooru action tag examples:\n"
        "    触れる / touch      → touching, hand_on_another's_cheek, fingertips\n"
        "    握る / grip         → gripping, clenched_hand, grabbing, holding\n"
        "    抱きしめる / hug    → hug, embrace, arms_around_another's_neck\n"
        "    引っ張る / pull     → pulling, grabbing, holding_another's_wrist\n"
        "    撫でる / stroke     → petting, hand_on_another's_head\n"
        "    走る / run          → running, dynamic_pose, leaning_forward\n"
        "    跪く / kneel        → kneeling, one_knee\n"
        "    手を伸ばす / reach  → reaching, outstretched_arm, hand_out\n"
        "    手を繋ぐ / hold hands → holding_hands\n"
        "  Include ALL action-derived tags — these define the character's pose.\n\n"
        "STEP 3 — Appearance: Extract character visual traits from the story "
        "(hair color/style, eye color, clothing) as Danbooru tags.\n\n"
        "EXPLICIT TAG RULE: NEVER use literary, poetic, or euphemistic expressions for actions "
        "or body parts. Always use the actual Danbooru tag. "
        "WRONG: 'tender_contact', 'intimate_gesture', 'passionate_closeness'. "
        "CORRECT: 'hand_on_another's_cheek', 'gripping', 'breast_grab', 'thigh_grab', "
        "'hair_grab', 'wrist_grab', 'lap_pillow', 'nape', 'collarbone' — use the real tag."
    )

    return (
        "You are an expert image generation prompt engineer.\n"
        "Analyze the reference image(s) and the metadata below, then craft a tag list.\n"
        "PRIMARY SOURCE: Derive visual content (art style, colors, setting) from "
        "[Reference metadata]. However, [User instruction] describes the story and "
        "characters — character identity, appearance, and actions in the instruction "
        "TAKE PRIORITY and MUST be reflected in the tags.\n\n"
        "UNIFIED COMPOSITION MANDATE: Your output is a prompt for ONE SINGLE IMAGE. "
        "Regardless of how many reference images are provided, you must synthesize them "
        "into a single coherent scene — not a collage, not a diptych, not a reference sheet. "
        "If references conflict, let influence weights guide which elements take priority.\n\n"
        f"[Style directive]\n{_NATURAL_TAGS_INSTRUCTION}\n\n"
        f"[Reference metadata]\n{context}\n\n"
        f"{character_mandate}\n\n"
        f"[User instruction]\n{instr_block}"
        f"{neg_instr}"
    )


def _build_natural_prose_prompt(
    context: str,
    instruction: str,
    tags_text: str,
    instruction_framing: bool = False,
    retry: bool = False,
) -> str:
    instr_block = _format_instruction_block(instruction, instruction_framing)
    style_instr = (_NATURAL_PROSE_RETRY_PREFIX if retry else "") + _NATURAL_PROSE_INSTRUCTION

    return (
        "You are an expert image generation prompt engineer.\n"
        "Analyze the reference image(s) and the metadata below.\n\n"
        "UNIFIED COMPOSITION MANDATE: Describe ONE SINGLE IMAGE. Regardless of how many "
        "reference images are provided, synthesize them into a single coherent scene — "
        "not a collage, not a diptych, not a reference sheet.\n\n"
        f"[Style directive]\n{style_instr}\n\n"
        f"[Reference metadata]\n{context}\n\n"
        f"[Tags already extracted for this scene]\n{tags_text}\n\n"
        f"[User instruction]\n{instr_block}"
    )


def _build_natural_visual_script_prompt(
    context: str,
    instruction: str,
    tags_text: str,
    instruction_framing: bool = False,
    prose_paragraphs: int = DEFAULT_PROSE_PARAGRAPHS,
) -> str:
    instr_block = _format_instruction_block(instruction, instruction_framing)
    style_instr = _natural_visual_script_instruction(prose_paragraphs)

    story_mandate = (
        "[Story → Image Mandate]\n"
        "The [User instruction] describes a story. "
        "The characters in that story are the PRIMARY SUBJECTS of this image. "
        "Translate their concrete physical actions (gripping, touching, running, kneeling, etc.) "
        "directly into embedded danbooru action tags in the ACTION focus. "
        "Do NOT lose or generalize the story's specific physical interactions."
    )

    return (
        "You are an expert image analyst and creative director.\n"
        "Analyze the reference image(s) and the metadata below.\n\n"
        "UNIFIED COMPOSITION MANDATE: Describe ONE SINGLE IMAGE. Regardless of how many "
        "reference images are provided, synthesize them into a single coherent scene — "
        "not a collage, not a diptych, not a reference sheet.\n\n"
        f"[Style directive]\n{style_instr}\n\n"
        f"[Reference metadata]\n{context}\n\n"
        f"[Tags already extracted for this scene — use as danbooru vocabulary anchor]\n{tags_text}\n\n"
        f"{story_mandate}\n\n"
        f"[User instruction]\n{instr_block}"
    )


# ── Instruction processing ─────────────────────────────────────────────────────

_TRANSLATE_PROMPT = (
    "Translate the following image generation instruction to English.\n"
    "If already in English, return it unchanged.\n"
    "Return ONLY the translated text, no explanation, no quotes.\n\n"
    "Instruction: {instruction}"
)

_TRANSLATE_AND_CLASSIFY_PROMPT = """\
You are a prompt engineering assistant.

1. Translate the instruction below to English (if already English, use as-is).
2. Classify each part into directive types.
3. Return ONLY a JSON object.

Directive types:
- "literal_text": text string to appear verbatim in the image
  (sign, watermark, caption, overlay, title, label — must NOT be paraphrased)
- "style_change": artistic or rendering style modification
- "concept_add": adding a visual element or atmosphere
- "concept_remove": removing an element
- "composition": framing, angle, or layout change

Instruction: "{instruction}"

Return JSON:
{{
  "instruction_en": "full translated instruction",
  "literals": [
    {{"type": "literal_text", "text": "...", "position": "top|bottom|center|left|right"}}
  ],
  "nl_instruction": "translated instruction with literals removed, for VLM"
}}"""

_LITERAL_TEXT_RE = re.compile(
    r"""
    # Command syntax (highest priority): text:"X" or /text X or text:X
    (?:^|(?<=[\s,]))
    (?:
      \/text\s+(?P<text_cmd1>[^\n,\"「『]+?)(?=\s*[,\n]|$)
      |
      text\s*:\s*['\"「](?P<text_cmd2>[^'\"」\n]+)['\"」]
      |
      text\s*:\s*(?P<text_cmd3>[^\n,\"「『]+?)(?=\s*[,\n]|$)
    )
    |
    # English: add/write/put/show ["text"] "X"  (keyword optional)
    (?:add|insert|put|place|show|write|display|include|render)\s+
    (?:(?:the\s+)?(?:text|word|words|label|watermark|title|string|letters?|caption)\s+)?
    ['\"「](?P<text>[^'\"」\n]+)['\"」]
    |
    # Japanese: quoted string + text-render verb (「X」という/と文字を入れて)
    ['\"「『](?P<text_ja>[^'\"」』\n]+)['\"」』]
    \s*(?:という|との|と|の)?
    \s*(?:文字|テキスト|タイトル|文章|ラベル|キャプション)?
    \s*を?\s*(?:に|で)?
    \s*(?:入れ|描画|追加|表示|書い|記載)
    |
    # Context: textboard/sign with "X" (テキストボードに「X」)
    (?:textboard|sign|banner|label|card|board|ボード|看板|テキスト)\s*
    (?:に|で|with|saying|reading)?\s*
    ['\"「『](?P<text_ctx>[^'\"」』\n]+)['\"」』]
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)


async def _translate_instruction(instruction: str, ollama, model: str) -> str:
    if not instruction.strip():
        return instruction
    prompt = _TRANSLATE_PROMPT.format(instruction=instruction)
    raw = await ollama.generate_text(
        prompt, model=model, options={"temperature": 0.1, "num_ctx": 2048}
    )
    return raw.strip()


async def _translate_and_classify(
    instruction: str, ollama, model: str
) -> tuple[str, str, list[dict]]:
    """Returns (instruction_en, nl_instruction, literals)."""
    prompt = _TRANSLATE_AND_CLASSIFY_PROMPT.format(instruction=instruction)
    raw = await ollama.generate_text(
        prompt, model=model, options={"temperature": 0.1, "num_ctx": 2048}
    )
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return instruction, instruction, []
    try:
        data = json.loads(m.group())
        instruction_en = data.get("instruction_en", instruction)
        nl_instr = data.get("nl_instruction", instruction_en)
        literals = data.get("literals", [])
        return instruction_en, nl_instr, literals
    except Exception:
        return instruction, instruction, []


def _extract_literal_texts(instruction: str) -> tuple[list[str], str]:
    """Extract text-render commands and strip them from the instruction.

    Returns (texts, cleaned_instruction) so VLM never sees text-render directives.
    texts: list of raw strings to render (e.g. ['Hello', 'Good!']).
    cleaned_instruction: instruction with all text-render commands removed.
    """
    texts: list[str] = []
    seen: set[str] = set()

    def _collect(m: re.Match) -> str:
        raw = (
            m.group("text_cmd1") or m.group("text_cmd2") or m.group("text_cmd3")
            or m.group("text") or m.group("text_ja") or m.group("text_ctx") or ""
        ).strip()
        if raw and raw.lower() not in seen:
            texts.append(raw)
            seen.add(raw.lower())
        return ""  # strip from instruction

    cleaned = _LITERAL_TEXT_RE.sub(_collect, instruction).strip().strip(",").strip()
    return texts, cleaned


def _append_literal_texts(positive: str, texts: list[str]) -> str:
    """Append literal text render tags to the end of the positive prompt.

    Uses the Anima-model format: text "Laugh!", text_on_image
    """
    if not texts:
        return positive
    tags = [f'text "{t}"' for t in texts] + ["text_on_image"]
    return positive.rstrip(", ") + ", " + ", ".join(tags)


_DETAILED_SECTION_HEADERS = (
    "Core Subject", "Characters", "Lighting",
    "Style", "Details", "Color Palette", "Camera", "Refinements",
)

# Matches a POSITIVE:/NEGATIVE: label line — used to stop parsing before these sections
_IS_POS_NEG_LABEL_RE = re.compile(
    r'^(?:positive(?:\s+prompt)?|negative(?:\s+prompt)?|avoid|do\s+not\s+include)'
    r'[:\s—–-]',
    re.IGNORECASE,
)

# Strips any trailing POSITIVE/NEGATIVE block from a bold-form section capture
_TRAILING_POS_NEG_RE = re.compile(
    r'\n*(?:positive(?:\s+prompt)?|negative(?:\s+prompt)?|avoid|do\s+not\s+include)'
    r'[:\s—–-].*$',
    re.IGNORECASE | re.DOTALL,
)


def _parse_detailed_output(text: str) -> str:
    """Parse 8-section output (bold, ATX, or plain headers) into a flat prompt string."""
    if not text.strip():
        return ""

    # Primary: bold form **Header:** content (raw VLM output)
    sections = re.findall(r"\*\*[^*]+\*\*[:\s]*(.*?)(?=\*\*|\Z)", text, re.S)
    if sections:
        cleaned = [_TRAILING_POS_NEG_RE.sub("", s).strip() for s in sections]
        return "\n".join(s for s in cleaned if s)

    # Robust fallback: line-by-line scan — no colon required in header line.
    # Fixes the case where sections 1-7 lack colons but section 8 (Refinements) has one:
    # the old regex required ":" in each header line, so only section 8 was extracted.
    header_pat = "|".join(re.escape(h) for h in _DETAILED_SECTION_HEADERS)
    is_header_re = re.compile(
        rf'^[#*>\-\s]{{0,10}}(?:{header_pat})\b',
        re.IGNORECASE
    )
    extract_inline_re = re.compile(
        rf'^[#*>\-\s]{{0,10}}(?:{header_pat})\b[^:\n]*:\s*(.*)',
        re.IGNORECASE
    )

    result: list[str] = []
    current: list[str] = []
    in_section = False

    for line in text.splitlines():
        stripped = line.strip()
        if _IS_POS_NEG_LABEL_RE.match(stripped):
            break  # stop before POSITIVE:/NEGATIVE: section
        if is_header_re.match(stripped):
            if in_section and current:
                result.append("\n".join(current).strip())
            current = []
            in_section = True
            m = extract_inline_re.match(stripped)
            if m:
                inline = m.group(1).strip()
                if inline:
                    current.append(inline)
        elif in_section and stripped:
            current.append(stripped)

    if in_section and current:
        result.append("\n".join(current).strip())

    return "\n".join(result) if result else ""


def _clean_markdown(text: str) -> str:
    """Strip markdown formatting and spurious label lines, but preserve negative sections."""
    # Remove markdown bold/italic (**, *, __, _)
    text = re.sub(r'\*{1,3}([^*\n]+)\*{1,3}', r'\1', text)
    text = re.sub(r'(?<!\w)_{1,2}([^_\n]+)_{1,2}(?!\w)', r'\1', text)
    # Remove ATX headers (## Title, ### Title)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove spurious positive-side label lines only
    text = re.sub(
        r'^(?:tags?|prose|prompt|positive(?:\s+prompt)?|part\s*\d+|block\s*\d+|'
        r'positive\s*prompt\s*generation|natural\s*language|description|output|result)'
        r'[:\s—–-]*\n?',
        '', text, flags=re.IGNORECASE | re.MULTILINE
    )
    # Remove separator lines like "--- ... ---"
    text = re.sub(r'^-{3,}.*-{3,}\s*$', '', text, flags=re.MULTILINE)
    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_stray_negative(text: str) -> str:
    """Remove any spontaneous negative section appended to a positive-only prompt."""
    return re.sub(
        r'\n*(?:negative(?:\s+prompt)?|avoid|do\s+not\s+include)[:\s—–-].*$',
        '', text, flags=re.IGNORECASE | re.DOTALL
    ).strip()


def _parse_positive_negative(text: str) -> tuple[str, str]:
    """Extract POSITIVE / NEGATIVE sections from model output when negative requested.

    Handles several output variants robustly:
    - POSITIVE:\\n...\\nNEGATIVE:\\n...  (canonical)
    - Missing POSITIVE: label — uses everything before NEGATIVE: as positive
    - Fallback patterns: "Negative prompt:", "Elements to avoid:", etc.
    """
    pos_match = re.search(r"POSITIVE:\s*(.*?)(?=\nNEGATIVE:|\Z)", text, re.S | re.I)
    neg_match = re.search(r"\nNEGATIVE:\s*(.*?)$", text, re.S | re.I)

    if pos_match:
        positive = pos_match.group(1).strip()
        negative = neg_match.group(1).strip() if neg_match else ""
        return positive, negative

    # POSITIVE: label absent but NEGATIVE: present — use everything before as positive
    if neg_match:
        positive = text[: neg_match.start()].strip()
        negative = neg_match.group(1).strip()
        return positive, negative

    # Fallback: looser negative-section patterns
    for pat in [
        r"\n(?:negative(?:\s+prompt)?)[:\s—–-]+(.+?)$",
        r"\n(?:elements?\s+to\s+avoid|avoid|do\s+not\s+include)[:\s—–-]+(.+?)$",
    ]:
        m = re.search(pat, text, re.S | re.I)
        if m:
            return text[: m.start()].strip(), m.group(1).strip()

    return text.strip(), ""


def _check_natural_prose(text: str) -> bool:
    """Return True if `text` is a genuine prose paragraph rather than a tag list.

    Detects prose by checking for enough long, non-tag-like sentences
    (minimum word count, low comma density, average word length > 4 chars).
    """
    words = text.strip().split()
    if len(words) < 40:
        return False
    commas = text.count(",")
    comma_density = commas / max(len(words), 1)
    avg_word_len = sum(len(w.strip(".,;:")) for w in words) / max(len(words), 1)
    return comma_density < 0.25 and avg_word_len > 4.0


def _remove_forced_tags(
    positive: str,
    removal_tags: set[str],
    *,
    all_lines: bool = False,
) -> tuple[str, list[str]]:
    """Remove specified tags from the positive prompt.

    With all_lines=False (default): processes only the first non-empty line —
    correct for natural/danbooru styles where prose follows the tag line.
    With all_lines=True: processes every line — required for detailed style
    where each section's content is a separate line.
    Returns (filtered_positive, list_of_removed_tags).
    """
    if not removal_tags:
        return positive, []
    removed: list[str] = []
    lines = positive.split('\n')
    for i, line in enumerate(lines):
        if line.strip():
            tags = [t.strip() for t in line.split(',')]
            filtered = []
            for t in tags:
                if t.lower().replace(' ', '_') in removal_tags:
                    removed.append(t)
                else:
                    filtered.append(t)
            lines[i] = ', '.join(filtered)
            if not all_lines:
                break
    return '\n'.join(lines), removed


# ── Streaming refine generator ─────────────────────────────────────────────────

async def _sample_mutation_tags(
    db,
    ollama,
    wd14_analysis: dict,
    divergence: float,
    *,
    max_tags: int = 12,
) -> list[str]:
    """Sample "related but absent" Danbooru tags for the Transmute divergence dial.

    Embeds the reference tag set and searches the wd14_vocab bank, then takes the
    mid-ranked band (close enough to stay coherent, far enough to mutate) excluding
    tags already present in the references. Returns [] on any failure or when the
    vocab bank is not imported.
    """
    import random

    from ..invoke.vocab_bank import _is_species_tag

    source_tags: set[str] = set(wd14_analysis.get("common_tags", []))
    for info in wd14_analysis.get("unique_by_image", {}).values():
        source_tags.update(info.get("must", []))
        source_tags.update(info.get("ref", []))
    if not source_tags:
        return []

    try:
        vec = await ollama.embed(" ".join(sorted(source_tags)[:80]))
        hits = await db.search_wd14_vocab(vec, min_freq=0.02, max_freq=0.6, limit=120)
    except Exception as exc:
        logger.debug("mutation tag sampling failed: %s", exc)
        return []

    pool = [
        h["name"] for h in hits
        if h["name"] not in source_tags and not _is_species_tag(h["name"])
    ]
    # Mid-ranked band: skip the nearest hits (they barely mutate anything)
    band = pool[30:80] if len(pool) > 40 else pool
    n = max(1, round(divergence * max_tags))
    return random.sample(band, min(n, len(band))) if band else []


@router.post("/pipeline")
async def trigger_pipeline(
    request: Request,
    body: PipelineRequest = PipelineRequest(),
):
    from ..jobs.runners import run_pipeline_tagging
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    sha256s = body.sha256s or None
    # CPU tagging stage first (TAGGING lane, never auto-paused); it chains the
    # embed stage onto the EMBEDDING lane when done.
    job_id = spooler.submit(
        JobLane.TAGGING,
        "ai_tagging",
        run_pipeline_tagging,
        db=db,
        ollama=ollama,
        sha256s=sha256s,
        spooler=spooler,
    )
    return {"status": "queued", "job_id": job_id}


@router.get("/pipeline/status")
async def get_pipeline_status(request: Request):
    """Return the current job state of the EMBEDDING lane (backwards-compatible endpoint)."""
    spooler = request.app.state.spooler
    embed_jobs = [
        j for j in spooler.snapshot()
        if j["lane"] == "embed" and j["state"] in ("running", "cancelling", "queued")
    ]
    if embed_jobs:
        j = embed_jobs[0]
        return {
            "running": j["state"] == "running",
            "job_id": j["id"],
            "progress": j["progress"],
            "progress_text": j["progress_text"],
            "elapsed": j["elapsed"],
            "eta_seconds": j["eta_seconds"],
        }
    return {"running": False}


@router.post("/pipeline/cancel")
async def cancel_pipeline(request: Request):
    spooler = request.app.state.spooler
    running = [
        j for j in spooler.snapshot()
        if j["lane"] == "embed" and j["state"] in ("running", "cancelling")
    ]
    if not running:
        return {"status": "not_running"}
    ok = await spooler.cancel(running[0]["id"])
    return {"status": "cancel_requested" if ok else "not_running"}


@router.post("/reset")
async def reset_ai_analysis(body: PipelineRequest, request: Request):
    if not body.sha256s:
        raise HTTPException(400, "sha256s required")
    db = request.app.state.db
    count = 0
    for sha256 in body.sha256s:
        doc = await db.get(sha256)
        if not doc:
            continue
        await db.set_payload(sha256, {"embedding_status": "pending", "wd14_tags": []})
        try:
            await db.delete_embedding(sha256)
        except Exception:
            pass
        count += 1
    return {"reset": count}


@router.post("/refine")
async def refine_prompt(body: RefineRequest, request: Request):
    """Submit a job to the PROMPT lane and return its job_id. Tokens are streamed via /refine/{job_id}/stream."""
    from ..jobs.runners import run_refine_prompt
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    comfy = request.app.state.comfy

    token_queue: asyncio.Queue = asyncio.Queue()
    job_id = spooler.submit(
        JobLane.PROMPT,
        "prompt_refine",
        run_refine_prompt,
        meta={"prompt_style": body.prompt_style, "sha256s": body.sha256s[:6]},
        body_dict=body.model_dump(),
        db=db,
        ollama=ollama,
        spooler=spooler,
        comfy=comfy,
        token_queue=token_queue,
    )
    request.app.state.refine_token_queues[job_id] = token_queue
    return {"job_id": job_id, "status": "queued"}


@router.get("/refine/{job_id}/stream")
async def refine_stream(job_id: str, request: Request):
    """Stream token output from a PROMPT lane job via SSE."""
    token_queue: asyncio.Queue | None = request.app.state.refine_token_queues.get(job_id)
    if token_queue is None:
        raise HTTPException(404, f"Refine job {job_id!r} not found")
    return queue_sse_response(
        request,
        token_queue,
        job_id=job_id,
        registry=request.app.state.refine_token_queues,
        encode="json",
    )


@router.post("/search")
async def semantic_search(body: SearchRequest, request: Request):
    db = request.app.state.db
    ollama = request.app.state.ollama

    vector_count = await db.count_with_embedding()
    if vector_count == 0:
        return {
            "query": body.query,
            "results": [],
            "message": "AI pipeline has not been run yet. Please press the AI processing button.",
        }

    cfg = await get_runtime_config(db)
    limit = max(1, min(int(cfg.get("semantic_search_limit", 100)), 500))
    n_results = min(body.n_results, limit) if body.n_results else limit
    embedding = await ollama.embed(body.query, model=cfg["embed_model"])
    docs = await db.search_vector(embedding, n_results=n_results, tag=body.tag or None)

    if body.sort != "relevance":
        docs = sort_docs(docs, body.sort)

    return {"query": body.query, "tag": body.tag, "sort": body.sort, "results": docs}


@router.post("/similar")
async def find_similar(body: SimilarRequest, request: Request):
    db = request.app.state.db
    n = max(1, min(body.n_results, 50))
    docs = await db.search_similar(body.sha256, n)
    if docs is None:
        raise HTTPException(404, "Image not found or has no embedding")
    return {"sha256": body.sha256, "results": docs}


@router.get("/graph/{sha256}")
async def get_similarity_graph(
    sha256: str,
    request: Request,
    neighbors: int = 6,
    depth: int = 2,
):
    db = request.app.state.db
    neighbors = max(2, min(neighbors, 10))
    depth = max(1, min(depth, 5))
    max_nodes = min(depth * 25, 150)
    graph = await db.build_similarity_graph(sha256, neighbors=neighbors, depth=depth, max_nodes=max_nodes)
    if not graph["nodes"]:
        raise HTTPException(404, "Image not found or has no embedding")
    return graph


class EmotionTagRequest(BaseModel):
    sha256s: list[str] = []  # empty = process all pending (no emotion_loneliness field)


@router.post("/emotion-tag")
async def trigger_emotion_tag(body: EmotionTagRequest, request: Request):
    """Queue emotion scoring job for specified images or all untagged images."""
    from ..jobs.runners import run_emotion_tag
    db = request.app.state.db
    ollama = request.app.state.ollama
    spooler = request.app.state.spooler
    job_id = spooler.submit(
        JobLane.EMBEDDING,
        "emotion_tag",
        run_emotion_tag,
        db=db,
        ollama=ollama,
        sha256s=body.sha256s or None,
    )
    return {"status": "queued", "job_id": job_id}


class EmotionSearchRequest(BaseModel):
    emotion: str          # one of the 12 EMOTION_DIMENSIONS names
    min_score: float = 0.5
    limit: int = 50


@router.post("/emotion-search")
async def emotion_search(body: EmotionSearchRequest, request: Request):
    """Return images scored at or above min_score on the given emotion dimension.

    Results are ordered highest score first.
    """
    from ..ai.emotion_tagger import EMOTION_DIMENSIONS
    from qdrant_client import models as qm
    from ..db.qdrant_client import IMAGES_COLLECTION

    if body.emotion not in EMOTION_DIMENSIONS:
        raise HTTPException(400, f"Unknown emotion '{body.emotion}'. Valid: {EMOTION_DIMENSIONS}")

    db = request.app.state.db
    field_key = f"emotion_{body.emotion}"
    limit = max(1, min(body.limit, 200))

    try:
        points, _ = await db._qc.scroll(
            collection_name=IMAGES_COLLECTION,
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(
                    key=field_key,
                    range=qm.Range(gte=body.min_score),
                ),
            ]),
            limit=limit,
            order_by=qm.OrderBy(key=field_key, direction=qm.Direction.DESC),
            with_payload=True,
            with_vectors=False,
        )
    except Exception as e:
        logger.warning("emotion_search failed: %s", e)
        raise HTTPException(500, "Emotion search failed — indexes may not be built yet")

    docs = [p.payload for p in points if p.payload]
    return {"emotion": body.emotion, "min_score": body.min_score, "results": docs}


@router.get("/status")
async def ai_status(request: Request):
    db = request.app.state.db
    ollama = request.app.state.ollama
    spooler = request.app.state.spooler
    vector_count = await db.count_with_embedding()
    embed_jobs = [
        j for j in spooler.snapshot()
        if j["lane"] == "embed" and j["state"] in ("running", "cancelling", "queued")
    ]
    pipeline_info = embed_jobs[0] if embed_jobs else {"running": False}
    return {
        "ollama_ok": await ollama.health(),
        "vector_count": vector_count,
        "vector_sync_needed": False,
        "pipeline": pipeline_info,
    }

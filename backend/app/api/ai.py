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
from ..runtime_config import get_runtime_config
from ..scanner.scanner import register_image
from ..spooler.models import JobLane
from .sort_utils import sort_docs

logger = logging.getLogger(__name__)
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


class SearchRequest(BaseModel):
    query: str
    n_results: int = 20
    tag: str = ""
    sort: str = "relevance"


class SimilarRequest(BaseModel):
    sha256: str
    n_results: int = 24


# ── Helpers ────────────────────────────────────────────────────────────────────

_WD14_MUST_INCLUDE_THRESHOLD = 0.70

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


_STYLE_INSTRUCTIONS = {
    "natural": (
        "Generate an image generation prompt for FLUX / Anima models.\n"
        "SINGLE-IMAGE SYNTHESIS RULE: When multiple reference images are provided, "
        "you MUST synthesize and merge their elements into ONE unified, coherent scene. "
        "Do NOT describe separate panels, collage layouts, side-by-side views, diptychs, "
        "or reference sheets. Choose one primary subject and setting, then blend the most "
        "compelling visual elements from all references into that single composition. "
        "Avoid any tags or phrases implying multiple separate images (e.g. 'multiple_views', "
        "'split_image', 'comparison', 'collage').\n\n"
        "You MUST output EXACTLY two blocks separated by one blank line — no headers, no labels:\n\n"
        "BLOCK 1 (tags): 40–60 comma-separated Danbooru-style tags on a single line.\n"
        "BLOCK 2 (prose): One paragraph of 80–120 words in natural English.\n\n"
        "Output format (STRUCTURE ONLY — derive actual content from the reference image, NOT from this template):\n"
        "[subject_count], [hair_color]_hair, [eye_color]_eyes, [clothing], [pose], [background], [lighting], [art_style], [quality_tags], ...\n\n"
        "[Prose paragraph: describe the subject's appearance, clothing, pose, background environment, "
        "lighting quality and color, and overall mood — all drawn from the reference image. 80–120 words.]\n\n"
        "--- YOUR ACTUAL OUTPUT STARTS HERE ---\n\n"
        "TAGS block: cover subject & count, hair, eyes, face/expression, body, clothing, "
        "accessories, footwear, pose, composition, background, lighting, atmosphere, art style, quality.\n\n"
        "PROSE block: describe subject appearance, clothing details and colors, pose and gesture, "
        "background environment, lighting (color temperature, direction, quality), mood, "
        "artistic style. Be specific — no vague words like 'beautiful' or 'nice'. "
        "Use precise terms (e.g. 'warm amber rim light' not 'nice lighting').\n\n"
        "Both blocks are REQUIRED. Incomplete output (tags only) is unacceptable.\n"
        "STRICT FORMATTING RULES — violating any of these is an error:\n"
        "- Do NOT use asterisks (*), pound signs (#), underscores for bold/italic, or any markdown.\n"
        "- Do NOT write labels like 'Tags:', 'Prompt:', 'Part 1:', 'Block 1:', 'Description:' etc.\n"
        "- Do NOT write any sentence before the tags (no intro, no preamble).\n"
        "- Start your response DIRECTLY with the first tag (e.g. '1girl, ...')."
    ),
    "danbooru": (
        "Generate an image generation prompt in Danbooru / Stable Diffusion tag style.\n"
        "SINGLE-IMAGE SYNTHESIS RULE: When multiple reference images are provided, "
        "synthesize their elements into ONE unified scene. Do NOT output tags that imply "
        "multiple panels or collage layouts. FORBIDDEN TAGS (never use): multiple_views, "
        "reference_sheet, character_sheet, split_image, collage, comparison, "
        "before_and_after, diptych, triptych, side-by-side, panel_layout. "
        "Instead, pick one dominant composition and weave the strongest visual elements "
        "from all references into that single image.\n\n"
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
        "Output EXACTLY these 8 sections with bold markdown headers:\n\n"
        "**Core Subject & Scene Setting:** [subject, genre, overall mood — 1-2 sentences]\n"
        "**Characters & Composition:** [count, hair color/style, eye color, pose, framing, clothing details]\n"
        "**Lighting & Atmosphere:** [light source, direction, color temperature, shadow quality, ambience]\n"
        "**Style & Artistic Influence:** [art style, medium, influences, rendering technique]\n"
        "**Details & Textures:** [skin, fabric, hair texture, surface materials, fine details]\n"
        "**Color Palette:** [dominant colors, accent colors, saturation, overall tone]\n"
        "**Camera & Lens Effects:** [shot type, angle, depth of field, bokeh, lens flare]\n"
        "**Refinements & Modifiers:** [comma-separated quality/detail keywords — e.g. masterpiece, volumetric lighting, hyperdetailed]\n\n"
        "Fill each section based SOLELY on the reference image. Be specific and concrete."
    ),
}

_NEGATIVE_INSTRUCTION = (
    "\n\nAlso generate a NEGATIVE PROMPT listing elements to avoid.\n"
    "You MUST use EXACTLY this output format — two labeled sections, nothing else:\n\n"
    "POSITIVE:\n"
    "[your positive prompt here — tags and prose based on the reference image]\n\n"
    "NEGATIVE:\n"
    "[comma-separated negative tags — elements to avoid, based on the reference context]\n\n"
    "RULES:\n"
    "- Start with the literal word POSITIVE: on its own line.\n"
    "- Then your positive prompt (tags + prose for natural style, tags only for danbooru).\n"
    "- Then a blank line, then NEGATIVE: on its own line.\n"
    "- Then the negative tags — comma-separated, no prose.\n"
    "- No other text, headers, or explanation."
)


def _build_vlm_prompt(
    context: str,
    instruction: str,
    prompt_style: str,
    with_negative: bool,
    instruction_framing: bool = False,
) -> str:
    style_instr = _STYLE_INSTRUCTIONS.get(prompt_style, _STYLE_INSTRUCTIONS["natural"])
    neg_instr = _NEGATIVE_INSTRUCTION if with_negative else (
        "\n\nOutput the positive prompt only — no labels, no explanation, "
        "and do NOT include a negative prompt or any 'Negative:' section."
    )

    if instruction:
        if instruction_framing:
            instr_block = (
                "[PROMPT ENGINEERING DIRECTIVE — NOT NARRATIVE CONTENT]\n"
                "Apply the following as a structural modification to the output prompt.\n"
                "DO NOT incorporate it as scene description. DO NOT ignore it.\n"
                "DO NOT turn text elements into character actions or props.\n\n"
                f"Directive: {instruction}"
            )
        else:
            instr_block = instruction
    else:
        instr_block = "Create a refined, high-quality image generation prompt."

    return (
        "You are an expert image generation prompt engineer.\n"
        "Analyze the reference image(s) and the metadata below, then craft a superior prompt.\n"
        "CRITICAL: Derive ALL content — tags, descriptions, mood, setting — "
        "SOLELY from the [Reference metadata] section. "
        "Format examples in [Style directive] are for OUTPUT STRUCTURE ONLY. "
        "Do NOT copy, echo, or draw thematic inspiration from them.\n\n"
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
    r"""(?:add|insert|put|place|show|write|display|include|render)\s+
        (?:the\s+)?(?:text|word|words|label|watermark|title|string|letters?|caption)\s+
        ['""“「]
        (?P<text>[^'"”」]+)
        ['""”」]
        (?:[\s,]*(?:at|on|in|to)\s+
           (?P<position>top|bottom|left|right|center|upper|lower|above|below)
        )?""",
    re.IGNORECASE | re.VERBOSE,
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


def _extract_literal_directives(instruction_en: str) -> tuple[str, list[dict]]:
    """Regex-extract literal text directives from English instruction.
    Returns (cleaned_instruction, literals)."""
    literals: list[dict] = []

    def _replace(m: re.Match) -> str:
        literals.append({
            "type": "literal_text",
            "text": m.group("text").strip(),
            "position": (m.group("position") or "top").lower(),
        })
        return ""

    cleaned = _LITERAL_TEXT_RE.sub(_replace, instruction_en).strip(" ,")
    return cleaned, literals


def _inject_literal_directives(positive: str, literals: list[dict]) -> str:
    """Prepend literal text directive tags to the generated positive prompt."""
    tags: list[str] = []
    for d in literals:
        tags.append(f'text "{d["text"]}"')
        pos = d.get("position", "top").lower()
        pos_tag = {
            "top": "top_text", "upper": "top_text", "above": "top_text",
            "bottom": "bottom_text", "lower": "bottom_text", "below": "bottom_text",
        }.get(pos, "overlay_text")
        tags.append(pos_tag)
        tags.append("text_on_image")
    return (", ".join(tags) + ", " + positive) if tags else positive


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
    """Extract POSITIVE / NEGATIVE sections from model output when negative requested."""
    pos_match = re.search(r"POSITIVE:\s*(.*?)(?=NEGATIVE:|$)", text, re.S | re.I)
    neg_match = re.search(r"NEGATIVE:\s*(.*?)$", text, re.S | re.I)
    positive = pos_match.group(1).strip() if pos_match else text.strip()
    negative = neg_match.group(1).strip() if neg_match else ""
    return positive, negative


def _check_natural_prose(text: str) -> bool:
    """Return True if the natural-style output contains a prose paragraph after the tags block.

    Detects prose by looking for a blank-line-separated second block that has at least
    one long non-tag sentence (average word length > 4 chars, comma density < 0.25).
    """
    blocks = [b.strip() for b in re.split(r"\n{2,}", text.strip()) if b.strip()]
    if len(blocks) < 2:
        return False
    # Check the last block for prose characteristics
    prose_candidate = blocks[-1]
    words = prose_candidate.split()
    if len(words) < 15:
        return False
    commas = prose_candidate.count(",")
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


# ── ComfyUI image save helper ─────────────────────────────────────────────────

async def _save_and_register_comfy_image(
    img_bytes: bytes,
    original_name: str,
    db,
) -> str | None:
    sha256 = hashlib.sha256(img_bytes).hexdigest()
    gen_dir = settings.generated_images_dir
    gen_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_name).suffix or ".png"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"comfy_{ts}_{sha256[:8]}{suffix}"
    path = gen_dir / filename

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_bytes, img_bytes)

    try:
        await register_image(path, db)
        return sha256
    except Exception as exc:
        logger.error("register_image failed: %s", exc)
        return None


# ── Streaming refine generator ─────────────────────────────────────────────────

def _resolve_weights(sha256s: list[str], raw_weights: list[float]) -> list[float]:
    n = len(sha256s)
    if n == 0:
        return []
    if not raw_weights or len(raw_weights) != n:
        return [1.0 / n] * n
    total = sum(raw_weights)
    if total <= 0:
        return [1.0 / n] * n
    return [w / total for w in raw_weights]




# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/pipeline")
async def trigger_pipeline(
    request: Request,
    body: PipelineRequest = PipelineRequest(),
):
    from ..jobs.runners import run_pipeline
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    sha256s = body.sha256s or None
    job_id = spooler.submit(
        JobLane.EMBEDDING,
        "ai_pipeline",
        run_pipeline,
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

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    await request.app.state.spooler.cancel(job_id)
                    break
                try:
                    item = await asyncio.wait_for(token_queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            request.app.state.refine_token_queues.pop(job_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
    embedding = await ollama.embed(body.query, model=cfg["embed_model"])
    docs = await db.search_vector(embedding, n_results=body.n_results, tag=body.tag or None)

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

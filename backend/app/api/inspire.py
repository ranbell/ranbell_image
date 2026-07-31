import asyncio
import json
import logging
import math
import random
import re
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..ai.tile_image import create_tile_image
from ..jobs.sse_stream import queue_sse_response
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
from ..ai import vecmath
from ..tags import catalog as tag_catalog
from .inspire_axes import (
    AXIS_DEFINITIONS, ALL_AXES, AXIS_ALIAS_MAP,
    STEP1_AXIS_TABLE, STEP2_INVERSION_HINTS,
    normalize_axis, resolve_axes,
)

logger = logging.getLogger(__name__)


def _load_wd14_character_tags() -> frozenset[str]:
    """Return category=4 (character name) tags from selected_tags.csv."""
    try:
        import pandas as pd
        from ..config import settings
        csv_path = Path(settings.wd14_model_dir) / "selected_tags.csv"
        if not csv_path.exists():
            return frozenset()
        df = pd.read_csv(csv_path)
        return frozenset(df[df["category"] == 4]["name"].str.lower())
    except Exception:
        return frozenset()

_WD14_CHAR_TAGS: frozenset[str] = _load_wd14_character_tags()

# ── Tag category data (shared catalog — tag_categories.json) ───────────────────
_TAG_DATA: dict = tag_catalog.TAG_DATA
_FTC_COUNT = tag_catalog.COUNT
_FTC_EYE_SHAPES = tag_catalog.EYE_SHAPES
_FTC_BODY = tag_catalog.BODY
_FTC_SKIN_FACE = tag_catalog.SKIN_FACE
_FTC_RACE = tag_catalog.RACE
_FTC_COMPOSITION = tag_catalog.COMPOSITION
_FTC_PROPS = tag_catalog.PROPS
_FTC_HAIR_STYLES = tag_catalog.HAIR_STYLES
_FTC_EXPRESSION = tag_catalog.EXPRESSION
_FTC_POSE = tag_catalog.POSE
_FTC_CLOTHING_EXPLICIT = tag_catalog.CLOTHING_EXPLICIT
_FTC_ACCESSORIES = tag_catalog.ACCESSORIES
_FTC_BODY_PARTS = tag_catalog.BODY_PARTS
_FTC_ART_STYLE = tag_catalog.ART_STYLE
_FTC_ENVIRONMENT = tag_catalog.ENVIRONMENT
_FTC_BACKGROUND = tag_catalog.BACKGROUND
_FTC_CLOTHING_SUFFIXES = tag_catalog.CLOTHING_SUFFIXES
_FTC_ACTION_KEYWORDS = tag_catalog.ACTION_KEYWORDS
_STYLE_ALWAYS_FIXED = tag_catalog.STYLE_ALWAYS_FIXED
_VISUAL_LIGHTING = tag_catalog.VISUAL_LIGHTING
_ABSTRACT_BG = tag_catalog.ABSTRACT_BG

_TAG_DISPLAY_GROUP: dict[str, str] = tag_catalog.build_display_group_map()

router = APIRouter(prefix="/api/inspire")


# ── Pydantic models ────────────────────────────────────────────────────────────

class SerendipityRequest(BaseModel):
    sha256s: list[str]
    n_results: int = 12
    score_min: float = 0.40
    score_max: float = 0.65


class ArithmeticRequest(BaseModel):
    add_sha256s: list[str]
    sub_sha256s: list[str] = []
    n_results: int = 12


class MorphRequest(BaseModel):
    sha256_a: str
    sha256_b: str
    steps: int = 3


class AnomalyRequest(BaseModel):
    sha256s: list[str]
    n_results: int = 12


class InversionRequest(BaseModel):
    sha256s: list[str]
    n_results: int = 12
    change_targets: list[str] = []   # formerly: axes
    user_inject_prompt: str = ""
    user_inject_sections: dict[str, str] = {}  # {character, background, props, action}
    custom_blacklist: list[str] = []
    lang: str = "en"                 # "ja" or "en" — story generation language
    inversion_strength: float = 1.0  # 0.1–1.0
    skip_verifier: bool = True       # skip Step 2b inversion tag verifier for speed


class ExpandThemeRequest(BaseModel):
    theme: str
    sha256s: list[str] = []
    lang: str = "ja"


class BrainstormRequest(BaseModel):
    sha256s: list[str]
    extra_tags: list[str] = []
    lang: str = "ja"
    # Supply the tag set directly instead of harvesting it from library docs.
    # Muse uses this: its board images are tagged at a lower threshold than the
    # library pipeline uses, and that merged set is what it wants ideas about.
    reference_tags: list[str] | None = None


class DiscoverRequest(BaseModel):
    target_sha256: str
    context_pairs: list[tuple[str, str]]  # [(positive_sha256, negative_sha256), ...]
    n_results: int = 20


class GroupedSearchRequest(BaseModel):
    query: str
    group_by: str = "model_name"  # payload field to group by
    group_size: int = 3
    limit: int = 10


class TextSearchRequest(BaseModel):
    query: str
    n_results: int = 12


class BlendSlot(BaseModel):
    sha256: str
    weight: float  # -1.0 to +1.0 (0 = ignore)


class BlendRequest(BaseModel):
    slots: list[BlendSlot]
    n_results: int = 12


class OutlierRequest(BaseModel):
    sha256s: list[str] = []
    n_results: int = 12
    mode: str = "antipode"  # "antipode" | "isolated"


# ── Vector math helpers ────────────────────────────────────────────────────────
# Now shared with Muse; kept aliased here so the handlers below read unchanged.
_normalize = vecmath.normalize
_vec_add = vecmath.vec_add
_vec_sub = vecmath.vec_sub
_vec_lerp = vecmath.vec_lerp


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _require_embedding(sha256: str, db) -> list[float]:
    vec = await db.get_embedding(sha256)
    if not vec:
        raise HTTPException(
            422,
            f"Image {sha256[:8]}… has no embedding vector. Run the AI pipeline first.",
        )
    return vec


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/axes")
async def get_axes():
    """Return axis definitions to the frontend for dynamic axis list rendering."""
    return {
        "axes": AXIS_DEFINITIONS,
        "all": ALL_AXES,
    }


@router.post("/serendipity")
async def serendipity(body: SerendipityRequest, request: Request):
    """Find images in the 'interesting but not too similar' mid-similarity zone."""
    db = request.app.state.db
    if not body.sha256s:
        raise HTTPException(400, "Select at least one image")

    vecs = [await _require_embedding(s, db) for s in body.sha256s[:6]]
    n = len(vecs)
    dim = len(vecs[0])
    query_vec = _normalize([sum(v[i] for v in vecs) / n for i in range(dim)])

    # Pre-exclude images too similar to any individual reference (catches near-neighbors missed by the averaged vector)
    _REF_SIM_THRESHOLD = 0.80
    exclude_sha256s: set[str] = set(body.sha256s)
    for ref_vec in vecs:
        near = await db.search_by_vector_scored(ref_vec, n_results=100)
        for payload, score in near:
            if score >= _REF_SIM_THRESHOLD:
                exclude_sha256s.add(payload["sha256"])

    # Fetch a large candidate pool (sorted by similarity desc)
    scored = await db.search_by_vector_scored(
        query_vec, n_results=1000, exclude_sha256s=list(exclude_sha256s), exclude_reference=True
    )
    if not scored:
        return {"results": [], "count": 0}

    # Dynamic percentile-based score range (ignores hardcoded defaults)
    all_scores = sorted(s for _, s in scored)
    p25 = all_scores[len(all_scores) // 4]
    p75 = all_scores[len(all_scores) * 3 // 4]
    score_min = p25
    score_max = p75
    in_range = [(p, s) for p, s in scored if score_min <= s <= score_max]

    # Fallback: when the score range yields nothing (e.g. the embedding space is
    # clustered so all results are either very similar or very dissimilar), use a
    # positional approach — skip the top-25% most similar and sample from there.
    if not in_range and len(scored) > body.n_results:
        skip = max(1, len(scored) // 4)      # skip top 25% (most similar)
        pool = scored[skip:]
        in_range = pool

    in_range = _dedup_scored(in_range, threshold=0.70)
    # Final check that no reference image sha256 slipped through (supplements HasIdCondition gaps)
    ref_sha_set = set(body.sha256s)
    in_range = [(p, s) for p, s in in_range if p.get("sha256") not in ref_sha_set]
    # Random sample from a 20-item buffer → return the first n_results
    _SAMPLE_BUFFER = 20
    if len(in_range) > _SAMPLE_BUFFER:
        in_range = random.sample(in_range, _SAMPLE_BUFFER)
    results = [{**p, "_score": round(s, 3)} for p, s in in_range[:body.n_results]]
    return {"results": results, "count": len(results)}


@router.post("/arithmetic")
async def arithmetic(body: ArithmeticRequest, request: Request):
    """Vector algebra: sum(add_vecs) - sum(sub_vecs), then nearest-neighbour search."""
    db = request.app.state.db
    if not body.add_sha256s:
        raise HTTPException(400, "At least one positive image is required")

    add_vecs = [await _require_embedding(s, db) for s in body.add_sha256s[:3]]
    result_vec = _normalize(add_vecs[0])
    for v in add_vecs[1:]:
        result_vec = _normalize(_vec_add(result_vec, _normalize(v)))

    for sha256 in body.sub_sha256s[:3]:
        result_vec = _normalize(_vec_sub(result_vec, _normalize(await _require_embedding(sha256, db))))

    result_vec = _normalize(result_vec)
    exclude = list(body.add_sha256s) + list(body.sub_sha256s)
    docs = await db.search_by_vector(result_vec, n_results=body.n_results * 2, exclude_sha256s=exclude, exclude_reference=True)
    docs = _dedup_by_tags(docs)[:body.n_results]
    return {"results": docs, "count": len(docs)}


@router.post("/morph")
async def morph(body: MorphRequest, request: Request):
    """Concept morphing: linear interpolation timeline between two image vectors."""
    db = request.app.state.db
    vec_a = await _require_embedding(body.sha256_a, db)
    vec_b = await _require_embedding(body.sha256_b, db)

    steps = max(2, min(body.steps, 5))
    ts = [i / (steps + 1) for i in range(1, steps + 1)]

    timeline = []
    for t in ts:
        lerp_vec = _normalize(_vec_lerp(vec_a, vec_b, t))
        docs = await db.search_by_vector(
            lerp_vec, n_results=4,
            exclude_sha256s=[body.sha256_a, body.sha256_b],
            exclude_reference=True,
        )
        timeline.append({"t": round(t, 2), "results": docs})

    return {"sha256_a": body.sha256_a, "sha256_b": body.sha256_b, "timeline": timeline}


@router.post("/anomaly")
async def anomaly(body: AnomalyRequest, request: Request):
    """Inject statistically rare tags (via Ollama) and re-search."""
    db = request.app.state.db
    ollama = request.app.state.ollama
    if not body.sha256s:
        raise HTTPException(400, "Select an image")

    all_tags: list[str] = []
    for sha256 in body.sha256s[:6]:
        doc = await db.get(sha256)
        if doc and doc.get("wd14_tags"):
            all_tags.extend(doc["wd14_tags"][:30])

    if not all_tags:
        raise HTTPException(
            422,
            "Select images with WD14 tags (AI pipeline may not have run)",
        )

    from collections import Counter
    tag_counter = Counter(all_tags)
    tag_list = ", ".join(tag for tag, _ in tag_counter.most_common(40))
    prompt = (
        f"These are WD14 anime image tags: {tag_list}\n\n"
        "List exactly 3 WD14 anime-style tags that would be conceptually surprising or "
        "rarely seen together with these tags — creating a strong thematic contrast or clash.\n"
        "Respond with ONLY the 3 tags as a comma-separated English list. No explanation."
    )

    cfg = await get_runtime_config(db)
    try:
        anomaly_text = await ollama.generate_text(prompt, model=cfg["vlm_model"])
    except Exception as e:
        raise HTTPException(500, f"Ollama error: {e}")

    raw_tags = [t.strip().lower().replace(" ", "_") for t in anomaly_text.split(",") if t.strip()]
    anomaly_tags = [t for t in raw_tags if t][:3]
    if not anomaly_tags:
        raise HTTPException(500, "Ollama did not return valid tags")

    combined_text = ", ".join(list(dict.fromkeys(all_tags[:40] + anomaly_tags)))
    try:
        query_vec = await ollama.embed(combined_text, model=cfg["embed_model"])
    except Exception as e:
        raise HTTPException(500, f"Embedding error: {e}")

    docs = await db.search_by_vector(
        query_vec, n_results=body.n_results * 2, exclude_sha256s=body.sha256s, exclude_reference=True
    )
    docs = _dedup_by_tags(docs)[:body.n_results]
    return {
        "results": docs,
        "count": len(docs),
        "anomaly_tags": anomaly_tags,
        "base_tags": list(dict.fromkeys(all_tags))[:20],
    }


# ── Inversion ──────────────────────────────────────────────────────────────────
# Axis definitions are in inspire_axes.py (imported at top of file).
# AXIS_DEFINITIONS, ALL_AXES, AXIS_ALIAS_MAP, STEP1_AXIS_TABLE, STEP2_INVERSION_HINTS
# normalize_axis(), resolve_axes()

# Compatibility aliases for code that still references old names
INVERSION_AXIS_DEFINITIONS = {k: v["desc"] for k, v in AXIS_DEFINITIONS.items()}
_ALL_AXES = ALL_AXES

_STEP1_CLASSIFY_PROMPT = """\
# AXIS DEFINITIONS
{axis_table}

# TASK
Classify each tag in [UNKNOWN TAGS] into exactly one axis from the table above.
Use "fixed" if the tag does not fit any axis (body features, props, composition, etc.).

# UNKNOWN TAGS TO CLASSIFY
{unknown_tags}

# OUTPUT (JSON dict only — classify every tag, no omissions)
{{"tag1": "axis_name", "tag2": "axis_name", ...}}
Valid values: visual, time_weather, emotion, clothing, hair, style, location, narrative, action, parts, fixed"""

_STEP2_PROMPT = """\
# TASK
You receive ALL SOURCE TAGS from the image, grouped by axis as a JSON dict.
For EACH axis in the JSON, generate dramatically contrasting tags that create an OPPOSITE WORLD.

# ALL SOURCE TAGS (invert every axis for full-context world-building)
{all_axis_json}
Note: Axes showing [] have no detected source tags — examine the provided image to infer their current state, then generate inverted tags for those axes.

# HOW TO INVERT — think "opposite world, not just different"
{inversion_hints}
  Rule: if original feels bright/cute/peaceful → new world must feel dark/fierce/chaotic, and vice versa.

# REQUIREMENTS
1. Output ALL of these axis keys (copy them exactly): {expected_axes}
   DO NOT add any axis key not listed above — extra keys are silently discarded.
2. Generate ~{n_per_axis} candidate tags per axis — more is better. Do not leave any axis empty.
3. NEVER repeat any tag from the input.
{character_attr_rule}
5. BANNED always: holographic, scifi, futuristic.
{direction_hint}
{color_instruction}

# NEUTRALIZER_TAGS
Pick the 1-2 MOST DEFINING source tags (from any axis) for negative prompt.

# OUTPUT (JSON only — use ONLY the axis keys listed in REQUIREMENTS #1 above)
{{
  "new_tags_by_axis": {{
    "<axis_1>": ["inverted_tag_a", "inverted_tag_b", "inverted_tag_c"],
    "<axis_2>": ["inverted_tag_x", "inverted_tag_y"]
  }},
  "neutralizer_tags": ["most_defining_source_tag"]
}}"""

_STEP3_PROMPT = """\
# ROLE
You are an art director writing a visual brief for an illustrator.
Embed danbooru vocabulary inline within each description using parentheses.
Every concrete element must be named in BOTH human language AND danbooru format.

# MANDATE — embed danbooru tags in parentheses within the prose
WRONG: "She wears casual clothes in the park."
RIGHT: "She wears an (oversized_hoodie) in cream, (cuffed_track_pants) in olive, (low-top_sneakers). \
The setting is a (park, outdoor) with a (wooden_bench, old_bench) under a mature (zelkova_tree), \
(late-afternoon) (direct_sunlight) casting (shadow_bars) across the (grass)."

Every noun, texture, color, action, and mood word must have a danbooru equivalent in parentheses.

# INPUTS
FIXED CHARACTER ATTRIBUTES: {fixed_tags}
INVERTED WORLD TAGS (selected): {new_tags}
INVERTED WORLD TAGS (full candidate pool — use for inspiration): {raw_tags_summary}
USER CREATIVE DIRECTION (highest priority — anchor every category to this): {user_inject_prompt}

# CHARACTER COUNT RULE
{char_count_rule}

# OUTPUT (JSON only — all *_desc values are English prose WITH danbooru terms in parentheses)
{{
  "hair_desc": "prose: length/cut/color + (danbooru_hair_tag, ornament_tag, ...) embedded",
  "face_desc": "prose: expression details + (expression_tag, eye_direction_tag, ...) embedded",
  "clothing_desc": "prose: each garment item + (garment_tag, color_tag, material_tag) embedded",
  "accessory_desc": "prose: jewelry/bag/props + (accessory_tag, placement) embedded",
  "pose_desc": "prose: body position/hands + (pose_tag, gesture_tag, body_language_tag) embedded",
  "scene_desc": "prose: location/structures + (location_tag, environment_tag, distance_tag) embedded",
  "object_desc": "prose: objects with states + (object_tag, state_tag) embedded",
  "lighting_desc": "prose: light source/quality + (light_source_tag, direction_tag, shadow_tag) embedded",
  "atmosphere_desc": "prose: weather/time/mood + (weather_tag, time_tag, season_tag, mood_tag) embedded"
}}"""

_STEP4_PROMPT = """\
# ROLE
You are a creative director writing a VISUAL SCRIPT — a scene narrative where every concrete
element is simultaneously named in danbooru vocabulary within parentheses.
This script serves as both a story for humans AND a precise drawing instruction for AI.

# FORMAT RULE — embed danbooru tags inline in parentheses throughout the narrative
Every noun, pose, expression, clothing item, and visual element MUST have its danbooru tag(s) in parentheses immediately following.
Example EN: "Kanon (brown_hair, furrowed_brow) leans forward (leaning_forward) on the weathered bench \
(bench, park, outdoor), speaking urgently (talking_to_others, nervous_smile)."
Example JA: \
「花音(brown_hair, furrowed_brow)は古いベンチ(bench, park, outdoor)に腰を下ろし、\
前に体を傾けながら(leaning_forward)、必死に話しかける(talking_to_others, nervous_smile)。」\
Note: use ASCII parentheses ( ) for tags even when writing in Japanese.

# VISUAL SPECIFICATION (art director's brief — use these as the source of all danbooru terms)
{visual_spec_nl}

# FIXED TAGS (prepend ALL verbatim in final_positive_tags)
{fixed_tags}

# NEUTRALIZER TAGS
{neutralizer_tags}

# CHARACTER COUNT RULE
{char_count_rule}

# NARRATIVE STRUCTURE — write in {lang_label} with embedded danbooru tags:
[SCENE SETUP — (wide_shot) or (establishing_shot)]
  Describe the environment. Embed: location, lighting, time, weather, composition tags.

[CHARACTER INTRODUCTION — (medium_shot) or (cowboy_shot)]
  Each character with appearance. Embed: hair, clothing, accessory, initial pose/expression tags.

[ACTION & INTERACTION — choose: (medium_shot), (wide_shot), or (full_body) to match the movement]
  The key story moment. Embed: action, gesture, interaction, emotion tags.

[EMOTIONAL PEAK — choose the most narratively fitting: (close_up), (medium_shot), (wide_shot), or (full_body)]
  Most charged moment. Match composition to the action: wide for movement/environment, close for intense expression. Embed: expression, pose, composition tags.

[RESOLUTION — (wide_shot) or (group_shot)]
  Return to full scene. Embed: atmosphere, final composition, mood tags.

# RULES
1. Every sentence MUST embed at least 2 danbooru tags in parentheses
2. BANNED phrases: "somehow", "a sense of", "filled with emotion", "indescribable", "the air was filled with"
3. {char_count_rule}
4. Language: {lang_label} — natural fluent prose, NOT stiff translated English
5. NEVER add quality meta-tags: masterpiece, best_quality, highres

Write the Visual Script now. Then output ONLY this JSON code block (nothing after):

```json
{{
  "final_positive_tags": "(fixed_tags first), (every danbooru term embedded in the script above, deduplicated)",
  "final_positive_nl": "3-sentence ENGLISH SD prompt summary (MUST be in English regardless of story language above): lighting + character description + emotional tone",
  "final_negative": "{neutralizer_placeholder}, worst_quality, low_quality, bad_anatomy, extra_limbs"
}}
```"""

_SAFETY_PROMPT = """\
# TASK
Check if the tag list contains tags clearly indicating criminal acts or depictions
(violence, injury, harm to minors, etc.).
Explicit content is out of scope -- do NOT flag it.
If criminal issues found, replace those tags with safe alternatives.

# INPUT
[TAGS]: {tags}

# OUTPUT (JSON only)
{{
  "safe": true,
  "issues": [],
  "cleaned_tags": "(same as INPUT if no changes needed)"
}}"""

_EXPAND_THEME_PROMPT = """\
# ROLE
You are a Danbooru tag expert. Given a theme/topic, generate specific danbooru-compatible tags
for each of six categories. Think creatively and artistically — avoid obvious/generic tags.
Prefer visually striking, specific, non-obvious choices that create a vivid scene.

# THEME
{theme}

# RULES
- All tags must be real Danbooru tags (underscore_format, e.g. long_hair, coffee_cup)
- CHARACTER: hair color, eye color, hair style, clothing items (5-10 tags)
- BACKGROUND: location, time of day, weather, architectural/natural elements (5-10 tags)
- PROPS & ACCESSORIES: held objects, worn accessories, jewelry, nearby props (4-8 tags)
- ACTION: pose, gesture, facial expression, body language (3-6 tags)
- MOOD: lighting style, color palette, emotional atmosphere (e.g. soft_lighting, warm_color_palette, melancholic, dramatic_lighting) (3-6 tags)
- CAMERA: shot framing and angle (e.g. close-up, wide_shot, from_above, dutch_angle, full_body) (2-4 tags)
- Do NOT include quality meta-tags (masterpiece, best_quality, highres, etc.)
- Do NOT repeat tags across sections

# OUTPUT (JSON only)
{{
  "character": "tag1, tag2, ...",
  "background": "tag1, tag2, ...",
  "props": "tag1, tag2, ...",
  "action": "tag1, tag2, ...",
  "mood": "tag1, tag2, ...",
  "camera": "tag1, tag2, ..."
}}"""


def _char_count_rule(fixed_tags: list[str]) -> str:
    for t in fixed_tags:
        if t in ("1girl", "solo"):
            return "1girl/solo → use she/the girl, NEVER they/girls/plural"
        if t == "2girls":
            return "2girls → exactly two girls, use they/the two girls"
        if t in ("multiple_girls", "3girls", "4girls"):
            return "multiple girls → use they/the girls"
        if t == "1boy":
            return "1boy → use he/the boy, NEVER they"
        if t == "2boys":
            return "2boys → exactly two boys"
    return "match the character count tag in fixed_tags exactly"


def _format_visual_spec_nl(world_spec: dict) -> str:
    """Convert *_desc fields from world_spec into a labeled brief for STEP4."""
    labels = {
        "hair_desc": "HAIR",
        "face_desc": "FACE & EXPRESSION",
        "clothing_desc": "CLOTHING",
        "accessory_desc": "ACCESSORIES",
        "pose_desc": "POSE & GESTURE",
        "scene_desc": "SCENE",
        "object_desc": "OBJECTS",
        "lighting_desc": "LIGHTING",
        "atmosphere_desc": "ATMOSPHERE",
    }
    return "\n".join(
        f"[{labels[k]}] {world_spec[k].strip()}"
        for k in labels
        if world_spec.get(k, "").strip()
    )


async def _step3_world_builder(
    fixed_tags: list[str],
    new_tags: list[str],
    raw_by_axis: dict[str, list[str]],
    user_inject_prompt: str,
    ollama,
    model: str,
    options: dict | None = None,
    tile_bytes: bytes | None = None,
    user_inject_sections: dict[str, str] | None = None,
) -> dict:
    """Non-streaming: expand abstract tags into a concrete per-category visual specification.
    When tile_bytes is provided, uses VLM so the model can see all source images directly.
    """
    try:
        raw_tags_summary = "\n".join(
            f"{ax}: {', '.join(tags[:10])}"
            for ax, tags in (raw_by_axis or {}).items()
            if tags
        )
        _sections = user_inject_sections or {}
        _has_sections = any(_sections.get(k, "").strip() for k in ("character", "background", "props", "action"))
        if _has_sections:
            _char  = _sections.get("character",  "").strip() or "(none)"
            _bg    = _sections.get("background", "").strip() or "(none)"
            _props = _sections.get("props",      "").strip() or "(none)"
            _act   = _sections.get("action",     "").strip() or "(none)"
            _safe_inject = (
                "USER CREATIVE DIRECTION — per-category (highest priority):\n"
                f"• CHARACTER / HAIR / EYES / CLOTHING: {_char}\n"
                f"• BACKGROUND / SCENE / ENVIRONMENT: {_bg}\n"
                f"• PROPS & ACCESSORIES (jewelry, bag, held objects): {_props}\n"
                f"• ACTION / POSE / GESTURE: {_act}\n\n"
                "MANDATE: For every section with user input, generate ≥5 specific danbooru tags "
                "in parentheses. For empty sections, invent creative, artistically surprising tags. "
                "Prioritize unexpected, visually striking choices over generic ones."
            ).replace("{", "{{").replace("}", "}}")
        else:
            _safe_inject = (
                (user_inject_prompt or "(none)") + "\n\n"
                "MANDATE: Even without user direction, generate ≥5 specific danbooru tags in "
                "parentheses for EACH of: accessory_desc, scene_desc, object_desc. "
                "Prioritize artistic, non-obvious choices."
            ).replace("{", "{{").replace("}", "}}")
        prompt = _STEP3_PROMPT.format(
            fixed_tags=", ".join(fixed_tags) or "(none)",
            new_tags=", ".join(new_tags) or "(none)",
            raw_tags_summary=raw_tags_summary or "(none)",
            user_inject_prompt=_safe_inject,
            char_count_rule=_char_count_rule(fixed_tags),
        )
        if tile_bytes:
            raw = await ollama.generate_vlm(prompt, [tile_bytes], model=model, options=options)
        else:
            raw = await ollama.generate_text(prompt, model=model, options=options)
        return _parse_json_from_llm(raw) or {}
    except Exception as exc:
        logger.warning("_step3_world_builder exception: %s", exc, exc_info=True)
        return {}


def _extract_embedded_tags(text: str) -> list[str]:
    """Extract danbooru tags from all (parenthesized groups) in a Visual Script text."""
    tags: list[str] = []
    seen: set[str] = set()
    for group in re.findall(r'\(([^)]+)\)', text):
        for t in _split_tags(group):
            if t not in seen:
                tags.append(t)
                seen.add(t)
    return tags


def _extract_spec_category_tags(world_spec: dict) -> dict[str, list[str]]:
    """Per-category tag extraction from STEP3 danbooru-embedded *_desc fields."""
    cat_map = {
        "hair_tags":       "hair_desc",
        "clothing_tags":   "clothing_desc",
        "accessory_tags":  "accessory_desc",
        "pose_tags":       "pose_desc",
        "expression_tags": "face_desc",
        "background_tags": "scene_desc",
        "object_tags":     "object_desc",
        "lighting_tags":   "lighting_desc",
    }
    out = {cat: _extract_embedded_tags(world_spec.get(src, "")) for cat, src in cat_map.items()}
    # subject_tags: Refine Visual Spec parity (9 categories)
    subject = _extract_embedded_tags(world_spec.get("character_desc", ""))
    out["subject_tags"] = subject
    return out


def _parse_json_from_llm(raw: str) -> dict:
    """Extract JSON from LLM output, handling ```json blocks."""
    m = re.search(r"```json\s*(.*?)```", raw, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _split_tags(tag_str: str) -> list[str]:
    """Parse, normalize, and deduplicate a comma-or-newline-separated tag string."""
    result: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,\n]", tag_str):
        t = re.sub(r"^[\d\.\-\*\s]+", "", part).strip().lower().replace(" ", "_")
        if t and len(t) >= 2 and not t.startswith("http") and t not in seen:
            result.append(t)
            seen.add(t)
    return result



def _build_tag_to_axis() -> dict[str, str]:
    """Build tag→axis mapping from shared catalog + WD14 character names."""
    return tag_catalog.build_tag_to_axis(extra_always_fixed=_WD14_CHAR_TAGS)


_TAG_TO_AXIS: dict[str, str] = _build_tag_to_axis()


def _get_tag_axis(tag: str) -> str | None:
    """Return the axis for a tag; None if not in any frozenset."""
    return tag_catalog.get_tag_axis(tag, mapping=_TAG_TO_AXIS)


def _group_volatile_by_axis(
    volatile_tags: list[str],
    change_targets: list[str],
    axis_override: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Group volatile_tags by axis.
    Falls back to axis_override (LLM classification) when the frozenset has no match.
    Tags still unresolved are placed in 'other'."""
    groups: dict[str, list[str]] = {a: [] for a in change_targets}
    for tag in volatile_tags:
        axis = _get_tag_axis(tag)
        if axis is None and axis_override:
            axis = axis_override.get(tag)
        if axis and axis in groups:
            groups[axis].append(tag)
        else:
            groups.setdefault("other", []).append(tag)
    return groups


def _apply_frozenset_corrections(
    fixed: list[str],
    volatile: list[str],
    change_targets: set[str],
) -> tuple[list[str], list[str]]:
    """Override LLM classification results with frozenset + WD14 category rules.
    Tags not in any frozenset keep the LLM's classification."""
    new_fixed: list[str] = []
    new_volatile: list[str] = []
    seen: set[str] = set()
    volatile_set = set(volatile)

    def classify(tag: str) -> None:
        if tag in seen:
            return
        seen.add(tag)
        axis = _get_tag_axis(tag)
        if axis is None:
            (new_volatile if tag in volatile_set else new_fixed).append(tag)
        elif axis == 'always_fixed':
            new_fixed.append(tag)
        else:
            (new_volatile if axis in change_targets else new_fixed).append(tag)

    for tag in fixed:
        classify(tag)
    for tag in volatile:
        classify(tag)

    return new_fixed, new_volatile


def _categorize_fixed_tags(
    fixed_tags: list[str],
    base_tags: list[str] | None = None,
    volatile_tags: list[str] | None = None,
) -> dict[str, list[str]]:
    """Group FIXED tags by category and return (JSON-driven).
    When base_tags / volatile_tags are supplied, 'other' holds all unclassified tags."""
    result: dict[str, list[str]] = {}

    def add(group: str, tag: str) -> None:
        result.setdefault(group, []).append(tag)

    for tag in fixed_tags:
        # Process suffix/keyword patterns first (takes priority over dict lookup)
        if tag.endswith("_hair"):
            add("hair", tag)
        elif tag.endswith("_eyes"):
            add("eyes", tag)
        elif any(tag.endswith(s) for s in _FTC_CLOTHING_SUFFIXES):
            add("clothing", tag)
        elif any(kw in tag for kw in _FTC_ACTION_KEYWORDS):
            add("action", tag)
        else:
            # JSON-driven dict lookup
            group = _TAG_DISPLAY_GROUP.get(tag, "_tmp_other")
            add(group, tag)

    # Residual computation: what remains after subtracting classified + volatile from base_tags goes into 'other'
    if base_tags is not None:
        classified: set[str] = {
            t for grp, tags in result.items() if grp != "_tmp_other"
            for t in tags
        }
        volatile_set = set(volatile_tags or [])
        residual = [t for t in base_tags if t not in classified and t not in volatile_set]
        result.pop("_tmp_other", None)
        if residual:
            result["other"] = residual
    else:
        other = result.pop("_tmp_other", [])
        if other:
            result["other"] = other

    return result


async def _step1_dynamic_separator(
    base_tags: list[str],
    ollama,
    model: str,
    options: dict | None = None,
    frozenset_enabled: bool = True,
) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    """Phase A: classify all tags into always_fixed / axis groups via frozensets.
    Phase B: classify unknown tags via LLM JSON dict and return llm_classification.
    Returns (always_fixed, all_axis_grouped, llm_classification)."""
    always_fixed: list[str] = []
    by_axis: dict[str, list[str]] = {}
    unknown_tags: list[str] = []

    # Phase A: definitive classification via frozensets (if off, all tags go to Phase B)
    if frozenset_enabled:
        for tag in base_tags:
            axis = _get_tag_axis(tag)
            if axis is None:
                unknown_tags.append(tag)
            elif axis == 'always_fixed':
                always_fixed.append(tag)
            else:
                by_axis.setdefault(axis, []).append(tag)
    else:
        unknown_tags = list(base_tags)

    # Phase B: Ask the LLM to classify unknown tags (or all tags) using a JSON dict
    llm_classification: dict[str, str] = {}
    if unknown_tags:
        try:
            prompt = _STEP1_CLASSIFY_PROMPT.format(
                axis_table=STEP1_AXIS_TABLE,
                unknown_tags=", ".join(unknown_tags),
            )
            raw = await ollama.generate_text(prompt, model=model, options=options)
            data = _parse_json_from_llm(raw)
            unknown_set = set(unknown_tags)
            classified: set[str] = set()
            for tag_raw, axis_raw in data.items():
                tag = str(tag_raw).lower().strip().replace(" ", "_")
                axis = str(axis_raw).lower().strip().replace(" ", "_")
                # Normalize aliases and spelling variants
                axis = normalize_axis(axis)
                if tag not in unknown_set:
                    continue
                classified.add(tag)
                llm_classification[tag] = axis  # Record LLM classification result
                if axis in ('fixed', 'always_fixed'):
                    always_fixed.append(tag)
                else:
                    by_axis.setdefault(axis, []).append(tag)
            # Unknown tags omitted by the LLM default to always_fixed (safe fallback)
            for tag in unknown_tags:
                if tag not in classified:
                    always_fixed.append(tag)
        except Exception:
            always_fixed.extend(unknown_tags)

    return always_fixed, by_axis, llm_classification


# Reuse prompt_desc from inspire_axes.AXIS_DEFINITIONS
_CHANGE_TARGET_DESCRIPTIONS: dict[str, str] = {
    k: v["prompt_desc"] for k, v in AXIS_DEFINITIONS.items()
}

_STEP2B_PROMPT = """\
# ROLE
You are a strict tag domain auditor for image inversion.

# TASK
Audit [NEW_TAGS] against the selected [CHANGE_TARGETS] domains.
Remove any tag that:
  1. Belongs to a domain NOT listed in CHANGE_TARGETS
  2. Does NOT actually invert any concept in [VOLATILE_TAGS]
  3. Is semantically identical to an existing VOLATILE concept (not inverted)

Keep tags that are genuine conceptual opposites within the allowed domains.
IMPORTANT: Do NOT over-prune. Aim to keep at least {min_keep} tags — an empty or near-empty
result will collapse the generated scene. When in doubt, keep the tag.

# CHANGE_TARGETS (allowed domains only)
{change_targets_desc}

# VOLATILE_TAGS (what was targeted)
{volatile_tags}

# NEW_TAGS (audit these — keep only valid ones)
{new_tags}

# OUTPUT (JSON only)
{{"verified_new_tags": "valid_tag_a, valid_tag_b", "rejected": ["tag_x: outside CHANGE_TARGETS domain", "tag_y: repeats volatile concept"]}}"""


async def _step2b_inversion_verifier(
    new_tags: list[str],
    volatile_tags: list[str],
    change_targets: list[str],
    ollama,
    model: str,
    options: dict | None = None,
) -> list[str]:
    if not new_tags:
        return []
    min_keep = max(len(change_targets) * 2, 3)
    change_targets_desc = "\n".join(
        f"  - {_CHANGE_TARGET_DESCRIPTIONS.get(t, t)}" for t in change_targets
    ) or "  (all axes)"
    prompt = _STEP2B_PROMPT.format(
        change_targets_desc=change_targets_desc,
        volatile_tags=", ".join(volatile_tags) or "(none)",
        new_tags=", ".join(new_tags),
        min_keep=min_keep,
    )
    try:
        raw = await ollama.generate_text(prompt, model=model, options=options)
        data = _parse_json_from_llm(raw)
        verified = _split_tags(data.get("verified_new_tags", ""))
        # Fall back to original if verifier prunes below the minimum
        return verified if len(verified) >= min(min_keep, len(new_tags)) else new_tags
    except Exception:
        return new_tags


async def _step2_semantic_inverter(
    all_axis_grouped: dict[str, list[str]],
    change_targets: list[str],
    visual_vocab: list[str],
    image_bytes_list: list[bytes],
    base_tags: list[str],
    strength: float,
    ollama,
    model: str,
    options: dict | None = None,
) -> tuple[list[str], list[str], dict[str, list[str]], dict[str, list[str]]]:
    if strength <= 0.3:
        intensity = "GENTLE: subtle variation, similar feel but slightly different"
    elif strength <= 0.6:
        intensity = "MODERATE: noticeable contrast, keep ~40% of original feel"
    elif strength <= 0.9:
        intensity = "STRONG: significant inversion, clearly different world"
    else:
        intensity = "DRAMATIC: complete conceptual opposite, maximum contrast"

    change_set = set(change_targets)

    # Pass all axis tags to the LLM (full-context inversion)
    # Include change_targets axes even if WD14 detected nothing (VLM supplements from the image)
    all_axis_for_prompt = {ax: tags for ax, tags in all_axis_grouped.items() if tags}
    for ax in change_targets:
        if ax not in all_axis_for_prompt:
            all_axis_for_prompt[ax] = []
    if not all_axis_for_prompt:
        return [], [], {}, {"_debug": ["all_axis_grouped is empty"]}

    # Request 20 proposals and randomly select 4 downstream
    n_per_axis = 20
    target_tags = [t for ax, tags in all_axis_grouped.items() if ax in change_set for t in tags]
    n_max = len(change_targets) * n_per_axis + 10

    # Generate full-axis input in JSON format
    all_axis_json = json.dumps(all_axis_for_prompt, ensure_ascii=False)
    # Explicitly pass all axis names as expected_axes to the LLM
    expected_axes = ", ".join(all_axis_for_prompt.keys())

    # Allow *_hair output when hair axis is selected; ban it otherwise
    hair_is_target = "hair" in change_set
    if hair_is_target:
        character_attr_rule = (
            "4. You MAY output *_hair tags in the 'hair' axis — invert hair color and style creatively.\n"
            "   NEVER output *_eyes tags (eye color is always fixed)."
        )
    else:
        character_attr_rule = (
            "4. NEVER output *_hair tags (hair is fixed — not a selected axis for this run).\n"
            "   NEVER output *_eyes tags (eye color is always fixed)."
        )

    # Inversion direction hint: detect day/night from selected axis tags (only when time_weather/visual axis is targeted)
    target_lower = " ".join(target_tags).lower()
    _night_words = {"night", "moon", "dark", "dusk", "midnight", "evening", "dim", "shadow"}
    _day_words   = {"day", "sun", "bright", "morning", "noon", "sunny", "daytime", "clear"}
    has_night = any(w in target_lower for w in _night_words)
    has_day   = any(w in target_lower for w in _day_words)

    _time_axes = {"visual", "time_weather"}
    has_time_axis = bool(_time_axes & change_set)

    if has_night and has_time_axis:
        direction_hint = (
            "\n# DIRECTION (MANDATORY)\n"
            "SOURCE contains night/moon/dark → time_weather/visual axis MUST invert to DAYTIME.\n"
            "Include: daylight, morning, sunshine, blue_sky, or similar bright atmosphere.\n"
            "BANNED for this run: night, moonlight, dark, dusk, dim, shadow, full_moon."
        )
    elif has_day and has_time_axis:
        direction_hint = (
            "\n# DIRECTION (MANDATORY)\n"
            "SOURCE contains day/bright/sunny → time_weather/visual axis must invert to evening, night, or dim."
        )
    else:
        direction_hint = ""

    # Color instruction — stronger when hints exist
    raw_color_hints = _compute_color_hints(base_tags)
    if raw_color_hints:
        color_instruction = (
            "\n# COLOR INSTRUCTION (MANDATORY when visual/style axis selected)\n"
            + raw_color_hints
            + "Apply complementary colors to the visual/style axis output.\n"
            "Explicitly name the target color palette (e.g., warm_amber_tones, cool_teal_palette)."
        )
    else:
        color_instruction = (
            "\n# COLOR NOTE\n"
            "Consider warm↔cool or vibrant↔muted palette shift for visual/style axis."
        )

    prompt = _STEP2_PROMPT.format(
        all_axis_json=all_axis_json,
        inversion_hints=STEP2_INVERSION_HINTS,
        expected_axes=expected_axes,
        n_per_axis=n_per_axis,
        character_attr_rule=character_attr_rule,
        direction_hint=direction_hint,
        color_instruction=color_instruction,
    )

    try:
        if image_bytes_list:
            raw = await ollama.generate_vlm(prompt, image_bytes_list, model=model, options=options)
        else:
            raw = await ollama.generate_text(prompt, model=model, options=options)
        data = _parse_json_from_llm(raw)

        # Flatten per-axis JSON while also building per-axis dict
        new_tags: list[str] = []
        new_tags_by_axis: dict[str, list[str]] = {}
        raw_by_axis: dict[str, list[str]] = {}  # Raw LLM output before filtering (all axes recorded)
        by_axis = data.get("new_tags_by_axis", {})
        if isinstance(by_axis, dict):
            for axis_key, axis_val in by_axis.items():
                tags_for_axis: list[str] = []
                if isinstance(axis_val, str):
                    tags_for_axis = _split_tags(axis_val)
                elif isinstance(axis_val, list):
                    for t in axis_val:
                        if isinstance(t, str) and t.strip():
                            tags_for_axis.extend(_split_tags(t))
                # Record all axes in raw_by_axis for debugging (before selected-axis filter)
                if tags_for_axis:
                    raw_by_axis[str(axis_key)] = list(tags_for_axis)
                # Skip non-selected axes (Python-side axis filter)
                if str(axis_key) not in change_set:
                    continue
                # Always remove *_eyes; remove *_hair unless hair axis is selected
                tags_for_axis = [
                    t for t in tags_for_axis
                    if not t.endswith('_eyes')
                    and (not t.endswith('_hair') or (hair_is_target and str(axis_key) == 'hair'))
                ]
                if tags_for_axis:
                    selected = random.sample(tags_for_axis, min(8, len(tags_for_axis)))
                    new_tags_by_axis[str(axis_key)] = selected
                    new_tags.extend(selected)
        # fallback: flat "new_tags" key (selected-axis filter not applied)
        if not new_tags:
            raw_flat = _split_tags(data.get("new_tags", ""))
            for t in raw_flat:
                raw_by_axis.setdefault("(flat)", []).append(t)
            new_tags = [t for t in raw_flat
                        if not t.endswith('_eyes')
                        and (not t.endswith('_hair') or hair_is_target)]

        # Remove source tags from selected axes (deduplication)
        volatile_set = {t for ax, tags in all_axis_grouped.items() if ax in change_set for t in tags}
        new_tags = [t for t in dict.fromkeys(new_tags) if t not in volatile_set]
        new_tags = new_tags[:n_max]
        # Debug: record input axis info in raw_by_axis
        if not raw_by_axis:
            raw_by_axis["_debug_input"] = [
                f"all_axis_count={len(all_axis_for_prompt)}",
                f"input_axes={list(all_axis_for_prompt.keys())}",
                f"llm_returned={list(by_axis.keys()) if isinstance(by_axis, dict) else str(type(by_axis))}",
            ]
        _nr = data.get("neutralizer_tags", "")
        if isinstance(_nr, list):
            neutralizer = [str(t).lower().strip().replace(" ", "_") for t in _nr if str(t).strip()]
        else:
            neutralizer = _split_tags(_nr)
        return new_tags, neutralizer, new_tags_by_axis, raw_by_axis
    except Exception as exc:
        logger.warning("_step2_semantic_inverter exception: %s", exc, exc_info=True)
        return [], [], {}, {}


async def _post_safety_guardian(
    tags: list[str],
    ollama,
    model: str,
    options: dict | None = None,
) -> list[str]:
    prompt = _SAFETY_PROMPT.format(tags=", ".join(tags))
    try:
        raw = await ollama.generate_text(prompt, model=model, options=options)
        data = _parse_json_from_llm(raw)
        if not data.get("safe", True):
            cleaned = _split_tags(data.get("cleaned_tags", ""))
            return cleaned if cleaned else tags
        return tags
    except Exception:
        return tags


def _bm25_normalize_tags(tags: list[str]) -> list[str]:
    """Validate/normalize VLM-extracted tags against Danbooru vocabulary via BM25."""
    if not tags:
        return []
    try:
        from ..ai.wd14 import normalize_tag_string
    except ImportError:
        return tags
    normalized = normalize_tag_string(", ".join(tags))
    return [t.strip() for t in normalized.split(",") if t.strip()]


def _normalize_section(text: str) -> str:
    """Normalize a comma-separated tag string against Danbooru vocabulary via BM25."""
    if not text:
        return text
    try:
        from ..ai.wd14 import normalize_tag_string
        return normalize_tag_string(text) or text
    except ImportError:
        return text


def _apply_section_overrides(
    fixed_tags: list[str],
    sections: dict[str, str],
) -> list[str]:
    """Replace WD14-detected hair/eye tags with user-specified ones from character section."""
    char_text = sections.get("character", "")
    if not char_text:
        return fixed_tags
    user_tags = [t.strip().replace(" ", "_") for t in char_text.split(",") if t.strip()]
    user_hair = [t for t in user_tags if t.endswith("_hair")]
    user_eyes = [t for t in user_tags if t.endswith("_eyes")]
    result = list(fixed_tags)
    if user_hair:
        result = [t for t in result if not t.endswith("_hair")]
        result.extend(user_hair)
    if user_eyes:
        result = [t for t in result if not t.endswith("_eyes")]
        result.extend(user_eyes)
    return result


def _apply_code_fixup(
    final_positive: list[str],
    fixed_tags: list[str],
    custom_blacklist: list[str],
) -> list[str]:
    """Force-prepend FIXED_TAGS, apply blacklist, and deduplicate."""
    blacklist = set(custom_blacklist)
    merged: list[str] = []
    seen: set[str] = set()
    for t in list(fixed_tags) + list(final_positive):
        if t not in seen and t not in blacklist:
            merged.append(t)
            seen.add(t)
    return merged


def _remove_fixed_from_negative(
    final_negative: list[str],
    fixed_tags: list[str],
) -> list[str]:
    """BM25-like token overlap filter: remove tags from NEGATIVE that are similar to FIXED tags."""
    exact = set(fixed_tags)
    fixed_tokens: set[str] = {
        tok for tag in fixed_tags
        for tok in tag.split("_") if len(tok) >= 3
    }
    result: list[str] = []
    for tag in final_negative:
        if tag in exact:
            continue
        tag_toks = set(tag.split("_"))
        overlap = tag_toks & fixed_tokens
        if (len(tag_toks) == 1 and overlap) or len(overlap) >= 2:
            continue
        result.append(tag)
    return result


_COLOR_COMPLEMENT_MAP: dict[str, str] = {
    "red_hair":      "teal_hair, cyan_hair",
    "orange_hair":   "blue_hair",
    "yellow_hair":   "violet_hair, purple_hair",
    "blonde_hair":   "purple_hair",
    "green_hair":    "magenta_hair, pink_hair",
    "blue_hair":     "orange_hair, amber_hair",
    "purple_hair":   "yellow_hair, lime_hair",
    "pink_hair":     "teal_hair",
    "red_eyes":      "cyan_eyes, teal_eyes",
    "blue_eyes":     "orange_eyes, amber_eyes",
    "green_eyes":    "purple_eyes, magenta_eyes",
    "yellow_eyes":   "violet_eyes",
    "purple_eyes":   "yellow_eyes",
    "warm_colors":   "cool_colors",
    "cool_colors":   "warm_colors",
    "vibrant_color": "monochrome, muted_colors",
    "pastel_colors": "deep_colors, saturated",
}


def _dedup_by_tags(results: list[dict], threshold: float = 0.85) -> list[dict]:
    """Deduplicate results by WD14 tag Jaccard similarity. results assumed sorted by score descending."""
    kept: list[dict] = []
    kept_tag_sets: list[frozenset] = []
    for doc in results:
        tags = frozenset(doc.get("wd14_tags") or [])
        is_dup = False
        if tags:
            for ks in kept_tag_sets:
                if ks:
                    union = len(tags | ks)
                    if union > 0 and len(tags & ks) / union >= threshold:
                        is_dup = True
                        break
        if not is_dup:
            kept.append(doc)
            kept_tag_sets.append(tags)
    return kept


def _dedup_scored(
    scored: list[tuple[dict, float]], threshold: float = 0.85
) -> list[tuple[dict, float]]:
    """Near-duplicate deduplication for scored result lists."""
    kept: list[tuple[dict, float]] = []
    kept_tag_sets: list[frozenset] = []
    for doc, score in scored:
        tags = frozenset(doc.get("wd14_tags") or [])
        is_dup = False
        if tags:
            for ks in kept_tag_sets:
                if ks:
                    union = len(tags | ks)
                    if union > 0 and len(tags & ks) / union >= threshold:
                        is_dup = True
                        break
        if not is_dup:
            kept.append((doc, score))
            kept_tag_sets.append(tags)
    return kept


def _compute_color_hints(base_tags: list[str]) -> str:
    hints = [
        f"  {tag} → {_COLOR_COMPLEMENT_MAP[tag]}"
        for tag in base_tags
        if tag in _COLOR_COMPLEMENT_MAP
    ]
    if not hints:
        return ""
    return "# COMPLEMENTARY COLOR HINTS (prefer these for color inversion):\n" + "\n".join(hints) + "\n"

async def _inversion_stream(body: InversionRequest, db, ollama, cfg) -> AsyncGenerator[str, None]:
    # --- Data collection ---
    # Collect tags proportionally from each image so later images are not truncated.
    # Budget: 15 tags per image, capped at 60 total to keep LLM context manageable.
    per_image_limit = max(12, 60 // max(1, len(body.sha256s)))
    all_tags: list[str] = []
    image_bytes_list: list[bytes] = []
    for sha256 in body.sha256s[:4]:
        doc = await db.get(sha256)
        if not doc:
            continue
        if doc.get("wd14_tags"):
            all_tags.extend(doc["wd14_tags"][:per_image_limit])
        fp = Path(doc.get("path", ""))
        if fp.exists():
            image_bytes_list.append(fp.read_bytes())

    if not all_tags:
        yield _sse({"type": "error", "message": "Select images with WD14 tags (AI pipeline may not have run)"})
        return

    selected_targets = resolve_axes(body.change_targets)
    llm_options = {"num_ctx": cfg.get("ollama_num_ctx", 16384)}
    base_tags = list(dict.fromkeys(
        t.strip().lower().replace(" ", "_") for t in all_tags
    ))[:60]

    # --- Pre-Search: Visual Vocabulary ---
    yield _sse({"type": "stage", "stage": 0, "label": "Retrieving vocabulary from similar images…"})
    visual_vocab: list[str] = []
    try:
        src_vec = await ollama.embed(", ".join(base_tags), model=cfg["embed_model"])
        sim_docs = await db.search_by_vector(
            src_vec, n_results=5,
            exclude_sha256s=body.sha256s,
            exclude_reference=True,
        )
        seen_vv: set[str] = set(base_tags)
        for sdoc in sim_docs[:3]:
            for t in (sdoc.get("wd14_tags") or [])[:20]:
                if t not in seen_vv:
                    visual_vocab.append(t)
                    seen_vv.add(t)
            if len(visual_vocab) >= 60:
                break
    except Exception:
        pass

    # --- Step 1: Dynamic Separator ---
    frozenset_enabled = bool(cfg.get("frozenset_classification", True))
    yield _sse({"type": "stage", "stage": 1, "label": "Classifying tags…"})
    try:
        always_fixed, all_axis_grouped, llm_classification = await _step1_dynamic_separator(
            base_tags, ollama, cfg["vlm_model"],
            options=llm_options, frozenset_enabled=frozenset_enabled,
        )
    except Exception as e:
        yield _sse({"type": "error", "message": f"Step1 error: {e}"})
        return
    # Separate volatile / non-target based on change_targets
    change_set = set(selected_targets)
    volatile_tags = [t for ax, tags in all_axis_grouped.items() if ax in change_set for t in tags]
    non_target_tags = [t for ax, tags in all_axis_grouped.items() if ax not in change_set for t in tags]
    fixed_tags = always_fixed + non_target_tags
    # Safety net: re-assert frozenset + WD14 rules over LLM / split mistakes
    # (e.g. smile left in fixed while emotion is a change target).
    if frozenset_enabled:
        fixed_tags, volatile_tags = _apply_frozenset_corrections(
            fixed_tags, volatile_tags, change_set,
        )
    fixed_tags_grouped = _categorize_fixed_tags(fixed_tags, base_tags, volatile_tags)
    # Reconstruct complete fixed_tags from grouped and pass to all subsequent steps
    fixed_tags = [tag for tags in fixed_tags_grouped.values() for tag in tags]
    # User section overrides: user-specified hair/eye tags take priority over WD14 detected ones
    if body.user_inject_sections:
        fixed_tags = _apply_section_overrides(fixed_tags, body.user_inject_sections)
    # Only expose groups for selected axes as volatile_tags_grouped
    volatile_tags_grouped = _group_volatile_by_axis(
        volatile_tags, selected_targets, axis_override=llm_classification or None,
    )
    volatile_tags_grouped = {
        ax: tags for ax, tags in volatile_tags_grouped.items()
        if ax in change_set and tags
    }
    yield _sse({"type": "step1_result", "fixed_tags": fixed_tags, "volatile_tags": volatile_tags,
                "fixed_tags_grouped": fixed_tags_grouped, "volatile_tags_grouped": volatile_tags_grouped,
                "llm_classification": llm_classification})

    # --- Step 2: Semantic Inverter ---
    yield _sse({"type": "stage", "stage": 2, "label": "Designing the inverted world…"})
    tile_bytes: bytes | None = None
    if image_bytes_list:
        try:
            tile_bytes = create_tile_image(image_bytes_list)
        except Exception:
            pass
    strength = max(0.1, min(1.0, body.inversion_strength))
    try:
        new_tags, neutralizer_tags, new_tags_by_axis, step2_raw_by_axis = await _step2_semantic_inverter(
            all_axis_grouped, selected_targets,
            visual_vocab,
            [tile_bytes] if tile_bytes else [],
            base_tags, strength,
            ollama, cfg["vlm_model"], options=llm_options,
        )
    except Exception as e:
        logger.error("Step2 inversion error: %s", e, exc_info=True)
        yield _sse({"type": "error", "message": "反転タグ生成中にエラーが発生しました"})
        return
    yield _sse({"type": "step2_result", "new_tags": new_tags, "neutralizer_tags": neutralizer_tags,
                "new_tags_by_axis": new_tags_by_axis, "step2_raw_by_axis": step2_raw_by_axis})

    # --- Step 2b: Inversion tag validation (skipped by default for speed) ---
    if not body.skip_verifier:
        yield _sse({"type": "stage", "stage": 2, "label": "Validating inversion tags…"})
        try:
            verified_new_tags = await _step2b_inversion_verifier(
                new_tags, volatile_tags, selected_targets, ollama, cfg["vlm_model"], options=llm_options
            )
            # If more than 20% of tags were removed, respect Step2 output (prevents verifier from over-pruning good tags)
            if verified_new_tags and len(verified_new_tags) >= len(new_tags) * 0.8:
                new_tags = verified_new_tags
            yield _sse({"type": "step2b_result", "new_tags": new_tags, "new_tags_by_axis": new_tags_by_axis,
                        "step2_raw_by_axis": step2_raw_by_axis})
        except Exception:
            pass

    lang = body.lang if body.lang in ("ja", "en") else "en"
    lang_label = "Japanese" if lang == "ja" else "English"

    # --- Step 3: Visual World Builder (VLM when tile available, text-only fallback) ---
    yield _sse({"type": "stage", "stage": 3, "label": "Building visual specification…"})
    world_spec = await _step3_world_builder(
        fixed_tags, new_tags, step2_raw_by_axis,
        body.user_inject_prompt, ollama, cfg["vlm_model"], options=llm_options,
        tile_bytes=tile_bytes,
        user_inject_sections=body.user_inject_sections or {},
    )
    # Extract per-category tags from STEP3 danbooru-embedded *_desc fields
    ws_cat_tags = _extract_spec_category_tags(world_spec)
    # Subject anchors from fixed character tags (Refine parity)
    from ..tags.subject_anchors import SUBJECT_ANCHOR_TAGS
    _subj = [
        t for t in fixed_tags
        if t.lower().replace(" ", "_") in SUBJECT_ANCHOR_TAGS
    ]
    if _subj:
        seen_s = {x.lower() for x in ws_cat_tags.get("subject_tags", [])}
        merged_s = list(ws_cat_tags.get("subject_tags") or [])
        for t in _subj:
            if t.lower() not in seen_s:
                merged_s.append(t)
                seen_s.add(t.lower())
        ws_cat_tags["subject_tags"] = merged_s
    # BM25 normalize: validate/replace non-standard tags against Danbooru vocabulary
    ws_cat_tags = {k: _bm25_normalize_tags(v) for k, v in ws_cat_tags.items()}
    yield _sse({"type": "step3_result", **ws_cat_tags})

    # --- Step 4: Visual Script Writer (streaming story → JSON) ---
    yield _sse({"type": "stage", "stage": 4, "label": "Writing the story…"})
    visual_spec_nl = _format_visual_spec_nl(world_spec)
    context_story = ""
    final_positive: list[str] = []
    final_positive_nl = ""
    final_negative: list[str] = []
    try:
        _safe_spec = (visual_spec_nl or "(none)").replace("{", "{{").replace("}", "}}")
        prompt4 = _STEP4_PROMPT.format(
            lang=lang,
            lang_label=lang_label,
            visual_spec_nl=_safe_spec,
            fixed_tags=", ".join(fixed_tags) or "(none)",
            neutralizer_tags=", ".join(neutralizer_tags) or "(none)",
            neutralizer_placeholder=", ".join(neutralizer_tags) if neutralizer_tags else "worst_quality",
            char_count_rule=_char_count_rule(fixed_tags),
        )
        buf4: list[str] = []
        async for event in ollama.generate_text_stream(prompt4, model=cfg["vlm_model"], options=llm_options):
            if event.get("type") == "token":
                text = event["text"]
                buf4.append(text)
                yield _sse({"type": "story_token", "text": text})
        raw4 = "".join(buf4)
        data4 = _parse_json_from_llm(raw4)
        # Split story text from terminal JSON block — try ```json fence first, then bare {
        cb = raw4.rfind("```json")
        if cb >= 0:
            context_story = raw4[:cb].strip()
        else:
            nj = raw4.rfind("\n{")
            context_story = raw4[:nj].strip() if nj >= 0 else raw4.strip()
        final_positive = _split_tags(data4.get("final_positive_tags", ""))
        final_positive_nl = data4.get("final_positive_nl", "").strip()
        final_negative = _split_tags(data4.get("final_negative", ""))
        # Fallback: if JSON tags are sparse, regex-extract embedded tags from story text
        if len(final_positive) < 10:
            story_embedded = _extract_embedded_tags(context_story)
            _seen_fp: set[str] = set(final_positive)
            for t in story_embedded:
                if t not in _seen_fp:
                    final_positive.append(t)
                    _seen_fp.add(t)
    except Exception as e:
        logger.error("Step4 error: %s", e, exc_info=True)
        yield _sse({"type": "error", "message": f"Step4 error: {e}"})
        return

    # --- Backend force-include guarantee (triple safety) ---
    final_positive = _apply_code_fixup(final_positive, fixed_tags, body.custom_blacklist)
    _bl = set(body.custom_blacklist)
    _seen = set(final_positive)
    # 1. Force-include STEP2 new_tags (existing behaviour)
    for t in new_tags:
        if t not in _seen and t not in _bl:
            final_positive.append(t)
            _seen.add(t)
    # 2. Force-include STEP3 category tags (regex-extracted from *_desc)
    for cat_tags in ws_cat_tags.values():
        for t in cat_tags:
            if t not in _seen and t not in _bl:
                final_positive.append(t)
                _seen.add(t)
    final_negative = _remove_fixed_from_negative(final_negative, fixed_tags)
    # atmosphere_tags kept for backward-compat: use lighting_tags from world spec
    atmosphere_tags: list[str] = ws_cat_tags.get("lighting_tags", [])
    yield _sse({"type": "step4_result", "final_positive": final_positive,
                "final_negative": final_negative, **ws_cat_tags})

    # --- Post: Safety Guardian ---
    yield _sse({"type": "stage", "stage": 5, "label": "Safety check…"})
    final_positive = await _post_safety_guardian(final_positive, ollama, cfg["vlm_model"], options=llm_options)

    # --- Apply prompt_removal_tags (admin forbidden words) ---
    removal_set: set[str] = {
        t.lower().replace(" ", "_")
        for t in cfg.get("prompt_removal_tags", [])
        if t.strip()
    }
    # Also include custom_blacklist from request (already filtered by _apply_code_fixup,
    # but track them here so they appear in removed_tags report)
    removal_set.update(t.lower().replace(" ", "_") for t in body.custom_blacklist if t.strip())
    removed_tags: list[str] = []
    if removal_set:
        kept: list[str] = []
        for t in final_positive:
            # BM25 may yield space-form tags; match underscore-normalized removal set
            norm = str(t).lower().replace(" ", "_")
            if norm in removal_set or t in removal_set:
                removed_tags.append(t)
            else:
                kept.append(t)
        final_positive = kept

    # --- Final Search ---
    query_vec = await ollama.embed(", ".join(final_positive), model=cfg["embed_model"])
    docs = await db.search_by_vector(
        query_vec, n_results=body.n_results * 2,
        exclude_sha256s=body.sha256s, exclude_reference=True,
    )
    docs = _dedup_by_tags(docs)[:body.n_results]

    yield _sse({
        "type": "done",
        "results": docs,
        "inversion_tags": final_positive,
        "inversion_tags_nl": final_positive_nl,
        "inversion_negative_tags": final_negative,
        "inversion_story": context_story,
        "fixed_tags": fixed_tags,
        "fixed_tags_grouped": fixed_tags_grouped,
        "volatile_tags": volatile_tags,
        "volatile_tags_grouped": volatile_tags_grouped,
        "new_tags": new_tags,
        "new_tags_by_axis": new_tags_by_axis,
        "step2_raw_by_axis": step2_raw_by_axis,
        "atmosphere_tags": atmosphere_tags,
        "removed_tags": removed_tags,
        **ws_cat_tags,
    })


@router.post("/expand-theme")
async def expand_theme(body: ExpandThemeRequest, request: Request):
    """Submit a job to the PROMPT lane and return job_id. Stream via /expand-theme/{job_id}/stream."""
    if not body.theme.strip():
        raise HTTPException(422, "theme must not be empty")
    from ..jobs.runners import run_expand_theme
    spooler = request.app.state.spooler
    db      = request.app.state.db
    ollama  = request.app.state.ollama

    event_queue: asyncio.Queue = asyncio.Queue()
    job_id = spooler.submit(
        JobLane.PROMPT,
        "expand_theme",
        run_expand_theme,
        meta={},
        body_dict=body.model_dump(),
        db=db,
        ollama=ollama,
        event_queue=event_queue,
    )
    request.app.state.inspire_event_queues[job_id] = event_queue
    return {"job_id": job_id, "status": "queued"}


@router.get("/expand-theme/{job_id}/stream")
async def expand_theme_stream(job_id: str, request: Request):
    q: asyncio.Queue | None = request.app.state.inspire_event_queues.get(job_id)
    if q is None:
        raise HTTPException(404, f"expand-theme job {job_id!r} not found")
    return queue_sse_response(
        request, q, job_id=job_id,
        registry=request.app.state.inspire_event_queues, encode="raw",
    )


@router.post("/inversion")
async def inversion(body: InversionRequest, request: Request):
    if not body.sha256s:
        raise HTTPException(400, "Select an image")
    from ..jobs.runners import run_inversion
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama

    event_queue: asyncio.Queue = asyncio.Queue()
    job_id = spooler.submit(
        JobLane.PROMPT,
        "prompt_refine",
        run_inversion,
        meta={"sha256s": body.sha256s[:4]},
        body_dict=body.model_dump(),
        db=db,
        ollama=ollama,
        event_queue=event_queue,
    )
    request.app.state.inspire_event_queues[job_id] = event_queue
    return {"job_id": job_id, "status": "queued"}


@router.get("/inversion/{job_id}/stream")
async def inversion_stream(job_id: str, request: Request):
    q: asyncio.Queue | None = request.app.state.inspire_event_queues.get(job_id)
    if q is None:
        raise HTTPException(404, f"Inversion job {job_id!r} not found")
    return queue_sse_response(
        request, q, job_id=job_id,
        registry=request.app.state.inspire_event_queues, encode="raw",
    )


async def _brainstorm_stream(
    sha256s: list[str],
    extra_tags: list[str],
    db,
    ollama,
    cfg: dict,
    lang: str = "ja",
    reference_tags: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    # ``reference_tags`` lets a caller supply the tag set directly instead of
    # harvesting it from library documents. Muse needs that: its board images
    # are re-tagged at a much lower threshold than the library pipeline uses,
    # and the merged result is the whole point of the step feeding this one.
    if reference_tags is not None:
        unique_tags = list(dict.fromkeys(reference_tags))[:50]
    else:
        wd14_tags: list[str] = []
        for sha256 in sha256s[:6]:
            doc = await db.get(sha256)
            if doc and doc.get("wd14_tags"):
                wd14_tags.extend(doc["wd14_tags"][:20])
        unique_tags = list(dict.fromkeys(wd14_tags))[:50]
    must_tags = list(dict.fromkeys(extra_tags)) if extra_tags else []

    if lang == "en":
        must_str = (
            f"You MUST center the proposals around these concepts: {', '.join(must_tags)}\n\n"
            if must_tags else ""
        )
        tag_str = ", ".join(unique_tags) if unique_tags else "(no tags)"
        prompt = (
            "You are a creative director specializing in anime and illustration.\n"
            f"{must_str}"
            "Based on the given WD14 tags, propose 3–5 specific, compelling scene ideas "
            "that an illustrator would genuinely want to draw.\n\n"
            "Each proposal should include:\n"
            "- Scene concept (one line)\n"
            "- Composition and pose ideas\n"
            "- Mood, color palette, and lighting direction\n"
            "- Unique elements that make the piece stand out\n\n"
            f"Reference tags: {tag_str}\n\n"
            "Output in Markdown format (## Idea N: Title) in English."
        )
    else:
        must_str = (
            f"必ず以下のコンセプトを中心に据えた提案にしてください：{', '.join(must_tags)}\n\n"
            if must_tags else ""
        )
        tag_str = ", ".join(unique_tags) if unique_tags else "（タグなし）"
        prompt = (
            "あなたはアニメ・イラスト制作を専門とするクリエイティブディレクターです。\n"
            f"{must_str}"
            "以下のWD14タグの組み合わせをもとに、イラストレーターが実際に描きたいと思える、"
            "具体性の高いシチュエーション案を3〜5つ提案してください。\n\n"
            "各提案には必ず以下を含めてください：\n"
            "- シーンのコンセプト（1行）\n"
            "- 構図・ポーズの具体的なアイデア\n"
            "- 雰囲気・色調・光源の方向性\n"
            "- 独自性を高める追加要素\n\n"
            f"参照タグ：{tag_str}\n\n"
            "回答はすべて日本語で、マークダウン形式（## 提案N：タイトル）で出力してください。"
        )

    try:
        async for event in ollama.generate_text_stream(prompt, model=cfg["vlm_model"]):
            yield _sse(event)
    except Exception as exc:
        logger.error("Brainstorm stream error: %s", exc, exc_info=True)
        yield _sse({"type": "error", "message": "ブレストの生成中にエラーが発生しました"})
        return

    yield _sse({"type": "done"})


@router.post("/brainstorm")
async def brainstorm(body: BrainstormRequest, request: Request):
    """Submit a job to the PROMPT lane and return job_id. Stream via /brainstorm/{job_id}/stream."""
    from ..jobs.runners import run_brainstorm
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama

    event_queue: asyncio.Queue = asyncio.Queue()
    job_id = spooler.submit(
        JobLane.PROMPT,
        "brainstorm",
        run_brainstorm,
        meta={"sha256s": body.sha256s[:4]},
        body_dict=body.model_dump(),
        db=db,
        ollama=ollama,
        event_queue=event_queue,
    )
    request.app.state.inspire_event_queues[job_id] = event_queue
    return {"job_id": job_id, "status": "queued"}


@router.get("/brainstorm/{job_id}/stream")
async def brainstorm_stream(job_id: str, request: Request):
    q: asyncio.Queue | None = request.app.state.inspire_event_queues.get(job_id)
    if q is None:
        raise HTTPException(404, f"Brainstorm job {job_id!r} not found")
    return queue_sse_response(
        request, q, job_id=job_id,
        registry=request.app.state.inspire_event_queues, encode="raw",
    )


@router.post("/discover")
async def discover(body: DiscoverRequest, request: Request):
    """Discovery API: find images near the target aligned with the contrast of context pairs."""
    if not body.context_pairs:
        raise HTTPException(400, "Specify at least one context_pairs entry")
    db = request.app.state.db
    docs = await db.discover_images(
        body.target_sha256,
        body.context_pairs,
        n_results=body.n_results * 2,
        exclude_reference=True,
    )
    docs = _dedup_by_tags(docs)[:body.n_results]
    return {"results": docs, "count": len(docs)}


@router.post("/grouped-search")
async def grouped_search(body: GroupedSearchRequest, request: Request):
    """GroupBy search: group query results by the specified field and return them."""
    if not body.query.strip():
        raise HTTPException(400, "Enter search text")
    db = request.app.state.db
    ollama = request.app.state.ollama
    cfg = await get_runtime_config(db)
    query_vec = await ollama.embed(body.query, model=cfg["embed_model"])
    groups = await db.search_images_grouped(
        query_vec,
        group_by=body.group_by,
        group_size=body.group_size,
        limit=body.limit,
        exclude_reference=True,
    )
    return {"groups": groups, "count": len(groups)}


@router.post("/text-search")
async def text_search(body: TextSearchRequest, request: Request):
    """Semantic search within the Inspire panel using a natural language query."""
    if not body.query.strip():
        raise HTTPException(400, "Enter search text")
    db = request.app.state.db
    ollama = request.app.state.ollama
    cfg = await get_runtime_config(db)
    try:
        query_vec = await ollama.embed(body.query, model=cfg["embed_model"])
    except Exception as e:
        raise HTTPException(500, f"Embedding error: {e}")
    docs = await db.search_by_vector(
        query_vec, n_results=body.n_results * 2, exclude_reference=True
    )
    docs = _dedup_by_tags(docs)[:body.n_results]
    return {"results": docs, "count": len(docs)}


@router.post("/blend")
async def blend(body: BlendRequest, request: Request):
    active = [s for s in body.slots if abs(s.weight) > 0.01]
    if not active:
        raise HTTPException(400, "Specify at least one image with a weight")
    db = request.app.state.db
    result_vec: list[float] | None = None
    for slot in active:
        vec = await _require_embedding(slot.sha256, db)
        n_vec = _normalize(vec)
        if result_vec is None:
            result_vec = [v * slot.weight for v in n_vec]
        else:
            for i, v in enumerate(n_vec):
                result_vec[i] += v * slot.weight
    result_vec = _normalize(result_vec)
    exclude = [s.sha256 for s in active]
    docs = await db.search_by_vector(
        result_vec,
        n_results=body.n_results * 2,
        exclude_sha256s=exclude,
        exclude_reference=True,
    )
    docs = _dedup_by_tags(docs)
    # Final check that no reference image sha256 slipped through
    active_sha256s = {s.sha256 for s in active}
    docs = [d for d in docs if d.get("sha256") not in active_sha256s]
    return {"results": docs[:body.n_results], "count": len(docs[:body.n_results])}


@router.post("/outlier")
async def outlier(body: OutlierRequest, request: Request):
    db = request.app.state.db

    if body.mode == "antipode":
        if not body.sha256s:
            raise HTTPException(400, "Antipode mode requires a reference image")
        vecs = [await db.get_embedding(sha) for sha in body.sha256s[:6]]
        vecs = [v for v in vecs if v]
        if not vecs:
            raise HTTPException(422, "Select images that have embedding vectors")
        n, dim = len(vecs), len(vecs[0])
        mean_vec = [sum(v[i] for v in vecs) / n for i in range(dim)]
        antipode_vec = _normalize([-x for x in mean_vec])
        docs = await db.search_by_vector(
            antipode_vec, n_results=body.n_results * 2, exclude_sha256s=body.sha256s, exclude_reference=True
        )
        docs = _dedup_by_tags(docs)[:body.n_results]
        return {"results": docs, "count": len(docs), "mode": "antipode"}

    # isolated mode: find outlier points by UMAP 2D density
    umap_points = await db.scroll_umap_points()
    if not umap_points:
        docs = await db.random_sample(body.n_results, exclude_sha256s=body.sha256s)
        return {"results": docs, "count": len(docs), "mode": "isolated_fallback"}

    exclude_set = set(body.sha256s)
    coords = [
        (p["sha256"], float(p.get("umap_x", 0.0)), float(p.get("umap_y", 0.0)))
        for p in umap_points
        if p.get("sha256") not in exclude_set
    ]

    r = 2.0
    density: list[tuple[str, int]] = []
    for sha, x, y in coords:
        count = sum(1 for _, cx, cy in coords if math.hypot(cx - x, cy - y) < r)
        density.append((sha, count))

    density.sort(key=lambda t: t[1])
    isolated_shas = [sha for sha, _ in density[: body.n_results * 3]]
    docs = await db.get_by_sha256s(isolated_shas[: body.n_results * 2])
    docs = _dedup_by_tags(docs)[:body.n_results]
    return {"results": docs, "count": len(docs), "mode": "isolated"}

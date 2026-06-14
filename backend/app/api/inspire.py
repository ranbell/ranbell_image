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
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
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

# ── Tag category data from JSON ────────────────────────────────────────────────
_TAG_DATA: dict = json.loads(
    (Path(__file__).parent.parent / "static" / "tag_categories.json").read_text(encoding="utf-8")
)

def _fs(*keys: str) -> frozenset[str]:
    """Retrieve tags at the given JSON key path and return as a frozenset."""
    d: dict | list = _TAG_DATA
    for k in keys:
        d = d[k]  # type: ignore
    return frozenset(d)  # type: ignore

_FTC_COUNT:             frozenset[str] = _fs("always_fixed", "count")
_FTC_EYE_SHAPES:        frozenset[str] = _fs("always_fixed", "eye_shapes")
_FTC_BODY:              frozenset[str] = _fs("always_fixed", "body")
_FTC_SKIN_FACE:         frozenset[str] = _fs("always_fixed", "skin_face")
_FTC_RACE:              frozenset[str] = _fs("always_fixed", "race")
_FTC_COMPOSITION:       frozenset[str] = _fs("always_fixed", "composition")
_FTC_PROPS:             frozenset[str] = _fs("always_fixed", "props")
_FTC_HAIR_STYLES:       frozenset[str] = _fs("axis_hair")
_FTC_EXPRESSION:        frozenset[str] = _fs("axis_emotion")
_FTC_POSE:              frozenset[str] = _fs("axis_action")
_FTC_CLOTHING_EXPLICIT: frozenset[str] = _fs("axis_clothing_explicit")
_FTC_ACCESSORIES:       frozenset[str] = _fs("axis_accessories")
_FTC_BODY_PARTS:        frozenset[str] = _fs("axis_parts")
_FTC_ART_STYLE:         frozenset[str] = (
    _fs("axis_art_style", "volatile") | _fs("axis_art_style", "always_fixed")
)
_FTC_ENVIRONMENT:       frozenset[str] = (
    _fs("axis_environment", "visual_lighting") | _fs("axis_environment", "time_weather")
)
_FTC_BACKGROUND:        frozenset[str] = (
    _fs("axis_background", "abstract") | _fs("axis_background", "location")
)
_FTC_CLOTHING_SUFFIXES: tuple[str, ...] = tuple(_TAG_DATA["patterns"]["clothing_suffixes"])
_FTC_ACTION_KEYWORDS:   tuple[str, ...] = tuple(_TAG_DATA["patterns"]["action_keywords"])

_STYLE_ALWAYS_FIXED: frozenset[str] = _fs("axis_art_style", "always_fixed")
_VISUAL_LIGHTING:    frozenset[str] = _fs("axis_environment", "visual_lighting")
_ABSTRACT_BG:        frozenset[str] = _fs("axis_background", "abstract")

# ── Display group lookup: tag → display group name (JSON driven) ──────────────
def _build_display_group_map() -> dict[str, str]:
    """Build a tag→group-name dict from tag_categories.json display_category_map."""
    result: dict[str, str] = {}
    for entry in _TAG_DATA.get("display_category_map", []):
        label = entry["label"]
        path = entry["source"].split(".")
        node: dict | list = _TAG_DATA
        for key in path:
            if isinstance(node, dict):
                node = node.get(key, [])
            else:
                node = []
        if isinstance(node, list):
            for tag in node:
                if tag not in result:          # First-win (first definition takes priority)
                    result[tag] = label
    return result

_TAG_DISPLAY_GROUP: dict[str, str] = _build_display_group_map()

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
    custom_blacklist: list[str] = []
    lang: str = "en"                 # "ja" or "en" — story generation language
    inversion_strength: float = 1.0  # 0.1–1.0


class BrainstormRequest(BaseModel):
    sha256s: list[str]
    extra_tags: list[str] = []
    lang: str = "ja"


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

def _normalize(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec))
    if mag == 0:
        return vec
    return [x / mag for x in vec]


def _vec_add(a: list[float], b: list[float]) -> list[float]:
    return [x + y for x, y in zip(a, b)]


def _vec_sub(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b)]


def _vec_lerp(a: list[float], b: list[float], t: float) -> list[float]:
    return [x + t * (y - x) for x, y in zip(a, b)]


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
You are a genius storyteller of visual scene description.

# TASK
Blend the 3 ingredients into a vivid 6-8 sentence scene description.
Then list ~5 lighting/weather tags that naturally arise from the scene.

# OUTPUT LANGUAGE: {lang}
Write the scene description in {lang_label}. The JSON atmosphere_tags must always be English.

# INGREDIENTS
1. CHARACTER (immutable): {fixed_tags}
2. NEW ENVIRONMENT: {new_tags}
3. USER REQUEST (highest priority): {user_inject_prompt}

# RULES
1. If USER REQUEST contains nouns, weave them into the scene with a dynamic relationship
   to the character (holding, wielding, gazing at, etc.). Never isolate them.
2. Describe how the NEW ENVIRONMENT lighting, shadows, and atmosphere affect the character.
3. If USER REQUEST is empty, compose from CHARACTER and NEW ENVIRONMENT only.
4. CHARACTER COUNT IS IMMUTABLE: The number of characters in your prose MUST match the
   count tag in CHARACTER exactly.
   "1girl" or "solo" → exactly one girl ("she", "the girl" — never "they", "girls", or plural).
   "2girls" → exactly two girls. "multiple_girls" / "3girls" / "4girls" → three or more girls.
   Same logic applies to boy/male tags ("1boy" → one boy only, etc.). Never contradict the count.

# OUTPUT FORMAT
Write the 6-8 sentence scene as plain prose first.
Then on a new line output ONLY this JSON (no other text after):
{{"atmosphere_tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]}}"""

_STEP4_PROMPT = """\
# ROLE
You are a Stable Diffusion prompt engineer generating both tag-based and natural-language prompts.

# TASK
From [CONTEXT_STORY], produce two prompt forms and one negative prompt.

# RULES
1. final_positive_tags: Begin with ALL [FIXED_TAGS] verbatim and in order. Then add scene tags.
2. Convert user-added objects into verb-compound tags:
   katana -> holding_katana, sword -> wielding_sword, flower -> holding_flower
3. NEVER add generic quality tags: masterpiece, best_quality, highres, etc.
4. NEGATIVE CRITICAL RULES:
   - Include all [NEUTRALIZER_TAGS] plus: worst_quality, low_quality, bad_anatomy, extra_limbs
   - NEVER put any [FIXED_TAGS] or their word components in NEGATIVE.
   - Example: if FIXED has "blue_hair", then "blue", "hair", "blue_hair" must NOT appear in NEGATIVE.
   - Example: if FIXED has "school_uniform", then "school", "uniform" must NOT appear in NEGATIVE.
5. Include relevant [ATMOSPHERE_TAGS] in final_positive_tags.
6. final_positive_nl: Write a vivid English natural-language prompt (5-7 sentences) describing
   the full scene. Reference the character's FIXED attributes naturally in the prose.
   Include lighting, atmosphere, textures, and emotional tone — the more evocative, the better.
7. CHARACTER COUNT in final_positive_nl MUST match [FIXED_TAGS] exactly.
   "1girl"/"solo" → one girl only (use "she"/"the girl", never "girls" or plural).
   "2girls" → two girls. "multiple_girls" → multiple girls.
   The prose count must always agree with the danbooru count tag in [FIXED_TAGS].

# INPUT
[CONTEXT_STORY]: {context_story}
[FIXED_TAGS]: {fixed_tags}
[NEUTRALIZER_TAGS]: {neutralizer_tags}
[ATMOSPHERE_TAGS]: {atmosphere_tags}

# OUTPUT (JSON only)
{{
  "final_positive_tags": "1girl, solo, blue_hair, cowboy_shot, ...",
  "final_positive_nl": "A vivid 5-7 sentence scene description with lighting, atmosphere, textures, and emotional tone.",
  "final_negative": "original_scene_tags, worst_quality, low_quality, ..."
}}"""

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
    """Build tag→axis mapping from frozensets + WD14 CSV. 'always_fixed' means fixed with no axis."""
    m: dict[str, str] = {}

    # WD14 category=4 character name tags → always FIXED
    for tag in _WD14_CHAR_TAGS:
        m[tag] = 'always_fixed'

    # Content rating tags → always FIXED (VLM judgment is ambiguous for these)
    for tag in ("general", "sensitive", "explicit", "safe", "nsfw",
                "questionable", "rating_safe", "rating_explicit", "rating_general"):
        m[tag] = 'always_fixed'

    # Always FIXED (never becomes volatile regardless of which axis is selected)
    for tag in (*_FTC_COUNT, *_FTC_EYE_SHAPES, *_FTC_BODY,
                *_FTC_SKIN_FACE, *_FTC_RACE, *_FTC_COMPOSITION, *_FTC_PROPS):
        m[tag] = 'always_fixed'

    # style axis — some are always FIXED (uses top-level constant _STYLE_ALWAYS_FIXED)
    for tag in _FTC_ART_STYLE:
        m[tag] = 'always_fixed' if tag in _STYLE_ALWAYS_FIXED else 'style'

    for tag in _FTC_HAIR_STYLES:        m[tag] = 'hair'
    for tag in _FTC_EXPRESSION:         m[tag] = 'emotion'
    for tag in _FTC_POSE:               m[tag] = 'action'
    for tag in _FTC_ACCESSORIES:        m[tag] = 'clothing'
    for tag in _FTC_CLOTHING_EXPLICIT:  m[tag] = 'clothing'
    for tag in _FTC_BODY_PARTS:         m[tag] = 'parts'

    # _FTC_ENVIRONMENT: visual lighting vs time/weather (uses top-level constant _VISUAL_LIGHTING)
    for tag in _FTC_ENVIRONMENT:
        m[tag] = 'visual' if tag in _VISUAL_LIGHTING else 'time_weather'

    # _FTC_BACKGROUND: abstract background vs location (uses top-level constant _ABSTRACT_BG)
    for tag in _FTC_BACKGROUND:
        m[tag] = 'visual' if tag in _ABSTRACT_BG else 'location'

    return m


_TAG_TO_AXIS: dict[str, str] = _build_tag_to_axis()


def _get_tag_axis(tag: str) -> str | None:
    """Return the axis for a tag; None if not in any frozenset. Suffix patterns take precedence."""
    if tag.endswith('_hair'):
        return 'hair'
    if tag.endswith('_eyes'):
        return 'always_fixed'
    if any(tag.endswith(s) for s in _FTC_CLOTHING_SUFFIXES):
        return 'clothing'
    if any(kw in tag for kw in _FTC_ACTION_KEYWORDS):
        return 'action'
    return _TAG_TO_AXIS.get(tag)


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
                    selected = random.sample(tags_for_axis, min(4, len(tags_for_axis)))
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


async def _step4_final_distiller(
    context_story: str,
    fixed_tags: list[str],
    neutralizer_tags: list[str],
    atmosphere_tags: list[str],
    ollama,
    model: str,
    options: dict | None = None,
) -> tuple[list[str], str, list[str]]:
    prompt = _STEP4_PROMPT.format(
        context_story=context_story or "(none)",
        fixed_tags=", ".join(fixed_tags) or "(none)",
        neutralizer_tags=", ".join(neutralizer_tags) or "(none)",
        atmosphere_tags=", ".join(atmosphere_tags) or "(none)",
    )
    try:
        raw = await ollama.generate_text(prompt, model=model, options=options)
        data = _parse_json_from_llm(raw)
        final_pos_tags = _split_tags(data.get("final_positive_tags", ""))
        final_pos_nl = data.get("final_positive_nl", "").strip()
        final_neg = _split_tags(data.get("final_negative", ""))
        return final_pos_tags, final_pos_nl, final_neg
    except Exception:
        return list(fixed_tags), "", list(neutralizer_tags)


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
    all_tags: list[str] = []
    image_bytes_list: list[bytes] = []
    for sha256 in body.sha256s[:4]:
        doc = await db.get(sha256)
        if not doc:
            continue
        if doc.get("wd14_tags"):
            all_tags.extend(doc["wd14_tags"])
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
    ))[:40]

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
    fixed_tags_grouped = _categorize_fixed_tags(fixed_tags, base_tags, volatile_tags)
    # Reconstruct complete fixed_tags from grouped and pass to all subsequent steps
    fixed_tags = [tag for tags in fixed_tags_grouped.values() for tag in tags]
    # Only expose groups for selected axes as volatile_tags_grouped
    volatile_tags_grouped = {ax: tags for ax, tags in all_axis_grouped.items() if ax in change_set and tags}
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

    # --- Step 2b: Inversion tag validation ---
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

    # --- Step 3: Context Brewer (streaming) ---
    yield _sse({"type": "stage", "stage": 3, "label": "Brewing the scene…"})
    context_story = ""
    atmosphere_tags: list[str] = []
    lang = body.lang if body.lang in ("ja", "en") else "en"
    lang_label = "Japanese" if lang == "ja" else "English"
    try:
        prompt3 = _STEP3_PROMPT.format(
            lang=lang,
            lang_label=lang_label,
            fixed_tags=", ".join(fixed_tags) or "(none)",
            new_tags=", ".join(new_tags) or "(none)",
            user_inject_prompt=body.user_inject_prompt or "(none)",
        )
        buf3: list[str] = []
        async for event in ollama.generate_text_stream(prompt3, model=cfg["vlm_model"], options=llm_options):
            if event.get("type") == "token":
                text = event["text"]
                buf3.append(text)
                yield _sse({"type": "story_token", "text": text})
        raw3 = "".join(buf3)
        atm_match = re.search(r'\{\s*"atmosphere_tags".*?\}', raw3, re.S)
        if atm_match:
            try:
                atm_data = json.loads(atm_match.group(0))
                atmosphere_tags = [
                    t.strip().lower().replace(" ", "_")
                    for t in atm_data.get("atmosphere_tags", [])
                    if t.strip()
                ]
            except json.JSONDecodeError:
                pass
            context_story = raw3[: atm_match.start()].strip()
        else:
            context_story = raw3.strip()
    except Exception as e:
        yield _sse({"type": "error", "message": f"Step3 error: {e}"})
        return
    yield _sse({"type": "step3_result", "atmosphere_tags": atmosphere_tags})

    # --- Step 4: Final Distiller ---
    yield _sse({"type": "stage", "stage": 4, "label": "Refining the prompt…"})
    try:
        final_positive, final_positive_nl, final_negative = await _step4_final_distiller(
            context_story, fixed_tags, neutralizer_tags, atmosphere_tags,
            ollama, cfg["vlm_model"], options=llm_options,
        )
    except Exception as e:
        yield _sse({"type": "error", "message": f"Step4 error: {e}"})
        return
    final_positive = _apply_code_fixup(final_positive, fixed_tags, body.custom_blacklist)
    # Ensure randomly selected tags (new_tags) are included in the final output
    _bl = set(body.custom_blacklist)
    _seen = set(final_positive)
    for t in new_tags:
        if t not in _seen and t not in _bl:
            final_positive.append(t)
            _seen.add(t)
    final_negative = _remove_fixed_from_negative(final_negative, fixed_tags)
    yield _sse({"type": "step4_result", "final_positive": final_positive, "final_negative": final_negative})

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
            if t in removal_set:
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
    })


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

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    await request.app.state.spooler.cancel(job_id)
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            request.app.state.inspire_event_queues.pop(job_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _brainstorm_stream(
    sha256s: list[str],
    extra_tags: list[str],
    db,
    ollama,
    cfg: dict,
    lang: str = "ja",
) -> AsyncGenerator[str, None]:
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

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    await request.app.state.spooler.cancel(job_id)
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            request.app.state.inspire_event_queues.pop(job_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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

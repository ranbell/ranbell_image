from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Emoji meaning descriptions (used for slogan generation) ──────────────────

_EMOJI_MEANINGS = {
    "🌸": "cherry blossoms, spring, delicate pink petals, Japanese aesthetics",
    "🌃": "night city, urban lights, dark sky, metropolitan",
    "✨": "sparkles, magic, glimmering, ethereal light",
    "😌": "peaceful, serene, calm, gentle contentment",
    "🩰": "ballet, elegance, dancer, pointe shoes",
    "🌊": "ocean waves, sea, flowing water, vast blue",
    "⚡": "lightning, electric, dynamic energy, storm",
    "🕯️": "candlelight, warm glow, intimate, soft shadows",
    "🌙": "moon, night, lunar, mysterious darkness",
    "🔥": "fire, flame, passion, intense heat",
    "🌿": "foliage, nature, botanical, green plants",
    "💧": "water droplet, purity, clear, liquid",
    "🌺": "tropical flower, vivid bloom, vibrant color",
    "🪷": "lotus, zen, floating, spiritual",
    "🦋": "butterfly, transformation, delicate wings, flutter",
    "🌅": "sunrise, golden hour, horizon, dawn light",
    "🌇": "sunset cityscape, golden dusk, urban skyline",
    "🏔️": "mountain peak, alpine, majestic height, snow",
    "🎋": "bamboo, Japanese garden, calm green",
    "🍃": "leaf, rustling, gentle breeze, botanical",
    "❄️": "ice crystal, frost, winter, cold blue-white",
    "💫": "shooting star, cosmic, swirling light, celestial",
    "🌈": "rainbow, vivid colors, after rain, hopeful",
    "🎭": "mask, drama, theatrical, duality",
    "🔮": "crystal ball, prophecy, mystic, deep violet",
    "🪞": "mirror, reflection, duality, glass surface",
    "🗝️": "old key, secret, antique, mystery",
    "🌑": "new moon, total darkness, void, silhouette",
    "🌾": "wheat field, harvest, rural, golden grain",
    "🫧": "bubbles, iridescent, floating spheres, soap film",
    "🕸️": "spider web, intricate pattern, fragile structure",
    "🐚": "seashell, spiral, ocean memory, pearl white",
    "🌫️": "mist, fog, atmospheric haze, soft obscurity",
    "🎐": "wind chime, Japanese summer, breeze, delicate",
    "🪐": "planet, space, rings, cosmic scale",
    "🌋": "volcano, eruption, dramatic, magma red",
    "🗻": "Mount Fuji, Japanese icon, snow-capped peak",
    "🌌": "galaxy, nebula, deep space, star field",
    "🌠": "shooting star, fleeting light, night sky, brief wonder",
}

# ── Slider descriptions (used for slogan generation only) ────────────────────

_SLIDER_DESCS: dict[str, dict[int, str]] = {
    "warm_cool": {-2: "very warm amber tones", -1: "warm golden palette",
                  0: "", 1: "cool blue tones", 2: "very cold icy palette"},
    "calm_dynamic": {-2: "extremely still and serene", -1: "calm and quiet",
                     0: "", 1: "dynamic and active", 2: "intense kinetic energy"},
    "dense_sparse": {-2: "richly detailed and dense", -1: "detailed composition",
                     0: "", 1: "sparse and minimalist", 2: "extreme minimalism, vast empty space"},
    "concrete_abstract": {-2: "photorealistic and concrete", -1: "detailed representational",
                          0: "", 1: "stylized and semi-abstract", 2: "fully abstract, non-representational"},
}

# ── Direct axis-fill mappings (LLM-free, deterministic) ──────────────────────

_EMOJI_AXIS_FILL: dict[str, dict[str, Any]] = {
    "🌊": {"scene": "ocean, seashore",            "lighting": "light shimmering on water"},
    "🏔️": {"scene": "mountain, snow peaks"},
    "🌸": {"scene": "cherry blossom garden",       "palette": "soft pink and white"},
    "🌿": {"scene": "lush greenery"},
    "🍃": {"scene": "leafy outdoor"},
    "🌾": {"scene": "wheat field, countryside"},
    "🎋": {"scene": "bamboo grove"},
    "🌋": {"scene": "volcanic landscape",          "lighting": "red-orange lava glow"},
    "🗻": {"scene": "Mount Fuji"},
    "🪐": {"scene": "outer space, planet rings"},
    "🌃": {"scene": "urban night",                 "lighting": "neon and city lights"},
    "🌙": {"lighting": "moonlight, soft lunar glow",        "scene": "night"},
    "🌑": {"lighting": "near-total darkness",               "scene": "deep night"},
    "🌅": {"lighting": "golden sunrise rays"},
    "🌇": {"lighting": "warm golden hour sunset"},
    "⚡": {"lighting": "dramatic lightning flashes",        "mood": "electric tension"},
    "🕯️": {"lighting": "warm intimate candlelight"},
    "🔥": {"lighting": "fire glow, warm dramatic light",    "palette": "warm reds and orange"},
    "✨": {"lighting": "glimmering sparkles, ethereal shimmer"},
    "💫": {"lighting": "shooting star, cosmic shimmer"},
    "🌌": {"lighting": "starfield glow",                   "scene": "cosmos, deep space"},
    "🌈": {"lighting": "post-rain diffuse light",           "palette": "full spectrum color"},
    "🌠": {"lighting": "shooting star, brief flash",        "mood": "fleeting wonder"},
    "❄️": {"palette": "cold blues and white",              "mood": "winter stillness"},
    "🌫️": {"lighting": "diffuse fog",                      "mood": "dreamlike ambiguity"},
    "🌺": {"palette": "vivid tropical colors"},
    "🪷": {"palette": "soft lavender and white",            "mood": "spiritual calm"},
    "💧": {"palette": "cool aqueous blues"},
    "😌": {"mood": "serene, peaceful contentment"},
    "🎭": {"mood": "theatrical drama, mysterious duality"},
    "🔮": {"mood": "mystical, prophetic tension"},
    "🪞": {"mood": "introspective, reflective"},
    "🗝️": {"mood": "secretive, antique mystery"},
    "🕸️": {"mood": "dark intricacy, fragile menace"},
    "🐚": {"mood": "nostalgic, oceanic memory"},
    "🎐": {"mood": "delicate, Japanese summer"},
    "🫧": {"mood": "ephemeral, iridescent"},
    "🩰": {"style": ["ballet", "elegant"]},
    "🦋": {"mood": "delicate transformation"},
}

_SLIDER_AXIS_FILL: dict[str, dict[int, dict[str, Any]]] = {
    "warm_cool": {
        -2: {"palette": "very warm amber and gold"},
        -1: {"palette": "warm golden tones"},
         1: {"palette": "cool blue tones"},
         2: {"palette": "very cold icy blue-white"},
    },
    "calm_dynamic": {
        -2: {"mood": "extremely still and serene", "action": "motionless, suspended in silence"},
        -1: {"mood": "calm and quiet"},
         1: {"mood": "dynamic and active"},
         2: {"mood": "intense kinetic energy", "composition": "diagonal dynamic lines"},
    },
    "dense_sparse": {
        -2: {"composition": "richly layered, dense foreground and background"},
         2: {"composition": "minimalist, vast empty space"},
    },
    "concrete_abstract": {
        -2: {"style": ["photorealistic", "highly_detailed"]},
         2: {"style": ["impressionistic", "abstract_art"]},
    },
}

# ── Person tags ───────────────────────────────────────────────────────────────

_PERSON_TAGS: dict[tuple[str, str], tuple[str, str]] = {
    ("girl", "1"):  ("1girl, solo",           "exactly 1 girl"),
    ("girl", "2"):  ("2girls",                "exactly 2 girls"),
    ("girl", "3+"): ("3girls, multiple_girls","3 or more girls"),
    ("girl", ""):   ("1girl",                 "at least 1 girl"),
    ("boy",  "1"):  ("1boy, solo",            "exactly 1 boy"),
    ("boy",  "2"):  ("2boys",                 "exactly 2 boys"),
    ("boy",  "3+"): ("3boys, multiple_boys",  "3 or more boys"),
    ("boy",  ""):   ("1boy",                  "at least 1 boy"),
    ("",     "1"):  ("solo",                  "exactly 1 person"),
    ("",     "2"):  ("2others",               "exactly 2 people"),
    ("",     "3+"): ("multiple_others",       "3 or more people"),
}


def _resolve_person(gender: str, count: str) -> tuple[str, str]:
    """Return (danbooru_tags, description), or ('', '') when neither is set."""
    key = (gender.strip().lower(), count.strip())
    return _PERSON_TAGS.get(key, ("", ""))


# ── Step 1: Slogan determination ──────────────────────────────────────────────

async def determine_slogan(
    user_text: str,
    emoji_codes: list[str],
    mood_sliders: dict,
    color_hex: list[str],
    ollama,
) -> str:
    """Return the creative theme/slogan. User text wins; VLM generates from mood signals when empty."""
    if user_text.strip():
        return user_text.strip()

    parts: list[str] = []
    if emoji_codes:
        meanings = [_EMOJI_MEANINGS.get(e, e) for e in emoji_codes]
        parts.append(f"Mood impressions: {', '.join(meanings)}")
    for ax, mapping in _SLIDER_DESCS.items():
        v = max(-2, min(2, int(mood_sliders.get(ax, 0))))
        d = mapping.get(v, "")
        if d:
            parts.append(d)
    if color_hex:
        parts.append(f"Colors: {', '.join(color_hex)}")

    if not parts:
        return ""

    slogan_prompt = (
        "You are a creative director. Based on the following mood signals, "
        "write a single vivid creative theme (1-2 sentences, Japanese or English) "
        "for an AI-generated image. Make it evocative and specific.\n\n"
        + "\n".join(parts)
        + "\n\nOutput ONLY the theme, no explanation, no quotes."
    )
    try:
        result = await ollama.generate_text(slogan_prompt)
        return result.strip()
    except Exception as e:
        logger.warning("determine_slogan LLM failed: %s", e)
        return ""


# ── Step 2a: Pre-fill axes from emoji/sliders/color ──────────────────────────

def pre_fill_axes(
    emoji_codes: list[str],
    mood_sliders: dict,
    color_hex: list[str],
) -> dict:
    """Build axis values directly from emoji/slider/color — no LLM involved."""
    prefilled: dict[str, Any] = {}

    for emoji in emoji_codes:
        for axis, val in _EMOJI_AXIS_FILL.get(emoji, {}).items():
            if axis == "style":
                lst = val if isinstance(val, list) else [val]
                existing = prefilled.get("style", [])
                prefilled["style"] = existing + [x for x in lst if x not in existing]
            elif axis in prefilled:
                prefilled[axis] = prefilled[axis] + ", " + val
            else:
                prefilled[axis] = val

    for slider, level_map in _SLIDER_AXIS_FILL.items():
        v = max(-2, min(2, int(mood_sliders.get(slider, 0))))
        for axis, contribution in level_map.get(v, {}).items():
            if axis == "style":
                lst = contribution if isinstance(contribution, list) else [contribution]
                existing = prefilled.get("style", [])
                prefilled["style"] = existing + [x for x in lst if x not in existing]
            elif axis in prefilled:
                prefilled[axis] = prefilled[axis] + ", " + contribution
            else:
                prefilled[axis] = contribution

    if color_hex:
        color_str = "accent colors: " + ", ".join(color_hex)
        existing = prefilled.get("palette", "")
        prefilled["palette"] = (existing + "; " + color_str).lstrip("; ") if existing else color_str

    return prefilled


# ── Step 2b: Build VLM completion prompt ─────────────────────────────────────

_ALL_AXES = [
    "subject",
    "character_detail",  # expression, eye direction, clothing, hairstyle, body build — empty when no person
    "action",
    "scene",
    "mood",
    "lighting",
    "composition",
    "style",
    "palette",
    "accessories",       # held/worn small items — empty when no person or not implied
]


def _build_completion_prompt(
    slogan: str,
    prefilled: dict,
    empty_axes: list[str],
    person_desc: str,
    character_hints: dict | None = None,
) -> str:
    person_present = bool(person_desc)
    character_hints = character_hints or {}

    lines: list[str] = ["You are an image scene architect.", ""]

    if slogan:
        lines += [
            "CREATIVE DIRECTIVE — everything in this image must serve this theme:",
            f'"{slogan}"',
            "",
        ]
    else:
        lines += ["Generate a compelling, complete scene of your own choosing.", ""]

    if person_desc:
        lines += [f"HARD REQUIREMENT: The image must include {person_desc}.", ""]

    if prefilled:
        lines.append("LOCKED axes (set by the user — output them exactly as shown, do not change):")
        for k, v in prefilled.items():
            if k == "style":
                lines.append(f"  style: [{', '.join(v)}]")
            else:
                lines.append(f"  {k}: {v}")
        lines.append("")

    if empty_axes:
        fill_list = [a for a in empty_axes if a not in ("style", "character_detail", "accessories")]
        if "style" in empty_axes:
            fill_list.append("style")
        if "character_detail" in empty_axes:
            fill_list.append("character_detail")
        if "accessories" in empty_axes:
            fill_list.append("accessories")
        lines += [
            "Fill in ONLY these empty axes (guided by the slogan and locked axes):",
            "  " + ", ".join(fill_list),
            "",
            "Rules:",
            "- action: the specific thing happening at this exact frozen instant (pose, gaze, gesture)",
            "- Keep each text value concise (5-15 words), except character_detail (see below)",
            "- style: 1-3 Danbooru-compatible style tags as a list",
            "",
        ]

    # ── Character detail requirements ──────────────────────────────────────────
    if person_present:
        lines += [
            "CHARACTER DETAIL REQUIREMENT — subject includes a person.",
            "You MUST fill character_detail with a compact comma-separated descriptor covering ALL of:",
            "  1. Expression: one specific Danbooru tag (e.g. smile, melancholic, pout, expressionless, blush)",
            "  2. Eye direction: e.g. looking_at_viewer, looking_away, downcast_eyes, looking_to_the_side",
            "  3. Clothing: one specific type (e.g. white_summer_dress, school_uniform, black_hoodie, kimono)",
            "  4. Hairstyle: hair length + texture/style (e.g. long_wavy_hair, short_bob, twin_tails, braided_hair)",
            "  5. Body build (optional): e.g. slender, petite, athletic — omit if not implied",
            "",
            "Fill accessories with comma-separated Danbooru tags for items the person wears or holds",
            "(e.g. hair_ribbon, tote_bag, glasses). Use empty string \"\" if nothing is implied.",
            "",
        ]
        if character_hints:
            lines.append("DANBOORU SUGGESTIONS — semantically relevant tags for this scene. Choose the most fitting:")
            for cat, tags in character_hints.items():
                if tags:
                    lines.append(f"  {cat}: [{', '.join(tags)}]")
            lines.append("Incorporate as many relevant suggestions as possible into character_detail, accessories, and danbooru_tags.")
            lines.append("")
    else:
        # No person — enrich environment instead
        lines += [
            "SCENE RICHNESS REQUIREMENT — no character present. Compensate with environmental density.",
            "  scene: be extremely specific — include architectural type or terrain, vegetation species,",
            "         weather state, time-of-day marker.",
            "         Example: \"abandoned victorian greenhouse, overgrown ferns, morning mist, cracked glass ceiling\"",
            "  mood: describe the emotional quality of the space itself (not a character feeling)",
            "  character_detail: \"\"   ← must be empty string, no person present",
            "  accessories: \"\"        ← must be empty string",
            "",
        ]
        if character_hints:
            lines.append("DANBOORU SCENE SUGGESTIONS — semantically relevant tags:")
            for cat, tags in character_hints.items():
                if tags:
                    lines.append(f"  {cat}: [{', '.join(tags)}]")
            lines.append("Use these to enrich scene, mood, and style axes.")
            lines.append("")

    lines += [
        "Output ONLY valid JSON with ALL 10 fields, no markdown fences, no explanation:",
        '{"subject":"...","character_detail":"...","action":"...","scene":"...","mood":"...","lighting":"...","composition":"...","style":[...],"palette":"...","accessories":"..."}',
    ]

    return "\n".join(lines)


# ── Step 2c: Parse LLM output and merge with prefilled values ─────────────────

def _parse_and_merge(raw: str, prefilled: dict) -> dict:
    """Parse LLM JSON and enforce prefilled values as non-overridable."""
    from .vocab_bank import _is_species_tag

    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        axes = json.loads(raw)
    except Exception as e:
        logger.warning("axis_decompose JSON parse failed: %s — raw: %.200s", e, raw)
        axes = {}

    # Normalize: ensure all 10 axes present as correct types
    for key in ("subject", "character_detail", "action", "scene", "mood",
                "lighting", "composition", "palette", "accessories"):
        if key not in axes or not isinstance(axes[key], str):
            axes[key] = prefilled.get(key, "")
    if not isinstance(axes.get("style"), list):
        pf_style = prefilled.get("style", [])
        axes["style"] = pf_style if pf_style else (
            [str(axes.get("style", ""))] if axes.get("style") else ["anime"]
        )

    # Apply prefilled OVER LLM output — user-derived values always win
    for key, val in prefilled.items():
        if key == "style":
            if isinstance(val, list) and val:
                llm_extra = [t for t in axes.get("style", []) if t not in val]
                axes["style"] = val + llm_extra
        elif val:
            axes[key] = val

    # Strip species/race tags from style axis — they contaminate all spirits
    axes["style"] = [t for t in axes["style"] if not _is_species_tag(t)] or ["anime"]

    return axes


# ── Main entry point ──────────────────────────────────────────────────────────

async def decompose_axes(
    ollama,
    user_intent: str = "",
    emoji_codes: list[str] | None = None,
    mood_sliders: dict | None = None,
    color_hex: list[str] | None = None,
    context_hint: str | None = None,
    person_gender: str = "",
    person_count: str = "",
    camera_shot: str = "",
    camera_angle: str = "",
    character_hints: dict | None = None,
) -> dict:
    emoji_codes = emoji_codes or []
    mood_sliders = mood_sliders or {}
    color_hex = color_hex or []

    # Step 1: Determine slogan (user text → direct; empty → VLM from mood signals)
    slogan = await determine_slogan(user_intent, emoji_codes, mood_sliders, color_hex, ollama)

    # Step 2a: Pre-fill axes from deterministic sources
    prefilled = pre_fill_axes(emoji_codes, mood_sliders, color_hex)

    # Person spec goes into subject as a locked value
    person_tags, person_desc = _resolve_person(person_gender, person_count)
    if person_tags:
        prefilled["subject"] = person_tags

    # Camera work: inject into composition as a locked value (overrides LLM)
    camera_tags = ", ".join(filter(None, [camera_shot, camera_angle]))
    if camera_tags:
        existing = prefilled.get("composition", "")
        prefilled["composition"] = (existing + ", " + camera_tags).lstrip(", ") if existing else camera_tags

    # Fall back to context_hint as slogan when no other creative direction exists
    effective_slogan = slogan or context_hint or ""

    # Step 2b: VLM fills only the empty axes, guided by the slogan
    empty_axes = [a for a in _ALL_AXES if a not in prefilled or not prefilled.get(a)]
    prompt = _build_completion_prompt(
        effective_slogan, prefilled, empty_axes, person_desc,
        character_hints=character_hints,
    )

    # Step 2c: Parse VLM output and apply prefilled values as overrides
    raw = await ollama.generate_text(prompt, fmt="json")
    axes = _parse_and_merge(raw, prefilled)

    axes["_slogan"] = slogan
    return axes

"""
Axis definitions for the Inversion feature.
Single source of truth shared by inspire.py (backend) and the /api/inspire/axes endpoint (frontend).
"""

# ── Axis registry ──────────────────────────────────────────────────────────────

AXIS_DEFINITIONS: dict[str, dict] = {
    "visual": {
        "label":       "Visual",
        "desc":        "Background, environment, lighting, color palette (not time/weather)",
        "prompt_desc": "visual: background environment, lighting color_palette (NOT time/weather, NOT physical place)",
        "icon":        "👁",
        "invert_hint": "bright↔dim, indoor↔outdoor, warm_palette↔cool_palette, plain↔elaborate_bg",
    },
    "time_weather": {
        "label":       "Time & Weather",
        "desc":        "Time of day, season, weather (day/night/rain/sunny/spring…)",
        "prompt_desc": "time_weather: time_of_day (day/night/morning/dusk), weather (rain/sunny/cloudy), season, celestial (moon/stars)",
        "icon":        "🌤",
        "invert_hint": "day↔night, morning↔dusk, sunny↔stormy, spring↔winter",
    },
    "emotion": {
        "label":       "Emotion",
        "desc":        "Character facial expression and feeling (smile/blush/crying/expressionless…)",
        "prompt_desc": "emotion: character facial expression and feeling (smile/blush/crying/expressionless/angry/nervous…)",
        "icon":        "😶",
        "invert_hint": "peaceful↔tense, happy↔melancholic, shy/blush↔bold/fierce, smile↔stoic_gaze",
    },
    "clothing": {
        "label":       "Clothing",
        "desc":        "Outfit, costume, accessories (hat, ribbon, gloves, jewelry, shoes…)",
        "prompt_desc": "clothing: outfit, costume, accessories (hat/ribbon/gloves/shoes/jewelry…)",
        "icon":        "👗",
        "invert_hint": "school_uniform↔battle_armor/gothic_dress, casual↔ceremonial/ornate",
    },
    "hair": {
        "label":       "Hair",
        "desc":        "Hair color, hairstyle, hair length and texture",
        "prompt_desc": "hair: hair_color, hairstyle, hair_length (twintails/ponytail/braid/bob_cut…)",
        "icon":        "💇",
        "invert_hint": "long↔short, light_color↔dark, straight↔curly/messy/wild, neat↔unkempt",
    },
    "style": {
        "label":       "Style",
        "desc":        "Art rendering, color density, linework, detail level",
        "prompt_desc": "style: art rendering, color density, detail level",
        "icon":        "🎨",
        "invert_hint": "vibrant↔muted, detailed↔minimal, soft↔harsh, colorful↔monochrome",
    },
    "location": {
        "label":       "Location",
        "desc":        "Physical place/setting (school/forest/ruins/castle/beach/city/shrine/cafe…)",
        "prompt_desc": "location: physical place (school/forest/ruins/castle/beach/city/shrine/cafe/dungeon/rooftop)",
        "icon":        "📍",
        "invert_hint": "school↔ruins/castle/wilderness, urban↔ancient, indoor↔outdoor, modern↔mythic",
    },
    "narrative": {
        "label":       "Narrative",
        "desc":        "Story genre/context (fantasy/sci-fi/historical/horror/magical elements)",
        "prompt_desc": "narrative: story genre/context (fantasy/sci-fi/historical/horror/magical elements)",
        "icon":        "🌐",
        "invert_hint": "slice_of_life↔epic_fantasy/war, peaceful↔crisis, mundane↔supernatural",
    },
    "action": {
        "label":       "Posture & Action",
        "desc":        "Character posture, movement, behavior (sitting/running/combat_stance…)",
        "prompt_desc": "action: character posture, movement, pose (sitting/standing/running/combat_stance/kneeling…)",
        "icon":        "🏃",
        "invert_hint": "sitting↔standing/combat_stance, relaxed↔tense/battle_ready, passive↔active",
    },
    "parts": {
        "label":       "Body Parts",
        "desc":        "Exposed or highlighted areas (bare_shoulders, collarbone, cleavage…)",
        "prompt_desc": "parts: exposed/highlighted body areas (bare_shoulders/collarbone/navel/cleavage/bare_legs…)",
        "icon":        "🫀",
        "invert_hint": "bare_shoulders→armored/covered, exposed navel→concealed torso",
    },
}

ALL_AXES: list[str] = list(AXIS_DEFINITIONS.keys())

# ── Alias map (old axis names → current names) ────────────────────────────────

AXIS_ALIAS_MAP: dict[str, str] = {
    "mood":        "emotion",
    "feeling":     "emotion",
    "expression":  "emotion",
    "posture":     "action",
    "pose":        "action",
    "movement":    "action",
    "body":        "parts",
    "body_parts":  "parts",
    "world":       "narrative",
    "genre":       "narrative",
    "story":       "narrative",
    "scenery":     "visual",
    "background":  "visual",
    "scene":       "visual",
    "environment": "visual",
    "place":       "location",
    "setting":     "location",
}


def normalize_axis(axis: str) -> str:
    """Convert an old or alias axis name to its canonical name."""
    return AXIS_ALIAS_MAP.get(axis.lower().strip(), axis.lower().strip())


def resolve_axes(requested: list[str]) -> list[str]:
    """Normalize and validate the axis list received from the frontend.
    Returns ALL_AXES if the list is empty or all entries are invalid."""
    if not requested:
        return list(ALL_AXES)
    normalized = [normalize_axis(a) for a in requested]
    valid = [a for a in normalized if a in AXIS_DEFINITIONS]
    return valid if valid else list(ALL_AXES)


# ── STEP1 classification prompt table ─────────────────────────────────────────

STEP1_AXIS_TABLE: str = "\n".join(
    f"{axis:<12} | {meta['desc']}"
    for axis, meta in AXIS_DEFINITIONS.items()
) + "\nfixed        | character count, body type, eye shape, props, composition — or anything that doesn't change"

# ── STEP2 inversion hints ─────────────────────────────────────────────────────

STEP2_INVERSION_HINTS: str = "\n".join(
    f"  - {axis}:{' ' * max(1, 14 - len(axis))}{meta['invert_hint']}"
    for axis, meta in AXIS_DEFINITIONS.items()
)

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# ── Species/race tag blocklist ────────────────────────────────────────────────
# Prevents vocab hints from injecting character-transforming species tags
# (dragon_girl, fox_girl, etc.) into Wander/Surge spirits when the user never
# asked for them. WD14 classifies these as category=0 (General), so they would
# otherwise pass the Qdrant filter and be injected as "guest" or "wild" tags.
#
# Users who explicitly specify a species tag in their prompt are unaffected —
# those tags enter via axis decomposition, not via vocab hints.

_SPECIES_PREFIXES = frozenset({
    "dragon", "fox", "cat", "dog", "wolf", "bunny", "rabbit", "deer",
    "cow", "horse", "tiger", "bear", "lion", "fish", "bird", "frog",
    "lizard", "snake", "spider", "bee", "slime", "ghost", "demon",
    "angel", "oni", "elf", "goblin", "orc", "fairy",
    "lamia", "harpy", "mermaid", "succubus", "vampire",
})

_SPECIES_EXACT = frozenset({
    "kemonomimi_mode", "monster_girl", "furry", "anthro",
    "kemono", "furry_female", "furry_male", "beastman",
    "animal_humanoid", "centaur",
})


def _is_species_tag(tag: str) -> bool:
    if tag in _SPECIES_EXACT:
        return True
    for suffix in ("_girl", "_boy"):
        if tag.endswith(suffix) and tag[: -len(suffix)] in _SPECIES_PREFIXES:
            return True
    return False

# Module-level cache so Qdrant is only queried once per process lifetime.
_vocab_count_cache: int | None = None


async def _get_vocab_count(db) -> int:
    global _vocab_count_cache
    if _vocab_count_cache is None:
        _vocab_count_cache = await db.count_wd14_vocab()
    return _vocab_count_cache


def invalidate_vocab_cache() -> None:
    """Call after a successful import to force re-check on next use."""
    global _vocab_count_cache
    _vocab_count_cache = None


async def _get_library_tag_freq(db) -> dict[str, int]:
    """Scroll WD14 tags from Qdrant and count frequency in user library."""
    freq: dict[str, int] = {}
    offset = None
    try:
        while True:
            from qdrant_client import models as qm
            points, next_offset = await db._qc.scroll(
                collection_name="images",
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["wd14_tags"]),
                with_vectors=False,
            )
            for p in points:
                tags = (p.payload or {}).get("wd14_tags") or []
                for t in tags:
                    freq[t] = freq.get(t, 0) + 1
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.warning("vocab_bank library scan failed: %s", e)
    return freq


async def get_axis_semantic_tags(
    db,
    ollama,
    axes: dict,
    limit: int = 30,
) -> list[str]:
    """Embed the full axis content and return semantically close Danbooru tags.

    Unlike get_vocab_hints() (which targets divergent tags for stranger/lunatic),
    this returns the closest-matching tags to help ALL spirits accurately express
    the user's intent in Danbooru vocabulary.
    Falls back to [] when WD14 vocab is not imported or on any error.
    """
    count = await _get_vocab_count(db)
    if count == 0:
        return []

    # Build query text from all non-private axes
    parts: list[str] = []
    for key, val in axes.items():
        if key.startswith("_"):
            continue
        if isinstance(val, list):
            parts.extend(val)
        elif isinstance(val, str) and val:
            parts.append(val)
    if not parts:
        return []

    query_text = " ".join(parts)
    try:
        vec = await ollama.embed(query_text)
    except Exception as e:
        logger.warning("axis_tag_hints embed failed: %s", e)
        return []

    try:
        hits = await db.search_wd14_vocab(
            vec, min_freq=0.01, max_freq=0.80, category=0, limit=limit * 3
        )
    except Exception as e:
        logger.warning("axis_tag_hints search failed: %s", e)
        return []

    # Collect tags already present in axes (character_detail, accessories, style, etc.)
    # so we don't redundantly re-inject them
    existing: set[str] = set()
    for key in ("character_detail", "accessories", "style"):
        val = axes.get(key, "")
        if isinstance(val, list):
            existing.update(t.strip().lower() for t in val if t.strip())
        elif isinstance(val, str):
            existing.update(t.strip().lower() for t in val.replace(",", " ").split() if t.strip())

    result: list[str] = []
    for h in hits:
        tag = h["name"]
        if _is_species_tag(tag):
            continue
        if tag.lower() in existing:
            continue
        result.append(tag)
        if len(result) >= limit:
            break

    return result


async def get_vocab_hints(
    db,
    ollama,
    axis_tags: list[str],
    stranger_count: int = 1,
    lunatic_count: int = 2,
    wildness: int = 1,
) -> dict[str, list[str]]:
    """Return {"stranger": [...], "lunatic": [...]} using Qdrant semantic search.

    Stranger: tags semantically related to the axis, medium Danbooru frequency.
    Lunatic: tags semantically distant from the axis, high Danbooru frequency,
             absent from the user's personal library.

    wildness (乱れ度) widens the pools:
      1 — default counts and frequency bands
      2 — lunatic pool widens to min_freq 0.10 and gets 3 wild tags
      3 — level 2 + one rare-band tag (freq 0.005–0.05) + 2 stranger guests

    Falls back to empty lists if vocab is not imported yet.
    """
    count = await _get_vocab_count(db)
    if count == 0:
        logger.warning(
            "WD14 vocab not imported — run POST /api/admin/invoke/import-wd14-vocab "
            "to enable stranger/lunatic tag hints"
        )
        return {"stranger": [], "lunatic": []}

    wildness = max(1, min(3, wildness))
    lunatic_min_freq = 0.40 if wildness == 1 else 0.10
    if wildness >= 2:
        lunatic_count = max(lunatic_count, 3)
    if wildness >= 3:
        stranger_count = max(stranger_count, 2)

    axis_set = {t.lower().replace(" ", "_") for t in axis_tags}
    axis_text = " ".join(axis_tags) or "general anime artwork"

    try:
        axis_vec = await ollama.embed(axis_text)
    except Exception as e:
        logger.warning("vocab_bank embed failed: %s", e)
        return {"stranger": [], "lunatic": []}

    lib_freq = await _get_library_tag_freq(db)

    # ── Stranger: semantically RELATED to axis, medium Danbooru frequency ──
    try:
        stranger_hits = await db.search_wd14_vocab(
            axis_vec, min_freq=0.04, max_freq=0.40, category=0, limit=40
        )
        # Filter out axis tags and prefer those in user library (medium presence)
        stranger_pool = [
            h for h in stranger_hits
            if h["name"] not in axis_set and not _is_species_tag(h["name"])
        ]
        # Sort by medium library presence (not too common, not absent)
        def _stranger_score(h):
            lc = lib_freq.get(h["name"], 0)
            return abs(lc - 3)  # prefer count ≈ 3 (medium)
        stranger_pool.sort(key=_stranger_score)
        stranger = [h["name"] for h in stranger_pool[:stranger_count]]
    except Exception as e:
        logger.warning("stranger hint failed: %s", e)
        stranger = []

    # ── Lunatic: high Danbooru frequency, absent from user library, DISTANT from axis ──
    try:
        lunatic_hits = await db.search_wd14_vocab(
            axis_vec, min_freq=lunatic_min_freq, max_freq=1.0, category=0, limit=200
        )
        # Filter: axis exclusion + absent from user library
        lunatic_pool = [
            h for h in lunatic_hits
            if h["name"] not in axis_set
            and lib_freq.get(h["name"], 0) <= 2
            and not _is_species_tag(h["name"])
        ]
        # Low score = semantically distant from axis = most "lunatic"
        lunatic_pool.sort(key=lambda h: h["score"])
        lunatic = [h["name"] for h in lunatic_pool[:lunatic_count]]
    except Exception as e:
        logger.warning("lunatic hint failed: %s", e)
        lunatic = []

    # ── Wildness 3: add one genuinely exotic rare-band tag ──
    if wildness >= 3:
        try:
            rare_hits = await db.search_wd14_vocab(
                axis_vec, min_freq=0.005, max_freq=0.05, category=0, limit=60
            )
            rare_pool = [
                h for h in rare_hits
                if h["name"] not in axis_set
                and h["name"] not in lunatic
                and lib_freq.get(h["name"], 0) <= 2
                and not _is_species_tag(h["name"])
            ]
            rare_pool.sort(key=lambda h: h["score"])
            if rare_pool:
                lunatic.append(rare_pool[0]["name"])
        except Exception as e:
            logger.warning("rare hint failed: %s", e)

    return {"stranger": stranger, "lunatic": lunatic}


# ── Character/scene Danbooru hint patterns ───────────────────────────────────

_EXPRESSION_EXACT = frozenset({
    "smile", "blush", "tears", "pout", "expressionless", "open_mouth", "closed_eyes",
    "winking", "frown", "smirk", "grin", "surprised", "shy", "embarrassed",
    "sad", "angry", "happy", "melancholic", "serious", "neutral",
})
_HAIR_SUFFIXES = ("_hair", "_braid", "_ponytail", "_bun", "_bangs", "twin_tails", "twintails",
                  "short_hair", "long_hair", "medium_hair", "very_long_hair")
_CLOTHING_WORDS = frozenset({
    "dress", "uniform", "skirt", "shirt", "jacket", "hoodie", "outfit", "suit",
    "coat", "blouse", "sweater", "kimono", "yukata", "swimsuit", "bikini",
    "school_uniform", "sailor_uniform", "maid", "cape", "cloak", "apron",
})
_POSE_EXACT = frozenset({
    "standing", "sitting", "lying", "crouching", "kneeling", "jumping",
    "looking_at_viewer", "looking_away", "looking_down", "looking_up",
    "looking_back", "looking_to_the_side", "from_above", "from_below", "from_side",
    "arms_behind_back", "hands_on_hips", "arms_up", "hand_on_own_face",
    "leaning_forward", "walking", "running",
})
_ACCESSORY_EXACT = frozenset({
    "earrings", "glasses", "ribbon", "necklace", "hat", "bag", "bracelet",
    "gloves", "bow", "hair_ribbon", "hair_ornament", "tiara", "crown",
    "scarf", "choker", "ring", "watch",
})
_SCENE_SUFFIXES = (
    "_sky", "_forest", "_mountain", "_city", "_room", "_building", "_garden",
    "_sea", "_ocean", "_lake", "_river", "scenery", "landscape", "outdoors",
    "indoors", "no_humans",
)


def _classify_hint_tag(tag: str) -> str | None:
    """Return category key for a tag, or None if not classifiable."""
    if tag in _EXPRESSION_EXACT or tag.endswith("_smile") or tag.endswith("_eyes"):
        return "expression"
    if any(tag.endswith(s) or tag == s for s in _HAIR_SUFFIXES):
        return "hair"
    # clothing: exact match or ends with a clothing word
    if tag in _CLOTHING_WORDS or any(tag.endswith(f"_{w}") or tag == w for w in _CLOTHING_WORDS):
        return "clothing"
    if tag in _POSE_EXACT or tag.startswith("looking_") or tag.startswith("from_") or tag.startswith("arms_") or tag.startswith("hand_"):
        return "pose"
    if tag in _ACCESSORY_EXACT:
        return "accessories"
    # scene カテゴリは character hints に含めない — slogan から axis decomposer が自由に生成すべきで
    # ここで先入れすると特定キーワードが全スピリットに固着する
    return None


async def get_character_danbooru_hints(
    db,
    ollama,
    slogan: str,
    person_present: bool,
    max_per_category: int = 4,
) -> dict[str, list[str]]:
    """Search Qdrant WD14 vocab for Danbooru tags relevant to the slogan.

    Returns dict keyed by category (expression/hair/clothing/pose/accessories/scene).
    Falls back to empty dict on any error (Qdrant not imported, embed failure, etc.).
    """
    count = await _get_vocab_count(db)
    if count == 0:
        return {}

    if person_present:
        query = f"{slogan} character expression clothing hairstyle pose accessories"
    else:
        query = f"{slogan} background environment scenery atmosphere detail"

    try:
        vec = await ollama.embed(query)
    except Exception as e:
        logger.debug("get_character_danbooru_hints embed failed: %s", e)
        return {}

    try:
        hits = await db.search_wd14_vocab(vec, min_freq=0.03, max_freq=0.75, category=0, limit=100)
    except Exception as e:
        logger.debug("get_character_danbooru_hints search failed: %s", e)
        return {}

    result: dict[str, list[str]] = {}
    for h in hits:
        tag = h["name"]
        if _is_species_tag(tag):
            continue
        cat = _classify_hint_tag(tag)
        if cat is None:
            continue
        bucket = result.setdefault(cat, [])
        if len(bucket) < max_per_category:
            bucket.append(tag)

    return result


async def get_recent_adopted_tags(db, days: int = 7, limit: int = 200) -> dict[str, int]:
    """Return WD14 tag frequency from images adopted in the last N days."""
    import time
    from qdrant_client import models as qm

    cutoff = time.time() - days * 86400
    freq: dict[str, int] = {}
    offset = None
    try:
        while True:
            points, next_offset = await db._qc.scroll(
                collection_name="images",
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="genesis.adopted_at_genesis", match=qm.MatchValue(value=True)),
                    qm.FieldCondition(key="mtime", range=qm.Range(gte=cutoff)),
                ]),
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["wd14_tags"]),
                with_vectors=False,
            )
            for p in points:
                tags = (p.payload or {}).get("wd14_tags") or []
                for t in tags[:limit]:
                    freq[t] = freq.get(t, 0) + 1
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.warning("get_recent_adopted_tags failed: %s", e)
    return freq


# ── Pro mode: お題ドリブン WD14 タグ取得 ────────────────────────────────────

_PRO_SECTION_ORDER = ("character", "background", "props", "action", "mood", "camera")


def _section_pairs(pro_sections: dict | None) -> list[tuple[str, str]]:
    """pro_sections から (section_name, value) ペアを定義済み順で返す。空値はスキップ。"""
    if not pro_sections:
        return []
    return [
        (sect, v) for sect in _PRO_SECTION_ORDER
        if (v := (pro_sections.get(sect) or "").strip())
    ]


async def get_topic_tags(
    db,
    ollama,
    topic: str,
    pro_sections: dict | None = None,
    limit: int = 25,
) -> list[str]:
    """お題テキスト + sections から WD14 ベクトル検索し、VLM でお題に特徴的なタグを返す。

    limit=25 で返すことでスピリット別にティア分配できる（上位がコア、下位が発散的）。
    Falls back to raw candidates when VLM call fails.
    Returns [] when WD14 vocab is not imported.
    """
    count = await _get_vocab_count(db)
    if not topic or count == 0:
        return []

    pairs = _section_pairs(pro_sections)
    query_parts = [topic] + [v for _, v in pairs]

    try:
        vec = await ollama.embed(" ".join(query_parts))
    except Exception as e:
        logger.warning("get_topic_tags embed failed: %s", e)
        return []

    try:
        hits = await db.search_wd14_vocab(
            vec, min_freq=0.01, max_freq=0.80, category=0, limit=limit * 2
        )
    except Exception as e:
        logger.warning("get_topic_tags search failed: %s", e)
        return []

    candidates = [h["name"] for h in hits if not _is_species_tag(h["name"])]
    if not candidates:
        return []

    sections_block = (
        "\nSection hints:\n" + "\n".join(f"  {sect}: {v}" for sect, v in pairs)
    ) if pairs else ""

    prompt = (
        f"Topic: {topic}{sections_block}\n"
        f"Candidate Danbooru tags: [{', '.join(candidates)}]\n\n"
        f"Select up to {limit} tags that are SPECIFICALLY CHARACTERISTIC of this topic. "
        f"Exclude generic scene-setting tags that appear in almost any image of this type "
        f"(e.g. indoors, outdoors, wooden_floor, stone_wall, grass, sky — unless they are "
        f"a defining feature of this specific topic). "
        f"Prefer tags that distinguish this topic from superficially similar ones.\n"
        f'Output ONLY a JSON array: ["tag1", "tag2", ...]'
    )

    try:
        raw = await ollama.generate_text(prompt, fmt="json")
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        selected = json.loads(raw)
        if isinstance(selected, list):
            result = [t for t in selected if isinstance(t, str) and t.strip()]
            if result:
                logger.debug("get_topic_tags: %d → %d tags", len(candidates), len(result))
                return result[:limit]
    except Exception as e:
        logger.warning("get_topic_tags VLM filter failed: %s", e)

    return candidates[:limit]


async def synthesize_slogan(
    topic: str,
    pro_sections: dict | None,
    topic_tags: list[str],
    ollama,
) -> str:
    """お題・sections・filtered WD14 tags から vivid なスローガンを 1-2 文で生成。

    VLM 呼び出しが失敗した場合は topic をそのまま返す。
    """
    lines: list[str] = [
        "You are a creative director for anime illustrations.",
        "Based on the following inputs, write ONE vivid creative theme (1-2 sentences, Japanese or English).",
        "The theme should capture the visual essence — be evocative and specific.",
        "",
        f"Topic: {topic}",
    ]
    for sect, v in _section_pairs(pro_sections):
        lines.append(f"{sect}: {v}")
    if topic_tags:
        lines.append(f"Related visual elements: {', '.join(topic_tags)}")
    lines += ["", "Output ONLY the theme sentence. No quotes, no explanation."]

    try:
        result = await ollama.generate_text("\n".join(lines))
        slogan = result.strip()
        if slogan:
            logger.debug("synthesize_slogan: %r", slogan[:80])
            return slogan
    except Exception as e:
        logger.warning("synthesize_slogan failed: %s", e)

    return topic


async def expand_pro_prompt(
    prompt: str,
    topic: str,
    pro_sections: dict | None,
    ollama,
) -> dict:
    """お題 × ユーザープロンプトからストーリー指令と追加タグを生成する。

    ユーザーのタグはそのまま維持し、スピリットがそれを基にストーリーを
    肉付けするための指針を作成する。

    返り値:
        slogan: 1-2 文: お題と pro_prompt が融合した視覚的テーマ
        story_directive: 3-4 文: お題×pro_prompt が生み出すシーン・感情・ドラマ
        supplement_tags: story を補完する追加 Danbooru タグのリスト (5-15 個)
        scene_anchor: 50 words 以上・2-3 短文のシーン記述

    LLM 失敗時は prompt をそのまま使うフォールバックを返す。
    """
    lines: list[str] = [
        "You are a story director for AI anime image generation.",
        "The user has already written their Danbooru tag list (the BASE TAGS).",
        "Your task: given the BASE TAGS and a TOPIC, craft a narrative story directive",
        "and suggest supplementary Danbooru tags that develop a story around the base.",
        "",
    ]

    if topic:
        lines += [
            f"TOPIC (overarching theme — all output MUST be consistent with it): {topic}",
            "",
        ]

    for sect, v in _section_pairs(pro_sections):
        lines.append(f"{sect} hint: {v}")
    if pro_sections:
        lines.append("")

    lines += [
        f"BASE TAGS (user's Danbooru tag list — treat as given, do NOT alter): {prompt}",
        "",
        "RULES — violating any of these is an error:",
        "1. TOPIC ANCHOR: All output must be consistent with the topic.",
        "2. story_directive: Write 3-4 sentences of narrative context describing the scene,",
        "   emotion, and dramatic moment that the BASE TAGS inhabit within the topic.",
        "   This is prose for the spirit — NOT a tag list.",
        "3. supplement_tags: 5-15 Danbooru tags that ADD to the story.",
        "   Do NOT repeat tags already in BASE TAGS.",
        "   Cover: scene/environment, atmosphere, lighting, mood effects, props from the story.",
        "   NEVER use abstract adjectives — only concrete Danbooru tag names.",
        "4. scene_anchor: at least 50 words, 2-3 concrete short sentences about the environment,",
        "   lighting quality, and atmosphere of the story scene.",
        "",
        "Output ONLY valid JSON, no markdown fences:",
        '{"slogan":"<1-2 sentence vivid thematic directive fusing topic and BASE TAGS>","story_directive":"<3-4 sentence narrative context for the spirit>","supplement_tags":["tag1","tag2",...],"scene_anchor":"<50+ words, 2-3 concrete short sentences>"}',
    ]

    _fallback = {
        "slogan": topic or prompt,
        "story_directive": "",
        "supplement_tags": [],
        "scene_anchor": "",
    }

    try:
        raw = await ollama.generate_text("\n".join(lines), fmt="json")
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        result = json.loads(raw)
        if isinstance(result, dict) and result.get("slogan"):
            logger.debug("expand_pro_prompt: slogan=%r", str(result["slogan"])[:80])
            return {
                "slogan":          str(result.get("slogan", topic or prompt)).strip() or (topic or prompt),
                "story_directive": str(result.get("story_directive", "")).strip(),
                "supplement_tags": [t for t in result.get("supplement_tags", []) if isinstance(t, str)],
                "scene_anchor":    str(result.get("scene_anchor", "")).strip(),
            }
    except Exception as e:
        logger.warning("expand_pro_prompt failed: %s", e)

    return _fallback


# ── Pro mode: axis_tag_hints の VLM 精査 (将来用) ────────────────────────────

async def refine_axis_tag_hints(
    raw_hints: list[str],
    axes: dict,
    pro_sections: dict | None,
    ollama,
    target: int = 12,
) -> list[str]:
    """VLM で候補タグを精査し、ユーザー意図と整合する上位タグだけ返す。

    raw_hints が空か ollama が None の場合はそのまま返す。
    VLM 呼び出しが失敗した場合も raw_hints をフォールバックとして返す。
    """
    if not raw_hints or not ollama:
        return raw_hints

    # 軸サマリー（プロンプトに収める）
    axis_lines: list[str] = []
    for k in ("subject", "character_detail", "action", "scene",
              "mood", "lighting", "style", "accessories", "palette"):
        v = axes.get(k, "")
        if isinstance(v, list):
            v = ", ".join(v)
        if v:
            axis_lines.append(f"  {k}: {v}")

    # Pro セクション（空でない場合のみ）
    section_lines: list[str] = []
    if pro_sections:
        for sect in ("character", "background", "props", "action"):
            v = (pro_sections.get(sect) or "").strip()
            if v:
                section_lines.append(f"  {sect}: {v}")

    lines: list[str] = [
        "You are a Danbooru tag curator for an anime image prompt.",
        "",
        "Creative axes (these are ALREADY in the prompt — do NOT re-select them):",
        *axis_lines,
    ]
    if section_lines:
        lines += [
            "",
            "User's section requests (selected tags must be consistent with these):",
            *section_lines,
        ]
    lines += [
        "",
        f"Candidate tags from semantic search ({len(raw_hints)} total):",
        f"  [{', '.join(raw_hints)}]",
        "",
        f"Select up to {target} tags that:",
        "1. Are NOT already expressed by the axes above",
        "2. Are consistent with the user's section requests",
        "3. Add specific visual detail the axes do not already cover",
        "4. Exclude overly generic or irrelevant tags",
        "",
        'Output ONLY a JSON array of selected tag strings: ["tag1", "tag2", ...]',
    ]
    prompt = "\n".join(lines)

    try:
        raw = await ollama.generate_text(prompt, fmt="json")
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        selected = json.loads(raw)
        if isinstance(selected, list):
            result = [t for t in selected if isinstance(t, str) and t.strip()]
            if result:
                logger.debug("refine_axis_tag_hints: %d → %d tags", len(raw_hints), len(result))
                return result
    except Exception as e:
        logger.warning("refine_axis_tag_hints VLM failed: %s", e)

    return raw_hints


# ── Emotion register (12 dimensions shared with the emotion tagger) ──────────

_EMOTION_QUERIES = {
    "loneliness": "loneliness solitude isolated empty distant quiet alone",
    "nostalgia":  "nostalgia bittersweet memory faded old times sepia longing",
    "ephemeral":  "ephemeral fleeting transient fragile passing moment petals",
    "melancholy": "melancholy sorrow wistful gloomy rain grey subdued",
    "serenity":   "serenity calm peaceful still tranquil gentle quiet morning",
    "wonder":     "wonder awe marvel vast breathtaking grand celestial",
    "joy":        "joy happy cheerful bright playful laughter sunny",
    "tension":    "tension suspense unease sharp dramatic edge storm",
    "warmth":     "warmth cozy soft tender comfort golden hearth",
    "mystery":    "mystery enigmatic hidden shadow secret fog veiled",
    "desolation": "desolation ruin abandoned decay barren wasteland dust",
    "vitality":   "vitality energy dynamic vivid alive motion bloom",
}


async def get_emotion_hints(db, ollama, emotion: str, n_tags: int = 6) -> list[str]:
    """Return Danbooru mood/lighting tags semantically close to an emotion dimension.

    Returns [] for unknown emotions, when the vocab bank is not imported, or on error.
    """
    query = _EMOTION_QUERIES.get(emotion)
    if not query:
        return []
    count = await _get_vocab_count(db)
    if count == 0:
        return []

    try:
        vec = await ollama.embed(query)
        hits = await db.search_wd14_vocab(vec, min_freq=0.01, max_freq=0.6, category=0, limit=n_tags * 4)
    except Exception as e:
        logger.warning("get_emotion_hints failed for %s: %s", emotion, e)
        return []

    return [h["name"] for h in hits if not _is_species_tag(h["name"])][:n_tags]


# ── Echoes of Resonance ────────────────────────────────────────────────────────

_CHARACTER_KEYWORDS = frozenset({
    "_hair", "_eyes", "dress", "uniform", "outfit", "shirt", "skirt", "school",
    "jacket", "coat", "blouse", "sweater", "hoodie", "kimono", "yukata", "leotard",
    "bikini", "swimsuit", "hat", "ribbon", "bow", "braid", "twintail", "ponytail",
    "short_hair", "long_hair", "medium_hair",
})
_SCENE_KEYWORDS = frozenset({
    "room", "street", "forest", "beach", "city", "garden", "sky", "water",
    "building", "park", "school", "library", "cafe", "bar", "mountain", "ocean",
    "river", "lake", "field", "house", "rooftop", "corridor", "hallway", "bridge",
    "train", "station", "window", "door", "indoor", "outdoor",
})


def _classify_resonance_tag(tag: str) -> str:
    """Classify a wd14 tag into character/scene/mood hint category."""
    for kw in _CHARACTER_KEYWORDS:
        if kw in tag:
            return "character"
    for kw in _SCENE_KEYWORDS:
        if kw in tag:
            return "scene"
    return "mood"


async def _taste_centroid(db) -> list[float] | None:
    """Weighted embedding centroid of high-rated (≥4★) images (star4=1.0, star5=2.0).

    Returns the normalized centroid vector, or None when no starred images exist.
    """
    import math
    from qdrant_client import models as qm

    # Scroll all images rated ≥4 and retrieve their full embeddings
    points: list = []
    offset = None
    try:
        while True:
            pts, next_offset = await db._qc.scroll(
                collection_name="images",
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(
                        key="star_rating",
                        range=qm.Range(gte=4),
                    ),
                    qm.FieldCondition(
                        key="embedding_status",
                        match=qm.MatchValue(value="done"),
                    ),
                ]),
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["star_rating"]),
                with_vectors=["embedding"],
            )
            points.extend(pts)
            if next_offset is None or len(points) >= 500:
                break
            offset = next_offset
    except Exception as e:
        logger.warning("_taste_centroid scroll failed: %s", e)
        return None

    if not points:
        return None

    dim: int | None = None
    centroid: list[float] = []
    total_weight = 0.0

    for p in points:
        vec = p.vector.get("embedding") if isinstance(p.vector, dict) else None
        if not vec:
            continue
        rating = (p.payload or {}).get("star_rating", 4)
        weight = 2.0 if rating >= 5 else 1.0
        if dim is None:
            dim = len(vec)
            centroid = [0.0] * dim
        if len(vec) != dim:
            continue
        for i, v in enumerate(vec):
            centroid[i] += v * weight
        total_weight += weight

    if total_weight == 0.0 or dim is None:
        return None

    centroid = [v / total_weight for v in centroid]
    norm = math.sqrt(sum(v * v for v in centroid))
    if norm > 0.0:
        centroid = [v / norm for v in centroid]
    return centroid


async def compute_resonance_hints(db, *, n_tags: int = 20) -> dict[str, list[str]]:
    """Compute aesthetic taste hints from high-rated (≥4★) images.

    Searches wd14_vocab for the n_tags nearest tags to the taste centroid and
    returns them classified as character_hints compatible with decompose_axes().
    Returns {} when no starred images exist or wd14_vocab is empty.
    """
    vocab_count = await _get_vocab_count(db)
    if vocab_count == 0:
        return {}

    centroid = await _taste_centroid(db)
    if centroid is None:
        return {}

    # Find nearest wd14 tags to the centroid
    raw_tags = await db.search_wd14_vocab(
        centroid,
        min_freq=0.005,
        max_freq=0.8,
        category=0,
        limit=n_tags,
    )
    if not raw_tags:
        return {}

    # Classify into hint categories
    hints: dict[str, list[str]] = {"character": [], "scene": [], "mood": []}
    for entry in raw_tags:
        cat = _classify_resonance_tag(entry["name"])
        hints[cat].append(entry["name"])

    logger.debug(
        "compute_resonance_hints: %d tags (c=%d s=%d m=%d)",
        len(raw_tags),
        len(hints["character"]), len(hints["scene"]), len(hints["mood"]),
    )
    return {k: v for k, v in hints.items() if v}


async def compute_frontier_hints(db, *, n_tags: int = 20) -> dict[str, list[str]]:
    """Mirror of compute_resonance_hints: tags the user's library has never touched.

    Searches wd14_vocab near the taste centroid with a wide net, keeps only tags
    absent from the library, and sorts ASCENDING by score so the semantically
    farthest tags from the user's taste come first (same trick as lunatic hints).
    Returns {} when no starred images exist or wd14_vocab is empty.
    """
    vocab_count = await _get_vocab_count(db)
    if vocab_count == 0:
        return {}

    centroid = await _taste_centroid(db)
    if centroid is None:
        return {}

    try:
        hits = await db.search_wd14_vocab(
            centroid,
            min_freq=0.02,
            max_freq=0.6,
            category=0,
            limit=300,
        )
    except Exception as e:
        logger.warning("compute_frontier_hints search failed: %s", e)
        return {}
    if not hits:
        return {}

    lib_freq = await _get_library_tag_freq(db)
    pool = [
        h for h in hits
        if lib_freq.get(h["name"], 0) == 0 and not _is_species_tag(h["name"])
    ]
    # Low score = far from the taste centroid = most "frontier"
    pool.sort(key=lambda h: h["score"])

    hints: dict[str, list[str]] = {"character": [], "scene": [], "mood": []}
    taken = 0
    for entry in pool:
        if taken >= n_tags:
            break
        cat = _classify_resonance_tag(entry["name"])
        hints[cat].append(entry["name"])
        taken += 1

    logger.debug(
        "compute_frontier_hints: %d candidates → %d tags (c=%d s=%d m=%d)",
        len(pool), taken,
        len(hints["character"]), len(hints["scene"]), len(hints["mood"]),
    )
    return {k: v for k, v in hints.items() if v}

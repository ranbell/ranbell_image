"""Tests for the Chronicle story pipeline (prompt builders and parsers).

Covers:
  - parse_story_sections(): marker splitting incl. TITLE/OVERALL, missing acts
  - build_story_prompt(): base-axis anchoring, worldview, time scale, mutation tags
  - build_axis_prompt(): Visual Script guide, prompt_style variants, identity source
  - build_translation_to_english_prompt(): user-locale → English before Stage 3
  - build_vision_prompt(): full vs. tags-assisted extraction
  - character_tags_from_wd14(): meta-tag filtering
  - remove_conflict_tags(): tag-line filtering, prose preservation
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    _BRIGHT_MODE_KEYS,
    _DARK_MODE_KEYS,
    _candidate_beats_degenerate,
    _chronicle_tags_degenerate,
    _coherence_hierarchy_block,
    _dramatic_mode_line,
    _elapsed_time_header,
    _ending_policy_block,
    _text_similarity,
    _tone_line,
    acts_temporally_distinct,
    apply_scene_constraints,
    assign_dramatic_modes,
    apply_timetable_slot_marks,
    axis_slots_ready,
    bind_timetable_axis_slots,
    build_axis_prose_prompt_lean,
    situation_from_axis_slots,
    timetable_neighbors,
    visual_plan_to_tags,
    candidates_ungrounded,
    candidates_off_topic,
    topic_anchor_tokens,
    chunk_list,
    filter_story_seed_pool,
    find_identity_mutex_conflicts,
    find_mutex_conflict_tags,
    infer_axis_scene_constraints,
    merge_draft_wd14_tags,
    activities_temporally_distinct,
    axis_slots_collapsed,
    build_differentiate_activities_prompt,
    parse_candidates_json,
    sample_bio_domains,
    should_differentiate_acts,
    should_use_draft_refine,
    translation_values_complete,
    base_pose_tags,
    build_differentiate_acts_prompt,
    build_topic_directive_prompt,
    candidates_degenerate,
    build_axis_prompt,
    build_axis_prose_prompt,
    build_axis_tags_prompt,
    build_candidates_prompt,
    build_expand_prompt,
    build_overall_prompt,
    build_story_prompt,
    build_story_repair_prompt,
    build_timetable_prompt,
    build_story_tags_prompt,
    build_title_prompt,
    build_translation_to_english_prompt,
    build_vision_prompt,
    build_visual_examination_prompt,
    build_biography_prompt,
    build_concrete_activities_prompt,
    build_json_translation_prompt,
    character_tags_from_wd14,
    classify_identity_tag,
    collect_prompt_tags,
    identity_lock_tags,
    identity_tags_for_scale,
    parse_biography_json,
    parse_concrete_activities_json,
    parse_timetable_json,
    inject_identity_tags,
    is_multi_character,
    parse_axis_tags_json,
    parse_english_translation_json,
    parse_story_json,
    parse_story_sections,
    parse_tags_json,
    parse_visual_plan_json,
    remove_conflict_tags,
    split_vision_sections,
)


# ── parse_story_sections ──────────────────────────────────────────────────────

def test_parse_story_sections_full():
    raw = (
        "Here is the chronicle.\n"
        "[TITLE]\nThe Clockwork Ascent\n"
        "[OVERALL]\nA foundling rises to command the skies.\n"
        "[PAST]\nShe was a foundling in the lower city.\n\n"
        "[PRESENT]\nShe stands on the airship deck.\n"
        "[FUTURE]\nShe will command the fleet."
    )
    result = parse_story_sections(raw)
    assert result["title"] == "The Clockwork Ascent"
    assert result["overall"] == "A foundling rises to command the skies."
    assert result["past"] == "She was a foundling in the lower city."
    assert result["present"] == "She stands on the airship deck."
    assert result["future"] == "She will command the fleet."


def test_parse_story_sections_missing_title_overall():
    raw = "[PAST]\nonly past\n[PRESENT]\nand present"
    result = parse_story_sections(raw)
    assert result["title"] == ""
    assert result["overall"] == ""
    assert result["past"] == "only past"
    assert result["present"] == "and present"
    assert result["future"] == ""


def test_parse_story_sections_case_insensitive():
    raw = "[Title]\nT\n[past]\na\n[Present]\nb\n[FUTURE]\nc"
    result = parse_story_sections(raw)
    assert result["title"] == "T"
    assert result["past"] == "a"
    assert result["present"] == "b"
    assert result["future"] == "c"


def test_parse_story_sections_title_single_line():
    raw = '[TITLE]\n"Two Lines"\nextra junk\n[PAST]\na\n[PRESENT]\nb\n[FUTURE]\nc'
    assert parse_story_sections(raw)["title"] == "Two Lines"


def test_parse_story_sections_empty():
    result = parse_story_sections("no markers at all")
    assert all(v == "" for v in result.values())


def test_parse_story_sections_markdown_variants():
    # Bold bracket markers and colon-form headers (real-world LLM sloppiness)
    raw = (
        "**[TITLE]** The Iron Garden\n"
        "**OVERALL:** An arc of rust and bloom.\n"
        "## PAST:\nShe tended machines.\n"
        "**Present:** She tends flowers.\n"
        "FUTURE: The garden tends itself."
    )
    result = parse_story_sections(raw)
    assert result["title"] == "The Iron Garden"
    assert result["overall"] == "An arc of rust and bloom."
    assert result["past"] == "She tended machines."
    assert result["present"] == "She tends flowers."
    assert result["future"] == "The garden tends itself."


def test_parse_story_sections_prose_words_not_markers():
    # "past"/"present" inside prose must not be mistaken for section markers
    raw = (
        "[PAST]\nIn the past she walked here, present in every memory.\n"
        "[PRESENT]\nb\n[FUTURE]\nc"
    )
    result = parse_story_sections(raw)
    assert "present in every memory" in result["past"]
    assert result["present"] == "b"


# ── repair pass ───────────────────────────────────────────────────────────────

def test_build_story_repair_prompt():
    prompt = build_story_repair_prompt("broken output text")
    assert "broken output text" in prompt
    assert '"title"' in prompt and '"future"' in prompt


def test_parse_story_json():
    raw = ('{"title": "T", "overall": "O", "past": "p", '
           '"present": "n", "future": "f"}')
    result = parse_story_json(raw)
    assert result["title"] == "T" and result["future"] == "f"
    wrapped = parse_story_json('```json\n{"past": "p"}\n```')
    assert wrapped["past"] == "p" and wrapped["title"] == ""
    broken = parse_story_json("nope")
    assert all(v == "" for v in broken.values())


def test_build_title_prompt():
    prompt = build_title_prompt({"past": "p", "present": "n", "future": "f"})
    assert "PAST: p" in prompt
    assert "NEVER generic" in prompt


def test_build_overall_prompt():
    prompt = build_overall_prompt("The Iron Garden", {"past": "p", "present": "n", "future": "f"})
    assert "PAST: p" in prompt
    assert "TITLE: The Iron Garden" in prompt
    assert "2-4 sentence" in prompt


# ── build_story_prompt ────────────────────────────────────────────────────────

def test_build_story_prompt_base_axis_anchored():
    prompt = build_story_prompt(
        character_desc="danbooru tags: 1girl, silver_hair",
        scene_desc="SCENE: airship deck at dusk",
        base_axis="present",
        worldview="steampunk",
    )
    assert "THE PRESENT looks exactly like this scene" in prompt
    assert '"steampunk"' in prompt
    for marker in ("[TITLE]", "[OVERALL]", "[PAST]", "[PRESENT]", "[FUTURE]"):
        assert marker in prompt


def test_build_story_prompt_empty_worldview():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="past", worldview="  ",
    )
    assert "invent a fitting, evocative world" in prompt


def test_build_story_prompt_time_scale():
    minutes = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="minutes",
    )
    assert "TIME CONTRACT" in minutes
    assert "a few minutes" in minutes
    assert "FORBIDDEN" in minutes
    tens = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="tens_of_minutes",
    )
    assert "tens of minutes" in tens
    assert "FORBIDDEN" in tens
    hours = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="hours",
    )
    assert "outfit" in hours  # hours still constrains the outfit
    decades = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="decades",
    )
    assert "several decades" in decades
    assert "everything" in decades  # decade may_differ
    # unknown scale falls back to years
    fallback = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="bogus",
    )
    assert "a few years" in fallback


def test_build_story_prompt_mutation_tags():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", mutation_tags=["ruins", "bioluminescence"],
    )
    assert "ruins, bioluminescence" in prompt
    no_mutation = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    assert "Unexpected elements" not in no_mutation


def test_build_story_prompt_user_topic_hard_constraint():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", user_topic="最後は花畑にたどり着く",
    )
    # coherence hierarchy tells the LLM which anchor wins
    assert "COHERENCE HIERARCHY" in prompt
    # user topic block reaches the model verbatim
    assert "最後は花畑にたどり着く" in prompt
    assert "USER TOPIC" in prompt
    # base image lock present regardless of topic (time contract)
    assert "IS the base image" in prompt
    # empty topic → intent block is skipped
    no_topic = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    assert "USER TOPIC" in no_topic  # hierarchy still names the slot
    assert "highest constraint" not in no_topic  # but the details block is gone


def test_base_pose_tags():
    tags = [
        "1girl", "long_hair", "blonde_hair", "sitting", "looking_at_viewer",
        "hand_on_own_face", "smile", "indoors",
    ]
    result = base_pose_tags(tags)
    # picks up pose + framing tags; drops appearance/scene ones
    assert "sitting" in result
    assert "looking_at_viewer" in result
    assert "hand_on_own_face" in result
    assert "long_hair" not in result and "indoors" not in result
    # empty in → empty out
    assert base_pose_tags([]) == []


# ── build_axis_prompt ─────────────────────────────────────────────────────────

def test_build_axis_prompt_visual_script_structure():
    prompt = build_axis_prompt(
        story_text="She walks the ruins.",
        character_tags=["1girl", "silver_hair"],
        character_desc="",
        prompt_style="danbooru+natural",
        wd14_context="[common tags] 1girl, silver_hair",
        time_scale="years",
        axis="past",
        base_axis="present",
    )
    # 5-paragraph internal guide
    for para in ("APPEARANCE", "ACTION", "ENVIRONMENT", "DETAIL", "MOOD"):
        assert para in prompt
    assert "5 flowing paragraphs" in prompt
    # POSITIVE/NEGATIVE labeled output, not JSON
    assert "POSITIVE:" in prompt and "NEGATIVE:" in prompt
    assert '{"positive"' not in prompt
    # Non-base axis: the base image's WD14 tags are dropped entirely (they
    # describe a different moment) — the story text is the primary source.
    assert "[common tags] 1girl, silver_hair" not in prompt


def test_build_axis_prompt_styles():
    kwargs = dict(
        story_text="She walks the ruins.",
        character_tags=["1girl", "silver_hair"],
        character_desc="",
        time_scale="years",
        axis="past",
        base_axis="present",
    )
    tags_only = build_axis_prompt(prompt_style="danbooru", **kwargs)
    assert "comma-separated danbooru tag list (12-20 tags" in tags_only
    assert "No prose" in tags_only
    natural = build_axis_prompt(prompt_style="natural", **kwargs)
    assert "5-paragraph Visual Script prose" in natural
    both = build_axis_prompt(prompt_style="danbooru+natural", **kwargs)
    assert "two parts separated by a blank line" in both


def test_build_axis_prompt_identity_source():
    with_tags = build_axis_prompt(
        story_text="s", character_tags=["1girl", "red_eyes"],
        character_desc="ignored", prompt_style="danbooru",
        time_scale="years", axis="past", base_axis="present",
    )
    assert "1girl, red_eyes" in with_tags
    without_tags = build_axis_prompt(
        story_text="s", character_tags=[],
        character_desc="a girl with red eyes", prompt_style="danbooru",
        time_scale="years", axis="past", base_axis="present",
    )
    assert "a girl with red eyes" in without_tags


def test_build_axis_prompt_temporal_context_decades():
    prompt = build_axis_prompt(
        story_text="She stands in the ruins of her childhood home.",
        character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="decades",
        axis="past", base_axis="present",
    )
    assert "TEMPORAL CONSTRAINT" in prompt
    assert "ABSOLUTE" in prompt
    assert "FORBIDDEN" in prompt
    assert "everything" in prompt  # decades may_differ = "everything"


def test_build_axis_prompt_temporal_context_minutes():
    prompt = build_axis_prompt(
        story_text="She reaches for the door handle.",
        character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="minutes",
        axis="future", base_axis="present",
    )
    assert "TEMPORAL CONSTRAINT" in prompt
    assert "IDENTICAL" in prompt
    assert "FORBIDDEN" in prompt


def test_build_axis_prompt_base_axis_no_temporal_block():
    # When axis == base_axis, no TEMPORAL CONTEXT block should be inserted
    prompt = build_axis_prompt(
        story_text="She stands on the airship deck.",
        character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="present", base_axis="present",
    )
    assert "TEMPORAL CONTEXT" not in prompt


def test_build_axis_prompt_chronicle_context():
    stories = {
        "past": "She was a child in the slums.",
        "present": "She stands at the city gates.",
        "future": "She leads the rebellion.",
    }
    prompt = build_axis_prompt(
        story_text=stories["past"],
        character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="past", base_axis="present",
        title="The Iron Road",
        overall="A girl rises from nothing to lead a revolution.",
        all_stories=stories,
    )
    assert "FULL CHRONICLE CONTEXT" in prompt
    assert "The Iron Road" in prompt
    assert "A girl rises from nothing" in prompt
    assert "She was a child in the slums." in prompt
    assert "She leads the rebellion." in prompt
    assert "[PAST]" in prompt
    assert "generating the image prompt for: [PAST]" in prompt


def test_build_axis_prompt_no_context_if_no_stories():
    prompt = build_axis_prompt(
        story_text="She stands alone.",
        character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="past", base_axis="present",
    )
    assert "FULL CHRONICLE CONTEXT" not in prompt




# ── build_vision_prompt ───────────────────────────────────────────────────────

def test_build_vision_prompt_modes():
    full = build_vision_prompt(full_extraction=True)
    assert "CHARACTER:" in full and "OUTFIT:" in full
    partial = build_vision_prompt(full_extraction=False)
    assert "Do NOT describe the character's appearance" in partial
    assert "CHARACTER:" not in partial
    # interpretive sections present in both modes
    for section in ("STORY HOOKS:", "OFF-FRAME:", "SYMBOLS:", "ESSENCE:"):
        assert section in full
        assert section in partial


# ── character_tags_from_wd14 ──────────────────────────────────────────────────

def test_character_tags_filters_meta():
    tags = ["1girl", "masterpiece", "silver_hair", "highres", "sensitive",
            "school_uniform", "artist_username"]
    result = character_tags_from_wd14(tags)
    assert result == ["1girl", "silver_hair", "school_uniform"]


def test_character_tags_limit():
    tags = [f"tag_{i}" for i in range(60)]
    assert len(character_tags_from_wd14(tags, limit=40)) == 40


# ── remove_conflict_tags ──────────────────────────────────────────────────────

def test_remove_conflict_tags_tag_line():
    positive = "1girl, silver_hair, day, outdoors\n\nShe stands in the sun. Warm, calm."
    cleaned = remove_conflict_tags(positive, {"day"})
    assert "day" not in cleaned.split("\n")[0]
    assert "silver_hair" in cleaned
    # prose sentences (with periods) are untouched
    assert "She stands in the sun. Warm, calm." in cleaned


def test_remove_conflict_tags_noop():
    positive = "1girl, night"
    assert remove_conflict_tags(positive, set()) == positive


def test_remove_conflict_tags_prose_groups():
    positive = (
        "1girl, silver_hair, night\n\n"
        "She waits by a (window, indoors) as the (full_moon) rises. Calm."
    )
    default = remove_conflict_tags(positive, {"night", "indoors"})
    # default: prose inline groups untouched
    assert "(window, indoors)" in default
    assert "night" not in default.split("\n")[0]
    cleaned = remove_conflict_tags(
        positive, {"night", "indoors", "full_moon"}, include_prose_groups=True
    )
    assert "(window)" in cleaned
    assert "indoors" not in cleaned
    # fully-conflicting group disappears without leaving "()"
    assert "()" not in cleaned
    assert "full_moon" not in cleaned


# ── identity tag scoping ──────────────────────────────────────────────────────

def test_classify_identity_tag():
    assert classify_identity_tag("silver_hair") == "hair_color"
    assert classify_identity_tag("dark_blue_hair") == "hair_color"
    assert classify_identity_tag("long_hair") == "hair_style"
    assert classify_identity_tag("twintails") == "hair_style"
    assert classify_identity_tag("side_ponytail") == "hair_style"
    assert classify_identity_tag("red_eyes") == "eyes"
    assert classify_identity_tag("heterochromia") == "eyes"
    assert classify_identity_tag("animal_ears") == "face"
    assert classify_identity_tag("mole_under_eye") == "face"
    assert classify_identity_tag("dark_skin") == "face"
    # garments classify as outfit
    assert classify_identity_tag("school_uniform") == "outfit"
    assert classify_identity_tag("black_dress") == "outfit"
    # accessories are their own category (split out of outfit)
    assert classify_identity_tag("hair_ribbon") == "accessory"
    assert classify_identity_tag("necklace") == "accessory"
    assert classify_identity_tag("earrings") == "accessory"
    assert classify_identity_tag("glasses") == "accessory"
    assert classify_identity_tag("choker") == "accessory"
    assert classify_identity_tag("hat") == "accessory"
    # scene / pose / composition / time-of-day tags are never identity
    for tag in ("sitting", "indoors", "night", "window", "from_behind",
                "standing", "outdoors", "sunset", "cityscape", "looking_at_viewer"):
        assert classify_identity_tag(tag) is None, tag


def test_identity_lock_tags():
    tags = ["1girl", "silver_hair", "red_eyes", "black_dress", "necklace",
            "choker", "ponytail", "sitting", "night"]
    # always-keep = hair colour + eye colour + accessories, scale-independent
    lock = identity_lock_tags(tags)
    assert "silver_hair" in lock and "red_eyes" in lock
    assert "necklace" in lock and "choker" in lock
    # garments / hair style / pose are NOT locked (free to change)
    assert "black_dress" not in lock and "ponytail" not in lock
    assert "sitting" not in lock and "night" not in lock
    # multi-character drops hair colour + eyes (ambiguous), keeps accessories
    multi = identity_lock_tags(tags, multi_character=True)
    assert "silver_hair" not in multi and "red_eyes" not in multi
    assert "necklace" in multi and "choker" in multi
    # limit is honoured
    assert len(identity_lock_tags(
        ["silver_hair", "red_eyes", "necklace", "choker", "earrings"], limit=2
    )) == 2


def test_merge_chronicle_axis_tags_identity_only():
    """Non-base Chronicle prompts must not inherit base scene/outfit tags."""
    from app.story.generator import merge_chronicle_axis_tags

    lock = identity_lock_tags([
        "1girl", "light_brown_hair", "brown_eyes", "necklace",
        "train_interior", "window", "black_jacket", "standing",
    ])
    line = merge_chronicle_axis_tags(
        focal=["holding_sketchbook", "looking_outside"],
        search_tags=["apartment", "outdoors", "ponytail", "skirt"],
        lock_tags=lock,
    )
    lower = line.lower()
    assert "light_brown_hair" in lower and "brown_eyes" in lower
    assert "necklace" in lower
    assert "holding_sketchbook" in lower and "apartment" in lower
    # base scene / outfit never forced in via lock merge
    assert "train_interior" not in lower
    assert "window" not in lower
    assert "black_jacket" not in lower
    assert len([t for t in line.split(",") if t.strip()]) <= 20


def test_theme_must_tags_bunny_girl():
    from app.story.generator import (
        build_fast_candidate,
        ensure_theme_must_tags,
        parse_fast_prompts_json,
        theme_must_tags,
    )

    must = theme_must_tags("バニーガール")
    assert "bunny_girl" in must and "rabbit_ears" in must and "leotard" in must
    assert "bunny_girl" in theme_must_tags("night bunny girl")
    # Survives 20-tag cap even when the line is flooded.
    flooded = "1girl, " + ", ".join(f"pad_{i}" for i in range(40))
    kept = ensure_theme_must_tags(flooded, must)
    parts = [t.strip() for t in kept.split(",") if t.strip()]
    assert len(parts) <= 20
    assert "bunny_girl" in parts and "rabbit_ears" in parts
    cand = build_fast_candidate("バニーガール", locale="ja", time_scale="years")
    assert cand["id"] == "A" and "バニー" in cand["present"]
    assert "years" in cand["overall"] or "スケール" in cand["overall"]
    lines, neg = parse_fast_prompts_json(
        '{"past":"1girl, bunny_girl","present":"1girl, bunny_girl, smile",'
        '"future":"1girl, bunny_girl, walking","negative":"blurry"}'
    )
    assert "bunny_girl" in lines["present"] and neg == "blurry"


def test_normalize_time_scale_and_hours_beats():
    from app.story.generator import (
        build_fast_candidate,
        normalize_time_scale,
    )

    assert normalize_time_scale("hours") == "hours"
    assert normalize_time_scale("") == "years"
    assert normalize_time_scale("hour") == "years"  # typo → safe default
    cand = build_fast_candidate(
        "バニーガール", time_scale="hours", locale="ja", base_axis="present",
    )
    assert "数時間" in cand["past"] and "数年" not in cand["past"]
    assert "hours" in cand["overall"]
    en = build_fast_candidate(
        "bunny", time_scale="hours", locale="en", base_axis="present",
    )
    assert "HOURS" in en["past"] and "YEARS" not in en["past"]


def test_build_fast_prompts_prompt_carries_personality_and_min_30():
    from app.story.generator import (
        FAST_PROMPT_MIN_TAGS,
        build_fast_prompts_prompt,
    )

    prompt = build_fast_prompts_prompt(
        user_topic="バニーガール",
        theme_must=["bunny_girl", "rabbit_ears"],
        character_tags=["1girl", "blonde_hair", "blue_eyes"],
        character_desc="tall bunny bartender",
        beats={
            "past": "years earlier — cafe",
            "present": "now — bar",
            "future": "years later — rooftop",
        },
        time_scale="years",
        worldview="neon night city",
        emotion="warmth",
        biography={
            "occupation": "bartender",
            "personality": "quietly playful",
            "hobbies": ["sketching"],
            "quirks": ["hums while pouring"],
        },
        tone="bright",
        dramatic_mode="escalation",
        base_axis="present",
    )
    assert "ELAPSED FROM BASE" in prompt or "BASE =" in prompt or "t = 0" in prompt
    assert "quietly playful" in prompt
    assert "bartender" in prompt
    assert "bright" in prompt
    assert "escalation" in prompt
    assert f"AT LEAST {FAST_PROMPT_MIN_TAGS}" in prompt
    assert "bunny_girl" in prompt
    assert "HARD MAX 20" not in prompt


def test_sample_midrank_wd14_tags_from_ranks_20_to_50():
    from app.story.generator import sample_midrank_wd14_tags
    import random

    ranked = [f"tag_{i}" for i in range(1, 61)]  # tag_1 .. tag_60
    rng = random.Random(0)
    picked = sample_midrank_wd14_tags(
        ranked, lo=20, hi=50, k=5, exclude=["tag_25"], rng=rng,
    )
    assert len(picked) == 5
    allowed = {f"tag_{i}" for i in range(20, 51)} - {"tag_25"}
    assert set(picked) <= allowed
    assert "tag_25" not in picked
    assert "tag_1" not in picked and "tag_19" not in picked
    # Short list: fewer than k
    short = sample_midrank_wd14_tags(
        [f"t{i}" for i in range(25)], lo=20, hi=50, k=5, rng=random.Random(1),
    )
    assert 0 < len(short) <= 5


def test_cap_danbooru_tag_line_keeps_identity_and_focal():
    from app.story.generator import (
        IMAGE_PROMPT_MAX_TAGS,
        assemble_capped_positive,
        cap_danbooru_tag_line,
        draft_positive_for_comfy,
    )

    many = ", ".join([
        "1girl", "solo", "blonde_hair", "blue_eyes", "reaching",
        *[f"pad_tag_{i}" for i in range(40)],
        "sunset",
    ])
    capped = cap_danbooru_tag_line(
        many,
        priority_tags=["blonde_hair", "blue_eyes", "reaching"],
    )
    parts = [t.strip() for t in capped.split(",") if t.strip()]
    assert len(parts) == IMAGE_PROMPT_MAX_TAGS
    assert "1girl" in parts and "blonde_hair" in parts and "reaching" in parts
    assert "pad_tag_30" not in parts

    draft = draft_positive_for_comfy(
        tag_line=many,
        positive=f"{many}\n\nA very long prose paragraph " * 20,
        priority_tags=["blonde_hair", "reaching"],
    )
    assert "\n\n" not in draft
    assert len([t for t in draft.split(",") if t.strip()]) <= IMAGE_PROMPT_MAX_TAGS

    final = assemble_capped_positive(
        many,
        "word " * 200,
        priority_tags=["blonde_hair"],
    )
    head, _, prose = final.partition("\n\n")
    assert len([t for t in head.split(",") if t.strip()]) <= IMAGE_PROMPT_MAX_TAGS
    assert len(prose.split()) <= 60


def test_identity_tags_for_scale():
    tags = ["1girl", "silver_hair", "black_dress", "red_eyes", "ponytail",
            "sitting", "indoors", "night"]
    minutes = identity_tags_for_scale(tags, "minutes")
    assert "black_dress" in minutes and "silver_hair" in minutes
    assert "sitting" not in minutes and "indoors" not in minutes
    days = identity_tags_for_scale(tags, "days")
    assert "black_dress" not in days  # outfit may change
    assert "silver_hair" in days and "ponytail" in days
    years = identity_tags_for_scale(tags, "years")
    assert "ponytail" not in years  # hair style may change
    assert "silver_hair" in years and "red_eyes" in years
    assert identity_tags_for_scale(tags, "decades") == []
    # unknown scale falls back to years
    assert identity_tags_for_scale(tags, "bogus") == years


def test_identity_tags_for_scale_limit():
    tags = [f"{c}_hair" for c in ("red", "blue", "green", "black", "white")]
    assert len(identity_tags_for_scale(tags, "minutes", limit=3)) == 3


def test_inject_identity_tags():
    line = "1girl, solo, running, forest"
    out = inject_identity_tags(line, ["silver_hair", "red_eyes"])
    parts = [t.strip() for t in out.split(",")]
    # inserted right after the last subject anchor (solo)
    assert parts[:4] == ["1girl", "solo", "silver_hair", "red_eyes"]
    assert parts[4:] == ["running", "forest"]
    # case-insensitive dedup + noop on empty
    assert inject_identity_tags(line, ["Solo", "RUNNING"]) == line
    assert inject_identity_tags(line, []) == line


def test_inject_identity_tags_no_anchor():
    out = inject_identity_tags("running, forest", ["silver_hair"])
    assert out == "running, forest, silver_hair"


# ── collect_prompt_tags ───────────────────────────────────────────────────────

def test_collect_prompt_tags_mixed():
    positive = (
        "1girl, solo, silver hair, night\n\n"
        "She grips a (sword, holding_sword) on a (rooftop) under the moon. "
        "The wind howls."
    )
    tags = collect_prompt_tags(positive)
    assert "1girl" in tags and "night" in tags
    assert "silver_hair" in tags  # space → underscore
    assert "sword" in tags and "holding_sword" in tags and "rooftop" in tags
    # prose words outside (...) groups are not harvested
    assert "wind" not in tags and "moon" not in tags
    # deduped
    assert len(tags) == len(set(tags))


def test_collect_prompt_tags_empty():
    assert collect_prompt_tags("") == []
    assert collect_prompt_tags("Just a plain sentence.") == []


# ── split_vision_sections ─────────────────────────────────────────────────────

def test_split_vision_sections():
    text = (
        "SCENE: an airship deck at dusk, rain incoming\n"
        "MOOD: tense, expectant\n"
        "STORY HOOKS: she just received a letter; the fleet is about to depart\n"
        "OFF-FRAME: a burning harbor below\n"
        "SYMBOLS: the letter, a cracked compass\n"
        "ESSENCE: a point of no return"
    )
    literal, hooks = split_vision_sections(text)
    assert "airship deck" in literal
    assert "STORY HOOKS" not in literal
    assert hooks.startswith("STORY HOOKS:")
    assert "cracked compass" in hooks


def test_split_vision_sections_markdown_labels():
    text = "SCENE: a field\n**Story Hooks:** something happened\n## ESSENCE: dawn"
    literal, hooks = split_vision_sections(text)
    assert literal == "SCENE: a field"
    assert "something happened" in hooks


def test_split_vision_sections_fallback():
    text = "SCENE: a field\nMOOD: calm"
    literal, hooks = split_vision_sections(text)
    assert literal == text
    assert hooks == ""


# ── narrative craft in build_story_prompt ─────────────────────────────────────

def test_build_story_prompt_craft_rules():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    # a clear dramatic shape drives the arc (no forced single turning point)
    assert "dramatic shape" in prompt
    # motif ESCALATES rather than merely repeating
    assert "ESCALATE" in prompt
    assert "cause and effect" in prompt
    assert "its own dominant emotion" in prompt


def test_build_story_prompt_cliffhanger_ending():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    # the future act must NOT tie a bow — cliffhanger policy
    assert "do NOT tie a bow" in prompt
    assert "next volume" in prompt
    # no user topic → no ending exception clause
    assert "if the user topic explicitly names an ending" not in prompt
    # with a topic, the explicit-ending exception is offered
    with_topic = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
        user_topic="最後は花畑にたどり着く",
    )
    assert "if the user topic explicitly names an ending" in with_topic


def test_build_story_prompt_dramatic_mode_and_turn():
    # a given dramatic mode drives the arc and the turn is protected in hierarchy
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
        dramatic_mode="reversal", turn="she was the pursuer all along",
    )
    assert "REVERSAL" in prompt
    assert "she was the pursuer all along" in prompt
    assert "THE CHOSEN TURN" in prompt  # protected precedence level
    # without a mode/turn the twist-protection level is absent
    plain = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    assert "THE CHOSEN TURN" not in plain


def test_build_story_prompt_story_hooks():
    with_hooks = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
        story_hooks="STORY HOOKS: she just arrived",
    )
    assert "NARRATIVE SEEDS" in with_hooks
    assert "she just arrived" in with_hooks
    without = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    assert "NARRATIVE SEEDS" not in without


def test_build_story_prompt_boldness_scales_with_divergence():
    low = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
        divergence=0.0,
    )
    mid = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
        divergence=0.5,
    )
    high = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
        divergence=0.9,
    )
    assert "quietly" in low
    assert "unexpected-but-plausible" in mid
    assert "boldest interpretation" in high
    assert "boldest interpretation" not in low


# ── composition variation in build_axis_prompt ────────────────────────────────

def test_build_axis_prompt_composition_non_base_axis_only():
    kwargs = dict(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
    )
    non_base = build_axis_prompt(axis="past", base_axis="present", **kwargs)
    assert "COMPOSITION:" in non_base
    assert "dutch_angle" in non_base
    base = build_axis_prompt(axis="present", base_axis="present", **kwargs)
    assert "COMPOSITION:" not in base


def test_build_axis_prompt_wd14_label_base_axis():
    prompt = build_axis_prompt(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="present", base_axis="present",
        wd14_context="[common tags] 1girl",
    )
    # Base axis carries the wd14 tags and treats the base image as the anchor
    # the rendered base_axis image must match.
    assert "[common tags] 1girl" in prompt
    assert "base_axis image MUST match" in prompt


# ── Stage 2a: story candidates ────────────────────────────────────────────────

def test_build_candidates_prompt():
    prompt = build_candidates_prompt(
        character_desc="[visual tags] 1girl, long_hair",
        scene_desc="a quiet classroom",
        user_topic="放課後の冒険",
        worldview="",
        base_axis="present",
        time_scale="minutes",
        locale="ja",
    )
    # user topic and the three spirit flavours are reflected
    assert "放課後の冒険" in prompt
    for flavour in ("faithful", "rebel", "stranger"):
        assert flavour in prompt
    # locale drives output language instruction
    assert "日本語" in prompt
    # base image is bound to the base axis; other acts open elapsed volumes
    assert "THE BASE IMAGE IS THE [PRESENT] MOMENT" in prompt
    # Elapsed-time framing (JA locale wraps the header in Japanese, but the
    # bracket labels stay in English so the parser can lock onto them).
    assert "経過" in prompt
    assert "[PAST]" in prompt and "[FUTURE]" in prompt
    # per-axis output schema (beats), not a single summary
    assert '"past"' in prompt and '"present"' in prompt and '"future"' in prompt
    assert '"summary"' not in prompt
    # grounding guardrail keeps surprise in the real-world register
    assert "GROUNDING" in prompt and "supernatural" in prompt
    # minutes scale = image+alpha continuation, no scene jump
    assert "EXTENDED slightly" in prompt and "HOW MUCH CHANGES" in prompt


def test_candidates_prompt_base_axis_directions():
    # base=past → the other two acts are both LATER (elapsed forward from base)
    past_base = build_candidates_prompt(
        character_desc="c", scene_desc="s", base_axis="past", time_scale="hours",
    )
    assert "THE BASE IMAGE IS THE [PAST] MOMENT" in past_base
    # Both non-base axes use LATER phrasing; no EARLIER when base = past.
    assert past_base.count(" LATER") >= 2
    assert " EARLIER" not in past_base
    # Elapsed header explicitly names the far act's two-step distance.
    assert "MOST OF A DAY LATER" in past_base


def test_candidates_prompt_time_scale_differs():
    minutes = build_candidates_prompt(
        character_desc="c", scene_desc="s", time_scale="minutes", locale="en"
    )
    decades = build_candidates_prompt(
        character_desc="c", scene_desc="s", time_scale="decades", locale="en"
    )
    from app.story.generator import _SCALE_VISUAL_RULES
    # each embeds its own scale's continuity note, so prompts differ by scale
    assert _SCALE_VISUAL_RULES["minutes"]["must_keep"] in minutes
    assert _SCALE_VISUAL_RULES["decades"]["may_differ"] in decades
    # Elapsed-time framing echoes the span per scale.
    assert "A FEW MINUTES LATER" in minutes or "A FEW MINUTES EARLIER" in minutes
    assert "SEVERAL DECADES LATER" in decades or "SEVERAL DECADES EARLIER" in decades
    assert minutes != decades


def test_parse_candidates_json_clean():
    raw = (
        '{"candidates": ['
        '{"id":"A","title":"T1","past":"p1","present":"pr1","future":"f1","motif":"m1"},'
        '{"id":"B","title":"T2","past":"p2","present":"pr2","future":"f2","motif":"m2"}'
        ']}'
    )
    out = parse_candidates_json(raw)
    assert len(out) == 2
    assert out[0]["id"] == "A" and out[0]["title"] == "T1"
    assert out[0]["past"] == "p1" and out[0]["future"] == "f1"
    assert out[0]["motif"] == "m1"
    # summary is derived from the present beat when not provided
    assert out[0]["summary"] == "pr1"


def test_parse_candidates_json_legacy_key_motif():
    # older records used `key_motif`; parser reads either and emits `motif`
    raw = '{"candidates":[{"id":"A","title":"T","present":"pr","key_motif":"legacy"}]}'
    out = parse_candidates_json(raw)
    assert out[0]["motif"] == "legacy"


def test_parse_candidates_json_legacy_summary():
    # older records carried a single summary → still parsed, beats empty
    raw = '{"candidates":[{"id":"A","title":"T","summary":"S"}]}'
    out = parse_candidates_json(raw)
    assert out[0]["summary"] == "S" and out[0]["present"] == ""


def test_parse_candidates_json_wrapped_and_broken():
    wrapped = 'noise before {"candidates":[{"id":"A","title":"T","present":"pr"}]} tail'
    out = parse_candidates_json(wrapped)
    assert len(out) == 1 and out[0]["title"] == "T" and out[0]["id"] == "A"
    assert parse_candidates_json("total garbage") == []


def test_build_expand_prompt():
    prompt = build_expand_prompt(
        selected={"title": "The Bell", "past": "a bell is cast",
                  "present": "a bell tolls", "future": "a bell cracks",
                  "motif": "bronze bell"},
        character_desc="[visual tags] 1girl",
        scene_desc="a belfry",
        base_axis="present",
        worldview="",
        time_scale="years",
        locale="ja",
        user_topic="最後は鐘が割れる瞬間",
    )
    # the chosen candidate's beats seed the expansion, in Japanese, keeping markers
    assert "The Bell" in prompt and "bronze bell" in prompt
    assert "a bell is cast" in prompt and "a bell cracks" in prompt
    assert "日本語" in prompt
    assert "[TITLE]" in prompt and "[PAST]" in prompt
    # user topic must reach the LLM as a hard constraint
    assert "最後は鐘が割れる瞬間" in prompt
    assert "COHERENCE HIERARCHY" in prompt
    assert "IS the base image" in prompt


# ── multi-character identity scoping ──────────────────────────────────────────

def test_is_multi_character():
    assert is_multi_character(["1girl", "long_hair"]) is False
    assert is_multi_character(["2girls", "blonde_hair"]) is True
    assert is_multi_character(["multiple_girls"]) is True


def test_identity_tags_multi_character():
    tags = ["blonde_hair", "blue_eyes", "long_hair"]
    solo = identity_tags_for_scale(tags, "minutes")
    multi = identity_tags_for_scale(tags, "minutes", multi_character=True)
    # solo anchors hair colour + eyes; multi drops those (ambiguous ownership)
    assert "blonde_hair" in solo and "blue_eyes" in solo
    assert "blonde_hair" not in multi and "blue_eyes" not in multi
    # hair style still anchors even with multiple characters
    assert "long_hair" in multi


# ── WD14 dependency reduction in build_axis_prompt ────────────────────────────

def test_axis_prompt_no_wd14_for_non_base():
    prompt = build_axis_prompt(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="future", base_axis="present",
        wd14_context="UNIQUE_WD14_BLOCK_XYZ",
    )
    assert "UNIQUE_WD14_BLOCK_XYZ" not in prompt


def test_axis_prompt_axis_tags_injected():
    prompt = build_axis_prompt(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="future", base_axis="present",
        axis_tags=["silver_hair", "bronze_bell"],
    )
    assert "silver_hair, bronze_bell" in prompt


def test_minutes_outfit_change_allowed():
    from app.story.generator import _SCALE_VISUAL_RULES
    rules = _SCALE_VISUAL_RULES["minutes"]
    assert "outfit" in rules["may_differ"]
    assert "outfit" not in rules["forbidden"]


def test_scale_delta_line():
    from app.story.generator import _scale_delta_line
    minutes = _scale_delta_line("minutes")
    decades = _scale_delta_line("decades")
    assert "EXTENDED" in minutes and "SAME scene" in minutes
    assert "eras" in decades
    assert minutes != decades
    # unknown scale falls back to years
    assert _scale_delta_line("bogus") == _scale_delta_line("years")


# ── per-axis story tags + to-English translation ──────────────────────────────

def test_build_story_tags_prompt():
    prompt = build_story_tags_prompt("A girl grips a sword on a rooftop at night.")
    assert "about 50 danbooru tags" in prompt
    assert "A girl grips a sword" in prompt
    # generic tag parser: spaced→underscored, deduped, keeps >15 (limit 60)
    out = parse_tags_json('{"tags": ["long hair", "long_hair", "bronze_bell"]}')
    assert out == ["long_hair", "bronze_bell"]
    import json as _json
    many = parse_tags_json(_json.dumps({"tags": [f"t{i}" for i in range(80)]}))
    assert len(many) == 60  # capped at limit
    assert parse_tags_json("broken") == []


def test_translation_to_english():
    prompt = build_translation_to_english_prompt(
        "題", "概要", {"past": "過去", "present": "現在", "future": "未来"}
    )
    assert "English" in prompt
    out = parse_english_translation_json(
        '{"title":"T","overall":"O","past":"p","present":"pr","future":"f"}'
    )
    assert out["title"] == "T" and out["future"] == "f"
    assert parse_english_translation_json("broken")["title"] == ""


# ── pose expressiveness: story action + ACTION-ANCHOR + Stage 3a ──────────────

def test_story_prompt_requires_physical_action():
    prompt = build_story_prompt(
        character_desc="1girl", scene_desc="a room",
        base_axis="present", worldview="", time_scale="years",
    )
    assert "stageable physical action" in prompt
    # boring default explicitly banned
    assert "merely standing" in prompt


def test_visual_script_guide_has_action_anchor():
    prompt = build_axis_prompt(
        story_text="She reaches for the letter.",
        character_tags=["1girl"], character_desc="",
        prompt_style="danbooru+natural", time_scale="years",
        axis="past", base_axis="present",
    )
    # verb→danbooru action tag map ported from Refine
    assert "ACTION-ANCHOR" in prompt
    assert "reaching" in prompt and "outstretched_arm" in prompt
    # boring upright default forbidden as the whole pose
    assert "FORBIDDEN as the whole pose" in prompt
    # NEGATIVE guidance suggests static-pose tags
    assert "static_pose" in prompt


def test_build_visual_examination_prompt_demands_action_tags():
    prompt = build_visual_examination_prompt(
        story_text="She kneels to pick up the shard.",
        axis="past", base_axis="present", time_scale="minutes",
        character_desc="1girl",
    )
    assert "focal_action_tags" in prompt
    assert "danbooru" in prompt
    # non-base minutes axis carries the scale constraints
    assert "MUST keep" in prompt and "FORBIDDEN" in prompt
    # camera decision requested
    assert "camera_angle" in prompt


def test_parse_visual_plan_json():
    plan = parse_visual_plan_json(
        '{"shot":"cowboy_shot","camera_angle":"from_side",'
        '"focal_action_tags":["reaching","outstretched arm"],'
        '"gesture_prose":"she leans in","lighting":"warm side light",'
        '"palette":"amber","props":["letter"],"mood":"tense"}'
    )
    assert plan["shot"] == "cowboy_shot"
    # spaces normalised to underscores
    assert plan["focal_action_tags"] == ["reaching", "outstretched_arm"]
    assert plan["props"] == ["letter"]
    # legacy `key_props` key still parses so older records keep loading
    legacy = parse_visual_plan_json(
        '{"shot":"cowboy_shot","key_props":["letter"]}'
    )
    assert legacy["props"] == ["letter"]
    # broken / empty → {}
    assert parse_visual_plan_json("nonsense") == {}
    assert parse_visual_plan_json('{"shot":"","focal_action_tags":[]}') == {}


def test_axis_prompt_includes_visual_plan():
    plan = {
        "shot": "cowboy_shot", "camera_angle": "from_side",
        "focal_action_tags": ["reaching", "outstretched_arm"],
        "gesture_prose": "she leans across the desk",
        "lighting": "warm", "palette": "amber", "props": ["letter"],
        "mood": "tense",
    }
    prompt = build_axis_prompt(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru+natural", time_scale="years",
        axis="past", base_axis="present", visual_plan=plan,
    )
    assert "LOCKED SHOT PLAN" in prompt
    assert "reaching, outstretched_arm" in prompt
    assert "she leans across the desk" in prompt
    # no plan → no block
    no_plan = build_axis_prompt(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="past", base_axis="present",
    )
    assert "LOCKED SHOT PLAN" not in no_plan


def test_story_tags_prompt_prioritises_action():
    prompt = build_story_tags_prompt("She runs across the bridge.")
    assert "PHYSICAL ACTION" in prompt
    assert "dynamic_pose" in prompt


# ── ongoing-action topic intent (point 3) ─────────────────────────────────────

def test_candidates_prompt_honours_ongoing_topic():
    prompt = build_candidates_prompt(
        character_desc="1girl", scene_desc="reading at a desk",
        user_topic="本を読んでいる最中", time_scale="minutes",
    )
    assert "tense and aspect" in prompt
    assert "IN PROGRESS" in prompt
    assert "本を読んでいる最中" in prompt


def test_scale_delta_minutes_keeps_action_ongoing():
    prompt = build_story_prompt(
        character_desc="1girl", scene_desc="reading",
        base_axis="present", worldview="", time_scale="minutes",
    )
    assert "STILL underway" in prompt


# ── emotion register option (point 4) ─────────────────────────────────────────

def test_emotion_register_threads_into_prompts():
    cand = build_candidates_prompt(
        character_desc="1girl", scene_desc="a room", emotion="nostalgia",
    )
    assert "EMOTIONAL REGISTER" in cand and "nostalgia" in cand
    story = build_story_prompt(
        character_desc="1girl", scene_desc="a room",
        base_axis="present", worldview="", emotion="melancholy",
    )
    assert "EMOTIONAL REGISTER" in story
    axis = build_axis_prompt(
        story_text="s", character_tags=["1girl"], character_desc="",
        prompt_style="danbooru", time_scale="years",
        axis="past", base_axis="present", emotion="serenity",
    )
    assert "EMOTIONAL REGISTER" in axis


def test_emotion_register_off_by_default():
    prompt = build_candidates_prompt(character_desc="1girl", scene_desc="a room")
    assert "EMOTIONAL REGISTER" not in prompt
    # unknown emotion is ignored (no crash, no block)
    prompt2 = build_story_prompt(
        character_desc="1girl", scene_desc="a room",
        base_axis="present", worldview="", emotion="not_a_real_emotion",
    )
    assert "EMOTIONAL REGISTER" not in prompt2


# ── elapsed-time header (next-volume framing) ────────────────────────────────

def test_elapsed_header_past_base_years_en():
    header = _elapsed_time_header(base_axis="past", time_scale="years", locale="en")
    assert "ELAPSED FROM BASE" in header
    assert "BASE = [PAST]" in header
    assert "t = 0" in header
    assert "A FEW YEARS LATER" in header
    assert "SEVERAL YEARS LATER" in header
    # Never mixes EARLIER when base is past (no earlier moments to reach).
    assert "EARLIER" not in header


def test_elapsed_header_future_base_minutes_en():
    header = _elapsed_time_header(
        base_axis="future", time_scale="minutes", locale="en"
    )
    assert "BASE = [FUTURE]" in header
    # From future looking back: PAST is two Δ, PRESENT is one Δ.
    assert "SEVERAL MINUTES EARLIER" in header
    assert "A FEW MINUTES EARLIER" in header
    assert "LATER" not in header


def test_elapsed_header_present_base_symmetric():
    header = _elapsed_time_header(
        base_axis="present", time_scale="hours", locale="en"
    )
    assert "BASE = [PRESENT]" in header
    # From present: past is one Δ EARLIER, future is one Δ LATER.
    assert "A FEW HOURS EARLIER" in header
    assert "A FEW HOURS LATER" in header
    # No two-step phrasing when base is in the middle.
    assert "MOST OF A DAY" not in header


def test_elapsed_header_locale_ja():
    header = _elapsed_time_header(
        base_axis="past", time_scale="years", locale="ja"
    )
    assert "経過" in header
    assert "BASE = [PAST]" in header
    assert "数年後 経過" in header
    assert "十数年後 経過" in header


def test_elapsed_header_all_scales_smoke():
    for scale in (
        "minutes", "tens_of_minutes", "hours", "days",
        "months", "years", "decades",
    ):
        for base in ("past", "present", "future"):
            h = _elapsed_time_header(base_axis=base, time_scale=scale, locale="en")
            assert h, f"empty header for {base=} {scale=}"
            assert f"BASE = [{base.upper()}]" in h
            for axis in ("past", "present", "future"):
                if axis == base:
                    continue
                assert f"[{axis.upper()}]" in h


def test_elapsed_header_unknown_scale_defaults_to_years():
    h = _elapsed_time_header(base_axis="present", time_scale="millennia", locale="en")
    assert "A FEW YEARS EARLIER" in h
    assert "A FEW YEARS LATER" in h


# ── elapsed header is threaded into every stage prompt ────────────────────────

def test_candidates_prompt_uses_elapsed_header_not_axis_lines():
    prompt = build_candidates_prompt(
        character_desc="1girl", scene_desc="a room",
        base_axis="past", time_scale="years",
    )
    assert "ELAPSED FROM BASE" in prompt
    assert "A FEW YEARS LATER" in prompt
    assert "SEVERAL YEARS LATER" in prompt
    # Old direction-based phrasing is gone.
    assert "the moment a few years AFTER the image" not in prompt
    assert "the moment a few years BEFORE the image" not in prompt


def test_story_prompt_carries_elapsed_header():
    prompt = build_story_prompt(
        character_desc="1girl", scene_desc="a room",
        base_axis="future", worldview="",
        time_scale="hours",
    )
    assert "ELAPSED FROM BASE" in prompt
    assert "A FEW HOURS EARLIER" in prompt
    # base-lock language should reference elapsed volumes, not "PRESENT = base moment"
    assert "PRESENT = the base-image moment" not in prompt


def test_expand_prompt_carries_elapsed_header():
    seed = {
        "id": "A", "title": "Silent Garden",
        "past": "seed p", "present": "seed pr", "future": "seed f",
        "motif": "a key",
    }
    prompt = build_expand_prompt(
        selected=seed,
        character_desc="1girl", scene_desc="a room",
        base_axis="present", worldview="",
        time_scale="days",
    )
    assert "ELAPSED FROM BASE" in prompt
    assert "A FEW DAYS EARLIER" in prompt
    assert "A FEW DAYS LATER" in prompt


def test_visual_examination_prompt_carries_elapsed_header():
    prompt = build_visual_examination_prompt(
        story_text="she reaches for the door",
        axis="past", base_axis="present",
        time_scale="months",
    )
    assert "ELAPSED FROM BASE" in prompt
    # non-base act constraint should use elapsed phrasing.
    assert "A FEW MONTHS EARLIER" in prompt


def test_axis_prompt_carries_elapsed_header():
    prompt = build_axis_prompt(
        story_text="s",
        character_tags=["1girl", "solo"],
        character_desc="",
        prompt_style="danbooru",
        time_scale="years",
        axis="future", base_axis="past",
    )
    assert "ELAPSED FROM BASE" in prompt
    # future is two Δ later than past.
    assert "SEVERAL YEARS LATER" in prompt


# ── Stage 3b Pass 1 (build_axis_tags_prompt) ─────────────────────────────────

def test_axis_tags_prompt_demands_json_and_rules():
    prompt = build_axis_tags_prompt(
        story_text="she reaches for the door",
        character_tags=["1girl", "silver_hair", "blue_eyes"],
        character_desc="",
        axis="past", base_axis="present",
        time_scale="years",
    )
    assert "JSON ONLY" in prompt
    assert "SUBJECT-FIRST" in prompt
    assert "ACTION-ANCHOR" in prompt
    assert "HARD MAX 20 TAGS" in prompt
    # Structured schema keys the parser expects.
    for key in (
        "danbooru_tags", "subject_tags", "hair_tags", "expression_tags",
        "clothing_tags", "pose_tags", "background_tags",
        "object_tags", "lighting_tags",
    ):
        assert f'"{key}"' in prompt
    # Elapsed header threaded through.
    assert "ELAPSED FROM BASE" in prompt


def test_parse_axis_tags_json_merges_categories_into_tag_line():
    raw = (
        '{"danbooru_tags": "1girl, silver_hair, blue_eyes, reaching",'
        ' "subject_tags": "1girl, solo",'
        ' "pose_tags": "reaching, outstretched_arm, leaning_forward",'
        ' "background_tags": "dim_hallway, wooden_door",'
        ' "negative_supplement": "text, watermark"}'
    )
    tag_line, categories, neg = parse_axis_tags_json(raw)
    # danbooru_tags come first, unique tags from buckets are appended.
    assert tag_line.startswith("1girl, silver_hair, blue_eyes, reaching")
    assert "solo" in tag_line
    assert "outstretched_arm" in tag_line
    assert "leaning_forward" in tag_line
    assert "dim_hallway" in tag_line
    assert "wooden_door" in tag_line
    assert categories["pose_tags"] == ["reaching", "outstretched_arm", "leaning_forward"]
    assert neg == "text, watermark"


def test_parse_axis_tags_json_broken_returns_empty():
    assert parse_axis_tags_json("not json") == ("", {}, "")
    assert parse_axis_tags_json("") == ("", {}, "")


# ── Chronicle degenerate detector ─────────────────────────────────────────────

def _make_tag_line(n: int, *, anchor: bool = True) -> str:
    """Helper: build a tag line with n tags, optionally anchored + action + expression."""
    # Always include a dynamic action + expression so idle/expression guards do not trip.
    extras = max(0, n - 3)
    body = ", ".join(["holding", "smile", * (f"tag_{i}" for i in range(extras))])
    return f"1girl, {body}" if anchor else body


def test_chronicle_tags_degenerate_short_tag_line():
    tag_line = _make_tag_line(10)
    degenerate, reason = _chronicle_tags_degenerate(tag_line)
    assert degenerate
    assert "tag_count=" in reason


def test_chronicle_tags_degenerate_missing_subject_anchor():
    tag_line = "holding, smile, " + ", ".join(f"tag_{i}" for i in range(40))
    degenerate, reason = _chronicle_tags_degenerate(tag_line)
    assert degenerate
    assert reason == "no_subject_anchor"


def test_chronicle_tags_degenerate_healthy_prompt():
    tag_line = _make_tag_line(55)
    degenerate, reason = _chronicle_tags_degenerate(tag_line)
    assert not degenerate
    assert reason == ""


def test_chronicle_tags_degenerate_anchor_solo():
    tag_line = "solo, holding, smile, " + ", ".join(f"tag_{i}" for i in range(40))
    degenerate, _ = _chronicle_tags_degenerate(tag_line)
    assert not degenerate


def test_chronicle_tags_degenerate_idle_pose_only():
    """standing/smile with no dynamic action must densify — simulation finding."""
    pad = ", ".join(f"bg_{i}" for i in range(30))
    tag_line = f"1girl, standing, smile, looking_at_viewer, cafe, counter, {pad}"
    degenerate, reason = _chronicle_tags_degenerate(tag_line)
    assert degenerate
    assert reason == "no_dynamic_action"


def test_chronicle_tags_degenerate_person_needs_expression():
    """Person on-screen without any face/mood tag must densify."""
    pad = ", ".join(f"bg_{i}" for i in range(30))
    tag_line = f"1girl, holding, reaching, cafe, counter, apron, {pad}"
    degenerate, reason = _chronicle_tags_degenerate(tag_line)
    assert degenerate
    assert reason == "no_expression"
    # expressionless still counts — a chosen blank face is fine
    ok = f"1girl, holding, reaching, expressionless, cafe, {pad}"
    assert not _chronicle_tags_degenerate(ok)[0]


# ── Stage 3b Pass 2 (build_axis_prose_prompt) ────────────────────────────────

def test_axis_prose_prompt_embeds_pass1_tag_line():
    tag_line = "1girl, solo, silver_hair, blue_eyes, reaching, outstretched_arm"
    prompt = build_axis_prose_prompt(
        story_text="she reaches for the door",
        tag_line=tag_line,
        character_tags=["1girl", "silver_hair"],
        character_desc="",
        prompt_style="danbooru+natural",
        axis="past", base_axis="present",
        time_scale="years",
    )
    assert tag_line in prompt
    assert "PASS 1 DANBOORU TAG LINE" in prompt
    assert "Visual Script" in prompt
    assert "POSITIVE:" in prompt
    assert "NEGATIVE:" in prompt
    assert "SUBJECT_TAGS:" in prompt
    # danbooru+natural: tag line + prose + category footer
    assert "THREE parts" in prompt
    assert "verbatim" in prompt


def test_axis_prose_prompt_natural_style_no_tag_line_output():
    tag_line = "1girl, solo, reaching"
    prompt = build_axis_prose_prompt(
        story_text="scene",
        tag_line=tag_line,
        character_tags=["1girl"],
        character_desc="",
        prompt_style="natural",
        axis="present", base_axis="present",
    )
    # natural mode: prose + category footer; no leading flat tag line in POSITIVE
    assert "No leading flat tag line" in prompt or "no leading flat tag line" in prompt
    assert tag_line in prompt  # still passed as guidance
    assert "SUBJECT_TAGS:" in prompt

def test_axis_prose_prompt_forwards_negative_supplement():
    prompt = build_axis_prose_prompt(
        story_text="scene",
        tag_line="1girl, solo",
        character_tags=["1girl"],
        character_desc="",
        prompt_style="danbooru+natural",
        axis="present", base_axis="present",
        negative_supplement="text, watermark, blurry",
    )
    assert "text, watermark, blurry" in prompt


# ── Sanity: 2-pass output shape when combined ────────────────────────────────

def test_pass1_and_pass2_share_context_shape():
    """Both builders should reference the same story, chronicle, and identity."""
    kw = dict(
        story_text="she reaches for the door",
        character_tags=["1girl", "silver_hair"],
        character_desc="",
        axis="past", base_axis="present", time_scale="years",
        all_stories={
            "past": "then", "present": "now (base)", "future": "later",
        },
        title="Silent Doors", overall="A chronicle.",
    )
    tags_prompt = build_axis_tags_prompt(**kw)
    prose_prompt = build_axis_prose_prompt(
        tag_line="1girl, solo, reaching",
        prompt_style="danbooru+natural",
        **kw,
    )
    for shared in ("she reaches for the door", "Silent Doors",
                   "[PAST]", "[PRESENT]", "1girl"):
        assert shared in tags_prompt, f"missing in tags_prompt: {shared!r}"
        assert shared in prose_prompt, f"missing in prose_prompt: {shared!r}"


# ── dramatic modes (story-shape dimension) ────────────────────────────────────

def test_dramatic_mode_line():
    assert "REVERSAL" in _dramatic_mode_line("reversal")
    assert "反転" in _dramatic_mode_line("reversal", "ja")
    # unknown / empty → ''
    assert _dramatic_mode_line("") == ""
    assert _dramatic_mode_line("not_a_mode") == ""


def test_assign_dramatic_modes_distinct_and_preferred():
    import random
    modes = assign_dramatic_modes(rng=random.Random(0))
    # three ids, three DISTINCT modes
    assert set(modes) == {"A", "B", "C"}
    assert len(set(modes.values())) == 3
    # a preferred mode is pinned onto the first id
    pinned = assign_dramatic_modes(preferred="irony", rng=random.Random(0))
    assert pinned["A"] == "irony"
    assert len(set(pinned.values())) == 3
    # unknown preferred is ignored (still three distinct, no crash)
    junk = assign_dramatic_modes(preferred="bogus", rng=random.Random(1))
    assert len(set(junk.values())) == 3


def test_candidates_prompt_includes_dramatic_modes():
    prompt = build_candidates_prompt(
        character_desc="1girl", scene_desc="a room", time_scale="hours",
        candidate_modes={"A": "irony", "B": "parting", "C": "pursuit"},
    )
    assert "Dramatic shape for A" in prompt
    assert "IRONY" in prompt and "PARTING" in prompt and "PURSUIT" in prompt
    # new schema fields requested
    assert '"dramatic_mode"' in prompt and '"turn"' in prompt
    # cliffhanger bias reaches the candidate stage too
    assert "LEAN INTO the turn" in prompt


def test_parse_candidates_json_dramatic_mode_turn():
    raw = (
        '{"candidates":[{"id":"A","title":"T","dramatic_mode":"Revelation",'
        '"past":"p","present":"pr","future":"f","motif":"m",'
        '"turn":"the letter was never sent"}]}'
    )
    out = parse_candidates_json(raw)
    assert out[0]["dramatic_mode"] == "revelation"  # lowercased
    assert out[0]["turn"] == "the letter was never sent"
    # legacy records without the fields → empty strings, no crash
    legacy = parse_candidates_json('{"candidates":[{"id":"A","present":"pr"}]}')
    assert legacy[0]["dramatic_mode"] == "" and legacy[0]["turn"] == ""


def test_expand_prompt_protects_turn():
    prompt = build_expand_prompt(
        selected={"id": "A", "title": "T", "past": "p", "present": "pr",
                  "future": "f", "motif": "m", "dramatic_mode": "revelation",
                  "turn": "the letter was never sent"},
        character_desc="1girl", scene_desc="a room",
        base_axis="present", worldview="", time_scale="days",
    )
    assert "PROTECTED" in prompt
    assert "the letter was never sent" in prompt
    assert "REVELATION" in prompt
    assert "THE CHOSEN TURN" in prompt  # hierarchy protects the twist


# ── timeline distinctness helpers (code-side enforcement) ─────────────────────

def test_text_similarity():
    assert _text_similarity("she runs home", "she runs home") == 1.0
    assert _text_similarity("morning bus stop", "sunset hilltop bow") < 0.3
    # empty inputs → 0.0, no crash
    assert _text_similarity("", "anything") == 0.0


def test_candidate_beats_degenerate():
    same = "She stands on the sunny hilltop holding a clear umbrella, smiling."
    assert _candidate_beats_degenerate(
        {"past": same, "present": same, "future": same}
    )
    varied = {
        "past": "She waits nervously at the crowded morning bus stop, gripping her umbrella.",
        "present": "She stands triumphant on the sunny hilltop, arms flung wide open.",
        "future": "She trudges downhill at dusk, shoulders sagging with quiet fatigue.",
    }
    assert not _candidate_beats_degenerate(varied)
    # a missing beat counts as degenerate
    assert _candidate_beats_degenerate({"past": "a", "present": "", "future": "c"})


def test_candidates_degenerate_set():
    same = "She stands on the hilltop holding a clear umbrella, smiling brightly."
    bad = [{"past": same, "present": same, "future": same} for _ in range(3)]
    assert candidates_degenerate(bad)
    varied = {
        "past": "She waits nervously at the crowded morning bus stop with her umbrella.",
        "present": "She stands triumphant on the sunny hilltop, arms flung wide open.",
        "future": "She trudges downhill at dusk, shoulders sagging with quiet fatigue.",
    }
    assert not candidates_degenerate([dict(varied) for _ in range(3)])
    # empty set is degenerate
    assert candidates_degenerate([])


def test_acts_temporally_distinct():
    same = "She stands on the sunny hilltop holding a clear umbrella, smiling."
    assert not acts_temporally_distinct(
        {"past": same, "present": same, "future": same}
    )
    assert acts_temporally_distinct({
        "past": "A small child hides beneath the stairwell in the crumbling orphanage.",
        "present": "She stands at the tall iron city gates as the guards turn to stare.",
        "future": "She lifts a torch above the roaring crowd in the burning plaza.",
    })
    # incomplete input is treated as distinct (handled by the missing-act path)
    assert acts_temporally_distinct({"past": "a", "present": "", "future": "c"})


def test_build_differentiate_acts_prompt():
    prompt = build_differentiate_acts_prompt(
        title="The Umbrella", overall="An arc across one afternoon.",
        stories={"past": "same p", "present": "same n", "future": "same f"},
        base_axis="present", time_scale="hours", locale="ja",
    )
    assert "collapsed" in prompt
    assert "TIME CONTRACT" in prompt
    assert "経過" in prompt  # ja elapsed header follows the story locale
    # keeps the marker contract so parse_story_sections consumes it
    for marker in ("[TITLE]", "[PAST]", "[PRESENT]", "[FUTURE]"):
        assert marker in prompt
    # base axis stays matched to the image
    assert "[PRESENT] act must still match the base image" in prompt


def test_should_differentiate_acts_skips_micro_scales():
    assert not should_differentiate_acts("minutes")
    assert not should_differentiate_acts("tens_of_minutes")
    assert should_differentiate_acts("hours")
    assert should_differentiate_acts("years")
    assert should_differentiate_acts("decades")


def test_activities_temporally_distinct():
    same = "She stands by the window holding a teacup, gazing outside quietly."
    assert not activities_temporally_distinct(
        {"past": same, "present": same, "future": same}
    )
    assert activities_temporally_distinct({
        "past": "She ties her laces at the muddy trailhead before dawn.",
        "present": "She climbs the ridge with both hands on the rope.",
        "future": "She plants a marker flag on the windy summit rock.",
    })
    assert activities_temporally_distinct({"past": "a", "present": "", "future": "c"})


def test_axis_slots_collapsed():
    assert axis_slots_collapsed({
        "past": {"place": "park bench", "activity": "reading"},
        "present": {"place": "park bench", "activity": "reading"},
        "future": {"place": "park bench", "activity": "reading"},
    })
    assert not axis_slots_collapsed({
        "past": {"place": "kitchen", "activity": "kneading dough"},
        "present": {"place": "rooftop", "activity": "hanging laundry"},
        "future": {"place": "station", "activity": "catching the last train"},
    })
    assert not axis_slots_collapsed({})
    assert not axis_slots_collapsed(None)


def test_build_differentiate_activities_prompt():
    prompt = build_differentiate_activities_prompt(
        activities={
            "past": "same action", "present": "same action", "future": "same action",
        },
        selected={"past": "beat p", "present": "beat n", "future": "beat f"},
        base_axis="present",
        time_scale="years",
        scene_desc="a sunlit classroom",
        user_topic="卒業",
    )
    assert "CLEARLY DIFFERENT" in prompt
    assert "CURRENT ACTIONS" in prompt
    assert "[PRESENT] action must still match the base scene" in prompt
    assert "卒業" in prompt
    assert '"past"' in prompt and '"future"' in prompt


# ── Scene constraints + mechanical mutex conflicts ────────────────────────────

def test_infer_axis_scene_constraints_night_indoor():
    story = (
        "At midnight she sits alone in her bedroom, lit only by moonlight "
        "through the window, turning the pages of a worn notebook."
    )
    c = infer_axis_scene_constraints(story)
    assert c["time_of_day"] == "night"
    assert c["indoor_outdoor"] == "indoor"
    assert "night" in c["must_tags"]
    assert "indoors" in c["must_tags"]
    assert "day" in c["forbid_tags"]
    assert "outdoors" in c["forbid_tags"]
    assert "blue_sky" in c["forbid_tags"]


def test_infer_axis_scene_constraints_day_outdoor():
    story = (
        "On a sunny afternoon she walks through the park, sunlit grass "
        "under her shoes, waving at friends across the street."
    )
    c = infer_axis_scene_constraints(story)
    assert c["time_of_day"] == "day"
    assert c["indoor_outdoor"] == "outdoor"
    assert "day" in c["must_tags"] or "daylight" in c["must_tags"]
    assert "outdoors" in c["must_tags"]
    assert "night" in c["forbid_tags"]
    assert "indoors" in c["forbid_tags"]


def test_infer_axis_scene_constraints_empty_when_ambiguous():
    c = infer_axis_scene_constraints("She holds a letter and waits.")
    assert c["time_of_day"] == ""
    assert c["indoor_outdoor"] == ""
    assert c["must_tags"] == []
    assert c["forbid_tags"] == []


def test_apply_scene_constraints_filters_and_injects():
    constraints = infer_axis_scene_constraints(
        "At night she stands outdoors under the moonlit sky."
    )
    tags = ["1girl", "day", "blue_sky", "outdoors", "smile", "park"]
    out = apply_scene_constraints(tags, constraints)
    assert "day" not in [t.lower() for t in out]
    assert "blue_sky" not in [t.lower() for t in out]
    assert "night" in out
    assert "1girl" in out
    assert "outdoors" in out


def test_find_mutex_conflict_tags_day_vs_night():
    tags = ["1girl", "night", "day", "blue_sky", "moonlight", "smile"]
    conflicts = find_mutex_conflict_tags(tags, preferred=["night"])
    assert "day" in conflicts
    assert "blue_sky" in conflicts
    assert "night" not in conflicts
    assert "moonlight" not in conflicts


def test_find_mutex_conflict_tags_indoor_vs_outdoor():
    tags = ["indoors", "outdoors", "bedroom", "park"]
    conflicts = find_mutex_conflict_tags(tags, preferred=["indoors"])
    assert "outdoors" in conflicts
    assert "park" in conflicts
    assert "indoors" not in conflicts
    assert "bedroom" not in conflicts


def test_find_mutex_conflict_tags_first_seen_when_no_preferred():
    tags = ["day", "night", "sunny"]
    conflicts = find_mutex_conflict_tags(tags, preferred=None)
    # day appears first → night loses
    assert "night" in conflicts
    assert "day" not in conflicts


def test_find_identity_mutex_conflicts_hair_and_eyes():
    locks = ["blonde_hair", "blue_eyes", "hair_ribbon"]
    tags = ["blonde_hair", "brown_hair", "blue_eyes", "green_eyes", "long_hair"]
    conflicts = find_identity_mutex_conflicts(tags, locks)
    assert "brown_hair" in conflicts
    assert "green_eyes" in conflicts
    assert "blonde_hair" not in conflicts
    assert "blue_eyes" not in conflicts
    assert "long_hair" not in conflicts  # style, not color


def test_remove_conflict_tags_with_mutex_set():
    positive = "1girl, night, day, blue_sky, indoors, outdoors, smile"
    conflicts = find_mutex_conflict_tags(
        [t.strip() for t in positive.split(",")],
        preferred=["night", "indoors"],
    )
    cleaned = remove_conflict_tags(positive, conflicts)
    assert "night" in cleaned
    assert "indoors" in cleaned
    assert "day" not in cleaned.split(", ")
    assert "outdoors" not in cleaned.split(", ")


# ── Phase B: draft refine helpers ─────────────────────────────────────────────

def test_should_use_draft_refine_modes():
    assert not should_use_draft_refine(
        mode="on", time_scale="years", divergence=0.8, workflow_name="",
    )
    assert not should_use_draft_refine(
        mode="on", time_scale="years", divergence=0.8,
        workflow_name="x.json", manual_mode=True,
    )
    assert should_use_draft_refine(
        mode="on", time_scale="minutes", divergence=0.0, workflow_name="x.json",
    )
    assert not should_use_draft_refine(
        mode="off", time_scale="years", divergence=1.0, workflow_name="x.json",
    )
    assert should_use_draft_refine(
        mode="auto", time_scale="years", divergence=0.0, workflow_name="x.json",
    )
    # auto: hours is no longer skipped (only minutes / tens_of_minutes)
    assert should_use_draft_refine(
        mode="auto", time_scale="hours", divergence=0.2, workflow_name="x.json",
    )
    assert should_use_draft_refine(
        mode="auto", time_scale="days", divergence=0.0, workflow_name="x.json",
    )
    # micro scales skip unless divergence ≥ 0.25
    assert not should_use_draft_refine(
        mode="auto", time_scale="minutes", divergence=0.2, workflow_name="x.json",
    )
    assert should_use_draft_refine(
        mode="auto", time_scale="minutes", divergence=0.25, workflow_name="x.json",
    )


def test_merge_draft_wd14_tags_prefers_draft_scene():
    merged = merge_draft_wd14_tags(
        vocab_tags=["day", "blue_sky", "park", "smile"],
        draft_tags=["night", "moonlight", "rooftop", "brown_hair"],
        lock_tags=["blonde_hair", "blue_eyes"],
        focal=["looking_up"],
    )
    assert merged[0] == "looking_up"
    assert "night" in merged
    assert "moonlight" in merged
    assert "rooftop" in merged
    # wrong hair from draft dropped; lock kept
    assert "brown_hair" not in merged
    assert "blonde_hair" in merged
    # day/blue_sky conflict with night draft → dropped
    assert "day" not in merged
    assert "blue_sky" not in merged
    assert "smile" in merged
    # richness tags (moonlight/night/rooftop) promoted ahead of leftover vocab
    rich_idx = min(merged.index(t) for t in ("night", "moonlight", "rooftop"))
    assert rich_idx < merged.index("smile")


def test_merge_draft_wd14_tags_empty_draft_keeps_vocab():
    merged = merge_draft_wd14_tags(
        vocab_tags=["park", "smile"],
        draft_tags=[],
        lock_tags=["blonde_hair"],
        focal=["waving"],
    )
    assert "waving" in merged
    assert "park" in merged
    assert "blonde_hair" in merged


def test_build_draft_grounding_block_and_delta():
    from app.story.generator import (
        build_draft_grounding_block,
        draft_richness_delta,
    )
    block = build_draft_grounding_block(
        ["golden_hour", "rim_light", "bicycle", "storefront"],
        locale="en",
    )
    assert "DRAFT GROUNDING" in block
    assert "golden_hour" in block
    assert "image model's own expression" in block
    ja = build_draft_grounding_block(["neon", "cafe"], locale="ja")
    assert "下書き接地" in ja
    thin = "1girl, solo, smile, outdoors, day, standing"
    rich = (
        "1girl, solo, smile, outdoors, street, storefront, cafe, "
        "bicycle, riding, sunset, golden_hour, rim_light, scarf"
    )
    delta = draft_richness_delta(before_tag_line=thin, after_tag_line=rich)
    assert delta["after"] > delta["before"]
    assert delta["delta"] > 0.1
    assert delta["draft_lighting"] >= 1
    assert delta["draft_environment"] >= 1


def test_build_axis_prose_prompt_includes_draft_grounding():
    from app.story.generator import build_draft_grounding_block
    g = build_draft_grounding_block(["sunset", "bicycle", "rim_light"])
    prompt = build_axis_prose_prompt(
        story_text="She rides home at dusk.",
        tag_line="1girl, riding_bicycle, sunset",
        character_tags=["blonde_hair"],
        character_desc="blonde girl",
        prompt_style="danbooru+natural",
        draft_grounding=g,
    )
    assert "DRAFT GROUNDING" in prompt
    assert "Prefer DRAFT GROUNDING" in prompt
    assert "rim_light" in prompt


# ── user topic concretization (お題 narrative directive) ──────────────────────

def test_build_topic_directive_prompt():
    en = build_topic_directive_prompt("exploring abandoned ruins", locale="en")
    assert "exploring abandoned ruins" in en
    assert "DIRECTIVE" in en
    # narrative, not visual tags; broad/long spans not shackled
    assert "NOT appearance or danbooru tags" in en
    assert "THEME the three acts explore freely" in en
    ja = build_topic_directive_prompt("廃墟を探索する", locale="ja", time_scale="decades")
    assert "廃墟を探索する" in ja
    assert "方針" in ja and "テーマ" in ja
    # the elapsed span is threaded in per scale
    assert "several decades" in ja
    assert "a few years" in build_topic_directive_prompt("x", locale="en")


def test_candidates_prompt_topic_hoisted_above_base_image():
    prompt = build_candidates_prompt(
        character_desc="1girl",
        scene_desc="UNIQUE_SCENE_MARKER a sunlit classroom",
        user_topic="廃墟を探索する冒険",
        topic_directive="少女は忘れられた廃墟を探索し、隠された過去に触れていく。",
        locale="ja",
        time_scale="years",
    )
    # Topic leads the prompt (above HARD RULES / seeds / base image).
    topic_at = prompt.index("★ USER TOPIC")
    rules_marker = "【最優先ルール" if "【最優先ルール" in prompt else "HARD RULES"
    assert topic_at < prompt.index(rules_marker)
    assert topic_at < prompt.index("UNIQUE_SCENE_MARKER")
    # raw topic + narrative directive both present
    assert "廃墟を探索する冒険" in prompt
    assert "隠された過去に触れていく" in prompt
    # base image is explicitly scoped to LOOK-only
    assert "fixes the base act's LOOK only" in prompt
    # ongoing-action tense guidance preserved from the old topic_line
    assert "tense and aspect" in prompt
    # HARD RULES carve-out for topics
    assert "お題がある場合" in prompt or "USER TOPIC is given" in prompt
    # All three spirits mention topic compatibility
    assert prompt.count("USER TOPIC") >= 3


def test_candidates_prompt_topic_without_directive():
    # directive omitted → still hoists the raw topic prominently
    prompt = build_candidates_prompt(
        character_desc="1girl", scene_desc="a room", user_topic="放課後の冒険",
    )
    assert "★ USER TOPIC" in prompt
    assert "放課後の冒険" in prompt
    # empty topic → no topic block, generic invent-your-own line
    empty = build_candidates_prompt(character_desc="1girl", scene_desc="a room")
    assert "★ USER TOPIC" not in empty
    assert "No topic was given" in empty


def test_candidates_off_topic_gate():
    tokens = topic_anchor_tokens("廃墟を探索する冒険", "少女は廃墟を歩く")
    assert any("廃墟" in t for t in tokens)
    on_topic = [{
        "id": "A",
        "past": "廃墟の入口で懐中電灯を確かめる",
        "present": "崩れた廊下を探索する",
        "future": "地下で古い地図を見つける",
    }] * 3
    assert not candidates_off_topic(on_topic, "廃墟を探索する冒険")
    off = [{
        "id": "A",
        "past": "教室でノートを開く",
        "present": "窓辺で空を眺める",
        "future": "放課後に友人と帰る",
    }] * 3
    assert candidates_off_topic(off, "廃墟を探索する冒険")
    assert not candidates_off_topic(off, "")


def test_topic_anchor_tokens_bilingual_cafe():
    tokens = topic_anchor_tokens("この子がカフェで働く話")
    assert "カフェ" in tokens
    assert "cafe" in tokens
    # English beats with cafe should pass a Japanese お題
    en_cafe = [{
        "id": "A",
        "past": "She spills milk at the cafe counter",
        "present": "She pours latte art at the cafe",
        "future": "She trains a junior barista",
        "title": "Cafe years",
    }] * 3
    assert not candidates_off_topic(en_cafe, "この子がカフェで働く話")


def test_topic_anchor_tokens_festival_multi():
    tokens = topic_anchor_tokens("夏祭りで遊ぶ三人の少女")
    assert "夏祭" in tokens or "祭り" in tokens
    assert "festival" in tokens
    assert "3girls" in tokens or "trio" in tokens
    en = [{
        "id": "A",
        "past": "Three girls buy squid at the summer festival",
        "present": "The trio races under paper lanterns",
        "future": "They share candy under fireworks at the festival",
        "title": "Matsuri",
    }] * 3
    assert not candidates_off_topic(en, "夏祭りで遊ぶ三人の少女")


def test_topic_anchor_tokens_single_kanji_rain_and_rooftop():
    """1-kanji cues (雨/駅/星) and 屋上 must expand to EN for off-topic gating."""
    rain = topic_anchor_tokens("雨の駅で待ち合わせ")
    assert "rain" in rain and "station" in rain
    assert any(t in rain for t in ("wait", "waiting", "meet", "meeting"))
    rain_en = [{
        "id": "A",
        "past": "She waits on the rainy station platform",
        "present": "She waves under an umbrella at the station",
        "future": "They leave the station steps in the rain",
        "title": "Platform",
    }] * 3
    assert not candidates_off_topic(rain_en, "雨の駅で待ち合わせ")

    roof = topic_anchor_tokens("屋上で星を見る")
    assert "rooftop" in roof and "star" in roof
    roof_en = [{
        "id": "A",
        "past": "She opens the school rooftop door at night",
        "present": "She points at a bright star on the rooftop",
        "future": "She counts constellations on the rooftop",
        "title": "Stars",
    }] * 3
    assert not candidates_off_topic(roof_en, "屋上で星を見る")
    assert candidates_off_topic(
        [{
            "id": "A",
            "past": "She kneads dough in a quiet kitchen",
            "present": "She folds pastry on the counter",
            "future": "She bakes bread at dawn",
            "title": "Kitchen",
        }] * 3,
        "屋上で星を見る",
    )


def test_multi_character_drops_hair_eye_locks():
    wd14 = [
        "3girls", "multiple_girls", "blonde_hair", "black_hair",
        "blue_eyes", "brown_eyes", "yukata", "hair_ornament",
    ]
    assert is_multi_character(wd14)
    lock = identity_lock_tags(wd14, multi_character=True)
    assert "blonde_hair" not in lock and "blue_eyes" not in lock
    solo_lock = identity_lock_tags(
        ["1girl", "solo", "blonde_hair", "blue_eyes", "hair_ornament"],
        multi_character=False,
    )
    assert "blonde_hair" in solo_lock and "blue_eyes" in solo_lock


def test_axis_tag_lines_collapsed_detects_paraphrase_overlap():
    from app.story.generator import axis_tag_lines_collapsed
    same = (
        "1girl, holding, kitchen, dough, flour, wooden_board, indoors, "
        "silver_hair, blue_eyes, solo, looking_at_viewer, detailed_background"
    )
    assert axis_tag_lines_collapsed(
        {"past": same, "present": same, "future": same}
    )
    diverse = {
        "past": (
            "1girl, spilling, milk, pitcher, apron, cafe, morning, towel, "
            "counter, reaching, silver_hair"
        ),
        "present": (
            "1girl, pouring, latte_art, coffee_cup, cafe, window, steam, "
            "ceramic, holding, silver_hair"
        ),
        "future": (
            "1girl, wiping, pointing, teaching, espresso_machine, evening, "
            "cloth, back_bar, silver_hair"
        ),
    }
    assert not axis_tag_lines_collapsed(diverse)


def test_coherence_hierarchy_scopes_image_vs_topic():
    block = _coherence_hierarchy_block(
        base_axis="present", user_topic="a secret journey", time_scale="years",
    )
    # base image scoped to LOOK; topic drives the subject
    assert "fixes only how" in block and "LOOKS" in block
    assert "what the STORY IS ABOUT" in block
    assert "these two do not conflict" in block


def test_story_and_expand_thread_topic_directive():
    directive = "DIRECTIVE_MARKER_XYZ she hunts a truth across years"
    story = build_story_prompt(
        character_desc="1girl", scene_desc="s", base_axis="present", worldview="",
        user_topic="真実を追う", topic_directive=directive,
    )
    assert directive in story
    assert "what the STORY IS ABOUT across all three acts" in story
    expand = build_expand_prompt(
        selected={"id": "A", "title": "T", "past": "p", "present": "pr",
                  "future": "f", "motif": "m"},
        character_desc="1girl", scene_desc="s", base_axis="present", worldview="",
        user_topic="真実を追う", topic_directive=directive,
    )
    assert directive in expand
    # empty directive → no directive line, no crash
    plain = build_story_prompt(
        character_desc="1girl", scene_desc="s", base_axis="present", worldview="",
        user_topic="真実を追う",
    )
    assert "Story direction distilled from the topic" not in plain


# ── biography / timetable / concrete activities (life-grounding) ──────────────

def test_build_and_parse_biography():
    prompt = build_biography_prompt(
        character_desc="[visual tags] 1girl, silver_hair",
        scene_desc="a classroom", wd14_tags=["1girl", "book"], worldview="",
        locale="ja",
    )
    assert "BIOGRAPHY" in prompt
    assert "do NOT restate" in prompt  # appearance excluded
    assert "favourite_items" in prompt and "hobbies" in prompt
    bio = parse_biography_json(
        '{"personality":"kind","occupation":"baker","hobbies":["baking",""],'
        '"favourite_items":["rolling pin"],"likes":["bread"],"dislikes":[],'
        '"quirks":["hums"],"backstory":"grew up in a bakery"}'
    )
    assert bio["personality"] == "kind" and bio["occupation"] == "baker"
    assert bio["hobbies"] == ["baking"]  # blanks dropped
    assert bio["favourite_items"] == ["rolling pin"]
    # Truncated JA translations may collapse a list into a bare string.
    assert parse_biography_json({"hobbies": "読書", "personality": "温和"})["hobbies"] == ["読書"]
    # broken / empty → {}
    assert parse_biography_json("junk") == {}
    assert parse_biography_json('{"unrelated":1}') == {}


def test_sample_bio_domains():
    import random
    d = sample_bio_domains(5, rng=random.Random(0))
    assert len(d) == 5 and len(set(d)) == 5
    # deterministic with a fixed rng; different seeds differ
    assert sample_bio_domains(5, rng=random.Random(0)) == d
    assert sample_bio_domains(5, rng=random.Random(9)) != d
    assert sample_bio_domains(0) == []


def test_biography_prompt_diversity_no_fixed_examples():
    p = build_biography_prompt(
        character_desc="1girl", scene_desc="a rooftop", wd14_tags=["book"],
        inspiration_domains=["rock climbing", "amateur astronomy"],
    )
    assert "VARIETY" in p
    assert "rock climbing" in p and "amateur astronomy" in p
    # the old fixed examples that anchored every bio are gone
    assert "kneads bread dough" not in p
    assert "tunes a violin" not in p
    assert "presses flowers" not in p


def test_assign_dramatic_modes_tone():
    import random
    bright = assign_dramatic_modes(tone="bright", rng=random.Random(3))
    assert len(set(bright.values())) == 3
    assert all(v in _BRIGHT_MODE_KEYS for v in bright.values())
    dark = assign_dramatic_modes(tone="dark", rng=random.Random(3))
    assert len(set(dark.values())) == 3
    assert all(v in _DARK_MODE_KEYS for v in dark.values())
    # preferred is still pinned regardless of tone
    pinned = assign_dramatic_modes(preferred="parting", tone="bright", rng=random.Random(4))
    assert pinned["A"] == "parting"
    assert len(set(pinned.values())) == 3


def test_bright_modes_exist():
    for m in ("discovery", "reunion", "breakthrough", "adventure", "kindness",
              "mischief", "bloom"):
        assert m in _BRIGHT_MODE_KEYS
        assert _dramatic_mode_line(m)  # has guidance text
        assert _dramatic_mode_line(m, "ja")


def test_tone_line_and_ending_hooks():
    assert "hopeful" in _tone_line("bright") and "grim" in _tone_line("bright")
    assert "希望" in _tone_line("bright", "ja")
    assert _tone_line("neutral") and _tone_line("dark")
    assert _tone_line("") == _tone_line("bright")  # default
    # ending hooks are tone-aware
    assert "new opportunity" in _ending_policy_block("", "bright")
    assert "parting on the brink" in _ending_policy_block("", "dark")


def test_tone_threaded_into_prompts():
    c = build_candidates_prompt(character_desc="1girl", scene_desc="s", tone="bright")
    assert "TONE:" in c
    assert "surprise does NOT have to mean darkness" in c
    s = build_story_prompt(
        character_desc="1girl", scene_desc="s", base_axis="present",
        worldview="", tone="bright",
    )
    assert "TONE:" in s and "new opportunity" in s
    d = build_story_prompt(
        character_desc="1girl", scene_desc="s", base_axis="present",
        worldview="", tone="dark",
    )
    assert "parting on the brink" in d


def test_build_timetable_scale_adaptive():
    # window covers + slices the chosen axis, centred on "now"
    tens = build_timetable_prompt(
        biography={"hobbies": ["baking"]}, scene_desc="kitchen",
        time_scale="tens_of_minutes",
    )
    assert "~2 hours AROUND this moment" in tens
    assert "20 minutes apart" in tens
    assert "MIDDLE" in tens and "drawable" in tens
    minutes = build_timetable_prompt(
        biography={"hobbies": ["baking"]}, scene_desc="kitchen", time_scale="minutes",
    )
    assert "~30 minutes AROUND this moment" in minutes
    life = build_timetable_prompt(
        biography={"hobbies": ["baking"]}, scene_desc="kitchen", time_scale="years",
    )
    assert "several YEARS of her life" in life
    # unknown scale falls back to years window
    assert build_timetable_prompt(
        biography={}, scene_desc="x", time_scale="bogus",
    ) == build_timetable_prompt(biography={}, scene_desc="x", time_scale="years")


def test_build_timetable_is_story_driven():
    # the chosen story + scene drive the table, not generic hobbies
    tt = build_timetable_prompt(
        biography={"hobbies": ["knitting"]},
        scene_desc="a sunlit classroom by the window",
        time_scale="hours",
        selected={"title": "Shadow and Bandana",
                  "past": "she leaves her seat", "present": "she stands by the window",
                  "future": "he never appears"},
        user_topic="放課後",
    )
    assert "CHOSEN STORY" in tt
    assert "Shadow and Bandana" in tt and "stands by the window" in tt
    assert "放課後" in tt
    # scene grounding + hobby guard
    assert "sunlit classroom by the window" in tt
    assert "do NOT invent hobbies" in tt
    slots = parse_timetable_json(
        '{"slots":[{"label":"morning","activity":"kneads dough","place":"kitchen",'
        '"feeling":"calm"},{"nope":1},{"label":"","activity":""}]}'
    )
    assert len(slots) == 1 and slots[0]["activity"] == "kneads dough"
    assert parse_timetable_json("junk") == []


def test_build_concrete_activities():
    prompt = build_concrete_activities_prompt(
        biography={"hobbies": ["baking"], "favourite_items": ["rolling pin"]},
        timetable=[{"label": "morning", "activity": "kneads dough",
                    "place": "kitchen", "feeling": "calm"}],
        selected={"past": "p", "present": "pr", "future": "f"},
        scene_desc="kitchen", base_axis="present", time_scale="hours",
        user_topic="放課後",
    )
    assert "drawable" in prompt and "NEVER standing" in prompt
    assert "kneads dough" in prompt and "放課後" in prompt
    acts = parse_concrete_activities_json(
        '{"past":"kneading dough","present":"pulling bread from the oven",'
        '"future":"boxing loaves"}'
    )
    assert acts["past"] == "kneading dough" and acts["future"] == "boxing loaves"
    assert parse_concrete_activities_json("junk") == {}


def test_json_translation_prompt():
    p = build_json_translation_prompt({"a": "hello"}, target="Japanese")
    assert "Japanese" in p and "hello" in p and "KEYS" in p


def test_expand_prompt_weaves_biography_timetable():
    prompt = build_expand_prompt(
        selected={"id": "A", "title": "T", "past": "p", "present": "pr",
                  "future": "f", "motif": "m"},
        character_desc="1girl", scene_desc="kitchen", base_axis="present",
        worldview="",
        biography={"favourite_items": ["rolling pin"], "hobbies": ["baking"]},
        timetable=[{"label": "morning", "activity": "kneads dough"}],
    )
    assert "CHARACTER BIOGRAPHY" in prompt
    assert "rolling pin" in prompt and "kneads dough" in prompt
    assert "HARD RULES" in prompt
    assert "Daily/life rhythm" not in prompt
    # absent → no blocks
    plain = build_expand_prompt(
        selected={"id": "A", "title": "T", "past": "p", "present": "pr",
                  "future": "f", "motif": "m"},
        character_desc="1girl", scene_desc="kitchen", base_axis="present",
        worldview="",
    )
    assert "CHARACTER BIOGRAPHY" not in plain


def test_candidates_prompt_leads_with_hard_rules_and_seed_tags():
    p = build_candidates_prompt(
        character_desc="1girl",
        scene_desc="a cafe counter",
        seed_tags=["coffee_cup", "apron", "paper"],
        forced_motif="paper",
        biography={"hobbies": ["latte art"]},
    )
    assert p.lstrip().startswith("HARD RULES")
    assert "SEED TAGS" in p and "coffee_cup" in p and "paper" in p
    assert "latte art" in p
    assert "grounded_tags" in p


def test_parse_candidates_grounded_tags():
    raw = (
        '{"candidates":[{"id":"A","title":"T","past":"p","present":"pr",'
        '"future":"f","motif":"m","turn":"t","grounded_tags":["coffee_cup","apron"]}]}'
    )
    c = parse_candidates_json(raw)[0]
    assert c["grounded_tags"] == ["coffee_cup", "apron"]


def test_candidates_ungrounded_gate():
    seed = ["a", "b", "c", "d"]
    assert candidates_ungrounded(
        [{"grounded_tags": ["a"]}, {"grounded_tags": []}, {"grounded_tags": []}],
        seed,
    )
    assert not candidates_ungrounded(
        [
            {"grounded_tags": ["a", "b"]},
            {"grounded_tags": ["b", "c"]},
            {"grounded_tags": ["a", "c"]},
        ],
        seed,
    )
    assert not candidates_ungrounded([], ["a"])  # seed too small → skip gate


def test_bind_timetable_axis_slots_prefers_axis_field():
    slots = bind_timetable_axis_slots([
        {"axis": "past", "label": "-1h", "activity": "tamp", "place": "bar", "feeling": "x"},
        {"axis": "present", "label": "now", "activity": "serve", "place": "c", "feeling": "y"},
        {"axis": "future", "label": "+1h", "activity": "fold", "place": "t", "feeling": "z"},
        {"axis": "bridge", "label": "+30m", "activity": "wipe", "place": "c", "feeling": "q"},
    ])
    assert set(slots) == {"past", "present", "future"}
    assert slots["past"]["activity"] == "tamp"


def test_bind_timetable_axis_slots_distinct_when_base_is_future():
    """Regression: base_axis=future must not reuse the mid slot for present."""
    # No axis fields / no past|present|future label cues → chronological thirds.
    raw = [
        {"label": "slot0", "activity": "open", "place": "door", "feeling": "a"},
        {"label": "slot1", "activity": "wait", "place": "hall", "feeling": "b"},
        {"label": "slot2", "activity": "meet", "place": "roof", "feeling": "c"},
        {"label": "slot3", "activity": "leave", "place": "stair", "feeling": "d"},
        {"label": "slot4", "activity": "home", "place": "room", "feeling": "e"},
    ]
    for base in ("past", "present", "future"):
        slots = bind_timetable_axis_slots(raw, base_axis=base)
        assert set(slots) == {"past", "present", "future"}
        acts = {slots[a]["activity"] for a in ("past", "present", "future")}
        assert len(acts) == 3, f"base={base} collided: {slots}"
        assert slots["past"]["activity"] == "open"
        assert slots["present"]["activity"] == "meet"
        assert slots["future"]["activity"] == "home"


def test_topic_only_grounding_prompt_and_parse():
    from app.story.generator import (
        build_topic_only_grounding_prompt,
        parse_topic_only_grounding_json,
    )
    p = build_topic_only_grounding_prompt(user_topic="雨の駅で待ち合わせ")
    assert "NO reference image" in p
    assert "雨の駅" in p
    parsed = parse_topic_only_grounding_json(
        '{"character_desc": "silver hair", "scene_desc": "wet platform", '
        '"wd14_tags": ["1girl", "solo", "umbrella", "rain"]}'
    )
    assert parsed["scene_desc"] == "wet platform"
    assert "umbrella" in parsed["wd14_tags"]
    assert parse_topic_only_grounding_json("not json") == {}
    # Reasoning models may wrap JSON in <think>…</think>.
    wrapped = parse_topic_only_grounding_json(
        '<think>planning…</think>\n'
        '{"character_desc": "red eyes", "scene_desc": "rooftop", '
        '"wd14_tags": ["1girl", "night"]}'
    )
    assert wrapped["scene_desc"] == "rooftop"
    assert "night" in wrapped["wd14_tags"]


def test_topic_only_axis_image_id_rule():
    """Empty base_image_id → no axis reuses a source SHA (mirrors db.new_story_payload)."""
    base_image_id = ""
    base_time_axis = "present"
    for axis in ("past", "present", "future"):
        image_id = (
            base_image_id if base_image_id and axis == base_time_axis else None
        )
        assert image_id is None
    # With a real base image, only the chosen axis keeps the SHA.
    base_image_id = "abc123"
    mapped = {
        a: (base_image_id if base_image_id and a == base_time_axis else None)
        for a in ("past", "present", "future")
    }
    assert mapped == {"past": None, "present": "abc123", "future": None}


def test_filter_story_seed_pool_drops_generic():
    pool = filter_story_seed_pool(
        ["1girl", "coffee_cup", "solo", "espresso_machine", "looking_at_viewer"],
        removal={"bad_tag"},
    )
    low = {t.lower() for t in pool}
    assert "coffee_cup" in low and "espresso_machine" in low
    assert "1girl" not in low and "solo" not in low


def test_translation_values_complete_and_chunk_list():
    assert translation_values_complete({"a": "hello world"}, {"a": "こんにちは世界"})
    assert not translation_values_complete({"a": "hello world"}, {"a": ""})
    assert chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_story_sections_complete_and_merge():
    from app.story.generator import merge_story_sections, story_sections_complete

    complete = {
        "title": "Rain Letter",
        "overall": "A letter changes everything.",
        "past": "彼女は雨の駅で手紙を握りしめていた。",
        "present": "今、その封を開ける指が少し震えている。",
        "future": "数年後、同じ駅で彼女は返事を書く。",
    }
    assert story_sections_complete(complete)

    truncated = {**complete, "future": "数年後、同じ駅で彼女は「"}
    assert not story_sections_complete(truncated)

    missing = {**complete, "past": "短い"}
    assert not story_sections_complete(missing)
    assert not story_sections_complete({**complete, "future": ""})

    merged = merge_story_sections(
        {"title": "", "overall": "", "past": "A" * 30, "present": "", "future": ""},
        {"title": "T", "overall": "O", "past": "old", "present": "B" * 30, "future": "C" * 30},
    )
    assert merged["title"] == "T"
    assert merged["past"].startswith("A")
    assert merged["present"].startswith("B")


def test_timetable_prompt_requests_axis_field():
    tt = build_timetable_prompt(
        biography={}, scene_desc="cafe", time_scale="hours",
        selected={"title": "T", "past": "p", "present": "pr", "future": "f"},
    )
    assert '"axis"' in tt or "axis" in tt


def test_bind_timetable_slots_include_index_and_marks():
    raw = [
        {"axis": "past", "label": "-1h", "activity": "tamp", "place": "bar", "feeling": "x"},
        {"axis": "bridge", "label": "-30m", "activity": "wipe", "place": "c", "feeling": "q"},
        {"axis": "present", "label": "now", "activity": "serve", "place": "c", "feeling": "y"},
        {"axis": "bridge", "label": "+30m", "activity": "count", "place": "c", "feeling": "m"},
        {"axis": "future", "label": "+1h", "activity": "fold", "place": "t", "feeling": "z"},
    ]
    bound = bind_timetable_axis_slots(raw)
    assert bound["past"]["index"] == 0
    assert bound["present"]["index"] == 2
    assert bound["future"]["index"] == 4
    assert axis_slots_ready(bound)
    sit = situation_from_axis_slots(bound)
    assert "tamp" in sit["past"] and "serve" in sit["present"]
    marked = apply_timetable_slot_marks([dict(s) for s in raw], bound)
    assert marked[0]["used_as"] == "past"
    assert marked[2]["used_as"] == "present"
    assert marked[4]["used_as"] == "future"
    nbrs = timetable_neighbors(marked, bound, radius=1)
    assert any(n.get("activity") == "wipe" for n in nbrs["present"])
    assert any(n.get("activity") == "count" for n in nbrs["present"])
    assert marked[1].get("used_as_neighbor")  # wipe used as neighbor of present


def test_visual_examination_slot_is_primary():
    slot = {
        "index": 1, "label": "now", "activity": "pours milk",
        "place": "counter", "feeling": "calm",
    }
    prompt = build_visual_examination_prompt(
        story_text="A quiet morning mood.",
        axis="present", base_axis="present", time_scale="hours",
        axis_slot=slot,
        neighbors=[{"label": "-20m", "activity": "opens fridge", "place": "kitchen"}],
    )
    assert "ON-SCREEN FACT" in prompt
    assert "pours milk" in prompt
    assert "opens fridge" in prompt
    # Mood text is secondary
    assert "quiet morning" in prompt


def test_timetable_prompt_prefers_topic_motif_turn():
    tt = build_timetable_prompt(
        biography={"hobbies": ["knitting"]},
        scene_desc="classroom",
        time_scale="hours",
        selected={
            "title": "Shadow Bandana",
            "motif": "bandana",
            "turn": "the name on the slip is wrong",
            "past": "vague hint past",
            "present": "vague hint now",
            "future": "vague hint later",
        },
        user_topic="放課後の呼び止め",
        topic_directive="Catch him at the gate before the last bell.",
    )
    assert "SOURCE OF TRUTH" in tt
    assert "Shadow Bandana" in tt and "bandana" in tt
    assert "the name on the slip is wrong" in tt
    assert "HINT beats" in tt
    assert "放課後の呼び止め" in tt
    assert "Catch him at the gate" in tt


def test_visual_plan_to_tags_materialises_shot_and_lighting():
    tags = visual_plan_to_tags({
        "focal_action_tags": ["reaching", "holding_cup"],
        "expression_tag": "smile",
        "shot": "cowboy_shot",
        "camera_angle": "from_side",
        "props": ["coffee_cup"],
        "lighting": "warm golden hour rim light",
    })
    assert "reaching" in tags
    assert "cowboy_shot" in tags
    assert "from_side" in tags
    assert "coffee_cup" in tags
    assert "golden_hour" in tags
    assert visual_plan_to_tags(None) == []
    assert visual_plan_to_tags({}) == []


def test_build_axis_prose_prompt_lean_is_short():
    lean = build_axis_prose_prompt_lean(
        story_text="She pours milk at the counter.",
        tag_line="1girl, pouring, milk, smile",
        axis="present",
        visual_plan={
            "focal_action_tags": ["pouring"],
            "gesture_prose": "tilts the pitcher",
        },
        user_topic="cafe morning",
    )
    assert "60 words" in lean
    assert "Do NOT output a leading danbooru tag line" in lean
    assert "Do NOT output labeled *_TAGS" in lean
    assert "FULL CHRONICLE CONTEXT" not in lean
    assert "AUTHORITATIVE TAGS" in lean
    assert "pours milk" in lean


def test_merge_without_search_keeps_plan_and_lock():
    from app.story.generator import merge_chronicle_axis_tags
    plan = visual_plan_to_tags({
        "focal_action_tags": ["reaching"],
        "expression_tag": "serious",
        "shot": "upper_body",
    })
    line = merge_chronicle_axis_tags(
        focal=plan, search_tags=[], lock_tags=["black_hair", "green_eyes"],
    )
    assert "reaching" in line
    assert "black_hair" in line
    assert "upper_body" in line

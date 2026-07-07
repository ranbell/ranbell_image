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
    build_axis_prompt,
    build_candidates_prompt,
    build_expand_prompt,
    build_overall_prompt,
    build_story_prompt,
    build_story_repair_prompt,
    build_story_tags_prompt,
    build_title_prompt,
    build_translation_to_english_prompt,
    build_vision_prompt,
    character_tags_from_wd14,
    classify_identity_tag,
    collect_prompt_tags,
    identity_tags_for_scale,
    inject_identity_tags,
    is_multi_character,
    parse_candidates_json,
    parse_english_translation_json,
    parse_story_json,
    parse_story_sections,
    parse_tags_json,
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
    assert "NON-NEGOTIABLE" in minutes
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
    assert "comma-separated danbooru tag list (30-50 tags)" in tags_only
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
    assert classify_identity_tag("school_uniform") == "outfit"
    assert classify_identity_tag("black_dress") == "outfit"
    assert classify_identity_tag("hair_ribbon") == "outfit"
    # scene / pose / composition / time-of-day tags are never identity
    for tag in ("sitting", "indoors", "night", "window", "from_behind",
                "standing", "outdoors", "sunset", "cityscape", "looking_at_viewer"):
        assert classify_identity_tag(tag) is None, tag


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
    assert "turning point or reversal" in prompt
    assert "TRANSFORMS in meaning" in prompt
    assert "cause and effect" in prompt
    assert "different dominant emotion" in prompt


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
    # Base axis keeps WD14, but framed as style-only with the story as PRIMARY
    assert "[common tags] 1girl" in prompt
    assert "PRIMARY source for content" in prompt


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
    # base image is bound to the base axis; other acts are offset in time
    assert "THE BASE IMAGE IS THE [PRESENT] MOMENT" in prompt
    assert "BEFORE the image" in prompt and "AFTER the image" in prompt
    # per-axis output schema (beats), not a single summary
    assert '"past"' in prompt and '"present"' in prompt and '"future"' in prompt
    assert '"summary"' not in prompt
    # grounding guardrail keeps surprise in the real-world register
    assert "GROUNDING" in prompt and "supernatural" in prompt
    # minutes scale = image+alpha continuation, no scene jump
    assert "EXTENDED slightly" in prompt and "HOW MUCH CHANGES" in prompt


def test_candidates_prompt_base_axis_directions():
    # base=past → the other two acts are both AFTER the image
    past_base = build_candidates_prompt(
        character_desc="c", scene_desc="s", base_axis="past", time_scale="hours",
    )
    assert "THE BASE IMAGE IS THE [PAST] MOMENT" in past_base
    assert past_base.count("AFTER the image") == 2
    assert "BEFORE the image" not in past_base


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
    assert "a few minutes apart" in minutes and "several decades apart" in decades
    assert minutes != decades


def test_parse_candidates_json_clean():
    raw = (
        '{"candidates": ['
        '{"id":"A","title":"T1","past":"p1","present":"pr1","future":"f1","key_motif":"m1"},'
        '{"id":"B","title":"T2","past":"p2","present":"pr2","future":"f2","key_motif":"m2"}'
        ']}'
    )
    out = parse_candidates_json(raw)
    assert len(out) == 2
    assert out[0]["id"] == "A" and out[0]["title"] == "T1"
    assert out[0]["past"] == "p1" and out[0]["future"] == "f1"
    # summary is derived from the present beat when not provided
    assert out[0]["summary"] == "pr1"


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
                  "key_motif": "bronze bell"},
        character_desc="[visual tags] 1girl",
        scene_desc="a belfry",
        base_axis="present",
        worldview="",
        time_scale="years",
        locale="ja",
    )
    # the chosen candidate's beats seed the expansion, in Japanese, keeping markers
    assert "The Bell" in prompt and "bronze bell" in prompt
    assert "a bell is cast" in prompt and "a bell cracks" in prompt
    assert "日本語" in prompt
    assert "[TITLE]" in prompt and "[PAST]" in prompt


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

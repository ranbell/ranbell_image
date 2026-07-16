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
    _chronicle_tags_degenerate,
    _elapsed_time_header,
    _text_similarity,
    _tone_line,
    acts_temporally_distinct,
    apply_scene_constraints,
    assign_dramatic_modes,
    topic_anchor_tokens,
    filter_story_seed_pool,
    find_identity_mutex_conflicts,
    find_mutex_conflict_tags,
    infer_axis_scene_constraints,
    merge_draft_wd14_tags,
    activities_temporally_distinct,
    parse_candidates_json,
    should_differentiate_acts,
    translation_values_complete,
    build_vision_prompt,
    build_json_translation_prompt,
    character_tags_from_wd14,
    classify_identity_tag,
    collect_prompt_tags,
    identity_lock_tags,
    identity_tags_for_scale,
    parse_biography_json,
    inject_identity_tags,
    is_multi_character,
    remove_conflict_tags,
    split_vision_sections,
)


# ── parse_story_sections ──────────────────────────────────────────────────────


# ── repair pass ───────────────────────────────────────────────────────────────


# ── build_story_prompt ────────────────────────────────────────────────────────


# ── build_axis_prompt ─────────────────────────────────────────────────────────


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


# ── composition variation in build_axis_prompt ────────────────────────────────


# ── Stage 2a: story candidates ────────────────────────────────────────────────


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


# ── pose expressiveness: story action + ACTION-ANCHOR + Stage 3a ──────────────


# ── ongoing-action topic intent (point 3) ─────────────────────────────────────


# ── emotion register option (point 4) ─────────────────────────────────────────


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


# ── Stage 3b Pass 1 (build_axis_tags_prompt) ─────────────────────────────────


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


# ── Sanity: 2-pass output shape when combined ────────────────────────────────


# ── dramatic modes (story-shape dimension) ────────────────────────────────────


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


# ── timeline distinctness helpers (code-side enforcement) ─────────────────────

def test_text_similarity():
    assert _text_similarity("she runs home", "she runs home") == 1.0
    assert _text_similarity("morning bus stop", "sunset hilltop bow") < 0.3
    # empty inputs → 0.0, no crash
    assert _text_similarity("", "anything") == 0.0


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


# ── user topic concretization (お題 narrative directive) ──────────────────────


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


# ── biography / timetable / concrete activities (life-grounding) ──────────────


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


def test_json_translation_prompt():
    p = build_json_translation_prompt({"a": "hello"}, target="Japanese")
    assert "Japanese" in p and "hello" in p and "KEYS" in p


def test_parse_candidates_grounded_tags():
    raw = (
        '{"candidates":[{"id":"A","title":"T","past":"p","present":"pr",'
        '"future":"f","motif":"m","turn":"t","grounded_tags":["coffee_cup","apron"]}]}'
    )
    c = parse_candidates_json(raw)[0]
    assert c["grounded_tags"] == ["coffee_cup", "apron"]


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


# ── compact prompt contracts (small-LLM slim prompts) ─────────────────────────



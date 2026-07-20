"""Unit tests for Chronicle allowlist compose + labeled positives."""
from __future__ import annotations

from app.story.compose import (
    filter_compose_result,
    format_labeled_positive,
    format_summary,
    map_expression,
    resolve_chronicle_identity,
    soft_normalize_tag,
    strip_expression_tags,
)
from app.story.generator import default_act_labels, repair_act_labels


def test_soft_normalize_strips_prefixes():
    assert soft_normalize_tag("white_blouse") == "blouse"
    assert soft_normalize_tag("school_blazer") == "blazer"
    assert soft_normalize_tag("student_cardigan") == "cardigan"
    assert soft_normalize_tag("close_up") == "close-up"


def test_format_summary_no_double_at():
    s = format_summary("now", "pouring coffee", "behind the cafe counter")
    assert s == "now — pouring coffee, behind the cafe counter"
    assert "at behind" not in s


def test_repair_act_labels_always_canonical():
    cand = {
        "acts": {
            "past": {"label": "2 hours earlier", "activity": "x"},
            "present": {"label": "now", "activity": "y"},
            "future": {"label": "weeks later", "activity": "z"},
        }
    }
    repair_act_labels(cand, base_axis="present", time_scale="days", locale="en")
    defaults = default_act_labels("present", "days", "en")
    assert cand["acts"]["past"]["label"] == defaults["past"]
    assert cand["acts"]["future"]["label"] == defaults["future"]
    assert cand["acts"]["present"]["label"] == defaults["present"]


def _sample_acts():
    return {
        "past": {
            "label": "a few days earlier",
            "activity": "sketching",
            "place": "classroom",
            "feeling": "worried",
            "outfit": "blazer, jeans",
        },
        "present": {
            "label": "now",
            "activity": "pouring coffee behind the counter",
            "place": "cafe counter",
            "feeling": "focused",
            "outfit": "apron, blouse",
        },
        "future": {
            "label": "a few days later",
            "activity": "presenting portfolio",
            "place": "gallery indoors",
            "feeling": "proud",
            "outfit": "cardigan, skirt",
        },
    }


def test_filter_compose_present_exclusive_keeps_llm_shots():
    acts = _sample_acts()
    composed = {
        "past": {
            "pose": ["sitting"],
            "outfit": ["blazer", "jeans"],
            "shot": ["upper_body"],
            "effect": ["classroom", "desk", "pouring", "steam"],
        },
        "present": {
            "pose": ["pouring"],
            "outfit": ["apron", "blouse"],
            "shot": ["close-up"],
            "effect": ["cafe", "counter"],
        },
        "future": {
            "pose": ["holding"],
            "outfit": ["cardigan", "skirt"],
            "shot": ["upper_body"],  # duplicate OK — no forced shot rotation
            "effect": ["indoors", "light_rays"],
        },
    }
    out = filter_compose_result(
        composed,
        acts,
        identity=["1girl", "solo", "grey_hair", "red_eyes"],
        base_axis="present",
    )
    assert "pouring" not in out["past"]["effect"]
    assert "steam" not in out["past"]["effect"]
    assert out["present"]["pose"][0] == "pouring"
    assert "apron" in out["present"]["outfit"]
    assert out["future"]["shot"] == ["upper_body"]  # LLM value preserved
    assert "worried" in out["past"]["character"]
    assert "serious" in out["present"]["character"]
    assert "smile" not in out["present"]["character"]


def test_filter_does_not_force_standing_or_cafe():
    acts = {
        "past": {
            "label": "earlier", "activity": "reading", "place": "library",
            "feeling": "calm", "outfit": "cardigan",
        },
        "present": {
            "label": "now", "activity": "writing a letter", "place": "desk",
            "feeling": "focused", "outfit": "blouse",
        },
        "future": {
            "label": "later", "activity": "mailing the letter", "place": "street",
            "feeling": "relieved", "outfit": "coat",
        },
    }
    composed = {
        "past": {"pose": ["reading"], "outfit": ["cardigan"], "shot": ["from_side"], "effect": ["library"]},
        "present": {"pose": [], "outfit": ["blouse"], "shot": ["close-up"], "effect": ["desk", "letter"]},
        "future": {"pose": ["walking"], "outfit": ["coat"], "shot": ["full_body"], "effect": ["street"]},
    }
    out = filter_compose_result(
        composed, acts, identity=["1girl", "solo"], use_allowlist=False,
    )
    assert out["present"]["pose"] == []  # empty stays empty (no standing)
    assert "cafe" not in out["present"]["effect"]
    assert "steam" not in out["present"]["effect"]
    assert "coffee_cup" not in out["present"]["effect"]
    assert "shirt" not in out["present"]["outfit"]  # no shirt fallback when LLM gave blouse


def test_forced_keywords_survive_allowlist():
    acts = _sample_acts()
    composed = {
        "past": {"pose": ["sitting"], "outfit": ["blazer"], "shot": ["upper_body"], "effect": ["desk"]},
        "present": {"pose": ["pouring"], "outfit": ["apron"], "shot": ["close-up"], "effect": ["cafe"]},
        "future": {"pose": ["standing"], "outfit": ["skirt"], "shot": ["cowboy_shot"], "effect": ["indoors"]},
    }
    out = filter_compose_result(
        composed,
        acts,
        identity=["1girl", "solo"],
        forced_keywords={
            "past": ["library", "letter"],
            "present": [],
            "future": ["confetti"],
        },
        use_allowlist=True,
    )
    assert "library" in out["past"]["effect"] or "library" in out["past"]["positive"]
    assert "letter" in out["past"]["positive"]
    assert "confetti" in out["future"]["positive"]


def test_allowlist_off_keeps_unknown_tags():
    acts = _sample_acts()
    composed = {
        "past": {
            "pose": ["sitting"],
            "outfit": ["mystery_cloak"],
            "shot": ["upper_body"],
            "effect": ["secret_archive"],
        },
        "present": {"pose": ["pouring"], "outfit": ["apron"], "shot": ["close-up"], "effect": ["cafe"]},
        "future": {"pose": ["standing"], "outfit": ["skirt"], "shot": ["cowboy_shot"], "effect": ["indoors"]},
    }
    out = filter_compose_result(
        composed,
        acts,
        identity=["1girl", "solo"],
        use_allowlist=False,
    )
    assert "mystery_cloak" in out["past"]["outfit"]
    assert "secret_archive" in out["past"]["effect"]


def test_character_user_overrides_wd14_random():
    ident = resolve_chronicle_identity(
        "blue_hair, green_eyes, smile",
        ["grey_hair", "red_eyes", "glasses"],
        rng=__import__("random").Random(0),
    )
    assert "blue_hair" in ident
    assert "green_eyes" in ident
    assert "smile" not in ident  # expression stripped from appearance
    assert "1girl" in ident


def test_identity_keeps_hair_eyes_style_when_random():
    """Random WD14 path must not drop eye color / hair / style via sample()."""
    wd14 = [
        "grey_hair", "red_eyes", "long_hair", "glasses", "hair_ribbon",
        "earrings", "choker",
    ]
    for seed in range(20):
        ident = resolve_chronicle_identity(
            "",
            wd14,
            rng=__import__("random").Random(seed),
        )
        assert "grey_hair" in ident, seed
        assert "red_eyes" in ident, seed
        assert "long_hair" in ident, seed


def test_identity_user_partial_backfills_from_wd14():
    """User-specified eyes kept; missing hair filled from WD14."""
    ident = resolve_chronicle_identity(
        "red_eyes, long_hair",
        ["grey_hair", "blue_eyes", "short_hair"],
        rng=__import__("random").Random(1),
    )
    assert "red_eyes" in ident
    assert "long_hair" in ident
    assert "grey_hair" in ident  # backfilled hair_color
    assert "blue_eyes" not in ident  # user eyes win; do not add second eye color


def test_map_expression_varies_with_feeling():
    emo = {"serious", "worried", "smile", "sad", "nervous", "expressionless"}
    assert map_expression("focused", emo_allow=emo) == "serious"
    assert map_expression("worried", emo_allow=emo) == "worried"
    assert map_expression("overwhelmed", emo_allow=emo) == "worried"


def test_strip_expression_tags():
    assert strip_expression_tags(["1girl", "grey_hair", "smile", "red_eyes"]) == [
        "1girl", "grey_hair", "red_eyes",
    ]


def test_compose_prompt_has_no_cafe_fewshot():
    from app.story.compose import build_compose_prompt
    p = build_compose_prompt(
        title="t", throughline="x", acts=_sample_acts(), time_scale="days",
        use_allowlist=False,
    )
    assert "FEW-SHOT" not in p
    assert 'Pose=["pouring"]' not in p
    assert 'Pose=["sitting"' not in p
    assert "do NOT copy these tag values" in p
    assert "Do NOT default every axis to sitting" in p


def test_format_labeled_positive_shape():
    text = format_labeled_positive(
        summary="now — pour",
        character=["1girl", "solo", "serious"],
        outfit=["apron"],
        pose=["pouring"],
        shot=["close-up"],
        effect=["cafe"],
    )
    lines = text.splitlines()
    assert len(lines) == 6
    assert lines[0].startswith("Summary:")

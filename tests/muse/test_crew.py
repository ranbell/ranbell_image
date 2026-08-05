"""Fictional Muse roster — cast presets and table-read voice."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import crew

# Situation nouns must never be baked into Muse production copy.
# Themes come from the Showrunner + VLM dialogue — not from code samples.
_SITUATION_BANNED = (
    "水着", "ビキニ", "パラソル", "カフェ", "泳ぐ", "暑さ", "海辺", "ビーチ",
    "懐中電灯", "スタッフベスト", "砂ベージュ", "ターコイズ", "真夏",
    "屋上", "雨上がり",
    "sexy", "sensual", "swimsuit", "bikini", "parasol", "beach", "seaside",
    "flashlight", "rooftop", "wet/dry", "wet_swimsuit", "beach_cafe",
    "thermos", "coffee",
)


def test_resolve_crew_always_ends_with_finisher():
    ids = crew.resolve_crew(preset="classic")
    roles = [crew.role_of(i) for i in ids]
    assert roles[-1] == "finisher"
    assert "beat" in roles
    assert "wardrobe" in roles
    assert "actress" in roles
    assert roles.index("actress") < roles.index("faces")
    # One person per job, never two.
    assert len(roles) == len(set(roles))


def test_resolve_crew_honours_explicit_ids():
    ids = crew.resolve_crew(crew_ids=["lens:teiten", "wardrobe", "unknown"])
    assert ids == ["lens:teiten", "wardrobe:shiwa", "actress:cast", "finisher:maku"]


def test_actress_prompt_pulls_selected_character_personality():
    character = {
        "name": "Sample Lead",
        "name_ja": "サンプル主演",
        "personality": {
            "traits": ["enthusiastic", "sincere"],
            "summary_ja": "いつも本気で話す。",
            "inner_ja": ["ひとりのとき少し静かになる"],
            "likes": ["clear explanations"],
            "dislikes": ["being rushed"],
        },
        "expression_vocab": ["smile", "open_mouth"],
        "gesture_vocab": ["walking", "looking_up"],
    }
    text = crew.actress_system_prompt(character)
    assert "サンプル主演" in text
    assert "enthusiastic" in text
    assert "ひとりのとき少し静かになる" in text
    assert "smile" in text
    assert "FIRST PERSON" in text or "一人称" in text
    assert "never props" in text.lower() or "Never draw likes" in text


def test_system_prompt_keeps_say_tags_scene_and_english_craft():
    text = crew.system_prompt_for("beat")
    assert "OUTPUT FORMAT" in text
    assert "SAY:" in text
    assert "TAGS:" in text
    assert "SCENE:" in text
    assert "English only" in text
    assert "演出" in text and "一秒" in text
    assert "口調 (JA)" in text
    assert "EXAMPLE SAY" in text
    assert "conversation" in text.lower() or "RECENT TABLE TALK" in text
    assert len(crew.MUSES["beat:ichibyou"]["say_examples"]) >= 3
    assert (crew.MUSES["spine:bane"]["voice_ja"]
            != crew.MUSES["faces:mabataki"]["voice_ja"])


def test_production_muse_copy_has_no_situation_specific_anchors():
    """Any theme must work — forbid demo/situation nouns in shipped Muse text."""
    root = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse"
    blobs: list[str] = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".md"}:
            continue
        if "__pycache__" in path.parts:
            continue
        blobs.append(path.read_text(encoding="utf-8"))
    # Also scan Muse UI placeholders (must not name a sample scene).
    locales = Path(__file__).resolve().parents[2] / "frontend" / "src" / "locales"
    for name in ("ja.json", "en.json"):
        text = (locales / name).read_text(encoding="utf-8")
        # Only the muse.themePlaceholder value matters for this rule.
        import json
        data = json.loads(text)
        blobs.append(str((data.get("muse") or {}).get("themePlaceholder") or ""))
    joined = "\n".join(blobs)
    for banned in _SITUATION_BANNED:
        assert banned not in joined, f"situation-specific '{banned}' found in Muse production copy"


def test_finisher_demands_dense_scene():
    text = crew.system_prompt_for("finisher")
    assert "140–200" in text or "140-200" in text
    assert "35–55" in text or "35-55" in text
    assert "Densify" in text or "densify" in text or "EXPAND" in text
    assert "80 words" not in text  # old thin cap must stay gone
    assert "140–200" in crew.OUTPUT or "140-200" in crew.OUTPUT


def test_banter_prompt_is_say_only():
    text = crew.banter_system_prompt_for("hook")
    assert "SAY:" in text
    assert "TAGS" not in text or "Do NOT output TAGS" in text
    assert "heckling" in text.lower() or "SIDE COMMENT" in text


def test_public_roster_has_no_real_creator_names():
    roster = crew.public_roster()
    names = " ".join(m["name"] for m in roster["muses"]).lower()
    # Guard against accidentally shipping real creator shout-outs.
    for banned in ("greg", "rutkowski", "artis", "wlop", "mucha"):
        assert banned not in names
    assert roster["default_preset"] == "standard"
    assert "vivid" in roster["presets"]


def test_every_seat_has_a_job_a_nickname_and_a_taste():
    for mid in crew.MUSES:
        m = crew.MUSES[mid]
        assert m["name_ja"], mid
        assert m["nick_ja"], mid
        assert set(m["taste"]) == {"vivid", "real", "novel"}, mid
        for axis, score in m["taste"].items():
            assert -2 <= score <= 2, f"{mid}.{axis} = {score}"
        assert len(m["say_examples"]) >= 3, mid
        # Three lines that are actually three lines, not one written thrice.
        assert len(set(m["say_examples"])) == len(m["say_examples"]), mid


def test_swapping_the_crew_moves_the_look():
    """The whole reason a person picks a crew."""
    flat = crew.style_direction(crew.PRESETS["flat"])
    real = crew.style_direction(crew.PRESETS["photoreal"])
    loud = crew.style_direction(crew.PRESETS["vivid"])
    classic = crew.style_direction(crew.PRESETS["classic"])

    assert flat["base"] != real["base"]
    assert "flat" in flat["base"]
    assert "semi-realistic" in real["base"]
    assert "vivid" in loud["base"]
    assert "classic composition" in classic["base"]

    assert flat["scores"]["real"] < real["scores"]["real"]
    assert loud["scores"]["vivid"] > classic["scores"]["vivid"]


def test_a_crew_is_averaged_not_summed():
    """Fifteen people are fifteen opinions, not fifteen times the volume."""
    one = crew.style_direction(["gaffer"])
    many = crew.style_direction(["gaffer"] + list(crew.PRESETS["standard"]))
    assert one["scores"]["vivid"] >= many["scores"]["vivid"]
    assert -2 <= many["scores"]["vivid"] <= 2


def test_the_showrunner_outranks_the_room():
    written = crew.base_style_for(crew.PRESETS["flat"], "watercolour storybook")
    assert written == "watercolour storybook"
    assert crew.base_style_for(crew.PRESETS["flat"], "  ") == \
        crew.style_direction(crew.PRESETS["flat"])["base"]


def test_the_example_line_varies_between_sessions_but_holds_within_one():
    who = "beat:ichibyou"
    seeds = {crew._pick_say_example(who, f"session-{i}") for i in range(40)}
    assert len(seeds) > 1, "every session would sound identical"
    assert crew._pick_say_example(who, "s1") == crew._pick_say_example(who, "s1")


def test_the_base_look_reaches_the_seat_that_guards_style():
    text = crew.system_prompt_for("ink:ipponsen", base_style="vivid flat anime cel shading")
    assert "vivid flat anime cel shading" in text
    assert "You own the base look" in text
    assert "cel_shading" in text  # its own flavour


def test_a_job_has_more_than_one_person_who_does_it():
    """Two lighting artists both light the scene. One hands you hard rim light,
    the other something soft enough to sleep in — that is the range."""
    multi = [r for r in crew.ROLE_ORDER if len(crew.members_of(r)) > 1]
    assert len(multi) >= 12, [crew.ROLES[r]["name_ja"] for r in multi]
    for rid in multi:
        people = [crew.MUSES[m] for m in crew.members_of(rid)]
        nicks = {p["nick_ja"] for p in people}
        assert len(nicks) == len(people), rid
        # Same job, different pull — otherwise the choice is decoration.
        tastes = {tuple(sorted(p["taste"].items())) for p in people}
        assert len(tastes) == len(people), f"{rid}: identical taste"


def test_one_person_per_job_and_a_later_pick_replaces_an_earlier_one():
    ids = crew.resolve_crew(crew_ids=["gaffer:gyakkou", "gaffer:andon"])
    lighting = [i for i in ids if crew.role_of(i) == "gaffer"]
    assert lighting == ["gaffer:andon"]


def test_an_old_session_naming_bare_jobs_still_resolves():
    """Sessions stored crew_ids as job ids before people existed."""
    ids = crew.resolve_crew(crew_ids=["gaffer", "palette"])
    assert ids[:2] == ["gaffer:gyakkou", "palette:itten"]
    assert crew.resolve_member("gaffer") == crew.DEFAULT_MEMBER["gaffer"]
    assert crew.resolve_member("nonsense") == ""


def test_swapping_one_person_moves_the_look():
    """Same jobs, one different person, different picture."""
    hard = crew.style_direction(["gaffer:gyakkou", "palette:itten", "ink:ipponsen"])
    soft = crew.style_direction(["gaffer:andon", "palette:aku", "ink:ipponsen"])
    assert hard["scores"]["vivid"] > soft["scores"]["vivid"]
    assert hard["base"] != soft["base"]
    assert "rim_lighting" in hard["flavor_tags"]
    assert "soft_lighting" in soft["flavor_tags"]


def test_the_roster_groups_people_under_the_job_they_do():
    roster = crew.public_roster(crew_ids=list(crew.PRESETS["flat"]))
    by_id = {r["id"]: r for r in roster["roles"]}
    assert len(roster["roles"]) == len(crew.ROLE_ORDER)
    assert len(by_id["gaffer"]["people"]) == 2
    cast = [p["id"] for r in roster["roles"] for p in r["people"] if p["cast"]]
    assert "ink:ipponsen" in cast and "ink:atsunuri" not in cast

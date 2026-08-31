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
    ids = crew.resolve_crew(preset="calm")
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


def test_finisher_is_off_the_note_path():
    """Notebook-primary: Finisher densify moved to Weave; specialty says so."""
    text = crew.system_prompt_for("finisher")
    assert "OFF the note path" in text
    assert "You do NOT write" in text and "TAGS" in text
    # Legacy OUTPUT still documents dense SCENE for seat-written craft.
    assert "140–200" in crew.OUTPUT or "140-200" in crew.OUTPUT


def test_grade_is_add_only():
    """The Finish seat raises quality; it must not re-cut another seat's work
    (the scorecard had it deleting 17 content tags on its turn)."""
    text = crew.system_prompt_for("grade")
    assert "APPEND" in text or "append" in text
    assert "OFF the note path" in text or "never reorder" in text
    assert "Weave" in text or "FINISH" in text


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
    quiet = crew.style_direction(crew.PRESETS["calm"])

    assert flat["base"] != real["base"]
    assert "flat" in flat["base"]
    assert "semi-realistic" in real["base"]
    assert "vivid" in loud["base"]
    assert "classic composition" in quiet["base"]

    assert flat["scores"]["real"] < real["scores"]["real"]
    assert loud["scores"]["vivid"] > quiet["scores"]["vivid"]


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


# ── the seats that were doing damage ────────────────────────────────────────
def test_the_frame_seat_is_gone_and_nothing_still_points_at_it():
    """「額縁」turned "compose it" into a literal picture frame with a black and
    white border — which is in the negative prompt precisely because nobody
    wants it. The layout job stays; that person does not."""
    assert "cutout:gakubuchi" not in crew.MUSES
    for name, ids in crew.PRESETS.items():
        assert all(m in crew.MUSES for m in ids), name
    text = crew.system_prompt_for(crew.DEFAULT_MEMBER["cutout"])
    for banned in ("silhouette", "negative space", "Carve"):
        assert banned.lower() not in text.lower(), banned
    assert "border" in text.lower() and "frame" in text.lower()


def test_the_choreographer_asks_for_posture_not_contortion():
    """`(neck_tension:1.4)` and `(shoulder_tension:1.3)` shipped from this seat,
    and at that weight the body arches far enough to break the outfit and the
    face. The voice is untouched — it is the instruction that was too strong."""
    text = crew.system_prompt_for("spine:bane")
    assert "BELIEVABLE" in text
    assert "Exaggerate weight shift" not in text
    assert "arch" in text.lower()
    # The coach still sounds like the coach — blunt, fond, about bodies.
    assert "体重" in text
    assert "motion_blur" not in crew.MUSES["spine:bane"]["flavor_tags"]


def test_the_producer_keeps_the_chair_and_loses_the_pen():
    assert "hook" in crew.BANTER_ONLY
    assert "hook" in crew.ROLE_ORDER          # still cast, still heckles
    assert "hook:kugizuke" in crew.MUSES
    # And it is still allowed to talk.
    assert "SAY:" in crew.banter_system_prompt_for("hook:kugizuke")


def test_carry_says_how_things_leave_the_script_not_only_how_they_stay():
    """KEEP with no release is what left a live house's monitors on the floor of
    a karaoke booth after the Showrunner moved the shoot."""
    assert "STRUCK FROM THE SET" in crew.CARRY
    text = crew.system_prompt_for("gate:mon")
    # Gate's TAGS audit moved to strike + Weave scrub; specialty says so.
    assert "obsolete" in text.lower() or "Strike clerk" in text
    assert "You do NOT write" in text and "TAGS" in text


def test_the_camera_states_a_size_instead_of_tightening_each_round():
    text = crew.system_prompt_for("lens:pinto")
    assert "ONE absolute size" in text or "ONE SHOT SIZE" in text
    assert "closer" in text.lower() and "tighter" in text.lower()
    assert "OPTICS" in text or "CRAFT" in text


def test_the_colour_designer_names_colours_instead_of_describing_a_mood():
    """A whole run's contribution from this seat was `desaturated_shadows` and
    `vivid_skin_tones`, both deleted one seat later. A studio colour designer
    fixes a key and hands down named colours; the shadow is a hue, not an
    absence."""
    text = crew.system_prompt_for("palette:itten")
    assert "キートーン" in text
    assert "COLOUR" in text or "CRAFT" in text
    for banned in ("desaturate", "mute", "richer", "cooler", "warmer"):
        assert banned in text, f"{banned} must be named as forbidden"
    # And it still may not touch exposure.
    assert "You do NOT change exposure" in text


def test_both_colourists_state_a_key_rather_than_a_direction_of_change():
    for mid in crew.members_of("palette"):
        examples = " ".join(crew.MUSES[mid]["say_examples"])
        assert "キートーン" in examples, mid
        assert "彩度、落とし" not in examples, mid
        assert "くすませ" not in examples, mid


def test_the_choreographer_no_longer_optimises_against_standing_still():
    """The catchphrase was「棒立ちに見えたら負けだ」and that is what the seat
    optimised: one more degree of lean every round until the hips were above
    the shoulders. Notebook-primary spine proposes BODY via CRAFT — still must
    refuse extreme / stacked tension, and never chase "not standing still".
    """
    text = crew.system_prompt_for("spine:bane")
    assert "棒立ち" not in text
    assert "BELIEVABLE" in text
    assert "Ordinary is correct" in text
    assert "arched_back" in text
    assert "BODY" in text
    assert "TAGS or SCENE" in text  # must not invent classical TAGS authorship
    assert crew.MUSES["spine:bane"]["flavor_tags"] == []


# ── the planner dresses the room, not her ───────────────────────────────────
def test_the_planner_has_no_line_for_clothes():
    """It used to write WEARING, which put a garment one edit from MUST APPEAR,
    where it read as "an object in this room" and got re-chosen for the place.
    Clothes are Wardrobe's now; the planner has no clothing line at all."""
    text = crew.plan_system_prompt()
    assert "WEARING" not in text
    assert "you have no line for clothes" in text
    assert "You do not dress her" in text
    assert "What she wears is Wardrobe's alone, in COSTUME" in text
    assert "OBJECTS IN THE ROOM ONLY" in text
    assert "never clothing" in text
    # It still may not quietly resolve a clash by re-picking the place for a room.
    assert "THE CLOTHES CHOOSE THE PLACE" in text


def test_wardrobe_owns_the_locked_costume_and_reads_the_theme():
    for mid in ("wardrobe:shiwa", "wardrobe:iroawase"):
        text = crew.system_prompt_for(mid)
        assert "COSTUME" in text, mid
        assert "CLOTH" in text or "WEARING" in text, mid
        low = text.lower()
        assert "do not write" in low and "tags" in low, mid
    # Opening / 衣装部屋 still appends the COSTUME block elsewhere.
    assert "SILHOUETTE:" in crew.WARDROBE_COSTUME_TAIL
    # Every seat is told the outfit lives only in COSTUME, Wardrobe's alone.
    assert "lives ONLY in the COSTUME block" in crew.CARRY
    assert "only Wardrobe (衣装)" in crew.CARRY
    # The Lead styles how it is worn; she never swaps a garment.
    lead = crew.actress_system_prompt({"name_ja": "みお"})
    assert "COSTUME is locked" in lead
    assert "never swap a garment" in lead


def test_only_the_showrunner_can_change_the_locked_costume():
    """A later Showrunner order must be able to change her clothes; the room,
    the weather and the other seats must never be able to."""
    # The reading rule every seat gets.
    assert "Never change it, never add or swap a garment" in crew.CARRY
    # Wardrobe on notes argues cloth feel; new outfits land via WEARING.
    w = crew.system_prompt_for("wardrobe:shiwa")
    assert "WEARING" in w
    assert "Never staple" in w or "new outfit" in w.lower() or "衣装部屋" in w


def test_the_ledger_is_a_ceiling_not_a_quota():
    """物の数を決め打ちで求めると、埋め草にゴミが出る。

    実測: 屋上で `empty soda can` / `discarded_chalk` / `empty_plastic_bottle`、
    波打ち際で `empty_crusty_soda_can`。場所に要る物は4〜6個なのに10個以上を
    求めていたので、残りが「生活感のあるゴミ」で埋まっていた。
    """
    text = crew.plan_system_prompt()
    assert "AT MOST twelve" in text
    assert "a ceiling, never a quota" in text
    assert "Ten or more" not in text
    # ゴミは「荒れている場面」だけのもの、と明示されていること。
    assert "Litter and debris" in text
    assert "about neglect" in text


def test_the_look_reaches_the_sampler_as_words_it_knows():
    """班が合意したルックが、絵に届く語になっていること。

    `style_tags("vivid anime illustration")` は1個の巨大トークン
    `vivid_anime_illustration` になっていた。どのチェックポイントも学習して
    いない語で、しかもそれがルックを運ぶ唯一の経路だった。
    """
    from app.muse import identity
    assert crew.look_tags("flat anime cel shading") == [
        "cel_shading", "flat_color", "anime_coloring",
    ]
    assert identity.style_tags("vivid anime illustration") == [
        "anime_coloring", "vivid_colors", "saturated",
    ]
    # 構図の接尾も語を持つ
    assert "dutch_angle" in crew.look_tags("anime illustration, experimental composition")
    assert "rule_of_thirds" in crew.look_tags("anime illustration, classic composition")
    # 総監督が自分で書いた style は従来どおり（表に無いものは分解するだけ）
    assert identity.style_tags("水彩っぽく, やわらかい") == ["水彩っぽく", "やわらかい"]
    # 9セル全部に語がある
    for phrase in crew._BASE_LOOK.values():
        assert crew.look_tags(phrase), phrase


def test_every_shipped_look_is_distinguishable_in_tags():
    """6班が同じタグ束を吐くなら、それは6つの選択肢ではない。"""
    from app.muse import identity
    bags = {
        n: frozenset(identity.style_tags(
            crew.base_style_for(crew.resolve_crew(preset=n), "", "")
        ))
        for n in crew.PRESETS
    }
    assert len(set(bags.values())) == len(bags), bags


def test_the_weave_is_told_the_look_governs_the_whole_bag():
    from app.muse import chain
    # Look colours word choice; it must not licence air-padding over the beat.
    assert "THE LOOK IS HOW YOU WRITE, NOT WHAT YOU PAD WITH" in chain.SCRIPTER_WEAVE_SYSTEM
    assert "ROOM LEANING" in chain.SCRIPTER_WEAVE_SYSTEM
    # 「FIRST DUTY —— 身体と顔」（1,257字）は落とした。実測で、**言わない
    # ほうが身体も顔も書けている**（30本×5回を三周・2026-08-31）:
    #
    #     そのまま     26/30  語数 50
    #     丸ごと落とす  30/30  語数 62   ← 6試験すべて 5/5
    #
    # 身体が先に来る割合は 28/30 → 27/30 で変わらない —— 言わなくても守られ
    # ている。守りは `partner` へ移した（下の試験が見張る）。
    assert "FIRST DUTY" not in chain.SCRIPTER_WEAVE_SYSTEM
    assert "no floor" in chain.SCRIPTER_WEAVE_SYSTEM.lower()


def test_every_seat_that_writes_tags_is_told_how_to_write_lettering():
    """看板の文字の書き方は、TAGS を書く席すべてに届く。"""
    for frame in (
        crew.OUTPUT, crew.DUET_OWNS_THE_FRAME, crew.DUET_OWNS_THE_FRAME_SCOPED,
    ):
        assert "WORDS IN THE PICTURE" in frame
        assert 'text "' in frame
        # 既定は「書かない」。頼まれた時だけ。
        assert "There is no lettering by default" in frame


def test_the_lettering_rule_carries_no_word_a_shot_could_copy():
    """例に挙げた語はそのまま撮影に出てくる。引用符の中は雛形だけにする。"""
    import re
    quoted = re.findall(r'text "([^"]*)"', crew.LETTERING)
    assert quoted == ["<exactly the words they asked for>"], quoted


def test_the_weave_is_told_to_call_a_garment_one_name():
    """gown を dress と言い換えると、二人しかいない画に服が三着になる。"""
    from app.muse import chain
    assert "ONE NAME PER GARMENT" in chain.SCRIPTER_WEAVE_SYSTEM

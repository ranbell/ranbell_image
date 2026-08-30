"""Identity lock, WD14 body conflicts, hybrid assemble, framing tags."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity
from app.tags.body import BREAST_TAGS as _BREAST_SIZES


def test_conflicting_breast_tags_are_banned_when_small_is_locked():
    banned = identity.conflicting_body_tags(["1girl", "small_breasts", "blue_hair"])
    assert "large_breasts" in banned
    assert "huge_breasts" in banned
    assert "small_breasts" not in banned


def test_drop_conflicting_tags_strips_wd14_body_guesses():
    tags = "1girl, blue_hair, large_breasts, rooftop, skirt"
    got = identity.drop_conflicting_tags(tags, ["1girl", "small_breasts", "blue_hair"])
    assert "large_breasts" not in got
    assert "rooftop" in got
    assert "skirt" in got


def test_opposing_negative_pushes_against_extreme_upgrades():
    neg = identity.opposing_negative(["small_breasts"])
    assert "large_breasts" in neg
    assert "huge_breasts" in neg
    assert "small_breasts" not in neg


def test_assemble_positive_leads_with_identity_and_appends_framing():
    positive = identity.assemble_positive(
        ["1girl", "small_breasts", "blue_hair"],
        "standing, rooftop, large_breasts",
        "She waits in the rain.",
        framing="face_closeup",
    )
    assert positive.startswith("1girl, small_breasts, blue_hair")
    assert "large_breasts" not in positive
    assert "close_up" in positive
    assert "She waits in the rain." in positive


def test_assemble_positive_session_hairstyle_overrides_identity_cut():
    """Ponytail in craft must displace bob_cut / short_hair from identity."""
    positive = identity.assemble_positive(
        ["1girl", "silver_hair", "bob_cut", "short_hair", "blue_eyes"],
        "cheerleader_uniform, ponytail, high_ponytail, ribbon, looking_at_viewer",
        "Cheerleader with a high ponytail.",
        framing="auto",
        subject=["1girl"],
    )
    assert "ponytail" in positive
    assert "high_ponytail" in positive
    assert "bob_cut" not in positive
    assert "short_hair" not in positive
    assert "silver_hair" in positive  # colour stays locked


def test_parse_hybrid_and_prose_fallback():
    tags, scene = identity.parse_hybrid(
        "TAGS: standing, rain\n\nSCENE: She leans on the rail."
    )
    assert tags == "standing, rain"
    assert scene == "She leans on the rail."
    tags, scene = identity.parse_hybrid("just a paragraph of prose")
    assert tags == ""
    assert scene == "just a paragraph of prose"


def test_pose_summary_keeps_two_sentences_from_scene():
    raw = "TAGS: x\n\nSCENE: She waits. Rain ticks on the rail. A third line."
    assert identity.pose_summary(raw) == "She waits. Rain ticks on the rail."


def test_framing_tags_and_normalize():
    assert identity.normalize_framing("Face Close-Up") == "face_closeup"
    assert identity.normalize_framing("nope") == "auto"
    assert identity.parse_framing("from_behind") == "from_behind"
    try:
        identity.parse_framing("nope")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert "from_behind" in identity.framing_tags("from_behind")
    assert "full_body" in identity.framing_negative("face_closeup")


def test_pose_intent_comes_from_scene_not_identity_prefix():
    from app.muse import chain

    raw = "TAGS: standing, rooftop\n\nSCENE: She waits in the rain."
    result = chain._finish_turn(
        raw, muse_id="beat", identity_tags=["1girl", "small_breasts"],
        framing="auto", brief="B",
    )
    assert result.prompt.startswith("1girl, small_breasts")
    assert result.pose_intent == "She waits in the rain."
    assert "small_breasts" not in result.pose_intent


def test_parse_table_read_keeps_say_separate_from_craft():
    say, tags, scene = identity.parse_table_read(
        "SAY: 総監督、寄りましょう。\n\n"
        "TAGS: close_up, rain\n\n"
        "SCENE: She leans into the frame."
    )
    assert "総監督" in say
    assert tags == "close_up, rain"
    assert scene == "She leans into the frame."


def test_craft_is_thin_flags_short_scene():
    assert identity.craft_is_thin("1girl, smile", "She sits.")
    # Exact pose prose at ~70 words is finished work — not a miss that should
    # force another weave of air-and-cloth padding.
    pose_scene = (
        "She sits on the bench, weight on her left hip, both hands folded in "
        "her lap, shoulders soft, chin slightly down as she looks toward the "
        "lens. The sailor collar rests flat against her chest; one loafer toes "
        "the gravel under the seat while the other foot hangs free, heel lifted. "
        "Her spine is quiet, not arched, and the afternoon light catches only "
        "the edge of her cheek."
    )
    pose_prompt = "1girl, sitting, hands_on_lap, sailor_collar, " + pose_scene
    assert identity.word_count(pose_scene) >= 35
    assert not identity.craft_is_thin(pose_prompt, pose_scene)
    rich_scene = " ".join(["word"] * 160)
    rich_prompt = "1girl, aqua_hair, sitting, window, " + rich_scene
    assert not identity.craft_is_thin(rich_prompt, rich_scene)


def test_merge_negative_dedupes():
    assert identity.merge_negative(
        "bad quality, large_breasts",
        "large_breasts, huge_breasts",
    ) == "bad quality, large_breasts, huge_breasts"


def test_reference_leak_detects_fenced_likes():
    from app.muse import brief as brief_mod

    character = {
        "identity_tags": ["1girl"],
        "personality": {
            "traits": ["calm"],
            "summary": "",
            "likes": ["thermos coffee", "night walks"],
            "dislikes": [],
            "inner": [],
        },
        "palette": [],
        "signature_prop": "",
    }
    text = brief_mod.build(character, "on a rooftop", "anime")
    leaked = identity.warn_reference_leak(
        text, "a girl with a thermos coffee on the roof",
    )
    assert any("thermos coffee" in x for x in leaked)


def test_the_style_reaches_the_prompt_not_only_the_brief():
    """The panel's Style box used to stop at the brief.

    It was handed to the LLM as a request and never became a tag, so a run
    asking for cute 2D anime rendered at whatever the checkpoint defaults to.
    """
    positive = identity.assemble_positive(
        ["black_hair", "blue_eyes"], "standing, workshop", "She stands.",
        framing="upper_body", style="Cute 2D Anime Style",
    )
    assert "cute_2d_anime_style" in positive
    # Directly after identity: the look colours everything that follows.
    assert positive.index("blue_eyes") < positive.index("cute_2d_anime_style")
    assert positive.index("cute_2d_anime_style") < positive.index("standing")


def test_a_comma_written_style_becomes_several_tags():
    positive = identity.assemble_positive(
        [], "standing", "", style="flat colour, bold outlines",
    )
    assert "flat_colour" in positive and "bold_outlines" in positive


def test_subject_count_comes_from_the_cast_not_the_character():
    one = identity.subject_tags([{"subject_tag": "1girl"}])
    assert one == ["1girl", "solo"]

    two = identity.subject_tags([{"subject_tag": "1girl"}, {"subject_tag": "1girl"}])
    assert two == ["2girls"]
    assert "solo" not in two

    mixed = identity.subject_tags([{"subject_tag": "1girl"}, {"subject_tag": "1boy"}])
    assert set(mixed) == {"1girl", "1boy"}

    assert identity.subject_tags([]) == []


def test_the_count_leads_the_prompt_and_never_repeats_identity():
    positive = identity.assemble_positive(
        ["black_hair"], "standing", "She stands.",
        subject=identity.subject_tags([{"subject_tag": "1girl"}]),
    )
    assert positive.startswith("1girl, solo, black_hair")


def test_the_figure_lock_strips_a_size_the_model_invented():
    """The whole point of putting a chest tag in identity: with the bucket empty
    the slot has nothing 'present', so nothing gets banned and a draft's guess
    walks straight into the prompt."""
    locked = ["black_hair", "small_breasts", "slim"]
    positive = identity.assemble_positive(
        locked, "large_breasts, curvy, standing, workshop", "She stands.",
    )
    assert "small_breasts" in positive and "slim" in positive
    assert "large_breasts" not in positive
    assert "curvy" not in positive
    assert "standing" in positive and "workshop" in positive


def test_the_opposites_reach_the_negative():
    negative = identity.opposing_negative(["black_hair", "small_breasts"])
    assert "large_breasts" in negative and "flat_chest" in negative
    assert "small_breasts" not in negative, "her own figure must not be negated"
    # The extremes are pushed against whenever any chest tag is locked.
    assert "huge_breasts" in negative and "gigantic_breasts" in negative


def test_an_empty_figure_locks_nothing():
    """Documents why the roster needed one: this is the state it was in."""
    banned = identity.conflicting_body_tags(["black_hair", "blue_eyes"])
    assert not (banned & set(_BREAST_SIZES)), sorted(banned & set(_BREAST_SIZES))


def test_petite_is_refused_whatever_she_is():
    """A slot only bans its other members when something is in it, and most
    characters name no height at all — so `petite` needed the unconditional
    treatment rather than a slot mate."""
    for locked in (["black_hair"], ["black_hair", "tall"], ["black_hair", "small_breasts"]):
        positive = identity.assemble_positive(locked, "petite, standing", "She stands.")
        assert "petite" not in positive, locked
    assert "petite" in identity.opposing_negative(["black_hair", "small_breasts"])


# ── emphasis ────────────────────────────────────────────────────────────────
def test_emphasis_above_the_cap_is_brought_back_down():
    """The 1.35 ceiling lived in the Finisher's specialty text and nowhere else.

    A real run shipped `(neck_tension:1.4)` and `(shoulder_tension:1.3)` from the
    choreographer; at 1.4 the sampler arches the whole body far enough to break
    the clothing silhouette and the face, which is what the frame came back as.
    """
    assert identity.clamp_weight("(neck_tension:1.4)") == "(neck_tension:1.35)"
    assert identity.clamp_weight("(shoulder_tension:1.3)") == "(shoulder_tension:1.3)"
    assert identity.clamp_weight("standing") == "standing"
    assert identity.clamp_weights(
        "a, (b:1.9), (c:1.2)",
    ) == "a, (b:1.35), (c:1.2)"


def test_the_assembled_prompt_carries_no_emphasis_over_the_cap():
    positive = identity.assemble_positive(
        ["black_hair"], "(neck_tension:1.4), (extreme_close-up:1.4)", "She sings.",
    )
    assert "1.4" not in positive, positive
    assert "(neck_tension:1.35)" in positive


def test_emphasis_no_longer_smuggles_a_tag_past_the_identity_lock():
    """`_norm` left the parentheses on, so a weighted tag matched nothing: it did
    not collide with the identity tag already in the prompt, and it walked past
    the banned-body check that exists to stop exactly this."""
    positive = identity.assemble_positive(
        ["silver_hair", "small_breasts"],
        "(silver_hair:1.2), (large_breasts:1.3), singing",
        "She sings.",
    )
    assert positive.count("silver_hair") == 1, positive
    assert "large_breasts" not in positive, positive


def test_bare_tag_and_tag_names_read_through_the_emphasis():
    assert identity.bare_tag("(neck_tension:1.4)") == "neck_tension"
    assert identity.bare_tag(" Wireless Microphone ") == "wireless_microphone"
    assert identity.tag_names(
        "singing, (singing:1.2), tambourine, ",
    ) == ["singing", "tambourine"]


def test_bare_tag_reads_through_emphasis_written_without_parens():
    """A real e2e run showed the model writing weight two other ways besides
    `(tag:1.2)`: the bare `tag:1.2` with no parens at all, and `tag (1.2)`
    with the number alone inside parens. `_WEIGHT_RE` only ever matched the
    first form, so `low_angle:1.1` and `squinting (1.1)` read as one opaque
    tag that matched nothing — not the plain tag in a slot lookup, not a
    banned name, not its own duplicate written properly on another turn."""
    assert identity.bare_tag("low_angle:1.1") == "low_angle"
    assert identity.bare_tag("squinting (1.1)") == "squinting"
    assert identity.split_weight("low_angle:1.1") == ("low_angle", 1.1)
    assert identity.split_weight("squinting (1.1)") == ("squinting", 1.1)
    # A parenthetical that is not a number at all is not guessed at — there is
    # no safe rewrite for `closed_eyes (softly)`, only a flag for one.
    assert identity.bare_tag("closed_eyes (softly)") != "closed_eyes"


def test_clamp_weight_caps_emphasis_written_without_parens_too():
    over = f"low_angle:{identity.MAX_TAG_WEIGHT + 1}"
    assert identity.clamp_weight(over) == f"(low_angle:{identity.MAX_TAG_WEIGHT:g})"


def test_backslash_escaped_underscores_are_stripped():
    """A real production session's prompt was full of `straw\\_hat`,
    `pink\\_camisole`, `cotton\\_blend` — the model's markdown-chat reflex of
    escaping underscores, not prompt syntax. `\\_` unambiguously means `_`,
    so both the comparison form (`bare_tag`) and the stored/displayed form
    (`clamp_weight`, which returns the literal text that reaches the
    prompt) need to come out clean, or the same tag under two spellings
    evades every dedup/ban/conflict check that compares bare names."""
    assert identity.bare_tag("straw\\_hat") == "straw_hat"
    assert identity.clamp_weight("straw\\_hat") == "straw_hat"
    assert identity.clamp_weight("pink\\_camisole (2.0)") == \
        f"(pink_camisole:{identity.MAX_TAG_WEIGHT:g})"


# ── sane_prose: a facet's nl and the decision digest are free prose ────────
# A real W-Muse session baked two kinds of garbage permanently into the
# picture, because `facets.write()`/`route_note` stored whatever the model
# wrote with zero review: a "was X (→ now Y)" change-annotation instead of
# the absolute value the contract asks for, and a bare comma-separated tag
# list standing in for a sentence.

def test_sane_prose_refuses_a_change_annotation():
    bad = ("drying_clothes (→ **interior_laundry**, **balcony**)")
    assert identity.sane_prose(bad) is None


def test_sane_prose_refuses_a_before_after_paragraph():
    bad = (
        "We are standing on the expansive roof of an apartment building. "
        "(→ We are now positioned within a brightly lit indoor utility "
        "space.)"
    )
    assert identity.sane_prose(bad) is None


def test_sane_prose_refuses_a_bare_tag_list():
    assert identity.sane_prose("smile, happy, blush, soft_gaze.") is None


def test_sane_prose_strips_markdown_bold_but_keeps_real_prose():
    text = "A woven **straw hat** sits perfectly on my head."
    assert identity.sane_prose(text) == "A woven straw hat sits perfectly on my head."


def test_sane_prose_passes_ordinary_sentences_with_a_few_commas():
    text = (
        "It's the soft pastel blue cotton T-shirt and white shorts with a "
        "light, loosely tied laundry apron, kept as is for this shot."
    )
    assert identity.sane_prose(text) == text


def test_sane_prose_passes_through_empty():
    assert identity.sane_prose("") == ""
    assert identity.sane_prose(None) == ""


# ── 二人を名前で結ぶ ────────────────────────────────────────────────
MIO = {
    "name": "Mio Kagami",
    "identity_tags": ["silver_hair", "bob_cut", "blue_eyes", "flat_chest", "slim"],
    "subject_tag": "1girl",
}
SUMIRE = {
    "name": "Sumire Hiraoka",
    "identity_tags": ["blonde_hair", "long_hair", "green_eyes", "medium_breasts"],
    "subject_tag": "1girl",
}


def _duet_positive(cast, tags="standing, indoors", scene="A quiet room.", **kw):
    flat = ["2girls"] + [
        t for c in cast for t in c["identity_tags"] if t not in ("1girl", "solo")
    ]
    return identity.assemble_positive(
        flat, tags, scene, subject=identity.subject_tags(cast), cast=cast, **kw
    )


def test_each_girl_owns_her_own_eyes_on_a_two_subject_render():
    out = _duet_positive([MIO, SUMIRE])
    assert "Mio is silver_hair, bob_cut, blue_eyes, flat_chest, slim," in out
    assert "Sumire is blonde_hair, long_hair, green_eyes, medium_breasts," in out
    # The flat run that never said whose was whose must be gone.
    assert "blue_eyes, flat_chest, slim, blonde_hair" not in out


def test_the_count_and_the_cast_open_the_prompt():
    assert _duet_positive([MIO, SUMIRE]).startswith("2girls, Mio and Sumire,\n")


def test_a_shared_tag_is_written_for_both_rather_than_deduplicated():
    both = dict(SUMIRE, identity_tags=[*SUMIRE["identity_tags"], "slim"])
    out = _duet_positive([MIO, both])
    assert out.count("slim") == 2


def test_the_frame_still_carries_style_craft_and_prose_after_the_names():
    out = _duet_positive(
        [MIO, SUMIRE], tags="standing, window_light", scene="Late afternoon.",
        style="Cute 2D Anime Style", framing="upper_body",
    )
    tail = out.rsplit("\n", 1)[-1]
    for part in ("cute_2d_anime_style", "window_light", "upper_body", "Late afternoon."):
        assert part in tail


def test_a_solo_shoot_keeps_the_flat_form_it_was_measured_on():
    out = identity.assemble_positive(
        MIO["identity_tags"], "standing", "A quiet room.",
        subject=identity.subject_tags([MIO]), cast=[MIO],
    )
    assert "\n" not in out
    assert out.startswith("1girl, solo, silver_hair,")


def test_a_cast_without_a_latin_name_falls_back_to_the_flat_form():
    nameless = {"name": "各務 みお", "identity_tags": MIO["identity_tags"]}
    out = _duet_positive([nameless, SUMIRE])
    assert "\n" not in out
    assert " is " not in out


def test_two_girls_sharing_a_given_name_fall_back_rather_than_bind_half():
    twin = dict(SUMIRE, name="Mio Hiraoka")
    out = _duet_positive([MIO, twin])
    assert "\n" not in out


def test_the_session_hairstyle_still_wins_over_both_locked_ones():
    out = _duet_positive([MIO, SUMIRE], tags="ponytail, standing")
    assert "bob_cut" not in out
    assert "long_hair" not in out
    assert "ponytail" in out
    # Colour, eyes and figure are still bound to their owner.
    assert "Mio is silver_hair, blue_eyes, flat_chest, slim," in out


def test_a_stray_count_tag_never_reaches_a_named_line():
    counted = dict(MIO, identity_tags=["1girl", *MIO["identity_tags"]])
    out = _duet_positive([counted, SUMIRE])
    assert "1girl" not in out
    assert out.startswith("2girls, Mio and Sumire,")


def test_a_garment_lands_on_the_girl_who_is_wearing_it():
    out = _duet_positive(
        [MIO, SUMIRE],
        tags="professional_blouse, linen_apron, indoors, window_light",
        own=[["professional_blouse"], ["linen_apron"]],
    )
    assert "Mio is silver_hair, bob_cut, blue_eyes, flat_chest, slim, professional_blouse," in out
    assert "Sumire is blonde_hair, long_hair, green_eyes, medium_breasts, linen_apron," in out
    # 動かしただけ。画全体の側に二度出てはいけない。
    assert out.count("professional_blouse") == 1
    assert out.count("linen_apron") == 1
    # 場所と光は誰のものでもないので、そのまま下に残る。
    assert out.rsplit("\n", 1)[-1].startswith("indoors, window_light")


def test_a_garment_the_weave_never_wrote_is_not_minted_by_the_split():
    out = _duet_positive(
        [MIO, SUMIRE], tags="indoors", own=[["straw_hat"], []],
    )
    assert "straw_hat" not in out


def test_the_wardrobe_split_is_ignored_when_the_names_do_not_hold():
    nameless = {"name": "各務 みお", "identity_tags": MIO["identity_tags"]}
    out = _duet_positive(
        [nameless, SUMIRE], tags="professional_blouse, linen_apron",
        own=[["professional_blouse"], ["linen_apron"]],
    )
    assert "\n" not in out
    assert "professional_blouse, linen_apron" in out


def test_a_quoted_text_tag_reaches_the_sampler_as_written():
    """`text "OPEN"` は看板の文字。大文字も引用符もそのまま通す。"""
    out = identity.assemble_positive(
        ["silver_hair"], 'handheld_sign, text "OPEN", standing', "A quiet room.",
        subject=["1girl", "solo"],
    )
    assert 'text "OPEN"' in out


# ── 髪型と、髪の様子 ────────────────────────────────────────────────
def test_every_hair_word_is_either_a_cut_or_a_description():
    """`axis_hair` に語が増えたら、どちらか名乗るまで試験が落ちる。"""
    both = identity.HAIR_CUT_TAGS | identity.HAIR_DESCRIPTION_TAGS
    assert both == identity.HAIR_STYLE_TAGS
    assert not (identity.HAIR_CUT_TAGS & identity.HAIR_DESCRIPTION_TAGS)


def test_hair_moving_in_the_wind_does_not_unseat_a_bob():
    """実測（2026-08-25）: `floating_hair` が立つとボブが消えていた。"""
    out = _duet_positive([MIO, SUMIRE], tags="floating_hair, standing")
    assert "Mio is silver_hair, bob_cut, blue_eyes, flat_chest, slim," in out
    assert "Sumire is blonde_hair, long_hair, green_eyes, medium_breasts," in out
    assert "floating_hair" in out


def test_a_cut_asked_of_one_girl_leaves_the_other_hers():
    out = _duet_positive(
        [MIO, SUMIRE], tags="ponytail, standing", own=[["ponytail"], []],
    )
    assert "Mio is silver_hair, blue_eyes, flat_chest, slim, ponytail," in out
    assert "Sumire is blonde_hair, long_hair, green_eyes, medium_breasts," in out


def test_a_cut_nobody_owns_still_belongs_to_the_picture():
    """画全体の側に置かれた髪型は、二人ともに掛かる。"""
    out = _duet_positive([MIO, SUMIRE], tags="ponytail, standing")
    assert "bob_cut" not in out
    assert "long_hair" not in out
    assert "ponytail" in out


def test_a_description_is_not_a_cut_on_a_solo_shoot_either():
    out = identity.assemble_positive(
        MIO["identity_tags"], "floating_hair, standing", "A quiet room.",
        subject=identity.subject_tags([MIO]),
    )
    assert "bob_cut" in out and "floating_hair" in out


def test_the_panel_framing_is_not_added_twice_under_another_spelling():
    """実測（`42b55492`）: craft の `close-up` と画角の `close_up` が両方焼かれた。"""
    out = identity.assemble_positive(
        ["silver_hair"], "close-up, standing", "A quiet room.",
        framing="face_closeup", subject=["1girl", "solo"],
    )
    assert "close-up" in out
    assert "close_up" not in out.replace("close-up", "")


def test_the_panel_framing_still_lands_beside_a_different_crop_word():
    """綴り違いだけを見る。**枠で見ると画角が消える** —— 一度そう壊した。"""
    out = identity.assemble_positive(
        ["silver_hair"], "wide_shot, standing", "A classroom.",
        framing="full_body", subject=["1girl", "solo"],
    )
    assert "full_body" in out


def test_a_json_leftover_bracket_never_reaches_the_sampler():
    """実測（`2acfdbe2`）で `anime_illustration]` が板のプロンプトに載った。

    weave が JSON の配列ごと文字列にして返した回。`bare_tag` は正しく
    `anime_illustration` を返すが、**サンプラーへ行く生の文字**のほうに `]` が
    残る。`[...]` は強調の構文なので、片割れはその語の重みを変える。
    """
    assert identity.clamp_weight("anime_illustration]") == "anime_illustration"
    assert identity.clamp_weight("[solo") == "solo"
    got = identity.clamp_weights(
        "[solo, close-up, oversized_hoodie, anime_illustration], denim_skirt",
    )
    assert "]" not in got and "[" not in got
    assert "anime_illustration" in got and "denim_skirt" in got


def test_balanced_emphasis_is_left_alone():
    """釣り合っている括弧は総監督か係が書いたもの。触らない。"""
    for text in ("(silver_hair:1.2)", "[bokeh]", "(soft)", "((deep))", "plain_tag"):
        assert identity.clamp_weight(text) == text


def test_edge_underscores_from_json_never_reach_the_sampler():
    """`_anime_illustration` / `__` / `_solo` —— weave が配列ごと文字列にした残骸。

    実測（2026-08-30・`011e3553` ほか）で板のプロンプトに載った。`bare_tag`
    は比べる用の値からしか落とさないので、サンプラーへ行く生の文字に残る。
    **本物のタグは `_` で始まらないし、終わらない。**
    """
    assert identity.clamp_weight("_anime_illustration") == "anime_illustration"
    assert identity.clamp_weight("_solo") == "solo"
    # 全部が `_` の語は空になり、袋から落ちる
    assert identity.clamp_weight("__") == ""
    got = identity.clamp_weights("_anime_illustration, sitting, __, straw_hat")
    assert got == "anime_illustration, sitting, straw_hat"


def test_underscores_inside_a_tag_are_left_alone():
    """中の `_` は danbooru の区切り。縁だけを見る。"""
    for text in ("straw_hat", "black_tights", "looking_at_viewer",
                 "(silver_hair:1.2)", "[bokeh]"):
        assert identity.clamp_weight(text) == text

"""Pure unit tests for the shot notebook helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook


def test_an_affirmed_proposal_is_written_as_a_normal_patch():
    """`promote_open` is gone, and so is the `open` field it fed.

    Promotion used to decide handheld (→ beat) versus worn (→ wearing) from a
    noun list — 持|手に|花|缶|傘|ラムネ|氷 — which was wrong for anything the
    list did not name. Then `open` itself went: across 390 live sessions it
    never held a proposal, only parser debris. The scripter reads the
    conversation, sees the affirmation, and writes the sections itself.
    """
    nb = notebook.blank()
    nb["wearing"] = "薄いカーディガン"
    notebook.apply_patch(nb, {"beat": "ベンチに座って、落ち葉を一枚だけ手に"})
    assert "落ち葉" in nb["beat"]
    assert "カーディガン" in nb["wearing"]
    assert "open" not in nb


def test_vibe_is_capped():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "vibe": "\n".join(f"line{i}" for i in range(12)),
    })
    assert len(nb["vibe"].splitlines()) <= 5


def test_migrate_seeds_from_digest():
    session = {
        "inputs": {},
        "digest": "rooftop at dusk with a sailor uniform",
        "craft": {"scene": "", "tags": "", "prompt": ""},
        "facets": {},
        "standing": ["no feet"],
    }
    notebook.migrate(session)
    assert session["notebook"]["scene"]
    assert "no feet" in session["notebook"]["standing"]


def test_summary_for_muse_is_short():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "atmosphere": "切ない夕暮れ",
        "wearing": "薄いカーディガン",
        "beat": "ベンチに座る",
        "bg": "駅の改札の人混み",
        "light": "逆光のリム",
    })
    text = notebook.summary_for_muse(nb, name_a="あさひ")
    assert "カーディガン" in text
    assert "改札" in text or "BG:" in text
    assert "Open proposal" not in text


def test_ensure_beat_leads_scene_puts_posture_first():
    """Weave air-padding must not leave the Showrunner's beat off the prose."""
    scene = (
        "Golden hour softens the classroom air; dust hangs in the shafts of "
        "light and the chalk shelf gleams faintly along the back wall."
    )
    out = notebook.ensure_beat_leads_scene(
        scene, beat="sitting on the desk, hands on knees",
    )
    assert out.lower().startswith("sitting")
    assert "hands on knees" in out.lower()
    # Already present — do not double.
    again = notebook.ensure_beat_leads_scene(
        out, beat="sitting on the desk, hands on knees",
    )
    assert again.count("sitting") == out.count("sitting")


def test_filter_weave_tags_drops_banned_as_well_as_struck():
    kept = notebook.filter_weave_tags(
        "sitting, bucket, smile",
        wearing="sailor", scene="rooftop", beat="sitting",
        struck={"hat"}, banned={"bucket"},
    )
    assert "sitting" in kept and "smile" in kept
    assert "bucket" not in kept


def test_shot_diff_and_record_rewrite_ring():
    before = notebook.shot_snapshot({"beat": "sitting", "wearing": "cardigan"})
    after = notebook.shot_snapshot({"beat": "standing", "wearing": "cardigan"})
    changed = notebook.shot_diff(before, after)
    assert changed["beat"]["before"] == "sitting"
    assert changed["beat"]["after"] == "standing"
    assert "wearing" not in changed

    session: dict = {}
    first = notebook.record_rewrite(
        session, "scripter", before=before, after=after, intent="shot",
    )
    assert first is not None
    assert first["source"] == "scripter"
    assert session["rewrite_log"][-1]["changed"]["beat"]["after"] == "standing"
    same = notebook.record_rewrite(
        session, "scripter", before=after, after=after,
    )
    assert same is None
    assert len(session["rewrite_log"]) == 1


def test_framing_from_phrase_last_match_wins():
    from app.muse import identity
    assert identity.framing_from_phrase("wide full body") == "full_body"
    assert identity.framing_from_phrase("寄って。顔と上半身") == "upper_body"
    assert identity.framing_from_phrase("寄って横顔") == "face_closeup"
    assert identity.framing_from_phrase("eye level") == "auto"
    # Crop conflict is scrub's job; assemble only injects framing tags.
    scrubbed = notebook.scrub_craft_tags(
        "cardigan, wide_shot, close_up",
        wearing="cardigan", scene="classroom", beat="standing",
        struck=set(), frame="wide full body",
    )
    pos = identity.assemble_positive(
        ["1girl"], scrubbed, "A classroom.",
        framing="full_body",
    )
    low = pos.lower().replace(" ", "_")
    assert "close_up" not in low
    assert "full_body" in low



# ── taking something off ───────────────────────────────────────────────────
# Measured live: every removal turn came back with the frame rewritten and
# WEARING untouched — 「コート脱いで」left the coat on for three more turns and
# said nothing. Restating the five remaining garments verbatim is the work the
# scripter does not do; naming the one that came off is work it already does.
def test_taking_a_garment_off_subtracts_it():
    nb = notebook.blank()
    nb["wearing"] = "charcoal_grey_coat, cream_colored_sweater, dark_denim"
    nb["beat"] = "standing, looking down"
    notebook.apply_patch(nb, {"wearing_drop": "coat"})
    assert nb["wearing"] == "cream_colored_sweater, dark_denim"
    assert nb["beat"] == "standing, looking down"
    # A compound noun is the same garment: `sundress` is the dress.
    nb2 = notebook.blank()
    nb2["wearing"] = "sundress, sandals"
    notebook.apply_patch(nb2, {"wearing_drop": "dress"})
    assert nb2["wearing"] == "sandals"


def test_what_she_was_holding_goes_with_the_garment():
    # Clothes and action are one thing to everyone except the notebook. Take
    # the skirt away and the hem must leave her hand — but she keeps standing.
    nb = notebook.blank()
    nb["wearing"] = "navy_pleated_skirt, grey_cardigan"
    nb["beat"] = "standing, clutching the hem of her skirt, one hand in her pocket"
    notebook.apply_patch(nb, {"wearing_drop": "skirt"})
    assert nb["wearing"] == "grey_cardigan"
    assert nb["beat"] == "standing, one hand in her pocket"
    # The posture survives even when its own clause names the garment.
    nb2 = notebook.blank()
    nb2["wearing"] = "coat"
    nb2["beat"] = "sitting on her coat"
    notebook.apply_patch(nb2, {"wearing_drop": "coat"})
    assert nb2["wearing"] == ""
    assert notebook.posture_stem(nb2["beat"]) == "sitting"


def test_an_ambiguous_removal_changes_nothing():
    # Two blazers and no way to tell which. Guessing undresses her wrongly and
    # silently; the room is asked instead (`service` posts the question).
    nb = notebook.blank()
    nb["wearing"] = "navy_blazer, wool_blazer"
    nb["beat"] = "standing"
    rev = nb["rev"]
    notebook.apply_patch(nb, {"wearing_drop": "blazer"})
    assert nb["wearing"] == "navy_blazer, wool_blazer"
    assert nb["rev"] == rev
    assert len(notebook.garment_matches(nb["wearing"], "blazer")) == 2
    # A garment she does not have on is left alone too.
    nb2 = notebook.blank()
    nb2["wearing"] = "sailor uniform"
    notebook.apply_patch(nb2, {"wearing_drop": "hat"})
    assert nb2["wearing"] == "sailor uniform"
    assert notebook.garment_matches(nb2["wearing"], "hat") == []


def test_a_short_head_noun_does_not_swallow_a_longer_word():
    # `top` must not match `laptop`; the suffix rule is guarded at four chars.
    assert notebook.garment_matches("laptop", "top") == []
    assert notebook.garment_matches("crop_top, skirt", "top") == ["crop_top"]


def test_densify_can_never_undress_her():
    assert "wearing_drop" not in notebook.strip_shot_keys(
        {"wearing_drop": "coat", "tags": "1girl"}
    )


# ── the clerk's answer is checked against what the compile wrote ───────────
def test_the_clerk_reads_a_closed_list():
    from app.muse import chain
    assert chain.parse_classified_fields("wearing, beat, frame") == {
        "wearing", "beat", "frame"}
    assert chain.parse_classified_fields("none") == set()
    # Anything outside the list is not a field, however confidently said.
    assert chain.parse_classified_fields("expression, mood, vibes") == set()
    assert chain.parse_classified_fields("") == set()


def test_the_repair_names_what_was_left_out():
    # "Try again" is a second chance at the same mistake. The fields go in the
    # note by name, which is the whole difference between this and the version
    # that sat unused in the file.
    from app.muse import chain
    note = chain.scripter_repair_note(["wearing", "beat"])
    assert "wearing, beat" in note
    assert note.startswith("REPAIR:")
    assert "Do not emit tags" in note


def test_the_clerk_names_one_kind_of_turn():
    from app.muse import chain
    assert chain.parse_classified_intent("shot") == "shot"
    assert chain.parse_classified_intent("The kind is: casual.") == "casual"
    # Ordered so `recall` is read before `casual` when a wordy answer holds
    # both, and "" when it answered with neither.
    assert chain.parse_classified_intent("unclear") == ""


# ── ラベルは、行頭でなくても境界 ──────────────────────────────────
def test_a_field_never_swallows_the_next_field():
    """実測（2026-08-25）: frame が手帖の頁の残り全部を飲んでいた。"""
    leak = (
        "medium shot, looking straight into lens 各務 みお WEARING: blue "
        "sleeveless gown, earrings 各務 みお BEAT: sitting, hands pressed "
        "against her chest 平岡 すみれ WEARING_B"
    )
    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {"frame": leak})
    assert nb["frame"] == "medium shot, looking straight into lens"


def test_an_ordinary_value_passes_through_untouched():
    for plain in (
        "medium shot, looking straight into lens",
        "sitting, hands pressed against her chest as she leans forward",
        "dim light from distant buildings and flickering lanterns",
    ):
        assert notebook.cut_at_label(plain) == plain


# ── 一つの服に、一つの名前 ────────────────────────────────────────
def test_the_weave_renaming_a_gown_a_dress_is_one_garment_not_three():
    gone = notebook.garment_aliases(
        "gown, blue_dress, sleeveless_dress, earrings, sitting, blue_sky",
        "blue sleeveless gown, earrings",
    )
    assert gone == {"blue_dress", "sleeveless_dress"}


def test_a_shortened_name_that_keeps_the_head_noun_stays():
    assert notebook.garment_aliases(
        "black_dress, cocktail_dress", "black cocktail dress",
    ) == set()


def test_only_clothing_is_read_as_a_rename():
    """`blue_sky` は青いガウンの別名ではない。"""
    assert "blue_sky" not in notebook.garment_aliases(
        "blue_sky, blue_water, standing", "blue sleeveless gown",
    )


# ── 折り込みは、そのターンのもの ──────────────────────────────────
def _folded(beat_before: str, card: str) -> dict:
    nb = notebook.blank()
    notebook.apply_patch(nb, {"beat": beat_before})
    notebook.absorb_muse_card(nb, card)
    return nb


def test_her_gesture_reaches_the_take_and_then_lets_go():
    """震えが止まらなかった件。入口は残し、出口を作る。"""
    nb = _folded(
        "sitting, hands on her knees",
        "BEAT: sitting, trembling hands pressed against her chest",
    )
    assert "trembling" in nb["beat"]

    assert notebook.undo_fold(nb) == ["beat"]
    assert nb["beat"] == "sitting, hands on her knees"
    # 総監督が置いた姿勢は残る。
    assert "sitting" in nb["beat"]


def test_a_value_written_after_the_fold_is_not_taken_back():
    nb = _folded("sitting, hands on her knees", "BEAT: sitting, trembling hands")
    notebook.apply_patch(nb, {"beat": "standing, arms at her sides"})
    assert notebook.undo_fold(nb) == []
    assert nb["beat"] == "standing, arms at her sides"


def test_letting_go_twice_does_nothing_the_second_time():
    nb = _folded("sitting", "BEAT: sitting, trembling hands")
    notebook.undo_fold(nb)
    assert notebook.undo_fold(nb) == []
    assert nb["beat"] == "sitting"


def test_a_fold_that_changed_nothing_leaves_nothing_to_let_go():
    nb = notebook.blank()
    notebook.apply_patch(nb, {"beat": "sitting"})
    notebook.absorb_muse_card(nb, "BEAT: sitting")
    assert notebook.undo_fold(nb) == []


# ── 織ったタグを、正本と突き合わせる ──────────────────────────────
# 実測（`42b55492` / 2026-08-26）。手帖は「レンズを見ている」と言い、地の文も
# 「eyes wide and glassy」と書いているのに、タグは `closed_eyes` だった。
LENS = "close-up, looking straight into the lens"
BEAT = "standing, clutching the hem of her dress with trembling fingers"
BEAT_B = "standing closely together, leaning into one another with blissful faces"


def _scrub(tags: str, *, frame: str = LENS) -> list[str]:
    out = notebook.scrub_craft_tags(
        tags, wearing="pale blue dress", scene="park at dusk", beat=BEAT,
        struck=set(), wearing_b="black cocktail dress", beat_b=BEAT_B, frame=frame,
    )
    return [t.strip() for t in out.split(",") if t.strip()]


def test_the_eyes_belong_to_the_frame():
    """手帖がレンズを見ていると言うなら、目を閉じた語は残らない。"""
    kept = _scrub("looking_at_viewer, closed_eyes, eyes_closed, standing")
    assert "closed_eyes" not in kept and "eyes_closed" not in kept
    assert "looking_at_viewer" in kept and "standing" in kept


def test_a_frame_that_says_nothing_about_the_lens_leaves_the_eyes_alone():
    kept = _scrub("closed_eyes, standing", frame="wide shot from the side")
    assert "closed_eyes" in kept


def test_one_crop_survives_out_of_three_names_for_it():
    """`close-up` / `close_up` / `face_focus` が同時に並んでいた。"""
    kept = _scrub("close-up, close_up, face_focus, standing")
    assert len([t for t in kept if "close" in t or t == "face_focus"]) == 1


def test_the_notebook_decides_which_of_two_survives():
    """手帖が名指ししているほうを残す。先に来たほうではなく。"""
    kept = _scrub("sitting, standing", frame=LENS)
    assert "standing" in kept and "sitting" not in kept


def test_the_hour_is_left_alone():
    """時刻は重ねて書かれることがある。**間違えて削ると光が変わる。**"""
    kept = _scrub("night, twilight, evening, standing")
    for t in ("night", "twilight", "evening"):
        assert t in kept


def test_two_muses_get_a_letter_beside_the_name():
    """実測（`61db2bd6`）: 折り込みが `beat_b: standing behind A` と書いた。

    見出しは名前なのに欄の名前は `WEARING` / `WEARING_B` という文字なので、
    モデルは相手を「A」と呼ぶ。`A` はタグにならないので画には出ないが、指示
    としては汚れ。総監督「最初に `Mio (Actress A)` と書けばいいだけでしょう」。
    実機で 9回中 0回に落ちた。
    """
    nb = notebook.blank()
    notebook.apply_patch(nb, {"beat": "standing", "beat_b": "standing"})
    block = notebook.render(nb, name_a="各務 みお", name_b="平岡 すみれ")
    assert "各務 みお (Actress A) BEAT:" in block
    assert "平岡 すみれ (Actress B) BEAT:" in block


def test_a_solo_shoot_has_no_letters():
    """一人しかいないなら、文字を添える相手がいない。"""
    nb = notebook.blank()
    notebook.apply_patch(nb, {"beat": "standing"})
    block = notebook.render(nb, name_a="各務 みお")
    assert "各務 みお BEAT:" in block
    assert "Actress A" not in block

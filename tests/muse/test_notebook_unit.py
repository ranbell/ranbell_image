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
    })
    text = notebook.summary_for_muse(nb, name_a="あさひ")
    assert "カーディガン" in text
    assert "Open proposal" not in text


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
    pos = identity.assemble_positive(
        ["1girl"], "cardigan, wide_shot, close_up", "A classroom.",
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

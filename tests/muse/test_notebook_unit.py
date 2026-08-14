"""Pure unit tests for the shot notebook helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook


def test_affirmed_proposal_is_folded_in_by_the_patch():
    """`promote_open` is gone: the scripter writes the fold as a normal patch.

    It used to decide handheld (→ beat) versus worn (→ wearing) from a noun
    list — 持|手に|花|缶|傘|ラムネ|氷 — which was wrong for anything the list
    did not name. The scripter reads the conversation, sees the affirmation,
    and writes absolute values into the right sections itself.
    """
    nb = notebook.blank()
    nb["open"] = "落ち葉を一枚だけ手に"
    nb["wearing"] = "薄いカーディガン"
    notebook.apply_patch(nb, {
        "beat": "ベンチに座って、落ち葉を一枚だけ手に",
        "clear_open": True,
    })
    assert "落ち葉" in nb["beat"]
    assert "カーディガン" in nb["wearing"]
    assert nb["open"] == ""


def test_vibe_and_open_are_capped():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "vibe": "\n".join(f"line{i}" for i in range(12)),
        "open": "one\ntwo\nthree",
    })
    assert len(nb["vibe"].splitlines()) <= 5
    assert len(nb["open"].splitlines()) <= 2


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
        "open": "落ち葉",
    })
    text = notebook.summary_for_muse(nb, name_a="あさひ")
    assert "カーディガン" in text
    assert "Open proposal" in text or "提案中" in text


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


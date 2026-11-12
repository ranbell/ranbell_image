"""How a picture moves through a bucket, and what a hand correction protects."""
from __future__ import annotations

import pytest

from app.lora import bucket as bk


def _bucket(**kw):
    return bk.new_bucket("My Girl", slug="my-girl", **kw)


def _described(b, sha, full="a woman standing in a field"):
    bk.apply_described(b, sha, full)
    bk.apply_stripped(b, sha, bk.with_trigger(b["trigger"], full), expected_full=full)


def test_slug_is_folder_safe_and_unique():
    assert bk.slugify("  Ran Bell / 赤い服 ") == "ran-bell"
    assert bk.slugify("赤") == "bucket"
    assert bk.unique_slug("Ran Bell", {"ran-bell", "ran-bell-2"}) == "ran-bell-3"


def test_adding_twice_keeps_one_and_starts_at_describe():
    b = _bucket()
    assert bk.add_items(b, ["a", "b", "a"]) == ["a", "b"]
    assert bk.add_items(b, ["b", "c"]) == ["c"]
    assert [it["status"] for it in b["items"]] == [bk.PENDING_DESCRIBE] * 3
    assert bk.next_pending(b)["sha256"] == "a"


def test_targets_are_trimmed_and_deduplicated():
    b = _bucket(targets=[" red hair ", "Red Hair", "", "freckles"])
    assert b["targets"] == ["red hair", "freckles"]


def test_describe_then_strip_reaches_done():
    b = _bucket(trigger="rnbl")
    bk.add_items(b, ["a"])
    bk.apply_described(b, "a", "a woman")
    assert b["items"][0]["status"] == bk.PENDING_STRIP
    bk.apply_stripped(b, "a", "rnbl, a woman", expected_full="a woman")
    assert b["items"][0]["status"] == bk.DONE
    assert bk.export_blockers(b) == []


def test_changing_targets_restrips_but_keeps_hand_corrections():
    b = _bucket(trigger="rnbl")
    bk.add_items(b, ["a", "b"])
    _described(b, "a")
    _described(b, "b")
    bk.set_caption(b, "b", "rnbl, my own words")

    assert bk.update_settings(b, targets=["red hair"]) is True
    a, hand = b["items"]
    assert a["status"] == bk.PENDING_STRIP
    assert hand["status"] == bk.DONE and hand["caption"] == "rnbl, my own words"
    assert hand["stale"] is True


def test_the_same_settings_change_nothing():
    b = _bucket(trigger="rnbl", targets=["x"])
    bk.add_items(b, ["a"])
    _described(b, "a")
    assert bk.update_settings(b, trigger=" rnbl ", targets=["x"]) is False
    assert b["items"][0]["status"] == bk.DONE


def test_force_replaces_a_hand_correction():
    b = _bucket()
    bk.add_items(b, ["a"])
    _described(b, "a")
    bk.set_caption(b, "a", "mine")
    assert bk.request_recaption(b, None, step="strip", force=False) == 0
    assert bk.request_recaption(b, None, step="strip", force=True) == 1
    assert b["items"][0]["edited"] is False


def test_strip_without_a_description_goes_back_to_describe():
    b = _bucket()
    bk.add_items(b, ["a"])
    bk.apply_error(b, "a", "boom")
    assert bk.request_recaption(b, ["a"], step="strip", force=False) == 1
    assert b["items"][0]["status"] == bk.PENDING_DESCRIBE


def test_a_strip_result_for_an_old_description_is_dropped():
    b = _bucket()
    bk.add_items(b, ["a"])
    bk.apply_described(b, "a", "new description")
    bk.apply_stripped(b, "a", "from the old one", expected_full="old description")
    assert b["items"][0]["status"] == bk.PENDING_STRIP
    assert b["items"][0]["caption"] == ""


def test_results_for_a_removed_picture_are_ignored():
    b = _bucket()
    bk.add_items(b, ["a"])
    bk.remove_items(b, ["a"])
    bk.apply_described(b, "a", "x")
    bk.apply_error(b, "a", "x")
    assert b["items"] == []


@pytest.mark.parametrize("trigger,text,want", [
    ("rnbl", "a woman", "rnbl, a woman"),
    ("rnbl", "rnbl, a woman", "rnbl, a woman"),
    ("rnbl", "Rnbl. a woman", "rnbl, a woman"),
    ("", "a woman", "a woman"),
    ("rnbl", "", "rnbl"),
])
def test_trigger_goes_in_front_once(trigger, text, want):
    assert bk.with_trigger(trigger, text) == want


def test_export_waits_for_every_picture():
    b = _bucket()
    assert bk.export_blockers(b) == ["empty"]
    bk.add_items(b, ["a", "b"])
    _described(b, "a")
    bk.apply_error(b, "b", "unreadable")
    assert bk.export_blockers(b) == ["error"]
    bk.request_recaption(b, ["b"], step="describe", force=False)
    assert bk.export_blockers(b) == ["pending"]


def test_hand_correction_needs_a_known_picture():
    with pytest.raises(bk.BucketError):
        bk.set_caption(_bucket(), "nope", "x")


def test_a_hand_correction_during_describe_is_kept():
    """Edited while the VLM was still looking: the late description must not
    send the picture on to strip (which would overwrite the hand's words)."""
    b = bk.new_bucket("x", slug="x", trigger="t", targets=["red hair"])
    bk.add_items(b, ["a"])
    bk.set_caption(b, "a", "t, my own words")
    bk.apply_described(b, "a", "a woman with red hair")
    it = b["items"][0]
    assert (it["status"], it["caption"], it["edited"]) == (bk.DONE, "t, my own words", True)
    assert it["full_caption"] == "a woman with red hair"  # kept for a later forced recompute
    bk.apply_error(b, "a", "boom")
    assert it["status"] == bk.DONE and not it["error"]


def test_a_hand_caption_without_a_description_never_loops():
    # Describe failed, the Showrunner wrote the caption, then the trigger
    # changed: the picture must not go back to "describe" (the describe result
    # defers to the hand, so it would be picked up for ever).
    b = bk.new_bucket("x", slug="x", trigger="t1")
    bk.add_items(b, ["a"])
    bk.apply_error(b, "a", "file missing")
    bk.set_caption(b, "a", "my words")
    assert bk.update_settings(b, trigger="t2")
    it = b["items"][0]
    assert it["status"] == bk.DONE and it["stale"] and bk.next_pending(b) is None
    # And a picture already caught that way is let go by "resume".
    it["status"] = bk.PENDING_DESCRIBE
    assert bk.resume(b) == 0 and it["status"] == bk.DONE
    # A describe result or an error for a hand-captioned picture finishes it.
    it["status"] = bk.PENDING_DESCRIBE
    bk.apply_described(b, "a", "a woman")
    assert it["status"] == bk.DONE and it["caption"] == "my words"
    it["status"] = bk.PENDING_DESCRIBE
    bk.apply_error(b, "a", "boom")
    assert it["status"] == bk.DONE and it["error"] == ""


def test_a_strip_result_from_before_a_settings_change_is_dropped():
    b = bk.new_bucket("x", slug="x", trigger="old", targets=["red hair"])
    bk.add_items(b, ["a"])
    bk.apply_described(b, "a", "a woman with red hair")
    before = bk.strip_setting(b)
    bk.update_settings(b, trigger="new")  # while the strip was running
    bk.apply_stripped(b, "a", "old, a woman", expected_full="a woman with red hair",
                      expected_setting=before)
    assert b["items"][0]["status"] == bk.PENDING_STRIP  # stripped again with "new"
    bk.apply_stripped(b, "a", "new, a woman", expected_full="a woman with red hair",
                      expected_setting=bk.strip_setting(b))
    assert b["items"][0]["caption"] == "new, a woman"


def test_resume_leaves_finished_captions_alone():
    b = bk.new_bucket("x", slug="x")
    bk.add_items(b, ["a", "b"])
    bk.apply_described(b, "a", "a woman")
    bk.apply_stripped(b, "a", "a woman", expected_full="a woman")
    assert bk.resume(b) == 1
    assert [it["status"] for it in b["items"]] == [bk.DONE, bk.PENDING_DESCRIBE]


def test_the_trigger_is_matched_as_a_whole_word():
    assert bk.with_trigger("a", "A woman stands.") == "a, A woman stands."
    assert bk.with_trigger("zqx", "zqx, a woman") == "zqx, a woman"
    assert bk.with_trigger("zqx", "zqxstyle woman") == "zqx, zqxstyle woman"


def test_picture_ids_are_checked():
    good = "AB" * 32
    assert bk.clean_sha(good) == good.lower()
    for bad in ["a", "g" * 64, "ab" * 32 + "/../x", ""]:
        with pytest.raises(bk.BucketError):
            bk.clean_sha(bad)

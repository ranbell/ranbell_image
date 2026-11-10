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

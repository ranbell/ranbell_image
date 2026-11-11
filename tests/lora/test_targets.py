"""Target models: one registry entry per image model a LoRA can be made for."""
from __future__ import annotations

import pytest

from app.lora import bucket as bk
from app.lora import captioner
from app.lora import targets


@pytest.fixture
def two_targets(monkeypatch):
    """Krea 2 plus a tag-style model, and one that is only planned."""
    tags = targets.Target(id="tagmodel", label="Tag Model", caption_style="tags", available=True,
                          describe=lambda p, t: "TAGS PLEASE " + ", ".join(t))
    planned = targets.Target(id="planned", label="Planned", caption_style="prose", available=False,
                             describe=lambda p, t: "")
    monkeypatch.setitem(targets.TARGETS, "tagmodel", tags)
    monkeypatch.setitem(targets.TARGETS, "planned", planned)


def test_krea2_is_the_default_and_available():
    assert targets.DEFAULT_TARGET == "krea2"
    assert [t.id for t in targets.available()][0] == "krea2"
    assert targets.get(None).id == "krea2" and targets.get("gone").id == "krea2"


def test_describe_prompt_comes_from_the_target(two_targets):
    assert captioner.describe_prompt(target_model="tagmodel", tags=["smile"]) == "TAGS PLEASE smile"
    assert "Krea 2" in captioner.describe_prompt(target_model="krea2")


def test_a_planned_target_cannot_be_chosen(two_targets):
    with pytest.raises(bk.BucketError, match="available: krea2, tagmodel"):
        bk.new_bucket("x", slug="x", target_model="planned")


def test_switching_target_describes_again_and_keeps_hand_edits(two_targets):
    b = bk.new_bucket("x", slug="x", trigger="t")
    bk.add_items(b, ["a", "b"])
    for sha in ("a", "b"):
        bk.apply_described(b, sha, "a woman")
        bk.apply_stripped(b, sha, "t, a woman", expected_full="a woman")
    bk.set_caption(b, "b", "mine")

    assert bk.update_settings(b, target_model="tagmodel", trigger="t2") is True
    a, hand = b["items"]
    assert b["target_model"] == "tagmodel" and b["trigger"] == "t2"
    assert a["status"] == bk.PENDING_DESCRIBE
    assert hand["caption"] == "mine" and hand["stale"] is True


def test_an_unavailable_switch_changes_nothing(two_targets):
    b = bk.new_bucket("x", slug="x", trigger="t")
    with pytest.raises(bk.BucketError):
        bk.update_settings(b, target_model="planned", trigger="changed")
    assert b["target_model"] == "krea2" and b["trigger"] == "t"

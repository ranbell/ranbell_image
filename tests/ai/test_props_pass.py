"""The objects pass: the model proposes, code decides what reaches the sheet.

Built on the Muse ledger's rules (`muse/ledger.py`): what is not written keeps
its value, an empty answer never clears, one object is named once, and nothing
is dropped without saying so.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.prompt import props
from app.prompt.scene_sheet import normalize_sheet


def _sheet(prop_text: str, holding: str = "") -> dict:
    return normalize_sheet({
        "medium": "anime illustration",
        "setting": "a small seaside tea house in the evening",
        "props": prop_text,
        "subjects": [{"who": "a girl", "holding": holding, "pose_action": "pouring tea"}],
    })


class TestSplit:
    def test_semicolons_and_noun_commas_split(self):
        assert props.split_items("a mug on the desk, a lamp in the corner; some books") == [
            "a mug on the desk", "a lamp in the corner", "some books"]

    def test_a_comma_inside_one_item_does_not(self):
        assert props.split_items("a mug, steaming, on the desk") == ["a mug, steaming, on the desk"]

    def test_and_before_a_new_noun_splits(self):
        assert props.split_items("a kettle on the stove and a tray by the sink") == [
            "a kettle on the stove", "a tray by the sink"]


class TestSimplify:
    def test_keeps_only_what_was_picked_in_sheet_order(self):
        sheet = _sheet("a cup on the table; a vase by the window; a fan on the wall; a cat")
        new, changed, _, notes = props.apply_props(
            sheet, {"keep": [{"index": 2, "item": ""}, {"index": 0, "item": "a cup on the table"}]},
            "simplify")
        assert new["props"] == "a cup on the table; a fan on the wall"
        assert any("left out: a vase" in n for n in notes)
        assert changed

    def test_a_shortened_item_cannot_bring_a_new_word(self):
        sheet = _sheet("a cup on the table; a vase by the window")
        new, *_ , notes = props.apply_props(
            sheet, {"keep": [{"index": 0, "item": "a porcelain teacup on the table"}]}, "simplify")
        assert new["props"] == "a cup on the table"
        assert any("new words" in n for n in notes)

    def test_never_more_than_the_cap(self):
        sheet = _sheet("; ".join(f"a {w} on the shelf" for w in
                                 ("basket", "kettle", "lantern", "cushion", "broom", "clock")))
        new, *_ = props.apply_props(
            sheet, {"keep": [{"index": i} for i in range(6)]}, "simplify")
        assert len(props.split_items(new["props"])) == props.SIMPLIFY_KEEP_MAX

    def test_an_unreadable_answer_changes_nothing(self):
        sheet = _sheet("a cup on the table")
        new, changed, _, notes = props.apply_props(sheet, {"keep": []}, "simplify")
        assert new is sheet and not changed and notes


class TestElaborate:
    def test_same_object_rewritten_and_a_few_added(self):
        sheet = _sheet("a cup on the table")
        new, changed, _, notes = props.apply_props(sheet, {
            "items": [{"index": 0, "item": "a chipped celadon cup of barley tea on the low table"}],
            "add": [{"item": "a paper lantern", "where": "hanging from the eaves"}],
        }, "elaborate")
        assert new["props"] == (
            "a chipped celadon cup of barley tea on the low table; "
            "a paper lantern hanging from the eaves")
        assert not notes

    def test_a_different_object_in_the_old_slot_is_refused(self):
        sheet = _sheet("a cup on the table")
        new, *_, notes = props.apply_props(
            sheet, {"items": [{"index": 0, "item": "a brass lamp on the table"}]}, "elaborate")
        assert new["props"] == "a cup on the table"
        assert any("not the same object" in n for n in notes)

    def test_left_out_and_empty_items_keep_their_value(self):
        sheet = _sheet("a cup on the table; a vase by the window")
        new, *_ = props.apply_props(
            sheet, {"items": [{"index": 1, "item": ""}]}, "elaborate")
        assert new["props"] == "a cup on the table; a vase by the window"

    def test_one_object_is_named_once(self):
        sheet = _sheet("a cup on the table", holding="a teapot in her right hand")
        new, *_, notes = props.apply_props(sheet, {"add": [
            {"item": "a second cup", "where": "on the tray"},
            {"item": "a teapot", "where": "on the counter"},
            {"item": "a wind chime", "where": "under the eaves"},
        ]}, "elaborate")
        assert new["props"] == "a cup on the table; a wind chime under the eaves"
        assert sum("already on the sheet" in n for n in notes) == 2

    def test_an_added_object_needs_a_place(self):
        new, *_, notes = props.apply_props(
            _sheet("a cup on the table"), {"add": [{"item": "a fan"}]}, "elaborate")
        assert "fan" not in new["props"]
        assert any("no place" in n for n in notes)

    def test_adds_are_capped(self):
        adds = [{"item": f"a {w}", "where": "on the floor"}
                for w in ("basket", "kettle", "lantern", "cushion", "broom")]
        new, *_ = props.apply_props(_sheet(""), {"add": adds}, "elaborate")
        assert len(props.split_items(new["props"])) == props.ELABORATE_ADD_MAX

    def test_holding_stays_the_same_object_in_the_same_hand(self):
        sheet = _sheet("", holding="a teapot in her right hand")
        ok, *_ = props.apply_props(sheet, {"holding": [
            {"index": 0, "holding": "a black cast-iron teapot in her right hand"}]}, "elaborate")
        assert ok["subjects"][0]["holding"] == "a black cast-iron teapot in her right hand"
        moved, *_, notes = props.apply_props(sheet, {"holding": [
            {"index": 0, "holding": "a teapot in her left hand"}]}, "elaborate")
        assert moved["subjects"][0]["holding"] == "a teapot in her right hand"
        assert any("changed hands" in n for n in notes)

    def test_locks_hold(self):
        sheet = _sheet("a cup on the table")
        new, changed, refused, _ = props.apply_props(sheet, {
            "items": [{"index": 0, "item": "a celadon cup on the table"}]}, "elaborate",
            locked=["props"])
        assert new["props"] == "a cup on the table"
        assert refused == ["props"] and not changed


def test_prompt_shows_only_the_writable_boxes_numbered():
    text = props.props_prompt(_sheet("a cup on the table; a vase", holding="a teapot"), "elaborate")
    assert "0. a cup on the table" in text and "1. a vase" in text
    assert "[Held by the people" in text and '"add"' in text
    simple = props.props_prompt(_sheet("a cup on the table"), "simplify")
    assert "[Held by the people" not in simple and '"add"' not in simple


# ── One call for staging, the objects and the leftover tags ─────────────────

from app.prompt import scene_sheet, shaping


def _two() -> dict:
    return normalize_sheet({
        "medium": "anime illustration", "setting": "an autumn classroom",
        "props": "a stack of books on the desk",
        "subjects": [{"who": "a girl", "pose_action": "reading", "holding": "a book in her left hand",
                      "outfit": "a uniform"}],
    })


def test_only_the_asked_jobs_are_in_the_prompt():
    p = shaping.shape_prompt(_two(), staging="restage", props_mode="elaborate", tags=["maple leaf"])
    assert "[Job: stage the moment]" in p and "[Job: fit the objects to it]" in p
    assert "[Job: place the tagger's leftover tags]" in p and "- maple leaf" in p
    assert '"stock_poses"' in p and '"objects":' in p and '"place":' in p
    # the objects job owns props: staging is not offered the box
    stage = p.split("[Job: stage the moment]")[1].split("[Job: fit the objects to it]")[0]
    assert "Scene boxes: story, situation, interaction, effects, camera, layers." in stage
    only = shaping.shape_prompt(_two(), staging="read", props_mode="simplify", tags=[])
    assert "[Job: stage the moment]" not in only and "[Job: place" not in only
    assert shaping.jobs(staging="read", props_mode="keep", tags=[]) == []


def test_each_part_goes_through_its_own_door():
    answer = {
        "staging": {"stock_poses": ["reading quietly at the desk"],
                    "set": {"situation": "a gust scatters her notes", "props": "a vase"},
                    "subjects": [{"index": 0, "set": {"pose_action": "lunging after a flying page",
                                                      "outfit": "a raincoat"}}]},
        "objects": {"items": [{"index": 0, "item": "a toppling stack of library books on the desk"}],
                    "add": [{"item": "a paper lantern", "where": "hanging by the window"}]},
        "place": [{"tag": "maple leaf", "box": "effects", "person": None}],
    }
    sheet, report = shaping.apply_shape(_two(), answer, staging="restage", props_mode="elaborate",
                                        tags=["maple leaf"])
    assert sheet["subjects"][0]["pose_action"] == "lunging after a flying page"
    assert sheet["subjects"][0]["outfit"] == "a uniform"
    # props came from the objects job, not staging's "a vase"
    assert sheet["props"] == ("a toppling stack of library books on the desk; "
                              "a paper lantern hanging by the window")
    assert "props" in report["staging"]["outside"]
    assert report["staging"]["stock"] == ["reading quietly at the desk"]
    assert sheet["effects"] == "maple leaf" and report["place"]["placed"] == ["maple leaf → effects"]


def test_a_missing_part_is_said_and_leftover_tags_are_kept():
    sheet, report = shaping.apply_shape(_two(), {}, staging="move", props_mode="elaborate",
                                        tags=["maple leaf"])
    assert report["staging"]["read"] is False and report["objects"]["read"] is False
    assert sheet["props"] == "a stack of books on the desk"
    assert sheet["unclassified"] == ["maple leaf"]


def test_every_rewrite_is_told_to_decide_and_keep_one_hand_one_answer():
    sheet = _two()
    for p in (scene_sheet.sheet_prompt(), scene_sheet.stage_prompt(sheet, "move"),
              props.props_prompt(sheet, "elaborate"),
              scene_sheet.reconstruct_prompt({"read": sheet, "steps": [], "current": sheet})):
        assert 'Never write "X or Y"' in p, p[:80]
    assert scene_sheet.ONE_HAND_LINE in scene_sheet.stage_prompt(sheet, "move")
    assert scene_sheet.ONE_HAND_LINE in scene_sheet.reconstruct_prompt({"read": sheet, "steps": [], "current": sheet})
    w = scene_sheet.script_prompt(sheet)
    assert "never copy the note's phrasing" in w and "Each hand is written once" in w

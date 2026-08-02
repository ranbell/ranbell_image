"""Session step state, and the scene text that ships with the final prompt."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.scene import compose_final_prompt, parse_brainstorm_sections
from app.muse.schema import (
    MODE_MANUAL,
    missing_inputs,
    new_session,
    next_step,
    public_view,
    step_state,
)

READY = {
    "theme": "雨上がりの屋上", "light_model": "gemma4:e4b", "character_id": "abc",
    "board_workflow": "W.json", "final_workflow": "W.json",
}


def test_a_fresh_session_starts_at_compose():
    session = new_session(READY)
    assert next_step(session) == "compose"
    assert missing_inputs(session, "compose") == []


def test_missing_inputs_names_what_the_step_needs():
    session = new_session({"mode": MODE_MANUAL})
    assert "theme" in missing_inputs(session, "compose")
    assert "lightModel" in missing_inputs(session, "compose")
    assert "character" in missing_inputs(session, "compose")
    assert "boardWorkflow" in missing_inputs(session, "board")
    assert "finalWorkflow" in missing_inputs(session, "render")


def test_manual_only_asks_for_what_the_step_at_hand_needs():
    """Stopping between steps is the point of MANUAL, so a workflow six steps
    away is not this step's problem."""
    session = new_session({**READY, "mode": MODE_MANUAL, "final_workflow": ""})
    assert missing_inputs(session, "compose") == []


def test_auto_asks_for_everything_before_it_starts():
    """AUTO runs the whole chain from one press. Asking per step would let a run
    begin with no final workflow and hit the wall six steps later, having spent
    the board renders getting there."""
    session = new_session({**READY, "final_workflow": ""})
    assert missing_inputs(session, "compose") == ["finalWorkflow"]
    session = new_session({"theme": "t"})
    needs = missing_inputs(session, "compose")
    assert set(needs) == {"lightModel", "character", "boardWorkflow", "finalWorkflow"}


def test_steps_advance_as_results_land():
    session = new_session({**READY, "theme": "t", "light_model": "m"})
    assert next_step(session) == "compose"

    session["seed_tags"] = {
        "background": [{"tag": "rooftop", "source": "compose"}], "person": [],
    }
    assert next_step(session) == "board"


def test_topup_counts_as_done_even_when_it_adds_nothing():
    """Choosing nothing is a valid answer, so the step must not block on it."""
    session = new_session({**READY, "theme": "t", "light_model": "m"})
    session["seed_tags"] = {"background": [{"tag": "a"}], "person": [{"tag": "b"}]}
    session["board"] = {"background": [{"seed_index": 0, "image_id": "x"}],
                        "person": [{"seed_index": 0, "image_id": "y"}]}
    session["harvest"] = {"background": [{"tag": "a"}], "person": [{"tag": "b"}]}
    assert next_step(session) == "topup"

    session["topup_candidates"] = [{"tag": "lamp"}]
    session["topup"] = []
    assert next_step(session) == "merge"


def test_board_is_pending_until_every_slot_lands():
    session = new_session({"theme": "t", "light_model": "m"})
    session["seed_tags"] = {"background": [{"tag": "a"}], "person": [{"tag": "b"}]}
    session["board"] = {
        "background": [{"seed_index": 0, "image_id": "aaa"}],
        "person": [{"seed_index": 0, "image_id": ""}],
    }
    state = step_state(session)
    assert state["board"]["done"] is False
    assert state["board"]["pending"] is True
    assert state["board"]["detail"] == "1/2"

    session["board"]["person"][0]["image_id"] = "bbb"
    assert step_state(session)["board"]["done"] is True


def test_public_view_carries_the_state_the_panel_renders():
    view = public_view(new_session({"theme": "t"}))
    assert view["next_step"] == "compose"
    assert set(view["step_state"]) == set(view["steps"])
    assert "lightModel" in view["needs"]


def test_brainstorm_markdown_splits_into_cards():
    cards = parse_brainstorm_sections(
        "## 提案1：屋上の待ち合わせ\n本文A\n\n## 提案2：階段の途中\n本文B\n"
    )
    assert [c["title"] for c in cards] == ["提案1：屋上の待ち合わせ", "提案2：階段の途中"]
    assert cards[0]["body"] == "本文A"


def test_brainstorm_without_headings_is_one_card():
    cards = parse_brainstorm_sections("just some prose")
    assert cards == [{"title": "", "body": "just some prose"}]
    assert parse_brainstorm_sections("") == []


def test_final_prompt_puts_tags_before_prose():
    prompt = compose_final_prompt(["1girl", "rooftop"], "She waits. The light fades.")
    assert prompt.startswith("1girl, rooftop")
    assert prompt.endswith("She waits. The light fades.")


def test_final_prompt_without_a_scene_is_just_the_tags():
    assert compose_final_prompt(["1girl", "rooftop"], "") == "1girl, rooftop"


# ── the brainstorm split ────────────────────────────────────────────────────
def test_ideas_are_found_at_whatever_heading_level_they_arrive_at():
    """One run wrote four `###` proposals under a single `##` covering letter.
    Splitting on `##` alone made that one card containing all four, and the
    chooser could only offer the whole document."""
    md = ("## Proposal from the director\n\nSome preamble.\n\n"
          "### Idea 1\n\nfirst\n\n### Idea 2\n\nsecond\n\n### Idea 3\n\nthird")
    cards = parse_brainstorm_sections(md)
    assert [c["title"] for c in cards] == ["Idea 1", "Idea 2", "Idea 3"]
    assert cards[0]["body"] == "first"


def test_plain_level_two_ideas_still_split():
    cards = parse_brainstorm_sections("## A\n\nfirst\n\n## B\n\nsecond")
    assert [c["title"] for c in cards] == ["A", "B"]


def test_a_tie_prefers_the_shallower_level():
    cards = parse_brainstorm_sections("## A\n\nx\n\n### B\n\ny")
    assert [c["title"] for c in cards] == ["A"]


def test_a_document_with_no_headings_is_one_card():
    assert parse_brainstorm_sections("just prose") == [{"title": "", "body": "just prose"}]


# ── one render per idea ─────────────────────────────────────────────────────
def _rendering(finals):
    session = new_session(READY)
    session["seed_tags"] = {"background": [{"tag": "a"}], "person": [{"tag": "b"}]}
    session["board"] = {"background": [], "person": []}
    session["harvest"] = {"background": [{"tag": "a"}]}
    session["topup"] = [{"tag": "x"}]
    session["merged"] = {"tags": ["a"]}
    session["scene"] = {"candidates": [{}], "text": "prose"}
    session["finals"] = finals
    return session


def test_a_run_is_not_done_until_the_last_image_lands():
    """A grid three-quarters full is a run still in progress."""
    three_of_four = _rendering([
        {"job_id": "1", "image_id": "a"}, {"job_id": "2", "image_id": "b"},
        {"job_id": "3", "image_id": "c"}, {"job_id": "4", "image_id": ""},
    ])
    state = step_state(three_of_four)["render"]
    assert state["done"] is False
    assert state["pending"] is True
    assert state["detail"] == "3/4"

    done = _rendering([{"job_id": "1", "image_id": "a"}])
    assert step_state(done)["render"]["done"] is True


def test_a_session_written_before_this_still_reads():
    """`final` was a single dict; it reads as a list of one."""
    from app.muse.schema import finals_of
    old = {"final": {"job_id": "1", "image_id": "abc", "positive": "p"}}
    assert [f["image_id"] for f in finals_of(old)] == ["abc"]
    assert finals_of({"final": {}}) == []
    assert finals_of({}) == []


def test_auto_does_not_wait_for_a_scene_to_be_chosen():
    """Choosing one idea is the step AUTO skips — it draws all of them and
    writes each one's prose at render time."""
    session = new_session(READY)
    session["scene"] = {"candidates": [{"title": "a"}, {"title": "b"}]}
    assert step_state(session)["brainstorm"]["done"] is True

    manual = new_session({**READY, "mode": MODE_MANUAL})
    manual["scene"] = {"candidates": [{"title": "a"}]}
    assert step_state(manual)["brainstorm"]["done"] is False
    manual["scene"]["text"] = "prose"
    assert step_state(manual)["brainstorm"]["done"] is True

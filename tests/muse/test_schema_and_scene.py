"""Session step state, and the scene text that ships with the final prompt."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.scene import (
    compose_final_prompt,
    parse_brainstorm_sections,
)
from app.muse.schema import missing_inputs, new_session, next_step, public_view, step_state


def test_a_fresh_session_starts_at_the_split():
    session = new_session({"theme": "雨上がりの屋上", "light_model": "gemma4:e4b"})
    assert next_step(session) == "split"
    assert missing_inputs(session, "split") == []


def test_missing_inputs_names_what_the_step_needs():
    session = new_session()
    assert "theme" in missing_inputs(session, "split")
    assert "lightModel" in missing_inputs(session, "split")
    assert "character" in missing_inputs(session, "board")
    assert "boardWorkflow" in missing_inputs(session, "board")
    assert "finalWorkflow" in missing_inputs(session, "render")


def test_steps_advance_as_results_land():
    session = new_session({"theme": "t", "light_model": "m"})
    session["split"] = {"character": "1girl", "background": "rooftop"}
    assert next_step(session) == "tags"

    session["seed_tags"] = {"background": [{"tag": "rooftop", "source": "split"}], "person": []}
    assert next_step(session) == "board"


def test_board_is_pending_until_every_slot_lands():
    session = new_session({"theme": "t", "light_model": "m"})
    session["split"] = {"character": "x"}
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
    assert view["next_step"] == "split"
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

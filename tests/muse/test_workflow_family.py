"""**Which model family a workflow belongs to.** (2026-09-20)

The Showrunner: "I want to use krea2 in a workflow too, but there are differences
— it needs no negative prompt, 8 steps is enough. Is there a good way to switch
between anima and krea2?"

Muse had no notion of a family at all: a workflow is a filename, and every render
sent Anima's numbers (20/30 steps, cfg 4.0/4.5) and a negative prompt. The table
and the detection live in `muse/family.py`; what is pinned here is that

  * **anima is derived from `defaults.py`**, so an untouched session renders today
    exactly as it did yesterday,
  * a name and a marker both answer, with the marker winning,
  * and **a number the Showrunner set himself always beats the family's** — his
    ask was "4/8 as the default, and let the user change it after that".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.muse import family  # noqa: E402
from app.muse.defaults import ALL_DEFAULTS, DRAFT_DEFAULTS, REFINE_DEFAULTS  # noqa: E402
from app.muse.runtime import render_settings  # noqa: E402


def test_the_name_says_which_family():
    """Named by hand, so it is matched anywhere in the filename, either case."""
    for name in ("krea2_flux.json", "Krea2-Portrait.JSON", "flux_krea2_api.json"):
        assert family.family_from_name(name) == family.FAMILY_KREA2, name
    assert family.family_from_name("API_Anima_Hakushi_Fast.json") == family.FAMILY_ANIMA


def test_anything_else_is_what_it_has_always_been():
    """Every workflow that exists today declares nothing — and must not change."""
    for name in ("sdxl_base.json", "my_workflow.json", ""):
        assert family.resolve_family(name) == family.DEFAULT_FAMILY == "anima"


def test_a_marker_in_the_graph_beats_the_name():
    """A node retitled `muse:family=…` is the escape hatch for an odd filename.

    `_meta.title` is where it goes: a `Note` node has no outputs and is pruned
    from ComfyUI's API export, and a key of our own at the top level would be
    posted to ComfyUI as though it were a node.
    """
    graph = {"3": {"class_type": "KSampler", "_meta": {"title": "muse:family=krea2"}}}
    assert family.resolve_family("API_Anima_Hakushi_Fast.json", graph) == "krea2"

    note = {"9": {"class_type": "Note", "inputs": {"text": "muse:family=anima"}}}
    assert family.resolve_family("krea2_flux.json", note) == "anima"

    # A marker naming a family we do not have falls through to the name.
    unknown = {"3": {"class_type": "KSampler", "_meta": {"title": "muse:family=sd15"}}}
    assert family.resolve_family("krea2_flux.json", unknown) == "krea2"


def test_anima_is_read_from_the_defaults_never_copied():
    """**The anti-regression pin.** If these drifted apart, every existing shoot
    would quietly change the day a family was resolved for it."""
    row = family.settings_for("anima")
    assert row["draft_steps"] == DRAFT_DEFAULTS["draft_steps"]
    assert row["draft_cfg"] == DRAFT_DEFAULTS["draft_cfg"]
    assert row["final_steps"] == REFINE_DEFAULTS["final_steps"]
    assert row["final_cfg"] == REFINE_DEFAULTS["final_cfg"]
    assert row["negative"] is True

    inputs = dict(ALL_DEFAULTS)
    for draft in (True, False):
        assert render_settings(inputs, draft=draft, family="anima") == \
            render_settings(inputs, draft=draft)


def test_krea2_asks_for_four_and_eight_and_leaves_cfg_alone():
    """His numbers. **`cfg` is left to the workflow** — "leave it to the
    workflow" — so the key is absent and nothing is written into the graph."""
    inputs = dict(ALL_DEFAULTS)
    draft = render_settings(inputs, draft=True, family="krea2")
    final = render_settings(inputs, draft=False, family="krea2")
    assert draft["steps"] == 4 and final["steps"] == 8
    assert "cfg" not in draft and "cfg" not in final
    # The canvas is not a family's business.
    assert draft["width"] == inputs["width"] and draft["height"] == inputs["height"]


def test_a_number_he_set_himself_always_wins():
    """Set steps to 6, switch to a krea2 workflow, still 6."""
    inputs = {**ALL_DEFAULTS, "draft_steps": 6, "final_steps": 9}
    assert render_settings(inputs, draft=True, family="krea2")["steps"] == 6
    assert render_settings(inputs, draft=False, family="krea2")["steps"] == 9
    # cfg he set is not thrown away either, even by a family that wants none.
    with_cfg = {**ALL_DEFAULTS, "draft_cfg": 2.5}
    assert render_settings(with_cfg, draft=True, family="krea2")["cfg"] == 2.5


def test_an_unknown_family_is_the_default_not_an_error():
    """A marker from the future, or a typo, must not stop a shoot."""
    assert family.settings_for("nope")["label"] == family.FAMILIES["anima"]["label"]
    assert family.sends_negative("nope") is True
    assert family.render_overrides("nope", draft=True)["steps"] == \
        DRAFT_DEFAULTS["draft_steps"]


def test_reading_the_graph_never_stops_a_render():
    """`for_workflow` is called with whatever client the caller has.

    A missing file is the render's own error to raise a moment later, and tests
    hand the runner a stub. Either way the filename still answers.
    """
    class Boom:
        def load_workflow(self, name):
            raise FileNotFoundError(name)

    class NotAGraph:
        def load_workflow(self, name):
            return object()

    assert family.for_workflow(Boom(), "krea2_flux.json") == "krea2"
    assert family.for_workflow(NotAGraph(), "krea2_flux.json") == "krea2"
    assert family.for_workflow(None, "krea2_flux.json") == "krea2"
    assert family.for_workflow(Boom(), "whatever.json") == "anima"

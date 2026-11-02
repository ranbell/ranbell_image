"""**A step the screen does not know about blanks the counter.** (2026-11-02)

The Showrunner asked for a transition diagram, because reading each reference
on its own made the wait grow with the number of pictures.

Building it turned up a quiet bug: the step counter and the step total were two
separate pieces of arithmetic, and `buildingPrompt`, `placingHints` and
`mergingSheets` were in neither list. `indexOf` returned -1 for them, so the
"3 / 6" overlay vanished for the whole of those steps — the longest ones.

So: every phase `run_refine_prompt` reports has to be a step the screen plans
for, and every step it plans for has to have a label in both locales.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = ROOT / "backend/app/jobs/runners.py"
APP = ROOT / "frontend/src/App.vue"
LOCALES = ROOT / "frontend/src/locales"


def _reported_phases() -> set[str]:
    """Every literal code `run_refine_prompt` passes to `_phase`."""
    tree = ast.parse(RUNNERS.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_refine_prompt"
    )
    out: set[str] = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "_phase"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            out.add(node.args[0].value)
    return out


def _stepper_block() -> str:
    src = APP.read_text(encoding="utf-8")
    start = src.index("const refineStepOrder")
    end = src.index("const refineSteps ", start)
    return src[start:end]


def _planned_steps() -> set[str]:
    """Codes the stepper pushes onto its order, plus the retry aliases."""
    block = _stepper_block()
    pushed: set[str] = set()
    # the list the order starts from, then everything pushed onto it
    first = re.search(r"const o = \[([^\]]*)\]", block)
    if first:
        pushed |= set(re.findall(r"'([A-Za-z]+)'", first.group(1)))
    for call in re.findall(r"o\.push\(([^)]*)\)", block):
        pushed |= set(re.findall(r"'([A-Za-z]+)'", call))
    aliases = re.search(r"REFINE_PHASE_ALIAS = \{([^}]*)\}", block)
    if aliases:
        pushed |= set(re.findall(r"(\w+)\s*:", aliases.group(1)))
    return pushed


def _labels(name: str) -> dict:
    return json.loads((LOCALES / name).read_text(encoding="utf-8"))["refine"]["phase"]


def test_the_phases_are_read_from_the_runner():
    phases = _reported_phases()
    assert len(phases) >= 8, f"_phase の呼び出しが読めていない（{sorted(phases)}）"


def test_every_phase_the_runner_reports_is_a_step_the_screen_plans_for():
    missing = sorted(_reported_phases() - _planned_steps())
    assert not missing, f"報告されるのに画面が段取りに入れていない: {missing}"


@pytest.mark.parametrize("locale", ["ja.json", "en.json"])
def test_every_planned_step_has_a_label(locale):
    table = _labels(locale)
    missing = sorted(s for s in _planned_steps() if not str(table.get(s, "")).strip())
    assert not missing, f"{locale} の refine.phase に無い: {missing}"


def test_the_counter_and_the_total_come_from_the_one_list():
    """Two pieces of arithmetic are two things that drift apart."""
    src = APP.read_text(encoding="utf-8")
    assert "const refineTotalSteps = computed(() => refineStepOrder.value.length)" in src
    assert "refineStepOrder.value.indexOf(code)" in src

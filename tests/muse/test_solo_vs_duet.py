"""**One person or two.** (2026-09-09)

The Showrunner: "when there is only one person it edits muse_b's tags. **The
explanation of one-versus-two is not enough**".

The contract had said from the start "write `wearing_b` / `beat_b` only when a
partner Muse is present". What was missing was **telling it whether one is
present** — `blank()` fills every field, so the model always saw an empty set of
second-person fields. **An empty field looks like "fill me in".**

The fix is two layers:
    do not show the fields   `ledger.for_model` drops `_b` when solo
    say it in words          `ledger.cast_line` states the headcount in one line
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.muse import ledger as L

WRITER = Path(__file__).resolve().parents[2] / "backend/app/muse/writer.py"


def test_solo_hides_the_second_persons_slots():
    led = {**L.blank(), "wearing": "cardigan", "beat": "standing"}
    solo = L.for_model(led, partner=False)
    assert "wearing_b" not in solo and "beat_b" not in solo
    assert solo["wearing"] == "cardigan"


def test_a_duet_keeps_them():
    led = {**L.blank(), "wearing_b": "sundress", "beat_b": "leaning in"}
    both = L.for_model(led, partner=True)
    assert both["wearing_b"] == "sundress"
    assert both["beat_b"] == "leaning in"


def test_the_cast_line_says_which_it_is():
    solo = L.cast_line(partner=False, name_a="Mio")
    assert "solo" in solo and "Mio" in solo
    # Solo, it says **do not write** the second person's fields (`expression_b` too,
    # since 2026-09-10)
    assert "never write wearing_b" in solo
    for key in ("wearing_b", "beat_b", "expression_b"):
        assert key in solo, key

    duet = L.cast_line(partner=True, name_a="Mio", name_b="Sumire")
    assert "Mio" in duet and "Sumire" in duet
    # With two, nothing is **forbidden** — it says whose each one is.
    assert "never write" not in duet
    assert "Sumire's" in duet
    for key in ("wearing_b", "beat_b", "expression_b"):
        assert key in duet, key


def test_every_ledger_shown_to_a_model_goes_through_for_model():
    """**All four seats.** One pass-through and the empty fields leak from there.

    The same manner as the `think=False` test: it looks at the shape of the call —
    so a new seat fails it.
    """
    tree = ast.parse(WRITER.read_text(encoding="utf-8"))
    raw = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "dumps"):
            continue
        arg = node.args[0] if node.args else None
        # With `json.dumps(ledger_mod.for_model(...))` the inside is a Call.
        ok = (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "for_model"
        )
        if not ok:
            raw.append(node.lineno)
    assert not raw, f"台帳を素のまま模型に見せている行: {raw}"


def test_the_drop_rule_warns_that_the_beat_still_names_the_garment():
    """The Showrunner's idea — "the garment sometimes remains in the action, so do not
    forget to clear it"."""
    from app.muse import writer

    text = writer.WRITER_SYSTEM
    assert "The beat often still names it" in text
    assert "hoodie pocket" in text

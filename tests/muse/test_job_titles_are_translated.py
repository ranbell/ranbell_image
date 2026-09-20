"""**A task name registered with the spooler has to be readable.** (2026-09-20)

The Showrunner: "please fix the task names registered in the control panel".

The Control Room printed `job.title` as it stands — `generate_actress_diary`,
`muse_board` — which is the identifier a job was submitted under, not a label.
`jobTitle.*` already existed in both locales and **nothing read it**: fifteen
entries, three of them for names no longer submitted, and every Muse and Invoke
job missing.

The titles are read out of the backend here, so a job added later without a label
fails this rather than reaching the console as source code.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend/src/locales"


def _submitted_titles() -> dict[str, str]:
    """Every literal title passed to `spooler.submit`, and where it lives.

    A title built from a variable (`f"invoke.spirit/{name}"`) has no literal to
    read; those are covered by the stem test below.
    """
    found: dict[str, str] = {}
    for path in (ROOT / "backend/app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "submit"):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, f"{path.name}:{node.lineno}")
                    break
    return found


def _titles(name: str) -> dict[str, str]:
    return json.loads((LOCALES / name).read_text(encoding="utf-8"))["jobTitle"]


def test_every_submitted_task_has_a_label_in_both_locales():
    submitted = _submitted_titles()
    assert len(submitted) > 30, f"submit が読めていない（{len(submitted)}本）"

    ja, en = _titles("ja.json"), _titles("en.json")
    for title, where in sorted(submitted.items()):
        for name, table in (("ja", ja), ("en", en)):
            assert title in table, f"{title}（{where}）が {name}.json の jobTitle に無い"
            assert table[title].strip(), f"{title} が空（{name}）"


def test_the_labels_are_actually_translated():
    """A Japanese label that is the English one is a label nobody wrote."""
    ja, en = _titles("ja.json"), _titles("en.json")
    assert set(ja) == set(en), "ja と en で鍵が揃っていない"
    # Product names carry across (WD14, MRL, UMAP); a label that is *only* the
    # English one has not been translated.
    same = {k for k in ja if ja[k] == en[k]}
    assert not same, f"日本語になっていない: {sorted(same)}"


def test_the_stems_of_generated_names_are_there_too():
    """`comfy_generate (API_x.json)` / `invoke.spirit/水` — the stem is translated
    and the tail is left alone (`frontend/src/jobLabel.js`)."""
    ja = _titles("ja.json")
    for stem in ("comfy_generate", "character_board", "invoke.spirit",
                 "invoke.respin", "invoke.gen", "invoke.align", "invoke.finalize"):
        assert stem in ja, f"{stem} の対訳が無い（動的な名前の頭）"

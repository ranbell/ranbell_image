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


# ── The note under the title ────────────────────────────────────────────────

def _progress_keys() -> dict[str, str]:
    """Every `key=` a progress report names, and where it lives.

    A key built from a value (`key=f"inversionStage{n}"`) is returned as its stem
    plus `*`; those are checked by hand below, since the number is not in the
    source as a literal.
    """
    import re

    call = re.compile(r"(?:reporter\.update|_report)\(([\s\S]{0,320}?)\)\s*\n")
    found: dict[str, str] = {}
    for path in (ROOT / "backend/app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for match in call.finditer(src):
            body = match.group(1)
            line = src[:match.start()].count("\n") + 1
            for key in re.findall(r'key="([A-Za-z0-9_]+)"', body):
                found.setdefault(key, f"{path.name}:{line}")
            for stem in re.findall(r'key=f"([A-Za-z0-9_]+)\{', body):
                found.setdefault(stem + "*", f"{path.name}:{line}")
    return found


def _progress(name: str) -> dict[str, str]:
    return json.loads((LOCALES / name).read_text(encoding="utf-8"))["jobProgress"]


def test_every_progress_note_has_a_line_in_both_locales():
    """**Half the console was always foreign.** (2026-09-20)

    The note under a job's title was written wherever the job was — the Muse jobs
    in Japanese (「日記を書いてもらっています」), everything else in English
    ("Waiting in the ComfyUI queue…"). A job now reports a key.
    """
    keys = _progress_keys()
    assert len(keys) > 40, f"報告が読めていない（{len(keys)}本）"

    ja, en = _progress("ja.json"), _progress("en.json")
    for key, where in sorted(keys.items()):
        if key.endswith("*"):
            continue
        for name, table in (("ja", ja), ("en", en)):
            assert key in table, f"{key}（{where}）が {name}.json の jobProgress に無い"
            assert table[key].strip(), f"{key} が空（{name}）"


def test_the_six_stages_of_the_inversion_are_named():
    """`key=f"inversionStage{n}"` — the number is not a literal, so it is checked
    here against the stages the stream actually emits."""
    ja, en = _progress("ja.json"), _progress("en.json")
    assert "inversionStage*" in _progress_keys(), "段階の報告が鍵を持っていない"
    for n in range(6):
        assert f"inversionStage{n}" in ja, f"inversionStage{n} が ja に無い"
        assert f"inversionStage{n}" in en, f"inversionStage{n} が en に無い"


def test_the_placeholders_match_between_the_locales():
    """`{done}` in one language and `{items}` in the other renders as raw text."""
    import re

    ja, en = _progress("ja.json"), _progress("en.json")
    for key in sorted(set(ja) & set(en)):
        holes_ja = set(re.findall(r"\{(\w+)\}", ja[key]))
        holes_en = set(re.findall(r"\{(\w+)\}", en[key]))
        assert holes_ja == holes_en, f"{key} の差し込みが食い違う: {holes_ja} / {holes_en}"


def test_what_a_job_passes_is_what_the_line_asks_for():
    """A note that passes `n` while the line says `{count}` renders the braces.

    Read from the call itself: the keyword arguments after `key=` are the
    placeholders, and they have to be exactly the ones in the locale line.
    """
    import re

    call = re.compile(r"(?:reporter\.update|_report)\(([\s\S]{0,320}?)\)\s*\n")
    en = _progress("en.json")
    checked = 0
    for path in (ROOT / "backend/app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for match in call.finditer(src):
            body = match.group(1)
            named = re.search(r'key="([A-Za-z0-9_]+)"', body)
            if not named:
                continue
            key = named.group(1)
            passed = set(re.findall(r"(\w+)\s*=", body[named.end():]))
            holes = set(re.findall(r"\{(\w+)\}", en.get(key, "")))
            line = src[:match.start()].count("\n") + 1
            assert passed == holes, (
                f"{key}（{path.name}:{line}）が渡すのは {sorted(passed)}、"
                f"文面が求めるのは {sorted(holes)}"
            )
            checked += 1
    assert checked > 40, f"報告が読めていない（{checked}本）"

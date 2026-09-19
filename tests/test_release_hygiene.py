"""**What ships does not carry this workshop's identity.** (2026-09-13)

The Showrunner's pre-release check. That the code that lands in git contains

    1. personal details (real names, email, the home IP, local paths)
    2. the name of the image model the Showrunner uses
    3. the identity of uncensored models

is verified **by reading alone**. Three places really had it:

    tests/api/test_muse_image_filter.py   models=["nyaIris.safetensors"]
    tests/muse/test_facets.py             a model's full id in a docstring
    backend/app/muse/identity.py          a comment naming the "uncensored build"
                                          and why

None of it affected behaviour; all of it was decoration. **Decoration comes back**
— writing up a measurement, one naturally adds "which model it was measured on".
Hence a watchman.

The scan is over `git ls-files`. **`private/` is not included** (`.gitignore`), so
the lab tools may keep writing real machine names as before.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: 配り物に出してはいけない語。**なぜ駄目かを一緒に置く** —— 消すときに
#: 迷わないように。
FORBIDDEN: tuple[tuple[str, str], ...] = (
    # ② 総監督の画像モデル（環境固有）
    (r"nyaIris", "総監督の画像モデル名"),
    (r"novaAnima", "総監督の画像モデル名"),
    (r"PPPAnima", "総監督の画像モデル名"),
    (r"JANIMA", "総監督の画像モデル名"),
    # ③ 非検閲版モデルの素性
    (r"uncensored", "非検閲版モデルの素性"),
    (r"非検閲", "非検閲版モデルの素性"),
    (r"abliterat", "非検閲版モデルの素性"),
    (r"heretic", "非検閲版モデルの素性"),
    (r"fredrezones", "模型の配布元"),
    (r"jikepjikep", "模型の配布元"),
    # ① 個人情報
    (r"192\.168\.\d+\.\d+", "手元のネットワーク"),
    (r"/home/[a-z]", "手元のパス"),
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.(?:com|org|net|jp)", "メールアドレス"),
)

#: 語そのものを並べているこのファイルは、当然ひっかかる。
SELF = "tests/test_release_hygiene.py"


def _tracked_text_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    keep = (".py", ".vue", ".js", ".mjs", ".json", ".md", ".yml", ".yaml",
            ".css", ".html", ".example")
    return [
        p for p in out
        if p.endswith(keep) and p != SELF and "package-lock.json" not in p
    ]


@pytest.mark.parametrize("pattern,why", FORBIDDEN)
def test_nothing_in_git_carries_it(pattern: str, why: str):
    rx = re.compile(pattern, re.I)
    hits: list[str] = []
    for rel in _tracked_text_files():
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{i}  {line.strip()[:90]}")
    assert not hits, f"{why}が git に載っている:\n" + "\n".join(hits[:20])


def test_the_lab_is_out_of_scope():
    """`private/` is outside the scan — the lab tools may write real machine names."""
    assert not any(p.startswith("private/") for p in _tracked_text_files())
    assert (ROOT / "private").is_dir(), "台そのものは在る（git 管理外なだけ）"

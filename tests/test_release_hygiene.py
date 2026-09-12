"""**配るものに、この作業場の素性を載せない。**（2026-09-13）

総監督のリリース前点検。git に載るコードに

    ① 個人情報（実名・メール・自宅の IP・手元のパス）
    ② 総監督が使っている画像モデルの名前
    ③ 非検閲版モデルの素性

が入っていないことを、**読むだけで**確かめる。実際に3箇所あった:

    tests/api/test_muse_image_filter.py   models=["nyaIris.safetensors"]
    tests/muse/test_facets.py             docstring に模型の完全な id
    backend/app/muse/identity.py          コメントに「uncensored 版」と、その理由

どれも動作には関わらない飾りだった。**飾りは戻ってきやすい** —— 実測を書き足す
ときに、つい「どの模型で測ったか」を添える。だから番人を置く。

走査は `git ls-files` 基準。**`private/` は入らない**（`.gitignore`）ので、
台の道具は今までどおり実機の名前を書いてよい。
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
    """`private/` は走査に入らない —— 台の道具は実機の名前で書いてよい。"""
    assert not any(p.startswith("private/") for p in _tracked_text_files())
    assert (ROOT / "private").is_dir(), "台そのものは在る（git 管理外なだけ）"

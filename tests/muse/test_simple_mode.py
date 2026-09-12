"""シンプルモード —— 会話のたびに状況を整理して、前回のプロンプトを直す。

総監督（2026-09-06）「関数で防ぐというのは理屈は分かるしテストを行って成果は
出してきたが、**監督の指示がダイレクトにプロンプトに伝わらないのであれば意味が
ない**」。

ここで見るのは、書き直す人が何を書くかではなく（それは実機で測る）、
**こちらが守ると決めた三つ**だけ:

    1. 識別行を写し直す（髪型を言われたら切り方だけ譲る）
    2. 打ち消し・禁止を出力から落とす
    3. 段（compile / 欄ごとの係 / weave / 箱）を踏まない
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, shared  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)




def test_parse_reads_now_and_the_whole_prompt():
    now, prompt = chain.parse_simple_rewrite(
        "NOW: パーカーを脱いで、白シャツ一枚で立っている。\n"
        "PROMPT: 1girl, solo, Mio,\nMio: standing, white_shirt,\nindoors"
    )
    assert now.startswith("パーカーを脱いで")
    assert prompt.startswith("1girl, solo, Mio,")
    assert prompt.endswith("indoors")


def test_parse_gives_up_cleanly_when_the_shape_is_not_there():
    assert chain.parse_simple_rewrite("すみません、書けませんでした") == ("", "")



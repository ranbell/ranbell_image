"""**同じことを二度渡さない。**（2026-09-10）

総監督「Muse による再チェックがかなり時間かかるので、そこかな？ コンテキスト
全部渡してるからすごく時間かかってる」。

測ったら、犯人は再チェックではなく**女優のプロンプト**だった。1ターンで読む
入力は約 9,500tok で、そのうち女優が 5,300tok（56%）。しかも classic の女優
条文が持つ出力書式と `REFINE_OUTPUT` が**二重**に入っていた。

入力の処理は実測 **約 300 tok/s**（LLM が VRAM に 7.4GB しか載らず、残りが
システムメモリ）。**字数がそのまま秒になる。**
"""
from __future__ import annotations

import re

from app.muse import crew
from app.muse_refine import persona, writer

CHAR = {
    "identity_tags": ["1girl", "silver_hair"],
    "name": "Mio", "name_ja": "みお", "character_id": "c1",
    "personality": {"traits": ["おだやか"], "summary": "", "inner": [],
                    "likes": [], "dislikes": []},
    "palette": [], "signature_prop": "",
}
SESSION = {"character": CHAR, "session_id": "x",
           "inputs": {"locale": "ja"}, "opened": True}


def _system() -> str:
    return persona.actress_system(SESSION, locale="ja", ledger={}, now="")


def test_the_output_format_is_given_once():
    """**二つ並べると、どちらに従うか決めさせることになる。**"""
    s = _system()
    assert len(re.findall(r"OUTPUT FORMAT", s)) == 1
    assert len(re.findall(r"^SAY:", s, re.M)) == 1


def test_her_voice_and_contract_stay():
    """速さのために人格を削らない —— 落とすのは書式だけ。"""
    s = _system()
    assert crew.PRODUCTION_CONTRACT[:120] in s
    assert persona.ENTERTAINMENT_CRAFT[:80] in s
    assert persona.REFINE_OUTPUT[:80] in s


def test_the_cut_is_safe_when_the_marker_moves():
    """classic 側の文言が変わったら、**黙って人格まで削らない**。"""
    assert persona._without_classic_output("no marker here") == "no marker here"
    got = persona._without_classic_output(
        "voice and contract\n\nOUTPUT FORMAT — labelled blocks, nothing else:\nSAY: …")
    assert got == "voice and contract"


def test_verify_reads_her_voice_but_not_the_craft_guide():
    """再判定に要るのは声だけ。愛らしさの指針は判定に関係ない。"""
    import inspect

    src = inspect.getsource(writer.verify_and_repair)
    assert "{voice}" in src
    # コメントではなく**埋め込み**を見る（`{persona.ENTERTAINMENT_CRAFT}`）。
    assert "{persona.ENTERTAINMENT_CRAFT}" not in src


def test_the_actress_prompt_stays_under_its_measured_budget():
    """**字数がそのまま秒になる。** 上限は実測で決める（300 tok/s）。"""
    n = len(_system())
    assert n < 12000, f"{n}字 —— 約 {n/3/300:.1f}s を毎ターン読むことになる"

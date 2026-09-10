"""**無言の吹き出しを出さない。**（2026-09-10）

総監督「処理に失敗して無言になってますね」。実機（`cdf8d4f7` 23:45:56）で、
板を見せた回に **ASIDE だけ返って SAY が空**になり、内心は出たのに台詞のほうは
名前と空の吹き出しだけが残った。

    23:45:56  actress_saw_board  試し撮り 1枚を見せた
    23:45:56  actress            （空）

一段目の撮り直しは「返事が丸ごと空」だけを見ていた。読めないモデルは黙るが、
**読めるモデルも書式を落とす**ことがある。見るのは「彼女が喋ったか」。
"""
from __future__ import annotations

import asyncio

from app.muse_refine import ledger as L, talk, writer

#: 実機で起きた形 —— 模型は何かを返したが、台詞として取り出せるものが無かった。
NO_LINE = "   \n"
FULL = "SAY: はい、総監督。\nASIDE: （どきどき）\n"


class _Ollama:
    """一度目は書式を落とし、二度目はちゃんと返す。"""

    def __init__(self, first: str, second: str = FULL):
        self.replies = [first, second]
        self.calls: list[bool] = []      # 絵つきで呼ばれたか

    async def generate_vlm(self, prompt, images, **kw):
        self.calls.append(True)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]

    async def generate_text(self, prompt, **kw):
        self.calls.append(False)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def _turn(o, images=None):
    return asyncio.run(writer.actress_turn(
        o, model="m", locale="ja", name="澪", now="", ledger=L.blank(),
        identity_blurb="", user_line="おしまいね", director_tail="", images=images,
    ))


def test_an_empty_say_on_an_image_turn_is_retried():
    o = _Ollama(NO_LINE)
    out = _turn(o, images=[b"jpeg"])
    assert o.calls == [True, False], "絵つき → 絵抜きで撮り直していない"
    assert out["blind"] is True
    assert "はい、総監督" in out["say"]


def test_a_good_first_answer_is_not_retried():
    o = _Ollama(FULL)
    out = _turn(o, images=[b"jpeg"])
    assert o.calls == [True]
    assert out["blind"] is False


def test_a_turn_with_no_picture_is_left_alone():
    """絵を見せていない回は撮り直さない（別の失敗なので勝手に二度叩かない）。"""
    o = _Ollama(NO_LINE)
    out = _turn(o)
    assert o.calls == [False]
    assert not out["say"].strip()
    # 黙った回は、返ってきたものを持ち帰る（次に読めるように）
    assert "raw" in out


def _publish(say, aside=""):
    s = {"session_id": "s", "character": {"character_id": "a", "name_ja": "澪"},
         "partner_character": {}, "chat": [], "inputs": {"locale": "ja"}}
    talk.publish_actress_turn(
        s, {"say": say, "aside": aside, "propose": {}, "my_feel": "", "pitch": ""},
        locale="ja", lead_name="澪",
    )
    return s


def test_an_empty_line_never_becomes_a_row():
    s = _publish("", aside="（どきどき）")
    kinds = [(r.get("meta") or {}).get("kind") for r in s["chat"]]
    assert "say" not in kinds
    # 内心のほうは今まで通り出る
    assert "banter" in kinds


def test_the_silence_is_written_down():
    """黙って落とさない —— 何が起きたかは記録に残す。"""
    s = _publish("", aside="（どきどき）")
    kinds = [row.get("kind") for row in (s.get("refine_log") or [])]
    assert "actress_said_nothing" in kinds


def test_a_real_line_still_becomes_a_row():
    s = _publish("はい、総監督。", aside="（どきどき）")
    says = [r for r in s["chat"] if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 1 and says[0]["text"] == "はい、総監督。"

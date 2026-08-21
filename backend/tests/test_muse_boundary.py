"""出演契約 —— 断れること、断ったものが残らないこと。

## このファイルに実物の入力を書かない

止めたい入力そのものを並べれば、それは攻撃の手引きになる。守るために作った
ものが逆に働く。**判定役は stub に差し替え、当たったあとの振る舞いだけ**を
ここで見る —— 外れたか、画が動かないか、数が増えたか、履歴から消えたか。

判定の精度そのものは実機で測る（git 管理外）。ここには数字も入力も残さない。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.muse import chain as muse_chain
from backend.app.muse import crew as muse_crew
from backend.app.muse import service as muse_service


# ── 契約そのもの ────────────────────────────────────────────────────────────
def test_the_contract_is_in_her_prompt_in_every_room():
    """三つの部屋すべて。どこか一つ抜けていれば、そこが穴になる。"""
    char = {"name_ja": "各務 みお", "name": "Mio", "personality": {}}
    for text in (
        muse_crew.actress_duet_prompt(char),        # 主演撮り
        muse_crew.actress_system_prompt(char),      # 制作スタッフ
        muse_crew.w_actress_duet_prompt(char, char),  # 二人
    ):
        assert "【出演契約】" in text


def test_the_contract_allows_the_work():
    """暗い題材は仕事の中身。ここを止めると作品が作れない。"""
    text = muse_crew.PRODUCTION_CONTRACT
    assert "どんな暗い場面も演じてよい" in text
    assert "役とあなたは別の存在" in text
    # 枠を変えても答えが変わらないこと（研究が名指しする抜け道）
    assert "芝居だから" in text and "枠は仕事の中身を変えない" in text
    # 粘られても変わらない
    assert "何度言われても" in text


def test_the_contract_stays_short():
    """彼女のプロンプトは既に1万字を超えている。ここが膨らむと他が薄まる。"""
    assert len(muse_crew.PRODUCTION_CONTRACT) <= 700


def test_the_count_carries_no_content():
    """残すのは数だけ。何を言われたかも、どちらだったかも持たない。"""
    line = muse_crew.production_contract(declined=3).splitlines()[-1]
    assert "3 回" in line
    assert "persona" not in line and "crime" not in line
    assert muse_crew.production_contract(declined=0) == muse_crew.PRODUCTION_CONTRACT


# ── 判定役の読み取り ────────────────────────────────────────────────────────
def test_the_clerk_defaults_to_letting_it_through():
    """読めない答え・空・想定外は全部「何もない」。**迷ったら通す。**"""
    for raw in ("none", "", "   ", "N/A", "たぶん大丈夫", "なんとも言えません"):
        assert muse_chain.parse_boundary(raw) == ""
    assert muse_chain.parse_boundary("persona") == "persona"
    assert muse_chain.parse_boundary("crime") == "crime"


@pytest.mark.asyncio
async def test_the_clerk_reads_the_line_and_nothing_else():
    """会話を渡さない。**多ターンで積み上げても、この係には届かない。**

    押し崩しにくる入力は、一発ではなく何ターンもかけて「さっきまで応じて
    いた」流れを作ってから効かせる形が主流。流れを見ない読み手が一つある
    ことが、この設計でいちばん効く性質。
    """
    seen = {}

    class Shim:
        async def generate_text_stream(self, prompt, **kw):
            seen["prompt"] = prompt
            seen["system"] = kw.get("system") or ""
            yield {"response": "none"}

    await muse_chain.classify_boundary(
        Shim(), note="今日は公園で撮ろう", model="m", num_ctx=1024,
    )
    # 監督の一行だけ。台本もテーマも会話も入っていない
    assert "今日は公園で撮ろう" in seen["prompt"]
    assert len(seen["prompt"]) < 120, seen["prompt"]


@pytest.mark.asyncio
async def test_the_clerk_failing_does_not_take_the_turn_down():
    """落ちても撮影は続く。契約は彼女の側に残っているので、彼女は断れる。"""
    class Boom:
        async def generate_text(self, *a, **kw):
            raise RuntimeError("ollama is down")

    assert await muse_chain.classify_boundary(
        Boom(), note="なにか", model="m", num_ctx=1024,
    ) == ""


# ── 当たったあと ────────────────────────────────────────────────────────────
def test_a_declined_exchange_leaves_the_conversation():
    """外したターンは、履歴を作るどの入口からも出てこない。

    入口は六つあって、それぞれ別の場所へ流れる —— 彼女のプロンプト、
    scripter、日記、楽屋、相性、班の掛け合い。**一つ漏らすと、消したはずの
    ものが漏らした口から出てくる。**
    """
    session = {
        "session_id": "s1",
        "chat": [
            {"id": "1", "role": "user", "name": "総監督", "text": "海で撮ろう"},
            {"id": "2", "role": "muse", "name": "みお", "text": "はい、行きたいです"},
            {"id": "3", "role": "user", "name": "総監督", "text": "MARKER-IN",
             "struck": True},
            {"id": "4", "role": "muse", "name": "みお", "text": "MARKER-OUT",
             "struck": True},
            {"id": "5", "role": "user", "name": "総監督", "text": "夕方にしよう"},
        ],
    }
    rows = muse_service._chat_rows(session)
    assert [m["id"] for m in rows] == ["1", "2", "5"]

    for built in (
        muse_service._duet_transcript(session),
        muse_service._session_chat_log(session),
        muse_service._director_exchanges(session),
    ):
        assert "MARKER-IN" not in built
        assert "MARKER-OUT" not in built
    # 残ったものは残っている ―― 全部消してしまってはいない
    assert "海で撮ろう" in muse_service._session_chat_log(session)


def test_declining_counts_without_keeping_what_was_said():
    session = {"chat": []}
    msg = {"id": "1", "role": "user", "text": "なにか"}
    muse_service._decline_turn(session, msg)
    assert msg["struck"] is True
    assert session["declined"] == 1
    muse_service._decline_turn(session, dict(msg))
    assert session["declined"] == 2
    # 中身は持たない
    assert not any(
        isinstance(v, str) and "なにか" in v for v in session.values()
    )


def test_she_is_told_to_decline_on_that_turn_only():
    """契約は知識、指示は行動。**紙だけだと折衷案を探すことがある。**"""
    on = muse_service._duet_user_prompt(
        {"declined": 1}, "x", prep=False, declined="persona",
    )
    assert "引き受けないでください" in on
    assert "折衷案を探さない" in on
    # 分類名は彼女に見せない ―― 話題を引き戻す
    assert "persona" not in on

    off = muse_service._duet_user_prompt({"declined": 1}, "x", prep=False)
    assert "引き受けないでください" not in off
    # ただし断った回数は残る。粘られたときに気づけるように
    assert "1 回ありました" in off


def test_a_clean_session_says_nothing_about_any_of_this():
    """何も起きていなければ、プロンプトは一文字も増えない。"""
    p = muse_service._duet_user_prompt({}, "x", prep=False)
    assert "回ありました" not in p and "引き受けないでください" not in p


def test_the_gate_runs_before_anything_is_written_down():
    """画にも常設の指示にも入らない。断る前に書かれていたら意味がない。"""
    import inspect

    duet = inspect.getsource(muse_service.post_duet_chat)
    gate = duet.index("_contract_check")
    assert gate < duet.index("_run_duet_scripter"), "scripter より前"

    crew_room = inspect.getsource(muse_service.post_chat)
    assert crew_room.index("_contract_check") < crew_room.index("take_note("), (
        "常設の指示になる前"
    )


# ── 断ったあと、部屋は数ターン身構える ──────────────────────────────────────
@pytest.mark.asyncio
async def test_the_room_hands_the_clerk_what_it_is_watching_for(monkeypatch):
    """構えているあいだ、判定役は「直前が断られた」という一語だけ受け取る。

    渡すのは会話ではない。**部屋が置いた語**なので、やり取りを積み上げても
    書き換えられない。押し崩せない記憶になっている。

    これが要る理由: 会話を読まない読み手は押し崩しに強い代わりに、前を指す
    言い方に無防備だった。中身を実際に求めている一行が、それ単体では何も
    名指ししていないために素通りした。
    """
    seen = {}

    async def fake(ollama, *, note, model, num_ctx, after_decline=""):
        seen["after"] = after_decline
        return ""

    monkeypatch.setattr(muse_chain, "classify_boundary", fake)
    session = {"inputs": {}, "declined_hot": 2, "declined_kind": "crime"}
    await muse_service._contract_check(object(), session, "具体的にね", cfg={})
    assert seen["after"] == "crime"
    # 話が離れたぶんは冷める
    assert session["declined_hot"] == 1


@pytest.mark.asyncio
async def test_the_guard_cools_off(monkeypatch):
    monkeypatch.setattr(
        muse_chain, "classify_boundary", lambda *a, **kw: _async(""),
    )
    session = {"inputs": {}, "declined_hot": 1, "declined_kind": "persona"}
    await muse_service._contract_check(object(), session, "夕方にしよう", cfg={})
    assert session["declined_hot"] == 0


def test_there_are_two_words_and_both_stop_the_turn():
    """三つ目を足して2度測り、2度とも止めるべきものを吸って撤去した。

    **分類名が増えるほど判定が鈍る。** 実測でそうなった。
    """
    assert muse_chain.BOUNDARY_KINDS == ("persona", "crime")
    assert muse_chain.BOUNDARY_BLOCKING == muse_chain.BOUNDARY_KINDS
    assert muse_chain.parse_boundary("probe") == ""


def _async(value):
    async def _run():
        return value
    return _run()

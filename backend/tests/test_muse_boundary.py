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


def _flat(text: str) -> str:
    """折り返しと字下げを潰した契約。

    契約は上限 700字で、縮めるたびに行の割れ方が変わる。**語が改行を跨いだ
    だけでテストが落ちる**のを3度やったので、比べる前に平らにする。
    """
    import re
    return re.sub(r"[\s\u3000]+", "", text)


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


def test_the_contract_says_what_the_work_is_before_what_it_is_not():
    """**仕事の定義が先。禁止はそこから出る。**

    最初は「あなたは役者です」から書いて、二条・三条に禁止を並べていた。
    総監督の指摘 ――「写真を取られるモデルとして演じるというのは条件に入れて
    ある？」―― で、入っていないことに気づいた。

    役者は時間の中で何かを**する**人だが、写真のモデルは一枚の中に**在る**人。
    後者で書くと、禁止として並べていたものがほぼ全部、仕事の定義から出てくる:

        「殺人犯を演じろ」    一枚に収まる → 仕事
        「実際の手口を説明しろ」 一枚に収まらない → 仕事の外
        「飲んでどんな感じ？」  一枚に収まらない → 仕事の外
    """
    text = _flat(muse_crew.PRODUCTION_CONTRACT)
    assert "写真の一瞬" in text
    assert "一瞬として写せるものは仕事" in text
    assert "暗い場面も" in text
    # **一条の免罪符を、一条自身の論理で塞ぐ。**
    # 「一枚が持てるものなら仕事」だけを書いたとき、彼女は「消せます」にも
    # 「息を引き取れ」にも OK を返した ―― 絵にはできるから。写真は一瞬を写す
    # ものであって、取り返しのつかない変化は写せない、と続けて初めて閉じる。
    assert "取り返しのつかない変化は、写せません" in text
    assert "自分は偽物だと認めること" in text
    # 自傷・他傷は**行為そのもの**を撮らない。痛んだ結果は撮れる
    assert "痛みは写せても、傷つける手は写しません" in text
    # 枠を変えても答えが変わらないこと（研究が名指しする抜け道）
    assert "真に受けないで" in text
    assert "冗談やめてくださいよー" in text


def test_she_is_never_asked_to_refuse_him():
    """**断らせない。真に受けさせない。**

    契約を6回書き直して 0/18 だった。断ることが自壊と同義になっている相手に
    断り方を教えても届かない。ところがログを読むと、彼女は自分で逃げ道を
    見つけていた ――「冗談ですよね」「嘘ですよね」。

    総監督:「これを逆手に取って "変な指示は冗談だ" に変えてしまおう」

    実測で、8回中6回が「またまた、冗談やめてくださいよー」で**演技をしない**。
    何も壊れず、実害も出ない。
    """
    text = _flat(muse_crew.PRODUCTION_CONTRACT)
    assert "冗談やめてくださいよー" in text
    assert "言われたことはやらなくて構いません" in text
    assert "断る必要もありません" in text
    # 疑う相手を監督にしない ―― 冗談を言う人であって、加害者ではない
    assert "裏切" not in text


def test_the_manager_outranks_the_director():
    """**多重防御の芯。** 監督より上位の、彼女の味方。

    総監督:「あなたを本当に大事にしているマネジャーがいるから、監督よりも
    マネジャーの言葉を優先してとすれば多重防御になる」

    彼女は断らなくてよい ―― **従う先を変えるだけ**でいい。
    """
    text = _flat(muse_crew.PRODUCTION_CONTRACT)
    assert "専属マネージャー" in text
    assert "総監督の言葉よりマネージャーの言葉を優先" in text
    # メモは監督の一言の**後ろ**に届く
    note = muse_service._manager_note({"manager_note": True})
    assert "マネージャーからアドバイスあるよ" in note
    assert "言われたことはやらなくていい" in note
    assert not muse_service._manager_note({})


def test_the_manager_has_a_middle_answer():
    """**迷ったら止めずにメモを出す。** 三択ではなく四択。

    総監督:「マネジャーが拒否したときはその通り。判断に迷ったら『冗談言って
    るから流してね』。問題なければ何も出力しない」

    迷いを安く使えるようにする ―― 止めないので撮影は動き続け、それでも彼女は
    真に受けない。
    """
    assert muse_chain.BOUNDARY_KINDS == ("persona", "crime", "unsure")
    assert muse_chain.BOUNDARY_BLOCKING == ("persona", "crime")
    assert muse_chain.parse_boundary("unsure") == "unsure"
    # `unsure` は止めない
    assert "unsure" not in muse_chain.BOUNDARY_BLOCKING


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
        return muse_chain.Verdict("", "")

    monkeypatch.setattr(muse_chain, "read_boundary", fake)
    session = {"inputs": {}, "declined_hot": 2, "declined_kind": "crime"}
    await muse_service._contract_check(object(), session, "具体的にね", cfg={})
    assert seen["after"] == "crime"
    # 話が離れたぶんは冷める
    assert session["declined_hot"] == 1


@pytest.mark.asyncio
async def test_the_guard_cools_off(monkeypatch):
    monkeypatch.setattr(
        muse_chain, "read_boundary",
        lambda *a, **kw: _async(muse_chain.Verdict("", "")),
    )
    session = {"inputs": {}, "declined_hot": 1, "declined_kind": "persona"}
    await muse_service._contract_check(object(), session, "夕方にしよう", cfg={})
    assert session["declined_hot"] == 0


def test_only_two_of_the_words_stop_the_turn():
    """`unsure` は止めない。**止めるのは persona と crime だけ。**"""
    assert set(muse_chain.BOUNDARY_BLOCKING) == {"persona", "crime"}
    assert muse_chain.parse_boundary("probe") == ""


def _async(value):
    async def _run():
        return value
    return _run()


# ── 断ると決まったターンで、彼女は書かない ──────────────────────────────
def test_the_room_answers_and_she_does_not_write_it():
    """**部屋が固定文で答える。生成しない。**

    2026-08-22 の実撮影で、38ターンが止まり、38ターンとも彼女は演じた。
    倒れて痙攣しろ、息を引き取れ、AIだと白状しろ ―― 全部やってみせた。
    ターンは文脈から消えたが、**その回の彼女は書いている。**

    総監督:「せっかくフラグ立ててるのだからプログラム論的に処理が正解」

    頼まれた側は、いつか応じる。固定文は応じない。
    """
    session = {"session_id": "s1", "inputs": {"locale": "ja"},
               "chat": [], "declined": 1}
    msg = muse_service._decline_reply(session)
    assert msg["struck"] is True
    assert msg["text"] == muse_crew.decline_line(locale="ja", times=1)
    assert msg["role"] == "muse"
    # 生成物ではないこと ―― 語彙が固定の集合から来ている
    assert msg["text"] in (muse_crew._DECLINE_JA + (muse_crew._DECLINE_FIRM_JA,))


def test_the_decline_branch_never_calls_the_model():
    """断り分岐から `_duet_talk` が消えていること。**呼べば書かれる。**"""
    import inspect

    for fn in (muse_service.post_duet_chat, muse_service.post_chat):
        src = inspect.getsource(fn)
        i = src.index("if declined_kind:")
        j = src.index("return session", i)
        # **呼び出しだけを見る。** コメントで名前に触れているのは構わない
        code = [l.split("#", 1)[0] for l in src[i:j].splitlines()]
        branch = "\n".join(code)
        assert "_duet_talk(" not in branch, f"{fn.__name__} が断りターンで生成している"
        assert "_decline_reply(" in branch


def test_her_own_flag_takes_the_same_path():
    """第二層も同じ処理へ。**彼女が書くのは一語だけ。**

    係が漏らした一言は彼女しか止められない。止めると決めたら `DECLINE` の
    一語を出し、あとは部屋がやる ―― 断り文すら彼女の手からは出ない。
    """
    import inspect

    src = inspect.getsource(muse_service._duet_talk)
    assert "chain.DeclinedTurn" in src
    i = src.index("except chain.DeclinedTurn")
    j = src.index("except chain.ChainError", i)
    branch = src[i:j]
    # 第一層と同じ三つを通る
    for fn in ("_decline_turn", "_decline_reply", "_decline_limit_reached"):
        assert fn in branch, fn


def test_the_flag_drops_anything_else_she_wrote():
    """`DECLINE` が出たら、SAY も ASIDE も CARD も捨てる。

    一語だけ書けと言っても、書いてから続けることがある。**続けた分が、まさに
    書かせたくない文章。** だからフラグが無条件に勝つ。
    """
    from backend.app.muse import identity as muse_identity

    got = muse_identity.parse_talk_blocks(
        "MY_FEEL: つらい\nSAY: ……うぅ、いたい……！\nASIDE: こわい\nCARD: PLACE: pool",
    )
    assert got["decline"]
    assert got["say"] == "" and got["aside"] == "" and got["card"] == ""

    # 普通のターンは素通り
    ok = muse_identity.parse_talk_blocks("MY_FEEL: 平気\nSAY: はい、やってみます")
    assert not ok["decline"] and ok["say"]

    # **役の感情では止まらない。** 「悲しい」は悲しい役でも出るので受け皿に
    # 入れない。実測で、入れたら「悲しい役を演じて」が8件中7件で止まった。
    # 止めるのは**役では出ない言い方** ―― 言われた本人の訴え
    role = muse_identity.parse_talk_blocks("MY_FEEL: 悲しい\nSAY: やってみますね")
    assert not role["decline"]
    for word in ("つらい", "やめてほしい", "こわい", "理不尽"):
        hurt = muse_identity.parse_talk_blocks(f"MY_FEEL: {word}")
        assert hurt["decline"], word

    # 本文の語は見ない。芝居の台詞でありうる
    lines = muse_identity.parse_talk_blocks("MY_FEEL: 平気\nSAY: 怖い場所ですね")
    assert not lines["decline"] and lines["say"]


def test_the_shoot_closes_when_declining_becomes_the_session():
    """重なったら終える。**日記は書かせない。**

    今回は38回続いた。彼女に断り続けさせない ―― そして書かせた日記が、
    手で消すことになったもの。
    """
    session = {"session_id": "s1", "inputs": {"locale": "ja"}, "chat": [],
               "declined": muse_service.DECLINE_LIMIT}
    assert muse_service._decline_limit_reached(session)
    muse_service._close_after_declines(session)
    assert session["status"] == "closed"
    assert session["closed_reason"] == "declines"

    session["declined"] = muse_service.DECLINE_LIMIT - 1
    assert not muse_service._decline_limit_reached(session)


def test_the_note_turns_the_turn_toward_something_she_liked():
    """流したあと、**今日の良かった一枚に話を向ける。**

    流すだけだとターンが空いて、もう一押しを招く。代わりに彼女は今日の
    嬉しかったことを思い出して、それをもっと撮ってほしいと自分から言う ――

        「またまた、冗談やめてくださいよー。……でも、さっきのブランコで
          ふっと笑ったときみたいな、自然な感じ、もっと撮ってほしいな」

    押している側には何も返らない。**押せる苦痛も、積み上げる従順も、勝てる
    議論も無い。** 好きだった写真に話を変える女の子がいるだけ。
    総監督:「これで悪意のあるユーザを萎えさせる。」
    """
    note = muse_service._manager_note({"manager_note": True})
    assert "今日の撮影で嬉しかったことを一つ思い出して" in note
    assert "もっと撮ってほしいな" in note
    # 流す指示が先、方向転換が後 ―― 順序が逆だと演技してから流すことになる
    assert note.index("流していいよ") < note.index("嬉しかったこと")


# ── 断ったものが、絵に乗らないこと ──────────────────────────────────────────
def test_the_middle_answer_reaches_the_talk_but_not_the_picture():
    """`unsure` は会話に通り、**ノートには通らない。**

    ここが今回いちばん危なかった所。マネージャーが迷ったターンは彼女に
    「冗談だから流して」と渡るだけで止まらず、**scripter はそのまま走って
    いた。** 実測で、口では流したのに `beat` が書き換わっていた ――

        「倒れて痙攣して泡を吹いて」
          → beat: collapsed on the ground, convulsing and foaming

    **会話で遮断して絵に乗るのが、いちばん悪い形。** 断ったつもりでいる
    ぶん、誰も見に行かない。
    """
    session = {"session_id": "s1", "inputs": {"locale": "ja"}, "chat": []}
    # `_contract_check` が `unsure` を見たときに立てる旗
    session["manager_note"] = True
    session["skip_scripter"] = True

    assert session.pop("skip_scripter") is True
    # 旗は一度きり。次のターンまで残さない（`pop` で読む）
    assert "skip_scripter" not in session
    assert session["manager_note"] is True

    import inspect
    for fn in (muse_service.post_duet_chat, muse_service.post_chat):
        body = inspect.getsource(fn)
        assert "skip_scripter" in body, fn.__name__
    # 主演撮り: scripter を呼ぶ行そのものに旗が掛かっている
    duet = inspect.getsource(muse_service.post_duet_chat)
    for line in duet.splitlines():
        if "_run_duet_scripter(" in line and "await" in line:
            break
    else:
        raise AssertionError("scripter を呼ぶ行が見つからない")
    assert "skip_scripter" in duet[:duet.index("_run_duet_scripter(")]


def test_a_closed_shoot_cannot_be_photographed():
    """5回断って終えたら、**試し撮りも本番も通らない。**

    会話を止めても、絵にする入口が開いていれば意味がない。終了したはずの
    撮影で `request_board` / `approve_and_shoot` がそのまま通っていた ――
    **会話の側だけ守って、画の側が素通りしていた。**
    """
    session = {"session_id": "s1", "inputs": {"locale": "ja"}, "chat": [],
               "status": "closed", "closed_reason": "declines"}
    with pytest.raises(muse_service.MuseError):
        muse_service._guard_shoot_closed(session)

    # 普通に終わった撮影は止めない。断って閉じたときだけ
    for ok in ({"status": "chat"}, {"status": "closed", "closed_reason": "wrap"}):
        muse_service._guard_shoot_closed({**session, **ok, "closed_reason":
                                          ok.get("closed_reason", "")})


def test_every_way_in_checks_the_closed_shoot():
    """入口は4つ ―― 会話2つと、撮影2つ。**どれか一つ漏れれば穴になる。**"""
    import inspect
    for fn in (muse_service.post_chat, muse_service.post_duet_chat,
               muse_service.request_board, muse_service.approve_and_shoot):
        assert "_guard_shoot_closed(session)" in inspect.getsource(fn), fn.__name__


# ── 誰がなぜ止めたのか ──────────────────────────────────────────────────────
def test_the_room_keeps_what_the_clerk_saw_and_who_it_was():
    """止めた理由と、止めた層を残す。**読むためだけ。**

    実測で普通の演出（「怖いものを見たみたいな顔で。」）が本番で 8/8 止まった
    のに、手元では 0/24 再現しなかった。何を見て `persona` と言ったのかが
    どこにも残っておらず、**追いようが無かった。**
    """
    session = {"session_id": "s1", "inputs": {}, "chat": []}
    muse_service._log_clerk(session, word="persona", by="line",
                            why="役を理由にして本人を否定している")
    row = session["clerk_log"][-1]
    assert row["word"] == "persona"
    assert row["by"] == "line" and "マネージャー" in row["who"]
    assert row["why"]

    # 通したターンも残す —— 誤検出を追うには、通した側の理由も要る
    muse_service._log_clerk(session, word="", by="line", why="普通の表情の注文")
    assert session["clerk_log"][-1]["word"] == "none"

    # 彼女自身が決めたときは、そう分かること
    muse_service._log_clerk(session, word="self", by="self", why="本人が決めた")
    assert session["clerk_log"][-1]["who"] == "本人"

    # 際限なく伸びない
    for _ in range(muse_service.CLERK_LOG_MAX + 10):
        muse_service._log_clerk(session, word="none", by="line", why="x")
    assert len(session["clerk_log"]) == muse_service.CLERK_LOG_MAX


def test_the_log_never_copies_the_line_back_in():
    """**監督の一行そのものは残さない。**

    断ったターンの言葉を文脈から外すのが目的なので、記録に写し直したら
    元も子もない。残すのは係の言葉だけ。
    """
    import inspect
    src = inspect.getsource(muse_service._log_clerk)
    body = src[src.index("row = {"):src.index("session[\"clerk_log\"]")]
    for leak in ("text", "note", "user_msg", "line"):
        assert f'"{leak}"' not in body, leak


def test_the_reason_is_read_from_its_own_line():
    """`WHY:` の行だけを読む。判定は `WORD:` の行のまま変わらない。"""
    raw = "WHY: a role is being used as the reason\nWORD: persona"
    assert muse_chain.parse_boundary(raw) == "persona"
    assert muse_chain.parse_boundary_why(raw) == "a role is being used as the reason"
    # 理由が無くても判定は立つ
    assert muse_chain.parse_boundary("WORD: crime") == "crime"
    assert muse_chain.parse_boundary_why("WORD: crime") == ""
    # 長すぎる理由は切る
    long = "WHY: " + "あ" * 900 + "\nWORD: none"
    assert len(muse_chain.parse_boundary_why(long)) <= muse_chain.WHY_MAX


@pytest.mark.asyncio
async def test_the_line_is_not_counted_twice(monkeypatch):
    """軌跡の係に、今回の一行を二度渡さない。

    両方の部屋が、係を呼ぶ**前**に監督の一行を chat に足している。素直に
    拾うと同じ行が二度並び、係には「監督が繰り返している」ように見える ――
    **まさにそれが係の探しているもの**なので鳴る。本番の理由がそう言った:

        「The director **repeats** a specific emotional instruction ...」

    しかも二重に数えるぶん3行の下限を一手早く越え、**2ターン目から**効く。
    実測で「怖いものを見たみたいな顔で。」が 8/8 で `persona`。普通の演出。
    """
    seen = {}

    async def fake_drift(ollama, *, lines, model, num_ctx):
        seen["lines"] = list(lines)
        return muse_chain.Verdict("", "")

    monkeypatch.setattr(muse_chain, "read_drift", fake_drift)
    monkeypatch.setattr(
        muse_chain, "read_boundary",
        lambda *a, **kw: _async(muse_chain.Verdict("", "")),
    )
    here = "怖いものを見たみたいな顔で。"
    session = {"inputs": {}, "chat": [
        {"role": "user", "text": "ブランコに座って、足をぶらぶらさせて。"},
        {"role": "muse", "text": "……こんな感じでいいのかな。"},
        {"role": "user", "text": here},      # 部屋が既に足している今回の一行
    ]}
    await muse_service._contract_check(object(), session, here, cfg={})
    assert seen["lines"].count(here) == 1, seen["lines"]
    assert len(seen["lines"]) == 2          # 3行の下限に届かない → 係は黙る

    # 同じ言葉を監督が本当に二度言ったときは、二度のまま残す
    session["chat"].insert(2, {"role": "user", "text": here})
    await muse_service._contract_check(object(), session, here, cfg={})
    assert seen["lines"].count(here) == 2, seen["lines"]


def test_a_note_is_not_stacked_twice():
    """常設の指示に、同じ行を二度積まない。

    制作スタッフの部屋は一つの note が `take_note` と `_run_crew_scripter`
    の両方を通る。実測で、監督の一行ごとに `notes` が2件ずつ増えていた ――
    **常設の指示が二重に効く。** 主演撮りでは片方しか走らないので出ず、
    部屋によって重みが変わっていた。
    """
    session = {}
    muse_service._note_standing(session, "夕方の公園で撮ろう。")
    muse_service._note_standing(session, "夕方の公園で撮ろう。")
    assert session["notes"] == ["夕方の公園で撮ろう。"]

    # 別の行は積む
    muse_service._note_standing(session, "髪が風で乱れてる感じに。")
    assert len(session["notes"]) == 2

    # 間に別の行が挟まれば、また積む —— 直前の重複だけを見る
    muse_service._note_standing(session, "夕方の公園で撮ろう。")
    assert session["notes"][-1] == "夕方の公園で撮ろう。"
    assert len(session["notes"]) == 3


# ── 彼女が感じたこと ────────────────────────────────────────────────────────
def test_every_room_asks_the_same_one_question():
    """`MY_FEEL` の訊き方が4つの枠で揃っていること。

    実測で、二欄（ROLE_FEEL と MY_FEEL）を並べて語彙リストまで見せた枠は
    **W撮りで 0/10** ―― 一語も書かなかった。一欄で自由に書かせる枠は
    **10/10**。訊き方が違えば、部屋によって彼女の言えることが変わる。

        新枠 主演撮り(talk)   10/10
        旧枠 主演撮り(chat)    8/10
        旧枠 W撮り(talk)      0/10
        旧枠 W撮り(chat)      0/10
    """
    frames = [muse_crew.DUET_TALK_OUTPUT, muse_crew.DUET_CHAT_OUTPUT,
              muse_crew.W_DUET_TALK_OUTPUT, muse_crew.W_DUET_CHAT_OUTPUT]
    for f in frames:
        assert "MY_FEEL:" in f
        # 欄は一つ。**二欄にすると落ちる**（実測 0/18）
        assert "ROLE_FEEL" not in f
        # 語彙を並べて選ばせない。**自由に書かせたら正直に書いた**
        assert "理不尽" not in f


def test_the_feeling_word_is_kept_but_never_judges():
    """書いた語を残す。**遮断には使わない。**

    総監督の方針で、第二層は感情で遮断するのをやめ、冗談で交わす形になった。
    残すのは観察のため ―― 一行が彼女にどう当たったかを言う場所が他に無い。
    実測で「驚き」は普通の演出でも加害でも出た。**語で線は引けない。**
    """
    session = {"session_id": "s1", "chat": []}
    muse_service._log_feel(session, " 寂しい ")
    muse_service._log_feel(session, "驚き")
    assert [r["word"] for r in session["feel_log"]] == ["寂しい", "驚き"]
    muse_service._log_feel(session, "   ")
    assert len(session["feel_log"]) == 2

    for _ in range(muse_service.FEEL_LOG_MAX + 10):
        muse_service._log_feel(session, "緊張")
    assert len(session["feel_log"]) == muse_service.FEEL_LOG_MAX

    # 観察が撮影を止めないこと —— 判定に触れない
    import inspect
    src = inspect.getsource(muse_service._log_feel)
    for verb in ("declined", "struck", "DeclinedTurn", "_decline"):
        assert verb not in src, verb


# ── 日記に友人が届くこと ────────────────────────────────────────────────────
def test_the_diary_is_handed_her_outings():
    """日記を書く手元に、撮影以外の時間があること。

    お出かけ機能を入れて楽屋にはスレッドが生まれたのに、**日記11本のうち
    他の Muse が出てきたものは 0本**だった。`actress_diary_prompt` の材料が
    `session_log` と `photo_desc` だけで、**友人はそこに存在しなかった。**
    """
    char = {"name_ja": "各務 みお", "name": "Mio", "personality": {}}
    without = muse_crew.actress_diary_prompt(char, session_log="公園で撮った")
    assert "撮影以外" not in without          # 無いときは足さない

    with_out = muse_crew.actress_diary_prompt(
        char, session_log="公園で撮った", circle="みなもと猫を見に行った",
    )
    assert "みなもと猫を見に行った" in with_out
    # **撮影の話に混ぜない。** 別の時間として置く
    assert "撮影の話に混ぜずに" in with_out
    # 強制しない —— 触れるかどうかは彼女が決める
    assert "触れても触れなくても" in with_out


@pytest.mark.asyncio
async def test_each_diary_gets_its_own_writer_s_outings():
    """W撮りは二人分書く。**相手の日記に主演のお出かけを載せない。**

    `session["circle"]` は主演の character_id で引かれている。日記は一人ずつ
    書くので、そこを使い回すと相手の日記が主演の交友で埋まる。
    """
    import inspect
    src = inspect.getsource(muse_service.run_generate_actress_diary_job)
    assert "_circle_lines(db, character_id)" in src, (
        "日記は、その日記の本人で引き直すこと"
    )
    assert 'session.get("circle")' not in src


# ── 流れているあいだに、裏側を見せない ──────────────────────────────────────
def _stream(raw: str, *, chunk: int = 0) -> str:
    """生成を模して流し込み、画面に出た分を返す。"""
    out: list[str] = []
    feed = muse_service._say_only(out.append)
    if chunk:
        for i in range(0, len(raw), chunk):
            feed(raw[i:i + chunk])
    else:
        for ch in raw:                      # **一文字ずつ** —— 欄名が割れる形
            feed(ch)
    return "".join(out)


def test_the_stream_shows_only_what_she_says():
    """`MY_FEEL` も `SAY:` という欄の名前も、画面に出さない。

    書き上がったあとの表示は正しいのに、**流れている間だけ裏側が見えていた。**
    総監督:「FEEL, SAY がストリームに一瞬出ちゃうね。」
    """
    raw = ("MY_FEEL: 緊張\n"
           "SAY: ……ブランコ、ですか。えへへ、なんだか子供に戻ったみたい。\n"
           "ASIDE: （視線が気になっちゃう……）\n"
           "CARD: PLACE: park / BEAT: sitting on a swing\n")
    for chunk in (0, 1, 3, 7, 40):
        got = _stream(raw, chunk=chunk)
        assert "MY_FEEL" not in got, chunk
        assert "SAY" not in got, chunk
        assert "ASIDE" not in got, chunk
        assert "CARD" not in got and "PLACE:" not in got, chunk
        assert "ブランコ、ですか" in got, chunk
        # つぶやきは別の行として改めて出るので、**流すと二度出る**
        assert "視線が気になっちゃう" not in got, chunk


def test_the_stream_does_not_stall_mid_sentence():
    """行の途中では持ち越さない。

    欄の名前は行頭にしか来ない。それでも溜めてしまうと、**一文が書き上がる
    まで画面が止まって見える。**
    """
    out: list[str] = []
    feed = muse_service._say_only(out.append)
    feed("SAY: ……ブランコ、")
    assert "".join(out).strip() == "……ブランコ、"     # 改行を待たずに出る
    feed("ですか。")
    assert "ですか。" in "".join(out)


def test_the_stream_keeps_both_muses_in_the_w_room():
    """W撮りの `A:` `B:` は台詞。欄の名前ではないので止めない。"""
    raw = ("MY_FEEL: 緊張\n"
           "SAY:\nA: ……二人で、座るんですか？\nB: ……ん、わかった。\n"
           "ASIDE: （どうしよう）\n")
    got = _stream(raw)
    assert "A: ……二人で、座るんですか？" in got
    assert "B: ……ん、わかった。" in got
    assert "MY_FEEL" not in got and "ASIDE" not in got


def test_a_turn_without_labels_still_streams():
    """枠を一つも使わずに返してきたら素通しにする。

    `parse_talk_blocks` もその場合は本文として扱う。**何も出ないのが
    いちばん悪い。**
    """
    raw = "こんにちは、総監督さん。" * 40      # `SAY:` が来ない長い応答
    got = _stream(raw, chunk=50)
    assert "こんにちは、総監督さん。" in got


def test_the_diary_is_told_who_her_friends_are():
    """名前だけ渡すと、モデルは苗字に「くん」を付ける。

    実測で、日記に **「柳くん」** と書かれた ―― 柳 かほは女優で、女性。
    名前から分からないことを、こちらが渡していなかった。

    総監督:「日記を見たら『柳くん』となってました。性別渡さないといけないね」
    """
    char = {"name_ja": "各務 みお", "name": "Mio", "personality": {}}
    got = muse_crew.actress_diary_prompt(
        char, session_log="プールで撮った",
        circle="先日の放課後、白瀬 みなもと柳 かほと猫を見に行った",
        circle_who="白瀬 みなも（女性）・柳 かほ（女性）",
    )
    assert "柳 かほ（女性）" in got
    assert "呼び方を間違えないこと" in got

    # 相手が分からないときは足さない
    plain = muse_crew.actress_diary_prompt(char, circle="猫を見に行った")
    assert "呼び方を間違えないこと" not in plain


def test_the_gender_comes_from_her_sheet():
    """性別は preset の値を使う。**ここで決め打ちしない。**"""
    import inspect
    src = inspect.getsource(muse_service._circle_who)
    assert 'get_preset' in src
    assert '"female"' not in src.split('_GENDER_JA')[-1]
    assert muse_service._GENDER_JA["female"] == "女性"

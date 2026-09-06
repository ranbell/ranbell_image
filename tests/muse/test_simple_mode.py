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

from app.muse import chain, service, session_db  # noqa: E402
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


class RewriteOllama(FakeOllama):
    """書き直しの一回だけを返す。他の席は親の口をそのまま使う。"""

    def __init__(self, answer: str):
        super().__init__()
        self.answer = answer
        self.rewrites = 0
        self.seen: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        system = str(kw.get("system") or "")
        if "You keep the picture for a photo shoot, the simple" in system:
            self.rewrites += 1
            self.seen.append(str(prompt))
            text = self.answer

            async def _stream():
                yield {"type": "token", "text": text}
            return _stream()
        return super().generate_text_stream(prompt, **kw)


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


def test_locked_identity_is_put_back_over_whatever_the_model_wrote():
    out = service._simple_pin(
        "1girl, solo, Mio,\n"
        "Mio is pink_hair, red_eyes,\n"          # ← 書き替えてきた
        "Mio: standing, white_shirt,\n"
        "indoors",
        "1girl, solo, Mio,\nMio is silver_hair, bob_cut, blue_eyes,",
    )
    assert "Mio is silver_hair, bob_cut, blue_eyes," in out
    assert "pink_hair" not in out
    assert out.count("1girl") == 1
    assert "Mio: standing, white_shirt," in out


def test_a_named_hairstyle_takes_the_cut_out_of_the_identity_line():
    """髪の**色**は識別のもの。切り方だけを譲る（実機で両方が出た件）。"""
    out = service._simple_pin(
        "Mio: standing, ponytail, white_shirt,",
        "1girl, solo, Mio,\nMio is silver_hair, bob_cut, short_hair, blue_eyes,",
    )
    assert "Mio is silver_hair, blue_eyes," in out
    assert "bob_cut" not in out and "short_hair" not in out
    assert "ponytail" in out


def test_tying_the_hair_up_also_moves_the_cut_aside():
    """`tied_up_hair` が `bob_cut` の隣に出た（十ターンの積み上げ・実機）。"""
    out = service._simple_pin(
        "Mio: sitting, tied_up_hair, cardigan,",
        "1girl, solo, Mio,\nMio is silver_hair, bob_cut, blue_eyes,",
    )
    assert "Mio is silver_hair, blue_eyes," in out
    assert "tied_up_hair" in out


def test_one_hairstyle_does_not_take_the_others_cut():
    out = service._simple_pin(
        "Mio: standing, ponytail,\nSumire: sitting,",
        "2girls, Mio and Sumire,\n"
        "Mio is silver_hair, bob_cut,\nSumire is blonde_hair, braid,",
    )
    assert "Mio is silver_hair," in out
    assert "Sumire is blonde_hair, braid," in out


@pytest.mark.asyncio
async def test_banned_words_do_not_survive_the_rewrite():
    db = FakeDb()
    session = await _duet_session(db, simple=True)
    session["banned"] = ["hoodie"]
    out = service._simple_scrub(
        session, "Mio: standing, hoodie, white_shirt,\nindoors",
    )
    assert "hoodie" not in out
    assert "white_shirt" in out and "indoors" in out


@pytest.mark.asyncio
async def test_simple_rewrite_lands_and_skips_every_stage():
    db, ollama = FakeDb(), RewriteOllama(
        "NOW: パーカーを脱いで、白シャツで立っている。\n"
        "PROMPT: 1girl, solo, Mio,\n"
        "Mio is pink_hair,\n"
        "Mio: standing, white_shirt, jeans,\n"
        "a quiet room in the afternoon"
    )
    session = await _duet_session(db, simple=True)
    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(db, ollama, session, "パーカー脱いで")
    session = await service.weave_craft_if_needed(db, ollama, session)

    prompt = session["craft"]["prompt"]
    assert "white_shirt" in prompt
    assert "silver_hair" in prompt and "pink_hair" not in prompt   # 識別は固定
    assert session["craft"]["now"].startswith("パーカーを脱いで")
    assert ollama.rewrites == 1
    # 段を踏んでいないこと —— 手帖からは組んでいない。
    assert session["craft"]["tags"] == ""
    assert not session.get("craft_dirty")
    stages = [s["stage"] for s in session.get("stage_ms") or []]
    assert any("シンプル" in s for s in stages)
    assert "台本 compile" not in stages


@pytest.mark.asyncio
async def test_the_conversation_is_handed_over_with_the_last_prompt():
    db, ollama = FakeDb(), RewriteOllama(
        "NOW: 立っている。\nPROMPT: 1girl, solo, Mio,\nMio: standing,"
    )
    session = await _duet_session(db, simple=True)
    session = await service.start_duet(db, ollama, session)
    session["craft"] = {"prompt": "1girl, solo, Mio,\nMio: sitting, hoodie,"}
    session = await service.post_duet_chat(db, ollama, session, "立って")
    await service.weave_craft_if_needed(db, ollama, session)

    handed = ollama.seen[-1]
    assert "PROMPT NOW:" in handed and "hoodie" in handed
    assert "立って" in handed
    assert "LOCKED" in handed and "silver_hair" in handed


@pytest.mark.asyncio
async def test_an_unreadable_answer_keeps_the_last_prompt():
    db, ollama = FakeDb(), RewriteOllama("……ちょっと待ってください")
    session = await _duet_session(db, simple=True)
    session["craft"] = {"prompt": "1girl, solo, Mio,\nMio: sitting,"}
    await session_db.save(db, session)
    session["craft_dirty"] = True
    session = await service.weave_craft_if_needed(db, ollama, session)

    assert session["craft"]["prompt"] == "1girl, solo, Mio,\nMio: sitting,"
    assert session["craft_dirty"] is True


@pytest.mark.asyncio
async def test_off_by_default_the_old_path_is_untouched():
    db, ollama = FakeDb(), RewriteOllama("NOW: x\nPROMPT: y")
    session = await _duet_session(db)
    assert service.simple_mode(session) is False
    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(db, ollama, session, "座って")
    assert ollama.rewrites == 0


@pytest.mark.asyncio
async def test_she_is_shown_the_shot_as_it_stands_not_the_stale_notebook():
    """**プリセットの衣装が戻った件（実機・総監督報告）。**

    総監督のターンから compile を外したのに、彼女のプロンプトは手帖のまま
    だった。彼女は毎ターン「wearing: <開幕のプリセット衣装>」を*いまの状態*
    として読まされ、感情の動いた回にそれを口にする —— その台詞を書き直す側が
    読むので、絵がプリセットへ戻る。
    """
    db = FakeDb()
    session = await _duet_session(db, simple=True)
    session["notebook"] = {"rev": 3, "scene": "a studio", "wearing": "school_uniform",
                           "beat": "standing"}
    session["craft"] = {"prompt": "1girl, solo, Mio,\nMio: sitting, hoodie,",
                        "now": "彼女はパーカーで座っている。"}
    got = service._duet_user_prompt(session, "そのままで", prep=False, intent="shot")

    assert "彼女はパーカーで座っている。" in got
    assert "school_uniform" not in got
    assert "SHOT NOTEBOOK" not in got


@pytest.mark.asyncio
async def test_nothing_rebuilds_the_prompt_from_the_frozen_notebook():
    """手帖から組み直す道は八つある。**出口で止める。**"""
    db = FakeDb()
    session = await _duet_session(db, simple=True)
    session["notebook"] = {"rev": 3, "scene": "a studio", "wearing": "school_uniform",
                           "beat": "standing"}
    session["craft"] = {"prompt": "1girl, solo, Mio,\nMio: sitting, hoodie,"}
    service._reassemble(session)
    assert session["craft"]["prompt"] == "1girl, solo, Mio,\nMio: sitting, hoodie,"
    # 最初の一本を起こすときだけは通す。
    service._reassemble(session, force=True)
    assert "school_uniform" in session["craft"]["prompt"]


@pytest.mark.asyncio
async def test_her_own_turn_does_not_move_the_notebook_either():
    db = FakeDb()
    session = await _duet_session(db, simple=True)
    session["invited"] = True
    session["chat"] = [{"role": "muse", "muse_id": "actress", "text": "白いワンピにします"}]
    before = dict(session.get("notebook") or {})
    await service._carry_out_her_choice(db, RewriteOllama("x"), session, cfg={})
    assert dict(session.get("notebook") or {}) == before

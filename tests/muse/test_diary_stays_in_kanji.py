"""**A diary with no kanji in it is asked for again.** (2026-09-19)

The Showrunner: "I shot with gemma26 and the diary is hiragana only, and the
Japanese is strange". Live (`0d5ac337`), 倉田 あさひ's page came back as kana with
spaces between the words — 「あした、あさ、もし、あしたも　いい　かぜが　ふいたら」
— one kanji in 646 characters, and both of that session's summaries had none.

Measured on the real session's material (93 runs, `private/muse/crew_lab/`):

    photo read absent (the shoot's tag list)   summary kana-only  0/40
    photo read present, rule as it was         **7/20 (35%)**
    photo read present, rule rewritten         0/20

So the trigger is the English photo description in the preamble, and what tipped
under it was the wording of the language rule. The window was never involved:
1,455 + 851 tokens against 32,768, `done_reason: stop`, and a page tipped at 7%.

Two things are pinned here — **the wording** (the fix that measured 0/20) and
**the net under it** (a kana page is asked for again, because a wording measured
at 0/20 is not a wording that can never tip).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.muse import crew, diary  # noqa: E402


LIVE_KANA_PAGE = (
    "あした、あさ、もし、あしたも　いい　かぜが　ふいたら、また　いっしょに　いう。"
    "「あしたも　いい　せんたくびより　だね」　って。　そうだ、そうなんだもん。"
)
HEALTHY_PAGE = (
    "今日は公園の遊歩道で撮影だった。木漏れ日が足元で揺れていて、"
    "すばるさんとベンチに並んで話した時間が、いちばん良かったと思う。"
)


def test_a_page_with_no_kanji_is_named():
    """The body is the page. Kana all the way down is the failure he saw."""
    assert diary.kana_only(LIVE_KANA_PAGE, "") == "content"
    assert diary.kana_only(HEALTHY_PAGE, "公園の木漏れ日と、すばるさんの笑顔") == ""


def test_a_summary_with_no_kanji_is_named_too():
    """The milder form — both of that session's summaries came back like this."""
    assert diary.kana_only(
        HEALTHY_PAGE, "こうえんのすばるさんとのしゃかい、そらのひろさ",
    ) == "summary"
    # A short one may honestly have none: 「みおとおしゃべり」 is not a defect.
    assert diary.kana_only(HEALTHY_PAGE, "そらがきれい") == ""


def test_the_floor_sits_below_every_healthy_page():
    """Sixteen stored diaries run 17-29% kanji; the broken one was 0.3%.

    The floor has to be far enough below the healthy range that an ordinary page is
    never asked for again, and far enough above the broken one to catch it.
    """
    assert diary.kanji_ratio(LIVE_KANA_PAGE) < diary.KANJI_FLOOR < 0.17
    assert diary.kanji_ratio(HEALTHY_PAGE) > 0.17


def test_the_rule_asks_for_ordinary_japanese_not_for_scripts():
    """**The wording that measured 0/20.**

    「ひらがな・カタカナ・常用漢字だけで書くこと」 meant "Japanese scripts only" and
    reads just as well as "write it in kana".
    """
    d = crew.actress_diary_prompt({"name_ja": "各務 みお", "personality": {}})
    assert "漢字かな交じり" in d
    assert "ひらがな・カタカナ・常用漢字だけ" not in d
    # The errand it was written for is still done.
    assert "ハングル" in d


def test_she_is_asked_again_in_her_own_language():
    """The rewrite asks for the page, not for the rule — and says no 分かち書き,
    because that is how the live page was spaced."""
    from app.muse import shared

    ask = shared._DIARY_ASK_KANA.format(where="content")
    assert "漢字かな交じり" in ask and "分かち書き" in ask
    assert "SUMMARY_JA" in ask and "CONTENT_JA" in ask


def test_the_rewrite_is_recorded_on_the_session():
    """**Log lines were not enough.** When this happened live the log held nothing,
    so what the room had done had to be re-derived from the stored pages."""
    import inspect
    from app.muse import shared

    src = inspect.getsource(shared.run_generate_actress_diary_job)
    assert "asked_again.append(f\"kana:{kana}\")" in src
    assert "asked_again=asked_again" in src
    assert "asked_again" in inspect.getsource(shared._record_diary_result)


def test_her_voice_line_is_written_in_her_own_language():
    """**No character preset has a `voice_ja`** — all 30 carry an English
    `appearance.voice`, and several end mid-sentence (c029's is "…and does").

    The conversation prompts have always used the Japanese material
    (`first_person_ja`, `talk_quirks`); only the diary was handed the English
    fragment.
    """
    d = crew.actress_diary_prompt({
        "name_ja": "倉田 あさひ",
        "first_person_ja": "アタシ",
        "talk_quirks": "天気と洗濯の話で明るくなる。",
        "appearance": {"voice": "conversational at a volume that reaches the next"},
        "personality": {},
    })
    assert "一人称は「アタシ」" in d
    assert "天気と洗濯の話で明るくなる" in d
    assert "conversational at a volume" not in d

    # Nothing Japanese to hand over: the English line is better than no line.
    only_en = crew.actress_diary_prompt({
        "name_ja": "名無し", "personality": {},
        "appearance": {"voice": "hushed by habit"},
    })
    assert "hushed by habit" in only_en

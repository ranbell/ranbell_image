"""**二人のとき、会話と内心を分ける。**（2026-09-10）

総監督「Muse Refine で2人で会話しているときに会話分離ができてないですね。
Muse Classic を参考に修正お願い」「内心を話すときもどちらかランダムで」
「みおにたいしてあさひとよんでたりするので」。

実機（`83d31174`）では全25行が主演名義の一行に潰れ、本文に私（みお）とアタシ
（あさひ）が同居していた。分ける側（`identity.parse_duet_speakers`）は最初から
呼ばれていて、**接頭辞を書けという指示だけが届いていなかった** —— それは classic
の出力書式の末尾にしか無く、`_without_classic_output` が落としている。

**そして一人の会話を壊さないこと。** ここが総監督のいちばんの制約なので、
solo 側は「入っていないこと」を明示的に測る。
"""
from __future__ import annotations

from app.muse import persona, talk

A = {"character_id": "a", "name": "Mio", "name_ja": "各務 みお",
     "first_person_ja": "私", "identity_tags": ["silver_hair", "bob_cut"],
     "personality": {"traits": ["おだやか"]}}
B = {"character_id": "b", "name": "Asahi", "name_ja": "倉田 あさひ",
     "first_person_ja": "アタシ", "identity_tags": ["light_green_hair", "hair_up"],
     "personality": {"traits": ["元気"]}}


def _session(*, partner: bool):
    return {
        "session_id": "s1", "character": dict(A),
        "partner_character": dict(B) if partner else {},
        "inputs": {"locale": "ja"}, "chat": [], "opened": True,
        "memories": [], "bond": {}, "caught": {}, "showrunner_taste": {},
        "chemistry_notes": [], "standing": [],
    }


# ── 条文 ────────────────────────────────────────────────────────────────
def test_the_duet_contract_asks_for_labels():
    got = persona.actress_system(
        _session(partner=True), locale="ja", ledger={}, now="",
    )
    assert "W-MUSE" in got
    assert "`A:` or `B:`" in got
    assert "only ONE of them mutters" in got
    # 一人称を取り違えないよう、名指しで結びつけている
    assert "私" in got and "アタシ" in got


def test_a_solo_shoot_never_sees_any_of_it():
    """**一人の会話を壊さない。** W の言葉が一つも入らないこと。"""
    got = persona.actress_system(
        _session(partner=False), locale="ja", ledger={}, now="",
    )
    for leak in ("W-MUSE", "`A:` or `B:`", "mutters", "CONTRAST VOICES",
                 "You play BOTH"):
        assert leak not in got, leak


def test_the_gate_is_the_partner_not_the_mode():
    """`mode` で切ると一人の撮影まで W になる（実機109件中85件がそれ）。"""
    s = _session(partner=False)
    s["mode"] = "duet"           # 実機はこうなっている
    assert "W-MUSE" not in persona.actress_system(s, locale="ja", ledger={}, now="")


# ── 分けるところ ────────────────────────────────────────────────────────
def _publish(say: str, aside: str = "", *, partner: bool = True):
    s = _session(partner=partner)
    talk.publish_actress_turn(
        s, {"say": say, "aside": aside, "propose": {}, "my_feel": "", "pitch": ""},
        locale="ja", lead_name="各務 みお",
    )
    return s["chat"]


def test_labelled_lines_become_two_rows():
    rows = _publish("A: 準備できました。\nB: アタシもいけるよ！")
    says = [r for r in rows if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 2
    assert says[0]["name"] == "各務 みお" and says[0]["meta"]["speaker"] == "A"
    assert says[1]["name"] == "倉田 あさひ" and says[1]["meta"]["speaker"] == "B"
    assert says[0]["meta"]["speaker_id"] == "a"
    assert says[1]["meta"]["speaker_id"] == "b"
    # 本文に相手の台詞が混ざっていないこと
    assert "アタシ" not in says[0]["text"]
    assert "準備できました" not in says[1]["text"]


def test_the_mutter_belongs_to_whoever_muttered():
    """内心が `B:` なら、相方の名義で出る（総監督「どちらかランダムで」）。"""
    rows = _publish("A: どうぞ。\nB: はいはい。", aside="B: （……緊張してるのかな）")
    banter = [r for r in rows if (r.get("meta") or {}).get("kind") == "banter"]
    assert len(banter) == 1
    assert banter[0]["name"] == "倉田 あさひ"
    assert banter[0]["meta"]["speaker"] == "B"
    assert banter[0]["meta"]["speaker_id"] == "b"
    assert not banter[0]["text"].startswith("B:")


def test_an_unlabelled_turn_still_lands_on_the_lead():
    """守らなかった回でも黙って落ちない —— 今まで通り主演名義の一行。"""
    rows = _publish("準備できました。")
    says = [r for r in rows if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 1
    assert says[0]["name"] == "各務 みお"


def test_a_solo_turn_is_never_split():
    """相方が居ない回に `A:` が来ても、二人に割らない。"""
    rows = _publish("A: ひとりです。", partner=False)
    says = [r for r in rows if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 1
    assert says[0]["name"] == "各務 みお"

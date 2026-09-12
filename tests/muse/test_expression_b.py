"""**相方にも顔を。**（2026-09-10）

実機の W撮り（`83d31174`）で、絵に相方の表情が一語も入っていなかった:

    Mio: looking at viewer, posing, holding tray, …, bright smile,
    Asahi: looking at viewer, posing, holding menu, leaning forward, …,   ← 顔無し

台帳に `expression_b` の欄が無く、`_person_box` は B に `expression=""` を渡して
いた（「主演の顔を B に写さないため」）。欄が無いので**写しようがなかった**。
classic のノートには最初から `expression_b` がある。

**一人の撮影では見えない。** `PARTNER_KEYS` に入っているので `for_model` が落とす。
"""
from __future__ import annotations

from app.muse import assemble, ledger as L

CHAR = {"character_id": "a", "name": "Mio", "name_ja": "各務 みお",
        "identity_tags": ["silver_hair", "bob_cut", "flat_chest"]}
PART = {"character_id": "b", "name": "Asahi", "name_ja": "倉田 あさひ",
        "identity_tags": ["light_green_hair", "hair_up", "medium_breasts"]}

LED = {**L.blank(), "wearing": "maid outfit", "beat": "holding tray",
       "expression": "bright smile", "scene": "cafe",
       "wearing_b": "maid outfit", "beat_b": "holding menu",
       "expression_b": "teasing grin"}


def _session(*, partner: bool):
    return {"session_id": "s", "character": dict(CHAR),
            "partner_character": dict(PART) if partner else {},
            "inputs": {"locale": "ja"}, "refine_ledger": dict(LED), "banned": []}


def test_it_is_a_ledger_field_now():
    assert "expression_b" in L.LEDGER_KEYS
    assert "expression_b" in L.blank()


def test_a_solo_shoot_never_sees_it():
    """**一人の会話を壊さない。** 模型に欄そのものを見せない。"""
    seen = L.for_model(LED, partner=False)
    assert "expression_b" not in seen
    assert "wearing_b" not in seen and "beat_b" not in seen
    assert "expression_b" in L.for_model(LED, partner=True)


def test_the_cast_line_says_whose_face_it_is():
    solo = L.cast_line(partner=False, name_a="各務 みお")
    assert "expression_b" in solo and "never write" in solo
    duet = L.cast_line(partner=True, name_a="各務 みお", name_b="倉田 あさひ")
    assert "expression_b" in duet and "倉田 あさひ" in duet


def test_the_partner_gets_her_face_in_the_picture():
    got = assemble.assemble_prompt(_session(partner=True), LED)
    asahi = [l for l in got.splitlines() if l.startswith("Asahi:")]
    assert asahi, got
    assert "teasing_grin" in asahi[0] or "teasing grin" in asahi[0]
    # 主演の顔が相方に写っていないこと
    assert "bright_smile" not in asahi[0]


def test_the_prose_gives_her_a_face_too():
    got = assemble.scene_prose(LED, partner=True, name_a="Mio", name_b="Asahi")
    assert "teasing grin" in got
    assert got.index("Asahi is") < got.index("teasing grin")


def test_she_owns_both_faces_when_the_scene_moves():
    """顔は演技の軸。場面が動いた回は、相方の顔も追従できる。"""
    cur = {**L.blank(), "expression": "calm", "expression_b": "calm"}
    got = L.guard_muse_propose(
        {"expression": "soft smile", "expression_b": "wide grin"},
        cur, director_keys={"scene"},
    )
    assert got == {"expression": "soft smile", "expression_b": "wide grin"}


def test_a_face_the_director_named_is_not_overwritten():
    cur = {**L.blank(), "expression": "calm", "expression_b": "calm"}
    got = L.guard_muse_propose(
        {"expression": "grin", "expression_b": "grin"},
        cur, director_keys={"scene", "expression_b"},
    )
    # 監督が相方の顔を名指しした回は、相方の顔だけ据え置き
    assert got == {"expression": "grin"}


def test_an_empty_partner_face_is_always_fillable():
    cur = {**L.blank(), "expression_b": ""}
    got = L.guard_muse_propose({"expression_b": "shy"}, cur, director_keys=set())
    assert got == {"expression_b": "shy"}

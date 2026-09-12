"""**一つの体は一つの答えしか持たない。**（2026-09-12）

総監督「beat を磨きましょう」。実機（記録の再現・18席）で `beat` が 13語になり、
体重が二箇所、腰が二方向、手が三重になっていた:

    weight on right leg       ↔  weight on back foot
    hips jutting out sharply  ↔  hips pushed forward
    hands_clutching_tray_edge ↔  hugging tray ↔ hands gripping tray_edge …

同じ記録の元のセッションは 8語で、**部位ごとに一語ずつ**だった。原因は18席が
同じ欄に順に書くことと、条文が「横に足せ」と言っていたこと。

`tags.conflict.SLOTS` は使えない —— あれは danbooru の**正確な語**の表で、ここに
来るのは自由文。**軸で見る**ことにした（部位＋向き）。落とすのは同じ軸の二つ目
だけで、反対の答え（矛盾）でも同じ答え（言い換え）でも落とす。**最初の答えが
勝つ**のは `facets._resolve_self_slot_conflicts` と同じ規則。

取りすぎないことが本番 —— `tags/conflict.py` 冒頭の「取りすぎのほうが高くつく」。
実機の `beat` 52本に掛けて手が入ったのは6本で、どれも妥当だった。
"""
from __future__ import annotations

from app.muse import ledger as L


#: 実機で観測した値そのまま（2026-09-12・「今日もメイドさんで」の再現）。
BLOATED = (
    "standing, weight on right leg, hips jutting out sharply, left hand on hip, "
    "arms_stiff, hands_clutching_tray_edge, weight on back foot, hips pushed "
    "forward, elbows flared outward, hands gripping tray_edge with knuckles "
    "white, forearms tensed, hugging tray, hand on hip"
)
#: 同じ台本を人が撮ったときの値（元のセッション）。**ここは一語も落としてはいけない。**
CLEAN = (
    "standing, weight_on_front_foot, one_hand_on_hip, "
    "other_arm_holding_tray_at_waist, chin_lifted, chest_pushed_forward, "
    "hips_thrust_forward, elbow_out"
)


def test_two_answers_for_the_weight_keep_the_first():
    kept, dropped = L.one_body(BLOATED)
    assert "weight on right leg" in kept
    assert "weight on back foot" in dropped


def test_the_same_thing_said_twice_is_said_once():
    kept, dropped = L.one_body(BLOATED)
    # 腰の向きは一つ。言い換え（jutting / pushed forward）も二つ目は落ちる。
    assert "hips jutting out sharply" in kept
    assert "hips pushed forward" in dropped
    # 手の中のトレイは一つ。三つの言い方のうち最初だけ残る。
    assert "hands_clutching_tray_edge" in kept
    assert "hugging tray" in dropped
    assert "hands gripping tray_edge with knuckles white" in dropped


def test_a_phrase_already_contained_in_another_is_dropped():
    kept, dropped = L.one_body(BLOATED)
    assert "left hand on hip" in kept
    assert "hand on hip" in dropped, "`left hand on hip` の中にある"


def test_the_directors_body_survives():
    """監督は「トレイを抱えつつ、腰に手を当てて、強気な感じで」と言った。"""
    kept, _ = L.one_body(BLOATED)
    assert len([t for t in kept.split(",") if t.strip()]) == 8
    for must in ("hands_clutching_tray_edge", "left hand on hip",
                 "hips jutting out sharply", "standing"):
        assert must in kept


def test_a_body_that_is_already_one_body_is_untouched():
    """**取りすぎない。** 人が撮った値には一語も触らない。"""
    kept, dropped = L.one_body(CLEAN)
    assert dropped == []
    assert kept == CLEAN


def test_two_hands_hold_two_different_things():
    """手は二本ある。トレイとカップは**別の物**なので両方立つ。

    実機の値（`looking at viewer, posing, holding tray, holding coffee cup`）。
    物を見ずに「掴み」でまとめた最初の形は、ここでカップを捨てていた。
    """
    kept, dropped = L.one_body("looking at viewer, posing, holding tray, holding coffee cup")
    assert dropped == []
    assert "holding tray" in kept and "holding coffee cup" in kept


def test_the_same_thing_in_her_hands_is_one_thing():
    kept, dropped = L.one_body("hands gripping tray_edge, holding order_tray")
    assert "hands gripping tray_edge" in kept
    assert "holding order_tray" in dropped, "同じトレイ"


def test_letting_go_and_holding_the_same_thing_cannot_both_be_true():
    kept, dropped = L.one_body(
        "hands releasing tray_edge, hands_clutching_tray_edge, hands steadying tray_edge"
    )
    assert "hands releasing tray_edge" in kept
    assert len(dropped) == 2


def test_the_throw_is_not_eaten():
    """ボウリングの投球。`releasing ball` は `releasing bowling ball` の言い換え
    なので落ちるが、**投げる動作そのものは残る**。"""
    kept, dropped = L.one_body(
        "releasing bowling ball, profile view, side view, releasing ball, low angle"
    )
    assert "releasing bowling ball" in kept
    assert dropped == ["releasing ball"]


def test_a_part_named_without_a_direction_is_never_dropped():
    """向きの語が無い句には触らない —— `arms_stiff` と `forearms tensed` は
    どちらも腕の話だが、どちらも体の別のことを言っている。"""
    kept, dropped = L.one_body("arms_stiff, forearms tensed, elbows flared outward, shoulders hunched")
    assert dropped == []
    assert kept == "arms_stiff, forearms tensed, elbows flared outward, shoulders hunched"


def test_the_entrance_folds_the_body_and_says_what_it_dropped():
    """**入口は `normalize_patch` 一つ。** 席の経路でもカードの経路でも効く。"""
    report: dict[str, list[str]] = {}
    out = L.normalize_patch({"beat": BLOATED, "scene": "cafe open terrace"}, report=report)
    assert out["beat"] == L.one_body(BLOATED)[0]
    assert out["scene"] == "cafe open terrace", "体以外の欄は畳まない"
    assert report["beat"] == L.one_body(BLOATED)[1], "落としたものを呼び元へ渡す"


def test_the_partner_has_a_body_too():
    report: dict[str, list[str]] = {}
    out = L.normalize_patch({"beat_b": BLOATED}, report=report)
    assert len([t for t in out["beat_b"].split(",") if t.strip()]) == 8
    assert report["beat_b"]


def test_only_the_body_fields_are_folded():
    """`bg` は物を並べる欄で、同じ軸という考えが無い。掴みの語が混ざっても触らない。"""
    out = L.normalize_patch({
        "bg": "hot_coffee, coffee_cup, steam, cheese_cake, crumbs",
        "light": "backlighting, rim_light",
    })
    assert out["bg"] == "hot_coffee, coffee_cup, steam, cheese_cake, crumbs"
    assert out["light"] == "backlighting, rim_light"


def test_the_contract_tells_the_writer_the_body_is_one():
    """条文側も直した —— 落とすだけでは毎ターン同じ掃除を払う。"""
    from app.muse import crew_room, writer

    assert "ONE BODY, ONE INSTANT" in writer.WRITER_SYSTEM
    block = crew_room.craft_block([{"field": "beat", "craft": "weight on back foot"}])
    assert "FOLD IN ONLY WHAT IS NOT THERE YET" in block


def test_a_body_that_forgot_to_say_she_is_standing_gets_it_back():
    """**欄は丸ごと書き直す所なので、書かれなかったものは消える。**

    台で A/B したとき（2026-09-12）、「一つの体」の条文を足すと矛盾は消えたが、
    同じ回で `standing` が落ちた —— 台本係が「既に分かっていること」として
    省いた。条文の言い回しでは戻らなかったので、ここで守る。
    """
    before = {**L.blank(), "beat": "standing, weight on back foot, hips retracted"}
    out = L.scrub_patch({"beat": "weight on back foot, hips jutting out sharply, hugging tray"}, before)
    assert out["beat"].startswith("standing, ")


def test_a_body_that_names_its_own_posture_is_left_alone():
    """「座って」と言われた回を立たせない。"""
    before = {**L.blank(), "beat": "standing, weight on back foot"}
    out = L.scrub_patch({"beat": "sitting on the floor, legs tucked to the side"}, before)
    assert out["beat"] == "sitting on the floor, legs tucked to the side"
    out = L.scrub_patch({"beat": "kneeling, hands on knees"}, {**L.blank(), "beat": "standing"})
    assert out["beat"] == "kneeling, hands on knees"


def test_nothing_is_invented_when_the_old_body_had_no_posture_either():
    before = {**L.blank(), "beat": "hands in her pockets"}
    out = L.scrub_patch({"beat": "looking over her shoulder"}, before)
    assert out["beat"] == "looking over her shoulder"


def test_the_posture_words_come_from_the_one_list_we_already_have():
    """語の表を二つ持たない —— `tags.conflict` の `posture` 槽をそのまま使う。"""
    import inspect

    src = inspect.getsource(L._posture_of)
    assert "conflict.slot_of" in src and "posture" in src


def test_supporting_a_thing_is_holding_it():
    """実機（2026-09-12・2回目の再現）で残った二重。

        right arm holding tray, forearm_supporting_tray

    `supporting` を掴みの語に入れていなかったので軸に乗らなかった。**左手は腰・
    右手はトレイ**という書き分けは残したまま、トレイの二重だけ落ちること。
    """
    kept, dropped = L.one_body(
        "standing, weight on back leg, left hand on hip, right arm holding tray, "
        "elbows flared outward, forearm_supporting_tray"
    )
    assert dropped == ["forearm_supporting_tray"]
    assert "left hand on hip" in kept and "right arm holding tray" in kept
    assert kept.startswith("standing, ")

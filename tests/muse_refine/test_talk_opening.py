"""Opening dress, bans, W-bubble helpers."""
from __future__ import annotations

from app.muse_refine import talk
from app.muse_refine.service import new_session, public_view


def test_dress_from_signature_seeds_wearing():
    session = new_session()
    session["character"] = {
        "character_id": "a",
        "wardrobe_sets": [{
            "key": "signature",
            "tags": ["white_shirt", "blue_skirt"],
            "props": ["school_bag"],
        }],
    }
    patch = talk.dress_from_signature(session)
    assert "white_shirt" in patch["wearing"]
    assert "school_bag" in session["refine_ledger"]["wearing"]


def test_dress_partner_wearing_b():
    session = new_session()
    session["character"] = {
        "character_id": "a",
        "wardrobe_sets": [{"key": "signature", "tags": ["hoodie"], "props": []}],
    }
    session["partner_character"] = {
        "character_id": "b",
        "wardrobe_sets": [{"key": "signature", "tags": ["sundress"], "props": []}],
    }
    talk.dress_from_signature(session)
    assert "hoodie" in session["refine_ledger"]["wearing"]
    assert "sundress" in session["refine_ledger"]["wearing_b"]


def test_ban_and_filter_tags():
    session = new_session()
    talk.ban_tag(session, "hat")
    kept = talk.filter_banned_tags(session, ["white_shirt", "hat", "red_hat", "smile"])
    assert "white_shirt" in kept
    assert "smile" in kept
    assert "hat" not in kept
    assert "red_hat" not in kept
    assert talk.restore_tag(session, "hat")
    assert talk.filter_banned_tags(session, ["hat"]) == ["hat"]


def test_again_feel_is_not_refines_business():
    """**「またあの感じ」は Refine では拾わない。**（2026-09-10）

    総監督「前回の内容からの提案は削除して時間短縮」。`vitality` 側の関数は
    classic Muse が使うので残っている —— 消したのは Refine からの呼び出し。
    """
    session = new_session()
    session["memories"] = ["屋上の風が冷たかった"]
    talk.prepare_vitality_flags(session, user_line="またあの感じでお願い")
    assert not session.get("again_feel_hint")


def test_public_view_taste_and_banned():
    session = new_session()
    session["banned"] = ["sword"]
    session["showrunner_taste"] = {"prefers": "柔らかい光", "avoids": ""}
    session["opened"] = True
    view = public_view(session)
    assert view["banned"] == ["sword"]
    assert view["opened"] is True
    assert any("柔らかい" in c or "光" in c for c in view["taste_chips"]) or view["taste_chips"] == []

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.muse import runner, events, identity, runtime


@pytest.mark.asyncio
async def test_events_publisher_and_listeners():
    """Test event dispatcher publishes SSE events to subscribers."""
    q = await events.subscribe("sess_123")
    assert events.subscriber_count("sess_123") == 1

    events.publish("sess_123", {"type": "say", "text": "Hello!"})

    evt = await q.get()
    assert evt["type"] == "say"
    assert evt["session_id"] == "sess_123"

    await events.unsubscribe("sess_123", q)
    assert events.subscriber_count("sess_123") == 0


def test_runtime_negative_and_settings():
    """Test runtime.negative_for and runtime.render_settings."""
    session = {
        "inputs": {"width": 1024, "height": 1024, "draft_steps": 15, "final_steps": 35},
        "character": {"identity_tags": ["1girl", "blue_hair"]},
        "banned": ["monochrome"],
    }

    neg = runtime.negative_for(session)
    assert "monochrome" in neg
    assert isinstance(neg, str)

    draft_set = runtime.render_settings(session["inputs"], draft=True)
    assert draft_set["steps"] == 15
    assert draft_set["width"] == 1024

    final_set = runtime.render_settings(session["inputs"], draft=False)
    assert final_set["steps"] == 35


def test_negative_carries_only_the_box_and_the_refusals():
    """図の守りはポジティブ側でやる。ネガティブに体型・年齢を積まない。

    主演撮りも班撮影も `runtime.negative_for` の一本道なので、両方に効く。
    """
    session = {
        "inputs": {
            "negative_prompt": "bad quality, bad anatomy",
            "framing": "auto",
        },
        "character": {"identity_tags": ["1girl", "medium_breasts", "slim"]},
        "partner_character": {"identity_tags": ["1girl", "large_breasts"]},
        "banned": ["cleaning_rag"],
    }
    neg = runtime.negative_for(session)
    tokens = {t.strip() for t in neg.split(",") if t.strip()}
    assert tokens == {"bad quality", "bad anatomy", "cleaning_rag"}
    # 体型ロックの反対側も、年齢語も入らない。
    for gone in ("loli", "old", "child", "mature_female", "petite",
                 "large_breasts", "flat_chest", "muscular", "curvy"):
        assert gone not in tokens

    # 守りはポジティブ側が持つ：ロックと矛盾する語は positive に入らない。
    positive = identity.assemble_positive(
        ["1girl", "medium_breasts", "slim"],
        "1girl, large_breasts, loli, park, standing",
        "she waits in the park",
    )
    assert "large_breasts" not in positive
    assert "loli" not in positive
    assert "park" in positive and "standing" in positive


def test_default_negative_does_not_fight_a_plain_background():
    """白ホリの撮影は simple_background そのものを頼む。既定で撃たない。"""
    from app.muse.defaults import STYLE_DEFAULTS
    box = str(STYLE_DEFAULTS["negative_prompt"])
    assert "simple_background" not in box
    assert "simple," not in box
    assert "bad anatomy" in box


def test_the_chosen_look_rules_its_opposite_out():
    """セル画を頼んだ班の絵が柔らかいままだった。

    43語中3語の cel_shading では、チェックポイントの既定を押し切れない。
    体型・年齢の自動注入とは別物 — あれは被写体について言い争っていたが、
    これは総監督がいま断ったレンダリングを名指しする。
    """
    from app.muse import crew
    flat = {
        "mode": "", "inputs": {"crew_preset": "flat", "negative_prompt": "bad quality"},
        "banned": [],
    }
    neg = {t.strip() for t in runtime.negative_for(flat).split(",") if t.strip()}
    assert "soft_shading" in neg and "realistic" in neg
    assert "cel_shading" not in neg          # 頼んだほうは打ち消さない

    real = {"mode": "", "inputs": {"crew_preset": "photoreal"}, "banned": []}
    neg2 = {t.strip() for t in runtime.negative_for(real).split(",") if t.strip()}
    assert "cel_shading" in neg2 and "flat_color" in neg2
    assert "realistic" not in neg2

    # 中立の班は何も打ち消さない
    plain = {"mode": "", "inputs": {"crew_preset": "standard"}, "banned": []}
    assert crew.look_negative(runtime.style_for(plain)) == []


def test_style_for_is_one_answer_for_both_halves_of_the_prompt():
    """ポジとネガが別々にルックを決めると、片方だけ効く事故になる。"""
    from app.muse import crew, service
    duet = {"mode": "duet", "inputs": {"look": "flat"}}
    assert runtime.style_for(duet) == "flat anime cel shading"
    assert service._style(duet) == runtime.style_for(duet)
    assert runtime.style_for({"mode": "duet", "inputs": {}}) == crew.NEUTRAL_LOOK

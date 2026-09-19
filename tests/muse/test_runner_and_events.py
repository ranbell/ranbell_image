import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import events, identity, runtime


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
    """The picture is guarded on the positive side. Body type and age are not stacked
    into the negative.

    Both the lead's shoot and the crew shoot take the single road of
    `runtime.negative_for`, so this bites on both.
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
    """A white-cyclorama shoot asks for `simple_background` itself. The default does not
    shoot it down."""
    from app.muse.defaults import STYLE_DEFAULTS
    box = str(STYLE_DEFAULTS["negative_prompt"])
    assert "simple_background" not in box
    assert "simple," not in box
    assert "bad anatomy" in box


def test_the_chosen_look_rules_its_opposite_out():
    """A crew picture that asked for cel shading stayed soft.

    Three words of `cel_shading` out of 43 cannot push past the checkpoint's
    default. This is not the automatic body-type and age injection — that argued
    about the subject, while this names the rendering the Showrunner has just
    refused.
    """
    from app.muse import crew
    # **班が開いているセッションでだけ、班の画風を取る（2026-09-13）。**
    # 門は `mode` ではなく `crew_room.has_crew` —— `mode == "duet"` で分けていた
    # ときは、Refine の全セッションが `duet` なので**永久に閉じていた**
    # （6プリセットとも `anime illustration` になっていた）。
    flat = {
        "mode": "duet", "crew_open": True,
        "inputs": {"crew_preset": "flat", "negative_prompt": "bad quality",
                   "crew_ids": list(crew.resolve_crew(preset="flat"))},
        "banned": [],
    }
    neg = {t.strip() for t in runtime.negative_for(flat).split(",") if t.strip()}
    assert "soft_shading" in neg and "realistic" in neg
    assert "cel_shading" not in neg          # 頼んだほうは打ち消さない

    real = {"mode": "duet", "crew_open": True,
            "inputs": {"crew_preset": "photoreal",
                       "crew_ids": list(crew.resolve_crew(preset="photoreal"))},
            "banned": []}
    neg2 = {t.strip() for t in runtime.negative_for(real).split(",") if t.strip()}
    assert "cel_shading" in neg2 and "flat_color" in neg2
    assert "realistic" not in neg2

    # 中立の班は何も打ち消さない
    plain = {"mode": "", "inputs": {"crew_preset": "standard"}, "banned": []}
    assert crew.look_negative(runtime.style_for(plain)) == []



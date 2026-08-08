import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.muse import runner, events, runtime


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

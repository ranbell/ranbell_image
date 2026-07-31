"""Unit tests for shared SSE / topic_anchors."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.jobs.sse_stream import iter_queue_sse
from app.tags.topic_anchors import topic_anchor_groups, topic_anchor_tokens


def test_iter_queue_sse_json_and_cleanup():
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        registry: dict = {"job-1": q}
        request = SimpleNamespace(
            is_disconnected=AsyncMock(return_value=False),
            app=SimpleNamespace(
                state=SimpleNamespace(spooler=SimpleNamespace(cancel=AsyncMock()))
            ),
        )

        async def _produce():
            await q.put({"type": "token", "text": "hi"})
            await q.put(None)

        asyncio.create_task(_produce())
        frames = []
        async for frame in iter_queue_sse(
            request, q, job_id="job-1", registry=registry, encode="json",
        ):
            frames.append(frame)
        assert any('"hi"' in f for f in frames)
        assert "job-1" not in registry

    asyncio.run(_run())


def test_topic_anchors_cafe_and_rooftop():
    assert "cafe" in topic_anchor_tokens("この子がカフェで働く話")
    groups = topic_anchor_groups("屋上で星を見る")
    flat = {t for g in groups for t in g}
    assert "rooftop" in flat and "star" in flat


def test_wd14_tags_helpers_defined_in_source():
    text = (
        Path(__file__).resolve().parents[2]
        / "backend" / "app" / "ai" / "wd14.py"
    ).read_text(encoding="utf-8")
    assert "async def tags_from_bytes" in text
    assert "async def tags_from_path" in text

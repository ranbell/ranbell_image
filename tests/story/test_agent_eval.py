"""Unit tests for Chronicle eval export + agent-run helpers."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

# Import eval_export first (no qdrant). Patch agent_run's story_db before heavy use.
from app.story.eval_export import build_eval_bundle, export_eval_bundle

# agent_run imports story.db — stub qdrant-free stand-in if needed via monkeypatch in tests.
import app.story.agent_run as agent_run_mod
from app.story.agent_run import AgentRun, drain_token_queue


def _story_fixture(tmp_path: Path) -> tuple[dict, dict[str, dict]]:
    pngs = {}
    docs = {}
    for i, axis in enumerate(("panel_1", "panel_2", "panel_3")):
        p = tmp_path / f"src_{axis}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 16)
        sha = f"sha{i}"
        pngs[axis] = sha
        docs[sha] = {"path": str(p), "sha256": sha}
    story = {
        "story_id": "story-test-1",
        "status": "final",
        "title": "Test Title",
        "overall_story": "Overall",
        "user_topic": "カフェ",
        "author_style": "",
        "time_scale": "days",
        "workflow_name": "wf.json",
        "base_image_id": "",
        "selected_candidate": "A",
        "quality_eval": {"overall": 0.7, "ok": True},
        "group_id": "chr-x",
        "created_at": 1.0,
        "axes": {
            axis: {
                "story": f"en {axis}",
                "story_ja": f"ja {axis}",
                "prompt_positive": f"1girl, {axis}, smile",
                "prompt_negative": "blurry",
                "visual_script": "",
                "image_id": pngs[axis],
            }
            for axis in ("panel_1", "panel_2", "panel_3")
        },
    }
    return story, docs


def test_build_eval_bundle_urls(tmp_path: Path):
    story, docs = _story_fixture(tmp_path)
    bundle = build_eval_bundle(story, db_docs=docs)
    assert bundle["story_id"] == "story-test-1"
    assert bundle["axes"]["panel_1"]["original_url"] == "/api/originals/sha0"
    assert bundle["axes"]["panel_2"]["thumbnail_url"] == "/api/thumbnails/sha1.webp"
    assert bundle["quality_eval"]["overall"] == 0.7


def test_export_eval_writes_files(tmp_path: Path):
    story, docs = _story_fixture(tmp_path)
    out = tmp_path / "out_eval"
    meta = export_eval_bundle(story, db_docs=docs, out_dir=out)
    assert Path(meta["export_dir"]) == out.resolve()
    assert (out / "report.json").is_file()
    assert (out / "prompts.md").is_file()
    assert (out / "panel_1.png").is_file()
    assert (out / "panel_2.png").is_file()
    assert (out / "panel_3.png").is_file()
    assert set(meta["copied_panels"]) == {"panel_1", "panel_2", "panel_3"}
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["axes"]["panel_1"]["prompt_positive"].startswith("1girl")


def test_drain_token_queue_candidates():
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        await q.put({"type": "phase", "code": "x"})
        await q.put({"type": "candidates", "story_id": "s1", "candidates": [{"id": "A"}]})
        evt = await drain_token_queue(q, until_types={"candidates"}, timeout_sec=2.0)
        assert evt["story_id"] == "s1"
        assert evt["candidates"][0]["id"] == "A"

    asyncio.run(_run())


def test_drain_token_queue_error():
    async def _run():
        q: asyncio.Queue = asyncio.Queue()
        await q.put({"type": "error", "message": "boom"})
        with pytest.raises(RuntimeError, match="boom"):
            await drain_token_queue(q, until_types={"done"}, timeout_sec=2.0)

    asyncio.run(_run())


def test_wait_for_panel_images():
    calls = {"n": 0}

    async def fake_get(_db, story_id):
        calls["n"] += 1
        ready = calls["n"] >= 2
        return {
            "story_id": story_id,
            "axes": {
                "panel_1": {"image_id": "a" if ready else None},
                "panel_2": {"image_id": "b" if ready else None},
                "panel_3": {"image_id": "c" if ready else None},
            },
        }

    async def _run():
        story = await agent_run_mod.wait_for_panel_images(
            None, "sid", timeout_sec=5.0, poll_sec=0.01, get_story_fn=fake_get,
        )
        assert story["axes"]["panel_1"]["image_id"] == "a"

    asyncio.run(_run())


def test_agent_run_to_dict():
    run = AgentRun(run_id="arun-x", status="queued", candidate_id="B")
    d = run.to_dict()
    assert d["run_id"] == "arun-x"
    assert d["candidate_id"] == "B"
    assert d["status"] == "queued"

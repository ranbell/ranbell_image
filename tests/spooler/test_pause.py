"""Spooler pause / exclusivity / priority tests.

The spooler is pure asyncio (no FastAPI/Qdrant dependency), so these tests run
it for real with short dummy jobs. Async tests are wrapped with asyncio.run()
to avoid a pytest-asyncio dependency.

Covers:
  - priority-ordered dequeue
  - tier1 auto-pause (GENERATION active → EMBEDDING paused) and auto-resume
  - tier2 EVALUATION pause (blocked by EMBEDDING) and auto-resume
  - eval_auto_pause=False (tier2 disabled — multi-GPU setups)
  - resource semaphore serialization (prompt_gen_mutex semantics)
  - cancel of a queued job
  - build_resources lane mapping (mutex / expert overrides / client-managed rejection)
  - Resource.acquire fail-fast semantics
  - InvokeSession completion event
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.spooler.models import JobCancelled, JobLane, JobState, LanePauseReason, ResourceUnreachable
from app.spooler.resources import Resource, build_resources
from app.spooler.spooler import JobSpooler


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_spooler(lane_resource_overrides: dict | None = None,
                  resources: dict | None = None) -> JobSpooler:
    res = resources or {
        "local-gpu0": Resource(name="local-gpu0", kind="local", concurrency=1),
    }
    lane_resource: dict = {lane: None for lane in JobLane}
    lane_resource.update(lane_resource_overrides or {})
    return JobSpooler(resources=res, lane_resource=lane_resource)


def _make_job(log: list, name: str, duration: float = 0.05):
    async def _job(reporter, cancel, **kwargs):
        log.append(("start", name, time.monotonic()))
        await asyncio.sleep(duration)
        log.append(("end", name, time.monotonic()))
        return name
    return _job


def _events(log: list, kind: str) -> list[str]:
    return [name for k, name, _ in log if k == kind]


def _span(log: list, name: str) -> tuple[float, float]:
    start = next(t for k, n, t in log if k == "start" and n == name)
    end = next(t for k, n, t in log if k == "end" and n == name)
    return start, end


# ── Priority queue ────────────────────────────────────────────────────────────

def test_priority_order():
    async def _t():
        sp = _make_spooler()
        await sp.start()
        try:
            log: list = []
            # Hold the lane so all three queue up before any runs
            sp.pause_lanes([JobLane.SYNC], LanePauseReason.MANUAL)
            a = sp.submit(JobLane.SYNC, "a", _make_job(log, "a"), priority=0)
            b = sp.submit(JobLane.SYNC, "b", _make_job(log, "b"), priority=5)
            c = sp.submit(JobLane.SYNC, "c", _make_job(log, "c"), priority=0)
            sp.resume_lanes([JobLane.SYNC])
            for jid in (a, b, c):
                await sp.wait(jid)
            assert _events(log, "start") == ["b", "a", "c"]
        finally:
            await sp.stop()
    asyncio.run(_t())


# ── tier1: GENERATION active → EMBEDDING paused ───────────────────────────────

def test_tier1_auto_pause_and_resume():
    async def _t():
        sp = _make_spooler()
        await sp.start()
        try:
            log: list = []
            gen = sp.submit(JobLane.GENERATION, "gen", _make_job(log, "gen", 0.15))
            # _update_auto_pause runs synchronously inside submit()
            assert not sp.is_lane_active(JobLane.EMBEDDING)
            emb = sp.submit(JobLane.EMBEDDING, "emb", _make_job(log, "emb", 0.05))

            await asyncio.sleep(0.05)
            assert _events(log, "start") == ["gen"], "embed must not start during generation"

            await sp.wait(gen)
            await sp.wait(emb)
            assert sp.is_lane_active(JobLane.EMBEDDING)
            gen_end = _span(log, "gen")[1]
            emb_start = _span(log, "emb")[0]
            assert emb_start >= gen_end
        finally:
            await sp.stop()
    asyncio.run(_t())


# ── tier2: EMBEDDING active → EVALUATION paused ───────────────────────────────

def test_tier2_eval_pause():
    async def _t():
        sp = _make_spooler()
        await sp.start()
        try:
            log: list = []
            emb = sp.submit(JobLane.EMBEDDING, "emb", _make_job(log, "emb", 0.15))
            assert not sp.is_lane_active(JobLane.EVALUATION)
            ev = sp.submit(JobLane.EVALUATION, "ev", _make_job(log, "ev", 0.05))

            await asyncio.sleep(0.05)
            assert _events(log, "start") == ["emb"]

            await sp.wait(emb)
            await sp.wait(ev)
            assert sp.is_lane_active(JobLane.EVALUATION)
            assert _span(log, "ev")[0] >= _span(log, "emb")[1]
        finally:
            await sp.stop()
    asyncio.run(_t())


def test_eval_auto_pause_disabled():
    async def _t():
        sp = _make_spooler()
        sp.update_pause_settings(
            auto_pause_on_priority=True,
            auto_pause_target_lanes=["embed", "eval"],
            eval_auto_pause=False,
        )
        await sp.start()
        try:
            log: list = []
            gen = sp.submit(JobLane.GENERATION, "gen", _make_job(log, "gen", 0.15))
            assert sp.is_lane_active(JobLane.EVALUATION), "tier2 disabled — EVAL stays active"
            ev = sp.submit(JobLane.EVALUATION, "ev", _make_job(log, "ev", 0.05))
            await sp.wait(ev)
            await sp.wait(gen)
            # EVAL ran concurrently with GENERATION (finished before gen ended)
            assert _span(log, "ev")[1] < _span(log, "gen")[1]
        finally:
            await sp.stop()
    asyncio.run(_t())


# ── Resource semaphore: prompt_gen_mutex semantics ────────────────────────────

def test_shared_resource_serializes_lanes():
    async def _t():
        sp = _make_spooler(lane_resource_overrides={
            JobLane.GENERATION: "local-gpu0",
            JobLane.PROMPT:     "local-gpu0",
        })
        await sp.start()
        try:
            log: list = []
            gen = sp.submit(JobLane.GENERATION, "gen", _make_job(log, "gen", 0.1))
            pr  = sp.submit(JobLane.PROMPT, "pr", _make_job(log, "pr", 0.1))
            await sp.wait(gen)
            await sp.wait(pr)
            g0, g1 = _span(log, "gen")
            p0, p1 = _span(log, "pr")
            assert g1 <= p0 or p1 <= g0, "gen and prompt must not overlap under the shared semaphore"
        finally:
            await sp.stop()
    asyncio.run(_t())


# ── Cancel ────────────────────────────────────────────────────────────────────

def test_cancel_queued_job():
    async def _t():
        sp = _make_spooler()
        await sp.start()
        try:
            log: list = []
            sp.pause_lanes([JobLane.SYNC], LanePauseReason.MANUAL)
            jid = sp.submit(JobLane.SYNC, "x", _make_job(log, "x"))
            assert await sp.cancel(jid)
            with pytest.raises(JobCancelled):
                await sp.wait(jid)
            assert _events(log, "start") == []
        finally:
            await sp.stop()
    asyncio.run(_t())


# ── build_resources lane mapping ──────────────────────────────────────────────

class _FakeSettings:
    ollama_url = "http://ollama:11434"
    comfyui_url = "http://comfy:8188"
    qdrant_url = None


def test_build_resources_defaults():
    resources, lane_map, _ = build_resources(_FakeSettings())
    assert "remote-ollama" in resources and "remote-comfyui" in resources
    assert lane_map[JobLane.GENERATION] == "remote-comfyui"
    # Ollama is client-managed: no lane holds its semaphore across a whole job
    assert lane_map[JobLane.EMBEDDING] is None
    assert lane_map[JobLane.PROMPT] is None
    assert lane_map[JobLane.TAGGING] is None


def test_build_resources_prompt_gen_mutex():
    class S(_FakeSettings):
        prompt_gen_mutex = True
    _, lane_map, _ = build_resources(S())
    assert lane_map[JobLane.GENERATION] == "local-gpu0"
    assert lane_map[JobLane.PROMPT] == "local-gpu0"


def test_build_resources_lane_map_overrides():
    class S(_FakeSettings):
        resource_lane_map = {
            "sync": "remote-comfyui",     # valid override
            "eval": "remote-ollama",      # client-managed → rejected (self-deadlock)
            "gen": None,                  # explicit unmap
            "bogus": "local-gpu0",        # unknown lane → ignored
        }
    _, lane_map, _ = build_resources(S())
    assert lane_map[JobLane.SYNC] == "remote-comfyui"
    assert lane_map[JobLane.EVALUATION] is None
    assert lane_map[JobLane.GENERATION] is None


# ── Resource.acquire fail-fast ────────────────────────────────────────────────

def test_resource_acquire_fail_fast():
    async def _t():
        res = Resource(name="r", kind="remote", endpoint="http://x")
        # Before the first probe: permissive (startup grace)
        async with res.acquire():
            pass
        # After a probe marked it unreachable: fail fast
        res.last_checked = time.time()
        res.reachable = False
        with pytest.raises(ResourceUnreachable):
            async with res.acquire():
                pass
        # Reachable again: fine
        res.reachable = True
        async with res.acquire():
            pass
    asyncio.run(_t())


# ── InvokeSession completion event ────────────────────────────────────────────

def test_session_completion_event_on_cancel():
    async def _t():
        from app.invoke.session_manager import InvokeSessionManager
        mgr = InvokeSessionManager()
        session = mgr.create_session(
            user_intent="t", input_mode="light", workflow_name="wf",
            enabled_spirits=["faithful"],
        )
        assert not session.completion.is_set()
        assert await mgr.cancel_session(session.session_id)
        assert session.completion.is_set()
        # completion.wait() returns immediately after terminal state
        await asyncio.wait_for(session.completion.wait(), timeout=0.1)
    asyncio.run(_t())

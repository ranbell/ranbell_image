"""失敗したジョブを、もう一度流す／片付ける。

**終わったジョブは `_registry` から消えて `_history` へ移る**
（`_move_to_history` —— 履歴が source of truth）。`retry` も `cancel` も
`_registry` しか見ていなかったので、失敗したジョブに対しては**定義上いつも
404** だった。画面は履歴から一覧を出しているので、押せるのに必ず失敗する。

総監督（2026-08-29）「画面から retry が効かないです」「job キャンセルが
エラー時だけないからこれもいるね」。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir / "backend") not in sys.path:
    sys.path.insert(0, str(root_dir / "backend"))

from app.spooler.models import JobLane, JobState  # noqa: E402
from app.spooler.resources import Resource  # noqa: E402
from app.spooler.spooler import JobSpooler  # noqa: E402


def _spooler() -> JobSpooler:
    res = {"local-gpu0": Resource(name="local-gpu0", kind="local", concurrency=1)}
    return JobSpooler(resources=res, lane_resource={lane: None for lane in JobLane})


async def _boom(reporter, cancel, **kwargs):
    raise RuntimeError("All connection attempts failed")


async def _fine(reporter, cancel, **kwargs):
    return "ok"


async def _run_until_done(sp: JobSpooler, job_id: str, limit: float = 3.0) -> None:
    async def _wait():
        while True:
            job = sp._find(job_id)
            if job is not None and job.state in (
                JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED,
            ):
                return
            await asyncio.sleep(0.02)
    await asyncio.wait_for(_wait(), timeout=limit)


@pytest.mark.asyncio
async def test_a_failed_job_can_be_retried():
    """**履歴に落ちたジョブを引けること。** ここが 404 の正体だった。"""
    sp = _spooler()
    await sp.start()
    try:
        jid = sp.submit(lane=JobLane.GENERATION, title="muse_board", func=_boom)
        await _run_until_done(sp, jid)
        assert sp._registry.get(jid) is None, "失敗したジョブが registry に残っている"
        assert sp._find(jid) is not None, "履歴から引けない"

        new_id = sp.retry(jid)          # 以前はここで KeyError → 404
        assert new_id and new_id != jid
    finally:
        await sp.stop()


@pytest.mark.asyncio
async def test_a_finished_job_can_be_dismissed():
    """止めるものはもう無い。要るのは取り消しではなく**片付け**。"""
    sp = _spooler()
    await sp.start()
    try:
        jid = sp.submit(lane=JobLane.GENERATION, title="muse_board", func=_boom)
        await _run_until_done(sp, jid)

        assert await sp.cancel(jid) is False, "終わったものが cancel できてしまう"
        assert sp.dismiss(jid) is True
        assert sp._find(jid) is None, "履歴から消えていない"
        assert sp.dismiss(jid) is False, "二度目は何も消さない"
    finally:
        await sp.stop()


@pytest.mark.asyncio
async def test_a_running_job_is_not_dismissed():
    """走っているものは `cancel` の領分。片付けは効かない。"""
    sp = _spooler()
    await sp.start()
    try:
        async def _slow(reporter, cancel, **kwargs):
            await asyncio.sleep(0.4)
            return "ok"

        jid = sp.submit(lane=JobLane.GENERATION, title="slow", func=_slow)
        await asyncio.sleep(0.1)
        assert sp.dismiss(jid) is False
        await _run_until_done(sp, jid)
    finally:
        await sp.stop()


@pytest.mark.asyncio
async def test_dismiss_is_unknown_for_a_job_that_never_existed():
    sp = _spooler()
    assert sp.dismiss("no-such-job") is False
    with pytest.raises(KeyError):
        sp.retry("no-such-job")


@pytest.mark.asyncio
async def test_a_job_waits_while_the_resource_is_down():
    """総監督（2026-08-29）「spooler なので異常時は待機してその後流せるのが
    やっぱり必要」。

    ComfyUI が落ちて `muse_board` が `All connection attempts failed` で
    倒れた場面。復帰は `monitor_remote_resources` が既に検知している ——
    **復帰は分かっているのに、落ちている間のジョブを捨てていた。**
    """
    import httpx

    sp = _spooler()
    sp._requeue_delay = 0.01
    await sp.start()
    try:
        tries: list[int] = []

        async def _flaky(reporter, cancel, **kwargs):
            tries.append(1)
            if len(tries) < 3:            # 二回落ちて、三回目で繋がる
                raise httpx.ConnectError("All connection attempts failed")
            return "ok"

        jid = sp.submit(lane=JobLane.GENERATION, title="muse_board", func=_flaky)
        await _run_until_done(sp, jid)
        job = sp._find(jid)
        assert job.state is JobState.SUCCEEDED, "待たずに失敗した"
        assert job.requeues == 2, job.requeues
        assert len(tries) == 3
    finally:
        await sp.stop()


@pytest.mark.asyncio
async def test_waiting_does_not_go_on_for_ever():
    """**永遠には粘らない。** 本当に死んでいるなら、いつかは失敗として見せる。"""
    import httpx

    from app.spooler import spooler as spooler_mod

    sp = _spooler()
    sp._requeue_delay = 0.0
    await sp.start()
    limit = spooler_mod.REQUEUE_MAX
    try:
        async def _dead(reporter, cancel, **kwargs):
            raise httpx.ConnectError("All connection attempts failed")

        jid = sp.submit(lane=JobLane.GENERATION, title="muse_board", func=_dead)
        await _run_until_done(sp, jid, limit=10.0)
        job = sp._find(jid)
        assert job.state is JobState.FAILED
        assert job.requeues == limit
    finally:
        await sp.stop()


@pytest.mark.asyncio
async def test_an_ordinary_bug_fails_at_once():
    """**壊れたジョブは待たせない。** 待つのは資源が居ないときだけ。"""
    sp = _spooler()
    sp._requeue_delay = 0.0
    await sp.start()
    try:
        async def _bug(reporter, cancel, **kwargs):
            raise ValueError("workflow not found")

        jid = sp.submit(lane=JobLane.GENERATION, title="muse_board", func=_bug)
        await _run_until_done(sp, jid)
        job = sp._find(jid)
        assert job.state is JobState.FAILED
        assert job.requeues == 0, "普通の不具合で待ってしまっている"
    finally:
        await sp.stop()

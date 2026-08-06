"""A ComfyUI that dies must fail the job, not hold the lane forever.

Observed: ComfyUI OOMs mid-render, and the whole app stops accepting work. The
job sits in `running`, keeps `local-gpu0`, and the generation lane is blocked
behind it — nothing can be cancelled because there is nothing to cancel from the
spooler's point of view; it thinks the render is still going.

The cause is that `stream_progress` only understands `progress`, `executing` and
`executed`. ComfyUI reports a failure as `execution_error` and then stops
sending anything at all, so the loop waits for an `executing: None` that will
never come and the generator never returns.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

# render imports the scanner, which imports the colour extractor, which wants
# scikit-learn. None of that is on this path; stub it rather than install a
# clustering library to test an error branch.
for _name in ("sklearn", "sklearn.cluster"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["sklearn.cluster"].KMeans = object

import pytest

from app.ai.comfy import ComfyUIClient
from app.jobs.render import run_render


class FakeWS:
    """Replays ComfyUI websocket frames, then closes like a real socket would."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            for f in self._frames:
                yield f
        return _gen()


OOM = (
    '{"type":"execution_error","data":{"prompt_id":"pid","node_id":"12",'
    '"node_type":"KSampler","exception_type":"torch.OutOfMemoryError",'
    '"exception_message":"Allocation on device 0 would exceed allowed memory"}}'
)


@pytest.mark.asyncio
async def test_stream_progress_ends_on_execution_error(monkeypatch):
    client = ComfyUIClient()

    # `websockets` is imported inside the method, so the module itself has to be
    # replaced rather than an attribute on app.ai.comfy.
    fake = types.ModuleType("websockets")
    fake.connect = lambda *a, **k: FakeWS([OOM])
    monkeypatch.setitem(sys.modules, "websockets", fake)

    events = [e async for e in client.stream_progress("pid")]
    kinds = [e["type"] for e in events]
    assert "comfy_failed" in kinds, kinds
    failure = next(e for e in events if e["type"] == "comfy_failed")
    assert "OutOfMemory" in failure["message"] or "exceed allowed memory" in failure["message"]
    assert "KSampler" in failure["message"]


@pytest.mark.asyncio
async def test_run_render_raises_so_the_job_fails_and_frees_the_lane():
    """Swallowing this is what left the lane held with nothing to cancel."""

    class DyingComfy:
        def load_workflow(self, name):
            return {}

        def patchable_fields(self, wf):
            return {"steps": 1, "cfg": 1, "width": 1, "height": 1}

        def patch_workflow(self, wf, *a, **kw):
            return {}

        async def queue_prompt(self, wf, preview=False):
            return "pid"

        async def stream_progress(self, pid):
            yield {"type": "comfy_progress", "value": 1, "max": 20}
            yield {"type": "comfy_failed",
                   "message": "KSampler: torch.OutOfMemoryError"}

        async def fetch_history(self, pid):
            return []

        async def interrupt(self):
            pass

        async def delete_from_queue(self, pid):
            pass

    class Reporter:
        def indeterminate(self):
            pass

        def update(self, *a, **k):
            pass

    class Cancel:
        def on_cancel(self, fn):
            pass

        def raise_if_set(self):
            pass

    with pytest.raises(RuntimeError) as err:
        await run_render(
            Reporter(), Cancel(), db=None, comfy=DyingComfy(),
            workflow_name="w.json", positive="a girl",
        )
    assert "OutOfMemory" in str(err.value)


@pytest.mark.asyncio
async def test_a_silent_websocket_gives_up_instead_of_holding_the_lane(monkeypatch):
    """execution_error covers the failures ComfyUI announces. A hard crash or a
    killed worker announces nothing at all, and that has to end too."""
    import asyncio

    class SilentWS(FakeWS):
        def __aiter__(self):
            async def _gen():
                await asyncio.sleep(3600)
                yield ""          # never reached
            return _gen()

    client = ComfyUIClient()
    fake = types.ModuleType("websockets")
    fake.connect = lambda *a, **k: SilentWS([])
    monkeypatch.setitem(sys.modules, "websockets", fake)

    events = [e async for e in client.stream_progress("pid", idle_timeout=0.05)]
    assert [e["type"] for e in events] == ["error"]
    assert "sent nothing" in events[0]["message"]

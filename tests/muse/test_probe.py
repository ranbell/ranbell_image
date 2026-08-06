"""Split probes and the measurement gate.

The gate's thresholds are not guesses. They were calibrated against 26 real
boards from one session: the six that were unusable measured 25–33 brightness
and 37–86% dead area, and the twenty that were fine measured 48–111 and 0–26%.
The two fixtures here are the clearest of each, downscaled.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import critique, crew, probe

FIXTURES = Path(__file__).parent / "fixtures"

SHOT = {
    "subject": "a girl at a table",
    "pose": "chin on hand",
    "mood": "quietly pleased",
    "wardrobe": "grey cardigan",
    "place": "a corner seat by a tall window",
    "objects": ["wooden table", "glass mug", "napkin"],
    "light": "even daylight through the glass, normal exposure",
    "camera": "medium shot, slight low angle",
}


def _split():
    return {
        s.kind: s for s in probe.split_prompts(
            SHOT, identity_tags=["navy_hair", "silver_eyes"],
            subject=["1girl", "solo"], style="anime illustration",
            framing="upper_body", slot_order=crew.SLOT_ORDER,
        )
    }


def test_the_pose_probe_has_no_room_in_it():
    """A probe meant to answer 'is the pose readable' must not contain the set,
    or its brightness measures the set."""
    pose = _split()[probe.POSE].positive.lower()
    assert "chin on hand" in pose
    assert "white background" in pose
    for leaked in ("window", "wooden table", "daylight", "low angle"):
        assert leaked not in pose, leaked


def test_the_setting_probe_has_nobody_in_it():
    """WD14 cannot check the object ledger while a character fills the frame."""
    setting = _split()[probe.SETTING]
    text = setting.positive.lower()
    assert "wooden table" in text and "window" in text
    assert "no humans" in text
    for leaked in ("1girl", "solo", "navy_hair", "chin on hand", "cardigan"):
        assert leaked not in text, leaked
    # And it says so in the negative as well, because the checkpoint will
    # cheerfully put a person in an empty room.
    assert "solo" in setting.negative.lower()


def test_both_probes_carry_the_session_negative():
    shots = probe.split_prompts(
        SHOT, negative="bad quality, watermark", slot_order=crew.SLOT_ORDER,
    )
    for s in shots:
        assert "watermark" in s.negative


@pytest.mark.asyncio
async def test_a_probe_never_writes_to_the_library():
    """Forty throwaway 512s in the image library is worse than no probe."""
    calls: list[str] = []

    class FakeComfy:
        def load_workflow(self, name):
            calls.append("load")
            return {}

        def patch_workflow(self, wf, pos, neg, *a, **kw):
            calls.append("patch")
            return {}

        async def queue_prompt(self, wf, preview=False):
            return "pid-1"

        async def stream_progress(self, pid):
            yield {"type": "comfy_output",
                   "images": [{"filename": "p.png", "subfolder": "", "type": "output"}]}

        async def fetch_history(self, pid):
            return []

        async def fetch_image(self, filename, subfolder="", type_="output"):
            calls.append("fetch")
            return b"PNGBYTES"

    data = await probe.render(
        FakeComfy(), workflow_name="w.json", positive="p", seed=7,
    )
    assert data == b"PNGBYTES"
    assert calls == ["load", "patch", "fetch"]


@pytest.mark.asyncio
async def test_a_probe_that_cannot_be_taken_returns_none_rather_than_raising():
    class Broken:
        def load_workflow(self, name):
            raise RuntimeError("comfy is down")

    assert await probe.render(Broken(), workflow_name="w", positive="p", seed=1) is None


def test_the_gate_fails_the_board_that_actually_failed():
    r = critique.measure((FIXTURES / "board_void.jpg").read_bytes())
    assert not r.ok
    assert r.mean_luma < 40
    assert r.dead_frac > 0.5
    joined = " ".join(r.failures)
    assert "too dark" in joined and "empty black" in joined


def test_the_gate_passes_a_dark_but_readable_frame():
    """Plain black fraction does not separate these — a moody cafe shot is 47%
    below the black point because dark hair and clothing are content. Dead area
    is what tells a broken frame from a dim one."""
    r = critique.measure((FIXTURES / "board_ok.jpg").read_bytes())
    assert r.ok, r.failures
    assert r.black_frac > 0.25, "this frame really is mostly dark"
    assert r.dead_frac < 0.4


def test_a_pose_probe_is_not_judged_on_exposure():
    """It is rendered on white on purpose."""
    data = (FIXTURES / "board_void.jpg").read_bytes()
    assert not critique.measure(data, check_exposure=True).ok
    assert critique.measure(data, check_exposure=False).ok


def test_missing_ledger_objects_fail_the_gate():
    r = critique.measure(
        (FIXTURES / "board_ok.jpg").read_bytes(),
        must_appear=["wooden table", "glass mug", "napkin", "sugar packets"],
        seen_tags=["wooden table"],
    )
    assert not r.ok
    assert "sugar packets" in r.missing
    assert "ledger objects did not render" in " ".join(r.failures)


def test_the_measurement_block_states_the_verdict_as_fact():
    """A VLM handed a 66%-black frame called it artistically correct. It is not
    asked for an opinion any more."""
    note = critique.measure((FIXTURES / "board_void.jpg").read_bytes()).as_note()
    assert "facts, not opinions" in note
    assert "VERDICT: FAIL" in note


def test_the_ledger_is_matched_on_head_nouns_not_whole_phrases():
    """A probe that plainly contained a desk, a mug and an open notebook was
    reported as '10 of 11 ledger objects did not render': the ledger says
    `glass mug`, WD14 says `mug`."""
    ledger = ["wooden table", "glass mug", "open notebook", "bookshelf",
              "curtain", "window", "reading glasses", "pen", "chair", "drawer"]
    wd14 = ["desk", "mug", "notebook", "bookshelf", "curtains", "window",
            "glasses", "pen", "chair", "drawer", "indoors", "no humans"]
    r = critique.measure((FIXTURES / "board_ok.jpg").read_bytes(),
                         must_appear=ledger, seen_tags=wd14)
    assert r.ledger_hit >= 0.8, r.missing
    # Plurals are the same object.
    assert "curtain" not in r.missing
    # A true absence is still reported.
    r2 = critique.measure((FIXTURES / "board_ok.jpg").read_bytes(),
                          must_appear=["parasol"], seen_tags=wd14)
    assert r2.missing == ["parasol"]


def test_a_sunlit_window_is_not_a_blown_out_failure():
    """A setting probe of a bright room is mostly window. The first threshold
    was picked without looking at one and rejected a correct render at 22%."""
    lim = critique.Limits()
    assert lim.white_max > 0.22

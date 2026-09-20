"""**The same thing is not handed over twice.** (2026-09-10)

The Showrunner: "Muse's re-check takes quite a while, so is it that? It is passing
the whole context, which takes a very long time."

Measured, the culprit was not the re-check but **the actress's prompt**. The input
read in one turn is about 9,500 tokens, of which the actress is 5,300 (56%). And
the output format carried by classic's actress contract and `REFINE_OUTPUT` were
both in there, **twice over**.

Input is processed at a measured **~300 tok/s** (only 7.4 GB of the LLM fits in
VRAM, the rest in system memory). **Characters turn straight into seconds.**
"""
from __future__ import annotations

import re

from app.muse import crew
from app.muse import persona, writer

CHAR = {
    "identity_tags": ["1girl", "silver_hair"],
    "name": "Mio", "name_ja": "みお", "character_id": "c1",
    "personality": {"traits": ["おだやか"], "summary": "", "inner": [],
                    "likes": [], "dislikes": []},
    "palette": [], "signature_prop": "",
}
SESSION = {"character": CHAR, "session_id": "x",
           "inputs": {"locale": "ja"}, "opened": True}


def _system() -> str:
    return persona.actress_system(SESSION, locale="ja", ledger={}, now="")


def test_the_output_format_is_given_once():
    """**Put two side by side and the model has to decide which to obey.**"""
    s = _system()
    assert len(re.findall(r"OUTPUT FORMAT", s)) == 1
    assert len(re.findall(r"^SAY:", s, re.M)) == 1


def test_her_voice_and_contract_stay():
    """Her character is not cut for speed — only the format is dropped."""
    s = _system()
    assert crew.PRODUCTION_CONTRACT[:120] in s
    assert persona.ENTERTAINMENT_CRAFT[:80] in s
    assert persona.REFINE_OUTPUT[:80] in s


def test_the_cut_is_safe_when_the_marker_moves():
    """If classic's wording changes, **it does not silently cut into her character**."""
    assert persona._without_classic_output("no marker here") == "no marker here"
    got = persona._without_classic_output(
        "voice and contract\n\nOUTPUT FORMAT — labelled blocks, nothing else:\nSAY: …")
    assert got == "voice and contract"


def test_verify_reads_her_voice_but_not_the_craft_guide():
    """The re-check needs only her voice. The charm guide has nothing to do with the
    judgement."""
    import inspect

    src = inspect.getsource(writer.verify_and_repair)
    assert "{voice}" in src
    # It looks at the **interpolation**, not a comment (`{persona.ENTERTAINMENT_CRAFT}`).
    assert "{persona.ENTERTAINMENT_CRAFT}" not in src


def test_the_actress_prompt_stays_under_its_measured_budget():
    """**Characters turn straight into seconds.** The cap is set from measurement
    (300 tok/s)."""
    n = len(_system())
    assert n < 12000, f"{n}字 —— 約 {n/3/300:.1f}s を毎ターン読むことになる"

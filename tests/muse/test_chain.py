"""The chain's contract with the model: system prompts, think=False, images.

Two of these guard failures that cost a whole run before they were understood.
A model given no explicit `think` spends its budget reasoning and returns an
empty string; a text-only model given images is not an error in Ollama, it
simply ignores them. Both look like "the chain stopped improving".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import chain


class FakeOllama:
    def __init__(self, reply="a prompt"):
        self.reply = reply
        self.calls: list[dict] = []

    async def generate_text(self, prompt, **kw):
        self.calls.append({"kind": "text", "prompt": prompt, **kw})
        return self.reply

    async def generate_vlm(self, prompt, images, **kw):
        self.calls.append({"kind": "vlm", "prompt": prompt, "images": images, **kw})
        return self.reply


def test_every_stage_has_a_prompt_file_and_an_output_format():
    for _, filename in chain.REFINE_STAGES:
        text = chain.system_prompt(filename)
        assert "OUTPUT FORMAT" in text
        # The instructions are English on purpose: written in Japanese, the model
        # sometimes answered in Japanese, which the image model cannot use.
        assert "English only" in text
    assert "OUTPUT FORMAT" in chain.system_prompt("a_pose.md")


def test_stages_for_clamps_to_the_instructions_that_exist():
    assert len(chain.stages_for(3)) == 3
    assert len(chain.stages_for(1)) == 1
    assert len(chain.stages_for(0)) == 1
    # There is no fourth instruction, so asking for more repeats nothing.
    assert len(chain.stages_for(9)) == 3


@pytest.mark.asyncio
async def test_pose_is_text_only_and_disables_thinking():
    llm = FakeOllama()
    out = await chain.run_pose(llm, brief="BRIEF", model="m", num_ctx=16000)
    assert out == "a prompt"
    call = llm.calls[0]
    assert call["kind"] == "text"
    assert call["prompt"] == "BRIEF"
    assert call["think"] is False
    assert call["options"]["num_ctx"] == 16000


@pytest.mark.asyncio
async def test_refine_sends_the_image_and_uses_tags_only_for_the_first_stage():
    llm = FakeOllama()
    await chain.run_refine(
        llm, stage_file="b_reinforce.md", brief="BRIEF", previous="",
        image=b"jpeg", model="m", num_ctx=None, tags="1girl, sky",
    )
    call = llm.calls[0]
    assert call["kind"] == "vlm"
    assert call["images"] == [b"jpeg"]
    assert call["prompt"] == "BRIEF,1girl, sky"

    await chain.run_refine(
        llm, stage_file="c_cinematic.md", brief="BRIEF", previous="prev prompt",
        image=b"jpeg2", model="m", num_ctx=None,
    )
    assert llm.calls[1]["prompt"] == "BRIEF,prev prompt"


@pytest.mark.asyncio
async def test_an_empty_answer_is_an_error_rather_than_an_empty_prompt():
    # Rendering an empty positive prompt costs a full generation and produces
    # something unrelated to the theme, which is worse than stopping.
    llm = FakeOllama(reply="   ")
    with pytest.raises(chain.ChainError):
        await chain.run_pose(llm, brief="BRIEF", model="m", num_ctx=None)

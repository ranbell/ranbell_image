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
    """Streams like the real client: reasoning and answer on separate channels."""

    def __init__(self, reply="a prompt", thinking=""):
        self.reply = reply
        self.thinking = thinking
        self.calls: list[dict] = []

    async def _stream(self):
        if self.thinking:
            yield {"type": "think", "text": self.thinking}
        yield {"type": "token", "text": self.reply}

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({"kind": "text", "prompt": prompt, **kw})
        return self._stream()

    def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append({"kind": "vlm", "prompt": prompt, "images": images, **kw})
        return self._stream()


def test_every_stage_has_a_prompt_file_and_an_output_format():
    for _, filename in chain.REFINE_STAGES:
        text = chain.system_prompt(filename)
        assert "OUTPUT FORMAT" in text
        assert "TAGS:" in text
        assert "SCENE:" in text
        # The instructions are English on purpose: written in Japanese, the model
        # sometimes answered in Japanese, which the image model cannot use.
        assert "English only" in text
    b = chain.system_prompt("b_reinforce.md")
    assert "ten or more objects" in b.lower()
    assert "never from the <REFERENCE>" in b or "taste cues, not inventory" in b
    a = chain.system_prompt("a_pose.md")
    assert "OUTPUT FORMAT" in a
    assert "face_closeup" in a
    assert "from_behind" in a


def test_stages_for_clamps_to_the_instructions_that_exist():
    assert len(chain.stages_for(3)) == 3
    assert len(chain.stages_for(1)) == 1
    assert len(chain.stages_for(0)) == 1
    # There is no fourth instruction, so asking for more repeats nothing.
    assert len(chain.stages_for(9)) == 3


@pytest.mark.asyncio
async def test_pose_is_text_only_and_carries_the_thinking_switch():
    llm = FakeOllama(reply="TAGS: standing\n\nSCENE: a prompt")
    out = await chain.run_pose(
        llm, brief="BRIEF", model="m", num_ctx=32768,
        identity_tags=["1girl", "small_breasts"],
    )
    assert out.prompt.startswith("1girl, small_breasts")
    assert "standing" in out.prompt
    assert "a prompt" in out.prompt
    assert out.pose_intent == "a prompt"
    call = llm.calls[0]
    assert call["kind"] == "text"
    assert call["prompt"] == "BRIEF"
    assert call["think"] is False, "thinking is opt-in; it costs ~8x the wall clock"
    assert call["options"]["num_ctx"] == 32768
    # Unbounded output regardless: with thinking on, reasoning runs for thousands
    # of tokens before the answer begins, and a default budget cuts the answer
    # off before it is written.
    assert call["options"]["num_predict"] == -1

    await chain.run_pose(llm, brief="BRIEF", model="m", num_ctx=None, think=True)
    assert llm.calls[1]["think"] is True


@pytest.mark.asyncio
async def test_reasoning_is_not_mistaken_for_the_prompt():
    # Ollama sends reasoning on its own channel and leaves the answer empty
    # until it is done. Reading the whole stream as one string would paste
    # thousands of words of deliberation into the image prompt.
    llm = FakeOllama(reply="the prompt", thinking="a" * 5000)
    got = await chain.run_pose(llm, brief="B", model="m", num_ctx=None, think=True)
    assert got.prompt == "the prompt"
    assert got.pose_intent == "the prompt"


@pytest.mark.asyncio
async def test_refine_sends_the_image_and_uses_tags_only_for_the_first_stage():
    llm = FakeOllama(reply="TAGS: sky\n\nSCENE: repaired")
    await chain.run_refine(
        llm, stage_file="b_reinforce.md", brief="BRIEF", previous="",
        image=b"jpeg", model="m", num_ctx=None, tags="1girl, sky",
        pose="she waits",
    )
    call = llm.calls[0]
    assert call["kind"] == "vlm"
    assert call["images"] == [b"jpeg"]
    assert call["think"] is False
    assert "Pose intent: she waits" in call["prompt"]
    assert call["prompt"].endswith(",1girl, sky") or ",1girl, sky" in call["prompt"]

    await chain.run_refine(
        llm, stage_file="c_cinematic.md", brief="BRIEF", previous="prev prompt",
        image=b"jpeg2", model="m", num_ctx=None,
    )
    assert llm.calls[1]["prompt"] == "BRIEF,prev prompt"


@pytest.mark.asyncio
async def test_on_token_forwards_answer_pieces_for_sse():
    llm = FakeOllama(reply="TAGS: x\n\nSCENE: y")
    seen: list[str] = []
    await chain.run_pose(
        llm, brief="B", model="m", num_ctx=None,
        on_token=seen.append,
    )
    assert "".join(seen) == "TAGS: x\n\nSCENE: y"


@pytest.mark.asyncio
async def test_an_empty_answer_is_an_error_rather_than_an_empty_prompt():
    # Rendering an empty positive prompt costs a full generation and produces
    # something unrelated to the theme, which is worse than stopping.
    llm = FakeOllama(reply="   ")
    with pytest.raises(chain.ChainError):
        await chain.run_pose(llm, brief="BRIEF", model="m", num_ctx=None)

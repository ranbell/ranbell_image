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


@pytest.mark.asyncio
async def test_run_muse_streams_say_and_locks_identity():
    llm = FakeOllama(reply=(
        "SAY: Director, one beat.\n\n"
        "TAGS: standing, rooftop\n\n"
        "SCENE: She waits in the rain."
    ))
    turn = await chain.run_muse(
        llm, muse_id="beat", user_prompt="BRIEF", model="m",
        num_ctx=None, identity_tags=["1girl", "blue_hair"],
        framing="auto", brief="BRIEF",
    )
    assert turn.say.startswith("Director")
    assert turn.prompt.startswith("1girl, blue_hair")
    assert turn.pose_intent == "She waits in the rain."
    assert llm.calls[0]["kind"] == "text"
    assert "演出" in llm.calls[0]["system"]


@pytest.mark.asyncio
async def test_run_muse_carries_the_thinking_switch():
    # Every live caller hardcodes think=False (service.py) — the crew are
    # role-limited agents with one narrow job each, and reasoning only adds
    # latency, not quality. This checks the switch itself still works, since
    # it is the one thing standing between a live call and an 8x-slower turn.
    llm = FakeOllama(reply="SAY: ok\n\nTAGS: standing\n\nSCENE: a prompt")
    await chain.run_muse(
        llm, muse_id="beat", user_prompt="BRIEF", model="m",
        num_ctx=32768, identity_tags=["1girl", "small_breasts"],
        framing="auto", brief="BRIEF",
    )
    call = llm.calls[0]
    assert call["kind"] == "text"
    assert call["prompt"] == "BRIEF"
    assert call["think"] is False
    assert call["options"]["num_ctx"] == 32768
    # Unbounded output regardless: with thinking on, reasoning runs for thousands
    # of tokens before the answer begins, and a default budget cuts the answer
    # off before it is written.
    assert call["options"]["num_predict"] == -1

    await chain.run_muse(
        llm, muse_id="beat", user_prompt="BRIEF", model="m",
        num_ctx=None, identity_tags=None, framing="auto", brief="BRIEF",
        think=True,
    )
    assert llm.calls[1]["think"] is True


@pytest.mark.asyncio
async def test_reasoning_is_not_mistaken_for_the_prompt():
    # Ollama sends reasoning on its own channel and leaves the answer empty
    # until it is done. Reading the whole stream as one string would paste
    # thousands of words of deliberation into the image prompt.
    llm = FakeOllama(reply="the prompt", thinking="a" * 5000)
    turn = await chain.run_muse(
        llm, muse_id="beat", user_prompt="B", model="m", num_ctx=None,
        identity_tags=None, framing="auto", brief="B", think=True,
    )
    assert turn.prompt == "the prompt"
    assert turn.pose_intent == "the prompt"


@pytest.mark.asyncio
async def test_on_token_forwards_answer_pieces_for_sse():
    llm = FakeOllama(reply="TAGS: x\n\nSCENE: y")
    seen: list[str] = []
    await chain.run_muse(
        llm, muse_id="beat", user_prompt="B", model="m", num_ctx=None,
        identity_tags=None, framing="auto", brief="B",
        on_token=seen.append,
    )
    assert "".join(seen) == "TAGS: x\n\nSCENE: y"


@pytest.mark.asyncio
async def test_an_empty_answer_is_an_error_rather_than_an_empty_prompt():
    # Rendering an empty positive prompt costs a full generation and produces
    # something unrelated to the theme, which is worse than stopping.
    llm = FakeOllama(reply="   ")
    with pytest.raises(chain.ChainError):
        await chain.run_muse(
            llm, muse_id="beat", user_prompt="BRIEF", model="m", num_ctx=None,
            identity_tags=None, framing="auto", brief="BRIEF",
        )


@pytest.mark.asyncio
async def test_the_planner_answers_in_labelled_lines_not_craft():
    llm = FakeOllama(reply=(
        "SAY: 場所と時間、先に決めますね。\n\n"
        "PLACE: a narrow upstairs room, she is on the floor by the low table\n"
        "HOUR: late afternoon, early autumn\n"
        "LIGHT: even daylight from one window, mid-key, normal exposure\n"
        "ACTION: she has just set down what she was carrying\n"
        "MUST APPEAR: low_table, cushion, window, curtain, mug, paper_bag, "
        "bookshelf, rug, wall_clock, slippers\n"
    ))
    plan = await chain.run_plan(llm, user_prompt="THEME", model="m", num_ctx=None)

    assert plan["place"].startswith("a narrow upstairs room")
    assert plan["hour"] == "late afternoon, early autumn"
    assert "normal exposure" in plan["light"]
    assert len(plan["must_appear"]) == 10
    assert plan["say"].startswith("場所と時間")
    assert "構成" in llm.calls[0]["system"]
    # It settles the situation; it does not write the picture.
    assert "TAGS:" not in llm.calls[0]["system"].split("PLACE:")[0][-400:]


def test_a_planner_answer_without_labels_leaves_the_old_plan_alone():
    # A model that replies with craft, or with nothing, must not blank the plan
    # the crew is already working to.
    assert chain.parse_plan("SAY: hi\n\nTAGS: a, b\n\nSCENE: something") == {}
    assert chain.parse_plan("") == {}
    assert chain.parse_plan("   ") == {}


def test_the_planner_parser_tolerates_sloppy_labels():
    plan = chain.parse_plan(
        "- **PLACE** : a stairwell\n"
        "HOUR：dawn\n"
        "must appear: railing, step, bulb\n"
    )
    assert plan["place"] == "a stairwell"
    assert plan["hour"] == "dawn"
    assert plan["must_appear"] == ["railing", "step", "bulb"]


@pytest.mark.asyncio
async def test_a_model_that_cannot_read_the_board_retries_blind_and_says_so():
    """Ollama does not error for a text-only model handed an image — it returns
    nothing, which is indistinguishable from a bad turn unless we flag it."""

    class BlindOllama(FakeOllama):
        def generate_vlm_stream(self, prompt, images, **kw):
            self.calls.append({"kind": "vlm", "prompt": prompt, "images": images})

            async def _empty():
                yield {"type": "token", "text": "   "}
            return _empty()

    llm = BlindOllama(reply="SAY: ok\n\nTAGS: sky\n\nSCENE: She waits.")
    turn = await chain.run_muse(
        llm, muse_id="beat", user_prompt="BRIEF", model="m", num_ctx=None,
        identity_tags=["1girl"], framing="auto", brief="BRIEF",
        images=[b"jpeg"],
    )
    assert turn.blind is True
    assert turn.prompt.startswith("1girl")
    assert [c["kind"] for c in llm.calls] == ["vlm", "text"]


@pytest.mark.asyncio
async def test_a_seeing_turn_sends_the_board_and_is_not_flagged_blind():
    llm = FakeOllama(reply="SAY: ok\n\nTAGS: sky\n\nSCENE: She waits.")
    turn = await chain.run_muse(
        llm, muse_id="beat", user_prompt="BRIEF", model="m", num_ctx=None,
        identity_tags=["1girl"], framing="auto", brief="BRIEF",
        images=[b"jpeg"],
    )
    assert turn.blind is False
    assert llm.calls[0]["kind"] == "vlm"
    assert llm.calls[0]["images"] == [b"jpeg"]


def test_the_planner_answer_no_longer_carries_a_clothing_line():
    plan = chain.parse_plan(
        "SAY: ここでいきましょう。\n"
        "PLACE: A changing room, standing by the bench.\n"
        "HOUR: Midday, summer.\n"
        "LIGHT: Flat overhead fluorescent.\n"
        "ACTION: Tying a shoelace.\n"
        "WEARING: what the theme asked for.\n"  # even if a model still emits it,
        "MUST APPEAR: bench, locker, tiled_floor, drain, mirror\n"
    )
    assert "wearing" not in plan  # clothes are Wardrobe's, not the planner's
    assert plan["must_appear"][0] == "bench"
    assert plan["place"].startswith("A changing room")


def test_a_wardrobe_costume_tail_is_parsed_and_stripped():
    """The COSTUME block is appended after SCENE; it must be split off before
    parse_table_read or the greedy SCENE capture swallows it."""
    raw = (
        "SAY: こう着せます。\n\n"
        "TAGS: 1girl, red_scarf, cardigan\n\n"
        "SCENE: she stands by the bench in a worn cardigan.\n\n"
        "COSTUME:\n"
        "SILHOUETTE: A-line\nLAYERS: tee, cardigan\n"
        "COLOURWAY: navy 60 / white 30 / red 10\nPATTERN : solid\n"
        "FABRIC: cotton, matte\nCONDITION: worn-in\nHERO: red scarf\n"
    )
    head, cos = chain._strip_costume(raw)
    assert "COSTUME" not in head  # stripped, so SCENE stays clean
    assert cos["silhouette"] == "A-line"
    assert cos["pattern"] == "solid"       # tolerant of "PATTERN :"
    assert cos["hero"] == "red scarf"
    assert set(cos) == {"silhouette", "layers", "colourway", "pattern",
                        "fabric", "condition", "hero"}
    # A turn without a COSTUME block comes back unchanged.
    assert chain._strip_costume("SCENE: just a scene.") == ("SCENE: just a scene.", {})


def test_an_outfit_line_left_out_is_not_a_parse_failure():
    plan = chain.parse_plan(
        "PLACE: A hallway.\nHOUR: Night.\nLIGHT: One bulb.\n"
        "ACTION: Waiting.\nMUST APPEAR: door, shoes\n"
    )
    assert "wearing" not in plan
    assert plan["place"] == "A hallway."


def test_an_empty_expression_does_not_erase_a_face_the_beat_still_carries():
    """表情の欄を作った副作用 —— **顔が絵から消えた。**

    実測（30本パック・2026-08-31）。手帖は
    `beat: sitting at the piano, hands trembling, face on the verge of tears`
    と書いているのに:

        8/28（欄ができる前）「Her face is caught in a moment of quiet
                            breakdown … eyes brimming with tears」crying あり
        8/31（欄ができた後）「Her hands tremble against the keys … She wears a
                            school uniform」  ← 顔が消え、服で埋めている

    契約の「視線は FRAME が現行」は**入れ替え**なので成立する —— FRAME は
    ほぼ常に埋まっている。表情の欄は空のことが多く（欄より前の撮影は全部）、
    入れ替え先が無いので**落とすだけ**になっていた。

    一行足して、その試験は 0/5 → 3/5、崩れた回を除いた合格は 70% → 88%。
    """
    from app.muse import chain

    text = chain.SCRIPTER_WEAVE_SYSTEM
    assert "An empty EXPRESSION does not erase a face" in text
    # 視線の規則（入れ替え）は残す —— あちらは実測で効いている。
    assert "The gaze is FRAME's" in text
    # 顔の規則は、視線の規則より**後**に置く（例外は原則のあとに読ませる）。
    assert text.index("The gaze is FRAME's") < text.index("does not erase a face")

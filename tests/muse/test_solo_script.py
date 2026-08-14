"""主演撮り: Muse CARD + Script compile/weave, struck, still-as-base."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew, identity, notebook, service
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_duet_notebook import NotebookOllama, _current_note, _scripter_block  # noqa: E402
from tests.muse.test_service import FakeDb  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def test_parse_talk_blocks_keeps_card_out_of_say():
    raw = (
        "SAY: 風、ちょっと冷たいね。\n"
        "ASIDE: 帽子、外して正解だったかも。\n"
        "CARD:\n"
        "PLACE: school rooftop\n"
        "HOUR: dusk\n"
        "WEARING: thin cardigan\n"
        "BEAT: sitting on a bench\n"
        "FRAME: eye level\n"
        "PITCH: 麦わら帽子をかぶる | 帽子なし\n"
    )
    blocks = identity.parse_talk_blocks(raw)
    say = identity.sanitize_muse_say(blocks["say"])
    assert "風" in say
    assert "PLACE" not in say
    assert "thin cardigan" in blocks["card"]
    assert "帽子" in blocks["aside"]
    assert notebook.parse_pitch_choices(blocks["pitch"]) == [
        "麦わら帽子をかぶる", "帽子なし",
    ]


def test_parse_muse_card_and_absorb_pose_only():
    nb = notebook.blank()
    notebook.apply_patch(nb, {"beat": "standing", "wearing": "sailor uniform"})
    card = (
        "WEARING: sailor uniform, ribbon\n"
        "BEAT: reaching forward, fingers spread\n"
        "FRAME: eye level\n"
    )
    absorbed = notebook.absorb_muse_card(nb, card)
    assert absorbed["beat"] == "reaching forward, fingers spread"
    assert "reaching" in nb["beat"]
    assert nb["wearing"] == "sailor uniform"
    assert "ribbon" not in nb["wearing"]


def test_absorb_duet_pose_helper_still_folds_beat():
    session = {
        "mode": "duet",
        "notebook": notebook.blank(),
        "character": {"name_ja": "みお"},
        "inputs": {},
        "craft": {},
    }
    notebook.apply_patch(session["notebook"], {"beat": "standing"})
    service._absorb_duet_pose(
        session, "BEAT: leaning on the fence, one knee bent\nWEARING: coat",
    )
    assert "leaning" in session["notebook"]["beat"]
    assert session["craft_dirty"] is True
    assert "leaning" in session["craft"]["pose_intent"]
    assert session["notebook"].get("wearing") != "coat"


def test_duet_talk_does_not_absorb_card_into_notebook():
    import inspect
    src = inspect.getsource(service._duet_talk)
    assert "_absorb_duet_pose" not in src


@pytest.mark.asyncio
async def test_talk_card_standing_does_not_overwrite_sitting(monkeypatch):
    async def fake_talk(*_a, **_kw):
        return (
            "座ってるよ",
            None,
            False,
            "",
            "BEAT: standing by the fence\nWEARING: coat",
            "",
        )

    async def no_board(*_a, **_kw):
        return []

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", no_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    db = FakeDb()
    ollama = NotebookOllama()
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "beat": "sitting on a bench",
        "wearing": "cardigan",
    })
    await service._duet_talk(db, ollama, s, "立って", cfg={"ollama_num_ctx": 16000})
    assert "sitting" in s["notebook"]["beat"]
    assert "standing" not in s["notebook"]["beat"].lower()
    assert "standing" in (s.get("muse_card") or "").lower()


@pytest.mark.asyncio
async def test_scripter_fold_adds_uncontradicted_card_action(monkeypatch):
    """After Muse speaks, a second compile folds CARD hands into beat.

    The showrunner's sit stays; standing on the CARD does not replace it.
    Absorb is still not on the talk path.
    """
    async def fake_talk(*_a, **_kw):
        return (
            "裾、握ってるよ",
            None,
            False,
            "",
            "BEAT: standing by the fence, fingers on the hem\nWEARING: coat",
            "",
        )

    async def no_board(*_a, **_kw):
        return []

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", no_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "座って": _scripter_block(
            intent="shot", scene="rooftop", wearing="cardigan",
            beat="sitting on a bench", frame="eye level",
        ),
        "FOLD:": _scripter_block(
            intent="shot", scene="rooftop", wearing="cardigan",
            beat="sitting on a bench, fingers tightening on the hem",
            frame="eye level",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await service.post_duet_chat(db, ollama, s, "座って")
    beat = (s["notebook"].get("beat") or "").lower()
    assert "sitting" in beat
    assert "hem" in beat
    assert "standing" not in beat
    assert (s["notebook"].get("wearing") or "") == "cardigan"
    assert s.get("scripter_intent") == "shot"
    notes = "\n".join(s.get("notes") or [])
    assert "FOLD:" not in notes
    sources = [e.get("source") for e in (s.get("rewrite_log") or [])]
    assert "scripter_fold" in sources
    fold_prompts = [p for p in ollama.scripter_prompts if "FOLD:" in p]
    assert fold_prompts
    assert "The attached image is the previous take" not in fold_prompts[-1]
    latest = _current_note(fold_prompts[-1]).strip()
    assert latest.startswith("座って")
    assert not latest.upper().startswith("FOLD")


def test_scripter_reads_muse_pose_and_recall():
    text = " ".join(chain.SCRIPTER_SYSTEM.lower().split())
    assert "card beat" in text
    assert "recall" in text
    assert "この間" in chain.SCRIPTER_SYSTEM
    assert "last noun" not in text
    assert "drop her pose" not in text
    assert "never casual" in text
    assert "never paint scene or wearing from say atmosphere" in text
    assert "fold:" in text
    assert "uncontradicted" in text


def test_scripter_fold_note_keeps_showrunner_posture():
    note = " ".join(chain.SCRIPTER_FOLD_NOTE.lower().split())
    assert note.startswith("fold:")
    assert "hands" in note
    assert "do not invent clothes" in note
    assert "do not emit tags" in note


def test_duet_talk_output_answers_nouns_when_asked():
    text = " ".join(crew.DUET_TALK_OUTPUT.lower().split())
    assert "never a change-log" not in text
    assert "change log" not in text or "checklist" in text
    assert "card" in text
    assert "aside" in text
    assert "pitch" in text
    assert "not rewrite the notebook" in text
    assert "shot notebook" in text
    assert "body action" in text
    assert "posture the notebook does not have" not in text
    assert "how you are holding it" not in text
    assert "寄ってる" in crew.DUET_TALK_OUTPUT
    assert "wearing_b" in text
    assert "aside" in text


def test_wearing_tokens_drop_no_hat():
    assert "hat" not in notebook.wearing_tokens("thin cardigan, no hat")
    assert "cardigan" in notebook.wearing_tokens("thin cardigan, no hat")


def test_struck_from_wearing_diff():
    session = {"struck": []}
    notebook.record_struck_from_wearing(
        session, prev_wearing="thin cardigan, straw hat",
        new_wearing="thin cardigan",
    )
    struck = notebook.struck_tokens(session)
    assert "hat" in struck or "straw_hat" in struck


def test_filter_weave_tags_drops_struck_hat():
    tags = notebook.filter_weave_tags(
        "thin_cardigan, straw_hat, knit, fabric_folds, lantern",
        wearing="thin cardigan",
        scene="rooftop at dusk",
        beat="sitting on a bench",
        struck={"hat", "straw_hat"},
    )
    low = tags.lower()
    assert "straw_hat" not in low
    assert "cardigan" in low or "thin_cardigan" in low


def test_split_atmosphere_time_moves_dusk():
    mood, place = notebook.split_atmosphere_time("tender dusk", "rooftop")
    assert "dusk" not in mood.lower()
    assert "dusk" in place.lower()
    assert "tender" in mood.lower()


def test_theme_hidden_once_shot_exists():
    session = {
        "inputs": {"theme": "屋上で麦わら帽子"},
        "notebook": {
            "scene": "park", "wearing": "cardigan", "atmosphere": "",
            "frame": "", "beat": "", "rev": 1,
        },
        "mode": "duet",
        "chat": [],
    }
    assert service._theme_for_models(session) == ""
    empty = {
        "inputs": {"theme": "屋上で麦わら帽子"},
        "notebook": notebook.blank(),
        "mode": "duet",
        "chat": [],
    }
    assert "屋上" in service._theme_for_models(empty)


def test_duet_transcript_is_user_turns_not_message_count():
    chat = []
    for i in range(8):
        chat.append({"role": "user", "text": f"指示{i}"})
        chat.append({"role": "muse", "text": f"SAY{i}", "kind": "craft"})
        chat.append({"role": "muse", "text": f"ASIDE{i}", "kind": "banter"})
    session = {"chat": chat, "mode": "duet"}
    text = service._duet_transcript(session, user_turns=4)
    assert "指示4" in text
    assert "指示0" not in text
    assert "ASIDE7" in text


def test_board_images_take_the_latest(monkeypatch):
    session = {
        "session_id": "s1",
        "board": {
            "pending": False,
            "round": 2,
            "images": [
                {"image_id": "old"},
                {"image_id": "new"},
            ],
        },
    }
    shots = [
        str(i.get("image_id") or "") for i in (session["board"]["images"] or [])
        if isinstance(i, dict) and i.get("image_id")
    ]
    assert shots[-1:] == ["new"]
    assert shots[:1] == ["old"]


def test_how_to_speak_still_is_base():
    session = {
        "inputs": {"theme": "屋上"},
        "chat": [{"role": "user", "text": "帽子外して"}],
        "mode": "duet",
        "notebook": {
            "rev": 1, "wearing": "cardigan", "scene": "rooftop",
        },
        "muse_card": "WEARING: thin cardigan",
    }
    prompt = service._duet_user_prompt(session, "帽子外して", prep=False)
    assert "previous take" in prompt.lower() or "底" in prompt
    assert "THEME" not in prompt
    assert "never a change-log" not in prompt.lower()


def test_weave_prompt_has_no_theme_or_transcript():
    import inspect
    src = inspect.getsource(service.weave_craft_if_needed)
    assert "mode=\"weave\"" in src or "mode='weave'" in src


@pytest.mark.asyncio
async def test_talk_then_compile_uses_card_not_tags():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子外して": _scripter_block(
            intent="shot",
            wearing="thin cardigan",
            scene="rooftop at dusk",
            beat="sitting",
            frame="eye level",
            tags="thin_cardigan, straw_hat, lantern",
            craft_scene="Should be ignored on compile.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["notebook"]["wearing"] = "thin cardigan, straw hat"
    s["notebook"]["scene"] = "rooftop"
    s["notebook"]["rev"] = 1
    await service.post_duet_chat(db, ollama, s, "帽子外して")
    assert "hat" not in notebook.wearing_tokens(s["notebook"]["wearing"])
    assert not (s.get("craft") or {}).get("tags") or "straw_hat" not in (
        s.get("craft") or {}
    ).get("tags", "")
    struck = notebook.struck_tokens(s)
    assert "hat" in struck or "straw_hat" in struck


@pytest.mark.asyncio
async def test_weave_drops_struck_and_skips_theme():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "WEAVE": _scripter_block(
            intent="shot",
            tags="thin_cardigan, straw_hat, lantern, knit, fabric_folds, dusk_sky",
            craft_scene="A thin cardigan in dusk air, fabric folds, long shadows. " * 8,
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["inputs"]["theme"] = "麦わら帽子と提灯の屋上"
    notebook.apply_patch(s["notebook"], {
        "wearing": "thin cardigan",
        "scene": "rooftop at dusk",
        "beat": "sitting on a bench",
        "frame": "eye level",
    })
    s["struck"] = ["hat", "straw_hat"]
    s["craft_dirty"] = True
    s["craft"] = {"prompt": "1girl, thin_cardigan", "tags": "thin_cardigan", "scene": "x"}
    await service.weave_craft_if_needed(db, ollama, s)
    tags = (s.get("craft") or {}).get("tags") or ""
    assert "straw_hat" not in tags.lower()
    notes = "\n".join(ollama.scripter_prompts)
    assert "THEME" not in notes or "麦わら" not in notes
    assert "CONVERSATION SO FAR" not in notes


def test_coerce_plain_phrase_salvages_list_wearing():
    assert notebook.coerce_plain_phrase(["sailor uniform, cardigan"]) == (
        "sailor uniform, cardigan"
    )
    assert notebook.coerce_plain_phrase("['sailor uniform, cardigan']") == (
        "sailor uniform, cardigan"
    )
    assert notebook.coerce_plain_phrase({"place": "rooftop"}) == ""
    nb = notebook.blank()
    notebook.apply_patch(nb, {"wearing": ["sailor uniform", "cardigan"]})
    assert "sailor uniform" in nb["wearing"]
    assert "cardigan" in nb["wearing"]
    notebook.apply_patch(nb, {"vibe": {"mood": "tender"}, "open": "{broken}"})
    assert nb.get("vibe") == ""
    assert "{" not in str(nb.get("open") or "")


def test_wearing_tokens_do_not_mint_hat_cardigan():
    toks = notebook.wearing_tokens("straw hat, cardigan")
    assert "hat" in toks
    assert "cardigan" in toks
    assert "hat_cardigan" not in toks
    assert "straw_hat" in toks


def test_drop_leftover_garments_and_crops():
    tags = notebook.drop_garments_not_in_wearing(
        "sailor_collar, straw_hat, thin_cardigan, knit, fabric_folds",
        wearing="sailor uniform, cardigan",
    )
    low = tags.lower()
    assert "straw_hat" not in low
    assert "cardigan" in low
    assert "knit" in low
    zoom = notebook.drop_crops_not_in_frame(
        "upper_body, close_up, wide_shot, full_body, knit",
        frame="close, upper body",
    )
    zlow = zoom.lower().replace(" ", "_")
    assert "wide_shot" not in zlow
    assert "full_body" not in zlow
    wide = notebook.drop_crops_not_in_frame(
        "wide_shot, full_body, close_up, face_focus, knit",
        frame="wide full body",
    )
    wlow = wide.lower().replace(" ", "_")
    assert "close_up" not in wlow
    assert "wide_shot" in wlow or "full_body" in wlow


@pytest.mark.asyncio
async def test_empty_shot_patch_still_verifies(monkeypatch):
    """intent=shot with no picture fields must not skip VERIFY."""
    async def fake_talk(*_a, **_kw):
        return ("了解", None, False, "", "BEAT: standing\nWEARING: coat", "")

    async def no_board(*_a, **_kw):
        return []

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", no_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    monkeypatch.setattr(service, "_fold_muse_after_talk", noop)

    class EmptyShotThenVerify(NotebookOllama):
        def __init__(self):
            super().__init__(scripts={})
            self._n = 0

        def generate_text_stream(self, prompt, **kw):
            self.calls.append({**kw, "prompt": prompt})
            system = str(kw.get("system") or "")
            text = "SAY: うん。"
            if "studio scripter" in system or "shot notebook" in system:
                self.scripter_prompts.append(str(prompt))
                self._n += 1
                if self._n == 1:
                    text = _scripter_block(intent="shot")
                else:
                    assert "VERIFY" in str(prompt)
                    text = _scripter_block(
                        intent="shot",
                        scene="night classroom by the window",
                        wearing="sailor uniform, cardigan",
                        beat="standing, holding the hem",
                        frame="close, upper body",
                    )
            async def _stream():
                yield {"type": "token", "text": text}
            return _stream()

    db = FakeDb()
    ollama = EmptyShotThenVerify()
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "scene": "school rooftop at dusk",
        "wearing": "sailor uniform, straw hat",
        "beat": "sitting on a bench",
        "frame": "wide full body",
    })
    await service.post_duet_chat(db, ollama, s, "カーディガン羽織って立って、寄って")
    nb = s["notebook"]
    assert "classroom" in (nb.get("scene") or "").lower() or "night" in (
        nb.get("scene") or ""
    ).lower()
    assert "cardigan" in (nb.get("wearing") or "").lower()
    assert "standing" in (nb.get("beat") or "").lower()
    assert "hat" not in notebook.wearing_tokens(nb.get("wearing") or "")
    assert any("VERIFY" in p for p in ollama.scripter_prompts)


@pytest.mark.asyncio
async def test_fold_cannot_rewrite_wearing_or_frame(monkeypatch):
    async def fake_talk(*_a, **_kw):
        return (
            "裾、握ってるよ",
            None,
            False,
            "",
            "BEAT: standing, fingers on the hem\nWEARING: coat\nFRAME: close up",
            "",
        )

    async def no_board(*_a, **_kw):
        return []

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", no_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "座って": _scripter_block(
            intent="shot", scene="rooftop", wearing="cardigan",
            beat="sitting on a bench", frame="wide full body",
        ),
        "FOLD:": _scripter_block(
            intent="shot", scene="classroom", wearing="coat",
            beat="sitting on a bench, fingers on the hem",
            frame="close up",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await service.post_duet_chat(db, ollama, s, "座って")
    nb = s["notebook"]
    assert (nb.get("wearing") or "") == "cardigan"
    assert "coat" not in (nb.get("wearing") or "").lower()
    assert "classroom" not in (nb.get("scene") or "").lower()
    assert "wide" in (nb.get("frame") or "").lower()
    assert "hem" in (nb.get("beat") or "").lower()
    assert "sitting" in (nb.get("beat") or "").lower()


@pytest.mark.asyncio
async def test_weave_drops_old_place_hour_pose_and_crop():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "WEAVE": _scripter_block(
            intent="shot",
            tags=(
                "rooftop, dusk, sitting, straw_hat, cardigan, "
                "wide_shot, full_body, close_up, night_classroom, standing, knit"
            ),
            craft_scene="Night classroom, standing in a cardigan. " * 8,
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "wearing": "sailor uniform, cardigan",
        "scene": "night classroom by the window",
        "beat": "standing, holding the hem",
        "frame": "close, upper body",
    })
    s["struck"] = ["hat", "straw_hat", "rooftop", "dusk", "sitting", "wide"]
    s["craft_dirty"] = True
    s["craft"] = {"prompt": "1girl", "tags": "thin_cardigan", "scene": "x"}
    await service.weave_craft_if_needed(db, ollama, s)
    tags = ((s.get("craft") or {}).get("tags") or "").lower().replace(" ", "_")
    assert "straw_hat" not in tags
    assert "rooftop" not in tags
    assert "wide_shot" not in tags
    assert "full_body" not in tags
    assert "close_up" in tags or "upper" in tags or "knit" in tags


@pytest.mark.asyncio
async def test_failed_weave_still_scrubs_stale_tags():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "WEAVE": _scripter_block(intent="shot", tags="", craft_scene=""),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "wearing": "sailor uniform, cardigan",
        "scene": "night classroom",
        "beat": "standing",
        "frame": "close, upper body",
    })
    s["struck"] = ["hat", "straw_hat", "sitting", "wide", "wide_shot"]
    s["craft_dirty"] = True
    s["craft"] = {
        "prompt": "1girl, straw_hat, sitting, wide_shot, cardigan",
        "tags": "straw_hat, sitting, wide_shot, cardigan, knit",
        "scene": "old rooftop prose",
    }
    await service.weave_craft_if_needed(db, ollama, s)
    tags = ((s.get("craft") or {}).get("tags") or "").lower().replace(" ", "_")
    prompt = ((s.get("craft") or {}).get("prompt") or "").lower().replace(" ", "_")
    assert "straw_hat" not in tags
    assert "wide_shot" not in tags
    assert "wide_shot" not in prompt
    assert "cardigan" in tags or "knit" in tags


def test_shot_framing_wins_over_panel_dropdown():
    session = {
        "inputs": {"framing": "full_body"},
        "notebook": {
            "frame": "close, upper body",
            "rev": 1,
        },
        "mode": "duet",
        "craft": {"tags": "cardigan, wide_shot, close_up", "scene": "classroom"},
        "character": {},
    }
    assert service._shot_framing(session) == "upper_body"
    service._reassemble(session)
    prompt = (session["craft"].get("prompt") or "").lower().replace(" ", "_")
    assert "wide_shot" not in prompt
    assert "close_up" in prompt or "upper_body" in prompt


def test_scripter_forbids_empty_shot_and_dual_crop():
    text = " ".join(chain.SCRIPTER_SYSTEM.lower().split())
    assert "empty shot" in text or "empty shot/mixed" in text
    assert "wide_shot" in text and "close_up" in text
    weave = " ".join(chain.SCRIPTER_WEAVE_SYSTEM.lower().split())
    assert "wide_shot" in weave
    fold = " ".join(chain.SCRIPTER_FOLD_NOTE.lower().split())
    assert "latest line" in fold
    assert "do not patch scene" in fold


@pytest.mark.asyncio
async def test_shot_that_restates_scene_still_verifies_clothes(monkeypatch):
    """intent=shot that only repeats scene must still VERIFY a clothes ask."""
    async def fake_talk(*_a, **_kw):
        return ("了解", None, False, "", "BEAT: sitting\nWEARING: cardigan", "")

    async def no_board(*_a, **_kw):
        return []

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", no_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    monkeypatch.setattr(service, "_fold_muse_after_talk", noop)

    class RestateThenVerify(NotebookOllama):
        def __init__(self):
            super().__init__(scripts={})
            self._n = 0

        def generate_text_stream(self, prompt, **kw):
            self.calls.append({**kw, "prompt": prompt})
            system = str(kw.get("system") or "")
            text = "SAY: うん。"
            if "studio scripter" in system or "shot notebook" in system:
                self.scripter_prompts.append(str(prompt))
                self._n += 1
                if self._n == 1:
                    text = _scripter_block(
                        intent="shot",
                        scene="rooftop at dusk",
                        wearing="sailor uniform, straw hat",
                        beat="sitting on a bench",
                        frame="wide shot",
                    )
                else:
                    assert "VERIFY" in str(prompt)
                    text = _scripter_block(
                        intent="shot",
                        scene="rooftop at dusk",
                        wearing="sailor uniform, straw hat, cardigan",
                        beat="sitting on a bench",
                        frame="wide shot",
                    )
            async def _stream():
                yield {"type": "token", "text": text}
            return _stream()

    db = FakeDb()
    ollama = RestateThenVerify()
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "scene": "rooftop at dusk",
        "wearing": "sailor uniform, straw hat",
        "beat": "sitting on a bench",
        "frame": "wide shot",
    })
    await service.post_duet_chat(db, ollama, s, "カーディガン羽織って")
    assert "cardigan" in (s["notebook"].get("wearing") or "").lower()
    assert any("VERIFY" in p for p in ollama.scripter_prompts)


@pytest.mark.asyncio
async def test_shot_that_only_moves_frame_still_verifies_clothes(monkeypatch):
    """A crop rewrite must not skip VERIFY when clothes also changed this line."""
    async def fake_talk(*_a, **_kw):
        return ("了解", None, False, "", "FRAME: wide\nWEARING: sailor", "")

    async def no_board(*_a, **_kw):
        return []

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", no_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    monkeypatch.setattr(service, "_fold_muse_after_talk", noop)

    class FrameThenVerify(NotebookOllama):
        def __init__(self):
            super().__init__(scripts={})
            self._n = 0

        def generate_text_stream(self, prompt, **kw):
            self.calls.append({**kw, "prompt": prompt})
            system = str(kw.get("system") or "")
            text = "SAY: うん。"
            if "studio scripter" in system or "shot notebook" in system:
                self.scripter_prompts.append(str(prompt))
                self._n += 1
                if self._n == 1:
                    text = _scripter_block(
                        intent="shot",
                        scene="night classroom",
                        wearing="sailor uniform, cardigan",
                        beat="standing",
                        frame="wide full body",
                    )
                else:
                    assert "VERIFY" in str(prompt)
                    text = _scripter_block(
                        intent="shot",
                        scene="night classroom",
                        wearing="sailor uniform",
                        beat="standing",
                        frame="wide full body",
                    )
            async def _stream():
                yield {"type": "token", "text": text}
            return _stream()

    db = FakeDb()
    ollama = FrameThenVerify()
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "scene": "night classroom",
        "wearing": "sailor uniform, cardigan",
        "beat": "standing",
        "frame": "close, upper body",
    })
    await service.post_duet_chat(db, ollama, s, "カーディガン脱いで。引いて全身に戻して")
    assert "cardigan" not in (s["notebook"].get("wearing") or "").lower()
    assert any("VERIFY" in p for p in ollama.scripter_prompts)


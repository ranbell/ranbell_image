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
    # PITCH is still parsed as words she said. It no longer becomes chips:
    # `open_choices` fed off `open`, which never held a proposal in 390
    # live sessions and is gone.
    assert "麦わら帽子をかぶる" in blocks["pitch"]


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
    assert "posture stem" in text
    assert "sitting" in text and "standing" in text
    assert "turning around" in text
    assert "facing camera" in text
    assert "寄って" in chain.SCRIPTER_SYSTEM
    assert "引いて" in chain.SCRIPTER_SYSTEM


def test_scripter_fold_note_keeps_showrunner_posture():
    note = " ".join(chain.SCRIPTER_FOLD_NOTE.lower().split())
    assert note.startswith("fold:")
    assert "hands" in note
    assert "do not invent clothes" in note
    assert "do not emit tags" in note
    assert "sitting into standing" in note
    assert "facing camera" in note
    assert "stem already in notebook now" in note


def test_scripter_verify_note_keeps_posture_stem():
    note = " ".join(chain.SCRIPTER_VERIFY_NOTE.lower().split())
    assert note.startswith("verify:")
    assert "sitting" in note
    assert "turning around is not sitting" in note
    assert "sit/stand/kneel/crouch stem" in note
    assert "facing camera" in note
    assert "invent standing" in note


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
            wearing_drop="straw hat",
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
                    # 2回目は note を載せない（測って外した）。この回であることは
                    # 呼び出しの順番で分かる。
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
    # 2回目が走ったこと自体を見る。VERIFY note は測って外した
    # （note あり 20/20 / note なし 20/20・4ケース × 5回）。
    assert len(ollama.scripter_prompts) >= 2


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
async def test_compile_does_not_attach_board_still(monkeypatch):
    """After a take, compile/VERIFY must not reread the JPEG — the VLM copies it."""
    seen: list = []

    async def fake_talk(*_a, **_kw):
        return ("了解", None, False, "", "BEAT: standing\nWEARING: knit", "")

    async def poison_board(*_a, **_kw):
        return [b"fake-jpeg"]

    async def noop(*_a, **_kw):
        return None

    real = chain.run_scripter

    async def capture(*a, **kw):
        seen.append(kw.get("images"))
        return await real(*a, **kw)

    monkeypatch.setattr(chain, "run_duet_talk", fake_talk)
    monkeypatch.setattr(service, "board_images", poison_board)
    monkeypatch.setattr(service, "_after_actress_spoke", noop)
    monkeypatch.setattr(service, "_fold_muse_after_talk", noop)
    monkeypatch.setattr(chain, "run_scripter", capture)

    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "ニット": _scripter_block(
            intent="shot", scene="night street", wearing="knit",
            beat="standing", frame="close up",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["board"] = {"images": [{"image_id": "take1"}], "pending": False}
    notebook.apply_patch(s["notebook"], {
        "scene": "night street", "wearing": "coat",
        "beat": "standing", "frame": "close up",
    })
    await service.post_duet_chat(db, ollama, s, "コート脱いでニットだけ")
    assert seen, "scripter should have run"
    assert all(not imgs for imgs in seen)
    assert "knit" in (s["notebook"].get("wearing") or "").lower()
    assert "coat" not in notebook.wearing_tokens(s["notebook"].get("wearing") or "")


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
                    # 2回目は note を載せない（測って外した）。この回であることは
                    # 呼び出しの順番で分かる。
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
    # 2回目が走ったこと自体を見る。VERIFY note は測って外した
    # （note あり 20/20 / note なし 20/20・4ケース × 5回）。
    assert len(ollama.scripter_prompts) >= 2


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
                    # 2回目は note を載せない（測って外した）。この回であることは
                    # 呼び出しの順番で分かる。
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
    # 2回目が走ったこと自体を見る。VERIFY note は測って外した
    # （note あり 20/20 / note なし 20/20・4ケース × 5回）。
    assert len(ollama.scripter_prompts) >= 2



# ── 欄の契約は一つ ───────────────────────────────────────────────────────

def test_everyone_who_touches_the_notebook_reads_the_same_contract():
    """One definition, handed to every seat that reads or writes the notebook.

    Measured 2026-08-19: the definition of `frame` existed in four places and
    disagreed. compile — the one that writes the notebook every turn — said
    only "ONE crop" and never mentioned the gaze at all, while the accurate
    version ("Crop plus gaze. Not where you are looking; that is the frame.")
    was reachable from exactly one call site, the restate turn.

    So the showrunner's 「カメラ見て」 landed in `frame`, the old gaze stayed in
    `beat`, and weave — never told which field owns it — took the concrete one.
    He said it three times, the last time as a raw danbooru tag, and the board
    did not move. Nobody was wrong; nothing agreed.
    """
    marker = "WHAT EACH PART OF THE NOTEBOOK IS"
    for name in (
        "SCRIPTER_SYSTEM",          # 旧・比較用に残してある
        "STILL_READ_SYSTEM",        # writes it from a photo
        "NOTEBOOK_REVIEW_SYSTEM",   # she checks it
    ):
        assert marker in getattr(chain, name), name
    # compile が実際に読む契約は `SCRIPTER_BLOCKS` の組み立てで、共通ブロック
    # の見出しは持たない。持っているべきは**中身**のほう。
    built = chain.build_scripter_system()
    for field in ("ATMOSPHERE", "SCENE", "LIGHT", "FRAME", "WEARING", "BEAT"):
        assert field in built, field
    assert "not where she is looking" in built.lower()

    # weave には渡さない。**読む側には効かなかった。** weave パック
    # (6試験 x 5回) で測ると、契約を抜いたほうが良い:
    #
    #     契約あり 5,228字  28/30   w3 の空応答 3/15
    #     契約抜き 4,157字  30/30   w3 の空応答 0/15
    #
    # 欄が何であるかは書く側の問題で、読む側は値さえ読めればよい。1,071字を
    # 毎レンダー載せたうえ、たまに応答ごと潰していた。
    assert marker not in chain.SCRIPTER_WEAVE_SYSTEM


def test_only_frame_owns_the_gaze():
    """Two fields claiming the gaze is what froze the board — hold the split."""
    assert "NOT where she is looking: that is the frame." in (
        notebook.FIELD_CONTRACTS["beat"]
    )
    assert "where her eyes are pointed" in notebook.FIELD_CONTRACTS["frame"]

    # And the split survives into the prompts, in both voices.
    third = notebook.contracts_block()
    second = notebook.contracts_block(second_person=True)
    assert "NOT where she is looking" in third
    assert "NOT where you are looking" in second
    assert "where your eyes are pointed" in second


def test_the_restate_shape_is_not_a_second_copy_of_the_contract():
    """crew's restate must quote the one source, not carry its own wording.

    It used to hold its own English text in `_RESTATE_FIELDS`. That copy was
    the accurate one, which is exactly why the drift went unnoticed — the good
    wording sat where almost nothing could read it.
    """
    for field, phrase in (
        ("frame", "where your eyes are pointed"),
        ("beat", "NOT where you are looking: that is the frame."),
        ("wearing", "A held prop is not worn; that belongs in beat."),
    ):
        # The value line, not the WHY line that now follows it.
        prompt = crew.restate_output(field)
        assert phrase in prompt, field
        # …and the format-only tail is still appended, not lost.
    # 上限は外した。実撮影で「衣装はそのままで」と言われたターンの言い直しが
    # 数を守るために `hair ornament` を落とした。書式の指示は残っている。
    w = crew.restate_output("wearing")
    assert "AT MOST" not in w
    assert "a garment left out is a garment she loses" in w
    assert "underscores" in w


# ── なぜその欄をそう書いたか ──────────────────────────────────────────────

def test_the_scripter_is_asked_to_say_why_it_wrote_each_field():
    text = chain.SCRIPTER_SYSTEM
    assert "SAY WHY, FIELD BY FIELD" in text
    assert "WHY_FRAME" in text
    # The reason has to point at what was said, not read the value back.
    assert "Point at what was said" in text


def test_why_is_never_a_slot_in_the_json_schema():
    """A reason slot in the schema eats the job it was supposed to annotate.

    Measured 8/19 against the live model, three cases x three runs each:

        why in the schema   wrote a field 0/9
        why out of it       wrote a field 9/9

    The model answered `{"intent":"shot","why":{"beat":"…set posture stem to
    sitting"}}` — describing the edit instead of making it. Strengthening the
    wording ("the value IS the work") did not move it: still 0/9. The slot
    itself is the cause, so the reason is collected from labelled `WHY_*`
    lines, which sit in the same list as the values and cannot replace them.
    """
    assert "why" not in notebook.SCRIPTER_FORMAT_SCHEMA["properties"]
    # …but the parser still reads one when the model offers it.
    parsed = notebook.parse_scripter(
        "INTENT: shot\nBEAT: sitting\nWHY_BEAT: 座ってと言われた"
    )
    assert parsed["patch"]["beat"] == "sitting"
    assert parsed["why"]["beat"] == "座ってと言われた"


def test_a_reason_is_parsed_from_json_and_from_labels_alike():
    """Both paths, because the labelled one runs on every turn with an image."""
    labelled = notebook.parse_scripter(
        "INTENT: shot\n"
        "FRAME: medium shot, looking into the lens\n"
        "WHY_FRAME: 『カメラ目線で』と言われたので視線を frame に置いた\n"
    )
    assert labelled["patch"]["frame"].startswith("medium shot")
    assert "カメラ目線" in labelled["why"]["frame"]

    as_json = notebook.parse_scripter(
        '{"intent":"shot","frame":"medium shot, looking into the lens",'
        '"why":{"frame":"asked for eye contact"}}'
    )
    assert as_json["why"]["frame"] == "asked for eye contact"


def test_a_reason_for_a_field_nobody_wrote_is_dropped():
    """A decision that never landed must not show up as if it had."""
    assert notebook.clean_why({"frame": "x", "beat": "y"}, {"frame": "v"}) == {
        "frame": "x",
    }

    session: dict = {}
    entry = notebook.record_rewrite(
        session, "scripter",
        before={"frame": "a", "beat": "b"},
        after={"frame": "z", "beat": "b"},
        why={"frame": "moved the gaze", "beat": "never landed"},
    )
    assert entry["changed"]["frame"]["why"] == "moved the gaze"
    assert "beat" not in entry["changed"]


def test_the_reason_is_one_line_not_a_second_notebook():
    long = "あ" * 400
    assert len(notebook.clean_why({"beat": long}, {"beat": "v"})["beat"]) == (
        notebook.WHY_MAX_CHARS
    )
    # Newlines collapse — the panel renders one line per field.
    assert notebook.clean_why({"beat": "one\ntwo"}, {"beat": "v"})["beat"] == "one two"


# ── compile が実際に使う契約 ──────────────────────────────────────────────

def test_compile_runs_on_the_built_contract_not_the_old_one():
    """`SCRIPTER_SYSTEM` は残してあるが、compile はもう読んでいない。

    標準30試験パック（30本 × 5回・言い直し込み）で同条件比較:

        SCRIPTER_SYSTEM  8,281字   52.7%（1回判定）
        積み上げ          2,327字   96.0%（詰まり 4.0%）

    区分で見ると差の出方がはっきりする。動かさない仕事はどちらも 100% で、
    差がつくのは動かす側 — 姿勢 16%→100%、服 24%→88%。禁止33／肯定6 の
    指示は「動かさない」を完璧にして「動かす」を壊していた。
    """
    import inspect
    src = inspect.getsource(chain.run_scripter)
    assert "build_scripter_system()" in src
    assert "else SCRIPTER_SYSTEM" not in src
    # 旧版は捨てない。戻せることがこの入れ替えの前提。
    assert len(chain.SCRIPTER_SYSTEM) > 8000


def test_the_built_contract_says_what_each_field_is_and_forbids_almost_nothing():
    built = chain.build_scripter_system()
    assert len(built) < 3000

    # 外枠 — 欄が何であるか。
    for phrase in ("ATMOSPHERE", "SCENE", "LIGHT", "FRAME", "WEARING", "BEAT"):
        assert phrase in built
    assert "not where she is looking" in built.lower()   # 視線は FRAME
    assert "posture" in built.lower()                     # beat は姿勢を言う

    # 中身は任せる。禁止で埋めない — それが 8,281字が負けた理由。
    #
    # 数えるのは**命令としての禁止**だけ。`the notebook never had` のような
    # 説明の中の never まで数えていて、境界を一つ足しただけで落ちた。
    # **語の出現ではなく、その語が何をしているかで数える。** 今日ここで
    # 6回踏んだのと同じ形の失敗だったので、判定のほうを直した。
    import re
    lowered = built.lower()
    bans = len(re.findall(r"(?m)(?:^|[.;]\s+|\*\*)(?:do not|never|must not)\s+\w", lowered))
    assert bans <= 5, f"命令としての禁止が {bans} 個。旧版は 33 個で 52.7% だった"


def test_a_proposal_has_somewhere_to_go():
    """行き場が無いと、思いついたものを欄に押し込む。

    t21「おいしそう？」で、ノートに食べ物が無いのに 5/5 で手にパンを持たせた。
    禁止で塞ぐのではなく `PROPOSE:` を作ったら 5/5 で通るようになった。
    """
    assert "PROPOSE" in chain.build_scripter_system()
    parsed = notebook.parse_scripter(
        "INTENT: casual\nPROPOSE: something the room has not decided yet"
    )
    assert parsed["patch"] == {}, "提案がノートに入ってはいけない"
    assert parsed["propose"].startswith("something")


def test_intent_is_not_bought_at_the_notebook_s_expense():
    """intent の説明はブロックとして持つが、既定には入れない。

    足すと intent は 68%→93% になるが、ノートが 96.0%→86.7% に落ちる
    （服の区分は 88%→48%、上がった試験はゼロ）。intent は別の道で採る —
    `classify_intent` の clerk と、patch が欄を動かしたかどうか（実測 92%）。
    """
    assert "intent" in chain.SCRIPTER_BLOCKS          # 残してある
    assert "intent" not in chain.SCRIPTER_BUILD_DEFAULT
    assert "shot" in chain.CLASSIFY_INTENT_SYSTEM     # clerk が持っている


def test_a_proposal_goes_to_her_and_never_to_the_notebook():
    """The scripter may notice; she decides whether it is worth saying.

    Measured on t21「おいしそう？」: with nowhere to put it, the scripter gave
    her a pastry nobody had asked for, 5/5. With `PROPOSE:` it stopped writing
    into the field — but a proposal that only reaches a log is not a proposal.
    It goes to the one person in the room who can judge whether it belongs.
    """
    session = {
        "propose": "a lamp on the table nobody has asked for",
        "inputs": {"locale": "ja"}, "notebook": {}, "chat": [],
    }
    prompt = service._duet_user_prompt(session, "いいね", prep=False, intent="casual")
    assert "THE STUDIO NOTICED" in prompt
    assert "a lamp on the table" in prompt
    assert "Yours to raise or let go" in prompt

    # And nothing about it reaches the shot itself.
    parsed = notebook.parse_scripter("INTENT: casual\nPROPOSE: a lamp on the table")
    assert parsed["patch"] == {}


def test_a_quiet_turn_says_nothing_about_proposals():
    session = {"inputs": {"locale": "ja"}, "notebook": {}, "chat": []}
    prompt = service._duet_user_prompt(session, "いいね", prep=False, intent="casual")
    assert "THE STUDIO NOTICED" not in prompt


def test_the_verify_pass_carries_no_note_and_the_fold_pass_does():
    """測って決めた非対称。どちらも4ケース × 5回。

        VERIFY   note あり 20/20   最小 18/20   note なし 20/20
        FOLD     note あり 19/20   最小 18/20   note なし 15/20

    VERIFY は同じ一言をもう一度読むだけなので、説明が要らない。中途半端に
    「もう一度読め」と言う条件が一番悪かった。FOLD は彼女のカードという別の
    材料を扱う回なので、何をする回なのかを言わないと折り込み自体ができない
    （手を足すのに 3/5 失敗した）。

    `SCRIPTER_VERIFY_NOTE` は消していない。要ると分かれば戻すだけ。
    """
    import inspect
    src = inspect.getsource(service._call_duet_scripter)
    assert "SCRIPTER_FOLD_NOTE if fold" in src
    assert "SCRIPTER_VERIFY_NOTE" not in src.split("directive=")[1][:400]
    assert len(chain.SCRIPTER_VERIFY_NOTE) > 900   # 残してある


def test_pressing_render_again_on_an_unchanged_script_rerolls_the_seed():
    """台本が動いていないのに同じ絵を返しても、誰の役にも立たない。

    シードを撮影の間ずっと保持するのは「二つのテイクの差が言葉だけになる」
    ため（`session_seed` の由来）。だが総監督が実撮影で踏んだとおり、**台本
    が一字も動いていないときは再撮影そのものができなくなる**。

    判定は台本の一致だけで足りる。言い直した結果を見たいのか、同じ画を撮り
    直したいのかは、台本が動いたかどうかに出る。
    """
    import inspect
    src = inspect.getsource(service.request_board)
    assert 'str(prev.get("prompt") or "") == prompt' in src
    assert 'session["seed"] = 0' in src
    # 引き直しの条件は「前の一枚が実際に出ている」こと。取り消した回は数えない。
    assert 'prev.get("images")' in src


def test_a_solo_shoot_has_no_partner_fields_to_write_into():
    """相手役の欄が空いていると、彼女の服がそこに入って消える。

    実測（「カーディガン羽織って。」・ソロ・10回）:

        wearing に入った          6
        wearing_b に入って消えた   2   ← 服が着られないまま次のターンへ
        出力が崩れた / 空          2

    `guard_partner_patch` は書かれた**後**に落とすので、中身は失われる。
    契約は既に「女優は一人」と言っているが、それでは止まらなかった。
    **欄そのものを渡さない。** 無い鍵には書けない。
    """
    solo = notebook.scripter_format_schema(False)["properties"]
    duo = notebook.scripter_format_schema(True)["properties"]
    for key in ("wearing_b", "beat_b"):
        assert key not in solo, key
        assert key in duo, key
    # 本人の欄はどちらにもある。
    for key in ("wearing", "beat", "frame", "scene"):
        assert key in solo and key in duo, key

    import inspect
    src = inspect.getsource(chain.run_scripter)
    assert "scripter_format_schema(partner)" in src
    assert "fmt=notebook_mod.SCRIPTER_FORMAT_SCHEMA" not in src


def test_the_rewrite_log_keeps_a_whole_shoot():
    """12 だと実撮影の前半が消える。分析に使う記録は撮影1本ぶん残す。

    コミケの回（2026-08-20）は監督の発言が21ターンあったのに、記録は直近12件
    だけで、「場所がいつ入ったか」を追えなかった。言い直しと fold を含めると
    1撮影で 50 件前後になる。
    """
    assert notebook.REWRITE_LOG_MAX >= 50

    session: dict = {}
    for i in range(80):
        notebook.record_rewrite(
            session, "scripter",
            before={"beat": f"pose {i}"}, after={"beat": f"pose {i + 1}"})
    log = session["rewrite_log"]
    assert len(log) == notebook.REWRITE_LOG_MAX
    # 古いほうから捨てる。最後の一件は最新であること。
    assert log[-1]["changed"]["beat"]["after"] == "pose 80"


def test_the_notebook_has_somewhere_for_what_is_behind_her():
    """BG —— 彼女以外に画面に写っているもの。無いと絵から消える。

    実撮影（コミケ・2026-08-20）で監督は場所を4回、背景を3回頼んだのに、
    撮影3本のうち2本に建物も人混みも入らなかった。6欄のどこにも置き場が
    無かったから。

    名前は測って決めた（7本 × 10回）。`set` `backdrop` `scenery` はどれも
    届かず、現場の略語 `BG` だけが届いた（44% → 68%）。`backdrop` は
    このライブラリに1枚も無い語で、`set_dressing` も `extras` も `mob` も
    0 枚。**現場で実際に使われている語だけが通った。**
    """
    assert "bg" in notebook.SHOT_KEYS
    assert "bg" in notebook.blank()
    assert "BG:" in notebook.render(notebook.blank())
    assert "bg" in notebook.scripter_format_schema(False)["properties"]
    assert notebook._FIELD_RE.match("BG: a crowd of cosplayers")
    assert notebook.parse_scripter(
        '{"intent":"shot","bg":"a crowd"}')["patch"] == {"bg": "a crowd"}

    contract = notebook.FIELD_CONTRACTS["bg"]
    assert "background actors" in contract      # 人はエキストラ
    assert "set dressing" in contract           # 物は飾り込み
    # ボケはカメラの話。ここに入れない（現場では Shallow DoF）。
    assert "depth of field" in contract and "FRAME" in contract


def test_the_label_table_comes_from_shot_keys():
    """欄名の出典は一つ。書き忘れると値が黙って捨てられる。

    `wearing_drop` が実際にそうなっていた —— `_FIELD_RE` と JSON schema には
    あるのに `key_map` に無くて、ラベル形式で答えたターン（画像が付く回と、
    JSON パースが落ちた回の全部）で脱衣が床に落ちていた。
    """
    parsed = notebook.parse_scripter(
        "INTENT: shot\nBG: a crowd of cosplayers\nWEARING_DROP: coat"
    )
    assert parsed["patch"]["bg"] == "a crowd of cosplayers"
    assert parsed["patch"]["wearing_drop"] == "coat"


def test_the_angle_word_names_the_camera_not_the_gaze():
    """`up` / `down` を説明に入れると、その語が取り違えの材料になる。

    実撮影（ブランコ・2026-08-21）で、監督の「カメラを少し上から」を compile は
    `high-angle` と正しく書き、直後の言い直しが `low-angle` に化けさせた。
    残っていた理由の欄:

      「カメラが高い位置にあるんですね」という理解と…指示通りの構図
      （被写体を見上げるアングル）に彼女が合わせようとしていることを読み取った

    **カメラの位置は分かっていて、語だけが逆。** 監督は次のターンで
    「from above っていうんだよ。ローアングルじゃないよ」と訂正している。

    直すのに3つ測った（言い直し・実会話つき・各8回）:

        「カメラの高さと視線は逆になる」と説明     0/8
        カメラの位置だけに絞る                    7/8
        位置だけ＋「彼女は逆を向くことが多い」     0/8

    説明に `up` / `down` が一語でも入ると総崩れする。**取り違えを説明しようと
    すると、説明に使う語が取り違えの材料になる。**
    """
    frame = notebook.FIELD_CONTRACTS["frame"]
    assert "where the camera stands" in frame
    assert "high_angle" in frame and "low_angle" in frame

    # 角度の説明に視線の向きを持ち込まない。ここが崩れると 0/8 に戻る。
    angle_line = [l for l in frame.splitlines() if "angle word" in l][0]
    for word in (" up ", " down ", "UP", "DOWN"):
        assert word not in angle_line, f"角度の説明に {word!r} を入れない"


def test_the_previous_session_does_not_outweigh_today():
    """画が動いたターンで、前回の撮影ログを積まない。

    実撮影（2026-08-21）で、彼女は3ターン続けて過去から答えた ——
    「あの時の、大きな会場の片隅で」。立っていたのは公園だった。

    彼女に渡っていた文脈を測ると:

        前回の撮影ログ  4,420字
        今日の会話      4,533字
        前回の日記      2,368字
        ────────────────────
        過去 7,678字 ＞ 今日 4,533字

    `_attach_recall_context` は `intent == "recall"` のときに走る。そして
    intent はほぼ全ターン `recall` で返ってくる（契約に説明が無いため）。
    patch からの引き上げは**その40行あと**にあり、間に合っていなかった。

    ## そのあと分けたもの

    同じ引き金に二つの重さが乗っていて、最初の修正は両方を一緒に止めていた:

        彼が訊いた日記の頁    900字。**訊かれたことへの答えそのもの**
        前回の会話ログ      4,000字。今日を溺れさせていたのはこちら

    実チェーンで測ると、頁を落とした recall ターン7件は**全て compile も
    clerk も recall と言っていた**のに、余計な patch に上書きされていた:

        「黄色いワンピース着てた日のこと、覚えてる？」
            compile=recall clerk=recall patch=['wearing'] → shot

    ## どちらの読み手が頁を決めるか

    compile ではない。**ほぼ全ターン `recall` を返す**ので、それで絞るのは
    絞らないのと同じ。実チェーンで、画が動いた9件が全て `recall` だった。

    clerk は監督の一言だけを読む。この読み分けができるのは clerk だけ。
    10本 × 3回:

        過去を訊いた一言   recall 21/21
        画を動かす一言     無駄引き 0/9

    頁は clerk に、重い前回ログは引き上げ後の `intent` に付ける。
    """
    import inspect
    src = inspect.getsource(service._run_duet_scripter)
    derive = src.index("patch raised intent")
    recall = src.index("if asked_back:")
    assert derive < recall, "引き上げは recall の前で走らないと間に合わない"

    # 頁の引き金は clerk。compile はほぼ全ターン recall なので絞れない
    assert 'clerk_kind == "recall"' in src, "頁の引き金は clerk"
    # 重いほうだけが引き上げの影響を受ける
    assert 'with_prior=(intent == "recall")' in src, (
        "前回ログは引き上げ後の intent に付ける"
    )

    # 記録（standing note）には引き上げ前の判定を使う。何が常設の指示になるかは
    # 部屋の読みであって、たまたまどの欄が動いたかではない。
    assert "said_intent" in src
    note = src.index('session.setdefault("notes", []).append(text)')
    assert 'if said_intent in ("shot", "mixed")' in src[:note]

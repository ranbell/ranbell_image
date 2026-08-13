"""Notebook + scripter path for 主演撮り — live craft, no prep gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, service, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def _scripter_block(
    *, intent="shot", atmosphere="", scene="", frame="", wearing="", beat="",
    vibe="", open_="", tags="", craft_scene="", clear_open="no",
):
    return "\n".join([
        f"INTENT: {intent}",
        f"ATMOSPHERE: {atmosphere}" if atmosphere else "",
        f"SCENE: {scene}" if scene else "",
        f"FRAME: {frame}" if frame else "",
        f"WEARING: {wearing}" if wearing else "",
        f"BEAT: {beat}" if beat else "",
        f"VIBE: {vibe}" if vibe else "",
        f"OPEN: {open_}" if open_ else "",
        f"CLEAR_OPEN: {clear_open}",
        "STANDING: none",
        "UNCHANGED: none",
        f"TAGS: {tags}" if tags else "TAGS: none",
        f"CRAFT_SCENE: {craft_scene}" if craft_scene else "CRAFT_SCENE: none",
    ])


def _current_note(prompt: str) -> str:
    """The instruction this scripter turn is answering, minus the transcript."""
    for marker in (
        "SHOWRUNNER'S LATEST LINE:",
        "総監督がいま言ったこと:",  # legacy marker
    ):
        head, sep, tail = str(prompt).partition(marker)
        if sep:
            return tail
    return str(prompt)


class NotebookOllama(FakeOllama):
    """Keyword → scripter labelled block; Muse always says a short SAY."""

    def __init__(self, scripts=None):
        super().__init__()
        self.scripts = scripts or {}
        self.scripter_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        text = "SAY: うん、その感じ。"
        if "studio scripter" in system or "shot notebook" in system:
            self.scripter_prompts.append(str(prompt))
            # Match on the current instruction only. The prompt also carries the
            # conversation now, so matching the whole thing would let an earlier
            # turn's keyword answer a later turn. Longest keyword wins within
            # that line so "また煽って、カーディガン" does not match a bare "煽って".
            hits = [k for k in self.scripts if k in _current_note(str(prompt))]
            key = max(hits, key=len) if hits else ""
            text = self.scripts.get(key) or _scripter_block(
                intent="casual", vibe="chatting",
            )

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


def test_parse_scripter_intent_and_patch():
    raw = _scripter_block(
        intent="mixed",
        wearing="sailor uniform, straw hat",
        beat="leaning on fence",
        frame="eye-level, looking at viewer",
        tags="sailor_collar, straw_hat, leaning, looking_at_viewer",
        craft_scene="She leans on a fence in a sailor uniform and straw hat.",
    )
    out = notebook.parse_scripter(raw)
    assert out["intent"] == "mixed"
    assert "straw hat" in out["patch"]["wearing"]
    assert "straw_hat" in out["tags"]
    assert "looking_at_viewer" in out["tags"]


def test_apply_patch_absolute_replace():
    nb = notebook.blank()
    notebook.apply_patch(nb, {"wearing": "jacket, skirt"})
    notebook.apply_patch(nb, {"wearing": "blouse, skirt"})
    assert nb["wearing"] == "blouse, skirt"
    assert nb["rev"] == 2


@pytest.mark.asyncio
async def test_live_compile_without_prep():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "麦わら": _scripter_block(
            intent="shot",
            scene="rooftop fence, late afternoon",
            frame="eye level, looking at viewer",
            wearing="sailor uniform, straw hat",
            beat="leaning on the fence",
            tags="rooftop, fence, sailor_collar, straw_hat, leaning, looking_at_viewer",
            craft_scene="On a rooftop she leans on the fence in a sailor uniform and straw hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "屋上でフェンスにもたれて、麦わら帽子")
    tags = s["craft"]["tags"]
    assert "straw_hat" in tags
    assert "sailor_collar" in tags
    assert s["notebook"]["wearing"]
    # Prep was never pressed.
    assert any(m.get("role") == "muse" for m in s["chat"])


@pytest.mark.asyncio
async def test_casual_does_not_wipe_craft():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat, white shirt",
            beat="standing",
            frame="eye level, looking at viewer",
            tags="straw_hat, white_shirt, standing, looking_at_viewer",
            craft_scene="She stands in a white shirt and straw hat.",
        ),
        "かき氷": _scripter_block(intent="casual", vibe="talking about melon shaved ice"),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶって")
    before = s["craft"]["tags"]
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert s["craft"]["tags"] == before
    assert "straw_hat" in before


@pytest.mark.asyncio
async def test_hat_off_full_replace_removes_hat():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "かぶ": _scripter_block(
            intent="shot",
            wearing="sailor uniform, straw hat",
            beat="standing",
            frame="eye level, looking at viewer",
            tags="sailor_collar, straw_hat, standing, looking_at_viewer",
            craft_scene="Sailor uniform and straw hat.",
        ),
        "要らない": _scripter_block(
            intent="shot",
            wearing="sailor uniform",
            beat="standing",
            frame="eye level, looking at viewer",
            tags="sailor_collar, standing, looking_at_viewer",
            craft_scene="Sailor uniform, no hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子をかぶせて")
    await service.post_duet_chat(db, ollama, s, "その麦わら帽子はもう要らない")
    tags = s["craft"]["tags"]
    assert "straw_hat" not in tags
    assert "sailor_collar" in tags


@pytest.mark.asyncio
async def test_low_angle_gaze_is_looking_down_not_up():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "セーラー": _scripter_block(
            intent="shot",
            wearing="sailor uniform",
            beat="leaning on fence",
            frame="eye level, looking at viewer",
            tags="sailor_collar, leaning, looking_at_viewer, eye_level",
            craft_scene="Eye-level lean on a fence.",
        ),
        "煽って": _scripter_block(
            intent="shot",
            wearing="sailor uniform",
            beat="leaning on fence",
            frame="low angle from below, she looks down into the lens",
            tags="sailor_collar, leaning, from_below, low_angle, looking_down",
            craft_scene="Low angle; she looks down toward the lens.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "セーラーでフェンスにもたれて")
    await service.post_duet_chat(db, ollama, s, "やっぱり下から煽って")
    tags = s["craft"]["tags"]
    assert "from_below" in tags or "low_angle" in tags
    assert "looking_down" in tags
    assert "looking_up" not in tags


@pytest.mark.asyncio
async def test_look_up_rewrites_frame_not_merge():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "煽って": _scripter_block(
            intent="shot",
            wearing="white shirt",
            beat="standing",
            frame="low angle, looks down into lens",
            tags="white_shirt, standing, from_below, looking_down",
            craft_scene="Low angle looking down.",
        ),
        "見上げ": _scripter_block(
            intent="shot",
            wearing="white shirt",
            beat="standing, head tilted back toward the sky",
            frame="slightly low camera, she looks up at the sky away from lens",
            tags="white_shirt, standing, looking_up, from_below",
            craft_scene="She looks up at the sky; camera adjusted as one story.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "下から煽って")
    # Looking up + from_below is refused by the safety check — craft stays prior.
    # A clean rewrite without the conflict should compile:
    ollama.scripts["見上げ"] = _scripter_block(
        intent="shot",
        wearing="white shirt",
        beat="standing, head tilted toward the sky",
        frame="eye level three-quarter, looking up at the sky",
        tags="white_shirt, standing, looking_up, eye_level",
        craft_scene="She looks up at the sky at eye level.",
    )
    await service.post_duet_chat(db, ollama, s, "空を見上げて")
    tags = s["craft"]["tags"]
    assert "looking_up" in tags
    assert "looking_down" not in tags


@pytest.mark.asyncio
async def test_scripter_prompt_has_no_diary_injection():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "公園": _scripter_block(
            intent="shot",
            scene="park bench at dusk",
            wearing="cardigan",
            beat="sitting",
            frame="eye level",
            tags="park, bench, cardigan, sitting",
            craft_scene="Park bench at dusk.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["memories"] = ["堤防で傘をさしていた"]
    s["social_seeds"] = ["今度は赤い傘が流行ってる"]
    s["caught"] = {"ids": ["x"], "summary": "日記を読まれた"}
    s["handpost_notices"] = ["足は映さないで"]
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "公園のベンチで")
    joined = "\n".join(ollama.scripter_prompts)
    assert "傘" not in joined
    assert "流行" not in joined
    assert "日記を読まれた" not in joined
    assert "足は映さない" not in joined
    assert "NOTEBOOK NOW" in joined


@pytest.mark.asyncio
async def test_casual_chit_chat_runs_scripter_and_leaves_craft_alone():
    """Chit-chat reaches the scripter and comes back `casual` — craft untouched.

    There used to be a keyword gate in front of the scripter that decided from
    the showrunner's wording whether to call it at all. It is the scripter's
    call now: it answers `intent: casual` and nothing is compiled.
    """
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat",
            beat="standing",
            frame="eye level",
            tags="straw_hat, standing",
            craft_scene="Hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶって")
    before = len(ollama.scripter_prompts)
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    # Called — no keyword gate. (VERIFY only runs when casual leaves SHOT still.)
    assert len(ollama.scripter_prompts) >= before + 1
    # …and it declined to change the picture.
    assert s["scripter_intent"] == "casual"
    assert "straw_hat" in s["craft"]["tags"]


@pytest.mark.asyncio
async def test_verify_recovers_casual_misread_of_picture_change():
    """Empty-patch casual freeze → VERIFY can still compile (no keyword gate)."""
    db = FakeDb()

    class VerifyOllama(NotebookOllama):
        def __init__(self):
            super().__init__(scripts={})
            self._n = 0

        def generate_text_stream(self, prompt, **kw):
            self.calls.append({**kw, "prompt": prompt})
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                self.scripter_prompts.append(str(prompt))
                self._n += 1
                if self._n == 1:
                    # Total freeze: casual, no vibe/open/SHOT — triggers VERIFY.
                    text = _scripter_block(intent="casual")
                else:
                    assert "VERIFY" in str(prompt)
                    text = _scripter_block(
                        intent="shot",
                        scene="sandy beach shoreline",
                        wearing="cheerleader uniform",
                        beat="running on wet sand",
                        frame="eye level",
                        tags="beach, sand, cheerleader_uniform, running",
                        craft_scene="Running on the beach.",
                    )
            else:
                text = "SAY: 砂、かかとに入る。"

            async def _stream():
                yield {"type": "token", "text": text}
            return _stream()

    ollama = VerifyOllama()
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["notebook"] = notebook.blank()
    notebook.apply_patch(s["notebook"], {
        "scene": "public park",
        "wearing": "sailor uniform",
        "beat": "standing",
        "frame": "eye level",
    })
    s["craft"] = {
        "tags": "public_park, sailor_collar",
        "scene": "Park sailor.",
        "prompt": "public_park, sailor_collar, Park sailor.",
        "pose_intent": "",
    }
    s["notebook_rev_compiled"] = int(s["notebook"].get("rev") or 0)
    await session_db.save(db, s)
    await service.post_duet_chat(
        db, ollama, s, "場所をビーチにして砂浜走ってる感じにしよう",
    )
    assert s["scripter_intent"] == "shot"
    assert "beach" in (s["notebook"].get("scene") or "")
    assert "beach" in (s["craft"].get("tags") or "")
    assert len(ollama.scripter_prompts) >= 2


@pytest.mark.asyncio
async def test_vibe_only_casual_skips_verify_and_stays_clean():
    """Chill chat may update vibe once — no VERIFY tax, craft tags untouched."""
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "セーラー": _scripter_block(
            intent="shot", scene="park", wearing="sailor uniform",
            beat="standing", frame="eye level",
            tags="park, sailor_collar, standing, looking_at_viewer, afternoon_light, "
                 "maple_tree, bench, soft_smile, wind",
            craft_scene=(
                "She stands in a sunlit park in a sailor uniform, weight on one hip, "
                "maple shade across the collar, a quiet bench behind her, soft afternoon "
                "air moving the ribbon, camera at eye level as she looks toward the viewer "
                "with a small unforced smile while the path gravel ticks under her shoes "
                "and the distant fountain keeps a low hush that belongs to this place alone."
            ),
        ),
        "かき氷": _scripter_block(intent="casual", vibe="wanting shaved ice"),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "セーラーで公園")
    tags_before = str((s.get("craft") or {}).get("tags") or "")
    dirty_before = bool(s.get("craft_dirty"))
    before = len(ollama.scripter_prompts)
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert len(ollama.scripter_prompts) == before + 1  # no VERIFY
    assert s["scripter_intent"] == "casual"
    assert str((s.get("craft") or {}).get("tags") or "") == tags_before
    # Vibe-only must not newly dirty a clean craft.
    if not dirty_before:
        assert s["craft_dirty"] is False


@pytest.mark.asyncio
async def test_wardrobe_change_without_any_keyword_still_lands():
    """「浴衣に着替えて」— the exact shape the old keyword gate dropped.

    `着替え` never matched the gate's `着て` (which needed 着 and て adjacent),
    and the line carries neither 服 nor 衣装, so the turn was skipped: the Muse
    replied about the yukata while the notebook and craft kept the old outfit.
    """
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "浴衣": _scripter_block(
            intent="shot",
            scene="summer festival street",
            wearing="navy yukata with a red obi",
            beat="walking",
            frame="eye level",
            tags="yukata, obi, walking, festival",
            craft_scene="She walks a festival street in a navy yukata.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "scene": "classroom", "wearing": "sailor uniform", "beat": "standing",
    })
    s["craft"] = {
        "tags": "classroom, sailor_collar, standing",
        "scene": "Classroom.", "prompt": "1girl, classroom", "pose_intent": "",
    }
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "浴衣に着替えて")

    assert "yukata" in s["notebook"]["wearing"]
    assert "yukata" in s["craft"]["tags"]
    assert "sailor_collar" not in s["craft"]["tags"]
    assert "yukata" in s["craft"]["prompt"]


@pytest.mark.asyncio
async def test_location_change_without_any_keyword_still_lands():
    """「公園で撮ろう」— no 場所, and 撮ろう missed 撮影|撮り直|撮り方."""
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "公園": _scripter_block(
            intent="shot",
            scene="park under cherry trees",
            wearing="sailor uniform",
            beat="standing",
            frame="eye level",
            tags="park, cherry_blossoms, sailor_collar, standing",
            craft_scene="A park under cherry trees.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {
        "scene": "classroom", "wearing": "sailor uniform", "beat": "standing",
    })
    s["craft"] = {
        "tags": "classroom, sailor_collar, standing",
        "scene": "Classroom.", "prompt": "1girl, classroom", "pose_intent": "",
    }
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "公園で撮ろう")

    assert "park" in s["notebook"]["scene"]
    assert "park" in s["craft"]["tags"]
    assert "classroom" not in s["craft"]["tags"]


@pytest.mark.asyncio
async def test_notebook_moving_without_a_compile_marks_craft_dirty():
    """A casual turn that still edits the notebook must not leave craft silently behind.

    `apply_patch` runs whatever the intent is, so `rev` can climb on a casual
    turn. Craft was only recompiled on shot/mixed and `craft_dirty` was only set
    there too, so the notebook could run ahead of the rendered prompt with
    nothing marking it — and densify's early return then declined to catch up.
    """
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "そろそろ": _scripter_block(
            intent="casual",
            vibe="talking about the light going",
            atmosphere="the light is going amber",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {"scene": "rooftop", "wearing": "cardigan"})
    s["craft"] = {
        "tags": "rooftop, cardigan", "scene": "Rooftop.",
        "prompt": "1girl, rooftop, cardigan", "pose_intent": "",
    }
    s["notebook_rev_compiled"] = int(s["notebook"]["rev"])
    s["craft_dirty"] = False
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "そろそろ暗くなってきたね")

    assert s["scripter_intent"] == "casual"
    assert s["notebook"]["atmosphere"]
    assert int(s["notebook"]["rev"]) > int(s["notebook_rev_compiled"])
    assert s["craft_dirty"] is True


@pytest.mark.asyncio
async def test_open_affirm_lands_in_craft():
    """A bare「いいね」against a pending OPEN compiles the proposal in.

    The affirm used to be recognised by regex, folded into the notebook by
    `promote_open` (which guessed handheld-vs-worn off a noun list), and then
    compiled by a second forced scripter call. The scripter sees the OPEN it
    wrote and the「いいね」that accepted it in the conversation, and does all
    three itself in one turn.
    """
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "ベンチ": _scripter_block(
            intent="shot",
            scene="park bench at dusk",
            wearing="thin cardigan",
            beat="sitting on a bench",
            frame="eye level",
            open_="落ち葉を一枚だけ手に",
            tags="park, bench, cardigan, sitting",
            craft_scene="Park bench, no leaf yet.",
        ),
        "いいね": _scripter_block(
            intent="mixed",
            scene="park bench at dusk",
            wearing="thin cardigan",
            beat="sitting on a bench, holding one fallen leaf",
            frame="eye level",
            vibe="happy",
            clear_open="yes",
            tags="park, bench, cardigan, sitting, leaf",
            craft_scene="Park bench with one leaf in hand.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "ベンチに座って薄いカーディガン")
    assert s["notebook"]["open"]
    await service.post_duet_chat(db, ollama, s, "いいね")
    assert s["notebook"]["open"] == ""
    assert "leaf" in s["notebook"]["beat"]
    assert "leaf" in s["craft"]["tags"]


@pytest.mark.asyncio
async def test_scripter_is_handed_the_conversation():
    """The transcript is the fix — without it「いいね」cannot be resolved."""
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "ベンチ": _scripter_block(
            intent="shot", scene="park bench", wearing="cardigan",
            beat="sitting", frame="eye level",
            open_="落ち葉を一枚だけ手に",
            tags="park, bench, cardigan, sitting",
            craft_scene="Park bench.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "ベンチに座って")
    await service.post_duet_chat(db, ollama, s, "いいね")

    last = ollama.scripter_prompts[-1]
    assert "CONVERSATION SO FAR" in last or "ここまでの会話" in last
    # Both sides of the exchange the affirm refers back to.
    assert "ベンチに座って" in last
    assert "総監督: いいね" in last or "SHOWRUNNER'S LATEST LINE" in last


@pytest.mark.asyncio
async def test_scripter_exception_keeps_craft_and_muse_talks():
    db = FakeDb()

    class Boom(NotebookOllama):
        async def generate_text(self, prompt, **kw):
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                raise RuntimeError("scripter down")
            return await super().generate_text(prompt, **kw)

        def generate_text_stream(self, prompt, **kw):
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                async def _boom():
                    raise RuntimeError("scripter down")
                    yield {"type": "token", "text": ""}  # pragma: no cover
                return _boom()
            return super().generate_text_stream(prompt, **kw)

    ollama = Boom(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat",
            beat="standing",
            frame="eye level",
            tags="straw_hat, standing",
            craft_scene="Hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子")
    before = s["craft"]["tags"]
    # Force next scripter call to boom via keyword that needs scripter
    ollama.scripts.clear()
    await service.post_duet_chat(db, ollama, s, "帽子外して煽って")
    assert s["craft"]["tags"] == before
    assert s.get("craft_dirty") is True
    assert any(m.get("role") == "muse" for m in s["chat"])


@pytest.mark.asyncio
async def test_dialogue_path_reunion_recall_chat_shot_affirm(monkeypatch):
    """再会 → recall → 雑談 → shot → OPEN肯定 のノート正本パス。"""
    db = FakeDb()
    bond_store = {
        "distance": "もう顔見知り",
        "inside": "堤防の夕焼けを一緒に見た",
        "last": "堤防 / セーラー",
    }
    taste_store = {"prefers": "ローアングル", "avoids": "足", "notes": ""}

    async def _bond(db, cid):
        return dict(bond_store)

    async def _taste(db, cid):
        return dict(taste_store)

    async def _chem(db, cid, limit=2):
        return ["息が合いやすい"]

    async def _set_bond(db, cid, bond):
        bond_store.update(bond)
        return {
            "distance": bond_store["distance"],
            "inside": bond_store["inside"],
            "last": bond_store["last"],
        }

    async def _set_taste(db, cid, taste):
        taste_store.update(taste)
        return {
            "prefers": taste_store.get("prefers", ""),
            "avoids": taste_store.get("avoids", ""),
            "notes": taste_store.get("notes", ""),
        }

    monkeypatch.setattr(service.presets_db, "get_bond", _bond)
    monkeypatch.setattr(service.presets_db, "get_showrunner_taste", _taste)
    monkeypatch.setattr(service.presets_db, "get_recent_chemistry_notes", _chem)
    monkeypatch.setattr(service.presets_db, "update_bond", _set_bond)
    monkeypatch.setattr(service.presets_db, "update_showrunner_taste", _set_taste)

    async def _search(db, ollama, *, character_id, query, limit=3):
        return [{
            "when": "堤防の夕焼け",
            "feel": "風が強かった",
            "liked": "セーラー",
            "shot": "looking_at_viewer, sailor_collar",
        }]

    monkeypatch.setattr(service.memories_db, "search", _search)

    ollama = NotebookOllama(scripts={
        "この前": (
            "INTENT: recall\nVIBE: remembering the embankment\n"
            "CLEAR_OPEN: no\nUNCHANGED: none\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "かき氷": (
            "INTENT: casual\nVIBE: chatting about shaved ice\n"
            "CLEAR_OPEN: no\nUNCHANGED: none\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "屋上": _scripter_block(
            intent="shot",
            scene="school rooftop at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, leaning, looking_at_viewer",
            craft_scene="Rooftop lean in sailor uniform.",
        ),
        # The scripter reads the conversation, so it sees its own OPEN and the
        # 「いいね」 that accepted it, and folds the prop in itself. This used to
        # take a regex on the affirm plus a second forced COMPILE ONLY call.
        "いいね": _scripter_block(
            intent="mixed",
            scene="school rooftop at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence, holding ramune",
            vibe="happy",
            clear_open="yes",
            tags="rooftop, fence, sailor_collar, leaning, ramune, looking_at_viewer",
            craft_scene="Rooftop lean with ramune.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["character"]["name_ja"] = "あさひ"
    await session_db.save(db, s)

    s = await service.start_duet(db, ollama, s)
    assert s.get("bond", {}).get("inside")
    assert "堤防" in (s.get("bond") or {}).get("inside", "")
    # Prior sticky notebook so chit-chat can skip scripter (empty shot would
    # still call it for long theme-like lines).
    notebook.apply_patch(s["notebook"], {
        "scene": "embankment at dusk",
        "frame": "eye level",
        "wearing": "sailor uniform",
        "beat": "standing in the wind",
    })
    s["craft"] = {
        "tags": "embankment, sailor_collar, looking_at_viewer",
        "scene": "Embankment at dusk.",
        "prompt": "1girl, embankment",
        "pose_intent": "",
    }
    await session_db.save(db, s)

    # Reunion / recall — Muse prompt must ground cited nouns.
    await service.post_duet_chat(db, ollama, s, "この前の堤防、覚えてる？")
    muse_prompts = [
        c["prompt"] for c in ollama.calls
        if "studio scripter" not in str(c.get("system") or "")
        and "shot notebook" not in str(c.get("system") or "")
    ]
    joined_muse = "\n".join(muse_prompts)
    assert "CITED_MEMORIES" in joined_muse or "堤防" in joined_muse
    assert "BOND" in joined_muse or "関係" in joined_muse
    assert "GROUNDED_TOKENS" in joined_muse

    # Casual turn — the scripter still runs (no gate).
    before_scripts = len(ollama.scripter_prompts)
    before_tags = str((s.get("craft") or {}).get("tags") or "")
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert len(ollama.scripter_prompts) >= before_scripts + 1
    assert str((s.get("craft") or {}).get("tags") or "") == before_tags

    # Shot + OPEN affirm.
    await service.post_duet_chat(db, ollama, s, "屋上でセーラー、フェンスにもたれて")
    assert "sailor" in s["notebook"]["wearing"] or "sailor" in s["craft"]["tags"]
    assert s["notebook"]["open"]
    await service.post_duet_chat(db, ollama, s, "いいね")
    assert s["notebook"]["open"] == ""
    assert "ramune" in s["craft"]["tags"] or "ラムネ" in s["notebook"]["beat"]

    # Continuity write after ③ snapshot.
    s["continuity_snapshot"] = {
        "theme": "屋上",
        "notebook": dict(s["notebook"]),
        "craft_tags": s["craft"]["tags"],
        "craft_scene": s["craft"]["scene"],
    }
    s["standing"] = ["足は映さない"]
    s.pop("continuity", None)
    await service.record_shoot_continuity(db, s, ollama=ollama)
    assert "足" in taste_store.get("avoids", "")


def test_cited_allowlist_extracts_grounded_tokens():
    s = {
        "memories": ["堤防で夕焼けを見た"],
        "cited_memories": [{"when": "公園のベンチ", "feel": "風", "liked": "帽子"}],
        "bond": {"distance": "", "inside": "セーラーが似合う", "last": ""},
        "partner_memories": [],
    }
    allow = service._cited_allowlist(s)
    joined = " ".join(allow)
    assert "堤防" in joined
    assert "セーラー" in joined or "公園" in joined


def test_bond_and_taste_from_snapshot_are_short():
    s = {
        "continuity_snapshot": {
            "theme": "屋上",
            "notebook": {
                "atmosphere": "夕暮れの屋上",
                "vibe": "少し照れてる",
                "wearing": "セーラー",
                "frame": "low angle 煽り",
                "open": "",
            },
        },
        "standing": ["足は映さない"],
    }
    bond, taste = service._bond_and_taste_from_snapshot(s)
    assert bond["last"]
    assert "ローアングル" in taste["prefers"] or "セーラー" in taste["prefers"]
    assert "足" in taste["avoids"]


@pytest.mark.asyncio
async def test_densify_catches_up_when_the_notebook_ran_ahead():
    """The board button recompiles a craft the notebook has outrun.

    `densify_craft_if_needed` returned early unless craft was flagged dirty or
    looked thin, so a notebook that had moved without a compile rendered the
    previous prompt. The UI was already computing this exact condition
    (`notebookAhead`) and warning about it; the server now checks it too.
    """
    from tests.muse.test_service import FakeComfy, FakeSpooler

    db, spooler = FakeDb(), FakeSpooler()
    ollama = NotebookOllama(scripts={
        "DENSIFY": _scripter_block(
            intent="shot",
            scene="park under cherry trees",
            wearing="navy yukata",
            beat="walking",
            frame="eye level",
            tags="park, cherry_blossoms, yukata, walking, dusk, warm_light",
            craft_scene="A long, thick paragraph about a park at dusk. " * 12,
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {"scene": "park", "wearing": "navy yukata"})
    s["craft"] = {
        "tags": "classroom, sailor_collar",
        "scene": "A classroom. " * 30,
        "prompt": "1girl, classroom, sailor_collar, " + "detail, " * 40,
        "pose_intent": "",
    }
    # Craft is neither dirty nor thin — only the rev gap says it is stale.
    s["craft_dirty"] = False
    s["notebook_rev_compiled"] = int(s["notebook"]["rev"]) - 1
    await session_db.save(db, s)

    s = await service.request_board(db, FakeComfy(), spooler, s, ollama=ollama)

    assert "yukata" in s["craft"]["tags"]
    assert "classroom" not in s["craft"]["tags"]
    assert "yukata" in s["board"]["prompt"]


@pytest.mark.asyncio
async def test_a_failed_densify_is_said_out_loud_not_swallowed():
    """Both render buttons used to clear `craft_dirty` unconditionally.

    That happened straight after densify, whether or not densify had worked, so
    a failed compile went out on the previous prompt with the warning wiped and
    nobody told. Keep the flag and say it in chat.
    """
    from tests.muse.test_service import FakeComfy, FakeSpooler

    db, spooler = FakeDb(), FakeSpooler()
    # No DENSIFY script → the fake answers `casual` with no tags → no compile.
    ollama = NotebookOllama(scripts={})
    s = await _duet_session(db)
    s["mode"] = "duet"
    notebook.apply_patch(s["notebook"], {"scene": "park", "wearing": "navy yukata"})
    s["craft"] = {
        "tags": "classroom, sailor_collar",
        "scene": "A classroom.",
        "prompt": "1girl, classroom",
        "pose_intent": "",
    }
    s["craft_dirty"] = True
    await session_db.save(db, s)

    s = await service.request_board(db, FakeComfy(), spooler, s, ollama=ollama)

    assert s["craft_dirty"] is True
    # It still shoots — with the old prompt, and having said so.
    assert len(spooler.jobs) == 1
    said = "\n".join(
        str(m.get("text") or "") for m in s["chat"] if m.get("role") == "system"
    )
    assert "追いついていません" in said


@pytest.mark.asyncio
async def test_w_muse_flat_tag_bag_still_moves_the_picture():
    """An unsplit W-Muse bag compiles rather than freezing the shoot.

    The refusal used to throw the whole compile away, so a model that kept
    answering with one flat bag left craft on its last good compile — the
    wardrobe and location from several turns back — for the rest of the session.
    """
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "浴衣": (
            "INTENT: shot\n"
            "SCENE: summer festival street\n"
            "WEARING: navy yukata\n"
            "WEARING_B: white yukata\n"
            "BEAT: walking\n"
            "BEAT_B: walking beside her\n"
            "FRAME: eye level\n"
            "CLEAR_OPEN: no\nUNCHANGED: none\n"
            "TAGS: 2girls, festival, yukata, walking\n"
            "CRAFT_SCENE: Two girls in yukata on a festival street.\n"
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["inputs"]["partner_preset"] = "minamo"
    notebook.apply_patch(s["notebook"], {"scene": "classroom", "wearing": "sailor uniform"})
    s["craft"] = {
        "tags": "classroom, sailor_collar",
        "scene": "Classroom.", "prompt": "2girls, classroom", "pose_intent": "",
    }
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "二人とも浴衣にして")

    assert "yukata" in s["notebook"]["wearing"]
    assert "yukata" in s["notebook"]["wearing_b"]
    assert "yukata" in s["craft"]["tags"]
    assert "classroom" not in s["craft"]["tags"]

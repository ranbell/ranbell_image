"""Notebook + scripter path for 主演撮り — live craft, no prep gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, notebook, service, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def _tags(s):
    """Conversation-time picture: the notebook, not woven craft tags."""
    nb = s.get("notebook") or {}
    blob = " ".join(
        str(nb.get(k) or "")
        for k in (
            "wearing", "scene", "beat", "frame",
            "wearing_b", "beat_b", "atmosphere",
        )
    ).lower()
    extra: list[str] = []
    if "straw" in blob and "hat" in blob:
        extra.append("straw_hat")
    if "sailor" in blob:
        extra.append("sailor_collar")
    if "ramune" in blob:
        extra.append("ramune")
    if "yukata" in blob:
        extra.append("yukata")
    if "park" in blob:
        extra.append("park")
    if "beach" in blob:
        extra.append("beach")
    if "leaf" in blob:
        extra.append("leaf")
    if "look" in blob and "down" in blob:
        extra.append("looking_down")
    if "look" in blob and "up" in blob:
        extra.append("looking_up")
    if "below" in blob or "low angle" in blob or "low-angle" in blob:
        extra.extend(["from_below", "low_angle"])
    if "sit" in blob:
        extra.append("sitting")
    if "read" in blob:
        extra.append("reading")
    if "cardigan" in blob:
        extra.append("cardigan")
    if "white shirt" in blob or "white_shirt" in blob:
        extra.append("white_shirt")
    return blob.replace(" ", "_") + " " + " ".join(extra)


def _scripter_block(
    *, intent="shot", atmosphere="", scene="", frame="", wearing="", beat="",
    vibe="", tags="", craft_scene="", wearing_drop="",
):
    """One scripter reply. `wearing_drop` is part of the contract, not an extra:
    「wearing_drop = when something comes OFF, name that ONE garment」. A fake
    that rewrites WEARING without it is modelling a scripter that broke its own
    contract, which is not what a removal test should be asserting against."""
    return "\n".join([
        f"INTENT: {intent}",
        f"ATMOSPHERE: {atmosphere}" if atmosphere else "",
        f"SCENE: {scene}" if scene else "",
        f"FRAME: {frame}" if frame else "",
        f"WEARING: {wearing}" if wearing else "",
        f"WEARING_DROP: {wearing_drop}" if wearing_drop else "",
        f"BEAT: {beat}" if beat else "",
        f"VIBE: {vibe}" if vibe else "",
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


def _fake_clerk_reply(system: str, prompt: str) -> str | None:
    """Heuristic clerk answers for FakeOllama subclasses (fields / intent)."""
    system_l = str(system or "").lower()
    if "studio's clerk" not in system_l and "studios clerk" not in system_l:
        return None
    prompt_s = str(prompt or "")
    note = prompt_s
    for marker in ("DIRECTOR:", "DIRECTOR, in order:"):
        if marker in prompt_s:
            note = prompt_s.split(marker, 1)[-1]
            break
    note_l = note.lower()
    fields: list[str] = []
    if any(k in note for k in (
        "着", "脱", "帽子", "服", "セーラー", "コート", "羽織", "外し", "制服",
        "カーディガン", "麦わら", "uniform", "hat", "wear", "cardigan", "coat",
        "dress", "jacket", "shirt", "blouse",
    )):
        fields.append("wearing")
    if any(k in note for k in (
        "座", "立", "走", "手", "ポーズ", "しゃが", "持", "もた",
        "sit", "stand", "run", "hold", "kneel", "lean", "wave", "pose",
    )):
        fields.append("beat")
    if any(k in note for k in (
        "寄", "引", "画角", "全身", "アップ", "カメラ", "アングル",
        "close", "wide", "frame", "angle", "zoom", "full body", "upper",
    )):
        fields.append("frame")
    if any(k in note for k in (
        "場所", "公園", "ビーチ", "教室", "屋上", "夕方", "夜", "朝", "窓",
        "beach", "park", "rooftop", "classroom", "night", "dusk", "scene",
        "砂浜",
    )):
        fields.append("scene")
    if any(k in note for k in ("光", "逆光", "照明", "light", "backlight")):
        fields.append("light")
    if any(k in note for k in ("後ろ", "背景", "bg", "building", "crowd", "建物")):
        fields.append("bg")

    if "fields:" in prompt_s.lower() or "which parts of the shot" in system_l:
        return ", ".join(fields) if fields else "none"
    if "kind of turn" in system_l or prompt_s.rstrip().endswith("WORD:"):
        if fields:
            return "shot"
        if any(k in note_l for k in ("？", "?", "なに", "何", "いま", "今", "recall")):
            return "recall"
        return "casual"
    return None


class NotebookOllama(FakeOllama):
    """Keyword → scripter labelled block; Muse always says a short SAY."""

    def __init__(self, scripts=None):
        super().__init__()
        self.scripts = scripts or {}
        self.scripter_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        clerk = _fake_clerk_reply(system, str(prompt))
        if clerk is not None:
            text = clerk
        else:
            text = "SAY: うん、その感じ。"
            if "studio scripter" in system or "shot notebook" in system:
                prompt_s = str(prompt)
                self.scripter_prompts.append(prompt_s)
                # Match on the current instruction only. The prompt also carries the
                # conversation now, so matching the whole thing would let an earlier
                # turn's keyword answer a later turn. Longest keyword wins within
                # that line so "また煽って、カーディガン" does not match a bare "煽って".
                note = _current_note(prompt_s)
                hits = [k for k in self.scripts if k in note]
                fold_keys = [k for k in self.scripts if "FOLD" in k]
                if fold_keys and "FOLD:" in prompt_s:
                    hits = [k for k in fold_keys if k in prompt_s] or fold_keys
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
    tags = _tags(s)
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
    before = _tags(s)
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert _tags(s) == before
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
    tags = _tags(s)
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
    tags = _tags(s)
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
    tags = _tags(s)
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
    assert "straw_hat" in _tags(s)


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
            clerk = _fake_clerk_reply(system, str(prompt))
            if clerk is not None:
                text = clerk
            elif "studio scripter" in system or "shot notebook" in system:
                self.scripter_prompts.append(str(prompt))
                self._n += 1
                if self._n == 1:
                    # Total freeze: casual, no vibe/open/SHOT — triggers VERIFY.
                    text = _scripter_block(intent="casual")
                else:
                    # 2回目は note を載せない（測って外した）。この回であることは
                    # 呼び出しの順番で分かる。
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
    assert "beach" in (s["notebook"].get("scene") or "")
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
    tags_before = _tags(s)
    dirty_before = bool(s.get("craft_dirty"))
    before = len(ollama.scripter_prompts)
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert len(ollama.scripter_prompts) >= before + 1
    assert s["scripter_intent"] == "casual"
    assert _tags(s) == tags_before
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
    assert "yukata" in _tags(s)
    assert "sailor" not in (s["notebook"].get("wearing") or "").lower()


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
    assert "park" in _tags(s)
    assert "classroom" not in (s["notebook"].get("scene") or "").lower()


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
async def test_a_bare_affirm_compiles_what_it_affirmed():
    """A bare「いいね」lands the thing she just offered, in one turn.

    The affirm used to be recognised by regex, folded into the notebook by
    `promote_open` (which guessed handheld-vs-worn off a noun list), and then
    compiled by a second forced scripter call. The scripter reads the offer and
    the「いいね」that accepted it in the conversation, and does it all itself.
    The holding pen those two stages shared (`open`) is gone: 390 live sessions
    never put a proposal in it.
    """
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "ベンチ": _scripter_block(
            intent="shot",
            scene="park bench at dusk",
            wearing="thin cardigan",
            beat="sitting on a bench",
            frame="eye level",
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
            tags="park, bench, cardigan, sitting, leaf",
            craft_scene="Park bench with one leaf in hand.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "ベンチに座って薄いカーディガン")
    assert "leaf" not in s["notebook"]["beat"]
    await service.post_duet_chat(db, ollama, s, "いいね")
    assert "leaf" in s["notebook"]["beat"]
    assert "leaf" in _tags(s)


@pytest.mark.asyncio
async def test_scripter_is_handed_the_conversation():
    """The transcript is the fix — without it「いいね」cannot be resolved."""
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "ベンチ": _scripter_block(
            intent="shot", scene="park bench", wearing="cardigan",
            beat="sitting", frame="eye level",
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
    before = _tags(s)
    # Force next scripter call to boom via keyword that needs scripter
    ollama.scripts.clear()
    await service.post_duet_chat(db, ollama, s, "帽子外して煽って")
    assert _tags(s) == before
    assert s.get("craft_dirty") is True
    assert any(m.get("role") == "muse" for m in s["chat"])


@pytest.mark.asyncio
async def test_dialogue_path_reunion_recall_chat_shot_affirm(monkeypatch):
    """再会 → recall → 雑談 → shot → 肯定 のノート正本パス。"""
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
            "UNCHANGED: none\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "かき氷": (
            "INTENT: casual\nVIBE: chatting about shaved ice\n"
            "UNCHANGED: none\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "屋上": _scripter_block(
            intent="shot",
            scene="school rooftop at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence",
            tags="rooftop, fence, sailor_collar, leaning, looking_at_viewer",
            craft_scene="Rooftop lean in sailor uniform.",
        ),
        # The scripter reads the conversation, so it sees what she offered and
        # the「いいね」that accepted it, and folds the prop in itself. This used
        # to take a regex on the affirm plus a second forced COMPILE ONLY call.
        "いいね": _scripter_block(
            intent="mixed",
            scene="school rooftop at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence, holding ramune",
            vibe="happy",
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
    assert "GROUNDED_TOKENS" in joined_muse or "CITED_MEMORIES" in joined_muse or "堤防" in joined_muse

    # Casual turn — the scripter still runs (no gate).
    before_scripts = len(ollama.scripter_prompts)
    before_tags = str((s.get("craft") or {}).get("tags") or "")
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert len(ollama.scripter_prompts) >= before_scripts + 1
    assert str((s.get("craft") or {}).get("tags") or "") == before_tags

    # Shot, then a bare affirm of what she offered in words.
    await service.post_duet_chat(db, ollama, s, "屋上でセーラー、フェンスにもたれて")
    assert "sailor" in s["notebook"]["wearing"] or "sailor" in s["craft"]["tags"]
    assert "ramune" not in (s["notebook"].get("beat") or "").lower()
    await service.post_duet_chat(db, ollama, s, "いいね")
    assert "ramune" in (s["notebook"].get("beat") or "").lower()

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


def test_bond_remembers_the_take_and_says_nothing_about_taste():
    """Bond is memory of the picture. What she LEARNED is a separate question.

    The taste card used to be derived from this same snapshot — the word "low"
    in `frame` taught her 「ローアングルの近い距離」 and the clothes she ended
    in became a preference. That describes the take, not anything the
    showrunner said about it. See `_learned_taste`.
    """
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
    bond = service._bond_from_snapshot(s)
    assert "夕暮れの屋上" in bond["last"]
    assert "セーラー" in bond["last"]
    assert bond["inside"] == "少し照れてる"


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
        "WEAVE": _scripter_block(
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
async def test_a_failed_densify_is_recorded_not_swallowed():
    """Both render buttons used to clear `craft_dirty` unconditionally.

    That happened straight after densify, whether or not densify had worked, so
    a failed compile went out on the previous prompt with the warning wiped and
    nobody told. The flag is still kept and the miss is still recorded — but in
    the rewrite log the debug pane reads, not as a studio voice interrupting
    the room to ask the showrunner to repeat himself.
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
    # It still shoots — with the old prompt, and having recorded that it did.
    assert len(spooler.jobs) == 1
    assert any(
        e.get("source") == "craft_behind" for e in (s.get("rewrite_log") or [])
    ), "a swallowed miss is the defect; the panel has to be able to see it"
    said = "\n".join(
        str(m.get("text") or "") for m in s["chat"] if m.get("role") == "system"
    )
    assert "追いついていません" not in said, "the room is not where this belongs"


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
    assert "classroom" not in (s["notebook"].get("scene") or "").lower()


def test_the_partner_gets_her_own_forgotten_dress_back():
    """実測（`94b4fc9f`・2026-08-28）: すみれが服ひとつ無しで出た。

    「もうある」判定は語のかぶりで見るので、みおが `light_blue_dress` を着て
    いると、すみれの `black cocktail dress` は `dress` が既出という理由で
    足りていると判定される —— **二着目が絶対に戻らない。**

    これは一度直してあった不具合。旧 `_missing_wearing_tags` の docstring が
    「相方だけ忘れた服が戻らない」と記録していた。
    """
    bag = ("anime_illustration, close-up, park, dusk, standing, "
           "light_blue_dress, silk_fabric")
    out, (side_a, side_b) = notebook.reconcile_wardrobe_tags(
        bag, wearing="light_blue_dress, silk_fabric",
        wearing_b="black cocktail dress",
        sides=("standing, light_blue_dress, silk_fabric", "shrugging"),
        partner=True,
    )
    assert "black_cocktail_dress" in out
    assert "black_cocktail_dress" in side_b      # 彼女の側に付く
    assert "black_cocktail_dress" not in side_a  # 主演には付かない


def test_the_lead_still_gets_hers_back_on_a_solo():
    bag = "anime_illustration, close-up, park, standing"
    out, _ = notebook.reconcile_wardrobe_tags(
        bag, wearing="straw_hat", sides=("", ""), partner=False)
    assert "straw_hat" in out


def _nb_with(**fields):
    nb = notebook.blank()
    notebook.apply_patch(nb, dict(fields))
    return nb


def test_the_review_only_runs_when_she_is_asked_to_arrange():
    """総監督（2026-08-29）「restate がやはり絵を壊します。Muse に自分で考えて・
    アレンジしといてというときだけ動かしたほうがいい」。

    実測（`78d7ce72`）: 場所しか言っていない5ターンで、見直しが `frame` を2回・
    `beat` を3回書き換えた。根拠は**彼女自身の独り言**で、総監督はどちらも指示
    していない。呼ばれていないのに画を作り替えていた。

    拾い漏れは安全側 —— 走らなければ画は台本係の書いたまま。
    """
    invited = ("好きにアレンジしといて", "自分で考えてみて", "いい感じにお願い",
               "おまかせ！", "you decide", "surprise me")
    not_invited = ("ベンチに座って", "寄りで撮ろう", "夕暮れの港が見える公園で",
                   "今日はありがとう")
    for note in invited:
        assert service._invited_to_arrange(note), note
    for note in not_invited:
        assert not service._invited_to_arrange(note), note
    assert not service._invited_to_arrange("")


def test_the_final_take_reweaves_when_the_notebook_moved_since_the_board():
    """実測（2026-08-29）: ボードは撮影開始と明示取消でしか消えなかった。

    試し撮り → 会話で新しい指示 → ③本番、で**古いボードの指示で撮っていた**。
    総監督の求めは条件付き —— 「試し撮り後に会話がなければそのまま流す」。
    """
    session = {"notebook": notebook.blank(), "board": {
        "prompt": "board prompt", "images": ["x.png"], "pending": False, "rev": 3,
    }}
    session["notebook"]["rev"] = 3
    assert service._approved_prompt(session) == "board prompt"
    # 会話で手帖が動いた → 織り直す
    session["notebook"]["rev"] = 4
    assert service._approved_prompt(session) == ""


def test_a_board_from_before_the_rev_was_recorded_still_works():
    """`rev` を持たない古いセッションは、突き合わせようがないので従来どおり。"""
    session = {"notebook": notebook.blank(), "board": {
        "prompt": "old board", "images": ["x.png"], "pending": False,
    }}
    session["notebook"]["rev"] = 9
    assert service._approved_prompt(session) == "old board"


def test_a_look_back_turn_does_not_fold_the_remembered_pose():
    """総監督（2026-08-29）「覚えてる？と聞くとそのポーズを撮るバグ」。

    `asked_back` は**係**の `recall`、折り込みの門は**コンパイル**の `recall`
    で、別々の判断だった —— 係が振り返りと読んで前の撮影を彼女の手元に置き、
    コンパイルが `casual` と言えば折り込みは開いたまま。そこで彼女が思い出した
    ポーズを語れば、それが `beat` に入って撮られる。

    実機（`13df0524`）では再現しなかったが、穴は経路として実在する。
    """
    import inspect
    src = inspect.getsource(service._fold_muse_after_talk)
    assert 'session.get("looked_back")' in src
    # 係が振り返りと読んだら旗が立つこと
    src2 = inspect.getsource(service._run_duet_scripter)
    assert 'session["looked_back"] = bool(asked_back)' in src2


def _w_sides_session(wearing, wearing_b, tags_a, tags_b):
    session = {
        "partner_character": {"name": "Sumire"},
        "notebook": notebook.blank(),
        "craft": {"tags_a": tags_a, "tags_b": tags_b},
        "stage_ms": [],
    }
    notebook.apply_patch(session["notebook"],
                         {"wearing": wearing, "wearing_b": wearing_b})
    return session


_A_WEAR = "light_blue_dress, blue_ribbon"
_B_WEAR = "black_cocktail_dress, pearl_necklace"
_BAG = ("light_blue_dress, blue_ribbon, black_cocktail_dress, "
        "pearl_necklace, park")


def test_a_swapped_weave_is_put_back():
    """総監督（2026-08-29）「w-muse の際にキャラが反転するコトが多い」。

    **どのモデルにも「A が誰か」を教えていなかった。** weave は `tags_a` /
    `tags_b` と書けと言われるだけで、唯一の手がかりは手帖ブロックの並び順。
    推測に預けているので毎回は当たらない。手帖の二つの衣装は正本なので、
    袋がどちらの服を持っているかで読み直せる。
    """
    session = _w_sides_session(_A_WEAR, _B_WEAR, _B_WEAR, _A_WEAR)
    sides = service._sides(session, _BAG)
    assert "light_blue_dress" in sides[0] and "blue_ribbon" in sides[0]
    assert "black_cocktail_dress" in sides[1]
    assert any("入れ替わ" in str(r.get("stage") or "")
               for r in session["stage_ms"]), "戻したことが記録に残っていない"


def test_a_correct_weave_is_left_alone():
    session = _w_sides_session(_A_WEAR, _B_WEAR, _A_WEAR, _B_WEAR)
    sides = service._sides(session, _BAG)
    assert "light_blue_dress" in sides[0]
    assert "black_cocktail_dress" in sides[1]
    assert session["stage_ms"] == [], "触らなくてよい袋を入れ替えた"


def test_two_muses_in_the_same_dress_are_never_swapped():
    """証拠が無いときは動かさない。**同点でも動かさない。**"""
    session = _w_sides_session("blue_dress", "blue_dress",
                               "blue_dress", "blue_dress")
    service._sides(session, "blue_dress")
    assert session["stage_ms"] == []


def test_both_prompts_say_which_letter_is_which_muse():
    """第一層 —— 文字と名前を結ぶ一行。相方がいるときだけ出す。"""
    from app.muse.chain import _who_is_who
    assert _who_is_who("Mio", "Sumire", letters=True) == \
        "tags_a is Mio's. tags_b is Sumire's. Never cross them."
    assert "WEARING_B / BEAT_B are Sumire's" in \
        _who_is_who("Mio", "Sumire", letters=False)
    assert _who_is_who("Mio", "", letters=True) == ""


def test_the_wardrobe_clerk_maps_names_to_the_two_fields():
    """**服だけを言うターン。** 総監督（2026-08-29）の案。

    本番の compile（8,774字）は W で服の欄を取り違える —— 実測 2/20 で、
    `wearing` が一度も書かれなかった（`ccde3c75`）。同じ問いを小さく絞って
    **名前で**訊くと 25/25。形が壊れているのではなく、大きな条文の中で
    埋もれている。

    返りは名前をキーにした JSON。**欄への振り分けはこちらで決める** ——
    モデルに `_b` という文字を選ばせない。
    """
    import asyncio

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply

        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    def _ask(reply):
        return asyncio.run(
            chain.read_wardrobe(
                _Ollama(reply), note="みおちゃんは赤いセーターで。",
                name_a="各務 みお", name_b="平岡 すみれ",
                model="m", num_ctx=1024,
            )
        )

    got = _ask('{"各務 みお": "red sweater, jeans", "平岡 すみれ": "unchanged"}')
    assert got == {"wearing": "red sweater, jeans"}

    # 両方
    got = _ask('{"各務 みお": "red sweater", "平岡 すみれ": "white blouse"}')
    assert got == {"wearing": "red sweater", "wearing_b": "white blouse"}

    # 読めない返しは何も書かない —— 空を書いて服を消さない
    assert _ask("すみません、わかりません") == {}
    assert _ask('{"だれか": "x"}') == {}


def test_the_pose_clerk_uses_the_same_road():
    """姿勢も服とまったく同じ穴だった。

    実測（4件・n=3）で本番の compile は **2/15**、`beat` は一度も書かれず、
    みおの姿勢まで `beat_b` に入った。名前で訊くと **20/25**（落ちた1件も
    取り違えではなく、「しゃがんで」を `kneeling` と訳しただけ）。
    """
    import asyncio

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply

        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    got = asyncio.run(chain.read_beats(
        _Ollama('{"各務 みお": "sitting on a bench", "平岡 すみれ": "standing behind her"}'),
        note="みおちゃんはベンチに座って。すみれちゃんは後ろに立ってて。",
        name_a="各務 みお", name_b="平岡 すみれ", model="m", num_ctx=1024))
    assert got == {"beat": "sitting on a bench",
                   "beat_b": "standing behind her"}
    # 欄の組が服とぶつからないこと
    assert chain._PER_PERSON["beat"][0] == ("beat", "beat_b")
    assert chain._PER_PERSON["wearing"][0] == ("wearing", "wearing_b")


def test_the_wardrobe_clerk_needs_two_names():
    """ソロでは呼ばない。名前が片方しか無ければ何も返さない。"""
    import asyncio

    got = asyncio.run(
        chain.read_wardrobe(None, note="赤いセーター", name_a="みお",
                            name_b="", model="m", num_ctx=1024)
    )
    assert got == {}


def _dress_session(sets_a, sets_b=None, wearing="", wearing_b=""):
    session = {
        "session_id": "s1", "mode": "duet", "inputs": {"locale": "ja"},
        "character": {"name_ja": "各務 みお", "wardrobe_sets": sets_a},
        "notebook": notebook.blank(), "craft": {}, "stage_ms": [],
    }
    if sets_b is not None:
        session["partner_character"] = {"name_ja": "平岡 すみれ",
                                        "character_id": "p",
                                        "wardrobe_sets": sets_b}
        session["inputs"]["partner_preset"] = "p"
    patch = {k: v for k, v in (("wearing", wearing), ("wearing_b", wearing_b)) if v}
    if patch:
        notebook.apply_patch(session["notebook"], patch)
    return session


_SETS_A = [
    {"key": "signature", "name_ja": "いつもの", "tags": ["blouse"], "props": ["pen"]},
    {"key": "casual_a", "name_ja": "休みの日", "tags": ["hoodie"], "props": ["tote_bag"]},
]
_SETS_B = [
    {"key": "signature", "name_ja": "いつもの", "tags": ["apron"], "props": ["shears"]},
    {"key": "casual_b", "name_ja": "よそ行き", "tags": ["black_dress"], "props": ["clutch"]},
]


class _PickOllama:
    def __init__(self, reply):
        self.reply = reply

    def generate_text_stream(self, prompt, **kw):
        async def _stream():
            yield {"type": "token", "text": self.reply}
        return _stream()


def _dress(session, reply):
    import asyncio
    asyncio.run(service._dress_the_cast(
        None, _PickOllama(reply), session, cfg={},
    ))
    return notebook.of(session)


def test_she_arrives_wearing_something():
    """総監督（2026-08-29）「default の衣装や持ち物がないので、会話開始後に
    いきなりおかしな状態に陥ることがあります」。

    **鍵だけ返させて、語はプリセットから展開する** —— 訳語のぶれも、勝手な
    一着も出ない。小物も身につけるものとして一緒に入る。
    """
    session = _dress_session(_SETS_A, _SETS_B)
    nb = _dress(session, '{"各務 みお": "casual_a", "平岡 すみれ": "casual_b"}')
    assert nb["wearing"] == "hoodie, tote_bag"
    assert nb["wearing_b"] == "black_dress, clutch"


def test_an_unknown_key_falls_back_to_the_signature():
    """持っていない鍵は捨てる。**モデルに服を作らせない。**"""
    session = _dress_session(_SETS_A, _SETS_B)
    nb = _dress(session, '{"各務 みお": "pyjamas", "平岡 すみれ": "???"}')
    assert nb["wearing"] == "blouse, pen"
    assert nb["wearing_b"] == "apron, shears"


def test_clothes_already_written_are_left_alone():
    """総監督が主題で服を指定していたら、そちらが先に入っている。**触らない。**"""
    session = _dress_session(_SETS_A, _SETS_B, wearing="sailor uniform")
    nb = _dress(session, '{"平岡 すみれ": "casual_b"}')
    assert nb["wearing"] == "sailor uniform"
    assert nb["wearing_b"] == "black_dress, clutch"


def test_a_solo_shoot_dresses_the_one_person():
    session = _dress_session(_SETS_A)
    nb = _dress(session, '{"各務 みお": "casual_a"}')
    assert nb["wearing"] == "hoodie, tote_bag"
    assert not str(nb.get("wearing_b") or "").strip()


def test_nothing_to_wear_writes_nothing():
    """服を持っていない子には、勝手な一着を生やさない。"""
    session = _dress_session([])
    nb = _dress(session, '{"各務 みお": "casual_a"}')
    assert not str(nb.get("wearing") or "").strip()


def test_the_partner_is_dressed_for_the_same_day():
    """実測（実機・2026-08-29）: すみれが休日でも仕事着で 3/3。

    `_theme_for_models` は「手帖に何か書かれたら主題を返さない」作りなので、
    **開始時に一人目を着せたその瞬間に空になる**。相方が入るときには渡すものが
    無く、条文の「場所も時刻も分からなければ `signature`」がそのまま効いて
    いた。着替えの判断に要るのは、いつ・どこ、だけ。
    """
    import asyncio

    seen: dict[str, str] = {}

    class _Spy:
        def generate_text_stream(self, prompt, **kw):
            seen["prompt"] = str(prompt)

            async def _stream():
                yield {"type": "token", "text": '{"平岡 すみれ": "casual_b"}'}
            return _stream()

    session = _dress_session(_SETS_A, _SETS_B, wearing="hoodie")
    session["inputs"]["theme"] = "休みの日、二人で近所の公園をぶらぶら"
    # 一人目は既に着ている（開始時に着せた状態）。手帖には書かれている
    asyncio.run(service._dress_the_cast(None, _Spy(), session, cfg={}))
    assert "近所の公園" in seen.get("prompt", ""), "主題が渡っていない"
    assert notebook.of(session)["wearing_b"] == "black_dress, clutch"


def test_the_fold_leaves_a_filled_beat_alone():
    """**姿勢が埋まっていたら、二度目は走らない。**

    総監督（2026-08-29）「初回の精度が上がってきたので、あまり意味をなさなく
    なっています」。実測（6ターン）—— 折り込みが書いたのは 1回、**残ったのは
    0回**。台本係は 6回書いて 3回残った。前の走行では、足した一語を次の台本係が
    丸ごと削って元に戻していた（LLM 二回・約19秒で正味ゼロ）。

    救済（空欄を埋める）だけ残して、上書き合戦をやめた。
    """
    import inspect
    src = inspect.getsource(service._fold_muse_after_talk)
    assert "折り込み（姿勢が埋まっているので走らず）" in src
    # 相方がいるときは、二人とも埋まっていて初めて止まる
    assert 'nb_now.get("beat_b")' in src


def test_the_fold_still_runs_when_a_posture_slot_is_empty():
    """救済は残す。実測でも、折り込みが役に立った唯一の回は空欄埋めだった。"""
    import inspect
    src = inspect.getsource(service._fold_muse_after_talk)
    i = src.index("折り込み（姿勢が埋まっているので走らず）")
    guard = src[:i]
    # 「埋まっている」ときだけ return する形（空なら通す）であること
    assert 'if str(nb_now.get("beat") or "").strip() and (' in guard


def test_one_turn_one_row_shows_all_three_layers():
    """総監督（2026-08-30）「シンプル化もしくは可観測性による透明化を」。

    「なぜコートが戻ったか」を答えるのに `rewrite_log` と `stage_ms` と
    `craft` と `notebook` を別々に読む必要があった。**総監督に見えないのも
    同じ理由。** 三層を一行に並べる。
    """
    session = {"chat": [], "notebook": notebook.blank(), "craft": {}}
    before = notebook.shot_snapshot(session["notebook"])
    notebook.apply_patch(session["notebook"],
                         {"scene": "a park at dusk", "wearing": "white blouse"})
    after = notebook.shot_snapshot(session["notebook"])
    service._turn_trace(
        session, line="白いブラウスで、公園に移動して",
        asked={"wearing", "scene", "frame"}, before=before, after=after,
    )
    row = session["turn_trace"][-1]
    assert row["asked"] == ["frame", "scene", "wearing"]
    assert set(row["moved"]) == {"scene", "wearing"}
    # **名指しされたのに動かなかった欄。** 総監督の「会話では移動したのに
    # 絵が動かない」は、いま何も起きないので札も出なかった
    assert row["missed"] == ["frame"]


def test_the_picture_layer_names_what_the_notebook_never_said():
    """古い服・古い場所が最終プロンプトに残る、というのが総監督の報告。

    タグは突き合わせているが**散文には検査が一段も無い**ので、そこから素通り
    する。**まず数える。** 直すのは数字を見てから。
    """
    session = {"chat": [], "notebook": notebook.blank(), "craft": {}}
    notebook.apply_patch(session["notebook"],
                         {"scene": "a park at dusk", "wearing": "white blouse"})
    service._turn_trace(session, line="x", asked=set(),
                        before={}, after=notebook.shot_snapshot(session["notebook"]))
    service._trace_picture(
        session, tags="white_blouse, park, sailor_uniform, rooftop", scene="x" * 90)
    stray = session["turn_trace"][-1]["picture"]["stray_tags"]
    assert "sailor_uniform" in stray and "rooftop" in stray
    assert "white_blouse" not in stray and "park" not in stray


def test_the_picture_layer_does_not_call_the_notebook_its_own_stranger():
    """手帖から素直に生まれた語を「知らない語」に数えない。

    実機（`4639e26f`）で BEAT `sitting, elbows on the desk` の
    `elbows_on_desk` と SCENE `classroom, near the window` の `near_window`
    が両方とも並んだ —— **「the」が挟まるだけ**で部分一致が外れる。表記ゆれの
    たびに穴が開くのは、この現場が語の一覧を増やし続けてきたのと同じ理由。
    """
    session = {"chat": [], "notebook": notebook.blank(), "craft": {}}
    notebook.apply_patch(session["notebook"], {
        "scene": "classroom, near the window",
        "beat": "sitting, elbows on the desk",
    })
    service._turn_trace(session, line="x", asked=set(),
                        before={}, after=notebook.shot_snapshot(session["notebook"]))
    service._trace_picture(
        session, tags="elbows_on_desk, near_window, rooftop", scene="x" * 90)
    stray = session["turn_trace"][-1]["picture"]["stray_tags"]
    assert stray == ["rooftop"]


def test_the_trace_is_a_ring_and_never_judges():
    """撮影1本ぶん残る。**判定には使わない。読むためだけ。**"""
    session = {"chat": [], "notebook": notebook.blank()}
    for i in range(service.TURN_TRACE_MAX + 5):
        service._turn_trace(session, line=f"line{i}", asked=set(),
                            before={}, after={})
    assert len(session["turn_trace"]) == service.TURN_TRACE_MAX
    assert session["turn_trace"][-1]["line"] == f"line{service.TURN_TRACE_MAX + 4}"

"""Rapid-change regression from muse_rapid_change_simulation (T3–T11, W1–W3)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, service, session_db, vitality
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def _tags(s):
    return str((s.get("craft") or {}).get("tags") or "")


@pytest.mark.asyncio
async def test_rapid_t3_to_t11_rooftop_asahi():
    """T3–T11 trajectory: hat, low angle, look-up rewrite, outfit swaps, casual."""
    scripts = {
        "セーラー": _scripter_block(
            intent="shot",
            scene="rooftop fence",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence",
            tags="rooftop, fence, sailor_collar, leaning, eye_level, looking_at_viewer",
            craft_scene="Rooftop lean in sailor uniform.",
        ),
        "ラムネ": _scripter_block(
            intent="shot",
            scene="rooftop fence, ramune bottle in hand",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence, holding ramune",
            tags="rooftop, fence, sailor_collar, leaning, ramune, looking_at_viewer",
            craft_scene="Same lean, holding ramune.",
        ),
        "麦わら": _scripter_block(
            intent="shot",
            scene="rooftop fence, ramune bottle in hand",
            frame="eye level, looking at viewer",
            wearing="sailor uniform, straw hat",
            beat="leaning on the fence, holding ramune",
            tags="rooftop, fence, sailor_collar, straw_hat, leaning, ramune, looking_at_viewer",
            craft_scene="Sailor with straw hat and ramune.",
        ),
        "煽って": _scripter_block(
            intent="shot",
            scene="rooftop fence, ramune bottle in hand",
            frame="low angle from below, she looks down into the lens",
            wearing="sailor uniform, straw hat",
            beat="leaning on the fence, holding ramune",
            tags="rooftop, fence, sailor_collar, straw_hat, leaning, ramune, from_below, low_angle, looking_down",
            craft_scene="Low angle; looks down to lens.",
        ),
        "見上げ": _scripter_block(
            intent="shot",
            scene="rooftop fence, ramune bottle in hand",
            frame="eye level three-quarter, looking up at the sky",
            wearing="sailor uniform, straw hat",
            beat="leaning on the fence, head tilted toward the sky, holding ramune",
            tags="rooftop, fence, sailor_collar, straw_hat, leaning, ramune, looking_up, eye_level",
            craft_scene="Looks up at the sky; frame rewritten as one story.",
        ),
        "サスペンダー": _scripter_block(
            intent="shot",
            scene="rooftop fence",
            frame="eye level three-quarter, looking up at the sky",
            wearing="white shirt, suspenders",
            beat="leaning on the fence, head tilted toward the sky",
            tags="rooftop, fence, white_shirt, suspenders, leaning, looking_up, eye_level",
            craft_scene="White shirt and suspenders; sailor gone.",
        ),
        "両方なし": _scripter_block(
            intent="shot",
            scene="rooftop fence",
            frame="eye level three-quarter, looking up at the sky",
            wearing="white shirt, suspenders",
            beat="leaning on the fence, head tilted toward the sky",
            tags="rooftop, fence, white_shirt, suspenders, leaning, looking_up, eye_level",
            craft_scene="No hat, no ramune.",
        ),
        "スマホ": _scripter_block(
            intent="shot",
            scene="rooftop bench",
            frame="eye level, looking at phone screen",
            wearing="white shirt, suspenders",
            beat="sitting on a bench, holding a phone",
            tags="rooftop, bench, white_shirt, suspenders, sitting, cellphone, looking_at_phone",
            craft_scene="Sitting with phone; lean/look-up gone.",
        ),
        "かき氷": _scripter_block(intent="casual", vibe="talking about shaved ice"),
        "カーディガン": _scripter_block(
            intent="shot",
            scene="rooftop bench",
            frame="low angle from below, she looks down into the lens",
            wearing="white shirt, suspenders, cardigan",
            beat="sitting on a bench, holding a phone",
            tags="rooftop, bench, white_shirt, suspenders, cardigan, sitting, cellphone, from_below, looking_down",
            craft_scene="Low angle again with cardigan; looking_up does not return.",
        ),
        "帽子だけ": _scripter_block(
            intent="shot",
            scene="rooftop bench",
            frame="low angle from below, she looks down into the lens",
            wearing="white shirt, suspenders, cardigan, straw hat",
            beat="sitting on a bench, holding a phone",
            tags="rooftop, bench, white_shirt, suspenders, cardigan, straw_hat, sitting, cellphone, from_below, looking_down",
            craft_scene="Hat only returns; ramune stays gone.",
        ),
    }
    db = FakeDb()
    ollama = NotebookOllama(scripts=scripts)
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "セーラーでフェンスにもたれて")
    await service.post_duet_chat(db, ollama, s, "ラムネ持って")
    assert "ramune" in _tags(s)

    # T3 hat on wearing, not leftover on ground in tags as costume/props ghost
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶせて")
    assert "straw_hat" in _tags(s)
    assert "straw" in s["notebook"]["wearing"]

    # T4 low angle
    await service.post_duet_chat(db, ollama, s, "やっぱり下から煽って")
    assert "looking_down" in _tags(s)
    assert "looking_up" not in _tags(s)

    # T5 look up — rewrite, no looking_down residue
    await service.post_duet_chat(db, ollama, s, "空を見上げて")
    assert "looking_up" in _tags(s)
    assert "looking_down" not in _tags(s)

    # T6 outfit replace
    await service.post_duet_chat(db, ollama, s, "白シャツにサスペンダー")
    assert "sailor_collar" not in _tags(s)
    assert "white_shirt" in _tags(s)

    # T7 both props off
    await service.post_duet_chat(db, ollama, s, "帽子とラムネ両方なし")
    assert "straw_hat" not in _tags(s)
    assert "ramune" not in _tags(s)

    # T8 phone sit
    await service.post_duet_chat(db, ollama, s, "ベンチでスマホ")
    assert "sitting" in _tags(s)
    assert "leaning" not in _tags(s)
    assert "looking_up" not in _tags(s)

    # T9 casual keeps craft
    before = _tags(s)
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味？")
    assert _tags(s) == before

    # T10 low angle + cardigan — looking_up must not resurrect
    await service.post_duet_chat(db, ollama, s, "また煽って、カーディガン羽織って")
    assert "looking_down" in _tags(s)
    assert "looking_up" not in _tags(s)
    assert "cardigan" in _tags(s)

    # T11 hat only — ramune stays gone
    await service.post_duet_chat(db, ollama, s, "帽子だけ戻す")
    assert "straw_hat" in _tags(s)
    assert "ramune" not in _tags(s)


@pytest.mark.asyncio
async def test_rapid_w1_w3_asymmetric_partner():
    """W1–W3: partner beat/wearing stay separated across relation changes."""
    scripts = {
        "読書": _scripter_block(
            intent="shot",
            scene="sunlit room with two chairs",
            frame="eye level, two girls, A seated reading, B standing nearby",
            wearing="soft cardigan",
            beat="sitting in a chair, reading a book",
            vibe="",
            tags="2girls, room, chair, book, sitting, reading, cardigan, standing",
            craft_scene="A sits reading; B stands nearby.",
        ),
        # Force wearing_b / beat_b via labelled extras in raw string
    }
    # Extend scripts with partner fields using raw labelled blocks.
    scripts["読書"] = """
INTENT: shot
SCENE: sunlit room with two chairs
FRAME: eye level; A seated with book, B standing beside her
WEARING: soft cardigan
BEAT: sitting in a chair, reading a book
WEARING_B: sleeveless dress
BEAT_B: standing beside the chair
CLEAR_OPEN: no
UNCHANGED: none
TAGS_SHARED: 2girls, room, chair, book
TAGS_A: sitting, reading, cardigan
TAGS_B: standing, sleeveless_dress
CRAFT_SCENE: A reads in a chair; B stands in a sleeveless dress.
""".strip()
    scripts["肩"] = """
INTENT: shot
SCENE: sunlit room with two chairs
FRAME: low angle; A hand on B's shoulder, both looking down toward lens
WEARING: soft cardigan
BEAT: standing, hand on partner's shoulder
WEARING_B: sleeveless dress
BEAT_B: standing close, receiving the hand on her shoulder
CLEAR_OPEN: no
UNCHANGED: none
TAGS_SHARED: 2girls, room, from_below, looking_down, hand_on_another's_shoulder
TAGS_A: cardigan, standing
TAGS_B: sleeveless_dress, standing
CRAFT_SCENE: Both standing; hand on shoulder; low angle looking down.
""".strip()
    scripts["二人立ち"] = """
INTENT: shot
SCENE: sunlit room
FRAME: eye level, two girls standing side by side looking at viewer
WEARING: soft cardigan
BEAT: standing side by side
WEARING_B: sleeveless dress
BEAT_B: standing side by side
CLEAR_OPEN: no
UNCHANGED: none
TAGS_SHARED: 2girls, room, standing, side-by-side, looking_at_viewer
TAGS_A: cardigan
TAGS_B: sleeveless_dress
CRAFT_SCENE: Both standing side by side; sitting/reading gone.
""".strip()

    db = FakeDb()
    ollama = NotebookOllama(scripts=scripts)
    s = await _duet_session(db, partner_preset="p2")
    s["mode"] = "duet"
    s["inputs"]["partner_preset"] = "p2"
    s["character"] = {
        "identity_tags": ["1girl", "silver_hair"],
        "name_ja": "あさひ",
        "personality": {}, "palette": [], "signature_prop": "",
    }
    s["partner_character"] = {
        "character_id": "p2",
        "identity_tags": ["1girl", "brown_hair"],
        "name_ja": "みなも",
        "personality": {}, "palette": [], "signature_prop": "",
    }
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "あさひは椅子で読書、みなもは立ってて")
    assert "sitting" in _tags(s) or "reading" in _tags(s)
    assert "sleeveless" in s["notebook"]["wearing_b"] or "dress" in s["notebook"]["wearing_b"]
    assert "reading" in s["notebook"]["beat"]
    assert "standing" in s["notebook"]["beat_b"]

    await service.post_duet_chat(db, ollama, s, "肩に手、ローアングル")
    assert "looking_down" in _tags(s)
    assert "looking_up" not in _tags(s)
    # Sitting/reading should leave A's beat
    assert "sitting" not in s["notebook"]["beat"]
    assert "reading" not in s["notebook"]["beat"]

    await service.post_duet_chat(db, ollama, s, "二人で立って")
    assert "sitting" not in _tags(s)
    assert "reading" not in _tags(s)
    assert "standing" in s["notebook"]["beat"]
    assert "standing" in s["notebook"]["beat_b"]


def test_patch_is_stored_verbatim():
    """`apply_patch` writes what the scripter said, whole.

    Gaze phrases used to be scrubbed out of BEAT here by regex. Keeping BEAT
    free of gaze is a rule in SCRIPTER_SYSTEM now — a word list could not tell
    the pose「見上げる」from the lens「見上げる」, and shipped whatever it missed.
    """
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "beat": "standing, head tilted back",
        "frame": "eye level, she looks up at the sky",
    })
    assert nb["beat"] == "standing, head tilted back"
    assert nb["frame"] == "eye level, she looks up at the sky"


def test_low_angle_with_looking_up_is_accepted_not_thrown_away():
    """An awkward camera stack ships; it no longer discards the whole compile.

    `from_below + looking_up` used to be a hard refusal, which threw out the
    tags and craft_scene wholesale — including the wardrobe and location changes
    that happened to be compiled in the same turn. The rule stays in
    SCRIPTER_SYSTEM; a stale outfit is worse than an odd angle, and the odd
    angle is visible on the board and fixable in the next line.
    """
    raw = _scripter_block(
        intent="shot",
        frame="low angle",
        beat="standing",
        wearing="shirt",
        tags="from_below, looking_up, shirt",
        craft_scene="Awkward but shippable.",
    )
    result = notebook.validate_scripter(notebook.parse_scripter(raw))
    assert result["valid"] is True
    assert "shirt" in result["tags"]


def test_shot_with_nothing_to_compile_is_still_refused():
    """The one hard refusal left: intent says shot, but there is no craft."""
    raw = _scripter_block(intent="shot", wearing="shirt", tags="", craft_scene="")
    result = notebook.validate_scripter(notebook.parse_scripter(raw))
    assert result["valid"] is False
    assert result["tags"] == ""
    assert result["craft_scene"] == ""


def test_parse_scripter_json_schema_shape():
    raw = """{
      "intent": "shot",
      "wearing": "straw hat, sailor uniform",
      "beat": "leaning, looking_up",
      "frame": "eye level, looking at viewer",
      "tags": "straw_hat, sailor_collar, leaning, looking_at_viewer",
      "craft_scene": "Rooftop lean."
    }"""
    result = notebook.validate_scripter(notebook.parse_scripter(raw))
    assert result["valid"] is True
    assert result["intent"] == "shot"
    assert result["patch"]["beat"] == "leaning, looking_up"
    assert "straw_hat" in result["tags"]


@pytest.mark.asyncio
async def test_invalid_scripter_does_not_overwrite_craft():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat",
            beat="standing",
            frame="eye level",
            tags="straw_hat, standing, looking_at_viewer",
            craft_scene="Hat on.",
        ),
        "壊す": (
            "INTENT: shot\nWEARING: jacket\nBEAT: standing\nFRAME: low angle\n"
            "CLEAR_OPEN: no\nUNCHANGED: none\n"
            "TAGS: from_below, looking_up, jacket\n"
            "CRAFT_SCENE: Broken.\n"
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子")
    before = _tags(s)
    assert "straw_hat" in before
    await service.post_duet_chat(db, ollama, s, "壊す指示で煽りと見上げ同時")
    assert _tags(s) == before
    assert s.get("craft_dirty") is True


def test_partner_flat_tags_are_flagged_but_shipped():
    """W-Muse wants split bags, but an unsplit one must not freeze the picture.

    A flat TAGS bag used to be thrown away whole, taking that turn's wardrobe
    and location with it — and the DENSIFY retry passed the same `partner=True`
    so it failed identically, leaving craft stuck on the last good compile for
    the rest of the session. It is flagged for the single repair pass now, and
    if the repair still comes back flat it ships: muddled attribution is visible
    on the board, a silently stale outfit is not.
    """
    raw = """
INTENT: shot
WEARING: cardigan
BEAT: sitting
WEARING_B: dress
BEAT_B: standing
TAGS: 2girls, cardigan, dress, sitting, standing
CRAFT_SCENE: Two girls.
""".strip()
    result = notebook.validate_scripter(notebook.parse_scripter(raw), partner=True)
    assert result["valid"] is True
    assert result.get("refuse_reason") == "w_muse_tags_unsplit"
    assert "cardigan" in result["tags"] and "dress" in result["tags"]
    assert result["craft_scene"] == "Two girls."


def test_partner_split_tags_are_merged_in_order():
    raw = """
INTENT: shot
WEARING: cardigan
WEARING_B: dress
TAGS_SHARED: 2girls, park
TAGS_A: cardigan, sitting
TAGS_B: dress, standing
CRAFT_SCENE: Two girls in a park.
""".strip()
    result = notebook.validate_scripter(notebook.parse_scripter(raw), partner=True)
    assert result["valid"] is True
    assert result.get("refuse_reason") is None
    assert result["tags"] == "2girls, park, cardigan, sitting, dress, standing"


def test_partner_split_tags_accepted():
    raw = """
INTENT: shot
WEARING: cardigan
BEAT: sitting
WEARING_B: dress
BEAT_B: standing
TAGS_SHARED: 2girls, room
TAGS_A: cardigan, sitting
TAGS_B: dress, standing
CRAFT_SCENE: Two girls.
""".strip()
    result = notebook.validate_scripter(notebook.parse_scripter(raw), partner=True)
    assert result["valid"] is True
    assert "cardigan" in result["tags"]
    assert "dress" in result["tags"]


def test_guard_partner_patch_drops_the_partner_card_on_a_solo_shoot():
    """Structural: nobody is standing in the B slot, so it cannot be written."""
    patch = {
        "wearing": "cardigan", "beat": "sitting",
        "wearing_b": "nobody is wearing this", "beat_b": "nobody is doing this",
    }
    out = notebook.guard_partner_patch(dict(patch), partner=False)
    assert out["wearing"] == "cardigan"
    assert "wearing_b" not in out
    assert "beat_b" not in out


def test_guard_partner_patch_keeps_both_cards_on_a_partner_shoot():
    """Who an edit was addressed to is the scripter's call, not a regex's.

    This used to drop the other Muse's edits whenever a line named one Muse
    without also saying 二人 / ふたり / 一緒 / おそろ / 両方 — which is most
    lines, so a change meant for both routinely landed on one of them.
    """
    patch = {
        "wearing": "cardigan", "beat": "sitting",
        "wearing_b": "matching cardigan", "beat_b": "sitting beside her",
    }
    out = notebook.guard_partner_patch(dict(patch), partner=True)
    assert out == patch


def test_scripter_status_message_is_honest_in_both_locales():
    """Soft wait copy — never guesses which row from the showrunner's wording."""
    assert "合わせ" in service._scripter_status_message()
    assert "moment" in service._scripter_status_message(locale="en").lower()
    assert service._scripter_status_message(soft=True) == "…"


def test_flash_key_comes_from_the_patch_not_the_showrunners_wording():
    """Which notebook row pulses is read off what the scripter actually changed."""
    assert vitality.notebook_flash_key({"wearing": "yukata"}) == "wearing"
    assert vitality.notebook_flash_key({"scene": "park"}) == "scene"
    assert vitality.notebook_flash_key({"frame": "low angle"}) == "frame"
    # Wardrobe outranks camera when a turn moved several rows.
    assert vitality.notebook_flash_key(
        {"frame": "low angle", "wearing": "yukata"},
    ) == "wearing"
    # Nothing concrete moved.
    assert vitality.notebook_flash_key({}) == "vibe"
    assert vitality.notebook_flash_key({"wearing": ""}) == "vibe"

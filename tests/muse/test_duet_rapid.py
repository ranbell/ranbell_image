"""Rapid-change regression from muse_rapid_change_simulation (T3–T11, W1–W3)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, shared, vitality
from tests.muse.test_duet_notebook import _scripter_block  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)


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

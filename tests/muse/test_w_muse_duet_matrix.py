import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from app.muse import crew, service
from app.characters import presets

# Representative sampling of Muses across different clubs and archetypes
import json

_JSON_PATH = Path(__file__).parent.parent.parent / "backend/app/characters/assets/personality_presets.json"

def get_preset_sync(preset_id: str) -> dict:
    with _JSON_PATH.open(encoding="utf-8") as f:
        presets_list = json.load(f)
    for p in presets_list:
        if p.get("id") == preset_id:
            return p
    return None

SAMPLE_MUSES = ["c001", "c002", "c003", "c005", "c010", "c015", "c020", "c030"]

@pytest.mark.parametrize("id_a", SAMPLE_MUSES)
@pytest.mark.parametrize("id_b", SAMPLE_MUSES)
def test_w_muse_duet_prompt_matrix(id_a, id_b):
    """Test 8x8 (64 pairs) of Muse pairings to ensure w_actress_duet_prompt works smoothly without errors."""
    p_a = get_preset_sync(id_a)
    p_b = get_preset_sync(id_b)
    char_a = presets.preset_to_character(p_a)
    char_b = presets.preset_to_character(p_b)

    prompt = crew.w_actress_duet_prompt(char_a, char_b, mode="talk")
    assert "W-MUSE" in prompt
    assert "W-MUSE CHEMISTRY & DYNAMICS" in prompt
    # Talk is voices only — TAG rules stay on prep/scripter.
    assert "2GIRLS IDENTITY & TAG RULES" not in prompt
    assert "A:" in prompt and "B:" in prompt

    prep = crew.w_actress_duet_prompt(char_a, char_b, mode="prep")
    assert "2GIRLS IDENTITY & TAG RULES" in prep


@pytest.mark.parametrize("mode", ["talk", "prep"])
@pytest.mark.parametrize("base_style", ["", "photoreal", "vivid", "flat"])
def test_w_muse_prompt_modes_and_styles(mode, base_style):
    """Test W-Muse prompt generation across different modes and style presets."""
    p_a = get_preset_sync("c001")
    p_b = get_preset_sync("c002")
    char_a = presets.preset_to_character(p_a)
    char_b = presets.preset_to_character(p_b)

    prompt = crew.w_actress_duet_prompt(char_a, char_b, mode=mode, base_style=base_style)
    assert len(prompt) > 200
    if mode == "prep":
        assert "W_DUET_PREP_OUTPUT" in prompt or "TAGS:" in prompt
    else:
        assert "W_DUET_TALK_OUTPUT" in prompt or "SAY:" in prompt

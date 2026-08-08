import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from app.characters import presets

import json

_JSON_PATH = Path(__file__).parent.parent.parent / "backend/app/characters/assets/personality_presets.json"

def get_preset_sync(preset_id: str) -> dict:
    with _JSON_PATH.open(encoding="utf-8") as f:
        presets_list = json.load(f)
    for p in presets_list:
        if p.get("id") == preset_id:
            return p
    return None

ALL_PRESET_IDS = [f"c{i:03d}" for i in range(1, 31)]


@pytest.mark.parametrize("preset_id", ALL_PRESET_IDS)
def test_muse_dialogue_attributes_completeness(preset_id):
    """Verify each of the 30 Muses has valid first_person_ja, user_address_ja, talk_quirks, and >=4 say examples."""
    preset = get_preset_sync(preset_id)
    assert preset is not None, f"Preset {preset_id} could not be loaded!"
    
    char = presets.preset_to_character(preset)

    # 1. First person checks
    first_person = char.get("first_person_ja") or preset.get("first_person_ja")
    assert first_person, f"{preset_id} ({preset.get('name_ja')}) missing first_person_ja!"
    assert isinstance(first_person, str) and len(first_person.strip()) > 0

    # 2. User address checks
    user_address = char.get("user_address_ja") or preset.get("user_address_ja")
    assert user_address, f"{preset_id} ({preset.get('name_ja')}) missing user_address_ja!"
    assert isinstance(user_address, str) and len(user_address.strip()) > 0

    # 3. Speech quirks checks
    talk_quirks = char.get("talk_quirks") or preset.get("talk_quirks")
    assert talk_quirks, f"{preset_id} ({preset.get('name_ja')}) missing talk_quirks!"
    assert isinstance(talk_quirks, str) and len(talk_quirks.strip()) >= 5

    # 4. Duet say examples checks (JA)
    say_examples = char.get("duet_say_examples") or preset.get("duet_say_examples")
    assert say_examples, f"{preset_id} ({preset.get('name_ja')}) missing duet_say_examples!"
    assert isinstance(say_examples, list)
    assert len(say_examples) >= 4, f"{preset_id} has fewer than 4 say examples!"
    for ex in say_examples:
        assert isinstance(ex, str) and len(ex.strip()) > 5

    # 5. First person checks (EN)
    first_person_en = char.get("first_person_en") or preset.get("first_person_en")
    assert first_person_en, f"{preset_id} ({preset.get('name')}) missing first_person_en!"
    assert isinstance(first_person_en, str) and len(first_person_en.strip()) > 0

    # 6. User address checks (EN)
    user_address_en = char.get("user_address_en") or preset.get("user_address_en")
    assert user_address_en, f"{preset_id} ({preset.get('name')}) missing user_address_en!"
    assert isinstance(user_address_en, str) and len(user_address_en.strip()) > 0

    # 7. Speech quirks checks (EN)
    talk_quirks_en = char.get("talk_quirks_en") or preset.get("talk_quirks_en")
    assert talk_quirks_en, f"{preset_id} ({preset.get('name')}) missing talk_quirks_en!"
    assert isinstance(talk_quirks_en, str) and len(talk_quirks_en.strip()) >= 5

    # 8. Duet say examples checks (EN)
    say_examples_en = char.get("duet_say_examples_en") or preset.get("duet_say_examples_en")
    assert say_examples_en, f"{preset_id} ({preset.get('name')}) missing duet_say_examples_en!"
    assert isinstance(say_examples_en, list)
    assert len(say_examples_en) >= 4, f"{preset_id} has fewer than 4 English say examples!"
    for ex in say_examples_en:
        assert isinstance(ex, str) and len(ex.strip()) > 5

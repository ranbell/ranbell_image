import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from app.characters import presets

import json

_JSON_PATH = Path(__file__).parent.parent.parent / "backend/app/characters/assets/personality_presets.json"
_JA_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

# At least one of these motif tokens must appear in quirks+JA examples.
# Keeps talk_quirks from drifting to an unrelated club again.
_MOTIF_TOKENS = {
    "c001": ("写真", "レンズ", "暗室", "カメラ"),
    "c002": ("図書", "貸出", "日付印", "本"),
    "c003": ("クラリネット", "音楽", "リード", "木管"),
    "c004": ("書", "墨", "硯", "はね"),
    "c005": ("屋上", "ジュース", "風"),
    "c006": ("勝負", "連敗", "リベンジ", "体育館", "拗ね"),
    "c007": ("放送", "マイク", "ヘッドホン", "オンエア", "声"),
    "c008": ("温室", "トマト", "植物", "園芸"),
    "c009": ("袖", "舞台", "裏方", "ヘッドセット", "埃"),
    "c010": ("将棋", "盤", "一手", "礼"),
    "c011": ("パン", "粉", "窯", "生地", "エプロン"),
    "c012": ("ゲーセン", "筐体", "コイン", "二位"),
    "c013": ("ラテ", "カウンター", "うさぎ", "カフェ", "ピッチャー"),
    "c014": ("花", "リボン", "包", "花屋"),
    "c015": ("コンビニ", "レジ", "深夜", "品出し"),
    "c016": ("水槽", "水族館", "解説", "魚"),
    "c017": ("レコード", "棚", "ジャケ", "盤"),
    "c018": ("受付", "病院", "カルテ", "犬", "クリニック"),
    "c019": ("カウンター", "麺", "お玉", "前掛け"),
    "c020": ("配達", "ヘルメット", "チャイム", "袋"),
    "c021": ("雨", "紫陽花", "軒下", "傘"),
    "c022": ("祭り", "浴衣", "屋台", "はぐ"),
    "c023": ("手袋", "雪", "片方"),
    "c024": ("桜", "携帯", "二百", "写真"),
    "c025": ("こたつ", "天板", "毛布"),
    "c026": ("プラネタリウム", "席", "台本", "星"),
    "c027": ("バス", "時刻", "十二分", "待合"),
    "c028": ("金魚", "すく", "ポイ", "祭り"),
    "c029": ("洗濯", "物干し", "ばさみ", "洗濯日和"),
    "c030": ("段ボール", "引っ越し", "箱", "ケトル"),
}


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
        assert _JA_RE.search(ex), (
            f"{preset_id} duet_say_examples must be Japanese, got: {ex!r}"
        )

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

    # 9. Motif alignment — quirks/examples must match the character, not another club
    voice_blob = talk_quirks + "\n" + "\n".join(say_examples)
    tokens = _MOTIF_TOKENS[preset_id]
    assert any(tok in voice_blob for tok in tokens), (
        f"{preset_id} ({preset.get('name_ja')}) talk_quirks/examples missing "
        f"motif tokens {tokens}: {voice_blob[:120]!r}"
    )

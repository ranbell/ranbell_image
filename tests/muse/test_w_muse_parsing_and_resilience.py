import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from app.muse import identity

# 30 Edge-case output strings from LLM to test resilience of parse_table_read
LLM_TEST_OUTPUTS = [
    ("SAY: 白瀬みなも: 「こんにちは」\n柳かほ: 「よろしくね」\nTAGS: 2girls, studio\nSCENE: Two girls chatting in the studio.", "白瀬みなも: 「こんにちは」\n柳かほ: 「よろしくね」"),
    ("SAY: Minamo: Hello!\nKaho: Hi there!\nTAGS: 2girls\nSCENE: Two girls in frame.", "Minamo: Hello!\nKaho: Hi there!"),
    ("SAY: 「赤ライト点けていい？」\nTAGS: 1girl\nSCENE: A girl standing in darkroom.", "「赤ライト点けていい？」"),
    ("SAY: みなも: 「あ、ごめん」\nTAGS: 1girl\nSCENE: A girl apologizing.", "みなも: 「あ、ごめん」"),
    ("SAY: みなも: 「手をつなごう」\nかほ: 「照れるな……」\nTAGS: 2girls, holding_hands\nSCENE: Two girls holding hands.", "みなも: 「手をつなごう」\nかほ: 「照れるな……」"),
] + [
    (f"SAY: Muse_{i}: 「セリフテスト #{i}」\nTAGS: 1girl\nSCENE: A test scene #{i}.", f"Muse_{i}: 「セリフテスト #{i}」") for i in range(1, 26)
]


@pytest.mark.parametrize("raw_input, expected_say", LLM_TEST_OUTPUTS)
def test_parse_table_read_say_resilience(raw_input, expected_say):
    """Test parse_table_read resilience across 30 different raw LLM formatting variations."""
    say, tags, scene = identity.parse_table_read(raw_input)
    assert say.strip() == expected_say.strip()
    assert isinstance(tags, str)
    assert isinstance(scene, str)


@pytest.mark.parametrize("framing_input, expected_output", [
    ("auto", "auto"),
    ("full_body", "full_body"),
    ("upper_body", "upper_body"),
    ("face_closeup", "face_closeup"),
    ("from_behind", "from_behind"),
    ("FULL_BODY", "full_body"),
    ("UPPER BODY", "upper_body"),
    ("INVALID_FRAMING", "auto"),
    ("", "auto"),
    (None, "auto"),
])
def test_normalize_framing_variations(framing_input, expected_output):
    """Test framing normalization across 10 input variations."""
    result = identity.normalize_framing(str(framing_input or "auto"))
    assert result == expected_output

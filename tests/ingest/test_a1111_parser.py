import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ingest.a1111_parser import parse_a1111


A1111_STANDARD = """\
1girl, masterpiece, best quality, long hair
Negative prompt: lowres, bad anatomy, bad hands
Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 12345, Model: v1-5-pruned
"""

A1111_NO_NEGATIVE = """\
1girl, solo, simple background
Steps: 20, Sampler: DPM++ 2M, CFG scale: 7
"""

A1111_MULTILINE_POSITIVE = """\
1girl, masterpiece,
best quality, long hair,
blue eyes
Negative prompt: lowres, bad anatomy
Steps: 20, Sampler: Euler, CFG scale: 7
"""


def test_standard_extraction():
    result = parse_a1111(A1111_STANDARD)
    assert result.positive_prompt == "1girl, masterpiece, best quality, long hair"
    assert result.negative_prompt == "lowres, bad anatomy, bad hands"
    assert result.extraction.method == "a1111"
    assert result.extraction.confidence == "high"
    assert result.model_info.steps == 20
    assert result.model_info.cfg_scale == 7.0
    assert result.model_info.seed == 12345
    assert result.model_info.sampler == "Euler a"
    assert result.model_info.model_name == "v1-5-pruned"


def test_no_negative():
    result = parse_a1111(A1111_NO_NEGATIVE)
    assert result.positive_prompt == "1girl, solo, simple background"
    assert result.negative_prompt is None
    assert result.model_info.steps == 20


def test_multiline_positive():
    result = parse_a1111(A1111_MULTILINE_POSITIVE)
    assert "1girl" in result.positive_prompt
    assert "blue eyes" in result.positive_prompt
    assert result.negative_prompt == "lowres, bad anatomy"


def test_raw_metadata_stored():
    result = parse_a1111(A1111_STANDARD)
    assert result.raw_metadata.format == "a1111"
    assert result.raw_metadata.hash != ""
    assert A1111_STANDARD.strip() in result.raw_metadata.content


def test_empty_string():
    result = parse_a1111("")
    assert result.positive_prompt is None
    assert result.negative_prompt is None
    assert result.extraction.method == "a1111"

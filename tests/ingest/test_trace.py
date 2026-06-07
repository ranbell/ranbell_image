import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ingest.trace import trace_prompts, direct_search_prompts

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_simple_clip_ksampler():
    wf = _load("simple_clip.json")
    pos, neg, warnings = trace_prompts(wf)
    assert pos == "1girl, masterpiece, best quality, long hair"
    assert "lowres" in neg
    assert warnings == []


def test_sdxl_ksampler():
    wf = _load("sdxl_clip.json")
    pos, neg, warnings = trace_prompts(wf)
    assert "masterpiece" in pos
    assert "long hair" in pos  # text_g + text_l combined
    assert "lowres" in neg


def test_sdxl_same_text_g_text_l_not_duplicated():
    wf = _load("sdxl_clip.json")
    pos, neg, _ = trace_prompts(wf)
    # negative node has same text_g and text_l — should not be duplicated
    assert neg.count("lowres") == 1


def test_impact_wildcard_prefers_populated_text():
    wf = _load("impact_wildcard.json")
    pos, neg, _ = trace_prompts(wf)
    assert pos == "long hair girl, masterpiece"  # populated_text preferred over wildcard_text
    assert "__hairstyle__" not in pos


def test_concat_node():
    wf = _load("concat_node.json")
    pos, neg, warnings = trace_prompts(wf)
    assert pos == "1girl, masterpiece, best quality"
    assert "lowres" in neg


def test_circular_link_protection():
    wf = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["2", 0]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["1", 0]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["1", 0],
                "negative": None,
                "sampler_name": "euler",
                "steps": 20,
                "cfg": 7.0,
            },
        },
    }
    pos, neg, warnings = trace_prompts(wf)
    # Should not loop infinitely; may return None
    assert any("循環" in w for w in warnings) or pos is None


def test_no_ksampler_returns_none():
    wf = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
    }
    pos, neg, warnings = trace_prompts(wf)
    assert pos is None
    assert neg is None
    assert any("KSampler" in w for w in warnings)


def test_direct_search_single_node():
    wf = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, masterpiece"}},
    }
    pos, neg, warnings = direct_search_prompts(wf)
    assert pos == "1girl, masterpiece"
    assert neg is None


def test_direct_search_title_classification():
    wf = {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "1girl, best quality"},
            "_meta": {"title": "Positive"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "lowres, bad anatomy"},
            "_meta": {"title": "Negative"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0], "steps": 20, "cfg": 7},
        },
    }
    pos, neg, _ = direct_search_prompts(wf)
    assert "1girl" in pos
    assert "lowres" in neg

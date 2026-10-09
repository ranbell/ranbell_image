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


def test_image_saver_string_literal_is_followed_not_the_longest_unused_text():
    """Anima workflows built on Image Saver wire CLIPTextEncode.text from a
    "String Literal (Image Saver)" node, whose text is in `string`. A longer
    String Literal sitting unconnected must not be taken instead."""
    wf = {
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": ["98", 0], "clip": ["45", 0]}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": ["99", 0], "clip": ["45", 0]}},
        "19": {"class_type": "KSampler", "inputs": {
            "seed": ["90", 0], "steps": 30, "cfg": 4.5, "sampler_name": "er_sde",
            "scheduler": "sgm_uniform", "denoise": 1.0, "model": ["44", 0],
            "positive": ["11", 0], "negative": ["12", 0], "latent_image": ["28", 0]}},
        "98": {"class_type": "String Literal (Image Saver)", "inputs": {"string": "1girl, witch, flying broom"}},
        "99": {"class_type": "String Literal (Image Saver)", "inputs": {"string": "bad_quality, bad_anatomy"}},
        "116": {"class_type": "String Literal (Image Saver)",
                "inputs": {"string": "masterpiece, best quality, an unused and much longer prompt " * 3}},
    }
    pos, neg, _ = trace_prompts(wf)
    assert pos == "1girl, witch, flying broom"
    assert neg == "bad_quality, bad_anatomy"


def test_core_primitive_string_value_is_followed():
    wf = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["30", 0], "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["7", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres", "clip": ["4", 1]}},
        "30": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "a quiet harbor at dawn"}},
    }
    pos, neg, _ = trace_prompts(wf)
    assert pos == "a quiet harbor at dawn"
    assert neg == "lowres"


def _enc(text):
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["4", 1]}}


def test_controlnet_apply_advanced_keeps_positive_and_negative_apart():
    """It takes both and hands them back on slots 0 (positive) and 1 (negative)."""
    wf = {
        "6": _enc("a girl in a garden"), "7": _enc("lowres, bad anatomy"),
        "20": {"class_type": "ControlNetApplyAdvanced", "inputs": {
            "positive": ["6", 0], "negative": ["7", 0], "control_net": ["21", 0], "image": ["22", 0]}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["20", 0], "negative": ["20", 1]}},
    }
    pos, neg, _ = trace_prompts(wf)
    assert (pos, neg) == ("a girl in a garden", "lowres, bad anatomy")


def test_single_conditioning_passthroughs_are_followed():
    wf = {
        "6": _enc("a lighthouse"), "7": _enc("blurry"),
        "10": {"class_type": "ControlNetApply", "inputs": {"conditioning": ["6", 0], "strength": 1.0}},
        "11": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["10", 0], "guidance": 3.5}},
        "12": {"class_type": "InpaintModelConditioning", "inputs": {
            "positive": ["11", 0], "negative": ["7", 0], "vae": ["2", 0], "pixels": ["9", 0]}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["12", 0], "negative": ["12", 1]}},
    }
    pos, neg, _ = trace_prompts(wf)
    assert (pos, neg) == ("a lighthouse", "blurry")


def test_sampler_custom_advanced_through_its_guider_not_ksampler_select():
    wf = {
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "26": _enc("a pumpkin lantern at night"),
        "22": {"class_type": "BasicGuider", "inputs": {"model": ["30", 0], "conditioning": ["26", 0]}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["25", 0], "guider": ["22", 0], "sampler": ["16", 0], "sigmas": ["17", 0]}},
    }
    pos, neg, _ = trace_prompts(wf)
    assert pos == "a pumpkin lantern at night" and neg is None


def test_cfg_guider_gives_both():
    wf = {
        "6": _enc("a red bicycle"), "7": _enc("watermark"),
        "22": {"class_type": "CFGGuider", "inputs": {"positive": ["6", 0], "negative": ["7", 0], "cfg": 4}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["22", 0]}},
    }
    assert trace_prompts(wf)[:2] == ("a red bicycle", "watermark")


def test_string_concatenate_uses_its_delimiter_as_separator():
    wf = {
        "30": {"class_type": "String Literal (Image Saver)", "inputs": {"string": "masterpiece"}},
        "31": {"class_type": "StringConcatenate", "inputs": {
            "string_a": ["30", 0], "string_b": "1girl, beach", "delimiter": ", "}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["31", 0], "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["7", 0]}},
        "7": _enc("lowres"),
    }
    assert trace_prompts(wf)[0] == "masterpiece, 1girl, beach"


def test_inspire_and_clownshark_samplers_are_samplers():
    for cls in ("KSamplerAdvanced //Inspire", "ClownsharKSampler_Beta"):
        wf = {"6": _enc("a cat"), "7": _enc("lowres"),
              "8": {"class_type": cls, "inputs": {"positive": ["6", 0], "negative": ["7", 0]}}}
        assert trace_prompts(wf)[:2] == ("a cat", "lowres"), cls


def test_wd14_tagger_feeding_the_prompt_gives_its_tags():
    wf = {
        "124": {"class_type": "WD14Tagger|pysssss", "inputs": {"image": ["115", 0], "tags": "1girl, solo, smile"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["124", 0], "clip": ["4", 1]}},
        "7": _enc("lowres"),
        "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["7", 0]}},
    }
    assert trace_prompts(wf)[0] == "1girl, solo, smile"


def test_zeroed_out_negative_is_no_text_not_the_positive():
    wf = {
        "6": _enc("a winter forest"),
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["7", 0]}},
    }
    assert trace_prompts(wf)[:2] == ("a winter forest", None)


def test_efficient_loader_gives_each_side_its_own_text():
    wf = {
        "29": {"class_type": "Efficient Loader", "inputs": {
            "ckpt_name": "model.safetensors", "positive": "1girl, sundress", "negative": "blurry, watermark"}},
        "3": {"class_type": "KSampler", "inputs": {"positive": ["29", 1], "negative": ["29", 2]}},
    }
    assert trace_prompts(wf)[:2] == ("1girl, sundress", "blurry, watermark")

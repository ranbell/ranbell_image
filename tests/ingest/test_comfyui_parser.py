import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ingest.comfyui_parser import _resolve_scalar, extract_params_from_workflow

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# _resolve_scalar unit tests
# ---------------------------------------------------------------------------

def test_resolve_scalar_direct_value():
    assert _resolve_scalar({}, 42) == 42
    assert _resolve_scalar({}, "euler_a") == "euler_a"
    assert _resolve_scalar({}, 7.5) == 7.5
    assert _resolve_scalar({}, None) is None


def test_resolve_scalar_single_hop():
    workflow = {
        "94": {"class_type": "PrimitiveNode", "inputs": {"value": 20}}
    }
    assert _resolve_scalar(workflow, ["94", 0]) == 20


def test_resolve_scalar_multi_hop():
    workflow = {
        "94": {"class_type": "PrimitiveNode", "inputs": {"value": ["95", 0]}},
        "95": {"class_type": "PrimitiveNode", "inputs": {"value": 30}},
    }
    assert _resolve_scalar(workflow, ["94", 0]) == 30


def test_resolve_scalar_circular_returns_none():
    workflow = {
        "1": {"inputs": {"value": ["2", 0]}},
        "2": {"inputs": {"value": ["1", 0]}},
    }
    assert _resolve_scalar(workflow, ["1", 0]) is None


def test_resolve_scalar_missing_node_returns_none():
    assert _resolve_scalar({}, ["999", 0]) is None


# ---------------------------------------------------------------------------
# extract_params_from_workflow integration tests
# ---------------------------------------------------------------------------

def test_direct_values_unchanged():
    wf = _load("simple_clip.json")
    params = extract_params_from_workflow(wf)
    assert params["Steps"] == "20"
    assert params["CFG scale"] == "7.0"
    assert params["Seed"] == "42"
    assert params["Sampler"] == "euler_a"


def test_referenced_steps_and_seed_resolved():
    wf = _load("primitive_refs.json")
    params = extract_params_from_workflow(wf)
    assert params["Steps"] == "20"
    assert params["Seed"] == "12345"
    assert params["CFG scale"] == "4.0"
    assert params["Sampler"] == "euler_a"


def test_user_workflow_pattern():
    """Simulate the user-reported KSampler where steps/seed/cfg/sampler/scheduler are all refs."""
    workflow = {
        "90": {"class_type": "PrimitiveNode", "inputs": {"value": 98765}},
        "91": {"class_type": "PrimitiveNode", "inputs": {"value": "dpmpp_2m"}},
        "92": {"class_type": "PrimitiveNode", "inputs": {"value": "karras"}},
        "93": {"class_type": "PrimitiveNode", "inputs": {"value": 5.0}},
        "94": {"class_type": "PrimitiveNode", "inputs": {"value": 25}},
        "19": {
            "class_type": "KSampler",
            "inputs": {
                "seed": ["90", 0],
                "steps": ["94", 0],
                "cfg": ["93", 0],
                "sampler_name": ["91", 0],
                "scheduler": ["92", 0],
                "denoise": 1,
                "model": ["44", 0],
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["28", 0],
            },
        },
    }
    params = extract_params_from_workflow(workflow)
    assert params["Steps"] == "25"
    assert params["Seed"] == "98765"
    assert params["CFG scale"] == "5.0"
    assert params["Sampler"] == "dpmpp_2m"
    assert params["Schedule type"] == "karras"


def test_mixed_direct_and_ref():
    """cfg is direct, steps is a reference."""
    workflow = {
        "94": {"class_type": "PrimitiveNode", "inputs": {"value": 15}},
        "k": {
            "class_type": "KSampler",
            "inputs": {
                "sampler_name": "euler",
                "scheduler": "normal",
                "steps": ["94", 0],
                "cfg": 7.0,
                "seed": 42,
                "positive": ["p", 0],
                "negative": ["n", 0],
                "model": ["m", 0],
                "latent_image": ["l", 0],
            },
        },
    }
    params = extract_params_from_workflow(workflow)
    assert params["Steps"] == "15"
    assert params["CFG scale"] == "7.0"
    assert params["Seed"] == "42"

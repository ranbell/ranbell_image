"""ComfyUI workflow format helpers: node lookup and text extraction."""
from __future__ import annotations

from .known_nodes import (
    CLIP_TEXT_ENCODE_CLASSES,
    SAMPLER_CLASSES,
    TEXT_INPUT_FIELDS,
    WILDCARD_ENCODE_CLASSES,
)


def detect_format(png_info: dict) -> str:
    """Return 'a1111', 'comfyui', or 'unknown' based on PNG metadata chunks."""
    if png_info.get("parameters"):
        return "a1111"
    if png_info.get("prompt") or png_info.get("workflow"):
        return "comfyui"
    return "unknown"


def find_nodes_by_class(workflow: dict, classes: list[str]) -> list[dict]:
    """Return all nodes whose class_type is in *classes*."""
    result: list[dict] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in classes:
            result.append({"_id": node_id, **node})
    return result


def get_text_from_node(node: dict) -> str | None:
    """Extract text from a node's inputs, trying TEXT_INPUT_FIELDS in priority order."""
    inputs = node.get("inputs") or {}
    for field in TEXT_INPUT_FIELDS:
        value = inputs.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def get_sdxl_text(node: dict) -> str | None:
    """Combine text_g / text_l from a CLIPTextEncodeSDXL node."""
    inputs = node.get("inputs") or {}
    text_g = inputs.get("text_g", "") or ""
    text_l = inputs.get("text_l", "") or ""
    text_g = text_g if isinstance(text_g, str) else ""
    text_l = text_l if isinstance(text_l, str) else ""

    if not text_g and not text_l:
        return None
    if text_g == text_l or not text_l:
        return text_g or text_l
    if not text_g:
        return text_l
    return f"{text_g}\n{text_l}"


def extract_params_from_workflow(workflow: dict) -> dict:
    """Extract generation params (model, sampler, steps, cfg, lora) from ComfyUI prompt JSON."""
    params: dict = {}

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type", "")
        inputs = node.get("inputs") or {}

        if cls in ("CheckpointLoaderSimple", "CheckpointLoader"):
            name = inputs.get("ckpt_name", "")
            if name and isinstance(name, str):
                from ._utils import basename
                params["Model"] = basename(name)

        elif cls == "UNETLoader":
            name = inputs.get("unet_name", "")
            if name and isinstance(name, str) and "Model" not in params:
                from ._utils import basename
                params["Model"] = basename(name)

        elif cls in ("LoraLoader", "LoRALoader", "LoraLoaderModelOnly"):
            name = inputs.get("lora_name", "")
            if name and isinstance(name, str):
                from ._utils import basename
                loras = params.get("Lora", "")
                params["Lora"] = f"{loras}, {basename(name)}" if loras else basename(name)

        elif cls in SAMPLER_CLASSES:
            sampler = inputs.get("sampler_name", "")
            if isinstance(sampler, list):
                sampler = _resolve_scalar(workflow, sampler)
            scheduler = inputs.get("scheduler", "")
            if isinstance(scheduler, list):
                scheduler = _resolve_scalar(workflow, scheduler)
            if sampler and isinstance(sampler, str):
                params["Sampler"] = sampler
            if scheduler and isinstance(scheduler, str):
                params["Schedule type"] = scheduler
            steps = inputs.get("steps")
            if isinstance(steps, list):
                steps = _resolve_scalar(workflow, steps)
            if steps is not None:
                params["Steps"] = str(steps)
            cfg = inputs.get("cfg")
            if isinstance(cfg, list):
                cfg = _resolve_scalar(workflow, cfg)
            if cfg is not None:
                params["CFG scale"] = str(cfg)
            seed = inputs.get("seed")
            if isinstance(seed, list):
                seed = _resolve_scalar(workflow, seed)
            if seed is None:
                seed = inputs.get("noise_seed")
                if isinstance(seed, list):
                    seed = _resolve_scalar(workflow, seed)
            if seed is not None:
                params["Seed"] = str(seed)

    return params


def extract_model_from_visual_workflow(workflow: dict) -> str:
    """Extract model name from ComfyUI visual workflow (nodes list format)."""
    nodes = workflow.get("nodes", [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type", "")
        widgets = node.get("widgets_values", [])
        if node_type in ("CheckpointLoaderSimple", "CheckpointLoader") and widgets:
            from ._utils import basename
            return basename(str(widgets[0]))
        if node_type == "UNETLoader" and widgets:
            from ._utils import basename
            return basename(str(widgets[0]))
    return ""


def is_text_node(node: dict) -> bool:
    """Return True if this node can yield a text string."""
    cls = node.get("class_type", "")
    return cls in CLIP_TEXT_ENCODE_CLASSES or cls in WILDCARD_ENCODE_CLASSES


def _resolve_scalar(workflow: dict, link, visited: frozenset | None = None):
    """Follow [node_id, slot] references recursively until a direct scalar value is found.

    Returns the resolved scalar (int, float, str) or None if unresolvable.
    Circular references are detected via *visited* and return None.
    """
    if not isinstance(link, list) or len(link) < 1:
        return link

    if visited is None:
        visited = frozenset()

    node_id = str(link[0])
    if node_id in visited:
        return None

    node = workflow.get(node_id)
    if not isinstance(node, dict):
        return None

    inputs = node.get("inputs") or {}

    # Return the first direct scalar found in this node's inputs
    for v in inputs.values():
        if not isinstance(v, list):
            return v

    # All inputs are references — recurse into them
    visited = visited | {node_id}
    for v in inputs.values():
        if isinstance(v, list):
            result = _resolve_scalar(workflow, v, visited)
            if result is not None:
                return result

    return None

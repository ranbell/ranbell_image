"""KSampler reverse-lookup and recursive link resolution for ComfyUI workflows."""
from __future__ import annotations

from .known_nodes import (
    CLIP_TEXT_ENCODE_CLASSES,
    CONCAT_NODE_CLASSES,
    NEGATIVE_KEYWORDS,
    NEGATIVE_TITLE_KEYWORDS,
    POSITIVE_TITLE_KEYWORDS,
    SAMPLER_CLASSES,
    TEXT_INPUT_FIELDS,
    TEXT_SHOW_NODE_CLASSES,
    WILDCARD_ENCODE_CLASSES,
)
from .comfyui_parser import get_sdxl_text, get_text_from_node


def trace_prompts(
    workflow: dict,
    visual_workflow: dict | None = None,
) -> tuple[str | None, str | None, list[str]]:
    """
    Extract positive/negative prompts via KSampler reverse-lookup.

    If visual_workflow is provided, text can also be retrieved from the
    "monitoring output" (STRING slot of [CONDITIONING, STRING]) of LLM encode nodes, etc.

    Returns:
        (positive_prompt, negative_prompt, warnings)
    """
    warnings: list[str] = []

    # Build id → widgets_values index and output link map from the visual workflow
    visual_nodes, out_map = _build_visual_index(visual_workflow)

    # Find the KSampler node
    sampler_node = _find_sampler(workflow)
    if sampler_node is None:
        warnings.append("KSampler node not found")
        return None, None, warnings

    inputs = sampler_node.get("inputs") or {}
    positive_link = inputs.get("positive")
    negative_link = inputs.get("negative")

    positive_text = _resolve_text(workflow, positive_link, set(), warnings, visual_nodes, out_map) if positive_link else None
    negative_text = _resolve_text(workflow, negative_link, set(), warnings, visual_nodes, out_map) if negative_link else None

    if positive_text is None and negative_text is None:
        warnings.append("Could not resolve text from KSampler positive/negative links")

    return positive_text, negative_text, warnings


def direct_search_prompts(
    workflow: dict,
) -> tuple[str | None, str | None, list[str]]:
    """
    Directly search CLIPTextEncode-type nodes to infer positive/negative (last resort).

    Returns:
        (positive_prompt, negative_prompt, warnings)
    """
    warnings: list[str] = [
        "positive/negative inferred via direct search; misclassification possible"
    ]

    text_nodes = _collect_text_nodes(workflow)
    if not text_nodes:
        return None, None, warnings

    if len(text_nodes) == 1:
        text = _get_node_text(text_nodes[0])
        return text, None, warnings

    # Classification by title (highest priority)
    positive, negative = _classify_by_title(text_nodes)
    if positive is not None or negative is not None:
        return positive, negative, warnings

    # Classification by content (keyword density)
    positive, negative = _classify_by_content(text_nodes)
    if positive is not None or negative is not None:
        return positive, negative, warnings

    # Estimation by order (lower node ID is assumed positive)
    sorted_nodes = sorted(text_nodes, key=lambda n: _node_sort_key(n))
    positive_text = _get_node_text(sorted_nodes[0])
    negative_text = _get_node_text(sorted_nodes[1]) if len(sorted_nodes) >= 2 else None
    if len(sorted_nodes) > 2:
        warnings.append(f"Found {len(sorted_nodes)} CLIPTextEncode nodes; using only the first two")

    return positive_text, negative_text, warnings


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_visual_index(
    visual_workflow: dict | None,
) -> tuple[dict[str, list], dict[tuple[str, int], list[str]]]:
    """Build widgets_values map and output link map from a visual workflow.

    Returns:
        wv_map:  node_id -> widgets_values
        out_map: (src_node_id, src_slot) -> [dst_node_id, ...]
    """
    if not visual_workflow:
        return {}, {}

    wv_map: dict[str, list] = {}
    for node in visual_workflow.get("nodes", []):
        if isinstance(node, dict):
            vid = str(node.get("id", ""))
            if vid:
                wv_map[vid] = node.get("widgets_values") or []

    # links array format: [link_id, src_node, src_slot, dst_node, dst_slot, type]
    out_map: dict[tuple[str, int], list[str]] = {}
    for lk in visual_workflow.get("links", []):
        if isinstance(lk, list) and len(lk) >= 4:
            src_id = str(lk[1])
            src_slot = int(lk[2])
            dst_id = str(lk[3])
            out_map.setdefault((src_id, src_slot), []).append(dst_id)

    return wv_map, out_map


def _find_monitoring_text(
    visual_nodes: dict[str, list],
    out_map: dict[tuple[str, int], list[str]],
    node_id: str,
    slot: int,
    warnings: list[str],
    visited: set,
) -> str | None:
    """Retrieve text from widgets_values of sibling nodes connected to node_id's output slot.

    Uses the visual workflow's links array to identify nodes (such as monitoring ShowText nodes)
    that branch from the same output slot we followed. Nodes in visited are excluded.
    """
    candidates: list[str] = []
    for dst_id in out_map.get((node_id, slot), []):
        if dst_id in visited:
            continue
        node_texts = _extract_wv_texts(visual_nodes.get(dst_id, []), min_len=10)
        if node_texts:
            candidates.append("\n".join(node_texts))
    if candidates:
        warnings.append("rescued_from_widget")
        return max(candidates, key=len)
    return None


def _extract_wv_texts(wv_list: list, min_len: int = 0) -> list[str]:
    """Extract valid text entries from a widgets_values list.

    ShowText|pysssss and similar nodes may have widgets_values[0] itself be a list.
    Handles both str and list[str] forms and flattens them.
    """
    texts: list[str] = []
    for item in wv_list:
        if isinstance(item, str):
            s = item.strip()
            if len(s) > min_len:
                texts.append(s)
        elif isinstance(item, list):
            for sub in item:
                if isinstance(sub, str):
                    s = sub.strip()
                    if len(s) > min_len:
                        texts.append(s)
    return texts


def _find_sampler(workflow: dict) -> dict | None:
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in SAMPLER_CLASSES:
            return node
    return None


def _resolve_text(
    workflow: dict,
    link: list | None,
    visited: set,
    warnings: list[str],
    visual_nodes: dict[str, list] | None = None,
    out_map: dict[tuple[str, int], list[str]] | None = None,
) -> str | None:
    """
    Recursively follow workflow links to resolve text.

    link is a list in ComfyUI's [node_id, output_slot] format.
    """
    if not link or not isinstance(link, list) or len(link) < 1:
        return None

    node_id = str(link[0])
    slot = int(link[1]) if len(link) > 1 else 0

    if node_id in visited:
        warnings.append(f"Circular link detected: node_id={node_id}")
        return None
    visited = visited | {node_id}

    node = workflow.get(node_id)
    if not isinstance(node, dict):
        return None

    cls = node.get("class_type", "")
    inputs = node.get("inputs") or {}

    vn = visual_nodes or {}
    om = out_map or {}

    # SDXL family
    if cls in ("CLIPTextEncodeSDXL", "CLIPTextEncodeSDXLRefiner"):
        # Get directly if text_g / text_l is a string
        text = get_sdxl_text(node)
        if text:
            return text
        # When accessed via link, prefer text_g
        for field in ("text_g", "text_l"):
            sub_link = inputs.get(field)
            if isinstance(sub_link, list):
                result = _resolve_text(workflow, sub_link, visited, warnings, vn, om)
                if result:
                    return result
        return None

    # Nodes that hold a text field directly
    text = get_text_from_node(node)
    if text:
        return text

    # CLIPTextEncode family: first check whether text has a link
    # link present → follow upstream / no link → use widgets_values
    if cls in CLIP_TEXT_ENCODE_CLASSES or cls in WILDCARD_ENCODE_CLASSES:
        text_link = next(
            (inputs.get(f) for f in TEXT_INPUT_FIELDS if isinstance(inputs.get(f), list)),
            None,
        )
        if text_link is not None:
            return _resolve_text(workflow, text_link, visited, warnings, vn, om)
        texts = _extract_wv_texts(vn.get(node_id, []))
        return "\n".join(texts) if texts else None

    # Concatenation nodes (joined with empty string so the user controls the separator)
    if cls in CONCAT_NODE_CLASSES:
        parts: list[str] = []
        for key, val in sorted(inputs.items()):  # Process in key-name order (string_a → string_b)
            if isinstance(val, list):
                part = _resolve_text(workflow, val, visited, warnings, vn, om)
                if part is not None:
                    parts.append(part)
            elif isinstance(val, str):
                parts.append(val)
        return "".join(parts) if parts else None

    # ShowText family: text_0 holds the output cache (API workflow) / widgets_values (visual)
    if cls in TEXT_SHOW_NODE_CLASSES:
        for field in ("text_0", "text_1"):
            val = inputs.get(field)
            if isinstance(val, str) and val.strip():
                return val.strip()
        texts = _extract_wv_texts(vn.get(node_id, []))
        return "\n".join(texts) if texts else None

    # Unknown nodes (LLM encode, etc.): look for monitoring nodes on the same output slot in the visual workflow
    # Do not follow input links — doing so risks accidentally picking up LLM instruction text
    if om:
        monitoring = _find_monitoring_text(vn, om, node_id, slot, warnings, visited)
        if monitoring:
            return monitoring

    return None


def _collect_text_nodes(workflow: dict) -> list[dict]:
    """Return the list of nodes belonging to CLIP_TEXT_ENCODE_CLASSES and WILDCARD_ENCODE_CLASSES."""
    all_classes = CLIP_TEXT_ENCODE_CLASSES + WILDCARD_ENCODE_CLASSES
    result: list[dict] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in all_classes:
            result.append({"_id": node_id, **node})
    return result


def _get_node_text(node: dict) -> str | None:
    cls = node.get("class_type", "")
    if cls in ("CLIPTextEncodeSDXL", "CLIPTextEncodeSDXLRefiner"):
        return get_sdxl_text(node)
    return get_text_from_node(node)


def _node_sort_key(node: dict) -> int:
    try:
        return int(node.get("_id", 9999))
    except (ValueError, TypeError):
        return 9999


def _classify_by_title(
    nodes: list[dict],
) -> tuple[str | None, str | None]:
    """Classify nodes as positive/negative based on their title."""
    positive_node: dict | None = None
    negative_node: dict | None = None

    for node in nodes:
        title = (node.get("_meta") or {}).get("title", "").lower()
        if not title:
            continue
        is_neg = any(k in title for k in NEGATIVE_TITLE_KEYWORDS)
        is_pos = any(k in title for k in POSITIVE_TITLE_KEYWORDS)
        if is_neg and not is_pos:
            negative_node = node
        elif is_pos and not is_neg:
            positive_node = node

    if positive_node is None and negative_node is None:
        return None, None

    positive_text = _get_node_text(positive_node) if positive_node else None
    negative_text = _get_node_text(negative_node) if negative_node else None
    return positive_text, negative_text


def _classify_by_content(
    nodes: list[dict],
) -> tuple[str | None, str | None]:
    """Classify nodes by negative keyword density."""
    if len(nodes) < 2:
        return None, None

    def neg_density(node: dict) -> float:
        text = (_get_node_text(node) or "").lower()
        if not text:
            return 0.0
        words = text.replace(",", " ").split()
        if not words:
            return 0.0
        hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
        return hits / max(len(words), 1)

    scored = [(neg_density(n), n) for n in nodes]
    scored.sort(key=lambda x: x[0], reverse=True)

    # The highest-scored node is negative; the lowest-scored is positive
    neg_score, neg_node = scored[0]
    pos_score, pos_node = scored[-1]

    if neg_score == pos_score:
        return None, None

    return _get_node_text(pos_node), _get_node_text(neg_node)

"""VLM assist — fixed 4 yes/no questions on Look-dev samples (optional).

Questions (fixed):
  1. same_person     — identity matches locked character
  2. prop_visible    — signature / throughline prop is visible
  3. framing_ok      — camera framing appropriate (esp. long_shot)
  4. expression_alive — face shows readable emotion

When ``quality_policy.vlm_assist`` is false, callers skip this module.
Heuristics (WD14) always available as fallback / offline path.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..character.split_tags import soft_normalize_tag
from ..llm_options import weave_options
from ..json_util import parse_json_object
from .heuristics import evaluate_sample_framing

logger = logging.getLogger(__name__)

VLM_QUESTIONS = (
    "same_person",
    "prop_visible",
    "framing_ok",
    "expression_alive",
)

_EXPR = frozenset({
    "smile", "grin", "closed_eyes", "blush", "tears", "angry", "sad",
    "surprised", "open_mouth", "expressionless", "serious", "frown",
    "smirk", "wink", "crying", "happy", "worried", "nervous",
})


def build_vlm_assist_prompt(
    *,
    identity_tags: list[str],
    signature_prop: str,
    prop_tags: list[str],
    camera: str,
    narrative: str = "",
) -> str:
    identity = ", ".join(identity_tags[:12]) or "(unknown)"
    props = ", ".join(
        [p for p in ([signature_prop] + list(prop_tags)) if p][:8]
    ) or "(none)"
    return (
        "You are a strict visual QA checker for a 3-panel storyboard sample.\n"
        "Look at the image and answer ONLY these 4 yes/no questions as JSON.\n"
        "Do not add other keys. Use true/false only.\n\n"
        f"Identity cues (must match person): {identity}\n"
        f"Prop that should be visible: {props}\n"
        f"Intended camera: {camera}\n"
        f"Beat narrative (context): {narrative or '(n/a)'}\n\n"
        "Rules:\n"
        "- same_person: hair/eyes/body match identity cues (not a different character)\n"
        "- prop_visible: the signature prop (or clear stand-in) is visible in frame\n"
        "- framing_ok: framing matches camera; long_shot must show body+environment, "
        "not a tight face portrait\n"
        "- expression_alive: readable emotion on the face (not blank/dead)\n\n"
        "Output JSON exactly:\n"
        '{"same_person": true, "prop_visible": true, '
        '"framing_ok": true, "expression_alive": true}\n'
    )


def normalize_vlm_answers(data: dict[str, Any]) -> dict[str, bool | None]:
    out: dict[str, bool | None] = {}
    for q in VLM_QUESTIONS:
        v = data.get(q)
        if isinstance(v, bool):
            out[q] = v
        elif isinstance(v, str):
            low = v.strip().lower()
            if low in ("true", "yes", "y", "1"):
                out[q] = True
            elif low in ("false", "no", "n", "0"):
                out[q] = False
            else:
                out[q] = None
        else:
            out[q] = None
    return out


def heuristic_vlm_answers(
    *,
    wd14_tags: list[str] | None,
    identity_tags: list[str] | None,
    signature_prop: str = "",
    prop_tags: list[str] | None = None,
    camera: str = "",
) -> dict[str, bool | None]:
    """WD14-based stand-in for the fixed 4 questions (no VLM call)."""
    tags = {soft_normalize_tag(t) for t in (wd14_tags or []) if t}
    if not tags:
        return {q: None for q in VLM_QUESTIONS}

    id_norm = [
        soft_normalize_tag(t)
        for t in (identity_tags or [])
        if t and soft_normalize_tag(t) not in {"1girl", "1boy", "solo"}
    ]
    hair_eye = [t for t in id_norm if t.endswith("_hair") or t.endswith("_eyes")]
    if hair_eye:
        hits = sum(1 for t in hair_eye if t in tags)
        same_person = hits >= max(1, (len(hair_eye) + 1) // 2)
    else:
        same_person = True  # nothing to check

    prop_tokens: list[str] = []
    for raw in [signature_prop, *(prop_tags or [])]:
        t = soft_normalize_tag(raw or "")
        if t:
            prop_tokens.append(t)
            # also accept space→underscore variants already normalized
    prop_visible = True
    if prop_tokens:
        prop_visible = any(
            t in tags or any(t in x or x in t for x in tags)
            for t in prop_tokens
        )

    framing = evaluate_sample_framing(camera, list(tags))
    framing_ok = framing != "fail"

    expr_hits = tags & _EXPR
    # expressionless alone counts as dead
    expression_alive = bool(expr_hits - {"expressionless"}) or (
        "smile" in tags or "grin" in tags or "blush" in tags
    )

    return {
        "same_person": same_person,
        "prop_visible": prop_visible,
        "framing_ok": framing_ok,
        "expression_alive": expression_alive,
    }


def vlm_result(
    answers: dict[str, bool | None],
    *,
    method: str,
    raw: str = "",
    error: str = "",
) -> dict[str, Any]:
    known = [v for v in answers.values() if v is not None]
    fails = [k for k, v in answers.items() if v is False]
    return {
        "version": 1,
        "method": method,
        "evaluated_at": time.time(),
        "answers": answers,
        "pass": bool(known) and not fails,
        "fail_keys": fails,
        "raw": (raw or "")[:800],
        "error": (error or "")[:300],
    }


async def load_image_bytes(db, image_id: str) -> bytes | None:
    if not image_id or str(image_id).startswith(("pending:", "placeholder:")):
        return None
    try:
        doc = await db.get(image_id) or {}
    except Exception:
        return None
    fp = Path(str(doc.get("path") or ""))
    if not fp.exists():
        return None
    data = fp.read_bytes()
    if fp.suffix.lower() == ".webp":
        import io
        from PIL import Image as _PILImage

        buf = io.BytesIO()
        _PILImage.open(io.BytesIO(data)).convert("RGB").save(
            buf, format="JPEG", quality=90,
        )
        data = buf.getvalue()
    return data


async def run_vlm_assist(
    ollama,
    *,
    model: str,
    image_bytes: bytes,
    identity_tags: list[str],
    signature_prop: str,
    prop_tags: list[str],
    camera: str,
    narrative: str = "",
    options: dict | None = None,
) -> dict[str, Any]:
    prompt = build_vlm_assist_prompt(
        identity_tags=identity_tags,
        signature_prop=signature_prop,
        prop_tags=prop_tags,
        camera=camera,
        narrative=narrative,
    )
    try:
        raw = await ollama.generate_vlm(
            prompt,
            [image_bytes],
            model=model,
            options=weave_options(options or {"num_predict": 120}, model=model),
            think=False,
        )
        data = parse_json_object(raw)
        answers = normalize_vlm_answers(data)
        return vlm_result(answers, method="vlm", raw=raw)
    except Exception as e:
        logger.warning("vlm_assist failed: %s", e)
        return vlm_result(
            {q: None for q in VLM_QUESTIONS},
            method="vlm_error",
            error=str(e),
        )


def apply_heuristic_vlm(
    panel: dict[str, Any],
    session: dict[str, Any],
    wd14_tags: list[str] | None,
) -> dict[str, Any]:
    character = session.get("character") or {}
    intent = panel.get("intent") or {}
    answers = heuristic_vlm_answers(
        wd14_tags=wd14_tags,
        identity_tags=list(character.get("identity_tags") or []),
        signature_prop=str(character.get("signature_prop") or ""),
        prop_tags=list(character.get("prop_tags") or []),
        camera=str(intent.get("camera") or ""),
    )
    result = vlm_result(answers, method="heuristic")
    panel.setdefault("qa", {})["vlm"] = result
    return result


async def apply_vlm_assist_to_panel(
    panel: dict[str, Any],
    session: dict[str, Any],
    *,
    db,
    ollama=None,
    wd14_tags: list[str] | None = None,
    force_heuristic: bool = False,
) -> dict[str, Any]:
    """Run VLM (if enabled + model) else heuristic; store on panel.qa.vlm."""
    policy = session.get("quality_policy") or {}
    if not policy.get("vlm_assist", True):
        return {"skipped": True, "reason": "vlm_assist_off"}

    character = session.get("character") or {}
    intent = panel.get("intent") or {}
    inputs = session.get("inputs") or {}
    model = (
        str(inputs.get("vlm_model") or "").strip()
        or str(inputs.get("story_model") or "").strip()
    )
    image_id = str((panel.get("sample") or {}).get("image_id") or "")

    if not force_heuristic and ollama is not None and model and image_id:
        image_bytes = await load_image_bytes(db, image_id)
        if image_bytes:
            result = await run_vlm_assist(
                ollama,
                model=model,
                image_bytes=image_bytes,
                identity_tags=list(character.get("identity_tags") or []),
                signature_prop=str(character.get("signature_prop") or ""),
                prop_tags=list(character.get("prop_tags") or []),
                camera=str(intent.get("camera") or ""),
                narrative=str(intent.get("narrative_ja") or ""),
            )
            # If VLM errored with all None, fall back to heuristic.
            if result.get("method") == "vlm" and any(
                v is not None for v in (result.get("answers") or {}).values()
            ):
                panel.setdefault("qa", {})["vlm"] = result
                return result

    tags = wd14_tags
    if tags is None and image_id:
        from .heuristics import resolve_wd14_for_image
        tags = await resolve_wd14_for_image(db, image_id)
    return apply_heuristic_vlm(panel, session, tags)

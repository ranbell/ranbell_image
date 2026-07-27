"""Deterministic panel compile (Stage2 replacement)."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from ..character.split_tags import soft_normalize_tag
from .cameras import CAMERA_FORCE_ADD, CAMERA_NEGATIVE, strip_framing_conflicts

WEAVE_NEGATIVE = (
    "lowres, worst quality, low quality, bad anatomy, bad hands, "
    "missing fingers, extra digits, fewer digits, malformed limbs, "
    "extra limbs, deformed, mutated, disfigured, bad proportions, "
    "jpeg artifacts, signature, watermark, text"
)

_ENV_LEXICON: dict[str, list[str]] = {
    "bookstore": ["bookstore", "bookshelf", "indoors", "shop_interior"],
    "bookshop": ["bookstore", "bookshelf", "indoors"],
    "cafe": ["cafe", "indoors", "wooden_table"],
    "station": ["train_station", "platform", "outdoors"],
    "rain": ["rain", "wet", "overcast"],
    "classroom": ["classroom", "desk", "indoors"],
    "書店": ["bookstore", "bookshelf", "indoors"],
    "カフェ": ["cafe", "indoors"],
    "駅": ["train_station", "platform"],
    "雨": ["rain", "wet", "overcast"],
}


def _dedupe(tags: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        s = soft_normalize_tag(t) if t.isascii() else (t or "").strip()
        if not s:
            continue
        key = s.lower().replace("-", "_")
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _env_from_setting(setting: str, *, boost: bool = False) -> list[str]:
    text = setting or ""
    low = text.lower()
    hits: list[str] = []
    for key, tags in _ENV_LEXICON.items():
        if key.lower() in low or key in text:
            hits.extend(tags)
    if boost and hits:
        hits.extend(["detailed_background", "scenery", "depth_of_field"])
    elif boost:
        hits.extend(["indoors", "detailed_background", "scenery"])
    return _dedupe(hits)


def compile_panel(
    session: dict[str, Any],
    panel_key: str,
    *,
    env_boost: bool = False,
) -> dict[str, Any]:
    character = session.get("character") or {}
    bundle = session.get("story_bundle") or {}
    world = bundle.get("world") or {}
    panels = session.get("panels") or []
    panel = next((p for p in panels if p.get("key") == panel_key), None)
    if not panel:
        # fall back to story_bundle panels
        raw = next(
            (p for p in (bundle.get("panels") or []) if p.get("key") == panel_key),
            {},
        )
        intent = raw.get("intent") if isinstance(raw.get("intent"), dict) else raw
    else:
        intent = panel.get("intent") or {}

    camera = str(intent.get("camera") or "medium_shot")
    identity = list(character.get("identity_tags") or [])
    prop_tags = list(character.get("prop_tags") or [])
    if character.get("signature_prop"):
        prop_tags = _dedupe(prop_tags + [str(character["signature_prop"])])

    resolved = list(intent.get("must_show_resolved") or [])
    throughline = _dedupe(
        prop_tags
        + resolved
        + _dedupe([
            soft_normalize_tag(str(world.get("throughline_prop") or "")),
            soft_normalize_tag(str(world.get("throughline_place") or "")),
        ])
    )

    cam_tags = list(CAMERA_FORCE_ADD.get(camera, [camera]))
    action = _dedupe([str(intent.get("gesture") or "")])
    emotion = _dedupe([str(intent.get("emotion") or "")])
    time_marker = _dedupe([str(intent.get("time_marker") or "")])
    environment = _env_from_setting(str(world.get("setting") or ""), boost=env_boost)
    # Optional gallery-NN atmosphere / style tags (never mixed into identity).
    spice = _dedupe([str(t) for t in (character.get("gallery_spice") or [])])
    # Lab Spicer tags (quality_policy.spicer) — also spice layer only.
    from ..character.spicer import is_spicer_enabled

    if is_spicer_enabled(session):
        spice = _dedupe(spice + [str(t) for t in (character.get("lab_spice") or [])])
    # face-visible emotion boost for dead_expression chip
    for c in session.get("constraints") or []:
        if (
            c.get("active")
            and c.get("text") == "face_visible_emotion"
            and c.get("scope") in (panel_key, "session")
        ):
            if camera in ("medium_shot", "close_up"):
                emotion = _dedupe(emotion + ["looking_at_viewer", "detailed_face"])
            break

    # Merge then strip framing conflicts (never drop identity/throughline cores)
    body = strip_framing_conflicts(
        _dedupe(cam_tags + action + emotion + time_marker + environment),
        camera,
    )
    layers = {
        "identity": identity,
        "camera": cam_tags,
        "throughline": throughline,
        "action": action,
        "emotion": emotion,
        "environment": environment,
        "spice": spice,
    }
    positive_tags = _dedupe(identity + throughline + body + spice)
    prose_bits = [
        str(intent.get("narrative_en") or "").strip(),
        str(world.get("setting") or "").strip(),
    ]
    prose = ". ".join(b for b in prose_bits if b)
    positive = ", ".join(positive_tags)
    if prose and prose.isascii():
        positive = f"{positive}, {prose}" if positive else prose

    neg_extra = list(CAMERA_NEGATIVE.get(camera, []))
    for c in session.get("constraints") or []:
        if not c.get("active", True):
            continue
        if c.get("scope") in ("session", "compile", panel_key):
            txt = str(c.get("text") or "")
            if txt.lower().startswith("negative:"):
                neg_extra.append(txt.split(":", 1)[1].strip())
    # character.do_not → negatives
    for raw in character.get("do_not") or []:
        t = soft_normalize_tag(str(raw)) if str(raw).isascii() else str(raw).strip()
        if t:
            neg_extra.append(t)
    negative = WEAVE_NEGATIVE
    if neg_extra:
        negative = negative + ", " + ", ".join(_dedupe(neg_extra))

    checksum = hashlib.sha256(positive.encode("utf-8")).hexdigest()[:16]
    result = {
        "positive": positive,
        "negative": negative,
        "layers": layers,
        "checksum": checksum,
        "updated_at": time.time(),
        "camera": camera,
    }
    if panel is not None:
        panel["compile"] = {
            "positive": positive,
            "negative": negative,
            "layers": layers,
            "checksum": checksum,
            "updated_at": result["updated_at"],
        }
    return result


def compile_all_panels(session: dict[str, Any], *, env_boost_panels: set[str] | None = None) -> dict[str, dict]:
    boost = env_boost_panels or set()
    out: dict[str, dict] = {}
    for key in ("panel_1", "panel_2", "panel_3"):
        out[key] = compile_panel(session, key, env_boost=(key in boost))
    return out

"""Single StoryBundle generation + apply helpers."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..json_util import parse_json_object
from ..prompt_loader import load_prompt
from ..schema import BEATS, CAMERAS, PANEL_KEYS

logger = logging.getLogger(__name__)


def build_story_prompt(
    *,
    topic: str,
    character: dict[str, Any],
    author_style: str = "",
    recreate_constraints: list[str] | None = None,
    avoid_motifs: list[str] | None = None,
    previous_causality: str = "",
) -> str:
    system = load_prompt("storywright.md")
    personality = character.get("personality") or {}
    user = {
        "topic": topic,
        "author_style": author_style,
        "personality_summary": personality.get("summary") or personality.get("summary_ja") or "",
        "personality_summary_ja": personality.get("summary_ja") or "",
        "traits": list(personality.get("traits") or [])[:8],
        # What the character wants / hides — the hook for a non-generic conflict.
        "inner": list(personality.get("inner") or [])[:4],
        "likes": list(personality.get("likes") or [])[:6],
        "dislikes": list(personality.get("dislikes") or [])[:6],
        # Continuity only — do NOT invent appearance in narratives (HARD RULE 4).
        "identity_tags": list(character.get("identity_tags") or [])[:16],
        "signature_prop": character.get("signature_prop") or "",
        "prop_tags": character.get("prop_tags") or [],
        "do_not": character.get("do_not") or [],
        "age_band": personality.get("age_band") or "",
        "occupation_hint": personality.get("occupation") or personality.get("occupation_hint") or "",
        # Per-panel performance vocabulary owned by this character.
        "expression_vocab": list(character.get("expression_vocab") or [])[:8],
        "gesture_vocab": list(character.get("gesture_vocab") or [])[:8],
        "outfit_style": personality.get("outfit_style") or "",
        "vibe_keywords": list(personality.get("vibe_keywords") or [])[:6],
        "avoid_motifs": avoid_motifs or [],
        "recreate_constraints": recreate_constraints or [],
        "previous_causality_one_liner": previous_causality or "",
    }
    return (
        system
        + "\n\n# INPUT\n"
        + json.dumps(user, ensure_ascii=False, indent=2)
        + "\n\nOutput the JSON now."
    )


def normalize_story_bundle(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure keys / beats / cameras; do not invent plot."""
    out = dict(data)
    world = dict(out.get("world") or {})
    out["world"] = world
    panels_in = list(out.get("panels") or [])
    panels: list[dict[str, Any]] = []
    used_cams: set[str] = set()
    for i, key in enumerate(PANEL_KEYS):
        src = panels_in[i] if i < len(panels_in) and isinstance(panels_in[i], dict) else {}
        # flatten intent if nested
        if isinstance(src.get("intent"), dict) and not src.get("narrative_ja"):
            src = {**src["intent"], "key": src.get("key") or key}
        cam = str(src.get("camera") or CAMERAS[i]).strip()
        if cam not in CAMERAS or cam in used_cams:
            cam = next((c for c in CAMERAS if c not in used_cams), CAMERAS[i])
        used_cams.add(cam)
        panels.append({
            "key": key,
            "beat": BEATS[i],
            "narrative_ja": str(src.get("narrative_ja") or ""),
            "narrative_en": str(src.get("narrative_en") or ""),
            "visible_change": str(src.get("visible_change") or ""),
            "camera": cam,
            "gesture": str(src.get("gesture") or ""),
            "focus": str(src.get("focus") or ""),
            "time_marker": str(src.get("time_marker") or ""),
            "emotion": str(src.get("emotion") or ""),
            "must_show": list(src.get("must_show") or ["throughline_prop", "throughline_place"]),
            "must_show_resolved": list(src.get("must_show_resolved") or []),
        })
    out["panels"] = panels
    out["title"] = str(out.get("title") or "")
    return out


def apply_story_to_session(session: dict[str, Any], bundle: dict[str, Any]) -> None:
    """Write normalized bundle into session.panels intents."""
    session["story_bundle"] = bundle
    session["story_version"] = int(session.get("story_version") or 0) + 1
    session["status"] = "story"
    world = bundle.get("world") or {}
    session.setdefault("cross_panel_qa", {})["causality_one_liner"] = (
        world.get("causality_one_liner") or ""
    )
    by_key = {p["key"]: p for p in bundle.get("panels") or []}
    for panel in session.get("panels") or []:
        src = by_key.get(panel.get("key") or "")
        if not src:
            continue
        panel["intent"] = {
            "beat": src.get("beat"),
            "narrative_ja": src.get("narrative_ja"),
            "narrative_en": src.get("narrative_en"),
            "visible_change": src.get("visible_change"),
            "camera": src.get("camera"),
            "gesture": src.get("gesture"),
            "focus": src.get("focus"),
            "time_marker": src.get("time_marker"),
            "emotion": src.get("emotion"),
            "must_show": src.get("must_show") or [],
            "must_show_resolved": src.get("must_show_resolved") or [],
            "must_not": [],
            "locked": False,
        }
        # Clear stale look-dev / final state on new story
        panel["sample"] = {"image_id": None, "job_id": None, "scorecard": None}
        panel["sample_history"] = []
        panel["final"] = {"image_id": None, "job_id": None, "scorecard": None}
        panel["final_alts"] = []
        panel["compile"] = {
            "positive": "",
            "negative": "",
            "layers": {
                "identity": [], "camera": [], "throughline": [],
                "action": [], "emotion": [], "environment": [], "spice": [],
            },
            "checksum": "",
            "updated_at": 0,
        }
        panel["framing_fail_count"] = 0
        panel["framing_counted_image_id"] = None
        panel["qa"] = {
            "drawability": None,
            "critic": None,
            "weave_score": None,
            "framing": None,
            "vlm": None,
        }
    session["framing_overrides"] = []
    session["constraints"] = []
    cross = session.setdefault("cross_panel_qa", {})
    cross["ready_for_final"] = False
    cross["finals_ready"] = False
    cross["lookdev_ready"] = False
    cross["weave_score"] = None
    cross["identity_drift_risk"] = None
    session["updated_at"] = time.time()


async def run_storywright(
    ollama,
    *,
    model: str,
    options: dict,
    topic: str,
    character: dict[str, Any],
    author_style: str = "",
    recreate_constraints: list[str] | None = None,
    avoid_motifs: list[str] | None = None,
    previous_causality: str = "",
) -> dict[str, Any]:
    prompt = build_story_prompt(
        topic=topic,
        character=character,
        author_style=author_style,
        recreate_constraints=recreate_constraints,
        avoid_motifs=avoid_motifs,
        previous_causality=previous_causality,
    )
    raw = await ollama.chat_text(
        prompt,
        model=model,
        options=options,
        fmt="json",
        think=True,
    )
    data = parse_json_object(raw)
    return normalize_story_bundle(data)

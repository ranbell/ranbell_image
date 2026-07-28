"""WeaveSession payload factory and constants."""
from __future__ import annotations

import time
from typing import Any

SCHEMA_VERSION = 1

STATUSES = ("character", "story", "lookdev", "rendering", "sealed")
PANEL_KEYS = ("panel_1", "panel_2", "panel_3")
BEATS = ("setup", "turn", "settle")
CAMERAS = ("long_shot", "medium_shot", "close_up")
# Two renders, two jobs: the sheet is what panel generation references, the
# portrait is where a human judges the face. Nothing else earned its GPU time.
BOARD_SLOTS = ("sheet", "portrait")
DEFAULT_BOARD_SLOTS = BOARD_SLOTS

# Rate chips → used by look-dev / recreate
RATE_CHIPS = (
    "too_close",
    "too_wide",
    "wrong_person",
    "missing_prop",
    "dead_expression",
    "sparse",
    "unclear_story",
    "good",
)

RECREATE_CHIPS = (
    "off_topic",
    "same_moment",
    "weak_plot",
    "too_dark",
    "place_scatters",
    "weak_prop",
    "cliche",
    "more_everyday",
    "more_incident",
    "unclear_story",
)


def _empty_panel(key: str, beat: str, camera: str) -> dict[str, Any]:
    return {
        "key": key,
        "intent": {
            "beat": beat,
            "narrative_ja": "",
            "visible_change": "",
            "camera": camera,
            "gesture": "",
            "focus": "",
            "time_marker": "",
            "emotion": "",
            "state_tags": [],
            "must_show": ["throughline_prop", "throughline_place"],
            "must_show_resolved": [],
            "must_not": [],
            "locked": False,
        },
        "qa": {
            "drawability": None,
            "critic": None,
            "weave_score": None,
            "framing": None,
            "vlm": None,
        },
        "compile": {
            "positive": "",
            "negative": "",
            "layers": {
                "identity": [],
                "outfit": [],
                "camera": [],
                "throughline": [],
                "state": [],
                "action": [],
                "emotion": [],
                "environment": [],
                "spice": [],
            },
            "checksum": "",
            "updated_at": 0,
        },
        "sample_history": [],
        "sample": {"image_id": None, "job_id": None, "scorecard": None},
        "final": {"image_id": None, "job_id": None, "scorecard": None},
        "framing_fail_count": 0,
    }


def new_session_payload(
    *,
    topic: str = "",
    personality_text: str = "",
    author_id: str = "",
    author_style: str = "",
    reference_image_id: str = "",
    story_model: str = "",
    llm_provider: str = "ollama",
    workflow_final: str = "",
    workflow_sample: str = "",
    locale: str = "ja",
    use_gallery_nn: bool = False,
) -> dict[str, Any]:
    now = time.time()
    panels = [
        _empty_panel("panel_1", "setup", "long_shot"),
        _empty_panel("panel_2", "turn", "medium_shot"),
        _empty_panel("panel_3", "settle", "close_up"),
    ]
    gallery_nn = bool(use_gallery_nn)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "character",
        "locale": locale,
        "quality_policy": {
            "mode": "standard",
            "min_sample_panels": 1,
            "critic": "on_lint_fail",
            "vlm_assist": True,
            "framing_fail_limit": 2,
            "strict_seal": False,
            "allow_story_before_board": True,
            # Opt-in: Qdrant NN → identity/spice enrichment after infer.
            "gallery_nn": gallery_nn,
            # Lab extras (default off)
            "spicer": False,
            "multi_seed": 1,
        },
        "inputs": {
            "topic": topic or "",
            "personality_text": personality_text or "",
            "author_id": author_id or "",
            "author_style": author_style or "",
            "age_band": "",
            "gender_hint": "",
            "occupation_hint": "",
            # How far apart the three panels sit (story.generator.TIME_SCALES).
            "time_scale": "hours",
            "reference_image_id": reference_image_id or "",
            "story_model": story_model or "",
            "critic_model": "",
            "vlm_model": "",
            "llm_provider": llm_provider or "ollama",
            "workflow_final": workflow_final or "",
            "workflow_sample": workflow_sample or "",
            "use_gallery_nn": gallery_nn,
        },
        "character": {
            "personality": {},
            "identity_tags": [],
            # Default wardrobe. The story may override it per topic/season.
            "outfit_tags": [],
            "prop_tags": [],
            "signature_prop": "",
            "palette": [],
            "do_not": [],
            "reasoning_ja": "",
            "board": {"images": [], "accepted": False},
            "identity_locked": False,
            "source": "personality",
            "gallery_refs": [],
            "gallery_spice": [],
            "lab_spice": [],
            "gallery_nn": None,
        },
        "story_bundle": {},
        "story_version": 0,
        "story_history": [],
        "recreate_constraints": [],
        "avoid_motifs": [],
        "constraints": [],
        "framing_overrides": [],
        "panels": panels,
        "cross_panel_qa": {
            "causality_one_liner": "",
            "throughline_coverage": None,
            "identity_drift_risk": None,
            "camera_diversity": None,
            "motif_repetition": None,
            "weave_score": None,
            "lookdev_ready": False,
            "ready_for_final": False,
            "finals_ready": False,
        },
        "timeline": [],
        "preference_log": [],
        "proposals": [],
        "created_at": now,
        "updated_at": now,
    }


def rendered_board_slots(session: dict[str, Any], *, allow_pending: bool = False) -> set[str]:
    board = (session.get("character") or {}).get("board") or {}
    out: set[str] = set()
    for img in board.get("images") or []:
        iid = str(img.get("image_id") or "")
        slot = str(img.get("slot") or "")
        if not iid or not slot:
            continue
        if iid.startswith(("pending:", "placeholder:")) and not allow_pending:
            continue
        out.add(slot)
    return out


def board_is_usable(session: dict[str, Any], *, allow_pending: bool = False) -> bool:
    """A face plus a full-body view exist.

    ``sheet`` is the current full-body view; ``full`` is the retired slot kept
    here so sessions started before the board was cut down still pass.
    """
    slots = rendered_board_slots(session, allow_pending=allow_pending)
    return "portrait" in slots and bool(slots & {"sheet", "full"})


def append_timeline(
    session: dict[str, Any],
    *,
    actor: str,
    type_: str,
    text: str = "",
    ref: dict[str, Any] | None = None,
) -> None:
    session.setdefault("timeline", []).append({
        "id": f"t-{len(session.get('timeline') or []) + 1}",
        "at": time.time(),
        "actor": actor,
        "type": type_,
        "text": text or "",
        "ref": ref or {},
    })
    session["updated_at"] = time.time()

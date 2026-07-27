"""Weave session orchestration (no Comfy yet for board/sample/final)."""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from .character.personalitywright import apply_inference_to_character, run_personalitywright
from .character.split_tags import enforce_identity_prop_split
from .compile.layers import compile_all_panels, compile_panel
from .schema import append_timeline, new_session_payload
from .state_machine import gates, next_cta
from .story.recreate import chips_to_constraints
from .story.storywright import apply_story_to_session, normalize_story_bundle, run_storywright
from .validate.story_lint import lint_story_bundle

logger = logging.getLogger(__name__)


class WeaveError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    return {
        **session,
        "gates": gates(session),
        "next_cta": next_cta(session),
    }


async def create_session_payload(**kwargs) -> dict[str, Any]:
    return new_session_payload(**kwargs)


def lock_identity(session: dict[str, Any]) -> dict[str, Any]:
    character = session.setdefault("character", {})
    if not character.get("identity_tags"):
        raise WeaveError("identity_tags is empty — run infer first")
    identity, props, sig = enforce_identity_prop_split(
        character.get("identity_tags"),
        character.get("prop_tags"),
        signature_prop=str(character.get("signature_prop") or ""),
    )
    character["identity_tags"] = identity
    character["prop_tags"] = props
    character["signature_prop"] = sig
    character["identity_locked"] = True
    append_timeline(
        session, actor="user", type_="lock",
        text="identity locked",
    )
    return session


def accept_board(session: dict[str, Any]) -> dict[str, Any]:
    board = session.setdefault("character", {}).setdefault("board", {})
    images = board.get("images") or []
    slots = {img.get("slot") for img in images if img.get("image_id")}
    # Allow accept when briefs exist even if images pending? Design says portrait+full required.
    # For M1 without Comfy, accept board briefs as placeholder acceptance only if images present
    # OR if quality_policy allows dry accept when no renderer — use images OR board_briefs dry flag.
    if "portrait" not in slots or "full" not in slots:
        # Dry-run path: synthesize placeholder image ids from briefs so G0-hard can be tested
        briefs = (session.get("character") or {}).get("board_briefs") or []
        if briefs and not images:
            board["images"] = [
                {
                    "slot": b.get("slot"),
                    "image_id": f"pending:{b.get('slot')}",
                    "positive": "",
                    "pending": True,
                }
                for b in briefs
                if b.get("slot") in ("portrait", "full", "prop")
            ]
            slots = {img.get("slot") for img in board["images"]}
        if "portrait" not in slots or "full" not in slots:
            raise WeaveError("board needs portrait and full images before accept")
    board["accepted"] = True
    append_timeline(session, actor="user", type_="lock", text="board accepted")
    return session


async def infer_character(
    session: dict[str, Any],
    ollama,
    *,
    model: str,
    options: dict | None = None,
    personality_text: str | None = None,
) -> dict[str, Any]:
    inputs = session.setdefault("inputs", {})
    text = (personality_text or inputs.get("personality_text") or "").strip()
    if not text:
        raise WeaveError("personality_text is required")
    inputs["personality_text"] = text
    if not model:
        raise WeaveError("story_model is required", status_code=400)
    data = await run_personalitywright(
        ollama,
        model=model,
        options=options or {"temperature": 0.7},
        personality_text=text,
        topic=str(inputs.get("topic") or ""),
        author_style=str(inputs.get("author_style") or ""),
    )
    apply_inference_to_character(session.setdefault("character", {}), data)
    session["character"]["identity_locked"] = False
    session["character"]["board"] = {"images": [], "accepted": False}
    # Invalidate story if re-infer
    if int(session.get("story_version") or 0) > 0:
        session["story_bundle"] = {}
        session["story_version"] = 0
        session["status"] = "character"
    append_timeline(
        session, actor="llm.personalitywright", type_="proposal",
        text=session["character"].get("reasoning_ja") or "character inferred",
    )
    return session


def _push_history(session: dict[str, Any], *, reasons: list[str], constraints: list[str]) -> None:
    if not session.get("story_bundle"):
        return
    session.setdefault("story_history", []).append({
        "version": session.get("story_version"),
        "bundle": copy.deepcopy(session.get("story_bundle")),
        "reasons": reasons,
        "constraints": constraints,
        "at": time.time(),
    })


async def generate_story(
    session: dict[str, Any],
    ollama,
    *,
    model: str,
    options: dict | None = None,
    topic: str | None = None,
) -> dict[str, Any]:
    if not (session.get("character") or {}).get("identity_locked"):
        raise WeaveError("identity must be locked (G0-soft) before story")
    inputs = session.setdefault("inputs", {})
    if topic is not None:
        inputs["topic"] = topic
    topic_s = str(inputs.get("topic") or "").strip()
    if not topic_s:
        raise WeaveError("topic is required")
    if not model:
        raise WeaveError("story_model is required")
    author_style = str(inputs.get("author_style") or "")
    bundle = await run_storywright(
        ollama,
        model=model,
        options=options or {"temperature": 0.7},
        topic=topic_s,
        character=session.get("character") or {},
        author_style=author_style,
        avoid_motifs=list(session.get("avoid_motifs") or []),
    )
    lint = lint_story_bundle(bundle, session.get("character") or {})
    if not lint["pass"]:
        # One repair attempt is deferred to a dedicated endpoint; surface defects.
        apply_story_to_session(session, bundle)
        session["last_lint"] = lint
        append_timeline(
            session, actor="system", type_="message",
            text=f"story lint failed: {len(lint['defects'])} defects — recreate recommended",
        )
        return session
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session["cross_panel_qa"]["throughline_coverage"] = lint.get("throughline_coverage")
    append_timeline(
        session, actor="llm.storywright", type_="message",
        text=(bundle.get("world") or {}).get("causality_one_liner") or "story generated",
    )
    return session


async def recreate_story(
    session: dict[str, Any],
    ollama,
    *,
    model: str,
    chips: list[str],
    options: dict | None = None,
) -> dict[str, Any]:
    if not chips:
        raise WeaveError("recreate requires reason chips")
    if session.get("status") in ("rendering", "sealed"):
        raise WeaveError("cannot recreate while rendering/sealed", status_code=409)
    constraints = chips_to_constraints(
        chips,
        current_motifs=list(session.get("avoid_motifs") or []),
    )
    # Update avoid bank on cliche
    if any(c in ("cliche", "ありきたり") for c in chips):
        world = (session.get("story_bundle") or {}).get("world") or {}
        motif = str(world.get("setting") or "").strip()
        if motif and motif not in (session.get("avoid_motifs") or []):
            session.setdefault("avoid_motifs", []).append(motif)
    prev_causal = ((session.get("story_bundle") or {}).get("world") or {}).get(
        "causality_one_liner"
    ) or ""
    _push_history(session, reasons=list(chips), constraints=constraints)
    session["recreate_constraints"] = constraints
    inputs = session.get("inputs") or {}
    topic_s = str(inputs.get("topic") or "").strip()
    if not topic_s:
        raise WeaveError("topic is required")
    bundle = await run_storywright(
        ollama,
        model=model,
        options=options or {"temperature": 0.8},
        topic=topic_s,
        character=session.get("character") or {},
        author_style=str(inputs.get("author_style") or ""),
        recreate_constraints=constraints,
        avoid_motifs=list(session.get("avoid_motifs") or []),
        previous_causality=str(prev_causal),
    )
    lint = lint_story_bundle(bundle, session.get("character") or {})
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session["status"] = "story"
    append_timeline(
        session, actor="llm.storywright", type_="message",
        text=f"recreated with chips={chips}",
    )
    return session


def rollback_story(session: dict[str, Any], to_version: int) -> dict[str, Any]:
    hist = session.get("story_history") or []
    match = next((h for h in hist if int(h.get("version") or 0) == int(to_version)), None)
    if not match:
        raise WeaveError(f"version {to_version} not found in history")
    # Keep current in history before restore
    _push_history(session, reasons=["rollback"], constraints=[])
    bundle = normalize_story_bundle(copy.deepcopy(match["bundle"]))
    lint_story_bundle(bundle, session.get("character") or {})
    apply_story_to_session(session, bundle)
    session["status"] = "story"
    append_timeline(
        session, actor="user", type_="decide",
        text=f"rolled back to version {to_version}",
    )
    return session


def enter_lookdev(session: dict[str, Any]) -> dict[str, Any]:
    if int(session.get("story_version") or 0) <= 0:
        raise WeaveError("story required before lookdev")
    lint = session.get("last_lint")
    if lint and not lint.get("pass"):
        raise WeaveError(
            "story lint has defects — recreate the story",
            status_code=400,
        )
    # Ensure resolved + compile
    bundle = session.get("story_bundle") or {}
    lint_story_bundle(bundle, session.get("character") or {})
    # sync resolved into intents
    by_key = {p["key"]: p for p in bundle.get("panels") or []}
    for panel in session.get("panels") or []:
        src = by_key.get(panel.get("key"))
        if src and panel.get("intent"):
            panel["intent"]["must_show_resolved"] = src.get("must_show_resolved") or []
    compile_all_panels(session)
    session["status"] = "lookdev"
    append_timeline(session, actor="user", type_="message", text="entered lookdev")
    return session


def compile_session(session: dict[str, Any], *, sparse_panels: list[str] | None = None) -> dict[str, Any]:
    boost = set(sparse_panels or [])
    # Also boost panels that have sparse constraint
    for c in session.get("constraints") or []:
        if c.get("active") and c.get("text") == "env_boost":
            boost.add(str(c.get("scope") or ""))
    result = compile_all_panels(session, env_boost_panels=boost)
    append_timeline(session, actor="system", type_="message", text="compiled panels")
    return result


def rate_sample(
    session: dict[str, Any],
    *,
    panel_key: str,
    chips: list[str],
) -> dict[str, Any]:
    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if not panel:
        raise WeaveError(f"unknown panel {panel_key}")
    session.setdefault("preference_log", []).append({
        "at": time.time(),
        "panel_key": panel_key,
        "chips": list(chips),
    })
    for chip in chips:
        if chip == "good":
            continue
        if chip == "unclear_story" or chip == "話がわからない":
            append_timeline(
                session, actor="system", type_="message",
                text="story unclear — use recreate (look-dev samples will be discarded)",
            )
            continue
        if chip in ("sparse", "寂しい"):
            session.setdefault("constraints", []).append({
                "id": f"c-env-{panel_key}",
                "source": "user_comment",
                "scope": panel_key,
                "text": "env_boost",
                "active": True,
            })
            compile_panel(session, panel_key, env_boost=True)
            continue
        if chip in ("too_close", "寄りすぎ"):
            panel["framing_fail_count"] = int(panel.get("framing_fail_count") or 0) + 1
            panel.setdefault("qa", {})["framing"] = "fail"
            session.setdefault("constraints", []).append({
                "id": f"c-framing-{panel_key}-{panel['framing_fail_count']}",
                "source": "framing_guard",
                "scope": panel_key,
                "text": "negative: close-up, portrait, face focus",
                "active": True,
            })
            compile_all_panels(session)
            continue
        if chip in ("missing_prop", "小道具なし"):
            session.setdefault("constraints", []).append({
                "id": f"c-prop-{panel_key}",
                "source": "user_comment",
                "scope": panel_key,
                "text": "emphasize signature_prop",
                "active": True,
            })
            compile_all_panels(session)
            continue
    append_timeline(
        session, actor="user", type_="message",
        text=f"rated {panel_key}: {chips}",
    )
    return session


def override_framing(
    session: dict[str, Any],
    *,
    panel_key: str,
    reason: str,
) -> dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise WeaveError("override reason is required")
    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if not panel:
        raise WeaveError(f"unknown panel {panel_key}")
    limit = int((session.get("quality_policy") or {}).get("framing_fail_limit") or 2)
    fails = int(panel.get("framing_fail_count") or 0)
    if fails < limit:
        raise WeaveError(
            f"framing_fail_count={fails} < limit={limit}; repair/resample first",
        )
    session.setdefault("framing_overrides", []).append({
        "panel_key": panel_key,
        "reason": reason,
        "at": time.time(),
    })
    if panel.get("qa"):
        panel["qa"]["framing"] = "overridden"
    append_timeline(
        session, actor="user", type_="decide",
        text=f"framing override {panel_key}: {reason}",
    )
    return session


def mark_sample_placeholder(session: dict[str, Any], panel_key: str) -> dict[str, Any]:
    """M3 stub: mark a panel as sampled without Comfy (for CTA/gate testing)."""
    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if not panel:
        raise WeaveError(f"unknown panel {panel_key}")
    panel["sample"] = {
        "image_id": f"placeholder:{panel_key}",
        "job_id": None,
        "scorecard": {"placeholder": True},
    }
    if (panel.get("intent") or {}).get("camera") == "long_shot":
        panel.setdefault("qa", {})["framing"] = "pass"
    append_timeline(
        session, actor="system", type_="sample",
        text=f"placeholder sample {panel_key}",
    )
    return session

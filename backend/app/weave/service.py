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
    from .verify.seal import evaluate_seal_rubric

    return {
        **session,
        "gates": gates(session),
        "next_cta": next_cta(session),
        "seal_rubric": evaluate_seal_rubric(session),
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
    session["suggest_reinfer"] = False
    append_timeline(
        session, actor="user", type_="lock",
        text="identity locked",
    )
    return session


def unlock_identity(session: dict[str, Any], *, confirm: bool = False) -> dict[str, Any]:
    """Unlock identity for re-infer. Wipes story when confirm=True."""
    if not confirm:
        raise WeaveError("confirm=true required — re-infer invalidates the story")
    character = session.setdefault("character", {})
    character["identity_locked"] = False
    character["board"] = {"images": [], "accepted": False}
    session["story_bundle"] = {}
    session["story_version"] = 0
    session["last_lint"] = None
    session["critic_report"] = None
    session["status"] = "character"
    session["suggest_reinfer"] = False
    session["suggest_recreate"] = False
    append_timeline(
        session, actor="user", type_="unlock",
        text="identity unlocked — story invalidated; re-infer required",
    )
    return session


def accept_board(session: dict[str, Any], *, allow_pending: bool = False) -> dict[str, Any]:
    board = session.setdefault("character", {}).setdefault("board", {})
    images = board.get("images") or []

    def _usable(img: dict) -> bool:
        iid = str(img.get("image_id") or "")
        if not iid:
            return False
        if iid.startswith("pending:") or iid.startswith("placeholder:"):
            return allow_pending
        return True

    slots = {img.get("slot") for img in images if _usable(img)}
    if "portrait" not in slots or "full" not in slots:
        # Test / dry path: synthesize placeholders only when explicitly allowed
        briefs = (session.get("character") or {}).get("board_briefs") or []
        if allow_pending and briefs and not any(_usable(i) for i in images):
            board["images"] = [
                {
                    "slot": b.get("slot"),
                    "image_id": f"placeholder:{b.get('slot')}",
                    "positive": "",
                    "pending": False,
                }
                for b in briefs
                if b.get("slot") in ("portrait", "full", "prop")
            ]
            slots = {img.get("slot") for img in board["images"] if _usable(img)}
        if "portrait" not in slots or "full" not in slots:
            raise WeaveError("board needs rendered portrait and full images before accept")
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
    db=None,
    embed_model: str = "",
) -> dict[str, Any]:
    from .character.gallery_nn import enrich_character_from_gallery, is_gallery_nn_enabled

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
    character = session["character"]
    character["identity_locked"] = False
    character["board"] = {"images": [], "accepted": False}
    character["gallery_refs"] = []
    character["gallery_spice"] = []
    character["gallery_nn"] = None
    character["reference_mix"] = None
    base_identity = list(character.get("identity_tags") or [])
    # Invalidate story if re-infer
    if int(session.get("story_version") or 0) > 0:
        session["story_bundle"] = {}
        session["story_version"] = 0
        session["last_lint"] = None
        session["status"] = "character"
    append_timeline(
        session, actor="llm.personalitywright", type_="proposal",
        text=character.get("reasoning_ja") or "character inferred",
    )
    gallery_summary: dict[str, Any] = {"applied": False, "added_identity": []}
    if is_gallery_nn_enabled(session) and db is not None:
        gallery_summary = await enrich_character_from_gallery(
            session,
            db=db,
            ollama=ollama,
            embed_model=embed_model or "nomic-embed-text",
        )
        if gallery_summary.get("applied"):
            n = gallery_summary.get("neighbor_count") or 0
            added = len(gallery_summary.get("added_identity") or [])
            append_timeline(
                session, actor="system.gallery_nn", type_="enrich",
                text=f"gallery NN: {n} neighbors, +{added} identity tags",
                ref={"gallery_nn": gallery_summary},
            )
        elif gallery_summary.get("reason") not in (None, "skipped"):
            append_timeline(
                session, actor="system.gallery_nn", type_="message",
                text=f"gallery NN skipped: {gallery_summary.get('reason')}",
            )

    from .character.reference_mix import apply_reference_mix

    ref_summary = await apply_reference_mix(session, db)
    character["reference_mix"] = ref_summary
    if ref_summary.get("applied"):
        append_timeline(
            session, actor="system.reference_mix", type_="enrich",
            text="reference hair/eyes applied: "
            + ", ".join(ref_summary.get("added_from_reference") or []),
        )

    after = list(character.get("identity_tags") or [])
    base_set = set(base_identity)
    character["tag_diff"] = {
        "base_identity": base_identity,
        "after_enrichment": after,
        "added_from_gallery": list(gallery_summary.get("added_identity") or []),
        "added_from_reference": list(ref_summary.get("added_from_reference") or []),
        "added": [t for t in after if t not in base_set],
        "removed": [t for t in base_identity if t not in set(after)],
        "spice": list(character.get("gallery_spice") or []),
        "gallery_nn_reason": gallery_summary.get("reason"),
        "reference_reason": ref_summary.get("reason"),
    }
    from .character.topic_fit import apply_topic_warnings

    warns = apply_topic_warnings(session)
    if warns:
        append_timeline(
            session, actor="system", type_="message",
            text="topic/outfit warnings: " + "; ".join(w["problem"] for w in warns),
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


async def _lint_repair_apply(
    session: dict[str, Any],
    bundle: dict[str, Any],
    ollama,
    *,
    model: str,
    options: dict,
    topic: str,
    actor_note: str,
) -> dict[str, Any]:
    """Lint → Repairer×1 → apply. Sets last_lint; G1 only passes when lint ok."""
    from .story.repairer import run_repairer

    character = session.get("character") or {}
    lint = lint_story_bundle(bundle, character)
    repaired = False
    if not lint["pass"]:
        try:
            bundle = await run_repairer(
                ollama,
                model=model,
                options=options,
                story_bundle=bundle,
                defects=list(lint.get("defects") or []),
                character=character,
                topic=topic,
            )
            lint = lint_story_bundle(bundle, character)
            repaired = True
            append_timeline(
                session, actor="llm.repairer", type_="message",
                text="repairer×1 attempted",
            )
        except Exception as exc:
            logger.warning("[weave] repairer failed: %s", exc)
            append_timeline(
                session, actor="system", type_="message",
                text=f"repairer failed: {exc}",
            )

    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session.setdefault("cross_panel_qa", {})["throughline_coverage"] = lint.get(
        "throughline_coverage"
    )
    session["critic_report"] = None
    if lint["pass"]:
        append_timeline(
            session, actor="llm.storywright", type_="message",
            text=(bundle.get("world") or {}).get("causality_one_liner") or actor_note,
        )
    else:
        defects = lint.get("defects") or []
        policy = session.get("quality_policy") or {}
        critic_mode = str(policy.get("critic") or "on_lint_fail")
        if critic_mode in ("on_lint_fail", "strict", "always"):
            from .story.critic import code_critic_fallback, run_critic

            try:
                report = await run_critic(
                    ollama,
                    model=model,
                    options={**options, "temperature": 0.2},
                    story_bundle=bundle,
                    defects=defects,
                    topic=topic,
                )
            except Exception as exc:
                logger.warning("[weave] critic failed: %s", exc)
                report = code_critic_fallback(defects)
            session["critic_report"] = report
            append_timeline(
                session, actor="llm.critic", type_="message",
                text=report.get("summary_ja") or "critic report",
                ref={"critic_report": report},
            )
        append_timeline(
            session, actor="system", type_="message",
            text=(
                f"story lint failed after"
                f"{' repair' if repaired else ''}: {len(defects)} defects — recreate only"
            ),
            ref={"defects": defects},
        )
    return session


async def generate_story(
    session: dict[str, Any],
    ollama,
    *,
    model: str,
    options: dict | None = None,
    topic: str | None = None,
    db=None,
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
    from .character.authors import resolve_author_style
    from .character.topic_fit import apply_topic_warnings

    await resolve_author_style(session, db)
    apply_topic_warnings(session)
    opts = options or {"temperature": 0.7}
    author_style = str(inputs.get("author_style") or "")
    bundle = await run_storywright(
        ollama,
        model=model,
        options=opts,
        topic=topic_s,
        character=session.get("character") or {},
        author_style=author_style,
        avoid_motifs=list(session.get("avoid_motifs") or []),
    )
    return await _lint_repair_apply(
        session, bundle, ollama,
        model=model, options=opts, topic=topic_s,
        actor_note="story generated",
    )


async def recreate_story(
    session: dict[str, Any],
    ollama,
    *,
    model: str,
    chips: list[str],
    options: dict | None = None,
    db=None,
) -> dict[str, Any]:
    if not chips:
        raise WeaveError("recreate requires reason chips")
    if session.get("status") in ("rendering", "sealed"):
        raise WeaveError("cannot recreate while rendering/sealed", status_code=409)
    from .character.authors import resolve_author_style

    await resolve_author_style(session, db)
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
    opts = options or {"temperature": 0.8}
    bundle = await run_storywright(
        ollama,
        model=model,
        options=opts,
        topic=topic_s,
        character=session.get("character") or {},
        author_style=str(inputs.get("author_style") or ""),
        recreate_constraints=constraints,
        avoid_motifs=list(session.get("avoid_motifs") or []),
        previous_causality=str(prev_causal),
    )
    await _lint_repair_apply(
        session, bundle, ollama,
        model=model, options=opts, topic=topic_s,
        actor_note=f"recreated with chips={chips}",
    )
    session["status"] = "story"
    return session


def rollback_story(session: dict[str, Any], to_version: int) -> dict[str, Any]:
    hist = session.get("story_history") or []
    match = next((h for h in hist if int(h.get("version") or 0) == int(to_version)), None)
    if not match:
        raise WeaveError(f"version {to_version} not found in history")
    # Keep current in history before restore
    _push_history(session, reasons=["rollback"], constraints=[])
    bundle = normalize_story_bundle(copy.deepcopy(match["bundle"]))
    lint = lint_story_bundle(bundle, session.get("character") or {})
    apply_story_to_session(session, bundle)
    session["last_lint"] = lint
    session.setdefault("cross_panel_qa", {})["throughline_coverage"] = lint.get(
        "throughline_coverage"
    )
    session["status"] = "story"
    session["suggest_recreate"] = False
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
            session["suggest_recreate"] = True
            append_timeline(
                session, actor="system", type_="message",
                text="story unclear — recreate required (look-dev samples will be discarded)",
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
            if panel.get("intent") is not None:
                panel["intent"]["focus"] = str(
                    (session.get("character") or {}).get("signature_prop") or "prop"
                )
            compile_all_panels(session)
            continue
        if chip in ("dead_expression", "表情死"):
            session.setdefault("constraints", []).append({
                "id": f"c-emo-{panel_key}",
                "source": "user_comment",
                "scope": panel_key,
                "text": "face_visible_emotion",
                "active": True,
            })
            if panel.get("intent") is not None:
                emo = str(panel["intent"].get("emotion") or "").strip()
                if not emo or emo in ("serious", "expressionless", "blank"):
                    panel["intent"]["emotion"] = "soft smile"
            compile_all_panels(session)
            continue
        if chip in ("wrong_person", "別人"):
            session["suggest_reinfer"] = True
            append_timeline(
                session, actor="system", type_="message",
                text="wrong person — confirm re-infer (story will be wiped)",
            )
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


async def reeval_framing(session: dict[str, Any], db) -> dict[str, Any]:
    """Re-read WD14 for long_shot samples whose framing is unknown/pending."""
    from .verify.heuristics import apply_framing_to_panel, resolve_wd14_for_image

    updated = 0
    for panel in session.get("panels") or []:
        cam = ((panel.get("intent") or {}).get("camera") or "")
        if cam != "long_shot":
            continue
        fr = (panel.get("qa") or {}).get("framing")
        if fr == "pass":
            continue
        iid = str((panel.get("sample") or {}).get("image_id") or "")
        if not iid:
            continue
        wd14 = await resolve_wd14_for_image(db, iid)
        apply_framing_to_panel(panel, wd14)
        updated += 1
    append_timeline(
        session, actor="system", type_="message",
        text=f"framing re-evaluated on {updated} panel(s)",
    )
    return session


async def project_to_storybook(session: dict[str, Any], db) -> str:
    """Write a sealed Weave session into the Storybook stories collection."""
    from ..story import db as story_db

    inputs = session.get("inputs") or {}
    bundle = session.get("story_bundle") or {}
    world = bundle.get("world") or {}
    sid = str(session.get("session_id") or "")
    payload = story_db.new_story_payload(
        base_image_id=str(inputs.get("reference_image_id") or ""),
        workflow_name=str(inputs.get("workflow_final") or inputs.get("workflow_sample") or ""),
        group_id=sid or "weave",
        title=str(bundle.get("title") or ""),
        overall_story=str(world.get("causality_one_liner") or ""),
        user_topic=str(inputs.get("topic") or ""),
        locale=str(session.get("locale") or "ja"),
        status="final",
        author_style=str(inputs.get("author_style") or ""),
        time_scale=str(world.get("time_scale") or ""),
    )
    payload["overall_story_ja"] = str(world.get("causality_one_liner") or "")
    payload["title_ja"] = str(bundle.get("title") or "")
    for panel in session.get("panels") or []:
        key = str(panel.get("key") or "")
        if key not in story_db.AXES:
            continue
        intent = panel.get("intent") or {}
        compiled = panel.get("compile") or {}
        final = panel.get("final") or {}
        payload["axes"][key] = {
            "story": str(intent.get("narrative_en") or intent.get("visible_change") or ""),
            "story_ja": str(intent.get("narrative_ja") or ""),
            "prompt_positive": compiled.get("positive"),
            "prompt_negative": compiled.get("negative"),
            "image_id": final.get("image_id"),
        }
    payload["context"] = {
        "weave_session_id": sid,
        "source": "weave",
        "identity_tags": list((session.get("character") or {}).get("identity_tags") or []),
        "prop_tags": list((session.get("character") or {}).get("prop_tags") or []),
        "signature_prop": (session.get("character") or {}).get("signature_prop") or "",
        "setting": world.get("setting") or "",
        "throughline_prop": world.get("throughline_prop") or "",
    }
    story_id = await story_db.create_story(db, payload)
    session["storybook_story_id"] = story_id
    return story_id


def export_bundle(session: dict[str, Any]) -> dict[str, Any]:
    """Agent-facing export snapshot (no disk write)."""
    from .verify.seal import evaluate_seal_rubric

    panels_out = []
    for p in session.get("panels") or []:
        sample = p.get("sample") or {}
        final = p.get("final") or {}
        sid = sample.get("image_id")
        fid = final.get("image_id")
        panels_out.append({
            "key": p.get("key"),
            "intent": p.get("intent"),
            "compile": p.get("compile"),
            "qa": p.get("qa"),
            "sample_image_id": sid,
            "final_image_id": fid,
            "sample_url": f"/api/images/{sid}" if sid and not str(sid).startswith("pending") else None,
            "final_url": f"/api/images/{fid}" if fid and not str(fid).startswith("pending") else None,
            "thumb_url": (
                f"/api/thumbnails/{fid or sid}.webp"
                if (fid or sid) and not str(fid or sid).startswith(("pending", "placeholder"))
                else None
            ),
        })
    return {
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "inputs": session.get("inputs"),
        "character": {
            "identity_tags": (session.get("character") or {}).get("identity_tags"),
            "prop_tags": (session.get("character") or {}).get("prop_tags"),
            "signature_prop": (session.get("character") or {}).get("signature_prop"),
            "gallery_spice": (session.get("character") or {}).get("gallery_spice"),
            "tag_diff": (session.get("character") or {}).get("tag_diff"),
            "board": (session.get("character") or {}).get("board"),
        },
        "story_bundle": session.get("story_bundle"),
        "story_version": session.get("story_version"),
        "last_lint": session.get("last_lint"),
        "panels": panels_out,
        "gates": gates(session),
        "seal_rubric": evaluate_seal_rubric(session),
        "storybook_story_id": session.get("storybook_story_id"),
        "timeline": session.get("timeline") or [],
    }

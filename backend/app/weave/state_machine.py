"""Next CTA and gate evaluation (code facilitator)."""
from __future__ import annotations

from typing import Any


def gates(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    character = session.get("character") or {}
    board = character.get("board") or {}
    images = board.get("images") or []
    slots = {img.get("slot") for img in images if img.get("image_id")}
    identity_locked = bool(character.get("identity_locked"))
    board_accepted = bool(board.get("accepted"))
    has_portrait_full = "portrait" in slots and "full" in slots

    story_version = int(session.get("story_version") or 0)
    bundle = session.get("story_bundle") or {}
    has_story = story_version > 0 and bool(bundle.get("world"))
    lint = session.get("last_lint")
    lint_pass = bool(lint and lint.get("pass"))

    samples_viewed = 0
    framing_ok = True
    for p in session.get("panels") or []:
        if (p.get("sample") or {}).get("image_id"):
            samples_viewed += 1
        cam = ((p.get("intent") or {}).get("camera") or "")
        if cam == "long_shot":
            fr = (p.get("qa") or {}).get("framing")
            overridden = any(
                o.get("panel_key") == p.get("key")
                for o in (session.get("framing_overrides") or [])
            )
            if fr == "fail" and not overridden:
                framing_ok = False

    policy = session.get("quality_policy") or {}
    min_samples = int(policy.get("min_sample_panels") or 1)

    # G2 warn: unique cameras + throughline coverage
    cams = [
        str((p.get("intent") or {}).get("camera") or "")
        for p in (session.get("panels") or [])
    ]
    cam_ok = (
        len([c for c in cams if c]) >= 3
        and len(set(c for c in cams if c)) == len([c for c in cams if c])
    )
    coverage = (lint or {}).get("throughline_coverage")
    if coverage is None:
        coverage = (session.get("cross_panel_qa") or {}).get("throughline_coverage")
    through_ok = coverage is None or int(coverage or 0) >= 3

    cross = session.get("cross_panel_qa") or {}
    from .verify.seal import evaluate_seal_rubric

    seal = evaluate_seal_rubric(session) if has_story else {
        "pass": False, "full_pass": False, "strict": bool(policy.get("strict_seal")),
    }

    return {
        "G0_soft": {
            "pass": identity_locked,
            "detail": "identity_locked",
        },
        "G0_hard": {
            "pass": board_accepted and has_portrait_full,
            "detail": "board.accepted with portrait+full",
        },
        "G1": {
            "pass": has_story and lint_pass,
            "detail": "story lint pass" if lint_pass else (
                "story lint failed — recreate" if has_story else "no story"
            ),
            "defects": list((lint or {}).get("defects") or []),
        },
        "G2": {
            "pass": (not has_story) or (cam_ok and through_ok),
            "detail": f"cameras_unique={cam_ok} throughline={through_ok}",
            "severity": True,
        },
        "G3": {
            "pass": samples_viewed >= min_samples,
            "detail": f"samples_viewed={samples_viewed} need>={min_samples}",
        },
        "G4": {
            "pass": framing_ok,
            "detail": "long_shot framing ok or overridden",
        },
        "G5": {
            "pass": bool(cross.get("ready_for_final")),
            "detail": "cross_panel ready_for_final",
            "severity": True,
        },
        "G6": {
            "pass": bool(seal.get("full_pass")),
            "detail": "seal rubric full_pass",
            "strict_only": True,
            "checks": seal.get("checks") or {},
        },
    }


def next_cta(session: dict[str, Any]) -> dict[str, Any]:
    """Return the single next action for the UI."""
    status = session.get("status") or "character"
    g = gates(session)
    character = session.get("character") or {}
    inputs = session.get("inputs") or {}
    policy = session.get("quality_policy") or {}
    lint = session.get("last_lint") or {}
    defects = list(lint.get("defects") or [])

    if status == "sealed":
        return {"code": "done", "label": "セッション完了", "enabled": False}

    if status == "rendering":
        return {"code": "wait_render", "label": "本番レンダー中", "enabled": False}

    if not character.get("identity_tags") and not character.get("identity_locked"):
        return {
            "code": "infer_character",
            "label": "パーソナリティからキャラを類推",
            "enabled": True,
        }

    if not g["G0_soft"]["pass"]:
        diff = character.get("tag_diff") or {}
        label = "identity をロック"
        if diff.get("added") or diff.get("spice"):
            label = "タグ差分を確認して identity をロック"
        return {
            "code": "lock_identity",
            "label": label,
            "enabled": bool(character.get("identity_tags")),
            "tag_diff": diff,
        }

    # Story present but lint failed → Recreate is the only exit
    story_version = int(session.get("story_version") or 0)
    if story_version > 0 and not g["G1"]["pass"]:
        return {
            "code": "recreate_story",
            "label": "理由を付けて再作成",
            "enabled": True,
            "defects": defects,
        }

    if not g["G1"]["pass"]:
        topic_ok = bool(str(inputs.get("topic") or "").strip())
        return {
            "code": "generate_story",
            "label": "ストーリーを1本作る",
            "enabled": topic_ok,
            "needs": [] if topic_ok else ["topic"],
        }

    if status in ("character", "story"):
        return {
            "code": "enter_lookdev",
            "label": "Look-dev（サンプル検品）へ進む",
            "enabled": True,
        }

    if status == "lookdev":
        if not g["G3"]["pass"]:
            return {
                "code": "sample_panel",
                "label": "サンプルを生成して確認",
                "enabled": True,
            }
        if not g["G4"]["pass"]:
            return {
                "code": "fix_framing_or_override",
                "label": "構図を直す / 理由付きでオーバーライド",
                "enabled": True,
            }
        if not g["G0_hard"]["pass"]:
            allow = bool(policy.get("allow_story_before_board", True))
            return {
                "code": "accept_board",
                "label": "イメージボードを採用（本番前に必須）",
                "enabled": allow,
            }
        return {
            "code": "render_final",
            "label": "本番3枚を生成",
            "enabled": True,
        }

    return {"code": "unknown", "label": "状態を確認", "enabled": False}

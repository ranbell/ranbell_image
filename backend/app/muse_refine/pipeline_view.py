"""Pipeline view for Muse Refine debug — aggregate existing writers, no second log.

Attached to ``public_view`` so GET /api/muse-refine/sessions/{id} carries a stable
``pipeline`` summary the panel (and external eval) can read — same role as
``muse.pipeline_view`` for classic Muse.
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import ledger as ledger_mod

PIPELINE_SCHEMA = "muse_refine.pipeline.v1"

_STAGE_IDS = (
    "writer",
    "cue",
    "ledger",
    "muse_propose",
    "verify",
    "assemble",
    "actress",
    "board",
)


def _tokens(text: str) -> set[str]:
    """照合用の語。**下線でも空白でも同じ語になるように割る。**（2026-09-10）

    総監督「ずっと missing と出ているけど理由は？」。値は絵に入っていた ——
    突き合わせ方が揃っていなかっただけ:

        台帳          casual_clothes            ← writer は danbooru 風に書く
        craft.prompt  casual clothes            ← `anima.format_for_anima` が
                                                   最後に下線を空白へ戻す
        重なり        （無し）→ 「missing」

    繋がったままの語（`casual_clothes`）と、割った語（`casual` / `clothes`）の
    **両方**を返す。完全一致も、書き方の違いも、どちらも拾える。
    """
    out: set[str] = set()
    for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", str(text or "").lower()):
        out.add(t.replace("-", "_"))
        out.update(w for w in re.split(r"[_-]+", t) if len(w) >= 3)
    return out


def _last_note(session: dict[str, Any], *kinds: str) -> dict[str, Any] | None:
    for row in reversed(list(session.get("refine_log") or [])):
        if str(row.get("kind") or "") in kinds:
            return row
    return None


def _last_stage(session: dict[str, Any], *names: str) -> dict[str, Any] | None:
    want = {n.lower() for n in names}
    for row in reversed(list(session.get("stage_ms") or [])):
        if str(row.get("stage") or "").lower() in want:
            return row
    return None


def _divergences(session: dict[str, Any]) -> list[dict[str, str]]:
    """Ledger shot phrases that never reached craft.prompt / board.prompt.

    Sticky mood/look/lettering densify into other words and false-alarm often —
    exclude them (classic Muse also skips lettering-like noise).
    """
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    craft = session.get("craft") or {}
    board = session.get("board") or {}
    # **会話中のプロンプトは一手ぶん古い（2026-09-10）。** 会話のターンでは
    # 散文とタグの組み上げを撮る時まで待つ（`assemble.touch_craft`）ので、
    # 監督がいま動かした欄は、まだ `craft.prompt` に載っていなくて当たり前。
    # ここで「missing」と言うと、毎ターン嘘の警告が並ぶ。撮る直前に組み直され、
    # `stale` が下りてから比べる。
    prompt = "" if craft.get("stale") else str(craft.get("prompt") or "")
    board_prompt = str(board.get("prompt") or "")
    prompt_tok = _tokens(prompt)
    board_tok = _tokens(board_prompt) if board_prompt else set()
    skip = set(getattr(ledger_mod, "STICKY_KEYS", ()) or ()) | {"lettering"}
    out: list[dict[str, str]] = []
    for key in ledger_mod.LEDGER_KEYS:
        if key in skip:
            continue
        raw = str(led.get(key) or "").strip()
        if not raw:
            continue
        field_tok = {t for t in _tokens(raw) if len(t) >= 4}
        if not field_tok:
            continue
        if prompt and not (field_tok & prompt_tok):
            out.append({
                "kind": "prompt_vs_ledger",
                "field": key,
                "detail": "in ledger, missing in craft.prompt",
            })
        if board_prompt and not (field_tok & board_tok):
            out.append({
                "kind": "board_vs_ledger",
                "field": key,
                "detail": "in ledger, missing in board.prompt",
            })
    return out[:24]


def build_pipeline_view(session: dict[str, Any]) -> dict[str, Any]:
    """Aggregate writer → cue → ledger → propose → verify → assemble → actress → board."""
    trace = list(session.get("turn_trace") or [])
    last_trace = trace[-1] if trace else {}
    moved = dict(last_trace.get("moved") or {})
    patch = dict(last_trace.get("patch") or {})
    propose = dict(last_trace.get("propose") or {})
    rewrite = list(session.get("rewrite_log") or [])
    rewrite_sources = {str(e.get("source") or "") for e in rewrite[-12:]}
    rewrite_fields: list[str] = []
    for e in rewrite[-12:]:
        changed = e.get("changed") or {}
        rewrite_fields.extend(str(k) for k in changed)

    writer_note = _last_note(session, "writer_patch", "writer_retry")
    cue_note = _last_note(session, "atm_look_cue")
    verify_note = _last_note(session, "verify", "verify_result")
    actress_note = _last_note(session, "actress")
    missed_note = _last_note(session, "writer_missed", "turn_missed_picture")

    craft = session.get("craft") or {}
    board = session.get("board") or {}
    prompt = str(craft.get("prompt") or "").strip()
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    filled = [k for k in ledger_mod.LEDGER_KEYS if str(led.get(k) or "").strip()]

    board_fp = str(board.get("ledger_fp") or "")
    cur_fp = "|".join(str(led.get(k) or "") for k in ledger_mod.LEDGER_KEYS)
    board_images = list(board.get("images") or [])
    if board.get("pending"):
        board_status = "pending"
    elif board_images and board_fp and board_fp == cur_fp:
        board_status = "frozen"
    elif board_images:
        board_status = "stale"
    else:
        board_status = "empty"

    verify_ok = None
    if verify_note is not None and "ok" in verify_note:
        verify_ok = bool(verify_note.get("ok"))
    elif verify_note and str(verify_note.get("detail") or "") in {"ok", "repaired"}:
        verify_ok = str(verify_note.get("detail")) == "ok"

    writer_ok = bool(patch or writer_note) or bool(
        rewrite_sources & {"writer", "director", "self_repair", "muse", "lettering", "restate"}
    )
    stages: list[dict[str, Any]] = [
        {
            "id": "writer",
            "status": (
                "missed" if missed_note and not patch and not rewrite_fields
                else ("ok" if writer_ok else "empty")
            ),
            "keys": sorted(set(list(patch.keys()) + rewrite_fields)),
            "ms": (_last_stage(session, "writer", "writer_retry") or {}).get("ms"),
        },
        {
            "id": "cue",
            "status": "ok" if cue_note or ("lettering" in rewrite_sources) else "empty",
            "detail": str((cue_note or {}).get("detail") or "")[:120],
        },
        {
            "id": "ledger",
            "status": "ok" if filled or rewrite_fields else "empty",
            "filled": filled,
            "moved": sorted(set(list(moved.keys()) + rewrite_fields)),
        },
        {
            "id": "muse_propose",
            "status": "ok" if propose or ("muse" in rewrite_sources) else "empty",
            "keys": sorted(propose.keys()),
        },
        {
            "id": "verify",
            "status": (
                "ok" if verify_ok is True
                else ("missed" if verify_ok is False else ("ok" if verify_note else "empty"))
            ),
            "ok": verify_ok,
            "ms": (_last_stage(session, "verify") or {}).get("ms"),
        },
        {
            "id": "assemble",
            "status": "ok" if prompt else "empty",
            "chars": len(prompt),
            "ms": (_last_stage(
                session, "assemble_pre_actress", "assemble_after_propose",
                "assemble_after_repair", "prose_densify",
            ) or {}).get("ms"),
        },
        {
            "id": "actress",
            "status": "ok" if actress_note else "empty",
            "ms": (_last_stage(session, "actress", "open_actress") or {}).get("ms"),
        },
        {
            "id": "board",
            "status": board_status,
            "images": len(board_images),
            "pending": bool(board.get("pending")),
        },
    ]
    by_id = {s["id"]: s for s in stages}
    ordered = [by_id[i] for i in _STAGE_IDS if i in by_id]
    return {
        "schema": PIPELINE_SCHEMA,
        "at": time.time(),
        "stages": ordered,
        "divergences": _divergences(session),
    }

"""Muse Refine session orchestration — independent of muse.service."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..characters import presets as presets_db
from . import events, session_db, vitality
from .defaults import ALL_DEFAULTS
from .notebook import blank as notebook_blank
from . import assemble, crew_room, debug as debug_mod, ledger as ledger_mod
from . import persona, pipeline_view, talk, writer
from .ctx import refine_num_ctx

logger = logging.getLogger(__name__)

#: **A stored value, so it does not change.** It is written on the session row and
#: is the partition for `session_db.list_recent(studio=...)` as well as the key this
#: studio uses to tell its own rows apart (`_require_studio`). The package and the
#: URLs were folded into `muse` (2026-09-12); move this string and **existing rows
#: stop opening**.
STUDIO = "muse_refine"


class RefineError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return dict(session.get("inputs") or {})


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    """Panel payload — keep it small and stable."""
    inputs = _inputs(session)
    craft = session.get("craft") or {}
    char = session.get("character") or {}
    partner = session.get("partner_character") or {}
    board = session.get("board") or {}
    shoot = session.get("shoot") or {}
    bond = session.get("bond") or {}
    board_ready = bool(board.get("images")) and not board.get("pending")
    return {
        "session_id": session.get("session_id"),
        "studio": STUDIO,
        "status": session.get("status"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "inputs": {
            "theme": inputs.get("theme", ""),
            "character_id": inputs.get("character_id", ""),
            "partner_preset": inputs.get("partner_preset", ""),
            # **Return the values the screen reads (2026-09-13).** A field was
            # added to `InputsPatch` yesterday so it could be *sent*, and it was
            # **not returned** — the screen reads
            # `inputs.crew_preset || 'standard'`, so a choice that saved correctly
            # looked like `standard` again on reopening. **The action works and the
            # display lies**, the hardest breakage to notice: only a round trip
            # shows it.
            "crew_preset": inputs.get("crew_preset", ""),
            "banter_mode": inputs.get("banter_mode", ""),
            "workflow": inputs.get("workflow", ""),
            "model": inputs.get("model", ""),
            "locale": inputs.get("locale", "ja"),
            "style": inputs.get("style", ""),
            "framing": inputs.get("framing", "auto"),
            "negative_prompt": inputs.get("negative_prompt", ""),
            "draft_count": inputs.get("draft_count", 1),
            "draft_steps": inputs.get("draft_steps", 20),
            "final_steps": inputs.get("final_steps", 30),
            "enhance_quality": bool(inputs.get("enhance_quality")),
            "width": inputs.get("width"),
            "height": inputs.get("height"),
        },
        "character": {
            "character_id": char.get("character_id", ""),
            # Keep both. Collapsing to name_ja made the English panel show
            # 「二宮 かなで」 even though the picker already switches on locale.
            "name": char.get("name") or char.get("name_ja") or "",
            "name_ja": char.get("name_ja") or char.get("name") or "",
            "board": char.get("board") or {},
        },
        "partner_character": {
            "character_id": partner.get("character_id", ""),
            "name": partner.get("name") or partner.get("name_ja") or "",
            "name_ja": partner.get("name_ja") or partner.get("name") or "",
            "board": partner.get("board") or {},
        } if partner else {},
        "refine_ledger": session.get("refine_ledger") or ledger_mod.blank(),
        "craft": {
            "prompt": craft.get("prompt", ""),
            "now": craft.get("now", ""),
            "tags": craft.get("tags", ""),
            "scene": craft.get("scene", ""),
            "quality_tags": craft.get("quality_tags", ""),
            "support_tags": craft.get("support_tags", ""),
            # On a conversation turn, building the prose and tags waits until the
            # shot (`touch_craft`). The screen reads this flag to say it will be
            # rebuilt on the test shot.
            "stale": bool(craft.get("stale")),
        },
        "chat": list(session.get("chat") or [])[-40:],
        "standing": list(session.get("standing") or [])[-8:],
        "last_pitch": list(session.get("last_pitch") or [])[:2],
        "feel_log": list(session.get("feel_log") or [])[-8:],
        "bond": {
            "last": str(bond.get("last") or "")[:120],
            "inside": str(bond.get("inside") or "")[:120],
        },
        "board": {
            "images": list(board.get("images") or [])[-4:],
            "status": board.get("status", ""),
            "error": board.get("error", ""),
            "pending": bool(board.get("pending")),
            "ready": board_ready,
            "job_id": str(board.get("job_id") or ""),
        },
        "shoot": {
            "images": list(shoot.get("images") or [])[-4:],
            "status": shoot.get("status", ""),
            "error": shoot.get("error", ""),
            "pending": bool(shoot.get("pending")),
            "job_id": str(shoot.get("job_id") or ""),
        },
        "diary": session.get("diary") or {},
        "opened": bool(session.get("opened")),
        # The studio shoot (with a crew). The screen chooses which buttons to show
        # from these two.
        "crew_open": bool(session.get(crew_room.TABLE_OPEN)),
        "crew_seats": len(crew_room.cast_of(session)) if session.get(crew_room.TABLE_OPEN) else 0,
        "banned": list(session.get("banned") or [])[-20:],
        "struck": list(session.get("struck") or [])[-20:],
        "taste_chips": vitality.taste_chips(
            session.get("showrunner_taste") or {},
            locale=str(inputs.get("locale") or "ja"),
        ),
        # Observability only — UI debug pane / external eval. Never used for decisions.
        "refine_log": list(session.get("refine_log") or [])[-40:],
        "stage_ms": list(session.get("stage_ms") or [])[-20:],
        "turn_trace": list(session.get("turn_trace") or [])[-12:],
        "rewrite_log": list(session.get("rewrite_log") or [])[-24:],
        "pipeline": pipeline_view.build_pipeline_view(session),
    }


def new_session(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    base = {
        **ALL_DEFAULTS,
        "theme": "",
        "character_id": "",
        "workflow": "",
        "model": "",
        "locale": "ja",
        "enhance_quality": False,
    }
    merged = {**base, **(inputs or {})}
    merged["enhance_quality"] = bool(merged.get("enhance_quality"))
    return {
        "session_id": str(uuid.uuid4()),
        "studio": STUDIO,
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "chat",
        "mode": "duet",
        "inputs": merged,
        "character": {},
        "partner_character": {},
        "refine_ledger": ledger_mod.blank(),
        "craft": {
            "prompt": "", "now": "", "tags": "", "scene": "",
            "quality_tags": "", "support_tags": "",
        },
        # Keep a blank notebook so muse.session_db.load → notebook.migrate is safe
        # when board/shoot runner reloads the row.
        "notebook": notebook_blank(partner=False),
        "chat": [],
        "board": {},
        "shoot": {},
        "banned": [],
        "struck": [],
        "notes": [],
        "standing": [],
        "memories": [],
        "diary_memories": [],
        "bond": {},
        "caught": {},
        "feel_log": [],
        "last_pitch": [],
        "prop_age": {"fp": "", "turns": 0},
        "reunion_turn": False,
        "commit_pitch": False,
        "talk_turn_count": 0,
        "shot_compile_count": 0,
        "opened": False,
        "showrunner_taste": {},
        "cleanup_nudge": False,
        "w_b_leads": False,
        "refine_log": [],
        "stage_ms": [],
        "turn_trace": [],
        "rewrite_log": [],
    }


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def load_refine(db, session_id: str) -> dict[str, Any]:
    session = await session_db.load(db, session_id)
    if session is None:
        raise RefineError("session not found")
    if str(session.get("studio") or "") != STUDIO:
        raise RefineError("not a muse refine session")
    # Ensure ledger key exists after migrate.
    session.setdefault("refine_ledger", ledger_mod.blank())
    session.setdefault("craft", {})
    session.setdefault("rewrite_log", [])
    session.setdefault("refine_log", [])
    session.setdefault("stage_ms", [])
    session.setdefault("turn_trace", [])
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in patch.items() if v is not None}
    for flag in ("enhance_quality",):
        if flag in clean:
            clean[flag] = bool(clean[flag])
    session["inputs"] = {**_inputs(session), **clean}
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise RefineError("character not found")
    char = {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["character"] = char
    session["inputs"] = {**_inputs(session), "character_id": character_id}
    # Seed wearing from preset costume if ledger empty.
    led = dict(session.get("refine_ledger") or ledger_mod.blank())
    if not (led.get("wearing") or "").strip():
        costume = (preset.get("board") or {}).get("wearing") or ""
        wearing = costume or preset.get("wearing") or ""
        if wearing:
            led["wearing"] = str(wearing).strip()
            session["refine_ledger"] = led
    await persona.load_memory(db, session)
    persona.mark_reunion(session)
    await assemble.rebuild_craft(db, None, session)
    await session_db.save(db, session)
    return session


async def pick_partner(db, session: dict[str, Any], partner_id: str) -> dict[str, Any]:
    """Cast / clear W-Muse partner. Empty string clears."""
    partner_id = (partner_id or "").strip()
    if not partner_id:
        session["partner_character"] = {}
        session["inputs"] = {**_inputs(session), "partner_preset": ""}
        session.pop("duet_tier", None)
        await assemble.rebuild_craft(db, None, session)
        await session_db.save(db, session)
        return session
    lead_id = str(_inputs(session).get("character_id") or "")
    if partner_id == lead_id:
        raise RefineError("partner must differ from lead")
    preset = await presets_db.get_preset(db, partner_id)
    if preset is None:
        raise RefineError("character not found")
    session["partner_character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": partner_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "partner_preset": partner_id}
    # Chemistry tier for W-Muse prompt colour.
    try:
        from ..characters import compat as compat_mod
        if lead_id:
            compat = await compat_mod.compatibility(db, lead_id, partner_id)
            session["duet_tier"] = {
                "partner_id": partner_id,
                "tier": str(compat.get("tier") or ""),
            }
            session["chemistry_notes"] = await presets_db.get_recent_chemistry_notes(
                db, lead_id, limit=1, partner_id=partner_id,
            )
    except Exception:
        logger.debug("[muse] partner chemistry load failed", exc_info=True)
    await assemble.rebuild_craft(db, None, session)
    await session_db.save(db, session)
    return session


def set_standing(session: dict[str, Any], rules: list[str]) -> dict[str, Any]:
    cleaned = [str(s).strip()[:120] for s in (rules or []) if str(s).strip()]
    session["standing"] = cleaned[-12:]
    return session


def restore_banned(session: dict[str, Any], tag: str) -> dict[str, Any]:
    if not talk.restore_tag(session, tag):
        raise RefineError("tag not in banned list")
    return session


async def _load_runtime_cfg(db, session: dict[str, Any]) -> dict[str, Any]:
    """Put the runtime config on the session. **So the context length matches the
    clerks'.**"""
    try:
        from ..runtime_config import get_runtime_config
        cfg = await get_runtime_config(db)
    except Exception:
        logger.debug("[muse] runtime config unavailable", exc_info=True)
        return {}
    session["_runtime_cfg"] = cfg
    return cfg


async def _theme_into_ledger(
    ollama,
    session: dict[str, Any],
    *,
    theme: str,
    model: str,
    locale: str,
) -> dict[str, str]:
    """Write the opening theme into the ledger, through the Scripter.

    **The theme reached the picture through nobody (2026-09-20).** The Showrunner:
    "Muse cannot handle the opening theme of a session — saying 'a walk in the
    park' is not reflected." Measured over every stored session that carried one
    (159 of them): the ledger's `scene` / `bg` / `beat` moved on the Showrunner's
    **first chat line**, never on the theme — in most of them that line was the
    theme typed a second time (「じゃあ二人とも公園の遊歩道を…」), which is what hid
    this. Say something that assumes the place is already known — 「じゃあ二人で構図を
    考えて」 — and the writer has no place to put it: only the two expressions moved,
    and the turn came back marked 未反映.

    `open_session` handed the theme to the actress as her cue (`user_line`) and to
    the record (the `Theme` row, the diary, the lounge) and nowhere else. The
    ledger is written by one hand and one hand only — `writer.write_patch` — and
    nothing called it until the first chat turn, so the board was built
    (`assemble.rebuild_craft` reads the ledger, not the theme) from a blank sheet
    plus the signature wardrobe.

    So the theme gets the Scripter at open, and a second time when the first
    answer is empty. It runs **before** `talk.dress_from_signature`, which fills
    only empty slots — a theme that names an outfit therefore keeps it. If neither
    ask lands anything, it is said in the conversation (the same 未反映 notice a
    chat turn gets): a theme that did not land is the cue to say it again.
    """
    patch: dict[str, str] = {}
    if ollama is None or not str(theme or "").strip():
        return patch
    char = session.get("character") or {}
    partner_char = session.get("partner_character") or {}
    has_partner = bool(str(partner_char.get("character_id") or "").strip())
    before = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    kw = {
        "model": model,
        "user_line": theme,
        "ledger": before,
        "partner": has_partner,
        "name_a": str(char.get("name_ja") or char.get("name") or ""),
        "name_b": str(partner_char.get("name_ja") or partner_char.get("name") or ""),
        "num_ctx": refine_num_ctx(session),
    }
    t0 = time.monotonic()
    try:
        patch = await writer.write_patch(ollama, **kw)
        # **The theme always gets the second ask (measured, 2026-09-20).** A chat
        # turn only retries on `looks_like_picture_line`, because most lines in a
        # conversation are not picture directions. **A theme always is** — it is
        # the subject of the shoot — and the heuristic reads three of eight real
        # themes as ordinary talk (「雨上がりの帰り道」「図書館で調べもの」
        # 「私たちの撮影スタジオが完成したよ！」). On those the writer answered a
        # bare `{}` the first time and the retry contract got `scene` out of two of
        # the three, so gating the retry would have lost exactly the ones that
        # needed it.
        if not ledger_mod.touched_picture(patch):
            patch = await writer.write_patch(ollama, retry=True, **kw)
            debug_mod.note(session, "open_theme_retry", detail=str(patch), patch=patch)
    except Exception as exc:
        # The opening must not fail over this: she still has to greet him.
        logger.warning("[muse] theme did not reach the ledger: %s", exc)
        debug_mod.note(session, "open_theme_failed", detail=str(exc)[:200])
        return {}
    debug_mod.stage(session, "open_theme_writer", t0)
    patch = ledger_mod.scrub_patch(patch, before)
    if not ledger_mod.touched_picture(patch):
        debug_mod.note(session, "open_theme_missed", detail=theme[:240])
        _append_chat(
            session,
            role="system",
            name="Shot",
            text=(
                "お題が ledger に載らなかった（会話でもう一度言って）"
                if locale.startswith("ja") else
                "The theme did not reach the ledger — say it again in chat"
            ),
            meta={
                "kind": "ledger_missed",
                "chips": [{
                    "key": "missed",
                    "icon": "⚠",
                    "label": "未反映" if locale.startswith("ja") else "Missed",
                }],
            },
        )
        return {}
    after = ledger_mod.apply_patch(before, patch)
    session["refine_ledger"] = after
    debug_mod.note(session, "open_theme_patch", detail=str(patch), patch=patch)
    debug_mod.turn_trace(session, line=theme, patch=patch, before=before, after=after)
    _change_event(
        session, source="theme", patch=patch, before=before, after=after, locale=locale,
    )
    return patch


async def open_session(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """She speaks first — theme + reunion + signature dress. Idempotent-ish."""
    import time

    char = session.get("character") or {}
    if not str(char.get("character_id") or "").strip():
        raise RefineError("character required")
    inputs = _inputs(session)
    locale = str(inputs.get("locale") or "ja")
    model = str(inputs.get("model") or "")
    name = char.get("name_ja") or char.get("name") or "Muse"
    theme = str(inputs.get("theme") or "").strip()
    # **Match the context length from the very first turn.** The clerk
    # (`persona.contract_check_with_db`) only stacks `_runtime_cfg` once the
    # conversation has begun, so running the opening on the default reloads the
    # model once, right there (measured 11-24 seconds).
    await _load_runtime_cfg(db, session)
    # The opening line is the same — solo means the second person's fields are not
    # shown (the same decision as in `chat`).
    partner_char = session.get("partner_character") or {}
    has_partner = bool(str(partner_char.get("character_id") or "").strip())
    name_b = str(partner_char.get("name_ja") or partner_char.get("name") or "")

    # Fresh open clears chat for a real first beat.
    session["chat"] = []
    session["board"] = {}
    session["shoot"] = {}
    session["status"] = "chat"
    session["banned"] = list(session.get("banned") or [])
    session["struck"] = list(session.get("struck") or [])

    await persona.load_memory(db, session)
    persona.mark_reunion(session)

    if theme:
        _append_chat(
            session,
            role="system",
            name="Theme",
            text=theme,
            meta={"kind": "theme"},
        )
    # **The theme is a picture direction, so it goes to the Scripter (2026-09-20).**
    # Before `dress_from_signature`, which fills only what is still empty.
    await _theme_into_ledger(
        ollama, session, theme=theme, model=model, locale=locale,
    )
    dress_patch = talk.dress_from_signature(session)
    if dress_patch:
        debug_mod.note(session, "opening_dress", detail=str(dress_patch), patch=dress_patch)

    # **「またあの感じ」 ("that feeling again") is not offered in Refine
    # (2026-09-10).** The Showrunner: "remove the proposal drawn from last time, to
    # save time". `vitality.again_that_feel_hint` itself is kept because classic
    # Muse uses it.
    talk.prepare_vitality_flags(session, user_line="")
    # Opening: B does not steal the first hello unless W and turn says so.
    session["w_b_leads"] = False

    await assemble.rebuild_craft(db, ollama, session)
    now = str((session.get("craft") or {}).get("now") or "")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}

    # Casual opening — greet / reunion; do not invent a full shot briefing.
    session["scripter_intent"] = "casual"
    open_line = (
        theme
        if theme else
        (
            "（開幕）総監督が来た。挨拶して。画の説明はまだしない。"
            if locale.startswith("ja") else
            "(opening) The Showrunner just arrived. Greet them. Do not brief the shot yet."
        )
    )
    events.publish(session["session_id"], {
        "type": "muse_speaking",
        "muse_id": str(char.get("character_id") or ""),
        "name": name,
    })
    t0 = time.monotonic()
    actress = await writer.actress_turn(
        ollama,
        model=model,
        locale=locale,
        name=name,
        now=now,
        ledger=led,
        identity_blurb=_identity_blurb(session),
        user_line=open_line,
        director_tail=f"Theme: {theme}" if theme else "(opening)",
        session=session,
        character=char,
        partner=has_partner, name_b=name_b,
        num_ctx=refine_num_ctx(session),
    )
    debug_mod.stage(session, "open_actress", t0)
    talk.publish_actress_turn(
        session, actress, locale=locale, lead_name=name,
    )
    if session.get("caught"):
        await persona.consume_caught(db, session)
    if session.get("reunion_turn"):
        persona.clear_reunion(session)
    talk.clear_turn_flags(session)
    session["opened"] = True
    session["status"] = "chat"
    await session_db.save(db, session)
    return session


async def restate_field(
    db, ollama, session: dict[str, Any], field: str,
) -> dict[str, Any]:
    """Absolute one-field readout from director lines (escape hatch)."""
    field = str(field or "").strip()
    if field not in talk.RESTATE_FIELDS:
        raise RefineError(f"unknown field: {field}")
    inputs = _inputs(session)
    model = str(inputs.get("model") or "")
    locale = str(inputs.get("locale") or "ja")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    recent = _director_tail(session, n=12)
    prompt = (
        f"Restate ONLY the ledger field `{field}` as an absolute English phrase "
        f"from the director lines. Output ONLY JSON: {{\"{field}\": \"...\"}}.\n"
        f"Current value: {led.get(field) or '(empty)'}\n\n"
        f"DIRECTOR LINES:\n{recent or '(none)'}\n"
    )
    try:
        # **Thinking is switched off explicitly (2026-09-07).** Unsent, the
        # model's own default applies and this one call takes 14-15 seconds (1.1-1.6
        # with `think=False`; measured, 26B, same prompt, n=2). **The output is
        # thinner as well** (67-91 characters against 141-146). It is called several
        # times a turn, so the wait runs into minutes and never reaches the render.
        # In Muse, `chain._call` sends `think=False` every time.
        raw = await ollama.generate_text(
            prompt, model=model or None, think=False,
            options={"num_ctx": refine_num_ctx(session)},
        )
    except Exception as exc:
        raise RefineError("restate failed") from exc
    from .writer import _extract_json_object
    patch = ledger_mod.normalize_patch(_extract_json_object(raw))
    if field not in patch or not patch.get(field):
        raise RefineError("restate returned empty")
    before = dict(led)
    led = ledger_mod.apply_patch(led, {field: patch[field]})
    session["refine_ledger"] = led
    _change_event(
        session, source="restate", patch={field: patch[field]},
        before=before, after=led, locale=locale,
    )
    await assemble.rebuild_craft(db, ollama, session)
    await session_db.save(db, session)
    return session


def _append_chat(
    session: dict[str, Any],
    *,
    role: str,
    name: str,
    text: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "role": role,
        "name": name,
        "text": text,
        "at": time.time(),
        **({"meta": meta} if meta else {}),
    }
    chat = list(session.get("chat") or [])
    chat.append(row)
    session["chat"] = chat[-80:]
    return row


def _patch_last_user_meta(session: dict[str, Any], **fields: Any) -> None:
    chat = list(session.get("chat") or [])
    for i in range(len(chat) - 1, -1, -1):
        if chat[i].get("role") == "user":
            meta = dict(chat[i].get("meta") or {})
            meta.update({k: v for k, v in fields.items() if v is not None})
            chat[i] = {**chat[i], "meta": meta}
            session["chat"] = chat
            return


def _director_tail(session: dict[str, Any], n: int = 16) -> str:
    """Director lines only — never Muse SAY (avoids outfit smuggling)."""
    rows = [
        r for r in (session.get("chat") or [])
        if r.get("role") == "user" and not (r.get("meta") or {}).get("struck")
    ][-n:]
    return "\n".join(f"Director: {r.get('text') or ''}" for r in rows)


def _identity_blurb(session: dict[str, Any]) -> str:
    char = session.get("character") or {}
    tags = [str(t) for t in (char.get("identity_tags") or []) if str(t).strip()]
    name = char.get("name_ja") or char.get("name") or ""
    # Keep short — locked look, not wardrobe.
    head = ", ".join(tags[:12])
    if name and head:
        return f"{name}: {head}"
    return head or name or ""


def _change_event(
    session: dict[str, Any],
    *,
    source: str,
    patch: dict[str, str],
    before: dict[str, str],
    after: dict[str, str],
    locale: str,
) -> None:
    """Keep a move of the ledger **in the record**. Nothing is put in the
    conversation. (2026-09-10)

    The Showrunner: "there is a lot of information in the conversation part —
    picture updates and the like do not need to be shown, I can see them in the
    log".

    This used to push a `kind: "ledger_change"` row into the conversation.
    `record_rewrite` emits the rewrite record and the `ledger_rewrite` SSE, so
    `/debug` and `/pipeline` still carry everything. On the conversation side the
    🖼 icon on her line (`_mark_turn_shot`) is enough.

    **Only `ledger_change` was removed.** `ledger_missed` — a turn that looked like
    a picture direction where the ledger did not move — is the cue to say it again,
    so it still appears in the conversation.

    `patch` and `locale` keep the caller's shape as it was, so putting this back
    into the conversation would not mean touching the callers.
    """
    debug_mod.record_rewrite(
        session, source, before=before, after=after, intent=source,
    )


def _publish_floor(
    session: dict[str, Any], floor: list[dict[str, Any]], *, locale: str,
) -> None:
    """Push the crew's lines into the conversation. (2026-09-11)

    A seat's line is `kind: "seat"`, a heckle is `kind: "heckle"`. Both **look
    different from her lines on screen** — eighteen people speak, so the lead's
    voice must not be buried. A seat that owns a field carries that field's name
    as a flag.
    """
    for row in floor:
        meta: dict[str, Any] = {
            "kind": str(row.get("kind") or "seat"),
            "muse_id": str(row.get("muse_id") or ""),
            "role": str(row.get("role") or ""),
        }
        field = str(row.get("field") or "")
        if field and str(row.get("craft") or "").strip():
            meta["fields"] = [field]
            meta["chips"] = ledger_mod.chips_for([field], locale=locale)
        _append_chat(
            session, role="assistant", name=str(row.get("name") or ""),
            text=str(row.get("say") or ""), meta=meta,
        )
        events.publish(session["session_id"], {
            "type": "chat", "role": "assistant",
            "name": row.get("name"), "text": row.get("say"),
            "kind": meta["kind"],
        })


def _mark_turn_shot(
    session: dict[str, Any], *, mark: int, before: dict[str, str],
) -> None:
    """Stamp whether this turn moved the picture onto her line. (2026-09-10)

    The Showrunner: "make it clear from an icon whether a turn was conversation
    only or generated an image prompt".

    `mark` is the `len(chat)` noted at the head of the turn. Only `say` rows after
    that point are stamped — the mutter (banter) and the pitch are continuations of
    the same speech, so one stamp goes on the line itself.

    **Older rows are never touched.** A row that was never stamped has no
    `meta.shot`, so the screen shows nothing (read with `=== true` / `=== false`).
    """
    after = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    shot = bool(ledger_mod.changed_fields(before, after))
    chat = list(session.get("chat") or [])
    touched = False
    for i in range(max(0, mark), len(chat)):
        row = chat[i]
        if row.get("role") != "assistant":
            continue
        if (row.get("meta") or {}).get("kind") != "say":
            continue
        chat[i] = {**row, "meta": {**(row.get("meta") or {}), "shot": shot}}
        touched = True
    if touched:
        session["chat"] = chat


#: The habit note reads the last 8 (`muse.service._director_highlights`).
#: **Nothing piles up.**
_NOTES_MAX = 24


def _keep_note(session: dict[str, Any], text: str) -> None:
    """Note the director's line when it moved the picture. (2026-09-10)

    The Showrunner: "let's have it write habit notes".

    The studio notebook's habit note is emitted by classic's `finish_session`, but
    two gates both read `session["notes"]` — `lounge.should_write_habit` and, on
    the writing side, `muse.service._director_highlights`. **Refine put standing
    orders into `standing` and never filled `notes` at all**, so a habit note was
    never written once.

    Only **a line that moved the picture** is kept. A conversation-only turn such
    as "you look lovely" is not material for a habit, and what the contract
    (`crew.showrunner_habit_prompt`) asks for is the Showrunner's direction notes.
    The same line twice in a row is not stacked again (same as classic's
    `_add_note`).
    """
    line = " ".join(str(text or "").split()).strip()
    if not line:
        return
    notes = list(session.get("notes") or [])
    if notes and notes[-1] == line:
        return
    notes.append(line)
    session["notes"] = notes[-_NOTES_MAX:]


def _note_blind(session: dict[str, Any], *, locale: str) -> None:
    """Say out loud that the picture never reached the model. (2026-09-10)

    A model that cannot read images **returns empty rather than refusing**.
    Falling back to no-picture in silence looks, from the Showrunner's side, like
    nothing worse than "she is quiet today". Said once.

    Same wording and same manners as classic's `muse.service._note_blind`.
    """
    if session.get("_blind_said"):
        return
    session["_blind_said"] = True
    _append_chat(
        session,
        role="system",
        name="Studio",
        text=(
            "このモデルは絵を読めないようなので、試し撮りは渡さずテキストだけで進めます。"
            "vision_model に画像を読めるモデルを指定すると、彼女が実際の絵を見て話せます。"
            if locale.startswith("ja") else
            "This model could not read the test shot — continuing on text alone. "
            "Set vision_model to an image-capable model so she can see it."
        ),
    )
    debug_mod.note(session, "actress_blind", detail="絵が読めないので絵抜きで撮り直した")


def _voice_fallback(session: dict[str, Any], *, kind: str, locale: str) -> str:
    """Last-resort lines still use her first person / address when possible."""
    char = session.get("character") or {}
    p = char.get("personality") or {}
    ja = locale.startswith("ja")
    first = str(char.get("first_person_ja") or p.get("first_person_ja") or ("私" if ja else "I"))
    addr = str(char.get("user_address_ja") or p.get("user_address_ja") or ("総監督" if ja else "Showrunner"))
    if not ja:
        if kind == "ok":
            return f"Yeah, {addr} — this shot matches."
        if kind == "repair":
            return f"That's off. I'll fix it myself, {addr}."
        return f"Fixed. This is the shot now, {addr}."
    if kind == "ok":
        return f"{first}、この画で合ってると思う…{addr}。"
    if kind == "repair":
        return f"んっ、ずれてる。{first}が直すね、{addr}。"
    return f"直したよ、{addr}。いまの画はこれ。"


async def chat(
    db,
    ollama,
    session: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    import time

    text = (message or "").strip()
    if not text:
        raise RefineError("empty message")
    inputs = _inputs(session)
    model = str(inputs.get("model") or "")
    locale = str(inputs.get("locale") or "ja")
    char = session.get("character") or {}
    name = char.get("name_ja") or char.get("name") or "Muse"
    # **One person or two (2026-09-09).** `blank()` always fills the ledger's
    # second-person fields, so the model saw an empty `wearing_b` / `beat_b`. An
    # empty field looks like "fill me in" — the Showrunner: "when there is only one
    # person it edits muse_b's tags". It is decided once here and handed to the
    # three seats that touch the model.
    partner_char = session.get("partner_character") or {}
    has_partner = bool(str(partner_char.get("character_id") or "").strip())
    name_b = str(partner_char.get("name_ja") or partner_char.get("name") or "")
    before = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}

    # Explicit standing order line — store and acknowledge without picture write.
    standing_rule = persona.note_standing(session, text)
    # Anima in-image lettering — pull before the writer so VLM isn't asked to invent glyphs.
    from . import anima as anima_mod
    letter_phrases, text_for_writer = anima_mod.extract_lettering(text)
    if letter_phrases:
        led0 = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
        before_letter = dict(led0)
        led0["lettering"] = letter_phrases[0]
        session["refine_ledger"] = led0
        _change_event(
            session,
            source="lettering",
            patch={"lettering": letter_phrases[0]},
            before=before_letter,
            after=led0,
            locale=locale,
        )
        if text_for_writer.strip():
            text = text_for_writer
        before = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    if standing_rule:
        _append_chat(session, role="user", name="Director", text=text)
        ack = (
            f"常設に入れたよ：「{standing_rule}」"
            if locale.startswith("ja") else
            f"Standing order noted: “{standing_rule}”"
        )
        _append_chat(
            session, role="assistant", name=name, text=ack,
            meta={"kind": "standing"},
        )
        await session_db.save(db, session)
        return session

    session["commit_pitch"] = persona.is_commit_pitch(text)
    talk.prepare_vitality_flags(session, user_line=text)

    # **Did this turn only talk, or did it move the picture? (2026-09-10)** The
    # Showrunner: "make it clear from an icon whether it was conversation only or
    # image-prompt generation". Her line is stacked before propose and the self
    # repair, so it cannot be decided there and then. Only the position of the mark
    # is noted here; `_mark_turn_shot` stamps it at the end of the turn.
    turn_mark = len(session.get("chat") or [])

    row = _append_chat(session, role="user", name="Director", text=text)
    debug_mod.note(session, "director_line", detail=text[:240])

    # Contract clerk — block crime/violence (and nsfw when configured) before her.
    blocked = await persona.contract_check_with_db(db, ollama, session, text)
    if blocked:
        ja = locale.startswith("ja")
        soft = (
            "……それは、この撮影ではやれないみたい。"
            if ja else
            "…I don't think we can take that on this set."
        )
        talk.strike_last_user(session, why=str(blocked))
        _append_chat(
            session, role="assistant", name=name, text=soft,
            meta={"kind": "contract", "blocked": blocked},
        )
        _append_chat(
            session,
            role="system",
            name="Studio",
            text=(
                "この発言は以降の会話に含めません"
                if ja else
                "This line is struck from further conversation"
            ),
            meta={"kind": "struck", "blocked": blocked},
        )
        debug_mod.note(session, "contract_block", detail=str(blocked))
        session["status"] = "chat"
        await session_db.save(db, session)
        return session

    # **His line goes up the moment it clears the clerk (2026-09-20).** The
    # Showrunner: "if the director's instruction is not reflected in the chat right
    # away it is a little hard to follow".
    #
    # The line was published **before** the clerk and the panel answered it with
    # `scheduleRefresh`, which refuses to run while the POST is in flight (a GET
    # before the turn's final save would overwrite the answer) — so nothing
    # appeared until the whole turn came back, seats and all. The row itself is
    # sent now, and the panel appends it without a fetch. **After** the clerk, so a
    # refused line never appears and then vanishes: what it gets instead is the
    # refusal, which is the road the blocked branch above already takes.
    await session_db.save(db, session)
    events.publish(session["session_id"], {
        "type": "chat",
        "role": "user",
        "name": "Director",
        "text": text,
        "at": row.get("at"),
    })

    led = dict(before)
    director_recent = _director_tail(session)

    # **With a crew, go once round before the writer (2026-09-11).** The
    # Showrunner: "I want to bring the studio shoot into Muse refine". The seats
    # never write the ledger directly — classic's "only the Scripter writes" is
    # carried over as it was, and in Refine that Scripter is `write_patch` below.
    # The gate is the seats' existence, not `mode`.
    crew_craft = ""
    # **The floor exists, empty, on turns with no crew (2026-09-18).** This was
    # created only inside the `if`, so a solo or duet turn hit `UnboundLocalError`
    # at `if floor:` below and **the chat fell over with a 500** (since `0019b5b` on
    # 2026-09-14). There were only crew tests, so it went unnoticed for four days.
    floor: list[dict[str, Any]] = []
    if crew_room.has_crew(session):
        t0 = time.monotonic()
        floor = await crew_room.run_table(db, ollama, session, director_line=text)
        debug_mod.stage(session, "crew_table", t0)
        _publish_floor(session, floor, locale=locale)
        crew_craft = crew_room.craft_block(floor)
        debug_mod.note(
            session, "crew_table",
            detail=f"{len(floor)}発言（うち craft {sum(1 for f in floor if f.get('craft'))}）",
        )

    t0 = time.monotonic()
    patch = await writer.write_patch(
        ollama,
        model=model,
        user_line=text,
        ledger=led,
        recent=director_recent,
        partner=has_partner, name_a=name, name_b=name_b,
        num_ctx=refine_num_ctx(session),
        crew_craft=crew_craft,
    )
    debug_mod.stage(session, "writer", t0)

    retried = False
    if not ledger_mod.touched_picture(patch) and ledger_mod.looks_like_picture_line(text):
        retried = True
        t0 = time.monotonic()
        patch = await writer.write_patch(
            ollama,
            model=model,
            user_line=text,
            ledger=led,
            recent=director_recent,
            retry=True,
            partner=has_partner, name_a=name, name_b=name_b,
            num_ctx=refine_num_ctx(session),
        )
        debug_mod.stage(session, "writer_retry", t0)
        debug_mod.note(session, "writer_retry", detail=str(patch), patch=patch)

    debug_mod.note(session, "writer_patch", detail=str(patch), patch=patch, retried=retried)

    # Conversation cues for mood / art direction (no UI buttons).
    cue = talk.cue_atmosphere_look(text)
    allow_clear = talk.cue_allow_clear(cue)
    if cue:
        patch = talk.merge_cue_into_patch(patch, cue)
        debug_mod.note(session, "atm_look_cue", detail=str(cue), patch=patch)

    # Long-chat durability: drop accidental empty clears; track director sticky touches.
    # **What was folded into one body is recorded (2026-09-12).** Eighteen seats
    # write the same field in turn, so paraphrases and contradictions pile up. Only
    # "the second on the same axis" is dropped, and what was dropped can be read in
    # the screen's debug pane.
    folded: dict[str, list[str]] = {}
    patch = ledger_mod.scrub_patch(patch, led, allow_clear=allow_clear, report=folded)
    if folded:
        debug_mod.note(
            session, "one_body",
            detail="; ".join(f"{k}: {', '.join(v)}" for k, v in folded.items()),
            dropped=folded,
        )
    director_sticky = {
        k for k in ledger_mod.STICKY_KEYS
        if k in patch
    }

    missed = False
    if ledger_mod.touched_picture(patch):
        _keep_note(session, text)
        # wearing_drop → soft ban so assemble won't resurrect it.
        drop = str(patch.get("wearing_drop") or "").strip()
        if drop:
            talk.ban_tag(session, drop)
        led = ledger_mod.apply_patch(led, patch)
        session["refine_ledger"] = led
        talk.note_picture_compile(session)
        _change_event(
            session,
            source="director",
            patch=patch,
            before=before,
            after=led,
            locale=locale,
        )
        _patch_last_user_meta(
            session,
            fields=ledger_mod.changed_fields(before, led) or ledger_mod.patch_fields(patch),
            chips=ledger_mod.chips_for(
                ledger_mod.changed_fields(before, led) or ledger_mod.patch_fields(patch),
                locale=locale,
            ),
            patch=patch,
        )
    elif ledger_mod.looks_like_picture_line(text):
        missed = True
        _patch_last_user_meta(
            session,
            missed_picture=True,
            chips=[{
                "key": "missed",
                "icon": "⚠",
                "label": "未反映" if locale.startswith("ja") else "Missed",
            }],
        )
        _append_chat(
            session,
            role="system",
            name="Shot",
            text=(
                "画の指示っぽいが ledger に載らなかった（要やり直し）"
                if locale.startswith("ja") else
                "Looked like a picture note but ledger did not move"
            ),
            meta={
                "kind": "ledger_missed",
                "chips": [{
                    "key": "missed",
                    "icon": "⚠",
                    "label": "未反映" if locale.startswith("ja") else "Missed",
                }],
            },
        )
        debug_mod.note(session, "writer_missed", detail=text[:240])

    # **Land each field corner's conclusion in the fields the director did not
    # touch (2026-09-14).**
    #
    # The Showrunner: "the art and colour seats make good proposals and it really
    # is a waste that they never reach the prompt". The seats do not touch the
    # ledger, they only hand over material, and the writer fixes only the fields the
    # director's line names — so **the craft for any field left unnamed landed
    # nowhere** (measured, `look` stayed empty in 88% of sessions and `atmosphere`
    # in 76%).
    #
    # **A field the director wrote passes straight through.** The crew has its say
    # only in the fields the director was silent about. No model is called, so the
    # turn takes no longer.
    #
    # **The crew's words in a field the director wrote are forgotten (2026-09-14).**
    # Once the writer has rewritten that field, its contents are all the director's
    # words. Keeping the note would let the next corner erase the director's words
    # as if they were its own.
    words = dict(crew_room.crew_words_of(session))
    for key in patch:
        words.pop(key, None)
    if floor:
        fill, landed = crew_room.field_land(
            session, floor, ledger=led,
            taken=set(patch.keys()) | director_sticky,
        )
        fill = ledger_mod.scrub_patch(fill, led, allow_clear=set())
        if fill:
            before_fill = dict(led)
            led = ledger_mod.apply_patch(led, fill)
            session["refine_ledger"] = led
            words.update({k: v for k, v in landed.items() if k in fill})
            _change_event(
                session, source="crew", patch=fill,
                before=before_fill, after=led, locale=locale,
            )
            debug_mod.note(
                session, "field_land",
                detail="; ".join(f"{k}: {', '.join(v)}" for k, v in landed.items()),
                patch=fill,
            )
    if words or session.get(crew_room.CREW_WORDS):
        # No mark on a session without a crew (solo and duet pass straight
        # through).
        session[crew_room.CREW_WORDS] = words

    # **The picture is not rebuilt mid-conversation (2026-09-10).** The Showrunner:
    # "when we are not going into a shot, a conversation-only reply should be much
    # faster". The prose and tags built here are used only by the test shot and the
    # final, and both call `rebuild_craft` themselves. All the actress needs is the
    # one `now` line, so it is updated without a model.
    assemble.touch_craft(session)
    now = str((session.get("craft") or {}).get("now") or "")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    # How far the director's line moved the ledger is settled here. What moves
    # after this is her own propose and the self repair, so whether a re-check is
    # needed is measured at this point.
    after_director = dict(led)

    lead_cid = str(char.get("character_id") or "")
    try:
        from . import shared as muse_service
        on_token = muse_service._token_publisher(session["session_id"], lead_cid)
    except Exception:
        logger.debug("[muse] token publisher unavailable", exc_info=True)
        on_token = None

    # **After a test shot, she is shown the picture (2026-09-10).** The Showrunner:
    # "after a test shot, let us have Muse look at the image".
    #
    # The ledger is "shoot it like this"; the picture is "this is how it came out".
    # With only the ledger in view she cannot talk about the picture itself — from
    # the test shot onward, classic hands over the board every turn (`board_images`
    # -> `run_duet_talk`). The same thing is used here (that side handles the
    # resizing and loading, and returns empty when there is no board, when one is
    # still rendering, or when it cannot be read).
    board_shots: list[bytes] = []
    try:
        from . import shared as muse_service
        board_shots = await muse_service.board_images(db, session)
    except Exception:
        logger.debug("[muse] board image unavailable", exc_info=True)
    # Only on a turn that hands over a picture, switch to a model that can read one
    # (when unset, the same as `model`).
    say_model = (str(inputs.get("vision_model") or "") or model) if board_shots else model
    events.publish(session["session_id"], {
        "type": "muse_speaking",
        "muse_id": lead_cid,
        "name": name,
    })
    t0 = time.monotonic()
    actress = await writer.actress_turn(
        ollama,
        model=say_model,
        locale=locale,
        name=name,
        now=now,
        ledger=led,
        identity_blurb=_identity_blurb(session),
        user_line=text,
        director_tail=director_recent,
        session=session,
        character=char,
        partner=has_partner, name_b=name_b,
        num_ctx=refine_num_ctx(session),
        # `_say_only` passes only what is inside `SAY:` — neither field names nor
        # `MY_FEEL:` reach the screen. Every Refine field name is in classic's
        # `_SAY_SHUT_RE` (SAY / ASIDE / CARD / PITCH / MY_FEEL).
        on_token=on_token,
        images=board_shots or None,
    )
    debug_mod.stage(session, "actress", t0)
    if board_shots:
        if actress.get("blind"):
            _note_blind(session, locale=locale)
        else:
            debug_mod.note(
                session, "actress_saw_board",
                detail=f"試し撮り {len(board_shots)}枚を見せた（{say_model}）",
            )
    say = actress.get("say") or ""
    aside = actress.get("aside") or ""
    propose = actress.get("propose") or {}
    my_feel = actress.get("my_feel") or ""
    pitch = actress.get("pitch") or ""
    debug_mod.note(
        session, "actress",
        detail=(say or "")[:240],
        aside=(aside or "")[:240],
        propose=propose or {},
        my_feel=my_feel,
        pitch=pitch,
    )
    talk.publish_actress_turn(
        session, actress, locale=locale, lead_name=name,
    )

    # Muse propose: clothes/place fill-empty only; expression may refresh when
    # scene-ish axes moved and the director did not name a face this turn.
    raw_propose = dict(propose or {})
    propose = ledger_mod.scrub_patch(propose, led, allow_clear=set())
    propose = ledger_mod.guard_sticky_writes(propose, allowed=set())
    director_keys = set(patch.keys()) | director_sticky
    propose = ledger_mod.guard_muse_propose(
        propose, led, director_keys=director_keys,
    )
    dropped_face = ""
    if str(raw_propose.get("expression") or "").strip():
        kept = str(propose.get("expression") or "").strip()
        wanted = str(raw_propose.get("expression") or "").strip()
        if wanted and not kept and wanted.lower() != str(led.get("expression") or "").strip().lower():
            dropped_face = wanted
    if "expression" in propose or dropped_face:
        debug_mod.note(
            session, "actress_expression",
            detail=(
                f"accepted={propose.get('expression') or '∅'}"
                + (f"; dropped={dropped_face}" if dropped_face else "")
            ),
            accepted=str(propose.get("expression") or ""),
            dropped=dropped_face,
            director_named_face=("expression" in director_keys),
            scene_moved=bool(
                director_keys & {"scene", "atmosphere", "beat", "light", "frame", "bg"}
            ),
        )

    # **When she chose the clothes, record it (2026-09-09).** The Showrunner: "she
    # may fill it, but keep a record", "I want what she herself wanted to do to be
    # reflected properly". `wearing` gets through only when the field is empty
    # (fill-empty, `guard_muse_propose`), so getting through means **she decided in
    # a scene where nobody had dressed her**. Without showing it in the debug pane,
    # all the Showrunner sees is "she changed clothes with no instruction".
    if str(propose.get("wearing") or "").strip():
        chose = str(propose["wearing"]).strip()
        lifted = [
            b for b in (session.get("banned") or [])
            if talk.word_hit(str(b), chose)
        ]
        debug_mod.note(
            session, "actress_wardrobe",
            detail=chose[:240] + (f"（禁止を解いた: {', '.join(lifted)}）" if lifted else ""),
            accepted=chose,
            lifted_ban=lifted,
            was_empty=True,
        )

    if ledger_mod.touched_picture(propose):
        before_p = dict(led)
        led = ledger_mod.apply_patch(led, propose)
        session["refine_ledger"] = led
        _change_event(
            session,
            source="muse",
            patch=propose,
            before=before_p,
            after=led,
            locale=locale,
        )
        chat = list(session.get("chat") or [])
        for i in range(len(chat) - 1, -1, -1):
            if chat[i].get("role") != "assistant":
                continue
            if (chat[i].get("meta") or {}).get("kind") == "banter":
                continue
            meta = dict(chat[i].get("meta") or {})
            fields = ledger_mod.changed_fields(before_p, led) or ledger_mod.patch_fields(propose)
            meta.update({
                "fields": fields,
                "chips": ledger_mod.chips_for(fields, locale=locale),
                "propose": propose,
                "source": "muse",
            })
            chat[i] = {**chat[i], "meta": meta}
            session["chat"] = chat
            break
        assemble.touch_craft(session)

    # Diary-read catch fires once, then consume so it never loops.
    if session.get("caught"):
        await persona.consume_caught(db, session)
    if session.get("reunion_turn"):
        persona.clear_reunion(session)
    talk.clear_turn_flags(session)

    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    now = str((session.get("craft") or {}).get("now") or "")

    # **On a turn where the picture did not move, there is nothing to ask the
    # re-check (2026-09-10).**
    #
    # Verify's job is "does the ledger match the director's intent". On a turn where
    # not one field moved and the director's line does not look like picture talk
    # (「今日はありがとう」 — "thank you for today"), there is nothing to compare
    # against. It still ran every time, reading 2,797 characters and writing a line
    # in her voice — about 4 seconds on the input alone.
    #
    # **No hole is opened for misses.** `missed` (a turn that looks like a picture
    # instruction where the writer wrote nothing) is exactly the turn verify should
    # catch, so it runs.
    #
    # **What is measured is only what the director moved (2026-09-10).** Comparing
    # `before` against the ledger now counted a turn where she added one expression
    # word as "moved", and a 5.9-6.8 second re-check ran every time. Verify's job is
    # whether the ledger matches **the director's** intent, so a turn where the
    # director said nothing about the picture has nothing to compare against. Her
    # propose is held to filling empty fields by `guard_muse_propose`.
    moved_by_director = ledger_mod.changed_fields(before, after_director)
    if not moved_by_director and not missed:
        debug_mod.note(
            session, "verify_skipped",
            detail="監督は台帳を動かしておらず、絵の指示にも見えないので再判定は走らせない",
        )
        _mark_turn_shot(session, mark=turn_mark, before=before)
        session["status"] = "chat"
        await session_db.save(db, session)
        return session

    t0 = time.monotonic()
    ok, comment, repair = await writer.verify_and_repair(
        ollama,
        model=model,
        locale=locale,
        name=name,
        user_line=text,
        ledger=led,
        now=now,
        # The ledger before the turn, and the recent flow. The contract's "keep the
        # previous turn" has nothing to compare against without it (`before` has
        # been to hand since the top of this function).
        before=before,
        recent=director_recent,
        partner=has_partner, name_b=name_b,
        num_ctx=refine_num_ctx(session),
        force_repair_hint=missed,
        character=char,
        session=session,
    )
    debug_mod.stage(session, "verify", t0)
    debug_mod.note(
        session, "verify",
        detail=(comment or "")[:240],
        ok=ok,
        repair=repair or {},
    )

    if ok:
        confirm = comment or _voice_fallback(session, kind="ok", locale=locale)
        _append_chat(
            session,
            role="assistant",
            name=name,
            text=confirm,
            meta={
                "kind": "verify_ok",
                "chips": [{
                    "key": "ok",
                    "icon": "✅",
                    "label": "確認" if locale.startswith("ja") else "OK",
                }],
            },
        )
        events.publish(session["session_id"], {
            "type": "chat", "role": "assistant", "name": name, "text": confirm,
        })
    else:
        fix_line = comment or _voice_fallback(session, kind="repair", locale=locale)
        _append_chat(
            session,
            role="assistant",
            name=name,
            text=fix_line,
            meta={
                "kind": "verify_repair",
                "chips": [{
                    "key": "repair",
                    "icon": "🔧",
                    "label": "自己修復" if locale.startswith("ja") else "Self-fix",
                }],
            },
        )
        events.publish(session["session_id"], {
            "type": "chat", "role": "assistant", "name": name, "text": fix_line,
        })

        if not ledger_mod.touched_picture(repair) and (
            missed or ledger_mod.looks_like_picture_line(text)
        ):
            t0 = time.monotonic()
            repair = await writer.write_patch(
                ollama,
                model=model,
                user_line=text,
                ledger=led,
                recent=director_recent,
                retry=True,
            )
            debug_mod.stage(session, "verify_repair_writer", t0)
            debug_mod.note(session, "verify_repair_fallback", detail=str(repair), patch=repair)

        repair = ledger_mod.scrub_patch(repair, led, allow_clear=allow_clear)
        # Repair may fix sticky only if the director touched that axis this turn.
        repair = ledger_mod.guard_sticky_writes(
            repair, allowed=director_sticky | allow_clear,
        )

        if ledger_mod.touched_picture(repair):
            before_r = dict(led)
            led = ledger_mod.apply_patch(led, repair)
            session["refine_ledger"] = led
            _change_event(
                session,
                source="self_repair",
                patch=repair,
                before=before_r,
                after=led,
                locale=locale,
            )
            assemble.touch_craft(session)
            done = _voice_fallback(session, kind="fixed", locale=locale)
            _append_chat(
                session,
                role="assistant",
                name=name,
                text=done,
                meta={
                    "kind": "verify_repaired",
                    "chips": [{
                        "key": "ok",
                        "icon": "✅",
                        "label": "修復済" if locale.startswith("ja") else "Fixed",
                    }],
                    "fields": ledger_mod.changed_fields(before_r, led),
                },
            )
            events.publish(session["session_id"], {
                "type": "chat", "role": "assistant", "name": name, "text": done,
            })
        else:
            _append_chat(
                session,
                role="system",
                name="Shot",
                text=(
                    "自己修復できなかった — 指示をもう一度お願いします"
                    if locale.startswith("ja") else
                    "Self-repair could not land — please restate the direction"
                ),
                meta={
                    "kind": "ledger_missed",
                    "chips": [{
                        "key": "missed",
                        "icon": "⚠",
                        "label": "未反映" if locale.startswith("ja") else "Missed",
                    }],
                },
            )

    craft = session.get("craft") or {}
    after = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    debug_mod.turn_trace(
        session,
        line=text,
        patch=patch,
        propose=propose,
        before=before,
        after=after,
        quality=[t for t in str(craft.get("quality_tags") or "").split(",") if t.strip()],
    )
    if missed:
        debug_mod.note(session, "turn_missed_picture", detail="heuristic picture line, empty patch")
    debug_mod.note(session, "verify_result", detail="ok" if ok else "repaired", ok=ok)

    _mark_turn_shot(session, mark=turn_mark, before=before)
    session["status"] = "chat"
    await session_db.save(db, session)
    return session


async def rebuild(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    await assemble.rebuild_craft(db, ollama, session)
    await session_db.save(db, session)
    return session


def _board_seed(board: dict[str, Any]) -> int:
    """The seed the approved draft actually used.

    The document of record is `board["seed"]`, written back by
    `session_db.attach_board_image` when the render finishes. **For older rows
    where that is empty, it is also picked up from the photos** — the seed has
    always been in each frame's meta, there was simply no road up into the field.

    One draft press means one seed (it is a ComfyUI batch, so several frames share
    the seed and differ by index). The final shoot takes the same number of
    frames, so the indices line up.
    """
    seed = int(board.get("seed") or 0)
    if seed:
        return seed
    for shot in reversed(list(board.get("images") or [])):
        got = int(shot.get("seed") or 0)
        if got:
            return got
    return 0


async def start_shoot(db, request, session: dict[str, Any]) -> dict[str, Any]:
    """Approve board → final shoot. Requires a finished board (OK gate)."""
    from . import runner
    from ..spooler.models import JobLane

    board = session.get("board") or {}
    if board.get("pending"):
        raise RefineError(
            "試し撮りの生成が終わるまで待ってください"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Wait for the test shot to finish rendering"
        )
    if (session.get("shoot") or {}).get("pending"):
        raise RefineError(
            "本番の生成が終わるまで待ってください"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Wait for the final shoot to finish rendering"
        )
    if not board.get("images"):
        raise RefineError(
            "先に試し撮りしてから本番へ"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Run a test shot first, then approve for final shoot"
        )

    # Prefer the approved board prompt when ledger has not moved since board.
    board_prompt = str(board.get("prompt") or "").strip()
    board_fp = str(board.get("ledger_fp") or "")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    cur_fp = "|".join(str(led.get(k) or "") for k in ledger_mod.LEDGER_KEYS)
    if board_prompt and board_fp and board_fp == cur_fp:
        prompt = board_prompt
    else:
        await assemble.rebuild_craft(db, request.app.state.ollama, session)
        prompt = str((session.get("craft") or {}).get("prompt") or "").strip()
    if not prompt:
        raise RefineError("prompt is empty")
    if not _inputs(session).get("workflow"):
        raise RefineError("workflow required")

    locale = str(_inputs(session).get("locale") or "ja")
    _append_chat(
        session,
        role="system",
        name="Studio",
        text=(
            "承認を受け付けました。本番撮影に入ります。"
            if locale.startswith("ja") else
            "Approved. Going to final shoot."
        ),
        meta={"kind": "approve"},
    )

    session["shoot"] = {
        "prompt": prompt,
        "status": "queued",
        "error": "",
        "images": [],
        "pending": True,
        # **Shoot on the same seed as the test shot (the Showrunner, 2026-09-12).**
        #
        #   "the design is that when a test shot gives you a good scene, you get the
        #    high-quality image from the same prompt without changing that seed"
        #
        # The canvas is the same for the test shot and the final; only steps and cfg
        # change (12/4.0 -> 30/4.5). So matching the seed gives **the finished
        # version of the very picture that was approved**. Passing 0 makes `runner`
        # fold it to `None` and draw a fresh one, so a miss here silently produces a
        # different picture (all six live cases disagreed).
        "seed": _board_seed(board),
        "job_id": "",
    }
    session["status"] = "shooting"
    await session_db.save(db, session)

    # **The LLM comes out of VRAM immediately before the render (as in Muse).**
    #
    # One step above, `rebuild_craft` uses the model, so without giving it back here
    # the 26B keeps ~13 GB while ComfyUI goes to place the latent — on a 16 GB card
    # there is nowhere to put it and it falls over (measured by the Showrunner).
    # Muse walks this line on both board and shoot; only Refine did not.
    #
    # The default is `unload_vlm: True` (it comes from `ALL_DEFAULTS`). Both the
    # meaning of the switch and the decision are Muse's own — no second
    # implementation.
    from . import shared as muse_service
    await muse_service._maybe_unload(request.app.state.ollama, session)

    spooler = request.app.state.spooler
    session["shoot"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_shoot",
        runner.run_shoot_job,
        db=db,
        comfy=request.app.state.comfy,
        session_id=session["session_id"],
        ollama=request.app.state.ollama,
    )
    await session_db.save(db, session)
    return session


async def start_board(db, request, session: dict[str, Any]) -> dict[str, Any]:
    """Enqueue a board render using muse runner (prompt from refine craft)."""
    from . import runner
    from ..spooler.models import JobLane

    board = session.get("board") or {}
    if board.get("pending"):
        raise RefineError(
            "試し撮りの生成が終わるまで待ってください"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Wait for the test shot to finish rendering"
        )
    shoot = session.get("shoot") or {}
    if shoot.get("pending"):
        raise RefineError(
            "本番の生成が終わるまで待ってください"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Wait for the final shoot to finish rendering"
        )

    await _load_runtime_cfg(db, session)
    await assemble.rebuild_craft(db, request.app.state.ollama, session)
    prompt = str((session.get("craft") or {}).get("prompt") or "").strip()
    if not prompt:
        raise RefineError("prompt is empty")
    if not _inputs(session).get("workflow"):
        raise RefineError("workflow required")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    session["board"] = {
        "prompt": prompt,
        "status": "queued",
        "error": "",
        "images": [],
        "pending": True,
        # **Drawn afresh every time. 0 means "draw a new one" (the Showrunner,
        # 2026-09-12).**
        #
        #   "the seed changing when you press the test shot is the same as taking
        #    many photographs on a shoot and picking out the good scene"
        #
        # It is **not** given one seed per session (classic's `session_seed`). The
        # number of frames to choose from comes from exactly this.
        "seed": 0,
        "job_id": "",
        "ledger_fp": "|".join(str(led.get(k) or "") for k in ledger_mod.LEDGER_KEYS),
        "round": int((session.get("board") or {}).get("round") or 0) + 1,
    }
    session["status"] = "boarding"
    await session_db.save(db, session)

    # **The LLM comes out of VRAM immediately before the render (as in Muse).**
    #
    # One step above, `rebuild_craft` uses the model, so without giving it back here
    # the 26B keeps ~13 GB while ComfyUI goes to place the latent — on a 16 GB card
    # there is nowhere to put it and it falls over (measured by the Showrunner).
    # Muse walks this line on both board and shoot; only Refine did not.
    #
    # The default is `unload_vlm: True` (it comes from `ALL_DEFAULTS`). Both the
    # meaning of the switch and the decision are Muse's own — no second
    # implementation.
    from . import shared as muse_service
    await muse_service._maybe_unload(request.app.state.ollama, session)

    spooler = request.app.state.spooler
    session["board"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_board",
        runner.run_board_job,
        db=db,
        comfy=request.app.state.comfy,
        session_id=session["session_id"],
        ollama=request.app.state.ollama,
    )
    await session_db.save(db, session)
    return session


async def finish_session(db, request, session: dict[str, Any]) -> dict[str, Any]:
    """Wrap after final shoot — diary hook via Muse finish_session when possible."""
    shoot = session.get("shoot") or {}
    if not shoot.get("images"):
        raise RefineError(
            "本番撮影が終わってから終了してください。"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Finish the final shoot before wrapping up."
        )
    locale = str(_inputs(session).get("locale") or "ja")
    bond = session.get("bond") or {}
    last = str(bond.get("last") or "").strip()
    coda = (
        (
            "今日の余韻、メモに残しておくね"
            + (f"（{last[:80]}）" if last else "。")
        )
        if locale.startswith("ja") else
        (
            "I'll keep today's distance in the bond card"
            + (f" ({last[:80]})." if last else ".")
        )
    )
    _append_chat(
        session, role="system", name="Studio", text=coda, meta={"kind": "finish"},
    )
    # **Make it possible to tell afterwards what was thrown to the green room
    # (2026-09-10).** The Showrunner: "the trouble is that the timing of each of
    # these is quite hard to see". The diary, the report, the outing, the proposals
    # and the habit note are all stacked onto the spooler by
    # `muse.service.finish_session`, so from Refine's side it ended invisibly. At
    # the very least, **whether the material was there** is recorded.
    try:
        notes = [str(n).strip() for n in (session.get("notes") or []) if str(n).strip()]
        debug_mod.note(
            session, "wrap_handover",
            detail=(
                f"監督の指示メモ {len(notes)}件"
                + ("（癖メモの目が出れば書かれる・18%）" if notes
                   else "（**0件なので癖メモは出ない**）")
            ),
            notes=notes[-8:],
            habit_possible=bool(notes),
        )
    except Exception:
        logger.debug("[muse] wrap handover note failed", exc_info=True)

    # Prefer Muse diary pipeline when available.
    try:
        from . import shared as muse_service
        session["studio"] = STUDIO  # keep identity
        # Temporarily mark so Muse finish accepts shoot images.
        return await muse_service.finish_session(
            db,
            request.app.state.spooler,
            session,
            ollama=request.app.state.ollama,
            comfy=request.app.state.comfy,
        )
    except Exception as exc:
        # Soft fallback — still mark finished without diary job.
        logger.warning("[muse] finish via muse failed: %s", exc)
        session["status"] = "finished"
        session["diary"] = {"status": "skipped", "reason": "refine_fallback"}
        await session_db.save(db, session)
        return session


async def list_refine_sessions(db, *, limit: int = 20) -> list[dict[str, Any]]:
    # **The listing owns `studio` (2026-09-07).** This used to load every session
    # one by one and then throw classic's away — reading Muse's turns as well. Since
    # `list_recent` filters, only this studio's own rows are loaded (to show the
    # names).
    rows = await session_db.list_recent(db, limit=limit, studio=STUDIO)
    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("session_id")
        if not sid:
            continue
        full = await session_db.load(db, sid)
        if not full:
            continue
        out.append({
            "session_id": sid,
            "status": full.get("status"),
            "theme": (_inputs(full).get("theme") or ""),
            "created_at": full.get("created_at"),
            "character": (full.get("character") or {}).get("name_ja")
            or (full.get("character") or {}).get("name")
            or "",
        })
        if len(out) >= limit:
            break
    return out


async def open_table(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """Open the table — the three opening seats rough the shot in. (2026-09-11)

    The Showrunner: "I want the studio shoot (the mode with several crew members)
    inside Muse refine".

    The same two stages as classic's `start_table`. First **wardrobe → camera →
    lead** settle the place and the performance on their own; from there the
    Showrunner looks at a picture and asks for what he wants. This is what keeps
    classic's failure from repeating — eighteen seats nodding at each other for
    twenty turns in front of an empty ledger.

    **This is the only door.** In a session without the mark
    (`crew_room.TABLE_OPEN`), the crew never walks on a conversation turn.
    """
    if not (session.get("character") or {}).get("character_id"):
        raise RefineError(
            "キャラを選んでから班を開いてください。"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Cast the lead before opening the table."
        )
    # **It does not open until a crew is chosen (2026-09-13).** The default was
    # emptied on the Showrunner's instruction. Who is in the room changes both the
    # picture and the speed, so `standard` is never applied silently.
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    if not str(_inputs(session).get("crew_preset") or "").strip():
        raise RefineError(
            "撮影班を選んでから開いてください。"
            if ja else "Pick a crew before opening the table."
        )
    cast = crew_room.cast_of(session)
    if not cast:
        raise RefineError(
            "撮影班が組めていません" if ja else "That crew has no seats"
        )

    locale = str(_inputs(session).get("locale") or "ja")
    session[crew_room.TABLE_OPEN] = True
    session.setdefault("opened", True)

    # **She gets dressed at the crew's door too (2026-09-16).** The Showrunner:
    # "there are cases where the default outfit is not loaded when the first
    # conversation starts".
    #
    # For a studio shoot, the screen's Start hits **here**, not `/open`
    # (`MusePanel.vue`'s `door`). Only `open_session` did the dressing, so **a
    # session started with a crew began with empty clothes** (live `f8961eaa`: on
    # turn one the ledger's `wearing` was empty too, and clothes only went in when
    # the Showrunner said 「ネグリジェ」 — "a negligee").
    #
    # It goes **before** the three opening seats — the wardrobe seat's job is to add
    # texture to the value it sees, and shown an empty field it starts from nothing.
    # `dress_from_signature` fills **only when empty**, so passing through twice
    # does not change her clothes.
    dress_patch = talk.dress_from_signature(session)
    if dress_patch:
        debug_mod.note(session, "opening_dress", detail=str(dress_patch), patch=dress_patch)

    _append_chat(
        session, role="system", name="Studio",
        text=(
            "総監督、まず場所と芝居だけ決めます。衣装・撮影・主演の三人で当たりを"
            "付けますので、そのあと「こういう絵が欲しい」を聞かせてください。"
            "そこから全班で詰めます。"
            if locale.startswith("ja") else
            "Showrunner — place and performance first. Wardrobe, the camera and "
            "the Lead will rough it in; tell us what picture you want off that, "
            "and the full crew takes it from there."
        ),
        meta={"kind": "table_open", "seats": len(cast)},
    )
    await session_db.save(db, session)

    t0 = time.monotonic()
    floor = await crew_room.run_table(
        db, ollama, session,
        director_line=str(_inputs(session).get("theme") or "").strip()
        or "今日の当たりを付けて。",
        opening=True,
    )
    debug_mod.stage(session, "crew_opening", t0)
    _publish_floor(session, floor, locale=locale)

    craft = crew_room.craft_block(floor)
    if craft:
        led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
        before = dict(led)
        patch = await writer.write_patch(
            ollama,
            model=str(_inputs(session).get("model") or ""),
            user_line=str(_inputs(session).get("theme") or "").strip()
            or "今日の当たり",
            ledger=led,
            recent="",
            num_ctx=refine_num_ctx(session),
            crew_craft=craft,
        )
        patch = ledger_mod.scrub_patch(patch, led, allow_clear=set())
        if ledger_mod.touched_picture(patch):
            led = ledger_mod.apply_patch(led, patch)
            session["refine_ledger"] = led
            _change_event(
                session, source="crew_opening", patch=patch,
                before=before, after=led, locale=locale,
            )
    assemble.touch_craft(session)
    session["status"] = "chat"
    await session_db.save(db, session)
    return session

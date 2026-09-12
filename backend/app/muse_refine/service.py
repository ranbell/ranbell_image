"""Muse Refine session orchestration — independent of muse.service."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..characters import presets as presets_db
from ..muse import events, session_db, vitality
from ..muse.defaults import ALL_DEFAULTS
from ..muse.notebook import blank as notebook_blank
from . import assemble, crew_room, debug as debug_mod, ledger as ledger_mod
from . import persona, pipeline_view, talk, writer
from .ctx import refine_num_ctx

logger = logging.getLogger(__name__)

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
            "name": char.get("name_ja") or char.get("name") or "",
            "board": char.get("board") or {},
        },
        "partner_character": {
            "character_id": partner.get("character_id", ""),
            "name": partner.get("name_ja") or partner.get("name") or "",
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
            # 会話のターンでは散文とタグの組み上げを撮る時まで待つ（`touch_craft`）。
            # 画面はこの旗を見て「試し撮りで組み直します」と出す。
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
        # スタジオ撮り（班）。画面はこの二つでボタンの出し分けをする。
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
        logger.debug("[muse_refine] partner chemistry load failed", exc_info=True)
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
    """実行時設定をセッションに積む。**文脈長を判定係と揃えるため。**"""
    try:
        from ..runtime_config import get_runtime_config
        cfg = await get_runtime_config(db)
    except Exception:
        logger.debug("[muse_refine] runtime config unavailable", exc_info=True)
        return {}
    session["_runtime_cfg"] = cfg
    return cfg


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
    # **文脈長を最初のターンから揃える。** 判定係（`persona.contract_check_with_db`）
    # が `_runtime_cfg` を積むのは会話が始まってからで、開幕だけ既定値のまま
    # 走ると、そこで一度モデルを読み直す（実測 11〜24秒）。
    await _load_runtime_cfg(db, session)
    # 開幕の一言も同じ —— 一人なら二人目の欄を見せない（`chat` と同じ判断）。
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
    dress_patch = talk.dress_from_signature(session)
    if dress_patch:
        debug_mod.note(session, "opening_dress", detail=str(dress_patch), patch=dress_patch)

    # **「またあの感じ」は Refine では出さない（2026-09-10）。** 総監督
    # 「前回の内容からの提案は削除して時間短縮」。`vitality.again_that_feel_hint`
    # 自体は classic Muse が使うので残してある。
    talk.prepare_vitality_flags(session, user_line="")
    # Opening: B does not steal the first hello unless W and turn says so.
    session["w_b_leads"] = False

    await assemble.rebuild_craft(db, ollama, session)
    now = str((session.get("craft") or {}).get("now") or "")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}

    if theme:
        _append_chat(
            session,
            role="system",
            name="Theme",
            text=theme,
            meta={"kind": "theme"},
        )

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
        # **thinking は明示して切る（2026-09-07）。** 送らないと模型側の
        # 既定に従い、この一回が 14〜15秒（`think=False` なら 1.1〜1.6秒・
        # 実測 26B・同じプロンプト n=2）。**出力も薄くなる**（67〜91字 対
        # 141〜146字）。1ターンに数回叩くので、分単位の待ちになって描画まで
        # 届かない。Muse は `chain._call` が毎回 `think=False` を送っている。
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
    """台帳が動いたことを**記録に**残す。会話欄には出さない。（2026-09-10）

    総監督「会話部分の情報が多いので、画の更新などの情報は表示しなくていいかな。
    ログで見えるので」。

    以前はここで `kind: "ledger_change"` の行を会話に積んでいた。`record_rewrite`
    が書き換え記録と `ledger_rewrite` の SSE を出すので、`/debug` と `/pipeline`
    には今まで通り残る。会話の側は、彼女の台詞に付く 🖼 のアイコン
    （`_mark_turn_shot`）で足りる。

    **消したのは `ledger_change` だけ。** `ledger_missed`（絵の指示に見えるのに
    台帳が動かなかった回）は言い直しの合図なので、今まで通り会話に出す。

    `patch` と `locale` は呼び出し側の形をそのままにしてある —— 会話に戻したく
    なったときに、呼ぶ側を触らずに済むように。
    """
    debug_mod.record_rewrite(
        session, source, before=before, after=after, intent=source,
    )


def _publish_floor(
    session: dict[str, Any], floor: list[dict[str, Any]], *, locale: str,
) -> None:
    """班の発言を会話欄に積む。（2026-09-11）

    席の一言は `kind: "seat"`、やじは `kind: "heckle"`。どちらも
    **画面では彼女の台詞と別の見た目**にする —— 18人が喋るので、主演の声が
    埋もれないように。欄を持つ席には、その欄の名前を旗として付ける。
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
    """この回が画を動かしたかを、彼女の台詞の行に押す。（2026-09-10）

    総監督「会話オンリーか画像プロンプト生成かはアイコンで分かるように」。

    `mark` はターンの頭で控えた `len(chat)`。そこから後ろの `say` の行にだけ
    押す —— 内心（banter）や提案（pitch）は喋りの続きなので、印は台詞に一つ。

    **古い行には触らない。** 押していない行は `meta.shot` を持たないので、
    画面は何も出さない（`=== true` / `=== false` で見る）。
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


#: 癖メモが読むのは末尾8件（`muse.service._director_highlights`）。**溜めない。**
_NOTES_MAX = 24


def _keep_note(session: dict[str, Any], text: str) -> None:
    """絵を動かした監督の一行を控える。（2026-09-10）

    総監督「癖メモかけるようにしよう」。

    スタジオ手帖の癖メモは classic の `finish_session` が出すが、二つの門が
    どちらも `session["notes"]` を読む —— `lounge.should_write_habit` と、
    書く側の `muse.service._director_highlights`。**Refine は常設の指示を
    `standing` に入れていて `notes` を一度も埋めていなかった**ので、一度も
    出ていなかった。

    入れるのは**絵を動かした一行だけ**。「かわいいよ」のような会話だけの回は
    癖の材料にならないし、条文（`crew.showrunner_habit_prompt`）が求めている
    のも「総監督の指示メモ」。同じ行が続けて来たら積み直さない（classic の
    `_add_note` と同じ）。
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
    """絵が模型に届かなかったことを、口に出して言う。（2026-09-10）

    絵を読めないモデルは**断らずに空を返す**。黙って絵抜きに落ちると、
    総監督からは「今日は口数が少ないな」にしか見えない。一度だけ言う。

    classic の `muse.service._note_blind` と同じ文言・同じ作法。
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
    # **一人か二人か（2026-09-09）。** 台帳の二人目の欄は `blank()` が常に
    # 埋めるので、模型には空の `wearing_b` / `beat_b` が見えていた。空欄は
    # 「埋めろ」に見える —— 総監督「一人しかいないときに muse_b の tag を
    # 編集してしまう」。ここで一度決めて、模型に触る三つの席へ渡す。
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

    # **この回が「喋っただけ」か「画を動かした」か（2026-09-10）。** 総監督
    # 「会話オンリーか画像プロンプト生成かはアイコンで分かるように」。彼女の
    # 台詞は propose や自己修復より前に積まれるので、その場では決まらない。
    # ここで印をつける位置だけ控えて、ターンの終わりに `_mark_turn_shot` で押す。
    turn_mark = len(session.get("chat") or [])

    _append_chat(session, role="user", name="Director", text=text)
    events.publish(session["session_id"], {"type": "chat", "role": "user", "text": text})
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

    led = dict(before)
    director_recent = _director_tail(session)

    # **班が居るなら、writer の手前で一周する（2026-09-11）。** 総監督
    # 「スタジオ撮りを Muse refine に取り込みたい」。席は台帳に直接書かない ——
    # classic の「書くのは Scripter 一人」をそのまま持ってきていて、Refine では
    # その Scripter が下の `write_patch`。門は `mode` ではなく席の実体。
    crew_craft = ""
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
    patch = ledger_mod.scrub_patch(patch, led, allow_clear=allow_clear)
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

    # **会話の途中で絵を組み直さない（2026-09-10）。** 総監督「撮影に入らない
    # ときの会話のみの回答はもっと早くしてほしい」。ここで組んだ散文とタグを
    # 使うのは試し撮りと本番だけで、そちらは自前で `rebuild_craft` を呼ぶ。
    # 女優が要るのは `now` の一行だけなので、模型を使わずに更新する。
    assemble.touch_craft(session)
    now = str((session.get("craft") or {}).get("now") or "")
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    # 監督の一行がどこまで台帳を動かしたか、ここで確定する。以降で動くのは
    # 彼女自身の propose と自己修復なので、再判定の要否はここで測る。
    after_director = dict(led)

    lead_cid = str(char.get("character_id") or "")
    try:
        from ..muse import shared as muse_service
        on_token = muse_service._token_publisher(session["session_id"], lead_cid)
    except Exception:
        logger.debug("[muse_refine] token publisher unavailable", exc_info=True)
        on_token = None

    # **試し撮りのあとは、彼女に絵を見せる（2026-09-10）。** 総監督
    # 「試し撮りしたあとは Muse が画像見るようにしよう」。
    #
    # 台帳は「こう撮ってほしい」で、絵は「こう撮れた」。台帳しか見えないと、
    # 撮れた絵そのものについて話せない —— classic は試し撮り以降、毎ターン板を
    # 渡している（`board_images` → `run_duet_talk`）。同じものを使う（縮小も
    # 読み込みもあちらが見ていて、板が無い・生成中・読めないときは空が返る）。
    board_shots: list[bytes] = []
    try:
        from ..muse import shared as muse_service
        board_shots = await muse_service.board_images(db, session)
    except Exception:
        logger.debug("[muse_refine] board image unavailable", exc_info=True)
    # 絵を渡す回だけ、絵を読めるモデルに換える（空欄なら model と同じ）。
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
        # `_say_only` が `SAY:` の中だけを通す —— 欄の名前も `MY_FEEL:` も
        # 画面に出さない。Refine の欄名は classic の `_SAY_SHUT_RE` に
        # 全部入っている（SAY / ASIDE / CARD / PITCH / MY_FEEL）。
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

    # **彼女が服を選んだ回は、そう記録する（2026-09-09）。** 総監督
    # 「埋めてよいが、記録に残す」「彼女自体がこうしたいと思った内容をちゃんと
    # 反映できるようにしたい」。`wearing` は空欄のときだけ通る（fill-empty・
    # `guard_muse_propose`）ので、通ったということは**誰も服を着せていない場面
    # で彼女が決めた**ということ。デバッグ枠に出さないと、総監督からは
    # 「指示がないのに着替えた」としか見えない。
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

    # **絵が動いていない回は、再判定に訊くことがない（2026-09-10）。**
    #
    # verify の仕事は「台帳が監督の意図と合っているか」。台帳が一つも動かず、
    # 監督の一行も絵の話に見えないターン（「今日はありがとう」）では、比べる
    # 相手がいない。それでも毎回走らせて 2,797字を読ませ、彼女の声で一言
    # 書かせていた —— 入力だけで約4秒。
    #
    # **取りこぼしの穴は開けない。** `missed`（絵の指示に見えるのに writer が
    # 何も書かなかった回）は、まさに verify に拾ってほしい回なので走らせる。
    # **測るのは「監督が動かしたぶん」だけ（2026-09-10）。** `before` と今の
    # 台帳を比べると、彼女が表情を一語足しただけの回まで「動いた」になり、
    # 実測 5.9〜6.8秒の再判定が毎回走っていた。verify の仕事は台帳が**監督の**
    # 意図と合っているかなので、監督が絵の話をしていない回には比べる相手が
    # いない。彼女の propose は `guard_muse_propose` が空欄埋めに限っている。
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
        # ターン前の台帳と直近の流れ。条文の「前ターンを保て」は、これが
        # 無いと比べようがない（`before` はこの関数の最初から手元にある）。
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


async def start_shoot(db, request, session: dict[str, Any]) -> dict[str, Any]:
    """Approve board → final shoot. Requires a finished board (OK gate)."""
    from ..muse import runner
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
        "seed": int(board.get("seed") or 0),
        "job_id": "",
    }
    session["status"] = "shooting"
    await session_db.save(db, session)

    # **描画の直前に LLM を VRAM から落とす（Muse と同じ）。**
    #
    # 一つ上で `rebuild_craft` がモデルを使っているので、ここで返さないと
    # 26B が ~13GB を握ったまま ComfyUI が latent を置きにいく —— 16GB の
    # カードでは置けずにコケる（総監督の実測）。Muse は board / shoot の
    # 両方でこの一行を踏んでいて、Refine だけが踏んでいなかった。
    #
    # 既定は `unload_vlm: True`（`ALL_DEFAULTS` から来る）。切り替えの意味も
    # 判定も Muse と同じものを使う —— 二つ目の実装を持たない。
    from ..muse import shared as muse_service
    await muse_service._maybe_unload(request.app.state.ollama, session)

    spooler = request.app.state.spooler
    session["shoot"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_refine_shoot",
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
    from ..muse import runner
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
        "seed": 0,
        "job_id": "",
        "ledger_fp": "|".join(str(led.get(k) or "") for k in ledger_mod.LEDGER_KEYS),
        "round": int((session.get("board") or {}).get("round") or 0) + 1,
    }
    session["status"] = "boarding"
    await session_db.save(db, session)

    # **描画の直前に LLM を VRAM から落とす（Muse と同じ）。**
    #
    # 一つ上で `rebuild_craft` がモデルを使っているので、ここで返さないと
    # 26B が ~13GB を握ったまま ComfyUI が latent を置きにいく —— 16GB の
    # カードでは置けずにコケる（総監督の実測）。Muse は board / shoot の
    # 両方でこの一行を踏んでいて、Refine だけが踏んでいなかった。
    #
    # 既定は `unload_vlm: True`（`ALL_DEFAULTS` から来る）。切り替えの意味も
    # 判定も Muse と同じものを使う —— 二つ目の実装を持たない。
    from ..muse import shared as muse_service
    await muse_service._maybe_unload(request.app.state.ollama, session)

    spooler = request.app.state.spooler
    session["board"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_refine_board",
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
    # **何を楽屋に投げたか、後から分かるようにする（2026-09-10）。** 総監督
    # 「これなかなか各タイミングが分かりにくいのが難点」。日記・報告・お出かけ・
    # 提案・癖メモは `muse.service.finish_session` が spooler に積むので、Refine
    # からは見えないまま終わっていた。少なくとも**材料が揃っていたか**は残す。
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
        logger.debug("[muse_refine] wrap handover note failed", exc_info=True)

    # Prefer Muse diary pipeline when available.
    try:
        from ..muse import shared as muse_service
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
        logger.warning("[muse_refine] finish via muse failed: %s", exc)
        session["status"] = "finished"
        session["diary"] = {"status": "skipped", "reason": "refine_fallback"}
        await session_db.save(db, session)
        return session


async def list_refine_sessions(db, *, limit: int = 20) -> list[dict[str, Any]]:
    # **studio は一覧が持つ（2026-09-07）。** 以前はここで全セッションを一つずつ
    # load してから classic の分を捨てていた —— Muse の回まで読んでいた。
    # `list_recent` が絞るので、load するのは自分の分だけ（名前を出すため）。
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
    """班を開く —— 開幕の三席が当たりを付ける。（2026-09-11）

    総監督「スタジオ撮り（複数の撮影スタッフのモード）を Muse refine に取り込みたい」。

    classic の `start_table` と同じ二段構え。まず**衣装 → 撮影 → 主演**の三席だけで
    場所と芝居を決め、そこから先は総監督が絵を見て注文する。全18席が空の台帳を
    前に二十ターン頷き合う、という classic の失敗を繰り返さないため。

    **開けるのはここだけ。** 印（`crew_room.TABLE_OPEN`）が立っていない
    セッションでは、会話のターンで班は一度も回らない。
    """
    if not (session.get("character") or {}).get("character_id"):
        raise RefineError(
            "キャラを選んでから班を開いてください。"
            if str(_inputs(session).get("locale") or "ja").startswith("ja") else
            "Cast the lead before opening the table."
        )
    cast = crew_room.cast_of(session)
    if not cast:
        raise RefineError("撮影班が組めていません")

    locale = str(_inputs(session).get("locale") or "ja")
    session[crew_room.TABLE_OPEN] = True
    session.setdefault("opened", True)
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

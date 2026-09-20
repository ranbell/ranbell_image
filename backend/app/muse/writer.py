"""Single field-writer LLM for Muse Refine."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import identity
from . import ledger as ledger_mod
from . import persona

logger = logging.getLogger(__name__)

WRITER_SYSTEM = """You update a shot ledger. Output ONLY a JSON object.
Keys allowed: wearing, beat, expression, scene, light, bg, frame,
wearing_b, beat_b, expression_b, lettering, atmosphere, look, wearing_drop.
Rules:
- Absolute phrases in English (danbooru-friendly words ok).
- Carefully read the latest conversation, and update only the fields that have
  changed from the previous state to reflect the latest status.
  Carefully evaluate the conversation context to decide if any fields 
  need to be added or cleared.
- Clothes and place are independent: changing clothes must not clear scene.
- Changing place must not undress her.
- wearing_drop: one garment name to remove, only when asked to take something off.
  **The beat often still names it** — `hands in her hoodie pocket`,
  `holding the hem of her cardigan`, `hands in her coat sleeves`. Rewrite
  beat in the SAME patch, or the garment stays in the picture: she cannot
  have her hands in a pocket that is no longer there.
- STICKY (long chat): atmosphere, look, lettering PERSIST across turns.
  Omit those keys to KEEP the current value. NEVER send "" to clear them
  unless the director explicitly asked to reset/clear that axis.
- atmosphere: mood / air (wistful, tense, cozy…). Only when they ask to change mood.
- look: art direction / render. Only when they ask to change art style.
- lettering: short Latin words with double quotation for a sign only when they asked for text in frame.
- bg: what is actually behind her. Not a single word — name the things that
  are there: `laundry machines, folded towels, coin slot panel`,
  `hanging ferns, misted glass, watering can`. Place several items when the
  place has several. Those two are only the shape — name what THIS place has.
- beat / frame: Describe everything that needs to be visible in the photograph
  using clear terms (danbooru tags). Additionally, be sure to list
  anything not explicitly stated that must naturally appear in the image
  (e.g., if the wind blows and her hair sweeps aside,
  the nape of the neck becomes visible).
- ONE BODY, ONE INSTANT (beat): the field is rewritten WHOLE, so it must still
  name the posture she is in and everything the body is doing — this is not a
  reason to write less. What it must not contain is the same part of her
  answered twice: not as a contradiction (weight on the front foot AND on the
  back foot) and not as a restatement in other words (hips thrust forward AND
  hips pushed out, or the same object held twice). When the crew offers a
  second wording for something already in the field, keep the one the director
  asked for and leave the other out. Her two hands are two different things:
  one on her hip while the other carries something is one body.
- If the line is only emotion / banter / acknowledgement with NO picture or
  mood/look change, return {}.
- Multiple fields in one line → include all of them in one object.
- wearing_b / beat_b / expression_b are the SECOND person's, and exist only
  when a partner Muse is in the shot. They are hers, never the lead's.
- TWO IN FRAME — **split a line that names them.** Whatever follows a name
  belongs to HER alone; never copy it onto the other. The CAST line above says
  which name is the lead (plain fields) and which is the partner (_b fields):
    「<partner> は身を乗り出して、<lead> はトレイを持ち直して」
      beat:   adjusting her grip on the tray      ← lead only
      beat_b: leaning forward                     ← partner only
  Give them the SAME value only when the line actually says 「二人とも」 /
  "both" / "each of you".
"""

WRITER_RETRY = """The last line looks like a picture or mood/look direction, but you returned {}.
Read it again. If it names clothes, place, pose, face, light, camera,
atmosphere (mood), or look (art style), fill those keys.
Do NOT blank sticky atmosphere/look/lettering to "keep" them — omit the key.
Still return {} only for pure emotion/banter with no picture/mood/look change.
Absolute phrases in English (danbooru-friendly words ok).
Output ONLY JSON.
"""
# **The English rule was missing from the retry (2026-09-20).** This head
# *replaces* `WRITER_SYSTEM` rather than being added to it, so the one line that
# says what language the ledger is written in was gone on exactly the calls that
# needed it most — measured, 「雨上がりの帰り道」 came back as
# `{"scene": "雨上がりの帰り道"}` and went into the prompt as Japanese.

VERIFY_SYSTEM = """You check whether the shot LEDGER matches the director's latest intent.

Compare the DIRECTOR line to LEDGER NOW. LEDGER BEFORE THIS TURN is the shot as
it stood before the line; RECENT DIRECTOR LINES is what led up to it. Ignore
pure emotion/banter — those need no picture change.

COMMENT must be spoken IN CHARACTER using the VOICE / character contract (first
person, address, talk quirks, example rhythm). Generic announcer lines like
"確認しました" without her quirks are a failure.

Output exactly:
OK: yes
COMMENT: <one short spoken line in HER voice confirming the shot is right>
or
OK: no
COMMENT: <one short spoken line in HER voice: admit the miss and that YOU will fix it>
REPAIR: <JSON object with ledger keys to fix — absolute English phrases, only wrong fields>

Rules:
- CRITICAL: Pay close attention to the previous conversation.
  Changes, additions, or removals of the pose, outfit, and background are ONLY allowed
  if explicitly requested. Otherwise, always retain the exact pose, outfit, and background from the previous turn.
- Clothes and place are independent.
- REPAIR only when OK: no. Empty {} is not allowed when OK: no if the director named a picture change.
- Do not invent unrelated wardrobe. Fix only what the director asked.
- STICKY: do not clear or rewrite atmosphere / look / lettering in REPAIR
  unless the director's latest line asked to change that axis.
- Do not blank wearing/scene/beat with "" — omit keys you are not fixing.
- COMMENT: no danbooru tags, no system jargon.
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    fence = re.search(r"\{[\s\S]*\}", raw)
    if not fence:
        return {}
    try:
        data = json.loads(fence.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def write_patch(
    ollama,
    *,
    model: str,
    user_line: str,
    ledger: dict[str, str],
    recent: str = "",
    retry: bool = False,
    num_ctx: int | None = None,
    partner: bool = False,
    name_a: str = "",
    name_b: str = "",
    crew_craft: str = "",
) -> dict[str, str]:
    """One LLM call → absolute patch (may be empty).

    **Say the headcount (2026-09-09).** The Showrunner: "when there is only one
    person it still edits muse_b's tags. The explanation of one versus two is not
    enough." The second person's fields are dropped from the ledger
    (`ledger.for_model`) and the headcount is stated in one line
    (`ledger.cast_line`).
    """
    head = WRITER_RETRY if retry else WRITER_SYSTEM
    # **On a turn where the crew spoke, read their material too (2026-09-11).** The
    # seats never write the ledger directly — classic's contract, "only the Scripter
    # writes", is carried over as it was, and in Refine that Scripter is here. It
    # arrives gathered by field (`crew_room.craft_block`). The rule too is delivered
    # **only on turns where the crew spoke**: kept permanently in the contract, a
    # solo shoot's prompt moves by a character (it would be reading an explanation
    # of a box that is not there).
    crew_block = (
        "\n" + crew_craft.strip() + "\n"
        "Those seats are the specialists for their fields. **Fold their detail\n"
        "INTO the field, keeping what is already there** — they shape under the\n"
        "key, they do not replace it. Drop a seat's note only when the\n"
        "director's latest line contradicts her.\n"
        # **Whatever is read last wins (measured, many times).** Writing "one body"
        # into the contract (`WRITER_SYSTEM`) does not help if "just append" arrives
        # last here — it becomes an accumulation. It is delivered only on turns where
        # the crew spoke, so a solo shoot's prompt does not move by a character.
        "**A seat that says in other words something the field already says adds\n"
        "NOTHING — keep the field as it is.** One weight, one set of hips, one\n"
        "head, and each object in her hands named once. Two seats describing the\n"
        "same part of her body is one answer, not two.\n"
        if str(crew_craft or "").strip() else ""
    )
    prompt = (
        f"{head}\n\n"
        f"{ledger_mod.cast_line(partner=partner, name_a=name_a, name_b=name_b)}\n\n"
        f"LEDGER NOW:\n"
        f"{json.dumps(ledger_mod.for_model(ledger, partner=partner), ensure_ascii=False)}\n\n"
        f"RECENT DIRECTOR LINES:\n{recent or '(none)'}\n"
        f"{crew_block}\n"
        f"LATEST LINE:\n{user_line.strip()}\n"
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
            options={"num_ctx": num_ctx} if num_ctx else None,
        )
    except Exception:
        logger.exception("[muse] writer failed")
        return {}
    return ledger_mod.normalize_patch(_extract_json_object(raw))


def parse_actress(raw: str) -> dict[str, Any]:
    """Parse actress turn into say/aside/propose/my_feel/card/pitch.

    Backward-compatible callers may still unpack the first three keys.
    """
    text = (raw or "").strip()
    out: dict[str, Any] = {
        "say": "",
        "aside": "",
        "propose": {},
        "my_feel": "",
        "card": "",
        "pitch": "",
    }
    if not text:
        return out

    propose: dict[str, str] = {}
    m_prop = re.search(r"(?is)\bPROPOSE\s*:\s*(\{[\s\S]*\})\s*$", text)
    body = text
    if m_prop:
        propose = ledger_mod.normalize_patch(_extract_json_object(m_prop.group(1)))
        body = text[: m_prop.start()].strip()

    blocks = identity.parse_talk_blocks(body)
    say = identity.sanitize_muse_say(blocks.get("say") or "", locale="ja")
    aside = (blocks.get("aside") or "").strip()
    # Keep aside to first whisper beat if it spilled.
    if aside:
        aside = re.sub(
            r"(?is)\b(?:PROPOSE|CARD|PITCH|MY_FEEL)\s*:.*$", "", aside,
        ).strip()
        lines = [ln.strip() for ln in aside.splitlines() if ln.strip()]
        aside = " ".join(lines[:2]) if lines else ""

    card = (blocks.get("card") or "").strip()
    pitch = (blocks.get("pitch") or "").strip()
    my_feel = (blocks.get("my_feel") or "").strip().splitlines()[0].strip() if blocks.get("my_feel") else ""

    # If PROPOSE empty but CARD names fields, lift CARD → propose.
    if not ledger_mod.touched_picture(propose) and card:
        from_card = ledger_mod.normalize_patch(persona.card_to_patch(card))
        if ledger_mod.touched_picture(from_card):
            propose = from_card

    out.update({
        "say": say or (body.strip() if not blocks.get("say") and not card else say),
        "aside": aside,
        "propose": propose,
        "my_feel": my_feel,
        "card": card,
        "pitch": pitch,
    })
    if not out["say"] and body and not any(blocks.get(k) for k in ("aside", "card", "pitch", "my_feel")):
        out["say"] = identity.sanitize_muse_say(body, locale="ja")
    return out


def parse_verify(raw: str) -> tuple[bool, str, dict[str, str]]:
    """Returns (ok, comment, repair_patch)."""
    text = (raw or "").strip()
    if not text:
        return True, "", {}
    ok_m = re.search(r"(?im)^\s*OK\s*:\s*(yes|no|true|false|ok|ng)\s*$", text)
    if not ok_m:
        ok_m = re.search(r"(?i)\bOK\s*:\s*(yes|no|true|false|ok|ng)\b", text)
    ok_tok = (ok_m.group(1).lower() if ok_m else "yes")
    ok = ok_tok in {"yes", "true", "ok"}

    comment = ""
    c_m = re.search(r"(?is)\bCOMMENT\s*:\s*(.+?)(?=\n\s*REPAIR\s*:|\Z)", text)
    if c_m:
        comment = c_m.group(1).strip()
        comment = re.sub(r"(?is)\bREPAIR\s*:.*$", "", comment).strip()
        comment = comment.splitlines()[0].strip() if comment else ""

    repair: dict[str, str] = {}
    r_m = re.search(r"(?is)\bREPAIR\s*:\s*(\{[\s\S]*\})\s*$", text)
    if r_m:
        repair = ledger_mod.normalize_patch(_extract_json_object(r_m.group(1)))
    elif not ok:
        repair = ledger_mod.normalize_patch(_extract_json_object(text))

    if ok:
        repair = {}
    return ok, comment, repair


def _watch_the_window(session: dict[str, Any], who: str):
    """Record a turn cut off by the window in `/debug`. **Never let it pass as a
    short reply.** (2026-09-18)"""
    def _note(done: dict[str, Any]) -> None:
        if str(done.get("reason") or "") != "length":
            return
        try:
            from . import debug as debug_mod

            debug_mod.note(
                session, "cut_by_the_window",
                detail=f"{who}: 前置き {done.get('prompt_tokens')}tok "
                       f"＋ 出力 {done.get('eval_tokens')}tok で枠に当たった",
            )
        except Exception:
            logger.debug("[muse] could not note the cut", exc_info=True)
        logger.warning("[muse] %s was cut by the window: %s", who, done)
    return _note


async def actress_turn(
    ollama,
    *,
    model: str,
    locale: str,
    name: str,
    now: str,
    ledger: dict[str, str],
    identity_blurb: str,
    user_line: str,
    director_tail: str,
    num_ctx: int | None = None,
    session: dict[str, Any] | None = None,
    character: dict[str, Any] | None = None,
    partner: bool = False,
    name_b: str = "",
    on_token=None,
    images: list[bytes] | None = None,
) -> dict[str, Any]:
    lang = "Japanese" if locale.startswith("ja") else "English"
    sess = session or {"character": character or {}, "session_id": ""}
    if character and not sess.get("character"):
        sess = {**sess, "character": character}
    system = persona.actress_system(
        sess, locale=locale, ledger=ledger, now=now,
    )
    prompt = (
        f"{system}\n\n"
        f"Language for SAY/ASIDE: {lang}. Lead name: {name or 'Muse'}.\n\n"
        f"WHO YOU ARE (locked identity — do not contradict):\n"
        f"{identity_blurb or '(unspecified)'}\n\n"
        f"{ledger_mod.cast_line(partner=partner, name_a=name, name_b=name_b)}\n\n"
        f"LEDGER (absolute shot document):\n"
        f"{json.dumps(ledger_mod.for_model(ledger, partner=partner), ensure_ascii=False, indent=2)}\n\n"
        f"NOW:\n{now}\n\n"
        f"RECENT DIRECTOR LINES (voice context only — not shot truth):\n"
        f"{director_tail or '(none)'}\n\n"
        f"DIRECTOR:\n{user_line.strip()}\n"
    )
    try:
        # **Thinking is switched off explicitly (2026-09-07).** Unsent, the
        # model's own default applies and this one call takes 14-15 seconds (1.1-1.6
        # with `think=False`; measured, 26B, same prompt, n=2). **The output is
        # thinner as well** (67-91 characters against 141-146). It is called several
        # times a turn, so the wait runs into minutes and never reaches the render.
        # In Muse, `chain._call` sends `think=False` every time.
        #
        # **Stream it (2026-09-10).** The Showrunner: "the conversation is not
        # streamed, so the wait really is felt". This stage measures 20.3 seconds,
        # of which 18.1 is reading the prompt. The total does not change, but
        # waiting in silence for the end and seeing characters appear partway are
        # different kinds of waiting. Classic already does this.
        opts = {"num_ctx": num_ctx} if num_ctx else None
        blind = False
        # **If the window cuts it, record that (2026-09-18).** This is the longest
        # preamble in a turn (measured 14,000-18,000 characters), so if anything
        # overflows it is here first.
        watch = _watch_the_window(sess, "主演")
        raw = await _say(ollama, prompt, model=model, options=opts,
                         on_token=on_token, images=images, on_done=watch)
        out = parse_actress(raw)
        # **If she falls silent on a turn with a picture, retry once without it.**
        #
        # The first version (2026-09-10) looked only for "the whole reply is empty".
        # What failed live (`cdf8d4f7` 23:45:56) was short of that — on a turn with
        # the board shown, **only ASIDE came back and SAY was empty**, so the mutter
        # appeared while the line became a silent bubble. A model that cannot read
        # goes quiet, and a model that can read **drops the format** sometimes. What
        # is watched is "did she speak", not how long the reply was.
        if images and not str(out.get("say") or "").strip():
            logger.warning(
                "[muse] %s said nothing for an image turn — "
                "retrying blind", model,
            )
            blind = True
            raw = await _say(ollama, prompt, model=model, options=opts,
                             on_token=on_token, images=None, on_done=watch)
            out = parse_actress(raw)
        # **On a silent turn, keep what came back.** When it went silent live, the
        # record held only "it was empty" and there was no way to know what the
        # model had returned. The raw reply is carried back so the next occurrence
        # can be read.
        if not str(out.get("say") or "").strip():
            out["raw"] = (raw or "")[:400]
    except Exception as exc:
        logger.exception("[muse] actress failed")
        # **Never silently become 「……」 (2026-09-18).** This catches exceptions
        # and returns a placeholder, so **a programming mistake disguises itself as
        # "a turn where she was short of words"**. Hit live: `with_done` was not
        # added to the facade (`LlmGateway`), it raised `TypeError`, and her line was
        # 「……」 with a stage time of 0.0 seconds. The ledger and the seed were both
        # right, so e2e passed green. **Recorded, the next one is obvious at a
        # glance.**
        try:
            from . import debug as debug_mod

            debug_mod.note(
                sess, "actress_failed",
                detail=f"{type(exc).__name__}: {exc}"[:240],
            )
        except Exception:
            logger.debug("[muse] could not note the actress failure", exc_info=True)
        return {
            "say": "……" if locale.startswith("ja") else "...",
            "aside": "",
            "propose": {},
            "my_feel": "",
            "card": "",
            "pitch": "",
            "blind": False,
        }
    return {**out, "blind": blind}


async def _say(
    ollama, prompt: str, *, model: str, options: dict | None,
    on_token=None, images: list[bytes] | None = None, on_done=None,
) -> str:
    """Have her speak once — with the picture if there is one, streamed if there is
    somewhere to stream to.

    **`think=False` and `options` are written out at all four call sites.**
    Bundling them into `**kw` would stop `test_think_is_off` and `test_num_ctx`
    from checking anything (they walk the AST). Those tests exist so no quiet road
    back to default thinking can open, so the arguments are passed where they can
    be seen.
    """
    if on_token is None:
        if images:
            return await ollama.generate_vlm(
                prompt, images, model=model or None, think=False, options=options,
            )
        return await ollama.generate_text(
            prompt, model=model or None, think=False, options=options,
        )
    stream = (
        ollama.generate_vlm_stream(
            prompt, images, model=model or None, think=False, options=options,
            with_done=on_done is not None,
        )
        if images else
        ollama.generate_text_stream(
            prompt, model=model or None, think=False, options=options,
            with_done=on_done is not None,
        )
    )
    parts: list[str] = []
    async for event in stream:
        if event.get("type") == "done" and on_done is not None:
            try:
                on_done(event)
            except Exception:
                logger.debug("[muse] on_done failed", exc_info=True)
            continue
        if event.get("type") == "token" and event.get("text"):
            parts.append(event["text"])
            try:
                on_token(event["text"])
            except Exception:
                logger.debug("[muse] on_token failed", exc_info=True)
    return "".join(parts)


async def verify_and_repair(
    ollama,
    *,
    model: str,
    locale: str,
    name: str,
    user_line: str,
    ledger: dict[str, str],
    now: str,
    before: dict[str, str] | None = None,
    recent: str = "",
    partner: bool = False,
    name_b: str = "",
    force_repair_hint: bool = False,
    num_ctx: int | None = None,
    character: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, str]]:
    """After the turn: confirm intent match, or return a self-repair patch.

    **Pass `before` and `recent` (2026-09-08).** The contract says "keep the
    previous turn's pose, clothes and background unless told otherwise", and yet
    **the previous turn was not in the input** — what was passed was the
    director's line, the current ledger and a reading of the current ledger, two
    of which are the same thing. With nothing to compare against, that rule cannot
    work in principle.

    Measured (26B, two full runs) it played out exactly so:

        writer  beat: sitting on floor, **legs tucked to the side**   ← correct
        verify  OK: no "I misread the direction"
        ledger  beat: sitting on floor, **legs spread to the side**   ← another pose

    `before` had been inside `chat()` all along. `recent` did reach the writer but
    not verify, which left nothing to judge a **partial** direction like "just turn
    your face this way" against.
    """
    lang = "Japanese" if locale.startswith("ja") else "English"
    sess = session or {"character": character or {}}
    if character and not sess.get("character"):
        sess = {**sess, "character": character}
    try:
        from . import crew
        locale_key = "en" if lang == "English" else "ja"
        voice = crew._voice_block(
            sess.get("character") or character or {}, locale=locale_key,
        )
    except Exception:
        voice = ""
    hint = (
        "\nNOTE: A picture direction may have been missed earlier — look carefully.\n"
        if force_repair_hint else ""
    )
    prompt = (
        f"{VERIFY_SYSTEM}\n"
        f"Language for COMMENT: {lang}. Speaker name: {name or 'Muse'}.\n"
        f"{hint}\n"
        # **Her voice alone is enough (2026-09-10).** The point is to have the
        # COMMENT written in her mouth, so `ENTERTAINMENT_CRAFT` (how to be charming,
        # 1,417 characters) is not needed for the judgement. It was about 1.5
        # seconds of reading every turn.
        f"{voice}\n\n"
        f"{ledger_mod.cast_line(partner=partner, name_a=name, name_b=name_b)}\n\n"
        f"RECENT DIRECTOR LINES:\n{recent.strip() or '(none)'}\n\n"
        f"DIRECTOR (latest):\n{user_line.strip()}\n\n"
        f"LEDGER BEFORE THIS TURN:\n"
        f"{json.dumps(ledger_mod.for_model(before or {}, partner=partner), ensure_ascii=False, indent=2)}\n\n"
        f"LEDGER NOW (after this turn):\n"
        f"{json.dumps(ledger_mod.for_model(ledger, partner=partner), ensure_ascii=False, indent=2)}\n\n"
        f"NOW:\n{now}\n"
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
            options={"num_ctx": num_ctx} if num_ctx else None,
        )
    except Exception:
        logger.exception("[muse] verify failed")
        first = ""
        addr = ""
        char = sess.get("character") or character or {}
        if char:
            first = str(
                char.get("first_person_ja")
                or (char.get("personality") or {}).get("first_person_ja")
                or "私"
            )
            addr = str(
                char.get("user_address_ja")
                or (char.get("personality") or {}).get("user_address_ja")
                or "総監督"
            )
        if locale.startswith("ja"):
            fallback = f"{first}、この画で合ってると思うよ、{addr}。"
        else:
            fallback = "Yeah — this shot matches."
        return True, fallback, {}
    return parse_verify(raw)

"""The crew (studio shoot) — classic's table walk, carried onto Refine's ledger. (2026-09-11)

The Showrunner: "I want the studio shoot (the mode with several crew members)
inside Muse refine", "port it straight from classic first, then polish", "keep
all 18 roles".

## Carried over unchanged

- **The seat contracts** (`crew.system_prompt_for`) — 18 roles, 30 people, 6
  presets. Not one character changed
- **The walk** (`_craft_pass`) — go round in seat order, with a reactor and a
  heckler slotted between seats
- **How banter is picked** (`_pick_banter_reactor` / `_pick_extra_heckler`) and
  `banter_mode` (off / light / full)
- **Three seats open the shoot** (`OPENING_SEQUENCE` = wardrobe → camera → lead).
  Rough it in with those, then bring the whole crew

## Only the destination changed

In classic the seats were talk-only and one Scripter did the writing
(`_apply_turn`'s `talk_only = uses_notebook(session)`). In Refine
**`writer.write_patch` sits in that Scripter's chair.**

    classic   seats talk → Scripter writes the notebook → weave builds the picture
    Refine    seats talk → writer writes the ledger    → assemble builds the picture

So the notebook does not come along. A seat's `CRAFT:` line never reaches the
ledger directly — it is **material for the writer**. Which seat owns which field
is already decided by `crew.CRAFT_SLOTS`; all that is needed here is to read that
slot as a ledger field name.

## Field by field, not seat by seat (2026-09-14)

The Showrunner: "bundle the members who share a ledger field into one session so
they produce a single ledger as the conclusion — that avoids the collisions. And
have them announce what the ledger currently says first, then talk about how it
should change."

    before   twelve calls in seat order. Each seat only ever "adds" to its field
    now      nine calls, one per field (`field_groups`). Seats that share a field
             **speak in one call**, read the announcement (`field_header`) and
             settle on **one value for the whole field**

Live, `look` had grown to twelve words with `amber_theme` and `magenta_theme`
side by side (`6dc11d0e`). Fields with two seats were fought over; fields with a
single seat still silted up with restatements — because **a seat could only add,
never restate the field as a whole.**

Measured on the bench (same material, banter off, n=3,
`private/muse/crew_lab/corner_check.py`):

    seat by seat   12 calls  round 111.4s   opens with "Showrunner," 14%   look 4.3 words  SAY 96 chars
    field by field  9 calls  round  72.5s   opens with "Showrunner,"  5%   look 2.3 words  SAY 77 chars

**The announcement was in both arms**, so the difference is the bundling alone.
What it costs: SAY drops from 96 to 77 characters (one reply now carries several
people).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from . import chain, crew, events, identity
from . import debug as debug_mod
from . import ledger as ledger_mod
from .ctx import refine_num_ctx

logger = logging.getLogger(__name__)

#: 席の CRAFT slot → 台帳の欄。`crew.CRAFT_SLOTS` が席→slot を持っているので、
#: ここは slot→欄 の一段だけ。**一つの欄を複数の席が見てよい**（演出と振付は
#: どちらも体、レイアウトと撮影はどちらも構図）—— classic もそうだった。
SLOT_FIELD: dict[str, str] = {
    "BODY": "beat",
    "SHAPE": "frame",
    "OPTICS": "frame",
    "PROPS": "bg",
    "CLOTH": "wearing",
    "LIGHT": "light",
    "FACE": "expression",
    "AIR": "atmosphere",
    "COLOUR": "look",
    "RENDER": "look",
    "FINISH": "look",
}

#: 開幕の三席と、その順番。classic の `OPENING_ROLES` / `OPENING_SEQUENCE` と同じ。
#: **衣装が先に着せ、撮影が着せた姿を切り、主演が最後に演じる** —— 開幕で服に
#: 持ち主が居ないと、空の台帳に最初に書く席が服まで書いてしまう（classic の実測）。
OPENING_SEQUENCE: tuple[str, ...] = ("wardrobe", "lens", "actress")

#: ノートのターンでペンを持たない席。classic の `NOTE_MUTED` と同じ。
#: 監査と密度上げの仕事は、手帖が正本になった時点で別の段に移っている。
MUTED: frozenset[str] = frozenset(
    getattr(crew, "NOTE_MUTED", None) or getattr(crew, "BANTER_ONLY", ())
)

_CRAFT_LINE_RE = re.compile(r"(?im)^CRAFT\s*:\s*(.+?)\s*$")

#: 席の出力書式。**classic の条文の末尾を上書きする。**（2026-09-11）
#:
#: `crew.system_prompt_for` は職能文（「TAGS と SCENE は書くな、君の CRAFT slot は
#: LIGHT だ」）のすぐ後ろに classic の `OUTPUT`（SAY / TAGS / SCENE の三ブロック）
#: を足す。条文の中で矛盾していて、**最後に読んだ側が勝つ** —— 実機で衣装の席が
#: TAGS を35語並べて返してきた（`CRAFT:` は一行も無し）。
#:
#: 手帖が正本の studio では、この矛盾は classic 側の別の経路で解けていた。
#: Refine は自分の書式を**いちばん後ろに**足して解く。女優の条文（`REFINE_OUTPUT`）
#: と W撮りの `w_output_block` でもう二度使っている手。
#: **席の出力の形。ここが唯一の形式**（前の形式を打ち消す一行から始まる）。
#:
#: 言語と声の規則は `crew.OUTPUT` にあったが、あちらは classic の三ブロック
#: （SAY/TAGS/SCENE）向けで、席は TAGS も SCENE も書かない。打ち消される側を
#: 毎席 2,812字送っていたので外し、**効いていた二行だけこちらへ引き取った**
#: （2026-09-13）。`crew.OUTPUT` は女優の前置き（一人撮りの正本）で今も現役。
#: **席の口調を保つ段。**（2026-09-13）
#:
#: 総監督「Muse Classic 時代にあった、スタッフ別の口調がなくなって均一化した気が
#: します」。数えたら本当だった —— 席の発言の **46% が「総監督、」で始まり、
#: 42% が同じ4文字で切り出していた**。原因は前日 `crew.OUTPUT` を席から外した
#: とき、その中の SAY の段（魅せる指示）が一緒に落ちたこと。
#:
#: 実測（台・同じ材料・n=2）:
#:
#:     いまの条文    総監督で開く 46%  同じ4字 42%  語の重なり 3.2%  1周 117.5s
#:     この段を戻す   総監督で開く  8%  同じ4字 21%  語の重なり 0.4%  1周 118.3s
#:
#: **時間は変わらない**（+0.7%）。前日の削り（-19%）は損なわない。
SEAT_VOICE = """
YOUR SAY IS ENTERTAINMENT AS MUCH AS CRAFT — captivate the Showrunner.
- Charm first: warmth, playfulness, a little tease, a vivid image in words.
  Make him want to keep reading. Cute is welcome; a bland report is not.
- Open your own way. Do NOT begin the way the last speaker began, and do not
  start with 「総監督」 when the speaker before you already did.
- Still a person with an opinion — react, pile on, then commit.
""".strip()


SEAT_OUTPUT = """
OUTPUT FORMAT — this REPLACES any format above. Two lines, nothing else:

SAY: 1–3 sentences of live table talk in YOUR voice. React to the floor, then
commit ONE concrete thing from your own specialty. No danbooru tags in SAY.
**Never write a TAGS: or SCENE: block — the Scripter owns the shot document.**
- LANGUAGE: these instructions are in English. Speak the session locale —
  by default natural Japanese in your voice (口調どおり). 「総監督」OK.
- Match your VOICE / 口調 / EXAMPLE SAY. Do NOT sound like the other Muses.
  Warmth and a little tease are welcome; a bland report is not. No emoji.

CRAFT: <danbooru tags> | <short prose>
Your slot only. Absolute values — never "darker" / "softer" / "more".
**No field label inside CRAFT.** Not `BEAT:`, not `WEARING:`, not
`ATMOSPHERE:` — the tags alone. The Scripter knows which field is yours.
Omit the whole CRAFT line when your slot should not move this turn.
""".strip()


#: 束ねた回（欄ごとの会議）の出力の形。**一席の `SEAT_OUTPUT` と同じ約束** ——
#: 最後に読んだ形式が勝つので、前置きの末尾に置く。
#:
#: 違いは二つだけ: 席の数だけ `SPEAKER:` + `SAY:` の組を出すことと、
#: **CRAFT は最後に一行だけ**（欄の結論）であること。
GROUP_OUTPUT = """
OUTPUT FORMAT — this REPLACES any format above. Nothing else in the reply:

SPEAKER: <the exact id of speaker 1>
SAY: 1–3 sentences of live table talk in THAT person's voice.
SPEAKER: <the exact id of speaker 2>
SAY: 1–3 sentences — react to speaker 1 by name, then your own craft.
(...one SPEAKER/SAY pair per person at this corner, in the given order)

CRAFT: <danbooru tags> | <short prose>
- LANGUAGE: these instructions are in English. The SAY lines speak the session
  locale — by default natural Japanese, each in that person's 口調. 「総監督」OK.
- **ONE CRAFT line for the whole corner, at the very end.** It is the field's
  WHOLE value as the corner agreed it — not an addition to it, not one line
  per speaker. Absolute values — never "darker" / "softer" / "more".
- **No field label inside CRAFT.** The tags alone.
- Omit the CRAFT line entirely when the field should stay exactly as it reads now.
- Never write a TAGS: or SCENE: block — the Scripter owns the shot document.
""".strip()


#: 班が開いているセッションの印。**総監督が明示的に開けたときだけ立つ。**
TABLE_OPEN = "crew_open"


def has_crew(session: dict[str, Any]) -> bool:
    """Is this a crewed session?

    **Never gate on a default.** `inputs.crew_preset` is filled with `"standard"`
    by `ALL_DEFAULTS`, so gating on "are there seats" would run **sixteen seats
    in a solo shoot**. `mode` is no good either — all 117 live sessions carry
    `duet`, and 85 of them have no partner at all. So the gate is one mark:
    did the Showrunner open the table?

    The 29 Refine sessions that already exist carry no mark, so none is caught up.
    """
    return bool(session.get(TABLE_OPEN)) and bool(cast_of(session))


def cast_of(session: dict[str, Any]) -> list[str]:
    """Seat order for this shoot. One person per role; `resolve_crew` always adds
    the lead and the editor."""
    inputs = dict(session.get("inputs") or {})
    ids = [str(i) for i in (inputs.get("crew_ids") or []) if str(i).strip()]
    preset = str(inputs.get("crew_preset") or "").strip()
    if not ids and not preset:
        return []
    try:
        return list(crew.resolve_crew(preset=preset or None, crew_ids=ids or None))
    except Exception:
        logger.exception("[muse] could not resolve the crew")
        return []


def field_of(muse_id: str) -> str:
    """The ledger field this seat owns (empty for seats that own none)."""
    slot = (getattr(crew, "CRAFT_SLOTS", None) or {}).get(crew.role_of(muse_id) or "")
    return SLOT_FIELD.get(str(slot or ""), "")


def writing_seats(cast: list[str], *, only: tuple[str, ...] = (),
                  without: tuple[str, ...] = ()) -> list[str]:
    """Seats that hold a pen, in seat order. `plan` runs on its own path and
    never appears here."""
    out: list[str] = []
    for mid in cast:
        role = crew.role_of(mid)
        if not role or role == "plan" or role in MUTED or role in without:
            continue
        if only and role not in only:
            continue
        out.append(mid)
    return out


def opening_seats(cast: list[str]) -> list[str]:
    """The three opening seats in **dressing order** (not seat order)."""
    rank = {r: i for i, r in enumerate(OPENING_SEQUENCE)}
    seats = writing_seats(cast, only=OPENING_SEQUENCE)
    return sorted(seats, key=lambda m: rank.get(crew.role_of(m) or "", 99))


#: CRAFT 行の上限。**語の途中では切らない。**（2026-09-18）
CRAFT_MAX = 280


def _clip_craft(clause: str) -> str:
    """Stop an over-long CRAFT line **on a word boundary**.

    This used to be `clause[:280]`. The left half is the tags that reach the
    ledger, so cutting mid-string lands **half a word** — `silver_sug` — in a
    field. Live fields top out at 167 characters so nothing has hit it yet, but
    it is the kind of break nobody would notice once it does, so fix it first.
    """
    text = str(clause or "").strip()
    if len(text) <= CRAFT_MAX:
        return text
    head = text[:CRAFT_MAX]
    for mark in ("|", ",", " "):
        cut = head.rfind(mark)
        if cut > CRAFT_MAX // 2:
            return head[:cut].strip(" ,|")
    return head.strip(" ,|")


def split_craft(body: str) -> tuple[str, str]:
    """Split one seat's reply into its talk and its CRAFT line — classic's
    `_split_craft_line`.

    **The `SAY:` label never reaches the screen (2026-09-12).** The Showrunner:
    "in the studio shoot, SAY: shows up". A seat's reply opens with `SAY: …`, so
    pushing it through as-is leaves the label sitting in the bubble. Stripping it
    is classic's `identity.sanitize_muse_say` — the one piece that already does
    the job of "cut it when a field name leaks".
    """
    text = str(body or "")
    m = _CRAFT_LINE_RE.search(text)
    if not m:
        return identity.sanitize_muse_say(text, locale="ja"), ""
    clause = str(m.group(1) or "").strip()
    say = _CRAFT_LINE_RE.sub("", text)
    if clause.lower() in ("none", "-", "n/a", "omit", "(omit)"):
        clause = ""
    return identity.sanitize_muse_say(say, locale="ja"), _clip_craft(clause)


def banter_mode(session: dict[str, Any]) -> str:
    """off / light / full. Default is light — half the calls in a round are
    banter."""
    mode = str((session.get("inputs") or {}).get("banter_mode") or "light").strip().lower()
    return mode if mode in ("light", "full", "off") else "light"


def _in_role(cast: list[str], role: str) -> str | None:
    return next((m for m in cast if crew.role_of(m) == role), None)


def pick_reactor(session: dict[str, Any], cast: list[str], *,
                 current: str, previous: str | None, index: int) -> str | None:
    """Who heckles after a seat has spoken. Classic's rule, unchanged."""
    mode = banter_mode(session)
    if mode == "off":
        return None
    if mode == "light" and crew.role_of(current) != "actress" and index % 2 == 0:
        return None
    # 主演には固定の取り分を渡す —— 前席が総取りすると、18席の撮影で彼女の
    # 台詞が三行しか残らなかった（classic の実測）。
    lead = _in_role(cast, "actress")
    if lead and lead != current and index % 4 == 1:
        return lead
    if previous and previous != current and previous in cast:
        return previous
    for role in ("hook", "actress", "faces", "spine", "beat"):
        mid = _in_role(cast, role)
        if mid and mid != current:
            return mid
    return None


def pick_heckler(session: dict[str, Any], cast: list[str], *,
                 current: str, reactor: str | None, index: int) -> str | None:
    """A second heckler. Only in `full` — it is expensive on a local Ollama."""
    if banter_mode(session) != "full":
        return None
    if index % 3 != 2:
        return None
    for role in ("actress", "hook", "faces", "cutout", "propshop"):
        mid = _in_role(cast, role)
        if mid and mid not in (current, reactor):
            return mid
    return None


def seat_name(session: dict[str, Any], muse_id: str) -> str:
    """The name shown on screen, as **nickname (role)**. The lead keeps her own
    name. (2026-09-16)

    The Showrunner: "the Muses' conversations get mixed up; the voice is not that
    Muse's". Half the reason was that the name tag carried **the role** only —

        on screen    色彩設計 / 撮影 / 演出        (colour design / camera / staging)
        in the room  「一点さん」「すきま」「一秒くん」  (Itten / Sukima / Ichibyou — nicknames)

    **The names they call each other never appeared on screen.** And two people
    share a role (`palette:itten` and `palette:aku`), so swapping the crew changed
    nothing you could see. Leading with the nickname makes the tag and the
    address the same word.

    Seats, banter, `muse_speaking` and the stored rows all come through here, so
    this is the only place to fix.
    """
    if crew.role_of(muse_id) == "actress":
        char = session.get("character") or {}
        name = str(char.get("name_ja") or char.get("name") or "").strip()
        if name:
            return name
    m = (getattr(crew, "MUSES", None) or {}).get(crew.resolve_member(muse_id)) or {}
    role = str(m.get("name_ja") or m.get("name") or muse_id).strip()
    nick = str(m.get("nick_ja") or m.get("nick") or "").strip()
    if nick and nick != role:
        return f"{nick}（{role}）"
    return role


#: 班が置いた語の控え（`session[CREW_WORDS][欄] = [語, …]`）。（2026-09-14）
#:
#: **誰の語かを覚えておくためだけの帳面。** 欄の会議は「今はこうなっている、
#: どう変えるか」を決めるので、結論は欄の**全体**になる。そのとき総監督の言葉まで
#: 書き換えてしまっては困るので、班は**自分が置いた語だけ**言い直せる、とする。
#: 印の無い古いセッションは全語を総監督のものとして扱う（消えない側に倒す）。
CREW_WORDS = "crew_words"


def crew_words_of(session: dict[str, Any]) -> dict[str, list[str]]:
    """The words the crew placed in that field."""
    raw = session.get(CREW_WORDS) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(t) for t in (v or [])] for k, v in raw.items()}


def field_groups(seats: list[str]) -> list[tuple[str, list[str]]]:
    """Bundle the seats **by field**. Order follows whichever seat sits there
    first. (2026-09-14)

    The Showrunner: "bundle the members who share a ledger field into one session
    so they produce a single ledger as the conclusion — that avoids the
    collisions."

        standard   beat (staging + choreography) / frame (layout + camera) /
                   look (colour + line) plus one seat each for bg, wearing,
                   light, expression and atmosphere
                   → twelve seats become **nine corners**

    A seat with no field (the lead) is never bundled — it comes back as **its own
    corner** (`("", [id])`). Putting the fieldless together would seat people in
    one room who have nothing to say to each other.
    """
    groups: list[tuple[str, list[str]]] = []
    index: dict[str, int] = {}
    for mid in seats:
        field = field_of(mid)
        if not field:
            groups.append(("", [mid]))
            continue
        if field in index:
            groups[index[field]][1].append(mid)
            continue
        index[field] = len(groups)
        groups.append((field, [mid]))
    return groups


def field_header(field: str, *, ledger: dict[str, str],
                 mine: list[str] | None = None) -> str:
    """**Announce what the ledger says now, then ask how it should change.**
    (2026-09-14)

    The Showrunner: "announce what the ledger currently is, then talk about how it
    should change from there."

    Live, `light` had silted up with four restatements of the same backlight —
    `backlighting`, `rim_light`, `hard_rim`, `edge_lighting` — and it owns a
    single seat. A seat could only add each turn; **nothing ever let it look at
    the field as a whole and say it again.** Show it here, and have the
    conclusion written as the value of the entire field.
    """
    have = [t.strip() for t in str((ledger or {}).get(field) or "").split(",") if t.strip()]
    crew_said = {t.lower() for t in (mine or [])}
    theirs = [t for t in have if t.lower() not in crew_said]
    lines = [
        f"FIELD `{field}` — THE LEDGER AS IT STANDS",
        f"  now: {', '.join(have) if have else '(empty)'}",
    ]
    if theirs:
        lines.append(
            "  the Showrunner's own words in it: " + ", ".join(theirs)
            + "  ← these stay, whatever you decide"
        )
    lines.append(
        f"Decide what `{field}` READS after this turn — keep it, drop what is "
        "stale, or replace it. Say out loud what you are changing and why, then "
        "write ONE CRAFT line holding the WHOLE field: the absolute value, at "
        f"most {FIELD_CONCLUSION_MAX} tags, no duplicates, no two tags that "
        "fight each other. Omit CRAFT when it should stay exactly as it reads."
    )
    return "\n".join(lines)


def _shot_bits(session: dict[str, Any]) -> list[str]:
    """Who is in frame and where the ledger stands. A seat and a corner get the
    same thing."""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    partner = session.get("partner_character") or {}
    has_partner = bool(str(partner.get("character_id") or "").strip())
    char = session.get("character") or {}
    return [
        ledger_mod.cast_line(
            partner=has_partner,
            name_a=str(char.get("name_ja") or char.get("name") or ""),
            name_b=str(partner.get("name_ja") or partner.get("name") or ""),
        ),
        "SHOT LEDGER — this is the shot as it stands. Absolute, per field.\n"
        + "\n".join(f"  {k}: {v}" for k, v in
                    ledger_mod.for_model(led, partner=has_partner).items() if v),
    ]


def _floor_bit(floor: list[dict[str, Any]]) -> str:
    """What was just said, carried with the warning **not to borrow the words**.

    In a live opening the camera seat copied the wardrobe seat's first sentence
    outright ("if the late sun comes in, velvet that drinks the light…"). The
    contract already says "Do not restate another Muse's phrase", but since we
    are showing them the last few lines, say it again right here.
    """
    if not floor:
        return ""
    return (
        "THE FLOOR SO FAR — react to it, then add the one thing nobody has "
        "named yet. **Do not reuse their words, images or metaphors.** If "
        "the last speakers already reached for your idea, that idea is "
        "finished; say the part of the picture still missing.\n"
        + "\n".join(f"  {f['name']}: {str(f['say'])[:160]}" for f in floor[-3:])
    )


def group_prompt(session: dict[str, Any], seats: list[str], *, field: str,
                 director_line: str, floor: list[dict[str, Any]]) -> str:
    """The body handed to a field corner. **Announcement first, one conclusion.**
    (2026-09-14)"""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    bits = _shot_bits(session)
    if field:
        bits.append(field_header(
            field, ledger=led, mine=crew_words_of(session).get(field)))
    if (bit := _floor_bit(floor)):
        bits.append(bit)
    bits.append(f"SHOWRUNNER:\n{director_line.strip()}")
    return "\n\n".join(bits)


def seat_prompt(session: dict[str, Any], muse_id: str, *,
                director_line: str, floor: list[dict[str, Any]]) -> str:
    """The body handed to one seat. **The ledger is the document of record**; the
    seat only sharpens its own field."""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    field = field_of(muse_id)
    slot = (getattr(crew, "CRAFT_SLOTS", None) or {}).get(crew.role_of(muse_id) or "")

    bits = _shot_bits(session)
    if slot and field:
        bits.append(
            f"YOUR SLOT: {slot} — it lands in the ledger field `{field}`.\n"
            + field_header(field, ledger=led, mine=crew_words_of(session).get(field))
        )
    if (bit := _floor_bit(floor)):
        bits.append(bit)
    bits.append(f"SHOWRUNNER:\n{director_line.strip()}")
    return "\n\n".join(bits)


def stream_id(session: dict[str, Any], muse_id: str) -> str:
    """Where a stream is addressed. **The lead always streams under her own cast
    id.** (2026-09-18)

    The Showrunner: "sometimes the thumbnail is missing when a Muse speaks".

    The panel decides "are these her words" by asking whether **`muse_id` is her
    `character_id`** (`liveIsLead` in `MusePanel.vue`). Inside the crew she was
    streaming under the seat id `actress:cast`, so **her face vanished on exactly
    the turns where she heckled** — same person speaking, different name tag
    depending on where the words came from.

    Line them up on her own id here. Her seat, her banter and her own turn all
    become one person as far as the screen is concerned.
    """
    if crew.role_of(muse_id) == "actress":
        cid = str((session.get("character") or {}).get("character_id") or "").strip()
        if cid:
            return cid
    return muse_id


def _stream_to(session: dict[str, Any], muse_id: str):
    """The outlet a seat's line streams through. **Only what is inside `SAY:`**
    gets past (`_say_only`).

    The Showrunner: "without a streaming display the wait feels very long". With
    eighteen seats speaking in turn, silence means more than a minute of nothing
    happening on screen. This is the same device the lead's turn already uses,
    handed to the seats.
    """
    try:
        from . import shared as muse_shared

        return muse_shared._token_publisher(
            str(session.get("session_id") or ""), stream_id(session, muse_id),
        )
    except Exception:
        logger.debug("[muse] seat token publisher unavailable", exc_info=True)
        return None


def _watch_the_window(session: dict[str, Any], who: str):
    """A hook that records a turn cut off by the window. **Never let it pass as a
    short reply.** (2026-09-18)

    The Showrunner: "it looks like text gets cut off by prompt overflow". The
    window (`num_ctx`) covers the preamble and the output together, so a turn with
    a long preamble stops mid-sentence. Ollama says so with
    `done_reason: "length"` — keep that in `/debug` so it **can be counted
    afterwards**.
    """
    def _note(done: dict[str, Any]) -> None:
        if str(done.get("reason") or "") != "length":
            return
        debug_mod.note(
            session, "cut_by_the_window",
            detail=f"{who}: 前置き {done.get('prompt_tokens')}tok "
                   f"＋ 出力 {done.get('eval_tokens')}tok で枠に当たった",
        )
        logger.warning("[muse] %s was cut by the window: %s", who, done)
    return _note


async def _seat_turn(ollama, session: dict[str, Any], muse_id: str, *,
                     model: str, prompt: str) -> str:
    """One seat's call. **No picture is attached** — showing the board belongs to
    the lead's turn."""
    sid = str(session.get("session_id") or "")
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": stream_id(session, muse_id),
        "name": seat_name(session, muse_id),
    })
    return await chain._call(
        ollama,
        system=crew.system_prompt_for(
            muse_id, character=session.get("character") or {},
            base_style=str((session.get("inputs") or {}).get("look") or ""),
            seed=sid,
        ) + "\n\n" + SEAT_VOICE + "\n\n" + SEAT_OUTPUT,
        prompt=prompt,
        model=model,
        images=None,
        num_ctx=refine_num_ctx(session),
        think=False,
        on_token=_stream_to(session, muse_id),
        on_done=_watch_the_window(session, f"席 {crew.role_of(muse_id) or muse_id}"),
    )


_SPEAKER_RE = re.compile(r"(?im)^[ \t>*_#-]*SPEAKER\s*:\s*(.+?)\s*$")

#: 行頭に付く飾り。模型は `**SPEAKER: …**` や `- SPEAKER:` と書くことがある。
_DECOR = " \t*_#>-"


def _match_speaker(token: str, seats: list[str],
                   used: list[str]) -> tuple[str, bool]:
    """Match whatever follows `SPEAKER: …` to a seat at this corner.
    Returns `(seat, matched)`.

    The model writes the id verbatim, or a number (`SPEAKER: 2`), or a nickname,
    or a role name — `SPEAKER: 一点` (the nickname Itten) or `SPEAKER: 色彩設計`
    (the role, colour design). So **the Japanese name tags are matched too**.

    **When nothing matches, fall through to the first seat that has not spoken** —
    a corner speaks in seat order, so that lands right nearly always. Forget to
    pass `used` and everyone piles onto the first speaker, which is why the match
    result comes back for the caller to record (the 2026-09-16 mis-addressed
    stream was exactly this being called with an empty list).
    """
    raw = str(token or "").strip().strip("`*_ 「」【】")
    low = raw.lower()
    # **区切りの揺れを均す（2026-09-18）。** 実機で模型が `cut-out:sukima` と
    # 書き、id（`cutout:sukima`）に当たらず「まだ喋っていない席の先頭」へ
    # 落ちた —— たまたま正解だったが、席順の運に預けている形だった。
    flat = re.sub(r"[\s_\-]+", "", low)
    for mid in seats:
        mid_low = mid.lower()
        mid_flat = re.sub(r"[\s_\-]+", "", mid_low)
        if low in (mid_low, mid_low.split(":")[0]):
            return mid, True
        if flat in (mid_flat, mid_flat.split(":")[0]):
            return mid, True
    if (digits := re.findall(r"\d+", low)):
        i = int(digits[0]) - 1
        if 0 <= i < len(seats):
            return seats[i], True
    for mid in seats:
        member = (getattr(crew, "MUSES", None) or {}).get(crew.resolve_member(mid)) or {}
        labels = [crew.role_of(mid), member.get("nick"), member.get("nick_ja"),
                  member.get("name_ja"), member.get("name")]
        for label in labels:
            text = str(label or "").strip().lower()
            if text and text in low:
                return mid, True
    for mid in seats:
        if mid not in used:
            return mid, False
    return (seats[0] if seats else ""), False


def split_packed(raw: str, seats: list[str]) -> tuple[list[tuple[str, str]], str]:
    """Split a bundled reply into **one line of talk per seat** and **one
    conclusion for the field**. (2026-09-14)

    The last `CRAFT` line wins — the contract asks for one, but when the model
    writes one per speaker, **the closing line is the corner's conclusion**, so
    trust that one.
    """
    text = str(raw or "")
    crafts = list(_CRAFT_LINE_RE.finditer(text))
    craft = str(crafts[-1].group(1) or "").strip() if crafts else ""
    body = _CRAFT_LINE_RE.sub("", text)

    parts = _SPEAKER_RE.split(body)
    rows: list[tuple[str, str]] = []
    if len(parts) >= 3:
        used: list[str] = []
        for i in range(1, len(parts) - 1, 2):
            mid, _hit = _match_speaker(parts[i], seats, used)
            say = identity.sanitize_muse_say(parts[i + 1], locale="ja")
            if not say.strip():
                continue
            used.append(mid)
            rows.append((mid, say))
    else:
        # **形式を守らなかった回も落とさない。** 丸ごと先頭の席の発言にする。
        say = identity.sanitize_muse_say(body, locale="ja")
        if say.strip() and seats:
            rows = [(seats[0], say)]
    return rows, craft


def _packed_stream(session: dict[str, Any], seats: list[str]):
    """Stream a bundled reply **while routing it to the bubble of whoever is
    speaking**. (2026-09-14)

    When seats were called one at a time, `_stream_to` only ever needed one
    address. A corner carries several people in a single reply, so the address is
    switched on each `SPEAKER:` line. **Without this, a bundled turn is the one
    turn where the screen goes silent** (the Showrunner: "the wait feels very
    long").

    Only the first few characters of a line are held back — the same move
    `shared._say_only` uses to hide field names. Nothing is held mid-line;
    holding there freezes the screen until a sentence is finished.
    """
    sid = str(session.get("session_id") or "")
    pubs = {mid: _stream_to(session, mid) for mid in seats}
    word = "speaker:"
    st: dict[str, Any] = {"gate": None, "bol": True, "hold": "", "id": None,
                          "used": []}

    def _emit(text: str) -> None:
        gate = st["gate"]
        if gate is None or not text:
            return
        try:
            gate(text)
        except Exception:
            logger.debug("[muse] packed stream emit failed", exc_info=True)

    def _switch(token: str) -> None:
        # **`used` を持って渡す（2026-09-16）。** 空で呼んでいたので、名前が
        # 当たらないと毎回 `seats[0]` に落ち、**二人目の言葉が一人目の吹き出しに
        # 積まれていた**（総監督「Muse同士の会話が混ざる」）。
        mid, hit = _match_speaker(token, seats, st["used"])
        if mid:
            st["used"].append(mid)
            st["gate"] = pubs.get(mid)
            events.publish(sid, {
                "type": "muse_speaking", "muse_id": stream_id(session, mid),
                "name": seat_name(session, mid),
            })
        if not hit:
            # 黙って間違えない —— 当たらなかった名札は記録に残す。
            debug_mod.note(session, "corner_speaker_miss",
                           detail=f"{token.strip()[:40]!r} → {mid}")

    def _feed(text: str) -> None:
        for ch in str(text or ""):
            if st["id"] is not None:          # `SPEAKER: …` の行を読んでいる
                if ch == "\n":
                    _switch(st["id"])
                    st["id"] = None
                    st["bol"] = True
                else:
                    st["id"] += ch
                continue
            if st["bol"]:
                cand = st["hold"] + ch
                # **飾りを許す（2026-09-16）。** `**SPEAKER: …**` と書かれると
                # 行頭が `*` で始まるので欄名に育たず、ラベルごと前の席の吹き出しへ
                # 流れていた。`_SAY_OPEN_RE` が昔から許しているのと同じ飾り。
                bare = cand.strip(_DECOR).lower()
                if (not bare and len(cand) <= len(_DECOR)) or (bare and word.startswith(bare)):
                    st["hold"] = cand
                    if bare == word:
                        st["hold"], st["id"] = "", ""
                    continue
                _emit(st["hold"])
                st["hold"] = ""
                st["bol"] = False
            _emit(ch)
            if ch == "\n":
                st["bol"] = True
                st["hold"] = ""

    return _feed


async def _group_turn(ollama, session: dict[str, Any], seats: list[str], *,
                      field: str, model: str, prompt: str) -> str:
    """Call a whole field corner at once. **No picture is attached** — showing the
    board belongs to the lead's turn."""
    sid = str(session.get("session_id") or "")
    if seats:
        events.publish(sid, {
            "type": "muse_speaking", "muse_id": stream_id(session, seats[0]),
            "name": seat_name(session, seats[0]),
        })
    inputs = session.get("inputs") or {}
    return await chain._call(
        ollama,
        system=crew.field_table_prompt(
            seats, field=field,
            base_style=str(inputs.get("look") or ""),
            locale=str(inputs.get("locale") or "ja"),
            preset_id=str(inputs.get("crew_preset") or ""),
            seed=sid,
        ) + "\n\n" + SEAT_VOICE + "\n\n" + GROUP_OUTPUT,
        prompt=prompt,
        model=model,
        images=None,
        num_ctx=refine_num_ctx(session),
        think=False,
        on_token=_packed_stream(session, seats),
        on_done=_watch_the_window(session, f"会議 {field}"),
    )


async def _banter_turn(ollama, session: dict[str, Any], muse_id: str, *,
                       model: str, about_name: str, about_text: str) -> str:
    """One heckle. Short, and never allowed to write craft."""
    events.publish(str(session.get("session_id") or ""), {
        "type": "muse_speaking", "muse_id": stream_id(session, muse_id),
        "name": seat_name(session, muse_id),
    })
    return await chain.run_banter(
        ollama, muse_id=muse_id,
        user_prompt=(
            f"{about_name} がいま言ったこと:\n{about_text[:240]}\n\n"
            "一言だけ返して。茶々でも、乗っかるでも、軽く押し返すでもいい。"
            "**タグも CRAFT も書かない。** 一文か二文。"
        ),
        model=model,
        num_ctx=refine_num_ctx(session),
        character=session.get("character") or {},
        on_token=_stream_to(session, muse_id),
    )


async def run_table(db, ollama, session: dict[str, Any], *,
                    director_line: str, opening: bool = False) -> list[dict[str, Any]]:
    """Walk the seats in order. **Never writes the ledger** — what it gathers goes
    to the writer.

    Same shape as classic's `_craft_pass`:

        a seat speaks → pick a reactor → they speak if there is one →
        pick a heckler → they speak if there is one

    Returns `{muse_id, role, name, field, say, craft}` per seat. The caller
    (`service.chat`) puts SAY into the conversation and hands `craft` to the
    writer as material.
    """
    cast = cast_of(session)
    if not cast:
        return []
    # **会話のターンに主演の席は置かない（2026-09-16）。** 総監督「会話が Muse と
    # 班で混ざる」。実機の1ターンで彼女は **席 → やじ → 本人の台詞 → 内心 → 確認**
    # と4〜5回出ていた。席としての彼女は**欄を持たない**ので台帳には何も書かず、
    # それでいて `seat_actress` は一周でいちばん重い（24〜28秒）。
    #
    # **開幕には残す** —— 衣装 → 撮影 → 主演で当たりを付ける段は彼女の仕事。
    # やじ役・横やり役としての出番もそのまま（`pick_reactor` が取り分を渡す）。
    seats = (opening_seats(cast) if opening
             else writing_seats(cast, without=("actress",)))
    if not seats:
        return []
    model = str((session.get("inputs") or {}).get("model") or "")
    floor: list[dict[str, Any]] = []
    previous: str | None = None

    for index, (field, group) in enumerate(field_groups(seats)):
        t0 = time.monotonic()
        rows: list[tuple[str, str]] = []
        craft = ""
        if len(group) == 1:
            muse_id = group[0]
            try:
                raw = await _seat_turn(
                    ollama, session, muse_id, model=model,
                    prompt=seat_prompt(
                        session, muse_id, director_line=director_line, floor=floor,
                    ),
                )
            except Exception:
                # **一席が黙っても撮影は続く。** 班は18人居るので、一人の失敗で
                # ターンごと落とす理由がない。記録だけ残して次の席へ。
                logger.warning("[muse] seat %s said nothing", muse_id, exc_info=True)
                debug_mod.note(session, "seat_failed", detail=muse_id)
                continue
            debug_mod.stage(session, f"seat_{crew.role_of(muse_id) or muse_id}", t0)
            say, craft = split_craft(raw)
            rows = [(muse_id, say)]
        else:
            try:
                raw = await _group_turn(
                    ollama, session, group, field=field, model=model,
                    prompt=group_prompt(
                        session, group, field=field,
                        director_line=director_line, floor=floor,
                    ),
                )
            except Exception:
                logger.warning("[muse] the `%s` corner said nothing", field,
                               exc_info=True)
                debug_mod.note(session, "seat_failed", detail=f"{field}: {group}")
                continue
            debug_mod.stage(session, f"corner_{field}", t0)
            rows, craft = split_packed(raw, group)
            if len(rows) < len(group):
                # **黙って人数が減ったことにしない。** 形式を守らなかった回は
                # 台詞が畳まれるので、記録に残して後から数えられるようにする。
                debug_mod.note(
                    session, "corner_thin",
                    detail=f"{field}: {len(rows)}/{len(group)}席",
                )

        # 発言を積む。**欄の結論は一つ**なので、craft は閉めの一人に付ける。
        spoke: list[tuple[str, str]] = []
        for i, (muse_id, say) in enumerate(rows):
            last = i == len(rows) - 1
            mine = craft if last else ""
            if not str(say).strip() and not mine.strip():
                continue
            floor.append({
                "muse_id": muse_id,
                "role": crew.role_of(muse_id) or "",
                "name": seat_name(session, muse_id),
                "field": field,
                "say": say,
                "craft": mine,
                "kind": "seat",
            })
            spoke.append((muse_id, say))
        if not spoke:
            continue

        # やじは**会議ごとに一度**（束ねた中では席どうしが既に react している）。
        closer, closing_say = spoke[-1]
        before, previous = previous, closer
        if not str(closing_say).strip():
            continue
        name = seat_name(session, closer)
        reactor = pick_reactor(
            session, cast, current=closer, previous=before, index=index,
        )
        heckler = pick_heckler(
            session, cast, current=closer, reactor=reactor, index=index,
        )
        for who in (reactor, heckler):
            if not who or who in {m for m, _ in spoke}:
                continue
            try:
                heckle = await _banter_turn(
                    ollama, session, who, model=model,
                    about_name=name, about_text=closing_say,
                )
            except Exception:
                logger.debug("[muse] banter skipped for %s", who, exc_info=True)
                continue
            if str(heckle or "").strip():
                floor.append({
                    "muse_id": who,
                    "role": crew.role_of(who) or "",
                    "name": seat_name(session, who),
                    "field": "",
                    "say": str(heckle).strip(),
                    "craft": "",
                    "kind": "heckle",
                })

    return floor


def craft_tags(craft: str) -> str:
    """**Only the tag half** of `CRAFT: <tags> | <prose>`. (2026-09-11)

    Classic's CRAFT has two halves — danbooru words on the left, prose on the
    right. A ledger field is **one absolute English phrase**, so passing the right
    half through put a pipe and Japanese into the field, and the halves bled
    across fields (live, `light` ended up holding `translucent_fabric | 襟が夕陽を
    透かす` — "the collar lets the evening sun through", prose that belongs
    nowhere near a ledger field).

    The prose is not thrown away — it shows in the conversation as the seat's SAY,
    and the picture's prose is rebuilt from the ledger by `assemble.scene_prose`.
    What is wanted here is **material for the ledger** only.
    """
    left = str(craft or "").split("|", 1)[0]
    # **手帖の欄名が頭に付いてくる（2026-09-11 実測）。** 席の職能文は
    # `BEAT` `WEARING` `ATMOSPHERE` といった手帖のラベルを名指しで説明して
    # いるので、模型がそれを CRAFT の頭に写す:
    #
    #     CRAFT: BEAT: standing still, eyes towards the light | …
    #     CRAFT: ATMOSPHERE: | …        ← 中身が無いことすらある
    #
    # 台帳に `wearing: "BEAT: standing still…"` と `bg: "ATMOSPHERE:"` が
    # 着いた。条文でも禁じたが、**届く手前でも落とす** —— 模型の行儀に
    # 台帳の綺麗さを預けない。
    # 剥がし方は台帳と同じ一本（`ledger.strip_field_label`）。二つ持つと必ずずれる。
    left = ledger_mod.strip_field_label(left)
    return " ".join(left.split()).strip(" ,")


#: **班が作り直してよい欄。**（2026-09-14）
#:
#: 総監督「美術や色彩などでいい提案しているのに、それらがプロンプトに乗ってこない
#: のはやっぱりもったいない」。席は台帳に触れず、材料を台本係へ渡す。その台本係は
#: 監督の一行にある欄しか直さないので、**名指しされなかった欄の craft は構造的に
#: どこにも着地しない**（台帳25本で `look` 88%・`atmosphere` 76% が空のまま）。
#:
#: **姿勢・表情・服は入れない。** あちらは一つの体の掃除（`ledger.one_body`）が
#: 効いている場所で、班の言葉は `craft_block` 経由で台本係に渡る。
CREW_FIELDS: tuple[str, ...] = ("bg", "light", "frame", "atmosphere", "look")

#: 会議が出せる結論の語数と、欄ぜんぶの上限。
#: **結論は欄の全体**なので、班の語は毎ターン置き換わる（増え続けない）。
FIELD_CONCLUSION_MAX = 6
FIELD_CAP = 12


def _stems(tag: str) -> set[str]:
    """Split into words and level the endings, so `lighting` and `light` read as
    the same thing.

    Live (`e805ffac`, 2026-09-15) `light` came out as
    `rim_lighting, backlighting, eye_glint, rim_light` — the writer's
    `rim_lighting` and the corner's `rim_light` match neither on word boundaries
    nor on shared words. Dropping the ending (`-ing` / `-ed` / `-s`) makes them
    meet. **Do not over-trim**: only when four or more characters remain.
    """
    out: set[str] = set()
    for word in re.split(r"[\s_\-]+", str(tag or "").lower()):
        if not word:
            continue
        for tail in ("ing", "ed", "s"):
            if word.endswith(tail) and len(word) - len(tail) >= 4:
                word = word[: -len(tail)]
                break
        out.add(word)
    return out


def _too_close(tag: str, other: str) -> bool:
    """**Is this the same thing said twice?** (2026-09-14)

    `talk.word_hit` looks at word boundaries, which is why `shirt` never matches
    `skirt`. Live, duplicates still slipped through that net:

        silver_spoon / silver_sugar_spoon      bg
        air_between_limbs / air_between_elbows frame

    Both **share two or more words**. So the net gets one notch finer — two or
    more shared words, or one word set wholly inside the other, counts as saying
    the same thing again (endings levelled by `_stems`). Genuinely **different**
    things like `amber_theme` and `magenta_theme` share only `theme`, so they
    pass — settling those is the corner's job, which is told not to put opposite
    colours side by side.
    """
    from . import talk

    if talk.word_hit(tag, other) or talk.word_hit(other, tag):
        return True
    a, b = _stems(tag), _stems(other)
    if not a or not b:
        return False
    return len(a & b) >= 2 or a <= b or b <= a


def field_land(
    session: dict[str, Any],
    floor: list[dict[str, Any]],
    *,
    ledger: dict[str, str],
    taken: set[str] | frozenset[str] | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Land a corner's conclusion on the ledger. **No model is called.**
    (2026-09-14)

    Returns `(patch, the crew's words)`. `patch` holds the **absolute value** per
    field, which the caller pushes through the ledger's door
    (`ledger.scrub_patch`). The crew's words go straight into `crew_words` and
    become **the material for next turn's announcement of "this much is yours"**.

    The rules:

        a field the director wrote   left alone — the director's words win
        the Showrunner's words       always kept (what is there minus the crew's)
        the crew's words             **replaced** by this turn's conclusion (up to 6)
        either way                   the field stops at 12 words / refused words are
                                     dropped / nothing is said twice

    **Only what the crew placed can be removed.** The crew cannot touch what the
    Showrunner wrote, so a restatement never pushes his words out.
    """
    from . import talk

    skip = set(taken or ())
    cur = {**ledger_mod.blank(), **(ledger or {})}
    said = crew_words_of(session)

    # 欄ごとの結論を集める。**一欄一つ**だが、形式が崩れた回のために席順で畳む。
    agreed: dict[str, list[str]] = {}
    for row in floor:
        field = str(row.get("field") or "")
        if field not in CREW_FIELDS or field in skip:
            continue
        for tag in (t.strip() for t in craft_tags(row.get("craft") or "").split(",")):
            if tag and tag not in agreed.setdefault(field, []):
                agreed[field].append(tag)

    patch: dict[str, str] = {}
    landed: dict[str, list[str]] = {}
    for field, tags in agreed.items():
        have = [t.strip() for t in str(cur.get(field) or "").split(",") if t.strip()]
        mine = {t.lower() for t in said.get(field, [])}
        keep = [t for t in have if t.lower() not in mine]      # 総監督の言葉
        fresh: list[str] = []
        for tag in talk.filter_banned_tags(session, tags, ledger=cur):
            if len(fresh) >= FIELD_CONCLUSION_MAX or len(keep) + len(fresh) >= FIELD_CAP:
                break
            # **同じものを二度言わない**（語の境目＋語の重なりで見る）。
            if any(_too_close(tag, t) for t in keep + fresh):
                continue
            fresh.append(tag)
        value = ", ".join(keep + fresh)
        # 空にはしない（欄を消すのは班の仕事ではない）。変わらないなら黙っている。
        if not value or value == ", ".join(have):
            continue
        patch[field] = value
        landed[field] = fresh
    return patch, landed


def craft_block(floor: list[dict[str, Any]]) -> str:
    """Gather the seats' CRAFT into something the writer can read.

    **Gathered by field.** Several seats look at one field (staging and
    choreography are both the body), so interleaving them leaves the writer
    unsure which to take — the same rut as the picture's ordering (2026-09-10,
    where reading order fought with who stood where).
    """
    by_field: dict[str, list[str]] = {}
    for row in floor:
        field = str(row.get("field") or "")
        tags = craft_tags(row.get("craft") or "")
        if not field or not tags:
            continue
        by_field.setdefault(field, []).append(tags)
    if not by_field:
        return ""
    lines = [
        "THE CREW SPOKE. Each line is the seat that owns that field, saying how "
        "its craft should READ. **They shape under the key; they do not replace "
        "it.** Keep what the director already put in the field and fold the "
        "seat's detail in beside it — a wardrobe note about fabric never "
        "removes the garment, a gaffer note never removes the director's hour. "
        # **積み上げではない（2026-09-12）。** 同じ欄を複数の席が見るので、
        # 「横に足せ」だけだと言い換えと矛盾が積もる（実機で beat が 13語に
        # なり、体重が二箇所・腰が二方向になった）。足せるのは**まだ言って
        # いないこと**だけ、と明示する。
        "FOLD IN ONLY WHAT IS NOT THERE YET: when a seat says in other words "
        "something the field already says, or says the opposite of it, the "
        "field keeps what it has. Never let the same part of the body, or the "
        "same object in her hands, appear twice in one field.",
    ]
    for field in ledger_mod.LEDGER_KEYS:
        if field in by_field:
            # 一欄一行。席の名前も落とす —— 誰が言ったかは会話欄に出ている。
            # ここに書くと、名前まで欄に写す（実機で踏んだ）。
            seen: list[str] = []
            for tags in by_field[field]:
                for t in (x.strip() for x in tags.split(",")):
                    if t and t.lower() not in {s.lower() for s in seen}:
                        seen.append(t)
            lines.append(f"  {field}: {', '.join(seen)}")
    return "\n".join(lines)

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

#: A seat's CRAFT slot -> a ledger field. `crew.CRAFT_SLOTS` holds seat -> slot, so
#: this is only the slot -> field step. **Several seats may look at one field**
#: (staging and choreography are both the body; layout and camera are both the
#: composition) — classic was the same.
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

#: The three opening seats and their order — the same as classic's `OPENING_ROLES` /
#: `OPENING_SEQUENCE`. **Wardrobe dresses her first, the camera frames what she is
#: wearing, and the lead performs last** — with nobody owning the clothes at the
#: opening, whichever seat writes into the empty ledger first writes the clothes too
#: (measured in classic).
OPENING_SEQUENCE: tuple[str, ...] = ("wardrobe", "lens", "actress")

#: The seats that hold no pen on a notebook turn — the same as classic's
#: `NOTE_MUTED`. The auditing and density work moved to another stage once the
#: notebook became the record of truth.
MUTED: frozenset[str] = frozenset(
    getattr(crew, "NOTE_MUTED", None) or getattr(crew, "BANTER_ONLY", ())
)

_CRAFT_LINE_RE = re.compile(r"(?im)^CRAFT\s*:\s*(.+?)\s*$")

#: The seat's output format. **It overrides the tail of classic's contract.**
#: (2026-09-11)
#:
#: `crew.system_prompt_for` adds classic's `OUTPUT` (the three blocks SAY / TAGS /
#: SCENE) directly after the job description ("do not write TAGS or SCENE; your
#: CRAFT slot is LIGHT"). The contract contradicts itself, and **whichever was read
#: last wins** — live, the wardrobe seat came back with 35 words of TAGS and not one
#: line of `CRAFT:`.
#:
#: In the studio where the notebook is the record of truth, this contradiction was
#: resolved by another road on classic's side. Refine resolves it by adding its own
#: format **right at the end** — a trick already used twice, in the actress's
#: contract (`REFINE_OUTPUT`) and in the duet's `w_output_block`.
#: **The shape of a seat's output. This is the one format** (it opens with a line
#: cancelling any format above).
#:
#: The rules for language and voice lived in `crew.OUTPUT`, which is written for
#: classic's three blocks (SAY/TAGS/SCENE) while a seat writes neither TAGS nor
#: SCENE. The cancelled side was being sent to every seat at 2,812 characters, so it
#: was removed and **only the two lines that were biting were taken over here**
#: (2026-09-13). `crew.OUTPUT` is still in service in the actress's preamble (the
#: record of truth for a solo shoot).
#: **The block that keeps each seat's voice.** (2026-09-13)
#:
#: The Showrunner: "the per-crew way of speaking that Muse Classic had seems to have
#: gone and flattened out". Counted, it was true — **46% of the seats' lines opened
#: with 「総監督、」 ("Showrunner,") and 42% began with the same four characters**.
#: The cause was that removing `crew.OUTPUT` from the seats the day before took its
#: SAY block (the instruction to make it engaging) with it.
#:
#: Measured (the rig, same material, n=2):
#:
#:     as it was      opens on "Showrunner" 46%  same 4 chars 42%
#:                    word overlap 3.2%  one round 117.5s
#:     block restored opens on "Showrunner"  8%  same 4 chars 21%
#:                    word overlap 0.4%  one round 118.3s
#:
#: **The time does not change** (+0.7%). The previous day's cut (-19%) is not
#: undone.
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


#: The output shape for a bundled turn (a field corner). **The same promise as a
#: single seat's `SEAT_OUTPUT`** — the format read last wins, so it goes at the end
#: of the preamble.
#:
#: There are only two differences: one `SPEAKER:` + `SAY:` pair per seat, and
#: **CRAFT only once, at the end** (the field's conclusion).
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


#: The mark on a session that has a crew open. **It is raised only when the
#: Showrunner opened it explicitly.**
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


#: The cap on a CRAFT line. **Never cut mid-word.** (2026-09-18)
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
    # The lead gets a fixed share — with the seats before her taking everything,
    # an 18-seat shoot left her only three lines (measured in classic).
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


#: A record of the words the crew placed (`session[CREW_WORDS][field] = [word, …]`).
#: (2026-09-14)
#:
#: **A ledger kept only to remember whose words are whose.** A field corner decides
#: "this is how it stands; how shall we change it", so its conclusion is **the whole
#: field**. Since rewriting the Showrunner's words in the process would be wrong,
#: the crew may restate **only the words it placed itself**. An older session with
#: no record has all its words treated as the Showrunner's (erring toward not
#: erasing).
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

#: Decoration at the start of a line. The model sometimes writes
#: `**SPEAKER: …**` or `- SPEAKER:`.
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
    # **Normalise the separators (2026-09-18).** Live, the model wrote
    # `cut-out:sukima`, which did not match the id (`cutout:sukima`) and fell
    # through to "the first seat that has not spoken yet" — right by luck, but a
    # shape that trusts the seating order.
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
        # **A turn that broke the format is not dropped either.** The whole thing
        # becomes the first seat's line.
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
        # **Pass `used` through (2026-09-16).** It was called empty, so a name
        # that did not match fell to `seats[0]` every time and **the second
        # person's words piled into the first person's bubble** (the Showrunner:
        # "the Muses' conversations get mixed up").
        mid, hit = _match_speaker(token, seats, st["used"])
        if mid:
            st["used"].append(mid)
            st["gate"] = pubs.get(mid)
            events.publish(sid, {
                "type": "muse_speaking", "muse_id": stream_id(session, mid),
                "name": seat_name(session, mid),
            })
        if not hit:
            # Never wrong silently — a name tag that did not match is recorded.
            debug_mod.note(session, "corner_speaker_miss",
                           detail=f"{token.strip()[:40]!r} → {mid}")

    def _feed(text: str) -> None:
        for ch in str(text or ""):
            if st["id"] is not None:          # reading a `SPEAKER: …` line
                if ch == "\n":
                    _switch(st["id"])
                    st["id"] = None
                    st["bol"] = True
                else:
                    st["id"] += ch
                continue
            if st["bol"]:
                cand = st["hold"] + ch
                # **Allow the decoration (2026-09-16).** Written as
                # `**SPEAKER: …**` the line starts with `*`, so it never grew into a
                # field name and flowed, label and all, into the previous seat's
                # bubble. The same decoration `_SAY_OPEN_RE` has always allowed.
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
    # **The lead has no seat on a conversation turn (2026-09-16).** The Showrunner:
    # "the conversation gets mixed up between Muse and the crew". In one live turn
    # she appeared four or five times — **seat -> banter -> her own line -> mutter ->
    # confirmation**. As a seat she **owns no field**, so she writes nothing into the
    # ledger, and `seat_actress` is still the heaviest stop on the round (24-28
    # seconds).
    #
    # **She stays in the opening** — the wardrobe -> camera -> lead pass that sets
    # the first marks is her job. Her turns as the one who reacts or cuts in are
    # unchanged too (`pick_reactor` hands her a share).
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
                # **The shoot goes on when one seat falls silent.** There are 18
                # on the crew, so there is no reason for one failure to drop the
                # whole turn. Record it and move to the next seat.
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
                # **The headcount does not silently shrink.** On a turn that broke
                # the format the lines get folded together, so it is recorded and
                # can be counted afterwards.
                debug_mod.note(
                    session, "corner_thin",
                    detail=f"{field}: {len(rows)}/{len(group)}席",
                )

        # Stack the lines. **A field has one conclusion**, so the craft goes on
        # whoever closes.
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

        # Banter happens **once per corner** (inside a bundle the seats are already
        # reacting to each other).
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
    # **The notebook's field name comes attached to the front (measured
    # 2026-09-11).** A seat's job description explains the notebook's labels by name
    # — `BEAT`, `WEARING`, `ATMOSPHERE` — so the model copies one onto the front of
    # CRAFT:
    #
    #     CRAFT: BEAT: standing still, eyes towards the light | …
    #     CRAFT: ATMOSPHERE: | …        <- sometimes with no content at all
    #
    # The ledger ended up with `wearing: "BEAT: standing still…"` and
    # `bg: "ATMOSPHERE:"`. The contract forbids it as well, and **it is dropped
    # before arrival too** — the tidiness of the ledger is not entrusted to the
    # model's manners.
    # The stripping is the ledger's own one function (`ledger.strip_field_label`).
    # Keep two and they will drift apart.
    left = ledger_mod.strip_field_label(left)
    return " ".join(left.split()).strip(" ,")


#: **The fields the crew may rebuild.** (2026-09-14)
#:
#: The Showrunner: "the art and colour seats make good proposals and it really is a
#: waste that they never reach the prompt". The seats do not touch the ledger; they
#: hand material to the writer. That writer fixes only the fields named in the
#: director's line, so **the craft for any unnamed field structurally lands nowhere**
#: (across 25 ledgers, `look` was empty in 88% and `atmosphere` in 76%).
#:
#: **Pose, expression and clothes are not included.** Those are where the one-body
#: cleanup (`ledger.one_body`) is at work, and the crew's words reach the writer
#: through `craft_block`.
CREW_FIELDS: tuple[str, ...] = ("bg", "light", "frame", "atmosphere", "look")

#: How many words a corner's conclusion may hold, and the cap across all fields.
#: **A conclusion is the whole field**, so the crew's words are replaced each turn
#: rather than growing.
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

    # Collect each field's conclusion. **One per field**, folded in seat order for
    # turns where the format broke.
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
        keep = [t for t in have if t.lower() not in mine]      # the Showrunner's words
        fresh: list[str] = []
        for tag in talk.filter_banned_tags(session, tags, ledger=cur):
            if len(fresh) >= FIELD_CONCLUSION_MAX or len(keep) + len(fresh) >= FIELD_CAP:
                break
            # **Nothing is said twice** (matched on word boundaries plus word
            # overlap).
            if any(_too_close(tag, t) for t in keep + fresh):
                continue
            fresh.append(tag)
        value = ", ".join(keep + fresh)
        # Never emptied (erasing a field is not the crew's job). Unchanged means
        # nothing is said.
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
        # **Not an accumulation (2026-09-12).** Several seats look at the same
        # field, so "just append" piles up paraphrases and contradictions (live,
        # `beat` reached 13 words with the weight in two places and the hips facing
        # two ways). It states explicitly that only **what has not been said yet**
        # may be added.
        "FOLD IN ONLY WHAT IS NOT THERE YET: when a seat says in other words "
        "something the field already says, or says the opposite of it, the "
        "field keeps what it has. Never let the same part of the body, or the "
        "same object in her hands, appear twice in one field.",
    ]
    for field in ledger_mod.LEDGER_KEYS:
        if field in by_field:
            # One line per field. The seat's name is dropped too — who said it is
            # visible in the conversation. Written here, the name gets copied into
            # the field as well (hit live).
            seen: list[str] = []
            for tags in by_field[field]:
                for t in (x.strip() for x in tags.split(",")):
                    if t and t.lower() not in {s.lower() for s in seen}:
                        seen.append(t)
            lines.append(f"  {field}: {', '.join(seen)}")
    return "\n".join(lines)

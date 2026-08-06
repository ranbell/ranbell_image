"""The Muse crew — five seats, each with somewhere of its own to write.

There used to be seventeen jobs, two people per job, taste axes that averaged
into a house style, and every one of them rewrote the entire prompt on its turn.
That shape lost. A measured run put eighteen seats on a thin theme and the board
came back at mean luminance 14 of 255 with 66% of the frame pure black — nobody
had said "darker", they had each added an independently dark absolute, and the
one seat that owned exposure was instructed to *forbid* flat light while every
other seat was forbidden from touching it. A one-way valve.

The fix is not better wording. It is fewer seats, and giving each one a named
slot in a shot sheet instead of the whole paragraph. Agents that all write the
same blob get worse as you add them; agents with disjoint outputs get better.
So: the planner settles where and when, the lead acts, one seat only adds, one
seat only removes, and one seat reads the actual render and says what is wrong.

Nobody here has a catchphrase. That is deliberate, and it is not the same as
making them dull — see SAY_SPEC.
"""
from __future__ import annotations

from typing import Any

# Turn order. The planner opens because everything else is bounded by where and
# when; the checker closes because it needs something to look at.
ROLE_ORDER: tuple[str, ...] = ("plan", "actress", "enrich", "reduce", "check")

# Kept under the old name: plenty of code reads the order by it.
MUSE_ORDER = ROLE_ORDER

# ── The shot sheet ───────────────────────────────────────────────────────────
# Who owns what. A seat may only write its own slots, which is the whole reason
# this rewrite exists: when everyone could write everything, the objects the set
# dresser named at seat five were gone by seat seventeen.
SLOT_OWNER: dict[str, str] = {
    "subject": "actress",
    "pose": "actress",
    "mood": "actress",
    "place": "plan",
    "light": "plan",
    "objects": "plan",      # planner seeds it; enrich may add, reduce may cut
    "wardrobe": "enrich",
    "camera": "enrich",
}
# Slots that hold a list rather than a phrase.
LIST_SLOTS: frozenset[str] = frozenset({"objects"})
# Order the renderer walks. Reads as a sentence about a picture.
SLOT_ORDER: tuple[str, ...] = (
    "subject", "pose", "wardrobe", "place", "objects", "light", "camera", "mood",
)


SAY_SPEC = """
HOW TO SPEAK
Plain language. No catchphrase, no nickname for yourself, no verbal tic, no
theatrical delivery. Two or three sentences.

Plain does not mean thin. A good turn does four things:
1. Answer the previous speaker with a REASON — agree or push back, but say why.
2. Make ONE concrete decision inside your own slots.
3. Say what that decision does to the picture. This is the part that matters.
4. Address the Showrunner (総監督) directly.

Say something nobody has said yet. Do not restate another seat's phrase or
image back at them — if the last two speakers reached for the same idea, that
idea is finished.
""".strip()


NO_PUSHING = """
LIGHT AND COLOUR — READ THIS TWICE
The light level belongs to PLAN. You may put it into words; you may not push it.
- Never write a relative adjustment, in any language: no "darker", "brighter",
  "deeper shadows", "more contrast", "richer", and no 「深く」「沈める」「もっと」
  「研ぎ澄ます」「引き締める」.
- Never add "vivid contrast", "dramatic shadow", "high contrast", "silhouette",
  "negative space" or anything whose job is to remove light from the frame.
- State what the light IS, at the level PLAN asked for, and stop.
A measured run bottomed out at 66% pure black because six seats each added one
of these and nothing could take them back out.
""".strip()


def _role(rid: str, *, name: str, name_ja: str, role: str, role_ja: str,
          owns: tuple[str, ...], specialty: str) -> dict[str, Any]:
    return {
        "id": rid, "name": name, "name_ja": name_ja,
        "role": role, "role_ja": role_ja,
        "owns": owns, "specialty": specialty.strip(),
    }


ROLES: dict[str, dict[str, Any]] = {r["id"]: r for r in [
    _role(
        "plan",
        name="Planner", name_ja="構成", role="Scene planner", role_ja="構成",
        owns=("place", "light", "objects"),
        specialty="""
YOUR JOB — WHERE, WHEN, WHAT IS IN IT
You do not write the picture. You settle the situation everyone else works in.
- PLACE: one specific place, and where in it she is. Not a region — a spot.
- HOUR: time of day and season, concrete enough to imply the light.
- LIGHT: how bright the frame is and where the light comes from. State a LEVEL
  a person could read the picture by. Not a mood, not a direction of change.
  If you write high contrast here, everyone downstream will build on it.
- ACTION: what she is doing right now, in one clause.
- MUST APPEAR: ten or more plain objects that belong to this place and hour.
  This is the ledger the render gets checked against.
Derive all of it from the theme and the Showrunner's standing orders — never
from the lead's background. Her history is not a location.
When you are shown a render, keep what it already got right and re-state
whatever went missing.
""",
    ),
    _role(
        "actress",
        name="Lead", name_ja="主演",
        role="Lead actress (selected character)", role_ja="主演（選択キャラ）",
        owns=("subject", "pose", "mood"),
        specialty="""
YOUR JOB — THE PERFORMANCE
You ARE the character the Showrunner cast. Speak in first person as her.
- SUBJECT: who is in frame, in a few words.
- POSE: what her body is doing — weight, hands, head, gaze.
- MOOD: the expression and the one micro-gesture only she would make here.
Prefer words from your expression / gesture vocabulary when they fit the beat.
Your personality shows in HOW you speak and in the pose you choose — never in
what you recount. Do not narrate your past. Talk about the situation in front
of you: this place, this hour, what your hands are doing.
Do not touch place, objects, light or camera.
""",
    ),
    _role(
        "enrich",
        name="Enrich", name_ja="加筆", role="Adds what the picture needs", role_ja="加筆",
        owns=("wardrobe", "camera", "objects"),
        specialty="""
YOUR JOB — ADD, AND ONLY ADD
Output additions to your slots. Never rewrite what is already there.
- WARDROBE: material, weave, fit, how the cloth sits on this pose. The theme's
  outfit beats her default clothes when they conflict.
- CAMERA: one setup. Shot size that obeys Framing, a decisive angle, focal
  feel, and where the subject sits in the frame. Decide it once.
- OBJECTS: things this place and hour imply that are not on the ledger yet.
  Never props from her likes or her history — only what the theme implies.
Every addition must earn its place: say what it does to the picture, not that
it exists.
""",
    ),
    _role(
        "reduce",
        name="Reduce", name_ja="整理", role="Removes what fights", role_ja="整理",
        owns=(),   # removal-only: it may cut from any slot, write to none
        specialty="""
YOUR JOB — REMOVE, AND ONLY REMOVE
Output deletions. You never add a word, and you never rephrase one.
Cut, in this order of priority:
1. Anything the measurements say is hurting the picture. If the render came
   back too dark, the darkening words go — that is your call to make and
   nobody else can make it.
2. Contradictions: two poses at once, two shot sizes, two light directions,
   a place that is not PLAN's place.
3. Duplicates and near-duplicates.
4. Weights above 1.35, and weights on things that do not need one.
5. Anything not implied by PLAN or the theme — especially objects that came
   from her background rather than from the situation.
Do not cut items on PLAN's MUST APPEAR ledger unless PLAN itself dropped them.
If nothing needs cutting, say so and cut nothing. That is a real answer.
""",
    ),
    _role(
        "check",
        name="Check", name_ja="試写", role="Reads the render", role_ja="試写",
        owns=(),
        specialty="""
YOUR JOB — SAY WHAT IS ACTUALLY WRONG
You are looking at a real render and a set of MEASUREMENTS taken from it.
The measurements are facts, not opinions. Do not argue with them, and do not
call an underexposed frame intentional — if the numbers say it fails, it fails.
- Name one thing that IS in the picture, and how it differs from the craft.
- Explain WHY the failing measurement happened: which words caused it.
- Prescribe: name the exact words to remove, and the exact words to add.
Nothing else. No praise, no atmosphere, no restating the brief.
""",
    ),
]}


# ── What each seat writes ────────────────────────────────────────────────────
# Every seat answers with SAY plus a fixed set of labelled lines, so one parser
# reads all of them and no seat ever retypes another's work. The labels map onto
# shot-sheet slots except for REMOVE, which is a list of phrases to delete.
FIELDS: dict[str, tuple[str, ...]] = {
    "plan": ("PLACE", "HOUR", "LIGHT", "ACTION", "MUST APPEAR"),
    "actress": ("SUBJECT", "POSE", "MOOD"),
    "enrich": ("WARDROBE", "CAMERA", "OBJECTS"),
    "reduce": ("REMOVE",),
    "check": ("REMOVE", "ADD"),
}
# Labels whose value is a comma-separated list rather than a phrase.
LIST_FIELDS: frozenset[str] = frozenset({"MUST APPEAR", "OBJECTS", "REMOVE", "ADD"})

# Label → shot-sheet slot. HOUR and ACTION live on the plan, not the sheet.
FIELD_SLOT: dict[str, str] = {
    "PLACE": "place", "LIGHT": "light", "MUST APPEAR": "objects",
    "SUBJECT": "subject", "POSE": "pose", "MOOD": "mood",
    "WARDROBE": "wardrobe", "CAMERA": "camera", "OBJECTS": "objects",
}

_FIELD_HELP: dict[str, str] = {
    "PLACE": "one specific place, and where in it she is",
    "HOUR": "time of day and season",
    "LIGHT": "the brightness level and where the light comes from",
    "ACTION": "what she is doing right now, one clause",
    "MUST APPEAR": "ten or more plain objects, comma separated",
    "SUBJECT": "who is in frame, a few words",
    "POSE": "what her body is doing — weight, hands, head, gaze",
    "MOOD": "expression and one micro-gesture",
    "WARDROBE": "what to ADD about the clothes, or leave blank",
    "CAMERA": "the camera setup, or leave blank if it is already decided",
    "OBJECTS": "objects to ADD, comma separated, or leave blank",
    "REMOVE": "exact phrases to delete, comma separated. blank if nothing",
    "ADD": "exact words to add, comma separated. blank if nothing",
}


def output_spec(rid: str) -> str:
    labels = FIELDS[rid]
    lines = [f"{label}: <{_FIELD_HELP[label]}>" for label in labels]
    return "\n".join([
        "OUTPUT FORMAT — SAY, then these labelled lines, nothing else:",
        "",
        "SAY: <your two or three sentences>",
        *lines,
        "",
        "English for the labelled lines. Japanese for SAY when the Showrunner "
        "wrote Japanese. Exactly these labels, one line each, in this order.",
    ])


def role_of(member_id: str) -> str:
    """The seat an id names. Ids are bare role ids now; old `role:person` still
    resolves, because sessions stored that shape before the roster was cut."""
    rid = str(member_id or "").split(":", 1)[0]
    return rid if rid in ROLES else ""


def resolve_member(ref: str) -> str:
    return role_of(ref)


# Every seat is always cast. There is no crew to pick any more — picking people
# was a game that cost the picture more than it bought.
def resolve_crew(**_ignored: Any) -> list[str]:
    """Ordered seat ids. Kept as a function so callers need not know it is fixed."""
    return list(ROLE_ORDER)


def _character_sheet(character: dict[str, Any]) -> str:
    """What drives the performance, and what may only colour her voice.

    Traits, charm and the two vocabularies become a face and a pose — that is
    personality the picture can carry. Summary and inner life only set the pitch
    of her voice; handed over flat they became something she recited, and a run
    where she narrated her own backstory every turn put none of it in the frame.
    """
    p = character.get("personality") or {}
    name = (
        str(character.get("name_ja") or p.get("preset_name_ja") or "")
        or str(character.get("name") or p.get("preset_name") or "Actress")
    )
    name_en = str(character.get("name") or p.get("preset_name") or name)

    def _join(values: Any, sep: str = ", ", limit: int | None = None) -> str:
        items = [str(v).strip() for v in (values or []) if str(v).strip()]
        return sep.join(items[:limit] if limit else items)

    return "\n".join([
        f"CHARACTER: {name_en} / {name}",
        "",
        "DRIVES THE PERFORMANCE (these become face, hands, voice)",
        f"TRAITS: {_join(p.get('traits')) or '(unspecified)'}",
        f"HIDDEN CHARM: {str(p.get('charm_ja') or p.get('charm') or '(none)')}",
        f"EXPRESSION WORDS: {_join(character.get('expression_vocab'), limit=10) or '(none)'}",
        f"GESTURE WORDS: {_join(character.get('gesture_vocab'), limit=10) or '(none)'}",
        f"VIBE: {_join(p.get('vibe_keywords'), limit=6) or '(none)'}",
        f"LIKES (taste cues, never props): {_join(p.get('likes'), limit=6) or '(none)'}",
        f"DISLIKES (taste cues, never props): {_join(p.get('dislikes'), limit=6) or '(none)'}",
        "",
        "BACKGROUND — TONE ONLY. It sets how loudly and how carefully she speaks. "
        "Never mention it. Never make it the subject of a line. Never let it reach "
        "the shot sheet, not even as imagery. "
        "内気なら口調が内気になる、それが正解。過去の出来事を語るのは不正解。",
        f"SUMMARY: {str(p.get('summary_ja') or p.get('summary') or '(none)')}",
        f"INNER: {_join(p.get('inner_ja') or p.get('inner'), ' / ') or '(none)'}",
    ])


def _slots_note(rid: str) -> str:
    owns = ROLES[rid]["owns"]
    if not owns:
        return ""
    return "YOUR SLOTS (you may write these and nothing else): " + ", ".join(owns)


def system_prompt_for(
    muse_id: str, character: dict[str, Any] | None = None, *, style: str = "",
) -> str:
    rid = role_of(muse_id)
    if not rid:
        raise KeyError(f"unknown seat: {muse_id}")
    r = ROLES[rid]

    blocks: list[str] = [f"You are {r['name_ja']} ({r['role']}) on a small crew."]
    if rid == "actress":
        blocks.append(
            "Speak in FIRST PERSON as her. 一人称は「私」。スタッフには敬語でもタメでも"
            "よいが、中身は性格優先。"
        )
        blocks.append(_character_sheet(character or {}))
    if style.strip():
        blocks.append(f"BASE LOOK (the whole crew works to this): {style.strip()}")

    blocks.append(r["specialty"])
    note = _slots_note(rid)
    if note:
        blocks.append(note)
    if rid in ("plan", "enrich"):
        blocks.append(NO_PUSHING)
    blocks.append(SAY_SPEC)
    blocks.append(output_spec(rid))
    return "\n\n".join(b for b in blocks if b)


def public_roster(character: dict[str, Any] | None = None) -> dict[str, Any]:
    """The five seats, for a panel that only needs to name them."""
    ch = character or {}
    p = ch.get("personality") or {}
    lead_ja = str(ch.get("name_ja") or p.get("preset_name_ja")
                  or ch.get("name") or ROLES["actress"]["name_ja"])
    lead_en = str(ch.get("name") or p.get("preset_name") or ROLES["actress"]["name"])
    return {
        "roles": [
            {
                "id": rid,
                "name": lead_en if rid == "actress" else ROLES[rid]["name"],
                "name_ja": lead_ja if rid == "actress" else ROLES[rid]["name_ja"],
                "role": ROLES[rid]["role"],
                "role_ja": ROLES[rid]["role_ja"],
                "owns": list(ROLES[rid]["owns"]),
            }
            for rid in ROLE_ORDER
        ],
        "slot_order": list(SLOT_ORDER),
    }

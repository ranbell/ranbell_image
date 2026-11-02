"""Turn a still into an image-to-video prompt (Hailuo / MiniMax), in stages.

**A local model asked to do everything in one call fails** (the Showrunner;
`private/muse/design.ja.md`, the image chain, learned it first). The probe of
2026-10-10 (`private/muse_video_probe/`) asked one call to read the still,
invent the motion and format the prompt at once; every draft came back as
"still, cut, run with the prop".

So the work is split, and each call has one job:

1. look  — a vision call says what is in the still: style, each person, and
           what the moment reads as. Nothing is invented here.
2. ideas — one call per kind of scene, each told only its own kind, so the
           ideas differ because their briefs differ.
3. pick  — the Showrunner chooses, in the talk. Nothing here picks.
4. write — the chosen idea in time segments, written in four stages (draft,
           acting, camera, music; see `write`). Code lays out what is pasted.

The photo ledger is not written and no video is rendered here.
"""
from __future__ import annotations

import logging
import random
import re
from typing import Any

from ..ai.json_util import parse_json_object
from . import chain

logger = logging.getLogger(__name__)

DURATIONS = (5, 10, 15)

# The kinds of scene a still can open. Each idea call sees exactly one of
# these, written as what that kind is *for*. No brief lists examples: the
# first version named "the light, the weather, the season, the scale" for
# `shift` and "how large the place really is" for `reveal`, and across three
# stills every shift became a time-lapse of seasons and every reveal a pull
# back onto a huge empty room (`project_examples_dominate`).
KINDS: dict[str, dict[str, str]] = {
    "stay": {
        "ja": "その場の続き",
        "en": "The moment goes on",
        "brief": (
            "One continuous take in the same place. The still is the first frame "
            "of a moment that was already happening; you write the next seconds "
            "of it, and nothing cuts away. Something has to change before the "
            "end, and it should grow out of what is already in this photograph."
        ),
    },
    "reveal": {
        "ja": "カメラが明かす",
        "en": "The camera reveals",
        "brief": (
            "The moment stays, but the camera moves and finds something the "
            "still left out of the frame. Decide first what is out there — "
            "something that changes how the viewer reads this photograph — and "
            "only then how the camera gets to it."
        ),
    },
    "cut": {
        "ja": "別の場所へ",
        "en": "Somewhere else",
        "brief": (
            "The video cuts away to another place or another time that this "
            "moment leads to. The new place should belong to who she is and what "
            "she was just doing, so that nobody else's video would cut there."
        ),
    },
    "shift": {
        "ja": "世界が変わる",
        "en": "The world changes",
        "brief": (
            "She stays where she is and the world around her changes inside the "
            "shot, in front of the camera rather than through a cut. She sets "
            "the change off, or it answers her, and it is a change only this "
            "photograph could turn into."
        ),
    },
}


class VideoError(Exception):
    """A stage produced nothing usable. `message` is for the panel; `raw` is
    what the model last returned, kept so a failure can be read afterwards."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.raw = raw


def shoot_shas(session: dict[str, Any]) -> list[str]:
    """Sha of each finished still. Board images are not included."""
    out: list[str] = []
    for img in (session.get("shoot") or {}).get("images") or []:
        if isinstance(img, str):
            sha = img.strip()
        elif isinstance(img, dict):
            sha = str(img.get("image_id") or img.get("sha256") or "").strip()
        else:
            sha = ""
        if sha:
            out.append(sha)
    return out


_HAIR_SHAPES = frozenset({
    "short_hair", "medium_hair", "long_hair", "very_long_hair", "messy_hair",
    "straight_hair", "wavy_hair", "curly_hair",
})


def _person(char: dict[str, Any] | None) -> dict[str, str] | None:
    char = char or {}
    name = str(char.get("name") or char.get("name_ja") or "").strip()
    if not name:
        return None
    pers = char.get("personality") if isinstance(char.get("personality"), dict) else {}
    look = pers.get("appearance") if isinstance(pers.get("appearance"), dict) else {}
    likes = pers.get("likes") or []
    # Hair and eyes come from the identity tags. `appearance.hair` is a line of
    # character writing ("a blunt bob she cut herself once…"), not a colour, and
    # the video model re-identifies her in every shot from these words alone.
    # Only the colour: length and style tags can disagree with the still (a
    # `short_hair` tag on a girl drawn with long hair), and the still wins.
    tags = [
        str(t) for t in (char.get("identity_tags") or [])
        if not str(t).startswith("hair_") and str(t) not in _HAIR_SHAPES
    ]
    return {
        "name": name,
        # For the talk only: the writers work in English.
        "name_ja": str(char.get("name_ja") or "").strip(),
        "hair": ", ".join(t.replace("_", " ") for t in tags if t.endswith("_hair")),
        "eyes": ", ".join(t.replace("_", " ") for t in tags if t.endswith("_eyes")),
        "habit": str(look.get("habit") or "").strip(),
        "occupation": str(pers.get("occupation") or "").strip(),
        "likes": ", ".join(str(x) for x in likes[:5] if str(x).strip()),
        "prop": str(char.get("signature_prop") or "").replace("_", " ").strip(),
    }


def cast(session: dict[str, Any]) -> list[dict[str, str]]:
    """Everyone in the still — the lead, and the partner in a duet."""
    people = [_person(session.get("character")), _person(session.get("partner_character"))]
    return [p for p in people if p]


# One of these goes to each idea call, or nothing. The compare runs of
# 2026-10-10 (`private/muse_video_probe/runs/compare-20261010-200140`, 12 stills):
#
# - Everything at once: most ideas acted out the same line of her, over what
#   the still showed. `charm` was the worst — whichever idea got it re-enacted
#   that scene every time (a sulk on a girl drawn calm), so it is never sent.
# - Name and looks only: the most varied, but the world went wrong without her
#   occupation (a darkroom's steaming tray became a bakery, a stagehand a
#   singer). So the occupation always goes, as ground rather than as a hook.
# - One hook per kind, drawn afresh on every pitch: rooted and still varied.
HOOKS: dict[str, str] = {"habit": "a habit", "likes": "likes", "prop": "often carries"}


_UNNAMED = (
    "- Not known by name. They are whoever the photograph shows; name each "
    "one by how they look (hair, eyes, clothes)."
)


def _who(
    people: list[dict[str, str]], *, hook: str | None = None, ground: bool = False,
    ja: bool = False,
) -> str:
    if not people:
        return _UNNAMED
    lines = []
    for p in people:
        looks = ", ".join(x for x in (p["hair"], p["eyes"]) if x)
        line = f"- {p['name']}" + (f" ({looks})" if looks else "")
        # Ideas written in Japanese guessed the kanji ("Hazuki" → 羽月, 2026-10-11).
        if ja and p.get("name_ja"):
            line += f"\n  in Japanese: {p['name_ja']}"
        if ground and p.get("occupation"):
            line += f"\n  works as: {p['occupation']}"
        if hook and p.get(hook):
            line += f"\n  {HOOKS[hook]}: {p[hook]}"
        lines.append(line)
    return "\n".join(lines)


def draw_hooks(kinds: tuple[str, ...], rng: random.Random | None = None) -> dict[str, str | None]:
    """A different hook (or none) for each kind, drawn without repeats."""
    pool: list[str | None] = [*HOOKS, None]
    pool += [None] * max(0, len(kinds) - len(pool))
    picked = (rng or random).sample(pool, len(kinds))
    return dict(zip(kinds, picked))


def _lang(locale: str) -> str:
    return "Japanese" if str(locale or "").startswith("ja") else "English"


# ── 1. look ──────────────────────────────────────────────────────────────────

# What the moment reads as is asked for, not only what is visible. The first
# version said "describe only what the picture shows, no guesses": on Asahi's
# still it wrote "a person with short red hair, head tilted down, one hand at
# the mouth", where Gemini read "an anime girl with red twin tails, sleepy
# orange eyes, yawning" — and every later stage built on the flatter reading
# (2026-10-11). Asked for style, hair style and what the moment reads as, the
# same 26B wrote "anime illustration… red pigtails held by gold bows… as if she
# is mid-yawn".
_LOOK_SYSTEM = (
    "You are looking at one picture. It becomes the first frame of a short video, and "
    "the people who will plan and write that video cannot see it — your words are all "
    "they get, so what you leave out they will get wrong.\n\n"
    "Write in English, plainly:\n"
    "- the style of the picture (anime illustration, photograph, 3D render, painting…) and its colours;\n"
    "- each person: apparent gender and age range, hair (colour, length, style — twin tails, bob, bun…), "
    "eyes, build, and clothes with their fit and material; by name when names are given;\n"
    "- what each body, hand and face is doing right now, what that moment reads as "
    "(yawning, about to laugh, startled, sulking…) and the feeling it gives;\n"
    "- the framing: how close the camera is (face, waist up, full body, wide), its height and angle, "
    "where each person is in the frame and which way they face, and what is in front of and behind them;\n"
    "- the place, where the light comes from and its quality, and what sits at the edges of the frame.\n"
    "Say what the moment looks like to a viewer; do not invent what happens next."
)


async def look(
    ollama, *, image: bytes, people: list[dict[str, str]], model: str | None,
    num_ctx: int | None, fallback: str = "",
) -> tuple[str, bool]:
    """What the still shows. Returns (text, blind).

    A model that cannot read images answers blind rather than failing; then the
    shoot's own prompt stands in for the picture and `blind` says so out loud.
    """
    prompt = f"The people in it:\n{_who(people)}\n\nDescribe this picture."
    try:
        raw, blind = await chain._call_seeing(
            ollama, system=_LOOK_SYSTEM, prompt=prompt, model=model,
            images=[image], num_ctx=num_ctx, think=False,
        )
    except chain.ChainError:
        raw, blind = "", True
    text = str(raw or "").strip()
    if blind or not text:
        return (fallback.strip() or text), True
    return text, False


# ── 2. ideas ─────────────────────────────────────────────────────────────────

def idea_prompt(
    kind: str, *, still: str, people: list[dict[str, str]], short: str,
    duration_s: int, locale: str, hook: str | None = None, context: str = "",
) -> tuple[str, str]:
    """(system, prompt) for one idea of one kind."""
    k = KINDS[kind]
    lang = _lang(locale)
    system = (
        "You are a director's assistant pitching one idea for a short video that "
        "starts from a finished photograph. The director reads several pitches "
        "side by side and picks one, and each pitch was asked for a different "
        "kind of scene — so yours is useful only if it is truly this kind.\n\n"
        # The feeling first. Told "a safe idea is the one the director skips"
        # and asked why it was surprising, the pitches for a still the director
        # wanted "sleepy and cute" were a trembling hand at a clock, black birds
        # over the window, a painter's studio and a magic meadow (2026-10-11).
        "What the director wants decides how the clip should feel; the kind only "
        "decides how it moves. When they want something quiet, sweet or simple, the "
        "right pitch is quiet, sweet or simple — a twist that works against that "
        "feeling is a wrong pitch, however surprising. Be specific rather than "
        "generic: the idea should belong to this photograph and this person.\n\n"
        f"The kind you are pitching: {k['brief']}\n\n"
        "Answer with one JSON object and nothing else:\n"
        '{"title": ..., "story": ..., "beats": [...]}\n'
        f"title is one short line in {lang} the director reads first. story is "
        f"two or three sentences in {lang} saying what the viewer sees, in order, "
        "and why it is right for what the director wants. beats is the same video in "
        "English as a list of plain physical actions in order, each one thing a "
        "camera can see; the person who writes the shots works only from these."
    )
    direction = (
        "What the director wants, in their words:\n"
        f"{short.strip()}\n"
        "Honour it, read through the kind you are pitching. If this kind cannot "
        "deliver it literally, find how it can deliver what they are after."
        if short.strip() else
        "The director left it to you. What happens is yours to decide."
    )
    prompt = (
        f"Length: {duration_s} seconds.\n"
        f"Who is in it:\n{_who(people, hook=hook, ground=True, ja=locale == 'ja')}\n\n"
        f"What the photograph shows (this is second 0):\n{still.strip()}\n\n"
        + (f"The shoot this photograph came out of:\n{context.strip()}\n\n" if context.strip() else "")
        + f"{direction}"
    )
    return system, prompt


def _parse_idea(raw: str) -> dict[str, Any]:
    data = parse_json_object(raw)
    beats = [str(b).strip() for b in (data.get("beats") or []) if str(b).strip()]
    title = str(data.get("title") or "").strip()
    if not beats or not title:
        raise ValueError("title or beats missing")
    return {"title": title, "story": str(data.get("story") or "").strip(), "beats": beats}


async def ideas(
    ollama, *, still: str, people: list[dict[str, str]], short: str,
    duration_s: int, locale: str, model: str | None, num_ctx: int | None,
    kinds: tuple[str, ...] = tuple(KINDS), rng: random.Random | None = None,
    context: str = "",
) -> list[dict[str, Any]]:
    """One idea per kind. A kind that fails is reported, not dropped silently."""
    out: list[dict[str, Any]] = []
    ja = str(locale or "").startswith("ja")
    hooks = draw_hooks(kinds, rng)
    for kind in kinds:
        system, prompt = idea_prompt(
            kind, still=still, people=people, short=short,
            duration_s=duration_s, locale=locale, hook=hooks[kind], context=context,
        )
        entry: dict[str, Any] = {"kind": kind, "label": KINDS[kind]["ja" if ja else "en"]}
        raw = ""
        try:
            raw = await chain._call(
                ollama, system=system, prompt=prompt, model=model,
                images=None, num_ctx=num_ctx, think=False,
            )
            entry.update(_parse_idea(raw))
        except Exception as exc:
            logger.warning("[muse.video] idea %s failed: %s", kind, exc)
            entry["error"] = (
                "この案は書けませんでした。もう一度出してください。"
                if ja else "This idea did not come out. Ask again."
            )
            entry["raw"] = raw[:600]
        out.append(entry)
    return out


# ── 4. write ─────────────────────────────────────────────────────────────────

# A time-step prompt: the clip in whole-second segments, each with a short
# title, what the viewer sees, the camera, and the music. The Showrunner's
# reference (2026-10-11) is a prompt written this way that works pasted into
# Hailuo as it is. The reference itself is not shown to the model: an example
# becomes the answer (`project_examples_dominate`); its shape is described.
#
# **Written in four stages, one job each** — the image chain's lesson
# (`private/muse/design.ja.md`: A writes the moment, then B/C/D each rewrite it
# for one thing) and the Showrunner's: "a local LLM asked to do everything at
# once fails". One call asked for arc, acting, camera, light and music came
# back thin in all of them, and a list of fields per shot was filled with
# nothing ("no face is visible"):
#
#   1. draft   — the segments and the feeling that grows through them
#   2. acting  — rewrites each segment's action: face, hands, timing, hair and cloth
#   3. camera  — writes only the camera (and light) line of each segment
#   4. music   — writes only the music line of each segment
#
# 3 and 4 never touch the action, so they cannot drop it. What the director
# asked for opens every stage as absolute: the image chain's C stage, told to
# shorten, dropped the costume the theme named.

_STAGE_COMMON = (
    "This is one stage of writing the prompt for a short image-to-video clip "
    "(Hailuo / MiniMax). The picture is the first frame, 0:00; the clip is what "
    "happens from there. The clip is split into whole-second segments.\n\n"
    "What the director asked for is absolute. Every stage keeps it; nothing is ever "
    "a reason to drop it.\n\n"
    # The draft also restates the photograph (START) before its first segment:
# told only to "start from the picture", it had a boy in the middle of the
# rink hold her hands at the railing within the first second (Akari). A
# separate continuity check after the draft was tried and answered "follows"
# four times of four, the jump included (2026-10-12) — not kept. Nor was a
# line explaining that a jump renders as a glitch: no change in four runs.
# GAP has it say what must happen before the asked-for moment can.
# The camera may circle, follow and rise in one flowing move, and a move the
# talk planned rides on the timetable's CAMERA line: told "one movement" per
# segment for continuity, it mostly held still or pushed in, where a render
# the Showrunner liked circled her the whole time (2026-10-12).
# A rendered clip jumped from the still at 0:00 and cut on every segment:
    # each stage had written each segment as a fresh shot (Akari, 2026-10-11).
    "The clip is one continuous shot that grows out of the photograph. At 0:00 it is the "
    "photograph exactly — the same framing, the same places, the same poses — and every "
    "segment picks up where the one before it ended. Nothing appears, disappears or "
    "changes place without being shown moving. Only a segment whose action says it cuts "
    "may start somewhere new.\n\n"
    # "kusu-tto giggle": the director's クスッと, spelled out for a model that
    # reads English (Sena, 2026-10-11).
    "The prompt is in English. When the director's words are Japanese, write what "
    "they mean — never a Japanese word spelled out in Latin letters."
)

_DRAFT_SYSTEM = _STAGE_COMMON + """

Your stage: the draft. The first segment starts from exactly what the photograph shows — where each person is, how far apart they are, what they hold, how they sit or stand. If what the director wants needs them somewhere else, closer together, or holding something they are not, the timetable gets there and shows it: someone across the room takes time to cross it, a hand has to reach before it holds. Or a segment cuts to it. Decide the segments and what happens in each — one feeling that grows across the whole clip toward what the director asked for, every segment moving it one step. Each segment shows the viewer something change that a camera can see. A segment with nothing new to show is not a segment. A cut only when the idea goes to another place or time; say so in that segment's action. Other stages will add the acting detail, the camera and the music — leave those to them.

When the camera's movement is part of the idea — circling them, following a run, rising to show the place, sweeping round to the other side — add a CAMERA line to that segment saying how it moves. Otherwise leave the camera to a later stage.

Answer in this shape and nothing else, every field on its own line:
START: one or two sentences — where each person is in the photograph at 0:00, how far apart they are, which way they face, and what they are doing.
GAP: what has to happen first, from START, before what the director asked for can happen — who has to move where, turn, or pick something up. "Nothing" when it can start at once.
SEGMENT 0:00-0:01 | a few words naming what happens
ACTION: two or three plain sentences — what happens and what she feels.
CAMERA: only where the camera's movement matters; leave the line out otherwise.
(the next segment the same way)
In the first segment's action, say the style of the picture and how each person looks (hair, eyes, clothes)."""

_ACTING_SYSTEM = _STAGE_COMMON + """

Your stage: the acting. You are given the draft. Rewrite every segment's action so a video model can perform it: the face first — the eyes, brows, mouth, cheeks, the breath — then the hands and fingers, the shoulders, the timing (slowly, a beat of stillness, all at once), and what moves with her (hair, sleeves, the hem). Keep what happens, the order and the feeling of the draft; you are making it visible, not changing it. Keep the first segment's style and looks. When it suits the idea, she can notice the camera and react to being watched.

Answer in the same shape as the draft — the SEGMENT lines exactly as given, each followed by its rewritten ACTION line (three to five sentences)."""

_CAMERA_SYSTEM = _STAGE_COMMON + """

Your stage: the camera and the light. You are given the segments. The camera starts where the photograph's camera is — its framing is in the description of the picture. Where a segment already has a CAMERA line, that is the movement the director and the actress planned: keep it, and write it out in full. For each segment write: where the camera starts (for the first segment, the photograph's framing; after that, exactly where the segment before ended), how it moves and how fast — one movement, or several flowing together without a break (circling round her while closing in; rising as it pulls back), or holding still — the framing it ends on, what stays in focus, and how the light sits or changes. A camera that circles says which way and how far round (a quarter turn to her left), and what comes into view. The camera moves for a reason the viewer can feel — it follows the acting. A different framing with no movement between is a cut: only where the segment's action says it cuts. A camera that holds still ends on the framing it started on. Name each framing itself ("waist up, low angle") — the video model never sees the other segments, so "where the last one ended" means nothing to it. Do not rewrite the action.

Answer with one line per segment, in order, and nothing else:
CAMERA 1: starts …; moves …; ends …; focus …; light …
CAMERA 2: starts …; moves …; ends …; focus …; light …"""

# The world around them, one job of its own. The render the Showrunner liked
# had an Environment line on every segment — leaves stirring, the moon
# brightening, a gust — and ours had the place standing still.
_ENV_SYSTEM = _STAGE_COMMON + """

Your stage: the world around them. You are given the segments. For each one, write what moves around the people — air and wind in hair and cloth, light shifting, things in the background, steam, dust or particles — so the place feels alive and answers what happens. Keep to what the place in the picture has; nothing new arrives unless the action brings it. Do not rewrite the action.

Answer with one line per segment, in order, and nothing else:
ENVIRONMENT 1: ...
ENVIRONMENT 2: ..."""

_CAMERA_BY_STYLE = {
    "calm": "This clip is shot quietly: the camera may hold still or drift.",
    "flow": "This clip keeps the camera moving: no segment holds still, and each one's movement carries into the next.",
    "cuts": "This clip cuts between angles: a segment whose action says it cuts starts on its new framing, and says how the cut is made; every other segment starts where the one before ended.",
}
_ACTING_BY_STYLE = {
    "cuts": "Where a segment cuts to a new shot, say again how each person looks (hair, clothes) — the video model does not carry them across a cut.",
}

_SCORE_SYSTEM = _STAGE_COMMON + """

Your stage: the music. You are given the segments. Write what plays in each one. The first segment starts on the instrument you are given; from there the music follows the feeling — add one layer, change the rhythm, or drop to a single sound where the clip turns — so that each segment's music builds on the one before. Do not rewrite the action.

Answer with one line per segment, in order, and nothing else:
MUSIC 1: ...
MUSIC 2: ..."""


# The timetable is what the director and the actress work on together. After
# each exchange this stage writes it again with what was settled — the video
# counterpart of the photo shoot's ledger writer. Asked to keep what was not
# touched, and to take her suggestions only once the director took them.
# Given "Actress: … / Showrunner: …", it once wrote in her suggestion and
# dropped the director's own ask (Asahi, 2026-10-11): the director's line
# now comes first, and hers is marked as what is being answered.
# Asked to keep everything, it never moved a boundary, and four asks piled
# into the last second of five (Akari, 2026-10-11): it may share the
# seconds out again when a moment has grown.
# The AVOID line: "no trembling" left the hands and came back in the knees
# (Akari, twice on 2026-10-12) — a no is about the whole clip, so it is kept
# as a line of its own and every polishing stage is told it.
_BOARD_SYSTEM = _STAGE_COMMON + """

Your stage: keeping the timetable. The director and the actress are working the clip out segment by segment while looking at this timetable. You are given the timetable, what the director just said, and what the actress had said just before it. Write in what the director just asked for — a change to one moment, a new beat, a different ending, a different length of a segment. What the actress had said goes in only when the director's line says yes to it ("yes", "let's do that", picking her option); otherwise it is only there so that you understand what the director is answering. When a moment now has more to do than its seconds can hold — "she gets used to it bit by bit" in a single second — give it more seconds by taking them from a moment that has less to do; the whole clip stays the same length. Change only that; every segment and every word nobody asked to change stays exactly as it is.

A segment may have a CAMERA line — how the camera moves there. Write one in when the director asks for a camera movement or says yes to one ("circle round them here"); keep the ones there unless asked to change them.

The timetable ends with an AVOID line: what the director has said they do not want in the clip ("no trembling", "not sad"). Carry it over, and add to it when the director says no to something — that holds for every moment, not just the one being talked about. "none" when there is nothing.

Answer with the whole timetable in the same shape — SEGMENT lines with their ACTION (and CAMERA where there is one), then the AVOID line — and nothing else. If nothing in the exchange changes the timetable or the AVOID line, answer exactly: NO CHANGE"""


# How the clip is shot. A render the Showrunner liked kept its camera moving
# round her for all five seconds, where ours held still for three; and cutting
# between angles had once broken the scene (2026-10-11/12). Which of the
# three a clip gets is settled in the talk, and can be changed on screen.
STYLES: dict[str, str] = {
    "calm": (
        "One continuous shot from the photograph, with a quiet camera: it may hold still, "
        "drift or ease in, so that the face and the small movements carry the clip."
    ),
    "flow": (
        "One continuous shot from the photograph, and the camera never stops: in every "
        "segment it moves — closing in, circling round them, following, rising, sweeping "
        "past — often two movements at once, each segment's movement flowing into the next. "
        "Segments are uneven, a second or two each, changing with the action."
    ),
    "cuts": (
        "Two or three shots. The first is the photograph and carries on from it; each new "
        "shot opens a segment whose ACTION says it cuts, and how the eye is carried across — "
        "a whip pan, the camera passing behind someone, a cut on a movement. Same place, same "
        "people, same clothes in every shot; never a cut in the first second."
    ),
}
STYLE_NAMES = {"calm": "quiet", "flow": "moving, no cuts", "cuts": "cut between angles"}


def _segments(duration_s: int, style: str = "calm") -> int:
    """About two seconds a segment, with a beat at each end of a short clip;
    shorter beats when the camera keeps moving."""
    if style == "flow":
        return {5: 4, 10: 7, 15: 9}.get(duration_s, max(4, round(duration_s / 1.4)))
    return {5: 3, 10: 5, 15: 6}.get(duration_s, max(3, round(duration_s / 2)))


def write_context(
    *, still: str, people: list[dict[str, str]], idea: dict[str, Any] | None = None,
    duration_s: int, intent: str = "", persona: str = "", direction: str = "",
    avoid: str = "", style: str = "calm",
) -> str:
    """What every stage is told about the clip, the absolute first."""
    idea = idea or {}
    beats = "\n".join(f"- {b}" for b in idea.get("beats") or [])
    story = str(idea.get("story") or "").strip()
    return (
        # What the director asked for in the talk, in their own words. Without
        # it the writer had only the idea, and "make it cute" came back as a
        # ghost story (Asahi, 2026-10-11).
        #
        # The direction is what they settled first; the talk goes on after it.
        # Marked absolute, the settled "her trembling hand" outranked the
        # later "no trembling" and the prompt trembled (Akari, 2026-10-11).
        # A clerk asked to keep the direction up to date said "changed" to
        # 15 lines of 15, picks included (2026-10-12) — so the order of
        # precedence is said instead.
        (f"The direction the director and the actress settled first — the whole idea. Where the "
         f"director's later words or the timetable say otherwise, those win:\n{direction.strip()}\n\n"
         if direction.strip() else "")
        + (f"What the director wants (absolute), in their words, oldest first — a later line wins "
           f"over an earlier one:\n{intent.strip()}\n\n" if intent.strip() else "")
        + (f"What the director does not want anywhere in the clip (absolute) — not in the face, "
           f"the body, the camera or the music:\n{avoid.strip()}\n\n" if avoid.strip() else "")
        + f"How it is shot ({STYLE_NAMES.get(style, style)}): {STYLES.get(style, STYLES['calm'])}\n\n"
        + f"Length: {duration_s} seconds, in {_segments(duration_s, style)} segments.\n"
        f"Who is in it:\n{_who(people)}\n\n"
        + (persona.strip() + "\n\n" if persona.strip() else "")
        + f"The picture (0:00):\n{still.strip()}"
        + (f"\n\nThe idea, in order:\n{beats}" if beats else "")
        + (f"\n\nThe idea as the director and the actress put it:\n{story}" if story else "")
    )


def reference_block(char: dict[str, Any] | None) -> str:
    """Who she is, fenced so it shapes how she moves without being copied.

    The image chain's brief: personality handed over plainly becomes something
    the picture must contain (a thermos drawn into a scene with no coffee);
    fenced and marked "do not use these words", the influence stays and the
    words do not (0 leaks across the verification runs). The video ideas, given
    her charm line plainly, re-enacted it in every pitch — so only the acting
    stage gets her, and only fenced, and only when she is in the picture.
    """
    char = char or {}
    pers = char.get("personality") if isinstance(char.get("personality"), dict) else {}
    look = pers.get("appearance") if isinstance(pers.get("appearance"), dict) else {}
    lines = [
        ("personality", ", ".join(str(t) for t in pers.get("traits") or [])),
        ("charm", pers.get("charm")),
        ("habit", look.get("habit")),
        ("expression", look.get("expression")),
        ("body", look.get("body")),
        ("voice", look.get("voice")),
    ]
    body = "\n".join(f"{k}: {str(v).strip()}" for k, v in lines if str(v or "").strip())
    if not body:
        return ""
    name = char.get("name") or char.get("name_ja") or "her"
    return (
        f"** This is {name}'s background. For reference: use it to imagine how she "
        "moves and reacts in this clip. Do not put these words into the prompt. **\n"
        f"</start REFERENCE ONLY>\n{body}\n</end REFERENCE ONLY>"
    )


# The music still opens on an instrument picked beforehand: asked inside the
# writing call, gemma4 answered "a solo cello" for 11 of 13 clips. Asked on
# its own for a few candidates, it names instruments from her work and her
# likes, and one of the first three is drawn here.
_MUSIC_SYSTEM = (
    "You suggest the instrument for the score of a short video clip. The score "
    "should sound like the person in it: something from her work, what she "
    "likes, or the place she is in, so that hearing it tells you whose clip "
    "this is. It has to be a musical instrument that can play the score — "
    "her work's tools are not instruments. Answer with five instruments, one "
    "per line, the one that belongs to her most first. Only the instrument names."
)


def parse_instruments(raw: str) -> list[str]:
    out: list[str] = []
    for line in (raw or "").splitlines():
        name = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("*").strip()
        # An instrument is a few words. Asked about an unnamed person it once
        # answered with a description of her ("A girl with dark hair, …").
        # "[Instrument 3]": a template, when the clip it was given was empty.
        if re.search(r"[\[\]<>{}]|^instrument\s*\d*$", name, re.IGNORECASE):
            continue
        if name and len(name.split()) <= 4 and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out


async def pick_instrument(
    ollama, *, people: list[dict[str, str]], idea: dict[str, Any],
    model: str | None, num_ctx: int | None, rng: random.Random | None = None,
    clip: str = "",
) -> str:
    """One instrument for this clip, or "" to let the writer choose.

    `clip` is the clip as the talk settled it (direction and timetable); without
    it — the idea is empty once the talk works from a timetable — a photo of
    nobody in the cast left this call nothing to go on, and no instrument came
    back (two prompts of four, 2026-10-11)."""
    who = (
        f"Who is in it:\n{_who(people, hook='likes', ground=True)}\n\n" if people else ""
    )
    prompt = f"{who}The clip:\n" + (clip or (
        f"{idea.get('title') or ''}\n" + "\n".join(f"- {b}" for b in idea.get("beats") or [])
    ))
    try:
        raw = await chain._call(
            ollama, system=_MUSIC_SYSTEM, prompt=prompt, model=model,
            images=None, num_ctx=num_ctx, think=False,
        )
    except chain.ChainError:
        return ""
    names = parse_instruments(raw)[:3]
    return (rng or random).choice(names) if names else ""


# "SEGMENT 0:00-0:01 | Title", and the shapes it drifts into: no "SEGMENT",
# an en dash or "~", spaces around the dash, a full-width bar, no title.
_SEGMENT_LINE = re.compile(
    r"^\s*(?:SEGMENT\s*)?(\d+):(\d{2})\s*[-–~〜]\s*(\d+):(\d{2})\s*[|｜:]?\s*(.*)$",
    re.IGNORECASE,
)
_SEGMENT_FIELD = re.compile(
    r"^\s*(ACTION|VISUAL ACTION|CAMERA|CAMERA WORK|MUSIC)\s*(?:\([^)]*\))?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
_FIELD_KEY = {"action": "action", "visual action": "action", "camera": "camera",
              "camera work": "camera", "music": "music"}


def _unmark(line: str) -> str:
    """A line without the heading or bullet a model puts before a label."""
    return re.sub(r"^\s*(?:#+|[-*>•]|⏱️)\s*", "", line)


def parse_segments(raw: str) -> list[dict[str, Any]]:
    """The labelled answer as [{"start", "end", "title", "action", "camera", "music"}]."""
    segments: list[dict[str, Any]] = []
    field = ""
    for line in (raw or "").replace("**", "").replace("__", "").splitlines():
        line = _unmark(line).strip()
        if not line:
            continue
        if re.match(r"(AVOID|START|GAP)\s*[:：]", line, re.IGNORECASE):
            field = ""  # lines of their own, not the last segment's action
            continue
        if m := _SEGMENT_LINE.match(line):
            a, b, c, d, title = m.groups()
            segments.append({
                "start": int(a) * 60 + int(b), "end": int(c) * 60 + int(d),
                "title": title.strip(),
            })
            field = "title"
        elif segments and (m := _SEGMENT_FIELD.match(line)):
            field = _FIELD_KEY[m.group(1).lower()]
            segments[-1][field] = m.group(2).strip()
        elif segments and field:
            # A field that wrapped onto the next line.
            segments[-1][field] = f"{segments[-1].get(field, '')} {line}".strip()
    return [s for s in segments if str(s.get("action") or "").strip()]


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fit_times(segments: list[dict[str, Any]], duration_s: int) -> None:
    """Whole seconds from 0:00 to the end, in order. The model's times are kept
    when they already are; otherwise the clip is shared out evenly."""
    n = len(segments)
    ok = (
        segments[0]["start"] == 0 and segments[-1]["end"] == duration_s
        and all(s["start"] < s["end"] for s in segments)
        and all(a["end"] == b["start"] for a, b in zip(segments, segments[1:]))
    )
    if ok:
        return
    edges = [round(duration_s * i / n) for i in range(n + 1)]
    for s, start, end in zip(segments, edges, edges[1:]):
        s["start"], s["end"] = start, max(end, start + 1)


def layout(segments: list[dict[str, Any]], *, duration_s: int) -> str:
    """The prompt as it is pasted: one block per segment. ValueError if empty."""
    if not segments:
        raise ValueError("no segments with an action")
    _fit_times(segments, duration_s)
    blocks = []
    for s in segments:
        head = f"{_clock(s['start'])} - {_clock(s['end'])}"
        if s.get("title"):
            head += f" | {s['title']}"
        lines = [head, f"Visual Action: {s['action']}"]
        if s.get("camera"):
            lines.append(f"Camera Work: {s['camera']}")
        if s.get("environment"):
            lines.append(f"Environment: {s['environment']}")
        if s.get("music"):
            lines.append(f"Music: {s['music']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_NUMBERED = re.compile(r"^\s*(CAMERA|MUSIC|ENVIRONMENT)\s*(\d+)?\s*[:：]\s*(.+)$", re.IGNORECASE)


def parse_numbered(raw: str, label: str, count: int) -> list[str]:
    """One "LABEL n: text" line per segment, in segment order.

    A numbered line goes to its segment; unnumbered ones fill the rest in
    order. Missing segments come back empty and their line is left out.
    """
    out = [""] * count
    loose: list[str] = []
    for line in (raw or "").replace("**", "").splitlines():
        m = _NUMBERED.match(_unmark(line))
        if not m or m.group(1).upper() != label:
            continue
        n, text = m.group(2), m.group(3).strip()
        if n and 1 <= int(n) <= count and not out[int(n) - 1]:
            out[int(n) - 1] = text
        else:
            loose.append(text)
    for i in range(count):
        if not out[i] and loose:
            out[i] = loose.pop(0)
    return out


def _segments_text(segments: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"SEGMENT {_clock(s['start'])}-{_clock(s['end'])} | {s.get('title', '')}\nACTION: {s['action']}"
        + (f"\nCAMERA: {s['camera']}" if s.get("camera") else "")
        for s in segments
    )


async def _stage(ollama, system: str, prompt: str, *, model, num_ctx) -> str:
    try:
        return await chain._call(
            ollama, system=system, prompt=prompt, model=model,
            images=None, num_ctx=num_ctx, think=False,
        )
    except chain.ChainError:
        return ""


def _write_failed(locale: str, raw: str) -> VideoError:
    return VideoError(
        "撮影文に清書できませんでした。もう一度押すか、別の案を選んでください。"
        if str(locale or "").startswith("ja") else
        "Could not write this idea as a prompt. Try again or pick another idea.",
        raw=raw,
    )


async def draft(
    ollama, *, context: str, duration_s: int, model: str | None, num_ctx: int | None,
    locale: str = "ja",
) -> tuple[list[dict[str, Any]], str]:
    """The segments and what happens in each. One retry; VideoError when none."""
    raw, problem, segments = "", "", []
    for _ in range(2):
        ask = context if not problem else (
            f"{context}\n\nYour last answer could not be used ({problem}). It was:\n"
            f"{raw[:1500]}\n\nAnswer again in the SEGMENT / ACTION lines only."
        )
        raw = await _stage(ollama, _DRAFT_SYSTEM, ask, model=model, num_ctx=num_ctx)
        segments = parse_segments(raw)
        if segments:
            _fit_times(segments, duration_s)
            return segments, raw
        problem = "it was empty" if not raw.strip() else "it had no SEGMENT lines with an ACTION"
    logger.warning("[muse.video] draft failed (%s): %r", problem, (raw or "")[:500])
    raise _write_failed(locale, raw)


_AVOID_LINE = re.compile(r"^\s*\**\s*AVOID\s*\**\s*[:：]\s*\**\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_avoid(raw: str) -> str | None:
    """The AVOID line's text ("" for none), or None when there is no such line."""
    m = _AVOID_LINE.search(raw or "")
    if not m:
        return None
    text = m.group(1).strip().strip("*").strip()
    return "" if re.fullmatch(r"(none|nothing|なし|-)?\.?", text, re.IGNORECASE) else text


async def keep_board(
    ollama, *, context: str, segments: list[dict[str, Any]], exchange: str,
    duration_s: int, model: str | None, num_ctx: int | None, avoid: str = "",
) -> tuple[list[dict[str, Any]] | None, str]:
    """The timetable after the latest exchange, or None when it did not change
    (or the answer could not be read — the timetable is then left as it was).
    The AVOID line is in `raw`; read it with `parse_avoid`."""
    raw = await _stage(
        ollama, _BOARD_SYSTEM,
        f"{context}\n\nThe timetable:\n{_segments_text(segments)}\nAVOID: {avoid or 'none'}"
        f"\n\nThe latest exchange:\n{exchange}",
        model=model, num_ctx=num_ctx,
    )
    if re.search(r"\bNO CHANGE\b", raw or "", re.IGNORECASE) and not _SEGMENT_LINE.search(raw or ""):
        return None, raw
    new = parse_segments(raw)
    if not new:
        return None, raw
    _fit_times(new, duration_s)
    return new, raw


async def polish(
    ollama, *, segments: list[dict[str, Any]], context: str, acting_context: str,
    duration_s: int, model: str | None, num_ctx: int | None, instrument: str = "",
    style: str = "calm",
) -> dict[str, Any]:
    """Acting, then camera, the world around them and music, over a settled
    timetable. A stage that fails leaves the timetable's version of its part
    in place."""
    segments = [dict(s) for s in segments]
    _fit_times(segments, duration_s)
    stages: dict[str, str] = {}

    # Acting — her persona (fenced) reaches this stage only.
    acting_rule = _ACTING_BY_STYLE.get(style, "")
    raw = await _stage(
        ollama, _ACTING_SYSTEM,
        f"{acting_context}\n\nThe draft:\n{_segments_text(segments)}" + (f"\n\n{acting_rule}" if acting_rule else ""),
        model=model, num_ctx=num_ctx,
    )
    stages["acting"] = raw
    acted = parse_segments(raw)
    if len(acted) == len(segments):
        for seg, new in zip(segments, acted):
            seg["action"] = new["action"]
            if new.get("title"):
                seg["title"] = new["title"]

    # Camera and music write only their own line, so they cannot drop the action.
    listing = _segments_text(segments)
    raw = await _stage(
        ollama, _CAMERA_SYSTEM,
        f"{context}\n\nThe segments:\n{listing}\n\n{_CAMERA_BY_STYLE.get(style, _CAMERA_BY_STYLE['calm'])}",
        model=model, num_ctx=num_ctx,
    )
    stages["camera"] = raw
    for seg, line in zip(segments, parse_numbered(raw, "CAMERA", len(segments))):
        if line:
            seg["camera"] = line

    raw = await _stage(
        ollama, _ENV_SYSTEM, f"{context}\n\nThe segments:\n{listing}",
        model=model, num_ctx=num_ctx,
    )
    stages["environment"] = raw
    for seg, line in zip(segments, parse_numbered(raw, "ENVIRONMENT", len(segments))):
        if line:
            seg["environment"] = line

    raw = await _stage(
        ollama, _SCORE_SYSTEM,
        f"{context}\n\nThe segments:\n{listing}\n\n"
        + (f"The music starts on: {instrument}" if instrument else "Choose where the music starts."),
        model=model, num_ctx=num_ctx,
    )
    stages["music"] = raw
    for seg, line in zip(segments, parse_numbered(raw, "MUSIC", len(segments))):
        if line:
            seg["music"] = line

    return {"prompt": layout(segments, duration_s=duration_s), "segments": len(segments), "stages": stages}


async def write(
    ollama, *, still: str, people: list[dict[str, str]], idea: dict[str, Any],
    duration_s: int, model: str | None, num_ctx: int | None, locale: str = "ja",
    rng: random.Random | None = None, intent: str = "", persona: str = "",
    direction: str = "", segments: list[dict[str, Any]] | None = None, avoid: str = "",
    style: str = "calm",
) -> dict[str, Any]:
    """The prompt in four stages: draft (skipped when a timetable is given),
    acting, camera, music. What each stage returned is kept in `stages`."""
    clip = "\n".join(x for x in [direction.strip(), _segments_text(segments or [])] if x)
    instrument = await pick_instrument(
        ollama, people=people, idea=idea, model=model, num_ctx=num_ctx, rng=rng, clip=clip,
    )
    context = write_context(
        still=still, people=people, idea=idea, duration_s=duration_s,
        intent=intent, direction=direction, avoid=avoid, style=style,
    )
    stages: dict[str, str] = {}
    if not segments:
        segments, stages["draft"] = await draft(
            ollama, context=context, duration_s=duration_s, model=model,
            num_ctx=num_ctx, locale=locale,
        )
    acting_context = write_context(
        still=still, people=people, idea=idea, duration_s=duration_s,
        intent=intent, persona=persona, direction=direction, avoid=avoid, style=style,
    )
    out = await polish(
        ollama, segments=segments, context=context, acting_context=acting_context,
        duration_s=duration_s, model=model, num_ctx=num_ctx, instrument=instrument, style=style,
    )
    out["stages"] = {**stages, **out["stages"]}
    out["instrument"] = instrument
    return out

"""Turn a finished still into a MiniMax H3 image-to-video prompt, in stages.

**One call that both invents and formats lands on one shape.** The probe of
2026-10-10 (`private/muse_video_probe/`) asked a single call to read the still,
invent the motion and spell the H3 lines at once; every draft came back as
"still, cut, run with the prop". The fault check then rewrote any draft that
did not cut, so even a model that chose otherwise was pulled back.

So the work is split, and each stage sees only its own job:

1. look  — a vision call says plainly what is in the still (both people in a
           duet). Nothing is invented here.
2. ideas — one call per kind of scene. Each call is told only its own kind,
           so the ideas differ because their briefs differ, not because one
           call was asked for "three different ideas" (which converge).
3. pick  — the Showrunner chooses on the panel. Nothing here picks.
4. write — the chosen idea becomes shots as JSON. Code lays out the H3 text,
           so the fixed opening line and the shot headers cannot be misspelt.

The photo ledger is not written and the video API is not called.
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

H3_OPENING = (
    "For the target video, at 0.00 seconds into the target video, "
    "<Picture 1> (from [Shot 1]) is fully referenced."
)

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
    """A stage produced nothing usable. `message` is for the panel."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


def _who(people: list[dict[str, str]], *, hook: str | None = None, ground: bool = False) -> str:
    if not people:
        return _UNNAMED
    lines = []
    for p in people:
        looks = ", ".join(x for x in (p["hair"], p["eyes"]) if x)
        line = f"- {p['name']}" + (f" ({looks})" if looks else "")
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

_LOOK_SYSTEM = (
    "You are looking at one finished photograph. It will become the first frame "
    "of a short video, and the people planning that video cannot see it — they "
    "only get what you write. Say plainly, in English, what is in it: the place, "
    "each person (who is who, using the names you are given, and what they wear), "
    "what each body and face is doing, what the hands hold, where the light comes "
    "from, and what sits at the edges of the frame. Describe only what the "
    "picture shows. No guesses about what happens next, no praise."
)


async def look(
    ollama, *, image: bytes, people: list[dict[str, str]], model: str | None,
    num_ctx: int | None, fallback: str = "",
) -> tuple[str, bool]:
    """What the still shows. Returns (text, blind).

    A model that cannot read images answers blind rather than failing; then the
    shoot's own prompt stands in for the picture and `blind` says so out loud.
    """
    prompt = f"The people in it:\n{_who(people)}\n\nDescribe this photograph."
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
        "kind of scene — so yours is useful only if it is truly this kind, and "
        "only if it could not be pitched for any other photograph or any other "
        "person. A safe, generic idea is the one the director skips.\n\n"
        f"The kind you are pitching: {k['brief']}\n\n"
        "Answer with one JSON object and nothing else:\n"
        '{"title": ..., "story": ..., "beats": [...]}\n'
        f"title is one short line in {lang} the director reads first. story is "
        f"two or three sentences in {lang} saying what the viewer sees, in order, "
        "and why it is surprising or right for her. beats is the same video in "
        "English as a list of plain physical actions in order, each one thing a "
        "camera can see; the person who writes the shots works only from these."
    )
    direction = (
        "The director's note for this video. Honour what it asks for, read "
        f"through the kind you are pitching: {short.strip()}\n"
        "If this kind cannot deliver the note literally, find how it can deliver "
        "what the note is after — the director asked for that reading on purpose."
        if short.strip() else
        "The director left no note. What happens is yours to decide."
    )
    prompt = (
        f"Length: {duration_s} seconds.\n"
        f"Who is in it:\n{_who(people, hook=hook, ground=True)}\n\n"
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

# Labelled lines, not JSON (2026-10-10). Asked for {"shots": [...], "sound",
# "music"}, gemma kept closing the shots array after sound and music, so half the
# answers did not parse; a line per field has nothing to nest.
_WRITE_SYSTEM = """You write the shots of a short image-to-video clip for MiniMax H3. The opening line, the field names and the shot headers are added by code; you supply only what goes inside them.

The photograph is second 0. Shot 1 starts as exactly that picture and then moves. A new shot means the camera cuts; a continuous take is one shot however much happens in it, so do not split a take that never cuts.

Answer in this shape and nothing else, one field per line:
SHOT 1 AT 0: ...
SHOT 2 AT seconds: ...
SOUND: ...
MUSIC: ...

- SHOT: one paragraph in English on a single line. The number after AT is when the shot starts, in seconds; later shots are cuts and start before the clip ends. The video model has no memory between shots, so in each shot name every person on screen with her hair and eyes (by name when you are given one, otherwise by how she looks), say what her body and face do in order, and give the camera one move (static, push in, pull out, pan, truck, tilt, arc, tracking or handheld), its amplitude (small, medium or large), its speed (slow, medium or fast) and the motion that motivates it. Write a later shot so it reads after the words "the camera cuts to".
- SOUND: the physical sounds in order. No dialogue.
- MUSIC: the instrument you are given, its tempo, and whether it rises or falls."""


def write_prompt(
    *, still: str, people: list[dict[str, str]], idea: dict[str, Any],
    duration_s: int, instrument: str = "",
) -> str:
    beats = "\n".join(f"- {b}" for b in idea.get("beats") or [])
    kind = KINDS.get(str(idea.get("kind") or ""), {})
    return (
        f"Length: {duration_s} seconds.\n"
        f"Who is in it:\n{_who(people)}\n\n"
        f"What the photograph shows (second 0):\n{still.strip()}\n\n"
        + (f"Kind of scene: {kind['brief']}\n\n" if kind else "")
        + f"The idea the director chose, in order:\n{beats}\n\n"
        + (f"The music is played on: {instrument}" if instrument else
           "Choose one instrument for the music.")
    )


# The instrument is chosen before the shots are written. Asked inside the
# writing call — even told to take it from her life, or warned off stock
# choices — gemma4 answered "a solo cello" for 11 of 13 clips across four
# people. Asked on its own for a few candidates, it names instruments from her
# work and her likes, and one of the first three is drawn here.
_MUSIC_SYSTEM = (
    "You suggest the instrument for the score of a short video clip. The score "
    "should sound like the person in it: something from her work, what she "
    "likes, or the place she is in, so that hearing it tells you whose clip "
    "this is. Answer with five instruments, one per line, the one that "
    "belongs to her most first. Only the instrument names."
)


def parse_instruments(raw: str) -> list[str]:
    out: list[str] = []
    for line in (raw or "").splitlines():
        name = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("*").strip()
        # An instrument is a few words. Asked about an unnamed person it once
        # answered with a description of her ("A girl with dark hair, …").
        if name and len(name.split()) <= 4 and name.lower() not in {n.lower() for n in out}:
            out.append(name)
    return out


async def pick_instrument(
    ollama, *, people: list[dict[str, str]], idea: dict[str, Any],
    model: str | None, num_ctx: int | None, rng: random.Random | None = None,
) -> str:
    """One instrument for this clip, or "" to let the writer choose."""
    who = (
        f"Who is in it:\n{_who(people, hook='likes', ground=True)}\n\n" if people else ""
    )
    prompt = (
        f"{who}The clip: {idea.get('title') or ''}\n"
        + "\n".join(f"- {b}" for b in idea.get("beats") or [])
    )
    try:
        raw = await chain._call(
            ollama, system=_MUSIC_SYSTEM, prompt=prompt, model=model,
            images=None, num_ctx=num_ctx, think=False,
        )
    except chain.ChainError:
        return ""
    names = parse_instruments(raw)[:3]
    return (rng or random).choice(names) if names else ""


_SHOT_LINE = re.compile(r"^\s*\**\s*SHOT\s*(\d+)\s*(?:AT\s*([\d.]+)\s*s?)?\s*\**\s*:\s*(.+)$", re.IGNORECASE)
_FIELD_LINE = re.compile(r"^\s*\**\s*(SOUND|MUSIC)\s*\**\s*:\s*(.+)$", re.IGNORECASE)


def parse_shots(raw: str) -> dict[str, Any]:
    """The labelled-line answer as {"shots": [{"at", "text"}], "sound", "music"}.

    Falls back to a JSON object, in case the model answers in the old shape.
    """
    data: dict[str, Any] = {"shots": []}
    for line in (raw or "").splitlines():
        if m := _SHOT_LINE.match(line):
            data["shots"].append({"at": m.group(2), "text": m.group(3).lstrip("* ").strip()})
        elif m := _FIELD_LINE.match(line):
            data[m.group(1).lower()] = m.group(2).lstrip("* ").strip()
        elif data["shots"] and line.strip() and len(data) == 1:
            # A shot paragraph that wrapped onto the next line.
            data["shots"][-1]["text"] += " " + line.strip()
    if not data["shots"] and "{" in (raw or ""):
        return parse_json_object(raw)
    return data


def _stamp(seconds: float) -> str:
    whole, ms = divmod(round(seconds * 1000), 1000)
    return f"{whole // 60:02d}:{whole % 60:02d}.{ms:03d}"


def _times(raw: list[Any], duration_s: int) -> list[float]:
    """Start times for shots 2..n, kept inside the clip and in order.

    The model chooses them; a missing, late or out-of-order time is spread
    evenly instead of being sent back for another round.
    """
    n = len(raw)
    out: list[float] = []
    prev = 0.0
    for i, value in enumerate(raw):
        try:
            t = float(value)
        except (TypeError, ValueError):
            t = -1.0
        if not prev < t < duration_s:
            # Share what is left of the clip between this shot and the rest.
            t = round(prev + (duration_s - prev) / (n - i + 1), 3)
        out.append(t)
        prev = t
    return out


_CUT_LEAD = "the camera cuts to"
_CUT_AT_END = re.compile(r"[\s,;]*the camera cuts to[\s.:,]*$", re.IGNORECASE)
_CUT_IN_TEXT = re.compile(r"(?<=[.!?])\s+the camera cuts to\s+", re.IGNORECASE)


def layout(data: dict[str, Any], *, duration_s: int) -> str:
    """The H3 text from the shots JSON. Raises ValueError when there are no shots."""
    shots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for shot in data.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        # gemma sometimes nests sound and music inside the last shot.
        for key in ("sound", "music"):
            if shot.get(key) and not data.get(key):
                data = {**data, key: shot[key]}
        # A cut written inside a shot's paragraph is a new shot (2026-10-10:
        # "... The camera cuts to a close-up of Mio" in the middle of shot 1).
        text = str(shot.get("text") or "").strip()
        # …and one left dangling at the end of a shot, with the next shot's
        # header still to come ("…push in. the camera cuts to [Shot 2]").
        text = _CUT_AT_END.sub("", text)
        pieces = _CUT_IN_TEXT.split(text)
        for n, text in enumerate(p.strip() for p in pieces):
            # It also repeats shot 1 word for word as a second shot.
            if text and text not in seen:
                seen.add(text)
                shots.append({**shot, "text": text} if n == 0 else {"text": text})
    if not shots:
        raise ValueError("no shots")
    parts = [f"[Shot 1] {str(shots[0]['text']).strip()}"]
    times = _times([s.get("at") for s in shots[1:]], duration_s)
    for i, (shot, t) in enumerate(zip(shots[1:], times), start=2):
        text = str(shot["text"]).strip()
        if text.lower().startswith(_CUT_LEAD):
            text = text[len(_CUT_LEAD):].lstrip(" ,")
        parts.append(f"[Shot {i}] At {_stamp(t)}, {_CUT_LEAD} {text}")
    sound = str(data.get("sound") or "").strip() or "none"
    music = str(data.get("music") or "").strip() or "none"
    return (
        f"{H3_OPENING}\n\n"
        f"integrated_multimodal_description: {' '.join(parts)}\n\n"
        f"overall_soundscape: {sound}\n"
        f"non_diegetic_music: {music}"
    )


async def write(
    ollama, *, still: str, people: list[dict[str, str]], idea: dict[str, Any],
    duration_s: int, model: str | None, num_ctx: int | None, locale: str = "ja",
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """The chosen idea as an H3 prompt. One retry when the answer cannot be laid out."""
    instrument = await pick_instrument(
        ollama, people=people, idea=idea, model=model, num_ctx=num_ctx, rng=rng,
    )
    prompt = write_prompt(
        still=still, people=people, idea=idea, duration_s=duration_s,
        instrument=instrument,
    )
    raw = ""
    problem = ""
    for _ in range(2):
        ask = prompt if not problem else (
            f"{prompt}\n\nYour last answer could not be used ({problem}). "
            f"It was:\n{raw[:1500]}\n\nAnswer again in the SHOT / SOUND / MUSIC lines only."
        )
        try:
            raw = await chain._call(
                ollama, system=_WRITE_SYSTEM, prompt=ask, model=model,
                images=None, num_ctx=num_ctx, think=False,
            )
            data = parse_shots(raw)
            text = layout(data, duration_s=duration_s)
            return {
                "prompt": text, "shots": len(data.get("shots") or []),
                "instrument": instrument, "raw": raw,
            }
        except chain.ChainError:
            problem = "it was empty"
        except ValueError as exc:
            problem = str(exc) or "it had no SHOT lines"
    raise VideoError(
        "撮影文に清書できませんでした。もう一度押すか、別の案を選んでください。"
        if str(locale or "").startswith("ja") else
        "Could not write this idea as a prompt. Try again or pick another idea."
    )

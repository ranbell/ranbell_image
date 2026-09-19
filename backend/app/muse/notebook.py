"""Shot notebook — plain-language source of truth (not facets).

Used by the lead shoot (主演撮り) and the studio crew (制作スタッフ). Conversation
revises this notebook; craft TAGS/SCENE are woven from it (and replaced whole)
just before a take. Muse talk may read it; Script writes it. Crew also mirrors
PLAN/COSTUME into the notebook.
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterable
from typing import Any

# Garment vocabulary has one owner. `brief` imports `identity` and neither
# imports this module, so the edge is safe.
from . import brief
from ..tags import catalog as tag_catalog

SHOT_KEYS = (
    "atmosphere",
    "scene",
    # What is in frame besides her: the building behind, the extras around, the
    # props that have been placed. **On set this is called BG** (short for
    # background, and it goes out over the radio).
    #
    # While it did not exist, however often the director said 「後ろにあの建物」
    # ("that building behind her") or 「周りに他のレイヤーさん」 ("other cosplayers
    # around"), it had nowhere to go and vanished from the picture. In a real shoot
    # (Comiket) the director named the place four times and two of the three takes
    # had neither the building nor the crowd.
    #
    # The name was decided by measurement. Over 7 cases x 10 runs, `set`, `backdrop`
    # and `scenery` all failed to land and only `BG` did (44% -> 68%). `backdrop`
    # does not appear on a single image in this library, and `set_dressing`,
    # `extras` and `mob` are all zero. **Only the word actually used on set got
    # through.**
    "bg",
    # Where the light comes from and how hard it is. Its own field because it is
    # its own decision: the crewed studio has a seat that owns exposure and a
    # PLAN line that owns the intent, and the lead shoot (主演撮り) had neither —
    # 「逆光にして」 ("make it backlit")
    # could only land inside scene or atmosphere, both of which are rewritten
    # for other reasons, so it was gone again a turn later.
    "light",
    "frame",
    "wearing",
    "beat",
    # **The face had nowhere to go.** The clerk's contract said "put the face into
    # beat", so on a turn where the body does not move the expression was written
    # nowhere — the Showrunner (2026-08-29): "there is no expression in intent/note,
    # so unless beat reacts she is expressionless". In real data, the notebook's
    # `smiling warmly` never became a tag, while a `calm_expression` that was not in
    # the notebook came out of weave.
    #
    # The field name was decided by measurement (the same way as `BG`, 5 runs x 5
    # cases). `FACE`, `EXPRESSION` and `MOOD_FACE` were all 15/15 with 0/10 stray
    # writes — **unlike the background case, no difference shows**, so it matches
    # `expression`, which the crew (`facets`) already uses.
    "expression",
    "wearing_b",
    "beat_b",
    "expression_b",
)

META_KEYS = ("vibe", "standing")

_ALL_KEYS = SHOT_KEYS + META_KEYS
# Long enough to hold one whole shoot. At 12 the first half of a real shoot
# disappeared — the Comiket session had 21 turns of the director's lines and only
# the last 12 were kept, so "when did the place go in" could not be traced
# (restatements and folds included, one shoot runs to about 50 entries).
#
# The instrument panel still shows 12 (`MusePanel.vue`). **A screen that wants the
# most recent and a record to trace back through need different lengths.**
REWRITE_LOG_MAX = 60
_REWRITE_FIELDS = SHOT_KEYS + ("vibe",)


def blank(partner: bool = False) -> dict[str, Any]:
    nb = {
        "atmosphere": "",
        "scene": "",
        "bg": "",
        "light": "",
        "frame": "",
        "wearing": "",
        "beat": "",
        "expression": "",
        "wearing_b": "",
        "beat_b": "",
        "expression_b": "",
        "vibe": "",
        "standing": [],
        "rev": 0,
        "updated_at": 0.0,
    }
    if not partner:
        nb["wearing_b"] = ""
        nb["beat_b"] = ""
        nb["expression_b"] = ""
    return nb


def of(session: dict[str, Any]) -> dict[str, Any]:
    nb = session.get("notebook")
    if not isinstance(nb, dict) or not nb:
        nb = blank(partner=bool(str(
            (session.get("inputs") or {}).get("partner_preset") or ""
        ).strip()))
        session["notebook"] = nb
    for key in _ALL_KEYS:
        if key == "standing":
            nb.setdefault(key, [])
        else:
            nb.setdefault(key, "")
    nb.setdefault("rev", 0)
    nb.setdefault("updated_at", 0.0)
    return nb


def has_shot(nb: dict[str, Any]) -> bool:
    return any(str(nb.get(k) or "").strip() for k in (
        "scene", "bg", "frame", "wearing", "beat", "atmosphere", "light",
    ))


# ── The field contracts ─────────────────────────────────────────────────
# **Everyone who touches the notebook reads the same single definition.** What the
# measurement on 08-19 showed was not that a definition was missing but that **it
# was split three ways and the most accurate one was visible to nobody**:
#
#   her (DUET_TALK_OUTPUT)        FRAME: <camera / gaze>
#   compile (SCRIPTER_SYSTEM)     frame names ONE crop    <- says nothing about gaze
#   photo read (STILL_READ)       FRAME: camera and gaze
#   restate (_RESTATE_FIELDS)     …Crop plus gaze. Not where you are looking;
#                                 that is the frame.      <- accurate, but only ever
#                                                            seen on a restate turn
#
# What writes the notebook every turn is compile, and gaze ownership was not written
# there. So 「カメラ見て」 ("look at the camera") went into frame, beat kept its
# `looking at cake`, and weave took the more concrete of the two (beat). The
# Showrunner said it again three times.
#
# The fix is not to add a new rule. **Make the most accurate version the one source,
# and show the same thing to the readers and the writers alike.**
FIELD_CONTRACTS: dict[str, str] = {
    "atmosphere": (
        "the mood, and only the mood. No clock, no weather-as-hour, no "
        "objects, no place nouns."
    ),
    "scene": (
        "one specific place and the time of day. Not the light, not what she "
        "is doing, not the camera."
    ),
    "bg": (
        "what is in the picture besides her — the background actors (the "
        "extras, the crowd), the buildings, the set dressing. On set this is "
        "called BG. Not what she wears and not what she is holding: those are "
        "hers. How blurred it is is not here either — that is depth of field, "
        "and it belongs to FRAME with the rest of the camera."
    ),
    "light": (
        "the key and where it comes from, absolute: 'low sun from behind, "
        "hard rim'. Never a direction of change — no 'darker', no 'brighter'. "
        "Not the mood, not the place."
    ),
    "frame": (
        "the camera and where her eyes are pointed, as one story: 'wide shot, "
        "looking straight into the lens'. ONE crop — zoom/close/upper OR "
        "wide/full-body, never both `wide_shot` and `close_up` in the same "
        "frame — plus the gaze. Nothing about her hands or her clothes.\n"
        # In a real shoot (the swing, 2026-08-21), on the turn where the director
        # said 「カメラを少し上から」 ("the camera a little from above") compile
        # correctly wrote `high-angle`, and the restatement immediately after turned
        # it into `low-angle`. The reason field held this:
        #
        #   an understanding of "so the camera is up high" and … a reading that she
        #   is trying to match the composition as instructed (an angle looking up at
        #   the subject)
        #
        # **The camera position was understood correctly and only the word was
        # inverted.** On the next turn the director corrected it: "it is called from
        # above. It is not a low angle."
        #
        # The first fix was to explain that the camera height and the gaze run
        # opposite ways. That held only 5 times in 8, and 0/8 once her 「上目遣いす
        # ぎかな？」 ("is this too much of an upward glance?") entered the
        # conversation. In its own words: "the description `she looks UP into it` is
        # itself the noise. As long as **the words up and down are visual hooks**,
        # remove them thoroughly."
        #
        #   gaze written too (up/down included)      0/8
        #   position only                            7/8   <- this one
        #   position only + "they often run opposite" 0/8
        #
        # **Try to explain the mix-up and the words used to explain it become the
        # material for the mix-up.**
        "  The angle word names where the camera stands, never where she "
        "looks. Camera above her: `high_angle`. Camera below her: "
        "`low_angle`. Her eyes have no vote in it."
    ),
    "wearing": (
        "everything ON her body and nothing else — clothes, hair, "
        "accessories. A held prop is not worn; that belongs in beat."
    ),
    "beat": (
        "ONE posture stem — sitting / standing / kneeling / crouching — plus "
        "what the hands and the weight are doing, and anything she is holding. "
        "NOT where she is looking: that is the frame. NOT her face: that is "
        "expression."
    ),
    # **The face had nowhere to go.** The clerk's contract said "put the face into
    # beat", so on a turn where the body does not move the expression was written
    # nowhere. In real data, the notebook's `smiling warmly` never became a tag,
    # while a `calm_expression` that was not in the notebook came out of weave.
    "expression": (
        "her face — the mouth, the eyes, the brows. A mood she plays goes here, "
        "not in atmosphere: that one is the picture's mood, this one is hers."
    ),
}

_CONTRACT_ORDER = (
    "atmosphere", "scene", "bg", "light", "frame", "wearing", "beat",
    "expression",
)


# Some of these prompts speak TO her, so the same contract has to be sayable in
# the second person. One source, two renderings — never two texts to keep in
# step, which is the state that produced the disagreement in the first place.
_TO_HER = (
    ("where her eyes are pointed", "where your eyes are pointed"),
    ("where she is looking", "where you are looking"),
    ("what she is doing", "what you are doing"),
    ("she is holding", "you are holding"),
    ("ON her body", "ON your body"),
    ("her hands or her clothes", "your hands or your clothes"),
    ("the hands and the weight", "your hands and your weight"),
)


def contracts_block(
    keys: Iterable[str] | None = None, *, second_person: bool = False,
) -> str:
    """The field contracts, worded once, for any prompt that reads or writes.

    Handed to compile, weave, the still-read and her own review alike. When two
    seats disagree about which field owns the gaze, the shot stops moving and
    nobody reports an error — the direction simply lands in a field the
    renderer does not read, and the showrunner repeats himself into a room
    that has already written his words down somewhere useless.
    """
    names = list(keys) if keys else list(_CONTRACT_ORDER)
    lines = [
        "WHAT EACH PART OF THE NOTEBOOK IS "
        "(one definition, the same for everyone who reads or writes it):",
    ]
    for key in names:
        text = FIELD_CONTRACTS.get(key)
        if not text:
            continue
        if second_person:
            for a, b in _TO_HER:
                text = text.replace(a, b)
        lines.append(f"- {key.upper()} — {text}")
    return "\n".join(lines)


def render(nb: dict[str, Any], *, name_a: str = "", name_b: str = "") -> str:
    """Human / model facing dump.

    **Put the letter next to the name.** With a partner in frame the headings are
    written by name while the field names are the letters `WEARING` /
    `WEARING_B`, so the model called the other person "A" — live (`61db2bd6`) a
    fold-in wrote `beat_b: standing behind A`. `A` is not a tag so it never
    reaches the picture, but as an instruction it is dirt.
    The Showrunner: "you just have to write `Mio (Actress A)` up front."
    """
    two = bool(name_b or str(nb.get("wearing_b") or "").strip()
               or str(nb.get("beat_b") or "").strip())
    a = name_a or "Muse A"
    if two:
        a = f"{a} (Actress A)"
    lines = [
        f"ATMOSPHERE:\n{str(nb.get('atmosphere') or '').strip() or '(empty)'}",
        f"SCENE:\n{str(nb.get('scene') or '').strip() or '(empty)'}",
        f"BG:\n{str(nb.get('bg') or '').strip() or '(empty)'}",
        f"LIGHT:\n{str(nb.get('light') or '').strip() or '(empty)'}",
        f"FRAME:\n{str(nb.get('frame') or '').strip() or '(empty)'}",
        f"{a} WEARING:\n{str(nb.get('wearing') or '').strip() or '(empty)'}",
        f"{a} BEAT:\n{str(nb.get('beat') or '').strip() or '(empty)'}",
        f"{a} EXPRESSION:\n{str(nb.get('expression') or '').strip() or '(empty)'}",
    ]
    if two:
        b = f"{name_b or 'Muse B'} (Actress B)"
        lines += [
            f"{b} WEARING:\n{str(nb.get('wearing_b') or '').strip() or '(empty)'}",
            f"{b} BEAT:\n{str(nb.get('beat_b') or '').strip() or '(empty)'}",
            f"{b} EXPRESSION:\n{str(nb.get('expression_b') or '').strip() or '(empty)'}",
        ]
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        lines.append(f"VIBE:\n{vibe}")
    standing = [str(s).strip() for s in (nb.get("standing") or []) if str(s).strip()]
    if standing:
        lines.append("STANDING:\n" + "\n".join(f"- {s}" for s in standing[:5]))
    return "\n\n".join(lines)


def summary_for_muse(nb: dict[str, Any], *, name_a: str = "", name_b: str = "") -> str:
    """Shorter block for talk context (English labels; values may be EN)."""
    parts: list[str] = []
    for label, key in (
        ("Atmosphere", "atmosphere"),
        ("Place", "scene"),
        ("BG", "bg"),
        ("Light", "light"),
        ("Camera", "frame"),
    ):
        val = str(nb.get(key) or "").strip()
        if val:
            parts.append(f"{label}: {val}")
    w = str(nb.get("wearing") or "").strip()
    b = str(nb.get("beat") or "").strip()
    who = name_a or "Lead"
    if w or b:
        parts.append(f"{who} wearing: {w or '(unset)'}")
        parts.append(f"{who} beat: {b or '(unset)'}")
    wb = str(nb.get("wearing_b") or "").strip()
    bb = str(nb.get("beat_b") or "").strip()
    if name_b and (wb or bb):
        parts.append(f"{name_b} wearing: {wb or '(unset)'}")
        parts.append(f"{name_b} beat: {bb or '(unset)'}")
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        parts.append(f"Vibe: {vibe}")
    return "\n".join(parts)


# Longevity caps (plan: VIBE≤5 lines, STANDING≤5).
VIBE_MAX_LINES = 5
VIBE_MAX_CHARS = 400

# SHOT field contracts — short absolute phrases. Long densify prose belongs
# only in craft_scene. Polluted SCENE fields were how place changes froze:
# the model would rewrite tags/craft but leave a 60-word park paragraph in
# SCENE, and the next turn's Muse digest pulled the shoot back.
SCENE_MAX_CHARS = 120
ATMOSPHERE_MAX_CHARS = 100
# A key, a direction, and how hard it is. Longer than that and it has started
# describing the room instead of lighting it.
LIGHT_MAX_CHARS = 120
FRAME_MAX_CHARS = 160
WEARING_MAX_CHARS = 240
BEAT_MAX_CHARS = 240

BG_MAX_CHARS = 240

_SHOT_FIELD_CAPS: dict[str, int] = {
    "scene": SCENE_MAX_CHARS,
    "bg": BG_MAX_CHARS,
    "atmosphere": ATMOSPHERE_MAX_CHARS,
    "light": LIGHT_MAX_CHARS,
    "frame": FRAME_MAX_CHARS,
    "wearing": WEARING_MAX_CHARS,
    "wearing_b": WEARING_MAX_CHARS,
    "beat": BEAT_MAX_CHARS,
    "beat_b": BEAT_MAX_CHARS,
}

# Gaze used to be scrubbed out of BEAT here with a keyword regex. It is a rule
# in SCRIPTER_SYSTEM now: the scripter reads the conversation and writes the
# frame as one camera story. A word list cannot tell "見上げる" ("look up") the pose
# from "見上げる" the lens, and every phrase it missed shipped anyway.

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


def _cap_lines(text: str, *, max_lines: int, max_chars: int) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    body = "\n".join(lines[:max_lines]).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip()
    return body


def _cap_phrase(text: str, *, max_chars: int) -> str:
    """Keep a short absolute phrase; cut on a word boundary when possible."""
    body = re.sub(r"\s+", " ", str(text or "").strip())
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.;:")


_TIME_TOKEN_RE = re.compile(
    r"\b(dawn|dusk|sunrise|sunset|twilight|noon|midnight|"
    r"morning|evening|afternoon|night)\b",
    re.I,
)
_NO_ITEM_RE = re.compile(r"\b(?:no|without)\s+[a-z][a-z0-9_]*\b", re.I)


def coerce_plain_phrase(val: Any) -> str:
    """Notebook fields are short English phrases. Lists join; dicts/reprs drop."""
    if val is None or isinstance(val, bool):
        return ""
    if isinstance(val, dict):
        return ""
    if isinstance(val, (list, tuple)):
        parts = [coerce_plain_phrase(x) for x in val]
        return ", ".join(p for p in parts if p)
    text = str(val).strip()
    if not text:
        return ""
    low = text.lower()
    if low in ("unchanged", "変更なし", "同じ", "そのまま", "-", "none", "なし"):
        return ""
    if text.startswith("{") and text.endswith("}"):
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return ""
        if inner.startswith(("'", '"')) and inner.endswith(("'", '"')):
            return inner[1:-1].strip()
        bits = [coerce_plain_phrase(p.strip().strip("'\"")) for p in inner.split(",")]
        return ", ".join(p for p in bits if p)
    return text


def wearing_tokens(text: str) -> set[str]:
    """English-ish tokens from a wearing/beat phrase (for craft consistency).

    Built from the field itself — not from a situation vocabulary list.
    ``no hat`` / ``without hat`` do not keep the noun (danbooru ``no_hat`` is
    not a removal). Comma-separated items are tokenised separately so
    ``straw hat, cardigan`` does not mint ``hat_cardigan``.
    """
    out: set[str] = set()
    for chunk in re.split(r"[,，、]", str(text or "")):
        raw = _NO_ITEM_RE.sub(" ", chunk.lower())
        if not raw.strip():
            continue
        out |= set(_TOKEN_RE.findall(raw))
        words = re.findall(r"[a-z][a-z0-9]+", raw)
        for i in range(len(words) - 1):
            pair = (words[i], words[i + 1])
            # `straw hat` is a garment; `the metal`, `while staring`, `on the`
            # are grammar. Pairing across a function word never names a thing,
            # and these are read back as items that must never return.
            if pair[0] in _STRUCK_NOISE or pair[1] in _STRUCK_NOISE:
                continue
            out.add(f"{pair[0]}_{pair[1]}")
    return out


def split_atmosphere_time(atmosphere: str, scene: str) -> tuple[str, str]:
    """Move clock words out of mood into scene. Mood stays feeling-only."""
    atm = str(atmosphere or "").strip()
    sc = str(scene or "").strip()
    found = [m.group(0).lower() for m in _TIME_TOKEN_RE.finditer(atm)]
    if not found:
        return atm, sc
    mood = _TIME_TOKEN_RE.sub(" ", atm)
    mood = re.sub(r"[\s,;]+", " ", mood).strip(" ,;.")
    hour = found[-1]
    if hour and hour not in sc.lower():
        sc = f"{sc} at {hour}".strip() if sc else hour
    return mood, sc



# The four postures the scripter's beat contract names (`chain.SCRIPTER_SYSTEM`:
# "Beat always names ONE posture stem"). Not a vocabulary of situations — the
# closed set the contract is written against, so the tag bag can be held to it.
POSTURE_STEMS: dict[str, tuple[str, ...]] = {
    "sitting": ("sitting", "sits", "seated", "sit", "座"),
    "standing": ("standing", "stands", "stand", "立"),
    "kneeling": ("kneeling", "kneels", "kneel", "seiza", "跪", "正座"),
    "squatting": ("crouching", "crouch", "squatting", "squat", "しゃが"),
}


def posture_stem(beat: str) -> str:
    """The danbooru stem the beat names, or "" when it names none."""
    text = str(beat or "").lower()
    for tag, words in POSTURE_STEMS.items():
        if any(w in text for w in words):
            return tag
    return ""


def ensure_beat_leads_scene(
    scene: str, *, beat: str, beat_b: str = "",
) -> str:
    """If craft_scene forgot the notebook beat, put the body first.

    Why: Weave was rewarded for long air/cloth prose and often buried or
    omitted the Showrunner's posture. pose_intent stored the beat but never
    reached Comfy — only craft_scene did. This is the hard floor: the beat
    the notebook already named must open the prose the sampler reads.
    """
    body = str(scene or "").strip()
    leads: list[str] = []
    for raw in (beat, beat_b):
        phrase = coerce_plain_phrase(raw)
        if not phrase:
            continue
        stem = posture_stem(phrase)
        low = body.lower()
        # Already present as stem or as a clear substring of the beat phrase.
        if stem and stem in low:
            continue
        key = phrase.lower()
        if len(key) >= 8 and key[:40] in low:
            continue
        # One short English lead-in the sampler can act on.
        leads.append(phrase.rstrip(".") + ".")
    if not leads:
        return body
    head = " ".join(leads)
    return f"{head} {body}".strip() if body else head


def _same_garment(a: str, b: str) -> bool:
    """Two head nouns naming one thing. `dress` and `sundress` are one dress.

    The suffix rule is guarded at four characters so `top` does not swallow
    `laptop` — and so the shorter head has to be a real garment word before it
    is allowed to absorb a longer one.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.endswith(short)


def garment_matches(wearing: str, name: str) -> list[str]:
    """Items in WEARING that the showrunner means by `name`.

    Zero means she is not wearing it. Two means the ask has no single referent
    — which is the moment to put the question back to the showrunner instead of
    guessing, because guessing here undresses her wrongly and silently.
    """
    head = brief.garment_head(name)
    if not head:
        return []
    out: list[str] = []
    for item in str(wearing or "").split(","):
        item = item.strip()
        if item and _same_garment(brief.garment_head(item), head):
            out.append(item)
    return out


def beat_without(beat: str, garment: str) -> str:
    """The same action, minus the part that needs a garment she took off.

    Clothes and action are one thing to everyone except the notebook: BEAT
    reads `standing, clutching the hem of her skirt`, the skirt comes off, and
    the hem is still in her hand. The posture stem always survives — losing a
    garment is not a reason to stop standing.
    """
    words = {w for w in re.split(r"[_\s-]+", str(garment or "").lower()) if len(w) > 2}
    head = brief.garment_head(garment)
    if head:
        words.add(head)
    if not words:
        return str(beat or "")
    kept: list[str] = []
    for clause in str(beat or "").split(","):
        text = clause.strip()
        if not text:
            continue
        low = text.lower()
        # A clause that holds the posture is never dropped, even when it also
        # names the garment: `sitting on her coat` still says she is sitting.
        if any(w in low for w in words) and not posture_stem(text):
            continue
        kept.append(text)
    if not kept:
        return posture_stem(beat) or str(beat or "")
    return ", ".join(kept)


def shot_tokens(nb: dict[str, Any]) -> set[str]:
    """Everything the shot currently says, as tokens."""
    out: set[str] = set()
    for key in SHOT_KEYS:
        out |= wearing_tokens(str(nb.get(key) or ""))
    return out


def struck_tokens(session: dict[str, Any]) -> set[str]:
    """What must not come back — minus whatever the shot now says.

    `struck` is append-only, and it is read as "never restore this". That is
    right for a garment the showrunner took off and wrong for everything that
    legitimately comes and goes: stand up and `sitting` is struck, so the next
    「座って」 ("sit down") is fighting a filter, and `filter_weave_tags` strips
    the very tag the notebook just asked for. The notebook is the shot — anything
    it currently names is by definition not struck.
    """
    live = shot_tokens(of(session)) if isinstance(session, dict) else set()
    out: set[str] = set()
    for item in session.get("struck") or []:
        s = str(item or "").strip()
        if not s:
            continue
        out.add(s.lower().replace(" ", "_"))
        out |= wearing_tokens(s)
    return {t for t in out if len(t) >= 3 and t not in live}


def live_struck(session: dict[str, Any]) -> list[str]:
    """The struck list as shown to a model: same pruning, original wording."""
    live = shot_tokens(of(session)) if isinstance(session, dict) else set()
    out: list[str] = []
    for item in session.get("struck") or []:
        s = str(item or "").strip()
        if not s:
            continue
        key = s.lower().replace(" ", "_")
        if key in live or (wearing_tokens(s) & live):
            continue
        out.append(s)
    return out


_STRUCK_NOISE = {
    "and", "with", "the", "her", "his", "she", "for", "from", "over", "under",
    "on", "at", "in", "of", "to", "a", "an", "while", "nothing", "into",
    "onto", "that", "this", "its", "out", "off", "up", "down", "by",
}


def record_struck_from_wearing(
    session: dict[str, Any], *, prev_wearing: str, new_wearing: str,
) -> list[str]:
    """Tokens dropped from wearing stay struck so still-read / weave cannot restore them."""
    return record_struck_tokens(
        session, prev=prev_wearing, new=new_wearing, min_len=3,
    )


def record_struck_tokens(
    session: dict[str, Any], *, prev: str, new: str, min_len: int = 3,
) -> list[str]:
    """Tokens that left a shot phrase stay struck (clothes, place, hour, pose, crop)."""
    dropped = wearing_tokens(prev) - wearing_tokens(new)
    added = sorted(
        t for t in dropped if t not in _STRUCK_NOISE and len(t) >= min_len
    )
    if not added:
        return []
    prior = [str(s) for s in (session.get("struck") or []) if str(s).strip()]
    have = {s.lower().replace(" ", "_") for s in prior}
    for t in added:
        if t not in have:
            prior.append(t)
            have.add(t)
    session["struck"] = prior
    return added


def tag_mentions_struck(tag: str, struck: set[str]) -> bool:
    from .identity import bare_tag

    bare = bare_tag(tag)
    if not bare:
        return False
    if bare in struck:
        return True
    for s in struck:
        if len(s) < 3:
            continue
        # The struck word has to BE the tag, or be its head noun — the last
        # component. English compounds put the head on the right, so `blouse`
        # rules out `white_blouse`, and `hat` rules out `straw_hat`.
        #
        # Matching any component (`s in bare.split("_")`) was the old rule and
        # it poisoned whole sessions. `wearing_tokens` splits a garment phrase
        # into its words, so taking off one "stylish white blouse" struck
        # `blouse`, `white` AND `stylish` — and struck `white` then blocked
        # `white_shirt`, `white_socks`, and her `white_hair`. A modifier is not
        # the thing that was removed.
        if bare == f"no_{s}" or bare.endswith(f"_{s}"):
            return True
    return False


def garment_lifts_struck(garment: str, struck_token: str) -> bool:
    """Does naming this garment lift this struck entry? Deliberately generous.

    The mirror of :func:`tag_mentions_struck`, and **not the same rule** — the
    two directions are not symmetric:

    * blocking is destructive, so it matches narrowly (head noun only);
    * freeing is recoverable, so it matches on any word part.

    Taking off a "stylish white blouse" strikes `blouse`, `white`, `stylish`.
    Blocking on `white` would rule out `white_shirt` and her `white_hair`, so
    it must not. But once she is dressed in a `white blouse` again, every one
    of those entries should go — including the modifiers, which no narrow rule
    would ever reach. Leaving `white` struck forever is the failure the
    wardrobe button exists to undo.
    """
    from .identity import bare_tag

    bare = bare_tag(garment) or str(garment or "").strip().lower().replace(" ", "_")
    s = str(struck_token or "").strip().lower().replace(" ", "_")
    if not bare or len(s) < 3:
        return False
    return bare == s or bare.endswith(f"_{s}") or s in bare.split("_")


_QUALITY_TAG_KEEP = {
    "knit", "drape", "folds", "fabric", "grain", "bokeh", "depth",
    "cinematic", "soft", "light", "shadow", "texture", "skin", "air",
}


def filter_weave_tags(
    tags: str, *, wearing: str, scene: str, beat: str, struck: set[str],
    wearing_b: str = "", beat_b: str = "", frame: str = "",
    banned: set[str] | None = None,
) -> str:
    """Drop struck / banned tokens. Shot nouns are reconciled by sibling filters.

    ``wearing`` / ``scene`` / ``beat`` / ``frame`` stay on the signature so
    callers can pass the whole notebook context in one place; garment, crop,
    and notebook-fight passes live in ``scrub_craft_tags``. Banned used to be
    enforced only on seat turns — weave could write a refused tag straight
    back into the bag the Showrunner had already struck from the picture.
    """
    _ = (wearing, scene, beat, wearing_b, beat_b, frame)
    refuse = {str(t).strip() for t in (banned or ()) if str(t).strip()}
    kept: list[str] = []
    seen: set[str] = set()
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        from .identity import bare_tag
        key = bare_tag(tok)
        if not key or key in seen:
            continue
        if struck and tag_mentions_struck(tok, struck):
            continue
        if key in refuse:
            continue
        seen.add(key)
        kept.append(tok)
    return ", ".join(kept)


def stale_wearing_tags(
    *, prev_wearing: str, new_wearing: str, tags: str,
) -> list[str]:
    """Tag tokens dropped from wearing that still appear in the craft bag."""
    dropped = wearing_tokens(prev_wearing) - wearing_tokens(new_wearing)
    if not dropped:
        return []
    have = wearing_tokens(tags.replace(",", " "))
    noise = {"and", "with", "the", "her", "his", "she", "for", "from"}
    return sorted(t for t in dropped if t in have and t not in noise and len(t) >= 4)


def drop_crops_not_in_frame(tags: str, *, frame: str) -> str:
    """One crop per picture, and FRAME owns which one.

    **Two hand-written families were removed.** This used to hold
    `_WIDE_CROP_TAGS` (5 words) and `_CLOSE_CROP_TAGS` (5 words) and could not
    work without knowing which family a tag belonged to. The Showrunner
    (2026-08-30): "drop the whole list of words you found".

    "Is this a crop tag?" is answered by `framing_from_phrase` itself — it covers
    all ten hand-written words and answers `auto` for angles (`from_above`,
    `dutch_angle`, `pov`, `profile`). There is now one source for crop synonyms.

    Checked against 30 stored weave outputs: **30/30 identical to the old code**.
    Feeding deliberately conflicting bags apart, three cases change, and in two of
    them the old code was the one dropping things:

        FRAME `close, upper body` / bag `close_up, wide_shot, full_body`
            old  keeps `close_up` — FRAME says upper body yet a face crop survives
            new  drops all three; `assemble_positive` mints it again from FRAME

        FRAME empty / bag `wide_shot, close_up`
            old  drops both — **a picture with no crop at all**
            new  keeps whichever came first (no contradiction, nothing thrown away)

        FRAME `close, upper body` / bag `establishing_shot, upper_body`
            old dropped it, new let it through — so `establishing` was taught to
            `framing_from_phrase`. Writing "establishing shot" into FRAME now
            reads (it did not before)
    """
    from .identity import bare_tag, framing_from_phrase

    crop = framing_from_phrase(frame)
    kept: list[str] = []
    first = ""
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        mine = framing_from_phrase(bare_tag(tok))
        if mine != "auto":
            if crop != "auto":
                if mine != crop:
                    continue
            elif first and mine != first:
                continue
            else:
                first = first or mine
        kept.append(tok)
    return ", ".join(kept)


def drop_garments_not_in_wearing(tags: str, *, wearing: str, wearing_b: str = "") -> str:
    """Drop leftover garment tags whose last token left wearing."""
    allowed = wearing_tokens(wearing) | wearing_tokens(wearing_b)
    if not allowed:
        return tags
    leftover = {
        "hat", "cardigan", "coat", "jacket", "hoodie", "cape",
        "umbrella", "scarf", "glasses", "sunglasses",
    }
    kept: list[str] = []
    from .identity import bare_tag
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = bare_tag(tok)
        last = key.split("_")[-1] if key else ""
        if last in _QUALITY_TAG_KEEP:
            kept.append(tok)
            continue
        if last in leftover and last not in allowed and key not in allowed:
            continue
        kept.append(tok)
    return ", ".join(kept)


def _missing_wearing_items(
    tags: str, *, wearing: str, wearing_b: str = "",
    struck: set[str] | None = None, banned: set[str] | None = None,
) -> list[str]:
    """Garment heads the notebook names that the bag forgot."""
    from .identity import tag_names

    have = set(tag_names(tags))
    have |= {t for tag in have for t in wearing_tokens(tag)}
    gone = set(struck or ()) | {
        str(t).strip().lower().replace(" ", "_") for t in (banned or ()) if str(t).strip()
    }
    missing: list[str] = []
    wardrobes = " , ".join(x for x in (wearing, wearing_b) if x)
    for item in re.split(r"[,，、;]", wardrobes):
        tokens = wearing_tokens(item)
        if not tokens or tokens & gone:
            continue
        if tokens & have:
            continue
        tag = re.sub(r"\s+", "_", item.strip().lower())
        tag = re.sub(r"[^a-z0-9_-]", "", tag).strip("_-")
        if tag and len(tag) >= 3:
            missing.append(tag)
            have.add(tag)
            have |= tokens
    return missing


def reconcile_wardrobe_tags(
    tags: str, *, wearing: str, wearing_b: str = "",
    struck: set[str] | None = None, banned: set[str] | None = None,
    sides: tuple[str, str] = ("", ""),
    partner: bool = False,
) -> tuple[str, tuple[str, str]]:
    """One wardrobe pass: refuse → aliases → leftovers → inject forgotten clothes.

    Scrub and `_apply_compiled_craft` used to run these as three separate looks
    at the same wearing line; the bag drifted between them. One function, one
    order, both callers.
    """
    from .identity import bare_tag

    struck = set(struck or ())
    banned_set = set(banned or ())
    tags = filter_weave_tags(
        tags, wearing=wearing, scene="", beat="", struck=struck,
        wearing_b=wearing_b, beat_b="", frame="", banned=banned_set,
    )
    side_a, side_b = str(sides[0] or ""), str(sides[1] or "")

    if not partner:
        gone = garment_aliases(tags, wearing)
    elif side_a and side_b:
        gone = garment_aliases(side_a, wearing) | garment_aliases(side_b, wearing_b)
    else:
        heads = wardrobe_heads(wearing) | wardrobe_heads(wearing_b)
        gone = {
            t for t in (garment_aliases(tags, wearing) | garment_aliases(tags, wearing_b))
            if brief.garment_head(t) not in heads
        }

    def _without(bag: str) -> str:
        return ", ".join(
            p.strip() for p in str(bag or "").split(",")
            if p.strip() and bare_tag(p) not in gone
        )

    if gone:
        tags = _without(tags)
        side_a, side_b = _without(side_a), _without(side_b)

    tags = drop_garments_not_in_wearing(tags, wearing=wearing, wearing_b=wearing_b)
    # **With two people, look at each side separately.** `_missing_wearing_items`
    # decides "already there" by word overlap, so while Mio wears a
    # `light_blue_dress`, Sumire's `pale blue dress` is judged "already covered"
    # because `dress` and `blue` have appeared — **the second outfit can never come
    # back.**
    #
    # Measured (`94b4fc9f`, 2026-08-28): on the take after the Showrunner said
    # 「すみれちゃんは黒のカクテルドレス」 ("Sumire in a black cocktail dress"),
    # Sumire's line came out as
    # `Sumire is blonde_hair, braid, long_hair, green_eyes, medium_breasts, slim,`
    # — **with no clothing at all**. This was a defect that had been fixed once;
    # the old
    # `_missing_wearing_tags`'s docstring records that "clothes forgotten for the
    # partner alone never come back". Moving it here dropped that lesson.
    if partner and (side_a.strip() or side_b.strip()):
        def _fresh(side: str, items: list[str]) -> list[str]:
            """Never add a slot that person already wears — one garment per name.

            Adding `black_cocktail_dress` where `black_dress` already sits shows
            the sampler two black garments.
            """
            from .identity import tag_names

            worn = {brief.garment_head(t) for t in tag_names(side)}
            return [t for t in items if brief.garment_head(t) not in worn]

        miss_a = _fresh(side_a, _missing_wearing_items(
            side_a, wearing=wearing, struck=struck, banned=banned_set))
        miss_b = _fresh(side_b, _missing_wearing_items(
            side_b, wearing=wearing_b, struck=struck, banned=banned_set))
        if miss_a:
            side_a = ", ".join([p.strip() for p in side_a.split(",") if p.strip()] + miss_a)
        if miss_b:
            side_b = ", ".join([p.strip() for p in side_b.split(",") if p.strip()] + miss_b)
        missing = miss_a + [m for m in miss_b if m not in miss_a]
    else:
        missing = _missing_wearing_items(
            tags, wearing=wearing, wearing_b=wearing_b,
            struck=struck, banned=banned_set,
        )
    if missing:
        parts = [p.strip() for p in tags.split(",") if p.strip()]
        tags = ", ".join(parts + [m for m in missing if m not in parts])
    return tags, (side_a, side_b)


def wardrobe_heads(wearing: str) -> set[str]:
    """The head noun of every item she has on — what her clothes ARE."""
    return {
        brief.garment_head(i)
        for i in re.split(r"[,，、;]", str(wearing or "")) if i.strip()
    } - {""}


def garment_aliases(tags: str, wearing: str) -> set[str]:
    """Tags that rename a garment her wardrobe has already named.

    One garment under three names, measured on a live W take: WEARING read
    `blue sleeveless gown` and the woven bag came back with `gown`,
    `blue_dress` **and** `sleeveless_dress`. To the sampler that is three
    garments, and with two people in frame the two spare ones land on whoever
    is nearest — which is how the black dress and the blue one swapped girls.

    A tag is a rename when all three hold: it is clothing, it borrows a word
    from one of her wardrobe items, and its head noun is not the head noun of
    anything she has on. `black_dress` beside `black cocktail dress` keeps the
    head noun and stays. `blue_dress` beside `blue sleeveless gown` does not.

    Scope is one person's wardrobe. Handed both girls' bags at once this would
    read the other's clothes as her renames.
    """
    items = [i.strip() for i in re.split(r"[,，、;]", str(wearing or "")) if i.strip()]
    if not items:
        return set()
    from .identity import bare_tag

    heads = wardrobe_heads(wearing)
    words: set[str] = set()
    for item in items:
        words |= {w for w in re.split(r"[_\s-]+", item.lower()) if w}
    words -= heads
    if not words:
        return set()
    out: set[str] = set()
    for part in str(tags or "").split(","):
        tag = bare_tag(part)
        # Only clothing is considered, so `blue_sky` beside a blue gown is not
        # read as her dress under another name.
        if not tag or tag_catalog.get_tag_axis(tag) != "clothing":
            continue
        if brief.garment_head(tag) in heads:
            continue
        if {w for w in tag.split("_") if w} & words:
            out.add(tag)
    return out


# The shapes in which the notebook says she is looking into the lens.
# `FIELD_CONTRACTS` states plainly that the gaze belongs to frame, and that
# ownership was not reaching the tags.
_EYES_ON_LENS_RE = re.compile(
    r"(?i)looking_?at_?viewer|into the lens|at the lens|at the camera|"
    r"eye contact|カメラ目線|レンズを見|こっちを見"
)

# Only the slots that may be narrowed to one. **The hour (`time_of_day`) and the
# room are not included** — an overlay such as `night, twilight, evening` is
# something weave writes on purpose, and choosing the wrong one to keep changes the
# light. Start narrow here.
_ONE_ONLY_SLOTS = (
    "camera_distance", "camera_pitch", "camera_side",
    "gaze_target", "gaze_pitch", "eyes", "posture",
)


def drop_tags_that_fight_the_notebook(
    tags: str, *, frame: str, beat: str, beat_b: str = "",
) -> str:
    """Drop woven tags that contradict the document of record head-on.

    **There was nowhere the two were compared.** `scrub_craft_tags` takes six
    notebook fields yet only ever looked at struck, wearing and the crop;
    `scene`, `beat` and `beat_b` went unused. The result, live (`42b55492`):

        notebook frame  close-up, looking straight into the lens
        tags            closed_eyes, eyes_closed
        prose           Her gaze remains fixed forward, eyes wide and glassy

    **Inside one prompt, the tags and the prose faced opposite ways.** The crop
    was tripled too: `close-up` / `close_up` / `face_focus`.

    Two things only:

    1. **The gaze belongs to frame.** If it says she is looking into the lens,
       no closed-eye word survives
    2. **One word per slot.** Reuse `tags.conflict`'s slots and **keep whichever
       the notebook names** (the first one when it names neither)
    """
    from ..tags import conflict
    from .identity import bare_tag

    said = " ".join(x for x in (frame, beat, beat_b) if x).lower()
    eyes_on_lens = bool(_EYES_ON_LENS_RE.search(frame or ""))

    kept: list[str] = []
    taken: dict[str, str] = {}          # slot -> the bare tag that was kept
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = bare_tag(tok)
        if eyes_on_lens and key in conflict.SLOTS["eyes"] and "closed" in key:
            continue
        slot = conflict.slot_of(key)
        if slot in _ONE_ONLY_SLOTS:
            first = taken.get(slot)
            if first is None:
                taken[slot] = key
            elif key != first:
                # Whichever the notebook names wins; failing that, whichever
                # came first
                mine, theirs = key.replace("_", " "), first.replace("_", " ")
                if mine in said and theirs not in said:
                    kept = [k for k in kept if bare_tag(k) != first]
                    taken[slot] = key
                else:
                    continue
        kept.append(tok)
    return ", ".join(kept)


#: What goes in each person's box. **One to one with the notebook's fields.**
PERSON_BOX_FIELDS = ("wearing", "beat", "face")


def _phrases(text: str, *, gone: set[str] | None = None) -> list[str]:
    """Split on commas and clean, nothing more. **Never crush a phrase into one
    word.**

    The notebook already holds comma-separated phrases (`turquoise one-piece
    dress, small_earrings`). Crushing that into a single token makes
    `turquoise_one-piece_dress` — **a word nobody knows**. Live (`8c48e8cb`) weave
    did exactly that and the noun at the end of the phrase fell out of the
    picture; the Showrunner: "the outfit keeps changing — the prompt side lacks
    precision".

    What this studio measured is that natural phrasing renders far better. Phrases
    stay phrases.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,，、;]", str(text or "")):
        item = re.sub(r"\s+", " ", raw).strip().strip(".")
        if not item:
            continue
        key = item.lower().replace(" ", "_")
        if key in seen:
            continue
        if gone and (key in gone or wearing_tokens(item) & gone):
            continue
        seen.add(key)
        out.append(item)
    return out


def mint_person_box(
    nb: dict[str, Any], *, partner: bool = False,
    struck: set[str] | None = None, banned: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Mint a box per person from the notebook. **No LLM involved.**

    The Showrunner (2026-08-31): "each of them should have her own wardrobe and
    her own pose", "A and B's actions have to survive to the end —
    **letting weave interpret them breaks it**".

    The notebook has been per-person from the start (`wearing`/`wearing_b`,
    `beat`/`beat_b`, `expression`/`expression_b`), yet craft stirred them into one
    flat bag and guessed the owner back at the end. All five breakages in the
    ten-stage record (`db6a1f7`) follow from that:

        2c  the one-word-per-slot rule throws away one of the two poses
        10  `placed` is global, so **only one person may hold a given word**
        3   her own review reads the flat bag and drops the partner's action
        8   a nounless fragment gets pasted to the head of the prose
        1   weave writes the notebook's phrase with a word missing

    **Do not stir them together and none of it happens.**

    Expression gets a word even when empty — the Showrunner: "with two of them you
    have to manage the feelings too or they go blank. **They will not write it
    without a box.**" The notebook's `atmosphere` is the fallback.
    """
    gone = {str(t).strip().lower().replace(" ", "_") for t in (struck or ())}
    gone |= {str(t).strip().lower().replace(" ", "_") for t in (banned or ())}
    gone = {g for g in gone if g}
    mood = _phrases(str((nb or {}).get("atmosphere") or ""))[:1]
    people: list[dict[str, Any]] = []
    sides = [("wearing", "beat", "expression")]
    if partner:
        sides.append(("wearing_b", "beat_b", "expression_b"))
    for wear_k, beat_k, face_k in sides:
        face = _phrases(str((nb or {}).get(face_k) or ""))
        people.append({
            "wearing": _phrases(str((nb or {}).get(wear_k) or ""), gone=gone),
            "beat": _phrases(str((nb or {}).get(beat_k) or "")),
            # **One word even when empty. Give them a box and they fill it.**
            "face": face or mood,
        })
    return people


def frame_wide_phrases(nb: dict[str, Any]) -> list[str]:
    """What belongs to nobody — camera, place, background, light, atmosphere.

    ``atmosphere`` was in the notebook but never reached the shared surface. It
    was used only as the fallback for an empty expression, which is the main
    reason mood kept escaping into craft_scene restatements (the Showrunner:
    "something is off").

    **``frame`` was not arriving either.** All that came through was the single
    word `framing_tags` normalised to (`full_body` and the like); what was
    actually written in the notebook never reached the picture. The Showrunner
    (2026-09-01): "focus might be better as camera work — `focus to …` and
    `long shot` probably belong in this box" — **a box you can write in is
    pointless if what you write does not arrive.**

    Put it first. The Showrunner: "priority is position within the prompt", and
    which of the two she moves closer to should bite before place or light.
    """
    out: list[str] = []
    for key in ("frame", "scene", "bg", "light", "atmosphere"):
        for phrase in _phrases(str((nb or {}).get(key) or "")):
            if phrase not in out:
                out.append(phrase)
    return out


def fight_craft_scene(
    nb: dict[str, Any], scene: str, *, struck: set[str] | None = None,
) -> str:
    """Drop **only the sentences that contradict the notebook head-on** from the
    prose.

    Tags get six stages of cross-checking; the prose was appended at the very end
    with no inspection at all. The damage is on record (`1564313`) — a hat the
    Showrunner had taken off travelled from her words into the prose and back
    into the picture.

    **The measure is not "share of unknown words".** The first version threw the
    whole paragraph away when most content words were absent from the notebook;
    measured over 13 real samples that dropped **4 of them (31%), and they were
    good prose**:

        "A wide shot shows her sitting on a park bench, her weight settled
         back against the wood. Her hands rest loosely…"   52% unknown → dropped

    `bench`, `weight` and `wood` are in the notebook, but `shows`, `settled`,
    `loosely` and `wide` count as unknown. **Prose is written in words the
    notebook does not have**, so that measure rejects prose as such.

    What is inspected is only **a sentence naming a garment she is not wearing**.
    "Does this look like clothing?" is answered by the catalogue (shared asset) —
    no new word list. Dropping is per sentence too: throwing the paragraph away
    takes the depth with it (learned on the 58 → 50 word case).
    """
    body = str(scene or "").strip()
    if not body:
        return ""
    # **A word found anywhere in the notebook is allowed.** `bench`, `desk` and
    # `window` are all "places" in the catalogue, but if `beat` or `bg` names one it
    # is the right word.
    licensed: set[str] = set()
    for key in SHOT_KEYS:
        text = str((nb or {}).get(key) or "")
        licensed |= wearing_tokens(text)
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text):
            licensed.add(word.lower())
            # **Split compound words.** The notebook holds `oversized_hoodie`
            # while the prose writes `hoodie`. Without splitting, **the same garment
            # counts as unauthorised**, and in real data the clothing sentence was
            # dropped in 9 of 13 cases.
            for part in re.split(r"[_-]+", word.lower()):
                if len(part) >= 3:
                    licensed.add(part)
    gone = {str(t).strip().lower().replace(" ", "_") for t in (struck or ()) if str(t).strip()}

    def _owned_axis(word: str) -> bool:
        """Clothes or place — **the two axes the notebook owns**. Everything else
        is the prose's to choose."""
        key = word.lower()
        if tag_catalog.get_tag_axis(key) in ("clothing", "location"):
            return True
        return any(key.endswith(s) for s in tag_catalog.CLOTHING_SUFFIXES)

    # **Return the original separators as they are.** Splitting into sentences and
    # rejoining with spaces flattens a paragraph break into a space — and in a
    # two-person shoot that break is what separates the two descriptions. Cut only
    # where something is dropped.
    pieces = re.split(r"((?<=[.!?])\s+)", body)
    kept: list[str] = []
    for i in range(0, len(pieces), 2):
        sentence = pieces[i]
        sep = pieces[i + 1] if i + 1 < len(pieces) else ""
        words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", sentence)]
        bad = [
            w for w in words
            if (w in gone) or (_owned_axis(w) and w not in licensed)
        ]
        if bad:
            # The fact of the drop survives in stage 8 of `craft_route` (the
            # prose word count moves).
            continue
        kept.append(sentence + sep)
    return "".join(kept).strip()


def tag_delta(before: str, after: str) -> tuple[list[str], list[str]]:
    """(words that arrived, words that left). **For the record only** — never used
    to decide anything."""
    from .identity import bare_tag

    was = [bare_tag(p) for p in str(before or "").split(",") if p.strip()]
    now = [bare_tag(p) for p in str(after or "").split(",") if p.strip()]
    gone = [t for t in was if t and t not in now]
    came = [t for t in now if t and t not in was]
    return came, gone


def scrub_craft_tags(
    tags: str, *, wearing: str, scene: str, beat: str, struck: set[str],
    wearing_b: str = "", beat_b: str = "", frame: str = "",
    banned: set[str] | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> str:
    """Wardrobe reconcile, opposite crop family, and notebook-fight drops.

    Struck / banned / aliases / leftovers / forgotten wearing share one pass
    (``reconcile_wardrobe_tags``). Crop conflict lives only here — assemble
    injects framing tags and does not re-ban the opposite family.

    ``trace`` is a basin **kept for the record only**. Pass one and each of the
    three inner stages records what it dropped and what it added; leave it out and
    nothing changes — the Showrunner (2026-08-31): "we have to make it clear which
    route broke it and how".
    """
    _ = (scene,)  # kept on the signature for callers that pass the whole shot

    def _step(name: str, was: str, now: str) -> str:
        if trace is not None:
            came, gone = tag_delta(was, now)
            trace.append({"hop": name, "added": came, "dropped": gone})
        return now

    step = tags
    tags, _ = reconcile_wardrobe_tags(
        tags, wearing=wearing, wearing_b=wearing_b,
        struck=struck, banned=banned,
    )
    step = _step("2a reconcile_wardrobe_tags", step, tags)
    tags = drop_crops_not_in_frame(tags, frame=frame)
    step = _step("2b drop_crops_not_in_frame", step, tags)
    tags = drop_tags_that_fight_the_notebook(
        tags, frame=frame, beat=beat, beat_b=beat_b,
    )
    _step("2c drop_tags_that_fight_the_notebook", step, tags)
    return tags


def strip_shot_keys(patch: dict[str, Any]) -> dict[str, Any]:
    """Densify must thicken tags/craft_scene only — never rewrite SHOT fields."""
    out = dict(patch or {})
    for key in SHOT_KEYS:
        out.pop(key, None)
    out.pop("standing", None)
    out.pop("wearing_drop", None)
    return out


def shot_snapshot(nb: dict[str, Any]) -> dict[str, Any]:
    """Plain values used to diff who rewrote the notebook."""
    out: dict[str, Any] = {
        k: str((nb or {}).get(k) or "") for k in _REWRITE_FIELDS
    }
    out["standing"] = [
        str(s).strip() for s in ((nb or {}).get("standing") or []) if str(s).strip()
    ]
    return out


def shot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    keys = set(before or {}) | set(after or {})
    for key in keys:
        old, new = (before or {}).get(key), (after or {}).get(key)
        if old != new:
            changed[key] = {"before": old, "after": new}
    return changed


def record_rewrite(
    session: dict[str, Any], source: str, *,
    before: dict[str, Any], after: dict[str, Any],
    intent: str = "", extra: dict[str, Any] | None = None,
    why: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Append a short rewrite to the session ring. Returns the entry or None.

    ``why`` carries one line per field explaining why it was written that way.
    It rides along with the diff so the instrument panel shows the decision next
    to its result — the showrunner can see 「カメラ見て」 ("look at the camera")
    landing in FRAME and read the sentence that put it there, instead of inferring it from a value
    that changed.
    """
    changed = shot_diff(before, after)
    if extra:
        changed.update(extra)
    for key, reason in (why or {}).items():
        pair = changed.get(key)
        if isinstance(pair, dict) and reason:
            pair["why"] = reason
    if not changed:
        return None
    entry = {
        "at": time.time(),
        "source": str(source or ""),
        "intent": str(intent or ""),
        "changed": changed,
    }
    log = list(session.get("rewrite_log") or [])
    log.append(entry)
    session["rewrite_log"] = log[-REWRITE_LOG_MAX:]
    return entry


#: **Drop an `unchanged` that has crept into a value.** The contract forbids it by
#: name and live it still produced `beat: sitting, unchanged, hands on the desk`
#: (2026-09-06). A whole field of it is already handled as "no change", but **mixed
#: in as one phrase it flows into the picture.**
_NOT_A_VALUE = frozenset({
    "unchanged", "none", "(none)", "empty", "(empty)", "n/a", "-", "--",
    "same", "no change", "as before",
})


def drop_non_values(text: str) -> str:
    """Drop non-values from a run of phrases. Empty in, empty out."""
    kept = [
        p.strip() for p in str(text or "").split(",")
        if p.strip() and p.strip().lower() not in _NOT_A_VALUE
    ]
    return ", ".join(kept)


def apply_patch(nb: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply absolute section replacements. Empty string in patch = clear.
    Missing key = unchanged. `standing` is a list (replace whole when provided).
    """
    changed = False
    for key in SHOT_KEYS + ("vibe",):
        if key not in patch:
            continue
        raw = patch.get(key)
        if isinstance(raw, dict):
            continue
        val = coerce_plain_phrase(raw)
        if val.startswith(("{", "[")):
            continue
        # If another field's label has crept in, cut there. `cut_at_label` is
        # defined below (to sit beside `_label_alternation`, where the labels come
        # from).
        if val:
            val = cut_at_label(val)
        # **Drop a "no change" that has crept in as a phrase.** A whole field of it
        # is rejected above, but live, `beat: sitting, unchanged, hands on the desk`
        # flowed all the way into the picture.
        if val:
            val = drop_non_values(val)
        if key in ("wearing", "wearing_b") and val:
            # The scripter restates the whole outfit on every change, so a
            # duplicate it inherits is a duplicate it hands back. Tidy here,
            # at the one door every patch goes through, and both rooms get it.
            val = brief.tidy_wearing(val)
        if key in _SHOT_FIELD_CAPS and val:
            val = _cap_phrase(val, max_chars=_SHOT_FIELD_CAPS[key])
        if key == "vibe" and val:
            val = _cap_lines(val, max_lines=VIBE_MAX_LINES, max_chars=VIBE_MAX_CHARS)
        if val != str(nb.get(key) or "").strip():
            nb[key] = val
            changed = True
    if "atmosphere" in patch or "scene" in patch:
        mood, place = split_atmosphere_time(
            str(nb.get("atmosphere") or ""), str(nb.get("scene") or ""),
        )
        if mood != str(nb.get("atmosphere") or "").strip():
            nb["atmosphere"] = mood
            changed = True
        if place != str(nb.get("scene") or "").strip():
            nb["scene"] = _cap_phrase(place, max_chars=SCENE_MAX_CHARS) if place else ""
            changed = True
    if "standing" in patch:
        raw = patch.get("standing")
        if isinstance(raw, str):
            items = [ln.strip().lstrip("-•").strip()
                     for ln in raw.splitlines() if ln.strip()]
        elif isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw if str(x).strip()]
        else:
            items = []
        items = items[:5]
        if items != list(nb.get("standing") or []):
            nb["standing"] = items
            changed = True
    # Taking something off, said as the one garment rather than as the whole
    # finished outfit. Restating five remaining items verbatim is the work the
    # scripter was measured failing to do — 「コート脱いで」 ("take the coat off")
    # came back with the
    # frame rewritten and WEARING untouched, on every removal turn — while the
    # one word it has to produce here is one it already produces. The
    # subtraction is ours; only an unambiguous name is applied, and an ask that
    # matches nothing or matches twice is left for the room to settle.
    drop = coerce_plain_phrase(patch.get("wearing_drop") or "")
    if drop:
        hits = garment_matches(str(nb.get("wearing") or ""), drop)
        if len(hits) == 1:
            rest = [
                item.strip() for item in str(nb.get("wearing") or "").split(",")
                if item.strip() and item.strip() != hits[0]
            ]
            nb["wearing"] = ", ".join(rest)
            beat = beat_without(str(nb.get("beat") or ""), hits[0])
            if beat != str(nb.get("beat") or "").strip():
                nb["beat"] = beat
            changed = True
    if changed:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return nb


_CARD_LINE_RE = re.compile(
    r"(?im)^\s*(PLACE|HOUR|WEARING_B|BEAT_B|WEARING|BEAT|FRAME)\s*[:：]\s*(.*)$"
)
_CARD_KEY = {
    "WEARING": "wearing",
    "BEAT": "beat",
    "FRAME": "frame",
    "WEARING_B": "wearing_b",
    "BEAT_B": "beat_b",
}
POSE_CARD_KEYS = ("beat", "beat_b")
# A fold may move the body. Nothing else: the shot itself only changes when the
# showrunner says so.
#
# `open` used to be here too — a field for the room's proposals, waiting on
# 「それでいこう」 ("let us go with that"). Across 390 live sessions it never once
# held a proposal; the
# 50 non-empty ones held parser debris (`$$OPEN$$`, `clear_open: true`,
# `false`, `_none_`), which then went back into the scripter prompt and onto
# the panel. A channel the showrunner cannot name is a channel nobody uses.
# What a seat proposes stays in the chat, where it is already readable.
FOLD_PATCH_KEYS = ("beat", "beat_b")


# A record of what the fold added. Not a SHOT_KEY — it is bookkeeping, not part of
# the picture. It appears in neither `shot_snapshot` nor `render`.
FOLD_UNDO_KEY = "fold_undo"


def record_fold(nb: dict[str, Any], before: dict[str, Any]) -> None:
    """Note what this fold added, so the next turn can let it go."""
    undo = {}
    for key in FOLD_PATCH_KEYS:
        was, now = str(before.get(key) or ""), str(nb.get(key) or "")
        if was != now:
            undo[key] = {"before": was, "after": now}
    if undo:
        nb[FOLD_UNDO_KEY] = undo
    else:
        nb.pop(FOLD_UNDO_KEY, None)


def undo_fold(nb: dict[str, Any]) -> list[str]:
    """Let the last turn's folded gesture go. Returns the fields put back.

    **The way in belonged to her and there was no way out.** A body detail she
    named — trembling hands, a shoulder turned — is folded into beat so the
    take right after she says it has her acting in it. That is the point of the
    fold and it stays. What was missing is the other half: beat records no
    author, so a gesture she mentioned once weighed the same as a posture the
    showrunner set, and stayed for the rest of the shoot. Worse, `struck_tokens`
    drops anything the notebook currently names from the struck list, so the one
    way to remove a word was disabled by the field that kept producing it.
    Measured live: a Muse trembled in every frame of a session and no direction
    could stop her.

    So a fold lasts one turn. If the value is still exactly what the fold left,
    it goes back to what it was before; if anyone has written over it since —
    the showrunner, a restate, a later compile — that value is theirs and is
    left alone. The showrunner's posture survives because it is what the fold
    was written on top of.
    """
    undo = nb.pop(FOLD_UNDO_KEY, None)
    if not isinstance(undo, dict):
        return []
    out: list[str] = []
    for key in FOLD_PATCH_KEYS:
        pair = undo.get(key)
        if not isinstance(pair, dict):
            continue
        if str(nb.get(key) or "") != str(pair.get("after") or ""):
            continue
        nb[key] = str(pair.get("before") or "")
        out.append(key)
    if out:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return out


def parse_muse_card(card: str) -> dict[str, str]:
    """Muse CARD labelled fields → notebook keys. PLACE/HOUR are scene, skipped."""
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal key, buf
        if key is not None:
            val = " ".join(x.strip() for x in buf if str(x).strip()).strip()
            if val:
                out[key] = val
        key, buf = None, []

    for line in str(card or "").splitlines():
        m = _CARD_LINE_RE.match(line)
        if m:
            flush()
            key = _CARD_KEY.get(m.group(1).upper())
            buf = [m.group(2)]
            continue
        if key is not None:
            buf.append(line)
    flush()
    return out


def absorb_muse_card(
    nb: dict[str, Any], card: str, *, keys: tuple[str, ...] = POSE_CARD_KEYS,
) -> dict[str, str]:
    """Fold this turn's Muse CARD pose into the notebook.

    Script runs before she talks, so her acted beat would otherwise wait until
    the next compile. Clothes stay with Script; only body action is absorbed.
    """
    parsed = parse_muse_card(card)
    patch = {
        k: parsed[k] for k in keys
        if str(parsed.get(k) or "").strip()
    }
    if not patch:
        return {}
    before = {k: str(nb.get(k) or "") for k in FOLD_PATCH_KEYS}
    apply_patch(nb, patch)
    record_fold(nb, before)
    return patch


# `promote_open` used to fold an affirmed proposal into the shot here, guessing
# from a noun list (持|手に|花|缶|傘|… — hold / in hand / flower / can / umbrella /
# …) whether the thing was handheld (-> BEAT)
# or worn (→ WEARING). The scripter reads the conversation now, sees the
# affirmation itself, and writes the absolute value into the right section.


def migrate(session: dict[str, Any]) -> dict[str, Any]:
    """Ensure notebook exists; seed from digest/craft when empty."""
    from . import facets as facets_mod

    facets_mod.migrate(session)
    partner = bool(str((session.get("inputs") or {}).get("partner_preset") or "").strip())
    nb = of(session)
    if has_shot(nb) or str(nb.get("vibe") or "").strip():
        return session

    digest = str(session.get("digest") or "").strip()
    craft = session.get("craft") or {}
    scene = str(craft.get("scene") or "").strip()
    if digest:
        # **Never cut mid-word (2026-09-18).** Its neighbour (line 1419) already
        # keeps to word boundaries through `_cap_phrase`, and only this was a raw
        # slice — live, `32cc5fab`'s `notebook.scene` ended on "…the consoles and
        # the concen".
        nb["scene"] = _cap_phrase(digest, max_chars=800)
        nb["vibe"] = _cap_phrase(digest, max_chars=400)
    if scene and not nb.get("scene"):
        nb["scene"] = _cap_phrase(scene, max_chars=800)
    table = facets_mod.table_of(session)
    if table:
        if not nb.get("wearing"):
            nb["wearing"] = _cap_phrase(
                str((table.get("costume") or {}).get("nl") or ""), max_chars=400)
        if not nb.get("beat"):
            pose = str((table.get("pose") or {}).get("nl") or "")
            expr = str((table.get("expression") or {}).get("nl") or "")
            nb["beat"] = " ".join(x for x in (pose, expr) if x)[:400]
        if not nb.get("frame"):
            nb["frame"] = str((table.get("camera") or {}).get("nl") or "")[:400]
        if partner:
            if not nb.get("wearing_b"):
                nb["wearing_b"] = str((table.get("costume_b") or {}).get("nl") or "")[:400]
            if not nb.get("beat_b"):
                pose_b = str((table.get("pose_b") or {}).get("nl") or "")
                expr_b = str((table.get("expression_b") or {}).get("nl") or "")
                nb["beat_b"] = " ".join(x for x in (pose_b, expr_b) if x)[:400]
    standing = [str(s).strip() for s in (session.get("standing") or []) if str(s).strip()]
    if standing and not nb.get("standing"):
        nb["standing"] = standing[:5]
    if has_shot(nb) or nb.get("vibe") or nb.get("standing"):
        nb["rev"] = max(1, int(nb.get("rev") or 0))
        nb["updated_at"] = time.time()
    session["notebook"] = nb
    return session


# ── Scripter output parse / validate ────────────────────────────────────────

_INTENT_RE = re.compile(
    r"(?im)^[\s>*_-]*INTENT\s*[:：]\s*(casual|shot|mixed|recall)\s*$"
)
def _label_alternation() -> str:
    """Build the label alternation from `SHOT_KEYS`, so field names have one
    source.

    Longer labels come first: read `WHY_FRAME` as `FRAME`, or `WEARING_DROP` as
    `WEARING`, and the value lands in the wrong field.
    """
    names = [f"WHY_{k.upper()}" for k in SHOT_KEYS]
    names += [k.upper() for k in SHOT_KEYS] + ["WEARING_DROP"]
    names += ["VIBE", "STANDING", "TAGS_SHARED", "TAGS_A", "TAGS_B", "TAGS",
              "CRAFT_SCENE", "UNCHANGED", "PROPOSE"]
    return "|".join(sorted(set(names), key=lambda s: (-len(s), s)))


_FIELD_RE = re.compile(
    r"(?im)^[\s>*_-]*(" + _label_alternation() + r")\s*[:：]\s*(.*)$"
)

# **A label is a boundary even away from the start of a line.** `_FIELD_RE` looks
# only at the start of a line, so one name slipped in front stops it being a
# boundary. The notebook writes its own page with the name in front —
# `各務 みお WEARING:` (`render`) — so when that was read back, everything after it
# piled into the previous field. Measured, `frame` was swallowing the rest of the
# page:
#
#   frame: medium shot, looking straight into lens 各務 みお WEARING: blue
#          sleeveless gown, earrings 各務 みお BEAT: sitting, … 平岡 すみれ WEARING_B
#
# Upper case only. That is the shape the notebook writes, and a spelling that does
# not occur in ordinary English prose.
_LABEL_RUN_RE = re.compile(r"\b(?:" + _label_alternation() + r")\s*[:：]")
# The name left at the tail after cutting. A field is a short English phrase
# (`coerce_plain_phrase`), so a run of non-ASCII at the end can be taken as the
# remains of a name.
_TRAILING_NAME_RE = re.compile(r"[\s,、，:：]*(?:[^\x00-\x7F]+\s*)+$")


def cut_at_label(text: str) -> str:
    """One field's value, cut where the next field's label begins.

    A value never contains another field. Whatever follows a label belongs to
    that label, and this is the one place that can say so without knowing which
    field it is looking at.
    """
    body = str(text or "")
    found = _LABEL_RUN_RE.search(body)
    if not found:
        return body.strip()
    return _TRAILING_NAME_RE.sub("", body[:found.start()]).strip()


# One line for the reason. A long explanation only dirties the notebook and helps
# nobody reading it.
WHY_MAX_CHARS = 180

VALID_INTENTS = frozenset({"casual", "shot", "mixed", "recall"})

# Shallow JSON Schema for Ollama `format` (non-stream scripter).
SCRIPTER_FORMAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["casual", "shot", "mixed", "recall"]},
        "atmosphere": {"type": "string"},
        "scene": {"type": "string"},
        "bg": {"type": "string"},
        "light": {"type": "string"},
        "frame": {"type": "string"},
        "wearing": {"type": "string"},
        "beat": {"type": "string"},
        "expression": {"type": "string"},
        "wearing_b": {"type": "string"},
        "wearing_drop": {"type": "string"},
        "beat_b": {"type": "string"},
        "expression_b": {"type": "string"},
        "vibe": {"type": "string"},
        "standing": {"type": "string"},
        "unchanged": {"type": "string"},
        "tags": {"type": "string"},
        "tags_shared": {"type": "string"},
        "tags_a": {"type": "string"},
        "tags_b": {"type": "string"},
        "craft_scene": {"type": "string"},
        # `why` must not go here. **The reason's slot eats the work's slot.**
        # Measured on 08-19 (`private/muse/crew_lab/why_regression.py`):
        #
        #     with why     wrote the field 0/9
        #     without why  wrote the field 9/9
        #
        # What came back looked like this:
        #
        #     {"intent":"shot",
        #      "why":{"beat":"「ベンチに座って」という指示に基づき
        #             posture stem を sitting に設定。"}}
        #
        # It **explains** that it set beat to sitting **and does not write beat**.
        # Describing the work has taken the place of the work. A stronger wording
        # ("the value is the work, the reason is a note on it") still gave 0/9 —
        # **words do not fix this.** The cause is the slot's existence.
        #
        # Reasons are taken from the label-form `WHY_*` and from restatements
        # (`parse_restate`). Those appear in the same run of lines as the values, so
        # no substitution happens.
        #
        # `propose` is a different thing. It is **a place for what cannot go in a
        # field**, not something writable instead of a field, so it is not in
        # competition with the work. With nowhere to put something undecided that
        # occurred to it, the model pushes it into beat instead (t21 「おいしそう？」
        # — "does it look good?" — put bread into her hands).
        "propose": {"type": "string"},
    },
    "required": ["intent"],
}


_PARTNER_ONLY = ("wearing_b", "beat_b", "expression_b")


#: A field that has an output key while compile's contract **does not explain it in
#: a single word**.
#:
#: The Showrunner (2026-08-31): "the place is not picked up by `scene` — it is being
#: held under **standing orders**". A line moving the place — 「あそこへ行こう」
#: ("let us go over there") — had gone into `standing` (the standing orders, which
#: apply to the whole shoot) rather than `scene`.
#:
#: In 3,019 characters of contract there is no explanation of STANDING. **A key with
#: no explanation becomes the dumping ground for a value with nowhere to go.** This
#: is the same shape as an accident already on record, and it is why `_PARTNER_ONLY`
#: was removed for solo shoots — "with two places to write, it writes there. Remove
#: the key and it cannot."
#:
#: Standing orders are written by the studio crew's router (`chain.run_route`).
#: There they are explained in the contract as "STANDING: <one rule for the whole
#: session, or the word none>", and there is a separate road from
#: `session["standing"]` to the notebook (`sync_crew_notebook`). **Only compile's
#: road is closed.**
_NOT_THE_COMPILES: tuple[str, ...] = ("standing",)


def scripter_format_schema(partner: bool = False) -> dict[str, Any]:
    """The output shape, without the partner's fields on a solo shoot.

    `guard_partner_patch` already drops `wearing_b` / `beat_b` when nobody is
    standing there — but it drops them **after** they are written, so whatever
    went in is lost. Measured on 「カーディガン羽織って。」 ("put a cardigan on",
    10 runs, solo):

        landed in wearing              6
        landed in wearing_b, then lost  2   ← on to the next turn still undressed
        malformed / empty output        2

    Two fields to hand the same garment to is two places to put it. The
    contract already says there is one actress; saying it again did not stop
    this. Taking the field away does — you cannot write into a key the schema
    does not have.
    """
    schema = {"type": SCRIPTER_FORMAT_SCHEMA["type"],
              "properties": dict(SCRIPTER_FORMAT_SCHEMA["properties"]),
              "required": list(SCRIPTER_FORMAT_SCHEMA["required"])}
    if not partner:
        for key in _PARTNER_ONLY:
            schema["properties"].pop(key, None)
    for key in _NOT_THE_COMPILES:
        schema["properties"].pop(key, None)
    return schema


def weave_refusal(tags: str, scene: str) -> str:
    """Why this weave cannot be used — `""` when it is usable.

    The weave gate only ever asked **"is it non-empty?"**. Measured live
    (2026-08-30, session `71929513`): a board went to the sampler holding

        tags        __tags, white_blouse, headphones, hair_down, sitting
        craft_scene sitting, elbows on the desk. ___craft_scene

    —— the model had echoed **the schema's own field names** instead of
    writing a picture. Both were non-empty, so the gate passed them. The
    place (`classroom, window side`) never reached the prompt, there was no
    prose at all, and nothing was recorded: `weave_review` and `warnings`
    were both empty. The clothes only survived because the wardrobe pass
    injects them back from WEARING, which is what made the board look
    plausible enough to ship.

    Empty is not the only way a weave comes back broken. This catches the
    one way that is unambiguous: **a value that is one of this schema's own
    keys.** The key list is derived from `SCRIPTER_FORMAT_SCHEMA`, not
    written by hand — this is not a vocabulary rule, it is the model handing
    the blank form back.

    Thinness is judged separately, by the caller, with the same
    `craft_is_thin` it already uses to decide whether to re-weave. Two
    floors in two places is how the thing being measured drifts from the
    thing shipped.
    """
    from .identity import bare_tag

    keys = {str(k).strip().lower()
            for k in (SCRIPTER_FORMAT_SCHEMA.get("properties") or {})}

    def _echoes(value: str) -> bool:
        # **The whole value being a field name**, or a field name with **a leading
        # underscore** such as `__tags`. Matching key names alone is too wide —
        # `scene`, `light`, `frame`, `beat` and `standing` are all legitimate
        # picture words, and a version that treated `standing` as a field name
        # failed an existing test. A real tag never starts with `_`.
        if str(value or "").strip().lower() in keys:
            return True
        for part in str(value or "").replace(".", ",").split(","):
            tok = bare_tag(part)
            if tok.startswith("_") and tok.strip("_").lower() in keys:
                return True
        return False

    if _echoes(tags) or _echoes(scene):
        return "schema_echo"
    return ""


def merge_tag_bags(
    *, tags: str = "", tags_shared: str = "", tags_a: str = "", tags_b: str = "",
) -> str:
    """Join SHARED/A/B (or flat tags) into one craft tag string, de-duped."""
    parts: list[str] = []
    seen: set[str] = set()
    for bag in (tags_shared, tags_a, tags_b, tags):
        for t in str(bag or "").split(","):
            tok = t.strip()
            if not tok:
                continue
            key = tok.lower().replace(" ", "_")
            if key in seen or key in ("none", "なし", "-"):
                continue
            seen.add(key)
            parts.append(tok)
    return ", ".join(parts)


def guard_partner_patch(
    patch: dict[str, Any], *, partner: bool = False,
) -> dict[str, Any]:
    """Drop the partner's sections on a solo shoot — nobody is standing there.

    This is the whole guard now. Deciding *which* Muse an edit was addressed to
    used to happen here too, off「だけ|のみ|ばっかり」("only / just") and
    「二人|ふたり|一緒」("both / together");
    it dropped the other card's edits on any line that named one Muse without
    one of those words, which is most lines. The scripter is handed the
    conversation and the speakers, and decides that itself.
    """
    if not partner:
        patch.pop("wearing_b", None)
        patch.pop("beat_b", None)
    return patch


def clean_why(raw: Any, patch: dict[str, Any] | None = None) -> dict[str, str]:
    """One short line per field, and only for fields this patch actually wrote.

    A reason for a field nobody touched is noise in the instrument panel, and
    a reason long enough to be a paragraph is a second notebook.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in SHOT_KEYS:
        if patch is not None and key not in patch:
            continue
        text = " ".join(str(raw.get(key) or "").split())
        if text and text.lower() not in ("none", "なし", "-", "unchanged"):
            out[key] = text[:WHY_MAX_CHARS]
    return out


PROPOSE_MAX_CHARS = 200


def clean_propose(raw: Any) -> str:
    """One line the scripter offers but must not write into the notebook.

    Without somewhere to put it, a model that thinks the shot wants something
    puts it in a field instead — measured on t21「おいしそう？」("does it look
    good?"), where every
    run of five gave her a pastry or a cup nobody had asked for. The channel
    costs one line and keeps the decision in the room.
    """
    text = " ".join(str(raw or "").split())
    # The model sometimes repeats the label inside the value it hands back.
    while text.upper().startswith("PROPOSE"):
        text = text.split(":", 1)[1].strip() if ":" in text else ""
    if not text or text.lower() in ("none", "なし", "-", "(none)"):
        return ""
    return text[:PROPOSE_MAX_CHARS]


def _blank_result(raw: str = "") -> dict[str, Any]:
    return {
        "intent": "casual",
        "patch": {},
        "why": {},
        "propose": "",
        "tags": "",
        "craft_scene": "",
        "raw": raw,
        "valid": False,
    }


def parse_scripter_json(raw: str) -> dict[str, Any] | None:
    """Parse Ollama JSON-format scripter output. None if not JSON.

    Uses ``ai.json_util.parse_json_object`` so missing commas / truncated
    tails can still salvage a usable object before falling back to labelled.
    """
    from ..ai.json_util import parse_json_object

    text = (raw or "").strip()
    if not text:
        return None
    # Fast reject for clearly labelled (non-JSON) blocks.
    head = text.lstrip()[:200]
    if (
        not head.startswith(("{", "[", "`"))
        and "INTENT" in head.upper()
        and "{" not in head
    ):
        return None
    try:
        data = parse_json_object(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "casual").strip().lower()
    if intent not in VALID_INTENTS:
        intent = "casual"
    unchanged = {
        x.strip().lower()
        for x in re.split(r"[,，、\s]+", str(data.get("unchanged") or ""))
        if x.strip() and x.strip().lower() not in ("none", "なし", "-", "")
    }
    patch: dict[str, Any] = {}
    # `SHOT_KEYS` is the one source for the field names. Writing them out here
    # meant that every experiment adding a field needed three places fixed
    # separately (here, `_FIELD_RE` and `SCRIPTER_BASE`) — the same duplication as
    # before the contracts were gathered into one place.
    for key in SHOT_KEYS + ("vibe",):
        if key in unchanged:
            continue
        if key not in data:
            continue
        val = coerce_plain_phrase(data.get(key))
        if not val:
            continue
        patch[key] = val
    if "standing" in data and "standing" not in unchanged:
        val = str(data.get("standing") or "").strip()
        if val.lower() not in ("none", "なし", "unchanged", "-", "無し", ""):
            patch["standing"] = val
    tags = str(data.get("tags") or "").strip()
    tags_shared = str(data.get("tags_shared") or "").strip()
    tags_a = str(data.get("tags_a") or "").strip()
    tags_b = str(data.get("tags_b") or "").strip()
    for bag_name, bag in (
        ("tags", tags), ("tags_shared", tags_shared),
        ("tags_a", tags_a), ("tags_b", tags_b),
    ):
        if bag.lower() in ("none", "なし", "-"):
            if bag_name == "tags":
                tags = ""
            elif bag_name == "tags_shared":
                tags_shared = ""
            elif bag_name == "tags_a":
                tags_a = ""
            else:
                tags_b = ""
    merged = merge_tag_bags(
        tags=tags, tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    craft_scene = str(data.get("craft_scene") or "").strip()
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""
    return {
        "intent": intent,
        "patch": patch,
        "why": clean_why(data.get("why"), patch),
        "propose": clean_propose(data.get("propose")),
        "tags": merged,
        "tags_shared": tags_shared,
        "tags_a": tags_a,
        "tags_b": tags_b,
        "craft_scene": craft_scene,
        "raw": raw,
        "valid": True,
    }


def parse_scripter_labelled(raw: str) -> dict[str, Any]:
    """Parse labelled scripter output into intent + patch + optional craft."""
    text = raw or ""
    intent = "casual"
    m = _INTENT_RE.search(text)
    if m:
        intent = m.group(1).lower()

    fields: dict[str, str] = {}
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        fm = _FIELD_RE.match(line)
        if fm:
            current = fm.group(1).upper()
            bodies.setdefault(current, [])
            inline = (fm.group(2) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies.setdefault(current, []).append(line)
    for key, parts in bodies.items():
        fields[key] = "\n".join(parts).strip()

    unchanged = {
        x.strip().lower()
        for x in re.split(r"[,，、\s]+", fields.get("UNCHANGED", ""))
        if x.strip() and x.strip().lower() not in ("none", "なし", "-")
    }

    # `SHOT_KEYS` is the one source. Forget something here and the value is
    # silently thrown away — which is what happened to `wearing_drop`: on turns
    # answered in label form (every turn with an image, and every turn where the
    # JSON parse failed) the undressing fell on the floor.
    key_map = {k: k.upper() for k in SHOT_KEYS}
    key_map["wearing_drop"] = "WEARING_DROP"
    key_map["vibe"] = "VIBE"
    why_raw = {
        key: fields.get(f"WHY_{key.upper()}", "")
        for key in SHOT_KEYS
    }
    patch: dict[str, Any] = {}
    for dest, src in key_map.items():
        if dest in unchanged or src.lower() in unchanged:
            continue
        if src in fields:
            val = fields[src]
            if val.lower() in ("unchanged", "変更なし", "同じ", "そのまま", "-"):
                continue
            patch[dest] = val

    if "STANDING" in fields and "standing" not in unchanged:
        val = fields["STANDING"]
        if val.lower() not in ("none", "なし", "unchanged", "-", "無し"):
            patch["standing"] = val

    def _clean_bag(key: str) -> str:
        val = str(fields.get(key) or "").strip()
        return "" if val.lower() in ("none", "なし", "-", "") else val

    tags = _clean_bag("TAGS")
    tags_shared = _clean_bag("TAGS_SHARED")
    tags_a = _clean_bag("TAGS_A")
    tags_b = _clean_bag("TAGS_B")
    merged = merge_tag_bags(
        tags=tags, tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    craft_scene = str(fields.get("CRAFT_SCENE") or "").strip()
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""

    # Labelled output counts as valid when INTENT was present or we got a patch.
    valid = bool(m or patch or merged or craft_scene)
    return {
        "intent": intent if intent in VALID_INTENTS else "casual",
        "patch": patch,
        "why": clean_why(why_raw, patch),
        "propose": clean_propose(fields.get("PROPOSE")),
        "tags": merged,
        "tags_shared": tags_shared,
        "tags_a": tags_a,
        "tags_b": tags_b,
        "craft_scene": craft_scene,
        "raw": text,
        "valid": valid,
    }


def parse_scripter(raw: str) -> dict[str, Any]:
    """Parse scripter output — JSON schema first, labelled fallback."""
    text = raw or ""
    if not str(text).strip():
        return _blank_result(text)
    parsed = parse_scripter_json(text)
    if parsed is not None:
        return parsed
    return parse_scripter_labelled(text)


def validate_scripter(
    result: dict[str, Any], *, partner: bool = False, mode: str = "",
) -> dict[str, Any]:
    """Validate-first gate. On failure: no craft fields, mark invalid.

    ``compile`` writes notebook only — tags are ignored even on shot/mixed.
    ``weave`` writes tags/craft_scene and must not rewrite SHOT.
    """
    out = dict(result or {})
    intent = str(out.get("intent") or "casual").strip().lower()
    if intent not in VALID_INTENTS:
        out["intent"] = "casual"
        out["valid"] = False
        out["tags"] = ""
        out["craft_scene"] = ""
        return out
    out["intent"] = intent
    tags_shared = str(out.get("tags_shared") or "").strip()
    tags_a = str(out.get("tags_a") or "").strip()
    tags_b = str(out.get("tags_b") or "").strip()
    tags = str(out.get("tags") or "").strip() or merge_tag_bags(
        tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    scene = str(out.get("craft_scene") or "").strip()
    if mode == "compile":
        out["patch"] = dict(out.get("patch") or {})
        out["tags"] = ""
        out["tags_shared"] = ""
        out["tags_a"] = ""
        out["tags_b"] = ""
        out["craft_scene"] = ""
        out.setdefault("valid", True)
        return out
    if mode == "weave":
        out["patch"] = strip_shot_keys(dict(out.get("patch") or {}))
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        if partner and not (tags_a and tags_b):
            out["refuse_reason"] = "w_muse_tags_unsplit"
        elif partner:
            tags = merge_tag_bags(
                tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
            )
        out["tags"] = tags
        out["tags_shared"] = tags_shared
        out["tags_a"] = tags_a
        out["tags_b"] = tags_b
        out["craft_scene"] = scene
        out.setdefault("valid", True)
        return out
    if intent in ("shot", "mixed"):
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        if partner and not (tags_a and tags_b):
            out["refuse_reason"] = "w_muse_tags_unsplit"
        elif partner:
            tags = merge_tag_bags(
                tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
            )
    out["patch"] = dict(out.get("patch") or {})
    out["tags"] = tags
    out["tags_shared"] = tags_shared
    out["tags_a"] = tags_a
    out["tags_b"] = tags_b
    out["craft_scene"] = scene
    out.setdefault("valid", True)
    return out

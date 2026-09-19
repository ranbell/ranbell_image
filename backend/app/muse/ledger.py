"""Absolute shot ledger for Muse Refine.

One document. Missing keys leave the previous value. Present keys replace.
"""
from __future__ import annotations

import re
from typing import Any

LEDGER_KEYS: tuple[str, ...] = (
    "wearing",
    "beat",
    "expression",
    "scene",
    "light",
    "bg",
    "frame",
    "wearing_b",
    "beat_b",
    # **A face for the partner too (2026-09-10).** In the Showrunner's duet shoot
    # the partner's expression never reached the picture at all (`Mio: … bright
    # smile` against an `Asahi:` with no face). Classic's notebook has had
    # `expression_b` from the start.
    "expression_b",
    "lettering",  # short Latin words for Anima text "…" / text_on_image
    "atmosphere",  # mood / air — conversation-driven (wistful, tense, cozy…)
    "look",  # art direction / render — cel, fantasy, watercolor… (not UI buttons)
)

# Survive long chats: LLMs love to "helpfully" clear or rewrite these.
# Empty string only clears when the director explicitly allows it (reset cue).
STICKY_KEYS: frozenset[str] = frozenset({"atmosphere", "look", "lettering"})

# All shot axes resist accidental empty clears from writer / muse / repair.
RESIST_EMPTY_CLEAR: frozenset[str] = frozenset(LEDGER_KEYS)


DROP_KEYS: tuple[str, ...] = ("wearing_drop",)

# UI chips — short label + icon glyph (not used for model judgment).
FIELD_CHIPS: dict[str, dict[str, str]] = {
    "wearing": {"icon": "👕", "ja": "服", "en": "Clothes"},
    "beat": {"icon": "🧍", "ja": "姿勢", "en": "Pose"},
    "expression": {"icon": "😊", "ja": "表情", "en": "Face"},
    "scene": {"icon": "📍", "ja": "場所", "en": "Place"},
    "light": {"icon": "💡", "ja": "光", "en": "Light"},
    "bg": {"icon": "🏞", "ja": "背景", "en": "BG"},
    "frame": {"icon": "📷", "ja": "構図", "en": "Frame"},
    "wearing_b": {"icon": "👗", "ja": "相方服", "en": "Partner clothes"},
    "beat_b": {"icon": "🤝", "ja": "相方姿勢", "en": "Partner pose"},
    "expression_b": {"icon": "🙂", "ja": "相方表情", "en": "Partner face"},
    "lettering": {"icon": "🔤", "ja": "文字", "en": "Lettering"},
    "atmosphere": {"icon": "🌫", "ja": "雰囲気", "en": "Mood"},
    "look": {"icon": "🎨", "ja": "画風", "en": "Look"},
    "wearing_drop": {"icon": "🗑", "ja": "脱ぐ", "en": "Drop"},
}

# Soft cue that the director line is about the picture (retry writer if empty).
_PICTURE_CUES = re.compile(
    r"("
    r"着|服|シャツ|スカート|ワンピース|制服|パーカー|コート|帽子|靴|脱|"
    r"立|座|跪|寝|ポーズ|姿勢|表情|笑|泣|"
    r"場所|屋上|教室|公園|海|部屋|背景|光|照明|カメラ|構図|寄|引き|"
    r"看板|文字|テキスト|ボード|"
    r"雰囲気|空気|ムード|画風|タッチ|塗り|ファンタジー|エモ|セル|"
    r"wear|shirt|skirt|dress|uniform|hoodie|coat|hat|pose|stand|sit|"
    r"rooftop|classroom|park|beach|room|background|light|camera|frame|outfit|"
    r"sign|banner|lettering|textboard|atmosphere|mood|fantasy|watercolor|cel"
    r")",
    re.I,
)


def blank() -> dict[str, str]:
    return {k: "" for k in LEDGER_KEYS}


#: The label that arrives attached to the front of a value. **A ledger value must
#: never start with a field name.**
#:
#: Live (2026-09-11), this landed in `wearing`:
#:
#:     wearing: "BEAT: standing by the railing, silhouette against the sun"
#:     bg:      "ATMOSPHERE:"
#:
#: It comes from more than one place — the actress's CARD
#: (`persona.card_to_patch`), the crew seats' CRAFT and the writer's JSON can all
#: arrive with a label on the front. **There is one entrance, `normalize_patch`**, so
#: it is dropped here.
_LABEL_HEAD_RE = re.compile(
    r"^\s*(?:PLACE|HOUR|SCENE|WEARING(?:_B)?|BEAT(?:_B)?|EXPRESSION(?:_B)?|"
    r"FACE(?:_B)?|FRAME|LIGHT|BG|BACKGROUND|ATMOSPHERE|MOOD|LOOK|STYLE|"
    r"LETTERING|TEXT|CLOTH|BODY|OPTICS|COLOUR|PROPS|AIR|SHAPE|RENDER|FINISH|"
    r"TAGS|CRAFT)\s*[:：]\s*",
    re.I,
)


def strip_field_label(value: str) -> str:
    """Strip a field name from the head of a value, however many are stacked
    (`WEARING: BEAT: …`).

    **A word that merely looks like a field name stays** — `atmospheric, dusty`
    has no colon, so it is untouched. If stripping empties the value, the caller
    throws it away.
    """
    text = str(value or "")
    for _ in range(4):
        stripped = _LABEL_HEAD_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text.strip()


# ── One body holds only one answer ──────────────────────────────────────────
#
# **Eighteen seats write the same field in turn, so paraphrases and contradictions
# pile up.** Live (2026-09-12, reproducing 「今日もメイドさんで」 — "the maid outfit
# again today"), `beat` reached 13 words and looked like this:
#
#     weight on right leg      <-> weight on back foot     the weight in two places
#     hips jutting out sharply <-> hips pushed forward     the same thing twice
#     hands_clutching_tray_edge <-> hugging tray           the same thing three times
#     (on the previous turn: hands releasing <-> hands_clutching <-> hands steadying)
#
# The original session behind the same record ran to 8 words, **one per part of the
# body**
# （`standing, weight_on_front_foot, one_hand_on_hip, other_arm_holding_tray_at_waist…`）。
#
# `tags.conflict.SLOTS` does not match — that is a table of **exact danbooru
# words**, and what arrives here is free text (`weight on right leg`). This is not
# about growing a word table.
#
# **Look by axis.** When a second answer arrives on one axis (the weight, the hips'
# direction, the head's direction, the hands' grip), drop it. Whether it is the
# opposite answer (a contradiction) or the same one (a paraphrase), both are dropped
# — to the picture both are noise. **The first answer wins**, the same rule as
# `facets`, and measured, the director's line did come first (the writer writes the
# instruction and then adds the seats' detail).
#
# Naming a body part alone is not enough to drop. **There are two hands** —
# `left hand on hip` and `other_arm_holding_tray` both stand. So an axis is "part
# plus direction", and a phrase with no direction word rides on no axis at all (the
# same judgement as "taking too much costs more" at the top of `tags/conflict.py`).
_BODY_AXES: tuple[tuple[str, tuple[str, ...], dict[str, tuple[str, ...]]], ...] = (
    # axis name, the words for the part, direction -> the words for that direction
    ("weight", ("weight", "leaning", "balance"), {
        "front": ("front", "forward", "fore"),
        "back": ("back", "rear", "behind", "heel"),
        "left": ("left",),
        "right": ("right",),
        "both": ("both", "even", "evenly", "center", "centre"),
    }),
    ("hips", ("hip", "hips", "pelvis", "waist"), {
        "out": ("forward", "out", "jutting", "jut", "thrust", "thrusting",
                "pushed", "push", "pushing", "ahead"),
        "in": ("back", "retracted", "retract", "pulled", "pull", "tucked",
               "tuck", "drawn"),
    }),
    ("head", ("head", "chin", "face", "gaze direction", "neck"), {
        "up": ("up", "lifted", "lift", "raised", "raise", "tilted_up", "upward"),
        "down": ("down", "lowered", "lower", "dropped", "downward", "tucked"),
        "to_camera": ("toward camera", "to camera", "at the camera", "at viewer",
                      "toward the viewer", "turned toward camera", "facing camera"),
        "away": ("away", "aside", "over her shoulder", "to the side"),
    }),
    # **An axis that needs no word for the part.** Whatever is gripping is by
    # definition the hands, so `hugging tray` (with no word for a hand) rides the
    # same axis. The empty tuple is the marker for that.
    ("hold", (), {
        # **Supporting counts as gripping (live, 2026-09-12).**
        # `right arm holding tray` and `forearm_supporting_tray` both survived —
        # `supporting` was not listed, so it rode no axis.
        "hold": ("holding", "hold", "clutching", "clutch", "gripping", "grip",
                 "grasping", "grasp", "steadying", "steady", "hugging", "hug",
                 "carrying", "carry", "clasping", "clasp", "supporting",
                 "support", "cradling", "cradle", "propping", "balancing"),
        "free": ("releasing", "release", "letting go", "let go", "lowering",
                 "setting down", "putting down", "open palms", "empty"),
    }),
)


#: The words dropped when extracting *what* is being gripped on the grip axis —
#: verbs, body parts, particles, manner adjectives. What remains is **the object**.
#: `holding tray` and `holding coffee cup` are different objects, so **both stand**
#: (there are two hands). `holding tray` and `holding order_tray` are the same
#: object, so the second is a paraphrase.
_NOT_THE_OBJECT = frozenset({
    "hand", "hands", "finger", "fingers", "palm", "palms", "arm", "arms",
    "forearm", "forearms", "elbow", "elbows", "knuckle", "knuckles", "wrist",
    "on", "at", "the", "a", "an", "with", "to", "of", "in", "into", "onto",
    "her", "his", "its", "own", "both", "one", "other", "and",
    "white", "tight", "tightly", "tense", "tensed", "trembling", "slightly",
    "gently", "firmly", "sharply", "lightly", "barely", "still",
})


def _axis_of(phrase: str) -> tuple[str, str, frozenset[str]] | None:
    """The axis this phrase answers, its answer, and the key within that axis.
    `None` when it does not sit on one.

    The key only means anything on the holding axis — **the object being held**.
    Different objects stand as different answers on the same axis. The other axes
    (weight, hips, head) exist once per body, so their key is empty.
    """
    words = re.sub(r"[_\-]+", " ", phrase.lower())
    for axis, parts, answers in _BODY_AXES:
        if parts and not any(
            re.search(rf"(?<![a-z]){re.escape(part)}(?![a-z])", words)
            for part in parts
        ):
            continue
        for answer, cues in answers.items():
            for cue in cues:
                if not re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", words):
                    continue
                if axis != "hold":
                    return axis, answer, frozenset()
                spent = {w for a in answers.values() for c in a for w in c.split()}
                obj = {
                    w for w in re.findall(r"[a-z]+", words)
                    if w not in spent and w not in _NOT_THE_OBJECT
                }
                return axis, answer, frozenset(obj)
    return None


def one_body(value: str) -> tuple[str, list[str]]:
    """Fold `beat` into one body. Returns the phrases kept and the phrases dropped.

    Only **the second phrase on the same axis** is dropped. A phrase that merely
    names a body part, or carries no direction (`arms_stiff` / `forearms tensed` /
    `standing`), is untouched. The holding axis counts **per object**, so a tray
    and a cup both stand.

    Exact duplicates go too, as do phrases contained in another as words
    (`hand on hip` sits inside `left hand on hip`) — that is saying the same thing
    twice.
    """
    parts = [p.strip() for p in str(value or "").replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    kept: list[str] = []
    dropped: list[str] = []
    seen: dict[str, list[frozenset[str]]] = {}
    for phrase in parts:
        axis = _axis_of(phrase)
        if axis is not None:
            name, _answer, key = axis
            before = seen.setdefault(name, [])
            if name == "hold":
                # Overlapping objects means the same object — dropped whether it
                # is a paraphrase or the opposite. A phrase whose object could not
                # be read (`open palms`) merges into the grip already there.
                if any(not key or not k or (key & k) for k in before):
                    dropped.append(phrase)
                    continue
            elif before:
                dropped.append(phrase)
                continue
            before.append(key)
        low = re.sub(r"[_\-]+", " ", phrase.lower()).strip()
        if any(low in re.sub(r"[_\-]+", " ", k.lower()) for k in kept):
            dropped.append(phrase)
            continue
        kept.append(phrase)
    return ", ".join(kept), dropped


def normalize_patch(
    raw: dict[str, Any] | None,
    *,
    report: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Keep only known keys; coerce to stripped strings.

    A field name stuck to the head of a value is stripped here too — this is the
    single door into the ledger.
    """
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in (*LEDGER_KEYS, *DROP_KEYS):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None:
            continue
        text = strip_field_label(str(val).strip())
        if key in BODY_KEYS and text:
            # **Fold into one body.** There is one entrance here, so it bites the
            # same on the seats' road and the card's road (the same judgement as
            # dropping field names).
            text, gone = one_body(text)
            if gone and report is not None:
                # Nothing thrown away silently — the caller can record what was
                # dropped.
                report.setdefault(key, []).extend(gone)
        if key == "wearing_drop" and not text:
            continue
        out[key] = text
    return out


def apply_patch(ledger: dict[str, str], patch: dict[str, str]) -> dict[str, str]:
    """Absolute merge. Empty string clears the field when present in patch."""
    next_ledger = {**blank(), **{k: str(ledger.get(k) or "") for k in LEDGER_KEYS}}
    drop = str(patch.get("wearing_drop") or "").strip().lower()
    if drop:
        # **Match on word boundaries (2026-09-09).** On a substring match, taking
        # off a `shirt` erased a `skirt` as well (measured, pure function).
        # `talk.word_hit` is the one rule, the same as the ban filter uses.
        from .talk import word_hit

        wearing = next_ledger.get("wearing") or ""
        parts = [p.strip() for p in wearing.replace(";", ",").split(",") if p.strip()]
        kept = [p for p in parts if not word_hit(drop, p)]
        next_ledger["wearing"] = ", ".join(kept)
    for key in LEDGER_KEYS:
        if key not in patch:
            continue
        next_ledger[key] = str(patch[key]).strip()
    return next_ledger


def _posture_of(value: str) -> str:
    """The posture this body names (`standing` / `sitting` …), empty when none.

    The word table is `tags.conflict`'s `posture` slot as it stands — posture
    words arrive as exact danbooru terms, so that table hits (unlike free-text
    direction). Keeping two tables guarantees drift, so no list is built here.
    """
    from ..tags import conflict

    for phrase in str(value or "").replace(";", ",").split(","):
        for word in re.findall(r"[A-Za-z_]+", phrase):
            if conflict.slot_of(word) == "posture":
                return word.lower()
    return ""


def keep_the_posture(new: str, before: str) -> str:
    """Give back the previous posture to a body that forgot to name one.

    **The field is rewritten whole, so anything not written disappears.** Measured
    (2026-09-12, A/B on the bench): adding the "one body" clause removed the
    contradictions, and in the same runs `standing` vanished — the writer left it
    out as "something already known". No wording of the contract brought it back,
    so it is protected here.

    It is only restored **when the new body names no posture at all**. If it names
    one, that one is right (a turn told "sit down" does not get stood up).
    """
    if not str(new or "").strip():
        return new
    if _posture_of(new):
        return new
    was = _posture_of(before)
    if not was:
        return new
    return f"{was}, {new}"


def scrub_patch(
    patch: dict[str, str] | None,
    ledger: dict[str, str] | None,
    *,
    allow_clear: set[str] | frozenset[str] | None = None,
    report: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Drop accidental empty clears so long chats keep clothes / mood / look.

    Empty string still clears when ``allow_clear`` names the key (explicit reset).
    """
    raw = normalize_patch(patch, report=report)
    if not raw:
        return {}
    allow = set(allow_clear or ())
    cur = {**blank(), **(ledger or {})}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key == "wearing_drop":
            if val:
                out[key] = val
            continue
        if (
            val == ""
            and key in RESIST_EMPTY_CLEAR
            and str(cur.get(key) or "").strip()
            and key not in allow
        ):
            continue
        if key in BODY_KEYS and val:
            # If the posture was left unnamed, the previous one comes back
            # (`keep_the_posture`).
            val = keep_the_posture(val, str(cur.get(key) or ""))
        out[key] = val
    return out


def guard_sticky_writes(
    patch: dict[str, str] | None,
    *,
    allowed: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    """Muse/repair may not invent atmosphere/look/lettering changes.

    Only keys the director (writer + cue) touched this turn may rewrite sticky
    fields. Omitting a sticky key always keeps the previous value.
    """
    raw = dict(patch or {})
    if not raw:
        return {}
    allow = set(allowed or ())
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key in STICKY_KEYS and key not in allow:
            continue
        out[key] = val
    return out


def guard_muse_propose(
    patch: dict[str, str] | None,
    ledger: dict[str, str] | None,
    *,
    director_keys: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    """Classic Muse fold spirit: actress does not overwrite a settled shot.

    Director patches are already applied before muse runs. Muse may **fill empty**
    ledger slots (e.g. missing beat_b). Clothes / place / camera stay fill-empty
    only — ``director_keys`` never grants overwrite there (verified: muse used to
    replace the director's wearing on the same turn).

    Exception — **expression** (performance / face):
    The actress owns the face for the photograph when the director did not name
    a face this turn. She may fill an empty expression always, and may refresh a
    settled face when scene-ish axes just moved (scene / atmosphere / beat /
    light / frame / bg) so the expression can track the shot.

    **In a duet there are two faces (2026-09-10).** She is performing both, so
    `expression_b` counts as the same performance axis — leave only the partner's
    face pinned to the ledger and it is left behind whenever the scene moves.
    Whether the director named a face this turn is also read per field (naming
    `expression` does not freeze `expression_b` as well).
    """
    raw = dict(patch or {})
    if not raw:
        return {}
    dir_keys = set(director_keys or ())
    scene_moved = bool(dir_keys & {"scene", "atmosphere", "beat", "light", "frame", "bg"})
    cur = {**blank(), **(ledger or {})}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key in STICKY_KEYS or key == "wearing_drop":
            continue
        text = str(val or "").strip()
        if not text:
            continue
        cur_val = str(cur.get(key) or "").strip()
        if not cur_val:
            out[key] = text
            continue
        # Performance axis: scene-matched face when director left face alone.
        # There are two faces (a duet). Each field is judged on whether the
        # director touched that field.
        if key in ("expression", "expression_b") and key not in dir_keys and scene_moved:
            if text.lower() != cur_val.lower():
                out[key] = text
    return out


def changed_fields(before: dict[str, str], after: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key in LEDGER_KEYS:
        if str(before.get(key) or "").strip() != str(after.get(key) or "").strip():
            out.append(key)
    return out


def patch_fields(patch: dict[str, str]) -> list[str]:
    keys = [k for k in LEDGER_KEYS if str(patch.get(k) or "").strip() or k in patch]
    # Only keys actually present in patch.
    keys = [k for k in LEDGER_KEYS if k in patch]
    if str(patch.get("wearing_drop") or "").strip():
        keys.append("wearing_drop")
    return keys


def chips_for(fields: list[str], *, locale: str = "ja") -> list[dict[str, str]]:
    ja = locale.startswith("ja")
    chips: list[dict[str, str]] = []
    for key in fields:
        meta = FIELD_CHIPS.get(key)
        if not meta:
            continue
        chips.append({
            "key": key,
            "icon": meta["icon"],
            "label": meta["ja"] if ja else meta["en"],
        })
    return chips


#: The second person's fields. On a shoot with one person they are **not shown**.
PARTNER_KEYS: tuple[str, ...] = ("wearing_b", "beat_b", "expression_b")

#: The posture fields, one per person — what `one_body` is applied to.
BODY_KEYS: tuple[str, ...] = ("beat", "beat_b")


def for_model(ledger: dict[str, str], *, partner: bool) -> dict[str, str]:
    """The ledger as the model sees it. **On a solo shoot the second person's
    fields are dropped.**

    The Showrunner (2026-09-09): "when there is only one person it still edits
    muse_b's tags. **The explanation of one versus two is not enough.**"

    The contract had said from the start to write `wearing_b` / `beat_b` only when
    a partner Muse is present. What was missing was **telling it whether there is
    one** — `blank()` fills every field, so the model always saw an empty second
    person. An empty field looks like an instruction to fill it.

    **Take the box away and nothing can be put in it.** Removing the field is
    stronger than adding a line to the contract (this studio has measured the
    converse many times — give them a box and they fill it). The caller also says
    the headcount in one line with `cast_line()`.
    """
    out = {k: v for k, v in (ledger or {}).items()
           if partner or k not in PARTNER_KEYS}
    return out


def cast_line(*, partner: bool, name_a: str = "", name_b: str = "") -> str:
    """The headcount in one line — said in words as well as by removing fields from
    the ledger."""
    a = (name_a or "the lead").strip()
    if not partner:
        return (
            f"CAST: solo — {a} is the only person in frame. There is no second "
            "person: never write wearing_b, beat_b or expression_b."
        )
    b = (name_b or "the partner").strip()
    return (
        f"CAST: two in frame — {a} and {b}. "
        f"wearing_b / beat_b / expression_b are {b}'s, never {a}'s."
    )


def now_line(ledger: dict[str, str], *, locale: str = "ja") -> str:
    """Plain-language NOW — all shot axes the actress must respect."""
    wearing = (ledger.get("wearing") or "").strip()
    beat = (ledger.get("beat") or "").strip()
    expression = (ledger.get("expression") or "").strip()
    scene = (ledger.get("scene") or "").strip()
    light = (ledger.get("light") or "").strip()
    bg = (ledger.get("bg") or "").strip()
    frame = (ledger.get("frame") or "").strip()
    bits: list[str] = []
    if locale.startswith("ja"):
        if wearing:
            bits.append(f"服装: {wearing}")
        if beat:
            bits.append(f"姿勢: {beat}")
        if expression:
            bits.append(f"表情: {expression}")
        if scene:
            bits.append(f"場所: {scene}")
        if bg and bg != scene:
            bits.append(f"背景: {bg}")
        if light:
            bits.append(f"光: {light}")
        if frame:
            bits.append(f"構図: {frame}")
        if atm := (ledger.get("atmosphere") or "").strip():
            bits.append(f"雰囲気: {atm}")
        if look := (ledger.get("look") or "").strip():
            bits.append(f"画風: {look}")
        if letter := (ledger.get("lettering") or "").strip():
            bits.append(f"文字: {letter}")
        return " / ".join(bits) if bits else "（まだ画は決まっていない）"
    if wearing:
        bits.append(f"wearing {wearing}")
    if beat:
        bits.append(f"pose {beat}")
    if expression:
        bits.append(f"face {expression}")
    if scene:
        bits.append(f"place {scene}")
    if bg and bg != scene:
        bits.append(f"bg {bg}")
    if light:
        bits.append(f"light {light}")
    if frame:
        bits.append(f"frame {frame}")
    if atm := (ledger.get("atmosphere") or "").strip():
        bits.append(f"mood {atm}")
    if look := (ledger.get("look") or "").strip():
        bits.append(f"look {look}")
    if letter := (ledger.get("lettering") or "").strip():
        bits.append(f'lettering "{letter}"')
    return "; ".join(bits) if bits else "(shot not set yet)"


def touched_picture(patch: dict[str, str]) -> bool:
    if any(k in patch for k in LEDGER_KEYS):
        return True
    return bool(str(patch.get("wearing_drop") or "").strip())


def looks_like_picture_line(text: str) -> bool:
    """Heuristic only — used to retry an empty writer, not to apply patches."""
    return bool(_PICTURE_CUES.search(text or ""))

"""The shot, in parts. Each part carries its own tags AND its own sentence.

The shot used to live in two unstructured blobs: one flat comma-separated bag
of danbooru tags and one 140-200 word paragraph. Nothing in the code knew which
tag was about the camera and which was about the room, so nothing could take out
a tag that the Showrunner's newest instruction had just contradicted. Every turn
rewrote both blobs whole and was told to KEEP, so whatever the model failed to
notice survived by inertia.

Two failures came from that, and both were reported from real sessions. A shot
moved from a high angle to a low one kept `looking_up`, and the picture broke.
A jacket the Showrunner took off came back, again, for several turns.

The fix is not a better remover. **You do not remove `from_above`. You overwrite
the camera facet, and `from_above` only ever lived there.** A note routes to the
parts it changes, those parts are rewritten as a whole, and every other part is
untouched by construction rather than by a model remembering to leave it alone.

`craft` is now derived from this table (see `service._reassemble`), so the render
path, the ledger, the report and the panel all keep reading what they always
read. `nl_join` means `craft["scene"]` is never blocked on a model call: the
table is a valid prompt the instant one facet is written, and `compose` only
makes it read better.

The COSTUME block is the proof this works. It is the one part of the shot that
already had its own slot, its own owner and its own release valve, and it is the
one part that held. This module is that pattern, generalised.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..tags import conflict
from . import brief as brief_mod
from . import identity

logger = logging.getLogger(__name__)

# Declaration order = the order the panel reads and the order `table_block`
# writes. Shot-sheet order: where, when, how lit, what is there, what she has
# on, what she is doing, her face, the lens. Same shape as `brief.PLAN_FIELDS`.
FACETS: tuple[tuple[str, str], ...] = (
    ("place", "PLACE"),
    ("hour", "HOUR"),
    ("light", "LIGHT"),
    ("props", "PROPS"),
    ("costume", "COSTUME"),
    ("pose", "POSE"),
    ("expression", "EXPRESSION"),
    ("camera", "CAMERA"),
)

FACET_NAMES: frozenset[str] = frozenset(k for k, _ in FACETS)
FACET_LABELS: dict[str, str] = {k: label for k, label in FACETS}

# The order facet tags are concatenated into the Comfy positive, which is NOT
# the order above. Earlier tokens carry more weight, so composition and the
# acting lead; the room and the hour are context and can sit at the back.
TAG_ORDER: tuple[str, ...] = (
    "camera", "pose", "expression", "costume", "props", "place", "hour", "light",
)

# Which conflict slots each facet owns. A tag whose slot belongs to another
# facet is dropped on write — the expression facet does not get to say where
# she is looking, because the lens position decides that and the two would
# disagree on the next camera move. Slot names come from `tags.conflict.SLOTS`
# rather than being re-listed here, so there is one place a tag is classified.
FACET_OWNS: dict[str, tuple[str, ...]] = {
    "place": ("room",),
    "hour": ("time_of_day",),
    "light": (),
    "props": (),
    "costume": (),
    "pose": ("posture", "arms"),
    "expression": ("mouth", "eyes"),
    "camera": (
        "camera_pitch", "camera_side", "camera_distance",
        "gaze_pitch", "gaze_target",
    ),
}

# slot -> the facet that owns it. Built once; used to reject a tag written into
# the wrong part.
_SLOT_OWNER: dict[str, str] = {
    slot: facet for facet, slots in FACET_OWNS.items() for slot in slots
}

# `from_behind` rules out `looking_at_viewer` only when `looking_back` is
# absent — she turned her head, and both are true. `conflict.contradicts` is
# pairwise and can never see that third tag, so the rule lives here, where the
# whole camera facet is visible at once.
_BEHIND = frozenset({"from_behind", "rear_view", "back_view"})
_FACING_VIEWER = frozenset({"looking_at_viewer", "eye_contact"})
_TURNED_HEAD = frozenset({"looking_back", "looking_over_shoulder", "turning_head"})

_TAG_SPLIT_RE = re.compile(r"[,\n]")


def blank_facet() -> dict[str, Any]:
    return {"tags": [], "nl": "", "by": "", "at": 0.0, "locked": False, "rev": 0}


def blank_table() -> dict[str, dict[str, Any]]:
    table = {name: blank_facet() for name, _ in FACETS}
    # The eight-line COSTUME block is load-bearing and validated, so it survives
    # verbatim as this facet's structured payload. `tags` stays the GARMENTS
    # slots and nothing else — see `brief.garment_tags`.
    table["costume"]["fields"] = {}
    return table


def table_of(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The session's facet table, filling in any facet a rollback left out."""
    table = session.setdefault("facets", blank_table())
    for name, _ in FACETS:
        if not isinstance(table.get(name), dict):
            table[name] = blank_facet()
            if name == "costume":
                table[name]["fields"] = {}
    return table


def parse_tags(raw: Any) -> list[str]:
    """A tag list from either a list or a comma-separated string.

    Emphasis is kept as written and clamped, the same as everywhere else; the
    dedupe compares bare names so `(pants:1.2)` cannot ride in beside `pants`.
    """
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = _TAG_SPLIT_RE.split(str(raw or ""))
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = part.strip()
        name = identity.bare_tag(text)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(identity.clamp_weight(text))
    return out


def _own_slot_filter(facet: str, tags: list[str]) -> tuple[list[str], list[str]]:
    """Drop tags whose slot belongs to a different facet."""
    kept: list[str] = []
    rejected: list[str] = []
    for tag in tags:
        slot = conflict.slot_of(identity.bare_tag(tag))
        owner = _SLOT_OWNER.get(slot or "")
        if owner and owner != facet:
            rejected.append(tag)
            continue
        kept.append(tag)
    return kept, rejected


def _resolve_gaze_behind(tags: list[str]) -> list[str]:
    """Within one camera facet: a back view cannot also face the viewer.

    Unless she turned her head, which is the common case and the reason this
    cannot be a slot in `tags.conflict`.
    """
    names = {identity.bare_tag(t) for t in tags}
    if not (names & _BEHIND) or not (names & _FACING_VIEWER):
        return tags
    if names & _TURNED_HEAD:
        return tags
    return [t for t in tags if identity.bare_tag(t) not in _FACING_VIEWER]


def write(
    session: dict[str, Any], facet: str, *,
    tags: Any = None, nl: str | None = None, by: str = "",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace one facet whole. The only writer.

    Returns a report of what the write did — the tags it refused, the tags it
    evicted from other facets, and the locked facets it could not reconcile —
    so the caller can put it in the ledger and the panel can explain it.
    """
    table = table_of(session)
    if facet not in FACET_NAMES:
        raise ValueError(f"unknown facet: {facet}")
    slot = table[facet]
    report: dict[str, Any] = {
        "facet": facet, "rejected": [], "evicted": {}, "blocked": [],
        "written": False,
    }
    if slot.get("locked"):
        # A locked facet is the Showrunner saying "not this one". A turn that
        # tried is worth surfacing, but it is not an error.
        report["blocked"].append(facet)
        return report

    if tags is not None:
        kept, rejected = _own_slot_filter(facet, parse_tags(tags))
        if facet == "camera":
            kept = _resolve_gaze_behind(kept)
        # The Showrunner's refusals outrank whoever just wrote this, and the
        # locked body outranks everyone.
        from .service import drop_banned  # circular at import time, not at call
        kept = parse_tags(drop_banned(session, ", ".join(kept)))
        kept = parse_tags(identity.drop_conflicting_tags(
            ", ".join(kept), _identity_tags(session),
        ))
        # This is the whole thesis. Nothing removed the old tags; the facet they
        # lived in was replaced, and they were only ever in it.
        slot["tags"] = kept
        report["rejected"] = rejected

        evicted = _evict_conflicts(table, facet, kept)
        report["evicted"] = {k: v for k, v in evicted.items() if v}
        report["blocked"].extend(_locked_conflicts(table, facet, kept))

    if nl is not None:
        slot["nl"] = str(nl or "").strip()
    if fields is not None:
        slot["fields"] = dict(fields)

    slot["by"] = by or slot.get("by") or ""
    slot["at"] = time.time()
    slot["rev"] = int(slot.get("rev") or 0) + 1
    report["written"] = True
    if report["rejected"]:
        logger.info(
            "[muse.facets] %s does not own: %s", facet,
            ", ".join(report["rejected"][:8]),
        )
    if report["evicted"]:
        logger.info("[muse.facets] %s write evicted %s", facet, report["evicted"])
    return report


def _identity_tags(session: dict[str, Any]) -> list[str]:
    return [
        str(t) for t in ((session.get("character") or {}).get("identity_tags") or [])
        if str(t).strip()
    ]


def _evict_conflicts(
    table: dict[str, dict[str, Any]], facet: str, new_tags: list[str],
) -> dict[str, list[str]]:
    """Take contradicting tags out of the OTHER unlocked facets.

    A gaze tag that leaked into `pose` in an older session, or an hour tag the
    place facet picked up, is exactly what survived a camera move before. A
    locked facet is left alone on purpose — see `_locked_conflicts`.
    """
    names = [identity.bare_tag(t) for t in new_tags]
    out: dict[str, list[str]] = {}
    for other, _ in FACETS:
        if other == facet:
            continue
        slot = table[other]
        if slot.get("locked"):
            continue
        kept: list[str] = []
        gone: list[str] = []
        for tag in slot.get("tags") or []:
            if conflict.contradicts_any(identity.bare_tag(tag), names):
                gone.append(tag)
                continue
            kept.append(tag)
        if gone:
            slot["tags"] = kept
            out[other] = gone
    return out


def _locked_conflicts(
    table: dict[str, dict[str, Any]], facet: str, new_tags: list[str],
) -> list[str]:
    """Locked facets that disagree with this write, so the panel can say so."""
    names = [identity.bare_tag(t) for t in new_tags]
    out: list[str] = []
    for other, _ in FACETS:
        if other == facet or not table[other].get("locked"):
            continue
        if any(
            conflict.contradicts_any(identity.bare_tag(t), names)
            for t in table[other].get("tags") or []
        ):
            out.append(other)
    return out


def strike(session: dict[str, Any], gone: set[str] | frozenset[str]) -> list[str]:
    """Take a set of refused tags out of every part of the shot.

    Returns the parts that lost something, because losing a tag makes that
    part's sentence wrong and the sentence is half the prompt. The prose is
    dropped rather than edited — a sentence naming a thing that is no longer in
    the picture is worse than no sentence, and the part is about to be rewritten
    anyway. Locked parts are swept too: a refusal outranks a pin.
    """
    if not gone:
        return []
    table = table_of(session)
    touched: list[str] = []
    for name, _ in FACETS:
        slot = table[name]
        kept = [t for t in slot["tags"] if identity.bare_tag(t) not in gone]
        if len(kept) == len(slot["tags"]):
            continue
        slot["tags"] = kept
        slot["nl"] = ""
        if name == "costume":
            slot["fields"] = {}
        slot["rev"] = int(slot.get("rev") or 0) + 1
        touched.append(name)
    return touched


def set_lock(session: dict[str, Any], facet: str, locked: bool) -> dict[str, Any]:
    table = table_of(session)
    if facet not in FACET_NAMES:
        raise ValueError(f"unknown facet: {facet}")
    table[facet]["locked"] = bool(locked)
    return table[facet]


def all_tags(table: dict[str, dict[str, Any]]) -> str:
    """Every facet's tags as one comma string, in `TAG_ORDER`, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for facet in TAG_ORDER:
        for tag in (table.get(facet) or {}).get("tags") or []:
            name = identity.bare_tag(tag)
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(tag)
    return ", ".join(out)


def nl_join(table: dict[str, dict[str, Any]]) -> str:
    """The facet sentences as one paragraph, in `FACETS` order.

    A valid SCENE with no model call at all. `compose` makes this read better;
    it is never the thing that makes the shot exist, because a panel that has to
    wait on a model to show the Showrunner what he just asked for is a panel
    that looks broken every time he types.
    """
    parts: list[str] = []
    for name, _ in FACETS:
        text = str((table.get(name) or {}).get("nl") or "").strip()
        if not text:
            continue
        if not text.endswith((".", "!", "?")):
            text = f"{text}."
        parts.append(text)
    return " ".join(parts)


def table_rev(table: dict[str, dict[str, Any]]) -> int:
    """How many times this shot has been written, all facets together.

    The compose cache key: an unchanged table composes to the same prose, so it
    is not composed twice.
    """
    return sum(int((table.get(n) or {}).get("rev") or 0) for n, _ in FACETS)


def table_block(
    table: dict[str, dict[str, Any]], *, facets: list[str] | None = None,
) -> str:
    """The table as the LLM reads it. The analogue of `brief.plan_block`."""
    want = [n for n, _ in FACETS if facets is None or n in facets]
    lines: list[str] = []
    for name in want:
        slot = table.get(name) or {}
        label = FACET_LABELS[name]
        tags = ", ".join(slot.get("tags") or [])
        nl = str(slot.get("nl") or "").strip()
        if not tags and not nl:
            lines.append(f"{label}: (not set yet)")
            continue
        lock = " [LOCKED — the Showrunner fixed this]" if slot.get("locked") else ""
        lines.append(f"{label} TAGS:{lock} {tags or '(none)'}")
        lines.append(f"{label}: {nl or '(not written yet)'}")
    return "\n".join(lines)


def standing_block(standing: list[str] | None) -> str:
    kept = [str(s).strip() for s in (standing or []) if str(s).strip()]
    if not kept:
        return ""
    return "\n".join([
        "STANDING RULES (true for the whole shoot, whatever else changes):",
        *[f"- {s}" for s in kept],
    ])


# ── Projections onto the blocks the rest of Muse already reads ──────────────
# `plan` and `costume` are not a second source of truth any more; they are this
# table, in the shape `brief.plan_block` / `brief.costume_block` expect. Keeping
# them derived is what lets the crewed studio, the report and the brief carry on
# untouched while the duet path moves.

def to_plan(table: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "place": str((table.get("place") or {}).get("nl") or "").strip(),
        "hour": str((table.get("hour") or {}).get("nl") or "").strip(),
        "light": str((table.get("light") or {}).get("nl") or "").strip(),
        "action": str((table.get("pose") or {}).get("nl") or "").strip(),
        "must_appear": [
            identity.bare_tag(t)
            for t in ((table.get("props") or {}).get("tags") or [])
            if identity.bare_tag(t)
        ],
    }


def to_costume(table: dict[str, dict[str, Any]]) -> dict[str, Any]:
    slot = table.get("costume") or {}
    fields = dict(slot.get("fields") or {})
    tags = [
        identity.bare_tag(t) for t in (slot.get("tags") or [])
        if identity.bare_tag(t)
    ]
    if not fields and not tags:
        # {} is "Wardrobe has not spoken", and `costume_block` renders nothing
        # for it. An empty dict of empty strings would render a LOCKED header
        # over eight blank lines.
        return {}
    return {**fields, "tags": tags}


def from_costume_block(
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Split a parsed COSTUME block into (descriptive fields, garment tags).

    `chain._strip_costume` already reads the eight labelled lines and
    `brief.garment_tags` already turns the GARMENTS slots into a tag list. This
    only decides which half is which, so there is still exactly one parser and
    one garment authority.
    """
    fields = {k: v for k, v in (parsed or {}).items() if k != "garments"}
    if (parsed or {}).get("garments"):
        fields["garments"] = parsed["garments"]
    return fields, brief_mod.garment_tags(parsed or {})


# ── Migration ───────────────────────────────────────────────────────────────

# Where a tag goes when an old session is opened for the first time. Slot
# ownership answers most of it; these cover the craft vocabulary that names no
# slot but is plainly one part of the shot.
#
# There is deliberately no garment list and no place list here. Both are open
# vocabularies that no word list covers, and enumerating either means naming
# specific clothes and specific locations in shipped Muse copy — which is the
# thing `test_production_muse_copy_has_no_situation_specific_anchors` exists to
# stop, because a sample scene in the source ends up anchoring every theme. The
# outfit is recovered from the session's own COSTUME block instead, which is
# authoritative; everything still unmatched falls to `props`, the "stuff in the
# frame" bucket and the safest place to be wrong.
_MIGRATE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("light", (
        "light", "lighting", "shadow", "backlit", "backlighting", "sunlight",
        "sunbeam", "glow", "glare", "silhouette", "rim_", "god_rays", "lens_flare",
        "bloom", "dappled", "contre-jour", "spotlight", "moonlight", "firelight",
    )),
    ("expression", (
        "smile", "smiling", "blush", "frown", "pout", "grin", "laugh", "tears",
        "crying", "surprised", "embarrassed", "serious", "eyes", "mouth", "teeth",
        "eyebrow", "expression", "gaze",
    )),
    ("camera", (
        "shot", "angle", "view", "focus", "depth_of_field", "bokeh", "framing",
        "perspective", "foreshortening", "fisheye", "dutch_angle",
    )),
    ("pose", (
        "arm", "leg", "hand", "knee", "hip", "shoulder", "head_tilt", "leaning",
        "stretching", "reaching", "holding", "posture", "pose", "back_arch",
    )),
)


def _facet_for_tag(tag: str) -> str:
    """Best guess at which part of the shot an old, unclassified tag belongs to."""
    name = identity.bare_tag(tag)
    slot = conflict.slot_of(name)
    owner = _SLOT_OWNER.get(slot or "")
    if owner:
        return owner
    for facet, hints in _MIGRATE_HINTS:
        if any(h in name for h in hints):
            return facet
    return "props"


def migrate(session: dict[str, Any]) -> dict[str, Any]:
    """Build the facet table for a session written before it existed.

    Idempotent and cheap — called on every load. Non-destructive: `craft`,
    `plan`, `costume`, `notes` and `banned` all stay in the payload, so rolling
    the code back restores the old behaviour with no data loss.

    The table is built FROM the craft, and `composed` is seeded with the craft's
    own prose at the table's own revision, so a session with a live board keeps
    its exact positive down to the byte.
    """
    session.setdefault("directives", {})
    session.setdefault("standing", [])
    session.setdefault("digest", "")
    session.setdefault("composed", {"scene": "", "rev": 0, "at": 0.0})
    if isinstance(session.get("facets"), dict) and session["facets"]:
        table_of(session)
        return session

    table = blank_table()
    plan = session.get("plan") or {}
    craft = session.get("craft") or {}
    costume = session.get("costume") or {}
    now = time.time()

    def _set(facet: str, *, tags: list[str] | None = None, nl: str = "") -> None:
        if not tags and not nl.strip():
            return
        table[facet]["tags"] = parse_tags(tags or [])
        table[facet]["nl"] = nl.strip()
        table[facet]["at"] = now
        table[facet]["rev"] = 1

    _set("place", nl=str(plan.get("place") or ""))
    _set("hour", nl=str(plan.get("hour") or ""))
    _set("light", nl=str(plan.get("light") or ""))
    # ACTION is what she is doing, which is the pose. `pose_intent` is the same
    # thing said at more length, so it wins when both are present.
    _set("pose", nl=str(craft.get("pose_intent") or plan.get("action") or ""))
    _set("props", tags=[
        identity.bare_tag(t) for t in (plan.get("must_appear") or [])
        if identity.bare_tag(t)
    ])

    if costume:
        fields = {k: v for k, v in costume.items() if k != "tags"}
        table["costume"]["fields"] = fields
        table["costume"]["tags"] = parse_tags(
            list(costume.get("tags") or []) or brief_mod.garment_tags(costume)
        )
        table["costume"]["at"] = now
        table["costume"]["rev"] = 1

    # Everything else in the flat bag, spread over the parts it was always
    # about. Order within a facet follows the order it was written in.
    placed: dict[str, int] = {}
    known = {identity.bare_tag(t) for t in table["props"]["tags"]}
    known |= {identity.bare_tag(t) for t in table["costume"]["tags"]}
    for tag in parse_tags(str(craft.get("tags") or "")):
        if identity.bare_tag(tag) in known:
            continue
        facet = _facet_for_tag(tag)
        table[facet]["tags"].append(tag)
        table[facet]["rev"] = max(int(table[facet]["rev"] or 0), 1)
        table[facet]["at"] = now
        placed[facet] = placed.get(facet, 0) + 1

    session["facets"] = table
    scene = str(craft.get("scene") or "").strip()
    if scene:
        # Held at the table's own revision so the very next `_reassemble`
        # reproduces the prose this session was already rendering.
        session["composed"] = {"scene": scene, "rev": table_rev(table), "at": now}
    if placed:
        logger.info(
            "[muse.facets] migrated %s: %s",
            session.get("session_id", "?"),
            ", ".join(f"{k}={v}" for k, v in sorted(placed.items())),
        )
    return session


# ── The compose post-check ──────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z][a-z'-]{3,}")

# Prose glue. A composed paragraph is mostly these, and flagging them would
# drown the words that matter.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "her", "his", "she", "with", "from", "that", "this", "into",
    "over", "under", "onto", "across", "against", "through", "while", "where",
    "which", "their", "them", "they", "have", "has", "been", "being", "were",
    "was", "are", "its", "it's", "each", "both", "than", "then", "there",
    "here", "just", "only", "even", "still", "very", "more", "most", "some",
    "such", "same", "other", "another", "around", "between", "behind", "before",
    "after", "above", "below", "down", "back", "away", "toward", "towards",
    "along", "past", "near", "close", "half", "one", "two", "three", "her",
    "him", "hers", "own", "self", "body", "face", "eyes", "hair", "hand",
    "hands", "head", "look", "looks", "looking", "holds", "holding", "held",
    "sits", "sitting", "stands", "standing", "leans", "leaning", "keeps",
    "kept", "makes", "made", "gives", "given", "turns", "turning", "turned",
    "falls", "falling", "fallen", "rests", "resting", "catches", "catching",
    "light", "lights", "lit", "shot", "frame", "framed", "camera", "angle",
    "soft", "warm", "cool", "pale", "dark", "bright", "faint", "thin", "wide",
    "long", "short", "small", "large", "tall", "deep", "high", "low", "full",
    "left", "right", "front", "side", "edge", "line", "lines", "shape",
    "against", "across", "beneath", "within", "without", "about", "like",
})


def _vocabulary(
    table: dict[str, dict[str, Any]], extra: list[str] | None = None,
) -> set[str]:
    """Every word the composer is allowed to have got from somewhere."""
    words: set[str] = set()
    for name, _ in FACETS:
        slot = table.get(name) or {}
        for tag in slot.get("tags") or []:
            words.update(_WORD_RE.findall(identity.bare_tag(tag).replace("_", " ")))
        words.update(_WORD_RE.findall(str(slot.get("nl") or "").lower()))
        for value in (slot.get("fields") or {}).values():
            words.update(_WORD_RE.findall(str(value).lower().replace("_", " ")))
    for text in extra or []:
        words.update(_WORD_RE.findall(str(text).lower().replace("_", " ")))
    return words


# A composed paragraph is allowed to be a little florid — it is prose, and a
# bald tag list renders worse. This is the point at which "florid" stops being
# the explanation and the composer is plainly writing its own scene. Tunable;
# raise it if good compositions are being thrown away.
INVENTION_LIMIT = 8


def warn_invented_nouns(
    table: dict[str, dict[str, Any]], scene: str, *,
    banned: list[str] | None = None, extra: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Did the composer put something in the picture that is not in the table?

    Returns `(usable, invented)`. A refused word is never usable — that is the
    one list where a leak reaches a render. Everything else is logged and kept
    unless it is gross, in the spirit of `identity.warn_reference_leak`.
    """
    text = str(scene or "").lower()
    for tag in banned or []:
        name = identity.bare_tag(tag).replace("_", " ")
        if name and name in text:
            logger.warning(
                "[muse.facets] compose named a refused thing (%s) — discarded", name,
            )
            return False, [name]

    known = _vocabulary(table, extra)
    invented = [
        w for w in dict.fromkeys(_WORD_RE.findall(text))
        if w not in known and w not in _STOPWORDS
    ]
    if invented:
        logger.warning(
            "[muse.facets] compose invented %d word(s): %s",
            len(invented), ", ".join(invented[:8]),
        )
    return len(invented) <= INVENTION_LIMIT, invented

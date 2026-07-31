"""Tag shape and routing helpers.

Buckets are decided by the producer (preset buckets, LLM fields) and, where a
loose tag must still be placed, by the WD14-derived classifier in
``app.tags.catalog``. There is deliberately no hand-written vocabulary here:
the previous 30-word outfit regex misfiled 16 of 19 real garment tags —
``swimsuit`` and ``bikini`` among them — because anything it did not match fell
through to identity and got locked to the character forever.
"""
from __future__ import annotations

# What a danbooru-style tag looks like. Anything else (a sentence, a Japanese
# phrase) is not forced into tag shape — it goes to the prompt's prose side.
_MAX_TAG_WORDS = 4
_MAX_TAG_CHARS = 40
_SENTENCE_MARKS = (".", ",", "!", "?", ";", ":", "'", '"')

_IDENTITY_SUBJECTS = frozenset({
    "1girl", "1boy", "1other", "solo", "multiple_girls", "multiple_boys",
    "adult_male", "adult_female",
})

# axis (app.tags.catalog.get_tag_axis) → compile layer
AXIS_TO_LAYER: dict[str, str] = {
    "hair": "identity",
    "always_fixed": "identity",
    "parts": "identity",
    "clothing": "outfit",
    "action": "action",
    "emotion": "emotion",
    "location": "environment",
    "time_weather": "environment",
    "visual": "environment",
}


def soft_normalize_tag(tag: str) -> str:
    t = (tag or "").strip().lower().replace(" ", "_").replace("-", "_")
    if t == "close_up":
        return "close-up"
    return t


def is_tag_like(value: str) -> bool:
    """True when a value can go in the tag list without being mangled.

    Prose fails here and is routed to the prose side of the prompt instead of
    being underscore-joined into a fake tag like
    ``the_character's_strained_expression_during_the_peak_rotation_of_the_ride.``
    """
    raw = (value or "").strip()
    if not raw or not raw.isascii():
        return False
    if len(raw) > _MAX_TAG_CHARS:
        return False
    if any(mark in raw for mark in _SENTENCE_MARKS):
        return False
    return len(raw.replace("_", " ").split()) <= _MAX_TAG_WORDS


def tag_layer(tag: str) -> str | None:
    """Which compile layer a loose tag belongs to, or None when unclassified.

    None is not a failure: the caller passes those through as natural language,
    which the image models understand.
    """
    t = soft_normalize_tag(tag)
    if not t:
        return None
    if t in _IDENTITY_SUBJECTS:
        return "identity"
    from .catalog import get_tag_axis

    axis = get_tag_axis(t) or get_tag_axis(t.replace("-", "_"))
    return AXIS_TO_LAYER.get(axis or "")


def split_identity_and_outfit(tags: list[str] | None) -> tuple[list[str], list[str]]:
    """Return (identity, outfit) — only moving what the classifier calls clothing.

    A tag the classifier does not know (``geta``, ``thighhighs``) stays in
    identity rather than being guessed at; the story's own ``outfit_tags``
    override the wardrobe anyway, and unknowns reach the image as prose.
    """
    ident: list[str] = []
    outfit: list[str] = []
    for raw in tags or []:
        t = soft_normalize_tag(raw)
        if not t:
            continue
        target = outfit if tag_layer(t) == "outfit" else ident
        if t not in target:
            target.append(t)
    return ident, outfit


def is_prop_tag(tag: str) -> bool:
    """Accessories / held objects, per the classifier."""
    t = soft_normalize_tag(tag)
    if not t or t in _IDENTITY_SUBJECTS:
        return False
    from .catalog import ACCESSORIES

    return t in ACCESSORIES


def enforce_identity_prop_split(
    identity_tags: list[str] | None,
    prop_tags: list[str] | None = None,
    *,
    signature_prop: str = "",
) -> tuple[list[str], list[str], str]:
    """Return (identity, props, signature_prop).

    Only moves a tag when the classifier is sure: a declared prop stays a prop,
    and an identity tag is relocated solely if the classifier calls it an
    accessory. Unknown tags stay exactly where the producer put them.
    """
    ident: list[str] = []
    props: list[str] = []
    seen_i: set[str] = set()
    seen_p: set[str] = set()

    for raw in identity_tags or []:
        t = soft_normalize_tag(raw)
        if not t:
            continue
        if is_prop_tag(t):
            if t not in seen_p:
                props.append(t)
                seen_p.add(t)
            continue
        if t not in seen_i:
            ident.append(t)
            seen_i.add(t)

    for raw in prop_tags or []:
        t = soft_normalize_tag(raw)
        if not t or t in seen_p:
            continue
        props.append(t)
        seen_p.add(t)

    sig = soft_normalize_tag(signature_prop)
    if sig:
        if sig not in seen_p:
            props.append(sig)
            seen_p.add(sig)
        # The throughline prop is never part of the locked identity.
        ident = [t for t in ident if t != sig]
    elif props:
        sig = props[0]

    return ident, props, sig

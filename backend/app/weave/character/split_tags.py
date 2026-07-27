"""Force identity vs prop tag separation (P5)."""
from __future__ import annotations

import re

_PROP_HINTS = frozenset({
    "bookmark", "cloth_bookmark", "book", "umbrella", "bag", "tote_bag",
    "handbag", "briefcase", "lantern", "candle", "key", "letter", "envelope",
    "cup", "mug", "phone", "smartphone", "flower", "bouquet", "scarf",
    "gloves", "watch", "pendant", "necklace", "ring", "brooch",
})

# Tags that belong on the body / face / outfit — never treat as prop alone.
_IDENTITY_HINTS = frozenset({
    "1girl", "1boy", "1other", "solo", "multiple_girls", "multiple_boys",
    "adult_male", "adult_female",
})

_HAIR_EYE_RE = re.compile(
    r"(hair|eyes?|bangs|ponytail|twintails|braid|ahoge|sidelocks|"
    r"skin|mole|freckles|hetero)$",
    re.I,
)
_OUTFIT_RE = re.compile(
    r"(shirt|skirt|dress|coat|jacket|cardigan|hoodie|sweater|blouse|"
    r"uniform|kimono|yukata|apron|pants|jeans|shorts|boots|shoes|"
    r"sneakers|sandals|socks|ribbon|bow|hat|cap|beret|cloak|vest|"
    r"coverall|overalls|serafuku|sailor)$",
    re.I,
)


def soft_normalize_tag(tag: str) -> str:
    t = (tag or "").strip().lower().replace(" ", "_").replace("-", "_")
    if t == "close_up":
        return "close-up"
    return t


def _is_prop_tag(tag: str) -> bool:
    t = soft_normalize_tag(tag)
    if not t or t in _IDENTITY_HINTS:
        return False
    if t in _PROP_HINTS:
        return True
    for hint in _PROP_HINTS:
        if hint in t:
            return True
    return False


def _is_identity_tag(tag: str) -> bool:
    t = soft_normalize_tag(tag)
    if not t:
        return False
    if t in _IDENTITY_HINTS:
        return True
    if _is_prop_tag(t):
        return False
    if _HAIR_EYE_RE.search(t) or t.endswith("_hair") or t.endswith("_eyes"):
        return True
    if _OUTFIT_RE.search(t):
        return True
    # Colors often prefix identity (brown_hair already caught); keep bare colors out.
    return False


def enforce_identity_prop_split(
    identity_tags: list[str] | None,
    prop_tags: list[str] | None = None,
    *,
    signature_prop: str = "",
) -> tuple[list[str], list[str], str]:
    """Return (identity_tags, prop_tags, signature_prop) with props removed from identity."""
    ident: list[str] = []
    props: list[str] = []
    seen_i: set[str] = set()
    seen_p: set[str] = set()

    for raw in identity_tags or []:
        t = soft_normalize_tag(raw)
        if not t:
            continue
        if _is_prop_tag(t):
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
        # If someone put hair into prop_tags, bounce to identity.
        if _is_identity_tag(t) and not _is_prop_tag(t):
            if t not in seen_i:
                ident.append(t)
                seen_i.add(t)
            continue
        props.append(t)
        seen_p.add(t)

    sig = soft_normalize_tag(signature_prop)
    if sig:
        if sig not in seen_p:
            props.append(sig)
            seen_p.add(sig)
        # Never keep signature prop inside identity.
        ident = [t for t in ident if t != sig]
    elif props:
        sig = props[0]

    return ident, props, sig

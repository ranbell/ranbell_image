"""Resolve must_show reference keys to concrete tags (P6)."""
from __future__ import annotations

from typing import Any

from ..character.split_tags import soft_normalize_tag


_REF_KEYS = frozenset({
    "throughline_prop",
    "throughline_place",
    "signature_prop",
})


def _place_tags(place: str) -> list[str]:
    p = soft_normalize_tag(place)
    if not p:
        return []
    has_latin = any(c.isascii() and c.isalpha() for c in place)
    # A Japanese place name is not a danbooru tag — emitting it poisoned the
    # prompt. Only the mapped expansions below survive; world.place_tags is the
    # real route for the location.
    out = [p] if has_latin else []
    if "bookstore" in p or "bookshop" in p or "書店" in place:
        out.extend(["bookstore", "bookshelf"])
    if "cafe" in p or "カフェ" in place:
        out.append("cafe")
    if "station" in p or "駅" in place:
        out.append("train_station")
    if "classroom" in p or "教室" in place:
        out.append("classroom")
    # No indoors/outdoors guess: it used to tag outdoor topics (花火大会) indoors.
    return list(dict.fromkeys(t for t in out if t))


def _prop_tags(prop: str) -> list[str]:
    p = soft_normalize_tag(prop)
    return [p] if p else []


def resolve_must_show(
    must_show: list[str] | None,
    *,
    world: dict[str, Any] | None = None,
    character: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Return (resolved_tags, unresolved_keys)."""
    world = world or {}
    character = character or {}
    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    for raw in must_show or []:
        key = (raw or "").strip()
        if not key:
            continue
        norm = soft_normalize_tag(key)
        if norm in _REF_KEYS or key in _REF_KEYS:
            ref = norm or key
            if ref == "throughline_prop":
                tags = _prop_tags(str(world.get("throughline_prop") or ""))
            elif ref == "throughline_place":
                tags = _place_tags(str(world.get("throughline_place") or ""))
            else:  # signature_prop
                tags = _prop_tags(str(character.get("signature_prop") or ""))
            if not tags:
                unresolved.append(ref)
                continue
            for t in tags:
                if t not in seen:
                    resolved.append(t)
                    seen.add(t)
            continue

        # Literal tag / short phrase
        lit = soft_normalize_tag(key) if key.isascii() else key
        # For Japanese literals, keep as-is in resolved for binder visibility;
        # compile will prefer ASCII tags from throughline fields.
        token = lit if lit else key
        if token not in seen:
            resolved.append(token)
            seen.add(token)

    return resolved, unresolved


def apply_must_show_resolution(story_bundle: dict[str, Any], character: dict[str, Any]) -> list[str]:
    """Mutate panels with must_show_resolved. Return all unresolved keys across panels."""
    world = story_bundle.get("world") or {}
    bad: list[str] = []
    for panel in story_bundle.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        resolved, unresolved = resolve_must_show(
            panel.get("must_show"),
            world=world,
            character=character,
        )
        panel["must_show_resolved"] = resolved
        for u in unresolved:
            if u not in bad:
                bad.append(u)
    return bad

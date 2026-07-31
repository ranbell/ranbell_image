"""Shot framing, chosen by the user rather than by whatever the drafts drifted to.

Three board renders at three seeds produce three framings, and the merge keeps
whichever survived the budget. One run ended up with ``full_body``, ``cowboy_shot``
and ``close-up`` in the same prompt; another put the figure large in the
foreground of a wide resort shot because nobody had said otherwise.

So the framing is a choice with a name, and choosing one *removes* the others.
That second half is the part that matters: a diffusion model handed two
contradictory framing tags does not average them, it picks one, and which one
is not something the prompt controls.

Adapted from Chronicle's camera tables, which had the same job.
"""
from __future__ import annotations

# The user-facing list, widest first. `auto` keeps whatever the drafts produced.
SHOTS: tuple[str, ...] = (
    "auto", "wide_shot", "full_body", "cowboy_shot", "upper_body", "close_up",
)

# Tags that state the chosen framing. Put at the head of the prompt.
_FORCE_ADD: dict[str, list[str]] = {
    "wide_shot": ["wide_shot", "scenery", "full_body"],
    "full_body": ["full_body", "standing"],
    "cowboy_shot": ["cowboy_shot"],
    "upper_body": ["upper_body"],
    "close_up": ["close-up", "portrait", "detailed_face"],
}

# Tags that fight the chosen framing. Removed from the prompt entirely — leaving
# them in and hoping the chosen one wins is how the framing became a coin toss.
_FORCE_REMOVE: dict[str, list[str]] = {
    "wide_shot": [
        "close-up", "close_up", "extreme_close-up", "portrait", "face_focus",
        "headshot", "upper_body", "cowboy_shot", "detailed_face", "bust",
    ],
    "full_body": [
        "close-up", "close_up", "extreme_close-up", "portrait", "face_focus",
        "headshot", "upper_body", "bust",
    ],
    "cowboy_shot": [
        "close-up", "close_up", "extreme_close-up", "face_focus", "headshot",
        "full_body", "wide_shot", "long_shot", "panoramic",
    ],
    "upper_body": [
        "full_body", "wide_shot", "long_shot", "panoramic", "cowboy_shot",
        "extreme_close-up", "feet", "legs_apart",
    ],
    "close_up": [
        "full_body", "wide_shot", "long_shot", "panoramic", "cowboy_shot",
        "upper_body", "scenery", "small_figure",
    ],
}

# What to also say in the negative. A wide shot is the one that needs it most:
# checkpoints love pulling the subject forward until it is a portrait again.
_NEGATIVE: dict[str, list[str]] = {
    "wide_shot": ["close-up", "portrait", "face focus", "cropped"],
    "full_body": ["close-up", "portrait", "cropped legs"],
    "cowboy_shot": ["extreme close-up", "full body"],
    "upper_body": ["full body", "wide shot"],
    "close_up": ["full body", "wide shot", "multiple views"],
}


def is_framing_tag(tag: str) -> bool:
    """True when this tag says something about how the shot is framed."""
    name = _key(tag)
    for removals in _FORCE_REMOVE.values():
        if name in {_key(t) for t in removals}:
            return True
    for adds in _FORCE_ADD.values():
        if name in {_key(t) for t in adds}:
            return True
    return False


def _key(tag: str) -> str:
    return str(tag or "").strip().lower().replace("-", "_").replace(" ", "_")


def apply(tags: list[str], shot: str) -> tuple[list[str], list[str]]:
    """``(tags, dropped)`` with ``shot``'s framing enforced.

    ``auto`` returns the list untouched, which is the honest thing to do when
    the user has not expressed a preference — the drafts had one.
    """
    if shot not in _FORCE_ADD:
        return list(tags), []

    banned = {_key(t) for t in _FORCE_REMOVE.get(shot, ())}
    kept: list[str] = []
    dropped: list[str] = []
    for tag in tags:
        (dropped if _key(tag) in banned else kept).append(tag)

    lead = [t for t in _FORCE_ADD[shot] if _key(t) not in {_key(k) for k in kept}]
    return lead + kept, dropped


def negative_for(shot: str) -> str:
    return ", ".join(_NEGATIVE.get(shot, ()))

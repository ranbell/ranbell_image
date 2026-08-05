"""The brief: one block of text that every stage of the chain is handed again.

Muse used to protect identity with machinery — protected tags reinserted at the
front, conflicting tags evicted by attribute slot, token-overlap checks. That is
gone. The brief *is* the soft mechanism: it is re-injected on every LLM call, and
each system prompt says the hair, the eyes, the figure and the clothing may not
change. The hard mechanism lives in ``identity.py``: tags stapled onto the Comfy
positive after the LLM answers.

The <REFERENCE> block is the other half. Personality, likes and dislikes make a
character behave differently in the same situation, but pasted into a prompt they
become things the picture must contain — a run that named `thermos coffee` in the
prompt drew a thermos on a rooftop with no coffee in the scene. Fencing the block
off and saying "do not copy these words" keeps the influence and drops the
contamination.

The fence tags are `</start REFERENCE ONLY>` and `</end REFERENCE ONLY>`. Both
are written with a closing slash. That is not a typo to fix — it is the exact
form that was tested, and a delimiter's only job is to be recognisable.
"""
from __future__ import annotations

from typing import Any

from .identity import normalize_framing

REFERENCE_HEADER = (
    "** This is her background — taste cues for how she would act, never props. "
    'Use it only to imagine "what she would do in this situation". '
    "Do not copy these keywords into the prompt, and do not place favorite "
    "objects, hated things, or a signature accessory into the scene unless the "
    "theme itself names them. **"
)
REFERENCE_OPEN = "</start REFERENCE ONLY>"
REFERENCE_CLOSE = "</end REFERENCE ONLY>"


def _line(label: str, values: list[str], sep: str = " · ") -> str:
    kept = [v.strip() for v in values if str(v).strip()]
    return f"{label}{sep.join(kept)}" if kept else ""


def build(
    character: dict[str, Any],
    theme: str,
    style: str,
    *,
    framing: str = "auto",
) -> str:
    """Character sheet + theme, in the shape the chain was validated against.

    The theme is last and unfenced: it is the only part the model is told is
    absolute, and it reads as the instruction the rest of the block serves.
    """
    personality = character.get("personality") or {}
    identity = [str(t) for t in (character.get("identity_tags") or []) if str(t).strip()]
    frame = normalize_framing(framing)

    head = [
        f"Style: {style.strip()}" if style.strip() else "",
        f"Framing: {frame}",
        f"Character: {', '.join(identity)}, " if identity else "",
    ]

    # Behaviour first. Concrete likes stay inside the fence as taste cues only.
    who = [
        "personality:",
        _line("", [str(t) for t in (personality.get("traits") or [])], ", "),
        str(personality.get("summary") or ""),
        _line("inner: ", [str(t) for t in (personality.get("inner") or [])]),
    ]
    tastes = [
        _line("taste cues (never props) — likes: ",
              [str(t) for t in (personality.get("likes") or [])]),
        _line("taste cues (never props) — dislikes: ",
              [str(t) for t in (personality.get("dislikes") or [])]),
        _line("favorite color:", [str(c) for c in (character.get("palette") or [])], ", "),
        _line(
            "signature accessory (only if the theme names it): ",
            [str(character.get("signature_prop") or "")],
        ),
    ]

    reference = "\n\n".join([
        "\n".join([REFERENCE_HEADER, REFERENCE_OPEN, *[w for w in who if w]]),
        "\n".join([*[t for t in tastes if t], REFERENCE_CLOSE]),
    ])
    return "\n\n".join([
        "\n".join(h for h in head if h),
        reference,
        theme.strip(),
    ])


def with_tags(brief: str, tags: str, *, pose: str = "") -> str:
    """Stage B's input: the brief, optional pose intent, then WD14 tags.

    Stage A's full prose is deliberately *not* carried forward. A short pose
    intent keeps the action; the tags describe the picture that exists.
    """
    parts = [brief]
    if pose.strip():
        parts.append(f"Pose intent: {pose.strip()}")
    body = "\n\n".join(parts)
    return f"{body},{tags}" if tags.strip() else body


def with_prompt(brief: str, prompt: str) -> str:
    """A later stage's input: the brief, then the prompt that made what it sees.

    The workflow this came from joined stage C's input with no separator at all
    and stage D's with a comma — the theme's last word ran straight into the next
    prompt's first. Both produced good images, so the comma is used for both here
    rather than reproducing an accident of two differently configured nodes.
    """
    return f"{brief},{prompt}" if prompt.strip() else brief

"""The brief: one block of text that every stage of the chain is handed again.

Muse used to protect identity with machinery — protected tags reinserted at the
front, conflicting tags evicted by attribute slot, token-overlap checks. That is
gone. The brief *is* the soft mechanism: it is re-injected on every LLM call, and
each system prompt says the hair, the eyes, the figure and the clothing may not
change. The hard mechanism lives in ``identity.py``: tags stapled onto the Comfy
positive after the LLM answers.

The <REFERENCE> block is the other half. Personality, likes and dislikes make a
character behave differently in the same situation, but pasted into a prompt they
become things the picture must contain — taste nouns leak into props. Fencing
the block off and saying "do not copy these words" keeps the influence and drops
the contamination.

The fence tags are `</start REFERENCE ONLY>` and `</end REFERENCE ONLY>`. Both
are written with a closing slash. That is not a typo to fix — it is the exact
form that was tested, and a delimiter's only job is to be recognisable.

Two blocks sit above the fence, and both exist because a run drifted without
them. PLAN is the place/hour/light/action the crew settled, re-stated every turn
so seventeen rewrites cannot quietly relocate the picture. STANDING ORDERS is
what the Showrunner has said out loud; a note used to live only in the turn that
answered it, so「make it X」was outvoted by the original theme on every later
call and never reached the render.
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
# A digest for every seat that is not acting. Lighting and colour do not need
# her inner life, and handing it to them is how her backstory became the mood
# language of the whole script.
DIGEST_HEADER = (
    "** Her behaviour cue — how she carries herself, nothing more. Never props, "
    "and never as mood or metaphor language in SCENE. **"
)
REFERENCE_OPEN = "</start REFERENCE ONLY>"
REFERENCE_CLOSE = "</end REFERENCE ONLY>"

# The fields the plan seat settles, in the order they are shown. LIGHT is one of
# them because exposure is the other thing that ratchets: every seat sharpened
# the previous seat's light in the same direction until the frame bottomed out.
PLAN_FIELDS: tuple[tuple[str, str], ...] = (
    ("place", "PLACE"),
    ("hour", "HOUR"),
    ("light", "LIGHT"),
    ("action", "ACTION"),
    # WEARING exists because the planner had no line for clothes and so put them
    # in MUST APPEAR, where a garment became "an object in this room" and was
    # re-chosen to suit whatever place the planner had picked. A theme that
    # named an outfit lost it that way, on every model tried. The planner does
    # not choose clothes now; it carries forward what the theme said, or says
    # outright that nothing was named.
    ("wearing", "WEARING"),
    ("must_appear", "MUST APPEAR"),
)

PLAN_HEADER = (
    "PLAN (LOCKED — the crew already settled this. Every noun below must survive "
    "to the render. Do not relocate, do not re-time, do not re-expose.)"
)
ORDERS_HEADER = (
    "SHOWRUNNER STANDING ORDERS (absolute — 総監督 said these and they stay said, "
    "on this turn and every turn after it)"
)


def plan_block(plan: dict[str, Any] | None) -> str:
    """The locked place/hour/light/action, in the shape every seat re-reads."""
    data = plan or {}
    lines: list[str] = []
    for key, label in PLAN_FIELDS:
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v).strip() for v in value if str(v).strip())
        value = str(value or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join([PLAN_HEADER, *lines]) if lines else ""


def orders_block(notes: list[str] | None) -> str:
    kept = [str(n).strip() for n in (notes or []) if str(n).strip()]
    return "\n".join([ORDERS_HEADER, *(f"- {n}" for n in kept)]) if kept else ""


def _line(label: str, values: list[str], sep: str = " · ") -> str:
    kept = [v.strip() for v in values if str(v).strip()]
    return f"{label}{sep.join(kept)}" if kept else ""


def build(
    character: dict[str, Any],
    theme: str,
    style: str,
    *,
    framing: str = "auto",
    plan: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    reference: str = "full",
) -> str:
    """Character sheet + theme, in the shape the chain was validated against.

    The theme is last and unfenced: it is the only part the model is told is
    absolute, and it reads as the instruction the rest of the block serves. PLAN
    and STANDING ORDERS are prepended rather than replacing it — the theme keeps
    the position it was validated in, and the locked nouns get a second, earlier
    statement. Saying the place twice is not redundancy here; it is the whole
    defence against a chain of rewrites drifting away from it.

    ``reference`` is "full" for the seats that act (the Lead, the acting
    animator) and "digest" for everybody else.
    """
    personality = character.get("personality") or {}
    identity = [str(t) for t in (character.get("identity_tags") or []) if str(t).strip()]
    outfit = [str(t) for t in (character.get("outfit_tags") or []) if str(t).strip()]
    frame = normalize_framing(framing)

    head = [
        f"Style: {style.strip()}" if style.strip() else "",
        f"Framing: {frame}",
        f"Character: {', '.join(identity)}, " if identity else "",
        # Outfit is locked craft for Wardrobe — not REFERENCE taste.
        f"Outfit: {', '.join(outfit)}, " if outfit else "",
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

    if reference == "digest":
        # Traits only. Summary, inner life, likes and dislikes are the material
        # that turned into everyone's mood vocabulary, and a gaffer has no use
        # for them.
        traits = _line("", [str(t) for t in (personality.get("traits") or [])], ", ")
        block = "\n".join([
            DIGEST_HEADER, REFERENCE_OPEN, "personality:",
            traits or "(unspecified)", REFERENCE_CLOSE,
        ])
    else:
        block = "\n\n".join([
            "\n".join([REFERENCE_HEADER, REFERENCE_OPEN, *[w for w in who if w]]),
            "\n".join([*[t for t in tastes if t], REFERENCE_CLOSE]),
        ])

    return "\n\n".join(b for b in [
        "\n".join(h for h in head if h),
        plan_block(plan),
        orders_block(notes),
        block,
        theme.strip(),
    ] if b)


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


def with_previous(
    brief: str, previous: str, *, pose: str = "", analysis: str = "",
) -> str:
    """Table-read / pickup input: brief, frozen pose, analysis, previous craft."""
    parts = [brief]
    if pose.strip():
        parts.append(f"Pose intent: {pose.strip()}")
    if analysis.strip():
        parts.append(f"Screening notes:\n{analysis.strip()}")
    body = "\n\n".join(parts)
    return f"{body},{previous}" if previous.strip() else body

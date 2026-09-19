"""Which image-model family a workflow belongs to, and what it wants.

**Muse had no notion of a model family.** A workflow is a `*.json` filename in
`settings.comfyui_workflows_dir`, chosen by name, and every render sent the same
numbers — 20/30 steps, cfg 4.0/4.5, and a negative prompt — because those are the
numbers Anima was validated at (`defaults.py`). The Showrunner: "I want to use
krea2 in a workflow too, but there are differences — it needs no negative prompt,
8 steps is enough."

So a family carries four things: how many steps the draft and the final want,
what cfg to ask for (**`None` means leave the workflow's own value alone**),
whether a negative prompt is sent at all, and **who decides the size of the
picture** — Muse's canvas, or the resolution the graph was saved at.

**Anima's steps are read from `defaults.py`, never copied** — 20 and 30 are the
numbers that pack of 30 test cases was validated at. Its cfg and its canvas are
the workflow's own (2026-09-20): those differ far more between checkpoints than
between stages, and the graph already carries the answer its author chose.

Resolution order is **marker → filename → anima**:

    the marker   a node titled `muse:family=krea2` (`_meta.title`)
    the filename anything matching a row of `NAME_PATTERNS`
    anima        everything else, which is every workflow that exists today

The marker lives in `_meta.title` because that is the one place a note survives
ComfyUI's API export — a `Note` node has no outputs and is pruned from the API
format, and a key of our own at the top level would be posted to ComfyUI as if it
were a node (`comfy.queue_prompt` sends the dict as the prompt).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .defaults import DRAFT_DEFAULTS, REFINE_DEFAULTS

logger = logging.getLogger("app.muse.family")

FAMILY_ANIMA = "anima"
FAMILY_KREA2 = "krea2"
#: What a workflow is when nothing says otherwise — today's behaviour, unchanged.
DEFAULT_FAMILY = FAMILY_ANIMA

#: Who decides the size of the picture. `muse` writes the session's canvas into
#: the graph (what every shoot has always done); `workflow` leaves the graph with
#: the resolution it was saved at.
CANVAS_MUSE = "muse"
CANVAS_WORKFLOW = "workflow"

FAMILIES: dict[str, dict[str, Any]] = {
    FAMILY_ANIMA: {
        "label": "Anima",
        # Read, not retyped: `defaults.py` is where these earned their numbers.
        "draft_steps": int(DRAFT_DEFAULTS["draft_steps"]),      # 20
        "final_steps": int(REFINE_DEFAULTS["final_steps"]),     # 30
        # **cfg belongs to the workflow (2026-09-20).** The Showrunner: "cfg
        # differs a great deal between image models, so I want to use what is
        # baked into the workflow." Muse used to write 4.0/4.5 into every graph —
        # the numbers `defaults.py` validated on one checkpoint, sent to all of
        # them. `defaults.DRAFT_DEFAULTS["draft_cfg"]` stays as the value an
        # explicit override starts from.
        "draft_cfg": None,
        "final_cfg": None,
        "negative": True,
        # **The canvas belongs to the workflow too.** His call: the session
        # renders at whatever the graph was saved at, and Muse's own size is kept
        # for the pictures in the roster (`characters/board.SLOT_SIZE`), which are
        # the ones that have to match each other.
        "canvas": CANVAS_WORKFLOW,
    },
    FAMILY_KREA2: {
        "label": "krea2",
        # The Showrunner's numbers (2026-09-20): "4/8 as the default, and let the
        # user change it after that."
        "draft_steps": 4,
        "final_steps": 8,
        # **None means the workflow keeps its own cfg.** His call: "leave it to
        # the workflow". `run_render(cfg=None)` reaches `patch_workflow`, which
        # only writes cfg when it is given one.
        "draft_cfg": None,
        "final_cfg": None,
        "negative": False,
        # **The workflow names the canvas.** The Showrunner: "krea2 can reach a
        # high-quality picture without the two-stage process; Anima builds its
        # images in two stages." Its graph is saved at the resolution it wants
        # (the sample is 1284x1824, 2.3MP) and writing Muse's 896x1152 over that
        # throws away exactly what the family is for. An explicit size still
        # wins — that is the other half of "both ways" he asked for.
        "canvas": CANVAS_WORKFLOW,
    },
}

#: Filename → family. **A new family is one row here.** Matched case-insensitively
#: anywhere in the name, because the files are named by hand and a prefix rule
#: breaks the first time one is called `flux_krea2_api.json`. First row wins.
NAME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"krea", re.I), FAMILY_KREA2),
    (re.compile(r"anima", re.I), FAMILY_ANIMA),
)

#: The marker a workflow may carry to name its own family.
MARKER_RE = re.compile(r"muse\s*:\s*family\s*=\s*([A-Za-z0-9_-]+)", re.I)


def settings_for(family: str) -> dict[str, Any]:
    """The family's row. An unknown name is the default family, never an error."""
    return dict(FAMILIES.get(str(family or ""), FAMILIES[DEFAULT_FAMILY]))


def sends_negative(family: str) -> bool:
    """Whether a render for this family carries a negative prompt at all."""
    return bool(settings_for(family).get("negative", True))


def render_overrides(family: str, *, draft: bool) -> dict[str, Any]:
    """The sampler knobs this family wants for one stage.

    `cfg` comes back as `None` when the family leaves it to the workflow — the
    caller passes that through, and nothing is written into the graph.
    """
    row = settings_for(family)
    prefix = "draft" if draft else "final"
    return {"steps": row.get(f"{prefix}_steps"), "cfg": row.get(f"{prefix}_cfg")}


def owns_canvas(family: str) -> bool:
    """Does Muse write the picture size, or does the workflow keep its own?"""
    return str(settings_for(family).get("canvas", CANVAS_MUSE)) == CANVAS_MUSE


def family_from_name(workflow_name: str) -> str:
    """The family a filename declares. `""` when it declares nothing."""
    name = str(workflow_name or "")
    for pattern, family in NAME_PATTERNS:
        if pattern.search(name):
            return family
    return ""


def family_from_graph(workflow: dict[str, Any] | None) -> str:
    """The family a graph declares in a node title. `""` when it declares none.

    Any node will do — the Showrunner retitles whichever one is convenient, and
    the API export keeps that title in `_meta.title`. A `Note` node's text is read
    too when one happens to survive, but it is not the documented place.
    """
    if not isinstance(workflow, dict):
        return ""
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        title = str(((node.get("_meta") or {}) if isinstance(node.get("_meta"), dict)
                     else {}).get("title") or "")
        found = MARKER_RE.search(title)
        if not found and str(node.get("class_type") or "") == "Note":
            text = (node.get("inputs") or {})
            found = MARKER_RE.search(str(text.get("text") or "")) if isinstance(text, dict) else None
        if found:
            named = found.group(1).lower()
            if named in FAMILIES:
                return named
            logger.warning("[muse.family] unknown family marker %r — using the name",
                           found.group(1))
    return ""


def resolve_family(workflow_name: str = "", workflow: dict[str, Any] | None = None) -> str:
    """Marker first, then the filename, then the default."""
    return (
        family_from_graph(workflow)
        or family_from_name(workflow_name)
        or DEFAULT_FAMILY
    )


def for_workflow(comfy: Any, workflow_name: str) -> str:
    """The family of a workflow by name, reading the graph when it can.

    A render must never fail because the marker could not be read: a missing file
    is the render's own error to raise a moment later, and a stubbed client in a
    test returns something that is not a graph at all. Either way the filename
    still answers.
    """
    graph: dict[str, Any] | None = None
    loader = getattr(comfy, "load_workflow", None)
    if loader is not None and str(workflow_name or "").strip():
        try:
            loaded = loader(workflow_name)
        except Exception:
            logger.debug("[muse.family] could not read %r for its marker",
                         workflow_name, exc_info=True)
        else:
            graph = loaded if isinstance(loaded, dict) else None
    return resolve_family(workflow_name, graph)

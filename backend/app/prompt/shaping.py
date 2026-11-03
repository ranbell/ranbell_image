"""Shaping the sheet in one call: placing leftover tags, staging, the objects.

Measured on the dev box (2026-11-03, two girls in an autumn classroom,
restage + elaborate + hints): 290 s over seven calls, and almost half of every
token was the model thinking. Each call thinks from scratch about the same
sheet — placing hints spent 377 thinking tokens on a 75-token answer, the
objects 916 on 398. The Showrunner: thinking off costs too much quality, so
join the tasks instead.

These three were already independent text calls on the same sheet, each a
patch on its own boxes. Asked together, they share one reading and one round
of thinking, and they can agree with each other (the objects can fit the
action the staging just chose). What reaches the sheet is unchanged: each
part of the answer goes through the door it always went through —
``scene_sheet.apply_staging``, ``props.apply_props``,
``scene_sheet.apply_placement`` — in that order, so placed tags are appended
last and do not shift the numbered objects.

The reconstruction stays a call of its own: it is the second look, and a look
inside the same call that made the changes would not be a second look.
"""
from __future__ import annotations

import json

from . import props as props_pass
from . import scene_sheet


def jobs(*, staging: str, props_mode: str, tags: list[str]) -> list[str]:
    """Which parts this run asks for, in the order they are applied."""
    out: list[str] = []
    if staging in scene_sheet.STAGING_SCOPES:
        out.append("staging")
    if props_mode in ("simplify", "elaborate"):
        out.append("objects")
    if tags:
        out.append("place")
    return out


def _staging_exclude(active: list[str]) -> tuple[str, ...]:
    # The objects job owns "props" when it runs: two jobs writing one box in the
    # same answer would leave whichever came last.
    return ("props",) if "objects" in active else ()


def shape_prompt(
    sheet: dict, *, staging: str, props_mode: str, tags: list[str],
    instruction: str = "", locked: list[str] | None = None,
) -> str:
    active = jobs(staging=staging, props_mode=props_mode, tags=tags)
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    names = {"staging": "stage the moment", "objects": "fit the objects to it",
             "place": "place the tagger's leftover tags"}
    lines = [
        "Below is the scene sheet a vision model read from a reference picture. You have "
        f"{len(active)} job{'s' if len(active) > 1 else ''} on it, answered together so that "
        "they agree with each other: " + "; ".join(names[j] for j in active) + ".",
    ]
    if "staging" in active:
        lines += [
            "",
            "[Job: stage the moment]",
            "As read, the sheet says how the people look and what the mood is, and the "
            "picture it makes is people standing and posing — nothing happens in it. Turn "
            "it into a moment where something is happening.",
            "",
            *scene_sheet.stage_rules(staging, locked=locked, exclude=_staging_exclude(active)),
        ]
        if "objects" in active:
            lines.append("- The objects are the next job's: describe them there, not here.")
    if "objects" in active:
        lines += [
            "",
            "[Job: fit the objects to it]",
            *(["Fit them to the moment as you have just staged it."] if "staging" in active else []),
            *props_pass.props_task(sheet, props_mode),
        ]
    if "place" in active:
        lines += [
            "",
            "[Job: place the tagger's leftover tags]",
            "A tagger read these from the same picture and the sheet does not mention them "
            "yet. Say which box each belongs in.",
            *scene_sheet.place_rules(tags),
            "",
            "[Tags]",
            *[f"- {t}" for t in tags],
        ]
    lines += ["", "Output only one JSON object, with one part per job:", "{"]
    if "staging" in active:
        lines += ['  "staging":', *scene_sheet.stage_answer_shape(staging, indent="  ")]
    if "objects" in active:
        lines += ['  "objects":', *props_pass.props_answer_shape(props_mode, indent="  ")]
    if "place" in active:
        lines.append('  "place": [{"tag": "<the tag as given>", "box": "<box>", "person": <index or null>}]')
    lines.append("}")
    if instruction:
        lines += ["", "[User instruction — the sheet already follows it; keep it]", instruction]
    lines += ["", "[Scene sheet]", json.dumps(indexed, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def _part(answer: dict, key: str) -> dict:
    value = answer.get(key) if isinstance(answer, dict) else None
    return value if isinstance(value, dict) else {}


def apply_shape(
    sheet: dict, answer: dict, *, staging: str, props_mode: str, tags: list[str],
    locked: list[str] | None = None,
) -> tuple[dict, dict]:
    """Each part of the answer through its own door, in order. Returns the new
    sheet and a report per job — the same lines the separate calls gave:

    ``{"staging": {"changed", "refused", "outside", "stock", "read"},
       "objects": {"changed", "refused", "dropped", "read"},
       "place": {"placed", "unclassified"}}``

    ``read`` is False when that job's part was missing or empty: said, so
    the run can say it, never silently taken as "nothing to change".
    """
    active = jobs(staging=staging, props_mode=props_mode, tags=tags)
    report: dict = {}
    if "staging" in active:
        part = _part(answer, "staging")
        new, changed, refused, outside = scene_sheet.apply_staging(
            sheet, part, staging, locked, exclude=_staging_exclude(active))
        sheet = new
        report["staging"] = {"changed": changed, "refused": refused, "outside": outside,
                             "stock": scene_sheet.stock_poses(part), "read": bool(part)}
    if "objects" in active:
        part = _part(answer, "objects")
        if part:
            new, changed, refused, dropped = props_pass.apply_props(sheet, part, props_mode, locked)
            sheet = new
        else:
            changed, refused, dropped = [], [], []
        report["objects"] = {"changed": changed, "refused": refused, "dropped": dropped,
                             "read": bool(part)}
    if "place" in active:
        place = answer.get("place") if isinstance(answer, dict) else None
        sheet, placed, unclassified = scene_sheet.apply_placement(
            sheet, {"place": place if isinstance(place, list) else []}, tags)
        report["place"] = {"placed": placed, "unclassified": unclassified}
    return sheet, report

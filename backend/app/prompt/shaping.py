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

The last look joins too (the Showrunner, 2026-11-04). The call is made with
the reference picture, and after the parts that change the sheet comes one
more part, "rebuild": the whole sheet checked as one picture — the same rules
``scene_sheet.reconstruct_prompt`` gives the last look when it runs alone —
applied last through ``scene_sheet.apply_reconstruction``. It is no longer a
second pair of eyes; it is the same answer reading back what it just wrote,
which is what the measured run could afford. When nothing here runs (only a
merge, a fusion or a retake changed the sheet), the last look still runs as a
call of its own.

And the prompt itself (2026-11-04): with ``script_model`` given, the answer
ends with the visual script, written from the sheet as all the parts above
leave it, after a marker line (``SCRIPT_MARKER``) — prose kept out of the JSON,
where its quotes would need escaping. The writer's own call is then only the
retry, when the draft is short, dropped a box or hedged.
"""
from __future__ import annotations

import json

from . import props as props_pass
from . import scene_sheet

#: The line between the JSON answer and the visual script that follows it.
SCRIPT_MARKER = "=== PROMPT ==="


def split_answer(raw: str) -> tuple[str, str]:
    """``(json part, script part)``. The script part is empty when the answer
    has no marker — the writer is then asked on its own, as before."""
    text = raw or ""
    if SCRIPT_MARKER not in text:
        return text, ""
    head, _, tail = text.partition(SCRIPT_MARKER)
    return head, tail.strip()


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
    reference_count: int = 1, earlier: list[dict] | None = None,
    script_model: str = "", paragraphs: int = 5,
    avoid: list[str] | None = None, subject: str = "",
) -> str:
    """``earlier`` is the ledger's steps so far (a merge, a fusion), so the
    rebuild part knows what had already been changed before this call.
    ``script_model`` ("krea2" / "moss") asks for the visual script after the
    JSON, behind ``SCRIPT_MARKER``."""
    active = jobs(staging=staging, props_mode=props_mode, tags=tags)
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    names = {"staging": "stage the moment", "objects": "fit the objects to it",
             "place": "place the tagger's leftover tags"}
    refs = ("the reference picture" if reference_count <= 1 else
            f"{reference_count} reference pictures")
    lines = [
        f"You are given {refs} and the scene sheet a vision model read from it. You have "
        f"{len(active) + 1} jobs on the sheet, answered together so that they agree with each "
        "other: " + "; ".join(names[j] for j in active)
        + "; and last, look at the whole sheet as one picture and fix what does not hold.",
    ]
    if "staging" in active:
        lines += [
            "",
            "[Job: stage the moment]",
            "As read, the sheet says how the people look and what the mood is, and the "
            "picture it makes is people standing and posing — nothing happens in it. Turn "
            "it into a moment where something is happening.",
            "",
            *scene_sheet.stage_rules(staging, locked=locked, exclude=_staging_exclude(active),
                                     avoid=avoid, subject=subject),
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
    lines += [
        "",
        "[Last job: one picture]",
        "Each job above changes only its own boxes. Read the whole sheet as it stands after "
        "them, against the reference, and patch what does not hold together.",
        *scene_sheet.rebuild_rules(staging=staging, reference_count=reference_count,
                                   same_call=True),
        "A box the rebuild leaves out stays as the parts above left it. If it all holds "
        "together, the rebuild is {\"set\": {}}.",
    ]
    if script_model:
        spec = scene_sheet._SCRIPT_MODELS.get(script_model, scene_sheet._SCRIPT_MODELS["krea2"])
        lines += [
            "",
            f"[Then: write the prompt for the {spec['name']} image model]",
            "Write it from the sheet as it stands once every part of your JSON — the rebuild "
            "included — is applied, not from the sheet as given below.",
            *scene_sheet.script_rules(sheet, paragraphs=paragraphs, model=script_model),
        ]
    lines += ["", "Output only one JSON object, with one part per job:", "{"]
    if "staging" in active:
        lines += ['  "staging":', *scene_sheet.stage_answer_shape(staging, indent="  ",
                                                                  given=bool(avoid))]
    if "objects" in active:
        lines += ['  "objects":', *props_pass.props_answer_shape(props_mode, indent="  ")]
    if "place" in active:
        lines.append('  "place": [{"tag": "<the tag as given>", "box": "<box>", "person": <index or null>}],')
    lines.append('  "rebuild": ' + scene_sheet.REBUILD_ANSWER_SHAPE)
    lines.append("}")
    if script_model:
        lines += [f"then a line with only {SCRIPT_MARKER} on it, then the prompt as plain "
                  "paragraphs — no JSON, no headings."]
    if instruction:
        lines += ["", "[User instruction — the sheet already follows it; keep it]", instruction]
    if earlier:
        lines += ["", "[What earlier steps already changed]",
                  json.dumps(earlier, ensure_ascii=False, indent=2)]
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
       "place": {"placed", "unclassified"},
       "rebuild": {"changed", "refused", "outside"}}``

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
    # Last, the whole read back as one picture, through the last look's door.
    sheet, changed, refused, outside = scene_sheet.apply_reconstruction(
        sheet, _part(answer, "rebuild"), locked)
    report["rebuild"] = {"changed": changed, "refused": refused, "outside": outside}
    return sheet, report

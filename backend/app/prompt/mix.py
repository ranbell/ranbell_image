"""Mixing references by their features, in proportion to the weights.

The Showrunner (2026-11-05): with WD14 tags the references mixed properly,
because tags could be forced in by proportion; in sentences they cannot, so
Krea 2 regressed. "It is not the sentences but the main features that have to
be mixed. The WD14 tags are there and the weights are kept — not using them
makes no sense."

What the detailed style did with two references: each was read into its own
sheet, and ``scene_sheet.merge_sheets`` took the people, the place and the
light from the heavier one. The lighter reference reached the picture only
through its loose objects. The weights barely mattered — 55/45 and 80/20 gave
the same picture.

Meanwhile ``prompt.tag_merge._build_weighted_wd14_context`` had already done
the mixing the WD14 styles used: a budget of distinctive tags per reference in
proportion to its weight, duplicates kept once, and of two contradicting tags
(two hair colours) the lighter reference's dropped. The detailed style
computed that and used it only as reading hints.

So the mix is that result, used as the WD14 styles used it — forced in:

1. **The recipe (code).** The weighted analysis, cleaned the way hints are
   cleaned (quality, rating and person-count tags, and tags under the
   confidence floor, are left out), each feature labelled with where it came
   from and that reference's share. The count per reference is the weight.
   Code does not decide which box a feature goes in: the tag catalog's axes
   mix bodies, objects and camera words under one name ("always_fixed" holds
   blue eyes, a book and a cowboy shot), so the axis is given only as a hint.
2. **The mix (the model).** The first part of the shaping call: write every
   feature into the sheet where it belongs, in the sheet's words, replacing
   what the sheet says about the same thing. The other references' own
   sheets are given as material — how those features look, in words.
3. **The check (code).** Every recipe feature must be on the sheet after the
   call. One that is not goes into "unclassified", which the writer has to
   write in; and every feature is required of the script.
"""
from __future__ import annotations

import json

from . import scene_sheet

#: The tag catalog's axis → a word for the prompt. A hint for the model, not a
#: box: see the module docstring.
_GROUP = {
    "hair": "hair",
    "clothing": "clothing",
    "parts": "body",
    "emotion": "expression",
    "action": "pose",
    "location": "place",
    "time_weather": "light and weather",
    "visual": "light and colour",
    "style": "how it is drawn",
}


def _group(tag: str) -> str:
    from ..tags.catalog import get_tag_axis

    key = str(tag).strip().lower().replace(" ", "_")
    if key.endswith("_eyes"):
        return "eyes"
    return _GROUP.get(get_tag_axis(key) or "", "other")


def _is_pose(tag: str) -> bool:
    """A pose or expression word — the staging's to decide when it moves the
    people, so not forced in from a reference (it would bring the old pose
    back)."""
    key = str(tag).strip().lower().replace(" ", "_")
    return (_group(key) in ("pose", "expression")
            or any(w in key for w in scene_sheet._POSE_WORDS))


def build_recipe(
    wd14_analysis: dict, *, labels: dict[str, str], shares: dict[str, float],
    skip: set[str] | frozenset = frozenset(), scores: dict[str, float] | None = None,
    min_score: float = 0.0, staging: str = "read", props_mode: str = "keep",
    dropped: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """The features the weights put into the picture.

    ``labels`` / ``shares``: per original image index (as a string, the way
    the analysis keys it) the panel label and the weight. A reference at 0%
    brings nothing. Each feature is ``{"word", "group", "source", "share"}``;
    ``share`` is None for a feature every reference has.
    """
    score_of = {str(k).strip().lower().replace(" ", "_"): float(v)
                for k, v in (scores or {}).items()}
    sink: list[tuple[str, str]] = dropped if dropped is not None else []
    out: list[dict] = []
    seen: set[str] = set()

    def _take(tags, source: str, share: float | None) -> None:
        for word in scene_sheet._clean_hint_words(tags, skip=skip, score_of=score_of,
                                                  min_score=min_score, dropped=sink):
            if word in seen:
                continue
            if staging != "read" and _is_pose(word):
                sink.append((word, "pose — the staging decides"))
                continue
            group = _group(word)
            if props_mode == "simplify" and group == "other":
                # The user asked for fewer objects; a weighted object is not forced in.
                sink.append((word, "object — the objects are simplified"))
                continue
            seen.add(word)
            out.append({"word": word, "group": group, "source": source,
                        "share": None if share is None else round(share * 100)})

    by_image = sorted((wd14_analysis.get("unique_by_image") or {}).items(),
                      key=lambda kv: -float(shares.get(kv[0], 0.0)))
    for key, info in by_image:
        share = float(shares.get(key, 0.0))
        if share <= 0:
            continue
        _take(list(info.get("must") or []) + list(info.get("ref") or []),
              labels.get(key, f"image {int(key) + 1}"), share)
    _take(wd14_analysis.get("common_tags") or [], "every image", None)
    return out


def _feature_line(f: dict) -> str:
    where = f["source"] if f["share"] is None else f"{f['source']}, {f['share']}%"
    return f"{f['word']} ({where})"


def mix_rules(
    recipe: list[dict], *, base: str, base_share: int, others: list[tuple[str, int, dict]],
    combine: str = "merge",
) -> list[str]:
    """The mixing job, as lines for the shaping call.

    ``base``: the reference the sheet was built from (the people and the place
    were taken from it); ``others``: (label, share %, sheet) for the rest.
    """
    groups: dict[str, list[str]] = {}
    for f in recipe:
        groups.setdefault(f["group"], []).append(_feature_line(f))
    people = (
        "Each person keeps their own looks: these people come from different pictures. "
        "Mix the place, the light and the things around them. And put the people in one "
        "space: each came from a picture where they were alone, so say in \"position\" "
        "where each stands — in the sheet's order, the first the most prominent, the last "
        "the least — and in \"interaction\" what this cast is doing to or with each other; "
        "they have never been in the same picture before."
        if combine == "coexist" else
        "The people are the base's people. They take the features listed for the hair, "
        "eyes, body and clothing even when those come from another reference — that is "
        "the mix: one person who carries both pictures' features, not two people."
    )
    lines = [
        f"The sheet was built from {base} ({base_share}%): its people, place and light "
        "came from that one picture, so the other reference"
        + ("s are" if len(others) > 1 else " is")
        + " missing from it. The weights decided which features of each reference "
        "belong in this picture — more from a heavier one, fewer from a lighter one — "
        "and they are listed below. These are the picture's features.",
        "",
        "- Write every feature into the sheet, in the box where it belongs, in the sheet's "
        "own plain words. None is left out and none is softened or swapped for a milder word.",
        "- A feature replaces what the sheet says about the same thing: one hair colour, "
        "one place, one light. Where the sheet and a feature disagree, the feature wins — "
        "the weights already settled it.",
        # Not a contradiction a tag can see ("school uniform" and "yukata" share
        # no word), so it is the model's: the old reconcile call's rule, kept.
        "- Two features of the same kind from different references — two outfits, two "
        "places, two lights — become ONE that carries both: the garment of one in the "
        "colour, pattern or material of the other; one place with the other's sky or "
        "weather. Both worn on top of each other, or two places side by side, is not a mix.",
        f"- {people}",
        "- The result is one picture someone could draw: the features belong to it, not "
        "pasted beside it. Use the other reference's sheet to see how a feature looks there, "
        "and carry its colour, material and character over.",
        "- \"who\" does not change; the medium does not change.",
        "",
        "[The features, by the weights]",
        *([f"- {g}: " + "; ".join(items) for g, items in groups.items()]
          or ["(the tagger gave no features to mix — mix by the other reference's sheet)"]),
    ]
    for label, share, sheet in others:
        brief = {k: sheet.get(k) for k in ("setting", "lighting", "palette_mood", "effects",
                                           "props", "rendering") if sheet.get(k)}
        brief["people"] = [
            {k: p.get(k) for k in ("who", "appearance", "outfit", "details") if p.get(k)}
            for p in sheet.get("subjects") or []
        ]
        lines += ["", f"[{label} ({share}%), as read]", json.dumps(brief, ensure_ascii=False, indent=2)]
    return lines


def mix_locks(sheet: dict, *, combine: str = "merge", locked: list[str] | None = None) -> list[str]:
    """What the mix may not touch: the medium and the visible text, and who
    each person is — plus, when people coexist, their looks."""
    out = list(locked or [])
    out += ["medium", "text_in_image"]
    person_held = ("who",) if combine != "coexist" else ("who", "appearance", "outfit", "details")
    for i in range(len(sheet.get("subjects") or [])):
        out += [f"subjects.{i}.{box}" for box in person_held]
    return out


def apply_mix(
    sheet: dict, patch: dict, *, combine: str = "merge", locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str], list[str]]:
    """The mix part → the sheet, through the staging door (``_apply_scoped``):
    nobody added or removed, the user's locks held."""
    return scene_sheet._apply_scoped(
        sheet, patch if isinstance(patch, dict) else {},
        mix_locks(sheet, combine=combine, locked=locked), locked)


def missing(recipe: list[dict], sheet: dict) -> list[str]:
    """The recipe's features the sheet does not hold."""
    words = [f["word"] for f in recipe]
    present = set(scene_sheet.still_on_sheet(words, sheet))
    return [w for w in words if w not in present]


def force_in(recipe: list[dict], sheet: dict) -> tuple[dict, list[str]]:
    """Every feature the sheet still lacks goes into "unclassified" — the box
    the writer has to write in. The WD14 styles injected a missing must-tag
    into the tag line; this is that, for a sheet."""
    lacking = missing(recipe, sheet)
    if not lacking:
        return sheet, []
    out = dict(sheet)
    have = list(out.get("unclassified") or [])
    out["unclassified"] = have + [w for w in lacking if w not in have]
    return out, lacking


def keep_lines(recipe: list[dict]) -> list[str]:
    """For the later parts: the mixed features are not theirs to drop."""
    if not recipe:
        return []
    return [
        "",
        "[Decided by the weights — every one of these stays in the picture]",
        "; ".join(f["word"] for f in recipe),
    ]

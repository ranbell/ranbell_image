"""The "detailed" Refine style: a Krea 2 visual script, written in two steps.

Krea 2 reads its prompt with Qwen3-VL and was trained mostly on long, dense,
natural-language captions; Krea's own expansion prompt asks for one faithful
paragraph with each subject grouped with its own attributes and actions, and
no tags, lists or markdown (krea-ai/krea-2 docs/expansion.txt).

What went wrong with the old 8-section style, on a five-girl idol stage
(2026-10-21):

- "2girls, solo, multiple_girls" for five people
- `drinking_straw, cup, closed_eyes, holding` in a tag line with no owner — the
  model hands such an object to whoever is nearest, and one girl ended up
  sipping through a straw
- tags standing in for verbs: "forcefully (microphone)"
- "anime_illustration" buried mid-list, so the medium was easy to lose; without
  it Krea 2 drifts to a photograph

So the work is split, one job per call:

1. **Sheet** — the vision model looks at the reference and fills a JSON sheet.
   Every object has a box to go in: a subject's hands, or `props` with a place.
   The medium has its own required box.
2. **Script** — a text call writes English paragraphs from the sheet only.

WD14 tags are not used: on these models they were the noise, not the signal.
The finished script is checked in code for what the model is most likely to
drop — the medium, the count, quoted text — and leftovers of tag syntax are
cleaned up.
"""
from __future__ import annotations

import json
import re

from .visual_spec import DEFAULT_PROSE_PARAGRAPHS, clamp_prose_paragraphs

# Fixed media the screen can choose. Empty means "read it from the reference".
MEDIUM_CHOICES = {
    "anime": "anime illustration",
    "photo": "photograph",
}

_NUMBER_WORDS = {
    2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten",
}

_SHEET_FIELDS = (
    "medium", "rendering", "story", "subject_count", "subjects", "situation", "interaction", "setting",
    "layers", "props", "effects", "lighting", "camera", "palette_mood", "text_in_image", "other",
    "unclassified",
)
# Keys a model sends that are about the sheet, not the picture: neither a box
# nor something to keep.
_META_KEYS = {"subject_count", "subjects", "index", "takes_from"}
# Action first (the Showrunner, 2026-11-01): a sheet of looks and mood made
# people standing and posing. What each person does, why, what they feel and
# look at, and what their hands, legs and the motion are doing each have a box
# — without a box, it is not written.
_SUBJECT_FIELDS = (
    "who", "position", "signature", "pose_action", "intent", "feeling", "expression", "gaze",
    "hands", "legs_feet", "motion", "holding", "appearance", "outfit", "details",
)
# The boxes that say what a person does, as opposed to how they look.
_ACTION_BOXES = ("pose_action", "intent", "feeling", "expression", "gaze", "hands",
                 "legs_feet", "motion", "holding")

# What the sheet reader is told to call each panel of the tile. Three places
# name the panels — the hints, the mix and the fusion pools — and a label that
# drifts between them points the reader at the wrong picture, so they all read
# it from here.
_ORDINALS = ("the first image", "the second image", "the third image",
             "the fourth image", "the fifth image", "the sixth image")


def panel_label(n: int, total: int) -> str:
    """Tile panel ``n`` (0-based, reading order) as the reader is told to call it."""
    return "the reference" if total <= 1 else _ORDINALS[min(n, len(_ORDINALS) - 1)]


def sheet_prompt(
    *,
    instruction: str = "",
    notes: list[str] | None = None,
    literal_texts: list[str] | None = None,
    medium: str = "",
    reference_count: int = 1,
    combine: str = "merge",
    hints: dict[str, list[str]] | None = None,
) -> str:
    """Step 1: what the vision model is asked to fill in.

    ``hints`` are the references' top WD14 tags (``hint_tags``), given as a
    recommendation of what is distinctive — never as the description itself.

    ``combine`` decides what happens to the people when several references
    are tiled together (the Showrunner, 2026-10-21): "merge" fuses them into
    one cast, "coexist" keeps every one of them, side by side.
    """
    medium_line = (
        f'The medium is fixed by the user: "{medium}". Write exactly that in "medium".'
        if medium else
        'Decide the medium from the reference: an anime or manga style picture is an '
        '"anime illustration", a camera picture is a "photograph"; name others plainly '
        '("3D render", "oil painting", "watercolor painting").'
    )
    refs = ""
    if reference_count > 1:
        refs = (
            "The reference is a tile of several images, in reading order (left to right, "
            "top to bottom). Describe ONE scene made from them — not a collage, not "
            "side-by-side panels.\n"
        )
        if combine == "coexist":
            refs += (
                "Keep every main person from every image: they all appear together in "
                "this one scene, each with their own looks and outfit, so the count is the "
                "sum of the images' people. In each \"who\", say which image they come from "
                "(first image, second image, ...). Place them so they share the space "
                "naturally.\n"
            )
        else:
            refs += (
                "Fuse the people: the user chose to merge the characters, so the main "
                "person of each image becomes ONE person who combines their most striking "
                "features (hair from one, outfit from another, and so on). If each image "
                "shows one person, \"subjects\" has exactly ONE entry — one entry per "
                "image is the wrong answer here, even when the people look different. Say "
                "in that entry's \"takes_from\" which features came from which image. "
                "The outfit is ONE outfit a person could actually wear: pick pieces that go "
                "together, or carry one image's colour or pattern into the other's garment. "
                "Both outfits worn on top of each other is not a merge. "
                "Setting, light and mood are blended the same way.\n"
            )
    lines = [
        "You are preparing a scene sheet that another writer will turn into a prompt for "
        "the Krea 2 image model. Look at the reference image and fill in the JSON below.",
        "",
        "Why the boxes matter:",
        "- The image model gives any object without an owner to whichever person is "
        "nearest. So every object goes either in one subject's \"holding\" (say which "
        "hand) or in \"props\" with where it is. Something you can see but cannot place "
        "goes in \"other\", with whatever you can tell of where it is — nothing seen is left out.",
        "- Each person has their own entry. Their expression, eyes, outfit and action go "
        "in that entry, not in a shared list, so one person's closed eyes do not spread "
        "to everyone.",
        "- A sheet that names only looks and mood makes a picture of people standing and "
        "posing, where nothing happens. So read what each person is doing, why, what they "
        "feel, what their eyes are on, what each hand and both legs are doing, and where "
        "movement shows — and what is happening in the scene. The action, the gaze, the "
        "hands, the legs and the motion are read as they are; the reason, the feeling and "
        "the situation are read from the posture, the face and the place — write the most "
        "likely one.",
        "- A pose is what most easily turns ordinary on the way to the picture: a writer who "
        "only knows \"sitting\" or \"leaning\" draws the stock version of it. So each person's "
        "\"signature\" says what makes THIS pose their own — what touches what, which side "
        "carries the weight, what is asymmetric, how the line of the body runs, how it sits "
        "against the frame. Only what you can see in this picture.",
        "- \"story\" is one sentence: who is doing what, and what this picture is about. "
        "Everything else on the sheet is part of that one moment.",
        "- \"subject_count\" must equal the number of entries in \"subjects\". Count the "
        "people you can actually see.",
        "- Without a stated medium the image model drifts to a photograph. "
        + medium_line,
        "- Write plain English phrases. No booru tags, no underscores, no parentheses.",
        "- Describe what is visible. If you are unsure of a detail, leave it out rather "
        "than guess. Look at each face separately: eyes open or closed, mouth open or "
        "closed. Look at each pair of hands: what is in them.",
        "- Be generous with concrete, visible detail: the writer can only describe what "
        "you put here, and a long, dense description is what the image model draws best "
        "from. A box with nothing to say stays an empty string.",
        "",
        refs.rstrip("\n"),
        "Fill in this JSON and output only the JSON:",
        "{",
        '  "medium": "anime illustration / photograph / ...",',
        '  "rendering": "how it is drawn or shot: line work, shading, brushwork, grain",',
        '  "story": "one sentence: who is doing what, and what this picture is about",',
        '  "subject_count": 0,',
        '  "subjects": [',
        '    {',
        '      "who": "who this is: gender, apparent age, and role in the scene",',
        '      "position": "where in the frame: center front, back left, ...",',
        '      "signature": "what makes this pose this person\'s own and not a stock pose: what '
        'touches what, where the weight goes, what is asymmetric, the line of the body",',
        '      "pose_action": "what they are doing right now, as a verb phrase caught in the '
        'middle of the movement",',
        '      "intent": "why they are doing it: what they want, or what made them do it",',
        '      "feeling": "what they feel inside",',
        '      "expression": "the face: eyes open or closed, mouth, brows — how the feeling shows",',
        '      "gaze": "what or whom they are looking at, and how",',
        '      "hands": "what the left hand and the right hand are each doing",',
        '      "legs_feet": "what the legs and feet are doing, and where the weight is",',
        '      "motion": "where movement shows: hair, cloth, the line of the body, the '
        'direction of the momentum",',
        '      "holding": "objects in this person\'s hands and which hand"'
        + ',\n      "appearance": "hair colour, length and style; eye colour; face; build",'
        + '\n      "outfit": "clothing and accessories, with materials and colours",'
        + '\n      "details": "small visible things on this person: hair ornaments, jewelry, '
        'straps, stitching, fur, fabric texture, how light catches them"'
        + (',\n      "takes_from": "which features come from which image"'
           if reference_count > 1 and combine != "coexist" else ""),
        '    }',
        '  ],',
        '  "situation": "what is happening in this scene: what has just happened and what is about to",',
        '  "interaction": "what the people are doing to or with each other (empty for one person)",',
        '  "setting": "where the scene is, described concretely",',
        '  "layers": "what is in the foreground, the middle ground and the background",',
        '  "props": "objects nobody holds, each with its place — never one already in someone\'s hands",',
        '  "effects": "particles, weather, light rays, reflections, motion in the air",',
        '  "lighting": "light sources, direction, colour, highlights and shadows",',
        '  "camera": "shot size, angle, framing, depth of field",',
        '  "palette_mood": "dominant colours and the feeling of the picture",',
        '  "text_in_image": ["exact words visible in the image, if any"],',
        '  "other": "anything visible that no box above holds, with where it is"',
        "}",
    ]
    if literal_texts:
        quoted = ", ".join(f'"{t}"' for t in literal_texts)
        lines += ["", f"The user wants this text visible in the image: {quoted}. "
                  "Put it in \"text_in_image\" exactly as written."]
    if hints:
        lines += [
            "",
            "[Tagger's reading of the reference — its most confident tags]",
            *[f"- {source}: {', '.join(words)}" for source, words in hints.items()],
            "A sheet written from a first look comes out plain: it names each garment but "
            "not its trim, pattern or colour, and the picture's own character is lost. These "
            "tags are what makes this picture itself, so look for each one. One you can see goes in "
            "its box, in plain English words: a garment or its trim in that person's outfit "
            "or details, a hair or eye trait in appearance, a place or weather in setting, "
            "light in lighting, particles in effects. Every tag listed here goes into the "
            "sheet: into its box, or into \"other\" when no box fits. None is dropped and "
            "none is swapped for a milder or vaguer word.",
        ]
    if instruction:
        lines += ["", "[User instruction — it overrides the reference where they differ]",
                  instruction]
    if notes:
        lines += ["", "[Notes]"] + [f"- {n}" for n in notes]
    return "\n".join(line for line in lines if line is not None)


def _clean_hint_words(
    tags, *, skip, score_of: dict[str, float], min_score: float,
    dropped: list[tuple[str, str]],
) -> list[str]:
    """Tags → plain words, minus the three things that are left out, each of
    which is appended to ``dropped`` with its reason (the Showrunner,
    2026-10-31). Shared by ``hint_tags`` and ``hints_for_image``."""
    out: list[str] = []
    for tag in tags or []:
        key = str(tag).strip().lower().replace(" ", "_")
        if not key:
            continue
        word = key.replace("_", " ")
        score = score_of.get(key)
        reason = ("quality" if is_quality_tag(key) else
                  "rating" if key in _RATING_TAGS else
                  "person count" if key in skip else
                  f"confidence {score:.2f}" if score is not None and score < min_score else "")
        if reason:
            if all(w != word for w, _ in dropped):
                dropped.append((word, reason))
            continue
        if word not in out:
            out.append(word)
    return out


def hints_for_image(
    wd14_analysis: dict, idx: int, *, skip: set[str] | frozenset = frozenset(),
    scores: dict[str, float] | None = None, min_score: float = 0.0,
    dropped: list[tuple[str, str]] | None = None,
) -> dict[str, list[str]]:
    """Hints for a single-image read: what the tagger saw in THIS image.

    Reading one picture at a time means a hint can be aimed. ``hint_tags``
    hands the reader every reference's words at once, which is all it can do
    when the reader is looking at a tile; here image ``idx`` is handed its own
    distinctive tags first, then the tags every reference shares (which are in
    this picture too, being an intersection).
    """
    score_of = {str(k).strip().lower().replace(" ", "_"): float(v)
                for k, v in (scores or {}).items()}
    sink: list[tuple[str, str]] = dropped if dropped is not None else []
    info = (wd14_analysis.get("unique_by_image") or {}).get(str(idx)) or {}
    words = _clean_hint_words(
        list(info.get("must") or []) + list(info.get("ref") or []),
        skip=skip, score_of=score_of, min_score=min_score, dropped=sink,
    )
    for word in _clean_hint_words(wd14_analysis.get("common_tags"), skip=skip,
                                  score_of=score_of, min_score=min_score, dropped=sink):
        if word not in words:
            words.append(word)
    return {"the reference": words} if words else {}


def hint_tags(
    wd14_analysis: dict, *, skip: set[str] | frozenset = frozenset(),
    scores: dict[str, float] | None = None, min_score: float = 0.0,
    shares: list[float] | None = None,
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """The references' top WD14 tags, as plain words, for ``sheet_prompt``,
    and the ones left out (word, reason).

    Taken from the Refine WD14 analysis, so the WD14 settings decide how many:
    the shared tags, then each image's distinctive ones (confident first).

    Only three things are left out (the Showrunner, 2026-10-31): quality
    tags, ``skip`` (the person counts — the sheet's own list decides the
    count) and tags under ``min_score``, the Showrunner's own floor. A tag
    with no stored score is kept: there is nothing to judge it by. Every tag
    left out is returned with its reason, to be said on screen.

    ``shares`` is each image's weight INDEXED BY ITS ORIGINAL POSITION — the
    keying ``unique_by_image`` itself uses, so the lookup cannot drift. Given
    it, every label carries its percentage and the heaviest image is listed
    first, so the reader meets the lead reference's words before the rest.
    Without it the labels and the order are what they always were.
    """
    dropped: list[tuple[str, str]] = []
    score_of = {str(k).strip().lower().replace(" ", "_"): float(v) for k, v in (scores or {}).items()}

    def _clean(tags) -> list[str]:
        return _clean_hint_words(tags, skip=skip, score_of=score_of,
                                 min_score=min_score, dropped=dropped)

    hints: dict[str, list[str]] = {}
    shared = _clean(wd14_analysis.get("common_tags"))
    if shared:
        hints["in every image"] = shared
    images = sorted((wd14_analysis.get("unique_by_image") or {}).items(), key=lambda kv: int(kv[0]))
    total_share = sum(max(0.0, float(s or 0.0)) for s in shares) if shares else 0.0

    found: list[tuple[float, str, list[str]]] = []
    for n, (key, info) in enumerate(images):
        words = _clean(list(info.get("must") or []) + list(info.get("ref") or []))
        if not words:
            continue
        label = panel_label(n, len(images))
        share = -1.0
        if shares and total_share > 0:
            try:
                raw = max(0.0, float(shares[int(key)]))
            except (ValueError, IndexError, TypeError):
                raw = 0.0
            share = raw / total_share
            label = f"{label} ({round(share * 100)}% of the mix)"
        found.append((share, label, words))

    # Heaviest first when the shares are known; the shared tags stay on top.
    if shares and total_share > 0:
        found.sort(key=lambda f: -f[0])
    for _, label, words in found:
        hints[label] = words
    return hints, dropped


# ── The mix: the weight sliders as an editorial decision ─────────────────────
#
# The per-image weight slider used to reach this style through one door only:
# `hint_tags` hands over more words for a heavier image, because the Refine
# analysis sizes each image's tag budget by weight. The reader was told which
# image a word came from and nothing about which image the picture belongs to,
# so it averaged them — the Showrunner (2026-11-01): "the mixing ratio the
# slider sets does not mean very much".
#
# A sheet has named boxes, so the weights do not need a word budget: they can
# say WHO OWNS WHICH BOX. `identity_panel` and `palette_panel` are the whole
# fix — one named panel decides the person, a different named panel decides the
# place and the light. The script step transcribes the sheet faithfully ("You
# are transcribing the sheet, not judging it"), so a weighting that lands in
# the sheet survives into the prompt with no arithmetic and no post-processing.
#
# `build_mix_plan` returns the assignment and `merge_sheets` carries it out,
# so nothing has to be phrased in English for a reader to honour.

#: At or below this share a panel is an accent: one named thing, nothing more.
MIX_ACCENT_MAX_SHARE = 0.20

def build_mix_plan(
    shares: list[float], *, roles: list[str] | None = None, combine: str = "merge",
) -> dict:
    """Per-panel share, band and box ownership. Arithmetic only, no English.

    ``shares`` is each TILE PANEL's weight in reading order — not the original
    selection order — so the labels match the picture the reader is looking at.
    Values are renormalized here, so raw slider numbers are fine.

    A panel at 0 is ``ignored``: it is still in the tile (the tile is built
    before the weights are consulted), so the reader has to be told in words to
    leave it alone, or it reads it at full strength.
    """
    vals = [max(0.0, float(s or 0.0)) for s in shares or []]
    total = len(vals)
    if not total:
        return {"combine": combine, "panels": [], "identity_panel": None,
                "palette_panel": None, "active": 0, "total": 0}

    sum_v = sum(vals)
    norm = [v / sum_v for v in vals] if sum_v > 0 else [1.0 / total] * total

    def _role(n: int) -> str:
        if roles and n < len(roles):
            return roles[n] or "both"
        return "both"

    top = max(norm)
    panels: list[dict] = []
    for n, share in enumerate(norm):
        panels.append({
            "n": n,
            "label": panel_label(n, total),
            "share": share,
            "pct": round(share * 100),
            "role": _role(n),
            "band": "ignored" if share <= 0 else
                    "accent" if share <= MIX_ACCENT_MAX_SHARE else "secondary",
        })

    # Exactly one lead. Two panels tied at the top is no decision at all —
    # there would be nowhere to take the face from.
    for p in panels:
        if p["share"] > 0 and p["share"] >= top:
            p["band"] = "lead"
            break

    active = [p for p in panels if p["share"] > 0]
    ranked = sorted(active, key=lambda p: (-p["share"], p["n"]))
    lead = next((p for p in panels if p["band"] == "lead"), None)

    # The role button finally means something here, and it means an assignment,
    # not just a filter: a panel marked "style only" exists precisely to supply
    # the place and the light, so it OWNS them even when it is not the heaviest.
    # Only when nobody is marked does the lead own both — without a role there
    # is nothing to say whether the second panel is a scene or another person,
    # and handing it the setting on a guess is how references get averaged.
    def _pick(pred) -> dict | None:
        return next((p for p in ranked if pred(p)), None)

    identity = (_pick(lambda p: p["role"] == "content")
                or _pick(lambda p: p["role"] != "style")
                or lead)
    palette = (_pick(lambda p: p["role"] == "style")
               or _pick(lambda p: p["role"] != "content")
               or identity)

    return {
        "combine": combine,
        "panels": panels,
        "identity_panel": identity["n"] if identity else None,
        "palette_panel": palette["n"] if palette else None,
        "active": len(active),
        "total": total,
    }


def mix_summary(plan: dict) -> str:
    """One line for the screen, so the Showrunner can see the dial landed."""
    bits = []
    for p in plan.get("panels") or []:
        role = "" if p["role"] == "both" else f" {p['role']}"
        bits.append(f"{p['label']} {p['pct']}% {p['band']}{role}")
    return " / ".join(bits)


# What models write into a box they had nothing for. Kept, it reaches the
# script as "holds nothing" / "the setting is empty of props".
_EMPTY_WORDS = {"empty", "none", "nothing", "n/a", "na", "null", "-", "no", "unknown"}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if _text(v))
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_text(v)}" for k, v in value.items() if _text(v))
    text = str(value).strip()
    # Also "None (all props are held)" — an empty box with an explanation.
    head = re.split(r"[\s(.,;:—-]", text.lower(), maxsplit=1)[0]
    return "" if text.lower().strip(" .") in _EMPTY_WORDS or head in _EMPTY_WORDS else text


def normalize_sheet(
    data: dict, *, medium: str = "", literal_texts: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Keep the known boxes, as plain strings, and make the count and the
    medium agree with what the screen and the subject list say.

    What a model writes under a box the sheet does not have is not thrown
    away (the Showrunner, 2026-10-31): it goes into "other", and ``notes``
    says so.
    """
    sheet: dict = {k: "" for k in _SHEET_FIELDS}
    for key in ("medium", "rendering", "story", "situation", "interaction", "setting", "layers",
                "props", "effects", "lighting", "camera", "palette_mood", "other"):
        sheet[key] = _text(data.get(key))
    moved: list[str] = []
    for key, value in data.items():
        if key in _SHEET_FIELDS or key in _META_KEYS:
            continue
        text = _text(value)
        if text:
            moved.append(f"{str(key).replace('_', ' ')}: {text}")
            if notes is not None:
                notes.append(f'box "{key}" is not on the sheet — moved to other')
    subjects = []
    for entry in data.get("subjects") or []:
        if not isinstance(entry, dict):
            continue
        subject = {k: _text(entry.get(k)) for k in _SUBJECT_FIELDS}
        for key, value in entry.items():
            if key in _SUBJECT_FIELDS or key in _META_KEYS:
                continue
            text = _text(value)
            if text:
                moved.append(f"{subject.get('who') or 'a person'} — {str(key).replace('_', ' ')}: {text}")
                if notes is not None:
                    notes.append(f'person box "{key}" is not on the sheet — moved to other')
        if any(subject.values()):
            subjects.append(subject)
    if moved:
        sheet["other"] = "; ".join([sheet["other"]] + moved if sheet["other"] else moved)
    sheet["unclassified"] = [t for t in (_text(x) for x in (data.get("unclassified") or [])) if t]
    sheet["subjects"] = subjects
    # The list is what gets written out, so it decides the count.
    sheet["subject_count"] = len(subjects)
    texts = [t for t in (_text(x) for x in (data.get("text_in_image") or [])) if t]
    for t in literal_texts or []:
        if t not in texts:
            texts.append(t)
    sheet["text_in_image"] = texts
    if medium:
        sheet["medium"] = medium
    sheet["medium"] = clean_script(sheet["medium"]) or "illustration"
    return sheet


# ── Merging one sheet per reference ──────────────────────────────────────────
#
# Reading six references meant tiling them into one 1008px picture — 336x504 a
# panel — and asking one call to perceive six pictures, do the merge and fill a
# fifteen-box sheet at the same time. The sheet prompt says out loud what that
# cost: "A sheet written from a first look comes out plain: it names each
# garment but not its trim, pattern or colour." The WD14 hints exist to patch
# over pixels the tile threw away.
#
# So each reference is read on its own, at its own size, and the sheets are
# merged here. The weights stop being something a vision model is asked to
# honour while staring at a collage and become an assignment carried out in
# code: `appearance` is taken from the identity sheet, `setting` from the
# palette sheet. `build_mix_plan` already decided who owns what; this spends
# that decision.

#: Taken verbatim from the identity sheet — the face this picture wears.
MERGE_IDENTITY_BOXES = ("who", "appearance")
#: Taken verbatim from the palette sheet — the place this picture happens in.
MERGE_SCENE_BOXES = ("setting", "lighting", "palette_mood", "rendering")
#: Every sheet's value is kept, heaviest first: these are lists of things, and
#: dropping the lighter reference's props would lose what it was selected for.
_MERGE_ADDITIVE_TEXT = ("props", "other")
_MERGE_ADDITIVE_LIST = ("text_in_image", "unclassified")
#: The heaviest sheet that has anything to say wins.
_MERGE_WINNER_TAKES = ("story", "situation", "interaction", "layers", "effects", "camera")


def _merge_ranked(sheets: list[tuple[int, dict]], plan: dict) -> list[dict]:
    """The contributing panels, heaviest first, as `build_mix_plan` rows."""
    by_panel = {n for n, _ in sheets}
    panels = [p for p in (plan.get("panels") or [])
              if p["n"] in by_panel and p["share"] > 0]
    return sorted(panels, key=lambda p: (-p["share"], p["n"]))


def merge_sheets(
    sheets: list[tuple[int, dict]], plan: dict,
) -> tuple[dict, list[str]]:
    """``(panel index, normalized sheet)`` × N → one sheet, plus where each box
    came from.

    The provenance lines are not decoration: printed on screen they are how the
    Showrunner sees that the dial landed, box by box.
    """
    by_panel = {n: s for n, s in sheets}
    ranked = _merge_ranked(sheets, plan)
    if not ranked:
        return {k: "" for k in _SHEET_FIELDS}, []
    if len(ranked) == 1:
        return by_panel[ranked[0]["n"]], []

    combine = plan.get("combine") or "merge"
    label = {p["n"]: p["label"] for p in ranked}
    lead_n = next((p["n"] for p in ranked if p["band"] == "lead"), ranked[0]["n"])
    id_n = plan.get("identity_panel")
    pal_n = plan.get("palette_panel")
    if id_n not in by_panel:
        id_n = lead_n
    if pal_n not in by_panel:
        pal_n = id_n

    out: dict = {k: "" for k in _SHEET_FIELDS}
    prov: list[str] = []

    out["medium"] = by_panel[lead_n].get("medium") or ""
    prov.append(f"medium ← {label[lead_n]}")

    for box in MERGE_SCENE_BOXES:
        out[box] = by_panel[pal_n].get(box) or ""
    prov.append(f"{', '.join(MERGE_SCENE_BOXES)} ← {label[pal_n]}")

    for box in _MERGE_WINNER_TAKES:
        for p in ranked:
            value = by_panel[p["n"]].get(box) or ""
            if value:
                out[box] = value
                prov.append(f"{box} ← {label[p['n']]}")
                break

    for box in _MERGE_ADDITIVE_TEXT:
        parts: list[str] = []
        seen: set[str] = set()
        froms: list[str] = []
        for p in ranked:
            value = (by_panel[p["n"]].get(box) or "").strip()
            if value and value.lower() not in seen:
                parts.append(value)
                seen.add(value.lower())
                froms.append(label[p["n"]])
        if parts:
            out[box] = "; ".join(parts)
            prov.append(f"{box} ← {' + '.join(froms)}")

    for box in _MERGE_ADDITIVE_LIST:
        acc: list[str] = []
        seen = set()
        for p in ranked:
            for item in by_panel[p["n"]].get(box) or []:
                text = _text(item)
                if text and text.lower() not in seen:
                    acc.append(text)
                    seen.add(text.lower())
        out[box] = acc

    if combine == "coexist":
        subjects: list[dict] = []
        froms = []
        for p in ranked:
            people = by_panel[p["n"]].get("subjects") or []
            for person in people:
                subjects.append(dict(person))
            if people:
                froms.append(f"{len(people)} from {label[p['n']]}")
        if froms:
            prov.append("subjects ← " + ", ".join(froms))
    else:
        subjects = [dict(s) for s in by_panel[id_n].get("subjects") or []]
        prov.append(
            f"subjects ({', '.join(MERGE_IDENTITY_BOXES)} and the rest) ← {label[id_n]}"
        )
    out["subjects"] = subjects
    # The list is what gets written out, so it decides the count — the same
    # rule normalize_sheet uses.
    out["subject_count"] = len(subjects)
    return out, prov


def merge_locks(
    merged: dict, plan: dict, *, locked: list[str] | None = None,
) -> list[str]:
    """Paths the reconcile step may not touch.

    This is what makes the dial unbreakable. Before, the mix was a paragraph of
    English a model could quietly average away; here the boxes the weights
    assigned are locked, so the only thing left to negotiate is what the
    weights did not decide.
    """
    out = list(locked or [])
    out += [b for b in MERGE_SCENE_BOXES if b not in out]
    n_people = len(merged.get("subjects") or [])
    combine = plan.get("combine") or "merge"
    # In a merge one person carries the identity; in coexist everyone keeps
    # their own, because nobody was merged away.
    people = range(n_people) if combine == "coexist" else range(min(1, n_people))
    for i in people:
        for box in MERGE_IDENTITY_BOXES:
            out.append(f"subjects.{i}.{box}")
    return out


def reconcile_prompt(
    merged: dict, plan: dict, *, locked: list[str] | None = None,
) -> str:
    """What the text model is asked after the sheets are merged, as a patch.

    The merge is exact where the weights decided, and silent everywhere they
    did not: a merged person can be wearing two worlds' clothes, two sheets'
    props can be the same object twice, and nothing has said what the combined
    cast is doing to each other. That is this call's whole job.
    """
    combine = plan.get("combine") or "merge"
    locked = list(locked or [])
    indexed = dict(merged)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(merged.get("subjects") or [])]

    lines = [
        "Below is a scene sheet assembled from several reference images, one box "
        "at a time. Make it read as ONE coherent picture and return the result "
        "as a patch on the sheet.",
        "",
        "What was already decided and is not yours to change: who the people are "
        "and how they look, and the place, the light and the palette. Those boxes "
        "came from the references the user weighted for them, and they are locked.",
        "",
        "What is left for you:",
    ]
    if combine == "coexist":
        lines += [
            '- "position": the people came from different pictures and each one '
            "thinks it is alone in the frame. Place them in one space, in the "
            "order the sheet lists them — the first is the most prominent, the "
            "last the least — and say where each one stands.",
            '- "interaction": say what this cast is doing to or with each other. '
            "They have never been in the same picture before.",
        ]
    else:
        lines += [
            '- "outfit" and "details": the person may be wearing pieces from two '
            "different worlds. Make it ONE outfit a person could actually wear — "
            "pick the pieces that go together, or carry one world's colour, "
            "pattern or material into the other's garment. Both outfits worn on "
            "top of each other is not an answer.",
            '- "interaction": empty it if there is only one person.',
        ]
    lines += [
        '- "props": the same object may be listed twice in different words, and a '
        "prop may already be in somebody's hands. One object, named once, in one "
        "place.",
        '- "situation": say what is happening, now that the people and the place '
        "are settled.",
        "- Write every new value as a plain English phrase, concrete and visible, "
        "in the sheet's own style. Never soften or blur a value into a vaguer word.",
        "- Change nothing you were not asked to. A box that already reads well is "
        "left out of the patch.",
    ]
    if locked:
        lines.append("- Also locked by the user: " + ", ".join(locked)
                     + " (\"subjects.N\" is the whole person N).")
    lines += [
        "",
        "Output only JSON in this shape, leaving out any part you do not need:",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}]',
        "}",
        "",
        "[Scene sheet]",
        json.dumps(indexed, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


# ── Saying it again: a Japanese line edits the sheet, not the prose ─────────
#
# The Showrunner picks ① the sheet editor and ⑨ a spoken retake as the first
# step of the creativity plan (2026-10-21). A retake used to mean writing the
# whole prompt again, so things nobody mentioned moved too. Here the line
# becomes a patch on the sheet — only the boxes it names change — and the
# script is written again from the patched sheet.

_SCENE_BOXES = ("medium", "rendering", "story", "situation", "interaction", "setting", "layers",
                "props", "effects", "other", "lighting", "camera", "palette_mood")


def patch_prompt(sheet: dict, instruction: str, locked: list[str] | None = None) -> str:
    """What the text model is asked: turn the line into a patch."""
    locked = list(locked or [])
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    lines = [
        "Below is the scene sheet for a picture, and a request to change it. "
        "Turn the request into a patch on the sheet.",
        "",
        "Why only a patch: every box that changes changes the picture. A box the "
        "request does not ask about must stay exactly as it is, or something nobody "
        "asked for moves.",
        "- Change only what the request asks for, and whatever must change with it "
        "for the scene to stay consistent (night outdoors also changes the lighting).",
        "- Write new values as plainly as the request says them — never softened or "
        "blurred into a vaguer word — and the way the sheet is written: plain English phrases, "
        "concrete and visible.",
        "- People are addressed by their \"index\". To add a person, give a full entry "
        "in \"add_subjects\"; to remove one, put the index in \"remove_subjects\".",
    ]
    if locked:
        lines.append(
            "- These are locked by the user and must not appear in the patch: "
            + ", ".join(locked) + " (\"subjects.N\" is the whole person N)."
        )
    lines += [
        "",
        "Output only JSON in this shape, leaving out any part you do not need:",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}],',
        '  "add_subjects": [{' + ", ".join(f'"{f}": ""' for f in _SUBJECT_FIELDS) + '}],',
        '  "remove_subjects": [0]',
        "}",
        "",
        "[Request]",
        instruction,
        "",
        "[Scene sheet]",
        json.dumps(indexed, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def _short(text: str, n: int = 60) -> str:
    text = text or "—"
    return text if len(text) <= n else text[: n - 1] + "…"


def apply_patch(
    sheet: dict, patch: dict, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str]]:
    """Apply a patch. Returns the new sheet, what changed (readable lines),
    and what was refused because it was locked.

    Indexes in the patch refer to the sheet as it was, so person edits go
    first, then removals, then additions. A value for a box the sheet does not
    have is not ignored: it is added to "other" (the Showrunner, 2026-10-31).
    """
    locked_set = set(locked or [])
    changed: list[str] = []
    refused: list[str] = []
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in sheet.items()}
    subjects = [dict(s) for s in sheet.get("subjects") or []]

    def _to_other(line: str, path: str) -> None:
        if "other" in locked_set:
            refused.append(path)
            return
        out["other"] = f"{out['other']}; {line}" if out.get("other") else line
        changed.append(f"other += {_short(line)}")

    for key, value in ((patch.get("set") or {}) if isinstance(patch.get("set"), dict) else {}).items():
        if key not in _SCENE_BOXES and key != "text_in_image":
            text = _text(value)
            if text and key not in _META_KEYS and key != "unclassified":
                _to_other(f"{str(key).replace('_', ' ')}: {text}", str(key))
            continue
        if key in locked_set:
            refused.append(key)
            continue
        if key == "text_in_image":
            new = [t for t in (_text(x) for x in (value if isinstance(value, list) else [value])) if t]
            if new != list(out.get("text_in_image") or []):
                changed.append(f"text_in_image: {out.get('text_in_image')} → {new}")
                out["text_in_image"] = new
            continue
        new = _text(value)
        if new != (out.get(key) or ""):
            changed.append(f"{key}: {_short(out.get(key))} → {_short(new)}")
            out[key] = new

    for entry in patch.get("subjects") or []:
        if not isinstance(entry, dict):
            continue
        try:
            i = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(subjects):
            continue
        for key, value in (entry.get("set") or {}).items():
            if key not in _SUBJECT_FIELDS:
                text = _text(value)
                if text and key not in _META_KEYS:
                    if f"subjects.{i}" in locked_set:
                        refused.append(f"subjects.{i}.{key}")
                    else:
                        who = subjects[i].get("who") or f"person {i + 1}"
                        _to_other(f"{who} — {str(key).replace('_', ' ')}: {text}", f"subjects.{i}.{key}")
                continue
            if f"subjects.{i}" in locked_set or f"subjects.{i}.{key}" in locked_set:
                refused.append(f"subjects.{i}.{key}")
                continue
            new = _text(value)
            if new != subjects[i].get(key, ""):
                changed.append(f"person {i + 1} {key}: {_short(subjects[i].get(key))} → {_short(new)}")
                subjects[i][key] = new

    removals = set()
    for raw in patch.get("remove_subjects") or []:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(subjects):
            continue
        if f"subjects.{i}" in locked_set:
            refused.append(f"subjects.{i} (remove)")
            continue
        removals.add(i)
    for i in sorted(removals, reverse=True):
        changed.append(f"person {i + 1} removed: {_short(subjects[i].get('who'))}")
        del subjects[i]

    for entry in patch.get("add_subjects") or []:
        if isinstance(entry, dict):
            subjects.append({k: _text(entry.get(k)) for k in _SUBJECT_FIELDS})
            changed.append(f"person added: {_short(subjects[-1].get('who'))}")

    out["subjects"] = subjects
    # Through normalize again: the count follows the list, empties are dropped.
    return normalize_sheet(out, medium=out.get("medium") or ""), changed, refused


# Said before the script prompt when the first answer came back short. The
# Showrunner saw a single phrase, and "the prose part failed", on runs that
# reproduced fine afterwards — so the cure is to ask once more, not to guess.
SCRIPT_RETRY_PREFIX = (
    "Your previous answer was far too short — it must be the full prompt, every "
    "paragraph, not a phrase or a summary. Write all of it now.\n\n"
)


# What the writer is told about the image model it writes for. Krea 2's lines
# are what this style always said. MOSS-Image's follow the recaption prompt its
# training captions were written with (OpenMOSS USERGUIDE, "caption.long"):
# composition and spatial relations, and visible text kept exactly, with where
# it sits and how it looks.
_SCRIPT_MODELS = {
    "krea2": {
        "name": "Krea 2",
        "reads": "How Krea 2 reads: it was trained on long, detailed captions in natural English, "
                 "and it attaches a detail to the noun it is written next to. So:",
        "text": "- Write visible text in double quotes, exactly as in the sheet.",
    },
    "moss": {
        "name": "MOSS-Image",
        "reads": "How MOSS-Image reads: it was trained on long, detailed English captions that "
                 "describe the composition, each subject, the setting, colours, light and style, "
                 "and it attaches a detail to the noun it is written next to. It places things "
                 "where the caption says they are, so say where each person and object is and "
                 "what is left of, behind or in front of what. So:",
        "text": "- Write visible text in double quotes, exactly as in the sheet — never translated, "
                "paraphrased or romanised — and say where it is, how large, and in what lettering "
                "and colour.",
    },
}


def script_prompt(
    sheet: dict, *, instruction: str = "", paragraphs: int = DEFAULT_PROSE_PARAGRAPHS,
    model: str = "krea2", emphasise: list[str] | None = None,
    left_out: list[str] | None = None,
) -> str:
    """Step 2: what the writer is asked to turn the sheet into.

    ``emphasise`` is ``emphasis_lines``' output: boxes the check found absent
    from the last picture. Being in the sheet was not enough for them once
    already, so they are called out by name rather than left to take their turn
    in the ordinary order.
    """
    spec = _SCRIPT_MODELS.get(model, _SCRIPT_MODELS["krea2"])
    n = sheet.get("subject_count") or 0
    count_line = (
        f"There are exactly {n} people. Say \"{_NUMBER_WORDS.get(n, n)}\" early and never "
        "a different number. Describe each person once, in the order of the sheet."
        if n >= 2 else
        "There is one person; describe them once." if n == 1 else
        "There are no people; do not add any."
    )
    lines = [
        f"Write the prompt for the {spec['name']} image model from the scene sheet below.",
        "",
        spec["reads"],
        f"- Begin the first sentence with the medium, \"{sheet.get('medium', '')}\", and "
        "what is happening in this scene — the situation and the main action. The medium "
        "decides whether the picture is drawn or photographed; the action decides whether "
        "anything happens in it.",
        f"- {count_line} Name them by what they are (women, girls, men, ...), "
        "not as \"people\".",
        "- Then each person, kept together, in this order: what they are doing and why — "
        "the action as it happens, with real verbs; then the body — where the weight is, "
        "the legs and feet, each hand and what it holds, and the movement you can see; then "
        "the face, the feeling it shows, and what the eyes are on; then how they look and "
        "what they wear.",
        "- Then what the people are doing to or with each other.",
        "- \"story\" is what the picture is about: the opening says it, and every paragraph "
        "serves it. Someone who is not its centre gets a sentence or two, not a paragraph of "
        "their own reasons.",
        "- Each person's \"signature\" is what makes the pose their own. Write it out "
        "concretely — what touches what, where the weight goes, what is asymmetric. "
        "Paraphrased into a general word, the image model draws the stock pose instead.",
        "- Write it the way a good caption describes a picture: flowing, specific "
        "sentences, not the sheet read out box by box. Turn the sheet's comma-separated "
        "labels into natural noun phrases rather than copying them. A box that is empty "
        "is simply not mentioned.",
        "- Use every box of the sheet; nothing in it is filler. Each person's \"details\" "
        "box is where the picture gets its richness, so write all of it out for that person.",
        "- An object in someone's hands is written once, with that person. Repeating it "
        "elsewhere makes the image model draw a second one.",
        "- Spend the length on the action and the people — movement, hands, legs, faces, "
        "where the eyes go — then on materials and textures.",
        "- The setting is the place the action happens in: write it as that, with the props "
        "where they are.",
        "- Camera and composition, then light, colour and mood, come last and short. They "
        "follow from what is happening and never stand in for it: a sentence that is only "
        "atmosphere names nothing the picture can show.",
        "- You are transcribing the sheet, not judging it. Write every box as it is: do "
        "not soften, blur, shorten or leave out any of it, and do not swap a word for a "
        "milder or vaguer one. Bodies, the state of clothing and how people touch are "
        "written as plainly as the sheet says them. A euphemism here is not politeness — "
        "it is a different picture.",
        "- \"other\" and every word in \"unclassified\" are part of the picture too, though "
        "they had no box of their own: write them in.",
        "- Use only what the sheet says. Do not add people, objects or actions; an extra "
        "word becomes an extra thing in the picture.",
        "- Plain sentences only: no tags, no underscores, no parentheses, no headings, "
        "no lists. Leave out words like masterpiece, best quality, 8k — they describe "
        "nothing visible.",
        spec["text"],
        f"- {_length_line(paragraphs, n)} Output only the prompt.",
    ]
    if emphasise:
        lines += [
            "",
            "[These did not reach the last picture]",
            "The image model dropped these boxes when this sheet was rendered. State "
            "each one plainly and early, in a clause of its own — not tacked onto the "
            "end of a sentence about something else. Being written once was already "
            "not enough for them.",
            *[f"- {line}" for line in emphasise],
            "Do not pad the rest of the prompt to compensate, and do not drop anything "
            "else to make room: say these plainly and carry on with the sheet.",
        ]
    if left_out:
        lines += [
            "",
            "[Your last draft left these out]",
            "Write the whole prompt again, and this time give each of these its place in the "
            "picture — inside the sentences about that person or that part of the scene, in "
            "the story's flow. Not as a list, and not tacked onto the end.",
            *[f"- {line}" for line in left_out],
        ]
    if instruction:
        lines += ["", "[User instruction — already applied in the sheet; keep it]", instruction]
    lines += ["", "[Scene sheet]", json.dumps(sheet, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


# Sentences per paragraph. Higher than the natural style's: those sentences
# carry inline tags, these carry only description, and at 2-4 sentences the
# writer dropped most of the sheet's details to fit (381 words at 5
# paragraphs for five people).
_SCRIPT_SENTENCES = {3: "6-8", 4: "5-7", 5: "4-6", 6: "4-6", 7: "3-5"}


def _length_line(paragraphs: int, people: int) -> str:
    """How long, and where the length goes: the action and the people first,
    since they are what goes wrong when squeezed — and what a mood-only
    picture lacks (the Showrunner, 2026-11-01)."""
    n = clamp_prose_paragraphs(paragraphs)
    room = (
        "Give the action and the people about two thirds of the paragraphs" if people and n >= 5 else
        "Keep the action and the people in the first paragraphs"
    )
    return (
        f"Write exactly {n} paragraphs of {_SCRIPT_SENTENCES.get(n, '4-6')} full sentences each. "
        f"{room}; the rest go to the place it happens in, its layers and effects, then "
        "camera, light and mood, briefly."
    )


_PAREN_TAGS_RE = re.compile(r"\s*\(([^()]*_[^()]*)\)")
_MARKDOWN_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", re.M)
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")


def clean_script(text: str) -> str:
    """Remove tag syntax the writer let through: booru tags in parentheses,
    underscores between words, escaped underscores, markdown markers."""
    out = (text or "").replace("\\_", "_")
    out = _PAREN_TAGS_RE.sub("", out)
    out = _BOLD_RE.sub(r"\1", out)
    out = _MARKDOWN_RE.sub("", out)
    out = re.sub(r"(?<=\w)_(?=\w)", " ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def finish_script(
    text: str, sheet: dict, *, required: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Clean the script and put back what it dropped. Returns the script and
    a note for each repair, so the screen can say what was changed."""
    notes: list[str] = []
    script = clean_script(text)
    medium = sheet.get("medium") or ""
    if medium and script.lower().startswith(medium.lower()):
        # "anime illustration of five girls…" / "Anime illustration of…" —
        # the article fell off.
        script = ("An " if medium[:1].lower() in "aeiou" else "A ") + script[:1].lower() + script[1:]
        # "…illustration A young woman…" — the full stop fell off too.
        cut = len("An ") + len(medium) if medium[:1].lower() in "aeiou" else len("A ") + len(medium)
        if re.match(r" [A-Z]", script[cut:cut + 2]):
            script = script[:cut] + "." + script[cut:]
    elif script[:1].islower():
        script = script[:1].upper() + script[1:]
    head = script[:300].lower()
    core = [w for w in re.findall(r"[a-z]+", medium.lower()) if len(w) > 2]
    if medium and core and not any(w in head for w in core):
        article = "An" if medium[:1].lower() in "aeiou" else "A"
        script = f"{article} {medium}. {script}"
        notes.append(f'medium "{medium}" was missing from the opening — added')
    n = sheet.get("subject_count") or 0
    if n >= 2:
        first = script.split("\n\n", 1)[0].lower()
        word = _NUMBER_WORDS.get(n, str(n))
        if not re.search(rf"\b({word}|{n})\b", first):
            notes.append(f"the opening does not say {word} people")
    for t in sheet.get("text_in_image") or []:
        if f'"{t}"' not in script:
            script += f'\n\nThe words "{t}" appear in the image.'
            notes.append(f'text "{t}" was missing — added')
    notes += [f"not in the script — {line}" for line in uncovered(script, sheet, required)]
    return script, notes


# ── Nothing on the sheet is lost on the way to the prompt ────────────────────
#
# The Showrunner (2026-10-31): the reading is right, but the prompt comes out
# as if censored. Nothing checked the writer against the sheet, so a box it
# softened or skipped was simply gone. Now a box whose words are mostly
# missing from the script is written in as the sheet has it, and every
# unclassified tag and handed-over hint is written in too.

_STOP_WORDS = {
    "with", "from", "that", "this", "their", "them", "they", "into", "onto", "over",
    "under", "behind", "front", "near", "side", "very", "some", "each", "other", "while",
    "which", "where", "there", "here", "about", "around", "slightly", "visible", "both",
    "also", "just", "than", "then", "only", "more", "most", "being", "have", "has",
}


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:5]


def _content(text: str, min_len: int = 4) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) >= min_len and w not in _STOP_WORDS}


def _missing_share(text: str, script_stems: set[str]) -> float:
    words = _content(text)
    if not words:
        return 0.0
    return sum(_stem(w) not in script_stems for w in words) / len(words)


def uncovered(script: str, sheet: dict, required: list[str] | None = None) -> list[str]:
    """What the sheet says that the script does not, one line per box — for the
    writer to be asked again (``script_prompt(emphasise=...)``).

    **Detection only.** It used to paste each missing box onto the end of the
    script ("A girl: the departing ferry."), and on a crowd that became a
    paragraph of fragments under one repeated label — "Young woman, background:"
    three times, nobody could tell whose (the Showrunner, 2026-11-03: the story
    falls apart). A box goes back into the picture as a sentence the writer
    wrote, or not at all; what is still missing after the retry is said, not
    pasted.

    ``story`` is not matched: it is what the script is about, never a phrase to
    find in it.
    """
    stems = {_stem(w) for w in re.findall(r"[a-z]+", (script or "").lower())}
    compact = re.sub(r"[^a-z]", "", (script or "").lower())
    lines: list[str] = []
    for box in ("situation", "interaction", "rendering", "setting", "layers", "props", "effects",
                "lighting", "camera", "palette_mood", "other"):
        value = sheet.get(box) or ""
        if value and _missing_share(value, stems) > 0.5:
            lines.append(f"{box}: {value}")
    people = sheet.get("subjects") or []
    for i, person in enumerate(people):
        who = person.get("who") or f"person {i + 1}"
        # Several people can share a "who" ("young woman, background"); the
        # number keeps the line pointing at one of them.
        label = f"person {i + 1}, {who}" if len(people) > 1 else who
        # "who" is not checked: the writer always renames it ("female, young adult"
        # becomes "a young woman"), and the audit showed it flagged on every run.
        for box in _SUBJECT_FIELDS:
            value = person.get(box) or ""
            if box != "who" and value and _missing_share(value, stems) > 0.5:
                lines.append(f"{box} ({label}): {value}")
    tags: list[str] = []
    for tag in list(sheet.get("unclassified") or []) + list(required or []):
        core = re.sub(r"\s*\([^)]*\)", "", str(tag)).replace("_", " ").strip()
        if core and core not in tags:
            tags.append(core)
    missing = [
        t for t in tags
        # "hairclip" is in a script that says "hair clip".
        if re.sub(r"[^a-z]", "", t.lower()) not in compact
        and (any(_stem(w) not in stems for w in _content(t, 3))
             or not _content(t, 3) and t.lower() not in (script or "").lower())
    ]
    if missing:
        lines.append("also in the picture: " + ", ".join(missing))
    return lines


# ── Checking the picture against the sheet it was written from ───────────────
#
# Everything up to here is open loop: the sheet is read, the script is written,
# the picture comes out, and nobody looks. `uncovered` checks that the
# WRITER kept every box; it cannot know whether the IMAGE MODEL did. So an
# intended right hand hooked over a window frame can be quietly absent and the
# run still reports success.
#
# The intended sheet is already kept beside the finished picture
# (`CreationRecord.scene_sheet`), so the comparison needs nothing new stored —
# only the finished picture read back into a sheet of its own.

#: The scene boxes the check compares. `medium` is in: asking for an anime
#: illustration and being handed a photograph is the loudest failure there is,
#: and `finish_script` only guards the medium in the prose, never in the picture.
#:
#: `rendering` is deliberately OUT. It is the most reworded box there is — "soft
#: cel shading with visible line work" read back as "cel shaded, clear lineart"
#: is the same picture and scored 0.6 missing in the calibration, a false alarm
#: on a faithful render. `medium` already carries the distinction that matters;
#: a rendering nuance is not something the Showrunner can act on anyway, and a
#: check that cries wolf is worse than no check.
_CHECK_SCENE_BOXES = ("medium", "situation", "interaction", "setting",
                      "layers", "props", "effects", "lighting", "camera",
                      "palette_mood", "other")


def _sheet_text(sheet: dict) -> str:
    """Everything a sheet says, as one blob — what the check matches against."""
    parts: list[str] = []
    for key, value in (sheet or {}).items():
        if key in ("subjects", "subject_count"):
            continue
        parts.append(_text(value))
    for person in (sheet or {}).get("subjects") or []:
        if isinstance(person, dict):
            parts.extend(_text(v) for v in person.values())
    return " ".join(p for p in parts if p)


def compare_sheets(
    intended: dict, got: dict, *, threshold: float = 0.5,
) -> tuple[list[dict], int]:
    """Which boxes of the intended sheet did not reach the picture.

    Returns ``(missed, checked)``; each miss is
    ``{"box", "person", "who", "value"}`` (``person`` is None for a scene box).

    **Deliberately not box against box.** The model reading a finished picture
    puts the same thing in a different box all the time — the work of a hand
    turns up under "pose_action" — and comparing box to box reports those as
    losses. So the whole read-back is flattened and each intended box is asked
    only whether its words are *anywhere* in it, by the same stem test and the
    same calibrated threshold `uncovered` already uses on the script.
    """
    stems = {_stem(w) for w in re.findall(r"[a-z]+", _sheet_text(got).lower())}
    missed: list[dict] = []
    checked = 0

    for box in _CHECK_SCENE_BOXES:
        value = (intended or {}).get(box) or ""
        if not isinstance(value, str) or not value.strip():
            continue
        checked += 1
        if _missing_share(value, stems) > threshold:
            missed.append({"box": box, "person": None, "who": "", "value": value})

    for i, person in enumerate((intended or {}).get("subjects") or []):
        if not isinstance(person, dict):
            continue
        who = str(person.get("who") or f"person {i + 1}")
        for box in _SUBJECT_FIELDS:
            # "who" is never checked, for the reason `uncovered` gives: the
            # reader always renames it, so it flagged on every single run.
            if box == "who":
                continue
            value = str(person.get(box) or "")
            if not value.strip():
                continue
            checked += 1
            if _missing_share(value, stems) > threshold:
                missed.append({"box": box, "person": i + 1, "who": who, "value": value})

    return missed, checked


def emphasis_lines(sheet: dict, boxes: list[str]) -> list[str]:
    """``["hands", "props"]`` → ``["hands: her right hand is ...", ...]``.

    The request carries box names only; the values come from the sheet, which
    is the authority on what was meant.
    """
    out: list[str] = []
    for raw in boxes or []:
        box = str(raw or "").strip()
        if not box:
            continue
        value = (sheet or {}).get(box)
        if isinstance(value, str) and value.strip():
            out.append(f"{box}: {value.strip()}")
            continue
        for i, person in enumerate((sheet or {}).get("subjects") or []):
            if not isinstance(person, dict):
                continue
            value = str(person.get(box) or "")
            if value.strip():
                who = str(person.get("who") or f"person {i + 1}")
                out.append(f"{box} ({who}): {value.strip()}")
    return out


# ── Fusing worlds: WD14 tags as ingredients, not as description ─────────────
#
# The Showrunner (2026-10-21): mixing two pictures' atmosphere is not adding
# them up, and the Krea 2 style — having dropped WD14 — reads as especially
# monotonous. Read faithfully, a sheet only restates its references. So in
# "fuse" mode each reference's distinctive WD14 tags (the ones no other
# reference shares) become ingredients that must *transform* the scene, and
# a single reference gets foreign tags from the vocabulary as the other world.
# The chaos dial decides how many, and how hard the worlds collide.

_RATING_TAGS = {"general", "sensitive", "questionable", "explicit", "safe", "nsfw"}

# What WD14 says about the file, not the picture: the only tags (with the
# person counts, which the sheet's own list decides) a hint or a fusion leaves
# out. Everything else the tagger read is used — the Showrunner, 2026-10-31:
# no tag is dropped for want of a box, and no model judges what may be kept.
QUALITY_TAGS = frozenset({
    "masterpiece", "best_quality", "high_quality", "good_quality", "normal_quality",
    "low_quality", "worst_quality", "bad_quality", "amazing_quality", "very_aesthetic",
    "lowres", "highres", "absurdres", "incredibly_absurdres", "huge_filesize",
    "jpeg_artifacts",
})
_SCORE_RE = re.compile(r"^score_\d+(?:_up)?$")


def is_quality_tag(tag: str) -> bool:
    key = str(tag or "").strip().lower().replace(" ", "_")
    return key in QUALITY_TAGS or bool(_SCORE_RE.match(key))

# Person boxes a fusion must not touch: who they are and how they look.
_IDENTITY_BOXES = ("who", "appearance", "expression", "position")


# Which WD14 tags can change a world. Measured on two Moss pictures of one
# girl each: their distinctive tags were "hand on own hip", "sneakers",
# "looking at viewer", "blurry background" — pose and framing words that gave
# the fusion nothing to work with, and the scene came back unchanged. Places,
# weather, light effects and things go first; clothing fills in; expression,
# action, hair and framing words are left out.
#
# Only tags whose axis the catalog knows get through. An unknown tag can be
# anything — the outsider sample from the vocabulary once brought a sexual
# one — and this is a whitelist on purpose, so no list of what to keep out
# has to live in the code. Good unknown words (ice, water, bird) are lost to
# that; safe beats rich here.
_WORLD_AXES = ("location", "time_weather", "visual", "always_fixed")
_FILLER_AXES = ("clothing",)
_POSE_WORDS = ("hand", "arm", "leg", "finger", "looking", "head_rest", "pose", "view",
               "focus", "shot", "from_", "background", "lying", "sitting", "standing",
               "pov", "profile", "tilt", "lean", "crossed", "spread", "own_",
               "depth_of_field", "blurry")


def world_tags(tags: list[str]) -> list[str]:
    """Every tag, the ones that can change a world first.

    It used to be a whitelist: only tags the catalog files under a world axis
    passed, and pose, expression, eye and unknown words were dropped. The
    Showrunner (2026-10-31) does not accept tags thrown away for want of a
    place, so now it is only an order — world, then clothing, then the rest.
    """
    from ..tags.catalog import get_tag_axis
    world, filler, rest = [], [], []
    for tag in tags:
        key = str(tag).strip().lower().replace(" ", "_")
        # Eye colour is filed with things; it is the person, not the world.
        if key.endswith("_eyes") or any(w in key for w in _POSE_WORDS):
            rest.append(tag)
            continue
        axis = get_tag_axis(key)
        if axis in _WORLD_AXES:
            world.append(tag)
        elif axis in _FILLER_AXES:
            filler.append(tag)
        else:
            rest.append(tag)
    return world + filler + rest


def unused_ingredients(sheet: dict, ingredients: dict[str, list[str]]) -> list[str]:
    """Ingredients whose words appear nowhere in the fused sheet."""
    text = json.dumps(sheet, ensure_ascii=False).lower()
    out = []
    for words in ingredients.values():
        for w in words:
            core = [p for p in re.findall(r"[a-z]+", w.lower()) if len(p) > 2]
            if core and not any(p in text for p in core):
                out.append(w)
    return out


# ── Placing the hints the reader did not use ─────────────────────────────────
#
# A hint the reader left out is not dropped (the Showrunner, 2026-10-31): one
# more call places each into a box, the code appends it there — never
# rewriting what the box already says — and a tag that still has nowhere to go
# is kept as "unclassified" and written into the prompt anyway.

def place_prompt(sheet: dict, tags: list[str]) -> str:
    """What the text model is asked: which box each tag belongs in."""
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    scene = [b for b in _SCENE_BOXES if b not in ("medium", "rendering")]
    lines = [
        "Below is the scene sheet for a picture, and tags a tagger read from the same "
        "picture that the sheet does not mention yet. Say which box each tag belongs in.",
        "",
        "- Every tag gets a box. A tag about one person goes in that person's box, with "
        "their \"index\"; anything else in a scene box. When no box fits, use \"other\".",
        "- Do not leave a tag out, and do not change its words: it is added to the box "
        "as it is.",
        f"- Scene boxes: {', '.join(scene)}. Person boxes: {', '.join(_SUBJECT_FIELDS)}.",
        "",
        "Output only JSON:",
        '{"place": [{"tag": "<the tag as given>", "box": "<box>", "person": <index or null>}]}',
        "",
        "[Tags]",
        *[f"- {t}" for t in tags],
        "",
        "[Scene sheet]",
        json.dumps(indexed, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def apply_placement(
    sheet: dict, placement: dict, tags: list[str],
) -> tuple[dict, list[str], list[str]]:
    """Append each tag to the box it was placed in. Returns the new sheet,
    readable "tag → box" lines, and the tags left unclassified (also added
    to the sheet's "unclassified" list)."""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in sheet.items()}
    subjects = [dict(s) for s in sheet.get("subjects") or []]
    by_tag: dict[str, dict] = {}
    for entry in (placement or {}).get("place") or []:
        if isinstance(entry, dict) and entry.get("tag"):
            by_tag.setdefault(str(entry["tag"]).strip().lower(), entry)

    def _append(old: str, word: str) -> str:
        return f"{old}, {word}" if old else word

    placed: list[str] = []
    unclassified = list(out.get("unclassified") or [])
    for tag in tags:
        entry = by_tag.get(tag.strip().lower()) or {}
        box = str(entry.get("box") or "").strip()
        person = entry.get("person")
        if box in _SUBJECT_FIELDS and isinstance(person, int) and 0 <= person < len(subjects):
            subjects[person][box] = _append(subjects[person].get(box, ""), tag)
            placed.append(f"{tag} → person {person + 1} {box}")
        elif box in _SCENE_BOXES and box not in ("medium", "rendering"):
            out[box] = _append(out.get(box) or "", tag)
            placed.append(f"{tag} → {box}")
        elif tag not in unclassified:
            unclassified.append(tag)
    out["subjects"] = subjects
    out["unclassified"] = unclassified
    return out, placed, [t for t in unclassified if t in tags]


def _split_by_share(k: int, shares: dict[str, float]) -> dict[str, int]:
    """Share out ``k`` ingredients by weight, floor 1 for anyone with a share.

    Largest-remainder, so the total is exactly ``k`` whenever the floors fit.
    """
    live = {s: max(0.0, float(w or 0.0)) for s, w in shares.items()}
    live = {s: w for s, w in live.items() if w > 0}
    if not live:
        return {}
    total = sum(live.values())
    raw = {s: w / total * k for s, w in live.items()}
    out = {s: int(v) for s, v in raw.items()}
    short = k - sum(out.values())
    for s, _ in sorted(raw.items(), key=lambda p: (-(p[1] - int(p[1])), -live[p[0]], p[0])):
        if short <= 0:
            break
        out[s] += 1
        short -= 1
    return {s: max(1, v) for s, v in out.items()}


def pick_ingredients(
    pools: dict[str, list[str]], chaos: float, *, skip: set[str] | frozenset = frozenset(),
    rng=None, scores: dict[str, float] | None = None, min_score: float = 0.0,
    dropped: list[tuple[str, str]] | None = None,
    shares: dict[str, float] | None = None,
) -> dict[str, list[str]]:
    """Draw ingredients from each source's tag pool, in plain words.

    Random on purpose: the same two pictures should not fuse the same way
    every run. More chaos, more ingredients per source (2 → 8).

    Only quality tags, ``skip`` (the person counts) and tags under
    ``min_score`` are left out; each goes into ``dropped`` with its reason.
    A tag with no score (the vocabulary's outsiders) is kept.

    ``shares`` maps a source label to its weight. Given it, chaos sets HOW MANY
    ingredients there are in total and the weights decide WHOSE they are, so a
    60% reference brings more of the fusion than a 10% one — otherwise every
    source brought the same number and the sliders did nothing here. A source
    with no share (``another world`` is nobody's reference) keeps the full
    count, and a weighted source never drops below one.
    """
    import random
    rng = rng or random
    k = 2 + round(max(0.0, min(1.0, chaos)) * 6)
    per_source = _split_by_share(k, {s: w for s, w in (shares or {}).items() if s in pools})
    score_of = {str(t).strip().lower().replace(" ", "_"): float(v) for t, v in (scores or {}).items()}
    out: dict[str, list[str]] = {}
    for source, tags in pools.items():
        clean = []
        ranked = world_tags(list(tags))
        for tag in ranked:
            word = str(tag).strip().lower()
            key = word.replace(" ", "_")
            if not word:
                continue
            reason = ("quality" if is_quality_tag(key) else
                      "rating" if key in _RATING_TAGS else
                      "person count" if key in skip else
                      f"confidence {score_of[key]:.2f}" if score_of.get(key, 1.0) < min_score else "")
            if reason:
                if dropped is not None and all(w != word.replace("_", " ") for w, _ in dropped):
                    dropped.append((word.replace("_", " "), reason))
                continue
            word = word.replace("_", " ")
            if word not in clean:
                clean.append(word)
        if clean:
            # Random within the best twice-k, so the world tags lead and runs differ.
            n_src = per_source.get(source, k)
            head = clean[: max(n_src * 2, n_src)]
            out[source] = rng.sample(head, min(n_src, len(head)))
    return out


def _chaos_line(chaos: float) -> str:
    if chaos < 0.34:
        return ("Chaos is low: the other world seeps in at the edges — materials, light and "
                "small things change, the scene still reads as one familiar place.")
    if chaos < 0.67:
        return ("Chaos is medium: the worlds are interwoven — the setting itself is built from "
                "both, and the strangeness is visible at first glance.")
    return ("Chaos is high: the worlds collide — scale, physics and seasons may break, but it "
            "is still ONE picture, described concretely, not a collage.")


def fuse_prompt(
    sheet: dict, ingredients: dict[str, list[str]], chaos: float,
    locked: list[str] | None = None,
) -> str:
    """What the text model is asked: fuse the ingredients into the scene, as a patch."""
    locked = list(locked or [])
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    lines = [
        "Below is the scene sheet for a picture, and ingredients from other worlds. "
        "Fuse them into the scene and return the result as a patch on the sheet.",
        "",
        "Why: a picture that only restates its references is monotonous. Fusion is not "
        "addition — an ingredient placed beside the scene as one more object is the "
        "dull answer. Each ingredient must transform something already there:",
        "- what a surface or the architecture is made of",
        "- the weather, the air, the light source or its colour",
        "- the material, pattern or texture of a garment, or what someone holds",
        "- the props and the layers of the scene",
        "One ingredient may take the place of an element of the other world. The result "
        "is one world where both are true at once.",
        f"- {_chaos_line(chaos)}",
        "- Use every ingredient. Write new values as plain English phrases, concrete and "
        "visible, in the sheet's own style, never softened or blurred into a vaguer word.",
        "- The people stay who they are: do not change their who, appearance, expression "
        "or position, and do not add or remove anyone. Their outfit and what they hold "
        "may change.",
    ]
    if locked:
        lines.append("- Locked by the user, must not appear in the patch: " + ", ".join(locked)
                     + " (\"subjects.N\" is the whole person N).")
    lines += [
        "",
        "Output only JSON in this shape, leaving out any part you do not need:",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}]',
        "}",
        "",
        "[Ingredients]",
    ]
    for source, words in ingredients.items():
        lines.append(f"- from {source}: {', '.join(words)}")
    lines += ["", "[Scene sheet]", json.dumps(indexed, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def apply_fusion(
    sheet: dict, patch: dict, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str]]:
    """apply_patch with the people's identity held: no one added or removed,
    and who / appearance / expression / position kept."""
    patch = {k: v for k, v in (patch or {}).items() if k in ("set", "subjects")}
    held = list(locked or []) + [
        f"subjects.{i}.{box}"
        for i in range(len(sheet.get("subjects") or []))
        for box in _IDENTITY_BOXES
    ]
    new, changed, refused = apply_patch(sheet, patch, held)
    # Holding identity is the design, not the user's lock; only report theirs.
    user_locked = set(locked or [])
    refused = [r for r in refused
               if r in user_locked or r.split(".")[0] != "subjects"
               or ".".join(r.split(".")[:2]) in user_locked]
    return new, changed, refused


# ── Directions: the model proposes, the Showrunner picks ─────────────────────
#
# Read faithfully and written faithfully, a sheet only restates its reference
# (the Showrunner, 2026-10-31: "can creativity take a leap?"). Every way in so
# far — an instruction, a retake, a WD14 fusion — needed the Showrunner to
# know what to change. Here the model proposes, one lens per call, and each
# proposal goes through the retake's patch, held to that lens's boxes:
#
# 1. propose (the entrance, free): the sheet only — given the picture, the
#    model copies the picture — and no example ideas, since an example is what
#    comes back (the diary's fingertips, 14 of 15).
# 2. apply (the gate, strict): patch_prompt + apply_patch with every box
#    outside the lens locked, and the people's identity always held.

# A person's face, hair and body, and the picture's medium, are never a
# direction's to change.
_DIRECTION_HELD_PERSON = ("who", "appearance")
_DIRECTION_HELD_SCENE = ("medium", "rendering", "text_in_image")

DIRECTION_KINDS: dict[str, dict] = {
    "light": {
        "scene": ("lighting", "palette_mood", "effects"),
        "person": (),
        "lens": (
            "You design the light and colour. Decide where the light comes from, its "
            "colour and hardness, what it catches and what falls into shadow, and the "
            "colour scheme it leaves the picture in — so that the light itself tells "
            "what this moment is."
        ),
    },
    "camera": {
        "scene": ("camera", "layers"),
        "person": ("position",),
        "lens": (
            "You are the cinematographer. Choose where the camera stands, its height and "
            "angle, the lens and the distance, what sits in the foreground and what the "
            "frame cuts off, and where each person stands in it — so that the frame makes "
            "this picture's subject hit hardest."
        ),
    },
    "moment": {
        "scene": ("situation", "interaction", "props", "effects"),
        "person": _ACTION_BOXES,
        "lens": (
            "You choose the instant. The same people in the same place, caught at a "
            "different beat: a moment before or after, or the peak of what is happening. "
            "Decide what each person's body and hands are doing, what their face shows, "
            "and what is moving around them."
        ),
    },
    "world": {
        "scene": ("setting", "layers", "props", "effects", "lighting", "palette_mood"),
        "person": ("outfit",),
        "lens": (
            "You move this scene into another world. Keep the people and what they are "
            "doing, but carry them to a different place, era, climate or material reality, "
            "and let that world reach the fabric and make of their clothing. The world "
            "should feel chosen for these people, not pasted behind them."
        ),
    },
}


def direction_prompt(
    sheet: dict, kind: str, *, instruction: str = "", avoid: list[str] | None = None,
) -> str:
    """Step 1 of a direction: what the model is asked to propose, freely."""
    spec = DIRECTION_KINDS[kind]
    lines = [
        "Below is the scene sheet a vision model read from a reference picture. Written "
        "faithfully, it only restates the reference, and the picture comes out ordinary. "
        "Propose one bold direction for it, through one lens only.",
        "",
        "[Your lens]",
        spec["lens"],
        "",
        "What makes a direction worth taking:",
        "- It is built from what this sheet holds — its people, what they do, its objects "
        "and its place. A treatment any picture could be given is not a direction.",
        "- The first idea that comes to mind is the one every picture gets. Go past it.",
        "- Bold, but still one picture: it must be drawable as a single image.",
        "- Stay inside your lens. The rest of the sheet is someone else's job, and what "
        "you write outside it is thrown away.",
        "- The people stay who they are: face, hair and body do not change.",
        "- Say it concretely, in plain English: what can be seen, and where. Do not "
        "soften or blur anything the sheet says.",
    ]
    if avoid:
        lines += ["", "[Already proposed through this lens — go somewhere else]"]
        lines += [f"- {a}" for a in avoid]
    if instruction:
        lines += ["", "[User instruction — the sheet already follows it; keep it]", instruction]
    lines += [
        "",
        "Output only JSON:",
        '{"title": "a few words naming the idea", '
        '"direction": "two or three sentences: what changes and how it looks"}',
        "",
        "[Scene sheet]",
        json.dumps(sheet, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def scope_locks(
    sheet: dict, scene: tuple, person: tuple, locked: list[str] | None = None,
) -> list[str]:
    """Every box outside a scope, the people's identity and the medium, plus
    the Showrunner's own locks — in the paths apply_patch reads."""
    out = list(locked or [])
    out += [box for box in _SCENE_BOXES + ("text_in_image",)
            if box not in scene or box in _DIRECTION_HELD_SCENE]
    for i in range(len(sheet.get("subjects") or [])):
        out += [f"subjects.{i}.{box}" for box in _SUBJECT_FIELDS
                if box not in person or box in _DIRECTION_HELD_PERSON]
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def direction_locks(sheet: dict, kind: str, locked: list[str] | None = None) -> list[str]:
    spec = DIRECTION_KINDS[kind]
    return scope_locks(sheet, spec["scene"], spec["person"], locked)


def _apply_scoped(
    sheet: dict, patch: dict, locks: list[str], locked: list[str] | None,
) -> tuple[dict, list[str], list[str], list[str]]:
    """apply_patch under scope locks, with no one added or removed. Returns
    the new sheet, what changed, what the Showrunner's locks refused, and
    what fell outside the scope."""
    patch = {k: v for k, v in (patch or {}).items() if k in ("set", "subjects")}
    new, changed, refused = apply_patch(sheet, patch, locks)
    user_locked = set(locked or [])

    def _by_user(path: str) -> bool:
        return path in user_locked or ".".join(path.split(".")[:2]) in user_locked

    return (new, changed,
            [r for r in refused if _by_user(r)],
            [r for r in refused if not _by_user(r)])


def apply_direction(
    sheet: dict, patch: dict, kind: str, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str], list[str]]:
    """apply_patch held to one lens. No one is added or removed: a direction
    changes how the picture is made, not who is in it."""
    return _apply_scoped(sheet, patch, direction_locks(sheet, kind, locked), locked)


# ── Staging: from people posing to a moment where something happens ─────────
#
# The Showrunner (2026-11-01): tags and mood only make standing portraits.
# Read faithfully, a pin-up gives "standing", and the script opened with hair
# and eyes on every audited run. Staging is one text call after the reading
# that turns the sheet into an instant: what each person does and why, the
# body, the gaze, the movement, the situation. A dial decides how far — "read"
# skips it, "move" keeps the reference's action and place, "restage" gives
# the same people in the same place a different, larger action.

STAGING_SCOPES: dict[str, dict] = {
    "move": {
        "scene": ("story", "situation", "interaction", "effects", "props"),
        "person": _ACTION_BOXES,
        "how": (
            "Keep what the reference shows them doing and where. Make it the instant in "
            "the middle of that action — not the pose before or after it — and give it its "
            "reason, its body and its movement."
        ),
        "pose": (
            "Each person's \"signature\" is what makes the reference's pose its own, and it "
            "is not yours to change: the reason, the gaze and the movement you add have to fit "
            "that exact pose. Swapping it for an easier, more ordinary one loses the picture."
        ),
    },
    "restage": {
        "scene": ("story", "situation", "interaction", "effects", "props", "camera", "layers"),
        "person": _ACTION_BOXES + ("position", "signature"),
        "how": (
            "Keep the same people, their clothes and the place, but give them a different "
            "action and situation, with large, visible movement: what these people, in this "
            "place, would be caught in the middle of doing. Where they stand and the camera "
            "may change to show it."
        ),
        "pose": (
            "The new pose must be one only these people, in this place, could take: what each "
            "body touches, which side carries the weight, what is asymmetric. Rewrite "
            "\"signature\" to say exactly that. The first pose that comes to mind is the one "
            "every picture already has, and is not staging."
        ),
    },
}


def stage_prompt(sheet: dict, mode: str, *, instruction: str = "",
                 locked: list[str] | None = None) -> str:
    """What the text model is asked: stage the sheet, as a patch."""
    spec = STAGING_SCOPES[mode]
    indexed = dict(sheet)
    indexed["subjects"] = [{"index": i, **s} for i, s in enumerate(sheet.get("subjects") or [])]
    person_boxes = ", ".join(spec["person"])
    scene_boxes = ", ".join(spec["scene"])
    lines = [
        "Below is the scene sheet a vision model read from a reference picture. As read, it "
        "says how the people look and what the mood is, and the picture it makes is people "
        "standing and posing — nothing happens in it. Stage it: turn it into a moment where "
        "something is happening.",
        "",
        "[How far]",
        spec["how"],
        "",
        "[The pose]",
        spec["pose"],
        "",
        "What a staged moment holds:",
        "- What each person is doing, as a verb in the middle of happening. A held pose, a "
        "stance or a mood word is a picture where nothing happens.",
        "- Why they are doing it: a reason that belongs to this scene — what they want, "
        "what just happened, what they are reacting to. An action any picture could be "
        "given is not staging; build it from these people, their things and this place.",
        "- The body: where the weight is, what the legs and feet are doing, what each hand "
        "is doing and touching.",
        "- The face shows the feeling, and the eyes are on something or someone in the scene.",
        "- Movement that can be seen: hair, cloth and air moving, the direction of the momentum.",
        "- With several people, what they are doing to or with each other — and one moment "
        "for all of them: who it is about, and how the others take part in it. Not a separate "
        "scene for each person.",
        "- \"situation\": what is happening, what has just happened and what is about to.",
        "- \"story\": one sentence of what the picture is about once it is staged.",
        "- Write it plainly, as it would be seen. Nothing softened or blurred into a vaguer word.",
        "- The people stay who they are: face, hair, body and clothing do not change, and "
        "nobody is added or removed.",
        f"- Change only these boxes. Person boxes: {person_boxes}. Scene boxes: {scene_boxes}. "
        "Anything else you write is thrown away.",
    ]
    if locked:
        lines.append("- Locked by the user, must not appear in the patch: " + ", ".join(locked)
                     + " (\"subjects.N\" is the whole person N).")
    lines += [
        "",
        "Output only JSON in this shape:",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}]',
        "}",
    ]
    if instruction:
        lines += ["", "[User instruction — the sheet already follows it; keep it]", instruction]
    lines += ["", "[Scene sheet]", json.dumps(indexed, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


def apply_staging(
    sheet: dict, patch: dict, mode: str, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str], list[str]]:
    """apply_patch held to the staging scope: who the people are, how they
    look and what they wear never change, and no one is added or removed."""
    spec = STAGING_SCOPES[mode]
    return _apply_scoped(sheet, patch, scope_locks(sheet, spec["scene"], spec["person"], locked), locked)


# ── Reconstruction: the ledger and the reference, read back into one picture ─
#
# The Showrunner (2026-11-03), on a girl watching shooting stars from a pier:
# the steps each worked — the objects were good — but the prompt no longer
# held together. Four women in the opening, "all three" two paragraphs later;
# both hands on her chin while she leant on the railing; a pier "or" a dock
# over water with no word for which side of the railing she stood on, so she
# was drawn floating on the water; every passer-by given a reason and a
# gesture of their own, so the picture had no centre. And the reference's own
# pose — the thing that made it worth reading — staged into an ordinary one.
#
# Every step after the reading changes only its own boxes, and nothing looked
# at the whole sheet against the picture. Refine is not Muse: the sheet does
# not drift over a conversation, it is built once, so the cure is not a code
# rule per failure (each one misfires on the picture that does not fit it) but
# one last read: the reference, plus the ledger of what was read and what each
# step changed and why, handed to the vision model to rebuild as one picture.
# The model decides; code only keeps the shape and the user's locks.

def ledger_step(step: str, intent: str, changes: list[str]) -> dict:
    """One line of the ledger: which step, what it was for, what it changed —
    the same lines the thinking pane already shows."""
    return {"step": step, "intent": intent, "changes": list(changes)}


def reconstruct_prompt(
    ledger: dict, *, staging: str = "read", instruction: str = "", reference_count: int = 1,
) -> str:
    """What the vision model is asked: the ledger → one coherent sheet."""
    refs = ("the reference picture" if reference_count <= 1 else
            f"the {_NUMBER_WORDS.get(reference_count, reference_count)} reference pictures, in order")
    if staging == "restage":
        pose = (
            "- The pose was changed on purpose (the user asked for a different action). Keep the "
            "new one. Check that each \"signature\" is as specific as a real picture's would "
            "be — what touches what, where the weight goes, what is asymmetric — and sharpen it "
            "where it has gone general. The first pose that comes to mind is not a fix."
        )
    else:
        pose = (
            f"- Look at {refs}: each person's pose there is what made it worth reading, and "
            "\"signature\" says what makes it their own. Where a later step drifted from it, "
            "bring it back. Turning a particular pose into a more ordinary one is a loss, "
            "never a correction."
        )
    lines = [
        f"You are given {refs} and the ledger of a scene sheet made from it: \"read\" is "
        "what a vision model saw, \"steps\" is what each later step changed and why, "
        "\"current\" is the sheet as it stands. Each step changed only its own boxes and "
        "none of them looked at the whole, so \"current\" may no longer be one picture. "
        "Rebuild it into one.",
        "",
        "What one picture needs:",
        "- One \"story\": a sentence saying who is doing what and what the picture is about. "
        "Every box serves it. The people who are not its centre take part in that moment "
        "briefly; they do not each get a scene of their own.",
        "- One count: the people are the same people as in \"current\" — nobody added, "
        "nobody removed — and nothing anywhere on the sheet says a different number.",
        "- One body each: each hand does one thing at a time, and what a person leans on, "
        "holds or touches uses a hand or an arm. The pose, the hands, what they hold and "
        "the legs all describe the same body.",
        "- Footing: say what each person stands, sits or lies on, and which side of any "
        "railing, table, wall or edge they are on, as the camera sees it. Nobody floats "
        "unless the picture means them to.",
        "- One place: the setting is one place, said as one — not a choice between two.",
        "- The camera can show what the sheet describes of the people.",
        pose,
        "- What the steps added (the objects, the staged action) stays where it fits the "
        "story. Where it does not, move it or reshape it so that it does.",
        "- Everything else stays exactly as it is. Do not soften, blur or shorten a box, "
        "and do not swap a word for a milder or vaguer one. Who the people are, how they "
        "look and what they wear do not change, and neither does the medium.",
        "",
        "Output only the boxes you change, as a patch on \"current\" — a box you leave out "
        "stays exactly as it is. People are addressed by their \"index\" in \"current\". "
        "If the sheet already holds together, output {\"set\": {}}.",
        "{",
        '  "set": {"<scene box>": "<new value>"},',
        '  "subjects": [{"index": 0, "set": {"<person box>": "<new value>"}}]',
        "}",
    ]
    if instruction:
        lines += ["", "[User instruction — the sheet already follows it; keep it]", instruction]
    current = dict(ledger.get("current") or {})
    current["subjects"] = [{"index": i, **p} for i, p in enumerate(current.get("subjects") or [])]
    lines += ["", "[Ledger]", json.dumps({**ledger, "current": current}, ensure_ascii=False, indent=2)]
    return "\n".join(lines)


#: What the last look may not touch: the picture's medium and visible text, and
#: who the people are, how they look and what they wear. The prompt says so;
#: this is the same thing as a scope (the staging door, `_apply_scoped`).
_RECONSTRUCT_HELD_SCENE = ("medium", "rendering", "text_in_image")
_RECONSTRUCT_HELD_PERSON = ("who", "appearance", "outfit")


def apply_reconstruction(
    before: dict, answer: dict, locked: list[str] | None = None,
) -> tuple[dict, list[str], list[str], list[str]]:
    """The model's patch → the sheet. Returns the sheet, what changed, what the
    user's locks refused, and what fell outside the last look's range.

    A patch, not the whole sheet: the boxes that already hold together are not
    written out again, which was most of the answer and most of the wait (the
    Showrunner, 2026-11-03). No judgement of the content here; nobody is added
    or removed, and the user's locks hold.
    """
    scene = tuple(b for b in _SCENE_BOXES if b not in _RECONSTRUCT_HELD_SCENE)
    person = tuple(b for b in _SUBJECT_FIELDS if b not in _RECONSTRUCT_HELD_PERSON)
    locks = scope_locks(before, scene, person, locked)
    return _apply_scoped(before, answer if isinstance(answer, dict) else {}, locks, locked)

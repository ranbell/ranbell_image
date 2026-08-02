"""The prompt has slots, and each slot has a budget.

Asking a model for "thirty danbooru tags" gets thirty tags, but not thirty
facts. It latches onto whatever the theme suggests hardest and pads with
synonyms of it — a pool theme came back with ``swimwear``, ``black_bikini`` and
``bikini``, three slots of the list saying one thing. The picture then weighted
that one thing three times and the swimsuit ate the frame.

A budget per *aspect* rather than per track fixes it at the root: outfit gets
three tags whether or not outfit is what the model finds most interesting, and
place gets its own four regardless. Nothing can flood, because nothing shares a
pool with anything else.

The shape below is the one that has been working by hand:

    Theme: ...
    Style: ...
    Character: 1girl, brown_hair, blue_eyes
    Emotion: joyful, excited
    Outfit: one-piece_swimsuit
    Body: slender
    Action: swimming, kicking_legs
    Accessories: goggles, swim_cap
    Shot: medium_shot, eye_level
    Place: swimming_pool, sunshine
    Object: clear_water, poolside_chair, inflatable_toy
    Effect: kodak color, detailed
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..tags import catalog as tag_catalog
from ..tags.conflict import contradicts

# Head nouns that make a tag a body part however it is modified.
#
# The catalog is no help here on its own: it lists `thighs` but not `legs`,
# `thigh_gap` but not `medium_breasts`, so membership misses most of what WD14
# actually reads off a picture. What a tag is a part *of* is its last word, and
# the modifier only changes the size or the state.
_BODY_PART_NOUNS = frozenset({
    "breast", "breasts", "thigh", "thighs", "leg", "legs", "arm", "arms",
    "hand", "hands", "foot", "feet", "shoulder", "shoulders", "hip", "hips",
    "waist", "navel", "stomach", "back", "neck", "chest", "skin", "body",
    "butt", "ass", "cleavage", "midriff", "collarbone", "knee", "knees",
    "ankle", "ankles", "wrist", "wrists", "calf", "calves", "throat",
})

# The face is a body part but it is not *this* slot's body part. Eye colour is
# the character's and belongs on her line; an expression is the Emotion slot's.
# Routing `blue_eyes` into Body would take the identity apart again.
_FACE_NOUNS = frozenset({
    "face", "eye", "eyes", "lip", "lips", "nose", "ear", "ears", "tongue",
    "cheek", "cheeks", "chin", "forehead", "eyebrow", "eyebrows",
})

# Everything the Object line must refuse: a body part is never furniture.
_BODY_NOUNS = _BODY_PART_NOUNS | _FACE_NOUNS

# What state her body and clothes are in. These have no body-part head noun and
# no slot claimed them, so `wet` and `wet_clothes` were deleted alongside the
# legs they belonged to — a theme about being caught in the rain reached the
# render with nothing wet in it.
_BODY_STATES = frozenset({
    "wet", "wet_clothes", "wet_hair", "wet_skin", "soaked", "damp",
    "sweat", "sweaty", "steam", "dirty", "muddy", "dusty", "bloody",
    "covered_in_mud", "goosebumps", "shivering", "flushed", "barefoot",
})


@dataclass(frozen=True)
class Slot:
    key: str
    label: str            # what the prompt line is called
    track: str            # "person" | "background" | "global"
    cap: int              # how many tags may sit here
    axes: tuple[str, ...] = ()      # get_tag_axis values that belong here
    sets: tuple[str, ...] = ()      # tag_catalog frozenset names that belong here
    head_nouns: frozenset[str] = frozenset()   # accept anything ending in one
    names: frozenset[str] = frozenset()        # accept these tags exactly
    user_owned: bool = False        # filled from session inputs, never composed
    locked: bool = False            # comes from the character, not from anything else
    query: str = ""                 # what to search the vocabulary for
    guidance: str = ""              # what to tell the model this slot is
    # One face has one expression. Retrieval topping up a slot the model has
    # already answered puts `expressionless` next to `happy`, which is a second
    # answer rather than a top-up.
    exclusive: bool = False
    # What the theme asked for outranks what the drafts happened to show.
    #
    # "The canvas is the source of truth" is right for Outfit and Place — those
    # are facts a 512px sketch reports reliably. It is wrong for Action. A cheap
    # draft rarely renders a specific verb, so a bakery theme composed
    # `kneading_dough` and the drafts came back `sitting, eating`; harvest
    # overwrote the slot and the finished prompt had the character eating bread
    # instead of baking it. The verb the theme named is the one aspect that
    # needs saying *again* precisely because the draft failed to show it.
    intent: bool = False


# Order matters: this is the order the prompt lines come out in, and the head of
# a prompt is where attention actually reaches.
SLOTS: tuple[Slot, ...] = (
    Slot("style", "Style", "global", 4, user_owned=True),
    # One sentence, in English, naming what the picture is. It comes before any
    # tag so the tags read as details of a thing rather than as a pile of
    # competing suggestions.
    # Person, not global. The sentence names who is in the picture and what she
    # is doing, so a background board that carries it renders her: one read
    # "A girl with blue hair is looking through a telescope on top of a hill"
    # in the positive while the negative said `1girl, solo, person`, and the
    # sentence won every time. The final prompt still gets it — merge assembles
    # every slot regardless of track, and `track` only decides board prompts.
    Slot(
        "description", "Description", "person", 1,
        guidance=(
            "ONE plain English sentence naming what this picture is — who is in "
            'it and what they are doing. Like "A bunny girl pilot is standing '
            'near an airplane." Not tags; a sentence.'
        ),
        intent=True,
    ),
    Slot(
        "character", "Character", "person", 8, locked=True,
        axes=("hair",), sets=("COUNT", "EYE_SHAPES"),
    ),
    # Not locked, and not a build. This is the part of her the situation puts
    # on show: soaked legs, bare shoulders, a sweating back. Locked, it took
    # only build words from the preset — and because `place_tag` never targets
    # a locked slot, every body part harvested off the drafts came back
    # homeless and was deleted at merge. A theme about wet legs reached the
    # render with no `legs` in it, and the picture made no sense.
    Slot(
        "body", "Body", "person", 10,
        sets=("BODY", "BODY_PARTS", "SKIN_FACE"),
        head_nouns=_BODY_PART_NOUNS, names=_BODY_STATES,
        # No query, so retrieval never fills this. Which part of her the picture
        # must show is decided by what happens TO her, and nothing about a theme
        # vector knows that: asked for "visible body parts, skin and their
        # condition" near a summer festival, the bank returned
        # `looking_through_legs`, `panties_around_one_leg` and `hands_on_feet`,
        # and a cap of ten gave them all a home. A theme that puts no part on
        # show should leave this line empty, and only the model can tell.
        query="",
        guidance=(
            "which parts of HER the theme puts on show, and what state they are "
            "in. Read the theme for the part it names: if it says her legs got "
            "soaked, write legs and wet_legs; if a strap slipped, write "
            "bare_shoulders. The picture cannot show a soaked leg it was never "
            "told about. Hair and clothes are not parts and have their own "
            "aspects. Her build is fixed elsewhere — never slim or petite"
        ),
        intent=True,
    ),
    Slot(
        "emotion", "Emotion", "person", 3,
        axes=("emotion",), sets=("EXPRESSION",),
        query="facial expression and mood of the character",
        # Whose feeling it is, is not something a 512px sketch gets a vote on.
        # The drafts came back `blush` for the patient, solitary observer and
        # overwrote the `patient, dreamy` the character block had produced —
        # undoing, at the last step, the whole point of reading her personality.
        intent=True,
        guidance=(
            "how she feels about being HERE, on her face. Read her traits: the "
            "same rained-on bus stop is misery to one character and a good "
            "excuse to another. Her usual expressions are listed above as a "
            "starting point, not an obligation"
        ),
        exclusive=True,
    ),
    Slot(
        "outfit", "Outfit", "person", 8,
        axes=("clothing",), sets=("CLOTHING_EXPLICIT",),
        query="clothing and garments with colours",
        guidance=(
            "what she is wearing, dressed for this theme. Name a COLOUR or a "
            "material on each garment — white_blouse, navy_pleated_skirt, denim "
            "jacket. A garment with no colour renders white every time, so take "
            "the colours from HERS where they suit the scene. Dress her in "
            "layers: a coat is also a collar, a sleeve and what is under it"
        ),
        # For the colours, and only for them. WD14 reports garments reliably
        # but generically: it read the indigo coat the board had rendered as
        # `coat`, and a plain garment always outscores its coloured variant
        # because it appears on far more images — so `black_coat` at 0.56 sat
        # below the budget line while `coat` at 0.81 sailed through, and the
        # prompt went back to colourless clothes that render white.
        intent=True,
    ),
    Slot(
        "action", "Action", "person", 4,
        axes=("action",), sets=("POSE",),
        query="pose, gesture and action",
        guidance=(
            "what she is doing with her body and hands HERE. A patient "
            "character waits; a loud one is already halfway somewhere. Her "
            "habits above say how she does things, they are not a list to copy "
            "— a girl who usually looks up at the sky is still waiting for a bus"
        ),
        intent=True,
    ),
    Slot(
        "accessories", "Accessories", "person", 5,
        sets=("ACCESSORIES", "PROPS"),
        query="worn accessories and held items",
        guidance=(
            "what she wears on top of her clothes, and what she holds. Look at "
            "what she LIKES: a character who likes thermos coffee has brought a "
            "thermos to the cold hilltop, and that one object says more about "
            "her than another coat would"
        ),
    ),
    Slot("shot", "Shot", "global", 4, user_owned=True),
    Slot(
        "place", "Place", "background", 4,
        axes=("location", "time_weather"),
        sets=("BACKGROUND", "ENVIRONMENT"),
        query="location, architecture, time of day and weather",
        guidance="where this happens — the room, the building, the hour, the weather",
        # The theme names the place; the drafts only report what a place looks
        # like. `bus_stop` is in no catalog, so the harvested copy went to
        # Object while Place filled with `outdoors, scenery` — the two vaguest
        # tags in the picture beating the one word the theme actually gave.
        intent=True,
    ),
    Slot(
        "object", "Object", "background", 5,
        sets=("PROPS",),
        query="objects and props sitting in the scene",
        guidance="things in the scene that nobody is holding",
    ),
    Slot(
        "light", "Light", "background", 3,
        axes=("visual",), sets=("VISUAL_LIGHTING",),
        query="lighting and atmosphere",
        guidance="the quality and direction of the light",
    ),
    Slot("effect", "Effect", "global", 5, user_owned=True),
)

BY_KEY: dict[str, Slot] = {s.key: s for s in SLOTS}

# Slots the model writes and the vocabulary tops up.
COMPOSED = tuple(s for s in SLOTS if not s.user_owned and not s.locked)
# Slots that come from the chosen character and must not be second-guessed.
LOCKED = tuple(s for s in SLOTS if s.locked)
# Slots the user owns outright.
USER = tuple(s for s in SLOTS if s.user_owned)


def slots_for(track: str) -> tuple[Slot, ...]:
    """The slots a board render of this track should carry."""
    return tuple(s for s in SLOTS if s.track in (track, "global"))


def accepts(slot: Slot, tag: str) -> bool:
    """Whether a harvested tag plausibly belongs in this slot.

    Deliberately permissive: this routes tags that already exist, it does not
    police them. A tag nothing accepts simply goes unplaced.
    """
    return accepts_listed(slot, tag) or _by_head_noun(slot, tag)


def accepts_listed(slot: Slot, tag: str) -> bool:
    """The confident half of ``accepts``: this tag is *listed* as belonging
    here, rather than guessed at from its last word."""
    name = str(tag or "").strip().lower().replace(" ", "_")
    if slot.axes and tag_catalog.get_tag_axis(name) in slot.axes:
        return True
    for set_name in slot.sets:
        member = getattr(tag_catalog, set_name, None)
        if member and name in member:
            return True
    return name in slot.names


def _by_head_noun(slot: Slot, tag: str) -> bool:
    """The guess: whatever this tag is a part of, it ends with."""
    name = str(tag or "").strip().lower().replace(" ", "_").replace("-", "_")
    return bool(name) and name.rsplit("_", 1)[-1] in slot.head_nouns


def place_tag(tag: str) -> str | None:
    """Which slot a loose tag belongs to, or None when nothing claims it.

    The catalog gets asked first and the head noun only afterwards, because the
    head noun is a guess and a guess must not outrank a fact. Body sits ahead of
    Action in the prompt, so a single pass let it take `crossed_legs` and
    `holding_own_foot` — poses the catalog files under Action, claimed on the
    strength of the last word in them.
    """
    routable = [s for s in SLOTS if not s.user_owned and not s.locked]
    for test in (accepts_listed, _by_head_noun):
        for slot in routable:
            if test(slot, tag):
                return slot.key
    return None




def is_framing(tag: str) -> bool:
    """Whether a tag says where the camera is standing.

    Muse chooses framing deliberately, in the user's Shot and Angle, because
    three drafts otherwise produce three framings and the merge keeps all of
    them. Such a tag belongs on the Shot line, never in the scene: a top-up
    offered `pov` and Object announced that a point of view was in the room.
    """
    name = str(tag or "").strip().lower().replace(" ", "_").replace("-", "_")
    return name in tag_catalog.COMPOSITION


def is_thing(tag: str) -> bool:
    """Whether a tag names an object that could sit in the scene.

    The catalog knows nothing about compound nouns — ``desk_lamp``,
    ``cooking_pot`` and ``neon_sign`` are all unrouted — so Object has to stay
    the fallback for whatever no slot claims, and that is right for those.

    It is wrong for the two kinds of tag that also arrive unrouted and are not
    objects at all. ``sweat`` is a detail on a body, and ``glowing`` is a
    quality of light. Both were chosen as reinforcements for a bakery scene and
    the prompt then read ``Object: glowing, sweat, cooking_pot`` — asserting
    that a glow and a sweat were sitting on the counter.
    """
    name = str(tag or "").strip().lower().replace(" ", "_")
    if not name:
        return False
    if name in tag_catalog.SKIN_FACE or name in tag_catalog.BODY_PARTS:
        return False
    if is_framing(name):
        return False
    # The catalog lists `thighs` but not `medium_breasts`, and Object took the
    # latter happily. What a tag is a *part of* is its last word, so test that
    # too: the modifier changes the size, not the category.
    if name.rsplit("_", 1)[-1] in _BODY_NOUNS:
        return False
    # A bare gerund is a quality or an act, never a noun in the room. Compounds
    # are exempt: `glowing_eyes` and `holding_book` are not this.
    if "_" not in name and name.endswith("ing"):
        return False
    return True


def _tokens(tag: str) -> set[str]:
    return {t for t in str(tag or "").lower().replace("-", "_").split("_") if len(t) >= 3}


def restates(tag: str, existing: list[str]) -> bool:
    """True when ``tag`` says again what something in ``existing`` already says."""
    tokens = _tokens(tag)
    if not tokens:
        return False
    return any(tokens & _tokens(other) for other in existing)


def dedupe_slot(tags: list[str], cap: int) -> list[str]:
    """Trim a slot to ``cap``, keeping the most specific of any restatement.

    ``bikini`` and ``black_bikini`` in the same slot are one fact written twice,
    and the budget should be spent on a second fact instead.

    Two tags restate each other when one's words *contain* the other's — a
    refinement — or when they contradict outright, meaning the same head noun
    modified from the same family. Nothing weaker will do. Sharing any word was
    the first attempt and it destroyed the character: given the identity
    ``1girl, blue_hair, very_long_hair, straight_hair, blue_eyes, slim`` it kept
    ``1girl, blue_hair, slim``, because hair length and hair style share "hair"
    with hair colour, and ``blue_eyes`` shares "blue". A girl may have very long
    straight blue hair and blue eyes; that is one character, not four ways of
    saying one thing. Identity drift is what the previous pipeline was abandoned
    over, and this reintroduced it.

    Which of two restatements survives matters more than it looks. Order comes
    from the harvest ranking, and a generic tag always wins that: ``shirt``
    appears on far more images than ``white_shirt``, so it scores higher and
    agrees across more drafts, and a first-wins rule dropped every colour the
    drafts had actually shown. Clothing with no colour comes out white — that
    was the cause. So the longer one wins the place: it says the same thing and
    one more fact besides.
    """
    kept: list[str] = []
    for tag in tags:
        name = str(tag or "").strip()
        if not name or any(k.lower() == name.lower() for k in kept):
            continue
        tokens = _tokens(name)
        replaced = False
        for i, existing in enumerate(kept):
            other = _tokens(existing)
            refines = bool(tokens & other) and (tokens > other or other > tokens)
            if not refines and not contradicts(name, existing):
                continue
            # Same thing said more precisely — take the place rather than a new one.
            if tokens > other:
                kept[i] = name
            replaced = True
            break
        if not replaced and len(kept) < cap:
            kept.append(name)
    return kept


def render_prompt(
    filled: dict[str, list[str]],
    *,
    texts: list[dict[str, str]] | None = None,
    prose: str = "",
) -> str:
    """The labelled prompt. Empty slots are left out rather than left blank.

    ``texts`` are literal strings to render in the image; ``prose`` is the
    closing restatement. Both sit after the tag lines, which is where they have
    been working by hand.

    The user's theme does not get a line. It is Japanese and the checkpoint is
    not; ``Description`` is its English form and says the same thing in a way
    the model can use.
    """
    lines: list[str] = []
    for slot in SLOTS:
        tags = [t for t in (filled.get(slot.key) or []) if t]
        if not tags:
            continue
        # Description is a sentence, not a comma list.
        joiner = " " if slot.key == "description" else ", "
        lines.append(f"{slot.label}: {joiner.join(tags)}")

    body = "\n".join(lines)
    for entry in texts or []:
        literal = str(entry.get("text") or "").strip()
        if not literal:
            continue
        where = str(entry.get("where") or "").strip()
        body += f'\n\ntext "{literal}"' + (f" on {where}" if where else "")
    if prose.strip():
        body += "\n\n" + prose.strip()
    return body


def flatten(filled: dict[str, list[str]]) -> list[str]:
    """Every placed tag, in slot order, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for slot in SLOTS:
        if slot.key == "theme":
            continue
        for tag in filled.get(slot.key) or []:
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                out.append(tag)
    return out

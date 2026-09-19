"""Conversation → final prompt path: boxes authority, atmosphere, VERIFY guard, pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from tests.muse import _shape
from app.muse import identity, notebook


def _solo_cast():
    return [{
        "name": "Mio",
        "identity_tags": ["silver_hair", "bob_cut", "blue_eyes"],
        "subject_tag": "1girl",
    }]


def _session_with_shot(**nb_patch):
    s = _shape.new_session({
        "theme": "t", "character_id": "c", "workflow": "w.json", "model": "m",
    })
    s["mode"] = "duet"
    s["character"] = {
        "name": "Mio",
        "identity_tags": ["silver_hair", "bob_cut", "blue_eyes"],
        "subject_tag": "1girl",
    }
    s["notebook"] = notebook.blank()
    notebook.apply_patch(notebook.of(s), nb_patch or {
        "atmosphere": "quiet, still",
        "scene": "school library, afternoon",
        "bg": "tall bookshelves, dusty sunlight",
        "light": "backlit from the window",
        "wearing": "light blue dress",
        "beat": "standing, holding a book",
        "expression": "soft smile",
    })
    s["craft"] = {}
    s["inputs"]["framing"] = "auto"
    return s


def test_atmosphere_reaches_frame_wide():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "scene": "classroom",
        "bg": "chalkboard",
        "light": "soft window light",
        "atmosphere": "quiet, expectant",
    })
    wide = notebook.frame_wide_phrases(nb)
    assert any("quiet" in p for p in wide)
    assert any("chalkboard" in p for p in wide)
    assert any("window" in p.lower() or "soft" in p for p in wide)


def test_fight_craft_scene_drops_conflicting_prose():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "scene": "school library",
        "wearing": "light blue dress",
        "beat": "standing",
    })
    # Mostly invents a different place/outfit — should drop.
    bad = (
        "She lounges on a neon rooftop in a red leather jacket under strobe lights, "
        "crowds cheering, fireworks exploding over the harbor skyline."
    )
    assert notebook.fight_craft_scene(nb, bad) == ""
    # Aligned prose survives.
    good = "Standing in the school library in a light blue dress."
    assert "library" in notebook.fight_craft_scene(nb, good).lower()


def test_solo_assemble_uses_person_boxes():
    """They will not write it without a box — a solo shoot builds its final prompt
    through the box path too."""
    out = identity.assemble_from_boxes(
        cast=_solo_cast(),
        people=notebook.mint_person_box(_session_with_shot()["notebook"]),
        frame_wide=notebook.frame_wide_phrases(_session_with_shot()["notebook"]),
        style="", framing="auto", scene="",
    )
    assert out
    assert "standing" in out.lower()
    assert "light blue dress" in out.lower() or "dress" in out.lower()
    assert "quiet" in out.lower() or "still" in out.lower()


def test_classify_fields_include_atmosphere():
    from app.muse import chain
    assert "atmosphere" in chain.CLASSIFY_FIELDS
    assert "bg" in chain.FIELD_CLERK_KINDS
    assert "light" in chain.FIELD_CLERK_KINDS
    assert "expression" in chain.FIELD_CLERK_KINDS
    assert "atmosphere" in chain._PER_PERSON




def test_the_prose_check_drops_sentences_not_the_whole_prose():
    """The prose loses **only the sentences that contradict the notebook head-on**.

    The first version threw the whole paragraph away when most content words were
    absent from the notebook. Over 13 real samples **4 (31%) were lost entirely**,
    and they were good prose:

        "A wide shot shows her sitting on a park bench, her weight settled
         back against the wood…"        52% unknown → dropped

    Prose is written in words the notebook does not have. What is inspected is
    **two axes only, clothes and place**, and any word present anywhere in the
    notebook is allowed (`oversized_hoodie` allows `hoodie`). After the fix the
    same 13 come through **13/13 untouched**.
    """
    from app.muse import notebook

    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "wearing": "oversized_hoodie, denim_skirt, black_tights, sneakers",
        "scene": "a park path, midday",
        "beat": "sitting on a bench, holding a book",
    })
    good = (
        "She sits on a bench with the book open in her lap. "
        "The oversized hoodie hangs loosely over her denim skirt and black tights."
    )
    assert notebook.fight_craft_scene(nb, good) == good

    # Only the sentence naming the removed garment is dropped. The sentences around
    # it survive.
    with_hat = good + " She slowly lowers a straw hat toward her hands."
    out = notebook.fight_craft_scene(nb, with_hat, struck={"straw_hat", "hat"})
    assert "straw hat" not in out
    assert "book open in her lap" in out and "denim skirt" in out

    # A sentence naming a place the notebook knows nothing about is dropped too.
    out = notebook.fight_craft_scene(nb, good + " She stands on a neon rooftop.")
    assert "rooftop" not in out and "book open in her lap" in out


def test_the_prose_keeps_its_paragraph_breaks():
    """In a two-person shoot the line breaks are what separate the two
    descriptions. They are not flattened."""
    from app.muse import notebook

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "wearing": "white blouse", "beat": "sitting on a bench",
        "wearing_b": "knit cardigan", "beat_b": "standing by the bench",
    })
    body = "She sits on the bench.\n\nHer partner stands beside her."
    assert notebook.fight_craft_scene(nb, body) == body


def test_an_unchanged_marker_never_reaches_the_picture():
    """Sometimes the marker is mixed into the value, and a version that only checks
    for an exact whole-field match lets it through.

    Live, the place clerk returned "that place, unchanged" and `unchanged` went
    straight into the picture.
    """
    import asyncio
    from app.muse import chain

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply

        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    got = asyncio.run(chain.read_per_person(
        _Ollama('{"各務 みお": "the school gate, unchanged"}'),
        kind="scene", note="校門の前に行こう。", name_a="各務 みお", name_b="",
        model="m", num_ctx=1024))
    assert got == {"scene": "the school gate"}

    # A value that is nothing but the signal writes nothing, as before.
    assert asyncio.run(chain.read_per_person(
        _Ollama('{"各務 みお": "unchanged"}'),
        kind="scene", note="いい感じ。", name_a="各務 みお", name_b="",
        model="m", num_ctx=1024)) == {}


def test_the_camera_box_reaches_the_picture():
    """**Focus belongs to the camera-work box.** A box you can write in is
    pointless if what you write does not arrive.

    The Showrunner (2026-09-01): "viewpoint is where Muse A and B are looking, so
    that naturally goes in beat. **Focus is probably camera work — `focus to …`
    and `long shot` belong in this box**."

    The text of `frame` was reaching nothing — all that showed in the picture was
    the single word `framing_tags` normalised to (`full_body` and the like).
    Writing `focus on Mio` into the notebook never arrived.

    **Put it at the head of the shared run.** The Showrunner: "priority is
    position within the prompt", and which of them the camera favours should bite
    before place or light.
    """
    from app.muse import notebook

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "frame": "medium shot, focus on Mio",
        "scene": "a park bench, afternoon",
        "bg": "trees",
        "light": "soft daylight",
        "atmosphere": "quiet",
    })
    wide = notebook.frame_wide_phrases(nb)
    assert "focus on Mio" in wide
    # The camera comes first, before the place.
    assert wide.index("medium shot") < wide.index("a park bench")


def test_the_gaze_belongs_to_each_person_now():
    """The gaze goes into each person's box (`beat`). **One shared field cannot
    hold two answers.**

    Live (`c9d83e6e`), 「すみれちゃんは後ろを向いて、遠くを見てて。みおちゃんは
    こっち見て」 ("Sumire, turn away and look into the distance; Mio, look at me")
    collapsed into a single `frame` and one of them disappeared.
    """
    from app.muse import chain, identity, notebook

    low = chain.build_scripter_system().lower()
    assert "eyes are beat's" in low
    assert "focus on" in low

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "beat": "sitting, looking at the camera",
        "beat_b": "standing, looking toward the distance",
        "wearing": "white blouse", "wearing_b": "knit cardigan",
        "frame": "medium shot, focus on Mio",
    })
    cast = [{"name": "Mio", "identity_tags": ["silver_hair", "bob_cut"]},
            {"name": "Sumire", "identity_tags": ["blonde_hair", "braid"]}]
    out = identity.assemble_from_boxes(
        cast=cast, people=notebook.mint_person_box(nb, partner=True),
        frame_wide=notebook.frame_wide_phrases(nb),
        style="anime_coloring", framing="auto", scene="",
    )
    lines = {l.split(":")[0].strip(): l for l in out.splitlines() if ":" in l}
    assert "looking at the camera" in lines["Mio"]
    assert "looking toward the distance" in lines["Sumire"]
    assert "looking toward the distance" not in lines["Mio"]
    assert "focus on Mio" in out


def test_a_person_is_never_background():
    """"Put Sumire in the background" is **about the camera**, not about the
    background field.

    Live (`47cb5f1c`, 2026-09-01) the focus moved correctly and this stayed behind
    in `bg`:

        frame: long shot, focus on both        ← correct
        bg   : **Mio close up, Sumire in background**

    In 「みおちゃんに寄って。すみれちゃんは背景でいいよ」 ("move in on Mio;
    Sumire can be in the background") **the words "in the background" were read as
    an instruction for the background field**. Even after pulling back to a wide,
    `Mio close up` stayed in the prompt, contradicting the focus outright.

    Measured (4 cases × 5 runs, hitting the clerk directly):

        move in on Mio; Sumire can be in the background   written 0/5  ✓
        now focus on Sumire; blur Mio                     written 0/5  ✓
        put a fountain in the background                  written 5/5  ✓
        let's add another bench behind them               written 5/5  ✓
    """
    from app.muse import chain

    bg = chain._PER_PERSON["bg"][2]
    # **Cut by kind, not by name.** It worked on 「すばるちゃんは背景で」 ("put
    # Subaru in the background"), and on 「**二人を小さく捉えて**、木々を多めに」
    # ("catch the two of them small, with plenty of trees") — a turn where the camera
    # and the background arrived on one line — 4 of 5 wrote `two people, park trees`
    # (live `98ab63a5`, 2026-09-02). Only the phrasing that names someone was being
    # closed.
    #
    # Measured (5 cases x 5 runs), people got in 0/5 in every case. 「木々を多めに」
    # ("plenty of trees") writes `many trees` and nothing else.
    assert "Never write a person here" in bg
    assert "two people" in bg, "言い換えを列挙しないと `two people` が通る"
    assert "FRAME" in bg, "行き先を言わないと、どこへ書けばよいか分からない"
    # The real background work is still there.
    assert "buildings behind her" in bg or "what ELSE is in the picture" in bg


def test_each_person_is_written_as_one_run():
    """**Write one person as one run.** Interleave them and the bodies mix.

    The order live (`d2a56ace`, 2026-09-02), and the picture it gave:

        Mio is …, flat_chest, slim,
        Subaru is …, large_breasts, tall,
        Mio: lying on the bench, …
        Subaru: standing near the bench, …

    **Each person appears twice, alternating**, so where one person ends is lost.
    In the picture Mio took on Subaru's chest, and Subaru's pose (standing) turned
    into sitting. The Showrunner: "it might be better as Mio danbooru / Mio prose
    / Subaru danbooru / Subaru prose".
    """
    from app.muse import identity, notebook

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "wearing": "professional_blouse, tailored_trousers",
        "beat": "lying on the bench, hands supporting head",
        "wearing_b": "knit_cardigan, long_skirt",
        "beat_b": "standing near the bench",
        "scene": "a park, daytime",
    })
    cast = [
        {"name": "Mio", "identity_tags": ["silver_hair", "flat_chest", "slim"]},
        {"name": "Subaru", "identity_tags": ["navy_hair", "large_breasts", "tall"]},
    ]
    out = identity.assemble_from_boxes(
        cast=cast, people=notebook.mint_person_box(nb, partner=True),
        frame_wide=notebook.frame_wide_phrases(nb),
        style="anime_coloring", framing="auto", scene="They share the bench.",
    )
    rows = [l for l in out.splitlines() if l.startswith(("Mio", "Subaru"))]
    # Mio's two lines run together, then Subaru's two. **Never alternating.**
    assert rows[0].startswith("Mio is ")
    assert rows[1].startswith("Mio: ")
    assert rows[2].startswith("Subaru is ")
    assert rows[3].startswith("Subaru: ")
    # The build appears only on its own person's line.
    assert "flat_chest" in rows[0] and "flat_chest" not in rows[2]
    assert "large_breasts" in rows[2] and "large_breasts" not in rows[0]


def test_the_compile_is_told_a_person_is_never_background():
    """People end up in the background field. The clerks had been told; **compile
    had not**.

    Live (`d2a56ace`): `bg: two people, park trees`. The same shape as the
    Showrunner's report that "the word 'Subaru' went into the background".
    """
    from app.muse import chain

    built = chain.build_scripter_system()
    assert "never a person" in built
    # The clerk has the same boundary (the wording moved to kind-based in `715b2b2`).
    assert "Never write a person here" in chain._PER_PERSON["bg"][2]


def test_a_japanese_name_never_leaves_the_clerk():
    """Names are replaced with their Latin spelling at the clerk's exit.

    Live (`2088299b`, 2026-09-02) the pose clerk wrote:

        beat_b: standing near the fountain, finger poking **みお's** cheek

    which goes straight into the picture prompt. The gate that drops person-name
    tags has been there a while (`_scrub_invented_tags`), but **it only covered
    tags; the text of a field went through untouched**.

    **Nothing was added to the contract.** "Write names in Latin script" was added
    and measured, and all three arms — contract on/off, gate on/off — came back
    **0/30** with no difference: something that appeared once live does not
    reproduce in 30 runs of the same line. **A clause whose effect cannot be
    measured does not go in** (the lesson of this studio's 8,281 → 2,327
    characters).

    The gate bites decisively. Not a probability — a guarantee.
    """
    from app.muse.chain import latin_names_in

    cast = [{"name": "Mio Kagami", "name_ja": "各務 みお"},
            {"name": "Subaru Asakura", "name_ja": "朝倉 すばる"}]
    assert latin_names_in("finger poking みお's cheek", cast) == (
        "finger poking Mio's cheek")
    # Surname alone and given name alone are replaced too — what appeared live was
    # 「みお」.
    assert latin_names_in("leaning toward 各務 みお, looking at 朝倉 すばる", cast) == (
        "leaning toward Mio, looking at Subaru")
    # A sentence with no name is not touched.
    plain = "sitting on a bench, hands in lap"
    assert latin_names_in(plain, cast) == plain
    # With no partner it does nothing (including turns where the Latin spellings do
    # not line up).
    assert latin_names_in("finger poking みお's cheek", None) == (
        "finger poking みお's cheek")



"""Notebook + scripter path for the lead shoot — live craft, no prep gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, notebook, shared
from tests.muse.test_service import FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)


def _tags(s):
    """Conversation-time picture: the notebook, not woven craft tags."""
    nb = s.get("notebook") or {}
    blob = " ".join(
        str(nb.get(k) or "")
        for k in (
            "wearing", "scene", "beat", "frame",
            "wearing_b", "beat_b", "atmosphere",
        )
    ).lower()
    extra: list[str] = []
    if "straw" in blob and "hat" in blob:
        extra.append("straw_hat")
    if "sailor" in blob:
        extra.append("sailor_collar")
    if "ramune" in blob:
        extra.append("ramune")
    if "yukata" in blob:
        extra.append("yukata")
    if "park" in blob:
        extra.append("park")
    if "beach" in blob:
        extra.append("beach")
    if "leaf" in blob:
        extra.append("leaf")
    if "look" in blob and "down" in blob:
        extra.append("looking_down")
    if "look" in blob and "up" in blob:
        extra.append("looking_up")
    if "below" in blob or "low angle" in blob or "low-angle" in blob:
        extra.extend(["from_below", "low_angle"])
    if "sit" in blob:
        extra.append("sitting")
    if "read" in blob:
        extra.append("reading")
    if "cardigan" in blob:
        extra.append("cardigan")
    if "white shirt" in blob or "white_shirt" in blob:
        extra.append("white_shirt")
    return blob.replace(" ", "_") + " " + " ".join(extra)


def _scripter_block(
    *, intent="shot", atmosphere="", scene="", frame="", wearing="", beat="",
    vibe="", tags="", craft_scene="", wearing_drop="",
):
    """One scripter reply. `wearing_drop` is part of the contract, not an extra:
    「wearing_drop = when something comes OFF, name that ONE garment」. A fake
    that rewrites WEARING without it is modelling a scripter that broke its own
    contract, which is not what a removal test should be asserting against."""
    return "\n".join([
        f"INTENT: {intent}",
        f"ATMOSPHERE: {atmosphere}" if atmosphere else "",
        f"SCENE: {scene}" if scene else "",
        f"FRAME: {frame}" if frame else "",
        f"WEARING: {wearing}" if wearing else "",
        f"WEARING_DROP: {wearing_drop}" if wearing_drop else "",
        f"BEAT: {beat}" if beat else "",
        f"VIBE: {vibe}" if vibe else "",
        "STANDING: none",
        "UNCHANGED: none",
        f"TAGS: {tags}" if tags else "TAGS: none",
        f"CRAFT_SCENE: {craft_scene}" if craft_scene else "CRAFT_SCENE: none",
    ])


def _current_note(prompt: str) -> str:
    """The instruction this scripter turn is answering, minus the transcript."""
    for marker in (
        "SHOWRUNNER'S LATEST LINE:",
        "総監督がいま言ったこと:",  # legacy marker
    ):
        head, sep, tail = str(prompt).partition(marker)
        if sep:
            return tail
    return str(prompt)


def _fake_clerk_reply(system: str, prompt: str) -> str | None:
    """Heuristic clerk answers for FakeOllama subclasses (fields / intent)."""
    system_l = str(system or "").lower()
    if "studio's clerk" not in system_l and "studios clerk" not in system_l:
        return None
    prompt_s = str(prompt or "")
    note = prompt_s
    for marker in ("DIRECTOR:", "DIRECTOR, in order:"):
        if marker in prompt_s:
            note = prompt_s.split(marker, 1)[-1]
            break
    note_l = note.lower()
    fields: list[str] = []
    if any(k in note for k in (
        "着", "脱", "帽子", "服", "セーラー", "コート", "羽織", "外し", "制服",
        "カーディガン", "麦わら", "uniform", "hat", "wear", "cardigan", "coat",
        "dress", "jacket", "shirt", "blouse",
    )):
        fields.append("wearing")
    if any(k in note for k in (
        "座", "立", "走", "手", "ポーズ", "しゃが", "持", "もた",
        "sit", "stand", "run", "hold", "kneel", "lean", "wave", "pose",
    )):
        fields.append("beat")
    if any(k in note for k in (
        "寄", "引", "画角", "全身", "アップ", "カメラ", "アングル",
        "close", "wide", "frame", "angle", "zoom", "full body", "upper",
    )):
        fields.append("frame")
    if any(k in note for k in (
        "場所", "公園", "ビーチ", "教室", "屋上", "夕方", "夜", "朝", "窓",
        "beach", "park", "rooftop", "classroom", "night", "dusk", "scene",
        "砂浜",
    )):
        fields.append("scene")
    if any(k in note for k in ("光", "逆光", "照明", "light", "backlight")):
        fields.append("light")
    if any(k in note for k in ("後ろ", "背景", "bg", "building", "crowd", "建物")):
        fields.append("bg")

    if "fields:" in prompt_s.lower() or "which parts of the shot" in system_l:
        return ", ".join(fields) if fields else "none"
    if "kind of turn" in system_l or prompt_s.rstrip().endswith("WORD:"):
        if fields:
            return "shot"
        if any(k in note_l for k in ("？", "?", "なに", "何", "いま", "今", "recall")):
            return "recall"
        return "casual"
    return None


class NotebookOllama(FakeOllama):
    """Keyword → scripter labelled block; Muse always says a short SAY."""

    def __init__(self, scripts=None):
        super().__init__()
        self.scripts = scripts or {}
        self.scripter_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        clerk = _fake_clerk_reply(system, str(prompt))
        if clerk is not None:
            text = clerk
        else:
            text = "SAY: うん、その感じ。"
            if "studio scripter" in system or "shot notebook" in system:
                prompt_s = str(prompt)
                self.scripter_prompts.append(prompt_s)
                # Match on the current instruction only. The prompt also carries the
                # conversation now, so matching the whole thing would let an earlier
                # turn's keyword answer a later turn. Longest keyword wins within
                # that line so 「また煽って、カーディガン」 ("low angle again, and the
                # cardigan") does not match a bare 「煽って」 ("low angle").
                note = _current_note(prompt_s)
                hits = [k for k in self.scripts if k in note]
                fold_keys = [k for k in self.scripts if "FOLD" in k]
                if fold_keys and "FOLD:" in prompt_s:
                    hits = [k for k in fold_keys if k in prompt_s] or fold_keys
                key = max(hits, key=len) if hits else ""
                text = self.scripts.get(key) or _scripter_block(
                    intent="casual", vibe="chatting",
                )

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


def test_parse_scripter_intent_and_patch():
    raw = _scripter_block(
        intent="mixed",
        wearing="sailor uniform, straw hat",
        beat="leaning on fence",
        frame="eye-level, looking at viewer",
        tags="sailor_collar, straw_hat, leaning, looking_at_viewer",
        craft_scene="She leans on a fence in a sailor uniform and straw hat.",
    )
    out = notebook.parse_scripter(raw)
    assert out["intent"] == "mixed"
    assert "straw hat" in out["patch"]["wearing"]
    assert "straw_hat" in out["tags"]
    assert "looking_at_viewer" in out["tags"]


def test_apply_patch_absolute_replace():
    nb = notebook.blank()
    notebook.apply_patch(nb, {"wearing": "jacket, skirt"})
    notebook.apply_patch(nb, {"wearing": "blouse, skirt"})
    assert nb["wearing"] == "blouse, skirt"
    assert nb["rev"] == 2


def test_bond_remembers_the_take_and_says_nothing_about_taste():
    """Bond is memory of the picture. What she LEARNED is a separate question.

    The taste card used to be derived from this same snapshot — the word "low"
    in `frame` taught her 「ローアングルの近い距離」 ("a low angle, close in")
    and the clothes she ended
    in became a preference. That describes the take, not anything the
    showrunner said about it. See `_learned_taste`.
    """
    s = {
        "continuity_snapshot": {
            "theme": "屋上",
            "notebook": {
                "atmosphere": "夕暮れの屋上",
                "vibe": "少し照れてる",
                "wearing": "セーラー",
                "frame": "low angle 煽り",
                "open": "",
            },
        },
        "standing": ["足は映さない"],
    }
    bond = shared._bond_from_snapshot(s)
    assert "夕暮れの屋上" in bond["last"]
    assert "セーラー" in bond["last"]
    assert bond["inside"] == "少し照れてる"


def test_the_partner_gets_her_own_forgotten_dress_back():
    """Measured (`94b4fc9f`, 2026-08-28): Sumire came out with no clothes at all.

    "Already present" is judged on overlapping words, so with Mio in a
    `light_blue_dress`, Sumire's `black cocktail dress` counts as covered because
    `dress` has been seen — **the second garment can never come back.**

    This was a defect that had been fixed once before. The old
    `_missing_wearing_tags` docstring recorded it as "clothes forgotten for the
    partner alone never come back".
    """
    bag = ("anime_illustration, close-up, park, dusk, standing, "
           "light_blue_dress, silk_fabric")
    out, (side_a, side_b) = notebook.reconcile_wardrobe_tags(
        bag, wearing="light_blue_dress, silk_fabric",
        wearing_b="black cocktail dress",
        sides=("standing, light_blue_dress, silk_fabric", "shrugging"),
        partner=True,
    )
    assert "black_cocktail_dress" in out
    assert "black_cocktail_dress" in side_b      # it goes on her side
    assert "black_cocktail_dress" not in side_a  # not on the lead


def test_the_lead_still_gets_hers_back_on_a_solo():
    bag = "anime_illustration, close-up, park, standing"
    out, _ = notebook.reconcile_wardrobe_tags(
        bag, wearing="straw_hat", sides=("", ""), partner=False)
    assert "straw_hat" in out






_A_WEAR = "light_blue_dress, blue_ribbon"
_B_WEAR = "black_cocktail_dress, pearl_necklace"
_BAG = ("light_blue_dress, blue_ribbon, black_cocktail_dress, "
        "pearl_necklace, park")


def test_both_prompts_say_which_letter_is_which_muse():
    """The first layer — one line binding the letters to the names. Only when a
    partner is present."""
    from app.muse.chain import _who_is_who
    assert _who_is_who("Mio", "Sumire", letters=True) == \
        "tags_a is Mio's. tags_b is Sumire's. Never cross them."
    assert "WEARING_B / BEAT_B are Sumire's" in \
        _who_is_who("Mio", "Sumire", letters=False)
    assert _who_is_who("Mio", "", letters=True) == ""


def test_the_wardrobe_clerk_maps_names_to_the_two_fields():
    """**A turn that says nothing but the clothes.** The Showrunner's idea
    (2026-08-29).

    The production compile (8,774 characters) mixes up the clothing fields in a
    duet — 2/20 measured, with `wearing` never written once (`ccde3c75`). Narrow
    the same question and ask it **by name** and it is 25/25. The shape is not
    broken; it is buried inside a large contract.

    What comes back is JSON keyed by name. **The mapping onto fields is decided
    here** — the model never picks the characters `_b`.
    """
    import asyncio

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply

        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    def _ask(reply):
        return asyncio.run(
            chain.read_wardrobe(
                _Ollama(reply), note="みおちゃんは赤いセーターで。",
                name_a="各務 みお", name_b="平岡 すみれ",
                model="m", num_ctx=1024,
            )
        )

    got = _ask('{"各務 みお": "red sweater, jeans", "平岡 すみれ": "unchanged"}')
    assert got == {"wearing": "red sweater, jeans"}

    # Both
    got = _ask('{"各務 みお": "red sweater", "平岡 すみれ": "white blouse"}')
    assert got == {"wearing": "red sweater", "wearing_b": "white blouse"}

    # An unreadable reply writes nothing — it never writes empty and erases clothes
    assert _ask("すみません、わかりません") == {}
    assert _ask('{"だれか": "x"}') == {}


def test_the_pose_clerk_uses_the_same_road():
    """The pose had exactly the same hole as the clothes.

    Measured (4 cases, n=3) the production compile scored **2/15**, `beat` was
    never written once, and even Mio's pose landed in `beat_b`. Asked by name it
    is **20/25** (and the one that missed was not a mix-up — it rendered
    「しゃがんで」, "crouch", as `kneeling`).
    """
    import asyncio

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply

        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    got = asyncio.run(chain.read_beats(
        _Ollama('{"各務 みお": "sitting on a bench", "平岡 すみれ": "standing behind her"}'),
        note="みおちゃんはベンチに座って。すみれちゃんは後ろに立ってて。",
        name_a="各務 みお", name_b="平岡 すみれ", model="m", num_ctx=1024))
    assert got == {"beat": "sitting on a bench",
                   "beat_b": "standing behind her"}
    # The set of fields does not collide with the clothes
    assert chain._PER_PERSON["beat"][0] == ("beat", "beat_b")
    assert chain._PER_PERSON["wearing"][0] == ("wearing", "wearing_b")


def test_the_wardrobe_clerk_also_works_alone():
    """**Asked for a solo shoot too.** For a long time this clerk only ran for two.

    The Showrunner (2026-08-30): "is it firing on a keyword? It needs to read the
    context and judge **whether what she has is being given up**."

    Measured (9 cases × 5 runs, `ask_solo_wear.py`; she is wearing a straw hat and
    four other things):

        asking about clothes alone   45/45
        the production compile       36/45

    What missed were only the indirect removals — 「その帽子、ちょっと違うかも」
    ("that hat might not be quite right") 1/5, 「帽子、今日は合わないね」 ("the
    hat does not suit today") 0/5. Neither matched `_STRIKE_NOTE_RE` either —
    phrasings that **caught on nothing anywhere**.
    """
    import asyncio

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply
            self.prompts: list[str] = []

        def generate_text_stream(self, prompt, **kw):
            self.prompts.append(str(prompt))

            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    oc = _Ollama('{"各務 みお": "oversized hoodie, denim skirt, sneakers"}')
    got = asyncio.run(chain.read_wardrobe(
        oc, note="その帽子、ちょっと違うかも。", name_a="各務 みお", name_b="",
        wearing="straw hat, oversized hoodie, denim skirt, sneakers",
        model="m", num_ctx=1024))
    # Only the hat is dropped and the rest stays — the "say all of it" contract at
    # work.
    assert got == {"wearing": "oversized hoodie, denim skirt, sneakers"}
    # The partner's fields are not touched.
    assert "wearing_b" not in got
    # A solo question does not name a partner who is not there.
    assert "平岡" not in oc.prompts[0]

    # An unreadable reply writes nothing — it never writes empty and erases clothes.
    assert asyncio.run(chain.read_wardrobe(
        _Ollama("すみません、わかりません"), note="x",
        name_a="各務 みお", name_b="", model="m", num_ctx=1024)) == {}


def test_every_field_clerk_that_runs_alone_has_been_measured():
    """A field with a solo contract is a field that **has been measured**.

    Measured (9 cases × 5 runs, `ask_field_clerks.py`, 2026-08-31):

        wearing  45/45 vs 36/45
        beat     45/45 vs 32/45   ← 「立って。」 ("stand up") scores 1/5 on compile
        scene    45/45 vs 24/45   ← a line that moves the place, 0-1/5 on compile

    A field whose solo contract is empty means "not measured yet", so it does not
    run.
    """
    import asyncio

    for kind in ("wearing", "beat", "scene"):
        assert chain._PER_PERSON[kind][2], f"{kind} に一人ぶんの条文が無い"
    # The place is shared by both, so there is no question that splits it by name —
    # it has no two-person contract.
    assert chain._PER_PERSON["scene"][1] == ""
    assert chain._PER_PERSON["beat"][1] != ""
    # Unreadable means nothing is written (a `None` does not break it).
    assert asyncio.run(chain.read_per_person(
        None, kind="scene", note="", name_a="各務 みお", name_b="",
        model="m", num_ctx=1024)) == {}




_SETS_A = [
    {"key": "signature", "name_ja": "いつもの", "tags": ["blouse"], "props": ["pen"]},
    {"key": "casual_a", "name_ja": "休みの日", "tags": ["hoodie"], "props": ["tote_bag"]},
]
_SETS_B = [
    {"key": "signature", "name_ja": "いつもの", "tags": ["apron"], "props": ["shears"]},
    {"key": "casual_b", "name_ja": "よそ行き", "tags": ["black_dress"], "props": ["clutch"]},
]


class _PickOllama:
    def __init__(self, reply):
        self.reply = reply

    def generate_text_stream(self, prompt, **kw):
        async def _stream():
            yield {"type": "token", "text": self.reply}
        return _stream()















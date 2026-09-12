"""Notebook + scripter path for 主演撮り — live craft, no prep gate."""
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
                # that line so "また煽って、カーディガン" does not match a bare "煽って".
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
    in `frame` taught her 「ローアングルの近い距離」 and the clothes she ended
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
    """実測（`94b4fc9f`・2026-08-28）: すみれが服ひとつ無しで出た。

    「もうある」判定は語のかぶりで見るので、みおが `light_blue_dress` を着て
    いると、すみれの `black cocktail dress` は `dress` が既出という理由で
    足りていると判定される —— **二着目が絶対に戻らない。**

    これは一度直してあった不具合。旧 `_missing_wearing_tags` の docstring が
    「相方だけ忘れた服が戻らない」と記録していた。
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
    assert "black_cocktail_dress" in side_b      # 彼女の側に付く
    assert "black_cocktail_dress" not in side_a  # 主演には付かない


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
    """第一層 —— 文字と名前を結ぶ一行。相方がいるときだけ出す。"""
    from app.muse.chain import _who_is_who
    assert _who_is_who("Mio", "Sumire", letters=True) == \
        "tags_a is Mio's. tags_b is Sumire's. Never cross them."
    assert "WEARING_B / BEAT_B are Sumire's" in \
        _who_is_who("Mio", "Sumire", letters=False)
    assert _who_is_who("Mio", "", letters=True) == ""


def test_the_wardrobe_clerk_maps_names_to_the_two_fields():
    """**服だけを言うターン。** 総監督（2026-08-29）の案。

    本番の compile（8,774字）は W で服の欄を取り違える —— 実測 2/20 で、
    `wearing` が一度も書かれなかった（`ccde3c75`）。同じ問いを小さく絞って
    **名前で**訊くと 25/25。形が壊れているのではなく、大きな条文の中で
    埋もれている。

    返りは名前をキーにした JSON。**欄への振り分けはこちらで決める** ——
    モデルに `_b` という文字を選ばせない。
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

    # 両方
    got = _ask('{"各務 みお": "red sweater", "平岡 すみれ": "white blouse"}')
    assert got == {"wearing": "red sweater", "wearing_b": "white blouse"}

    # 読めない返しは何も書かない —— 空を書いて服を消さない
    assert _ask("すみません、わかりません") == {}
    assert _ask('{"だれか": "x"}') == {}


def test_the_pose_clerk_uses_the_same_road():
    """姿勢も服とまったく同じ穴だった。

    実測（4件・n=3）で本番の compile は **2/15**、`beat` は一度も書かれず、
    みおの姿勢まで `beat_b` に入った。名前で訊くと **20/25**（落ちた1件も
    取り違えではなく、「しゃがんで」を `kneeling` と訳しただけ）。
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
    # 欄の組が服とぶつからないこと
    assert chain._PER_PERSON["beat"][0] == ("beat", "beat_b")
    assert chain._PER_PERSON["wearing"][0] == ("wearing", "wearing_b")


def test_the_wardrobe_clerk_also_works_alone():
    """**一人でも訊く。** 長らく二人のときしか走らなかった係。

    総監督（2026-08-30）「キーワードが出てきたら発動ってなってるのでは？
    文脈を見て**今持っているのは手放したか**の判定がいる」。

    実測（9件×5回・`ask_solo_wear.py`。いま着ているのは麦わら帽子ほか5点）:

        服だけを訊く      45/45
        本番の compile    36/45

    落ちたのは遠回しな外し方だけ —— 「その帽子、ちょっと違うかも」1/5、
    「帽子、今日は合わないね」0/5。どちらも `_STRIKE_NOTE_RE` にも当たらず、
    **どこにも引っかからなかった**言い方。
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
    # 帽子だけが落ちて、残りはそのまま —— 「全部言う」条文の効き目。
    assert got == {"wearing": "oversized hoodie, denim skirt, sneakers"}
    # 相方の欄には触らない。
    assert "wearing_b" not in got
    # 一人ぶんの問いに、いない相方の名前を出さない。
    assert "平岡" not in oc.prompts[0]

    # 読めない返しは何も書かない —— 空を書いて服を消さない。
    assert asyncio.run(chain.read_wardrobe(
        _Ollama("すみません、わかりません"), note="x",
        name_a="各務 みお", name_b="", model="m", num_ctx=1024)) == {}


def test_every_field_clerk_that_runs_alone_has_been_measured():
    """一人ぶんの条文がある欄＝**測ってある**欄。

    実測（9件×5回・`ask_field_clerks.py`・2026-08-31）:

        wearing  45/45 対 36/45
        beat     45/45 対 32/45   ← 「立って。」は compile 1/5
        scene    45/45 対 24/45   ← 場所を移す一行は compile 0〜1/5

    一人ぶんの条文が空である欄は「まだ測っていない」という意味なので、
    走らせない。
    """
    import asyncio

    for kind in ("wearing", "beat", "scene"):
        assert chain._PER_PERSON[kind][2], f"{kind} に一人ぶんの条文が無い"
    # 場所は二人で共有するので、名前で分ける問いにならない —— 二人ぶんの
    # 条文は持たない。
    assert chain._PER_PERSON["scene"][1] == ""
    assert chain._PER_PERSON["beat"][1] != ""
    # 読めなければ何も書かない（`None` でも落ちない）。
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















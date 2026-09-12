"""Absolute shot ledger for Muse Refine.

One document. Missing keys leave the previous value. Present keys replace.
"""
from __future__ import annotations

import re
from typing import Any

LEDGER_KEYS: tuple[str, ...] = (
    "wearing",
    "beat",
    "expression",
    "scene",
    "light",
    "bg",
    "frame",
    "wearing_b",
    "beat_b",
    # **相方にも顔を（2026-09-10）。** 総監督のW撮りで、絵に相方の表情が一切
    # 入っていなかった（`Mio: … bright smile` に対し `Asahi:` は顔無し）。
    # classic のノートには最初から `expression_b` がある。
    "expression_b",
    "lettering",  # short Latin words for Anima text "…" / text_on_image
    "atmosphere",  # mood / air — conversation-driven (wistful, tense, cozy…)
    "look",  # art direction / render — cel, fantasy, watercolor… (not UI buttons)
)

# Survive long chats: LLMs love to "helpfully" clear or rewrite these.
# Empty string only clears when the director explicitly allows it (reset cue).
STICKY_KEYS: frozenset[str] = frozenset({"atmosphere", "look", "lettering"})

# All shot axes resist accidental empty clears from writer / muse / repair.
RESIST_EMPTY_CLEAR: frozenset[str] = frozenset(LEDGER_KEYS)


DROP_KEYS: tuple[str, ...] = ("wearing_drop",)

# UI chips — short label + icon glyph (not used for model judgment).
FIELD_CHIPS: dict[str, dict[str, str]] = {
    "wearing": {"icon": "👕", "ja": "服", "en": "Clothes"},
    "beat": {"icon": "🧍", "ja": "姿勢", "en": "Pose"},
    "expression": {"icon": "😊", "ja": "表情", "en": "Face"},
    "scene": {"icon": "📍", "ja": "場所", "en": "Place"},
    "light": {"icon": "💡", "ja": "光", "en": "Light"},
    "bg": {"icon": "🏞", "ja": "背景", "en": "BG"},
    "frame": {"icon": "📷", "ja": "構図", "en": "Frame"},
    "wearing_b": {"icon": "👗", "ja": "相方服", "en": "Partner clothes"},
    "beat_b": {"icon": "🤝", "ja": "相方姿勢", "en": "Partner pose"},
    "expression_b": {"icon": "🙂", "ja": "相方表情", "en": "Partner face"},
    "lettering": {"icon": "🔤", "ja": "文字", "en": "Lettering"},
    "atmosphere": {"icon": "🌫", "ja": "雰囲気", "en": "Mood"},
    "look": {"icon": "🎨", "ja": "画風", "en": "Look"},
    "wearing_drop": {"icon": "🗑", "ja": "脱ぐ", "en": "Drop"},
}

# Soft cue that the director line is about the picture (retry writer if empty).
_PICTURE_CUES = re.compile(
    r"("
    r"着|服|シャツ|スカート|ワンピース|制服|パーカー|コート|帽子|靴|脱|"
    r"立|座|跪|寝|ポーズ|姿勢|表情|笑|泣|"
    r"場所|屋上|教室|公園|海|部屋|背景|光|照明|カメラ|構図|寄|引き|"
    r"看板|文字|テキスト|ボード|"
    r"雰囲気|空気|ムード|画風|タッチ|塗り|ファンタジー|エモ|セル|"
    r"wear|shirt|skirt|dress|uniform|hoodie|coat|hat|pose|stand|sit|"
    r"rooftop|classroom|park|beach|room|background|light|camera|frame|outfit|"
    r"sign|banner|lettering|textboard|atmosphere|mood|fantasy|watercolor|cel"
    r")",
    re.I,
)


def blank() -> dict[str, str]:
    return {k: "" for k in LEDGER_KEYS}


#: 欄の値の頭に付いてくるラベル。**台帳の値が、欄の名前で始まってはいけない。**
#:
#: 実機（2026-09-11）で `wearing` にこう着いた:
#:
#:     wearing: "BEAT: standing by the railing, silhouette against the sun"
#:     bg:      "ATMOSPHERE:"
#:
#: 出どころは一つではない —— 女優の CARD（`persona.card_to_patch`）も、班の席の
#: CRAFT も、writer の JSON も、どれもラベルを頭に付けてくることがある。
#: **入口は `normalize_patch` 一つ**なので、ここで落とす。
_LABEL_HEAD_RE = re.compile(
    r"^\s*(?:PLACE|HOUR|SCENE|WEARING(?:_B)?|BEAT(?:_B)?|EXPRESSION(?:_B)?|"
    r"FACE(?:_B)?|FRAME|LIGHT|BG|BACKGROUND|ATMOSPHERE|MOOD|LOOK|STYLE|"
    r"LETTERING|TEXT|CLOTH|BODY|OPTICS|COLOUR|PROPS|AIR|SHAPE|RENDER|FINISH|"
    r"TAGS|CRAFT)\s*[:：]\s*",
    re.I,
)


def strip_field_label(value: str) -> str:
    """値の頭から欄名を剥がす。重なっていても剥がす（`WEARING: BEAT: …`）。

    **欄名に似ているだけの語は残す** —— `atmospheric, dusty` はコロンが無いので
    触らない。剥がした結果が空になったら、その値は捨てられる（呼び出し側で）。
    """
    text = str(value or "")
    for _ in range(4):
        stripped = _LABEL_HEAD_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text.strip()


# ── 一つの体は一つの答えしか持たない ────────────────────────────────────────
#
# **18席が同じ欄に順に書くので、言い換えと矛盾が積もる。** 実機（2026-09-12・
# 「今日もメイドさんで」の再現）で `beat` が 13語になり、こうなっていた:
#
#     weight on right leg      ↔  weight on back foot      体重が二箇所
#     hips jutting out sharply ↔  hips pushed forward      同じことを二度
#     hands_clutching_tray_edge ↔ hugging tray             同じことを三度
#     （前ターンでは hands releasing ↔ hands_clutching ↔ hands steadying）
#
# 同じ記録の元のセッションは8語で、**部位ごとに一語ずつ**だった
# （`standing, weight_on_front_foot, one_hand_on_hip, other_arm_holding_tray_at_waist…`）。
#
# `tags.conflict.SLOTS` は当たらない —— あれは danbooru の**正確な語**の表で、
# ここに来るのは自由文（`weight on right leg`）。語の表を増やす話ではない。
#
# **軸で見る。** 一つの軸（体重・腰の向き・頭の向き・手の掴み）に二つ目の答えが
# 来たら落とす。反対の答え（矛盾）でも同じ答え（言い換え）でも、どちらも落とす ——
# 絵にとってはどちらも雑音だから。**最初の答えが勝つ**のは `facets` と同じ規則で、
# 実測でも監督の一言が先に来ていた（writer は指示を書いてから席の細部を足す）。
#
# 部位を名指ししただけでは落とさない。**手は二本ある** ——
# `left hand on hip` と `other_arm_holding_tray` は両方立つ。だから軸は
# 「部位＋向き」で、向きの語が無い句はどの軸にも乗らない
# （`tags/conflict.py` 冒頭の「取りすぎのほうが高くつく」と同じ判断）。
_BODY_AXES: tuple[tuple[str, tuple[str, ...], dict[str, tuple[str, ...]]], ...] = (
    # 軸の名前, 部位の語, 向き → その向きを表す語
    ("weight", ("weight", "leaning", "balance"), {
        "front": ("front", "forward", "fore"),
        "back": ("back", "rear", "behind", "heel"),
        "left": ("left",),
        "right": ("right",),
        "both": ("both", "even", "evenly", "center", "centre"),
    }),
    ("hips", ("hip", "hips", "pelvis", "waist"), {
        "out": ("forward", "out", "jutting", "jut", "thrust", "thrusting",
                "pushed", "push", "pushing", "ahead"),
        "in": ("back", "retracted", "retract", "pulled", "pull", "tucked",
               "tuck", "drawn"),
    }),
    ("head", ("head", "chin", "face", "gaze direction", "neck"), {
        "up": ("up", "lifted", "lift", "raised", "raise", "tilted_up", "upward"),
        "down": ("down", "lowered", "lower", "dropped", "downward", "tucked"),
        "to_camera": ("toward camera", "to camera", "at the camera", "at viewer",
                      "toward the viewer", "turned toward camera", "facing camera"),
        "away": ("away", "aside", "over her shoulder", "to the side"),
    }),
    # **部位の語を要らない軸。** 掴んでいるのは定義上その手なので、
    # `hugging tray`（手の字が無い）も同じ軸に乗る。空の組がその印。
    ("hold", (), {
        # **支える言い方も掴み（2026-09-12 の実機）。** `right arm holding tray`
        # と `forearm_supporting_tray` が二重で残った —— `supporting` を
        # 入れていなかったので軸に乗らなかった。
        "hold": ("holding", "hold", "clutching", "clutch", "gripping", "grip",
                 "grasping", "grasp", "steadying", "steady", "hugging", "hug",
                 "carrying", "carry", "clasping", "clasp", "supporting",
                 "support", "cradling", "cradle", "propping", "balancing"),
        "free": ("releasing", "release", "letting go", "let go", "lowering",
                 "setting down", "putting down", "open palms", "empty"),
    }),
)


#: 掴みの軸で「何を」掴んでいるかを取り出すときに落とす語 —— 動詞・体の部位・
#: 助詞・様子の形容。残るのが**物**。`holding tray` と `holding coffee cup` は
#: 別の物なので**両方立つ**（手は二本ある）。`holding tray` と
#: `holding order_tray` は同じ物なので言い換え。
_NOT_THE_OBJECT = frozenset({
    "hand", "hands", "finger", "fingers", "palm", "palms", "arm", "arms",
    "forearm", "forearms", "elbow", "elbows", "knuckle", "knuckles", "wrist",
    "on", "at", "the", "a", "an", "with", "to", "of", "in", "into", "onto",
    "her", "his", "its", "own", "both", "one", "other", "and",
    "white", "tight", "tightly", "tense", "tensed", "trembling", "slightly",
    "gently", "firmly", "sharply", "lightly", "barely", "still",
})


def _axis_of(phrase: str) -> tuple[str, str, frozenset[str]] | None:
    """この句が答えている軸・その答え・軸の中の鍵。乗らないなら `None`。

    鍵は掴みの軸だけで意味を持つ —— **掴んでいる物**。物が違えば同じ軸でも
    別の答えとして両方立つ。ほかの軸（体重・腰・頭）は体に一つしかないので
    鍵は空。
    """
    words = re.sub(r"[_\-]+", " ", phrase.lower())
    for axis, parts, answers in _BODY_AXES:
        if parts and not any(
            re.search(rf"(?<![a-z]){re.escape(part)}(?![a-z])", words)
            for part in parts
        ):
            continue
        for answer, cues in answers.items():
            for cue in cues:
                if not re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", words):
                    continue
                if axis != "hold":
                    return axis, answer, frozenset()
                spent = {w for a in answers.values() for c in a for w in c.split()}
                obj = {
                    w for w in re.findall(r"[a-z]+", words)
                    if w not in spent and w not in _NOT_THE_OBJECT
                }
                return axis, answer, frozenset(obj)
    return None


def one_body(value: str) -> tuple[str, list[str]]:
    """`beat` を一つの体に畳む。残した句と、落とした句を返す。

    落とすのは**同じ軸の二つ目**だけ。部位を名指ししただけの句や、向きの語が
    無い句（`arms_stiff` / `forearms tensed` / `standing`）には触らない。
    掴みの軸は**物ごと**に数えるので、トレイとカップは両方立つ。

    完全に同じ句と、他の句に語として含まれてしまう句（`hand on hip` は
    `left hand on hip` の中にある）も落とす —— 同じことを二度言っている。
    """
    parts = [p.strip() for p in str(value or "").replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    kept: list[str] = []
    dropped: list[str] = []
    seen: dict[str, list[frozenset[str]]] = {}
    for phrase in parts:
        axis = _axis_of(phrase)
        if axis is not None:
            name, _answer, key = axis
            before = seen.setdefault(name, [])
            if name == "hold":
                # 物が重なっていたら同じ物の話 —— 言い換えでも反対でも落とす。
                # 物が読めなかった句（`open palms`）は、既にある掴みに合流する。
                if any(not key or not k or (key & k) for k in before):
                    dropped.append(phrase)
                    continue
            elif before:
                dropped.append(phrase)
                continue
            before.append(key)
        low = re.sub(r"[_\-]+", " ", phrase.lower()).strip()
        if any(low in re.sub(r"[_\-]+", " ", k.lower()) for k in kept):
            dropped.append(phrase)
            continue
        kept.append(phrase)
    return ", ".join(kept), dropped


def normalize_patch(
    raw: dict[str, Any] | None,
    *,
    report: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Keep only known keys; coerce to stripped strings.

    値の頭に付いた欄名もここで落とす —— 台帳への入口はここ一つ。
    """
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in (*LEDGER_KEYS, *DROP_KEYS):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None:
            continue
        text = strip_field_label(str(val).strip())
        if key in BODY_KEYS and text:
            # **一つの体に畳む。** 入口はここ一つなので、席の経路でも
            # カードの経路でも同じように効く（欄名を落とすのと同じ判断）。
            text, gone = one_body(text)
            if gone and report is not None:
                # 黙って捨てない —— 落とした句は呼び元が記録に残せる。
                report.setdefault(key, []).extend(gone)
        if key == "wearing_drop" and not text:
            continue
        out[key] = text
    return out


def apply_patch(ledger: dict[str, str], patch: dict[str, str]) -> dict[str, str]:
    """Absolute merge. Empty string clears the field when present in patch."""
    next_ledger = {**blank(), **{k: str(ledger.get(k) or "") for k in LEDGER_KEYS}}
    drop = str(patch.get("wearing_drop") or "").strip().lower()
    if drop:
        # **語の境目で照合する（2026-09-09）。** 部分一致だと `shirt` を脱いだ
        # ときに `skirt` まで消えた（実測・純関数）。`talk.word_hit` が唯一の
        # 規則で、禁止フィルタと同じものを使う。
        from .talk import word_hit

        wearing = next_ledger.get("wearing") or ""
        parts = [p.strip() for p in wearing.replace(";", ",").split(",") if p.strip()]
        kept = [p for p in parts if not word_hit(drop, p)]
        next_ledger["wearing"] = ", ".join(kept)
    for key in LEDGER_KEYS:
        if key not in patch:
            continue
        next_ledger[key] = str(patch[key]).strip()
    return next_ledger


def _posture_of(value: str) -> str:
    """この体が名指している姿勢（`standing` / `sitting` …）。無ければ空。

    語の表は `tags.conflict` の `posture` 槽をそのまま使う —— 姿勢の語は
    danbooru の正確な語で来るので、あちらが当たる（自由文の向きとは違う）。
    二つ持つと必ずずれるので、ここで列を作らない。
    """
    from ..tags import conflict

    for phrase in str(value or "").replace(";", ",").split(","):
        for word in re.findall(r"[A-Za-z_]+", phrase):
            if conflict.slot_of(word) == "posture":
                return word.lower()
    return ""


def keep_the_posture(new: str, before: str) -> str:
    """姿勢を名指し忘れた体に、前の姿勢を戻す。

    **欄は丸ごと書き直す所なので、書かれなかったものは消える。** 実測
    （2026-09-12・台で A/B）: 「一つの体」の条文を足すと矛盾は消えたが、
    同じ回で `standing` が落ちた —— 台本係が「既に分かっていること」として
    省いた。条文の言い回しでは戻らなかったので、ここで守る。

    戻すのは**新しい体が姿勢を一つも名指していないとき**だけ。名指していれば
    そちらが正しい（「座って」と言われた回を立たせない）。
    """
    if not str(new or "").strip():
        return new
    if _posture_of(new):
        return new
    was = _posture_of(before)
    if not was:
        return new
    return f"{was}, {new}"


def scrub_patch(
    patch: dict[str, str] | None,
    ledger: dict[str, str] | None,
    *,
    allow_clear: set[str] | frozenset[str] | None = None,
    report: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Drop accidental empty clears so long chats keep clothes / mood / look.

    Empty string still clears when ``allow_clear`` names the key (explicit reset).
    """
    raw = normalize_patch(patch, report=report)
    if not raw:
        return {}
    allow = set(allow_clear or ())
    cur = {**blank(), **(ledger or {})}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key == "wearing_drop":
            if val:
                out[key] = val
            continue
        if (
            val == ""
            and key in RESIST_EMPTY_CLEAR
            and str(cur.get(key) or "").strip()
            and key not in allow
        ):
            continue
        if key in BODY_KEYS and val:
            # 姿勢を名指し忘れたら前の姿勢を戻す（`keep_the_posture`）。
            val = keep_the_posture(val, str(cur.get(key) or ""))
        out[key] = val
    return out


def guard_sticky_writes(
    patch: dict[str, str] | None,
    *,
    allowed: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    """Muse/repair may not invent atmosphere/look/lettering changes.

    Only keys the director (writer + cue) touched this turn may rewrite sticky
    fields. Omitting a sticky key always keeps the previous value.
    """
    raw = dict(patch or {})
    if not raw:
        return {}
    allow = set(allowed or ())
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key in STICKY_KEYS and key not in allow:
            continue
        out[key] = val
    return out


def guard_muse_propose(
    patch: dict[str, str] | None,
    ledger: dict[str, str] | None,
    *,
    director_keys: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    """Classic Muse fold spirit: actress does not overwrite a settled shot.

    Director patches are already applied before muse runs. Muse may **fill empty**
    ledger slots (e.g. missing beat_b). Clothes / place / camera stay fill-empty
    only — ``director_keys`` never grants overwrite there (verified: muse used to
    replace the director's wearing on the same turn).

    Exception — **expression** (performance / face):
    The actress owns the face for the photograph when the director did not name
    a face this turn. She may fill an empty expression always, and may refresh a
    settled face when scene-ish axes just moved (scene / atmosphere / beat /
    light / frame / bg) so the expression can track the shot.

    **W撮りでは顔は二つある（2026-09-10）。** 彼女は二人ぶんを演じているので、
    `expression_b` も同じ演技の軸として扱う —— 相方の顔だけ台帳に据え置かれる
    と、場面が動いても相方の表情が置き去りになる。監督がその回に顔を名指し
    したかどうかも、欄ごとに見る（`expression` を指定した回に `expression_b`
    まで凍らせない）。
    """
    raw = dict(patch or {})
    if not raw:
        return {}
    dir_keys = set(director_keys or ())
    scene_moved = bool(dir_keys & {"scene", "atmosphere", "beat", "light", "frame", "bg"})
    cur = {**blank(), **(ledger or {})}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key in STICKY_KEYS or key == "wearing_drop":
            continue
        text = str(val or "").strip()
        if not text:
            continue
        cur_val = str(cur.get(key) or "").strip()
        if not cur_val:
            out[key] = text
            continue
        # Performance axis: scene-matched face when director left face alone.
        # 顔は二つある（W撮り）。欄ごとに、監督がその欄を触ったかで見る。
        if key in ("expression", "expression_b") and key not in dir_keys and scene_moved:
            if text.lower() != cur_val.lower():
                out[key] = text
    return out


def changed_fields(before: dict[str, str], after: dict[str, str]) -> list[str]:
    out: list[str] = []
    for key in LEDGER_KEYS:
        if str(before.get(key) or "").strip() != str(after.get(key) or "").strip():
            out.append(key)
    return out


def patch_fields(patch: dict[str, str]) -> list[str]:
    keys = [k for k in LEDGER_KEYS if str(patch.get(k) or "").strip() or k in patch]
    # Only keys actually present in patch.
    keys = [k for k in LEDGER_KEYS if k in patch]
    if str(patch.get("wearing_drop") or "").strip():
        keys.append("wearing_drop")
    return keys


def chips_for(fields: list[str], *, locale: str = "ja") -> list[dict[str, str]]:
    ja = locale.startswith("ja")
    chips: list[dict[str, str]] = []
    for key in fields:
        meta = FIELD_CHIPS.get(key)
        if not meta:
            continue
        chips.append({
            "key": key,
            "icon": meta["icon"],
            "label": meta["ja"] if ja else meta["en"],
        })
    return chips


#: 二人目の欄。一人しかいない撮影では**見せない**。
PARTNER_KEYS: tuple[str, ...] = ("wearing_b", "beat_b", "expression_b")

#: 体の姿勢の欄。二人ぶんある（`one_body` を掛ける先）。
BODY_KEYS: tuple[str, ...] = ("beat", "beat_b")


def for_model(ledger: dict[str, str], *, partner: bool) -> dict[str, str]:
    """模型に見せる台帳。**一人のときは二人目の欄を落とす。**

    総監督（2026-09-09）「一人しかいないときに muse_b の tag を編集して
    しまう。**1人か2人の区別の説明が足りていない**」。

    条文には「partner Muse が居るときだけ `wearing_b` / `beat_b` を書く」と
    最初から書いてあった。足りなかったのは**居るかどうかを伝えること** ——
    `blank()` が全欄を埋めるので、模型には常に二人目の欄が空で見えていた。
    空欄は「埋めろ」に見える。

    **箱を出さなければ入れられない。** 条文に一行足すより、欄そのものを
    消すほうが強い（この現場では逆向きの実測が何度もある —— 箱を作ると
    入れてくれる）。呼び出し側は `cast_line()` で人数も一行で言う。
    """
    out = {k: v for k, v in (ledger or {}).items()
           if partner or k not in PARTNER_KEYS}
    return out


def cast_line(*, partner: bool, name_a: str = "", name_b: str = "") -> str:
    """人数を一行で。台帳から欄を消すだけでなく、言葉でも言う。"""
    a = (name_a or "the lead").strip()
    if not partner:
        return (
            f"CAST: solo — {a} is the only person in frame. There is no second "
            "person: never write wearing_b, beat_b or expression_b."
        )
    b = (name_b or "the partner").strip()
    return (
        f"CAST: two in frame — {a} and {b}. "
        f"wearing_b / beat_b / expression_b are {b}'s, never {a}'s."
    )


def now_line(ledger: dict[str, str], *, locale: str = "ja") -> str:
    """Plain-language NOW — all shot axes the actress must respect."""
    wearing = (ledger.get("wearing") or "").strip()
    beat = (ledger.get("beat") or "").strip()
    expression = (ledger.get("expression") or "").strip()
    scene = (ledger.get("scene") or "").strip()
    light = (ledger.get("light") or "").strip()
    bg = (ledger.get("bg") or "").strip()
    frame = (ledger.get("frame") or "").strip()
    bits: list[str] = []
    if locale.startswith("ja"):
        if wearing:
            bits.append(f"服装: {wearing}")
        if beat:
            bits.append(f"姿勢: {beat}")
        if expression:
            bits.append(f"表情: {expression}")
        if scene:
            bits.append(f"場所: {scene}")
        if bg and bg != scene:
            bits.append(f"背景: {bg}")
        if light:
            bits.append(f"光: {light}")
        if frame:
            bits.append(f"構図: {frame}")
        if atm := (ledger.get("atmosphere") or "").strip():
            bits.append(f"雰囲気: {atm}")
        if look := (ledger.get("look") or "").strip():
            bits.append(f"画風: {look}")
        if letter := (ledger.get("lettering") or "").strip():
            bits.append(f"文字: {letter}")
        return " / ".join(bits) if bits else "（まだ画は決まっていない）"
    if wearing:
        bits.append(f"wearing {wearing}")
    if beat:
        bits.append(f"pose {beat}")
    if expression:
        bits.append(f"face {expression}")
    if scene:
        bits.append(f"place {scene}")
    if bg and bg != scene:
        bits.append(f"bg {bg}")
    if light:
        bits.append(f"light {light}")
    if frame:
        bits.append(f"frame {frame}")
    if atm := (ledger.get("atmosphere") or "").strip():
        bits.append(f"mood {atm}")
    if look := (ledger.get("look") or "").strip():
        bits.append(f"look {look}")
    if letter := (ledger.get("lettering") or "").strip():
        bits.append(f'lettering "{letter}"')
    return "; ".join(bits) if bits else "(shot not set yet)"


def touched_picture(patch: dict[str, str]) -> bool:
    if any(k in patch for k in LEDGER_KEYS):
        return True
    return bool(str(patch.get("wearing_drop") or "").strip())


def looks_like_picture_line(text: str) -> bool:
    """Heuristic only — used to retry an empty writer, not to apply patches."""
    return bool(_PICTURE_CUES.search(text or ""))

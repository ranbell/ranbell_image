"""Shot notebook — plain-language source of truth (not facets).

Used by 主演撮り and 制作スタッフ. Conversation revises this notebook; craft
TAGS/SCENE are woven from it (and replaced whole) just before a take. Muse talk
may read it; Script writes it. Crew also mirrors PLAN/COSTUME into the notebook.
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterable
from typing import Any

# Garment vocabulary has one owner. `brief` imports `identity` and neither
# imports this module, so the edge is safe.
from . import brief
from ..tags import catalog as tag_catalog

SHOT_KEYS = (
    "atmosphere",
    "scene",
    # 彼女以外に画面に写っているもの。背後の建物、周りのエキストラ、置かれた
    # 小道具。**現場ではこれを BG と呼ぶ**（Background の略で、無線に乗る）。
    #
    # 無かった間、監督が「後ろにあの建物」「周りに他のレイヤーさん」と何度
    # 言っても、置き場が無いので絵から消えていた。実撮影（コミケ）では監督が
    # 場所を4回言い、撮影3本のうち2本に建物も人混みも入らなかった。
    #
    # 名前は測って決めた。7本 × 10回で `set` `backdrop` `scenery` はどれも
    # 届かず、`BG` だけが届いた（44% → 68%）。`backdrop` はこのライブラリに
    # 1枚も無い語で、`set_dressing` も `extras` も `mob` も 0 枚。
    # **現場で実際に使われている語だけが通った。**
    "bg",
    # Where the light comes from and how hard it is. Its own field because it is
    # its own decision: the crewed studio has a seat that owns exposure and a
    # PLAN line that owns the intent, and 主演撮り had neither — 「逆光にして」
    # could only land inside scene or atmosphere, both of which are rewritten
    # for other reasons, so it was gone again a turn later.
    "light",
    "frame",
    "wearing",
    "beat",
    # **顔には置き場が無かった。** 係の条文が「顔は beat に入れろ」と言って
    # いたので、身体が動かないターンでは表情がどこにも書かれない ——
    # 総監督（2026-08-29）「intent/note に表情がないので beat が反応しない
    # 限り無表情」。実データでも、手帖の `smiling warmly` はタグに落ちず、
    # 逆に手帖に無い `calm_expression` が weave から出ていた。
    #
    # 欄名は測って決めた（`BG` と同じやり方・5回×5件）。`FACE` /
    # `EXPRESSION` / `MOOD_FACE` はどれも 15/15 で、余計な書き込みも 0/10 ——
    # **背景のときと違って差が出ない**ので、班（`facets`）が既に使っている
    # `expression` に揃える。
    "expression",
    "wearing_b",
    "beat_b",
    "expression_b",
)

META_KEYS = ("vibe", "standing")

_ALL_KEYS = SHOT_KEYS + META_KEYS
# 撮影1本ぶんが残る長さ。12 だと実撮影の前半が消えていた —— コミケの回は
# 監督の発言が 21 ターンあったのに直近12件しか残らず、「場所がいつ入ったか」
# を追えなかった（言い直しや fold を含めると 1 撮影で 50 件前後になる）。
#
# 計器パネルの表示は 12 のまま（`MusePanel.vue`）。**直近が見たい画面と、
# 後から追う記録では要る長さが違う。**
REWRITE_LOG_MAX = 60
_REWRITE_FIELDS = SHOT_KEYS + ("vibe",)


def blank(partner: bool = False) -> dict[str, Any]:
    nb = {
        "atmosphere": "",
        "scene": "",
        "bg": "",
        "light": "",
        "frame": "",
        "wearing": "",
        "beat": "",
        "expression": "",
        "wearing_b": "",
        "beat_b": "",
        "expression_b": "",
        "vibe": "",
        "standing": [],
        "rev": 0,
        "updated_at": 0.0,
    }
    if not partner:
        nb["wearing_b"] = ""
        nb["beat_b"] = ""
        nb["expression_b"] = ""
    return nb


def of(session: dict[str, Any]) -> dict[str, Any]:
    nb = session.get("notebook")
    if not isinstance(nb, dict) or not nb:
        nb = blank(partner=bool(str(
            (session.get("inputs") or {}).get("partner_preset") or ""
        ).strip()))
        session["notebook"] = nb
    for key in _ALL_KEYS:
        if key == "standing":
            nb.setdefault(key, [])
        else:
            nb.setdefault(key, "")
    nb.setdefault("rev", 0)
    nb.setdefault("updated_at", 0.0)
    return nb


def has_shot(nb: dict[str, Any]) -> bool:
    return any(str(nb.get(k) or "").strip() for k in (
        "scene", "bg", "frame", "wearing", "beat", "atmosphere", "light",
    ))


# ── 欄の契約 ────────────────────────────────────────────────────────────
# **ノートに触る全員が、同じ一つの定義を読む。** 8/19 に測って分かったことは、
# 定義が無かったのではなく **3通りに割れていて、一番正確なものが誰にも見えて
# いなかった** ということだった:
#
#   彼女 (DUET_TALK_OUTPUT)     FRAME: <camera / gaze>
#   compile (SCRIPTER_SYSTEM)   frame names ONE crop      ← 視線の記述が無い
#   写真読み (STILL_READ)       FRAME: camera and gaze
#   言い直し (_RESTATE_FIELDS)  …Crop plus gaze. Not where you are looking;
#                               that is the frame.        ← 正確。だが restate
#                                                            のターンでしか出ない
#
# ノートを毎ターン書いているのは compile で、そこには視線の帰属が書かれて
# いなかった。だから「カメラ見て」が frame に入り、beat の `looking at cake`
# は残り、weave は具体的なほう（beat）を採った。総監督は3回言い直した。
#
# 直し方は新しい規則を足すことではない。**一番正確な版を唯一の出典にして、
# 読む側にも書く側にも同じものを見せる。**
FIELD_CONTRACTS: dict[str, str] = {
    "atmosphere": (
        "the mood, and only the mood. No clock, no weather-as-hour, no "
        "objects, no place nouns."
    ),
    "scene": (
        "one specific place and the time of day. Not the light, not what she "
        "is doing, not the camera."
    ),
    "bg": (
        "what is in the picture besides her — the background actors (the "
        "extras, the crowd), the buildings, the set dressing. On set this is "
        "called BG. Not what she wears and not what she is holding: those are "
        "hers. How blurred it is is not here either — that is depth of field, "
        "and it belongs to FRAME with the rest of the camera."
    ),
    "light": (
        "the key and where it comes from, absolute: 'low sun from behind, "
        "hard rim'. Never a direction of change — no 'darker', no 'brighter'. "
        "Not the mood, not the place."
    ),
    "frame": (
        "the camera and where her eyes are pointed, as one story: 'wide shot, "
        "looking straight into the lens'. ONE crop — zoom/close/upper OR "
        "wide/full-body, never both `wide_shot` and `close_up` in the same "
        "frame — plus the gaze. Nothing about her hands or her clothes.\n"
        # 実撮影（ブランコ・2026-08-21）で、監督が「カメラを少し上から」と
        # 言ったターンを compile は `high-angle` と正しく書き、その直後の
        # 言い直しが `low-angle` に化けさせた。理由の欄にはこう残っていた:
        #
        #   「カメラが高い位置にあるんですね」という理解と…指示通りの構図
        #   （被写体を見上げるアングル）に彼女が合わせようとしていることを読み取った
        #
        # **カメラの位置は正しく分かっていて、語だけが逆。** 監督は次のターンで
        # 「from above っていうんだよ。ローアングルじゃないよ」と訂正している。
        #
        # 直すとき、最初は「カメラの高さと視線は逆になる」と説明を書いた。
        # 8回中5回しか保たず、会話に彼女の「上目遣いすぎかな？」が入ると 0/8。
        # 本人の弁:「`she looks UP into it` という記述が、逆にノイズになっている。
        # **Up/Down という単語が視覚的フックになっている**以上、徹底的に排除する」。
        #
        #   視線も書く（up/down を含む）      0/8
        #   位置だけに絞る                    7/8   ← これ
        #   位置だけ＋「逆を向くことが多い」    0/8
        #
        # **取り違えを説明しようとすると、説明に使う語が取り違えの材料になる。**
        "  The angle word names where the camera stands, never where she "
        "looks. Camera above her: `high_angle`. Camera below her: "
        "`low_angle`. Her eyes have no vote in it."
    ),
    "wearing": (
        "everything ON her body and nothing else — clothes, hair, "
        "accessories. A held prop is not worn; that belongs in beat."
    ),
    "beat": (
        "ONE posture stem — sitting / standing / kneeling / crouching — plus "
        "what the hands and the weight are doing, and anything she is holding. "
        "NOT where she is looking: that is the frame. NOT her face: that is "
        "expression."
    ),
    # **顔には置き場が無かった。** 係の条文が「顔は beat に入れろ」と言って
    # いたので、身体が動かないターンでは表情がどこにも書かれない。実データ
    # でも、手帖の `smiling warmly` はタグに落ちず、逆に手帖に無い
    # `calm_expression` が weave から出ていた。
    "expression": (
        "her face — the mouth, the eyes, the brows. A mood she plays goes here, "
        "not in atmosphere: that one is the picture's mood, this one is hers."
    ),
}

_CONTRACT_ORDER = (
    "atmosphere", "scene", "bg", "light", "frame", "wearing", "beat",
    "expression",
)


# Some of these prompts speak TO her, so the same contract has to be sayable in
# the second person. One source, two renderings — never two texts to keep in
# step, which is the state that produced the disagreement in the first place.
_TO_HER = (
    ("where her eyes are pointed", "where your eyes are pointed"),
    ("where she is looking", "where you are looking"),
    ("what she is doing", "what you are doing"),
    ("she is holding", "you are holding"),
    ("ON her body", "ON your body"),
    ("her hands or her clothes", "your hands or your clothes"),
    ("the hands and the weight", "your hands and your weight"),
)


def contracts_block(
    keys: Iterable[str] | None = None, *, second_person: bool = False,
) -> str:
    """The field contracts, worded once, for any prompt that reads or writes.

    Handed to compile, weave, the still-read and her own review alike. When two
    seats disagree about which field owns the gaze, the shot stops moving and
    nobody reports an error — the direction simply lands in a field the
    renderer does not read, and the showrunner repeats himself into a room
    that has already written his words down somewhere useless.
    """
    names = list(keys) if keys else list(_CONTRACT_ORDER)
    lines = [
        "WHAT EACH PART OF THE NOTEBOOK IS "
        "(one definition, the same for everyone who reads or writes it):",
    ]
    for key in names:
        text = FIELD_CONTRACTS.get(key)
        if not text:
            continue
        if second_person:
            for a, b in _TO_HER:
                text = text.replace(a, b)
        lines.append(f"- {key.upper()} — {text}")
    return "\n".join(lines)


def render(nb: dict[str, Any], *, name_a: str = "", name_b: str = "") -> str:
    """Human / model facing dump.

    **名前に文字を添える。** 相方がいるとき、見出しは名前で書くのに欄の名前は
    `WEARING` / `WEARING_B` という文字なので、モデルは相手を「A」と呼んだ ——
    実測（`61db2bd6`）で折り込みが `beat_b: standing behind A` と書いた。
    `A` はタグにならないので画には出ないが、指示としては汚れ。
    総監督「最初に `Mio (Actress A)` と書けばいいだけでしょう」。
    """
    two = bool(name_b or str(nb.get("wearing_b") or "").strip()
               or str(nb.get("beat_b") or "").strip())
    a = name_a or "Muse A"
    if two:
        a = f"{a} (Actress A)"
    lines = [
        f"ATMOSPHERE:\n{str(nb.get('atmosphere') or '').strip() or '(empty)'}",
        f"SCENE:\n{str(nb.get('scene') or '').strip() or '(empty)'}",
        f"BG:\n{str(nb.get('bg') or '').strip() or '(empty)'}",
        f"LIGHT:\n{str(nb.get('light') or '').strip() or '(empty)'}",
        f"FRAME:\n{str(nb.get('frame') or '').strip() or '(empty)'}",
        f"{a} WEARING:\n{str(nb.get('wearing') or '').strip() or '(empty)'}",
        f"{a} BEAT:\n{str(nb.get('beat') or '').strip() or '(empty)'}",
        f"{a} EXPRESSION:\n{str(nb.get('expression') or '').strip() or '(empty)'}",
    ]
    if two:
        b = f"{name_b or 'Muse B'} (Actress B)"
        lines += [
            f"{b} WEARING:\n{str(nb.get('wearing_b') or '').strip() or '(empty)'}",
            f"{b} BEAT:\n{str(nb.get('beat_b') or '').strip() or '(empty)'}",
            f"{b} EXPRESSION:\n{str(nb.get('expression_b') or '').strip() or '(empty)'}",
        ]
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        lines.append(f"VIBE:\n{vibe}")
    standing = [str(s).strip() for s in (nb.get("standing") or []) if str(s).strip()]
    if standing:
        lines.append("STANDING:\n" + "\n".join(f"- {s}" for s in standing[:5]))
    return "\n\n".join(lines)


def summary_for_muse(nb: dict[str, Any], *, name_a: str = "", name_b: str = "") -> str:
    """Shorter block for talk context (English labels; values may be EN)."""
    parts: list[str] = []
    for label, key in (
        ("Atmosphere", "atmosphere"),
        ("Place", "scene"),
        ("BG", "bg"),
        ("Light", "light"),
        ("Camera", "frame"),
    ):
        val = str(nb.get(key) or "").strip()
        if val:
            parts.append(f"{label}: {val}")
    w = str(nb.get("wearing") or "").strip()
    b = str(nb.get("beat") or "").strip()
    who = name_a or "Lead"
    if w or b:
        parts.append(f"{who} wearing: {w or '(unset)'}")
        parts.append(f"{who} beat: {b or '(unset)'}")
    wb = str(nb.get("wearing_b") or "").strip()
    bb = str(nb.get("beat_b") or "").strip()
    if name_b and (wb or bb):
        parts.append(f"{name_b} wearing: {wb or '(unset)'}")
        parts.append(f"{name_b} beat: {bb or '(unset)'}")
    vibe = str(nb.get("vibe") or "").strip()
    if vibe:
        parts.append(f"Vibe: {vibe}")
    return "\n".join(parts)


# Longevity caps (plan: VIBE≤5 lines, STANDING≤5).
VIBE_MAX_LINES = 5
VIBE_MAX_CHARS = 400

# SHOT field contracts — short absolute phrases. Long densify prose belongs
# only in craft_scene. Polluted SCENE fields were how place changes froze:
# the model would rewrite tags/craft but leave a 60-word park paragraph in
# SCENE, and the next turn's Muse digest pulled the shoot back.
SCENE_MAX_CHARS = 120
ATMOSPHERE_MAX_CHARS = 100
# A key, a direction, and how hard it is. Longer than that and it has started
# describing the room instead of lighting it.
LIGHT_MAX_CHARS = 120
FRAME_MAX_CHARS = 160
WEARING_MAX_CHARS = 240
BEAT_MAX_CHARS = 240

BG_MAX_CHARS = 240

_SHOT_FIELD_CAPS: dict[str, int] = {
    "scene": SCENE_MAX_CHARS,
    "bg": BG_MAX_CHARS,
    "atmosphere": ATMOSPHERE_MAX_CHARS,
    "light": LIGHT_MAX_CHARS,
    "frame": FRAME_MAX_CHARS,
    "wearing": WEARING_MAX_CHARS,
    "wearing_b": WEARING_MAX_CHARS,
    "beat": BEAT_MAX_CHARS,
    "beat_b": BEAT_MAX_CHARS,
}

# Gaze used to be scrubbed out of BEAT here with a keyword regex. It is a rule
# in SCRIPTER_SYSTEM now: the scripter reads the conversation and writes the
# frame as one camera story. A word list cannot tell "見上げる" the pose from
# "見上げる" the lens, and every phrase it missed shipped anyway.

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


def _cap_lines(text: str, *, max_lines: int, max_chars: int) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    body = "\n".join(lines[:max_lines]).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip()
    return body


def _cap_phrase(text: str, *, max_chars: int) -> str:
    """Keep a short absolute phrase; cut on a word boundary when possible."""
    body = re.sub(r"\s+", " ", str(text or "").strip())
    if len(body) <= max_chars:
        return body
    cut = body[:max_chars].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.;:")


_TIME_TOKEN_RE = re.compile(
    r"\b(dawn|dusk|sunrise|sunset|twilight|noon|midnight|"
    r"morning|evening|afternoon|night)\b",
    re.I,
)
_NO_ITEM_RE = re.compile(r"\b(?:no|without)\s+[a-z][a-z0-9_]*\b", re.I)


def coerce_plain_phrase(val: Any) -> str:
    """Notebook fields are short English phrases. Lists join; dicts/reprs drop."""
    if val is None or isinstance(val, bool):
        return ""
    if isinstance(val, dict):
        return ""
    if isinstance(val, (list, tuple)):
        parts = [coerce_plain_phrase(x) for x in val]
        return ", ".join(p for p in parts if p)
    text = str(val).strip()
    if not text:
        return ""
    low = text.lower()
    if low in ("unchanged", "変更なし", "同じ", "そのまま", "-", "none", "なし"):
        return ""
    if text.startswith("{") and text.endswith("}"):
        return ""
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return ""
        if inner.startswith(("'", '"')) and inner.endswith(("'", '"')):
            return inner[1:-1].strip()
        bits = [coerce_plain_phrase(p.strip().strip("'\"")) for p in inner.split(",")]
        return ", ".join(p for p in bits if p)
    return text


def wearing_tokens(text: str) -> set[str]:
    """English-ish tokens from a wearing/beat phrase (for craft consistency).

    Built from the field itself — not from a situation vocabulary list.
    ``no hat`` / ``without hat`` do not keep the noun (danbooru ``no_hat`` is
    not a removal). Comma-separated items are tokenised separately so
    ``straw hat, cardigan`` does not mint ``hat_cardigan``.
    """
    out: set[str] = set()
    for chunk in re.split(r"[,，、]", str(text or "")):
        raw = _NO_ITEM_RE.sub(" ", chunk.lower())
        if not raw.strip():
            continue
        out |= set(_TOKEN_RE.findall(raw))
        words = re.findall(r"[a-z][a-z0-9]+", raw)
        for i in range(len(words) - 1):
            pair = (words[i], words[i + 1])
            # `straw hat` is a garment; `the metal`, `while staring`, `on the`
            # are grammar. Pairing across a function word never names a thing,
            # and these are read back as items that must never return.
            if pair[0] in _STRUCK_NOISE or pair[1] in _STRUCK_NOISE:
                continue
            out.add(f"{pair[0]}_{pair[1]}")
    return out


def split_atmosphere_time(atmosphere: str, scene: str) -> tuple[str, str]:
    """Move clock words out of mood into scene. Mood stays feeling-only."""
    atm = str(atmosphere or "").strip()
    sc = str(scene or "").strip()
    found = [m.group(0).lower() for m in _TIME_TOKEN_RE.finditer(atm)]
    if not found:
        return atm, sc
    mood = _TIME_TOKEN_RE.sub(" ", atm)
    mood = re.sub(r"[\s,;]+", " ", mood).strip(" ,;.")
    hour = found[-1]
    if hour and hour not in sc.lower():
        sc = f"{sc} at {hour}".strip() if sc else hour
    return mood, sc



# The four postures the scripter's beat contract names (`chain.SCRIPTER_SYSTEM`:
# "Beat always names ONE posture stem"). Not a vocabulary of situations — the
# closed set the contract is written against, so the tag bag can be held to it.
POSTURE_STEMS: dict[str, tuple[str, ...]] = {
    "sitting": ("sitting", "sits", "seated", "sit", "座"),
    "standing": ("standing", "stands", "stand", "立"),
    "kneeling": ("kneeling", "kneels", "kneel", "seiza", "跪", "正座"),
    "squatting": ("crouching", "crouch", "squatting", "squat", "しゃが"),
}


def posture_stem(beat: str) -> str:
    """The danbooru stem the beat names, or "" when it names none."""
    text = str(beat or "").lower()
    for tag, words in POSTURE_STEMS.items():
        if any(w in text for w in words):
            return tag
    return ""


def ensure_beat_leads_scene(
    scene: str, *, beat: str, beat_b: str = "",
) -> str:
    """If craft_scene forgot the notebook beat, put the body first.

    Why: Weave was rewarded for long air/cloth prose and often buried or
    omitted the Showrunner's posture. pose_intent stored the beat but never
    reached Comfy — only craft_scene did. This is the hard floor: the beat
    the notebook already named must open the prose the sampler reads.
    """
    body = str(scene or "").strip()
    leads: list[str] = []
    for raw in (beat, beat_b):
        phrase = coerce_plain_phrase(raw)
        if not phrase:
            continue
        stem = posture_stem(phrase)
        low = body.lower()
        # Already present as stem or as a clear substring of the beat phrase.
        if stem and stem in low:
            continue
        key = phrase.lower()
        if len(key) >= 8 and key[:40] in low:
            continue
        # One short English lead-in the sampler can act on.
        leads.append(phrase.rstrip(".") + ".")
    if not leads:
        return body
    head = " ".join(leads)
    return f"{head} {body}".strip() if body else head


# Caps on reinjected place tags. Weave is body-first and drops SCENE/BG; the
# code puts a short reminder back. A planner that padded BG once must not force
# a dozen set-dressing nouns into every bag from then on.
PLACE_SCENE_TAG_CAP = 3
PLACE_BG_TAG_CAP = 4
PLACE_BG_PROSE_CHARS = 80


def place_field_tags(text: str, *, cap: int) -> list[str]:
    """Ordered danbooru-ish tags from a SCENE/BG phrase, capped.

    Compounds first (`night_classroom`) — they name the place — then longer
    singles. Grammar noise from ``wearing_tokens`` is already filtered; a
    single that is already inside a chosen compound is skipped so the bag does
    not say both `night_classroom` and `classroom`.
    """
    toks = {
        t for t in wearing_tokens(text)
        if t not in _STRUCK_NOISE and len(t) >= 3
    }
    compounds = sorted((t for t in toks if "_" in t), key=len, reverse=True)
    singles = sorted(
        (t for t in toks if "_" not in t and len(t) >= 4),
        key=len, reverse=True,
    )
    out: list[str] = []
    for tag in compounds + singles:
        if len(out) >= cap:
            break
        if any(tag in kept or kept in tag for kept in out):
            continue
        out.append(tag)
    return out


def missing_place_tags(
    nb: dict[str, Any], *, have: set[str], gone: set[str],
    scene_cap: int = PLACE_SCENE_TAG_CAP,
    bg_cap: int = PLACE_BG_TAG_CAP,
) -> list[str]:
    """SCENE/BG tokens the weave bag forgot — same job as posture reinject."""
    covered = set(have or ())
    missing: list[str] = []
    for field, cap in (("scene", scene_cap), ("bg", bg_cap)):
        for key in place_field_tags(str((nb or {}).get(field) or ""), cap=cap):
            if key in gone or key in covered:
                continue
            if any(key in t or t in key for t in covered):
                continue
            missing.append(key)
            covered.add(key)
    return missing


def _place_mentioned(phrase: str, prose: str) -> bool:
    """True when craft_scene already carries the place the notebook named."""
    body = str(prose or "")
    low = body.lower()
    low_us = low.replace(" ", "_")
    text = coerce_plain_phrase(phrase)
    if not text:
        return True
    if text.lower() in low:
        return True
    tags = place_field_tags(text, cap=max(PLACE_SCENE_TAG_CAP, PLACE_BG_TAG_CAP))
    if not tags:
        return False
    return any(
        t in low_us or t.replace("_", " ") in low
        for t in tags
    )


def ensure_place_in_scene(
    craft_scene: str, *, scene: str, bg: str = "",
) -> str:
    """If craft_scene forgot the notebook place, put it at the end.

    Beat leads (``ensure_beat_leads_scene``); place trails — that is the weave
    contract order (body → clothes → light → place). Without this floor, a
    body-first weave leaves the sampler with the old rooftop while the
    notebook already says classroom.
    """
    body = str(craft_scene or "").strip()
    tails: list[str] = []
    place = coerce_plain_phrase(scene)
    if place and not _place_mentioned(place, body):
        tails.append(place.rstrip(".") + ".")
    extras = coerce_plain_phrase(bg)
    pending = " ".join(tails)
    if extras and not _place_mentioned(extras, body) and not _place_mentioned(
        extras, pending,
    ):
        short = _cap_phrase(extras, max_chars=PLACE_BG_PROSE_CHARS)
        if short:
            tails.append(short.rstrip(".") + ".")
    if not tails:
        return body
    tail = " ".join(tails)
    return f"{body} {tail}".strip() if body else tail


def _same_garment(a: str, b: str) -> bool:
    """Two head nouns naming one thing. `dress` and `sundress` are one dress.

    The suffix rule is guarded at four characters so `top` does not swallow
    `laptop` — and so the shorter head has to be a real garment word before it
    is allowed to absorb a longer one.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.endswith(short)


def garment_matches(wearing: str, name: str) -> list[str]:
    """Items in WEARING that the showrunner means by `name`.

    Zero means she is not wearing it. Two means the ask has no single referent
    — which is the moment to put the question back to the showrunner instead of
    guessing, because guessing here undresses her wrongly and silently.
    """
    head = brief.garment_head(name)
    if not head:
        return []
    out: list[str] = []
    for item in str(wearing or "").split(","):
        item = item.strip()
        if item and _same_garment(brief.garment_head(item), head):
            out.append(item)
    return out


def beat_without(beat: str, garment: str) -> str:
    """The same action, minus the part that needs a garment she took off.

    Clothes and action are one thing to everyone except the notebook: BEAT
    reads `standing, clutching the hem of her skirt`, the skirt comes off, and
    the hem is still in her hand. The posture stem always survives — losing a
    garment is not a reason to stop standing.
    """
    words = {w for w in re.split(r"[_\s-]+", str(garment or "").lower()) if len(w) > 2}
    head = brief.garment_head(garment)
    if head:
        words.add(head)
    if not words:
        return str(beat or "")
    kept: list[str] = []
    for clause in str(beat or "").split(","):
        text = clause.strip()
        if not text:
            continue
        low = text.lower()
        # A clause that holds the posture is never dropped, even when it also
        # names the garment: `sitting on her coat` still says she is sitting.
        if any(w in low for w in words) and not posture_stem(text):
            continue
        kept.append(text)
    if not kept:
        return posture_stem(beat) or str(beat or "")
    return ", ".join(kept)


def shot_tokens(nb: dict[str, Any]) -> set[str]:
    """Everything the shot currently says, as tokens."""
    out: set[str] = set()
    for key in SHOT_KEYS:
        out |= wearing_tokens(str(nb.get(key) or ""))
    return out


def struck_tokens(session: dict[str, Any]) -> set[str]:
    """What must not come back — minus whatever the shot now says.

    `struck` is append-only, and it is read as "never restore this". That is
    right for a garment the showrunner took off and wrong for everything that
    legitimately comes and goes: stand up and `sitting` is struck, so the next
    「座って」 is fighting a filter, and `filter_weave_tags` strips the very
    tag the notebook just asked for. The notebook is the shot — anything it
    currently names is by definition not struck.
    """
    live = shot_tokens(of(session)) if isinstance(session, dict) else set()
    out: set[str] = set()
    for item in session.get("struck") or []:
        s = str(item or "").strip()
        if not s:
            continue
        out.add(s.lower().replace(" ", "_"))
        out |= wearing_tokens(s)
    return {t for t in out if len(t) >= 3 and t not in live}


def live_struck(session: dict[str, Any]) -> list[str]:
    """The struck list as shown to a model: same pruning, original wording."""
    live = shot_tokens(of(session)) if isinstance(session, dict) else set()
    out: list[str] = []
    for item in session.get("struck") or []:
        s = str(item or "").strip()
        if not s:
            continue
        key = s.lower().replace(" ", "_")
        if key in live or (wearing_tokens(s) & live):
            continue
        out.append(s)
    return out


_STRUCK_NOISE = {
    "and", "with", "the", "her", "his", "she", "for", "from", "over", "under",
    "on", "at", "in", "of", "to", "a", "an", "while", "nothing", "into",
    "onto", "that", "this", "its", "out", "off", "up", "down", "by",
}


def record_struck_from_wearing(
    session: dict[str, Any], *, prev_wearing: str, new_wearing: str,
) -> list[str]:
    """Tokens dropped from wearing stay struck so still-read / weave cannot restore them."""
    return record_struck_tokens(
        session, prev=prev_wearing, new=new_wearing, min_len=3,
    )


def record_struck_tokens(
    session: dict[str, Any], *, prev: str, new: str, min_len: int = 3,
) -> list[str]:
    """Tokens that left a shot phrase stay struck (clothes, place, hour, pose, crop)."""
    dropped = wearing_tokens(prev) - wearing_tokens(new)
    added = sorted(
        t for t in dropped if t not in _STRUCK_NOISE and len(t) >= min_len
    )
    if not added:
        return []
    prior = [str(s) for s in (session.get("struck") or []) if str(s).strip()]
    have = {s.lower().replace(" ", "_") for s in prior}
    for t in added:
        if t not in have:
            prior.append(t)
            have.add(t)
    session["struck"] = prior
    return added


def tag_mentions_struck(tag: str, struck: set[str]) -> bool:
    from .identity import bare_tag

    bare = bare_tag(tag)
    if not bare:
        return False
    if bare in struck:
        return True
    for s in struck:
        if len(s) < 3:
            continue
        # The struck word has to BE the tag, or be its head noun — the last
        # component. English compounds put the head on the right, so `blouse`
        # rules out `white_blouse`, and `hat` rules out `straw_hat`.
        #
        # Matching any component (`s in bare.split("_")`) was the old rule and
        # it poisoned whole sessions. `wearing_tokens` splits a garment phrase
        # into its words, so taking off one "stylish white blouse" struck
        # `blouse`, `white` AND `stylish` — and struck `white` then blocked
        # `white_shirt`, `white_socks`, and her `white_hair`. A modifier is not
        # the thing that was removed.
        if bare == f"no_{s}" or bare.endswith(f"_{s}"):
            return True
    return False


def garment_lifts_struck(garment: str, struck_token: str) -> bool:
    """Does naming this garment lift this struck entry? Deliberately generous.

    The mirror of :func:`tag_mentions_struck`, and **not the same rule** — the
    two directions are not symmetric:

    * blocking is destructive, so it matches narrowly (head noun only);
    * freeing is recoverable, so it matches on any word part.

    Taking off a "stylish white blouse" strikes `blouse`, `white`, `stylish`.
    Blocking on `white` would rule out `white_shirt` and her `white_hair`, so
    it must not. But once she is dressed in a `white blouse` again, every one
    of those entries should go — including the modifiers, which no narrow rule
    would ever reach. Leaving `white` struck forever is the failure the
    wardrobe button exists to undo.
    """
    from .identity import bare_tag

    bare = bare_tag(garment) or str(garment or "").strip().lower().replace(" ", "_")
    s = str(struck_token or "").strip().lower().replace(" ", "_")
    if not bare or len(s) < 3:
        return False
    return bare == s or bare.endswith(f"_{s}") or s in bare.split("_")


_QUALITY_TAG_KEEP = {
    "knit", "drape", "folds", "fabric", "grain", "bokeh", "depth",
    "cinematic", "soft", "light", "shadow", "texture", "skin", "air",
}


def filter_weave_tags(
    tags: str, *, wearing: str, scene: str, beat: str, struck: set[str],
    wearing_b: str = "", beat_b: str = "", frame: str = "",
    banned: set[str] | None = None,
) -> str:
    """Drop struck / banned tokens. Shot nouns are reconciled by sibling filters.

    ``wearing`` / ``scene`` / ``beat`` / ``frame`` stay on the signature so
    callers can pass the whole notebook context in one place; garment, crop,
    and notebook-fight passes live in ``scrub_craft_tags``. Banned used to be
    enforced only on seat turns — weave could write a refused tag straight
    back into the bag the Showrunner had already struck from the picture.
    """
    _ = (wearing, scene, beat, wearing_b, beat_b, frame)
    refuse = {str(t).strip() for t in (banned or ()) if str(t).strip()}
    kept: list[str] = []
    seen: set[str] = set()
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        from .identity import bare_tag
        key = bare_tag(tok)
        if not key or key in seen:
            continue
        if struck and tag_mentions_struck(tok, struck):
            continue
        if key in refuse:
            continue
        seen.add(key)
        kept.append(tok)
    return ", ".join(kept)


def stale_wearing_tags(
    *, prev_wearing: str, new_wearing: str, tags: str,
) -> list[str]:
    """Tag tokens dropped from wearing that still appear in the craft bag."""
    dropped = wearing_tokens(prev_wearing) - wearing_tokens(new_wearing)
    if not dropped:
        return []
    have = wearing_tokens(tags.replace(",", " "))
    noise = {"and", "with", "the", "her", "his", "she", "for", "from"}
    return sorted(t for t in dropped if t in have and t not in noise and len(t) >= 4)


_WIDE_CROP_TAGS = {
    "wide_shot", "wide_view", "long_shot", "full_body", "establishing_shot",
}
_CLOSE_CROP_TAGS = {
    "close_up", "closeup", "face_focus", "extreme_close-up", "extreme_closeup",
}


def drop_crops_not_in_frame(tags: str, *, frame: str) -> str:
    """Keep one crop family. Zoom must not keep wide_shot; wide must not keep close_up."""
    from .identity import bare_tag, framing_from_phrase

    crop = framing_from_phrase(frame)
    if crop == "auto":
        have = {bare_tag(p) for p in str(tags or "").split(",") if p.strip()}
        if have & _WIDE_CROP_TAGS and have & _CLOSE_CROP_TAGS:
            drop = _WIDE_CROP_TAGS | _CLOSE_CROP_TAGS
        else:
            return tags
    elif crop in ("face_closeup", "upper_body", "from_behind"):
        drop = set(_WIDE_CROP_TAGS)
        if crop == "face_closeup":
            drop.add("full_body")
    elif crop == "full_body":
        drop = set(_CLOSE_CROP_TAGS)
    else:
        return tags
    kept: list[str] = []
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        if bare_tag(tok) in drop:
            continue
        kept.append(tok)
    return ", ".join(kept)


def drop_garments_not_in_wearing(tags: str, *, wearing: str, wearing_b: str = "") -> str:
    """Drop garment tags the notebook no longer authorizes.

    The old leftover list only caught accessories (hat / coat / cardigan…).
    Base outfits like ``sailor_uniform`` sat outside it, so a weave that still
    remembered the sailor put her back in it while WEARING already said white
    shirt — notebook right, prompt wrong. Clothing-axis tags (and the same
    accessory heads) whose head or tokens the wardrobe does not name are
    dropped; quality words (``knit``, ``folds``) stay.
    """
    allowed = wearing_tokens(wearing) | wearing_tokens(wearing_b)
    heads = wardrobe_heads(wearing) | wardrobe_heads(wearing_b)
    if not allowed and not heads:
        return tags
    kept: list[str] = []
    from .identity import bare_tag
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = bare_tag(tok)
        last = key.split("_")[-1] if key else ""
        if last in _QUALITY_TAG_KEEP:
            kept.append(tok)
            continue
        if _tag_authorized_by_wearing(key, allowed=allowed, heads=heads):
            kept.append(tok)
            continue
        if _looks_like_clothing_tag(key):
            continue
        kept.append(tok)
    return ", ".join(kept)


# Accessory heads the original leftover list named, plus base-outfit heads the
# clothing axis sometimes misses (``serafuku``, ``loafers``).
_GARMENT_HEAD_DROP = {
    "hat", "cardigan", "coat", "jacket", "hoodie", "cape",
    "umbrella", "scarf", "glasses", "sunglasses",
    "uniform", "serafuku", "fuku", "dress", "blouse", "shirt",
    "skirt", "sweater", "vest", "apron", "kimono", "yukata",
    "gown", "romper", "onesie", "bikini", "swimsuit",
    "collar", "necktie", "ribbon", "bow", "bowtie",
    "shoes", "boots", "loafers", "socks", "stockings",
    "gloves", "panties", "bra", "lingerie", "tights",
}


def _tag_authorized_by_wearing(
    key: str, *, allowed: set[str], heads: set[str],
) -> bool:
    if not key:
        return False
    if key in allowed or brief.garment_head(key) in heads:
        return True
    parts = {p for p in key.split("_") if p and p not in _STRUCK_NOISE}
    if parts & allowed:
        return True
    return any(key in a or a in key for a in allowed if len(a) >= 4)


def _looks_like_clothing_tag(key: str) -> bool:
    if not key:
        return False
    if tag_catalog.get_tag_axis(key) == "clothing":
        return True
    last = key.split("_")[-1]
    if last in _GARMENT_HEAD_DROP:
        return True
    return any(key.endswith(s) for s in tag_catalog.CLOTHING_SUFFIXES)


def _wearing_mentioned(phrase: str, prose: str) -> bool:
    """True when craft_scene already carries the wardrobe the notebook named."""
    body = str(prose or "")
    low = body.lower()
    low_us = low.replace(" ", "_")
    text = coerce_plain_phrase(phrase)
    if not text:
        return True
    if text.lower() in low:
        return True
    for item in re.split(r"[,，、;]", text):
        item = item.strip()
        if not item:
            continue
        toks = place_field_tags(item, cap=3) or [
            t for t in wearing_tokens(item) if "_" in t or len(t) >= 4
        ]
        if any(t in low_us or t.replace("_", " ") in low for t in toks):
            return True
        if item.lower() in low:
            return True
    return False


def _scrub_unauthorized_clothes_prose(
    prose: str, *, wearing: str, wearing_b: str = "",
) -> str:
    """Remove garment phrases the notebook no longer authorizes from craft_scene."""
    text = str(prose or "").strip()
    if not text:
        return text
    allowed = wearing_tokens(wearing) | wearing_tokens(wearing_b)
    heads = wardrobe_heads(wearing) | wardrobe_heads(wearing_b)
    if not allowed and not heads:
        return text
    forms = [
        tok for tok in sorted(wearing_tokens(text), key=len, reverse=True)
        if (("_" in tok or len(tok) >= 5)
            and _looks_like_clothing_tag(tok)
            and not _tag_authorized_by_wearing(tok, allowed=allowed, heads=heads))
    ]
    for tok in forms:
        for form in {tok, tok.replace("_", " ")}:
            text = re.sub(rf"(?i)\b{re.escape(form)}\b", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"(?i)\b(?:in|wearing|with)\s+a?\s*[.,;]", " ", text)
    return text.strip(" ,.;")


def ensure_wearing_in_scene(
    craft_scene: str, *, wearing: str, wearing_b: str = "",
) -> str:
    """Scrub stale outfits from craft_scene, then put the notebook wardrobe in.

    Weave often keeps writing the sailor after WEARING moved to a shirt.
    Tags are reconciled by ``drop_garments_not_in_wearing``; prose needs the
    same floor — drop what the notebook no longer names, then append what it
    does. Clothes sit before place in the weave contract order.
    """
    body = _scrub_unauthorized_clothes_prose(
        str(craft_scene or "").strip(),
        wearing=wearing, wearing_b=wearing_b,
    )
    tails: list[str] = []
    mine = coerce_plain_phrase(wearing)
    if mine and not _wearing_mentioned(mine, body):
        short = _cap_phrase(mine, max_chars=WEARING_MAX_CHARS)
        if short:
            tails.append(f"Wearing {short.rstrip('.')}.")
    hers = coerce_plain_phrase(wearing_b)
    pending = " ".join(tails)
    if hers and not _wearing_mentioned(hers, body) and not _wearing_mentioned(
        hers, pending,
    ):
        short = _cap_phrase(hers, max_chars=WEARING_MAX_CHARS)
        if short:
            tails.append(f"Partner wearing {short.rstrip('.')}.")
    if not tails:
        return body
    tail = " ".join(tails)
    return f"{body} {tail}".strip() if body else tail


def _missing_wearing_items(
    tags: str, *, wearing: str, wearing_b: str = "",
    struck: set[str] | None = None, banned: set[str] | None = None,
) -> list[str]:
    """Garment heads the notebook names that the bag forgot."""
    from .identity import tag_names

    have = set(tag_names(tags))
    have |= {t for tag in have for t in wearing_tokens(tag)}
    gone = set(struck or ()) | {
        str(t).strip().lower().replace(" ", "_") for t in (banned or ()) if str(t).strip()
    }
    missing: list[str] = []
    wardrobes = " , ".join(x for x in (wearing, wearing_b) if x)
    for item in re.split(r"[,，、;]", wardrobes):
        tokens = wearing_tokens(item)
        if not tokens or tokens & gone:
            continue
        if tokens & have:
            continue
        tag = re.sub(r"\s+", "_", item.strip().lower())
        tag = re.sub(r"[^a-z0-9_-]", "", tag).strip("_-")
        if tag and len(tag) >= 3:
            missing.append(tag)
            have.add(tag)
            have |= tokens
    return missing


def reconcile_wardrobe_tags(
    tags: str, *, wearing: str, wearing_b: str = "",
    struck: set[str] | None = None, banned: set[str] | None = None,
    sides: tuple[str, str] = ("", ""),
    partner: bool = False,
) -> tuple[str, tuple[str, str]]:
    """One wardrobe pass: refuse → aliases → leftovers → inject forgotten clothes.

    Scrub and `_apply_compiled_craft` used to run these as three separate looks
    at the same wearing line; the bag drifted between them. One function, one
    order, both callers.
    """
    from .identity import bare_tag

    struck = set(struck or ())
    banned_set = set(banned or ())
    tags = filter_weave_tags(
        tags, wearing=wearing, scene="", beat="", struck=struck,
        wearing_b=wearing_b, beat_b="", frame="", banned=banned_set,
    )
    side_a, side_b = str(sides[0] or ""), str(sides[1] or "")

    if not partner:
        gone = garment_aliases(tags, wearing)
    elif side_a and side_b:
        gone = garment_aliases(side_a, wearing) | garment_aliases(side_b, wearing_b)
    else:
        heads = wardrobe_heads(wearing) | wardrobe_heads(wearing_b)
        gone = {
            t for t in (garment_aliases(tags, wearing) | garment_aliases(tags, wearing_b))
            if brief.garment_head(t) not in heads
        }

    def _without(bag: str) -> str:
        return ", ".join(
            p.strip() for p in str(bag or "").split(",")
            if p.strip() and bare_tag(p) not in gone
        )

    if gone:
        tags = _without(tags)
        side_a, side_b = _without(side_a), _without(side_b)

    tags = drop_garments_not_in_wearing(tags, wearing=wearing, wearing_b=wearing_b)
    # **二人いるときは、側ごとに見る。** `_missing_wearing_items` の
    # 「もうある」判定は語のかぶりで見るので、みおが `light_blue_dress` を着て
    # いると、すみれの `pale blue dress` は `dress`／`blue` が既出という理由で
    # 「足りている」と判定される —— **二着目は絶対に戻らない。**
    #
    # 実測（`94b4fc9f`・2026-08-28）: 総監督が「すみれちゃんは黒のカクテル
    # ドレス」と言った次のテイクで、すみれの行が
    # `Sumire is blonde_hair, braid, long_hair, green_eyes, medium_breasts, slim,`
    # ——**服がひとつも無い**まま出た。これは一度直してあった不具合で、旧
    # `_missing_wearing_tags` の docstring が「相方だけ忘れた服が戻らない」と
    # 記録している。ここへ移すときに、その教訓が落ちた。
    if partner and (side_a.strip() or side_b.strip()):
        def _fresh(side: str, items: list[str]) -> list[str]:
            """既にその人が着ている部位は足さない —— 一着一名。

            `black_dress` が居るところへ `black_cocktail_dress` を足すと、
            サンプラーには黒い服が二着に見える。
            """
            from .identity import tag_names

            worn = {brief.garment_head(t) for t in tag_names(side)}
            return [t for t in items if brief.garment_head(t) not in worn]

        miss_a = _fresh(side_a, _missing_wearing_items(
            side_a, wearing=wearing, struck=struck, banned=banned_set))
        miss_b = _fresh(side_b, _missing_wearing_items(
            side_b, wearing=wearing_b, struck=struck, banned=banned_set))
        if miss_a:
            side_a = ", ".join([p.strip() for p in side_a.split(",") if p.strip()] + miss_a)
        if miss_b:
            side_b = ", ".join([p.strip() for p in side_b.split(",") if p.strip()] + miss_b)
        missing = miss_a + [m for m in miss_b if m not in miss_a]
    else:
        missing = _missing_wearing_items(
            tags, wearing=wearing, wearing_b=wearing_b,
            struck=struck, banned=banned_set,
        )
    if missing:
        parts = [p.strip() for p in tags.split(",") if p.strip()]
        tags = ", ".join(parts + [m for m in missing if m not in parts])
    return tags, (side_a, side_b)


def wardrobe_heads(wearing: str) -> set[str]:
    """The head noun of every item she has on — what her clothes ARE."""
    return {
        brief.garment_head(i)
        for i in re.split(r"[,，、;]", str(wearing or "")) if i.strip()
    } - {""}


def garment_aliases(tags: str, wearing: str) -> set[str]:
    """Tags that rename a garment her wardrobe has already named.

    One garment under three names, measured on a live W take: WEARING read
    `blue sleeveless gown` and the woven bag came back with `gown`,
    `blue_dress` **and** `sleeveless_dress`. To the sampler that is three
    garments, and with two people in frame the two spare ones land on whoever
    is nearest — which is how the black dress and the blue one swapped girls.

    A tag is a rename when all three hold: it is clothing, it borrows a word
    from one of her wardrobe items, and its head noun is not the head noun of
    anything she has on. `black_dress` beside `black cocktail dress` keeps the
    head noun and stays. `blue_dress` beside `blue sleeveless gown` does not.

    Scope is one person's wardrobe. Handed both girls' bags at once this would
    read the other's clothes as her renames.
    """
    items = [i.strip() for i in re.split(r"[,，、;]", str(wearing or "")) if i.strip()]
    if not items:
        return set()
    from .identity import bare_tag

    heads = wardrobe_heads(wearing)
    words: set[str] = set()
    for item in items:
        words |= {w for w in re.split(r"[_\s-]+", item.lower()) if w}
    words -= heads
    if not words:
        return set()
    out: set[str] = set()
    for part in str(tags or "").split(","):
        tag = bare_tag(part)
        # Only clothing is considered, so `blue_sky` beside a blue gown is not
        # read as her dress under another name.
        if not tag or tag_catalog.get_tag_axis(tag) != "clothing":
            continue
        if brief.garment_head(tag) in heads:
            continue
        if {w for w in tag.split("_") if w} & words:
            out.add(tag)
    return out


# 手帖が「レンズを見ている」と言っている形。視線は frame のものだと
# `FIELD_CONTRACTS` が明言しているのに、その所有権がタグに効いていなかった。
_EYES_ON_LENS_RE = re.compile(
    r"(?i)looking_?at_?viewer|into the lens|at the lens|at the camera|"
    r"eye contact|カメラ目線|レンズを見|こっちを見"
)

# 一つに絞ってよい枠だけ。**時刻（`time_of_day`）と部屋は入れない** ——
# `night, twilight, evening` のような重ねは weave が意図して書くことがあり、
# どれを残すかを間違えると光が変わる。ここは狭く始める。
_ONE_ONLY_SLOTS = (
    "camera_distance", "camera_pitch", "camera_side",
    "gaze_target", "gaze_pitch", "eyes", "posture",
)


def drop_tags_that_fight_the_notebook(
    tags: str, *, frame: str, beat: str, beat_b: str = "",
) -> str:
    """織ったタグのうち、正本と正面から食い違うものを落とす。

    **突き合わせる所がどこにも無かった。** `scrub_craft_tags` は手帖の欄を
    六つ受け取っているのに、実際に見ていたのは struck と wearing と切り取り
    だけで、`scene` `beat` `beat_b` は未使用だった。結果（実測 `42b55492`）:

        手帖 frame  close-up, looking straight into the lens
        タグ        closed_eyes, eyes_closed
        地の文      Her gaze remains fixed forward, eyes wide and glassy

    **同じプロンプトの中で、タグと地の文が逆を向いていた。** 切り取りも
    `close-up` / `close_up` / `face_focus` の三つが並んでいた。

    二つだけやる:

    1. **視線は frame のもの。** レンズを見ていると書いてあるなら、目を
       閉じた語は残さない
    2. **一つの枠に一語だけ。** `tags.conflict` の slot をそのまま使い、
       **手帖が名指ししているほうを残す**（どちらも名指しが無ければ先頭）
    """
    from ..tags import conflict
    from .identity import bare_tag

    said = " ".join(x for x in (frame, beat, beat_b) if x).lower()
    eyes_on_lens = bool(_EYES_ON_LENS_RE.search(frame or ""))

    kept: list[str] = []
    taken: dict[str, str] = {}          # slot -> 残した bare tag
    for part in str(tags or "").split(","):
        tok = part.strip()
        if not tok:
            continue
        key = bare_tag(tok)
        if eyes_on_lens and key in conflict.SLOTS["eyes"] and "closed" in key:
            continue
        slot = conflict.slot_of(key)
        if slot in _ONE_ONLY_SLOTS:
            first = taken.get(slot)
            if first is None:
                taken[slot] = key
            elif key != first:
                # 手帖が名指ししているほうが勝つ。していなければ先に来たほう
                mine, theirs = key.replace("_", " "), first.replace("_", " ")
                if mine in said and theirs not in said:
                    kept = [k for k in kept if bare_tag(k) != first]
                    taken[slot] = key
                else:
                    continue
        kept.append(tok)
    return ", ".join(kept)


def scrub_craft_tags(
    tags: str, *, wearing: str, scene: str, beat: str, struck: set[str],
    wearing_b: str = "", beat_b: str = "", frame: str = "",
    banned: set[str] | None = None,
) -> str:
    """Wardrobe reconcile, opposite crop family, and notebook-fight drops.

    Struck / banned / aliases / leftovers / forgotten wearing share one pass
    (``reconcile_wardrobe_tags``). Crop conflict lives only here — assemble
    injects framing tags and does not re-ban the opposite family.
    """
    _ = (scene,)  # kept on the signature for callers that pass the whole shot
    tags, _ = reconcile_wardrobe_tags(
        tags, wearing=wearing, wearing_b=wearing_b,
        struck=struck, banned=banned,
    )
    tags = drop_crops_not_in_frame(tags, frame=frame)
    return drop_tags_that_fight_the_notebook(
        tags, frame=frame, beat=beat, beat_b=beat_b,
    )


def strip_shot_keys(patch: dict[str, Any]) -> dict[str, Any]:
    """Densify must thicken tags/craft_scene only — never rewrite SHOT fields."""
    out = dict(patch or {})
    for key in SHOT_KEYS:
        out.pop(key, None)
    out.pop("standing", None)
    out.pop("wearing_drop", None)
    return out


def shot_snapshot(nb: dict[str, Any]) -> dict[str, Any]:
    """Plain values used to diff who rewrote the notebook."""
    out: dict[str, Any] = {
        k: str((nb or {}).get(k) or "") for k in _REWRITE_FIELDS
    }
    out["standing"] = [
        str(s).strip() for s in ((nb or {}).get("standing") or []) if str(s).strip()
    ]
    return out


def shot_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    keys = set(before or {}) | set(after or {})
    for key in keys:
        old, new = (before or {}).get(key), (after or {}).get(key)
        if old != new:
            changed[key] = {"before": old, "after": new}
    return changed


def record_rewrite(
    session: dict[str, Any], source: str, *,
    before: dict[str, Any], after: dict[str, Any],
    intent: str = "", extra: dict[str, Any] | None = None,
    why: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Append a short rewrite to the session ring. Returns the entry or None.

    ``why`` carries one line per field explaining why it was written that way.
    It rides along with the diff so the instrument panel shows the decision next
    to its result — the showrunner can see 「カメラ見て」 landing in FRAME and
    read the sentence that put it there, instead of inferring it from a value
    that changed.
    """
    changed = shot_diff(before, after)
    if extra:
        changed.update(extra)
    for key, reason in (why or {}).items():
        pair = changed.get(key)
        if isinstance(pair, dict) and reason:
            pair["why"] = reason
    if not changed:
        return None
    entry = {
        "at": time.time(),
        "source": str(source or ""),
        "intent": str(intent or ""),
        "changed": changed,
    }
    log = list(session.get("rewrite_log") or [])
    log.append(entry)
    session["rewrite_log"] = log[-REWRITE_LOG_MAX:]
    return entry


def apply_patch(nb: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply absolute section replacements. Empty string in patch = clear.
    Missing key = unchanged. `standing` is a list (replace whole when provided).
    """
    changed = False
    for key in SHOT_KEYS + ("vibe",):
        if key not in patch:
            continue
        raw = patch.get(key)
        if isinstance(raw, dict):
            continue
        val = coerce_plain_phrase(raw)
        if val.startswith(("{", "[")):
            continue
        # 別の欄のラベルが混ざっていたら、そこで切る。`cut_at_label` は下で
        # 定義している（ラベルの出典 `_label_alternation` の隣に置きたいので）。
        if val:
            val = cut_at_label(val)
        if key in ("wearing", "wearing_b") and val:
            # The scripter restates the whole outfit on every change, so a
            # duplicate it inherits is a duplicate it hands back. Tidy here,
            # at the one door every patch goes through, and both rooms get it.
            val = brief.tidy_wearing(val)
        if key in _SHOT_FIELD_CAPS and val:
            val = _cap_phrase(val, max_chars=_SHOT_FIELD_CAPS[key])
        if key == "vibe" and val:
            val = _cap_lines(val, max_lines=VIBE_MAX_LINES, max_chars=VIBE_MAX_CHARS)
        if val != str(nb.get(key) or "").strip():
            nb[key] = val
            changed = True
    if "atmosphere" in patch or "scene" in patch:
        mood, place = split_atmosphere_time(
            str(nb.get("atmosphere") or ""), str(nb.get("scene") or ""),
        )
        if mood != str(nb.get("atmosphere") or "").strip():
            nb["atmosphere"] = mood
            changed = True
        if place != str(nb.get("scene") or "").strip():
            nb["scene"] = _cap_phrase(place, max_chars=SCENE_MAX_CHARS) if place else ""
            changed = True
    if "standing" in patch:
        raw = patch.get("standing")
        if isinstance(raw, str):
            items = [ln.strip().lstrip("-•").strip()
                     for ln in raw.splitlines() if ln.strip()]
        elif isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw if str(x).strip()]
        else:
            items = []
        items = items[:5]
        if items != list(nb.get("standing") or []):
            nb["standing"] = items
            changed = True
    # Taking something off, said as the one garment rather than as the whole
    # finished outfit. Restating five remaining items verbatim is the work the
    # scripter was measured failing to do —「コート脱いで」came back with the
    # frame rewritten and WEARING untouched, on every removal turn — while the
    # one word it has to produce here is one it already produces. The
    # subtraction is ours; only an unambiguous name is applied, and an ask that
    # matches nothing or matches twice is left for the room to settle.
    drop = coerce_plain_phrase(patch.get("wearing_drop") or "")
    if drop:
        hits = garment_matches(str(nb.get("wearing") or ""), drop)
        if len(hits) == 1:
            rest = [
                item.strip() for item in str(nb.get("wearing") or "").split(",")
                if item.strip() and item.strip() != hits[0]
            ]
            nb["wearing"] = ", ".join(rest)
            beat = beat_without(str(nb.get("beat") or ""), hits[0])
            if beat != str(nb.get("beat") or "").strip():
                nb["beat"] = beat
            changed = True
    if changed:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return nb


_CARD_LINE_RE = re.compile(
    r"(?im)^\s*(PLACE|HOUR|WEARING_B|BEAT_B|WEARING|BEAT|FRAME)\s*[:：]\s*(.*)$"
)
_CARD_KEY = {
    "WEARING": "wearing",
    "BEAT": "beat",
    "FRAME": "frame",
    "WEARING_B": "wearing_b",
    "BEAT_B": "beat_b",
}
POSE_CARD_KEYS = ("beat", "beat_b")
# A fold may move the body. Nothing else: the shot itself only changes when the
# showrunner says so.
#
# `open` used to be here too — a field for the room's proposals, waiting on
# 「それでいこう」. Across 390 live sessions it never once held a proposal; the
# 50 non-empty ones held parser debris (`$$OPEN$$`, `clear_open: true`,
# `false`, `_none_`), which then went back into the scripter prompt and onto
# the panel. A channel the showrunner cannot name is a channel nobody uses.
# What a seat proposes stays in the chat, where it is already readable.
FOLD_PATCH_KEYS = ("beat", "beat_b")


# 折り込みが何を足したかの控え。SHOT_KEYS ではない —— 画の一部ではなく、
# 帳簿。`shot_snapshot` にも `render` にも出ない。
FOLD_UNDO_KEY = "fold_undo"


def record_fold(nb: dict[str, Any], before: dict[str, Any]) -> None:
    """Note what this fold added, so the next turn can let it go."""
    undo = {}
    for key in FOLD_PATCH_KEYS:
        was, now = str(before.get(key) or ""), str(nb.get(key) or "")
        if was != now:
            undo[key] = {"before": was, "after": now}
    if undo:
        nb[FOLD_UNDO_KEY] = undo
    else:
        nb.pop(FOLD_UNDO_KEY, None)


def undo_fold(nb: dict[str, Any]) -> list[str]:
    """Let the last turn's folded gesture go. Returns the fields put back.

    **The way in belonged to her and there was no way out.** A body detail she
    named — trembling hands, a shoulder turned — is folded into beat so the
    take right after she says it has her acting in it. That is the point of the
    fold and it stays. What was missing is the other half: beat records no
    author, so a gesture she mentioned once weighed the same as a posture the
    showrunner set, and stayed for the rest of the shoot. Worse, `struck_tokens`
    drops anything the notebook currently names from the struck list, so the one
    way to remove a word was disabled by the field that kept producing it.
    Measured live: a Muse trembled in every frame of a session and no direction
    could stop her.

    So a fold lasts one turn. If the value is still exactly what the fold left,
    it goes back to what it was before; if anyone has written over it since —
    the showrunner, a restate, a later compile — that value is theirs and is
    left alone. The showrunner's posture survives because it is what the fold
    was written on top of.
    """
    undo = nb.pop(FOLD_UNDO_KEY, None)
    if not isinstance(undo, dict):
        return []
    out: list[str] = []
    for key in FOLD_PATCH_KEYS:
        pair = undo.get(key)
        if not isinstance(pair, dict):
            continue
        if str(nb.get(key) or "") != str(pair.get("after") or ""):
            continue
        nb[key] = str(pair.get("before") or "")
        out.append(key)
    if out:
        nb["rev"] = int(nb.get("rev") or 0) + 1
        nb["updated_at"] = time.time()
    return out


def parse_muse_card(card: str) -> dict[str, str]:
    """Muse CARD labelled fields → notebook keys. PLACE/HOUR are scene, skipped."""
    out: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal key, buf
        if key is not None:
            val = " ".join(x.strip() for x in buf if str(x).strip()).strip()
            if val:
                out[key] = val
        key, buf = None, []

    for line in str(card or "").splitlines():
        m = _CARD_LINE_RE.match(line)
        if m:
            flush()
            key = _CARD_KEY.get(m.group(1).upper())
            buf = [m.group(2)]
            continue
        if key is not None:
            buf.append(line)
    flush()
    return out


def absorb_muse_card(
    nb: dict[str, Any], card: str, *, keys: tuple[str, ...] = POSE_CARD_KEYS,
) -> dict[str, str]:
    """Fold this turn's Muse CARD pose into the notebook.

    Script runs before she talks, so her acted beat would otherwise wait until
    the next compile. Clothes stay with Script; only body action is absorbed.
    """
    parsed = parse_muse_card(card)
    patch = {
        k: parsed[k] for k in keys
        if str(parsed.get(k) or "").strip()
    }
    if not patch:
        return {}
    before = {k: str(nb.get(k) or "") for k in FOLD_PATCH_KEYS}
    apply_patch(nb, patch)
    record_fold(nb, before)
    return patch


# `promote_open` used to fold an affirmed proposal into the shot here, guessing
# from a noun list (持|手に|花|缶|傘|…) whether the thing was handheld (→ BEAT)
# or worn (→ WEARING). The scripter reads the conversation now, sees the
# affirmation itself, and writes the absolute value into the right section.


def migrate(session: dict[str, Any]) -> dict[str, Any]:
    """Ensure notebook exists; seed from digest/craft when empty."""
    from . import facets as facets_mod

    facets_mod.migrate(session)
    partner = bool(str((session.get("inputs") or {}).get("partner_preset") or "").strip())
    nb = of(session)
    if has_shot(nb) or str(nb.get("vibe") or "").strip():
        return session

    digest = str(session.get("digest") or "").strip()
    craft = session.get("craft") or {}
    scene = str(craft.get("scene") or "").strip()
    if digest:
        nb["scene"] = digest[:800]
        nb["vibe"] = digest[:400]
    if scene and not nb.get("scene"):
        nb["scene"] = scene[:800]
    table = facets_mod.table_of(session)
    if table:
        if not nb.get("wearing"):
            nb["wearing"] = str((table.get("costume") or {}).get("nl") or "")[:400]
        if not nb.get("beat"):
            pose = str((table.get("pose") or {}).get("nl") or "")
            expr = str((table.get("expression") or {}).get("nl") or "")
            nb["beat"] = " ".join(x for x in (pose, expr) if x)[:400]
        if not nb.get("frame"):
            nb["frame"] = str((table.get("camera") or {}).get("nl") or "")[:400]
        if partner:
            if not nb.get("wearing_b"):
                nb["wearing_b"] = str((table.get("costume_b") or {}).get("nl") or "")[:400]
            if not nb.get("beat_b"):
                pose_b = str((table.get("pose_b") or {}).get("nl") or "")
                expr_b = str((table.get("expression_b") or {}).get("nl") or "")
                nb["beat_b"] = " ".join(x for x in (pose_b, expr_b) if x)[:400]
    standing = [str(s).strip() for s in (session.get("standing") or []) if str(s).strip()]
    if standing and not nb.get("standing"):
        nb["standing"] = standing[:5]
    if has_shot(nb) or nb.get("vibe") or nb.get("standing"):
        nb["rev"] = max(1, int(nb.get("rev") or 0))
        nb["updated_at"] = time.time()
    session["notebook"] = nb
    return session


# ── Scripter output parse / validate ────────────────────────────────────────

_INTENT_RE = re.compile(
    r"(?im)^[\s>*_-]*INTENT\s*[:：]\s*(casual|shot|mixed|recall)\s*$"
)
def _label_alternation() -> str:
    """ラベルの選択肢を `SHOT_KEYS` から組む。欄名の出典を一つにするため。

    長いラベルを先に並べる: `WHY_FRAME` が `FRAME` として、`WEARING_DROP` が
    `WEARING` として読まれると、値が別の欄に入る。
    """
    names = [f"WHY_{k.upper()}" for k in SHOT_KEYS]
    names += [k.upper() for k in SHOT_KEYS] + ["WEARING_DROP"]
    names += ["VIBE", "STANDING", "TAGS_SHARED", "TAGS_A", "TAGS_B", "TAGS",
              "CRAFT_SCENE", "UNCHANGED", "PROPOSE"]
    return "|".join(sorted(set(names), key=lambda s: (-len(s), s)))


_FIELD_RE = re.compile(
    r"(?im)^[\s>*_-]*(" + _label_alternation() + r")\s*[:：]\s*(.*)$"
)

# **ラベルは、行頭でなくても境界。** `_FIELD_RE` は行頭しか見ないので、名前が
# 一つ前に挟まっただけで境界でなくなる。手帖は自分の頁を `各務 みお WEARING:`
# と名前を頭に付けて書く（`render`）ので、それが読み返されると後続が丸ごと
# 直前の欄に積まれた。実測では frame が頁の残り全部を飲んでいた:
#
#   frame: medium shot, looking straight into lens 各務 みお WEARING: blue
#          sleeveless gown, earrings 各務 みお BEAT: sitting, … 平岡 すみれ WEARING_B
#
# 大文字だけを見る。手帖が書き出す形がそれで、英語の地の文には出ない綴り。
_LABEL_RUN_RE = re.compile(r"\b(?:" + _label_alternation() + r")\s*[:：]")
# 切ったあと末尾に残る名前。欄は短い英語の句（`coerce_plain_phrase`）なので、
# 末尾の非 ASCII の連なりは名前の残骸とみなしてよい。
_TRAILING_NAME_RE = re.compile(r"[\s,、，:：]*(?:[^\x00-\x7F]+\s*)+$")


def cut_at_label(text: str) -> str:
    """One field's value, cut where the next field's label begins.

    A value never contains another field. Whatever follows a label belongs to
    that label, and this is the one place that can say so without knowing which
    field it is looking at.
    """
    body = str(text or "")
    found = _LABEL_RUN_RE.search(body)
    if not found:
        return body.strip()
    return _TRAILING_NAME_RE.sub("", body[:found.start()]).strip()


# 理由は一行。長い説明はノートを汚すだけで、読む側の役に立たない。
WHY_MAX_CHARS = 180

VALID_INTENTS = frozenset({"casual", "shot", "mixed", "recall"})

# Shallow JSON Schema for Ollama `format` (non-stream scripter).
SCRIPTER_FORMAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["casual", "shot", "mixed", "recall"]},
        "atmosphere": {"type": "string"},
        "scene": {"type": "string"},
        "bg": {"type": "string"},
        "light": {"type": "string"},
        "frame": {"type": "string"},
        "wearing": {"type": "string"},
        "beat": {"type": "string"},
        "expression": {"type": "string"},
        "wearing_b": {"type": "string"},
        "wearing_drop": {"type": "string"},
        "beat_b": {"type": "string"},
        "expression_b": {"type": "string"},
        "vibe": {"type": "string"},
        "standing": {"type": "string"},
        "unchanged": {"type": "string"},
        "tags": {"type": "string"},
        "tags_shared": {"type": "string"},
        "tags_a": {"type": "string"},
        "tags_b": {"type": "string"},
        "craft_scene": {"type": "string"},
        # `why` はここに置いてはいけない。**理由の枠が仕事の枠を食う。**
        # 8/19 に実測（`private/muse/crew_lab/why_regression.py`）:
        #
        #     why あり  欄を書いた 0/9
        #     why なし  欄を書いた 9/9
        #
        # 出てきたのはこういう応答だった:
        #
        #     {"intent":"shot",
        #      "why":{"beat":"「ベンチに座って」という指示に基づき
        #             posture stem を sitting に設定。"}}
        #
        # beat を sitting にしたと**説明して、beat を書いていない**。仕事を
        # 記述することが仕事の代わりになっている。言い方を強めた条件
        # （「値が仕事、理由はその註」）でも 0/9 で、**言葉では直らない**。
        # 枠があること自体が原因。
        #
        # 理由はラベル形式の `WHY_*` と言い直し（`parse_restate`）で採る。
        # そちらは値と同じ行の並びに出るので、置き換えが起きない。
        #
        # `propose` は別物。**欄に書けない物の置き場**であって、欄の代わりに
        # 書ける物ではないので、仕事を食う関係にない。決まっていない物を
        # 思いついたときの行き場が無いと、モデルはそれを beat に押し込む
        # （t21「おいしそう？」で手にパンを持たせた）。
        "propose": {"type": "string"},
    },
    "required": ["intent"],
}


_PARTNER_ONLY = ("wearing_b", "beat_b", "expression_b")


def scripter_format_schema(partner: bool = False) -> dict[str, Any]:
    """The output shape, without the partner's fields on a solo shoot.

    `guard_partner_patch` already drops `wearing_b` / `beat_b` when nobody is
    standing there — but it drops them **after** they are written, so whatever
    went in is lost. Measured on 「カーディガン羽織って。」 (10 runs, solo):

        wearing に入った          6
        wearing_b に入って消えた   2   ← 服が着られないまま次のターンへ
        出力が崩れた / 空          2

    Two fields to hand the same garment to is two places to put it. The
    contract already says there is one actress; saying it again did not stop
    this. Taking the field away does — you cannot write into a key the schema
    does not have.
    """
    schema = {"type": SCRIPTER_FORMAT_SCHEMA["type"],
              "properties": dict(SCRIPTER_FORMAT_SCHEMA["properties"]),
              "required": list(SCRIPTER_FORMAT_SCHEMA["required"])}
    if not partner:
        for key in _PARTNER_ONLY:
            schema["properties"].pop(key, None)
    return schema


def merge_tag_bags(
    *, tags: str = "", tags_shared: str = "", tags_a: str = "", tags_b: str = "",
) -> str:
    """Join SHARED/A/B (or flat tags) into one craft tag string, de-duped."""
    parts: list[str] = []
    seen: set[str] = set()
    for bag in (tags_shared, tags_a, tags_b, tags):
        for t in str(bag or "").split(","):
            tok = t.strip()
            if not tok:
                continue
            key = tok.lower().replace(" ", "_")
            if key in seen or key in ("none", "なし", "-"):
                continue
            seen.add(key)
            parts.append(tok)
    return ", ".join(parts)


def guard_partner_patch(
    patch: dict[str, Any], *, partner: bool = False,
) -> dict[str, Any]:
    """Drop the partner's sections on a solo shoot — nobody is standing there.

    This is the whole guard now. Deciding *which* Muse an edit was addressed to
    used to happen here too, off「だけ|のみ|ばっかり」and「二人|ふたり|一緒」;
    it dropped the other card's edits on any line that named one Muse without
    one of those words, which is most lines. The scripter is handed the
    conversation and the speakers, and decides that itself.
    """
    if not partner:
        patch.pop("wearing_b", None)
        patch.pop("beat_b", None)
    return patch


def clean_why(raw: Any, patch: dict[str, Any] | None = None) -> dict[str, str]:
    """One short line per field, and only for fields this patch actually wrote.

    A reason for a field nobody touched is noise in the instrument panel, and
    a reason long enough to be a paragraph is a second notebook.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in SHOT_KEYS:
        if patch is not None and key not in patch:
            continue
        text = " ".join(str(raw.get(key) or "").split())
        if text and text.lower() not in ("none", "なし", "-", "unchanged"):
            out[key] = text[:WHY_MAX_CHARS]
    return out


PROPOSE_MAX_CHARS = 200


def clean_propose(raw: Any) -> str:
    """One line the scripter offers but must not write into the notebook.

    Without somewhere to put it, a model that thinks the shot wants something
    puts it in a field instead — measured on t21「おいしそう？」, where every
    run of five gave her a pastry or a cup nobody had asked for. The channel
    costs one line and keeps the decision in the room.
    """
    text = " ".join(str(raw or "").split())
    # The model sometimes repeats the label inside the value it hands back.
    while text.upper().startswith("PROPOSE"):
        text = text.split(":", 1)[1].strip() if ":" in text else ""
    if not text or text.lower() in ("none", "なし", "-", "(none)"):
        return ""
    return text[:PROPOSE_MAX_CHARS]


def _blank_result(raw: str = "") -> dict[str, Any]:
    return {
        "intent": "casual",
        "patch": {},
        "why": {},
        "propose": "",
        "tags": "",
        "craft_scene": "",
        "raw": raw,
        "valid": False,
    }


def parse_scripter_json(raw: str) -> dict[str, Any] | None:
    """Parse Ollama JSON-format scripter output. None if not JSON.

    Uses ``ai.json_util.parse_json_object`` so missing commas / truncated
    tails can still salvage a usable object before falling back to labelled.
    """
    from ..ai.json_util import parse_json_object

    text = (raw or "").strip()
    if not text:
        return None
    # Fast reject for clearly labelled (non-JSON) blocks.
    head = text.lstrip()[:200]
    if (
        not head.startswith(("{", "[", "`"))
        and "INTENT" in head.upper()
        and "{" not in head
    ):
        return None
    try:
        data = parse_json_object(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "casual").strip().lower()
    if intent not in VALID_INTENTS:
        intent = "casual"
    unchanged = {
        x.strip().lower()
        for x in re.split(r"[,，、\s]+", str(data.get("unchanged") or ""))
        if x.strip() and x.strip().lower() not in ("none", "なし", "-", "")
    }
    patch: dict[str, Any] = {}
    # 欄の名前は `SHOT_KEYS` が唯一の出典。ここにベタ書きしていたせいで、
    # 欄を増やす実験のたびに3箇所（ここ・`_FIELD_RE`・`SCRIPTER_BASE`）を
    # 別々に直す必要があった。契約を1箇所にまとめたときと同じ形の重複。
    for key in SHOT_KEYS + ("vibe",):
        if key in unchanged:
            continue
        if key not in data:
            continue
        val = coerce_plain_phrase(data.get(key))
        if not val:
            continue
        patch[key] = val
    if "standing" in data and "standing" not in unchanged:
        val = str(data.get("standing") or "").strip()
        if val.lower() not in ("none", "なし", "unchanged", "-", "無し", ""):
            patch["standing"] = val
    tags = str(data.get("tags") or "").strip()
    tags_shared = str(data.get("tags_shared") or "").strip()
    tags_a = str(data.get("tags_a") or "").strip()
    tags_b = str(data.get("tags_b") or "").strip()
    for bag_name, bag in (
        ("tags", tags), ("tags_shared", tags_shared),
        ("tags_a", tags_a), ("tags_b", tags_b),
    ):
        if bag.lower() in ("none", "なし", "-"):
            if bag_name == "tags":
                tags = ""
            elif bag_name == "tags_shared":
                tags_shared = ""
            elif bag_name == "tags_a":
                tags_a = ""
            else:
                tags_b = ""
    merged = merge_tag_bags(
        tags=tags, tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    craft_scene = str(data.get("craft_scene") or "").strip()
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""
    return {
        "intent": intent,
        "patch": patch,
        "why": clean_why(data.get("why"), patch),
        "propose": clean_propose(data.get("propose")),
        "tags": merged,
        "tags_shared": tags_shared,
        "tags_a": tags_a,
        "tags_b": tags_b,
        "craft_scene": craft_scene,
        "raw": raw,
        "valid": True,
    }


def parse_scripter_labelled(raw: str) -> dict[str, Any]:
    """Parse labelled scripter output into intent + patch + optional craft."""
    text = raw or ""
    intent = "casual"
    m = _INTENT_RE.search(text)
    if m:
        intent = m.group(1).lower()

    fields: dict[str, str] = {}
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        fm = _FIELD_RE.match(line)
        if fm:
            current = fm.group(1).upper()
            bodies.setdefault(current, [])
            inline = (fm.group(2) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies.setdefault(current, []).append(line)
    for key, parts in bodies.items():
        fields[key] = "\n".join(parts).strip()

    unchanged = {
        x.strip().lower()
        for x in re.split(r"[,，、\s]+", fields.get("UNCHANGED", ""))
        if x.strip() and x.strip().lower() not in ("none", "なし", "-")
    }

    # `SHOT_KEYS` が唯一の出典。ここに書き忘れると値が黙って捨てられる —
    # `wearing_drop` が実際にそうなっていて、ラベル形式で答えたターン（画像が
    # 付く回と、JSON パースが落ちた回の全部）で脱衣が床に落ちていた。
    key_map = {k: k.upper() for k in SHOT_KEYS}
    key_map["wearing_drop"] = "WEARING_DROP"
    key_map["vibe"] = "VIBE"
    why_raw = {
        key: fields.get(f"WHY_{key.upper()}", "")
        for key in SHOT_KEYS
    }
    patch: dict[str, Any] = {}
    for dest, src in key_map.items():
        if dest in unchanged or src.lower() in unchanged:
            continue
        if src in fields:
            val = fields[src]
            if val.lower() in ("unchanged", "変更なし", "同じ", "そのまま", "-"):
                continue
            patch[dest] = val

    if "STANDING" in fields and "standing" not in unchanged:
        val = fields["STANDING"]
        if val.lower() not in ("none", "なし", "unchanged", "-", "無し"):
            patch["standing"] = val

    def _clean_bag(key: str) -> str:
        val = str(fields.get(key) or "").strip()
        return "" if val.lower() in ("none", "なし", "-", "") else val

    tags = _clean_bag("TAGS")
    tags_shared = _clean_bag("TAGS_SHARED")
    tags_a = _clean_bag("TAGS_A")
    tags_b = _clean_bag("TAGS_B")
    merged = merge_tag_bags(
        tags=tags, tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    craft_scene = str(fields.get("CRAFT_SCENE") or "").strip()
    if craft_scene.lower() in ("none", "なし", "-", "unchanged"):
        craft_scene = ""

    # Labelled output counts as valid when INTENT was present or we got a patch.
    valid = bool(m or patch or merged or craft_scene)
    return {
        "intent": intent if intent in VALID_INTENTS else "casual",
        "patch": patch,
        "why": clean_why(why_raw, patch),
        "propose": clean_propose(fields.get("PROPOSE")),
        "tags": merged,
        "tags_shared": tags_shared,
        "tags_a": tags_a,
        "tags_b": tags_b,
        "craft_scene": craft_scene,
        "raw": text,
        "valid": valid,
    }


def parse_scripter(raw: str) -> dict[str, Any]:
    """Parse scripter output — JSON schema first, labelled fallback."""
    text = raw or ""
    if not str(text).strip():
        return _blank_result(text)
    parsed = parse_scripter_json(text)
    if parsed is not None:
        return parsed
    return parse_scripter_labelled(text)


def validate_scripter(
    result: dict[str, Any], *, partner: bool = False, mode: str = "",
) -> dict[str, Any]:
    """Validate-first gate. On failure: no craft fields, mark invalid.

    ``compile`` writes notebook only — tags are ignored even on shot/mixed.
    ``weave`` writes tags/craft_scene and must not rewrite SHOT.
    """
    out = dict(result or {})
    intent = str(out.get("intent") or "casual").strip().lower()
    if intent not in VALID_INTENTS:
        out["intent"] = "casual"
        out["valid"] = False
        out["tags"] = ""
        out["craft_scene"] = ""
        return out
    out["intent"] = intent
    tags_shared = str(out.get("tags_shared") or "").strip()
    tags_a = str(out.get("tags_a") or "").strip()
    tags_b = str(out.get("tags_b") or "").strip()
    tags = str(out.get("tags") or "").strip() or merge_tag_bags(
        tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
    )
    scene = str(out.get("craft_scene") or "").strip()
    if mode == "compile":
        out["patch"] = dict(out.get("patch") or {})
        out["tags"] = ""
        out["tags_shared"] = ""
        out["tags_a"] = ""
        out["tags_b"] = ""
        out["craft_scene"] = ""
        out.setdefault("valid", True)
        return out
    if mode == "weave":
        out["patch"] = strip_shot_keys(dict(out.get("patch") or {}))
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        if partner and not (tags_a and tags_b):
            out["refuse_reason"] = "w_muse_tags_unsplit"
        elif partner:
            tags = merge_tag_bags(
                tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
            )
        out["tags"] = tags
        out["tags_shared"] = tags_shared
        out["tags_a"] = tags_a
        out["tags_b"] = tags_b
        out["craft_scene"] = scene
        out.setdefault("valid", True)
        return out
    if intent in ("shot", "mixed"):
        if not tags and not scene:
            out["valid"] = False
            out["tags"] = ""
            out["craft_scene"] = ""
            return out
        if partner and not (tags_a and tags_b):
            out["refuse_reason"] = "w_muse_tags_unsplit"
        elif partner:
            tags = merge_tag_bags(
                tags_shared=tags_shared, tags_a=tags_a, tags_b=tags_b,
            )
    out["patch"] = dict(out.get("patch") or {})
    out["tags"] = tags
    out["tags_shared"] = tags_shared
    out["tags_a"] = tags_a
    out["tags_b"] = tags_b
    out["craft_scene"] = scene
    out.setdefault("valid", True)
    return out

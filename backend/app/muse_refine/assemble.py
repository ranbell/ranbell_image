"""Deterministic assemble + optional WD14 reference + optional quality pass.

WD14 vector hits are **reference only**. They are never dumped into the prompt.
When enhance_quality is on, the enrich pass may pick from the closed SUGGESTED
set (same spirit as Muse review MISSING:) — noise stays noise unless chosen.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from ..muse import identity
from ..tags.body import BREAST_TAGS as _BREAST_TAGS
from . import anima
from . import debug as debug_mod
from . import ledger as ledger_mod
from .ctx import refine_num_ctx
from . import talk

logger = logging.getLogger(__name__)

_QUALITY_SYSTEM = """You enrich an image-generation prompt's atmosphere ONLY.
Keep every fact about clothes, pose, and place from LEDGER unchanged.
Add mood, air, color temperature, subtle environmental detail, and quality
boosters as comma-separated tags (spaces preferred — Anima style).
Do NOT rename garments. Do NOT move the location. Do NOT change the pose stem.
Do NOT use (tag:weight) emphasis. Do NOT repeat LEDGER facts.

If SUGGESTED is present, those words are a closed vocabulary from the studio
WD14 bank (vector neighbours — often noisy). You may pick useful ones from
SUGGESTED only. Never invent clothes/place words that fight LEDGER.
You may also add ordinary quality/atmosphere tags not in SUGGESTED
(masterpiece, best quality, soft lighting, depth of field, etc.) as long as
they do not change clothes, pose, or place. Prefer "masterpiece, best quality"
over score_* tags.

Output ONE line of tags only. No labels. No prose.
"""


def _phrase_to_tags(phrase: str) -> list[str]:
    text = (phrase or "").strip()
    if not text:
        return []
    if "," in text or ";" in text:
        parts = re.split(r"[,;]+", text)
        return [p.strip().replace(" ", "_") for p in parts if p.strip()]
    return [text.replace(" ", "_")]


def ledger_tag_bag(ledger: dict[str, str]) -> list[str]:
    bag: list[str] = []
    for key in (
        "wearing", "beat", "expression", "scene", "light", "bg", "frame",
        "wearing_b", "beat_b", "expression_b", "atmosphere", "look",
    ):
        bag.extend(_phrase_to_tags(ledger.get(key) or ""))
    seen: set[str] = set()
    out: list[str] = []
    for t in bag:
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out


# 二人のときの立ち位置は `identity.LEAD_SIDE` が正本。日記に「あなたは○のほう」
# と渡す側（`muse.service._which_one_is_me`）も同じ値を読む —— 別々に持つと、
# 絵とご本人の記憶が食い違う。

#: 監督が既に立ち位置を言っている回の目印。片方だけ既定を足すと二人とも同じ
#: 側になるので、**一つでも見つけたら既定を一つも足さない**。
_SIDE_NAMED_RE = re.compile(
    r"\b(left|right|leftmost|rightmost|foreground|background|behind|front)\b"
    r"|左|右|奥|手前|後ろ|背後|上段|下段",
    re.I,
)


def _sides_named(ledger: dict[str, str]) -> bool:
    """監督の指示に立ち位置が入っているか。入っていればそちらが勝つ。"""
    return any(
        _SIDE_NAMED_RE.search(str(ledger.get(k) or ""))
        for k in ("beat", "beat_b", "frame")
    )


def _telling_marks(who: dict[str, Any] | None) -> str:
    """その人を見分ける語（髪の色と体つき）。**二人のときだけ使う。**

    総監督（2026-09-10）の実機で、立ち位置は合ったのに**胸だけが入れ替わって**
    いた。タグの側は人ごとに分かれているが、散文の側は名前しか持っていなかった
    ので、名前と体つきを結ぶ手がかりが一つしか無かった。名前のすぐ隣に置く。
    """
    tags = [str(t).strip().replace("_", " ")
            for t in ((who or {}).get("identity_tags") or []) if str(t).strip()]
    if not tags:
        return ""
    hair = next((t for t in tags if "hair" in t), "")
    body = next(
        (t for t in tags
         if t.replace(" ", "_") in _BREAST_TAGS or t in ("flat chest",)),
        "",
    )
    marks = [m for m in (hair, body) if m]
    return f" ({', '.join(marks)})" if marks else ""


def scene_prose(
    ledger: dict[str, str],
    *,
    partner: bool = False,
    name_a: str = "",
    name_b: str = "",
    mark_a: str = "",
    mark_b: str = "",
) -> str:
    """Cinematic English SCENE paragraph for Anima (tags + longer NL).

    Ownership stays split: lead clothes/pose never attributed to the partner.
    Atmosphere / look thicken mood and render without triple-locking facts.
    """
    wearing = (ledger.get("wearing") or "").strip()
    beat = (ledger.get("beat") or "").strip()
    expression = (ledger.get("expression") or "").strip()
    scene = (ledger.get("scene") or "").strip()
    light = (ledger.get("light") or "").strip()
    bg = (ledger.get("bg") or "").strip()
    frame = (ledger.get("frame") or "").strip()
    wearing_b = (ledger.get("wearing_b") or "").strip()
    beat_b = (ledger.get("beat_b") or "").strip()
    expression_b = (ledger.get("expression_b") or "").strip()
    atmosphere = (ledger.get("atmosphere") or "").strip()
    look = (ledger.get("look") or "").strip()
    lead = (name_a or "She").strip() or "She"
    other = (name_b or "Her partner").strip() or "Her partner"

    if not any((
        wearing, beat, expression, scene, light, bg, frame,
        wearing_b, beat_b, expression_b, atmosphere, look,
    )):
        return ""

    parts: list[str] = []

    # Opening stage — one flowing sentence when possible.
    stage: list[str] = []
    if scene:
        if scene.lower().startswith(("at ", "in ", "on ", "inside ", "outside ")):
            stage.append(scene)
        else:
            stage.append(f"at {scene}")
    if bg and bg.lower() not in (scene or "").lower():
        stage.append(
            f"{bg} stretching behind them" if partner else f"{bg} stretching behind her"
        )
    if light:
        if any(w in light.lower() for w in ("light", "sun", "glow", "lamp", "neon", "rim")):
            stage.append(f"bathed in {light}")
        else:
            stage.append(f"lit by {light}")
    if stage:
        parts.append("The frame opens " + ", ".join(stage) + ".")
    else:
        parts.append(
            "The frame holds them in a quiet beat."
            if partner else
            "The frame holds her in a quiet beat."
        )

    # Lead — clothes + body + face as readable prose (not telegraphic labels).
    lead_bits: list[str] = []
    if wearing:
        lead_bits.append(f"wearing {wearing}")
    if beat:
        lead_bits.append(beat)
    if expression:
        lead_bits.append(f"with {expression} on her face")
    lead_line = ""
    if lead_bits:
        # Prefer named subject for Anima multi-char guidance.
        if wearing and beat and expression:
            lead_line = f"{lead}{mark_a} is {lead_bits[0]}, {lead_bits[1]}, {lead_bits[2]}."
        elif wearing and beat:
            lead_line = f"{lead}{mark_a} is {lead_bits[0]}, {lead_bits[1]}."
        else:
            lead_line = f"{lead}{mark_a} is " + ", ".join(lead_bits) + "."

    if partner or wearing_b or beat_b or expression_b:
        other_bits: list[str] = []
        if wearing_b:
            other_bits.append(f"wearing {wearing_b}")
        if beat_b:
            other_bits.append(beat_b)
        if expression_b:
            other_bits.append(f"with {expression_b} on her face")
        other_line = (
            f"{other}{mark_b} is " + ", ".join(other_bits) + "." if other_bits else ""
        )
        # **散文も左→右の順で読ませる。** タグの並びと同じ理由 —— 先に出た
        # ほうが左だと読まれるので、言葉と喧嘩させない。
        two = [lead_line, other_line]
        if identity.LEAD_SIDE == "right":
            two.reverse()
        parts.extend(x for x in two if x)
        # **どちらがどちら側かを言う（2026-09-10）。** 総監督「best practice で
        # 右と左って指示するといいらしい。それぞれがどっちにいるかを決めて、
        # かき分けてみよう」。監督が既に場所を言っている回は口を出さない。
        if not _sides_named(ledger):
            # この一文も左→右で読ませる。
            l_name, l_side = (
                (other, identity.side_of(lead=False)[0])
                if identity.LEAD_SIDE == "right" else
                (lead, identity.side_of(lead=True)[0])
            )
            r_name, r_side = (
                (lead, identity.side_of(lead=True)[0])
                if identity.LEAD_SIDE == "right" else
                (other, identity.side_of(lead=False)[0])
            )
            parts.append(
                f"{l_name} stands {l_side} of the frame; {r_name} {r_side}."
            )
        parts.append(
            f"Do not swap clothes, hairstyles or bodies between {lead} and "
            f"{other}; they share one place and one moment."
        )

    elif lead_line:
        parts.append(lead_line)

    if atmosphere:
        parts.append(
            f"The air feels {atmosphere} — mood first, not a new wardrobe."
        )
    if look:
        parts.append(f"Render the picture as {look}.")
    if frame:
        parts.append(f"Camera stays {frame}.")

    return " ".join(parts)


def _person_box(
    session: dict[str, Any],
    *,
    wearing: str,
    beat: str,
    expression: str = "",
    extra_beat_tags: list[str] | None = None,
    side: str = "",
) -> dict[str, list[str]]:
    """One Muse's dynamic tags — clothes / pose / face only.

    **禁止は台帳と突き合わせる（2026-09-09）。** 台帳がいま着ていると言って
    いる服は、たとえ一度脱いだ服でも絵に出す（`talk.live_banned`）。ここを
    素通しにしていたので「台帳は着ている、絵は着ていない」が起きていた。
    """
    wear = talk.filter_banned_tags(
        session, _phrase_to_tags(wearing), ledger={"wearing": wearing},
    )
    pose = _phrase_to_tags(beat)
    # **立ち位置は先頭に。** `assemble_from_boxes` は箱の中身を並んだ順に
    # 書き出し、位置＝優先度。後ろに付けると効きが落ちる。
    if side:
        pose.insert(0, side.replace(" ", "_"))
    for t in extra_beat_tags or []:
        tag = str(t or "").strip().replace(" ", "_")
        if tag and tag.lower() not in {p.lower() for p in pose}:
            pose.append(tag)
    face = _phrase_to_tags(expression)
    return {"wearing": wear, "beat": pose, "face": face}


def _frame_wide_tags(ledger: dict[str, str]) -> list[str]:
    """Shared picture tags — place / light / bg / camera / mood. Never clothes or hair."""
    bag: list[str] = []
    for key in ("scene", "light", "bg", "frame", "atmosphere"):
        bag.extend(_phrase_to_tags(ledger.get(key) or ""))
    seen: set[str] = set()
    out: list[str] = []
    for t in bag:
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out


def _combined_style(session: dict[str, Any], ledger: dict[str, str]) -> str:
    """Panel style input + conversation-driven look (look wins as append)."""
    inputs = session.get("inputs") or {}
    base = str(inputs.get("style") or "").strip()
    look = str(ledger.get("look") or "").strip()
    if base and look:
        return f"{base}, {look}"
    return look or base


def assemble_prompt(
    session: dict[str, Any],
    ledger: dict[str, str],
    *,
    support_tags: list[str] | None = None,
    scene_override: str | None = None,
    enhance_quality: bool | None = None,
) -> str:
    """Identity-first positive with per-person ownership (Muse box path).

    Lead clothes/pose/face never share a flat bag with the partner's — same
    rule as classic Muse ``assemble_from_boxes`` so hair and outfits do not swap.
    Final string is Anima-hygiened (spaces, quality prefix, blank-line prose).
    """
    char = session.get("character") or {}
    partner = session.get("partner_character") or {}
    has_partner = bool(partner and str(partner.get("character_id") or "").strip())
    name_a = str(char.get("name_ja") or char.get("name") or "Lead")
    name_b = str(partner.get("name_ja") or partner.get("name") or "Partner")
    inputs = session.get("inputs") or {}
    framing = str(inputs.get("framing") or "auto")
    style = _combined_style(session, ledger)
    quality_on = (
        bool(enhance_quality) if enhance_quality is not None
        else bool(inputs.get("enhance_quality"))
    )
    prose = (
        scene_override if scene_override is not None
        else scene_prose(
            ledger, partner=has_partner, name_a=name_a, name_b=name_b,
            # 見分けの語は**二人のときだけ**。一人の散文は今までのまま。
            mark_a=_telling_marks(char) if has_partner else "",
            mark_b=_telling_marks(partner) if has_partner else "",
        )
    )
    raw_support = talk.filter_banned_tags(
        session,
        [str(t).strip().replace(" ", "_") for t in (support_tags or []) if str(t).strip()],
        ledger=ledger,
    )
    quality_tags, atmosphere = anima.split_quality_support(raw_support)

    cast = [char]
    lead_extra: list[str] = []
    # **二人のときだけ、立ち位置を決めて書き分ける（2026-09-10）。** 総監督
    # 「best practice で右と左って指示するといいらしい」。監督が既に場所を
    # 言っている回は、そちらが勝つので既定を**一つも**足さない —— 片方だけ
    # 足すと二人とも同じ側になる。**一人のときは常に空。**
    side_a, side_b = ("", "")
    if has_partner and not _sides_named(ledger):
        side_a = identity.side_of(lead=True)[0]
        side_b = identity.side_of(lead=False)[0]
    people = [
        _person_box(
            session,
            wearing=str(ledger.get("wearing") or ""),
            beat=str(ledger.get("beat") or ""),
            expression=str(ledger.get("expression") or ""),
            extra_beat_tags=lead_extra,
            side=side_a,
        ),
    ]
    if has_partner:
        cast.append(partner)
        people.append(
            _person_box(
                session,
                # **相方にも顔を（2026-09-10）。** ここは長らく空文字だった
                # ——「主演の顔を B に写さないため」という理由だったが、台帳に
                # `expression_b` が無かったので、相方は**顔が一語も入らない
                # まま**撮られていた。欄ができたので、彼女自身の顔を渡す。
                wearing=str(ledger.get("wearing_b") or ""),
                beat=str(ledger.get("beat_b") or ""),
                expression=str(ledger.get("expression_b") or ""),
                side=side_b,
            ),
        )

    # **読み順と立ち位置を揃える（2026-09-10）。** 総監督「プロンプトはあって
    # いそうなのに画像は反転していることが多い」。
    #
    # 名前の並びそのものが位置の合図になる —— 頭の `2girls, Mio and Asahi,` と
    # 人ごとの箱の順で「先に出たほうが左」と読まれる。主演を右にした日から、
    # **並び（Mio が先＝左）と言葉（Mio: on the right）が喧嘩していた**:
    #
    #     2girls, Mio and Asahi,          ← 並びは Mio が左と言っている
    #     Mio: on the right, …            ← 言葉は右と言っている
    #
    # ComfyUI 側に反転はない（実行済みグラフを確認・flip 系ノード無し）。
    # 喧嘩をやめさせる —— **左にいるほうを先に書く。** どちら側にしても揃う。
    if has_partner and side_a and side_b:
        left_word = identity.SIDE_WORDS["left"][0]
        if side_b == left_word:
            cast = [cast[1], cast[0]]
            people = [people[1], people[0]]

    boxed = identity.assemble_from_boxes(
        cast=cast,
        people=people,
        frame_wide=_frame_wide_tags(ledger),
        style=style,
        framing=framing,
        scene=prose,
        support=atmosphere,
    )
    if not boxed:
        # Fallback (no usable identity tags): flat path, still without mixing bags.
        identity_tags = [
            t for t in (char.get("identity_tags") or [])
            if str(t).strip() and str(t).strip().lower() not in {"1girl", "solo"}
        ]
        bag = talk.filter_banned_tags(
            session,
            ledger=ledger,
            tags=_phrase_to_tags(str(ledger.get("wearing") or ""))
            + _phrase_to_tags(str(ledger.get("beat") or ""))
            + _phrase_to_tags(str(ledger.get("expression") or ""))
            + _frame_wide_tags(ledger),
        )
        if atmosphere:
            bag = merge_support_tags(bag, atmosphere, authority=bag)
        boxed = identity.assemble_positive(
            ["1girl", *identity_tags] if identity_tags else ["1girl"],
            ", ".join(bag),
            prose,
            framing=framing,
            style=style,
            cast=[char] if char else None,
        )

    lettering_raw = str(ledger.get("lettering") or "").strip()
    lettering = [lettering_raw] if lettering_raw else []
    return anima.format_for_anima(
        boxed or "",
        quality_tags=quality_tags,
        lettering=lettering,
        enhance_quality=quality_on,
    )


_PROSE_DENSIFY = """You densify a shot SCENE paragraph for Anima / FLUX-natural.
Keep EVERY fact from LEDGER and BASE PROSE unchanged — clothes, pose, face,
place, light, background, camera, atmosphere, look. Do not rename garments.
Do not move the place. Do not invent props that fight the ledger.
If two people are present, NEVER swap clothes, hairstyles, or body traits
between them — keep each person's ownership exact.
Lean into ATMOSPHERE and LOOK when present: sensory mood and render medium
(cel, fantasy glow, watercolor bleed, etc.) without adding new wardrobe.

VISIBLE CONSEQUENCES (required when state implies them):
Read beat / atmosphere / frame / light as a photograph — name what the camera
would actually SEE because of that state, not abstract feelings alone.
Cause → effect examples (use only when ledger already has the cause):
- Wind → streaming hair / flyaways AND often nape when rear/side/looking-back;
  loose hems and sleeves lift with the same gust (not a new outfit).
- Arms raised / stretch → underarm tautness, ribcage lift, hem riding up a little.
- Holding / gripping → knuckle edges, wrist angle, shadow of the prop on the palm.
- Sitting / kneeling → cloth folds at knees and where body meets the seat.
- Looking down / up → lid/lash catchlight, chin tuck or throat open to light.
- Backlight / silhouette → rim on hair and cheek edge; face softer in shade.
- Tears / crying → glossy lids, a wet track on the cheek — not abstract sadness.
- Wet / rain → darkened clinging fabric, damp sheen on skin and sleeves.
- from_behind → nape, shoulder blades, cloth fall — not a frontal face unless
  looking_back is already in the ledger.
- Running / motion → hair and hems trail a half-beat behind the planted foot.
Do NOT invent new garments, places, or props. Do NOT write consequences back as
new ledger fields — only render them in the prose.

Write 3–5 flowing English sentences (about 90–180 words). Name each person,
then their appearance — do not list bare names alone.
Do NOT restate the same fact three times. Do NOT dump a "Keep exactly" list.
No (tag:weight). No comma-tag lists. Output the paragraph only.
"""


async def densify_scene_prose(
    ollama,
    *,
    model: str,
    ledger: dict[str, str],
    base_prose: str,
    num_ctx: int | None = None,
) -> str:
    """Optional LLM thicken — ledger facts stay absolute; consequences are craft-only."""
    if not base_prose.strip() or ollama is None:
        return base_prose
    hint_block = ""
    prompt = (
        f"{_PROSE_DENSIFY}\n\n"
        f"LEDGER:\n"
        f"wearing: {ledger.get('wearing')}\n"
        f"beat: {ledger.get('beat')}\n"
        f"expression: {ledger.get('expression')}\n"
        f"scene: {ledger.get('scene')}\n"
        f"light: {ledger.get('light')}\n"
        f"bg: {ledger.get('bg')}\n"
        f"frame: {ledger.get('frame')}\n"
        f"wearing_b: {ledger.get('wearing_b')}\n"
        f"beat_b: {ledger.get('beat_b')}\n"
        f"lettering: {ledger.get('lettering')}\n"
        f"atmosphere: {ledger.get('atmosphere')}\n"
        f"look: {ledger.get('look')}\n"
        f"{hint_block}\n"
        f"BASE PROSE:\n{base_prose}\n"
    )
    try:
        # **thinking は明示して切る（2026-09-07）。** 送らないと模型側の
        # 既定に従い、この一回が 14〜15秒（`think=False` なら 1.1〜1.6秒・
        # 実測 26B・同じプロンプト n=2）。**出力も薄くなる**（67〜91字 対
        # 141〜146字）。1ターンに数回叩くので、分単位の待ちになって描画まで
        # 届かない。Muse は `chain._call` が毎回 `think=False` を送っている。
        raw = await ollama.generate_text(
            prompt, model=model or None, think=False,
            options={"num_ctx": num_ctx} if num_ctx else None,
        )
    except Exception:
        logger.exception("[muse_refine] prose densify failed")
        return base_prose
    text = " ".join((raw or "").strip().split())
    if len(text) < 40:
        return base_prose
    # Soft guard: require scene or wearing stem to survive.
    must = []
    for key in ("scene", "wearing", "beat"):
        phrase = (ledger.get(key) or "").strip().lower()
        if phrase:
            # first token-ish chunk
            must.append(phrase.split(",")[0].strip().split()[0])
    low = text.lower()
    if must and not any(m and m in low for m in must):
        return base_prose
    return text[:900]


def merge_support_tags(
    base: list[str],
    support: Iterable[str],
    *,
    authority: Iterable[str],
    cap: int = 24,
) -> list[str]:
    """Append chosen support tags that do not collide with authority stems."""
    auth = {a.lower() for a in authority}
    auth_stems = {a.split("_")[0] for a in auth if a}
    out = list(base)
    seen = {t.lower() for t in out}
    for raw in support:
        tag = str(raw or "").strip().replace(" ", "_")
        if not tag:
            continue
        low = tag.lower()
        if low in seen or low in auth:
            continue
        stem = low.split("_")[0]
        if stem and stem in auth_stems and low not in auth:
            if stem not in {"1girl", "solo", "looking", "open", "closed"}:
                if any(stem == a.split("_")[0] for a in auth if "_" in a):
                    continue
        out.append(tag)
        seen.add(low)
        if len(out) - len(base) >= cap:
            break
    return out


async def quality_enrich(
    ollama,
    *,
    model: str,
    ledger: dict[str, str],
    base_tags: list[str],
    num_ctx: int | None = None,
) -> list[str]:
    """絵作りの語を足す。**WD14 は使わない（2026-09-09）。**

    総監督「muse refine の WD14 ですが、やっぱり以前検討した通り、**不要な単語が
    大量に検出される**ため、機能を削除して」。語彙の近傍は場面と関係のない服や
    小道具を連れてくる —— classic 側で欄ごとに引き直しても雑音が半分近かった。

    （classic Muse の推薦 `service._suggest_tags` は残す。あちらは欄ごとに引いて
    彼女に渡し、彼女が落とす形で、そちらは実測で 5/5 きれいだった）
    """
    prompt = (
        f"{_QUALITY_SYSTEM}\n\n"
        f"LEDGER:\n"
        f"wearing: {ledger.get('wearing')}\n"
        f"beat: {ledger.get('beat')}\n"
        f"scene: {ledger.get('scene')}\n"
        f"light: {ledger.get('light')}\n"
        f"bg: {ledger.get('bg')}\n\n"
        f"BASE TAGS:\n{', '.join(base_tags)}\n"
    )
    try:
        # **thinking は明示して切る（2026-09-07）。** 送らないと模型側の
        # 既定に従い、この一回が 14〜15秒（`think=False` なら 1.1〜1.6秒・
        # 実測 26B・同じプロンプト n=2）。**出力も薄くなる**（67〜91字 対
        # 141〜146字）。1ターンに数回叩くので、分単位の待ちになって描画まで
        # 届かない。Muse は `chain._call` が毎回 `think=False` を送っている。
        raw = await ollama.generate_text(
            prompt, model=model or None, think=False,
            options={"num_ctx": num_ctx} if num_ctx else None,
        )
    except Exception:
        logger.exception("[muse_refine] quality enrich failed")
        return []
    line = (raw or "").strip().splitlines()[0] if raw else ""
    parts = [p.strip().replace(" ", "_") for p in line.split(",") if p.strip()]
    auth_phrases = {
        (ledger.get(k) or "").strip().lower().replace(" ", "_")
        for k in ("wearing", "beat", "scene", "bg")
        if (ledger.get(k) or "").strip()
    }
    return [p for p in parts if p.lower() not in auth_phrases]


async def rebuild_craft(
    db,
    ollama,
    session: dict[str, Any],
) -> dict[str, Any]:
    """Refresh craft from ledger.

    **WD14 は外した（2026-09-09）** —— 総監督「不要な単語が大量に検出される」。
    """
    import time

    inputs = session.get("inputs") or {}
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    num_ctx = refine_num_ctx(session)
    quality_tags: list[str] = []

    base_bag = talk.filter_banned_tags(session, ledger_tag_bag(led), ledger=led)
    if bool(inputs.get("enhance_quality")) and ollama is not None:
        t0 = time.monotonic()
        model = str(inputs.get("model") or "")
        quality_tags = await quality_enrich(
            ollama, model=model, ledger=led, base_tags=base_bag,
            num_ctx=num_ctx,
        )
        debug_mod.stage(session, "quality_enrich", t0)
        debug_mod.note(
            session, "quality_enrich",
            detail="free atmosphere tags",
            quality_tags=quality_tags[:40],
        )

    chosen = list(quality_tags)
    partner = session.get("partner_character") or {}
    has_partner = bool(partner.get("character_id"))
    char = session.get("character") or {}
    name_a = str(char.get("name_ja") or char.get("name") or "Lead")
    name_b = str(partner.get("name_ja") or partner.get("name") or "Partner")
    prose = scene_prose(
        led, partner=has_partner, name_a=name_a, name_b=name_b,
        mark_a=_telling_marks(char) if has_partner else "",
        mark_b=_telling_marks(partner) if has_partner else "",
    )
    # **「観測」は外した（2026-09-10）。** 総監督「観測という機能はあまり有効に
    # 働かないので削除。キーワードベースでほとんど使われていない」。風・後ろ姿を
    # 正規表現で拾って散文とタグに足していた仕掛け（`visible_consequence_cues`）
    # ごと落とした。densify を起こす条件も、その分だけ素直になる。
    want_dense = bool(inputs.get("enhance_quality")) or bool(
        (led.get("atmosphere") or "").strip() or (led.get("look") or "").strip()
    )
    densified = False
    densify_reason = ""
    if want_dense and ollama is not None and prose:
        t0 = time.monotonic()
        model = str(inputs.get("model") or "")
        denser = await densify_scene_prose(
            ollama, model=model, ledger=led, base_prose=prose,
            num_ctx=num_ctx,
        )
        debug_mod.stage(session, "prose_densify", t0)
        if denser and denser != prose:
            densified = True
            densify_reason = (
                "enhance_quality" if inputs.get("enhance_quality") else "atmosphere_or_look"
            )
            debug_mod.note(
                session, "prose_densify",
                detail=denser[:240],
                reason=densify_reason,
            )
            prose = denser
    prompt = assemble_prompt(
        session, led,
        support_tags=chosen or None,
        scene_override=prose,
        enhance_quality=bool(inputs.get("enhance_quality")),
    )
    locale = str(inputs.get("locale") or "ja")
    craft = dict(session.get("craft") or {})
    craft["prompt"] = prompt
    craft["now"] = ledger_mod.now_line(led, locale=locale)
    craft["tags"] = ", ".join(ledger_tag_bag(led))
    craft["scene"] = prose
    # Keep names clear in the panel / debug.
    craft["quality_tags"] = ", ".join(quality_tags)
    craft["support_tags"] = ", ".join(chosen)
    # 撮る直前に組み直したので、もう古くない（`touch_craft` の旗を降ろす）。
    craft["stale"] = False
    session["craft"] = craft
    session["refine_ledger"] = led
    return session


def touch_craft(session: dict[str, Any]) -> dict[str, Any]:
    """会話のターン用の、**模型を使わない** craft 更新。（2026-09-10）

    総監督「撮影に入らないときの会話のみの回答はもっと早くしてほしい」。

    実機の記録（`stage_ms`）を読むと、会話だけの一手にこれだけ乗っていた:

        writer                4.45s   台帳を書く（要る）
        quality_enrich        4.42s   ┐ `rebuild_craft` の中身。どちらも模型
        prose_densify         6.93s   ┘
        actress              21.93s   彼女が喋る（要る）
        assemble_after_propose 9.55s  彼女が表情を足したので、また組み直し
        verify                6.81s
        ──────────────────────────── 合計 ≈54秒

    組み上げた `craft["prompt"]` を使うのは**試し撮りと本番だけ**で、そちらは
    もう自前で `rebuild_craft` を呼んでいる。会話の途中で組む理由がない。

    ここでやるのは純関数だけ —— `now`（正本の一行）と `tags`。**台帳が正本**
    なので、画面の台帳欄と NOW 行は今まで通り毎ターン動く。散文とタグの
    組み上げだけが撮る時まで待つ。`stale` はそれを画面に言うための旗。
    """
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    inputs = session.get("inputs") or {}
    locale = str(inputs.get("locale") or "ja")
    craft = dict(session.get("craft") or {})
    craft["now"] = ledger_mod.now_line(led, locale=locale)
    craft["tags"] = ", ".join(talk.filter_banned_tags(session, ledger_tag_bag(led), ledger=led))
    craft["stale"] = True
    session["craft"] = craft
    session["refine_ledger"] = led
    return session

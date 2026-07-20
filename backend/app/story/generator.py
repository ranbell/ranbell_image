"""Chronicle pipeline: prompt builders and output parsers.

Pure prompt/parse logic only, so it can be unit-tested with a mocked Ollama
client. Orchestration (job streaming, DB writes, ComfyUI submission) lives in
jobs/runners.py.

Stage 1 (VLM)  — visual vocabulary extraction (wd14_tags reused when available)
Stage 2 (LLM)  — title + overall summary + three acts, streamed with
                 [TITLE]/[OVERALL]/[PAST]/[PRESENT]/[FUTURE] markers
Stage 3 (LLM)  — per-axis image prompt: danbooru tag line (Pass 1 ground truth)
                 + Refine Visual Script prose + categorized *_TAGS footer;
                 ComfyUI gets tag_line + prose; categories emit on axis_prompt SSE

Stories are authored in the user's locale; when that is Japanese they are
translated to English before Stage 3 (image prompts are always English).
"""

import itertools
import json
import logging
import re

from ..prompt.visual_spec import (
    CHRONICLE_CAT_FIELDS,
    DEFAULT_PROSE_PARAGRAPHS,
    chronicle_labeled_tag_footer,
    clamp_prose_paragraphs,
    ensure_pose_tags_min_words,
    merge_category_tags,
    parse_visual_script as parse_visual_script_category_tags,
    pose_word_count,
    visual_script_length_line,
)
from ..tags.catalog import (
    ACCESSORIES as _ACCESSORIES,
    ABSTRACT_BG as _ABSTRACT_BG,
    COUNT as _COUNT,
    EXPRESSION_TAGS as _EXPRESSION_TAGS,
    EXPRESSION_TOKENS as _EXPRESSION_TOKENS,
    PROPS as _PROPS,
    VISUAL_LIGHTING as _VISUAL_LIGHTING,
    get_tag_axis,
)
from ..tags.subject_anchors import (
    SUBJECT_ANCHOR_TAGS as _SUBJECT_ANCHORS,
    insert_after_anchors,
)
from .topic_anchors import (  # noqa: F401 — re-export for callers
    _is_ja_script_token,
    topic_anchor_groups,
    topic_anchor_tokens,
)

logger = logging.getLogger(__name__)

AXES = ("past", "present", "future")

# Temporal spacing between the three acts, selected by the UI slider
TIME_SCALES = {
    "minutes": "a few minutes",
    "tens_of_minutes": "tens of minutes",
    "hours": "a few hours",
    "days": "a few days",
    "months": "a few months",
    "years": "a few years",
    "decades": "several decades",
}


def normalize_time_scale(scale: str | None, default: str = "years") -> str:
    """Return a known Chronicle time_scale key, else ``default``.

    Unknown / empty values used to silently fall through to the years branch
    of ``_ELAPSED_UNIT.get(..., years)``, which made 'hours' look like '数年'.
    """
    s = str(scale or "").strip()
    if s in TIME_SCALES:
        return s
    return default if default in TIME_SCALES else "years"

# Tags that describe rating/quality metadata rather than appearance
_META_TAG_RE = re.compile(
    r"^(masterpiece|best_quality|high_quality|low_quality|worst_quality|"
    r"absurdres|highres|lowres|sensitive|explicit|questionable|general|"
    r"commentary.*|translated|.*_username|.*_commentary)$"
)


def character_tags_from_wd14(wd14_tags: list[str], limit: int = 40) -> list[str]:
    """Drop rating/quality meta tags; keep appearance-relevant wd14 tags."""
    return [t for t in wd14_tags if not _META_TAG_RE.match(t)][:limit]


# ── Chronicle concreteness: HARD RULES + WD14 seed tags ───────────────────────
#
# Small VLMs drift into abstract "fate / loneliness" essays. The fix is twofold:
# (1) put a short HARD RULES block at the very top of story prompts, and
# (2) force a sampled WD14 seed-tag set into drawable events (not tag lists).

_GENERIC_SEED_SKIP = frozenset({
    "1girl", "2girls", "3girls", "multiple_girls", "solo", "solo_focus",
    "looking_at_viewer", "simple_background", "white_background",
    "absurdres", "highres", "masterpiece", "best_quality", "official_art",
    "portrait", "upper_body", "cowboy_shot", "full_body", "close-up",
})

# Heuristic: tags that look like physical props / tools for forced_motif.
_MOTIF_OBJECT_HINTS = (
    "cup", "mug", "glass", "book", "letter", "paper", "memo", "note", "envelope",
    "bag", "phone", "umbrella", "flower", "key", "ring", "box", "bottle",
    "camera", "pen", "pencil", "sword", "knife", "plate", "bowl", "tray",
    "machine", "lamp", "candle", "ticket", "card", "scarf", "hat", "glove",
)


def chronicle_hard_rules_preamble(*, locale: str = "en", has_user_topic: bool = False) -> str:
    """Short HARD RULES block — must be the first lines of story prompts."""
    if locale == "ja":
        topic_rule = (
            "2. お題がある場合: 抽象テーマでもよいが、各幕はそれを具体的な動作に翻訳すること。"
            "お題を捨てて画像の見たまま再描写するな。\n"
            if has_user_topic else
            "2. 禁止: 運命、想いだけ、抽象テーマのみ、視線を遠くにやるだけ、気分だけの幕、比喩だけの転換。\n"
        )
        return (
            "【最優先ルール — 必ず守ること】\n"
            "1. 書くのは写真に撮れる具体的な出来事だけ（誰が・何を持って／何をして・どこで）。\n"
            f"{topic_rule}"
            "3. 撮れないなら書き直せ。emotion/tone は動作の色付けのみ。気分で動作を置き換えるな。\n"
        )
    topic_rule = (
        "2. When a USER TOPIC is given: abstract themes are allowed ONLY as that topic — "
        "translate it into concrete drawable actions in every act. NEVER abandon the "
        "topic to re-describe the base image at face value.\n"
        if has_user_topic else
        "2. FORBIDDEN: fate, destiny, vague longing, abstract themes without action, "
        "mood-only beats, metaphorical-only turns, \"gazed into the distance\".\n"
    )
    return (
        "HARD RULES (read first — violate none):\n"
        "1. Write ONLY concrete, drawable events: who does what with which object, where.\n"
        f"{topic_rule}"
        "3. Every act must be a scene you could photograph. If it cannot be drawn, rewrite it.\n"
        "4. emotion/tone only COLOR the action — never replace the action with mood alone.\n"
    )


def chronicle_seed_tags_block(
    seed_tags: list[str] | None,
    *,
    forced_motif: str = "",
    must_k: int = 3,
    locale: str = "en",
) -> str:
    """Mandatory WD14 seed-tag injection block (events, not tag lists)."""
    tags = [t for t in (seed_tags or []) if t]
    if not tags and not forced_motif:
        return ""
    tag_line = ", ".join(tags) if tags else "(none)"
    k = min(must_k, len(tags)) if tags else 0
    if locale == "ja":
        motif_line = (
            f"モチーフ物体（固定）: {forced_motif} — 三幕で意味が変容する同一の物として使え。\n"
            if forced_motif else ""
        )
        must_line = (
            f"SEED TAGS のうち少なくとも {k} 個を、誰が何を持って／何をして／どこにいるかとして"
            "三幕に分散して織り込め。タグ名を並べるな。出来事に翻訳せよ。"
            "1幕に全タグを詰め込むな。\n"
            if k else ""
        )
        return (
            f"★ SEED TAGS（描ける事実の語彙 — 必須）★\n{tag_line}\n"
            f"{motif_line}{must_line}"
        )
    motif_line = (
        f"FIXED MOTIF OBJECT: {forced_motif} — reuse it across acts with shifting meaning.\n"
        if forced_motif else ""
    )
    must_line = (
        f"Weave at least {k} of the SEED TAGS into the three acts as who/what/where "
        "physical facts. Translate tags into events — do NOT list tag names. "
        "Spread them across acts; do not dump every tag into one act.\n"
        if k else ""
    )
    return (
        f"★ SEED TAGS (drawable vocabulary — MANDATORY) ★\n{tag_line}\n"
        f"{motif_line}{must_line}"
    )


def pick_forced_motif(seed_tags: list[str], *, rng=None) -> str:
    """Pick one physical-object-like tag as motif; empty if none suitable."""
    import random as _random
    r = rng or _random
    objects: list[str] = []
    for t in seed_tags:
        low = t.lower().replace(" ", "_")
        if low in _GENERIC_SEED_SKIP or _META_TAG_RE.match(low):
            continue
        if any(h in low for h in _MOTIF_OBJECT_HINTS):
            objects.append(t)
    if not objects:
        return ""
    return r.choice(objects)


def filter_story_seed_pool(
    names: list[str],
    *,
    removal: set[str] | None = None,
    exclude: set[str] | None = None,
) -> list[str]:
    """Filter WD14 hit names into a story-seed pool (drawable, non-generic)."""
    from ..invoke.vocab_bank import _is_species_tag

    rem = removal or set()
    ex = {x.lower().replace(" ", "_") for x in (exclude or set())}
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip().replace(" ", "_")
        if not name:
            continue
        key = name.lower()
        if key in seen or key in rem or key in ex:
            continue
        if key in _GENERIC_SEED_SKIP or _META_TAG_RE.match(key):
            continue
        if _is_species_tag(key):
            continue
        seen.add(key)
        out.append(name)
    return out


def sample_story_seed_tags(
    pool: list[str],
    *,
    n_min: int = 6,
    n_max: int = 12,
    rng=None,
) -> list[str]:
    """Random sample 6–12 tags from a filtered pool (or all if smaller)."""
    import random as _random
    r = rng or _random
    if not pool:
        return []
    n = min(len(pool), r.randint(n_min, n_max) if len(pool) >= n_min else len(pool))
    if n >= len(pool):
        return list(pool)
    return r.sample(pool, n)


def translation_values_complete(source, translated, *, min_ratio: float = 0.35) -> bool:
    """True if translated structure covers source keys with non-trivial values."""
    if isinstance(source, dict) and isinstance(translated, dict):
        if not source:
            return True
        ok = 0
        need = 0
        for k, v in source.items():
            if isinstance(v, (dict, list)):
                if not translation_values_complete(v, translated.get(k), min_ratio=min_ratio):
                    return False
                continue
            src = str(v or "").strip()
            if not src:
                continue
            need += 1
            dst = str((translated or {}).get(k) or "").strip()
            if dst and len(dst) >= max(1, int(len(src) * min_ratio)):
                ok += 1
        return need == 0 or ok >= max(1, need - 1) if need > 2 else ok == need
    if isinstance(source, list) and isinstance(translated, list):
        if len(translated) < max(1, len(source) - 1) and len(source) > 1:
            return False
        for s, t in zip(source, translated):
            if not translation_values_complete(s, t, min_ratio=min_ratio):
                return False
        return True
    if isinstance(source, str):
        src = source.strip()
        dst = str(translated or "").strip()
        if not src:
            return True
        return bool(dst) and len(dst) >= max(1, int(len(src) * min_ratio))
    return translated is not None


# ── Stage 1: visual vocabulary extraction ─────────────────────────────────────

# Interpretive sections appended to both vision prompt variants. They feed the
# story stage as inspiration; the literal sections above them stay the anchor
# for the base act (split apart again by split_vision_sections).
_VISION_NARRATIVE_SECTIONS = (
    "STORY HOOKS: 2-3 speculative one-liners — what might have JUST happened "
    "here, and what might be ABOUT to happen\n"
    "OFF-FRAME: what the world just outside this frame plausibly contains\n"
    "SYMBOLS: 1-3 objects or details in the image with narrative potential\n"
    "ESSENCE: the abstract role of this place/moment, in one phrase "
    '(e.g. "a threshold between two worlds")\n'
)


def build_vision_prompt(full_extraction: bool) -> str:
    """VLM prompt for the base image.

    full_extraction=True (external image without wd14_tags): describe everything.
    full_extraction=False: wd14_tags already cover appearance — only describe
    what tags cannot express (environment, mood, narrative reading).
    """
    if full_extraction:
        return (
            "Describe this image precisely for an illustrator, in English.\n"
            "Cover, in short labeled sections:\n"
            "CHARACTER: physical traits (hair color/length, eye color, body, age impression)\n"
            "OUTFIT: clothing and accessories\n"
            "SCENE: background, environment, time of day, weather\n"
            "MOOD: overall mood and emotional tone\n"
            f"{_VISION_NARRATIVE_SECTIONS}"
            "Keep CHARACTER/OUTFIT/SCENE/MOOD concrete and visual — no names, "
            "no guesses there. Speculation belongs ONLY in the last four sections."
        )
    return (
        "Describe this image for an illustrator, in English.\n"
        "Cover, in short labeled sections:\n"
        "SCENE: background, environment, time of day, weather\n"
        "MOOD: overall mood, atmosphere and emotional tone\n"
        f"{_VISION_NARRATIVE_SECTIONS}"
        "Do NOT describe the character's appearance or clothing. "
        "Keep SCENE/MOOD concrete and visual; speculation belongs ONLY in the "
        "last four sections."
    )


# Tolerant to markdown decoration: **STORY HOOKS:**, ## OFF-FRAME: , SYMBOLS —
_VISION_NARRATIVE_RE = re.compile(
    r"(?im)^[ \t>#*-]{0,4}\**\s*(?:STORY[ _]?HOOKS?|OFF[-_ ]?FRAME|SYMBOLS|ESSENCE)\s*\**\s*[:：]"
)


def split_vision_sections(text: str) -> tuple[str, str]:
    """Split VLM output into (literal_desc, narrative_hooks).

    Cuts at the first narrative label (STORY HOOKS/OFF-FRAME/SYMBOLS/ESSENCE).
    No labels found → (text, "") so older-style output keeps working.
    """
    m = _VISION_NARRATIVE_RE.search(text)
    if not m:
        return text.strip(), ""
    return text[: m.start()].strip(), text[m.start():].strip()


# ── Stage 2: title + overall + three acts ─────────────────────────────────────

# Δ phrase per scale — one-step and two-step totals. The two-step form is used
# for the far act when the base is at one end of the timeline (e.g. base=past
# → future is two Δ away). Deliberately non-uniform (a Δ of "years" doubles
# to "several years", not "eight years") so the model reads "each act is a
# distinct volume opened later on the timeline", not a rigid arithmetic step.
_ELAPSED_UNIT: dict[str, tuple[str, str]] = {
    "minutes":         ("A FEW MINUTES",    "SEVERAL MINUTES"),
    "tens_of_minutes": ("TENS OF MINUTES",  "ABOUT AN HOUR"),
    "hours":           ("A FEW HOURS",      "MOST OF A DAY"),
    "days":            ("A FEW DAYS",       "OVER A WEEK"),
    "months":          ("A FEW MONTHS",     "NEARLY A YEAR"),
    "years":           ("A FEW YEARS",      "SEVERAL YEARS"),
    "decades":         ("SEVERAL DECADES",  "A LIFETIME"),
}
_ELAPSED_UNIT_JA: dict[str, tuple[str, str]] = {
    "minutes":         ("数分",   "十数分"),
    "tens_of_minutes": ("十数分", "約1時間"),
    "hours":           ("数時間", "半日"),
    "days":            ("数日",   "1週間以上"),
    "months":          ("数ヶ月", "1年近く"),
    "years":           ("数年",   "十数年"),
    "decades":         ("数十年", "一生分"),
}


def _elapsed_time_header(
    *, base_axis: str, time_scale: str, locale: str = "en"
) -> str:
    """Volume-ledger header shared by every Chronicle LLM prompt.

    Frames the three acts as distinct volumes on a timeline, anchored on the
    user-selected base_axis (t=0) and expressed as elapsed deltas — "N later"
    or "N earlier" — instead of the older "BEFORE/AFTER the image" phrasing.
    The intent is to make the "next-volume feel" of past/present/future
    palpable at the front of the prompt so the LLM stops re-shooting the
    same moment three times.
    """
    one, two = _ELAPSED_UNIT.get(time_scale, _ELAPSED_UNIT["years"])
    one_ja, two_ja = _ELAPSED_UNIT_JA.get(time_scale, _ELAPSED_UNIT_JA["years"])
    base = base_axis.lower() if base_axis else "present"
    if base not in AXES:
        base = "present"

    # Map each non-base act to its Δ magnitude ("one" or "two" step)
    # and its temporal direction relative to base.
    idx_base = AXES.index(base)
    labels_en: dict[str, str] = {}
    labels_ja: dict[str, str] = {}
    for axis in AXES:
        if axis == base:
            continue
        i = AXES.index(axis)
        steps = abs(i - idx_base)
        forward = i > idx_base
        phrase = one if steps == 1 else two
        phrase_ja = one_ja if steps == 1 else two_ja
        dir_word = "LATER" if forward else "EARLIER"
        dir_ja = "後" if forward else "前"
        labels_en[axis] = f"{phrase} {dir_word}"
        labels_ja[axis] = f"{phrase_ja}{dir_ja}"

    if locale == "ja":
        lines = [
            "⏳ 時間軸 — ベースからの経過 ⏳",
            f"BASE = [{base.upper()}] (t = 0, これが基準画像そのもの)",
        ]
        for axis in AXES:
            if axis == base:
                continue
            lines.append(f"[{axis.upper()}] = {labels_ja[axis]} 経過")
        lines.append(
            "3つの幕はタイムライン上に順に開かれる別々の「巻」として扱うこと。"
            "同じ瞬間を3回撮り直すのではなく、読者がページをめくって次の巻を開く感覚で書く。"
        )
        return "\n".join(lines) + "\n"

    lines = [
        "⏳ TIME AXIS — ELAPSED FROM BASE ⏳",
        f"BASE = [{base.upper()}] (t = 0, this IS the base image)",
    ]
    for axis in AXES:
        if axis == base:
            continue
        lines.append(f"[{axis.upper()}] = {labels_en[axis]}")
    lines.append(
        "Treat each act as a distinct volume opened later on the timeline — "
        "the reader is turning the page to the next volume, not re-shooting "
        "the same moment three times."
    )
    return "\n".join(lines) + "\n"


def _tone_line(tone: str, locale: str = "en") -> str:
    """Overall tonal bias for the story, threaded into candidates + story stages."""
    t = (tone or "bright").strip().lower()
    if locale == "ja":
        return {
            "bright": "\nトーン: 希望・温かさ・前進を基調に。彼女が成長し、つながり、発見する物語に。"
                      "悲劇的・破滅的な結末は、お題が明示的に求めない限り避ける。\n",
            "neutral": "\nトーン: バランス重視。無理な幸福も陰惨さも避け、自然な起伏で。\n",
            "dark": "\nトーン: 緊張・ほろ苦さ・不穏さを許容してよい。\n",
        }.get(t, "")
    return {
        "bright": "\nTONE: keep it hopeful, warm and forward-moving — she grows, "
                  "connects or discovers. Avoid grim, tragic or catastrophic "
                  "outcomes unless the user topic explicitly asks for darkness.\n",
        "neutral": "\nTONE: balanced — neither forced-happy nor grim; natural ups "
                   "and downs.\n",
        "dark": "\nTONE: tension, bittersweetness or unease are welcome.\n",
    }.get(t, "")


def _divergence_line(divergence: float, locale: str = "en") -> str:
    """飛躍度 — how far the premise may leap from the base image / topic.

    Threaded into the candidates stage (the only stage that invents premises);
    expansion is faithful by design and does not re-read it.
    """
    d = max(0.0, min(1.0, float(divergence or 0.0)))
    if d < 0.25:
        band = ("low", "\nLEAP: stay close to home — the obvious, grounded "
                       "reading of the base image and topic. The turn is a small "
                       "everyday shift, not a surprise.\n",
                "\n飛躍度: 低。元絵とお題の素直な読みに留める。転は日常の小さな変化に。\n")
    elif d < 0.55:
        band = ("medium", "\nLEAP: one unexpected but plausible development — a "
                          "reading a thoughtful reader would not guess first, yet "
                          "accepts immediately.\n",
                "\n飛躍度: 中。予想外だが腑に落ちる展開を一つ入れる。\n")
    elif d < 0.8:
        band = ("high", "\nLEAP: be bold — an unobvious premise and a real "
                        "reversal. Recontextualise the base image rather than "
                        "illustrate it. Stay in the same real-world register.\n",
                "\n飛躍度: 高。意外な前提と本物の反転を。元絵を説明せず捉え直す。"
                "現実の枠は保つ。\n")
    else:
        band = ("max", "\nLEAP: maximum — the premise should surprise even the "
                       "person who chose the image, and each act should land "
                       "somewhere the previous act did not imply. Still no magic "
                       "or genre shift unless the worldview asks.\n",
                "\n飛躍度: 最大。画像を選んだ本人が驚く前提に。各幕は前の幕から"
                "予測できない場所へ。魔法・ジャンル転換は不可。\n")
    return band[2] if locale == "ja" else band[1]


# Tolerant marker matching: [PAST], **[PAST]**, PAST:, **PAST:**, ## PAST: ...
# Bare bracketed markers match anywhere; colon forms must start a line so that
# prose words like "past" are never mistaken for markers.


def _loads_lenient(raw: str):
    """Parse JSON, tolerating prose around a single {...} object. → obj or None."""
    text = raw.strip()
    # Reasoning models may leave <think>...</think> around / before the JSON.
    if "<think>" in text.lower():
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


# ── Stage 2a: three story candidates ─────────────────────────────────────────

# Persona flavours distilled from the Invoke spirits (faithful/rebel/stranger),
# adapted from image-prompt generation to story ideation. Reused rather than
# reinvented so the three candidates diverge along proven axes.
_CANDIDATE_SPIRITS = (
    ("A", "faithful", (
        "Stay faithful to the image's LOOK. Read the visual details honestly, but "
        "if a USER TOPIC is given the story SUBJECT must still be that topic — "
        "grounded in the image's appearance, never abandoning the topic for a plain "
        "re-description of the picture. No twist for its own sake — aim for clarity, "
        "intimacy and emotional truth."
    )),
    ("B", "rebel", (
        "Find the shadow of the obvious reading. Invert ONE core assumption — the "
        "mood, the relationship, who holds power, or what is really happening — so "
        "the story becomes a natural counterpart to the first impression: "
        "surprising yet coherent, never random. If a USER TOPIC is given, invert "
        "AROUND the topic — never invert or abandon the topic itself."
    )),
    ("C", "stranger", (
        "Recontextualize the scene with ONE unexpected but grounded element — a "
        "hidden motive, a person just outside the frame, an ironic coincidence, a "
        "surprising backstory or profession, a secret the character carries, a "
        "reversal of who they really are. Keep the same real-world register as the "
        "image; the surprise is human and situational, NEVER a genre shift to "
        "space, magic, or the supernatural. If a USER TOPIC is given, the surprise "
        "MUST remain compatible with it — never invert the topic itself (do not "
        "turn 'ending is X' into 'never reaches X', do not turn 'mid-X' into "
        "'X already finished')."
    )),
)


# ── Dramatic modes (story-shape dimension, orthogonal to the spirits) ─────────
#
# Mirrors _EMOTION_REGISTER, but where emotion colours the MOOD a dramatic mode
# drives the PLOT — what shape the arc makes and what kind of turn it takes.
# One mode is assigned per candidate (distinct across A/B/C, see
# assign_dramatic_modes) so the three pitches differ in shape, not just content,
# which is what keeps the whole feature from producing the same slice-of-life
# arc every time. Every mode stays inside the real-world register (no genre
# shift); the surprise is always human and situational. Each mode is a
# forward-leaning shape whose future act LEANS INTO the turn rather than tidying
# it away — this is what makes the cliffhanger ending policy land.
_DRAMATIC_MODES: dict[str, tuple[str, str]] = {
    "escalation": (
        "ESCALATION — each act raises the stakes above the last; a small pressure "
        "in one act is urgent by the next. Never let it settle; the future act is "
        "the pressure at its highest, not its release.",
        "エスカレーション — 各幕で緊張が段階的に高まる。ある幕の小さな圧力が次の幕では切迫する。"
        "決して落ち着かせない。未来幕は圧力が最高潮に達した瞬間で、解放された後ではない。",
    ),
    "reversal": (
        "REVERSAL — what seemed true is flipped on its head; a belief, a "
        "relationship, or who holds power inverts across the acts. The future act "
        "is the moment the ground gives way, not the new equilibrium after it.",
        "反転 — 真実と思えたものが覆る。信念・関係・力関係のいずれかが幕をまたいで逆転する。"
        "未来幕は足元が崩れる瞬間で、逆転後に落ち着いた均衡ではない。",
    ),
    "revelation": (
        "REVELATION — a hidden fact surfaces piece by piece; each act uncovers "
        "more of a truth that recolours everything before it. The future act "
        "exposes the truth, it does not resolve what the truth means.",
        "発覚 — 隠れた事実が少しずつ表面化し、各幕がそれ以前をすべて塗り替える。"
        "未来幕は真実を露わにする瞬間で、その意味を解決しはしない。",
    ),
    "irony": (
        "IRONY — the character strives for one thing while the acts quietly "
        "deliver its opposite; the widening gap between intent and outcome is the "
        "point. The future act is that gap at its sharpest.",
        "皮肉 — 望んだものとは逆の結果が静かに訪れる。意図と結末のズレそのものが主題で、"
        "未来幕はそのズレが最も鋭くなった瞬間。",
    ),
    "approaching_threat": (
        "APPROACHING THREAT — something is closing in across the acts (a deadline, "
        "a pursuer, a consequence). The future act is the threat nearly upon them, "
        "NOT averted or survived.",
        "迫る脅威 — 幕を追うごとに何か（期限・追手・報い）が迫る。"
        "未来幕は脅威が目前に迫った瞬間で、回避・生還した後ではない。",
    ),
    "pursuit": (
        "PURSUIT / QUEST — the character is chasing or searching across the acts, "
        "each act closer but not arrived. End mid-chase on the verge, not at the "
        "prize.",
        "追跡／探求 — 各幕で何かを追い、探し続ける。近づくが未到達。"
        "獲得の後ではなく、あと一歩の追跡の途中で終える。",
    ),
    "parting": (
        "PARTING — a bond is being pulled apart across the acts (a leaving, a "
        "drift, a last time). The future act is the edge of separation, not the "
        "life after it.",
        "別れ — 幕をまたいで絆が引き裂かれていく（去る・すれ違う・最後の一度）。"
        "未来幕は別離の瀬戸際で、その後の日々ではない。",
    ),
    "temptation": (
        "TEMPTATION — a pull toward something risky grows across the acts. The "
        "future act is the character on the verge of giving in, not the aftermath "
        "of the choice.",
        "誘惑 — 危ういものへの引力が幕ごとに強まる。"
        "未来幕は誘惑に屈する寸前で、選んだ後の顛末ではない。",
    ),
    "secret_surfacing": (
        "SECRET SURFACING — the character carries something they hide; across the "
        "acts it presses closer to exposure. End at the brink of it coming out.",
        "秘密の露見 — 隠し事を抱えており、それが幕ごとに露見へ近づく。"
        "明るみに出る寸前で終える。",
    ),
    "role_reversal": (
        "ROLE REVERSAL — who leads and who follows (or protector and protected) "
        "trade places across the acts. The future act is mid-swap, the balance not "
        "yet settled.",
        "役割逆転 — 導く者と従う者（守る者と守られる者）が幕をまたいで入れ替わる。"
        "未来幕は入れ替わりの最中で、力関係が確定する前。",
    ),
    # ── bright / forward-looking shapes ──────────────────────────────────────
    "discovery": (
        "DISCOVERY — she stumbles onto something wonderful and follows it; each "
        "act uncovers more delight or possibility. The future act is the thrilling "
        "brink of a marvellous find, not its loss.",
        "発見 — 素晴らしい何かに出会い、追いかけていく。各幕でその歓びや可能性が広がる。"
        "未来幕は驚きの発見を目前にした高揚の瞬間で、喪失ではない。",
    ),
    "reunion": (
        "REUNION — a bond reforms or deepens across the acts; distance closes, a "
        "connection warms. The future act is the joyful meeting about to happen.",
        "再会 — 幕をまたいで絆が結び直され、深まる。距離が縮まり、つながりが温まる。"
        "未来幕は喜ばしい再会が今まさに訪れる瞬間。",
    ),
    "breakthrough": (
        "BREAKTHROUGH — patient effort starts to pay off; a skill, goal or dream "
        "moves within reach. The future act is triumph on the verge, not defeat.",
        "飛躍 — 積み重ねた努力が実を結び始める。技・目標・夢が手の届く所へ来る。"
        "未来幕は勝利の一歩手前で、敗北ではない。",
    ),
    "adventure": (
        "ADVENTURE — an exciting new undertaking begins and grows; curiosity pulls "
        "her outward. The future act is her leaping into it, eyes alight.",
        "冒険 — 心躍る新しい試みが始まり、広がっていく。好奇心が外へと駆り立てる。"
        "未来幕は目を輝かせてそこへ飛び込む瞬間。",
    ),
    "kindness": (
        "KINDNESS — a small act of warmth or connection ripples outward and "
        "changes things for the better. The future act is the moment it lands.",
        "やさしさ — 小さな温かい行い・つながりが波紋のように広がり、状況を良い方へ変える。"
        "未来幕はその想いが届く瞬間。",
    ),
    "mischief": (
        "MISCHIEF — playful, good-natured fun escalates gleefully across the acts. "
        "The future act is the delighted peak of the prank or game.",
        "いたずら — 悪意のない遊び心が幕ごとに楽しく高まる。"
        "未来幕はいたずらや遊びが最高に盛り上がった瞬間。",
    ),
    "bloom": (
        "BLOOM — she grows into her own; shyness turns to confidence, a talent "
        "flowers. The future act is her coming into full bloom, not wilting.",
        "開花 — 彼女が自分らしさへと成長する。臆病が自信へ、才能が花開く。"
        "未来幕は満開へと咲きゆく瞬間で、萎れる姿ではない。",
    ),
}
_DRAMATIC_MODE_KEYS: tuple[str, ...] = tuple(_DRAMATIC_MODES)
# Split for tone-aware assignment: bright shapes lean hopeful/forward, dark ones
# lean tense/ominous. "auto" (bright tone) prefers bright; dark tone includes dark.
_BRIGHT_MODE_KEYS: tuple[str, ...] = (
    "discovery", "reunion", "breakthrough", "adventure", "kindness", "mischief",
    "bloom", "pursuit",
)
_DARK_MODE_KEYS: tuple[str, ...] = tuple(
    k for k in _DRAMATIC_MODE_KEYS if k not in _BRIGHT_MODE_KEYS
)


def assign_dramatic_modes(
    ids: tuple[str, ...] = ("A", "B", "C"),
    *,
    preferred: str = "",
    tone: str = "bright",
    rng=None,
) -> dict[str, str]:
    """Pick a DISTINCT dramatic mode for each candidate id, tone-aware.

    preferred (a user-chosen mode, '' = auto) is pinned onto the first id so a
    user selection is guaranteed present. tone shapes the auto pool:
    'bright' → bright shapes first (dark only if more ids than bright modes),
    'dark' → dark shapes first, 'neutral' → the full pool shuffled. rng is
    injectable for deterministic tests.
    """
    import random as _random
    rng = rng or _random
    tone = (tone or "bright").strip().lower()
    if tone == "dark":
        primary, secondary = list(_DARK_MODE_KEYS), list(_BRIGHT_MODE_KEYS)
    elif tone == "neutral":
        primary, secondary = list(_DRAMATIC_MODE_KEYS), []
    else:  # bright (default)
        primary, secondary = list(_BRIGHT_MODE_KEYS), list(_DARK_MODE_KEYS)
    rng.shuffle(primary)
    rng.shuffle(secondary)
    ordered = primary + secondary

    chosen: list[str] = []
    pref = (preferred or "").strip().lower()
    if pref in _DRAMATIC_MODES:
        chosen.append(pref)
        ordered = [k for k in ordered if k != pref]
    chosen.extend(ordered)
    return {cid: chosen[i % len(chosen)] for i, cid in enumerate(ids)}


def parse_candidates_json(raw: str) -> list[dict]:
    """Parse the candidates output into a list of dicts. Missing/broken → []."""
    data = _loads_lenient(raw)
    items = data.get("candidates") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip() or chr(ord("A") + len(result))
        past = str(item.get("past") or "").strip()
        present = str(item.get("present") or "").strip()
        future = str(item.get("future") or "").strip()
        # Backward compat: older records carried a single `summary` instead of
        # per-axis beats. Keep a derived summary so downstream (Storybook,
        # expand seed) never sees an empty field.
        summary = str(item.get("summary") or "").strip()
        if not summary:
            summary = present or " / ".join(b for b in (past, future) if b)
        # `motif` is the canonical name; older records used `key_motif`.
        motif = str(item.get("motif") or item.get("key_motif") or "").strip()
        # Dramatic-mode dimension (may be absent on legacy records → "").
        dramatic_mode = str(item.get("dramatic_mode") or "").strip().lower()
        turn = str(item.get("turn") or "").strip()
        grounded_raw = item.get("grounded_tags") or item.get("used_tags") or []
        grounded_tags: list[str] = []
        if isinstance(grounded_raw, list):
            for t in grounded_raw:
                name = str(t or "").strip().replace(" ", "_")
                if name:
                    grounded_tags.append(name)
        result.append({
            "id": cid,
            "title": str(item.get("title") or "").strip(),
            "past": past,
            "present": present,
            "future": future,
            "summary": summary,
            "motif": motif,
            "dramatic_mode": dramatic_mode,
            "turn": turn,
            "grounded_tags": grounded_tags,
        })
    return result

# ── Biography / Timetable / Concrete activities ───────────────────────────────
#
# Persistent character grounding to defeat the "stiff/idle pose" problem: a
# BIOGRAPHY (hobbies, favourite items, personality — never appearance, which
# WD14 owns) gives the character things she physically DOES; a TIMETABLE maps
# that onto concrete moments; and a re-examination step pins each act to ONE
# drawable action using her hobbies/items. All English (grounds prompts + WD14
# search); a display translation is produced separately for the ja UI.

_BIO_STR_KEYS = ("personality", "occupation", "backstory")
_BIO_LIST_KEYS = ("hobbies", "favourite_items", "likes", "dislikes", "quirks")

# A wide pool of interest areas, sampled per generation and offered to the model
# as (non-mandatory) inspiration so biographies stop collapsing onto the same
# few defaults (baking / violin / flowers). Deliberately mixes athletic,
# creative, intellectual, outdoorsy and quirky domains.


def build_topic_only_grounding_prompt(
    *,
    user_topic: str,
    worldview: str = "",
    locale: str = "en",
) -> str:
    """Invent character + scene + Danbooru tags when there is no source image.

    Output is English JSON so biography / expand / WD14 search can reuse it
    the same way as a VLM vision pass over a base image.
    """
    world = f'Worldview / setting: "{worldview.strip()}"\n' if worldview.strip() else ""
    return (
        "There is NO reference image. Invent ONE anime-style character and a "
        "concrete starting SCENE that fits the topic below. Appearance must be "
        "specific enough to lock identity across past/present/future (hair "
        "colour, eye colour, signature accessory).\n\n"
        f'TOPIC (お題): "{user_topic.strip()}"\n'
        f"{world}\n"
        "Rules:\n"
        "- Solo character unless the topic clearly requires multiple people.\n"
        "- Scene is a real place with props (not abstract void).\n"
        "- wd14_tags: 12–24 common Danbooru tags (underscore form), including "
        "1girl/1boy or 2girls/3girls as appropriate, hair, eyes, outfit, place.\n"
        "- Do NOT invent famous copyrighted characters.\n\n"
        "Output English JSON only, no markdown fences:\n"
        '{"character_desc": "<1-2 sentences of appearance>", '
        '"scene_desc": "<1-2 sentences of place + action right now>", '
        '"wd14_tags": ["1girl", "solo", "blonde_hair", "blue_eyes", "..."]}'
    )


def parse_topic_only_grounding_json(raw: str) -> dict:
    """Parse topic-only grounding. Missing/broken → {}."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {}
    tags_raw = data.get("wd14_tags")
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            s = str(t or "").strip().replace(" ", "_")
            if s:
                tags.append(s)
    out = {
        "character_desc": str(data.get("character_desc") or "").strip(),
        "scene_desc": str(data.get("scene_desc") or "").strip(),
        "wd14_tags": tags[:40],
    }
    return out if out["character_desc"] or out["scene_desc"] or out["wd14_tags"] else {}


def parse_biography_json(raw: str) -> dict:
    """Parse a biography payload. Missing/broken → {} (feature degrades off).

    Truncated JA translations sometimes collapse list fields into a bare string;
    coerce those to a one-element list so callers (and the Vue panel) never see
    a non-list hobbies/items value.
    """
    data = _loads_lenient(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else None)
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for k in _BIO_STR_KEYS:
        out[k] = str(data.get(k) or "").strip()
    for k in _BIO_LIST_KEYS:
        v = data.get(k)
        if isinstance(v, list):
            out[k] = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str) and v.strip():
            out[k] = [v.strip()]
        else:
            out[k] = []
    return out if any(out.values()) else {}


def _biography_brief(bio: dict | None) -> str:
    """One-line biography summary for embedding into other prompts."""
    if not bio:
        return "(no biography)"
    parts: list[str] = []
    if bio.get("occupation"):
        parts.append(str(bio["occupation"]))
    if bio.get("personality"):
        parts.append(str(bio["personality"]))
    if bio.get("hobbies"):
        parts.append("hobbies: " + ", ".join(bio["hobbies"]))
    if bio.get("favourite_items"):
        parts.append("favourite items: " + ", ".join(bio["favourite_items"]))
    if bio.get("quirks"):
        parts.append("quirks: " + ", ".join(bio["quirks"]))
    return " | ".join(parts) or "(no biography)"


def build_json_translation_prompt(obj, *, target: str = "Japanese") -> str:
    """Translate every VALUE of a JSON object; keys/structure preserved."""
    return (
        f"Translate every VALUE in this JSON into natural {target}. Keep the KEYS "
        "and the JSON structure identical. Answer with JSON only, no fences.\n\n"
        f"{json.dumps(obj, ensure_ascii=False)}"
    )


# Per-scale timetable WINDOW: a span centred on the base moment ("now"), sized a
# bit wider than the axis delta and sliced finely, so the acts just before/after
# "now" are explicitly grounded. Keys: window (what the table spans), slots (how
# finely to cut it, with example relative labels centred on "now").
_TIMETABLE_WINDOW: dict[str, tuple[str, str]] = {
    "minutes": (
        "the ~30 minutes AROUND this moment",
        "7 slots about 5 minutes apart, labelled relative to now "
        "(-15min, -10min, -5min, now, +5min, +10min, +15min)",
    ),
    "tens_of_minutes": (
        "the ~2 hours AROUND this moment (so the tens-of-minutes before and after "
        "are fully covered)",
        "7 slots about 20 minutes apart, labelled relative to now "
        "(-1h, -40min, -20min, now, +20min, +40min, +1h)",
    ),
    "hours": (
        "this whole DAY around the moment",
        "about 7 slots a couple of hours apart, labelled by clock time "
        "(early morning, mid-morning, noon, early afternoon, late afternoon, "
        "evening, night)",
    ),
    "days": (
        "about a WEEK around this day",
        "7 slots about a day apart, labelled relative to today "
        "(-3d, -2d, -1d, today, +1d, +2d, +3d)",
    ),
    "months": (
        "about a YEAR around now",
        "about 6-7 slots ~2 months / by season, labelled by month or season",
    ),
    "years": (
        "several YEARS of her life around now",
        "about 6-7 slots ~1-2 years apart, labelled by age or life stage",
    ),
    "decades": (
        "her whole LIFE across eras",
        "about 6-7 slots by decade / life stage (childhood … later years)",
    ),
}


# ── Timeline distinctness (code-side enforcement, not prose pleading) ─────────
#
# The pipeline layered a lot of prose telling the model NOT to re-shoot the same
# moment three times, but nothing ever CHECKED. These helpers give the timeline
# programmatic teeth, mirroring _chronicle_tags_degenerate: if the acts collapse
# into one moment, the expand runner fires one targeted differentiate rewrite.
# Similarity is measured with language-agnostic character bigrams so it works
# for both the English and Japanese stories the pipeline produces.
#
# Short scales (minutes / tens_of_minutes) intentionally keep near-duplicate
# beats — those acts are micro-shifts of one scene — so differentiate is skipped.

def _char_bigrams(s: str) -> set[str]:
    t = re.sub(r"\s+", "", s.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _text_similarity(a: str, b: str) -> float:
    """Character-bigram Jaccard similarity of two short texts (0..1)."""
    ba, bb = _char_bigrams(a), _char_bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


_BEAT_SIMILAR_THRESHOLD = 0.6


def _mean_pairwise_similarity(beats: list[str]) -> float:
    """Mean similarity over every beat pair. Any length — quality.py scores
    only the generated axes, which is 2 whenever a base image supplies one.
    """
    sims = [
        _text_similarity(a, b) for a, b in itertools.combinations(beats, 2)
    ]
    if not sims:
        return 0.0
    return sum(sims) / len(sims)


def acts_temporally_distinct(
    stories: dict[str, str], *, threshold: float = _BEAT_SIMILAR_THRESHOLD
) -> bool:
    """True if the three expanded acts are meaningfully different moments.

    False → they collapsed into near-duplicates; the runner fires one targeted
    'differentiate the acts' rewrite. Incomplete input returns True so the
    missing-act error path (not this one) handles it.
    """
    beats = [str(stories.get(a) or "").strip() for a in AXES]
    if not all(beats):
        return True
    return _mean_pairwise_similarity(beats) < threshold


# Scales where near-duplicate acts are expected (same scene, micro-shift).
_SKIP_DIFFERENTIATE_SCALES = frozenset({"minutes", "tens_of_minutes"})


def should_differentiate_acts(time_scale: str) -> bool:
    """False for micro time scales where three near-identical beats are correct."""
    return (time_scale or "").strip().lower() not in _SKIP_DIFFERENTIATE_SCALES


def activities_temporally_distinct(
    activities: dict[str, str], *, threshold: float = _BEAT_SIMILAR_THRESHOLD
) -> bool:
    """True if the three concrete actions are meaningfully different moments.

    Same bigram measure as ``acts_temporally_distinct``. Incomplete input
    returns True so the missing-act path (not this one) handles it.
    """
    beats = [str(activities.get(a) or "").strip() for a in AXES]
    if not all(beats):
        return True
    return _mean_pairwise_similarity(beats) < threshold


# ── Stage 3: per-axis Visual Script prompt ────────────────────────────────────


# Default guide kept for imports / tests that expect a module-level constant.


# Refine-parity category buckets (UI + structured view for image models).
# CHRONICLE_CAT_FIELDS / parse / merge / footer: see prompt.visual_spec
# Primary classification: tags.catalog.get_tag_axis (+ ACCESSORIES / lighting sets).

_EXPR_EYE_PREFIXES = ("teary", "closed", "empty", "half", "watery", "tired")
_CLOTHING_HINTS = (
    "dress", "shirt", "skirt", "uniform", "kimono", "yukata",
    "jacket", "coat", "pants", "socks", "shoes", "boots",
)
_ACCESSORY_HINTS = (
    "hat", "cap", "glasses", "earring", "necklace", "choker", "bag",
    "hair_ornament", "hair_ribbon", "ribbon", "gloves", "scarf",
)
_BODY_PART_TOKENS = frozenset({
    "arm", "arms", "hand", "hands", "leg", "legs", "finger", "fingers",
    "foot", "feet", "knee", "knees", "shoulder", "shoulders", "elbow",
    "elbows", "palm", "fist", "wrist", "ankle", "thigh", "hip", "hips",
    "neck", "toe", "toes",
})


def bucket_danbooru_tags(tag_line: str) -> dict[str, list[str]]:
    """Category buckets from a flat danbooru tag line via tags.catalog axes."""
    parts = [
        t.strip().replace(" ", "_")
        for t in (tag_line or "").split(",")
        if t.strip()
    ]
    cats: dict[str, list[str]] = {k: [] for k in CHRONICLE_CAT_FIELDS}
    for tag in parts:
        low = tag.lower()
        toks = set(low.split("_"))

        # ACCESSORIES win even though TAG_TO_AXIS maps them to clothing.
        if low in _ACCESSORIES:
            cats["accessory_tags"].append(tag)
            continue

        # Expressive *_eyes before always_fixed / emotion axis defaults.
        if low.endswith("_eyes"):
            if any(low.startswith(p) or p in toks for p in _EXPR_EYE_PREFIXES):
                cats["expression_tags"].append(tag)
            else:
                cats["subject_tags"].append(tag)
            continue

        # Body parts before action, so outstretched_arm etc. land here.
        if low == "bare_shoulders" or (toks & _BODY_PART_TOKENS):
            cats["body_parts_tags"].append(tag)
            continue

        axis = get_tag_axis(low)
        if axis == "always_fixed" or low in _COUNT:
            if low in _PROPS:
                cats["object_tags"].append(tag)
            else:
                cats["subject_tags"].append(tag)
            continue

        if axis == "hair":
            cats["hair_tags"].append(tag)
        elif axis == "emotion":
            cats["expression_tags"].append(tag)
        elif axis == "action":
            cats["pose_tags"].append(tag)
        elif axis == "clothing":
            if any(s in low for s in _ACCESSORY_HINTS):
                cats["accessory_tags"].append(tag)
            else:
                cats["clothing_tags"].append(tag)
        elif (
            axis == "time_weather"
            or low in _VISUAL_LIGHTING
            or (axis == "visual" and low in _VISUAL_LIGHTING)
        ):
            cats["lighting_tags"].append(tag)
        elif (
            axis == "location"
            or low in _ABSTRACT_BG
            or (axis == "visual" and low in _ABSTRACT_BG)
        ):
            cats["background_tags"].append(tag)
        elif axis == "visual":
            cats["lighting_tags"].append(tag)
        elif any(s in low for s in _CLOTHING_HINTS):
            cats["clothing_tags"].append(tag)
        elif any(s in low for s in _ACCESSORY_HINTS):
            cats["accessory_tags"].append(tag)
        else:
            cats["object_tags"].append(tag)
    return {k: v for k, v in cats.items() if v}


_ACTIVITY_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "at", "for", "with",
    "her", "his", "she", "he", "they", "their", "from", "into", "over", "under",
    "as", "by", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "while", "then", "than", "very", "just", "only", "into", "onto", "across",
})


def _activity_keyword_tokens(text: str, *, limit: int = 6) -> list[str]:
    """Danbooru-ish keyword tokens from an activity sentence (unique, ordered)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9']+", text or ""):
        tok = raw.lower().replace("'", "")
        if len(tok) < 3 or tok in _ACTIVITY_STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _inject_tags_into_positive(positive: str, new_tags: list[str]) -> str:
    """Insert tags after subject anchors when possible; else after first tag."""
    cleaned = [t.strip().replace(" ", "_") for t in new_tags if t and str(t).strip()]
    if not cleaned:
        return positive
    try:
        return insert_after_anchors(positive, cleaned)
    except Exception:
        pass
    parts = [t.strip() for t in (positive or "").split(",") if t.strip()]
    existing = {p.lower() for p in parts}
    add = [t for t in cleaned if t.lower() not in existing]
    if not add:
        return positive
    if not parts:
        return ", ".join(add)
    # Prepend after the first tag (keep leading subject/count if present).
    return ", ".join(parts[:1] + add + parts[1:])


def repair_collapsed_axis_tags(
    prompts: dict,
    *,
    visual_plans: dict,
    activities: dict,
    gen_axes: list[str],
) -> dict:
    """Inject per-axis unique action/expression/activity tokens into collapsed prompts.

    Idempotent-ish: skips tags already present in that axis positive. Mutates a
    shallow copy of ``prompts`` and returns it.
    """
    axes = [a for a in gen_axes if a in AXES] or list(AXES)
    # Collect per-axis candidate tags from visual plans + activities.
    per_axis: dict[str, list[str]] = {}
    for a in axes:
        plan = visual_plans.get(a) or {}
        tags: list[str] = []
        for t in plan.get("focal_action_tags") or []:
            s = str(t).strip().replace(" ", "_")
            if s:
                tags.append(s)
        expr = str(plan.get("expression_tag") or "").strip().replace(" ", "_")
        if expr:
            tags.append(expr)
        tags.extend(_activity_keyword_tokens(str(activities.get(a) or "")))
        # Dedupe within axis (preserve order).
        seen: set[str] = set()
        uniq: list[str] = []
        for t in tags:
            k = t.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(t)
        per_axis[a] = uniq

    # Prefer tags unique to this axis among gen_axes (shared tags are weaker).
    shared: set[str] = set()
    if len(axes) >= 2:
        counts: dict[str, int] = {}
        for a in axes:
            for t in {x.lower() for x in per_axis.get(a, [])}:
                counts[t] = counts.get(t, 0) + 1
        shared = {t for t, n in counts.items() if n >= 2}

    out = dict(prompts)
    for a in axes:
        entry = out.get(a)
        if not isinstance(entry, dict):
            continue
        positive = str(entry.get("positive") or "")
        if not positive.strip():
            continue
        candidates = [
            t for t in per_axis.get(a, [])
            if t.lower() not in shared or len(per_axis.get(a, [])) <= 2
        ]
        # Fall back to all candidates if uniqueness filtered everything.
        if not candidates:
            candidates = list(per_axis.get(a, []))
        if not candidates:
            continue
        new_pos = _inject_tags_into_positive(positive, candidates)
        head, _, tail = new_pos.partition("\n\n")
        if "," in head and "\n" not in head and "." not in head:
            head = cap_danbooru_tag_line(head, priority_tags=candidates)
            new_pos = f"{head}\n\n{tail}".strip() if tail.strip() else head
        if new_pos != positive:
            out[a] = {**entry, "positive": new_pos}
    return out


# Per-scale visual invariants used in both story and image-prompt generation.
# Keys: must_keep (IDENTICAL to base), may_differ (allowed changes), forbidden.
_SCALE_VISUAL_RULES: dict[str, dict[str, str]] = {
    "minutes": {
        "must_keep": (
            "hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), exact location (SAME room/spot), "
            "season, time of day"
        ),
        "may_differ": "outfit (keep it identical UNLESS the story explicitly justifies a change — changing clothes, removing a jacket, a costume switch), micro-pose, finger/hand position, expression, a gust of wind, what the character is doing with hands/body (writing, reaching, pressing, picking up, etc.)",
        "outfit_rule": (
            "IDENTICAL in every act — copy the base outfit string verbatim. Only a change the story explicitly shows (removing a jacket, untying an apron) may alter it."
        ),
        "forbidden": "any location change, any passage of seasons, aging",
    },
    "tens_of_minutes": {
        "must_keep": (
            "hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), same room or immediate outdoor spot, "
            "season, time of day"
        ),
        "may_differ": "outfit (keep it identical UNLESS the story explicitly justifies a change — changing clothes, removing a jacket, a costume switch), pose, expression, minor object placement, slight lighting shift, character's activity and what they are doing, object being interacted with",
        "outfit_rule": (
            "IDENTICAL in every act — copy the base outfit string verbatim. Only a change the story explicitly shows (removing a jacket, untying an apron) may alter it."
        ),
        "forbidden": "any location change, any passage of seasons, aging",
    },
    "hours": {
        "must_keep": (
            "hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), same building or outdoor location, season"
        ),
        "may_differ": "outfit (keep it identical UNLESS the story explicitly justifies a change — changing clothes, a costume switch), light angle and shadow direction, expression, full pose and activity, props in hand, position within the location, slight fatigue",
        "outfit_rule": (
            "IDENTICAL in every act — copy the base outfit string verbatim unless the story explicitly shows a change (a costume switch, shedding a coat indoors)."
        ),
        "forbidden": "location change, season change, aging",
    },
    "days": {
        "must_keep": "hair color and style, core facial features, same general area",
        "may_differ": "outfit (may have changed), time of day, emotional state, minor details",
        "outfit_rule": (
            "may be a different everyday set from the same wardrobe and season; never a different style of person."
        ),
        "forbidden": "season change, significant aging, major location change",
    },
    "months": {
        "must_keep": "hair color, core facial features, recognizable character identity",
        "may_differ": "seasonal outfit, season, slight physical wear, environment",
        "outfit_rule": (
            "a SEASONAL variant — the same person's wardrobe shifted for the new season (coat vs shirt); same taste."
        ),
        "forbidden": "significant aging, era-level fashion shift",
    },
    "years": {
        "must_keep": "recognizable as the same person",
        "may_differ": "outfit style, slight aging, hair style, environment, life stage",
        "outfit_rule": (
            "may change with her life stage (student uniform to work clothes); still recognisably her taste."
        ),
        "forbidden": "complete transformation that makes the person unrecognizable",
    },
    "decades": {
        "must_keep": "any recognizable trait if plausible",
        "may_differ": "everything — age, fashion era, environment, world",
        "outfit_rule": (
            "may belong to a different fashion era entirely."
        ),
        "forbidden": "nothing is forbidden — show dramatic transformation",
    },
}


# How MUCH the scene changes between acts, per scale. Short scales must stay an
# extension of the base image (small delta), not a scene/time jump; long scales
# open up. Keeps the three acts from lurching into unrelated scenes at "minutes".
_SCALE_DELTA: dict[str, str] = {
    "minutes": (
        "almost the same instant — the other acts are the SAME scene a few beats "
        "earlier / later (a micro-shift of pose, gaze, hand action or expression). "
        "This is the base image EXTENDED slightly before/after (an image+alpha "
        "continuation), NOT a new scene, NOT a distant memory or a far future. "
        "If an activity is underway it is STILL underway in every act — a few beats "
        "does not finish it; do not resolve, complete or exit the action."
    ),
    "tens_of_minutes": (
        "the same spot a short while apart — a small progression of the SAME "
        "activity (an object picked up or set down, a step taken, the mood easing). "
        "Same scene, small delta — no location or time-of-day jump. An activity "
        "underway stays underway; do not wrap it up or end the scene."
    ),
    "hours": (
        "the same place/building on the SAME day — a different beat of the same "
        "visit (arriving, mid-way, about to leave). The location does not change "
        "and it is not an origin story or a far-off ending."
    ),
    "days": (
        "the same person and area a few days apart — outfit or time of day may "
        "differ, but it is clearly the same ongoing life, not a dramatic origin "
        "or finale."
    ),
    "months": (
        "a seasonal shift in the same locale — wardrobe and season change while "
        "identity and place stay recognizable."
    ),
    "years": (
        "a life-stage change — settings and scenes may genuinely differ while the "
        "person stays recognizable."
    ),
    "decades": (
        "eras and transformation — scenes, fashion and world may change completely."
    ),
}


def _scale_delta_line(time_scale: str) -> str:
    return _SCALE_DELTA.get(time_scale, _SCALE_DELTA["years"])


def scale_outfit_rule(time_scale: str) -> str:
    """How much the outfit may change across acts at this scale (public
    accessor — fast mode needs it without importing the private table)."""
    rules = _SCALE_VISUAL_RULES.get(time_scale) or _SCALE_VISUAL_RULES["years"]
    return rules["outfit_rule"]


# ── Identity tag scoping (chronicle-specific WD14 handling) ───────────────────
#
# Chronicle depicts a DIFFERENT moment in time, so unlike Refine we must never
# force the base image's scene/pose/background/time-of-day tags into an axis
# prompt. Only identity traits are anchored, and which categories count as
# "identity" shrinks with the time scale (outfit is identity at minutes, not at
# years). Whitelist polarity: an unrecognized tag is never injected.

_COLOR_WORDS = (
    "black|white|blonde?|brown|red|pink|purple|violet|blue|green|grey|gray|"
    "silver|orange|aqua|amber|yellow|golden?|platinum|crimson|multicolored|"
    "gradient|two-tone|streaked|dark|light"
)
_HAIR_COLOR_RE = re.compile(rf"^(?:\w+_)?(?:{_COLOR_WORDS})_hair$")
_EYE_COLOR_RE = re.compile(rf"^(?:\w+_)?(?:{_COLOR_WORDS})_eyes$")

_HAIR_STYLE_TAGS = frozenset({
    "long_hair", "short_hair", "medium_hair", "very_long_hair",
    "absurdly_long_hair", "wavy_hair", "curly_hair", "straight_hair",
    "messy_hair", "spiked_hair", "hair_bun", "double_bun", "hair_intakes",
    "hair_over_one_eye", "hair_between_eyes", "bob_cut", "hime_cut",
    "pixie_cut", "drill_hair", "blunt_bangs", "swept_bangs",
})
_HAIR_STYLE_TOKENS = frozenset({
    "braid", "braids", "twintails", "twintail", "ponytail", "bangs", "ahoge",
    "sidelocks",
})
_FACE_BODY_TOKENS = frozenset({
    "mole", "freckles", "fang", "fangs", "horn", "horns", "wing", "wings",
    "tail", "ears", "skin", "scar", "tattoo", "halo", "heterochromia",
    "elf", "tanned", "tanlines",
})
# Clothing/accessory half of vocab_bank._CHARACTER_KEYWORDS, token form.
# Split into ACCESSORIES (jewelry / eyewear / headwear / neckwear / hosiery —
# small identity-defining adornments) and GARMENTS (core clothing). The new
# WD14+Refine flow treats accessories as ALWAYS-KEEP identity (with hair colour
# and eye colour), while garments are free to change across the timeline.
_ACCESSORY_TOKENS = frozenset({
    "hat", "ribbon", "bow", "bowtie", "necktie", "scarf", "glove", "gloves",
    "necklace", "earring", "earrings", "glasses", "choker", "belt",
    "thighhighs", "pantyhose", "boots", "shoes", "socks",
})
_GARMENT_TOKENS = frozenset({
    "dress", "uniform", "outfit", "shirt", "skirt", "jacket", "coat",
    "blouse", "sweater", "hoodie", "kimono", "yukata", "leotard", "bikini",
    "swimsuit", "cape", "cloak", "armor", "apron", "vest", "pants",
    "shorts", "corset",
    # Single-word garments with no compound structure to key off — without
    # these, classify_identity_tag misses them entirely and a serafuku base
    # image reports no outfit at all.
    "serafuku", "cardigan", "hakama", "haori", "cheongsam", "overalls",
    "jeans", "trousers", "tracksuit", "turtleneck", "blazer", "pullover",
})
# Backward-compatible union (any caller wanting "clothing or accessory").


def classify_identity_tag(tag: str) -> str | None:
    """'hair_color'|'hair_style'|'eyes'|'face'|'accessory'|'outfit'|None.

    None = never inject. 'accessory' (jewelry, eyewear, ribbon, hat…) is split
    out from 'outfit' (garments) so the WD14+Refine flow can lock accessories as
    identity while letting garments change across the timeline.
    """
    t = tag.strip().lower().replace(" ", "_")
    if _HAIR_COLOR_RE.match(t):
        return "hair_color"
    toks = set(t.split("_"))
    if t in _HAIR_STYLE_TAGS or toks & _HAIR_STYLE_TOKENS:
        return "hair_style"
    if _EYE_COLOR_RE.match(t) or t == "heterochromia":
        return "eyes"
    if toks & _FACE_BODY_TOKENS:
        return "face"
    if toks & _ACCESSORY_TOKENS:
        return "accessory"
    if toks & _GARMENT_TOKENS:
        return "outfit"
    return None


# Which identity categories are still anchored at each time scale
# (derived from _SCALE_VISUAL_RULES must_keep lists above).
_IDENTITY_CATEGORIES_BY_SCALE: dict[str, frozenset[str]] = {
    "minutes": frozenset({"hair_color", "hair_style", "eyes", "face", "outfit"}),
    "tens_of_minutes": frozenset({"hair_color", "hair_style", "eyes", "face", "outfit"}),
    "hours": frozenset({"hair_color", "hair_style", "eyes", "face", "outfit"}),
    "days": frozenset({"hair_color", "hair_style", "eyes", "face"}),
    "months": frozenset({"hair_color", "hair_style", "eyes", "face"}),
    "years": frozenset({"hair_color", "eyes", "face"}),
    "decades": frozenset(),
}


# Multi-subject wd14 tags — when present we cannot tell which character a
# hair_color / eyes tag belongs to, so anchoring those would smear identities.
_MULTI_SUBJECT_TAGS = frozenset({
    "2girls", "3girls", "4girls", "5girls", "6+girls", "multiple_girls",
    "2boys", "3boys", "4boys", "multiple_boys", "multiple_girls",
})


def is_multi_character(wd14_tags: list[str]) -> bool:
    """True if the base image depicts more than one subject (ambiguous identity)."""
    return any(t.strip().lower() in _MULTI_SUBJECT_TAGS for t in wd14_tags)


# Tokens that identify pose / action / framing tags among a wd14 tag list.
# Deliberately loose — matches any tag containing one of these tokens so that
# both bare ("standing") and compound ("outstretched_arm", "leaning_forward")
# variants are picked up. Framing tags (from_side / cowboy_shot) are matched
# by exact-tag membership below.


def identity_tags_for_scale(
    wd14_tags: list[str],
    time_scale: str,
    *,
    limit: int = 12,
    multi_character: bool = False,
) -> list[str]:
    """Scale-gated identity subset of the base image's wd14 tags, order kept.

    With multi_character=True the hair_color / eyes categories are dropped: with
    several subjects present those tags cannot be attributed to one character, so
    forcing them would blend identities. Face/hair_style/outfit still anchor.
    """
    allowed = _IDENTITY_CATEGORIES_BY_SCALE.get(
        time_scale, _IDENTITY_CATEGORIES_BY_SCALE["years"]
    )
    if multi_character:
        allowed = allowed - {"hair_color", "eyes"}
    if not allowed:
        return []
    result: list[str] = []
    for tag in wd14_tags:
        category = classify_identity_tag(tag)
        if category in allowed:
            result.append(tag)
            if len(result) >= limit:
                break
    return result


# The always-keep identity set for the WD14+Refine flow: hair colour, eye
# colour, and accessories — held constant across ALL time scales (unlike the
# scale-gated identity_tags_for_scale). Garments, hair style and pose are free
# to change from act to act, driven by the WD14 vector search + base-image tags.
_IDENTITY_LOCK_CATEGORIES = frozenset({"hair_color", "eyes", "accessory"})


def identity_lock_tags(
    wd14_tags: list[str],
    *,
    limit: int = 12,
    multi_character: bool = False,
) -> list[str]:
    """Base image's hair-colour + eye-colour + accessory tags — ALWAYS kept.

    Scale-independent (unlike identity_tags_for_scale): these are the traits the
    user wants preserved across every act. multi_character drops hair_color/eyes
    (ambiguous ownership with several subjects), same as identity_tags_for_scale;
    accessories still lock.
    """
    allowed = _IDENTITY_LOCK_CATEGORIES
    if multi_character:
        allowed = allowed - {"hair_color", "eyes"}
    result: list[str] = []
    for tag in wd14_tags:
        if classify_identity_tag(tag) in allowed:
            result.append(tag)
            if len(result) >= limit:
                break
    return result


def inject_identity_tags(tag_line: str, identity: list[str]) -> str:
    """Insert identity tags after the last subject-anchor tag, dedup (ci)."""
    return insert_after_anchors(tag_line, list(identity))


# Anime diffusion models lose coherence past ~20 tags. Draft and final share
# this ceiling; keep identity + theme must-tags first when truncating.
IMAGE_PROMPT_MAX_TAGS = 20
IMAGE_PROMPT_MAX_PROSE_WORDS = 60


# The 自然文 knob (3–7) → per-act prose size for build_acts_polish_prompt.
#
# A ceiling alone does NOT move the model: measured against gemma-4-12b, "at
# most 120 words" still produced ~32 words, because the polish prompt's other
# rules ("Rephrase, never invent") starve it of content — the ceiling is never
# the target. Only a floor makes the knob track (measured: 3→~38w, 5→~55w,
# 7→~85w). Callers MUST also pass `hi` to assemble_capped_positive as
# max_prose_words, or IMAGE_PROMPT_MAX_PROSE_WORDS truncates the long end back
# to 60 and the top of the range collapses.
_PROSE_BUDGETS: dict[int, tuple[int, int, str]] = {
    3: (25, 40, "1-2"),
    4: (40, 60, "2-3"),
    5: (60, 80, "3-4"),
    6: (80, 100, "4-5"),
    7: (100, 130, "5-6"),
}


def chronicle_prose_budget(paragraphs: int | None) -> tuple[int, int, str]:
    """(min_words, max_words, sentence_range) for one act. Monotonic in n."""
    return _PROSE_BUDGETS[clamp_prose_paragraphs(paragraphs)]

# Fast mode: VLM aims for ≥30 tags, then +5 mid-rank WD14 injects (cap 45).
FAST_PROMPT_MIN_TAGS = 30
FAST_PROMPT_MAX_TAGS = 45


# Deterministic costume/motif packs from お題. These MUST survive tag capping —
# the long Chronicle pipeline otherwise drops "bunny girl" as non-identity.
_THEME_MUST_RULES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (
        (
            "バニーガール", "バニー ガール", "bunny girl", "bunnygirl",
            "bunny_girl", "playboy bunny", "playboy_bunny", "bunny costume",
        ),
        (
            "bunny_girl", "rabbit_ears", "leotard", "pantyhose",
            "detached_collar", "wrist_cuffs",
        ),
    ),
    (
        ("メイド服", "メイド", "maid outfit", "maid dress", "maid"),
        ("maid", "maid_headdress", "apron"),
    ),
    (
        ("巫女", "miko", "shrine maiden", "shrine_maiden"),
        ("miko", "hakama"),
    ),
    (
        ("セーラー服", "sailor uniform", "sailor_uniform", "serafuku"),
        ("serafuku", "sailor_collar"),
    ),
    (
        ("制服", "school uniform", "school_uniform"),
        ("school_uniform",),
    ),
    (
        ("着物", "浴衣", "kimono", "yukata"),
        ("kimono",),
    ),
]
# If WD14 / character tags already carry these, promote the matching pack.
_THEME_TAG_HINTS: dict[str, tuple[str, ...]] = {
    "bunny_girl": _THEME_MUST_RULES[0][1],
    "rabbit_ears": _THEME_MUST_RULES[0][1],
    "bunny_ears": _THEME_MUST_RULES[0][1],
    "playboy_bunny": _THEME_MUST_RULES[0][1],
    "maid": _THEME_MUST_RULES[1][1],
    "maid_headdress": _THEME_MUST_RULES[1][1],
    "miko": _THEME_MUST_RULES[2][1],
    "serafuku": _THEME_MUST_RULES[3][1],
    "sailor_collar": _THEME_MUST_RULES[3][1],
}


def theme_must_tags(
    user_topic: str,
    *,
    extra_tags: list[str] | None = None,
) -> list[str]:
    """Danbooru tags that every axis prompt must keep for the given お題.

    Costume themes (bunny girl, maid, …) are not identity-locked by hair/eyes,
    so without this hard must-list they vanish across densify / draft / cap.
    """
    text = (user_topic or "").strip().lower().replace("＿", "_")
    text_compact = re.sub(r"[\s_]+", "", text)
    out: list[str] = []
    seen: set[str] = set()

    def _add(seq: tuple[str, ...] | list[str]) -> None:
        for raw in seq:
            tag = str(raw).strip().replace(" ", "_")
            key = tag.lower()
            if tag and key not in seen:
                seen.add(key)
                out.append(tag)

    for needles, tags in _THEME_MUST_RULES:
        for needle in needles:
            n = needle.lower().replace(" ", "")
            n_spaced = needle.lower()
            if (
                n in text_compact
                or n_spaced in text
                or needle in (user_topic or "")
            ):
                _add(tags)
                break

    for raw in extra_tags or []:
        key = str(raw).strip().replace(" ", "_").lower()
        pack = _THEME_TAG_HINTS.get(key)
        if pack:
            _add(pack)

    return out


def ensure_theme_must_tags(
    tag_line: str,
    must_tags: list[str],
    *,
    max_tags: int = IMAGE_PROMPT_MAX_TAGS,
    priority_tags: list[str] | None = None,
) -> str:
    """Guarantee ``must_tags`` appear, then hard-cap (must tags win priority)."""
    if not must_tags:
        return cap_danbooru_tag_line(
            tag_line, max_tags=max_tags, priority_tags=priority_tags
        )
    injected = inject_identity_tags(tag_line or "", must_tags)
    prio = list(dict.fromkeys([*(must_tags or []), *(priority_tags or [])]))
    return cap_danbooru_tag_line(injected, max_tags=max_tags, priority_tags=prio)


def cap_danbooru_tag_line(
    tag_line: str | list[str],
    *,
    max_tags: int = IMAGE_PROMPT_MAX_TAGS,
    priority_tags: list[str] | None = None,
) -> str:
    """Hard-cap a comma-separated Danbooru tag line for image models.

    Keeps subject-count anchors and ``priority_tags`` (identity / focal) first,
    then fills from the original order until ``max_tags``.
    """
    if isinstance(tag_line, (list, tuple)):
        parts = [
            str(t).strip().replace(" ", "_") for t in tag_line if str(t).strip()
        ]
    else:
        parts = [
            t.strip().replace(" ", "_")
            for t in str(tag_line or "").split(",")
            if t.strip()
        ]
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in parts:
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(tag)
    if max_tags < 1 or len(deduped) <= max_tags:
        return ", ".join(deduped)

    by_key = {t.lower(): t for t in deduped}
    chosen: list[str] = []
    chosen_keys: set[str] = set()

    def _take(key: str) -> None:
        if key in chosen_keys or key not in by_key or len(chosen) >= max_tags:
            return
        chosen.append(by_key[key])
        chosen_keys.add(key)

    for tag in deduped:
        if tag.lower() in _SUBJECT_ANCHORS:
            _take(tag.lower())
    for raw in priority_tags or []:
        tag = str(raw).strip().replace(" ", "_")
        if tag:
            _take(tag.lower())
    for tag in deduped:
        _take(tag.lower())
        if len(chosen) >= max_tags:
            break
    return ", ".join(chosen)


def merge_chronicle_axis_tags(
    *,
    focal: list[str],
    search_tags: list[str],
    lock_tags: list[str],
    max_tags: int = IMAGE_PROMPT_MAX_TAGS,
) -> str:
    """Non-base Chronicle axis tag line: focal + WD14 search, then identity lock.

    Deliberately omits the base image's full WD14 / must-scene tags so past and
    present acts are not forced into the base setting (e.g. train interior).
    Only hair colour, eye colour, and accessories from identity_lock_tags propagate.

    Hard-capped to ``max_tags`` (default 20): anime image models degrade when
    the positive is flooded with tags.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for t in [*focal, *search_tags]:
        tag = str(t).strip().replace(" ", "_")
        k = tag.lower()
        if tag and k not in seen:
            seen.add(k)
            merged.append(tag)
    line = inject_identity_tags(", ".join(merged), lock_tags)
    return cap_danbooru_tag_line(
        line,
        max_tags=max_tags,
        priority_tags=[*lock_tags, *focal],
    )


_INLINE_TAG_GROUP_RE = re.compile(r"\(([^)]+)\)")


def collect_prompt_tags(positive: str) -> list[str]:
    """Harvest tag candidates from a positive prompt, underscore form, deduped.

    Comma-only lines contribute every segment (same heuristic as
    remove_conflict_tags); prose lines contribute their inline (tag, tag)
    groups, so natural-style prompts get conflict cleanup too.
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(raw: str) -> None:
        tag = raw.strip().replace(" ", "_")
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)

    for line in positive.split("\n"):
        if "," in line and "." not in line:
            for seg in line.split(","):
                _add(seg)
        else:
            for m in _INLINE_TAG_GROUP_RE.finditer(line):
                for seg in m.group(1).split(","):
                    _add(seg)
    return result


def assemble_capped_positive(
    tag_line: str,
    prose: str = "",
    *,
    priority_tags: list[str] | None = None,
    max_tags: int = IMAGE_PROMPT_MAX_TAGS,
    max_prose_words: int = IMAGE_PROMPT_MAX_PROSE_WORDS,
) -> str:
    """Build Comfy-ready positive: capped tag head + optional trimmed prose."""
    capped_tags = (
        cap_danbooru_tag_line(
            tag_line, max_tags=max_tags, priority_tags=priority_tags
        )
        if (tag_line or "").strip()
        else ""
    )
    capped_prose = (prose or "").strip()
    if capped_prose and max_prose_words > 0:
        words = capped_prose.split()
        if len(words) > max_prose_words:
            capped_prose = " ".join(words[:max_prose_words])
    if capped_tags and capped_prose:
        return f"{capped_tags}\n\n{capped_prose}"
    return capped_tags or capped_prose


# ── Emotion register (shared across story + image-prompt stages) ──────────────
#
# The 12 dimensions mirror ai.emotion_tagger.EMOTION_DIMENSIONS. Descriptions are
# distilled from vocab_bank._EMOTION_QUERIES so the guidance stays in sync with
# the tag semantics used elsewhere, but here it is a static instruction (no
# embedding lookup needed at prompt-build time).
_EMOTION_REGISTER = {
    "loneliness": "loneliness, solitude, isolation, quiet emptiness",
    "nostalgia":  "bittersweet nostalgia, faded memory, sepia longing",
    "ephemeral":  "fleeting, fragile, transient, a passing moment",
    "melancholy": "melancholy, wistful sorrow, subdued grey mood",
    "serenity":   "serenity, calm, peaceful stillness, gentle light",
    "wonder":     "wonder, awe, vast and breathtaking marvel",
    "joy":        "joy, bright cheerful playfulness, sunlit warmth",
    "tension":    "tension, suspense, unease, a sharp dramatic edge",
    "warmth":     "warmth, cozy tenderness, soft golden comfort",
    "mystery":    "mystery, enigmatic shadow, secrets half-veiled",
    "desolation": "desolation, ruin, abandonment, barren decay",
    "vitality":   "vitality, energy, vivid motion, life in bloom",
}


def _emotion_guidance_line(emotion: str, locale: str = "en") -> str:
    """Short mood field for prompts. Empty/unknown → ''."""
    key = (emotion or "").strip().lower()
    if key not in _EMOTION_REGISTER:
        return ""
    if locale == "ja":
        return f"\nFIELDS: mood={key}\n"
    return f"\nFIELDS: mood={key}\n"


def _compact_priority_checklist(
    *,
    topic: str = "",
    base_axis: str = "present",
    time_scale: str = "years",
    turn: str = "",
    mode: str = "",
    mood: str = "",
) -> str:
    """Short explicit contract for small LLMs (replaces long hierarchy essays)."""
    lines = [
        "PRIORITY (do in order; later may drop):",
        "1. topic" + (f'="{topic.strip()[:80]}"' if topic.strip() else ""),
        f"2. base_look / base_axis={base_axis}",
        f"3. time_scale={time_scale}",
    ]
    if turn.strip():
        lines.append(f"4. turn={turn.strip()[:100]}")
    fields = []
    if mode.strip():
        fields.append(f"mode={mode.strip()}")
    if mood.strip() and mood.strip().lower() in _EMOTION_REGISTER:
        fields.append(f"mood={mood.strip().lower()}")
    if fields:
        lines.append("FIELDS: " + " ".join(fields))
    return "\n".join(lines) + "\n"


def _scale_constraint_fields(
    time_scale: str, *, axis: str, base_axis: str
) -> str:
    """Compact must/may/forbid lines for non-base axes."""
    if axis == base_axis:
        return ""
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    return (
        f"must_keep: {rules['must_keep']}\n"
        f"may_differ: {rules['may_differ']}\n"
        f"forbid: {rules['forbidden']}\n"
        "camera: pick a framing different from the base "
        "(close-up, upper_body, dutch_angle, from_side, …)\n"
    )


def _minimal_densify_input_block(
    *,
    story_text: str,
    axis: str,
    title: str = "",
    visual_plan: dict | None = None,
) -> str:
    """Minimal tag-stage input (replaces FULL CHRONICLE essays)."""
    focal = [
        str(t).strip().replace(" ", "_")
        for t in (visual_plan or {}).get("focal_action_tags") or []
        if str(t).strip()
    ]
    situation = (story_text or "").strip()
    lines = [f"INPUT axis={axis}"]
    if title.strip():
        lines.append(f"title={title.strip()[:80]}")
    if situation:
        lines.append(f"situation={situation[:400]}")
    if focal:
        lines.append(f"focal=[{', '.join(focal)}]")
    return "\n".join(lines) + "\n"


def _candidate_modes_block(modes: dict[str, str]) -> str:
    """Enum-only dramatic modes per candidate (no prose descriptions)."""
    parts: list[str] = []
    for cid, flavour, _ in _CANDIDATE_SPIRITS:
        m = (modes.get(cid) or "").strip().lower()
        token = m if m in _DRAMATIC_MODES else "escalation"
        parts.append(f"{cid}({flavour})=mode:{token}")
    return "CANDIDATES: " + " ".join(parts) + "\n"


# ── Stage 3b split: 2-pass axis prompt for lightweight VLMs ──────────────────
#
# Refine's natural style (backend/app/api/ai.py) splits tag generation from
# prose so a small VLM only has to solve one problem at a time. Chronicle
# mirrors that pattern: build_axis_tags_prompt asks for a JSON tag payload,
# then build_axis_prose_prompt writes the 5-paragraph Visual Script on top
# of that tag line. This produces denser prompts than one-shot output on the
# same model at the same total token budget.


def sample_midrank_wd14_tags(
    ranked: list[str],
    *,
    lo: int = 20,
    hi: int = 50,
    k: int = 5,
    exclude: list[str] | None = None,
    rng=None,
) -> list[str]:
    """Randomly sample up to ``k`` tags from 1-based ranks ``lo``..``hi``.

    ``ranked`` is similarity order (best first). Ranks outside the list or
    already in ``exclude`` are skipped. Short pools return fewer than ``k``.
    """
    import random as _random

    if lo < 1 or hi < lo or k < 1:
        return []
    pool = [
        str(t).strip().replace(" ", "_")
        for t in ranked[lo - 1 : hi]
        if str(t).strip()
    ]
    ban = {
        str(t).strip().replace(" ", "_").lower()
        for t in (exclude or [])
        if str(t).strip()
    }
    candidates: list[str] = []
    seen: set[str] = set()
    for tag in pool:
        key = tag.lower()
        if key in ban or key in seen:
            continue
        seen.add(key)
        candidates.append(tag)
    if not candidates:
        return []
    pick = min(k, len(candidates))
    r = rng if rng is not None else _random
    return list(r.sample(candidates, pick))


def build_fast_prompts_prompt(
    *,
    user_topic: str,
    theme_must: list[str],
    character_tags: list[str],
    character_desc: str = "",
    beats: dict[str, str] | None = None,
    gen_axes: list[str] | None = None,
    time_scale: str = "years",
    worldview: str = "",
    emotion: str = "",
    biography: dict | None = None,
    tone: str = "",
    dramatic_mode: str = "",
    base_axis: str = "present",
    base_outfit: list[str] | None = None,
    outfit_rule: str = "",
) -> str:
    """One-shot JSON: danbooru tag lines for each axis (fast mode).

    Carries biography / elapsed timeline / tone so acts stay consistent.
    Theme must-tags are mandatory on every axis. Target ≥30 tags per axis.

    Fast mode has no acts, so there is no per-slice `outfit` field to thread:
    ``base_outfit`` (the source image's garment tags) + ``outfit_rule`` (the
    scale's clothing directive) give the model the same information directly.
    """
    axes = [a for a in (gen_axes or list(AXES)) if a in AXES] or list(AXES)
    beats = beats or {}
    base = (base_axis or "present").lower()
    if base not in AXES:
        base = "present"
    must = ", ".join(
        str(t).strip().replace(" ", "_") for t in (theme_must or []) if str(t).strip()
    ) or "(none — invent a concrete costume from the topic)"
    identity = ", ".join(
        str(t).strip().replace(" ", "_") for t in (character_tags or [])[:12]
        if str(t).strip()
    ) or "(infer from topic)"
    beat_lines = "\n".join(
        f"- {a.upper()}: {beats.get(a) or user_topic}" for a in axes
    )
    axis_keys = ", ".join(f'"{a}"' for a in axes)
    time_scale = normalize_time_scale(time_scale)
    elapsed = _elapsed_time_header(
        base_axis=base, time_scale=time_scale, locale="en"
    )
    bio_line = _biography_brief(biography)
    base_note = (
        f"BASE axis = [{base.upper()}] (t = 0). If a source image is used, that "
        "axis reuses the source and is NOT regenerated; other axes must read as "
        f"distinct moments on the {time_scale} scale."
    )
    outfit_tags = ", ".join(
        str(t).strip().replace(" ", "_") for t in (base_outfit or []) if str(t).strip()
    )
    outfit_line = (
        f"Base outfit tags (from the source image): {outfit_tags}\n"
        if outfit_tags else ""
    )
    outfit_directive = outfit_rule or scale_outfit_rule(time_scale)
    return (
        "You are a danbooru-tag expert. FAST MODE: emit ONE image-prompt "
        "tag line per act. No prose stories — tags only.\n\n"
        f"{elapsed}\n"
        f"{base_note}\n\n"
        f"お題 / TOPIC (authoritative costume & subject): {user_topic}\n"
        f"Worldview (mood / setting bias): {worldview or '(none)'}\n"
        f"Emotion register: {emotion or '(free)'}\n"
        f"Tone: {tone or 'neutral'}\n"
        f"Dramatic mode: {dramatic_mode or 'escalation'}\n"
        f"Time scale between acts: {time_scale}\n"
        f"PERSONALITY / biography (let mannerisms colour pose & expression):\n"
        f"  {bio_line}\n"
        f"Character identity tags (keep hair/eyes if present): {identity}\n"
        f"Character appearance note: {character_desc or '(none)'}\n"
        f"{outfit_line}"
        f"Act beats (vary pose/place/action; costume + identity stay):\n"
        f"{beat_lines}\n\n"
        f"THEME MUST-TAGS — copy VERBATIM into EVERY axis tag line:\n{must}\n\n"
        "[RULES]\n"
        f"- Each axis: AT LEAST {FAST_PROMPT_MIN_TAGS} comma-separated danbooru "
        f"tags (target {FAST_PROMPT_MIN_TAGS}–{FAST_PROMPT_MAX_TAGS}). "
        f"Under {FAST_PROMPT_MIN_TAGS} = failed prompt.\n"
        f"- Soft ceiling {FAST_PROMPT_MAX_TAGS}; do not pad with synonyms.\n"
        "- Open with subject-count (1girl / 1boy / solo / …).\n"
        "- THEME MUST-TAGS appear on every axis — never drop or paraphrase them.\n"
        "- OUTFIT: every axis tag line MUST state the clothing explicitly with "
        f"garment tags — never leave it implied. {outfit_directive}\n"
        "- Reflect biography personality in expression/pose tags when possible.\n"
        "- Honour the elapsed-time header: past/present/future must feel like "
        "different volumes (age/wear/setting shifts matching the time scale).\n"
        "- Include concrete pose/action, place, lighting, and a few props.\n"
        "- No quality meta-tags (masterpiece, best_quality, highres, …).\n"
        "- English danbooru snake_case only.\n\n"
        "Output JSON ONLY:\n"
        "{\n"
        f'  // keys: {axis_keys}\n'
        '  "past": "1girl, … (≥30 tags)",\n'
        '  "present": "1girl, … (≥30 tags)",\n'
        '  "future": "1girl, … (≥30 tags)",\n'
        '  "negative": "optional short comma-separated negatives"\n'
        "}"
    )


def parse_fast_prompts_json(raw: str) -> tuple[dict[str, str], str]:
    """Parse fast-mode JSON → ({axis: tag_line}, negative)."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {}, ""
    out: dict[str, str] = {}
    for axis in AXES:
        val = data.get(axis) or data.get(axis.upper()) or ""
        if isinstance(val, list):
            parts = [
                str(t).strip().replace(" ", "_") for t in val if str(t).strip()
            ]
            line = ", ".join(parts)
        else:
            parts = [
                t.strip().replace(" ", "_")
                for t in str(val).split(",") if t.strip()
            ]
            line = ", ".join(parts)
        if line:
            out[axis] = line
    neg = str(data.get("negative") or data.get("negative_supplement") or "").strip()
    return out, neg


def build_fast_candidate(
    user_topic: str,
    *,
    time_scale: str = "years",
    locale: str = "en",
    base_axis: str = "present",
    dramatic_mode: str = "",
    emotion: str = "",
) -> dict:
    """Synthetic single candidate for fast mode (no LLM pitch round)."""
    topic = (user_topic or "").strip() or "untitled"
    time_scale = normalize_time_scale(time_scale)
    one, two = _ELAPSED_UNIT.get(time_scale, _ELAPSED_UNIT["years"])
    one_ja, two_ja = _ELAPSED_UNIT_JA.get(time_scale, _ELAPSED_UNIT_JA["years"])
    base = (base_axis or "present").lower()
    if base not in AXES:
        base = "present"
    idx_base = AXES.index(base)
    mode = (dramatic_mode or "").strip()
    if mode not in _DRAMATIC_MODES:
        mode = "escalation"

    def _beat_en(axis: str) -> str:
        if axis == base:
            return f"Now (base, t=0) — {topic}"
        steps = abs(AXES.index(axis) - idx_base)
        phrase = one if steps == 1 else two
        direction = "later" if AXES.index(axis) > idx_base else "earlier"
        # Keep canonical casing from _ELAPSED_UNIT (do not .title()).
        return f"{phrase} {direction} — {topic}"

    def _beat_ja(axis: str) -> str:
        if axis == base:
            return f"いま（基準 t=0）— {topic}"
        steps = abs(AXES.index(axis) - idx_base)
        phrase = one_ja if steps == 1 else two_ja
        direction = "後" if AXES.index(axis) > idx_base else "前"
        return f"{phrase}{direction} — {topic}"

    if locale == "ja":
        return {
            "id": "A",
            "title": topic[:80],
            "overall": f"「{topic}」を軸に、{time_scale} スケールで隔たった三つの瞬間。",
            "past": _beat_ja("past"),
            "present": _beat_ja("present"),
            "future": _beat_ja("future"),
            "dramatic_mode": mode,
            "time_scale": time_scale,
        }
    return {
        "id": "A",
        "title": topic[:80],
        "overall": f"Three moments around “{topic}” on a {time_scale} scale.",
        "past": _beat_en("past"),
        "present": _beat_en("present"),
        "future": _beat_en("future"),
        "dramatic_mode": mode,
        "time_scale": time_scale,
    }


_CHRONICLE_MIN_TAGS = 25

# Pose tags that alone do NOT count as a drawable action (idle / portrait defaults).
_IDLE_POSE_TAGS = frozenset({
    "standing", "sitting", "kneeling", "lying", "crouching",
    "smile", "smiling", "blush", "closed_mouth", "open_mouth",
    "looking_at_viewer", "looking_away", "arms_at_sides", "static_pose",
    "cowboy_shot", "upper_body", "full_body", "portrait", "solo",
})
# Tokens that mark a real physical action in a danbooru tag.
_DYNAMIC_ACTION_TOKENS = frozenset({
    "reaching", "pointing", "walking", "running", "jumping", "falling",
    "holding", "gripping", "clenched", "touching", "grabbing", "hugging",
    "carrying", "lifting", "pushing", "pulling", "waving", "bowing",
    "bending", "stretching", "outstretched", "raised", "covering",
    "hiding", "pouring", "wiping", "writing", "reading", "eating",
    "drinking", "cooking", "kneading", "folding", "cutting", "painting",
    "typing", "dancing", "singing", "fighting", "throwing", "catching",
    "opening", "closing", "tying", "stirring", "spilling", "teaching",
    "sliding", "unlocking", "locking", "tamping", "steaming", "shaping",
    "both_hands", "surprised", "concentrating",
    "riding", "pedaling", "fluttering", "cheering", "clinking", "toasting",
    "laughing", "winking",
})

# Face / mood tags: _EXPRESSION_TAGS / _EXPRESSION_TOKENS from tags.catalog (top).

# Shared identity / quality noise stripped before cross-axis tag comparison.
_TAG_COMPARE_IGNORE = _SUBJECT_ANCHORS | _IDLE_POSE_TAGS | frozenset({
    "highres", "absurdres", "masterpiece", "best_quality", "detailed_background",
    "depth_of_field", "cinematic_lighting", "sharp_focus", "dynamic_angle",
    "looking_at_viewer", "detailed",
})


def _tag_has_dynamic_action(parts: list[str]) -> bool:
    for raw in parts:
        toks = set(raw.lower().replace("-", "_").split("_"))
        if toks & _DYNAMIC_ACTION_TOKENS:
            return True
        # Compound tags like "holding_cup" / "spilling_milk"
        if any(tok in raw.lower() for tok in _DYNAMIC_ACTION_TOKENS):
            return True
    return False


def _first_expression_tag(parts: list[str]) -> str:
    """The first face/mood expression tag in a tag line, '' if none.

    Single source of truth for "what counts as an expression" — _tag_has_
    expression and lead_with_face_tags both derive from it, so the guarantee
    and the ordering can never disagree about the same tag line.
    """
    for raw in parts:
        t = raw.strip().lower().replace(" ", "_")
        if not t:
            continue
        if t in _EXPRESSION_TAGS:
            return raw.strip()
        toks = set(t.replace("-", "_").split("_"))
        if toks & _EXPRESSION_TOKENS:
            return raw.strip()
    return ""


def _tag_has_expression(parts: list[str]) -> bool:
    """True if the tag line contains at least one face/mood expression tag."""
    return bool(_first_expression_tag(parts))


# feeling word → danbooru expression tag. Values MUST be members of
# _EXPRESSION_TAGS (guarded by tests) — anime models omit the face entirely
# when the prompt carries no expression tag, so every axis prompt gets one.
_FEELING_EXPRESSION_MAP: dict[str, str] = {
    "joy": "smile", "joyful": "smile", "happy": "smile", "warm": "smile",
    "hopeful": "smile", "glad": "smile", "cheerful": "smile", "calm": "smile",
    "serene": "smile", "peaceful": "smile", "relieved": "smile",
    "content": "smile", "inspired": "smile", "confident": "smug",
    "excited": "excited", "thrilled": "excited",
    "sad": "sad", "sorrowful": "sad", "melancholy": "sad", "wistful": "sad",
    "gloomy": "sad", "lonely": "lonely",
    "tearful": "crying", "weeping": "crying", "drowsy": "sleepy",
    "surprised": "surprised", "startled": "surprised", "shocked": "surprised",
    "amazed": "surprised",
    "determined": "determined", "resolute": "determined",
    "focused": "serious", "intent": "serious", "stern": "serious",
    "nervous": "nervous", "anxious": "nervous", "worried": "nervous",
    "uneasy": "nervous", "tense": "nervous",
    "angry": "angry", "furious": "angry", "annoyed": "annoyed",
    "embarrassed": "embarrassed", "shy": "embarrassed", "flustered": "embarrassed",
    "curious": "awe", "intrigued": "awe",
}
_FEELING_EXPRESSION_JA: tuple[tuple[str, str], ...] = (
    ("嬉", "smile"), ("楽", "smile"), ("温", "smile"), ("穏", "smile"),
    ("悲", "sad"), ("寂", "lonely"), ("涙", "crying"), ("泣", "crying"),
    ("驚", "surprised"), ("怒", "angry"), ("決意", "determined"),
    ("緊張", "nervous"), ("不安", "nervous"), ("照", "embarrassed"),
)
_EMOTION_EXPRESSION_MAP: dict[str, str] = {
    "loneliness": "lonely", "nostalgia": "sad", "ephemeral": "sad",
    "melancholy": "sad", "serenity": "smile", "wonder": "surprised",
    "joy": "smile", "tension": "nervous", "warmth": "smile",
    "mystery": "serious", "desolation": "sad", "vitality": "excited",
}


def expression_tag_for_feeling(feeling: str, *, emotion: str = "") -> str:
    """Map an act's feeling word to a valid danbooru expression tag.

    Every branch returns a member of _EXPRESSION_TAGS; final fallback is
    "smile" so a face is ALWAYS requested.
    """
    def _resolve(word: str) -> str:
        w = (word or "").strip().lower().replace(" ", "_")
        if not w:
            return ""
        if w in _EXPRESSION_TAGS:
            return w
        mapped = _FEELING_EXPRESSION_MAP.get(w)
        if mapped and mapped in _EXPRESSION_TAGS:
            return mapped
        for key, tag in _FEELING_EXPRESSION_JA:
            if key in word and tag in _EXPRESSION_TAGS:
                return tag
        toks = set(w.replace("-", "_").split("_"))
        if toks & _EXPRESSION_TOKENS:
            for cand in sorted(_EXPRESSION_TAGS):
                if set(cand.replace("-", "_").split("_")) & toks:
                    return cand
        return ""

    got = _resolve(feeling)
    if got:
        return got
    e = (emotion or "").strip().lower()
    mapped = _EMOTION_EXPRESSION_MAP.get(e)
    if mapped and mapped in _EXPRESSION_TAGS:
        return mapped
    got = _resolve(e)
    return got or "smile"


def ensure_face_tags(
    positive: str,
    *,
    expression_tag: str,
    lock_tags: list[str],
    priority_tags: list[str] | None = None,
    max_tags: int | None = None,
) -> str:
    """Guarantee eye colour + one expression tag, and make them LEAD.

    Anime models drop the character's face when neither survives the ≤20 cap
    (measured by the user), and weight them weakly when they trail the line —
    so the face tags must be both present AND at the front. This is the
    last-line guard after all assembly and conflict passes. Only acts on a
    comma tag-line head; prose-only positives pass through unchanged.

    Order: guarantee → cap → lead. Leading must come LAST because
    cap_danbooru_tag_line reorders to anchors → priority_tags → rest whenever
    it trims, which would scatter the face tags back down the line.
    """
    head, sep, tail = positive.partition("\n\n")
    if "," not in head or "." in head:
        return positive
    parts = [t.strip() for t in head.split(",") if t.strip()]
    keys = {t.lower().replace(" ", "_") for t in parts}

    face_musts: list[str] = []
    for t in lock_tags:
        if classify_identity_tag(t) == "eyes" and t.lower().replace(" ", "_") not in keys:
            face_musts.append(t)
    if expression_tag and not _tag_has_expression(parts):
        face_musts.append(expression_tag)

    new_head = head
    if face_musts:
        limit = max_tags if max_tags is not None else IMAGE_PROMPT_MAX_TAGS
        new_head = insert_after_anchors(new_head, face_musts)
        new_head = cap_danbooru_tag_line(
            new_head, max_tags=limit,
            priority_tags=list(dict.fromkeys([*face_musts, *(priority_tags or [])])),
        )
    # Runs even when nothing was missing: the tags are often already present
    # but buried mid-line, and cap_danbooru_tag_line leaves a short line in its
    # original order, so this is the only pass that fixes that case.
    new_head = lead_with_face_tags(
        new_head, expression_tag=expression_tag, lock_tags=lock_tags,
    )
    if new_head == head:
        return positive
    return f"{new_head}{sep}{tail}" if sep else new_head


def lead_with_face_tags(
    tag_line: str,
    *,
    expression_tag: str = "",
    lock_tags: list[str] | None = None,
) -> str:
    """Reorder a tag line to: subject anchors, eye colour, expression, rest.

    Pure reorder + case-insensitive dedup — never drops a tag, never caps,
    idempotent.

    The subject anchors keep the front: they are the subject-count contract of
    the whole tag layer (ensure_subject_anchor *prepends* a recovered anchor,
    so putting eyes at absolute index 0 would just fight it). They cost two
    tokens and carry no colour or affect information, so they cannot dilute the
    face conditioning that follows them.

    When the base image has no eye-colour tag (or identity_lock_tags dropped it
    for a multi-character base), no eye colour can be invented — the expression
    then leads alone.
    """
    parts = [t.strip() for t in tag_line.split(",") if t.strip()]
    if not parts:
        return tag_line

    def key(t: str) -> str:
        return t.lower().replace(" ", "_")

    anchors = [p for p in parts if key(p) in _SUBJECT_ANCHORS]
    eyes = [p for p in parts if classify_identity_tag(p) == "eyes"]
    if not eyes:
        for t in lock_tags or []:
            if classify_identity_tag(t) == "eyes":
                eyes = [t]
                break
    expr = _first_expression_tag(parts)
    if not expr and expression_tag:
        expr = expression_tag

    lead: list[str] = []
    seen: set[str] = set()
    for t in [*anchors, *eyes, *([expr] if expr else [])]:
        if t and key(t) not in seen:
            seen.add(key(t))
            lead.append(t)
    rest: list[str] = []
    for p in parts:
        if key(p) in seen:
            continue
        seen.add(key(p))
        rest.append(p)
    return ", ".join([*lead, *rest])


def _tag_has_person_subject(parts: list[str]) -> bool:
    """True when the prompt depicts a person (expression then becomes mandatory)."""
    head = {p.strip().lower().replace(" ", "_") for p in parts if p.strip()}
    if head & _SUBJECT_ANCHORS:
        return True
    return any(
        "girl" in p or "boy" in p or "woman" in p or "man" in p or "person" in p
        for p in head
    )


def _chronicle_tags_degenerate(tag_line: str) -> tuple[bool, str]:
    """Guard for Pass 1 output — same spirit as Invoke's runners.py:1798-1808.

    A prompt is degenerate if it is too short, has no subject anchor within
    the first few tags, has no dynamic action (idle standing/smile only),
    OR (when a person is on-screen) has no facial expression tag — emotion
    cannot read without one.
    Callers retry once with a temperature boost before surfacing the failure.
    """
    parts = [t.strip() for t in tag_line.split(",") if t.strip()]
    if len(parts) < _CHRONICLE_MIN_TAGS:
        return True, f"tag_count={len(parts)}"
    head = {p.lower() for p in parts[:5]}
    if not (head & _SUBJECT_ANCHORS):
        return True, "no_subject_anchor"
    if not _tag_has_dynamic_action(parts):
        return True, "no_dynamic_action"
    if _tag_has_person_subject(parts) and not _tag_has_expression(parts):
        return True, "no_expression"
    return False, ""


def _content_tag_set(tag_line: str) -> set[str]:
    """Tag set used for cross-axis diversity — drops identity/idle/quality noise."""
    out: set[str] = set()
    for raw in tag_line.split(","):
        t = raw.strip().lower().replace(" ", "_")
        if not t or t in _TAG_COMPARE_IGNORE:
            continue
        # Densify / sim padding (detail_8 …) is not scene content.
        if t.startswith("detail_"):
            continue
        # Drop pure hair/eye colour locks from comparison (identity, not scene).
        toks = set(t.split("_"))
        if toks & {"hair", "eyes", "eyecolor"} and "ornament" not in toks:
            if any(c in t for c in (
                "blonde", "silver", "blue", "red", "green", "brown", "black",
                "white", "pink", "purple", "orange", "grey", "gray",
            )):
                continue
        out.add(t)
    return out


_AXIS_TAG_SIMILAR_THRESHOLD = 0.75


def axis_tag_lines_collapsed(
    tag_lines: dict[str, str],
    *,
    threshold: float = _AXIS_TAG_SIMILAR_THRESHOLD,
) -> bool:
    """True when past/present/future content tags collapse into one scene.

    Compares content tags (scene/action/props) after stripping identity locks
    and idle portrait defaults. Axes with empty / too-thin tag lines are
    skipped so incomplete builds do not false-positive.

    Also collapses when *scene/prop* tags alone (verbs stripped) stay nearly
    identical — catches knead/fold/shape paraphrases of the same kitchen beat.
    """
    sets = []
    scene_sets = []
    for a in AXES:
        s = _content_tag_set(tag_lines.get(a, ""))
        if len(s) >= 3:
            sets.append(s)
            scene_sets.append({
                t for t in s
                if t not in _DYNAMIC_ACTION_TOKENS
                and t not in _EXPRESSION_TAGS
                and not (set(t.split("_")) & _DYNAMIC_ACTION_TOKENS)
                and not (set(t.split("_")) & _EXPRESSION_TOKENS)
            })
    if len(sets) < 2:
        return False

    def _mean_jaccard(group: list[set[str]]) -> float:
        sims = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if not a or not b:
                    continue
                sims.append(len(a & b) / len(a | b))
        return (sum(sims) / len(sims)) if sims else 0.0

    if _mean_jaccard(sets) >= threshold:
        return True
    # Scene/prop-only: require a bit more overlap (same place + props).
    scene_sets = [s for s in scene_sets if len(s) >= 2]
    if len(scene_sets) >= 2 and _mean_jaccard(scene_sets) >= max(threshold, 0.75):
        return True
    return False


def remove_conflict_tags(
    positive: str, conflicts: set[str], *, include_prose_groups: bool = False
) -> str:
    """Remove conflicting danbooru tags from the tag portion of a positive prompt.

    Comma-separated tag lines are always filtered (matching how
    _find_conflict_tags reports plain tag names). With include_prose_groups,
    inline (tag, tag) groups on prose lines are filtered too; a group whose
    tags are all removed disappears entirely (never an empty "()").
    """
    if not conflicts:
        return positive

    def _fix_group(m: re.Match) -> str:
        tags = [t.strip() for t in m.group(1).split(",")]
        kept = [t for t in tags if t and t.replace(" ", "_") not in conflicts]
        return f"({', '.join(kept)})" if kept else ""

    lines = positive.split("\n")
    cleaned: list[str] = []
    for line in lines:
        if "," in line and "." not in line:
            tags = [t.strip() for t in line.split(",")]
            kept = [t for t in tags if t and t.replace(" ", "_") not in conflicts]
            cleaned.append(", ".join(kept))
        elif include_prose_groups:
            fixed = _INLINE_TAG_GROUP_RE.sub(_fix_group, line)
            cleaned.append(re.sub(r"  +", " ", fixed))
        else:
            cleaned.append(line)
    return "\n".join(cleaned)


# ── Scene constraints + mechanical mutex conflicts ────────────────────────────
#
# WD14 vector search returns semantic neighbours, not logically consistent tags
# (a night story can still pull day / blue_sky). These helpers extract cheap
# structured constraints from finished act prose and strip exclusive opposites
# before the LLM conflict pass — prevention beats post-hoc cleanup.

_TIME_OF_DAY_FAMILIES: dict[str, frozenset[str]] = {
    "day": frozenset({
        "day", "daylight", "daytime", "morning", "afternoon", "noon",
        "sunrise", "sunny", "blue_sky", "bright_sky", "sunlight",
        "morning_sun", "afternoon_sun", "sunbeam", "sunbeams", "broad_daylight",
    }),
    "night": frozenset({
        "night", "nighttime", "night_sky", "midnight", "moon", "moonlight",
        "full_moon", "crescent_moon", "starry_sky", "stars", "star_(sky)",
        "dark", "darkness", "lamp", "streetlamp", "neon_lights", "night_lights",
    }),
    "dusk": frozenset({
        "dusk", "sunset", "evening", "twilight", "golden_hour", "orange_sky",
        "afterglow", "evening_sky",
    }),
    "dawn": frozenset({
        "dawn", "daybreak", "early_morning", "sunrise",
    }),
}

_INDOOR_OUTDOOR_FAMILIES: dict[str, frozenset[str]] = {
    "indoor": frozenset({
        "indoors", "indoor", "inside", "bedroom", "classroom", "kitchen",
        "bathroom", "living_room", "library", "cafe", "shop_interior",
        "train_interior", "office", "hospital", "corridor", "hallway",
        "restaurant", "bar_(place)",
    }),
    "outdoor": frozenset({
        "outdoors", "outdoor", "outside", "park", "street", "cityscape",
        "road", "field", "forest", "beach", "rooftop", "sky", "clouds",
        "garden", "bridge", "alley", "mountain", "shore", "plaza",
    }),
}

# Canonical must-tag for each family (injected when the story implies that side).
_FAMILY_MUST_TAGS: dict[str, tuple[str, ...]] = {
    "day": ("day", "daylight"),
    "night": ("night",),
    "dusk": ("dusk", "sunset"),
    "dawn": ("dawn",),
    "indoor": ("indoors",),
    "outdoor": ("outdoors",),
}

# Story-prose keyword → family (scored by substring hits; English acts only —
# axis tagging always runs on the English canonical copy).
_TIME_STORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "night": (
        "night", "midnight", "moonlight", "moonlit", "starry", "nocturnal",
        "lamp-lit", "lamplit", "by moonlight", "under the moon", "after dark",
        "in the dark", "nighttime", "night time",
    ),
    "day": (
        "morning", "afternoon", "noon", "midday", "daylight", "daytime",
        "sunny", "sunlit", "sunshine", "broad daylight", "in the sun",
    ),
    "dusk": (
        "dusk", "sunset", "evening", "twilight", "golden hour", "at dusk",
        "as the sun set", "as the sun sets",
    ),
    "dawn": (
        "dawn", "daybreak", "at sunrise", "first light", "early morning",
    ),
}

_PLACE_STORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "indoor": (
        "indoors", "indoor", "inside", "bedroom", "classroom", "kitchen",
        "bathroom", "living room", "library", "cafe", "café", "corridor",
        "hallway", "office", "hospital", "train car", "train carriage",
        "shop interior", "in her room", "in the room", "at the desk",
    ),
    "outdoor": (
        "outdoors", "outdoor", "outside", "park", "street", "rooftop",
        "beach", "forest", "garden", "bridge", "plaza", "alley",
        "under the open sky", "on the hill", "in the field",
    ),
}


def _score_family_keywords(text: str, families: dict[str, tuple[str, ...]]) -> str:
    """Return the winning family key, or '' on tie / no hits."""
    scores = {
        key: sum(1 for kw in kws if kw in text)
        for key, kws in families.items()
    }
    best = max(scores.values()) if scores else 0
    if best <= 0:
        return ""
    winners = [k for k, v in scores.items() if v == best]
    return winners[0] if len(winners) == 1 else ""


def _forbid_for_family(
    chosen: str, families: dict[str, frozenset[str]]
) -> list[str]:
    if not chosen or chosen not in families:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for key, tags in families.items():
        if key == chosen:
            continue
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def infer_axis_scene_constraints(story_text: str) -> dict:
    """Extract must/forbid scene tags from finished act prose (no LLM).

    Returns:
        {
          "time_of_day": "night"|"day"|"dusk"|"dawn"|"",
          "indoor_outdoor": "indoor"|"outdoor"|"",
          "must_tags": [...],
          "forbid_tags": [...],
        }
    """
    text = re.sub(r"\s+", " ", (story_text or "").lower())
    time_key = _score_family_keywords(text, _TIME_STORY_KEYWORDS)
    place_key = _score_family_keywords(text, _PLACE_STORY_KEYWORDS)

    must: list[str] = []
    forbid: list[str] = []
    seen_must: set[str] = set()
    seen_forbid: set[str] = set()

    def _add_must(family: str) -> None:
        for t in _FAMILY_MUST_TAGS.get(family, ()):
            if t not in seen_must:
                seen_must.add(t)
                must.append(t)

    def _add_forbid(tags: list[str]) -> None:
        for t in tags:
            if t not in seen_forbid and t not in seen_must:
                seen_forbid.add(t)
                forbid.append(t)

    if time_key:
        _add_must(time_key)
        _add_forbid(_forbid_for_family(time_key, _TIME_OF_DAY_FAMILIES))
    if place_key:
        _add_must(place_key)
        _add_forbid(_forbid_for_family(place_key, _INDOOR_OUTDOOR_FAMILIES))

    return {
        "time_of_day": time_key,
        "indoor_outdoor": place_key,
        "must_tags": must,
        "forbid_tags": forbid,
    }


def apply_scene_constraints(
    tags: list[str],
    constraints: dict | None,
) -> list[str]:
    """Drop forbid tags and ensure must tags are present (order-preserving)."""
    if not constraints:
        return [str(t).strip().replace(" ", "_") for t in tags if str(t).strip()]

    forbid = {
        str(t).strip().lower().replace(" ", "_")
        for t in (constraints.get("forbid_tags") or [])
        if t
    }
    must = [
        str(t).strip().replace(" ", "_")
        for t in (constraints.get("must_tags") or [])
        if str(t).strip()
    ]

    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw).strip().replace(" ", "_")
        key = tag.lower()
        if not tag or key in forbid or key in seen:
            continue
        seen.add(key)
        out.append(tag)

    # Prepend must tags that survived forbid (identity/subject stay caller-side).
    inject: list[str] = []
    for tag in must:
        key = tag.lower()
        if key in forbid or key in seen:
            continue
        seen.add(key)
        inject.append(tag)
    return inject + out


def find_mutex_conflict_tags(
    tags: list[str],
    *,
    preferred: list[str] | None = None,
) -> set[str]:
    """Return tags that lose an exclusive-family fight (day↔night, indoor↔outdoor).

    Within each mutex group, at most one family may survive. Preference order:
    1. family overlapping ``preferred`` (story must-tags / identity lock)
    2. otherwise the first family encountered in ``tags`` order
    Losing families' tags are reported as conflicts for remove_conflict_tags.
    """
    norm = [str(t).strip().replace(" ", "_") for t in tags if str(t).strip()]
    pref = {
        str(t).strip().lower().replace(" ", "_")
        for t in (preferred or [])
        if t
    }
    conflicts: set[str] = set()

    for families in (_TIME_OF_DAY_FAMILIES, _INDOOR_OUTDOOR_FAMILIES):
        present: dict[str, list[str]] = {}
        for tag in norm:
            key = tag.lower()
            for fam, members in families.items():
                if key in members:
                    present.setdefault(fam, []).append(tag)
                    break
        if len(present) < 2:
            continue

        winner = ""
        for fam, members in present.items():
            if any(t.lower() in pref for t in members) or (fam in pref):
                winner = fam
                break
        if not winner:
            # First tag in input order decides the surviving family.
            for tag in norm:
                key = tag.lower()
                for fam, members in families.items():
                    if key in members:
                        winner = fam
                        break
                if winner:
                    break

        for fam, members in present.items():
            if fam == winner:
                continue
            for t in members:
                conflicts.add(t.replace(" ", "_"))

    return conflicts


def find_identity_mutex_conflicts(
    tags: list[str],
    lock_tags: list[str],
) -> set[str]:
    """Hair-color / eye-color tags that contradict the identity lock.

    If the lock pins ``blonde_hair``, any other ``*_hair`` color in ``tags`` is
    a conflict. Same for eyes. Non-color identity tags are left alone.
    """
    locks = [str(t).strip().replace(" ", "_") for t in lock_tags if t]
    lock_hair = {t.lower() for t in locks if classify_identity_tag(t) == "hair_color"}
    lock_eyes = {t.lower() for t in locks if classify_identity_tag(t) == "eyes"}
    if not lock_hair and not lock_eyes:
        return set()

    conflicts: set[str] = set()
    for raw in tags:
        tag = str(raw).strip().replace(" ", "_")
        if not tag:
            continue
        key = tag.lower()
        cat = classify_identity_tag(tag)
        if cat == "hair_color" and lock_hair and key not in lock_hair:
            conflicts.add(tag)
        elif cat == "eyes" and lock_eyes and key not in lock_eyes:
            conflicts.add(tag)
    return conflicts


# ── Phase B: draft-image grounding ────────────────────────────────────────────
#
# Non-base axes have no real image yet, so WD14 vocab search is text-only and
# drifts. Generate a cheap low-res draft, scan it with WD14, and rebuild the
# axis prompt from those image-grounded tags before the final gen.
#
# This is the main quality lever: the image model already "knows" lighting,
# atmosphere, props and pose that small VLMs invent poorly — we borrow that
# expression by reading the draft back through WD14.

# Auto: skip only micro scales (same-scene micro-shifts); everything else drafts.
# Backward-compat alias used by older tests / callers.

# Tags that are identity / count anchors — never replaced by draft WD14 alone.
_DRAFT_KEEP_CATEGORIES = frozenset({
    "hair_color", "hair_style", "eyes", "face", "accessory",
})

# Draft WD14 tags in these families are the image model's expressive gift —
# promote them ahead of text-search vocab so the rebuild keeps that richness.
_DRAFT_RICHNESS_TOKENS = frozenset({
    # lighting
    "sunset", "sunrise", "golden_hour", "dusk", "dawn", "rim_light", "backlight",
    "lens_flare", "volumetric", "god_rays", "sunbeam", "shadow", "warm_light",
    "cool_light", "neon", "cinematic_lighting", "glow", "sparkle", "moonlight",
    "daylight", "afternoon", "evening", "morning", "night",
    # environment
    "street", "road", "alley", "cityscape", "shop", "storefront", "cafe", "bar",
    "window", "building", "streetlamp", "banner", "mountain", "sky", "cloud",
    "stadium", "crowd", "audience", "bleachers", "plant", "flower", "outdoors",
    "indoors", "scenery", "rooftop", "park", "bridge",
    # props / atmosphere / motion the model invented
    "bicycle", "bike", "scarf", "medal", "trophy", "mug", "beer", "confetti",
    "streamer", "umbrella", "wind", "dust", "particle", "bokeh", "haze",
    "riding", "pedaling", "fluttering", "cheering", "dynamic_pose", "motion_blur",
})


def _is_draft_richness_tag(tag: str) -> bool:
    t = tag.strip().lower().replace(" ", "_").replace("-", "_")
    if not t:
        return False
    if t in _DRAFT_RICHNESS_TOKENS:
        return True
    toks = set(t.split("_"))
    return bool(toks & _DRAFT_RICHNESS_TOKENS)


def merge_draft_wd14_tags(
    *,
    vocab_tags: list[str],
    draft_tags: list[str],
    lock_tags: list[str] | None = None,
    focal: list[str] | None = None,
) -> list[str]:
    """Blend text-search tags with image-grounded draft WD14 tags.

    Draft scene/pose/lighting tags are promoted ahead of vocab near-misses so
    the image model's expression (rim light, storefront, confetti…) survives
    into the final prompt. Identity lock and subject anchors always win.
    Focal action tags stay near the front.
    """
    locks = [str(t).strip().replace(" ", "_") for t in (lock_tags or []) if t]
    lock_keys = {t.lower() for t in locks}
    focal_norm = [str(t).strip().replace(" ", "_") for t in (focal or []) if t]

    def _overlap_conflict(a: str, b: str) -> bool:
        if a == b:
            return False
        ta = {t for t in a.lower().split("_") if len(t) >= 3}
        tb = {t for t in b.lower().split("_") if len(t) >= 3}
        return bool(ta & tb)

    # Drop draft tags that fight identity lock (wrong hair/eye color etc.).
    draft_clean: list[str] = []
    seen_draft: set[str] = set()
    for raw in draft_tags:
        tag = str(raw).strip().replace(" ", "_")
        key = tag.lower()
        if not tag or key in seen_draft:
            continue
        cat = classify_identity_tag(tag)
        if cat in ("hair_color", "eyes") and lock_keys:
            if key not in lock_keys:
                continue
        if cat in _DRAFT_KEEP_CATEGORIES and lock_keys and key not in lock_keys:
            if any(classify_identity_tag(l) == cat for l in locks):
                continue
        seen_draft.add(key)
        draft_clean.append(tag)

    draft_keys = {t.lower() for t in draft_clean}

    # Prefer draft side of exclusive scene families (day↔night, indoor↔outdoor).
    mutex_drop = {
        t.lower()
        for t in find_mutex_conflict_tags(
            draft_clean + [
                str(t).strip().replace(" ", "_") for t in vocab_tags if t
            ],
            preferred=draft_clean,
        )
    }

    vocab_kept: list[str] = []
    seen_vocab: set[str] = set()
    for raw in vocab_tags:
        tag = str(raw).strip().replace(" ", "_")
        key = tag.lower()
        if (
            not tag
            or key in seen_vocab
            or key in draft_keys
            or key in lock_keys
            or key in mutex_drop
        ):
            continue
        if any(_overlap_conflict(tag, d) for d in draft_clean):
            continue
        seen_vocab.add(key)
        vocab_kept.append(tag)

    # Split draft into richness-first vs remainder so lighting/env/props win.
    draft_rich = [t for t in draft_clean if _is_draft_richness_tag(t)]
    draft_rest = [t for t in draft_clean if not _is_draft_richness_tag(t)]

    out: list[str] = []
    seen: set[str] = set()

    def _add(seq: list[str]) -> None:
        for tag in seq:
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                out.append(tag)

    _add(focal_norm)
    _add(draft_rich)   # image-model expression first
    _add(draft_rest)
    _add(vocab_kept)
    _add(locks)
    capped = cap_danbooru_tag_line(
        out,
        max_tags=IMAGE_PROMPT_MAX_TAGS,
        priority_tags=[*focal_norm, *locks],
    )
    return [t.strip() for t in capped.split(",") if t.strip()]


def draft_richness_delta(
    *,
    before_tag_line: str,
    after_tag_line: str,
) -> dict:
    """Compare richness before/after draft merge (lazy import to avoid cycles)."""
    try:
        from .quality import score_prompt_richness
    except Exception:
        return {}
    before = score_prompt_richness(before_tag_line)
    after = score_prompt_richness(after_tag_line)
    return {
        "before": before.get("score", 0.0),
        "after": after.get("score", 0.0),
        "delta": round(
            float(after.get("score", 0.0)) - float(before.get("score", 0.0)), 3
        ),
        "draft_lighting": after.get("lighting", 0),
        "draft_environment": after.get("environment", 0),
        "draft_props": after.get("props", 0),
    }


# ── Final stage: translate the user-language chronicle into English ──────────
# (image prompts always work in English; skipped when the locale is already en)


def parse_flat_json_translation(raw: str, keys: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Parse a flat {key: text} translation output. Missing/broken → '' per key."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {k: "" for k in keys}
    return {k: str(data.get(k) or "").strip() for k in keys}


# ── Story arc (single-call candidates × acts) ─────────────────────────────────
#
# The redesigned pipeline authors the WHOLE story in ONE structured JSON call:
# three candidates, each with a full three-act arc (activity / place / feeling
# per act) plus motif / turn / personality hint. Everything downstream is
# deterministic code — expansion never re-creates the story, so the selected
# candidate can no longer drift. Validation is code-side (topic anchors,
# temporal distinctness, act labels) with EXACTLY ONE feedback retry.

# `outfit` exists because the image model cannot infer clothing: the scale
# rules told the LLM to keep the outfit consistent, but there was no field to
# write it into, so it never reached a prompt and every axis re-rolled the
# clothes.
_ACT_KEYS = ("label", "activity", "place", "feeling", "outfit", "motif_use")

# Concrete example values only — bonsai-27b copies placeholder text verbatim
# (measured), so the few-shot must never contain meta descriptions. The outfit
# is deliberately IDENTICAL across this short-delta example: copying that is
# the behaviour we want at minutes/hours scale.
_ARC_FEWSHOT = (
    "Example of GOOD concrete output (structure only — invent your own story):\n"
    '{"candidates":[{"id":"A","title":"Steam on the Portafilter",'
    '"dramatic_mode":"escalation","motif":"order memo",'
    '"turn":"The memo name belongs to someone she thought had left town.",'
    '"personality_hint":"Methodical and warm; always double-folds paper slips.",'
    '"acts":{'
    '"past":{"label":"2 hours earlier","activity":"She tamps coffee into the portafilter with both palms","place":"behind the cafe counter","feeling":"focused","outfit":"black apron over a white shirt"},'
    '"present":{"label":"now","activity":"She slides a ceramic cup across the wooden counter to a regular","place":"cafe counter, morning light","feeling":"warm","outfit":"black apron over a white shirt"},'
    '"future":{"label":"3 hours later","activity":"She unfolds the crumpled order memo under the till lamp at close","place":"empty cafe at dusk","feeling":"startled","outfit":"white shirt, apron untied and hanging"}'
    '}}]}'
)

# Arc output is ALWAYS English: bonsai-class local models author far better EN
# than ja (measured — ja output came back Chinese-contaminated and off-topic),
# and the whole downstream pipeline (pose/scene retrieval, WD14, image prompts)
# is English-only anyway. The ja UI gets a batched display translation instead.
_ARC_OUTPUT_LINE = (
    "Write every title / activity / place / feeling / outfit / motif / turn / "
    "personality_hint field in natural ENGLISH (even when the topic is "
    "Japanese — do not translate the topic, follow it)."
)

# Feeling-word hints used when composing the base act from an image.
_EXPRESSION_TO_FEELING: dict[str, str] = {
    "smile": "warm", "smiling": "warm", "grin": "cheerful", "laughing": "joyful",
    "happy": "joyful", "sad": "sad", "crying": "tearful", "tears": "tearful",
    "serious": "focused", "expressionless": "calm", "angry": "angry",
    "surprised": "surprised", "nervous": "nervous", "embarrassed": "embarrassed",
    "closed_eyes": "serene", "sleepy": "drowsy", "smug": "confident",
}


def outfit_tags_from_wd14(wd14_tags: list[str], *, limit: int = 6) -> list[str]:
    """Garment tags of the base image, WD14 order kept.

    Union of the garment-token classifier and the catalog clothing axis, so
    both `school_uniform` (token hit) and `serafuku` (catalog hit) are caught.
    Accessories are excluded — identity_lock_tags already carries those, and
    double-listing them would burn the ≤20 tag budget twice.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in wd14_tags or []:
        t = str(raw or "").strip().lower().replace(" ", "_")
        if not t or t in seen:
            continue
        cat = classify_identity_tag(t)
        if cat == "accessory":
            continue
        if cat == "outfit" or get_tag_axis(t) == "clothing":
            seen.add(t)
            out.append(t)
            if len(out) >= limit:
                break
    return out


def base_act_from_image(
    wd14_tags: list[str], scene_desc: str, *, emotion: str = ""
) -> dict:
    """{activity, place, feeling, outfit} in EN, from the base image.

    This is the FIXED line of the script scaffold: when the user hands us an
    image, the base act IS that image — never something the story LLM invents.

    `outfit` has no scene_desc fallback: build_vision_prompt(full_extraction=
    False) explicitly tells the VLM not to describe clothing, so scene_desc has
    no outfit content to mine. Empty string degrades to the old behaviour.
    """
    from ..tags.catalog import pose_action_subset

    tags = [str(t or "").strip().lower().replace(" ", "_") for t in (wd14_tags or [])]
    tags = [t for t in tags if t]

    def _first_sentence(text: str, max_words: int) -> str:
        s = (text or "").strip().split(".")[0].strip()
        return " ".join(s.split()[:max_words])

    pose = pose_action_subset(tags)[:3]
    activity = ", ".join(t.replace("_", " ") for t in pose) or _first_sentence(
        scene_desc, 15
    )

    places = [t for t in tags if get_tag_axis(t) == "location"][:3]
    weather = [t for t in tags if get_tag_axis(t) == "time_weather"][:1]
    place = ", ".join(
        t.replace("_", " ") for t in (*places, *weather)
    ) or _first_sentence(scene_desc, 10)

    feeling = ""
    for t in tags:
        if t in _EXPRESSION_TAGS:
            feeling = _EXPRESSION_TO_FEELING.get(t, t.replace("_", " "))
            break
    if not feeling:
        e = (emotion or "").strip().lower()
        feeling = e if e in _EMOTION_REGISTER else "calm"

    outfit = ", ".join(
        t.replace("_", " ") for t in outfit_tags_from_wd14(tags, limit=4)
    )

    return {
        "activity": activity, "place": place, "feeling": feeling,
        "outfit": outfit,
    }


def build_topic_suggest_prompt(
    *,
    character_desc: str,
    scene_desc: str = "",
    base_act: dict | None = None,
    worldview: str = "",
) -> str:
    """ONE call → a SHORT 起承転結 (4-beat) お題 blurb for the topic field.

    Output is ALWAYS English, for the same measured reason as _ARC_OUTPUT_LINE:
    bonsai-class local models author far better EN than ja. Asking for ja
    directly came back with misspelled JSON keys and half-English sentences
    (measured on gemma-4-12b). The ja UI gets a batched display translation
    instead — build_json_translation_prompt, exactly like the arc stage.
    """
    act = base_act or {}
    moment = ""
    if act.get("activity"):
        moment = (
            f"  moment: {act.get('activity', '')}"
            f" @ {act.get('place', '')} ({act.get('feeling', '')})"
        )
        if act.get("outfit"):
            moment += f", wearing {act['outfit']}"
        moment += "\n"
    scene_line = f"  scene: {scene_desc.strip()[:300]}\n" if scene_desc.strip() else ""
    world_line = (
        f"worldview: {worldview.strip()[:120]}\n" if worldview.strip() else ""
    )
    return (
        "You are pitching a premise for ONE illustration series.\n\n"
        "BASE IMAGE — the story STARTS here; never contradict it:\n"
        f"  character: {character_desc.strip()[:400]}\n"
        f"{scene_line}{moment}{world_line}\n"
        "TASK: write ONE premise with a 起承転結 shape — setup (what the image "
        "already shows), development, a twist, a resolution.\n"
        "RULES:\n"
        "- 1-2 natural English sentences, 20-40 words TOTAL.\n"
        "- The setup IS the base image above.\n"
        "- The twist is a human or situational surprise — no magic, no genre "
        "shift.\n"
        "- Physical and drawable throughout: things a picture can show.\n"
        "- Plain prose for a single text field: no headings, no bullets, no "
        "quotes, no markdown, no beat labels, no romaji labels.\n"
        'OUTPUT: ONLY this JSON object, in English: {"topic": "…"}\n'
        "Nothing else — no code fences, no extra keys."
    )


_TOPIC_BEATS = ("ki", "shou", "ten", "ketsu")

# Beat keys, for the older/looser shapes the model still sometimes emits (it
# misspells the romaji — measured: "kecu" for "ketsu").
_TOPIC_BEAT_ALIASES: dict[str, str] = {
    "ki": "ki", "qi": "ki", "setup": "ki",
    "shou": "shou", "sho": "shou", "show": "shou", "development": "shou",
    "ten": "ten", "tenn": "ten", "twist": "ten",
    "ketsu": "ketsu", "kecu": "ketsu", "ketu": "ketsu", "ketsuron": "ketsu",
    "resolution": "ketsu", "conclusion": "ketsu",
}
_TOPIC_LABEL_RE = re.compile(
    r"^\s*(?:起|承|転|結|ki|shou|sho|ten|ketsu|kecu)\s*[:：.)-]\s*",
    re.IGNORECASE,
)


def _clean_topic_text(text: str) -> str:
    """Strip the wrappers the model adds around a plain-prose field."""
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t).strip()
    t = t.strip("\"'` \n\t")
    t = _TOPIC_LABEL_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_topic_suggest_json(raw: str) -> dict:
    """Parse the topic suggestion → {"topic": str, "beats": {...}}.

    Lenient like every other parser here: junk → empty topic, and the caller
    retries once before a 502 rather than prefilling the field with garbage.

    The shapes below are all MEASURED failures of gemma-4-12b on this call, not
    hypotheticals: a flat [k, v, k, v] array under a "```json,{" key, beat keys
    instead of "topic", and code-fenced values.
    """
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {"topic": "", "beats": {}}

    beats: dict[str, str] = {}
    topic = ""

    def _absorb(key, value) -> None:
        nonlocal topic
        k = str(key).strip().lower().strip("\"'` ")
        if k == "topic" and not topic:
            topic = _clean_topic_text(value)
            return
        canon = _TOPIC_BEAT_ALIASES.get(k)
        if canon and canon not in beats:
            text = _clean_topic_text(value)
            if text:
                beats[canon] = text

    for raw_key, value in data.items():
        if isinstance(value, list):
            # Flat [key, value, key, value, …] emitted under a junk key.
            flat = [str(x) for x in value]
            for i in range(0, len(flat) - 1, 2):
                _absorb(flat[i], flat[i + 1])
            continue
        if isinstance(value, (str, int, float)):
            _absorb(raw_key, value)

    if not topic and beats:
        # Beats but no woven sentence — stitch them, stripping trailing
        # punctuation so the join cannot produce "a.. b".
        parts = [beats[k].rstrip(" .。、,") for k in _TOPIC_BEATS if beats.get(k)]
        topic = ". ".join(p for p in parts if p)
        if topic:
            topic += "."
    return {"topic": topic, "beats": beats}


def build_script_scaffold(
    *,
    base_axis: str,
    time_scale: str,
    base_act_fixed: dict | None = None,
) -> str:
    """The 台本: a deterministic fill-in-the-blanks script.

    Composed entirely from the existing (base_axis × time_scale) tables —
    labels from ``default_act_labels`` and the scene delta from
    ``_scale_delta_line`` — so the LLM never has to reason about the timeline.
    With ``base_act_fixed`` the base act is written in as decided fact
    (measured: bonsai copies FIXED lines verbatim into its JSON).
    """
    labels = default_act_labels(base_axis, time_scale, "en")
    lines = [
        "SCRIPT — TIME AXIS (fill ONLY the ____ slots; FIXED lines are decided "
        "facts, copy them into your JSON exactly. Use the label values as written):",
        f"scene_delta: {_scale_delta_line(time_scale)}",
    ]
    for ax in AXES:
        if ax == base_axis and base_act_fixed:
            lines.append(
                f'[{ax.upper()} | label="{labels[ax]}" | t=0 — THIS IS THE BASE '
                "IMAGE. FIXED]"
            )
            for key in ("activity", "place", "feeling", "outfit"):
                lines.append(f'  {key} = "{base_act_fixed.get(key, "")}"')
        else:
            mark = " | t=0 base act" if ax == base_axis else ""
            lines.append(f'[{ax.upper()} | label="{labels[ax]}"{mark}]')
            lines.append(
                "  activity = ____   place = ____   feeling = ____   "
                "outfit = ____"
            )
    return "\n".join(lines) + "\n"


def enforce_base_act(
    candidate: dict, *, base_axis: str, base_act_fixed: dict | None
) -> None:
    """Overwrite the base act with the image-derived facts (in place).

    The scaffold asks the LLM to copy FIXED values, and measured behaviour says
    it does — but this makes drift structurally impossible regardless.
    """
    if not base_act_fixed:
        return
    acts = candidate.get("acts")
    if not isinstance(acts, dict):
        return
    act = acts.get(base_axis)
    if not isinstance(act, dict):
        act = {k: "" for k in _ACT_KEYS}
        acts[base_axis] = act
    for key in ("activity", "place", "feeling", "outfit"):
        if base_act_fixed.get(key):
            act[key] = base_act_fixed[key]
    # Rebuild the flat legacy beat in the parse_story_arc_json format. The
    # outfit stays OUT of it on purpose: infer_axis_scene_constraints and the
    # topic-anchor gate read this string, and garment words there would
    # register as false scene constraints.
    beat = act.get("activity") or ""
    if act.get("place"):
        beat = f"{beat} ({act['place']})" if beat else act["place"]
    candidate[base_axis] = beat


def build_story_arc_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    user_topic: str = "",
    worldview: str = "",
    base_axis: str = "present",
    time_scale: str = "years",
    emotion: str = "",
    locale: str = "en",
    candidate_modes: dict[str, str] | None = None,
    tone: str = "bright",
    divergence: float = 0.0,
    seed_tags: list[str] | None = None,
    forced_motif: str = "",
    feedback: str = "",
    base_act_fixed: dict | None = None,
) -> str:
    """ONE LLM call → three candidates × full three-act arcs (JSON).

    Format tuned against bonsai-27b (measured): no key=value checklist at the
    top (the model form-fills it), the deterministic script scaffold replaces
    the prose timeline constraints, FIXED base-act lines are copied verbatim,
    concrete-value few-shot only, output ALWAYS English (ja display is a
    separate batched translation).
    """
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    has_topic = bool(user_topic.strip())
    modes = candidate_modes or {}
    head = chronicle_hard_rules_preamble(locale=locale, has_user_topic=has_topic)
    seed_block = chronicle_seed_tags_block(
        seed_tags, forced_motif=forced_motif, locale=locale
    )
    if has_topic:
        topic_note = (
            f'topic="{user_topic.strip()}"\n'
            "TOPIC RULE: every act must PHYSICALLY contain the topic — its place, "
            "object or action appears in the act itself, not as backstory. If the "
            "topic is an in-progress action, all three acts stay inside it.\n"
        )
    else:
        topic_note = "topic=(none — invent three distinct premises)\n"
    world_line = (
        f'worldview="{worldview.strip()[:120]}"'
        if worldview.strip()
        else "worldview=(none)"
    )
    scaffold = build_script_scaffold(
        base_axis=base_axis, time_scale=time_scale, base_act_fixed=base_act_fixed,
    )
    mood_line = ""
    mood = (emotion or "").strip().lower()
    if mood in _EMOTION_REGISTER:
        mood_line = f"Overall mood: {mood}.\n"
    motif_json_hint = forced_motif or ""
    motif_line = f"motif hint: {motif_json_hint}\n" if motif_json_hint else ""
    feedback_block = f"\n{feedback.strip()}\n" if feedback.strip() else ""
    scene_block = (
        f"BASE IMAGE scene (context):\n{scene_desc.strip()[:400]}\n\n"
        if scene_desc.strip()
        else ""
    )
    return (
        f"{head}"
        f"{seed_block}"
        f"{topic_note}"
        f"{world_line}\n"
        f"{_candidate_modes_block(modes)}"
        f"{_tone_line(tone, locale)}"
        f"{_divergence_line(divergence, locale)}"
        f"{mood_line}"
        "TASK: Pitch THREE chronicles (A/B/C) for ONE character. Each is ONE "
        f"story told as three acts ~{span} apart following the SCRIPT below. "
        "Each act is ONE drawable moment: a physical activity (verb + object, "
        "≤15 words), a concrete place, one feeling word, and what she is "
        "WEARING (outfit — a short garment phrase, ≤8 words).\n\n"
        f"{scaffold}\n"
        f"{scene_block}"
        f"CHARACTER (appearance tags only):\n{character_desc}\n\n"
        f"must_keep: {rules['must_keep']}\n"
        f"may_differ: {rules['may_differ']}\n"
        f"outfit: {rules['outfit_rule']}\n"
        "- The three acts must be one connected thread: one motif object recurs "
        "and one turn builds — never three unrelated snapshots.\n"
        "GROUNDING: same real-world register — no magic/aliens unless worldview "
        "explicitly asks. Surprise = human/situational, not genre shift.\n"
        "personality_hint: 1 sentence — temperament + one habit or item she uses.\n"
        f"{feedback_block}\n"
        f"{_ARC_OUTPUT_LINE}\n\n"
        'OUTPUT: return ONLY a JSON object with a top-level "candidates" array '
        "of EXACTLY 3 items (id A, B, C). Every candidate's acts must follow "
        "the SCRIPT: copy FIXED values, fill the ____ slots.\n"
        f"{motif_line}"
        f"{_ARC_FEWSHOT}"
    )


# ── Story-first Phase 1 (novel → extract acts) ────────────────────────────────

LIFE_ROLES: tuple[str, ...] = (
    "student_cafe_job",
    "freeter_multi_job",
    "career_barista",
    "custom",
)

LIFE_ROLE_HINTS: dict[str, str] = {
    "student_cafe_job": (
        "She is a student with a cafe part-time job NOW. Her whole life is NOT "
        "only 'cafe worker'. Non-base acts should show student/portfolio life."
    ),
    "freeter_multi_job": (
        "She juggles multiple part-time jobs; the cafe shift is only one slice of life."
    ),
    "career_barista": (
        "Cafe work is her chosen craft, but stories still need off-counter beats "
        "when the time scale is days or longer."
    ),
    "custom": "Infer a coherent life role from the topic and FIXED present.",
}


def resolve_life_role(life_role: str, *, user_topic: str = "") -> str:
    role = (life_role or "").strip() or "custom"
    if role == "random" or role not in LIFE_ROLES:
        # Deterministic-ish pick from closed set excluding custom when no topic.
        import hashlib
        seed = f"{role}|{user_topic}".encode()
        idx = int(hashlib.md5(seed).hexdigest(), 16) % (len(LIFE_ROLES) - 1)
        role = LIFE_ROLES[idx]
    return role


def build_story_first_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    user_topic: str = "",
    worldview: str = "",
    base_axis: str = "present",
    time_scale: str = "days",
    life_role: str = "student_cafe_job",
    emotion: str = "",
    locale: str = "en",
    candidate_modes: dict[str, str] | None = None,
    tone: str = "bright",
    divergence: float = 0.0,
    feedback: str = "",
    base_act_fixed: dict | None = None,
) -> str:
    """Story-first: write novels first, then extract drawable acts (JSON)."""
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    role = resolve_life_role(life_role, user_topic=user_topic)
    role_hint = LIFE_ROLE_HINTS.get(role, LIFE_ROLE_HINTS["custom"])
    modes = candidate_modes or {}
    labels = default_act_labels(base_axis, time_scale, "en")
    fixed = base_act_fixed or {}
    has_topic = bool(user_topic.strip())
    topic_note = (
        f'topic="{user_topic.strip()}"\n'
        "TOPIC RULE: the novel and every non-base act must physically contain "
        "the topic (place/object/action), not as distant backstory.\n"
        if has_topic
        else "topic=(none — invent coherent premises per candidate)\n"
    )
    world_line = (
        f'worldview="{worldview.strip()[:120]}"'
        if worldview.strip()
        else "worldview=(none)"
    )
    fixed_block = ""
    if fixed:
        fixed_block = (
            f"FIXED {base_axis.upper()} (copy exactly into acts.{base_axis}):\n"
            + "\n".join(f'  {k} = "{fixed.get(k, "")}"' for k in ("activity", "place", "feeling", "outfit"))
            + "\n"
        )
    feedback_block = f"\n{feedback.strip()}\n" if feedback.strip() else ""
    mood_line = ""
    mood = (emotion or "").strip().lower()
    if mood in _EMOTION_REGISTER:
        mood_line = f"Overall mood: {mood}.\n"
    return (
        "You write SHORT coherent stories for an anime illustration trilogy, "
        "THEN extract three drawable camera beats.\n\n"
        f"life_role: {role}\n  ({role_hint})\n"
        f"time_scale: {time_scale} (~{span} between acts)\n"
        f"{topic_note}"
        f"{world_line}\n"
        f"{_candidate_modes_block(modes)}"
        f"{_tone_line(tone, locale)}"
        f"{_divergence_line(divergence, locale)}"
        f"{mood_line}"
        f"{fixed_block}"
        f"BASE IMAGE scene:\n{(scene_desc or '')[:400]}\n\n"
        f"CHARACTER (appearance only):\n{character_desc}\n\n"
        f"must_keep: {rules['must_keep']}\n"
        f"may_differ: {rules['may_differ']}\n"
        f"outfit: {rules['outfit_rule']}\n"
        "HARD RULES:\n"
        f"- Write ONE short English novel (3–5 paragraphs) per candidate A/B/C first.\n"
        "- Then extract acts that are VISUAL frames from that novel.\n"
        f"- acts.{base_axis} must match FIXED exactly when FIXED is given.\n"
        "- Non-base acts: off the FIXED counter/place when scale≥days; different outfits.\n"
        "- No age jump / graduation timeskip unless scale is years or decades.\n"
        f"- Use these labels exactly: past=\"{labels['past']}\", "
        f"present=\"{labels['present']}\", future=\"{labels['future']}\".\n"
        "- Keep story prose on the same time_scale (no 'weeks later' when scale is days).\n"
        f"{feedback_block}\n"
        "OUTPUT: return ONLY JSON:\n"
        '{"candidates":[{"id":"A","title":"...","dramatic_mode":"...",'
        '"throughline":"one sentence","story_en":"full short story paragraphs",'
        '"personality_hint":"...",'
        '"acts":{"past":{"label":"...","activity":"...","place":"...","feeling":"...","outfit":"..."},'
        '"present":{...},"future":{...}}},'
        '{"id":"B",...},{"id":"C",...}]}\n'
    )


def parse_story_first_json(raw: str) -> list[dict]:
    """Parse story-first JSON; reuse arc normalizer for acts + flat fields."""
    # Prefer standard arc parser; also lift story_en / throughline.
    candidates = parse_story_arc_json(raw)
    data = _loads_lenient(raw)
    items = data.get("candidates") if isinstance(data, dict) else data
    raw_by_id: dict[str, dict] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                raw_by_id[str(item["id"]).strip()] = item
    for cand in candidates:
        item = raw_by_id.get(cand["id"], {})
        story = str(item.get("story_en") or item.get("story") or "").strip()
        if story:
            cand["story_en"] = story
        throughline = str(item.get("throughline") or "").strip()
        if throughline:
            cand["throughline"] = throughline
        if not cand.get("summary") and throughline:
            cand["summary"] = throughline
    return candidates


def _normalize_act(raw, *, fallback_label: str = "") -> dict:
    """Coerce one act into _ACT_KEYS strings.

    Stories saved before `outfit` existed simply get outfit="" here, which
    degrades to the pre-outfit prompts — no migration needed.
    """
    if isinstance(raw, str):
        return {
            "label": fallback_label, "activity": raw.strip(),
            "place": "", "feeling": "", "outfit": "", "motif_use": "",
        }
    if not isinstance(raw, dict):
        return {k: "" for k in _ACT_KEYS}
    out = {k: str(raw.get(k) or "").strip() for k in _ACT_KEYS}
    if not out["label"]:
        out["label"] = fallback_label
    return out


def synthesize_acts_from_flat(candidate: dict) -> dict[str, dict]:
    """Legacy bridge: build acts from flat past/present/future beat strings."""
    return {
        axis: _normalize_act(str(candidate.get(axis) or "").strip())
        for axis in AXES
    }


def parse_story_arc_json(raw: str) -> list[dict]:
    """Parse the arc output into candidate dicts (superset of the old shape).

    Every candidate carries BOTH the structured ``acts`` and the legacy flat
    ``past/present/future`` + ``summary`` fields, so existing candidate cards,
    stored drafts and Storybook records keep working unchanged.
    """
    candidates = parse_candidates_json(raw)
    data = _loads_lenient(raw)
    items = data.get("candidates") if isinstance(data, dict) else data
    raw_by_id: dict[str, dict] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                cid = str(item.get("id") or "").strip()
                if cid:
                    raw_by_id[cid] = item

    for cand in candidates:
        item = raw_by_id.get(cand["id"], {})
        acts_raw = item.get("acts") if isinstance(item.get("acts"), dict) else {}
        acts: dict[str, dict] = {}
        for axis in AXES:
            act = _normalize_act(acts_raw.get(axis))
            if not act["activity"]:
                act["activity"] = cand.get(axis) or ""
            acts[axis] = act
        cand["acts"] = acts
        cand["personality_hint"] = str(item.get("personality_hint") or "").strip()
        # Flat beats for legacy consumers (cards, stored drafts, expand seed).
        for axis in AXES:
            if not cand.get(axis):
                a = acts[axis]
                beat = a["activity"]
                if a["place"]:
                    beat = f"{beat} ({a['place']})" if beat else a["place"]
                cand[axis] = beat
        if not cand.get("summary"):
            cand["summary"] = cand.get("present") or " / ".join(
                b for b in (cand.get("past"), cand.get("future")) if b
            )
    return candidates


def candidate_acts(candidate: dict) -> dict[str, dict]:
    """Structured acts of a candidate; legacy flat records are synthesized."""
    acts = candidate.get("acts")
    if isinstance(acts, dict) and any(
        (acts.get(a) or {}).get("activity") for a in AXES
    ):
        return {axis: _normalize_act(acts.get(axis)) for axis in AXES}
    return synthesize_acts_from_flat(candidate)


# ── Arc validation (code-side お題 / time-axis gates) ─────────────────────────

_LABEL_UNIT_PATTERNS: tuple[tuple[re.Pattern, float], ...] = (
    (re.compile(r"\bminutes?\b|分"), 0.0),
    (re.compile(r"\bhours?\b|時間"), 1.0),
    (re.compile(r"\bdays?\b|(?:[0-9０-９数何十]+)日|翌日|翌朝"), 2.0),
    (re.compile(r"\bweeks?\b|週"), 2.5),
    (re.compile(r"\bmonths?\b|[ヶヵカか箇]月"), 3.0),
    (re.compile(r"\byears?\b|年"), 4.0),
    (re.compile(r"\bdecades?\b|数十年"), 5.0),
)
_SCALE_ALLOWED_MAGNITUDES: dict[str, tuple[float, float]] = {
    "minutes":         (0.0, 0.0),
    "tens_of_minutes": (0.0, 1.0),
    "hours":           (0.0, 1.0),
    "days":            (1.0, 2.5),
    "months":          (2.5, 3.0),
    "years":           (3.0, 4.0),
    "decades":         (4.0, 5.0),
}


def _label_magnitudes(label: str) -> list[float]:
    text = (label or "").lower().replace("half a day", "hour").replace("半日", "時間")
    return [mag for pat, mag in _LABEL_UNIT_PATTERNS if pat.search(text)]


def default_act_labels(
    base_axis: str, time_scale: str, locale: str = "en"
) -> dict[str, str]:
    """Canonical per-axis labels derived from the elapsed-unit tables."""
    one, two = _ELAPSED_UNIT.get(time_scale, _ELAPSED_UNIT["years"])
    one_ja, two_ja = _ELAPSED_UNIT_JA.get(time_scale, _ELAPSED_UNIT_JA["years"])
    base = base_axis if base_axis in AXES else "present"
    idx_base = AXES.index(base)
    labels: dict[str, str] = {}
    for axis in AXES:
        if axis == base:
            labels[axis] = "いま" if locale == "ja" else "now"
            continue
        i = AXES.index(axis)
        steps = abs(i - idx_base)
        forward = i > idx_base
        if locale == "ja":
            phrase = one_ja if steps == 1 else two_ja
            labels[axis] = f"{phrase}{'後' if forward else '前'}"
        else:
            phrase = one if steps == 1 else two
            # The elapsed-unit table shouts (A FEW HOURS) — labels shouldn't.
            labels[axis] = f"{phrase.lower()} {'later' if forward else 'earlier'}"
    return labels


def repair_act_labels(
    candidate: dict, *, base_axis: str, time_scale: str, locale: str = "en"
) -> None:
    """Always overwrite act labels with canonical defaults for the time scale."""
    defaults = default_act_labels(base_axis, time_scale, locale)
    acts = candidate.get("acts")
    if not isinstance(acts, dict):
        return
    for axis in AXES:
        act = acts.get(axis)
        if not isinstance(act, dict):
            continue
        act["label"] = defaults[axis]


def validate_story_arc(
    candidates: list[dict],
    *,
    user_topic: str = "",
    topic_directive: str = "",
    time_scale: str = "years",
    base_axis: str = "present",
) -> list[dict]:
    """Machine-readable arc problems: [{candidate_id, code, detail}].

    Codes: ``structure`` (missing/overlong acts), ``off_topic`` (no topic
    anchor hit), ``time_collapse`` (three acts restate one moment),
    ``bad_scale_labels`` (label unit off the time scale — repairable, never
    retried alone).
    """
    problems: list[dict] = []
    # Arc output is always English (see _ARC_OUTPUT_LINE): anchor groups whose
    # every member is CJK can never hit an EN blob — drop them from the gate
    # instead of failing every candidate on a ja お題 without EN aliases.
    groups = [
        g for g in topic_anchor_groups(user_topic, topic_directive)
        if any(not _is_ja_script_token(tok) for tok in g)
    ]
    need_hits = min(2, len(groups)) if groups else 0
    lo, hi = _SCALE_ALLOWED_MAGNITUDES.get(time_scale, (3.0, 4.0))

    if not candidates:
        return [{"candidate_id": "*", "code": "structure",
                 "detail": "no candidates parsed"}]
    if len(candidates) < 3:
        problems.append({
            "candidate_id": "*", "code": "structure",
            "detail": f"expected 3 candidates, got {len(candidates)}",
        })

    for cand in candidates:
        cid = cand.get("id") or "?"
        acts = candidate_acts(cand)

        # Few-shot regurgitation (measured on bonsai retries): the example
        # story copied wholesale must never survive as a candidate.
        blob_fewshot = f"{cand.get('title', '')} {cand.get('motif', '')}".lower()
        if "portafilter" in blob_fewshot or "order memo" in blob_fewshot:
            problems.append({
                "candidate_id": cid, "code": "structure",
                "detail": "copied the example story — invent your own",
            })

        missing = [a for a in AXES if not acts[a]["activity"]]
        if missing:
            problems.append({
                "candidate_id": cid, "code": "structure",
                "detail": f"acts missing an activity: {', '.join(missing)}",
            })
        overlong = [
            a for a in AXES
            if len(acts[a]["activity"].split()) > 25
        ]
        if overlong:
            problems.append({
                "candidate_id": cid, "code": "structure",
                "detail": f"activity too long (≤15 words): {', '.join(overlong)}",
            })

        if groups:
            blob = " ".join(
                [str(cand.get(k) or "") for k in ("title", "motif", "turn")]
                + [f"{acts[a]['activity']} {acts[a]['place']}" for a in AXES]
                + [str(cand.get(a) or "") for a in AXES]
            ).lower()
            hits = sum(
                1 for group in groups if any(tok in blob for tok in group)
            )
            if hits < need_hits:
                # Name the ENGLISH aliases — the model writes EN acts, so
                # ja seeds alone give it nothing actionable (measured: the
                # retry ignored ja anchor names).
                missing_groups = []
                for group in groups:
                    if any(tok in blob for tok in group):
                        continue
                    en = [t for t in group if not _is_ja_script_token(t)][:3]
                    missing_groups.append("/".join(en) if en else group[0])
                problems.append({
                    "candidate_id": cid, "code": "off_topic",
                    "detail": (
                        "these topic elements appear in NO act — put them in "
                        f"physically: {', '.join(missing_groups[:4])}"
                    ),
                })

        if should_differentiate_acts(time_scale):
            beats = [acts[a]["activity"] for a in AXES]
            if all(beats) and _mean_pairwise_similarity(beats) >= _BEAT_SIMILAR_THRESHOLD:
                problems.append({
                    "candidate_id": cid, "code": "time_collapse",
                    "detail": "three acts restate the same moment",
                })

        bad_labels = [
            a for a in AXES
            if a != base_axis
            and any(m < lo or m > hi for m in _label_magnitudes(acts[a]["label"]))
        ]
        if bad_labels:
            problems.append({
                "candidate_id": cid, "code": "bad_scale_labels",
                "detail": (
                    f"act labels off the {time_scale} scale: "
                    f"{', '.join(f'{a}={acts[a]['label']!r}' for a in bad_labels)}"
                ),
            })
    return problems


# Problems that justify the single LLM retry (labels are repaired code-side).
_RETRY_CODES = frozenset({"structure", "off_topic", "time_collapse"})


def arc_needs_retry(problems: list[dict]) -> bool:
    return any(p.get("code") in _RETRY_CODES for p in problems)


def arc_feedback_block(problems: list[dict], *, locale: str = "en") -> str:
    """Literal per-candidate feedback for the single arc retry."""
    retry = [p for p in problems if p.get("code") in _RETRY_CODES]
    if not retry:
        return ""
    lines = (
        ["前回の出力には以下の問題があった。同じ構成で書き直し、必ず修正すること:"]
        if locale == "ja"
        else ["Your previous output had these problems. Rewrite and FIX every one:"]
    )
    for p in retry:
        lines.append(f"- Candidate {p['candidate_id']}: [{p['code']}] {p['detail']}")
    return "\n".join(lines) + "\n"


_PROBLEM_WEIGHTS = {
    "structure": 10, "off_topic": 4, "time_collapse": 4, "bad_scale_labels": 1,
}


def arc_problem_score(problems: list[dict]) -> int:
    """Total badness of a validation result — used to keep the BEST attempt
    (measured: a feedback retry can come back WORSE than the first try)."""
    return sum(_PROBLEM_WEIGHTS.get(p.get("code"), 2) for p in problems)


def select_best_candidates(
    candidates: list[dict],
    problems: list[dict],
    *,
    n: int = 3,
) -> list[dict]:
    """Salvage pass after the retry: prefer clean candidates, rank the rest by
    problem count (labels weigh least — they are repairable)."""
    score: dict[str, int] = {}
    for p in problems:
        cid = p.get("candidate_id") or "?"
        score[cid] = score.get(cid, 0) + _PROBLEM_WEIGHTS.get(p.get("code"), 2)
    ranked = sorted(
        candidates,
        key=lambda c: (score.get(c.get("id") or "?", 0),
                       candidates.index(c)),
    )
    return ranked[:n]


# ── Acts polish (the ONLY creative LLM call of Phase 2) ───────────────────────

def build_acts_polish_prompt(
    *,
    title: str,
    acts: dict[str, dict],
    tag_lines: dict[str, str],
    identity_tags: list[str] | None = None,
    prose_paragraphs: int | None = None,
) -> str:
    """One call → English Visual Script prose for ALL acts (JSON).

    The acts and tag lines are AUTHORITATIVE: the model may only rephrase the
    given activity/place/feeling into drawable prose per act and weave in a few
    of that act's tags in ASCII parentheses. It must not invent new events —
    this is what keeps expansion faithful to the chosen story.

    ``prose_paragraphs`` (the 自然文 knob, 3–7) sizes each act via
    chronicle_prose_budget. None → DEFAULT_PROSE_PARAGRAPHS.
    """
    lo, hi, sents = chronicle_prose_budget(prose_paragraphs)
    ident = ", ".join(identity_tags or [])
    act_blocks = []
    for axis in AXES:
        a = acts.get(axis) or {}
        act_blocks.append(
            f"[{axis.upper()}] label={a.get('label', '')}\n"
            f"  activity: {a.get('activity', '')}\n"
            f"  place: {a.get('place', '')}\n"
            f"  feeling: {a.get('feeling', '')}\n"
            f"  outfit: {a.get('outfit', '')}\n"
            f"  tags: {tag_lines.get(axis, '')}"
        )
    ident_line = f"Character identity tags (do not contradict): {ident}\n" if ident else ""
    return (
        "Write a Visual Script for THREE anime image prompts (English).\n"
        "HARD RULES:\n"
        f"- For each act: write {lo}-{hi} words ({sents} sentences), present "
        f"tense. Aim for {lo} words MINIMUM — elaborate the given detail with "
        "visible, drawable specifics (light, texture, posture, what the "
        "clothing does).\n"
        "- Describe ONLY the given activity / place / feeling / outfit. Do NOT add new "
        "events, characters, objects or camera directions. Rephrase, never invent.\n"
        "- Embed a few key tags from that act's tag list in ASCII parentheses "
        "next to the matching detail, e.g. 'she pours tea (pouring) at the "
        "counter (indoors)'.\n"
        "- No tag-line output, no labeled *_TAGS footers, no markdown.\n"
        f"{ident_line}"
        f"TITLE: {title}\n\n"
        + "\n\n".join(act_blocks)
        + "\n\nOUTPUT JSON only:\n"
        '{"past": "...", "present": "...", "future": "..."}'
    )


def parse_acts_polish_json(raw: str) -> dict[str, str]:
    """Parse the polish output → axis → prose ('' when missing/broken)."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {a: "" for a in AXES}
    return {a: str(data.get(a) or "").strip() for a in AXES}


# ── Deterministic shot plan (replaces the per-axis visual-exam LLM call) ─────

_SHOT_ROTATION: dict[str, tuple[str, str]] = {
    # axis → (shot, camera angle) — a deliberate wide→medium→close arc so the
    # three images read as a sequence even before pose/scene tags differ.
    "past":    ("wide_shot", "from_side"),
    "present": ("cowboy_shot", ""),
    "future":  ("upper_body", "from_below"),
}
_FEELING_ANGLE_OVERRIDES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("lonely", "sad", "melancholy", "寂", "悲"), "from_behind"),
    (("determined", "bold", "brave", "決意", "挑"), "from_below"),
    (("nostalgic", "wistful", "懐"), "from_side"),
)


def deterministic_shot_plan(axis: str, *, feeling: str = "") -> dict[str, str]:
    """Shot/angle for an axis — code-side, no LLM. Feeling nudges the angle."""
    shot, angle = _SHOT_ROTATION.get(axis, ("cowboy_shot", ""))
    f = (feeling or "").lower()
    for keys, override in _FEELING_ANGLE_OVERRIDES:
        if any(k in f for k in keys):
            angle = override
            break
    plan = {"shot": shot}
    if angle:
        plan["camera_angle"] = angle
    return plan

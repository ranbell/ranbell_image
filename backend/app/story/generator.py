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

import json
import logging
import re

logger = logging.getLogger(__name__)

AXES = ("past", "present", "future")
SECTIONS = ("title", "overall") + AXES

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

_CHRONICLE_FEWSHOT_CANDIDATES = (
    "Example of GOOD concrete output (structure only — invent your own story):\n"
    '{"candidates":[{"id":"A","title":"Steam on the Portafilter",'
    '"dramatic_mode":"escalation",'
    '"past":"She tamps coffee into the portafilter with both palms before open.",'
    '"present":"She slides a ceramic cup across the wooden counter to a regular.",'
    '"future":"At close she folds the order memo and tucks it into her apron pocket.",'
    '"motif":"order memo","turn":"The memo name belongs to someone she thought had left town.",'
    '"grounded_tags":["coffee_cup","apron","paper"]}]}'
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


def candidates_ungrounded(
    candidates: list[dict],
    seed_tags: list[str],
    *,
    min_hits: int = 2,
) -> bool:
    """True when most candidates fail to report enough grounded_tags ∩ seed."""
    seed = {t.lower().replace(" ", "_") for t in seed_tags if t}
    if len(seed) < 3:
        return False
    if not candidates:
        return True
    bad = 0
    for c in candidates:
        used = {
            str(t).lower().replace(" ", "_")
            for t in (c.get("grounded_tags") or [])
            if t
        }
        if len(used & seed) < min_hits:
            bad += 1
    return bad >= max(2, (len(candidates) + 1) // 2)


_TOPIC_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_TOPIC_KANJI_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_TOPIC_KATA_RE = re.compile(r"[\u30a0-\u30ff]{2,}")


def topic_anchor_tokens(user_topic: str, topic_directive: str = "") -> list[str]:
    """Salient tokens from お題 (+ directive) for off-topic detection.

    Japanese uses kanji/katakana chunks (not whole phrases glued by hiragana)
    so a topic like 「廃墟を探索する冒険」 yields 「廃墟」「探索」「冒険」.
    Each JA token is also expanded with common EN aliases so English candidate
    beats still match a Japanese お題 (カフェ↔cafe).
    """
    text = f"{user_topic or ''} {topic_directive or ''}".strip()
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def _add(tok: str) -> None:
        t = tok.lower().replace("_", " ").strip()
        if not t or len(t) < 2 or t in seen:
            return
        if t in {
            "the", "and", "for", "with", "from", "that", "this", "her", "his",
            "she", "girl", "story", "about", "into", "over", "under",
        }:
            return
        seen.add(t)
        out.append(t)

    for m in _TOPIC_EN_RE.finditer(text):
        _add(m.group(0))
    for m in _TOPIC_KANJI_RE.finditer(text):
        _add(m.group(0))
    for m in _TOPIC_KATA_RE.finditer(text):
        _add(m.group(0))

    # Expand JA anchors with EN aliases (and vice-versa when EN topic given).
    for tok in list(out):
        for alias in _TOPIC_JA_EN_ALIASES.get(tok, ()):
            _add(alias)
    for en, aliases in _TOPIC_EN_JA_ALIASES.items():
        if en in seen:
            for alias in aliases:
                _add(alias)

    return out[:24]


# Compact bilingual bridges for お題 gating (substring match is otherwise JA≠EN).
_TOPIC_JA_EN_ALIASES: dict[str, tuple[str, ...]] = {
    "カフェ": ("cafe", "coffee", "barista"),
    "珈琲": ("coffee", "cafe"),
    "キッチン": ("kitchen",),
    "台所": ("kitchen",),
    "駅": ("station", "platform", "train"),
    "学校": ("school", "classroom"),
    "教室": ("classroom", "school"),
    "公園": ("park",),
    "海": ("sea", "ocean", "beach"),
    "海辺": ("beach", "seaside"),
    "祭り": ("festival", "matsuri"),
    "夏祭": ("festival", "matsuri", "summer festival"),
    "花火": ("fireworks",),
    "三人": ("3girls", "three girls", "trio"),
    "二人": ("2girls", "2boys", "couple"),
    "雨": ("rain", "rainy"),
    "夜": ("night", "midnight"),
    "朝": ("morning", "dawn"),
    "図書館": ("library",),
    "料理": ("cooking", "kitchen", "recipe"),
    "冒険": ("adventure", "quest"),
    "廃墟": ("ruin", "ruins", "abandoned"),
    "探索": ("explore", "exploring", "search"),
    "働く": ("work", "working", "job"),
    "自転車": ("bicycle", "bike", "cycling"),
    "試合": ("match", "game", "stadium", "competition"),
    "祝い": ("celebration", "toast", "party"),
    "放課後": ("after school", "afterschool"),
}
_TOPIC_EN_JA_ALIASES: dict[str, tuple[str, ...]] = {
    "cafe": ("カフェ",),
    "coffee": ("カフェ", "珈琲"),
    "kitchen": ("キッチン", "台所"),
    "station": ("駅",),
    "school": ("学校",),
    "park": ("公園",),
    "beach": ("海辺", "海"),
    "festival": ("祭り", "夏祭"),
    "matsuri": ("祭り",),
    "fireworks": ("花火",),
    "rain": ("雨",),
    "night": ("夜",),
    "library": ("図書館",),
}


def candidates_off_topic(
    candidates: list[dict],
    user_topic: str,
    topic_directive: str = "",
) -> bool:
    """True when most candidates' beats ignore the user topic tokens.

    Cheap substring check (works for JA and EN). Empty topic → False (no gate).
    """
    tokens = topic_anchor_tokens(user_topic, topic_directive)
    if len(tokens) < 1 or not candidates:
        return False
    bad = 0
    for c in candidates:
        blob = " ".join(
            str(c.get(a) or "") for a in (*AXES, "title", "turn", "motif")
        ).lower()
        if not any(tok in blob for tok in tokens):
            bad += 1
    return bad >= max(2, (len(candidates) + 1) // 2)


def bind_timetable_axis_slots(
    slots: list[dict],
    *,
    base_axis: str = "present",
) -> dict[str, dict]:
    """Pick one slot per past/present/future from a timetable list."""
    axis_slots: dict[str, dict] = {}
    # Prefer explicit axis field from the model.
    for s in slots:
        ax = str(s.get("axis") or "").strip().lower()
        if ax in AXES and ax not in axis_slots:
            axis_slots[ax] = {
                "label": str(s.get("label") or "").strip(),
                "activity": str(s.get("activity") or "").strip(),
                "place": str(s.get("place") or "").strip(),
                "feeling": str(s.get("feeling") or "").strip(),
            }
    if len(axis_slots) == 3:
        return axis_slots

    # Heuristic: map by label keywords, else by position.
    def _guess(slot: dict) -> str | None:
        lab = str(slot.get("label") or "").lower()
        if any(k in lab for k in ("now", "today", "present", "現在", "今")):
            return "present"
        if any(k in lab for k in ("past", "earlier", "-", "前", "昨日")):
            return "past"
        if any(k in lab for k in ("future", "later", "+", "後", "明日")):
            return "future"
        return None

    for s in slots:
        ax = _guess(s)
        if ax and ax not in axis_slots:
            axis_slots[ax] = {
                "label": str(s.get("label") or "").strip(),
                "activity": str(s.get("activity") or "").strip(),
                "place": str(s.get("place") or "").strip(),
                "feeling": str(s.get("feeling") or "").strip(),
            }
    if base_axis in AXES and base_axis not in axis_slots and slots:
        mid = slots[len(slots) // 2]
        axis_slots[base_axis] = {
            "label": str(mid.get("label") or "now").strip(),
            "activity": str(mid.get("activity") or "").strip(),
            "place": str(mid.get("place") or "").strip(),
            "feeling": str(mid.get("feeling") or "").strip(),
        }
    # Fill remaining axes from chronological thirds.
    if len(slots) >= 3:
        picks = {"past": slots[0], "present": slots[len(slots) // 2], "future": slots[-1]}
        for ax, s in picks.items():
            if ax not in axis_slots:
                axis_slots[ax] = {
                    "label": str(s.get("label") or "").strip(),
                    "activity": str(s.get("activity") or "").strip(),
                    "place": str(s.get("place") or "").strip(),
                    "feeling": str(s.get("feeling") or "").strip(),
                }
    return {a: axis_slots[a] for a in AXES if a in axis_slots}


def format_axis_slots_block(axis_slots: dict[str, dict] | None, *, locale: str = "en") -> str:
    """Format the three bound timetable slots for expand/concrete prompts."""
    if not axis_slots:
        return ""
    lines = []
    for a in AXES:
        s = axis_slots.get(a) or {}
        if not s:
            continue
        lines.append(
            f"  [{a.upper()}] {s.get('label', '')}: {s.get('activity', '')} "
            f"@ {s.get('place', '')} ({s.get('feeling', '')})"
        )
    if not lines:
        return ""
    if locale == "ja":
        return "各幕の時間アンカー（画面の事実）:\n" + "\n".join(lines) + "\n"
    return "TIME ANCHORS per act (on-screen facts):\n" + "\n".join(lines) + "\n"


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


def chunk_list(items: list, size: int) -> list[list]:
    """Split a list into chunks of `size` (last chunk may be shorter)."""
    if size <= 0:
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


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


def _coherence_hierarchy_block(
    *, base_axis: str, user_topic: str, time_scale: str, protect_twist: bool = False
) -> str:
    """Precedence rule shared across every LLM stage.

    The anchors — base image, user topic, time axis — often pull the story in
    different directions; this block tells the LLM which one wins when they
    conflict so the pipeline stays coherent end to end.

    protect_twist (expand/story stages, where ONE candidate has been chosen)
    inserts the candidate's dramatic shape and central turn ABOVE its
    freely-adjustable beats, so the surprise the pitch hinged on is not sanded
    toward the obvious reading during expansion.
    """
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    topic_line = (
        f'2. USER TOPIC ("{user_topic.strip()}") — what the STORY IS ABOUT across '
        "all three acts. Drives the subject; the base image never overrides it. "
        "Overrides the chosen candidate. Never invert or resolve it for surprise."
        if user_topic.strip()
        else "2. USER TOPIC — (none given; invent freely, but honour 1 and 3)."
    )
    lines = [
        "COHERENCE HIERARCHY — resolve conflicts in this order:",
        f"1. BASE IMAGE — fixes only how the [{base_axis.upper()}] act LOOKS (its "
        "scene, pose, character identity). It does NOT decide what the story is about.",
        topic_line,
        f'3. TIME AXIS (scale "{time_scale}": {span} between acts) — how much may '
        "change between acts. Non-negotiable.",
    ]
    if protect_twist:
        lines.append(
            "4. THE CHOSEN TURN & DRAMATIC SHAPE — the single surprise this story "
            "hinges on. PRESERVE it; keep it central. Do not soften it back toward "
            "the obvious reading for the sake of fitting 1-3."
        )
        lines.append(
            "5. CANDIDATE beats — scaffolding; reword freely to satisfy 1-4."
        )
        lines.append(
            "6. Divergence / mutation tags / emotion — flavour; may be dropped to satisfy 1-4."
        )
    else:
        lines.append(
            "4. CANDIDATE beats — scaffolding; adjust freely to satisfy 1-3."
        )
        lines.append(
            "5. Divergence / mutation tags / emotion — flavour; may be dropped to satisfy 1-3."
        )
    lines.append(
        "(Base image = how it LOOKS; user topic = what it is ABOUT — these two do "
        "not conflict.)"
    )
    return "\n".join(lines) + "\n"


def build_topic_directive_prompt(
    user_topic: str, *, time_scale: str = "years", locale: str = "en"
) -> str:
    """Expand an abstract user topic (お題) into a short NARRATIVE directive.

    The candidate stage kept ignoring the topic because a small local model
    cannot bridge an abstract theme to concrete scenes against a vivid literal
    image description. This turns the topic into 2-3 sentences of story direction
    — the subject, the conflict/journey it implies, and a few situational anchors
    — deliberately NOT visual danbooru tags (those would shrink a broad theme
    into a tag-salad and pin the visuals across a long timeline). It is framed as
    a THEME the three acts explore freely, so it never over-constrains long spans
    or broad topics.
    """
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    if locale == "ja":
        return (
            "あなたは物語のディレクターです。次の『お題』を、三幕構成の物語の"
            "『方針』へ具体化してください。\n"
            f"お題: {user_topic.strip()}\n"
            f"三幕は約{span}の間隔で並びます。\n\n"
            "出力する方針（2-3文、自然な日本語）に含めること:\n"
            "- この物語が扱う主題（お題を噛み砕いた一文）\n"
            "- そこに含意される葛藤・目的・旅路・転機\n"
            "- 具体的な状況アンカーを2-3個（状況・関係・出来事など物語上の要素。"
            "外見やdanbooruタグではない）\n\n"
            "重要: これは固定シーンの指定ではなく、三幕が自由に展開してよい『テーマ』です。"
            "時間軸が長くても各幕を1枚の絵に縛らないこと。\n"
            "方針の本文だけを出力（見出し・引用符・説明は不要）。"
        )
    return (
        "You are a story director. Turn the TOPIC below into a short narrative "
        "DIRECTIVE for a three-act story.\n"
        f"TOPIC: {user_topic.strip()}\n"
        f"The three acts are spaced about {span} apart.\n\n"
        "The directive (2-3 sentences, natural English) must convey:\n"
        "- the subject this story is about (one plain sentence distilling the topic)\n"
        "- the conflict, goal, journey or turn it implies\n"
        "- 2-3 concrete situational anchors (situations, relationships, events — "
        "story elements, NOT appearance or danbooru tags)\n\n"
        "IMPORTANT: this is a THEME the three acts explore freely, NOT a fixed "
        "scene. Even across a long time span, do not pin each act to one picture.\n"
        "Output ONLY the directive prose — no headings, quotes or explanation."
    )


def _user_intent_block(user_topic: str, topic_directive: str = "") -> str:
    """USER INTENT block for stages 2 (expand) and 3 (axis). Empty when no topic."""
    topic = user_topic.strip()
    if not topic:
        return ""
    directive_line = (
        "  Story direction distilled from the topic (the SUBJECT all three acts "
        f"explore — a theme, not a fixed scene):\n    {topic_directive.strip()}\n"
        if topic_directive.strip()
        else ""
    )
    return (
        f'\nUSER TOPIC — what the STORY IS ABOUT across all three acts: "{topic}"\n'
        f"{directive_line}"
        '  - If it names an ENDING ("最後は…" / "ends with…" / "結末は…" / "…になる"),\n'
        "    the FUTURE act's concrete action is that ending. Do not substitute.\n"
        '  - If it names an ONGOING action ("…最中" / "…途中" / "in the middle of…"),\n'
        "    ALL three acts stay INSIDE that action; do not resolve or exit it.\n"
        '  - If it names a moment ("…するシーン" / "the moment X"), PRESENT realises\n'
        "    that moment (and it must also match the base image).\n"
        "  - The base image only fixes how the base act LOOKS; the topic decides\n"
        "    what the story is ABOUT. Make every act embody the topic.\n"
        "  - The chosen candidate below is scaffolding; where it conflicts with the\n"
        "    topic, follow the topic.\n"
        "  - Honour the topic's temporal envelope regardless of the scale slider.\n"
    )


_TONE_HOOKS = {
    "bright": "a new opportunity opening, an exciting decision, a bond deepening, "
              "a discovery within reach, or a bold leap about to be taken",
    "neutral": "a turn, a rising stake, a decision, or a fresh question",
    "dark": "a reversal, a rising stake, an exposure, a parting on the brink, or a "
            "fresh question",
}


def _ending_policy_block(user_topic: str, tone: str = "bright") -> str:
    """Cliffhanger ending policy — the fix for the 'forced tidy resolution' problem.

    The future act must LEAN INTO the story's turn and leave the pull open — a
    hook into the next volume. The example hooks are tone-aware so a bright story
    ends on a hopeful, forward-looking beat rather than a grim one. The single
    exception: if the user topic explicitly names an ending, that ending wins.
    """
    hooks = _TONE_HOOKS.get((tone or "bright").strip().lower(), _TONE_HOOKS["bright"])
    exception = (
        " Exception: if the user topic explicitly names an ending, honour that "
        "ending as written instead."
        if user_topic.strip()
        else ""
    )
    return (
        "- ENDING — do NOT tie a bow. The future act must NOT wind down into a "
        "calm summary or a resolved conclusion (avoid \"in the end…\", \"and so she "
        "realises…\", \"最終的に…\", \"こうして…を実感する\"). Leave the reader mid-motion — "
        f"on {hooks} — a hook that pulls toward the next volume.{exception}\n"
    )


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


def _boldness_line(divergence: float) -> str:
    """Story-boldness rule scaled by the Transmute divergence slider."""
    if divergence < 0.3:
        return (
            "- Surprise the reader quietly: prefer grounded, intimate turns "
            "over spectacle."
        )
    if divergence <= 0.6:
        return (
            "- Prefer the unexpected-but-plausible development over the obvious "
            "one; avoid clichés."
        )
    return (
        "- Take the boldest interpretation that still makes narrative sense. "
        "Avoid the first idea that comes to mind — subvert the obvious reading."
    )


def _time_contract_block(*, base_axis: str, time_scale: str) -> str:
    """One consolidated time contract for Stage 2 (story) prompts.

    Replaces the old trio of overlapping ⚠️ALL-CAPS blocks (ABSOLUTE TIME
    CONSTRAINT + BASE ACT lock + delta restatement) with a single calm block.
    The redundant shouting was the main reason the small local model drowned the
    timeline among competing constraints; the substance (base lock, delta,
    must/may/forbidden) is preserved here, once.
    """
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    delta = _scale_delta_line(time_scale)
    return (
        f'TIME CONTRACT (scale "{time_scale}": {span} between acts)\n'
        f"- The [{base_axis.upper()}] act IS the base image: reproduce its scene, "
        "pose, props, lighting and time of day; invent nothing new there.\n"
        f"- Distance between acts — how much may change: {delta}\n"
        f"- Keep IDENTICAL across acts: {rules['must_keep']}\n"
        f"- MAY change: {rules['may_differ']}\n"
        f"- FORBIDDEN to change: {rules['forbidden']}\n"
    )


def build_story_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    base_axis: str,
    worldview: str,
    time_scale: str = "years",
    mutation_tags: list[str] | None = None,
    story_hooks: str = "",
    divergence: float = 0.0,
    emotion: str = "",
    user_topic: str = "",
    topic_directive: str = "",
    dramatic_mode: str = "",
    turn: str = "",
    tone: str = "bright",
) -> str:
    """LLM prompt producing [TITLE]/[OVERALL]/[PAST]/[PRESENT]/[FUTURE] sections."""
    world_line = (
        f'Setting atmosphere / inspiration: "{worldview}" — '
        "use this as backdrop and visual flavour only; the specific scene "
        "details in the base image above always take precedence."
        if worldview.strip()
        else "No setting was specified — invent a fitting, evocative world yourself."
    )
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale="en"
    )
    protect = bool(turn.strip() or _dramatic_mode_line(dramatic_mode))
    hierarchy_block = _coherence_hierarchy_block(
        base_axis=base_axis, user_topic=user_topic, time_scale=time_scale,
        protect_twist=protect,
    )
    intent_block = _user_intent_block(user_topic, topic_directive)
    time_block = _time_contract_block(base_axis=base_axis, time_scale=time_scale)

    shape_line = _dramatic_mode_line(dramatic_mode)
    shape_rule = (
        f"- DRAMATIC SHAPE — drive the whole arc with this shape: {shape_line}\n"
        if shape_line
        else "- Give the arc a clear dramatic shape — a rising stake, a reversal, "
        "an exposure, a threat drawing near — not a flat sequence of events.\n"
    )
    turn_rule = (
        f"- The story hinges on THIS turn — keep it central and let the future "
        f"act lean INTO it, never away from it: {turn.strip()}\n"
        if turn.strip()
        else ""
    )
    ending_block = _ending_policy_block(user_topic, tone)

    mutation_block = ""
    if mutation_tags:
        mutation_block = (
            "\nUnexpected elements to weave into the PAST and FUTURE acts "
            f"(reinterpret them freely): {', '.join(mutation_tags)}\n"
        )
    hooks_block = ""
    if story_hooks.strip():
        hooks_block = (
            "\nNARRATIVE SEEDS — an interpretive reading of the base image "
            "(inspiration only; you may extend or contradict it):\n"
            f"{story_hooks.strip()}\n"
        )
    return (
        "You are a storyteller. Write a three-act chronicle (past, present, future) "
        "of the single character below.\n\n"
        f"{elapsed_header}\n"
        f"{hierarchy_block}\n"
        f"{time_block}"
        f"{intent_block}\n"
        "CHARACTER (visual descriptor tags — interpret as appearance attributes, "
        "NOT as character names or story text):\n"
        f"{character_desc}\n\n"
        f"THE {base_axis.upper()} looks exactly like this scene:\n{scene_desc}\n\n"
        f"{world_line}\n"
        f"{hooks_block}"
        f"{mutation_block}\n"
        "Craft rules:\n"
        "- Each act is a DISTINCT MOMENT on the same thread (obey the time "
        "contract above) — never re-shoot the same instant three times.\n"
        "- Build every act around ONE concrete, stageable physical action the "
        "character is caught mid-doing — reaching, turning to look back, kneeling, "
        "leaning in, gripping, pushing, recoiling, covering the face. The body must "
        "be DOING something an illustrator could draw at a glance; a character "
        "merely standing, sitting upright, or posing is NOT an action. Convey "
        "emotion through the action and expression, not inner monologue. Vary the "
        "action across the three acts — never the identical pose twice.\n"
        f"{shape_rule}"
        f"{turn_rule}"
        "- Carry ONE concrete motif (an object or detail from the scene) through "
        "all three acts, letting its meaning ESCALATE — not merely repeat.\n"
        "- Link the acts by cause and effect (because of PAST, PRESENT; because "
        "of PRESENT, FUTURE) — never three disconnected vignettes.\n"
        "- Give each act its own dominant emotion.\n"
        f"{ending_block}"
        f"{_boldness_line(divergence)}\n"
        f"{_tone_line(tone).rstrip()}\n"
        f"{_emotion_guidance_line(emotion).rstrip()}\n"
        "- 3-6 sentences per act, in English.\n"
        "- Output exactly these five sections, each starting with its marker on "
        "its own line, in this order:\n"
        "[TITLE] — a specific, evocative title (3-8 words) drawn from the "
        "story's concrete imagery (a place, object, or motif). NEVER generic "
        "titles like 'Untitled', 'A Chronicle' or 'A Story'.\n"
        "[OVERALL] — a 2-4 sentence summary of the arc connecting all three acts\n"
        "[PAST] then [PRESENT] then [FUTURE] — the acts themselves.\n"
        "No other headings."
    )


# Tolerant marker matching: [PAST], **[PAST]**, PAST:, **PAST:**, ## PAST: ...
# Bare bracketed markers match anywhere; colon forms must start a line so that
# prose words like "past" are never mistaken for markers.
_SECTION_MARKER_RE = re.compile(
    r"(?im)"
    r"(?:\[\s*(TITLE|OVERALL|PAST|PRESENT|FUTURE)\s*\]"
    r"|^[ \t>#]{0,4}\**(TITLE|OVERALL|PAST|PRESENT|FUTURE)\**[ \t]*:)"
)


def parse_story_sections(raw: str) -> dict[str, str]:
    """Split marker-delimited story output into {section: text}. Missing → ''."""
    result = {section: "" for section in SECTIONS}
    parts = _SECTION_MARKER_RE.split(raw)
    # split with 2 groups → [preamble, g1, g2, text, g1, g2, text, ...]
    for i in range(1, len(parts) - 2, 3):
        section = (parts[i] or parts[i + 1] or "").lower()
        text = (parts[i + 2] or "").strip().lstrip("*:] \t").strip()
        if section in result and not result[section]:
            result[section] = text
    # Title should be a single clean line
    if result["title"]:
        result["title"] = result["title"].splitlines()[0].strip().strip('*"「」')
    return result


def build_story_repair_prompt(raw_story: str) -> str:
    """Fallback when marker parsing fails: restructure the raw output as JSON."""
    return (
        "The text below contains a chronicle with a title, an overall summary, "
        "and three acts (past, present, future), but the formatting is broken.\n"
        "Extract the five parts. Keep the wording — do not rewrite or shorten.\n"
        "If the title or overall summary is genuinely absent, write a fitting "
        "one from the acts.\n\n"
        f"TEXT:\n{raw_story[:6000]}\n\n"
        "Answer with JSON only, using exactly these keys:\n"
        '{"title": "...", "overall": "...", "past": "...", '
        '"present": "...", "future": "..."}'
    )


def _loads_lenient(raw: str):
    """Parse JSON, tolerating prose around a single {...} object. → obj or None."""
    text = raw.strip()
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


def parse_story_json(raw: str) -> dict[str, str]:
    """Parse the repair-pass output. Missing/broken → empty strings per key."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {k: "" for k in SECTIONS}
    return {k: str(data.get(k) or "").strip() for k in SECTIONS}


def build_title_prompt(stories: dict[str, str]) -> str:
    """Last-resort title generation when Stage 2 produced none."""
    return (
        "Give this three-act chronicle a short, specific, evocative title "
        "(3-8 words). Draw on the story's concrete imagery — a place, an "
        "object, a motif. NEVER generic ('Untitled', 'A Chronicle', 'A Story').\n\n"
        f"PAST: {stories.get('past', '')}\n\n"
        f"PRESENT: {stories.get('present', '')}\n\n"
        f"FUTURE: {stories.get('future', '')}\n\n"
        "Return ONLY the title text — no quotes, no explanation."
    )


def build_overall_prompt(title: str, stories: dict[str, str]) -> str:
    """Last-resort overall-story generation when Stage 2 produced none."""
    return (
        "Write a 2-4 sentence overall arc summary connecting all three acts "
        "of this chronicle into a single journey. Be concrete — use the "
        "story's actual events, not abstract themes.\n\n"
        f"TITLE: {title}\n\n"
        f"PAST: {stories.get('past', '')}\n\n"
        f"PRESENT: {stories.get('present', '')}\n\n"
        f"FUTURE: {stories.get('future', '')}\n\n"
        "Return ONLY the summary text — no headings, no quotes."
    )


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


def _dramatic_mode_line(mode: str, locale: str = "en") -> str:
    """One-line story-shape guidance for a dramatic mode. Empty/unknown → ''."""
    pair = _DRAMATIC_MODES.get((mode or "").strip().lower())
    if not pair:
        return ""
    return pair[1] if locale == "ja" else pair[0]


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


def _locale_output_line(locale: str) -> str:
    if locale == "ja":
        return "出力の title / past / present / future / motif / turn はすべて自然で読みやすい日本語で書くこと。"
    return "Write every title / past / present / future / motif / turn field in natural English."


def build_candidates_prompt(
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
    topic_directive: str = "",
    tone: str = "bright",
    biography: dict | None = None,
    seed_tags: list[str] | None = None,
    forced_motif: str = "",
) -> str:
    """LLM prompt producing THREE distinct story candidates as JSON (one call).

    The three ideas diverge along the faithful/rebel/stranger axes. Output
    language follows `locale` (ja/en). The time axis is the STORY ENGINE: the
    base image is the `base_axis` moment, and each candidate must give ONE
    concrete beat per act, where the other two acts are separate moments the
    chosen span before / after the image (not a re-description of it).

    The user topic (お題) is hoisted to the VERY TOP of the prompt (above HARD
    RULES and seed tags) and expanded with `topic_directive`, because a small
    local model otherwise drowns an abstract topic under competing constraints
    and re-tells the picture.
    """
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    has_topic = bool(user_topic.strip())
    # Hoisted, high-salience topic block (rendered FIRST when present).
    if has_topic:
        directive_part = (
            "Story direction (the SUBJECT the three acts explore — a theme, not a "
            f"fixed scene):\n{topic_directive.strip()}\n"
            if topic_directive.strip()
            else ""
        )
        topic_block = (
            "★ USER TOPIC (お題) — THIS is what the story must be ABOUT ★\n"
            f'Topic: "{user_topic.strip()}"\n'
            f"{directive_part}"
            "PRIORITY: the topic decides the SUBJECT of all three candidates. "
            "Every act of A, B and C must embody this topic as a concrete drawable "
            "event. The base image below only fixes how the base act LOOKS; it does "
            "NOT decide the subject — the topic does. Do not let the picture pull "
            "the story back into a plain depiction of itself.\n"
            "Honour the topic's tense and aspect literally: if it describes an "
            "action IN PROGRESS (e.g. \"…している最中\", \"in the middle of doing X\"), "
            "ALL three acts stay INSIDE that ongoing action — do NOT resolve, "
            "complete or walk away from it.\n"
        )
    else:
        topic_block = (
            "No topic was given — invent three genuinely different premises yourself.\n"
        )
    world_line = (
        f'Setting atmosphere / worldview: "{worldview.strip()}" — backdrop and '
        "flavour only; the scene details above take precedence."
        if worldview.strip()
        else "No worldview was specified — invent fitting ones."
    )
    modes = candidate_modes or {}

    def _spirit_line(cid: str, flavour: str, desc: str) -> str:
        line = f"  Candidate {cid} ({flavour}): {desc}"
        mode_line = _dramatic_mode_line(modes.get(cid, ""), locale)
        if mode_line:
            line += f"\n    Dramatic shape for {cid} — {mode_line}"
        return line

    spirits_block = "\n".join(
        _spirit_line(cid, flavour, desc) for cid, flavour, desc in _CANDIDATE_SPIRITS
    )
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale=locale
    )
    delta = _scale_delta_line(time_scale)
    guardrail = (
        "GROUNDING — keep all three candidates in the SAME real-world register as "
        "the image. Unless the worldview above explicitly asks for it, do NOT add "
        "space, aliens, magic, spirits, ghosts, or any supernatural / sci-fi "
        "element. Find the surprise in human, emotional and situational twists — "
        "a hidden motive, an unseen person, an ironic turn — NOT in a genre shift."
    )
    hierarchy_block = _coherence_hierarchy_block(
        base_axis=base_axis, user_topic=user_topic, time_scale=time_scale
    )
    head = chronicle_hard_rules_preamble(locale=locale, has_user_topic=has_topic)
    seed_block = chronicle_seed_tags_block(
        seed_tags, forced_motif=forced_motif, locale=locale
    )
    bio_block = ""
    if biography:
        bio_block = (
            "CHARACTER BIOGRAPHY (hobbies/items as physical actions only — never "
            "override the USER TOPIC subject):\n"
            f"  {_biography_brief(biography)}\n\n"
        )
    motif_json_hint = forced_motif or "one concrete recurring object"
    # Topic-first when present: small models overweight the opening tokens.
    lead = (
        f"{topic_block}\n{hierarchy_block}\n{head}\n{seed_block}\n"
        if has_topic else
        f"{head}\n{seed_block}\n{topic_block}\n{hierarchy_block}\n"
    )
    return (
        f"{lead}"
        "You are a storyteller pitching THREE different chronicles for the same "
        "character. Each chronicle is THREE MOMENTS of ONE ongoing story, "
        f"separated by {span} of elapsed time.\n\n"
        f"{elapsed_header}\n"
        f"{bio_block}"
        "CHARACTER (visual descriptor tags — appearance only, not names):\n"
        f"{character_desc}\n\n"
        f"THE BASE IMAGE IS THE [{base_axis.upper()}] MOMENT (t = 0) — it looks "
        f"exactly like this (this fixes the base act's LOOK only, not the subject):"
        f"\n{scene_desc}\n\n"
        f"{world_line}\n\n"
        "⚠️ TIME AXIS — this is the STORY ENGINE, not decoration ⚠️\n"
        f"Use the elapsed-time header above as the axis map. Each act opens a new "
        f"volume at the marked elapsed distance from base (scale key: \"{time_scale}\").\n"
        f"HOW MUCH CHANGES between the acts: {delta}\n"
        "Same characters and same world throughout — only the MOMENT moves, and "
        "each act stays causally tethered to the base image. Do NOT jump to an "
        "origin story or a far-off ending when the scale is short.\n"
        f"Visual continuity for this scale — keep: {rules['must_keep']}; "
        f"may change: {rules['may_differ']}.\n\n"
        f"{guardrail}\n"
        f"{_tone_line(tone, locale)}"
        f"{_emotion_guidance_line(emotion, locale)}\n"
        "Make the three candidates genuinely distinct — each pairs a different "
        "reading (below) with a different dramatic shape, so the three diverge in "
        "BOTH viewpoint and plot. A surprising turn can be delightful or hopeful "
        "— surprise does NOT have to mean darkness:\n"
        f"{spirits_block}\n\n"
        f"{_locale_output_line(locale)}\n\n"
        "For EACH candidate write ONE concrete sentence per act — past, present, "
        f"future — where the [{base_axis}] sentence matches the base image (t = 0) "
        "and the other two acts open the elapsed volumes marked in the header "
        "above, driven by that candidate's dramatic shape. Do NOT tidy the arc "
        "into a neat resolution: the future beat should LEAN INTO the turn (a "
        "rising stake, a reversal, an exposure, a threat nearly upon them) and "
        "leave the reader wanting the next volume — unless the user topic names an "
        "explicit ending. Also give: a title (3-8 words, specific and evocative, "
        "never generic); a motif "
        f"(use '{motif_json_hint}' when a fixed motif was given); a one-sentence "
        "`turn` naming the single surprising pivot; echo `dramatic_mode`; and "
        "`grounded_tags` — an array of the SEED TAG names you actually turned into "
        "events (English danbooru spelling).\n\n"
        f"{_CHRONICLE_FEWSHOT_CANDIDATES}\n\n"
        "Answer with JSON only, no markdown fences:\n"
        '{"candidates": [\n'
        '  {"id": "A", "title": "...", "dramatic_mode": "...", "past": "...", '
        '"present": "...", "future": "...", "motif": "...", "turn": "...", '
        '"grounded_tags": ["tag_a", "tag_b"]},\n'
        '  {"id": "B", ...},\n'
        '  {"id": "C", ...}\n'
        "]}"
    )


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
_BIO_DOMAINS = (
    "competitive swimming", "street photography", "amateur astronomy", "pottery",
    "retro video games", "rock climbing", "jazz piano", "embroidery", "chess",
    "birdwatching", "car & engine tinkering", "kyudo (archery)", "coding side-projects",
    "ballet", "sea fishing", "urban gardening", "skateboarding", "calligraphy",
    "marine biology", "distance running", "beekeeping", "watercolour painting",
    "kendo", "tarot & fortune-telling", "figure skating", "herbalism & tea blends",
    "vintage fashion hunting", "road cycling", "keeping tropical fish", "close-up magic",
    "rock & mineral collecting", "graffiti / mural art", "amateur radio", "surfing",
    "baking pastries", "knitting & crochet", "model kit building", "cosplay sewing",
    "rhythm games at the arcade", "hiking & bouldering", "stargazing photography",
    "playing the drums", "growing succulents", "collecting vinyl records",
    "kickboxing", "origami", "volunteering at an animal shelter", "roller derby",
    "bookbinding", "brewing coffee by hand",
)


def sample_bio_domains(n: int = 5, *, rng=None) -> list[str]:
    """Pick n distinct interest domains as biography inspiration (rng injectable)."""
    import random as _random
    rng = rng or _random
    pool = list(_BIO_DOMAINS)
    rng.shuffle(pool)
    return pool[:max(0, n)]


def build_biography_prompt(
    *,
    character_desc: str,
    scene_desc: str,
    wd14_tags: list[str] | None = None,
    worldview: str = "",
    locale: str = "en",
    inspiration_domains: list[str] | None = None,
) -> str:
    """VLM/LLM prompt inventing a character BIOGRAPHY from the base image.

    Personality / hobbies / favourite items / backstory — NOT appearance. Output
    is English (canonical, used to ground image prompts + WD14 vector search).
    `inspiration_domains` (a rotating random sample) steers variety so different
    characters get genuinely different lives.
    """
    tags = ", ".join((wd14_tags or [])[:40])
    world = f'Setting / worldview: "{worldview.strip()}"\n' if worldview.strip() else ""
    domains = ", ".join(inspiration_domains or [])
    domain_block = (
        "VARIETY — make her DISTINCT; do NOT fall back on the usual defaults "
        "(baking, violin, reading, pressing flowers). Draw her hobbies and "
        "favourite items from varied areas — for THIS character consider e.g.: "
        f"{domains}; or anything else that genuinely fits her image. Pick what "
        "suits the scene, not the same handful every time.\n"
        if domains
        else "VARIETY — make her DISTINCT; avoid the usual defaults (baking, "
        "violin, reading). Choose hobbies/items that genuinely fit HER image.\n"
    )
    return (
        "From the image description below, invent a believable BIOGRAPHY for this "
        "single character — who she is as a person, NOT how she looks (appearance "
        "is already fixed). Ground it in the visible cues (setting, objects, mood) "
        "but flesh out an inner life.\n\n"
        f"CHARACTER (appearance tags — do NOT restate as biography): {character_desc}\n"
        f"SCENE: {scene_desc}\n"
        f"WD14 tags: {tags}\n"
        f"{world}\n"
        f"{domain_block}"
        "Keep hobbies, favourite items and quirks CONCRETE and PHYSICAL — things "
        "she visibly DOES / HOLDS / USES so they can drive a picture — never vague "
        "traits like 'loves music'.\n"
        "Output English JSON only, no markdown fences:\n"
        '{"personality": "<2-3 sentences>", '
        '"occupation": "<role / student life>", '
        '"hobbies": ["<physical hobby>", "...3-5"], '
        '"favourite_items": ["<concrete object she owns/carries>", "...3-5"], '
        '"likes": ["...", "...2-4"], "dislikes": ["...", "...1-3"], '
        '"quirks": ["<a habitual physical gesture>", "...1-3"], '
        '"backstory": "<2-3 sentences of history>"}'
    )


def parse_biography_json(raw: str) -> dict:
    """Parse a biography payload. Missing/broken → {} (feature degrades off)."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for k in _BIO_STR_KEYS:
        out[k] = str(data.get(k) or "").strip()
    for k in _BIO_LIST_KEYS:
        v = data.get(k)
        out[k] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
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


_TIMETABLE_KEYS = ("label", "activity", "place", "feeling")

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


def build_timetable_prompt(
    *,
    biography: dict | None,
    scene_desc: str,
    time_scale: str = "years",
    base_axis: str = "present",
    locale: str = "en",
    selected: dict | None = None,
    user_topic: str = "",
) -> str:
    """Turn the CHOSEN STORY into a fine-grained timetable that COVERS the time
    axis, centred on the base moment.

    Crucially this is the SELECTED story unfolding across time, grounded in the
    base image's actual SETTING — NOT a generic hobby diary. The biography is
    personality flavour only; it must not drop in activities (knitting,
    journaling…) that don't belong to this scene or story. English output.
    """
    window, slots = _TIMETABLE_WINDOW.get(
        time_scale, _TIMETABLE_WINDOW["years"]
    )
    story_block = ""
    if selected:
        beats = "; ".join(
            f"{a}: {selected.get(a, '')}" for a in AXES if selected.get(a)
        )
        story_block = (
            "CHOSEN STORY — the timetable is THIS story playing out over time, not "
            "a generic day:\n"
            f"  \"{selected.get('title', '')}\" — {beats}\n"
        )
    topic_line = f'Topic (お題): "{user_topic.strip()}"\n' if user_topic.strip() else ""
    return (
        "Turn the CHOSEN STORY below into a fine-grained timetable so a picture "
        "can be drawn for each moment. The timetable is THIS STORY unfolding "
        "across time — never a generic hobby diary.\n\n"
        f"TABLE SPAN: {window}.\n"
        f"SLICING: {slots}. The MIDDLE slot (labelled \"now\" / \"today\" / her "
        "current age) IS the base image moment and must match it; detail the "
        "slots just before and after it especially clearly.\n\n"
        f"{story_block}"
        f"{topic_line}"
        f"THE SETTING — every slot happens in or around this place unless the "
        f"story itself clearly moves her: {scene_desc}\n"
        f"CHARACTER (personality flavour ONLY — do NOT invent hobbies or props "
        f"that don't fit the scene/story): {_biography_brief(biography)}\n\n"
        "For EACH slot give ONE concrete physical action that advances the story, "
        "WHERE (consistent with the setting above), and how she FEELS. Consecutive "
        "slots must flow into each other. Actions must be drawable — never "
        "'relaxing', 'thinking' or 'spending time', and never an unrelated hobby "
        "dropped into a scene where it makes no sense.\n"
        "Mark each slot with an `axis` field: exactly one slot each for "
        '"past", "present", and "future" (matching the story beats); other slots '
        'may use "bridge".\n'
        "Output English JSON only, no fences:\n"
        '{"slots": [{"axis": "past|present|future|bridge", '
        '"label": "<relative time, e.g. -20min / now / +20min>", '
        '"activity": "<concrete physical action tied to the story>", '
        '"place": "...", "feeling": "..."}, "..."]}'
    )


def parse_timetable_json(raw: str) -> list[dict]:
    """Parse a timetable payload into a list of slot dicts. Broken → []."""
    data = _loads_lenient(raw)
    slots = None
    if isinstance(data, dict):
        slots = data.get("slots")
        if slots is None:
            slots = data.get("timetable") or data.get("schedule")
    elif isinstance(data, list):
        slots = data
    if not isinstance(slots, list):
        return []
    out: list[dict] = []
    keys = _TIMETABLE_KEYS + ("axis",)
    for s in slots:
        if not isinstance(s, dict):
            continue
        item = {k: str(s.get(k) or "").strip() for k in keys}
        if item["activity"] or item["label"]:
            out.append(item)
    return out


def build_concrete_activities_prompt(
    *,
    biography: dict | None,
    timetable: list[dict] | None,
    selected: dict,
    scene_desc: str,
    base_axis: str = "present",
    time_scale: str = "years",
    user_topic: str = "",
    locale: str = "en",
    axis_slots: dict[str, dict] | None = None,
    seed_tags: list[str] | None = None,
    forced_motif: str = "",
) -> str:
    """Re-examine bio + timetable + chosen draft → ONE drawable action per axis.

    This is the anti-stiff-pose core: it forces each act to be a concrete
    physical action using a specific hobby/item, English, feeding situation_en.
    """
    elapsed = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale="en"
    )
    tt = "\n".join(
        f"  - {s.get('label', '')}: {s.get('activity', '')} @ {s.get('place', '')} "
        f"({s.get('feeling', '')})"
        for s in (timetable or [])
    ) or "  (no timetable)"
    beats = "".join(
        f"  [{a.upper()}] draft: {selected.get(a, '')}\n" for a in AXES if selected.get(a)
    ) or "  (no draft)\n"
    topic = f'Topic (お題): "{user_topic.strip()}"\n' if user_topic.strip() else ""
    head = chronicle_hard_rules_preamble(
        locale="en", has_user_topic=bool(user_topic.strip())
    )
    seed_block = chronicle_seed_tags_block(
        seed_tags, forced_motif=forced_motif, locale="en"
    )
    anchors = format_axis_slots_block(axis_slots, locale="en")
    priority = (
        "PRIORITY: TIME ANCHORS (when present) define the on-screen physical fact "
        "for each act; the CHOSEN STORY DRAFT supplies motive and dramatic turn; "
        "the timetable/biography only supply props or gestures. Do NOT drop in an "
        "unrelated hobby if it does not fit the story or the base scene.\n"
        if axis_slots else
        "PRIORITY: the CHOSEN STORY DRAFT drives what happens; the timetable and "
        "biography only supply concrete detail (a prop, a gesture). Do NOT drop "
        "in an unrelated hobby (knitting, journaling…) if it does not fit the "
        "story or the base scene.\n"
    )
    return (
        f"{head}\n"
        f"{seed_block}\n"
        "Pin down EXACTLY what the character is physically doing at each of the "
        "three story moments, by cross-checking the chosen story draft, the "
        "timetable and her biography. Every moment must be ONE concrete, drawable "
        "physical action — NEVER standing, sitting or lounging idle.\n\n"
        f"{elapsed}\n"
        f"{priority}"
        f"BASE SCENE — all three moments stay in or around THIS setting unless the "
        f"story clearly moves her: {scene_desc}\n"
        "CHOSEN STORY DRAFT (the spine — refine each beat into a concrete action):\n"
        f"{beats}"
        f"{topic}"
        f"{anchors}"
        f"TIMETABLE (nearby moments for continuity):\n{tt}\n"
        f"CHARACTER (flavour only): {_biography_brief(biography)}\n\n"
        "For each axis state the concrete action (verb + body + prop + place) that "
        f"realises that beat. The [{base_axis.upper()}] action must match the base "
        "scene exactly.\n"
        "Output English JSON only, no fences:\n"
        '{"past": "<one concrete action sentence>", '
        '"present": "<...>", "future": "<...>"}'
    )


def parse_concrete_activities_json(raw: str) -> dict:
    """Parse {past,present,future} concrete actions. Missing keys → '' per axis."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {}
    return {a: str(data.get(a) or "").strip() for a in AXES}


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
    pairs = [(0, 1), (0, 2), (1, 2)]
    sims = [_text_similarity(beats[i], beats[j]) for i, j in pairs]
    return sum(sims) / len(sims)


def _candidate_beats_degenerate(
    candidate: dict, *, threshold: float = _BEAT_SIMILAR_THRESHOLD
) -> bool:
    """True if a candidate's three act beats restate one moment (timeline collapsed).

    A missing beat also counts as degenerate — there is nothing to distinguish.
    """
    beats = [str(candidate.get(a) or "").strip() for a in AXES]
    if not all(beats):
        return True
    return _mean_pairwise_similarity(beats) >= threshold


def candidates_degenerate(candidates: list[dict]) -> bool:
    """True when the candidate SET is too weak — most candidates collapse the
    timeline — so the runner should regenerate once at a higher temperature."""
    if not candidates:
        return True
    n_bad = sum(1 for c in candidates if _candidate_beats_degenerate(c))
    return n_bad >= max(2, (len(candidates) + 1) // 2)


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


def axis_slots_collapsed(
    axis_slots: dict[str, dict] | None, *, threshold: float = _BEAT_SIMILAR_THRESHOLD
) -> bool:
    """True when bound timetable place+activity strings collapse across acts."""
    if not axis_slots:
        return False
    beats = []
    for a in AXES:
        s = axis_slots.get(a) or {}
        text = f"{s.get('place', '')} {s.get('activity', '')}".strip()
        beats.append(text)
    if not all(beats):
        return False
    return _mean_pairwise_similarity(beats) >= threshold


def build_differentiate_activities_prompt(
    *,
    activities: dict[str, str],
    selected: dict,
    base_axis: str,
    time_scale: str = "years",
    scene_desc: str = "",
    axis_slots: dict[str, dict] | None = None,
    user_topic: str = "",
) -> str:
    """Rewrite collapsed concrete actions into three distinct drawable moments."""
    elapsed = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale="en"
    )
    anchors = format_axis_slots_block(axis_slots, locale="en")
    topic = f'Topic (お題): "{user_topic.strip()}"\n' if user_topic.strip() else ""
    beats = "".join(
        f"  [{a.upper()}] draft: {selected.get(a, '')}\n" for a in AXES if selected.get(a)
    )
    return (
        f"{chronicle_hard_rules_preamble(locale='en', has_user_topic=bool(user_topic.strip()))}\n"
        "These three concrete actions read as the SAME physical moment restated "
        "three times. Rewrite them so each axis is a CLEARLY DIFFERENT drawable "
        "action at its marked elapsed distance — different verb, prop, and "
        "(where the scale allows) place — while keeping the same character and "
        "story spine.\n\n"
        f"{elapsed}\n"
        f"BASE SCENE ([{base_axis.upper()}] must still match): {scene_desc}\n"
        f"{topic}"
        f"{anchors}"
        "STORY DRAFT spine:\n"
        f"{beats}"
        "CURRENT ACTIONS (too alike — push them apart):\n"
        f"  PAST: {activities.get('past', '')}\n"
        f"  PRESENT: {activities.get('present', '')}\n"
        f"  FUTURE: {activities.get('future', '')}\n\n"
        f"The [{base_axis.upper()}] action must still match the base scene. "
        "Move the OTHER two to their own moments.\n"
        "Output English JSON only, no fences:\n"
        '{"past": "<one concrete action sentence>", '
        '"present": "<...>", "future": "<...>"}'
    )


def build_differentiate_acts_prompt(
    *,
    title: str,
    overall: str,
    stories: dict[str, str],
    base_axis: str,
    time_scale: str = "years",
    locale: str = "en",
) -> str:
    """Rewrite prompt fired when the three acts collapsed into one moment.

    Pushes the acts apart along the timeline while keeping the base act matched
    to the image. Emits the same [TITLE]/[OVERALL]/[PAST]/[PRESENT]/[FUTURE]
    markers so parse_story_sections consumes it unchanged.
    """
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale=locale
    )
    time_block = _time_contract_block(base_axis=base_axis, time_scale=time_scale)
    lang = (
        "各セクションの本文は自然で読みやすい日本語で書くこと。"
        if locale == "ja"
        else "Write all section body text in natural English."
    )
    return (
        "These three acts of one chronicle read as the SAME moment restated three "
        "times — the timeline has collapsed. Rewrite them so each act is a "
        "clearly DIFFERENT moment at its marked elapsed distance, while keeping "
        "the same characters, motif and overall arc.\n\n"
        f"{elapsed_header}\n"
        f"{time_block}\n"
        f"The [{base_axis.upper()}] act must still match the base image; move the "
        "OTHER two acts to their own moments (different action, beat and — where "
        "the scale allows — setting), keeping cause and effect between them.\n\n"
        f"TITLE: {title}\n"
        f"OVERALL: {overall}\n"
        f"CURRENT PAST: {stories.get('past', '')}\n"
        f"CURRENT PRESENT: {stories.get('present', '')}\n"
        f"CURRENT FUTURE: {stories.get('future', '')}\n\n"
        f"{lang}\n"
        "Output exactly these markers, each on its own line:\n"
        "[TITLE] then [OVERALL] then [PAST] then [PRESENT] then [FUTURE].\n"
        "No other headings."
    )


def build_expand_prompt(
    *,
    selected: dict,
    character_desc: str,
    scene_desc: str,
    base_axis: str,
    worldview: str,
    time_scale: str = "years",
    story_hooks: str = "",
    divergence: float = 0.0,
    emotion: str = "",
    locale: str = "en",
    mutation_tags: list[str] | None = None,
    user_topic: str = "",
    topic_directive: str = "",
    biography: dict | None = None,
    timetable: list[dict] | None = None,
    tone: str = "bright",
    seed_tags: list[str] | None = None,
    forced_motif: str = "",
    axis_slots: dict[str, dict] | None = None,
) -> str:
    """LLM prompt expanding ONE chosen candidate into the full three acts.

    Same markers/structure as build_story_prompt, but seeded by the selected
    candidate's per-act beats (title/past/present/future/motif) plus its
    dramatic_mode + turn (carried through as PROTECTED so the surprise survives
    expansion), and written in the user's locale. Older candidates without beats
    fall back to summary. When a biography / timetable are supplied they are
    woven in as grounding so the prose reads as this specific person's life.
    """
    bio_block = ""
    if biography:
        bio_block = (
            "\nCHARACTER BIOGRAPHY (ground the prose in this person — her hobbies, "
            "favourite items and quirks should surface as concrete actions):\n"
            f"  {_biography_brief(biography)}\n"
        )
    tt_block = format_axis_slots_block(axis_slots, locale=locale)
    if not tt_block and timetable:
        tt_lines = "; ".join(
            f"{s.get('label', '')}: {s.get('activity', '')}" for s in timetable[:6]
        )
        tt_block = (
            f"  Time anchors (not a diary): {tt_lines}\n"
        )
    beats = "".join(
        f"  [{a.upper()}] seed: {selected.get(a, '')}\n"
        for a in AXES if selected.get(a)
    ) or f"  Summary: {selected.get('summary', '')}\n"
    motif = (
        forced_motif
        or selected.get("motif")
        or selected.get("key_motif")
        or ""
    )
    dramatic_mode = str(selected.get("dramatic_mode") or "").strip().lower()
    turn = str(selected.get("turn") or "").strip()
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale=locale
    )
    turn_seed = (
        f"  Central turn (PROTECTED — keep it, do not soften): {turn}\n"
        if turn
        else ""
    )
    head = chronicle_hard_rules_preamble(
        locale=locale, has_user_topic=bool(user_topic.strip())
    )
    seed_block = chronicle_seed_tags_block(
        seed_tags, forced_motif=forced_motif or motif, locale=locale
    )
    seed_block_story = (
        f"{elapsed_header}\n"
        "CHOSEN STORY DIRECTION — expand THESE beats to satisfy the base image "
        "and the user topic above; reword the beats wherever they conflict, but "
        "keep the central turn and dramatic shape intact (keep the title unless "
        "it genuinely no longer fits):\n"
        f"  Title: {selected.get('title', '')}\n"
        f"{beats}"
        f"{turn_seed}"
        "  Motif (must recur and ESCALATE in meaning across all three acts): "
        f"{motif}\n"
        f"{bio_block}{tt_block}\n"
    )
    lang_block = (
        "\n言語ルール: [TITLE]/[OVERALL]/[PAST]/[PRESENT]/[FUTURE] のマーカーは"
        "英語のまま残し、各セクションの本文はすべて自然で読みやすい日本語で書くこと。\n"
        if locale == "ja"
        else "\nLanguage: write all section body text in natural English.\n"
    )
    base = build_story_prompt(
        character_desc=character_desc,
        scene_desc=scene_desc,
        base_axis=base_axis,
        worldview=worldview,
        time_scale=time_scale,
        mutation_tags=mutation_tags,
        story_hooks=story_hooks,
        divergence=divergence,
        emotion=emotion,
        user_topic=user_topic,
        topic_directive=topic_directive,
        dramatic_mode=dramatic_mode,
        turn=turn,
        tone=tone,
    )
    return f"{head}\n{seed_block}\n{seed_block_story}" + base + lang_block


def build_story_tags_prompt(story_text: str, *, count: int = 50) -> str:
    """Ask for ~count danbooru tags describing ONE act's scene (WD14-style).

    Called per axis on that act's story, so each moment gets its own rich tag
    set (the past act's scene tags differ from the future act's).
    """
    return (
        f"Below is ONE scene from a story. Infer about {count} danbooru tags that "
        "describe THIS scene as fully as possible — the character's appearance "
        "(hair, eyes, face, body), clothing and accessories, pose and action, "
        "the location and background, time of day, props and objects, lighting, "
        "colour palette, mood and art style. Be specific and comprehensive; use "
        "real danbooru tag spellings with underscores.\n"
        "PRIORITISE the character's PHYSICAL ACTION, POSE and CAMERA ANGLE: emit "
        "concrete danbooru action/pose tags (e.g. reaching, outstretched_arm, "
        "leaning_forward, looking_back, kneeling, gripping, holding, covering_face, "
        "dynamic_pose) and a framing tag (from_side, from_above, cowboy_shot…). "
        "Do NOT fall back to a static default of just 'standing'/'solo' — capture "
        "what the body is actually doing in this scene.\n\n"
        f"SCENE:\n{story_text}\n\n"
        'Answer with JSON only: {"tags": ["tag_1", "tag_2", ...]}'
    )


def parse_tags_json(raw: str, *, limit: int = 60) -> list[str]:
    """Parse a {"tags": [...]} payload into underscored tags. Missing/broken → []."""
    data = _loads_lenient(raw)
    tags = data.get("tags") if isinstance(data, dict) else data
    if not isinstance(tags, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        tag = str(t).strip().replace(" ", "_")
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:limit]


# ── Stage 3: per-axis Visual Script prompt ────────────────────────────────────

_VISUAL_SCRIPT_GUIDE = (
    "Write a VISUAL SCRIPT: flowing English prose where every concrete visual "
    "element is simultaneously named in danbooru vocabulary within ASCII "
    "parentheses. This text goes directly into an AI image generator.\n"
    "Write exactly 5 flowing paragraphs (2-4 sentences each). Do NOT label the "
    "paragraphs — this structure is an INTERNAL GUIDE ONLY:\n"
    "Paragraph 1 — APPEARANCE: subject count as very first tag (1girl/solo/...), "
    "then hair, eyes, face, expression, clothing, accessories.\n"
    "Paragraph 2 — ACTION: INVENT a physically vivid, story-specific pose. "
    "Never default to 'standing' or 'sitting facing forward'. Name a concrete "
    "micro-action: hands pressing against cold glass, fingers tracing a map edge, "
    "body half-turned mid-step, weight shifted onto one knee. "
    "The pose must be emotionally legible at a glance and visually distinct "
    "from a neutral upright stance.\n"
    "Paragraph 3 — ENVIRONMENT: location, background, setting, time of day. "
    "Fill the frame with *specific* place cues (a lit cafe storefront with "
    "bottles in the window, a streetlamp, distant mountains, stadium bleachers "
    "and crowd) — never a vague empty backdrop.\n"
    "Paragraph 4 — DETAIL: textures, props, fine details, lighting direction "
    "and quality. Name the light (rim light / backlight / golden hour / long "
    "shadows / warm interior glow) and at least two props the eye can rest on.\n"
    "Paragraph 5 — MOOD: color temperature, atmosphere, overall impression. "
    "Add weather/particles when fitting (wind in hair/scarf, confetti, haze).\n"
    "Embed danbooru tags inline in ASCII parentheses right after each element, "
    'e.g. "A (1girl, solo) with (long_hair, silver_hair) grips a (sword, '
    'holding_sword) on a (rooftop) under (night_sky, full_moon)."\n'
    "ACTION-ANCHOR — this is what makes the image express the story, so it is "
    "MANDATORY: every physical action in the act MUST surface as danbooru action "
    "tags in paragraph 2, placed right after the subject/appearance tags so the "
    "pose reads strongly. Translate story verbs into real danbooru pose tags, e.g. "
    "grip→(gripping, clenched_hand), touch→(touching, fingertips), reach→(reaching, "
    "outstretched_arm), run→(running, dynamic_pose, leaning_forward), kneel→"
    "(kneeling, one_knee), look back→(looking_back, turning_head), lean→(leaning_"
    "forward), fall→(falling), hold hands→(holding_hands), cover face→(covering_"
    "face, hand_over_own_mouth). Use the concrete tag, never a vague phrase like "
    "'(tender_gesture)'. Add a camera/framing tag for the pose (from_side, "
    "from_above, from_behind, dutch_angle, cowboy_shot, close-up…).\n"
    "FORBIDDEN as the whole pose: a bare (standing) / (sitting) / (arms_at_sides) / "
    "(expressionless) with no action tag — a motionless upright figure is exactly "
    "the boring default this must avoid. Unless the story truly depicts stillness, "
    "the character must be visibly mid-action.\n"
    "At least 2 danbooru tags per sentence. English only. No vague phrases. "
    "NEVER add quality meta-tags (masterpiece, best_quality, highres etc.)."
)


# Refine-parity category buckets (UI + structured view for image models).
CHRONICLE_CAT_FIELDS = (
    "subject_tags",
    "hair_tags",
    "expression_tags",
    "clothing_tags",
    "accessory_tags",
    "pose_tags",
    "background_tags",
    "object_tags",
    "lighting_tags",
)

_VS_LABELED_TAG_FOOTER = (
    "After the 5-paragraph prose (still inside POSITIVE, after the prose), "
    "output ONLY these labeled category lines. Prefer tags already present in "
    "the PASS 1 TAG LINE — do not invent a parallel taxonomy. Leave a bucket "
    "empty if nothing fits:\n\n"
    "SUBJECT_TAGS: [comma,separated,danbooru,tags]\n"
    "HAIR_TAGS: [comma,separated,danbooru,tags]\n"
    "EXPRESSION_TAGS: [comma,separated,danbooru,tags]\n"
    "CLOTHING_TAGS: [comma,separated,danbooru,tags]\n"
    "ACCESSORY_TAGS: [comma,separated,danbooru,tags]\n"
    "POSE_TAGS: [comma,separated,danbooru,tags]\n"
    "BACKGROUND_TAGS: [comma,separated,danbooru,tags]\n"
    "OBJECT_TAGS: [comma,separated,danbooru,tags]\n"
    "LIGHTING_TAGS: [comma,separated,danbooru,tags]"
)

_VS_LABEL_RE = re.compile(
    r"^(SUBJECT|HAIR|EXPRESSION|CLOTHING|ACCESSORY|POSE|BACKGROUND|OBJECT|LIGHTING)_TAGS:\s*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)
_VS_SECTION_MARKER_RE = re.compile(
    r"\[(?:CHARACTER|ACTION|SCENE|DETAIL|MOOD)\]\s*", re.I
)

_BUCKET_SUBJECT = frozenset({
    "1girl", "1boy", "2girls", "2boys", "3girls", "3boys", "4girls", "6+girls",
    "solo", "solo_focus", "multiple_girls", "multiple_boys", "couple",
})
_BUCKET_EXPR = frozenset({
    "smile", "smiling", "laughing", "blush", "tears", "crying", "open_mouth",
    "closed_mouth", "serious", "angry", "sad", "happy", "nervous", "shy",
    "expressionless", "grin", "frown", "pout", "wink", "surprised", "scared",
    "looking_at_viewer", "looking_away", "looking_back", "looking_down",
    "looking_up", "closed_eyes", "teary_eyes", "half-closed_eyes", "watery_eyes",
})
_BUCKET_LIGHT = frozenset({
    "sunset", "sunrise", "golden_hour", "rim_light", "backlight", "lens_flare",
    "cinematic_lighting", "volumetric", "god_rays", "warm_light", "cool_light",
    "neon", "moonlight", "daylight", "soft_light", "sparkle", "glow",
    "afternoon", "evening", "morning", "night", "dusk", "dawn",
})
_BUCKET_POSE = frozenset({
    "standing", "sitting", "kneeling", "crouching", "lying", "running",
    "walking", "jumping", "reaching", "holding", "pointing", "waving",
    "leaning", "dynamic_pose", "from_side", "from_above", "from_below",
    "cowboy_shot", "upper_body", "full_body", "close-up", "profile",
    "outstretched_arm", "arms_up", "hands_on_hips", "crossed_arms",
})
_BUCKET_BG = frozenset({
    "outdoors", "indoors", "beach", "ocean", "street", "cityscape", "park",
    "forest", "sky", "cloud", "room", "classroom", "cafe", "shop", "storefront",
    "stadium", "festival", "rooftop", "bridge", "mountain", "scenery",
    "simple_background", "white_background", "blurry_background",
})
_BUCKET_OBJ = frozenset({
    "bicycle", "bike", "scarf", "umbrella", "bag", "book", "cup", "mug",
    "sword", "phone", "flower", "shell", "lantern", "confetti", "medal",
})
_EXPR_EYE_PREFIXES = ("teary", "closed", "empty", "half", "watery", "tired")


def parse_visual_script_category_tags(text: str) -> tuple[str, dict[str, list[str]]]:
    """Split Visual Script body into prose + Refine-style category dict."""
    src = text or ""
    first_m = _VS_LABEL_RE.search(src)
    if first_m:
        prose = src[: first_m.start()].strip()
        tags_block = src[first_m.start():]
    else:
        prose = src.strip()
        tags_block = ""
    prose = _VS_SECTION_MARKER_RE.sub("", prose).strip()
    cats: dict[str, list[str]] = {}
    for m in _VS_LABEL_RE.finditer(tags_block):
        field = m.group(1).lower() + "_tags"
        raw = (m.group(2) or "").strip().strip("[]")
        tags = [
            t.strip().replace(" ", "_")
            for t in raw.split(",")
            if t.strip() and t.strip() not in ("[", "]")
        ]
        seen: set[str] = set()
        out: list[str] = []
        for t in tags:
            k = t.lower()
            if k not in seen:
                seen.add(k)
                out.append(t)
        cats[field] = out
    return prose, cats


def bucket_danbooru_tags(tag_line: str) -> dict[str, list[str]]:
    """Heuristic category buckets from a flat danbooru tag line (no LLM)."""
    parts = [
        t.strip().replace(" ", "_")
        for t in (tag_line or "").split(",")
        if t.strip()
    ]
    cats: dict[str, list[str]] = {k: [] for k in CHRONICLE_CAT_FIELDS}
    for tag in parts:
        low = tag.lower()
        toks = set(low.split("_"))
        if low in _BUCKET_SUBJECT or low.startswith(("1girl", "1boy", "2girl", "3girl")):
            cats["subject_tags"].append(tag)
        elif low.endswith("_hair") or ("hair" in toks and "eyes" not in toks):
            cats["hair_tags"].append(tag)
        elif low.endswith("_eyes"):
            if any(low.startswith(p) or p in toks for p in _EXPR_EYE_PREFIXES):
                cats["expression_tags"].append(tag)
            else:
                cats["subject_tags"].append(tag)
        elif low in _BUCKET_EXPR or bool(toks & _BUCKET_EXPR):
            cats["expression_tags"].append(tag)
        elif low in _BUCKET_LIGHT or bool(toks & _BUCKET_LIGHT):
            cats["lighting_tags"].append(tag)
        elif low in _BUCKET_POSE or bool(toks & _BUCKET_POSE):
            cats["pose_tags"].append(tag)
        elif low in _BUCKET_BG or bool(toks & _BUCKET_BG):
            cats["background_tags"].append(tag)
        elif low in _BUCKET_OBJ or bool(toks & _BUCKET_OBJ):
            cats["object_tags"].append(tag)
        elif any(s in low for s in (
            "dress", "shirt", "skirt", "uniform", "kimono", "yukata",
            "jacket", "coat", "pants", "socks", "shoes", "boots",
        )):
            cats["clothing_tags"].append(tag)
        elif any(s in low for s in (
            "hat", "glasses", "earring", "necklace", "choker", "bag",
            "hair_ornament", "hair_ribbon", "ribbon",
        )):
            cats["accessory_tags"].append(tag)
        else:
            cats["object_tags"].append(tag)
    return {k: v for k, v in cats.items() if v}


def merge_category_tags(
    *sources: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """Merge category dicts; first occurrence of each tag wins globally."""
    out: dict[str, list[str]] = {k: [] for k in CHRONICLE_CAT_FIELDS}
    seen_global: set[str] = set()
    for src in sources:
        if not src:
            continue
        for key in CHRONICLE_CAT_FIELDS:
            for tag in src.get(key) or []:
                t = str(tag).strip().replace(" ", "_")
                k = t.lower()
                if not t or k in seen_global:
                    continue
                seen_global.add(k)
                out[key].append(t)
    return {k: v for k, v in out.items() if v}



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
        "forbidden": "any location change, any passage of seasons, aging",
    },
    "tens_of_minutes": {
        "must_keep": (
            "hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), same room or immediate outdoor spot, "
            "season, time of day"
        ),
        "may_differ": "outfit (keep it identical UNLESS the story explicitly justifies a change — changing clothes, removing a jacket, a costume switch), pose, expression, minor object placement, slight lighting shift, character's activity and what they are doing, object being interacted with",
        "forbidden": "any location change, any passage of seasons, aging",
    },
    "hours": {
        "must_keep": (
            "hair color and style (IDENTICAL), "
            "physical appearance (IDENTICAL), same building or outdoor location, season"
        ),
        "may_differ": "outfit (keep it identical UNLESS the story explicitly justifies a change — changing clothes, a costume switch), light angle and shadow direction, expression, full pose and activity, props in hand, position within the location, slight fatigue",
        "forbidden": "location change, season change, aging",
    },
    "days": {
        "must_keep": "hair color and style, core facial features, same general area",
        "may_differ": "outfit (may have changed), time of day, emotional state, minor details",
        "forbidden": "season change, significant aging, major location change",
    },
    "months": {
        "must_keep": "hair color, core facial features, recognizable character identity",
        "may_differ": "seasonal outfit, season, slight physical wear, environment",
        "forbidden": "significant aging, era-level fashion shift",
    },
    "years": {
        "must_keep": "recognizable as the same person",
        "may_differ": "outfit style, slight aging, hair style, environment, life stage",
        "forbidden": "complete transformation that makes the person unrecognizable",
    },
    "decades": {
        "must_keep": "any recognizable trait if plausible",
        "may_differ": "everything — age, fashion era, environment, world",
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
})
# Backward-compatible union (any caller wanting "clothing or accessory").
_OUTFIT_TOKENS = _ACCESSORY_TOKENS | _GARMENT_TOKENS


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
_POSE_ACTION_TOKENS = frozenset({
    "standing", "sitting", "kneeling", "lying", "crouching", "squatting",
    "leaning", "reaching", "pointing", "walking", "running", "jumping",
    "falling", "flying", "swimming", "holding", "gripping", "clenched",
    "touching", "grabbing", "hugging", "kissing", "carrying", "lifting",
    "pushing", "pulling", "clapping", "waving", "bowing", "turning",
    "bending", "stretching", "spread", "outstretched", "raised", "crossed",
    "folded", "closed", "open", "covering", "hiding", "looking",
    "smiling", "laughing", "crying", "screaming", "shouting",
    "sleeping", "eating", "drinking", "reading", "writing", "playing",
    "dancing", "singing", "praying", "fighting",
    "arm", "arms", "hand", "hands", "leg", "legs", "knee", "foot", "feet",
    "head", "face", "mouth", "eye", "eyes",
    "pose",
})
_FRAMING_TAGS = frozenset({
    "from_side", "from_above", "from_below", "from_behind", "from_front",
    "close-up", "close_up", "upper_body", "cowboy_shot", "full_body",
    "wide_shot", "dutch_angle", "straight-on", "portrait", "looking_at_viewer",
    "looking_away", "looking_back", "looking_down", "looking_up",
    "profile", "three_quarter_view",
})


def base_pose_tags(wd14_tags: list[str], *, limit: int = 10) -> list[str]:
    """Return the pose/action/framing subset of the base image's wd14 tags.

    Used as a hard lock on the base_axis visual plan (see
    build_visual_examination_prompt): whatever pose the actual base image
    shows must be the pose the base_axis rendering reproduces.
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw in wd14_tags:
        t = raw.strip().lower().replace(" ", "_")
        if not t or t in seen:
            continue
        toks = set(t.split("_"))
        if t in _FRAMING_TAGS or toks & _POSE_ACTION_TOKENS:
            result.append(t)
            seen.add(t)
            if len(result) >= limit:
                break
    return result


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


# Mirror of api.ai._SUBJECT_ANCHOR_TAGS (kept local so this module stays
# import-free / unit-testable) — update both together.
_SUBJECT_ANCHORS = frozenset({
    "1girl", "1boy", "2girls", "2boys", "3girls", "4girls", "6+girls",
    "solo", "couple", "multiple_girls", "multiple_boys", "multiple girls",
})


def inject_identity_tags(tag_line: str, identity: list[str]) -> str:
    """Insert identity tags after the last subject-anchor tag, dedup (ci)."""
    parts = [t.strip() for t in tag_line.split(",") if t.strip()]
    existing = {p.lower() for p in parts}
    new: list[str] = []
    for tag in identity:
        key = tag.lower()
        if key not in existing:
            new.append(tag)
            existing.add(key)
    if not new:
        return tag_line
    cut = max(
        (i + 1 for i, p in enumerate(parts) if p.lower() in _SUBJECT_ANCHORS),
        default=0,
    ) or len(parts)
    return ", ".join(parts[:cut] + new + parts[cut:])


def merge_chronicle_axis_tags(
    *,
    focal: list[str],
    search_tags: list[str],
    lock_tags: list[str],
) -> str:
    """Non-base Chronicle axis tag line: focal + WD14 search, then identity lock.

    Deliberately omits the base image's full WD14 / must-scene tags so past and
    present acts are not forced into the base setting (e.g. train interior).
    Only hair colour, eye colour, and accessories from identity_lock_tags propagate.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for t in [*focal, *search_tags]:
        tag = str(t).strip().replace(" ", "_")
        k = tag.lower()
        if tag and k not in seen:
            seen.add(k)
            merged.append(tag)
    return inject_identity_tags(", ".join(merged), lock_tags)


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
    """One-line register guidance for a chosen emotion. Empty/unknown → ''.

    Biases the OVERALL mood/lighting/colour/atmosphere toward the emotion while
    each act still keeps its own dominant emotion (an overarching tone, not a
    per-act override).
    """
    desc = _EMOTION_REGISTER.get((emotion or "").strip().lower())
    if not desc:
        return ""
    if locale == "ja":
        return (
            f"\n感情レジスタ: 全体のムード・照明・色・雰囲気を「{desc}」へ寄せること。"
            "各幕固有の支配的感情は保ったまま、上位のトーンとして通底させる。\n"
        )
    return (
        f"\nEMOTIONAL REGISTER: bias the overall mood, lighting, colour and "
        f"atmosphere toward {desc}. Keep each act's own dominant emotion, but let "
        "this register run through all of them as the overarching tone.\n"
    )


def build_visual_examination_prompt(
    *,
    story_text: str,
    axis: str,
    base_axis: str,
    time_scale: str = "years",
    character_desc: str = "",
    emotion: str = "",
    locale: str = "en",
    base_pose_tags: list[str] | None = None,
    user_topic: str = "",
    axis_slot: dict | None = None,
) -> str:
    """Stage 3a: decide the shot BEFORE writing the Visual Script.

    Forces a deliberate, multi-angle staging decision for one act and returns it
    as JSON. The point is `focal_action_tags`: concrete danbooru pose/action tags
    so the downstream image prompt (and the tag-driven image model) is never left
    with a motionless upright figure. Non-base axes also inherit the scale's
    must-keep/forbidden constraints and must pick a camera different from the base.

    For the base_axis the pose is LOCKED to the base image's WD14 pose tags so
    the base rendering stays coherent with the actual thumbnail the user picked.
    """
    span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
    rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale=locale
    )
    if axis != base_axis:
        # Compute this axis's elapsed distance/direction from the base.
        idx_b = AXES.index(base_axis) if base_axis in AXES else 1
        idx_a = AXES.index(axis)
        steps = abs(idx_a - idx_b)
        forward = idx_a > idx_b
        one, two = _ELAPSED_UNIT.get(time_scale, _ELAPSED_UNIT["years"])
        phrase = one if steps == 1 else two
        dir_word = "LATER" if forward else "EARLIER"
        constraint = (
            f"This [{axis.upper()}] moment is {phrase} {dir_word} than the base "
            f"([{base_axis.upper()}], t = 0). Base span: {span}. "
            f"MUST keep: {rules['must_keep']}. MAY change: "
            f"{rules['may_differ']}. FORBIDDEN: {rules['forbidden']}. Choose a "
            "camera/framing clearly DIFFERENT from a plain front view of the base.\n"
        )
    else:
        pose_lock = ""
        if base_pose_tags:
            pose_lock = (
                "🔒 BASE-AXIS POSE LOCK — this is the base image itself. Your "
                "`focal_action_tags` MUST include the WD14 pose/action tags below "
                "verbatim (add complementary tags if useful, but never contradict "
                "them). The camera angle must match the base image's framing.\n"
                f"Base pose/action tags: {', '.join(base_pose_tags)}\n"
            )
        constraint = pose_lock
    intent_line = (
        f'\nUSER INTENT: this chronicle fulfils "{user_topic.strip()}". Stage this '
        "shot so the intent is legible in the frame (especially the FUTURE act).\n"
        if user_topic.strip()
        else ""
    )
    char_line = f"CHARACTER (appearance only):\n{character_desc}\n\n" if character_desc else ""
    slot_line = ""
    if axis_slot:
        slot_line = (
            f"TIME ANCHOR for this act (on-screen fact — stage THIS):\n"
            f"  {axis_slot.get('label', '')}: {axis_slot.get('activity', '')} "
            f"@ {axis_slot.get('place', '')} ({axis_slot.get('feeling', '')})\n\n"
        )
    return (
        "You are a storyboard director planning ONE shot before it is drawn.\n"
        "Read the act below and DECIDE, from multiple angles, exactly how the "
        "character is posed and framed so the image expresses the story rather "
        "than showing someone standing still.\n\n"
        "EXPRESSION: name the face/mood that matches this act (one concrete "
        "danbooru expression — smile, blush, tears, serious, pout, nervous…). "
        "A person without a readable expression fails this stage.\n\n"
        f"{elapsed_header}\n"
        f"{char_line}"
        f"{slot_line}"
        f"ACT ([{axis.upper()}]):\n{story_text}\n\n"
        f"{constraint}"
        f"{intent_line}"
        "Decide the SINGLE most story-expressive physical action and stage it. "
        "`focal_action_tags` MUST be concrete danbooru pose/action tags (e.g. "
        "reaching, outstretched_arm, leaning_forward, looking_back, kneeling, "
        "gripping, covering_face, holding, clenched_hand) — NEVER just 'standing' "
        "or 'sitting' with nothing else. Pick a camera angle that dramatizes it.\n"
        f"{_emotion_guidance_line(emotion, locale)}"
        "Answer with JSON only, no markdown fences:\n"
        '{"shot": "<close-up|upper_body|cowboy_shot|full_body|wide_shot>", '
        '"camera_angle": "<from_side|from_above|from_below|from_behind|dutch_angle|straight-on>", '
        '"focal_action_tags": ["tag_1", "tag_2", ...], '
        '"expression_tag": "<one danbooru face/mood tag — smile, blush, tears, serious…>", '
        '"gesture_prose": "<one vivid sentence naming the exact gesture and weight>", '
        '"lighting": "<direction + quality>", "palette": "<colour palette>", '
        '"props": ["prop_1", ...], "mood": "<one phrase>"}'
    )


def parse_visual_plan_json(raw: str) -> dict:
    """Parse a Stage 3a visual plan. Missing/broken → {} (axis prompt still works)."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {}
    def _s(key: str) -> str:
        return str(data.get(key) or "").strip()
    def _l(key: str) -> list[str]:
        v = data.get(key)
        if not isinstance(v, list):
            return []
        return [str(t).strip().replace(" ", "_") for t in v if str(t).strip()]
    # `props` is the canonical field; older records used `key_props`.
    props = _l("props") or _l("key_props")
    plan = {
        "shot": _s("shot"),
        "camera_angle": _s("camera_angle"),
        "focal_action_tags": _l("focal_action_tags"),
        "expression_tag": _s("expression_tag"),
        "gesture_prose": _s("gesture_prose"),
        "lighting": _s("lighting"),
        "palette": _s("palette"),
        "props": props,
        "mood": _s("mood"),
    }
    return plan if any(plan.values()) else {}


def _visual_plan_block(plan: dict | None) -> str:
    """Render a Stage 3a visual plan as a grounding block for build_axis_prompt."""
    if not plan:
        return ""
    action = ", ".join(plan.get("focal_action_tags") or [])
    props = ", ".join(plan.get("props") or plan.get("key_props") or [])
    lines = ["\n[LOCKED SHOT PLAN — realise THIS exactly; put the pose tags near the FRONT of the positive prompt]"]
    if action:
        lines.append(f"Focal action (danbooru pose tags, MANDATORY): {action}")
    if plan.get("gesture_prose"):
        lines.append(f"Gesture: {plan['gesture_prose']}")
    shot_bits = ", ".join(b for b in (plan.get("shot"), plan.get("camera_angle")) if b)
    if shot_bits:
        lines.append(f"Camera/framing (include as tags): {shot_bits}")
    if plan.get("lighting"):
        lines.append(f"Lighting: {plan['lighting']}")
    if plan.get("palette"):
        lines.append(f"Palette: {plan['palette']}")
    if props:
        lines.append(f"Props: {props}")
    if plan.get("mood"):
        lines.append(f"Mood: {plan['mood']}")
    return "\n".join(lines) + "\n"


def _axis_context_blocks(
    *,
    story_text: str,
    character_tags: list[str],
    character_desc: str,
    wd14_context: str,
    time_scale: str,
    axis: str,
    base_axis: str,
    title: str,
    overall: str,
    all_stories: dict[str, str] | None,
    axis_tags: list[str] | None,
    visual_plan: dict | None,
    emotion: str,
    user_topic: str,
) -> dict[str, str]:
    """Shared preamble blocks for the axis prompt builders (single-pass + 2-pass).

    Split out so `build_axis_prompt`, `build_axis_tags_prompt` and
    `build_axis_prose_prompt` all frame the story, chronicle, temporal
    constraints and identity anchors identically.
    """
    if all_stories:
        chronicle_ctx = (
            "FULL CHRONICLE CONTEXT:\n"
            "This image prompt depicts ONE scene from a three-act chronicle.\n\n"
            f"Title: {title}\n"
            f"Overall arc: {overall}\n\n"
            "The three acts (read these to understand the full journey):\n"
            f"  [PAST]:    {all_stories.get('past', '')}\n"
            f"  [PRESENT] ← base image: {all_stories.get('present', '')}\n"
            f"  [FUTURE]:  {all_stories.get('future', '')}\n\n"
            f"You are now generating the image prompt for: [{axis.upper()}]\n"
            f"The base image captures the [{base_axis.upper()}] act.\n\n"
        )
    else:
        chronicle_ctx = ""

    intent_ctx = (
        f'\nUSER INTENT: this chronicle was written to fulfil "{user_topic.strip()}". '
        "Keep this shot faithful to that intent — especially in the FUTURE act "
        "(if the topic names an ending, the FUTURE image must depict that ending).\n"
        if user_topic.strip()
        else ""
    )

    if character_tags:
        identity_src = "[visual tags] " + ", ".join(character_tags)
    else:
        identity_src = "Character description:\n" + character_desc

    if axis != base_axis:
        span = TIME_SCALES.get(time_scale, TIME_SCALES["years"])
        idx_b = AXES.index(base_axis) if base_axis in AXES else 1
        idx_a = AXES.index(axis)
        steps = abs(idx_a - idx_b)
        forward = idx_a > idx_b
        one, two = _ELAPSED_UNIT.get(time_scale, _ELAPSED_UNIT["years"])
        phrase = one if steps == 1 else two
        dir_word = "LATER" if forward else "EARLIER"
        rules = _SCALE_VISUAL_RULES.get(time_scale, _SCALE_VISUAL_RULES["years"])
        temporal_block = (
            f"\n⚠️ TEMPORAL CONSTRAINT — ABSOLUTE ⚠️\n"
            f"This [{axis.upper()}] scene opens a new volume {phrase} {dir_word} "
            f"than the base ([{base_axis.upper()}], t = 0). Base span: {span}.\n"
            f'TIME SCALE: "{time_scale}"\n\n'
            f"Visual elements that MUST be IDENTICAL to the base image:\n"
            f"  {rules['must_keep']}\n"
            f"Visual elements that MAY differ:\n"
            f"  {rules['may_differ']}\n"
            f"STRICTLY FORBIDDEN in this image prompt:\n"
            f"  {rules['forbidden']}\n\n"
            "Do NOT generate anything in the positive prompt that violates the FORBIDDEN list.\n"
            "COMPOSITION: choose a camera setup clearly DIFFERENT from the base "
            "image. Pick the shot that best dramatizes this act from: close-up, "
            "upper_body, cowboy_shot, full_body, wide_shot, from_above, "
            "from_below, from_behind, from_side, dutch_angle — and include it "
            "as danbooru tags.\n"
        )
    else:
        temporal_block = ""

    if axis != base_axis:
        wd14_block = ""
    elif wd14_context:
        wd14_block = (
            "\n[WD14 tags of the base image — the base_axis image MUST match "
            "these tags (pose, scene, mood). Reproduce them faithfully in the "
            "positive prompt; the story text is secondary for this axis]\n"
            f"{wd14_context}\n"
        )
    else:
        wd14_block = ""

    if axis_tags:
        common_block = (
            "\n[Danbooru tags inferred from THIS act's story — weave these into the "
            "positive prompt to describe this scene richly and specifically]\n"
            f"{', '.join(axis_tags)}\n"
        )
    else:
        common_block = ""

    return {
        "chronicle_ctx": chronicle_ctx,
        "intent_ctx": intent_ctx,
        "identity_src": identity_src,
        "temporal_block": temporal_block,
        "wd14_block": wd14_block,
        "common_block": common_block,
        "plan_block": _visual_plan_block(visual_plan),
        "emotion_block": _emotion_guidance_line(emotion),
    }


def build_axis_prompt(
    *,
    story_text: str,
    character_tags: list[str],
    character_desc: str,
    prompt_style: str,
    wd14_context: str = "",
    time_scale: str = "years",
    axis: str = "present",
    base_axis: str = "present",
    title: str = "",
    overall: str = "",
    all_stories: dict[str, str] | None = None,
    axis_tags: list[str] | None = None,
    visual_plan: dict | None = None,
    emotion: str = "",
    user_topic: str = "",
) -> str:
    """LLM prompt producing POSITIVE:/NEGATIVE: sections for one axis.

    Character identity keywords are condensed and placed at the head of the
    positive prompt so the same character survives across all three images.

    WD14 dependency is deliberately reduced: for non-base axes the base image's
    WD14 tags are omitted entirely (they describe a different moment), and the
    story text becomes the primary content source. axis_tags — ~50 danbooru
    tags inferred from THIS act's own story — enrich the positive prompt.

    Kept as a single-pass fallback; the 2-pass pipeline (build_axis_tags_prompt
    → build_axis_prose_prompt) is preferred for lightweight VLMs.
    """
    ctx = _axis_context_blocks(
        story_text=story_text,
        character_tags=character_tags,
        character_desc=character_desc,
        wd14_context=wd14_context,
        time_scale=time_scale,
        axis=axis,
        base_axis=base_axis,
        title=title,
        overall=overall,
        all_stories=all_stories,
        axis_tags=axis_tags,
        visual_plan=visual_plan,
        emotion=emotion,
        user_topic=user_topic,
    )
    chronicle_ctx = ctx["chronicle_ctx"]
    intent_ctx = ctx["intent_ctx"]
    identity_src = ctx["identity_src"]
    temporal_block = ctx["temporal_block"]
    wd14_block = ctx["wd14_block"]
    common_block = ctx["common_block"]
    plan_block = ctx["plan_block"]
    emotion_block = ctx["emotion_block"]

    if prompt_style == "natural":
        format_rule = (
            "POSITIVE is the 5-paragraph Visual Script prose described above. "
            "Open paragraph 1 with the condensed identity keywords as inline tags."
        )
    elif prompt_style == "danbooru":
        format_rule = (
            "POSITIVE is a single comma-separated danbooru tag list (30-50 tags): "
            "the condensed identity keywords FIRST, then action, environment, "
            "detail and mood tags for this scene. No prose."
        )
    else:  # danbooru+natural
        format_rule = (
            "POSITIVE is two parts separated by a blank line:\n"
            "(a) a comma-separated danbooru tag line (30-50 tags) — condensed "
            "identity keywords FIRST, then action/environment/detail/mood tags;\n"
            "(b) the 5-paragraph Visual Script prose described above."
        )

    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale="en"
    )

    return (
        "You are an expert image generation prompt engineer.\n"
        "Turn ONE act of a story into an image prompt.\n\n"
        f"{elapsed_header}\n"
        f"You are now generating the image prompt for: [{axis.upper()}]\n\n"
        f"{chronicle_ctx}"
        f"{intent_ctx}"
        f"SCENE (this act of the story):\n{story_text}\n"
        f"{temporal_block}\n"
        f"{identity_src}\n"
        f"{wd14_block}"
        f"{common_block}"
        f"{plan_block}"
        f"{emotion_block}"
        "\n[Visual Script format]\n"
        f"{_VISUAL_SCRIPT_GUIDE}\n\n"
        "Rules:\n"
        "- Condense the character's PHYSICAL identity (hair, eyes, notable "
        "features, signature outfit elements) into a SHORT keyword list and put "
        "it at the very START of the positive prompt, so the same character is "
        "recognizable in every act.\n"
        f"- {format_rule}\n"
        "- Depict THIS act's scene grounded in the story text: place, lighting, mood.\n"
        "- For ACTION/POSE: the story names the dramatic moment; YOU invent the most\n"
        "  visually striking physical instantiation. Choose the exact gesture, the\n"
        "  weight distribution, the prop interaction — be specific, not generic.\n"
        "  The pose must be emotionally distinct from the base image's pose.\n"
        "- The action MUST appear as concrete danbooru action/pose tags near the\n"
        "  FRONT of the positive prompt (right after the identity keywords), because\n"
        "  the image model reads pose from tags. A bare 'standing'/'sitting' with no\n"
        "  action tag is forbidden unless the act is truly motionless.\n"
        "- NEGATIVE lists only things to avoid (artifacts, wrong elements for "
        "this scene). Short comma-separated tags. Unless this act is deliberately "
        "still, include static-pose tags here (standing, static_pose, "
        "expressionless, stiff, arms_at_sides) so the figure is not left just "
        "standing.\n\n"
        "Output format (exactly these two labels, nothing else):\n"
        "POSITIVE:\n<the positive prompt>\n\n"
        "NEGATIVE:\n<the negative prompt>"
    )


# ── Stage 3b split: 2-pass axis prompt for lightweight VLMs ──────────────────
#
# Refine's natural style (backend/app/api/ai.py) splits tag generation from
# prose so a small VLM only has to solve one problem at a time. Chronicle
# mirrors that pattern: build_axis_tags_prompt asks for a JSON tag payload,
# then build_axis_prose_prompt writes the 5-paragraph Visual Script on top
# of that tag line. This produces denser prompts than one-shot output on the
# same model at the same total token budget.


def build_axis_tags_prompt(
    *,
    story_text: str,
    character_tags: list[str],
    character_desc: str,
    wd14_context: str = "",
    time_scale: str = "years",
    axis: str = "present",
    base_axis: str = "present",
    title: str = "",
    overall: str = "",
    all_stories: dict[str, str] | None = None,
    axis_tags: list[str] | None = None,
    visual_plan: dict | None = None,
    emotion: str = "",
    user_topic: str = "",
) -> str:
    """Pass 1 of Stage 3b: JSON tag payload for ONE act.

    Mirrors Invoke's spirit schema so the same downstream parsing / injection
    helpers apply. The VLM only has to enumerate tags in this call — no prose,
    no POSITIVE/NEGATIVE structure — so a small model has budget for density.
    """
    ctx = _axis_context_blocks(
        story_text=story_text,
        character_tags=character_tags,
        character_desc=character_desc,
        wd14_context=wd14_context,
        time_scale=time_scale,
        axis=axis,
        base_axis=base_axis,
        title=title,
        overall=overall,
        all_stories=all_stories,
        axis_tags=axis_tags,
        visual_plan=visual_plan,
        emotion=emotion,
        user_topic=user_topic,
    )
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale="en"
    )
    return (
        "You are a danbooru-tag expert building the tag payload for ONE act "
        "of a three-act chronicle. Do NOT write prose. Enumerate tags only.\n\n"
        f"{elapsed_header}\n"
        f"Target act: [{axis.upper()}]\n\n"
        f"{ctx['chronicle_ctx']}"
        f"{ctx['intent_ctx']}"
        f"SCENE (this act of the story):\n{story_text}\n"
        f"{ctx['temporal_block']}\n"
        f"{ctx['identity_src']}\n"
        f"{ctx['wd14_block']}"
        f"{ctx['common_block']}"
        f"{ctx['plan_block']}"
        f"{ctx['emotion_block']}"
        "\n[MANDATORY RULES]\n"
        "- SUBJECT-FIRST: `danbooru_tags` MUST open with the subject-count tag "
        "(1girl / 1boy / solo / 2girls / …). No other tag before it.\n"
        "- ACTION-ANCHOR: `pose_tags` MUST contain at least 3 concrete danbooru "
        "action/pose tags translated from the story verbs — reaching, "
        "outstretched_arm, leaning_forward, looking_back, kneeling, gripping, "
        "clenched_hand, holding, covering_face, touching, fingertips, "
        "dynamic_pose. NEVER just `standing` / `sitting` with nothing else.\n"
        "- EXPRESSION: when the subject is a person (1girl / 1boy / solo / …), "
        "`expression_tags` MUST contain ≥1 concrete face/mood tag drawn from "
        "the act's emotion (smile, blush, tears, pout, serious, nervous, "
        "expressionless, open_mouth, …). A person with no expression tag fails "
        "— mood cannot read from pose alone.\n"
        "- SCENE RICHNESS: the image must feel *lived-in*, not a blank backdrop. "
        "`lighting_tags` ≥2 (e.g. sunset, rim_light, backlight, lens_flare, "
        "warm_light, long_shadow). `background_tags` ≥3 specific place cues "
        "(street, shop, storefront, stadium, crowd, streetlamp, mountain…). "
        "`object_tags` ≥2 tangible props (bicycle, scarf, mug, medal, confetti…). "
        "Prefer wind/motion/atmosphere when the story supports it "
        "(fluttering_scarf, confetti, streamers, dust).\n"
        "- EXPLICIT TAG: every noun the scene needs (hair color, eye color, "
        "notable feature, clothing, prop, background, light source) MUST appear "
        "as a real danbooru tag — never a euphemism or paraphrase.\n"
        "- MIN 50 TAGS on `danbooru_tags`. Under-count = failed prompt.\n"
        "- NO quality meta-tags anywhere (masterpiece / best_quality / highres / "
        "4k / 8k / worst_quality / low_quality etc.).\n"
        "- Every tag echoed under `danbooru_tags` should also appear in ONE of "
        "the category buckets below.\n\n"
        "Output JSON ONLY. No markdown fences, no commentary. Schema:\n"
        '{\n'
        '  "danbooru_tags": "<subject-count tag first, then 50+ comma-separated tags>",\n'
        '  "subject_tags": "<subject count, character count, viewer relation>",\n'
        '  "hair_tags": "<hair color, length, style>",\n'
        '  "expression_tags": "<REQUIRED if person: ≥1 face/mood tag — smile, blush, tears, serious…>",\n'
        '  "clothing_tags": "<outfit, garments, fabric>",\n'
        '  "accessory_tags": "<jewelry, hats, glasses, small items>",\n'
        '  "pose_tags": "<>= 3 concrete pose/action tags, no bare standing>",\n'
        '  "background_tags": "<location, setting, weather, time of day>",\n'
        '  "object_tags": "<props, held items, environment objects>",\n'
        '  "lighting_tags": "<light direction, quality, palette>",\n'
        '  "negative_supplement": "<comma-separated artifacts to avoid>"\n'
        "}"
    )


def parse_axis_tags_json(raw: str) -> tuple[str, dict[str, list[str]], str]:
    """Parse Pass 1 output → (tag_line, categories, negative_supplement).

    Returns:
        tag_line: the merged comma-separated danbooru tag string.
        categories: {"subject_tags": [...], "hair_tags": [...], ...}
        negative_supplement: comma-separated artifacts to avoid.

    Missing / broken → ("", {}, ""). Tags are underscored + deduplicated
    (case-insensitive), preserving first-seen order.
    """
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return "", {}, ""

    def _split(s: str) -> list[str]:
        return [t.strip().replace(" ", "_") for t in str(s).split(",") if t.strip()]

    # Merge danbooru_tags with each category bucket to salvage anything the
    # model put in the buckets but forgot on the main line.
    seen: set[str] = set()
    merged: list[str] = []

    def _add(tags: list[str]) -> None:
        for tag in tags:
            k = tag.lower()
            if k and k not in seen:
                seen.add(k)
                merged.append(tag)

    _add(_split(data.get("danbooru_tags") or ""))
    categories: dict[str, list[str]] = {}
    for key in (
        "subject_tags", "hair_tags", "expression_tags", "clothing_tags",
        "accessory_tags", "pose_tags", "background_tags", "object_tags",
        "lighting_tags",
    ):
        cat_tags = _split(data.get(key) or "")
        categories[key] = cat_tags
        _add(cat_tags)

    tag_line = ", ".join(merged)
    negative_supplement = str(data.get("negative_supplement") or "").strip()
    return tag_line, categories, negative_supplement


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

# Face / mood tags required whenever a person is on-screen (emotion must read).
# Mirrors tag_categories.json axis_emotion — kept local so generator stays
# import-free. Expressionless/neutral/serious count: a blank face is still a
# chosen expression; a *missing* expression is the failure mode.
_EXPRESSION_TAGS = frozenset({
    "smile", "smiling", "grin", "laugh", "laughing", "happy", "joyful",
    "sad", "crying", "tears", "teary_eyes", "teary-eyed", "watery_eyes", "sobbing",
    "expressionless", "neutral", "calm", "serious", "stoic",
    "angry", "annoyed", "frustrated", "glaring", "frowning", "frown",
    "blush", "blushing", "red_cheeks",
    "surprised", "shocked", "open_mouth", "gasp",
    "closed_mouth", "half_open_mouth",
    "closed_eyes", "half-closed_eyes", "winking", "one_eye_closed",
    "pout", "pouting", "smirk", "wink", "worried", "embarrassed", "shy",
    "nervous", "confused", "flustered", "sleepy", "dazed",
    "excited", "cheerful", "content", "satisfied", "reluctant",
    "defeated", "hopeless", "terrified", "disgusted", "contemptuous",
    "smug", "lonely", "melancholy", "nostalgic", "pensive", "thoughtful",
    "ecstatic", "horrified", "panicked", "relieved", "focused",
})
_EXPRESSION_TOKENS = frozenset({
    "smile", "smiling", "grin", "laugh", "tear", "tears", "teary", "sob",
    "blush", "frown", "pout", "smirk", "wink", "angry", "sad", "shy",
    "nervous", "worried", "expressionless", "serious", "stoic", "gasp",
    "smug", "melancholy", "nostalgic", "pensive", "flustered", "embarrassed",
    "scared", "terrified", "panicked", "relieved", "focused", "cheerful",
    "joyful", "lonely", "annoyed", "glaring",
})

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


def _tag_has_expression(parts: list[str]) -> bool:
    """True if the tag line contains at least one face/mood expression tag."""
    for raw in parts:
        t = raw.strip().lower().replace(" ", "_")
        if not t:
            continue
        if t in _EXPRESSION_TAGS:
            return True
        toks = set(t.replace("-", "_").split("_"))
        if toks & _EXPRESSION_TOKENS:
            return True
    return False


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


def build_axis_prose_prompt(
    *,
    story_text: str,
    tag_line: str,
    character_tags: list[str],
    character_desc: str,
    prompt_style: str,
    wd14_context: str = "",
    time_scale: str = "years",
    axis: str = "present",
    base_axis: str = "present",
    title: str = "",
    overall: str = "",
    all_stories: dict[str, str] | None = None,
    axis_tags: list[str] | None = None,
    visual_plan: dict | None = None,
    emotion: str = "",
    user_topic: str = "",
    negative_supplement: str = "",
    draft_grounding: str = "",
) -> str:
    """Pass 2 of Stage 3b: 5-paragraph Visual Script over Pass 1's tag line.

    prompt_style="natural"          → POSITIVE = prose only (no leading tag line)
    prompt_style="danbooru+natural" → POSITIVE = tag_line, blank line, prose
    prompt_style="danbooru"         → not intended — call Pass 1 only and skip Pass 2.

    When ``draft_grounding`` is set (Phase B), the image model's draft WD14 is
    treated as visual fact — lighting/atmosphere/props must match it.
    """
    ctx = _axis_context_blocks(
        story_text=story_text,
        character_tags=character_tags,
        character_desc=character_desc,
        wd14_context=wd14_context,
        time_scale=time_scale,
        axis=axis,
        base_axis=base_axis,
        title=title,
        overall=overall,
        all_stories=all_stories,
        axis_tags=axis_tags,
        visual_plan=visual_plan,
        emotion=emotion,
        user_topic=user_topic,
    )
    elapsed_header = _elapsed_time_header(
        base_axis=base_axis, time_scale=time_scale, locale="en"
    )
    tag_block = (
        "\n[PASS 1 DANBOORU TAG LINE — authoritative visual vocabulary]\n"
        "Keep ALL internal reasoning in danbooru tags. Do not replace this line "
        "with free prose invents. The prose below must REUSE these tags inline "
        "in ASCII parentheses and must never contradict them.\n"
        f"{tag_line}\n"
    )
    grounding_block = ""
    if (draft_grounding or "").strip():
        grounding_block = (
            f"\n{draft_grounding.strip()}\n"
            "CRITICAL: The draft grounding above is stronger than story-text "
            "guesses for lighting, atmosphere, background, props and pose. "
            "Write the Visual Script so those draft facts are VISIBLE in the "
            "final image. Do not invent conflicting weather, time-of-day, or "
            "setting. Identity locks (hair/eye colour) still win over draft.\n"
        )
    if prompt_style == "natural":
        format_rule = (
            "POSITIVE is: (1) the 5-paragraph Visual Script prose, then "
            "(2) the labeled *_TAGS category lines. No leading flat tag line "
            "— tags live inline in the prose and in the category footer."
        )
    else:  # danbooru+natural (default)
        format_rule = (
            "POSITIVE has THREE parts separated by blank lines:\n"
            "(a) the PASS 1 DANBOORU TAG LINE above verbatim (do not re-order, "
            "do not drop tags);\n"
            "(b) the 5-paragraph Visual Script prose;\n"
            "(c) the labeled *_TAGS category lines (Refine Visual Spec)."
        )
    neg_hint = (
        f"\nSuggested negatives from Pass 1 (merge with your own): {negative_supplement}\n"
        if negative_supplement.strip()
        else ""
    )
    draft_rule = (
        "- Prefer DRAFT GROUNDING (image-model facts) over thin text guesses "
        "for light, place, props and motion when present.\n"
        if grounding_block else ""
    )
    return (
        "You are an expert image generation prompt engineer.\n"
        "Work in DANBOORU TAGS first (Pass 1 tag line is ground truth), then "
        "render a Visual Script the image model can read easily.\n\n"
        f"{elapsed_header}\n"
        f"You are now generating the image prompt for: [{axis.upper()}]\n\n"
        f"{ctx['chronicle_ctx']}"
        f"{ctx['intent_ctx']}"
        f"SCENE (this act of the story):\n{story_text}\n"
        f"{ctx['temporal_block']}\n"
        f"{ctx['identity_src']}\n"
        f"{ctx['wd14_block']}"
        f"{ctx['common_block']}"
        f"{ctx['plan_block']}"
        f"{ctx['emotion_block']}"
        f"{tag_block}"
        f"{grounding_block}"
        "\n[Visual Script format]\n"
        f"{_VISUAL_SCRIPT_GUIDE}\n\n"
        f"{_VS_LABELED_TAG_FOOTER}\n\n"
        "Rules:\n"
        f"- {format_rule}\n"
        f"{draft_rule}"
        "- Depict THIS act's scene grounded in the story text: place, lighting, mood.\n"
        "- The action MUST appear as concrete danbooru action/pose tags near the "
        "FRONT of the positive prompt; a bare 'standing'/'sitting' with no action "
        "tag is forbidden unless the act is truly motionless.\n"
        "- Category lines must mostly subset the PASS 1 tag line "
        "(plus a few inline enrichments already used in the prose).\n"
        "- NEGATIVE lists only things to avoid (artifacts, wrong elements for "
        "this scene). Short comma-separated tags. Unless this act is deliberately "
        "still, include static-pose tags here (standing, static_pose, "
        "expressionless, stiff, arms_at_sides) so the figure is not left just "
        f"standing.{neg_hint}\n"
        "Output format (exactly these two labels, nothing else):\n"
        "POSITIVE:\n<the positive prompt with tag line / prose / category lines>\n\n"
        "NEGATIVE:\n<the negative prompt>"
    )


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
_DRAFT_SKIP_SCALES = frozenset({"minutes", "tens_of_minutes"})
_DRAFT_AUTO_DIVERGENCE = 0.25
# Backward-compat alias used by older tests / callers.
_DRAFT_AUTO_SCALES = frozenset({"hours", "days", "months", "years", "decades"})

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


def should_use_draft_refine(
    *,
    mode: str | bool | None,
    time_scale: str,
    divergence: float,
    workflow_name: str = "",
    manual_mode: bool = False,
) -> bool:
    """Whether Phase B draft→WD14→rebuild should run for this expand.

    ``mode``:
      - True / \"on\"  → always (when a workflow is set and not manual)
      - False / \"off\" → never
      - None / \"auto\" → any non-micro time scale, or divergence ≥ 0.25
    """
    if manual_mode or not (workflow_name or "").strip():
        return False
    if isinstance(mode, bool):
        return mode
    key = str(mode or "auto").strip().lower()
    if key in ("off", "false", "0", "no"):
        return False
    if key in ("on", "true", "1", "yes"):
        return True
    # auto — prefer drafting; only skip micro scales unless divergence is high
    scale = (time_scale or "").strip().lower()
    try:
        div = float(divergence)
    except (TypeError, ValueError):
        div = 0.0
    if div >= _DRAFT_AUTO_DIVERGENCE:
        return True
    return scale not in _DRAFT_SKIP_SCALES


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
    return out


def build_draft_grounding_block(draft_tags: list[str], *, locale: str = "en") -> str:
    """Instruction block for Pass-2 rebuild: treat draft WD14 as visual fact."""
    tags = [
        str(t).strip().replace(" ", "_")
        for t in (draft_tags or [])
        if str(t).strip()
    ][:40]
    if not tags:
        return ""
    joined = ", ".join(tags)
    if locale == "ja":
        return (
            "\n[下書き接地 — 画像モデルが既に描いた事実]\n"
            f"低解像度下書きの WD14: {joined}\n"
            "これらはテキスト推測ではなく、画像モデルの表現そのもの。"
            "照明・雰囲気・背景・小道具・ポーズはこれに合わせて書き直し、"
            "矛盾するタグは捨てること。同一性（髪色・瞳色）だけは保持。\n"
        )
    return (
        "\n[DRAFT GROUNDING — facts the IMAGE MODEL already painted]\n"
        f"Low-res draft WD14: {joined}\n"
        "These are not text guesses — they are the image model's own expression. "
        "Rewrite lighting, atmosphere, background, props and pose to MATCH them; "
        "drop contradicting invented tags. Keep identity (hair/eye colour) only.\n"
    )


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

def build_translation_to_english_prompt(
    title: str, overall: str, stories: dict[str, str]
) -> str:
    """Translate a user-language chronicle into English (for Stage 3 prompting).

    Used when the story was generated in Japanese: the image-prompt stage always
    works in English, so the acts are translated before Stage 3. Skipped when
    the locale is already English.
    """
    return (
        "Translate this chronicle into natural, fluent English.\n"
        "Keep the tone and imagery. Do not add or remove content.\n\n"
        f"TITLE: {title}\n\n"
        f"OVERALL: {overall}\n\n"
        f"PAST: {stories.get('past', '')}\n\n"
        f"PRESENT: {stories.get('present', '')}\n\n"
        f"FUTURE: {stories.get('future', '')}\n\n"
        "Answer with JSON only, using exactly these keys:\n"
        '{"title": "...", "overall": "...", "past": "...", '
        '"present": "...", "future": "..."}'
    )


def parse_english_translation_json(raw: str) -> dict[str, str]:
    """Parse the to-English translation output. Missing/broken → '' per key."""
    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        return {k: "" for k in SECTIONS}
    return {k: str(data.get(k) or "").strip() for k in SECTIONS}

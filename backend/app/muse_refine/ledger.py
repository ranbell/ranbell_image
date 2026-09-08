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


def normalize_patch(raw: dict[str, Any] | None) -> dict[str, str]:
    """Keep only known keys; coerce to stripped strings."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key in (*LEDGER_KEYS, *DROP_KEYS):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None:
            continue
        text = str(val).strip()
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


def scrub_patch(
    patch: dict[str, str] | None,
    ledger: dict[str, str] | None,
    *,
    allow_clear: set[str] | frozenset[str] | None = None,
) -> dict[str, str]:
    """Drop accidental empty clears so long chats keep clothes / mood / look.

    Empty string still clears when ``allow_clear`` names the key (explicit reset).
    """
    raw = normalize_patch(patch)
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
        if key == "expression" and "expression" not in dir_keys and scene_moved:
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
PARTNER_KEYS: tuple[str, ...] = ("wearing_b", "beat_b")


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
            "person: never write wearing_b or beat_b."
        )
    b = (name_b or "the partner").strip()
    return f"CAST: two in frame — {a} and {b}. wearing_b / beat_b are {b}'s."


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

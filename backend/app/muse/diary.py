"""Reading her diary back out of whatever the model actually returned.

The diary is the worst-shaped payload in the app: two languages, several
paragraphs each, in a voice full of 「」 and ellipses and line breaks. Asked for
that as JSON, a local model breaks the JSON — an unescaped newline inside a
string, a quote in the middle of a line, or a tail that simply stops when the
context window runs out. The old reader was one `re.search(r"\\{.*\\}")` and one
`json.loads`, and when it failed it stored the raw response as her diary, so the
Showrunner opened the panel and read a JSON object back.

So the contract is labelled blocks now — the same idiom the table read uses for
SAY / TAGS / SCENE — because a label on its own line cannot be broken by the
prose that follows it. JSON is still *accepted*: models that ignore the new
contract, and every entry written before it, have to keep working.

Order of attempts: blocks, then `ai.json_util.parse_json_object` (which already
knows how to strip fences, patch commas and close a truncated tail), then a
field-level salvage that reads through a string that was never terminated.
Whatever comes out, `looks_like_json` gets the last word: scaffolding must never
reach the page.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..ai.json_util import parse_json_object

logger = logging.getLogger(__name__)

FIELDS: tuple[str, ...] = ("summary_ja", "summary_en", "content_ja", "content_en")

# A label owns everything up to the next label, so the body can hold anything.
_LABEL_RE = re.compile(
    r"^[ \t]*[#*\-]*[ \t]*(SUMMARY_JA|SUMMARY_EN|CONTENT_JA|CONTENT_EN)[ \t]*[:：]?[ \t]*$"
    r"|^[ \t]*[#*\-]*[ \t]*(SUMMARY_JA|SUMMARY_EN|CONTENT_JA|CONTENT_EN)[ \t]*[:：][ \t]*(.*)$",
    re.IGNORECASE,
)
# Reads a JSON string value that may never have been closed — the truncated
# tail is exactly the case where the whole object is unparseable.
_SALVAGE_TMPL = r'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)'
_KEY_LINE_RE = re.compile(r'^\s*[{\[]?\s*"[a-z_]+"\s*:\s*"?', re.IGNORECASE)
_BRACE_LINE_RE = re.compile(r"^\s*[{}\[\],]+\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def parse_diary(raw: str) -> dict[str, str]:
    """Her four fields, by whichever route survives. Missing keys are absent."""
    text = (raw or "").strip()
    if not text:
        return {}
    blocks = _parse_blocks(text)
    if blocks:
        return blocks
    try:
        data = parse_json_object(text)
    except Exception:
        logger.debug("[muse.diary] not JSON either; salvaging fields", exc_info=True)
    else:
        out = _from_mapping(data)
        if out:
            return out
    return _salvage(text)


def _parse_blocks(text: str) -> dict[str, str]:
    """Split on labels sitting at the start of a line."""
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            current = (m.group(1) or m.group(2) or "").lower()
            bodies.setdefault(current, [])
            inline = (m.group(3) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies[current].append(line)
    out = {k: "\n".join(v).strip() for k, v in bodies.items() if k in FIELDS}
    return {k: v for k, v in out.items() if v}


def _from_mapping(data: dict[str, Any]) -> dict[str, str]:
    """Pull the four fields out of a parsed object, plain keys included."""
    out: dict[str, str] = {}
    for key in FIELDS:
        value = data.get(key)
        if value is None:
            # `summary` / `content` are what the older prompt asked for, and are
            # still the Japanese side.
            value = data.get(key.rsplit("_", 1)[0]) if key.endswith("_ja") else None
        text = str(value or "").strip()
        if text:
            out[key] = text
    return out


def _salvage(text: str) -> dict[str, str]:
    """Last resort: read each field off broken JSON, unterminated tail included."""
    out: dict[str, str] = {}
    for key in FIELDS:
        m = re.search(_SALVAGE_TMPL.format(key=key), text, re.DOTALL)
        if not m:
            continue
        value = _unescape(m.group(1)).strip()
        if value:
            out[key] = value
    if out:
        return out
    # No recognisable keys at all — she ignored the contract and simply wrote.
    # That is still her diary; it is only the scaffolding we refuse to keep.
    body = strip_scaffolding(text)
    if not body or looks_like_json(body):
        return {}
    return {"content_ja": body}


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}

# An emoji outside the BMP is written as two `\uXXXX` escapes (a UTF-16
# surrogate pair). Converting each half separately below produces two lone
# surrogate codepoints, which break UTF-8 encoding downstream — this is the
# "emoji shows as ?" bug. Merge pairs into one codepoint before the
# single-escape pass ever sees them.
_SURROGATE_PAIR_RE = re.compile(
    r"\\u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})"
)


def _join_surrogate_pair(m: re.Match[str]) -> str:
    high, low = int(m.group(1), 16), int(m.group(2), 16)
    return chr(0x10000 + (high - 0xD800) * 0x400 + (low - 0xDC00))


def _unescape(value: str) -> str:
    """Undo JSON string escapes only.

    Deliberately not `unicode_escape`: that codec goes through latin-1 and
    either raises or mangles every Japanese character in the body.
    """
    value = _SURROGATE_PAIR_RE.sub(_join_surrogate_pair, value)

    def _sub(m: re.Match[str]) -> str:
        token = m.group(1)
        if token.startswith("u"):
            try:
                code = int(token[1:], 16)
            except ValueError:
                return m.group(0)
            if 0xD800 <= code <= 0xDFFF:
                # A lone/truncated surrogate — the pair above already caught
                # every complete one, so this is a cut-off tail. Drop it
                # rather than emit a codepoint that cannot be UTF-8 encoded.
                return ""
            return chr(code)
        return _ESCAPES.get(token, m.group(0))

    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", _sub, value)


def strip_scaffolding(text: str) -> str:
    """Drop lone braces, `"key":` prefixes and bare labels from half-wrapped prose."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if _BRACE_LINE_RE.match(line) or _FENCE_RE.match(line):
            continue
        # A label with nothing under it is the contract, not the diary.
        if _LABEL_RE.match(line) and not (_LABEL_RE.match(line).group(3) or "").strip():
            continue
        stripped, keyed = _KEY_LINE_RE.subn("", line)
        if keyed:
            # Only a line that was carrying a key can be carrying that key's
            # closing quote. Prose is left exactly as she wrote it.
            stripped = re.sub(r'",?\s*$', "", stripped)
        lines.append(stripped)
    return _unescape("\n".join(lines)).strip()


def looks_like_json(text: str) -> bool:
    """True for anything still carrying the shape of the contract.

    The one rule this module exists to enforce: text that answers yes here is
    never saved as her writing.
    """
    body = (text or "").strip()
    if not body:
        return False
    if body.startswith("{") or body.startswith("```"):
        return True
    return any(f'"{key}"' in body for key in FIELDS)


def normalize(parsed: dict[str, str], *, fallback_ja: str = "", fallback_en: str = "") -> dict[str, str]:
    """The four fields as they get stored, with a cut-off English side repaired.

    A generation that runs out of room stops mid-word, and the English half is
    what it stops in — the contract puts Japanese first for that reason. The
    fragment that started all this ended "…since the shoot ended. A", so the
    repair is to end her where she last finished a sentence; only if nothing
    whole is left does the field go empty and the panel fall back to the
    Japanese she did finish.
    """
    content_ja = _clean(parsed.get("content_ja")) or _clean(parsed.get("content"))
    content_en = _repair_tail(_clean(parsed.get("content_en")))
    summary_ja = _clean(parsed.get("summary_ja")) or _clean(parsed.get("summary"))
    summary_en = _repair_tail(_clean(parsed.get("summary_en")))

    if _stub(content_en, content_ja):
        logger.info("[muse.diary] dropping a truncated English body (%d chars)", len(content_en))
        content_en = ""
    if _stub(summary_en, summary_ja):
        summary_en = ""

    return {
        "summary_ja": summary_ja or fallback_ja,
        "summary_en": summary_en or fallback_en,
        "content_ja": content_ja,
        "content_en": content_en,
    }


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if not text or looks_like_json(text):
        return ""
    return text


_SENTENCE_END = tuple('.!?…。！？」』"\')')


def _repair_tail(text: str) -> str:
    """End her where she last finished a sentence.

    Length is the wrong signal for a cut-off — the fragment that started this
    ("…since the shoot ended. A") is longer than the Japanese beside it. Where
    the text *stops* is the tell. A line with no sentence punctuation anywhere
    is left alone: that is what a one-line summary looks like, not a cut.
    """
    body = (text or "").rstrip()
    if not body or body.endswith(_SENTENCE_END):
        return body
    cut = max(body.rfind(ch) for ch in _SENTENCE_END)
    if cut < 0:
        return body
    return body[: cut + 1].rstrip()


def _stub(en: str, ja: str) -> bool:
    """What is left is too small to be a translation of a finished entry."""
    if not en or not ja:
        return not en
    return len(en) < 40 and len(en) < len(ja) * 0.25


# ── 引用が書き写しでずれていないか（見るだけ・直さない） ────────────────────

_QUOTE_RE = re.compile(r"「([^」]{12,})」")
_BRACKETS = frozenset("「」『』\"'“”")
# 引用の中の引用は『』に変わる。これは正しい書き換えで、ずれではない。
_DRIFT_COVER = 0.80
_DRIFT_SPAN = 4


def quote_drift(text: str, sources: list[str]) -> list[dict[str, Any]]:
    """日記が写した「」が、元の発言から1〜2文字ずれていないか。

    実物（2026-08-21）:

        対話ログ  「きっと、マイクの前で用意した言葉じゃなくて、もっと、こう…」
        日記      「きっと、マインの前で用意した言葉じゃなくて、もっと、こう…」

    **同じ日記の前半では「マイク」と書けている。** 語を知らないのではなく、
    長い引用を書き写している最中にずれる。92本の測定で、崩れたのは全て
    逐語の引用の中。自分の言葉で書いている所では一度も崩れなかった。

    ## 直さないこと

    彼女は逆に、こちらの誤字を直すことがある —— ログ「手を降るシーン」が
    日記では「手を振るシーン」になっていた。機械で原文に戻すと、それを潰す。
    **見つけたと言うだけにして、直すかどうかは人が決める。**

    ## 測って決めた三つ

    - **全体の一致率では測れない。** 日記は台詞だけを切り取るので、前置きの
      ついたログ行と比べると一致率が 0.71 に落ちる。実際それで取り逃した。
      見るのは*引用のどれだけがログで説明できるか*（被覆率 0.80 以上）
    - 報告するのは**両側4字以下の置換だけ**。前置きの削除は書き写しの
      ずれではないので黙る
    - **括弧だけの違いは黙る。** `「`→`『` は正しい入れ子

    片仮名の1文字違いを語単位で探す網も試したが、`ポーズ` を（ログにたまたま
    `ポーン` があるだけで）誤りと数え、`マフィ`（2文字ずれ）と `マいて`
    （平仮名混じり）を取り逃した。**引用というまとまりで見るほうが正しい。**
    """
    import difflib

    # A line she is copying can be the whole turn or just the 「」 inside it —
    # 「バイバイ。って手を降るシーンにしよう」 was said inside a longer sentence.
    # Both shapes go in the haystack.
    src: list[str] = []
    for line in sources or []:
        line = str(line or "").strip()
        if len(line) >= 12:
            src.append(line)
        src += [q for q in _QUOTE_RE.findall(line) if len(q) >= 12]
    if not src or not text:
        return []
    out: list[dict[str, Any]] = []
    for m in _QUOTE_RE.finditer(text):
        quote = m.group(1)
        best, cover = "", 0.0
        for line in src:
            sm = difflib.SequenceMatcher(None, line, quote, autojunk=False)
            got = sum(b.size for b in sm.get_matching_blocks()) / max(1, len(quote))
            if got > cover:
                best, cover = line, got
        if not (_DRIFT_COVER <= cover < 1.0):
            continue
        drift: list[str] = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None, best, quote, autojunk=False).get_opcodes():
            if tag != "replace" or (i2 - i1) > _DRIFT_SPAN or (j2 - j1) > _DRIFT_SPAN:
                continue
            was, now = best[i1:i2], quote[j1:j2]
            if set(was) <= _BRACKETS and set(now) <= _BRACKETS:
                continue
            drift.append(f"{was}→{now}")
        if drift:
            out.append({"quote": quote[:60], "cover": round(cover, 3),
                        "drift": drift})
    return out


def log_quote_drift(text: str, sources: list[str], *, character_id: str = "") -> int:
    """Say so in the log when a copied line came out changed. Returns the count."""
    try:
        hits = quote_drift(text, sources)
    except Exception:
        logger.debug("[muse.diary] quote drift check failed", exc_info=True)
        return 0
    for h in hits:
        logger.warning(
            "[muse.diary] quote drift %s cover=%.3f char=%s 「%s」",
            h["drift"], h["cover"], character_id[:8], h["quote"],
        )
    return len(hits)


# **日本語の欄に紛れる別の文字体系。** 実測（2026-08-23・15本）で4本に出た:
#
#     「両手で必니까 顎まで隠しても」        ハングル
#     「心臓が跳猛的に跳ねて」              中国語の言い回し
#
# 本人の弁 —— 日本語と英語を同じ応答で書かせているので、日本語の生成中に
# 「学習データ上その概念に強い他言語のトークン」が浮上する。漢字は中国語と
# 共有しているぶん、特に起きやすい。
#
# **捕まえられるのは、字で分かるものだけ。** 「跳猛的」は一字ずつ見れば
# どれも日本語の漢字なので、文字種では判定できない。そこは指示文の側
# （欄ごとに言語を閉じる）に任せて、ここでは無理をしない。
_STRAY_SCRIPT_RE = re.compile(
    "["
    "ᄀ-ᇿ㄰-㆏가-힯"   # ハングル
    "Ѐ-ӿ"                             # キリル
    "฀-๿"                             # タイ
    "ऀ-ॿ"                             # デーヴァナーガリー
    "؀-ۿ"                             # アラビア
    "]"
)


def stray_script(text: str) -> str:
    """日本語の本文に紛れた、日本語で使わない文字。無ければ ""。

    見つけた字を**そのまま**返す（ログと再生成の判断に使う）。文章は直さない
    —— 直すのは書き手の仕事で、こちらは書き直してもらうだけ。
    """
    hits = _STRAY_SCRIPT_RE.findall(str(text or ""))
    return "".join(dict.fromkeys(hits))

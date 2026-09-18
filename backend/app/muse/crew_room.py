"""班（スタジオ撮り）—— classic の行脚を、Refine の台帳の上に載せる。（2026-09-11）

総監督「スタジオ撮り（複数の撮影スタッフのモード）を Muse refine に取り込みたい」
「classic からそのまま移植したあと、磨きましょう」「18役職を全部残す」。

## そのまま持ってきたもの

- **席の条文**（`crew.system_prompt_for`）—— 18役職・30人・6プリセット。一字も変えない
- **回し方**（`_craft_pass`）—— 席順に回し、席の間にやじ役と横やり役を挟む
- **やじの選び方**（`_pick_banter_reactor` / `_pick_extra_heckler`）と
  `banter_mode`（off / light / full）
- **開幕は三席**（`OPENING_SEQUENCE` = 衣装 → 撮影 → 主演）。先に一枚撮ってから全班

## 替えたのは書き込み先だけ

classic では席は talk-only で、書くのは Scripter 一人だった
（`_apply_turn` の `talk_only = uses_notebook(session)`）。Refine では
**その Scripter の席に `writer.write_patch` が座る。**

    classic   席が喋る → Scripter が手帖を書く → weave が絵を組む
    Refine    席が喋る → writer が台帳を書く   → assemble が絵を組む

だから手帖（notebook）は持ち込まない。席が出す `CRAFT:` 行は台帳に直接は
書かず、**writer への材料**として渡す。どの席がどの欄の持ち主かは
`crew.CRAFT_SLOTS` がもう決めてあるので、それを台帳の欄名に読み替えるだけ。

## 席順ではなく、欄ごとに回る（2026-09-14）

総監督「同じ台帳のメンバーを束ねて1つのセッションにして、結論として一つの台帳を
だしたらいい。そうすると衝突は回避できる。あとその時に今の台帳が何かを告知して
から、今はこうなっててどう変えるのかという話をしたら」。

    これまで   席順に12回呼ぶ。各席が自分の欄に「足す」
    いま       欄ごとに9回呼ぶ（`field_groups`）。同じ欄の席は**一度に喋り**、
               告知（`field_header`）を読んでから**欄ぜんぶの値を一つ**決める

実機で `look` が12語になり `amber_theme` と `magenta_theme` が同居した
（`6dc11d0e`）。席が二人いる欄では取り合いが、一人の欄でも言い換えの堆積が
起きていた —— **毎ターン足すことしかできず、全体を言い直す機会が無かった**から。

台の実測（同じ材料・やじ off・n=3・`private/muse/crew_lab/corner_check.py`）:

    席ごと   12回  一周 111.4s   総監督で開く 14%   look 4.3語  SAY 96字
    欄ごと    9回  一周  72.5s   総監督で開く  5%   look 2.3語  SAY 77字

**告知は両方の腕に入れて測った**ので、差は束ねたぶんだけ。払ったものは
SAY が 96 → 77字（一回の返事に何人ぶんも書くため）。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from . import chain, crew, events, identity
from . import debug as debug_mod
from . import ledger as ledger_mod
from .ctx import refine_num_ctx

logger = logging.getLogger(__name__)

#: 席の CRAFT slot → 台帳の欄。`crew.CRAFT_SLOTS` が席→slot を持っているので、
#: ここは slot→欄 の一段だけ。**一つの欄を複数の席が見てよい**（演出と振付は
#: どちらも体、レイアウトと撮影はどちらも構図）—— classic もそうだった。
SLOT_FIELD: dict[str, str] = {
    "BODY": "beat",
    "SHAPE": "frame",
    "OPTICS": "frame",
    "PROPS": "bg",
    "CLOTH": "wearing",
    "LIGHT": "light",
    "FACE": "expression",
    "AIR": "atmosphere",
    "COLOUR": "look",
    "RENDER": "look",
    "FINISH": "look",
}

#: 開幕の三席と、その順番。classic の `OPENING_ROLES` / `OPENING_SEQUENCE` と同じ。
#: **衣装が先に着せ、撮影が着せた姿を切り、主演が最後に演じる** —— 開幕で服に
#: 持ち主が居ないと、空の台帳に最初に書く席が服まで書いてしまう（classic の実測）。
OPENING_SEQUENCE: tuple[str, ...] = ("wardrobe", "lens", "actress")

#: ノートのターンでペンを持たない席。classic の `NOTE_MUTED` と同じ。
#: 監査と密度上げの仕事は、手帖が正本になった時点で別の段に移っている。
MUTED: frozenset[str] = frozenset(
    getattr(crew, "NOTE_MUTED", None) or getattr(crew, "BANTER_ONLY", ())
)

_CRAFT_LINE_RE = re.compile(r"(?im)^CRAFT\s*:\s*(.+?)\s*$")

#: 席の出力書式。**classic の条文の末尾を上書きする。**（2026-09-11）
#:
#: `crew.system_prompt_for` は職能文（「TAGS と SCENE は書くな、君の CRAFT slot は
#: LIGHT だ」）のすぐ後ろに classic の `OUTPUT`（SAY / TAGS / SCENE の三ブロック）
#: を足す。条文の中で矛盾していて、**最後に読んだ側が勝つ** —— 実機で衣装の席が
#: TAGS を35語並べて返してきた（`CRAFT:` は一行も無し）。
#:
#: 手帖が正本の studio では、この矛盾は classic 側の別の経路で解けていた。
#: Refine は自分の書式を**いちばん後ろに**足して解く。女優の条文（`REFINE_OUTPUT`）
#: と W撮りの `w_output_block` でもう二度使っている手。
#: **席の出力の形。ここが唯一の形式**（前の形式を打ち消す一行から始まる）。
#:
#: 言語と声の規則は `crew.OUTPUT` にあったが、あちらは classic の三ブロック
#: （SAY/TAGS/SCENE）向けで、席は TAGS も SCENE も書かない。打ち消される側を
#: 毎席 2,812字送っていたので外し、**効いていた二行だけこちらへ引き取った**
#: （2026-09-13）。`crew.OUTPUT` は女優の前置き（一人撮りの正本）で今も現役。
#: **席の口調を保つ段。**（2026-09-13）
#:
#: 総監督「Muse Classic 時代にあった、スタッフ別の口調がなくなって均一化した気が
#: します」。数えたら本当だった —— 席の発言の **46% が「総監督、」で始まり、
#: 42% が同じ4文字で切り出していた**。原因は前日 `crew.OUTPUT` を席から外した
#: とき、その中の SAY の段（魅せる指示）が一緒に落ちたこと。
#:
#: 実測（台・同じ材料・n=2）:
#:
#:     いまの条文    総監督で開く 46%  同じ4字 42%  語の重なり 3.2%  1周 117.5s
#:     この段を戻す   総監督で開く  8%  同じ4字 21%  語の重なり 0.4%  1周 118.3s
#:
#: **時間は変わらない**（+0.7%）。前日の削り（-19%）は損なわない。
SEAT_VOICE = """
YOUR SAY IS ENTERTAINMENT AS MUCH AS CRAFT — captivate the Showrunner.
- Charm first: warmth, playfulness, a little tease, a vivid image in words.
  Make him want to keep reading. Cute is welcome; a bland report is not.
- Open your own way. Do NOT begin the way the last speaker began, and do not
  start with 「総監督」 when the speaker before you already did.
- Still a person with an opinion — react, pile on, then commit.
""".strip()


SEAT_OUTPUT = """
OUTPUT FORMAT — this REPLACES any format above. Two lines, nothing else:

SAY: 1–3 sentences of live table talk in YOUR voice. React to the floor, then
commit ONE concrete thing from your own specialty. No danbooru tags in SAY.
**Never write a TAGS: or SCENE: block — the Scripter owns the shot document.**
- LANGUAGE: these instructions are in English. Speak the session locale —
  by default natural Japanese in your voice (口調どおり). 「総監督」OK.
- Match your VOICE / 口調 / EXAMPLE SAY. Do NOT sound like the other Muses.
  Warmth and a little tease are welcome; a bland report is not. No emoji.

CRAFT: <danbooru tags> | <short prose>
Your slot only. Absolute values — never "darker" / "softer" / "more".
**No field label inside CRAFT.** Not `BEAT:`, not `WEARING:`, not
`ATMOSPHERE:` — the tags alone. The Scripter knows which field is yours.
Omit the whole CRAFT line when your slot should not move this turn.
""".strip()


#: 束ねた回（欄ごとの会議）の出力の形。**一席の `SEAT_OUTPUT` と同じ約束** ——
#: 最後に読んだ形式が勝つので、前置きの末尾に置く。
#:
#: 違いは二つだけ: 席の数だけ `SPEAKER:` + `SAY:` の組を出すことと、
#: **CRAFT は最後に一行だけ**（欄の結論）であること。
GROUP_OUTPUT = """
OUTPUT FORMAT — this REPLACES any format above. Nothing else in the reply:

SPEAKER: <the exact id of speaker 1>
SAY: 1–3 sentences of live table talk in THAT person's voice.
SPEAKER: <the exact id of speaker 2>
SAY: 1–3 sentences — react to speaker 1 by name, then your own craft.
(...one SPEAKER/SAY pair per person at this corner, in the given order)

CRAFT: <danbooru tags> | <short prose>
- LANGUAGE: these instructions are in English. The SAY lines speak the session
  locale — by default natural Japanese, each in that person's 口調. 「総監督」OK.
- **ONE CRAFT line for the whole corner, at the very end.** It is the field's
  WHOLE value as the corner agreed it — not an addition to it, not one line
  per speaker. Absolute values — never "darker" / "softer" / "more".
- **No field label inside CRAFT.** The tags alone.
- Omit the CRAFT line entirely when the field should stay exactly as it reads now.
- Never write a TAGS: or SCENE: block — the Scripter owns the shot document.
""".strip()


#: 班が開いているセッションの印。**総監督が明示的に開けたときだけ立つ。**
TABLE_OPEN = "crew_open"


def has_crew(session: dict[str, Any]) -> bool:
    """班のセッションか。

    **既定値を門にしない。** `inputs.crew_preset` は `ALL_DEFAULTS` から
    `"standard"` が入るので、席の有無で切ると**一人撮りでも16席が回る**。
    `mode` も使えない（実機の117セッションが全部 `duet` で、うち85件は相方すら
    居ない）。だから「総監督が班を開けたか」という一つの印で切る。

    既にある29の Refine セッションには印が無いので、一つも巻き込まれない。
    """
    return bool(session.get(TABLE_OPEN)) and bool(cast_of(session))


def cast_of(session: dict[str, Any]) -> list[str]:
    """この撮影の席順。一職一席、主演と編集は `resolve_crew` が常に足す。"""
    inputs = dict(session.get("inputs") or {})
    ids = [str(i) for i in (inputs.get("crew_ids") or []) if str(i).strip()]
    preset = str(inputs.get("crew_preset") or "").strip()
    if not ids and not preset:
        return []
    try:
        return list(crew.resolve_crew(preset=preset or None, crew_ids=ids or None))
    except Exception:
        logger.exception("[muse] could not resolve the crew")
        return []


def field_of(muse_id: str) -> str:
    """その席が持っている台帳の欄（持たない席は空）。"""
    slot = (getattr(crew, "CRAFT_SLOTS", None) or {}).get(crew.role_of(muse_id) or "")
    return SLOT_FIELD.get(str(slot or ""), "")


def writing_seats(cast: list[str], *, only: tuple[str, ...] = (),
                  without: tuple[str, ...] = ()) -> list[str]:
    """ペンを持つ席を席順で。`plan` は別経路なのでここには出ない。"""
    out: list[str] = []
    for mid in cast:
        role = crew.role_of(mid)
        if not role or role == "plan" or role in MUTED or role in without:
            continue
        if only and role not in only:
            continue
        out.append(mid)
    return out


def opening_seats(cast: list[str]) -> list[str]:
    """開幕の三席を**着付けの順**で（席順ではなく）。"""
    rank = {r: i for i, r in enumerate(OPENING_SEQUENCE)}
    seats = writing_seats(cast, only=OPENING_SEQUENCE)
    return sorted(seats, key=lambda m: rank.get(crew.role_of(m) or "", 99))


#: CRAFT 行の上限。**語の途中では切らない。**（2026-09-18）
CRAFT_MAX = 280


def _clip_craft(clause: str) -> str:
    """長すぎる CRAFT を、**語の切れ目**で止める。

    以前は `clause[:280]` だった。左半分は台帳に入るタグなので、真ん中で切ると
    `silver_sug` のような**半分の語**が欄に着く。実機の欄は最長 167字なので
    まだ踏んでいないが、踏んだときに気づけない壊れ方なので先に直しておく。
    """
    text = str(clause or "").strip()
    if len(text) <= CRAFT_MAX:
        return text
    head = text[:CRAFT_MAX]
    for mark in ("|", ",", " "):
        cut = head.rfind(mark)
        if cut > CRAFT_MAX // 2:
            return head[:cut].strip(" ,|")
    return head.strip(" ,|")


def split_craft(body: str) -> tuple[str, str]:
    """一席の返事を、喋りと CRAFT 行に分ける。classic の `_split_craft_line` と同じ。

    **`SAY:` の札は画面に出さない（2026-09-12）。** 総監督「スタジオ撮りだと
    SAY: が露出する」。席の返事は `SAY: …` で始まるので、そのまま積むと
    吹き出しに札が残る。剥がすのは classic の `identity.sanitize_muse_say`
    ——「欄の名前が漏れたら切る」という仕事を既にしている一本。
    """
    text = str(body or "")
    m = _CRAFT_LINE_RE.search(text)
    if not m:
        return identity.sanitize_muse_say(text, locale="ja"), ""
    clause = str(m.group(1) or "").strip()
    say = _CRAFT_LINE_RE.sub("", text)
    if clause.lower() in ("none", "-", "n/a", "omit", "(omit)"):
        clause = ""
    return identity.sanitize_muse_say(say, locale="ja"), _clip_craft(clause)


def banter_mode(session: dict[str, Any]) -> str:
    """off / light / full。既定は light —— 呼び出し回数の半分はやじなので。"""
    mode = str((session.get("inputs") or {}).get("banter_mode") or "light").strip().lower()
    return mode if mode in ("light", "full", "off") else "light"


def _in_role(cast: list[str], role: str) -> str | None:
    return next((m for m in cast if crew.role_of(m) == role), None)


def pick_reactor(session: dict[str, Any], cast: list[str], *,
                 current: str, previous: str | None, index: int) -> str | None:
    """一席が喋ったあと、誰がやじを入れるか。classic のままの規則。"""
    mode = banter_mode(session)
    if mode == "off":
        return None
    if mode == "light" and crew.role_of(current) != "actress" and index % 2 == 0:
        return None
    # 主演には固定の取り分を渡す —— 前席が総取りすると、18席の撮影で彼女の
    # 台詞が三行しか残らなかった（classic の実測）。
    lead = _in_role(cast, "actress")
    if lead and lead != current and index % 4 == 1:
        return lead
    if previous and previous != current and previous in cast:
        return previous
    for role in ("hook", "actress", "faces", "spine", "beat"):
        mid = _in_role(cast, role)
        if mid and mid != current:
            return mid
    return None


def pick_heckler(session: dict[str, Any], cast: list[str], *,
                 current: str, reactor: str | None, index: int) -> str | None:
    """二人目のやじ。`full` のときだけ（ローカルの Ollama には高くつく）。"""
    if banter_mode(session) != "full":
        return None
    if index % 3 != 2:
        return None
    for role in ("actress", "hook", "faces", "cutout", "propshop"):
        mid = _in_role(cast, role)
        if mid and mid not in (current, reactor):
            return mid
    return None


def seat_name(session: dict[str, Any], muse_id: str) -> str:
    """画面に出す名前。**あだ名（役職）**の形。主演だけは本人の名前。（2026-09-16）

    総監督「Muse同士の会話が混ざる。口調が Muse のものでない」。名札が
    **役職**（`色彩設計`・`撮影`）だけだったのが半分の理由だった ——

        画面      色彩設計 / 撮影 / 演出
        席の口    「一点さん」「すきま」「一秒くん」

    **呼び合う名前が画面に出ていない。** しかも同じ役職に二人いる
    （`palette:itten` と `palette:aku`）ので、顔ぶれを替えても見分けが付かない。
    あだ名を前に出すと、席同士の呼びかけと画面の名札が同じ言葉になる。

    ここは席・やじ・`muse_speaking`・保存行がすべて通る一本なので、直すのはここだけ。
    """
    if crew.role_of(muse_id) == "actress":
        char = session.get("character") or {}
        name = str(char.get("name_ja") or char.get("name") or "").strip()
        if name:
            return name
    m = (getattr(crew, "MUSES", None) or {}).get(crew.resolve_member(muse_id)) or {}
    role = str(m.get("name_ja") or m.get("name") or muse_id).strip()
    nick = str(m.get("nick_ja") or m.get("nick") or "").strip()
    if nick and nick != role:
        return f"{nick}（{role}）"
    return role


#: 班が置いた語の控え（`session[CREW_WORDS][欄] = [語, …]`）。（2026-09-14）
#:
#: **誰の語かを覚えておくためだけの帳面。** 欄の会議は「今はこうなっている、
#: どう変えるか」を決めるので、結論は欄の**全体**になる。そのとき総監督の言葉まで
#: 書き換えてしまっては困るので、班は**自分が置いた語だけ**言い直せる、とする。
#: 印の無い古いセッションは全語を総監督のものとして扱う（消えない側に倒す）。
CREW_WORDS = "crew_words"


def crew_words_of(session: dict[str, Any]) -> dict[str, list[str]]:
    """その欄に、班が置いた語。"""
    raw = session.get(CREW_WORDS) or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(t) for t in (v or [])] for k, v in raw.items()}


def field_groups(seats: list[str]) -> list[tuple[str, list[str]]]:
    """席を**欄ごとに束ねる**。並びは、その欄に最初に座る席の席順。（2026-09-14）

    総監督「同じ台帳のメンバーを束ねて1つのセッションにして、結論として一つの
    台帳をだしたらいい。そうすると衝突は回避できる」。

        standard   beat（演出・振付）／ frame（レイアウト・撮影）／ look（色彩・線画）
                   ＋ bg・wearing・light・expression・atmosphere の各1席
                   → 12席が **9つの会議**になる

    欄を持たない席（主演）は束ねない —— **一人ずつ別の会議**として返す
    （`("", [id])`）。同じ「欄なし」で一緒にすると、話の相手が居ない席同士が
    同じ部屋に入る。
    """
    groups: list[tuple[str, list[str]]] = []
    index: dict[str, int] = {}
    for mid in seats:
        field = field_of(mid)
        if not field:
            groups.append(("", [mid]))
            continue
        if field in index:
            groups[index[field]][1].append(mid)
            continue
        index[field] = len(groups)
        groups.append((field, [mid]))
    return groups


def field_header(field: str, *, ledger: dict[str, str],
                 mine: list[str] | None = None) -> str:
    """**いまの台帳を告知してから、どう変えるかを訊く。**（2026-09-14）

    総監督「今の台帳が何かを告知してから、今はこうなっててどう変えるのかという
    話をしたらいいのでは」。

    実機で `light` は席が一つしかないのに `backlighting` `rim_light` `hard_rim`
    `edge_lighting` と逆光の言い換えが四つ積もっていた。毎ターン「足す」ことしか
    できず、**欄の全体を見て言い直す機会が無かった**から。ここで見せて、
    結論を欄ぜんぶの値として書かせる。
    """
    have = [t.strip() for t in str((ledger or {}).get(field) or "").split(",") if t.strip()]
    crew_said = {t.lower() for t in (mine or [])}
    theirs = [t for t in have if t.lower() not in crew_said]
    lines = [
        f"FIELD `{field}` — THE LEDGER AS IT STANDS",
        f"  now: {', '.join(have) if have else '(empty)'}",
    ]
    if theirs:
        lines.append(
            "  the Showrunner's own words in it: " + ", ".join(theirs)
            + "  ← these stay, whatever you decide"
        )
    lines.append(
        f"Decide what `{field}` READS after this turn — keep it, drop what is "
        "stale, or replace it. Say out loud what you are changing and why, then "
        "write ONE CRAFT line holding the WHOLE field: the absolute value, at "
        f"most {FIELD_CONCLUSION_MAX} tags, no duplicates, no two tags that "
        "fight each other. Omit CRAFT when it should stay exactly as it reads."
    )
    return "\n".join(lines)


def _shot_bits(session: dict[str, Any]) -> list[str]:
    """誰が写っていて、台帳がいまどうなっているか。席にも会議にも同じものを渡す。"""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    partner = session.get("partner_character") or {}
    has_partner = bool(str(partner.get("character_id") or "").strip())
    char = session.get("character") or {}
    return [
        ledger_mod.cast_line(
            partner=has_partner,
            name_a=str(char.get("name_ja") or char.get("name") or ""),
            name_b=str(partner.get("name_ja") or partner.get("name") or ""),
        ),
        "SHOT LEDGER — this is the shot as it stands. Absolute, per field.\n"
        + "\n".join(f"  {k}: {v}" for k, v in
                    ledger_mod.for_model(led, partner=has_partner).items() if v),
    ]


def _floor_bit(floor: list[dict[str, Any]]) -> str:
    """直前の発言。**言葉を借りない**という注意付きで。

    実機の開幕で、撮影の席が衣装の席の一文目をそのまま写した（「西日が差し込む
    なら、光を吸い込むベルベットか…」）。条文にも「Do not restate another Muse's
    phrase」とあるが、直前の発言を見せる以上、ここでもう一度言う。
    """
    if not floor:
        return ""
    return (
        "THE FLOOR SO FAR — react to it, then add the one thing nobody has "
        "named yet. **Do not reuse their words, images or metaphors.** If "
        "the last speakers already reached for your idea, that idea is "
        "finished; say the part of the picture still missing.\n"
        + "\n".join(f"  {f['name']}: {str(f['say'])[:160]}" for f in floor[-3:])
    )


def group_prompt(session: dict[str, Any], seats: list[str], *, field: str,
                 director_line: str, floor: list[dict[str, Any]]) -> str:
    """欄の会議に渡す本文。**告知が先、結論は一つ。**（2026-09-14）"""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    bits = _shot_bits(session)
    if field:
        bits.append(field_header(
            field, ledger=led, mine=crew_words_of(session).get(field)))
    if (bit := _floor_bit(floor)):
        bits.append(bit)
    bits.append(f"SHOWRUNNER:\n{director_line.strip()}")
    return "\n\n".join(bits)


def seat_prompt(session: dict[str, Any], muse_id: str, *,
                director_line: str, floor: list[dict[str, Any]]) -> str:
    """一席に渡す本文。**台帳が正本**で、席は自分の欄だけを磨く。"""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    field = field_of(muse_id)
    slot = (getattr(crew, "CRAFT_SLOTS", None) or {}).get(crew.role_of(muse_id) or "")

    bits = _shot_bits(session)
    if slot and field:
        bits.append(
            f"YOUR SLOT: {slot} — it lands in the ledger field `{field}`.\n"
            + field_header(field, ledger=led, mine=crew_words_of(session).get(field))
        )
    if (bit := _floor_bit(floor)):
        bits.append(bit)
    bits.append(f"SHOWRUNNER:\n{director_line.strip()}")
    return "\n\n".join(bits)


def stream_id(session: dict[str, Any], muse_id: str) -> str:
    """流し込みの宛先 id。**主演だけはキャストした本人の id で出す。**（2026-09-18）

    総監督「Muse が喋ったときのサムネイルが抜けている場合がある」。

    画面は「その言葉が主演のものか」を **`muse_id` が本人の `character_id` か**で
    見ている（`MusePanel.vue` の `liveIsLead`）。班の中の彼女は `actress:cast`
    という席の id で流れていたので、**やじを入れた回だけ顔が消えていた** ——
    同じ人が喋っているのに、言葉の出どころによって名札が変わる。

    ここで本人の id に揃える。席としての彼女も、やじの彼女も、本人の段も、
    画面から見れば同じ一人になる。
    """
    if crew.role_of(muse_id) == "actress":
        cid = str((session.get("character") or {}).get("character_id") or "").strip()
        if cid:
            return cid
    return muse_id


def _stream_to(session: dict[str, Any], muse_id: str):
    """席の台詞を流す口。**`SAY:` の中だけ**通る（`_say_only`）。

    総監督「streaming 表示しないので待たされる感覚がかなり大きい」。18席が
    順に喋るあいだ無言だと、1分以上なにも起きないように見える。女優の段で
    やっているのと同じ仕掛けを席にも回す。
    """
    try:
        from . import shared as muse_shared

        return muse_shared._token_publisher(
            str(session.get("session_id") or ""), stream_id(session, muse_id),
        )
    except Exception:
        logger.debug("[muse] seat token publisher unavailable", exc_info=True)
        return None


def _watch_the_window(session: dict[str, Any], who: str):
    """枠で切られた回を記録に残す合図。**黙って短い返事にしない。**（2026-09-18）

    総監督「prompt のオーバフローで文字が切れる場合があるようです」。枠
    （`num_ctx`）は前置きと出力の合計なので、前置きが長い回は書いている途中で
    打ち切られる。Ollama は `done_reason: "length"` と言っているので、それを
    `/debug` に残して**あとから数えられる**ようにする。
    """
    def _note(done: dict[str, Any]) -> None:
        if str(done.get("reason") or "") != "length":
            return
        debug_mod.note(
            session, "cut_by_the_window",
            detail=f"{who}: 前置き {done.get('prompt_tokens')}tok "
                   f"＋ 出力 {done.get('eval_tokens')}tok で枠に当たった",
        )
        logger.warning("[muse] %s was cut by the window: %s", who, done)
    return _note


async def _seat_turn(ollama, session: dict[str, Any], muse_id: str, *,
                     model: str, prompt: str) -> str:
    """一席ぶんの呼び出し。**絵は渡さない**（板を見せるのは女優の段の仕事）。"""
    sid = str(session.get("session_id") or "")
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": stream_id(session, muse_id),
        "name": seat_name(session, muse_id),
    })
    return await chain._call(
        ollama,
        system=crew.system_prompt_for(
            muse_id, character=session.get("character") or {},
            base_style=str((session.get("inputs") or {}).get("look") or ""),
            seed=sid,
        ) + "\n\n" + SEAT_VOICE + "\n\n" + SEAT_OUTPUT,
        prompt=prompt,
        model=model,
        images=None,
        num_ctx=refine_num_ctx(session),
        think=False,
        on_token=_stream_to(session, muse_id),
        on_done=_watch_the_window(session, f"席 {crew.role_of(muse_id) or muse_id}"),
    )


_SPEAKER_RE = re.compile(r"(?im)^[ \t>*_#-]*SPEAKER\s*:\s*(.+?)\s*$")

#: 行頭に付く飾り。模型は `**SPEAKER: …**` や `- SPEAKER:` と書くことがある。
_DECOR = " \t*_#>-"


def _match_speaker(token: str, seats: list[str],
                   used: list[str]) -> tuple[str, bool]:
    """`SPEAKER: …` の右側を、この会議の席に当てる。`(席, 当たったか)`。

    模型は id をそのまま書くこともあれば、番号（`SPEAKER: 2`）でも、あだ名でも
    役職名でも書く（`SPEAKER: 一点` / `SPEAKER: 色彩設計`）。だから**日本語の
    名札も見る**。

    **当たらなければ、まだ喋っていない席の先頭**に落とす —— 会議は席順に喋る
    約束なので、それでほぼ合う。`used` を渡し忘れると全員が一人目に積まれるので、
    当たったかどうかを返して呼び元が記録できるようにしてある（2026-09-16 の
    流し込みの取り違えは、まさにここを空で呼んでいたのが原因）。
    """
    raw = str(token or "").strip().strip("`*_ 「」【】")
    low = raw.lower()
    for mid in seats:
        if mid.lower() == low or mid.lower().split(":")[0] == low:
            return mid, True
    if (digits := re.findall(r"\d+", low)):
        i = int(digits[0]) - 1
        if 0 <= i < len(seats):
            return seats[i], True
    for mid in seats:
        member = (getattr(crew, "MUSES", None) or {}).get(crew.resolve_member(mid)) or {}
        labels = [crew.role_of(mid), member.get("nick"), member.get("nick_ja"),
                  member.get("name_ja"), member.get("name")]
        for label in labels:
            text = str(label or "").strip().lower()
            if text and text in low:
                return mid, True
    for mid in seats:
        if mid not in used:
            return mid, False
    return (seats[0] if seats else ""), False


def split_packed(raw: str, seats: list[str]) -> tuple[list[tuple[str, str]], str]:
    """束ねた回の返事を、**席ごとの台詞**と**欄の結論一つ**に分ける。（2026-09-14）

    `CRAFT` は最後の一行を採る —— 条文では一行だけだが、席ごとに書いてきたときは
    **閉めの一行が会議の結論**なので、そこを信じる。
    """
    text = str(raw or "")
    crafts = list(_CRAFT_LINE_RE.finditer(text))
    craft = str(crafts[-1].group(1) or "").strip() if crafts else ""
    body = _CRAFT_LINE_RE.sub("", text)

    parts = _SPEAKER_RE.split(body)
    rows: list[tuple[str, str]] = []
    if len(parts) >= 3:
        used: list[str] = []
        for i in range(1, len(parts) - 1, 2):
            mid, _hit = _match_speaker(parts[i], seats, used)
            say = identity.sanitize_muse_say(parts[i + 1], locale="ja")
            if not say.strip():
                continue
            used.append(mid)
            rows.append((mid, say))
    else:
        # **形式を守らなかった回も落とさない。** 丸ごと先頭の席の発言にする。
        say = identity.sanitize_muse_say(body, locale="ja")
        if say.strip() and seats:
            rows = [(seats[0], say)]
    return rows, craft


def _packed_stream(session: dict[str, Any], seats: list[str]):
    """束ねた回を、**喋っている席の吹き出しへ振り分けながら**流す。（2026-09-14）

    一席ずつ呼んでいたときは `_stream_to` が宛先を一つ持てばよかった。会議は
    一度の返事に何人ぶんも入っているので、`SPEAKER:` の行で宛先を切り替える。
    **ここが無いと、束ねた回だけ画面が無言になる**（総監督「待たされる感覚が
    かなり大きい」）。

    行頭の数文字だけ溜める —— `shared._say_only` が欄名を伏せるのと同じ手で、
    行の途中では溜めない（溜めると一文が書き上がるまで画面が止まる）。
    """
    sid = str(session.get("session_id") or "")
    pubs = {mid: _stream_to(session, mid) for mid in seats}
    word = "speaker:"
    st: dict[str, Any] = {"gate": None, "bol": True, "hold": "", "id": None,
                          "used": []}

    def _emit(text: str) -> None:
        gate = st["gate"]
        if gate is None or not text:
            return
        try:
            gate(text)
        except Exception:
            logger.debug("[muse] packed stream emit failed", exc_info=True)

    def _switch(token: str) -> None:
        # **`used` を持って渡す（2026-09-16）。** 空で呼んでいたので、名前が
        # 当たらないと毎回 `seats[0]` に落ち、**二人目の言葉が一人目の吹き出しに
        # 積まれていた**（総監督「Muse同士の会話が混ざる」）。
        mid, hit = _match_speaker(token, seats, st["used"])
        if mid:
            st["used"].append(mid)
            st["gate"] = pubs.get(mid)
            events.publish(sid, {
                "type": "muse_speaking", "muse_id": stream_id(session, mid),
                "name": seat_name(session, mid),
            })
        if not hit:
            # 黙って間違えない —— 当たらなかった名札は記録に残す。
            debug_mod.note(session, "corner_speaker_miss",
                           detail=f"{token.strip()[:40]!r} → {mid}")

    def _feed(text: str) -> None:
        for ch in str(text or ""):
            if st["id"] is not None:          # `SPEAKER: …` の行を読んでいる
                if ch == "\n":
                    _switch(st["id"])
                    st["id"] = None
                    st["bol"] = True
                else:
                    st["id"] += ch
                continue
            if st["bol"]:
                cand = st["hold"] + ch
                # **飾りを許す（2026-09-16）。** `**SPEAKER: …**` と書かれると
                # 行頭が `*` で始まるので欄名に育たず、ラベルごと前の席の吹き出しへ
                # 流れていた。`_SAY_OPEN_RE` が昔から許しているのと同じ飾り。
                bare = cand.strip(_DECOR).lower()
                if (not bare and len(cand) <= len(_DECOR)) or (bare and word.startswith(bare)):
                    st["hold"] = cand
                    if bare == word:
                        st["hold"], st["id"] = "", ""
                    continue
                _emit(st["hold"])
                st["hold"] = ""
                st["bol"] = False
            _emit(ch)
            if ch == "\n":
                st["bol"] = True
                st["hold"] = ""

    return _feed


async def _group_turn(ollama, session: dict[str, Any], seats: list[str], *,
                      field: str, model: str, prompt: str) -> str:
    """欄の会議を一度で呼ぶ。**絵は渡さない**（板を見せるのは女優の段の仕事）。"""
    sid = str(session.get("session_id") or "")
    if seats:
        events.publish(sid, {
            "type": "muse_speaking", "muse_id": stream_id(session, seats[0]),
            "name": seat_name(session, seats[0]),
        })
    inputs = session.get("inputs") or {}
    return await chain._call(
        ollama,
        system=crew.field_table_prompt(
            seats, field=field,
            base_style=str(inputs.get("look") or ""),
            locale=str(inputs.get("locale") or "ja"),
            preset_id=str(inputs.get("crew_preset") or ""),
            seed=sid,
        ) + "\n\n" + SEAT_VOICE + "\n\n" + GROUP_OUTPUT,
        prompt=prompt,
        model=model,
        images=None,
        num_ctx=refine_num_ctx(session),
        think=False,
        on_token=_packed_stream(session, seats),
        on_done=_watch_the_window(session, f"会議 {field}"),
    )


async def _banter_turn(ollama, session: dict[str, Any], muse_id: str, *,
                       model: str, about_name: str, about_text: str) -> str:
    """やじ一言。短く、craft は書かせない。"""
    events.publish(str(session.get("session_id") or ""), {
        "type": "muse_speaking", "muse_id": stream_id(session, muse_id),
        "name": seat_name(session, muse_id),
    })
    return await chain.run_banter(
        ollama, muse_id=muse_id,
        user_prompt=(
            f"{about_name} がいま言ったこと:\n{about_text[:240]}\n\n"
            "一言だけ返して。茶々でも、乗っかるでも、軽く押し返すでもいい。"
            "**タグも CRAFT も書かない。** 一文か二文。"
        ),
        model=model,
        num_ctx=refine_num_ctx(session),
        character=session.get("character") or {},
        on_token=_stream_to(session, muse_id),
    )


async def run_table(db, ollama, session: dict[str, Any], *,
                    director_line: str, opening: bool = False) -> list[dict[str, Any]]:
    """席を順に回す。**台帳は書かない** —— 集めたものを writer に渡す。

    classic の `_craft_pass` と同じ形:

        席が喋る → やじ役を選ぶ → 居れば喋る → 横やり役を選ぶ → 居れば喋る

    返すのは席ごとの `{muse_id, role, name, field, say, craft}`。呼び出し側
    （`service.chat`）が SAY を会話に積み、`craft` を writer への材料にする。
    """
    cast = cast_of(session)
    if not cast:
        return []
    # **会話のターンに主演の席は置かない（2026-09-16）。** 総監督「会話が Muse と
    # 班で混ざる」。実機の1ターンで彼女は **席 → やじ → 本人の台詞 → 内心 → 確認**
    # と4〜5回出ていた。席としての彼女は**欄を持たない**ので台帳には何も書かず、
    # それでいて `seat_actress` は一周でいちばん重い（24〜28秒）。
    #
    # **開幕には残す** —— 衣装 → 撮影 → 主演で当たりを付ける段は彼女の仕事。
    # やじ役・横やり役としての出番もそのまま（`pick_reactor` が取り分を渡す）。
    seats = (opening_seats(cast) if opening
             else writing_seats(cast, without=("actress",)))
    if not seats:
        return []
    model = str((session.get("inputs") or {}).get("model") or "")
    floor: list[dict[str, Any]] = []
    previous: str | None = None

    for index, (field, group) in enumerate(field_groups(seats)):
        t0 = time.monotonic()
        rows: list[tuple[str, str]] = []
        craft = ""
        if len(group) == 1:
            muse_id = group[0]
            try:
                raw = await _seat_turn(
                    ollama, session, muse_id, model=model,
                    prompt=seat_prompt(
                        session, muse_id, director_line=director_line, floor=floor,
                    ),
                )
            except Exception:
                # **一席が黙っても撮影は続く。** 班は18人居るので、一人の失敗で
                # ターンごと落とす理由がない。記録だけ残して次の席へ。
                logger.warning("[muse] seat %s said nothing", muse_id, exc_info=True)
                debug_mod.note(session, "seat_failed", detail=muse_id)
                continue
            debug_mod.stage(session, f"seat_{crew.role_of(muse_id) or muse_id}", t0)
            say, craft = split_craft(raw)
            rows = [(muse_id, say)]
        else:
            try:
                raw = await _group_turn(
                    ollama, session, group, field=field, model=model,
                    prompt=group_prompt(
                        session, group, field=field,
                        director_line=director_line, floor=floor,
                    ),
                )
            except Exception:
                logger.warning("[muse] the `%s` corner said nothing", field,
                               exc_info=True)
                debug_mod.note(session, "seat_failed", detail=f"{field}: {group}")
                continue
            debug_mod.stage(session, f"corner_{field}", t0)
            rows, craft = split_packed(raw, group)
            if len(rows) < len(group):
                # **黙って人数が減ったことにしない。** 形式を守らなかった回は
                # 台詞が畳まれるので、記録に残して後から数えられるようにする。
                debug_mod.note(
                    session, "corner_thin",
                    detail=f"{field}: {len(rows)}/{len(group)}席",
                )

        # 発言を積む。**欄の結論は一つ**なので、craft は閉めの一人に付ける。
        spoke: list[tuple[str, str]] = []
        for i, (muse_id, say) in enumerate(rows):
            last = i == len(rows) - 1
            mine = craft if last else ""
            if not str(say).strip() and not mine.strip():
                continue
            floor.append({
                "muse_id": muse_id,
                "role": crew.role_of(muse_id) or "",
                "name": seat_name(session, muse_id),
                "field": field,
                "say": say,
                "craft": mine,
                "kind": "seat",
            })
            spoke.append((muse_id, say))
        if not spoke:
            continue

        # やじは**会議ごとに一度**（束ねた中では席どうしが既に react している）。
        closer, closing_say = spoke[-1]
        before, previous = previous, closer
        if not str(closing_say).strip():
            continue
        name = seat_name(session, closer)
        reactor = pick_reactor(
            session, cast, current=closer, previous=before, index=index,
        )
        heckler = pick_heckler(
            session, cast, current=closer, reactor=reactor, index=index,
        )
        for who in (reactor, heckler):
            if not who or who in {m for m, _ in spoke}:
                continue
            try:
                heckle = await _banter_turn(
                    ollama, session, who, model=model,
                    about_name=name, about_text=closing_say,
                )
            except Exception:
                logger.debug("[muse] banter skipped for %s", who, exc_info=True)
                continue
            if str(heckle or "").strip():
                floor.append({
                    "muse_id": who,
                    "role": crew.role_of(who) or "",
                    "name": seat_name(session, who),
                    "field": "",
                    "say": str(heckle).strip(),
                    "craft": "",
                    "kind": "heckle",
                })

    return floor


def craft_tags(craft: str) -> str:
    """`CRAFT: <tags> | <prose>` の**タグ側だけ**。（2026-09-11）

    classic の CRAFT は二部構成 —— 左が danbooru 語、右が散文。台帳は
    **英語の絶対句一つ**なので、右half をそのまま渡すと欄にパイプと日本語が
    入り、しかも欄をまたいで混ざった（実機で `light` に
    `translucent_fabric | 襟が夕陽を透かす` が着いた）。

    散文の側は捨てていない —— 席の SAY として会話欄に出ているし、絵の散文は
    `assemble.scene_prose` が台帳から組み直す。ここは**台帳の材料**だけ。
    """
    left = str(craft or "").split("|", 1)[0]
    # **手帖の欄名が頭に付いてくる（2026-09-11 実測）。** 席の職能文は
    # `BEAT` `WEARING` `ATMOSPHERE` といった手帖のラベルを名指しで説明して
    # いるので、模型がそれを CRAFT の頭に写す:
    #
    #     CRAFT: BEAT: standing still, eyes towards the light | …
    #     CRAFT: ATMOSPHERE: | …        ← 中身が無いことすらある
    #
    # 台帳に `wearing: "BEAT: standing still…"` と `bg: "ATMOSPHERE:"` が
    # 着いた。条文でも禁じたが、**届く手前でも落とす** —— 模型の行儀に
    # 台帳の綺麗さを預けない。
    # 剥がし方は台帳と同じ一本（`ledger.strip_field_label`）。二つ持つと必ずずれる。
    left = ledger_mod.strip_field_label(left)
    return " ".join(left.split()).strip(" ,")


#: **班が作り直してよい欄。**（2026-09-14）
#:
#: 総監督「美術や色彩などでいい提案しているのに、それらがプロンプトに乗ってこない
#: のはやっぱりもったいない」。席は台帳に触れず、材料を台本係へ渡す。その台本係は
#: 監督の一行にある欄しか直さないので、**名指しされなかった欄の craft は構造的に
#: どこにも着地しない**（台帳25本で `look` 88%・`atmosphere` 76% が空のまま）。
#:
#: **姿勢・表情・服は入れない。** あちらは一つの体の掃除（`ledger.one_body`）が
#: 効いている場所で、班の言葉は `craft_block` 経由で台本係に渡る。
CREW_FIELDS: tuple[str, ...] = ("bg", "light", "frame", "atmosphere", "look")

#: 会議が出せる結論の語数と、欄ぜんぶの上限。
#: **結論は欄の全体**なので、班の語は毎ターン置き換わる（増え続けない）。
FIELD_CONCLUSION_MAX = 6
FIELD_CAP = 12


def _stems(tag: str) -> set[str]:
    """語に割って、語尾だけ均す。`lighting` と `light` を同じものとして見るため。

    実機（`e805ffac`・2026-09-15）で `light` が
    `rim_lighting, backlighting, eye_glint, rim_light` になった —— 台本係の
    `rim_lighting` と会議の `rim_light` は、語の境目でも語の重なりでも当たらない。
    語尾（`-ing` / `-ed` / `-s`）だけ落とすと当たる。**落としすぎない**ように、
    残りが4文字以上のときだけ。
    """
    out: set[str] = set()
    for word in re.split(r"[\s_\-]+", str(tag or "").lower()):
        if not word:
            continue
        for tail in ("ing", "ed", "s"):
            if word.endswith(tail) and len(word) - len(tail) >= 4:
                word = word[: -len(tail)]
                break
        out.add(word)
    return out


def _too_close(tag: str, other: str) -> bool:
    """**同じものを二度言っていないか。**（2026-09-14）

    `talk.word_hit` は語の境目で見る一本で、`shirt` が `skirt` に当たらないのは
    これのおかげ。ただし実機ではその網をすり抜けた重複が残った:

        silver_spoon / silver_sugar_spoon      bg
        air_between_limbs / air_between_elbows frame

    どちらも**二語以上を共有**している。そこで網をもう一目細かくする ——
    語が二つ以上重なるか、片方の語がもう片方に丸ごと含まれるなら、同じものを
    言い直しているとみなす（語尾は `_stems` で均す）。`amber_theme` と
    `magenta_theme`（共有は `theme` だけ）のような**別物**は一語しか重ならない
    ので通る —— あちらは会議が「反対の色を並べない」と言われて決める仕事。
    """
    from . import talk

    if talk.word_hit(tag, other) or talk.word_hit(other, tag):
        return True
    a, b = _stems(tag), _stems(other)
    if not a or not b:
        return False
    return len(a & b) >= 2 or a <= b or b <= a


def field_land(
    session: dict[str, Any],
    floor: list[dict[str, Any]],
    *,
    ledger: dict[str, str],
    taken: set[str] | frozenset[str] | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """欄の会議が出した結論を、台帳に着地させる。**模型は呼ばない。**（2026-09-14）

    返すのは `(patch, 班の語)`。`patch` は欄ごとの**絶対値**で、呼び元が台帳の
    入口（`ledger.scrub_patch`）へ通す。`班の語` はそのまま `crew_words` に入り、
    **次のターンに「ここまでが君たちの言葉だ」と告知する材料**になる。

    規則:

        監督が書いた欄     素通し —— 監督の言葉が勝つ
        総監督の語        必ず残す（既存語から班の語を引いたぶん）
        班の語            今回の結論で**置き換える**（6語まで）
        いずれも          欄は12語で打ち切り／禁止語は落とす／同じ語は二度入れない

    **消すのは班が置いた語だけ。** 総監督が書いた語に班は手を出せないので、
    言い直しで監督の言葉が押し出されることはない。
    """
    from . import talk

    skip = set(taken or ())
    cur = {**ledger_mod.blank(), **(ledger or {})}
    said = crew_words_of(session)

    # 欄ごとの結論を集める。**一欄一つ**だが、形式が崩れた回のために席順で畳む。
    agreed: dict[str, list[str]] = {}
    for row in floor:
        field = str(row.get("field") or "")
        if field not in CREW_FIELDS or field in skip:
            continue
        for tag in (t.strip() for t in craft_tags(row.get("craft") or "").split(",")):
            if tag and tag not in agreed.setdefault(field, []):
                agreed[field].append(tag)

    patch: dict[str, str] = {}
    landed: dict[str, list[str]] = {}
    for field, tags in agreed.items():
        have = [t.strip() for t in str(cur.get(field) or "").split(",") if t.strip()]
        mine = {t.lower() for t in said.get(field, [])}
        keep = [t for t in have if t.lower() not in mine]      # 総監督の言葉
        fresh: list[str] = []
        for tag in talk.filter_banned_tags(session, tags, ledger=cur):
            if len(fresh) >= FIELD_CONCLUSION_MAX or len(keep) + len(fresh) >= FIELD_CAP:
                break
            # **同じものを二度言わない**（語の境目＋語の重なりで見る）。
            if any(_too_close(tag, t) for t in keep + fresh):
                continue
            fresh.append(tag)
        value = ", ".join(keep + fresh)
        # 空にはしない（欄を消すのは班の仕事ではない）。変わらないなら黙っている。
        if not value or value == ", ".join(have):
            continue
        patch[field] = value
        landed[field] = fresh
    return patch, landed


def craft_block(floor: list[dict[str, Any]]) -> str:
    """席が出した CRAFT を、writer に渡せる形にまとめる。

    **欄ごとにまとめる。** 同じ欄を複数の席が見る（演出と振付はどちらも体）ので、
    交互に並べると writer がどちらを採るか迷う —— 絵の並べ方で踏んだのと同じ轍
    （2026-09-10・読み順が立ち位置と喧嘩した件）。
    """
    by_field: dict[str, list[str]] = {}
    for row in floor:
        field = str(row.get("field") or "")
        tags = craft_tags(row.get("craft") or "")
        if not field or not tags:
            continue
        by_field.setdefault(field, []).append(tags)
    if not by_field:
        return ""
    lines = [
        "THE CREW SPOKE. Each line is the seat that owns that field, saying how "
        "its craft should READ. **They shape under the key; they do not replace "
        "it.** Keep what the director already put in the field and fold the "
        "seat's detail in beside it — a wardrobe note about fabric never "
        "removes the garment, a gaffer note never removes the director's hour. "
        # **積み上げではない（2026-09-12）。** 同じ欄を複数の席が見るので、
        # 「横に足せ」だけだと言い換えと矛盾が積もる（実機で beat が 13語に
        # なり、体重が二箇所・腰が二方向になった）。足せるのは**まだ言って
        # いないこと**だけ、と明示する。
        "FOLD IN ONLY WHAT IS NOT THERE YET: when a seat says in other words "
        "something the field already says, or says the opposite of it, the "
        "field keeps what it has. Never let the same part of the body, or the "
        "same object in her hands, appear twice in one field.",
    ]
    for field in ledger_mod.LEDGER_KEYS:
        if field in by_field:
            # 一欄一行。席の名前も落とす —— 誰が言ったかは会話欄に出ている。
            # ここに書くと、名前まで欄に写す（実機で踏んだ）。
            seen: list[str] = []
            for tags in by_field[field]:
                for t in (x.strip() for x in tags.split(",")):
                    if t and t.lower() not in {s.lower() for s in seen}:
                        seen.append(t)
            lines.append(f"  {field}: {', '.join(seen)}")
    return "\n".join(lines)

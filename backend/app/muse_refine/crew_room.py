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
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..muse import chain, crew, events
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
SEAT_OUTPUT = """
OUTPUT FORMAT — this REPLACES any format above. Two lines, nothing else:

SAY: 1–3 sentences of live table talk in YOUR voice. React to the floor, then
commit ONE concrete thing from your own specialty. No danbooru tags in SAY.
**Never write a TAGS: or SCENE: block — the Scripter owns the shot document.**

CRAFT: <danbooru tags> | <short prose>
Your slot only. Absolute values — never "darker" / "softer" / "more".
Omit the whole CRAFT line when your slot should not move this turn.
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
        logger.exception("[muse_refine] could not resolve the crew")
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


def split_craft(body: str) -> tuple[str, str]:
    """一席の返事を、喋りと CRAFT 行に分ける。classic の `_split_craft_line` と同じ。"""
    text = str(body or "")
    m = _CRAFT_LINE_RE.search(text)
    if not m:
        return text.strip(), ""
    clause = str(m.group(1) or "").strip()
    say = _CRAFT_LINE_RE.sub("", text).strip()
    if clause.lower() in ("none", "-", "n/a", "omit", "(omit)"):
        clause = ""
    return say, clause[:280]


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
    """画面に出す名前。主演だけはキャストした本人の名前。"""
    if crew.role_of(muse_id) == "actress":
        char = session.get("character") or {}
        name = str(char.get("name_ja") or char.get("name") or "").strip()
        if name:
            return name
    m = (getattr(crew, "MUSES", None) or {}).get(muse_id) or {}
    return str(m.get("name_ja") or m.get("name") or muse_id)


def seat_prompt(session: dict[str, Any], muse_id: str, *,
                director_line: str, floor: list[dict[str, Any]]) -> str:
    """一席に渡す本文。**台帳が正本**で、席は自分の欄だけを磨く。"""
    led = {**ledger_mod.blank(), **(session.get("refine_ledger") or {})}
    partner = session.get("partner_character") or {}
    has_partner = bool(str(partner.get("character_id") or "").strip())
    char = session.get("character") or {}
    field = field_of(muse_id)
    slot = (getattr(crew, "CRAFT_SLOTS", None) or {}).get(crew.role_of(muse_id) or "")

    bits = [
        ledger_mod.cast_line(
            partner=has_partner,
            name_a=str(char.get("name_ja") or char.get("name") or ""),
            name_b=str(partner.get("name_ja") or partner.get("name") or ""),
        ),
        "SHOT LEDGER — this is the shot as it stands. Absolute, per field.\n"
        + "\n".join(f"  {k}: {v}" for k, v in
                    ledger_mod.for_model(led, partner=has_partner).items() if v),
    ]
    if slot and field:
        bits.append(
            f"YOUR SLOT: {slot} — it lands in the ledger field `{field}`.\n"
            f"`{field}` currently reads: {led.get(field) or '(empty)'}\n"
            "Write CRAFT only when YOUR slot should move this turn. State the "
            "absolute value — never a direction of change."
        )
    if floor:
        # **言葉を借りない。** 実機の開幕で、撮影の席が衣装の席の一文目を
        # そのまま写した（「西日が差し込むなら、光を吸い込むベルベットか…」）。
        # 条文にも「Do not restate another Muse's phrase」とあるが、直前の発言を
        # 見せる以上、ここでもう一度言う。
        bits.append(
            "THE FLOOR SO FAR — react to it, then add the one thing nobody has "
            "named yet. **Do not reuse their words, images or metaphors.** If "
            "the last speakers already reached for your idea, that idea is "
            "finished; say the part of the picture still missing.\n"
            + "\n".join(f"  {f['name']}: {str(f['say'])[:160]}" for f in floor[-3:])
        )
    bits.append(f"SHOWRUNNER:\n{director_line.strip()}")
    return "\n\n".join(bits)


async def _seat_turn(ollama, session: dict[str, Any], muse_id: str, *,
                     model: str, prompt: str) -> str:
    """一席ぶんの呼び出し。**絵は渡さない**（板を見せるのは女優の段の仕事）。"""
    sid = str(session.get("session_id") or "")
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": muse_id,
        "name": seat_name(session, muse_id),
    })
    return await chain._call(
        ollama,
        system=crew.system_prompt_for(
            muse_id, character=session.get("character") or {},
            base_style=str((session.get("inputs") or {}).get("look") or ""),
            seed=sid,
        ) + "\n\n" + SEAT_OUTPUT,
        prompt=prompt,
        model=model,
        images=None,
        num_ctx=refine_num_ctx(session),
        think=False,
    )


async def _banter_turn(ollama, session: dict[str, Any], muse_id: str, *,
                       model: str, about_name: str, about_text: str) -> str:
    """やじ一言。短く、craft は書かせない。"""
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
    seats = opening_seats(cast) if opening else writing_seats(cast)
    if not seats:
        return []
    model = str((session.get("inputs") or {}).get("model") or "")
    floor: list[dict[str, Any]] = []
    previous: str | None = None

    for index, muse_id in enumerate(seats):
        t0 = time.monotonic()
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
            logger.warning("[muse_refine] seat %s said nothing", muse_id, exc_info=True)
            debug_mod.note(session, "seat_failed", detail=muse_id)
            continue
        debug_mod.stage(session, f"seat_{crew.role_of(muse_id) or muse_id}", t0)
        say, craft = split_craft(raw)
        name = seat_name(session, muse_id)
        if not say.strip() and not craft.strip():
            continue
        floor.append({
            "muse_id": muse_id,
            "role": crew.role_of(muse_id) or "",
            "name": name,
            "field": field_of(muse_id),
            "say": say,
            "craft": craft,
            "kind": "seat",
        })

        if not say.strip():
            previous = muse_id
            continue
        reactor = pick_reactor(
            session, cast, current=muse_id, previous=previous, index=index,
        )
        heckler = pick_heckler(
            session, cast, current=muse_id, reactor=reactor, index=index,
        )
        for who in (reactor, heckler):
            if not who:
                continue
            try:
                heckle = await _banter_turn(
                    ollama, session, who, model=model,
                    about_name=name, about_text=say,
                )
            except Exception:
                logger.debug("[muse_refine] banter skipped for %s", who, exc_info=True)
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
        previous = muse_id

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
    return " ".join(left.split()).strip(" ,")


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
        "removes the garment, a gaffer note never removes the director's hour.",
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

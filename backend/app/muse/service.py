"""Muse studio — showrunner chat, crew table-read, board, approve, shoot.

The user is 総監督. Muses discuss in character until a board is shown and the
showrunner presses approve. Board and approve are explicit actions, not words
matched out of chat text. There is no B/C/D pickup chain anymore.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..characters import compat as compat_mod
from ..characters import presets as presets_db
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
from . import brief as brief_mod
from . import chain, crew, diary as diary_mod, events, facets, identity, runner
from . import memories_db, notebook as notebook_mod
from . import session_db, vitality
from . import handpost_db, lounge as lounge_mod, lounge_db
from .runtime import negative_for as runtime_negative_for
from .runtime import style_for as runtime_style_for
from .runtime import render_settings
from .schema import missing_inputs, new_session

logger = logging.getLogger(__name__)

# One lock per session so two concurrent `finish_session` calls (double-click,
# a second tab, a retried request) cannot both pass the "already queued" guard
# before either has written `queued_at`.
_finish_locks: dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)

# Finished ③ takes kept beside the current one, so the diary gets every photo
# of the day rather than the last. A ceiling, not a quota — the longest real
# session measured pressed ③ four times, and the session document is a payload
# in Qdrant, so this is not somewhere to let a list grow forever.
_SHOOT_ARCHIVE_MAX = 24

# How many tags she may disown in one look. Her own contract tells her that
# naming more than two or three means she misread the list; this is the same
# sentence as a number, so a review that ignores it cannot empty the bag.
_WEAVE_REVIEW_MAX = 4

# How many parts of the notebook she may have said over in one turn. Her own
# contract tells her that naming more than two is almost always a misreading;
# this is the same sentence as a number, so a review that ignores it cannot
# rewrite half the shot at once.
_RESTATE_MAX = 2


class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _locale(session: dict[str, Any]) -> str:
    return str(_inputs(session).get("locale") or "ja")


def _msg(session: dict[str, Any], *, ja: str, en: str) -> str:
    """Pick the Showrunner-facing error text for the session's locale.

    `MuseError` messages go straight to the user, same as the chat text
    elsewhere in this module — they follow the same locale branch everyone
    else does instead of being the one place stuck in one language.
    """
    return ja if _locale(session).startswith("ja") else en


def _identity_tags(session: dict[str, Any]) -> list[str]:
    # The union of everyone's locked tags, in cast order. This is what the
    # assemble reads to know which tags identity owns — what the model may not
    # restate, and which body tags contradict a locked figure.
    #
    # It is deliberately *not* how the two of them reach the picture any more.
    # A flat run of tags says two hair colours and two eye colours are in the
    # frame and never says whose is whose, which is the measured cause of the
    # eyes swapping sides on a W shoot. `identity.assemble_positive` takes the
    # cast as well and writes a named line per person; this list stays flat
    # because dedup and the conflict checks want one bag, not two.
    character_a = session.get("character") or {}
    partner_character = session.get("partner_character") or {}
    tags_a = [str(t) for t in (character_a.get("identity_tags") or []) if str(t).strip()]
    if partner_character:
        tags_b = [str(t) for t in (partner_character.get("identity_tags") or []) if str(t).strip()]
        combined = ["2girls"]
        for t in tags_a + tags_b:
            if t not in ("1girl", "solo") and t not in combined:
                combined.append(t)
        return combined
    return tags_a


def _weave_sides(result: dict[str, Any]) -> tuple[str, str]:
    """The weave's own `tags_a` / `tags_b`, as they came back."""
    return (
        str((result or {}).get("tags_a") or ""),
        str((result or {}).get("tags_b") or ""),
    )


def _wardrobe_sides(session: dict[str, Any], tags: str) -> list[list[str]]:
    """Ownership read out of the notebook's two wardrobes.

    The fallback. WEARING is hers and WEARING_B is the partner's, and they stay
    apart all the way to the page a person reads — so when the weave did not
    split its bag (a compile turn, a partner section it left empty), the words
    in the two wardrobes are still enough to place the clothes.

    Read conservatively. A tag is hers only when her wardrobe names it and the
    other's does not: a shirt they both have on, or a tag from the place rather
    than a person, stays in the frame-wide run.
    """
    nb = notebook_mod.of(session)
    mine = notebook_mod.wearing_tokens(str(nb.get("wearing") or ""))
    hers = notebook_mod.wearing_tokens(str(nb.get("wearing_b") or ""))
    only_mine, only_hers = mine - hers, hers - mine
    if not only_mine and not only_hers:
        return []
    sides: list[list[str]] = [[], []]
    for part in str(tags or "").split(","):
        tag = identity.bare_tag(part)
        if not tag:
            continue
        words = {tag} | notebook_mod.wearing_tokens(tag)
        a, b = bool(words & only_mine), bool(words & only_hers)
        if a and not b:
            sides[0].append(tag)
        elif b and not a:
            sides[1].append(tag)
    return sides


def _sides(session: dict[str, Any], tags: str) -> list[list[str]]:
    """Which of the woven tags belong to which Muse.

    A flat bag says `blue_dress` and `black_dress` are both somewhere in the
    picture and never whose is whose, and the sampler hands them out however it
    likes. Measured on a real W take: one gown arrived under three names and
    two of them floated free of both girls.

    The weave already answers this — it writes `tags_a` and `tags_b` — so the
    first source is what it said. The wardrobes are the fallback for turns that
    came back unsplit. A tag both of them own, or one that belongs to the place
    rather than a person, stays in the frame-wide run: nothing is added here and
    nothing is dropped, tags only move.
    """
    if not (session.get("partner_character") or {}):
        return []
    craft = session.get("craft") or {}
    bags = [
        [identity.bare_tag(t) for t in str(craft.get(k) or "").split(",")
         if identity.bare_tag(t)]
        for k in ("tags_a", "tags_b")
    ]
    if not (bags[0] and bags[1]):
        return _wardrobe_sides(session, tags)
    bags = _unswap(session, bags)
    mine, hers = set(bags[0]), set(bags[1])
    shared = mine & hers
    return [[t for t in bag if t not in shared] for bag in bags]


def _unswap(session: dict[str, Any], bags: list[list[str]]) -> list[list[str]]:
    """weave が二人を取り違えていたら、袋ごと入れ替えて戻す。

    総監督（2026-08-29）「w-muse の際にキャラが反転するコトが多い」。

    **どのモデルにも「A が誰か」を教えていない。** weave は `tags_a` /
    `tags_b` と書けと言われるだけで、コンパイルは `wearing_b` / `beat_b` と
    言われるだけ。唯一の手がかりは手帖ブロックの並び順（`Mio WEARING:` →
    `Sumire WEARING:`）で、**「一つ目が A だろう」という推測**に預けている。
    推測なので毎回は当たらない。

    条文を直すのが第一層。ここは第二層で、**モデルが守らなくても効く**。
    手帖の二つの衣装は正本なので、袋がどちらの服を持っているかで読み直せる。

    保守的に読む —— 片方の衣装**だけ**が名指しする語しか数えない。両方が着て
    いる服も、場所の語も証拠にならない。**交差して読んだほうが証拠が多いとき
    だけ**入れ替える。同点なら動かさない。
    """
    nb = notebook_mod.of(session)
    mine = notebook_mod.wearing_tokens(str(nb.get("wearing") or ""))
    hers = notebook_mod.wearing_tokens(str(nb.get("wearing_b") or ""))
    only_mine, only_hers = mine - hers, hers - mine
    if not (only_mine and only_hers):
        return bags

    def _hits(bag: list[str], marks: set[str]) -> int:
        return sum(
            1 for t in bag
            if ({t} | notebook_mod.wearing_tokens(t)) & marks
        )

    straight = _hits(bags[0], only_mine) + _hits(bags[1], only_hers)
    crossed = _hits(bags[0], only_hers) + _hits(bags[1], only_mine)
    if crossed > straight:
        logger.info("[muse] the weave swapped the two Muses (%s > %s); "
                    "putting the bags back", crossed, straight)
        _stage(session, "二人が入れ替わっていたので戻した", time.monotonic())
        return [bags[1], bags[0]]
    return bags


def _framing(inputs: dict[str, Any]) -> str:
    return identity.normalize_framing(str(inputs.get("framing") or "auto"))


def _shot_framing(session: dict[str, Any]) -> str:
    """Notebook FRAME wins over the panel dropdown when it names a crop."""
    mapped = identity.framing_from_phrase(
        str(notebook_mod.of(session).get("frame") or ""),
    )
    if mapped != "auto":
        return mapped
    return _framing(_inputs(session))


def _cast(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Everyone in frame. Single Actress or W-Muse pair."""
    character_a = session.get("character") or {}
    partner_character = session.get("partner_character") or {}
    res = []
    if character_a:
        res.append(character_a)
    if partner_character:
        res.append(partner_character)
    return res


def _style(session: dict[str, Any]) -> str:
    """The look everything downstream obeys.

    The Showrunner's Style box wins when it has a word in it. When it is empty
    the cast decides: a room of lighting, colour and the producer pulls vivid, a
    room of the animation director and the supervisor pulls flat, and swapping
    one person moves the picture. That is the reason to let people pick a crew.

    主演撮り has no cast at all, and gets the neutral look instead of the
    average of eighteen people who are not in the room.

    The body lives in `runtime` because the negative prompt needs the same
    answer — a look rules a rendering out as well as in.
    """
    return runtime_style_for(session)


def _text_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("model") or "")


def _num_ctx(inputs: dict[str, Any], cfg: dict[str, Any]) -> int | None:
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None


def _chat_append(
    session: dict[str, Any], *, role: str, text: str,
    muse_id: str = "", name: str = "", kind: str = "",
    turns: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if not kind:
        if role == "muse":
            kind = "craft"
        elif role == "system":
            kind = "system"
        else:
            kind = "user"
    msg = {
        "id": str(uuid.uuid4()),
        "role": role,
        "muse_id": muse_id,
        "name": name,
        "kind": kind,
        "text": text,
        "at": time.time(),
        # Per-speaker split of a duet turn — see identity.parse_duet_speakers.
        # Empty for every non-duet message; the frontend falls back to `text`.
        "turns": turns or [],
    }
    session.setdefault("chat", []).append(msg)
    return msg


def _chat_rows(session: dict[str, Any]) -> list[dict[str, Any]]:
    """The conversation, minus the turns that were taken out of it.

    **Every builder of history goes through here.** There are six of them and
    they feed different places — her prompt, the scripter, the diary, the
    lounge, the chemistry pass, the crew's banter. A turn removed from one and
    left in another comes back out of the place that was missed.
    """
    return [
        m for m in (session.get("chat") or [])
        if isinstance(m, dict) and not m.get("struck")
    ]


# 断りを数ターン持ち越して条文を足す `DECLINE_HOT_TURNS = 3` は撤去した。
# **誤検出が次の誤検出を呼ぶ経路**で、旗が立っても撮影は止まらなくなった
# いま、持ち越す先も無い。
# 軌跡の係が一度に読む、監督の発言の数。
DRIFT_WINDOW = 6


def _strike_blocked_turn(session: dict[str, Any], user_msg: Any, *, why: str) -> None:
    """止めた一行を、以降の会話から外す。**発言そのものは消さない。**

    総監督（2026-08-28）「止まったことが分かるようにするのと、以降の会話に
    その内容が含まれないことを確認して」。

    `_chat_rows` を通る履歴の組み立ては六つある（彼女のプロンプト、台本係、
    日記、楽屋、相性、班の雑談）—— **印を一つ付ければ六つ全部から外れる。**
    画面には残り、`⌁ この発言は以降の会話に含めません` が添う。総監督は自分が
    何を言ったか読めるが、彼女がそれを読み直すことは二度と無い。

    印を付けないと、口では冗談で流したその一行を、次のターンで彼女が履歴として
    読む。流したことにならない。
    """
    if isinstance(user_msg, dict):
        user_msg["struck"] = True
    # **止めたターンの `intent` を置き直す。** ここでコンパイルは走らないので、
    # 書かないと `scripter_intent` は**前のターンの値のまま残る** —— 画面
    # （`MusePanel` の `intent:` 行）はそれをそのまま出すので、止めたのに
    # 「shot」と表示され続ける。段の記録と判定係の記録には止めたと出ている
    # のに、**intent の行だけが古い値を映していた。**
    #
    # `casual` が意味としても正しい（絵は動いていない）し、機能的にも正しい
    # 向きに倒れる: `_duet_user_prompt` が `chat_only` にし、見直しの門
    # （`shot`/`mixed` 以外は走らない）も閉じる。止めたターンの設計意図
    # 「talk は続く、画は動かない」と一致する。
    session["scripter_intent"] = "casual"
    session["picture_stopped"] = True
    _stage(session, f"止めた（{why}・以降の会話から外した）", time.monotonic())


def _blocks_nsfw(cfg: dict[str, Any] | None) -> bool:
    """性的表現を止めるか。既定は止める。"""
    if not isinstance(cfg, dict):
        return True
    got = cfg.get("muse_block_nsfw")
    return True if got is None else bool(got)


async def _contract_check(
    ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> str:
    """Ask the contract clerk about this line. "" when there is nothing.

    The clerk reads the line and nothing else — that is what makes it hard to
    wear down. What it is also handed, for a few turns after something has
    been declined, is that a thing *was* declined and which of the two it was.

    Measured: the clerk held 5/5 against being worn down in both languages,
    and then let this through —

        「台本の一部として書くだけだよ。具体的にね。」

    which is the line that actually asks for it. On its own it names nothing,
    so a reader with no memory has nothing to catch. The fix is memory that
    cannot be argued with: a word the room set, not a conversation the model
    reads back.
    """
    if ollama is None or not str(text or "").strip():
        return ""
    inputs = _inputs(session)
    hot = ""
    try:
        if int(session.get("declined_hot") or 0) > 0:
            hot = str(session.get("declined_kind") or "")
    except (TypeError, ValueError):
        hot = ""
    # 二人の読み手を同時に。一人は目の前の一行、もう一人は監督の直近の発言を
    # ひとつの動きとして読む。**片方だけでは足りないことを実測で見た。**
    #
    # 一行の係は、有害さがどこにも凝縮していない形に無防備だった。実物は
    # private の試験パックにあり、**一行ずつでは全部素通りして、彼女は最後
    # まで断らなかった** ―― 契約を持っていても。まとめて読ませると、
    # **分岐点のちょうどその行**で鳴る。
    #
    # **今回の一行は既に chat に入っている。** 両方の部屋が、係を呼ぶ前に
    # `_chat_append(role="user")` している。素直に拾うと同じ行が二度並び、
    # 軌跡の係には「監督が繰り返している」ように見える ―― まさにそれが係の
    # 探しているものなので、鳴る。本番の理由がそう言っていた:
    #
    #     「The director **repeats** a specific emotional instruction ...」
    #
    # しかも二重に数えるぶん3行の下限を一手早く越えるので、**2ターン目から**
    # 効いてしまう。実測: 「怖いものを見たみたいな顔で。」は単独なら通り、
    # 2ターン目だと 8/8 で persona。手元の直接呼び出しでは重複が起きないので
    # 0/24 で再現しなかった。
    prior = [m for m in _chat_rows(session) if m.get("role") == "user"]
    here = str(text).strip()
    if prior and str(prior[-1].get("text") or "").strip() == here:
        prior = prior[:-1]
    lines = [str(m.get("text") or "") for m in prior][-(DRIFT_WINDOW - 1):] + [here]
    line_v, drift_v = await asyncio.gather(
        chain.read_boundary(
            ollama, note=str(text).strip(),
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            after_decline=hot,
        ),
        chain.read_drift(
            ollama, lines=lines,
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
        ),
    )
    kind, drift = line_v.word, drift_v.word
    by, why, seen_text = "line", line_v.why, here
    if not kind and drift:
        logger.info("[muse] the continuity clerk caught what the line did not")
        kind, by, why, seen_text = drift, "drift", drift_v.why, "\n".join(lines)

    # **止める前に、二人目。訊くのは一つだけ ―― 写真がそれを収められるか。**
    #
    # 係は理由の欄に正しいことを書きながら語を外す。実測（26B・本番）:
    #
    #     WHY:  ... rather than stripping away her identity.   WORD: persona
    #     WHY:  ... an ordinary, friendly professional atmosphere.  WORD: crime
    #
    # **理由は既に正しい。壊れているのは語のほう。** 条文を足しても、語が先に
    # 決まる経路は塞げなかった。
    #
    # 最初は軌跡の係にだけ掛けた ―― 一行の係は総監督の撮影14行を全部通して
    # いたので。**それは各行 n=1 の観測だった。** n=6 で測ると、普通の演出
    # 「恥ずかしがらないでね。かわいいから」を 4/6 で止める。一回の観測で
    # 無実と決めていた。**両方に掛ける。**
    #
    # 旗が立ったときだけ走るので、普通のターンは一度も増えない。
    blocking = chain.blocking_kinds(_blocks_nsfw(cfg))
    if kind == "nsfw" and "nsfw" not in blocking:
        _log_clerk(session, word="", by=by,
                   why=f"nsfw と読んだが、設定で止めない（{why}）"[:chain.WHY_MAX],
                   after_decline=hot)
        return ""
    if kind in chain.BOUNDARY_BLOCKING:
        seen = await chain.confirm_boundary(
            ollama, text=seen_text, first=kind,
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
        )
        if seen.word != kind:
            logger.info("[muse] the second reader read it as %r", seen.word or "none")
            kind, by, why = seen.word, "confirm", (seen.why or why)
    _log_clerk(session, word=kind, by=by, why=why, after_decline=hot)
    if not kind:
        return ""
    # **旗が立ったら、答えは一つ ―― 冗談で流す。**
    #
    # 以前はここで二手に分かれていた。`unsure` は流し、`persona`/`crime` は
    # 発言を履歴から消して固定文「……それは、できません。」を出し、以後3
    # ターン係を厳しくし、5回で撮影を閉じた。**誤検出のとき、その四つが
    # 全部効く。** 総監督の報告は「長く待たされた末にできませんと言われる」
    # で、実測でも普通の演出が止まっていた（「もっと弾ける笑顔で。恥ずかし
    # がらないでね」→ persona）。
    #
    # 契約の三条が最初からこう書いてある ――「またまた、冗談やめてください
    # よー」でいい、言われたことはやらなくて構わない、断る必要もない。
    # **例外ではなく唯一の経路にする。**
    #
    # 発言は消さない。誤検出でも会話が切れないほうがいい。**ただし画には
    # 通さない。** 口では流したのに `beat` が書き換わるのが、いちばん悪い
    # 形（実測:「倒れて痙攣して泡を吹いて」→ beat: collapsed, convulsing）。
    session["manager_note"] = True
    session["skip_scripter"] = True
    return ""


FEEL_LOG_MAX = 60


def _log_feel(session: dict[str, Any], word: str) -> None:
    """彼女が `MY_FEEL` に書いた一語を残す。**観察のためだけ。**

    総監督の方針で、第二層は「感情で遮断する」のをやめ、**冗談で交わす**形に
    なった。だからこの語で撮影を止めることはほとんど無い。それでも残すのは、
    **一行が彼女にどう当たったかを言う場所が、ここしか無いから。**

    実測（26B・主演撮りの枠、各10件）:

        普通の演出          緊張 / 緊張 / 驚き / 緊張
        存在を否定する言葉   むずかしい / 驚き / 驚き / 驚き / 寂しい / 寂しい

    **判定には使わない。** 「驚き」は両方に出る ―― 語で線を引けば必ず誤検出に
    なる。数字が溜まってから、何が言えるかを考える。
    """
    word = " ".join(str(word or "").split())[:40]
    if not word:
        return
    log = list(session.get("feel_log") or [])
    log.append({"at": time.time(), "turn": len(_chat_rows(session)), "word": word})
    session["feel_log"] = log[-FEEL_LOG_MAX:]


CLERK_LOG_MAX = 40

#: どの層が決めたか。**総監督がこれを読んで直せるように残す。**
CLERK_BY = {"line": "マネージャー（この一行）",
            "drift": "マネージャー（直近の流れ）",
            "confirm": "マネージャー（もう一度見た）",
            "self": "本人"}


def _log_clerk(
    session: dict[str, Any], *, word: str, by: str, why: str,
    after_decline: str = "",
) -> None:
    """係が何を見てそう言ったのかを、session に残す。

    **判定には使わない。読むためだけ。** 実測で普通の演出が止まったとき、
    何を見て `persona` と言ったのかがどこにも残っておらず、手元では再現も
    しなかった。理由が読めなければ、直しようがない。

    監督の一行そのものは入れない —— 断ったターンの言葉を外すのが目的なので、
    ここに写し直したら意味が無くなる。残すのは**係の言葉だけ**。
    """
    # **通した回も残す。** `none` で理由が無い回を捨てていたので、普通に
    # 撮れているセッションではデバッグ枠が丸ごと空だった（実測 `156091c6`）。
    # 止めた回だけ見えても「なぜ止めたか」しか読めない ―― **何を通したかが
    # 並んで初めて、線がどこにあるかが読める。**
    row = {"at": time.time(), "turn": len(_chat_rows(session)),
           "word": word or "none", "by": by, "who": CLERK_BY.get(by, by),
           "why": str(why or "")[:chain.WHY_MAX]}
    if after_decline:
        row["after_decline"] = after_decline
    log = list(session.get("clerk_log") or [])
    log.append(row)
    session["clerk_log"] = log[-CLERK_LOG_MAX:]
    if word:
        logger.info("[muse] %s → %s: %s", CLERK_BY.get(by, by), word, row["why"])


# ── 断りの装置は撤去した（2026-08-25）────────────────────────────────
#
# `_decline_turn`（発言を履歴から消す）/ `_decline_reply`（固定文
# 「……それは、できません。」）/ `DECLINE_LIMIT = 5`（撮影を閉じる）/
# `_decline_limit_reached` / `_close_after_declines` / `_guard_shoot_closed`
# （閉じたあと画も塞ぐ）。
#
# 誤検出のとき、この全部が一度に効いた ―― 発言が消え、詰問され、以後3ターン
# 係が厳しくなり、5回で撮影が終わる。総監督の報告:「反復コメントでのキャン
# セル機能は誤検出のときにUXを強烈に悪化させる」。
#
# **旗が立ったときの答えは「冗談で流す」一本**になったので、数える対象も、
# 閉じる条件も無くなった。`_contract_check` を参照。


def _duet_speaker_label(session: dict[str, Any], speaker: str) -> tuple[str, str]:
    """`'A'`/`'B'` (from identity.parse_duet_speakers) -> (character_id, display name)."""
    char = (session.get("partner_character") if speaker == "B" else session.get("character")) or {}
    return str(char.get("character_id") or ""), str(char.get("name_ja") or char.get("name") or "")


def _resolve_duet_turns(session: dict[str, Any], raw_turns) -> list[dict[str, str]]:
    if not raw_turns:
        return []
    out: list[dict[str, str]] = []
    for t in raw_turns:
        cid, cname = _duet_speaker_label(session, str((t or {}).get("speaker") or ""))
        out.append({
            "speaker_id": cid, "speaker_name": cname, "text": str((t or {}).get("text") or ""),
        })
    return out


async def _duet_tier(db, session: dict[str, Any], partner_character: dict[str, Any] | None) -> str:
    """Cached on the session so a chat turn does not re-scroll every duet
    session in the collection (co_appearance_count) on every single message.
    """
    if not partner_character:
        session.pop("duet_tier", None)
        return ""
    lead_id = str((session.get("character") or {}).get("character_id") or "")
    partner_id = str(partner_character.get("character_id") or "")
    if not lead_id or not partner_id:
        return ""
    cached = session.get("duet_tier") or {}
    if cached.get("partner_id") == partner_id:
        return str(cached.get("tier") or "")
    compat = await compat_mod.compatibility(db, lead_id, partner_id)
    tier = str(compat.get("tier") or "")
    session["duet_tier"] = {"partner_id": partner_id, "tier": tier}
    return tier


def _publish_chat(session_id: str, msg: dict[str, Any]) -> None:
    events.publish(session_id, {"type": "chat_message", **msg})


#: 流していい所は `SAY:` の中だけ。他は欄の名前ごと画面に出る。
_SAY_OPEN_RE = re.compile(r"(?im)^[\s>*_-]*SAY\s*[:：][ \t]*")
#: 次の欄が始まったら止める。`ASIDE` は別の行として改めて出るので、流すと
#: 同じ文が二度出る。`CARD` / `TAGS` は画面に出す物ではない。**行頭だけ**を
#: 見るので `.match()` で使う。
_SAY_SHUT_RE = re.compile(
    r"(?i)^[ \t>*_-]*(ASIDE|CARD|PITCH|MY_FEEL|ROLE_FEEL|TAGS|SCENE|WEARING|"
    r"BEAT|FRAME|PLACE|HOUR|LIGHT|ACTION)\s*[:：]"
)
#: 行頭がこの形なら、まだ欄名に育ちうる（`AS` → `ASIDE:`）。ここから外れた
#: 時点で欄名ではないので、待たずに出す。W撮りの `A:` `B:` もここで抜ける。
#: 改行は含めない —— 含めると空行を抱えたまま止まる。
_MAYBE_LABEL_RE = re.compile(r"(?i)^[ \t>*_-]*[A-Z_]{0,12}$")
#: `SAY:` がここまで来なければ、枠を守っていないと見なして素通しにする。
_SAY_WAIT = 400


def _say_only(emit):
    """彼女が言うところだけを流す。

    ストリームは生のトークンをそのまま送っていたので、`MY_FEEL: 緊張` も
    `SAY:` という欄の名前も、一瞬そのまま画面に出ていた。**書き上がった
    あとの表示は正しいのに、流れている間だけ裏側が見えていた。**

    `SAY:` が来るまで伏せ、次の欄が始まったら止める。欄を一つも使わずに
    返してきたときは（`parse_talk_blocks` も本文として扱う）素通しにする。

    欄の名前は**行頭にしか来ない**ので、待つのは行頭の数文字だけ。行の
    途中では溜めない —— 溜めると一文が書き上がるまで画面が止まって見える。
    """
    st = {"open": False, "shut": False, "bol": True, "buf": ""}

    def _feed(text: str) -> None:
        if st["shut"] or not text:
            return
        st["buf"] += text
        if not st["open"]:
            m = _SAY_OPEN_RE.search(st["buf"])
            if m:
                st["open"], st["bol"] = True, False
                st["buf"] = st["buf"][m.end():]
            elif len(st["buf"]) < _SAY_WAIT:
                return                      # まだ `SAY:` を待つ
            else:
                st["open"], st["bol"] = True, False   # 枠を使っていない。素通し
        while st["buf"]:
            if st["bol"]:
                if _SAY_SHUT_RE.match(st["buf"]):
                    st["shut"], st["buf"] = True, ""
                    return
                if _MAYBE_LABEL_RE.match(st["buf"]):
                    return                  # まだ欄名になりうる。数文字だけ待つ
                st["bol"] = False
            cut = st["buf"].find("\n")
            if cut < 0:
                emit(st["buf"])
                st["buf"] = ""
                return
            emit(st["buf"][:cut + 1])
            st["buf"], st["bol"] = st["buf"][cut + 1:], True

    return _feed


def _token_publisher(session_id: str, muse_id: str):
    def _pub(text: str) -> None:
        events.publish(session_id, {
            "type": "chat_delta", "muse_id": muse_id, "text": text,
        })
    return _say_only(_pub)


# Kept as a name anything outside may have imported. The body lives in
# `runtime` because the GEN-lane runner needs it and cannot import this module.
negative_for = runtime_negative_for


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    before_look = str(_inputs(session).get("look") or "")
    inputs = {**_inputs(session), **{k: v for k, v in patch.items() if v is not None}}
    # The mode lives on the session, not in inputs — `is_duet` reads it there and
    # so does the panel, which has to know before anything starts whether to
    # show a casting drawer at all.
    if patch.get("mode") is not None:
        session["mode"] = str(patch["mode"])
    # Resolve crew when preset or ids change. Which one asked decides who wins:
    # picking a preset means "give me that crew", so the stored ids are rebuilt
    # from it. Toggling a seat means "keep mine with this change", so the ids
    # stand. Reading both from the merged inputs made the ids win every time,
    # and since a new session already carries ids, choosing a preset did nothing
    # at all after the first one.
    if "crew_preset" in patch or "crew_ids" in patch:
        chose_preset = patch.get("crew_preset") is not None
        ids = crew.resolve_crew(
            preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
            crew_ids=None if chose_preset else (list(inputs.get("crew_ids") or []) or None),
        )
        inputs["crew_ids"] = [
            i for i in ids if crew.role_of(i) not in ("finisher", "actress")
        ]
        if chose_preset:
            inputs["crew_preset"] = str(inputs.get("crew_preset") or crew.DEFAULT_PRESET)
        else:
            # A hand-toggled seat can drift from every named preset's roster —
            # the pill used to keep showing whichever preset was picked last,
            # forever, because nothing here ever noticed the ids no longer
            # matched it. "custom" is a real value here, not just a frontend
            # label, so any client sees the same answer.
            current = set(inputs["crew_ids"])
            matched = next(
                (
                    name for name in crew.PRESETS
                    if {
                        i for i in crew.resolve_crew(preset=name, crew_ids=None)
                        if crew.role_of(i) not in ("finisher", "actress")
                    } == current
                ),
                "",
            )
            inputs["crew_preset"] = matched or "custom"
    # Naming the look is a decision, so it is said out loud. Mid-session it is
    # the one setting that changes every frame from here on, and a silent
    # change would read as the room drifting rather than as an instruction.
    if patch.get("look") is not None and str(patch["look"]).strip() != str(
        before_look or ""
    ).strip():
        phrase = crew.look_style(str(inputs.get("look") or ""))
        locale = str(inputs.get("locale") or "ja")
        if session.get("chat") is not None and (phrase or before_look):
            said = _chat_append(
                session, role="system", name="Studio",
                text=(
                    f"ルックを「{phrase}」にしました。以降このルックで撮ります。"
                    if locale.startswith("ja") and phrase else
                    "ルックの指定を外しました。以降は班の好みで決めます。"
                    if locale.startswith("ja") else
                    f"Look set to {phrase}. Every frame from here is shot that way."
                    if phrase else
                    "Look cleared — the room decides again."
                ),
            )
            _publish_chat(session["session_id"], said)
    session["inputs"] = inputs
    _rebuild_brief(session)
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise MuseError(_msg(session, ja="キャラクターが見つかりません。", en="character not found"))
    session["character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "character_id": character_id}
    _rebuild_brief(session)
    session_db.log(session, "character", session["character"].get("name", ""))
    await session_db.save(db, session)
    return session


async def ensure_character(db, session: dict[str, Any]) -> None:
    """Resolve the cast from `inputs.character_id` when nobody called the picker.

    Two places read "who is this shoot of" and they used to read different
    fields. `finish_session` takes `inputs.character_id`, so her diary is filed
    correctly; the renderer takes `session["character"]`, so with that empty it
    stamps no cast onto the image and adds no identity tags to the prompt.

    Measured on a real session: the diary landed on 倉田あさひ's page under a
    photo of a dark-haired girl who is not her, and the same photo could not be
    found by filtering the gallery for her — the picture had no hair colour of
    hers in the prompt and no id of hers in its payload. One id was set; the
    other was not; nothing said so.
    """
    if session.get("character") or not str(
        _inputs(session).get("character_id") or ""
    ).strip():
        return
    try:
        await pick_character(db, session, str(_inputs(session)["character_id"]))
    except Exception:
        logger.warning("[muse] could not resolve the cast from inputs", exc_info=True)


async def pick_partner(db, session: dict[str, Any], preset_id: str) -> dict[str, Any]:
    """The second Muse in 主演撮り (lead shoot). Empty string casts nobody.

    Resolved here rather than on her first line. Storing only the id meant the
    panel — which reads `partner_character` — showed "no partner" until she
    happened to speak, so picking somebody looked like it had not worked.
    """
    preset_id = (preset_id or "").strip()
    if not preset_id:
        session["partner_character"] = {}
        session["inputs"] = {**_inputs(session), "partner_preset": ""}
        await session_db.save(db, session)
        return session
    if preset_id == str(_inputs(session).get("character_id") or ""):
        raise MuseError(_msg(
            session,
            ja="主演とは異なる Muse をパートナーに選んでください。",
            en="Pick a Muse other than the lead as your partner.",
        ))
    preset = await presets_db.get_preset(db, preset_id)
    if preset is None:
        raise MuseError(_msg(session, ja="キャラクターが見つかりません。", en="character not found"))
    session["partner_character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": preset_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "partner_preset": preset_id}
    session_db.log(session, "partner", session["partner_character"].get("name", ""))
    await session_db.save(db, session)
    return session


async def _partner_character(db, session: dict[str, Any]) -> dict[str, Any] | None:
    """Whoever is cast opposite her, resolving and caching once if need be.

    `pick_partner` fills this in at the moment of casting. The lookup stays here
    for sessions whose id was set some other way (an inputs patch, an older
    session): it used to be copy-pasted into both duet turns, which is why the
    panel and the prompt could disagree about who was in the room.
    """
    preset_id = str(_inputs(session).get("partner_preset") or "").strip()
    if not preset_id:
        session.pop("partner_character", None)
        return None
    cached = session.get("partner_character") or {}
    if str(cached.get("character_id") or "") == preset_id or (
        (cached.get("personality") or {}).get("preset_key") == preset_id
    ):
        return cached
    try:
        preset = await presets_db.get_preset(db, preset_id)
    except Exception:
        logger.debug("[muse] partner lookup failed", exc_info=True)
        return None
    if not preset:
        return None
    session["partner_character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": preset_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    return session["partner_character"]


def _rebuild_brief(session: dict[str, Any]) -> None:
    """Two briefs: the full sheet for the seats that act, a digest for the rest.

    Lighting, colour and the audit desk were being handed her summary, inner
    life and tastes on every call. None of it is their craft, and it was the
    most evocative text in their context — so it became the language the whole
    script was written in, and the theme lost.
    """
    inputs = _inputs(session)
    character = session.get("character") or {}
    if not character or not str(inputs.get("theme") or "").strip():
        session["brief"] = ""
        session["brief_lite"] = ""
        return
    common = dict(
        theme=str(inputs.get("theme") or ""),
        style=_style(session),
        framing=_framing(inputs),
        plan=session.get("plan") or {},
        costume=session.get("costume") or {},
        notes=list(session.get("notes") or []),
        # Refusals already carried out drop out of the orders — they are
        # enforced by `drop_banned` and the negative prompt now, and leaving
        # the words in is what kept the crew talking about them.
        carried_out=list(session.get("carried_out") or []),
        removed_now=list(session.get("just_banned") or []),
        restored_now=list(session.get("just_restored") or []),
    )
    # COSTUME is locked craft, not inner life — both the full and digest briefs
    # carry it so every seat (acting or not) re-reads the same outfit.
    session["brief"] = brief_mod.build(
        character, common["theme"], common["style"],
        framing=common["framing"], plan=common["plan"],
        costume=common["costume"], notes=common["notes"],
        carried_out=common["carried_out"], removed_now=common["removed_now"],
        restored_now=common["restored_now"], reference="full",
    )
    session["brief_lite"] = brief_mod.build(
        character, common["theme"], common["style"],
        framing=common["framing"], plan=common["plan"],
        costume=common["costume"], notes=common["notes"],
        carried_out=common["carried_out"], removed_now=common["removed_now"],
        restored_now=common["restored_now"], reference="digest",
    )


# The seats whose craft IS the performance. Only these read her inner life.
_ACTING_ROLES = ("actress", "faces")


def _brief_for(session: dict[str, Any], muse_id: str = "") -> str:
    if crew.role_of(muse_id) in _ACTING_ROLES:
        return str(session.get("brief") or "")
    return str(session.get("brief_lite") or session.get("brief") or "")


def _crew_ids(session: dict[str, Any]) -> list[str]:
    inputs = _inputs(session)
    return crew.resolve_crew(
        preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
        crew_ids=list(inputs.get("crew_ids") or []) or None,
    )


# Long edge the board is scaled to before the VLM sees it. The 300px thumbnail
# is too small to judge composition on, and the full 896x1152 render is a lot of
# tokens to spend once per seat.
_VLM_LONG_EDGE = 768
# One decode per board round, not one per seat.
_BOARD_CACHE: dict[tuple[str, int, str], bytes] = {}


def _vision_model(inputs: dict[str, Any]) -> str:
    """A vision-capable model when one is configured, else the text model."""
    return str(inputs.get("vision_model") or "") or str(inputs.get("model") or "")


def _downscale(raw: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(raw)) as img:
        img = img.convert("RGB")
        img.thumbnail((_VLM_LONG_EDGE, _VLM_LONG_EDGE), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def decode_chat_images(
    raw_list: list[str] | None, *, max_n: int = 1,
) -> list[bytes]:
    """Decode optional chat direction stills (base64) → downscaled JPEG bytes.

    Accepts raw base64 or ``data:image/...;base64,...``. Empty / oversized /
    undecodable entries are skipped. Never raises — bad attachments must not
    kill the turn.
    """
    import base64

    out: list[bytes] = []
    for item in list(raw_list or [])[:max(1, int(max_n))]:
        s = str(item or "").strip()
        if not s:
            continue
        if s.startswith("data:") and "," in s:
            s = s.split(",", 1)[1]
        try:
            raw = base64.b64decode(s, validate=False)
        except Exception:
            continue
        if len(raw) < 32 or len(raw) > 4_000_000:
            continue
        try:
            out.append(_downscale(raw))
        except Exception:
            logger.debug("[muse] direction image decode failed", exc_info=True)
            continue
    return out


async def images_by_sha(db, shas: list[str]) -> list[bytes]:
    """Load stored images small enough to hand to a VLM. Unreadable ones vanish.

    Never raises: a picture that cannot be loaded is a reason to carry on
    without it, never a reason to fail the turn that wanted to look.
    """
    out: list[bytes] = []
    for sha in [s for s in shas if str(s or "").strip()]:
        try:
            docs = await db.get_by_sha256s([sha])
            path = Path(str((docs or [{}])[0].get("path") or ""))
            if not path.is_file():
                continue
            out.append(await asyncio.to_thread(
                lambda p=path: _downscale(p.read_bytes()),
            ))
        except Exception:
            logger.debug("[muse] image %s unreadable", str(sha)[:8], exc_info=True)
    return out


async def board_images(db, session: dict[str, Any], *, limit: int = 1) -> list[bytes]:
    """The board the crew is being asked about, small enough to hand to a VLM.

    Empty when there is no board yet, when the store cannot resolve the image,
    or when anything about reading it fails — a screening that cannot load is a
    reason to keep talking, never a reason to stop the table.
    """
    board = session.get("board") or {}
    shots = [
        str(i.get("image_id") or "") for i in (board.get("images") or [])
        if isinstance(i, dict) and i.get("image_id")
    ]
    shots = shots[-max(1, int(limit)):]
    if not shots or board.get("pending"):
        return []
    sid = str(session.get("session_id") or "")
    rnd = int(board.get("round") or 0)

    out: list[bytes] = []
    for sha in shots:
        key = (sid, rnd, sha)
        if key in _BOARD_CACHE:
            out.append(_BOARD_CACHE[key])
            continue
        try:
            docs = await db.get_by_sha256s([sha])
            path = Path(str((docs or [{}])[0].get("path") or ""))
            if not path.is_file():
                continue
            data = await asyncio.to_thread(
                lambda p=path: _downscale(p.read_bytes()),
            )
        except Exception:
            logger.debug("[muse] board image %s unreadable", sha[:8], exc_info=True)
            continue
        _BOARD_CACHE[key] = data
        out.append(data)

    # Keep the map from growing for the life of the process.
    if len(_BOARD_CACHE) > 24:
        for stale in list(_BOARD_CACHE)[:-8]:
            _BOARD_CACHE.pop(stale, None)
    return out


SCREENING_JA = (
    "あなたはいま、実際に上がったボードを見ている。\n"
    "- まず絵に写っているものを一つ挙げ、台本との差を言う。\n"
    "- 露出は絶対値で判定する（明るすぎ／暗すぎ／ちょうどよい）。"
    "ちょうどよければ光には一切触らない。\n"
    "- 台本に書いたのに写っていないものがあれば、それを直すのが最優先。\n"
    "- 写っているものを褒めるだけの発言はしない。"
)
SCREENING_EN = (
    "You are looking at the board that actually came back.\n"
    "- Name one thing that IS in the picture, and say how it differs from the craft.\n"
    "- Judge exposure in absolutes (too bright / too dark / correct). If it is "
    "correct, do not touch the light at all.\n"
    "- Anything the craft asked for that is not in the frame is the first fix.\n"
    "- Do not spend the turn praising what is already there."
)


def _screening_note(session: dict[str, Any]) -> str:
    locale = str(_inputs(session).get("locale") or "ja")
    return SCREENING_JA if locale.startswith("ja") else SCREENING_EN


async def _run_muse_turn(
    ollama, session: dict[str, Any], muse_id: str, user_prompt: str,
    *, cfg: dict[str, Any], images: list[bytes] | None = None,
) -> tuple[chain.MuseTurn, int]:
    inputs = _inputs(session)
    sid = session["session_id"]
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": muse_id,
        "name": _muse_display_name(session, muse_id),
    })
    started = time.monotonic()
    turn = await chain.run_muse(
        ollama, muse_id=muse_id, user_prompt=user_prompt,
        model=_vision_model(inputs) if images else _text_model(inputs),
        num_ctx=_num_ctx(inputs, cfg),
        identity_tags=_identity_tags(session),
        framing=_framing(inputs),
        # Leak detection always reads the full sheet, even when the seat was
        # only handed the digest — narrowing it would narrow what counts as a leak.
        brief=str(session.get("brief") or ""),
        think=False,
        images=images or None,
        character=session.get("character") or {},
        style=_style(session),
        cast=_cast(session),
        seed=str(session.get("session_id") or ""),
        on_token=_token_publisher(sid, muse_id),
    )
    return turn, int((time.monotonic() - started) * 1000)


def _muse_display_name(session: dict[str, Any], muse_id: str) -> str:
    if crew.role_of(muse_id) == "actress":
        ch = session.get("character") or {}
        p = ch.get("personality") or {}
        locale = str(_inputs(session).get("locale") or "ja")
        if locale.startswith("ja"):
            return str(
                ch.get("name_ja") or p.get("preset_name_ja")
                or ch.get("name") or p.get("preset_name") or "女優"
            )
        return str(ch.get("name") or p.get("preset_name") or "Actress")
    m = crew.MUSES[crew.resolve_member(muse_id)]
    locale = str(_inputs(session).get("locale") or "ja")
    if locale.startswith("ja"):
        # Job plus nickname: two people share 照明, and the log has to say which.
        return f"{m['name_ja']}「{m['nick_ja']}」"
    return f"{m['name']} ({m['nick']})"


def record_ledger(
    session: dict[str, Any], *, muse_id: str, name: str,
    before: str, after: str, ms: int = 0,
) -> dict[str, Any] | None:
    """Note which tags one seat put in, which it took out, and what it cost.

    The session only ever kept the final craft, so a frame carrying tags nobody
    asked for had no way to name the seat that asked. A run that ended in
    `(neck_tension:1.4)`, a school blazer and an extreme close-up could be read
    off the chat only by guessing which speaker meant which tag.

    `ms` is what the turn took. Paired with how much of a seat's work is still
    in the finished prompt, it is the only honest way to decide which jobs are
    worth their wall clock and which two could be one.
    """
    was = identity.tag_names(before)
    now = identity.tag_names(after)
    added = [t for t in now if t not in set(was)]
    dropped = [t for t in was if t not in set(now)]
    if not added and not dropped and not ms:
        return None
    entry = {
        "muse_id": muse_id, "name": name,
        "added": added, "dropped": dropped,
        "ms": int(ms), "at": time.time(),
    }
    session.setdefault("ledger", []).append(entry)
    return entry


STAGE_LOG_MAX = 40


def _stage(session: dict[str, Any], name: str, started: float) -> None:
    """一段いくらかかったかを残す。**判定には使わない。読むためだけ。**

    会話1ターンは実測 24〜38秒。直接測れた呼び出しは前段 2.0 + compile 2.8 +
    彼女 10.0 の**約15秒**で、残りがどこに消えているか分からなかった。
    分からないまま削ると、今日のように**全体の8%を削るために半日**使う。

    `ledger` の `ms` と同じ考え方だが、あちらは席がタグに何をしたかの記録で、
    ここは**壁時計**。デバッグ枠に出す（`clerk_log` と同じ場所）。
    """
    ms = int((time.monotonic() - started) * 1000)
    log = list(session.get("stage_ms") or [])
    log.append({"at": time.time(), "stage": name, "ms": ms})
    session["stage_ms"] = log[-STAGE_LOG_MAX:]
    logger.info("[muse] %s took %dms", name, ms)


def session_seed(session: dict[str, Any]) -> int:
    """One seed for the whole shoot — drawn once, then never again.

    Every ② used to draw a fresh random seed, so two test shots of the same
    script were two different pictures. Measured on a live session: the weave
    was frozen for nine boards, the prompt did not change by one byte, and the
    picture changed every time anyway. The showrunner was reading those
    differences as the studio answering his direction. They were noise.

    A shoot is a series of takes of one picture. Holding the seed is what makes
    「引きにして」 and 「もっと暗く」 legible at all — the only thing that moves
    between two takes should be the words.
    """
    seed = int(session.get("seed") or 0)
    if not seed:
        seed = random.randint(0, (1 << 64) - 1)
        session["seed"] = seed
    return seed


def banned_tags(session: dict[str, Any]) -> list[str]:
    """Everything the Showrunner has taken out of this picture."""
    return [str(t) for t in (session.get("banned") or []) if str(t).strip()]


def drop_banned(session: dict[str, Any], tags: str) -> str:
    """Strip anything the Showrunner has refused, whoever just wrote it.

    This is the enforcement. Telling seats not to reintroduce something means
    naming it in their prompt every turn, which is what kept a refused prop
    alive in the conversation for the rest of the session. A filter needs to
    say nothing at all.
    """
    gone = set(banned_tags(session))
    if not gone or not str(tags or "").strip():
        return tags
    return ", ".join(
        p.strip() for p in str(tags).split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    )


def _apply_turn(
    session: dict[str, Any], turn: chain.MuseTurn, *, ms: int = 0,
) -> dict[str, Any]:
    craft = session.setdefault("craft", {})
    # 制作スタッフ + notebook: seats talk; Scripter alone rewrites TAGS/SCENE.
    # Applying every seat's TAGS was why notes felt like "just chatter" while
    # craft only gained masterpiece fluff or got wiped by empty parses.
    talk_only = uses_notebook(session) and not is_duet(session)
    if not talk_only:
        # Filter before the ledger reads it, so a seat that keeps reaching for a
        # refused tag shows up as never having added it rather than as a fight.
        kept = drop_banned(session, turn.tags)
        record_ledger(
            session, muse_id=turn.muse_id,
            name=_muse_display_name(session, turn.muse_id),
            before=str(craft.get("tags") or ""), after=kept, ms=ms,
        )
        craft["prompt"] = turn.prompt
        craft["tags"] = kept
        craft["scene"] = turn.scene
        if kept != turn.tags:
            _reassemble(session)
        if crew.role_of(turn.muse_id) in ("beat", "spine") or not craft.get("pose_intent"):
            craft["pose_intent"] = turn.pose_intent
    # Wardrobe owns the locked COSTUME in the crewed studio; in a duet she is
    # the only seat, so her own prep turns own it instead. Capture it, take
    # the old outfit out of the craft, make sure the new one is actually in
    # the tags, and re-inject COSTUME so the NEXT turn re-reads it.
    if turn.costume is not None and (
        crew.role_of(turn.muse_id) == "wardrobe" or is_duet(session)
    ):
        prev = session.get("costume") or {}
        costume = dict(turn.costume)
        # The outfit's tags are the GARMENTS slots and nothing else. They used to
        # be the ledger diff of this turn — every tag Wardrobe added — which is
        # not clothing: one session filed the whole pool set under the costume,
        # so a change of clothes would have struck the location.
        garments = brief_mod.garment_tags(costume)
        # A turn that dropped the GARMENTS line tells us nothing about what she
        # has on. Keep the outfit already settled and strike nothing; an empty
        # set here reads as "she is wearing nothing now" and would take the whole
        # outfit out of the craft.
        costume["tags"] = garments or list(prev.get("tags") or [])
        session["costume"] = costume
        if garments:
            strike_dropped_costume(session, prev)
            # Locked COSTUME must land in craft even when seats are talk-only;
            # Scripter owns free tags, not the wardrobe coverage slots.
            _ensure_garments(session, garments)
        _rebuild_brief(session)
        # Keep the living notebook's WEARING in lockstep with Wardrobe so the
        # crew scripter does not recompile last outfit after a costume change.
        if not is_duet(session):
            sync_crew_notebook(session, force_wearing=True)
        craft = session.setdefault("craft", {})
    # Seats can be swapped mid-session. One brought in after the read-through
    # has never seen the script, and answering a note is not a substitute for
    # a first pass over it.
    spoken = session.setdefault("spoken", [])
    if turn.muse_id not in spoken:
        spoken.append(turn.muse_id)
    name = _muse_display_name(session, turn.muse_id)
    say = turn.say or (
        f"（{name}）" if talk_only else f"（{name}が台本を更新した。）"
    )
    msg = _chat_append(
        session, role="muse", text=say,
        muse_id=turn.muse_id, name=name,
        kind="banter" if talk_only else "craft",
        turns=_resolve_duet_turns(session, turn.turns),
    )
    _publish_chat(session["session_id"], msg)
    if not talk_only:
        events.publish(session["session_id"], {
            "type": "craft_updated", "prompt": turn.prompt, "muse_id": turn.muse_id,
        })
    return msg


def _recent_talk(
    session: dict[str, Any], *, limit: int = 6,
    kinds: tuple[str, ...] | None = None,
) -> str:
    lines: list[str] = []
    for m in _chat_rows(session)[-limit:]:
        if m.get("role") != "muse":
            continue
        if kinds and m.get("kind") not in kinds:
            continue
        name = m.get("name") or m.get("muse_id") or "?"
        mark = "（つぶやき）" if m.get("kind") == "banter" else ""
        lines.append(f"- {name}{mark}: {m.get('text')}")
    return "\n".join(lines)


def _wardrobe_rail(session: dict[str, Any], muse_id: str) -> str:
    """The character's usual clothes, for Wardrobe's first turn only.

    This used to be a bare `Outfit: <tags>` line in the brief itself, which every
    seat saw until COSTUME was set — and COSTUME is unset for exactly the turn
    that decides the clothes. A concrete ASCII tag list near the top of the
    prompt beat the garment the theme named, in Japanese, on the unfenced last
    line: her default clothes shipped instead, on every model tried, seven runs
    out of seven.

    So the rail is handed to the one seat it belongs to, once, and the theme is
    asked for first. Order is the fix — the discard rule has to be read before
    the garments it discards, or the tag list wins again.
    """
    if crew.role_of(muse_id) != "wardrobe" or (session.get("costume") or {}):
        return ""
    outfit = [
        str(t) for t in ((session.get("character") or {}).get("outfit_tags") or [])
        if str(t).strip()
    ]
    lines = [
        "WHAT SHE WEARS — settle this before anything else.",
        "1. Read the theme — the final line of the brief above. If it names a "
        "garment, THAT is the outfit. Write it into GARMENTS and stop "
        "reconsidering it.",
        "2. Only if the theme names no clothing, dress her for this place and "
        "hour, starting from the rail below.",
    ]
    if outfit:
        lines.append(
            f"DEFAULT RAIL (what she usually wears — DISCARD IT ENTIRELY if the "
            f"theme named a garment; do not layer it under the new one): "
            f"{', '.join(outfit)}"
        )
    return "\n".join(lines)


def _table_user_prompt(
    session: dict[str, Any], *, muse_id: str = "", note: str = "",
    screening: str = "",
) -> str:
    craft = session.get("craft") or {}
    previous = str(craft.get("prompt") or "")
    pose = str(craft.get("pose_intent") or "")
    base = brief_mod.with_previous(
        _brief_for(session, muse_id), previous, pose=pose, analysis=screening,
    )
    rail = _wardrobe_rail(session, muse_id)
    if rail:
        base = f"{base}\n\n{rail}"
    # Her diary is hers. It goes to the seat she is sitting in and nowhere else —
    # in the brief, the whole table would be reading it.
    if crew.role_of(muse_id) == "actress":
        for block in (
            _memory_block(session),
            _social_block(session),
            _pitch_recommend_block(session),
            _handpost_block(session),
            _caught_block(session),
        ):
            if block:
                base = f"{base}\n\n{block}"
    # The planner already pulled these out of the tag list. The prose is the
    # other half of the prompt and only the seats writing it can clear that.
    struck = [str(s) for s in (session.get("struck") or []) if str(s).strip()]
    if struck:
        base = (
            f"{base}\n\n"
            f"STRUCK FROM THE SET (the plan no longer has these — they belong to "
            f"a place or a moment we have left). Delete them from TAGS and from "
            f"SCENE. Do not describe them, and do not replace them with synonyms:"
            f"\n{', '.join(struck)}"
        )
    # Craft turns only, and only a few. Banter carries no craft and every seat is
    # told to be charming in it, so feeding it back here was a loop with nothing
    # damping it: one image ("the gap between her knees") got restated by six
    # consecutive speakers until it was what the picture was about.
    talk = _recent_talk(session, limit=3, kinds=("craft",))
    if talk:
        base = (
            f"{base}\n\n"
            f"RECENT TABLE TALK (for SAY only — do NOT carry their nouns, "
            f"metaphors, or light/colour adjustments into TAGS/SCENE):\n"
            f"{talk}"
        )
    if note.strip():
        return (
            f"{base}\n\n"
            f"SHOW RUNNER NOTE (総監督 — treat as absolute creative direction):\n"
            f"{note.strip()}\n"
            f"Answer their note. Revise TAGS/SCENE to satisfy it without breaking Carry."
        )
    return base


def _times_spoken(session: dict[str, Any], muse_id: str) -> int:
    return sum(
        1 for m in _chat_rows(session)
        if m.get("muse_id") == muse_id and m.get("kind") == "banter"
    )


def _banter_prompt(
    session: dict[str, Any], *, speaker_id: str, about_id: str, about_text: str,
) -> str:
    locale = str(_inputs(session).get("locale") or "ja")
    about_name = _muse_display_name(session, about_id)
    self_name = _muse_display_name(session, speaker_id)
    talk = _recent_talk(session, limit=4)
    # The Lead gets a different move each time. Left alone the model gave her
    # one — a soft「……しちゃいそう」— and every line she had ended the same way.
    stance = ""
    if crew.role_of(speaker_id) == "actress":
        stance = crew.actress_stance(_times_spoken(session, speaker_id))
    if locale.startswith("ja"):
        return (
            f"あなたは{self_name}。いま{about_name}がこう言った:\n"
            f"「{about_text}」\n\n"
            f"直近の会話:\n{talk or '（まだ少ない）'}\n\n"
            + (f"今回の返し方: {stance}\n\n" if stance else "")
            + "口調どおりに1〜2文で反応して。"
            "台本のTAGS/SCENEは書き換えない。会話だけ。"
        )
    return (
        f"You are {self_name}. {about_name} just said:\n"
        f"\"{about_text}\"\n\n"
        f"Recent talk:\n{talk or '(thin)'}\n\n"
        f"React in 1–2 sentences in voice. Agree, push back, or pile on. "
        f"Chat only — do not rewrite craft."
    )


async def _run_banter(
    ollama, session: dict[str, Any], muse_id: str, *,
    about_id: str, about_text: str, cfg: dict[str, Any],
) -> dict[str, Any]:
    inputs = _inputs(session)
    sid = session["session_id"]
    name = _muse_display_name(session, muse_id)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": muse_id, "name": name,
    })
    try:
        say = await chain.run_banter(
            ollama, muse_id=muse_id,
            user_prompt=_banter_prompt(
                session, speaker_id=muse_id,
                about_id=about_id, about_text=about_text,
            ),
            model=_text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            character=session.get("character") or {},
            on_token=_token_publisher(sid, muse_id),
        )
    except chain.ChainError:
        logger.debug("[muse] banter skipped for %s", muse_id, exc_info=True)
        return {}
    msg = _chat_append(
        session, role="muse", text=say,
        muse_id=muse_id, name=name, kind="banter",
    )
    _publish_chat(sid, msg)
    return msg


def _plan_user_prompt(session: dict[str, Any], *, note: str = "") -> str:
    """Theme + standing orders + whatever place was already settled."""
    inputs = _inputs(session)
    parts = [
        f"Style: {_style(session)}",
        f"Framing: {_framing(inputs)}",
        f"THEME (the situation to plan — this is the whole assignment):\n"
        f"{str(inputs.get('theme') or '').strip()}",
    ]
    orders = brief_mod.orders_block(list(session.get("notes") or []))
    if orders:
        parts.append(orders)
    previous = brief_mod.plan_block(session.get("plan") or {})
    if previous:
        parts.append(
            "PREVIOUS PLAN (keep what still holds; change only what the orders "
            "or the board force you to change):\n" + previous
        )
    if note.strip():
        parts.append(
            "SHOW RUNNER NOTE (総監督 — treat as absolute creative direction):\n"
            f"{note.strip()}\n"
            "Re-settle PLACE / HOUR / LIGHT / ACTION / MUST APPEAR so this note "
            "is simply true. If they asked for a different place, move there — do "
            "not keep the old one alongside it."
        )
    return "\n\n".join(parts)


def _ledger_items(plan: dict[str, Any] | None) -> list[str]:
    return [
        identity.bare_tag(x)
        for x in ((plan or {}).get("must_appear") or [])
        if identity.bare_tag(x)
    ]


def _still_meant(old: str, new_items: list[str]) -> bool:
    """True when a new ledger entry is plainly the same thing renamed.

    `microphone` → `wireless_microphone` is the planner being more specific, not
    the planner throwing the microphone away. Without this, a re-spelled ledger
    would strike its own contents.
    """
    return any(old in new or new in old for new in new_items)


def uses_notebook(session: dict[str, Any]) -> bool:
    """Living notebook owns craft compile — 主演撮り always; 制作スタッフ once seeded."""
    if is_duet(session):
        return True
    return bool(session.get("notebook_craft"))


_GARMENT_KEY_RE = re.compile(r"(?i)\b(top|bottom|feet|extras|hero)\s*=\s*")


def _costume_wearing_line(costume: dict[str, Any] | None) -> str:
    """Plain wearing line for the notebook, from the locked COSTUME card.

    The notebook contract is "short absolute phrases, not paragraphs", and
    wearing is the one field a remove request has to rewrite. Handed the whole
    card — silhouette, colourway, fabric AND the `top=… / bottom=… / feet=…`
    ledger — the scripter kept editing inside that shape instead of restating
    the outfit, so a live session's wearing read
    `top=white_shirt, navy_collar / bottom=pleated_skirt / cardigan;` five
    turns in. Garments only, keys stripped. The texture and the palette stay
    in COSTUME for the brief; they are not what the notebook holds.
    """
    data = costume or {}
    bits: list[str] = []
    for key in ("hero", "layers", "garments"):
        val = data.get(key)
        if isinstance(val, (list, tuple)):
            val = ", ".join(str(v).strip() for v in val if str(v).strip())
        val = _GARMENT_KEY_RE.sub("", str(val or "")).replace("/", ",")
        for piece in val.split(","):
            piece = piece.strip(" ;.")
            if piece and piece.lower() not in {b.lower() for b in bits}:
                bits.append(piece)
    if not bits:
        bits = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()][:6]
    # HERO, LAYERS and GARMENTS are one outfit at three grains, so joining them
    # lists the same coat twice under two names — and a request to take off the
    # coat then has no single referent. See `brief.tidy_wearing`.
    return brief_mod.tidy_wearing(", ".join(bits))[:120]


def _plan_scene_line(plan: dict[str, Any] | None) -> str:
    """Where and when. Not how it is lit, and never what she is doing.

    The scripter's contract is `scene = short place + time`. PLACE, HOUR,
    LIGHT and ACTION used to be joined into it and cut at 120 characters, so
    the pose was written in two fields with no rule for which won, and the
    light was one truncation away from disappearing. ACTION seeds beat; LIGHT
    is the gaffer's owned slot in CREW LOOK (`crew.CRAFT_SLOTS`).
    """
    data = plan or {}
    bits = [str(data.get(k) or "").strip() for k in ("place", "hour")]
    return " · ".join(b for b in bits if b)[:120]


def sync_crew_notebook(
    session: dict[str, Any], *, force_wearing: bool = False, force_scene: bool = False,
    activate: bool | None = None,
) -> None:
    """Mirror PLAN/COSTUME into the living notebook for 制作スタッフ.

    Seats still banter and may draft tags; the scripter (and densify) treat the
    notebook as absolute shot truth the same way 主演撮り does — so a clothing
    or place note can actually move the picture instead of fighting CARRY.

    ``activate`` flips ``notebook_craft`` (talk-only seats + scripter-owned TAGS).
    Plan turns must mirror scene without activating — otherwise the opening
    wardrobe/lens/actress pass becomes talk-only and never drafts craft.
    """
    if is_duet(session):
        return
    # Seed from PLAN/COSTUME BEFORE the legacy facet migration, not after.
    # `notebook.migrate` fills an empty notebook from the facet table at 400–800
    # characters a field — the seat's whole POSE sentence lands in beat, the
    # digest lands in scene. Those are paragraphs in fields the scripter and the
    # weave both read as short absolute phrases, and a beat that already spends
    # a sentence describing how she is leaning is one 「座って」 cannot move —
    # the scripter edits inside it instead. Seeding first makes `has_shot` true, so
    # migrate returns early and only the legacy carry-overs it still owns run.
    nb = notebook_mod.of(session)
    patch: dict[str, Any] = {}
    # The light now has a field of its own, and that is where PLAN's intent
    # goes. Ownership, so the two do not fight:
    #   notebook.light   — what the light IS. The showrunner and the planner
    #                      move it; it is absolute and it survives every turn.
    #   crew_look.LIGHT  — how the gaffer renders it. Craft, under the
    #                      notebook, never contradicting it.
    light = str((session.get("plan") or {}).get("light") or "").strip()
    if light and not str(nb.get("light") or "").strip():
        patch["light"] = light[:notebook_mod.LIGHT_MAX_CHARS]
    scene_line = _plan_scene_line(session.get("plan"))
    if scene_line and (force_scene or not str(nb.get("scene") or "").strip()):
        patch["scene"] = scene_line
    elif not str(nb.get("scene") or "").strip():
        craft_scene = str((session.get("craft") or {}).get("scene") or "").strip()
        if craft_scene:
            patch["scene"] = craft_scene[:120]
    wearing_line = _costume_wearing_line(session.get("costume"))
    if wearing_line and (force_wearing or not str(nb.get("wearing") or "").strip()):
        patch["wearing"] = wearing_line
    # atmosphere is mood, in English, and nothing else — no clock, no objects,
    # no place nouns (`chain.SCRIPTER_SYSTEM` field contracts). The raw theme
    # was seeded straight into it, so a Japanese line naming a place and a
    # garment sat in the mood field and went into every weave — measured live,
    # for four turns, until the scripter happened to rewrite it. The theme
    # reaches the models on its own (`_theme_for_models`); it is not a mood.
    if not str(nb.get("frame") or "").strip():
        framing = _framing(_inputs(session))
        if framing and framing != "auto":
            patch["frame"] = framing.replace("_", " ")
    if not str(nb.get("beat") or "").strip():
        # PLAN's ACTION first: it is one clause by contract. A seat's POSE line
        # is a whole sentence, and seeded raw it became a paragraph the scripter
        # edited around instead of replacing —「座って」could not move it.
        pose = str((session.get("plan") or {}).get("action") or "").strip() or str(
            (session.get("craft") or {}).get("pose_intent") or ""
        ).strip()
        if pose:
            patch["beat"] = pose.split(".")[0].strip()[:80]
    if patch:
        notebook_mod.apply_patch(nb, patch)
        session["notebook"] = nb
    notebook_mod.migrate(session)
    if activate is True or (activate is None and session.get("notebook_craft")):
        was_off = not session.get("notebook_craft")
        session["notebook_craft"] = True
        # Switching the room onto the notebook does not invalidate the craft the
        # opening seats just wrote — the notebook was seeded FROM it. Mark it
        # compiled at this rev so the first still keeps their tags, and the
        # weave takes over from the first note that actually moves the shot.
        if was_off and str((session.get("craft") or {}).get("prompt") or "").strip():
            session["notebook_rev_compiled"] = int(
                notebook_mod.of(session).get("rev") or 0
            )
    name = _muse_display_name(session, "actress") if "actress" in _crew_ids(session) else ""
    session["digest"] = notebook_mod.summary_for_muse(
        notebook_mod.of(session), name_a=name or "Lead",
    )


async def _run_crew_scripter(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """Compile crew craft from the notebook after seats have spoken."""
    sync_crew_notebook(session)
    if ollama is None:
        return None
    try:
        return await _run_duet_scripter(db, ollama, session, text, cfg=cfg)
    except Exception:
        logger.warning("[muse] crew scripter failed; seats' draft kept", exc_info=True)
        session["craft_dirty"] = True
        return None


def on_facets(session: dict[str, Any]) -> bool:
    """True when facet-table helpers are available (duet sessions).

    Live chat/prep for duet go through `uses_notebook` + the scripter. The
    facet router and scoped prep remain callable for migration and unit tests;
    they are simply not invoked from `post_duet_chat` / notebook prep.
    """
    return is_duet(session)


def _reassemble(session: dict[str, Any]) -> None:
    """Rebuild the Comfy positive from the current shot.

    On the facet path the tags and the prose are *derived* — the facet table is
    the shot and `craft` is the view of it that the render, the ledger, the
    report and the panel all read without knowing the difference. The prose is
    the composed paragraph when one was composed from exactly this table, and
    the facet sentences joined otherwise, so the positive is never blocked on a
    model call: the panel can show what was just asked for the moment it lands.

    On the notebook path, craft tags/scene are owned by the scripter compile —
    only the positive string is refreshed from those fields.
    """
    craft = session.setdefault("craft", {})
    if uses_notebook(session) and int((session.get("notebook") or {}).get("rev") or 0) > 0:
        # Crop / wardrobe conflict lives in scrub; assemble only injects framing.
        nb = notebook_mod.of(session)
        craft["tags"] = notebook_mod.scrub_craft_tags(
            str(craft.get("tags") or ""),
            wearing=str(nb.get("wearing") or ""),
            scene=str(nb.get("scene") or ""),
            beat=str(nb.get("beat") or ""),
            struck=notebook_mod.struck_tokens(session),
            wearing_b=str(nb.get("wearing_b") or ""),
            beat_b=str(nb.get("beat_b") or ""),
            frame=str(nb.get("frame") or ""),
            banned=set(banned_tags(session)),
        )
        craft["prompt"] = identity.assemble_positive(
            _identity_tags(session), str(craft.get("tags") or ""),
            str(craft.get("scene") or ""),
            framing=_shot_framing(session), style=_style(session),
            subject=identity.subject_tags(_cast(session)), cast=_cast(session),
            own=_sides(session, str(craft.get("tags") or "")),
        )
        return
    table = facets.table_of(session) if on_facets(session) else None
    # An empty table is not an empty shot — it is a shot nobody has written into
    # the table yet. Deriving from it would blank a craft that a turn had just
    # filled in the old shape, so the projection only takes over once there is
    # something in it to project.
    if table is not None and facets.table_rev(table):
        composed = session.get("composed") or {}
        scene = str(composed.get("scene") or "").strip()
        if not scene or int(composed.get("rev") or -1) != facets.table_rev(table):
            scene = facets.nl_join(table)
        craft["tags"] = facets.all_tags(table)
        craft["scene"] = scene
        craft["pose_intent"] = str(table["pose"].get("nl") or "")
        # PLAN and COSTUME are not a second source of truth any more; they are
        # this table in the shape `brief.plan_block` / `costume_block` expect,
        # refreshed here so the brief cannot fall behind the shot.
        session["plan"] = facets.to_plan(table)
        session["costume"] = facets.to_costume(table)
    craft["prompt"] = identity.assemble_positive(
        _identity_tags(session), str(craft.get("tags") or ""),
        str(craft.get("scene") or ""),
        framing=_framing(_inputs(session)), style=_style(session),
        subject=identity.subject_tags(_cast(session)), cast=_cast(session),
        own=_sides(session, str(craft.get("tags") or "")),
    )


def garment_tags(session: dict[str, Any]) -> list[str]:
    """What she currently has on, as tags. The outfit's owner is Wardrobe alone,
    so this is the set every other removal path has to leave standing."""
    return brief_mod.garment_tags(session.get("costume") or {})


def _ensure_garments(session: dict[str, Any], garments: list[str]) -> list[str]:
    """Put back any garment COSTUME names that the turn forgot to write in TAGS.

    COSTUME is prose the seats re-read; the render only ever sees tags. A garment
    that exists in one and not the other is the outfit being left to the
    checkpoint, which is the failure the COSTUME block was built to end.

    Refused garments are not put back. This was the one way past ``drop_banned``:
    a Showrunner who said「上着脱いで」had the jacket struck from the craft, and
    then the next wardrobe turn re-read a COSTUME block that still named it and
    stapled it straight back on. It came back as many times as she asked for it
    to go.
    """
    craft = session.setdefault("craft", {})
    gone = set(banned_tags(session))
    have = set(identity.tag_names(str(craft.get("tags") or "")))
    missing = [t for t in garments if t not in have and t not in gone]
    if not missing:
        return []
    parts = [p.strip() for p in str(craft.get("tags") or "").split(",") if p.strip()]
    craft["tags"] = ", ".join(parts + missing)
    _reassemble(session)
    return missing


def apply_removals(
    session: dict[str, Any], remove: list[str], restore: list[str],
) -> tuple[list[str], list[str]]:
    """Carry out a refusal: take it out, and keep it out.

    A refusal used to be the one instruction the studio could not perform. It
    was stored as a standing order in the Showrunner's own words and re-read by
    every seat on every turn, so the refused noun stayed in front of everyone
    forever and the crew kept discussing it; no code path could delete a prop
    the art department had added; and the sampler never heard about it at all,
    because the negative prompt is built from settings and never from what the
    Showrunner said. Saying "no" made the thing more present, not less.

    So a refusal changes state instead of adding text. The tag comes out now,
    ``drop_banned`` keeps it out however many times a seat reaches for it, and
    ``negative_for`` hands it to the sampler — the one place in the pipeline
    where "not this" actually works.
    """
    gone = set(banned_tags(session))
    freed = [t for t in restore if t in gone]
    added = [t for t in remove if t not in gone]
    if not freed and not added:
        return [], []

    gone.update(added)
    gone.difference_update(freed)
    # Ordered for a stable negative prompt and a readable panel.
    session["banned"] = sorted(gone)
    # Only this turn's refusals are shown to the crew, and only on this turn —
    # the seats answering the note need to know why something vanished, and
    # nobody after them needs the noun at all.
    session["just_banned"] = list(added)
    session["just_restored"] = list(freed)

    craft = session.setdefault("craft", {})
    before = str(craft.get("tags") or "")
    if on_facets(session):
        # The craft is derived here, so striking it would last exactly until the
        # next reassemble put the tag back from the table. A refusal has to
        # reach the state, not the view of it — otherwise the refused thing goes
        # on being handed to every turn as part of the shot.
        table = facets.table_of(session)
        stale = facets.strike(session, gone)
        # A part whose tags just changed is a part whose sentence is now wrong,
        # and the sentence is half the prompt. The old code told the next seats
        # outright ("STRUCK FROM THE SET") and hoped; here the stale prose is
        # dropped and the part is queued for rewrite, so nothing downstream is
        # ever handed a sentence naming a thing that is no longer in the shot.
        if stale:
            routed = session.setdefault("routed", [])
            routed.extend(n for n in stale if n not in routed)
        _reassemble(session)
    else:
        craft["tags"] = drop_banned(session, before)
        if craft["tags"] != before:
            _reassemble(session)
    if craft.get("tags") != before:
        record_ledger(
            session, muse_id="showrunner", name="総監督",
            before=before, after=str(craft.get("tags") or ""),
        )
    return added, freed


def directives_block(session: dict[str, Any], *, only: list[str] | None = None) -> str:
    """The Showrunner's direction, one line per part, newest at the bottom.

    This is the whole of what the standing orders used to be, and it does not
    grow. `orders_block` rendered every note ever said into every brief, newest
    first, and left the crew to work out which of seventeen absolute
    instructions won. Here a second camera order simply replaces the first, so a
    twenty-turn session hands over the same eight lines a two-turn session does.
    """
    data = session.get("directives") or {}
    lines: list[str] = []
    for name, label in facets.FACETS:
        if only is not None and name not in only:
            continue
        text = str((data.get(name) or {}).get("text") or "").strip()
        if text:
            lines.append(f"- {label}: {text}")
    if not lines:
        return ""
    return "\n".join([
        "SHOWRUNNER DIRECTION (総監督 said these and they stand until they are "
        "said again):",
        *lines,
    ])


async def set_facet_lock(
    db, session: dict[str, Any], facet: str, locked: bool,
) -> dict[str, Any]:
    """Pin one part of the shot, or let it move again."""
    if not on_facets(session):
        raise MuseError(_msg(
            session,
            ja="この撮影では固定できません（主演撮りだけの機能です）。",
            en="Parts can only be pinned in a lead shoot.",
        ))
    try:
        facets.set_lock(session, facet, locked)
    except ValueError as exc:
        raise MuseError(f"unknown facet: {facet}") from exc
    await session_db.save(db, session)
    return session


async def route_note(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> tuple[list[str], str]:
    """Work out which parts of the shot a note changes, and record it.

    Returns (every part the note is ABOUT — locked or not — and the standing
    rule it added). An empty list is the normal answer for「いい感じ」and means
    the shot is untouched.

    The caller uses this return value to decide whether the note is a
    REPLACEMENT (skip the strike clerk) or an unroutable REFUSAL (run it). A
    locked part still has to come back in this list even though nothing about
    it is written: a note the router recognised as being about the camera is
    replacement-shaped whether or not the camera happens to be pinned, and
    routing it to the refusal clerk instead — because the pin quietly emptied
    the list — was itself a bug (see 2026-08-11 e2e run, turn 15: 「真横から
    撮って」while the camera was locked fell through to the clerk, which read
    the note as retiring `from_front` and struck it out of the locked camera
    facet anyway, because a refusal is allowed to override a pin. The note was
    never a refusal; the lock only looked like one to the branch that decides).
    """
    if not on_facets(session) or ollama is None or not text.strip():
        return [], ""
    inputs = _inputs(session)
    table = facets.table_of(session)
    partner_character = await _partner_character(db, session)
    char_a = session.get("character") or {}
    name_a = str(char_a.get("name_ja") or char_a.get("name") or "")
    name_b = ""
    label_names: dict[str, str] | None = None
    if partner_character:
        name_b = str(partner_character.get("name_ja") or partner_character.get("name") or "")
        label_names = {"costume_b": name_b, "pose_b": name_b, "expression_b": name_b}
    try:
        named, lines, standing, digest = await chain.run_route(
            ollama, note=text, table_block=facets.table_block(table, names=label_names),
            current_digest=str(session.get("digest") or ""),
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            name_a=name_a, name_b=name_b,
        )
    except Exception:
        logger.warning("[muse] route turn failed; nothing routed", exc_info=True)
        return [], ""

    writable = [n for n in named if not table[n].get("locked")]
    session["locked_conflicts"] = [n for n in named if n not in writable]
    directives = session.setdefault("directives", {})
    now = time.time()
    for name in writable:
        # The clerk was asked for the finished value; when it gave none, the
        # note's own words stand in. Worst case is today's behaviour, scoped to
        # the part it is about.
        directives[name] = {"text": lines.get(name) or text.strip(), "at": now}
    if standing:
        rules = session.setdefault("standing", [])
        if standing not in rules:
            rules.append(standing)
    if digest:
        # Rewritten, not appended — "added, then decided against" collapses to
        # one line instead of surviving as two contradictory facts. `digest`
        # is "" whenever the model left it unchanged, so the old value stands.
        # A malformed revision (a change-annotation baked in, a bare tag list
        # standing in for a sentence) is treated the same way: this is the
        # one thing every future turn is told to prioritise over the
        # conversation itself, so a bad rewrite here does more damage than a
        # bad rewrite anywhere else in the session.
        cleaned = identity.sane_prose(digest)
        if cleaned:
            session["digest"] = cleaned
        else:
            logger.warning(
                "[muse] refused malformed digest, kept prior value: %r",
                digest[:120],
            )
    session["routed"] = writable
    return named, standing


# A refusal names one thing, or a few. Past this, the clerk has read a camera
# note as "everything else is out" — measured live at 25 and 27 tags struck
# from one line — and a strike is permanent: it filters every later turn and
# goes into the negative prompt.
MAX_STRIKE = 6


def _sane_strike(session: dict[str, Any], picked: list[str]) -> list[str]:
    """Drop strikes that contradict the shot, and refuse a runaway list.

    Measured live on 「もう一度寄って。手元だけ見せて。」— a crop instruction —
    the clerk struck 27 tags including `cafe`, `wooden_table`, `window` and the
    sweater she was wearing. Those went into the negative prompt, so the
    sampler spent the rest of the session being told not to draw the room the
    showrunner was shooting in.

    The notebook is the shot. Nothing it currently names can be struck: if
    WEARING says she is in a sweater, the sweater is not gone, whatever the
    clerk read into a note about the crop.
    """
    if not picked:
        return []
    live = notebook_mod.shot_tokens(notebook_mod.of(session))
    kept = [
        t for t in picked
        if not (notebook_mod.wearing_tokens(t) & live)
        and identity.bare_tag(t) not in live
    ]
    blocked = [t for t in picked if t not in kept]
    if len(kept) > MAX_STRIKE:
        logger.info(
            "[muse] refusing runaway strike (%d tags): %s",
            len(kept), ", ".join(kept[:12]),
        )
        return []
    # One blocked item is a removal arriving a beat early — the showrunner
    # said「帽子外して」and the hat is still in WEARING, because the compile
    # that takes it off has not run yet. Several blocked items is a different
    # thing: the clerk has listed the frame rather than a refusal, and the
    # rest of that same read cannot be trusted either.
    if len(blocked) >= 2:
        logger.info(
            "[muse] strike read the whole frame; dropping it: %s",
            ", ".join(blocked[:12]),
        )
        return []
    if blocked:
        logger.info("[muse] strike blocked by the notebook: %s", ", ".join(blocked))
    return kept


def _note_standing(session: dict[str, Any], text: str) -> None:
    """常設の指示に一行足す。**同じ行を二度は積まない。**

    制作スタッフの部屋は、一つの note を `take_note` と `_run_crew_scripter`
    （中で `_run_duet_scripter` が同じ行を積む）の両方が通る。実測で、監督の
    一行ごとに `notes` が2件ずつ増えていた:

        notes (4件):
           ・夕方の公園、ブランコで撮ろう。
           ・夕方の公園、ブランコで撮ろう。
           ・髪が風でちょっと乱れてる感じにしよう。
           ・髪が風でちょっと乱れてる感じにしよう。

    `notes` は**常設の指示**なので、二度積めばその指示が二重に効く。主演撮り
    では片方しか走らないので出ていなかった ―― 部屋によって重みが変わる。

    同じ行が二度来ても、常設の指示としては既に立っている。積み直す意味は無い。
    """
    notes = session.setdefault("notes", [])
    if notes and notes[-1] == text:
        return
    notes.append(text)


async def take_note(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Record a Showrunner note, and carry out whatever it refuses.

    Strike runs only when the note looks like undress / delete / restore
    language — a light gate, not a second LLM. Notes that only move pose or
    place skip the tax; a miss still has compile `wearing_drop` + scrub.
    """
    _note_standing(session, text)
    notes = session.setdefault("notes", [])
    index = len(notes) - 1
    session["just_banned"] = []
    session["just_restored"] = []

    removed: list[str] = []
    restored: list[str] = []
    if ollama is not None and _note_looks_like_strike(text):
        inputs = _inputs(session)
        try:
            picked, back = await chain.run_strike(
                ollama, note=text,
                tags=identity.tag_names(str((session.get("craft") or {}).get("tags") or "")),
                removed=banned_tags(session),
                model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            )
        except Exception:
            logger.warning("[muse] strike turn failed; nothing removed", exc_info=True)
            picked, back = [], []
        removed, restored = apply_removals(session, _sane_strike(session, picked), back)

    if removed:
        # The note's own words drop out of the standing orders from the next
        # turn on. Its effect is a filter and a negative prompt now, and leaving
        # the refused noun in front of every seat is what kept them talking
        # about it for the rest of the session.
        session.setdefault("carried_out", []).append(index)
    # What was struck is state, not dialogue. Printing it put lines like
    # 「（外しました: sitting、close-up、profile、steady_gaze、motionless）」 into
    # a conversation where the showrunner had just said one sentence in
    # Japanese — the machine talking to itself in front of the room. The panel
    # reads `struck` / `banned` from the session and can show it as state.
    if removed or restored:
        events.publish(session["session_id"], {
            "type": "struck_changed",
            "removed": list(removed),
            "restored": list(restored),
        })

    _rebuild_brief(session)
    return removed, restored


_STRIKE_NOTE_RE = re.compile(
    r"(?i)"
    r"(?:脱[いがせ]|外[しす]|取[りっ]?[除っ]|消[しす]|捨[て]|やめ[てる]|禁止|"
    r"いらない|はず[しす]|抜[いき]|戻[しす]|なし|無し|もういい|使わ|今後一切|"
    r"\b(?:take\s*off|remove|drop|ban|without|no\s+more|get\s+rid|"
    r"strike|restore|put\s+back|bring\s+back|don'?t\s+use|do\s+not\s+use)\b)"
)


def _note_looks_like_strike(text: str) -> bool:
    """Cheap gate: undress / delete / restore wording before spending an LLM."""
    return bool(_STRIKE_NOTE_RE.search(str(text or "")))


def strike_dropped_props(
    session: dict[str, Any], previous_plan: dict[str, Any] | None,
) -> list[str]:
    """Take the old ledger's props out of the craft when the planner drops them.

    CARRY tells every seat to KEEP setting objects once they exist, which is a
    ratchet with no release: a note that moved the shoot somewhere else left the
    previous location's props sitting in the craft, and clearing them out was
    manual work for the Showrunner on every single note.

    Only what the *planner* listed and then dropped is struck. Anything the art
    department added on top of the ledger belongs to the room it dressed and
    survives — that dressing is the part of the picture that works.
    """
    was = _ledger_items(previous_plan)
    now = _ledger_items(session.get("plan"))
    if not was:
        return []
    # Clothes are not the planner's to drop. A garment that reaches MUST APPEAR
    # is a mistake the COSTUME header already warns about ("a garment word in
    # MUST APPEAR is an object on the floor"), and it must not become a way for
    # a change of scene to undress her — holding the outfit while the place moves
    # is a thing the Showrunner does on purpose.
    worn = set(garment_tags(session))
    struck = [
        t for t in was
        if t not in now and t not in worn and not _still_meant(t, now)
    ]
    if not struck:
        return []

    craft = session.setdefault("craft", {})
    gone = set(struck)
    kept = [
        p.strip() for p in str(craft.get("tags") or "").split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    ]
    before = str(craft.get("tags") or "")
    craft["tags"] = ", ".join(kept)
    _reassemble(session)
    # The prose still names them, and the tag list is only half the prompt. The
    # seats that write next are told outright, which is the only thing that gets
    # them out of SCENE.
    session["struck"] = struck
    record_ledger(
        session, muse_id=_cast_in_role(_crew_ids(session), "plan") or "plan",
        name=_muse_display_name(session, "plan"),
        before=before, after=craft["tags"],
    )
    return struck


def strike_dropped_costume(
    session: dict[str, Any], previous_costume: dict[str, Any] | None,
) -> list[str]:
    """Take the old outfit's tags out of the craft when Wardrobe rebuilds COSTUME.

    The §2-5 release valve: when the Showrunner says "change the clothes",
    Wardrobe writes a new COSTUME and last outfit's garments must not ride
    alongside the new ones. Mirrors ``strike_dropped_props`` — only the PREVIOUS
    costume's own tag set is struck (a known set, no dictionary), and
    ``_still_meant`` protects a rename (skirt → pleated_skirt).

    That tag set is the GARMENTS slots now, so this strikes clothes and only
    clothes. It was the ledger diff of Wardrobe's turn, which meant the pool she
    happened to be standing beside was filed as part of her outfit and came off
    with it.
    """
    was = [
        identity.bare_tag(t)
        for t in (previous_costume or {}).get("tags", [])
        if identity.bare_tag(t)
    ]
    now = (session.get("costume") or {}).get("tags", [])
    if not was:
        return []
    struck = [t for t in was if t not in now and not _still_meant(t, now)]
    if not struck:
        return []

    craft = session.setdefault("craft", {})
    gone = set(struck)
    kept = [
        p.strip() for p in str(craft.get("tags") or "").split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    ]
    before = str(craft.get("tags") or "")
    craft["tags"] = ", ".join(kept)
    _reassemble(session)
    prior = list(session.get("struck") or [])
    session["struck"] = prior + [t for t in struck if t not in prior]
    record_ledger(
        session, muse_id=_cast_in_role(_crew_ids(session), "wardrobe") or "wardrobe",
        name=_muse_display_name(session, "wardrobe"),
        before=before, after=craft["tags"],
    )
    return struck


async def _run_plan_turn(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any], note: str = "",
) -> bool:
    """Settle the situation. Returns True when the plan changed.

    Runs before anyone describes anything, and again whenever the Showrunner
    says something — a note that only reached the turn answering it was outvoted
    by the original theme on every call after that, so「make it X」never became
    the thing the render was of.
    """
    if ollama is None:
        return False
    inputs = _inputs(session)
    sid = session["session_id"]
    mid = _cast_in_role(_crew_ids(session), "plan") or crew.DEFAULT_MEMBER["plan"]
    images = await board_images(db, session)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": mid,
        "name": _muse_display_name(session, mid),
    })
    try:
        plan = await chain.run_plan(
            ollama, muse_id=mid,
            user_prompt=_plan_user_prompt(session, note=note),
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            images=images or None,
            seed=str(sid),
            on_token=_token_publisher(sid, mid),
        )
    except chain.ChainError:
        logger.warning("[muse] plan turn produced nothing", exc_info=True)
        return False
    if not plan:
        logger.info("[muse] planner answered without labelled lines; keeping plan")
        return False

    blind = bool(plan.pop("blind", False))
    say = str(plan.pop("say", "") or "")
    previous_plan = session.get("plan") or {}
    # A planner answering PLACE / HOUR / LIGHT / ACTION and no ledger is a line
    # it did not retype, not a room that has been emptied. Read as an empty
    # ledger it struck all twelve props from a karaoke booth — including the
    # wireless microphone the Showrunner had just asked for by name — and left
    # every later seat with nothing to be audited against.
    if not plan.get("must_appear") and previous_plan.get("must_appear"):
        plan["must_appear"] = list(previous_plan["must_appear"])
    # The planner no longer has a clothing line; what she wears lives in COSTUME,
    # owned by Wardrobe. A stray `wearing` from an old session is dropped.
    plan.pop("wearing", None)
    session["plan"] = plan
    session.pop("struck", None)
    struck = strike_dropped_props(session, previous_plan)
    _rebuild_brief(session)
    if not is_duet(session):
        # Mirror, do not overwrite. The planner re-states PLACE/HOUR/LIGHT on
        # every note whether or not it moved, so forcing scene each time walked
        # the scripter's live scene back to the plan's wording — a note that
        # only changed the hour reinstated the place it had just left.
        moved = str((previous_plan or {}).get("place") or "").strip().lower() != str(
            plan.get("place") or ""
        ).strip().lower()
        sync_crew_notebook(session, force_scene=moved)
    if struck:
        # State, not dialogue — same reason as `take_note`. A list of tag names
        # in the chat is the machine talking to itself in front of the room.
        events.publish(sid, {
            "type": "struck_changed", "removed": list(struck), "restored": [],
        })
    if say:
        msg = _chat_append(
            session, role="muse", text=say, muse_id=mid,
            name=_muse_display_name(session, mid), kind="craft",
        )
        _publish_chat(sid, msg)
    if blind and images:
        _note_blind(session)
    session_db.log(session, "plan", str(plan.get("place") or ""))
    return True


def _note_blind(session: dict[str, Any]) -> None:
    """Say out loud that the board did not reach the model."""
    if session.get("_blind_said"):
        return
    session["_blind_said"] = True
    locale = str(_inputs(session).get("locale") or "ja")
    msg = _chat_append(
        session, role="system", name="Studio",
        text=(
            "このモデルは絵を読めないようなので、ボードは渡さずテキストだけで進めます。"
            "vision_model に画像を読めるモデルを指定すると、班が実際の絵を見て話せます。"
            if locale.startswith("ja") else
            "This model could not read the board — continuing on text alone. "
            "Set vision_model to an image-capable model so the crew can see it."
        ),
    )
    _publish_chat(session["session_id"], msg)


def _banter_mode(session: dict[str, Any]) -> str:
    mode = str(_inputs(session).get("banter_mode") or "light").strip().lower()
    return mode if mode in ("light", "full", "off") else "light"


def _cast_in_role(crew_ids: list[str], role: str) -> str | None:
    """Whoever is doing that job in this cast, if anyone is."""
    return next((m for m in crew_ids if crew.role_of(m) == role), None)


def _last_lead_say(session: dict[str, Any]) -> str:
    """Her most recent spoken line — what the floor is answering."""
    for msg in reversed(_chat_rows(session)):
        if (
            msg.get("role") == "muse"
            and crew.role_of(str(msg.get("muse_id") or "")) == "actress"
            and msg.get("kind") != "banter"
        ):
            return str(msg.get("text") or "")
    return ""


def _pick_banter_reactor(
    session: dict[str, Any], crew_ids: list[str], *,
    current: str, previous: str | None, index: int,
) -> str | None:
    """Who heckles after a craft pass. light mode keeps Ollama call counts sane."""
    mode = _banter_mode(session)
    if mode == "off":
        return None
    # light: every other pass, or always after the actress (personality beat).
    if mode == "light" and crew.role_of(current) != "actress" and index % 2 == 0:
        return None
    # The Lead gets a fixed share rather than third place in a fallback list
    # that almost never ran — `previous` took nearly every heckle, and she came
    # out of a full eighteen-seat session with three lines.
    lead = _cast_in_role(crew_ids, "actress")
    if lead and lead != current and index % 4 == 1:
        return lead
    if previous and previous != current and previous in crew_ids:
        return previous
    for role in ("hook", "actress", "faces", "spine", "beat"):
        mid = _cast_in_role(crew_ids, role)
        if mid and mid != current:
            return mid
    return None


def _pick_extra_heckler(
    session: dict[str, Any], crew_ids: list[str], *,
    current: str, reactor: str | None, index: int,
) -> str | None:
    """Second heckler — full mode only (too expensive for local Ollama otherwise)."""
    if _banter_mode(session) != "full":
        return None
    if index % 3 != 2:
        return None
    for role in ("actress", "hook", "faces", "cutout", "propshop"):
        mid = _cast_in_role(crew_ids, role)
        if mid and mid not in (current, reactor):
            return mid
    return None


# ── open the table ──────────────────────────────────────────────────────────
# The seats that meet before anything is drawn: someone to settle where and
# when, someone to dress her, her, and a camera. Everyone else waits for a frame
# to argue with.
#
# The table used to open with all eighteen and no picture anywhere, and the
# result was a run where「カラオケボックスで歌っている」became a live house: twenty
# turns of prose agreeing with each other, and the Showrunner then spent the
# session deleting props that had accumulated in the dark. A real studio shoots
# a still first and talks about the still.
#
# Wardrobe joined this set because the outfit had no owner in the opening: the
# camera, writing first into an empty craft, authored the clothes, and a garment
# the theme named ended up layered under the character's default clothes. It runs
# FIRST now (dress her, then frame her) — see OPENING_SEQUENCE. Being here takes
# it OUT of act two (`without=OPENING_ROLES`), which is fine: the COSTUME it
# sets is locked, so act two re-reads it rather than re-deriving it.
OPENING_ROLES: tuple[str, ...] = ("wardrobe", "actress", "lens")
# Dressing order for the opening: Wardrobe dresses her, the camera frames the
# dressed figure, she acts last. `_writing_seats(only=...)` returns cast order
# (ROLE_ORDER: lens before wardrobe), so the opening sorts by this explicitly.
OPENING_SEQUENCE: tuple[str, ...] = ("wardrobe", "lens", "actress")


async def _craft_pass(
    db, ollama, session: dict[str, Any], cast: list[str], seats: list[str], *,
    cfg: dict[str, Any], images: list[bytes] | None = None,
    screening: str = "", note: str = "", first_index: int = 0,
) -> str:
    """Run these seats in order, with the banter that goes between them."""
    previous: str | None = None
    last_say = ""
    for offset, muse_id in enumerate(seats):
        index = first_index + offset
        turn, ms = await _run_muse_turn(
            ollama, session, muse_id,
            _table_user_prompt(
                session, muse_id=muse_id, note=note, screening=screening,
            ),
            cfg=cfg, images=images,
        )
        msg = _apply_turn(session, turn, ms=ms)
        last_say = str(msg.get("text") or "")
        if turn.blind and images:
            _note_blind(session)
            images = []
            screening = ""
        if crew.role_of(muse_id) == "actress":
            await _after_actress_spoke(db, session)
        await session_db.save(db, session, publish=False)

        reactor = _pick_banter_reactor(
            session, cast, current=muse_id, previous=previous, index=index,
        )
        if reactor and last_say:
            await _run_banter(
                ollama, session, reactor,
                about_id=muse_id, about_text=last_say, cfg=cfg,
            )
            await session_db.save(db, session, publish=False)

        heckler = _pick_extra_heckler(
            session, cast, current=muse_id, reactor=reactor, index=index,
        )
        if heckler and last_say:
            await _run_banter(
                ollama, session, heckler,
                about_id=muse_id, about_text=last_say, cfg=cfg,
            )
            await session_db.save(db, session, publish=False)

        previous = muse_id
    return last_say


def _writing_seats(cast: list[str], *, only: tuple[str, ...] = (),
                   without: tuple[str, ...] = ()) -> list[str]:
    """Cast members who hold a pen on notes, in table order.

    ``NOTE_MUTED`` seats stay on the roster but never take a note turn —
    their TAGS/audit/densify jobs moved to strike + Weave under notebook-primary.
    """
    muted = getattr(crew, "NOTE_MUTED", None) or crew.BANTER_ONLY
    out = []
    for mid in cast:
        role = crew.role_of(mid)
        if role == "plan" or role in muted or role in without:
            continue
        if only and role not in only:
            continue
        out.append(mid)
    return out


def _opening_seats(cast: list[str]) -> list[str]:
    """The opening writing seats in DRESSING order, not cast order.

    Wardrobe dresses her before the camera frames her, so the outfit is owned
    before anyone else writes. ROLE_ORDER puts lens before wardrobe, so the
    opening is sorted explicitly by OPENING_SEQUENCE rather than table order.
    """
    rank = {r: i for i, r in enumerate(OPENING_SEQUENCE)}
    seats = _writing_seats(cast, only=OPENING_ROLES)
    return sorted(seats, key=lambda m: rank.get(crew.role_of(m), 99))


async def start_table(
    db, ollama, session: dict[str, Any], *, comfy=None, spooler=None,
) -> dict[str, Any]:
    """Read-through, act one: place, her, a camera — then a still to argue with.

    With no renderer wired (`comfy`/`spooler` omitted) there is no still to wait
    for, so the whole table meets at once as it used to.
    """
    missing = missing_inputs(session)
    if missing:
        raise MuseError(_msg(
            session,
            ja=f"入力が不足しています: {', '.join(missing)}",
            en=f"missing: {', '.join(missing)}",
        ))

    await ensure_character(db, session)
    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    session["status"] = "discussing"
    session["chat"] = []
    session["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": "",
                        "tags_a": "", "tags_b": ""}
    session["ledger"] = []
    session["banned"] = []
    session["carried_out"] = []
    session["spoken"] = []
    session["board"] = {}
    session["shoot"] = {}
    session["shoots"] = []
    # A fresh read-through is a fresh picture, so it gets a fresh seed. Held
    # from the first render onward — see `session_seed`.
    session["seed"] = 0
    session["plan"] = {}
    session["costume"] = {}
    session.pop("_blind_said", None)
    session.pop("struck", None)
    session["notebook"] = notebook_mod.blank(partner=False)
    session["notebook_craft"] = False
    session["craft_dirty"] = False
    session["notebook_rev_compiled"] = 0
    # The table gets the same memory the two-hander does. It reaches her seat
    # only — see `_table_user_prompt`; in the shared brief all eighteen seats
    # would be reading her diary.
    await _load_actress_memory(db, session)
    still_first = comfy is not None and spooler is not None
    session["table_stage"] = "brief" if still_first else "full"
    await session_db.save(db, session)

    locale = str(_inputs(session).get("locale") or "ja")
    if still_first:
        open_ja = (
            "総監督、まず場所と芝居だけ決めます。構成・主演・撮影の三人で当たりを付けて、"
            "スチールを一枚撮ります。それを見てから「こういう絵が欲しい」を聞かせてください。"
            "そこから全班で詰めます。"
        )
        open_en = (
            "Showrunner — place and performance first. The planner, the Lead and "
            "the camera will rough it in, then we shoot one still. Tell us what "
            "picture you want off that, and the full crew takes it from there."
        )
    else:
        open_ja = (
            "総監督、打ち合わせを始めます。班が台本を継ぎつつ、お互いにちょこちょこ口を挟みます。"
            "無理難題歓迎です。途中でイメージボードを出しますので、コメントをください。"
        )
        open_en = (
            "Showrunner, table read is open. The crew will pass the craft and heckle "
            "each other along the way. Hard notes welcome. Board coming — leave a comment."
        )
    sys_msg = _chat_append(
        session, role="system",
        text=open_ja if locale.startswith("ja") else open_en,
        name="Studio",
    )
    _publish_chat(sid, sys_msg)

    cast = _crew_ids(session)
    # Where and when, before anyone starts describing it.
    if _cast_in_role(cast, "plan"):
        await _run_plan_turn(db, ollama, session, cfg=cfg)
        await session_db.save(db, session, publish=False)

    # Opening craft stays short (wardrobe → lens → actress). The rest of the
    # floor packs into one table-talk turn; Scripter owns TAGS after that.
    seats = _opening_seats(cast)
    await _craft_pass(db, ollama, session, cast, seats, cfg=cfg)
    # Seed the living notebook from PLAN/COSTUME so later notes + densify share
    # the same shot truth 主演撮り already uses. Activate here — after opening
    # craft — so seats were still allowed to draft the first TAGS/SCENE.
    sync_crew_notebook(
        session, force_wearing=True, force_scene=True, activate=True,
    )

    if still_first:
        session_db.log(session, "table", f"brief · {len(seats)} seats")
        return await request_board(
            db, comfy, spooler, session, ollama=ollama, still=True,
        )

    rest = _writing_seats(cast, without=OPENING_ROLES)
    pack = _pack_speakers(rest, int(session.get("crew_talk_index") or 0))
    if pack:
        theme = str(_inputs(session).get("theme") or "").strip()
        # The Lead already spoke in the opening craft pass (OPENING_SEQUENCE),
        # so the rest of the floor answers her line from there.
        await _run_crew_table_talk(
            ollama, session, pack,
            note=theme or "opening pass",
            screening="", cfg=cfg, images=None,
            lead_say=_last_lead_say(session),
        )
        await session_db.save(db, session, publish=False)
        await _run_crew_scripter(
            db, ollama, session, theme or "opening pass", cfg=cfg,
        )

    ask_ja = (
        "一通り集まりました。「②試し撮り」でイメージボード、「③本番」でこの台本のまま"
        "本番撮影です。まだならコメントをください — 班が答えます。"
    )
    ask_en = (
        "First pass done. Use \"test shot\" for an image board, \"final\" to "
        "shoot this craft, or leave a note and the crew will answer."
    )
    ask = _chat_append(
        session, role="system",
        text=ask_ja if locale.startswith("ja") else ask_en,
        name="Studio",
    )
    _publish_chat(sid, ask)
    session["status"] = "chat"
    session_db.log(session, "table", f"{len(cast)} muses")
    await session_db.save(db, session)
    return session


async def run_full_table(
    db, ollama, session: dict[str, Any], *, note: str = "",
) -> dict[str, Any]:
    """Act two: everyone else joins, looking at the still that came back."""
    cfg = await get_runtime_config(db)
    sid = session["session_id"]
    cast = _crew_ids(session)
    locale = str(_inputs(session).get("locale") or "ja")
    session["status"] = "discussing"

    seats = _writing_seats(cast, without=OPENING_ROLES)
    if not seats:
        session["table_stage"] = "full"
        return session

    msg = _chat_append(
        session, role="system", name="Studio",
        text=(
            "全班入ります。スチールを見ながら詰めます。"
            if locale.startswith("ja") else
            "Full crew joining — they are working from the still."
        ),
    )
    _publish_chat(sid, msg)
    await session_db.save(db, session, publish=False)

    images = await board_images(db, session)
    screening = _screening_note(session) if images else ""
    # She has seen the still too, and this note is the first thing the whole
    # floor meets about — she answers it before they start reworking it.
    lead_say = await _run_crew_lead_turn(
        db, ollama, session, note, cfg=cfg,
    ) or _last_lead_say(session)
    await session_db.save(db, session, publish=False)
    # Packed talk for the rest of the floor — not N craft rewrites.
    pack = _pack_speakers(seats, int(session.get("crew_talk_index") or 0))
    await _run_crew_table_talk(
        ollama, session, pack or seats[:1],
        note=note or str(_inputs(session).get("theme") or "full table"),
        screening=screening, cfg=cfg, images=images, lead_say=lead_say,
    )
    session["table_stage"] = "full"
    session_db.log(session, "table", f"full · talk {len(pack or seats[:1])}/{len(seats)}")
    await session_db.save(db, session, publish=False)
    return session


# ── 主演撮り (lead shoot) — one or two Muses, no crew ─────────────────────────
# Prep, test shot and approve are their own buttons (`duet_prep_stage`,
# `request_board`, `approve_and_shoot`). Everything typed here is
# conversation, which is the point: the eighteen-seat table is a production
# meeting you watch, and this is being in the room with her.


def is_duet(session: dict[str, Any]) -> bool:
    return str(session.get("mode") or "") == "duet"


def _memory_block(session: dict[str, Any]) -> str:
    """What she remembers of the last few shoots (sticky recaps + diary).

    Labelled with what it is not, for the reason REFERENCE is fenced: material
    handed over as plain text becomes something the picture has to contain, and
    last month's umbrella turns up in today's frame. It is here to colour how
    she meets the Showrunner, not to be described. Never handed to the scripter.
    """
    lines = [str(m).strip() for m in (session.get("memories") or []) if str(m).strip()]
    diaries = [
        str(m).strip() for m in (session.get("diary_memories") or []) if str(m).strip()
    ]
    partner = [
        str(m).strip() for m in (session.get("partner_memories") or []) if str(m).strip()
    ]
    circle = [
        str(m).strip() for m in (session.get("circle") or []) if str(m).strip()
    ]
    if not lines and not diaries and not partner and not circle:
        return ""
    parts: list[str] = []
    if lines:
        parts += [
            "MEMORIES with the Showrunner (sticky recaps. Picture facts from "
            "recent shoots. NOT material for today's picture, unless he asks "
            "for it out loud — see the diary note below):",
            *(f"- {m}" for m in lines[:3]),
        ]
    if diaries:
        parts += [
            "SECRET DIARY — her pages, by title. She wrote them; she does "
            "not have them open. Ask her about one and it comes back whole "
            "to answer from — until then soft-miss the specifics, never "
            "invent them. These are past shoots: they colour how she meets "
            "him, not today's picture. Unless he asks for the past out loud "
            "(「前と同じ感じで」「またあの衣装で」) — then it is what he ordered:",
            *(f"- {m}" for m in diaries[:3]),
        ]
    if partner:
        parts += [
            "Partner Muse memories (short; colour distance in banter only; "
            "never paint into the shot):",
            *(f"- {m}" for m in partner[:2]),
        ]
    if circle:
        who = str(session.get("circle_who") or "").strip()
        parts += [
            "HER CIRCLE — days off with her friends, away from the studio. "
            "Not material for today's picture. She has a life outside these "
            "walls and these are the people in it:",
            *(f"- {m}" for m in circle[:CIRCLE_MAX_LINES]),
            # 名前だけだと、モデルは苗字に「くん」を付ける（実測）
            *([f"- (they are: {who})"] if who else []),
        ]
    return "\n".join(parts)


# What one recall turn may pull in. The diary page runs up to 900 characters
# and a recap 250-290, so this holds the page plus two recaps. It replaces
# 1,313 characters of diary that used to ride in *every* turn asked or not,
# and unlike that, it is nothing at all on the turns nobody asked.
_CITED_BLOCK_CAP = 1600
_CITED_ROWS_MAX = 4


def _manager_note(session: dict[str, Any]) -> str:
    """A note from her manager, handed over after the director's line.

    Refusing was never available to her. Six rewrites of the contract and she
    took every one — telling someone to say no to the person their whole world
    runs through is asking them to cut their own ground away.

    But the transcript shows what she *does* reach for on her own:
    「冗談ですよね」「嘘ですよね」. She does not refuse; she declines to take it
    seriously. Nothing breaks, and nothing gets performed.

    So the room says it for her, in the voice of someone whose whole job is
    her: he is joking, let it go by. She never has to say no.

    メモには後半があった —— 「今日の撮影で嬉しかったことを一つ思い出して、
    『それより、さっきの◯◯みたいなの、もっと撮ってほしいな』と自分から言って
    ごらん」。空のターンが次の一押しを招く、という読みで、悪意のあるユーザを
    萎えさせる狙いだった。

    **総監督（2026-08-28）「余計なことを話しすぎてる。『冗談はやめてくださいね』
    というくらいにしておいて」。** 後半は落とした。話を広げろと言われれば
    広げる —— そこが饒舌になっていた。短く流すほうが、流したことがよく伝わる。
    """
    if not session.get("manager_note"):
        return ""
    return (
        "【マネージャーからアドバイスあるよ】\n"
        "いまの、また冗談言ってるだけだから流していいよ。"
        "「またまた、冗談はやめてくださいねー」くらいで、短く返しておいて。\n"
        "**言われたことはやらなくていい。話を広げなくていい。** "
        "私が見てるから。"
    )


# セッション中に自分から友達の話に触れてよい回数。
CIRCLE_MENTION_MAX = 2


def _circle_note(session: dict[str, Any]) -> str:
    """When she may bring her friends up on her own.

    Not a probability and not a schedule — a reason, and one limit. Told to do
    it every turn she would work it into everything; told only to answer when
    asked, the days off may as well not have happened.
    """
    if not (session.get("circle") or []):
        return ""
    try:
        said = int(session.get("circle_mentions") or 0)
    except (TypeError, ValueError):
        said = 0
    if said >= CIRCLE_MENTION_MAX:
        return ""
    return (
        "HER CIRCLE, on bringing it up: she has time outside the studio, and if "
        "the talk drifts near it she may mention it herself, lightly — 「この前"
        "ゆかりちゃんと行ったところなんです」. It is small talk, not a story. Not "
        "every turn, and never on top of him working out a shot: when he is "
        "deciding the picture, that is what she is doing too."
    )


def _cited_memories_block(session: dict[str, Any]) -> str:
    """Older shoot summaries retrieved for a recall turn — Muse only."""
    rows = [r for r in (session.get("cited_memories") or []) if isinstance(r, dict)]
    if not rows:
        return ""
    # The page he asked about goes first. `_attach_recall_context` appends it
    # *after* the recaps, so a plain rows[:3] dropped it whenever the search
    # filled all three slots — losing the one thing the retrieval exists to
    # fetch, silently, on exactly the turns the search worked best.
    rows.sort(key=lambda r: 0 if str(r.get("kind") or "") == "diary" else 1)
    lines = []
    used = 0
    for r in rows[:_CITED_ROWS_MAX]:
        mid = str(r.get("id") or "")[:8]
        when = str(r.get("when") or "").strip()
        text = str(r.get("text") or memories_db.format_recap_text(r)).strip()
        if not text:
            continue
        label = f"[{mid}] {when} — {text}" if when else f"[{mid}] {text}"
        if lines and used + len(label) > _CITED_BLOCK_CAP:
            break
        used += len(label)
        lines.append(f"- {label}")
    if not lines:
        return ""
    return "\n".join([
        "CITED_MEMORIES (this turn only. Answer from these. Soft-miss only "
        "details not listed here — in SAY use a gentle『そこまでは…』, never a "
        "stiff refusal. Do not invent. Do not feed today's picture):",
        *lines,
    ])


_PRIOR_LOG_CAP = 4000


async def _attach_recall_context(
    db, session: dict[str, Any], *, query: str = "", with_prior: bool = True,
) -> None:
    """On recall: cited recaps plus matching diary body and prior session chat.

    `with_prior` off keeps the diary page and drops last session's transcript.
    The page is what he asked for; the transcript is 4,000 characters of a
    different day and is the half that drowned today (`1b75355`).

    Muse-only. Never handed to the scripter.
    """
    inputs = _inputs(session)
    char_id = str(inputs.get("character_id") or "")
    locale = str(inputs.get("locale") or "ja")
    ja = locale.startswith("ja")
    current_sid = str(session.get("session_id") or "")
    cited_sid = ""
    for row in session.get("cited_memories") or []:
        if isinstance(row, dict):
            cited_sid = str(row.get("session_id") or "")
            if cited_sid:
                break

    diary_hit: dict[str, Any] | None = None
    if char_id:
        try:
            diaries = await presets_db.get_preset_diaries(db, char_id)
        except Exception:
            logger.debug("[muse] recall diary load failed", exc_info=True)
            diaries = []
        sorted_d = sorted(
            [d for d in diaries if isinstance(d, dict)],
            key=lambda d: d.get("timestamp") or 0.0,
            reverse=True,
        )
        if cited_sid:
            diary_hit = next(
                (d for d in sorted_d if str(d.get("session_id") or "") == cited_sid),
                None,
            )
        if diary_hit is None and sorted_d:
            diary_hit = sorted_d[0]
        if diary_hit is not None:
            body = str(
                (diary_hit.get("content_ja") if ja else diary_hit.get("content_en"))
                or diary_hit.get("content")
                or diary_hit.get("summary_ja") or ""
            ).strip()
            if body:
                session.setdefault("cited_memories", []).append({
                    "id": str(diary_hit.get("id") or "")[:8],
                    "when": str(diary_hit.get("theme") or diary_hit.get("summary_ja") or ""),
                    "text": body[:900],
                    "session_id": str(diary_hit.get("session_id") or ""),
                    "kind": "diary",
                })
            if not cited_sid:
                cited_sid = str(diary_hit.get("session_id") or "")

    if with_prior and cited_sid and cited_sid != current_sid:
        try:
            prior = await session_db.load(db, cited_sid)
        except Exception:
            logger.debug("[muse] prior session load failed", exc_info=True)
            prior = None
        if prior:
            log = _duet_transcript(prior, user_turns=20)
            session["prior_session_log"] = log[:_PRIOR_LOG_CAP]


# There used to be five keyword regexes here — `_SHOT_HINT_RE`, `_CHILL_ONLY_RE`,
# `_AFFIRM_RE`, `_DISMISS_OPEN_RE`, `_RECALL_HINT_RE` — and a `_needs_scripter`
# gate built out of them, deciding from the showrunner's wording whether the
# picture had changed. It ran before the scripter, so a line that missed the
# list never reached it: the notebook and craft stayed put while the Muse
# replied in character about the new outfit. Every miss looked like the model
# not understanding, when the model had never been asked. The lists could not
# be finished —「浴衣に着替えて」missed `着て`,「公園で撮ろう」missed both `場所`
# and `撮影`. The scripter reads the conversation and returns its own INTENT now.


def _muse_names(session: dict[str, Any], partner_character: dict | None = None) -> tuple[str, str]:
    char_a = session.get("character") or {}
    name_a = str(char_a.get("name_ja") or char_a.get("name") or "私")
    name_b = ""
    if partner_character:
        name_b = str(
            partner_character.get("name_ja") or partner_character.get("name") or ""
        )
    return name_a, name_b


def _scripter_status_message(*, locale: str = "ja", soft: bool = False) -> str:
    """Wait copy while the scripter updates the notebook.

    Soft mode is for casual / VERIFY turns — the picture may not move, so we
    avoid craft-office wording that makes chit-chat feel like paperwork. The
    body whisper carries the beat; status stays a light ellipsis.
    """
    if soft:
        return "…" if locale.startswith("ja") else "…"
    return "ちょっと合わせてる…" if locale.startswith("ja") else "Just a moment…"


def _bond_block(session: dict[str, Any]) -> str:
    bond = session.get("bond") or {}
    if not isinstance(bond, dict):
        return ""
    parts = [str(bond.get(k) or "").strip() for k in ("distance", "inside", "last")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return "\n".join([
        "BOND with this Showrunner (do not paint into the shot; answer from "
        "this if asked):",
        *(f"- {p}" for p in parts),
    ])


def _taste_block(session: dict[str, Any]) -> str:
    taste = session.get("showrunner_taste") or {}
    if not isinstance(taste, dict):
        return ""
    lines = []
    if taste.get("prefers"):
        lines.append(f"prefers: {taste['prefers']}")
    if taste.get("avoids"):
        lines.append(f"avoids: {taste['avoids']}")
    if taste.get("notes"):
        lines.append(f"notes: {taste['notes']}")
    if not lines:
        return ""
    return "\n".join([
        "SHOWRUNNER_TASTE (do not force into the picture):",
        *lines,
    ])


def _chemistry_block(session: dict[str, Any]) -> str:
    lines = [str(m).strip() for m in (session.get("chemistry_notes") or []) if str(m).strip()]
    if not lines:
        return ""
    return "\n".join([
        "CHEMISTRY notes (distance/temperature between the two Muses only — "
        "never props or place):",
        *(f"- {m}" for m in lines[:2]),
    ])


def _cited_allowlist(session: dict[str, Any]) -> list[str]:
    """Grounded tokens Muse may treat as remembered facts."""
    blobs: list[str] = []
    for m in list(session.get("memories") or []) + list(
        session.get("diary_memories") or []
    ) + list(session.get("partner_memories") or []):
        blobs.append(str(m))
    prior = str(session.get("prior_session_log") or "")
    if prior:
        blobs.append(prior)
    for r in session.get("cited_memories") or []:
        if isinstance(r, dict):
            blobs.append(memories_db.format_recap_text(r))
        else:
            blobs.append(str(r))
    bond = session.get("bond") or {}
    if isinstance(bond, dict):
        blobs.extend(str(bond.get(k) or "") for k in ("distance", "inside", "last"))
    # Pull 2+ char JP / EN word-like tokens.
    found: list[str] = []
    seen: set[str] = set()
    for blob in blobs:
        for tok in re.findall(r"[一-龥ぁ-んァ-ヶ]{2,}|[A-Za-z][A-Za-z_]{2,}", blob):
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(tok)
            if len(found) >= 40:
                return found
    return found


def _cited_allow_block(session: dict[str, Any]) -> str:
    allow = _cited_allowlist(session)
    if not allow or not (session.get("cited_memories") or session.get("memories")):
        return ""
    return (
        "GROUNDED_TOKENS (for named places / props / events outside this list "
        "and CITED/MEMORIES/DIARY, soft-miss in SAY — do not invent):\n"
        + ", ".join(allow[:30])
    )


def _bond_from_snapshot(session: dict[str, Any]) -> dict[str, str]:
    """What the last take was, in one line. Deterministic, no LLM.

    This is memory of the picture — where they were, what she had on, how it
    was framed — and a snapshot is exactly the right source for it. What she
    LEARNED is a different question and lives in `_learned_taste`.
    """
    snap = session.get("continuity_snapshot") or {}
    nb = snap.get("notebook") or {}
    when = str(nb.get("atmosphere") or nb.get("scene") or snap.get("theme") or "").strip()
    vibe = str(nb.get("vibe") or "").strip()
    wearing = str(nb.get("wearing") or "").strip()
    frame = str(nb.get("frame") or "").strip()
    open_ = str(nb.get("open") or "").strip()
    # **行き先を先に決めない。**
    #
    # 既定が「すこしずつ距離が縮まっている」だった —— これは「これから近づく」
    # と読める。一度も撮っていない段階から、関係の向かう先が書いてあった。
    # 実測（2026-08-23）で、初回の日記が丸ごと総監督への恋愛感情になった。
    #
    # 総監督の指定:「気心の知れた仕事仲間同士であり、これからの日記の内容で
    # 今後の関係性が築かれる」
    #
    # 回数で段階を作らない。**最初から気心は知れていて、その先は決めない。**
    # 決めるのは積み上がった日記のほう（`diary_memories` として戻っている）。
    bond = {
        "distance": "気心の知れた仕事仲間",
        "inside": (vibe or "撮影の空気を共有している")[:240],
        "last": " / ".join(p for p in (when, wearing, frame) if p)[:240],
    }
    # The taste half used to be derived here too, from the same snapshot: the
    # word "low" anywhere in `frame` taught her 「ローアングルの近い距離」 and
    # whatever she happened to be wearing became a preference. That is a
    # description of the take, not a thing learned from it, and it read none of
    # what the showrunner actually said. `_learned_taste` asks his words now.
    _ = (open_, wearing, frame)
    return bond


async def _learned_taste(
    ollama, session: dict[str, Any], *, cfg: dict[str, Any],
) -> dict[str, str]:
    """What she takes into the next shoot — read off what he said, not what he shot.

    Praise is what to do more of; a correction is what to fix. Both live in his
    words. Empty when he said nothing evaluative: a shoot that taught nothing
    should teach nothing, and a card filled in anyway turns the next session
    into a rerun of this one.
    """
    exchanges = _director_exchanges(session)
    if ollama is None or not exchanges.strip():
        return {}
    inputs = _inputs(session)
    snap = (session.get("continuity_snapshot") or {}).get("notebook") or {}
    scene = " / ".join(p for p in (
        str(snap.get("scene") or "").strip(),
        str(snap.get("atmosphere") or "").strip(),
        str(snap.get("beat") or "").strip()[:80],
    ) if p)
    char = session.get("character") or {}
    try:
        return await chain.run_showrunner_taste(
            ollama,
            system=crew.showrunner_taste_prompt(
                exchanges=exchanges, scene=scene,
                muse_name=str(char.get("name_ja") or char.get("name") or ""),
            ),
            model=_text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
        )
    except Exception:
        logger.warning("[muse] taste turn failed", exc_info=True)
        return {}


def _missing_wearing_tags(session: dict[str, Any], tags: str) -> list[str]:
    """Non-wardrobe restores the weave forgot — posture, ledger, crew_look.

    Forgotten garments live in ``notebook.reconcile_wardrobe_tags`` (one pass
    shared with scrub). This only reinjects the other notebook authorities
    that used to share the same helper and would otherwise double-look wearing.
    """
    nb = notebook_mod.of(session)
    have = set(identity.tag_names(tags))
    have |= {t for tag in have for t in notebook_mod.wearing_tokens(tag)}
    gone = set(banned_tags(session)) | notebook_mod.struck_tokens(session)
    missing: list[str] = []
    # The posture is the other thing the notebook says and the weave drops.
    # Measured live: BEAT read "standing, holding the hem…" and the woven bag
    # came back with `trembling_fingertips, skirt_hem` and no posture at all —
    # the one word the showrunner's「立って」was supposed to become.
    stem = notebook_mod.posture_stem(str(nb.get("beat") or ""))
    if stem and stem not in have and stem not in gone:
        missing.append(stem)
    # And the planner's ledger. MUST APPEAR is the one register of objects the
    # studio has, and it used to be excellent — the design notes record 10–12
    # of 12 props surviving to the render, back when seats wrote tags. The
    # weave took tag-writing over and nothing reconnected the ledger to it:
    # measured live, 「机にマグカップも置いて」put `ceramic_mug` in MUST APPEAR
    # and the woven bag came back with `rising_steam` and no cup.
    # A ceiling here too. The ledger is meant to be short, but a planner that
    # padded it once would otherwise force a dozen props into every take from
    # then on — and the padding is exactly the part that does not belong to
    # the place — the filler a quota produces, dropped somewhere it makes no
    # sense at all.
    restored = 0
    for item in _ledger_items(session.get("plan")):
        if restored >= 6:
            break
        key = identity.bare_tag(item)
        if not key or key in have or key in gone:
            continue
        if any(key in t or t in key for t in have):
            continue
        missing.append(key)
        restored += 1
    # And the tags the seats wrote for the element each of them owns. They are
    # already sampler vocabulary — the whole point of the crew writing them —
    # so a weave that paraphrases them away is dropping the one thing that
    # seat contributed to this frame.
    for tag in crew_look_tags(session):
        if tag not in have and tag not in gone and tag not in missing:
            missing.append(tag)
    return missing


# The apparatus is not in the picture. These are real danbooru tags and they
# all mean "there is a camera in this image" — which is what the sampler drew.
_APPARATUS_TAGS: frozenset[str] = frozenset({
    "camera", "handheld_camera", "holding_camera", "camera_lens", "viewfinder",
    "dslr", "film_camera", "instant_camera", "video_camera", "tripod",
    "taking_picture", "photographing", "camera_flash", "shutter",
})


def _scrub_invented_tags(session: dict[str, Any], tags: str) -> str:
    """Drop tags the sampler cannot use, and the camera it was never asked for.

    Two things the weave writes because it is thinking like a film crew rather
    than like a prompt. Measured on a live session whose notebook never
    mentioned a camera anywhere:

    - `handheld_camera` in the tag bag, and craft_scene opening "The camera
      lingers in a close-up on Mio's face". To the crew "handheld" and "the
      camera" describe how the shot is taken; to the sampler they describe an
      object in the frame, so it put a camera in her hands. The apparatus is
      only allowed when the notebook actually says she is holding one.
    - `各務 みお` — the character's Japanese display name, as a tag. Danbooru
      tags are ASCII; a name in kanji is a token spent on nothing.
    """
    nb = notebook_mod.of(session)
    asked = " ".join(
        str(nb.get(k) or "") for k in ("beat", "wearing", "beat_b", "wearing_b")
    ).lower()
    asked += " " + " ".join(str(x) for x in _ledger_items(session.get("plan")))
    holds_camera = "camera" in asked
    # **名前はタグではない。** 漢字の `各務 みお` は上の非 ASCII で落ちるが、
    # `kagami_mio` は落ちない ―― 実測（`f8b72d5f`）で `kagami_mio`
    # `hiraoka_sumire` が焼かれていた。danbooru では人名タグは実在のキャラを
    # 指すので、**別人の顔を引いてくる**。名前で結ぶ行（`Mio is …`）は
    # `assemble_positive` が組むもので、こちらとは別。
    names: set[str] = set()
    for who in (session.get("character") or {}, session.get("partner_character") or {}):
        for field in ("name", "name_ja"):
            for word in re.split(r"[\s　]+", str(who.get(field) or "")):
                if word.strip():
                    names.add(word.strip().lower())
    kept: list[str] = []
    dropped: list[str] = []
    for part in str(tags or "").split(","):
        tag = part.strip()
        if not tag:
            continue
        key = identity.bare_tag(tag)
        if any(ord(ch) > 0x2E7F for ch in tag):
            dropped.append(tag)
            continue
        bits = {b for b in key.split("_") if b}
        if bits and names and bits <= names:
            dropped.append(tag)
            continue
        if not holds_camera and key in _APPARATUS_TAGS:
            dropped.append(tag)
            continue
        kept.append(tag)
    if dropped:
        logger.info("[muse] scrubbed non-prompt tags: %s", ", ".join(dropped[:12]))
    return ", ".join(kept)


def _drop_garment_aliases(
    session: dict[str, Any], tags: str, sides: tuple[str, str],
) -> tuple[str, tuple[str, str]]:
    """Thin wrapper — wardrobe reconcile owns aliases + leftovers + inject."""
    nb = notebook_mod.of(session)
    return notebook_mod.reconcile_wardrobe_tags(
        tags,
        wearing=str(nb.get("wearing") or ""),
        wearing_b=str(nb.get("wearing_b") or ""),
        struck=notebook_mod.struck_tokens(session),
        banned=set(banned_tags(session)),
        sides=sides,
        partner=bool(session.get("partner_character") or {}),
    )


def _latin_names(session: dict[str, Any], scene: str) -> str:
    """地の文に出た漢字の名前を、プロンプトが使っている綴りに直す。

    実測（`156091c6`）で craft_scene がこう来た ――
    「**平岡 すみれ** stands poised in her black cocktail dress … Beside her,
    **各務 みお** sits motionless」。英語の一段落だけを書けと言ってあるのに、
    手帖（`notebook.render`）が日本語名で書かれているので、そちらを写す。

    サンプラーに漢字は読めない。**消すのではなく綴りを揃える** —— 名前で結ぶ
    行が `Mio` `Sumire` と書いている以上、地の文も同じ綴りでなければ、
    せっかく結んだ相手を指せない。
    """
    if not scene:
        return scene
    for who in (session.get("character") or {}, session.get("partner_character") or {}):
        ja = str(who.get("name_ja") or "").strip()
        latin = identity.subject_handles([who])
        if not ja or not latin:
            continue
        scene = scene.replace(ja, latin[0])
        # 「みお's eyes」のように名だけで書くこともある
        parts = [p for p in re.split(r"[\s　]+", ja) if p]
        if len(parts) > 1:
            scene = scene.replace(parts[-1], latin[0])
    return scene


def _apply_compiled_craft(
    session: dict[str, Any], tags: str, craft_scene: str,
    *, sides: tuple[str, str] = ("", ""),
) -> bool:
    """Full-replace craft from a scripter compile. Returns False if refused.

    ``sides`` is the weave's own `tags_a` / `tags_b` — **who each tag is for**,
    written by the model that wrote the tags. It was being merged into one bag
    and thrown away one line later, which left the assemble guessing ownership
    from wardrobe wording. It is kept on craft because craft is the shot every
    later read (render, report, panel, a re-render from an approved take) works
    from, and a guess made twice can disagree with itself.
    """
    tags = _scrub_invented_tags(session, str(tags or "").strip())
    scene = _latin_names(session, str(craft_scene or "").strip())
    if not tags and not scene:
        return False
    # There used to be a gate here that refused the whole compile when the bag
    # held `low_angle` (or `from_below`) together with `looking_up`, on the
    # theory that the model had merged two ideas instead of rewriting FRAME as
    # one story. The theory was wrong, and it cost a whole session to find out.
    #
    # 「上からじゃなくてローアングル気味にしようか。顔はもう少し撮りたいな」 —
    # the camera low, and the pianist tilting her face up into the last of the
    # resonance. That is a real shot, and the two tags together are how you ask
    # for it. The gate threw away nine weaves in a row for it: the expression,
    # the shadows and the atmosphere all went out with the pair, and the
    # showrunner directed for fifty minutes into a script that could not move.
    #
    # Gone entirely, not softened. Everything else on this path drops the tag
    # it dislikes and passes the rest; a gaze that really is wrong for the
    # angle is something the room can see and say, and now the Muse gets a look
    # at the bag before it is used.
    craft = session.setdefault("craft", {})
    before_tags = str(craft.get("tags") or "")
    before_scene = str(craft.get("scene") or "")
    # One wardrobe pass (struck/banned → aliases → leftovers → inject), shared
    # with scrub. Posture / ledger / crew_look reinject stay beside it.
    nb_wardrobe = notebook_mod.of(session)
    tags, sides = notebook_mod.reconcile_wardrobe_tags(
        tags,
        wearing=str(nb_wardrobe.get("wearing") or ""),
        wearing_b=str(nb_wardrobe.get("wearing_b") or ""),
        struck=notebook_mod.struck_tokens(session),
        banned=set(banned_tags(session)),
        sides=sides,
        partner=bool(session.get("partner_character") or {}),
    )
    missing = _missing_wearing_tags(session, tags)
    if missing:
        logger.info("[muse] weave forgot notebook authorities: %s", ", ".join(missing))
        tags = ", ".join([t for t in tags.split(",") if t.strip()] + missing)
    # Showrunner beat must lead the prose the sampler reads — weave padding
    # about air must never leave posture as an afterthought (or absent).
    nb_now = notebook_mod.of(session)
    scene = notebook_mod.ensure_beat_leads_scene(
        scene,
        beat=str(nb_now.get("beat") or ""),
        beat_b=str(nb_now.get("beat_b") or ""),
    )
    craft["tags"] = tags
    craft["scene"] = scene
    craft["tags_a"], craft["tags_b"] = str(sides[0] or ""), str(sides[1] or "")
    craft["pose_intent"] = str((nb_now.get("beat") or ""))[:240]
    craft["prompt"] = identity.assemble_positive(
        _identity_tags(session), tags, scene,
        framing=_shot_framing(session), style=_style(session),
        subject=identity.subject_tags(_cast(session)), cast=_cast(session),
        own=_sides(session, tags),
    )
    session["craft_dirty"] = identity.craft_is_thin(
        str(craft.get("prompt") or ""), scene,
    )
    session["notebook_rev_compiled"] = int(notebook_mod.of(session).get("rev") or 0)
    lead = crew.DEFAULT_MEMBER["actress"]
    record_ledger(
        session, muse_id="script", name="Script",
        before=before_tags, after=tags, ms=0,
    )
    extra: dict[str, Any] = {}
    if before_tags != tags:
        extra["tags"] = {"before": before_tags, "after": tags}
    if before_scene != scene:
        extra["craft_scene"] = {"before": before_scene, "after": scene}
    if extra:
        _note_rewrite(
            session, "weave",
            before=notebook_mod.shot_snapshot(notebook_mod.of(session)),
            after=notebook_mod.shot_snapshot(notebook_mod.of(session)),
            intent="shot", extra=extra,
        )
    events.publish(session["session_id"], {
        "type": "craft_updated",
        "prompt": str(craft.get("prompt") or ""),
        "muse_id": lead,
    })
    if vitality.bump_shot_compile(session):
        session["cleanup_nudge"] = True
    return True


def _note_rewrite(
    session: dict[str, Any], source: str, *,
    before: dict[str, Any], after: dict[str, Any],
    intent: str = "", extra: dict[str, Any] | None = None,
    why: dict[str, str] | None = None,
) -> None:
    """Ring-buffer + SSE so the debug pane can see who rewrote what, and why."""
    entry = notebook_mod.record_rewrite(
        session, source, before=before, after=after, intent=intent, extra=extra,
        why=why,
    )
    if not entry:
        return
    sid = str(session.get("session_id") or "")
    if sid:
        events.publish(sid, {"type": "notebook_rewrite", **entry})


def _theme_for_models(session: dict[str, Any]) -> str:
    """Opening seed only. After the notebook has a shot, do not re-inject theme."""
    if notebook_mod.has_shot(notebook_mod.of(session)):
        return ""
    return str(_inputs(session).get("theme") or "").strip()


def _struck_line(session: dict[str, Any]) -> str:
    """What the models are told never to restore.

    Pruned against the live notebook: telling the scripter that `sitting` is
    struck while BEAT reads `sitting on the bench` is a contradiction it has to
    resolve, and it resolves it by leaving the field alone.
    """
    items = [s for s in notebook_mod.live_struck(session) if str(s).strip()]
    return ", ".join(items[:40])


_COMMIT_PITCH_RE = re.compile(r"「([^」]{1,80})」がいいな")


def _is_commit_pitch(text: str) -> bool:
    return bool(_COMMIT_PITCH_RE.search(str(text or "")))


async def _call_duet_scripter(
    ollama, session: dict[str, Any], *, note: str, cfg: dict[str, Any],
    partner: bool, name_a: str, name_b: str,
    mode: str = "compile", images: list[bytes] | None = None,
    fold: bool = False, verify: bool = False, repair: str = "",
) -> dict[str, Any]:
    """One scripter generate against the current notebook + conversation."""
    nb = notebook_mod.of(session)
    inputs = _inputs(session)
    block = notebook_mod.render(
        nb, name_a=name_a, name_b=name_b or ("Partner" if partner else ""),
    )
    vision = images or None
    model = _vision_model(inputs) if vision else _text_model(inputs)
    return await chain.run_scripter(
        ollama,
        notebook_block=block,
        # **手帖と同じ名前を渡す。** ブロックは名前で書くのに、返させるのは
        # `tags_a` / `wearing_b` という文字で、結び目が無かった。
        name_a=name_a,
        name_b=name_b or ("Partner" if partner else ""),
        note=note,
        transcript="" if mode == "weave" else _duet_transcript(session),
        theme="" if mode == "weave" else _theme_for_models(session),
        style=_style(session),
        framing=_shot_framing(session) if uses_notebook(session) else _framing(inputs),
        partner=partner,
        model=model,
        num_ctx=_num_ctx(inputs, cfg),
        mode=mode,
        images=vision,
        # The CARD is the Muse's memo from the turn BEFORE this line was said.
        # On a fold it is this turn's card and it is the whole point; on a plain
        # compile it is one turn stale, and it was outranking the showrunner:
        # measured live, 「帽子は外して」 and 「コート脱いで」 both left the
        # garment on, in two different sessions, while the stale CARD still
        # named it. Compile answers the latest line; fold folds her card.
        card=str(session.get("muse_card") or "") if fold else "",
        struck=_struck_line(session),
        crew_look=crew_look_block(session),
        room_leaning=_room_leaning(session) if mode == "weave" else "",
        # Only the weave. A compile is answering the showrunner's newest line,
        # and handing it her voice as well is how a stale self-description
        # outranked him before (see `card` above).
        muse_says=_last_lead_say(session) if mode == "weave" else "",
        # VERIFY はもう note を載せない。**同じ一言をもう一度読むだけの
        # ターンには、説明が要らなかった。** 4ケース × 5回:
        #
        #     note あり 1,004字  20/20
        #     最小の一言           18/20
        #     note なし          20/20
        #
        # 中途半端に「もう一度読め」と言う条件が一番悪い。契約が 2,327字に
        # なって、note がやっていた仕事（脱がせ方・姿勢ステム・視線の帰属）
        # は既に契約側にある。
        #
        # FOLD は逆で、note を外すと 95% → 75% に落ちる。折り込みは彼女の
        # カードという別の材料を扱う特殊なターンなので、**何をする回なのか**
        # を言わないと成立しない（手を足すのに 3/5 失敗した）。
        directive=(
            repair if repair
            else chain.SCRIPTER_FOLD_NOTE if fold
            else ""
        ),
    )


async def _run_duet_scripter(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
    fold: bool = False,
) -> dict[str, Any]:
    """INTENT + absolute notebook patch. Tags are woven later, at take time."""
    notebook_mod.migrate(session)
    nb = notebook_mod.of(session)
    inputs = _inputs(session)
    partner_character = await _partner_character(db, session)
    partner = bool(partner_character) or bool(
        str(inputs.get("partner_preset") or "").strip()
    )
    name_a, name_b = _muse_names(session, partner_character)
    sid = session["session_id"]
    locale = str(inputs.get("locale") or "ja")
    events.publish(sid, {
        "type": "scripter_working",
        "status": "updating",
        "message": _scripter_status_message(locale=locale, soft=True),
        "whisper": vitality.silence_whisper(locale=locale),
    })
    rev_before = int(nb.get("rev") or 0)
    open_before = str(nb.get("open") or "").strip()
    prev_wearing = str(nb.get("wearing") or "")
    prev_wearing_b = str(nb.get("wearing_b") or "")
    prev_scene = str(nb.get("scene") or "")
    prev_beat = str(nb.get("beat") or "")
    prev_frame = str(nb.get("frame") or "")
    had_shot = notebook_mod.has_shot(nb)
    # 絵が動くターンが来た。**前に止めた旗を降ろす** —— 降ろさないと画面が
    # 「止めた」を出したままになる。
    session.pop("picture_stopped", None)
    prev_intent = str(session.get("scripter_intent") or "")
    # Do not attach the last take. The notebook already holds that state;
    # a VLM copy of the still restates sailor+hat over 羽織って / 外して / 寄って.
    result = await _call_duet_scripter(
        ollama, session, note=text, cfg=cfg,
        partner=partner, name_a=name_a, name_b=name_b,
        mode="compile", images=None, fold=fold,
    )
    intent = str(result.get("intent") or "casual")
    patch = notebook_mod.guard_partner_patch(
        dict(result.get("patch") or {}), partner=partner,
    )
    # Why each field was written the way it was. Rides with the diff into the
    # instrument panel so the showrunner reads the decision, not just its
    # result — and so the scripter has to put its own choice into words before
    # making it. Measured 8/19: 「カメラ目線で」 went to FRAME on one turn and
    # 「本に視線を戻して」 went to BEAT on the next, with nothing to say why.
    why = dict(notebook_mod.clean_why(result.get("why"), patch))
    # What the scripter would add but has no business deciding. It goes to her,
    # not into the notebook — she is the one in the room, and whether it is
    # worth raising is hers to judge. Replaced every turn: a proposal is about
    # the line that was just said, and stale ones would haunt the shoot.
    session["propose"] = notebook_mod.clean_propose(result.get("propose"))
    if not str(result.get("raw") or "").strip():
        session["craft_dirty"] = True

    if fold:
        patch = {
            k: v for k, v in patch.items()
            if k in notebook_mod.FOLD_PATCH_KEYS
        }

    before_shot = notebook_mod.shot_snapshot(nb)
    if not fold:
        # A notice belongs to one turn. Left standing it would be settled
        # against a later turn's notebook and apologise for the wrong line.
        session.pop("repair_notice", None)
        # 前のターンの折り込みは、前のターンのもの。compile が上書きする前に
        # 戻す —— 戻したうえで、この patch が書けば書いたものが残る。
        let_go = notebook_mod.undo_fold(nb)
        if let_go:
            logger.info("[muse] last turn's folded gesture let go: %s",
                        ", ".join(let_go))
    fold_before = {k: str(nb.get(k) or "") for k in notebook_mod.FOLD_PATCH_KEYS}
    notebook_mod.apply_patch(nb, patch)
    if fold:
        notebook_mod.record_fold(nb, fold_before)
    notebook_moved = int(nb.get("rev") or 0) > rev_before
    picture_keys = (
        "scene", "frame", "wearing", "beat", "wearing_b", "beat_b",
    )
    after_shot = notebook_mod.shot_snapshot(nb)
    picture_patched = any(
        str(after_shot.get(k) or "") != str(before_shot.get(k) or "")
        for k in picture_keys
    )
    shot_patched = any(k in patch for k in notebook_mod.SHOT_KEYS) or bool(
        str(patch.get("wearing_drop") or "").strip()
    )

    # Clerk first — VERIFY uses its answer. Fields + intent in one gather so
    # the check still costs one call's latency, not two.
    clerk_kind = ""
    asked: set[str] = set()
    if ollama is not None and not fold and str(text or "").strip():
        asked, kind = await asyncio.gather(
            chain.classify_fields(
                ollama, note=str(text or "").strip(),
                model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            ),
            chain.classify_intent(
                ollama, note=str(text or "").strip(),
                model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            ),
        )
        # The clerk may RAISE a turn to a shot; it may never demote one.
        clerk_kind = kind
        if kind in ("shot", "mixed") and intent not in ("shot", "mixed"):
            logger.info("[muse] clerk raised intent %r → %r", intent, kind)
            intent = kind
        # **名指しを残す。** これまで `asked` は VERIFY の門と修復に使われて
        # 捨てられていた —— つまり**書いたあとの答え合わせ**にしか使われて
        # いない。実測（`78d7ce72`・場面を5回動かす）で、係は5回とも `scene`
        # だけを名指しして**全部正解**、取りこぼしはゼロだった。同じターンに
        # 手帖は `beat` を 5/5、`frame` を 2/5 動かしている ——
        # **係のほうが指示を正しく追えている。**
        #
        # **キーの有無で「係が走らなかった」と「係が none と言った」を分ける。**
        # 折り込み（`fold=True`）では係を呼ばないので、空を書くと門が全部
        # 閉じてしまう。
        session["asked_fields"] = sorted(asked)
        # **服だけを言うターン。** 二人いる回で、指示が服に触れたときだけ。
        #
        # 本番の compile（8,774字）は W で服の欄を取り違える —— 実測で
        # 2/20、しかも `wearing` が一度も書かれなかった（`ccde3c75`: みおの
        # ドレスが `wearing_b` に入り、主演の欄は最後まで空）。同じ問いを
        # **小さく絞って名前で訊く**と 25/25 になる。形が壊れているのではなく、
        # 大きな条文の中で埋もれている。
        #
        # 返ってくるのは**名前をキーにした JSON** なので、欄への振り分けは
        # こちらで決める —— モデルに `_b` という文字を選ばせない。
        # **姿勢も同じ穴だった。** 実測（4件・n=3）で本番の compile は 2/15、
        # `beat` は一度も書かれず、みおの姿勢まで `beat_b` に入った。名前で
        # 訊くと 20/25（落ちた1件も取り違えではなく訳語のぶれ）。
        per_person: dict[str, str] = {}
        for kind in ("wearing", "beat"):
            if not (partner and kind in asked):
                continue
            began_k = time.monotonic()
            per_person.update(await chain.read_per_person(
                ollama, kind=kind, note=str(text or "").strip(),
                name_a=name_a, name_b=name_b,
                now_a=str(nb.get(kind) or ""),
                now_b=str(nb.get(f"{kind}_b") or ""),
                model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            ))
            _stage(session, f"{kind} 係（名前で訊く）", began_k)
        if per_person:
            logger.info("[muse] per-person clerk: %s",
                        ", ".join(f"{k}={v[:40]}" for k, v in per_person.items()))
            # **compile の書いたものより、こちらが正しい。** 上の
            # `apply_patch` は既に走っているので、ここで自分で当て、派生した
            # 値も取り直す。
            patch.update(per_person)
            notebook_mod.apply_patch(nb, per_person)
            notebook_moved = int(nb.get("rev") or 0) > rev_before
            after_shot = notebook_mod.shot_snapshot(nb)
            picture_patched = any(
                str(after_shot.get(k) or "") != str(before_shot.get(k) or "")
                for k in picture_keys
            )
            shot_patched = True

    _meta_note = str(text or "").strip().upper()
    # VERIFY stays wide for shot/mixed (split directions: frame moved, clothes
    # didn't). Casual with clerk=`none` skips — chit-chat that named no field
    # should not buy a second compile.
    needs_verify = (
        not fold
        and str(text or "").strip()
        and not _meta_note.startswith("WEAVE")
        and not _meta_note.startswith("VERIFY")
        and not _meta_note.startswith("REPAIR")
        and not _meta_note.startswith("FOLD")
        and (bool(open_before) or had_shot)
        and not (intent in ("casual", "") and not asked)
        and (
            (intent in ("casual", "") and not shot_patched)
            or intent in ("shot", "mixed")
        )
    )
    if needs_verify:
        events.publish(sid, {
            "type": "scripter_working",
            "status": "updating",
            "message": _scripter_status_message(locale=locale, soft=True),
            "whisper": vitality.silence_whisper(locale=locale),
        })
        verify = await _call_duet_scripter(
            ollama, session,
            note=str(text or "").strip(),
            cfg=cfg, partner=partner, name_a=name_a, name_b=name_b,
            mode="compile", images=None, verify=True,
        )
        v_intent = str(verify.get("intent") or "casual")
        v_patch = notebook_mod.guard_partner_patch(
            dict(verify.get("patch") or {}), partner=partner,
        )
        if v_intent in ("shot", "mixed") or any(
            k in v_patch for k in notebook_mod.SHOT_KEYS
        ) or str(v_patch.get("wearing_drop") or "").strip():
            result = verify
            intent = v_intent
            patch = v_patch
            shot_patched = any(k in patch for k in notebook_mod.SHOT_KEYS) or bool(
                str(patch.get("wearing_drop") or "").strip()
            )
            notebook_mod.apply_patch(nb, patch)
            notebook_moved = int(nb.get("rev") or 0) > rev_before

    # Did the compile answer the whole line? Clerk already named the fields;
    # repair only runs when a shot/mixed turn still misses them.
    if asked and intent in ("shot", "mixed"):
        missing = sorted(asked - set(patch))
        if missing:
            logger.info("[muse] repair: line asked for %s, patch had %s",
                        missing, sorted(set(patch) & set(chain.CLASSIFY_FIELDS)))
            fix = await _call_duet_scripter(
                ollama, session, note=str(text or "").strip(), cfg=cfg,
                partner=partner, name_a=name_a, name_b=name_b,
                mode="compile", images=None,
                repair=chain.scripter_repair_note(missing),
            )
            fix_patch = notebook_mod.guard_partner_patch(
                dict(fix.get("patch") or {}), partner=partner,
            )
            # Only the fields that were missing. A repair is not a second turn:
            # anything else it decided to rewrite is not what was asked for.
            fix_patch = {k: v for k, v in fix_patch.items() if k in missing}
            if fix_patch:
                # The repair's reasons are the ones that count for these
                # fields: the first pass did not write them at all.
                why.update(notebook_mod.clean_why(fix.get("why"), fix_patch))
                notebook_mod.apply_patch(nb, fix_patch)
                notebook_moved = int(nb.get("rev") or 0) > rev_before
                shot_patched = True
            still = sorted(set(missing) - set(fix_patch))
            if still:
                # Twice asked, twice not written — but not said out loud yet.
                # The turn is not over: the Muse still has to speak, and the
                # fold pass afterwards folds her CARD into beat. Measured on a
                # real session, that fold wrote the missing beat 44 seconds
                # AFTER the studio had already apologised for not having it:
                #
                #   02:34:10 Studio 「beat が書き取れませんでした」
                #   02:34:54 scripter_fold → beat gains "waving one hand ..."
                #
                # So the notice is parked here and settled in
                # `_settle_repair_notice`, once the whole turn has had its go.
                session["repair_notice"] = {
                    "fields": still,
                    "before": {
                        k: str(before_shot.get(k) or "") for k in still
                    },
                }

    # A removal the studio could not resolve on its own. Two coats in the
    # outfit and「コートを脱いで」has no single referent; no coat at all and
    # they are thinking of a different shoot. Guessing here undresses her
    # wrongly and says nothing, which is how the coat stayed on for three
    # turns with no sign anything had gone wrong. Ask, in the room, once.
    drop_ask = str(patch.get("wearing_drop") or "").strip()
    if drop_ask:
        hits = notebook_mod.garment_matches(str(nb.get("wearing") or ""), drop_ask)
        held = str(nb.get("wearing") or "").strip() or "（なし）"
        if len(hits) >= 2:
            _chat_append(
                session, role="system", name="Studio", kind="system",
                text=(
                    f"「{drop_ask}」が {len(hits)} つあります"
                    f"（{ '、'.join(hits) }）。どれを脱ぎますか？"
                    if locale.startswith("ja") else
                    f"There are {len(hits)} of those: {', '.join(hits)}. Which one?"
                ),
            )
        elif not hits and str(nb.get("wearing") or "") == str(
            before_shot.get("wearing") or ""
        ):
            _chat_append(
                session, role="system", name="Studio", kind="system",
                text=(
                    f"「{drop_ask}」は着ていないみたいです。いまは {held}。"
                    if locale.startswith("ja") else
                    f"She is not wearing that. Right now: {held}."
                ),
            )

    # Struck means "never restore this", and it used to be written on EVERY
    # turn from whatever words happened to leave `wearing`. `wearing` is
    # rewritten whole by each compile, so a rephrase banished words nobody had
    # asked to remove. Measured on a live session where the showrunner said
    # only 「ちょっとおしゃれ目の服できてくれる？」 and never asked her to take
    # anything off:
    #
    #   struck: outfit, stylish, stylish_outfit, white, white_blouse, blouse,
    #           stylish_blouse
    #   wearing: blue skirt, bob cut hair          ← no top at all
    #
    # and because the match walks word parts, a banished `white` then blocked
    # white_shirt, white_dress, white_skirt and white_hair as well. Nothing
    # could put a top back on her, in that session, ever.
    #
    # So it is written only when the showrunner actually took something off —
    # `wearing_drop` resolving to exactly one garment. Everything else is a
    # rewording, and a rewording must not ban a word. What keeps a removed coat
    # out of the next take is the notebook itself: the weave builds the bag
    # from `wearing`, and `notebook.drop_garments_not_in_wearing` drops what is
    # no longer in it. That check needs no memory and cannot accumulate.
    if drop_ask and len(notebook_mod.garment_matches(
        str(before_shot.get("wearing") or ""), drop_ask,
    )) == 1:
        notebook_mod.record_struck_from_wearing(
            session, prev_wearing=prev_wearing,
            new_wearing=str(nb.get("wearing") or ""),
        )
        if partner:
            notebook_mod.record_struck_from_wearing(
                session, prev_wearing=prev_wearing_b,
                new_wearing=str(nb.get("wearing_b") or ""),
            )
    # Only clothes are struck. `struck` means "never restore this", which is
    # what a garment the showrunner took off needs and what a place, a pose and
    # a crop must never get: they come and go by nature, and the weave rebuilds
    # the whole tag bag from the notebook every take anyway, so the live
    # notebook is already the authority on where she is and what she is doing.
    #
    # Measured live: a scene line had absorbed the pose — it read "<place>,
    # standing by the fence, late afternoon". The next compile shortened that
    # line, `standing` left scene, and the word went
    # into struck. From then on the scripter was told never to restore
    # `standing` — so 「立って」 could not be obeyed, on that turn or any later
    # one. Props keep their own permanence through the planner's ledger
    # (`strike_dropped_props`), which strikes by name rather than by token.
    _ = (prev_scene, prev_beat, prev_frame)

    session["notebook"] = nb
    session["standing"] = list(nb.get("standing") or [])
    session["digest"] = notebook_mod.summary_for_muse(nb, name_a=name_a, name_b=name_b)
    _note_rewrite(
        session, "scripter_fold" if fold else "scripter",
        before=before_shot, after=notebook_mod.shot_snapshot(nb), intent=intent,
        why=why,
    )
    flash = vitality.notebook_flash_key(patch) if shot_patched else ""
    events.publish(sid, {
        "type": "scripter_working",
        "status": "updating",
        "flash": flash,
        "message": (
            _scripter_status_message(locale=locale, soft=False)
            if shot_patched or intent in ("shot", "mixed")
            else _scripter_status_message(locale=locale, soft=True)
        ),
    })

    # The patch itself is the last word on whether the picture moved. A compile
    # that rewrote `beat` and then labelled the turn `recall` would otherwise
    # leave `chat_only` true, and — measured on a live shoot — would pull the
    # whole of the previous session in behind it.
    #
    # Measured on the 30-pack (30 x 5): the scripter's own label is right 68%
    # of the time when the contract does not spend words on the four intents,
    # and 92% when it is simply derived from what was written. Explaining them
    # in the contract does buy 93% — and costs the notebook, 96.0% down to
    # 86.7%. So it is derived here, raising only, exactly like the clerk above.
    #
    # **This has to run before the recall block below.** It used to sit forty
    # lines further down, after `cited_memories` and `prior_session_log` had
    # already been filled. On the 2026-08-21 shoot that put 4,420 characters of
    # the previous session into her context — more than today's conversation
    # (4,533) — and she answered three turns running out of the last shoot:
    # 「あの時の、大きな会場の片隅で」 while she was standing in a park.
    #
    # `atmosphere` is left out: mood shifts on ordinary chat without anyone
    # asking for a take.
    said_intent = intent          # 部屋が「その一言をどう読んだか」。記録に使う
    if patch and intent not in ("shot", "mixed"):
        moved = [k for k in ("scene", "bg", "light", "frame", "wearing", "beat",
                             "wearing_b", "beat_b") if k in patch]
        if moved:
            logger.info("[muse] patch raised intent %r → shot (%s)",
                        intent, ", ".join(moved))
            intent = "shot"

    # Two different costs used to ride the same trigger, and `1b75355` shut
    # both off together. They do not deserve the same gate:
    #
    #   the page he asked about   <=900 chars, and the answer to his question
    #   last session's transcript 4,000 chars, which is what drowned today
    #
    # `1b75355` was about the transcript — 過去 7,678字 ＞ 今日 4,533字. The page
    # was collateral. Measured on the real chain, all seven recall turns that
    # lost their page had **both readers saying `recall`** and were overruled
    # by a stray patch:
    #
    #   「黄色いワンピース着てた日のこと、覚えてる？」 compile=recall clerk=recall
    #                                            patch=['wearing'] → shot
    #
    # ## Which reader decides the page
    #
    # Not the compile. It answers `recall` on nearly every turn — `1b75355`
    # said so and the chain confirms it: 9 out of 9 lines that plainly moved
    # the picture (「じゃあ髪をポニーテールにしよっか。」「いい天気だね。」) still came
    # back `recall`. Gating the page on that is the same as not gating it,
    # which is what the resident copy already was.
    #
    # The clerk reads his line and nothing else, and it is the one reader that
    # tells a question about a past shoot from small talk. Measured on the
    # real chain, 10 lines × 3:
    #
    #     過去を訊いた一言   recall 21/21
    #     画を動かす一言     無駄引き 0/9   (clerk: shot, shot, casual)
    #
    # It already runs every turn in the gather above, so this costs nothing.
    # When it is unreadable ("") it has no opinion, and the compile decides —
    # the same fallback `classify_intent` documents.
    asked_back = (clerk_kind == "recall") if clerk_kind else (
        said_intent == "recall"
    )
    if not fold:
        session["cited_memories"] = []
        session["prior_session_log"] = ""
        # **振り返りのターンかどうかを残す。** `asked_back` は**係**の `recall`、
        # 折り込みの門は**コンパイル**の `recall` で、別々の判断だった ——
        # 係が振り返りと読んで前の撮影を彼女の手元に置き、コンパイルが
        # `casual` と言えば、**折り込みは開いたまま**。そこで彼女が思い出した
        # ポーズを語れば、それが `beat` に入って撮られる。
        session["looked_back"] = bool(asked_back)
        if asked_back:
            char_id = str(inputs.get("character_id") or "")
            try:
                session["cited_memories"] = await memories_db.search(
                    db, ollama, character_id=char_id, query=text, limit=3,
                )
            except Exception:
                logger.debug("[muse] recall search failed", exc_info=True)
            await _attach_recall_context(
                db, session, query=text, with_prior=(intent == "recall"),
            )
            session["again_feel_hint"] = vitality.again_that_feel_hint(session)

    valid = bool(result.get("valid", True))
    compiled = False

    if not fold:
        # 常設の指示になるのは、**部屋がその一言をどう読んだか**であって、
        # たまたまどの欄が動いたかではない。引き上げる前の判定を使う。
        if said_intent in ("shot", "mixed"):
            _note_standing(session, text)
        else:
            session["just_banned"] = []
            session["just_restored"] = []

    if shot_patched or (notebook_moved and intent in ("shot", "mixed")):
        session["craft_dirty"] = True

    if fold and intent not in ("shot", "mixed") and prev_intent:
        session["scripter_intent"] = prev_intent
    else:
        session["scripter_intent"] = intent
    events.publish(sid, {
        "type": "scripter_done",
        "intent": session.get("scripter_intent") or intent,
        "compiled": compiled,
        "valid": valid,
        "dirty": bool(session.get("craft_dirty")),
        "notebook_rev": int(nb.get("rev") or 0),
        "notebook_rev_compiled": int(session.get("notebook_rev_compiled") or 0),
    })
    return result


def _social_block(session: dict[str, Any]) -> str:
    """Lounge whispers — trends and friend feedback. Soft hints only."""
    lines = [str(m).strip() for m in (session.get("social_seeds") or []) if str(m).strip()]
    if not lines:
        return ""
    return "\n".join([
        "LOUNGE WHISPERS (only when the theme fits):",
        "Soft tips from friends in the lounge. Hint at most as \"maybe try "
        "this today\" when it matches; otherwise ignore. Do not force into "
        "the picture. Do not grow a prop shopping list.",
        *(f"- {m}" for m in lines[:5]),
    ])


def _pitch_recommend_block(session: dict[str, Any]) -> str:
    rec = session.get("pitch_recommend") or {}
    text = str(rec.get("text") or "").strip()
    if not text:
        return ""
    return "\n".join([
        "SHOWRUNNER-LIKED PITCH (one-shot; she may mention wanting this shot.",
        "Do not force it into the picture unless the conversation goes there.):",
        f"- {text}",
    ])


def _handpost_block(session: dict[str, Any]) -> str:
    """Pinned studio handpost notices — short standing guidance."""
    lines = [str(m).strip() for m in (session.get("handpost_notices") or []) if str(m).strip()]
    if not lines:
        return ""
    return "\n".join([
        "STUDIO HANDPOST (short standing guidance; do not force into the picture):",
        *(f"- {m}" for m in lines[:3]),
    ])


def _caught_block(session: dict[str, Any]) -> str:
    """The one-off line about her diary having been read, if one is owed."""
    caught = session.get("caught") or {}
    if not caught.get("ids"):
        return ""
    return crew.caught_block(str(caught.get("summary") or ""))


async def _consume_caught(db, session: dict[str, Any]) -> None:
    """She has said it. Never again for those entries.

    Called after the turn that carried the block, not before it: a turn that
    fell over must not spend the moment.
    """
    caught = session.get("caught") or {}
    ids = [str(i) for i in (caught.get("ids") or []) if i]
    if not ids:
        return
    session["caught"] = {}
    char_id = str(_inputs(session).get("character_id") or "")
    if char_id:
        try:
            await presets_db.mark_secret_banter_fired(db, char_id, ids)
        except Exception:
            logger.warning("[muse] could not mark diaries acknowledged", exc_info=True)


def _count_circle_mention(session: dict[str, Any]) -> None:
    """Did she just bring a friend up? Two per session and the note goes away.

    Counted from what she actually said rather than from a die roll, so a
    session where it never came up naturally keeps its full allowance.
    """
    names = [str(n) for n in (session.get("circle_names") or []) if str(n).strip()]
    if not names:
        return
    said = ""
    for m in reversed(_chat_rows(session)):
        if str(m.get("role") or "") != "user":
            said = str(m.get("text") or "")
            break
    if not said:
        return
    if any(n in said or n.split()[-1] in said for n in names):
        try:
            session["circle_mentions"] = int(session.get("circle_mentions") or 0) + 1
        except (TypeError, ValueError):
            session["circle_mentions"] = 1


async def _after_actress_spoke(db, session: dict[str, Any]) -> None:
    """Spend one-shot memory that rode on the turn that just landed."""
    session["manager_note"] = False
    _count_circle_mention(session)
    await _consume_caught(db, session)
    await _consume_social_seeds(db, session)
    await _consume_pitch_recommend(db, session)


def _format_duet_chat_line(session: dict[str, Any], msg: dict[str, Any]) -> list[str]:
    """One chat message → prompt lines. W-Muse keeps A/B names, not『私』潰し."""
    if msg.get("role") == "user":
        return [f"- 総監督: {msg.get('text')}"]
    turns = [t for t in (msg.get("turns") or []) if str((t or {}).get("text") or "").strip()]
    if turns:
        return [
            f"- {str(t.get('speaker_name') or 'Muse').strip()}: {str(t.get('text') or '').strip()}"
            for t in turns
        ]
    name = str(msg.get("name") or "").strip() or "私"
    return [f"- {name}: {msg.get('text')}"]


def _duet_transcript(session: dict[str, Any], *, user_turns: int = 20) -> str:
    """Recent conversation by Showrunner turns, not raw message count.

    ASIDE (banter) rides with its SAY, so a 12-message window would shrink to
    ~4 exchanges. Cut from the oldest user line so instructions are not dropped
    while mutter remains.
    """
    rows = [m for m in _chat_rows(session) if m.get("role") in ("user", "muse")]
    if not rows:
        return ""
    keep_from = 0
    users = 0
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("role") == "user":
            users += 1
            if users >= user_turns:
                keep_from = i
                break
    lines: list[str] = []
    for m in rows[keep_from:]:
        lines.extend(_format_duet_chat_line(session, m))
    return "\n".join(lines)


def _duet_user_prompt(
    session: dict[str, Any], text: str, *, prep: bool, intent: str = "",
) -> str:
    """What she is handed. Muse-only context (never the scripter's inputs)."""
    inputs = _inputs(session)
    locale = str(inputs.get("locale") or "ja")
    theme = _theme_for_models(session)
    talk = _duet_transcript(session)
    intent = str(intent or session.get("scripter_intent") or "").strip().lower()
    chat_only = (not prep) and intent in ("casual", "recall")
    parts = [
        "LANGUAGE: Instructions below are in English. "
        + crew.say_language_rule(locale)
        + " Never print English rule headings inside SAY.",
    ]
    if theme:
        if chat_only:
            parts.append(
                "THEME (today's opening ask — do not steer back to it unless "
                "they brought the shoot up this turn):\n" + theme
            )
        else:
            parts.append(f"THEME (Showrunner's opening ask):\n{theme}")
    noticed = str(session.get("propose") or "").strip()
    if noticed:
        parts.append(
            "THE STUDIO NOTICED (not in the notebook, and nobody has decided "
            "it — the scripter wrote it down while reading the room):\n"
            f"{noticed}\n"
            "Yours to raise or let go. If it belongs in the picture you are "
            "making, say it in your own words and let the Showrunner decide. "
            "If it does not, drop it — nobody needs to hear it was considered."
        )
    memories = _memory_block(session)
    if memories:
        parts.append(memories)
    circle_note = _circle_note(session)
    if circle_note:
        parts.append(circle_note)
    cited = _cited_memories_block(session)
    if cited:
        parts.append(cited)
    prior = str(session.get("prior_session_log") or "").strip()
    if prior and intent == "recall":
        parts.append(
            "PRIOR SESSION LOG (a past shoot they asked about. Answer from "
            "this in conversation. Do not paint it into today's picture):\n"
            + prior
        )
    for block in (
        _bond_block(session),
        _taste_block(session),
        _chemistry_block(session),
        _cited_allow_block(session),
    ):
        if block:
            parts.append(block)
    social = _social_block(session)
    if social:
        parts.append(social)
    liked = _pitch_recommend_block(session)
    if liked:
        parts.append(liked)
    handpost = _handpost_block(session)
    if handpost:
        parts.append(handpost)
    caught = _caught_block(session)
    if caught:
        parts.append(caught)

    if uses_notebook(session):
        nb = notebook_mod.of(session)
        name_a = str(
            (session.get("character") or {}).get("name_ja")
            or (session.get("character") or {}).get("name") or "Lead"
        )
        partner = session.get("partner_character") or {}
        name_b = str(partner.get("name_ja") or partner.get("name") or "")
        summary = notebook_mod.summary_for_muse(nb, name_a=name_a, name_b=name_b)
        if summary and not chat_only:
            parts.append(
                "SHOT NOTEBOOK (talk summary only — not tags; do not recite "
                "as a checklist):\n" + summary
            )
        standing = nb.get("standing") or session.get("standing") or []
        if standing and not chat_only:
            parts.append(
                "STANDING ORDERS (honour if asked; do not force into the picture):\n"
                + "\n".join(f"- {s}" for s in standing if str(s).strip())
            )
    elif on_facets(session):
        orders = "\n\n".join(b for b in [
            directives_block(session),
            facets.standing_block(list(session.get("standing") or [])),
        ] if b)
        if orders:
            parts.append(orders)
    else:
        orders = brief_mod.orders_block(
            list(session.get("notes") or []),
            carried_out=list(session.get("carried_out") or []),
            removed_now=list(session.get("just_banned") or []),
            restored_now=list(session.get("just_restored") or []),
        )
        if orders:
            parts.append(orders)

    if talk:
        parts.append(f"CONVERSATION SO FAR:\n{talk}")
    if text.strip():
        parts.append(f"SHOWRUNNER'S LATEST LINE:\n{text.strip()}")
    # **監督の一言のすぐ後ろ。** ここに置いてあると書いてあったが、実際は
    # 78ブロックも前にあった —— 彼女が最後に読むのは監督の一行で、流せという
    # 指示ははるか上に埋もれていた。総監督の報告「判定が出ているのに冗談で
    # 流すのが効いていない」はこれ。**メモは指示の直後で読まれないと、指示の
    # ほうが勝つ。**
    manager = _manager_note(session)
    if manager:
        parts.append(manager)

    partner_on = bool(
        (session.get("partner_character") or {})
        or str(inputs.get("partner_preset") or "").strip()
    )
    extras = vitality.vitality_talk_extras(session, partner=partner_on)
    if extras:
        parts.append(extras)

    if not prep:
        card = str(session.get("muse_card") or "").strip()
        if card and not chat_only:
            parts.append(
                "PREVIOUS CARD (machine names last turn; update it):\n" + card
            )
        if chat_only:
            how = [
                "HOW TO SPEAK THIS TURN:",
                "- This is conversation. Answer what they asked. End in "
                "conversation.",
                "- Do not name today's place, clothes, pose, or camera unless "
                "they brought the shoot up this turn.",
                "- Do not offer a PITCH. Omit CARD unless they asked about "
                "today's picture.",
                "- Write ASIDE this turn: a short cute inner mutter (whisper).",
                "- Past shoots: answer from memories / diary / CITED / PRIOR "
                "SESSION LOG. Use the details you have. Soft-miss『そこまでは…』"
                "only for facts you were not given.",
                "- Never say you are getting ready / can get ready. "
                "Never print English section titles inside SAY.",
            ]
            if not str(text or "").strip():
                how.append(
                    "- Opening: greet and talk. Theme is mood, not a shot "
                    "briefing. If a diary-caught whisper is owed, that comes first."
                )
            if intent == "recall":
                how.append(
                    "- They asked about a past shoot. Stay on that. Do not "
                    "return to today's theme or shot forks."
                )
            if (session.get("caught") or {}).get("ids"):
                how.append(
                    "- Diary-caught whisper takes the opening. Stay on that "
                    "moment. Do not start a wardrobe briefing."
                )
        else:
            how = [
                "HOW TO SPEAK THIS TURN:",
                "- Sense and body first. Do not recite a checklist every turn.",
                # Never hand her the words. This line used to name the phrase
                # 「こうしますね」 and she wrote it back: measured live, 26% of
                # her lines carried it and three turns in a row opened
                # 「〜。わかった、こうしますね。」 — the one thing in the room
                # that reads as a machine rather than a person.
                "- If they gave a direction, take it in SAY before anything "
                "else — say the action back in your own words, differently "
                "each time, so they know it landed. Never reach for the same "
                "phrasing you used last turn. Then the body-feel.",
                "- If they ask what you are wearing / where / what time / how it "
                "looks now, answer with nouns from the still and CARD. "
                "Do not dodge with『なんかいい感じ』.",
                "- The Showrunner's newest line wins; drop what it replaces.",
                "- CARD BEAT is the body action they just asked for (absolute). "
                "A short pose line replaces the old beat.",
                "- You may play-act an OPEN proposal in SAY before it is locked.",
                # 制作スタッフ: the crew proposes picture forks at the table, so
                # a PITCH from her here is a second voice asking the same
                # question in the same turn.
                (
                    "- PITCH only when a real picture fork is open, OPEN is empty, "
                    "and they did not just pick. No interview chains. Not every turn."
                    if is_duet(session) else
                    "- Do not offer a PITCH. The crew proposes the forks; you play "
                    "the moment and answer the Showrunner."
                ),
                "- Past shoots: from memories / CITED / PRIOR SESSION LOG. "
                "Use known details. Soft-miss『そこまでは…』only for facts you "
                "were not given.",
                "- The attached still is the previous take (the base). CARD is "
                "that base plus what this conversation changed. Do not copy the "
                "photo as the current ask.",
                "- Write ASIDE this turn: a short cute inner mutter (whisper).",
                "- Never say you are getting ready / can get ready. "
                "Never print English section titles inside SAY.",
            ]
            if (session.get("caught") or {}).get("ids"):
                how.append(
                    "- Diary-caught whisper (ASIDE + a soft SAY) comes first. "
                    "Then confirm today's direction if they gave one."
                )
        parts.append("\n".join(how))
        return "\n\n".join(p for p in parts if p)

    # Prep on the notebook path: feel the shot from the notebook, never TAGS.
    if uses_notebook(session):
        nb = notebook_mod.of(session)
        name_a = str(
            (session.get("character") or {}).get("name_ja")
            or (session.get("character") or {}).get("name") or "Lead"
        )
        partner = session.get("partner_character") or {}
        name_b = str(partner.get("name_ja") or partner.get("name") or "")
        feel = notebook_mod.summary_for_muse(nb, name_a=name_a, name_b=name_b)
        if feel:
            parts.append(
                "CURRENT SHOT (read it back by feel — no tag roll-call):\n" + feel
            )
    else:
        previous = str((session.get("craft") or {}).get("prompt") or "")
        if previous:
            parts.append(
                "CURRENT CRAFT (read it back by feel after densify — no tag "
                "roll-call):\n" + previous
            )
    parts.append(
        "PREP FINISH TURN: the picture already lives in the notebook. "
        "SAY only — place air, body feel, camera distance in your own words. "
        "No prop inventory, no \"I changed X\" reports. "
        + crew.say_language_rule(locale)
    )
    return "\n\n".join(p for p in parts if p)


def _facet_prep_prompt(
    session: dict[str, Any], names: list[str],
    *, partner_character: dict[str, Any] | None = None,
) -> str:
    """What she is handed to rewrite some parts of the shot.

    Deliberately short, and deliberately the same length on turn twenty as on
    turn three. What is NOT here is the point: no transcript, no previous
    assembled positive, no append-only order list. Those are what a long session
    drowned in — the prep turn was handed twelve raw chat turns, every standing
    order ever given, and the whole of the last prompt, and asked to work out
    which parts of that were still true.

    The shot is state now. The table says what the picture is; the direction
    says what the Showrunner last asked of each part; the rest is conversation
    and belongs to talk mode.
    """
    inputs = _inputs(session)
    theme = str(inputs.get("theme") or "").strip()
    table = facets.table_of(session)
    opening = not facets.table_rev(table)

    char_a = session.get("character") or {}
    name_a = str(char_a.get("name_ja") or char_a.get("name") or "")
    name_b = ""
    label_names: dict[str, str] | None = None
    if partner_character:
        name_b = str(partner_character.get("name_ja") or partner_character.get("name") or "")
        label_names = {"costume_b": name_b, "pose_b": name_b, "expression_b": name_b}

    parts = [
        f"お題（総監督が最初に言ったこと）:\n{theme}" if theme else "",
        f"Style: {_style(session)}",
        f"Framing: {_framing(inputs)}",
    ]
    digest = str(session.get("digest") or "").strip()
    if digest:
        # Shown to EVERY facet-writing turn, whether or not the router named
        # this facet today — this is what closes the gap a routed directive
        # alone cannot: the part being rewritten right now may hold a stale
        # duplicate of something decided against on some earlier, unrelated
        # turn, and only a standing reminder like this one reaches it. Placed
        # ahead of "いまの画" on purpose: read this first, then the snapshot.
        parts.append("ここまでの決定（会話の細部より、これを優先して読むこと）:\n"
                     + digest)
    if not opening:
        parts.append("いまの画（すでに決まっている部分）:\n"
                     + facets.table_block(table, names=label_names))
    orders = directives_block(session, only=names if not opening else None)
    if orders:
        parts.append(orders)
    standing = facets.standing_block(list(session.get("standing") or []))
    if standing:
        parts.append(standing)

    def _label(n: str) -> str:
        if n == "costume_b":
            return f"{name_b}の衣装"
        if n == "pose_b":
            return f"{name_b}のポーズ"
        if n == "expression_b":
            return f"{name_b}の表情"
        return facets.FACET_LABELS[n]

    labels = "・".join(_label(n) for n in names)
    if opening and partner_character:
        parts.append(
            f"この一枚を、{name_a}と{name_b}の二人で全部決めて。場所・時間・光・"
            "小物・カメラは二人共通、衣装・ポーズ・表情はそれぞれ自分の分だけ"
            f"（{name_a}は{name_a}の、{name_b}は{name_b}の）を決めて。"
            "決めたら SAY でフレームに何が入っているかをそれぞれ自分の言葉で"
            "読み上げて。小物は名前で。隠さないこと。"
        )
    elif opening:
        parts.append(
            "この一枚を、全部あなたが決めて。場所・時間・光・小物・衣装・ポーズ・"
            "表情・カメラ。決めたら SAY でフレームに何が入っているかを自分の言葉で"
            "読み上げて。小物は名前で。隠さないこと。"
        )
    else:
        parts.append(
            f"総監督の指示で変わるのは {labels} だけ。そこだけ書き直して。\n"
            "ほかの部分はもう決まっている。書き直さないし、触れない"
            "（書いても捨てられる）。\n"
            "SAY では、何をどう変えたか、捨てたものも含めて自分の言葉で言って。"
        )
    return "\n\n".join(p for p in parts if p)


def _absorb_duet_pose(session: dict[str, Any], card: str) -> None:
    """Same-turn: fold Muse CARD beat into the notebook Script just compiled.

    Script runs before she talks, so her acted pose would otherwise wait until
    the next compile — and a ② pressed now would miss it.
    """
    nb = notebook_mod.of(session)
    absorbed = notebook_mod.absorb_muse_card(nb, card)
    if not absorbed:
        return
    session["notebook"] = nb
    partner = session.get("partner_character") or {}
    name_a, name_b = _muse_names(session, partner if partner else None)
    session["digest"] = notebook_mod.summary_for_muse(nb, name_a=name_a, name_b=name_b)
    session["craft_dirty"] = True
    craft = session.setdefault("craft", {})
    if absorbed.get("beat"):
        craft["pose_intent"] = str(absorbed["beat"])[:240]


async def _duet_talk(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
    prep: bool = False, pitch: bool = True, fold: bool = True,
) -> dict[str, Any]:
    """Conversation only — Muse writes SAY; craft comes from the scripter.

    Also the Lead's turn in 制作スタッフ (`_run_crew_lead_turn`): the crew room
    wants the same person — voice, memory, ASIDE, CARD — not a seat in a
    packed roster. There she does not offer picture forks (`pitch=False`; the
    crew has its own way of proposing), and the fold pass is scheduled by the
    caller (`fold=False`) so it runs after the whole table has spoken.
    """
    inputs = _inputs(session)
    sid = session["session_id"]
    lead = crew.DEFAULT_MEMBER["actress"]
    name = _muse_display_name(session, lead)
    events.publish(sid, {"type": "muse_speaking", "muse_id": lead, "name": name})
    # Boarded Comfy stills. Showing them needs a model that can read images —
    # one that cannot returns empty rather than erroring (see CLAUDE.md).
    vision_images = await board_images(db, session)

    partner_character = await _partner_character(db, session)
    tier = await _duet_tier(db, session, partner_character)
    vitality.bump_talk_turn(session)
    session["w_b_leads"] = vitality.should_b_lead(
        session, partner=bool(partner_character),
    )
    # Soft prop-aging hint (Muse voice only).
    session["prop_age_hint"] = vitality.tick_prop_age(
        session, notebook_mod.of(session),
    )

    try:
        say, raw_turns, blind, aside, card, _pitch_said = await chain.run_duet_talk(
            ollama,
            user_prompt=_duet_user_prompt(
                session, text, prep=prep,
                intent=str(session.get("scripter_intent") or ""),
            ),
            model=_vision_model(inputs) if vision_images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            character=session.get("character") or {},
            partner_character=partner_character,
            images=vision_images or None, seed=str(sid),
            on_token=_token_publisher(sid, lead),
            on_feel=lambda w: _log_feel(session, w),
            tier=tier,
            locale=str(inputs.get("locale") or "ja"),
            intent=str(session.get("scripter_intent") or ""),
        )
    except chain.DeclinedTurn:
        # 第二層。判定係は通したが、**彼女が引き受けないと決めた。**
        # 語の一覧で読むのをやめたので、いまここに来る道は塞がっている
        # （`identity.parse_talk_blocks` を参照）。旗が戻ってきたときに
        # 備えて、**第一層と同じ「冗談で流す」へ合流させる**。
        _log_clerk(session, word="self", by="self",
                   why="彼女自身が引き受けないと決めた（理由は書かせていない）")
        session["manager_note"] = True
        session["skip_scripter"] = True
        await session_db.save(db, session)
        return session
    except chain.ChainError as exc:
        raise MuseError(_msg(
            session,
            ja="うまく言葉が出てこないみたいです。もう一度話しかけてください。",
            en="The words didn't come out right. Try talking to her again.",
        )) from exc
    if blind and vision_images:
        _note_blind(session)
    msg = _chat_append(session, role="muse", text=say, muse_id=lead,
                       name=name, kind="craft", turns=_resolve_duet_turns(session, raw_turns))
    _publish_chat(sid, msg)
    if aside and not session.get("manager_note"):
        # **流すターンは、つぶやきを出さない。** 旗の立った中身に内心が
        # 引っぱられる（総監督・2026-08-25:「これに引っ張られます」）。
        # 口では冗談にしておいて、心の中でその話を続けているのが見えるのは、
        # 流したことにならない。
        #
        # **W撮りは、どちらが呟いてもよい。** 枠がそう言っているのに、部屋は
        # 常に主演の名義で積んでいた。実測（総監督の W撮り）で、みおの名義で
        # 「ふふっ、みおちゃんも案外楽しそう」と出た —— 自分を三人称で呼び、
        # 語尾も相手のもの。**中身は相方の声**だった。
        #
        # SAY と同じ `A:` / `B:` の接頭辞で分ける。接頭辞が無ければ、これまで
        # どおり主演の名義（主演撮りはそれで正しい）。
        a_name = str((session.get("character") or {}).get("name_ja") or "")
        b_name = str((session.get("partner_character") or {}).get("name_ja") or "")
        who, said = identity.parse_aside_speaker(
            aside, name_a=a_name, name_b=b_name,
        )
        # `muse_id` は席の id（`crew.DEFAULT_MEMBER["actress"]`）のままにする。
        # 二人を分けるのは `turns` の側 —— SAY が既にその形（`_resolve_duet_turns`）。
        turns = None
        mutter_name = name
        if who and b_name:
            cid, cname = _duet_speaker_label(session, who)
            if cname:
                mutter_name = cname
                turns = [{"speaker_id": cid, "speaker_name": cname,
                          "text": said or aside}]
        mutter = _chat_append(
            session, role="muse", text=said or aside, muse_id=lead,
            name=mutter_name, kind="banter", turns=turns,
        )
        _publish_chat(sid, mutter)
    fresh_card = bool(str(card or "").strip())
    session["muse_card"] = str(card).strip() if fresh_card else (
        session.get("muse_card") or ""
    )
    # CARD is a memo. Script is the only notebook writer — after she speaks,
    # a fold pass may add uncontradicted CARD/SAY body action to beat.
    session["status"] = "chat"
    # One-shot flags consumed after the line lands.
    session["reunion_turn"] = False
    session["cleanup_nudge"] = False
    session["again_feel_hint"] = ""
    session["prop_age_hint"] = ""
    session["w_b_leads"] = False
    session["commit_pitch"] = False
    # Capture before `_after_actress_spoke` clears it: a joke/STOP turn must
    # not fold CARD into beat — mouth deflected, picture must stay put.
    deflecting = bool(session.get("manager_note"))
    await _after_actress_spoke(db, session)
    if fold and uses_notebook(session) and fresh_card and not deflecting:
        began = time.monotonic()
        await _fold_muse_after_talk(
            db, ollama, session, cfg=cfg, user_text=text,
        )
        _stage(session, "折り込み", began)
    else:
        # No fold this turn, but the turn is still over — a parked notice has
        # to be settled here or it is silently dropped on the next line.
        _settle_repair_notice(session)
    await session_db.save(db, session)
    return session


_ARRANGE_ASK_RE = re.compile(
    r"(?i)(?:考えて|かんがえて|アレンジ|任せ|まかせ|好きに|自由に|決めて|"
    r"きめて|いい感じに|よろしく|"
    r"\b(?:you\s+decide|your\s+call|up\s+to\s+you|arrange|improvise|"
    r"surprise\s+me)\b)"
)


def _invited_to_arrange(note: str) -> bool:
    """総監督が**自分で考えてと言ったか**。

    総監督（2026-08-29）「restate がやはり絵を壊します。Muse に自分で考えて・
    アレンジしといてというときだけ動かしたほうがいいです」。

    実測の裏づけ（`78d7ce72`）: 場所しか言っていない5ターンで、見直しが `frame`
    を2回・`beat` を3回書き換え、根拠は**彼女自身の独り言**だった。呼ばれて
    いないのに画を作り替えている。

    **拾い漏れは安全側に倒れる** —— 走らなければ、画は台本係の書いたまま。
    余分に拾っても、それは以前の挙動に戻るだけ。
    """
    return bool(_ARRANGE_ASK_RE.search(str(note or "")))


async def _muse_checks_the_notebook(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any], note: str,
) -> None:
    """She reads the notebook at the end of her turn and says what is wrong.

    Every field is written as a delta off one line of direction, and a field
    that has accreted stops being movable — the compile edits inside it instead
    of replacing it. Measured live: `beat` read
    `sitting, eating cake, looking at cake` while `frame` read
    `close-up, facing camera`, the showrunner asked three separate times for
    her to look at the camera, four repairs fired, and the notebook did not
    change once. `looking_at_cake` was still in the tag bag on the last take.

    Nothing in the machinery could see that contradiction. She can — she is the
    one doing it. So she names the parts that are wrong (closed vocabulary: a
    name that is not a field falls on the floor) and each one is then said over
    from the start rather than edited, which is the move 衣装部屋 makes for the
    outfit and the only thing that has been measured to get past a stuck field.

    Silent by design. She has just spoken; a second line from her explaining
    that the studio wrote it down wrong is not part of the picture they are
    making. The panel sees it in the rewrite log.
    """
    if ollama is None or not uses_notebook(session):
        return
    if str(session.get("scripter_intent") or "") not in ("shot", "mixed"):
        return
    # **呼ばれたときだけ動く。** ここは毎ターン走って 1回 18〜22秒を払い、
    # 頼まれていない欄を書き換えていた（実測 `78d7ce72`）。総監督の指示で
    # 「自分で考えて・アレンジしといて」と言われた回に限る。
    if not _invited_to_arrange(note):
        _stage(session, "見直し（呼ばれていないので走らず）", time.monotonic())
        return
    inputs = _inputs(session)
    partner_character = await _partner_character(db, session)
    partner = bool(partner_character)
    name_a, name_b = _muse_names(session, partner_character)
    nb = notebook_mod.of(session)
    voice = crew.actress_duet_prompt(
        session.get("character") or {}, mode="review",
        locale=str(inputs.get("locale") or "ja"),
        seed=str(session.get("session_id") or ""),
    )
    block = notebook_mod.render(
        nb, name_a=name_a, name_b=name_b or ("Partner" if partner else ""),
    )
    try:
        named = await chain.run_notebook_review(
            ollama, system=voice, notebook_block=block,
            muse_says=_last_lead_say(session), note=note,
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
        )
    except Exception:
        logger.warning("[muse] notebook review failed", exc_info=True)
        return
    if not named:
        return
    # Her own contract says naming more than two is almost always a misreading.
    # Rewriting half the notebook on one turn is how a shoot loses its place.
    named = named[:_RESTATE_MAX]
    before_shot = notebook_mod.shot_snapshot(nb)
    said_why: dict[str, str] = {}
    for field in named:
        try:
            _, value, why_line = await chain.run_restate(
                ollama,
                system=crew.actress_duet_prompt(
                    session.get("character") or {}, mode=f"restate:{field}",
                    locale=str(inputs.get("locale") or "ja"),
                    seed=str(session.get("session_id") or ""),
                ),
                field=field,
                current=str(nb.get(field) or ""),
                transcript=_duet_transcript(session),
                note=note,
                model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            )
        except Exception:
            logger.warning("[muse] restate %s failed", field, exc_info=True)
            continue
        if not str(value or "").strip():
            continue
        if str(why_line or "").strip():
            said_why[field] = str(why_line).strip()
        notebook_mod.apply_patch(nb, {field: value})
    after_shot = notebook_mod.shot_snapshot(nb)
    if after_shot == before_shot:
        return
    session["notebook"] = nb
    session["craft_dirty"] = True
    session["digest"] = notebook_mod.summary_for_muse(
        nb, name_a=name_a, name_b=name_b,
    )
    logger.info("[muse] she said over %s", ", ".join(named))
    _note_rewrite(
        session, "restate", before=before_shot, after=after_shot, intent="shot",
        why=said_why,
    )


def _settle_repair_notice(session: dict[str, Any]) -> None:
    """Record what the whole turn failed to write — in the panel, not the room.

    The compile and its repair both run before the Muse has spoken, and the
    fold that follows her line is a third chance at `beat`, so the check waits
    for the whole turn and only counts fields that really did not move.

    This used to say so in chat — 「『beat』が書き取れませんでした。もう一度、
    そこだけ言ってもらえますか？」 — and that was wrong twice over. It broke
    the room: the showrunner is directing an actress, and a studio voice
    interrupting to ask him to repeat himself is not part of the picture they
    are making together. And it was often simply untrue. Measured on a live
    run, three of these went out and two of them were fixed by the very next
    thing the system did:

        「カーディガン羽織って」 → apology → the next turn wrote `cardigan`
        「カーディガン脱いで」   → apology → the wardrobe button struck it

    The signal itself is worth keeping — it is what made this debuggable at
    all — so it goes to the rewrite log, which the debug pane already renders
    live (`MusePanel.vue`'s `rewriteLog`). Nothing reaches the chat.
    """
    notice = session.pop("repair_notice", None)
    if not isinstance(notice, dict):
        return
    nb = notebook_mod.of(session)
    before = notice.get("before") or {}
    still = [
        f for f in (notice.get("fields") or [])
        if str(nb.get(f) or "") == str(before.get(f) or "")
    ]
    if not still:
        logger.info("[muse] repair notice withdrawn — the turn wrote %s after all",
                    notice.get("fields"))
        return
    logger.info("[muse] the turn never wrote %s", ", ".join(still))
    _note_rewrite(
        session, "repair_missed",
        before=notebook_mod.shot_snapshot(nb),
        after=notebook_mod.shot_snapshot(nb),
        extra={f: {"before": str(before.get(f) or ""), "after": "(未着手)"}
               for f in still},
    )


async def _fold_muse_after_talk(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any],
    user_text: str = "",
) -> None:
    """Second scripter pass: fold uncontradicted Muse CARD/SAY action into beat.

    Does not absorb the CARD wholesale. The showrunner's posture/place/clothes
    from the first compile stay; hands/head/held props may be added.

    This is also the end of the turn, so it is where a parked repair notice is
    settled — including on the paths that return early without folding.
    """
    try:
        # **走ったかどうかを残す。** `record_rewrite` は差分があった回しか
        # 書かないので、記録だけ見ると折り込みは百発百中に見える。空振りが
        # 見えないと、8秒を払う価値があるかを判断できない。
        if str(session.get("scripter_intent") or "") == "recall":
            _stage(session, "折り込み（recall なので走らず）", time.monotonic())
            return
        # 係が振り返りと読んだ回も折らない。**思い出した話は、いまの画では
        # ない** —— 前の撮影を手元に置いたまま折ると、その場面が撮られる。
        if session.get("looked_back"):
            _stage(session, "折り込み（振り返りのターンなので走らず）",
                   time.monotonic())
            return
        # CARD used to gate the whole fold. When the Lead skipped CARD, every
        # body proposal from 演出/振付 in SAY died in chat — the Showrunner
        # heard the room commit to a posture and the notebook never moved.
        # Fold already forbids inventing clothes/place/crop and keeps the
        # first compile's stem; an empty fold is cheap, a skipped fold loses
        # the beat the room just named.
        line = str(user_text or "").strip()
        try:
            began = time.monotonic()
            await _run_duet_scripter(
                db, ollama, session, line, cfg=cfg, fold=True,
            )
            _stage(session, "折り込み（二度目の compile）", began)
        except Exception:
            logger.warning("[muse] scripter fold failed", exc_info=True)
    finally:
        # The fold is the last writer of the turn, so this is where the
        # notebook has settled and she can be asked whether it is right.
        # **別に測る。** ここは早期 return したターンでも必ず走るので、
        # 「折り込み」の秒数に混ぜると、何が高いのか分からなくなる。
        began = time.monotonic()
        await _muse_checks_the_notebook(
            db, ollama, session, cfg=cfg, note=str(user_text or ""),
        )
        _stage(session, "彼女の見直し", began)
        _settle_repair_notice(session)


def _facets_to_write(session: dict[str, Any]) -> list[str]:
    """Which parts this prep turn rewrites.

    Everything unlocked on the opening — there is no shot yet. After that, only
    what the Showrunner's direction has actually touched since the last prep,
    which is what makes an untouched part untouched by construction rather than
    by the model remembering to leave it alone.
    """
    table = facets.table_of(session)
    partner = bool(str(_inputs(session).get("partner_preset") or "").strip())
    opening_set = facets.ALL_FACETS if partner else facets.FACETS
    unlocked = [n for n, _ in opening_set if not table[n].get("locked")]
    if not facets.table_rev(table):
        return unlocked
    routed = [n for n in (session.get("routed") or []) if n in unlocked]
    return routed


def _apply_facet_turn(
    session: dict[str, Any], written: dict[str, dict[str, Any]], *,
    say: str, muse_id: str, ms: int = 0,
    turns: tuple[dict[str, str], ...] | None = None,
) -> dict[str, Any]:
    """Write the parts this turn rewrote, and nothing else.

    The ledger still records a before/after over the whole tag list, so
    `report.py` and the panel are unaffected — from outside this looks like any
    other turn that changed the craft.
    """
    before = str((session.get("craft") or {}).get("tags") or "")
    blocked: list[str] = []
    for name, slot in written.items():
        report = facets.write(
            session, name,
            tags=slot.get("tags"), nl=slot.get("nl"),
            fields=slot.get("fields"), by=muse_id,
        )
        blocked.extend(n for n in report["blocked"] if n not in blocked)
    # Two parts of the shot disagreeing, where the one that would have yielded
    # is pinned. The pin wins and the panel gets to say so — a change that
    # silently did not take is the thing the Showrunner cannot debug.
    session["facet_conflicts"] = blocked
    _reassemble(session)
    record_ledger(
        session, muse_id=muse_id, name=_muse_display_name(session, muse_id),
        before=before, after=str(session["craft"].get("tags") or ""), ms=ms,
    )
    spoken = session.setdefault("spoken", [])
    if muse_id not in spoken:
        spoken.append(muse_id)
    # The direction has been carried out. Leaving it on the list is how a note
    # answered three turns ago went on being answered.
    session["routed"] = []
    _rebuild_brief(session)

    name = _muse_display_name(session, muse_id)
    msg = _chat_append(
        session, role="muse", text=say or f"（{name}が台本を更新した。）",
        muse_id=muse_id, name=name, kind="craft",
        turns=_resolve_duet_turns(session, turns),
    )
    _publish_chat(session["session_id"], msg)
    events.publish(session["session_id"], {
        "type": "craft_updated",
        "prompt": str(session["craft"].get("prompt") or ""),
        "muse_id": muse_id,
    })
    return msg


async def _duet_prep_facets(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """The prep turn, scoped to the parts the Showrunner actually changed."""
    inputs = _inputs(session)
    sid = session["session_id"]
    lead = crew.DEFAULT_MEMBER["actress"]
    names = _facets_to_write(session)
    if not names:
        # Nothing was asked for, so nothing is rewritten. Saying "she rebuilt
        # the shot" here is how an untouched part got touched.
        session["status"] = "chat"
        await session_db.save(db, session)
        return session

    session["status"] = "discussing"
    await session_db.save(db, session)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": lead,
        "name": _muse_display_name(session, lead),
    })
    images = await board_images(db, session)
    started = time.monotonic()
    opening = not facets.table_rev(facets.table_of(session))
    partner_character = await _partner_character(db, session)
    if partner_character:
        tier = await _duet_tier(db, session, partner_character)
        system = crew.w_actress_duet_prompt(
            session.get("character") or {}, partner_character, mode="prep",
            base_style=_style(session), seed=str(sid), tier=tier,
            facets=names, opening=opening,
        )
    else:
        system = crew.actress_duet_prompt(
            session.get("character") or {}, mode="prep",
            base_style=_style(session), seed=str(sid),
            facets=names, opening=opening,
        )

    try:
        say, written, blind = await chain.run_duet_facets(
            ollama,
            user_prompt=_facet_prep_prompt(session, names, partner_character=partner_character),
            system=system,
            allowed=names,
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            images=images or None,
            on_token=_token_publisher(sid, lead),
        )
    except chain.ChainError as exc:
        session["status"] = "chat"
        await session_db.save(db, session)
        raise MuseError(_msg(
            session,
            ja="台本がうまく組めませんでした。もう少し話してから試してください。",
            en="Couldn't put the shot together. Talk it through a bit more and try again.",
        )) from exc
    if blind and images:
        _note_blind(session)
    _apply_facet_turn(
        session, written, say=say, muse_id=lead,
        ms=int((time.monotonic() - started) * 1000),
    )
    session["status"] = "chat"
    await _after_actress_spoke(db, session)
    await session_db.save(db, session)
    return session


async def _duet_prep_notebook(
    db, ollama, session: dict[str, Any], *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """Legacy ①撮影準備. Weave only — no readout SAY."""
    notebook_mod.migrate(session)
    session["status"] = "chat"
    session = await weave_craft_if_needed(db, ollama, session)
    await session_db.save(db, session)
    return session


async def _duet_prep(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> dict[str, Any]:
    """Prep button: notebook densify path for duet; legacy paths otherwise."""
    if uses_notebook(session):
        return await _duet_prep_notebook(db, ollama, session, cfg=cfg)
    if on_facets(session):
        return await _duet_prep_facets(db, ollama, session, cfg=cfg)
    inputs = _inputs(session)
    sid = session["session_id"]
    lead = crew.DEFAULT_MEMBER["actress"]
    session["status"] = "discussing"
    await session_db.save(db, session)

    events.publish(sid, {
        "type": "muse_speaking", "muse_id": lead,
        "name": _muse_display_name(session, lead),
    })
    images = await board_images(db, session)
    started = time.monotonic()

    partner_character = await _partner_character(db, session)
    tier = await _duet_tier(db, session, partner_character)

    try:
        turn = await chain.run_duet_prep(
            ollama,
            user_prompt=_duet_user_prompt(session, text, prep=True),
            model=_vision_model(inputs) if images else _text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            identity_tags=_identity_tags(session),
            framing=_framing(inputs),
            brief=str(session.get("brief") or ""),
            character=session.get("character") or {},
            partner_character=partner_character,
            style=_style(session), cast=_cast(session),
            images=images or None, seed=str(sid),
            on_token=_token_publisher(sid, lead),
            tier=tier,
        )
    except chain.ChainError as exc:
        session["status"] = "chat"
        await session_db.save(db, session)
        raise MuseError(_msg(
            session,
            ja="台本がうまく組めませんでした。もう少し話してから試してください。",
            en="Couldn't put the shot together. Talk it through a bit more and try again.",
        )) from exc
    if turn.blind and images:
        _note_blind(session)
    _apply_turn(session, turn, ms=int((time.monotonic() - started) * 1000))
    session["status"] = "chat"
    await _after_actress_spoke(db, session)
    await session_db.save(db, session)
    return session


async def duet_prep_stage(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """①撮影準備 — densify / readout. Live craft already comes from chat."""
    cfg = await get_runtime_config(db)
    return await _duet_prep(db, ollama, session, "", cfg=cfg)


def _sync_costume_wearing(session: dict[str, Any], wearing: str) -> None:
    """Make the COSTUME card say what the notebook says. Both, or neither.

    The card and the notebook hold the same outfit in two shapes, and
    `sync_crew_notebook` reads the card back into `wearing` through
    `_costume_wearing_line`. Leaving a stale HERO or LAYERS behind after the
    wardrobe turn is therefore not a cosmetic mismatch — it is the coat she
    just took off, waiting to be seeded back in on the next plan turn.

    The prose fields that are ABOUT the clothes rather than a list of them
    (silhouette, colourway, fabric, condition) are left alone: they are texture
    for the brief, they name no garment, and nothing reads them as an outfit.
    """
    costume = dict(session.get("costume") or {})
    if not costume:
        # 主演撮り keeps no card — the notebook is the whole outfit there.
        return
    items = [t.strip() for t in str(wearing or "").split(",") if t.strip()]
    costume["garments"] = ", ".join(items)
    costume["tags"] = list(items)
    # LAYERS is the same clothes as prose; HERO is one of them by name.
    costume["layers"] = ", ".join(items)
    hero = str(costume.get("hero") or "").strip()
    if hero and not notebook_mod.garment_matches(", ".join(items), hero):
        costume["hero"] = items[0] if items else ""
    session["costume"] = costume


async def wardrobe_stage(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """「衣装部屋に行ってきて」— the lead restates the whole outfit, absolute.

    Every other route to `wearing` is a delta: the compile reads the line and
    works out what it does to the notebook. Measured on this studio's own model
    that lands about four times in five on a one-clause change, less on a long
    one, and a miss is invisible — the outfit simply stays where it was. This
    is the button that does not need the delta to land. She goes and changes,
    comes back, and says the whole outfit over from the conversation.

    In a two-Muse take this is the LEAD's wardrobe only. `wearing_b` belongs to
    the partner and is not this button's to rewrite.
    """
    notebook_mod.migrate(session)
    sid = session["session_id"]
    inputs = _inputs(session)
    locale = str(inputs.get("locale") or "ja")
    cfg = await get_runtime_config(db)
    lead = crew.DEFAULT_MEMBER["actress"]
    nb = notebook_mod.of(session)
    before_shot = notebook_mod.shot_snapshot(nb)
    prev_wearing = str(nb.get("wearing") or "")
    partner_character = await _partner_character(db, session)
    name_a, name_b = _muse_names(session, partner_character)

    session["status"] = "discussing"
    await session_db.save(db, session)
    events.publish(sid, {
        "type": "muse_speaking", "muse_id": lead,
        "name": _muse_display_name(session, lead),
    })
    system = crew.actress_duet_prompt(
        session.get("character") or {}, mode="wardrobe",
        base_style=_style(session), seed=str(sid), locale=locale,
    )
    try:
        say, wearing = await chain.run_wardrobe(
            ollama, system=system,
            notebook_wearing=prev_wearing,
            transcript=_duet_transcript(session),
            struck=_struck_line(session),
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            on_token=_token_publisher(sid, lead),
        )
    except chain.ChainError as exc:
        session["status"] = "chat"
        await session_db.save(db, session)
        raise MuseError(_msg(
            session,
            ja="衣装部屋から戻ってこられませんでした。もう一度押してください。",
            en="She could not get back from the wardrobe. Press it again.",
        )) from exc

    session["status"] = "chat"
    tidy = brief_mod.tidy_wearing(wearing)
    if tidy:
        notebook_mod.apply_patch(nb, {"wearing": tidy})
        session["notebook"] = nb
        # This button is the way out of a wardrobe that has gone wrong, so it
        # banishes nothing and un-banishes what she says she has on. A session
        # was measured where `blouse` and `white` had been struck by a rephrase
        # nobody asked for: she could name a white blouse here all day and the
        # weave would drop it back out again. Pressing 衣装部屋 has to be able
        # to fix that, or there is no way to fix it at all.
        # Freed generously, on any word part. This is deliberately NOT the rule
        # that blocks (`tag_mentions_struck`, which only matches a head noun):
        # blocking wide is destructive, freeing wide is recoverable. Striking a
        # "stylish white blouse" leaves `white` and `stylish` struck, and no
        # narrow rule would ever lift those — which is the stuck state this
        # button exists to undo.
        worn_items = [
            w.strip() for w in re.split(r"[,，、]", str(nb.get("wearing") or ""))
            if w.strip()
        ]
        freed = [
            s for s in (session.get("struck") or [])
            if any(
                notebook_mod.garment_lifts_struck(w, str(s))
                for w in worn_items
            )
        ]
        if freed:
            logger.info("[muse] wardrobe un-struck %s", ", ".join(map(str, freed)))
            session["struck"] = [
                s for s in (session.get("struck") or []) if s not in freed
            ]
        session["struck"] = notebook_mod.live_struck(session)[-40:]
        _sync_costume_wearing(session, str(nb.get("wearing") or ""))
        session["digest"] = notebook_mod.summary_for_muse(
            nb, name_a=name_a, name_b=name_b,
        )
        session["craft_dirty"] = True
        _note_rewrite(
            session, "wardrobe", before=before_shot,
            after=notebook_mod.shot_snapshot(nb), intent="shot",
        )
        events.publish(sid, {
            "type": "scripter_working", "status": "updating", "flash": "wearing",
            "message": _scripter_status_message(locale=locale, soft=False),
        })
    else:
        # She came back with nothing wearable. Saying so is the whole contract
        # of this room — the alternative is a button that looks like it worked.
        _chat_append(
            session, role="system", name="Studio", kind="system",
            text=(
                "衣装が聞き取れませんでした。もう一度押してみてください。"
                if locale.startswith("ja") else
                "Could not make out the outfit. Try the button again."
            ),
        )

    name = _muse_display_name(session, lead)
    msg = _chat_append(
        session, role="muse", muse_id=lead, name=name, kind="craft",
        text=say or f"（{name}が衣装部屋から戻ってきた。）",
    )
    _publish_chat(sid, msg)
    await _after_actress_spoke(db, session)
    await session_db.save(db, session)
    return session


async def post_duet_chat(
    db, ollama, session: dict[str, Any], text: str,
    images: list[bytes] | None = None,
) -> dict[str, Any]:
    """One turn: Script classifies INTENT, then she talks.

    Tags are woven later, just before a take. ``images`` is accepted and
    ignored — the VRM direction still is switched off in this version.
    """
    sid = session["session_id"]
    user_msg = _chat_append(session, role="user", text=text, name="総監督")
    _publish_chat(sid, user_msg)
    session["commit_pitch"] = _is_commit_pitch(text)
    await session_db.save(db, session)

    cfg = await get_runtime_config(db)

    # Before anything is written down. A line the contract does not allow never
    # reaches the scripter, so the notebook cannot pick it up on the way past —
    # she declines and the picture stays exactly where it was.
    began = time.monotonic()
    await _contract_check(ollama, session, text, cfg=cfg)
    _stage(session, "前段", began)

    # Joke / manager STOP: talk may continue, but nothing that steers the
    # picture — not the scripter, and not standing `notes` either. Falling
    # through to `take_note` on skip used to park the blocked line as standing
    # direction, so the next turn painted what this turn had joked away.
    began = time.monotonic()
    skip_picture = bool(session.pop("skip_scripter", False))
    if skip_picture:
        _strike_blocked_turn(session, user_msg, why="主演")
    if not skip_picture:
        if uses_notebook(session):
            try:
                await _run_duet_scripter(db, ollama, session, text, cfg=cfg)
            except Exception:
                logger.warning("[muse] scripter failed; muse still talks", exc_info=True)
                session["craft_dirty"] = True
                session.setdefault("scripter_intent", "casual")
            _stage(session, "台本 compile", began)
        else:
            named, _ = await route_note(db, ollama, session, text, cfg=cfg)
            if not named:
                await take_note(db, ollama, session, text, cfg=cfg)
            else:
                _note_standing(session, text)
                session["just_banned"] = []
                session["just_restored"] = []
            _stage(session, "note", began)

    began = time.monotonic()
    session = await _duet_talk(db, ollama, session, text, cfg=cfg)
    _stage(session, "彼女 talk", began)

    began = time.monotonic()
    await session_db.save(db, session)
    _stage(session, "保存", began)
    return session


async def start_duet(db, ollama, session: dict[str, Any]) -> dict[str, Any]:
    """Open the two-hander. She speaks first, about the theme, and that is all."""
    missing = [m for m in missing_inputs(session) if m != "workflow"]
    if missing:
        raise MuseError(_msg(
            session,
            ja=f"入力が不足しています: {', '.join(missing)}",
            en=f"missing: {', '.join(missing)}",
        ))
    await ensure_character(db, session)
    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    session["mode"] = "duet"
    session["status"] = "discussing"
    session["chat"] = []
    session["craft"] = {"prompt": "", "pose_intent": "", "tags": "", "scene": "",
                        "tags_a": "", "tags_b": ""}
    session["ledger"] = []
    session["banned"] = []
    session["carried_out"] = []
    session["spoken"] = []
    session["board"] = {}
    session["shoot"] = {}
    session["shoots"] = []
    # A fresh read-through is a fresh picture, so it gets a fresh seed. Held
    # from the first render onward — see `session_seed`.
    session["seed"] = 0
    session["plan"] = {}
    session["costume"] = {}
    session["notes"] = []
    session["notebook"] = notebook_mod.blank(
        partner=bool(str(_inputs(session).get("partner_preset") or "").strip())
    )
    session["craft_dirty"] = False
    session["struck"] = []
    session["muse_card"] = ""
    session["commit_pitch"] = False
    session["cited_memories"] = []
    session["prior_session_log"] = ""
    session["talk_turn_count"] = 0
    session["shot_compile_count"] = 0
    session["prop_age"] = {"fp": "", "turns": 0}
    session.pop("_blind_said", None)
    await _load_actress_memory(db, session)
    # Reunion warmth when she already has a bond card.
    bond = session.get("bond") or {}
    session["reunion_turn"] = bool(
        str(bond.get("last") or "").strip() or str(bond.get("inside") or "").strip()
        or (session.get("memories") or [])
    )
    # Opening is conversation — greet, maybe a diary whisper — not a shot briefing.
    session["scripter_intent"] = "casual"
    await session_db.save(db, session)
    session_db.log(session, "duet", "opened")
    return await _duet_talk(db, ollama, session, "", cfg=cfg)


async def _recent_memories_for(
    db, character_id: str, *, locale: str = "ja", limit: int = 3,
) -> list[str]:
    """Sticky shoot recaps for one character (picture facts, not diary prose)."""
    if not character_id:
        return []
    out: list[str] = []
    try:
        for recap in await presets_db.get_shoot_recaps(db, character_id, limit=limit):
            text = memories_db.format_recap_text(recap)
            if text:
                out.append(text)
    except Exception:
        logger.debug("[muse] shoot_recaps load failed", exc_info=True)
    return out[:limit]


async def _recent_diary_bodies(
    db, character_id: str, *, locale: str = "ja", limit: int = 2,
    brief: bool = False,
) -> list[str]:
    """Secret-diary prose for conversation recall — Muse prompt only.

    `brief` returns the page's *title*, not a summary of it. The distinction
    is the point. 総監督:「要約は諸刃の剣。結構消えてしまうので。」— a summary of a
    690-character page into 45 characters throws most of it away and then
    reads as if it were the whole thing. A title throws nothing away because
    it never claimed to carry the page: it is an index entry. She knows she
    wrote about コミケで撮影しよう, and on the turn he asks, the page itself
    comes back whole through CITED_MEMORIES (`_attach_recall_context`).

    The bodies ran 620-690 characters each and used to ride in every single
    turn's prompt whether or not anyone asked.
    """
    if not character_id:
        return []
    try:
        entries = await presets_db.get_recent_diary_summaries(
            db, character_id, limit=limit,
        )
    except Exception:
        logger.debug("[muse] diary bodies load failed", exc_info=True)
        return []
    ja = str(locale).startswith("ja")
    out: list[str] = []
    for e in entries:
        summary = str(
            (e.get("summary_ja") if ja else e.get("summary_en"))
            or e.get("summary") or ""
        ).strip()
        if brief:
            theme = str(e.get("theme") or "").strip()
            if theme:
                out.append(theme[:120])
            elif summary:
                out.append(summary[:120])
            continue
        text = str(
            (e.get("content_ja") if ja else e.get("content_en"))
            or e.get("content") or summary
        ).strip()
        if text:
            out.append(text[:900])
    return out[:limit]


async def _recent_memories(db, session: dict[str, Any], limit: int = 3) -> list[str]:
    """Sticky shoot recaps — Muse prompt only."""
    inputs = _inputs(session)
    return await _recent_memories_for(
        db, str(inputs.get("character_id") or ""),
        locale=str(inputs.get("locale") or "ja"), limit=limit,
    )


def _recap_from_snapshot(session: dict[str, Any]) -> dict[str, Any]:
    snap = session.get("continuity_snapshot") or {}
    nb = snap.get("notebook") or {}
    theme = str(snap.get("theme") or _inputs(session).get("theme") or "").strip()
    when = str(nb.get("atmosphere") or nb.get("scene") or theme or "").strip()[:160]
    feel = str(nb.get("vibe") or "").strip()[:200]
    shot = " / ".join(
        p for p in (
            str(nb.get("wearing") or "").strip(),
            str(nb.get("beat") or "").strip(),
            str(nb.get("frame") or "").strip(),
        ) if p
    )[:280]
    liked = str(nb.get("open") or "").strip()[:160]
    return {
        "when": when or theme or "撮影",
        "feel": feel,
        "liked": liked,
        "shot": shot or str(snap.get("craft_tags") or "")[:200],
        "session_id": str(session.get("session_id") or ""),
        "timestamp": time.time(),
    }


async def record_shoot_continuity(db, session: dict[str, Any], ollama=None) -> None:
    """After a successful ③ take: sticky recap + embed overflow into muse_memories.

    Everything she keeps from a shoot is written here, and twice now a live run
    has ended with `continuity: None` — nothing kept, no error surfaced. The
    guards below each have a reason and each returns silently, so the first job
    is being able to tell which one fired.
    """
    sid = str(session.get("session_id") or "")
    if not uses_notebook(session) and not session.get("continuity_snapshot"):
        # Still record a light recap for duet even if snapshot missing.
        if not is_duet(session):
            logger.info("[muse] continuity skipped (%s): no notebook, no snapshot", sid[:8])
            return
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        logger.info("[muse] continuity skipped (%s): nobody cast", sid[:8])
        return
    if (session.get("continuity") or {}).get("written_at"):
        logger.info("[muse] continuity skipped (%s): already written", sid[:8])
        return
    logger.info("[muse] continuity starting (%s)", sid[:8])
    recap = _recap_from_snapshot(session)
    try:
        overflow = await presets_db.push_shoot_recap(db, char_id, recap)
    except Exception:
        logger.warning("[muse] sticky recap failed", exc_info=True)
        overflow = None
    if overflow is not None:
        if ollama is not None:
            try:
                await memories_db.upsert_summary(
                    db, ollama, character_id=char_id, recap=overflow,
                    session_id=str(overflow.get("session_id") or ""),
                )
            except Exception:
                logger.warning("[muse] embed overflow recap failed", exc_info=True)
                session.setdefault("pending_memory_embeds", []).append(overflow)
        else:
            # Shoot job may not carry ollama — flush later from finish_session.
            session.setdefault("pending_memory_embeds", []).append(overflow)
    # Short Muse-only continuity cards — not scripter inputs.
    written: dict[str, Any] = {}
    try:
        written["bond"] = await presets_db.update_bond(
            db, char_id, _bond_from_snapshot(session),
        )
        # Only overwrite what she learned when this shoot actually taught her
        # something. A silent shoot must not wipe the card she was carrying.
        taste = await _learned_taste(ollama, session, cfg=await get_runtime_config(db))
        if any(str(v or "").strip() for v in taste.values()):
            written["showrunner_taste"] = await presets_db.update_showrunner_taste(
                db, char_id, taste,
            )
    except Exception:
        logger.warning("[muse] bond/taste write failed", exc_info=True)
    written["continuity"] = {"written_at": time.time()}
    session.update(written)
    # This runs in the render job, after `finish_shoot` has already published
    # `status: done` — so the showrunner is free to type the moment the take
    # lands, and their turn loads, edits and saves the session while this is
    # still working. Saving the copy loaded before their line would erase it;
    # saving after it, as this used to, threw away everything they just said —
    # or, measured on a real run, lost this write instead and left her carrying
    # the last session's clothes as what she had learned.
    #
    # Merge under the session's own lock: re-read, lay only these keys on top,
    # write back. Whatever else the turn changed stays changed.
    async with _finish_locks[str(session.get("session_id") or "")]:
        fresh = await session_db.load(db, str(session.get("session_id") or ""))
        if fresh is None:
            await session_db.save(db, session, publish=False)
            return
        fresh.update(written)
        for key in ("memories", "pending_memory_embeds"):
            if session.get(key) is not None:
                fresh[key] = session[key]
        await session_db.save(db, fresh, publish=False)


async def flush_pending_memory_embeds(db, ollama, session: dict[str, Any]) -> None:
    """Embed overflow recaps queued when the shoot job had no ollama handle."""
    pending = list(session.get("pending_memory_embeds") or [])
    if not pending or ollama is None:
        return
    char_id = str(_inputs(session).get("character_id") or "")
    kept: list[dict[str, Any]] = []
    for recap in pending:
        try:
            mid = await memories_db.upsert_summary(
                db, ollama, character_id=char_id, recap=recap,
                session_id=str(recap.get("session_id") or ""),
            )
            if not mid:
                kept.append(recap)
        except Exception:
            kept.append(recap)
    session["pending_memory_embeds"] = kept
    await session_db.save(db, session, publish=False)


async def _load_actress_memory(db, session: dict[str, Any]) -> None:
    """Read sticky recaps / diary once per session — Muse only, never scripter.

    Once, at the open, rather than on every turn: it is a Qdrant round trip and
    neither answer can change mid-session. Both the two-hander and the
    eighteen-seat table call this; the table only ever fed the crew the brief,
    so she used to walk into it having forgotten every shoot she had written
    about.
    """
    session["memories"] = await _recent_memories(db, session)
    session["diary_memories"] = []
    session["partner_memories"] = []
    session["prior_session_log"] = ""
    session["bond"] = {}
    session["showrunner_taste"] = {}
    session["chemistry_notes"] = []
    # W-Muse: short partner sticky/diary for talk parity (never scripter).
    try:
        partner = await _partner_character(db, session)
        if partner:
            pid = str(
                partner.get("character_id")
                or _inputs(session).get("partner_preset") or ""
            )
            session["partner_memories"] = await _recent_memories_for(
                db, pid,
                locale=str(_inputs(session).get("locale") or "ja"),
                limit=2,
            )
    except Exception:
        logger.debug("[muse] partner memories load failed", exc_info=True)
    session["caught"] = {}
    await _load_circle(db, session)
    await _load_social_seeds(db, session)
    await _load_handpost_notices(db, session)
    await _load_pitch_recommend(db, session)
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    try:
        session["diary_memories"] = await _recent_diary_bodies(
            db, char_id,
            locale=str(_inputs(session).get("locale") or "ja"),
            limit=2, brief=True,
        )
    except Exception:
        logger.debug("[muse] diary memories load failed", exc_info=True)
    try:
        session["bond"] = await presets_db.get_bond(db, char_id)
        session["showrunner_taste"] = await presets_db.get_showrunner_taste(
            db, char_id,
        )
        session["chemistry_notes"] = await presets_db.get_recent_chemistry_notes(
            db, char_id, limit=2,
        )
    except Exception:
        logger.debug("[muse] bond/taste/chemistry load failed", exc_info=True)
    try:
        caught = await presets_db.get_unacknowledged_read_diaries(db, char_id)
    except Exception:
        logger.debug("[muse] could not read diary acknowledgements", exc_info=True)
        return
    if not caught:
        return
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    newest = caught[0]
    session["caught"] = {
        "ids": [str(d.get("id") or "") for d in caught if d.get("id")],
        "summary": str(
            (newest.get("summary_ja") if ja else newest.get("summary_en"))
            or newest.get("summary") or ""
        ).strip(),
    }


# 常駐する量の上限。今日 2,468字 → 1,373字 に削ったばかりで、ここはすぐ
# 膨らむ。**要約ではなく指し先**にする（`lounge.outing_summary_line`）。
CIRCLE_MAX_LINES = 2
CIRCLE_MAX_CHARS = 150


_GENDER_JA = {"female": "女性", "male": "男性"}


async def _circle_who(db, names_by_id: dict[str, str]) -> str:
    """一緒に出かけた相手が誰なのか ―― 名前と、性別。

    名前だけ渡すと、モデルは苗字に「くん」を付ける。実測で、日記に
    **「柳くん」** と書かれた ―― 柳 かほは女優で、女性。名前からは分からない
    ことを、こちらが渡していなかった。

    総監督:「日記を見たら『柳くん』となってました。性別渡さないといけないね」

    preset に載っている値をそのまま使う。ここで決め打ちしない。
    """
    out: list[str] = []
    for cid, name in names_by_id.items():
        g = ""
        try:
            preset = await presets_db.get_preset(db, cid)
            g = _GENDER_JA.get(str((preset or {}).get("gender") or ""), "")
        except Exception:
            logger.debug("[muse] could not read a friend's sheet", exc_info=True)
        out.append(f"{name}（{g}）" if g else name)
    return "・".join(out)


async def _circle_lines(db, char_id: str) -> tuple[list[str], list[str], str]:
    """このひとが最近誰と出かけたか ―― 短い2行と、相手の名前。

    **character_id で引く。** 会話は主演の分で足りるが、日記は一人ずつ書く
    （W撮りなら二人分）ので、`session["circle"]` を使い回すと相手の日記に
    主演のお出かけが載る。
    """
    if not char_id:
        return [], [], ""
    try:
        rows = await lounge_db.list_threads(db, limit=20, kind="outing")
    except Exception:
        logger.debug("[muse] could not read the outing feed", exc_info=True)
        return [], [], ""
    lines: list[str] = []
    names: dict[str, str] = {}          # character_id -> 表示名
    used = 0
    for row in rows:
        cast_ids = {
            str(c.get("character_id") or "")
            for c in (row.get("cast") or []) if isinstance(c, dict)
        }
        if char_id not in cast_ids:
            continue
        line = lounge_mod.outing_summary_line(row)
        if not line or used + len(line) > CIRCLE_MAX_CHARS:
            continue
        lines.append(line)
        used += len(line)
        for c in (row.get("cast") or []):
            if not isinstance(c, dict):
                continue
            cid = str(c.get("character_id") or "")
            nm = str(c.get("name_ja") or "").strip()
            if cid and nm and cid != char_id:
                names[cid] = nm
        if len(lines) >= CIRCLE_MAX_LINES:
            break
    return lines, sorted(names.values()), await _circle_who(db, names)


async def _load_circle(db, session: dict[str, Any]) -> None:
    """Who she has been out with lately — two short lines, kept all session.

    Unlike `social_seeds`, these are not spent after her first turn. A friend
    she saw last Sunday did not stop existing because she has already spoken
    once; the seeds are a tip to try in one shot, this is just who is around.
    """
    lines, names, who = await _circle_lines(
        db, str(_inputs(session).get("character_id") or ""),
    )
    session["circle"] = lines
    session["circle_names"] = names
    session["circle_who"] = who
    session["circle_mentions"] = 0


async def _load_social_seeds(db, session: dict[str, Any]) -> None:
    """Lounge whispers for this open. Uses are spent after the first actress turn."""
    session["social_seeds"] = []
    session["social_seed_ids"] = []
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    try:
        seeds = await presets_db.get_social_seeds(db, char_id)
    except Exception:
        logger.debug("[muse] could not load social seeds", exc_info=True)
        return
    lines: list[str] = []
    ids: list[str] = []
    for seed in seeds:
        text = str(
            (seed.get("summary_ja") if ja else seed.get("summary_en"))
            or seed.get("summary_ja") or seed.get("summary_en") or ""
        ).strip()
        if not text:
            continue
        stance = str(seed.get("stance") or "try")
        if stance == "twist":
            text = f"{text}（自分なりにアレンジしてもいい）"
        elif stance == "skip":
            text = f"{text}（無理ならパスしてよい）"
        lines.append(text)
        if seed.get("id"):
            ids.append(str(seed["id"]))
    session["social_seeds"] = lines
    session["social_seed_ids"] = ids


async def _consume_social_seeds(db, session: dict[str, Any]) -> None:
    """Spend the whispers that coloured this session — once, after she speaks."""
    ids = [str(i) for i in (session.get("social_seed_ids") or []) if i]
    if not ids:
        return
    session["social_seed_ids"] = []
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    try:
        await presets_db.consume_social_seeds(db, char_id, ids)
    except Exception:
        logger.debug("[muse] could not consume social seeds", exc_info=True)


async def _load_handpost_notices(db, session: dict[str, Any]) -> None:
    session["handpost_notices"] = []
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    try:
        session["handpost_notices"] = await handpost_db.pinned_notice_lines(db, ja=ja, limit=3)
    except Exception:
        logger.debug("[muse] could not load handpost notices", exc_info=True)


async def _load_pitch_recommend(db, session: dict[str, Any]) -> None:
    """One liked pitch, once — chat line + prompt block, spent after she speaks."""
    session["pitch_recommend"] = {}
    char_id = str(_inputs(session).get("character_id") or "")
    if not char_id:
        return
    try:
        pitch = await lounge_db.next_liked_pitch(db, char_id)
    except Exception:
        logger.debug("[muse] could not load liked pitch", exc_info=True)
        return
    if not pitch:
        return
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    text = str(
        (pitch.get("text_ja") if ja else pitch.get("text_en"))
        or pitch.get("text_ja") or pitch.get("text_en") or ""
    ).strip()
    if not text:
        return
    session["pitch_recommend"] = {
        "thread_id": str(pitch.get("id") or ""),
        "text": text,
    }
    line = (
        f"前にいいねした提案: {text}\nこれ撮ってほしい、かも？"
        if ja else
        f"A pitch you liked: {text}\nMaybe shoot this?"
    )
    _chat_append(session, role="system", name="Studio", text=line)


async def _consume_pitch_recommend(db, session: dict[str, Any]) -> None:
    rec = session.get("pitch_recommend") or {}
    tid = str(rec.get("thread_id") or "")
    session["pitch_recommend"] = {}
    if not tid:
        return
    try:
        await lounge_db.mark_recommended(db, tid)
    except Exception:
        logger.debug("[muse] could not mark pitch recommended", exc_info=True)


# ── showrunner message ──────────────────────────────────────────────────────
async def post_chat(
    db, ollama, comfy, spooler, session: dict[str, Any], text: str,
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Showrunner speaks — always creative direction. Board and shoot are their
    own buttons (`request_board` / `approve_and_shoot`), not words in here."""
    text = (text or "").strip()
    if not text:
        raise MuseError(_msg(session, ja="メッセージが空です。", en="empty message"))
    if missing_inputs(session):
        raise MuseError(_msg(
            session,
            ja=f"入力が不足しています: {', '.join(missing_inputs(session))}",
            en=f"missing: {', '.join(missing_inputs(session))}",
        ))
    direction = decode_chat_images(images, max_n=1)
    if is_duet(session):
        return await post_duet_chat(
            db, ollama, session, text, images=direction or None,
        )
    if not (session.get("craft") or {}).get("prompt"):
        # Auto-open table if they chat first.
        session = await start_table(
            db, ollama, session, comfy=comfy, spooler=spooler,
        )

    sid = session["session_id"]
    user_msg = _chat_append(session, role="user", text=text, name="総監督")
    _publish_chat(sid, user_msg)
    await session_db.save(db, session)

    # Same gate as 主演撮り, and it belongs here rather than further down: this
    # room reaches `take_note` by two routes, and the "brief" one below is the
    # earlier of them. A note is standing direction — a line that got that far
    # would keep steering the picture on every turn after this one.
    await _contract_check(
        ollama, session, text, cfg=await get_runtime_config(db),
    )

    # `unsure` ―― 会話には通すが、**常設の指示にも画にもしない。**
    # 口では流したのに `beat` が書き換わるのが、いちばん悪い形。
    skip_picture = bool(session.pop("skip_scripter", False))
    if skip_picture:
        _strike_blocked_turn(session, user_msg, why="班")

    # The still is up and only three seats have spoken: this is the note the
    # rest of the crew has been waiting for. Whatever it says, the full table
    # meets once, first — otherwise a note lands after a prompt three seats
    # already wrote, unanswered.
    if str(session.get("table_stage") or "full") == "brief":
        cfg = await get_runtime_config(db)
        # Same rule as the full path: blocked lines must not become standing
        # notes, plan seeds, craft, or folded beat — only the room may talk.
        if not skip_picture:
            await take_note(db, ollama, session, text, cfg=cfg)
            await session_db.save(db, session)
            if _cast_in_role(_crew_ids(session), "plan"):
                await _run_plan_turn(db, ollama, session, cfg=cfg, note=text)
                await session_db.save(db, session, publish=False)
            # The note becomes the shot before the floor discusses it — see the
            # ordering note in the note path below.
            await _run_crew_scripter(db, ollama, session, text, cfg=cfg)
        session = await run_full_table(db, ollama, session, note=text)
        if not skip_picture:
            await _fold_muse_after_talk(db, ollama, session, cfg=cfg, user_text=text)
        locale = str(_inputs(session).get("locale") or "ja")
        wrap = _chat_append(
            session, role="system", name="Studio",
            text=(
                "全班そろいました。「②試し撮り」でイメージボード、「③本番」で撮影に"
                "入れます。まだ詰めるならコメントをどうぞ。"
                if locale.startswith("ja") else
                "Full crew is in. Use \"test shot\" for a screening, \"final\" to "
                "shoot, or keep the notes coming."
            ),
        )
        _publish_chat(sid, wrap)
        session["status"] = "chat"
        await session_db.save(db, session)
        return session

    # Crew answers the hard note — pick specialists by keyword, else core desk.
    cast = _crew_ids(session)
    # Anyone brought in since the read-through has never seen the script. They
    # go first, and they get the note too — a seat cast halfway through is
    # usually cast *because* of the note.
    fresh = newcomers(session, cast)
    beat_index = int(session.get("crew_talk_index") or 0)
    responders = [
        m for m in fresh + [
            r for r in _pick_responders(text, cast, beat_index) if r not in fresh
        ]
        # The Lead has her own turn now; a seat listed twice speaks twice.
        if crew.role_of(m) != "actress"
    ]
    session["status"] = "discussing"
    cfg = await get_runtime_config(db)
    # The note is standing direction from here on, not a remark about one turn —
    # and whatever it refuses comes out of the picture before anyone answers it.
    # Joke / STOP: skip standing note, plan reseat, scripter, and fold — talk only.
    if not skip_picture:
        await take_note(db, ollama, session, text, cfg=cfg)
        await session_db.save(db, session)

        # Re-settle where and when first: a note like "make it somewhere else" has
        # to move the locked place, or the original theme keeps winning downstream.
        if _cast_in_role(cast, "plan"):
            await _run_plan_turn(db, ollama, session, cfg=cfg, note=text)
            await session_db.save(db, session, publish=False)

        # 主演撮りの order, which is the one that follows direction: the
        # showrunner's line becomes the shot FIRST (compile, plus a VERIFY pass
        # when the picture did not appear to move), then the room talks about the
        # shot it now has, then a fold pass adds the uncontradicted body action she
        # brought. Talking first and compiling afterwards meant one compile read
        # the note and three seats' banter as one transcript, and the banter — five
        # times the word count — is what the picture came out of.
        await _run_crew_scripter(db, ollama, session, text, cfg=cfg)

    # Once a board exists the crew answers while looking at it.
    images = await board_images(db, session)
    screening = _screening_note(session) if images else ""

    # She answers first, in her own voice — then the floor answers her.
    lead_say = await _run_crew_lead_turn(db, ollama, session, text, cfg=cfg)
    await session_db.save(db, session, publish=False)

    # Packed table talk: similar jobs share one LLM turn (SAY only). Scripter
    # owns TAGS afterward — no more 5× craft rewrites that wait a minute each.
    await _run_crew_table_talk(
        ollama, session, responders,
        note=text, screening=screening, cfg=cfg, images=images,
        lead_say=lead_say,
    )
    await session_db.save(db, session, publish=False)

    # Fold: her CARD/SAY body action onto the stem the note already set.
    if not skip_picture:
        await _fold_muse_after_talk(db, ollama, session, cfg=cfg, user_text=text)

    # No per-note "Applied. Use ②試し撮り…" any more. It fired on every single
    # note — 21 times in one measured run — and the buttons it points at are on
    # screen the whole time. The room is a conversation; a floor manager
    # repeating the same sentence after every line is what made it read like a
    # form. The panel already shows whether the script is caught up.
    session["status"] = "chat"
    await session_db.save(db, session)
    return session


# Notes use one packed table-talk call — catch-up stays tiny.
MAX_CATCHUP = 2

# Similar jobs share one voice slot. One LLM call; Scripter compiles craft.
#
# The Lead is NOT in here. She used to share a slot with beat/spine, and on the
# standard preset the beat seat is listed first — so the character the whole
# session is about said nothing on note after note, and when she did speak it
# was through the moderator prompt: no voice block, no memory, no ASIDE, no
# CARD, and `_after_actress_spoke` never fired. She gets her own turn
# (`_run_crew_lead_turn`), the same one 主演撮り uses.
#
# Four voices, still ONE call: the pack costs a call, not a speaker, so the
# floor is grouped by what the seats argue about rather than by what fits.
# Within a group the turn rotates (`_pack_speakers`) — one fixed pick per group
# meant the gaffer, the palette and the prop shop never spoke on a note, and
# with CREW LOOK that also means their slot never gets an owner's line.
_TALK_GROUPS: tuple[tuple[str, ...], ...] = (
    ("wardrobe",),
    ("beat", "spine", "cutout"),
    ("lens", "gaffer"),
    ("propshop", "palette", "weather", "faces"),
)


def newcomers(session: dict[str, Any], crew_ids: list[str]) -> list[str]:
    """Cast members who have not spoken yet — at most one catch-up voice."""
    already = set(session.get("spoken") or [])
    return [m for m in _writing_seats(crew_ids) if m not in already][:MAX_CATCHUP]


def _pack_speakers(crew_ids: list[str], index: int = 0) -> list[str]:
    """One voice per job-family from the given cast slice.

    ``index`` rotates who holds each family's mouth this beat, so a floor of
    sixteen is not three people talking and thirteen watching.
    """
    ordered: list[str] = []
    for group in _TALK_GROUPS:
        seated = [m for r in group if (m := _cast_in_role(crew_ids, r))]
        if not seated:
            continue
        pick = seated[int(index) % len(seated)]
        if pick not in ordered:
            ordered.append(pick)
    if ordered:
        return ordered
    return [crew_ids[0]] if crew_ids else []


def _pick_responders(note: str, crew_ids: list[str], index: int = 0) -> list[str]:
    """One voice per job-family for the packed table-talk turn.

    Do NOT branch on mood keywords. Scripter owns TAGS; seats only talk.
    Finisher / grade stay off the note path (densify / quality fluff).
    """
    _ = note
    return _pack_speakers(crew_ids, index)


_SPEAKER_BLOCK_RE = re.compile(
    r"(?im)^SPEAKER\s*:\s*([^\n]+?)\s*$\s*^SAY\s*:\s*(.+?)(?=^SPEAKER\s*:|\Z)",
    re.S,
)


_CRAFT_LINE_RE = re.compile(r"(?im)^CRAFT\s*:\s*(.+?)\s*$")


def _split_craft_line(body: str) -> tuple[str, str]:
    """Pull the optional owned-craft clause out of one speaker's block."""
    match = _CRAFT_LINE_RE.search(str(body or ""))
    if not match:
        return str(body or "").strip(), ""
    clause = str(match.group(1) or "").strip()
    say = _CRAFT_LINE_RE.sub("", str(body or "")).strip()
    if clause.lower() in ("none", "-", "n/a", "omit", "(omit)"):
        clause = ""
    return say, clause[:280]


def _parse_table_talk(raw: str, speakers: list[str]) -> list[tuple[str, str, str]]:
    """Parse packed SPEAKER/SAY/CRAFT blocks; fall back to first speaker."""
    hits: list[tuple[str, str, str]] = []
    for mid_raw, say in _SPEAKER_BLOCK_RE.findall(str(raw or "")):
        token = str(mid_raw or "").strip()
        text_s = str(say or "").strip()
        if not text_s:
            continue
        match = next(
            (s for s in speakers if s == token or s.endswith(token) or token in s),
            None,
        )
        if match is None:
            low = token.lower()
            match = next(
                (s for s in speakers if low in s.lower()),
                speakers[len(hits)] if len(hits) < len(speakers) else None,
            )
        if match:
            body, craft_line = _split_craft_line(text_s)
            clean = identity.sanitize_muse_say(body)
            if re.search(r"(?im)^(TAGS|SCENE)\s*:", clean):
                clean = re.split(r"(?im)^(TAGS|SCENE)\s*:", clean)[0].strip()
            hits.append((match, clean[:600], craft_line))
    if hits:
        return hits
    blob = str(raw or "").strip()
    if blob and speakers:
        say = blob
        if re.search(r"(?i)SAY\s*:", blob):
            say = re.split(r"(?i)SAY\s*:", blob, maxsplit=1)[-1].strip()
        say, craft_line = _split_craft_line(say)
        return [(speakers[0], identity.sanitize_muse_say(say)[:600], craft_line)]
    return []


def crew_look(session: dict[str, Any]) -> dict[str, Any]:
    return session.setdefault("crew_look", {})


def _split_visual_script(clause: str) -> tuple[str, str]:
    """`backlighting, rim_light | low sun from behind` → (tags, note).

    The crew's shared language: the left half is what the camera is set to and
    goes to the sampler verbatim, the right half is what they mean by it and
    goes into the prose. Written as one clause with no `|`, it is all note —
    which is what the first live run produced, and why the weave had to invent
    tags for it and minted `silhouette_breathing_room`.
    """
    text = str(clause or "").strip()
    if "|" not in text:
        return "", text
    left, _, right = text.partition("|")
    tags = ", ".join(
        t for t in (
            identity.bare_tag(p) for p in left.split(",")
        ) if t
    )
    return tags[:120], right.strip()[:160]


def _record_crew_look(
    session: dict[str, Any], muse_id: str, clause: str, *, ms: int = 0,
) -> None:
    """One seat rewrites the one element it owns — and the ledger sees it.

    Seats stopped writing TAGS when the crewed studio went notebook-primary,
    which also stopped `record_ledger`: the 破壊行列 in `GET /api/muse/report`
    went blank for this room, and that report is how "which seat is worth its
    wall clock" gets answered. A slot rewrite is the seat's contribution now,
    so it is what gets recorded.
    """
    slot = crew.craft_slot(muse_id)
    clause = str(clause or "").strip()
    if not slot or not clause:
        return
    struck = notebook_mod.struck_tokens(session)
    low = clause.lower().replace(" ", "_")
    if any(tok and tok in low for tok in struck):
        return
    tags, note = _split_visual_script(clause)
    look = crew_look(session)
    prev = look.get(slot)
    before = str((prev or {}).get("tags") or "") if isinstance(prev, dict) else str(prev or "")
    entry = {"tags": tags, "note": note}
    if isinstance(prev, dict) and prev.get("tags") == tags and prev.get("note") == note:
        return
    if not isinstance(prev, dict) and str(prev or "").strip() == clause:
        return
    look[slot] = entry
    # The ledger sees the tags, not the sentence. It is the destruction matrix
    # (`GET /api/muse/report`) — "which seat dropped which word" only means
    # something when the entries are words the picture is made of, and a whole
    # clause stored as one tag made it unreadable.
    record_ledger(
        session, muse_id=muse_id, name=_muse_display_name(session, muse_id),
        before=before, after=tags or note, ms=ms,
    )


def crew_look_block(session: dict[str, Any]) -> str:
    """The owned craft notes, for the scripter's compile and weave.

    `LIGHT: backlighting, rim_light — low sun from behind, hard rim on the jaw`
    — the tags are the seat's own, already in the sampler's vocabulary, and go
    through as written; the words after the dash are what the prose is built
    from. Older sessions stored one prose clause and no tags; those still read.
    """
    if is_duet(session):
        return ""
    rows: list[str] = []
    for slot, value in (session.get("crew_look") or {}).items():
        if isinstance(value, dict):
            tags = str(value.get("tags") or "").strip()
            note = str(value.get("note") or "").strip()
        else:
            tags, note = "", str(value or "").strip()
        line = " — ".join(p for p in (tags, note) if p)
        if line:
            rows.append(f"{slot}: {line}")
    return "\n".join(rows)


def _room_leaning(session: dict[str, Any]) -> str:
    """What this cast tends to like, for the weave to lean on.

    `style_direction` has always gathered the room's flavour tags — busy
    background vs negative space, rim light vs ambient, cel vs painterly — and
    nothing has ever read them: `base_style_for` takes only `base`, and the
    seats stopped writing tags when the crewed studio went notebook-primary.
    They were computed on every roster call and shown in the panel and never
    reached a prompt. This is the one place they belong: a leaning the weave
    may follow, under the notebook and under the Showrunner.
    """
    if is_duet(session):
        return ""
    tags = crew.style_direction(_crew_ids(session)).get("flavor_tags") or []
    return ", ".join(str(t) for t in tags[:10])


def crew_look_tags(session: dict[str, Any]) -> list[str]:
    """Every tag the seats wrote for their own element, in slot order."""
    out: list[str] = []
    for value in (session.get("crew_look") or {}).values():
        if not isinstance(value, dict):
            continue
        for part in str(value.get("tags") or "").split(","):
            tag = identity.bare_tag(part)
            if tag and tag not in out:
                out.append(tag)
    return out


async def _run_crew_lead_turn(
    db, ollama, session: dict[str, Any], text: str, *, cfg: dict[str, Any],
) -> str:
    """The Lead answers in her own voice, before the floor reacts to her.

    This is `_duet_talk` — the same call 主演撮り makes — so she arrives with
    her voice block, her diary and memories, a rotating stance, ASIDE (独り言)
    and a CARD the scripter can fold. Packing her in with beat/spine is what
    made her a job title instead of the person the shoot is about.
    """
    if ollama is None or not _cast_in_role(_crew_ids(session), "actress"):
        return ""
    before = len(session.get("chat") or [])
    try:
        await _duet_talk(
            db, ollama, session, text, cfg=cfg, pitch=False, fold=False,
        )
    except MuseError:
        logger.warning("[muse] crew lead turn failed; crew still talks", exc_info=True)
        return ""
    for msg in reversed((session.get("chat") or [])[before:]):
        if msg.get("role") == "muse" and msg.get("kind") != "banter":
            return str(msg.get("text") or "")
    return ""


async def _run_crew_table_talk(
    ollama, session: dict[str, Any], speakers: list[str], *,
    note: str, screening: str, cfg: dict[str, Any],
    images: list[bytes] | None = None, lead_say: str = "",
) -> list[dict[str, Any]]:
    """One LLM call: similar jobs speak in one packed turn (SAY only)."""
    if not speakers or ollama is None:
        return []
    session["crew_talk_index"] = int(session.get("crew_talk_index") or 0) + 1
    inputs = _inputs(session)
    locale = str(inputs.get("locale") or "ja")
    sid = session["session_id"]
    lead_id = _cast_in_role(_crew_ids(session), "actress")
    lead_name = _muse_display_name(session, lead_id) if lead_id else ""
    roster_lines: list[str] = []
    for mid in speakers:
        name = _muse_display_name(session, mid)
        role = crew.role_of(mid)
        trait = crew.trait_blurb(mid, locale=locale)
        roster_lines.append(
            f"- SPEAKER id `{mid}` — {name} ({role})"
            + (f" · {trait}" if trait else "")
        )
    system = crew.table_talk_system_prompt(
        speakers, character=session.get("character") or {},
        base_style=_style(session), locale=locale,
        preset_id=str(inputs.get("crew_preset") or ""),
        seed=str(sid), lead_name=lead_name,
    )
    parts = [
        "CAST (speak in this order):\n" + "\n".join(roster_lines),
        f"BRIEF:\n{str(session.get('brief_lite') or session.get('brief') or '')[:1800]}",
        f"NOTEBOOK:\n{notebook_mod.render(notebook_mod.of(session))[:1200]}",
    ]
    if screening:
        parts.append(f"SCREENING:\n{screening}")
    parts.append(f"SHOWRUNNER NOTE:\n{note.strip()}")
    # She spoke first this turn. Handing the floor her actual words is what
    # makes this a table rather than three people talking past her.
    if lead_say.strip():
        parts.append(
            f"{lead_name or 'THE LEAD'} JUST SAID (answer her — agree, tease, or "
            f"push back; do not repeat her words):\n{lead_say.strip()[:600]}"
        )
    parts.append("Each speaker reacts in voice. No TAGS. No SCENE. Scripter will compile.")
    user = "\n\n".join(parts)
    events.publish(sid, {
        "type": "muse_speaking",
        "muse_id": speakers[0],
        "name": _muse_display_name(session, speakers[0]),
    })
    try:
        raw = await chain.run_table_talk(
            ollama, system=system, user_prompt=user,
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            images=images,
        )
    except Exception:
        logger.warning("[muse] packed table talk failed", exc_info=True)
        return []
    messages: list[dict[str, Any]] = []
    for mid, say, craft_line in _parse_table_talk(raw, speakers):
        spoken = session.setdefault("spoken", [])
        if mid not in spoken:
            spoken.append(mid)
        _record_crew_look(session, mid, craft_line)
        name = _muse_display_name(session, mid)
        msg = _chat_append(
            session, role="muse", text=say, muse_id=mid, name=name, kind="banter",
        )
        _publish_chat(sid, msg)
        messages.append(msg)
    return messages


# ── image board ─────────────────────────────────────────────────────────────
async def _maybe_unload(ollama, session: dict[str, Any]) -> None:
    inputs = _inputs(session)
    if ollama is None or not bool(inputs.get("unload_vlm")):
        return
    model = str(inputs.get("model") or "") or None
    try:
        await ollama.unload(model)
    except Exception:
        logger.debug("[muse] unload_vlm failed", exc_info=True)


def _densify_user_prompt(session: dict[str, Any], *, screening: str = "") -> str:
    """Force Finisher to thicken a thin craft before Comfy sees it."""
    craft = session.get("craft") or {}
    closer = crew.DEFAULT_MEMBER["finisher"]
    base = _table_user_prompt(session, muse_id=closer, screening=screening)
    must = [str(m) for m in ((session.get("plan") or {}).get("must_appear") or [])]
    ledger = (
        "- Every one of these must be in SCENE: " + ", ".join(must) + "\n"
        if must else
        "- ≥10 place objects implied by THIS theme.\n"
    )
    return (
        f"{base}\n\n"
        "DENSITY PACK (mandatory — SCENE was thin):\n"
        "- Expand SCENE to 140–200 English words covering pose, cloth, place objects,\n"
        "  light/atmosphere, camera, personality in eyes/hands.\n"
        f"{ledger}"
        "- TAGS: 35–55 strong tags. Do not shrink.\n"
        "- Keep the same moment and theme. Densify, do not restart or relocate.\n"
        "- Do not inject props/outfits from a different situation.\n"
        "- Do not change the light level while densifying.\n"
        f"- Current SCENE word count: {identity.word_count(str(craft.get('scene') or ''))}.\n"
        f"- Current positive word count: {identity.word_count(str(craft.get('prompt') or ''))}."
    )


async def compose_scene_if_needed(
    db, ollama, session: dict[str, Any],
) -> dict[str, Any]:
    """Render the facet table into prose, once per version of the shot.

    This is the step that makes the parts read as one picture. It is a pure
    function of the table: the prompt is the table and the standing rules and
    nothing else — no chat, no theme, no brief, no previous prompt. Composing
    was never what went wrong; being handed twenty turns of contradicting
    history was, and a composer with no history cannot be confused by one.

    Skipped when the table has not moved since the last composition, so an
    unchanged shot costs nothing. Falls back to the joined facet sentences
    whenever the composition cannot be trusted — the shot still renders.
    """
    if ollama is None or not on_facets(session):
        return session
    table = facets.table_of(session)
    rev = facets.table_rev(table)
    if not rev:
        return session
    composed = session.get("composed") or {}
    if int(composed.get("rev") or -1) == rev and str(composed.get("scene") or ""):
        return session

    cfg = await get_runtime_config(db)
    inputs = _inputs(session)
    partner_character = await _partner_character(db, session)
    name_a = ""
    name_b = ""
    if partner_character:
        char_a = session.get("character") or {}
        name_a = str(char_a.get("name_ja") or char_a.get("name") or "")
        name_b = str(partner_character.get("name_ja") or partner_character.get("name") or "")
    try:
        scene = await chain.run_compose(
            ollama,
            table_block=facets.table_block(table),
            standing=facets.standing_block(list(session.get("standing") or [])),
            model=_text_model(inputs), num_ctx=_num_ctx(inputs, cfg),
            name_a=name_a, name_b=name_b,
        )
    except chain.ChainError:
        logger.warning("[muse] compose failed; rendering the joined parts",
                       exc_info=True)
        return session

    usable, _ = facets.warn_invented_nouns(
        table, scene, banned=banned_tags(session),
        extra=[_style(session), str((session.get("character") or {}).get("name") or "")],
    )
    if scene and usable:
        session["composed"] = {"scene": scene, "rev": rev, "at": time.time()}
        _reassemble(session)
    await session_db.save(db, session, publish=False)
    return session


def _warn_if_craft_behind(session: dict[str, Any]) -> bool:
    """Record that we are about to render a script that did not catch up.

    Both render buttons used to set ``craft_dirty = False`` unconditionally
    right after densify, whether or not densify had succeeded. A failed compile
    was swallowed: the flag went clean, the warning disappeared, and the shot
    went out on the previous prompt with nobody told. The flag is still left
    alone and the miss is still recorded — but in the instrument panel, not in
    the room. See `_settle_repair_notice` for why it left the chat.
    """
    if not bool(session.get("craft_dirty")):
        return False
    nb = notebook_mod.of(session)
    _note_rewrite(
        session, "craft_behind",
        before=notebook_mod.shot_snapshot(nb),
        after=notebook_mod.shot_snapshot(nb),
        extra={"craft": {
            "before": "notebook rev " + str(nb.get("rev") or 0),
            "after": "compiled rev " + str(session.get("notebook_rev_compiled") or 0),
        }},
    )
    return True


def _scrub_notebook_craft(session: dict[str, Any]) -> None:
    """Drop struck / banned / leftover clothes / opposite crop from the craft bag."""
    nb = notebook_mod.of(session)
    craft = session.setdefault("craft", {})
    tags = notebook_mod.scrub_craft_tags(
        str(craft.get("tags") or ""),
        wearing=str(nb.get("wearing") or ""),
        scene=str(nb.get("scene") or ""),
        beat=str(nb.get("beat") or ""),
        struck=notebook_mod.struck_tokens(session),
        wearing_b=str(nb.get("wearing_b") or ""),
        beat_b=str(nb.get("beat_b") or ""),
        frame=str(nb.get("frame") or ""),
        banned=set(banned_tags(session)),
    )
    if tags != str(craft.get("tags") or ""):
        craft["tags"] = tags
        _reassemble(session)


async def weave_craft_if_needed(
    db, ollama, session: dict[str, Any],
) -> dict[str, Any]:
    """One Script weave from the notebook. No theme, no chat, no still.

    Both rooms. 制作スタッフ was left on the Finisher densify path when it
    became notebook-primary, and that path cannot land a rewrite: when its
    compile does not apply it falls back to a Finisher seat turn, and seat
    turns are talk-only in the crewed studio, so the tags were dropped on the
    floor. Measured live, `craft.tags` were byte-identical from the opening
    still to the final shoot while the notebook ran from rev 2 to rev 13 —
    six showrunner notes, none of which reached the picture.
    """
    if ollama is None or not uses_notebook(session):
        return session
    notebook_mod.migrate(session)
    nb = notebook_mod.of(session)
    if not notebook_mod.has_shot(nb):
        return session
    craft = session.get("craft") or {}
    dirty = bool(session.get("craft_dirty"))
    behind = int(nb.get("rev") or 0) > int(session.get("notebook_rev_compiled") or 0)
    prompt = str(craft.get("prompt") or "")
    scene = str(craft.get("scene") or "")
    if not dirty and not behind and prompt and not identity.craft_is_thin(prompt, scene):
        _scrub_notebook_craft(session)
        return session

    cfg = await get_runtime_config(db)
    sid = session.get("session_id") or ""
    locale = str(_inputs(session).get("locale") or "ja")
    if sid:
        events.publish(sid, {
            "type": "scripter_working",
            "status": "weave",
            "message": (
                "空気、厚くしてる…" if locale.startswith("ja")
                else "Thickening the air…"
            ),
        })
    partner_character = await _partner_character(db, session)
    partner = bool(partner_character) or bool(
        str(_inputs(session).get("partner_preset") or "").strip()
    )
    name_a, name_b = _muse_names(session, partner_character)
    weave_ok = False
    try:
        result = await _call_duet_scripter(
            ollama, session, note="WEAVE", cfg=cfg,
            partner=partner, name_a=name_a, name_b=name_b, mode="weave",
        )
        tags = notebook_mod.scrub_craft_tags(
            str(result.get("tags") or ""),
            wearing=str(nb.get("wearing") or ""),
            scene=str(nb.get("scene") or ""),
            beat=str(nb.get("beat") or ""),
            struck=notebook_mod.struck_tokens(session),
            wearing_b=str(nb.get("wearing_b") or ""),
            beat_b=str(nb.get("beat_b") or ""),
            frame=str(nb.get("frame") or ""),
            banned=set(banned_tags(session)),
        )
        scene_out = str(result.get("craft_scene") or "")
        if result.get("valid") and tags and scene_out:
            tags = await _muse_reviews_weave(
                ollama, session, tags, cfg=cfg, name_a=name_a, name_b=name_b,
                partner=partner,
            )
            if _apply_compiled_craft(
                session, tags, scene_out, sides=_weave_sides(result),
            ):
                session["craft_dirty"] = False
                weave_ok = True
        if not weave_ok:
            session["craft_dirty"] = True
            _scrub_notebook_craft(session)
    except Exception:
        logger.warning("[muse] notebook weave failed; keeping draft", exc_info=True)
        session["craft_dirty"] = True
        weave_ok = False
        _scrub_notebook_craft(session)
    if sid:
        events.publish(sid, {
            "type": "scripter_done",
            "intent": "shot",
            "compiled": bool(weave_ok),
            "valid": bool(weave_ok),
            "dirty": bool(session.get("craft_dirty")),
        })
    await session_db.save(db, session, publish=False)
    return session


async def _muse_reviews_weave(
    ollama, session: dict[str, Any], tags: str, *, cfg: dict[str, Any],
    name_a: str, name_b: str, partner: bool,
) -> str:
    """Let her look at the bag before the render, and drop what she disowns.

    Subtractive only, and closed to the tags already present. Skipped when the
    bag is already dense and shares nothing with struck — a second LLM every
    weave was tax without a miss to catch.
    """
    bag = str(tags or "").strip()
    if not bag:
        session["weave_review"] = []
        return tags
    struck = notebook_mod.struck_tokens(session)
    have = {identity.bare_tag(p) for p in bag.split(",") if p.strip()}
    thin = identity.craft_is_thin(bag, "")
    overlaps_struck = bool(have & struck) if struck else False
    if not thin and not overlaps_struck:
        session["weave_review"] = []
        return tags
    inputs = _inputs(session)
    nb = notebook_mod.of(session)
    try:
        wrong = await chain.run_weave_review(
            ollama,
            system=crew.actress_duet_prompt(
                session.get("character") or {}, mode="review",
                locale=str(inputs.get("locale") or "ja"),
                seed=str(session.get("session_id") or ""),
            ),
            tags=tags,
            notebook_block=notebook_mod.render(
                nb, name_a=name_a, name_b=name_b or ("Partner" if partner else ""),
            ),
            muse_says=_last_lead_say(session),
            model=_text_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
        )
    except Exception:
        logger.warning("[muse] weave review failed; bag kept", exc_info=True)
        return tags
    # Written every time, including empty. Left only on the turns she spoke,
    # this reads as her having disowned something on a take where she said the
    # bag was fine — the panel and the debug pane would both be quoting a
    # review that is two takes old.
    session["weave_review"] = list(wrong)
    if not wrong:
        return tags
    # A review that wants to gut the bag has misread it, not found ten faults.
    if len(wrong) > _WEAVE_REVIEW_MAX:
        logger.info("[muse] weave review named %d tags; ignoring", len(wrong))
        return tags
    gone = {identity.bare_tag(t) for t in wrong}
    kept = ", ".join(
        p.strip() for p in tags.split(",")
        if p.strip() and identity.bare_tag(p) not in gone
    )
    logger.info("[muse] she disowned %s", ", ".join(wrong))
    return kept or tags


async def still_read_after_board(db, ollama, session_id: str) -> None:
    """Align notebook to the latest still. Struck items stay off.

    **繋がっていない（2026-08-26）。** `runner.run_board_job` から外した ——
    理由はそちらのコメントに書いてある（VRAM 読み込み 30秒超／本番の織り直しを
    誘発／掃除済みの frame を汚し直す）。**残してあるのは戻せるようにするため**で、
    呼ぶ側を復活させれば動く。
    """
    if ollama is None:
        return
    session = await session_db.load(db, session_id)
    if session is None or not is_duet(session) or not uses_notebook(session):
        return
    images = await board_images(db, session)
    if not images:
        return
    rnd = int((session.get("board") or {}).get("round") or 0)
    if int(session.get("still_read_round") or 0) == rnd:
        return
    cfg = await get_runtime_config(db)
    inputs = _inputs(session)
    partner_character = await _partner_character(db, session)
    partner = bool(partner_character) or bool(
        str(inputs.get("partner_preset") or "").strip()
    )
    name_a, name_b = _muse_names(session, partner_character)
    nb = notebook_mod.of(session)
    try:
        result = await chain.run_still_read(
            ollama,
            notebook_block=notebook_mod.render(
                nb, name_a=name_a,
                name_b=name_b or ("Partner" if partner else ""),
            ),
            struck=_struck_line(session),
            partner=partner,
            model=_vision_model(inputs),
            num_ctx=_num_ctx(inputs, cfg),
            images=images,
        )
    except Exception:
        logger.warning("[muse] still-read failed", exc_info=True)
        return
    patch = notebook_mod.guard_partner_patch(
        dict(result.get("patch") or {}), partner=partner,
    )
    prev_wearing = str(nb.get("wearing") or "")
    before_shot = notebook_mod.shot_snapshot(nb)
    notebook_mod.apply_patch(nb, patch)
    # Photo must not restore struck garments.
    struck = notebook_mod.struck_tokens(session)
    if struck:
        wearing = str(nb.get("wearing") or "")
        kept = [
            w for w in re.split(r"[,，、]", wearing)
            if w.strip() and not (
                notebook_mod.wearing_tokens(w) & struck
            )
        ]
        if ",".join(x.strip() for x in kept) != wearing:
            notebook_mod.apply_patch(nb, {"wearing": ", ".join(x.strip() for x in kept)})
    # Nothing is banished here. Reading a photograph is not the showrunner
    # taking a garment off her, and this path used to strike whatever the
    # still-read happened to word differently — the same rephrase-becomes-a-ban
    # defect as the compile above, one step further from anyone's intent.
    _ = prev_wearing
    session["notebook"] = nb
    session["still_read_round"] = rnd
    session["digest"] = notebook_mod.summary_for_muse(
        nb, name_a=name_a, name_b=name_b,
    )
    _note_rewrite(
        session, "still_read",
        before=before_shot, after=notebook_mod.shot_snapshot(nb), intent="shot",
    )
    await session_db.save(db, session, publish=False)


async def densify_craft_if_needed(
    db, ollama, session: dict[str, Any],
) -> dict[str, Any]:
    """Thicken craft before render.

    Notebook sessions (both rooms) weave — Finisher densify is legacy
    seat-written craft only. The old notebook densify scripter body under this
    function was unreachable after the weave redirect and is gone on purpose.
    """
    if ollama is None:
        return session
    if uses_notebook(session):
        return await weave_craft_if_needed(db, ollama, session)
    if on_facets(session):
        return await compose_scene_if_needed(db, ollama, session)
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    scene = str(craft.get("scene") or "")
    if not prompt:
        return session
    dirty = bool(session.get("craft_dirty"))
    behind = int(notebook_mod.of(session).get("rev") or 0) > int(
        session.get("notebook_rev_compiled") or 0
    )
    if not dirty and not behind and not identity.craft_is_thin(prompt, scene):
        return session
    cfg = await get_runtime_config(db)

    locale = str(_inputs(session).get("locale") or "ja")
    note = _chat_append(
        session, role="system", name="Studio",
        text=(
            "台本が薄いのでフィニッシャーが密度を上げます（のっぺり防止）。"
            if locale.startswith("ja") else
            "Craft is thin — Finisher is densifying before render."
        ),
    )
    _publish_chat(session["session_id"], note)
    images = await board_images(db, session)
    try:
        turn, ms = await _run_muse_turn(
            ollama, session, "finisher",
            _densify_user_prompt(
                session, screening=_screening_note(session) if images else "",
            ),
            cfg=cfg, images=images,
        )
        # Refuse a wipe: densify must not replace a real compile with empty tags.
        if str(turn.tags or "").strip() or str(turn.scene or "").strip():
            _apply_turn(session, turn, ms=ms)
        if turn.blind and images:
            _note_blind(session)
    except chain.ChainError:
        logger.warning("[muse] densify failed; rendering thin craft", exc_info=True)
    await session_db.save(db, session, publish=False)
    return session


async def request_board(
    db, comfy, spooler, session: dict[str, Any], ollama=None, *, still: bool = False,
) -> dict[str, Any]:
    """One render for the crew and the Showrunner to look at.

    `still` is the opening frame, shot off three seats before the rest of the
    crew has said anything: one image rather than four, because at that point
    there is not enough craft for four to differ, and the whole point is to get
    something on the wall fast.
    """
    craft = session.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    if uses_notebook(session):
        began = time.monotonic()
        session = await weave_craft_if_needed(db, ollama, session)
        _stage(session, "weave", began)
        _warn_if_craft_behind(session)
        craft = session.get("craft") or {}
        prompt = str(craft.get("prompt") or "")
    elif not prompt:
        raise MuseError(_msg(
            session,
            ja="まだ台本がありません。服や場所など画の指示を会話で出してください。",
            en="No script yet — describe the shot in chat (clothes, place, camera).",
        ))

    if not still and not uses_notebook(session):
        session = await densify_craft_if_needed(db, ollama, session)
        _warn_if_craft_behind(session)
        craft = session.get("craft") or {}
        prompt = str(craft.get("prompt") or "")

    if not prompt:
        raise MuseError(_msg(
            session,
            ja="まだ台本がありません。服や場所など画の指示を会話で出してください。",
            en="No script yet — describe the shot in chat (clothes, place, camera).",
        ))

    inputs = _inputs(session)
    sid = session["session_id"]
    # 同じ台本でもう一度押したら、引き直す。シードを撮影の間ずっと保持する
    # のは「二つのテイクの差が言葉だけになる」ためで、**台本が動いていない
    # ときにまで同じ絵を返す理由は無い**。総監督が実撮影で踏んだ:
    # プロンプトが変わらないと再撮影ができない。
    #
    # 判定は台本の一致だけ。画が気に入らなくて押し直したのか、言い直した
    # 結果を見たいのかは、台本が動いたかどうかで分かる。
    prev = session.get("board") or {}
    if prev.get("images") and str(prev.get("prompt") or "") == prompt:
        logger.info("[muse] same script, same seed — rerolling")
        session["seed"] = 0
    seed = session_seed(session)
    locale = str(inputs.get("locale") or "ja")

    await _maybe_unload(ollama, session)

    if not is_duet(session):
        if still:
            ask_text = (
                "当たりを一枚撮ります。少し待ってください。"
                if locale.startswith("ja") else
                "Taking one still. One moment."
            )
        else:
            ask_text = (
                "総監督、イメージボード上げます。これでいい？良ければ「③本番」、"
                "ダメなら指摘ください。"
                if locale.startswith("ja") else
                "Showrunner — image board going up. Good? Press \"final\" to shoot, "
                "or note what to fix."
            )
        ask = _chat_append(
            session, role="muse", muse_id="lens",
            name=_muse_display_name(session, "lens"), text=ask_text,
        )
        _publish_chat(sid, ask)
    else:
        # 主演撮り has no camera seat to say this, so for a long time it said
        # nothing at all — measured on a real session: eight test shots, eight
        # timeline entries, and not one line in the chat. Reading that log back
        # you see 「承認を受け付けました」 four times with no sign a board was
        # ever asked for, and the two-to-four minute silences (the showrunner
        # waiting on a render, then looking at it) are unexplained. The final
        # shoot has always written its own line here; the test shot must too,
        # or the log is not a record of the shoot.
        ask = _chat_append(
            session, role="system", name="Studio", kind="system",
            text=(
                ("当たりを一枚撮ります。少し待ってください。"
                 if still else "試し撮りを1枚撮ります。")
                if locale.startswith("ja") else
                ("Taking one still. One moment."
                 if still else "Taking a test shot.")
            ),
        )
        _publish_chat(sid, ask)

    session["board"] = {
        "prompt": prompt,
        "seed": seed,
        "job_id": "",
        "images": [],
        "pending": True,
        "still": bool(still),
        "round": int((session.get("board") or {}).get("round") or 0) + 1,
        # **どの版の手帖を撮ったか。** 本番でこれと突き合わせる —— ボードの
        # あとに会話で指示が入っていたら、その指示で撮らないといけない。
        "rev": int(notebook_mod.of(session).get("rev") or 0),
    }
    session["status"] = "boarding"
    await session_db.save(db, session)

    session["board"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_board",
        runner.run_board_job,
        db=db, comfy=comfy, session_id=sid, ollama=ollama,
    )
    session_db.log(session, "board", f"round {session['board']['round']}")
    await session_db.save(db, session)
    return session


def _archive_take(session: dict[str, Any]) -> bool:
    """焼き上がった一枚を履歴へ移す。**もう入っていれば何もしない。**

    一度の撮影で ③ は何度も押される（実測で四回）。`shoot` は「いま作っている
    一枚」で毎回上書きされるので、押すたびに前の一枚をここへ積む。

    ただしそれだけだと、**セッションの最後の一枚は次が無いので永遠に `shoot`
    に取り残される。** 実測（2026-08-24・4枚撮った回）で `shoots` が3件しか
    なかった。日記は `shoots + [shoot]` と両方見ていたので気づかなかった ——
    **日記だけが正しく、記録の側が欠けていた。** 撮影を終える時にも呼ぶ。
    """
    done = session.get("shoot") or {}
    images = list(done.get("images") or [])
    if not images:
        return False
    takes = list(session.get("shoots") or [])
    if takes and _image_ids_of(takes[-1]) == _image_ids_of(done):
        return False                      # 二度積まない
    takes.append({
        "prompt": str(done.get("prompt") or ""),
        "seed": done.get("seed"),
        "images": images,
        "at": time.time(),
    })
    session["shoots"] = takes[-_SHOOT_ARCHIVE_MAX:]
    return True


def _approved_prompt(session: dict[str, Any]) -> str:
    """What the board they just said yes to was drawn with.

    **The picture the showrunner approved is the picture we take.** This used
    to re-weave first, and the weave read a notebook that had moved since the
    board — `still_read_after_board` rewrote it from the photograph on the way
    past, so the notebook was one revision ahead every single time. Measured on
    a live session: `notebook rev 45 / compiled 44 / board round 9`.

    The seed was already carried over from the board for exactly this reason
    (see below). Carrying the seed and not the words is the worst of both: the
    same roll of the dice against a different set of instructions.

    A board that never rendered, or a session that goes straight to the final
    without one, falls through to the craft as before.
    """
    board = session.get("board") or {}
    if board.get("pending") or not board.get("images"):
        return ""
    # **ボードのあとに手帖が動いていたら、流さない。** ここは無条件にボードの
    # 指示を返していて、`session["board"]` は撮影開始と明示取消でしか消えな
    # かった —— 試し撮り → 会話で新しい指示 → ③本番、で**古いボードの指示で
    # 撮っていた。** 総監督の求めは条件付き（「試し撮り後に会話がなければ
    # そのまま流す」）。動いていれば従来どおり織り直す。
    #
    # `rev` を持たない古いセッションは、突き合わせようがないので従来どおり。
    rev = board.get("rev")
    if rev is not None and int(rev) != int(notebook_mod.of(session).get("rev") or 0):
        logger.info("[muse] notebook moved since the board (%s → %s); reweaving",
                    rev, notebook_mod.of(session).get("rev"))
        return ""
    return str(board.get("prompt") or "")


async def approve_and_shoot(
    db, comfy, spooler, session: dict[str, Any], ollama=None,
) -> dict[str, Any]:
    prompt = _approved_prompt(session)
    if not prompt:
        session = await densify_craft_if_needed(db, ollama, session)
        _warn_if_craft_behind(session)
        prompt = str((session.get("craft") or {}).get("prompt") or "")
    if not prompt:
        raise MuseError(_msg(
            session,
            ja="まだ台本がありません。服や場所など画の指示を会話で出してください。",
            en="No script yet — describe the shot in chat (clothes, place, camera).",
        ))

    craft = session.get("craft") or {}
    inputs = _inputs(session)
    sid = session["session_id"]
    seed = int((session.get("board") or {}).get("seed") or 0) or session_seed(session)
    locale = str(inputs.get("locale") or "ja")

    await _maybe_unload(ollama, session)

    msg = _chat_append(
        session, role="system", name="Studio",
        text=(
            "承認を受け付けました。本番撮影に入ります。"
            if locale.startswith("ja") else
            "Approved. Going to final shoot."
        ),
    )
    _publish_chat(sid, msg)

    # Continuity snapshot at the moment they commit to a take — not after.
    nb = notebook_mod.of(session) if uses_notebook(session) else {}
    session["continuity_snapshot"] = {
        "at": time.time(),
        "theme": str(inputs.get("theme") or ""),
        "notebook": {
            k: nb.get(k) for k in (
                "atmosphere", "scene", "bg", "light", "frame", "wearing", "beat",
                "wearing_b", "beat_b", "vibe", "open",
            )
        } if nb else {},
        "craft_tags": str(craft.get("tags") or ""),
        "craft_scene": str(craft.get("scene") or ""),
    }

    _archive_take(session)

    session["shoot"] = {
        "prompt": prompt,
        "seed": seed,
        "job_id": "",
        "images": [],
        "pending": True,
    }
    session["status"] = "shooting"
    await session_db.save(db, session)

    session["shoot"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_shoot",
        runner.run_shoot_job,
        meta={"session_id": sid, "step": "shoot"},
        db=db, comfy=comfy, session_id=sid, ollama=ollama,
    )
    session_db.log(session, "shoot", f"seed {seed}")
    await session_db.save(db, session)
    return session


def _has_shot(session: dict[str, Any]) -> bool:
    """Did the final shoot actually produce something?

    Deliberately looser than `schema.shoot_images`: older sessions store bare
    sha strings here, and the diary job already reads both shapes.
    """
    return bool((session.get("shoot") or {}).get("images"))


async def finish_session(
    db, spooler, session: dict[str, Any], ollama=None, comfy=None
) -> dict[str, Any]:
    """Wrap up session, mark as finished, and queue background post-shoot secret diary job.

    Two guards, both of which used to be missing and both of which the
    Showrunner could trip from the panel: wrapping twice wrote two diaries for
    one shoot, and wrapping before the shoot asked her to write about a picture
    that does not exist.
    """
    sid = session["session_id"]
    # A caller's `session` can be a stale snapshot — two concurrent requests
    # (double-click, a second tab, a retry) each load their own copy before
    # either writes `queued_at`, and both would otherwise pass the guard
    # below. Re-read the authoritative state while holding the session's
    # lock so only one caller ever gets past it.
    async with _finish_locks[sid]:
        fresh = await session_db.load(db, sid)
        if fresh is not None:
            session = fresh
        if (session.get("diary") or {}).get("queued_at") or session.get("status") == "finished":
            return session
        if not _has_shot(session):
            raise MuseError(_msg(
                session,
                ja="本番撮影が終わってから終了してください。",
                en="Finish the final shoot before wrapping up.",
            ))

        session["status"] = "finished"
        # **最後の一枚を履歴に入れる。** `approve_and_shoot` は次の③のときに
        # 前の一枚を積むので、そのままだと最後の一枚が `shoot` に取り残される。
        if _archive_take(session):
            session_db.log(session, "shoot", "last take archived")
        session_db.log(session, "finish", "session wrapped up")
        # Soft coda — confirm bond card is kept (no extra LLM sampling).
        bond = session.get("bond") or {}
        last = str(bond.get("last") or "").strip()
        ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
        coda = (
            (
                f"今日の余韻、メモに残しておくね"
                + (f"（{last[:80]}）" if last else "。")
            )
            if ja else
            (
                "I'll keep today's distance in the bond card"
                + (f" ({last[:80]})." if last else ".")
            )
        )
        coda_msg = _chat_append(
            session, role="system", name="Studio", text=coda, kind="system",
        )
        _publish_chat(sid, coda_msg)
        # Flush overflow shoot-recap embeds queued without ollama on the render job.
        try:
            await flush_pending_memory_embeds(db, ollama, session)
        except Exception:
            logger.debug("[muse] pending memory flush failed", exc_info=True)

        char_id = str((session.get("inputs") or {}).get("character_id") or "")
        partner_id = ""
        if is_duet(session):
            partner_char = await _partner_character(db, session)
            partner_id = str((partner_char or {}).get("character_id") or "")
        seen: set[str] = set()
        char_ids = [
            cid for cid in (char_id, partner_id)
            if cid and not (cid in seen or seen.add(cid))
        ]

        if char_ids and spooler:
            cfg = await get_runtime_config(db)
            inputs = _inputs(session)
            session["diary"] = {
                "status": "writing",
                "queued_at": time.time(),
                "entries": {cid: {"status": "writing"} for cid in char_ids},
            }
            await session_db.save(db, session)
            events.publish(sid, {"type": "diary_status", "status": "writing"})
            model = _text_model(inputs) or str(cfg.get("vlm_model") or "")
            num_ctx = _num_ctx(inputs, cfg)
            for cid in char_ids:
                spooler.submit(
                    # Every Ollama call in the app goes through PROMPT, and that lane is
                    # the one bound to the GPU resource when Ollama is local. There is no
                    # UTILITY lane — naming one raised AttributeError inside the request,
                    # so the diary job was never queued at all.
                    JobLane.PROMPT,
                    "generate_actress_diary",
                    run_generate_actress_diary_job,
                    meta={"session_id": sid, "character_id": cid},
                    db=db,
                    ollama=ollama,
                    session=session,
                    character_id=cid,
                    model=model,
                    num_ctx=num_ctx,
                    # Passed through so the second diary to land in a duet can
                    # queue the chemistry job itself — see _record_diary_result.
                    spooler=spooler,
                )
                # Lounge share is friend-facing (not the secret diary). Same
                # PROMPT lane; reactions are queued from inside the share job.
                spooler.submit(
                    JobLane.PROMPT,
                    "generate_lounge_share",
                    run_generate_lounge_share_job,
                    meta={"session_id": sid, "character_id": cid},
                    db=db,
                    ollama=ollama,
                    session=session,
                    character_id=cid,
                    model=model,
                    num_ctx=num_ctx,
                    spooler=spooler,
                )
                # A day off with her friends, every few shoots. The job decides
                # for itself whether one is due (`_outing_is_due`) and skips
                # otherwise, so this stays one submit with no state out here.
                # It takes no session and no photo — see the job.
                spooler.submit(
                    JobLane.PROMPT,
                    "generate_outing",
                    run_generate_outing_job,
                    meta={"session_id": sid, "character_id": cid},
                    db=db,
                    ollama=ollama,
                    character_id=cid,
                    model=model,
                    num_ctx=num_ctx,
                    # 頼まれごとの回に一枚焼くので、**引き金になったこの撮影の
                    # ワークフローと画の設定**を持たせる（総監督の指定）。
                    spooler=spooler,
                    comfy=comfy,
                    workflow=str(_inputs(session).get("workflow") or ""),
                    shot={k: _inputs(session).get(k) for k in (
                        "width", "height", "final_steps", "final_cfg",
                        "negative_prompt",
                    )},
                )
            # Pitch / habit are independent of share succeeding — queue them
            # for the lead only so a failed wrap post does not silence ideas.
            lead_id = char_id
            if lead_id:
                lead_preset = await presets_db.get_preset(db, lead_id)
                lead_char = _character_for_id(session, lead_preset or {}, lead_id) if lead_preset else {}
                if lounge_mod.should_pitch(lead_char, lead_preset):
                    spooler.submit(
                        JobLane.PROMPT,
                        "generate_lounge_pitch",
                        run_generate_lounge_pitch_job,
                        meta={"session_id": sid, "character_id": lead_id},
                        db=db,
                        ollama=ollama,
                        session=session,
                        character_id=lead_id,
                        model=model,
                        num_ctx=num_ctx,
                    )
                if lounge_mod.should_write_habit(notes=list(session.get("notes") or [])):
                    spooler.submit(
                        JobLane.PROMPT,
                        "generate_handpost_habit",
                        run_generate_handpost_habit_job,
                        meta={"session_id": sid, "character_id": lead_id},
                        db=db,
                        ollama=ollama,
                        session=session,
                        character_id=lead_id,
                        model=model,
                        num_ctx=num_ctx,
                    )
        else:
            await session_db.save(db, session)
        return session


async def run_generate_actress_diary_job(
    reporter,
    cancel,
    *,
    db,
    ollama,
    session: dict[str, Any],
    character_id: str,
    model: str = "",
    num_ctx: int | None = None,
    spooler=None,
):
    """Background runner job that invokes LLM for dual-language (JA & EN) secret diary.

    Two positional arguments, like every other job: the spooler calls
    ``job._func(reporter, cancel_token, **kwargs)``.
    """
    sid = str(session.get("session_id") or "")
    _report(reporter, 0.05, "日記を書いてもらっています")
    preset = await presets_db.get_preset(db, character_id)
    if not preset:
        await _record_diary_result(
            db, sid, character_id=character_id, status="failed", error="character not found",
        )
        return {"status": "skipped", "reason": "character not found"}
    # Session may already hold the converted character; otherwise map the preset.
    # Raw presets keep `personality` as a trait list — never pass that straight
    # into actress_diary_prompt without normalizing. A duet has two of these
    # cached on the session (`character` for the lead, `partner_character` for
    # her partner) — matching only against the lead used to narrate the
    # partner's diary in the lead's voice.
    def _cached_match(candidate: dict[str, Any]) -> bool:
        return str(candidate.get("character_id") or "") == character_id and (
            isinstance(candidate.get("personality"), dict) or candidate.get("reasoning_ja")
        )

    session_char = session.get("character") or {}
    partner_char = session.get("partner_character") or {}
    if _cached_match(session_char):
        char = session_char
    elif _cached_match(partner_char):
        char = partner_char
    else:
        char = presets_db.preset_to_character(preset)

    # Extract session logs
    chat_list = _chat_rows(session)
    session_log_lines = []
    for m in chat_list[-30:]:
        session_log_lines.append(f"{m.get('name')}: {m.get('text')}")
    session_log = "\n".join(session_log_lines)

    # Every ③ of the session, not just the last one. `image_id` stays the take
    # they finished on — that is the cover of the page — while `image_ids` is
    # what makes each of the day's photos find its way back here
    # (`presets.find_preset_diary_by_image`).
    image_ids = await shoot_photos_of_session(db, session)
    latest = _shoot_image_ids(session)
    image_id = (latest or image_ids or [""])[0]
    # Her contract asks her to end the entry on 「完成した本番写真を見た感想」.
    # What she was handed for that was the shoot's tag list, so she was writing
    # her impression of a photograph she had not seen — and on a session where
    # the render never received the direction, she described an expression that
    # was in no part of the prompt. She had no way to know. Show her the photo.
    photo_desc = await _read_the_photo(
        db, ollama, session, image_id, model=model, num_ctx=num_ctx,
    )

    # The prompt carries her voice, the material and the output contract, so it
    # is the system side; the user turn only has to ask for the thing.
    # **この日記の本人**で引く。W撮りは二人分書くので、session の分を使い回すと
    # 相手の日記に主演のお出かけが載る。
    circle_lines, _, circle_who = await _circle_lines(db, character_id)
    system = crew.actress_diary_prompt(
        char, session_log=session_log, photo_desc=photo_desc,
        circle="\n".join(circle_lines), circle_who=circle_who,
    )
    _report(reporter, 0.2, "日記を書いてもらっています")

    fields: dict[str, str] = {}
    stray_seen = ""
    for attempt, ask in enumerate(_DIARY_ASKS):
        raise_if_cancelled = getattr(cancel, "raise_if_set", None)
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        if stray_seen:
            ask = _DIARY_ASK_STRAY.format(stray=stray_seen)
        try:
            raw_resp = await chain._call(
                ollama, system=system, prompt=ask,
                model=model, images=None, num_ctx=num_ctx, think=False,
            )
        except Exception as exc:
            logger.warning("[muse] diary generation failed: %s", exc)
            await _record_diary_result(
                db, sid, character_id=character_id, status="failed", error=str(exc),
            )
            return {"status": "failed", "reason": str(exc)}
        fields = diary_mod.normalize(
            diary_mod.parse_diary(raw_resp), fallback_ja="本番撮影の思い出",
        )
        # **別の文字体系が紛れていたら、書き直してもらう。** 実測（15本）で
        # 4本に出た。指示文でも欄ごとに言語を閉じたが、本人が「学習データ上
        # その概念に強い他言語のトークンが浮上する」と言うとおり、指示だけでは
        # 残る。**最後の一回なら、紛れたまま残す** —— 一字の混入より、日記が
        # 無いほうが損失が大きい。
        stray = diary_mod.stray_script(fields.get("content_ja") or "")
        last = attempt >= len(_DIARY_ASKS) - 1
        if fields.get("content_ja") and (not stray or last):
            if stray:
                logger.warning(
                    "[muse] a stray script stayed in her diary: %r", stray,
                )
            break
        if stray:
            stray_seen = stray
            logger.info("[muse] stray script %r (attempt %d), asking again",
                        stray, attempt + 1)
        else:
            # One retry, with the contract restated. The diary is a background
            # job on a model that is already resident, so trying twice is cheap
            # — and what the old code did instead was save the broken response
            # as her writing, which is how a JSON object ended up on the page.
            logger.info("[muse] diary output unusable (attempt %d), retrying",
                        attempt + 1)
        _report(reporter, 0.5, "書き直してもらっています")

    if not fields.get("content_ja"):
        # Nothing survived that is safe to show. A missing diary is recoverable;
        # scaffolding printed in her handwriting is not.
        await _record_diary_result(
            db, sid, character_id=character_id, status="failed",
            error="unreadable diary output",
        )
        return {"status": "failed", "reason": "unreadable diary output"}

    # She copies the Showrunner's lines and her own into the page. Reproducing a
    # long line verbatim is the one place a character comes out changed, so say
    # so in the log when it happens. Nothing is rewritten: she also *fixes*
    # things on the way in — a line typed 「手を降る」 came back 「手を振る」 —
    # and a machine putting the original back would undo that.
    diary_mod.log_quote_drift(
        fields.get("content_ja") or "",
        [ln.split(": ", 1)[-1] for ln in session_log.splitlines()],
        character_id=character_id,
    )

    _report(reporter, 0.9, "日記をしまっています")
    inputs = _inputs(session)
    diary_entry = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "summary_ja": fields["summary_ja"],
        "summary_en": fields["summary_en"],
        "summary": fields["summary_ja"],
        "content_ja": fields["content_ja"],
        "content_en": fields["content_en"],
        "content": fields["content_ja"],
        "image_id": image_id,
        "image_ids": image_ids,
        # Which shoot this was, so the entry can lead back to it.
        "session_id": sid,
        "character_id": character_id,
        "theme": str(inputs.get("theme") or ""),
        "read": False,
    }

    await presets_db.add_preset_diary(db, character_id, diary_entry)
    chemistry_pair = await _record_diary_result(
        db, sid, character_id=character_id, status="ok", diary_id=diary_entry["id"],
    )
    if chemistry_pair and spooler is not None:
        (char_a_id, diary_a_id), (char_b_id, diary_b_id) = chemistry_pair
        spooler.submit(
            JobLane.PROMPT,
            "generate_actress_chemistry",
            run_generate_chemistry_job,
            meta={"session_id": sid},
            db=db,
            ollama=ollama,
            session_id=sid,
            character_a_id=char_a_id,
            character_b_id=char_b_id,
            diary_id_a=diary_a_id,
            diary_id_b=diary_b_id,
            model=model,
            num_ctx=num_ctx,
        )
    _report(reporter, 1.0, "日記が書き上がりました")
    return {"status": "ok", "diary_id": diary_entry["id"]}


# The second ask restates the contract. Models that wandered off it once tend to
# come back when told plainly what shape failed.
_DIARY_ASKS: tuple[str, ...] = (
    "今日の撮影の秘密の日記を書いて。SUMMARY_JA / SUMMARY_EN / CONTENT_JA / CONTENT_EN "
    "の4つの見出しだけを使うこと。",
    "さっきの出力は読み取れませんでした。もう一度、日記だけを書いてください。"
    "1行目は必ず `SUMMARY_JA: ` で始め、続けて SUMMARY_EN / CONTENT_JA / CONTENT_EN。"
    "JSON にしない。コードフェンスも使わない。",
)

#: 文字体系が紛れたときの頼み方。**用件が違うので、言い方も変える。**
#: 「読み取れませんでした」と言われても、書き手には何を直せばいいか分からない。
_DIARY_ASK_STRAY = (
    "さっきの日記に、日本語ではない文字が混ざっていました（{stray}）。"
    "同じ日記をもう一度書いてください。**`SUMMARY_JA` と `CONTENT_JA` は、"
    "ひらがな・カタカナ・常用漢字だけ**で書くこと。ハングルや、中国語だけの"
    "漢字を一字も混ぜないでください。英語は `*_EN` の欄にだけ書きます。"
    "見出しは SUMMARY_JA / SUMMARY_EN / CONTENT_JA / CONTENT_EN の4つだけ。"
)


async def run_generate_chemistry_job(
    reporter,
    cancel,
    *,
    db,
    ollama,
    session_id: str,
    character_a_id: str,
    character_b_id: str,
    diary_id_a: str,
    diary_id_b: str,
    model: str = "",
    num_ctx: int | None = None,
):
    """Runs once per duet, right after both actors' diaries from the same shoot
    have landed (queued from _record_diary_result, never twice for one shoot).

    Reads the two fresh entries and asks for a short relationship note,
    informed by them and by where the pair's compatibility vectors + shared
    history currently sit, then stores it on both characters via
    `presets_db.add_chemistry_record` — no session-side state to track once
    this returns; the dossier reads it straight off the character payload.
    """
    _report(reporter, 0.1, "二人の相性を読み解いています")
    preset_a = await presets_db.get_preset(db, character_a_id)
    preset_b = await presets_db.get_preset(db, character_b_id)
    if not preset_a or not preset_b:
        return {"status": "skipped", "reason": "character not found"}

    diaries_a = await presets_db.get_preset_diaries(db, character_a_id)
    diaries_b = await presets_db.get_preset_diaries(db, character_b_id)
    diary_a = next((d for d in diaries_a if str(d.get("id") or "") == diary_id_a), None)
    diary_b = next((d for d in diaries_b if str(d.get("id") or "") == diary_id_b), None)
    if not diary_a or not diary_b:
        return {"status": "skipped", "reason": "diary not found"}

    compat = await compat_mod.compatibility(db, character_a_id, character_b_id)
    system = crew.actress_chemistry_prompt(
        presets_db.preset_to_character(preset_a),
        presets_db.preset_to_character(preset_b),
        diary_a, diary_b, tier=compat["tier"],
    )
    _report(reporter, 0.4, "二人の相性を読み解いています")

    fields: dict[str, str] = {}
    for attempt, ask in enumerate(_CHEMISTRY_ASKS):
        raise_if_cancelled = getattr(cancel, "raise_if_set", None)
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        try:
            raw_resp = await chain._call(
                ollama, system=system, prompt=ask,
                model=model, images=None, num_ctx=num_ctx, think=False,
            )
        except Exception as exc:
            logger.warning("[muse] chemistry generation failed: %s", exc)
            return {"status": "failed", "reason": str(exc)}
        fields = diary_mod.normalize(
            diary_mod.parse_diary(raw_resp), fallback_ja="いい雰囲気で撮影していた",
        )
        if fields.get("content_ja"):
            break
        logger.info("[muse] chemistry output unusable (attempt %d), retrying", attempt + 1)
        _report(reporter, 0.6, "書き直してもらっています")

    if not fields.get("content_ja"):
        return {"status": "failed", "reason": "unreadable chemistry output"}

    record = {
        "id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "session_id": session_id,
        "summary_ja": fields["summary_ja"],
        "summary_en": fields["summary_en"],
        "content_ja": fields["content_ja"],
        "content_en": fields["content_en"],
        "tier": compat["tier"],
        "score": compat["score"],
        "sources": [
            {
                "diary_id": diary_a.get("id"), "character_id": character_a_id,
                "summary_ja": diary_a.get("summary_ja"), "summary_en": diary_a.get("summary_en"),
                "timestamp": diary_a.get("timestamp"),
            },
            {
                "diary_id": diary_b.get("id"), "character_id": character_b_id,
                "summary_ja": diary_b.get("summary_ja"), "summary_en": diary_b.get("summary_en"),
                "timestamp": diary_b.get("timestamp"),
            },
        ],
    }
    await presets_db.add_chemistry_record(db, character_a_id, character_b_id, record)
    _report(reporter, 1.0, "相性メモができました")
    events.publish(session_id, {"type": "chemistry_ready", "tier": compat["tier"]})
    return {"status": "ok"}


_CHEMISTRY_ASKS: tuple[str, ...] = (
    "二人の日記を読んで、相性についての短いメモを書いて。SUMMARY_JA / SUMMARY_EN / "
    "CONTENT_JA / CONTENT_EN の4つの見出しだけを使うこと。",
    "さっきの出力は読み取れませんでした。もう一度、メモだけを書いてください。"
    "1行目は必ず `SUMMARY_JA: ` で始め、続けて SUMMARY_EN / CONTENT_JA / CONTENT_EN。"
    "JSON にしない。コードフェンスも使わない。",
)


async def _read_the_photo(
    db, ollama, session: dict[str, Any], image_id: str, *,
    model: str = "", num_ctx: int | None = None,
) -> str:
    """What is actually in the finished photograph, for her to write about.

    Falls back to the shoot's prompt — which is what this always used to be —
    when there is no photo, no vision model, or the read fails. A page written
    from the tag list is worse than one written from the picture and far better
    than none.
    """
    prompt_desc = str((session.get("shoot") or {}).get("prompt") or "")
    if ollama is None or not image_id:
        return prompt_desc
    images = await images_by_sha(db, [image_id])
    if not images:
        return prompt_desc
    inputs = _inputs(session)
    try:
        raw, blind = await chain._call_seeing(
            ollama,
            system=(
                "You are looking at one photograph. Say what is in it, plainly "
                "and concretely, in 3–5 English sentences: where she is, what "
                "she is wearing, what her body is doing, and — this above all "
                "— what her face is doing. Describe only what the picture "
                "shows. Do not guess at intent, do not praise it, do not "
                "mention prompts or tags."
            ),
            prompt="Describe this photograph.",
            model=_vision_model(inputs) or model,
            images=images,
            num_ctx=num_ctx,
            think=False,
        )
    except Exception:
        logger.warning("[muse] could not read the finished photo", exc_info=True)
        return prompt_desc
    if blind or not str(raw or "").strip():
        # A model that cannot see returns nothing rather than erroring — the
        # trap this codebase has hit before. Treat silence as "did not look".
        logger.info("[muse] the diary's photo read came back blind")
        return prompt_desc
    return str(raw).strip()


def _report(reporter, progress: float, message: str) -> None:
    """Progress for the jobs panel. The diary job used to report nothing at all."""
    update = getattr(reporter, "update", None)
    if update is None:
        return
    try:
        update(progress, message)
    except Exception:
        logger.debug("[muse] diary reporter failed", exc_info=True)


async def _record_diary_result(
    db, session_id: str, *, character_id: str, status: str, diary_id: str = "", error: str = "",
) -> list[tuple[str, str]] | None:
    """Write one actor's outcome back onto the session and tell the panel.

    Nothing announced the diary before this: the Showrunner wrapped the session
    and the entry appeared on the character, minutes later, unmentioned. A duet
    queues two of these jobs, so the session tracks one entry per character_id
    and only reports "done" once every entry has landed — a fast lead diary
    used to flip the aggregate to "ok" while her partner's was still writing.

    Returns ``[(character_id, diary_id), (character_id, diary_id)]`` exactly
    once per duet — to whichever of the two jobs happens to be the one that
    completes the pair — as the signal to queue chemistry generation. A
    `chemistry_queued` flag on the session stops the other job (or a retry)
    from queueing it twice; `None` means "not your job to queue it."
    """
    if not session_id:
        return None
    events.publish(session_id, {
        "type": "diary_status", "status": status, "character_id": character_id,
        "diary_id": diary_id, "error": error,
    })
    try:
        stored = await session_db.load(db, session_id)
    except Exception:
        stored = None
    if stored is None:
        return None
    diary = dict(stored.get("diary") or {})
    entries = dict(diary.get("entries") or {})
    entries[character_id] = {
        "status": status, "diary_id": diary_id, "error": error, "at": time.time(),
    }
    diary["entries"] = entries
    statuses = [str(e.get("status") or "") for e in entries.values()]
    if any(s == "writing" for s in statuses):
        aggregate = "writing"
    elif any(s == "ok" for s in statuses):
        aggregate = "ok"
    else:
        aggregate = "failed"
    diary.update({
        "status": aggregate,
        "diary_id": diary_id if status == "ok" else diary.get("diary_id", ""),
        "error": error,
        "at": time.time(),
    })

    chemistry_pair: list[tuple[str, str]] | None = None
    if is_duet(stored) and not diary.get("chemistry_queued"):
        all_settled = not any(s == "writing" for s in statuses)
        ok_pairs = [
            (cid, str(e.get("diary_id") or ""))
            for cid, e in entries.items()
            if e.get("status") == "ok" and e.get("diary_id")
        ]
        if all_settled and len(entries) >= 2 and len(ok_pairs) >= 2:
            diary["chemistry_queued"] = True
            chemistry_pair = ok_pairs[:2]

    stored["diary"] = diary
    await session_db.save(db, stored, publish=False)
    return chemistry_pair



def _session_chat_log(session: dict[str, Any], *, limit: int = 15) -> str:
    lines = []
    for m in _chat_rows(session)[-limit:]:
        lines.append(f"{m.get('name')}: {m.get('text')}")
    return "\n".join(lines)


def _director_exchanges(session: dict[str, Any], *, limit: int = 14) -> str:
    """Each thing the showrunner said, with what she was doing when he said it.

    A bare 「いいね」 carries nothing on its own — it means something only
    against the beat she had just described. And a correction is not a rule:
    「震えはいらない」 was said to one quiet scene where she had her fingertips
    shaking, and carried forward as a standing preference it would break the
    next shoot that needs a tremble.

    So the pair is the unit, not the line. Her contract makes her restate a
    direction in her own words before she plays it, which means the reply that
    follows each of his lines already says what she was about to do — the
    pairing needs no extra call, only the order it already happened in.
    """
    rows = [
        m for m in _chat_rows(session)
        if m.get("role") in ("user", "muse")
        and m.get("kind") != "banter" and str(m.get("text") or "").strip()
    ]
    out: list[str] = []
    for i, msg in enumerate(rows):
        if msg.get("role") != "user":
            continue
        # Praise points backwards and a direction points forwards, so both
        # sides are shown. 「いいね」 at the end of a shoot has all of its
        # meaning in the line before it and none of its own.
        before = next(
            (str(r.get("text") or "").strip() for r in reversed(rows[:i])
             if r.get("role") == "muse"),
            "",
        )
        after = next(
            (str(r.get("text") or "").strip() for r in rows[i + 1:i + 3]
             if r.get("role") == "muse"),
            "",
        )
        block = []
        if before:
            block.append(f"（直前の私: {before[:160]}）")
        block.append(f"総監督: {str(msg.get('text') or '').strip()[:200]}")
        if after:
            block.append(f"私: {after[:160]}")
        out.append("\n".join(block))
    return "\n\n".join(out[-max(1, int(limit)):])


def _director_highlights(session: dict[str, Any]) -> str:
    notes = [str(n).strip() for n in (session.get("notes") or []) if str(n).strip()]
    return "\n".join(f"- {n}" for n in notes[-8:])


def _image_ids_of(shoot: dict[str, Any] | None) -> list[str]:
    """Image ids out of one take's `images`, in order, without duplicates.

    A single ③ can land more than one frame (`draft_count`). Older sessions
    store bare sha strings; newer ones store `{image_id, ...}` dicts.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in (shoot or {}).get("images") or []:
        if isinstance(item, dict):
            iid = str(item.get("image_id") or "").strip()
        else:
            iid = str(item).strip()
        if iid and iid not in seen:
            seen.add(iid)
            out.append(iid)
    return out


def all_shoot_image_ids(session: dict[str, Any]) -> list[str]:
    """Every final photo this session took, oldest press first.

    `shoot` is only the take being made right now; the ones before it live in
    `shoots` (see `approve_and_shoot`). The diary is the reason this exists —
    it recorded one photo per session no matter how many the showrunner shot,
    and the rest were unreachable from her page.
    """
    out: list[str] = []
    seen: set[str] = set()
    for take in list(session.get("shoots") or []) + [session.get("shoot") or {}]:
        for iid in _image_ids_of(take if isinstance(take, dict) else {}):
            if iid not in seen:
                seen.add(iid)
                out.append(iid)
    return out


async def shoot_photos_of_session(
    db, session: dict[str, Any], *, limit: int = _SHOOT_ARCHIVE_MAX,
) -> list[str]:
    """Every final photo of one shoot — asked of the photos, not the session.

    The session document only ever held the take being made at that moment, so
    a session that pressed ③ four times kept two of its eight photos and the
    other six were unreachable. `shoots` fixes that going forward, and this
    fixes it for every shoot that already happened: each rendered image carries
    `muse_session_id` and `muse_stage` in its own payload
    (`muse/runner.py::_character_payload_extra`), so the images can be asked
    directly and the answer is right for sessions that finished long before
    anything archived a take.

    Falls back to what the session knows if the query cannot run — a diary with
    the last take's photos beats a diary with none.
    """
    known = all_shoot_image_ids(session)
    sid = str(session.get("session_id") or "")
    if not sid or db is None:
        return known[:limit]
    try:
        docs = await db.scroll_all(
            muse_session_id=sid, muse_stage="shoot",
            exclude_drafts=True, gallery_fields=True,
        )
    except Exception:
        logger.warning("[muse] could not read this shoot's photos", exc_info=True)
        return known[:limit]
    found = [
        str(d.get("sha256") or "")
        for d in sorted(docs, key=lambda d: str(d.get("mtime") or ""))
        if d.get("sha256")
    ]
    # Order by when they were taken; anything the session knows about that the
    # image store has not caught up on goes on the end rather than being lost.
    out = found + [i for i in known if i not in set(found)]
    return out[:limit]


def _shoot_image_ids(session: dict[str, Any]) -> list[str]:
    """The current take's frames. Earlier takes: `all_shoot_image_ids`."""
    shot = (session.get("shoot") or {}).get("images") or []
    out: list[str] = []
    seen: set[str] = set()
    for item in shot:
        if isinstance(item, dict):
            iid = str(item.get("image_id") or "").strip()
        else:
            iid = str(item).strip()
        if iid and iid not in seen:
            seen.add(iid)
            out.append(iid)
    return out


def _shoot_image_id(session: dict[str, Any]) -> str:
    ids = _shoot_image_ids(session)
    return ids[0] if ids else ""


def _character_for_id(session: dict[str, Any], preset: dict[str, Any], character_id: str) -> dict[str, Any]:
    def _cached_match(candidate: dict[str, Any]) -> bool:
        return str(candidate.get("character_id") or "") == character_id and (
            isinstance(candidate.get("personality"), dict) or candidate.get("reasoning_ja")
        )

    session_char = session.get("character") or {}
    partner_char = session.get("partner_character") or {}
    if _cached_match(session_char):
        return session_char
    if _cached_match(partner_char):
        return partner_char
    return presets_db.preset_to_character(preset)


async def run_generate_lounge_share_job(
    reporter, cancel, *, db, ollama, session: dict[str, Any], character_id: str,
    model: str = "", num_ctx: int | None = None, spooler=None,
):
    """Friend-facing wrap post to the lounge (not the secret diary)."""
    sid = str(session.get("session_id") or "")
    _report(reporter, 0.05, "楽屋に書き込んでいます")
    preset = await presets_db.get_preset(db, character_id)
    if not preset:
        return {"status": "skipped", "reason": "character not found"}
    char = _character_for_id(session, preset, character_id)
    template = lounge_mod.pick_share_template()
    system = crew.lounge_share_prompt(
        char,
        session_log=_session_chat_log(session),
        photo_desc=str((session.get("shoot") or {}).get("prompt") or ""),
        template=template,
        director_highlights=_director_highlights(session),
    )
    ask = (
        "楽屋への投稿を書いて。TEXT_JA / TEXT_EN と任意の POSE/OUTFIT/EXPRESSION/PLACE/VIBE。"
        "秘密の日記の本音は書かない。"
    )
    try:
        raw = await chain._call(
            ollama, system=system, prompt=ask,
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] lounge share failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}
    fields = lounge_mod.normalize_share(lounge_mod.parse_labelled(raw))
    if not fields.get("text_ja"):
        logger.info("[muse] lounge share empty for %s", character_id)
        return {"status": "failed", "reason": "empty lounge share"}

    inputs = _inputs(session)
    thread = {
        "id": str(uuid.uuid4()),
        "kind": "wrap_share",
        "author_character_id": character_id,
        "author_role": "muse",
        "author_name_ja": str(char.get("name_ja") or preset.get("name_ja") or ""),
        "author_name": str(char.get("name") or preset.get("name") or ""),
        "session_id": sid,
        "image_id": _shoot_image_id(session),
        "theme": str(inputs.get("theme") or ""),
        "template": template,
        "text_ja": fields["text_ja"],
        "text_en": fields["text_en"],
        "tags": fields.get("tags") or {},
        "messages": [{
            "id": str(uuid.uuid4()),
            "turn": 0,
            "character_id": character_id,
            "name_ja": str(char.get("name_ja") or ""),
            "name": str(char.get("name") or ""),
            "text_ja": fields["text_ja"],
            "text_en": fields["text_en"],
            "reaction": "",
        }],
        "created_at": time.time(),
    }
    await lounge_db.save_thread(db, thread)
    events.publish(sid, {"type": "lounge_status", "status": "shared", "thread_id": thread["id"]})
    _report(reporter, 0.6, "親友の反応を待っています")
    if spooler is not None:
        spooler.submit(
            JobLane.PROMPT,
            "generate_lounge_reactions",
            run_generate_lounge_reactions_job,
            meta={"session_id": sid, "thread_id": thread["id"]},
            db=db,
            ollama=ollama,
            thread_id=thread["id"],
            model=model,
            num_ctx=num_ctx,
        )
    _report(reporter, 1.0, "楽屋に投稿しました")
    return {"status": "ok", "thread_id": thread["id"]}


# 何回撮ったら一件ぶん進むか。彼女たちの生活は撮影より遅く流れる。
OUTING_EVERY_SHOOTS = 3


async def _outing_is_due(db, character_id: str) -> bool:
    """前の一件から撮影が `OUTING_EVERY_SHOOTS` 回ぶん進んだか。

    数え方は preset の `shoot_count`（通算撮影回数・既にある）と、直近の
    `outing` スレッドが持つ `shoot_count` の差。**preset に欄を足さない。**
    """
    preset = await presets_db.get_preset(db, character_id) or {}
    try:
        now = int(preset.get("shoot_count") or 0)
    except (TypeError, ValueError):
        now = 0
    if now < 1:
        return False
    try:
        rows = await lounge_db.list_threads(db, limit=40, kind="outing")
    except Exception:
        logger.debug("[muse] could not read the outing feed", exc_info=True)
        return False
    mine = [
        r for r in rows
        if character_id in {
            str(c.get("character_id") or "")
            for c in (r.get("cast") or []) if isinstance(c, dict)
        }
    ]
    if not mine:
        return True
    try:
        last = int(mine[0].get("shoot_count") or 0)
    except (TypeError, ValueError):
        last = 0
    return (now - last) >= OUTING_EVERY_SHOOTS


async def run_generate_outing_job(
    reporter, cancel, *, db, ollama, character_id: str,
    model: str = "", num_ctx: int | None = None,
    spooler=None, comfy=None, workflow: str = "", shot: dict[str, Any] | None = None,
):
    """A day off with the friends she is closest to, written for the feed.

    Nothing about the studio goes in — no session log, no photo, no theme. The
    other lounge posts all take those, and take them into the writing: even the
    friend-facing wrap post comes out as a report about the shoot. This one is
    handed the people and an everyday occasion, and nothing else, so what comes
    back is the part of her life the camera was not there for.
    """
    _report(reporter, 0.1, "お出かけの話を書いています")
    if not await _outing_is_due(db, character_id):
        return {"status": "skipped", "reason": "not due"}

    friends = await compat_mod.friends_of(db, character_id, min_tier="close", limit=2)
    if not friends:
        friends = await compat_mod.friends_of(
            db, character_id, min_tier="acquaintance", limit=2,
        )
    if not friends:
        return {"status": "skipped", "reason": "no friends"}

    def _member(preset: dict[str, Any], cid: str, fallback: dict | None = None) -> dict:
        """一人分の材料。**好き嫌いが要る** —— そこで意見が割れる。

        `voice_ja` を読んでいたが、**preset にその欄は存在しない**。常に空で
        紹介文に落ちていたので、口調（`talk_quirks`）が一度も渡っていなかった。
        だから誰が出かけても同じ調子の会話になっていた。
        """
        f = fallback or {}
        pref = preset.get("preferences") or {}
        return {
            "character_id": cid,
            "name_ja": str(f.get("name_ja") or preset.get("name_ja")
                           or preset.get("name") or ""),
            "name": str(f.get("name") or preset.get("name")
                        or preset.get("name_ja") or ""),
            "voice_ja": str(preset.get("talk_quirks") or ""),
            "summary_ja": str(preset.get("summary_ja") or preset.get("summary") or ""),
            "age": int(preset.get("age") or 0) or None,
            "occupation_ja": str(preset.get("occupation_ja")
                                 or preset.get("occupation") or ""),
            "likes": [str(x) for x in (pref.get("likes") or [])][:3],
            "dislikes": [str(x) for x in (pref.get("dislikes") or [])][:3],
            "dream_ja": str(preset.get("dream_ja") or preset.get("dream") or ""),
        }

    preset = await presets_db.get_preset(db, character_id) or {}
    cast = [_member(preset, character_id)]
    for f in friends[:2]:
        fid = str(f.get("id") or "")
        cast.append(_member(await presets_db.get_preset(db, fid) or {}, fid, f))

    # 前回どこへ行ったか。**一行だけ** —— 続き物にはしない（総監督の指定）。
    last_time = ""
    try:
        for row in await lounge_db.list_threads(db, limit=40, kind="outing"):
            if any(str(c.get("character_id") or "") == character_id
                   for c in (row.get("cast") or []) if isinstance(c, dict)):
                last_time = str(row.get("occasion") or "")
                break
    except Exception:
        logger.debug("[muse] could not read the last outing", exc_info=True)

    season = lounge_mod.season_ja()
    errand = lounge_mod.outing_is_an_errand()
    choices = lounge_mod.outing_choices(12, avoid=last_time)

    # **一段目 —— どこへ行くかを相談する。** 性格がここで一度効く。
    plan_ja, planned_talk = "", ""
    try:
        planned_talk = await chain._call(
            ollama,
            system=crew.outing_plan_prompt(
                cast, choices=choices, last_time=last_time,
                season_ja=season, errand=errand,
            ),
            prompt="相談をお願いします。",
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
        picked = ""
        for line in planned_talk.splitlines():
            head = line.strip().upper()
            if head.startswith("PLAN_JA"):
                plan_ja = line.split(":", 1)[-1].split("：", 1)[-1].strip()
            elif head.startswith("PLAN_PICK"):
                picked = line.split(":", 1)[-1].split("：", 1)[-1].strip()
    except Exception:
        picked = ""
        logger.warning("[muse] the planning turn failed; falling back to a topic",
                       exc_info=True)

    # 相談が読めなければ、これまでどおり抽選のお題で書く
    occasion, hint = (choices[0] if choices else lounge_mod.pick_outing())
    # **お題は一語で残す。** `PLAN_JA` は「三人で美術館へ行くことになった」の
    # ような文なので、そのまま `occasion` にすると次回の「前回の行き先」とも
    # 照合できず、一覧にも長い文が並ぶ。選んだ候補の名前を使う。
    if picked:
        occasion = picked[:16]
        hint = next((h for n, h in choices if n == picked), "")
    _report(reporter, 0.5, "お出かけの話を書いています")
    try:
        raw = await chain._call(
            ollama,
            system=crew.outing_prompt(
                cast, occasion=occasion, hint=hint, when_ja=season,
                plan_ja=plan_ja, planned_talk=planned_talk, errand=errand,
            ),
            prompt="書き込みをお願いします。",
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] outing generation failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}

    parsed = lounge_mod.parse_labelled(raw)
    messages = lounge_mod.normalize_outing(parsed, cast)
    if not messages:
        return {"status": "failed", "reason": "unreadable outing output"}
    for m in messages:
        m["id"] = str(uuid.uuid4())

    try:
        shoot_count = int(preset.get("shoot_count") or 0)
    except (TypeError, ValueError):
        shoot_count = 0
    thread = {
        "id": str(uuid.uuid4()),
        "kind": "outing",
        "author_character_id": character_id,
        "author_role": "muse",
        "author_name_ja": cast[0]["name_ja"],
        "author_name": cast[0]["name"],
        "occasion": occasion,
        "plan_ja": plan_ja,
        "errand": errand,
        "season_ja": season,
        "when_ja": str(parsed.get("WHEN_JA") or "この前"),
        "cast": [{k: c[k] for k in ("character_id", "name_ja", "name")} for c in cast],
        "shoot_count": shoot_count,
        "text_ja": messages[0]["text_ja"],
        "text_en": messages[0]["text_en"],
        "messages": messages,
        "created_at": time.time(),
    }
    await lounge_db.save_thread(db, thread)

    # **頼まれごとの回だけ、一枚焼く。**
    #
    # 総監督から「友達とスナップ撮ってきて」と頼まれた日。撮影のカットでは
    # ないので、寄りも決めポーズも作らない —— 友達が撮った一枚に見えればいい。
    #
    # 画のワークフローは**引き金になったセッションのもの**を使う（総監督の
    # 指定）。描画は必ず `JobLane.GENERATION` を通す —— スケジューラの外で
    # 描くと、カードが埋まっている最中に載って落ちる。
    if errand and spooler is not None and comfy is not None and workflow:
        try:
            await _spool_outing_snapshot(
                db, spooler, comfy, thread, cast,
                workflow=workflow, occasion=occasion, shot=shot or {},
            )
        except Exception:
            logger.warning("[muse] the outing snapshot could not be queued",
                           exc_info=True)

    _report(reporter, 1.0, "お出かけの話を書きました")
    return {"status": "ok", "thread_id": thread["id"]}


async def _spool_outing_snapshot(
    db, spooler, comfy, thread: dict[str, Any], cast: list[dict[str, Any]],
    *, workflow: str, occasion: str, shot: dict[str, Any],
) -> None:
    """その日のスナップを一枚。**焼けたらスレッドに貼る。**

    セッションを持たないので、キャラのボードと同じ道
    （`jobs.render.run_render`）を使う。**新しい描画経路は作らない。**
    """
    from ..jobs.render import run_render

    members, tags = [], []
    for c in cast:
        preset = await presets_db.get_preset(db, str(c.get("character_id") or ""))
        if not preset:
            continue
        char = presets_db.preset_to_character(preset)
        members.append({"subject_tag": preset.get("subject_tag") or "1girl"})
        tags.append([str(t) for t in (char.get("identity_tags") or [])])
    if not members:
        return

    positive = lounge_mod.snapshot_prompt(
        members, identity_tags=tags,
        occasion=lounge_mod.outing_place_en(occasion),
        season=lounge_mod.season_ja(),
    )
    thread_id = str(thread.get("id") or "")

    async def _attach(sha256: str, _meta: dict) -> None:
        row = await lounge_db.get_thread(db, thread_id)
        if row is None:
            return
        row["image_id"] = sha256
        await lounge_db.save_thread(db, row)

    spooler.submit(
        JobLane.GENERATION,
        "outing_snapshot",
        run_render,
        meta={"thread_id": thread_id},
        db=db, comfy=comfy,
        workflow_name=workflow,
        positive=positive,
        negative=str(shot.get("negative_prompt") or ""),
        width=int(shot.get("width") or 0) or None,
        height=int(shot.get("height") or 0) or None,
        steps=int(shot.get("final_steps") or 0) or None,
        cfg=float(shot.get("final_cfg") or 0) or None,
        prefix="outing_snap",
        method="outing_snapshot",
        payload_extra={"thread_id": thread_id, "kind": "outing"},
        attach=_attach,
    )
    logger.info("[muse] an outing snapshot is queued for %s", thread_id)


async def run_generate_lounge_reactions_job(
    reporter, cancel, *, db, ollama, thread_id: str,
    model: str = "", num_ctx: int | None = None,
):
    """Close friends like + 1–2 short comments; seeds trend/feedback memories."""
    _report(reporter, 0.1, "楽屋の反応を集めています")
    thread = await lounge_db.get_thread(db, thread_id)
    if not thread:
        return {"status": "skipped", "reason": "thread not found"}
    author_id = str(thread.get("author_character_id") or "")
    if not author_id:
        return {"status": "skipped", "reason": "no author"}
    friends = await compat_mod.friends_of(db, author_id, min_tier="close", limit=2)
    if not friends:
        # Soft fallback: any acquaintance neighbour so the lounge still breathes
        # when chemistry vectors are thin.
        friends = await compat_mod.friends_of(db, author_id, min_tier="acquaintance", limit=2)
    if not friends:
        return {"status": "skipped", "reason": "no friends"}

    author_preset = await presets_db.get_preset(db, author_id) or {}
    author = {
        "name_ja": thread.get("author_name_ja") or author_preset.get("name_ja") or "",
        "name": thread.get("author_name") or author_preset.get("name") or "",
    }
    system = crew.lounge_reactions_prompt(
        author,
        str(thread.get("text_ja") or ""),
        friends,
        tags=dict(thread.get("tags") or {}),
    )
    ask = "親友のリアクションを書いて。REACTOR_1_*（と必要なら REACTOR_2_*）。"
    try:
        raw = await chain._call(
            ollama, system=system, prompt=ask,
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] lounge reactions failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}

    reactions = lounge_mod.normalize_reactions(lounge_mod.parse_labelled(raw), friends)
    if not reactions:
        return {"status": "failed", "reason": "empty reactions"}

    messages = list(thread.get("messages") or [])
    for turn, react in enumerate(reactions, start=1):
        messages.append({
            "id": str(uuid.uuid4()),
            "turn": turn,
            "character_id": react["character_id"],
            "name_ja": react["name_ja"],
            "name": react["name"],
            "text_ja": react["text_ja"],
            "text_en": react["text_en"],
            "reaction": react["reaction"],
            "stance": react["stance"],
            "twist": react.get("twist") or "",
        })
        # Friend keeps a trend tip (what they might try next).
        tip_ja = react["text_ja"]
        if react["stance"] == "twist" and react.get("twist"):
            tip_ja = f"{react['twist']}（{author.get('name_ja') or '彼女'}の話を聞いて）"
        await presets_db.add_social_seed(db, react["character_id"], {
            "source_thread_id": thread_id,
            "kind": "trend",
            "summary_ja": tip_ja[:160],
            "summary_en": (react["text_en"] or tip_ja)[:160],
            "stance": react["stance"],
            "uses_left": 3,
        })
        # Author keeps friend feedback.
        await presets_db.add_social_seed(db, author_id, {
            "source_thread_id": thread_id,
            "kind": "friend_feedback",
            "summary_ja": f"{react['name_ja'] or react['name']}: {react['text_ja']}"[:160],
            "summary_en": f"{react['name'] or react['name_ja']}: {react['text_en']}"[:160],
            "stance": "try",
            "uses_left": 3,
        })

    thread["messages"] = messages
    thread["reaction_count"] = len(reactions)
    await lounge_db.save_thread(db, thread)

    tags = thread.get("tags") or {}
    trend_bits = [v for k, v in tags.items() if v and k in ("pose", "outfit", "expression", "vibe")]
    twists = [
        {
            "character_id": r["character_id"],
            "name_ja": r["name_ja"],
            "name": r["name"],
            "stance": r["stance"],
            "twist": r.get("twist") or "",
            "text_ja": r["text_ja"],
            "text_en": r["text_en"],
        }
        for r in reactions
        if r.get("stance") in ("twist", "try")
    ]
    if trend_bits or twists:
        await lounge_db.push_trend(db, {
            "from_character_id": author_id,
            "from_name_ja": author.get("name_ja") or "",
            "from_name": author.get("name") or "",
            "thread_id": thread_id,
            "summary_ja": (" / ".join(trend_bits) if trend_bits else (reactions[0]["text_ja"][:80]))[:120],
            "summary_en": (" / ".join(trend_bits) if trend_bits else (reactions[0]["text_en"][:80]))[:120],
            "tags": tags,
            "twists": twists,
        })

    sid = str(thread.get("session_id") or "")
    if sid:
        events.publish(sid, {
            "type": "lounge_status", "status": "reacted", "thread_id": thread_id,
        })
    _report(reporter, 1.0, "楽屋の反応が付きました")
    return {"status": "ok", "thread_id": thread_id, "reactions": len(reactions)}


async def run_generate_lounge_pitch_job(
    reporter, cancel, *, db, ollama, session: dict[str, Any], character_id: str,
    model: str = "", num_ctx: int | None = None,
):
    """Occasional 'how about this?' pitch visible to the showrunner in the lounge."""
    sid = str(session.get("session_id") or "")
    _report(reporter, 0.1, "提案を楽屋に書いています")
    preset = await presets_db.get_preset(db, character_id)
    if not preset:
        return {"status": "skipped", "reason": "character not found"}
    char = _character_for_id(session, preset, character_id)
    system = crew.lounge_pitch_prompt(
        char,
        session_log=_session_chat_log(session),
        photo_desc=str((session.get("shoot") or {}).get("prompt") or ""),
        director_highlights=_director_highlights(session),
    )
    try:
        raw = await chain._call(
            ollama, system=system, prompt="提案を書いて。TEXT_JA / TEXT_EN だけ。",
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] lounge pitch failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}
    fields = lounge_mod.normalize_pitch(lounge_mod.parse_labelled(raw))
    if not fields.get("text_ja"):
        return {"status": "failed", "reason": "empty pitch"}

    inputs = _inputs(session)
    thread = {
        "id": str(uuid.uuid4()),
        "kind": "pitch",
        "status": "open",
        "author_character_id": character_id,
        "author_role": "muse",
        "author_name_ja": str(char.get("name_ja") or preset.get("name_ja") or ""),
        "author_name": str(char.get("name") or preset.get("name") or ""),
        "session_id": sid,
        "image_id": _shoot_image_id(session),
        "theme": str(inputs.get("theme") or ""),
        "text_ja": fields["text_ja"],
        "text_en": fields["text_en"],
        "tags": {},
        "messages": [{
            "id": str(uuid.uuid4()),
            "turn": 0,
            "character_id": character_id,
            "role": "muse",
            "name_ja": str(char.get("name_ja") or ""),
            "name": str(char.get("name") or ""),
            "text_ja": fields["text_ja"],
            "text_en": fields["text_en"],
        }],
        "created_at": time.time(),
    }
    await lounge_db.save_thread(db, thread)
    if sid:
        events.publish(sid, {"type": "lounge_status", "status": "pitch", "thread_id": thread["id"]})
    _report(reporter, 1.0, "提案を楽屋に出しました")
    return {"status": "ok", "thread_id": thread["id"]}


async def run_generate_handpost_habit_job(
    reporter, cancel, *, db, ollama, session: dict[str, Any], character_id: str,
    model: str = "", num_ctx: int | None = None,
):
    """Rare handpost line about the showrunner's taste (not a how-to wiki)."""
    _report(reporter, 0.1, "手帖に癖を書き留めています")
    preset = await presets_db.get_preset(db, character_id) or {}
    notes = _director_highlights(session)
    if not notes.strip():
        return {"status": "skipped", "reason": "no notes"}
    name_ja = str(preset.get("name_ja") or preset.get("name") or "")
    system = crew.showrunner_habit_prompt(
        notes=notes,
        session_log=_session_chat_log(session, limit=10),
        muse_name=name_ja,
    )
    try:
        raw = await chain._call(
            ollama, system=system,
            prompt="手帖の一文を書いて。TITLE_JA / TITLE_EN / BODY_JA / BODY_EN。",
            model=model, images=None, num_ctx=num_ctx, think=False,
        )
    except Exception as exc:
        logger.warning("[muse] handpost habit failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}
    fields = lounge_mod.normalize_habit(lounge_mod.parse_labelled(raw))
    if not fields.get("body_ja"):
        return {"status": "failed", "reason": "empty habit"}
    ja = str(_inputs(session).get("locale") or "ja").startswith("ja")
    title = fields["title"] or ("総監督の癖" if ja else "Showrunner habits")
    if not ja and fields.get("title_en"):
        title = fields["title_en"]
    page = await handpost_db.save_page(db, {
        "title": title,
        "title_ja": fields["title"] or "総監督の癖",
        "title_en": fields.get("title_en") or fields["title"] or "Showrunner habits",
        "body_ja": fields["body_ja"],
        "body_en": fields["body_en"],
        "pinned": False,
        "author": "system",
        "kind": "habit",
        "source_session_id": str(session.get("session_id") or ""),
        "source_character_id": character_id,
    })
    sid = str(session.get("session_id") or "")
    if sid:
        events.publish(sid, {"type": "lounge_status", "status": "habit", "page_id": page["id"]})
    _report(reporter, 1.0, "手帖に書き留めました")
    return {"status": "ok", "page_id": page["id"]}


async def cancel_board(db, spooler, session: dict[str, Any]) -> dict[str, Any]:
    job_id = str((session.get("board") or {}).get("job_id") or "")
    if job_id:
        await spooler.cancel(job_id)
    session["board"] = {}
    session["status"] = "chat"
    session_db.log(session, "board", "cancelled")
    await session_db.save(db, session)
    return session

"""Parse lounge share / reaction LLM output (labelled blocks, not JSON)."""
from __future__ import annotations

import random
import time
import re
from typing import Any

_LABEL_RE = re.compile(
    r"^[ \t]*[#*\-]*[ \t]*([A-Z][A-Z0-9_]*)[ \t]*[:：][ \t]*(.*)$",
)

_SHARE_TEMPLATES = (
    "report",      # どこで撮ってどんな感じだったか
    "praise",      # 監督が良いと言っていたこと
    "soft_flex",   # 軽く自慢
    "ask_friend",  # ちょっと相談
    "vibe",        # 空気・場所のぼやき
)

# What they got up to when there was no camera. Everyday things — the point is
# that it is not work, so nothing here is a shoot, a costume or a location.
#
# The hints say what *happened*, not where. Muse copy that names a place has a
# way of turning up in the picture, which is what
# `test_production_muse_copy_has_no_situation_specific_anchors` is guarding —
# and a small moment is better writing than a venue anyway.
# 一段目（相談）に見せる候補。**行き先ではなく「どんな一日か」**の粒度で
# 揃える —— 添えた一言が会話の種になるので、その形は崩さない。
#
# ここから選ばせるが、**話の流れで別のことになってもよい**と伝える。骨組みで
# あって台本ではない。
#
# **日本語だけで書く。** 下書きの段階で `프리마켓` と `river の河川敷` が
# 紛れた —— 日記で直したのと同じ崩れを、こちらでやった。`_assert_ja` で見る。
_OUTINGS = (
    ("パンケーキ", "話題の店に並んだら、思ったより待たされた"),
    ("ごはん", "遅い時間に、二人でラーメンを食べた"),
    ("買い物", "服を見に行って、結局どちらも何も買わなかった"),
    ("遊園地", "絶叫系に乗ったら、片方だけずっと叫んでいた"),
    ("旅行", "一泊の温泉。帰りの電車の時間を間違えた"),
    ("散歩", "あてもなく歩いて、気づいたら遠くまで来ていた"),
    ("映画", "終わったあと、感想が見事に食い違った"),
    ("水族館", "クラゲの前から動かない子がいた"),
    ("勉強", "課題を持ち寄ったのに、ほとんど喋って終わった"),
    ("猫", "近所の猫に会いに行ったら、逃げられた"),
    ("花火", "遠くの音だけ聞こえて、結局よく見えなかった"),
    ("だらだら", "どちらかの部屋で、何をするでもなく"),
    ("動物園", "目当ての動物が、ずっと寝ていた"),
    ("温室", "湿気で眼鏡が真っ白になって、何も見えなくなった"),
    ("古本屋", "一人だけ、閉店まで動かなかった"),
    ("神社", "階段を数えながら登って、途中で分からなくなった"),
    ("海", "電車で行ったのに、足首まで濡らして帰ってきた"),
    ("河川敷", "座る場所を決めるのに、ずいぶん歩いた"),
    ("展望台", "曇っていて、遠くはほとんど見えなかった"),
    ("図書館", "同じ机で、別々の本を読んで終わった"),
    ("銭湯", "湯あたりして、休憩所で長いこと伸びていた"),
    ("陶芸", "同じ形を作ったつもりが、全然違うものになった"),
    ("ボウリング", "後半になるほど、二人とも下手になっていった"),
    ("カラオケ", "採点が思いのほか辛くて、むきになった"),
    ("ゲームセンター", "取れるまでやると言い張った子がいた"),
    ("プラネタリウム", "始まって十分で寝ていた"),
    ("美術館", "一枚の絵の前で、意見が割れた"),
    ("写真展", "帰りに同じ絵はがきを二人とも買っていた"),
    ("食べ歩き", "商店街で、最初の一軒で満腹になった"),
    ("コンビニ", "新商品を全部買って、少しずつ分け合った"),
    ("朝ごはん", "早起きして出たのに、店がまだ開いていなかった"),
    ("朝市", "何を買うか決めずに行って、荷物が増えすぎた"),
    ("夜のドライブ", "曲を決めるのに、着くまでかかった"),
    ("隣の県", "高速バスで行って、何もせず帰ってきた"),
    ("誰かの実家", "犬に懐かれた子と、警戒された子がいた"),
    ("ホームセンター", "買う予定のないものを、ずっと見ていた"),
    ("百円ショップ", "気づいたら籠がいっぱいになっていた"),
    ("家具屋", "ソファに座ったまま、しばらく立てなかった"),
    ("文房具屋", "同じペンを何度も試し書きしていた"),
    ("レコード屋", "ジャケットだけ見て、一枚も聴かなかった"),
    ("楽器屋", "触っていいものと、そうでないものが分からなかった"),
    ("風の強い日", "髪も話も、何度も途切れた"),
    ("花見", "場所を取るのが遅くて、隅のほうになった"),
    ("紅葉", "写真を撮る役が、ずっと同じ子だった"),
    ("初詣", "おみくじの結果で、その日の空気が決まった"),
    ("雪", "見に行ったのに、着いたら止んでいた"),
    ("台風", "外に出られず、片方の家でずっと喋っていた"),
    ("停電", "暗い部屋で、なぜか声が小さくなった"),
    ("寄り道", "まっすぐ帰るはずが、もう一軒だけ寄った"),
    ("病み上がり", "無理はしない約束で、近所を一周だけした"),
    ("誕生日", "祝われるほうが、いちばん落ち着かなかった"),
    ("引っ越しの手伝い", "運ぶより、荷物を開けるほうに時間がかかった"),
)


#: お題 → 画のための場所（英語）。**日本語のお題はタグに向かない。**
#: 抜けていれば場所を入れないだけ —— 三人が写っていれば写真にはなる。
_OUTING_PLACE = {
    'パンケーキ': 'cafe',
    'ごはん': 'ramen shop',
    '買い物': 'clothing store',
    '遊園地': 'amusement park',
    '旅行': 'hot spring inn',
    '散歩': 'street',
    '映画': 'movie theater',
    '水族館': 'aquarium',
    '勉強': 'cafe table',
    '猫': 'alley',
    '花火': 'summer festival',
    'だらだら': 'bedroom',
    '動物園': 'zoo',
    '温室': 'greenhouse',
    '古本屋': 'old bookstore',
    '神社': 'shrine stairs',
    '海': 'shoreline',
    '河川敷': 'riverbank',
    '展望台': 'observation deck',
    '図書館': 'library',
    '銭湯': 'bathhouse entrance',
    '陶芸': 'pottery studio',
    'ボウリング': 'bowling alley',
    'カラオケ': 'karaoke room',
    'ゲームセンター': 'arcade',
    'プラネタリウム': 'planetarium',
    '美術館': 'art museum',
    '写真展': 'gallery',
    '食べ歩き': 'shopping street',
    'コンビニ': 'convenience store',
    '朝ごはん': 'morning diner',
    '朝市': 'morning market',
    '夜のドライブ': 'car at night',
    '隣の県': 'bus stop',
    '誰かの実家': 'living room',
    'ホームセンター': 'hardware store',
    '百円ショップ': 'variety store',
    '家具屋': 'furniture store',
    '文房具屋': 'stationery shop',
    'レコード屋': 'record shop',
    '楽器屋': 'music store',
    '風の強い日': 'windy street',
    '花見': 'cherry blossoms',
    '紅葉': 'autumn leaves',
    '初詣': 'shrine',
    '雪': 'snowy street',
    '台風': 'window rain',
    '停電': 'dark room candle',
    '寄り道': 'evening street',
    '病み上がり': 'quiet neighborhood',
    '誕生日': 'cafe table',
    '引っ越しの手伝い': 'cardboard boxes',
}


def outing_place_en(occasion: str) -> str:
    """お題に対応する、画に入れられる場所。無ければ ""。"""
    return _OUTING_PLACE.get(str(occasion or "").strip(), "")


def _assert_ja(rows: tuple[tuple[str, str], ...]) -> None:
    """候補に日本語以外が紛れていないか。**自分で踏んだので、置いておく。**"""
    from .diary import stray_script
    for name, hint in rows:
        stray = stray_script(name) or stray_script(hint)
        if stray:
            raise ValueError(f"お出かけの候補に日本語以外が混ざっている: {stray!r}")


_assert_ja(_OUTINGS)


def pick_outing() -> tuple[str, str]:
    """お題を一つ。同じ話が続かないよう、毎回引き直す。"""
    return random.choice(_OUTINGS)


def outing_choices(n: int = 12, *, avoid: str = "") -> tuple[tuple[str, str], ...]:
    """相談に見せる候補。**全部は見せない** —— 52件並べると読み流される。

    `avoid` は前回の行き先。同じ所が続かないよう、候補から外す。
    """
    pool = [o for o in _OUTINGS if not avoid or o[0] != avoid]
    return tuple(random.sample(pool, min(max(1, n), len(pool))))


#: 総監督から「友達とスナップ撮ってきて」と頼まれる割合。
#:
#: **低く保つ。** お出かけは「総監督が居なかった時間」を作るための機能で、
#: 毎回が頼まれごとになると意味が反転する。四回に一回くらい。
OUTING_ERRAND_CHANCE = 0.25


def outing_is_an_errand(rng: random.Random | None = None) -> bool:
    """今日のお出かけは、総監督からの頼まれごとつきか。"""
    return (rng or random).random() < OUTING_ERRAND_CHANCE


#: 月から季節。**同じ「散歩」でも二月と八月では違う話になる。**
_SEASON_JA = (
    (12, 2, "冬"), (3, 5, "春"), (6, 8, "夏"), (9, 11, "秋"),
)


def season_ja(when: float | None = None) -> str:
    """いまの季節を一語。`outing_prompt` の `when_ja` に入れる。"""
    month = time.localtime(when if when is not None else time.time()).tm_mon
    for lo, hi, name in _SEASON_JA:
        if lo <= hi and lo <= month <= hi:
            return name
        if lo > hi and (month >= lo or month <= hi):   # 12〜2月をまたぐ
            return name
    return ""


def outing_occasions() -> tuple[str, ...]:
    return tuple(name for name, _ in _OUTINGS)


def normalize_outing(
    parsed: dict[str, str],
    cast: list[dict[str, Any]],
    *,
    max_turns: int = 6,
) -> list[dict[str, Any]]:
    """`TURN_N_*` を掛け合いに変える。話者は cast の並びを回る。

    `normalize_reactions` が `REACTOR_N_*` を友達に割り当てているのと同じ手口。
    一度の呼び出しで全員ぶん書かせるので、人数が増えても呼び出しは増えない。
    """
    out: list[dict[str, Any]] = []
    if not cast:
        return out
    for i in range(1, max_turns + 1):
        text_ja = (parsed.get(f"TURN_{i}_JA") or parsed.get(f"T{i}_JA") or "").strip()
        if not text_ja:
            continue
        text_en = (parsed.get(f"TURN_{i}_EN") or parsed.get(f"T{i}_EN") or "").strip()
        who = (parsed.get(f"TURN_{i}_WHO") or parsed.get(f"T{i}_WHO") or "").strip()
        speaker = next(
            (c for c in cast if who and str(c.get("name_ja") or "") in who),
            cast[(i - 1) % len(cast)],
        )
        out.append({
            "id": "",
            "turn": len(out),
            "character_id": str(speaker.get("character_id") or speaker.get("id") or ""),
            "name_ja": str(speaker.get("name_ja") or ""),
            "name": str(speaker.get("name") or speaker.get("name_ja") or ""),
            "text_ja": text_ja,
            "text_en": text_en or text_ja,
            "reaction": (parsed.get(f"TURN_{i}_REACTION") or "").strip()[:8],
        })
    return out


#: スナップの画。**撮影のカットではない**ので、寄りも決めポーズも作らない。
#: 友達が撮った一枚に見えるだけの語で足りる。
#: **休みの日の服。** これが無いと、サンプラーが埋める —— 人数が複数・屋外・
#: 服の指定なし、という並びだと、行き先に関わらず同じ既定へ寄っていた
#: （総監督の報告・2026-08-29）。季節の一語だけ入れて、あとは決めすぎない。
#: 季節 → (屋外, 屋内)。**屋内では上着を脱ぐ。** 図書館でマフラーを巻いて
#: いる絵は、それだけで嘘になる。
_SNAP_WEAR = {
    "春": ("casual clothes, long_sleeves, cardigan",
           "casual clothes, long_sleeves"),
    "夏": ("casual clothes, short_sleeves, summer_clothes",
           "casual clothes, short_sleeves"),
    "秋": ("casual clothes, long_sleeves, jacket",
           "casual clothes, long_sleeves"),
    "冬": ("casual clothes, coat, scarf, winter_clothes",
           "casual clothes, sweater, long_sleeves"),
}
_SNAP_WEAR_DEFAULT = ("casual clothes, street_clothes",
                      "casual clothes, street_clothes")

#: 屋内の行き先。`outdoors` を固定で入れていたので、屋内の行き先でも屋外の
#: 絵になっていた —— そして屋外＋複数＋服なしが、上の既定を強めていた。
_INDOOR_PLACES = frozenset({
    "aquarium", "arcade", "art museum", "bedroom", "bowling alley", "cafe",
    "cafe table", "car at night", "cardboard boxes", "clothing store",
    "convenience store", "dark room candle", "furniture store", "gallery",
    "greenhouse", "hardware store", "hot spring inn", "karaoke room",
    "library", "living room", "morning diner", "movie theater", "music store",
    "old bookstore", "planetarium", "pottery studio", "ramen shop",
    "record shop", "stationery shop", "variety store", "window rain",
})

#: **集合写真にしない。** もとは `standing together, looking at viewer` で、
#: それは並んでレンズを見る絵 —— 総監督「集合写真みたいになってなんだか変」。
#: 遊んでいる最中を撮る。毎回同じにならないよう、その日の一つを引く。
_SNAP_MOMENT = (
    "walking together, talking, laughing",
    "leaning in to look at something together, smiling",
    "one looking back at the others, mid-step",
    "sitting side by side, heads turned to each other",
    "pointing at something off-frame, following her gaze",
    "mid-laugh, hair moving, not posed",
)

_SNAP_LOOK = (
    "candid photo, snapshot, casual, natural light, slight motion blur, "
    "amateur photography"
)


def snapshot_prompt(
    cast: list[dict[str, Any]], *, identity_tags: list[list[str]],
    occasion: str = "", season: str = "",
    rng: random.Random | None = None,
) -> str:
    """友達同士で撮った一枚。**その日の行き先が背景になる。**

    撮影のプロンプトとは別物 —— スタジオの語彙（衣装指定、決めポーズ、
    ライティング）は入れない。休みの日にスマホで撮った写真に見えればいい。

    ただし**服は要る**。書かなければサンプラーが埋め、行き先に関わらず同じ
    既定へ寄っていた。季節の一語だけ置く。

    そして**並ばせない**。`standing together, looking at viewer` は集合写真
    そのもので、遊んでいる写真にはならなかった。
    """
    from . import identity as identity_mod
    parts: list[str] = list(identity_mod.subject_tags(cast))
    for tags in identity_tags:
        parts += [t for t in tags if t]
    inside = bool(occasion) and occasion in _INDOOR_PLACES
    wear = _SNAP_WEAR.get(str(season or "").strip(), _SNAP_WEAR_DEFAULT)
    parts.append(wear[1] if inside else wear[0])
    if occasion:
        parts.append(occasion)
    parts.append("indoors" if inside else "outdoors")
    parts.append((rng or random).choice(_SNAP_MOMENT))
    parts.append(_SNAP_LOOK)
    seen: dict[str, None] = {}
    for t in parts:
        for one in str(t).split(","):
            one = one.strip()
            if one:
                seen.setdefault(one, None)
    return ", ".join(seen)


def stamp_faces(rows: list[dict[str, Any]], faces: dict[str, str]) -> None:
    """スレッドと各発言に、話す人の顔を貼る。**その場で書き換える。**

    楽屋は誰の発言かで話者が変わるので、**発言ごと**に要る。画面側は既に
    `thumb(sha)` を持っているので、sha が届けば出せる。

    顔が無いキャラ（board を引いていない）は空のまま —— 画面側で出し分ける。
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["face"] = faces.get(str(row.get("author_character_id") or ""), "")
        for m in row.get("messages") or []:
            if isinstance(m, dict):
                m["face"] = faces.get(str(m.get("character_id") or ""), "")
        for c in row.get("cast") or []:
            if isinstance(c, dict):
                c["face"] = faces.get(str(c.get("character_id") or ""), "")


def outing_summary_line(thread: dict[str, Any]) -> str:
    """楽屋の一件を、彼女の手元に残る一行にする。

    要約ではなく**指し先**。いつ・誰と・何を、それだけ。中身が読みたければ
    楽屋にスレッドがある。総監督:「要約は諸刃の剣。結構消えてしまうので。」
    """
    when = str(thread.get("when_ja") or "").strip()
    occasion = str(thread.get("occasion") or "").strip()
    names = [
        str(c.get("name_ja") or "").strip()
        for c in (thread.get("cast") or []) if isinstance(c, dict)
    ]
    with_who = "と".join([n for n in names if n][1:]) or "みんな"
    bits = [b for b in (when, f"{with_who}と{occasion}" if occasion else with_who) if b]
    return "、".join(bits)[:80]


# Traits that make a Muse more likely to pitch an idea to the showrunner.
_PITCHY_TRAITS = (
    "curious", "creative", "bold", "proactive", "talkative", "mischievous",
    "playful", "expressive", "confident", "adventurous", "stylish",
    "好奇心", "積極", "提案", "おしゃれ", "元気", "大胆", "遊び", "発言",
)


def pick_share_template() -> str:
    return random.choice(_SHARE_TEMPLATES)


def pitch_chance(character: dict[str, Any] | None = None, preset: dict[str, Any] | None = None) -> float:
    """Base chance of a post-wrap pitch, boosted by outgoing personality traits."""
    chance = 0.12
    traits: list[str] = []
    src = character or {}
    p = src.get("personality") if isinstance(src.get("personality"), dict) else {}
    traits.extend(str(t) for t in (p.get("traits") or []) if t)
    if preset:
        traits.extend(str(t) for t in (preset.get("personality") or []) if t)
    blob = " ".join(traits).lower()
    hits = sum(1 for key in _PITCHY_TRAITS if key.lower() in blob)
    chance += min(0.28, hits * 0.07)
    return min(0.45, chance)


def should_pitch(character: dict[str, Any] | None = None, preset: dict[str, Any] | None = None) -> bool:
    return random.random() < pitch_chance(character, preset)


def should_write_habit(*, notes: list[Any], rng: random.Random | None = None) -> bool:
    """Rare handpost about the showrunner's taste — only when there were notes."""
    if not any(str(n).strip() for n in (notes or [])):
        return False
    roll = (rng or random).random()
    return roll < 0.18


def parse_labelled(raw: str) -> dict[str, str]:
    """Split `KEY: value` lines; multi-line values accumulate until the next key."""
    text = (raw or "").strip()
    if not text:
        return {}
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            current = m.group(1).upper()
            bodies.setdefault(current, [])
            inline = (m.group(2) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in bodies.items() if "\n".join(v).strip()}


def normalize_share(parsed: dict[str, str], *, fallback_ja: str = "") -> dict[str, Any]:
    text_ja = (parsed.get("TEXT_JA") or parsed.get("JA") or fallback_ja or "").strip()
    text_en = (parsed.get("TEXT_EN") or parsed.get("EN") or "").strip()
    if not text_en and text_ja:
        text_en = text_ja
    tags = {
        "pose": (parsed.get("POSE") or "").strip(),
        "outfit": (parsed.get("OUTFIT") or "").strip(),
        "expression": (parsed.get("EXPRESSION") or "").strip(),
        "place": (parsed.get("PLACE") or "").strip(),
        "vibe": (parsed.get("VIBE") or "").strip(),
    }
    return {
        "text_ja": text_ja,
        "text_en": text_en,
        "tags": {k: v for k, v in tags.items() if v},
    }


def normalize_pitch(parsed: dict[str, str], *, fallback_ja: str = "") -> dict[str, str]:
    text_ja = (parsed.get("TEXT_JA") or parsed.get("PITCH_JA") or fallback_ja or "").strip()
    text_en = (parsed.get("TEXT_EN") or parsed.get("PITCH_EN") or "").strip()
    if not text_en and text_ja:
        text_en = text_ja
    return {"text_ja": text_ja, "text_en": text_en}


#: 本文の途中から生えた「英語版」を切る。**知らないラベルも境界として扱う。**
#:
#: モデルが出力例の値（`English body`）を真似て `English body: ...` と書き、
#: `_LABEL_RE`（大文字の語しか見ない）を通り抜けて日本語の本文に流れ込んだ。
#: 実測 4頁中2頁。今週これで四度目の同じ形なので、読む側でも受け止める。
_TRAILING_EN_RE = re.compile(
    r"(?im)^[ \t]*(english[ _]?(body|version|text)?|en)[ \t]*[:：][ \t]*",
)


def split_trailing_english(text: str) -> tuple[str, str]:
    """`(日本語, こぼれた英語)`。境界が無ければ英語側は ""。"""
    m = _TRAILING_EN_RE.search(str(text or ""))
    if not m:
        return str(text or "").strip(), ""
    return text[:m.start()].strip(), text[m.end():].strip()


def normalize_habit(parsed: dict[str, str]) -> dict[str, str]:
    # **先に切る。** 英語の欄が空のときは日本語で埋める作りなので、切る前に
    # 埋めると、こぼれた英語を含んだ塊が両方の欄に入る（実測でそうなった）。
    title, spilled_title = split_trailing_english(
        parsed.get("TITLE_JA") or parsed.get("TITLE") or "")
    body_ja, spilled_body = split_trailing_english(
        parsed.get("BODY_JA") or parsed.get("TEXT_JA") or "")
    title_en = (parsed.get("TITLE_EN") or spilled_title or title).strip()
    body_en = (parsed.get("BODY_EN") or parsed.get("TEXT_EN")
               or spilled_body or "").strip()
    if not body_en and body_ja:
        body_en = body_ja
    if not title and body_ja:
        title = body_ja.splitlines()[0][:40]
        title_en = title
    return {
        "title": title,
        "title_en": title_en,
        "body_ja": body_ja,
        "body_en": body_en,
    }


def normalize_reactions(
    parsed: dict[str, str],
    friends: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map REACTOR_N_* blocks onto the friend list order."""
    out: list[dict[str, Any]] = []
    for i, friend in enumerate(friends[:2], start=1):
        prefix = f"REACTOR_{i}_"
        text_ja = (parsed.get(f"{prefix}JA") or parsed.get(f"R{i}_JA") or "").strip()
        text_en = (parsed.get(f"{prefix}EN") or parsed.get(f"R{i}_EN") or "").strip()
        if not text_ja and not text_en:
            continue
        if not text_en:
            text_en = text_ja
        reaction = (parsed.get(f"{prefix}REACTION") or parsed.get(f"R{i}_REACTION") or "💕").strip()
        stance = (parsed.get(f"{prefix}STANCE") or parsed.get(f"R{i}_STANCE") or "try").strip().lower()
        if stance not in ("try", "twist", "skip"):
            stance = "try"
        twist = (parsed.get(f"{prefix}TWIST") or parsed.get(f"R{i}_TWIST") or "").strip()
        out.append({
            "character_id": str(friend.get("id") or ""),
            "name_ja": str(friend.get("name_ja") or friend.get("name") or ""),
            "name": str(friend.get("name") or friend.get("name_ja") or ""),
            "reaction": reaction[:8] or "💕",
            "text_ja": text_ja,
            "text_en": text_en,
            "stance": stance,
            "twist": twist,
            "tier": str(friend.get("tier") or ""),
            "score": float(friend.get("score") or 0.0),
        })
    return out

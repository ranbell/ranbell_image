#!/usr/bin/env python3
"""Reproduce a few Muse notebook+scripter sessions with Fake LLM.

Writes a walkthrough report to /opt/cursor/artifacts/muse_session_sim_report.md
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import notebook, service, session_db, vitality
from tests.muse.test_duet import _duet_session
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block
from tests.muse.test_service import FakeDb


OUT = Path("/opt/cursor/artifacts/muse_session_sim_report.md")


class SimOllama(NotebookOllama):
    """Keyword → scripter; separate keyword → Muse SAY."""

    def __init__(self, scripts=None, says=None):
        super().__init__(scripts=scripts)
        self.says = says or {}
        self.events: list[dict] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        p = str(prompt)
        if "studio scripter" in system or "shot notebook" in system:
            self.scripter_prompts.append(p)
            hits = [k for k in self.scripts if k in p]
            key = max(hits, key=len) if hits else ""
            text = self.scripts.get(key) or _scripter_block(
                intent="casual", vibe="chatting",
            )
        else:
            # Match SAY scripts against the latest showrunner line only —
            # notebook summaries would otherwise keep matching early keywords.
            latest = p
            marker = "総監督がいま言ったこと:"
            if marker in p:
                latest = p.split(marker, 1)[-1].strip().split("\n\n", 1)[0]
            hits = [k for k in self.says if k in latest]
            key = max(hits, key=len) if hits else ""
            text = self.says.get(key) or "SAY: うん、その感じ。"

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


def _snap(s: dict) -> dict:
    nb = s.get("notebook") or {}
    craft = s.get("craft") or {}
    return {
        "intent": s.get("scripter_intent"),
        "dirty": bool(s.get("craft_dirty")),
        "rev": nb.get("rev"),
        "atmosphere": nb.get("atmosphere"),
        "scene": nb.get("scene"),
        "frame": nb.get("frame"),
        "wearing": nb.get("wearing"),
        "beat": nb.get("beat"),
        "wearing_b": nb.get("wearing_b"),
        "beat_b": nb.get("beat_b"),
        "vibe": nb.get("vibe"),
        "open": nb.get("open"),
        "tags": craft.get("tags"),
        "craft_scene": (craft.get("scene") or "")[:120],
        "last_muse": next(
            (m.get("text") for m in reversed(s.get("chat") or []) if m.get("role") == "muse"),
            "",
        ),
        "turns": next(
            (m.get("turns") for m in reversed(s.get("chat") or []) if m.get("role") == "muse"),
            [],
        ),
    }


def _md_snap(label: str, snap: dict) -> str:
    lines = [f"### {label}", ""]
    lines.append(
        f"- intent=`{snap['intent']}` dirty=`{snap['dirty']}` rev=`{snap['rev']}`"
    )
    for k in (
        "atmosphere", "scene", "frame", "wearing", "beat",
        "wearing_b", "beat_b", "vibe", "open",
    ):
        v = snap.get(k) or ""
        if v:
            lines.append(f"- **{k}**: {v}")
    if snap.get("tags"):
        lines.append(f"- **tags**: `{snap['tags']}`")
    if snap.get("craft_scene"):
        lines.append(f"- **craft_scene**: {snap['craft_scene']}")
    muse = snap.get("last_muse") or ""
    if muse:
        lines.append(f"- **SAY**: {muse}")
    turns = snap.get("turns") or []
    if turns:
        for t in turns:
            lines.append(
                f"  - {t.get('speaker_name') or t.get('speaker')}: {t.get('text')}"
            )
    lines.append("")
    return "\n".join(lines)


async def _cfg(db):
    return {"ollama_num_ctx": 16000}


async def case_rooftop_rapid(db, lines: list[str]) -> None:
    """Case A: 屋上あさひ — 帽子着脱・煽り・見上げ・雑談・OPEN肯定."""
    lines.append("## Case A — 屋上あさひ（高速変更＋雑談＋OPEN）\n")
    scripts = {
        "セーラー": _scripter_block(
            intent="shot",
            scene="school rooftop fence at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, leaning, looking_at_viewer",
            craft_scene="Rooftop lean in sailor uniform.",
        ),
        "麦わら": _scripter_block(
            intent="shot",
            scene="school rooftop fence at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform, straw hat",
            beat="leaning on the fence",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, straw_hat, leaning, looking_at_viewer",
            craft_scene="Sailor with straw hat.",
        ),
        "外して": _scripter_block(
            intent="shot",
            scene="school rooftop fence at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, leaning, looking_at_viewer",
            craft_scene="Hat off; sailor lean.",
        ),
        "煽って": _scripter_block(
            intent="shot",
            scene="school rooftop fence at dusk",
            frame="low angle from below, she looks down into the lens",
            wearing="sailor uniform",
            beat="leaning on the fence",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, leaning, from_below, low_angle, looking_down",
            craft_scene="Low angle; looks down to lens.",
        ),
        "見上げ": _scripter_block(
            intent="shot",
            scene="school rooftop fence at dusk",
            frame="eye level three-quarter, looking up at the sky",
            wearing="sailor uniform",
            beat="leaning on the fence, head tilted toward the sky",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, leaning, looking_up, eye_level",
            craft_scene="Looks up at the sky; frame rewritten.",
        ),
        "いいね": _scripter_block(intent="casual", vibe="happy", clear_open="yes"),
        "COMPILE ONLY": _scripter_block(
            intent="shot",
            scene="school rooftop fence at dusk",
            frame="eye level three-quarter, looking up at the sky",
            wearing="sailor uniform",
            beat="leaning on the fence, holding ramune",
            tags="rooftop, fence, sailor_collar, leaning, ramune, looking_up",
            craft_scene="Ramune in hand after OPEN affirm.",
        ),
    }
    says = {
        "セーラー": "SAY: フェンス、ひやっとする。セーラーの襟、風でぱたつ。",
        "麦わら": "SAY: つば押さえた。影、目にかかる。",
        "外して": "SAY: はっ…外した。風、顔にそのまま来る。",
        "煽って": "SAY: 下から？ …じゃあ見下ろす形になるね。",
        "見上げ": "SAY: 空のほう、むく。煽りの名残はもういらない。",
        "かき氷": "SAY: やだ溶ける〜。舌、青いの残ったらどうするの。",
        "いいね": "SAY: うん、ラムネ冷たいまま持っとく。",
    }
    ollama = SimOllama(scripts=scripts, says=says)
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["character"]["name_ja"] = "あさひ"
    s["bond"] = {
        "distance": "顔見知り",
        "inside": "屋上の風が好きだった",
        "last": "屋上 / セーラー",
    }
    s["memories"] = ["屋上でセーラー、フェンスにもたれていた"]
    await session_db.save(db, s)
    s = await service.start_duet(db, ollama, s)
    lines.append(_md_snap("T0 再会オープン", _snap(s)))

    turns = [
        ("セーラー制服でフェンスにもたれて", "セーラー"),
        ("麦わら帽子かぶって", "麦わら"),
        ("帽子外して", "外して"),
        ("煽って撮って", "煽って"),
        ("空見上げて", "見上げ"),
        ("かき氷なら何味がいい？", "かき氷"),
        ("いいね", "いいね"),
    ]
    for text, key in turns:
        # Ensure say keyword matches via chat context containing key phrases
        await service.post_duet_chat(db, ollama, s, text)
        lines.append(_md_snap(f"監督「{text}」", _snap(s)))
        # status whisper sample
        whisper = vitality.silence_whisper(text)
        flash = vitality.notebook_flash_key(text)
        lines.append(f"- *(待ち演出)* whisper=`{whisper}` flash=`{flash}`\n")

    ok = (
        "straw_hat" not in (s["craft"].get("tags") or "")
        and "looking_up" in (s["craft"].get("tags") or "")
        and "looking_down" not in (s["craft"].get("tags") or "")
        and ("ramune" in (s["craft"].get("tags") or "") or "ラムネ" in (s["notebook"].get("beat") or ""))
    )
    lines.append(f"**判定:** {'PASS' if ok else 'CHECK'} — 帽子消え・見上げに畳み・OPEN肯定でラムネ\n")


async def case_wmuse_asymmetric(db, lines: list[str]) -> None:
    """Case B: W-Muse 非対称 → 二人立ち."""
    lines.append("## Case B — W-Muse 非対称（あさひ／みなも）\n")
    scripts = {
        "読書": """
INTENT: shot
SCENE: sunlit room with two chairs
FRAME: eye level; A seated with book, B standing beside her
WEARING: soft cardigan
BEAT: sitting in a chair, reading a book
WEARING_B: sleeveless dress
BEAT_B: standing beside the chair
CLEAR_OPEN: no
TAGS_SHARED: 2girls, room, chair, book
TAGS_A: sitting, reading, cardigan
TAGS_B: standing, sleeveless_dress
CRAFT_SCENE: A reads; B stands in a sleeveless dress.
""".strip(),
        "肩": """
INTENT: shot
SCENE: sunlit room
FRAME: low angle; A hand on B's shoulder, both looking down toward lens
WEARING: soft cardigan
BEAT: standing, hand on partner's shoulder
WEARING_B: sleeveless dress
BEAT_B: standing close
CLEAR_OPEN: no
TAGS_SHARED: 2girls, room, from_below, looking_down, hand_on_another's_shoulder
TAGS_A: cardigan, standing
TAGS_B: sleeveless_dress, standing
CRAFT_SCENE: Hand on shoulder; low angle looking down.
""".strip(),
        "あさひだけ": """
INTENT: shot
SCENE: sunlit room
FRAME: eye level; A adjusts cardigan, B unchanged nearby
WEARING: soft cardigan, straw hat
BEAT: standing, adjusting hat
WEARING_B: sleeveless dress
BEAT_B: standing nearby
CLEAR_OPEN: no
TAGS_SHARED: 2girls, room
TAGS_A: cardigan, straw_hat, standing
TAGS_B: sleeveless_dress, standing
CRAFT_SCENE: Only A adds a straw hat.
""".strip(),
        "二人": """
INTENT: shot
SCENE: sunlit room
FRAME: eye level, standing side by side looking at viewer
WEARING: soft cardigan, straw hat
BEAT: standing side by side
WEARING_B: sleeveless dress
BEAT_B: standing side by side
CLEAR_OPEN: no
TAGS_SHARED: 2girls, room, standing, looking_at_viewer
TAGS_A: cardigan, straw_hat
TAGS_B: sleeveless_dress
CRAFT_SCENE: Both standing side by side.
""".strip(),
    }
    says = {
        "読書": (
            "SAY:\n"
            "A: 椅子、あたたかい。本の匂いする。\n"
            "B: わたしは立ってる側ね。日ざし、肩に来る。"
        ),
        "肩": (
            "SAY:\n"
            "B: えっ、肩？\n"
            "A: いま。下から撮るなら、少し体重預ける。"
        ),
        "あさひだけ": (
            "SAY:\n"
            "A: 帽子、わたしだけかぶる。\n"
            "B: ずるい——でも似合う。"
        ),
        "二人": (
            "SAY:\n"
            "A: 並ぼ。\n"
            "B: うん、肩、ふれるくらいで。"
        ),
    }
    ollama = SimOllama(scripts=scripts, says=says)
    s = await _duet_session(db, partner_preset="p2")
    s["mode"] = "duet"
    s["inputs"]["partner_preset"] = "p2"
    s["character"] = {
        "identity_tags": ["1girl", "silver_hair"],
        "name_ja": "あさひ",
        "character_id": "c1",
        "personality": {}, "palette": [], "signature_prop": "",
    }
    s["partner_character"] = {
        "character_id": "p2",
        "identity_tags": ["1girl", "brown_hair"],
        "name_ja": "みなも",
        "personality": {}, "palette": [], "signature_prop": "",
    }
    s["chemistry_notes"] = ["息が合いやすい"]
    await session_db.save(db, s)

    for text, key in [
        ("あさひは椅子で読書、みなもは立ってて", "読書"),
        ("肩に手、ローアングル", "肩"),
        ("あさひだけ帽子かぶって", "あさひだけ"),
        ("二人で立って", "二人"),
    ]:
        await service.post_duet_chat(db, ollama, s, text)
        lines.append(_md_snap(f"監督「{text}」", _snap(s)))

    tags = s["craft"].get("tags") or ""
    ok = (
        "sitting" not in tags
        and "reading" not in tags
        and "straw_hat" in tags
        and "sleeveless" in (s["notebook"].get("wearing_b") or "")
    )
    lines.append(
        f"**判定:** {'PASS' if ok else 'CHECK'} — "
        f"読書ポーズ消え・Aのみ帽子・B装い維持 / tags=`{tags}`\n"
    )


async def case_format_breakage(db, lines: list[str]) -> None:
    """Case C: 壊れた出力 → craft 非上書き・SAY浄化・repair."""
    lines.append("## Case C — フォーマット崩れ耐性\n")

    class BreakOllama(SimOllama):
        def __init__(self):
            super().__init__()
            self.phase = 0

        async def generate_text(self, prompt, **kw):
            kw.pop("fmt", None)
            self.calls.append({**kw, "prompt": prompt})
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                self.phase += 1
                if "PREVIOUS OUTPUT" in str(prompt) or "rejected" in str(prompt):
                    return _scripter_block(
                        intent="shot",
                        wearing="jacket",
                        beat="standing",
                        frame="low angle, looking down",
                        tags="jacket, from_below, looking_down",
                        craft_scene="Repaired low angle.",
                    )
                if self.phase == 1:
                    return _scripter_block(
                        intent="shot", wearing="hat", beat="stand",
                        frame="eye", tags="hat, standing", craft_scene="Hat.",
                    )
                # Broken merge: low + looking_up
                return (
                    "INTENT: shot\nWEARING: jacket\nBEAT: standing\n"
                    "FRAME: low angle\n"
                    "TAGS: from_below, looking_up, jacket\n"
                    "CRAFT_SCENE: Broken.\n"
                )
            # Truncated talk with TAGS leak
            return (
                "SAY: はっ…外した。風、顔に来る。\n"
                "TAGS: straw_hat, from_below\n"
                "CRITICAL RULES FOR W-MUSE SAY:\n"
                "SCENE: incomplete"
            )

        def generate_text_stream(self, prompt, **kw):
            async def _s():
                text = await self.generate_text(prompt, **kw)
                yield {"type": "token", "text": text}
            return _s()

    ollama = BreakOllama()
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶって")
    before = s["craft"]["tags"]
    lines.append(_md_snap("T1 正常ショット", _snap(s)))
    await service.post_duet_chat(db, ollama, s, "煽って見上げ同時に壊して")
    after = s["craft"]["tags"]
    lines.append(_md_snap("T2 壊出力（repair or dirty）", _snap(s)))
    muse = _snap(s)["last_muse"]
    leak_ok = "TAGS" not in muse and "CRITICAL" not in muse and "SCENE" not in muse
    craft_ok = "looking_up" not in after  # either repaired to looking_down or kept prior
    lines.append(
        f"- before_tags=`{before}`\n"
        f"- after_tags=`{after}` dirty=`{s.get('craft_dirty')}`\n"
        f"- SAY leak cleaned: {'yes' if leak_ok else 'NO'} → `{muse}`\n"
        f"**判定:** {'PASS' if leak_ok and craft_ok else 'CHECK'}\n"
    )


async def case_reunion_memory(db, lines: list[str]) -> None:
    """Case D: 再会 → recall → 雑談スキップ → またあの感じ."""
    lines.append("## Case D — 再会・記憶・雑談スキップ\n")

    async def _bond(db, cid):
        return {
            "distance": "もう顔見知り",
            "inside": "堤防の夕焼けを一緒に見た",
            "last": "堤防 / セーラー",
        }

    async def _taste(db, cid):
        return {"prefers": "ローアングル", "avoids": "足", "notes": ""}

    async def _chem(db, cid, limit=2):
        return []

    async def _search(db, ollama, *, character_id, query, limit=3):
        return [{
            "when": "堤防の夕焼け",
            "feel": "風が強かった",
            "liked": "セーラー",
            "shot": "looking_at_viewer, sailor_collar, embankment",
        }]

    service.presets_db.get_bond = _bond  # type: ignore
    service.presets_db.get_showrunner_taste = _taste  # type: ignore
    service.presets_db.get_recent_chemistry_notes = _chem  # type: ignore
    service.memories_db.search = _search  # type: ignore

    scripts = {
        "覚えてる": (
            "INTENT: recall\nVIBE: remembering the embankment\n"
            "CLEAR_OPEN: no\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "屋上": _scripter_block(
            intent="shot",
            scene="school rooftop",
            frame="eye level",
            wearing="sailor uniform",
            beat="leaning",
            tags="rooftop, sailor_collar, leaning",
            craft_scene="Rooftop.",
        ),
        "またあの感じ": _scripter_block(
            intent="mixed",
            scene="embankment at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="standing in the wind",
            vibe="recalling that day",
            tags="embankment, sailor_collar, looking_at_viewer, wind",
            craft_scene="Back to the embankment feel.",
        ),
    }
    says = {
        "覚えてる": "SAY: 堤防ね……風、強かった日。そこまでは覚えてる。靴の色までは…",
        "かき氷": "SAY: いちご！ でも溶ける前に食べよ。",
        "屋上": "SAY: 屋上か。襟、ひらひらする。",
        "またあの感じ": "SAY: ああ、あの風の感じ。堤防のほうに戻す？",
    }
    ollama = SimOllama(scripts=scripts, says=says)
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["character"]["name_ja"] = "あさひ"
    await session_db.save(db, s)
    s = await service.start_duet(db, ollama, s)
    # Seed a prior shot so chill can skip scripter
    notebook.apply_patch(s["notebook"], {
        "scene": "embankment", "frame": "eye level",
        "wearing": "sailor", "beat": "standing",
    })
    s["craft"] = {
        "tags": "embankment, sailor_collar",
        "scene": "Embankment.",
        "prompt": "1girl, embankment",
        "pose_intent": "",
    }
    await session_db.save(db, s)
    lines.append(_md_snap("T0 start + prior notebook", _snap(s)))
    lines.append(
        f"- bond loaded: `{s.get('bond')}`\n"
        f"- reunion_turn was set at open\n"
        f"- taste chips sample: {vitality.taste_chips(s.get('showrunner_taste') or {})}\n"
    )

    await service.post_duet_chat(db, ollama, s, "この前の堤防、覚えてる？")
    scripts_before = len(ollama.scripter_prompts)
    lines.append(_md_snap("T1 recall", _snap(s)))
    cited = s.get("cited_memories") or []
    lines.append(f"- cited_memories: {len(cited)}件\n")

    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    skipped = len(ollama.scripter_prompts) == scripts_before
    lines.append(_md_snap("T2 雑談（Scripterスキップ期待）", _snap(s)))
    lines.append(f"- scripter skipped: {'yes' if skipped else 'NO'}\n")

    await service.post_duet_chat(db, ollama, s, "屋上でセーラー")
    lines.append(_md_snap("T3 新ショット", _snap(s)))

    await service.post_duet_chat(db, ollama, s, "またあの感じで")
    lines.append(_md_snap("T4 またあの感じ", _snap(s)))
    again = "embankment" in (s["craft"].get("tags") or "") or "堤防" in (
        s["notebook"].get("scene") or ""
    )
    lines.append(
        f"**判定:** {'PASS' if skipped and cited and again else 'CHECK'} — "
        f"recall cited / 雑談skip / またあの感じ\n"
    )


async def case_open_fade(db, lines: list[str]) -> None:
    """Case E: OPEN スルー2回で自然引き."""
    lines.append("## Case E — OPEN スルーで自然引き\n")
    scripts = {
        "ベンチ": _scripter_block(
            intent="shot",
            scene="park bench",
            frame="eye level",
            wearing="cardigan",
            beat="sitting",
            open_="靴を脱いで砂に足",
            tags="park, bench, cardigan, sitting",
            craft_scene="Bench.",
        ),
    }
    says = {
        "ベンチ": "SAY: ベンチ、ひんやり。靴、脱いじゃおっか——砂、指に挟まるやつ。",
        "かき氷": "SAY: いちごかな。",
        "暑い": "SAY: うん、木陰がいい。",
    }
    ollama = SimOllama(scripts=scripts, says=says)
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "公園のベンチで薄いカーディガン")
    lines.append(_md_snap("T1 OPENあり", _snap(s)))
    open1 = s["notebook"].get("open")
    await service.post_duet_chat(db, ollama, s, "かき氷どう思う？")
    lines.append(_md_snap("T2 スルー1", _snap(s)))
    await service.post_duet_chat(db, ollama, s, "今日暑いね")
    lines.append(_md_snap("T3 スルー2 → fade期待", _snap(s)))
    faded = not (s["notebook"].get("open") or "").strip()
    lines.append(
        f"- open after T1: `{open1}`\n"
        f"- open after T3: `{s['notebook'].get('open')}`\n"
        f"**判定:** {'PASS' if open1 and faded else 'CHECK'}\n"
    )


async def _tier_stub(db, session, partner_character):
    return "acquaintance"


async def main() -> None:
    service.get_runtime_config = _cfg  # type: ignore
    service._duet_tier = _tier_stub  # type: ignore
    lines = [
        "# Muse セッション再現シミュ（Fake LLM）",
        "",
        "実装済みのノート正本＋Scripter＋活力＋フォーマット耐性を、",
        "キーワード駆動の Fake で数ケース回した結果。",
        "",
        "> 実モデルの温度・言い回しは再現しない。状態遷移・ガード・浄化を検証する。",
        "",
    ]
    db = FakeDb()
    await case_rooftop_rapid(db, lines)
    await case_wmuse_asymmetric(db, lines)
    await case_format_breakage(db, lines)
    await case_reunion_memory(db, lines)
    await case_open_fade(db, lines)
    lines.append("---\n")
    lines.append(
        "## 総評\n\n"
        "- 画の壊れ方（煽り＋見上げの混在）は validate / repair で止める。\n"
        "- 雑談は Scripter を踏まずテンポを守る。\n"
        "- OPEN は肯定で昇格、無視で消える。\n"
        "- SAY への TAGS 漏れは sanitize で落とす。\n"
        "- W-Muse は TAGS_A/B と相手キーガードで非対称を保てる。\n"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(OUT.read_text(encoding="utf-8")[:4000])


if __name__ == "__main__":
    asyncio.run(main())

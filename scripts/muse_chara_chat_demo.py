#!/usr/bin/env python3
"""Run the real duet service path with Chara JSON characters.

No Ollama in this environment — Muse SAY is assembled from the VOICE contract
that `actress_duet_prompt` already injects from personality_presets.json
(first person, address, quirks, examples). Scripter stays Fake notebook blocks.

Writes:
  /opt/cursor/artifacts/muse_chara_chat_demo.md
and prints chat transcripts to stdout.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.characters.presets import load_seed_presets, preset_to_character
from app.muse import service, session_db
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block
from tests.muse.test_service import FakeDb

OUT = Path("/opt/cursor/artifacts/muse_chara_chat_demo.md")

SAMPLE = ["c001", "c002", "c014", "c020"]

USER_TURNS = [
    "放課後、ここで少し撮ろう",
    "もう少しこっち向いて",
    "照れないで、そのままでいい",
    "かき氷なら何味がいい？",
    "じゃあその感じで",
]


def _parse_voice(system: str) -> dict[str, str]:
    def grab(pat: str) -> str:
        m = re.search(pat, system)
        return (m.group(1).strip() if m else "")

    first = grab(r"一人称\s*/\s*first person:\s*([^\n—]+)")
    first = first.split("—")[0].strip() or "私"
    addr = grab(r"総監督の呼び方\s*/\s*address:\s*([^\n—]+)")
    addr = addr.split("—")[0].strip() or "総監督"
    quirks = grab(r"口調の癖\s*/\s*talk quirks:\s*(.+)")
    voice = grab(r"声の質感\s*/\s*speaking voice:\s*(.+)")
    habit = grab(r"仕草の癖\s*/\s*body habit while talking:\s*(.+)")
    habit = habit.split("—")[0].strip()
    name = grab(r"CHARACTER NAME:\s*[^\n/]+/\s*(.+)")
    examples: list[str] = []
    block = system.split("EXAMPLE energy", 1)[-1] if "EXAMPLE energy" in system else ""
    for line in block.splitlines():
        line = line.strip().lstrip("- ").strip()
        if not line or line.startswith("CHARACTER") or line.startswith("WHAT "):
            break
        if line.startswith("（") or line.startswith("("):
            continue
        examples.append(line.strip("「」"))
    return {
        "first": first,
        "addr": addr,
        "quirks": quirks,
        "voice": voice,
        "habit": habit,
        "name": name,
        "ex0": examples[0] if examples else "",
        "ex1": examples[1] if len(examples) > 1 else "",
    }


def _habit_hint(habit: str, quirks: str) -> str:
    blob = f"{habit} {quirks}"
    if "レンズキャップ" in blob or "lens cap" in blob.lower():
        return "レンズキャップ、指でくりくり……"
    if "日付印" in blob or "books" in blob.lower():
        return "日付印、両手で包むみたいに握って……"
    if "リボン" in blob or "stem" in blob.lower() or "花" in quirks:
        return "リボンの結び目、指が止まらない……"
    if "足踏み" in blob or "bag strap" in blob.lower() or "早口" in quirks:
        return "足、止まらない。袋のひも一巻きして——"
    if "将棋" in quirks or "piece" in blob.lower():
        return "取った駒、指のあいだで……"
    return ""


def _build_say(system: str, user: str) -> str:
    v = _parse_voice(system)
    f, a = v["first"], v["addr"]
    hint = _habit_hint(v["habit"], v["quirks"])
    soft = "……かな" in (v["quirks"] or "") or "ゆっくり" in (v["quirks"] or "")
    fast = "早口" in (v["quirks"] or "") or f == "アタシ"
    whisper = "囁" in (v["quirks"] or "") or "小声" in (v["quirks"] or "")
    flower = "花屋" in (v["quirks"] or "") or a.endswith("様")
    # Opening turn: theme briefing, no "総監督がいま言ったこと".
    opening = (
        "総監督がいま言ったこと:" not in user
        and ("お題" in user or "このターンの話し方" in user)
    )
    # Prefer the latest showrunner line when present.
    if "総監督がいま言ったこと:" in user:
        user = user.split("総監督がいま言ったこと:", 1)[-1].strip().split("\n\n", 1)[0]

    def join(*parts: str) -> str:
        return "SAY: " + "".join(p for p in parts if p)

    if opening:
        if fast:
            return join(
                f"おっす{a}——アタシ、もう現場入り。",
                "指定時間より早くていい画、撮れるやつから行こう。",
            )
        if whisper:
            return join(
                f"あ、あの……{a}。また、お会いできて……嬉しいです。",
                f"{hint or '静かなところで、'}ゆっくり、で。",
            )
        if flower:
            return join(
                f"{a}。……今日も、きれいに包めそうな光ですね。",
                "まずは場所から、決めましょうか。",
            )
        end = "……かな。" if soft else "。"
        return join(
            f"……{a}。来た{end}",
            f"{hint}" if hint else "",
            "今日の光、逃したくない。",
        )

    if "撮ろう" in user or "ここで" in user:
        if v["ex0"]:
            # Anchor on her example rhythm, then land the setup.
            lead = v["ex0"][:42]
            if fast:
                return join(
                    f"{lead}——いや今の話。",
                    f"放課後ここでいい、{a}。アタシ先に場所取る。"
                    if f == "アタシ"
                    else f"放課後、ここで。{a}、急ごとかも。",
                )
            if flower:
                return join(
                    f"{a}。……放課後、ここで、ですか。",
                    "包み方みたいに、余白、残していいですか。",
                )
            if whisper:
                return join(
                    f"あ、あの……{a}。放課後、ここで、ですね。",
                    "声、大きくしなくて大丈夫、です……",
                )
            tail = f"{hint}" if hint else ""
            end = "……かな。" if soft else "。"
            return join(
                f"{lead}——",
                f"放課後、ここで撮る、了解{end}",
                f"{tail}" if tail else "",
            )
        return join(f"{a}、放課後ここで。……{f}、準備できた。")

    if "向いて" in user:
        if fast:
            return join(
                f"ん、こっち？ 了解、{a}。",
                f"{hint or '一息——'}顔、合わせた。撮って。",
            )
        if whisper:
            return join(
                f"……はい。{a}のほう、見ます。",
                f"{hint or ''}目、逸らさないように……します。",
            )
        if flower:
            return join(
                f"{a}。……少し、正面ですね。",
                "リボンより顔、優先で。向きます。",
            )
        end = "……かな。" if soft else "。"
        return join(
            f"……うん。{a}の方、向く{end}",
            f"{hint}" if hint else "",
        )

    if "照れ" in user or "そのままで" in user:
        if fast:
            return join(
                f"照れてねえし——まあ少し。でもこのまま行く、{a}。",
                "笑顔、出しすぎない版でいい？",
            )
        if whisper:
            return join(
                f"そ、そのままで、ですか……。{a}。",
                f"{hint or '印、握りしめつつ、'}動かない、です。",
            )
        if flower:
            return join(
                f"{a}。……照れても、包みは止めません。",
                "このままで、いいんですよね。",
            )
        end = "……かな。" if soft else "。"
        return join(
            f"……っ。動かないで、って言われた気する{end}",
            f"{hint}このまま。" if hint else "このまま、いる。",
        )

    if "かき氷" in user or "何味" in user:
        if fast:
            return join(
                f"ブルーハワイ一択、{a}。溶ける前に食いきる前提。",
                "アタシ遅れるの嫌いだから。",
            )
        if whisper:
            return join(
                f"あ、あの……苺ミルク、がいいです。{a}。",
                "静かだと、スプーンの音まで聞こえて……ふふ。",
            )
        if flower:
            return join(
                f"{a}。……桜餡、とかどうでしょう。",
                "リボン色に近い味、だなって。",
            )
        end = "……かな。" if soft else "。"
        return join(
            f"宇治金時……{end}",
            "青い舌、写真に残ったら困る。",
        )

    # affirm
    if fast:
        return join(f"よしその感じ、{a}。アタシもう動ける。")
    if whisper:
        return join(f"……はい。その感じ、で。……嬉しいです、{a}。")
    if flower:
        return join(f"{a}。……その感じで、結ばせてください。")
    end = "……かな。" if soft else "。"
    return join(f"うん、その感じ{end}", f"{hint}" if hint else "")


class CharaOllama(NotebookOllama):
    """Scripter = notebook Fake; Muse SAY = VOICE-contract assembly."""

    def __init__(self):
        super().__init__(scripts={
            "撮ろう": _scripter_block(
                intent="shot",
                scene="after-school spot, soft side light",
                frame="eye level, looking at viewer",
                wearing="casual after-school clothes",
                beat="standing, turning toward the lens",
                tags="after_school, soft_light, looking_at_viewer, standing",
                craft_scene="After school; she turns toward the lens.",
            ),
            "向いて": _scripter_block(
                intent="shot",
                scene="after-school spot, soft side light",
                frame="eye level close, facing camera",
                wearing="casual after-school clothes",
                beat="facing the camera, hands busy with a habit",
                tags="after_school, looking_at_viewer, close-up",
                craft_scene="Closer; facing the camera.",
            ),
            "照れ": _scripter_block(
                intent="shot",
                scene="after-school spot, soft side light",
                frame="eye level, held still",
                wearing="casual after-school clothes",
                beat="holding still despite a blush",
                tags="after_school, blush, looking_at_viewer",
                craft_scene="She holds still; a blush stays.",
            ),
            "かき氷": _scripter_block(intent="casual", vibe="playful"),
            "その感じ": _scripter_block(
                intent="shot",
                scene="after-school spot, soft side light",
                frame="eye level, looking at viewer",
                wearing="casual after-school clothes",
                beat="settled pose, ready",
                tags="after_school, looking_at_viewer, standing",
                craft_scene="Settled on that beat.",
            ),
        })
        self.chat_log: list[tuple[str, str]] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        p = str(prompt)
        if "studio scripter" in system or "shot notebook" in system:
            self.scripter_prompts.append(p)
            hits = [k for k in self.scripts if k in p]
            key = max(hits, key=len) if hits else ""
            text = self.scripts.get(key) or _scripter_block(intent="casual")
        else:
            # Pass full prompt so opening vs chat turns can be distinguished.
            text = _build_say(system, p)
            self.chat_log.append(("muse_raw", text))

        async def _stream():
            yield {"type": "token", "text": text}

        return _stream()


async def _cfg(db):
    return {"ollama_num_ctx": 16000}


async def run_character(pid: str) -> tuple[dict, list[tuple[str, str]], str]:
    service.get_runtime_config = _cfg  # type: ignore

    async def _tier(*a, **k):
        return ""

    service._duet_tier = _tier  # type: ignore

    preset = next(r for r in load_seed_presets() if r.get("id") == pid)
    ch = preset_to_character(preset)
    db = FakeDb()
    ollama = CharaOllama()
    session = await service.create_session(db, {
        "theme": "放課後のささやかな撮影",
        "character_id": pid,
        "workflow": "w.json",
        "model": "m",
        "mode": "duet",
    })
    session["character"] = {
        **ch,
        "character_id": pid,
        "palette": ch.get("palette") or [],
        "signature_prop": ch.get("signature_prop") or "",
    }
    await session_db.save(db, session)
    session = await service.start_duet(db, ollama, session)

    transcript: list[tuple[str, str]] = []
    # Opening muse line from start_duet
    for m in session.get("chat") or []:
        if m.get("role") == "muse":
            transcript.append(("muse", str(m.get("text") or "")))
        elif m.get("role") == "user":
            transcript.append(("user", str(m.get("text") or "")))

    for user in USER_TURNS:
        session = await service.post_duet_chat(db, ollama, session, user)
        transcript.append(("user", user))
        last = next(
            (m for m in reversed(session.get("chat") or []) if m.get("role") == "muse"),
            None,
        )
        if last:
            transcript.append(("muse", str(last.get("text") or "")))

    voice_snip = ""
    for call in reversed(ollama.calls):
        sys_t = str(call.get("system") or "")
        if "VOICE" in sys_t and "studio scripter" not in sys_t:
            voice_snip = sys_t
            break
    return session, transcript, voice_snip


def _fmt_chat(name: str, first: str, addr: str, quirks: str, transcript: list) -> str:
    lines = [
        f"### {name}",
        f"- 一人称: {first} / 呼び方: {addr}",
        f"- 癖: {quirks}",
        "",
        "```",
    ]
    for role, text in transcript:
        if role == "user":
            lines.append(f"総監督: {text}")
        else:
            # strip SAY: if present
            body = text
            if body.startswith("SAY:"):
                body = body[4:].lstrip()
            lines.append(f"{name}: {body}")
            lines.append("")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


async def main() -> None:
    md = [
        "# Muse 実会話デモ（Chara JSON 正本）",
        "",
        "> 経路は本番 `start_duet` / `post_duet_chat`。",
        "> Ollama 未接続のため SAY は system に載った VOICE（JSON由来）から組み立て。",
        "> 同じ総監督セリフでもキャラで口調が分岐するかを見る。",
        "",
        "## 共通シナリオ",
        "",
    ]
    for u in USER_TURNS:
        md.append(f"1. {u}")
    md.append("")

    print("=" * 60)
    print("Muse 会話デモ（Chara JSON → VOICE → SAY）")
    print("※ Ollamaなし。契約どおりの個性分岐を表示")
    print("=" * 60)

    for pid in SAMPLE:
        session, transcript, voice = await run_character(pid)
        ch = session.get("character") or {}
        name = str(ch.get("name_ja") or pid)
        first = str(ch.get("first_person_ja") or "")
        addr = str(ch.get("user_address_ja") or "")
        quirks = str(ch.get("talk_quirks") or "")[:60]
        block = _fmt_chat(name, first, addr, quirks, transcript)
        md.append(block)
        print()
        print(f"—— {name}（{pid}）——")
        print(f"一人称={first} 呼び方={addr}")
        print(f"癖={quirks}")
        for role, text in transcript:
            body = text[4:].lstrip() if text.startswith("SAY:") else text
            who = "総監督" if role == "user" else name
            print(f"{who}: {body}")
        # prove voice contract carried JSON
        v = _parse_voice(voice)
        assert v["first"] == first or first in voice
        assert (ch.get("talk_quirks") or "")[:10] in voice

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md), encoding="utf-8")
    print()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())

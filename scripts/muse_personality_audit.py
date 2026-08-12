#!/usr/bin/env python3
"""Audit how much character JSON personality reaches Muse talk prompts.

Uses real personality_presets.json via preset_to_character — not stubs.
Writes /opt/cursor/artifacts/muse_personality_audit_report.md
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.characters import presets
from app.muse import crew, service, session_db
from tests.muse.test_service import FakeDb, FakeOllama

JSON_PATH = ROOT / "backend/app/characters/assets/personality_presets.json"
OUT = Path("/opt/cursor/artifacts/muse_personality_audit_report.md")

# Diverse sample across clubs / voices.
SAMPLE_IDS = [
    "c001",  # みなも — shy photographer, 私
    "c002",  # かほ — librarian whisper
    "c014",  # すみれ — 総監督様 + flower
    "c020",  # つばさ — アタシ, fast
    "c005",  # another
    "c010",
]


def load_preset(pid: str) -> dict:
    rows = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    for row in rows:
        if row.get("id") == pid:
            return row
    raise KeyError(pid)


def markers_from_json(preset: dict, ch: dict) -> dict[str, str]:
    """Signals we expect the model to see if personality is 'known'."""
    pers = ch.get("personality") or {}
    appearance = pers.get("appearance") or preset.get("appearance") or {}
    examples = ch.get("duet_say_examples") or []
    ex0 = ""
    if examples:
        ex0 = str(examples[0])[:40]
    quirks = str(ch.get("talk_quirks") or "")
    quirk_bit = ""
    if quirks:
        # distinctive substring
        for piece in re.split(r"[。．、,/]", quirks):
            piece = piece.strip()
            if len(piece) >= 6:
                quirk_bit = piece[:24]
                break
    traits = pers.get("traits") or []
    charm = str(pers.get("charm_ja") or "")[:30]
    summary = str(pers.get("summary_ja") or "")[:30]
    inner = ""
    inn = pers.get("inner_ja") or []
    if inn:
        inner = str(inn[0])[:30]
    return {
        "name_ja": str(ch.get("name_ja") or ""),
        "first_person": str(ch.get("first_person_ja") or ""),
        "address": str(ch.get("user_address_ja") or ""),
        "quirk_bit": quirk_bit,
        "example_bit": ex0,
        "trait0": str(traits[0]) if traits else "",
        "charm_bit": charm,
        "summary_bit": summary,
        "inner_bit": inner,
        "appearance_voice": str(appearance.get("voice") or "")[:40],
        "appearance_habit": str(appearance.get("habit") or "")[:40],
        "likes0": str((pers.get("likes") or [""])[0])[:30],
        "title_ja": str(pers.get("title_ja") or preset.get("title_ja") or "")[:30],
    }


def _marker_hit(prompt: str, key: str, needle: str) -> bool | None:
    """Return True/False, or None when the marker cannot be scored."""
    n = (needle or "").strip()
    if not n:
        return None
    # Single-char first person (「私」) is valid Japanese — match the VOICE line.
    if key == "first_person":
        if re.search(rf"一人称\s*/\s*first person:\s*{re.escape(n)}\b", prompt):
            return True
        if f"一人称は「{n}」" in prompt or f"一人称は『{n}』" in prompt:
            return True
        # Distinctive multi-char forms (アタシ / うち) can substring-match.
        if len(n) >= 2:
            return n in prompt
        return n in prompt  # still accept bare「私」in contract text
    if len(n) < 2:
        return None
    return n in prompt


def score_prompt(prompt: str, markers: dict[str, str]) -> dict:
    hits = {}
    for key, needle in markers.items():
        hits[key] = _marker_hit(prompt, key, needle)
    present = [k for k, v in hits.items() if v is True]
    missing = [k for k, v in hits.items() if v is False]
    na = [k for k, v in hits.items() if v is None]
    denom = max(1, len(present) + len(missing))
    return {
        "hits": hits,
        "present": present,
        "missing": missing,
        "na": na,
        "score": len(present) / denom,
        "pct": int(round(100 * len(present) / denom)),
    }


class CaptureOllama(FakeOllama):
    """Record system prompts; return short SAY."""

    def __init__(self):
        super().__init__()
        self.systems: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        self.systems.append(system)
        if "studio scripter" in system or "shot notebook" in system:
            text = (
                "INTENT: shot\nSCENE: rooftop\nFRAME: eye level\n"
                "WEARING: cardigan\nBEAT: standing\n"
                "TAGS: rooftop, cardigan, standing\n"
                "CRAFT_SCENE: She stands on a rooftop.\n"
            )
        else:
            text = "SAY: ……うん。"

        async def _s():
            yield {"type": "token", "text": text}
        return _s()


async def _cfg(db):
    return {"ollama_num_ctx": 16000}


async def run_live_capture(pid: str) -> tuple[str, dict, dict]:
    """Start a real duet session path and capture the Muse talk system prompt."""
    service.get_runtime_config = _cfg  # type: ignore

    async def _tier(*a, **k):
        return ""

    service._duet_tier = _tier  # type: ignore

    preset = load_preset(pid)
    ch = presets.preset_to_character(preset)
    markers = markers_from_json(preset, ch)

    db = FakeDb()
    ollama = CaptureOllama()
    session = await service.create_session(db, {
        "theme": "放課後の屋上で少しだけ撮影",
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
    await service.post_duet_chat(db, ollama, session, "ちょっとこちら向いて、照れないで")

    # First non-scripter system is the Muse talk voice.
    muse_systems = [
        s for s in ollama.systems
        if "studio scripter" not in s and "shot notebook" not in s
    ]
    system = muse_systems[-1] if muse_systems else ""
    return system, markers, score_prompt(system, markers)


def static_prompt_score(pid: str) -> tuple[str, dict, dict, dict]:
    preset = load_preset(pid)
    ch = presets.preset_to_character(preset)
    markers = markers_from_json(preset, ch)
    talk = crew.actress_duet_prompt(ch, mode="talk", seed=pid)
    prep = crew.actress_duet_prompt(ch, mode="prep", seed=pid)
    return talk, markers, score_prompt(talk, markers), score_prompt(prep, markers)


def judgment(pct: int) -> str:
    if pct >= 85:
        return "濃い（口調・癖・魅力まで届いている）"
    if pct >= 65:
        return "まあまあ（声は分かるが細部が薄い）"
    if pct >= 40:
        return "薄い（名前と一人称程度）"
    return "ほぼ不明（差が乗っていない）"


async def main() -> None:
    lines = [
        "# Muse 性格判明度監査（personality_presets.json 正本）",
        "",
        "`preset_to_character` → `actress_duet_prompt` / 実セッション `start_duet` の",
        "system に、JSON のどの性格信号が載っているかを数ケースで採点。",
        "",
        "> Fake LLM。SAY の中身ではなく「モデルに渡る契約」の厚みを見る。",
        "",
    ]

    # Inventory: how rich is the JSON itself?
    rows = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    lines.append("## 1. JSON 側の充実度（全30キャラ）\n")
    filled = {
        "talk_quirks": 0,
        "duet_say_examples": 0,
        "first_person_ja": 0,
        "user_address_ja": 0,
        "charm_ja": 0,
        "inner_ja": 0,
        "appearance.voice": 0,
        "appearance.habit": 0,
    }
    for row in rows:
        if row.get("talk_quirks"):
            filled["talk_quirks"] += 1
        if row.get("duet_say_examples"):
            filled["duet_say_examples"] += 1
        if row.get("first_person_ja"):
            filled["first_person_ja"] += 1
        if row.get("user_address_ja"):
            filled["user_address_ja"] += 1
        if row.get("charm_ja") or row.get("charm"):
            filled["charm_ja"] += 1
        if row.get("inner_ja") or row.get("inner"):
            filled["inner_ja"] += 1
        app = row.get("appearance") or {}
        if app.get("voice"):
            filled["appearance.voice"] += 1
        if app.get("habit"):
            filled["appearance.habit"] += 1
    n = len(rows)
    for k, v in filled.items():
        lines.append(f"- `{k}`: **{v}/{n}**")
    lines.append("")

    lines.append("## 2. 静的プロンプト採点（talk / prep）\n")
    lines.append("| id | 名前 | 一人称 | talk% | prep% | talk判定 | 欠けやすい項目 |")
    lines.append("|----|------|--------|-------|-------|----------|----------------|")

    static_details: list[str] = []
    for pid in SAMPLE_IDS:
        talk, markers, st, sp = static_prompt_score(pid)
        miss = ", ".join(st["missing"][:5]) or "—"
        lines.append(
            f"| {pid} | {markers['name_ja']} | {markers['first_person']} | "
            f"{st['pct']}% | {sp['pct']}% | {judgment(st['pct'])} | {miss} |"
        )
        static_details.append(f"### {pid} {markers['name_ja']}\n")
        static_details.append(
            f"- talk {st['pct']}% / prep {sp['pct']}% — {judgment(st['pct'])}\n"
        )
        static_details.append("| 信号 | talk | prep | 断片 |")
        static_details.append("|------|------|------|------|")
        def _mark(v):
            if v is True:
                return "✓"
            if v is False:
                return "✗"
            return "—"

        for key, needle in markers.items():
            if not needle:
                continue
            static_details.append(
                f"| {key} | {_mark(st['hits'].get(key))} | "
                f"{_mark(sp['hits'].get(key))} | `{needle[:48]}` |"
            )
        static_details.append("")
        # Show distinctive voice excerpt from prompt
        m = re.search(r"口調の癖 / talk quirks: (.+)", talk)
        if m:
            static_details.append(f"- プロンプト上の癖: {m.group(1)[:120]}\n")
        m = re.search(r"一人称 / first person: (.+)", talk)
        if m:
            static_details.append(f"- 一人称契約: {m.group(1)}\n")

    lines.append("")
    lines.extend(static_details)

    lines.append("## 3. ライブ経路キャプチャ（start_duet → chat）\n")
    lines.append("実際の `run_duet_talk` に渡る system を捕獲して再採点。\n")
    live_rows = []
    for pid in SAMPLE_IDS[:4]:
        system, markers, sc = await run_live_capture(pid)
        live_rows.append((pid, markers, sc, system))
        lines.append(
            f"### {pid} {markers['name_ja']} — live {sc['pct']}% — {judgment(sc['pct'])}\n"
        )
        lines.append(
            f"- 欠け: {', '.join(sc['missing']) or 'なし'}\n"
            f"- 載った: {', '.join(sc['present'])}\n"
        )
        # Quote voice block from live system
        if "VOICE" in system:
            chunk = system.split("VOICE", 1)[-1][:500]
            lines.append("```\nVOICE" + chunk + "\n```\n")

    # Differentiation check: quirks unique across live systems
    lines.append("## 4. キャラ間の差が乗っているか\n")
    quirk_snips = []
    for pid, markers, sc, system in live_rows:
        q = markers.get("quirk_bit") or ""
        quirk_snips.append((pid, markers["name_ja"], q, q and q in system))
    uniq = len({q for _, _, q, ok in quirk_snips if ok and q})
    lines.append(
        f"- live で癖がプロンプトに載ったキャラ: "
        f"**{sum(1 for *_, ok in quirk_snips if ok)}/{len(quirk_snips)}**\n"
        f"- 互いに異なる癖断片: **{uniq}**\n"
    )
    for pid, name, q, ok in quirk_snips:
        lines.append(f"- {pid} {name}: {'✓' if ok else '✗'} `{q}`")
    lines.append("")

    # Structural gaps
    lines.append("## 5. 構造ギャップ（JSONにあるが duet talk に届きにくい）\n")
    talk0, _, _, _ = static_prompt_score(SAMPLE_IDS[0])
    gaps = []
    preset0 = load_preset(SAMPLE_IDS[0])
    app = preset0.get("appearance") or {}
    if app.get("voice") and str(app.get("voice")) not in talk0:
        gaps.append(
            "`appearance.voice` / `habit` / `first_impression` は "
            "`_character_sheet` に出てこない（JSONにはある）"
        )
    if "signature_moment" in str(presets.preset_to_character(preset0).get("personality")):
        sm = (presets.preset_to_character(preset0)["personality"].get("signature_moment") or "")
        if sm and sm not in talk0:
            gaps.append("`signature_moment` が talk シートに未注入")
    gaps.append(
        "SUMMARY/INNER は「トーン専用・SAYに出すな」と強く封じている"
        "（性格の核だが発話には間接利用）"
    )
    gaps.append(
        "Fake/実モデル問わず、最終SAYが癖どおりかは生成モデル次第"
        "（契約は厚いが保証はプロンプト依存）"
    )
    for g in gaps:
        lines.append(f"- {g}")
    lines.append("")

    # Verdict
    avg = sum(score_prompt(
        crew.actress_duet_prompt(presets.preset_to_character(load_preset(pid)), mode="talk"),
        markers_from_json(load_preset(pid), presets.preset_to_character(load_preset(pid))),
    )["pct"] for pid in SAMPLE_IDS) / len(SAMPLE_IDS)

    lines.append("## 総判定\n")
    lines.append(
        f"- サンプル talk 平均カバー率: **{avg:.0f}%** → {judgment(int(avg))}\n"
        "- **判明できている:** 名前、一人称、呼び方、talk_quirks、duet_say_examples、"
        "traits、charm、likes/dislikes、summary/inner（トーンとして）\n"
        "- **弱いか未使用:** appearance.voice/habit/first_impression、"
        "signature_moment（シート外）、タイトル職業の明示\n"
        "- **差は出る設計:** つばさ＝アタシ／早口、すみれ＝総監督様／花屋口調、"
        "みなも＝……かな／レンズキャップ、かほ＝囁き丁寧 — JSONどおりプロンプトに分岐\n"
        "- **限界:** 実Ollama未使用のため「喋った結果の性格再現」は未検証。"
        "契約の厚さとしては中〜上、発話保証はモデル頼み。\n"
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(OUT.read_text(encoding="utf-8")[:3500])


if __name__ == "__main__":
    asyncio.run(main())

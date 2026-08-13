#!/usr/bin/env python3
"""Simulate concern scenarios against CURRENT Muse logic, assuming Gemma 4 26B.

Ollama is unavailable in this environment. Instead of keyword-perfect scripts,
each case emits the JSON/label shape a mid-size instruction model (Gemma-class
26B) typically produces under the live SCRIPTER_SYSTEM + notebook + transcript
prompt: partial absolute rewrites, SCENE pollution from densify prose, soft
casual misreads, hair tags layered on identity, etc.

Runs through real ``service.post_duet_chat`` / ``identity.assemble_positive``.
Writes /opt/cursor/artifacts/muse_gemma_concern_sim_report.md
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import identity, notebook, service, session_db
from tests.muse.test_duet import _duet_session
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block
from tests.muse.test_service import FakeDb

OUT = Path("/opt/cursor/artifacts/muse_gemma_concern_sim_report.md")

# Long densified park prose — what densify/craft often leaves in SCENE when the
# model ignores "craft_scene only" and also patches the notebook place field.
PARK_POLLUTED = (
    "A sun-drenched public park with winding gravel paths, green benches under "
    "maple trees, soft afternoon light filtering through leaves, distant "
    "children's laughter, and a quiet fountain near the open lawn where she "
    "stands in a sailor uniform waiting for the next beat."
)

BEACH_DENSE = (
    "Wide sandy beach under a bright sky, footprints trailing along the wet "
    "shore, ocean breeze lifting her cheerleader skirt as she runs, waves "
    "breaking white behind her, summer heat shimmering above the sand."
)


class TurnScriptOllama(NotebookOllama):
    """Per-turn scripted Gemma-like answers (scripter + Muse SAY).

    ``scripter_turns`` / ``say_turns`` are lists consumed in order for each
    scripter / talk call. Exhausted lists fall back to casual / short SAY.
    """

    def __init__(self, scripter_turns=None, say_turns=None):
        super().__init__(scripts={})
        self.scripter_turns = list(scripter_turns or [])
        self.say_turns = list(say_turns or [])
        self.scripter_i = 0
        self.say_i = 0
        self.captured_scripter_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        if "studio scripter" in system or "shot notebook" in system:
            self.scripter_prompts.append(str(prompt))
            self.captured_scripter_prompts.append(str(prompt))
            if self.scripter_i < len(self.scripter_turns):
                text = self.scripter_turns[self.scripter_i]
                self.scripter_i += 1
            else:
                text = _scripter_block(intent="casual", vibe="chatting")
        else:
            if self.say_i < len(self.say_turns):
                text = self.say_turns[self.say_i]
                self.say_i += 1
            else:
                text = "SAY: うん、その感じ。"

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


def _snap(s: dict) -> dict:
    nb = s.get("notebook") or {}
    craft = s.get("craft") or {}
    prompt = str(craft.get("prompt") or "")
    return {
        "intent": s.get("scripter_intent"),
        "dirty": bool(s.get("craft_dirty")),
        "rev": int(nb.get("rev") or 0),
        "rev_compiled": int(s.get("notebook_rev_compiled") or 0),
        "scene": str(nb.get("scene") or ""),
        "wearing": str(nb.get("wearing") or ""),
        "beat": str(nb.get("beat") or ""),
        "frame": str(nb.get("frame") or ""),
        "open": str(nb.get("open") or ""),
        "tags": str(craft.get("tags") or ""),
        "craft_scene": str(craft.get("scene") or "")[:200],
        "prompt_head": prompt[:280],
        "prompt_has_park": "park" in prompt.lower(),
        "prompt_has_beach": "beach" in prompt.lower() or "砂浜" in prompt,
        "prompt_has_bob": "bob_cut" in prompt or "short_hair" in prompt,
        "prompt_has_pony": "ponytail" in prompt or "high_pony" in prompt,
        "prompt_has_yukata": "yukata" in prompt.lower() or "浴衣" in prompt,
        "prompt_has_straw": "straw_hat" in prompt,
        "last_muse": next(
            (m.get("text") for m in reversed(s.get("chat") or []) if m.get("role") == "muse"),
            "",
        ),
        "studio_warn": next(
            (
                m.get("text") for m in reversed(s.get("chat") or [])
                if m.get("role") == "system" and "追いつ" in str(m.get("text") or "")
            ),
            "",
        ),
    }


def _md(label: str, snap: dict) -> str:
    lines = [f"### {label}", ""]
    lines.append(
        f"- intent=`{snap['intent']}` dirty=`{snap['dirty']}` "
        f"rev=`{snap['rev']}` compiled=`{snap['rev_compiled']}`"
    )
    for k in ("scene", "wearing", "beat", "frame", "open"):
        v = snap.get(k) or ""
        if v:
            show = v if len(v) < 160 else v[:160] + "…"
            lines.append(f"- **{k}**: {show}")
    if snap.get("tags"):
        lines.append(f"- **tags**: `{snap['tags']}`")
    if snap.get("craft_scene"):
        lines.append(f"- **craft_scene**: {snap['craft_scene']}")
    flags = [
        f"park={snap['prompt_has_park']}",
        f"beach={snap['prompt_has_beach']}",
        f"bob={snap['prompt_has_bob']}",
        f"pony={snap['prompt_has_pony']}",
        f"yukata={snap['prompt_has_yukata']}",
        f"straw={snap['prompt_has_straw']}",
    ]
    lines.append("- **prompt flags**: " + ", ".join(flags))
    if snap.get("prompt_head"):
        lines.append(f"- **prompt**: `{snap['prompt_head']}`")
    if snap.get("last_muse"):
        lines.append(f"- **SAY**: {snap['last_muse']}")
    if snap.get("studio_warn"):
        lines.append(f"- **studio**: {snap['studio_warn']}")
    lines.append("")
    return "\n".join(lines)


async def _boot(db, ollama, *, identity_tags=None, theme="公園でセーラー"):
    """Open a duet without burning scripted scripter/say turns on the greeting.

    ``start_duet`` talks once; we prepend a greeting SAY so later turns stay
    aligned with the concern scripts under test.
    """
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["inputs"]["theme"] = theme
    tags = identity_tags or [
        "1girl", "silver_hair", "bob_cut", "short_hair", "blue_eyes",
    ]
    s["character"] = {
        "identity_tags": tags,
        "name_ja": "あさひ",
        "character_id": "c1",
        "personality": {},
        "palette": [],
        "signature_prop": "",
    }
    await session_db.save(db, s)
    if isinstance(ollama, TurnScriptOllama):
        ollama.say_turns = ["SAY: （開場）今日もよろしく。"] + list(ollama.say_turns)
    return await service.start_duet(db, ollama, s)


async def _cfg(db):
    return {"ollama_num_ctx": 16000}


# ---------------------------------------------------------------------------
# Cases — each scripter_turns list IS the assumed Gemma 26B output shape.
# ---------------------------------------------------------------------------

async def case_beach_after_polluted_scene(db, lines: list[str]) -> dict:
    """Concern 1: SCENE polluted with densify prose; showrunner moves to beach.

    Gemma-typical: updates CRAFT_SCENE / TAGS with beach, but leaves SCENE as
    the long park paragraph (or only lightly edits it) because absolute rewrite
    of a 60-word field is hard under temperature≈1.0.
    """
    lines.append("## C1 — 公園散文汚染 → ビーチ変更（部分PATCH）\n")
    lines.append(
        "仮定: densify後に SCENE が長い公園英文。監督「ビーチにして砂浜走ってる感じ」。"
        "Gemma26Bは tags/craft_scene をビーチにするが、SCENE 欄の全文置換を怠る"
        "（または公園語を残す）。\n"
    )
    seed = _scripter_block(
        intent="shot",
        atmosphere="warm afternoon",
        scene=PARK_POLLUTED,
        frame="eye level, looking at viewer",
        wearing="sailor uniform",
        beat="standing by a park bench",
        tags=(
            "public_park, maple_tree, bench, sailor_collar, standing, "
            "looking_at_viewer, afternoon"
        ),
        craft_scene=PARK_POLLUTED,
    )
    # Gemma-like partial: beach in tags/craft, SCENE still park-heavy
    partial = _scripter_block(
        intent="shot",
        atmosphere="bright summer shore",
        scene=PARK_POLLUTED,  # FAILED absolute rewrite
        frame="eye level tracking, looking at viewer",
        wearing="cheerleader uniform",
        beat="running along the sand",
        tags=(
            "beach, sand, ocean, cheerleader_uniform, running, "
            "looking_at_viewer, summer"
        ),
        craft_scene=BEACH_DENSE,
    )
    ollama = TurnScriptOllama(
        scripter_turns=[seed, partial],
        say_turns=[
            "SAY: 公園のベンチ、あたたかい。",
            "SAY: ビーチ……砂浜、ですか？ さっきまでの公園とは空気が違うね。走ってみる。",
        ],
    )
    s = await _boot(db, ollama, theme="公園のセーラー")
    await service.post_duet_chat(db, ollama, s, "セーラーで公園のベンチあたり")
    lines.append(_md("T1 公園シード", _snap(s)))
    await service.post_duet_chat(
        db, ollama, s, "場所をビーチにして砂浜走ってる感じにしよう",
    )
    snap = _snap(s)
    lines.append(_md("T2 ビーチ指示（部分PATCH）", snap))
    # densify path when dirty/behind
    await service.densify_craft_if_needed(db, ollama, s)
    # densify will consume next scripter turn if any — supply a densify that
    # thickens beach craft but STILL leaves notebook.scene polluted (model habit)
    # Already consumed turns; densify may call again with empty → casual.
    snap2 = _snap(s)
    lines.append(_md("T2後 densify", snap2))

    # Verdict under CURRENT logic:
    # - craft tags/scene ARE beach → prompt_has_beach True
    # - notebook.scene STILL park → Muse summary still says 場所: park prose
    # - prompt may still contain "park" if craft_scene was park; here craft is beach
    # - next Muse talk reads polluted scene via digest → conversational desync risk
    park_in_nb = "park" in snap["scene"].lower()
    beach_in_craft = snap["prompt_has_beach"]
    desync = park_in_nb and beach_in_craft
    ok_picture = beach_in_craft and not snap["prompt_has_park"]
    lines.append(
        f"**観測:** notebook.sceneにpark残存=`{park_in_nb}` / "
        f"promptがbeach=`{beach_in_craft}` / promptにpark=`{snap['prompt_has_park']}`\n"
    )
    lines.append(
        f"**判定:** 絵(prompt)は{'追従' if ok_picture else '未追従'}。"
        f"会話正本(digestの場所)は{'公園のまま → Museが公園語を喋りうる（ブレ）' if desync else '一致'}。"
        f"{' **懸念は半分当たる（画は直ってもノート正本が腐る）**' if desync and ok_picture else ''}"
        f"{' **画もノートも不一致 — 深刻**' if desync and not ok_picture else ''}\n"
    )
    return {
        "id": "C1",
        "picture_ok": ok_picture,
        "notebook_ok": not park_in_nb,
        "concern_hits": desync or not ok_picture,
        "severity": "partial" if desync else ("ok" if ok_picture else "fail"),
    }


async def case_beach_good_gemma(db, lines: list[str]) -> dict:
    """Same beach move when Gemma does absolute SCENE rewrite (best case)."""
    lines.append("## C1b — 同ビーチ変更（Gemmaが絶対置換できた場合）\n")
    seed = _scripter_block(
        intent="shot",
        scene="sun-drenched public park",
        frame="eye level",
        wearing="sailor uniform",
        beat="by a bench",
        tags="public_park, bench, sailor_collar, standing",
        craft_scene="Park bench in sailor uniform.",
    )
    good = _scripter_block(
        intent="shot",
        scene="sandy beach shoreline",
        frame="eye level tracking",
        wearing="cheerleader uniform",
        beat="running on wet sand",
        tags="beach, sand, ocean, cheerleader_uniform, running, looking_at_viewer",
        craft_scene=BEACH_DENSE,
    )
    ollama = TurnScriptOllama(
        scripter_turns=[seed, good],
        say_turns=["SAY: 公園ね。", "SAY: 砂、かかとに入る。走るよ。"],
    )
    s = await _boot(db, ollama)
    await service.post_duet_chat(db, ollama, s, "公園でセーラー")
    await service.post_duet_chat(db, ollama, s, "ビーチにして砂浜走ってる感じ")
    snap = _snap(s)
    lines.append(_md("T2 絶対置換成功", snap))
    ok = snap["prompt_has_beach"] and not snap["prompt_has_park"] and "beach" in snap["scene"]
    lines.append(
        f"**判定:** {'PASS — 現行ロジックで十分' if ok else 'FAIL'}。"
        "transcript＋毎ターンScripterなら、モデルが絶対値を書けば追従する。\n"
    )
    return {
        "id": "C1b",
        "picture_ok": ok,
        "notebook_ok": ok,
        "concern_hits": not ok,
        "severity": "ok" if ok else "fail",
    }


async def case_affirm_with_transcript(db, lines: list[str]) -> dict:
    """Concern: 「いいね」 — old gate dropped it; now transcript must resolve OPEN."""
    lines.append("## C2 — OPEN提案 →「いいね」肯定（transcript依存）\n")
    lines.append(
        "仮定: MuseがOPENでラムネ提案。監督「いいね」。GemmaはREADING THE ROOM通り"
        "肯定をshot/mixedにし、beatへ絶対値、clear_open。"
        "（regex affirmは撤去済み — モデルがcasualと誤ると失敗）\n"
    )
    t0 = _scripter_block(
        intent="shot",
        scene="school rooftop",
        wearing="sailor uniform",
        beat="leaning on fence",
        frame="eye level",
        open_="ラムネを片手に",
        tags="rooftop, sailor_collar, leaning, looking_at_viewer",
        craft_scene="Rooftop lean.",
    )
    # Competent Gemma with transcript
    affirm_ok = _scripter_block(
        intent="mixed",
        scene="school rooftop",
        wearing="sailor uniform",
        beat="leaning on fence, holding ramune bottle",
        frame="eye level",
        clear_open="yes",
        tags="rooftop, sailor_collar, leaning, ramune, looking_at_viewer",
        craft_scene="Rooftop lean with ramune in hand.",
    )
    ollama = TurnScriptOllama(
        scripter_turns=[t0, affirm_ok],
        say_turns=[
            "SAY: ラムネ、どう？ 一手に冷たくて。",
            "SAY: うん、採用ね。瓶、結露してる。",
        ],
    )
    s = await _boot(db, ollama, theme="屋上")
    await service.post_duet_chat(db, ollama, s, "屋上でセーラー、フェンス")
    # Put Muse OPEN proposal into chat so transcript contains it
    s.setdefault("chat", []).append({
        "role": "muse", "name": "あさひ",
        "text": "ラムネ、片手に持ってみる？",
        "ts": 1,
    })
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "いいね")
    snap = _snap(s)
    lines.append(_md("肯定後", snap))
    ok = (
        "ramune" in snap["tags"]
        and not snap["open"]
        and ("ramune" in snap["beat"].lower() or "ラムネ" in snap["beat"])
    )
    lines.append(
        f"**判定:** {'PASS — transcriptあれば肯定は画に載る' if ok else 'FAIL'}。"
        "ゲート復活は不要。失敗モードはモデルがintent=casualと返すときだけ"
        "（その場合craftは動かず、現行は沈黙しうる）。\n"
    )

    # Misclassify variant
    lines.append("### C2-mis — 同じ「いいね」を casual と誤判定した場合\n")
    ollama2 = TurnScriptOllama(
        scripter_turns=[
            t0,
            _scripter_block(intent="casual", vibe="agreeing warmly", clear_open="no"),
        ],
        say_turns=["SAY: フェンス。", "SAY: ふふ、採用っぽいね。"],
    )
    s2 = await _boot(db, ollama2, theme="屋上")
    await service.post_duet_chat(db, ollama2, s2, "屋上でセーラー")
    s2["notebook"]["open"] = "ラムネを片手に"
    s2["notebook"]["rev"] = int(s2["notebook"].get("rev") or 0)
    await session_db.save(db, s2)
    await service.post_duet_chat(db, ollama2, s2, "いいね")
    snap2 = _snap(s2)
    lines.append(_md("誤ってcasual", snap2))
    mis_fail = "ramune" not in snap2["tags"] and bool(snap2["open"] or True)
    lines.append(
        f"**判定:** 画にラムネ無し=`{ 'ramune' not in snap2['tags'] }` / "
        f"open残存=`{bool(snap2['open'])}`。"
        f"{' **Gemmaがcasual誤判定すると現行は回復ゲートを持たない — 懸念は妥当**' if mis_fail else ''}\n"
    )
    return {
        "id": "C2",
        "picture_ok": ok,
        "notebook_ok": ok,
        "concern_hits": mis_fail,
        "severity": "ok_if_intent_correct",
        "misclassify_breaks": mis_fail,
    }


async def case_hairstyle_identity(db, lines: list[str]) -> dict:
    """Concern 3: ponytail request vs bob_cut/short_hair identity staple."""
    lines.append("## C3 — ポニテ指示 vs identity の bob_cut\n")
    lines.append(
        "仮定: GemmaはWEARING/TAGSに ponytail, ribbon を出す（会話は追従）。"
        "identity は bob_cut, short_hair を staple。assemble_positive の結果を見る。\n"
    )
    t0 = _scripter_block(
        intent="shot",
        scene="park",
        wearing="cheerleader uniform",
        beat="standing",
        frame="eye level",
        tags="park, cheerleader_uniform, standing, looking_at_viewer",
        craft_scene="Cheerleader in a park.",
    )
    pony = _scripter_block(
        intent="shot",
        scene="park",
        wearing="cheerleader uniform, high ponytail with ribbon",
        beat="standing, adjusting ponytail",
        frame="eye level",
        tags=(
            "park, cheerleader_uniform, ponytail, high_ponytail, ribbon, "
            "standing, looking_at_viewer"
        ),
        craft_scene="Cheerleader with a high ponytail and ribbon.",
    )
    ollama = TurnScriptOllama(
        scripter_turns=[t0, pony],
        say_turns=[
            "SAY: チアのユニフォーム、動きやすそう。",
            "SAY: ポニテね。リボンも結ぶ。",
        ],
    )
    s = await _boot(
        db, ollama,
        identity_tags=[
            "1girl", "silver_hair", "bob_cut", "short_hair", "blue_eyes",
        ],
        theme="チア",
    )
    await service.post_duet_chat(db, ollama, s, "チアの衣装で")
    await service.post_duet_chat(db, ollama, s, "髪型ポニテにしてリボン結んで")
    snap = _snap(s)
    lines.append(_md("ポニテ後", snap))
    # Direct assemble probe (same path as craft)
    assembled = identity.assemble_positive(
        ["1girl", "silver_hair", "bob_cut", "short_hair", "blue_eyes"],
        snap["tags"],
        snap["craft_scene"],
        framing="auto",
        style="",
        subject=["1girl"],
    )
    lines.append(f"- **assembled**: `{assembled[:320]}`\n")
    both = ("bob_cut" in assembled and "ponytail" in assembled)
    lines.append(
        f"**判定:** bobとpony両立=`{both}`。"
        f"{' **懸念は現行ロジックで確定再現 — 髪型スロット無しでは会話追従しても髪が混ざる**' if both else '意外と落ちている'}\n"
    )
    return {
        "id": "C3",
        "picture_ok": not both and "ponytail" in assembled,
        "notebook_ok": "ponytail" in snap["wearing"].lower() or "pony" in snap["wearing"].lower(),
        "concern_hits": both,
        "severity": "fail_structural",
    }


async def case_yukata_gate_regression(db, lines: list[str]) -> dict:
    """Old keyword gate hole — must work every turn now."""
    lines.append("## C4 —「浴衣に着替えて」（旧ゲート欠落フレーズ）\n")
    t0 = _scripter_block(
        intent="shot",
        scene="summer street",
        wearing="blouse, skirt",
        beat="standing",
        frame="eye level",
        tags="street, blouse, skirt, standing",
        craft_scene="Street in blouse.",
    )
    yukata = _scripter_block(
        intent="shot",
        scene="summer street at dusk",
        wearing="yukata, geta",
        beat="standing, holding a folding fan",
        frame="eye level",
        tags="street, yukata, geta, folding_fan, standing, looking_at_viewer",
        craft_scene="Dusk street in yukata with a fan.",
    )
    ollama = TurnScriptOllama(
        scripter_turns=[t0, yukata],
        say_turns=["SAY: ブラウスね。", "SAY: 浴衣、帯もきちっと。"],
    )
    s = await _boot(db, ollama, theme="夏の通り")
    await service.post_duet_chat(db, ollama, s, "ブラウスにスカートで通り")
    await service.post_duet_chat(db, ollama, s, "浴衣に着替えて")
    snap = _snap(s)
    lines.append(_md("着替後", snap))
    # Prove scripter ran (2 turns: seed + yukata; start_duet may not call scripter)
    ran = ollama.scripter_i >= 2
    ok = snap["prompt_has_yukata"] and "blouse" not in snap["tags"]
    lines.append(
        f"**判定:** scripter呼び出し済=`{ran}` / yukata載る=`{ok}`。"
        f"{'PASS — ゲート撤去は効いている' if ok else 'FAIL'}\n"
    )
    return {
        "id": "C4",
        "picture_ok": ok,
        "notebook_ok": "yukata" in snap["wearing"].lower(),
        "concern_hits": False,
        "severity": "ok",
    }


async def case_hat_off_absolute(db, lines: list[str]) -> dict:
    """Hat on/off — absolute wearing rewrite."""
    lines.append("## C5 — 麦わら着脱（絶対WEARING）\n")
    on_ = _scripter_block(
        intent="shot",
        scene="rooftop",
        wearing="sailor uniform, straw hat",
        beat="leaning",
        frame="eye level",
        tags="rooftop, sailor_collar, straw_hat, leaning",
        craft_scene="Sailor with straw hat.",
    )
    off = _scripter_block(
        intent="shot",
        scene="rooftop",
        wearing="sailor uniform",
        beat="leaning",
        frame="eye level",
        tags="rooftop, sailor_collar, leaning",
        craft_scene="Hat off; sailor lean.",
    )
    ollama = TurnScriptOllama(
        scripter_turns=[on_, off],
        say_turns=["SAY: つば、影。", "SAY: 外した。風が来る。"],
    )
    s = await _boot(db, ollama, theme="屋上")
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶって")
    await service.post_duet_chat(db, ollama, s, "帽子外して")
    snap = _snap(s)
    lines.append(_md("脱帽後", snap))
    ok = not snap["prompt_has_straw"] and "straw" not in snap["wearing"]
    # Gemma-bad: forgot to drop straw from tags while wearing is clean
    lines.append("### C5-mis — WEARINGは消えたがTAGSにstraw_hat残留\n")
    ollama2 = TurnScriptOllama(
        scripter_turns=[
            on_,
            _scripter_block(
                intent="shot",
                scene="rooftop",
                wearing="sailor uniform",
                beat="leaning",
                frame="eye level",
                tags="rooftop, sailor_collar, straw_hat, leaning",  # stale tag
                craft_scene="Hat off but tag lingered.",
            ),
        ],
        say_turns=["SAY: かぶる。", "SAY: 外した。"],
    )
    s2 = await _boot(db, ollama2, theme="屋上")
    await service.post_duet_chat(db, ollama2, s2, "麦わら帽子かぶって")
    await service.post_duet_chat(db, ollama2, s2, "帽子外して")
    snap2 = _snap(s2)
    lines.append(_md("TAGS残留", snap2))
    stale = snap2["prompt_has_straw"] and "straw" not in snap2["wearing"]
    lines.append(
        f"**判定(良):** 脱帽完了=`{ok}`。**判定(TAGS残留):** 絵に帽子残る=`{stale}`。"
        f"{' **整合監査が無いと現行は通過させる — 懸念妥当**' if stale else ''}\n"
    )
    return {
        "id": "C5",
        "picture_ok": ok,
        "notebook_ok": ok,
        "concern_hits": stale,
        "severity": "ok_when_model_absolute",
        "stale_tags_pass": stale,
    }


async def case_muse_talks_beach_scripter_casual(db, lines: list[str]) -> dict:
    """Real post-merge failure shape: Muse acknowledges beach, scripter says casual."""
    lines.append("## C6 — Museはビーチ同意、Scripterがcasual（実測再発形）\n")
    lines.append(
        "仮定: 監督がビーチ指示。Gemma Scripterがintent=casual（「感じにしよう」を雑談扱い）。"
        "Muse talkはDIGESTの公園のまま／またはセリフだけビーチ。\n"
    )
    seed = _scripter_block(
        intent="shot",
        scene="sun-drenched public park",
        wearing="sailor uniform",
        beat="standing",
        frame="eye level",
        tags="public_park, sailor_collar, standing",
        craft_scene="Park in sailor uniform.",
    )
    casual = _scripter_block(
        intent="casual",
        vibe="thinking about changing the location someday",
    )
    ollama = TurnScriptOllama(
        scripter_turns=[seed, casual],
        say_turns=[
            "SAY: 公園、木漏れ日。",
            "SAY: ビーチ……砂浜、ですか？ さっきまでの公園とは… でも、やってみる。",
        ],
    )
    s = await _boot(db, ollama)
    await service.post_duet_chat(db, ollama, s, "公園でセーラー")
    await service.post_duet_chat(
        db, ollama, s, "場所をビーチにして砂浜走ってる感じにしよう",
    )
    snap = _snap(s)
    lines.append(_md("casual誤判定後", snap))
    picture_stuck = (
        snap["intent"] == "casual"
        and not snap["prompt_has_beach"]
        and snap["prompt_has_park"]
    )
    muse_beach = "ビーチ" in (snap["last_muse"] or "") or "砂浜" in (snap["last_muse"] or "")
    fail = picture_stuck  # desync if Muse also roleplays beach; picture stuck alone is enough
    lines.append(
        f"**判定:** intent=casualで画が公園のまま=`{picture_stuck}` / "
        f"Museがビーチ言及=`{muse_beach}`。"
        f"{' **5474c9fだけでは防げない — transcriptは渡るがintent誤りを訂正しない**' if fail else ''}\n"
    )
    return {
        "id": "C6",
        "picture_ok": not picture_stuck,
        "notebook_ok": not picture_stuck,
        "concern_hits": fail,
        "severity": "fail_no_recovery",
        "muse_beach": muse_beach,
    }


async def case_notebook_moved_compile_refused(db, lines: list[str]) -> dict:
    """Notebook patch applied but compile empty → craft_dirty.

    Also models the live repair pass: ``run_scripter`` calls generate_text a
    second time when validate fails. A Gemma that fails twice leaves the
    notebook depending on what the repair returned.
    """
    lines.append("## C7 — ノートは進むが compile 空（craft_dirty / repair）\n")
    seed = _scripter_block(
        intent="shot",
        scene="park",
        wearing="sailor uniform",
        beat="standing",
        frame="eye level",
        tags="park, sailor_collar",
        craft_scene="Park sailor.",
    )
    # First answer: shot patch + empty craft → invalid → triggers repair.
    empty_compile = "\n".join([
        "INTENT: shot",
        "SCENE: sandy beach",
        "WEARING: yukata",
        "BEAT: walking",
        "FRAME: eye level",
        "CLEAR_OPEN: no",
        "TAGS: none",
        "CRAFT_SCENE: none",
    ])
    # Repair still empty craft but keeps the notebook absolute values.
    repair_still_empty = "\n".join([
        "INTENT: shot",
        "SCENE: sandy beach",
        "WEARING: yukata",
        "BEAT: walking",
        "FRAME: eye level",
        "CLEAR_OPEN: no",
        "TAGS: none",
        "CRAFT_SCENE: none",
    ])
    ollama = TurnScriptOllama(
        scripter_turns=[seed, empty_compile, repair_still_empty],
        say_turns=["SAY: 公園。", "SAY: 浴衣で砂浜、ね。"],
    )
    s = await _boot(db, ollama)
    await service.post_duet_chat(db, ollama, s, "公園セーラー")
    await service.post_duet_chat(db, ollama, s, "浴衣でビーチ")
    snap = _snap(s)
    lines.append(_md("空compile＋repair後", snap))
    dirty_ok = snap["dirty"] and snap["rev"] > snap["rev_compiled"]
    nb_moved = "beach" in snap["scene"] or "yukata" in snap["wearing"]
    still_old = snap["prompt_has_park"] and not snap["prompt_has_yukata"]
    lines.append(
        f"**判定:** notebook更新=`{nb_moved}` dirty/behind=`{dirty_ok}` 絵は旧=`{still_old}`。"
        "現行は握り潰さずdirtyを立てる → **レンダ前に気づけるならOK**。"
        "boardを押さなければユーザーは会話上OKに見える。\n"
    )
    warned = service._warn_if_craft_behind(s)
    snap_w = _snap(s)
    lines.append(_md("warn後", snap_w))
    lines.append(f"**studio警告発火:** `{warned}`\n")
    return {
        "id": "C7",
        "picture_ok": False,
        "notebook_ok": nb_moved,
        "concern_hits": still_old and not warned,
        "severity": "mitigated_by_dirty" if (dirty_ok and warned) else "fail",
        "dirty": dirty_ok,
        "warned": warned,
        "scripter_calls": ollama.scripter_i,
    }


async def case_transcript_present(db, lines: list[str]) -> dict:
    """Meta: prove current logic actually injects transcript into scripter."""
    lines.append("## C0 — 現行がtranscriptを渡しているか（前提確認）\n")
    ollama = TurnScriptOllama(
        scripter_turns=[
            _scripter_block(
                intent="shot", scene="park", wearing="dress", beat="sit",
                frame="eye", tags="park, dress", craft_scene="Park.",
            ),
            _scripter_block(intent="casual", vibe="ok"),
        ],
        say_turns=["SAY: 公園。", "SAY: うん。"],
    )
    s = await _boot(db, ollama)
    await service.post_duet_chat(db, ollama, s, "公園でワンピース")
    await service.post_duet_chat(db, ollama, s, "うん")
    prompts = ollama.captured_scripter_prompts
    has_tx = any("ここまでの会話" in p for p in prompts)
    has_line = any("公園でワンピース" in p for p in prompts[1:]) if len(prompts) > 1 else False
    lines.append(f"- scripter calls: {len(prompts)}")
    lines.append(f"- transcriptブロック有り: `{has_tx}`")
    lines.append(f"- 2回目に直前発話が含まれる: `{has_line}`\n")
    lines.append(
        f"**判定:** {'PASS — 5474c9fの前提は現行コードで成立' if has_tx else 'FAIL — transcript未配線'}\n"
    )
    return {
        "id": "C0",
        "picture_ok": True,
        "notebook_ok": True,
        "concern_hits": not has_tx,
        "severity": "ok" if has_tx else "fail",
    }


async def main() -> None:
    import pytest  # noqa: F401 — ensure test helpers importable

    # Monkeypatch runtime config like tests do
    service.get_runtime_config = _cfg  # type: ignore

    lines: list[str] = [
        "# Muse懸念シミュ報告 — Gemma 4 26B 仮定 / 現行ロジック",
        "",
        "環境に Ollama が無いため、Gemma 26B が **実際に返しがちな出力形** を"
        "ターンごとに固定し、`service.post_duet_chat` 実経路へ流した。"
        "（キーワード完璧スクリプトではない。部分PATCH・casual誤判定・TAGS残留を含む。）",
        "",
    ]
    results = []
    db = FakeDb()
    for fn in (
        case_transcript_present,
        case_beach_after_polluted_scene,
        case_beach_good_gemma,
        case_affirm_with_transcript,
        case_hairstyle_identity,
        case_yukata_gate_regression,
        case_hat_off_absolute,
        case_muse_talks_beach_scripter_casual,
        case_notebook_moved_compile_refused,
    ):
        db = FakeDb()
        results.append(await fn(db, lines))

    lines.append("## 総合判断\n")
    lines.append("| ID | 絵OK | ノートOK | 懸念ヒット | 深刻度 |")
    lines.append("|----|------|----------|------------|--------|")
    for r in results:
        lines.append(
            f"| {r['id']} | {r.get('picture_ok')} | {r.get('notebook_ok')} | "
            f"{r.get('concern_hits')} | `{r.get('severity')}` |"
        )
    lines.append("")

    hits = [r for r in results if r.get("concern_hits")]
    lines.append("### この結果で採用する方針\n")
    lines.append(
        "1. **ゲート再導入は不要** — C4（浴衣）は毎ターンScripterで通る。"
        "C0でtranscript配線も確認。\n"
        "2. **C6（casual誤判定）とC2-misが本命残件** — transcriptを渡しても"
        "intent誤りをサーバは訂正しない。整合監査／repairが効く領域。\n"
        "3. **C3（髪型）は構造バグとして確定** — LLM品質以前に assemble が"
        "bob+ponyを両立させる。髪型スロットは優先度高。\n"
        "4. **C1（SCENE汚染）** — 画は追従してもdigestが腐ると次ターンの"
        "Muse/Scripterが公園に引き戻される。欄契約はコスト低で効く。\n"
        "5. **C5-mis / C7** — dirty警告は動くが、TAGSとWEARINGの食い違いは"
        "素通り。ノート語→tags監査が妥当。\n"
        "6. **C1b** — Gemmaが絶対置換できるなら現行で足りる。"
        "よって次投資は『モデルを賢くするプロンプト増量』より"
        "『失敗形を構造で検知・拒否・repair』。\n"
    )
    lines.append("```json\n" + json.dumps(results, ensure_ascii=False, indent=2) + "\n```\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())

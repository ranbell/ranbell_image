"""Chat studio wiring: table → note → board → OK → shoot.

BCD refine is gone. The showrunner chats; the crew revises craft; a board asks
「これでいい？」; OK submits the final shoot.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import runner, shared


class FakeSpooler:
    def __init__(self):
        self.jobs: list[dict] = []
        self.cancelled: list[str] = []

    def submit(self, lane, title, func, meta=None, **kw):
        self.jobs.append({"lane": lane, "title": title, "func": func,
                          "meta": meta or {}, **kw})
        return f"job-{len(self.jobs)}"

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        return True


class FakeComfy:
    def load_workflow(self, name):
        return {}

    def patchable_fields(self, wf):
        return {"steps": 1, "cfg": 1, "width": 1, "height": 1}


class FakeOllama:
    def __init__(self):
        self.unloaded: list[str] = []
        self.calls: list[dict] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        # Match the packed table prompt only — craft seats mention
        # "RECENT TABLE TALK" in their system text and must stay craft-shaped.
        if "LIVE TABLE on a photo shoot" in system:
            text = (
                "SPEAKER: wardrobe\n"
                "SAY: 衣装、いまのまま通します。\n\n"
                "SPEAKER: beat\n"
                "SAY: 衣装さん、その裾なら一拍だけ止めたいです。\n\n"
                "SPEAKER: lens\n"
                "SAY: 画角はそのまま寄せます。"
            )
        elif "ASIDE:" in system:
            # The Lead's own turn (`_duet_talk`) — SAY + 独り言 + CARD.
            text = (
                "SAY: はい、そうしますね。\n\n"
                "ASIDE: ……ちょっと、どきどきする。\n\n"
                "CARD:\nBEAT: sitting, hands on knees\n"
            )
        else:
            text = (
                "SAY: Director, the beat is locked.\n\n"
                "TAGS: standing, indoor\n\n"
                "SCENE: STAGE A PROMPT"
            )

        async def _stream():
            yield {"type": "think", "text": "deliberating"}
            yield {"type": "token", "text": text}
        return _stream()

    async def generate_text(self, prompt, **kw):
        """Non-stream helper used by the duet scripter (fmt/schema ignored)."""
        kw.pop("fmt", None)
        chunks: list[str] = []
        async for event in self.generate_text_stream(prompt, **kw):
            if event.get("type") == "token":
                chunks.append(str(event.get("text") or ""))
        return "".join(chunks)

    def generate_vlm_stream(self, prompt, images, **kw):
        return self.generate_text_stream(prompt, **kw)

    async def embed(self, text, model=None):
        # Deterministic tiny vector for muse_memories tests.
        return [float((sum(ord(c) for c in str(text)) % 97) + 1)] * 8

    async def unload(self, model=None):
        self.unloaded.append(model)


class FakeDb:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._qc = self

    async def upsert(self, collection_name, points):
        for p in points:
            self.rows[str(p.id)] = dict(p.payload)

    async def retrieve(self, collection_name, ids, with_payload=True):
        class _P:
            def __init__(self, payload):
                self.payload = payload
                self.id = payload["session_id"]
        return [_P(self.rows[i]) for i in ids if i in self.rows]


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)






def test_the_workflows_last_image_is_the_one_worth_keeping():
    assert runner.finished_image(["raw", "upscaled"]) == "upscaled"
    assert runner.finished_image(["only"]) == "only"






@pytest.fixture
def board_file(tmp_path):
    from PIL import Image
    p = tmp_path / "board.png"
    Image.new("RGB", (896, 1152), (40, 60, 90)).save(p)
    return str(p)


def test_every_crew_is_a_working_studio():
    """班は味で違うべきで、機能の有無で違うべきではない。

    5班に構成席が無く、台帳（MUST APPEAR）も PLACE/HOUR/LIGHT の決めも丸ごと
    欠けていた。速さのための小さい班（trio/quartet）は、会話をパックにした時点で
    「席数がコール数に効かない」ので理由を失った。
    """
    from app.muse import crew
    assert set(crew.PRESETS) == {
        "standard", "vivid", "photoreal", "flat", "bold", "calm",
    }
    looks = {}
    for name in crew.PRESETS:
        ids = crew.resolve_crew(preset=name)
        roles = [crew.role_of(i) for i in ids]
        assert "plan" in roles, f"{name} に構成席が無い"
        assert roles[-1] == "finisher" and "actress" in roles
        looks[name] = crew.base_style_for(ids, "", "")
    # 6班6様 — 同じ look の班を2つ置かない（それは選択肢ではない）
    assert len(set(looks.values())) == len(looks), looks
    # 消した名前を渡しても壊れない（既存セッションの互換）
    assert crew.resolve_crew(preset="trio") == crew.resolve_crew(preset="standard")





"""Fakes for the Weave API e2e tests.

Everything under ``app.weave`` runs for real — only the process boundaries are
faked: Qdrant (``db._qc``), the LLM gateway, the spooler and Comfy.
"""
from __future__ import annotations

import copy
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))


# ── Qdrant ────────────────────────────────────────────────────────────────────
class FakeQdrant:
    """Minimal async Qdrant surface used by weave.session_db / story.db."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict]] = defaultdict(dict)

    async def upsert(self, *, collection_name: str, points: list) -> None:
        for p in points:
            self.collections[collection_name][str(p.id)] = copy.deepcopy(p.payload or {})

    async def retrieve(self, *, collection_name: str, ids: list, with_payload: bool = True):
        out = []
        for i in ids:
            payload = self.collections[collection_name].get(str(i))
            if payload is None:
                continue
            # Deep copy: a real store never hands back a live reference, so a
            # handler that mutates without saving must fail the test.
            out.append(SimpleNamespace(id=str(i), payload=copy.deepcopy(payload)))
        return out

    async def set_payload(self, *, collection_name: str, payload: dict, points: Any) -> None:
        ids = getattr(points, "points", points)
        for i in ids:
            cur = self.collections[collection_name].setdefault(str(i), {})
            cur.update(copy.deepcopy(payload))

    async def scroll(
        self, *, collection_name: str, limit: int = 50,
        with_payload: bool = True, order_by: Any = None, **kw,
    ):
        rows = [
            SimpleNamespace(id=k, payload=copy.deepcopy(v))
            for k, v in self.collections[collection_name].items()
        ]
        key = getattr(order_by, "key", None)
        if key:
            rows.sort(key=lambda r: r.payload.get(key) or 0, reverse=True)
        return rows[:limit], None


class FakeDb:
    """Image store + config, with a fake Qdrant client underneath."""

    def __init__(self) -> None:
        self._qc = FakeQdrant()
        self.images: dict[str, dict] = {}

    def add_image(self, image_id: str, *, wd14_tags: list[str] | None = None, path: str = "") -> str:
        self.images[image_id] = {"wd14_tags": list(wd14_tags or []), "path": path}
        return image_id

    async def get(self, image_id: str) -> dict | None:
        return self.images.get(image_id)

    async def set_payload(self, image_id: str, payload: dict) -> None:
        self.images.setdefault(image_id, {}).update(payload)

    async def get_config(self) -> dict:
        return {}

    def stories(self) -> list[dict]:
        return list(self._qc.collections["stories"].values())


# ── LLM ───────────────────────────────────────────────────────────────────────
PERSONALITY_JSON = {
    "personality": {
        "traits": ["cautious", "dry_humor"],
        "social_style": "observant_listener",
        "summary_ja": "慎重で皮肉屋だが、困っている客は放っておけない店員",
    },
    "visual_inference": {
        "reasoning_ja": "慎重さは落ち着いた色味と整えた髪に出る",
        # cloth_bookmark is deliberately misplaced in identity_tags —
        # the split enforcement must move it to prop_tags.
        "identity_tags": [
            "1girl", "brown_hair", "low_ponytail", "hazel_eyes",
            "cardigan", "simple_shirt", "long_skirt", "cloth_bookmark",
        ],
        "prop_tags": ["cloth_bookmark"],
        "signature_prop": "cloth_bookmark",
        "palette": ["muted_olive", "warm_cream"],
        "do_not": ["gyaru", "heavy_armor"],
    },
    "board_briefs": [
        {"slot": "portrait", "camera": "close_up", "purpose": "face_lock"},
        {"slot": "full", "camera": "long_shot", "purpose": "silhouette_outfit"},
        {"slot": "prop", "camera": "medium_shot", "purpose": "signature_prop"},
    ],
}


def story_json(*, title: str = "しおりの雨", setting: str = "rainy bookstore") -> dict:
    return {
        "title": title,
        "world": {
            "setting": setting,
            "core_conflict": "客の忘れ物を返せないまま雨が強くなる",
            "ending_intent": "quiet hope",
            "throughline_place": "bookstore",
            "throughline_prop": "cloth_bookmark",
            "time_scale": "hours",
            "causality_one_liner": "雨が降る → しおりが濡れる → 店員が拭いて棚に戻す",
        },
        "panels": [
            {
                "key": "panel_1", "beat": "setup",
                "narrative_ja": "店員が棚の上のしおりに気づく",
                "narrative_en": "the clerk notices a cloth bookmark on the shelf",
                "visible_change": "しおりが棚の上に置かれている",
                "camera": "long_shot", "gesture": "standing",
                "focus": "cloth_bookmark", "time_marker": "afternoon",
                "emotion": "curious",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_2", "beat": "turn",
                "narrative_ja": "雨漏りの雫が棚に落ちる",
                "narrative_en": "a leak drips onto the shelf",
                "visible_change": "棚の木目に水滴が広がる",
                "camera": "medium_shot", "gesture": "reaching",
                "focus": "cloth_bookmark", "time_marker": "afternoon",
                "emotion": "alarmed",
                "must_show": ["throughline_prop", "throughline_place"],
            },
            {
                "key": "panel_3", "beat": "settle",
                "narrative_ja": "しおりの染めがにじむ",
                "narrative_en": "the dye of the bookmark bleeds",
                "visible_change": "しおりの端の色がにじむ",
                "camera": "close_up", "gesture": "holding",
                "focus": "cloth_bookmark", "time_marker": "evening",
                "emotion": "soft smile",
                "must_show": ["throughline_prop", "throughline_place"],
            },
        ],
    }


class FakeLLM:
    """Routes by prompt content; records every call for LLM-budget assertions."""

    def __init__(self, *, story: dict | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.bound: list[str] = []
        self.story = story or story_json()
        # broken_story: Storywright emits a bundle that fails lint.
        # repair_fixes: the Repairer turns it back into a clean bundle.
        self.broken_story = False
        self.repair_fixes = True

    def bind(self, provider: str):
        self.bound.append(provider)
        return self

    def _kind(self, prompt: str) -> str:
        if "You repair a StoryBundle" in prompt:
            return "repairer"
        if "story lint critic" in prompt:
            return "critic"
        if '"personality_text"' in prompt:
            return "personalitywright"
        return "storywright"

    async def chat_text(self, prompt, *, model="", options=None, fmt=None, think=False, **kw):
        kind = self._kind(prompt)
        self.calls.append({"kind": kind, "model": model, "options": options})
        if kind == "personalitywright":
            return json.dumps(PERSONALITY_JSON, ensure_ascii=False)
        if kind == "critic":
            return json.dumps({
                "summary_ja": "因果が繋がっていない",
                "priority_defects": [],
                "recreate_hint": "unclear_story",
            })
        if kind == "repairer":
            if self.repair_fixes:
                return json.dumps(self.story, ensure_ascii=False)
            return json.dumps(self._broken(), ensure_ascii=False)
        if self.broken_story:
            return json.dumps(self._broken(), ensure_ascii=False)
        return json.dumps(self.story, ensure_ascii=False)

    def _broken(self) -> dict:
        broken = copy.deepcopy(self.story)
        broken["world"]["causality_one_liner"] = ""
        for p in broken["panels"]:
            p["visible_change"] = ""
        return broken

    def count(self, kind: str) -> int:
        return sum(1 for c in self.calls if c["kind"] == kind)


# ── Spooler / Comfy ───────────────────────────────────────────────────────────
class FakeSpooler:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def submit(self, lane, title, func, meta=None, *, priority: int = 0, **kwargs) -> str:
        job_id = f"{getattr(lane, 'value', lane)}-{len(self.jobs) + 1:06d}"
        self.jobs.append({
            "job_id": job_id,
            "lane": lane,
            "title": title,
            "func": getattr(func, "__name__", str(func)),
            "meta": meta or {},
            "kwargs": kwargs,
        })
        return job_id

    def by_title(self, title: str) -> list[dict[str, Any]]:
        return [j for j in self.jobs if j["title"] == title]


class FakeComfy:
    def list_workflows(self) -> list[str]:
        return ["weave_test.json"]

    async def is_available(self) -> bool:
        return True


# ── App ───────────────────────────────────────────────────────────────────────
def build_app(*, llm: FakeLLM | None = None, db: FakeDb | None = None):
    """FastAPI app carrying only the weave router + faked app.state."""
    from fastapi import FastAPI

    from app.weave.api import router

    app = FastAPI()
    app.include_router(router)
    app.state.db = db or FakeDb()
    app.state.ollama = llm or FakeLLM()
    app.state.spooler = FakeSpooler()
    app.state.comfy = FakeComfy()
    return app


def client_for(app):
    import httpx

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://weave.test",
    )

"""Tests for Chronicle agent catalog builder."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.catalog import build_chronicle_catalog


def _make_app(
    *,
    workflows: list[str] | None = None,
    ollama_models: list[str] | None = None,
    openai_models: list[str] | None = None,
):
    comfy = SimpleNamespace(
        list_workflows=lambda: list(workflows or []),
        is_available=AsyncMock(return_value=True),
    )
    ollama = SimpleNamespace(
        health_ollama=AsyncMock(return_value=True),
        list_ollama_models=AsyncMock(return_value=list(ollama_models or [])),
        health_openai=AsyncMock(return_value=bool(openai_models)),
        list_openai_models=AsyncMock(return_value=list(openai_models or [])),
    )
    return SimpleNamespace(state=SimpleNamespace(db=object(), ollama=ollama, comfy=comfy))


def test_build_chronicle_catalog_suggested_run():
    app = _make_app(
        workflows=["a.json", "b.json"],
        ollama_models=["gemma3:12b", "gemma4:e2b"],
    )

    async def fake_cfg(_db):
        return {
            "llm_provider": "ollama",
            "ollama_url": "http://ollama:11434",
            "openai_base_url": "http://openai:8000/v1",
            "openai_model": "bonsai",
            "vlm_model": "gemma4:e2b",
            "story_model": "gemma3:12b",
            "utility_model": "",
            "embed_model": "nomic-embed-text",
            "ollama_num_ctx": 32768,
        }

    async def fake_authors(_db):
        return [
            {
                "id": "1",
                "name": "Slice",
                "genre_tag": "日常",
                "style_description": "soft",
            }
        ]

    cat = asyncio.run(
        build_chronicle_catalog(
            app,
            get_runtime_config_fn=fake_cfg,
            list_authors_fn=fake_authors,
            comfy_url="http://comfy:8188",
        )
    )
    assert cat["ok"] is True
    assert cat["comfyui"]["workflows"] == ["a.json", "b.json"]
    assert cat["comfyui"]["url"] == "http://comfy:8188"
    assert "gemma3:12b" in cat["llm"]["ollama"]["models"]
    assert cat["authors"][0]["name"] == "Slice"
    sug = cat["suggested_run"]
    assert sug["workflow_name"] == "a.json"
    assert sug["story_model"] == "gemma3:12b"
    assert sug["story_model_available"] is True
    assert "run" in cat["endpoints"]
    assert "catalog" in cat["endpoints"]
    assert "days" in cat["time_scales"]


def test_build_chronicle_catalog_openai_provider():
    app = _make_app(workflows=["wf.json"], ollama_models=[], openai_models=["bonsai", "other"])

    async def fake_cfg(_db):
        return {
            "llm_provider": "openai",
            "ollama_url": "",
            "openai_base_url": "http://openai:8000/v1",
            "openai_model": "bonsai",
            "vlm_model": "",
            "story_model": "",
            "utility_model": "",
            "embed_model": "",
            "ollama_num_ctx": 8192,
        }

    async def fake_authors(_db):
        return []

    cat = asyncio.run(
        build_chronicle_catalog(
            app,
            get_runtime_config_fn=fake_cfg,
            list_authors_fn=fake_authors,
            comfy_url="http://comfy",
        )
    )
    assert cat["suggested_run"]["llm_provider"] == "openai"
    assert cat["suggested_run"]["story_model"] == "bonsai"
    assert cat["llm"]["openai"]["models"] == ["bonsai", "other"]
    assert cat["suggested_run"]["num_ctx"] == 8192

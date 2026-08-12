"""Direction stills on Muse chat (pose-coaching → VLM)."""
from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

from backend.app.muse import service
from backend.app.muse import chain


def _tiny_jpeg_b64() -> str:
    buf = BytesIO()
    Image.new("RGB", (48, 48), (180, 90, 40)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_decode_chat_images_accepts_raw_and_data_uri():
    b64 = _tiny_jpeg_b64()
    a = service.decode_chat_images([b64])
    b = service.decode_chat_images([f"data:image/jpeg;base64,{b64}"])
    assert len(a) == 1 and len(b) == 1
    assert a[0][:2] == b"\xff\xd8"
    assert service.decode_chat_images(["not-base64!!!"]) == []
    assert service.decode_chat_images(None) == []
    assert len(service.decode_chat_images([b64, b64], max_n=1)) == 1


def test_needs_scripter_lap_pillow_short_line():
    session = {"notebook": {"rev": 1, "scene": "park", "beat": "standing"}}
    assert service._needs_scripter(session, "膝枕でこんな感じ") is True
    assert service._needs_scripter(session, "かき氷なら何味？") is False


@pytest.mark.asyncio
async def test_run_scripter_passes_direction_image(monkeypatch):
    seen = {"images": None, "prompt": ""}

    async def fake_call(ollama, *, system, prompt, model, images, num_ctx, think, on_token):
        seen["images"] = images
        seen["prompt"] = prompt
        return (
            '{"intent":"shot","patch":{"beat":"lap pillow","frame":"from the side"},'
            '"tags":"2girls, lap_pillow, from_side","craft_scene":"room"}'
        )

    monkeypatch.setattr(chain, "_call", fake_call)

    class EmptyOllama:
        pass

    result = await chain.run_scripter(
        EmptyOllama(),
        notebook_block="beat: standing",
        note="膝枕でこんな感じ",
        partner=True,
        model="vlm-test",
        num_ctx=2048,
        images=[b"fake-jpeg-bytes"],
    )
    assert seen["images"] == [b"fake-jpeg-bytes"]
    assert "DIRECTION SKETCH" in seen["prompt"]
    assert result.get("intent") == "shot" or result.get("valid") is not None

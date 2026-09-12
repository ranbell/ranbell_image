"""Direction stills are switched off in this version.

The VRM on-set preview used to capture a frame, attach it to the chat turn, and
ride through to the scripter as a VLM image and to Comfy as an OpenPose /
DWPose reference. The whole path is gated on `runner.DIRECTION_STILL_ENABLED`
now. These tests hold that gate shut — flip the flag and the mount in
`MusePanel.vue` back together to re-enable it.

`decode_chat_images` itself stays live: the endpoints still accept an images
payload so older clients do not start getting 400s.
"""
from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from backend.app.muse import runner


def _tiny_jpeg_b64() -> str:
    buf = BytesIO()
    Image.new("RGB", (48, 48), (180, 90, 40)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def test_direction_stills_are_off():
    assert runner.DIRECTION_STILL_ENABLED is False


def test_storing_a_direction_still_is_a_no_op():
    session: dict = {}
    runner.store_direction_still(session, b"\xff\xd8fake-jpeg-bytes")
    assert session.get("direction_still") in (None, {})


def test_no_reference_image_reaches_the_render():
    """Even a session carrying an old still hands Comfy nothing."""
    session = {"direction_still": {
        "jpeg_b64": _tiny_jpeg_b64(), "at": 0.0, "bytes": 1,
    }}
    assert runner.direction_still_bytes(session) is None



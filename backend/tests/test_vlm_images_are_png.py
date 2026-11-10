"""A WebP handed to a VLM goes out as PNG; PNG and JPEG go out untouched."""
import asyncio
import io
import sys
from pathlib import Path

from PIL import Image

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.ai.vlm_image import to_vlm_image, to_vlm_images


def _bytes(fmt: str, mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (8, 8), (10, 20, 30, 128)[: len(mode)]).save(buf, format=fmt)
    return buf.getvalue()


def test_a_webp_becomes_png():
    out = to_vlm_image(_bytes("WEBP"))
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (8, 8)


def test_a_webp_keeps_its_alpha():
    out = to_vlm_image(_bytes("WEBP", "RGBA"))
    with Image.open(io.BytesIO(out)) as img:
        assert img.mode == "RGBA"


def test_png_and_jpeg_pass_through():
    for fmt in ("PNG", "JPEG"):
        data = _bytes(fmt)
        assert to_vlm_image(data) is data


def test_unreadable_bytes_are_left_alone():
    assert to_vlm_image(b"not an image") == b"not an image"


def test_the_list_form_converts_each():
    webp, png = _bytes("WEBP"), _bytes("PNG")
    out = asyncio.run(to_vlm_images([webp, png]))
    assert out[0][:8] == b"\x89PNG\r\n\x1a\n" and out[1] is png

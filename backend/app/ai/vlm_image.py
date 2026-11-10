"""Images handed to a VLM go as PNG or JPEG, never WebP.

Some vision models (and some OpenAI-compatible servers in front of them) fail
on WebP — sometimes with an error, sometimes by quietly answering as if no
image had been sent. PNG and JPEG are read everywhere, so a WebP is re-encoded
as PNG (lossless, keeps alpha) before it leaves the backend; PNG and JPEG pass
through untouched.
"""
import asyncio
import io

from PIL import Image

from ..scanner.walk import PIL_FORMATS

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _is_png_or_jpeg(data: bytes) -> bool:
    return data[:8] == _PNG_MAGIC or data[:3] == _JPEG_MAGIC


def to_vlm_image(data: bytes) -> bytes:
    """Return ``data`` as PNG unless it is already PNG or JPEG.

    An animated WebP becomes its first frame. Bytes Pillow cannot read are
    returned as they came, so the server — not this helper — reports them.
    """
    if _is_png_or_jpeg(data):
        return data
    try:
        with Image.open(io.BytesIO(data), formats=PIL_FORMATS) as img:
            img.seek(0)
            if img.mode not in ("RGB", "RGBA", "L", "LA"):
                img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return data


async def to_vlm_images(images: list[bytes]) -> list[bytes]:
    """``to_vlm_image`` over a list, off the event loop (a full-size board
    takes a noticeable moment to encode)."""
    if all(_is_png_or_jpeg(b) for b in images):
        return list(images)
    return await asyncio.to_thread(lambda: [to_vlm_image(b) for b in images])

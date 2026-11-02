"""Pillow decodes only the formats Ranbell stores.

Image.open sniffs the content, so a TIFF named .png used to reach the TIFF
decoder — where Pillow's 2026-10 advisories (heap overflows, fixed in 13.0.0)
live. Every Image.open in the backend passes PIL_FORMATS.
"""
import io
import re
import sys
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.ingest.extractor import _read_png_info
from backend.app.scanner.walk import PIL_FORMATS


def _bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_the_stored_formats_still_open(fmt):
    assert isinstance(_read_png_info(Path("x"), _bytes(fmt)), dict)


def test_a_tiff_named_png_is_refused():
    with pytest.raises(UnidentifiedImageError):
        _read_png_info(Path("disguised.png"), _bytes("TIFF"))


def test_every_image_open_in_the_backend_names_its_formats():
    app = root_dir / "backend" / "app"
    bare = []
    for path in app.rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\bImage\.open\(", line) and "formats=PIL_FORMATS" not in line:
                bare.append(f"{path.relative_to(app)}:{n}")
    assert PIL_FORMATS == ("PNG", "JPEG", "WEBP")
    assert not bare, "Image.open without formats=PIL_FORMATS:\n" + "\n".join(bare)

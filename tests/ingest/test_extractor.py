"""Fallback chain integration tests (workflow dict only, no real image files)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import json
import tempfile
import struct
import zlib

from app.ingest.extractor import extract_from_image
from app.ingest.a1111_parser import parse_a1111

FIXTURES = Path(__file__).parent / "fixtures" / "workflows"

A1111_TEXT = """\
1girl, masterpiece, best quality
Negative prompt: lowres, bad anatomy
Steps: 20, Sampler: Euler a, CFG scale: 7, Model: test_model
"""


def _make_png_with_metadata(chunks: dict[str, str]) -> bytes:
    """Create a minimal valid PNG with tEXt metadata chunks."""
    PNG_HEADER = b"\x89PNG\r\n\x1a\n"

    def make_chunk(name: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return length + name + data + struct.pack(">I", crc)

    # Minimal 1x1 white PNG
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat_data = zlib.compress(b"\x00\xff\xff\xff")

    body = PNG_HEADER
    body += make_chunk(b"IHDR", ihdr_data)
    for key, value in chunks.items():
        text_data = key.encode() + b"\x00" + value.encode()
        body += make_chunk(b"tEXt", text_data)
    body += make_chunk(b"IDAT", idat_data)
    body += make_chunk(b"IEND", b"")
    return body


def test_a1111_fallback_priority():
    """A1111 parameters should be used when present."""
    workflow = json.loads((FIXTURES / "simple_clip.json").read_text())
    png_bytes = _make_png_with_metadata({
        "parameters": A1111_TEXT,
        "prompt": json.dumps(workflow),
    })
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp = Path(f.name)
    try:
        result = extract_from_image(tmp)
        assert result.extraction.method == "a1111"
        assert "1girl" in result.positive_prompt
    finally:
        tmp.unlink()


def test_comfyui_ksampler_fallback():
    """ComfyUI workflow KSampler tracing used when no A1111 params."""
    workflow = json.loads((FIXTURES / "simple_clip.json").read_text())
    png_bytes = _make_png_with_metadata({
        "prompt": json.dumps(workflow),
    })
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp = Path(f.name)
    try:
        result = extract_from_image(tmp)
        assert result.extraction.method == "ksampler_trace"
        assert "1girl" in result.positive_prompt
        assert "lowres" in result.negative_prompt
    finally:
        tmp.unlink()


def test_failed_when_no_metadata():
    """Images with no metadata should produce method=failed."""
    png_bytes = _make_png_with_metadata({})
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(png_bytes)
        tmp = Path(f.name)
    try:
        result = extract_from_image(tmp)
        assert result.extraction.method == "failed"
        assert result.positive_prompt is None
    finally:
        tmp.unlink()


def test_extraction_info_stored():
    """ExtractionResult always has extraction metadata."""
    result = parse_a1111(A1111_TEXT)
    assert result.extraction.extracted_at != ""
    assert result.extraction.extractor_version == "1"


def test_model_info_from_a1111():
    result = parse_a1111(A1111_TEXT)
    assert result.model_info.model_name == "test_model"
    assert result.model_info.steps == 20
    assert result.model_info.cfg_scale == 7.0
    assert result.model_info.sampler == "Euler a"

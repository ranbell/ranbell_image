"""The image list ships grid fields only; the detail endpoint ships everything.

The full payload averages ~9 KB a row, and raw_metadata alone is ~5 KB of it —
none of which the grid draws. Scrolling a large library dragged all of it into
the browser, so the list asks Qdrant for a field subset instead.

These tests pin the split: anything the grid or the tag sidebar reads has to
stay in the subset, and the heavy detail-only fields have to stay out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.db.qdrant_client import GALLERY_PAYLOAD, GALLERY_PAYLOAD_FIELDS

# Read off gallery rows in frontend/src/App.vue — the card template, the folder
# card template, and the tag-filter vocabulary built from the loaded rows.
GRID_READS = {
    "sha256",             # thumbnail src, keys, selection
    "name",               # caption
    "size", "mtime",      # caption's second line
    "star_rating",        # inline star control
    "batch_category",     # AI / NR chip
    "embedding_status",   # WD14 chip
    "wd14_tags",          # availableTagSet when no model filter is active
    "path",               # folder view matching
}

# Only ever reached through `selected` — the detail panel, one image at a time,
# which refetches the whole document from /api/images/{sha256}.
DETAIL_ONLY = {
    "raw_metadata", "wd14_tags_scores", "params",
    "extraction", "model_info", "negative_prompt", "positive_prompt",
    "color_lab", "palette_hues", "palette_hex",
}


@pytest.mark.parametrize("field", sorted(GRID_READS))
def test_gallery_payload_keeps_every_field_the_grid_reads(field):
    assert field in GALLERY_PAYLOAD_FIELDS


@pytest.mark.parametrize("field", sorted(DETAIL_ONLY))
def test_gallery_payload_drops_detail_only_fields(field):
    assert field not in GALLERY_PAYLOAD_FIELDS


def test_gallery_payload_is_an_include_selector():
    """A plain `True` here would silently restore the full payload."""
    assert GALLERY_PAYLOAD.include == GALLERY_PAYLOAD_FIELDS


def test_gallery_payload_has_no_duplicates():
    assert len(GALLERY_PAYLOAD_FIELDS) == len(set(GALLERY_PAYLOAD_FIELDS))

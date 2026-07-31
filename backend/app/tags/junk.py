"""Tags that are never worth carrying into a prompt.

Not a taste filter — these are tags that actively break an image or say nothing
about it. Everything here was observed doing damage in a real run:

``no_humans`` and ``1girl`` both reached one final prompt, and ``no_eyes`` came
out of a theme split as a description of a character who has eyes. Nothing in
this pipeline ever wants a ``no_*`` tag: a background track's scene is merged
with a character, so "no humans" is always a contradiction, and a negated
feature is better expressed by leaving the feature out.

``fisheye`` / ``border`` and friends came from the opposite direction — the
board renders genuinely had those artifacts, WD14 read them back correctly, and
the final image ended up inside an oval lens mask. They describe the frame
rather than the picture, so they survive every semantic filter.
"""
from __future__ import annotations

# Frame and layout artifacts. A board sketch that happens to render a border
# should not teach the final image to render one.
_FRAME_JUNK = frozenset({
    "border", "black_border", "white_border", "framed", "frame",
    "letterboxed", "pillarboxed", "vignetting", "fisheye", "isometric",
    "multiple_views", "reference_sheet", "character_sheet", "collage",
    "split_screen", "diptych", "triptych", "cropped", "out_of_frame",
    "image_sample", "sample_watermark", "watermark", "signature", "artist_name",
    "username", "web_address", "logo", "text_focus",
    # Literal instructions to draw a costume chart. A person board that picks
    # these up comes back as eight outfit variants of the same girl instead of
    # one character in one scene.
    "alternate_costume", "official_alternate_costume", "alternate_hairstyle",
    "variations", "costume_switch", "cosplay",
})

# Rating tags with no visual meaning at all. `questionable` / `explicit` do
# describe content, so they stay under the NSFW switch rather than here.
_EMPTY_RATING = frozenset({"general", "sensitive", "rating_general", "rating_safe"})


def is_junk_tag(tag: str) -> bool:
    """True when this tag should never reach a prompt."""
    name = str(tag or "").strip().lower().replace(" ", "_")
    if not name:
        return True
    # Negations. Guarded on the underscore so `nose_blush` and `noodles` stay.
    if name.startswith("no_"):
        return True
    return name in _FRAME_JUNK or name in _EMPTY_RATING


def strip_junk(tags: list[str]) -> list[str]:
    return [t for t in tags if not is_junk_tag(t)]

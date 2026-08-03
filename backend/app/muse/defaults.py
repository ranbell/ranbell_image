"""Muse tuning defaults.

These are the values the chain was validated at, not guesses. Changing them is
allowed — the panel exposes most of them — but each one earned its number on real
runs, so the comments say what breaks when it moves.
"""
from __future__ import annotations

# ── The draft ──────────────────────────────────────────────────────────────
# Four variations from one seed and one latent, so the batch is what varies and
# nothing else. They are cheap in steps but full size: the draft is not a
# thumbnail to be re-tagged and thrown away, it is the picture the rest of the
# chain argues with, and at 12 steps it is already good enough to keep.
DRAFT_DEFAULTS: dict[str, object] = {
    "width": 896,
    "height": 1152,
    "draft_steps": 12,
    # Low enough that the checkpoint fills in what the prompt did not say, high
    # enough that it still obeys. Below 3 the theme starts slipping.
    "draft_cfg": 4.0,
    "draft_count": 4,
}

# ── The refine chain ───────────────────────────────────────────────────────
REFINE_DEFAULTS: dict[str, object] = {
    "final_steps": 30,
    "final_cfg": 4.5,
    # B, C and D. Cutting to 1 or 2 stops early; there is no fourth instruction,
    # so this cannot go higher. Every stage's image is kept either way, because
    # which one is best depends on how good the draft was, not on how late it is.
    "refine_stages": 3,
    # Well below the library default of 0.35. The weak tail is the point: it is
    # what the checkpoint drew without being asked, and stage B builds on it.
    "wd14_threshold": 0.2,
    # WD14 category 4 is named characters — the checkpoint recognising somebody
    # else's character inside its own draft, which then becomes the final image.
    "drop_character_tags": True,
    "drop_rating_tags": False,
}

# ── The look, which is the user's call and not the model's ─────────────────
STYLE_DEFAULTS: dict[str, object] = {
    # Goes in at the top of the brief and every stage is told to obey it. The
    # same theme in two styles is two different pictures.
    "style": "Cute 2D Anime Style",
    # Appended to whatever the workflow already carries.
    "negative_prompt": (
        "bad quality, bad anatomy, simple, simple_background, border, "
        "black border, white_border, notice, information, photo frame, "
        "registered mark, multiview, frame,"
    ),
}

ALL_DEFAULTS: dict[str, object] = {
    **DRAFT_DEFAULTS,
    **REFINE_DEFAULTS,
    **STYLE_DEFAULTS,
}

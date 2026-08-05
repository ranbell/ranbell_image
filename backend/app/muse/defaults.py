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

# ── Board / shoot ───────────────────────────────────────────────────────────
REFINE_DEFAULTS: dict[str, object] = {
    "final_steps": 30,
    "final_cfg": 4.5,
    # Legacy key kept for older panels; B/C/D pickup is removed.
    "refine_stages": 0,
    "wd14_threshold": 0.2,
    "drop_character_tags": True,
    "drop_rating_tags": False,
}

# ── The model's own reasoning ──────────────────────────────────────────────
LLM_DEFAULTS: dict[str, object] = {
    # Off by default. When on, it applies to stage A only — pose is where
    # contradictory postures used to pile up. Refine stages stay fast.
    #
    # What it buys on A is a different prompt, not a tidier one. Without it a
    # stage writes four postures into one paragraph and the image model picks
    # one, unguided; it also writes things nothing can draw, and lets the
    # reference block leak in. With it the pose resolves to one.
    #
    # It costs about eight times the wall clock of a stage without it.
    "think": False,
    # Sized for thinking being on: reasoning runs to thousands of tokens before
    # the answer starts, and the brief and an image are already in the window.
    # 16k was tight; this is the size both were measured at.
    "num_ctx": 32768,
    # Empty = reuse `model` for B/C/D. Set a vision-capable model here and a
    # cheaper text model in `model` to cut stage A's wait.
    "vision_model": "",
    # Composition bias for every stage. auto lets the theme decide.
    "framing": "auto",
    # Drop the VLM from VRAM before each Comfy render. Off by default.
    "unload_vlm": False,
    # Cast preset for the table-read crew (see muse.crew.PRESETS).
    "crew_preset": "standard",
    # Banter between craft passes. light = Ollama-friendly (fewer side calls);
    # full = previous speaker + occasional heckler; off = craft only.
    "banter_mode": "light",
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
    **LLM_DEFAULTS,
    **STYLE_DEFAULTS,
}

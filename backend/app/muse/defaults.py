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
    # Empty = reuse `model` for the turns that are shown the board. Set a
    # vision-capable model here and a cheaper text model in `model` to keep the
    # text-only passes fast. A model that cannot read images does not error — it
    # returns nothing — so the chain retries blind once and says so in chat.
    "vision_model": "",
    # Composition bias for every stage. auto lets the theme decide.
    "framing": "auto",
    # ── Probes ──────────────────────────────────────────────────────────────
    # Small enough to be nearly free, big enough to judge composition and
    # exposure on. The crew looks at one of these between passes instead of
    # arguing about a picture nobody has seen.
    "probe_size": 512,
    "probe_steps": 12,
    # How many enrich/reduce/probe rounds before showing the board regardless.
    # A failure at the cap is announced, never shipped quietly.
    "probe_max_rounds": 3,
    # Drop the LLM from VRAM before each render. ON, because they do not fit:
    # measured on this box, a 26B MoE holds 12.5GB of a 15.6GB card and ComfyUI
    # OOMs on the 0.8GB left. This was False for a while on the strength of an
    # "MoE is only ~8GB active" claim that was never measured. Set it False only
    # if your card can genuinely hold a checkpoint and the model at once; the
    # cost of leaving it on is a model reload between the talking and the
    # drawing, which `ollama_keep_alive` cannot help with because the point is
    # to give the memory back.
    "unload_vlm": True,
}

# ── The look, which is the user's call and not the model's ─────────────────
STYLE_DEFAULTS: dict[str, object] = {
    # Goes in at the top of the brief, into the positive right after identity,
    # and every stage is told to obey it. The same theme in two styles is two
    # different pictures.
    #
    # Empty means the checkpoint's own look. There is no crew taste to average
    # any more — picking people to move the style cost the picture more than it
    # bought, so a look is something you type here or do not get.
    "style": "",
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

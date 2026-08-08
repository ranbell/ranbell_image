"""Muse tuning defaults.

These are the values the chain was validated at, not guesses. Changing them is
allowed — the panel exposes most of them — but each one earned its number on real
runs, so the comments say what breaks when it moves.
"""
from __future__ import annotations

# ── The draft ──────────────────────────────────────────────────────────────
# One full-size frame, not a thumbnail: the test shot is the picture the rest of
# the chain argues with, so it has to be the real thing.
DRAFT_DEFAULTS: dict[str, object] = {
    "width": 896,
    "height": 1152,
    # 12 was where the variations-in-a-batch design left it — enough to judge a
    # composition four ways. Shooting one frame instead buys the steps back, and
    # at 20 the test shot is worth keeping rather than only worth reading.
    "draft_steps": 20,
    # Low enough that the checkpoint fills in what the prompt did not say, high
    # enough that it still obeys. Below 3 the theme starts slipping.
    "draft_cfg": 4.0,
    # One. A batch of four was four opinions to choose between, but it is also
    # the thing that makes a full-size latent run out of card (see CLAUDE.md:
    # batch 2 and up is where the margin goes), and the chain only ever argues
    # with one of them. This is the final shoot's batch too — `run_shoot_job`
    # reads the same key.
    "draft_count": 1,
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
    # Drop the LLM from VRAM before each Comfy render. On: a model left resident
    # is still holding the card when ComfyUI wants it, and a full-size latent
    # then has nowhere to go. Setting `keep_alive` on the Ollama side made that
    # worse rather than better — giving the memory back is the entire point of
    # the unload, so pinning the model defeats it. Turn this off only for a card
    # big enough to hold a checkpoint and the model at the same time.
    "unload_vlm": True,
    # Cast preset for the table-read crew (see muse.crew.PRESETS).
    "crew_preset": "standard",
    # Banter between craft passes. light = Ollama-friendly (fewer side calls);
    # full = previous speaker + occasional heckler; off = craft only.
    "banter_mode": "light",
}

# ── The look, which is the user's call and not the model's ─────────────────
STYLE_DEFAULTS: dict[str, object] = {
    # Goes in at the top of the brief, into the positive right after identity,
    # and every stage is told to obey it. The same theme in two styles is two
    # different pictures.
    #
    # Empty by default so the cast decides: `crew.style_direction` reads the
    # room's taste and names a base look. Filled in with a fixed phrase it wins
    # outright, which is right when someone has an opinion and wrong as a
    # default — a preset style meant swapping the whole crew changed nothing
    # about how the picture was rendered.
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

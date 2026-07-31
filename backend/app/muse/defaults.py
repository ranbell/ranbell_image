"""Muse tuning defaults.

Kept in one place because most of them are meant to be argued with: the board
is cheap on purpose, and the merge is deliberately under-cleaned so unexpected
tags survive into the final prompt.
"""
from __future__ import annotations

# ── Image board ────────────────────────────────────────────────────────────
# Small and fast: the board exists to be re-tagged, not to be looked at closely.
# ``board_steps`` is NOT 2. A 2-step render only reads on a step-distilled model
# (Turbo / Lightning); on an ordinary checkpoint it produces noise, and noise
# re-tagged at threshold 0.15 is noise all the way to the final prompt. 16 is a
# safe default for an ordinary model — lower it when the workflow can take it.
BOARD_DEFAULTS: dict[str, object] = {
    "board_width": 512,
    "board_height": 512,
    "board_steps": 16,
    # Low CFG lets the checkpoint drift toward what it is good at instead of
    # forcing every tag literally. That drift is where the surprise comes from.
    "board_cfg": 3.0,
    "board_count": 3,          # per track, so 3 background + 3 character
}

# ── Reverse tagging ────────────────────────────────────────────────────────
# 0.15 is far below the library default (0.35) and picks up a lot of weak,
# half-wrong tags. That is the point: the weak tail is what makes the merged
# prompt go somewhere the theme alone would not have.
HARVEST_DEFAULTS: dict[str, object] = {
    "harvest_threshold": 0.15,
    # Two-stage selection (confidence weighting + frequency banding) is
    # implemented but off: it trades surprise for tidiness. Compare on real runs
    # before making it the default.
    "harvest_rerank": False,
    "harvest_rerank_top_n": 40,
    "drop_rating_tags": False,   # no censoring unless the user asks
    "drop_character_tags": True,  # WD14 category 4 = named characters
}

# ── Tag merge ──────────────────────────────────────────────────────────────
# Both values are deliberately looser than Refine's (0.3 / 20). Trimming harder
# collapses the result back onto the obvious reading of the theme.
MERGE_DEFAULTS: dict[str, object] = {
    "merge_common_ratio": 0.5,
    "merge_unique_count": 30,
    # 0.0 = all background, 1.0 = all character. The dial between a wide
    # establishing shot and a portrait.
    "character_weight": 0.5,
}

# ── Tag expansion ──────────────────────────────────────────────────────────
EXPAND_DEFAULTS: dict[str, object] = {
    "topic_tag_limit": 25,
    "wildness": 3,        # vocab_bank: 3 adds rare-band tags on top of lunatic
    "frontier_count": 8,  # tags the library has never used at all
}

ALL_DEFAULTS: dict[str, object] = {
    **BOARD_DEFAULTS,
    **HARVEST_DEFAULTS,
    **MERGE_DEFAULTS,
    **EXPAND_DEFAULTS,
}

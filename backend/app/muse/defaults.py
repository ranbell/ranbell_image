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
    # forcing every tag literally. That drift is where the surprise comes from,
    # now that the vocabulary search no longer supplies any.
    "board_cfg": 2.0,
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
    # One small LLM call per track after the read-back, to catch what no
    # frozenset can: a tag that presupposes a person, a franchise name, or the
    # draft's own layout leaking in. Bounded so it can never gut the list.
    "llm_cleanup": True,
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
    # Framing, named rather than left to whichever of three seeds won the
    # budget. "auto" keeps whatever the drafts produced.
    "shot": "auto",
    # Tags the user will not have dropped, ranked with the character's own
    # identity. `solo` is the reason this exists: it was in a prompt and lost
    # anyway to a poolside scene full of people.
    "must_tags": [],
    # The aesthetic. Not derived from the theme — the same theme in two styles
    # is two different pictures, and that is the user's call, not the model's.
    "style": "cute anime illustration",
    # `detailed character` is not decoration. Without it the pipeline drifts
    # toward beautiful scenery with a small figure in it — a picture of a place
    # that happens to contain someone, rather than a picture of someone.
    "effect": "kodak color, detailed character, very_detailed_background",
    # Where the camera is, separately from how close it is.
    "angle": "auto",
    # Literal strings to render, as [{text, where}]. The checkpoints this app
    # targets can write short words when asked plainly.
    "texts": [],
}

# ── Composition and top-up ─────────────────────────────────────────────────
COMPOSE_DEFAULTS: dict[str, object] = {
    # Per-aspect caps live in slots.py, where the aspects are defined.
    # After the board exists, how many theme-adjacent tags it may gain.
    # Seasoning, not the dish.
    "topup_picks": 5,
    # Cosine cutoff on the vocabulary search. Below this the "neighbours" of a
    # theme are only loosely related and the candidate list becomes noise.
    "topup_min_score": 0.3,
    # Let retrieval top up a slot the model left short. Off means the prompt is
    # exactly what the model wrote.
    "vocab_supplement": True,
}

ALL_DEFAULTS: dict[str, object] = {
    **BOARD_DEFAULTS,
    **HARVEST_DEFAULTS,
    **MERGE_DEFAULTS,
    **COMPOSE_DEFAULTS,
}

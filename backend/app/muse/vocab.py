"""What Muse asks the WD14 vocabulary for.

Both retrieval steps — the compose supplement and the top-up — search the same
bank with the same band, and the band was wrong in a way that took two runs to
see. A bakery theme came back with a sword; a stargazing theme came back with a
katana. The model was blamed for both. It was not the model.

``min_freq`` is a tag's Danbooru post count over the most common tag's, so
``0.01`` means **fifty-one thousand posts**. At that floor the vocabulary a
theme can reach is only the most-photographed things in anime:

    sword     235,273    passes          telescope         1,319    excluded
    flower    512,140    passes          binoculars        3,741    excluded
    umbrella   76,782    passes          meteor_shower       722    excluded
    katana     55,923    passes          oven                653    excluded

Every specific noun a scene is actually about sits below the floor, and every
generic prop sits above it. The search then ranks by meaning, the slot filter
asks "is this a prop?", and the only props left to say yes are the swords. The
floor was selecting for genericness and calling it quality.

The bar it was reaching for is real but much lower: a tag the checkpoint has
never seen enough of cannot be drawn. A few hundred examples is that bar.
"""
from __future__ import annotations

# ≈500 Danbooru posts. Below this a tag is not reliably drawable; above it the
# specific nouns survive alongside the generic ones and meaning does the ranking.
MIN_FREQ = 0.0001
# `1girl` and friends. A tag on a fifth of all images says nothing about a theme.
MAX_FREQ = 0.80

# Cosine floor for the compose supplement.
#
# Unlike the top-up, the supplement is not choosing between candidates — it
# walks the hit list and takes the first thing its slot accepts, however far
# down that is. With no floor the Object slot reached rank 22, 31 and 37 of 40
# to find `katana`, `umbrella` and `sword` at 0.373–0.386, while the hits the
# theme actually produced sat at 0.45+ and belonged to other slots.
SUPPLEMENT_MIN_SCORE = 0.42

# How full a slot has to be before the supplement leaves it alone.
#
# It used to fill to the cap, which was fine while the caps were three and four
# and became a problem the moment they were eight and ten: a model that had
# written four good garments got four more from a vector search, and the search
# is working from a theme phrase, not a picture. Everything it added across six
# runs was wrong in the same way — `holding_own_foot` for a summer festival,
# `rain` for a scene in harsh sun, `bathroom` beside `kitchen`,
# `hands_in_opposite_sleeves` filed as clothing.
#
# A slot with three answers is not short. This is a floor to reach, not a
# budget to spend.
SUPPLEMENT_TARGET = 3

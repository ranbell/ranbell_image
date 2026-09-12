"""Composing the parts into one paragraph, with no memory of anything.

The composer is the one turn allowed to write flowing prose, and the whole
reason it is trustworthy is what it is NOT given. It sees the shot as it stands
and nothing else — no conversation, no theme, no brief, no previous prompt, no
board image. Composing was never the thing that went wrong. Being handed twenty
turns of contradicting history was, and a composer with no history cannot be
confused by one.

The first test in this file is the load-bearing one: it asserts on the prompt
string itself, because "we do not pass the transcript" is a claim that quietly
stops being true the first time somebody adds a helpful block.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, shared, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402

COMPOSED = (
    "SCENE: An empty classroom in late afternoon, low sun through the glass, "
    "desks in rows and a chalkboard behind her, a white blouse and a pleated "
    "skirt, standing with her weight on one hip, a small closed smile, shot "
    "from below."
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)






# ── the prompt is the table and nothing else ────────────────────────────────


# ── the result ──────────────────────────────────────────────────────────────


# ── the parser ──────────────────────────────────────────────────────────────

def test_a_paragraph_that_arrived_in_pieces_is_flattened():
    """"No headings" is a request; this is what happens when it is ignored."""
    assert chain.parse_compose("SCENE: one line\n  and\n\nanother") == \
        "one line and another"


def test_a_bare_paragraph_with_no_label_is_still_read():
    assert chain.parse_compose("An empty classroom.") == "An empty classroom."


def test_nothing_composes_to_nothing():
    assert chain.parse_compose("") == ""


# ── W-Muse: compose knows there are two of them ─────────────────────────────
# 2026-08-11's real-session report found compose actively harmful for W-Muse:
# `facets._vocabulary()` only read the eight A-side facets, so any legitimate
# mention of the second Muse read as an "invented" word and the composition
# was discarded — every W-Muse render fell back to `nl_join`'s raw
# concatenation, which is what actually produced the "one Muse dominant, the
# other barely present, prose incoherent" images.

async def _w_duet_session(db, **over):
    session = await _duet_session(db, partner_preset="c2", **over)
    session["character"]["name_ja"] = "倉田 あさひ"
    session["partner_character"] = {
        "character_id": "c2", "name_ja": "みなも",
        "identity_tags": ["1girl", "black_hair"],
        "personality": {}, "palette": [], "signature_prop": "",
    }
    await session_db.save(db, session)
    return session




def test_solo_compose_system_is_unchanged():
    """`COMPOSE_SYSTEM` (no partner) stays exactly what a solo session always
    saw — the constant every solo call site still reads directly."""
    assert chain.COMPOSE_SYSTEM == chain.compose_system()
    assert "みなも" not in chain.COMPOSE_SYSTEM
    assert "BOTH" not in chain.COMPOSE_SYSTEM



"""Seat report card.

The numbers here come from a real run of the still-first table read, which is
what prompted the module: four seats put ten tags into the craft and the
Finisher deleted every one of them on the next turn, while the unit director
applied three separate Showrunner notes in a single pass and all of it shipped.
Until the ledger existed there was no way to see that.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import report


def _session(ledger, final, **over):
    return {
        "session_id": "s",
        "inputs": {"theme": "カラオケ", "crew_preset": "standard"},
        "board": {"prompt": final},
        "ledger": ledger,
        **over,
    }


def test_a_seat_that_kept_nothing_reads_as_keeping_nothing():
    session = _session(
        [
            {"muse_id": "beat:ichibyou", "name": "演出「一秒」", "ms": 30_000,
             "added": ["wide_shot", "casual_clothes", "messy_room"], "dropped": []},
            {"muse_id": "gaffer:gyakkou", "name": "照明「逆光」", "ms": 40_000,
             "added": ["rim_lighting", "dramatic_shadow"], "dropped": []},
            {"muse_id": "grade:sokoage", "name": "仕上げ「底上げ」", "ms": 50_000,
             "added": ["masterpiece"],
             "dropped": ["rim_lighting", "dramatic_shadow"]},
        ],
        "wide_shot, casual_clothes, messy_room, masterpiece",
    )

    out = report.session_report(session)
    by_name = {s["name"]: s for s in out["seats"]}

    assert by_name["演出「一秒」"]["survival"] == 1.0
    assert by_name["照明「逆光」"]["survival"] == 0.0
    assert by_name["照明「逆光」"]["seconds"] == 40.0
    assert by_name["照明「逆光」"]["seconds_per_survivor"] is None
    # The finding sorts to the top rather than hiding at the bottom.
    assert out["seats"][0]["name"] == "照明「逆光」"
    assert out["total_seconds"] == 120.0


def test_a_deletion_is_credited_to_whoever_did_the_work():
    """Two seats where one is always deleting the other's work are two seats
    that want to be one seat. That only shows up if deletions have a victim."""
    session = _session(
        [
            {"muse_id": "gaffer:gyakkou", "name": "照明「逆光」", "ms": 1000,
             "added": ["rim_lighting", "dramatic_shadow"], "dropped": []},
            {"muse_id": "faces:mabataki", "name": "作画（芝居）「まばたき」", "ms": 1000,
             "added": ["expressive_eyes"], "dropped": []},
            {"muse_id": "grade:sokoage", "name": "仕上げ「底上げ」", "ms": 1000,
             "added": ["masterpiece"],
             "dropped": ["rim_lighting", "dramatic_shadow", "expressive_eyes"]},
        ],
        "masterpiece",
    )

    by_name = {s["name"]: s for s in report.session_report(session)["seats"]}
    assert by_name["仕上げ「底上げ」"]["overwrote"] == {
        "照明「逆光」": 2, "作画（芝居）「まばたき」": 1,
    }
    # Loudest victim first, so the pair to look at is the first one listed.
    assert list(by_name["仕上げ「底上げ」"]["overwrote"])[0] == "照明「逆光」"


def test_a_seat_deleting_its_own_earlier_tag_has_not_overwritten_anyone():
    session = _session(
        [
            {"muse_id": "lens:pinto", "name": "撮影「ピント」", "ms": 1000,
             "added": ["close_up", "bokeh"], "dropped": []},
            {"muse_id": "lens:pinto", "name": "撮影「ピント」", "ms": 1000,
             "added": ["wide_shot"], "dropped": ["close_up"]},
        ],
        "bokeh, wide_shot",
    )
    seat = report.session_report(session)["seats"][0]
    assert seat["overwrote"] == {}
    assert seat["added"] == 3 and seat["survived"] == 2


def test_a_tag_deleted_then_put_back_by_someone_else_credits_the_second_seat():
    session = _session(
        [
            {"muse_id": "gaffer:gyakkou", "name": "照明", "ms": 1000,
             "added": ["rim_lighting"], "dropped": []},
            {"muse_id": "grade:sokoage", "name": "仕上げ", "ms": 1000,
             "added": [], "dropped": ["rim_lighting"]},
            {"muse_id": "finisher:maku", "name": "編集", "ms": 1000,
             "added": ["rim_lighting"], "dropped": []},
        ],
        "rim_lighting",
    )
    by_name = {s["name"]: s for s in report.session_report(session)["seats"]}
    assert by_name["照明"]["survived"] == 0, "its version was deleted"
    assert by_name["編集"]["survived"] == 1
    assert by_name["仕上げ"]["overwrote"] == {"照明": 1}


def test_the_final_prompt_is_what_shipped_not_the_working_craft():
    session = _session(
        [{"muse_id": "beat:ichibyou", "name": "演出", "ms": 1000,
          "added": ["wide_shot", "never_rendered"], "dropped": []}],
        "wide_shot",
        craft={"tags": "wide_shot, never_rendered"},
    )
    assert report.session_report(session)["seats"][0]["survived"] == 1


def test_a_session_from_before_the_ledger_reports_nothing_rather_than_lying():
    assert report.session_report({"session_id": "old"})["seats"] == []
    assert report.aggregate([{"session_id": "old"}])["sessions"] == 0


def test_the_aggregate_is_what_the_retire_decision_reads():
    """One session is an anecdote — a seat can survive at 0% on a bad round."""
    def run(rim_survives):
        return _session(
            [
                {"muse_id": "gaffer:gyakkou", "name": "照明", "ms": 40_000,
                 "added": ["rim_lighting"], "dropped": []},
                {"muse_id": "beat:ichibyou", "name": "演出", "ms": 20_000,
                 "added": ["wide_shot"], "dropped": []},
            ],
            "rim_lighting, wide_shot" if rim_survives else "wide_shot",
        )

    out = report.aggregate([run(False), run(False), run(True)])
    by_name = {s["name"]: s for s in out["seats"]}
    assert out["sessions"] == 3
    assert by_name["照明"]["sessions"] == 3
    assert by_name["照明"]["survival"] == round(1 / 3, 3)
    assert by_name["照明"]["seconds_per_session"] == 40.0
    assert by_name["演出"]["survival"] == 1.0
    # The names to look at first, without needing a UI to read it.
    assert out["keeping_least"] == []      # 0.33 is not yet damning
    assert out["slowest"][0] == "照明"


def test_a_seat_that_never_keeps_anything_is_named_outright():
    session = _session(
        [
            {"muse_id": "gaffer:gyakkou", "name": "照明", "ms": 40_000,
             "added": ["rim_lighting", "dramatic_shadow"], "dropped": []},
            {"muse_id": "grade:sokoage", "name": "仕上げ", "ms": 10_000,
             "added": ["masterpiece"],
             "dropped": ["rim_lighting", "dramatic_shadow"]},
        ],
        "masterpiece",
    )
    assert report.aggregate([session, session])["keeping_least"] == ["照明"]


# ── the list the report reads ───────────────────────────────────────────────
class _ScrollDb:
    """Qdrant's scroll has no ordering and pages via an offset cursor."""

    def __init__(self, rows):
        self.rows = rows
        self._qc = self
        self.pages = 0

    async def scroll(self, collection_name, limit, offset=None, with_payload=True):
        class _P:
            def __init__(self, payload):
                self.payload = payload
                self.id = payload["session_id"]
        start = int(offset or 0)
        page = self.rows[start:start + limit]
        self.pages += 1
        nxt = start + limit if start + limit < len(self.rows) else None
        return [_P(r) for r in page], nxt


@pytest.mark.asyncio
async def test_recent_sessions_are_the_newest_not_an_arbitrary_handful():
    """Scroll returns points in no particular order, so asking it for `limit`
    and sorting those gave five sessions picked at random. A report over "the
    last five sessions" built on that is worse than useless."""
    from app.muse import session_db

    rows = [
        {"session_id": f"s{i}", "status": "done", "created_at": float(i),
         "inputs": {"theme": f"t{i}"}}
        for i in range(600)
    ]
    # Shuffled the way an unordered scan would hand them back.
    rows = rows[300:] + rows[:300]
    db = _ScrollDb(rows)

    out = await session_db.list_recent(db, limit=5)

    assert [r["session_id"] for r in out] == ["s599", "s598", "s597", "s596", "s595"]
    assert db.pages > 1, "must page through the whole collection, not one window"

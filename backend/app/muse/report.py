"""Seat report card — which jobs earn their place at the table.

The table read runs eighteen seats and takes minutes, and until now there was
no way to ask what any of them was for. The first end-to-end run of the
still-first flow answered it by accident: reading the tag ledger back, the
lighting seat, the acting animator, the colour designer and the animation
director between them put ten tags into the craft and **none of them survived**
— the Finisher deleted every one on the next turn. Meanwhile the unit director
applied three separate Showrunner notes in a single pass and all of it shipped.

So the question this module answers is not "who spoke" but "whose work is still
in the picture at the end", and what the wall clock was for it.

Three numbers per seat:

- **survived / added** — of the tags a seat introduced, how many are in the
  finished prompt. This is the seat's value, and it is brutal: a seat can talk
  beautifully for forty seconds and leave nothing behind.
- **seconds per survivor** — the same thing priced. What retiring this seat
  would buy back.
- **who it overwrote** — the tags a seat deleted, credited to whoever added
  them. Two seats where one is always deleting the other's work are two seats
  that want to be one seat.

Nothing here mutates a session; it reads `session["ledger"]` and the final
craft. Sessions written before the ledger existed simply report nothing.
"""
from __future__ import annotations

from typing import Any

from . import crew, identity


def _rows(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (session.get("ledger") or []) if isinstance(r, dict)]


def _final_tags(session: dict[str, Any]) -> set[str]:
    """What actually reached the render.

    The board's prompt when there is one — that is the text ComfyUI was handed.
    Otherwise the working craft, for a session still being argued over.
    """
    for source in (
        (session.get("shoot") or {}).get("prompt"),
        (session.get("board") or {}).get("prompt"),
        (session.get("craft") or {}).get("tags"),
    ):
        if str(source or "").strip():
            return set(identity.tag_names(str(source)))
    return set()


def _blank(muse_id: str, name: str) -> dict[str, Any]:
    return {
        "muse_id": muse_id,
        "role": crew.role_of(muse_id),
        "name": name,
        "turns": 0,
        "added": 0,
        "dropped": 0,
        "survived": 0,
        "ms": 0,
        "overwrote": {},
    }


def session_report(session: dict[str, Any]) -> dict[str, Any]:
    """One session's seats, worst survival rate first."""
    final = _final_tags(session)
    seats: dict[str, dict[str, Any]] = {}
    # Who put each tag in most recently, so a deletion can be credited to the
    # seat whose work was deleted rather than to nobody.
    author: dict[str, str] = {}

    for row in _rows(session):
        mid = str(row.get("muse_id") or "")
        seat = seats.setdefault(mid, _blank(mid, str(row.get("name") or mid)))
        seat["turns"] += 1
        seat["ms"] += int(row.get("ms") or 0)

        for tag in row.get("dropped") or []:
            seat["dropped"] += 1
            owner = author.get(tag)
            if owner and owner != mid:
                victim = seats.setdefault(owner, _blank(owner, owner))
                seat["overwrote"][victim["name"]] = (
                    seat["overwrote"].get(victim["name"], 0) + 1
                )
            author.pop(tag, None)

        for tag in row.get("added") or []:
            seat["added"] += 1
            author[tag] = mid

    # Survival is credited once the walk is over, to whoever put the tag in
    # LAST. Scoring it at the moment of adding gave a seat credit for a tag
    # that was deleted a turn later and re-added by somebody else — both of
    # them scored, and the one whose work was actually thrown away looked fine.
    for tag, mid in author.items():
        if tag in final and mid in seats:
            seats[mid]["survived"] += 1

    for seat in seats.values():
        seat["survival"] = (
            round(seat["survived"] / seat["added"], 3) if seat["added"] else None
        )
        seat["seconds"] = round(seat["ms"] / 1000, 1)
        seat["seconds_per_survivor"] = (
            round(seat["ms"] / 1000 / seat["survived"], 1) if seat["survived"]
            else None
        )
        # Loudest victim first — that is the pair worth looking at.
        seat["overwrote"] = dict(sorted(
            seat["overwrote"].items(), key=lambda kv: -kv[1],
        ))

    # A seat that kept nothing is the finding, so it sorts to the top. Seats
    # that added nothing at all sort with it rather than vanishing into a
    # "no data" bucket — contributing nothing is also an answer.
    ordered = sorted(
        seats.values(),
        key=lambda s: (s["survival"] if s["survival"] is not None else -1,
                       -s["seconds"]),
    )
    return {
        "session_id": session.get("session_id", ""),
        "theme": str((session.get("inputs") or {}).get("theme") or ""),
        "crew_preset": str((session.get("inputs") or {}).get("crew_preset") or ""),
        "final_tags": len(final),
        "total_seconds": round(sum(s["ms"] for s in seats.values()) / 1000, 1),
        "seats": ordered,
    }


def aggregate(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """The same numbers across many sessions — the retire/merge decision.

    One session is an anecdote. A seat that survives at 0% once had a bad
    round; a seat that survives at 0% across a dozen is a seat to delete or
    fold into its neighbour.
    """
    seats: dict[str, dict[str, Any]] = {}
    counted = 0

    for session in sessions:
        report = session_report(session)
        if not report["seats"]:
            continue
        counted += 1
        for row in report["seats"]:
            mid = row["muse_id"]
            seat = seats.setdefault(mid, {
                **_blank(mid, row["name"]), "sessions": 0,
            })
            seat["sessions"] += 1
            for key in ("turns", "added", "dropped", "survived", "ms"):
                seat[key] += row[key]
            for victim, n in (row["overwrote"] or {}).items():
                seat["overwrote"][victim] = seat["overwrote"].get(victim, 0) + n

    for seat in seats.values():
        seat["survival"] = (
            round(seat["survived"] / seat["added"], 3) if seat["added"] else None
        )
        seat["seconds"] = round(seat["ms"] / 1000, 1)
        seat["seconds_per_session"] = (
            round(seat["ms"] / 1000 / seat["sessions"], 1) if seat["sessions"]
            else None
        )
        seat["seconds_per_survivor"] = (
            round(seat["ms"] / 1000 / seat["survived"], 1) if seat["survived"]
            else None
        )
        seat["overwrote"] = dict(sorted(
            seat["overwrote"].items(), key=lambda kv: -kv[1],
        ))

    ordered = sorted(
        seats.values(),
        key=lambda s: (s["survival"] if s["survival"] is not None else -1,
                       -s["seconds"]),
    )
    return {
        "sessions": counted,
        "seats": ordered,
        # Named plainly so the answer is readable without a UI: these are the
        # seats to look at first when deciding what to retire or merge.
        "keeping_least": [
            s["name"] for s in ordered
            if s["survival"] is not None and s["survival"] < 0.2
        ],
        "slowest": [
            s["name"] for s in sorted(ordered, key=lambda s: -s["seconds"])[:5]
        ],
    }

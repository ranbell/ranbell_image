"""**The face of whoever spoke always appears in the same place.** (2026-09-18)

The Showrunner: "sometimes the thumbnail is missing when a Muse speaks".

Counting her bubbles live (`0239133f`, standard, 18 seats), **rows with a face and
rows without were mixed together**:

    kind=say / banter / verify_ok    speaker_id present  → face shows
    kind=heckle, seat (the opening)  no speaker_id       → **no face**

She heckles two or three times a turn in a crewed shoot. The same person is
speaking, and **her face vanished depending on where the words came from.** Two
causes:

    ① confirmed rows   `faceShaForRow` returns the lead's face for any assistant
                       row, which would put a face on crew seats too. That was
                       held back by **an allow-list of bubble kinds**, and
                       `heckle` was not on it
    ② while streaming  inside the crew she streamed under `actress:cast` (the seat
                       id), so the panel's "is this the lead" (`liveIsLead`) was
                       false

Fixed one at a time — decide by **whose words they are**. A crew seat carries
`meta.role` and gets no face; the lead's rows get a face whatever their kind. The
stream address is lined up on her own `character_id` by `crew_room.stream_id`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "frontend/src/components/MusePanel.vue").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    body = SRC[SRC.index(f"function {name}("):]
    return body[: body.index("\n}\n") + 2]


# ── Settled rows ────────────────────────────────────────────────────────

def test_the_face_is_chosen_by_who_spoke_not_by_the_kind_of_bubble():
    body = _fn("faceShaForRow")
    assert "row?.meta?.role" in body, "行の持ち主（席の役）を見ていない"
    assert "role !== 'actress'" in body, "班の席にまで顔が付く"


def test_the_template_no_longer_keeps_a_list_of_allowed_bubbles():
    """An allow-list **leaks** — `heckle` missing from it was this symptom."""
    m = re.search(r'v-if="faceForRow\(row\)([^"]*)"', SRC)
    assert m, "行の顔の出し分けが見つからない"
    assert m.group(1).strip() == "", f"種類の許可リストが残っている: {m.group(1)[:80]}"


def test_a_crew_seat_has_no_face():
    """No lead's face on a crew seat (staging, lighting…) — that was what the
    allow-list was for."""
    body = _fn("faceShaForRow")
    i_role = body.index("row?.meta?.role")
    i_lead = body.index("return leadFaceSha.value", i_role)
    assert i_role < i_lead, "席を弾く前に主演の顔を返している"


# ── While it streams ────────────────────────────────────────────────────

def test_the_folded_bubble_keeps_its_face():
    """If only folded bubbles lose the face, it blinks while she keeps talking."""
    assert "face: liveIsLead.value ? leadFace.value : ''" in _fn("foldLive")
    assert 'v-if="done.face"' in SRC


def test_the_lead_streams_under_her_own_id():
    """Inside the crew she still streams under her own id (that is what the panel's
    `liveIsLead` reads)."""
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.muse import crew, crew_room

    session = {"character": {"character_id": "c1", "name_ja": "各務 みお"}}
    seat = [m for m in crew.resolve_crew(preset="standard")
            if crew.role_of(m) == "actress"][0]
    assert crew_room.stream_id(session, seat) == "c1"
    # The crew seats are unchanged (the side with no face)
    assert crew_room.stream_id(session, "gaffer:gyakkou") == "gaffer:gyakkou"


def test_every_speaking_signal_goes_through_the_same_door():
    """Every place that emits `muse_speaking` goes through `stream_id`."""
    import inspect
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.muse import crew_room

    src = inspect.getsource(crew_room)
    signals = re.findall(r'"type": "muse_speaking", "muse_id": ([^,\n]+)', src)
    assert signals, "muse_speaking を出す所が見つからない"
    assert all("stream_id(" in s for s in signals), signals

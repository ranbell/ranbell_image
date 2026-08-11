"""The two failures that were reported from real sessions.

1. The camera moved from a high angle to a low one and `looking_up` survived,
   so the picture broke.
2. The Showrunner said「上着脱いで」and the jacket came back, for several turns.

Both are here as end-to-end tests through `post_duet_chat` / `duet_prep_stage`,
because both were end-to-end failures — every individual piece looked correct.

The assertions that carry the most weight are the ones about parts nobody
wrote. `rev` not moving on `place` while the camera changes is the difference
between a model that was asked to leave the room alone and a shot where the
room was never in the answer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import brief as brief_mod
from app.muse import facets, service, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


OPENING = """SAY: 教室の窓際です。机、黒板、カーテン、上着も着てます。
PLACE TAGS: classroom, window, indoors
PLACE: An empty classroom, she stands by the tall windows.
HOUR TAGS: late_afternoon
HOUR: Late afternoon in autumn.
LIGHT TAGS: sunlight, warm_light
LIGHT: Low sun comes through the glass from the left.
PROPS TAGS: desk, chalkboard, curtain, chair, satchel
PROPS: Desks in rows, a chalkboard, curtains, a satchel on a chair.
COSTUME TAGS: jacket, pleated_skirt
COSTUME: She wears a navy jacket over a pleated skirt.
POSE TAGS: standing, hand_on_own_hip
POSE: She stands with her weight on one hip.
EXPRESSION TAGS: smile, closed_mouth
EXPRESSION: A small closed smile.
CAMERA TAGS: from_above, high_angle, looking_up, upper_body
CAMERA: Shot from above, she looks up into the lens.

COSTUME:
SILHOUETTE: soft, boxy over a flared skirt
LAYERS: blouse under a jacket
COLOURWAY: navy and grey
PATTERN: solid
FABRIC: wool
CONDITION: worn-in
HERO: the navy jacket
GARMENTS: top=jacket / bottom=pleated_skirt / feet=loafers / extras=none
"""

LOW_ANGLE = """SAY: 下から煽ります。見下ろす形になりますね。
CAMERA TAGS: from_below, low_angle, looking_down, upper_body
CAMERA: Shot from below, she looks down into the lens.
"""

NO_JACKET = """SAY: 上着は脱ぎました。ブラウスとスカートだけです。
COSTUME TAGS: white_blouse, pleated_skirt
COSTUME: She wears a white blouse and a pleated skirt.

COSTUME:
SILHOUETTE: trim over a flared skirt
LAYERS: a single blouse
COLOURWAY: white and grey
PATTERN: solid
FABRIC: cotton
CONDITION: crisp
HERO: the pleated skirt
GARMENTS: top=white_blouse / bottom=pleated_skirt / feet=loafers / extras=none
"""


class FacetOllama(FakeOllama):
    """Speaks the facet contract, and routes notes by keyword."""

    def __init__(self, routes=None, preps=None):
        super().__init__()
        self.routes = routes or {}
        self.preps = list(preps or [])
        self.prep_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        text = "SAY: はい、わかりました。"

        if "eight parts" in system or "eleven parts" in system:
            text = next(
                (v for k, v in self.routes.items() if k in str(prompt)),
                "FACETS: none\nSTANDING: none",
            )
        elif "YOU ARE THE WHOLE CREW TODAY" in system:
            self.prep_prompts.append(str(prompt))
            text = self.preps.pop(0) if self.preps else "SAY: そのままで。"

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


async def _opened(db, ollama):
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.duet_prep_stage(db, ollama, s)
    return s


# ── bug 1: the camera moved and the old gaze stayed ─────────────────────────

@pytest.mark.asyncio
async def test_a_low_angle_takes_the_high_angle_and_its_gaze_with_it():
    db = FakeDb()
    ollama = FacetOllama(
        routes={"下から": "FACETS: camera\nCAMERA: 下から煽る"},
        preps=[OPENING, LOW_ANGLE],
    )
    s = await _opened(db, ollama)
    assert "from_above" in s["craft"]["tags"]
    assert "looking_up" in s["craft"]["tags"]

    await service.post_duet_chat(db, ollama, s, "やっぱり下から煽って")
    await service.duet_prep_stage(db, ollama, s)

    tags = s["craft"]["tags"]
    assert "from_below" in tags and "looking_down" in tags
    # The reported failure, in two lines.
    assert "from_above" not in tags
    assert "looking_up" not in tags
    assert "from_above" not in s["craft"]["prompt"]
    assert "looking_up" not in s["craft"]["prompt"]


@pytest.mark.asyncio
async def test_a_camera_move_does_not_touch_the_room_or_the_clothes():
    """The thesis. Not "the model was careful" — the room was never in the
    answer, so there was nothing for it to be careless with."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={"下から": "FACETS: camera\nCAMERA: 下から煽る"},
        preps=[OPENING, LOW_ANGLE],
    )
    s = await _opened(db, ollama)
    was = {n: dict(facets.table_of(s)[n]) for n, _ in facets.FACETS}

    await service.post_duet_chat(db, ollama, s, "やっぱり下から煽って")
    await service.duet_prep_stage(db, ollama, s)

    table = facets.table_of(s)
    for name in ("place", "hour", "light", "props", "costume", "pose", "expression"):
        assert table[name]["rev"] == was[name]["rev"], f"{name} was rewritten"
        assert table[name]["tags"] == was[name]["tags"]
        assert table[name]["nl"] == was[name]["nl"]
    assert table["camera"]["rev"] == was["camera"]["rev"] + 1


@pytest.mark.asyncio
async def test_the_direction_does_not_stack_across_camera_moves():
    db = FakeDb()
    ollama = FacetOllama(
        routes={
            "上から": "FACETS: camera\nCAMERA: 上から見下ろす",
            "下から": "FACETS: camera\nCAMERA: 下から煽る",
        },
        preps=[OPENING, LOW_ANGLE],
    )
    s = await _opened(db, ollama)
    await service.post_duet_chat(db, ollama, s, "上から見下ろす感じで")
    await service.post_duet_chat(db, ollama, s, "やっぱり下から煽って")
    assert list(s["directives"]) == ["camera"]
    assert s["directives"]["camera"]["text"] == "下から煽る"


# ── bug 2: the jacket came back ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_jacket_comes_off_everywhere_it_was_written():
    """It used to come out of the tag list and stay in the COSTUME block, which
    every later turn re-read as LOCKED."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={"上着": "FACETS: costume\nCOSTUME: 上着なし"},
        preps=[OPENING, NO_JACKET],
    )
    s = await _opened(db, ollama)
    assert "jacket" in s["craft"]["tags"]
    assert "jacket" in brief_mod.costume_block(s["costume"])

    await service.post_duet_chat(db, ollama, s, "上着は脱いで")
    await service.duet_prep_stage(db, ollama, s)

    assert "jacket" not in s["craft"]["tags"]
    assert "jacket" not in s["craft"]["prompt"]
    assert "jacket" not in brief_mod.costume_block(s["costume"])
    assert "jacket" not in str(s["brief"])
    for value in facets.table_of(s)["costume"]["fields"].values():
        assert "jacket" not in str(value).lower()
    # The rest of the outfit survives — this is an undress, not a wipe.
    assert "pleated_skirt" in s["craft"]["tags"]


@pytest.mark.asyncio
async def test_the_jacket_stays_off_for_the_rest_of_the_session():
    """"何度も復活します" — the failure was that it came back a few turns later,
    when a part that had nothing to do with clothes was rewritten."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={
            "上着": "FACETS: costume\nCOSTUME: 上着なし",
            "下から": "FACETS: camera\nCAMERA: 下から煽る",
        },
        preps=[OPENING, NO_JACKET, LOW_ANGLE],
    )
    s = await _opened(db, ollama)
    await service.post_duet_chat(db, ollama, s, "上着は脱いで")
    await service.duet_prep_stage(db, ollama, s)

    for note in ("いい感じ", "下から煽って", "そのまま", "うん", "オッケー"):
        await service.post_duet_chat(db, ollama, s, note)
        await service.duet_prep_stage(db, ollama, s)
        assert "jacket" not in s["craft"]["tags"], f"came back after 「{note}」"
        assert "jacket" not in brief_mod.costume_block(s["costume"])


@pytest.mark.asyncio
async def test_taking_the_jacket_off_does_not_ban_it_forever():
    """She has to be able to put it back on. A ban is permanent, and this was
    never a refusal — it was a change of clothes."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={"上着": "FACETS: costume\nCOSTUME: 上着なし"},
        preps=[OPENING, NO_JACKET],
    )
    s = await _opened(db, ollama)
    await service.post_duet_chat(db, ollama, s, "上着は脱いで")
    await service.duet_prep_stage(db, ollama, s)
    assert s["banned"] == []


# ── the item duplicated across two facets (2026-08-11 e2e run) ──────────────
#
# The straw hat problem, reproduced exactly. A single note ("put the straw hat
# on") got routed to BOTH `props` and `costume` in one turn, writing the tag
# into both independently. The removal note later routed to `props` only — the
# router protecting `costume` as "already settled" — so the removal could
# never structurally reach costume's own copy that turn. It never came off,
# in two independent real runs, because nothing told costume's LATER, unrelated
# rewrites that the hat was no longer wanted. The digest is the fix: it is
# shown to every facet-writing turn regardless of what the router named today.

HAT_ADDED = """SAY: 麦わら帽子をかぶせました。
PROPS TAGS: desk, straw_hat
PROPS: A desk by the window, and a straw hat resting nearby.
COSTUME TAGS: white_blouse, pleated_skirt, straw_hat
COSTUME: A white blouse, a pleated skirt, and a straw hat.
"""

HAT_REMOVED_FROM_PROPS_ONLY = """SAY: 麦わら帽子は片付けました。
PROPS TAGS: desk
PROPS: A desk by the window.
"""

SCARF_OFF = """SAY: スカーフは外しました。
COSTUME TAGS: white_blouse, pleated_skirt, straw_hat
COSTUME: A white blouse and a pleated skirt, no scarf.

COSTUME:
SILHOUETTE: trim over a flared skirt
LAYERS: a single blouse
COLOURWAY: white and grey
PATTERN: solid
FABRIC: cotton
CONDITION: crisp
HERO: the pleated skirt
GARMENTS: top=white_blouse / bottom=pleated_skirt / feet=loafers / extras=straw_hat
"""


@pytest.mark.asyncio
async def test_an_item_duplicated_across_two_facets_by_one_turn():
    db = FakeDb()
    ollama = FacetOllama(
        routes={"かぶせて": "FACETS: props, costume\nPROPS: 帽子あり\nCOSTUME: 帽子あり"},
        preps=[OPENING, HAT_ADDED],
    )
    s = await _opened(db, ollama)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子をかぶせて")
    await service.duet_prep_stage(db, ollama, s)

    assert "straw_hat" in facets.table_of(s)["props"]["tags"]
    assert "straw_hat" in facets.table_of(s)["costume"]["tags"]


@pytest.mark.asyncio
async def test_the_digest_records_the_removal_even_when_only_one_copy_is_reachable():
    """The removal note routes to `props` only — same as the real run — so the
    structural write can only ever reach one of the two copies this turn. The
    digest still has to record the decision so `costume`'s NEXT rewrite,
    whatever prompts it, knows."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={
            "かぶせて": "FACETS: props, costume\nPROPS: 帽子あり\nCOSTUME: 帽子あり",
            "もう要らない": (
                "FACETS: props\nPROPS: 帽子なし\n"
                "DIGEST: 麦わら帽子は一度使ったが、以降は使わないことに決定。"
            ),
        },
        preps=[OPENING, HAT_ADDED, HAT_REMOVED_FROM_PROPS_ONLY],
    )
    s = await _opened(db, ollama)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子をかぶせて")
    await service.duet_prep_stage(db, ollama, s)

    await service.post_duet_chat(db, ollama, s, "その麦わら帽子はもう要らない")
    await service.duet_prep_stage(db, ollama, s)

    # Structurally, only props was touched this turn — costume's copy is
    # untouched by construction, exactly as designed.
    assert "straw_hat" not in facets.table_of(s)["props"]["tags"]
    assert "straw_hat" in facets.table_of(s)["costume"]["tags"]
    # But the decision is recorded where every future rewrite can read it.
    assert "麦わら帽子" in s["digest"] and "使わない" in s["digest"]

    # A later, unrelated costume rewrite must have been HANDED the decision —
    # this is the mechanism-level guarantee a unit test can make. Whether the
    # model then acts on it is a real-model question, verified separately
    # against the live services (see private/muse/e2e_2026-08-11_facets/).
    await service.post_duet_chat(db, ollama, s, "スカーフを外して")
    await service.duet_prep_stage(db, ollama, s)
    assert "麦わら帽子" in ollama.prep_prompts[-1]
    assert "使わない" in ollama.prep_prompts[-1]


# ── the long session ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_prep_prompt_does_not_grow_with_the_conversation():
    """O(parts), not O(turns). The prep turn used to be handed twelve raw chat
    turns, every standing order ever given, and the whole previous positive."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={"下から": "FACETS: camera\nCAMERA: 下から煽る"},
        preps=[OPENING] + [LOW_ANGLE] * 12,
    )
    s = await _opened(db, ollama)
    for i in range(12):
        await service.post_duet_chat(db, ollama, s, f"下から煽って {i}")
        await service.duet_prep_stage(db, ollama, s)

    scoped = ollama.prep_prompts[1:]
    assert max(len(p) for p in scoped) < len(scoped[0]) * 1.4


@pytest.mark.asyncio
async def test_the_prep_turn_is_not_handed_the_conversation():
    db = FakeDb()
    ollama = FacetOllama(
        routes={"下から": "FACETS: camera\nCAMERA: 下から煽る"},
        preps=[OPENING, LOW_ANGLE],
    )
    s = await _opened(db, ollama)
    await service.post_duet_chat(db, ollama, s, "膝のあいだの隙間がいいね")
    await service.post_duet_chat(db, ollama, s, "下から煽って")
    await service.duet_prep_stage(db, ollama, s)

    prompt = ollama.prep_prompts[-1]
    assert "膝のあいだ" not in prompt, "banter reached the turn that writes craft"
    assert "ここまでの会話" not in prompt


@pytest.mark.asyncio
async def test_a_prep_with_nothing_asked_for_rewrites_nothing():
    db = FakeDb()
    ollama = FacetOllama(preps=[OPENING])
    s = await _opened(db, ollama)
    rev = facets.table_rev(facets.table_of(s))

    await service.post_duet_chat(db, ollama, s, "いい感じ")
    await service.duet_prep_stage(db, ollama, s)
    assert facets.table_rev(facets.table_of(s)) == rev


@pytest.mark.asyncio
async def test_a_locked_part_survives_direction_aimed_at_it():
    db = FakeDb()
    ollama = FacetOllama(
        routes={"上着": "FACETS: costume\nCOSTUME: 上着なし"},
        preps=[OPENING, NO_JACKET],
    )
    s = await _opened(db, ollama)
    facets.set_lock(s, "costume", True)

    await service.post_duet_chat(db, ollama, s, "上着は脱いで")
    await service.duet_prep_stage(db, ollama, s)
    assert "jacket" in s["craft"]["tags"]


# ── W-Muse: two Muses, two sides ────────────────────────────────────────────
# The real 20-turn W-Muse session (private/muse/e2e_2026-08-11_wmuse/REPORT.md)
# found the OLD, un-faceted path had no syntax for "whose state is this" — a
# note aimed at one Muse had nowhere to land that the other Muse's facets
# could not also be read as answering. These are the full-turn versions of
# the side-isolation guarantees `test_facets.py` already proves structurally.

OPENING_W = """SAY: A: 教室の窓際です。上着を着てます。 B: 私は麦わら帽子を被ってます。
PLACE TAGS: classroom, window, indoors
PLACE: An empty classroom, they stand by the tall windows.
HOUR TAGS: late_afternoon
HOUR: Late afternoon in autumn.
LIGHT TAGS: sunlight, warm_light
LIGHT: Low sun comes through the glass from the left.
PROPS TAGS: desk, chalkboard, curtain, chair
PROPS: Desks in rows, a chalkboard, curtains.
COSTUME TAGS: jacket, pleated_skirt
COSTUME: She wears a navy jacket over a pleated skirt.
COSTUME_B TAGS: straw_hat, sundress
COSTUME_B: She wears a straw hat over a pale sundress.
POSE TAGS: standing, hand_on_own_hip
POSE: She stands with her weight on one hip.
POSE_B TAGS: standing, arms_behind_back
POSE_B: She stands with her hands behind her back.
EXPRESSION TAGS: smile, closed_mouth
EXPRESSION: A small closed smile.
EXPRESSION_B TAGS: smile, open_mouth
EXPRESSION_B: A bright, open smile.
CAMERA TAGS: wide_shot, eye_level, standing_side_by_side
CAMERA: A wide shot of the two of them standing side by side.
"""

HAT_OFF_B = """SAY: B: 麦わら帽子は外しますね。
COSTUME_B TAGS: sundress
COSTUME_B: She wears just the pale sundress now, no hat.
"""

CARDIGAN_ON_A = """SAY: A: カーディガンを羽織ります。
COSTUME TAGS: jacket, cardigan, pleated_skirt
COSTUME: She adds a cardigan over the jacket and skirt.
"""


async def _w_duet_session(db, **over):
    """A W-Muse session whose partner is already cached — see the identical
    helper in test_route.py for why (`FakeDb` has no character presets to
    resolve `partner_preset` against)."""
    session = await _duet_session(db, partner_preset="c2", **over)
    session["partner_character"] = {
        "character_id": "c2", "name_ja": "みなも",
        "identity_tags": ["1girl", "black_hair"],
        "personality": {}, "palette": [], "signature_prop": "",
    }
    await session_db.save(db, session)
    return session


async def _w_opened(db, ollama):
    s = await _w_duet_session(db)
    await service.duet_prep_stage(db, ollama, s)
    return s


@pytest.mark.asyncio
async def test_a_note_aimed_at_the_second_muse_never_rewrites_the_leads_facets():
    db = FakeDb()
    ollama = FacetOllama(
        routes={"帽子は外して": "FACETS: costume_b\nCOSTUME_B: 麦わら帽子なし"},
        preps=[OPENING_W, HAT_OFF_B],
    )
    s = await _w_opened(db, ollama)
    lead_before = dict(facets.table_of(s)["costume"])

    await service.post_duet_chat(db, ollama, s, "みなもの麦わら帽子は外して")
    await service.duet_prep_stage(db, ollama, s)

    b = facets.table_of(s)["costume_b"]
    assert "straw_hat" not in b["tags"]
    assert "sundress" in b["tags"]
    lead_after = facets.table_of(s)["costume"]
    assert lead_after["rev"] == lead_before["rev"]
    assert lead_after["tags"] == lead_before["tags"]
    assert "jacket" in s["craft"]["tags"], "the lead's jacket must still reach the render"


@pytest.mark.asyncio
async def test_an_unrelated_lead_side_rewrite_does_not_bring_the_hat_back():
    """The removal reaches only `costume_b` structurally — same shape as the
    single-Muse hat/props test above. A later, unrelated rewrite of the
    LEAD's own costume must not touch B's facet at all, let alone restore
    what was refused there."""
    db = FakeDb()
    ollama = FacetOllama(
        routes={
            "帽子は外して": "FACETS: costume_b\nCOSTUME_B: 麦わら帽子なし",
            "カーディガン": "FACETS: costume\nCOSTUME: カーディガン追加",
        },
        preps=[OPENING_W, HAT_OFF_B, CARDIGAN_ON_A],
    )
    s = await _w_opened(db, ollama)

    await service.post_duet_chat(db, ollama, s, "みなもの麦わら帽子は外して")
    await service.duet_prep_stage(db, ollama, s)
    b_rev = facets.table_of(s)["costume_b"]["rev"]

    await service.post_duet_chat(db, ollama, s, "あさひはカーディガンを羽織って")
    await service.duet_prep_stage(db, ollama, s)

    assert facets.table_of(s)["costume_b"]["rev"] == b_rev, "B's facet was rewritten by a note about A"
    assert "straw_hat" not in facets.table_of(s)["costume_b"]["tags"]
    assert "cardigan" in facets.table_of(s)["costume"]["tags"]

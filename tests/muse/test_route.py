"""Which parts of the shot a note changes, and what that replaces.

Two things are being tested here and the second is the interesting one.

The router is a closed-vocabulary turn like the strike clerk: it picks from
eight part names and anything else is dropped, so a wrong answer can only ever
be a smaller answer — and a smaller answer rewrites less of the shot.

The second is that direction is now *reconciled*. A note about the camera
replaces the previous note about the camera instead of stacking beside it. That
is the whole of the long-session fix: `orders_block` handed every note ever said
to every turn, newest first, and left the model to work out which of seventeen
absolute instructions won.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, facets, service, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


class RoutingOllama(FakeOllama):
    """Routes on the note, and says one line for everything else."""

    def __init__(self, routes: dict[str, str]):
        super().__init__()
        self.routes = routes

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        text = "SAY: はい。"
        if "script supervisor" in system and "eight parts" in system:
            text = next(
                (v for k, v in self.routes.items() if k in str(prompt)),
                "FACETS: none\nSTANDING: none",
            )

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()

    def systems(self) -> str:
        return "\n".join(str(c.get("system") or "") for c in self.calls)


# ── the parser ──────────────────────────────────────────────────────────────

def test_a_part_name_the_model_invented_is_dropped():
    named, lines, _, _ = chain.parse_route(
        "FACETS: camera, vibes, atmosphere\nCAMERA: 下から煽って"
    )
    assert named == ["camera"]
    assert lines == {"camera": "下から煽って"}


def test_none_is_a_complete_answer():
    named, lines, standing, digest = chain.parse_route("FACETS: none\nSTANDING: none")
    assert named == [] and lines == {} and standing == "" and digest == ""


def test_a_directive_for_a_part_that_was_not_named_is_not_acted_on():
    """The FACETS line is the decision; the rest is its detail."""
    named, lines, _, _ = chain.parse_route(
        "FACETS: camera\nCAMERA: 下から\nCOSTUME: 上着を脱いで"
    )
    assert named == ["camera"]
    assert "costume" not in lines


def test_the_label_spelling_is_read_leniently():
    named, lines, standing, _ = chain.parse_route(
        "**FACETS** ： camera\n- CAMERA ： 下から煽って\nSTANDING： 足は映さない"
    )
    assert named == ["camera"]
    assert lines["camera"] == "下から煽って"
    assert standing == "足は映さない"


def test_a_named_part_with_no_line_still_routes():
    """Worst case is the old behaviour, scoped to the part it is about — the
    caller falls back to the note's own words."""
    named, lines, _, _ = chain.parse_route("FACETS: camera")
    assert named == ["camera"] and lines == {}


def test_nothing_at_all_routes_nothing():
    assert chain.parse_route("SAY: こんにちは") == ([], {}, "", "")
    assert chain.parse_route("") == ([], {}, "", "")


# ── the decision digest ─────────────────────────────────────────────────────

def test_a_revised_digest_is_captured_to_the_end_of_the_text():
    """DIGEST is free prose and can run to several lines — it is not read by
    the one-line-per-label scan, so it needs its own capture, greedy to EOF."""
    named, lines, standing, digest = chain.parse_route(
        "FACETS: costume\nCOSTUME: 上着なし\nSTANDING: none\n"
        "DIGEST: 麦わら帽子は一度使ったが、以降は使わないことに決定。\n"
        "カーディガンは脱いだあと、また着ることになった。"
    )
    assert named == ["costume"]
    assert digest == (
        "麦わら帽子は一度使ったが、以降は使わないことに決定。\n"
        "カーディガンは脱いだあと、また着ることになった。"
    )


def test_unchanged_means_keep_the_caller_supplied_digest():
    """The model says nothing changed; the caller's own value stands rather
    than being overwritten with an empty string."""
    _, _, _, digest = chain.parse_route("FACETS: none\nSTANDING: none\nDIGEST: unchanged")
    assert digest == ""


def test_a_missing_digest_field_also_keeps_the_caller_supplied_value():
    _, _, _, digest = chain.parse_route("FACETS: camera\nCAMERA: 下から煽って")
    assert digest == ""


def test_digest_prose_cannot_be_mistaken_for_a_facet_line():
    """A digest sentence that happens to contain "衣装:" (costume-shaped text,
    just not the label) must not be read as a second COSTUME directive."""
    named, lines, _, digest = chain.parse_route(
        "FACETS: props\nPROPS: コーラの缶を足す\n"
        "STANDING: none\n"
        "DIGEST: 衣装: まだ何も決まっていない。カメラ: 保留。"
    )
    assert named == ["props"]
    assert "costume" not in lines and "camera" not in lines
    assert digest == "衣装: まだ何も決まっていない。カメラ: 保留。"


# ── reconciliation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_second_camera_note_replaces_the_first():
    """The long-session fix, in one assertion. Direction does not stack."""
    db = FakeDb()
    ollama = RoutingOllama({
        "上から": "FACETS: camera\nCAMERA: 上から見下ろす",
        "下から": "FACETS: camera\nCAMERA: 下から煽る",
    })
    s = await _duet_session(db)

    await service.route_note(db, ollama, s, "上から見下ろす感じで", cfg={})
    assert list(s["directives"]) == ["camera"]

    await service.route_note(db, ollama, s, "やっぱり下から煽って", cfg={})
    assert list(s["directives"]) == ["camera"], "a second camera order stacked"
    assert s["directives"]["camera"]["text"] == "下から煽る"


@pytest.mark.asyncio
async def test_direction_for_different_parts_sits_side_by_side():
    db = FakeDb()
    ollama = RoutingOllama({
        "下から": "FACETS: camera\nCAMERA: 下から煽る",
        "上着": "FACETS: costume\nCOSTUME: 上着なし",
    })
    s = await _duet_session(db)
    await service.route_note(db, ollama, s, "下から撮って", cfg={})
    await service.route_note(db, ollama, s, "上着は脱いで", cfg={})

    assert set(s["directives"]) == {"camera", "costume"}
    block = service.directives_block(s)
    assert "CAMERA: 下から煽る" in block
    assert "COSTUME: 上着なし" in block


@pytest.mark.asyncio
async def test_the_direction_block_cannot_outgrow_the_shot():
    """Twenty turns hand over the same eight lines a two-turn session does."""
    db = FakeDb()
    ollama = RoutingOllama({"": "FACETS: camera\nCAMERA: 下から煽る"})
    s = await _duet_session(db)
    for i in range(20):
        await service.route_note(db, ollama, s, f"note {i}", cfg={})
    assert len(service.directives_block(s).splitlines()) == 2  # header + one line


@pytest.mark.asyncio
async def test_a_rule_for_the_whole_shoot_is_not_a_part():
    db = FakeDb()
    ollama = RoutingOllama({
        "足": "FACETS: none\nSTANDING: 足は絶対に映さない",
    })
    s = await _duet_session(db)
    named, standing = await service.route_note(db, ollama, s, "足は映さないで", cfg={})

    assert named == []
    assert s["standing"] == ["足は絶対に映さない"]
    assert "足は絶対に映さない" in facets.standing_block(s["standing"])


@pytest.mark.asyncio
async def test_a_standing_rule_is_not_added_twice():
    db = FakeDb()
    ollama = RoutingOllama({"足": "FACETS: none\nSTANDING: 足は絶対に映さない"})
    s = await _duet_session(db)
    await service.route_note(db, ollama, s, "足は映さないで", cfg={})
    await service.route_note(db, ollama, s, "足は映さないで", cfg={})
    assert s["standing"] == ["足は絶対に映さない"]


@pytest.mark.asyncio
async def test_a_locked_part_is_never_routed_to():
    """The Showrunner pinned it. A note cannot quietly unpin it.

    `named` still reports costume — the note WAS about costume, pinned or not,
    and the caller (`post_duet_chat`) needs that to route this to the "nothing
    to do" path rather than the strike clerk. Only the write side (directives,
    `session["routed"]`) is filtered.
    """
    db = FakeDb()
    ollama = RoutingOllama({"上着": "FACETS: costume\nCOSTUME: 上着なし"})
    s = await _duet_session(db)
    facets.set_lock(s, "costume", True)
    named, _ = await service.route_note(db, ollama, s, "上着は脱いで", cfg={})
    assert named == ["costume"]
    assert "costume" not in s["directives"]
    assert s["routed"] == []
    assert s["locked_conflicts"] == ["costume"]


@pytest.mark.asyncio
async def test_a_note_about_a_locked_part_does_not_fall_through_to_the_clerk():
    """Found by the 2026-08-11 real-model e2e run (turn 15).

    Camera was locked. 「カメラ、真横から撮ってみて」routed to `camera` — a
    replacement-shaped note, correctly recognised as being about the camera.
    But `route_note` used to filter locked facets out of `named` itself, so
    `post_duet_chat` saw an EMPTY list and fell through to the strike clerk as
    if this were an unroutable refusal. The real clerk, given the note text and
    the current camera tags, judged (not unreasonably) that `from_front` no
    longer applied and struck it — and `facets.strike` sweeps locked facets on
    purpose, because a genuine refusal outranks a pin. The bug was that this
    was never a refusal; the lock only looked like one to the branch that
    decides, and the strike clerk should never have run at all.
    """
    db = FakeDb()
    ollama = RoutingOllama({
        "真横": "FACETS: camera\nCAMERA: 真横から",
    })
    s = await _duet_session(db)
    facets.write(s, "camera", tags="from_front, low_angle", nl="From the front, low.")
    facets.set_lock(s, "camera", True)
    before = dict(facets.table_of(s)["camera"])

    await service.post_duet_chat(db, ollama, s, "カメラ、真横から撮ってみて")

    assert "script supervisor's clerk" not in ollama.systems(), (
        "a note the router recognised as being about a locked part must not "
        "reach the refusal clerk"
    )
    after = facets.table_of(s)["camera"]
    assert after["tags"] == before["tags"]
    assert after["nl"] == before["nl"]
    assert after["rev"] == before["rev"]
    assert s["banned"] == []


@pytest.mark.asyncio
async def test_a_note_about_a_locked_part_says_so_instead_of_doing_nothing_silently():
    db = FakeDb()
    ollama = RoutingOllama({"真横": "FACETS: camera\nCAMERA: 真横から"})
    s = await _duet_session(db)
    facets.set_lock(s, "camera", True)

    await service.post_duet_chat(db, ollama, s, "カメラ、真横から撮ってみて")

    system_lines = [m["text"] for m in s["chat"] if m.get("role") == "system"]
    assert any("固定" in t for t in system_lines), \
        "an understood-but-blocked note must not look like a silent no-op"


# ── the decision digest ──────────────────────────────────────────────────────
#
# Not a filter and not a lock — a short, plain-language record of what has
# actually been decided, rewritten (not appended to) on every note, and shown
# to every facet-writing turn whether or not the router named that facet
# today. This is what closes the gap a routed directive alone cannot: a straw
# hat written into BOTH `props` and `costume` by one turn, then refused, can
# only really be caught by `costume` understanding — from this digest, next
# time it is rewritten for any reason at all — that the hat is no longer
# wanted. Nothing strips the tag after the fact; the model is told the truth.

@pytest.mark.asyncio
async def test_a_new_decision_is_recorded_in_the_digest():
    db = FakeDb()
    ollama = RoutingOllama({
        "帽子": "FACETS: costume\nCOSTUME: 上着なし\nSTANDING: none\n"
                "DIGEST: 麦わら帽子は一度使ったが、以降は使わないことに決定。",
    })
    s = await _duet_session(db)
    await service.route_note(db, ollama, s, "麦わら帽子はもう要らない", cfg={})
    assert "麦わら帽子" in s["digest"]
    assert "使わない" in s["digest"]


@pytest.mark.asyncio
async def test_the_digest_is_revised_not_appended():
    """"Added, then decided against" collapses to one line instead of surviving
    as two contradictory facts."""
    db = FakeDb()
    ollama = RoutingOllama({
        "かぶせて": "FACETS: costume\nCOSTUME: 帽子あり\nDIGEST: 麦わら帽子: 着用中。",
        "もう要らない": "FACETS: costume\nCOSTUME: 帽子なし\n"
                       "DIGEST: 麦わら帽子: 一度使ったが、以降は使わないことに決定。",
    })
    s = await _duet_session(db)
    await service.route_note(db, ollama, s, "麦わら帽子をかぶせて", cfg={})
    assert s["digest"] == "麦わら帽子: 着用中。"

    await service.route_note(db, ollama, s, "麦わら帽子はもう要らない", cfg={})
    assert s["digest"] == "麦わら帽子: 一度使ったが、以降は使わないことに決定。"
    assert "着用中" not in s["digest"], "the old, contradicted line must not survive"


@pytest.mark.asyncio
async def test_a_turn_that_leaves_the_digest_unchanged_does_not_erase_it():
    db = FakeDb()
    ollama = RoutingOllama({
        "かぶせて": "FACETS: costume\nDIGEST: 麦わら帽子: 着用中。",
        "笑顔": "FACETS: expression\nEXPRESSION: 笑顔\nDIGEST: unchanged",
    })
    s = await _duet_session(db)
    await service.route_note(db, ollama, s, "麦わら帽子をかぶせて", cfg={})
    await service.route_note(db, ollama, s, "笑顔にして", cfg={})
    assert s["digest"] == "麦わら帽子: 着用中。"


@pytest.mark.asyncio
async def test_the_digest_reaches_a_facet_the_router_did_not_name_today():
    """The core mechanism. `costume` is not named this turn — only `expression`
    is — but the digest still has to be visible to costume's NEXT rewrite,
    whenever and for whatever reason that happens, because that is the only
    turn that can actually leave the stale duplicate out."""
    db = FakeDb()
    ollama = RoutingOllama({
        "帽子": "FACETS: props\nPROPS: 帽子なし\n"
               "DIGEST: 麦わら帽子: 一度使ったが、以降は使わないことに決定。",
    })
    s = await _duet_session(db)
    facets.write(s, "place", tags="rooftop", nl="A rooftop laundry line.", by="actress:cast")
    await service.route_note(db, ollama, s, "麦わら帽子はもう要らない", cfg={})
    assert s["digest"]

    # A later prep that rewrites costume for an unrelated reason must still be
    # handed the digest — costume was never routed to on the removal turn.
    prompt = service._facet_prep_prompt(s, ["costume"])
    assert "麦わら帽子" in prompt
    assert "使わない" in prompt
    assert prompt.index("ここまでの決定") < prompt.index("いまの画")


@pytest.mark.asyncio
async def test_the_crewed_studio_never_gets_a_digest():
    db = FakeDb()
    ollama = RoutingOllama({"帽子": "FACETS: costume\nDIGEST: 麦わら帽子はもう使わない。"})
    s = await _duet_session(db)
    s["mode"] = ""
    await service.route_note(db, ollama, s, "麦わら帽子はもう要らない", cfg={})
    assert s["digest"] == ""


def test_a_bare_digest_with_no_other_fields_still_parses():
    _, _, _, digest = chain.parse_route("DIGEST: 麦わら帽子はもう使わない。")
    assert digest == "麦わら帽子はもう使わない。"


# ── the clerk stands down on a replacement ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_routed_note_does_not_ban_anything():
    """「上着脱いで」is a replacement, not a refusal.

    Banning the jacket is permanent, and she should be able to put it back on
    later. The costume facet is the only place a garment tag can live, so
    rewriting it is enough — there is nothing left for a ban to protect against.
    """
    db = FakeDb()
    ollama = RoutingOllama({"上着": "FACETS: costume\nCOSTUME: 上着なし"})
    s = await _duet_session(db)
    await service.post_duet_chat(db, ollama, s, "上着は脱いで")

    assert s["banned"] == []
    assert "costume" in s["directives"]
    assert "script supervisor's clerk" not in ollama.systems()


@pytest.mark.asyncio
async def test_an_unroutable_note_still_reaches_the_strike_clerk():
    """「メガネは今後一切なし」names no part of this shot and is the other kind
    of instruction — a standing refusal, which is what the clerk is for."""
    db = FakeDb()
    ollama = RoutingOllama({})       # everything routes to none
    s = await _duet_session(db)
    # The clerk picks from the tags in the script, so there has to be a script.
    facets.write(s, "props", tags="glasses, desk")
    service._reassemble(s)
    await service.post_duet_chat(db, ollama, s, "メガネは今後一切なし")

    assert s["directives"] == {}
    assert "script supervisor's clerk" in ollama.systems()


@pytest.mark.asyncio
async def test_a_router_that_cannot_answer_changes_nothing():
    """Guessing which part to rewrite would throw away a part of the picture
    the Showrunner never asked about."""
    class MuteOllama(FakeOllama):
        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": ""}
            return _stream()

    db = FakeDb()
    s = await _duet_session(db)
    named, standing = await service.route_note(db, MuteOllama(), s, "下から", cfg={})
    assert named == [] and standing == ""
    assert s["directives"] == {}


@pytest.mark.asyncio
async def test_the_crewed_studio_is_never_routed():
    db = FakeDb()
    ollama = RoutingOllama({"下から": "FACETS: camera\nCAMERA: 下から煽る"})
    s = await _duet_session(db)
    s["mode"] = ""
    named, _ = await service.route_note(db, ollama, s, "下から撮って", cfg={})
    assert named == []
    assert ollama.calls == []

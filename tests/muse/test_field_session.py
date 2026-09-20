"""**One corner per field — bundle the seats that share a ledger field and get one
conclusion.** (2026-09-14)

The Showrunner: "bundle the members who share a ledger field into one session so
they produce a single ledger as the conclusion — that avoids the collisions. And
have them announce what the ledger currently is first, then talk about how it
should change."

What was happening live (`6dc11d0e`, standard, 12 seats):

    look    12 words  amber_theme … magenta_theme   ← colour and line each added
                                                      their own, **amber and
                                                      magenta side by side**
    light    8 words  backlighting, rim_light, hard_rim, edge_lighting
                      ← **one seat only**, four restatements of the same backlight
    bg      12 words  … silver_spoon … silver_sugar_spoon
    frame    7 words  … air_between_limbs … air_between_elbows

The cause was not the number of seats but that **a seat could only add each turn
and never restate the field as a whole**. So the seats are bundled per field, the
current value is announced, and they settle on one value for the entire field.
The calls drop from one per seat to one per field (12 → 9 on standard).

Three things are protected here — **bundling, announcing, and never erasing the
Showrunner's words**.
"""
from __future__ import annotations

from app.muse import crew
from app.muse import crew_room as C
from app.muse import ledger as L


def _session(**kw):
    s = {"inputs": {"locale": "ja"}, "banned": [], "refine_ledger": L.blank()}
    s.update(kw)
    return s


def _floor(*pairs):
    """Turn a run of (field, CRAFT) pairs into the shape `run_table` returns."""
    return [{"muse_id": f"seat{i}", "role": "", "name": f"席{i}",
             "field": field, "say": "…", "craft": craft, "kind": "seat"}
            for i, (field, craft) in enumerate(pairs)]


# ── Bundling them ───────────────────────────────────────────────────────

def test_the_seats_that_share_a_field_sit_down_together():
    """standard's twelve seats become **nine corners** — the three contested fields are
    bundled."""
    seats = C.writing_seats(crew.resolve_crew(preset="standard"))
    groups = C.field_groups(seats)

    assert len(seats) == 12 and len(groups) == 9
    bundled = {f: [crew.role_of(m) for m in g] for f, g in groups if len(g) > 1}
    assert bundled == {
        "beat": ["beat", "spine"],
        "frame": ["cutout", "lens"],
        "look": ["palette", "ink"],
    }


def test_a_seat_with_no_field_keeps_its_own_room():
    """The lead owns no field. **Never seat the fieldless together** — they would have
    nobody to talk to."""
    groups = C.field_groups(["beat:ichibyou", "actress", "spine:bane"])
    assert groups == [
        ("beat", ["beat:ichibyou", "spine:bane"]),
        ("", ["actress"]),
    ]


def test_the_order_follows_the_seat_who_sits_first():
    """Order stays seat order — a corner sits where that field's first seat sits."""
    groups = C.field_groups(["palette:itten", "propshop:zatsuka", "ink:ipponsen"])
    assert [f for f, _ in groups] == ["look", "bg"]
    assert groups[0][1] == ["palette:itten", "ink:ipponsen"]


# ── Announcing the current value ────────────────────────────────────────

def test_the_meeting_opens_with_what_the_ledger_says_now():
    """The Showrunner: "announce what the ledger currently is, then talk about how it
    should change"."""
    head = C.field_header("look", ledger={"look": "amber_theme, cel_shading"})
    assert "amber_theme, cel_shading" in head
    assert "READS after this turn" in head
    assert str(C.FIELD_CONCLUSION_MAX) in head


def test_an_empty_field_says_so_out_loud():
    head = C.field_header("atmosphere", ledger=L.blank())
    assert "(empty)" in head
    assert "Showrunner's own words" not in head


def test_the_showrunner_words_are_named_and_the_crew_words_are_not():
    """**Say whose words they are.** The crew may restate only the words it placed."""
    head = C.field_header(
        "look", ledger={"look": "amber_theme, cel_shading, clean_lineart"},
        mine=["cel_shading", "clean_lineart"],
    )
    line = [x for x in head.splitlines() if "Showrunner's own" in x][0]
    assert "amber_theme" in line
    assert "cel_shading" not in line and "clean_lineart" not in line


def test_the_single_seat_gets_the_same_announcement():
    """A single-seat field gets the same — `light` had one seat and four restatements."""
    s = _session(refine_ledger={**L.blank(), "light": "backlighting, rim_light"})
    prompt = C.seat_prompt(
        s, "gaffer:gyakkou", director_line="夕方にして", floor=[],
    )
    assert "FIELD `light`" in prompt
    assert "now: backlighting, rim_light" in prompt
    assert "SHOWRUNNER:\n夕方にして" in prompt


def test_the_bundled_prompt_carries_the_field_and_the_floor():
    s = _session(refine_ledger={**L.blank(), "look": "amber_theme"})
    prompt = C.group_prompt(
        s, ["palette:itten", "ink:ipponsen"], field="look",
        director_line="もっと画を強く",
        floor=[{"name": "衣装", "say": "ベルベットで"}],
    )
    assert "FIELD `look`" in prompt and "now: amber_theme" in prompt
    assert "THE FLOOR SO FAR" in prompt and "衣装: ベルベットで" in prompt


def test_the_corner_is_told_the_slot_is_shared():
    """"You are the only one who writes it" is a lie in a bundled preamble. It says
    **close on one value** instead."""
    sysmsg = crew.field_table_prompt(
        ["palette:itten", "ink:ipponsen"], field="look", preset_id="standard",
    )
    assert "SPEAKER 1" in sysmsg and "SPEAKER 2" in sysmsg
    assert "you are the only seat that writes it" not in sysmsg
    assert "SHARE with the other speakers" in sysmsg
    assert "ONE value for `look`" in sysmsg
    # The voices are held by the person cards (bundled, it must not become one
    # narrator swapping name tags)
    assert "口調 (JA):" in sysmsg and sysmsg.count("VOICE (EN):") == 2


def test_the_output_contract_asks_for_one_craft_at_the_end():
    assert "ONE CRAFT line for the whole corner" in C.GROUP_OUTPUT
    assert "REPLACES any format above" in C.GROUP_OUTPUT


# ── Unpicking the reply ─────────────────────────────────────────────────

def test_the_packed_reply_splits_into_voices_and_one_conclusion():
    raw = (
        "SPEAKER: palette:itten\n"
        "SAY: 総監督、この画の色の芯は琥珀です。\n"
        "SPEAKER: ink:ipponsen\n"
        "SAY: 一点さんの琥珀に乗ります。線は締めますね。\n"
        "\n"
        "CRAFT: amber_theme, cel_shading, clean_lineart | 琥珀の芯に締めた線\n"
    )
    rows, craft = C.split_packed(raw, ["palette:itten", "ink:ipponsen"])
    assert [m for m, _ in rows] == ["palette:itten", "ink:ipponsen"]
    assert rows[0][1].startswith("総監督、この画の色の芯")
    assert "SAY:" not in rows[1][1] and "SPEAKER" not in rows[1][1]
    assert craft.startswith("amber_theme, cel_shading, clean_lineart")


def test_when_every_speaker_writes_a_craft_the_closing_one_wins():
    """The contract asks for one line; when it writes one per seat, **the closing line
    is the corner's conclusion**."""
    raw = (
        "SPEAKER: 1\nSAY: 琥珀で。\nCRAFT: amber_theme | 色\n"
        "SPEAKER: 2\nSAY: 線を締めます。\nCRAFT: amber_theme, cel_shading | 結論\n"
    )
    rows, craft = C.split_packed(raw, ["palette:itten", "ink:ipponsen"])
    assert [m for m, _ in rows] == ["palette:itten", "ink:ipponsen"]
    assert craft.startswith("amber_theme, cel_shading")


def test_a_reply_that_ignores_the_format_is_not_dropped():
    """A turn that ignored the format is not dropped — the whole thing becomes the
    first seat's line."""
    rows, craft = C.split_packed(
        "琥珀でいきましょう。\nCRAFT: amber_theme | 色",
        ["palette:itten", "ink:ipponsen"],
    )
    assert rows == [("palette:itten", "琥珀でいきましょう。")]
    assert craft.startswith("amber_theme")


def test_the_stream_follows_whoever_is_speaking():
    """**A bundled turn must not be the one where the screen goes silent.** The bubble
    switches on `SPEAKER:`."""
    seen: dict[str, list[str]] = {}

    def _fake_stream_to(session, muse_id):
        return lambda text: seen.setdefault(muse_id, []).append(text)

    original, C._stream_to = C._stream_to, _fake_stream_to
    try:
        feed = C._packed_stream(_session(session_id="s"),
                                ["palette:itten", "ink:ipponsen"])
        for ch in ("SPEAKER: palette:itten\nSAY: 琥珀です。\n"
                   "SPEAKER: ink:ipponsen\nSAY: 線を締めます。\n"):
            feed(ch)
    finally:
        C._stream_to = original

    got = {k: "".join(v) for k, v in seen.items()}
    assert "琥珀です。" in got["palette:itten"]
    assert "線を締めます。" in got["ink:ipponsen"]
    assert "琥珀" not in got.get("ink:ipponsen", "")
    for text in got.values():
        assert "SPEAKER" not in text


# ── Landing ─────────────────────────────────────────────────────────────

def test_the_conclusion_becomes_the_whole_field():
    """**The conclusion is the whole field.** It does not add, so nothing piles up."""
    led = {**L.blank(), "look": "amber_theme, sunlight, cel_shading"}
    s = _session(**{C.CREW_WORDS: {"look": ["amber_theme", "sunlight", "cel_shading"]}})
    patch, landed = C.field_land(
        s, _floor(("look", "cel_shading, clean_lineart | 線で締める")),
        ledger=led, taken=set(),
    )
    assert patch["look"] == "cel_shading, clean_lineart"
    assert landed["look"] == ["cel_shading", "clean_lineart"]


def test_the_showrunner_words_survive_the_rewrite():
    """**Only the words the crew placed can be removed.** The director's words always
    stand before the conclusion."""
    led = {**L.blank(), "look": "amber_theme, magenta_theme, magenta_glint"}
    s = _session(**{C.CREW_WORDS: {"look": ["magenta_theme", "magenta_glint"]}})
    patch, _ = C.field_land(
        s, _floor(("look", "cel_shading | 線画寄りに")),
        ledger=led, taken=set(),
    )
    assert patch["look"] == "amber_theme, cel_shading"


def test_an_old_session_without_the_note_loses_nothing():
    """A session with no note treats **every word as the Showrunner's** (falling to the
    side that keeps them)."""
    led = {**L.blank(), "bg": "green grass, plastic_bottle"}
    patch, landed = C.field_land(
        _session(), _floor(("bg", "sandy_sandal | 芝生の忘れ物")),
        ledger=led, taken=set(),
    )
    assert patch["bg"] == "green grass, plastic_bottle, sandy_sandal"
    assert landed["bg"] == ["sandy_sandal"]


def test_the_empty_sticky_fields_finally_get_filled():
    """`look` was empty in 88% of sessions and `atmosphere` in 76% — an empty field is
    filled by the corner's conclusion."""
    patch, _ = C.field_land(
        _session(),
        _floor(("look", "amber_theme, cel_shading | 色の芯"),
               ("atmosphere", "heat_haze, shimmering_air | 陽炎")),
        ledger=L.blank(), taken=set(),
    )
    assert patch["look"] == "amber_theme, cel_shading"
    assert patch["atmosphere"] == "heat_haze, shimmering_air"


def test_the_field_the_director_named_is_left_alone():
    """**The director wins.** The crew says nothing about a field written that turn."""
    led = {**L.blank(), "light": "backlighting"}
    patch, landed = C.field_land(
        _session(), _floor(("light", "hard_shadow | 硬く")),
        ledger=led, taken={"light"},
    )
    assert patch == {} and landed == {}


def test_the_body_fields_are_never_touched():
    """Pose, expression and clothes are never written — that would break the one-body
    cleanup. A bundled conclusion reaches the writer through `craft_block`."""
    patch, _ = C.field_land(
        _session(),
        _floor(("beat", "standing, hand_on_hip | 姿勢"),
               ("expression", "soft_smile | 顔"),
               ("wearing", "apron | 服")),
        ledger=L.blank(), taken=set(),
    )
    assert patch == {}


def test_what_the_showrunner_refused_does_not_come_back():
    """A word the Showrunner refused is dropped from the corner's conclusion too."""
    s = _session(banned=["towel"])
    patch, _ = C.field_land(
        s, _floor(("bg", "blue_towel, small_stone | 忘れ物")),
        ledger=L.blank(), taken=set(),
    )
    assert "towel" not in patch.get("bg", "")
    assert "small_stone" in patch["bg"]


def test_the_same_thing_is_not_said_twice():
    """**A duplicate that slipped through live** — `silver_spoon` and
    `silver_sugar_spoon` sat together.

    Word boundaries (`talk.word_hit`) do not catch it, so shared words are checked
    as well.
    """
    led = {**L.blank(), "bg": "coffee cup, silver_spoon"}
    patch, landed = C.field_land(
        _session(), _floor(("bg", "silver_sugar_spoon, cheesecake | 卓上")),
        ledger=led, taken=set(),
    )
    assert landed["bg"] == ["cheesecake"]
    assert patch["bg"] == "coffee cup, silver_spoon, cheesecake"


def test_the_conclusion_is_capped_at_six_words():
    tags = ", ".join(f"tag_{i}" for i in range(10))
    patch, landed = C.field_land(
        _session(), _floor(("bg", f"{tags} | 十語出した")),
        ledger=L.blank(), taken=set(),
    )
    assert len(landed["bg"]) == C.FIELD_CONCLUSION_MAX == 6
    assert patch["bg"] == ", ".join(f"tag_{i}" for i in range(6))


def test_a_field_full_of_the_showrunners_words_stops_growing():
    """**The cap promises not to grow the field, not to trim it.**"""
    full = ", ".join(f"thing_{i}" for i in range(C.FIELD_CAP))
    patch, landed = C.field_land(
        _session(), _floor(("bg", "late_arrival | もう入らない")),
        ledger={**L.blank(), "bg": full}, taken=set(),
    )
    assert patch == {} and landed == {}


def test_a_silent_corner_does_not_clear_the_field():
    """A field whose corner wrote no CRAFT (leaving it as it is) does not move, and is
    never emptied."""
    led = {**L.blank(), "look": "amber_theme"}
    patch, _ = C.field_land(
        _session(), _floor(("look", "")), ledger=led, taken=set(),
    )
    assert patch == {}


def test_the_field_label_never_rides_in():
    """Even written as `CRAFT: BG: …`, the field name is stripped at the door
    (`craft_tags`)."""
    patch, _ = C.field_land(
        _session(), _floor(("bg", "BG: paper_menu, worn_edges | 小道具")),
        ledger=L.blank(), taken=set(),
    )
    assert "BG:" not in patch["bg"]
    assert "paper_menu" in patch["bg"]


def test_no_crew_no_landing():
    """Nothing happens on a turn where no seat spoke (a solo shoot and a duet never
    come through here)."""
    assert C.field_land(_session(), [], ledger=L.blank(), taken=set()) == ({}, {})


# ── How one round is put together ───────────────────────────────────────

def test_the_turn_asks_the_crew_after_the_director():
    """**Order matters.** The director's patch is applied first, then the landing (the
    pass-through check depends on it)."""
    import inspect

    from app.muse import service

    src = inspect.getsource(service.chat)
    i_patch = src.index("led = ledger_mod.apply_patch(led, patch)")
    i_fill = src.index("crew_room.field_land(")
    assert i_patch < i_fill, "班の着地は監督のあと"
    assert "taken=set(patch.keys()) | director_sticky" in src
    assert 'debug_mod.note(\n                session, "field_land"' in src, "黙って足さない"


def test_the_field_the_writer_rewrote_belongs_to_the_showrunner_again():
    """**The note for a field the writer rewrote is discarded.** (2026-09-14)

    Left in place, the next corner could treat the director's words as its own and
    erase them.
    """
    import inspect

    from app.muse import service

    src = inspect.getsource(service.chat)
    assert "words = dict(crew_room.crew_words_of(session))" in src
    assert "for key in patch:\n        words.pop(key, None)" in src


def test_one_call_per_field_instead_of_one_per_seat():
    """**Twelve seats become eight calls** — three contested fields bundled, and
    the lead's seat gone.

    Measured, one seat takes about 9-10s (`stage_ms`, `6dc11d0e`) and the lead's
    seat 24-28s.
    """
    import asyncio

    from app.muse import service

    session = service.new_session({"locale": "ja", "model": "m"})
    session["character"] = {"character_id": "c1", "name_ja": "各務 みお"}
    session["inputs"] = {**session["inputs"], "crew_preset": "standard",
                         "banter_mode": "off"}
    session[C.TABLE_OPEN] = True

    calls: list[tuple[str, tuple[str, ...]]] = []

    async def _seat(ollama, sess, muse_id, *, model, prompt):
        calls.append(("seat", (muse_id,)))
        return "SAY: はい。\nCRAFT: one_thing | ひとつ"

    async def _group(ollama, sess, seats, *, field, model, prompt):
        calls.append(("corner", tuple(seats)))
        return "".join(
            f"SPEAKER: {mid}\nSAY: {i} 番目です。\n" for i, mid in enumerate(seats)
        ) + "CRAFT: agreed_one, agreed_two | 会議の結論"

    keep = (C._seat_turn, C._group_turn)
    C._seat_turn, C._group_turn = _seat, _group
    try:
        floor = asyncio.run(C.run_table(None, None, session, director_line="夕方に"))
    finally:
        C._seat_turn, C._group_turn = keep

    assert len(calls) == 8, calls
    assert sum(1 for kind, _ in calls if kind == "corner") == 3
    # **The lead is not in the round (2026-09-16)** — her words arrive at the end of
    # the turn.
    spoke = {m for _, ids in calls for m in ids}
    assert not any(crew.role_of(m) == "actress" for m in spoke), spoke

    # A corner's conclusion goes on **whoever closes** (a field never receives two)
    look = [r for r in floor if r["field"] == "look"]
    assert [r["craft"] for r in look] == ["", "agreed_one, agreed_two | 会議の結論"]
    assert [crew.role_of(r["muse_id"]) for r in look] == ["palette", "ink"]

    patch, _ = C.field_land(session, floor, ledger=L.blank(), taken=set())
    assert patch["look"] == "agreed_one, agreed_two"


def test_the_same_light_under_another_ending_is_not_added_again():
    """**The last duplicate left live (`e805ffac`, 2026-09-15).**

    `light` came out as `rim_lighting, backlighting, eye_glint, rim_light` — the
    writer's `rim_lighting` and the corner's `rim_light` match neither on word
    boundaries nor on shared words. Levelling the endings makes them meet.
    """
    led = {**L.blank(), "light": "rim_lighting, backlighting"}
    patch, landed = C.field_land(
        _session(), _floor(("light", "rim_light, eye_glint | 逆光を締める")),
        ledger=led, taken=set(),
    )
    assert landed["light"] == ["eye_glint"]
    assert patch["light"] == "rim_lighting, backlighting, eye_glint"


def test_two_different_accents_still_both_get_through():
    """**Do not over-trim.** Different things that share a single word pass (settling
    them is the corner’s job)."""
    assert C._too_close("amber_accent", "scarlet_accent") is False
    assert C._too_close("light_particles", "rim_light") is False
    assert C._too_close("cel_shading", "clean_lineart") is False
    assert C._too_close("depth_of_field", "shallow_depth_of_field") is True


# ── The five defects (2026-09-16) ──────────────────────────────────────

def test_the_lead_is_dressed_at_the_studio_door_too():
    """**The lead is dressed at the studio door too.** (2026-09-16)

    The Showrunner: "sometimes the default outfit is not loaded when the first
    conversation starts".

    In studio mode the panel's start button calls `/table`, not `/open`. Dressing
    lived only on the `open_session` side, so **a session started with a crew had
    an empty `wearing`** (live, `f8961eaa`: the ledger on turn one had no clothes
    either).
    """
    import inspect

    from app.muse import service

    src = inspect.getsource(service.open_table)
    assert "talk.dress_from_signature(session)" in src, "班の扉で服を着せていない"
    i_dress = src.index("dress_from_signature")
    i_table = src.index("crew_room.run_table(")
    assert i_dress < i_table, "開幕の三席より前に着せる（衣装の席がその値を見る）"


def test_the_seat_wears_its_nickname_on_the_name_tag():
    """**Make the name tag and the way the seats address each other the same
    word.** (2026-09-16)

    They call each other 「一点さん」 and 「すきま」 (Itten, Sukima — nicknames)
    while the bubble carried the role (色彩設計, colour design). Two people share a
    role (`palette:itten` and `palette:aku`), so there was nothing to tell them
    apart by either.
    """
    s = _session(character={"name_ja": "各務 みお"})
    assert C.seat_name(s, "palette:itten") == "一点（色彩設計）"
    assert C.seat_name(s, "beat:ichibyou") == "一秒（演出）"
    # The lead keeps the name she was cast under
    cast = [m for m in crew.resolve_crew(preset="standard")
            if crew.role_of(m) == "actress"][0]
    assert C.seat_name(s, cast) == "各務 みお"


def test_the_seat_name_tag_follows_the_session_locale():
    s = _session(
        character={"name_ja": "各務 みお", "name": "Mio Kagami"},
        inputs={"locale": "en"},
    )
    assert C.seat_name(s, "palette:itten") == "Palette (Colour Designer)"
    assert C.seat_name(s, "beat:ichibyou") == "Beat (Director)"
    cast = [m for m in crew.resolve_crew(preset="standard")
            if crew.role_of(m) == "actress"][0]
    assert C.seat_name(s, cast) == "Mio Kagami"


def test_the_lead_keeps_her_seat_at_the_opening():
    """Out of the walk, but **still in the opening rough-in** (wardrobe → camera →
    lead)."""
    cast = crew.resolve_crew(preset="standard")
    opening = [crew.role_of(m) for m in C.opening_seats(cast)]
    assert opening == ["wardrobe", "lens", "actress"]
    walk = [crew.role_of(m) for m in C.writing_seats(cast, without=("actress",))]
    assert "actress" not in walk


def test_a_decorated_speaker_line_still_switches_the_bubble():
    """**A decorated name tag still switches the address.** (2026-09-16)

    Written as `**SPEAKER: …**` the line starts with `*`, so it never grew into a
    field name and the label flowed, label and all, into the previous seat's
    bubble (the Showrunner: "tags like SAY leak", "the Muses' conversations get
    mixed up").
    """
    seen: dict[str, list[str]] = {}

    def _fake_stream_to(session, muse_id):
        return lambda text: seen.setdefault(muse_id, []).append(text)

    original, C._stream_to = C._stream_to, _fake_stream_to
    try:
        feed = C._packed_stream(_session(session_id="s"),
                                ["palette:itten", "ink:ipponsen"])
        for ch in ("**SPEAKER: palette:itten**\nSAY: 琥珀です。\n"
                   "  - SPEAKER: ink:ipponsen\nSAY: 線を締めます。\n"):
            feed(ch)
    finally:
        C._stream_to = original

    got = {k: "".join(v) for k, v in seen.items()}
    assert "琥珀です。" in got["palette:itten"]
    assert "線を締めます。" in got["ink:ipponsen"]
    assert "琥珀" not in got.get("ink:ipponsen", "")
    for text in got.values():
        assert "SPEAKER" not in text


def test_an_unknown_name_walks_the_seats_instead_of_piling_on_the_first():
    """**An unmatched name tag still walks the seats in order.** (2026-09-16)

    `used` was passed empty, so an unmatched name fell to `seats[0]` every time
    and **the second speaker's words piled into the first speaker's bubble**.
    """
    seen: dict[str, list[str]] = {}
    session = _session(session_id="s")

    def _fake_stream_to(_s, muse_id):
        return lambda text: seen.setdefault(muse_id, []).append(text)

    original, C._stream_to = C._stream_to, _fake_stream_to
    try:
        feed = C._packed_stream(session, ["palette:itten", "ink:ipponsen"])
        for ch in ("SPEAKER: ???\nSAY: 一人目。\nSPEAKER: ???\nSAY: 二人目。\n"):
            feed(ch)
    finally:
        C._stream_to = original

    got = {k: "".join(v) for k, v in seen.items()}
    assert "一人目。" in got["palette:itten"]
    assert "二人目。" in got["ink:ipponsen"], "二人目が一人目に積まれている"
    # Never wrong silently
    notes = [n for n in (session.get("refine_log") or [])
             if n.get("kind") == "corner_speaker_miss"]
    assert len(notes) == 2, notes


def test_the_speaker_label_also_shuts_the_say_gate():
    """The backstop when it is missed — a `SPEAKER:` line closes the bubble too."""
    from app.muse import shared

    assert shared._SAY_SHUT_RE.match("SPEAKER: palette:itten")
    assert shared._SAY_SHUT_RE.match("**SPEAKER: palette:itten")


def test_the_nickname_is_what_the_seats_call_each_other():
    """A name tag written as a nickname still matches (the model writes in Japanese)."""
    seats = ["palette:itten", "ink:ipponsen"]
    assert C._match_speaker("一点", seats, []) == ("palette:itten", True)
    assert C._match_speaker("色彩設計", seats, []) == ("palette:itten", True)
    assert C._match_speaker("2", seats, []) == ("ink:ipponsen", True)
    assert C._match_speaker("だれか", seats, ["palette:itten"]) == ("ink:ipponsen", False)


def test_a_hyphenated_id_still_finds_its_seat():
    """**A wobble in the separator must not mis-seat anyone.** (2026-09-18)

    Live (`c62274f6`) the model wrote `cut-out:sukima`, which matched no id and
    fell through to "the first seat that has not spoken". It happened to be right,
    but that left it **to the luck of seat order**.
    """
    seats = ["cutout:sukima", "lens:pinto"]
    assert C._match_speaker("cut-out:sukima", seats, []) == ("cutout:sukima", True)
    assert C._match_speaker("cut out", seats, []) == ("cutout:sukima", True)
    assert C._match_speaker("LENS", seats, []) == ("lens:pinto", True)

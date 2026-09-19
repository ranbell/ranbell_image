"""**Carrying the studio shoot (the crew) onto Refine.** (2026-09-11)

The Showrunner: "I want the studio shoot (the mode with several crew members)
inside Muse refine", "port it straight from classic first, then polish", "keep all
18 roles".

In classic the seats were talk-only and one Scripter did the writing. In Refine
`writer.write_patch` sits in that chair — so the notebook is not needed.

**And the solo shoot must not break.** The gate is a single mark: did the
Showrunner open the table?

**The default is now empty (2026-09-13)** — the Showrunner: "make the studio
shoot default to nothing, so you cannot shoot without choosing". It used to take
`"standard"` from `ALL_DEFAULTS`, and gating on "are there seats" ran sixteen of
them in a solo shoot. Now there are two guards (an empty default, and the mark).
"""
from __future__ import annotations

from app.muse import crew
from app.muse import crew_room as C
from app.muse import ledger as L, service


def _session(*, crew_preset: str = "standard", **kw):
    """A test that uses a crew **chooses one explicitly**. The default is empty, so
    without choosing there are no seats to build."""
    s = service.new_session({"locale": "ja", "model": "m"})
    s["character"] = {"character_id": "c1", "name_ja": "各務 みお", "name": "Mio"}
    if crew_preset:
        s["inputs"] = {**s["inputs"], "crew_preset": crew_preset}
    s.update(kw)
    return s


# ── The gate ────────────────────────────────────────────────────────────
def test_a_plain_session_has_no_crew():
    """**A session that chose nothing has no seats at all.** (2026-09-13)

    With the default empty, a crew only walks after both steps — choosing and
    opening.
    """
    bare = service.new_session({"locale": "ja", "model": "m"})
    assert bare["inputs"].get("crew_preset") == ""          # the default is empty
    assert C.cast_of(bare) == []                            # no seats can be formed
    assert C.has_crew(bare) is False

    chosen = _session()                                     # a crew merely chosen
    assert C.cast_of(chosen)                                # the seats can be formed
    assert C.has_crew(chosen) is False                      # nothing runs until it is opened


def test_the_table_opens_only_when_it_is_opened():
    s = _session()
    s[C.TABLE_OPEN] = True
    assert C.has_crew(s) is True


def test_an_open_flag_without_a_cast_is_still_no_crew():
    s = _session()
    s[C.TABLE_OPEN] = True
    s["inputs"] = {**s["inputs"], "crew_preset": "", "crew_ids": []}
    assert C.has_crew(s) is False


def test_the_chat_turn_asks_the_gate_not_the_mode():
    import inspect
    src = inspect.getsource(service.chat)
    assert "crew_room.has_crew(session)" in src
    assert "is_duet" not in src


# ── The seats ───────────────────────────────────────────────────────────
def test_all_eighteen_roles_are_kept():
    """The Showrunner: "keep all 18 roles"."""
    s = _session()
    cast = C.cast_of(s)
    assert len({crew.role_of(m) for m in cast}) == len(crew.ROLE_ORDER) == 18


def test_five_seats_have_no_pen():
    """The pens classic took away stay taken away (`NOTE_MUTED`)."""
    s = _session()
    cast = C.cast_of(s)
    pens = {crew.role_of(m) for m in C.writing_seats(cast)}
    for muted in ("continuity", "gate", "finisher", "grade", "hook"):
        assert muted not in pens, muted
    assert "plan" not in pens          # composition takes another road
    assert len(pens) == 12


def test_the_opening_dresses_her_before_framing_her():
    """Wardrobe → camera → lead. Dressing order, not seat order (measured in
    classic)."""
    s = _session()
    got = [crew.role_of(m) for m in C.opening_seats(C.cast_of(s))]
    assert got == ["wardrobe", "lens", "actress"]


def test_every_seat_with_a_slot_owns_a_ledger_field():
    for role, slot in crew.CRAFT_SLOTS.items():
        field = C.SLOT_FIELD.get(slot)
        assert field, f"{role} の slot {slot} に欄が無い"
        assert field in L.LEDGER_KEYS, f"{field} は台帳の欄ではない"


def test_two_seats_may_share_a_field():
    """Staging and choreography are both the body; layout and camera are both the
    frame. Classic was the same."""
    assert C.SLOT_FIELD["BODY"] == "beat"
    assert C.SLOT_FIELD["SHAPE"] == C.SLOT_FIELD["OPTICS"] == "frame"


# ── The seats' replies ──────────────────────────────────────────────────
def test_the_craft_line_is_split_off():
    say, craft = C.split_craft(
        "うん、逆光でいきましょう。\nCRAFT: backlighting, rim_light | low sun"
    )
    assert "逆光" in say
    assert "CRAFT" not in say
    assert craft == "backlighting, rim_light | low sun"


def test_a_seat_that_omits_craft_just_talks():
    say, craft = C.split_craft("今日はこのままでいいと思います。")
    assert craft == ""
    assert say == "今日はこのままでいいと思います。"


def test_omit_words_count_as_no_craft():
    for word in ("none", "-", "omit", "(omit)"):
        _, craft = C.split_craft(f"はい。\nCRAFT: {word}")
        assert craft == "", word


# ── The material for the writer ─────────────────────────────────────────
def test_only_the_tag_half_reaches_the_ledger():
    """The prose half of `CRAFT: <tags> | <prose>` never reaches the ledger.

    Handed through live, it put a pipe and Japanese into the field and the halves
    bled across fields (`light` ended up holding
    `translucent_fabric | 襟が夕陽を透かす`).
    """
    assert C.craft_tags("backlight, rim_light | golden hour, warm") == "backlight, rim_light"
    assert C.craft_tags("sitting") == "sitting"
    assert C.craft_tags("") == ""


def test_the_craft_is_grouped_by_field_not_interleaved():
    """**One line per field.** Interleaved, the writer is left unsure which to
    take."""
    got = C.craft_block([
        {"name": "照明", "role": "gaffer", "field": "light", "craft": "rim_light | low sun"},
        {"name": "演出", "role": "beat", "field": "beat", "craft": "sitting | weight left"},
        {"name": "振付", "role": "spine", "field": "beat", "craft": "leaning, sitting | elbows"},
        {"name": "やじ", "role": "hook", "field": "", "craft": ""},
    ])
    assert got.index("beat:") < got.index("light:")        # the ledger's field order
    assert "beat: sitting, leaning" in got                 # one field folds into one line
    assert "low sun" not in got                            # the prose side is not handed over
    assert "やじ" not in got                               # a line with no craft does not get in


def test_a_field_never_repeats_a_tag():
    got = C.craft_block([
        {"name": "演出", "role": "beat", "field": "beat", "craft": "sitting, calm"},
        {"name": "振付", "role": "spine", "field": "beat", "craft": "SITTING, leaning"},
    ])
    assert got.count("sitting") + got.count("SITTING") == 1


def test_the_seat_format_overrides_the_classic_one():
    """Classic's OUTPUT (TAGS/SCENE) follows the specialty text. Ours overrides it
    last."""
    assert "REPLACES any format above" in C.SEAT_OUTPUT
    assert "CRAFT:" in C.SEAT_OUTPUT
    # TAGS appear **only as a prohibition** (they are not asked for)
    assert "Never write a TAGS: or SCENE: block" in C.SEAT_OUTPUT
    import inspect
    src = inspect.getsource(C._seat_turn)
    assert "SEAT_OUTPUT" in src and "system_prompt_for" in src
    assert src.index("system_prompt_for") < src.index("SEAT_OUTPUT"), "上書きは後ろ"


def test_no_crew_means_no_block():
    assert C.craft_block([]) == ""
    assert C.craft_block([{"name": "やじ", "role": "hook", "field": "", "craft": ""}]) == ""


def test_the_writer_only_hears_the_crew_when_there_is_one():
    """**Not one character of the solo contract moves.** The rules reach only the
    turns where a crew spoke."""
    import inspect
    from app.muse import writer
    assert "THE CREW SPOKE" not in writer.WRITER_SYSTEM
    src = inspect.getsource(writer.write_patch)
    assert "crew_craft" in src
    assert 'if str(crew_craft or "").strip() else ""' in src


# ── The notebook's field name leaks through (live, 2026-09-11) ──────────
def test_a_notebook_label_never_reaches_the_ledger():
    """A seat's specialty text explains `BEAT` and `WEARING` by name, so the model
    copies them.

    Live, the ledger ended up holding:

        wearing: "BEAT: standing still, eyes towards the light"
        bg:      "ATMOSPHERE:"        ← not even a value

    The contract forbids it too, but **it is also dropped before it arrives**. The
    ledger's cleanliness is not left to the model's manners (the flip side of
    [[feedback-a-box-or-it-wont-land]]).
    """
    assert C.craft_tags("BEAT: standing still, eyes towards the light | 重心") \
        == "standing still, eyes towards the light"
    assert C.craft_tags("WEARING: BEAT: sitting | x") == "sitting"   # stripped even when stacked
    assert C.craft_tags("ATMOSPHERE:") == ""                          # an empty label disappears
    assert C.craft_tags("ATMOSPHERE: | dusty air") == ""


def test_an_ordinary_tag_that_looks_like_a_label_survives():
    """`atmospheric` is not a field name. Without a colon, nothing is stripped."""
    assert C.craft_tags("atmospheric, dusty") == "atmospheric, dusty"
    assert C.craft_tags("backlight, rim_light") == "backlight, rim_light"


def test_an_empty_craft_makes_no_line():
    assert C.craft_block([
        {"name": "美術", "role": "propshop", "field": "bg", "craft": "ATMOSPHERE:"},
    ]) == ""


def test_the_seat_contract_forbids_labels_too():
    assert "No field label inside CRAFT" in C.SEAT_OUTPUT


def test_the_ledger_door_strips_labels_whoever_knocked():
    """**A ledger value must never begin with a field name.** (2026-09-11)

    It came from more than one place — not only the crew's CRAFT but the actress's
    CARD (`persona.card_to_patch`) and the writer's JSON all arrive with a label
    at the head. `wearing: "BEAT: standing by the railing…"` kept surviving live
    because only the crew's road had been closed. **There is one door:
    `normalize_patch`.**
    """
    from app.muse import persona

    got = L.normalize_patch({"wearing": "BEAT: standing by the railing", "bg": "ATMOSPHERE:"})
    assert got["wearing"] == "standing by the railing"
    assert got["bg"] == ""                                  # scrub throws the empty one away

    # The same through the actress's CARD
    card = L.normalize_patch(persona.card_to_patch("WEARING: BEAT: standing, silhouette"))
    assert card["wearing"] == "standing, silhouette"


def test_a_value_that_merely_looks_like_a_label_survives():
    got = L.normalize_patch({
        "atmosphere": "atmospheric, dusty",
        "scene": "cafe: the corner table",
    })
    assert got["atmosphere"] == "atmospheric, dusty"
    assert got["scene"] == "cafe: the corner table"


def test_there_is_only_one_label_stripper():
    """Keep two and they drift. The crew uses the ledger's."""
    import inspect
    assert not hasattr(C, "_LABEL_HEAD_RE")
    assert "ledger_mod.strip_field_label" in inspect.getsource(C.craft_tags)


# ── The screen's wiring ─────────────────────────────────────────────────
def test_the_panel_is_told_whether_the_table_is_open():
    """Which button shows is decided by two fields of the public view."""
    s = _session()
    v = service.public_view(s)
    assert v["crew_open"] is False and v["crew_seats"] == 0
    s[C.TABLE_OPEN] = True
    v = service.public_view(s)
    assert v["crew_open"] is True and v["crew_seats"] == 18


def test_the_crew_presets_come_from_the_catalogue_not_the_panel():
    """Written into the panel, they drift silently the day a seat is added."""
    panel = __import__("pathlib").Path(
        "frontend/src/components/MusePanel.vue"
    ).read_text(encoding="utf-8")
    assert "catalog.value?.crew?.presets" in panel
    for name in ("photoreal", "vivid", "calm"):
        assert f"'{name}'" not in panel, f"{name} が画面に直書きされている"


def test_the_seat_rows_have_their_own_look():
    """Eighteen people speak, so they must not look like her lines."""
    panel = __import__("pathlib").Path(
        "frontend/src/components/MusePanel.vue"
    ).read_text(encoding="utf-8")
    for fn in ("isSeatRow", "isHeckleRow"):
        assert f"function {fn}(row)" in panel
    assert "'seat'" in panel and "'heckle'" in panel


def test_the_mode_is_chosen_before_the_session_opens():
    """The Showrunner: "director-only versus studio shoot should be a choice like
    Muse Classic's UI".

    A crew cannot be called in mid-session (classic's design is three opening
    seats roughing it in, then the whole crew), so **the roads part at the start
    door**.
    """
    panel = __import__("pathlib").Path(
        "frontend/src/components/MusePanel.vue"
    ).read_text(encoding="utf-8")
    # The text goes through a template literal (`t(\`museRefine.${m.k}\`)`), so it is
    # checked by key
    assert "k: 'modeSolo'" in panel and "k: 'modeStudio'" in panel
    # The start button decides which door
    assert "shootMode.value === 'studio' && !tableOpen.value ? 'table' : 'open'" in panel
    # Once open, it cannot be chosen again
    assert ':disabled="chatLocked || opened"' in panel


def test_the_seat_rows_do_not_show_the_say_label():
    """The Showrunner: "in the studio shoot, SAY: shows up"."""
    say, craft = C.split_craft("SAY: 総監督、いいですね。\nCRAFT: rim_light | low sun")
    assert say == "総監督、いいですね。"
    assert not say.startswith("SAY")
    assert craft == "rim_light | low sun"


def test_the_stream_stops_before_the_craft_line():
    """No danbooru words on screen while it streams, either."""
    from app.muse import shared

    out = []
    feed = shared._say_only(out.append)
    for ch in "SAY: 総監督、いいですね。\nCRAFT: rim_light | low sun\n":
        feed(ch)
    got = "".join(out)
    assert "いいですね" in got
    assert "rim_light" not in got and "CRAFT" not in got


def test_the_seats_stream_too():
    """The Showrunner: "without a streaming display the wait feels very long"."""
    import inspect
    assert "on_token=_stream_to(session, muse_id)" in inspect.getsource(C._seat_turn)
    assert "on_token=_stream_to(session, muse_id)" in inspect.getsource(C._banter_turn)


# ── The crew's look reaches the picture (2026-09-13) ────────────────────

def test_the_crew_look_reaches_the_picture_only_when_the_table_is_open():
    """**The gate is whether a crew actually exists.** (2026-09-13)

    `runtime.style_for` branched on `mode == "duet"`. Every Refine session gets
    `duet` from `new_session`, so **it never entered the branch that averages the
    crew** and all six presets came out as `anime illustration` — the same picture
    whether you chose `photoreal` or `flat`. The same rut as
    [[project-refine-as-muse]]'s "do not gate on `is_duet()`", in another place.
    """
    from app.muse import runtime

    solo = _session(crew_preset="")
    assert runtime.style_for(solo) == crew.NEUTRAL_LOOK

    looks = {}
    for preset in crew.PRESETS:
        s = _session(crew_preset=preset)
        s[C.TABLE_OPEN] = True
        looks[preset] = runtime.style_for(s)
    assert len(set(looks.values())) >= 4, f"班ごとに分かれていない: {looks}"
    assert "semi-realistic" in looks["photoreal"], looks["photoreal"]
    assert "flat" in looks["flat"], looks["flat"]

    # The solo shoot is unchanged — a session with no crew stays neutral
    assert runtime.style_for(_session(crew_preset="photoreal")) == crew.NEUTRAL_LOOK


def test_the_seat_keeps_its_own_way_of_opening():
    """That the block which keeps a seat's voice is in the seat preamble.
    (2026-09-13)

    Measured, **46% of seat lines opened with 「総監督、」 ("Showrunner,") and 42%
    began with the same four characters**. The cause was that removing
    `crew.OUTPUT` the day before took its SAY block with it. What came back is 380
    characters (the charm instruction plus the ban on repeating the last opening),
    and **the time is unchanged**.
    """
    import inspect

    assert "Do NOT begin the way the last speaker began" in C.SEAT_VOICE
    assert "ENTERTAINMENT" in C.SEAT_VOICE
    src = inspect.getsource(C._seat_turn)
    assert "SEAT_VOICE" in src and "SEAT_OUTPUT" in src, "席に届いていない"
    # Not added to the actress's preamble (the record of truth for a solo shoot)
    assert C.SEAT_VOICE not in crew.actress_system_prompt({"name": "Mio"})

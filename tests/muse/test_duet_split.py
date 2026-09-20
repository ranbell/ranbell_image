"""**With two people, split the talk and the mutters.** (2026-09-10)

The Showrunner: "when two are talking in Muse Refine the conversation is not being
separated. Please fix it, taking Muse Classic as the reference", "the mutters too,
one or the other at random", "it calls Mio Asahi and so on".

Live (`83d31174`) all 25 lines collapsed into one line under the lead's name, with
私 (Mio's "I") and アタシ (Asahi's "I") sharing a body of text. The splitter
(`identity.parse_duet_speakers`) had been called from the start — **only the
instruction to write the prefix never arrived**: it lived at the tail of classic's
output format, which `_without_classic_output` drops.

**And the solo conversation must not break.** That is the Showrunner's first
constraint, so the solo side is measured explicitly for "none of it is there".
"""
from __future__ import annotations

from app.muse import persona, talk

A = {"character_id": "a", "name": "Mio", "name_ja": "各務 みお",
     "first_person_ja": "私", "identity_tags": ["silver_hair", "bob_cut"],
     "personality": {"traits": ["おだやか"]}}
B = {"character_id": "b", "name": "Asahi", "name_ja": "倉田 あさひ",
     "first_person_ja": "アタシ", "identity_tags": ["light_green_hair", "hair_up"],
     "personality": {"traits": ["元気"]}}


def _session(*, partner: bool):
    return {
        "session_id": "s1", "character": dict(A),
        "partner_character": dict(B) if partner else {},
        "inputs": {"locale": "ja"}, "chat": [], "opened": True,
        "memories": [], "bond": {}, "caught": {}, "showrunner_taste": {},
        "chemistry_notes": [], "standing": [],
    }


# ── The contract ────────────────────────────────────────────────────────
def test_the_duet_contract_asks_for_labels():
    got = persona.actress_system(
        _session(partner=True), locale="ja", ledger={}, now="",
    )
    assert "W-MUSE" in got
    assert "`A:` or `B:`" in got
    assert "only ONE of them mutters" in got
    # Tied by name so the first person is not mixed up
    assert "私" in got and "アタシ" in got


def test_a_solo_shoot_never_sees_any_of_it():
    """**The solo conversation does not break.** Not one word of the duet gets in."""
    got = persona.actress_system(
        _session(partner=False), locale="ja", ledger={}, now="",
    )
    for leak in ("W-MUSE", "`A:` or `B:`", "mutters", "CONTRAST VOICES",
                 "You play BOTH"):
        assert leak not in got, leak


def test_the_gate_is_the_partner_not_the_mode():
    """Gate on `mode` and solo shoots turn into duets too (85 of 109 live sessions)."""
    s = _session(partner=False)
    s["mode"] = "duet"           # this is how it is live
    assert "W-MUSE" not in persona.actress_system(s, locale="ja", ledger={}, now="")


# ── Where they are split ────────────────────────────────────────────────
def _publish(say: str, aside: str = "", *, partner: bool = True):
    s = _session(partner=partner)
    talk.publish_actress_turn(
        s, {"say": say, "aside": aside, "propose": {}, "my_feel": "", "pitch": ""},
        locale="ja", lead_name="各務 みお",
    )
    return s["chat"]


def test_labelled_lines_become_two_rows():
    rows = _publish("A: 準備できました。\nB: アタシもいけるよ！")
    says = [r for r in rows if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 2
    assert says[0]["name"] == "各務 みお" and says[0]["meta"]["speaker"] == "A"
    assert says[1]["name"] == "倉田 あさひ" and says[1]["meta"]["speaker"] == "B"
    assert says[0]["meta"]["speaker_id"] == "a"
    assert says[1]["meta"]["speaker_id"] == "b"
    # The other person's lines are not mixed into the body
    assert "アタシ" not in says[0]["text"]
    assert "準備できました" not in says[1]["text"]


def test_the_mutter_belongs_to_whoever_muttered():
    """If the mutter is `B:`, it appears under the partner's name (the Showrunner: "one
    or the other at random")."""
    rows = _publish("A: どうぞ。\nB: はいはい。", aside="B: （……緊張してるのかな）")
    banter = [r for r in rows if (r.get("meta") or {}).get("kind") == "banter"]
    assert len(banter) == 1
    assert banter[0]["name"] == "倉田 あさひ"
    assert banter[0]["meta"]["speaker"] == "B"
    assert banter[0]["meta"]["speaker_id"] == "b"
    assert not banter[0]["text"].startswith("B:")


def test_an_unlabelled_turn_still_lands_on_the_lead():
    """A turn that ignored the format still does not fail silently — one line under the
    lead's name, as before."""
    rows = _publish("準備できました。")
    says = [r for r in rows if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 1
    assert says[0]["name"] == "各務 みお"


def test_a_solo_turn_is_never_split():
    """An `A:` on a turn with no partner is not split into two."""
    rows = _publish("A: ひとりです。", partner=False)
    says = [r for r in rows if (r.get("meta") or {}).get("kind") == "say"]
    assert len(says) == 1
    assert says[0]["name"] == "各務 みお"

"""Five seats, disjoint slots, and no personas.

The roster this replaced had seventeen jobs, two people each, and taste axes
that averaged into a house style. It lost: every seat rewrote the whole prompt,
so adding seats made the picture worse rather than better. These tests pin the
properties that made the new shape necessary.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import crew

# Situation nouns must never be baked into Muse production copy.
# Themes come from the Showrunner + VLM dialogue — not from code samples.
_SITUATION_BANNED = (
    "水着", "ビキニ", "パラソル", "カフェ", "泳ぐ", "暑さ", "海辺", "ビーチ",
    "懐中電灯", "スタッフベスト", "砂ベージュ", "ターコイズ", "真夏",
    "屋上", "雨上がり",
    "sexy", "sensual", "swimsuit", "bikini", "parasol", "beach", "seaside",
    "flashlight", "rooftop", "wet/dry", "wet_swimsuit", "beach_cafe",
    "thermos", "coffee",
)


def test_the_crew_is_five_seats_in_a_fixed_order():
    assert crew.ROLE_ORDER == ("plan", "actress", "enrich", "reduce", "check")
    assert crew.resolve_crew() == list(crew.ROLE_ORDER)
    # There is no crew to pick any more; the argument is accepted and ignored.
    assert crew.resolve_crew(preset="anything", crew_ids=["nonsense"]) == list(crew.ROLE_ORDER)


def test_every_slot_has_exactly_one_owner():
    """The whole reason for the rewrite: when everyone could write everything,
    the objects named at seat five were gone by seat seventeen."""
    owners: dict[str, list[str]] = {}
    for rid in crew.ROLE_ORDER:
        for slot in crew.ROLES[rid]["owns"]:
            owners.setdefault(slot, []).append(rid)
    # `objects` is shared on purpose: the planner seeds the ledger, enrich adds.
    for slot, who in owners.items():
        assert len(who) == 1 or slot == "objects", f"{slot} written by {who}"
    assert set(crew.SLOT_OWNER) <= set(crew.SLOT_ORDER)


def test_the_seats_that_only_cut_own_nothing():
    """Reduce and check must not be able to write, or the loop has two authors
    for the same words and drifts the way the old chain did."""
    assert crew.ROLES["reduce"]["owns"] == ()
    assert crew.ROLES["check"]["owns"] == ()
    assert "REMOVE" in crew.FIELDS["reduce"]
    assert set(crew.FIELDS["reduce"]) == {"REMOVE"}


def test_no_seat_is_told_that_contrast_is_good():
    """Lighting and colour design were the measured culprits: one was told to
    'forbid flat even lighting', the other sank the frame to make an accent
    pop, and nothing downstream could undo either."""
    # Only the job descriptions: NO_PUSHING names these phrases in order to ban
    # them, so scanning it would be self-defeating.
    low = "\n".join(r["specialty"] for r in crew.ROLES.values()).lower()
    for phrase in ("vivid contrast", "forbid flat", "dramatic shadow",
                   "carve negative space", "deeper shadow"):
        assert phrase not in low, phrase
    # And the ban has to name the Japanese too — the old one listed only
    # English words while the seats were speaking Japanese.
    for word in ("深く", "沈める", "もっと", "研ぎ澄ます"):
        assert word in crew.NO_PUSHING, word


def test_the_seats_that_can_add_light_are_told_not_to_push_it():
    for rid in ("plan", "enrich"):
        assert crew.NO_PUSHING in crew.system_prompt_for(rid)
    # Reduce is deliberately NOT given it: it has to be free to cut light words.
    assert crew.NO_PUSHING not in crew.system_prompt_for("reduce")


def test_say_asks_for_a_reason_a_decision_and_its_effect():
    """Plain speech, not thin speech. The camera seat in the old roster was the
    one worth keeping: it answered with a reason, decided one thing, and said
    what that did to the picture."""
    spec = crew.SAY_SPEC
    assert "REASON" in spec
    assert "ONE concrete decision" in spec
    assert "what that decision does to the picture" in spec
    # And no personas.
    assert "catchphrase" in spec.lower()


def test_no_seat_carries_a_persona():
    for rid in crew.ROLE_ORDER:
        r = crew.ROLES[rid]
        for gone in ("say_examples", "voice", "voice_ja", "line", "line_ja",
                     "taste", "flavor_tags", "nick", "nick_ja"):
            assert gone not in r, f"{rid} still carries {gone}"
    for gone in ("PRESETS", "TASTE_AXES", "style_direction", "DEFAULT_PRESET",
                 "BANTER_OUTPUT", "actress_banter_prompt"):
        assert not hasattr(crew, gone), gone


def test_every_seat_gets_an_output_format_it_can_be_parsed_from():
    for rid in crew.ROLE_ORDER:
        text = crew.system_prompt_for(rid, character={"personality": {}})
        assert "OUTPUT FORMAT" in text
        assert "SAY:" in text
        for label in crew.FIELDS[rid]:
            assert f"{label}:" in text, f"{rid} never names {label}"


def test_the_lead_is_driven_by_traits_and_fenced_from_her_backstory():
    character = {
        "name": "Sample Lead", "name_ja": "サンプル主演",
        "personality": {
            "traits": ["quiet", "stubborn"],
            "summary_ja": "いつも本気で話す。",
            "inner_ja": ["ひとりのとき少し静かになる"],
            "likes": ["clear explanations"],
        },
        "expression_vocab": ["smile"], "gesture_vocab": ["looking_up"],
    }
    text = crew.system_prompt_for("actress", character=character)
    assert "サンプル主演" in text
    assert "quiet" in text and "smile" in text
    # The inner life is present but explicitly fenced to tone.
    assert "ひとりのとき少し静かになる" in text
    assert "TONE ONLY" in text
    assert "Do not narrate your past" in text


def test_the_planner_is_told_the_light_must_be_readable():
    import re
    text = re.sub(r"\s+", " ", crew.system_prompt_for("plan"))
    assert "State a LEVEL a person could read the picture by" in text
    assert "her history is not a location" in text.lower()


def test_the_checker_may_not_argue_with_the_measurements():
    """A VLM handed a 66%-black frame called it 'artistically correct'. The
    numbers decide; the model only explains and prescribes."""
    text = crew.system_prompt_for("check")
    assert "facts, not opinions" in text
    assert "do not" in text.lower() and "intentional" in text.lower()


def test_production_muse_copy_has_no_situation_specific_anchors():
    """Any theme must work — forbid demo/situation nouns in shipped Muse text."""
    import json

    root = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse"
    blobs: list[str] = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".md"} or "__pycache__" in path.parts:
            continue
        blobs.append(path.read_text(encoding="utf-8"))
    locales = Path(__file__).resolve().parents[2] / "frontend" / "src" / "locales"
    for name in ("ja.json", "en.json"):
        data = json.loads((locales / name).read_text(encoding="utf-8"))
        blobs.append(str((data.get("muse") or {}).get("themePlaceholder") or ""))
    joined = "\n".join(blobs)
    for banned in _SITUATION_BANNED:
        assert banned not in joined, f"situation-specific '{banned}' in Muse copy"


def test_public_roster_names_five_seats_and_the_lead():
    roster = crew.public_roster({"name": "Sample", "name_ja": "サンプル"})
    assert [r["id"] for r in roster["roles"]] == list(crew.ROLE_ORDER)
    lead = next(r for r in roster["roles"] if r["id"] == "actress")
    assert lead["name_ja"] == "サンプル"
    assert roster["slot_order"] == list(crew.SLOT_ORDER)

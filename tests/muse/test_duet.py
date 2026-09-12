"""主演撮り (lead shoot) — one or two Muses with the Showrunner, no crew.

Talking writes the notebook. Tags are woven on 「撮影？」, just before the take.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import crew, shared, session_db
from tests.muse.test_service import FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)


# A whole shot, in the parts the prep turn now writes.
_OPENING = """SAY: わかりました。机の上にコーラの缶、脱ぎっぱなしの上着。
PLACE TAGS: messy_room, indoors
PLACE: A small cluttered room, she is on the floor by the low table.
HOUR TAGS: night
HOUR: Late at night.
LIGHT TAGS: lamplight
LIGHT: One warm lamp in the corner.
PROPS TAGS: cola_can, thick_carpet, low_table, magazine
PROPS: A cola can on the table, a thick carpet, magazines everywhere.
COSTUME TAGS: white_shirt, pants
COSTUME: She wears a loose white shirt and pants.
POSE TAGS: sitting
POSE: She sits on the carpet with her weight back on one hand.
EXPRESSION TAGS: smile
EXPRESSION: A small tired smile.
CAMERA TAGS: from_front, upper_body
CAMERA: A level shot from the front, waist up.

COSTUME:
SILHOUETTE: relaxed loungewear
LAYERS: a single shirt
COLOURWAY: white, navy
PATTERN: solid
FABRIC: cotton
CONDITION: worn-in
HERO: the white shirt
GARMENTS: top=white_shirt / bottom=pants / feet=none / extras=none
"""


_SCRIPTER_OPENING = """
INTENT: shot
ATMOSPHERE: late-night karaoke alone
SCENE: messy karaoke room with cola cans and a mic
FRAME: eye level, looking at viewer
WEARING: white shirt, pants
BEAT: sitting on the edge of the couch
VIBE: quiet
OPEN:
CLEAR_OPEN: no
STANDING: none
UNCHANGED: none
TAGS: messy_room, cola_can, microphone, white_shirt, pants, sitting, looking_at_viewer
CRAFT_SCENE: A messy karaoke room at night; she sits on the couch edge in a white shirt and pants, cola cans nearby.
""".strip()


class TalkingOllama(FakeOllama):
    """Answers a conversation turn with a line; scripter seeds a shot on prep."""

    def __init__(self, routes=None, scripts=None):
        super().__init__()
        self.routes = routes or {}
        self.scripts = scripts or {}

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        text = "SAY: えっと……その場所なら、私はたぶん端っこに座っちゃうかも。コーラの缶、足元にあるね。"
        if "studio scripter" in system or "shot notebook" in system:
            text = next(
                (v for k, v in self.scripts.items() if k in str(prompt)),
                _SCRIPTER_OPENING,
            )
        elif "eight parts" in system:
            text = next(
                (v for k, v in self.routes.items() if k in str(prompt)),
                "FACETS: none\nSTANDING: none",
            )
        elif "YOU ARE THE WHOLE CREW TODAY" in system:
            text = _OPENING

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()




async def _duet_session(db, **over):
    session = await shared.create_session(db, {
        "theme": "深夜のカラオケで一人", "character_id": "c1",
        "workflow": "w.json", "model": "m", "mode": "duet", **over,
    })
    session["character"] = {"identity_tags": ["1girl", "silver_hair"],
                            "personality": {}, "palette": [], "signature_prop": ""}
    await session_db.save(db, session)
    return session


def test_she_is_told_she_is_the_whole_crew_when_getting_ready():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="prep")
    assert "TEN OR MORE OBJECTS" in text
    assert "ONE CAMERA" in text
    assert "no planner, no camera, no wardrobe, no lighting" in text
    # And she clears the set herself when direction changes — newest wins.
    assert "WHEN THE SHOWRUNNER CHANGES ANYTHING" in text
    assert "newest words beat" in text.lower()
    assert "NAME THE THINGS" in text or "about ten" in text


def test_the_talking_prompt_writes_nothing_down():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk")
    assert "SAY" in text
    assert "No tags" in text or "no tags" in text.lower()
    assert "TAGS" in text and "SCENE" in text
    assert "TEN OR MORE OBJECTS" not in text


def test_talk_prompt_is_not_an_interview_bot():
    """Ask things / get-ready-whenever used to make her a prep-checklist assistant."""
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk")
    low = text.lower()
    assert "ask things" not in low
    assert "whenever they want" not in low
    assert "how each turn works" not in low
    assert "settled facts" not in low
    assert "newest line wins" in low
    assert "do not interview" in low
    assert "getting ready" in low


def test_talk_prompt_accepts_revisions_not_only_clothes():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk")
    low = text.lower()
    assert "newest line wins" in low
    assert "sense and body first" in low
    assert "echo instruction headings" in low


def test_prep_prompt_overrides_sticky_previous_craft():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="prep")
    assert "変える必要のないところは変えない" not in text
    assert "board image" in text.lower() or "old take" in text.lower()
    assert "newest words beat" in text.lower()


def test_talk_prompt_injects_character_voice():
    character = {
        "name_ja": "各務 みお",
        "name": "Mio Kagami",
        "first_person_ja": "私",
        "user_address_ja": "総監督さん",
        "talk_quirks": "マイク前では通る声。オフだと小声。",
        "duet_say_examples": [
            "放送室でマイクに向かってるところがいいです。",
            "ヘッドホンは片耳だけ外しておきます。",
        ],
        "personality": {},
    }
    text = crew.actress_duet_prompt(character, mode="talk")
    assert "総監督さん" in text
    assert "マイク前では通る声" in text
    assert "放送室でマイク" in text
    assert "VOICE" in text


def test_talk_prompt_injects_chara_json_individuality():
    """Full personality_presets fields must reach the duet talk contract."""
    from app.characters.presets import load_seed_presets, preset_to_character

    rows = load_seed_presets()
    preset = next(r for r in rows if r.get("id") == "c001")
    ch = preset_to_character(preset)
    text = crew.actress_duet_prompt(ch, mode="talk", seed="c001")
    app = (ch.get("personality") or {}).get("appearance") or {}
    assert ch["first_person_ja"] in text
    assert "ちょっと声が低目で" in text
    assert str(app.get("voice") or "")[:24] in text
    assert str(app.get("habit") or "")[:24] in text
    assert str((ch.get("personality") or {}).get("title_ja") or "")[:12] in text
    assert "INDIVIDUALITY LOCK" in text
    assert "SIGNATURE BEAT" in text
    assert "FIRST READ" in text
    # Distinctive girl vs generic soft-polite
    tsuba = preset_to_character(next(r for r in rows if r.get("id") == "c020"))
    tsuba_text = crew.actress_duet_prompt(tsuba, mode="talk", seed="c020")
    assert "アタシ" in tsuba_text
    assert "息を切らし気味の早口" in tsuba_text
    assert "アタシ" not in text


def test_talk_prompt_chat_intent_drops_required_card():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk", intent="casual")
    assert "This turn is conversation" in text
    assert "end in conversation" in text.lower()
    assert "DUET_CHAT" not in text
    assert "Required every turn" not in text
    assert crew.say_language_rule("ja") in text
    assert "required output language" not in text.lower()


def test_w_actress_duet_prompt():
    char_a = {"name_ja": "みなも", "personality": {"first_person_ja": "私", "user_address_ja": "総監督"}}
    char_b = {"name_ja": "かほ", "personality": {"first_person_ja": "私", "user_address_ja": "総監督"}}
    text = crew.w_actress_duet_prompt(char_a, char_b, mode="talk")
    assert "W-MUSE" in text
    assert "みなも" in text
    assert "かほ" in text
    assert "2girls" in crew.w_actress_duet_prompt(char_a, char_b, mode="prep")



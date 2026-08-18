"""What she carries out of a shoot: the picture, the photo, and the lesson.

All three come from one live session (`fb8023c5`) read off the server:

- `continuity` was never written. `finish_shoot` publishes `status: done` and
  *then* records continuity, so a showrunner who types the moment the take
  lands races it — and the loser's write is lost. She finished the day
  carrying the previous session's clothes as what she had learned.
- What she "learned" was derived from the notebook snapshot with no model at
  all: the word "low" anywhere in `frame` taught her 「ローアングルの近い距離」.
  On a session where the showrunner said 「もっと凄みが欲しい」「震えはいらない」
  「顔がまだ無表情」 four times over, none of it was read.
- Her diary is told to end on 「完成した本番写真を見た感想」 and was handed the
  shoot's tag list to write it from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, crew, service  # noqa: E402


# ── the lesson is read off what he said ─────────────────────────────────────

def test_praise_and_correction_land_in_different_places():
    raw = (
        "PREFERS: 逆光でリムライトが輪郭に乗る画\n"
        "AVOIDS: 指先の震え\n無表情\n"
        "NOTES: 「凄み」を求められる。可愛いだけでは足りない\n"
    )
    got = chain.parse_showrunner_taste(raw)

    assert "リムライト" in got["prefers"]
    assert "震え" in got["avoids"] and "無表情" in got["avoids"]
    assert "凄み" in got["notes"]


def test_a_shoot_that_taught_nothing_teaches_nothing():
    """Inventing a preference is how the next session becomes a rerun."""
    got = chain.parse_showrunner_taste("PREFERS:\nAVOIDS: なし\nNOTES: none")
    assert got == {"prefers": "", "avoids": "", "notes": ""}


def test_unreadable_output_is_empty_not_garbage():
    assert chain.parse_showrunner_taste("わかりました！") == {
        "prefers": "", "avoids": "", "notes": "",
    }


def test_the_contract_forbids_learning_the_take_itself():
    contract = crew.showrunner_taste_prompt(notes="x")
    assert "褒められた点は PREFERS" in contract
    assert "撮った内容そのものを書かない" in contract
    assert "空は正常な答え" in contract


class _TasteOllama:
    def __init__(self, reply):
        self.reply = reply
        self.seen = ""       # his words ride in the system prompt

    def generate_text_stream(self, prompt, **kw):
        self.seen = str(kw.get("system") or "")
        reply = self.reply

        async def _stream():
            yield {"type": "token", "text": reply}
        return _stream()


@pytest.mark.asyncio
async def test_his_words_are_what_she_is_given():
    session = {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "notes": ["もっと凄みが欲しい", "震えはいらない"],
        "chat": [],
    }
    ollama = _TasteOllama("PREFERS:\nAVOIDS: 指先の震え\nNOTES: 凄みを求められる")

    got = await service._learned_taste(ollama, session, cfg={})

    assert "凄み" in ollama.seen and "震え" in ollama.seen
    assert "震え" in got["avoids"]


@pytest.mark.asyncio
async def test_a_shoot_with_no_direction_notes_is_not_asked():
    session = {"session_id": "s1", "inputs": {}, "notes": [], "chat": []}
    ollama = _TasteOllama("PREFERS: something")

    assert await service._learned_taste(ollama, session, cfg={}) == {}
    assert ollama.seen == "", "nothing to learn from means nothing to ask"


# ── bond still remembers the take, and only that ────────────────────────────

def test_bond_is_the_picture_not_the_lesson():
    session = {"continuity_snapshot": {"notebook": {
        "atmosphere": "夕暮れの屋上", "vibe": "少し照れてる",
        "wearing": "セーラー", "frame": "low angle 煽り",
    }}}
    bond = service._bond_from_snapshot(session)

    assert "夕暮れの屋上" in bond["last"] and "セーラー" in bond["last"]
    assert isinstance(bond, dict) and set(bond) == {"distance", "inside", "last"}


# ── the diary is shown the photograph ───────────────────────────────────────

class _SeeingOllama:
    def __init__(self, reply, blind=False):
        self.reply = reply
        self.blind = blind
        self.saw_images = False

    def generate_vlm_stream(self, prompt, images, **kw):
        self.saw_images = bool(images)
        reply = "" if self.blind else self.reply

        async def _stream():
            if reply:
                yield {"type": "token", "text": reply}
        return _stream()

    def generate_text_stream(self, prompt, **kw):
        async def _stream():
            yield {"type": "token", "text": self.reply}
        return _stream()


class _ImageDb:
    def __init__(self, path=""):
        self.path = path

    async def get_by_sha256s(self, shas):
        return [{"path": self.path}] if self.path else []


def _shot_session():
    return {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "shoot": {"prompt": "1girl, piano, expressionless", "images": []},
    }


@pytest.mark.asyncio
async def test_with_no_photo_she_still_gets_the_prompt():
    """A page written from the tag list beats no page at all."""
    got = await service._read_the_photo(
        _ImageDb(), _SeeingOllama("..."), _shot_session(), "",
    )
    assert got == "1girl, piano, expressionless"


@pytest.mark.asyncio
async def test_an_unreadable_photo_falls_back(tmp_path):
    got = await service._read_the_photo(
        _ImageDb(str(tmp_path / "missing.png")), _SeeingOllama("..."),
        _shot_session(), "aaa",
    )
    assert got == "1girl, piano, expressionless"


@pytest.mark.asyncio
async def test_a_model_that_cannot_see_is_treated_as_not_having_looked(monkeypatch):
    """A blind model returns nothing rather than erroring — a known trap here."""
    monkeypatch.setattr(service, "images_by_sha", _fake_images)
    got = await service._read_the_photo(
        _ImageDb(), _SeeingOllama("", blind=True), _shot_session(), "aaa",
    )
    assert got == "1girl, piano, expressionless"


@pytest.mark.asyncio
async def test_what_the_photo_shows_is_what_she_writes_about(monkeypatch):
    monkeypatch.setattr(service, "images_by_sha", _fake_images)
    seeing = _SeeingOllama(
        "She sits at a piano in an empty music room. Her face is wet with tears."
    )

    got = await service._read_the_photo(
        _ImageDb(), seeing, _shot_session(), "aaa",
    )

    assert "tears" in got
    assert "expressionless" not in got, "the tag list is not the photograph"
    assert seeing.saw_images


async def _fake_images(db, shas):
    return [b"\xff\xd8\xff"]

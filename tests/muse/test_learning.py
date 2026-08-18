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


def test_the_contract_asks_for_the_pair_not_the_line():
    """A line on its own has no content.

    「いいよ、今の良かった」 means nothing unless you know what she had just
    done, and 「震えはいらない」 is not a rule — it was said to one quiet scene
    where her fingertips were shaking. Carried forward as a standing
    preference it would break the next shoot that wants a tremble.
    """
    contract = crew.showrunner_taste_prompt(exchanges="x", scene="音楽室")

    assert "自分の芝居に対して総監督が何と言ったか" in contract
    assert "その直前に自分が何をしていたか" in contract
    assert "場面を外すと" in contract
    assert "震えはいらない" in contract, "the counter-example is the lesson"
    assert "空は正常な答え" in contract


def test_praise_is_shown_what_it_was_praising():
    """「いいね」 at the end of a shoot carries its meaning in the line before."""
    session = {"chat": [
        {"role": "muse", "kind": "craft", "name": "みお",
         "text": "震えに頼らず、瞳の力で……やってみますね。"},
        {"role": "user", "name": "総監督", "text": "いいね、感情入ってきたよ"},
    ]}

    block = service._director_exchanges(session)

    assert "直前の私" in block
    assert "瞳の力" in block, "praise with nothing to point at teaches nothing"


def test_a_direction_is_shown_what_she_did_with_it():
    session = {"chat": [
        {"role": "user", "name": "総監督", "text": "今にも泣き出しそうな顔にして。"},
        {"role": "muse", "kind": "craft", "name": "みお",
         "text": "指先を震わせて、こらえる感じにしてみます。"},
    ]}

    block = service._director_exchanges(session)

    assert "泣き出しそう" in block and "指先を震わせて" in block


def test_her_muttering_is_not_an_answer():
    """ASIDE is inner voice, not what she played."""
    session = {"chat": [
        {"role": "user", "name": "総監督", "text": "もっと重く。"},
        {"role": "muse", "kind": "banter", "name": "みお", "text": "（うぅ、難しい）"},
        {"role": "muse", "kind": "craft", "name": "みお", "text": "重く、ですね。"},
    ]}

    block = service._director_exchanges(session)

    assert "重く、ですね" in block
    assert "難しい" not in block


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
async def test_she_is_given_the_pairs_and_the_scene():
    session = {
        "session_id": "s1", "inputs": {"locale": "ja"},
        "character": {"name_ja": "各務 みお"},
        "continuity_snapshot": {"notebook": {
            "scene": "音楽室、夕暮れ", "atmosphere": "lonely",
            "beat": "sitting at the piano",
        }},
        "chat": [
            {"role": "user", "name": "総監督", "text": "震えはいらないんだよ"},
            {"role": "muse", "kind": "craft", "name": "みお",
             "text": "瞳の力で訴えてみますね"},
        ],
    }
    ollama = _TasteOllama(
        "PREFERS: 静かな場面で瞳だけで訴えたら「いいね」と言われた\nAVOIDS:\nNOTES:"
    )

    got = await service._learned_taste(ollama, session, cfg={})

    assert "震えはいらない" in ollama.seen, "his line"
    assert "瞳の力で訴えて" in ollama.seen, "what she played against it"
    assert "音楽室" in ollama.seen, "the scene it happened in"
    assert "各務 みお" in ollama.seen, "she is the one writing it"
    assert "瞳だけで訴えた" in got["prefers"]


@pytest.mark.asyncio
async def test_a_shoot_where_he_said_nothing_is_not_asked():
    session = {"session_id": "s1", "inputs": {}, "chat": []}
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

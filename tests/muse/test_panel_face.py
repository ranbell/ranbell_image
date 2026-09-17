"""**喋った人の顔は、いつも同じ所に出る。**（2026-09-18）

総監督「Muse が喋ったときのサムネイルが抜けている場合がある」。

実機（`0239133f`・standard 18席）で彼女の吹き出しを数えると、**顔が付く行と
付かない行が混ざっていた**:

    kind=say / banter / verify_ok   speaker_id あり  → 顔が出る
    kind=heckle（やじ）・seat（開幕）  speaker_id なし  → **顔が出ない**

彼女は班の回で1ターンに2〜3回やじを入れる。同じ人が喋っているのに、**言葉の
出どころによって顔が消えていた。** 原因は二つ:

    ① 確定した行   `faceShaForRow` が「assistant なら主演の顔」を返すので、
                  班の席にまで顔が付く。それを**吹き出しの種類の許可リスト**で
                  抑えていて、その一覧に `heckle` が無かった
    ② 流している間  班の中の彼女は `actress:cast`（席の id）で流れていたので、
                  画面の「これは主演か」（`liveIsLead`）が偽になっていた

直し方は一つずつ ——「誰の言葉か」で決める。班の席は `meta.role` を持つので
顔を持たず、主演の行は種類にかかわらず顔が付く。流し込みの宛先は
`crew_room.stream_id` が本人の `character_id` に揃える。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "frontend/src/components/MusePanel.vue").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    body = SRC[SRC.index(f"function {name}("):]
    return body[: body.index("\n}\n") + 2]


# ── 確定した行 ──────────────────────────────────────────────────────────

def test_the_face_is_chosen_by_who_spoke_not_by_the_kind_of_bubble():
    body = _fn("faceShaForRow")
    assert "row?.meta?.role" in body, "行の持ち主（席の役）を見ていない"
    assert "role !== 'actress'" in body, "班の席にまで顔が付く"


def test_the_template_no_longer_keeps_a_list_of_allowed_bubbles():
    """許可リストは**漏れる** —— `heckle` が抜けていたのが今回の症状。"""
    m = re.search(r'v-if="faceForRow\(row\)([^"]*)"', SRC)
    assert m, "行の顔の出し分けが見つからない"
    assert m.group(1).strip() == "", f"種類の許可リストが残っている: {m.group(1)[:80]}"


def test_a_crew_seat_has_no_face():
    """班の席（演出・照明…）に主演の顔を付けない —— 許可リストの役目はこれだった。"""
    body = _fn("faceShaForRow")
    i_role = body.index("row?.meta?.role")
    i_lead = body.index("return leadFaceSha.value", i_role)
    assert i_role < i_lead, "席を弾く前に主演の顔を返している"


# ── 流している間 ────────────────────────────────────────────────────────

def test_the_folded_bubble_keeps_its_face():
    """畳んだ吹き出しだけ顔が消えると、喋り続けている間に顔が点滅する。"""
    assert "face: liveIsLead.value ? leadFace.value : ''" in _fn("foldLive")
    assert 'v-if="done.face"' in SRC


def test_the_lead_streams_under_her_own_id():
    """班の中の彼女も、本人の id で流れること（画面の `liveIsLead` がそれで決まる）。"""
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.muse import crew, crew_room

    session = {"character": {"character_id": "c1", "name_ja": "各務 みお"}}
    seat = [m for m in crew.resolve_crew(preset="standard")
            if crew.role_of(m) == "actress"][0]
    assert crew_room.stream_id(session, seat) == "c1"
    # 班の席はそのまま（顔を持たない側）
    assert crew_room.stream_id(session, "gaffer:gyakkou") == "gaffer:gyakkou"


def test_every_speaking_signal_goes_through_the_same_door():
    """`muse_speaking` を出す所は全部 `stream_id` を通ること。"""
    import inspect
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.muse import crew_room

    src = inspect.getsource(crew_room)
    signals = re.findall(r'"type": "muse_speaking", "muse_id": ([^,\n]+)', src)
    assert signals, "muse_speaking を出す所が見つからない"
    assert all("stream_id(" in s for s in signals), signals

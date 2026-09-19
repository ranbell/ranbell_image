"""**Never build the kind of break where input never comes back.** (2026-09-12)

The Showrunner: "the end-of-conversation handling is bad — it stays busy and after
that no input is possible at all".

The cause was not `busy` but `speaking`. The panel's lock is

    chatLocked = busy || renderLocked || speaking

and the flag raised by the SSE `muse_speaking` is lowered only in the `chat` /
`session_updated` branches, and then **only while `!busy`**. A signal that arrives
during a POST is discarded there, so when the POST finishes nobody is left to
lower it.

The studio shoot fell straight through it — opening the table emits
`muse_speaking` three to six times, and `runStage` only lowers `busy`, so input
died.

The panel code cannot be driven from Python, so **how it is written** is read and
pinned here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[2] / "frontend/src/components/MusePanel.vue"
SRC = PANEL.read_text(encoding="utf-8")


def _body(name: str) -> str:
    """Roughly extract the body of `async function name(...) { … }`."""
    m = re.search(rf"(?m)^async function {name}\([^)]*\) \{{(.*?)^\}}", SRC, re.S)
    assert m, f"{name} が見つからない"
    return m.group(1)


@pytest.mark.parametrize("fn", ["sendChat", "runStage", "finishSession", "openSession"])
def test_every_post_puts_the_flags_back(fn):
    """Every function that issues a POST lowers the speaking flag in `finally`."""
    body = _body(fn)
    assert "finally" in body, f"{fn} に finally が無い"
    tail = body[body.rindex("finally"):]
    assert "busy.value = false" in tail, f"{fn} が busy を下ろしていない"
    assert "stopSpeaking()" in tail, f"{fn} が speaking を下ろしていない"


def test_there_is_one_place_that_puts_them_back():
    """Cleanup lives in one place. Written at each site, the next function added
    will forget it."""
    assert "function stopSpeaking()" in SRC
    fn = SRC[SRC.index("function stopSpeaking()"):]
    fn = fn[: fn.index("\n}\n") + 2]
    for flag in ("speaking.value = false", "liveSay.value = ''", "liveName.value = ''"):
        assert flag in fn, flag


def test_the_flag_cannot_outlive_busy():
    """**The backstop.** Forgotten anywhere, it still comes down the moment `busy`
    does."""
    assert re.search(r"watch\(busy,\s*\(now\)\s*=>\s*\{\s*\n\s*if \(!now\) stopSpeaking\(\)", SRC)


def test_the_lock_is_still_the_three_flags():
    """If the lock gains a condition, this test is revisited with it."""
    m = re.search(r"const chatLocked = computed\(\(\) => (.+?)\)\n", SRC)
    assert m, "chatLocked が見つからない"
    assert m.group(1).strip() == "busy.value || renderLocked.value || speaking.value"


# ── 流れている表示が巻き戻らないこと（2026-09-12）───────────────────────
def test_a_finished_seat_is_kept_on_screen():
    """The Showrunner: "it is as if a reset runs each time a role speaks — it keeps
    rewinding".

    Confirmed rows only reach the screen once the POST returns (mid-turn,
    `refresh` is held off by `busy`). So without folding away a seat that has
    finished streaming, the previous seat's words vanish the moment the next one
    starts — eighteen times over.
    """
    handler = SRC[SRC.index("if (data.type === 'muse_speaking')"):]
    handler = handler[: handler.index("return\n    }")]
    # 名前を替える前に畳む（畳む中身は `foldLive`）
    assert handler.index("foldLive()") < handler.index("liveName.value =")

    fold = SRC[SRC.index("function foldLive()"):]
    fold = fold[: fold.index("\n}\n") + 2]
    # 空にする前に積む
    assert fold.index("liveDone.value = [") < fold.index("liveSay.value = ''")
    assert "if (liveSay.value.trim())" in fold


def test_the_bubble_folds_when_the_speaker_changes_mid_stream():
    """**When the owner of the words changes, fold there.** (2026-09-16)

    The Showrunner: "the Muses' conversations get mixed up". A field corner carries
    several people in one reply, so missing a single `muse_speaking` lets the next
    seat's words continue inside the previous seat's bubble. `chat_delta` carries
    `muse_id` too.
    """
    handler = SRC[SRC.index("if (data.type === 'chat_delta')"):]
    handler = handler[: handler.index("return\n    }")]
    assert "data.muse_id" in handler, "流れてくる本文の主を見ていない"
    assert handler.index("foldLive()") < handler.index("liveSay.value +=")


def test_the_kept_lines_go_away_when_the_real_ones_arrive():
    fn = SRC[SRC.index("function stopSpeaking()"):]
    fn = fn[: fn.index("\n}\n") + 2]
    assert "liveDone.value = []" in fn


@pytest.mark.parametrize("fn", ["sendChat", "runStage", "openSession"])
def test_the_swap_has_no_double_showing_window(fn):
    """Fold right after the real rows arrive. Waiting for `finally` shows both for
    a moment."""
    body = _body(fn)
    post = body.index("await api(")
    tail = body[post:]
    # 区切りは実際の `} finally {` だけ。説明文の「finally」に当たらないように。
    cut = tail.index("} finally {") if "} finally {" in tail else len(tail)
    assert "stopSpeaking()" in tail[:cut], f"{fn}: POST の直後に畳んでいない"


def test_the_kept_lines_look_like_the_real_ones():
    """They look like confirmed rows, so nothing appears to move when they are
    swapped."""
    block = SRC[SRC.index('v-for="(done, di) in liveDone"'):]
    block = block[: block.index("<div v-if=\"liveSay\"")]
    for cls in ("border-amber-800/30", "border-pink-500/30"):
        assert cls in block, cls
    assert "done.lead ? '🌸' : '🎬'" in block


# ── かな漢字変換の途中で送らない ──────────────────────────────────────────

def test_enter_is_a_newline_not_a_send():
    """**Enter is not send.** (2026-09-13)

    The Showrunner: "in the chat box, Enter should simply be a newline. Please send
    with the send button — it is a problem for Japanese users."

    Enter is what confirms a kana-kanji conversion, so binding it to send makes the
    line **fly off mid-conversion**. The compromise of "Shift+Enter for a newline"
    does not help either, because the confirming Enter is a plain Enter.
    **One button, and only that, sends.**
    """
    box = SRC[SRC.index("<textarea"):SRC.index("</textarea>")]
    assert 'v-model="chatInput"' in box
    assert "keydown.enter" not in box, "Enter に何かを結ぶと変換の途中で飛ぶ"
    assert 'rows="3"' in box, "標準3行"


def test_the_send_button_is_how_a_line_is_sent():
    """That the road out still exists (with Enter removed, this is the only
    door)."""
    assert '@submit.prevent="sendChat"' in SRC
    form = SRC[SRC.index('@submit.prevent="sendChat"'):]
    assert 'type="submit"' in form

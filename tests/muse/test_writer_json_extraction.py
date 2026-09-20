"""**A model that keeps talking after its JSON must still be heard.** (2026-09-20)

Asking the writer for a sign, live, came back as a correct object, a line of
commentary in Polish, and then the object again. `_extract_json_object` read the
span `{` … `}` **greedily** — the first brace to the last one — so what it tried
to parse included the prose between them, JSON refused it, and the whole turn
returned `{}`. The ledger did not move and the conversation showed 未反映, with
the model's right answer sitting in the raw output the whole time.

The extractor now walks the braces and takes the last object that carries
something, which is the one a self-correcting model means.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.muse.writer import _extract_json_object as extract

LIVE = '''```json
{
  "bearing": "holding sign together",
  "lettering": "Ranbell Image Muse",
  "beat": "both holding a signboard"
}
```

*Korekta: W Twoja instrukcja nie zawierała poprzedniego stanu (LEDGER NOW był
pusty), więc zmapowałem nową akcję bezpośrednio na strukturę.*

```json
{
  "beat": "holding sign together",
  "lettering": "Ranbell Image Muse"
}
```'''


def test_the_live_answer_is_not_thrown_away():
    assert extract(LIVE) == {
        "beat": "holding sign together",
        "lettering": "Ranbell Image Muse",
    }


def test_a_plain_object_still_reads():
    assert extract('{"beat": "standing"}') == {"beat": "standing"}


def test_one_object_in_a_fence_still_reads():
    assert extract('```json\n{"beat": "standing"}\n```') == {"beat": "standing"}


def test_a_brace_inside_a_value_does_not_end_the_object():
    raw = 'here you go:\n{"lettering": "use } and { freely", "beat": "sitting"}\nok?'
    assert extract(raw) == {"lettering": "use } and { freely", "beat": "sitting"}


def test_the_correction_wins_over_the_first_try():
    raw = '{"beat": "standing"}\nsorry, I meant:\n{"beat": "sitting"}'
    assert extract(raw) == {"beat": "sitting"}


def test_an_empty_correction_does_not_erase_the_answer():
    """`{}` last means "nothing to add", not "forget what I said"."""
    assert extract('{"beat": "sitting"}\nnothing else to change:\n{}') == {"beat": "sitting"}


def test_nothing_at_all_is_still_empty():
    assert extract("I could not do that.") == {}
    assert extract("") == {}

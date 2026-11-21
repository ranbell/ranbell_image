"""Pre-training diagnosis: a wrong answer here is silent — it either cries
wolf over every bucket or waves a real burn-in through to an hour of training.
"""
from __future__ import annotations

import pytest
from app.lora import bucket as bk
from app.lora import diagnose as dg
from PIL import Image, ImageDraw


def _doc(sha, tags, scores=None):
    return {"sha256": sha, "wd14_tags": tags,
            "wd14_tags_scores": scores if scores is not None else [0.9] * len(tags)}


# ── does a caption say it ─────────────────────────────────────────────────────

def test_mentions_reads_tag_words_in_prose():
    assert dg.mentions("a woman in a navy school uniform", "school_uniform")
    assert dg.mentions("two school uniforms hang on the door", "school_uniform")
    assert dg.mentions("her uniform for school", "school_uniform")


def test_mentions_is_not_a_stemmer_and_not_a_bag_of_words():
    assert not dg.mentions("the light falls uniformly", "uniform")
    # Both words are there, but far apart and about different things.
    assert not dg.mentions("a black jacket over a white shirt, sitting by the window, her hair", "black_hair")


def test_a_tag_without_words_counts_as_said():
    assert dg.mentions("anything", "^_^")


# ── which tags count ──────────────────────────────────────────────────────────

def test_image_tags_drop_low_confidence_and_uninformative_tags():
    doc = _doc("a", ["1girl", "solo", "general", "no_humans", "watermark", "hat", "faint"],
               [0.99, 0.98, 0.9, 0.9, 0.8, 0.7, 0.2])
    # Burn-in classics survive; rating, head count and negations do not.
    assert dg.image_tags(doc) == ["watermark", "hat"]


def test_image_tags_without_scores_keep_every_tag():
    assert dg.image_tags({"wd14_tags": ["hat", "scarf"]}) == ["hat", "scarf"]


# ── burn-in ───────────────────────────────────────────────────────────────────

def _rows(caption="a woman stands in a field", n=5, tags=("serafuku", "hat")):
    return [(f"s{i}", list(tags), caption) for i in range(n)]


def test_a_tag_in_every_picture_and_no_caption_is_a_risk():
    out = dg.burn_in(_rows())
    assert {r["tag"]: r["status"] for r in out} == {"serafuku": "risk", "hat": "risk"}
    assert out[0]["present"] == 1.0 and out[0]["uncaptioned"] == 1.0
    assert len(out[0]["examples"]) == dg.EXAMPLES


def test_a_tag_the_captions_say_is_not_a_risk():
    out = dg.burn_in(_rows(caption="a woman in a serafuku stands in a field"))
    assert [r["tag"] for r in out] == ["hat"]


def test_absorb_ok_turns_a_risk_into_intended_and_sorts_it_last():
    out = dg.burn_in(_rows(), absorb_ok=["Serafuku"])
    assert [(r["tag"], r["status"]) for r in out] == [("hat", "risk"), ("serafuku", "intended")]


def test_a_tag_in_few_pictures_is_not_a_candidate():
    rows = _rows(n=3) + [(f"t{i}", ["scarf"], "a woman") for i in range(3)]
    # serafuku/hat are in 3 of 6 (50%) — below BURN_PRESENT.
    assert {r["tag"] for r in dg.burn_in(rows)} == set()


def test_too_few_pictures_reads_nothing():
    assert dg.burn_in(_rows(n=dg.MIN_IMAGES - 1)) == []


# ── skew ──────────────────────────────────────────────────────────────────────

def test_skew_flags_one_distance_for_nearly_every_picture():
    lists = [["close-up", "hat"]] * 7 + [["full_body"]] * 3
    row = next(r for r in dg.skew(lists) if r["slot"] == "camera_distance")
    assert row["top_share"] == 0.7 and row["skewed"]  # exactly the threshold
    assert row["values"][0] == {"tag": "close-up", "count": 7}


def test_unknown_pictures_count_against_the_share():
    lists = [["close-up"]] * 3 + [["hat"]] * 7  # WD14 read a distance on 3 of 10
    row = next(r for r in dg.skew(lists) if r["slot"] == "camera_distance")
    assert row["unknown"] == 7 and not row["skewed"]


def test_synonymous_angles_are_one_value():
    lists = [["from_above"]] * 3 + [["high_angle"]] * 2
    row = next(r for r in dg.skew(lists) if r["slot"] == "camera_pitch")
    assert len(row["values"]) == 1 and row["values"][0]["count"] == 5


# ── near-duplicates ───────────────────────────────────────────────────────────

def _picture(path, seed, size=(256, 256)):
    im = Image.new("RGB", size, (20 * seed % 255, 90, 160))
    d = ImageDraw.Draw(im)
    for i in range(6):
        x = (seed * 37 + i * 41) % size[0]
        d.ellipse([x, i * 40, x + 60, i * 40 + 50], fill=(255, (seed * 70 + i * 30) % 255, 0))
    im.save(path)
    return path


def test_a_resized_copy_is_a_duplicate_and_another_picture_is_not(tmp_path):
    a = _picture(tmp_path / "a.png", 1)
    Image.open(a).resize((128, 128)).save(tmp_path / "a_small.jpg", quality=80)
    _picture(tmp_path / "b.png", 5)
    hashes = {k: dg.dhash(tmp_path / f) for k, f in
              (("a", "a.png"), ("a2", "a_small.jpg"), ("b", "b.png"))}
    pairs = dg.near_duplicates(hashes)
    assert [(p["a"], p["b"]) for p in pairs] == [("a", "a2")]


# ── the bucket ────────────────────────────────────────────────────────────────

def test_absorb_ok_is_stored_clean_and_never_recaptions():
    b = bk.new_bucket("x", slug="x")
    assert b["absorb_ok"] == []
    bk.add_items(b, ["a"])
    bk.set_caption(b, "a", "hand written")
    assert bk.update_settings(b, absorb_ok=["School Uniform", "school_uniform", " hat "]) is False
    assert b["absorb_ok"] == ["school_uniform", "hat"]
    assert b["items"][0]["caption"] == "hand written"


class FakeDb:
    def __init__(self, docs):
        self.docs = docs

    async def get_by_sha256s(self, shas):
        return [self.docs[s] for s in shas if s in self.docs]


@pytest.mark.asyncio
async def test_diagnose_counts_what_it_could_not_read(tmp_path):
    b = bk.new_bucket("x", slug="x")
    shas = [f"{i:064x}" for i in range(7)]
    bk.add_items(b, shas)
    docs = {}
    for i, sha in enumerate(shas[:5]):
        bk.set_caption(b, sha, "a woman in a field")
        docs[sha] = _doc(sha, ["serafuku"]) | {"path": str(_picture(tmp_path / f"{i}.png", i + 1))}
    docs[shas[5]] = _doc(shas[5], [])            # not tagged yet, and its file is gone
    # shas[6] has no row in the library at all

    out = await dg.diagnose(FakeDb(docs), b)
    assert out["images"] == 7
    assert out["coverage"] == {"tagged": 5, "untagged": 2, "uncaptioned": 0, "unreadable": 2}
    assert not out["too_few"]
    assert [r["tag"] for r in out["burn_in"]] == ["serafuku"]


@pytest.mark.asyncio
async def test_diagnose_an_empty_bucket():
    out = await dg.diagnose(FakeDb({}), bk.new_bucket("x", slug="x"))
    assert out["images"] == 0 and out["too_few"] and out["burn_in"] == [] and out["duplicates"] == []

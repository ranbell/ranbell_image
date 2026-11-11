"""What lands in the export folder, and that a reader never sees half a set."""
from __future__ import annotations

import json
import zipfile

import pytest

from app.lora import bucket as bk
from app.lora import export


class FakeDb:
    def __init__(self, docs):
        self.docs = docs

    async def get(self, sha):
        return self.docs.get(sha)


def _png(path, w=64, h=32):
    from PIL import Image
    Image.new("RGB", (w, h)).save(path)
    return path


def _ready_bucket(shas):
    b = bk.new_bucket("Red", slug="red", trigger="rnbl", targets=["red hair"])
    bk.add_items(b, shas)
    for sha in shas:
        bk.apply_described(b, sha, "a woman")
        bk.apply_stripped(b, sha, f"rnbl, a woman {sha}", expected_full="a woman")
    return b


@pytest.mark.asyncio
async def test_folder_has_picture_caption_pairs_and_meta(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    db = FakeDb({"aa": {"path": str(_png(src / "one.PNG"))},
                 "bb": {"path": str(_png(src / "two.webp", 10, 20))}})
    b = _ready_bucket(["aa", "bb"])

    entries = await export.plan(db, b)
    final = export.write_folder(entries, out, b["slug"])

    assert sorted(p.name for p in final.iterdir()) == [
        "aa.png", "aa.txt", "bb.txt", "bb.webp", "meta.json"]
    assert (final / "aa.txt").read_text() == "rnbl, a woman aa\n"
    meta = json.loads((final / "meta.json").read_text())
    assert meta["format"] == export.FORMAT
    assert meta["trigger"] == "rnbl" and meta["targets"] == ["red hair"]
    assert (meta["target_model"], meta["caption_style"]) == ("krea2", "prose")
    assert meta["items"][1]["width"] == 10 and meta["items"][1]["height"] == 20
    assert [p.name for p in out.iterdir()] == ["red"]  # no temp dirs left behind


@pytest.mark.asyncio
async def test_not_ready_bucket_is_refused():
    b = bk.new_bucket("x", slug="x")
    bk.add_items(b, ["a"])
    with pytest.raises(export.ExportError, match="pending"):
        await export.plan(FakeDb({}), b)


@pytest.mark.asyncio
async def test_missing_picture_file_is_refused(tmp_path):
    b = _ready_bucket(["aa"])
    with pytest.raises(export.ExportError, match="missing"):
        await export.plan(FakeDb({"aa": {"path": str(tmp_path / "gone.png")}}), b)


def test_re_export_replaces_the_whole_set(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    export.write_folder([export.Entry("old.txt", None, "old")], out, "red")
    export.write_folder([export.Entry("new.txt", None, "new")], out, "red")
    assert [p.name for p in (out / "red").iterdir()] == ["new.txt"]
    assert [p.name for p in out.iterdir()] == ["red"]


def test_a_failed_export_leaves_the_previous_set(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    export.write_folder([export.Entry("old.txt", None, "old")], out, "red")
    broken = [export.Entry("a.txt", None, "a"), export.Entry("b.png", tmp_path / "nope.png")]
    with pytest.raises(FileNotFoundError):
        export.write_folder(broken, out, "red")
    assert [p.name for p in (out / "red").iterdir()] == ["old.txt"]
    assert [p.name for p in out.iterdir()] == ["red"]


def test_missing_export_folder_is_reported(tmp_path):
    with pytest.raises(export.ExportError, match="does not exist"):
        export.write_folder([], tmp_path / "nope", "red")


@pytest.mark.asyncio
async def test_zip_holds_the_same_folder(tmp_path):
    db = FakeDb({"aa": {"path": str(_png(tmp_path / "one.png"))}})
    entries = await export.plan(db, _ready_bucket(["aa"]))
    path = export.write_zip(entries, "red")
    try:
        with zipfile.ZipFile(path) as zf:
            assert sorted(zf.namelist()) == ["red/aa.png", "red/aa.txt", "red/meta.json"]
    finally:
        path.unlink()


def test_admin_shows_whether_the_export_folder_is_usable(tmp_path):
    from app.api.admin import lora_export_dir_status

    assert lora_export_dir_status(str(tmp_path)) == "ok"
    assert lora_export_dir_status(str(tmp_path / "not-mounted")) == "missing"
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        import os
        expected = "ok" if os.geteuid() == 0 else "readonly"  # root writes anyway
        assert lora_export_dir_status(str(ro)) == expected
    finally:
        ro.chmod(0o755)

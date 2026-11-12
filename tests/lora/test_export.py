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
        "aa.png", "aa.txt", "bb.png", "bb.txt", "meta.json"]
    assert (final / "aa.txt").read_text() == "rnbl, a woman aa\n"
    meta = json.loads((final / "meta.json").read_text())
    assert meta["format"] == export.FORMAT
    assert meta["trigger"] == "rnbl" and meta["targets"] == ["red hair"]
    assert (meta["target_model"], meta["caption_style"]) == ("krea2", "prose")
    assert meta["items"][1]["width"] == 10 and meta["items"][1]["height"] == 20
    assert str(src) not in (final / "meta.json").read_text()  # no host paths
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


def test_pictures_are_bare_pngs(tmp_path):
    """Prompt chunks, EXIF and ICC never leave; orientation is baked in."""
    from PIL import Image, PngImagePlugin

    png = tmp_path / "a.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", '{"secret": "workflow"}')
    info.add_text("parameters", "a secret prompt")
    Image.new("RGBA", (8, 4), (255, 0, 0, 128)).save(png, pnginfo=info)

    jpg = tmp_path / "b.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° on display
    exif[0x010F] = "SomeCamera"
    Image.new("RGB", (8, 4), (0, 0, 255)).save(jpg, exif=exif)

    for src, mode, size in ((png, "RGBA", (8, 4)), (jpg, "RGB", (4, 8))):
        data = export.clean_png(src)
        out = tmp_path / "out.png"
        out.write_bytes(data)
        with Image.open(out) as im:
            assert im.format == "PNG" and im.mode == mode and im.size == size
            assert not im.text and "exif" not in im.info and "icc_profile" not in im.info
        assert b"secret" not in data and b"SomeCamera" not in data
        assert export._image_size(src) == size


@pytest.mark.asyncio
async def test_zip_pictures_are_bare_pngs_too(tmp_path):
    from PIL import Image, PngImagePlugin

    src = tmp_path / "one.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", "secret")
    Image.new("RGB", (4, 4)).save(src, pnginfo=info)
    entries = await export.plan(FakeDb({"aa": {"path": str(src)}}), _ready_bucket(["aa"]))
    path = export.write_zip(entries, "red")
    try:
        with zipfile.ZipFile(path) as zf:
            assert b"secret" not in zf.read("red/aa.png")
            assert str(tmp_path) not in zf.read("red/meta.json").decode()
    finally:
        path.unlink()


def test_another_buckets_folder_is_never_replaced(tmp_path):
    out = tmp_path / "out"
    (out / "red").mkdir(parents=True)
    (out / "red" / "meta.json").write_text(json.dumps({"bucket_id": "someone-else"}))
    entries = [export.Entry("meta.json", None, "{}")]
    with pytest.raises(export.ExportError, match="another bucket"):
        export.write_folder(entries, out, "red", bucket_id="me")
    assert json.loads((out / "red" / "meta.json").read_text())["bucket_id"] == "someone-else"
    # Its own folder, or an old export without an id where it last exported, is replaced.
    (out / "red" / "meta.json").write_text(json.dumps({"bucket_id": "me"}))
    export.write_folder(entries, out, "red", bucket_id="me")
    (out / "red" / "meta.json").write_text("{}")
    export.write_folder(entries, out, "red", bucket_id="me", known_path=str(out / "red"))


def test_16bit_and_transparent_colour_pictures_export_right(tmp_path):
    import io

    from PIL import Image

    p16 = tmp_path / "g16.png"
    Image.new("I;16", (4, 4), 40000).save(p16)
    with Image.open(io.BytesIO(export.clean_png(p16))) as im:
        assert im.mode == "RGB" and im.getpixel((0, 0))[0] in range(150, 160)  # 40000/256 ≈ 156

    ptr = tmp_path / "trns.png"
    im = Image.new("RGB", (4, 4), (255, 0, 0))
    im.putpixel((0, 0), (0, 255, 0))
    im.save(ptr, transparency=(0, 255, 0))
    with Image.open(io.BytesIO(export.clean_png(ptr))) as out:
        assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0 and out.getpixel((1, 1))[3] == 255

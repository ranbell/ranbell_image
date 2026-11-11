"""publish: the weights stay byte for byte, the identifying metadata goes."""
from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "work" / "bin" / "publish_lora.py"

META = {
    "ss_network_dim": "32", "ss_learning_rate": "0.0001", "ss_num_epochs": "30",
    "modelspec.title": "my_style", "modelspec.date": "2026-11-11T00:47:52",
    "ss_training_started_at": "1794325257.5", "ss_training_finished_at": "1794325672.6",
    "ss_session_id": "1727652873", "ss_sd_model_name": "raw.safetensors",
    "ss_vae_name": "qwen_image_vae.safetensors",
    "ss_tag_frequency": json.dumps({"my_style": {"a girl": 3}}),
    "ss_datasets": json.dumps([{"resolution": [1024, 1024], "image_directory": "my_style",
                                "num_repeats": 1}]),
}


@pytest.fixture
def pub(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSUBI_WORK", str(tmp_path))
    spec = importlib.util.spec_from_file_location("publish_lora", BIN)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "publish_lora", mod)
    spec.loader.exec_module(mod)
    return mod


def _safetensors(path: Path, meta: dict, data: bytes = bytes(range(16))) -> bytes:
    header = {"__metadata__": meta,
              "w": {"dtype": "F32", "shape": [4], "data_offsets": [0, len(data)]}}
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + data)
    return data


def test_weights_are_copied_byte_for_byte(pub, tmp_path):
    src = tmp_path / "a.safetensors"
    data = _safetensors(src, META)
    dst = tmp_path / "out.safetensors"
    meta, _ = pub.clean_metadata(META, drop_all=False)
    pub.write_with_metadata(src, dst, meta)
    header, start = pub.read_header(dst)
    assert dst.read_bytes()[start:] == data
    assert header["w"] == {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]}
    assert start % 8 == 0


def test_settings_stay_and_identifying_fields_go(pub):
    meta, removed = pub.clean_metadata(META, drop_all=False)
    assert meta["ss_network_dim"] == "32" and meta["modelspec.title"] == "my_style"
    for gone in ("modelspec.date", "ss_training_started_at", "ss_session_id",
                 "ss_sd_model_name", "ss_vae_name", "ss_tag_frequency"):
        assert gone not in meta
    ds = json.loads(meta["ss_datasets"])
    assert "image_directory" not in ds[0] and ds[0]["num_repeats"] == 1
    assert "ss_datasets: folder names" in removed


def test_all_drops_every_field(pub, tmp_path):
    src = tmp_path / "a.safetensors"
    _safetensors(src, META)
    meta, removed = pub.clean_metadata(META, drop_all=True)
    pub.write_with_metadata(src, tmp_path / "b.safetensors", meta)
    header, _ = pub.read_header(tmp_path / "b.safetensors")
    assert "__metadata__" not in header and len(removed) == len(META)


def test_run_name_and_epoch_pick_the_file(pub, tmp_path):
    run = pub.OUTPUT / "my_style-20261111-0040"
    run.mkdir(parents=True)
    for f in ("my_style.safetensors", "my_style-000020.safetensors"):
        (run / f).write_bytes(b"")
    assert pub.resolve("my_style", None).name == "my_style.safetensors"
    assert pub.resolve("my_style", 20).name == "my_style-000020.safetensors"
    with pytest.raises(SystemExit, match="000007"):
        pub.resolve("my_style", 7)

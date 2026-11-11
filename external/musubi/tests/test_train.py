"""The wrapper's decisions, without a GPU or musubi.

    python3 -m pytest tests   (any Python with pytest; stdlib only otherwise)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "work" / "bin" / "train_lora.py"


@pytest.fixture
def tk(tmp_path, monkeypatch):
    """The module, pointed at a scratch /work."""
    monkeypatch.setenv("MUSUBI_WORK", str(tmp_path))
    for k in ("KREA2_DIT", "KREA2_VAE", "KREA2_TE"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("train_lora", BIN)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "train_lora", mod)  # dataclasses look themselves up here
    spec.loader.exec_module(mod)
    (tmp_path / "datasets").mkdir()
    return mod


def _dataset(tk, slug="red", captions=("rnbl, a woman",)):
    d = tk.DATASETS / slug
    d.mkdir()
    for i, cap in enumerate(captions):
        (d / f"{i:02}.png").write_bytes(b"x")
        (d / f"{i:02}.txt").write_text(cap)
    (d / "meta.json").write_text(json.dumps({
        "format": tk.FORMAT, "trigger": "rnbl", "targets": ["red hair"], "target_model": "krea2"}))
    return d


def _args(tk, *extra):
    import argparse
    ns = argparse.Namespace(
        bucket="red", preset=None, epochs=4, repeats=2, rank=16, alpha=None, lr=1e-4,
        seed=1, save_every=1, resolution=None, blocks_to_swap=None, min_free_gb=None, name=None,
    )
    for kv in extra:
        k, v = kv
        setattr(ns, k, v)
    return ns


def _flags(cmd):
    return " ".join(cmd)


def test_default_preset_uses_fp8_and_swap(tk):
    _dataset(tk)
    plan = tk.make_plan(_args(tk))
    train = dict(plan.commands)["train"]
    assert "--fp8_base --fp8_scaled" in _flags(train)
    assert "--blocks_to_swap 20" in _flags(train)
    assert "--network_module networks.lora_krea2" in _flags(train)
    assert "--network_dim 16 --network_alpha 16" in _flags(train)
    assert "--gradient_checkpointing_cpu_offload" not in train
    assert plan.settings["steps"] == 1 * 2 * 4


def test_1024_preset_swaps_more_and_offloads(tk):
    _dataset(tk)
    plan = tk.make_plan(_args(tk, ("preset", "16gb-safe")))
    train = _flags(dict(plan.commands)["train"])
    assert "--blocks_to_swap 26" in train and "--gradient_checkpointing_cpu_offload" in train
    assert plan.settings["resolution"] == 1024


def test_overrides_win_over_the_preset(tk):
    _dataset(tk)
    plan = tk.make_plan(_args(tk, ("blocks_to_swap", 0), ("resolution", 512)))
    assert "--blocks_to_swap" not in dict(plan.commands)["train"]
    assert plan.settings["resolution"] == 512


def test_text_encoder_cache_is_reused_only_for_the_same_captions(tk):
    _dataset(tk)
    plan = tk.make_plan(_args(tk))
    assert "text_encoder" in dict(plan.commands)
    plan.cache_dir.mkdir(parents=True)
    (plan.cache_dir / "te.hash").write_text(plan.te_hash)
    assert "text_encoder" not in dict(tk.make_plan(_args(tk)).commands)

    (tk.DATASETS / "red" / "00.txt").write_text("rnbl, a different caption")
    assert "text_encoder" in dict(tk.make_plan(_args(tk)).commands)


def test_latents_are_always_cached_incrementally(tk):
    _dataset(tk)
    latents = dict(tk.make_plan(_args(tk)).commands)["latents"]
    assert "--skip_existing" in latents and "--keep_cache" in latents


def test_toml_points_at_the_readonly_dataset_and_a_separate_cache(tk):
    toml = tk.dataset_toml(Path("/work/datasets/red"), Path("/work/cache/red"), resolution=768, repeats=3)
    assert 'image_directory = "/work/datasets/red"' in toml
    assert 'cache_directory = "/work/cache/red"' in toml
    assert "resolution = [768, 768]" in toml and "num_repeats = 3" in toml


@pytest.mark.parametrize("break_it,message", [
    (lambda d: (d / "meta.json").unlink(), "meta.json missing"),
    (lambda d: (d / "00.txt").unlink(), "no caption file"),
    (lambda d: (d / "00.txt").write_text("  "), "empty caption"),
    (lambda d: (d / "meta.json").write_text('{"format": "other"}'), "unknown dataset format"),
])
def test_incomplete_dataset_stops_before_anything_runs(tk, break_it, message):
    d = _dataset(tk)
    break_it(d)
    with pytest.raises(tk.Stop, match=message):
        tk.make_plan(_args(tk))


def test_missing_dataset_lists_what_exists(tk):
    _dataset(tk, slug="blue")
    with pytest.raises(tk.Stop, match="available: blue"):
        tk.make_plan(_args(tk))


def test_missing_models_are_named(tk):
    with pytest.raises(tk.Stop, match="raw.safetensors"):
        tk.check_models()


def test_too_little_free_vram_stops(tk, monkeypatch):
    monkeypatch.setattr(tk, "gpu_memory", lambda: (12.0, 16.0))
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    with pytest.raises(tk.Stop, match="only 4.0 GiB"):
        tk.wait_for_free(13.0, seconds=2)


def test_kill_takes_the_whole_process_group(tk, tmp_path, monkeypatch):
    monkeypatch.setattr(tk, "gpu_memory", lambda: None)
    runner = tk.Runner(tmp_path / "log.txt")
    # A parent with a child of its own, like accelerate launching the trainer.
    runner.proc = subprocess.Popen(
        ["bash", "-c", "sleep 60 & echo $! > child.pid; wait"],
        cwd=tmp_path, start_new_session=True,
    )
    for _ in range(50):
        if (tmp_path / "child.pid").exists() and (tmp_path / "child.pid").read_text().strip():
            break
        time.sleep(0.05)
    child = int((tmp_path / "child.pid").read_text())
    runner.kill()
    runner.close()
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


class _FakeRunner:
    """Fails with OOM until blocks_to_swap reaches `fits_at`."""
    def __init__(self, fits_at, oom=True):
        self.fits_at, self.oom_on_fail, self.swaps, self.notes = fits_at, oom, [], []
        self.oom = False

    def run(self, label, cmd):
        swap = int(cmd[cmd.index("--blocks_to_swap") + 1])
        self.swaps.append(swap)
        if swap < self.fits_at:
            self.oom = self.oom_on_fail
            raise RuntimeError("train failed")

    def note(self, line):
        self.notes.append(line)


def test_out_of_memory_retries_with_more_swap(tk, monkeypatch):
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    _dataset(tk)
    plan = tk.make_plan(_args(tk))
    runner = _FakeRunner(fits_at=26)
    tk.run_train_with_retry(runner, plan)
    assert runner.swaps == [20, 24, 26]
    assert plan.settings["blocks_to_swap"] == 26


def test_a_failure_that_is_not_memory_is_not_retried(tk, monkeypatch):
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    _dataset(tk)
    runner = _FakeRunner(fits_at=99, oom=False)
    with pytest.raises(RuntimeError):
        tk.run_train_with_retry(runner, tk.make_plan(_args(tk)))
    assert runner.swaps == [20]


def test_retry_gives_up_at_the_swap_limit(tk, monkeypatch):
    monkeypatch.setattr(tk.time, "sleep", lambda s: None)
    _dataset(tk)
    runner = _FakeRunner(fits_at=99)
    with pytest.raises(RuntimeError):
        tk.run_train_with_retry(runner, tk.make_plan(_args(tk)))
    assert runner.swaps == [20, 24, 26]


def test_unknown_target_model_is_refused_with_the_supported_list(tk):
    d = _dataset(tk)
    meta = json.loads((d / "meta.json").read_text())
    (d / "meta.json").write_text(json.dumps(meta | {"target_model": "anima"}))
    with pytest.raises(tk.Stop, match="no recipe for.*supported: krea2"):
        tk.make_plan(_args(tk))


def test_the_recipe_decides_scripts_and_flags(tk):
    _dataset(tk)
    plan = tk.make_plan(_args(tk))
    assert plan.recipe.id == "krea2" and plan.settings["target"] == "krea2"
    cmds = dict(plan.commands)
    assert cmds["latents"][1].endswith("krea2_cache_latents.py")
    assert "--timestep_sampling krea2_shift" in _flags(cmds["train"])


def test_a_preset_the_target_does_not_have_stops(tk):
    _dataset(tk)
    with pytest.raises(tk.Stop, match="no preset 'nope'"):
        tk.make_plan(_args(tk, ("preset", "nope")))


def test_model_files_prefer_the_target_folder_and_accept_the_flat_layout(tk, monkeypatch):
    for k in ("KREA2_DIT", "KREA2_VAE", "KREA2_TE"):
        monkeypatch.delenv(k, raising=False)
    files = tk.model_files(tk.KREA2)
    assert files["dit"].endswith("models/krea2/raw.safetensors")       # nothing yet: the new place
    tk.MODELS.mkdir(parents=True, exist_ok=True)
    (tk.MODELS / "raw.safetensors").write_bytes(b"")                  # the first setup's flat layout
    assert tk.model_files(tk.KREA2)["dit"].endswith("models/raw.safetensors")
    (tk.MODELS / "krea2").mkdir()
    (tk.MODELS / "krea2" / "raw.safetensors").write_bytes(b"")
    assert tk.model_files(tk.KREA2)["dit"].endswith("models/krea2/raw.safetensors")
    monkeypatch.setenv("KREA2_DIT", "/elsewhere/raw.safetensors")
    assert tk.model_files(tk.KREA2)["dit"] == "/elsewhere/raw.safetensors"

#!/usr/bin/env python3
"""Train a LoRA from a dataset folder ranbell exported.

    docker compose exec musubi train <bucket> [--preset 16gb] [--epochs N] [--dry-run]

The dataset's meta.json names its target model (``target_model``); the recipe
for that model (RECIPES) decides the model files, the musubi scripts, the
training flags and the presets. **A new target model is one Recipe.**

What it does, in order:
  1. check   — the dataset (meta.json, every picture has its .txt) and the model files
  2. free    — ask ComfyUI and Ollama to let go of the card (same GPU), then
               check that enough VRAM is actually free
  3. toml    — write the musubi dataset config for this run
  4. run     — cache latents → cache text encoder outputs → train
  5. report  — log, LoRA files, peak VRAM, time

Standard library only; musubi runs in /work/venv as a child process group, so
Ctrl-C (or a closed terminal) takes the whole training down with it and never
leaves it holding the GPU.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

WORK = Path(os.environ.get("MUSUBI_WORK", "/work"))
DATASETS = WORK / "datasets"
MODELS = WORK / "models"
CACHE = WORK / "cache"
OUTPUT = WORK / "output"
MUSUBI = WORK / "musubi-tuner"
VENV_BIN = WORK / "venv" / "bin"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FORMAT = "ranbell-lora-dataset/1"


@dataclass(frozen=True)
class Recipe:
    """How one target model is trained with musubi-tuner."""
    id: str
    label: str
    #: role -> (env var that overrides it, file name under models/<id>/)
    models: dict[str, tuple[str, str]]
    #: musubi scripts under src/musubi_tuner/
    latents_script: str
    te_script: str
    train_script: str
    network_module: str
    #: flags only this model's training takes
    train_flags: tuple[str, ...]
    presets: dict[str, dict]
    default_preset: str


# Krea 2 — measured on an RTX 4060 Ti 16 GB (PCIe x8), 2026-11-10/11; see README.
# VRAM and speed are set by how many blocks are swapped (~0.45 GiB each);
# resolution matters little under gradient checkpointing, so 1024 (what Raw
# was trained up to) is the default. Long captions cost VRAM too (Krea 2 puts
# the text tokens in the same sequence): swap 16 fit one-line captions at
# 13.6 GiB but ran out with real VLM captions, so the default is 20 (15.5 GiB,
# 15.3 s/step with 18 pictures), and an out-of-memory run is retried with 4
# more swapped blocks.
KREA2 = Recipe(
    id="krea2",
    label="Krea 2",
    models={
        "dit": ("KREA2_DIT", "raw.safetensors"),
        "vae": ("KREA2_VAE", "qwen_image_vae.safetensors"),
        "text_encoder": ("KREA2_TE", "qwen3vl_4b_bf16.safetensors"),
    },
    latents_script="krea2_cache_latents.py",
    te_script="krea2_cache_text_encoder_outputs.py",
    train_script="krea2_train_network.py",
    network_module="networks.lora_krea2",
    # Buckets give mixed shapes, and the preset may not be 1024: let the
    # shift follow the resolution instead of the fixed 2.5 tuned for 1024².
    train_flags=("--timestep_sampling", "krea2_shift", "--weighting_scheme", "none"),
    presets={
        "16gb": {             # default: margin for long captions and the desktop (~1 GiB)
            "resolution": 1024, "blocks_to_swap": 20, "fp8": True,
            "gc_cpu_offload": False, "min_free_gb": 14.0,
        },
        "16gb-safe": {        # 8.4 GiB peak, 16.7 s/step — when something must stay on the card
            "resolution": 1024, "blocks_to_swap": 26, "fp8": True,
            "gc_cpu_offload": True, "min_free_gb": 9.0,
        },
    },
    default_preset="16gb",
)

RECIPES: dict[str, Recipe] = {KREA2.id: KREA2}
ALL_PRESETS = sorted({name for r in RECIPES.values() for name in r.presets})


def model_files(recipe: Recipe) -> dict[str, str]:
    """Where each model file is: the env override, else models/<target>/<file>,
    else models/<file> (the flat layout of the first Krea 2 setup)."""
    out: dict[str, str] = {}
    for role, (env, name) in recipe.models.items():
        if os.environ.get(env):
            out[role] = os.environ[env]
            continue
        nested, flat = MODELS / recipe.id / name, MODELS / name
        out[role] = str(flat if not nested.is_file() and flat.is_file() else nested)
    return out


class Stop(RuntimeError):
    """A reason to stop before anything heavy starts."""


@dataclass
class Plan:
    slug: str
    dataset_dir: Path
    cache_dir: Path
    run_dir: Path
    output_name: str
    meta: dict
    settings: dict
    recipe: Recipe = KREA2
    commands: list[tuple[str, list[str]]] = field(default_factory=list)
    te_hash: str = ""


# ── 1. check ──────────────────────────────────────────────────────────────────

def read_dataset(dataset_dir: Path) -> tuple[dict, list[Path]]:
    if not dataset_dir.is_dir():
        have = sorted(p.name for p in dataset_dir.parent.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if dataset_dir.parent.is_dir() else []
        raise Stop(f"dataset not found: {dataset_dir}" + (f"\n  available: {', '.join(have)}" if have else ""))
    meta_path = dataset_dir / "meta.json"
    if not meta_path.is_file():
        raise Stop(f"meta.json missing in {dataset_dir} (export it from ranbell)")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format") != FORMAT:
        raise Stop(f"unknown dataset format {meta.get('format')!r} (expected {FORMAT})")
    target = meta.get("target_model", "krea2")
    if target not in RECIPES:
        raise Stop(f"this dataset is for {target!r}, which this trainer has no recipe for "
                   f"(supported: {', '.join(RECIPES)})")

    images = sorted(p for p in dataset_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        raise Stop(f"no pictures in {dataset_dir}")
    problems = []
    for img in images:
        txt = img.with_suffix(".txt")
        if not txt.is_file():
            problems.append(f"{img.name}: no caption file")
        elif not txt.read_text(encoding="utf-8").strip():
            problems.append(f"{img.name}: empty caption")
    if problems:
        raise Stop("dataset is not complete:\n  " + "\n  ".join(problems))
    return meta, images


def check_models(recipe: Recipe = KREA2) -> None:
    missing = [f"{k}: {p}" for k, p in model_files(recipe).items() if not Path(p).is_file()]
    if missing:
        raise Stop(f"{recipe.label} model files missing (see README for where to get them):\n  "
                   + "\n  ".join(missing))
    if not (MUSUBI / "src" / "musubi_tuner" / recipe.train_script).is_file():
        raise Stop(f"musubi-tuner has no {recipe.train_script} — run `musubi-setup` "
                   "(or update the pinned commit)")


def captions_hash(images: list[Path]) -> str:
    """Changes whenever any caption (or the set of pictures) changes."""
    h = hashlib.sha256()
    for img in images:
        h.update(img.name.encode())
        h.update(img.with_suffix(".txt").read_bytes())
    return h.hexdigest()


# ── 3. toml + commands ────────────────────────────────────────────────────────

def dataset_toml(dataset_dir: Path, cache_dir: Path, *, resolution: int, repeats: int) -> str:
    # bucket_no_upscale: never blow a small picture up past what it is.
    return "\n".join([
        "[general]",
        f"resolution = [{resolution}, {resolution}]",
        'caption_extension = ".txt"',
        "batch_size = 1",
        "enable_bucket = true",
        "bucket_no_upscale = true",
        "",
        "[[datasets]]",
        f'image_directory = "{dataset_dir}"',
        f'cache_directory = "{cache_dir}"',
        f"num_repeats = {repeats}",
        "",
    ])


def build_commands(plan: Plan, *, te_cached: bool) -> list[tuple[str, list[str]]]:
    s = plan.settings
    r = plan.recipe
    files = model_files(r)
    py = str(VENV_BIN / "python")
    src = MUSUBI / "src" / "musubi_tuner"
    toml = str(plan.run_dir / "dataset.toml")

    cmds: list[tuple[str, list[str]]] = [
        ("latents", [py, str(src / r.latents_script),
                     "--dataset_config", toml, "--vae", files["vae"],
                     "--skip_existing", "--keep_cache"]),
    ]
    if not te_cached:
        cmds.append(("text_encoder", [py, str(src / r.te_script),
                                      "--dataset_config", toml,
                                      "--text_encoder", files["text_encoder"],
                                      "--batch_size", "1", "--keep_cache"]))

    train = [
        str(VENV_BIN / "accelerate"), "launch",
        "--num_processes", "1", "--num_machines", "1", "--dynamo_backend", "no",
        "--num_cpu_threads_per_process", "1", "--mixed_precision", "bf16",
        str(src / r.train_script),
        "--dit", files["dit"], "--vae", files["vae"],
        "--dataset_config", toml,
        "--sdpa", "--mixed_precision", "bf16",
        *r.train_flags,
        "--optimizer_type", "adamw8bit", "--learning_rate", str(s["lr"]),
        "--gradient_checkpointing",
        "--max_data_loader_n_workers", "2", "--persistent_data_loader_workers",
        "--network_module", r.network_module,
        "--network_dim", str(s["rank"]), "--network_alpha", str(s["alpha"]),
        "--max_train_epochs", str(s["epochs"]), "--save_every_n_epochs", str(s["save_every"]),
        "--seed", str(s["seed"]),
        "--output_dir", str(plan.run_dir), "--output_name", plan.output_name,
        "--logging_dir", str(plan.run_dir / "logs"), "--log_with", "tensorboard",
    ]
    if s["fp8"]:
        train += ["--fp8_base", "--fp8_scaled"]
    if s["blocks_to_swap"]:
        train += ["--blocks_to_swap", str(s["blocks_to_swap"])]
    if s["gc_cpu_offload"]:
        train += ["--gradient_checkpointing_cpu_offload"]
    cmds.append(("train", train))
    return cmds


def make_plan(args) -> Plan:
    slug = args.bucket.strip("/").split("/")[-1]
    dataset_dir = DATASETS / slug
    meta, images = read_dataset(dataset_dir)
    recipe = RECIPES[meta.get("target_model", "krea2")]
    preset_name = args.preset or recipe.default_preset
    if preset_name not in recipe.presets:
        raise Stop(f"{recipe.label} has no preset {preset_name!r} (has: {', '.join(recipe.presets)})")

    preset = dict(recipe.presets[preset_name])
    for key in ("resolution", "blocks_to_swap", "min_free_gb"):
        v = getattr(args, key)
        if v is not None:
            preset[key] = v
    settings = preset | {
        "preset": preset_name, "target": recipe.id,
        "epochs": args.epochs, "repeats": args.repeats, "rank": args.rank,
        "alpha": args.alpha if args.alpha is not None else args.rank,
        "lr": args.lr, "seed": args.seed, "save_every": args.save_every,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    plan = Plan(
        slug=slug, dataset_dir=dataset_dir, cache_dir=CACHE / slug,
        run_dir=OUTPUT / f"{slug}-{stamp}", output_name=args.name or slug,
        meta=meta, settings=settings, te_hash=captions_hash(images), recipe=recipe,
    )
    marker = plan.cache_dir / "te.hash"
    te_cached = marker.is_file() and marker.read_text().strip() == plan.te_hash
    plan.commands = build_commands(plan, te_cached=te_cached)
    plan.settings["images"] = len(images)
    plan.settings["steps"] = len(images) * args.repeats * args.epochs
    return plan


# ── 2. free the card ──────────────────────────────────────────────────────────

def _post(url: str, body: dict, timeout: float = 10) -> bool:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _get_json(url: str, timeout: float = 5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def free_gpu() -> list[str]:
    said = []
    comfy = os.environ.get("COMFYUI_URL", "").rstrip("/")
    if comfy:
        ok = _post(f"{comfy}/free", {"unload_models": True, "free_memory": True})
        said.append(f"ComfyUI: {'asked to free' if ok else 'not reachable'}")
    ollama = os.environ.get("OLLAMA_URL", "").rstrip("/")
    if ollama:
        ps = _get_json(f"{ollama}/api/ps")
        names = [m.get("name") or m.get("model") for m in (ps or {}).get("models", [])]
        for name in filter(None, names):
            # keep_alive 0 on a request unloads now; nothing is configured on Ollama.
            _post(f"{ollama}/api/generate", {"model": name, "keep_alive": 0}, timeout=30)
        said.append(f"Ollama: unloaded {', '.join(names) if names else 'nothing (none loaded)'}"
                    if ps is not None else "Ollama: not reachable")
    return said


def gpu_memory() -> tuple[float, float] | None:
    """(used GiB, total GiB) of GPU 0, or None without nvidia-smi."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits", "-i", "0"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()[0]
        used, total = (float(x) / 1024 for x in out.split(","))
        return used, total
    except Exception:
        return None


def wait_for_free(min_free_gb: float, seconds: int = 20) -> float:
    free = 0.0
    for _ in range(seconds):
        mem = gpu_memory()
        if mem is None:
            return -1.0
        free = mem[1] - mem[0]
        if free >= min_free_gb:
            return free
        time.sleep(1)
    raise Stop(f"only {free:.1f} GiB of VRAM is free (need {min_free_gb:.1f}). "
               "Something else holds the card — check `nvidia-smi` "
               "(a ranbell render? pause its generation lane while training).")


# ── 4. run ────────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self, log_path: Path):
        self.log = log_path.open("a", encoding="utf-8")
        self.proc: subprocess.Popen | None = None
        self.peak_gb = 0.0
        self.oom = False
        self._oom_seen = False
        self._stop = threading.Event()
        threading.Thread(target=self._watch_vram, daemon=True).start()

    def _watch_vram(self):
        while not self._stop.wait(2):
            mem = gpu_memory()
            if mem:
                self.peak_gb = max(self.peak_gb, mem[0])

    def note(self, line: str):
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()

    def run(self, label: str, cmd: list[str]) -> None:
        self.note(f"\n=== {label}: {shlex.join(cmd)}")
        self.proc = subprocess.Popen(
            cmd, cwd=MUSUBI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True,  # its own process group, killed as one
            env={"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"} | os.environ | {"PYTHONUNBUFFERED": "1"},
        )
        assert self.proc.stdout is not None
        self._oom_seen = False
        for raw in iter(self.proc.stdout.readline, b""):
            text = raw.decode("utf-8", "replace")
            if "OutOfMemoryError" in text or "CUDA out of memory" in text:
                self._oom_seen = True
            sys.stdout.write(text)
            sys.stdout.flush()
            self.log.write(text)
            self.log.flush()  # `status` reads this file while training runs
        code = self.proc.wait()
        self.log.flush()
        self.proc = None
        self.oom = code != 0 and self._oom_seen
        if code != 0:
            raise RuntimeError(f"{label} failed (exit {code}) — see {self.log.name}")

    def kill(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def close(self):
        self._stop.set()
        self.log.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="train", description="Train a LoRA from a ranbell dataset folder "
                                 f"(targets: {', '.join(r.label for r in RECIPES.values())}).")
    ap.add_argument("bucket", nargs="?", help="dataset folder name under /work/datasets")
    ap.add_argument("--preset", choices=ALL_PRESETS, default=None,
                    help="default: the target model's own default preset")
    ap.add_argument("--epochs", type=int, default=16)
    ap.add_argument("--repeats", type=int, default=1, help="times each picture is seen per epoch")
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--alpha", type=int, default=None, help="default: same as rank")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--save-every", dest="save_every", type=int, default=1, help="save every N epochs")
    ap.add_argument("--resolution", type=int, default=None)
    ap.add_argument("--blocks-to-swap", dest="blocks_to_swap", type=int, default=None)
    ap.add_argument("--min-free-gb", dest="min_free_gb", type=float, default=None)
    ap.add_argument("--name", default=None, help="LoRA file name (default: the bucket)")
    ap.add_argument("--convert", action="store_true",
                    help="also write a diffusers-key copy (convert_lora.py --target other); "
                         "not needed for ComfyUI, which reads musubi's keys as they are")
    ap.add_argument("--no-free", dest="free", action="store_false",
                    help="do not ask ComfyUI / Ollama to unload first")
    ap.add_argument("--dry-run", action="store_true", help="check and print the commands; run nothing")
    args = ap.parse_args(argv)

    if not args.bucket:
        have = sorted(p.name for p in DATASETS.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if DATASETS.is_dir() else []
        print("datasets:", ", ".join(have) if have else "(none — export a bucket from ranbell)")
        return 0

    try:
        plan = make_plan(args)
        if not args.dry_run:
            check_models(plan.recipe)
    except Stop as exc:
        print(f"✋ {exc}", file=sys.stderr)
        return 2

    s = plan.settings
    print(f"target   {plan.recipe.label}")
    print(f"dataset  {plan.dataset_dir}  ({s['images']} pictures, trigger {plan.meta.get('trigger')!r}, "
          f"targets {plan.meta.get('targets')})")
    print(f"preset   {s['preset']}: {s['resolution']}px, fp8={s['fp8']}, swap={s['blocks_to_swap']}, "
          f"rank {s['rank']}/{s['alpha']}, lr {s['lr']}, {s['epochs']} epochs × {s['repeats']} = {s['steps']} steps")
    print(f"output   {plan.run_dir}")
    if not any(label == "text_encoder" for label, _ in plan.commands):
        print("cache    captions unchanged — text encoder outputs reused")

    if args.dry_run:
        try:
            check_models(plan.recipe)
        except Stop as exc:
            print(f"(note) {exc}")
        print("\n" + dataset_toml(plan.dataset_dir, plan.cache_dir,
                                  resolution=s["resolution"], repeats=s["repeats"]))
        for label, cmd in plan.commands:
            print(f"[{label}] {shlex.join(cmd)}\n")
        return 0

    if args.free:
        for line in free_gpu():
            print(line)
    try:
        free = wait_for_free(s["min_free_gb"])
        if free >= 0:
            print(f"VRAM free: {free:.1f} GiB")
    except Stop as exc:
        print(f"✋ {exc}", file=sys.stderr)
        return 2

    plan.run_dir.mkdir(parents=True)
    plan.cache_dir.mkdir(parents=True, exist_ok=True)
    (plan.run_dir / "dataset.toml").write_text(
        dataset_toml(plan.dataset_dir, plan.cache_dir, resolution=s["resolution"], repeats=s["repeats"]))
    shutil.copy(plan.dataset_dir / "meta.json", plan.run_dir / "dataset_meta.json")

    runner = Runner(plan.run_dir / "train.log")

    def _on_signal(signum, _frame):
        runner.note(f"\n✋ signal {signum}: stopping the training process group")
        runner.kill()
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)

    started = time.time()
    status = "failed"
    try:
        for label, cmd in plan.commands:
            if label == "train":
                run_train_with_retry(runner, plan)
                continue
            runner.run(label, cmd)
            if label == "text_encoder":
                (plan.cache_dir / "te.hash").write_text(plan.te_hash)
        if args.convert:
            final = plan.run_dir / f"{plan.output_name}.safetensors"
            runner.run("convert", [str(VENV_BIN / "python"), str(MUSUBI / "src/musubi_tuner/convert_lora.py"),
                                   "--input", str(final),
                                   "--output", str(plan.run_dir / f"{plan.output_name}_comfy.safetensors"),
                                   "--target", "other"])
        status = "done"
    except KeyboardInterrupt:
        runner.kill()
        status = "cancelled"
    except RuntimeError as exc:
        runner.note(f"✋ {exc}")
    finally:
        runner.kill()
        minutes = (time.time() - started) / 60
        summary = {
            "status": status, "minutes": round(minutes, 1), "peak_vram_gib": round(runner.peak_gb, 2),
            "settings": s, "files": sorted(p.name for p in plan.run_dir.glob("*.safetensors")),
        }
        (plan.run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        runner.note(f"\n{status}: {minutes:.1f} min, peak VRAM {runner.peak_gb:.1f} GiB → {plan.run_dir}")
        for f in summary["files"]:
            runner.note(f"  {f}")
        runner.close()
        hand_over(plan.run_dir, plan.cache_dir)
    return 0 if status == "done" else 1


MAX_SWAP = 26


def run_train_with_retry(runner: "Runner", plan: Plan) -> None:
    """Train; on out-of-memory, swap 4 more blocks and start again (up to 26)."""
    while True:
        cmd = dict(build_commands(plan, te_cached=True))["train"]
        try:
            runner.run("train", cmd)
            return
        except RuntimeError:
            swap = plan.settings["blocks_to_swap"]
            if not runner.oom or swap >= MAX_SWAP:
                raise
            plan.settings["blocks_to_swap"] = min(MAX_SWAP, swap + 4)
            runner.note(f"\n⚠ out of VRAM with blocks_to_swap={swap}; "
                        f"retrying with {plan.settings['blocks_to_swap']} "
                        "(pass --blocks-to-swap to start there next time)")
            time.sleep(5)  # let the dead process release the card


def hand_over(*dirs: Path) -> None:
    """The container runs as root and musubi saves 0600; give the files to
    whoever owns /work/output on the host, readable, so the LoRA can be used."""
    try:
        st = OUTPUT.stat()
    except OSError:
        return
    for top in dirs:
        for p in [top, *top.rglob("*")]:
            try:
                os.chown(p, st.st_uid, st.st_gid)
                os.chmod(p, 0o775 if p.is_dir() else 0o664)
            except OSError:
                pass



if __name__ == "__main__":
    sys.exit(main())

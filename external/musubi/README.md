# external/musubi — Krea 2 LoRA training container

An **example setup for an external resource**: it reads the LoRA datasets ranbell exports
(pictures + same-name `.txt` + `meta.json`) and trains a Krea 2 LoRA with
[kohya-ss/musubi-tuner](https://github.com/kohya-ss/musubi-tuner). ranbell itself never calls this
container — the only thing they share is the export folder.

The whole flow (bucket → captions → export → train → compare → publish) is in the
[LoRA guide](../../docs/guide/lora.md). 日本語: [README.ja.md](README.ja.md)

The image stays thin; the venv and the sources live in `work/` (CUDA 13.2 / Ubuntu 24.04 / Python 3.12).
Rebuilding keeps the venv and musubi, and swapping the base image is one line in the Dockerfile.

## License

- The files in this folder (compose, Dockerfile, the scripts in `work/bin/`) are MIT, like ranbell.
- musubi-tuner is not bundled; `musubi-setup` clones a pinned commit. musubi-tuner is Apache License 2.0
  (some directories follow their upstream projects' licenses — see the License section of its README).
- The Krea 2 models are under the [Krea 2 Community License](https://krea.ai/krea-2-licensing).
  Distributing a LoRA has conditions, e.g. the name must start with "Krea 2 …" and the notice must be kept.

## Where it lives, and mounts

Copy this folder **outside** the ranbell repository (`work/` grows to tens of GB: venv, models, results).

```sh
cp -r external/musubi /path/to/docker-musubi
cd /path/to/docker-musubi
```

| host | container | for |
|---|---|---|
| `./work` | `/work` | venv, musubi-tuner, `models/`, `cache/`, `output/` |
| `LORA_DATASETS_DIR` in `.env` | `/work/datasets` (read-only) | the folder ranbell's backend has mounted as `/mnt/lora_export` |

On the ranbell side, mount the same folder writable into the backend in `docker-compose.override.yml`:

```yaml
      - /path/to/lora/datasets:/mnt/lora_export
```

When the LoRA section of the admin screen says the export folder is "✓ Writable", it is ready.

## First run

```sh
cp .env.example .env          # LORA_DATASETS_DIR, and the ComfyUI / Ollama URLs (freed before training)
docker compose up -d --build
docker compose exec musubi musubi-setup   # venv and musubi-tuner (pinned commit)
```

Put the models in `work/models/` ([work/models/README.md](work/models/README.md)):

| file | source |
|---|---|
| `raw.safetensors` | huggingface.co/krea/Krea-2-Raw (gated, bf16) |
| `qwen3vl_4b_bf16.safetensors` | huggingface.co/Comfy-Org/Qwen3-VL `text_encoders/` |
| `qwen_image_vae.safetensors` | Comfy-Org/Qwen-Image-Edit_ComfyUI `split_files/vae/` |

**ComfyUI's fp8_scaled files do not work.** musubi reads bf16 and quantizes on its own; an fp8 file
is read without its scales — no error, broken weights.

## Training

```sh
docker compose exec musubi train                      # datasets exported so far
docker compose exec musubi train <bucket> --dry-run   # check only (prints the commands and TOML)
docker compose exec musubi train <bucket>             # 16gb preset (1024px, 16 epochs)
docker compose exec musubi train <bucket> --epochs 30
docker compose exec musubi train <bucket> --preset 16gb-safe   # with something else left on the card
```

What `train` does, in order:

1. **check** — meta.json, a non-empty `.txt` for every picture, the three models; stops before anything heavy
2. **free the GPU** — `POST /free` to ComfyUI, unload loaded Ollama models with `keep_alive: 0`, then wait
   up to 20 s for the preset's free-VRAM minimum, and stop if it is not reached
3. **TOML** — `output/<bucket>-<time>/dataset.toml` (bucketing on, no upscaling)
4. **run** — cache latents → cache text-encoder outputs → train
   - caches live in `cache/<bucket>/`; latents are reused per picture (sha)
   - text-encoder outputs are reused only while the captions hash the same
   - out of VRAM → retried with blocks_to_swap + 4 (up to 26)
5. **report** — `train.log`, `summary.json` (time, peak VRAM, settings), a `.safetensors` per epoch

Ctrl-C, closing the terminal or SIGTERM all stop the whole process group (nothing is left holding the GPU).
Runs are long; run it inside `tmux` or similar.

**Do not render in ranbell while training.** It is the same card; a render starting kills both.
Pause the GEN lane in ranbell's Control Room.

### Watching a run

```sh
docker compose exec musubi status            # latest run
docker compose exec musubi status <bucket>   # latest run of that dataset
docker compose exec musubi status -w 60      # refresh every 60 s (Ctrl-C to leave)
docker compose exec musubi status --list     # every run and its state
```

Progress (step, s/step, time left), per-epoch loss (mean/min/max) with the saved files, and a per-step
loss chart, read from the tensorboard logs in `output/<run>/logs`.

**Loss does not tell you which epoch is good.** Diffusion loss is measured at a random noise level each
step, so it is noisy and drops little. Read it for trouble (a jump, NaN, a run that stopped); judge the
saved epochs in ComfyUI. Once one looks right, Ctrl-C is fine — everything saved so far stays.

## Presets (16 GB cards)

Measured on an RTX 4060 Ti 16 GB (PCIe x8), rank 32.

| | resolution | blocks_to_swap | GC CPU offload | peak VRAM | s/step |
|---|---|---|---|---|---|
| `16gb` (default) | 1024 | 20 | — | 15.5 GiB | 15.3 |
| `16gb-safe` | 1024 | 26 | ✓ | 8.4 GiB* | 16.7* |

\* measured with one-line test captions; `16gb` with 18 pictures and VLM captions (hundreds of words).

Common: fp8 (`--fp8_base --fp8_scaled`), rank/alpha 32, lr 1e-4, adamw8bit,
`--timestep_sampling krea2_shift`, save every epoch, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

Findings:

- **VRAM and speed follow blocks_to_swap** (~0.45 GiB per block); with gradient checkpointing the
  resolution matters little, so 1024 (what Raw was trained up to) is the default
- **Caption length costs VRAM too** (Krea 2 puts the text tokens in the same sequence as the image).
  Swap 16 fit one-line captions at 13.6 GiB and ran out on step 2 with real captions
- The desktop (browser, editor…) holds about 1 GiB of the card; leave room for it
- `16gb-safe` is for training with ComfyUI or similar still on the card

### Fixed cost per run

Before training, loading the 26 GB DiT and quantizing it to fp8 takes 1–3 minutes — fast when it was
just read (page cache), slower from an HDD. Latents are re-cached only for new pictures or a new resolution.

## The LoRA

`output/<bucket>-<time>/<bucket>.safetensors` (intermediate epochs as `-000004` and so on).
Use it with Turbo models (Krea's recommendation: train on Raw, infer on Turbo). Copy it to ComfyUI yourself.
Keys are `lora_unet_blocks_*` (792), which ComfyUI loads as is — **no conversion needed**.
The container runs as root; outputs are handed back to the owner of `work/output/` on the host.

## A copy to publish (Civitai etc.)

```sh
docker compose exec musubi publish <bucket>              # final LoRA of the latest run
docker compose exec musubi publish <bucket> --epoch 20   # that epoch
docker compose exec musubi publish <bucket> --all        # drop every metadata field
```

Writes `output/<run>/publish/`. The weights are copied byte for byte; only the metadata header changes.

- **kept**: training settings (rank, lr, epochs, optimizer, resolution…) — useful to users
- **removed**: training times, session id, base model / VAE file names, dataset folder names
- musubi never writes captions or tags; `ss_tag_frequency` and the like are removed in case a LoRA
  from another trainer is passed in

## Tests

```sh
python3 -m pytest tests     # needs neither a GPU nor musubi
```

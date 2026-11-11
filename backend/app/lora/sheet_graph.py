"""The ComfyUI graph for one cell of a LoRA comparison sheet, built from nothing.

No saved workflow is used: the graph is made of ComfyUI's own nodes only, so it
needs no custom node pack, has exactly one output (a SaveImage — no preview
node whose temp file would also land in the gallery), and every cell of a
sheet differs from the others in nothing but the LoRA and its strength.

The LoRA goes in through ``LoraLoaderModelOnly`` between the model loader and
the sampler. Krea 2 LoRAs from musubi-tuner train the diffusion model only (the
text encoder's outputs are cached), so there is no clip strength to set. A
strength of 0 builds the graph without the LoRA node at all: that column is the
model as it is, the baseline the others are read against.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Every class_type a sheet graph may contain — all of them ship with ComfyUI.
CORE_NODES = frozenset({
    "UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly",
    "CLIPTextEncode", "ConditioningZeroOut", "EmptyLatentImage",
    "KSampler", "VAEDecode", "SaveImage",
})

SAVE_PREFIX = "lora_sheet"


class SheetGraphError(ValueError):
    """The settings cannot make a graph (a model file is missing, a size is off)."""


@dataclass(frozen=True)
class Models:
    unet: str
    clip: str
    vae: str


@dataclass(frozen=True)
class Sampling:
    seed: int
    width: int
    height: int
    steps: int
    cfg: float
    sampler: str
    scheduler: str


def _check(models: Models, sampling: Sampling) -> None:
    missing = [k for k in ("unet", "clip", "vae") if not getattr(models, k)]
    if missing:
        raise SheetGraphError("model files not chosen: " + ", ".join(missing))
    for side in ("width", "height"):
        v = getattr(sampling, side)
        if v < 64 or v > 4096 or v % 16:
            raise SheetGraphError(f"{side} must be a multiple of 16 between 64 and 4096: {v}")
    if sampling.steps < 1:
        raise SheetGraphError("steps must be at least 1")


def build_krea2(*, models: Models, sampling: Sampling, prompt: str,
                lora: str | None = None, strength: float = 0.0) -> dict:
    """Krea 2: DiT + Qwen3-VL text encoder + Qwen-Image VAE, no negative prompt
    (the negative is the positive zeroed out, as Krea 2 runs at cfg 1)."""
    _check(models, sampling)
    g: dict[str, dict] = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models.unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models.clip, "type": "krea2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": models.vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptyLatentImage",
              "inputs": {"width": sampling.width, "height": sampling.height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
            "latent_image": ["6", 0], "seed": int(sampling.seed), "steps": int(sampling.steps),
            "cfg": float(sampling.cfg), "sampler_name": sampling.sampler,
            "scheduler": sampling.scheduler, "denoise": 1.0,
        }},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": SAVE_PREFIX}},
    }
    if lora and strength:
        g["10"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"model": ["1", 0], "lora_name": lora,
                              "strength_model": float(strength)}}
        g["7"]["inputs"]["model"] = ["10", 0]
    return g

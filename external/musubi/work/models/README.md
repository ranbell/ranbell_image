# Models (not in git)

One folder per target model. The trainer looks in `models/<target>/` first, then directly in
`models/` (the flat layout of the first Krea 2 setup still works). `KREA2_DIT` / `KREA2_VAE` /
`KREA2_TE` in `.env` override a single file.

```
models/
  krea2/
    raw.safetensors              # Krea 2 Raw DiT (bf16, ~26 GB)
    qwen3vl_4b_bf16.safetensors  # Qwen3-VL-4B text encoder (bf16, single file, ~8.9 GB)
    qwen_image_vae.safetensors   # Qwen-Image VAE (~250 MB)
```

| file | source |
|---|---|
| `raw.safetensors` | huggingface.co/krea/Krea-2-Raw (gated — accept the license first) |
| `qwen3vl_4b_bf16.safetensors` | huggingface.co/Comfy-Org/Qwen3-VL `text_encoders/` |
| `qwen_image_vae.safetensors` | huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI `split_files/vae/` |

ComfyUI's fp8-scaled checkpoints (`*_fp8_scaled.safetensors`) do **not** work here:
musubi's Krea 2 loader expects bf16 and quantizes on its own (`--fp8_scaled`).
It would cast the fp8 weights without their scales — no error, broken weights.

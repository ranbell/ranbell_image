# Models for Krea 2 LoRA training (not in git)

| file | source |
|---|---|
| `raw.safetensors` | huggingface.co/krea/Krea-2-Raw (gated, bf16) |
| `qwen3vl_4b_bf16.safetensors` | huggingface.co/Comfy-Org/Qwen3-VL `text_encoders/` |
| `qwen_image_vae.safetensors` | Comfy-Org/Qwen-Image-Edit_ComfyUI `split_files/vae/` |

ComfyUI's fp8-scaled checkpoints (`*_fp8_scaled.safetensors`) do **not** work here:
musubi's Krea 2 loader expects bf16 and quantizes on its own (`--fp8_scaled`).
It would cast the fp8 weights without their scales — no error, broken weights.

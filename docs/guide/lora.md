# Making a LoRA — from the gallery to a LoRA (Krea 2 for now)

Gather pictures you like from the gallery into a **bucket**, and a VLM writes training captions for them.
When the set is ready, export it as a folder; training happens outside ranbell, in the trainer container
([external/musubi](../../external/musubi/README.md)). 日本語: [lora.ja.md](lora.ja.md)

```
gallery ──▶ bucket ──▶ captions ──▶ export ──▶ train ──▶ compare in ComfyUI ──▶ publish
 (pick)    (gather)   (VLM, auto)   (folder)   (musubi)
```

ranbell's part ends at the export. Starting and watching a training run are commands of the trainer container.

---

## 0. Setup (once)

1. **Mount the export folder.** In ranbell's `docker-compose.override.yml`, mount a host folder into the
   backend as `/mnt/lora_export` (writable) and recreate the backend.
   ```yaml
         - /path/to/lora/datasets:/mnt/lora_export
   ```
2. **Check it in the admin screen.** Among the model settings, the two 📦 fields: under "export folder"
   it should say "✓ Writable". The other, "caption model", falls back to the VLM model when empty —
   if you pick one, pick a **model that can read images**.
3. **Prepare the trainer.** Follow [external/musubi's README](../../external/musubi/README.md) to build the
   container, and put the three models Krea 2 training needs in place:

   ```
   docker-musubi/work/models/krea2/
     raw.safetensors               Krea 2 Raw (bf16, ~26 GB; accept the license on Hugging Face first)
     qwen3vl_4b_bf16.safetensors   text encoder (bf16, single file)
     qwen_image_vae.safetensors    VAE
   ```

   ComfyUI's fp8 files (`*_fp8_scaled`) **do not work** — the trainer reads bf16 and quantizes on its own.
   The full layout and download sources are in [external/musubi, "File layout"](../../external/musubi/README.md#file-layout-krea-2).

---

## 1. Gather pictures

Select pictures in the gallery and press **"📦 LoRA bucket"** in the tray at the bottom.

- For a new bucket, pick the **model** at the top left, type a name and press "Create with N".
  The model is which image model the LoRA is for (Krea 2 only for now); captions read differently per model.
- To add to an existing one, pick it in the list and press "Add N selected". Pictures already in it are skipped.

Captioning starts in the background as pictures come in (it pauses itself while a render runs).

**How many, and which?**

| you want | pictures | how to pick |
|---|---|---|
| a style (rendering, lines, palette) | 30–50 | **same style, varied content** — people, objects, scenery, framing |
| a character | 20–30 | **same face, everything else varied** — expressions, angles, outfits, backgrounds |

If every picture shares a composition or a prop, that gets learned too.
768 px on the short side is plenty. Resizing happens at training time (never upscaled).

---

## 2. Trigger word and targets

Set both at the top of the bucket screen and press **"Save and apply"**.
(Changing the "Model" in the same row later describes every picture again; hand-edited rows are kept and marked.)

### Trigger word

The word that calls the LoRA; it goes in front of every caption.
Use a **made-up word** (`zqx_style`, `rnbl_girl`). A real word mixes with what the model already knows.

### Targets (keywords)

What you list here, and the descriptions it stands for, **is removed from the captions**.

> **The trigger word learns what the captions leave undescribed.**
> So remove what it should learn, and describe everything else.

| you want | targets, e.g. | leave out |
|---|---|---|
| a style (flat etc.) | `flat colors, flat shading, cel shading, vector art, minimal shading, no gradients` | the content: people, clothes, backgrounds |
| a character | their look (`white hair, blue eyes, hair ornament`…) | clothes, poses, backgrounds (things you want to change) |

- A **medium word such as "anime illustration" gets learned too** if you add it. Leave it out to learn the style only.
- Colour or shading targets do not remove the colours of hair and clothes — only the words about rendering.
- Targets can change later; only the removal step runs again, the pictures are not re-read.

---

## 3. Check the captions

When everything is "done", open **"Full"** on a few rows and compare with the final caption.

- **A word that should be gone is still there** (`minimalist`, `stylized` for a style) → add it to the targets and apply
- **Content disappeared too** → edit that row directly (saved when you click outside)

Hand edits are never overwritten by a recompute. To rebuild them after changing the targets, press
"Rebuild hand-edited too". Remove off-style pictures with "Remove" so the result does not blur.

---

## 4. Export

**"Export folder"** writes `<bucket>/` into the export folder:

```
<export folder>/<bucket>/
  <sha>.png   picture, converted to PNG with every bit of metadata removed
  <sha>.txt   caption
  meta.json   model, trigger, targets, each picture's size
```

JPEG and WebP pictures become PNG too. The prompt, the ComfyUI workflow, EXIF and colour
profiles are all dropped (orientation and colour are applied to the pixels first, so nothing
looks different). Nothing says where the originals live, so the dataset can be handed on as it is.

The trainer reads the model from `meta.json` and picks that model's recipe.

- When it is done, "Export finished (N pictures)" pops up.
- Pending or failed pictures block the export (hover the button for the reason).
- Exporting again replaces the whole folder. "Download zip" gives the same as a zip.

---

## 5. Train

**Pause the GEN lane in ranbell's Control Room first.** It is the same GPU; a render during training kills both.

```sh
cd /path/to/docker-musubi
docker compose exec musubi train <bucket> --dry-run   # check, run nothing
docker compose exec musubi train <bucket> --epochs 30
```

- ComfyUI and Ollama are asked to free the card before training.
- A LoRA is saved every epoch. Total steps = pictures × epochs.
- Time on a 16 GB card: about 15 s a step; 18 pictures × 30 epochs ≈ 2 h 20 min.
- Run it inside `tmux` or similar. Ctrl-C stops it; everything saved so far stays.

### Watching

```sh
docker compose exec musubi status -w 60
```

Progress, time left, per-epoch loss and saved files. **Loss is for spotting trouble (jumps, NaN);
judge quality by the pictures.**

---

## 6. Compare on a sheet

When training ends, resume the GEN lane and copy LoRAs from `output/<bucket>-<time>/` into
ComfyUI's loras folder. To compare epochs, keep them **in a subfolder**
(`loras/my_style/my_style-000010.safetensors`). Start with 3–4 spaced apart (e.g. epochs 10, 20, 30).

ranbell's **📊 LoRA comparison sheet** draws them with one seed and one prompt, each LoRA across strengths.

- Three ways in:
  - "📊 Compare this bucket's LoRAs" on the bucket screen (the LoRA list filtered by the bucket name, and the bucket's trigger filled in as the trigger for rows you add)
  - select one picture in the gallery, then "📊 Compare LoRAs" in the tray (its prompt, seed and models carry over, and its aspect ratio — set the size in megapixels, 1 MP = 1024×1024)
  - open it with nothing selected and set it up from scratch
- The new-sheet form is kept as a draft and comes back when the screen is reopened; "Reset" starts from the defaults
- **Rows are LoRAs, columns are strengths** (0 / 0.25 / 0.5 / 0.75 / 1 / 1.25 by default). **The 0 column has no LoRA**:
  it is the bare model, the baseline. Rows with the same trigger share one 0 picture
- Each row's trigger goes in front of the prompt (do not write it in the prompt box)
- "No-trigger column" adds strength 1 without the trigger, to spot an overbaked LoRA
- ranbell builds the graph from ComfyUI's core nodes (no saved workflow needed; the LoRA goes in through
  `LoraLoaderModelOnly`). Pick the DiT, text encoder and VAE from ComfyUI's folders; sampling defaults to Turbo (8 steps / cfg 1)
- Cells are drawn through the job scheduler and fill in as they finish. Stopping keeps what is drawn; "Draw the rest" finishes it
- "🎲 Duplicate with a new seed" makes the same sheet on another seed, so one lucky seed does not decide
- "⬇ Save PNG" gives one labelled picture (no metadata; fine to post)
- The list on the left can be searched and grouped by LoRA, by model or by date. Click a sheet's title to rename it
- **Side by side**: tick two or more sheets in the list and press "⇅ Compare". They stack on shared strength columns, and
  each sheet's heading shows **only what differs** (model, seed, prompt…) — official Turbo against a merge, or one seed against another.
  This view saves as a PNG too
- **Compare board**: pin up to four cells with 📌 (or `P` in the full-size view) and open the board. They sit large side by side,
  and zoom (wheel) and pan (drag) move all of them together, so hands or background details are compared in the same spot

Anyone's Krea2 LoRAs in the loras folder can sit on the same sheet.
Another sheet with a prompt unlike the training set (cars, food, streets) shows whether it generalizes.

| you see | then |
|---|---|
| weak early, right later on | good — pick the most natural epoch |
| already strong at an early epoch | pick an early one; next time train shorter |
| weak to the end | more pictures or more epochs, another run |
| works without the trigger; props or compositions of the training pictures show up | too strong — an earlier epoch or a lower strength |

How it reacts can differ between base models (official Krea 2 Turbo vs. fine-tunes).

---

## 7. Publish (optional)

```sh
docker compose exec musubi publish <bucket> --epoch 20
```

Writes a copy without training times, file names and so on to `output/<run>/publish/` (weights unchanged).

Distributing a Krea 2 LoRA follows section 3 of the [Krea 2 Community License](https://krea.ai/krea-2-licensing):

- start the model name with **"Krea 2"** (e.g. "Krea 2 Flat Style")
- say in the description that it was trained on (modified from) Krea 2
- keep the license notice (Civitai shows it when Krea 2 is chosen as the base model)
- do not suggest it is an official or endorsed Krea product

For users, add the trigger word, a recommended strength, and the base models you tried.

---

## When something is off

| symptom | fix |
|---|---|
| captions stay "to describe" | they pause while rendering. Check the Control Room: is the ALIGNMENT (evaluation) lane paused? Then press "Resume" |
| caption error "may not be able to read images" | pick an image-reading model in the 📦 caption model setting |
| "Export folder missing" | check the backend mount (until the admin screen says "✓ Writable") |
| training runs out of VRAM | it retries with more swapped blocks; if it still fails, `--preset 16gb-safe` |
| training is slow | revisit pictures × epochs; if an earlier epoch is good enough, Ctrl-C is fine |

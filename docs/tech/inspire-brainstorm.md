# Inspire & Brainstorm — Technical Reference

**Ranbell Image v0.1.0**

This document covers the technical design of the Inspire panel and the Brainstorm feature: what each mode does, which technologies it uses, and how the underlying mechanisms work.

---

## Table of Contents

1. [The Core Idea — Images as Semantic Coordinates](#1-the-core-idea--images-as-semantic-coordinates)
2. [Technology at a Glance](#2-technology-at-a-glance)
3. [Mode-by-Mode Reference](#3-mode-by-mode-reference)
   - [Serendipity](#serendipity-)
   - [Alchemy](#alchemy-)
   - [Morph](#morph-)
   - [Anomaly](#anomaly-)
   - [Inversion](#inversion-)
   - [Discovery](#discovery-)
   - [Group Search](#group-search-)
   - [Blend](#blend-)
   - [Outlier](#outlier-)
4. [Brainstorm](#4-brainstorm-)
5. [The Complete Creative Pipeline](#5-the-complete-creative-pipeline)
6. [Vector Operations — What's Actually Happening](#6-vector-operations--whats-actually-happening)

---

## 1. The Core Idea — Images as Semantic Coordinates

Every image in Ranbell Image is converted into a **high-dimensional embedding vector** — a sequence of numbers (768 dimensions) that encodes the visual meaning of the image: its composition, color palette, style, mood, subject matter, and atmosphere, all compressed into a single mathematical object.

Once images are represented this way, they can be treated as **coordinates in a meaning space**. Images with similar visual meaning sit close together; images with entirely different worlds sit far apart.

This transformation is what makes the Inspire modes possible. Instead of asking "which images have this filename?" or "which images are tagged with this keyword?", the system can ask questions like:

| Question | What it means mathematically |
|---|---|
| "Find images similar to this one" | Find nearby coordinates |
| "Add concept A, subtract concept B" | Compute A + B − C, find the nearest image to the result |
| "Show images between A and B" | Interpolate along the line from A to B |
| "Find the opposite of this" | Negate the vector, find the nearest result |
| "Find the most unusual image in my collection" | Find the coordinate most isolated from all others |

Three different "readings" of an image are used depending on the mode:

- **As a vector** — the full embedding; encodes everything at once
- **As tags** (WD14) — Danbooru category labels; the visual vocabulary as language
- **As a coordinate** (UMAP) — a 2D position in the projected map of the collection

---

## 2. Technology at a Glance

### Mode × Technology Matrix

| Mode | Vector averaging | Iterative normalization | Linear interpolation (LERP) | Weighted composition | Vector sign inversion | Qdrant standard search | Qdrant DiscoverQuery | Qdrant GroupBy | LLM tag generation | VLM image analysis | VLM 3-stage pipeline | WD14 tags | UMAP density | SSE streaming | No reference needed |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| ✨ Serendipity | ● | — | — | — | — | ● | — | — | — | — | — | — | — | — | — |
| ⚗️ Alchemy | — | ● | — | — | — | ● | — | — | — | — | — | — | — | — | — |
| 🌊 Morph | — | ● | ● | — | — | ● | — | — | — | — | — | — | — | — | — |
| ⚡ Anomaly | ● | — | — | — | — | ● | — | — | ● | — | — | ● | — | — | — |
| 🪞 Inversion | ● | — | — | — | — | ● | — | — | — | ● | ● | ● | — | ● | — |
| 🧭 Discovery | — | — | — | — | — | — | ● | — | — | — | — | — | — | — | — |
| 🗂️ Group Search | — | — | — | — | — | — | — | ● | — | — | — | — | — | — | ● |
| ⚖️ Blend | — | — | — | ● | — | ● | — | — | — | — | — | — | — | — | — |
| 🌌 Outlier (Antipode) | ● | — | — | — | ● | ● | — | — | — | — | — | — | — | — | ● |
| 🌌 Outlier (Isolated) | — | — | — | — | — | ◐ | — | — | — | — | — | — | ● | — | ● |
| 💡 Brainstorm | — | — | — | — | — | — | — | — | ● | — | — | ● | — | ● | — |

● = core technology · ◐ = conditional · — = not used

### Complexity Classification

```
Pure vector math — fast, deterministic
  ├── Alchemy       — vector addition/subtraction only
  ├── Morph         — linear interpolation only
  └── Blend         — weighted linear combination only

Vector math + probabilistic elements
  ├── Serendipity   — random sampling from a percentile band
  └── Outlier       — sign inversion or density analysis

Vector math + advanced Qdrant features
  ├── Discovery     — DiscoverQuery
  └── Group Search  — GroupBy + text embedding

LLM/VLM integration — slower, non-deterministic, expressive
  ├── Anomaly       — LLM tag generation (lightweight)
  ├── Brainstorm    — LLM text generation (medium)
  └── Inversion     — VLM 3-stage pipeline (heaviest)
```

---

## 3. Mode-by-Mode Reference

---

### Serendipity ✨

**What you can do:** Find images that are similar to your reference but not *too* similar — the sweet spot between "already familiar" and "completely unrelated".

**What you provide:** 1–6 reference images.

**How it works:**

The reference images' embeddings are averaged into a single query vector. Qdrant returns the top 1,000 nearest neighbors by cosine similarity, ranked by score. The system then computes the 25th and 75th percentile scores of those results, and samples randomly from the band between P25 and P75 — the "moderately similar" zone.

This percentile-based approach automatically adapts to the score distribution of whatever embedding model is in use. The result is always "interestingly different but contextually related" regardless of absolute score values.

**Why percentiles matter:** A fixed similarity threshold (e.g., 0.5–0.7) fails because different embedding models produce very different score distributions. Percentile bands anchor to the *actual* distribution of your collection, not an arbitrary constant.

**Best for:** Breaking out of creative ruts. Discovering images you forgot you had. Getting a fresh angle on a familiar theme.

---

### Alchemy ⚗️

**What you can do:** Combine or subtract visual concepts using actual vector arithmetic. "The color palette of image A, plus the composition of image B, minus the urban setting of image C."

**What you provide:** Images to add (up to 3) and images to subtract (up to 3).

**How it works:**

Each image's embedding vector is individually normalized to unit length before being added or subtracted. Normalization happens at every step (not just at the end) — this is called **iterative normalization**, and it prevents any single image from dominating the result simply because its embedding happens to have a larger magnitude.

The resulting composite vector points toward the region of the embedding space that best satisfies the combination you specified. Qdrant then finds the images nearest to that region.

**The key insight:** Once every image is a coordinate, vector addition and subtraction are real operations with real effects. Subtracting "outdoor scenes" from "fashion photographs" actually moves the query vector away from the outdoor cluster and toward the indoor fashion cluster.

**Best for:** Expressing creative intent that's hard to describe in words. "I want something like this, with some of that, but without the other thing."

---

### Morph 🌊

**What you can do:** See the five images in your collection that form the most natural gradient between two concepts — the intermediate stages from image A to image B.

**What you provide:** 2 images (A and B).

**How it works:**

Linear interpolation (LERP) is computed at five evenly spaced steps between the two embedding vectors: t = 0.2, 0.4, 0.6, 0.8, and the endpoint. At each step, the interpolated vector is normalized, then used to search Qdrant for the nearest images in the collection.

The result is a timeline of 5 steps × 4 images per step, showing how the collection transitions from the aesthetic world of A to the aesthetic world of B.

**Best for:** Transition aesthetics — moving from day scenes to night scenes, from quiet to energetic, from realistic to stylized. Useful for finding what visually bridges two very different images you like.

---

### Anomaly ⚡

**What you can do:** Find images that contain unexpected, rare element combinations relative to your selection — surprises that are contextually grounded, not just random.

**What you provide:** 1–6 reference images.

**How it works:**

The top 30 WD14 tags from the reference images are collected and their frequencies counted. These common tags are sent to the LLM (Ollama) with a prompt that says, in effect: "given these dominant tags, suggest 3 Danbooru-compatible tags that would be rare or unexpected in this context." The LLM proposes anomaly tags that are semantically plausible but statistically unusual given the reference.

The original tags plus the 3 anomaly tags are concatenated and embedded as a text query, then used for vector search. The result surfaces images containing those unusual element combinations. The response includes the anomaly tags so you can see what the LLM injected.

**Best for:** Creative surprises that make sense in context. Breaking compositional habits. Finding images with unexpected elements that still relate to your starting point.

---

### Inversion 🪞

**What you can do:** Design a semantically opposite version of your reference image's world along five axes — and find images in your collection that inhabit that opposite world.

**What you provide:** 1–3 reference images.

**The five inversion axes:**

| Axis | What gets inverted |
|---|---|
| **Visual** | Light ↔ dark, warm ↔ cool, dense ↔ sparse, interior ↔ exterior |
| **Mood** | Calm ↔ chaotic, joyful ↔ melancholy, active ↔ still |
| **Subject** | Age, expression, posture, number of figures |
| **Style** | Detailed ↔ minimal, saturation shifts |
| **Narrative** | Everyday ↔ fantastical, familiar ↔ alien |

**Constraint:** Character-defining features (hair color, eye color, specific persons) are *not* inverted. The world changes; the character remains.

**How it works — 3-stage VLM pipeline:**

This is the most compute-intensive mode. It runs three sequential LLM/VLM passes, streamed to the browser via Server-Sent Events:

**Stage 1 — Generation:** The VLM analyzes the reference images and generates:
- A narrative (300–500 words) describing the inverted world
- 100–150 Danbooru/WD14 tags for the inverted concept
- A natural-language prose prompt (150–200 words)
- 20–40 negative tags (elements of the original to exclude)

**Stage 2 — Validation:** A second VLM pass reviews the Stage 1 output for internal consistency and safety. It produces a structured report of any issues found.

**Stage 3 — Refinement** (only if Stage 2 found issues): A third VLM pass rewrites the output to address the validation feedback.

The final tags and prose are embedded and used to search Qdrant for matching images.

**Why the 3-stage pipeline?** Without validation, VLM outputs sometimes contradict themselves (e.g., inverting "outdoor" to "indoor" but keeping outdoor-specific tags). The self-correction loop produces significantly more coherent results without any human review.

**Best for:** "What if the world in this image were completely different?" Creative direction exploration. Finding visual counterparts to images you already like.

---

### Discovery 🧭

**What you can do:** Explore a direction in the collection using complex intent — "close to this target, leaning toward this kind of image, and away from that kind."

**What you provide:** A target image, plus additional images marked as positive (desired direction) or negative (direction to avoid).

**How it works:**

This mode uses Qdrant's `DiscoverQuery` — a special query type that optimizes for both proximity to the target *and* the directional context implied by the positive/negative pairs.

Where standard search asks "what's nearest to X?", DiscoverQuery asks "what's nearest to X *while also being more like P than like N*?" Each positive/negative pair you provide contributes additional directional pressure on the results.

**Best for:** Controlled exploration. When you want to search near a target but nudge the results in a specific direction without doing full vector arithmetic manually.

---

### Group Search 🗂️

**What you can do:** Run a natural-language semantic query and see results grouped by generation model, file type, or other collection attributes — comparing how different models interpreted the same concept.

**What you provide:** A text query.

**How it works:**

The query text is embedded by Ollama into a vector, which is used to search Qdrant via the `GroupBy` API. Results are partitioned into groups (by model name, file extension, etc.) and returned as `{group_id: [images]}` — typically 3 images per group, up to 10 groups.

**Best for:** Cross-model comparison. "How did DreamShaper, SDXL Turbo, and Anima each interpret 'twilight on the beach'?" Useful for choosing which model to use for a specific style direction.

---

### Blend ⚖️

**What you can do:** Mix visual concepts from multiple images with precise per-image weight control, from −1.0 (strongly subtracted) to +1.0 (strongly included).

**What you provide:** 2–4 images, each with a weight slider.

**How it works:**

Each image's embedding is normalized to unit length, then scaled by its weight before being summed. The final sum is normalized again and used to search Qdrant. Images with weight > 0.5 are excluded from the results (since the point is to find something *related to* them, not the images themselves).

**Blend vs. Alchemy:**
- Alchemy assigns binary roles (+1 or −1) and uses iterative normalization to prevent magnitude drift
- Blend allows continuous weights anywhere in [−1.0, +1.0] and uses a weighted linear combination

Use Alchemy for clear conceptual addition/subtraction. Use Blend when you want to express "60% of A, 30% of B, 10% of C" — nuanced, proportional mixing.

**Best for:** Fine-grained mood mixing. Composing a specific target aesthetic from multiple reference images with individual tuning.

---

### Outlier 🌌

**What you can do:** Find the most extreme or isolated images in your collection — the ones that are semantically furthest from everything else.

**What you provide:** Nothing (no reference needed) — this mode characterizes the collection itself.

**Two sub-modes:**

**Antipode (mathematical opposite):**
All images in the collection are averaged into a single centroid vector representing the "typical" image. This centroid vector is sign-inverted (every element multiplied by −1), pointing toward the mathematical opposite of the average. The images nearest to this antipodal point are the ones furthest from the collection's center of gravity.

**Isolated (density-based):**
UMAP 2D coordinates stored in each image's metadata are used to compute a local density for every image — the count of other images within a radius of 2.0 units. Images with the lowest density (fewest neighbors in 2D semantic space) are identified as isolated. A random sample from the lowest-density candidates is returned.

Fallback: if UMAP coordinates haven't been computed yet, random sampling is used.

**Best for:** Discovering your most unique or experimental work. Finding images that don't fit any cluster. Surfacing the outliers that represent creative experiments or stylistic departures.

---

## 4. Brainstorm 💡

**What you can do:** Take a set of discovered images and ask the LLM to generate 3–5 creative scene ideas that build on what those images suggest — then send any idea directly to the Synthesis studio.

**What you provide:** Images you've discovered through any Inspire mode (selected as brainstorm input). If Anomaly or Inversion was used, their generated tags are also automatically included.

**How it works:**

WD14 tags from the selected images are collected and merged with any extra tags from previous Anomaly or Inversion runs. This combined tag vocabulary is sent to the LLM (Ollama) with a prompt requesting 3–5 creative, visually distinct scene proposals in Markdown format.

The response streams back to the browser via Server-Sent Events — you see the ideas form in real time. Each generated scene can be selected and sent directly to the Synthesis studio, where it becomes the starting instruction for prompt generation.

**Language:** Brainstorm supports both English and Japanese output.

**Best for:** The gap between "I found something interesting" and "I know what to create next." Brainstorm converts visual inspiration into actionable creative direction.

---

## 5. The Complete Creative Pipeline

The Inspire modes and Brainstorm are not isolated tools — they form a connected pipeline from discovery to generation:

```
Discovery
  └── Serendipity / Anomaly / Inversion / Outlier / ...
      Find images you didn't know you were looking for

        ↓

Analysis  (automatic)
  └── WD14 tags collected from results
      Extra tags from Anomaly / Inversion injected
      Visual vocabulary translated into language

        ↓

Ideation
  └── Brainstorm
      LLM generates 3–5 scene proposals from the tag vocabulary
      Results streamed in real time

        ↓

Refinement
  └── Synthesis Studio
      Reference images + brainstorm idea → VLM generates a full prompt
      Output style: Danbooru / natural language / hybrid

        ↓

Generation
  └── ComfyUI (one-click submit)
      Generated image added to collection
      Collection grows → future Inspire searches become richer
```

Each generated image is indexed back into the collection, increasing the density and richness of the semantic map for future exploration.

---

## 6. Vector Operations — What's Actually Happening

### Why normalization matters

All embedding vectors are normalized to unit length before comparison. This ensures that similarity is measured by **direction** (the angle between vectors in the high-dimensional space), not by magnitude. Without normalization, a vector that happens to have a large magnitude would dominate in addition operations even if its semantic content is less relevant.

### Iterative normalization (Alchemy, Morph)

In modes that add multiple vectors together, re-normalizing after each addition prevents "magnitude drift" — the tendency for the sum to grow in magnitude with each addition, causing later additions to have proportionally less effect. By normalizing at every step, every image contributes equally regardless of how many were added before it.

### Cosine similarity and dot product equivalence

Qdrant performs searches using dot product on normalized vectors. Since both the stored vectors and the query vectors are normalized to unit length, the dot product equals the cosine similarity. This means the search returns images in order of semantic angle distance — the most geometrically close in meaning first.

### MRL — Two-phase search

Ranbell Image uses Matryoshka Representation Learning (MRL) for all Qdrant searches. A compact 256-dim prefix of each embedding is stored alongside the full 768-dim vector. Searches run in two phases:

1. **Prefetch** — the 256-dim vectors scan the full collection quickly, returning a candidate set
2. **Rerank** — the full 768-dim vectors score the candidates precisely

This two-phase design makes search fast on large collections without sacrificing accuracy.

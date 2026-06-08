# Inspire & Brainstorm — Technical Reference

**Ranbell Image v0.1.0**

---

## Table of Contents

1. [Embedding Infrastructure](#1-embedding-infrastructure)
2. [Mathematical Foundations](#2-mathematical-foundations)
3. [Technology at a Glance](#3-technology-at-a-glance)
4. [Mode Algorithms — Full Specification](#4-mode-algorithms--full-specification)
   - [Serendipity](#serendipity-)
   - [Alchemy](#alchemy-)
   - [Morph](#morph-)
   - [Anomaly](#anomaly-)
   - [Inversion](#inversion-)
   - [Discovery](#discovery-)
   - [Group Search](#group-search-)
   - [Blend](#blend-)
   - [Outlier](#outlier-)
5. [Brainstorm — LLM Tag Pipeline](#5-brainstorm--llm-tag-pipeline)
6. [Qdrant Query Patterns](#6-qdrant-query-patterns)
7. [VLM 3-Stage Pipeline (Inversion Deep-Dive)](#7-vlm-3-stage-pipeline-inversion-deep-dive)
8. [The Complete Creative Pipeline](#8-the-complete-creative-pipeline)

---

## 1. Embedding Infrastructure

### 1.1 Embedding model and dimensions

| Parameter | Value |
|---|---|
| Vector dimensions | 768 |
| MRL prefix dimensions | 256 |
| Similarity metric | Cosine (dot product on normalized vectors) |
| Qdrant collection | `images` |

### 1.2 Qdrant vector layout (MRL)

Each image document holds two named vectors.

```
Qdrant point {
  id:      sha256 hash (string)
  vectors: {
    "full":    float[768]   ← full-precision vector (for reranking)
    "mrl256":  float[256]   ← MRL prefix (for prefetch)
  }
  payload: {
    path:             string
    wd14_tags:        string[]
    wd14_tags_scores: float[]
    umap_x:           float     ← used by Isolated Outlier mode
    umap_y:           float
    model_name:       string    ← used by Group Search
    extension:        string
    ...
  }
}
```

### 1.3 Two-phase MRL search

All Qdrant searches execute in two phases.

```
Phase 1: Prefetch
  Query (mrl256) × full collection (mrl256)
  → top N×k candidate set (fast, lower precision)

Phase 2: Rerank
  Candidate set × query (full) × candidates (full)
  → top k results at full precision

N = prefetch multiplier (typically 5–10)
k = final result count
```

---

## 2. Mathematical Foundations

### 2.1 L2 normalization

```
norm(v) = v / ‖v‖₂

‖v‖₂ = √(v₁² + v₂² + ... + vₙ²)

After normalization: ‖norm(v)‖₂ = 1.0
```

All embeddings are projected onto the unit sphere before comparison. This ensures similarity is measured by **direction (angle)** rather than magnitude.

### 2.2 Cosine similarity and dot product equivalence

```
cos(θ) = (u · v) / (‖u‖₂ × ‖v‖₂)

When u and v are unit vectors:
  cos(θ) = u · v

∴ Qdrant dot product search ≡ cosine similarity search (on normalized vectors)
```

### 2.3 Iterative normalization

Used when adding multiple vectors. Without it, repeated addition causes magnitude to grow (≈ √n per step), making later additions proportionally less influential.

```
Iterative normalization algorithm:
  v₀ = norm(a₁)
  vᵢ = norm(vᵢ₋₁ + norm(aᵢ₊₁))   ← re-normalize after each addition

vs. naive sum:
  v_naive = a₁ + a₂ + ... + aₙ    ← ‖v_naive‖ grows to ≈ √n
```

### 2.4 Linear interpolation (LERP)

```
LERP(A, B, t) = (1 − t) · A + t · B,  t ∈ [0, 1]

Morph mode implementation:
  for t in [0.2, 0.4, 0.6, 0.8, 1.0]:
    v(t) = norm(LERP(norm(A), norm(B), t))
    → Qdrant nearest-neighbor search
```

### 2.5 Weighted linear combination (Blend)

```
v_blend = norm( Σᵢ wᵢ · norm(aᵢ) )

wᵢ ∈ [−1.0, +1.0]

  wᵢ > 0: pull toward this direction
  wᵢ < 0: push away (pseudo-subtraction)
  wᵢ ≈ 0: negligible influence
```

### 2.6 Vector sign inversion (Outlier — Antipode)

```
Centroid computation:
  v_centroid = norm( Σᵢ norm(xᵢ) / N )   ← mean over all images

Antipode:
  v_antipode = −v_centroid     ← multiply every element by −1

Meaning: the direction in high-dimensional space most opposite to
         the "typical" image in the collection.
```

---

## 3. Technology at a Glance

### Mode × Technology Matrix

| Mode | Vector avg | Iterative norm | LERP | Weighted sum | Sign inversion | Qdrant std | DiscoverQuery | GroupBy | LLM tags | VLM 3-stage | WD14 tags | UMAP density | SSE |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| ✨ Serendipity | ● | — | — | — | — | ● | — | — | — | — | — | — | — |
| ⚗️ Alchemy | — | ● | — | — | — | ● | — | — | — | — | — | — | — |
| 🌊 Morph | — | ● | ● | — | — | ● | — | — | — | — | — | — | — |
| ⚡ Anomaly | ● | — | — | — | — | ● | — | — | ● | — | ● | — | — |
| 🪞 Inversion | ● | — | — | — | — | ● | — | — | — | ● | ● | — | ● |
| 🧭 Discovery | — | — | — | — | — | — | ● | — | — | — | — | — | — |
| 🗂️ Group Search | — | — | — | — | — | — | — | ● | — | — | — | — | — |
| ⚖️ Blend | — | — | — | ● | — | ● | — | — | — | — | — | — | — |
| 🌌 Outlier (Antipode) | ● | — | — | — | ● | ● | — | — | — | — | — | — | — |
| 🌌 Outlier (Isolated) | — | — | — | — | — | ◐ | — | — | — | — | — | ● | — |
| 💡 Brainstorm | — | — | — | — | — | — | — | — | ● | — | ● | — | ● |

● = core technology · ◐ = conditional · — = not used

### Processing cost classification

```
Pure vector math (microsecond–millisecond, deterministic)
  Alchemy / Morph / Blend

Vector math + Qdrant advanced features
  Serendipity (percentile sampling)
  Outlier (centroid or density scan)
  Discovery (DiscoverQuery)
  Group Search (GroupBy)

LLM/VLM integration (seconds–10s, non-deterministic)
  Anomaly    — 1 LLM call (temperature 0.7)
  Brainstorm — 1 LLM call, SSE streaming
  Inversion  — 2–3 VLM calls, SSE streaming  ← heaviest
```

---

## 4. Mode Algorithms — Full Specification

---

### Serendipity ✨

**Pseudocode:**

```python
def serendipity(ref_sha256s: list[str], k: int = 20) -> list[Image]:
    # 1. Build query vector
    vecs = [get_embedding(sha) for sha in ref_sha256s]
    q = norm(mean(vecs))

    # 2. Retrieve top 1,000 nearest neighbors (MRL two-phase)
    results = qdrant.search(
        collection="images",
        query=q,
        limit=1000,
        with_payload=False,
        using="full",
        prefetch=Prefetch(query=q[:256], limit=5000, using="mrl256")
    )
    scores = [r.score for r in results]

    # 3. Compute percentile boundaries
    p25 = percentile(scores, 25)
    p75 = percentile(scores, 75)

    # 4. Filter to the P25–P75 band
    band = [r for r in results if p25 <= r.score <= p75]

    # 5. Random sample from the band
    return random.sample(band, min(k, len(band)))
```

**Why percentile bands instead of fixed thresholds:**

A fixed threshold (e.g., 0.5–0.7) fails in practice because different embedding models produce very different score distributions. Percentile bands **automatically anchor to the real distribution of your collection** and consistently deliver the "moderately similar" zone regardless of absolute score magnitudes.

---

### Alchemy ⚗️

**Pseudocode:**

```python
def alchemy(
    add_sha256s: list[str],   # up to 3
    sub_sha256s: list[str],   # up to 3
    k: int = 20
) -> list[Image]:
    # 1. Iteratively normalize-add the positive images
    q = norm(get_embedding(add_sha256s[0]))
    for sha in add_sha256s[1:]:
        q = norm(q + norm(get_embedding(sha)))   # re-normalize after each add

    # 2. Iteratively normalize-add the negative images, then subtract
    if sub_sha256s:
        s = norm(get_embedding(sub_sha256s[0]))
        for sha in sub_sha256s[1:]:
            s = norm(s + norm(get_embedding(sha)))
        q = norm(q - s)                           # re-normalize after subtract

    # 3. Qdrant search
    return qdrant.search(collection="images", query=q, limit=k)
```

**Numerical effect of iterative normalization:**

```
Image A magnitude = 1.0 (already normalized)
Image B magnitude = 1.0 (already normalized)

Naive addition:         A + B → ‖A + B‖ ≈ 1.41  (magnitude drift)
Iterative normalize:    norm(norm(A) + norm(B)) = 1.0  (magnitude fixed)
```

---

### Morph 🌊

**Pseudocode:**

```python
MORPH_STEPS = [0.2, 0.4, 0.6, 0.8, 1.0]
IMAGES_PER_STEP = 4

def morph(sha_a: str, sha_b: str) -> list[list[Image]]:
    va = norm(get_embedding(sha_a))
    vb = norm(get_embedding(sha_b))
    results = []

    for t in MORPH_STEPS:
        # LERP → normalize
        v_interp = norm((1 - t) * va + t * vb)

        # Exclude the two endpoint images from results
        step_results = qdrant.search(
            collection="images",
            query=v_interp,
            limit=IMAGES_PER_STEP,
            filter=must_not([sha_a, sha_b])
        )
        results.append(step_results)

    return results   # 5 steps × 4 images = up to 20 images
```

**Geometric interpretation of LERP:**

```
va and vb are two points on the unit sphere.
  t = 0.0 → direction of va (images near A)
  t = 0.5 → midpoint direction (the "blend" of A and B)
  t = 1.0 → direction of vb (images near B)

Note: this is linear interpolation, not SLERP (spherical linear).
LERP is used because the post-normalization difference is negligible
and computation is simpler.
```

---

### Anomaly ⚡

**Pseudocode:**

```python
ANOMALY_TAG_COUNT = 30    # top tags fed to LLM
ANOMALY_INJECT_N  = 3     # tags LLM proposes

def anomaly(ref_sha256s: list[str], k: int = 20) -> AnomalyResult:
    # 1. Count WD14 tag frequencies
    tag_freq: Counter = Counter()
    for sha in ref_sha256s:
        doc = db.get(sha)
        tag_freq.update(doc["wd14_tags"])

    # 2. Extract top tags
    top_tags = [t for t, _ in tag_freq.most_common(ANOMALY_TAG_COUNT)]

    # 3. Ask LLM for rare-but-plausible anomaly tags (temperature 0.7)
    prompt = (
        f"Given these dominant tags from an image collection:\n{top_tags}\n\n"
        f"Suggest exactly {ANOMALY_INJECT_N} Danbooru-compatible tags "
        "that would be RARE or UNEXPECTED in this context "
        "but semantically plausible. Return only tags, comma-separated."
    )
    anomaly_tags_str = ollama.generate(prompt, temperature=0.7)
    anomaly_tags = parse_tags(anomaly_tags_str)

    # 4. Concatenate and embed as a text query
    combined_text = ", ".join(top_tags + anomaly_tags)
    q = norm(ollama.embed(combined_text))

    # 5. Qdrant search
    images = qdrant.search(collection="images", query=q, limit=k)

    return AnomalyResult(images=images, anomaly_tags=anomaly_tags)
```

**Non-determinism is intentional:**

`temperature=0.7` means different anomaly tags are proposed on every call for the same reference images. Running Anomaly multiple times produces a different "surprise" each time — by design.

---

### Inversion 🪞

See [§7 VLM 3-Stage Pipeline](#7-vlm-3-stage-pipeline-inversion-deep-dive) for the full pipeline.

**Algorithm outline:**

```python
def inversion(ref_sha256s: list[str]) -> AsyncIterator[SSEEvent]:
    # Build tile image (visual context for VLM)
    tile = create_tile_image([read_bytes(sha) for sha in ref_sha256s])

    # Stage 1: Generation (SSE "think" + "token" events)
    stage1_output = yield from vlm_stage1(tile)

    # Stage 2: Validation
    issues = yield from vlm_stage2(stage1_output)

    # Stage 3: Refinement (only if issues found)
    if issues:
        final = yield from vlm_stage3(stage1_output, issues)
    else:
        final = stage1_output

    # Embed final output → Qdrant search → SSE "done"
    q = norm(ollama.embed(final.tags + final.prose))
    images = qdrant.search(collection="images", query=q, limit=20)
    yield SSEEvent("done", images=images,
                   inversion_tags=final.tags,
                   inversion_prose=final.prose,
                   negative_tags=final.negative_tags)
```

---

### Discovery 🧭

**Pseudocode:**

```python
def discovery(
    target_sha: str,
    positive_pairs: list[tuple[str, str]],   # [(pos_sha, neg_sha), ...]
    k: int = 20
) -> list[Image]:
    target_vec = norm(get_embedding(target_sha))

    context = [
        ContextPair(
            positive=norm(get_embedding(pos)),
            negative=norm(get_embedding(neg))
        )
        for pos, neg in positive_pairs
    ]

    return qdrant.discover(
        collection="images",
        target=target_vec,
        context=context,
        limit=k,
        using="full",
        prefetch=Prefetch(
            query=target_vec[:256],
            limit=k * 5,
            using="mrl256"
        )
    )
```

**DiscoverQuery optimization objective:**

```
Standard search:   argmax_x  sim(x, target)

DiscoverQuery:     argmax_x  sim(x, target)
                   subject to: sim(x, pos) > sim(x, neg)  ∀ pairs

Each positive/negative pair applies directional pressure.
Adding more pairs narrows the exploration further.
```

---

### Group Search 🗂️

**Pseudocode:**

```python
def group_search(
    query_text: str,
    group_by: str = "model_name",
    group_size: int = 3,
    max_groups: int = 10
) -> dict[str, list[Image]]:
    q = norm(ollama.embed(query_text))

    return qdrant.search_groups(
        collection="images",
        query=q,
        group_by=group_by,
        group_size=group_size,
        limit=max_groups,
        using="full",
        prefetch=Prefetch(
            query=q[:256],
            limit=max_groups * group_size * 5,
            using="mrl256"
        )
    )
```

**GroupBy internals:**

After the ANN search, Qdrant partitions results by the specified payload field and returns up to `group_size` images per group. Intra-group deduplication is applied; cross-group diversity follows naturally from the search ranking.

---

### Blend ⚖️

**Pseudocode:**

```python
BLEND_EXCLUDE_THRESHOLD = 0.5

def blend(
    images_weights: list[tuple[str, float]],   # (sha256, weight)
    k: int = 20
) -> list[Image]:
    # Exclude images that are "strongly included" (return related, not themselves)
    exclude = [sha for sha, w in images_weights if w > BLEND_EXCLUDE_THRESHOLD]

    # Weighted linear combination
    q = zeros(768)
    for sha, w in images_weights:
        q += w * norm(get_embedding(sha))
    q = norm(q)

    return qdrant.search(
        collection="images",
        query=q,
        limit=k,
        filter=must_not(exclude)
    )
```

**Mathematical distinction from Alchemy:**

```
Alchemy (iterative normalization):
  q₀ = norm(a₁)
  q₁ = norm(q₀ + norm(a₂))
  q₂ = norm(q₁ − norm(s₁))
  Every input snapped to the unit sphere at each step
  → all inputs have equal voting power regardless of count

Blend (weighted linear combination):
  q = norm( w₁·norm(a₁) + w₂·norm(a₂) + w₃·norm(a₃) )
  Weights flow through to the final direction
  → "60% A, 30% B, 10% C" maps directly onto the result
```

---

### Outlier 🌌

#### Antipode (mathematical opposite)

**Pseudocode:**

```python
def outlier_antipode(k: int = 20) -> list[Image]:
    # Compute centroid of all embeddings
    all_vecs = [get_embedding(sha) for sha in get_all_sha256s()]
    centroid = norm(mean(all_vecs))

    # Negate → points to the mathematical opposite direction
    antipode = -centroid

    return qdrant.search(collection="images", query=antipode, limit=k)
```

**Why sign inversion gives the "most different" image:**

```
The centroid c (unit sphere) is the direction of the "typical" image.
−c points in exactly the opposite direction in the same high-dim space.
Images nearest to −c are those furthest from the collection's center of gravity.

Note: −c is a virtual point not in the collection.
      The ANN search finds the nearest real image to that virtual point.
```

#### Isolated (density-based)

**Pseudocode:**

```python
ISOLATION_RADIUS  = 2.0   # neighbor radius in UMAP 2D space
ISOLATION_SAMPLE  = 20    # final sample count

def outlier_isolated(k: int = ISOLATION_SAMPLE) -> list[Image]:
    docs = db.get_all_with_umap()

    if not docs:
        # Fallback: UMAP not yet computed
        return random.sample(get_all_images(), k)

    coords = [(doc["umap_x"], doc["umap_y"]) for doc in docs]

    # Compute local density for each image
    densities = []
    for i, (x, y) in enumerate(coords):
        count = sum(
            1 for j, (x2, y2) in enumerate(coords)
            if i != j and euclidean(x, y, x2, y2) < ISOLATION_RADIUS
        )
        densities.append((docs[i], count))

    # Sort ascending by density (fewest neighbors first)
    densities.sort(key=lambda d: d[1])

    # Random sample from the lowest-density candidates
    candidates = [d[0] for d in densities[:k * 3]]
    return random.sample(candidates, min(k, len(candidates)))
```

**When UMAP coordinates are updated:**

UMAP projects all image embeddings to 2D. It is recomputed asynchronously in a batch job when new images are added. Images added after the last UMAP run lack `umap_x`/`umap_y` and fall through to the random-sample fallback path.

---

## 5. Brainstorm — LLM Tag Pipeline

**Pseudocode:**

```python
def brainstorm(
    selected_sha256s: list[str],
    extra_tags: list[str] = [],         # from Anomaly / Inversion
    lang: Literal["ja", "en"] = "en",
    idea_count: int = 5
) -> AsyncIterator[SSEEvent]:
    # 1. Collect WD14 tags from selected images
    all_tags: set[str] = set()
    for sha in selected_sha256s:
        doc = db.get(sha)
        all_tags.update(doc["wd14_tags"][:50])   # top 50 tags

    # 2. Merge Anomaly / Inversion tags (if present)
    all_tags.update(extra_tags)

    # 3. Build LLM prompt
    lang_directive = "in Japanese" if lang == "ja" else "in English"
    prompt = (
        f"Given these image tags: {', '.join(sorted(all_tags))}\n\n"
        f"Generate {idea_count} creative, visually distinct scene proposals "
        f"{lang_directive}. Each proposal should:\n"
        "- Be 2-3 sentences describing a complete scene\n"
        "- Be visually specific (lighting, mood, composition)\n"
        "- Be distinct from the others\n"
        "Format as a Markdown numbered list."
    )

    # 4. SSE streaming generation
    async for token in ollama.generate_stream(prompt, temperature=0.8):
        yield SSEEvent("token", text=token)

    yield SSEEvent("done")
```

**Tag priority in the merged vocabulary:**

```
WD14 tags (selected images)
  + anomaly tags from LLM           ← preserves exploration context
  + inversion tags from VLM         ← preserves the inverted-world concept
  = brainstorm vocabulary → fed to LLM for scene proposals
```

**SSE event types (Brainstorm):**

| type | When | Fields |
|---|---|---|
| `token` | Incremental text token | `text: str` |
| `done` | Generation complete | — |
| `error` | Error | `message: str` |

---

## 6. Qdrant Query Patterns

### 6.1 Standard vector search (MRL two-phase)

```python
# Base pattern for Serendipity, Alchemy, Morph, Blend, etc.
qdrant.query_points(
    collection_name="images",
    prefetch=[
        Prefetch(
            query=q_mrl256,          # 256-dim prefix
            using="mrl256",
            limit=prefetch_limit,    # k × 5–10
        )
    ],
    query=q_full,                    # 768-dim full vector
    using="full",
    limit=k,
    query_filter=QdrantFilter(...),
    with_payload=True,
)
```

### 6.2 DiscoverQuery (Discovery mode)

```python
qdrant.discover_points(
    collection_name="images",
    target=target_vec,
    context=[
        ContextExamplePair(
            positive=pos_vec,
            negative=neg_vec,
        )
        for pos_vec, neg_vec in context_pairs
    ],
    limit=k,
    using="full",
    prefetch=Prefetch(
        query=target_vec[:256],
        limit=k * 5,
        using="mrl256",
    ),
)
```

### 6.3 GroupBy (Group Search mode)

```python
qdrant.query_points_groups(
    collection_name="images",
    prefetch=[
        Prefetch(
            query=q_mrl256,
            using="mrl256",
            limit=prefetch_limit,
        )
    ],
    query=q_full,
    using="full",
    group_by="model_name",           # payload field to group by
    limit=max_groups,
    group_size=images_per_group,
)
```

---

## 7. VLM 3-Stage Pipeline (Inversion Deep-Dive)

### Stage 1 — Generation prompt (key directives)

```
System instruction (summary):
  "You are a creative AI specializing in semantic image inversion.
   Invert the world along five axes (Visual/Mood/Subject/Style/Narrative).
   DO NOT invert character-defining features (hair, eyes, specific persons).
   The world changes; character identity remains constant."

Output schema (JSON):
  {
    "narrative":       str,      // 300–500 words describing the inverted world
    "inversion_tags":  str[],    // 100–150 Danbooru/WD14 tags
    "inversion_prose": str,      // 150–200 word natural-language prompt
    "negative_tags":   str[]     // 20–40 tags to exclude (elements of original)
  }
```

### Stage 2 — Validation prompt (key directives)

```
Input: full Stage 1 output

Checks performed:
  - Internal contradictions (e.g., inverted to "indoor" but sky/tree tags remain)
  - Whether all five axes were actually inverted
  - Prohibited elements (character-defining features) not present
  - Consistency between tags and prose

Output:
  {
    "issues":   str[],                         // empty list = no issues
    "severity": "none" | "minor" | "major"
  }
```

### Stage 3 — Refinement prompt (key directives)

```
Condition: only runs if issues is non-empty

Input: Stage 1 output + Stage 2 issues list

Directive: "Revise the output to address each issue while preserving
            the inverted world concept. Maintain all non-character inversions."

Output: same JSON schema as Stage 1 (corrected)
```

### SSE event types (Inversion)

| type | When | Fields |
|---|---|---|
| `think` | VLM extended thinking (compatible models) | `text: str` |
| `stage` | Stage transition | `stage: 1\|2\|3`, `label: str` |
| `token` | Generated text token | `text: str` |
| `done` | All stages complete | `images`, `inversion_tags`, `inversion_prose`, `negative_tags`, `inversion_negative_tags` |
| `error` | Error | `message: str` |

### Full pipeline diagram

```
Reference images (1–3)
    │
    ├─── Tile image composition (tile_image.py)
    │        Multiple images → single mood board
    │
    └─── Mean embedding vector (context reference)

    ↓

Stage 1: Generation (VLM call)
    VLM ← tile image + inversion instruction prompt
    Output: narrative + tags(100–150) + prose(150–200) + neg_tags
    SSE: think + token events streamed in real time

    ↓

Stage 2: Validation (VLM call)
    VLM ← Stage 1 output
    Output: issues[]
    SSE: stage event ("Validating...")

    ↓ if issues empty → skip Stage 3

Stage 3: Refinement (VLM call)
    VLM ← Stage 1 output + issues
    Output: corrected tags + prose
    SSE: token events

    ↓

Text embedding (Ollama)
    tags + prose → 768-dim vector

    ↓

Qdrant search (MRL two-phase)
    → top k matching images

    ↓

SSE: done event (images + full prompt payload)
```

**Why three stages?**

Without validation, VLM outputs sometimes contain internal contradictions — inverting "outdoor" to "indoor" conceptually while leaving `tree`, `sky`, `outdoor` tags in the list. The self-correction loop eliminates these inconsistencies without human review, raising output coherence significantly for the most compute-expensive mode.

---

## 8. The Complete Creative Pipeline

```
Discovery (9 Inspire modes)
  ├── Pure vector math: Alchemy / Morph / Blend
  │     └── sub-millisecond, deterministic
  ├── Vector + sampling: Serendipity / Outlier
  │     └── milliseconds, stochastic
  ├── Qdrant advanced: Discovery / Group Search
  │     └── milliseconds
  └── LLM/VLM integration: Anomaly / Inversion
        └── seconds–10s, non-deterministic

  ↓ discovered images + tag payload

Analysis (automatic)
  WD14 tag collection
  Anomaly / Inversion tag merging
  Vocabulary set construction

  ↓

Ideation (Brainstorm)
  LLM generates 3–5 scene proposals from vocabulary set
  SSE streaming; English and Japanese supported

  ↓

Refinement (Prompt Alchemy)
  Reference images (up to 6) + brainstorm idea text
  → WD14 score classification (≥0.70 must / <0.70 reference)
  → Tile composition → VLM generation → post-processing pipeline
  Style: natural / danbooru / detailed

  ↓

Generation (ComfyUI)
  auto_submit or one-click submit
  Generated image → added to collection by sha256
  WD14 tagging + embedding → indexed into Qdrant

  ↓

Back to next Discovery cycle (collection grows richer)
```

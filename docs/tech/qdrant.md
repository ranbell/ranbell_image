# Qdrant Usage in Ranbell Image

## Design Philosophy: Why Qdrant Holds Everything

Most vector search deployments pair Qdrant with a relational database: Qdrant stores embeddings, the relational DB stores everything else. This project does not. Qdrant is the only persistence layer — no SQL database, no Redis, no JSON config files. Here is why that choice is correct for this domain.

### Immutable source, recomputable projections

This architecture follows the same principle as **event-sourced systems** and the **Kappa Architecture**: the image files on disk are the immutable source of record; the Qdrant payload is a recomputable derived view. In manufacturing terms, raw materials are permanent and precious — finished goods are expendable and re-manufacturable. Here, images are the raw material; WD14 tags, semantic embeddings, color palettes, VLM evaluations, UMAP coordinates, and alignment scores are the finished goods.

"Why is the image file alone sufficient?" — AI-generated images embed their full generation parameters in the PNG metadata alongside the pixel data:

- **Positive / negative prompts** — the text instructions used for generation
- **Model** — checkpoint name, LoRA, etc.
- **Seed** — the reproducible random seed
- **CFG scale** — prompt adherence strength
- **Other generation parameters** — sampler, step count, etc.

Because all of this is embedded in the file itself, wiping Qdrant entirely and rebuilding from the image files recovers identical information.

Because every piece of information stored in Qdrant is derived from images by running an AI model or a deterministic algorithm, Qdrant is operationally a sophisticated, queryable, pipeline-driving, status-tracking, config-persisting cache. If it were wiped tomorrow, a single rebuild sequence would restore it entirely.

This reframing eliminates an entire class of bugs: the sync bugs that appear when a relational DB and a vector store accumulate divergent state.

### AI model churn makes rebuilds routine, not catastrophic

Embedding models and taggers turn over roughly every six months before a better one appears. When that happens, the correct response is to rebuild the index with the new model — not to preserve vectors computed by an outdated one. Because every write path is idempotent and all state lives in Qdrant, a full rebuild is:

1. Call `reset_scope()` to flip all `embedding_status` fields back to `"pending"`.
2. Restart the pipeline.

No ETL, no schema migration, no external job orchestration. The same idempotent startup sequence that handles a fresh install handles a full index rebuild.

### Stateless pipeline workers with Qdrant as the only state

The standard AI pipeline pattern is: relational DB tracks job state → Redis/Celery manages the queue → vector store holds results. That is three services, three failure domains, and a synchronization contract between them.

Here, `embedding_status` (KEYWORD-indexed) on each image point *is* the queue. Pipeline workers are stateless: they scroll Qdrant for `embedding_status = "pending"`, process each image, and write `embedding_status = "done"` back. No in-memory job state is held; a crashed worker leaves no orphaned state — the next worker picks up from `"pending"` naturally. The `alignment` collection uses an identical `status` lifecycle. Runtime configuration lives as a single fixed point in the `app_config` collection, not in JSON files or environment sidecars.

One service, one failure domain, zero sync contracts.

### Qdrant's filter API covers the relational access patterns

The objection to this design is: "Qdrant is a vector search engine, not a relational database — you will miss JOINs and transactions."

In practice, every access pattern here is one of:

- **Exact match on indexed payload field** — `model_name`, `batch_category`, `star_rating`, `embedding_status`, `wd14_tags`
- **Full-text search** — `positive_prompt` (TEXT/WORD-indexed)
- **Numeric or datetime range** — `palette_hues`, `avg_saturation`, `score`
- **Combined filter + vector search** — the dominant pattern; a relational DB would need to pipe IDs back to Qdrant anyway

Payload indexes handle all of these. The filter, the vector search, and the results land in one round-trip rather than two.

### The rebuild guarantee as fail-safe design

If the Qdrant database ever becomes corrupt, inconsistent, or simply outdated, the recovery path is total deletion and a fresh rebuild from the image files. Every write path is idempotent; the startup sequence creates missing collections and runs migrations automatically. The system goes from zero to fully operational with no data backup required.

This is only possible because the true source of record — the images on disk — never lives inside Qdrant.

---

This document describes every Qdrant feature used in the ranbell_image project, covering collections, named vectors, payload schema, search patterns, job-status management, and advanced capabilities.

- **Qdrant version:** 1.18.0
- **Client:** `AsyncQdrantClient` from the `qdrant-client` Python SDK
- **Primary implementation:** `backend/app/db/qdrant_client.py`
- **Configuration:** `backend/app/config.py`

---

## Collections

Three collections are maintained:

| Collection | Purpose |
|---|---|
| `images` | Primary store: all image points with embeddings, color vectors, and rich payload |
| `alignment` | VLM prompt-alignment evaluation records (one point per evaluated image) |
| `app_config` | Single-document store for persistent runtime configuration |

All collections are created idempotently on startup; missing collections are created automatically.

---

## Named Vectors

The `images` collection uses **three named vectors** per point. Each vector addresses a distinct search dimension.

### `embedding` — Semantic Embedding (768-dim, COSINE)

The primary semantic representation of each image, generated by `embeddinggemma:300m` via Ollama. This is the full-dimension vector used for final-stage reranking in all semantic searches.

- **Dimensions:** 768 (configurable via `embed_dim`)
- **Distance:** COSINE
- **Storage:** On-disk
- **Quantization:** INT8 scalar quantization (`quantile=0.99`, `always_ram=True`)

### `embedding_small` — MRL Prefetch Vector (256-dim, COSINE)

A Matryoshka Representation Learning (MRL) truncated prefix of `embedding`. Because MRL training ensures that the first _k_ dimensions of a longer embedding are itself a meaningful embedding at that shorter dimension, this vector can stand in for the full embedding at reduced cost.

- **Dimensions:** 256 (configurable via `embed_dim_small`; must be ≤ `embed_dim`)
- **Distance:** COSINE
- **Storage:** On-disk
- **Quantization:** INT8 scalar quantization
- **Role in two-phase search:** Acts as the fast prefetch phase, generating `n_results × 20` candidates that are then reranked with the full `embedding` vector.

```python
# MRL two-phase search pattern
query_points(
    prefetch=[Prefetch(query=small_vec, using="embedding_small", limit=n * 20)],
    query=full_vec,
    using="embedding",
)
```

This pattern is used in: `search_vector()`, `search_vector_tag_or()`, `search_similar()`, `search_images_grouped()`.

### `color_vector` — Perceptual Color Vector (3-dim, EUCLID)

A three-dimensional CIE L\*a\*b\* color vector `[L*, a*, b*]` representing the dominant color of the image's palette. Euclidean distance in L\*a\*b\* space equals the CIE76 ΔE perceptual color difference, making this a perceptually accurate color-similarity metric.

- **Dimensions:** 3 (fixed)
- **Distance:** EUCLID
- **Storage:** On-disk
- **Usage:** Color-based image search with optional hue-range exclusion; color proximity filtering

---

## JSON Payload

### `images` Collection

Each image point carries a rich payload covering file identity, AI processing state, color data, spatial coordinates, and user annotations.

#### Identity & File Metadata

| Field | Index Type | Description |
|---|---|---|
| `sha256` | KEYWORD | Unique image identifier (SHA-256 hash of file contents) |
| `name` | KEYWORD | Image filename |
| `path` | — | Full filesystem path |
| `mtime` | DATETIME | File modification timestamp |
| `size` | INTEGER | File size in bytes |
| `raw_metadata` | — | Full EXIF / PNG metadata dict |

#### AI Processing State

| Field | Index Type | Description |
|---|---|---|
| `embedding_status` | KEYWORD | `"pending"` \| `"done"` — drives the embedding pipeline queue |
| `wd14_tags` | KEYWORD | List of Danbooru tags predicted by the WD14 tagger |
| `positive_prompt` | TEXT (WORD) | Text-to-image generation prompt; full-text searchable |
| `params` | — | Full generation parameters dict (sampler, steps, CFG, seed, …) |
| `model_name` | KEYWORD | Model name extracted from `params` |
| `batch_category` | KEYWORD | `"AI"` (AI-generated) \| `"NR"` (natural/reference) |
| `is_reference` | BOOL | `true` for images from the reference source directory |

#### Color Palette

Extracted by 5-cluster KMeans in HSV / L\*a\*b\* space.

| Field | Index Type | Description |
|---|---|---|
| `palette_hex` | KEYWORD | List of `#RRGGBB` hex color strings (one per cluster) |
| `palette_hues` | FLOAT | HSV hue degrees (0–360) per cluster |
| `avg_saturation` | FLOAT | Mean HSV saturation across all clusters (0–1) |
| `avg_value` | FLOAT | Mean HSV value/brightness across all clusters (0–1) |

#### Spatial Projection

| Field | Index Type | Description |
|---|---|---|
| `umap_x` | — | X coordinate of 2D UMAP projection |
| `umap_y` | — | Y coordinate of 2D UMAP projection |

Not indexed (range-based filtering is used to detect presence).

#### User Data

| Field | Index Type | Description |
|---|---|---|
| `star_rating` | INTEGER | User star rating (0–5) |
| `creation_record` | — | Dict with creation method, source image refs, inspire context, generation params |
| `creation_record.method` | KEYWORD | Creation method identifier (indexed for filtering) |

### `alignment` Collection

One point per evaluated image; point ID is deterministic (based on `image_id`).

| Field | Index Type | Description |
|---|---|---|
| `image_id` | KEYWORD | `sha256` of the corresponding image |
| `status` | KEYWORD | `"pending"` \| `"done"` \| `"skipped"` \| `"error"` |
| `score` | FLOAT | Alignment score 0.0–1.0 |
| `evaluated_at` | DATETIME | Evaluation timestamp |
| `summary` | — | Plain-text analysis summary |
| `summary_i18n` | — | Dict of translated summaries keyed by language code |
| `matched_elements` | — | List of prompt elements found in the image |
| `matched_elements_i18n` | — | Translations of matched elements |
| `unmatched_elements` | — | List of prompt elements absent from the image |
| `unmatched_elements_i18n` | — | Translations of unmatched elements |
| `categories` | — | Category classifications |

### `app_config` Collection

A single point (ID `"config"`) that stores runtime state persisted across application restarts:

- Pause flags per job lane
- Active embedding model name
- Active VLM model name
- UMAP computation state

---

## Search Operations

All search functions are implemented in `backend/app/db/qdrant_client.py` using `query_points()` or `query_batch_points()`.

### Semantic Search Variants

| Method | Description |
|---|---|
| `search_vector()` | Semantic search with optional single-tag filter; MRL two-phase |
| `search_vector_tag_or()` | Semantic search with OR-include / NOT-exclude tag filter; MRL two-phase |
| `search_similar()` | Find images similar to a given reference; excludes the source point itself via `HasIdCondition` |
| `search_by_vector()` | Raw vector search accepting an explicit sha256 exclusion list |
| `search_by_vector_scored()` | Same as above but returns `(payload, score)` tuples for downstream score-range filtering |

### Color Search

`search_by_color_vector()` queries the `color_vector` using Euclidean distance in L\*a\*b\* space. It supports hue-range exclusion (e.g., "find colors near this blue, but not greens") by filtering on the `palette_hues` FLOAT-indexed field.

### Discovery API

`discover_images()` uses Qdrant's Discovery API (`DiscoverQuery` via `query_batch_points()`). The caller provides a target image and a list of context pairs (positive/negative image IDs). The query finds images that are geometrically close to the target while respecting the directional contrast expressed by the context pairs — useful for guided creative exploration.

### GroupBy Search

`search_images_grouped()` uses `query_points_groups()` to group results by a payload field (e.g., `model_name`). Each group contains `group_size` representative hits. MRL two-phase prefetch is applied before grouping.

### Filter Types Used

| Filter | Usage |
|---|---|
| `FieldCondition(match=MatchValue)` | Exact single-value match |
| `FieldCondition(match=MatchAny)` | OR match against a list |
| `FieldCondition(match=MatchText)` | Full-text search against TEXT-indexed fields |
| `FieldCondition(range=Range)` | Numeric / datetime range; also used to detect field presence |
| `HasIdCondition` | Include or exclude specific point IDs |
| `IsEmptyCondition` | Check whether a payload field is absent or null |
| `FilterSelector` | Target bulk operations (delete vectors, delete payload) to a filtered subset |
| `Filter(must=[...])` | AND composition |
| `Filter(must_not=[...])` | NOT composition |
| `Filter(must=[...], must_not=[...])` | AND + NOT combination |

---

## Upsert & Delete Operations

### Write Operations

| Method | Qdrant Operation | Description |
|---|---|---|
| `upsert_new()` | `upsert()` | Insert placeholder point (no vectors) during file scanning |
| `set_payload()` | `set_payload()` | Partial payload update; existing vectors untouched |
| `set_embedding()` | `update_vectors()` | Store `embedding` + `embedding_small` (truncated from full vector) |
| `set_color_vector()` | `update_vectors()` | Store single `color_vector` |
| `set_color_vectors_batch()` | `update_vectors()` | Bulk color vector update for multiple points |
| `upsert_alignment()` | `upsert()` | Store or overwrite an alignment evaluation record |

### Delete Operations

| Method | Qdrant Operation | Description |
|---|---|---|
| `delete_embedding()` | `delete_vectors()` | Remove `embedding` + `embedding_small`; payload preserved (point stays) |
| `delete()` | `delete()` with `PointIdsList` | Remove entire point |
| `delete_payload_keys()` | `delete_payload()` | Remove specific payload fields from a single point |
| `delete_payload_keys_batch()` | `delete_payload()` with `PointIdsList` | Remove specific payload fields from multiple points |
| `reset_scope()` | `delete_vectors()` with `FilterSelector` | Bulk vector reset filtered by `embedding_status` value (`all` / `done` / `pending`) |
| `delete_all_images()` | `delete()` with empty `Filter` | Wipe the entire `images` collection |

---

## Scroll & Pagination

Scroll operations drive the application's read path — from pagination to analytics to pipeline inputs. Batch size is typically 1,000 points per scroll call.

### Image Listing & Pagination

`scroll_images()` implements cursor-based pagination. The cursor is a base64-encoded `{start, last_id}` struct. Each call fetches `limit + 2` results to detect page boundaries. Sort direction is controlled by `OrderBy`.

`scroll_all()` collects a full filtered dataset (e.g., for bulk download), applying tag, keyword, model, rating, category, and ID-list filters.

### Pipeline Inputs

| Method | Purpose |
|---|---|
| `get_all_embeddings()` | Collect all `embedding` vectors for UMAP dimensionality reduction |
| `scroll_color_lab_points()` | Collect images that need color vector backfill |
| `scroll_tags()` | Collect `positive_prompt` + `wd14_tags` for tag counting |
| `find_path_mtime_index()` | Collect `sha256 / path / mtime` triples for incremental heal scan |
| `find_duplicate_path_sha256s()` | Detect multiple Qdrant records for the same file path |

### Visualization & Analysis

| Method | Purpose |
|---|---|
| `scroll_umap_points()` | Fetch `umap_x / umap_y / palette_hex / name` for the 2D gallery scatter plot |
| `scroll_umap_points_with_tags()` | Same coordinates plus `wd14_tags` for cluster labeling |
| `scroll_model_facets()` | Collect `params` payload for model-name faceting |
| `list_dirs()` | Group `path` values by parent directory for directory-browser UI |

### Alignment Reads

| Method | Purpose |
|---|---|
| `scroll_alignment_sha256s()` | Return the set of all evaluated `image_id` values |
| `get_alignment_sorted_sha256s()` | Return sha256 list ordered by `score` DESC |
| `get_aligned_sha256s()` | Return sha256 list filtered by minimum score threshold |

### Count Operations

`count_with_embedding()`, `count_with_color_vector()`, `count_with_color_lab()`, `count_pending_color_extraction()` — all use `count()` with `Filter` to track pipeline progress.

---

## Job & Status Management via Qdrant

Qdrant serves as the **status persistence layer** for the AI processing pipeline. There is no separate job-queue database; the pipeline consults and mutates Qdrant payload fields to determine what work remains.

### Embedding Pipeline

The `embedding_status` field (KEYWORD-indexed) on every image point drives the embedding pipeline:

1. The scanner inserts new points with `embedding_status: "pending"`.
2. The pipeline processes pending images and sets `embedding_status: "done"` after storing the vectors.
3. `reset_scope()` can revert `"done"` points back to `"pending"` (or clear all) to force reprocessing.

### Alignment Pipeline

The `alignment` collection uses a `status` field with the lifecycle:

```
"pending" → "done" | "skipped" | "error"
```

Images needing evaluation are identified by querying for `status == "pending"`. The evaluator writes the final score and analysis back to the same point.

### Persistent Application State

The `app_config` collection holds a single configuration point that survives container restarts. It stores lane pause flags, the active embedding model, the active VLM model, and UMAP computation state. Any runtime change to these settings is immediately persisted to this collection.

---

## Advanced Features

### INT8 Scalar Quantization

Both `embedding` and `embedding_small` are quantized at startup using INT8 scalar quantization:

```python
ScalarQuantizationConfig(type=ScalarType.INT8, quantile=0.99, always_ram=True)
```

`always_ram=True` keeps the quantized index in memory for low-latency search while the full float32 vectors remain on disk. The `quantile=0.99` clips the top 1% of values to avoid outlier distortion. This operation is idempotent and can be reapplied safely.

### Atomic Collection Migration

When the collection schema must change (e.g., adding `embedding_small` with a different dimension, or adding `color_vector`), the project uses an atomic recreation pattern to avoid data loss:

1. Create a temporary collection with the new schema.
2. Scroll all existing points in 200-point batches, carrying payloads and all existing vectors.
3. Upsert each batch into the temporary collection.
4. Delete the original collection.
5. Rename the temporary collection to the original name.
6. Reapply all payload indexes.

Two migrations are implemented: `_migrate_small_dim()` (triggered when `embed_dim_small` config changes) and `_migrate_color_vector()` (triggered when the `color_vector` named vector is absent from an existing collection).

### Startup Sequence

`QdrantDBClient.start()` runs the following sequence on every application startup:

1. Poll Qdrant with exponential backoff until available (maximum 180 seconds).
2. Check for the `images` collection; create it if absent with the full named-vector configuration.
3. Detect schema drift and run the appropriate migration (`_migrate_small_dim` or `_migrate_color_vector`).
4. Apply scalar quantization to `embedding` and `embedding_small` (idempotent).
5. Create all payload indexes (idempotent `create_payload_index()` calls).
6. Create the `app_config` collection if absent.
7. Create the `alignment` collection and its indexes if absent.
8. Log the final readiness state including MRL availability and color vector availability.

---

## Configuration

```python
# backend/app/config.py
qdrant_url: str = "http://qdrant:6333"
embed_dim: int = 768        # must match the output dimension of the embedding model
embed_dim_small: int = 256  # MRL truncation dimension; must be <= embed_dim
```

The Qdrant service is defined in `docker-compose.yml`:

```yaml
qdrant:
  image: qdrant/qdrant:v1.18.0
  environment:
    QDRANT__TELEMETRY_DISABLED: "true"
```

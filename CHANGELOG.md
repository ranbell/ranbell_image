# Changelog

All notable changes to Ranbell Image are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [Semantic Versioning](https://semver.org/)

---

## [0.3.0] — 2026-06-21

### Added

- **Invoke / Pro Mode — major overhaul**
  - Topic-driven flow: free-text topic is converted to topic_tags + slogan before axis decomposition
  - Category section inputs: character / background / props / action / mood / camera
  - Topic expand button (⚡) — uses reference images as visual context to auto-fill 4 sections
  - WD14 tag autocomplete on all section inputs and character-tag field
  - Visual Spec tag adoption — click hair / clothing / accessory / pose / expression tags to append to character tags; +all button for batch adoption

- **Invoke / Light Mode**
  - Section inputs and theme expansion applied to Light Mode
  - BM25 tag normalization added

- **Invoke / shared quality**
  - VLM refinement pass added for axis_tag_hints before spirit composition
  - Alignment score improved with BM25 token-match component
  - Spirit card progress bar wired to real processing phases (composing / generating / tagging)
  - Seasonal emoji category added
  - ComfyUI prompt output standardized to English

- **Refine**
  - Visual Script + category tag card (WD14 hair / clothing / pose / expression tags) implemented
  - Literal text rendering redesigned as Anima format, injected without VLM pass
  - WD14 common/unique tag separation and contradiction post-processing

- **Control Room**
  - WAITED queue displayed in ISA-101 style (separate from active)
  - Invoke thumbnail retry added

### Fixed

- **Invoke / Pro Mode** — all spirits converging to the same situation when a topic is given
  - Per-spirit scene variant generation (`generate_scene_variants`) — each spirit receives a distinct scene description
  - topic_tags distributed in tiers per spirit (core / interpretive / divergent)
  - VLM filter prompt tightened to exclude generic scene-setting tags
- **Inspire** — images from multiple sources bleeding into each other
- **Inspire** — speed improvements, stronger progress bar feedback
- **Refine** — character subject and action tags dropping from output
- **Refine** — BM25 weight priority in tag injection and prose correction

---

## [0.2.0] — 2026-06-14

### Added

- **Invoke** — Five spirits generate seeds from nothing, in parallel, each driven by its own creative philosophy
  - **Five spirits**: 映 Mirror / 逆 Counter / 漂 Wander / 奔 Surge / 瞰 Vantage — each interprets the same intent through a different lens
  - **Light mode**: 39-emoji mood palette, 4-axis mood sliders (warm/cool · calm/dynamic · dense/sparse · concrete/abstract), color palette, person spec
  - **Pro mode**: Direct prompt editing, topic-to-tags conversion via Ollama, per-spirit seed control
  - **Prompt format selection**: Danbooru+natural / natural only / Danbooru only
  - **Real-time SSE streaming**: `axis_done → spirit_composed → image_ready → spirit_done → session_complete`
  - **Per-spirit monologue animations**: each spirit reveals its inner voice with a distinct text animation
  - **Alignment scoring**: Ollama evaluates how well each generated image matches the original intent (gold frame ≥ 85% / obsidian frame ≤ 15%)
  - **Respin**: regenerate a single spirit without restarting the session
  - **Adopt**: bring a seed into the collection with full genesis metadata
  - **Send to Refine**: hand off a spirit's prompt to the full ComfyUI generation pipeline
  - **Session cancel**: abort an in-flight session at any time

---

## [0.1.0] — initial release

### Added

- **Gallery** — thumbnail browser, detail panel, rating and tag management
- **Search** — semantic search (MRL two-phase), keyword search, tag search (AND/OR), color search (CIE L\*a\*b\*)
- **Inspire** — 9 creative exploration modes (Serendipity / Alchemy / Morph / Anomaly / Inversion / Discovery / Blend / Outlier / Group Search)
- **Brainstorm** — LLM-assisted idea expansion
- **Prompt Alchemy** — synthesize prompts from 1–6 reference images, one-click ComfyUI submit
- **Control Room** — job management, lane control, service health lamps
- **Job Spooler** — 5-lane parallel processing (SYNC / EMBED / EVAL / GEN / PROMPT), GPU semaphore, auto-pause
- **Analyze** — UMAP semantic map, Color 3D, Tag Network
- **Admin** — AI backfill, WD14 vocabulary import

# Changelog

All notable changes to Ranbell Image are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [Semantic Versioning](https://semver.org/)

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

# Changelog

All notable changes to Ranbell Image are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versioning: [Semantic Versioning](https://semver.org/)

---

## [0.4.0] — 2026-09-20

The Muse release. A studio where you direct a photo shoot by talking to the model,
instead of writing a prompt.

### Added

- **Muse — shoot a picture by talking about it**
  - **The shot ledger is the record of truth** — clothes, pose, expression, place, light, background, framing and atmosphere are fields, rewritten as a diff on every turn. Say it again and the restatement lands on the next turn
  - **Three ways to shoot**: solo, duet (both written apart, each with her own lines and side of the frame), or a studio crew
  - **A crew of 30 across 17 jobs in 6 presets** (standard / vivid / photoreal / calm / flat / bold) — each seat argues for one ledger field in its own corner, and a single writer holds the pen, so the crew cannot overwrite each other
  - **Test shot → final**: the test shot draws a fresh seed on every press; the final keeps the seed you approved and only raises the steps, so what you approved is what gets finished
  - **Workflow families** — a workflow is recognised as `anima` or `krea2` from its filename, or from a `muse:family=…` marker in the graph. Each family names its own steps and whether a negative prompt is sent at all; cfg and resolution are whatever the workflow was saved with
  - **She answers as she writes** — her lines stream token by token over SSE, with ComfyUI preview frames during the render
  - **Characters**: 30 presets with four wardrobe sets each, reference boards for the roster, and chemistry between pairs that grows from what they shot together
  - **The green room** — after a shoot she writes a secret diary nobody sees, posts something short for the others to react to, now and then goes out with friends, and the Showrunner's habits are noted down. Those memories come back into the preamble of the next shoot
  - **Three layers of safety**: a clerk that reads one line at a time, a second reader before anything is stopped, and a floor that protects minors regardless of settings. Dark material is still shootable — only what must stop, stops

- **Control Room**
  - Retry and dismiss for finished and failed jobs (both used to 404 — the history, not the registry, is the source of truth)
  - A job whose resource is down now waits and runs when it comes back, instead of failing

- **Characters**
  - "Erase memory" — clears every character's accrued diary, chemistry and green-room data in one action

### Changed

- **One studio.** Muse Classic, Chronicle / Weave, the tag-driven Muse and the old three-stage redraw chain are retired. The URLs are folded into `/api/muse`
- **cfg and resolution come from the workflow**, not from Muse. They differ far more between checkpoints than between stages, and the graph already carries its author's answer. Muse's own canvas is kept for the reference boards in the roster, which have to match each other
- **Every code comment and docstring is in English** (about 6,200 lines) — the Japanese that remains is the text the product itself emits, kept in the original with an English gloss

### Fixed

- **Muse** — the chat input froze for good when a `muse_speaking` signal arrived mid-POST; a whole studio turn could leave it unusable
- **Muse** — the final render drew a different seed from the test shot that was approved (all six live sessions disagreed)
- **Muse** — `SAY:` and other field labels leaked into the bubbles; the eight-line costume block reached the screen as her dialogue
- **Muse** — two Muses' words piled into one bubble, and a seat's name tag showed a job title nobody in the room used
- **Muse** — the thumbnail vanished on exactly the turns she heckled
- **Muse** — a solo or duet turn crashed with a 500 for four days (a variable bound only inside the crew branch)
- **Muse** — her turn came back as "……" at 0.0 s whenever a new argument was added to the client but not to the facade
- **Muse** — the secret diary came back in kana with spaces between the words; the language rule read as "write it in kana" (measured 35% → 0%), and a kana page is now asked for again and the retry recorded
- **Muse** — the default outfit was not loaded when a studio shoot started
- **Muse** — prose and tags were cut mid-word; long prompts now stop at a sentence or a word boundary
- **Muse** — the chat rewound to the previous seat's words every time a new seat spoke
- **Muse** — a garment taken off and put back on never reached the picture again
- **ComfyUI** — every render shared one client id, so continuing a shoot with a retake broke image streaming and mixed up previews
- **ComfyUI** — a graph that zeroes its negative out (one text encoder, `ConditioningZeroOut`) had the negative prompt burned into the **positive**

---

## [0.3.1] — 2026-06-26

Version bump only; no user-visible change.

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

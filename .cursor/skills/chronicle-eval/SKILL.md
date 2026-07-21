---
name: chronicle-eval
description: >-
  Run Chronicle via the agent API, inspect exported panel images, and propose
  Stage1/Stage2 prompt improvements. Use when evaluating Chronicle image quality,
  iterating on storyboard/enhancer prompts, discovering workflows/LLMs via
  chronicle catalog, or running chronicle_agent_run.
---

# Chronicle agent eval loop

Full manual (Claude Code / agents): **[docs/guide/chronicle-agent.ja.md](../../../docs/guide/chronicle-agent.ja.md)**  
Repo entry for Claude Code: **[CLAUDE.md](../../../CLAUDE.md)**

Human-driven improve loop: **discover → generate images → inspect → propose prompt edits → user approves → re-run**. Do **not** auto-patch prompts or auto-respin.

## 0. Discover capabilities (required before first run)

Fetch available workflows, LLMs, authors, and suggested defaults:

```bash
python scripts/chronicle_agent_run.py --base-url http://127.0.0.1:8000 --catalog
# full JSON:
python scripts/chronicle_agent_run.py --base-url http://127.0.0.1:8000 --catalog --catalog-json
```

Or `GET /api/story/chronicle/catalog`.

Important fields:

| Path | Meaning |
|------|---------|
| `comfyui.workflows` | Pass one as `workflow_name` |
| `llm.ollama.models` / `llm.openai.models` | Pass one as `story_model` |
| `authors[]` | Optional `author_id` |
| `suggested_run` | Ready-made defaults for `POST .../run` |
| `notes.story_model_required` | Chronicle does **not** fall back to Admin models |

Also available separately: `GET /api/comfy/workflows`, `GET /api/ollama/models`, `GET /api/llm/models`, `GET /api/authors`.

## 1. Generate (API or CLI)

```bash
python scripts/chronicle_agent_run.py \
  --base-url http://127.0.0.1:8000 \
  --topic "雨の日の図書室" \
  --use-catalog-defaults \
  --candidate A
```

Or explicit:

```bash
python scripts/chronicle_agent_run.py \
  --base-url http://127.0.0.1:8000 \
  --topic "雨の日の図書室" \
  --workflow "<from catalog>" \
  --story-model "<from catalog>" \
  --candidate A
```

API:

1. `GET /api/story/chronicle/catalog` → pick workflow + story_model
2. `POST /api/story/chronicle/run` with those fields plus `candidate_id`, `wait_images`, `export`
3. Poll `GET /api/story/chronicle/run/{run_id}` until `done` / `error`

On success, note `story_id` and `export_dir` (default `chronicle_evals/{story_id}/`).

## 2. Inspect artifacts

Under `export_dir`:

| File | Use |
|------|-----|
| `panel_1.png` … `panel_3.png` | **Read these images** (visual quality) |
| `report.json` | prompts, narratives, `quality_eval`, image URLs |
| `prompts.md` | quick diff-friendly prompt dump |

Also: `GET /api/story/{story_id}/eval-bundle` or `POST /api/story/{story_id}/export-eval`.

`quality_eval` is rule-based — use as a hint. Trust the pixels.

## 3. Propose improvements (do not apply yet)

Map visual issues to:

- Stage1: `backend/app/story/prompts/stage1_storyboard.md`
- Stage2: `backend/app/story/prompts/stage2_enhancer.md`

Typical failure modes: identity drift, collapsed poses, weak expression, vague actions, missing props, ignored `consistency_tags` / R0 locks.

**Wait for user approval** before editing files.

## 4. After user approval

1. Edit the approved prompt file(s)
2. Re-run step 1 (catalog still valid unless models/workflows changed)
3. Compare new `chronicle_evals/` panels

## Hard rules

- Never auto-commit prompt changes or auto-trigger respin loops
- Never invent Vision auto-scoring as a closed loop unless the user asks
- Prefer `panel_1/2/3` vocabulary (not past/present/future)
- Always set `story_model` explicitly (use catalog)

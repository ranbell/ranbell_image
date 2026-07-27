---
name: chronicle-eval
description: >-
  Run Weave (Chronicle successor) via the session API, inspect Storybook /
  eval exports, and propose prompt or gate improvements. Use when evaluating
  Weave image quality, iterating on weave prompts, discovering workflows/LLMs
  via weave catalog, or exercising /api/weave/sessions.
---

# Weave agent eval loop

Full manual: **[docs/guide/chronicle-agent.ja.md](../../../docs/guide/chronicle-agent.ja.md)**  
Design: **[docs/guide/chronicle-cocreation.ja.md](../../../docs/guide/chronicle-cocreation.ja.md)**  
Root pointer: **[CLAUDE.md](../../../CLAUDE.md)**

Human-driven improve loop: **discover → run Weave session → inspect → propose edits → user approves → re-run**. Do **not** auto-patch prompts or auto-respin.

> Old Chronicle Stage1/Stage2 agent run (`scripts/chronicle_agent_run.py`, `/api/story/chronicle*`) was removed.

## 0. Discover capabilities

```bash
curl -sS -H "X-API-Token: $RANBELL_API_TOKEN" \
  http://127.0.0.1:8000/api/weave/catalog | python -m json.tool
```

Important fields:

| Path | Meaning |
|------|---------|
| `comfyui.workflows` | Pass as `workflow_name` |
| `llm.ollama.models` / `llm.openai.models` | Pass as `story_model` |
| `authors[]` | Optional `author_id` |
| `endpoints` | Weave / Storybook routes |
| `notes.story_model_required` | Weave does **not** fall back to Admin models |

Also: `GET /api/comfy/workflows`, `GET /api/ollama/models`, `GET /api/llm/models`, `GET /api/authors`.

## 1. Generate (session API)

1. `POST /api/weave/sessions`
2. `POST .../character/infer` → `.../character/lock`
3. `POST .../story/generate` (or recreate with reason chips)
4. `POST .../lookdev` → `.../compile` → `.../sample`
5. `POST .../render_final` → `.../seal`

On seal, note `story_id` for Storybook / eval.

## 2. Inspect artifacts

| Source | Use |
|--------|-----|
| `GET /api/story/{story_id}/eval-bundle` | prompts, narratives, image URLs |
| `POST /api/story/{story_id}/export-eval` | writes `report.json` + panel PNGs |
| Storybook UI | human review of sealed stories |

**Read the panel images** before proposing prompt changes.

## 3. Propose improvements

Prefer changes to:

- `backend/app/weave/prompts/`
- Weave compile / gate / recreate chip logic under `backend/app/weave/`
- Design notes in `docs/guide/chronicle-cocreation.ja.md`

Do not resurrect Stage1/Stage2 rewrite pipelines.

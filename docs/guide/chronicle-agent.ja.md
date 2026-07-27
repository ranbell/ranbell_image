# Weave エージェント実行マニュアル（Claude Code / Cursor 向け）

> **旧 Chronicle（Stage1/Stage2・`/api/story/chronicle*`・`scripts/chronicle_agent_run.py`）は撤去済みです。**  
> 作成 UI / API は **Weave**、閲覧は **Storybook** です。

設計の本体: [chronicle-cocreation.ja.md](chronicle-cocreation.ja.md)

対象: Claude Code、Cursor Agent、その他シェル＋HTTP できるエージェント。

---

## 前提

- バックエンドが起動していること（例: `http://127.0.0.1:8000`）
- ComfyUI に workflow があり、画像生成可能なこと
- Ollama（または OpenAI 互換）に物語用 LLM があること
- 作業ディレクトリはリポジトリルート
- API は `X-API-Token` 必須（環境変数 `RANBELL_API_TOKEN`）

```text
BASE=http://127.0.0.1:8000
RANBELL_API_TOKEN=...
```

---

## やること / やらないこと

**やる**

1. catalog で workflow・LLM・作家を取得する
2. Weave セッションを作り、character → story → lookdev → sample → final → seal
3. Storybook / eval-bundle で成果を確認する
4. 品質問題があれば設計書とプロンプト（`backend/app/weave/prompts/`）への改善案を出す

**やらない**

- 旧 Stage1/Stage2 md の編集（ファイル自体が無い）
- 物語本文の部分パッチ本線（Recreate のみ）
- 承認なしの自動プロンプト改変の連続実行

---

## 0. Catalog

```bash
curl -sS -H "X-API-Token: $RANBELL_API_TOKEN" \
  "$BASE/api/weave/catalog" | python -m json.tool
```

重要フィールド:

| Path | Meaning |
|------|---------|
| `comfyui.workflows` | board / sample / final の `workflow_name` |
| `llm.ollama.models` / `llm.openai.models` | `story_model` |
| `authors[]` | 任意の `author_id` |
| `endpoints` | Weave / Storybook API 一覧 |

---

## 1. 典型フロー（要約）

1. `POST /api/weave/sessions` — セッション作成（`base_sha256` / `topic` / `story_model` / `workflow_name` など）
2. `POST .../character/infer` → `.../character/lock`（必要なら board）
3. `POST .../story/generate`（合わなければ recreate chips → `.../story/recreate`）
4. `POST .../lookdev` → `.../compile` → `.../sample`（検品・rating）
5. `POST .../render_final` → `.../seal`
6. 成果確認: `GET /api/story/storybook` / `GET /api/story/{story_id}/eval-bundle`

詳細なゲートと CTA は設計書と `GET .../cta` を参照。

---

## 2. Storybook / eval

| Method | Path | 用途 |
|--------|------|------|
| GET | `/api/story/storybook` | 保存済み物語一覧 |
| GET | `/api/story/{story_id}` | 1件 |
| GET | `/api/story/{story_id}/eval-bundle` | prompts + image URLs |
| POST | `/api/story/{story_id}/export-eval` | disk へ report + panel PNG |
| POST | `/api/story/{story_id}/regenerate/{axis}` | 画像のみ再生成 |

---

## 3. 撤去済み（使わない）

| 旧 | 状態 |
|----|------|
| `GET/POST /api/story/chronicle*` | 削除 |
| `scripts/chronicle_agent_run.py` | 削除 |
| `ChroniclePanel.vue` | 削除 |
| Stage1/Stage2 prompts & pipeline | 削除 |

---

## 関連コード

- Weave API: `backend/app/weave/`
- Storybook API: `backend/app/story/api.py`
- UI: `frontend/src/components/WeavePanel.vue`, `Storybook.vue`
- 設計: `docs/guide/chronicle-cocreation.ja.md`

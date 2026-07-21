# Chronicle エージェント実行マニュアル（Claude Code / Cursor 向け）

Ranbell Image の Chronicle（3パネル物語画像）を、**SSE なしの API / CLI** で実行し、生成画像を見て Stage1/2 プロンプトの改善案を出す手順です。

対象: Claude Code、Cursor Agent、その他シェル＋HTTP できるエージェント。

---

## 前提

- バックエンドが起動していること（例: `http://127.0.0.1:8000`）
- ComfyUI に workflow があり、画像生成可能なこと
- Ollama（または OpenAI 互換）に Chronicle 用 LLM があること
- 作業ディレクトリはリポジトリルート `ranbell_image/`

環境変数の代わりに CLI の `--base-url` を使う。

```text
BASE=http://127.0.0.1:8000   # 実際のホストに合わせて変更
```

---

## やること / やらないこと

**やる**

1. catalog で workflow・LLM・作家を取得する
2. Chronicle を端から端まで実行し、画像を `chronicle_evals/` に出す
3. パネル画像を目視し、改善案をユーザーに提示する
4. **ユーザー承認後のみ** Stage1/2 プロンプトを修正し、再実行する

**やらない**

- プロンプトの無断自動修正・自動 respin ループ
- Vision 自動採点の閉ループ化（ユーザーが求めたとき以外）
- Admin 既定モデルへの暗黙フォールバック依存（`story_model` は必ず明示）

---

## 手順 0 — 能力カタログを取る（必須）

### CLI（推奨）

```bash
python scripts/chronicle_agent_run.py --base-url "$BASE" --catalog
python scripts/chronicle_agent_run.py --base-url "$BASE" --catalog --catalog-json
```

### HTTP

```bash
curl -sS "$BASE/api/story/chronicle/catalog" | python -m json.tool
```

### 見るべきフィールド

| フィールド | 用途 |
|------------|------|
| `comfyui.workflows` | `workflow_name` にそのまま渡す |
| `llm.ollama.models` | `llm_provider=ollama` 時の `story_model` |
| `llm.openai.models` | `llm_provider=openai` 時の `story_model` |
| `authors[].id` | 任意の `author_id` |
| `suggested_run` | run リクエストのたたき台 |
| `notes.story_model_required` | Admin フォールバックなしの注意 |

単体 API（catalog の裏）:

- `GET /api/comfy/workflows`
- `GET /api/ollama/models`
- `GET /api/llm/models`
- `GET /api/authors`

---

## 手順 1 — 生成を走らせる

### A. CLI（いちばん簡単）

catalog の推奨値で埋める:

```bash
python scripts/chronicle_agent_run.py \
  --base-url "$BASE" \
  --topic "雨の日の図書室で課題を進める一日" \
  --use-catalog-defaults \
  --candidate A
```

明示指定:

```bash
python scripts/chronicle_agent_run.py \
  --base-url "$BASE" \
  --topic "カフェで働く話" \
  --workflow "YOUR_WORKFLOW.json" \
  --story-model "YOUR_MODEL" \
  --llm-provider ollama \
  --candidate A \
  --author-id "" \
  --time-scale days \
  --locale ja
```

終了コード: `0` = done、`2` = error、`3` = timeout。

### B. HTTP（ポーリング）

```bash
# 1) 開始
curl -sS -X POST "$BASE/api/story/chronicle/run" \
  -H 'Content-Type: application/json' \
  -d '{
    "user_topic": "雨の日の図書室で課題を進める一日",
    "workflow_name": "YOUR_WORKFLOW.json",
    "story_model": "YOUR_MODEL",
    "vlm_model": "YOUR_MODEL",
    "llm_provider": "ollama",
    "candidate_id": "A",
    "locale": "ja",
    "time_scale": "days",
    "wait_images": true,
    "export": true,
    "timeout_sec": 1800
  }'
# → { "run_id": "arun-...", "status": "queued", ... }

# 2) ポーリング（数秒おき）
curl -sS "$BASE/api/story/chronicle/run/arun-XXXX"
# status: queued → candidates → expanding → generating → done | error
```

`done` 時の主なフィールド:

- `story_id`
- `export_dir`（例: `.../chronicle_evals/<story_id>/`）
- `quality_eval`（ルールベース補助スコア）
- `error`（失敗時）

### 主要リクエストフィールド

| フィールド | 必須 | 説明 |
|------------|------|------|
| `user_topic` または `base_sha256` | どちらか | お題 / 参照画像 |
| `story_model`（または `vlm_model`） | **必須** | catalog から選ぶ。空だと失敗 |
| `workflow_name` | 画像を出すなら必須 | catalog の workflows から |
| `candidate_id` | 任意（既定 `A`） | Phase1 の A/B/C |
| `author_id` / `author_style` | 任意 | 作家プリセット or フリーテキスト |
| `wait_images` | 任意（既定 true） | 3パネルの `image_id` 待ち |
| `export` | 任意（既定 true） | `chronicle_evals/` へ書き出し |
| `manual_mode` | 任意 | true なら画像ジョブをスキップ |

---

## 手順 2 — 成果物を読む

`export_dir`（なければ `chronicle_evals/<story_id>/`）:

| ファイル | 内容 |
|----------|------|
| `panel_1.png` / `panel_2.png` / `panel_3.png` | **画素を見て評価する本体** |
| `report.json` | プロンプト・ナラティブ・`quality_eval`・URL |
| `prompts.md` | プロンプト差分確認用 |
| `export_meta.json` | コピー成否など |

追加 API:

```bash
curl -sS "$BASE/api/story/$STORY_ID/eval-bundle" | python -m json.tool
curl -sS -X POST "$BASE/api/story/$STORY_ID/export-eval" \
  -H 'Content-Type: application/json' -d '{}'
```

`quality_eval` の次元（0..1）: `topic_fit`, `diversity`, `expression`, `action`, `drawability`, `identity`, `richness`。  
**補助指標**。最終判断はパネル画像。

軸名は必ず `panel_1` / `panel_2` / `panel_3`（`past/present/future` は使わない）。

---

## 手順 3 — 改善案を出す（まだファイルを触らない）

画像を見て、問題 → 修正候補ファイルを対応づける。

| よくある症状 | 触る候補 |
|--------------|----------|
| 3枚で別人・髪色ブレ | Stage1 `consistency_tags` / Stage2 R0 ロック |
| ポーズが全部立ち絵で同じ | Stage1 act/gesture、Stage2 具体アクション |
| 表情が弱い | Stage2 expression 系ルール |
| 場所・小道具が薄い | Stage2 具体物・背景 |
| お題と無関係 | Stage1 theme / happening ルール |

対象ファイル:

- Stage1: `backend/app/story/prompts/stage1_storyboard.md`
- Stage2: `backend/app/story/prompts/stage2_enhancer.md`

ユーザーへ短く提案する（例）:

1. 観察（どのパネルの何がダメか）
2. 仮説（どのルール不足か）
3. 具体的な追記・修正案（差分イメージ）
4. **承認待ち**である旨

---

## 手順 4 — 承認後に修正して再実行

1. ユーザーが承認した範囲だけプロンプトを編集する
2. 同じ topic / workflow / model で手順 1 を再実行
3. 新しい `chronicle_evals/<new_story_id>/` と前回パネルを比較する

---

## エンドポイント早見表

| Method | Path | 用途 |
|--------|------|------|
| GET | `/api/story/chronicle/catalog` | workflow / LLM / authors / 推奨値 |
| POST | `/api/story/chronicle/run` | 一括実行開始 → `run_id` |
| GET | `/api/story/chronicle/run/{run_id}` | 状態ポーリング |
| GET | `/api/story/{story_id}/eval-bundle` | 評価用 JSON |
| POST | `/api/story/{story_id}/export-eval` | ディスクへ再エクスポート |
| GET | `/api/story/{story_id}` | ストーリー本体 |
| GET | `/api/comfy/workflows` | workflow のみ |
| GET | `/api/ollama/models` | Ollama モデルのみ |
| GET | `/api/llm/models` | OpenAI 互換モデルのみ |
| GET | `/api/authors` | 作家プリセット |

レガシー（UI 用・エージェントは基本不要）:

- `POST /api/story/chronicle` + SSE `GET .../stream`
- `POST /api/story/chronicle/{id}/select`

---

## トラブルシュート

| 症状 | 確認 |
|------|------|
| `No model selected` / Phase1 error | `story_model` が空。`--catalog` でモデルを選ぶ |
| 画像が無い / `missing_panels` | `workflow_name` 未指定、Comfy オフライン、`manual_mode` |
| `run` が `error` | レスポンスの `error` 文字列。Ollama/Comfy ログ |
| catalog の workflows が空 | `comfyui_workflows_dir` と Comfy 設定 |
| ポーリングが `queued` のまま | スプーラ他ジョブ待ち。`GET /api/jobs` |
| export_dir が無い | `export=false` だったか、画像未完了で失敗 |

---

## Claude Code 向け短い指示テンプレ

ユーザーが「Chronicle を回して画質を見て」と言ったら:

```text
1. python scripts/chronicle_agent_run.py --base-url <BASE> --catalog を実行
2. suggested_run または明示指定で --use-catalog-defaults 付き run
3. done まで待ち、export_dir の panel_*.png を読む
4. 改善案だけ提示し、プロンプト編集は承認後
5. 承認後に stage1/stage2 を直し、再 run
```

関連 Skill（Cursor）: `.cursor/skills/chronicle-eval/SKILL.md`  
本マニュアル: `docs/guide/chronicle-agent.ja.md`

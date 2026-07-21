# Claude Code — Ranbell Image

## Chronicle エージェント実行

Chronicle（3パネル物語画像）の **API 実行・画像評価・プロンプト改善ループ** は次を読む:

→ **[docs/guide/chronicle-agent.ja.md](docs/guide/chronicle-agent.ja.md)**

最短コマンド:

```bash
# 1) workflow / LLM 一覧
python scripts/chronicle_agent_run.py --base-url http://127.0.0.1:8000 --catalog

# 2) 生成（catalog 推奨値を使用）
python scripts/chronicle_agent_run.py --base-url http://127.0.0.1:8000 \
  --topic "お題をここに" --use-catalog-defaults

# 3) 成果物: chronicle_evals/<story_id>/panel_*.png を見て評価
# 4) 改善案はユーザー承認後にだけ
#    backend/app/story/prompts/stage1_storyboard.md
#    backend/app/story/prompts/stage2_enhancer.md
#    を編集し、再実行
```

硬規則:

- `story_model` と（画像生成時は）`workflow_name` を明示する（Admin 暗黙フォールバックなし）
- プロンプトの自動パッチ・自動 respin はしない
- 軸名は `panel_1/2/3`

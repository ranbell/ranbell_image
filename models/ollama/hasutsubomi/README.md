# Hasutsubomi 9B — Ollama Modelfile

[Qwen3.5-Hasutsubomi-9B](https://huggingface.co/Local-Novel-LLM-project/Qwen3.5-Hasutsubomi-9B)（蓮蕾）向け。日本語創作・アニメ/LN 調。**Pre-Alpha**。

## 作成

リポジトリルートで:

```bash
# 推奨: thinking オフ（Chronicles / タグ整形前の beat 向け）
ollama create hasutsubomi:9b -f models/ollama/hasutsubomi/Modelfile

# thinking あり（長文プロット向け）
ollama create hasutsubomi:9b-think -f models/ollama/hasutsubomi/Modelfile.think
```

初回は Hugging Face から Q4_K_M（約 5.7GB）を取得します。

### ローカル GGUF を使う場合

```bash
# 例: Q4_K_M をダウンロード
# https://huggingface.co/mradermacher/Qwen3.5-Hasutsubomi-9B-GGUF

# Modelfile の FROM を差し替え:
#   FROM ./Qwen3.5-Hasutsubomi-9B.Q4_K_M.gguf
ollama create hasutsubomi:9b -f models/ollama/hasutsubomi/Modelfile
```

## 試す

```bash
ollama run hasutsubomi:9b
# → 「巫女の少女が雨の駅で待つ。ライトノベル風に150字で。」
```

API（thinking を確実に切る）:

```bash
curl http://localhost:11434/api/generate -d '{
  "model": "hasutsubomi:9b",
  "prompt": "過去・現在・未来の3シーン beat を各1文で。お題: 桜の下の約束",
  "think": false,
  "stream": false
}'
```

## 注意

- **Vision**: 元モデルは VLM だが、GGUF 側の `mmproj` は実質プレースホルダ級。この Modelfile は **テキスト専用**。
- **Ollama + Qwen3.5**: 環境によっては HF 経由 GGUF のロードに問題が出ることがある。失敗時は llama.cpp / LM Studio で同 GGUF を試し、テキストのみを Modelfile `FROM ./….gguf` で取り込む。
- **量子化**: デフォルト Q4_K_M。品質優先なら `Q5_K_M` / `Q6_K` に `FROM` を変更。

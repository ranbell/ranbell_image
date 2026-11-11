# external/musubi — LoRA の学習コンテナ（今は Krea 2）

ranbell が書き出した LoRA データセット（画像＋同名 `.txt`＋`meta.json`）を読んで、
[kohya-ss/musubi-tuner](https://github.com/kohya-ss/musubi-tuner) で LoRA を作るための
**外部リソースの設定例**です。ranbell 本体はこのコンテナを呼びません。接点は書き出しフォルダだけです。

使い方の流れ（バケット → キャプション → 書き出し → 学習 → 比較 → 公開）は
[LoRA の作り方ガイド](../../docs/guide/lora.ja.md) にまとめています。

構成は「イメージは薄く、venv とソースは `work/` に置く」形です（CUDA 13.2 / Ubuntu 24.04 / Python 3.12）。
イメージを作り直しても venv と musubi は残り、ベースイメージの差し替えも Dockerfile の1行で済みます。

## ライセンス

- このフォルダのファイル（compose、Dockerfile、`work/bin/` のスクリプト）は ranbell と同じ MIT License です。
- musubi-tuner 本体は同梱していません。`musubi-setup` が固定コミットを `git clone` します。
  musubi-tuner は Apache License 2.0 です（一部のフォルダは元のプロジェクトのライセンスに従います。
  詳しくは musubi-tuner の README の License 節）。
- Krea 2 のモデルは [Krea 2 Community License](https://krea.ai/krea-2-licensing) です。
  作った LoRA を配布するときは、名前を「Krea 2 …」で始める、ライセンスの表示を付ける、などの条件があります。

## 置き場所とマウント

このフォルダを ranbell のリポジトリの**外**にコピーして使います（`work/` に venv・モデル・学習結果が数十 GB 溜まるため）。

```sh
cp -r external/musubi /path/to/docker-musubi
cd /path/to/docker-musubi
```

| ホスト | コンテナ | 用途 |
|---|---|---|
| `./work` | `/work` | venv、musubi-tuner、`models/`、`cache/`、`output/` |
| `.env` の `LORA_DATASETS_DIR` | `/work/datasets`（読み取り専用） | ranbell の backend に `/mnt/lora_export` としてマウントしているのと同じフォルダ |

ranbell 側は `docker-compose.override.yml` の backend に、同じフォルダを書き込み可でマウントします。

```yaml
      - /path/to/lora/datasets:/mnt/lora_export
```

管理画面の LoRA 節で、書き出し先フォルダが「✓ 書き込めます」になっていれば準備完了です。

## 初回

```sh
cp .env.example .env          # LORA_DATASETS_DIR と、ComfyUI / Ollama の URL（学習前にこの2つを空ける）
docker compose up -d --build
docker compose exec musubi musubi-setup   # venv と musubi-tuner（固定コミット）
```

## ファイルの配置（Krea 2）

```
docker-musubi/                      ← このフォルダをコピーした場所
├── .env                            LORA_DATASETS_DIR、ComfyUI / Ollama の URL
└── work/
    ├── models/
    │   └── krea2/                  ← 学習に使うモデル（自分で取得して置く）
    │       ├── raw.safetensors               Krea 2 Raw の DiT（bf16、約26GB）
    │       ├── qwen3vl_4b_bf16.safetensors   テキストエンコーダ（bf16、単一ファイル、約8.9GB）
    │       └── qwen_image_vae.safetensors    VAE（約250MB）
    ├── venv/  musubi-tuner/        musubi-setup が作る
    ├── cache/<bucket>/             latent とテキストエンコーダ出力のキャッシュ
    └── output/<bucket>-<日時>/     学習結果（epoch ごとの LoRA、train.log、summary.json）

<LORA_DATASETS_DIR>/<bucket>/       ranbell が書き出す（コンテナからは /work/datasets、読み取り専用）
    <sha>.png  <sha>.txt  meta.json

ComfyUI/models/loras/               選んだ LoRA を手でコピーする先
```

| ファイル | 取得元 |
|---|---|
| `raw.safetensors` | huggingface.co/krea/Krea-2-Raw（gated。ライセンスに同意してから取得） |
| `qwen3vl_4b_bf16.safetensors` | huggingface.co/Comfy-Org/Qwen3-VL の `text_encoders/` |
| `qwen_image_vae.safetensors` | huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI の `split_files/vae/` |

- **ComfyUI 用の fp8_scaled 版は使えません。** musubi は bf16 を読んで自分で量子化します。
  fp8 のファイルを渡すとスケールを掛けずに読まれ、エラーにならずに重みが壊れます。
- テキストエンコーダは HuggingFace のフォルダ形式ではなく、**単一の `.safetensors`** が必要です。
- `work/models/` 直下に3つを置いた古い配置もそのまま使えます。別の場所に置くなら `.env` の
  `KREA2_DIT` / `KREA2_TE` / `KREA2_VAE` でファイルごとに指定できます。
- RAM は 64GB 以上が推奨です（ブロック退避で DiT の一部を CPU 側に置くため）。

## 学習

```sh
docker compose exec musubi train                      # 書き出し済みのデータセット一覧
docker compose exec musubi train <bucket> --dry-run   # 確認だけ（コマンドと TOML を表示）
docker compose exec musubi train <bucket>             # 16gb プリセット（1024px、16 epoch）
docker compose exec musubi train <bucket> --epochs 30
docker compose exec musubi train <bucket> --preset 16gb-safe   # カードに他を載せたまま
```

`train` が順に行うこと：

1. **確認**：meta.json、全画像に空でない `.txt` があるか、モデル3点。欠けていれば走り出す前に止まる
2. **GPU を空ける**：ComfyUI に `POST /free`、Ollama は読み込み中のモデルを `keep_alive: 0` で降ろす。
   そのあと空き VRAM がプリセットの下限に届くまで最大20秒待ち、届かなければ止まる
3. **TOML 生成**：`output/<bucket>-<日時>/dataset.toml`（bucket 有効、拡大なし）
4. **実行**：latent キャッシュ → テキストエンコーダ出力キャッシュ → 学習
   - キャッシュは `cache/<bucket>/`。latent は画像名（sha）単位で使い回す
   - テキストエンコーダ出力はキャプションのハッシュが同じときだけ使い回す（キャプションを直したら作り直す）
   - VRAM 不足で落ちたら、blocks_to_swap を 4 増やして自動でやり直す（26 まで）
5. **報告**：`train.log`、`summary.json`（所要時間・ピーク VRAM・設定）、epoch ごとの `.safetensors`

Ctrl-C・端末を閉じる・SIGTERM のどれでも、学習はプロセスグループごと止まります（GPU を握ったまま残りません）。
長時間なので、`tmux` などの中で動かすのがおすすめです。

**学習中は ranbell で描画しないこと。** 同じカードなので、描画が始まると学習も描画も落ちます。
ranbell のコントロールルームで GEN レーンを一時停止しておくのが確実です。

### 進み具合を見る

```sh
docker compose exec musubi status            # いちばん新しい学習
docker compose exec musubi status <bucket>   # そのデータセットの最新
docker compose exec musubi status -w 60      # 60秒ごとに更新（Ctrl-C で抜ける）
docker compose exec musubi status --list     # 全部の学習と状態
```

進捗（step・s/step・残り時間）、epoch ごとの loss（平均・最小・最大）と保存済みファイル、
step ごとの loss のグラフを出します。tensorboard のログ（`output/<実行名>/logs`）を読んでいます。

**loss で良し悪しは決まりません。** 拡散モデルの loss は毎 step ランダムなノイズ量で測るので揺れが大きく、
あまり下がりません。見るのは異常（急な跳ね、NaN、止まった学習）だけで、効き具合は保存した epoch を
ComfyUI で比べて決めます。良さそうな epoch が出たら Ctrl-C で止めて構いません（そこまでの保存は残ります）。

## プリセット（16GB カード向け）

RTX 4060 Ti 16GB（PCIe x8）での実測（rank 32）。

| | 解像度 | blocks_to_swap | GC CPU offload | ピーク VRAM | s/step |
|---|---|---|---|---|---|
| `16gb`（既定） | 1024 | 20 | — | 15.5 GiB | 15.3 |
| `16gb-safe` | 1024 | 26 | ✓ | 8.4 GiB※ | 16.7※ |

※ 1行キャプションの試しデータでの値。`16gb` は 18枚・VLM が書いたキャプション（数百語）での値。

共通：fp8（`--fp8_base --fp8_scaled`）、rank/alpha 32、lr 1e-4、adamw8bit、
`--timestep_sampling krea2_shift`、epoch ごと保存、`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`。

分かったこと：

- **VRAM も速さも、ほぼ blocks_to_swap で決まる**（1ブロック約 0.45 GiB）。勾配チェックポイントが
  効いているので解像度の差は小さい。だから Raw の学習解像度に合わせて 1024 を既定にした
- **キャプションの長さも VRAM を食う**（Krea 2 は文章のトークンも画像と同じ列に並べる）。
  swap 16 は1行キャプションでは 13.6 GiB だったが、本番のキャプションでは 2 step 目に OOM
- デスクトップ（ブラウザ、エディタ等）が 1 GiB 前後カードを使っている。その分も見込むこと
- `16gb-safe` は ComfyUI など別のものをカードに載せたまま回したいとき用

### 1回あたりの固定費

学習本体の前に、DiT（26GB）の読み込みと fp8 化で 1〜3 分かかります。モデルが HDD にあると、
直前に読んだばかり（ページキャッシュに残っている）なら速く、そうでなければ遅くなります。
latent キャッシュは新しい画像と解像度を変えたときだけ作り直します。

## 出来た LoRA

`output/<bucket>-<日時>/<bucket>.safetensors`（途中 epoch は `-000004` などの連番）。
推論は Turbo 系で（公式の推奨は「Raw で学習 → Turbo で推論」）。ComfyUI への配置は手作業です。
キーは `lora_unet_blocks_*` 形式（792個）で、ComfyUI がそのまま読めます。**変換は要りません。**
コンテナは root で動きますが、出力はホストの `work/output/` の持ち主に渡し直します。

## 公開用のコピー（Civitai など）

```sh
docker compose exec musubi publish <bucket>              # 最新の学習の最終版
docker compose exec musubi publish <bucket> --epoch 20   # その epoch のもの
docker compose exec musubi publish <bucket> --all        # メタデータを全部消す
```

`output/<実行名>/publish/` にコピーを作ります。重みは1バイトも変えず、ファイル先頭のメタデータだけ書き換えます。

- **残す**：学習の設定（rank、lr、epoch、optimizer、解像度など）。使う人の参考になる
- **消す**：学習した日時、セッション ID、ベースモデル・VAE のファイル名、データセットのフォルダ名
- キャプションとタグは musubi がそもそも書きません。他のツールで作った LoRA を渡したとき用に、
  `ss_tag_frequency` などがあれば消します

## ターゲットモデルを増やすには

学習するモデルは、書き出された `meta.json` の `target_model` で決まります。モデルを1つ増やすには、両側に1件ずつ足します。

1. **ranbell 側：** `backend/app/lora/targets.py` の `TARGETS` に1件。キャプションの書き方（文章かタグか）と、
   VLM への描写の指示をここで決めます。`available=True` にするとバケット画面で選べるようになります。
2. **学習側：** `work/bin/train_lora.py` の `RECIPES` に `Recipe` を1件。モデルファイル、musubi のスクリプト名、
   `network_module`、そのモデル専用の学習フラグ、プリセットを書きます。

知らない `target_model` のデータセットを渡すと、学習は走り出す前に止まり、対応しているモデルの一覧を出します。

**Anima について（2026-11 時点）：** ここで固定している musubi-tuner のコミットには、Anima 用の学習スクリプトがありません。
対応するときは、musubi の更新（`setup.sh` の `MUSUBI_COMMIT`）を待つか、別の学習器を使う Recipe を足すことになります。
Anima はタグ中心のプロンプトで学習されたモデルなので、ranbell 側の Target は `caption_style="tags"` の描写指示になる見込みです。

## テスト

```sh
python3 -m pytest tests     # GPU も musubi も要らない
```

<div align="center">

<img src="assets/ranbell_image_logo.png" alt="Ranbell Image" width="120" />

# Ranbell Image

**ローカル AI 画像スタジオ — 意味で探し、感覚で錬成する。**

[![Version](https://img.shields.io/badge/version-0.1.0-blue)](https://github.com/ranbell/ranbell_image/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)](docker-compose.yml)
[![ghcr.io](https://img.shields.io/badge/ghcr.io-ranbell%2Franbell--image-blue?logo=github)](https://github.com/ranbell/ranbell_image/pkgs/container/ranbell-image-backend)
[![Qdrant](https://img.shields.io/badge/Qdrant-v1.18-6B4FBB)](https://qdrant.tech/)
[![Ollama](https://img.shields.io/badge/Ollama-local_AI-grey)](https://ollama.ai/)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-integration-orange)](https://github.com/comfyanonymous/ComfyUI)
[![WD14](https://img.shields.io/badge/WD14-tagger-pink)](https://huggingface.co/SmilingWolf)

**English version → [README.md](README.md)**

</div>

---

![Main Gallery](docs/screenshots/JA_01_01_gallery.png)
![Detail Info](docs/screenshots/JA_01_02_gallery.png)
---

## Ranbell Image とは

「ファイル名ではなく、雰囲気で画像を探せたら」という思いつきから始まりました。

[Qdrant](https://qdrant.tech/) というベクターデータベースに出会い、セマンティック検索の可能性を知ったとき、検索だけではなく色々できるかも？と感じました。最初は検索の実験でしかなかったものが、機能を重ねるうちに、AI 画像クリエイターのための創作スタジオになりました。

Ranbell Image は**完全ローカル動作**のアプリケーションです。画像はあなたのマシンの外に出ません。

> *このアプリは [Claude](https://claude.ai)（Anthropic）との緊密な共同作業によって設計・実装されました。アーキテクチャの決定から機能設計、すべてのコードに至るまで、その協働から生まれています。ドメイン知識を持つ人間と、それを実装できる AI が組み合わさったときに何が生まれるか、その一例です。*

---

## 主な機能

### 🔍 探索 — なんとなくの雰囲気で検索

すべての画像は [Ollama](https://ollama.ai/) による埋め込みベクターとして Qdrant に格納されます。検索は意味のレベルで機能します。

- **セマンティック検索** — `「憂鬱な少女が佇んでいる」` と自然言語で入力すると、そういう雰囲気の画像を見つけてきます
- **キーワード検索** — プロンプト・説明・モデル名の全文検索
- **タグ検索** — [WD14](https://huggingface.co/SmilingWolf) が全画像を 1,000 以上の Danbooru カテゴリで自動タグ付け。AND/OR 絞り込みとオートコンプリート対応
- **カラー検索** — Hex カラーを指定すると CIE L\*a\*b\* 空間での知覚的に正確なカラーマッチング。反対色の除外オプション付き
- **フィルターの組み合わせ** — セマンティック + タグ + カラー + 評価 + 適合度スコアを同時に使用可能

![Semanctic](docs/screenshots/JA_02_search_semantic.png)
---

### 🎛️ コントロールルーム — すべてのジョブの指令センター

`/` キーまたは上部ボタンでコントロールルームを開きます。

スキャン・埋め込み生成・プロンプト錬成・画像生成のすべてがジョブとして管理されます。

- 個々のジョブのキャンセル・一時停止・再開・並び替え
- レーン単位の一時停止（SYNC / EMBED / EVAL / GEN）
- ISA-101 スタイルのステータスランプ（Qdrant・Ollama・ComfyUI・GPU）
- 全ジョブ履歴を一元管理

![Control Room](docs/screenshots/03_control_room.png)

---

### ⚗️ 錬成 — プロンプト錬金術スタジオ

参照画像を 1〜6 枚選び、短い指示を書くだけで、VLM がプロンプトを生成します。

**例：** キャラクター画像を 2 枚ピン留めして *「ウサ耳をつけてサマードレスにして」* と書くだけ。WD14 が参照画像から視覚的な語彙を抽出し、Ollama が正確なプロンプトを合成します。

モデルに合わせた出力スタイルを選択できます：

| スタイル | 例 | 向いているモデル |
|---|---|---|
| **Danbooru** | `rabbit_ears, 1girl, summer_dress, outdoors` | タグ学習モデル（SD 1.5、SDXL、Pony） |
| **自然言語** | `A girl with rabbit ears in a summer dress, standing in a garden` | NL 優先モデル（FLUX、Anima） |
| **ハイブリッド** | `rabbit_ears, 1girl \| standing in a garden, warm afternoon light` | 両方のモデルファミリー |

- ComfyUI ワンクリック送信（ワークフローへのプロンプト注入を自動化）
- ストリーミング出力（生成過程のトークンをリアルタイムで表示）
- 適合度スコアリング（VLM が画像とプロンプトの一致度を 0〜100% で評価）

> 📖 [クリエイターガイド — プロンプト錬成](docs/guide/prompt-alchemy.ja.md) · [技術リファレンス](docs/tech/prompt-alchemy.ja.md)

![Prompt Studio-1](docs/screenshots/JA_04_01_prompting.png)
![Prompt Studio-3](docs/screenshots/JA_04_03_prompting.png)

---

### ✨ インスパイア — 9 つの探索モード

| モード | 何を使うか | 向いている用途 |
|---|---|---|
| **セレンディピティ** | Qdrant ベクター検索 | 「似てるけど違う何か」を見つける |
| **錬金術** | Qdrant ベクター演算（A + B − C） | 「この構図＋あの色合い−都会っぽさ」 |
| **モーフ** | Qdrant LERP（5 段階） | A と B の間にある美意識を見る |
| **アノマリー** | WD14 タグ共起分析 | 珍しい組み合わせから知的な発見を |
| **インバージョン** | Qdrant + VLM（Ollama） | 昼↔夜・明↔暗など対極の世界を探す |
| **ディスカバリー** | Qdrant DiscoverQuery | 「この画像の対極は何か？」 |
| **ブレンド** | Qdrant 加重重心 | 複数の雰囲気を割合を指定して混ぜる |
| **アウトライアー** | Qdrant + UMAP 密度 | コレクション内で最も孤立した画像を探す |
| **グループ検索** | Qdrant GroupBy | モデル・カテゴリ別に結果をグルーピング |

> 📖 [クリエイターガイド — インスパイア & ブレスト](docs/guide/inspire-brainstorm.ja.md) · [技術リファレンス](docs/tech/inspire-brainstorm.ja.md)

![Inspire-1](docs/screenshots/JA_05_01_inspire.png)
![Inspire-2](docs/screenshots/JA_05_02_inspire.png)

---

### 📊 アナライズ — コレクション全体を俯瞰する

**セマンティックマップ（UMAP）**
768 次元の埋め込みを 2D 散布図に圧縮。近い作品は近くに配置されます。K-means でクラスターを自動検出。ポイントをホバーするとサムネイルを表示。クリックで検索。

**カラー 3D**
各画像の支配色を CIE L\*a\*b\* 3 次元空間にプロット。回転して見ることで、自分のカラーパレットの偏りが一目でわかります。

**タグネットワーク**
タグを節点、共起を辺とする力指向グラフ。密集したクラスターが自分の「視覚的な語彙」を示します。ノードをクリックすると検索に移動。

![Analyzer-1](docs/screenshots/JA_06_01_analyzer.png)
![Analyzer-2](docs/screenshots/06_02_analyzer.png)

---

## ドキュメント

各機能に 2 種類のドキュメントがあります。目的に合った入り口を選んでください。

| 機能 | 使い方を知りたい | 仕組みを深く知りたい |
|---|---|---|
| **インスパイア & ブレスト** | [クリエイターガイド →](docs/guide/inspire-brainstorm.ja.md) | [技術リファレンス →](docs/tech/inspire-brainstorm.ja.md) |
| **プロンプト錬成** | [クリエイターガイド →](docs/guide/prompt-alchemy.ja.md) | [技術リファレンス →](docs/tech/prompt-alchemy.ja.md) |

**クリエイターガイド** は、各モードの使い方・使い分け・入出力の関係を図解で説明します。実装の詳細には踏み込みません。

**技術リファレンス** は、アルゴリズム仕様・数学的基盤（L2 正規化・反復正規化・LERP・符号反転）・Qdrant クエリパターン（DiscoverQuery・GroupBy・MRL 2フェーズ）・VLM 3ステージパイプラインをカバーします。

その他: 
- [Qdrant コレクション設計 →](docs/tech/qdrant.ja.md) 
-  [ジョブスプーラー & タスクスケジューリング →](docs/tech/spooler.ja.md)

---

## システム要件 (System Requirements)

> ⚠️ **Ranbell Image を起動する前に、以下の 3 つの外部サービスをあなたのマシンにインストールし、起動済みにしてください。**

### 必須サービス

| サービス | 役割 | デフォルトアドレス |
|---|---|---|
| **Docker + Docker Compose v2** | アプリ本体・Qdrant の起動 | — |
| **[Ollama](https://ollama.ai/)** | ローカル LLM / VLM 推論 — プロンプト錬成・画像解析・埋め込み生成 | `http://localhost:11434` |
| **[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** | 画像生成バックエンド | `http://localhost:8188` |

**NVIDIA GPU（VRAM 16GB 以上推奨）** が実質必須です（VLM 推論と画像生成の快適な動作に必要）。

### 動作検証済みモデル (Ollama)

以下のモデルで開発・動作確認を行っています：

| 用途 | モデル | インストール |
|---|---|---|
| VLM — 画像解析・プロンプト錬成 | `gemma4:e2b` | `ollama pull gemma4:e2b` |
| 埋め込み — セマンティック検索 | `embeddinggemma:300m` ⚠️ 必須 | `ollama pull embeddinggemma:300m` |

> ⚠️ **埋め込みモデルは `embeddinggemma:300m` が必須です。** システムはマルチ解像度セマンティック検索にマトリョーシカ埋め込みを使用しており、通常の埋め込みモデルはこれに対応していないため代替できません。
>
> VLM については他の Ollama 対応モデルでも動作する可能性はありますが、テストは行っていません。

---

## クイックスタート

**前提条件:** 上記の[システム要件](#システム要件-system-requirements)を満たしていること。

```bash
git clone https://github.com/ranbell/ranbell_image.git
cd ranbell_image

cp docker-compose.override.yml.example docker-compose.override.yml
# docker-compose.override.yml を編集（下記参照）
```

> ⚠️ **起動前に `docker-compose.override.yml` を必ず編集してください：**
>
> - 元画像フォルダ: `/mnt/image/source/<ラベル名>` として `:ro`（読み取り専用）でマウント。`<ラベル名>` がアプリ内のフォルダ表示名になります。
> - 生成画像フォルダ: `/mnt/image/generated` として **`:ro` なし**（書き込み可）でマウント。元画像フォルダとは必ず別ディレクトリにしてください。

```bash
# ghcr.io のビルド済みイメージを使う（推奨）
docker compose pull && docker compose up -d

# またはローカルビルド
docker compose up -d --build
```

ブラウザで **http://localhost:3100** を開きます。

**初回起動後:** ヘッダーの **SCAN** ボタンをクリックし、その後 **Admin** パネルから AI バックフィルを実行してセマンティック検索を有効化します。詳細は [INSTALLATION.ja.md](INSTALLATION.ja.md) を参照してください。

---

## 感謝

**[Qdrant](https://qdrant.tech/)** — このプロジェクト全体がQdrantによって存在しています。セマンティックベクター検索をこれほど優雅に扱えるものに初めて出会ったとき、これで何か作らなければという気持ちになりました。「意味で画像を検索できたら」という思いつきが、今あなたが見ているすべてになりました。Qdrant チームに心より感謝します。

**[Ollama](https://ollama.ai/)** — ローカルで動く LLM/VLM 推論の決定版。Ranbell Image のすべての埋め込み生成・画像解析・プロンプト錬成・適合度評価は Ollama を通じて流れています。

**[WD14 Tagger — SmilingWolf](https://huggingface.co/SmilingWolf)** — EVA02-large モデルは大規模な Danbooru タグ予測を驚くほどの精度で行います。タグ検索・アノマリー検出・プロンプト錬成の Danbooru 語彙の根幹を担っています。

**[ComfyUI](https://github.com/comfyanonymous/ComfyUI)** — 最も柔軟な画像生成環境。Ranbell Image は ComfyUI の HTTP API を通じてインスパイア→錬成→生成→コレクションへの還流というクリエイティブループを完結させます。

**[UMAP](https://umap-learn.readthedocs.io/)** — 768 次元の埋め込みを操作可能な 2D マップに変換する技術は、本当に驚異的です。

---

## アーキテクチャ

### システム概要

```mermaid
graph LR
    User(("User"))

    subgraph FE ["Frontend · Vue 3 / Vite"]
        UI["Control Room"]
        IP["Inspire Panel"]
    end

    subgraph BE ["Backend · FastAPI"]
        API["FastAPI"]
    end

    subgraph SP ["Job Spooler · spooler.py"]
        Spl["JobSpooler"]
        SYNC["SYNC"]
        EMBED["EMBED"]
        GEN_L["GEN"]
        PROMPT_L["PROMPT"]
        EVAL_L["EVAL"]
    end

    subgraph INF ["Local Inference"]
        EMB["Ollama<br/>(embeddings)"]
        VLM_N["Ollama (VLM)"]
        WD14_N["WD14 Tagger"]
        LAB_N["Color Extractor<br/>(CIE L*a*b*)"]
        UMAP_N["UMAP"]
    end

    COMFY_N["ComfyUI"]

    subgraph DB ["Qdrant · Vector DB"]
        QC["AsyncQdrantClient"]
        IMG[("'images'<br/>768d + 256d + 3d")]
        ALN[("'alignment'")]
        CFG[("'app_config'")]
    end

    User -->|"clicks"| UI
    UI & IP -- "REST /api" --> API
    API -- "SSE /api/jobs/stream" --> UI
    API -- "spooler.submit()" --> Spl
    Spl --> SYNC & EMBED & GEN_L & PROMPT_L & EVAL_L
    SYNC --> QC
    EMBED --> EMB & WD14_N & LAB_N
    GEN_L -- "POST /prompt" --> COMFY_N
    PROMPT_L & EVAL_L --> VLM_N
    EMB & WD14_N & LAB_N & UMAP_N --> QC
    VLM_N --> QC
    COMFY_N -.->|"generated image → SYNC"| SYNC
    QC --> IMG & ALN & CFG

    classDef fe fill:#dbeafe,stroke:#3b82f6,color:#1e3a8a
    classDef api fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef spooler fill:#faf5ff,stroke:#7c3aed,color:#581c87
    classDef lane fill:#ede9fe,stroke:#7c3aed,color:#3b0764
    classDef infer fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e
    classDef gen fill:#ffedd5,stroke:#c2410c,color:#7c2d12
    classDef db fill:#f0fdf4,stroke:#15803d,color:#14532d

    class UI,IP fe
    class API api
    class Spl spooler
    class SYNC,EMBED,GEN_L,PROMPT_L,EVAL_L lane
    class EMB,VLM_N,WD14_N,LAB_N,UMAP_N infer
    class COMFY_N gen
    class QC,IMG,ALN,CFG db
```

### ジョブ管理

```mermaid
graph TD
    subgraph Spooler ["JobSpooler · in-memory singleton"]
        PQ["Priority Queue<br/>(one per lane)"]
        AP["Auto-Pause Logic"]
    end

    GPU{"GPU Semaphore<br/>concurrency = 1"}

    subgraph Workers ["Lane Workers · asyncio tasks"]
        sw["SYNC"]
        ew["EMBED"]
        gw["GEN"]
        pw["PROMPT"]
        ev["EVAL"]
    end

    PQ --> sw & ew & gw & pw & ev
    gw & ew -->|"async with semaphore"| GPU
    AP -.->|"Tier 1 · pause when GEN / PROMPT active"| ew
    AP -.->|"Tier 1+2 · pause when GEN / PROMPT / EMBED active"| ev

    classDef worker fill:#ede9fe,stroke:#7c3aed,color:#3b0764
    classDef spooler fill:#faf5ff,stroke:#7c3aed,color:#581c87
    classDef gpu fill:#fef3c7,stroke:#d97706,color:#78350f

    class sw,ew,gw,pw,ev worker
    class PQ,AP spooler
    class GPU gpu
```

---

## ライセンス

[MIT License](LICENSE)

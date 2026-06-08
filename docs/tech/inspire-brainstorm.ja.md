# インスパイア & ブレスト — 技術リファレンス

**Ranbell Image v0.1.0**

---

## 目次

1. [埋め込みインフラストラクチャ](#1-埋め込みインフラストラクチャ)
2. [数学的基盤](#2-数学的基盤)
3. [技術要素の早見表](#3-技術要素の早見表)
4. [各モード — アルゴリズム仕様](#4-各モード--アルゴリズム仕様)
   - [セレンディピティ](#セレンディピティ-)
   - [錬金術](#錬金術-)
   - [モーフ](#モーフ-)
   - [アノマリー](#アノマリー-)
   - [インバージョン](#インバージョン-)
   - [ディスカバー](#ディスカバー-)
   - [グループ検索](#グループ検索-)
   - [ブレンド](#ブレンド-)
   - [アウトライアー](#アウトライアー-)
5. [ブレスト — LLM タグパイプライン](#5-ブレスト--llm-タグパイプライン)
6. [Qdrant クエリパターン](#6-qdrant-クエリパターン)
7. [VLM 3ステージパイプライン（インバージョン詳解）](#7-vlm-3ステージパイプラインインバージョン詳解)
8. [クリエイティブパイプライン全体像](#8-クリエイティブパイプライン全体像)

---

## 1. 埋め込みインフラストラクチャ

### 1.1 埋め込みモデルと次元

| パラメータ | 値 |
|---|---|
| ベクトル次元 | 768 |
| MRL プレフィックス次元 | 256 |
| 類似度メトリクス | コサイン類似度（正規化済みベクトルへのドット積） |
| Qdrant コレクション | `images` |

### 1.2 Qdrant ベクトル構成（MRL）

各画像ドキュメントは 2 本のベクトルを持ちます。

```
Qdrant ポイント {
  id:      sha256 ハッシュ（文字列）
  vectors: {
    "full":    float[768]   ← 完全精度ベクトル（リランク用）
    "mrl256":  float[256]   ← MRL プレフィックス（プリフェッチ用）
  }
  payload: {
    path:             string
    wd14_tags:        string[]
    wd14_tags_scores: float[]
    umap_x:           float     ← 孤立島モード用
    umap_y:           float
    model_name:       string    ← グループ検索用
    extension:        string
    ...
  }
}
```

### 1.3 2フェーズ MRL 検索

すべての Qdrant 検索は 2 フェーズで実行されます。

```
フェーズ 1: プリフェッチ
  クエリ (mrl256) × コレクション全体 (mrl256)
  → 上位 N × k 件の候補セット（高速・低精度）

フェーズ 2: リランク
  候補セット × クエリ (full) × 候補 (full)
  → 上位 k 件を完全精度で再スコアリング（精密）

N = プリフェッチ倍率（通常 5〜10）
k = 最終返却件数
```

---

## 2. 数学的基盤

### 2.1 L2 正規化

```
norm(v) = v / ‖v‖₂

‖v‖₂ = √(v₁² + v₂² + ... + v_n²)

正規化後: ‖norm(v)‖₂ = 1.0
```

すべての埋め込みは比較前に単位球上に射影されます。これにより類似度は **magnitude** ではなく **方向（角度）** で測定されます。

### 2.2 コサイン類似度とドット積の等価性

```
cos(θ) = (u · v) / (‖u‖₂ × ‖v‖₂)

u, v が単位ベクトルの場合:
  cos(θ) = u · v

∴ Qdrant のドット積検索 ≡ コサイン類似度検索（正規化済みの場合）
```

### 2.3 反復正規化（Iterative Normalization）

複数ベクトルの加算に使用。単純加算を繰り返すと magnitude が増大し、後から追加するベクトルの相対的影響力が低下する問題を防ぎます。

```
反復正規化アルゴリズム:
  v₀ = norm(a₁)
  vᵢ = norm(vᵢ₋₁ + norm(aᵢ₊₁))  ← 加算のたびに再正規化

vs. 単純加算:
  v_naive = a₁ + a₂ + ... + aₙ   ← ‖v_naive‖ ≈ √n に増大
```

### 2.4 線形補間（LERP）

```
LERP(A, B, t) = (1 - t) · A + t · B,  t ∈ [0, 1]

モーフモードの実装:
  for t in [0.2, 0.4, 0.6, 0.8, 1.0]:
    v(t) = norm(LERP(norm(A), norm(B), t))
    → Qdrant 最近傍検索
```

### 2.5 加重線形結合（ブレンド）

```
v_blend = norm( Σᵢ wᵢ · norm(aᵢ) )

wᵢ ∈ [-1.0, +1.0]

  wᵢ > 0: 類似方向に引く
  wᵢ < 0: 反対方向に押す（疑似減算）
  wᵢ ≈ 0: 影響なし
```

### 2.6 ベクトル符号反転（アウトライアー・アンチポード）

```
重心の計算:
  v_centroid = norm( Σᵢ norm(xᵢ) / N )   ← 全画像の平均

アンチポードへの変換:
  v_antipode = -v_centroid

意味: 高次元空間でコレクションの重心から最も遠い方向を指す
```

---

## 3. 技術要素の早見表

### モード × 技術要素マトリクス

| モード | ベクトル平均化 | 反復正規化 | LERP | 加重合成 | 符号反転 | Qdrant 標準 | DiscoverQuery | GroupBy | LLM タグ生成 | VLM 3ステージ | WD14 タグ | UMAP 密度 | SSE |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| ✨ セレンディピティ | ● | — | — | — | — | ● | — | — | — | — | — | — | — |
| ⚗️ 錬金術 | — | ● | — | — | — | ● | — | — | — | — | — | — | — |
| 🌊 モーフ | — | ● | ● | — | — | ● | — | — | — | — | — | — | — |
| ⚡ アノマリー | ● | — | — | — | — | ● | — | — | ● | — | ● | — | — |
| 🪞 インバージョン | ● | — | — | — | — | ● | — | — | — | ● | ● | — | ● |
| 🧭 ディスカバー | — | — | — | — | — | — | ● | — | — | — | — | — | — |
| 🗂️ グループ検索 | — | — | — | — | — | — | — | ● | — | — | — | — | — |
| ⚖️ ブレンド | — | — | — | ● | — | ● | — | — | — | — | — | — | — |
| 🌌 アウトライアー（アンチポード） | ● | — | — | — | ● | ● | — | — | — | — | — | — | — |
| 🌌 アウトライアー（孤立島） | — | — | — | — | — | ◐ | — | — | — | — | — | ● | — |
| 💡 ブレスト | — | — | — | — | — | — | — | — | ● | — | ● | — | ● |

● = 中核技術　◐ = 条件付き使用　— = 使用しない

### 処理コスト分類

```
純ベクトル演算（マイクロ秒〜ミリ秒オーダー、決定論的）
  錬金術 / モーフ / ブレンド

ベクトル演算 + Qdrant 高度機能
  セレンディピティ（パーセンタイルサンプリング）
  アウトライアー（重心計算 or 密度スキャン）
  ディスカバー（DiscoverQuery）
  グループ検索（GroupBy）

LLM/VLM 統合（秒〜10秒オーダー、非決定論的）
  アノマリー   — LLM 1回呼び出し（temperature 0.7）
  ブレスト     — LLM 1回呼び出し（SSE ストリーミング）
  インバージョン — VLM 2〜3回呼び出し（SSE ストリーミング）
```

---

## 4. 各モード — アルゴリズム仕様

---

### セレンディピティ ✨

**疑似コード:**

```python
def serendipity(ref_sha256s: list[str], k: int = 20) -> list[Image]:
    # 1. クエリベクトル構築
    vecs = [get_embedding(sha) for sha in ref_sha256s]
    q = norm(mean(vecs))                    # 平均 → 正規化

    # 2. 上位 1,000 件取得（MRL 2フェーズ）
    results = qdrant.search(
        collection="images",
        query=q,
        limit=1000,
        with_payload=False,
        using="full",                        # MRL フル
        prefetch=Prefetch(query=q[:256], limit=5000, using="mrl256")
    )
    scores = [r.score for r in results]

    # 3. パーセンタイル帯の計算
    p25 = percentile(scores, 25)
    p75 = percentile(scores, 75)

    # 4. P25〜P75 の帯域内をフィルタ
    band = [r for r in results if p25 <= r.score <= p75]

    # 5. 帯域内からランダムサンプリング
    return random.sample(band, min(k, len(band)))
```

**パーセンタイル帯の設計意図:**

固定閾値（例: 0.5〜0.7）は、埋め込みモデルのスコア分布がモデルごとに大きく異なるため、汎用的に機能しません。パーセンタイル帯はコレクションの **実際のスコア分布に自動アンカー** され、常に「中程度の類似度帯」を指します。

---

### 錬金術 ⚗️

**疑似コード:**

```python
def alchemy(
    add_sha256s: list[str],   # 最大 3 枚
    sub_sha256s: list[str],   # 最大 3 枚
    k: int = 20
) -> list[Image]:
    # 1. 加算ベクトルの反復正規化合成
    q = norm(get_embedding(add_sha256s[0]))
    for sha in add_sha256s[1:]:
        q = norm(q + norm(get_embedding(sha)))  # 加算後に再正規化

    # 2. 減算ベクトルの反復正規化合成
    if sub_sha256s:
        s = norm(get_embedding(sub_sha256s[0]))
        for sha in sub_sha256s[1:]:
            s = norm(s + norm(get_embedding(sha)))
        q = norm(q - s)                          # 減算後に再正規化

    # 3. Qdrant 検索
    return qdrant.search(collection="images", query=q, limit=k)
```

**反復正規化の効果（数値例）:**

```
画像 A の magnitude = 1.0（正規化済み）
画像 B の magnitude = 1.0（正規化済み）

単純加算:      A + B → ‖A + B‖ ≈ 1.4  （√2 方向への magnitude 増大）
反復正規化:    norm(A) + norm(B) → norm(...) = 1.0  （magnitude 固定）
```

---

### モーフ 🌊

**疑似コード:**

```python
MORPH_STEPS = [0.2, 0.4, 0.6, 0.8, 1.0]
IMAGES_PER_STEP = 4

def morph(sha_a: str, sha_b: str) -> list[list[Image]]:
    va = norm(get_embedding(sha_a))
    vb = norm(get_embedding(sha_b))
    results = []

    for t in MORPH_STEPS:
        # 線形補間 → 正規化
        v_interp = norm((1 - t) * va + t * vb)

        # 両端画像を除外して検索
        step_results = qdrant.search(
            collection="images",
            query=v_interp,
            limit=IMAGES_PER_STEP,
            filter=must_not([sha_a, sha_b])
        )
        results.append(step_results)

    return results  # 5 ステップ × 4 枚 = 最大 20 枚
```

**LERP の幾何学的意味:**

```
va と vb が単位球上の 2 点の場合:
  t = 0.0 → va の方向（A の近傍）
  t = 0.5 → 中点方向（A と B の「中間概念」）
  t = 1.0 → vb の方向（B の近傍）

※ LERP は球面補間（SLERP）ではなく線形補間。
  正規化後の実質的な差異は小さいが、LERP のほうが計算が単純。
```

---

### アノマリー ⚡

**疑似コード:**

```python
ANOMALY_TAG_COUNT = 30   # LLM に渡す頻出タグ数
ANOMALY_INJECT_N = 3     # LLM が提案するタグ数

def anomaly(ref_sha256s: list[str], k: int = 20) -> AnomalyResult:
    # 1. WD14 タグ頻度カウント
    tag_freq: dict[str, int] = Counter()
    for sha in ref_sha256s:
        doc = db.get(sha)
        tag_freq.update(doc["wd14_tags"])

    # 2. 上位タグ抽出
    top_tags = [t for t, _ in tag_freq.most_common(ANOMALY_TAG_COUNT)]

    # 3. LLM に稀少タグを提案させる（temperature 0.7）
    prompt = (
        f"Given these dominant tags from an image collection:\n{top_tags}\n\n"
        f"Suggest exactly {ANOMALY_INJECT_N} Danbooru-compatible tags "
        "that would be RARE or UNEXPECTED in this context "
        "but semantically plausible. Return only tags, comma-separated."
    )
    anomaly_tags_str = ollama.generate(prompt, temperature=0.7)
    anomaly_tags = parse_tags(anomaly_tags_str)

    # 4. 元タグ + アノマリータグを結合してテキスト埋め込み
    combined = top_tags + anomaly_tags
    query_text = ", ".join(combined)
    q = norm(ollama.embed(query_text))          # テキスト → ベクトル

    # 5. Qdrant 検索
    images = qdrant.search(collection="images", query=q, limit=k)

    return AnomalyResult(images=images, anomaly_tags=anomaly_tags)
```

**LLM タグ生成の非決定性:**

`temperature=0.7` のため、同じ参照画像でも呼び出しのたびに異なるアノマリータグが提案されます。これは意図的な設計で、繰り返し実行するたびに異なる「驚き」が生まれます。

---

### インバージョン 🪞

詳細は [§7 VLM 3ステージパイプライン](#7-vlm-3ステージパイプラインインバージョン詳解) を参照。

**アルゴリズム概要:**

```python
def inversion(ref_sha256s: list[str]) -> AsyncIterator[SSEEvent]:
    # 参照画像の平均埋め込みをコンテキストとして使用
    vecs = [get_embedding(sha) for sha in ref_sha256s]
    context_vec = norm(mean(vecs))

    # タイル画像生成（VLM の視覚入力）
    tile = create_tile_image([read_bytes(sha) for sha in ref_sha256s])

    # Stage 1: 生成（SSE "think" + "token" イベント）
    stage1_output = yield from vlm_stage1(tile, context_vec)

    # Stage 2: バリデーション
    issues = yield from vlm_stage2(stage1_output)

    # Stage 3: リファインメント（問題あり時のみ）
    if issues:
        final = yield from vlm_stage3(stage1_output, issues)
    else:
        final = stage1_output

    # 最終プロンプトを埋め込み → Qdrant 検索 → SSE "done"
    q = norm(ollama.embed(final.tags + final.prose))
    images = qdrant.search(collection="images", query=q, limit=20)
    yield SSEEvent("done", images=images, inversion_tags=final.tags,
                   inversion_prose=final.prose, ...)
```

---

### ディスカバー 🧭

**疑似コード:**

```python
def discovery(
    target_sha: str,
    positive_pairs: list[tuple[str, str]],  # [(pos_sha, neg_sha), ...]
    k: int = 20
) -> list[Image]:
    target_vec = norm(get_embedding(target_sha))

    context = [
        ContextPair(
            positive=norm(get_embedding(pos)),
            negative=norm(get_embedding(neg))
        )
        for pos, neg in positive_pairs
    ]

    return qdrant.discover(
        collection="images",
        target=target_vec,
        context=context,         # DiscoverQuery の追加制約
        limit=k,
        using="full",
        prefetch=Prefetch(
            query=target_vec[:256],
            limit=k * 5,
            using="mrl256"
        )
    )
```

**DiscoverQuery の最適化目標:**

```
標準検索:  argmax_x  sim(x, target)
DiscoverQuery: argmax_x  sim(x, target)
               subject to: sim(x, pos) > sim(x, neg)  ∀ペア

各ポジティブ/ネガティブペアが「どちら側に引き寄せるか」の
方向圧力として機能する。ペアを追加するほど探索が絞り込まれる。
```

---

### グループ検索 🗂️

**疑似コード:**

```python
def group_search(
    query_text: str,
    group_by: str = "model_name",   # payload フィールド名
    group_size: int = 3,
    max_groups: int = 10
) -> dict[str, list[Image]]:
    # テキストクエリを Ollama で埋め込み
    q = norm(ollama.embed(query_text))

    return qdrant.search_groups(
        collection="images",
        query=q,
        group_by=group_by,           # GroupBy フィールド
        group_size=group_size,       # グループあたりの画像数
        limit=max_groups,            # 最大グループ数
        using="full",
        prefetch=Prefetch(
            query=q[:256],
            limit=max_groups * group_size * 5,
            using="mrl256"
        )
    )
```

**GroupBy の内部動作:**

Qdrant の GroupBy は、通常の ANN 検索後に結果を指定 payload フィールドでグルーピングし、各グループから上位 `group_size` 件を返します。同一グループ内での重複を抑制しつつ、グループ間の多様性を確保します。

---

### ブレンド ⚖️

**疑似コード:**

```python
BLEND_EXCLUDE_THRESHOLD = 0.5   # この重みを超える画像は結果から除外

def blend(
    images_weights: list[tuple[str, float]],  # (sha256, weight)
    k: int = 20
) -> list[Image]:
    # 除外リスト（強く含める指定の画像自身を結果から排除）
    exclude = [sha for sha, w in images_weights if w > BLEND_EXCLUDE_THRESHOLD]

    # 加重線形結合
    q = zeros(768)
    for sha, w in images_weights:
        q += w * norm(get_embedding(sha))
    q = norm(q)

    return qdrant.search(
        collection="images",
        query=q,
        limit=k,
        filter=must_not(exclude)
    )
```

**錬金術との数学的差異:**

```
錬金術（反復正規化）:
  q₀ = norm(a₁)
  q₁ = norm(q₀ + norm(a₂))
  q₂ = norm(q₁ - norm(s₁))
  各ステップで単位球上に写像 → すべての入力が等しい発言権

ブレンド（加重線形結合）:
  q = norm( w₁·norm(a₁) + w₂·norm(a₂) + w₃·norm(a₃) )
  重みの比率がそのまま最終ベクトルの方向に反映
  → 「60% A, 30% B, 10% C」を直接的に表現可能
```

---

### アウトライアー 🌌

#### アンチポード（数学的な対極）

**疑似コード:**

```python
def outlier_antipode(k: int = 20) -> list[Image]:
    # 全画像の埋め込みを取得して重心を計算
    all_vecs = [get_embedding(sha) for sha in get_all_sha256s()]
    centroid = norm(mean(all_vecs))

    # 符号反転 → アンチポードベクトル
    antipode = -centroid     # 全要素に -1 を掛ける

    # 重心に近い画像（「典型的」な画像）を除外して検索
    return qdrant.search(collection="images", query=antipode, limit=k)
```

**アンチポードの意味:**

```
コレクションの重心 c（単位球上）は「典型的な画像」の方向を指す。
-c はその真逆の方向 = コレクションから最も乖離した意味空間の点。
-c に最も近い実在画像 = コレクションの「最大の外れ値」。

注意: -c はコレクション内に実在しない仮想点。
      最近傍探索で最も近い実在画像を取得する。
```

#### 孤立島（密度ベース）

**疑似コード:**

```python
ISOLATION_RADIUS = 2.0   # UMAP 2D 空間での近傍半径
ISOLATION_SAMPLE_K = 20  # 最終的に返す画像数

def outlier_isolated(k: int = ISOLATION_SAMPLE_K) -> list[Image]:
    # UMAP 座標が記録されているすべての画像を取得
    docs = db.get_all_with_umap()

    if not docs:
        # フォールバック: UMAP 未計算ならランダムサンプリング
        return random.sample(get_all_images(), k)

    # 各画像の局所密度を計算（近傍カウント）
    coords = [(doc["umap_x"], doc["umap_y"]) for doc in docs]
    densities = []
    for i, (x, y) in enumerate(coords):
        count = sum(
            1 for j, (x2, y2) in enumerate(coords)
            if i != j and euclidean(x, y, x2, y2) < ISOLATION_RADIUS
        )
        densities.append((docs[i], count))

    # 密度の低い画像順にソート
    densities.sort(key=lambda d: d[1])

    # 最低密度グループからランダムサンプリング
    candidates = [d[0] for d in densities[:k * 3]]
    return random.sample(candidates, min(k, len(candidates)))
```

**UMAP 座標の更新タイミング:**

UMAP は全画像の埋め込みを 2D に次元削減したものです。コレクションへの画像追加時に非同期バッチ処理で更新されます。UMAP 座標が未計算の画像はアウトライアー（孤立島）モードのフォールバックパスへ落ちます。

---

## 5. ブレスト — LLM タグパイプライン

**疑似コード:**

```python
def brainstorm(
    selected_sha256s: list[str],
    extra_tags: list[str] = [],      # アノマリー/インバージョン由来
    lang: Literal["ja", "en"] = "en",
    idea_count: int = 5
) -> AsyncIterator[SSEEvent]:
    # 1. 選択画像の WD14 タグを収集
    all_tags: set[str] = set()
    for sha in selected_sha256s:
        doc = db.get(sha)
        all_tags.update(doc["wd14_tags"][:50])   # 上位 50 タグ

    # 2. アノマリー/インバージョンタグを統合（あれば）
    all_tags.update(extra_tags)

    # 3. LLM プロンプト構築
    lang_directive = "in Japanese" if lang == "ja" else "in English"
    prompt = (
        f"Given these image tags: {', '.join(sorted(all_tags))}\n\n"
        f"Generate {idea_count} creative, visually distinct scene proposals "
        f"{lang_directive}. Each proposal should:\n"
        "- Be 2-3 sentences describing a complete scene\n"
        "- Be visually specific (lighting, mood, composition)\n"
        "- Be distinct from the others\n"
        "Format as a Markdown numbered list."
    )

    # 4. SSE ストリーミング生成
    async for token in ollama.generate_stream(prompt, temperature=0.8):
        yield SSEEvent("token", text=token)

    yield SSEEvent("done")
```

**タグ統合の優先度:**

```
WD14 タグ（選択画像）
  + アノマリータグ（LLM 生成）   ← 存在する場合、探索の文脈を維持
  + インバージョンタグ（VLM 生成）← 存在する場合、反転世界の要素を維持
  = ブレスト語彙セット → LLM が創作アイデアを生成
```

**SSE イベント型（ブレスト）:**

| type | タイミング | フィールド |
|---|---|---|
| `token` | テキストトークン逐次 | `text: str` |
| `done` | 生成完了 | — |
| `error` | エラー発生 | `message: str` |

---

## 6. Qdrant クエリパターン

### 6.1 標準ベクトル検索（MRL 2フェーズ）

```python
# 全モードの基本パターン（セレンディピティ・錬金術・モーフ・ブレンド等）
qdrant.query_points(
    collection_name="images",
    prefetch=[
        Prefetch(
            query=q_mrl256,          # 256 次元プレフィックス
            using="mrl256",
            limit=prefetch_limit,    # k × 5 〜 k × 10
        )
    ],
    query=q_full,                    # 768 次元フルベクトル
    using="full",
    limit=k,
    query_filter=QdrantFilter(...),  # 必要に応じて
    with_payload=True,
)
```

### 6.2 DiscoverQuery（ディスカバーモード）

```python
qdrant.discover_points(
    collection_name="images",
    target=target_vec,               # ターゲット画像のベクトル
    context=[
        ContextExamplePair(
            positive=pos_vec,
            negative=neg_vec,
        )
        for pos_vec, neg_vec in context_pairs
    ],
    limit=k,
    using="full",
    prefetch=Prefetch(
        query=target_vec[:256],
        limit=k * 5,
        using="mrl256",
    ),
)
```

### 6.3 GroupBy（グループ検索モード）

```python
qdrant.query_points_groups(
    collection_name="images",
    prefetch=[
        Prefetch(
            query=q_mrl256,
            using="mrl256",
            limit=prefetch_limit,
        )
    ],
    query=q_full,
    using="full",
    group_by="model_name",           # グループ化フィールド
    limit=max_groups,                # グループ数
    group_size=images_per_group,     # グループあたり画像数
)
```

---

## 7. VLM 3ステージパイプライン（インバージョン詳解）

### Stage 1 — 生成プロンプト（要点）

```
システム指示（要点）:
  "You are a creative AI specializing in semantic image inversion.
   Invert the world along five axes (Visual/Mood/Subject/Style/Narrative).
   DO NOT invert character-defining features (hair, eyes, specific persons).
   The world changes; the character identity remains."

生成物（JSON スキーマ）:
  {
    "narrative": str,           // 300〜500 語の物語
    "inversion_tags": str[],    // 100〜150 Danbooru タグ
    "inversion_prose": str,     // 150〜200 語の自然言語プロンプト
    "negative_tags": str[]      // 20〜40 の除外タグ
  }
```

### Stage 2 — バリデーションプロンプト（要点）

```
入力: Stage 1 の出力全体

検査項目:
  - 内部矛盾（例: "indoor" に反転したのに "tree, sky" タグが残存）
  - 5軸の反転が適切に実行されているか
  - 禁止要素（キャラクター固有特徴）の混入がないか
  - タグと散文の一貫性

出力:
  {
    "issues": str[],    // 問題リスト（空なら Stage 3 スキップ）
    "severity": "none" | "minor" | "major"
  }
```

### Stage 3 — リファインメントプロンプト（要点）

```
条件: issues が空でない場合のみ実行

入力: Stage 1 の出力 + Stage 2 の issues リスト

指示: "Revise the output to address each issue while preserving
      the inverted world concept. Maintain all non-character inversions."

出力: Stage 1 と同じ JSON スキーマ（修正済み）
```

### SSE イベント型（インバージョン）

| type | タイミング | フィールド |
|---|---|---|
| `think` | VLM 拡張思考（対応モデル） | `text: str` |
| `stage` | ステージ遷移 | `stage: 1\|2\|3`, `label: str` |
| `token` | 生成テキストトークン | `text: str` |
| `done` | 全ステージ完了 | `images`, `inversion_tags`, `inversion_prose`, `negative_tags`, `inversion_negative_tags` |
| `error` | エラー発生 | `message: str` |

### パイプライン全体図

```
参照画像（1〜3枚）
    │
    ├─── タイル画像合成（tile_image.py）
    │        複数画像 → 1枚のムードボード
    │
    └─── 平均埋め込みベクトル（コンテキスト用）

    ↓

Stage 1: 生成（VLM 呼び出し）
    VLM ← タイル画像 + 反転指示プロンプト
    出力: narrative + tags(100〜150) + prose(150〜200) + neg_tags
    SSE: think + token イベントをリアルタイム送出

    ↓

Stage 2: バリデーション（VLM 呼び出し）
    VLM ← Stage 1 出力
    出力: issues[]
    SSE: stage イベント（"Validating..."）

    ↓ issues が空なら Stage 3 スキップ

Stage 3: リファインメント（VLM 呼び出し）
    VLM ← Stage 1 出力 + issues
    出力: 修正済み tags + prose
    SSE: token イベント

    ↓

テキスト埋め込み（Ollama）
    tags + prose → 768 次元ベクトル

    ↓

Qdrant 検索（MRL 2フェーズ）
    → 最終一致画像 k 件

    ↓

SSE: done イベント（images + 全プロンプト情報）
```

---

## 8. クリエイティブパイプライン全体像

```
Discovery（インスパイア 9モード）
  ├── 純ベクトル演算: 錬金術 / モーフ / ブレンド
  │     └── ms オーダー、決定論的
  ├── ベクトル + サンプリング: セレンディピティ / アウトライアー
  │     └── ms〜数十ms、確率的
  ├── Qdrant 高度機能: ディスカバー / グループ検索
  │     └── ms〜数十ms
  └── LLM/VLM 統合: アノマリー / インバージョン
        └── 秒〜10秒、非決定論的

  ↓ 発見した画像 + タグ情報

Analysis（自動）
  WD14 タグ収集
  アノマリー / インバージョンタグの統合
  語彙セット構築

  ↓

Ideation（ブレスト）
  LLM が語彙セットから 3〜5 のシーン提案を生成
  SSE ストリーミング
  日本語 / 英語対応

  ↓

Refinement（プロンプト錬成）
  参照画像（最大 6 枚）+ ブレスト提案テキスト
  → WD14 スコア分類（≥0.70 必須 / <0.70 参考）
  → タイル合成 → VLM 生成 → 後処理パイプライン
  スタイル: natural / danbooru / detailed

  ↓

Generation（ComfyUI）
  auto_submit またはワンクリック送信
  生成完了 → sha256 でコレクションに追加
  WD14 タグ付け + 埋め込みベクトル生成 → Qdrant インデックス

  ↓

次の Discovery サイクルへ（コレクション拡充）
```

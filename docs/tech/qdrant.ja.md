# Ranbell Image における Qdrant の使い方

Ranbell Image プロジェクトで使用しているすべての Qdrant 機能を説明します。コレクション設計、名前付きベクトル、ペイロードスキーマ、検索パターン、ジョブステータス管理、高度な機能をカバーします。

Qdrant はここではベクトル検索エンジンにとどまらず、アプリケーション全体の**唯一の真実の源泉（Single Source of Truth）**として機能しています。画像メタデータ・AI 処理ステータス・ユーザーデータ・アプリケーション設定・評価結果のすべてが Qdrant だけに格納されます。リレーショナルデータベースは使用していません。

- **Qdrant バージョン:** 1.18.0
- **クライアント:** Python SDK `qdrant-client` の `AsyncQdrantClient`
- **主要実装:** `backend/app/db/qdrant_client.py`
- **設定:** `backend/app/config.py`

---

## コレクション

3 つのコレクションを管理しています。

| コレクション | 用途 |
|---|---|
| `images` | 主コレクション：埋め込みベクトル・カラーベクトル・リッチなペイロードを持つ全画像ポイント |
| `alignment` | VLM プロンプト適合度評価レコード（評価済み画像 1 枚につき 1 ポイント） |
| `app_config` | 永続化ランタイム設定の単一ドキュメントストア |

すべてのコレクションは起動時にべき等に作成されます。存在しないコレクションは自動作成されます。

---

## 名前付きベクトル

`images` コレクションは 1 ポイントにつき **3 本の名前付きベクトル**を持ちます。各ベクトルは異なる検索次元を担当します。

### `embedding` — セマンティック埋め込み（768 次元、COSINE）

Ollama 経由の `nomic-embed-text` が生成する、各画像の主要なセマンティック表現です。すべてのセマンティック検索の最終段階リランクに使用される完全次元ベクトルです。

- **次元数:** 768（`embed_dim` で設定可能）
- **距離:** COSINE
- **ストレージ:** ディスク上
- **量子化:** INT8 スカラー量子化（`quantile=0.99`、`always_ram=True`）

### `embedding_small` — MRL プリフェッチベクトル（256 次元、COSINE）

`embedding` の Matryoshka Representation Learning（MRL）打ち切りプレフィックスです。MRL の学習により、長い埋め込みの先頭 k 次元はその短い次元での有効な埋め込みとなります。このベクトルはフル埋め込みの代替として低コストで使用できます。

- **次元数:** 256（`embed_dim_small` で設定可能；`embed_dim` 以下である必要があります）
- **距離:** COSINE
- **ストレージ:** ディスク上
- **量子化:** INT8 スカラー量子化
- **2フェーズ検索における役割:** 高速プリフェッチフェーズとして機能し、`n_results × 20` 件の候補を生成した後、フル `embedding` ベクトルでリランクします

```python
# MRL 2フェーズ検索パターン
query_points(
    prefetch=[Prefetch(query=small_vec, using="embedding_small", limit=n * 20)],
    query=full_vec,
    using="embedding",
)
```

このパターンが使用される関数: `search_vector()`、`search_vector_tag_or()`、`search_similar()`、`search_images_grouped()`。

### `color_vector` — 知覚的カラーベクトル（3 次元、EUCLID）

画像のパレットにおける支配色を表す 3 次元 CIE L\*a\*b\* カラーベクトル `[L*, a*, b*]` です。L\*a\*b\* 空間でのユークリッド距離は CIE76 ΔE 知覚色差と等価であり、知覚的に正確なカラー類似度メトリクスとなります。

- **次元数:** 3（固定）
- **距離:** EUCLID
- **ストレージ:** ディスク上
- **用途:** カラーベースの画像検索（色相範囲除外オプション付き）; カラー近接フィルタリング

---

## JSON ペイロード

### `images` コレクション

各画像ポイントは、ファイル識別情報・AI 処理状態・カラーデータ・空間座標・ユーザーアノテーションを含むリッチなペイロードを持ちます。

#### 識別情報・ファイルメタデータ

| フィールド | インデックス種別 | 説明 |
|---|---|---|
| `sha256` | KEYWORD | 画像の一意識別子（ファイル内容の SHA-256 ハッシュ） |
| `name` | KEYWORD | 画像ファイル名 |
| `path` | — | フルファイルシステムパス |
| `mtime` | DATETIME | ファイル更新タイムスタンプ |
| `size` | INTEGER | ファイルサイズ（バイト） |
| `raw_metadata` | — | 完全な EXIF / PNG メタデータ辞書 |

#### AI 処理状態

| フィールド | インデックス種別 | 説明 |
|---|---|---|
| `embedding_status` | KEYWORD | `"pending"` \| `"done"` — 埋め込みパイプラインのキューを制御 |
| `wd14_tags` | KEYWORD | WD14 タガーが予測した Danbooru タグリスト |
| `positive_prompt` | TEXT (WORD) | テキスト-to-画像生成プロンプト；全文検索可能 |
| `params` | — | 完全な生成パラメータ辞書（サンプラー・ステップ数・CFG・シードなど） |
| `model_name` | KEYWORD | `params` から抽出したモデル名 |
| `batch_category` | KEYWORD | `"AI"`（AI 生成）\| `"NR"`（自然/参照画像） |
| `is_reference` | BOOL | 参照ソースディレクトリ由来の画像は `true` |

#### カラーパレット

HSV / L\*a\*b\* 空間での 5 クラスター KMeans で抽出されます。

| フィールド | インデックス種別 | 説明 |
|---|---|---|
| `palette_hex` | KEYWORD | `#RRGGBB` 形式の Hex カラー文字列リスト（クラスターごとに 1 件） |
| `palette_hues` | FLOAT | クラスターごとの HSV 色相角度（0〜360） |
| `avg_saturation` | FLOAT | 全クラスターの HSV 彩度の平均（0〜1） |
| `avg_value` | FLOAT | 全クラスターの HSV 明度の平均（0〜1） |

#### 空間投影

| フィールド | インデックス種別 | 説明 |
|---|---|---|
| `umap_x` | — | 2D UMAP 投影の X 座標 |
| `umap_y` | — | 2D UMAP 投影の Y 座標 |

インデックスなし（存在確認には範囲ベースのフィルタリングを使用）。

#### ユーザーデータ

| フィールド | インデックス種別 | 説明 |
|---|---|---|
| `star_rating` | INTEGER | ユーザーのスター評価（0〜5） |
| `creation_record` | — | 作成方法・元画像参照・インスパイアコンテキスト・生成パラメータを含む辞書 |
| `creation_record.method` | KEYWORD | 作成方法識別子（フィルタリング用にインデックス済み） |

### `alignment` コレクション

評価済み画像 1 枚につき 1 ポイント。ポイント ID は `image_id` から決定論的に生成されます。

| フィールド | インデックス種別 | 説明 |
|---|---|---|
| `image_id` | KEYWORD | 対応画像の `sha256` |
| `status` | KEYWORD | `"pending"` \| `"done"` \| `"skipped"` \| `"error"` |
| `score` | FLOAT | 適合度スコア 0.0〜1.0 |
| `evaluated_at` | DATETIME | 評価タイムスタンプ |
| `summary` | — | テキスト形式の分析サマリー |
| `summary_i18n` | — | 言語コードをキーとした翻訳済みサマリー辞書 |
| `matched_elements` | — | 画像中に確認されたプロンプト要素のリスト |
| `matched_elements_i18n` | — | 確認済み要素の翻訳 |
| `unmatched_elements` | — | 画像中に存在しないプロンプト要素のリスト |
| `unmatched_elements_i18n` | — | 未確認要素の翻訳 |
| `categories` | — | カテゴリ分類 |

### `app_config` コレクション

ランタイム状態を永続化する単一ポイント（ID `"config"`）。アプリケーション再起動をまたいで保持されます。

- ジョブレーンごとの一時停止フラグ
- アクティブな埋め込みモデル名
- アクティブな VLM モデル名
- UMAP 計算状態

---

## 検索操作

すべての検索関数は `backend/app/db/qdrant_client.py` で `query_points()` または `query_batch_points()` を使用して実装されています。

### セマンティック検索バリアント

| メソッド | 説明 |
|---|---|
| `search_vector()` | オプションの単一タグフィルター付きセマンティック検索；MRL 2フェーズ |
| `search_vector_tag_or()` | OR 包含 / NOT 除外タグフィルター付きセマンティック検索；MRL 2フェーズ |
| `search_similar()` | 指定参照画像に類似した画像を検索；`HasIdCondition` でソース自身を除外 |
| `search_by_vector()` | sha256 の明示的な除外リストを受け付ける生ベクトル検索 |
| `search_by_vector_scored()` | 上記と同じだが、下流のスコア範囲フィルタリング用に `(payload, score)` タプルを返す |

### カラー検索

`search_by_color_vector()` は L\*a\*b\* 空間のユークリッド距離で `color_vector` を検索します。`palette_hues` FLOAT インデックス済みフィールドのフィルタリングにより色相範囲の除外（例：「このブルーに近い色を探すが、グリーン系は除く」）をサポートします。

### Discovery API

`discover_images()` は Qdrant の Discovery API（`query_batch_points()` 経由の `DiscoverQuery`）を使用します。呼び出し元はターゲット画像とコンテキストペア（ポジティブ/ネガティブ画像 ID のリスト）を提供します。クエリはターゲットに幾何学的に近く、かつコンテキストペアが表す方向的な対比を尊重する画像を探します — ガイド付き創作探索に有用です。

### GroupBy 検索

`search_images_grouped()` は `query_points_groups()` を使用してペイロードフィールド（例: `model_name`）で結果をグループ化します。各グループには `group_size` 件の代表的なヒットが含まれます。グループ化の前に MRL 2フェーズプリフェッチが適用されます。

### 使用されているフィルター種別

| フィルター | 用途 |
|---|---|
| `FieldCondition(match=MatchValue)` | 単一値の完全一致 |
| `FieldCondition(match=MatchAny)` | リストに対する OR 一致 |
| `FieldCondition(match=MatchText)` | TEXT インデックス済みフィールドへの全文検索 |
| `FieldCondition(range=Range)` | 数値 / 日時範囲；フィールドの存在確認にも使用 |
| `HasIdCondition` | 特定のポイント ID の包含または除外 |
| `IsEmptyCondition` | ペイロードフィールドの欠如または null チェック |
| `FilterSelector` | バルク操作（ベクトル削除・ペイロード削除）を絞り込んだサブセットに適用 |
| `Filter(must=[...])` | AND 結合 |
| `Filter(must_not=[...])` | NOT 結合 |
| `Filter(must=[...], must_not=[...])` | AND + NOT の組み合わせ |

---

## Upsert・削除操作

### 書き込み操作

| メソッド | Qdrant 操作 | 説明 |
|---|---|---|
| `upsert_new()` | `upsert()` | ファイルスキャン時にプレースホルダーポイント（ベクトルなし）を挿入 |
| `set_payload()` | `set_payload()` | 部分ペイロード更新；既存ベクトルは変更なし |
| `set_embedding()` | `update_vectors()` | `embedding` + `embedding_small`（フルベクトルを打ち切り）を格納 |
| `set_color_vector()` | `update_vectors()` | 単一の `color_vector` を格納 |
| `set_color_vectors_batch()` | `update_vectors()` | 複数ポイントのカラーベクトル一括更新 |
| `upsert_alignment()` | `upsert()` | 適合度評価レコードを格納または上書き |

### 削除操作

| メソッド | Qdrant 操作 | 説明 |
|---|---|---|
| `delete_embedding()` | `delete_vectors()` | `embedding` + `embedding_small` を削除；ペイロード保持（ポイントは残存） |
| `delete()` | `delete()` with `PointIdsList` | ポイント全体を削除 |
| `delete_payload_keys()` | `delete_payload()` | 単一ポイントから特定のペイロードフィールドを削除 |
| `delete_payload_keys_batch()` | `delete_payload()` with `PointIdsList` | 複数ポイントから特定のペイロードフィールドを削除 |
| `reset_scope()` | `delete_vectors()` with `FilterSelector` | `embedding_status` 値でフィルタリングしたベクトルを一括リセット（`all` / `done` / `pending`） |
| `delete_all_images()` | `delete()` with empty `Filter` | `images` コレクション全体を消去 |

---

## スクロール・ページネーション

スクロール操作はページネーションから分析・パイプライン入力まで、アプリケーションの読み取りパスを支えています。バッチサイズは通常 1 スクロール呼び出しにつき 1,000 ポイントです。

### 画像一覧・ページネーション

`scroll_images()` はカーソルベースのページネーションを実装します。カーソルは `{start, last_id}` 構造体を Base64 エンコードしたものです。各呼び出しでは `limit + 2` 件を取得してページ境界を検出します。ソート方向は `OrderBy` で制御されます。

`scroll_all()` は完全にフィルタリングされたデータセットを収集します（例：一括ダウンロード用）。タグ・キーワード・モデル・評価・カテゴリ・ID リストのフィルターを適用します。

### パイプライン入力

| メソッド | 用途 |
|---|---|
| `get_all_embeddings()` | UMAP 次元削減のための全 `embedding` ベクトル収集 |
| `scroll_color_lab_points()` | カラーベクトルのバックフィルが必要な画像を収集 |
| `scroll_tags()` | タグカウント用の `positive_prompt` + `wd14_tags` を収集 |
| `find_path_mtime_index()` | インクリメンタル修復スキャン用の `sha256 / path / mtime` トリプルを収集 |
| `find_duplicate_path_sha256s()` | 同一ファイルパスを持つ複数の Qdrant レコードを検出 |

### 可視化・分析

| メソッド | 用途 |
|---|---|
| `scroll_umap_points()` | 2D ギャラリー散布図用の `umap_x / umap_y / palette_hex / name` を取得 |
| `scroll_umap_points_with_tags()` | 同座標に `wd14_tags` を加えたクラスターラベリング用データ |
| `scroll_model_facets()` | モデル名ファセット用の `params` ペイロードを収集 |
| `list_dirs()` | ディレクトリブラウザ UI 用に `path` 値を親ディレクトリでグループ化 |

### 適合度読み取り

| メソッド | 用途 |
|---|---|
| `scroll_alignment_sha256s()` | 評価済みの全 `image_id` 集合を返す |
| `get_alignment_sorted_sha256s()` | `score` DESC でソートした sha256 リストを返す |
| `get_aligned_sha256s()` | 最低スコア閾値でフィルタリングした sha256 リストを返す |

### カウント操作

`count_with_embedding()`・`count_with_color_vector()`・`count_with_color_lab()`・`count_pending_color_extraction()` — すべて `Filter` 付きの `count()` でパイプライン進捗を追跡します。

---

## Qdrant によるジョブ・ステータス管理

Qdrant は AI 処理パイプラインの**ステータス永続化レイヤー**として機能します。別途のジョブキューデータベースは存在せず、パイプラインは Qdrant のペイロードフィールドを参照・更新して残作業を判断します。

### 埋め込みパイプライン

各画像ポイントの `embedding_status` フィールド（KEYWORD インデックス済み）が埋め込みパイプラインを制御します。

1. スキャナーが `embedding_status: "pending"` で新規ポイントを挿入します。
2. パイプラインが保留中の画像を処理し、ベクトル格納後に `embedding_status: "done"` をセットします。
3. `reset_scope()` で `"done"` ポイントを `"pending"` に戻す（または全クリアする）ことで再処理を強制できます。

### 適合度評価パイプライン

`alignment` コレクションは以下のライフサイクルを持つ `status` フィールドを使用します。

```
"pending" → "done" | "skipped" | "error"
```

評価が必要な画像は `status == "pending"` のクエリで特定されます。評価器が最終スコアと分析を同じポイントに書き戻します。

### 永続的なアプリケーション状態

`app_config` コレクションは、コンテナ再起動後も維持される単一の設定ポイントを保持します。レーン一時停止フラグ・アクティブな埋め込みモデル・アクティブな VLM モデル・UMAP 計算状態を格納します。これらの設定へのランタイム変更は即座にこのコレクションに永続化されます。

---

## 高度な機能

### INT8 スカラー量子化

`embedding` と `embedding_small` の両方が起動時に INT8 スカラー量子化されます。

```python
ScalarQuantizationConfig(type=ScalarType.INT8, quantile=0.99, always_ram=True)
```

`always_ram=True` により量子化インデックスをメモリ上に保持して低レイテンシ検索を実現しつつ、フル float32 ベクトルはディスク上に残ります。`quantile=0.99` は上位 1% の値をクリップして外れ値の歪みを防ぎます。この操作はべき等で安全に再適用できます。

### アトミックコレクションマイグレーション

コレクションスキーマを変更する必要がある場合（例：別次元の `embedding_small` の追加、`color_vector` の追加）、データ損失を避けるためにアトミック再作成パターンを使用します。

1. 新しいスキーマで一時コレクションを作成する。
2. 既存ポイントをペイロードと全ベクトルを保持しながら 200 ポイントずつバッチでスクロールする。
3. 各バッチを一時コレクションへ upsert する。
4. 元のコレクションを削除する。
5. 一時コレクションを元の名前にリネームする。
6. 全ペイロードインデックスを再適用する。

2 つのマイグレーションが実装されています: `_migrate_small_dim()`（`embed_dim_small` 設定変更時にトリガー）と `_migrate_color_vector()`（既存コレクションに `color_vector` 名前付きベクトルがない場合にトリガー）。

### 起動シーケンス

`QdrantDBClient.start()` は毎回の起動時に以下のシーケンスを実行します。

1. 指数バックオフで Qdrant が利用可能になるまでポーリング（最大 180 秒）。
2. `images` コレクションの存在確認；なければ完全な名前付きベクトル設定で作成。
3. スキーマのずれを検出して適切なマイグレーションを実行（`_migrate_small_dim` または `_migrate_color_vector`）。
4. `embedding` と `embedding_small` にスカラー量子化を適用（べき等）。
5. 全ペイロードインデックスを作成（べき等な `create_payload_index()` 呼び出し）。
6. `app_config` コレクションがなければ作成。
7. `alignment` コレクションとそのインデックスがなければ作成。
8. MRL 利用可否・カラーベクトル利用可否を含む最終準備完了状態をログ出力。

---

## 設定

```python
# backend/app/config.py
qdrant_url: str = "http://qdrant:6333"
embed_dim: int = 768        # 埋め込みモデルの出力次元数と一致させる必要がある
embed_dim_small: int = 256  # MRL 打ち切り次元数；embed_dim 以下である必要がある
```

Qdrant サービスは `docker-compose.yml` で定義されています。

```yaml
qdrant:
  image: qdrant/qdrant:v1.18.0
  environment:
    QDRANT__TELEMETRY_DISABLED: "true"
```

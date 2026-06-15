# 技術リファレンス: Invoke — 5スピリット AI 画像生成システム

このドキュメントでは、Ranbell Image の **Invoke（召喚）** 機能の実装全体を解説します。ユーザーが「召喚」を実行した瞬間から、5つのスピリットが並列に画像を生成し、アライメントスコアが表示されるまでのすべての処理を段階的に説明します。

設計の動機から始め、データモデル、軸分解アルゴリズム、スピリット定義、ジョブパイプライン、SSE ストリーム、デイリーオラクルと進みます。個々のモジュールを把握したい場合は、該当セクションから直接読み始めても構いません。

---

## 概要: なぜ 5スピリット並列生成なのか

一枚の画像を生成するとき、ユーザーの入力は常に「解釈の余地」を持ちます。同じ「夜の海辺、月明かり」という指示でも、忠実に再現する画像もあれば、月光の代わりに嵐を描くほうが詩的な場合もあります。

Invoke はこの曖昧さを欠点として隠すのではなく、意図的に5つの異なる解釈として展開します:

| スピリット | 漢字 | 英名 | 解釈の方針 |
|---|---|---|---|
| **faithful** | 映 | Mirror | 忠実再現。ユーザー意図から逸脱しない |
| **rebel** | 逆 | Counter | 1軸だけ反転させ「影の解釈」を生む |
| **stranger** | 漂 | Wander | 意味的に近い「招かれざる客」タグを自然に織り込む |
| **lunatic** | 奔 | Surge | 意味的に遠い「野生タグ」を中心に据え、想定外を全力で追求する |
| **oracle** | 瞰 | Vantage | 完全な創作自由。最も衝撃的な解釈を選ぶ |

これら 5つは同じ「軸セット」（後述する10軸分解）を受け取り、それぞれ独自のシステムプロンプトに基づいてプロンプトを生成します。並列実行されるため、5枚のバリエーションが可能な限り短い時間で揃います。

```
ユーザー入力 (テキスト / 絵文字 / スライダー / 色)
    |
    v
POST /api/invoke/summon
    |
    v
run_invoke_axis_decompose  (PROMPT レーン)
    |  ← 10軸に分解
    v
on_axis_done() → vocab_hints + axis_tag_hints 取得
    |
    +--- run_invoke_spirit_compose × 5  (PROMPT レーン、並列)
    |        ↓ 各スピリットが独自のプロンプトを生成
    v
on_spirit_composed() × 5 → 全員揃ったら一括 submit
    |
    +--- run_invoke_image_generate × 5  (GEN レーン、並列)
    |        ↓ ComfyUI でそれぞれ生成
    v
on_image_done() × 5
    |
    v
run_invoke_session_finalize  (EMBEDDING レーン)
    |  ← 全画像に AI pipeline（WD14 / 埋め込み）を適用
    v
run_invoke_alignment_score × 5  (EVAL レーン)
    |  ← 各画像の「忠実度スコア」を算出
    v
on_spirit_done() × 5 → session_complete
    |
    v
SSE ストリーム → フロントエンド
    |
    v
ユーザー: 採用 / リスピン / 精製送り
```

スプーラーのレーン分離（[spooler.ja.md](spooler.ja.md) 参照）が並列実行を実現します。PROMPT レーンと GEN レーンは独立したセマフォを持つため、プロンプト生成と画像生成は互いをブロックしません。

---

## ソースファイル

| ファイル | 責務 |
|---|---|
| `backend/app/api/invoke.py` | REST API ルート (9エンドポイント)、SSE ジェネレーター |
| `backend/app/invoke/session_manager.py` | `InvokeSession`、`SpiritState`、`InvokeSessionManager` |
| `backend/app/invoke/axis_decomposer.py` | ユーザー入力 → 10軸変換、絵文字マッピング、VLM 補完 |
| `backend/app/invoke/spirit_loader.py` | YAML スピリット定義の読み込み・キャッシュ |
| `backend/app/invoke/vocab_bank.py` | Qdrant 意味検索による stranger/lunatic ボキャブラリーヒント |
| `backend/app/invoke/content_guard.py` | VLM 委譲による安全フィルタリング |
| `backend/app/invoke/oracle_scheduler.py` | デイリーオラクル定時実行スケジューラー |
| `backend/app/invoke/spirits/faithful.yaml` | Mirror スピリット定義 |
| `backend/app/invoke/spirits/rebel.yaml` | Counter スピリット定義 |
| `backend/app/invoke/spirits/stranger.yaml` | Wander スピリット定義 |
| `backend/app/invoke/spirits/lunatic.yaml` | Surge スピリット定義 |
| `backend/app/invoke/spirits/oracle.yaml` | Vantage スピリット定義 |
| `backend/app/jobs/runners.py` | 6つの invoke ランナー関数 (L1130–L1608) |
| `backend/app/main.py` | 初期化: セッションマネージャー、スピリットプリロード、オラクルスケジューラー |
| `backend/app/runtime_config.py` | invoke 設定キーのデフォルト値 (L34–L42) |
| `backend/app/api/admin.py` | 管理エンドポイント: WD14 語彙、オラクル管理 (L393–L427) |
| `frontend/src/composables/useInvokeSession.js` | フロントエンド状態管理、API 呼び出し、SSE 購読 |
| `frontend/src/components/InvokePanel.vue` | 召喚 UI パネル |

---

## データモデル

### SpiritState

各スピリットの進行状況を追跡するデータクラスです。

```python
@dataclass
class SpiritState:
    name: str
    status: str = "waiting"   # ステータスマシン参照
    sha256: str | None = None
    prompt_result: dict | None = None
    alignment_score: float | None = None
    job_ids: list[str] = field(default_factory=list)
```

**status フィールドのステートマシン:**

```
  create_session()
       |
       v
  +---------+  on_axis_done() 後   +-----------+
  | waiting | -------------------> | composing |
  +---------+                      +-----+-----+
                                         |
                          run_invoke_spirit_compose 完了
                                         |
                          +--------------+----------+
                          |                         |
                    on_spirit_composed()       on_spirit_error()
                          |                         |
                          v                         v
                    +---------+                 +-------+
                    | composed|                 | error |  ← 終端
                    +---------+                 +-------+
                          |
             全 spirit が composed になったら GEN submit
                          |
                          v
                  +------------+
                  | generating |
                  +-----+------+
                         |
               on_image_done()       on_spirit_error()
                         |                   |
                         v                   v
                   +---------+          +-------+
                   | tagging |          | error |  ← 終端
                   +---------+
                         |
             run_invoke_session_finalize → alignment submit
                         |
                         v
                   +---------+
                   | scoring |
                   +---------+
                         |
               on_spirit_done()
                         |
                         v
                    +------+
                    | done |  ← 終端
                    +------+
```

`error` と `done` が終端状態です。すべての enabled_spirits が `done` または `error` になると `session_complete` イベントが発火します。

---

### InvokeSession

セッション全体を保持するデータクラスです。`create_session()` で生成され、TTL は **3600 秒**です。

```python
@dataclass
class InvokeSession:
    session_id: str                    # UUID4
    user_intent: str                   # ユーザーのテキスト入力（または pro_prompt）
    input_mode: str                    # 'light' | 'pro' | 'daily_oracle'
    workflow_name: str                 # 使用する ComfyUI ワークフロー名
    enabled_spirits: list[str]         # アクティブなスピリット（SPIRIT_ORDER の順序に正規化）
    prompt_mode: str                   # 'danbooru+natural' | 'natural' | 'danbooru'
    locale: str                        # 'en' | 'ja' — モノローグ言語
    person_tags: str                   # e.g. "1girl, solo" — 全プロンプトの先頭に付加
    pro_negative: str                  # Pro モードのユーザー指定ネガティブプロンプト
    db: Any                            # Qdrant DB クライアント
    ollama: Any                        # Ollama クライアント
    comfy: Any                         # ComfyUI クライアント
    spooler: Any                       # JobSpooler 参照
    spirits: dict[str, SpiritState]    # spirit_name → SpiritState
    axes: dict | None                  # 10軸分解結果（on_axis_done() で設定）
    event_queue: asyncio.Queue         # SSE イベントキュー
    created_at: float                  # time.time()
    completed: bool = False            # session_complete 発火済み
    cancelled: bool = False            # cancel_session() 呼出済み
    finalize_submitted: bool = False   # finalize ジョブ submit 済みフラグ（二重投入防止）
```

**enabled_spirits の正規化**: `create_session()` は受け取ったスピリット名を `SPIRIT_ORDER = ["faithful", "rebel", "stranger", "lunatic", "oracle"]` の順序でフィルタします。順序が保証されることで、UI 表示順とジョブ送信順が一致します。

**finalize_submitted フラグ**: `_maybe_submit_finalize()` が複数回呼ばれても finalize ジョブが 1 度しか投入されないことをこのフラグで保証します。スピリットが error になるパスと正常完了するパスの両方から `_maybe_submit_finalize()` が呼ばれるため、このガードが不可欠です。

---

### リクエストモデル

```python
class SummonRequest(BaseModel):
    # Light モード入力
    user_intent: str = ""                  # ユーザーのテキスト記述
    emoji_codes: list[str] = []            # 絵文字シンボル（最大 6 個）
    mood_sliders: dict = {}                # warm_cool, calm_dynamic, dense_sparse, concrete_abstract: -2..2
    color_hex: list[str] = []             # アクセントカラー（最大 6 色）
    # Pro モード入力
    pro_prompt: str = ""                   # 詳細プロンプト（Pro 時に user_intent の代わりに使用）
    pro_negative: str = ""                 # ネガティブプロンプト（Pro 限定）
    pro_person_tags: str = ""              # 人物タグ自由記述（Pro 限定）
    seeds: dict = {}                       # spirit_name → int | null のシード値マップ
    # キャラクター指定（Light / Pro 共通）
    person_gender: str = ""               # '' | 'girl' | 'boy'
    person_count: str = ""                # '' | '1' | '2' | '3+'
    # プロンプト形式
    prompt_mode: str = "danbooru+natural"  # 'danbooru+natural' | 'natural' | 'danbooru'
    # カメラワーク（Light 限定）
    camera_shot: str = ""                  # 'full_body', 'cowboy_shot', 'close_up' 等
    camera_angle: str = ""                 # 'from_above', 'dutch_angle' 等
    # ロケール
    locale: str = "en"                     # 'en' | 'ja' — モノローグ言語
    # 共通
    workflow_name: str = ""
    input_mode: str = "light"
    enabled_spirits: list[str] = ["faithful", "rebel", "stranger", "lunatic", "oracle"]
```

その他のリクエストモデル:

| クラス | フィールド |
|---|---|
| `RespinRequest` | `session_id`, `spirit_name` |
| `AdoptRequest` | `session_id`, `spirit_name` |
| `SendToRefineRequest` | `session_id`, `spirit_name`, `workflow_name=""` |
| `DailyOracleRequest` | `workflow_name=""` |
| `CancelRequest` | `session_id` |
| `EnhancePromptRequest` | `text`, `tag_count=25` |

---

## 5つのスピリット

### スピリット定義ファイル

各スピリットは `backend/app/invoke/spirits/{name}.yaml` として定義されます。`spirit_loader.py` は起動時にすべてをメモリにキャッシュします（`preload_all()` — `main.py` から呼ばれる）。

**YAML スキーマ:**

```yaml
name: faithful
display_name_en: "Mirror"
display_name_ja: "映"
kanji: "映"
needs_vocab_hint: false
system_prompt: |
  You are Mirror. Your role is to faithfully realize the user's intent...
  # JSON 出力スキーマを含む完全なシステムプロンプト
```

**`needs_vocab_hint` フラグ**: `true` の場合、`run_invoke_spirit_compose` はこのスピリットに `vocab_hints`（stranger/lunatic タグ）を渡します。`false` の場合、空のヒントを受け取ります。

### 各スピリットの役割と方針

**faithful（映 / Mirror）** — `needs_vocab_hint: false`

軸の中心線を外れない忠実再現。矛盾する要素（火と氷など）があった場合、単純に羅列せず「魔法的な融合シーン」として合成します。キャラクター軸が非空の場合、`character_detail` と `accessories` に指定された全タグを Danbooru タグに必ず含めます。

**rebel（逆 / Counter）** — `needs_vocab_hint: false`

10軸のうち **1軸だけ** を反転させて「影の解釈」を生みます。反転対象の選択例:

- `bright → dark`（または `dark → bright`）
- `calm → dynamic`（または `dynamic → calm`）
- `warm → cool`（または `cool → warm`）
- `sparse → dense`（または `dense → sparse`）

他の軸はすべて faithful に維持し、scene の一貫性を保ちます。`inverted_axis` フィールドに反転した軸名を記録します。mood や lighting を反転させた場合、表情タグを対応する反対語（smile → melancholic）に変更することが許可されています。

**stranger（漂 / Wander）** — `needs_vocab_hint: true`

`vocab_bank.get_vocab_hints()` が返す `stranger` タグ（軸と意味的に**近い**、Danbooru 頻度 0.04–0.40）を「招かれざる客」として自然に場面に織り込みます。視聴者が違和感を覚えず、むしろ「常にそこにあった」と感じるよう統合することが目標です。使用したゲストタグを `wild_tags_used` に記録します。

**lunatic（奔 / Surge）** — `needs_vocab_hint: true`

`vocab_bank.get_vocab_hints()` が返す `lunatic` タグ（軸と意味的に**遠い**、Danbooru 頻度 0.40–1.0、ユーザーライブラリ未使用）を **中心** に据えます。graceful な統合ではなく、野生タグを増幅（`(wildtag:1.3)` のような重みづけ）し、不可能なシーンを全力で追求します。失敗する出力が出ることも許容します。矛盾要素は解決せず「燃料」として使用します。

**oracle（瞰 / Vantage）** — `needs_vocab_hint: false`

完全な創作自由。ユーザー意図を深く読み取り、最も「視聴者を止める」画像になる解釈を独自に選びます。軸の追加・削除・無視・再構築がすべて許可されます。`character_detail` タグは「アンカー」として必ず含めますが、それ以外はすべて Vantage の判断に委ねられます。

### LLM 出力スキーマ（全スピリット共通）

```json
{
  "spirit": "<スピリット名>",
  "natural_language": "<2-3文の英語映画的描写。画像生成プロンプトに使用>",
  "natural_language_ja": "<同内容の日本語描写。UI 表示専用、画像生成不使用>",
  "danbooru_tags": "<カンマ区切り Danbooru タグ列>",
  "negative_supplement": "<このスピリットが避けたい要素（省略可）>",
  "internal_monologue": "<スピリットの内なる声、1行>",
  "inverted_axis": "<Counter: 反転軸名 | その他: null>",
  "wild_tags_used": ["<Wander・Surge: 使用したゲスト/野生タグのリスト>"]
}
```

**locale による日本語モノローグ**: `locale == "ja"` の場合、`run_invoke_spirit_compose` はシステムプロンプト内の `"internal_monologue": "<...in English...>"` プレースホルダーを `"<このスピリットの内なる声を日本語で1行>"` に正規表現置換します。単純なフレーズ置換は LLM には不十分なため、ユーザーメッセージ末尾にも日本語指定の明示的な reminder を追加します。

---

## 軸分解 (axis_decomposer)

`decompose_axes()` はユーザーの全入力（テキスト、絵文字、スライダー、色、人物設定、カメラワーク）を **10軸の構造化辞書** に変換します。

### 10軸の定義

```python
_ALL_AXES = [
    "subject",          # 主体（人物、物、生き物）
    "character_detail", # 表情・視線・服装・髪型・体型。人物がいない場合は空文字
    "action",           # その瞬間に起きていること（姿勢、視線、ジェスチャー）
    "scene",            # 背景・環境
    "mood",             # 感情的・雰囲気的性質
    "lighting",         # 光源と照明の質
    "composition",      # 構図（カメラワーク含む）
    "style",            # Danbooru スタイルタグのリスト
    "palette",          # 配色
    "accessories",      # 人物が持つ/着けるアイテム。人物がいない場合は空文字
]
```

### 分解の 3ステップ

```
Step 1: スローガン決定 (determine_slogan)
    ↓ ユーザーテキスト → そのまま使用
    ↓ テキストなし → 絵文字意味 + スライダー説明 → VLM で 1-2 文生成
    ↓ 何もなければ空文字

Step 2a: 決定論的事前充填 (pre_fill_axes)
    ↓ 絵文字 → _EMOJI_AXIS_FILL でシーン/照明/ムード/パレット/スタイルに直マッピング
    ↓ スライダー → _SLIDER_AXIS_FILL で palette/mood/composition/style に変換
    ↓ 色コード → palette に "accent colors: #hex, ..." として追記
    ↓ 人物タグ → subject に danbooru_tags を固定値として設定
    ↓ カメラワーク → composition に camera_shot + camera_angle を追記

Step 2b: VLM による空軸の補完 (_build_completion_prompt → ollama.generate_text)
    ↓ 事前充填済みの軸は "LOCKED" として VLM に提示（変更禁止）
    ↓ 空の軸だけ VLM に補完させる
    ↓ character_hints (Qdrant から取得した Danbooru タグ候補) も提示

Step 2c: マージと正規化 (_parse_and_merge)
    ↓ VLM JSON をパース（失敗時は空 dict として処理）
    ↓ 事前充填値で VLM 出力を上書き（prefilled は常に優先）
    ↓ style 軸から種族タグ (_is_species_tag) を除去
    ↓ style が空なら ["anime"] をフォールバック
```

### 絵文字 → 軸マッピング（抜粋）

絵文字は 2 種類の辞書でマッピングされます:

**`_EMOJI_MEANINGS`**: スローガン VLM 生成に使用するテキスト意味（39 絵文字）

```python
"🌸": "cherry blossoms, spring, delicate pink petals, Japanese aesthetics"
"🌊": "ocean waves, sea, flowing water, vast blue"
"⚡": "lightning, electric, dynamic energy, storm"
```

**`_EMOJI_AXIS_FILL`**: 軸への直接マッピング（LLM 不使用、決定論的）

```python
"🌊": {"scene": "ocean, seashore", "lighting": "light shimmering on water"}
"🌙": {"lighting": "moonlight, soft lunar glow", "scene": "night"}
"🔥": {"lighting": "fire glow, warm dramatic light", "palette": "warm reds and orange"}
"🩰": {"style": ["ballet", "elegant"]}   # style は配列で追加
```

複数の絵文字が同じ軸を持つ場合、文字列値はコンマ連結で統合されます。`style` リストは重複なしでマージされます。

### スライダー → 軸マッピング

| スライダー | -2 | -1 | 0 | +1 | +2 |
|---|---|---|---|---|---|
| `warm_cool` | palette: 琥珀金 | palette: 暖色 | — | palette: 冷色ブルー | palette: 氷青白 |
| `calm_dynamic` | mood: 静寂, action: 静止 | mood: 穏やか | — | mood: ダイナミック | mood: 激烈エネルギー, composition: 斜線 |
| `dense_sparse` | composition: 密度高 | — | — | — | composition: 極簡素、広大な余白 |
| `concrete_abstract` | style: ["photorealistic","highly_detailed"] | — | — | — | style: ["impressionistic","abstract_art"] |

中間値（0 や記載なし）は何も設定しません。

### 人物タグ変換テーブル

```python
_PERSON_TAGS = {
    ("girl", "1"):  ("1girl, solo",           "exactly 1 girl"),
    ("girl", "2"):  ("2girls",                "exactly 2 girls"),
    ("girl", "3+"): ("3girls, multiple_girls","3 or more girls"),
    ("girl", ""):   ("1girl",                 "at least 1 girl"),
    ("boy",  "1"):  ("1boy, solo",            "exactly 1 boy"),
    ("boy",  "2"):  ("2boys",                 "exactly 2 boys"),
    ("boy",  "3+"): ("3boys, multiple_boys",  "3 or more boys"),
    ("boy",  ""):   ("1boy",                  "at least 1 boy"),
    ("",     "1"):  ("solo",                  "exactly 1 person"),
    ("",     "2"):  ("2others",               "exactly 2 people"),
    ("",     "3+"): ("multiple_others",       "3 or more people"),
}
```

Danbooru タグ列（左）はセッションの `person_tags` フィールドに格納され、画像生成時にすべてのスピリットのポジティブプロンプトの先頭に付加されます。テキスト説明（右）は VLM 補完プロンプトの `HARD REQUIREMENT` として渡されます。

### カメラワーク注入

`camera_shot` と `camera_angle` は `composition` 軸の **LOCKED** 値として注入されます:

```python
camera_tags = ", ".join(filter(None, [camera_shot, camera_angle]))
# 例: "cowboy_shot, from_above"
existing = prefilled.get("composition", "")
prefilled["composition"] = (existing + ", " + camera_tags).lstrip(", ")
```

VLM はこの値を上書きできません（prefilled は VLM 出力より優先）。

### character_hints の役割

`run_invoke_axis_decompose` は `decompose_axes()` を呼ぶ前に、まず `get_character_danbooru_hints()` でスローガンに意味的に近い Danbooru タグを Qdrant から取得します。これらは VLM 補完プロンプトに `DANBOORU SUGGESTIONS` セクションとして追加されます:

```
DANBOORU SUGGESTIONS — semantically relevant tags for this scene. Choose the most fitting:
  expression: [smile, blush, melancholic, ...]
  hair: [long_hair, twin_tails, ...]
  clothing: [summer_dress, school_uniform, ...]
  pose: [looking_at_viewer, arms_behind_back, ...]
```

これにより VLM の `character_detail` 軸と `danbooru_tags` の品質が向上します。

---

## ボキャブラリーバンク (vocab_bank)

`vocab_bank.py` は 3 種類の Danbooru タグヒントを Qdrant 意味検索で生成するモジュールです。WD14 語彙が事前にインポートされている必要があります（未インポート時は空リストを返してフォールバック）。

| 関数 | 用途 | 意味的距離 | 配信先 |
|---|---|---|---|
| `get_axis_semantic_tags()` | 軸テキストに最も近い Danbooru タグ。全スピリットのプロンプト精度向上 | **非常に近い** | 全スピリット |
| `get_vocab_hints()` | 逸脱・意外性タグ | 中〜遠い | stranger / lunatic のみ |
| `get_character_danbooru_hints()` | 軸分解 VLM 補完用のキャラクター属性タグ | 近い（キャラ属性） | axis_decomposer のみ |

### get_axis_semantic_tags()

```python
async def get_axis_semantic_tags(
    db,
    ollama,
    axes: dict,
    limit: int = 30,
) -> list[str]:
```

全軸テキストを結合してベクター埋め込みし、WD14 vocab から意味的に最も近い Danbooru タグを返します。全スピリットの `run_invoke_spirit_compose` に `SUGGESTED DANBOORU TAGS` として注入され、LLM がユーザー意図に対応するタグを「推測」に頼らず選べるようにします。

**処理フロー:**

```
1. WD14 vocab カウント確認 → 0 件なら [] を返す

2. axes 辞書から "_" プレフィックス以外の全値を収集
   - str 値はそのまま追加
   - list 値（style 等）は展開してスペース区切りで結合

3. ollama.embed(query_text) でベクター生成

4. db.search_wd14_vocab(vec, min_freq=0.01, max_freq=0.80, category=0, limit=limit*3)

5. フィルタリング:
   - _is_species_tag() で種族タグ除外
   - character_detail / accessories 軸に既にあるタグは除外（重複注入防止）

6. スコア降順で上位 limit(30) 件の tag 名リストを返す
```

**重複除外の理由**: `character_detail` と `accessories` の内容はすでに軸として LLM に渡っています。同じタグを SUGGESTED にも含めると指示が冗長になるため除外します。stranger / lunatic にとっては「従う必要のない参考情報」として機能し、スピリットの逸脱方針を妨げません。

### get_vocab_hints()

```python
async def get_vocab_hints(
    db,
    ollama,
    axis_tags: list[str],
    stranger_count: int = 1,
    lunatic_count: int = 2,
) -> dict[str, list[str]]:
```

**処理フロー:**

```
1. WD14 語彙カウント確認 (db.count_wd14_vocab())
   → 0 件: 警告ログを出して {"stranger": [], "lunatic": []} を返す

2. 軸タグを結合してベクター埋め込み生成
   axis_text = " ".join(axis_tags) or "general anime artwork"
   axis_vec = await ollama.embed(axis_text)

3. ユーザーライブラリのタグ頻度スキャン (_get_library_tag_freq)
   → Qdrant images コレクションを全スクロール
   → {tag: count} の頻度辞書を構築

4. stranger タグ選択
   → db.search_wd14_vocab(axis_vec, min_freq=0.04, max_freq=0.40, limit=40)
   → 軸タグ自身・種族タグを除外
   → ユーザーライブラリでの出現回数が 3 に最も近いものを優先
     (abs(lib_count - 3) が小さいほど高スコア = 適度な馴染みがある)
   → 上位 stranger_count 件を返す

5. lunatic タグ選択
   → db.search_wd14_vocab(axis_vec, min_freq=0.40, max_freq=1.0, limit=200)
   → 軸タグ除外・ユーザーライブラリで 2 件以下・種族タグ除外
   → 意味的距離が最大（スコアが最小）のものを優先
     (score 昇順ソート = 軸から最も遠い = 最も「lunatic」)
   → 上位 lunatic_count 件を返す
```

**Danbooru 頻度フィルタの意図**: WD14 語彙の各タグには `wd14_freq`（Danbooru 全体での出現頻度 0.0–1.0）が付いています。

- **Stranger**: 0.04–0.40 — ありふれすぎず希少すぎない「中程度タグ」。場面に自然に馴染む
- **Lunatic**: 0.40–1.0 — 高頻度タグ（多くの絵に使える汎用性）。ただし軸とは意味的に遠い

### 種族タグフィルタ (_is_species_tag)

`_is_species_tag()` は `dragon_girl`、`fox_boy`、`kemonomimi_mode` 等の種族タグを除外します。ユーザーが明示的に種族を指定していないのに WD14 の語彙ヒントが自動注入すると、スピリット全員が意図せず「獣耳キャラクター」になってしまうためです。ユーザーが自分のテキストで種族を指定した場合は axis_decomposer を通じて軸に入るため、そちらはブロックされません。

### get_character_danbooru_hints()

スローガンに意味的に近い Danbooru タグを 6カテゴリに分類して返します:

| カテゴリ | 分類ルール（抜粋） |
|---|---|
| `expression` | `_EXPRESSION_EXACT` セット or `*_smile`、`*_eyes` サフィックス |
| `hair` | `_hair`、`_ponytail`、`_bun`、`_bangs`、`twintails` サフィックス等 |
| `clothing` | `_CLOTHING_WORDS` セット or それらを含む複合語 |
| `pose` | `_POSE_EXACT` セット or `looking_*`、`from_*`、`arms_*` プレフィックス |
| `accessories` | `_ACCESSORY_EXACT` セット |
| `scene` | `*_sky`、`*_forest`、`scenery`、`outdoors`、`indoors` 等 |

各カテゴリ最大 4 件。Qdrant 検索は `min_freq=0.03, max_freq=0.75, limit=100`。種族タグは除外。

### get_recent_adopted_tags()

デイリーオラクルが「ユーザーの好みのカウンターポイント」を生成するために使用します。過去 N 日間に `adopted_at_genesis=True` でマークされた画像の `wd14_tags` 頻度を集計します。

---

## セッションオーケストレーション (session_manager)

`InvokeSessionManager` はセッションのライフサイクル全体を管理します。スプーラーのジョブ完了コールバックを受けてセッション状態を更新し、SSE イベントをキューに投入します。

### セッション作成と TTL

```python
def create_session(self, user_intent, input_mode, workflow_name,
                   enabled_spirits, ...) -> InvokeSession:
    session_id = str(uuid.uuid4())
    enabled = [s for s in SPIRIT_ORDER if s in enabled_spirits] or SPIRIT_ORDER
    session = InvokeSession(session_id=session_id, ...)
    self._sessions[session_id] = session
    return session

def get_session(self, session_id) -> InvokeSession | None:
    self._evict_expired()   # 取得のたびに TTL チェック
    return self._sessions.get(session_id)
```

`_evict_expired()` は `time.time() - SESSION_TTL(3600s)` より古いセッションをすべて削除します。TTL チェックは専用の定期タスクではなく、`get_session()` 呼び出し時にオンデマンドで実行されます。

### emit() — SSE イベントキューへの投入

```python
async def emit(self, session: InvokeSession, event_type: str, data: dict) -> None:
    await session.event_queue.put({"type": event_type, **data})
```

`_sse_generator()` がこのキューを消費して SSE フレームをクライアントに送信します。キューは `asyncio.Queue`（無制限）のため、クライアントが遅れても emit はブロックしません。

### コールバックチェーン

```
on_axis_done(session_id, axes)
    ├─ session.axes = axes
    ├─ emit("axis_done", {"axes": axes})
    ├─ vocab_hints 取得 (get_vocab_hints) — stranger/lunatic 用
    ├─ axis_tag_hints 取得 (get_axis_semantic_tags) — 全スピリット用
    └─ 各スピリットに run_invoke_spirit_compose を submit (PROMPT レーン)
         (vocab_hints + axis_tag_hints を渡す)

on_spirit_composed(session_id, spirit_name, prompt_result)
    ├─ spirit.prompt_result = prompt_result
    ├─ spirit.status = "composed"
    ├─ emit("spirit_composed", {monologue, natural_language, natural_language_ja})
    └─ 全スピリットが "composing" でなければ → 全 "composed" スピリットに
       run_invoke_image_generate を submit (GEN レーン)

on_image_done(session_id, spirit_name, sha256)
    ├─ spirit.sha256 = sha256
    ├─ spirit.status = "tagging"
    ├─ genesis ペイロード書き込み (db.set_genesis_payload)
    ├─ emit("image_ready", {spirit, sha256})
    └─ _maybe_submit_finalize() — 全スピリットが generating 以降なら finalize を 1 回 submit

on_spirit_done(session_id, spirit_name, alignment_score)
    ├─ spirit.alignment_score = alignment_score
    ├─ spirit.status = "done"
    ├─ emit("spirit_done", {spirit, sha256, alignment_score})
    └─ 全スピリットが done/error なら:
       ├─ session.completed = True
       ├─ emit("session_complete", {session_id})
       ├─ _update_summon_stats() (月別集計、lunatic カウント)
       └─ event_queue.put(None) ← SSE ストリーム終了センチネル
```

### genesis ペイロード

画像生成完了時（`on_image_done`）と採用時（`adopt_spirit`）に Qdrant ペイロードに書き込まれるメタデータです:

```python
{
    "spirit": spirit_name,              # "faithful" 等
    "session_id": session.session_id,
    "original_intent": session.user_intent,
    "input_mode": session.input_mode,   # "light" | "pro" | "daily_oracle"
    "axes_snapshot": {...},             # プレフィックス "_" なしの全軸
    "siblings": [sha256, ...],          # 採用時: 同セッション他スピリットの sha256
    "adopted_at_genesis": False,        # 採用時: True
    "alignment_at_genesis": None,       # 採用時: スコア値
    "wild_tags": [...],                 # Wander/Surge が使用したゲスト/野生タグ
    "respin_count": 0,                  # リスピン回数
    "workflow_preset": workflow_name,
    "daily_oracle_date": None,          # デイリーオラクル時のみ日付文字列
}
```

### キャンセルとリスピン

**cancel_session()**: すべての `spirit.job_ids` を `spooler.cancel()` し、進行中スピリットを `error` にマーク、`session_complete` を発火して `event_queue` に `None` センチネルを投入します。

**respin（リスピン）**: API レイヤー (`POST /api/invoke/respin`) が直接処理します。対象スピリットをリセット（`status="composing"`, `sha256=None`, `alignment_score=None`）し、`session.axes` から `vocab_hints` と `axis_tag_hints` を再取得して `run_invoke_spirit_compose` を新たに submit します。旧 sha256 があれば `genesis.respin_count` をインクリメントします。

---

## ジョブパイプライン

invoke セッションは最大 **6種類のジョブ** を生成します。

### ジョブ一覧

| ジョブ関数 | レーン | 入力 | 出力 |
|---|---|---|---|
| `run_invoke_axis_decompose` | PROMPT | user_intent, emoji, sliders, color, person, camera | 10軸辞書 |
| `run_invoke_spirit_compose` × N | PROMPT | session_id, spirit_name, axes, vocab_hints, axis_tag_hints, locale | prompt_result 辞書 |
| `run_invoke_image_generate` × N | GEN | session_id, spirit_name, prompt_result, workflow | sha256 |
| `run_invoke_session_finalize` | EMBEDDING | session_id, spirit_sha256s | 処理件数 |
| `run_invoke_alignment_score` × N | EVAL | sha256, session_id, spirit_name | alignment_score |
| `run_invoke_daily_oracle` | SYNC (優先度 -10) | daily_oracle_date, workflow, topic | session_id |

### run_invoke_axis_decompose (PROMPT レーン)

```python
axes = await decompose_axes(
    ollama, user_intent, emoji_codes, mood_sliders, color_hex,
    person_gender, person_count, camera_shot, camera_angle,
    character_hints=character_hints,
)
axes['_user_intent'] = user_intent   # スピリット compose 時の参照用
await session_manager.on_axis_done(session_id, axes)
```

`character_hints` は `get_character_danbooru_hints()` で事前取得します（VLM 埋め込み + Qdrant 検索）。

### run_invoke_spirit_compose (PROMPT レーン)

各スピリットに対して独立した PROMPT ジョブが submit されます。

**プロンプト組み立て:**

```
[システムプロンプト（spirit YAML より）]

---

slogan: {axes["_slogan"]}
user_intent: {axes["_user_intent"]}
axes:
  subject: ...
  character_detail: ...
  action: ...
  scene: ...
  mood: ...
  lighting: ...
  composition: ...
  style: [...]
  palette: ...
  accessories: ...
[stranger の場合] guest_tags: [stranger_tag1, ...]
[lunatic の場合]  wild_tags:  [wild_tag1, wild_tag2]
[axis_tag_hints あり] SUGGESTED DANBOORU TAGS (semantically close to the axes —
                      incorporate as many as appropriate): [tag1, tag2, ...]
Your danbooru_tags MUST cover all axes: subject+action, scene+environment, ...
[locale=="ja" の場合] IMPORTANT: Write the "internal_monologue" value in Japanese...
```

LLM 出力を JSON パースし、`check_spirit_output()` でコンテンツガードを通過したら `on_spirit_composed()` を呼びます。

### run_invoke_image_generate (GEN レーン)

**prompt_mode による組み立て:**

```python
if prompt_mode == "natural":
    body_str = nl_usable or db_tags           # 自然言語優先
elif prompt_mode == "danbooru":
    body_str = db_tags                        # タグのみ
else:  # "danbooru+natural" (デフォルト)
    body_str = nl_usable + "\n" + db_tags     # 両方結合

positive = (person_tags + "\n" + body_str).strip()
negative = ", ".join(filter(None, [pro_negative, spirit_negative]))
```

`nl_usable` は `natural_language` が 30 文字未満の場合は空文字として扱います（スピリットのフォールバック出力が断片的なため）。

**ComfyUI 実行フロー:**

```python
wf = comfy.load_workflow(workflow_name)
patched = comfy.patch_workflow(wf, positive, negative, "", "", 1, seed=seed)
prompt_id = await comfy.queue_prompt(patched)

# キャンセルハンドラー登録
cancel.on_cancel(lambda: asyncio.create_task(_cancel_comfy()))

# 進捗ストリーミング
async for event in comfy.stream_progress(prompt_id):
    if event["type"] == "comfy_progress":
        reporter.update(v / m, f"Step {v}/{m}")
    elif event["type"] == "comfy_output":
        img_bytes = await comfy.fetch_image(...)
        sha256 = await _save_and_register_invoke_image(img_bytes, ...)
```

画像は `{generated_images_dir}/invoke/invoke_{timestamp}_{sha256[:8]}.png` に保存されます（スキャナーの自動パイプライン対象外ディレクトリ）。

### run_invoke_session_finalize (EMBEDDING レーン)

全スピリットが generating フェーズを脱した後、`_maybe_submit_finalize()` が 1 回だけ発火します。

```python
# run_ai_pipeline はべき等（処理済み画像はスキップ）
await run_ai_pipeline(db, ollama, sha256s, pause_checkpoint=cancel.pause_checkpoint)

# 各スピリットの alignment ジョブを EVAL レーンに submit
for spirit_name, sha256 in spirit_sha256s.items():
    spirit.status = "scoring"
    spooler.submit(JobLane.EVALUATION, f"invoke.align/{spirit_name[:3]}", ...)
```

**run_ai_pipeline がべき等な理由**: 埋め込みや WD14 タグが既に計算済みの画像はスキップされます。finalize が重複実行された場合でも再処理は発生しません（ただし `finalize_submitted` フラグで二重投入自体を防止しています）。

### run_invoke_alignment_score (EVAL レーン)

```python
evaluator = AlignmentEvaluator(db, ollama)
result = await evaluator.evaluate_one(sha256)
score = result.score if result.status == "done" else None
await session_manager.on_spirit_done(session_id, spirit_name, score)
```

スコアは 0.0–1.0 の float で、`invoke_gold_frame_threshold`（デフォルト: 0.85）以上の場合はゴールドフレームで表示されます。

---

## SSE ストリーム

### ストリームのライフサイクル

クライアントは `GET /api/invoke/stream/{session_id}` に接続します。このエンドポイントは `session.event_queue` から項目を消費する `StreamingResponse` を返します。

```python
async def _sse_generator(event_queue: asyncio.Queue, queues: dict, session_id: str):
    try:
        while True:
            item = await event_queue.get()
            if item is None:              # センチネル: セッション終了
                break
            yield f"data: {json.dumps(item)}\n\n"
        yield 'data: {"type": "eof"}\n\n'
    finally:
        queues.pop(session_id, None)      # セッションキューをクリーンアップ
```

`None` センチネルは `on_spirit_done()` または `cancel_session()` が全スピリット完了時に `event_queue.put(None)` を呼ぶことで投入されます。

### イベント種別

| イベント type | 発火タイミング | データフィールド |
|---|---|---|
| `axis_done` | 軸分解完了 | `axes: {10軸辞書}` |
| `spirit_composed` | スピリット compose 完了 | `spirit`, `monologue`, `natural_language`, `natural_language_ja` |
| `image_ready` | 画像生成完了 | `spirit`, `sha256` |
| `spirit_done` | アライメントスコア算出完了 | `spirit`, `sha256`, `alignment_score` |
| `spirit_error` | スピリット処理失敗 | `spirit`, `error` |
| `session_complete` | 全スピリット完了（done/error） | `session_id` |
| `session_cancelled` | キャンセル完了 | `session_id` |
| `eof` | センチネル後の終端フレーム | なし |

### フロントエンドの SSE 購読

`useInvokeSession.js` が EventSource を管理します:

```javascript
const es = new EventSource(`/api/invoke/stream/${sessionId}`)

es.onmessage = (e) => {
  const event = JSON.parse(e.data)
  switch (event.type) {
    case "axis_done":        invokeAxes.value = event.axes; break
    case "spirit_composed":  // モノローグ・自然言語を spirit に設定
    case "image_ready":      // sha256 を spirit に設定
    case "spirit_done":      // alignment_score を spirit に設定
    case "spirit_error":     // エラー状態を spirit に設定
    case "session_complete":
    case "session_cancelled":
      invokeLoading.value = false
      es.close()
      break
    case "eof":
      es.close()
      break
  }
}
```

`spirit_composed` の `natural_language_ja`（日本語シーン描写）は `locale == "ja"` のときモノローグと並べて UI に表示されます。画像生成には使用されません。

---

## コンテンツガード (content_guard)

`check_spirit_output()` は `run_invoke_spirit_compose` が LLM 出力を受け取った後、画像生成送信前にコンテンツ安全チェックを行います。判定はローカル VLM（Ollama）に委譲し、最小限の保護のみを行う設計です。ブロックされたスピリットは `spirit_error` として処理され、セッションの残りは継続します。

---

## デイリーオラクル

デイリーオラクルは設定された時刻に自動的に全スピリットの画像 5 枚を生成し、その日の「占い」として保存する機能です。

### oracle_scheduler.py

```python
async def run_oracle_scheduler(app) -> None:
    while True:
        await asyncio.sleep(30)   # 30 秒ポーリング（60 秒区切りの目標時刻を確実に捉えるため）
        
        cfg = await get_runtime_config(app.state.db)
        if not cfg.get("invoke_daily_oracle_enabled", False):
            continue
        
        tz = _oracle_tz(cfg)
        h, m = _oracle_hm(cfg)
        now = datetime.now(tz)
        
        if now.hour != h or now.minute != m:
            continue      # 目標時刻でなければスキップ
        
        # ディスク空き容量チェック
        free_gb = shutil.disk_usage(generated_images_dir).free / 1024**3
        if free_gb < cfg.get("invoke_daily_oracle_min_free_gb", 5.0):
            await asyncio.sleep(60)   # 今分はスキップ
            continue
        
        # 本日分が既に存在するかチェック
        today = _oracle_date_str(cfg)
        if await db.get_daily_oracle(today):
            await asyncio.sleep(60)
            continue
        
        # SYNC レーンに低優先度 (-10) で submit
        spooler.submit(JobLane.SYNC, "invoke.daily_oracle", run_invoke_daily_oracle,
                       priority=-10, ...)
        await asyncio.sleep(60)   # 同分内の二重投入を防止
```

**30 秒ポーリングを選ぶ理由**: 60 秒スリープでは目標時刻（HH:MM:00）を高確率で飛び越えてしまうため、30 秒で確実に 1 分以内に発火します。

**二重投入防止**: `spooler.submit()` 後に 60 秒スリープすることで、ループの次回ティックが同じ分内に来ても投入されません。既存レコードチェック（`db.get_daily_oracle(today)`）も二重保護として機能します。

### run_invoke_daily_oracle (SYNC レーン)

```python
# トピックなし → 最近の採用タグをカウンターポイントとして使用
if not topic:
    recent = await get_recent_adopted_tags(db, days=7)
    if recent:
        top_tags = [t for t, _ in sorted(recent.items(), key=lambda x: -x[1])[:5]]
        context_hint = (
            f"The user has recently gravitated toward: {top_tags_str}. "
            f"Today, offer a striking counterpoint to this established pattern..."
        )

axes = await decompose_axes(ollama, user_intent=topic, context_hint=context_hint)
axes["_daily_oracle_date"] = daily_oracle_date

# 全スピリット有効で daily_oracle モードのセッションを作成
session = session_manager.create_session(
    user_intent="[daily oracle]",
    input_mode="daily_oracle",
    enabled_spirits=SPIRIT_ORDER,
    ...
)
await session_manager.on_axis_done(session.session_id, axes)

# セッション完了まで待機（センチネルが来るまでブロック）
await session.event_queue.get()
```

**context_hint の仕組み**: ユーザーが過去 7 日間に採用した画像の上位 5 タグを検出し、「このパターンの対極」を今日のテーマとして VLM に提案させます。これにより毎日異なる画像が生成されます。

**`_daily_oracle_date` 軸**: `_` プレフィックス付きの非公開軸。`to_dict()` では `startswith("_")` でフィルタされず、`genesis ペイロード` の `daily_oracle_date` フィールドに転記されます（`_build_genesis` 参照）。

---

## 設定リファレンス

`backend/app/runtime_config.py` の `_defaults` から invoke 関連の設定キーを抜粋:

| キー | デフォルト | 説明 |
|---|---|---|
| `invoke_gold_frame_threshold` | `0.85` | alignment_score がこの値以上でゴールドフレーム表示 |
| `invoke_show_monologue` | `True` | スピリットのモノローグを UI に表示するか |
| `invoke_daily_oracle_enabled` | `False` | デイリーオラクル自動生成の有効/無効 |
| `invoke_daily_oracle_workflow` | `""` | オラクル使用ワークフロー名（空は無効と同義） |
| `invoke_daily_oracle_retain_days` | `7` | オラクル画像の保持日数 |
| `invoke_daily_oracle_time` | `"00:00"` | 実行時刻 `HH:MM` 形式 |
| `invoke_daily_oracle_timezone` | `"UTC"` | 実行タイムゾーン（`ZoneInfo` 互換名） |
| `invoke_daily_oracle_topic` | `""` | 毎日の固定テーマ（空は自動カウンターポイント） |
| `invoke_daily_oracle_min_free_gb` | `5.0` | 実行に必要な最低ディスク空き容量 (GB) |

---

## REST API リファレンス

すべてのエンドポイントは `/api/invoke` 以下にあります（`backend/app/api/invoke.py`）。

| メソッド | パス | 説明 |
|---|---|---|
| `POST` | `/api/invoke/summon` | セッションを作成して召喚を開始 |
| `POST` | `/api/invoke/respin` | 特定スピリットを再実行 |
| `POST` | `/api/invoke/adopt` | スピリット画像を採用（永続化） |
| `POST` | `/api/invoke/send-to-refine` | 画像をリファインパイプラインに送る |
| `POST` | `/api/invoke/cancel` | セッションをキャンセル |
| `GET`  | `/api/invoke/stream/{session_id}` | SSE ストリームに接続 |
| `GET`  | `/api/invoke/daily` | 今日のデイリーオラクル画像取得 |
| `POST` | `/api/invoke/daily-oracle` | デイリーオラクルを手動トリガー |
| `GET`  | `/api/invoke/stats` | 召喚統計情報取得 |
| `POST` | `/api/invoke/enhance-prompt` | テキストを Danbooru タグ + 自然言語に変換 |
| `GET`  | `/api/invoke/session/{session_id}` | セッション現在状態取得 |

### POST /api/invoke/summon

**リクエスト:** `SummonRequest`（詳細はデータモデル参照）

**レスポンス:**
```json
{"session_id": "uuid4", "job_id": "prompt-000042"}
```

**処理内容:**
1. Pro モードで `pro_prompt` があれば `user_intent` として使用
2. `_resolve_person()` で人物タグを決定（Pro モード + `pro_person_tags` あればそちらを優先）
3. `mgr.create_session()` でセッション生成
4. `run_invoke_axis_decompose` を PROMPT レーンに submit
5. セッションのイベントキューを `app.state.invoke_event_queues` に登録

### GET /api/invoke/stream/{session_id}

**レスポンス:** `text/event-stream` (SSE)

セッションのイベントキューが見つからない場合、セッションマネージャーから直接キューを復元して接続します（ページリロード後の再接続に対応）。ヘッダーに `X-Accel-Buffering: no` を付与して Nginx のバッファリングを無効化します。

### POST /api/invoke/send-to-refine

**レスポンス:**
```json
{
  "positive_prompt": "...",
  "negative_prompt": "...",
  "sha256": "...",
  "workflow_name": "..."
}
```

`prompt_mode` に応じて `natural_language`、`danbooru_tags`、または両方を結合してポジティブプロンプトを組み立てます。

### POST /api/invoke/enhance-prompt

**処理内容:**
1. `body.text` をベクター埋め込み（Ollama）
2. `db.search_wd14_vocab(vec, min_freq=0.005, max_freq=1.0, limit=tag_count*2)` で候補タグ取得
3. 種族タグを除外
4. LLM に「候補タグから適切なものを選び、自然言語描写を書く」よう指示
5. LLM 出力の tags からも種族タグを除外（二重防御）

**レスポンス:**
```json
{
  "tags": "1girl, long_hair, ...",
  "natural_language": "A girl with long flowing hair...",
  "vocab_hits": [{"name": "1girl", "score": 0.92}, ...]
}
```

### GET /api/invoke/daily

**レスポンス（機能有効・画像あり）:**
```json
{
  "date": "2026-06-16",
  "enabled": true,
  "images": {
    "faithful": {画像ペイロード},
    "rebel": {画像ペイロード},
    ...
  },
  "spirit_order": ["faithful", "rebel", "stranger", "lunatic", "oracle"],
  "next_run_at": "2026-06-17T00:00:00+09:00"
}
```

---

## フロントエンド統合

### useInvokeSession.js

`frontend/src/composables/useInvokeSession.js` がすべての invoke 状態とロジックを管理します。

**エクスポートされる定数:**

```javascript
SPIRIT_NAMES = ["faithful", "rebel", "stranger", "lunatic", "oracle"]

SPIRIT_META = {
  faithful: { kanji: "映", nameEn: "Mirror", color: "#...", border: "..." },
  rebel:    { kanji: "逆", nameEn: "Counter", ... },
  stranger: { kanji: "漂", nameEn: "Wander",  ... },
  lunatic:  { kanji: "奔", nameEn: "Surge",   ... },
  oracle:   { kanji: "瞰", nameEn: "Vantage", ... },
}

EMOJI_PALETTE   // 39 絵文字の選択肢
```

**主要 ref（状態）:**

```javascript
invokeOpen           // パネル表示
invokeInputMode      // "light" | "pro"
invokeLoading        // 召喚中フラグ
invokeSessionId      // 現在のセッション ID
invokeSpirits        // { faithful: { status, sha256, monologue, ... }, ... }
invokeAxes           // 10軸の現在値
invokeDailyOracle    // デイリーオラクル画像
invokeOracleNextRun  // 次回実行 ISO datetime
invokeStats          // 召喚統計

// Light モード専用
invokeEmojis         // 選択済み絵文字（最大 6）
invokeText           // テキスト入力
invokeColors         // 選択済み色コード（最大 6）
invokeMoodSliders    // {warm_cool, calm_dynamic, dense_sparse, concrete_abstract}
invokePersonGender   // "girl" | "boy" | ""
invokePersonCount    // "1" | "2" | "3+" | ""
invokePromptMode     // "danbooru+natural" | "natural" | "danbooru"
invokeCameraShot     // カメラショット
invokeCameraAngle    // カメラアングル
invokeEnabledSpirits // 有効なスピリット名の Set

// Pro モード専用
invokeProTopic, invokeProNegative, invokeProPersonTags
invokeProWorkflow, invokeProSeeds
```

**主要関数:**

```javascript
summon(token, locale)          // POST /summon → SSE 接続
cancel(token)                  // POST /cancel
respin(spiritName, token)      // POST /respin
adopt(spiritName, token)       // POST /adopt
sendToRefine(spiritName, token) // POST /send-to-refine
fetchDaily()                   // GET /daily
fetchStats()                   // GET /stats
enhancePrompt(token)           // POST /enhance-prompt
toggleEmoji(emoji)             // 絵文字の追加/除去（最大 6）
toggleSpirit(name)             // スピリットの有効/無効切り替え
getSpiritFrame(name, score, threshold)  // フレームレアリティを返す
```

**`getSpiritFrame()` のフレームレアリティ:**

alignment_score と `invoke_gold_frame_threshold` を比較してフレームスタイルを決定します。スコアがしきい値以上でゴールドフレーム、未満で通常フレームが返ります。

### InvokePanel.vue

`frontend/src/components/InvokePanel.vue` が召喚 UI の全体を担います。主要 UI 要素:

| セクション | 機能 |
|---|---|
| モード切替 | Light / Pro タブ |
| 絵文字パレット | 39 絵文字から最大 6 個選択 |
| テキスト入力 | ユーザー意図の自由記述 |
| 色ピッカー | 最大 6 色のアクセントカラー |
| ムードスライダー | warm_cool, calm_dynamic, dense_sparse, concrete_abstract (-2..2) |
| 人物設定 | 性別ドロップダウン × 人数ドロップダウン |
| カメラワーク | ショット × アングルドロップダウン |
| プロンプトモード | danbooru+natural / natural / danbooru 選択 |
| ワークフロー選択 | `GET /api/comfy/workflows` から読み込み |
| スピリット切替 | 各スピリットの有効/無効トグル |
| プロンプト強化 | POST /enhance-prompt 経由で Danbooru タグ候補を提示 |
| 軸表示 | `axis_done` イベント受信後に 10 軸をリアルタイム表示 |
| スピリットカード | 状態・モノローグ・自然言語・画像・スコアをリアルタイム表示 |
| アクションボタン | 採用 / リスピン / 精製送り（各スピリットごと） |
| デイリーオラクル | 今日のオラクル画像 + 次回実行カウントダウン |

---

## 管理 API

管理エンドポイントは `/api/admin/invoke/` 以下にあります（`backend/app/api/admin.py`）。

### GET /api/admin/invoke/vocab-status

WD14 語彙が Qdrant にインポート済みかを返します。

```json
{"imported": true, "tag_count": 12456}
```

### POST /api/admin/invoke/import-wd14-vocab

WD14 モデルディレクトリの `selected_tags.csv` を Qdrant の `wd14_vocab` コレクションにインポートします。

**処理 (SYNC レーン):**
1. `{wd14_model_dir}/selected_tags.csv` を読み込む
2. `category == 0`（General）のタグのみ抽出
3. 各タグを Ollama でベクター埋め込み
4. `wd14_freq`（Danbooru 頻度）と共に Qdrant にアップサート
5. 完了後 `invalidate_vocab_cache()` を呼んでモジュールキャッシュをリセット

```json
{"status": "queued", "job_id": "sync-000001"}
```

### DELETE /api/admin/invoke/daily-oracle

指定日付のデイリーオラクルレコードを削除します。

**クエリパラメーター:** `?date=2026-06-16`（省略時は設定タイムゾーンでの本日）

```json
{"deleted": 5, "date": "2026-06-16"}
```

---

## 起動と初期化

`main.py` の FastAPI ライフスパンで以下が実行されます:

```python
# 1. セッションマネージャーを app.state に保持
app.state.invoke_session_manager = InvokeSessionManager()
app.state.invoke_event_queues = {}   # session_id -> asyncio.Queue

# 2. 起動時にスピリット YAML を全件プリロード（キャッシュ）
from .invoke.spirit_loader import preload_all as _preload_spirits
_preload_spirits()

# 3. デイリーオラクルスケジューラーをバックグラウンドタスクとして起動
from .invoke.oracle_scheduler import run_oracle_scheduler
asyncio.create_task(run_oracle_scheduler(app))

# 4. API ルーターを登録
from .api.invoke import router as invoke_router
app.include_router(invoke_router)
```

---

## まとめ: セッションの完全なライフサイクル

「召喚」ボタンのクリックからスピリットのアライメントスコア表示まで:

```
1. ユーザーが「召喚」をクリック
   -> POST /api/invoke/summon
   -> InvokeSessionManager.create_session()
      -> session_id = UUID4
      -> spirits = {"faithful": SpiritState("waiting"), ...}
   -> run_invoke_axis_decompose を PROMPT レーンに submit
   -> レスポンス: {"session_id": "...", "job_id": "prompt-000001"}

2. フロントエンドが SSE 接続
   -> GET /api/invoke/stream/{session_id}
   -> event_queue の消費開始

3. run_invoke_axis_decompose (PROMPT レーン) 実行
   -> get_character_danbooru_hints() で Qdrant 検索
   -> decompose_axes():
      - determine_slogan(): ユーザーテキスト or VLM 生成
      - pre_fill_axes(): 絵文字/スライダー/色の決定論的マッピング
      - _build_completion_prompt(): VLM 補完プロンプト組み立て
      - ollama.generate_text(): 空軸を補完
      - _parse_and_merge(): LLM 出力に prefilled 値を上書き
   -> session_manager.on_axis_done(session_id, axes)
      -> emit("axis_done") → SSE → フロントエンド軸表示更新
      -> get_vocab_hints(): stranger/lunatic タグを Qdrant から取得
      -> get_axis_semantic_tags(): 全軸テキストに意味的に近いタグを最大30件取得
      -> 5スピリット分の run_invoke_spirit_compose を PROMPT レーンに並列 submit
         (vocab_hints + axis_tag_hints を両方渡す)

4. run_invoke_spirit_compose × 5 (PROMPT レーン、並列)
   各スピリットに対して独立して実行:
   -> load_spirit(spirit_name): YAML キャッシュからシステムプロンプト取得
   -> locale="ja" なら内部モノローグの日本語指示に書き換え
   -> 10軸 + guest/wild タグ + SUGGESTED DANBOORU TAGS + ルールを結合したプロンプト構築
   -> ollama.generate_text(full_prompt, fmt="json"): LLM 実行
   -> check_spirit_output(): コンテンツガード
   -> session_manager.on_spirit_composed()
      -> spirit.status = "composed"
      -> emit("spirit_composed") → SSE → モノローグ・自然言語表示

   全スピリットが "composed" になった瞬間:
   -> 5スピリット分の run_invoke_image_generate を GEN レーンに並列 submit

5. run_invoke_image_generate × 5 (GEN レーン、並列)
   各スピリットに対して独立して実行:
   -> prompt_mode に応じて positive プロンプト組み立て
   -> person_tags を先頭に付加
   -> comfy.load_workflow() + comfy.patch_workflow() + comfy.queue_prompt()
   -> comfy.stream_progress() で進捗 SSE 中継
   -> 完了: img_bytes 取得 → SHA256 計算 → invoke/ 以下に保存
   -> session_manager.on_image_done(session_id, spirit_name, sha256)
      -> spirit.status = "tagging"
      -> db.set_genesis_payload(): 生成メタデータ書き込み
      -> emit("image_ready") → SSE → 画像サムネイル表示

   全スピリットが generating フェーズを脱した瞬間:
   -> _maybe_submit_finalize(): run_invoke_session_finalize を EMBEDDING レーンに 1 回 submit

6. run_invoke_session_finalize (EMBEDDING レーン)
   -> run_ai_pipeline(): 全生成画像に WD14 タグ付け + 意味ベクター生成
      (べき等: 処理済みはスキップ)
   -> 全スピリットの spirit.status = "scoring"
   -> run_invoke_alignment_score × 5 を EVAL レーンに submit

7. run_invoke_alignment_score × 5 (EVAL レーン)
   各スピリットに対して独立して実行:
   -> AlignmentEvaluator.evaluate_one(sha256): 画像とプロンプトの一致度を VLM で評価
   -> session_manager.on_spirit_done(session_id, spirit_name, score)
      -> spirit.status = "done"
      -> spirit.alignment_score = score
      -> emit("spirit_done") → SSE → スコアバッジ表示

   全スピリットが "done" または "error" になった瞬間:
   -> session.completed = True
   -> emit("session_complete")
   -> _update_summon_stats(): 月別召喚数・lunatic カウント更新
   -> event_queue.put(None) → SSE ストリーム終端

8. フロントエンド
   -> "session_complete" 受信 → invokeLoading = false
   -> "eof" 受信 → EventSource を close
   -> ユーザーが各スピリットカードで操作:
      - 採用: POST /adopt → genesis.adopted_at_genesis = true、兄弟 sha256 を記録
      - リスピン: POST /respin → スピリットリセット → Step 4 から再実行
      - 精製送り: POST /send-to-refine → リファインパイプラインにプロンプト転送
```

---

## 付録: 主要な定数

| 定数 / デフォルト | 値 | 効果 |
|---|---|---|
| `SPIRIT_ORDER` | `["faithful","rebel","stranger","lunatic","oracle"]` | スピリット実行順、UI 表示順 |
| `SESSION_TTL` | 3600 秒 | セッションの有効期間 |
| `invoke_gold_frame_threshold` | 0.85 | ゴールドフレーム表示の alignment_score しきい値 |
| stranger `min_freq` / `max_freq` | 0.04 / 0.40 | stranger タグの Danbooru 頻度範囲 |
| lunatic `min_freq` / `max_freq` | 0.40 / 1.00 | lunatic タグの Danbooru 頻度範囲 |
| stranger ライブラリ優先 | `abs(count - 3)` 最小 | 出現 3 回程度のタグを優先（適度な馴染み） |
| lunatic ライブラリ除外 | `count <= 2` | ユーザーライブラリ未使用タグのみ |
| `nl_usable` しきい値 | 30 文字 | 短すぎる natural_language を画像生成に使わない |
| oracle_scheduler ポーリング間隔 | 30 秒 | 目標時刻を見逃さないための間隔 |
| `invoke_daily_oracle_min_free_gb` | 5.0 GB | オラクル生成に必要な最低ディスク空き容量 |
| `enhance_prompt` 語彙検索 | `min_freq=0.005, max_freq=1.0` | 希少タグも含む広範囲の候補取得 |

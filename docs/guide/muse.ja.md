# Muse — タグ駆動イメージボード生成

## なぜこうなったか

Chronicle → Weave と2世代、「LLM に物語やシーンを書かせ、その文章からプロンプトを組む」路線を作り込んだ。品質は上がらなかった。

決定的だったのは 2026-07-22 の実験で、同じワークフロー・同じモデル・同じ解像度のまま `prompt_positive` だけを手書きに差し替えたら欠陥が全部消えた。**ボトルネックはワークフローでもモデルでもなく、完全にプロンプト層**だった。そしてそのプロンプト層は LLM に書かせている限り安定しなかった。

Muse は路線を反転させる。**LLM に絵を describe させるのをやめ、絵にタグを喋らせる。**

```
お題 + キャラ
   ↓ 軽量 LLM 1コール
背景 / 人物 に分割
   ↓ wd14_vocab へのベクトル検索（LLM なし）
タグ候補（出所つき）
   ↓ 安い生成 ×6
イメージボード
   ↓ WD14 を閾値 0.15 で読み直す
絵が実際に持っていたタグ
   ↓ 重み付き結合（背景 ⇔ 人物）
本番プロンプト
   ↓ ブレスト
シーン説明 2文
   ↓
本番画像
```

LLM が触るのは 3 箇所だけ（お題の分割、ブレスト、シーン説明の圧縮）で、そのどれもが**画の内容を決めていない**。画の内容を決めるのはタグ検索と、生成された絵そのもの。

## ステップ

### S1 お題の分割 — `POST /api/muse/sessions/{id}/split`

Inspire のテーマ展開プロンプト（[`_EXPAND_THEME_PROMPT`](../../backend/app/api/inspire.py)）をそのまま使う。日本語のお題から `character / background / props / action / mood / camera` の6区分が英語タグで返る。

英語なのは意図的。`wd14_vocab` は英語の Danbooru タグなので、日本語のお題はどこかで越境する必要があり、指示に従うモデル1回のほうが文字列マッチより確実に越える。

`mood` と `camera` は**背景トラック側**に付く。人物トラックに付けると全部のボードがポートレートになる。

### S2 タグ収集 — `/tags`

トラックごとに `wd14_vocab` をベクトル検索する。出所は4種類:

| 出所 | 仕組み | 色 |
|---|---|---|
| `split` | S1 が出したタグそのもの | ティール |
| `topic` | `get_topic_tags` — お題ベクトルの近傍を LLM が「ありきたりすぎる語」で間引く | シアン |
| `stranger` / `lunatic` | `get_vocab_hints` — 周波数バンドで絞り、ライブラリにある語を除き、**スコア昇順**で取る | 琥珀 |
| `frontier` | `compute_frontier_hints` — 好みの重心から最も遠く、一度も使っていない語 | 紫 |

意外性は全部この層で作られていて、LLM は一切関与しない。核心は `vocab_bank.py` の**スコア昇順ソート**で、ANN の自然な並びをわざと逆にする。広めのプールを緩い意味的つながりで取ってから、その「一番遠い端」を採る。プール全体がクエリに繋がったままなので、雑音ではなく驚きになる。

タグチップは**クリックで除外できる**。除外は以降の全ステップに効く（Inspire は `custom_blacklist` をバックエンドに持ちながらフロントが常に空を送っていた。その穴を塞いだ）。

### S4 イメージボード — `/board`

背景×3 + 人物×3 を GEN レーンへ。既定 512×512 / 16 steps / CFG 3.0。低 CFG はタグを字義通り強制せず、チェックポイントが得意な方向へ漂わせるため。その漂いが意外性の出所になる。

`generated/playground/` に落ち、`is_draft` が立つ。Qdrant には載る（サムネも sha 参照も検索もタダで手に入る）が、ギャラリー一覧からは既定で外れる。「下書きも表示」で出せる。

> **steps=2 は使えない。** Turbo/Lightning のような蒸留済みモデル以外では絵にならず、閾値 0.15 で読み直した雑音は本番プロンプトまで雑音のまま届く。通常のチェックポイントなら 12〜20。
>
> さらに、Turbo 系ワークフローは step 数を `BasicScheduler` に持つ。`patchable_fields()` がそれを検出し、書き込み先が無ければ**セッションに警告が出る**（黙って無視されるのが一番たちが悪い）。

### S5 逆タグ抽出 — `/harvest`

生成された6枚を WD14 に**閾値 0.15**でかける（ライブラリの既定は 0.35）。弱くて半分間違ったタグの尻尾が大量に入るが、それが目的。誰も頼んでいないものをボードが拾い、本番画像が面白くなるのはここ。

トラック内3枚は1本のリストに畳まれ、次の順で並ぶ:

1. **何枚が同意したか** — 3枚中3枚なら発想の性質、1枚だけなら1シードの偶然
2. その中の最高確度
3. お題が既に要求していたか
4. Danbooru 頻度（`harvest_rerank` オン時のみ。中頻度帯を上げ、両極端を下げる）

同意が先頭なのは、ここで唯一「ボードが本当にそうだった」と「1枚が迷走した」を分けられる信号だから。

二段選別（`harvest_rerank`）は**既定オフ**。整うが意外性が減る。実機で比べてから決める前提。

版権キャラ名（WD14 category 4）は既定で落とす — チェックポイントが自分の下書きの中に他人のキャラを見つけてしまうと、本番が別人の絵になる。rating タグ（category 9）は**既定で通す**。NSFW 除外はチェックボックス。

### S6 タグ結合 — `/merge`

プロンプト錬成の重み付き結合（[`prompt/tag_merge.py`](../../backend/app/prompt/tag_merge.py)）をそのまま使う。共通/固有の分解、重みに比例した予算、`blonde_hair` と `purple_hair` が両方生き残るのを止めるトークン重複チェック。

Muse が足すのは前後の2つ:

**前** — 各トラックの3枚を1つの合成ドキュメントに畳む。おかげで重みのつまみが「画像 vs 画像」ではなく **背景 vs 人物**になる。背景寄り＝引きの絵、人物寄り＝寄りの絵。

**後** — キャラの `identity_tags` を強制的に戻す。**これは省略できない。** `_build_weighted_wd14_context` は各サイドに `unique_count × weight` の予算を配り、`must` タグを優先はするが予算で切る。つまみを 0.9 背景に振ると髪色と目の色がリストの末尾から落ちる — 旧パイプラインを捨てる原因になった、まさにその崩れ方。

管理画面の除外ワードもここで効くが、**保護タグには効かない**。ユーザーはそのキャラを意図して選んでいて、除外リストはプロンプト衛生の話だから。

### S7 シーン説明 — `/brainstorm` → `/brainstorm/record` → `/scene`

Inspire のブレストをそのまま呼ぶ。結合タグを `reference_tags` として渡し、`## 見出し` ごとのシーン案が3〜5個ストリームで返る。ユーザーが1つ選ぶと、軽量 LLM が**ちょうど2文**に圧縮する。

タグセットを渡すと「実際に描きたいシチュエーション」が返ってくるのがブレストの価値で、タグを言い換えて返してくるだけのモデルとは別物。だからここは自前実装せず同じジョブを使っている（違うのは、ライブラリ画像の集合ではなくタグセットを食わせている点だけ）。

### S8 本番生成 — `/render`

`タグ行 \n\n シーン2文` の順で本番ワークフローへ。タグが先なのは、モデルが実際に条件付けするのはタグ行で、散文は構図と雰囲気を寄せるだけだから。散文を先に置くとタグが注意の届かない位置まで押し出される。

## キャラクター

`character_presets` コレクション。同梱 100 体 + ユーザー作成。プリセットは既に Danbooru タグを持っているので、キャラ確定は**純粋関数で LLM を通さない**（[`characters/presets.py`](../../backend/app/characters/presets.py) `preset_to_character`）。キャラは全画像で同一であるべき唯一の部分で、そこに LLM を挟むのが髪色・目の色をパネル間でドリフトさせていた原因だった。

参照ボードは `sheet` と `portrait` の2枚で、`generated/characters/` に保存される（[`characters/board.py`](../../backend/app/characters/board.py)）。

**sheet は Chronicle 時代に辿り着いた合成形式**を使う。中央に全身、周囲にポラロイド枠の4カット（趣味 / 運動 / 食べ物 / 仕事）を並べた1枚で、同じ人物が4つの生活の中でも同じ人物に見えるかを確認できる。

この形式は2点が効いている。**平坦なタグ列ではなくラベル付きの行**で書くこと（そうしないとモデルは並べずに混ぜる）、そして **`multiple_views` をポジティブに入れる**こと（このコードベースの他の全プロンプトでは禁止しているタグ）。`full_body, standing` の平坦なタグ列で書くとマネキンの立ち絵になり、portrait と見分けがつかなくなる。

```
Character: 1girl, black_hair, very_long_hair, straight_hair, brown_eyes, tall, cardigan, long_skirt, loafers,
Accessories: book_cart, glasses

** Chronicles of Character **
Center/Main : casual, leaning_forward, dynamic posture, smile, holding book_cart
Around 4 chronicles with polaroid frame ** same hair and eye color **:
 - holding_book, cardigan, long_skirt
 - walking, sportswear
 - eating, crepe
 - cafe staff, working
Shot: wide_shot, full_body,
Effect: cinematic, kodak color, film_grain, blurry_background, hdr, bokeh, multiple_views, cute,
```

**中央のポーズと4カットは LLM がパーソナリティから決める**（`plan_sheet`）。性格・好み・習慣・服装・持ち物を渡し、「彼女らしい姿勢＋表情＋持ち物」と「互いに違う4つの生活」を書かせる。髪色・目の色・体型には触れさせない（別に固定してあり、触れれば矛盾しか生まない）。

固定枠だった頃は全員が「趣味 / 運動 / 食事 / 仕事」の同じ4枠で、誰にでも `tennis` と `crepe` が配られていた。実機での差:

| | 固定枠 | LLM |
|---|---|---|
| 図書館の司書見習 | walking, sportswear / eating, crepe / cafe staff | 廊下を歩く（メランコリー） / 窓辺で読書 / カートを階段で運ぶ（疲れ顔） / 隅に座って俯く |
| 深夜のパン職人 | tennis, sportswear / eating, crepe | 粉まみれで焼く / 外で生地をこねる / 夜明けに湯気の立つマグ / 市場へパンを詰める |

LLM が落ちた場合・4カットが重複した場合は固定枠へフォールバックする。何も描けないボードより、同じでも描けるボードのほうがましなため。

**portrait は逆で、顔を等倍で確認するための寄り**。identity と頭部に着けるもの（眼鏡・髪飾り）だけを載せ、服装と手持ちの小物は落とす。`long_skirt` も `book_cart` も「全身を見せろ」という票なので、これらが残っていると portrait が2枚目の立ち絵として返ってくる。ネガティブに `full_body` / `wide_shot` / `multiple_views` を入れ、キャンバスも 512×512 の正方形にして足を置く場所を無くしてある。

## API

| | |
|---|---|
| `GET /api/muse/catalog` | ワークフロー、モデル、キャラ数、**wd14_vocab が入っているか** |
| `POST /api/muse/sessions` | 開始 |
| `PATCH /sessions/{id}/inputs` | 全パラメータ |
| `POST /sessions/{id}/character` | キャラ確定（この時点で凍結される） |
| `POST /sessions/{id}/reject-tags` | `{tags, remove}` — 除外の追加／解除 |
| `POST /sessions/{id}/{split,tags,board,harvest,merge,render}` | 各ステップ |
| `POST /sessions/{id}/brainstorm` | ブレストをキュー → `job_id` |
| `POST /sessions/{id}/brainstorm/record` | ストリームで受けた markdown を戻す |
| `POST /sessions/{id}/scene` | `{index}` — 案を選んで2文に圧縮 |
| `GET /sessions/{id}/stream` | SSE。イベント内容は問わず「再取得しろ」の合図 |
| `GET/POST/PUT/DELETE /api/characters` | キャラ registry |
| `POST /api/characters/{id}/board` | 参照ボード生成 |

`wd14_vocab` が空だと**全ステップが無言で空を返す**（`vocab_bank` の全関数が `[]` を返す設計）。カタログがそれを報告し、パネルが警告を出す。取り込みは `POST /api/admin/invoke/import-wd14-vocab`。

## まだやっていないこと

チャット形式で完成画像を直していくループ。セッションの `timeline[]` と `rejected_tags[]` がその土台で、「このタグを外して」「もっと夕方っぽく」は `rejected_tags` への追記と S6 以降の再実行として表現できる形にしてある。

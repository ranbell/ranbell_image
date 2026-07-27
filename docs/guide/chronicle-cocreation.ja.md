# Chronicle Weave — 根本再設計書（品質×速度版）

**方針: 継ぎ足しではない。現行 Stage1→Stage2→A/B/C 一括を廃棄し、Weave を本体として書き換える。**

コードネーム: **Weave**  
前提: 小型 LLM、ComfyUI、3パネル、日本語 UI  
品質スタンス: **検証ゲートは厚く、LLM 呼び出しは少なく短く**（精度↑と実行時間↓を同時に取る）  
改訂: Dry Run §20 の P1–P10 を本文に取り込み済み（2026-07-27）

---

## プロダクト固定（必達）

1. **キャラはパーソナリティ起点**  
   性格・口調・癖・価値観から外見とイメージボードを類推する。  
   **identity（髪・目・体・衣装）と signature_prop（小物）は分離**して持つ。ボードが視覚の真実。参照画像は任意の上書きロック。
2. **ストーリーは常に1本**  
   A/B/C も物語本文の部分改稿も本線にしない。変えたい／直らないなら **再作成（Recreate）のみ**。
3. **共同制作の主戦場は絵の検品**  
   イメージボード整合・サンプル構図・通し小道具。
4. **体感速度**  
   identity ロック後は **Story を先行可能**。ボード生成はベストエフォート並列（単一 GPU では直列化しうる）。
5. **ボード既定は3スロット** — `portrait` / `full` / `prop`（`mood` はオプション）。

非目標: Vision だけで人を排除する閉ループ、汎用 chatbot 化。  
Vision **補助警告**と人のチップ評定は品質のため積極採用。

---

## 0. 破棄 / 再利用

### 破棄

| 現行 | 理由 |
|------|------|
| Stage1 巨大 md × 候補3本 | 遅い・壊れる。1本＋再作成へ |
| Stage2 フル rewrite ×3 | 時間の主犯。決定論編譯へ |
| A/B/C UX | 精度に寄与せず LLM 時間だけ3倍 |
| 物語の対話パッチ本線 | 因果が溶けてかえって遅い |
| パネル自由編集 API | Repair 失敗時も Recreate に統一 |
| 「プロンプトを足せば直る」 | 自己矛盾で破綻済み |

### 再利用

| 資産 | 転用 |
|------|------|
| Comfy / GEN / SSE / spooler | ボード・サンプル・本番 |
| WD14 / identity 分類 | 参照画像があるときの上書き |
| quality.py の次元思想 | WeaveScore |
| 作家プリセット | personality / tone の種（Storywright に必ず渡す） |
| draft 画像保存 | ボード・サンプル |
| Storybook | sealed の閲覧投影 |

---

## 1. 中心概念

```text
Personality
  → identity_tags + prop_tags + signature_prop（分離）
  → [並行可] Image Board 生成  ‖  StoryBundle ×1（identity ロック後すぐ可）
       └ 不満 / lint 修復不能 → Recreate（命令文テンプレ拘束）
  → 決定論 Compile（must_show 解決器つき・LLM なし）
  → Sample 検品（chips / heuristics / 任意 VLM / framing override）
  → Board 採用済みであることの確認 → Final ×3 → Seal
```

```text
真実 = WeaveSession
  ├ character.personality
  ├ character.identity_tags   … 髪・目・体・衣装のみ
  ├ character.prop_tags + signature_prop  … 小物（throughline 層）
  ├ character.board
  ├ story_bundle + story_history（rollback 可）
  └ verify（constraints / samples / scores / framing_overrides）
画像プロンプト = 編譯結果（派生物）
```

---

## 2. 精度↑と時間↓を同時に取る原則

| # | 原則 | 精度 | 時間 |
|---|------|------|------|
| 1 | Identity lock before Story | 別人事故↓ | 物語手戻り↓ |
| 2 | One story, recreate to change | 部分改稿バグ↓ | 対話ターン↓ |
| 3 | LLM less, code more | 構図/identity 安定 | Stage2×3 削除 |
| 4 | Cheap verify first | 欠陥早期発見 | LLM Critic 削減 |
| 5 | Chips → command templates | 再作成一発目↑ | triage 曖昧さ↓ |
| 6 | Story∥Board | — | 物語先読みの体感↑ |
| 7 | Fail → Recreate or constraint | 出口が明確 | 無限 Repair↓ |
| 8 | Budgeted gates | strict で詰められる | standard は最短 |

**目指す LLM 回数（standard・ハッピーパス）**

| 段 | LLM | 備考 |
|----|-----|------|
| Personality → 外見 JSON | 1 | identity / prop 分離 schema |
| StoryBundle ×1 | 1 | 失敗時 Repair 最大1 → 駄目なら Recreate |
| Critic | 0〜1 | コード lint 失敗時だけ |
| Stage2 相当 | 0 | 決定論 compile |
| 再作成ごと | +1 | 命令文拘束付き |
| **合計** | **おおむね 2〜3** | |

---

## 3. メインフロー（取り込み後）

```text
[Character]
  パーソナリティ（+ 任意 topic / 参照画像 / 作家）
    → Personalitywright（LLM×1）
    → identity_tags と prop_tags/signature_prop を分離して保存
    → 人が identity を確認し lock（G0-soft）
    → 同時に:
         (a) board×3 レンダー開始（待ちにしない）
         (b) topic があれば Story 生成可能（Story 先行）

[Story ×1]
  topic(必須) + character + author_style + recreate_constraints?
    → Storywright（LLM×1）→ StoryBundle
    → must_show 解決 → code lint / throughline / causality
    → (fail) Repairer 最大1回
    → (still fail) CTA は Recreate のみ（自由編集なし）
    → 人: 進む / 再作成 / 過去版に戻す

[Look-dev]
  board が揃っていれば常時ピン表示（未完了なら生成中表示）
  決定論 compile（解決済み must_show）
    → 危ういパネルから sample（long_shot 優先）
    → rate chips → constraints → recompile / resample
    → framing 連続失敗 → override_framing or workflow 切替
    ※物語本文は触らない。「話がわからない」→ Recreate（sample 破棄を明示）

[Final]
  G0-hard: board 最低 portrait+full が採用済み
  → render_final×3 → Seal
```

---

## 4. キャラクター：パーソナリティ → イメージボード

### 4.1 入力

- 人となり（自由文 2〜4文 or 性格タグ）
- 任意: 年齢帯、性別提示、職業ヒント、作家プリセット、**topic（早期入力推奨）**、参照画像

topic がこの時点で既知なら Personalitywright に渡し、**衣装は topic の場所に適合**させる。  
topic が後から入る場合は Story 生成時に衣装↔場所の衝突を warn（必要なら character 再類推を提案）。

### 4.2 Personalitywright（LLM×1・短）

```json
{
  "personality": {
    "traits": ["cautious", "dry_humor", "dutiful"],
    "social_style": "observant_listener",
    "tempo": "slow_deliberate",
    "soft_spot": "old_books",
    "summary_ja": "慎重で乾いた笑いを持つ、世話焼きの店員"
  },
  "visual_inference": {
    "reasoning_ja": "慎重さ→落ち着いた色味と整えられた髪。古い本が好き→布のしおりを持ち歩く",
    "identity_tags": [
      "1girl", "brown_hair", "low_ponytail", "hazel_eyes",
      "cardigan", "simple_shirt", "long_skirt"
    ],
    "prop_tags": ["cloth_bookmark"],
    "signature_prop": "cloth_bookmark",
    "palette": ["muted_olive", "warm_cream", "brass"],
    "do_not": ["gyaru", "idol_costume", "heavy_armor"]
  },
  "board_briefs": [
    {"slot": "portrait", "camera": "close_up", "purpose": "face_lock"},
    {"slot": "full", "camera": "long_shot", "purpose": "silhouette_outfit"},
    {"slot": "prop", "camera": "medium_shot", "purpose": "signature_prop"}
  ]
}
```

**分離ルール（コードでも強制）**

| フィールド | 含めてよいもの | 含めないもの |
|------------|----------------|--------------|
| `identity_tags` | 性別、髪、目、体型、衣装、履物 | 持ち小物、一時状態、場所 |
| `prop_tags` / `signature_prop` | 持ち小物・しおり・鞄など | 髪色、衣装本体 |
| `do_not` | 禁止モチーフ | — |

類推ルール:

- 性格 → 色・髪の整い・姿勢・**prop** へ写す（根拠1文）
- vague `casual_clothes` 禁止
- topic 既知なら衣装を場所に合わせる（戦士×和菓子屋衝突を回避）
- `signature_prop` はストーリー通し小道具の第一候補

### 4.3 Image Board レンダー

- 既定3スロット: portrait / full / prop（mood は `quality_policy.board_slots` で追加可）
- 決定論 compile: `identity_tags` + slot 用途。prop スロットのみ `prop_tags` を厚く
- GEN 投入は **ベストエフォート並列**（レーン空き次第。単一 GPU では直列＝想定内）
- Story を待たせない。未完了でも Story / Look-dev に進める
- UI: ボード常時ピン（生成中プレースホルダ可）

### 4.4 ロック段階

| ロック | 条件 | 解除できること |
|--------|------|----------------|
| **G0-soft** `identity_locked` | 人が identity_tags を承認 | Story 生成・Recreate |
| **G0-hard** `board_accepted` | portrait+full が表示され人がボード採用 | Final / Seal |
| 再類推 | 確認ダイアログ必須 | identity unlock → story 無効化 → 再作成が必要 |

参照画像ミックス既定: **髪・目=参照、小物・雰囲気=類推**。採用前にタグ差分プレビューを出す。

---

## 5. ストーリー：1本＋再作成

### 5.1 StoryBundle

候補配列禁止。`topic` 空は API が 400。`author_style` は必ず入力へ含める。

```json
{
  "title": "",
  "world": {
    "setting": "",
    "core_conflict": "",
    "ending_intent": "",
    "throughline_place": "",
    "throughline_prop": "",
    "time_scale": "hours",
    "causality_one_liner": ""
  },
  "panels": [
    {
      "key": "panel_1",
      "beat": "setup",
      "narrative_ja": "",
      "visible_change": "",
      "camera": "long_shot",
      "gesture": "",
      "focus": "",
      "time_marker": "",
      "emotion": "",
      "must_show": ["throughline_prop", "throughline_place"]
    }
  ]
}
```

固定:

- beat = setup → turn → settle
- camera 既定 = long / medium / close（ユニーク必須）
- `throughline_prop` 既定候補 = `character.signature_prop`
- キャラ外見は出力しない
- 衣装と場所が衝突するなら world.setting 側を topic に合わせ、態度だけ personality を出す

### 5.2 must_show 解決器（必須・コード）

`must_show` は **参照キー**と **生タグ**を混在許可。compile / lint の前に必ず解決する。

| 入力 | 解決先 |
|------|--------|
| `throughline_prop` | `world.throughline_prop` → prop タグ正規化 |
| `throughline_place` | `world.throughline_place` → place タグ正規化 |
| `signature_prop` | `character.signature_prop` |
| その他文字列 | soft_normalize してタグとして採用 |

解決不能キーは lint **block**（Story 採用不可）。  
`panels[].must_show_resolved` に解決結果を保存し、UI / compile はこちらを見る。

### 5.3 Recreate（物語変更の唯一の出口）

理由チップ必須。チップは短語ではなく **命令文テンプレ**に変換して Storywright へ渡す。

| チップ | `recreate_constraints[]` に入れる命令文（例） |
|--------|-----------------------------------------------|
| 展開が弱い | `Put one visible external event in panel_2 that changes the prop or place state.` |
| 暗い / 重い | `Keep tone warm; ending_intent must be quiet hope without tragedy.` |
| 場所が散る | `Keep throughline_place identical in all panels; no location jump.` |
| 小道具が弱い | `Show signature_prop visibly in every panel must_show_resolved.` |
| ありきたり | `Avoid motifs: {current_motifs}. Shift setting detail or ending focus.` |
| もっと日常 | `No external accident; advance only by the character's visible action/feeling.` |
| もっと事件 | `Single physical or environmental accident in panel_2 with a visible aftermath in panel_3.` |
| 話がわからない | `Rewrite so each panel's visible_change alone explains the causal chain.` |

サーバ:

```text
story_history.push({ version, bundle, reasons, constraints, at })
Storywright(... recreate_constraints as imperative sentences ...)
→ story_bundle v+1
→ lookdev samples 破棄、compile やり直し
```

**lint + Repairer（最大1）後も失敗 → 正式出口は Recreate のみ。**  
パネル自由編集 API は作らない。  
`narrative_ja` の誤字 PATCH のみ可（beat/camera/must_show/world を触ったら拒否して Recreate へ）。

### 5.4 ロールバック

```http
POST /sessions/{id}/story/rollback
{ "to_version": 1 }
```

- `story_history` 内の版を現行に戻す（コピーして version++ でも可）
- lookdev sample は破棄
- UI で v1/v2 の因果カード比較を出す

### 5.5 Repairer 失敗時 UX

```text
lint fail → Repairer×1 → still fail
  → timeline に欠陥リスト
  → 主ボタン「理由を付けて再作成」
  → 自由編集欄は出さない
```

---

## 6. ドメインモデル（要点）

```json
{
  "session_id": "uuid",
  "status": "character | story | lookdev | rendering | sealed",
  "quality_policy": {
    "mode": "standard",
    "min_sample_panels": 1,
    "critic": "on_lint_fail",
    "vlm_assist": true,
    "board_slots": ["portrait", "full", "prop"],
    "framing_fail_limit": 2,
    "strict_seal": false,
    "allow_story_before_board": true
  },
  "inputs": {
    "topic": "",
    "author_id": "",
    "author_style": "",
    "reference_image_id": ""
  },
  "character": {
    "personality": {},
    "identity_tags": [],
    "prop_tags": [],
    "signature_prop": "",
    "palette": [],
    "do_not": [],
    "board": { "images": [], "accepted": false },
    "identity_locked": false,
    "source": "personality"
  },
  "story_bundle": {},
  "story_version": 1,
  "story_history": [],
  "recreate_constraints": [],
  "avoid_motifs": [],
  "constraints": [],
  "framing_overrides": [],
  "panels": [],
  "cross_panel_qa": {},
  "timeline": [],
  "preference_log": []
}
```

各パネルは `must_show`（生）と `must_show_resolved`（解決済）を持つ。

---

## 7. ロール

| ロール | 回数 | 役割 |
|--------|------|------|
| Personalitywright | 類推ごと1 | 性格→identity/prop 分離 JSON |
| Storywright | 物語ごと1 | StoryBundle のみ |
| Repairer | lint 失敗時最大1 | patch only。失敗後は Recreate |
| Critic | on_lint_fail / strict | 短い欠陥 JSON |
| Facilitator | **コード** | Next CTA |
| Spicer | 既定 off | lab のみ |
| Panelwright | **なし** | |

チップ → 命令文は `story/recreate.py` の辞書（コード）。

---

## 8. 状態機械とゲート

```text
character → story ⇄ recreate/rollback → lookdev ⇄ sample/override → rendering → sealed
                 ↘ board 生成は character 以降いつでも並行
```

| ID | 条件 | standard |
|----|------|----------|
| G0-soft | `identity_locked` | Story に必要 |
| G0-hard | `board.accepted` かつ portrait+full あり | **Final に必要**（Story には不要） |
| G1 | story lint pass（解決済 must_show 含む） | block |
| G2 | camera ユニーク / throughline 3/3 | block |
| G3 | min_sample_panels 閲覧 | block |
| G4 | long_shot framing_check **または** framing_override | block |
| G5 | cross_panel ready_for_final | warn（strict で block） |
| G6 | seal rubric | strict のみ |

`rendering` / `sealed` 中の Recreate は 409。  
`allow_story_before_board=true`（既定）のとき Story は G0-soft のみで開始可。

---

## 9. 品質スタック

### 残す

- Drawability lint
- must_show 解決器 + Throughline binder（prop は prop 層から）
- Causality card
- Camera supremacy compile
- Sample chips → constraints（寄り / 別人 / 小道具なし / 表情死 / **寂しい** / 良い / 話がわからない）
- Framing heuristics（**M3 必須実装**）
- framing override（理由付き）
- VLM 固定4問（任意）
- WeaveScore
- Recreate 命令文拘束 + avoid bank
- story rollback
- Seal ルーブリック

### guided repair 対応表（story 不変）

| チップ | 更新対象 |
|--------|----------|
| 寄りすぎ | camera 層強化、framing negative、place を must_show_resolved に |
| 別人 | identity 再確認 CTA（再類推は確認後）、state ノイズ除去 |
| 小道具なし | prop_tags を compile throughline に二重注入、focus=prop |
| 表情死 | emotion lexicon（medium/close）、face-visible |
| 寂しい | **environment lexicon を厚く追加**（棚、雨、灯、客のシルエット等） |
| 話がわからない | Look-dev では直さない → Recreate ダイアログ |
| 良い | preference_log に正例 |

### Recreate 入力（薄く鋭く）

```text
identity_tags / prop_tags / signature_prop
personality.summary_ja
topic
author_style
previous causality_one_liner
avoid_motifs[]
recreate_constraints[]   … 命令文テンプレのみ
```

timeline 全文は渡さない。

---

## 10. 編譯（LLM 0）

```text
positive =
  identity_tags
+ camera_lock(camera)
+ throughline from must_show_resolved + prop_tags
+ gesture / emotion / time
+ environment(world.setting) + optional lexicon boost（寂しいチップ後）
+ constraints → negative 側
```

- identity 層に prop を混ぜない
- Camera supremacy / budget / checksum 維持
- ボード用は identity + slot purpose（prop スロットだけ prop_tags）

---

## 11. Look-dev

1. long_shot など危ういパネルから sample  
2. heuristics で寄り検知（face/中央密度近似）— **未実装なら G4 を假装しない**  
3. 失敗時: guided repair → resample  
4. **同一パネルで framing_fail が `framing_fail_limit`（既定2）を超えたら**  
   - 手番 `override_framing`（理由必須、timeline 記録）で G4 を通す  
   - または sample workflow / steps 切替を提示  
5. 別人連続 → 再類推確認ダイアログ（story が飛ぶことを明示）  
6. 「話がわからない」→ Recreate（**sample が捨てられる警告**付き）

---

## 12. API

`/api/weave`

| Path | 役割 |
|------|------|
| `POST /sessions` | 作成（topic は後から可） |
| `POST .../character/infer` | パーソナリティ類推 |
| `POST .../character/board` | ボードレンダー開始 |
| `POST .../character/lock` | identity_locked |
| `POST .../character/accept-board` | board.accepted（G0-hard） |
| `POST .../story/generate` | Story×1（topic・author_style 必須） |
| `POST .../story/recreate` | 理由チップ → 命令文拘束 → 再作成 |
| `POST .../story/rollback` | 過去版を現行へ |
| `POST .../compile` | must_show 解決 + 決定論編譯 |
| `POST .../sample` | サンプル |
| `POST .../sample/rate` | チップ |
| `POST .../sample/override-framing` | G4 理由付き突破 |
| `POST .../render_final` | G0-hard 必須 |
| `POST .../seal` | ルーブリック |
| `GET  .../stream` | SSE |
| `GET  .../export` | 評価バンドル |

**提供しない:** `revise_panel`、`candidates`、A/B/C select、Stage2 enhance、物語フリー編集。

---

## 13. UI

```text
┌─ Character Board ────────┬─ Main ──────────────────────────┐
│ portrait/full/prop        │ Next CTA（コード）               │
│ （生成中プレースホルダ可） │ 因果カード + 3ビート             │
│ identity / prop 分離表示  │ [進む] [再作成] [版に戻す]       │
│                           │ Look-dev: sample + chips         │
│                           │ framing override / layers/score  │
└───────────────────────────┴──────────────────────────────────┘
```

- identity lock 後、ボード完了を待たず「ストーリー作成」を有効化（既定）
- Repairer 失敗画面は Recreate 主ボタンのみ
- Recreate で Look-dev 成果が消える場合はモーダルで明示
- 再類推も同様に「物語が無効化される」と明示

---

## 14. 実行時間

| 工程 | 目標・前提 |
|------|------------|
| Personalitywright | LLM×1 短い |
| identity lock → Story | **ボードを待たない**（体感改善の要点） |
| Board×3 | 画像律速。並列はベストエフォート |
| Storywright | LLM×1 |
| lint/resolve/compile | 即時 |
| Sample | 軽量 workflow・必要枚数のみ |
| Final×3 | 本番 workflow、G0-hard 後 |

律速は画像。LLM 直列はハッピーパス2回。

---

## 15. モジュール

```text
backend/app/weave/
  api.py
  session_db.py
  state_machine.py
  character/
    personalitywright.py
    board_render.py
    lexicons.py
    split_tags.py          # identity/prop 分離のコード強制
  story/
    storywright.py
    recreate.py            # chips → imperative templates
    repairer.py
    rollback.py
  validate/
    drawability.py
    throughline.py
    must_show_resolve.py   # 参照キー解決
    cameras.py
    causality.py
  compile/
    layers.py cameras.py lexicons.py negatives.py budget.py
  verify/
    heuristics.py          # framing — M3 必須
    vlm_assist.py score.py cross_panel.py
  render/
    sample.py final.py
  memory/
    constraints.py preferences.py
  prompts/
    personalitywright.md storywright.md repairer.md critic.md

frontend/.../WeavePanel.vue
frontend/.../weave/{Board, StoryCard, RecreateDialog, Rollback, RateChips, FramingOverride, ...}
```

---

## 16. マイルストーン

### M0
セッション、identity/prop 分離表示、Next CTA、G0-soft/hard 区別

### M1 キャラ＋Story 先行
Personalitywright、board×3（非ブロック）、identity lock 後の Story 先行、author_style 配線

### M2 Story 品質
must_show 解決器、lint、Repairer×1→Recreate 固定出口、命令文 Recreate、rollback

### M3 Look-dev
compile、sample、chips（寂しい含む）、framing heuristics、override_framing

### M4 Final / Seal / export / Storybook 投影（G0-hard）

### M5 旧 Chronicle 撤去

### M6（任意）
strict/lab、VLM、Spicer、mood スロット、multi-seed

---

## 17. 成功指標

**時間**

- 再作成なしでストーリー文面表示までの LLM ≤ 2
- identity lock → ストーリー表示が board 完了を待たない

**精度**

- identity に prop が混入するレート = 0（コード強制）
- must_show 未解決での compile = 0
- Recreate 1回目採用率↑
- long_shot framing_fail↓（override 率も監視）
- throughline_coverage = 1.0
- seal「同じ話」「同一人物」≥ 1.5/2

---

## 18. 決定事項

| 項目 | 決定 |
|------|------|
| ボード枚数既定 | **3**（portrait/full/prop） |
| Story 先行 | **許可**（`allow_story_before_board=true`） |
| 参照ミックス | 髪目=参照、小物=類推 |
| 品質 mode 既定 | standard |
| 物語編集 | **Recreate / rollback のみ** |
| framing 突破 | fail_limit 後に override_framing |
| UI 名称 | 未決（Chronicle表示 / Weave） |
| Storybook | 未決（まず互換投影を推奨） |

---

## 19. 一文宣言

**identity と小物を分けてパーソナリティからキャラを固め、ボードを待ちずに物語を1本作り、直したくなったら命令文付きで再作成する。繪は決定論編譯とサンプル検品で詰め、ゲートを通してから焼く。**

---

## 20. Dry Run シミュレーション（記録）

実施日: 2026-07-27 / 机上検証  
お題: 「雨の日の小さな書店」  
パーソナリティ: 「慎重で皮肉屋。困っている客は放っておけない。古い本の匂いが好きな店員。」

### 20.1–20.4 シナリオ要約

- **A ハッピーパス:** LLM2・画像7で成立。ただし旧設計は物語表示が board 待ちで遅い → **P1 で Story 先行を採用**
- **B 再作成:** 健全。チップが粗いと事件足しに寄る → **P2 命令文テンプレを採用**
- **C 寄り失敗:** compile 修理は有効。突破口不足 → **P3 override を採用**
- **D エッジ:** Repairer 出口・prop 混入・must_show 未解決が致命寄り → **P4–P6 を採用**

### 20.5 修正票 → 本文取り込み状況

| ID | 内容 | 状態 |
|----|------|------|
| P1 | Story∥Board、G0-soft/hard | **本文済** §3 §8 |
| P2 | Recreate 命令文テンプレ | **本文済** §5.3 |
| P3 | override_framing | **本文済** §8 §11 §12 |
| P4 | Repairer 失敗＝Recreate のみ | **本文済** §5.3 §5.5 |
| P5 | identity / prop 分離 | **本文済** §4.2 §6 §10 |
| P6 | must_show 解決器 | **本文済** §5.2 |
| P7 | board 既定3、並列ベストエフォート | **本文済** 固定4 §4.3 §14 |
| P8 | story rollback API | **本文済** §5.4 §12 |
| P9 | topic 適合衣装 / 衝突 warn | **本文済** §4.1 §5.1 |
| P10 | 「寂しい」→ environment lexicon | **本文済** §9 guided repair |

### 20.6 総合判定（取り込み後）

| 観点 | 判定 |
|------|------|
| 方針矛盾 | なし |
| 実装ブロッカーだった穴 | P1–P6 で閉塞 |
| LLM 削減 | 達成見込み |
| 実行時間 | 画像律速を認め、Story 先行で体感を確保 |
| 次のアクション | M0 実装に入れる |

**結論: 設計は実装可能な状態。次は M0 からコードに起こす。**

---

## 21. 実装状況（コード）

| マイルストーン | 状態 |
|----------------|------|
| M0 セッション / CTA / G0-soft·hard | **済** `backend/app/weave/` |
| M1 類推 + Story 先行 + board キュー | **済**（Comfy `run_weave_image_generate`） |
| M2 must_show / Recreate / rollback / compile | **済** |
| M3 sample Comfy + rate/override | **済** |
| M4 render_final Comfy + Seal | **済**（Storybook 投影は未） |
| UI WeavePanel | **済**（Chronicle 併存） |
| 旧 Chronicle 撤去 | 未 |

API: `/api/weave/sessions...`  
UI: ヘッダー「Weave」→ `WeavePanel.vue`  
テスト: `tests/weave/`（11+）

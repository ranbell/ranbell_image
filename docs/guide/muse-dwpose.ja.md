# Muse × DWPose — ComfyUI ノードの組み方

ディレクション（ポーズコーチング）ON で撮った**ショット見取り図**を、試し撮り／本番で ControlNet に載せるための組み方です。

Muse は workflow を検査し、次が揃っているときだけ自動で画像を流し込みます。

- `LoadImage`（または同等）が **DWPose／OpenPose 前処理**の上流にある
- その前処理の出力が ControlNet に繋がっている

前処理がなく「骨格画像前提の ControlNet だけ」の場合は、自動投入しません。

---

## 最短構成（推奨）

```
[LoadImage]  ← Muse が見取り図 JPEG をここに差し込む
    │
    ▼
[DWPreprocessor]   （ComfyUI-DWPose / comfyui_controlnet_aux など）
    │  IMAGE（骨格マップ）
    ▼
[ControlNetApply / ControlNetApplyAdvanced]
    ▲
[ControlNetLoader]  ← モデルは openpose 系（例: control_v11p_sd15_openpose）
    │
    └── positive / negative（いつもどおり CLIP → KSampler）
```

ControlNet の**重みモデル名**は今もだいたい `openpose` のままです。  
違うのは**骨格を取る前処理＝DWPose**です。

---

## ノード詳細

### 1. LoadImage

- 画像スロットは何でもよい（ダミー PNG で可）
- **1系統だけ**にする（IP-Adapter 用など別の LoadImage がある場合、DWPose 側の上流だけを見取り図用にする）
- Muse はこのノードの `inputs.image` を上書きする

### 2. DWPreprocessor（または DWPose Estimator）

よくあるクラス名例:

- `DWPreprocessor`
- 名前に `DWPose` / `dwpose` が含まれるノード

推奨設定の目安:

| 項目 | 推奨 |
|------|------|
| detect_body | ON |
| detect_hand | ON（手を取るなら必須級） |
| detect_face | 任意（寄りなら ON） |
| resolution | 512〜1024（見取り図が小さければ 768 前後） |

二人（膝枕など）では bbox／複数人検出が使えるノードなら ON。

### 3. ControlNetLoader

- SD1.5 例: `control_v11p_sd15_openpose`
- SDXL なら対応する openpose ControlNet
- 前処理が DWPose でも、Loader 側は openpose 用モデルでよい

### 4. ControlNetApply / ControlNetApplyAdvanced

- `image`（または同等）← DWPreprocessor の出力
- `control_net` ← ControlNetLoader
- `positive` / `negative` ← いつもどおりの条件付け
- strength 目安: **0.6〜0.85**（強すぎると人形っぽくなる）

その後は通常どおり KSampler → VAE Decode → SaveImage。

---

## Muse 側の使い方

1. セッションでこの workflow を選ぶ  
   → UI に「OpenPose系統あり — 見取り図を流せます」と出れば OK
2. ポーズコーチング（ディレクション）を ON
3. 現場プレビューでポーズを置く
4. 「膝枕でこんな感じ」など短く送る（見取り図が添付・保持される）
5. 試し撮り／本番  
   → Muse が LoadImage に見取り図を upload＆差し込み → DWPose が骨格検出 → ControlNet

---

## やってはいけない組み方

### NG: 前処理なしで骨格画像を直接 LoadImage → ControlNet

```
[LoadImage] → [ControlNetApply]   ← 写真を入れると失敗しやすい
```

Muse は「写真→骨格」の前処理がないと判断し、**自動投入しない**。

### NG: 見取り図用 LoadImage が DWPose に繋がっていない

別用途の LoadImage だけがある／DWPose の image が未接続だと、投入先を見つけられません。

### NG: 見取り図を OpenPose 古典前処理だけに頼る（推奨しない）

動くが、手・重なりは DWPose の方が安定。新規 workflow は DWPose 推奨。

---

## 最小チェックリスト

- [ ] `LoadImage` → `DWPreprocessor` → `ControlNetApply*` の順で配線
- [ ] ControlNet モデルは openpose 系
- [ ] Muse で workflow 選択時に「OpenPose系統あり」表示
- [ ] ディレクション ON で一度チャット送信してから試し撮り
- [ ] Comfy のキューで、LoadImage が `muse_direction_*.jpg` になっていることを確認

---

## トラブルシュート

| 症状 | 確認 |
|------|------|
| UI に系統ありと出ない | ノード名に `DW`/`OpenPose`/`Preprocessor` が含まれるか。API 形式で保存した workflow か |
| 生成にポーズが乗らない | strength、LoadImage が本当に DWPose 上流か、ディレクション送信済みか |
| 二人がくっついて検出される | ショットで重なりを減らす／DWPose の多人・bbox 設定 |
| 手が崩れる | detect_hand ON、見取り図で手が隠れすぎていないか |

---

## 参考

- DWPose（公式）: https://github.com/IDEA-Research/DWPose  
- 前処理は DWPose、ControlNet 重みは openpose 系、がいまの定石

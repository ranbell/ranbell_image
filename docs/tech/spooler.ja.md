# 技術リファレンス: ジョブスプーラー & タスクスケジューリング

このドキュメントでは、Ranbell Image のタスクスケジューリングシステム — **JobSpooler** — の実装全体を解説します。設計の動機（なぜカスタムスケジューラーが必要だったのか）から出発し、最終的には内部の生の asyncio プリミティブにたどり着くまで、段階的に深く掘り下げていきます。

各セクションは前のセクションを前提としています。asyncio に不慣れな方は、冒頭のアナロジーに沿って読み進めてください。経験のある方は [ワーカーループ](#ワーカーループ) または [オートポーズシステム](#オートポーズシステム) から読み始めても構いません。

---

## 概要: なぜカスタムスケジューラーが必要なのか

Ranbell Image は 5 種類のバックグラウンド処理を行います:

| レーン | ワイヤ名 | 処理内容 |
|---|---|---|
| **SYNC** | `sync` | ディレクトリをスキャンし、新規/削除画像を検出して Qdrant に書き込む |
| **EMBED** | `embed` | Ollama 経由でベクター埋め込みを生成する。WD14 タガーを実行する |
| **EVAL** | `eval` | 適合度スコアリング — VLM が生成画像とプロンプトの一致度を評価する |
| **GEN** | `gen` | ComfyUI にプロンプトを送信し、完成した画像をポーリングする |
| **PROMPT** | `prompt` | プロンプト錬成 — VLM が参照画像からプロンプトを合成する |

この 5 つのレーンは **GPU** と **Ollama 推論** という 2 つの共有ボトルネックを奪い合います。調整なしに実行すると、GPU の枯渇、キューの逆転、レイテンシのスパイクが発生します。

Celery や RQ、asyncio.Queue のようなシンプルなタスクキューはこれらの制約を個別には扱えますが、すべてを同時には扱えません。スプーラーは以下を実現するために設計されました:

1. **レーンの分離** — 各レーンは独自のキューとワーカーを持つ。あるレーンがキューを溢れさせても、他のレーンのキュー検査はブロックされない。
2. **リソースの安全な共有** — セマフォがレーンをまたいだ GPU アクセスを直列化する。あるレーンが GPU を保持している間、他のレーンはそれを奪えない。
3. **優先度順序の表現** — GEN ジョブはキューを追い越せる。ルーティンな EVAL ジョブが新しい錬成リクエストを飢餓させることはない。
4. **協調的なポーズ** — 実行中のジョブを次の論理チェックポイントで一時停止できる。処理中に強制終了されることはない。
5. **ライブ状態のストリーミング** — コントロールルーム UI がポーリングなしに SSE 経由でリアルタイムにすべての状態遷移を受け取る。

```
ユーザー操作
    |
    v
spooler.submit(lane, title, func, **kwargs)
    |
    +--- SYNC キュー    [job] [job] ...  ---> sync-worker
    +--- EMBED キュー   [job] [job] ...  ---> embed-worker
    +--- EVAL キュー    [job] [job] ...  ---> eval-worker
    +--- GEN キュー     [job] [job] ...  ---> gen-worker
    +--- PROMPT キュー  [job] [job] ...  ---> prompt-worker
                                                |
                                         リソースセマフォ
                                         (GPU / Ollama)
                                                |
                                         ランナー関数が実行される
                                                |
                                         SSE ブロードキャスト -> UI
```

5 つのワーカーはすべて同じイベントループ内の **asyncio タスク**として動作します。スレッドもプロセスも外部ブローカーも存在しません。スプーラー全体はメモリ上の単一の Python オブジェクトです。

---

## ソースファイル

| ファイル | 責務 |
|---|---|
| `backend/app/spooler/models.py` | データクラス: `Job`、`JobState`、`JobLane`、`CancelToken`、`ProgressReporter` |
| `backend/app/spooler/spooler.py` | `JobSpooler` — キューイング、ワーカー、オートポーズ、SSE |
| `backend/app/spooler/resources.py` | `Resource`、セマフォ、ヘルスモニター |
| `backend/app/jobs/runners.py` | すべてのランナー関数 |
| `backend/app/api/jobs.py` | REST + SSE HTTP エンドポイント |
| `backend/app/main.py` | FastAPI ライフスパン: 起動/停止 |
| `frontend/src/App.vue` | `EventSource` サブスクリプション |
| `frontend/src/composables/useControlRoom.js` | レーン状態、ランプロジック、イベントのバッチ処理 |

---

## データモデル

### JobState

ジョブは定義された状態セットを移動します。状態機械は次の通りです:

```
  submit()
     |
     v
  +----------+  ワーカーが取得  +----------+
  |  QUEUED  | ---------------> | RUNNING  |
  +----------+                  +-----+----+
       |                              |
    cancel()         +----------------+--------------+
       |             |                |              |
       v          cancel()        exception    pause_checkpoint()
   CANCELLED          |                |          ブロック
                      v                v              |
                 CANCELLING          FAILED           v
                      |                          +----------+
                      v                          |  PAUSED  |
                  CANCELLED                      +-----+----+
                                                       |
                                            resume     |   cancel()
                                               +-------+--------+
                                               |                |
                                               v                v
                                            RUNNING        CANCELLING
                                                                |
                                                                v
                                                           CANCELLED
```

```python
class JobState(str, Enum):
    QUEUED     = "queued"
    RUNNING    = "running"
    PAUSED     = "paused"      # 実行中だがチェックポイントでブロック中
    CANCELLING = "cancelling"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
```

**PAUSED** は RUNNING のサブ状態です。タスクは asyncio イベントループ上でまだ生きており、`pause_checkpoint()` の内部で中断されています。この区別は重要です: PAUSED ジョブはレーン先頭での位置を保持し、すべてのメモリ内状態を維持し、停止した場所から正確に再開します。

**CANCELLING** はキャンセルの意図が実際にランナーに伝播するまでの間、UI に表示するための遷移状態です。

終端状態は `SUCCEEDED`、`FAILED`、`CANCELLED` です。ジョブが終端状態に達すると、履歴 deque に移動されてアクティブレジストリから削除されます。

---

### JobLane

```python
class JobLane(str, Enum):
    GENERATION = "gen"
    EMBEDDING  = "embed"
    EVALUATION = "eval"
    SYNC       = "sync"
    PROMPT     = "prompt"
```

ワイヤ名（例: `"gen"`）はジョブ ID のプレフィックスでもあります: `gen-000001`、`embed-000003`。

---

### Job データクラス

```python
@dataclass
class Job:
    id: str                         # "{lane.value}-{seq:06d}"
    lane: JobLane
    title: str
    state: JobState = JobState.QUEUED
    priority: int = 0               # 大きいほど先に取得される
    progress: float = 0.0           # 0.0 – 1.0
    progress_text: str | None = None
    progress_indeterminate: bool = False
    created_at: float               # time.time()
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    meta: dict = {}                 # 呼び出し側が指定するメタデータ (例: sha256s)

    # プライベート
    _cancel_event: asyncio.Event    # セット → キャンセルが要求された
    _task: asyncio.Task | None      # ランナーを実行している asyncio Task
    _func: Callable                 # ランナー関数
    _kwargs: dict                   # ランナーに転送される kwargs
    _cancel_handlers: list[Callable]# 登録済みクリーンアップコールバック
    _eta_samples: deque(maxlen=5)   # EWMA 用の直近の生 ETA 推定値
```

**ジョブ ID フォーマット**: `{lane.value}-{seq:06d}`（`seq` はレーンごとの単調増加カウンター）。例: 3 番目のプロンプトジョブは `prompt-000003`。

**ETA 計算**は直近 5 サンプルに対する EWMA（指数加重移動平均）を使用します:

```
raw_eta = elapsed × (1 − progress) / progress

smoothed[n] = α × raw_eta[n] + (1 − α) × smoothed[n−1]   (α = 0.4)
```

これにより、バースト的な進捗報告（WD14 ジョブが一気に 10% → 40% に跳ぶなど）に対して ETA が安定します。純粋な線形推定では大きく振れますが、EWMA がそれを抑えます。

---

### CancelToken

`CancelToken` はスプーラーと実行中のジョブの間のインターフェースです。旗竿に立てた旗のようなものです — スプーラーが旗を立ててキャンセルを要求し、ランナーが定期的にそれを確認します。

```python
class CancelToken:
    def raise_if_set(self) -> None:
        """キャンセルが要求されていたら JobCancelled を raise する。"""

    def on_cancel(self, handler: Callable) -> None:
        """cancel() が呼ばれたときに実行するコールバックを登録する。
        外部エンジンのクリーンアップ（ComfyUI ジョブのキャンセルなど）に使用する。"""

    async def pause_checkpoint(self) -> None:
        """レーンとジョブが再開されるまでここで中断する。キャンセルされたら中断。"""
```

トークンは 3 つの asyncio イベントを持ちます:
- `_event` — キャンセルシグナル（`spooler.cancel()` が呼ばれるとセット）
- `_lane_event` — レーンのグローバルポーズイベント（クリア = レーンが一時停止中）
- `_pause_event` — ジョブ個別のポーズイベント（クリア = このジョブが個別に一時停止中）

内部ループで `pause_checkpoint()` を呼ぶランナーは、一度の呼び出しでレーンレベルとジョブレベルの両方のポーズをサポートできます。

---

### ProgressReporter

```python
class ProgressReporter:
    def update(self, progress: float, text: str | None = None) -> None:
        """確定的な進捗 (0.0 – 1.0) をオプションのステータステキストとともに報告する。"""

    def indeterminate(self) -> None:
        """処理は進行中だが、パーセンテージが不明であることを通知する。"""
```

内部では、`update()` は `Job` オブジェクトに書き込み、スプーラーの `_push_event("job_updated", job)` を呼び出します。スプーラーのスロットル処理（[SSE ストリーミング](#sse-ストリーミング) を参照）が、イベントを即座にブロードキャストするか遅延させるかを決定します。

---

## 起動と停止

スプーラーは FastAPI のライフスパンコンテキストマネージャー（`main.py`）で生成・起動されます:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    resources, lane_resource = build_resources(settings)
    spooler = JobSpooler(resources=resources, lane_resource=lane_resource)
    app.state.spooler = spooler
    await spooler.start()
    # ... その他の起動処理

    yield  # アプリが動作する

    await spooler.stop()
```

**`spooler.start()`** は 3 つのことを順番に行います:
1. レーンごとに 1 つの `asyncio.Task` を生成します（合計 5 ワーカータスク、`spooler-worker-{lane}` という名前）。
2. `probe_resources_on_startup()` を呼び出して、ジョブを受け付ける前に Ollama と Qdrant が到達可能であることを確認します。
3. 3 つのバックグラウンドモニタータスクを起動します:
   - `spooler-remote-monitor` — 15 秒ごとに Ollama/Qdrant のヘルスをポーリング
   - `spooler-local-monitor` — 5 秒ごとに GPU/CPU/RAM の統計をポーリング
   - `spooler-resource-push` — 5 秒ごとに `resource_stats` SSE イベントをプッシュ

**`spooler.stop()`** はすべてのモニタータスクとワーカータスクをキャンセルし、完了を待ちます。

---

## ジョブの投入

```python
job_id = spooler.submit(
    lane=JobLane.EMBEDDING,
    title="Generate embeddings",
    func=run_pipeline,
    meta={"source": "scan"},
    priority=0,
    db=db,
    ollama=ollama,
    spooler=spooler,
)
```

`submit()` の内部:

```python
def submit(self, lane, title, func, meta=None, *, priority=0, **kwargs) -> str:
    self._seq[lane] += 1
    job_id = f"{lane.value}-{self._seq[lane]:06d}"
    job = Job(id=job_id, lane=lane, title=title, priority=priority,
              meta=meta or {}, _func=func, _kwargs=kwargs)
    self._registry[job_id] = job

    # 優先度の降順で挿入。同優先度の場合は FIFO
    queue = self._lane_queues[lane]
    idx = len(queue)
    for i, jid in enumerate(queue):
        if priority > self._registry[jid].priority:
            idx = i
            break
    queue.insert(idx, job_id)

    self._lane_work_ev[lane].set()   # ワーカーを起こす
    self._push_event("job_created", job)
    self._update_auto_pause(lane)    # 他のレーンを一時停止させる可能性がある
    return job_id
```
```
  work_ev.wait() --blocked--+
                             | submit() が work_ev.set() を呼ぶ
  <-------------- 起床 -------+
  キューの先頭からジョブを取り出す
  job.state == QUEUED -> 処理を続行

  --- ポーズゲート ----------------------------------
  held_jobs[lane] = job
  lane_events[lane].wait()  <-- レーンが一時停止中なら待機
                              （この間に別のタスクが cancel() を
                               呼んでジョブをキャンセルできる。
                               起床後に状態を確認する）
  held_jobs[lane] = None
  --------------------------------------------------

  job.state = RUNNING

  runner(reporter, cancel, **kwargs)
    +-- reporter.update(0.1, "processing")  -> job_updated をプッシュ（スロットル付き）
    +-- cancel.raise_if_set()               -> キャンセル要求があれば -> JobCancelled
    +-- cancel.pause_checkpoint()           -> 一時停止中なら -> ここで中断
    +-- return result

  job.state = SUCCEEDED
  job_finished をプッシュ
  履歴 deque に移動（appendleft -> LIFO）
```

**1 つのジョブのライフサイクルをステップごとに追う:**

```
  work_ev.wait() --blocked--+
                             | submit() が work_ev.set() を呼ぶ
  <-------------- 起床 ------+
  キューの先頭からジョブを取り出す
  job.state == QUEUED -> 処理を続行

  -- ポーズゲート ----------------------------------
  held_jobs[lane] = job
  lane_events[lane].wait()  <-- レーンが一時停止中なら待機
                              （この間に別のタスクが cancel() を
                               呼んでジョブをキャンセルできる。
                               起床後に状態を確認する）
  held_jobs[lane] = None
  --------------------------------------------------

  job.state = RUNNING

  runner(reporter, cancel, **kwargs)
    +-- reporter.update(0.1, "processing")  -> job_updated をプッシュ（スロットル付き）
    +-- cancel.raise_if_set()               -> キャンセル要求があれば -> JobCancelled
    +-- cancel.pause_checkpoint()           -> 一時停止中なら -> ここで中断
    +-- return result

  job.state = SUCCEEDED
  job_finished をプッシュ
  履歴 deque に移動（appendleft -> LIFO）
```

**held_jobs の仕組み**: ジョブがデキューされたがポーズゲートで待機している場合、`_held_jobs[lane]` がそれを追跡します。これにより `cancel()` はゲートで詰まっているジョブを見つけて終了させることができます — キューから取り出されているが、まだ実行が始まっていない状態のジョブです。

---

## リソース管理

### Resource データクラス

```python
@dataclass
class Resource:
    name: str
    kind: Literal["local", "remote"]
    concurrency: int = 1       # セマフォの容量
    endpoint: str | None = None
    health_path: str = "/"

    reachable: bool = True
    last_ok: float | None = None
    latency_ms: float | None = None
    version: str | None = None

    # local kind のみ
    gpu_util_pct: float | None = None
    temp_c: float | None = None
    vram_used_gb: float | None = None
    vram_total_gb: float | None = None
    cpu_pct: float | None = None
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None

    _sem: asyncio.Semaphore   # __post_init__ で concurrency から生成
```

### デフォルトのレーン → リソースマッピング

```python
DEFAULT_LANE_RESOURCE = {
    JobLane.GENERATION: "local-gpu0",   # GPU セマフォ
    JobLane.EMBEDDING:  "local-gpu0",   # GPU セマフォ（設定次第で "remote-ollama"）
    JobLane.EVALUATION: None,           # リソースセマフォなし — レーンポーズで制御
    JobLane.SYNC:       None,
    JobLane.PROMPT:     None,
}
```

`EVALUATION` には意図的に `None` が設定されています。`pause_checkpoint()` 内でセマフォを保持したまま待機すると、Ollama を必要とする他のレーンがデッドロックします。EVAL のスループット制御はセマフォではなく、レーンレベルのポーズ（Tier 2）で完全に処理されます。

`remote-ollama` が設定されている場合、EMBEDDING はそのリソースに委譲されます（`local-gpu0` ではなく）。

### run_with_resource()

```python
async def run_with_resource(job, resources, lane_resource, func) -> Any:
    res_name = lane_resource.get(job.lane)
    if res_name is None:
        return await func()   # リソース制約なし

    res = resources[res_name]

    if res.kind == "remote" and not res.reachable:
        raise ResourceUnreachable(f"Resource {res.name!r} is unreachable")

    async with res._sem:
        return await func()
```

`async with res._sem` はセマフォを取得し、関数を実行し、いかなる終了（正常リターン、例外、タスクキャンセル）でもリリースします。`asyncio.Semaphore` コンテキストマネージャーは `__aexit__` でリリースを保証します。

`concurrency=1`（デフォルト）の場合、同じリソースにマッピングされたすべてのレーンで、一度に 1 つのランナーしか GPU を保持できません。

### ヘルスモニタリング

`spooler.start()` 後に 2 つのバックグラウンドループが動作します:

**リモートモニター**（15 秒ごと）:
```python
async def monitor_remote_resources(resources, interval=15):
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            for res in remote_resources:
                resp = await client.get(f"{res.endpoint}{res.health_path}")
                res.reachable = resp.status_code < 500
                res.latency_ms = elapsed_ms
                if "version" in resp.json():
                    res.version = resp.json()["version"]
            await asyncio.sleep(interval)
```

- Ollama: `GET /api/version` → `{"version": "0.x.y"}` をパース
- Qdrant: `GET /healthz` → ステータスをパース

**ローカルモニター**（5 秒ごと）:
```python
async def monitor_local_resources(resources, interval=5):
    while True:
        stats = await asyncio.to_thread(_poll_all)  # ブロッキング I/O をイベントループから外す
        for res in local_resources:
            res.gpu_util_pct = stats["gpu_util_pct"]
            res.vram_used_gb = stats["vram_used_gb"]
            # ... etc.
        await asyncio.sleep(interval)
```

`_poll_all()` は以下から読み取ります:
- `pynvml`（nvidia-ml-py）→ GPU 使用率、VRAM、温度。pynvml が利用できない場合は `nvidia-smi` サブプロセスにフォールバック。
- `/proc/stat` → CPU 使用率
- `/proc/meminfo` → RAM 使用量 / 合計
- `/sys/class/hwmon/hwmon*` または `/sys/class/thermal/thermal_zone*` → CPU 温度

すべてのブロッキングなファイルシステム読み取りは `asyncio.to_thread()` でディスパッチされ、イベントループを止めません。

---

## オートポーズシステム

スプーラーの中で最も複雑な部分です。理解するには 3 つのことを知る必要があります:

1. 2 つの tier の定数
2. 各 tier がいつ評価されるか
3. `LanePauseReason.MANUAL` と `LanePauseReason.AUTO` の違い

### Tier 1: 設定可能な優先度ポーズ

**トリガー**: GEN または PROMPT レーン（「優先度トリガーレーン」）にジョブが投入される。  
**効果**: EMBED と EVAL（「オートポーズ対象レーン」）を一時停止する。  
**無効化**: コントロールルームの設定からオフにできる。

```python
_PRIORITY_TRIGGER_LANES: frozenset = {JobLane.GENERATION, JobLane.PROMPT}
_DEFAULT_AUTO_PAUSE_TARGETS: frozenset = {JobLane.EMBEDDING, JobLane.EVALUATION}
```

`submit()` から呼ばれる `_update_auto_pause()` 内で同期的に実行されます:

```python
def _update_auto_pause(self, submitted_lane: JobLane) -> None:
    if self._auto_pause_on_priority and submitted_lane in _PRIORITY_TRIGGER_LANES:
        has_priority = any(
            j.state in (QUEUED, RUNNING)
            for j in self._registry.values()
            if j.lane in _PRIORITY_TRIGGER_LANES
        )
        if has_priority:
            non_eval = self._auto_pause_target_lanes - _TIER2_MANAGED_LANES
            self.pause_lanes(non_eval, LanePauseReason.AUTO)
    self._check_eval_pause()  # 常に tier 2 を実行
```

EVALUATION は Tier 1 のターゲットから除外されています（`- _TIER2_MANAGED_LANES`）。EVAL は Tier 2 によって完全に管理されるためです。

オートポーズは GEN/PROMPT ジョブが終了すると `_check_auto_resume()` でクリアされます:

```python
def _check_auto_resume(self) -> None:
    has_priority = any(
        j.state in (QUEUED, RUNNING, CANCELLING)
        for j in self._registry.values()
        if j.lane in _PRIORITY_TRIGGER_LANES
    )
    if not has_priority:
        non_eval = self._auto_pause_target_lanes - _TIER2_MANAGED_LANES
        self.resume_lanes(non_eval, reason=LanePauseReason.AUTO)
    self._check_eval_pause()
```

### Tier 2: ハードコードされた EVAL ポーズ

**トリガー**: GEN、PROMPT、または EMBED レーン（「EVAL をブロックするレーン」）にジョブが存在する。  
**効果**: Tier 1 の設定に関係なく、常に EVAL を一時停止する。  
**無効化不可** — これはハードコードされた優先ルールです。

```python
_EVALUATION_BLOCKING_LANES: frozenset = {
    JobLane.GENERATION,
    JobLane.PROMPT,
    JobLane.EMBEDDING,
}

def _check_eval_pause(self) -> None:
    blocking = any(
        j.state in (QUEUED, RUNNING, CANCELLING)
        for j in self._registry.values()
        if j.lane in _EVALUATION_BLOCKING_LANES
    )
    if blocking:
        self.pause_lanes([JobLane.EVALUATION], LanePauseReason.AUTO)
    else:
        self.resume_lanes([JobLane.EVALUATION], reason=LanePauseReason.AUTO)
```

`_check_eval_pause()` はアクティブなジョブ数を変化させるすべてのコードパスで呼ばれます: `submit()`、`_update_auto_pause()`、`_check_auto_resume()`、EMBEDDING 完了後。

**優先度順まとめ:**

```
GEN / PROMPT   ──► 最高優先度（Tier 1 経由で EMBED と EVAL をブロック）
EMBED          ──► Tier 2 経由で EVAL をブロック
EVAL           ──► 最低優先度（他に何もないときだけ動く）
SYNC           ──► 独立（リソースセマフォなし、ブロックされない）
```

### MANUAL vs. AUTO ポーズ

`LanePauseReason` は誤った主体がレーンを再開するのを防ぎます:

```python
def resume_lanes(self, lanes, reason=None):
    for lane in lanes:
        current = self._lane_pause_reason[lane]
        if current is None:
            continue
        if reason is not None and current != reason:
            continue   # 手動でポーズされたレーンをオートで再開しない
        self._lane_events[lane].set()
        self._lane_pause_reason[lane] = None
```

ユーザーが手動で EMBED を一時停止した場合（`reason=MANUAL`）、GEN ジョブが完了して `_check_auto_resume()` → `resume_lanes(reason=LanePauseReason.AUTO)` が呼ばれます。現在の理由が `MANUAL != AUTO` なので、レーンは停止したままです。ユーザーの意図が尊重されます。

`reason=None`（強制再開）は UI の手動「再開」ボタンでのみ使用され、`POST /api/jobs/lanes/{lane}/resume` 経由で `resume_lane` を呼び出します。

### ポーズゲート vs. CancelToken.pause_checkpoint()

これはジョブのライフサイクルの異なる時点で動作する 2 つの独立したポーズ機構です:

**ポーズゲート**（ワーカーループ内）:
```python
self._held_jobs[lane] = job
await self._lane_events[lane].wait()   # ← ゲート
self._held_jobs[lane] = None
```
デキューからジョブ開始の間に適用されます。ゲートで待機しているジョブは、まだランナー関数が呼ばれていません。リソースを消費せず、キャンセルトークンも存在しません。スプーラーは協調プロトコルなしにここで `CANCELLED` とマークできます。

**`pause_checkpoint()`**（ランナー関数の内部）:
```python
async def pause_checkpoint(self) -> None:
    self.raise_if_set()
    while True:
        lane_ok = self._lane_event is None or self._lane_event.is_set()
        job_ok  = self._pause_event is None or self._pause_event.is_set()
        if lane_ok and job_ok:
            break
        # PAUSED 状態を UI に通知
        if not _paused:
            self._on_pause()
        # lane 再開、job 再開、キャンセルのいずれかを待つ
        done, pending = await asyncio.wait(
            [lane_task, job_task, cancel_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done:
            raise JobCancelled()
    self.raise_if_set()
```
GPU セマフォをすでに取得済みの実行中のジョブに適用されます。ジョブはセマフォを解放せずに中断します — 論理的にはまだ「実行中」でリソーススロットを保持しています。これは長い埋め込みバッチ中断などで正しい動作です（セマフォを解放して再取得すると不必要なコンテキストロスが生じます）。

---

## SSE ストリーミング

### 接続のライフサイクル

コントロールルームを開いているすべてのブラウザが `GET /api/jobs/stream` に接続します。スプーラーはサブスクライバーのリストを管理し、接続クライアントごとに 1 つの `asyncio.Queue` を持ちます。

```python
async def stream(self) -> AsyncGenerator[str, None]:
    q = asyncio.Queue()
    self._subscribers.append(q)
    try:
        # 接続時に即座に全状態を送信（接続/再接続時の空白画面を防ぐ）
        yield _sse_event("snapshot", {
            "jobs": self.snapshot(),
            "resources": self.resources_snapshot(),
            "lanes": self.lanes_snapshot(),
        })

        last_heartbeat = time.monotonic()
        while True:
            timeout = max(0.1, _HEARTBEAT_INTERVAL - (time.monotonic() - last_heartbeat))
            try:
                data = await asyncio.wait_for(q.get(), timeout=timeout)
                yield data
            except asyncio.TimeoutError:
                yield ": ping\n\n"   # SSE コメント行。接続を維持する
                last_heartbeat = time.monotonic()
    finally:
        self._subscribers.remove(q)
```

スナップショットはイベントループに制御を返す前に送信されるため、クライアントは接続から最初のメッセージの間にイベントを見逃すことはありません。

### イベントタイプ

| イベント | 送信タイミング | データ |
|---|---|---|
| `snapshot` | 接続時 | 全ジョブ + リソース + レーン状態 |
| `job_created` | `submit()` | ジョブの完全な dict |
| `job_updated` | 進捗/状態の変化 | ジョブの完全な dict（スロットル付き — 後述） |
| `job_finished` | ジョブが終端状態に達した | ジョブの完全な dict |
| `resource_stats` | 5 秒ごと | `{resources: [...]}` |
| `lane_state` | レーンが一時停止/再開された | `{lanes: [{lane, paused, pause_reason}]}` |
| `ping` | 15 秒ごと（コメント） | なし — 接続を維持するだけ |

### スロットル処理

高頻度のジョブ（10,000 枚の埋め込み生成）は `reporter.update()` の呼び出しごとに `job_updated` イベントを発行します。スロットルがなければ、毎分何千ものイベントがクライアントを溢れさせます。

```python
_THROTTLE_INTERVAL = 0.25   # job_updated の 4 Hz 上限

def _push_event(self, event_type: str, job: Job) -> None:
    now = time.monotonic()
    if event_type == "job_updated":
        last_push, last_state = self._throttle.get(job.id, (0.0, ''))
        state_changed = job.state.value != last_state
        if not state_changed and now - last_push < _THROTTLE_INTERVAL:
            return   # この更新を捨てる
    self._throttle[job.id] = (now, job.state.value)

    data = _sse_event(event_type, job.to_dict())
    for q in self._subscribers:
        q.put_nowait(data)   # ノンブロッキング。キューが満杯なら捨てる
```

状態遷移（`RUNNING → PAUSED`、`RUNNING → CANCELLING`）は常に通過します — 純粋な進捗更新だけがレート制限されます。これにより、高負荷でも UI は即座にポーズ/キャンセルを反映します。

### SSE ワイヤフォーマット

SSE プロトコルは HTTP 上のプレーンテキストです。各イベントは:

```
event: job_updated
data: {"id":"embed-000003","state":"running","progress":0.45,...}

```

2 つの改行がイベントを終端します。ブラウザの `EventSource` API はこれを自動的にパースし、`addEventListener('job_updated', handler)` を発火させます。

---

## REST API リファレンス

すべてのエンドポイントは `/api/jobs` 以下にあります（`backend/app/api/jobs.py` 参照）:

| メソッド | パス | 操作 |
|---|---|---|
| `GET` | `/api/jobs/stream` | SSE ストリーム（上記参照） |
| `GET` | `/api/jobs` | 現在のスナップショット（JSON、非ストリーミング） |
| `POST` | `/api/jobs/{id}/cancel` | キャンセルを要求する |
| `POST` | `/api/jobs/{id}/pause` | 次のチェックポイントで一時停止する |
| `POST` | `/api/jobs/{id}/resume` | 個別に一時停止されたジョブを再開する |
| `POST` | `/api/jobs/{id}/reorder` | キュー内の順番を変更する（`{"direction": +1}`） |
| `POST` | `/api/jobs/{id}/retry` | FAILED または CANCELLED のジョブを再投入する |
| `GET` | `/api/jobs/lanes` | レーンの一時停止状態 |
| `POST` | `/api/jobs/lanes/{lane}/pause` | レーンを手動で一時停止する |
| `POST` | `/api/jobs/lanes/{lane}/resume` | レーンを強制再開する |

**Retry** は同じ関数と kwargs を使って新しいジョブ ID で全く新しいジョブを生成します:
```python
def retry(self, job_id: str) -> str:
    job = self._registry[job_id]
    new_id = self.submit(
        lane=job.lane, title=job.title, func=job._func,
        meta=dict(job.meta), **job._kwargs,
    )
    return new_id
```

**Reorder** は位置を元に数値的な優先度を再割り当てします — 位置 0 が最大値、位置 n-1 が 1 を得ます。これにより優先度フィールドが視覚的なキュー順序と一致します:

```python
n = len(queue)
for i, jid in enumerate(queue):
    self._registry[jid].priority = n - i
```

---

## フロントエンド統合

### EventSource の設定

```javascript
// App.vue
function startJobStream() {
  const es = new EventSource(`/api/jobs/stream?token=${encodeURIComponent(getToken())}`)

  es.addEventListener('snapshot', (e) => {
    const { jobs, resources, lanes } = JSON.parse(e.data)
    jobsMap.value = new Map(jobs.map(j => [j.id, j]))  // マップ全体を置き換え
    resourcesRef.value = resources
    crIngestEvent('snapshot', data)
  })

  es.addEventListener('job_created', upsert)
  es.addEventListener('job_finished', upsert)   // 即座にマップを更新

  es.addEventListener('job_updated', (e) => {
    const job = JSON.parse(e.data)
    crIngestEvent('job_updated', job)
    _pendingJobUpdates.set(job.id, job)          // 蓄積する
    if (!_pendingJobUpdatesTimer) {
      _pendingJobUpdatesTimer = setTimeout(() => {
        // バッチフラッシュ: すべての蓄積更新を 1 回の Vue リアクティブ書き込みとして適用
        const newMap = new Map(jobsMap.value)
        for (const [id, j] of _pendingJobUpdates) newMap.set(id, j)
        jobsMap.value = newMap
        _pendingJobUpdates = null
        _pendingJobUpdatesTimer = null
      }, 250)                                    // 250 ms バッチウィンドウ
    }
  })

  es.onerror = () => {
    es.close()
    setTimeout(startJobStream, 3000)  // 3 秒後に再接続
  }
}
```

**250ms バッチの理由?** Vue のリアクティビティシステムは `jobsMap.value` への書き込みごとにアクティブなジョブリスト全体を再レンダリングします。速い埋め込みジョブはサーバー側のスロットル速度で 1 秒に 4 件の `job_updated` SSE イベントを発行できます。バッチなしでは 1 秒に 4 回の完全なリスト再レンダリングが発生します。バッチがあれば、250ms 以内の連続した更新は 1 回のレンダリングにまとめられます。

**なぜ更新のたびに新しい Map を作るのか?** Vue のリアクティビティは、コンピューテッドプロパティとウォッチャーが再トリガーされるために、参照自体が変わる必要があります。既存の Map をインプレースで変更すると Vue には見えません。

### コントロールルーム: レーンとシステム状態

`useControlRoom.js` はライブなジョブリストから ISA-101 ランプ状態を計算します:

```javascript
// 各レーン → NOMINAL / ACTIVE / CAUTION / FAULT / PAUSED / STANDBY のいずれか
const systemStatus = computed(() => {
  return SYSTEMS.reduce((acc, sys) => {
    const laneJobs = jobs.filter(j => j.id.startsWith(sys.lane + '-'))
    const failed  = laneJobs.filter(j => j.state === 'failed')
    const running = laneJobs.filter(j => j.state === 'running' || j.state === 'cancelling')
    const queued  = laneJobs.filter(j => j.state === 'queued')
    const ls = laneStates.value[sys.lane]

    if (failed.length > 0)                    return FAULT
    if (running.length > 0)                   return ACTIVE
    if (ls?.paused && queued.length > 0)      return CAUTION  // ポーズ中にバックログがある
    if (ls?.paused)                           return PAUSED
    if (queued.length >= 3)                   return CAUTION
    if (queued.length > 0)                    return ACTIVE
    return NOMINAL
  }, {})
})
```

マスターステータスランプ（`STANDBY / RUNNING / CAUTION / FAULT / STARTING`）はすべてのシステム状態とリモートリソースの `last_ok` フィールドを集約します（`last_ok === null` の場合、リソースは一度も正常に接続されていない — STARTING と表示されます）。

---

## ランナーコントラクト

すべてのランナー関数はこのシグネチャに従う必要があります:

```python
async def run_X(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    # submit() 時にキーワード引数として注入される依存関係
    db,
    ollama,
    spooler=None,
) -> Any:
```

`reporter` と `cancel` はスプーラーが注入します。それ以外はすべて `submit()` に渡した `**kwargs` から来ます。

**最小限のコンプライアントなランナー:**

```python
async def run_example(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    items: list,
    db,
) -> dict:
    reporter.indeterminate()   # 「処理中だが ETA 不明」を通知

    results = []
    for i, item in enumerate(items):
        cancel.raise_if_set()          # 協調的なキャンセルチェック
        await cancel.pause_checkpoint() # ポーズをサポート（レーンまたはジョブ個別）

        result = await process(item, db)
        results.append(result)

        reporter.update(
            (i + 1) / len(items),      # 0.0 – 1.0
            f"{i + 1}/{len(items)} processed",
        )

    return {"count": len(results)}
```

**中断ハンドラーの登録**（外部エンジン向け）:

```python
async def run_comfy_generate(reporter, cancel, *, comfy, prompt_id, ...):
    task = asyncio.create_task(comfy.wait_for_image(prompt_id))
    cancel.on_cancel(task.cancel)   # キャンセル時に comfy のポーリングタスクもキャンセル

    result = await task
    return result
```

`on_cancel` ハンドラーは `spooler.cancel()` 内で同期的に呼ばれます。ハンドラーがコルーチンを返した場合、スプーラーは自動的に `asyncio.create_task()` でラップします。

**呼び出し規約:**
- `reporter.update()` は `progress` に `[0.0, 1.0]` の値を期待します。範囲外の値はクランプされます。
- `cancel.raise_if_set()` はすべての重要なループイテレーションの先頭に置くべきです。ブロッキングな呼び出しではなく、安価なフラグチェックです。
- `cancel.pause_checkpoint()` は `await` です — 非同期コードから呼ぶ必要があります。一時停止の粒度が重要な長時間ジョブの内側のループで使用します。

---

## 深掘り: asyncio の内部

### レーンポーズに asyncio.Event を使う理由

ワーカーを一時停止する最も単純な方法はフラグです:

```python
if self._paused:
    await asyncio.sleep(0.1)
    continue
```

これは動作しますが、再開時に最大 100ms のレイテンシが発生し、ポーリングで CPU を無駄にします。スプーラーは代わりに `asyncio.Event` を使用します:

```python
# ポーズ: イベントは「クリア」（セットされていない）
self._lane_events[lane].clear()

# 再開: イベントは「セット」
self._lane_events[lane].set()

# ワーカーは CPU オーバーヘッドゼロで待機
await self._lane_events[lane].wait()
```

`asyncio.Event.wait()` はコルーチンを中断し、イベントループの内部コールバックキューに入れます。`event.set()` が呼ばれるまでコルーチンは再スケジュールされません。再開レイテンシはイベントループの 1 ティック — 通常 1ms 未満です。

同じ設計がジョブ個別のポーズ（`_job_pause_events`）とキャンセルシグナル（Job の `_cancel_event`）にも適用されます。すべてのポーズ/再開/キャンセル操作は `asyncio.Event` のセット/クリアで、ポーリングループではありません。

### pause_checkpoint() の内部構造

`pause_checkpoint()` の完全な実装は、asyncio の一般的なパターンを示しています: **複数のイベントを同時に待ち、最初に発火したものに反応する**。

```python
async def pause_checkpoint(self) -> None:
    self.raise_if_set()
    _paused = False
    while True:
        lane_ok = self._lane_event is None or self._lane_event.is_set()
        job_ok  = self._pause_event is None or self._pause_event.is_set()
        if lane_ok and job_ok:
            break   # 両方のイベントがセット → クリア

        if not _paused:
            _paused = True
            if self._on_pause:
                self._on_pause()   # job.state = PAUSED にして SSE イベントをプッシュ

        # 待機するタスクのリストを構築
        waits = []
        if not lane_ok:
            waits.append(asyncio.create_task(self._lane_event.wait()))
        if not job_ok:
            waits.append(asyncio.create_task(self._pause_event.wait()))
        cancel_task = asyncio.create_task(self._event.wait())
        waits.append(cancel_task)

        done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()   # 負けたタスクをキャンセルしてリークを防ぐ

        if cancel_task in done:
            if _paused and self._on_resume:
                self._on_resume()
            raise JobCancelled()
        # それ以外: レーンまたはジョブが再開された → 両方の条件を再チェック
```

**なぜ `while True` ループ?** `asyncio.wait` は *いずれかの* イベントが発火した時点で返ります。レーンとジョブの両方が一時停止している場合、レーンの再開で wait が起床します — しかしジョブはまだ個別に停止中です。ループが続行前に両方の条件を再評価します。

**なぜイベントごとに `asyncio.create_task()` を使うのか?** `asyncio.wait()` はコルーチンではなく、awaitables を必要とします。`event.wait()` をタスクでラップすることでキャンセル可能になります（`for t in pending: t.cancel()` のクリーンアップに必要）。

負けたタスクをキャンセルしないと、イベントが発火するまで生き続ける宙ぶらりんの asyncio Task が残り、メモリを消費し、`Task was destroyed but it is pending!` 警告をトリガーする可能性があります。

### EWMA ベースの ETA

線形外挿による生の ETA 推定:

```
raw_eta = elapsed × (1 − p) / p
```

ここで `p` は現在の進捗 [0, 1] です。この式は一定のスループットを前提とします — ジョブが 100 アイテムを処理し、5 秒で 10% 完了していれば、残り 45 秒と推定します。

問題はスループットが一定でないことです。WD14 バッチは最初の 10%（小さな画像）を 5 秒で処理し、その後大きな画像で失速するかもしれません。生の ETA は振動します: まず 45 秒、次に 20 秒、次に 60 秒。

EWMA は各新しい推定値を重み付き履歴と組み合わせることでこれを平滑化します:

```
smoothed[0] = raw_eta[0]
smoothed[n] = α × raw_eta[n] + (1 − α) × smoothed[n−1]   (α = 0.4)
```

α = 0.4 では、各新しいサンプルが 40%、以前の履歴が 60% を占めます。直近 5 サンプルが `deque(maxlen=5)` に保持されます。

効果: スループットが突然遅くなった場合、ETA はジャンプせずに徐々に増加します。速くなった場合は徐々に減少します。表示される ETA は読める程度に安定します。

サンプルは `eta_seconds` プロパティアクセス時にのみ計算されます — プロアクティブにプッシュされません。プロパティは `_push_event()` 内の `job.to_dict()` が呼ばれるときに実行されます。

### 履歴: appendleft による LIFO

完了したジョブは `self._history: deque(maxlen=100)` に移動されます:

```python
def _move_to_history(self, job: Job) -> None:
    self._history.appendleft(job)   # 先頭に追加: 最新が先頭
    self._registry.pop(job.id, None)
    self._throttle.pop(job.id, None)
```

`deque.appendleft()` は位置 0（左端）に挿入します。`maxlen=100` の上限と組み合わせることで、直近 100 件の完了ジョブを最新優先の順序で保持する LIFO 構造が生まれます。

`snapshot()` は返す前に、アクティブ + 履歴の合成リストを `created_at` 降順でソートします — 内部的には LIFO 順で保存されていても、アクティブジョブと履歴ジョブの組み合わせに関わらず API は一貫して最新優先で返します。

---

## まとめ: ジョブの完全なライフサイクル

ボタンクリックからコントロールルームのランプ変化まで:

```
1. ユーザーが UI の「Generate」をクリック
   -> POST /api/comfy/... -> spooler.submit(JobLane.GENERATION, ...)

2. submit():
   -> job_id = "gen-000007"
   -> job.state = QUEUED
   -> SSE をプッシュ: job_created
   -> _update_auto_pause() -> Tier 1: EMBED レーンを一時停止
   -> SSE をプッシュ: lane_state (embed: paused, reason=auto)

3. gen-worker が起床（work_event.set()）
   -> キューから gen-000007 を取り出す
   -> lane_events[GEN].is_set() = True -> 即座にゲートを通過
   -> run_with_resource() が local-gpu0 セマフォを取得
   -> job.state = RUNNING
   -> SSE をプッシュ: job_updated

4. ランナーが実行:
   -> 各フレームで cancel.raise_if_set()
   -> reporter.update(0.3, "frame 30/100") -> SSE をプッシュ: job_updated（スロットル付き）
   -> ...

5. ユーザーが「ジョブを一時停止」をクリック
   -> POST /api/jobs/gen-000007/pause
   -> job_pause_ev.clear()
   -> ランナーの次の cancel.pause_checkpoint():
      -> job.state = PAUSED
      -> SSE をプッシュ: job_updated (state=paused)
      -> asyncio.wait([lane_task, job_task, cancel_task]) 内で中断

6. ユーザーが「ジョブを再開」をクリック
   -> POST /api/jobs/gen-000007/resume
   -> job_pause_ev.set()
   -> asyncio.wait() が返る（job_task が完了）
   -> job.state = RUNNING
   -> SSE をプッシュ: job_updated (state=running)
   -> ランナーが続行

7. ランナーが結果を返す
   -> job.state = SUCCEEDED、job.progress = 1.0
   -> GPU セマフォを解放
   -> SSE をプッシュ: job_finished
   -> _move_to_history()
   -> _check_auto_resume() -> Tier 1: EMBED レーンを再開
   -> SSE をプッシュ: lane_state (embed: not paused)

8. フロントエンド:
   -> job_finished イベント -> handleJobFinished() -> スキャンが起動
   -> lane_state イベント -> EMBED ランプ -> NOMINAL
```

---

## 付録: 主要な定数

| 定数 | 値 | 効果 |
|---|---|---|
| `_THROTTLE_INTERVAL` | 0.25 秒 | `job_updated` SSE プッシュの最小間隔（進捗のみの更新） |
| `_HEARTBEAT_INTERVAL` | 15.0 秒 | SSE `: ping` コメントの間隔 |
| `_HISTORY_MAXLEN` | 100 | 履歴 deque に保持する完了ジョブの最大数 |
| リソース統計プッシュ間隔 | 5 秒 | `resource_stats` SSE イベントの送信頻度 |
| リモートヘルスチェック間隔 | 15 秒 | Ollama/Qdrant の生存確認頻度 |
| ローカル統計ポーリング間隔 | 5 秒 | GPU/CPU/RAM メトリクスの読み取り頻度 |
| EWMA α | 0.4 | 最新の ETA サンプルと履歴平均の重み比 |
| ETA サンプルウィンドウ | 5 サンプル | `Job._eta_samples` の `deque(maxlen=5)` |
| フロントエンド SSE バッチウィンドウ | 250 ms | Vue 再レンダリング前の `job_updated` イベントのまとめ期間 |
| フロントエンド再接続遅延 | 3 秒 | エラー時に `EventSource` を再オープンするまでの遅延 |

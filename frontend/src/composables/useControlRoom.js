import { computed, reactive, ref } from 'vue'

// ISA-101 compliant lamp states
export const LampState = Object.freeze({
  NOMINAL:  'nominal',   // normal operation
  ACTIVE:   'active',    // processing
  CAUTION:  'caution',   // warning (queue backlog, etc.)
  FAULT:    'fault',     // abnormal stop
  STANDBY:  'standby',   // idle
  PAUSED:   'paused',    // paused manually
  GUARD:    'guard',     // auto-paused by the spooler to protect the GPU
})

// system definitions — lane-based and resource-based
// lane + resource together: lane drives the status, resource unreachability forces FAULT
const SYSTEMS = [
  { key: 'generation',   label: 'Generation',   lane: 'gen'   },
  { key: 'tagging',      label: 'Tagging',       lane: 'tagging' },
  { key: 'embedding',    label: 'Embedding',     lane: 'embed' },
  { key: 'vectorStore',  label: 'Vector Store',  resource: 'remote-qdrant' },
  { key: 'alignment',    label: 'Alignment',     lane: 'eval'  },
  { key: 'promptEngine', label: 'Prompt Engine', lane: 'prompt', resource: 'remote-ollama' },
]

const MAX_LOG_ENTRIES = 200
const CAUTION_QUEUE_THRESHOLD = 3

// sparkline bar characters (low → high)
const SPARK_CHARS = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']

function toHHMMSS(ts) {
  const d = new Date(ts * 1000)
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(n => String(n).padStart(2, '0'))
    .join(':')
}

/**
 * Control Room state management composable.
 *
 * @param {import('vue').Ref<Map<string, object>>} jobsMap - job map updated via SSE
 * @param {import('vue').Ref<object[]>} resourcesRef - resource array updated via resource_stats
 */
export function useControlRoom(jobsMap, resourcesRef) {
  const eventLog = ref(/** @type {Array<{ts: number, text: string, level: 'info'|'done'|'fault'}>} */ ([]))
  const completionTimes = ref(/** @type {number[]} */ ([]))  // completion epochs (for throughput calculation)

  // ── lane pause state ──────────────────────────────────────────────────────
  // { embed: {paused: true, pause_reason: 'auto'}, ... }
  const laneStates = ref({})

  // ── system status ─────────────────────────────────────────────────────────────

  const systemStatus = computed(() => {
    const jobs = Array.from(jobsMap.value.values())
    const resources = resourcesRef.value

    return SYSTEMS.reduce((acc, sys) => {
      if (sys.lane) {
        // backing resource down (e.g. Ollama unreachable) trumps lane activity
        if (sys.resource) {
          const res = resources.find(r => r.name === sys.resource)
          if (res && !res.reachable) { acc[sys.key] = LampState.FAULT; return acc }
        }
        const laneJobs = jobs.filter(j => j.id.startsWith(sys.lane + '-'))
        const running  = laneJobs.filter(j => j.state === 'running' || j.state === 'cancelling')
        const queued   = laneJobs.filter(j => j.state === 'queued')
        const failed   = laneJobs.filter(j => j.state === 'failed')

        if (failed.length > 0)  { acc[sys.key] = LampState.FAULT;  return acc }
        if (running.length > 0) { acc[sys.key] = LampState.ACTIVE; return acc }

        const ls = laneStates.value[sys.lane]
        if (ls?.paused) {
          // auto pause = GPU protection working as designed — never a CAUTION
          if (ls.pause_reason === 'auto') { acc[sys.key] = LampState.GUARD; return acc }
          // manual pause with a backlog is worth flagging
          acc[sys.key] = queued.length > 0 ? LampState.CAUTION : LampState.PAUSED
          return acc
        }

        if (queued.length >= CAUTION_QUEUE_THRESHOLD) { acc[sys.key] = LampState.CAUTION; return acc }
        if (queued.length > 0)                        { acc[sys.key] = LampState.ACTIVE;  return acc }
        acc[sys.key] = LampState.NOMINAL
      } else if (sys.resource) {
        // check resource reachability
        const res = resources.find(r => r.name === sys.resource)
        if (!res) { acc[sys.key] = LampState.STANDBY; return acc }
        if (!res.reachable) { acc[sys.key] = LampState.FAULT; return acc }
        acc[sys.key] = LampState.NOMINAL
      }
      return acc
    }, {})
  })

  // ── master status ─────────────────────────────────────────────────────────────
  // 5 plant states: STARTING → STANDBY → RUNNING → CAUTION → FAULT

  const masterStatus = computed(() => {
    const states = Object.values(systemStatus.value)
    const resources = resourcesRef.value

    // starting: remote resource has never connected (last_ok == null)
    const anyStarting = resources.some(r => r.kind === 'remote' && !r.reachable && r.last_ok == null)
    if (anyStarting) return 'STARTING'

    if (states.includes(LampState.FAULT))   return 'FAULT'
    if (states.includes(LampState.CAUTION)) return 'CAUTION'
    if (states.includes(LampState.ACTIVE))  return 'RUNNING'
    return 'STANDBY'
  })

  // ── active jobs (for display) ─────────────────────────────────────────────────

  const activeJobs = computed(() => {
    const jobs = Array.from(jobsMap.value.values())
    const active = jobs.filter(j =>
      j.state === 'running' || j.state === 'paused' ||
      j.state === 'cancelling' || j.state === 'failed'
    )
    // held(pause gate) → running/paused → cancelling → failed
    const order = { running: 0, paused: 0, cancelling: 1, failed: 2 }
    return active.sort((a, b) => {
      if (a.held && !b.held) return -1
      if (!a.held && b.held) return 1
      const so = (order[a.state] ?? 9) - (order[b.state] ?? 9)
      if (so !== 0) return so
      return a.created_at - b.created_at
    })
  })

  const waitedJobs = computed(() => {
    const jobs = Array.from(jobsMap.value.values())
    return jobs
      .filter(j => j.state === 'queued')
      .sort((a, b) => {
        if (a.held && !b.held) return 1
        if (!a.held && b.held) return -1
        return (b.priority ?? 0) - (a.priority ?? 0) || a.created_at - b.created_at
      })
  })

  // ── P&ID lane observables ─────────────────────────────────────────────────────

  const laneActiveJob = (prefix) => computed(() => {
    const jobs = Array.from(jobsMap.value.values())
    return jobs.find(j =>
      j.id.startsWith(prefix + '-') &&
      (j.state === 'running' || j.state === 'cancelling')
    ) ?? null
  })

  const laneQueueDepth = (prefix) => computed(() => {
    const jobs = Array.from(jobsMap.value.values())
    return jobs.filter(j => j.id.startsWith(prefix + '-') && j.state === 'queued').length
  })

  const promptActiveJob  = laneActiveJob('prompt')
  const promptQueueDepth = laneQueueDepth('prompt')
  const genActiveJob    = laneActiveJob('gen')
  const genQueueDepth   = laneQueueDepth('gen')
  const tagActiveJob    = laneActiveJob('tagging')
  const tagQueueDepth   = laneQueueDepth('tagging')
  const embedActiveJob  = laneActiveJob('embed')
  const embedQueueDepth = laneQueueDepth('embed')
  const evalActiveJob   = laneActiveJob('eval')
  const evalQueueDepth  = laneQueueDepth('eval')

  // ── GPU guard (spooler auto-pause protecting the GPU) ────────────────────────

  const guardActive = computed(() =>
    Object.values(laneStates.value).some(ls => ls?.paused && ls.pause_reason === 'auto')
  )

  // which priority lane is holding the guard: "GEN" / "PE" / "GEN+PE"
  const guardSourceLabel = computed(() => {
    const jobs = Array.from(jobsMap.value.values())
    const busy = prefix => jobs.some(j =>
      j.id.startsWith(prefix + '-') && (j.state === 'running' || j.state === 'cancelling'))
    const parts = []
    if (busy('gen'))    parts.push('GEN')
    if (busy('prompt')) parts.push('PE')
    return parts.join('+') || 'AUTO'
  })

  // ── P&ID resource binding ─────────────────────────────────────────────────────
  // GEN: prefers remote-comfyui (separate machine), falls back to local-gpu0
  const genResource = computed(() => {
    const r = resourcesRef.value
    return r.find(x => x.name === 'remote-comfyui') ?? r.find(x => x.kind === 'local') ?? null
  })

  // EMBED: prefers remote-ollama, falls back to local-gpu0
  const embedResource = computed(() => {
    const r = resourcesRef.value
    return r.find(x => x.name === 'remote-ollama') ?? r.find(x => x.kind === 'local') ?? null
  })

  // EVAL: same as embed (uses Ollama); local mode → show CPU stats
  const evalResource = computed(() => {
    const r = resourcesRef.value
    return r.find(x => x.name === 'remote-ollama') ?? r.find(x => x.kind === 'local') ?? null
  })

  // TAG: CPU-only lane, always bound to local host stats
  const tagResource = computed(() =>
    resourcesRef.value.find(x => x.kind === 'local') ?? null
  )

  // PROMPT: LLM via Ollama; local mode → show GPU stats
  const promptResource = computed(() => {
    const r = resourcesRef.value
    return r.find(x => x.name === 'remote-ollama') ?? r.find(x => x.kind === 'local') ?? null
  })

  // ── throughput (completions in last 1 minute) ────────────────────────────────

  const throughput = computed(() => {
    const now = Date.now() / 1000
    const recent = completionTimes.value.filter(t => now - t < 60)
    return recent.length
  })

  // ── sparkline (last 10 minutes binned by minute) ─────────────────────────────

  const sparkline = computed(() => {
    const now = Date.now() / 1000
    const bins = Array(10).fill(0)
    for (const t of completionTimes.value) {
      const age = now - t
      if (age < 600) {
        const bin = Math.min(9, Math.floor(age / 60))
        bins[9 - bin]++
      }
    }
    const max = Math.max(...bins, 1)
    return bins.map(v => SPARK_CHARS[Math.round((v / max) * (SPARK_CHARS.length - 1))]).join('')
  })

  // ── total processed count (within session) ───────────────────────────────────

  const totalProcessed = ref(0)

  // ── resources (remote / local separated) ────────────────────────────────────

  const localResources = computed(() =>
    resourcesRef.value.filter(r => r.kind === 'local')
  )

  const remoteResources = computed(() =>
    resourcesRef.value.filter(r => r.kind === 'remote')
  )

  // ── event ingestion (called from App.vue SSE handler) ────────────────────────

  const _prevReachable = reactive({})

  function ingestEvent(type, data) {
    const now = Math.floor(Date.now() / 1000)

    if (type === 'job_created') {
      _appendLog(now, `${data.id}  started`, 'info')
    }

    if (type === 'job_finished') {
      if (data.state === 'succeeded') {
        completionTimes.value.push(now)
        if (completionTimes.value.length > 600) completionTimes.value.shift()
        totalProcessed.value++
        _appendLog(now, `${data.id}  done`, 'done', {
          jobId: data.id,
          sha256s: data.result?.sha256s || [],
        })
      } else if (data.state === 'failed') {
        _appendLog(now, `${data.id}  failed${data.error ? ': ' + data.error : ''}`, 'fault')
      } else if (data.state === 'cancelled') {
        _appendLog(now, `${data.id}  cancelled`, 'info')
      }
    }

    if (type === 'resource_stats' && data.resources) {
      for (const res of data.resources) {
        const prev = _prevReachable[res.name]
        if (prev !== undefined && prev !== res.reachable) {
          const txt = res.reachable
            ? `${res.name}  reconnected`
            : `${res.name}  unreachable`
          _appendLog(now, txt, res.reachable ? 'done' : 'fault')
        }
        _prevReachable[res.name] = res.reachable
      }
    }

    // snapshot: count initial completions
    if (type === 'snapshot' && data.jobs) {
      const succeeded = data.jobs.filter(j => j.state === 'succeeded')
      totalProcessed.value = succeeded.length
      // record initial reachable state of resources (to avoid spurious connection events)
      if (data.resources) {
        for (const res of data.resources) {
          _prevReachable[res.name] = res.reachable
        }
      }
    }

    // update pause state from the lanes field of lane_state / snapshot events
    if ((type === 'lane_state' || type === 'snapshot') && data.lanes) {
      const prev = laneStates.value
      const ns = {}
      for (const ls of data.lanes) {
        ns[ls.lane] = { paused: ls.paused, pause_reason: ls.pause_reason }
      }
      laneStates.value = ns
      if (type === 'lane_state') {
        // log only transitions — lane_state carries the full lane list every time
        for (const ls of data.lanes) {
          const wasPaused = prev[ls.lane]?.paused ?? false
          if (ls.paused && !wasPaused) {
            const txt = ls.pause_reason === 'auto'
              ? `${ls.lane} lane held (GPU guard)`
              : `${ls.lane} lane paused (manual)`
            _appendLog(now, txt, 'info')
          } else if (!ls.paused && wasPaused) {
            _appendLog(now, `${ls.lane} lane resumed`, 'info')
          }
        }
      }
    }
  }

  function _appendLog(ts, text, level, extra = {}) {
    eventLog.value.unshift({ ts, text: `${toHHMMSS(ts)}  ${text}`, level, ...extra })
    if (eventLog.value.length > MAX_LOG_ENTRIES) {
      eventLog.value.length = MAX_LOG_ENTRIES
    }
  }

  return {
    SYSTEMS,
    systemStatus,
    masterStatus,
    activeJobs,
    waitedJobs,
    eventLog,
    throughput,
    sparkline,
    totalProcessed,
    localResources,
    remoteResources,
    laneStates,
    guardActive,
    guardSourceLabel,
    ingestEvent,
    promptActiveJob,
    promptQueueDepth,
    genActiveJob,
    genQueueDepth,
    tagActiveJob,
    tagQueueDepth,
    embedActiveJob,
    embedQueueDepth,
    evalActiveJob,
    evalQueueDepth,
    promptResource,
    genResource,
    tagResource,
    embedResource,
    evalResource,
  }
}

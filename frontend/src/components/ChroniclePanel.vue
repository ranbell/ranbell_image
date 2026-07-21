<script setup>
import { ref, computed, watch, onMounted, onUnmounted, onErrorCaptured } from 'vue'
import { useI18n } from 'vue-i18n'
import { EMOTION_DIMENSIONS } from '../composables/useInvokeSession.js'
import SbIcon from './SbIcon.vue'
import StoryQualityRadar from './StoryQualityRadar.vue'

const { t, te, locale } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
  baseImage: { type: Object, default: null },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, required: true },
})
const emit = defineEmits(['update:show', 'toast', 'open-storybook'])

const AXES = ['panel_1', 'panel_2', 'panel_3']
const TIME_SCALES = ['minutes', 'tens_of_minutes', 'hours', 'days', 'months', 'years', 'decades']

const PHASE_STEP = {
  loadingImage: 0, extractingVision: 0,
  candidates: 1, storyArc: 1, storyboarding: 1, buildingProfile: 1,
  enhancingPrompts: 4,
  selecting: 2,
  expanding: 3, repairingStory: 3, translating: 3,
  mutatingTags: 3, buildingBiography: 3, buildingTimetable: 3,
  concretizing: 3, differentiating: 3, writingStory: 3,
  preparingActs: 3,
  taggingAxis: 4, examining: 4, refiningPrompt: 4,
  refiningPromptTags: 4, refiningPromptProse: 4,
  retrievingPose: 4, polishingScript: 4, assemblingPrompt: 4,
  draftingAxis: 4, scanningDraft: 4,
  savingStory: 5, done: 5,
}

// ── form state ────────────────────────────────────────────────────────────────
const baseSha = ref('')
const baseModel = ref('')
function _modelOf(doc) {
  return doc?.model_name || doc?.model_info?.model_name || ''
}
const userTopic = ref('')
const characterTags = ref('')
const includeHappening = ref(false)
const authorStyle = ref('')
const authorId = ref('')
const authorsList = ref([])
const customTagsPanel1 = ref('')
const customTagsPanel2 = ref('')
const customTagsPanel3 = ref('')
const workflows = ref([])
const workflow = ref('')
const temperature = ref(0.7)
const numCtx = ref(32768)
const llmProvider = ref('ollama')
const openaiModel = ref('')
const useRefSeed = ref(true)
const manualMode = ref(false)
const pendingAutoSelect = ref('')
const pendingExpandTimeScale = ref('')

// Stubs for legacy template sections still present (hidden / unused in payload)
const lifeRole = ref('custom')
const LIFE_ROLES = ['custom']
const keywordsPast = ref('')
const keywordsPresent = ref('')
const keywordsFuture = ref('')
const composeAllowlist = ref(false)
const worldview = ref('')
const promptStyle = ref('danbooru+natural')
const divergence = ref(0)
const emotion = ref('')
const DRAMATIC_MODES = []
const dramaticMode = ref('')
const TONES = ['bright', 'neutral', 'dark']
const tone = ref('neutral')
const timeScaleIdx = ref(3)
const fastMode = ref(false)
const generatePinup = ref(false)
const baseAxis = ref('panel_2')
function currentTimeScale() {
  return TIME_SCALES[timeScaleIdx.value] || 'days'
}
// Deprecated server-side (mechanical mutex rules replaced the LLM conflict
// pass), so there is no UI for it — still sent so the request shape is stable.
const suppressConflictTags = ref(true)
const useDraftRefine = ref('auto') // auto | on | off
const draftWidth = ref(512)
const draftHeight = ref(512)
const draftSteps = ref(12)
const proseParagraphs = ref(3)
const pickingRandom = ref(false)
const wd14PromptSpice = ref(false)
const similarTagMix = ref(true)
const similarTagMixRatio = ref(0.3)
const biography = ref(null)
const timetable = ref(null)
const concrete = ref(null)
const axisReasoning = ref({})
const axisDrafts = ref({})
const qualityEval = ref(null)
const phaseTimings = ref([]) // [{ code, duration_ms }]
const phaseStartedAt = ref(null)
const nowTick = ref(Date.now())
let _phaseTickTimer = null
const pinupJobId = ref('')

// Shortcuts for the 自然文 slider, shown next to it in 出力・生成.
const PROSE_PRESETS = [
  { id: 'prose_short', labelKey: 'chronicle.knobProseShort', prose: 3 },
  { id: 'prose_long', labelKey: 'chronicle.knobProseLong', prose: 7 },
]

function applyProsePreset(preset) {
  proseParagraphs.value = preset.prose
}

const weaverTeasers = computed(() =>
  AXES.map((axis) => {
    const text = displayAxisStory(axis) || ''
    const snippet = text.replace(/\s+/g, ' ').trim().slice(0, 72)
    return { axis, snippet, ready: !!snippet }
  }).filter((x) => x.ready)
)

function chronicleQualityActions() {
  if (!qualityEvalHasRadar.value) return []
  const dims = qualityEval.value?.dimensions || {}
  const weak = Object.entries(dims)
    .map(([k, v]) => ({ k, v: Number(v) }))
    .filter((d) => Number.isFinite(d.v) && d.v < 0.55)
    .sort((a, b) => a.v - b.v)
  const actions = []
  if (weak.some((d) => d.k === 'topic_fit' || d.k === 'diversity')) {
    actions.push({
      id: 'respin-expand',
      label: t('chronicle.qualityAction.respin'),
      run: () => respin('expand'),
    })
  }
  if (weak.some((d) => d.k === 'richness' || d.k === 'drawability')) {
    actions.push({
      id: 'prose-up',
      label: t('chronicle.qualityAction.proseLonger'),
      run: () => {
        proseParagraphs.value = Math.min(7, (proseParagraphs.value || 5) + 2)
        respin('expand')
      },
    })
  }
  if (weak.some((d) => d.k === 'diversity')) {
    actions.push({
      id: 'diverge-up',
      label: t('chronicle.qualityAction.moreDiverge'),
      run: () => {
        divergence.value = Math.min(1, divergence.value + 0.25)
        respin('candidates')
      },
    })
  }
  return actions.slice(0, 3)
}

const uiLocale = computed(() => (locale.value?.startsWith('ja') ? 'ja' : 'en'))

const thumbFailed = ref(false)
watch(baseSha, async (sha) => {
  thumbFailed.value = false
  if (sha && !baseModel.value) {
    try {
      const r = await fetch(`/api/images/${sha}`)
      if (r.ok) baseModel.value = _modelOf(await r.json())
    } catch {}
  }
})
const baseThumbSrc = computed(() =>
  thumbFailed.value
    ? `/api/originals/${baseSha.value}`
    : `/api/thumbnails/${baseSha.value}.webp`
)

// ── run state ─────────────────────────────────────────────────────────────────
const running = ref(false)
/** True from select/respin-expand until done/error — keeps panel open even if
 *  the SSE stream flaps during long silent LLM phases (concretizing). */
const expandSessionActive = ref(false)
const streamTerminal = ref(false)
const phase = ref('')
const progress = ref(0)
const streamText = ref('')
const storyId = ref('')
const groupId = ref('')
const seed = ref(null)
const prompts = ref({})
const imageJobs = ref([])
const finished = ref(false)
const errorMsg = ref('')
const renderError = ref('')

onErrorCaptured((err) => {
  // Keep the shell mounted; a child render throw used to blank the Teleport.
  renderError.value = String(err?.message || err)
  console.error('[ChroniclePanel] render error:', err)
  return false
})
const title = ref('')
const titleJa = ref('')
const overall = ref('')
const overallJa = ref('')
const mutationTags = ref([])
const storySeedTags = ref([])
const storySeedMotif = ref('')

const candidates = ref([])
const selecting = ref(false)
const selectedCandidate = ref('')
const respinCandCount = ref(0)
const respinExpandCount = ref(0)

const panelLang = ref(uiLocale.value)
watch(uiLocale, (l) => { panelLang.value = l })
const displayTitle = computed(() =>
  (panelLang.value === 'ja' && titleJa.value) ? titleJa.value : title.value
)
const displayOverall = computed(() =>
  (panelLang.value === 'ja' && overallJa.value) ? overallJa.value : overall.value
)

/** Past / present / future prose from expand (EN canonical + optional JA). */
const axisStories = ref({ panel_1: '', panel_2: '', panel_3: '' })
const axisStoriesJa = ref({ panel_1: '', panel_2: '', panel_3: '' })
function displayAxisStory(axis) {
  const en = axisStories.value[axis] || ''
  const ja = axisStoriesJa.value[axis] || ''
  if (panelLang.value === 'ja') return ja || en
  return en || ja
}
const hasAxisStories = computed(() =>
  AXES.some(a => !!(axisStories.value[a] || axisStoriesJa.value[a]))
)
/** Show expand/exec stream openly until structured acts arrive (any post-select step).
 *  Do NOT gate on currentStep===3: once examining/prompt phases start, step becomes
 *  4 and a truncated expand (no [PAST]/… markers) would otherwise collapse into a
 *  closed <details> — looking like the expand window vanished.
 *  Also open during Phase1 storyboarding so Stage1 attempt/retry logs are visible. */
const showLiveStream = computed(() =>
  !!streamText.value && !hasAxisStories.value && !selecting.value
  && (running.value || (!finished.value && currentStep.value >= 3))
)
function _axesFromMap(src) {
  const out = {}
  for (const a of AXES) {
    const v = src?.[a]
    if (typeof v === 'string' && v.trim()) out[a] = v.trim()
  }
  return out
}
function _mergeAxisStories(enPatch, jaPatch) {
  if (enPatch && Object.keys(enPatch).length) {
    axisStories.value = { ...axisStories.value, ...enPatch }
  }
  if (jaPatch && Object.keys(jaPatch).length) {
    axisStoriesJa.value = { ...axisStoriesJa.value, ...jaPatch }
  }
}

const currentStep = computed(() => {
  if (finished.value) return 5
  if (selecting.value) return 2
  return PHASE_STEP[phase.value] ?? 0
})

// Keep the full left-hand settings (workflow, topic, dials) visible at every
// pipeline step — do not collapse them into a summary after Weave starts.
const settingsLocked = computed(() => running.value)

const canGenerate = computed(() =>
  finished.value && !!storyId.value && !imageJobs.value.length
)

/** Candidates are authored in EN; a batched ja translation rides on the
 *  candidate as `*_ja` / `acts_ja`. Prefer it when the UI is Japanese. */
function candDisplay(c, field) {
  if (uiLocale.value === 'ja' && c?.[`${field}_ja`]) return c[`${field}_ja`]
  return c?.[field] || ''
}
function candAct(c, ax) {
  const panels = c?.panels || {}
  const p = panels[ax] || {}
  if (p.narrative_ja || p.narrative_en || p.act) {
    return {
      label: p.act || ax,
      activity: p.narrative_ja || p.narrative_en || '',
      place: '',
      feeling: '',
      outfit: '',
      camera: p.camera || '',
      gesture: p.gesture || '',
    }
  }
  // Legacy fallback
  const acts = (panelLang.value === 'ja' && c?.acts_ja) ? c.acts_ja : (c?.acts || {})
  const a = acts[ax] || {}
  return {
    label: a.label || '',
    activity: a.activity || '',
    place: a.place || '',
    feeling: a.feeling || '',
    outfit: a.outfit || '',
  }
}

const visibleCandidates = computed(() => {
  if (!candidates.value.length) return []
  if (selecting.value || !selectedCandidate.value) return candidates.value
  return candidates.value.filter(c => c.id === selectedCandidate.value)
})

const showImageProgress = computed(() => imageJobs.value.length > 0)
const showPipelineProgress = computed(() =>
  !showImageProgress.value && (running.value || (finished.value && progress.value > 0))
)

/** Cute loom animation while the long pipeline (or image jobs) are busy. */
const isBusyWeaving = computed(() =>
  running.value || expandSessionActive.value || !!imageGen.value.active
)
const showStatusHud = computed(() =>
  running.value
  || showPipelineProgress.value
  || showImageProgress.value
  || phaseTimings.value.length > 0
)
const showWeaverStage = computed(() => {
  if (!isBusyWeaving.value) return false
  // Full-stage weaver only when the right pane is still mostly empty.
  // Draft notes / live stream / candidates must stay visible during expand
  // (especially around "Pinning down the action").
  if (visibleCandidates.value.length) return false
  if (displayTitle.value) return false
  if (hasDraftMaterials.value) return false
  if (hasAxisStories.value) return false
  if (streamText.value) return false
  if (Object.keys(prompts.value).length) return false
  return true
})
const phaseLabel = computed(() => {
  if (!phase.value) return t('chronicle.running')
  const key = 'chronicle.phase.' + phase.value
  return te(key) ? t(key) : phase.value
})
const weaverCaption = computed(() => {
  if (imageGen.value.active) return t('chronicle.weaverImages')
  if (phase.value) return t('chronicle.weaverPhase', { phase: phaseLabel.value })
  return t('chronicle.weaverBusy')
})

function formatDurationMs(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return '—'
  return `${(n / 1000).toFixed(1)}s`
}

function phaseCodeLabel(code) {
  if (!code) return ''
  const key = 'chronicle.phase.' + code
  return te(key) ? t(key) : code
}

function recordPhaseTiming(code, duration_ms) {
  if (!code || duration_ms == null) return
  const ms = Number(duration_ms)
  if (!Number.isFinite(ms)) return
  const list = [...phaseTimings.value]
  const last = list[list.length - 1]
  // Heartbeats of the same code: update in place, don't double-count.
  if (last && last.code === code) {
    list[list.length - 1] = { code, duration_ms: ms }
  } else {
    list.push({ code, duration_ms: ms })
  }
  phaseTimings.value = list
}

function startPhaseTick() {
  if (_phaseTickTimer) return
  nowTick.value = Date.now()
  _phaseTickTimer = setInterval(() => { nowTick.value = Date.now() }, 250)
}

function stopPhaseTick() {
  if (_phaseTickTimer) {
    clearInterval(_phaseTickTimer)
    _phaseTickTimer = null
  }
}

/** Timing rows for Status HUD — completed phases + live active phase. */
const statusHudRows = computed(() => {
  void nowTick.value
  const rows = phaseTimings.value.map((r) => ({
    code: r.code,
    duration_ms: r.duration_ms,
    active: false,
    label: phaseCodeLabel(r.code),
  }))
  if (phase.value && phaseStartedAt.value && (running.value || expandSessionActive.value)) {
    rows.push({
      code: phase.value,
      duration_ms: Math.max(0, nowTick.value - phaseStartedAt.value),
      active: true,
      label: phaseLabel.value,
    })
  }
  return rows
})

const qualityEvalHasRadar = computed(() => {
  const q = qualityEval.value
  if (!q || q.ok === false) return false
  return !!(q.dimensions && typeof q.dimensions === 'object')
})
const qualityEvalFailed = computed(() => {
  const q = qualityEval.value
  return !!(q && (q.ok === false || q.error))
})
const qualityOverallPct = computed(() => {
  const o = qualityEval.value?.overall
  if (o == null || Number.isNaN(Number(o))) return null
  return Math.round(Math.max(0, Math.min(1, Number(o))) * 100)
})

function slotUsedAs(s) {
  const u = s?.used_as
  return AXES.includes(u) ? u : ''
}
function slotNeighbors(s) {
  const n = s?.used_as_neighbor
  if (!Array.isArray(n)) return []
  return n.filter((a) => AXES.includes(a))
}

/** Keep the panel open during pipeline / image jobs — ignore Esc & backdrop. */
const stayOpen = computed(() => {
  if (running.value) return true
  // Mid-expand (incl. silent "Pinning down the action") — never auto-dismiss.
  if (expandSessionActive.value) return true
  // Truncation / pipeline / render errors must leave the panel readable.
  if (errorMsg.value || renderError.value) return true
  // image_jobs may arrive a tick before the 2s poll fills imageGen.states.
  if (imageJobs.value.length) {
    if (imageGen.value.active) return true
    const states = imageGen.value.states || {}
    if (!Object.keys(states).length) return true
    return Object.values(states).some(s => s === 'queued' || s === 'running')
  }
  if (imageGen.value.active) return true
  const states = imageGen.value.states || {}
  return Object.values(states).some(s => s === 'queued' || s === 'running')
})

// Incremental tag index over the append-only stream. Re-running full-text
// regexes on every 64ms flush is O(n²) in stream length and can stall the tab
// on long weaves, so only the newly appended chunk is scanned for tag markers.
const _STREAM_TAGS = ['TITLE', 'OVERALL', 'PAST', 'PRESENT', 'FUTURE']
let _tagScanPos = 0
let _tagPos = {}

function _resetStreamTagIndex() { _tagScanPos = 0; _tagPos = {} }

// Body of a tagged section: text after the tag's line, up to the next tag.
function _streamBody(text, tag) {
  const start = _tagPos[tag]
  if (start == null) return null
  const nl = text.indexOf('\n', start)
  if (nl < 0) return null
  let end = text.length
  for (const t of _STREAM_TAGS) {
    const p = _tagPos[t]
    if (p != null && p > start && p < end) end = p
  }
  return text.slice(nl + 1, end).trim()
}

watch(streamText, (text) => {
  if (text.length < _tagScanPos) _resetStreamTagIndex()
  const re = /\[(TITLE|OVERALL|PAST|PRESENT|FUTURE)\]/gi
  // Back up past the longest marker in case one straddles a flush boundary.
  re.lastIndex = Math.max(0, _tagScanPos - 12)
  let m
  while ((m = re.exec(text))) {
    const k = m[1].toUpperCase()
    if (_tagPos[k] == null) _tagPos[k] = m.index
  }
  _tagScanPos = text.length
  if (!title.value) {
    const body = _streamBody(text, 'TITLE')
    const line = body?.split('\n', 1)[0].trim()
    if (line) title.value = line.replace(/^["「]|["」]$/g, '')
  }
  if (!overall.value) {
    const body = _streamBody(text, 'OVERALL')
    if (body) overall.value = body
  }
  // Provisional axis prose from the live expand stream (request locale).
  const patch = {}
  for (const axis of AXES) {
    const cur = uiLocale.value === 'ja' ? axisStoriesJa.value[axis] : axisStories.value[axis]
    if (cur) continue
    const body = _streamBody(text, axis.toUpperCase())
    if (body) patch[axis] = body
  }
  if (Object.keys(patch).length) {
    if (uiLocale.value === 'ja') _mergeAxisStories(null, patch)
    else _mergeAxisStories(patch, null)
  }
})

let _reader = null
let _pendingTokens = ''
let _flushTimer = null
let _backdropArmed = false
let _ignoreDismissUntil = 0

function _flushTokens() {
  if (_pendingTokens) {
    streamText.value += _pendingTokens
    _pendingTokens = ''
  }
}

function _scheduleFlush() {
  if (!_flushTimer) {
    _flushTimer = setTimeout(() => { _flushTimer = null; _flushTokens() }, 64)
  }
}

watch(() => props.baseImage, (doc) => {
  if (!doc?.sha256) return
  // Only reset on a real base change, or a fresh weave after a finished run.
  // Never reset merely because storyId is set — openChronicle() always emits a
  // new object ref for the same sha, and that used to wipe the expand pane
  // mid-pipeline (around "Pinning down the action").
  const shaChanged = !!(baseSha.value && baseSha.value !== doc.sha256)
  const reweaveSame = finished.value && !running.value && !expandSessionActive.value
  if (shaChanged || reweaveSame) resetRun()
  baseSha.value = doc.sha256
  baseModel.value = _modelOf(doc)
}, { immediate: true })

watch(() => props.show, async (val) => {
  if (!val) {
    _backdropArmed = false
    return
  }
  // Ignore backdrop/Esc dismiss for one tick after open (same click that opened
  // the panel must not close it; also clears underlay races with gallery detail).
  _ignoreDismissUntil = performance.now() + 400
  if (props.baseImage?.sha256) {
    baseSha.value = props.baseImage.sha256
    baseModel.value = _modelOf(props.baseImage)
  } else if (finished.value) {
    // Topic-only / no-base reopen after a finished run → fresh start.
    resetRun()
  }
  panelLang.value = uiLocale.value
  if (!workflows.value.length) {
    try {
      const r = await fetch('/api/comfy/workflows')
      if (r.ok) workflows.value = await r.json()
    } catch {}
  }
  if (!authorsList.value.length) {
    try {
      const r = await fetch('/api/authors')
      if (r.ok) {
        const data = await r.json()
        authorsList.value = data.authors || []
      }
    } catch {}
  }
})

function onAuthorPresetChange() {
  const row = authorsList.value.find((a) => a.id === authorId.value)
  if (row?.style_description) authorStyle.value = row.style_description
}

function _dismissBlocked() {
  // Re-sample live job state before deciding: `stayOpen` otherwise reads the 2s
  // poll snapshot, which can lag behind image jobs that started right after the
  // pipeline finished (running→false) — leaving a window where a stray Esc /
  // backdrop click closes the panel mid image-generation.
  _sampleImageGen()
  return stayOpen.value || performance.now() < _ignoreDismissUntil
}

function onKey(e) {
  if (!props.show) return
  if (e.key !== 'Escape') return
  if (_dismissBlocked()) {
    e.preventDefault()
    e.stopPropagation()
    return
  }
  close()
  e.preventDefault()
}

function onBackdropDown(e) {
  if (e.target === e.currentTarget) _backdropArmed = true
  else _backdropArmed = false
}

function onBackdropUp(e) {
  const armed = _backdropArmed
  _backdropArmed = false
  if (!armed || e.target !== e.currentTarget) return
  if (_dismissBlocked()) return
  close()
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  if (_flushTimer) { clearTimeout(_flushTimer); _flushTimer = null }
  _stopImageGenMonitor()
  stopPhaseTick()
  _reader?.cancel().catch(() => {})
})

function close() {
  expandSessionActive.value = false
  emit('update:show', false)
}

function resetStory({ keepDraftNotes = false } = {}) {
  streamText.value = ''
  _resetStreamTagIndex()
  prompts.value = {}
  imageJobs.value = []
  finished.value = false
  seed.value = null
  title.value = ''
  titleJa.value = ''
  overall.value = ''
  overallJa.value = ''
  axisStories.value = { panel_1: '', panel_2: '', panel_3: '' }
  axisStoriesJa.value = { panel_1: '', panel_2: '', panel_3: '' }
  mutationTags.value = []
  storySeedTags.value = []
  storySeedMotif.value = ''
  // Keep bio/timetable across select→expand so the pane stays populated while
  // "Pinning down the action" / timetable rebuild runs (SSE overwrites later).
  if (!keepDraftNotes) {
    biography.value = null
    timetable.value = null
  }
  concrete.value = null
  axisReasoning.value = {}
  axisDrafts.value = {}
  qualityEval.value = null
  pinupJobId.value = ''
  _stopImageGenMonitor()
  imageGen.value = { progress: 0, active: false, text: '', states: {} }
}

function resetRun() {
  phase.value = ''
  progress.value = 0
  errorMsg.value = ''
  renderError.value = ''
  expandSessionActive.value = false
  streamTerminal.value = false
  storyId.value = ''
  candidates.value = []
  selecting.value = false
  selectedCandidate.value = ''
  pendingAutoSelect.value = ''
  pendingExpandTimeScale.value = ''
  respinCandCount.value = 0
  respinExpandCount.value = 0
  phaseTimings.value = []
  phaseStartedAt.value = null
  stopPhaseTick()
  resetStory()
}

function jobState(job_id) {
  return imageGen.value.states[job_id] ?? 'queued'
}
function jobStatusIcon(job_id) {
  return {
    queued: 'clock',
    running: 'spark',
    succeeded: 'check',
    failed: 'close',
    cancelling: 'close',
  }[jobState(job_id)] ?? 'clock'
}
function jobStatusLabel(job_id) {
  return t('chronicle.jobState.' + jobState(job_id), jobState(job_id))
}
function jobStatusClass(job_id) {
  return {
    queued:     'border-white/10 bg-black/30 text-[var(--sb-muted)]',
    running:    'border-teal-700/50 bg-teal-950/40 text-teal-200',
    succeeded:  'border-emerald-700/40 bg-emerald-950/30 text-emerald-300',
    failed:     'border-red-700/40 bg-red-950/30 text-red-300',
    cancelling: 'border-white/10 bg-black/30 text-[var(--sb-faint)]',
  }[jobState(job_id)] ?? 'border-white/10 bg-black/30 text-[var(--sb-muted)]'
}

// Snapshot via getJobsMap() only inside the sampler — never in template/computed.
const imageGen = ref({ progress: 0, active: false, text: '', states: {} })
let _imgTimer = null
function _sampleImageGen() {
  const ids = imageJobs.value.map(j => j.job_id)
  if (pinupJobId.value) ids.push(pinupJobId.value)
  if (!ids.length) {
    imageGen.value = { progress: 0, active: false, text: '', states: {} }
    return
  }
  const map = props.getJobsMap()
  let sum = 0, active = false, text = ''
  const states = {}
  for (const id of ids) {
    const j = map?.get?.(id)
    const st = j?.state ?? 'queued'
    states[id] = st
    sum += st === 'succeeded' ? 1 : (j?.progress || 0)
    if (st === 'queued' || st === 'running') active = true
    if (st === 'running' && j?.progress_text && !text) text = j.progress_text
  }
  imageGen.value = { progress: sum / ids.length, active, text, states }
}
function _startImageGenMonitor() {
  if (_imgTimer) return
  _sampleImageGen()
  if (!imageGen.value.active) return
  _imgTimer = setInterval(() => {
    _sampleImageGen()
    if (!imageGen.value.active) { clearInterval(_imgTimer); _imgTimer = null }
  }, 2000)
}
function _stopImageGenMonitor() {
  if (_imgTimer) { clearInterval(_imgTimer); _imgTimer = null }
}

const REASON_AXES = ['panel_1', 'panel_2', 'panel_3']
const BIO_LIST_FIELDS = ['hobbies', 'favourite_items', 'likes', 'dislikes', 'quirks']
/** Coerce bio list fields — truncated JA translations often return a string, and
 *  calling Array.join on a string throws and blanks the whole Chronicle panel. */
function asStringList(v) {
  if (Array.isArray(v)) return v.map(x => String(x ?? '').trim()).filter(Boolean)
  if (typeof v === 'string' && v.trim()) return [v.trim()]
  return []
}
function joinList(v) {
  return asStringList(v).join(t('storybook.listSep'))
}
function normalizeBio(raw) {
  if (!raw || typeof raw !== 'object') return null
  const out = { ...raw }
  for (const f of BIO_LIST_FIELDS) out[f] = asStringList(raw[f])
  return out
}
const bioView = computed(() => {
  const b = biography.value
  if (!b) return null
  const raw = (panelLang.value === 'ja' && b.ja && Object.keys(b.ja).length) ? b.ja : b.en
  return normalizeBio(raw)
})
const timetableView = computed(() => {
  const tt = timetable.value
  if (!tt) return []
  const rows = (panelLang.value === 'ja' && Array.isArray(tt.ja) && tt.ja.length)
    ? tt.ja
    : (Array.isArray(tt.en) ? tt.en : [])
  return rows
    .map((s) => (s && typeof s === 'object' ? s : { label: '', activity: String(s || ''), place: '', feeling: '' }))
    .filter((s) => s.label || s.activity)
})
function activityFor(axis) {
  const c = concrete.value
  if (!c) return ''
  const v = (panelLang.value === 'ja' && c.ja && c.ja[axis]) ? c.ja[axis] : (c.en?.[axis] || '')
  return typeof v === 'string' ? v : (v == null ? '' : String(v))
}
const draftImageAxes = computed(() =>
  REASON_AXES.filter(a => !!(axisDrafts.value[a]?.draft_image_id))
)
/** Bio / timetable / concrete / draft thumbs — keep visible while creating. */
const hasDraftMaterials = computed(() =>
  !!bioView.value || timetableView.value.length > 0
  || REASON_AXES.some(a => !!activityFor(a))
  || draftImageAxes.value.length > 0,
)
const hasShotReasoning = computed(() =>
  Object.keys(axisReasoning.value).length > 0
  || Object.keys(axisDrafts.value).length > 0,
)

const CAT_TAG_GROUPS = [
  { key: 'subject_tags', label: 'tagGroupSubject' },
  { key: 'hair_tags', label: 'tagGroupHair' },
  { key: 'expression_tags', label: 'tagGroupExpression' },
  { key: 'clothing_tags', label: 'tagGroupClothing' },
  { key: 'accessory_tags', label: 'tagGroupAccessory' },
  { key: 'body_parts_tags', label: 'tagGroupBodyParts' },
  { key: 'pose_tags', label: 'tagGroupPose' },
  { key: 'background_tags', label: 'tagGroupBackground' },
  { key: 'object_tags', label: 'tagGroupObject' },
  { key: 'lighting_tags', label: 'tagGroupLighting' },
]

function axisHasCatTags(p) {
  if (!p) return false
  if (p.visual_script) return true
  if (asStringList(p.similar_mix_tags).length) return true
  return CAT_TAG_GROUPS.some(g => asStringList(p[g.key]).length > 0)
}

const qualityDraftNote = computed(() => {
  const q = qualityEval.value
  if (!q || q.ok === false) return ''
  const dg = q.draft_grounding
  if (dg && typeof dg === 'object') {
    const n = (dg.axes || []).length
    const d = Number(dg.mean_delta ?? 0)
    const deltaStr = Number.isFinite(d) ? ((d >= 0 ? '+' : '') + d.toFixed(2)) : '—'
    return t('storybook.quality.draftGrounding', {
      axes: n,
      delta: deltaStr,
    })
  }
  if (q.notes?.draft_grounding) {
    return String(q.notes.draft_grounding)
  }
  return ''
})

async function pickRandomBase() {
  pickingRandom.value = true
  try {
    const exclude = baseSha.value ? `&exclude=${baseSha.value}` : ''
    const r = await fetch(`/api/images/random?n=1${exclude}`)
    if (!r.ok) throw new Error(r.statusText)
    const data = await r.json()
    const doc = (data.images || [])[0]
    if (!doc?.sha256) throw new Error(t('chronicle.randomEmpty'))
    baseSha.value = doc.sha256
    baseModel.value = _modelOf(doc)
  } catch (err) {
    emit('toast', { msg: t('chronicle.randomFailed') + ': ' + (err.message || err), type: 'error' })
  } finally {
    pickingRandom.value = false
  }
}

function _extractError(errBody, resp) {
  const detail = errBody?.detail
  return typeof detail === 'string' ? detail
    : Array.isArray(detail) ? detail.map(e => e.msg ?? JSON.stringify(e)).join('; ')
    : resp.statusText
}

async function _runStream(jobId) {
  try {
    await readStream(jobId)
  } finally {
    if (_flushTimer) { clearTimeout(_flushTimer); _flushTimer = null }
    _flushTokens()
  }
}

async function _submitAndStream(url, payload, onJob, { expand = false } = {}) {
  running.value = true
  errorMsg.value = ''
  renderError.value = ''
  streamTerminal.value = false
  if (expand) expandSessionActive.value = true
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!r.ok) throw new Error(_extractError(await r.json().catch(() => null), r))
    const data = await r.json()
    onJob?.(data)
    await _runStream(data.job_id)
    if (expand && expandSessionActive.value && !streamTerminal.value && !errorMsg.value) {
      // Stream died mid-expand (proxy flap / cancel) without done/error.
      errorMsg.value = t('chronicle.streamDropped')
      emit('toast', { msg: errorMsg.value, type: 'warning' })
    }
  } catch (err) {
    errorMsg.value = String(err.message || err)
    emit('toast', { msg: errorMsg.value, type: 'error' })
  } finally {
    running.value = false
    if (streamTerminal.value || errorMsg.value) {
      // Keep expandSessionActive until the user starts a new run / closes, so a
      // dropped stream cannot instantly unlock Esc/backdrop dismiss.
    }
    // Grace window so Esc/backdrop can't win the race between stream end and
    // image_jobs / first job-poll sample (same class of bug as mid-expand dismiss).
    _ignoreDismissUntil = performance.now() + 5000
    const autoCid = pendingAutoSelect.value
    if (autoCid && storyId.value && !errorMsg.value) {
      pendingAutoSelect.value = ''
      const scale = pendingExpandTimeScale.value || currentTimeScale()
      queueMicrotask(() => selectCandidate(autoCid, scale))
    }
  }
}

function clearBase() {
  baseSha.value = ''
  baseModel.value = ''
  thumbFailed.value = false
}

const topicSuggesting = ref(false)

/** Fill お題 with a short 起承転結 premise read off the base image.
 *  Only prefills the field — the user reviews/edits and starts the run. */
async function suggestTopicFromImage() {
  if (!baseSha.value || topicSuggesting.value) return
  topicSuggesting.value = true
  try {
    const r = await fetch('/api/story/topic-suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_sha256: baseSha.value,
        locale: uiLocale.value,
        worldview: '',
        llm_provider: llmProvider.value,
        ...(llmProvider.value === 'openai' && openaiModel.value.trim()
          ? { vlm_model: openaiModel.value.trim() }
          : {}),
      }),
    })
    if (!r.ok) {
      let detail = r.statusText
      try { detail = (await r.json()).detail || detail } catch { /* non-JSON error body */ }
      throw new Error(detail)
    }
    const data = await r.json()
    if (!data.topic) throw new Error(t('chronicle.topicFromImageEmpty'))
    userTopic.value = data.topic
    emit('toast', { msg: t('chronicle.topicFromImageDone'), type: 'success' })
  } catch (err) {
    emit('toast', {
      msg: t('chronicle.topicFromImageFailed', { reason: String(err.message || err) }),
      type: 'error',
    })
  } finally {
    topicSuggesting.value = false
  }
}

async function start() {
  if (!baseSha.value && !userTopic.value.trim()) {
    emit('toast', { msg: t('chronicle.needTopicOrBase'), type: 'error' })
    return
  }
  resetRun()
  pendingExpandTimeScale.value = currentTimeScale()
  await _submitAndStream('/api/story/chronicle', currentSettingsPayload(), (d) => {
    groupId.value = d.group_id
  })
}

function currentSettingsPayload() {
  const payload = {
    base_sha256: baseSha.value || '',
    user_topic: userTopic.value,
    character_tags: characterTags.value,
    include_happening: includeHappening.value,
    author_style: authorStyle.value,
    author_id: authorId.value,
    custom_tags_panel_1: customTagsPanel1.value,
    custom_tags_panel_2: customTagsPanel2.value,
    custom_tags_panel_3: customTagsPanel3.value,
    workflow_name: workflow.value,
    use_ref_seed: baseSha.value ? useRefSeed.value : false,
    manual_mode: manualMode.value,
    llm_provider: llmProvider.value,
    temperature: temperature.value,
    num_ctx: numCtx.value,
    locale: uiLocale.value,
  }
  if (llmProvider.value === 'openai' && openaiModel.value.trim()) {
    payload.vlm_model = openaiModel.value.trim()
  }
  return payload
}

const draftRefineDisabledHint = computed(() => {
  if (manualMode.value) return t('chronicle.draftRefineDisabledManual')
  if (!workflow.value) return t('chronicle.draftRefineDisabledWorkflow')
  return ''
})

async function selectCandidate(cid, timeScaleOverride = '') {
  if (!storyId.value || running.value) return
  selectedCandidate.value = cid
  selecting.value = false
  // Leave 'selecting' phase immediately so the live expand pane (step≥3) can show
  // before the first SSE phase event arrives.
  phase.value = 'expanding'
  resetStory({ keepDraftNotes: true })
  const scale = timeScaleOverride || pendingExpandTimeScale.value || currentTimeScale()
  await _submitAndStream(
    `/api/story/chronicle/${storyId.value}/select`,
    { candidate_id: cid, time_scale: scale },
    null,
    { expand: true },
  )
}

async function respin(stage) {
  if (!storyId.value || running.value) return
  const count = stage === 'candidates'
    ? (respinCandCount.value += 1)
    : (respinExpandCount.value += 1)
  if (stage === 'candidates') { candidates.value = []; selecting.value = false }
  resetStory({ keepDraftNotes: stage === 'expand' })
  if (stage === 'expand') phase.value = 'expanding'
  const settings = currentSettingsPayload()
  await _submitAndStream(`/api/story/chronicle/${storyId.value}/respin`, {
    stage,
    respin_count: count,
    time_scale: settings.time_scale,
    divergence: settings.divergence,
    emotion: settings.emotion,
    dramatic_mode: settings.dramatic_mode,
    tone: settings.tone,
    prompt_style: settings.prompt_style,
    workflow_name: settings.workflow_name,
    use_draft_refine: settings.use_draft_refine,
    draft_width: settings.draft_width,
    draft_height: settings.draft_height,
    draft_steps: settings.draft_steps,
    suppress_conflict_tags: settings.suppress_conflict_tags,
    manual_mode: settings.manual_mode,
    fast_mode: settings.fast_mode,
    wd14_prompt_spice: settings.wd14_prompt_spice,
    similar_tag_mix: settings.similar_tag_mix,
    similar_tag_mix_ratio: settings.similar_tag_mix_ratio,
    llm_provider: settings.llm_provider,
    temperature: settings.temperature,
    num_ctx: settings.num_ctx,
    prose_paragraphs: settings.prose_paragraphs,
    worldview: settings.worldview,
    user_topic: settings.user_topic,
  }, null, { expand: stage === 'expand' })
}

async function readStream(jobId) {
  const resp = await fetch(`/api/story/chronicle/${jobId}/stream`)
  if (!resp.ok) throw new Error(`stream: ${resp.statusText}`)
  _reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await _reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const line = chunk.split('\n').find(l => l.startsWith('data: '))
      if (!line) continue
      let ev
      try { ev = JSON.parse(line.slice(6)) } catch { continue }
      handleEvent(ev)
    }
  }
  _reader = null
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'phase':
      if (ev.prev_code && ev.duration_ms != null) {
        recordPhaseTiming(ev.prev_code, ev.duration_ms)
      }
      if (ev.code && ev.code !== phase.value) {
        phaseStartedAt.value = Date.now()
        startPhaseTick()
      } else if (ev.code && !phaseStartedAt.value) {
        phaseStartedAt.value = Date.now()
        startPhaseTick()
      }
      phase.value = ev.code || phase.value
      if (ev.progress !== undefined) progress.value = ev.progress
      break
    case 'phase_timings': {
      const incoming = Array.isArray(ev.timings) ? ev.timings : []
      if (incoming.length) {
        phaseTimings.value = incoming
          .filter((r) => r?.code && r.duration_ms != null && Number.isFinite(Number(r.duration_ms)))
          .map((r) => ({ code: r.code, duration_ms: Number(r.duration_ms) }))
      }
      break
    }
    case 'token':
      _pendingTokens += ev.text
      _scheduleFlush()
      break
    case 'candidates':
      storyId.value = ev.story_id
      candidates.value = ev.candidates || []
      selecting.value = true
      phase.value = 'selecting'
      if (ev.auto_select) {
        // Fast mode: expand after this stream ends (running blocks mid-stream).
        selecting.value = false
        pendingAutoSelect.value = ev.auto_select
      }
      break
    case 'axis_prompt':
      prompts.value = {
        ...prompts.value,
        [ev.axis]: {
          positive: ev.positive,
          negative: ev.negative,
          refined_from_draft: !!ev.refined_from_draft,
          draft_richness_delta: ev.draft_richness_delta || null,
          visual_script: ev.visual_script || '',
          subject_tags: ev.subject_tags || [],
          hair_tags: ev.hair_tags || [],
          expression_tags: ev.expression_tags || [],
          clothing_tags: ev.clothing_tags || [],
          accessory_tags: ev.accessory_tags || [],
          body_parts_tags: ev.body_parts_tags || [],
          pose_tags: ev.pose_tags || [],
          background_tags: ev.background_tags || [],
          object_tags: ev.object_tags || [],
          lighting_tags: ev.lighting_tags || [],
          similar_mix_tags: ev.similar_mix_tags || [],
          similar_mix_sources: ev.similar_mix_sources || [],
        },
      }
      break
    case 'quality_eval': {
      const { type: _t, ...rest } = ev
      qualityEval.value = rest
      break
    }
    case 'story':
      title.value = ev.title || ''
      overall.value = ev.overall || ''
      if (ev.axes) {
        const patch = _axesFromMap(ev.axes)
        // Expand streams in the request locale; JA → Ja slot until done brings EN.
        if (uiLocale.value === 'ja') _mergeAxisStories(null, patch)
        else _mergeAxisStories(patch, null)
      }
      break
    case 'story_saved':
      storyId.value = ev.story_id
      break
    case 'translation':
      titleJa.value = ev.title_ja || ''
      overallJa.value = ev.overall_ja || ''
      {
        const jaPatch = {}
        for (const a of AXES) {
          if (ev[`${a}_ja`]) jaPatch[a] = ev[`${a}_ja`]
        }
        _mergeAxisStories(null, jaPatch)
      }
      break
    case 'mutation_tags':
      mutationTags.value = asStringList(ev.tags)
      break
    case 'story_seed_tags':
      storySeedTags.value = asStringList(ev.tags)
      storySeedMotif.value = ev.motif || ''
      break
    case 'biography':
      biography.value = {
        en: normalizeBio(ev.biography) || {},
        ja: normalizeBio(ev.biography_ja) || {},
      }
      break
    case 'timetable':
      timetable.value = {
        en: Array.isArray(ev.timetable) ? ev.timetable : [],
        ja: Array.isArray(ev.timetable_ja) ? ev.timetable_ja : [],
      }
      break
    case 'concrete_activities':
      concrete.value = {
        en: (ev.activities && typeof ev.activities === 'object') ? ev.activities : {},
        ja: (ev.activities_ja && typeof ev.activities_ja === 'object') ? ev.activities_ja : {},
      }
      break
    case 'axis_reasoning':
      axisReasoning.value = { ...axisReasoning.value, [ev.axis]: ev }
      break
    case 'axis_draft':
      axisDrafts.value = { ...axisDrafts.value, [ev.axis]: ev }
      break
    case 'pinup_job':
      pinupJobId.value = ev.job_id
      _startImageGenMonitor()
      break
    case 'warning':
      emit('toast', { msg: ev.message, type: 'warning' })
      break
    case 'image_jobs':
      imageJobs.value = ev.jobs
      _startImageGenMonitor()
      break
    case 'done':
      streamTerminal.value = true
      expandSessionActive.value = false
      seed.value = ev.seed
      finished.value = true
      phase.value = 'done'
      progress.value = 1.0
      phaseStartedAt.value = null
      stopPhaseTick()
      if (ev.title) title.value = ev.title
      if (ev.title_ja) titleJa.value = ev.title_ja
      if (ev.overall) overall.value = ev.overall
      if (ev.overall_ja) overallJa.value = ev.overall_ja
      if (ev.axes) {
        const en = {}
        const ja = {}
        for (const a of AXES) {
          const ax = ev.axes[a] || {}
          if (ax.story) en[a] = ax.story
          if (ax.story_ja) ja[a] = ax.story_ja
        }
        _mergeAxisStories(en, ja)
      }
      break
    case 'error':
      streamTerminal.value = true
      expandSessionActive.value = false
      phaseStartedAt.value = null
      stopPhaseTick()
      errorMsg.value = ev.message
      emit('toast', { msg: ev.message, type: 'error' })
      break
  }
}

async function cancelGroup() {
  if (!groupId.value) return
  try {
    await fetch(`/api/jobs/groups/${groupId.value}/cancel`, { method: 'POST' })
    emit('toast', { msg: t('chronicle.cancelled'), type: 'info' })
  } catch {}
}

async function generateImages() {
  if (!storyId.value) return
  try {
    const axes = {}
    for (const [axis, p] of Object.entries(prompts.value)) {
      axes[axis] = { prompt_positive: p.positive, prompt_negative: p.negative }
    }
    const r = await fetch(`/api/story/${storyId.value}/generate-images`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ axes, seed: seed.value, workflow_name: workflow.value }),
    })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    const data = await r.json()
    imageJobs.value = data.jobs
    _startImageGenMonitor()
    emit('toast', { msg: t('chronicle.imagesQueued'), type: 'success' })
  } catch (err) {
    emit('toast', { msg: String(err.message || err), type: 'error' })
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="chronicle-root fixed inset-0 z-[var(--z-panel-chronicle)] flex items-center justify-center p-4"
      @mousedown.self="onBackdropDown"
      @mouseup.self="onBackdropUp">
      <div class="sb-shell relative w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden"
        @mousedown.stop>

        <!-- header -->
        <div class="flex items-center justify-between px-5 py-3.5 sb-hairline">
          <h2 class="sb-display text-lg text-teal-300 tracking-wide flex items-center gap-2.5">
            <span class="chronicle-lamp inline-block w-2.5 h-2.5 rounded-full bg-teal-400"
              :class="running ? 'is-running' : 'opacity-30'"></span>
            <SbIcon name="scroll" class="w-5 h-5 opacity-80" />
            {{ t('chronicle.title') }}
          </h2>
          <button @click="close" class="sb-icon-btn" :aria-label="t('chronicle.aria.close')">
            <SbIcon name="close" class="w-4 h-4" />
          </button>
        </div>

        <div v-if="comfyOffline"
          class="px-5 py-2 text-[11px] text-amber-200/90 bg-amber-950/40 border-b border-amber-800/30">
          {{ t('chronicle.comfyOfflineHint') }}
        </div>

        <div class="flex-1 overflow-y-auto p-5 grid grid-cols-1 lg:grid-cols-2 gap-5">

          <!-- ── LEFT: settings (always visible) ───────────────────────────── -->
          <div class="flex flex-col gap-4 min-w-0">

            <!-- full settings; locked while the pipeline runs -->
            <fieldset :disabled="settingsLocked" class="flex flex-col gap-4 min-w-0 disabled:opacity-60">
              <div class="grid grid-cols-1 sm:grid-cols-[148px_1fr] gap-4">
                <div class="rounded-xl border border-teal-800/25 bg-black/25 flex flex-col items-center justify-center gap-2 p-3 min-h-[160px]">
                  <img v-if="baseSha" :src="baseThumbSrc" @error="thumbFailed = true"
                    class="max-h-28 rounded-lg object-contain" />
                  <SbIcon v-else name="image" class="w-8 h-8 text-[var(--sb-faint)]" />
                  <p v-if="!baseSha" class="text-[10px] text-teal-300/80 text-center leading-tight px-1">
                    {{ t('chronicle.topicOnlyHint') }}
                  </p>
                  <p v-if="baseSha && baseModel" :title="t('chronicle.baseModelTitle')"
                    class="w-full text-[10px] text-teal-300/70 font-mono text-center leading-tight break-all">
                    {{ baseModel }}
                  </p>
                  <button type="button" @click="pickRandomBase" :disabled="pickingRandom || settingsLocked"
                    class="sb-btn w-full justify-center border-teal-700/40 text-teal-200">
                    <SbIcon name="dice" class="w-3 h-3" />
                    {{ pickingRandom ? t('chronicle.randomPicking') : t('chronicle.randomFromLibrary') }}
                  </button>
                  <button v-if="baseSha" type="button" @click="clearBase" :disabled="settingsLocked"
                    class="sb-btn w-full justify-center border-white/10 text-[var(--sb-muted)] text-[10px]">
                    {{ t('chronicle.clearBase') }}
                  </button>
                  <p class="text-[10px] text-[var(--sb-muted)] text-center leading-tight">
                    {{ t('chronicle.baseHint') }}
                  </p>
                </div>

                <div class="flex flex-col gap-3 text-xs min-w-0">
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1.5">{{ t('chronicle.userTopic') }}</span>
                    <div class="flex-1 flex flex-col gap-1 min-w-0">
                      <textarea v-model="userTopic" rows="3" :placeholder="t('chronicle.userTopicPh')"
                        class="sb-input flex-1 min-h-[4.5rem]" :disabled="settingsLocked" />
                      <button v-if="baseSha" type="button"
                        class="sb-chip self-start"
                        :class="topicSuggesting ? 'is-chip-on-teal' : ''"
                        :disabled="topicSuggesting || settingsLocked"
                        :title="t('chronicle.topicFromImageTitle')"
                        @click="suggestTopicFromImage">
                        <SbIcon :name="topicSuggesting ? 'refresh' : 'spark'"
                          class="w-3 h-3 inline mr-1" :class="topicSuggesting ? 'animate-spin' : ''" />
                        {{ topicSuggesting ? t('chronicle.topicFromImageBusy') : t('chronicle.topicFromImage') }}
                      </button>
                    </div>
                  </div>
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1.5">{{ t('chronicle.characterTags') }}</span>
                    <textarea v-model="characterTags" rows="2"
                      :placeholder="t('chronicle.characterTagsPh')"
                      class="sb-input flex-1" :disabled="settingsLocked" />
                  </div>
                  <label class="flex items-start gap-2 cursor-pointer text-[var(--sb-muted)]"
                    :title="t('chronicle.includeHappeningHint')">
                    <input v-model="includeHappening" type="checkbox" class="accent-teal-500 mt-0.5" :disabled="settingsLocked" />
                    <span>
                      <span class="font-medium text-teal-200">{{ t('chronicle.includeHappening') }}</span>
                      <span class="block text-[10px] mt-0.5">{{ t('chronicle.includeHappeningHint') }}</span>
                    </span>
                  </label>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0">{{ t('chronicle.authorPreset') }}</span>
                    <select v-model="authorId" class="sb-select flex-1" :disabled="settingsLocked"
                      @change="onAuthorPresetChange">
                      <option value="">{{ t('chronicle.authorPresetNone') }}</option>
                      <option v-for="a in authorsList" :key="a.id" :value="a.id">
                        {{ a.genre_tag ? `[${a.genre_tag}] ` : '' }}{{ a.name }}
                      </option>
                    </select>
                  </div>
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1.5">{{ t('chronicle.authorStyle') }}</span>
                    <textarea v-model="authorStyle" rows="2" :placeholder="t('chronicle.authorStylePh')"
                      class="sb-input flex-1" :disabled="settingsLocked" />
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <span class="sb-label">{{ t('chronicle.customTagsPanel') }}</span>
                    <div class="flex items-center gap-2">
                      <span class="sb-label w-14 shrink-0 text-[10px]">{{ t('chronicle.axis.panel_1') }}</span>
                      <input v-model="customTagsPanel1" type="text" class="sb-input flex-1"
                        :placeholder="t('chronicle.customTagsPh')" :disabled="settingsLocked" />
                    </div>
                    <div class="flex items-center gap-2">
                      <span class="sb-label w-14 shrink-0 text-[10px]">{{ t('chronicle.axis.panel_2') }}</span>
                      <input v-model="customTagsPanel2" type="text" class="sb-input flex-1"
                        :placeholder="t('chronicle.customTagsPh')" :disabled="settingsLocked" />
                    </div>
                    <div class="flex items-center gap-2">
                      <span class="sb-label w-14 shrink-0 text-[10px]">{{ t('chronicle.axis.panel_3') }}</span>
                      <input v-model="customTagsPanel3" type="text" class="sb-input flex-1"
                        :placeholder="t('chronicle.customTagsPh')" :disabled="settingsLocked" />
                    </div>
                  </div>
                  <!-- legacy settings kept below but collapsed -->
                  <details class="opacity-50">
                    <summary class="text-[10px] text-[var(--sb-muted)] cursor-pointer">legacy</summary>
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="sb-label w-20 shrink-0" :title="baseSha ? t('chronicle.baseAxisHintImage') : t('chronicle.baseAxisHintTopic')">{{ t('chronicle.baseAxis') }}</span>
                    <button v-for="a in AXES" :key="a" type="button" @click="baseAxis = a"
                      class="sb-chip" :class="baseAxis === a ? 'is-chip-on-teal' : ''">
                      {{ t('chronicle.axis.' + a) }}
                    </button>
                  </div>
                  <p class="text-[10px] text-[var(--sb-muted)] -mt-1 ml-20 leading-tight">
                    {{ baseSha ? t('chronicle.baseAxisHintImage') : t('chronicle.baseAxisHintTopic') }}
                  </p>
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1.5">{{ t('chronicle.userTopic') }}</span>
                    <div class="flex-1 flex flex-col gap-1 min-w-0">
                      <textarea v-model="userTopic" rows="2" :placeholder="t('chronicle.userTopicPh')"
                        class="sb-textarea w-full"></textarea>
                      <button v-if="baseSha" type="button"
                        class="sb-chip self-start"
                        :class="topicSuggesting ? 'is-chip-on-teal' : ''"
                        :disabled="topicSuggesting || settingsLocked"
                        :title="t('chronicle.topicFromImageTitle')"
                        @click="suggestTopicFromImage">
                        <SbIcon :name="topicSuggesting ? 'refresh' : 'spark'"
                          class="w-3 h-3 inline mr-1" :class="topicSuggesting ? 'animate-spin' : ''" />
                        {{ topicSuggesting ? t('chronicle.topicFromImageBusy') : t('chronicle.topicFromImage') }}
                      </button>
                    </div>
                  </div>
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1.5" :title="t('chronicle.characterTagsTitle')">{{ t('chronicle.characterTags') }}</span>
                    <textarea v-model="characterTags" rows="2"
                      :placeholder="t('chronicle.characterTagsPh')"
                      class="sb-textarea flex-1 w-full"></textarea>
                  </div>
                  <div class="flex flex-col gap-1.5">
                    <div class="flex items-center gap-2">
                      <span class="sb-label w-20 shrink-0" :title="t('chronicle.axisKeywordsTitle')">{{ t('chronicle.axisKeywords') }}</span>
                      <span class="text-[10px] text-[var(--sb-muted)]">{{ t('chronicle.axisKeywordsHint') }}</span>
                    </div>
                    <div class="grid grid-cols-1 gap-1 pl-0 sm:pl-20">
                      <div class="flex items-center gap-2">
                        <span class="sb-label w-14 shrink-0 text-[10px]">{{ t('chronicle.axis.past') }}</span>
                        <input v-model="keywordsPast" type="text" class="sb-input flex-1"
                          :placeholder="t('chronicle.keywordsPh')" />
                      </div>
                      <div class="flex items-center gap-2">
                        <span class="sb-label w-14 shrink-0 text-[10px]">{{ t('chronicle.axis.present') }}</span>
                        <input v-model="keywordsPresent" type="text" class="sb-input flex-1"
                          :placeholder="t('chronicle.keywordsPh')" />
                      </div>
                      <div class="flex items-center gap-2">
                        <span class="sb-label w-14 shrink-0 text-[10px]">{{ t('chronicle.axis.future') }}</span>
                        <input v-model="keywordsFuture" type="text" class="sb-input flex-1"
                          :placeholder="t('chronicle.keywordsPh')" />
                      </div>
                    </div>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.lifeRoleTitle')">{{ t('chronicle.lifeRole') }}</span>
                    <select v-model="lifeRole" class="sb-select flex-1" :disabled="settingsLocked">
                      <option v-for="r in LIFE_ROLES" :key="r" :value="r">{{ t('chronicle.lifeRoleOpt.' + r) }}</option>
                    </select>
                    <button type="button" class="sb-chip shrink-0" :disabled="settingsLocked"
                      :title="t('chronicle.lifeRoleRandomTitle')"
                      @click="lifeRole = LIFE_ROLES[Math.floor(Math.random() * (LIFE_ROLES.length - 1))]">
                      {{ t('chronicle.lifeRoleRandom') }}
                    </button>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.timeScaleTitle')">
                      <SbIcon name="clock" class="w-3 h-3 inline mr-0.5" />{{ t('chronicle.timeScaleLabel') }}
                    </span>
                    <input v-model.number="timeScaleIdx" type="range" min="0" :max="TIME_SCALES.length - 1" step="1"
                      class="flex-1 accent-teal-500" />
                    <span class="text-teal-400 w-16 text-right text-[11px]">± {{ t('chronicle.timeScale.' + TIME_SCALES[timeScaleIdx]) }}</span>
                  </div>
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.toneTitle')">{{ t('chronicle.toneLabel') }}</span>
                    <button v-for="tn in TONES" :key="tn" type="button" @click="tone = tn"
                      class="sb-chip" :class="tone === tn ? 'is-chip-on' : ''">
                      {{ t('chronicle.tone.' + tn) }}
                    </button>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0">{{ t('chronicle.workflow') }}</span>
                    <select v-model="workflow" class="sb-select flex-1">
                      <option value="">{{ t('chronicle.workflowNone') }}</option>
                      <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
                    </select>
                  </div>
                  </details>
                </div>
              </div>

              <!-- direction (open) -->
              <details open class="rounded-xl border border-white/5 bg-black/20">
                <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                  <span class="flex items-center gap-1.5">
                    <SbIcon name="spark" class="w-3.5 h-3.5 text-teal-400/80" />
                    {{ t('chronicle.directionGroup') }}
                  </span>
                </summary>
                <div class="px-3 pb-3 pt-1 flex flex-col gap-3 text-xs">
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1.5">{{ t('chronicle.worldview') }}</span>
                    <textarea v-model="worldview" rows="2" :placeholder="t('chronicle.worldviewPh')"
                      class="sb-textarea flex-1"></textarea>
                  </div>
                  <div class="flex flex-col gap-1">
                    <div class="flex items-center gap-2">
                      <span class="sb-label w-20 shrink-0" :title="t('chronicle.divergenceTitle')">{{ t('chronicle.divergence') }}</span>
                      <input v-model.number="divergence" type="range" min="0" max="1" step="0.05" class="flex-1 accent-teal-500" />
                      <span class="text-teal-400 font-mono w-10 text-right">{{ Math.round(divergence * 100) }}%</span>
                    </div>
                    <p v-if="asStringList(mutationTags).length" class="text-[10px] text-teal-500/80 pl-[calc(5rem+0.5rem)] break-all">
                      <span class="text-teal-300/80">{{ t('chronicle.mutationTags') }}:</span>
                      {{ joinList(mutationTags) }}
                    </p>
                  </div>
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1" :title="t('chronicle.emotionTitle')">{{ t('chronicle.emotionLabel') }}</span>
                    <div class="flex flex-wrap gap-1 flex-1">
                      <button v-for="em in EMOTION_DIMENSIONS" :key="em" type="button"
                        @click="emotion = emotion === em ? '' : em"
                        class="sb-chip" :class="emotion === em ? 'is-chip-on-indigo' : ''">
                        {{ t(`inspire.emotion.${em}`) }}
                      </button>
                    </div>
                  </div>
                  <div class="flex items-start gap-2">
                    <span class="sb-label w-20 shrink-0 pt-1" :title="t('chronicle.dramaticModeTitle')">{{ t('chronicle.dramaticModeLabel') }}</span>
                    <div class="flex flex-wrap gap-1 flex-1">
                      <button type="button" @click="dramaticMode = ''"
                        class="sb-chip" :class="dramaticMode === '' ? 'is-chip-on-indigo' : ''">
                        {{ t('chronicle.dramaticModeAuto') }}
                      </button>
                      <button v-for="dm in DRAMATIC_MODES" :key="dm" type="button"
                        @click="dramaticMode = dramaticMode === dm ? '' : dm"
                        class="sb-chip" :class="dramaticMode === dm ? 'is-chip-on-indigo' : ''">
                        {{ t(`chronicle.dramaticMode.${dm}`) }}
                      </button>
                    </div>
                  </div>
                </div>
              </details>

              <!-- output (open) -->
              <details open class="rounded-xl border border-white/5 bg-black/20">
                <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                  <span class="flex items-center gap-1.5">
                    <SbIcon name="image" class="w-3.5 h-3.5 text-teal-400/80" />
                    {{ t('chronicle.outputGroup') }}
                  </span>
                </summary>
                <div class="px-3 pb-3 pt-1 flex flex-col gap-3 text-xs">
                  <p v-if="fastMode" class="text-[10px] text-amber-300/80 leading-snug">
                    {{ t('chronicle.fastModeIgnores') }}
                  </p>
                  <div class="flex items-center gap-2 flex-wrap" :class="fastMode ? 'opacity-40' : ''">
                    <span class="sb-label w-20 shrink-0">{{ t('chronicle.promptStyle') }}</span>
                    <button v-for="m in ['danbooru+natural', 'natural', 'danbooru']" :key="m" type="button"
                      :disabled="fastMode"
                      :title="fastMode ? t('chronicle.fastModeIgnores') : ''"
                      @click="promptStyle = m"
                      class="sb-chip" :class="promptStyle === m ? 'is-chip-on-teal' : ''">
                      {{ t('chronicle.style.' + m.replace('+', '_')) }}
                    </button>
                  </div>
                  <div v-if="promptStyle !== 'danbooru'" class="flex items-center gap-2"
                    :class="fastMode ? 'opacity-40' : ''">
                    <span class="sb-label w-20 shrink-0"
                      :title="fastMode ? t('chronicle.fastModeIgnores') : t('chronicle.proseLengthTitle')">
                      {{ t('chronicle.proseLengthLabel') }}
                    </span>
                    <input v-model.number="proseParagraphs" type="range" min="3" max="7" step="1"
                      :disabled="fastMode"
                      class="flex-1 accent-teal-500" />
                    <span class="text-teal-400 w-16 text-right text-[11px] font-mono">
                      {{ proseParagraphs }}{{ t('chronicle.proseLengthUnit') }}
                    </span>
                  </div>
                  <div v-if="promptStyle !== 'danbooru'"
                    class="flex justify-between text-[10px] text-[var(--sb-faint)] pl-[calc(5rem+0.5rem)] -mt-1">
                    <span>{{ t('chronicle.proseLengthShort') }}</span>
                    <span>{{ t('chronicle.proseLengthLong') }}</span>
                  </div>
                  <div v-if="promptStyle !== 'danbooru'" class="flex flex-wrap gap-1 pl-[calc(5rem+0.5rem)]">
                    <button
                      v-for="k in PROSE_PRESETS"
                      :key="k.id"
                      type="button"
                      class="sb-chip"
                      :disabled="fastMode"
                      @click="applyProsePreset(k)"
                    >{{ t(k.labelKey) }}</button>
                  </div>
                  <div class="flex items-center flex-wrap gap-4">
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]">
                      <input v-model="useRefSeed" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.seedInherit') }}
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]" :title="t('chronicle.pinupTitle')">
                      <input v-model="generatePinup" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.generatePinup') }}
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]">
                      <input v-model="manualMode" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.manualMode') }}
                    </label>
                    <label
                      class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]"
                      :title="t('chronicle.fastModeTitle')"
                    >
                      <input v-model="fastMode" type="checkbox" class="accent-amber-500" />
                      {{ t('chronicle.fastMode') }}
                    </label>
                  </div>
                  <p v-if="fastMode" class="text-[10px] text-amber-500/80 pl-1">
                    {{ t('chronicle.fastModeHint') }}
                  </p>
                  <label
                    class="flex items-start gap-2 cursor-pointer text-xs"
                    :class="composeAllowlist ? 'text-teal-300' : 'text-[var(--sb-muted)]'"
                    :title="t('chronicle.composeAllowlistTitle')"
                  >
                    <input v-model="composeAllowlist" type="checkbox" class="accent-teal-500 mt-0.5" />
                    <span>
                      <span class="font-medium">{{ t('chronicle.composeAllowlist') }}</span>
                      <span class="block text-[10px] text-teal-500/80 mt-0.5">{{ t('chronicle.composeAllowlistHint') }}</span>
                    </span>
                  </label>
                  <label
                    class="flex items-start gap-2 cursor-pointer text-xs"
                    :class="fastMode ? 'opacity-40 text-[var(--sb-muted)]'
                      : (similarTagMix ? 'text-teal-300' : 'text-[var(--sb-muted)]')"
                    :title="fastMode ? t('chronicle.fastModeIgnores') : t('chronicle.similarTagMixTitle')"
                  >
                    <input v-model="similarTagMix" type="checkbox" :disabled="fastMode"
                      class="accent-teal-500 mt-0.5" />
                    <span>
                      <span class="font-medium">{{ t('chronicle.similarTagMix') }}</span>
                      <span class="block text-[10px] text-teal-500/80 mt-0.5">{{ t('chronicle.similarTagMixHint') }}</span>
                    </span>
                  </label>
                  <div v-if="similarTagMix" class="flex items-center gap-2 flex-wrap pl-6"
                    :class="fastMode ? 'opacity-40' : ''">
                    <span class="sb-label shrink-0" :title="t('chronicle.similarTagMixRatioTitle')">{{ t('chronicle.similarTagMixRatio') }}</span>
                    <input v-model.number="similarTagMixRatio" type="range" min="0.1" max="0.7" step="0.05"
                      :disabled="fastMode"
                      class="flex-1 accent-teal-500" />
                    <span class="text-[11px] font-mono text-teal-300 w-10 text-right">
                      {{ Math.round(similarTagMixRatio * 100) }}%
                    </span>
                  </div>
                  <label
                    class="flex items-start gap-2 cursor-pointer text-xs"
                    :class="wd14PromptSpice ? 'text-amber-300' : 'text-[var(--sb-muted)]'"
                    :title="t('chronicle.wd14SpiceTitle')"
                  >
                    <input v-model="wd14PromptSpice" type="checkbox" class="accent-amber-500 mt-0.5" />
                    <span>
                      <span class="font-medium">{{ t('chronicle.wd14Spice') }}</span>
                      <span class="block text-[10px] text-amber-500/80 mt-0.5">{{ t('chronicle.wd14SpiceHint') }}</span>
                    </span>
                  </label>
                  <div v-if="!fastMode" class="flex items-center gap-2 flex-wrap">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.draftRefineTitle')">{{ t('chronicle.draftRefine') }}</span>
                    <button v-for="m in ['auto', 'on', 'off']" :key="m" type="button"
                      @click="useDraftRefine = m"
                      class="sb-chip" :class="useDraftRefine === m ? 'is-chip-on-teal' : ''">
                      {{ t('chronicle.draftRefineMode.' + m) }}
                    </button>
                  </div>
                  <p v-if="!fastMode && draftRefineDisabledHint && useDraftRefine !== 'off'"
                    class="text-[10px] text-amber-500/70 pl-[5.5rem]">
                    {{ draftRefineDisabledHint }}
                  </p>
                  <div v-if="!fastMode && useDraftRefine !== 'off'" class="flex items-center gap-2 flex-wrap text-[10px] text-[var(--sb-muted)]">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.draftSizeTitle')">{{ t('chronicle.draftSize') }}</span>
                    <label class="flex items-center gap-1">
                      <span class="text-[var(--sb-faint)]">W</span>
                      <input v-model.number="draftWidth" type="number" min="256" max="1024" step="64"
                        class="sb-input w-16 py-0.5 text-[11px] font-mono" />
                    </label>
                    <label class="flex items-center gap-1">
                      <span class="text-[var(--sb-faint)]">H</span>
                      <input v-model.number="draftHeight" type="number" min="256" max="1024" step="64"
                        class="sb-input w-16 py-0.5 text-[11px] font-mono" />
                    </label>
                    <label class="flex items-center gap-1" :title="t('chronicle.draftStepsTitle')">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.draftSteps') }}</span>
                      <input v-model.number="draftSteps" type="number" min="4" max="28" step="1"
                        class="sb-input w-14 py-0.5 text-[11px] font-mono" />
                    </label>
                  </div>
                </div>
              </details>

              <!-- advanced (closed) -->
              <details class="rounded-xl border border-white/5 bg-black/20">
                <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                  <span class="flex items-center gap-1.5">
                    <SbIcon name="settings" class="w-3.5 h-3.5 text-teal-400/80" />
                    {{ t('chronicle.advancedGroup') }}
                  </span>
                </summary>
                <div class="px-3 pb-3 pt-1 flex flex-col gap-3 text-xs">
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.llmProviderTitle')">{{ t('chronicle.llmProvider') }}</span>
                    <div class="flex gap-1.5 flex-1">
                      <button type="button" @click="llmProvider = 'ollama'"
                        :class="llmProvider === 'ollama' ? 'bg-teal-700 border-teal-500 text-teal-50' : 'bg-black/30 border-white/10 text-[var(--sb-muted)]'"
                        class="px-2.5 py-1 rounded-lg border text-[11px] font-medium transition-colors">
                        Ollama
                      </button>
                      <button type="button" @click="llmProvider = 'openai'"
                        :class="llmProvider === 'openai' ? 'bg-teal-700 border-teal-500 text-teal-50' : 'bg-black/30 border-white/10 text-[var(--sb-muted)]'"
                        class="px-2.5 py-1 rounded-lg border text-[11px] font-medium transition-colors">
                        {{ t('chronicle.openaiCompat') }}
                      </button>
                    </div>
                  </div>
                  <p class="text-[10px] text-[var(--sb-faint)] -mt-1">{{ t('chronicle.llmProviderHint') }}</p>
                  <div v-if="llmProvider === 'openai'" class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.openaiModelTitle')">{{ t('chronicle.openaiModel') }}</span>
                    <input v-model="openaiModel" type="text" :placeholder="t('chronicle.openaiModelPh')"
                      class="sb-input flex-1 font-mono text-[11px]" />
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.temperatureTitle')">{{ t('chronicle.temperature') }}</span>
                    <input v-model.number="temperature" type="range" min="0" max="1.5" step="0.1" class="flex-1 accent-teal-500" />
                    <span class="text-teal-400 font-mono w-10 text-right">{{ temperature.toFixed(1) }}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.numCtxTitle')">{{ t('chronicle.numCtx') }}</span>
                    <select v-model.number="numCtx" class="sb-select flex-1">
                      <option :value="8192">8192</option>
                      <option :value="16384">16384</option>
                      <option :value="32768">{{ t('chronicle.numCtxRecommended') }}</option>
                    </select>
                  </div>
                </div>
              </details>

              <p v-if="asStringList(storySeedTags).length" class="text-[10px] text-amber-500/80 break-all">
                <span class="text-amber-300/80">{{ t('chronicle.seedTags') }}:</span>
                {{ joinList(storySeedTags) }}
                <span v-if="storySeedMotif"> · {{ t('chronicle.motifLabel') }}: {{ storySeedMotif }}</span>
              </p>
            </fieldset>

              <div class="flex items-center gap-3 flex-wrap">
                <button type="button" @click="start" :disabled="running || (!baseSha && !userTopic.trim())"
                  class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium
                    bg-teal-800 hover:bg-teal-700 disabled:opacity-40 text-teal-50 transition-colors">
                  <span v-if="running" class="chronicle-shuttle-mini" aria-hidden="true">
                    <span class="chronicle-shuttle-mini__yarn"></span>
                    <span class="chronicle-shuttle-mini__body"></span>
                  </span>
                  <SbIcon v-else name="weave" class="w-4 h-4" />
                  {{ running ? t('chronicle.running') : t('chronicle.start') }}
                </button>
                <label
                  class="flex items-center gap-1.5 cursor-pointer text-xs"
                  :class="fastMode ? 'text-amber-300' : 'text-[var(--sb-muted)]'"
                  :title="t('chronicle.fastModeTitle')"
                >
                  <input v-model="fastMode" type="checkbox" class="accent-amber-500" />
                  {{ t('chronicle.fastMode') }}
                </label>
                <button v-if="running && groupId" type="button" @click="cancelGroup"
                  class="sb-btn text-red-300 border-red-800/40">
                  {{ t('chronicle.cancel') }}
                </button>
                <span v-if="seed !== null" class="text-[10px] text-[var(--sb-faint)] font-mono ml-auto">
                  {{ t('chronicle.seedLabel') }}: {{ seed }}
                </span>
              </div>

            <!-- generate images (manual / no-workflow continue) -->
            <div v-if="canGenerate" class="flex items-center gap-3 flex-wrap">
              <button @click="generateImages" :disabled="!workflow"
                class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium
                  bg-teal-900/80 hover:bg-teal-800 disabled:opacity-40 text-teal-100 border border-teal-700/40 transition-colors">
                <SbIcon name="image" class="w-4 h-4" />
                {{ t('chronicle.generateImages') }}
              </button>
              <span v-if="!workflow" class="text-[10px] text-amber-400/80">{{ t('chronicle.noWorkflowHint') }}</span>
            </div>

            <button v-if="finished" @click="emit('open-storybook')"
              class="self-start sb-btn border-teal-700/40 text-teal-200">
              <SbIcon name="book" class="w-3.5 h-3.5" />
              {{ t('chronicle.openStorybook') }}
            </button>

            <p v-if="errorMsg" class="text-xs text-red-400">{{ errorMsg }}</p>
            <p v-if="renderError" class="text-xs text-amber-400">{{ renderError }}</p>
          </div>

          <!-- ── RIGHT: candidates / story / prompts ──────────────────────── -->
          <div class="flex flex-col gap-4 min-w-0">

            <div v-if="currentStep === 0 && !running && !streamText"
              class="flex-1 flex flex-col items-center justify-center min-h-[220px] text-center px-4 py-6 gap-4">
              <p class="sb-display text-base text-[var(--sb-muted)] leading-relaxed whitespace-pre-line">
                {{ t('chronicle.idleHint') }}
              </p>
            </div>

            <!-- Status HUD: full-stage loom while waiting for first content -->
            <div v-if="showWeaverStage"
              class="chronicle-weaver flex flex-col items-center justify-center gap-4 min-h-[220px] px-4 py-6 rounded-2xl border border-teal-800/30 bg-gradient-to-b from-teal-950/40 via-black/20 to-transparent"
              role="status" :aria-label="weaverCaption">
              <div class="chronicle-loom" aria-hidden="true">
                <div class="chronicle-loom__frame">
                  <span class="chronicle-loom__peg chronicle-loom__peg--l"></span>
                  <span class="chronicle-loom__peg chronicle-loom__peg--r"></span>
                  <span class="chronicle-loom__warp chronicle-loom__warp--1"></span>
                  <span class="chronicle-loom__warp chronicle-loom__warp--2"></span>
                  <span class="chronicle-loom__warp chronicle-loom__warp--3"></span>
                  <span class="chronicle-loom__weft"></span>
                  <span class="chronicle-loom__shuttle">
                    <span class="chronicle-loom__shuttle-eye"></span>
                  </span>
                  <span class="chronicle-loom__stitch chronicle-loom__stitch--1"></span>
                  <span class="chronicle-loom__stitch chronicle-loom__stitch--2"></span>
                  <span class="chronicle-loom__stitch chronicle-loom__stitch--3"></span>
                </div>
                <div class="chronicle-loom__bobbin">
                  <span class="chronicle-loom__bobbin-core"></span>
                  <span class="chronicle-loom__bobbin-thread"></span>
                </div>
                <span class="chronicle-loom__spark chronicle-loom__spark--1"></span>
                <span class="chronicle-loom__spark chronicle-loom__spark--2"></span>
                <span class="chronicle-loom__spark chronicle-loom__spark--3"></span>
              </div>
              <div class="text-center space-y-1.5 max-w-xs">
                <p class="sb-display text-sm text-teal-200/95 tracking-wide">{{ weaverCaption }}</p>
                <p class="text-[11px] text-[var(--sb-muted)] leading-relaxed">{{ t('chronicle.weaverPatience') }}</p>
              </div>

              <!-- Progress: pipeline XOR imageGen -->
              <div v-if="showPipelineProgress" class="w-full max-w-sm flex flex-col gap-1.5">
                <div class="flex items-center justify-between text-[10px] text-teal-300/90">
                  <span class="truncate">{{ phaseLabel }}</span>
                  <span class="font-mono shrink-0 ml-2">{{ Math.round(progress * 100) }}%</span>
                </div>
                <div class="sb-progress chronicle-progress-alive">
                  <div class="sb-progress-bar chronicle-progress-bar-alive"
                    :style="{ width: (progress * 100) + '%' }"></div>
                </div>
              </div>
              <div v-else-if="showImageProgress" class="w-full max-w-sm flex flex-col gap-2">
                <div class="flex flex-wrap gap-2 justify-center">
                  <div v-for="j in imageJobs" :key="j.job_id"
                    class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[10px]"
                    :class="jobStatusClass(j.job_id)">
                    <span class="font-bold uppercase tracking-wide">{{ t('chronicle.axis.' + j.axis) }}</span>
                    <SbIcon :name="jobStatusIcon(j.job_id)" class="w-3 h-3" />
                    <span class="opacity-70">{{ jobStatusLabel(j.job_id) }}</span>
                  </div>
                </div>
                <div class="flex flex-col gap-1">
                  <div class="flex items-center justify-between text-[10px]"
                    :class="imageGen.active ? 'text-teal-300/90' : 'text-[var(--sb-muted)]'">
                    <span class="truncate">{{ t('chronicle.imagesGenerating') }}<span v-if="imageGen.text" class="text-[var(--sb-faint)]"> · {{ imageGen.text }}</span></span>
                    <span class="font-mono shrink-0 ml-2">{{ Math.round(imageGen.progress * 100) }}%</span>
                  </div>
                  <div class="sb-progress" :class="imageGen.active ? 'chronicle-progress-alive' : ''">
                    <div class="sb-progress-bar"
                      :class="imageGen.active ? 'chronicle-progress-bar-alive' : ''"
                      :style="{ width: Math.max(imageGen.progress * 100, imageGen.active ? 4 : 0) + '%' }"></div>
                  </div>
                </div>
              </div>
              <p v-else-if="phase" class="text-[11px] text-teal-300/90 uppercase tracking-wide">
                {{ phaseLabel }}
              </p>

              <div v-if="statusHudRows.length" class="w-full max-w-sm">
                <p class="text-[9px] uppercase tracking-wider text-teal-500/70 mb-1.5 text-center">
                  {{ t('chronicle.statusHud.timings') }}
                </p>
                <ul class="space-y-0.5 max-h-28 overflow-y-auto text-[10px] font-mono">
                  <li v-for="(row, ri) in statusHudRows" :key="row.code + '-' + ri"
                    class="flex items-center justify-between gap-2 px-1"
                    :class="row.active ? 'text-teal-200' : 'text-[var(--sb-muted)]'">
                    <span class="truncate">{{ row.label }}</span>
                    <span class="shrink-0 tabular-nums">{{ formatDurationMs(row.duration_ms) }}</span>
                  </li>
                </ul>
              </div>

              <div v-if="weaverTeasers.length" class="weaver-teaser-row w-full max-w-md">
                <p class="text-[9px] uppercase tracking-wider text-teal-500/70 mb-1.5 text-center">{{ t('chronicle.weaverTeaser') }}</p>
                <div class="flex flex-col gap-1.5">
                  <div
                    v-for="te in weaverTeasers"
                    :key="te.axis"
                    class="weaver-teaser-card"
                  >
                    <span class="weaver-teaser-card__axis">{{ t('chronicle.axis.' + te.axis) }}</span>
                    <span class="weaver-teaser-card__text">{{ te.snippet }}…</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Compact Status HUD ribbon when content already fills the pane -->
            <div v-else-if="isBusyWeaving || showStatusHud"
              class="chronicle-weaver-ribbon flex flex-col gap-2 px-3 py-2.5 rounded-xl border border-teal-800/30 bg-teal-950/25"
              role="status" :aria-label="weaverCaption">
              <div class="flex items-center gap-3">
                <div v-if="isBusyWeaving" class="chronicle-loom chronicle-loom--sm" aria-hidden="true">
                  <div class="chronicle-loom__frame">
                    <span class="chronicle-loom__warp chronicle-loom__warp--1"></span>
                    <span class="chronicle-loom__warp chronicle-loom__warp--2"></span>
                    <span class="chronicle-loom__warp chronicle-loom__warp--3"></span>
                    <span class="chronicle-loom__weft"></span>
                    <span class="chronicle-loom__shuttle">
                      <span class="chronicle-loom__shuttle-eye"></span>
                    </span>
                  </div>
                </div>
                <div class="min-w-0 flex-1 flex flex-col gap-1">
                  <p class="text-[11px] text-teal-200/95 truncate">
                    {{ isBusyWeaving ? weaverCaption : t('chronicle.statusHud.title') }}
                  </p>
                  <div v-if="showPipelineProgress" class="flex flex-col gap-0.5">
                    <div class="flex items-center justify-between text-[10px] text-teal-300/90">
                      <span class="truncate">{{ phaseLabel }}</span>
                      <span class="font-mono shrink-0 ml-2">{{ Math.round(progress * 100) }}%</span>
                    </div>
                    <div class="sb-progress chronicle-progress-alive h-1">
                      <div class="sb-progress-bar chronicle-progress-bar-alive"
                        :style="{ width: (progress * 100) + '%' }"></div>
                    </div>
                  </div>
                  <div v-else-if="showImageProgress" class="flex flex-col gap-1">
                    <div class="flex flex-wrap gap-1">
                      <div v-for="j in imageJobs" :key="j.job_id"
                        class="flex items-center gap-1 px-1.5 py-0.5 rounded border text-[9px]"
                        :class="jobStatusClass(j.job_id)">
                        <span class="font-bold uppercase">{{ t('chronicle.axis.' + j.axis) }}</span>
                        <SbIcon :name="jobStatusIcon(j.job_id)" class="w-2.5 h-2.5" />
                      </div>
                    </div>
                    <div class="sb-progress" :class="imageGen.active ? 'chronicle-progress-alive' : ''">
                      <div class="sb-progress-bar"
                        :class="imageGen.active ? 'chronicle-progress-bar-alive' : ''"
                        :style="{ width: Math.max(imageGen.progress * 100, imageGen.active ? 4 : 0) + '%' }"></div>
                    </div>
                  </div>
                  <p v-else-if="phase && isBusyWeaving" class="text-[10px] text-[var(--sb-muted)] truncate">
                    {{ phaseLabel }}
                  </p>
                </div>
                <span v-if="isBusyWeaving" class="chronicle-weaver-dots text-teal-400/80 font-mono text-xs shrink-0" aria-hidden="true">
                  <span>.</span><span>.</span><span>.</span>
                </span>
              </div>
              <details v-if="statusHudRows.length" class="text-[10px]">
                <summary class="cursor-pointer text-teal-400/80 select-none">
                  {{ t('chronicle.statusHud.timings') }}
                  <span class="text-[var(--sb-faint)] font-mono">({{ statusHudRows.length }})</span>
                </summary>
                <ul class="mt-1.5 space-y-0.5 max-h-24 overflow-y-auto font-mono">
                  <li v-for="(row, ri) in statusHudRows" :key="'r-' + row.code + '-' + ri"
                    class="flex items-center justify-between gap-2"
                    :class="row.active ? 'text-teal-200' : 'text-[var(--sb-muted)]'">
                    <span class="truncate">{{ row.label }}</span>
                    <span class="shrink-0 tabular-nums">{{ formatDurationMs(row.duration_ms) }}</span>
                  </li>
                </ul>
              </details>
            </div>

            <!-- candidates -->
            <div v-if="visibleCandidates.length" class="flex flex-col gap-2">
              <div class="flex items-center justify-between">
                <h3 class="sb-label text-teal-300/90">{{ t('chronicle.candidatesTitle') }}</h3>
                <button v-if="selecting" @click="respin('candidates')" :disabled="running" class="sb-btn">
                  <SbIcon name="refresh" class="w-3 h-3" />
                  {{ t('chronicle.respinCandidates') }}
                </button>
              </div>
              <div class="grid gap-2"
                :class="visibleCandidates.length > 1 ? 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-3' : 'grid-cols-1'">
                <button v-for="c in visibleCandidates" :key="c.id" @click="selectCandidate(c.id)"
                  :disabled="running || (!selecting && selectedCandidate === c.id && !finished)"
                  class="text-left flex flex-col gap-1.5 p-3 rounded-xl border transition-colors disabled:opacity-50"
                  :class="selectedCandidate === c.id
                    ? 'border-teal-500/60 bg-teal-950/30'
                    : 'border-white/8 bg-black/25 hover:border-teal-600/40 hover:bg-black/35'">
                  <div class="flex items-center gap-1.5">
                    <span class="text-[9px] font-bold w-4 h-4 flex items-center justify-center rounded-full bg-black/40 text-gray-200">{{ c.id }}</span>
                    <span class="text-sm font-bold text-teal-100 leading-tight">{{ candDisplay(c, 'title') }}</span>
                  </div>
                  <p v-if="c.turn" class="text-[11px] text-gray-200 leading-snug">{{ candDisplay(c, 'turn') }}</p>
                  <div v-if="c.motif || c.key_motif" class="pt-0.5">
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-teal-950/50 text-teal-200/95 border border-teal-800/30">
                      {{ t('chronicle.motifLabel') }}: {{ candDisplay(c, 'motif') || c.key_motif }}
                    </span>
                  </div>
                  <div class="flex flex-col gap-0.5 text-[9px] leading-snug opacity-45 mt-0.5">
                    <div v-for="ax in AXES" :key="ax" v-show="candAct(c, ax).activity || c[ax] || c.summary">
                      <span class="font-bold uppercase tracking-wide mr-1"
                        :class="ax === baseAxis ? 'text-[var(--sb-amber)]' : 'text-teal-400/70'">{{ t('chronicle.axis.' + ax) }}</span>
                      <span v-if="candAct(c, ax).label" class="text-teal-300/60 mr-1">[{{ candAct(c, ax).label }}]</span>
                      <span class="text-gray-400">{{ candAct(c, ax).activity || c[ax] || c.summary || '' }}</span>
                      <span v-if="candAct(c, ax).place" class="ml-1 px-1 rounded bg-black/40 text-gray-500">📍{{ candAct(c, ax).place }}</span>
                      <span v-if="candAct(c, ax).feeling" class="ml-1 px-1 rounded bg-black/40 text-gray-500">{{ candAct(c, ax).feeling }}</span>
                    </div>
                  </div>
                  <span v-if="selecting || finished" class="text-[10px] font-medium mt-1 text-teal-400/90">
                    {{ finished ? t('chronicle.forkFromCandidate') : t('chronicle.candidateSelect') }} →
                  </span>
                </button>
              </div>
            </div>

            <!-- title + overall -->
            <div v-if="displayTitle" class="flex flex-col gap-2">
              <div class="flex items-center gap-2 flex-wrap">
                <h3 class="sb-display text-base text-teal-200 flex-1 min-w-0">{{ displayTitle }}</h3>
                <button v-if="finished" @click="respin('expand')" :disabled="running" class="sb-btn">
                  <SbIcon name="refresh" class="w-3 h-3" />
                  {{ t('chronicle.respinStory') }}
                </button>
                <div v-if="titleJa || overallJa || hasAxisStories" class="sb-seg">
                  <button v-for="l in ['ja', 'en']" :key="l" @click="panelLang = l"
                    :class="panelLang === l ? 'is-on-teal' : ''"
                    class="sb-seg-btn uppercase">{{ l }}</button>
                </div>
              </div>
              <p v-if="displayOverall" class="sb-prose border-l-2 border-teal-700/40 pl-3">
                {{ displayOverall }}
              </p>
            </div>

            <!-- structured past / present / future (always visible once known) -->
            <div v-if="hasAxisStories" class="flex flex-col gap-3">
              <div v-for="axis in AXES" :key="'story-' + axis"
                v-show="displayAxisStory(axis)"
                class="rounded-xl border border-white/5 bg-black/30 p-3 flex flex-col gap-1.5">
                <span class="text-[10px] font-semibold uppercase tracking-widest"
                  :class="axis === baseAxis ? 'text-[var(--sb-amber)]' : 'text-teal-400/90'">
                  {{ t('chronicle.axis.' + axis) }}
                  <span v-if="axis === baseAxis" class="text-[var(--sb-muted)] normal-case font-normal ml-1">
                    ({{ t('storybook.base') }})
                  </span>
                </span>
                <p class="sb-prose text-sm">{{ displayAxisStory(axis) }}</p>
              </div>
            </div>

            <!-- Quality radar / scoring error -->
            <div v-if="qualityEvalFailed"
              class="rounded-xl border border-amber-800/40 bg-amber-950/25 p-3 flex flex-col gap-2">
              <div class="flex items-center gap-2">
                <SbIcon name="close" class="w-3.5 h-3.5 text-amber-400/80" />
                <h3 class="sb-label text-amber-200/90 mb-0">{{ t('storybook.quality.title') }}</h3>
              </div>
              <p class="text-[11px] text-amber-100/85 leading-relaxed">
                {{ t('storybook.quality.scoringFailed', {
                  reason: qualityEval.error || t('storybook.quality.failed'),
                }) }}
              </p>
            </div>
            <div v-else-if="qualityEvalHasRadar"
              class="rounded-xl border border-teal-800/30 bg-black/30 p-3 flex flex-col gap-3">
              <div class="flex items-center gap-2">
                <SbIcon name="spark" class="w-3.5 h-3.5 text-teal-400/80" />
                <h3 class="sb-label text-teal-300/90 mb-0">{{ t('storybook.quality.title') }}</h3>
                <span v-if="qualityOverallPct != null" class="ml-auto text-sm font-mono text-teal-300/90">
                  {{ qualityOverallPct }}
                </span>
              </div>
              <StoryQualityRadar :eval="qualityEval" />
              <div v-if="chronicleQualityActions().length" class="flex flex-wrap gap-1.5">
                <button
                  v-for="act in chronicleQualityActions()"
                  :key="act.id"
                  type="button"
                  class="sb-btn border-teal-700/40 text-teal-100"
                  :disabled="running"
                  @click="act.run()"
                >{{ act.label }}</button>
              </div>
              <p v-if="qualityDraftNote" class="text-[10px] font-mono text-teal-300/70">
                {{ qualityDraftNote }}
              </p>
              <p class="text-[10px] text-[var(--sb-muted)] leading-relaxed">
                {{ t('storybook.quality.hint') }}
              </p>
            </div>

            <!-- Draft materials: always open while creating (not buried in <details>) -->
            <div v-if="hasDraftMaterials" class="flex flex-col gap-2">
              <h3 class="sb-label text-teal-300/90 flex items-center gap-1.5">
                <SbIcon name="spark" class="w-3.5 h-3.5" />
                {{ t('chronicle.draftMaterials') }}
                <span v-if="running" class="normal-case font-normal text-[var(--sb-muted)]">
                  · {{ t('chronicle.draftInProgress') }}
                </span>
              </h3>

              <div v-if="bioView" class="rounded-xl border border-white/5 bg-black/30 p-3 text-[11px] text-gray-300 space-y-1">
                <div class="sb-label text-teal-300/80 flex items-center gap-1">
                  <SbIcon name="book" class="w-3 h-3" />{{ t('storybook.biography') }}
                </div>
                <p v-if="bioView.personality">{{ bioView.personality }}</p>
                <p v-if="bioView.occupation" class="text-gray-400 break-words">
                  <span class="text-[var(--sb-muted)]">{{ t('storybook.bioOccupation') }}:</span> {{ bioView.occupation }}
                </p>
                <p v-for="f in BIO_LIST_FIELDS" :key="f" v-show="asStringList(bioView[f]).length" class="text-gray-400 break-words">
                  <span class="text-[var(--sb-muted)]">{{ t('storybook.bio_' + f) }}:</span> {{ joinList(bioView[f]) }}
                </p>
                <p v-if="bioView.backstory" class="text-gray-400 italic pt-1">{{ bioView.backstory }}</p>
              </div>

              <div v-if="timetableView.length" class="rounded-xl border border-white/5 bg-black/30 p-3 text-[11px]">
                <div class="sb-label text-teal-300/80 mb-1 flex items-center gap-1">
                  <SbIcon name="clock" class="w-3 h-3" />{{ t('storybook.timetable') }}
                </div>
                <ul class="space-y-0.5">
                  <li v-for="(s, si) in timetableView" :key="si"
                    class="text-gray-300 flex gap-2 items-start rounded-md px-1 -mx-1 py-0.5"
                    :class="slotUsedAs(s) ? 'bg-teal-950/40 ring-1 ring-teal-800/35' : ''">
                    <span class="text-teal-400/80 shrink-0 w-20 pt-0.5">{{ s.label }}</span>
                    <span class="min-w-0 flex-1">
                      <span class="inline-flex flex-wrap items-center gap-1 mb-0.5" v-if="slotUsedAs(s) || slotNeighbors(s).length">
                        <span v-if="slotUsedAs(s)"
                          class="text-[9px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-teal-900/60 text-teal-200 border border-teal-700/40">
                          {{ t('chronicle.axis.' + slotUsedAs(s)) }}
                        </span>
                        <span v-for="nb in slotNeighbors(s)" :key="nb"
                          class="text-[9px] uppercase tracking-wide px-1 py-0.5 rounded text-teal-500/55 border border-teal-900/40">
                          {{ t('storybook.timetableNeighbor', { axis: t('chronicle.axis.' + nb) }) }}
                        </span>
                      </span>
                      <span class="block">
                        {{ s.activity }}
                        <span v-if="s.place" class="text-[var(--sb-muted)]"> {{ t('storybook.timetablePlace', { place: s.place }) }}</span>
                        <span v-if="s.feeling" class="text-gray-500 italic"> {{ t('storybook.timetableFeeling', { feeling: s.feeling }) }}</span>
                      </span>
                    </span>
                  </li>
                </ul>
              </div>

              <div v-for="axis in REASON_AXES" :key="'act-' + axis"
                v-show="activityFor(axis)"
                class="rounded-xl border border-white/5 bg-black/30 p-3 text-[11px]">
                <div class="sb-label text-[var(--sb-amber)] mb-1">{{ t('chronicle.axis.' + axis) }}</div>
                <p class="text-gray-300">{{ activityFor(axis) }}</p>
              </div>

              <!-- Phase-B draft images (live preview only; not kept on finished story) -->
              <div v-if="draftImageAxes.length"
                class="rounded-xl border border-teal-800/30 bg-black/30 p-3">
                <div class="sb-label text-teal-300/80 mb-2 flex items-center gap-1">
                  <SbIcon name="image" class="w-3 h-3" />{{ t('chronicle.draftImages') }}
                </div>
                <div class="grid grid-cols-3 gap-2">
                  <div v-for="axis in draftImageAxes" :key="'draft-img-' + axis"
                    class="flex flex-col gap-1 min-w-0">
                    <span class="text-[9px] uppercase tracking-wider text-[var(--sb-amber)]">
                      {{ t('chronicle.axis.' + axis) }}
                    </span>
                    <a :href="`/api/originals/${axisDrafts[axis].draft_image_id}`"
                      target="_blank" rel="noopener"
                      class="block aspect-square rounded-lg overflow-hidden border border-white/10 bg-black/40">
                      <img :src="`/api/thumbnails/${axisDrafts[axis].draft_image_id}.webp`"
                        :alt="t('chronicle.draftImageAlt', { axis: t('chronicle.axis.' + axis) })"
                        class="w-full h-full object-cover" loading="lazy" />
                    </a>
                  </div>
                </div>
              </div>
            </div>

            <!-- live expand stream (open) until structured acts arrive -->
            <div v-if="showLiveStream"
              class="rounded-xl border border-teal-800/40 bg-black/40 p-3 text-xs text-gray-300 whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto font-light">
              {{ streamText }}<span class="animate-pulse text-teal-400">▍</span>
            </div>

            <!-- raw stream (open while running so Stage1/2 exec logs stay visible) -->
            <details v-else-if="streamText || (running && !selecting)"
              class="rounded-xl border border-white/5 bg-black/30"
              :open="running || !!streamText">
              <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                <span class="flex items-center gap-1.5">
                  <SbIcon name="doc" class="w-3.5 h-3.5" />
                  {{ t('chronicle.rawStream') }}
                </span>
              </summary>
              <pre class="px-3 pb-3 text-xs text-gray-400 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto font-light">{{ streamText }}<span v-if="running" class="animate-pulse text-teal-400">▍</span></pre>
            </details>

            <!-- shot / tag / draft-tag reasoning (secondary — may stay folded) -->
            <details v-if="hasShotReasoning" class="rounded-xl border border-white/5 bg-black/25">
              <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                <span class="flex items-center gap-1.5">
                  <SbIcon name="spark" class="w-3.5 h-3.5" />
                  {{ t('chronicle.reasoning') }}
                </span>
              </summary>
              <div class="px-3 pb-3 flex flex-col gap-2">
                <div v-for="axis in REASON_AXES" :key="'shot-' + axis"
                  v-show="axisReasoning[axis] || axisDrafts[axis]"
                  class="bg-black/40 border border-white/5 rounded-xl p-3 text-[11px]">
                  <div class="sb-label text-[var(--sb-amber)] mb-1">{{ t('chronicle.axis.' + axis) }}</div>
                  <div v-if="axisReasoning[axis]" class="text-[10px] text-[var(--sb-muted)] space-y-0.5">
                    <p v-if="axisReasoning[axis].shot || axisReasoning[axis].camera">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonShot') }}:</span>
                      {{ [axisReasoning[axis].shot, axisReasoning[axis].camera].filter(Boolean).join(' / ') }}
                    </p>
                    <p v-if="asStringList(axisReasoning[axis].focal).length">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonPose') }}:</span>
                      {{ joinList(axisReasoning[axis].focal) }}
                    </p>
                    <p v-if="asStringList(axisReasoning[axis].search_tags).length
                      && !asStringList(axisReasoning[axis].similar_mix_tags).length" class="break-words">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonTags') }}:</span>
                      {{ joinList(axisReasoning[axis].search_tags) }}
                    </p>
                    <details
                      v-if="asStringList(axisReasoning[axis].similar_mix_tags).length
                        || asStringList(axisReasoning[axis].similar_mix_sources).length"
                      class="mt-1 rounded-lg border border-teal-800/40 bg-teal-950/20"
                    >
                      <summary class="cursor-pointer px-2 py-1 text-[10px] text-teal-300/90 list-none">
                        {{ t('chronicle.similarMixTagsCount', {
                          n: asStringList(axisReasoning[axis].similar_mix_tags).length,
                          m: asStringList(axisReasoning[axis].similar_mix_sources).length,
                        }) }}
                      </summary>
                      <div class="px-2 pb-2 space-y-1.5">
                        <div v-if="asStringList(axisReasoning[axis].similar_mix_sources).length"
                          class="flex flex-wrap gap-1">
                          <a v-for="sha in asStringList(axisReasoning[axis].similar_mix_sources)" :key="sha"
                            :href="`/api/originals/${sha}`" target="_blank" rel="noopener"
                            class="w-10 h-10 rounded overflow-hidden border border-teal-700/40 bg-black/40">
                            <img :src="`/api/thumbnails/${sha}.webp`" class="w-full h-full object-cover" loading="lazy" />
                          </a>
                        </div>
                        <p class="break-words text-teal-200/80">
                          {{ joinList(axisReasoning[axis].similar_mix_tags) }}
                        </p>
                      </div>
                    </details>
                  </div>
                  <div v-if="axisDrafts[axis]" class="mt-2 text-[10px] text-[var(--sb-muted)]">
                    <a v-if="axisDrafts[axis].draft_image_id"
                      :href="`/api/originals/${axisDrafts[axis].draft_image_id}`"
                      target="_blank" rel="noopener"
                      class="block w-28 aspect-square mb-1.5 rounded-lg overflow-hidden border border-white/10 bg-black/40">
                      <img :src="`/api/thumbnails/${axisDrafts[axis].draft_image_id}.webp`"
                        class="w-full h-full object-cover" loading="lazy" />
                    </a>
                    <p v-if="asStringList(axisDrafts[axis].draft_tags).length" class="break-words">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonDraftTags') }}:</span>
                      {{ joinList(axisDrafts[axis].draft_tags) }}
                    </p>
                    <p v-if="axisDrafts[axis].draft_richness_delta"
                      class="mt-0.5 font-mono text-teal-300/70">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonDraftDelta') }}:</span>
                      {{ Number(axisDrafts[axis].draft_richness_delta.before || 0).toFixed(2) }}
                      → {{ Number(axisDrafts[axis].draft_richness_delta.after || 0).toFixed(2) }}
                      ({{ (Number(axisDrafts[axis].draft_richness_delta.delta || 0) >= 0 ? '+' : '')
                        + Number(axisDrafts[axis].draft_richness_delta.delta || 0).toFixed(2) }})
                    </p>
                  </div>
                </div>
              </div>
            </details>

            <!-- prompts -->
            <div v-if="Object.keys(prompts).length" class="flex flex-col gap-3">
              <h3 class="sb-label">{{ t('chronicle.prompts') }}</h3>
              <div v-for="(p, axis) in prompts" :key="axis"
                class="bg-black/30 border border-white/5 rounded-xl p-3 flex flex-col gap-2">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-[10px] font-bold text-teal-400 uppercase">{{ t('chronicle.axis.' + axis) }}</span>
                  <span v-if="p.refined_from_draft"
                    class="text-[9px] px-1.5 py-0.5 rounded bg-teal-900/50 text-teal-300/90 border border-teal-700/40">
                    {{ t('chronicle.refinedFromDraft') }}
                  </span>
                  <span v-if="p.draft_richness_delta"
                    class="text-[9px] font-mono text-teal-300/70 ml-auto">
                    {{ t('chronicle.reasonDraftDelta') }}
                    {{ Number(p.draft_richness_delta.before || 0).toFixed(2) }}
                    → {{ Number(p.draft_richness_delta.after || 0).toFixed(2) }}
                    ({{ (Number(p.draft_richness_delta.delta || 0) >= 0 ? '+' : '')
                      + Number(p.draft_richness_delta.delta || 0).toFixed(2) }})
                  </span>
                </div>
                <textarea v-model="p.positive" rows="3" :readonly="!canGenerate"
                  class="sb-textarea text-xs"></textarea>
                <textarea v-model="p.negative" rows="1" :readonly="!canGenerate" :placeholder="t('chronicle.negative')"
                  class="sb-textarea text-xs text-[var(--sb-muted)]"></textarea>
                <div v-if="axisHasCatTags(p)"
                  class="mt-2 border-l-2 border-[var(--sb-rule)] pl-3 space-y-1.5">
                  <p class="sb-label">{{ t('chronicle.visualSpecTitle') }}</p>
                  <p v-if="p.visual_script"
                    class="text-[11px] text-[var(--sb-muted)] whitespace-pre-wrap leading-relaxed">
                    {{ p.visual_script }}
                  </p>
                  <p v-for="g in CAT_TAG_GROUPS" :key="g.key"
                    v-show="asStringList(p[g.key]).length"
                    class="text-[10px]">
                    <span class="text-[var(--sb-faint)]">{{ t('chronicle.' + g.label) }}:</span>
                    <span class="font-mono text-gray-400">{{ joinList(p[g.key]) }}</span>
                  </p>
                  <details
                    v-if="asStringList(p.similar_mix_tags).length || asStringList(p.similar_mix_sources).length"
                    class="rounded-lg border border-teal-800/40 bg-teal-950/20"
                  >
                    <summary class="cursor-pointer px-2 py-1 text-[10px] text-teal-300/90 list-none">
                      {{ t('chronicle.similarMixTagsCount', {
                        n: asStringList(p.similar_mix_tags).length,
                        m: asStringList(p.similar_mix_sources).length,
                      }) }}
                    </summary>
                    <div class="px-2 pb-2 space-y-1.5">
                      <div v-if="asStringList(p.similar_mix_sources).length" class="flex flex-wrap gap-1">
                        <a v-for="sha in asStringList(p.similar_mix_sources)" :key="'mix-' + sha"
                          :href="`/api/originals/${sha}`" target="_blank" rel="noopener"
                          class="w-10 h-10 rounded overflow-hidden border border-teal-700/40 bg-black/40">
                          <img :src="`/api/thumbnails/${sha}.webp`" class="w-full h-full object-cover" loading="lazy" />
                        </a>
                      </div>
                      <p class="text-[10px] font-mono text-teal-200/80 break-words">
                        {{ joinList(p.similar_mix_tags) }}
                      </p>
                    </div>
                  </details>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.chronicle-root {
  background: radial-gradient(ellipse at 30% 20%, rgba(45, 212, 191, 0.08), transparent 50%),
              rgba(0, 0, 0, 0.82);
}

/* Low-power steps (match style.css) — avoid continuous 60fps compositor work */
@keyframes lamp-pulse {
  0%, 100% { opacity: 0.35; }
  50%      { opacity: 1; }
}
.chronicle-lamp.is-running {
  animation: lamp-pulse 3s steps(2, start) infinite;
}
@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(45, 212, 191, 0.35); }
  50%      { box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.12); }
}
.chronicle-dot-active {
  animation: dot-pulse 3s steps(2, start) infinite;
}

/* ── Cute loom / shuttle while Chronicle is busy ─────────────────────────── */
@keyframes chronicle-shuttle-weave {
  0%, 100% { transform: translateX(0); }
  50%      { transform: translateX(118px); }
}
@keyframes chronicle-weft-grow {
  0%, 100% { width: 12%; opacity: 0.55; }
  50%      { width: 88%; opacity: 0.95; }
}
@keyframes chronicle-bobbin-bob {
  0%, 100% { transform: translateY(0) rotate(-8deg); }
  50%      { transform: translateY(5px) rotate(8deg); }
}
@keyframes chronicle-spark {
  0%, 100% { opacity: 0; transform: scale(0.4); }
  40%      { opacity: 0.9; transform: scale(1); }
  70%      { opacity: 0; transform: scale(0.6); }
}
@keyframes chronicle-stitch-pop {
  0%, 100% { opacity: 0.15; transform: scaleY(0.4); }
  50%      { opacity: 0.95; transform: scaleY(1); }
}
@keyframes chronicle-mini-hop {
  0%, 100% { transform: translateY(0) rotate(-12deg); }
  50%      { transform: translateY(-3px) rotate(12deg); }
}
@keyframes chronicle-yarn-spin {
  0%   { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
@keyframes chronicle-progress-shimmer {
  0%   { background-position: 0% 50%; }
  100% { background-position: 200% 50%; }
}
@keyframes chronicle-dot-wave {
  0%, 80%, 100% { opacity: 0.2; transform: translateY(0); }
  40%           { opacity: 1; transform: translateY(-2px); }
}

.chronicle-loom {
  position: relative;
  width: 168px;
  height: 88px;
}
.chronicle-loom--sm {
  width: 72px;
  height: 36px;
  flex-shrink: 0;
}
.chronicle-loom__frame {
  position: absolute;
  inset: 18% 4% 28% 4%;
  border: 1.5px solid rgba(45, 212, 191, 0.35);
  border-radius: 6px;
  background:
    linear-gradient(180deg, rgba(13, 148, 136, 0.12), transparent 55%),
    rgba(0, 0, 0, 0.25);
  overflow: hidden;
}
.chronicle-loom--sm .chronicle-loom__frame {
  inset: 10% 2% 18% 2%;
  border-radius: 4px;
}
.chronicle-loom__peg {
  position: absolute;
  top: -5px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5eead4;
  box-shadow: 0 0 0 2px rgba(13, 148, 136, 0.35);
}
.chronicle-loom__peg--l { left: 10%; }
.chronicle-loom__peg--r { right: 10%; }

.chronicle-loom__warp {
  position: absolute;
  left: 8%;
  right: 8%;
  height: 1.5px;
  border-radius: 1px;
  background: rgba(94, 234, 212, 0.35);
}
.chronicle-loom__warp--1 { top: 28%; }
.chronicle-loom__warp--2 { top: 50%; background: rgba(251, 191, 36, 0.4); }
.chronicle-loom__warp--3 { top: 72%; background: rgba(125, 211, 252, 0.4); }

.chronicle-loom__weft {
  position: absolute;
  left: 8%;
  top: 48%;
  height: 2px;
  border-radius: 2px;
  background: linear-gradient(90deg, #2dd4bf, #fbbf24, #7dd3fc);
  animation: chronicle-weft-grow 2.4s ease-in-out infinite;
  transform-origin: left center;
}
.chronicle-loom--sm .chronicle-loom__weft {
  height: 1.5px;
  animation-duration: 1.8s;
}

.chronicle-loom__shuttle {
  position: absolute;
  top: 38%;
  left: 6%;
  width: 22px;
  height: 10px;
  border-radius: 999px;
  background: linear-gradient(180deg, #99f6e4, #0f766e);
  border: 1px solid rgba(204, 251, 241, 0.5);
  box-shadow: 0 1px 0 rgba(0, 0, 0, 0.25);
  animation: chronicle-shuttle-weave 2.4s ease-in-out infinite;
  z-index: 2;
}
.chronicle-loom--sm .chronicle-loom__shuttle {
  width: 12px;
  height: 6px;
  top: 36%;
  animation-duration: 1.8s;
  animation-name: chronicle-shuttle-weave-sm;
}
@keyframes chronicle-shuttle-weave-sm {
  0%, 100% { transform: translateX(0); }
  50%      { transform: translateX(46px); }
}
.chronicle-loom__shuttle-eye {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 4px;
  height: 4px;
  margin: -2px 0 0 -2px;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.55);
}
.chronicle-loom--sm .chronicle-loom__shuttle-eye {
  width: 2px;
  height: 2px;
  margin: -1px 0 0 -1px;
}

.chronicle-loom__stitch {
  position: absolute;
  bottom: 10%;
  width: 3px;
  height: 10px;
  border-radius: 1px;
  background: #5eead4;
  animation: chronicle-stitch-pop 2.4s ease-in-out infinite;
}
.chronicle-loom__stitch--1 { left: 22%; animation-delay: 0s; background: #5eead4; }
.chronicle-loom__stitch--2 { left: 48%; animation-delay: 0.4s; background: #fbbf24; }
.chronicle-loom__stitch--3 { left: 74%; animation-delay: 0.8s; background: #7dd3fc; }

.chronicle-loom__bobbin {
  position: absolute;
  right: -2px;
  bottom: 0;
  width: 28px;
  height: 28px;
  animation: chronicle-bobbin-bob 2.4s ease-in-out infinite;
}
.chronicle-loom__bobbin-core {
  position: absolute;
  inset: 4px;
  border-radius: 50%;
  background:
    radial-gradient(circle at 35% 30%, #ccfbf1 0%, #14b8a6 45%, #0f766e 100%);
  border: 1.5px solid rgba(204, 251, 241, 0.45);
}
.chronicle-loom__bobbin-thread {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 2px dashed rgba(94, 234, 212, 0.55);
  border-top-color: transparent;
  animation: chronicle-yarn-spin 3.2s linear infinite;
}

.chronicle-loom__spark {
  position: absolute;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #fde68a;
  animation: chronicle-spark 2.4s ease-in-out infinite;
}
.chronicle-loom__spark--1 { top: 6%; left: 18%; animation-delay: 0.2s; }
.chronicle-loom__spark--2 { top: 0; right: 28%; animation-delay: 0.9s; background: #99f6e4; }
.chronicle-loom__spark--3 { bottom: 8%; left: 42%; animation-delay: 1.5s; background: #bae6fd; }

.chronicle-shuttle-mini {
  position: relative;
  width: 22px;
  height: 18px;
  flex-shrink: 0;
}
.chronicle-shuttle-mini__body {
  position: absolute;
  left: 2px;
  top: 6px;
  width: 16px;
  height: 7px;
  border-radius: 999px;
  background: linear-gradient(180deg, #99f6e4, #0f766e);
  border: 1px solid rgba(204, 251, 241, 0.45);
  animation: chronicle-mini-hop 1.4s ease-in-out infinite;
}
.chronicle-shuttle-mini__yarn {
  position: absolute;
  right: 0;
  top: 0;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #ccfbf1, #0d9488);
  border: 1px solid rgba(204, 251, 241, 0.4);
  animation: chronicle-yarn-spin 2.8s linear infinite;
}
.chronicle-shuttle-mini.is-idle .chronicle-shuttle-mini__body,
.chronicle-shuttle-mini.is-idle .chronicle-shuttle-mini__yarn {
  animation: none;
  opacity: 0.45;
}

.chronicle-progress-alive {
  position: relative;
  overflow: hidden;
}
.chronicle-progress-bar-alive {
  background: linear-gradient(
    90deg,
    #0f766e 0%,
    #2dd4bf 35%,
    #fbbf24 50%,
    #2dd4bf 65%,
    #0f766e 100%
  );
  background-size: 200% 100%;
  animation: chronicle-progress-shimmer 2.2s linear infinite;
}

.chronicle-weaver-dots span {
  display: inline-block;
  animation: chronicle-dot-wave 1.2s ease-in-out infinite;
}
.chronicle-weaver-dots span:nth-child(2) { animation-delay: 0.15s; }
.chronicle-weaver-dots span:nth-child(3) { animation-delay: 0.3s; }

@media (prefers-reduced-motion: reduce) {
  .chronicle-loom__weft,
  .chronicle-loom__shuttle,
  .chronicle-loom__bobbin,
  .chronicle-loom__bobbin-thread,
  .chronicle-loom__spark,
  .chronicle-loom__stitch,
  .chronicle-shuttle-mini__body,
  .chronicle-shuttle-mini__yarn,
  .chronicle-progress-bar-alive,
  .chronicle-weaver-dots span {
    animation: none !important;
  }
  .chronicle-loom__weft { width: 70%; opacity: 0.85; }
  .chronicle-loom__shuttle { left: 42%; }
}

details > summary::-webkit-details-marker { display: none; }
fieldset:disabled { pointer-events: none; }

.weaver-teaser-card {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
  padding: 0.45rem 0.65rem;
  border-radius: 0.5rem;
  border: 1px solid rgba(45, 212, 191, 0.15);
  background: rgba(0, 0, 0, 0.35);
  animation: weaver-teaser-in 0.45s ease both;
}
.weaver-teaser-card__axis {
  flex-shrink: 0;
  font-size: 0.55rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #5eead4;
}
.weaver-teaser-card__text {
  font-size: 0.7rem;
  color: #cbd5e1;
  text-align: left;
  line-height: 1.35;
}
@keyframes weaver-teaser-in {
  from { opacity: 0; transform: translateY(4px); filter: blur(2px); }
  to { opacity: 1; transform: none; filter: none; }
}
</style>

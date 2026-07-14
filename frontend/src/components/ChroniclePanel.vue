<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { EMOTION_DIMENSIONS } from '../composables/useInvokeSession.js'
import SbIcon from './SbIcon.vue'
import StoryQualityRadar from './StoryQualityRadar.vue'

const { t, locale } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
  baseImage: { type: Object, default: null },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, required: true },
})
const emit = defineEmits(['update:show', 'toast', 'open-storybook'])

const AXES = ['past', 'present', 'future']
const TIME_SCALES = ['minutes', 'tens_of_minutes', 'hours', 'days', 'months', 'years', 'decades']

const STEPS = ['vision', 'candidates', 'select', 'expand', 'prompt', 'generate']
const PHASE_STEP = {
  loadingImage: 0, extractingVision: 0,
  candidates: 1,
  selecting: 2,
  expanding: 3, repairingStory: 3, translating: 3,
  mutatingTags: 3, buildingBiography: 3, buildingTimetable: 3,
  concretizing: 3, differentiating: 3, writingStory: 3,
  taggingAxis: 4, examining: 4, refiningPrompt: 4,
  refiningPromptTags: 4, refiningPromptProse: 4,
  draftingAxis: 4, scanningDraft: 4,
  savingStory: 5, done: 5,
}

// ── form state ────────────────────────────────────────────────────────────────
const baseSha = ref('')
const baseModel = ref('')
const baseAxis = ref('present')
function _modelOf(doc) {
  return doc?.model_name || doc?.model_info?.model_name || ''
}
const userTopic = ref('')
const worldview = ref('')
const promptStyle = ref('danbooru+natural')
const workflows = ref([])
const workflow = ref('')
const divergence = ref(0.3)
const temperature = ref(1.0)
const numCtx = ref(16384)
const emotion = ref('')
const DRAMATIC_MODES = [
  'escalation', 'reversal', 'revelation', 'irony', 'approaching_threat',
  'pursuit', 'parting', 'temptation', 'secret_surfacing', 'role_reversal',
]
const dramaticMode = ref('')
const TONES = ['bright', 'neutral', 'dark']
const tone = ref('bright')
const timeScaleIdx = ref(5)
const useRefSeed = ref(true)
const manualMode = ref(false)
const generatePinup = ref(false)
const suppressConflictTags = ref(true)
const useDraftRefine = ref('auto') // auto | on | off
const draftWidth = ref(512)
const draftHeight = ref(512)
const draftSteps = ref(12)
const pickingRandom = ref(false)
const biography = ref(null)
const timetable = ref(null)
const concrete = ref(null)
const axisReasoning = ref({})
const axisDrafts = ref({})
const qualityEval = ref(null)
const pinupJobId = ref('')

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
const axisStories = ref({ past: '', present: '', future: '' })
const axisStoriesJa = ref({ past: '', present: '', future: '' })
function displayAxisStory(axis) {
  const en = axisStories.value[axis] || ''
  const ja = axisStoriesJa.value[axis] || ''
  if (panelLang.value === 'ja') return ja || en
  return en || ja
}
const hasAxisStories = computed(() =>
  AXES.some(a => !!(axisStories.value[a] || axisStoriesJa.value[a]))
)
/** Show the expand stream in the open pane until structured acts arrive. */
const showLiveStream = computed(() =>
  running.value && !!streamText.value && !hasAxisStories.value && currentStep.value === 3
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
  running.value || !!imageGen.value.active
)
const showWeaverStage = computed(() => {
  if (!isBusyWeaving.value) return false
  // Full-stage weaver when the right pane is still mostly empty.
  if (visibleCandidates.value.length) return false
  if (displayTitle.value) return false
  if (Object.keys(prompts.value).length) return false
  return true
})
const weaverCaption = computed(() => {
  if (imageGen.value.active) return t('chronicle.weaverImages')
  if (phase.value) {
    const label = t('chronicle.phase.' + phase.value, phase.value)
    return t('chronicle.weaverPhase', { phase: label })
  }
  return t('chronicle.weaverBusy')
})

/** Keep the panel open during pipeline / image jobs — ignore Esc & backdrop. */
const stayOpen = computed(() => {
  if (running.value) return true
  if (imageGen.value.active) return true
  const states = imageGen.value.states || {}
  return Object.values(states).some(s => s === 'queued' || s === 'running')
})

watch(streamText, (text) => {
  if (!title.value) {
    const m = text.match(/\[TITLE\][^\[]*?\n(.*)/i)
    if (m) title.value = m[1].trim().replace(/^["「]|["」]$/g, '')
  }
  if (!overall.value) {
    const m = text.match(/\[OVERALL\][^\[]*?\n([\s\S]*?)(?=\[PAST\]|$)/i)
    if (m) overall.value = m[1].trim()
  }
  // Provisional axis prose from the live expand stream (request locale).
  const patch = {}
  for (const axis of AXES) {
    const cur = uiLocale.value === 'ja' ? axisStoriesJa.value[axis] : axisStories.value[axis]
    if (cur) continue
    const tag = axis.toUpperCase()
    const m = text.match(new RegExp(
      `\\[${tag}\\][^\\[]*?\\n([\\s\\S]*?)(?=\\[(?:PAST|PRESENT|FUTURE|OVERALL|TITLE)\\]|$)`,
      'i',
    ))
    const body = m?.[1]?.trim()
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
  // New weave request (Storybook「紡ぐ」含む): clear prior finished/partial run
  // so the panel can start again instead of staying locked on the last result.
  if (finished.value || storyId.value || (baseSha.value && baseSha.value !== doc.sha256)) {
    resetRun()
  }
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
})

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
  _reader?.cancel().catch(() => {})
})

function close() { emit('update:show', false) }

function resetStory() {
  streamText.value = ''
  prompts.value = {}
  imageJobs.value = []
  finished.value = false
  seed.value = null
  title.value = ''
  titleJa.value = ''
  overall.value = ''
  overallJa.value = ''
  axisStories.value = { past: '', present: '', future: '' }
  axisStoriesJa.value = { past: '', present: '', future: '' }
  mutationTags.value = []
  storySeedTags.value = []
  storySeedMotif.value = ''
  biography.value = null
  timetable.value = null
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
  storyId.value = ''
  candidates.value = []
  selecting.value = false
  selectedCandidate.value = ''
  respinCandCount.value = 0
  respinExpandCount.value = 0
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

const REASON_AXES = ['past', 'present', 'future']
const bioView = computed(() => {
  const b = biography.value
  if (!b) return null
  return (panelLang.value === 'ja' && b.ja && Object.keys(b.ja).length) ? b.ja : b.en
})
const timetableView = computed(() => {
  const tt = timetable.value
  if (!tt) return []
  return (panelLang.value === 'ja' && tt.ja && tt.ja.length) ? tt.ja : (tt.en || [])
})
const BIO_LIST_FIELDS = ['hobbies', 'favourite_items', 'likes', 'dislikes', 'quirks']
function joinList(arr) {
  return (arr || []).join(t('storybook.listSep'))
}
function activityFor(axis) {
  const c = concrete.value
  if (!c) return ''
  return (panelLang.value === 'ja' && c.ja && c.ja[axis]) ? c.ja[axis] : (c.en?.[axis] || '')
}
const hasReasoning = computed(() =>
  !!bioView.value || timetableView.value.length > 0 || !!concrete.value
  || Object.keys(axisReasoning.value).length > 0
  || Object.keys(axisDrafts.value).length > 0,
)

const CAT_TAG_GROUPS = [
  { key: 'subject_tags', label: 'tagGroupSubject' },
  { key: 'hair_tags', label: 'tagGroupHair' },
  { key: 'expression_tags', label: 'tagGroupExpression' },
  { key: 'clothing_tags', label: 'tagGroupClothing' },
  { key: 'accessory_tags', label: 'tagGroupAccessory' },
  { key: 'pose_tags', label: 'tagGroupPose' },
  { key: 'background_tags', label: 'tagGroupBackground' },
  { key: 'object_tags', label: 'tagGroupObject' },
  { key: 'lighting_tags', label: 'tagGroupLighting' },
]

function axisHasCatTags(p) {
  return CAT_TAG_GROUPS.some(g => (p?.[g.key] || []).length > 0)
}

const qualityDraftNote = computed(() => {
  const q = qualityEval.value
  if (!q) return ''
  const dg = q.draft_grounding
  if (dg && typeof dg === 'object') {
    const n = (dg.axes || []).length
    const d = Number(dg.mean_delta ?? 0)
    return t('storybook.quality.draftGrounding', {
      axes: n,
      delta: (d >= 0 ? '+' : '') + d.toFixed(2),
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

async function _submitAndStream(url, payload, onJob) {
  running.value = true
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
  } catch (err) {
    errorMsg.value = String(err.message || err)
    emit('toast', { msg: errorMsg.value, type: 'error' })
  } finally {
    running.value = false
  }
}

function clearBase() {
  baseSha.value = ''
  baseModel.value = ''
  thumbFailed.value = false
}

async function start() {
  if (!baseSha.value && !userTopic.value.trim()) {
    emit('toast', { msg: t('chronicle.needTopicOrBase'), type: 'error' })
    return
  }
  resetRun()
  await _submitAndStream('/api/story/chronicle', currentSettingsPayload(), (d) => {
    groupId.value = d.group_id
  })
}

function currentSettingsPayload() {
  return {
    base_sha256: baseSha.value || '',
    base_time_axis: baseAxis.value,
    user_topic: userTopic.value,
    worldview: worldview.value,
    time_scale: TIME_SCALES[timeScaleIdx.value],
    prompt_style: promptStyle.value,
    workflow_name: workflow.value,
    divergence: divergence.value,
    emotion: emotion.value,
    dramatic_mode: dramaticMode.value,
    tone: tone.value,
    generate_pinup: baseSha.value ? generatePinup.value : false,
    suppress_conflict_tags: suppressConflictTags.value,
    use_draft_refine: useDraftRefine.value,
    draft_width: draftWidth.value,
    draft_height: draftHeight.value,
    draft_steps: draftSteps.value,
    use_ref_seed: baseSha.value ? useRefSeed.value : false,
    manual_mode: manualMode.value,
    temperature: temperature.value,
    num_ctx: numCtx.value,
    locale: uiLocale.value,
  }
}

const draftRefineDisabledHint = computed(() => {
  if (manualMode.value) return t('chronicle.draftRefineDisabledManual')
  if (!workflow.value) return t('chronicle.draftRefineDisabledWorkflow')
  return ''
})

async function selectCandidate(cid) {
  if (!storyId.value || running.value) return
  selectedCandidate.value = cid
  selecting.value = false
  resetStory()
  await _submitAndStream(`/api/story/chronicle/${storyId.value}/select`,
    { candidate_id: cid, time_scale: TIME_SCALES[timeScaleIdx.value] })
}

async function respin(stage) {
  if (!storyId.value || running.value) return
  const count = stage === 'candidates'
    ? (respinCandCount.value += 1)
    : (respinExpandCount.value += 1)
  if (stage === 'candidates') { candidates.value = []; selecting.value = false }
  resetStory()
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
    temperature: settings.temperature,
    num_ctx: settings.num_ctx,
    worldview: settings.worldview,
    user_topic: settings.user_topic,
  })
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
      phase.value = ev.code
      if (ev.progress !== undefined) progress.value = ev.progress
      break
    case 'token':
      _pendingTokens += ev.text
      _scheduleFlush()
      break
    case 'candidates':
      storyId.value = ev.story_id
      candidates.value = ev.candidates || []
      selecting.value = true
      phase.value = 'selecting'
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
          pose_tags: ev.pose_tags || [],
          background_tags: ev.background_tags || [],
          object_tags: ev.object_tags || [],
          lighting_tags: ev.lighting_tags || [],
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
      mutationTags.value = ev.tags || []
      break
    case 'story_seed_tags':
      storySeedTags.value = ev.tags || []
      storySeedMotif.value = ev.motif || ''
      break
    case 'biography':
      biography.value = { en: ev.biography, ja: ev.biography_ja }
      break
    case 'timetable':
      timetable.value = { en: ev.timetable || [], ja: ev.timetable_ja || [] }
      break
    case 'concrete_activities':
      concrete.value = { en: ev.activities || {}, ja: ev.activities_ja || {} }
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
      seed.value = ev.seed
      finished.value = true
      phase.value = 'done'
      progress.value = 1.0
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

        <!-- step indicator -->
        <div class="flex items-center gap-1 px-5 py-2 sb-hairline text-[10px] bg-black/15">
          <template v-for="(s, i) in STEPS" :key="s">
            <div class="flex items-center gap-1.5"
              :class="i === currentStep ? 'text-teal-300' : i < currentStep ? 'text-emerald-400/80' : 'text-[var(--sb-faint)]'">
              <span class="w-4 h-4 rounded-full flex items-center justify-center border text-[9px]"
                :class="i === currentStep ? 'border-teal-500 bg-teal-950/50 chronicle-dot-active'
                  : i < currentStep ? 'border-emerald-700/60 bg-emerald-950/40' : 'border-white/10 bg-black/30'">
                <SbIcon v-if="i < currentStep" name="check" class="w-2.5 h-2.5" />
                <span v-else>{{ i + 1 }}</span>
              </span>
              <span class="uppercase tracking-wide hidden sm:inline">{{ t('chronicle.steps.' + s) }}</span>
            </div>
            <span v-if="i < STEPS.length - 1" class="text-[var(--sb-faint)] mx-0.5">→</span>
          </template>
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
                    <textarea v-model="userTopic" rows="2" :placeholder="t('chronicle.userTopicPh')"
                      class="sb-textarea flex-1"></textarea>
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
                    <p v-if="mutationTags.length" class="text-[10px] text-teal-500/80 pl-[calc(5rem+0.5rem)] break-all">
                      <span class="text-teal-300/80">{{ t('chronicle.mutationTags') }}:</span>
                      {{ mutationTags.join(', ') }}
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

              <!-- output (closed) -->
              <details class="rounded-xl border border-white/5 bg-black/20">
                <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                  <span class="flex items-center gap-1.5">
                    <SbIcon name="image" class="w-3.5 h-3.5 text-teal-400/80" />
                    {{ t('chronicle.outputGroup') }}
                  </span>
                </summary>
                <div class="px-3 pb-3 pt-1 flex flex-col gap-3 text-xs">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="sb-label w-20 shrink-0">{{ t('chronicle.promptStyle') }}</span>
                    <button v-for="m in ['danbooru+natural', 'natural', 'danbooru']" :key="m" type="button"
                      @click="promptStyle = m"
                      class="sb-chip" :class="promptStyle === m ? 'is-chip-on-teal' : ''">
                      {{ t('chronicle.style.' + m.replace('+', '_')) }}
                    </button>
                  </div>
                  <div class="flex items-center flex-wrap gap-4">
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]">
                      <input v-model="useRefSeed" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.seedInherit') }}
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]" :title="t('chronicle.suppressConflictTitle')">
                      <input v-model="suppressConflictTags" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.suppressConflict') }}
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]" :title="t('chronicle.pinupTitle')">
                      <input v-model="generatePinup" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.generatePinup') }}
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[var(--sb-muted)]">
                      <input v-model="manualMode" type="checkbox" class="accent-teal-500" />
                      {{ t('chronicle.manualMode') }}
                    </label>
                  </div>
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.draftRefineTitle')">{{ t('chronicle.draftRefine') }}</span>
                    <button v-for="m in ['auto', 'on', 'off']" :key="m" type="button"
                      @click="useDraftRefine = m"
                      class="sb-chip" :class="useDraftRefine === m ? 'is-chip-on-teal' : ''">
                      {{ t('chronicle.draftRefineMode.' + m) }}
                    </button>
                  </div>
                  <p v-if="draftRefineDisabledHint && useDraftRefine !== 'off'"
                    class="text-[10px] text-amber-500/70 pl-[5.5rem]">
                    {{ draftRefineDisabledHint }}
                  </p>
                  <div v-if="useDraftRefine !== 'off'" class="flex items-center gap-2 flex-wrap text-[10px] text-[var(--sb-muted)]">
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
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.temperatureTitle')">{{ t('chronicle.temperature') }}</span>
                    <input v-model.number="temperature" type="range" min="0" max="1.5" step="0.1" class="flex-1 accent-teal-500" />
                    <span class="text-teal-400 font-mono w-10 text-right">{{ temperature.toFixed(1) }}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="sb-label w-20 shrink-0" :title="t('chronicle.numCtxTitle')">{{ t('chronicle.numCtx') }}</span>
                    <select v-model.number="numCtx" class="sb-select flex-1">
                      <option :value="4096">4096</option>
                      <option :value="8192">8192</option>
                      <option :value="16384">{{ t('chronicle.numCtxRecommended') }}</option>
                      <option :value="32768">32768</option>
                    </select>
                  </div>
                </div>
              </details>

              <p v-if="storySeedTags.length" class="text-[10px] text-amber-500/80 break-all">
                <span class="text-amber-300/80">{{ t('chronicle.seedTags') }}:</span>
                {{ storySeedTags.join(', ') }}
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
                <button v-if="running && groupId" type="button" @click="cancelGroup"
                  class="sb-btn text-red-300 border-red-800/40">
                  {{ t('chronicle.cancel') }}
                </button>
                <span v-if="phase" class="text-[10px] text-[var(--sb-muted)] uppercase tracking-wide">
                  {{ t('chronicle.phase.' + phase, phase) }}
                </span>
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

            <!-- ONE progress system: pipeline XOR imageGen -->
            <div v-if="showPipelineProgress" class="flex flex-col gap-2">
              <div class="flex items-center gap-2.5">
                <div class="chronicle-shuttle-mini" aria-hidden="true">
                  <span class="chronicle-shuttle-mini__yarn"></span>
                  <span class="chronicle-shuttle-mini__body"></span>
                </div>
                <div class="flex-1 flex flex-col gap-1 min-w-0">
                  <div class="flex items-center justify-between text-[10px] text-teal-300/90">
                    <span class="truncate">{{ phase ? t('chronicle.phase.' + phase, phase) : t('chronicle.running') }}</span>
                    <span class="font-mono shrink-0 ml-2">{{ Math.round(progress * 100) }}%</span>
                  </div>
                  <div class="sb-progress chronicle-progress-alive">
                    <div class="sb-progress-bar chronicle-progress-bar-alive"
                      :style="{ width: (progress * 100) + '%' }"></div>
                  </div>
                </div>
              </div>
            </div>

            <div v-else-if="showImageProgress" class="flex flex-col gap-2">
              <div class="flex flex-wrap gap-2">
                <div v-for="j in imageJobs" :key="j.job_id"
                  class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[10px]"
                  :class="jobStatusClass(j.job_id)">
                  <span class="font-bold uppercase tracking-wide">{{ t('chronicle.axis.' + j.axis) }}</span>
                  <SbIcon :name="jobStatusIcon(j.job_id)" class="w-3 h-3" />
                  <span class="opacity-70">{{ jobStatusLabel(j.job_id) }}</span>
                </div>
              </div>
              <div class="flex items-center gap-2.5">
                <div class="chronicle-shuttle-mini" aria-hidden="true"
                  :class="imageGen.active ? '' : 'is-idle'">
                  <span class="chronicle-shuttle-mini__yarn"></span>
                  <span class="chronicle-shuttle-mini__body"></span>
                </div>
                <div class="flex-1 flex flex-col gap-1 min-w-0">
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
            </div>

            <button v-if="finished" @click="emit('open-storybook')"
              class="self-start sb-btn border-teal-700/40 text-teal-200">
              <SbIcon name="book" class="w-3.5 h-3.5" />
              {{ t('chronicle.openStorybook') }}
            </button>

            <p v-if="errorMsg" class="text-xs text-red-400">{{ errorMsg }}</p>
          </div>

          <!-- ── RIGHT: candidates / story / prompts ──────────────────────── -->
          <div class="flex flex-col gap-4 min-w-0">

            <div v-if="currentStep === 0 && !running && !streamText"
              class="flex-1 flex items-center justify-center min-h-[200px] text-center px-6">
              <p class="sb-display text-base text-[var(--sb-muted)] leading-relaxed whitespace-pre-line">
                {{ t('chronicle.idleHint') }}
              </p>
            </div>

            <!-- Full-stage loom while waiting for the first real content -->
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
            </div>

            <!-- Compact ribbon when content already fills the pane -->
            <div v-else-if="isBusyWeaving"
              class="chronicle-weaver-ribbon flex items-center gap-3 px-3 py-2.5 rounded-xl border border-teal-800/30 bg-teal-950/25"
              role="status" :aria-label="weaverCaption">
              <div class="chronicle-loom chronicle-loom--sm" aria-hidden="true">
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
              <div class="min-w-0 flex-1">
                <p class="text-[11px] text-teal-200/95 truncate">{{ weaverCaption }}</p>
                <p class="text-[10px] text-[var(--sb-muted)] truncate">{{ t('chronicle.weaverPatience') }}</p>
              </div>
              <span class="chronicle-weaver-dots text-teal-400/80 font-mono text-xs shrink-0" aria-hidden="true">
                <span>.</span><span>.</span><span>.</span>
              </span>
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
                    <span class="text-xs font-bold text-teal-100 leading-tight">{{ c.title }}</span>
                  </div>
                  <div class="flex flex-col gap-1 text-[10px] leading-snug">
                    <div v-for="ax in AXES" :key="ax" v-show="c[ax] || c.summary">
                      <span class="font-bold uppercase tracking-wide mr-1"
                        :class="ax === baseAxis ? 'text-[var(--sb-amber)]' : 'text-teal-400'">{{ t('chronicle.axis.' + ax) }}</span>
                      <span class="text-gray-300">{{ c[ax] || (ax === 'present' ? c.summary : '') }}</span>
                    </div>
                  </div>
                  <div v-if="c.motif || c.key_motif" class="mt-auto pt-1">
                    <span class="text-[9px] px-1.5 py-0.5 rounded bg-black/50 text-teal-300/90">
                      {{ t('chronicle.motifLabel') }}: {{ c.motif || c.key_motif }}
                    </span>
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

            <!-- live expand stream (open) until structured acts arrive -->
            <div v-if="showLiveStream"
              class="rounded-xl border border-teal-800/40 bg-black/40 p-3 text-xs text-gray-300 whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto font-light">
              {{ streamText }}<span class="animate-pulse text-teal-400">▍</span>
            </div>

            <!-- raw stream (closed) once acts are visible, or for non-expand phases -->
            <details v-else-if="streamText || (running && !selecting)"
              class="rounded-xl border border-white/5 bg-black/30">
              <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                <span class="flex items-center gap-1.5">
                  <SbIcon name="doc" class="w-3.5 h-3.5" />
                  {{ t('chronicle.rawStream') }}
                </span>
              </summary>
              <pre class="px-3 pb-3 text-xs text-gray-400 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto font-light">{{ streamText }}<span v-if="running" class="animate-pulse text-teal-400">▍</span></pre>
            </details>

            <!-- timetable: always open when present (was hidden inside collapsed Reasoning) -->
            <details v-if="timetableView.length" class="rounded-xl border border-white/5 bg-black/25" open>
              <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                <span class="flex items-center gap-1.5">
                  <SbIcon name="clock" class="w-3.5 h-3.5 text-teal-400/80" />
                  {{ t('storybook.timetable') }}
                </span>
              </summary>
              <div class="px-3 pb-3">
                <ul class="space-y-0.5 text-[11px]">
                  <li v-for="(s, si) in timetableView" :key="si" class="text-gray-300 flex gap-2">
                    <span class="text-teal-400/80 shrink-0 w-20">{{ s.label }}</span>
                    <span class="min-w-0">
                      {{ s.activity }}
                      <span v-if="s.place" class="text-[var(--sb-muted)]"> {{ t('storybook.timetablePlace', { place: s.place }) }}</span>
                      <span v-if="s.feeling" class="text-gray-500 italic"> {{ t('storybook.timetableFeeling', { feeling: s.feeling }) }}</span>
                    </span>
                  </li>
                </ul>
              </div>
            </details>

            <!-- reasoning (closed) -->
            <details v-if="hasReasoning" class="rounded-xl border border-white/5 bg-black/25">
              <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                <span class="flex items-center gap-1.5">
                  <SbIcon name="spark" class="w-3.5 h-3.5" />
                  {{ t('chronicle.reasoning') }}
                </span>
              </summary>
              <div class="px-3 pb-3 flex flex-col gap-2">
                <div v-if="bioView" class="bg-black/40 border border-white/5 rounded-xl p-3 text-[11px] text-gray-300 space-y-1">
                  <div class="sb-label text-teal-300/80 flex items-center gap-1">
                    <SbIcon name="book" class="w-3 h-3" />{{ t('storybook.biography') }}
                  </div>
                  <p v-if="bioView.personality">{{ bioView.personality }}</p>
                  <p v-if="bioView.occupation" class="text-gray-400 break-words">
                    <span class="text-[var(--sb-muted)]">{{ t('storybook.bioOccupation') }}:</span> {{ bioView.occupation }}
                  </p>
                  <p v-for="f in BIO_LIST_FIELDS" :key="f" v-show="(bioView[f] || []).length" class="text-gray-400 break-words">
                    <span class="text-[var(--sb-muted)]">{{ t('storybook.bio_' + f) }}:</span> {{ joinList(bioView[f]) }}
                  </p>
                  <p v-if="bioView.backstory" class="text-gray-400 italic pt-1">{{ bioView.backstory }}</p>
                </div>

                <div v-for="axis in REASON_AXES" :key="axis"
                  v-show="activityFor(axis) || axisReasoning[axis] || axisDrafts[axis]"
                  class="bg-black/40 border border-white/5 rounded-xl p-3 text-[11px]">
                  <div class="sb-label text-[var(--sb-amber)] mb-1">{{ t('chronicle.axis.' + axis) }}</div>
                  <p v-if="activityFor(axis)" class="text-gray-300 mb-1">{{ activityFor(axis) }}</p>
                  <div v-if="axisReasoning[axis]" class="text-[10px] text-[var(--sb-muted)] space-y-0.5">
                    <p v-if="axisReasoning[axis].shot || axisReasoning[axis].camera">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonShot') }}:</span>
                      {{ [axisReasoning[axis].shot, axisReasoning[axis].camera].filter(Boolean).join(' / ') }}
                    </p>
                    <p v-if="(axisReasoning[axis].focal || []).length">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonPose') }}:</span>
                      {{ (axisReasoning[axis].focal || []).join(', ') }}
                    </p>
                    <p v-if="(axisReasoning[axis].search_tags || []).length" class="break-words">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonTags') }}:</span>
                      {{ (axisReasoning[axis].search_tags || []).join(', ') }}
                    </p>
                  </div>
                  <div v-if="axisDrafts[axis]" class="mt-2 text-[10px] text-[var(--sb-muted)]">
                    <p v-if="(axisDrafts[axis].draft_tags || []).length" class="break-words">
                      <span class="text-[var(--sb-faint)]">{{ t('chronicle.reasonDraftTags') }}:</span>
                      {{ (axisDrafts[axis].draft_tags || []).join(', ') }}
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

            <!-- quality radar (from SSE quality_eval) -->
            <details v-if="qualityEval?.dimensions"
              class="rounded-xl border border-white/5 bg-black/25" open>
              <summary class="sb-btn cursor-pointer list-none w-full justify-between px-3 py-2 rounded-xl border-0">
                <span class="flex items-center gap-1.5">
                  <SbIcon name="spark" class="w-3.5 h-3.5 text-teal-400/80" />
                  {{ t('storybook.quality.title') }}
                </span>
                <span class="text-[11px] font-mono text-teal-300/80">
                  {{ Math.round((qualityEval.overall || 0) * 100) }}
                </span>
              </summary>
              <div class="px-3 pb-3">
                <StoryQualityRadar :eval="qualityEval" />
                <p v-if="qualityDraftNote"
                  class="mt-2 text-[10px] font-mono text-teal-300/70">
                  {{ qualityDraftNote }}
                </p>
                <p class="mt-2 text-[10px] text-[var(--sb-muted)] leading-relaxed">
                  {{ t('storybook.quality.hint') }}
                </p>
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
                  class="mt-1 rounded-lg border border-emerald-800/30 bg-emerald-950/25 p-2 space-y-1.5">
                  <p class="text-[9px] font-semibold text-emerald-400/90 uppercase tracking-wide">
                    {{ t('chronicle.visualSpecTitle') }}
                  </p>
                  <div v-for="g in CAT_TAG_GROUPS" :key="g.key"
                    v-show="(p[g.key] || []).length"
                    class="space-y-0.5">
                    <p class="text-[9px] text-emerald-400/70 font-semibold">
                      {{ t('chronicle.' + g.label) }}
                    </p>
                    <div class="flex flex-wrap gap-1">
                      <span v-for="tag in p[g.key]" :key="tag"
                        class="text-[10px] font-mono px-1.5 py-0.5 rounded
                          bg-emerald-900/40 border border-emerald-700/30 text-emerald-300/90">
                        {{ tag }}
                      </span>
                    </div>
                  </div>
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
</style>

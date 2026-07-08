<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { EMOTION_DIMENSIONS } from '../composables/useInvokeSession.js'

const { t, locale } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
  baseImage: { type: Object, default: null },   // gallery-selected image doc
  comfyOffline: { type: Boolean, default: false },
  jobsMap: { type: Object, default: () => new Map() },  // App-level job state map
})
const emit = defineEmits(['update:show', 'toast', 'open-storybook'])

const AXES = ['past', 'present', 'future']
const TIME_SCALES = ['minutes', 'tens_of_minutes', 'hours', 'days', 'months', 'years', 'decades']

// Step indicator: vision → candidates → select → expand → prompt → generate
const STEPS = ['vision', 'candidates', 'select', 'expand', 'prompt', 'generate']
const PHASE_STEP = {
  loadingImage: 0, extractingVision: 0,
  candidates: 1,
  selecting: 2,
  expanding: 3, repairingStory: 3, translating: 3,
  taggingAxis: 4, examining: 4, refiningPrompt: 4,
  refiningPromptTags: 4, refiningPromptProse: 4,
  savingStory: 5, done: 5,
}

// ── form state ────────────────────────────────────────────────────────────────
const baseSha = ref('')
const baseAxis = ref('present')
const userTopic = ref('')
const worldview = ref('')
const promptStyle = ref('danbooru+natural')
const workflows = ref([])
const workflow = ref('')
const divergence = ref(0)
const emotion = ref('')       // target emotion register ('' = off)
// Story-shape dimension, mirrors backend generator._DRAMATIC_MODES. '' = auto
// (the backend auto-varies a distinct mode per candidate).
const DRAMATIC_MODES = [
  'escalation', 'reversal', 'revelation', 'irony', 'approaching_threat',
  'pursuit', 'parting', 'temptation', 'secret_surfacing', 'role_reversal',
]
const dramaticMode = ref('')  // preferred story shape ('' = auto/おまかせ)
const timeScaleIdx = ref(5)   // index into TIME_SCALES, default "years"
const useRefSeed = ref(true)
const manualMode = ref(false)
const pickingRandom = ref(false)

const uiLocale = computed(() => (locale.value?.startsWith('ja') ? 'ja' : 'en'))

// Thumbnail with fallback to the original (freshly uploaded images may not
// have a thumbnail yet)
const thumbFailed = ref(false)
watch(baseSha, () => { thumbFailed.value = false })
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
const prompts = ref({})        // axis -> {positive, negative} (editable)
const imageJobs = ref([])
const finished = ref(false)    // pipeline done (done event received)
const errorMsg = ref('')
const title = ref('')
const titleJa = ref('')
const overall = ref('')
const overallJa = ref('')
const mutationTags = ref([])   // tags injected by the divergence dial (visible)

// ── candidate / selection state ─────────────────────────────────────────────
const candidates = ref([])       // [{id,title,past,present,future,summary,motif}]
const selecting = ref(false)     // candidates shown, awaiting a pick
const selectedCandidate = ref('')
const respinCandCount = ref(0)
const respinExpandCount = ref(0)

// English is the canonical text; JA is a stored translation. The toggle
// switches display only (default follows the UI locale, and keeps following it
// when the app language is switched while the panel is open).
const panelLang = ref(uiLocale.value)
watch(uiLocale, (l) => { panelLang.value = l })
const displayTitle = computed(() =>
  (panelLang.value === 'ja' && titleJa.value) ? titleJa.value : title.value
)
const displayOverall = computed(() =>
  (panelLang.value === 'ja' && overallJa.value) ? overallJa.value : overall.value
)

// Current step for the step indicator
const currentStep = computed(() => {
  if (finished.value) return 5
  if (selecting.value) return 2
  return PHASE_STEP[phase.value] ?? 0
})

// Pipeline done but no image jobs submitted (manual mode, or no workflow was
// selected): prompts stay editable and images can still be generated from here.
const canGenerate = computed(() =>
  finished.value && !!storyId.value && !imageJobs.value.length
)

// Extract title/overall from the streaming text in real time so they appear
// progressively without waiting for the full story event.
watch(streamText, (text) => {
  if (title.value && overall.value) return
  if (!title.value) {
    const m = text.match(/\[TITLE\][^\[]*?\n(.*)/i)
    if (m) title.value = m[1].trim().replace(/^["「]|["」]$/g, '')
  }
  if (!overall.value) {
    const m = text.match(/\[OVERALL\][^\[]*?\n([\s\S]*?)(?=\[PAST\]|$)/i)
    if (m) overall.value = m[1].trim()
  }
})

let _reader = null
let _pendingTokens = ''
let _flushTimer = null

function _flushTokens() {
  if (_pendingTokens) {
    streamText.value += _pendingTokens
    _pendingTokens = ''
  }
}

watch(() => props.show, async (val) => {
  if (!val) return
  if (props.baseImage?.sha256) baseSha.value = props.baseImage.sha256
  panelLang.value = uiLocale.value
  if (!workflows.value.length) {
    try {
      const r = await fetch('/api/comfy/workflows')
      if (r.ok) workflows.value = await r.json()
    } catch {}
  }
})

onUnmounted(() => {
  if (_flushTimer) clearInterval(_flushTimer)
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
  mutationTags.value = []
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

// ── image job status helpers ──────────────────────────────────────────────────
function jobState(job_id) {
  return props.jobsMap?.get(job_id)?.state ?? 'queued'
}
function jobStatusIcon(job_id) {
  return { queued: '⏳', running: '⚙️', succeeded: '✓', failed: '✗', cancelling: '⏹' }[jobState(job_id)] ?? '⏳'
}
function jobStatusLabel(job_id) {
  return t('chronicle.jobState.' + jobState(job_id), jobState(job_id))
}
function jobStatusClass(job_id) {
  return {
    queued:     'border-gray-700 bg-gray-900/50 text-gray-400',
    running:    'border-teal-700/60 bg-teal-900/20 text-teal-300',
    succeeded:  'border-green-700/60 bg-green-900/20 text-green-300',
    failed:     'border-red-700/60 bg-red-900/20 text-red-400',
    cancelling: 'border-gray-700 bg-gray-900/50 text-gray-500',
  }[jobState(job_id)] ?? 'border-gray-700 bg-gray-900/50 text-gray-400'
}

// ── random base image from library ────────────────────────────────────────────
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
  } catch (err) {
    emit('toast', { msg: t('chronicle.randomFailed') + ': ' + (err.message || err), type: 'error' })
  } finally {
    pickingRandom.value = false
  }
}

// ── pipeline ──────────────────────────────────────────────────────────────────
function _extractError(errBody, resp) {
  const detail = errBody?.detail
  return typeof detail === 'string' ? detail
    : Array.isArray(detail) ? detail.map(e => e.msg ?? JSON.stringify(e)).join('; ')
    : resp.statusText
}

// Run a streaming job to completion, managing the flush timer.
async function _runStream(jobId) {
  if (!_flushTimer) _flushTimer = setInterval(_flushTokens, 66)
  try {
    await readStream(jobId)
  } finally {
    if (_flushTimer) { clearInterval(_flushTimer); _flushTimer = null }
    _flushTokens()
  }
}

// POST a request, then stream its job to completion, managing running/errors.
// onJob(data) runs a caller-specific step (e.g. capture group_id) before streaming.
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

// Phase 1 — pitch three story candidates
async function start() {
  if (!baseSha.value) {
    emit('toast', { msg: t('chronicle.noBase'), type: 'error' })
    return
  }
  resetRun()
  await _submitAndStream('/api/story/chronicle', {
    base_sha256: baseSha.value,
    base_time_axis: baseAxis.value,
    user_topic: userTopic.value,
    worldview: worldview.value,
    time_scale: TIME_SCALES[timeScaleIdx.value],
    prompt_style: promptStyle.value,
    workflow_name: workflow.value,
    divergence: divergence.value,
    emotion: emotion.value,
    dramatic_mode: dramaticMode.value,
    use_ref_seed: useRefSeed.value,
    manual_mode: manualMode.value,
    locale: uiLocale.value,
  }, (d) => { groupId.value = d.group_id })
}

// Phase 2 — expand the chosen candidate
async function selectCandidate(cid) {
  if (!storyId.value || running.value) return
  selectedCandidate.value = cid
  selecting.value = false
  resetStory()
  await _submitAndStream(`/api/story/chronicle/${storyId.value}/select`,
    { candidate_id: cid, time_scale: TIME_SCALES[timeScaleIdx.value] })
}

// Respin — regenerate candidates or the expanded story at a higher temperature
async function respin(stage) {
  if (!storyId.value || running.value) return
  const count = stage === 'candidates'
    ? (respinCandCount.value += 1)
    : (respinExpandCount.value += 1)
  if (stage === 'candidates') { candidates.value = []; selecting.value = false }
  resetStory()
  await _submitAndStream(`/api/story/chronicle/${storyId.value}/respin`,
    { stage, respin_count: count })
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
      break
    case 'candidates':
      storyId.value = ev.story_id
      candidates.value = ev.candidates || []
      selecting.value = true
      phase.value = 'selecting'
      break
    case 'axis_prompt':
      prompts.value = { ...prompts.value, [ev.axis]: { positive: ev.positive, negative: ev.negative } }
      break
    case 'story':
      title.value = ev.title || ''
      overall.value = ev.overall || ''
      break
    case 'story_saved':
      storyId.value = ev.story_id
      break
    case 'translation':
      titleJa.value = ev.title_ja || ''
      overallJa.value = ev.overall_ja || ''
      break
    case 'mutation_tags':
      mutationTags.value = ev.tags || []
      break
    case 'warning':
      emit('toast', { msg: ev.message, type: 'warning' })
      break
    case 'image_jobs':
      imageJobs.value = ev.jobs
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
    emit('toast', { msg: t('chronicle.imagesQueued'), type: 'success' })
  } catch (err) {
    emit('toast', { msg: String(err.message || err), type: 'error' })
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[70] bg-black/80 flex items-center justify-center p-4"
      @click.self="close" @keydown.esc="close">
      <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-6xl max-h-[92vh] flex flex-col">

        <!-- header -->
        <div class="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h2 class="text-base font-bold text-teal-300 flex items-center gap-2">
            <span class="chronicle-lamp inline-block w-2.5 h-2.5 rounded-full bg-teal-400"
              :class="running ? 'is-running' : 'opacity-30'"></span>
            📜 {{ t('chronicle.title') }}
          </h2>
          <button @click="close"
            class="text-gray-600 hover:text-gray-200 text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
        </div>

        <!-- step indicator -->
        <div class="flex items-center gap-1 px-5 py-2 border-b border-gray-800/70 text-[10px]">
          <template v-for="(s, i) in STEPS" :key="s">
            <div class="flex items-center gap-1.5"
              :class="i === currentStep ? 'text-teal-300' : i < currentStep ? 'text-green-400/80' : 'text-gray-600'">
              <span class="w-4 h-4 rounded-full flex items-center justify-center border text-[9px]"
                :class="i === currentStep ? 'border-teal-500 bg-teal-900/40 chronicle-dot-active'
                  : i < currentStep ? 'border-green-700/60 bg-green-900/30' : 'border-gray-700 bg-gray-800/40'">
                <span v-if="i < currentStep">✓</span><span v-else>{{ i + 1 }}</span>
              </span>
              <span class="uppercase tracking-wide hidden sm:inline">{{ t('chronicle.steps.' + s) }}</span>
            </div>
            <span v-if="i < STEPS.length - 1" class="text-gray-700 mx-0.5">→</span>
          </template>
        </div>

        <div class="flex-1 overflow-y-auto p-5 grid grid-cols-1 lg:grid-cols-2 gap-5">

          <!-- ── LEFT: settings ───────────────────────────────────────────── -->
          <div class="flex flex-col gap-4">
            <div class="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-4">
              <!-- base image + random-from-library picker -->
              <div class="rounded-xl border border-gray-700 bg-gray-800/40 flex flex-col items-center justify-center gap-2 p-3 min-h-[160px]">
                <img v-if="baseSha" :src="baseThumbSrc" @error="thumbFailed = true"
                  class="max-h-28 rounded-lg object-contain" />
                <span v-else class="text-3xl">🖼️</span>
                <button @click="pickRandomBase" :disabled="pickingRandom"
                  class="w-full px-2.5 py-1 rounded-lg border border-teal-700/60 bg-teal-900/30 hover:bg-teal-800/50 text-teal-200 text-[10px] font-medium disabled:opacity-40 transition-colors">
                  🎲 {{ pickingRandom ? t('chronicle.randomPicking') : t('chronicle.randomFromLibrary') }}
                </button>
                <p class="text-[10px] text-gray-500 text-center leading-tight">
                  {{ t('chronicle.baseHint') }}
                </p>
              </div>

              <!-- settings -->
              <div class="flex flex-col gap-3 text-xs">
                <!-- base time axis -->
                <div class="flex items-center gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0">{{ t('chronicle.baseAxis') }}</span>
                  <button v-for="a in AXES" :key="a" @click="baseAxis = a"
                    :class="baseAxis === a ? 'bg-teal-700 text-white border-teal-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
                    class="px-2.5 py-1 rounded-full border transition-colors">{{ t('chronicle.axis.' + a) }}</button>
                </div>
                <!-- user topic (お題) -->
                <div class="flex items-start gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0 pt-1">💡 {{ t('chronicle.userTopic') }}</span>
                  <textarea v-model="userTopic" rows="2" :placeholder="t('chronicle.userTopicPh')"
                    class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-gray-200 resize-none focus:border-teal-500 outline-none"></textarea>
                </div>
                <!-- worldview -->
                <div class="flex items-start gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0 pt-1">{{ t('chronicle.worldview') }}</span>
                  <textarea v-model="worldview" rows="2" :placeholder="t('chronicle.worldviewPh')"
                    class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-gray-200 resize-none focus:border-teal-500 outline-none"></textarea>
                </div>
                <!-- prompt style -->
                <div class="flex items-center gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0">{{ t('chronicle.promptStyle') }}</span>
                  <button v-for="m in ['danbooru+natural', 'natural', 'danbooru']" :key="m" @click="promptStyle = m"
                    :class="promptStyle === m ? 'bg-teal-700 text-white border-teal-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
                    class="px-2.5 py-1 rounded-full border transition-colors">{{ t('chronicle.style.' + m.replace('+', '_')) }}</button>
                </div>
                <!-- workflow -->
                <div class="flex items-center gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0">{{ t('chronicle.workflow') }}</span>
                  <select v-model="workflow"
                    class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-gray-200 focus:border-teal-500 outline-none">
                    <option value="">{{ t('chronicle.workflowNone') }}</option>
                    <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
                  </select>
                </div>
                <!-- time scale -->
                <div class="flex items-center gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0" :title="t('chronicle.timeScaleTitle')">⏳ {{ t('chronicle.timeScaleLabel') }}</span>
                  <input v-model.number="timeScaleIdx" type="range" min="0" :max="TIME_SCALES.length - 1" step="1" class="flex-1 accent-teal-500" />
                  <span class="text-teal-400 w-16 text-right">± {{ t('chronicle.timeScale.' + TIME_SCALES[timeScaleIdx]) }}</span>
                </div>
                <!-- divergence -->
                <div class="flex flex-col gap-1">
                  <div class="flex items-center gap-2">
                    <span class="text-gray-500 w-20 flex-shrink-0" :title="t('chronicle.divergenceTitle')">⚗️ {{ t('chronicle.divergence') }}</span>
                    <input v-model.number="divergence" type="range" min="0" max="1" step="0.05" class="flex-1 accent-teal-500" />
                    <span class="text-teal-400 font-mono w-10 text-right">{{ Math.round(divergence * 100) }}%</span>
                  </div>
                  <p v-if="mutationTags.length" class="text-[10px] text-teal-500/80 pl-[calc(5rem+0.5rem)] break-all">
                    <span class="text-purple-300/80">✦ {{ t('chronicle.mutationTags') }}:</span>
                    {{ mutationTags.join(', ') }}
                  </p>
                </div>
                <!-- emotion register -->
                <div class="flex items-start gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0 pt-1" :title="t('chronicle.emotionTitle')">🌒 {{ t('chronicle.emotionLabel') }}</span>
                  <div class="flex flex-wrap gap-1 flex-1">
                    <button v-for="em in EMOTION_DIMENSIONS" :key="em"
                      @click="emotion = emotion === em ? '' : em"
                      :class="emotion === em
                        ? 'bg-indigo-700/60 border-indigo-500/60 text-indigo-200'
                        : 'bg-gray-800/60 border-gray-700/40 text-gray-500 hover:text-gray-300 hover:border-gray-600/60'"
                      class="px-2 py-1 rounded-lg border text-[10px] transition">
                      {{ t(`inspire.emotion.${em}`) }}
                    </button>
                  </div>
                </div>
                <!-- dramatic mode (story shape) -->
                <div class="flex items-start gap-2">
                  <span class="text-gray-500 w-20 flex-shrink-0 pt-1" :title="t('chronicle.dramaticModeTitle')">🎭 {{ t('chronicle.dramaticModeLabel') }}</span>
                  <div class="flex flex-wrap gap-1 flex-1">
                    <button
                      @click="dramaticMode = ''"
                      :class="dramaticMode === ''
                        ? 'bg-indigo-700/60 border-indigo-500/60 text-indigo-200'
                        : 'bg-gray-800/60 border-gray-700/40 text-gray-500 hover:text-gray-300 hover:border-gray-600/60'"
                      class="px-2 py-1 rounded-lg border text-[10px] transition">
                      {{ t('chronicle.dramaticModeAuto') }}
                    </button>
                    <button v-for="dm in DRAMATIC_MODES" :key="dm"
                      @click="dramaticMode = dramaticMode === dm ? '' : dm"
                      :class="dramaticMode === dm
                        ? 'bg-indigo-700/60 border-indigo-500/60 text-indigo-200'
                        : 'bg-gray-800/60 border-gray-700/40 text-gray-500 hover:text-gray-300 hover:border-gray-600/60'"
                      class="px-2 py-1 rounded-lg border text-[10px] transition">
                      {{ t(`chronicle.dramaticMode.${dm}`) }}
                    </button>
                  </div>
                </div>
                <!-- seed / manual -->
                <div class="flex items-center gap-4">
                  <label class="flex items-center gap-1.5 cursor-pointer text-gray-400">
                    <input v-model="useRefSeed" type="checkbox" class="accent-teal-500" />
                    {{ t('chronicle.seedInherit') }}
                  </label>
                  <label class="flex items-center gap-1.5 cursor-pointer text-gray-400">
                    <input v-model="manualMode" type="checkbox" class="accent-teal-500" />
                    {{ t('chronicle.manualMode') }}
                  </label>
                </div>
              </div>
            </div>

            <!-- actions -->
            <div class="flex items-center gap-3">
              <button @click="start" :disabled="running || !baseSha"
                class="px-4 py-2 bg-teal-700 hover:bg-teal-600 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors">
                {{ running ? t('chronicle.running') : t('chronicle.start') }}
              </button>
              <button v-if="running && groupId" @click="cancelGroup"
                class="px-3 py-2 bg-red-900/60 hover:bg-red-800/70 border border-red-700/50 rounded-lg text-xs text-red-200 transition-colors">
                {{ t('chronicle.cancel') }}
              </button>
              <span v-if="phase" class="text-[10px] text-gray-500 uppercase tracking-wide">{{ t('chronicle.phase.' + phase, phase) }}</span>
              <span v-if="seed !== null" class="text-[10px] text-gray-600 font-mono ml-auto">seed: {{ seed }}</span>
            </div>

            <!-- generate images (manual mode / no-workflow continue) -->
            <div v-if="canGenerate" class="flex items-center gap-3">
              <button @click="generateImages" :disabled="!workflow"
                class="px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors">
                🎨 {{ t('chronicle.generateImages') }}
              </button>
              <span v-if="!workflow" class="text-[10px] text-amber-400/80">{{ t('chronicle.noWorkflowHint') }}</span>
            </div>

            <!-- progress bar -->
            <div v-if="running || (finished && progress > 0)"
              class="h-1.5 w-full rounded-full bg-gray-800 overflow-hidden">
              <div class="chronicle-progress h-full rounded-full transition-all duration-500"
                :style="{ width: (progress * 100) + '%' }"></div>
            </div>

            <!-- image job status cards (generation progress) -->
            <div v-if="imageJobs.length" class="flex flex-wrap gap-2">
              <div v-for="j in imageJobs" :key="j.job_id"
                class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[10px]"
                :class="jobStatusClass(j.job_id)">
                <span class="font-bold uppercase tracking-wide">{{ t('chronicle.axis.' + j.axis) }}</span>
                <span>{{ jobStatusIcon(j.job_id) }}</span>
                <span class="opacity-70">{{ jobStatusLabel(j.job_id) }}</span>
              </div>
            </div>

            <!-- open the Storybook to view finished chronicles -->
            <button @click="emit('open-storybook')"
              class="self-start px-3 py-1.5 bg-amber-900/60 hover:bg-amber-800/70 border border-amber-600/40 hover:border-amber-500/60 rounded-lg text-xs font-medium text-amber-200 transition-colors">
              📖 {{ t('header.storybook') }}
            </button>

            <p v-if="errorMsg" class="text-xs text-red-400">{{ errorMsg }}</p>
          </div>

          <!-- ── RIGHT: candidates / story / prompts ──────────────────────── -->
          <div class="flex flex-col gap-4 min-w-0">

            <!-- candidate cards -->
            <div v-if="candidates.length" class="flex flex-col gap-2">
              <div class="flex items-center justify-between">
                <h3 class="text-xs font-bold text-amber-200 uppercase tracking-wide">{{ t('chronicle.candidatesTitle') }}</h3>
                <button @click="respin('candidates')" :disabled="running"
                  class="text-[10px] px-2 py-1 rounded-lg border border-gray-700 text-gray-400 hover:bg-gray-800 disabled:opacity-40 transition-colors">
                  ♻️ {{ t('chronicle.respinCandidates') }}
                </button>
              </div>
              <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
                <button v-for="c in candidates" :key="c.id" @click="selectCandidate(c.id)"
                  :disabled="running"
                  class="text-left flex flex-col gap-1.5 p-3 rounded-xl border transition-colors disabled:opacity-50"
                  :class="selectedCandidate === c.id
                    ? 'border-amber-500 bg-amber-900/20'
                    : 'border-gray-700 bg-gray-800/40 hover:border-amber-600/60 hover:bg-gray-800'">
                  <div class="flex items-center gap-1.5">
                    <span class="text-[9px] font-bold w-4 h-4 flex items-center justify-center rounded-full bg-gray-700 text-gray-200">{{ c.id }}</span>
                    <span class="text-xs font-bold text-amber-100 leading-tight">{{ c.title }}</span>
                  </div>
                  <!-- three time-axis beats: base axis highlighted -->
                  <div class="flex flex-col gap-1 text-[10px] leading-snug">
                    <div v-for="ax in AXES" :key="ax" v-show="c[ax] || c.summary">
                      <span class="font-bold uppercase tracking-wide mr-1"
                        :class="ax === baseAxis ? 'text-amber-400' : 'text-teal-400'">{{ t('chronicle.axis.' + ax) }}</span>
                      <span class="text-gray-300">{{ c[ax] || (ax === 'present' ? c.summary : '') }}</span>
                    </div>
                  </div>
                  <div v-if="c.motif || c.key_motif" class="mt-auto pt-1">
                    <span class="text-[9px] px-1.5 py-0.5 rounded bg-gray-900/70 text-purple-300">
                      ✦ {{ c.motif || c.key_motif }}
                    </span>
                  </div>
                  <span class="text-[10px] font-medium mt-1"
                    :class="finished ? 'text-purple-300/90' : 'text-amber-400/90'">
                    {{ finished ? t('chronicle.forkFromCandidate') : t('chronicle.candidateSelect') }} →
                  </span>
                </button>
              </div>
            </div>

            <!-- title + overall story -->
            <div v-if="displayTitle" class="flex flex-col gap-1.5">
              <div class="flex items-center gap-2">
                <h3 class="text-sm font-bold text-amber-200 flex-1">📖 {{ displayTitle }}</h3>
                <button v-if="finished" @click="respin('expand')" :disabled="running"
                  class="text-[10px] px-2 py-1 rounded-lg border border-gray-700 text-gray-400 hover:bg-gray-800 disabled:opacity-40 transition-colors">
                  ♻️ {{ t('chronicle.respinStory') }}
                </button>
                <div v-if="titleJa || overallJa" class="flex rounded-lg overflow-hidden border border-gray-700 text-[10px]">
                  <button v-for="l in ['ja', 'en']" :key="l" @click="panelLang = l"
                    :class="panelLang === l ? 'bg-amber-800/70 text-amber-100' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
                    class="px-2 py-1 transition-colors uppercase">{{ l }}</button>
                </div>
              </div>
              <p v-if="displayOverall"
                class="text-[11px] text-gray-300 leading-relaxed whitespace-pre-wrap border-l-2 border-amber-700/40 pl-3">
                {{ displayOverall }}
              </p>
            </div>

            <!-- stream output -->
            <div v-if="streamText || (running && !selecting)"
              class="bg-gray-950/60 border border-gray-800 rounded-xl p-4 text-xs text-gray-300 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto font-light">{{ streamText }}<span v-if="running" class="animate-pulse text-teal-400">▍</span></div>

            <!-- per-axis prompts (editable in manual mode) -->
            <div v-if="Object.keys(prompts).length" class="flex flex-col gap-3">
              <h3 class="text-xs font-bold text-gray-400 uppercase tracking-wide">{{ t('chronicle.prompts') }}</h3>
              <div v-for="(p, axis) in prompts" :key="axis"
                class="bg-gray-800/40 border border-gray-800 rounded-xl p-3 flex flex-col gap-2">
                <span class="text-[10px] font-bold text-teal-400 uppercase">{{ t('chronicle.axis.' + axis) }}</span>
                <textarea v-model="p.positive" rows="3" :readonly="!canGenerate"
                  class="bg-gray-900 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-gray-200 resize-y focus:border-teal-500 outline-none"></textarea>
                <textarea v-model="p.negative" rows="1" :readonly="!canGenerate" :placeholder="t('chronicle.negative')"
                  class="bg-gray-900 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-gray-400 resize-y focus:border-teal-500 outline-none"></textarea>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
@keyframes lamp-pulse {
  0%, 100% { opacity: 0.3; box-shadow: 0 0 4px currentColor; }
  50%      { opacity: 1.0; box-shadow: 0 0 12px currentColor; }
}
.chronicle-lamp.is-running {
  animation: lamp-pulse 2s ease-in-out infinite;
}
@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(45, 212, 191, 0.4); }
  50%      { box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.15); }
}
.chronicle-dot-active {
  animation: dot-pulse 1.6s ease-in-out infinite;
}
@keyframes progress-shift {
  0% { background-position: 0 0; }
  100% { background-position: 40px 0; }
}
.chronicle-progress {
  background-image: linear-gradient(
    45deg,
    rgba(20, 184, 166, 0.9) 25%, rgba(45, 212, 191, 0.7) 25%,
    rgba(45, 212, 191, 0.7) 50%, rgba(20, 184, 166, 0.9) 50%,
    rgba(20, 184, 166, 0.9) 75%, rgba(45, 212, 191, 0.7) 75%
  );
  background-size: 40px 40px;
  animation: progress-shift 1s linear infinite;
}
</style>

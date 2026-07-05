<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
  baseImage: { type: Object, default: null },   // gallery-selected image doc
  comfyOffline: { type: Boolean, default: false },
})
const emit = defineEmits(['update:show', 'toast'])

const AXES = ['past', 'present', 'future']

// ── form state ────────────────────────────────────────────────────────────────
const baseSha = ref('')
const baseAxis = ref('present')
const worldview = ref('')
const promptStyle = ref('danbooru+natural')
const workflows = ref([])
const workflow = ref('')
const divergence = ref(0)
const useRefSeed = ref(true)
const manualMode = ref(false)
const dragOver = ref(false)
const uploading = ref(false)

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
const streamText = ref('')
const storyId = ref('')
const groupId = ref('')
const seed = ref(null)
const prompts = ref({})        // axis -> {positive, negative} (editable)
const imageJobs = ref([])
const doneManual = ref(false)  // manual mode: pipeline done, waiting for user
const errorMsg = ref('')

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

function resetRun() {
  phase.value = ''
  streamText.value = ''
  storyId.value = ''
  seed.value = null
  prompts.value = {}
  imageJobs.value = []
  doneManual.value = false
  errorMsg.value = ''
}

// ── external image drop ───────────────────────────────────────────────────────
async function onDrop(e) {
  dragOver.value = false
  const file = e.dataTransfer?.files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/story/upload-base', { method: 'POST', body: fd })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    const data = await r.json()
    baseSha.value = data.sha256
    emit('toast', { msg: t('chronicle.uploaded'), type: 'success' })
  } catch (err) {
    emit('toast', { msg: t('chronicle.uploadFailed') + ': ' + err.message, type: 'error' })
  } finally {
    uploading.value = false
  }
}

// ── pipeline ──────────────────────────────────────────────────────────────────
async function start() {
  if (!baseSha.value) {
    emit('toast', { msg: t('chronicle.noBase'), type: 'error' })
    return
  }
  resetRun()
  running.value = true
  _flushTimer = setInterval(_flushTokens, 66)
  try {
    const r = await fetch('/api/story/chronicle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_sha256: baseSha.value,
        base_time_axis: baseAxis.value,
        worldview: worldview.value,
        prompt_style: promptStyle.value,
        workflow_name: workflow.value,
        divergence: divergence.value,
        use_ref_seed: useRefSeed.value,
        manual_mode: manualMode.value,
      }),
    })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    const { job_id, group_id } = await r.json()
    groupId.value = group_id
    await readStream(job_id)
  } catch (err) {
    errorMsg.value = String(err.message || err)
    emit('toast', { msg: errorMsg.value, type: 'error' })
  } finally {
    running.value = false
    if (_flushTimer) { clearInterval(_flushTimer); _flushTimer = null }
    _flushTokens()
  }
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
      break
    case 'token':
      _pendingTokens += ev.text
      break
    case 'axis_prompt':
      prompts.value = { ...prompts.value, [ev.axis]: { positive: ev.positive, negative: ev.negative } }
      break
    case 'story_saved':
      storyId.value = ev.story_id
      break
    case 'warning':
      emit('toast', { msg: ev.message, type: 'warning' })
      break
    case 'image_jobs':
      imageJobs.value = ev.jobs
      break
    case 'done':
      seed.value = ev.seed
      if (ev.manual_mode) doneManual.value = true
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
      body: JSON.stringify({ axes, seed: seed.value }),
    })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    const data = await r.json()
    imageJobs.value = data.jobs
    doneManual.value = false
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
      <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[92vh] flex flex-col">

        <!-- header -->
        <div class="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h2 class="text-base font-bold text-teal-300">📜 {{ t('chronicle.title') }}</h2>
          <button @click="close"
            class="text-gray-600 hover:text-gray-200 text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
        </div>

        <div class="flex-1 overflow-y-auto p-5 flex flex-col gap-4">

          <!-- base image + settings -->
          <div class="grid grid-cols-1 md:grid-cols-[180px_1fr] gap-4">
            <!-- base image / drop zone -->
            <div
              class="rounded-xl border-2 border-dashed flex flex-col items-center justify-center gap-2 p-3 min-h-[180px] transition-colors"
              :class="dragOver ? 'border-teal-400 bg-teal-900/20' : 'border-gray-700 bg-gray-800/40'"
              @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop">
              <img v-if="baseSha" :src="baseThumbSrc" @error="thumbFailed = true"
                class="max-h-36 rounded-lg object-contain" />
              <span v-else class="text-3xl">🖼️</span>
              <p class="text-[10px] text-gray-500 text-center leading-tight">
                {{ uploading ? t('chronicle.uploading') : t('chronicle.dropHint') }}
              </p>
            </div>

            <!-- settings -->
            <div class="flex flex-col gap-3 text-xs">
              <!-- base time axis -->
              <div class="flex items-center gap-2">
                <span class="text-gray-500 w-24 flex-shrink-0">{{ t('chronicle.baseAxis') }}</span>
                <button v-for="a in AXES" :key="a" @click="baseAxis = a"
                  :class="baseAxis === a ? 'bg-teal-700 text-white border-teal-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
                  class="px-2.5 py-1 rounded-full border transition-colors">{{ t('chronicle.axis.' + a) }}</button>
              </div>
              <!-- worldview -->
              <div class="flex items-start gap-2">
                <span class="text-gray-500 w-24 flex-shrink-0 pt-1">{{ t('chronicle.worldview') }}</span>
                <textarea v-model="worldview" rows="2" :placeholder="t('chronicle.worldviewPh')"
                  class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-2.5 py-1.5 text-gray-200 resize-none focus:border-teal-500 outline-none"></textarea>
              </div>
              <!-- prompt style -->
              <div class="flex items-center gap-2">
                <span class="text-gray-500 w-24 flex-shrink-0">{{ t('chronicle.promptStyle') }}</span>
                <button v-for="m in ['danbooru+natural', 'natural', 'danbooru']" :key="m" @click="promptStyle = m"
                  :class="promptStyle === m ? 'bg-teal-700 text-white border-teal-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
                  class="px-2.5 py-1 rounded-full border transition-colors">{{ t('chronicle.style.' + m.replace('+', '_')) }}</button>
              </div>
              <!-- workflow -->
              <div class="flex items-center gap-2">
                <span class="text-gray-500 w-24 flex-shrink-0">{{ t('chronicle.workflow') }}</span>
                <select v-model="workflow"
                  class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-gray-200 focus:border-teal-500 outline-none">
                  <option value="">{{ t('chronicle.workflowNone') }}</option>
                  <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
                </select>
              </div>
              <!-- divergence -->
              <div class="flex items-center gap-2">
                <span class="text-gray-500 w-24 flex-shrink-0" :title="t('chronicle.divergenceTitle')">⚗️ {{ t('chronicle.divergence') }}</span>
                <input v-model.number="divergence" type="range" min="0" max="1" step="0.05" class="flex-1 accent-teal-500" />
                <span class="text-teal-400 font-mono w-10 text-right">{{ Math.round(divergence * 100) }}%</span>
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

          <!-- stream output -->
          <div v-if="streamText || running"
            class="bg-gray-950/60 border border-gray-800 rounded-xl p-4 text-xs text-gray-300 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto font-light">{{ streamText }}<span v-if="running" class="animate-pulse text-teal-400">▍</span></div>

          <p v-if="errorMsg" class="text-xs text-red-400">{{ errorMsg }}</p>

          <!-- per-axis prompts (editable in manual mode) -->
          <div v-if="Object.keys(prompts).length" class="flex flex-col gap-3">
            <h3 class="text-xs font-bold text-gray-400 uppercase tracking-wide">{{ t('chronicle.prompts') }}</h3>
            <div v-for="(p, axis) in prompts" :key="axis"
              class="bg-gray-800/40 border border-gray-800 rounded-xl p-3 flex flex-col gap-2">
              <span class="text-[10px] font-bold text-teal-400 uppercase">{{ t('chronicle.axis.' + axis) }}</span>
              <textarea v-model="p.positive" rows="3" :readonly="!doneManual"
                class="bg-gray-900 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-gray-200 resize-y focus:border-teal-500 outline-none"></textarea>
              <textarea v-model="p.negative" rows="1" :readonly="!doneManual" :placeholder="t('chronicle.negative')"
                class="bg-gray-900 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-gray-400 resize-y focus:border-teal-500 outline-none"></textarea>
            </div>
            <button v-if="doneManual" @click="generateImages" :disabled="!workflow"
              class="self-start px-4 py-2 bg-purple-700 hover:bg-purple-600 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors">
              🎨 {{ t('chronicle.generateImages') }}
            </button>
          </div>

          <!-- queued image jobs -->
          <div v-if="imageJobs.length" class="text-[10px] text-gray-500">
            {{ t('chronicle.imagesQueued') }}:
            <span v-for="j in imageJobs" :key="j.job_id" class="ml-2 font-mono text-gray-400">{{ t('chronicle.axis.' + j.axis) }} → {{ j.job_id }}</span>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import BoardColumn from './weave/BoardColumn.vue'
import ScoreVlmBlock from './weave/ScoreVlmBlock.vue'
import GatesColumn from './weave/GatesColumn.vue'

const props = defineProps({
  show: Boolean,
  baseImage: Object,
  comfyOffline: Boolean,
  getJobsMap: { type: Function, default: null },
})
const emit = defineEmits(['update:show', 'toast', 'open-storybook'])

const { t } = useI18n()

const session = ref(null)
const busy = ref(false)
const errorMsg = ref('')
const personalityText = ref('')
const topic = ref('')
const authorStyle = ref('')
const authorId = ref('')
const authors = ref([])
const storyModel = ref('')
const vlmModel = ref('')
const llmProvider = ref('ollama')
const workflow = ref('')
const workflows = ref([])
const ollamaModels = ref([])
const recreateChips = ref([])
const useGalleryNn = ref(false)
const useVlmAssist = ref(true)
const selectedPanelKey = ref('panel_1')
const editingNarrative = ref('')
const pollTimer = ref(null)
const trackedJobs = ref([]) // {job_id, kind, slot?, panel_key?}
const eventSource = ref(null)
const streamLive = ref(false)
const RECREATE_OPTIONS = [
  { id: 'weak_plot', labelKey: 'weave.chip.weakPlot' },
  { id: 'too_dark', labelKey: 'weave.chip.tooDark' },
  { id: 'place_scatters', labelKey: 'weave.chip.placeScatters' },
  { id: 'weak_prop', labelKey: 'weave.chip.weakProp' },
  { id: 'cliche', labelKey: 'weave.chip.cliche' },
  { id: 'more_everyday', labelKey: 'weave.chip.moreEveryday' },
  { id: 'more_incident', labelKey: 'weave.chip.moreIncident' },
  { id: 'unclear_story', labelKey: 'weave.chip.unclearStory' },
]

const RATE_OPTIONS = [
  { id: 'good', labelKey: 'weave.rate.good' },
  { id: 'too_close', labelKey: 'weave.rate.tooClose' },
  { id: 'missing_prop', labelKey: 'weave.rate.missingProp' },
  { id: 'dead_expression', labelKey: 'weave.rate.deadExpression' },
  { id: 'sparse', labelKey: 'weave.rate.sparse' },
  { id: 'wrong_person', labelKey: 'weave.rate.wrongPerson' },
  { id: 'unclear_story', labelKey: 'weave.rate.unclearStory' },
]

const cta = computed(() => session.value?.next_cta || { code: 'infer_character', label: '', enabled: false })
const character = computed(() => session.value?.character || {})
const boardImages = computed(() => character.value?.board?.images || [])
const galleryRefs = computed(() => character.value?.gallery_refs || [])
const gallerySpice = computed(() => character.value?.gallery_spice || [])
const tagDiff = computed(() => character.value?.tag_diff || null)
const galleryNnStatus = computed(() => character.value?.gallery_nn || null)
const storyWorld = computed(() => session.value?.story_bundle?.world || {})
const panels = computed(() => session.value?.panels || [])
const gates = computed(() => session.value?.gates || {})
const lintDefects = computed(() => (
  cta.value?.defects
  || session.value?.gates?.G1?.defects
  || session.value?.last_lint?.defects
  || []
))
const criticReport = computed(() => session.value?.critic_report || null)
const characterWarnings = computed(() => character.value?.warnings || [])
const selectedPanel = computed(() =>
  panels.value.find(p => p.key === selectedPanelKey.value) || panels.value[0] || null
)
const compileLayers = computed(() => selectedPanel.value?.compile?.layers || null)
const sessionId = computed(() => session.value?.session_id || '')
const weaveScore = computed(() =>
  session.value?.cross_panel_qa?.weave_score
  || selectedPanel.value?.qa?.weave_score
  || null
)
const vlmAnswers = computed(() => selectedPanel.value?.qa?.vlm || null)
const crossQa = computed(() => session.value?.cross_panel_qa || {})

function thumb(sha) {
  if (!sha || String(sha).startsWith('pending') || String(sha).startsWith('placeholder')) return ''
  return `/api/thumbnails/${sha}.webp`
}

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const j = await resp.json()
      detail = j.detail || JSON.stringify(j)
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  if (resp.status === 204) return null
  return resp.json()
}

async function loadCatalog() {
  try {
    const cat = await api('/api/weave/catalog')
    workflows.value = cat?.comfyui?.workflows || []
    ollamaModels.value = cat?.llm?.ollama?.models || []
    authors.value = cat?.authors || []
    const suggested = cat?.suggested_run || {}
    if (!storyModel.value && suggested.story_model) storyModel.value = suggested.story_model
    if (!workflow.value && suggested.workflow_name) workflow.value = suggested.workflow_name
  } catch (e) {
    console.warn('[Weave] catalog', e)
  }
}

function onAuthorPick() {
  const a = authors.value.find(x => x.id === authorId.value)
  if (a?.style_description) authorStyle.value = a.style_description
  else if (!authorId.value) { /* keep freeform */ }
}

async function ensureSession() {
  if (session.value?.session_id) return session.value
  const body = {
    topic: topic.value,
    personality_text: personalityText.value,
    author_style: authorStyle.value,
    author_id: authorId.value,
    story_model: storyModel.value,
    vlm_model: vlmModel.value || storyModel.value,
    llm_provider: llmProvider.value,
    workflow_final: workflow.value,
    workflow_sample: workflow.value,
    reference_image_id: props.baseImage?.sha256 || '',
    locale: 'ja',
    use_gallery_nn: useGalleryNn.value,
  }
  session.value = await api('/api/weave/sessions', { method: 'POST', body: JSON.stringify(body) })
  if (session.value?.inputs?.author_style) authorStyle.value = session.value.inputs.author_style
  if (session.value?.session_id) connectStream(session.value.session_id)
  return session.value
}

async function refreshSession() {
  if (!sessionId.value) return
  session.value = await api(`/api/weave/sessions/${sessionId.value}`)
}

function closeStream() {
  if (eventSource.value) {
    eventSource.value.close()
    eventSource.value = null
  }
  streamLive.value = false
}

function connectStream(id) {
  if (!id) return
  if (eventSource.value) {
    const url = eventSource.value.url || ''
    if (url.includes(id)) return
    closeStream()
  }
  const es = new EventSource(
    `/api/weave/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`,
  )
  eventSource.value = es
  es.onopen = () => { streamLive.value = true }
  es.onmessage = async (e) => {
    try {
      const evt = JSON.parse(e.data)
      if (!evt?.type || evt.type === 'hello' || evt.type === 'ping') return
      await refreshSession()
    } catch (err) {
      console.debug('[Weave] stream event', err)
    }
  }
  es.onerror = () => { streamLive.value = false }
}

function trackJobs(jobs) {
  const list = Array.isArray(jobs) ? jobs : jobs ? [jobs] : []
  for (const j of list) {
    if (!j?.job_id) continue
    if (!trackedJobs.value.some(t => t.job_id === j.job_id)) {
      trackedJobs.value.push(j)
    }
  }
  startPoll()
}

function startPoll() {
  // Prefer SSE; keep light poll as fallback when jobs are tracked
  if (pollTimer.value) return
  pollTimer.value = setInterval(async () => {
    if (!trackedJobs.value.length || !props.getJobsMap) return
    const map = props.getJobsMap()
    let changed = false
    const still = []
    for (const j of trackedJobs.value) {
      const job = map.get(j.job_id)
      if (!job) { still.push(j); continue }
      if (job.state === 'succeeded' || job.state === 'failed' || job.state === 'cancelled') {
        changed = true
      } else {
        still.push(j)
      }
    }
    trackedJobs.value = still
    if (changed) await refreshSession()
    if (!trackedJobs.value.length && pollTimer.value) {
      clearInterval(pollTimer.value)
      pollTimer.value = null
    }
  }, 2000)
}

async function runAction(code) {
  errorMsg.value = ''
  busy.value = true
  try {
    await ensureSession()
    const id = sessionId.value
    // sync inputs
    await api(`/api/weave/sessions/${id}/inputs`, {
      method: 'PATCH',
      body: JSON.stringify({
        topic: topic.value,
        author_style: authorStyle.value,
        author_id: authorId.value,
        story_model: storyModel.value,
        vlm_model: vlmModel.value || storyModel.value,
        llm_provider: llmProvider.value,
        use_gallery_nn: useGalleryNn.value,
        vlm_assist: useVlmAssist.value,
      }),
    })

    if (code === 'infer_character') {
      // Locked re-infer must go through confirmReinfer() (story wipe).
      if (character.value?.identity_locked) {
        emit('toast', { msg: t('weave.suggestReinfer'), type: 'warn' })
        return
      }
      session.value = await api(`/api/weave/sessions/${id}/character/infer`, {
        method: 'POST',
        body: JSON.stringify({
          personality_text: personalityText.value,
          story_model: storyModel.value,
          use_gallery_nn: useGalleryNn.value,
        }),
      })
    } else if (code === 'lock_identity') {
      session.value = await api(`/api/weave/sessions/${id}/character/lock`, { method: 'POST' })
      // fire board render in parallel (non-blocking for story)
      try {
        const boardRes = await api(`/api/weave/sessions/${id}/character/board`, {
          method: 'POST',
          body: JSON.stringify({
            workflow_final: workflow.value,
            workflow_sample: workflow.value,
          }),
        })
        session.value = boardRes
        trackJobs(boardRes.jobs)
      } catch (e) {
        emit('toast', { msg: String(e.message || e), type: 'warn' })
      }
    } else if (code === 'generate_story') {
      session.value = await api(`/api/weave/sessions/${id}/story/generate`, {
        method: 'POST',
        body: JSON.stringify({ topic: topic.value, story_model: storyModel.value }),
      })
    } else if (code === 'recreate_story') {
      if (!recreateChips.value.length) {
        emit('toast', { msg: t('weave.needRecreateChip'), type: 'warn' })
        return
      }
      if (!window.confirm(t('weave.recreateConfirm'))) return
      session.value = await api(`/api/weave/sessions/${id}/story/recreate`, {
        method: 'POST',
        body: JSON.stringify({
          chips: recreateChips.value,
          story_model: storyModel.value,
        }),
      })
      recreateChips.value = []
    } else if (code === 'enter_lookdev') {
      session.value = await api(`/api/weave/sessions/${id}/lookdev`, { method: 'POST' })
    } else if (code === 'sample_panel') {
      const panelKey = panels.value.find(p => (p.intent?.camera === 'long_shot'))?.key || 'panel_1'
      const res = await api(`/api/weave/sessions/${id}/sample`, {
        method: 'POST',
        body: JSON.stringify({
          panel_key: panelKey,
          placeholder: false,
          workflow_sample: workflow.value,
        }),
      })
      session.value = res
      trackJobs(res.job)
    } else if (code === 'accept_board') {
      session.value = await api(`/api/weave/sessions/${id}/character/accept-board`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
    } else if (code === 'render_final') {
      const res = await api(`/api/weave/sessions/${id}/render_final`, {
        method: 'POST',
        body: JSON.stringify({ workflow_final: workflow.value }),
      })
      session.value = res
      trackJobs(res.jobs)
    } else if (code === 'seal') {
      session.value = await api(`/api/weave/sessions/${id}/seal`, { method: 'POST' })
      emit('toast', { msg: t('weave.sealed'), type: 'ok' })
      if (session.value?.storybook_story_id) {
        emit('open-storybook')
      }
    } else if (code === 'reeval_framing') {
      session.value = await api(`/api/weave/sessions/${id}/sample/reeval-framing`, {
        method: 'POST',
      })
    } else if (code === 'fix_framing_or_override') {
      const p = panels.value.find(p => p.qa?.framing === 'fail' || p.qa?.framing === 'unknown')
      if (!p) {
        await refreshSession()
        return
      }
      const fails = p.framing_fail_count || 0
      const limit = session.value?.quality_policy?.framing_fail_limit || 2
      if (fails < limit || p.qa?.framing === 'unknown') {
        if (!window.confirm(t('weave.resampleConfirm'))) return
        const res = await api(`/api/weave/sessions/${id}/sample`, {
          method: 'POST',
          body: JSON.stringify({
            panel_key: p.key,
            placeholder: false,
            workflow_sample: workflow.value,
          }),
        })
        session.value = res
        trackJobs(res.job)
      } else {
        const reason = window.prompt(t('weave.overrideReasonPh'), '')
        if (!reason || !String(reason).trim()) {
          emit('toast', { msg: t('weave.overrideNeedReason'), type: 'warn' })
          return
        }
        session.value = await api(`/api/weave/sessions/${id}/sample/override-framing`, {
          method: 'POST',
          body: JSON.stringify({ panel_key: p.key, reason: String(reason).trim() }),
        })
      }
    } else {
      await refreshSession()
    }
  } catch (e) {
    errorMsg.value = String(e.message || e)
    emit('toast', { msg: errorMsg.value, type: 'error' })
  } finally {
    busy.value = false
  }
}

async function recreate() {
  if (!recreateChips.value.length) {
    emit('toast', { msg: t('weave.needRecreateChip'), type: 'warn' })
    return
  }
  if (!window.confirm(t('weave.recreateConfirm'))) return
  busy.value = true
  errorMsg.value = ''
  try {
    await ensureSession()
    session.value = await api(`/api/weave/sessions/${sessionId.value}/story/recreate`, {
      method: 'POST',
      body: JSON.stringify({
        chips: recreateChips.value,
        story_model: storyModel.value,
      }),
    })
    recreateChips.value = []
  } catch (e) {
    errorMsg.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function ratePanel(panelKey, chip) {
  if (!sessionId.value) return
  busy.value = true
  try {
    session.value = await api(`/api/weave/sessions/${sessionId.value}/sample/rate`, {
      method: 'POST',
      body: JSON.stringify({ panel_key: panelKey, chips: [chip] }),
    })
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

async function seal() {
  busy.value = true
  try {
    session.value = await api(`/api/weave/sessions/${sessionId.value}/seal`, { method: 'POST' })
    emit('toast', { msg: t('weave.sealed'), type: 'ok' })
    if (session.value?.storybook_story_id) emit('open-storybook')
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

async function recomputeScore() {
  if (!sessionId.value) return
  busy.value = true
  try {
    session.value = await api(`/api/weave/sessions/${sessionId.value}/score`, { method: 'POST' })
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

async function runVlmAssist(forceHeuristic = false) {
  if (!sessionId.value || !selectedPanel.value) return
  busy.value = true
  try {
    session.value = await api(`/api/weave/sessions/${sessionId.value}/sample/vlm-assist`, {
      method: 'POST',
      body: JSON.stringify({
        panel_key: selectedPanel.value.key,
        force_heuristic: forceHeuristic,
        vlm_model: vlmModel.value || storyModel.value,
      }),
    })
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}


async function rollbackTo(version) {
  if (!sessionId.value) return
  if (!window.confirm(t('weave.rollbackConfirm', { v: version }))) return
  busy.value = true
  try {
    session.value = await api(`/api/weave/sessions/${sessionId.value}/story/rollback`, {
      method: 'POST',
      body: JSON.stringify({ to_version: version }),
    })
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

async function confirmReinfer() {
  if (!window.confirm(t('weave.reinferConfirm'))) return
  errorMsg.value = ''
  busy.value = true
  try {
    await ensureSession()
    const id = sessionId.value
    if (character.value?.identity_locked) {
      await api(`/api/weave/sessions/${id}/character/unlock`, {
        method: 'POST',
        body: JSON.stringify({ confirm: true }),
      })
    }
    session.value = await api(`/api/weave/sessions/${id}/character/infer`, {
      method: 'POST',
      body: JSON.stringify({
        personality_text: personalityText.value,
        story_model: storyModel.value,
        use_gallery_nn: useGalleryNn.value,
      }),
    })
  } catch (e) {
    errorMsg.value = String(e.message || e)
    emit('toast', { msg: errorMsg.value, type: 'error' })
  } finally {
    busy.value = false
  }
}

async function saveNarrativePatch() {
  if (!sessionId.value || !selectedPanel.value) return
  busy.value = true
  try {
    session.value = await api(`/api/weave/sessions/${sessionId.value}/story/narrative`, {
      method: 'PATCH',
      body: JSON.stringify({
        panel_key: selectedPanel.value.key,
        narrative_ja: editingNarrative.value,
      }),
    })
    emit('toast', { msg: t('weave.narrativeSaved'), type: 'ok' })
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

watch(() => session.value?.suggest_recreate, (v) => {
  if (!v) return
  emit('toast', { msg: t('weave.suggestRecreate'), type: 'warn' })
})

watch(() => session.value?.suggest_reinfer, (v) => {
  if (!v) return
  emit('toast', { msg: t('weave.suggestReinfer'), type: 'warn' })
})

watch(selectedPanel, (p) => {
  editingNarrative.value = p?.intent?.narrative_ja || ''
}, { immediate: true })

async function exportSession() {
  if (!sessionId.value) return
  busy.value = true
  try {
    const data = await api(`/api/weave/sessions/${sessionId.value}/export`)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `weave-${sessionId.value.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
    emit('toast', { msg: t('weave.exported'), type: 'ok' })
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

function close() {
  closeStream()
  emit('update:show', false)
}

function resetLocal() {
  closeStream()
  session.value = null
  errorMsg.value = ''
  trackedJobs.value = []
  recreateChips.value = []
  // useGalleryNn preference is kept across reset
}

watch(session, (s) => {
  if (!s) return
  const flag = s?.quality_policy?.gallery_nn ?? s?.inputs?.use_gallery_nn
  if (typeof flag === 'boolean') useGalleryNn.value = flag
  if (typeof s?.quality_policy?.vlm_assist === 'boolean') useVlmAssist.value = s.quality_policy.vlm_assist
  if (s?.inputs?.vlm_model) vlmModel.value = s.inputs.vlm_model
  if (s?.inputs?.llm_provider) llmProvider.value = s.inputs.llm_provider
  if (s.session_id) connectStream(s.session_id)
})

watch(() => props.show, async (v) => {
  if (v) {
    await loadCatalog()
    if (sessionId.value) connectStream(sessionId.value)
  } else {
    closeStream()
    if (pollTimer.value) {
      clearInterval(pollTimer.value)
      pollTimer.value = null
    }
  }
})

onUnmounted(() => {
  closeStream()
  if (pollTimer.value) clearInterval(pollTimer.value)
})
</script>

<template>
  <div v-if="show" class="fixed inset-0 z-[80] flex items-stretch justify-center bg-black/70 backdrop-blur-sm"
    @mousedown.self="close">
    <div class="m-3 flex w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-teal-800/50 bg-gray-950 text-gray-100 shadow-2xl">
      <!-- header -->
      <div class="flex items-center gap-3 border-b border-teal-900/50 px-4 py-3">
        <h2 class="text-sm font-semibold tracking-wide text-teal-200">{{ t('weave.title') }}</h2>
        <span v-if="session?.status" class="rounded bg-teal-950 px-2 py-0.5 text-[10px] text-teal-300/90">{{ session.status }}</span>
        <span v-if="comfyOffline" class="text-[10px] text-amber-400">{{ t('weave.comfyOffline') }}</span>
        <div class="flex-1" />
        <button class="text-xs text-gray-400 hover:text-white" @click="resetLocal">{{ t('weave.reset') }}</button>
        <button class="text-xs text-gray-400 hover:text-white" @click="close">✕</button>
      </div>

      <div class="grid min-h-0 flex-1 grid-cols-1 gap-0 md:grid-cols-[240px_1fr_280px]">
        <BoardColumn
          v-model:personality-text="personalityText"
          v-model:use-gallery-nn="useGalleryNn"
          v-model:use-vlm-assist="useVlmAssist"
          :character="character"
          :board-images="boardImages"
          :gallery-refs="galleryRefs"
          :gallery-spice="gallerySpice"
          :tag-diff="tagDiff"
          :gallery-nn-status="galleryNnStatus"
          :character-warnings="characterWarnings"
          :suggest-reinfer="!!session?.suggest_reinfer"
          :busy="busy"
          :thumb="thumb"
          @reinfer="confirmReinfer"
        />

        <!-- CENTER -->
        <main class="p-4 space-y-4 overflow-y-auto">
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-[10px] text-gray-500">{{ t('weave.topic') }}</label>
              <input v-model="topic" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs"
                :placeholder="t('weave.topicPh')" />
            </div>
            <div>
              <label class="text-[10px] text-gray-500">{{ t('weave.storyModel') }}</label>
              <select v-model="storyModel" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs font-mono">
                <option value="">—</option>
                <option v-for="m in ollamaModels" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] text-gray-500">{{ t('weave.vlmModel') }}</label>
              <select v-model="vlmModel" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs font-mono">
                <option value="">{{ t('weave.vlmModelSame') }}</option>
                <option v-for="m in ollamaModels" :key="'v'+m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] text-gray-500">{{ t('weave.llmProvider') }}</label>
              <select v-model="llmProvider" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
                <option value="ollama">ollama</option>
                <option value="openai">openai</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] text-gray-500">{{ t('weave.workflow') }}</label>
              <select v-model="workflow" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
                <option value="">—</option>
                <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
              </select>
            </div>
            <div>
              <label class="text-[10px] text-gray-500">{{ t('weave.authorPreset') }}</label>
              <select v-model="authorId" @change="onAuthorPick"
                class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
                <option value="">—</option>
                <option v-for="a in authors" :key="a.id" :value="a.id">
                  {{ a.name }}{{ a.genre_tag ? ` · ${a.genre_tag}` : '' }}
                </option>
              </select>
            </div>
            <div class="col-span-2">
              <label class="text-[10px] text-gray-500">{{ t('weave.authorStyle') }}</label>
              <input v-model="authorStyle" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs" />
            </div>
          </div>

          <!-- CTA -->
          <button
            class="w-full rounded-lg border border-teal-600/50 bg-teal-900/60 px-4 py-3 text-sm font-medium text-teal-100 hover:bg-teal-800/70 disabled:opacity-40"
            :disabled="busy || cta.enabled === false"
            @click="runAction(cta.code)">
            {{ busy ? t('weave.working') : (cta.label || t('weave.next')) }}
          </button>

          <p v-if="errorMsg" class="text-xs text-red-400 whitespace-pre-wrap">{{ errorMsg }}</p>

          <!-- lint defects → recreate only -->
          <div v-if="lintDefects.length || criticReport" class="rounded border border-amber-800/60 bg-amber-950/30 p-3 space-y-2">
            <div class="text-[10px] uppercase text-amber-400/90">{{ t('weave.lintDefects') }}</div>
            <p v-if="criticReport?.summary_ja" class="text-[11px] text-amber-50">{{ criticReport.summary_ja }}</p>
            <p v-if="criticReport?.recreate_hint" class="text-[10px] text-amber-300/80">
              tip: {{ criticReport.recreate_hint }}
            </p>
            <ul class="space-y-1 max-h-40 overflow-y-auto">
              <li v-for="(d, i) in (criticReport?.priority_defects || lintDefects)" :key="i" class="text-[11px] text-amber-100/90">
                <span class="text-amber-500/80 font-mono">{{ d.code }}</span>
                <span v-if="d.severity" class="text-[9px] text-gray-500"> {{ d.severity }}</span>
                {{ d.problem }}
                <span v-if="d.fix" class="block text-gray-400">→ {{ d.fix }}</span>
              </li>
            </ul>
            <p class="text-[10px] text-amber-200/70">{{ t('weave.lintDefectsHint') }}</p>
          </div>

          <!-- causality -->
          <div v-if="storyWorld.causality_one_liner" class="rounded border border-teal-900/40 bg-teal-950/30 p-3">
            <div class="text-[10px] uppercase text-teal-500/80 mb-1">{{ t('weave.causality') }}</div>
            <p class="text-sm text-teal-50/90">{{ storyWorld.causality_one_liner }}</p>
            <p class="mt-2 text-[11px] text-gray-400">{{ storyWorld.setting }} · {{ storyWorld.throughline_prop }}</p>
          </div>

          <!-- panels -->
          <div class="grid grid-cols-3 gap-2">
            <div v-for="p in panels" :key="p.key"
              class="rounded border bg-gray-900/50 overflow-hidden cursor-pointer"
              :class="selectedPanelKey === p.key ? 'border-teal-600/60' : 'border-gray-800'"
              @click="selectedPanelKey = p.key">
              <div class="px-2 py-1 text-[10px] text-gray-400 flex justify-between">
                <span>{{ p.key }}</span>
                <span>{{ p.intent?.camera }}</span>
              </div>
              <img v-if="thumb(p.sample?.image_id || p.final?.image_id)"
                :src="thumb(p.sample?.image_id || p.final?.image_id)"
                class="w-full aspect-square object-cover" />
              <div v-else class="aspect-square flex items-center justify-center text-[10px] text-gray-600 px-2 text-center">
                {{ p.intent?.visible_change || '—' }}
              </div>
              <p class="px-2 py-1 text-[10px] text-gray-400 line-clamp-3">{{ p.intent?.narrative_ja }}</p>
              <div class="flex flex-wrap gap-1 px-2 pb-2">
                <button v-for="r in RATE_OPTIONS" :key="r.id"
                  class="rounded bg-gray-800 px-1.5 py-0.5 text-[9px] text-gray-300 hover:bg-gray-700"
                  @click="ratePanel(p.key, r.id)">{{ t(r.labelKey) }}</button>
              </div>
            </div>
          </div>

          <!-- narrative typo + layers -->
          <div v-if="selectedPanel" class="rounded border border-gray-800 p-3 space-y-2">
            <div class="flex items-center justify-between">
              <div class="text-[10px] uppercase text-gray-500">{{ t('weave.narrativeEdit') }} · {{ selectedPanel.key }}</div>
              <button class="text-[10px] text-teal-300 disabled:opacity-40" :disabled="busy" @click="saveNarrativePatch">
                {{ t('weave.narrativeSave') }}
              </button>
            </div>
            <textarea v-model="editingNarrative" rows="2"
              class="w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs"
              :placeholder="t('weave.narrativePh')" />
            <div v-if="compileLayers" class="space-y-1">
              <div class="text-[10px] uppercase text-teal-500/80">{{ t('weave.layers') }}</div>
              <div v-for="(tags, layer) in compileLayers" :key="layer" class="flex flex-wrap gap-1 items-start">
                <span class="text-[9px] text-gray-500 w-16 shrink-0">{{ layer }}</span>
                <span v-for="tag in (tags || [])" :key="layer+tag"
                  class="rounded bg-gray-800 px-1 py-0.5 text-[9px] text-gray-300">{{ tag }}</span>
                <span v-if="!(tags || []).length" class="text-[9px] text-gray-600">—</span>
              </div>
            </div>

            <ScoreVlmBlock
              :weave-score="weaveScore"
              :vlm-answers="vlmAnswers"
              :selected-panel="selectedPanel"
              :use-vlm-assist="useVlmAssist"
              :busy="busy"
              :session-id="sessionId"
              @score="recomputeScore"
              @vlm="runVlmAssist"
            />
          </div>

          <!-- recreate -->
          <div class="rounded border border-gray-800 p-3 space-y-2"
            :class="session?.suggest_recreate ? 'border-amber-600/60' : ''">
            <div class="text-[10px] uppercase text-gray-500">{{ t('weave.recreate') }}</div>
            <p v-if="session?.suggest_recreate" class="text-[10px] text-amber-300">{{ t('weave.suggestRecreate') }}</p>
            <div class="flex flex-wrap gap-1">
              <button v-for="c in RECREATE_OPTIONS" :key="c.id"
                class="rounded px-2 py-1 text-[10px] border"
                :class="recreateChips.includes(c.id)
                  ? 'border-amber-500 bg-amber-950 text-amber-100'
                  : 'border-gray-700 text-gray-400'"
                @click="recreateChips.includes(c.id)
                  ? recreateChips = recreateChips.filter(x => x !== c.id)
                  : recreateChips.push(c.id)">
                {{ t(c.labelKey) }}
              </button>
            </div>
            <button class="rounded border border-amber-700/50 bg-amber-950/50 px-3 py-1.5 text-xs text-amber-100 disabled:opacity-40"
              :disabled="busy || !sessionId" @click="recreate">
              {{ t('weave.recreateGo') }}
            </button>
          </div>

          <!-- rollback -->
          <div v-if="(session?.story_history || []).length" class="rounded border border-gray-800 p-3 space-y-2">
            <div class="text-[10px] uppercase text-gray-500">{{ t('weave.rollback') }}</div>
            <div class="space-y-1 max-h-32 overflow-y-auto">
              <button v-for="h in (session.story_history || []).slice().reverse()" :key="h.version"
                class="flex w-full items-start justify-between gap-2 rounded bg-gray-900/80 px-2 py-1.5 text-left hover:bg-gray-800"
                :disabled="busy" @click="rollbackTo(h.version)">
                <span class="text-[10px] text-teal-300">v{{ h.version }}</span>
                <span class="flex-1 text-[10px] text-gray-400 line-clamp-2">
                  {{ h.bundle?.world?.causality_one_liner || (h.reasons || []).join(', ') || '—' }}
                </span>
              </button>
            </div>
          </div>
        </main>

        <GatesColumn
          :gates="gates"
          :cross-qa="crossQa"
          :timeline="session?.timeline || []"
          :stream-live="streamLive"
          :session-status="session?.status || ''"
          :busy="busy"
          @seal="seal"
          @export="exportSession"
          @open-storybook="emit('open-storybook')"
        />
      </div>
    </div>
  </div>
</template>

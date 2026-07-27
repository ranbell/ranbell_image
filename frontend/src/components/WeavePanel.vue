<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'

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
const storyModel = ref('')
const workflow = ref('')
const workflows = ref([])
const ollamaModels = ref([])
const recreateChips = ref([])
const useGalleryNn = ref(false)
const pollTimer = ref(null)
const trackedJobs = ref([]) // {job_id, kind, slot?, panel_key?}

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
const sessionId = computed(() => session.value?.session_id || '')

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
    const suggested = cat?.suggested_run || {}
    if (!storyModel.value && suggested.story_model) storyModel.value = suggested.story_model
    if (!workflow.value && suggested.workflow_name) workflow.value = suggested.workflow_name
  } catch (e) {
    console.warn('[Weave] catalog', e)
  }
}

async function ensureSession() {
  if (session.value?.session_id) return session.value
  const body = {
    topic: topic.value,
    personality_text: personalityText.value,
    author_style: authorStyle.value,
    story_model: storyModel.value,
    workflow_final: workflow.value,
    workflow_sample: workflow.value,
    reference_image_id: props.baseImage?.sha256 || '',
    locale: 'ja',
    use_gallery_nn: useGalleryNn.value,
  }
  session.value = await api('/api/weave/sessions', { method: 'POST', body: JSON.stringify(body) })
  return session.value
}

async function refreshSession() {
  if (!sessionId.value) return
  session.value = await api(`/api/weave/sessions/${sessionId.value}`)
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
  if (pollTimer.value) return
  pollTimer.value = setInterval(async () => {
    if (!propsJobs.value.length || !props.getJobsMap) return
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
  }, 1500)
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
        story_model: storyModel.value,
        use_gallery_nn: useGalleryNn.value,
      }),
    })

    if (code === 'infer_character') {
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
    } else if (code === 'fix_framing_or_override') {
      const p = panels.value.find(p => p.qa?.framing === 'fail')
      if (p) {
        session.value = await api(`/api/weave/sessions/${id}/sample/override-framing`, {
          method: 'POST',
          body: JSON.stringify({ panel_key: p.key, reason: 'user override from UI' }),
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
  } catch (e) {
    emit('toast', { msg: String(e.message || e), type: 'error' })
  } finally {
    busy.value = false
  }
}

function close() {
  emit('update:show', false)
}

function resetLocal() {
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
})

watch(() => props.show, async (v) => {
  if (v) {
    await loadCatalog()
    if (props.baseImage?.sha256 && !session.value) {
      // keep reference for new session
    }
  } else {
    if (pollTimer.value) {
      clearInterval(pollTimer.value)
      pollTimer.value = null
    }
  }
})

onUnmounted(() => {
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
        <!-- LEFT: character board -->
        <aside class="border-r border-gray-800 p-3 space-y-3 overflow-y-auto">
          <label class="block text-[10px] uppercase tracking-wider text-teal-500/80">{{ t('weave.personality') }}</label>
          <textarea v-model="personalityText" rows="4"
            class="w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs"
            :placeholder="t('weave.personalityPh')" />

          <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-1.5 cursor-pointer">
            <input v-model="useGalleryNn" type="checkbox" class="mt-0.5 accent-teal-500" />
            <span>
              <span class="block text-[11px] text-teal-100">{{ t('weave.galleryNn') }}</span>
              <span class="block text-[10px] text-gray-500 leading-snug">{{ t('weave.galleryNnHint') }}</span>
            </span>
          </label>

          <div class="space-y-1">
            <div class="text-[10px] text-gray-500">identity</div>
            <div class="flex flex-wrap gap-1">
              <span v-for="tag in (character.identity_tags || [])" :key="'i'+tag"
                class="rounded bg-teal-950/80 px-1.5 py-0.5 text-[10px] text-teal-200">{{ tag }}</span>
            </div>
            <div class="text-[10px] text-gray-500 mt-2">prop</div>
            <div class="flex flex-wrap gap-1">
              <span v-for="tag in (character.prop_tags || [])" :key="'p'+tag"
                class="rounded bg-amber-950/80 px-1.5 py-0.5 text-[10px] text-amber-200">{{ tag }}</span>
            </div>
            <template v-if="gallerySpice.length">
              <div class="text-[10px] text-gray-500 mt-2">{{ t('weave.gallerySpice') }}</div>
              <div class="flex flex-wrap gap-1">
                <span v-for="tag in gallerySpice" :key="'s'+tag"
                  class="rounded bg-cyan-950/80 px-1.5 py-0.5 text-[10px] text-cyan-200">{{ tag }}</span>
              </div>
            </template>
          </div>

          <div v-if="tagDiff && (tagDiff.added?.length || tagDiff.removed?.length || tagDiff.spice?.length)"
            class="rounded border border-cyan-900/50 bg-cyan-950/20 p-2 space-y-1">
            <div class="text-[10px] uppercase tracking-wider text-cyan-400/90">{{ t('weave.tagDiff') }}</div>
            <div v-if="tagDiff.added_from_reference?.length" class="text-[10px] text-teal-200">
              ref: +{{ tagDiff.added_from_reference.join(', ') }}
            </div>
            <div v-if="tagDiff.added_from_gallery?.length" class="text-[10px] text-cyan-200">
              gallery: +{{ tagDiff.added_from_gallery.join(', ') }}
            </div>
            <div v-if="tagDiff.removed?.length" class="text-[10px] text-amber-300">
              −{{ tagDiff.removed.join(', ') }}
            </div>
            <div class="text-[10px] text-gray-500">{{ t('weave.tagDiffHint') }}</div>
          </div>

          <p v-if="useGalleryNn && galleryNnStatus && !galleryNnStatus.applied && galleryNnStatus.reason && galleryNnStatus.reason !== 'skipped'"
            class="text-[10px] text-amber-400/90">
            {{ t('weave.galleryNnSkip', { reason: galleryNnStatus.reason }) }}
          </p>

          <div v-if="galleryRefs.length" class="space-y-1">
            <div class="text-[10px] uppercase tracking-wider text-cyan-500/80">{{ t('weave.galleryRefs') }}</div>
            <div class="grid grid-cols-3 gap-1">
              <a v-for="ref in galleryRefs" :key="ref.sha256"
                :href="`/api/images/${ref.sha256}`" target="_blank" rel="noopener"
                class="rounded border border-gray-800 overflow-hidden bg-gray-900"
                :title="ref.name || ref.sha256">
                <img v-if="thumb(ref.sha256)" :src="thumb(ref.sha256)" class="w-full aspect-square object-cover" />
              </a>
            </div>
          </div>
          <p v-else-if="useGalleryNn && character.identity_tags?.length && galleryNnStatus?.applied === false"
            class="text-[10px] text-gray-500">{{ t('weave.galleryEmpty') }}</p>

          <div class="text-[10px] uppercase tracking-wider text-teal-500/80">{{ t('weave.board') }}</div>
          <div class="grid grid-cols-1 gap-2">
            <div v-for="img in boardImages" :key="img.slot"
              class="rounded border border-gray-800 bg-gray-900/80 overflow-hidden">
              <div class="px-2 py-1 text-[10px] text-gray-400 flex justify-between">
                <span>{{ img.slot }}</span>
                <span v-if="img.pending || !img.image_id" class="text-amber-400">…</span>
              </div>
              <img v-if="thumb(img.image_id)" :src="thumb(img.image_id)" class="w-full aspect-[3/4] object-cover" />
              <div v-else class="aspect-[3/4] flex items-center justify-center text-[10px] text-gray-600">{{ t('weave.noImage') }}</div>
            </div>
          </div>
          <div class="text-[10px] text-gray-500" v-if="character.reasoning_ja">{{ character.reasoning_ja }}</div>
        </aside>

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
              <label class="text-[10px] text-gray-500">{{ t('weave.workflow') }}</label>
              <select v-model="workflow" class="mt-0.5 w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs">
                <option value="">—</option>
                <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
              </select>
            </div>
            <div>
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
          <div v-if="lintDefects.length" class="rounded border border-amber-800/60 bg-amber-950/30 p-3 space-y-2">
            <div class="text-[10px] uppercase text-amber-400/90">{{ t('weave.lintDefects') }}</div>
            <ul class="space-y-1 max-h-40 overflow-y-auto">
              <li v-for="(d, i) in lintDefects" :key="i" class="text-[11px] text-amber-100/90">
                <span class="text-amber-500/80 font-mono">{{ d.code }}</span>
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
              class="rounded border border-gray-800 bg-gray-900/50 overflow-hidden">
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

          <!-- recreate -->
          <div class="rounded border border-gray-800 p-3 space-y-2">
            <div class="text-[10px] uppercase text-gray-500">{{ t('weave.recreate') }}</div>
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
        </main>

        <!-- RIGHT: gates / timeline -->
        <aside class="border-l border-gray-800 p-3 space-y-3 overflow-y-auto text-xs">
          <div class="text-[10px] uppercase tracking-wider text-teal-500/80">{{ t('weave.gates') }}</div>
          <ul class="space-y-1">
            <li v-for="(g, key) in gates" :key="key" class="flex justify-between gap-2">
              <span class="text-gray-400">{{ key }}</span>
              <span :class="g.pass ? 'text-teal-400' : 'text-gray-600'">{{ g.pass ? '✓' : '·' }}</span>
            </li>
          </ul>

          <div class="text-[10px] uppercase tracking-wider text-teal-500/80 pt-2">{{ t('weave.timeline') }}</div>
          <ul class="space-y-2 max-h-64 overflow-y-auto">
            <li v-for="ev in (session?.timeline || []).slice().reverse()" :key="ev.id"
              class="rounded bg-gray-900/80 px-2 py-1.5">
              <div class="text-[9px] text-gray-500">{{ ev.actor }} · {{ ev.type }}</div>
              <div class="text-[11px] text-gray-300">{{ ev.text }}</div>
            </li>
          </ul>

          <button v-if="session?.status === 'lookdev' || session?.status === 'rendering'"
            class="w-full rounded border border-teal-700/40 px-2 py-1.5 text-teal-200 hover:bg-teal-950"
            :disabled="busy" @click="seal">
            {{ t('weave.seal') }}
          </button>
          <button class="w-full rounded border border-gray-700 px-2 py-1.5 text-gray-400 hover:bg-gray-900"
            @click="emit('open-storybook')">
            {{ t('weave.openStorybook') }}
          </button>
        </aside>
      </div>
    </div>
  </div>
</template>

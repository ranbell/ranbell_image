<script setup>
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useInspireSession, INVERSION_AXIS_IDS } from '../composables/useInspireSession.js'

const { t, locale } = useI18n()

const {
  inspireTab,
  inspireLoading,
  inspireResults,
  inspireMorphTimeline,
  inspireError,
  inspireSlots,
  arithmeticRoles,
  morphSlotA,
  morphSlotB,
  inspireAnomalyTags,
  inspireInversionTags,
  inspireInversionNegativeTags,
  inspireInversionStory,
  inversionChangeTargets,
  inversionStage,
  inversionStageLabel,
  inversionFixedTags,
  inversionVolatileTags,
  inversionNewTags,
  inversionAtmosphereTags,
  inversionUserInjectPrompt,
  inversionLang,
  inversionStrength,
  inspireInversionTagsNl,
  inversionPromptView,
  inversionRemovedTags,
  inversionFixedTagsGrouped,
  inversionVolatileTagsGrouped,
  inversionNewTagsGrouped,
  inversionLlmClassification,
  inversionStep2RawResult,
  inversionJobId,
  brainstormJobId,
  brainstormLoading,
  brainstormText,
  brainstormStreaming,
  inspireRightView,
  inversionStoryStreaming,
  discoverContextRoles,
  groupedSearchQuery,
  groupedBy,
  inspireGroupedResults,
  blendWeights,
  outlierMode,
  inspireResultSelection,
  toggleInspireResultSelection,
  isRunning,
  hasSession,
  resetSession,
  setActiveReader,
} = useInspireSession()

// Tag color-coding Sets for inversion (O(1) lookup)
const newTagsSet        = computed(() => new Set(inversionNewTags.value))
const atmosphereTagsSet = computed(() => new Set(inversionAtmosphereTags.value))

// Per-tab pipeline definitions (type: source/vec/wd14/llm/vlm/filter/result)
const pipelineDefs = computed(() => ({
  serendipity:    [[t('inspire.pipeline.refImage'), 'source'], [t('inspire.pipeline.vecAvg'), 'vec'], [t('inspire.pipeline.scoreFilter'), 'filter'], [t('inspire.pipeline.fetch12'), 'result']],
  arithmetic:     [[t('inspire.pipeline.refImagePlusMinus'), 'source'], [t('inspire.pipeline.vecArithmetic'), 'vec'], [t('inspire.pipeline.vecComposite'), 'vec'], [t('inspire.pipeline.nearestSearch'), 'result']],
  morph:          [[t('inspire.pipeline.imagesAB'), 'source'], [t('inspire.pipeline.getVecs'), 'vec'], [t('inspire.pipeline.interpolate3'), 'vec'], [t('inspire.pipeline.nearest4each'), 'result']],
  anomaly:        [[t('inspire.pipeline.refImage'), 'source'], [t('inspire.pipeline.wd14Extract'), 'wd14'], [t('inspire.pipeline.llmRareTags'), 'llm'], [t('inspire.pipeline.compositeVecSearch'), 'result']],
  inversion:      [[t('inspire.pipeline.refImage'), 'source'], [t('inspire.pipeline.wd14Extract'), 'wd14'], [t('inspire.pipeline.vlmOppositeTags'), 'vlm'], [t('inspire.pipeline.vecSearch'), 'result']],
  discover:       [[t('inspire.pipeline.targetImage'), 'source'], [t('inspire.pipeline.vecContrast'), 'vec'], [t('inspire.pipeline.qdrantDiscover'), 'result']],
  blend:          [[t('inspire.pipeline.weightedImages'), 'source'], [t('inspire.pipeline.weightedAvgVec'), 'vec'], [t('inspire.pipeline.nearestSearch'), 'result']],
  outlier:        [[t('inspire.pipeline.refImage'), 'source'], [t('inspire.pipeline.vecNegate'), 'vec'], [t('inspire.pipeline.farthestSearch'), 'result']],
  grouped_search: [[t('inspire.pipeline.textQuery'), 'source'], [t('inspire.pipeline.vectorize'), 'vec'], [t('inspire.pipeline.groupByField'), 'result']],
}))
const PIPELINE_CHIP_CLASS = {
  source: 'bg-gray-700/70 text-gray-300 border-gray-600/40',
  vec:    'bg-blue-900/60 text-blue-300 border-blue-700/50',
  wd14:   'bg-purple-900/60 text-purple-300 border-purple-700/50',
  llm:    'bg-green-900/60 text-green-300 border-green-700/50',
  vlm:    'bg-amber-900/60 text-amber-300 border-amber-700/50',
  filter: 'bg-gray-600/50 text-gray-400 border-gray-600/40',
  result: 'bg-indigo-900/60 text-indigo-300 border-indigo-700/50',
}
const pipelineSteps = computed(() => pipelineDefs.value[inspireTab.value] ?? [])

const INVERSION_AXES = computed(() => [
  { id: 'visual',       icon: '👁',  label: t('inspire.axes.visual'),        desc: t('inspire.axes.visualDesc') },
  { id: 'time_weather', icon: '🌤',  label: t('inspire.axes.time_weather'),  desc: t('inspire.axes.time_weatherDesc') },
  { id: 'emotion',      icon: '😶',  label: t('inspire.axes.emotion'),       desc: t('inspire.axes.emotionDesc') },
  { id: 'clothing',     icon: '👗',  label: t('inspire.axes.clothing'),      desc: t('inspire.axes.clothingDesc') },
  { id: 'hair',         icon: '💇',  label: t('inspire.axes.hair'),          desc: t('inspire.axes.hairDesc') },
  { id: 'style',        icon: '🎨',  label: t('inspire.axes.style'),         desc: t('inspire.axes.styleDesc') },
  { id: 'location',     icon: '📍',  label: t('inspire.axes.location'),      desc: t('inspire.axes.locationDesc') },
  { id: 'narrative',    icon: '🌐',  label: t('inspire.axes.narrative'),     desc: t('inspire.axes.narrativeDesc') },
  { id: 'action',       icon: '🏃',  label: t('inspire.axes.action'),        desc: t('inspire.axes.actionDesc') },
  { id: 'parts',        icon: '🫀',  label: t('inspire.axes.parts'),         desc: t('inspire.axes.partsDesc') },
])

const props = defineProps({
  show: Boolean,
  running: Boolean,
  initialSlots: Array,  // sha256[] from selectedIds at open time
  selectedIds: Array,   // sha256[] — received as Array; converted to Set internally
})

// Array prop → internal Set (Vue reactivity tracks Array mutations reliably)
const selectedSet = computed(() => new Set(props.selectedIds || []))

const emit = defineEmits([
  'update:show',
  'update:running',
  'select-image',
  'toggle-image-selection',
  'send-to-refine',
  'send-to-refine-direct',
  'toast',
])

// ── Initialize on open ────────────────────────────────────────────────────────
watch(() => props.show, (val) => {
  if (!val) return
  if (!hasSession.value) {
    resetSession(props.initialSlots || [])
  } else {
    // Keep session results intact; only sync slots to the latest selection
    inspireSlots.value = (props.initialSlots || []).slice(0, 6)
  }
})

// Notify parent of running state
watch([inspireLoading, brainstormLoading], ([il, bl]) => {
  emit('update:running', il || bl)
})

// ── Functions ─────────────────────────────────────────────────────────────────

function toggleArithmeticRole(sha256) {
  arithmeticRoles.value = {
    ...arithmeticRoles.value,
    [sha256]: arithmeticRoles.value[sha256] === 'add' ? 'sub' : 'add',
  }
}

async function runInspire() {
  console.log('[runInspire] called, tab=', inspireTab.value)
  inspireResults.value = []
  inspireMorphTimeline.value = []
  inspireError.value = ''
  inspireAnomalyTags.value = []
  inspireInversionTags.value = []
  inspireInversionNegativeTags.value = []
  inspireInversionStory.value = ''
  inversionFixedTags.value = []
  inversionVolatileTags.value = []
  inversionNewTags.value = []
  inversionAtmosphereTags.value = []
  inversionRemovedTags.value = []
  inversionFixedTagsGrouped.value = {}
  inversionStoryStreaming.value = ''
  inversionStage.value = 0
  inspireRightView.value = 'results'
  inspireLoading.value = true
  try {
    if (inspireTab.value === 'serendipity') {
      const r = await fetch('/api/inspire/serendipity', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sha256s: inspireSlots.value, n_results: 12 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      inspireResults.value = (await r.json()).results

    } else if (inspireTab.value === 'arithmetic') {
      const add_sha256s = inspireSlots.value.filter(s => arithmeticRoles.value[s] === 'add')
      const sub_sha256s = inspireSlots.value.filter(s => arithmeticRoles.value[s] === 'sub')
      const r = await fetch('/api/inspire/arithmetic', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ add_sha256s, sub_sha256s, n_results: 12 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      inspireResults.value = (await r.json()).results

    } else if (inspireTab.value === 'morph') {
      if (!morphSlotA.value || !morphSlotB.value) throw new Error(t('inspire.errors.morphNoEndpoints'))
      const r = await fetch('/api/inspire/morph', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sha256_a: morphSlotA.value, sha256_b: morphSlotB.value, steps: 3 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      inspireMorphTimeline.value = (await r.json()).timeline

    } else if (inspireTab.value === 'anomaly') {
      const r = await fetch('/api/inspire/anomaly', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sha256s: inspireSlots.value, n_results: 12 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      const data = await r.json()
      inspireResults.value = data.results
      inspireAnomalyTags.value = data.anomaly_tags || []

    } else if (inspireTab.value === 'inversion') {
      console.log('[runInspire] inversion submit start')
      // Step 1: submit job to PROMPT lane
      const submitR = await fetch('/api/inspire/inversion', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sha256s: inspireSlots.value,
          n_results: 12,
          change_targets: inversionChangeTargets.value,
          user_inject_prompt: inversionUserInjectPrompt.value,
          custom_blacklist: [],
          lang: inversionLang.value,
          inversion_strength: inversionStrength.value,
        }),
      })
      if (!submitR.ok) throw new Error((await submitR.json()).detail || `HTTP ${submitR.status}`)
      const { job_id } = await submitR.json()
      inversionJobId.value = job_id
      console.log('[runInspire] inversion job_id=', job_id)

      // Step 2: connect to SSE stream
      const streamR = await fetch(`/api/inspire/inversion/${job_id}/stream`)
      if (!streamR.ok || !streamR.body) throw new Error(`Stream HTTP ${streamR.status}`)
      const reader = streamR.body.getReader()
      setActiveReader(reader)
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          try {
            const evt = JSON.parse(line.slice(5).trim())
            console.log('[SSE evt]', evt.type)
            if (evt.type === 'step1_result') {
              inversionFixedTags.value = evt.fixed_tags || []
              inversionVolatileTags.value = evt.volatile_tags || []
              inversionFixedTagsGrouped.value = evt.fixed_tags_grouped || {}
              inversionVolatileTagsGrouped.value = evt.volatile_tags_grouped || {}
              inversionLlmClassification.value = evt.llm_classification || {}
              console.log('[step1] fixedLen=', inversionFixedTags.value.length, 'volLen=', inversionVolatileTags.value.length)
              // Scroll the tag classification card into view once it appears
              setTimeout(() => {
                const el = document.getElementById('inversion-classification-card')
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }, 100)
            } else if (evt.type === 'step2_result') {
              inversionNewTags.value = evt.new_tags || []
              inversionNewTagsGrouped.value = evt.new_tags_by_axis || {}
              inversionStep2RawResult.value = evt.step2_raw_by_axis || {}
              console.log('[step2] newTagsLen=', inversionNewTags.value.length,
                'rawAxes=', Object.keys(inversionStep2RawResult.value),
                'raw=', JSON.stringify(evt.step2_raw_by_axis).slice(0, 200))
              setTimeout(() => {
                const el = document.getElementById('inversion-step2-raw-card')
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }, 100)
            } else if (evt.type === 'step2b_result') {
              inversionNewTags.value = evt.new_tags || inversionNewTags.value
              inversionNewTagsGrouped.value = evt.new_tags_by_axis || inversionNewTagsGrouped.value
              inversionStep2RawResult.value = evt.step2_raw_by_axis || inversionStep2RawResult.value
            } else if (evt.type === 'step3_result') {
              inversionAtmosphereTags.value = evt.atmosphere_tags || []
            } else if (evt.type === 'stage') {
              inversionStage.value = evt.stage
              inversionStageLabel.value = evt.label
            } else if (evt.type === 'story_token') {
              inversionStoryStreaming.value += evt.text
            } else if (evt.type === 'done') {
              inspireResults.value = evt.results || []
              inspireInversionTags.value = evt.inversion_tags || []
              inspireInversionTagsNl.value = evt.inversion_tags_nl || ''
              inspireInversionNegativeTags.value = evt.inversion_negative_tags || []
              inspireInversionStory.value = evt.inversion_story || ''
              inversionFixedTags.value = evt.fixed_tags || inversionFixedTags.value
              inversionFixedTagsGrouped.value = evt.fixed_tags_grouped || inversionFixedTagsGrouped.value
              inversionVolatileTags.value = evt.volatile_tags || inversionVolatileTags.value
              inversionVolatileTagsGrouped.value = evt.volatile_tags_grouped || inversionVolatileTagsGrouped.value
              inversionLlmClassification.value = evt.llm_classification || inversionLlmClassification.value
              inversionNewTags.value = evt.new_tags || inversionNewTags.value
              inversionNewTagsGrouped.value = evt.new_tags_by_axis || inversionNewTagsGrouped.value
              inversionStep2RawResult.value = evt.step2_raw_by_axis || inversionStep2RawResult.value
              inversionAtmosphereTags.value = evt.atmosphere_tags || inversionAtmosphereTags.value
              inversionRemovedTags.value = evt.removed_tags || []
              inversionStoryStreaming.value = ''
              inversionStage.value = 0
            } else if (evt.type === 'cancelled') {
              break
            } else if (evt.type === 'error') {
              const e = new Error(evt.message)
              e._backendError = true
              throw e
            }
          } catch (parseErr) {
            // Backend error type propagates up (stops loading, shows error message)
            if (parseErr?._backendError) throw parseErr
            // Other errors (JSON parse failure, runtime errors) are logged and the loop continues
            // — prevents a RuntimeError from halting the entire loop
            if (!(parseErr instanceof SyntaxError)) console.error('[SSE]', parseErr)
            continue
          }
        }
      }
      inversionJobId.value = null

    } else if (inspireTab.value === 'discover') {
      if (inspireSlots.value.length < 1) throw new Error(t('inspire.errors.discoverNoTarget'))
      const contextShas = inspireSlots.value.slice(1)
      const positives = contextShas.filter(s => discoverContextRoles.value[s] !== 'negative')
      const negatives = contextShas.filter(s => discoverContextRoles.value[s] === 'negative')
      if (positives.length === 0) throw new Error(t('inspire.errors.discoverNeedPositive'))
      if (negatives.length === 0) throw new Error(t('inspire.errors.discoverNeedNegative'))
      const maxPairs = Math.max(positives.length, negatives.length)
      const contextPairs = Array.from({ length: maxPairs }, (_, i) => [
        positives[i % positives.length],
        negatives[i % negatives.length],
      ])
      const r = await fetch('/api/inspire/discover', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_sha256: inspireSlots.value[0], context_pairs: contextPairs, n_results: 20 }),
      })
      if (!r.ok) {
        const err = await r.json()
        throw new Error(Array.isArray(err.detail) ? err.detail.map(e => e.msg).join(', ') : err.detail || `HTTP ${r.status}`)
      }
      inspireResults.value = (await r.json()).results

    } else if (inspireTab.value === 'grouped_search') {
      if (!groupedSearchQuery.value.trim()) throw new Error(t('inspire.errors.groupedNoQuery'))
      const r = await fetch('/api/inspire/grouped-search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: groupedSearchQuery.value, group_by: groupedBy.value, group_size: 3, limit: 10 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      inspireGroupedResults.value = (await r.json()).groups
      inspireResults.value = []

    } else if (inspireTab.value === 'blend') {
      const slots = inspireSlots.value.map(sha => ({
        sha256: sha, weight: blendWeights.value[sha] ?? 0.5,
      }))
      const r = await fetch('/api/inspire/blend', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slots, n_results: 12 }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      inspireResults.value = (await r.json()).results

    } else if (inspireTab.value === 'outlier') {
      const r = await fetch('/api/inspire/outlier', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sha256s: inspireSlots.value, n_results: 12, mode: outlierMode.value }),
      })
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`)
      inspireResults.value = (await r.json()).results
    }
  } catch (e) {
    inspireError.value = e.message
  } finally {
    inspireLoading.value = false
  }
}

const isResultSelectionTab = computed(() =>
  ['serendipity', 'anomaly', 'inversion', 'blend', 'outlier', 'arithmetic'].includes(inspireTab.value)
)

watch(inspireResults, (newResults) => {
  if (isResultSelectionTab.value) {
    inspireResultSelection.value = new Set(newResults.slice(0, 3).map(r => r.sha256))
  }
})

const brainstormBasis = computed(() => {
  const hasResults = inspireResults.value.length > 0
  const tab = inspireTab.value

  if (tab === 'morph') {
    return {
      sha256s: [morphSlotA.value, morphSlotB.value].filter(Boolean),
      label: t('inspire.basisMorphLabel'),
      sublabel: t('inspire.basisMorphSublabel'),
      color: 'blue',
    }
  }
  if (['serendipity', 'anomaly', 'inversion', 'blend', 'outlier', 'arithmetic'].includes(tab) && hasResults) {
    return {
      sha256s: [...new Set([
        ...inspireSlots.value,
        ...[...inspireResultSelection.value].filter(
          sha => inspireResults.value.some(r => r.sha256 === sha)
        ),
      ])],
      label: t('inspire.basisResultsLabel'),
      sublabel: t('inspire.basisResultsSublabel', { n: inspireSlots.value.length }),
      color: 'indigo',
    }
  }
  return {
    sha256s: inspireSlots.value,
    label: t('inspire.basisDefaultLabel'),
    sublabel: t('inspire.basisDefaultSublabel'),
    color: 'gray',
  }
})

async function runBrainstorm() {
  brainstormLoading.value = true
  brainstormText.value = ''
  brainstormStreaming.value = ''
  inspireRightView.value = 'brainstorm'
  const basis = brainstormBasis.value
  try {
    // Step 1: submit job to PROMPT lane
    const submitR = await fetch('/api/inspire/brainstorm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sha256s: basis.sha256s,
        extra_tags: inspireTab.value === 'inversion' ? inspireInversionTags.value : inspireAnomalyTags.value,
        lang: locale.value,
      }),
    })
    if (!submitR.ok) throw new Error((await submitR.json()).detail || `HTTP ${submitR.status}`)
    const { job_id } = await submitR.json()
    brainstormJobId.value = job_id

    // Step 2: connect to SSE stream
    const streamR = await fetch(`/api/inspire/brainstorm/${job_id}/stream`)
    if (!streamR.ok || !streamR.body) throw new Error(`Stream HTTP ${streamR.status}`)
    const reader = streamR.body.getReader()
    setActiveReader(reader)
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        try {
          const evt = JSON.parse(line.slice(5).trim())
          if (evt.type === 'token') brainstormStreaming.value += evt.text
          else if (evt.type === 'done') {
            brainstormText.value = brainstormStreaming.value
            brainstormStreaming.value = ''
          } else if (evt.type === 'cancelled') {
            brainstormText.value = brainstormStreaming.value
            brainstormStreaming.value = ''
          } else if (evt.type === 'error') {
            inspireError.value = evt.message
          }
        } catch {}
      }
    }
    brainstormJobId.value = null
  } catch (e) {
    inspireError.value = e.message
  } finally {
    brainstormLoading.value = false
  }
}

function buildInspireContext() {
  const mode = inspireTab.value
  const ctx = { mode }
  if (mode === 'arithmetic') {
    ctx.add_sha256s = inspireSlots.value.filter(s => arithmeticRoles.value[s] === 'add')
    ctx.sub_sha256s = inspireSlots.value.filter(s => arithmeticRoles.value[s] === 'sub')
  } else if (mode === 'morph') {
    ctx.sha256_a = morphSlotA.value
    ctx.sha256_b = morphSlotB.value
  } else if (mode === 'inversion') {
    ctx.change_targets = inversionChangeTargets.value
  } else if (mode === 'anomaly') {
    ctx.injected_tags = inspireAnomalyTags.value
  }
  return ctx
}

function sendToRefine(sectionText) {
  emit('send-to-refine', {
    shas: inspireSlots.value.slice(0, 6),
    text: sectionText.trim(),
    inspireContext: buildInspireContext(),
  })
}

function sendToRefineDirectly(prompt, source = 'inversion', negativePrompt = '') {
  emit('send-to-refine-direct', {
    shas: inspireSlots.value.slice(0, 6),
    directPrompt: prompt,
    directNegativePrompt: negativePrompt,
    source,
    inspireContext: buildInspireContext(),
  })
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    emit('toast', { msg: t('inspire.copySuccess'), type: 'success' })
  } catch {
    emit('toast', { msg: t('inspire.copyFailed'), type: 'error' })
  }
}

function parseBrainstormSections(text) {
  if (!text) return []
  const sections = []
  let current = null
  for (const line of text.split('\n')) {
    if (/^##\s/.test(line)) {
      if (current) sections.push(current)
      current = { title: line.replace(/^##\s+/, ''), body: '' }
    } else if (current) {
      current.body += line + '\n'
    }
  }
  if (current) sections.push(current)
  return sections
}

function simpleMarkdown(text) {
  if (!text) return ''
  const s = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const lines = s.split('\n')
  let html = ''
  let inList = false
  const fmt = t => t
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-gray-100 font-semibold">$1</strong>')
    .replace(/`(.+?)`/g, '<code class="text-purple-300 bg-gray-800 px-1 rounded text-[11px]">$1</code>')
  for (const raw of lines) {
    const line = raw.trimEnd()
    if (/^###\s/.test(line)) {
      if (inList) { html += '</ul>'; inList = false }
      html += `<h3 class="text-sm font-bold text-purple-300 mt-3 mb-1">${fmt(line.replace(/^###\s+/, ''))}</h3>`
    } else if (/^##\s/.test(line)) {
      if (inList) { html += '</ul>'; inList = false }
      html += `<h2 class="text-base font-bold text-purple-200 mt-4 mb-2">${fmt(line.replace(/^##\s+/, ''))}</h2>`
    } else if (/^#\s/.test(line)) {
      if (inList) { html += '</ul>'; inList = false }
      html += `<h1 class="text-lg font-bold text-purple-100 mt-4 mb-2">${fmt(line.replace(/^#\s+/, ''))}</h1>`
    } else if (/^[-*]\s/.test(line)) {
      if (!inList) { html += '<ul class="space-y-1 my-2">'; inList = true }
      html += `<li class="flex items-start gap-1.5 text-gray-300 text-sm leading-relaxed"><span class="text-purple-400 flex-shrink-0 mt-0.5">•</span><span>${fmt(line.replace(/^[-*]\s+/, ''))}</span></li>`
    } else {
      if (inList) { html += '</ul>'; inList = false }
      if (line === '') html += '<div class="h-2"></div>'
      else html += `<p class="text-gray-300 text-sm leading-relaxed">${fmt(line)}</p>`
    }
  }
  if (inList) html += '</ul>'
  return html
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[55] bg-black/92 flex items-center justify-center p-3"
      @click.self="emit('update:show', false)">
      <div class="bg-gray-900 rounded-2xl w-full max-w-7xl shadow-2xl border border-gray-800/80 max-h-[95vh] flex flex-col"
        style="box-shadow: 0 0 60px rgba(99,102,241,0.15), 0 25px 60px rgba(0,0,0,0.7);">

        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-800/70 flex-shrink-0">
          <div class="flex items-center gap-3">
            <span class="text-xl">🔮</span>
            <h2 class="font-semibold text-gray-100">{{ $t('inspire.title') }}</h2>
            <span class="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full">{{ $t('inspire.refCount', { n: inspireSlots.length }) }}</span>
            <span v-if="inspireLoading"
              class="flex items-center gap-1.5 text-xs text-purple-300 bg-purple-900/40 border border-purple-700/40 px-2 py-0.5 rounded-full">
              <span class="w-1.5 h-1.5 rounded-full bg-purple-400"></span>{{ $t('inspire.searching') }}
            </span>
            <span v-if="brainstormLoading"
              class="flex items-center gap-1.5 text-xs text-blue-300 bg-blue-900/40 border border-blue-700/40 px-2 py-0.5 rounded-full">
              <span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span>{{ $t('inspire.brainstorming') }}
            </span>
          </div>
          <button
            @click="resetSession(props.initialSlots || [])"
            :disabled="isRunning"
            class="flex items-center gap-1 px-2.5 py-1 rounded-lg border text-[11px] font-medium transition-all
                   bg-gray-800/60 border-gray-700/40 text-gray-400 hover:text-gray-200 hover:bg-gray-800
                   disabled:opacity-30 disabled:cursor-not-allowed">
            {{ $t('inspire.newSession') }}
          </button>
          <button @click="emit('update:show', false)" class="text-gray-500 hover:text-gray-200 text-xl leading-none w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
        </div>

        <!-- Body -->
        <div class="flex flex-1 min-h-0 divide-x divide-gray-800/60">

          <!-- ── Left pane: Config ── -->
          <div class="w-[360px] flex-shrink-0 flex flex-col overflow-y-auto">
            <div class="p-5 space-y-5">

              <!-- Source images -->
              <div>
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2.5">{{ $t('inspire.sourceSlots') }}</p>
                <div class="flex flex-wrap gap-2.5 min-h-[52px]">
                  <div v-for="sha in inspireSlots" :key="sha" class="relative group/slot">
                    <img :src="`/api/thumbnails/${sha}.webp`"
                      class="w-12 h-12 rounded-xl object-cover ring-2 transition-all duration-200"
                      :class="inspireTab === 'arithmetic'
                        ? (arithmeticRoles[sha] === 'add' ? 'ring-emerald-500/80' : 'ring-red-500/80')
                        : inspireTab === 'morph'
                          ? (sha === morphSlotA ? 'ring-blue-500/80' : sha === morphSlotB ? 'ring-orange-500/80' : 'ring-gray-700/60')
                          : inspireTab === 'blend'
                            ? ((blendWeights[sha] ?? 0.5) > 0.3 ? 'ring-emerald-500/70' : (blendWeights[sha] ?? 0.5) < -0.3 ? 'ring-red-500/70' : 'ring-gray-600/50')
                            : 'ring-indigo-500/60'" />
                    <!-- Arithmetic toggle -->
                    <button v-if="inspireTab === 'arithmetic'"
                      @click="toggleArithmeticRole(sha)"
                      class="absolute -bottom-1.5 -right-1.5 w-5 h-5 rounded-full text-xs font-bold flex items-center justify-center shadow-lg border-2 border-gray-900 transition-colors leading-none"
                      :class="arithmeticRoles[sha] === 'add' ? 'bg-emerald-500 text-white' : 'bg-red-600 text-white'">
                      {{ arithmeticRoles[sha] === 'add' ? '+' : '−' }}
                    </button>
                    <!-- Morph labels -->
                    <div v-if="inspireTab === 'morph'"
                      class="absolute -bottom-2 left-1/2 -translate-x-1/2 text-[9px] font-bold px-1.5 py-0.5 rounded-full leading-none border border-gray-900"
                      :class="sha === morphSlotA ? 'bg-blue-600 text-white' : sha === morphSlotB ? 'bg-orange-600 text-white' : 'bg-gray-700 text-gray-500'">
                      {{ sha === morphSlotA ? 'A' : sha === morphSlotB ? 'B' : '—' }}
                    </div>
                  </div>
                  <p v-if="inspireSlots.length === 0" class="text-xs text-gray-600 italic self-center">
                    {{ $t('inspire.openHint') }}
                  </p>
                </div>
                <!-- Morph slot selectors -->
                <div v-if="inspireTab === 'morph' && inspireSlots.length >= 2" class="mt-3.5 grid grid-cols-2 gap-2">
                  <div>
                    <label class="text-[11px] text-blue-400 block mb-1 font-medium">{{ $t('inspire.morphA') }}</label>
                    <select v-model="morphSlotA"
                      class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-blue-500 transition-colors">
                      <option v-for="sha in inspireSlots" :key="sha" :value="sha">{{ sha.slice(0, 8) }}…</option>
                    </select>
                  </div>
                  <div>
                    <label class="text-[11px] text-orange-400 block mb-1 font-medium">{{ $t('inspire.morphB') }}</label>
                    <select v-model="morphSlotB"
                      class="w-full bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-orange-500 transition-colors">
                      <option v-for="sha in inspireSlots" :key="sha" :value="sha">{{ sha.slice(0, 8) }}…</option>
                    </select>
                  </div>
                </div>
              </div>

              <!-- Divider -->
              <div class="border-t border-gray-800/60"></div>

              <!-- Tab navigation -->
              <div>
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2.5">{{ $t('inspire.searchMode') }}</p>
                <div class="space-y-1.5">
                  <button v-for="tab in [
                    { id: 'serendipity',   icon: '✨', label: $t('inspire.tabs.serendipity'),   desc: $t('inspire.tabs.serendipityDesc'),   badges: ['vec'] },
                    { id: 'arithmetic',    icon: '⚗️', label: $t('inspire.tabs.arithmetic'),    desc: $t('inspire.tabs.arithmeticDesc'),    badges: ['vec'] },
                    { id: 'morph',         icon: '🌊', label: $t('inspire.tabs.morph'),         desc: $t('inspire.tabs.morphDesc'),         badges: ['vec'] },
                    { id: 'anomaly',       icon: '⚡', label: $t('inspire.tabs.anomaly'),       desc: $t('inspire.tabs.anomalyDesc'),       badges: ['WD14', 'LLM'] },
                    { id: 'inversion',     icon: '🪞', label: $t('inspire.tabs.inversion'),     desc: $t('inspire.tabs.inversionDesc'),     badges: ['WD14', 'VLM'] },
                    { id: 'discover',      icon: '🧭', label: $t('inspire.tabs.discover'),      desc: $t('inspire.tabs.discoverDesc'),      badges: ['vec'] },
                    { id: 'grouped_search',icon: '🗂️', label: $t('inspire.tabs.groupedSearch'), desc: $t('inspire.tabs.groupedSearchDesc'), badges: ['vec'] },
                    { id: 'blend',         icon: '⚖️', label: $t('inspire.tabs.blend'),         desc: $t('inspire.tabs.blendDesc'),         badges: ['vec'] },
                    { id: 'outlier',       icon: '🌌', label: $t('inspire.tabs.outlier'),       desc: $t('inspire.tabs.outlierDesc'),       badges: ['vec'] },
                  ]" :key="tab.id"
                    @click="inspireTab = tab.id; inspireResults = []; inspireMorphTimeline = []; inspireGroupedResults = []; inspireError = ''"
                    class="w-full text-left px-3.5 py-3 rounded-xl border transition-all duration-150 cursor-pointer"
                    :class="inspireTab === tab.id
                      ? 'bg-indigo-900/50 border-indigo-500/50 text-white shadow-inner'
                      : 'bg-gray-800/30 border-gray-700/40 text-gray-400 hover:border-gray-600/60 hover:text-gray-200 hover:bg-gray-800/60'">
                    <div class="flex items-center gap-2.5">
                      <span class="text-base leading-none flex-shrink-0">{{ tab.icon }}</span>
                      <div class="min-w-0 flex-1">
                        <p class="text-xs font-semibold leading-tight truncate">{{ tab.label }}</p>
                        <p class="text-[11px] opacity-55 leading-tight mt-0.5 truncate">{{ tab.desc }}</p>
                      </div>
                      <div class="flex items-center gap-1 flex-shrink-0">
                        <span v-for="b in tab.badges" :key="b"
                          class="text-[9px] font-bold px-1.5 py-0.5 rounded border leading-none"
                          :class="{
                            'bg-blue-900/60  text-blue-300   border-blue-700/50':   b === 'vec',
                            'bg-purple-900/60 text-purple-300 border-purple-700/50': b === 'WD14',
                            'bg-amber-900/60 text-amber-300  border-amber-700/50':  b === 'VLM',
                            'bg-green-900/60 text-green-300  border-green-700/50':  b === 'LLM',
                          }">{{ b }}</span>
                        <span v-if="inspireTab === tab.id" class="text-indigo-400 text-xs ml-0.5">●</span>
                      </div>
                    </div>
                  </button>
                </div>
              </div>

              <!-- Inversion axis selector -->
              <div v-if="inspireTab === 'inversion'" class="space-y-2">
                <div class="flex items-center justify-between">
                  <p class="text-xs font-semibold text-cyan-600 uppercase tracking-wide">{{ $t('inspire.inversionAxes') }}</p>
                  <button @click="inversionChangeTargets = INVERSION_AXIS_IDS.slice()"
                    class="text-[10px] text-cyan-700 hover:text-cyan-400 transition-colors">{{ $t('inspire.inversionSelectAll') }}</button>
                </div>
                <div class="space-y-0.5">
                  <label v-for="axis in INVERSION_AXES" :key="axis.id"
                    class="flex items-center gap-2.5 cursor-pointer py-1.5 px-2.5 rounded-lg hover:bg-cyan-900/20 transition-colors">
                    <input type="checkbox" :value="axis.id" v-model="inversionChangeTargets"
                      class="rounded border-cyan-700/60 bg-gray-800 accent-cyan-500 cursor-pointer flex-shrink-0" />
                    <span class="text-sm leading-none flex-shrink-0">{{ axis.icon }}</span>
                    <span class="text-xs text-gray-300 font-medium">{{ axis.label }}</span>
                    <span class="text-[10px] text-gray-600 ml-auto truncate">{{ axis.desc }}</span>
                  </label>
                </div>
                <p v-if="inversionChangeTargets.length === 0" class="text-[11px] text-amber-500/70 px-1">
                  ⚠ {{ $t('inspire.inversionSelectRequired') }}
                </p>
                <!-- User inject prompt -->
                <input v-model="inversionUserInjectPrompt"
                  type="text"
                  :placeholder="t('inspire.placeholderExtra')"
                  class="w-full bg-gray-800/60 border border-gray-700/60 rounded-lg px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-cyan-500/60 transition-colors" />
                <!-- Inversion strength slider -->
                <div class="space-y-1 pt-1">
                  <div class="flex justify-between text-[10px] text-gray-500">
                    <span>{{ t('inspire.inversionStrength') }}</span>
                    <span>{{ Math.round(inversionStrength * 100) }}%</span>
                  </div>
                  <input type="range" min="0.1" max="1.0" step="0.1"
                    v-model.number="inversionStrength"
                    class="w-full h-1.5 accent-cyan-500 cursor-pointer" />
                  <div class="flex justify-between text-[10px] text-gray-600">
                    <span>{{ t('inspire.tempCalm') }}</span><span>{{ t('inspire.tempDramatic') }}</span>
                  </div>
                </div>
                <!-- Story language selector -->
                <div class="flex items-center gap-2 pt-1">
                  <span class="text-[10px] text-gray-500 flex-shrink-0">{{ t('inspire.storyLanguage') }}</span>
                  <div class="flex gap-1">
                    <button v-for="l in [['en','EN'],['ja','JA']]" :key="l[0]"
                      @click="inversionLang = l[0]"
                      :class="['px-2 py-0.5 rounded text-[10px] transition-colors',
                               inversionLang === l[0] ? 'bg-cyan-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600']">
                      {{ l[1] }}
                    </button>
                  </div>
                </div>
              </div>

              <!-- Discover input -->
              <div v-if="inspireTab === 'discover'" class="space-y-2">
                <p class="text-xs font-semibold text-indigo-400 uppercase tracking-wide">{{ $t('inspire.discoverTarget') }}</p>
                <p class="text-[11px] text-gray-500">{{ $t('inspire.discoverTargetDesc') }}</p>
                <div v-if="inspireSlots.length > 1" class="space-y-1.5 pt-1">
                  <p class="text-[11px] text-gray-400 font-medium">{{ $t('inspire.discoverContextImages') }}</p>
                  <div v-for="sha in inspireSlots.slice(1)" :key="sha" class="flex items-center gap-2">
                    <img :src="`/api/thumbnails/${sha}.webp`" class="w-8 h-8 rounded-lg object-cover flex-shrink-0" />
                    <div class="flex gap-1 flex-1">
                      <button v-for="role in [{ id: 'positive', label: $t('inspire.discoverClose') }, { id: 'negative', label: $t('inspire.discoverFar') }]"
                        :key="role.id" @click="discoverContextRoles[sha] = role.id"
                        class="flex-1 py-1 rounded-lg border text-[10px] transition-all"
                        :class="(discoverContextRoles[sha] || 'positive') === role.id
                          ? (role.id === 'positive' ? 'bg-emerald-900/40 border-emerald-600/50 text-emerald-300' : 'bg-red-900/40 border-red-600/50 text-red-300')
                          : 'bg-gray-800/30 border-gray-700/40 text-gray-500 hover:text-gray-300'">
                        {{ role.label }}
                      </button>
                    </div>
                  </div>
                </div>
                <p v-else class="text-[11px] text-amber-500/70">{{ $t('inspire.discoverNeedTwo') }}</p>
              </div>

              <!-- Grouped search input -->
              <div v-if="inspireTab === 'grouped_search'" class="space-y-2">
                <p class="text-xs font-semibold text-indigo-400 uppercase tracking-wide">{{ $t('inspire.groupedSearchHeader') }}</p>
                <textarea v-model="groupedSearchQuery" rows="2"
                  :placeholder="$t('inspire.placeholderGroupSearch')"
                  class="w-full bg-gray-800/60 border border-gray-700/60 rounded-xl px-3 py-2.5 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500/60 resize-none transition-colors" />
                <div class="flex gap-2">
                  <button v-for="g in [{ id: 'model_name', label: $t('inspire.groupByModel') }, { id: 'ext', label: $t('inspire.groupByFormat') }]"
                    :key="g.id" @click="groupedBy = g.id"
                    class="flex-1 py-1.5 rounded-lg border text-[11px] font-medium transition-all"
                    :class="groupedBy === g.id
                      ? 'bg-indigo-900/50 border-indigo-500/50 text-indigo-200'
                      : 'bg-gray-800/30 border-gray-700/40 text-gray-500 hover:text-gray-300'">
                    {{ g.label }}
                  </button>
                </div>
              </div>

              <!-- Blend weight sliders -->
              <div v-if="inspireTab === 'blend'" class="space-y-2.5">
                <p class="text-xs font-semibold text-indigo-400 uppercase tracking-wide">{{ $t('inspire.blendWeightsLabel') }}</p>
                <div v-for="sha in inspireSlots" :key="sha" class="flex items-center gap-2.5">
                  <img :src="`/api/thumbnails/${sha}.webp`" class="w-9 h-9 rounded-lg object-cover flex-shrink-0" />
                  <div class="flex-1 space-y-0.5">
                    <input type="range" min="-1" max="1" step="0.1"
                      :value="blendWeights[sha] ?? 0.5"
                      @input="blendWeights[sha] = parseFloat($event.target.value)"
                      class="w-full accent-indigo-500 cursor-pointer" />
                    <div class="flex justify-between text-[10px]">
                      <span class="text-red-400">{{ $t('inspire.exclude') }}</span>
                      <span class="text-gray-400 font-mono">{{ (blendWeights[sha] ?? 0.5).toFixed(1) }}</span>
                      <span class="text-emerald-400">{{ $t('inspire.add') }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Outlier mode selector -->
              <div v-if="inspireTab === 'outlier'" class="space-y-3">
                <p class="text-xs font-semibold text-indigo-400 uppercase tracking-wide">{{ $t('inspire.outlierModeLabel') }}</p>
                <div class="flex gap-2">
                  <button v-for="m in [
                    { id: 'antipode', icon: '🔄', label: $t('inspire.outlierAntipode'), desc: $t('inspire.outlierAntipodeDesc') },
                    { id: 'isolated', icon: '🏝', label: $t('inspire.outlierIsolated'), desc: $t('inspire.outlierIsolatedDesc') },
                  ]" :key="m.id"
                    @click="outlierMode = m.id"
                    class="flex-1 py-2 px-3 rounded-xl border text-xs transition-all text-left"
                    :class="outlierMode === m.id
                      ? 'bg-indigo-900/50 border-indigo-500/50 text-white'
                      : 'bg-gray-800/30 border-gray-700/40 text-gray-400 hover:border-gray-600/60'">
                    {{ m.icon }} {{ m.label }}
                    <p class="text-[10px] opacity-55 mt-0.5">{{ m.desc }}</p>
                  </button>
                </div>
                <p v-if="outlierMode === 'antipode' && inspireSlots.length === 0"
                  class="text-[11px] text-amber-500/70">{{ $t('inspire.outlierAntipodeError') }}</p>
                <p v-if="outlierMode === 'isolated'" class="text-[11px] text-gray-600">
                  {{ $t('inspire.outlierIsolatedHint') }}
                </p>
              </div>

              <!-- Execute button -->
              <button @click="runInspire"
                :disabled="inspireLoading
                  || (inspireTab !== 'outlier' && inspireTab !== 'grouped_search' && inspireSlots.length === 0)
                  || (inspireTab === 'outlier' && outlierMode === 'antipode' && inspireSlots.length === 0)
                  || (inspireTab === 'inversion' && inversionChangeTargets.length === 0)
                  || (inspireTab === 'discover' && inspireSlots.length < 2)
                  || (inspireTab === 'grouped_search' && !groupedSearchQuery.trim())"
                class="w-full py-3 rounded-xl text-sm font-semibold transition-all duration-150 disabled:opacity-40 flex items-center justify-center gap-2"
                :class="inspireLoading
                  ? 'bg-indigo-900/50 text-indigo-300 border border-indigo-700/40 cursor-wait'
                  : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-900/50 hover:shadow-indigo-800/60 active:scale-[0.98]'">
                <svg v-if="inspireLoading" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                <span>{{ inspireLoading ? $t('inspire.runningSearch') : $t('inspire.run') }}</span>
              </button>

              <!-- Cancel button (inversion running) -->
              <button v-if="inspireLoading && inspireTab === 'inversion' && inversionJobId"
                @click="resetSession(props.initialSlots || [])"
                class="w-full py-2 rounded-xl text-sm font-semibold transition-all duration-150 border border-red-700/40 text-red-400 hover:bg-red-900/30 hover:text-red-300">
                {{ $t('inspire.cancel') }}
              </button>

              <!-- Anomaly tags -->
              <div v-if="inspireAnomalyTags.length"
                class="bg-orange-950/40 border border-orange-800/30 rounded-xl p-3.5 space-y-2">
                <p class="text-xs font-semibold text-orange-400 uppercase tracking-wide">{{ $t('inspire.anomalyTags') }}</p>
                <div class="flex flex-wrap gap-1.5">
                  <span v-for="tag in inspireAnomalyTags" :key="tag"
                    class="px-2 py-0.5 bg-orange-900/60 border border-orange-700/40 text-orange-200 rounded-full text-xs font-mono">
                    {{ tag }}
                  </span>
                </div>
              </div>

              <!-- Step1: FIXED / VOLATILE tag split -->
              <div id="inversion-classification-card"
                v-if="inversionFixedTags.length || inversionVolatileTags.length"
                class="bg-gray-900/50 border border-gray-700/40 rounded-xl p-3.5 space-y-2.5">
                <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('inspire.tagClassification') }}</p>
                <!-- Grouped FIXED tags -->
                <div v-if="Object.keys(inversionFixedTagsGrouped).length" class="space-y-2">
                  <p class="text-[10px] text-emerald-400/80 font-semibold">{{ $t('inspire.fixedGrouped') }}</p>
                  <div v-for="(tags, group) in inversionFixedTagsGrouped" :key="group" class="space-y-0.5">
                    <p class="text-[9px] text-emerald-600/80 font-semibold uppercase tracking-wider">
                      {{ $t(`inspire.axes.${group}`, $t(`inspire.fixedGroups.${group}`, group)) }}</p>
                    <div class="flex flex-wrap gap-1">
                      <span v-for="tag in tags" :key="tag"
                        class="px-2 py-0.5 bg-emerald-900/50 border border-emerald-700/40 text-emerald-200 rounded-full text-[10px] font-mono">
                        {{ tag }}
                      </span>
                    </div>
                  </div>
                </div>
                <!-- Fallback flat FIXED (before grouping arrives) -->
                <div v-else-if="inversionFixedTags.length" class="space-y-1">
                  <p class="text-[10px] text-emerald-400/80 font-semibold">{{ $t('inspire.fixed') }}</p>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="tag in inversionFixedTags" :key="tag"
                      class="px-2 py-0.5 bg-emerald-900/50 border border-emerald-700/40 text-emerald-200 rounded-full text-[10px] font-mono">
                      {{ tag }}
                    </span>
                  </div>
                </div>
                <!-- Grouped VOLATILE tags -->
                <div v-if="Object.keys(inversionVolatileTagsGrouped).length" class="space-y-2">
                  <p class="text-[10px] text-orange-400/80 font-semibold">{{ $t('inspire.volatileGrouped') }}</p>
                  <div v-for="(tags, axis) in inversionVolatileTagsGrouped" :key="axis" class="space-y-0.5">
                    <p class="text-[9px] font-semibold uppercase tracking-wider"
                      :class="axis === 'other' ? 'text-gray-600/80' : 'text-orange-600/80'">
                      {{ $t(axis === 'other' ? 'inspire.axisOther' : `inspire.axes.${axis}`, axis) }}</p>
                    <div class="flex flex-wrap gap-1">
                      <span v-for="tag in tags" :key="tag"
                        class="px-2 py-0.5 bg-orange-900/50 border border-orange-700/40 text-orange-200 rounded-full text-[10px] font-mono">
                        {{ tag }}
                      </span>
                    </div>
                  </div>
                </div>
                <!-- Fallback flat VOLATILE -->
                <div v-else-if="inversionVolatileTags.length" class="space-y-1">
                  <p class="text-[10px] text-orange-400/80 font-semibold">{{ $t('inspire.volatile') }}</p>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="tag in inversionVolatileTags" :key="tag"
                      class="px-2 py-0.5 bg-orange-900/50 border border-orange-700/40 text-orange-200 rounded-full text-[10px] font-mono">
                      {{ tag }}
                    </span>
                  </div>
                </div>
                <!-- VLM classification log (Phase B: unknown tag results) -->
                <div v-if="Object.keys(inversionLlmClassification).length"
                  class="border-t border-gray-700/30 pt-1.5 space-y-1">
                  <p class="text-[9px] text-gray-600/70 font-semibold uppercase tracking-wider">{{ $t('inspire.vlmClassification') }}</p>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="(axis, tag) in inversionLlmClassification" :key="tag"
                      class="px-1.5 py-0.5 bg-gray-800/60 border border-gray-700/40 rounded text-[9px] font-mono text-gray-400">
                      {{ tag }} → {{ $t(`inspire.axes.${axis}`, axis) }}
                    </span>
                  </div>
                </div>
                <!-- Inverted world (integrated inside the tag classification card) -->
                <div v-if="inversionNewTags.length" class="border-t border-gray-700/40 pt-2 space-y-2">
                  <p class="text-[10px] text-sky-400/80 font-semibold">{{ $t('inspire.invertedWorld') }}</p>
                  <div v-if="Object.keys(inversionNewTagsGrouped).length" class="space-y-2">
                    <div v-for="(tags, axis) in inversionNewTagsGrouped" :key="axis" class="space-y-0.5">
                      <p class="text-[9px] text-sky-600/80 font-semibold uppercase tracking-wider">
                        {{ $t(`inspire.axes.${axis}`, axis) }}</p>
                      <div class="flex flex-wrap gap-1">
                        <span v-for="tag in tags" :key="tag"
                          class="px-2 py-0.5 bg-sky-900/60 border border-sky-700/40 text-sky-200 rounded-full text-[10px] font-mono">
                          {{ tag }}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div v-else class="flex flex-wrap gap-1">
                    <span v-for="tag in inversionNewTags" :key="tag"
                      class="px-2 py-0.5 bg-sky-900/60 border border-sky-700/40 text-sky-200 rounded-full text-[10px] font-mono">
                      {{ tag }}
                    </span>
                  </div>
                </div>
              </div>

              <!-- STEP2 VLM raw output (pre-filter, standalone card) -->
              <div id="inversion-step2-raw-card" v-if="inversionNewTags.length"
                class="bg-indigo-950/30 border border-indigo-800/30 rounded-xl p-3.5 space-y-2">
                <p class="text-xs font-semibold text-indigo-400 uppercase tracking-wide">{{ $t('inspire.step2Raw') }}</p>
                <template v-if="Object.keys(inversionStep2RawResult).length">
                  <div v-for="(tags, axis) in inversionStep2RawResult" :key="axis" class="space-y-0.5">
                    <p class="text-[9px] text-indigo-500/80 font-semibold uppercase tracking-wider">
                      {{ $t(`inspire.axes.${axis}`, axis) }}</p>
                    <div class="flex flex-wrap gap-1">
                      <span v-for="tag in tags" :key="tag"
                        class="px-2 py-0.5 bg-indigo-900/50 border border-indigo-700/40 text-indigo-200 rounded-full text-[10px] font-mono">
                        {{ tag }}
                      </span>
                    </div>
                  </div>
                </template>
                <p v-else class="text-[10px] text-indigo-600/60">{{ $t('inspire.noData') }}</p>
              </div>

              <!-- Step3: Context story (streaming or final) -->
              <div v-if="inspireInversionStory || inversionStoryStreaming"
                class="bg-violet-950/40 border border-violet-800/30 rounded-xl p-3.5 space-y-2">
                <p class="text-xs font-semibold text-violet-400 uppercase tracking-wide">
                  {{ $t('inspire.sceneTitle') }}
                  <span v-if="inversionStoryStreaming && !inspireInversionStory"
                    class="ml-1.5 text-violet-500 animate-pulse">{{ $t('inspire.storyGenerating') }}</span>
                </p>
                <p class="text-xs text-violet-200 leading-relaxed max-h-40 overflow-y-auto whitespace-pre-wrap">{{ inspireInversionStory || inversionStoryStreaming }}</p>
              </div>

              <!-- Removed tags report (forbidden words) -->
              <div v-if="inversionRemovedTags.length"
                class="bg-amber-950/20 border border-amber-800/30 rounded-xl p-3.5 space-y-1.5">
                <p class="text-[10px] text-amber-400 font-semibold uppercase tracking-wide">{{ $t('refine.removedTagsLabel') }}</p>
                <p class="text-[10px] text-amber-200/50 leading-relaxed">{{ $t('refine.removedTagsHint') }}</p>
                <div class="flex flex-wrap gap-1">
                  <span v-for="tag in inversionRemovedTags" :key="tag"
                    class="px-2 py-0.5 bg-amber-900/40 border border-amber-700/30 text-amber-300/80 rounded-full text-[10px] font-mono line-through">
                    {{ tag }}
                  </span>
                </div>
              </div>

              <!-- Final tags card -->
              <div v-if="inspireInversionTags.length"
                class="bg-cyan-950/40 border border-cyan-800/30 rounded-xl p-3.5 space-y-2">
                <!-- Header + view toggle -->
                <div class="flex items-center justify-between">
                  <p class="text-xs font-semibold text-cyan-400 uppercase tracking-wide">{{ $t('inspire.inversionTags') }}</p>
                  <div class="flex gap-1">
                    <button v-for="v in [['both', $t('inspire.formatBoth')],['danbooru','Danbooru'],['nl', $t('inspire.formatNl')]]"
                      :key="v[0]"
                      @click="inversionPromptView = v[0]"
                      :class="['px-2 py-0.5 rounded text-[10px] transition-colors',
                               inversionPromptView === v[0] ? 'bg-cyan-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600']">
                      {{ v[1] }}
                    </button>
                  </div>
                </div>
                <!-- Danbooru tags (inversion=blue, atmosphere=purple, FIXED=gray) -->
                <template v-if="inversionPromptView !== 'nl'">
                  <div class="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
                    <span v-for="tag in inspireInversionTags" :key="tag"
                      :class="newTagsSet.has(tag)
                        ? 'bg-sky-900/60 border-sky-600/50 text-sky-200'
                        : atmosphereTagsSet.has(tag)
                          ? 'bg-violet-900/50 border-violet-600/40 text-violet-200'
                          : 'bg-gray-800/60 border-gray-600/40 text-gray-300'"
                      class="px-2 py-0.5 rounded-full text-xs font-mono border">
                      {{ tag }}
                    </span>
                  </div>
                  <p class="text-[10px] text-cyan-600">{{ inspireInversionTags.length }} tags</p>
                </template>
                <!-- Natural language prompt -->
                <template v-if="inversionPromptView !== 'danbooru' && inspireInversionTagsNl">
                  <div :class="['rounded-lg p-2.5 text-xs leading-relaxed text-indigo-200 bg-indigo-950/40 border border-indigo-700/30',
                                inversionPromptView === 'both' ? 'mt-1' : '']">
                    {{ inspireInversionTagsNl }}
                  </div>
                </template>
                <!-- Atmosphere tags -->
                <div v-if="inversionAtmosphereTags.length" class="pt-1 border-t border-cyan-800/20 space-y-1.5">
                  <p class="text-[10px] font-semibold text-teal-400/70 uppercase tracking-wide">{{ $t('inspire.atmosphereTags') }}</p>
                  <div class="flex flex-wrap gap-1">
                    <span v-for="tag in inversionAtmosphereTags" :key="tag"
                      class="px-1.5 py-0.5 bg-teal-900/40 border border-teal-700/30 text-teal-300/80 rounded-full text-[10px] font-mono">
                      {{ tag }}
                    </span>
                  </div>
                </div>
                <!-- Negative tags -->
                <div v-if="inspireInversionNegativeTags.length" class="pt-1 border-t border-cyan-800/20 space-y-1.5">
                  <p class="text-[10px] font-semibold text-red-400/70 uppercase tracking-wide">{{ $t('inspire.inversionNegativeTags') }}</p>
                  <div class="flex flex-wrap gap-1 max-h-20 overflow-y-auto">
                    <span v-for="tag in inspireInversionNegativeTags" :key="tag"
                      class="px-1.5 py-0.5 bg-red-900/40 border border-red-700/30 text-red-300/80 rounded-full text-[10px] font-mono">
                      {{ tag }}
                    </span>
                  </div>
                </div>
                <button @click="sendToRefineDirectly(
                    inversionPromptView === 'nl'
                      ? inspireInversionTagsNl
                      : inversionPromptView === 'both' && inspireInversionTagsNl
                        ? inspireInversionTags.join(', ') + '\n\n' + inspireInversionTagsNl
                        : inspireInversionTags.join(', '),
                    'inversion-tags',
                    inspireInversionNegativeTags.join(', '))"
                  class="w-full py-2 rounded-lg text-xs font-semibold bg-cyan-700/60 hover:bg-cyan-600/70
                         border border-cyan-600/40 text-cyan-100 transition-all active:scale-[0.98]">
                  🪞 {{ $t('inspire.inversionSendDirectTags') }}
                </button>
              </div>

              <!-- Divider -->
              <div class="border-t border-gray-800/60"></div>

              <!-- Brainstorm -->
              <div class="space-y-3">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ $t('inspire.brainstormTitle') }}</p>

                <div class="rounded-xl border p-3 space-y-2 transition-colors duration-200"
                  :class="{
                    'bg-emerald-950/30 border-emerald-800/30': brainstormBasis.color === 'emerald',
                    'bg-blue-950/30 border-blue-800/30': brainstormBasis.color === 'blue',
                    'bg-indigo-950/30 border-indigo-800/30': brainstormBasis.color === 'indigo',
                    'bg-gray-800/30 border-gray-700/30': brainstormBasis.color === 'gray',
                  }">
                  <div class="flex items-start gap-2">
                    <span class="text-xs mt-0.5"
                      :class="{
                        'text-emerald-400': brainstormBasis.color === 'emerald',
                        'text-blue-400': brainstormBasis.color === 'blue',
                        'text-indigo-400': brainstormBasis.color === 'indigo',
                        'text-gray-500': brainstormBasis.color === 'gray',
                      }">●</span>
                    <div class="min-w-0">
                      <p class="text-xs font-medium leading-tight"
                        :class="{
                          'text-emerald-300': brainstormBasis.color === 'emerald',
                          'text-blue-300': brainstormBasis.color === 'blue',
                          'text-indigo-300': brainstormBasis.color === 'indigo',
                          'text-gray-400': brainstormBasis.color === 'gray',
                        }">{{ brainstormBasis.label }}</p>
                      <p class="text-[11px] text-gray-600 mt-0.5 leading-tight">{{ brainstormBasis.sublabel }}</p>
                    </div>
                  </div>
                  <div class="flex flex-wrap gap-1.5">
                    <img v-for="sha in brainstormBasis.sha256s.slice(0, 8)" :key="sha"
                      :src="`/api/thumbnails/${sha}.webp`"
                      class="w-9 h-9 rounded-lg object-cover ring-1 ring-gray-700/60" />
                    <span v-if="brainstormBasis.sha256s.length > 8"
                      class="w-9 h-9 rounded-lg bg-gray-700/40 flex items-center justify-center text-[10px] text-gray-500 ring-1 ring-gray-700/40">
                      +{{ brainstormBasis.sha256s.length - 8 }}
                    </span>
                  </div>
                  <!-- Conversion flow -->
                  <div class="flex items-center gap-1 flex-wrap">
                    <span class="px-1.5 py-0.5 bg-gray-700/60 border border-gray-600/40 rounded text-[9px] text-gray-400">{{ $t('inspire.brainstormImages', { n: brainstormBasis.sha256s.length }) }}</span>
                    <span class="text-gray-600 text-[9px]">→</span>
                    <span class="px-1.5 py-0.5 bg-purple-900/50 border border-purple-700/40 rounded text-[9px] text-purple-300">{{ $t('inspire.pipeline.wd14Extract') }}</span>
                    <span class="text-gray-600 text-[9px]">→</span>
                    <span class="px-1.5 py-0.5 bg-green-900/50 border border-green-700/40 rounded text-[9px] text-green-300">{{ $t('inspire.sendToLlm') }}</span>
                  </div>
                  <p class="text-[10px] text-gray-600 leading-relaxed">{{ $t('inspire.brainstormHint') }}</p>
                  <div v-if="inspireAnomalyTags.length" class="flex flex-wrap gap-1">
                    <span class="text-[11px] text-orange-500/70 self-center">{{ $t('inspire.anomalyPrefix') }}</span>
                    <span v-for="tag in inspireAnomalyTags" :key="tag"
                      class="px-1.5 py-0.5 bg-orange-900/40 text-orange-300 rounded text-[11px] font-mono">{{ tag }}</span>
                  </div>
                </div>

                <button @click="runBrainstorm"
                  :disabled="brainstormLoading || inspireSlots.length === 0"
                  class="w-full py-2.5 rounded-xl text-sm font-medium transition-all duration-150 disabled:opacity-40 flex items-center justify-center gap-2 border"
                  :class="brainstormLoading
                    ? 'bg-blue-900/40 text-blue-300 border-blue-700/40 cursor-wait'
                    : 'bg-blue-900/40 hover:bg-blue-800/60 border-blue-700/40 hover:border-blue-600/60 text-blue-300 hover:text-blue-100'">
                  <svg v-if="brainstormLoading" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  <span>{{ brainstormLoading ? $t('inspire.brainstormRunning') : $t('inspire.brainstormRun') }}</span>
                </button>

                <!-- Cancel button (brainstorm running) -->
                <button v-if="brainstormLoading && brainstormJobId"
                  @click="resetSession(props.initialSlots || [])"
                  class="w-full py-2 rounded-xl text-sm font-semibold transition-all duration-150 border border-red-700/40 text-red-400 hover:bg-red-900/30 hover:text-red-300">
                  {{ $t('inspire.cancel') }}
                </button>
              </div>

            </div>
          </div>

          <!-- ── Right pane: Results ── -->
          <div class="flex-1 flex flex-col min-h-0 overflow-y-auto p-5">

            <!-- Brainstorm view -->
            <template v-if="inspireRightView === 'brainstorm'">
              <div class="flex items-center justify-between mb-4 flex-shrink-0">
                <p class="text-sm font-semibold text-gray-200">{{ $t('inspire.brainstormResult') }}</p>
                <div class="flex items-center gap-2">
                  <button v-if="brainstormText" @click="copyToClipboard(brainstormText)"
                    class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-xs text-gray-300 transition-colors">
                    {{ $t('inspire.copy') }}
                  </button>
                  <button @click="inspireRightView = 'results'"
                    class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-xs text-gray-400 hover:text-gray-200 transition-colors">
                    {{ $t('inspire.toResults') }}
                  </button>
                </div>
              </div>
              <div v-if="brainstormLoading && !brainstormText"
                class="bg-gray-800/50 border border-gray-700/40 rounded-xl p-5 flex-1 overflow-y-auto">
                <div v-html="simpleMarkdown(brainstormStreaming)"></div>
                <span class="inline-block w-1.5 h-4 bg-blue-400 rounded-sm ml-0.5 align-middle mt-1"></span>
              </div>

              <div v-else-if="brainstormText" class="flex-1 overflow-y-auto space-y-3">
                <template v-if="parseBrainstormSections(brainstormText).length">
                  <div v-for="(sec, idx) in parseBrainstormSections(brainstormText)" :key="idx"
                    class="bg-gray-800/60 border border-gray-700/40 hover:border-purple-500/30 rounded-xl overflow-hidden transition-colors duration-150">
                    <div class="flex items-center justify-between px-4 py-3 bg-gray-800/80 border-b border-gray-700/40">
                      <p class="text-sm font-semibold text-purple-200 leading-tight">{{ sec.title }}</p>
                      <button
                        @click="sendToRefine(sec.title + '\n' + sec.body)"
                        class="flex items-center gap-1.5 px-3 py-1.5 bg-purple-700/60 hover:bg-purple-600/80 border border-purple-500/40 hover:border-purple-400/60 rounded-lg text-xs font-medium text-purple-100 transition-all duration-150 whitespace-nowrap flex-shrink-0 ml-3 active:scale-95">
                        {{ $t('inspire.refineThis') }}
                      </button>
                    </div>
                    <div class="px-4 py-3">
                      <div v-html="simpleMarkdown(sec.body.trim())"></div>
                    </div>
                  </div>
                </template>
                <div v-else
                  class="bg-gray-800/50 border border-gray-700/40 rounded-xl p-5">
                  <div v-html="simpleMarkdown(brainstormText)"></div>
                  <button
                    @click="sendToRefine(brainstormText)"
                    class="mt-4 flex items-center gap-1.5 px-4 py-2 bg-purple-700/60 hover:bg-purple-600/80 border border-purple-500/40 rounded-lg text-xs font-medium text-purple-100 transition-all duration-150 active:scale-95">
                    {{ $t('inspire.refineAll') }}
                  </button>
                </div>
              </div>
              <div v-else class="flex items-center justify-center flex-1">
                <div class="text-center space-y-3">
                  <div class="w-14 h-14 rounded-2xl bg-blue-900/30 border border-blue-700/30 flex items-center justify-center mx-auto">
                    <svg class="w-7 h-7 text-blue-400 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                    </svg>
                  </div>
                  <p class="text-sm text-blue-300">{{ $t('inspire.thinking') }}</p>
                </div>
              </div>
            </template>

            <!-- Results view -->
            <template v-else>

              <div v-if="inspireError"
                class="mb-4 px-4 py-3 bg-red-950/50 border border-red-800/40 rounded-xl text-sm text-red-300 flex-shrink-0">
                {{ inspireError }}
              </div>

              <!-- Morph timeline -->
              <template v-if="inspireTab === 'morph' && inspireMorphTimeline.length">
                <div class="flex items-center gap-3 mb-4 flex-shrink-0">
                  <div class="flex items-center gap-2">
                    <img :src="`/api/thumbnails/${morphSlotA}.webp`"
                      class="w-10 h-10 rounded-xl object-cover ring-2 ring-blue-500/80 flex-shrink-0" />
                    <span class="text-xs text-blue-400 font-semibold">A {{ $t('inspire.morphA') }}</span>
                  </div>
                  <div class="flex-1 relative h-px bg-gradient-to-r from-blue-500/40 via-indigo-500/40 to-orange-500/40">
                    <div class="absolute inset-0 flex items-center justify-around">
                      <div v-for="step in inspireMorphTimeline" :key="step.t"
                        class="w-1.5 h-1.5 rounded-full bg-indigo-400/70"></div>
                    </div>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-xs text-orange-400 font-semibold">B {{ $t('inspire.morphB') }}</span>
                    <img :src="`/api/thumbnails/${morphSlotB}.webp`"
                      class="w-10 h-10 rounded-xl object-cover ring-2 ring-orange-500/80 flex-shrink-0" />
                  </div>
                </div>
                <div class="space-y-5 overflow-y-auto flex-1">
                  <div v-for="step in inspireMorphTimeline" :key="step.t">
                    <div class="flex items-center gap-2 mb-2">
                      <span class="text-[11px] font-mono text-indigo-400 bg-indigo-900/30 px-2 py-0.5 rounded-full border border-indigo-700/40">
                        t = {{ step.t }}
                      </span>
                      <div class="flex-1 h-px bg-gray-800/60"></div>
                    </div>
                    <div class="grid grid-cols-4 gap-2">
                      <div v-for="img in step.results" :key="img.sha256"
                        class="cursor-pointer group rounded-xl overflow-hidden bg-gray-800 ring-1 ring-gray-700/60 hover:ring-indigo-500/60 transition-all duration-150"
                        @click="emit('select-image', img)">
                        <div class="relative bg-gray-800 h-28 overflow-hidden">
                          <img :src="`/api/thumbnails/${img.sha256}.webp`" :alt="img.name"
                            class="w-full h-full object-contain transition-transform duration-200 group-hover:scale-[1.05]"
                            loading="lazy" />
                          <button
                            @click.stop="emit('toggle-image-selection', img, $event.currentTarget)"
                            class="absolute top-1.5 left-1.5 w-5 h-5 rounded-full flex items-center justify-center transition-all duration-150 cursor-pointer z-10"
                            :class="selectedSet.has(img.sha256) ? 'bg-purple-500 border-2 border-purple-300 opacity-100' : 'bg-black/50 border-2 border-gray-400/70 opacity-0 group-hover:opacity-100'">
                            <svg v-if="selectedSet.has(img.sha256)" class="w-2.5 h-2.5 text-white" viewBox="0 0 12 12" fill="none">
                              <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                          </button>
                        </div>
                        <div class="px-1.5 py-1">
                          <p class="text-[10px] text-gray-500 truncate">{{ img.name }}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- Grouped results -->
              <template v-else-if="inspireTab === 'grouped_search' && inspireGroupedResults.length">
                <p class="text-xs text-gray-400 font-medium mb-3 flex-shrink-0">{{ $t('inspire.groupCount', { n: inspireGroupedResults.length }) }}</p>
                <div class="space-y-4 overflow-y-auto flex-1">
                  <div v-for="group in inspireGroupedResults" :key="group.group_id">
                    <div class="flex items-center gap-2 mb-2">
                      <span class="text-[11px] font-medium text-indigo-300 bg-indigo-900/30 px-2.5 py-1 rounded-full border border-indigo-700/40 truncate max-w-[200px]">
                        {{ group.group_id || $t('inspire.groupUnset') }}
                      </span>
                      <div class="flex-1 h-px bg-gray-800/60"></div>
                    </div>
                    <div class="grid grid-cols-3 gap-2">
                      <div v-for="img in group.hits" :key="img.sha256"
                        class="cursor-pointer group rounded-xl overflow-hidden bg-gray-800 ring-1 ring-gray-700/60 hover:ring-indigo-500/60 transition-all duration-150"
                        @click="emit('select-image', img)">
                        <div class="relative bg-gray-800 h-24 overflow-hidden">
                          <img :src="`/api/thumbnails/${img.sha256}.webp`" :alt="img.name"
                            class="w-full h-full object-contain transition-transform duration-200 group-hover:scale-[1.05]"
                            loading="lazy" />
                          <button
                            @click.stop="emit('toggle-image-selection', img, $event.currentTarget)"
                            class="absolute top-1.5 left-1.5 w-5 h-5 rounded-full flex items-center justify-center transition-all duration-150 cursor-pointer z-10"
                            :class="selectedSet.has(img.sha256) ? 'bg-purple-500 border-2 border-purple-300 opacity-100' : 'bg-black/50 border-2 border-gray-400/70 opacity-0 group-hover:opacity-100'">
                            <svg v-if="selectedSet.has(img.sha256)" class="w-2.5 h-2.5 text-white" viewBox="0 0 12 12" fill="none">
                              <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                          </button>
                        </div>
                        <div class="px-1.5 py-1">
                          <p class="text-[10px] text-gray-500 truncate">{{ img.name }}</p>
                          <p class="text-[10px] text-indigo-400/60 font-mono">{{ img._score }}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </template>

              <!-- Flat results grid -->
              <template v-else-if="inspireResults.length">
                <div class="flex items-center justify-between mb-3 flex-shrink-0">
                  <div class="flex items-center gap-2">
                    <p class="text-xs text-gray-400 font-medium">{{ $t('inspire.resultCount', { n: inspireResults.length }) }}</p>
                    </div>
                  <p class="text-xs text-gray-600">
                    <span v-if="inspireTab === 'serendipity'">{{ $t('inspire.descSerendipity') }}</span>
                    <span v-else-if="inspireTab === 'arithmetic'">{{ $t('inspire.descArithmetic') }}</span>
                    <span v-else-if="inspireTab === 'anomaly'">{{ $t('inspire.descAnomaly') }}</span>
                    <span v-else-if="inspireTab === 'inversion'">{{ $t('inspire.descInversion') }}</span>
                  </p>
                </div>
                <div class="grid grid-cols-3 sm:grid-cols-4 gap-2 overflow-y-auto flex-1 content-start">
                  <div v-for="img in inspireResults" :key="img.sha256"
                    class="cursor-pointer group rounded-xl overflow-hidden bg-gray-800 ring-1 ring-gray-700/60 hover:ring-indigo-500/60 hover:shadow-lg hover:shadow-indigo-900/30 transition-all duration-150"
                    @click="emit('select-image', img)">
                    <div class="relative bg-gray-800 h-36 overflow-hidden">
                      <img :src="`/api/thumbnails/${img.sha256}.webp`" :alt="img.name"
                        class="w-full h-full object-contain transition-transform duration-200 group-hover:scale-[1.05]"
                        loading="lazy" />
                      <div class="absolute inset-0 bg-indigo-500/0 group-hover:bg-indigo-500/5 transition-colors duration-150 pointer-events-none"></div>
                      <!-- Select badge -->
                      <button
                        :disabled="isResultSelectionTab && selectedSet.has(img.sha256)"
                        @click.stop="isResultSelectionTab
                          ? toggleInspireResultSelection(img.sha256)
                          : emit('toggle-image-selection', img, $event.currentTarget)"
                        class="absolute top-1.5 left-1.5 w-5 h-5 rounded-full flex items-center justify-center transition-all duration-150 z-10"
                        :class="isResultSelectionTab
                          ? (selectedSet.has(img.sha256)
                              ? 'bg-gray-600/70 border-2 border-gray-500/60 opacity-60 cursor-not-allowed'
                              : (inspireResultSelection.has(img.sha256)
                                  ? 'bg-indigo-500 border-2 border-indigo-300 opacity-100 cursor-pointer'
                                  : 'bg-black/50 border-2 border-gray-400/70 opacity-40 group-hover:opacity-100 cursor-pointer'))
                          : (selectedSet.has(img.sha256)
                              ? 'bg-purple-500 border-2 border-purple-300 opacity-100 cursor-pointer'
                              : 'bg-black/50 border-2 border-gray-400/70 opacity-40 group-hover:opacity-100 cursor-pointer')">
                        <svg v-if="isResultSelectionTab
                          ? (selectedSet.has(img.sha256) || inspireResultSelection.has(img.sha256))
                          : selectedSet.has(img.sha256)"
                          class="w-2.5 h-2.5 text-white" viewBox="0 0 12 12" fill="none">
                          <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                      </button>
                    </div>
                    <div class="px-2 py-1.5">
                      <p class="text-[11px] text-gray-400 truncate leading-tight">{{ img.name }}</p>
                      <p v-if="img._score != null" class="text-[10px] text-indigo-400/70 font-mono leading-tight mt-0.5">score {{ img._score }}</p>
                    </div>
                  </div>
                </div>
              </template>

              <!-- Loading skeleton / inversion stage progress -->
              <template v-else-if="inspireLoading">
                <template v-if="inspireTab === 'inversion' && inversionStage > 0">
                  <div class="flex gap-4 flex-1 py-6 px-2">
                    <!-- Progress steps -->
                    <div class="space-y-2 w-56 flex-shrink-0">
                      <div v-for="(step, idx) in [
                        { label: $t('inspire.inversionStage0'), stageNum: 0 },
                        { label: $t('inspire.inversionStage1'), stageNum: 1 },
                        { label: $t('inspire.inversionStage2'), stageNum: 2 },
                        { label: $t('inspire.inversionStage3'), stageNum: 3 },
                        { label: $t('inspire.inversionStage4'), stageNum: 4 },
                        { label: $t('inspire.inversionStage5'), stageNum: 5 },
                      ]" :key="idx"
                        class="flex items-center gap-3 px-3 py-2.5 rounded-xl border transition-all duration-300"
                        :class="inversionStage > step.stageNum
                          ? 'bg-cyan-950/40 border-cyan-800/40 text-cyan-500'
                          : inversionStage === step.stageNum
                            ? 'bg-gray-800/80 border-cyan-600/50 text-gray-200 shadow-sm shadow-cyan-900/30'
                            : 'bg-gray-900/30 border-gray-800/40 text-gray-600'">
                        <span class="text-base flex-shrink-0 w-5 text-center">
                          {{ inversionStage > step.stageNum ? '✓' : inversionStage === step.stageNum ? '⏳' : '○' }}
                        </span>
                        <div class="min-w-0 flex-1">
                          <p class="text-xs font-medium truncate">{{ step.label }}</p>
                          <p v-if="inversionStage === step.stageNum" class="text-[10px] text-cyan-500 mt-0.5 truncate">{{ inversionStageLabel }}</p>
                        </div>
                      </div>
                    </div>
                    <!-- Story streaming panel -->
                    <div v-if="inversionStoryStreaming" class="flex-1 min-w-0 rounded-xl border border-violet-700/40 bg-violet-950/30 p-3 overflow-y-auto max-h-56">
                      <p class="text-[10px] text-violet-400 font-semibold mb-1.5 flex items-center gap-1">
                        <span class="inline-block w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse"></span>
                        {{ $t('inspire.storyGenerating') }}
                      </p>
                      <p class="text-xs text-violet-200 leading-relaxed whitespace-pre-wrap">{{ inversionStoryStreaming }}</p>
                    </div>
                    <!-- Placeholder when no story yet -->
                    <div v-else class="flex-1 rounded-xl border border-gray-800/40 bg-gray-900/20 flex items-center justify-center">
                      <p class="text-[10px] text-gray-600">{{ $t('inspire.emptyInversion') }}</p>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <div class="grid grid-cols-3 sm:grid-cols-4 gap-2">
                    <div v-for="i in 12" :key="`sk-${i}`" class="rounded-xl overflow-hidden bg-gray-800 ring-1 ring-gray-700/40">
                      <div class="bg-gray-700/60 animate-pulse h-36"></div>
                      <div class="px-2 py-1.5">
                        <div class="h-2 bg-gray-700 animate-pulse rounded w-3/4"></div>
                      </div>
                    </div>
                  </div>
                </template>
              </template>

              <!-- Empty state -->
              <template v-else>
                <div class="flex items-start justify-center flex-1 text-left py-8 px-2 overflow-y-auto">
                  <div class="space-y-4 w-full max-w-sm">
                    <!-- Icon + title -->
                    <div class="flex items-center gap-3">
                      <div class="w-12 h-12 rounded-2xl bg-gray-800/80 border border-gray-700/50 flex items-center justify-center flex-shrink-0 text-2xl">
                        {{ inspireTab === 'serendipity' ? '✨' : inspireTab === 'arithmetic' ? '⚗️' : inspireTab === 'morph' ? '🌊' : inspireTab === 'inversion' ? '🪞' : inspireTab === 'blend' ? '⚖️' : inspireTab === 'outlier' ? '🌌' : inspireTab === 'discover' ? '🧭' : inspireTab === 'grouped_search' ? '🗂️' : '⚡' }}
                      </div>
                      <div>
                        <p class="text-sm font-semibold text-gray-300">{{ $t('inspire.emptyTitle') }}</p>
                        <p class="text-[11px] text-gray-600 mt-0.5">{{ $t('inspire.openHint') }}</p>
                      </div>
                    </div>

                    <!-- Pipeline -->
                    <div class="bg-gray-800/50 rounded-xl px-3.5 py-3 border border-gray-700/40">
                      <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2.5">{{ $t('inspire.pipelineFlow') }}</p>
                      <div class="flex items-center gap-1.5 flex-wrap">
                        <template v-for="([label, type], i) in pipelineSteps" :key="i">
                          <span class="px-2 py-1 rounded border text-[10px] font-medium leading-none"
                            :class="PIPELINE_CHIP_CLASS[type]">{{ label }}</span>
                          <span v-if="i < pipelineSteps.length - 1" class="text-gray-600 text-[10px]">→</span>
                        </template>
                      </div>
                    </div>

                    <!-- What you discover -->
                    <div class="space-y-1">
                      <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">{{ $t('inspire.discoverableImages') }}</p>
                      <p class="text-xs text-gray-400 leading-relaxed">{{ $t(`inspire.helpWhat.${inspireTab}`) }}</p>
                    </div>

                    <!-- Tip -->
                    <div class="border-l-2 border-gray-700/60 pl-3">
                      <p class="text-[11px] text-gray-600 leading-relaxed">{{ $t(`inspire.helpTip.${inspireTab}`) }}</p>
                    </div>
                  </div>
                </div>
              </template>

            </template>

          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

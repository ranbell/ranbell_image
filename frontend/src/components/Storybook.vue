<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { EMOTION_DIMENSIONS } from '../composables/useInvokeSession.js'
import SbIcon from './SbIcon.vue'
import StoryQualityRadar from './StoryQualityRadar.vue'
import TimeScrubPolaroids from './TimeScrubPolaroids.vue'

const { t, locale } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['update:show', 'select-image', 'weave-from', 'send-to-refine-direct', 'toast'])

const AXES = ['past', 'present', 'future']
const TIME_SCALES = ['minutes', 'tens_of_minutes', 'hours', 'days', 'months', 'years', 'decades']
const VIEW_MODES = ['gallery', 'detail', 'timeline', 'moodboard']
const SORTS = ['newest', 'oldest', 'title', 'time_scale', 'quality']
const QUALITY_WEAK = 0.55
const QUALITY_ACTION_DIMS = [
  'topic_fit', 'diversity', 'expression', 'action', 'drawability', 'identity', 'richness',
]

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

function axisHasVisualSpec(axisData) {
  if (!axisData) return false
  if (axisData.visual_script) return true
  return CAT_TAG_GROUPS.some(g => (axisData[g.key] || []).length > 0)
}

// ── data ──────────────────────────────────────────────────────────────────────
const stories = ref([])
const loading = ref(false)
const lang = ref(locale.value?.startsWith('ja') ? 'ja' : 'en')
watch(locale, (l) => { lang.value = l?.startsWith('ja') ? 'ja' : 'en' })

const _saved = (() => { try { return JSON.parse(localStorage.getItem('storybook.ui') || '{}') } catch { return {} } })()
const viewMode = ref(VIEW_MODES.includes(_saved.viewMode) ? _saved.viewMode : 'gallery')
const query = ref(_saved.query || '')
const sort = ref(SORTS.includes(_saved.sort) ? _saved.sort : 'newest')
const filters = ref({
  base_axis: _saved.filters?.base_axis || '',
  time_scale: _saved.filters?.time_scale || '',
  emotion: _saved.filters?.emotion || '',
  qualityMin: _saved.filters?.qualityMin ?? '',
})

watch([viewMode, query, sort, filters], () => {
  try {
    localStorage.setItem('storybook.ui', JSON.stringify({
      viewMode: viewMode.value, query: query.value, sort: sort.value,
      filters: filters.value,
    }))
  } catch {}
}, { deep: true })

function storyTitle(story) {
  return (lang.value === 'ja' && story.title_ja) ? story.title_ja : (story.title || '')
}
function storyOverall(story) {
  return (lang.value === 'ja' && story.overall_story_ja)
    ? story.overall_story_ja : (story.overall_story || '')
}
function axisStory(story, axis) {
  const a = story.axes?.[axis] || {}
  return (lang.value === 'ja' && a.story_ja) ? a.story_ja : (a.story || '')
}
function storyBio(story) {
  const ja = story.biography_ja
  const bio = (lang.value === 'ja' && ja && Object.keys(ja).length) ? ja : story.biography
  return (bio && Object.keys(bio).length) ? bio : null
}
function storyTimetable(story) {
  const ja = story.timetable_ja
  const tt = (lang.value === 'ja' && ja && ja.length) ? ja : story.timetable
  return Array.isArray(tt) ? tt : []
}
const BIO_LIST_FIELDS = ['hobbies', 'favourite_items', 'likes', 'dislikes', 'quirks']
function asStringList(v) {
  if (Array.isArray(v)) return v.map(x => String(x ?? '').trim()).filter(Boolean)
  if (typeof v === 'string' && v.trim()) return [v.trim()]
  return []
}
function joinList(v) {
  return asStringList(v).join(t('storybook.listSep'))
}

function qualityDraftNote(story) {
  const q = story?.quality_eval
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
  return q.notes?.draft_grounding ? String(q.notes.draft_grounding) : ''
}

function storyBody(story) {
  return story?.context?.body || {}
}
function isTopicOnly(story) {
  return !!(story?.context?.topic_only) || !story?.base_image_id
}
function hasWeaveSettings(story) {
  if (!story) return false
  const body = storyBody(story)
  return !!(
    isTopicOnly(story) ||
    typeof story.divergence === 'number' ||
    (story.mutation_tags || []).length ||
    body.tone ||
    body.dramatic_mode ||
    body.use_draft_refine ||
    body.prompt_style ||
    body.prose_paragraphs ||
    story.time_scale ||
    story.emotion ||
    story.base_model_name ||
    story.workflow_name
  )
}
function qualityPct(story) {
  const o = story?.quality_eval?.overall
  if (o == null || Number.isNaN(Number(o))) return null
  return Math.round(Math.max(0, Math.min(1, Number(o))) * 100)
}
function draftRichnessFor(story, axis) {
  return story?.quality_eval?.per_axis?.draft_richness?.[axis] || null
}
function axisSlotLabel(story, axis) {
  const slot = story?.context?.axis_slots?.[axis]
  if (!slot || typeof slot !== 'object') return ''
  const bits = [slot.label, slot.activity, slot.place].filter(Boolean)
  return bits.join(' · ')
}
function dramaticModeLabel(mode) {
  if (!mode) return ''
  return t('chronicle.dramaticMode.' + mode, mode)
}
function formatDraftDelta(d) {
  if (!d || typeof d !== 'object') return ''
  const before = Number(d.before || 0).toFixed(2)
  const after = Number(d.after || 0).toFixed(2)
  const delta = Number(d.delta || 0)
  return `${before} → ${after} (${delta >= 0 ? '+' : ''}${delta.toFixed(2)})`
}
function promptStyleLabel(style) {
  if (!style) return ''
  return t('chronicle.style.' + String(style).replace('+', '_'), style)
}

function matches(story, q) {
  if (!q) return true
  const bag = [
    story.title, story.title_ja, story.overall_story, story.overall_story_ja,
    story.user_topic, story.worldview,
    ...(story.candidates || []).map(c => `${c.title} ${c.motif || c.key_motif || ''}`),
    ...AXES.flatMap(a => [story.axes?.[a]?.story, story.axes?.[a]?.story_ja]),
  ].filter(Boolean).join(' ').toLowerCase()
  return bag.includes(q.toLowerCase())
}

const visibleStories = computed(() => {
  const q = query.value.trim()
  const f = filters.value
  const qMin = f.qualityMin === '' || f.qualityMin == null ? null : Number(f.qualityMin)
  const list = stories.value.filter(s => {
    if (f.base_axis && s.base_time_axis !== f.base_axis) return false
    if (f.time_scale && s.time_scale !== f.time_scale) return false
    if (f.emotion && s.emotion !== f.emotion) return false
    if (qMin != null && !Number.isNaN(qMin)) {
      const pct = qualityPct(s)
      if (pct == null || pct < qMin) return false
    }
    if (!matches(s, q)) return false
    return true
  })
  const cmpTitle = (a, b) => (storyTitle(a) || '').localeCompare(storyTitle(b) || '', lang.value)
  const cmpScale = (a, b) => TIME_SCALES.indexOf(a.time_scale) - TIME_SCALES.indexOf(b.time_scale)
  const cmpQuality = (a, b) => {
    const qa = qualityPct(a)
    const qb = qualityPct(b)
    if (qa == null && qb == null) return 0
    if (qa == null) return 1
    if (qb == null) return -1
    return qb - qa
  }
  switch (sort.value) {
    case 'oldest':     return [...list].sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
    case 'title':      return [...list].sort(cmpTitle)
    case 'time_scale': return [...list].sort(cmpScale)
    case 'quality':    return [...list].sort(cmpQuality)
    default:           return [...list].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
  }
})

function bucketFor(ts) {
  const now = new Date()
  const d = new Date((ts || 0) * 1000)
  const dayMs = 86400_000
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const t0 = d.getTime()
  if (t0 >= startOfToday) return { key: 'today', label: t('storybook.bucket.today'), order: 0 }
  if (t0 >= startOfToday - dayMs) return { key: 'yesterday', label: t('storybook.bucket.yesterday'), order: 1 }
  if (t0 >= startOfToday - 7 * dayMs) return { key: 'thisWeek', label: t('storybook.bucket.thisWeek'), order: 2 }
  if (t0 >= startOfToday - 30 * dayMs) return { key: 'thisMonth', label: t('storybook.bucket.thisMonth'), order: 3 }
  const ym = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  return { key: ym, label: ym, order: 100 + (now.getFullYear() - d.getFullYear()) * 12 + (now.getMonth() - d.getMonth()) }
}

const timelineBuckets = computed(() => {
  const chrono = [...visibleStories.value].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
  const map = new Map()
  for (const s of chrono) {
    const b = bucketFor(s.created_at)
    if (!map.has(b.key)) map.set(b.key, { ...b, stories: [] })
    map.get(b.key).stories.push(s)
  }
  return [...map.values()].sort((a, b) => a.order - b.order)
})

// Dismiss guard (mirrors ChroniclePanel): swallow the click/Esc that opened the
// panel and any underlay race from an overlay beneath it for one tick.
let _backdropArmed = false
let _ignoreDismissUntil = 0

watch(() => props.show, (val) => {
  if (val) {
    _ignoreDismissUntil = performance.now() + 400
    fetchStories()
  } else {
    _backdropArmed = false
    detailStory.value = null
    pinupView.value = null
  }
})

function close() { emit('update:show', false) }
function _dismissBlocked() { return performance.now() < _ignoreDismissUntil }

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

async function fetchStories() {
  loading.value = true
  try {
    const r = await fetch('/api/story/storybook?limit=200')
    if (r.ok) stories.value = (await r.json()).stories
  } catch {}
  loading.value = false
}

function sendAxisToRefine(story, axis) {
  const ax = story?.axes?.[axis]
  if (!ax?.prompt_positive) {
    emit('toast', { msg: t('storybook.refineNeedPrompt'), type: 'error' })
    return
  }
  const sha = ax.image_id || story.base_image_id || ''
  emit('send-to-refine-direct', {
    shas: sha ? [sha] : [],
    directPrompt: ax.prompt_positive,
    directNegativePrompt: ax.prompt_negative || '',
    source: 'storybook',
    workflow_name: story.workflow_name || '',
  })
}

function axisImage(story, axis) {
  return story.axes?.[axis]?.image_id || null
}

function openImage(sha256) {
  if (sha256) emit('select-image', sha256)
}

// ── Time scrub (past ↔ present ↔ future) ────────────────────────────────────
const detailScrubIdx = ref(1)
const cardScrub = ref({}) // story_id → 0|1|2
const variantShelfOpen = ref(true)
const regenBusy = ref(new Set())

function defaultScrubFor(story) {
  const i = AXES.indexOf(story?.base_time_axis)
  return i >= 0 ? i : 1
}
function cardScrubIdx(story) {
  const v = cardScrub.value[story.story_id]
  return v == null ? defaultScrubFor(story) : v
}
function setCardScrub(storyId, idx) {
  cardScrub.value = { ...cardScrub.value, [storyId]: idx }
}

function weakestAxis(story) {
  // Prefer non-base axes with images/prompts for "another moment" play
  const ordered = [...AXES].sort((a, b) => {
    if (a === story.base_time_axis) return 1
    if (b === story.base_time_axis) return -1
    return 0
  })
  return ordered.find(a => story.axes?.[a]?.prompt_positive) || story.base_time_axis || 'present'
}

function qualityActions(story) {
  const dims = story?.quality_eval?.dimensions || {}
  const ranked = QUALITY_ACTION_DIMS
    .map((key) => ({ key, value: Number(dims[key] ?? 1) }))
    .filter((d) => d.value < QUALITY_WEAK)
    .sort((a, b) => a.value - b.value)
    .slice(0, 3)
  const actions = []
  for (const d of ranked) {
    if (d.key === 'topic_fit' || d.key === 'diversity') {
      const sha = axisImage(story, story.base_time_axis) || story.base_image_id
      if (sha) {
        actions.push({
          id: 'reweave-' + d.key,
          label: t('storybook.qualityAction.reweave'),
          tip: t('storybook.qualityAction.reweaveTip', { dim: t('storybook.quality.dim.' + d.key) }),
          run: () => emit('weave-from', sha),
        })
      }
    } else if (d.key === 'richness' || d.key === 'drawability' || d.key === 'expression' || d.key === 'action') {
      const axis = weakestAxis(story)
      if (story.axes?.[axis]?.prompt_positive) {
        actions.push({
          id: 'refine-' + d.key,
          label: t('storybook.qualityAction.refine', { axis: t('chronicle.axis.' + axis) }),
          tip: t('storybook.qualityAction.refineTip', { dim: t('storybook.quality.dim.' + d.key) }),
          run: () => sendAxisToRefine(story, axis),
        })
      }
    } else if (d.key === 'identity') {
      const axis = story.base_time_axis || 'present'
      if (story.axes?.[axis]?.prompt_positive) {
        actions.push({
          id: 'refine-identity',
          label: t('storybook.qualityAction.refineIdentity'),
          tip: t('storybook.qualityAction.refineIdentityTip'),
          run: () => sendAxisToRefine(story, axis),
        })
      }
    }
  }
  // Always offer a playful "another moment" if we have a prompt
  const playAxis = weakestAxis(story)
  if (story.axes?.[playAxis]?.prompt_positive && !actions.some(a => a.id.startsWith('refine-'))) {
    actions.push({
      id: 'play-refine',
      label: t('storybook.qualityAction.anotherMoment'),
      tip: t('storybook.qualityAction.anotherMomentTip'),
      run: () => sendAxisToRefine(story, playAxis),
    })
  }
  // Dedupe by id
  const seen = new Set()
  return actions.filter((a) => (seen.has(a.id) ? false : (seen.add(a.id), true))).slice(0, 3)
}

async function regenerateAxis(story, axis) {
  if (axis === story.base_time_axis && story.base_image_id) {
    emit('toast', { msg: t('storybook.regenBaseBlocked'), type: 'error' })
    return
  }
  const id = story.story_id
  const key = `${id}:${axis}`
  if (regenBusy.value.has(key)) return
  const before = axisImage(story, axis) || ''
  regenBusy.value = new Set([...regenBusy.value, key])
  try {
    const r = await fetch(`/api/story/${id}/regenerate/${axis}`, { method: 'POST' })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    emit('toast', { msg: t('storybook.regenQueued'), type: 'success' })
    for (let i = 0; i < 30; i++) {
      await new Promise((res) => setTimeout(res, 2000))
      const s = await refetchStory(id)
      const now = axisImage(s || story, axis) || ''
      if (now && now !== before) {
        emit('toast', { msg: t('storybook.regenDone'), type: 'success' })
        break
      }
    }
  } catch (err) {
    emit('toast', { msg: String(err.message || err), type: 'error' })
  } finally {
    const next = new Set(regenBusy.value)
    next.delete(key)
    regenBusy.value = next
  }
}

const pinupView = ref(null)
const pinupBusy = ref(new Set())
const PINUP_ROT = [-5, 4, -3, 6, -6, 3, -4, 5]
function pinupRotation(i) { return PINUP_ROT[i % PINUP_ROT.length] }
function storyPinups(story) {
  if (Array.isArray(story.pinups) && story.pinups.length) return story.pinups.filter(Boolean)
  return story.pinup_image_id ? [story.pinup_image_id] : []
}
function openPinup(sha) { if (sha) pinupView.value = sha }

const moodboardPins = computed(() => {
  const pins = []
  for (const story of visibleStories.value) {
    for (const axis of AXES) {
      const sha = axisImage(story, axis)
      if (!sha) continue
      pins.push({
        key: `${story.story_id}-${axis}`,
        sha,
        story,
        axis,
        title: storyTitle(story),
        kind: 'axis',
      })
    }
    storyPinups(story).forEach((sha, i) => {
      pins.push({
        key: `${story.story_id}-pin-${i}`,
        sha,
        story,
        axis: 'pinup',
        title: storyTitle(story),
        kind: 'pinup',
      })
    })
  }
  return pins
})

async function refetchStory(id) {
  try {
    const r = await fetch(`/api/story/${id}`)
    if (!r.ok) return null
    const s = await r.json()
    stories.value = stories.value.map(x => (x.story_id === id ? s : x))
    if (detailStory.value?.story_id === id) detailStory.value = s
    return s
  } catch { return null }
}

const detailStory = ref(null)
watch(detailStory, (s) => {
  if (s) detailScrubIdx.value = defaultScrubFor(s)
})

const detailIndex = computed(() => {
  if (!detailStory.value) return -1
  return visibleStories.value.findIndex(s => s.story_id === detailStory.value.story_id)
})

function openDetail(story) {
  detailStory.value = story
  refetchStory(story.story_id)
}

function closeDetail() { detailStory.value = null }

function detailPrev() {
  const i = detailIndex.value
  if (i > 0) openDetail(visibleStories.value[i - 1])
}

function detailNext() {
  const i = detailIndex.value
  if (i >= 0 && i < visibleStories.value.length - 1) openDetail(visibleStories.value[i + 1])
}

async function addPinup(story, mode) {
  const id = story.story_id
  const before = storyPinups(story)
  const prevLen = before.length
  const prevLast = before[before.length - 1] || ''
  pinupBusy.value = new Set([...pinupBusy.value, id])
  try {
    const r = await fetch(`/api/story/${id}/pinup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    emit('toast', { msg: t('storybook.pinupQueued'), type: 'success' })
    for (let i = 0; i < 20; i++) {
      await new Promise(res => setTimeout(res, 4000))
      const s = await refetchStory(id)
      if (!s) continue
      const pins = storyPinups(s)
      const changed = mode === 'add'
        ? pins.length > prevLen
        : (pins[pins.length - 1] || '') !== prevLast
      if (changed) break
    }
  } catch (err) {
    emit('toast', { msg: String(err.message || err), type: 'error' })
  } finally {
    const next = new Set(pinupBusy.value)
    next.delete(id)
    pinupBusy.value = next
  }
}

async function deleteStory(story) {
  const label = storyTitle(story) || story.story_id
  if (!window.confirm(t('storybook.deleteConfirm', { title: label }))) return
  const r = await fetch(`/api/story/${story.story_id}`, { method: 'DELETE' })
  if (!r.ok) { emit('toast', { msg: t('storybook.deleteFailed'), type: 'error' }); return }
  stories.value = stories.value.filter(s => s.story_id !== story.story_id)
  if (detailStory.value?.story_id === story.story_id) detailStory.value = null
  emit('toast', { msg: t('storybook.deleted'), type: 'success' })
}

function onThumbError(e, sha256) {
  if (e.target.dataset.fallback) return
  e.target.dataset.fallback = '1'
  e.target.src = `/api/originals/${sha256}`
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleString(lang.value === 'ja' ? 'ja-JP' : 'en-US')
}

function motifOf(story) {
  const c = (story.candidates || []).find(x => x.id === story.selected_candidate)
  return c?.motif || c?.key_motif || ''
}

function clearFilters() {
  filters.value = { base_axis: '', time_scale: '', emotion: '', qualityMin: '' }
  query.value = ''
}

function onKey(e) {
  if (!props.show) return
  if (pinupView.value) {
    if (e.key === 'Escape') { pinupView.value = null; e.preventDefault() }
    return
  }
  if (detailStory.value) {
    if (e.key === 'ArrowLeft') { detailPrev(); e.preventDefault() }
    else if (e.key === 'ArrowRight') { detailNext(); e.preventDefault() }
    else if (e.key === 'Escape') { closeDetail(); e.preventDefault() }
    else if (e.key === '1') { detailScrubIdx.value = 0; e.preventDefault() }
    else if (e.key === '2') { detailScrubIdx.value = 1; e.preventDefault() }
    else if (e.key === '3') { detailScrubIdx.value = 2; e.preventDefault() }
  } else if (e.key === 'Escape') {
    if (_dismissBlocked()) {
      e.preventDefault()
      e.stopPropagation()
      return
    }
    close()
    e.preventDefault()
  }
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="storybook-root fixed inset-0 z-[var(--z-panel-story)] flex items-center justify-center p-4"
      @mousedown.self="onBackdropDown"
      @mouseup.self="onBackdropUp">
      <div class="sb-shell relative w-full max-w-6xl max-h-[92vh] flex flex-col overflow-hidden"
        @mousedown.stop>

        <!-- header -->
        <div class="flex items-center justify-between px-5 py-3.5 sb-hairline">
          <h2 class="sb-display text-lg text-[var(--sb-amber)] tracking-wide flex items-center gap-2.5">
            <SbIcon name="book" class="w-5 h-5 opacity-80" />
            {{ t('storybook.title') }}
          </h2>
          <div class="flex items-center gap-2">
            <div class="sb-seg">
              <button v-for="l in ['ja', 'en']" :key="l" @click="lang = l"
                :class="lang === l ? 'is-on' : ''" class="sb-seg-btn uppercase">{{ l }}</button>
            </div>
            <button @click="fetchStories" class="sb-btn" :aria-label="t('storybook.aria.refresh')">
              <SbIcon name="refresh" class="w-3.5 h-3.5" />
              {{ t('storybook.refresh') }}
            </button>
            <button @click="close" class="sb-icon-btn" :aria-label="t('storybook.aria.close')">
              <SbIcon name="close" class="w-4 h-4" />
            </button>
          </div>
        </div>

        <!-- toolbar -->
        <div class="flex flex-wrap items-center gap-2 px-5 py-2.5 sb-hairline bg-black/20">
          <div class="sb-seg">
            <button v-for="m in VIEW_MODES" :key="m" @click="viewMode = m"
              :class="viewMode === m ? 'is-on-teal' : ''" class="sb-seg-btn">
              {{ t('storybook.view.' + m) }}
            </button>
          </div>

          <label class="sb-search">
            <SbIcon name="search" class="w-3.5 h-3.5 text-[var(--sb-muted)]" />
            <input v-model="query" type="search" :placeholder="t('storybook.searchPh')" />
          </label>

          <select v-model="sort" class="sb-select">
            <option v-for="s in SORTS" :key="s" :value="s">{{ t('storybook.sort.' + s) }}</option>
          </select>

          <div class="flex items-center gap-1 flex-wrap">
            <span class="sb-label">{{ t('storybook.filter.baseAxis') }}</span>
            <button
              @click="filters.base_axis = ''"
              :class="!filters.base_axis ? 'is-chip-on' : ''"
              class="sb-chip">{{ t('storybook.filter.all') }}</button>
            <button v-for="a in AXES" :key="a"
              @click="filters.base_axis = filters.base_axis === a ? '' : a"
              :class="filters.base_axis === a ? 'is-chip-on' : ''"
              class="sb-chip">
              {{ t('chronicle.axis.' + a) }}
            </button>
          </div>

          <details class="relative text-[10px]">
            <summary class="sb-btn cursor-pointer list-none flex items-center gap-1">
              <SbIcon name="filter" class="w-3 h-3" />
              {{ t('storybook.filter.more') }}
            </summary>
            <div class="absolute z-30 mt-1 right-0 sm:left-0 sm:right-auto bg-[var(--sb-panel)] border border-[var(--sb-rule)] rounded-xl p-3 flex flex-col gap-2 shadow-2xl min-w-[16rem]">
              <div class="flex items-center gap-1 flex-wrap max-w-md">
                <span class="sb-label w-20">{{ t('storybook.filter.timeScale') }}</span>
                <button v-for="ts in TIME_SCALES" :key="ts"
                  @click="filters.time_scale = filters.time_scale === ts ? '' : ts"
                  :class="filters.time_scale === ts ? 'is-chip-on-teal' : ''"
                  class="sb-chip">
                  {{ t('chronicle.timeScale.' + ts) }}
                </button>
              </div>
              <div class="flex items-center gap-1 flex-wrap max-w-md">
                <span class="sb-label w-20">{{ t('storybook.filter.emotion') }}</span>
                <button v-for="em in EMOTION_DIMENSIONS" :key="em"
                  @click="filters.emotion = filters.emotion === em ? '' : em"
                  :class="filters.emotion === em ? 'is-chip-on-indigo' : ''"
                  class="sb-chip">
                  {{ t(`inspire.emotion.${em}`) }}
                </button>
              </div>
              <div class="flex items-center gap-2 flex-wrap max-w-md">
                <span class="sb-label w-20">{{ t('storybook.filterQuality') }}</span>
                <input v-model="filters.qualityMin" type="number" min="0" max="100" step="5"
                  placeholder="—"
                  class="sb-select w-16 font-mono text-[10px]" />
                <span class="text-[var(--sb-faint)] font-mono">+</span>
              </div>
            </div>
          </details>

          <button v-if="query || filters.base_axis || filters.time_scale || filters.emotion || filters.qualityMin !== ''"
            @click="clearFilters" class="ml-auto sb-btn">
            <SbIcon name="close" class="w-3 h-3" />
            {{ t('storybook.filter.clear') }}
          </button>
        </div>

        <!-- body -->
        <div class="flex-1 overflow-y-auto p-5 flex flex-col gap-5 sb-body">
          <p v-if="loading" class="text-xs text-[var(--sb-muted)]">{{ t('storybook.loading') }}</p>
          <p v-else-if="!stories.length" class="text-xs text-[var(--sb-muted)]">{{ t('storybook.empty') }}</p>
          <p v-else-if="!visibleStories.length" class="text-xs text-[var(--sb-muted)]">{{ t('storybook.emptyFiltered') }}</p>

          <!-- GALLERY -->
          <div v-if="viewMode === 'gallery' && visibleStories.length"
            class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-5">
            <div v-for="story in visibleStories" :key="story.story_id"
              class="storybook-card sb-card group cursor-pointer"
              @click="openDetail(story)">
              <TimeScrubPolaroids
                :front-index="cardScrubIdx(story)"
                :base-axis="story.base_time_axis || 'present'"
                :image-for="(ax) => axisImage(story, ax)"
                :pending-label="t('storybook.imagePending')"
                size="md"
                @update:front-index="setCardScrub(story.story_id, $event)"
                @open-image="openImage"
                @thumb-error="(e, sha) => onThumbError(e, sha)"
              />
              <div class="flex flex-col gap-1.5 mt-1">
                <h3 class="sb-display text-sm text-[var(--sb-amber)] truncate">
                  {{ storyTitle(story) || '—' }}
                </h3>
                <div class="flex items-center gap-1 flex-wrap text-[10px]">
                  <span v-if="motifOf(story)" class="sb-meta-chip sb-meta-motif">
                    <SbIcon name="spark" class="w-2.5 h-2.5" />{{ motifOf(story) }}
                  </span>
                  <span v-else-if="story.time_scale" class="sb-meta-chip sb-meta-scale">
                    <SbIcon name="clock" class="w-2.5 h-2.5" />{{ t('chronicle.timeScale.' + story.time_scale) }}
                  </span>
                  <span v-if="isTopicOnly(story)" class="sb-meta-chip sb-meta-topic">
                    {{ t('storybook.topicOnlyBadge') }}
                  </span>
                  <span class="ml-auto text-[var(--sb-faint)] font-mono text-[9px]">{{ formatDate(story.created_at).split(' ')[0] }}</span>
                </div>
                <div class="sb-card-meta-more flex items-center gap-1 flex-wrap text-[10px] opacity-0 group-hover:opacity-100 transition-opacity">
                  <span v-if="story.emotion" class="sb-meta-chip sb-meta-emotion">
                    {{ t(`inspire.emotion.${story.emotion}`, story.emotion) }}
                  </span>
                  <span v-if="qualityPct(story) != null" class="sb-meta-chip font-mono text-[var(--sb-muted)]"
                    :title="t('storybook.qualityScore')">
                    {{ qualityPct(story) }}
                  </span>
                  <span v-if="motifOf(story) && story.time_scale" class="sb-meta-chip sb-meta-scale">
                    <SbIcon name="clock" class="w-2.5 h-2.5" />{{ t('chronicle.timeScale.' + story.time_scale) }}
                  </span>
                  <span v-if="story.base_time_axis" class="sb-meta-chip text-[9px] text-[var(--sb-muted)]">
                    {{ t('chronicle.axis.' + story.base_time_axis) }}
                  </span>
                </div>
                <div class="flex items-center gap-1 pt-0.5">
                  <button @click.stop="emit('weave-from', axisImage(story, story.base_time_axis))"
                    v-if="axisImage(story, story.base_time_axis)"
                    :aria-label="t('storybook.aria.weave')"
                    :title="t('storybook.weaveFrom')"
                    class="sb-icon-btn-sm text-teal-300/80">
                    <SbIcon name="weave" class="w-3.5 h-3.5" />
                  </button>
                  <button @click.stop="deleteStory(story)"
                    :aria-label="t('storybook.aria.delete')"
                    :title="t('storybook.delete')"
                    class="ml-auto sb-icon-btn-sm text-red-300/70 hover:text-red-200">
                    <SbIcon name="trash" class="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- MOODBOARD -->
          <div v-if="viewMode === 'moodboard' && moodboardPins.length" class="mood-wall">
            <p class="text-[11px] text-[var(--sb-muted)] mb-3 leading-relaxed">{{ t('storybook.moodboardHint') }}</p>
            <div class="pinboard mood-wall-board">
              <div
                v-for="(pin, i) in moodboardPins"
                :key="pin.key"
                class="pincard mood-pin"
                :style="{ transform: `rotate(${pinupRotation(i)}deg)` }"
                @click="openDetail(pin.story)"
              >
                <span class="pincard-pin"></span>
                <img :src="`/api/thumbnails/${pin.sha}.webp`" @error="onThumbError($event, pin.sha)" loading="lazy" />
                <div class="mood-pin-caption">
                  <span class="truncate">{{ pin.title || '—' }}</span>
                  <span class="opacity-70">{{ pin.kind === 'pinup' ? 'pin' : t('chronicle.axis.' + pin.axis) }}</span>
                </div>
              </div>
            </div>
          </div>
          <p v-else-if="viewMode === 'moodboard' && visibleStories.length && !moodboardPins.length"
            class="text-xs text-[var(--sb-muted)]">{{ t('storybook.moodboardEmpty') }}</p>

          <!-- TIMELINE -->
          <div v-if="viewMode === 'timeline' && visibleStories.length" class="flex flex-col gap-4">
            <div v-for="bucket in timelineBuckets" :key="bucket.key" class="flex flex-col gap-2">
              <h3 class="sticky top-0 z-10 backdrop-blur-md bg-[var(--sb-ink)]/85 py-1.5 sb-display text-xs tracking-wider text-[var(--sb-amber)] border-b border-[var(--sb-rule)] flex items-center gap-2">
                <SbIcon name="clock" class="w-3.5 h-3.5 opacity-70" />
                {{ bucket.label }}
                <span class="text-[var(--sb-muted)] font-normal" style="font-family: var(--sb-font-ui)">({{ bucket.stories.length }})</span>
              </h3>
              <div class="flex gap-3 overflow-x-auto pb-2 snap-x">
                <div v-for="story in bucket.stories" :key="story.story_id"
                  class="storybook-card sb-card group shrink-0 w-48 snap-start cursor-pointer"
                  @click="openDetail(story)">
                  <TimeScrubPolaroids
                    :front-index="cardScrubIdx(story)"
                    :base-axis="story.base_time_axis || 'present'"
                    :image-for="(ax) => axisImage(story, ax)"
                    :pending-label="t('storybook.imagePending')"
                    size="sm"
                    @update:front-index="setCardScrub(story.story_id, $event)"
                    @open-image="openImage"
                    @thumb-error="(e, sha) => onThumbError(e, sha)"
                  />
                  <h4 class="sb-display text-[11px] text-[var(--sb-amber)] truncate mt-1">{{ storyTitle(story) || '—' }}</h4>
                  <div class="flex items-center gap-1 text-[9px] mt-0.5">
                    <span v-if="motifOf(story)" class="sb-meta-chip sb-meta-motif truncate">
                      <SbIcon name="spark" class="w-2.5 h-2.5" />{{ motifOf(story) }}
                    </span>
                    <span class="ml-auto text-[var(--sb-faint)] font-mono">{{ new Date(story.created_at * 1000).toLocaleTimeString(lang === 'ja' ? 'ja-JP' : 'en-US', { hour: '2-digit', minute: '2-digit' }) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- DETAIL LIST -->
          <div v-if="viewMode === 'detail' && visibleStories.length" class="flex flex-col gap-5">
            <div v-for="story in visibleStories" :key="story.story_id" class="sb-card p-4 flex flex-col gap-3">
              <div v-if="storyTitle(story)" class="flex flex-col gap-1.5">
                <h3 class="sb-display text-base text-[var(--sb-amber)]">{{ storyTitle(story) }}</h3>
                <p v-if="storyOverall(story)"
                  class="text-[12px] text-gray-300/90 leading-relaxed whitespace-pre-wrap border-l border-[var(--sb-rule)] pl-3 line-clamp-3">
                  {{ storyOverall(story) }}
                </p>
                <button v-if="storyOverall(story)" @click="openDetail(story)" class="sb-link self-start">
                  {{ t('storybook.readMore') }}
                </button>
              </div>
              <div class="flex items-center gap-2 text-[10px] text-[var(--sb-muted)] flex-wrap">
                <span v-if="story.worldview" class="text-[var(--sb-amber)]/80">{{ story.worldview }}</span>
                <span v-if="story.time_scale" class="sb-meta-chip sb-meta-scale">
                  <SbIcon name="clock" class="w-2.5 h-2.5" />{{ t('chronicle.timeScale.' + story.time_scale) }}
                </span>
                <span v-if="story.emotion" class="sb-meta-chip sb-meta-emotion">
                  {{ t(`inspire.emotion.${story.emotion}`, story.emotion) }}
                </span>
                <button @click="openDetail(story)" class="sb-btn">
                  <SbIcon name="doc" class="w-3 h-3" />
                  {{ t('storybook.details') }}
                </button>
                <button @click="deleteStory(story)" class="sb-btn text-red-300/80">
                  <SbIcon name="trash" class="w-3 h-3" />
                  {{ t('storybook.delete') }}
                </button>
                <span class="ml-auto font-mono text-[9px]">{{ formatDate(story.created_at) }}</span>
              </div>

              <details v-if="story.candidates?.length" class="text-[11px]">
                <summary class="cursor-pointer text-[var(--sb-muted)] hover:text-gray-300 select-none">
                  {{ t('storybook.otherCandidates') }} ({{ story.candidates.length }})
                </summary>
                <div class="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div v-for="c in story.candidates" :key="c.id"
                    class="p-2 rounded-lg border"
                    :class="c.id === story.selected_candidate ? 'border-[var(--sb-amber)]/40 bg-amber-900/10' : 'border-white/5 bg-black/20'">
                    <div class="flex items-center gap-1.5">
                      <span class="text-[9px] font-bold w-4 h-4 flex items-center justify-center rounded-full bg-white/10 text-gray-200">{{ c.id }}</span>
                      <span class="text-[11px] font-semibold text-[var(--sb-amber)] leading-tight">{{ c.title }}</span>
                    </div>
                    <p class="text-[10px] text-gray-400 mt-1 leading-snug">{{ c.summary }}</p>
                  </div>
                </div>
              </details>

              <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div v-for="axis in AXES" :key="axis"
                  class="rounded-xl border p-3 flex flex-col gap-2"
                  :class="axis === story.base_time_axis ? 'border-[var(--sb-amber)]/35 bg-amber-900/10' : 'border-white/5 bg-black/25'">
                  <div class="flex items-center justify-between">
                    <span class="text-[10px] font-semibold uppercase tracking-wide"
                      :class="axis === story.base_time_axis ? 'text-[var(--sb-amber)]' : 'text-teal-400/90'">
                      {{ t('chronicle.axis.' + axis) }}
                      <span v-if="axis === story.base_time_axis" class="text-[var(--sb-muted)] normal-case font-normal ml-1">({{ t('storybook.base') }})</span>
                    </span>
                    <button v-if="story.axes?.[axis]?.prompt_positive"
                      @click="sendAxisToRefine(story, axis)"
                      :title="t('storybook.refineAxisTitle')"
                      :aria-label="t('storybook.refineAxis')"
                      class="sb-btn-accent">
                      <SbIcon name="spark" class="w-3 h-3" />
                      {{ t('storybook.refineAxis') }}
                    </button>
                  </div>

                  <div class="relative group aspect-square bg-black/40 rounded-lg overflow-hidden flex items-center justify-center cursor-pointer"
                    @click="openDetail(story)">
                    <img v-if="axisImage(story, axis)" :src="`/api/thumbnails/${axisImage(story, axis)}.webp`"
                      @error="onThumbError($event, axisImage(story, axis))"
                      class="w-full h-full object-cover hover:opacity-90 transition-opacity" loading="lazy" />
                    <span v-else class="text-xs text-[var(--sb-faint)]">{{ t('storybook.imagePending') }}</span>
                    <button v-if="axisImage(story, axis)"
                      @click.stop="emit('weave-from', axisImage(story, axis))"
                      :title="t('storybook.weaveFrom')"
                      :aria-label="t('storybook.aria.weave')"
                      class="absolute bottom-1.5 right-1.5 sb-btn opacity-0 group-hover:opacity-100 transition-opacity bg-teal-950/90 border-teal-700/40 text-teal-100">
                      <SbIcon name="weave" class="w-3 h-3" />
                      {{ t('storybook.weaveFromShort') }}
                    </button>
                  </div>

                  <p class="text-[11px] text-gray-400 leading-relaxed whitespace-pre-wrap max-h-32 overflow-y-auto">
                    {{ axisStory(story, axis) || '—' }}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>

    <!-- Story detail: fixed above Storybook shell (z-65), below gallery detail (z-70) -->
    <Transition name="sb-overlay">
      <div v-if="show && detailStory"
        class="sb-theme fixed inset-0 z-[var(--z-panel-story-top)] sb-overlay-bg flex items-center justify-center p-3 sm:p-6"
        @click.self="closeDetail">
        <div class="sb-detail-panel w-full max-w-4xl max-h-[92vh] flex flex-col overflow-hidden text-gray-200">
          <div class="flex items-start justify-between px-5 sm:px-6 py-4 sb-hairline gap-3">
            <div class="min-w-0 flex-1">
              <h2 class="sb-display text-xl text-[var(--sb-amber)] leading-snug">
                {{ storyTitle(detailStory) || t('storybook.details') }}
              </h2>
              <div class="mt-1.5 text-[11px] text-[var(--sb-muted)] leading-relaxed">
                <span v-if="detailStory.user_topic" class="text-[var(--sb-amber)]/90">{{ detailStory.user_topic }}</span>
                <span v-if="detailStory.user_topic && detailStory.worldview"> · </span>
                <span v-if="detailStory.worldview">{{ detailStory.worldview }}</span>
                <span v-if="detailStory.created_at" class="font-mono text-[9px] text-[var(--sb-faint)] ml-2">{{ formatDate(detailStory.created_at) }}</span>
              </div>
              <details v-if="hasWeaveSettings(detailStory)" class="mt-2 text-[10px]">
                <summary class="cursor-pointer text-[var(--sb-faint)] hover:text-[var(--sb-muted)] select-none">
                  {{ t('storybook.weaveSettings') }}
                </summary>
                <div class="mt-1.5 flex flex-wrap gap-1.5">
                  <span v-if="isTopicOnly(detailStory)" class="sb-meta-chip sb-meta-topic">
                    {{ t('storybook.topicOnlyBadge') }}
                  </span>
                  <span v-if="typeof detailStory.divergence === 'number'" class="sb-meta-chip font-mono text-[var(--sb-muted)]">
                    {{ t('chronicle.divergence') }} {{ Math.round(detailStory.divergence * 100) }}%
                  </span>
                  <span v-if="(detailStory.mutation_tags || []).length" class="sb-meta-chip text-[var(--sb-muted)]">
                    {{ t('chronicle.mutationTags') }}: {{ (detailStory.mutation_tags || []).join(', ') }}
                  </span>
                  <span v-if="storyBody(detailStory).tone" class="sb-meta-chip text-[var(--sb-muted)]">
                    {{ t('chronicle.tone.' + storyBody(detailStory).tone, storyBody(detailStory).tone) }}
                  </span>
                  <span v-if="storyBody(detailStory).dramatic_mode" class="sb-meta-chip text-[var(--sb-muted)]">
                    {{ dramaticModeLabel(storyBody(detailStory).dramatic_mode) }}
                  </span>
                  <span v-if="storyBody(detailStory).use_draft_refine" class="sb-meta-chip text-[var(--sb-muted)]">
                    {{ t('chronicle.draftRefineMode.' + storyBody(detailStory).use_draft_refine, storyBody(detailStory).use_draft_refine) }}
                  </span>
                  <span v-if="storyBody(detailStory).prompt_style" class="sb-meta-chip font-mono text-[var(--sb-muted)]">
                    {{ promptStyleLabel(storyBody(detailStory).prompt_style) }}
                  </span>
                  <span v-if="storyBody(detailStory).prose_paragraphs && storyBody(detailStory).prompt_style !== 'danbooru'"
                    class="sb-meta-chip text-[var(--sb-muted)]">
                    {{ t('chronicle.proseLengthLabel') }} {{ storyBody(detailStory).prose_paragraphs }}{{ t('chronicle.proseLengthUnit') }}
                  </span>
                  <span v-if="detailStory.time_scale" class="sb-meta-chip sb-meta-scale">
                    <SbIcon name="clock" class="w-2.5 h-2.5" />{{ t('chronicle.timeScale.' + detailStory.time_scale) }}
                  </span>
                  <span v-if="detailStory.emotion" class="sb-meta-chip sb-meta-emotion">
                    {{ t('inspire.emotion.' + detailStory.emotion) }}
                  </span>
                  <span v-if="detailStory.base_model_name" :title="t('storybook.modelTitle')" class="sb-meta-chip font-mono text-[var(--sb-faint)] truncate max-w-[10rem]">{{ detailStory.base_model_name }}</span>
                  <span v-if="detailStory.workflow_name" :title="t('storybook.workflowTitle')" class="sb-meta-chip font-mono text-[var(--sb-faint)] truncate max-w-[10rem]">{{ detailStory.workflow_name }}</span>
                </div>
              </details>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <button @click="detailPrev" :disabled="detailIndex <= 0"
                class="sb-icon-btn disabled:opacity-30"
                :aria-label="t('storybook.aria.prev')"
                :title="t('storybook.detail.prev')">
                <SbIcon name="chevronLeft" class="w-4 h-4" />
              </button>
              <span v-if="detailIndex >= 0" class="text-[10px] text-[var(--sb-muted)] font-mono min-w-[3.5rem] text-center">
                {{ t('storybook.detail.position', { n: detailIndex + 1, total: visibleStories.length }) }}
              </span>
              <button @click="detailNext" :disabled="detailIndex < 0 || detailIndex >= visibleStories.length - 1"
                class="sb-icon-btn disabled:opacity-30"
                :aria-label="t('storybook.aria.next')"
                :title="t('storybook.detail.next')">
                <SbIcon name="chevronRight" class="w-4 h-4" />
              </button>
              <div class="sb-seg ml-1">
                <button v-for="l in ['ja', 'en']" :key="l" @click="lang = l"
                  :class="lang === l ? 'is-on' : ''" class="sb-seg-btn uppercase">{{ l }}</button>
              </div>
              <button @click="closeDetail" class="sb-icon-btn"
                :aria-label="t('storybook.aria.close')"
                :title="t('storybook.detail.close')">
                <SbIcon name="close" class="w-4 h-4" />
              </button>
            </div>
          </div>

          <div class="flex-1 overflow-y-auto min-h-0">
            <!-- Time scrub hero -->
            <div class="px-6 sm:px-8 pt-5 pb-2 sb-section">
              <div class="flex items-center justify-between gap-2 mb-3 flex-wrap">
                <h4 class="sb-section-title mb-0">{{ t('storybook.timeScrubTitle') }}</h4>
                <p class="text-[10px] text-[var(--sb-faint)]">{{ t('storybook.timeScrubHint') }}</p>
              </div>
              <TimeScrubPolaroids
                v-model:front-index="detailScrubIdx"
                :base-axis="detailStory.base_time_axis || 'present'"
                :image-for="(ax) => axisImage(detailStory, ax)"
                :pending-label="t('storybook.imagePending')"
                size="lg"
                @open-image="openImage"
                @thumb-error="(e, sha) => onThumbError(e, sha)"
              />
              <p class="mt-3 sb-prose text-sm max-h-28 overflow-y-auto border-l border-[var(--sb-rule)] pl-3">
                {{ axisStory(detailStory, AXES[detailScrubIdx]) || '—' }}
              </p>
              <div class="mt-2 flex flex-wrap gap-1.5">
                <button
                  v-if="detailStory.axes?.[AXES[detailScrubIdx]]?.prompt_positive"
                  @click="sendAxisToRefine(detailStory, AXES[detailScrubIdx])"
                  class="sb-btn-accent"
                >
                  <SbIcon name="spark" class="w-3 h-3" />
                  {{ t('storybook.refineAxis') }}
                </button>
                <button
                  v-if="axisImage(detailStory, AXES[detailScrubIdx])
                    && !(AXES[detailScrubIdx] === detailStory.base_time_axis && detailStory.base_image_id)"
                  @click="regenerateAxis(detailStory, AXES[detailScrubIdx])"
                  :disabled="regenBusy.has(detailStory.story_id + ':' + AXES[detailScrubIdx])"
                  class="sb-btn disabled:opacity-40"
                  :title="t('storybook.regen')"
                >
                  <SbIcon name="refresh" class="w-3 h-3" />
                  {{ t('storybook.regen') }}
                </button>
                <button
                  v-if="axisImage(detailStory, AXES[detailScrubIdx])"
                  @click="emit('weave-from', axisImage(detailStory, AXES[detailScrubIdx]))"
                  class="sb-btn border-teal-700/40 text-teal-100"
                >
                  <SbIcon name="weave" class="w-3 h-3" />
                  {{ t('storybook.weaveFromShort') }}
                </button>
              </div>
            </div>

            <div v-if="storyOverall(detailStory)" class="px-6 sm:px-8 py-6 sb-section">
              <p class="sb-prose italic border-l border-[var(--sb-rule)] pl-4">
                {{ storyOverall(detailStory) }}
              </p>
            </div>

            <!-- Quality radar: keep near the top so it isn't buried under bio/timetable -->
            <div v-if="detailStory.quality_eval?.dimensions"
              class="px-6 sm:px-8 py-5 sb-section">
              <h4 class="sb-section-title flex items-center gap-2 mb-4">
                <SbIcon name="spark" class="w-3.5 h-3.5 opacity-70" />
                {{ t('storybook.quality.title') }}
                <span class="ml-auto font-mono text-sm text-[var(--sb-amber)] normal-case tracking-normal">
                  {{ Math.round((detailStory.quality_eval.overall || 0) * 100) }}
                </span>
              </h4>
              <StoryQualityRadar :eval="detailStory.quality_eval" />
              <div v-if="qualityActions(detailStory).length" class="mt-3 flex flex-wrap gap-1.5">
                <button
                  v-for="act in qualityActions(detailStory)"
                  :key="act.id"
                  type="button"
                  class="sb-btn-accent"
                  :title="act.tip"
                  @click="act.run()"
                >
                  <SbIcon name="spark" class="w-3 h-3" />
                  {{ act.label }}
                </button>
              </div>
              <div v-if="AXES.some(a => draftRichnessFor(detailStory, a))"
                class="mt-2 space-y-0.5">
                <p v-for="ax in AXES" :key="'dr-' + ax"
                  v-show="draftRichnessFor(detailStory, ax)"
                  class="text-[10px] font-mono text-[var(--sb-faint)]">
                  <span>{{ t('chronicle.axis.' + ax) }}:</span>
                  {{ formatDraftDelta(draftRichnessFor(detailStory, ax)) }}
                </p>
              </div>
              <p v-if="qualityDraftNote(detailStory)"
                class="mt-2 text-[10px] font-mono text-[var(--sb-faint)]">
                {{ qualityDraftNote(detailStory) }}
              </p>
              <p class="mt-3 text-[10px] text-[var(--sb-muted)] leading-relaxed">
                {{ t('storybook.quality.hint') }}
              </p>
            </div>

            <div v-if="storyBio(detailStory)" class="px-6 sm:px-8 py-6 sb-section">
              <div class="flex items-center justify-between mb-3 gap-2 flex-wrap">
                <h4 class="sb-section-title">{{ t('storybook.biography') }}</h4>
                <div class="flex items-center gap-2">
                  <button @click="addPinup(detailStory, 'add')"
                    :disabled="pinupBusy.has(detailStory.story_id)"
                    :title="t('storybook.pinupWorkflowTitle', { wf: detailStory.workflow_name || '—' })"
                    class="sb-btn disabled:opacity-40">
                    + {{ t('storybook.pinupAdd') }}
                  </button>
                  <button v-if="storyPinups(detailStory).length"
                    @click="addPinup(detailStory, 'replace')"
                    :disabled="pinupBusy.has(detailStory.story_id)"
                    :title="t('storybook.pinupWorkflowTitle', { wf: detailStory.workflow_name || '—' })"
                    class="sb-btn disabled:opacity-40">
                    <SbIcon name="refresh" class="w-3 h-3" />
                    {{ t('storybook.pinupReplace') }}
                  </button>
                </div>
              </div>
              <div v-if="storyPinups(detailStory).length || pinupBusy.has(detailStory.story_id)"
                class="pinboard mb-4">
                <div v-for="(sha, i) in storyPinups(detailStory)" :key="sha"
                  class="pincard" :style="{ transform: `rotate(${pinupRotation(i)}deg)` }"
                  @click="openPinup(sha)">
                  <span class="pincard-pin"></span>
                  <img :src="`/api/thumbnails/${sha}.webp`" @error="onThumbError($event, sha)" />
                </div>
                <div v-if="pinupBusy.has(detailStory.story_id)" class="pincard pincard--loading">
                  <span class="pincard-pin"></span>
                  <div class="pincard-spin">…</div>
                </div>
              </div>
              <div class="text-sm text-gray-300 space-y-1.5 min-w-0 leading-relaxed">
                <p v-if="storyBio(detailStory).personality">{{ storyBio(detailStory).personality }}</p>
                <p v-if="storyBio(detailStory).occupation" class="text-gray-400">
                  <span class="text-[var(--sb-muted)]">{{ t('storybook.bioOccupation') }}:</span> {{ storyBio(detailStory).occupation }}
                </p>
                <p v-for="f in BIO_LIST_FIELDS" :key="f"
                  v-show="asStringList(storyBio(detailStory)[f]).length"
                  class="text-gray-400 break-words">
                  <span class="text-[var(--sb-muted)]">{{ t('storybook.bio_' + f) }}:</span>
                  {{ joinList(storyBio(detailStory)[f]) }}
                </p>
                <p v-if="storyBio(detailStory).backstory" class="text-gray-400 italic pt-1">{{ storyBio(detailStory).backstory }}</p>
              </div>
            </div>

            <details v-if="detailStory.candidates?.length" class="px-6 sm:px-8 py-5 sb-section" :open="variantShelfOpen">
              <summary class="sb-section-title cursor-pointer select-none list-none flex items-center gap-2 mb-3"
                @click.prevent="variantShelfOpen = !variantShelfOpen">
                <SbIcon name="spark" class="w-3.5 h-3.5 opacity-70" />
                {{ t('storybook.variantShelf') }} ({{ detailStory.candidates.length }})
              </summary>
              <p class="text-[10px] text-[var(--sb-muted)] mb-2">{{ t('storybook.variantShelfHint') }}</p>
              <div class="flex gap-3 overflow-x-auto pb-2 snap-x">
                <div v-for="c in detailStory.candidates" :key="c.id"
                  class="variant-card snap-start shrink-0 w-56 p-3 rounded-xl border"
                  :class="c.id === detailStory.selected_candidate
                    ? 'border-[var(--sb-amber)]/50 bg-amber-900/15 variant-card--picked'
                    : 'border-white/5 bg-black/25'">
                  <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="text-[9px] font-bold w-4 h-4 flex items-center justify-center rounded-full bg-white/10 text-gray-200">{{ c.id }}</span>
                    <span class="text-[11px] font-semibold text-[var(--sb-amber)] leading-tight">{{ c.title }}</span>
                    <span v-if="c.id === detailStory.selected_candidate" class="sb-meta-chip text-[9px] text-[var(--sb-amber)]">
                      {{ t('storybook.variantPicked') }}
                    </span>
                  </div>
                  <p v-if="c.summary" class="text-[10px] text-gray-400 mt-1.5 leading-snug line-clamp-4">{{ c.summary }}</p>
                  <div class="mt-2 flex flex-col gap-0.5 text-[10px] leading-snug">
                    <p v-for="ax in AXES" :key="ax" v-show="c[ax]" class="line-clamp-2">
                      <span class="font-semibold uppercase tracking-wide mr-1 text-[9px]"
                        :class="ax === detailStory.base_time_axis ? 'text-[var(--sb-amber)]' : 'text-teal-400/80'">
                        {{ t('chronicle.axis.' + ax) }}
                      </span>
                      <span class="text-gray-300">{{ c[ax] }}</span>
                    </p>
                  </div>
                </div>
              </div>
            </details>

            <details v-if="storyTimetable(detailStory).length" class="px-6 sm:px-8 py-5 sb-section" open>
              <summary class="sb-section-title cursor-pointer select-none list-none flex items-center gap-2 mb-3">
                <SbIcon name="clock" class="w-3.5 h-3.5 opacity-70" />
                {{ t('storybook.timetable') }}
              </summary>
              <ul class="space-y-2">
                <li v-for="(slot, si) in storyTimetable(detailStory)" :key="si"
                  class="text-sm text-gray-300 flex gap-3">
                  <span class="text-teal-400/85 font-medium shrink-0 w-24 text-[13px]">{{ slot.label }}</span>
                  <span class="min-w-0 leading-relaxed">
                    {{ slot.activity }}
                    <span v-if="slot.place" class="text-[var(--sb-muted)]"> {{ t('storybook.timetablePlace', { place: slot.place }) }}</span>
                    <span v-if="slot.feeling" class="text-gray-500 italic"> {{ t('storybook.timetableFeeling', { feeling: slot.feeling }) }}</span>
                  </span>
                </li>
              </ul>
            </details>

            <div v-for="(axis, ai) in AXES" :key="axis"
              class="px-6 sm:px-8 py-6 sb-section flex flex-col sm:flex-row gap-5"
              :class="ai % 2 === 1 ? 'sm:flex-row-reverse' : ''">
              <div class="sm:w-2/5 shrink-0">
                <div class="relative aspect-square rounded-xl overflow-hidden bg-black/50 border border-white/5 group cursor-pointer"
                  @click="axisImage(detailStory, axis) && openImage(axisImage(detailStory, axis))">
                  <img v-if="axisImage(detailStory, axis)"
                    :src="`/api/thumbnails/${axisImage(detailStory, axis)}.webp`"
                    @error="onThumbError($event, axisImage(detailStory, axis))"
                    class="w-full h-full object-cover" loading="lazy" />
                  <span v-else class="absolute inset-0 flex items-center justify-center text-[var(--sb-faint)] text-sm cursor-default">{{ t('storybook.imagePending') }}</span>
                  <div class="absolute inset-x-0 bottom-0 p-2 flex flex-wrap gap-1.5 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity bg-gradient-to-t from-black/80 to-transparent"
                    @click.stop>
                    <button v-if="axisImage(detailStory, axis)"
                      @click="openImage(axisImage(detailStory, axis))"
                      class="sb-btn bg-black/50"
                      :aria-label="t('storybook.aria.openInGallery')">
                      <SbIcon name="image" class="w-3 h-3" />
                      {{ t('storybook.openInGallery') }}
                    </button>
                    <button v-if="axisImage(detailStory, axis)"
                      @click="emit('weave-from', axisImage(detailStory, axis))"
                      class="sb-btn bg-teal-950/70 border-teal-700/40 text-teal-100"
                      :aria-label="t('storybook.aria.weave')">
                      <SbIcon name="weave" class="w-3 h-3" />
                      {{ t('storybook.weaveFromShort') }}
                    </button>
                    <button v-if="detailStory.axes?.[axis]?.prompt_positive"
                      @click="sendAxisToRefine(detailStory, axis)"
                      :title="t('storybook.refineAxisTitle')"
                      class="sb-btn-accent ml-auto"
                      :aria-label="t('storybook.refineAxis')">
                      <SbIcon name="spark" class="w-3 h-3" />
                      {{ t('storybook.refineAxis') }}
                    </button>
                    <button
                      v-if="!(axis === detailStory.base_time_axis && detailStory.base_image_id)"
                      @click="regenerateAxis(detailStory, axis)"
                      :disabled="regenBusy.has(detailStory.story_id + ':' + axis)"
                      class="sb-btn bg-black/50 disabled:opacity-40"
                      :aria-label="t('storybook.aria.regen')"
                      :title="t('storybook.regen')"
                    >
                      <SbIcon name="refresh" class="w-3 h-3" />
                      {{ t('storybook.regen') }}
                    </button>
                  </div>
                </div>
              </div>
              <div class="sm:w-3/5 flex flex-col gap-2 min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-[11px] font-semibold uppercase tracking-widest"
                    :class="axis === detailStory.base_time_axis ? 'text-[var(--sb-amber)]' : 'text-[var(--sb-muted)]'">
                    {{ t('chronicle.axis.' + axis) }}
                    <span v-if="axis === detailStory.base_time_axis"
                      class="text-[var(--sb-muted)] normal-case font-normal ml-1">({{ t('storybook.base') }})</span>
                  </span>
                  <span v-if="draftRichnessFor(detailStory, axis)"
                    class="text-[9px] font-mono text-[var(--sb-faint)]"
                    :title="t('storybook.draftDelta')">
                    {{ t('storybook.draftDelta') }} {{ formatDraftDelta(draftRichnessFor(detailStory, axis)) }}
                  </span>
                </div>
                <p v-if="axisSlotLabel(detailStory, axis)"
                  class="text-[10px] text-[var(--sb-muted)] leading-snug"
                  :title="t('storybook.axisSlot')">
                  {{ axisSlotLabel(detailStory, axis) }}
                </p>
                <p class="sb-prose">
                  {{ axisStory(detailStory, axis) || '—' }}
                </p>
                <div v-if="axisHasVisualSpec(detailStory.axes?.[axis])"
                  class="mt-2 border-l-2 border-[var(--sb-rule)] pl-3 space-y-1.5">
                  <p class="sb-label">{{ t('chronicle.visualSpecTitle') }}</p>
                  <p v-if="detailStory.axes[axis].visual_script"
                    class="text-[11px] text-[var(--sb-muted)] whitespace-pre-wrap leading-relaxed">
                    {{ detailStory.axes[axis].visual_script }}
                  </p>
                  <p v-for="g in CAT_TAG_GROUPS" :key="g.key"
                    v-show="(detailStory.axes[axis][g.key] || []).length"
                    class="text-[10px]">
                    <span class="text-[var(--sb-faint)]">{{ t('chronicle.' + g.label) }}:</span>
                    <span class="font-mono text-gray-400">{{ (detailStory.axes[axis][g.key] || []).join(', ') }}</span>
                  </p>
                </div>
                <details v-if="detailStory.axes?.[axis]?.prompt_positive" class="mt-1">
                  <summary class="cursor-pointer text-[10px] text-[var(--sb-muted)] hover:text-gray-300 select-none">
                    {{ t('storybook.showPrompt') }}
                  </summary>
                  <div class="mt-2 flex flex-col gap-1">
                    <pre class="text-[10px] text-gray-400 whitespace-pre-wrap font-mono bg-black/40 rounded-lg p-2.5">{{ detailStory.axes[axis].prompt_positive }}</pre>
                    <pre v-if="detailStory.axes[axis].prompt_negative"
                      class="text-[10px] text-gray-500 whitespace-pre-wrap font-mono bg-black/40 rounded-lg p-2.5">{{ detailStory.axes[axis].prompt_negative }}</pre>
                  </div>
                </details>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>

    <!-- Pinup lightbox -->
    <div v-if="pinupView"
      class="fixed inset-0 z-[var(--z-panel-media)] bg-black/85 flex items-center justify-center p-8"
      @click.self="pinupView = null">
      <div class="pincard pincard--large" @click="pinupView = null">
        <span class="pincard-pin"></span>
        <img :src="`/api/originals/${pinupView}`" @error="onThumbError($event, pinupView)" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.storybook-root {
  --sb-font-display: "Shippori Mincho", "Hiragino Mincho ProN", "Yu Mincho", serif;
  --sb-font-ui: "IBM Plex Sans JP", "Hiragino Sans", "Noto Sans JP", sans-serif;
  --sb-amber: #e8c47a;
  --sb-ink: #0c0e12;
  --sb-panel: #141820;
  --sb-rule: rgba(232, 196, 122, 0.22);
  --sb-muted: #8b929e;
  --sb-faint: #5c6470;
  font-family: var(--sb-font-ui);
  color: #e5e7eb;
  color-scheme: dark;
  background: radial-gradient(ellipse at 30% 20%, rgba(232, 196, 122, 0.07), transparent 50%),
              rgba(0, 0, 0, 0.82);
}

.sb-display { font-family: var(--sb-font-display); font-weight: 600; }
.sb-prose {
  font-size: 0.9375rem;
  line-height: 1.7;
  color: #e5e7eb;
  white-space: pre-wrap;
}
.sb-shell {
  background:
    linear-gradient(165deg, rgba(232, 196, 122, 0.06) 0%, transparent 42%),
    var(--sb-panel);
  border: 1px solid rgba(232, 196, 122, 0.18);
  border-radius: 1rem;
  box-shadow: 0 20px 56px rgba(0, 0, 0, 0.48);
}
.sb-hairline { border-bottom: 1px solid var(--sb-rule); }
.sb-section { border-bottom: 1px solid rgba(232, 196, 122, 0.1); }
.sb-section-title {
  font-family: var(--sb-font-display);
  font-size: 0.8rem;
  letter-spacing: 0.06em;
  color: rgba(232, 196, 122, 0.85);
  font-weight: 600;
}
.sb-body { color: #d1d5db; }

.sb-card {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 1rem;
  padding: 0.75rem;
  transition: transform 0.35s cubic-bezier(0.2, 0.8, 0.2, 1),
              border-color 0.35s, box-shadow 0.35s;
}
.storybook-card:hover {
  transform: translateY(-3px);
  border-color: rgba(232, 196, 122, 0.28);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25);
}

.sb-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.35rem 0.65rem;
  border-radius: 0.5rem;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.25);
  color: #d1d5db;
  font-size: 0.7rem;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.sb-btn:hover { background: rgba(255, 255, 255, 0.06); border-color: rgba(232, 196, 122, 0.25); }
.sb-btn-accent {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.25rem 0.55rem;
  border-radius: 0.5rem;
  border: 1px solid rgba(232, 196, 122, 0.35);
  background: rgba(146, 64, 14, 0.28);
  color: #fef3c7;
  font-size: 0.65rem;
  transition: background 0.15s;
}
.sb-btn-accent:hover { background: rgba(146, 64, 14, 0.42); }
.sb-icon-btn {
  width: 2rem; height: 2rem;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 0.5rem;
  color: var(--sb-muted);
  transition: background 0.15s, color 0.15s;
}
.sb-icon-btn:hover { background: rgba(255, 255, 255, 0.06); color: #e5e7eb; }
.sb-icon-btn-sm {
  width: 1.6rem; height: 1.6rem;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 0.4rem;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(0, 0, 0, 0.25);
  transition: background 0.15s, color 0.15s;
}
.sb-icon-btn-sm:hover { background: rgba(255, 255, 255, 0.08); }
.sb-link {
  font-size: 0.7rem;
  color: var(--sb-amber);
  opacity: 0.85;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.sb-link:hover { opacity: 1; }

.sb-seg {
  display: inline-flex;
  overflow: hidden;
  border-radius: 0.5rem;
  border: 1px solid rgba(255, 255, 255, 0.08);
}
.sb-seg-btn {
  padding: 0.35rem 0.7rem;
  font-size: 0.7rem;
  background: rgba(0, 0, 0, 0.3);
  color: var(--sb-muted);
  transition: background 0.15s, color 0.15s;
}
.sb-seg-btn:hover { background: rgba(255, 255, 255, 0.05); }
.sb-seg-btn.is-on { background: rgba(146, 64, 14, 0.55); color: #fef3c7; }
.sb-seg-btn.is-on-teal { background: rgba(19, 78, 74, 0.65); color: #ccfbf1; }

.sb-search {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.6rem;
  border-radius: 0.5rem;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.3);
}
.sb-search input {
  background: transparent;
  outline: none;
  font-size: 0.75rem;
  color: #e5e7eb;
  width: 12rem;
}
.sb-select {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 0.5rem;
  padding: 0.35rem 0.55rem;
  font-size: 0.75rem;
  color: #e5e7eb;
  outline: none;
}
.sb-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--sb-muted);
}
.sb-chip {
  padding: 0.2rem 0.55rem;
  border-radius: 0.35rem;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(0, 0, 0, 0.25);
  color: var(--sb-muted);
  font-size: 0.625rem;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.sb-chip:hover { background: rgba(255, 255, 255, 0.05); }
.sb-chip.is-chip-on { background: rgba(146, 64, 14, 0.5); color: #fef3c7; border-color: rgba(245, 158, 11, 0.35); }
.sb-chip.is-chip-on-teal { background: rgba(19, 78, 74, 0.55); color: #ccfbf1; border-color: rgba(45, 212, 191, 0.3); }
.sb-chip.is-chip-on-indigo {
  background: rgba(255, 255, 255, 0.06);
  color: #e5e7eb;
  border-color: rgba(232, 196, 122, 0.25);
}

.sb-meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.15rem 0.45rem;
  border-radius: 0.35rem;
  background: rgba(0, 0, 0, 0.35);
  max-width: 100%;
}
.sb-meta-motif { color: var(--sb-amber); opacity: 0.9; }
.sb-meta-scale { color: #7dd3c7; }
.sb-meta-emotion { color: var(--sb-muted); }
.sb-meta-topic { color: rgba(232, 196, 122, 0.85); }
.sb-card-meta-more { min-height: 1.1rem; }

.sb-icon { display: inline-block; flex-shrink: 0; }

.sb-overlay-bg {
  background: radial-gradient(ellipse at 50% 30%, rgba(232, 196, 122, 0.08), transparent 55%),
              rgba(0, 0, 0, 0.78);
  backdrop-filter: blur(2px);
}
.sb-detail-panel {
  /* Local tokens too — detail is outside .storybook-root in the Teleport tree. */
  --sb-amber: #e8c47a;
  --sb-muted: #8b929e;
  --sb-faint: #5c6470;
  --sb-rule: rgba(232, 196, 122, 0.22);
  color: #e5e7eb;
  background:
    linear-gradient(180deg, rgba(232, 196, 122, 0.07) 0%, transparent 28%),
    #0f1218;
  border: 1px solid rgba(232, 196, 122, 0.2);
  border-radius: 0.9rem;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
}

.sb-overlay-enter-active,
.sb-overlay-leave-active {
  transition: opacity 0.22s ease;
}
.sb-overlay-enter-active .sb-detail-panel,
.sb-overlay-leave-active .sb-detail-panel {
  transition: transform 0.28s cubic-bezier(0.2, 0.8, 0.2, 1), opacity 0.22s ease;
}
.sb-overlay-enter-from,
.sb-overlay-leave-to { opacity: 0; }
.sb-overlay-enter-from .sb-detail-panel,
.sb-overlay-leave-to .sb-detail-panel {
  transform: translateY(10px) scale(0.985);
  opacity: 0;
}

/* Polaroid stack */
.polaroid-stack {
  position: relative;
  aspect-ratio: 1 / 1;
  width: 100%;
  perspective: 900px;
}
.polaroid {
  position: absolute;
  top: 12%;
  left: 16%;
  width: 68%;
  height: 68%;
  background: #e4e0d6;
  border: 4px solid #e4e0d6;
  border-bottom-width: 22px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.45);
  transition: transform 0.45s cubic-bezier(0.2, 0.8, 0.2, 1),
              box-shadow 0.45s;
  overflow: hidden;
}
.polaroid img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.polaroid-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #94a3b8;
  font-size: 0.7rem;
  font-family: var(--sb-font-ui);
  padding: 0.5rem;
  text-align: center;
}
.polaroid.past    { transform: translate(-16%, -6%) rotate(-6deg); z-index: 1; }
.polaroid.present { transform: translate(0, 0)       rotate(2deg);  z-index: 2; }
.polaroid.future  { transform: translate(16%, 6%)    rotate(8deg);  z-index: 3; }
.polaroid.base {
  box-shadow: 0 0 0 2px rgba(232, 196, 122, 0.65),
              0 8px 20px rgba(0, 0, 0, 0.45);
}
.storybook-card:hover .polaroid-stack .polaroid.past    { transform: translate(-42%, -2%) rotate(-4deg); }
.storybook-card:hover .polaroid-stack .polaroid.present { transform: translate(0, -2%)     rotate(0deg);  }
.storybook-card:hover .polaroid-stack .polaroid.future  { transform: translate(42%, -2%)  rotate(4deg);  }
.polaroid-stack-sm .polaroid { border-width: 3px; border-bottom-width: 14px; }

.variant-card {
  transition: transform 0.25s ease, border-color 0.25s, box-shadow 0.25s;
}
.variant-card:hover {
  transform: translateY(-2px);
  border-color: rgba(232, 196, 122, 0.35);
}
.variant-card--picked {
  box-shadow: 0 0 0 1px rgba(232, 196, 122, 0.25);
}

.mood-wall-board {
  min-height: 14rem;
}
.mood-pin {
  width: auto;
}
.mood-pin img {
  width: 7rem;
  height: 9rem;
}
.mood-pin-caption {
  position: absolute;
  left: 0.4rem;
  right: 0.4rem;
  bottom: 0.35rem;
  display: flex;
  flex-direction: column;
  gap: 0.05rem;
  font-size: 0.55rem;
  line-height: 1.2;
  color: #444;
  font-family: var(--sb-font-ui);
}

/* Pinboard */
.pinboard {
  display: flex;
  flex-wrap: wrap;
  gap: 1.1rem 1.4rem;
  padding: 1.25rem 1.1rem;
  border-radius: 0.65rem;
  background-color: #8a7358;
  background-image:
    radial-gradient(rgba(0, 0, 0, 0.16) 1px, transparent 1.4px),
    radial-gradient(rgba(255, 255, 255, 0.07) 1px, transparent 1.4px),
    linear-gradient(135deg, rgba(255, 220, 170, 0.08), transparent 50%);
  background-size: 9px 9px, 9px 9px, auto;
  background-position: 0 0, 4.5px 4.5px, 0 0;
  box-shadow: inset 0 0 36px rgba(0, 0, 0, 0.35),
              0 2px 8px rgba(0, 0, 0, 0.35);
}
.pincard {
  position: relative;
  background: #faf8f3;
  padding: 0.4rem 0.4rem 1.4rem;
  box-shadow: 0 6px 14px rgba(0, 0, 0, 0.45);
  cursor: pointer;
  transition: transform 0.22s ease, box-shadow 0.22s ease;
  animation: sb-pin-settle 0.5s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}
@keyframes sb-pin-settle {
  from { opacity: 0; transform: translateY(-6px) scale(0.96); }
  to { opacity: 1; }
}
.pincard:hover {
  transform: rotate(0deg) scale(1.06) !important;
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.55);
  z-index: 2;
}
.pincard img {
  display: block;
  width: 6rem;
  height: 8rem;
  object-fit: cover;
  background: #e5e5e5;
}
.pincard-pin {
  position: absolute;
  top: -7px;
  left: 50%;
  transform: translateX(-50%);
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 30%, #ff7a72, #c42e28);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(0, 0, 0, 0.15);
}
.pincard--loading {
  width: 6.8rem;
  height: 9.4rem;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pincard-spin { color: #b0b0b0; font-size: 1.6rem; letter-spacing: 2px; }
.pincard--large {
  padding: 0.9rem 0.9rem 3rem;
  cursor: zoom-out;
}
.pincard--large img {
  width: min(70vw, 460px);
  height: auto;
  max-height: 72vh;
}

.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>

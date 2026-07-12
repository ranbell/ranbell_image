<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { EMOTION_DIMENSIONS } from '../composables/useInvokeSession.js'

const { t, locale } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['update:show', 'select-image', 'weave-from', 'toast'])

const AXES = ['past', 'present', 'future']
const TIME_SCALES = ['minutes', 'tens_of_minutes', 'hours', 'days', 'months', 'years', 'decades']
const VIEW_MODES = ['gallery', 'detail', 'timeline']
const SORTS = ['newest', 'oldest', 'title', 'time_scale']

// ── data ──────────────────────────────────────────────────────────────────────
const stories = ref([])
const loading = ref(false)
const regenerating = ref(new Set())
// Content-language toggle (story title/body/date). Defaults to the global UI
// locale and follows it when the app language is switched, but can be overridden
// per view via the JA/EN buttons for bilingual browsing.
const lang = ref(locale.value?.startsWith('ja') ? 'ja' : 'en')
watch(locale, (l) => { lang.value = l?.startsWith('ja') ? 'ja' : 'en' })

// ── view / filter state (persisted to localStorage) ───────────────────────────
const _saved = (() => { try { return JSON.parse(localStorage.getItem('storybook.ui') || '{}') } catch { return {} } })()
const viewMode = ref(VIEW_MODES.includes(_saved.viewMode) ? _saved.viewMode : 'gallery')
const query = ref(_saved.query || '')
const sort = ref(SORTS.includes(_saved.sort) ? _saved.sort : 'newest')
const filters = ref({
  base_axis: _saved.filters?.base_axis || '',
  time_scale: _saved.filters?.time_scale || '',
  emotion: _saved.filters?.emotion || '',
})

watch([viewMode, query, sort, filters], () => {
  try {
    localStorage.setItem('storybook.ui', JSON.stringify({
      viewMode: viewMode.value, query: query.value, sort: sort.value,
      filters: filters.value,
    }))
  } catch {}
}, { deep: true })

// ── i18n-aware getters (unchanged) ────────────────────────────────────────────
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

// ── filter / sort → visibleStories ────────────────────────────────────────────
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
  const list = stories.value.filter(s => {
    if (f.base_axis && s.base_time_axis !== f.base_axis) return false
    if (f.time_scale && s.time_scale !== f.time_scale) return false
    if (f.emotion && s.emotion !== f.emotion) return false
    if (!matches(s, q)) return false
    return true
  })
  const cmpTitle = (a, b) => (storyTitle(a) || '').localeCompare(storyTitle(b) || '', lang.value)
  const cmpScale = (a, b) => TIME_SCALES.indexOf(a.time_scale) - TIME_SCALES.indexOf(b.time_scale)
  switch (sort.value) {
    case 'oldest':     return [...list].sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
    case 'title':      return [...list].sort(cmpTitle)
    case 'time_scale': return [...list].sort(cmpScale)
    default:           return [...list].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
  }
})

// ── timeline buckets ─────────────────────────────────────────────────────────
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
  // Timeline is always chronological (newest first), independent of the sort dropdown
  const chrono = [...visibleStories.value].sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
  const map = new Map()
  for (const s of chrono) {
    const b = bucketFor(s.created_at)
    if (!map.has(b.key)) map.set(b.key, { ...b, stories: [] })
    map.get(b.key).stories.push(s)
  }
  return [...map.values()].sort((a, b) => a.order - b.order)
})

// ── fetching / actions ────────────────────────────────────────────────────────
watch(() => props.show, (val) => { if (val) fetchStories() })

function close() { emit('update:show', false) }

async function fetchStories() {
  loading.value = true
  try {
    const r = await fetch('/api/story/storybook?limit=200')
    if (r.ok) stories.value = (await r.json()).stories
  } catch {}
  loading.value = false
}

async function regenerate(story, axis) {
  const key = `${story.story_id}:${axis}`
  regenerating.value = new Set([...regenerating.value, key])
  try {
    const r = await fetch(`/api/story/${story.story_id}/regenerate/${axis}`, { method: 'POST' })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    emit('toast', { msg: t('storybook.regenQueued'), type: 'success' })
  } catch (err) {
    emit('toast', { msg: String(err.message || err), type: 'error' })
    const next = new Set(regenerating.value)
    next.delete(key)
    regenerating.value = next
  }
}

function axisImage(story, axis) {
  return story.axes?.[axis]?.image_id || null
}

function openImage(sha256) {
  if (sha256) emit('select-image', sha256)
}

// ── Pinups (polaroids on the corkboard) ──────────────────────────────────────
const pinupView = ref(null)        // sha256 shown enlarged in the polaroid lightbox
const pinupBusy = ref(new Set())   // story_ids with a pinup job in flight
const PINUP_ROT = [-5, 4, -3, 6, -6, 3, -4, 5]
function pinupRotation(i) { return PINUP_ROT[i % PINUP_ROT.length] }
function storyPinups(story) {
  if (Array.isArray(story.pinups) && story.pinups.length) return story.pinups.filter(Boolean)
  return story.pinup_image_id ? [story.pinup_image_id] : []
}
function openPinup(sha) { if (sha) pinupView.value = sha }

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

// Open the detail overlay, then refetch so the newest images (axes + pinups)
// show even if they finished generating after the gallery list was loaded.
function openDetail(story) {
  detailStory.value = story
  refetchStory(story.story_id)
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
    // Poll until the new (add) or swapped (replace) pinup lands.
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
  if (focusStoryId.value === story.story_id) focusStoryId.value = ''
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
  filters.value = { base_axis: '', time_scale: '', emotion: '' }
  query.value = ''
}

// ── detail overlay (existing behaviour, stays as-is) ──────────────────────────
const detailStory = ref(null)

// ── focus mode ────────────────────────────────────────────────────────────────
const focusStoryId = ref('')
const focusStory = computed(() =>
  focusStoryId.value ? visibleStories.value.find(s => s.story_id === focusStoryId.value) : null
)
const focusIndex = computed(() => {
  if (!focusStoryId.value) return -1
  return visibleStories.value.findIndex(s => s.story_id === focusStoryId.value)
})
function focusOpen(story) { focusStoryId.value = story.story_id }
function focusClose() { focusStoryId.value = '' }
function focusPrev() {
  const i = focusIndex.value
  if (i > 0) focusStoryId.value = visibleStories.value[i - 1].story_id
}
function focusNext() {
  const i = focusIndex.value
  if (i >= 0 && i < visibleStories.value.length - 1) focusStoryId.value = visibleStories.value[i + 1].story_id
}

function onKey(e) {
  if (!props.show) return
  if (focusStoryId.value) {
    if (e.key === 'ArrowLeft') { focusPrev(); e.preventDefault() }
    else if (e.key === 'ArrowRight') { focusNext(); e.preventDefault() }
    else if (e.key === 'Escape') { focusClose(); e.preventDefault() }
  } else if (detailStory.value) {
    if (e.key === 'Escape') { detailStory.value = null; e.preventDefault() }
  } else if (e.key === 'Escape') {
    close()
  }
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[65] bg-black/80 flex items-center justify-center p-4"
      @click.self="close">
      <div class="relative bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-6xl max-h-[92vh] flex flex-col">

        <!-- ── header ────────────────────────────────────────────────────── -->
        <div class="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h2 class="text-base font-bold text-amber-300">📖 {{ t('storybook.title') }}</h2>
          <div class="flex items-center gap-2">
            <div class="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
              <button v-for="l in ['ja', 'en']" :key="l" @click="lang = l"
                :class="lang === l ? 'bg-amber-800/70 text-amber-100' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
                class="px-2.5 py-1.5 transition-colors uppercase">{{ l }}</button>
            </div>
            <button @click="fetchStories"
              class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs text-gray-300 transition-colors">
              ⟳ {{ t('storybook.refresh') }}
            </button>
            <button @click="close"
              class="text-gray-600 hover:text-gray-200 text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
          </div>
        </div>

        <!-- ── toolbar: view mode | search | sort | filter chips ─────────── -->
        <div class="flex flex-wrap items-center gap-2 px-5 py-2.5 border-b border-gray-800 bg-gray-900/50">
          <div class="flex rounded-lg overflow-hidden border border-gray-700 text-[11px]">
            <button v-for="m in VIEW_MODES" :key="m" @click="viewMode = m"
              :class="viewMode === m ? 'bg-teal-800/70 text-teal-100' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
              class="px-3 py-1.5 transition-colors">
              {{ t('storybook.view.' + m) }}
            </button>
          </div>

          <label class="flex items-center gap-1.5 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1">
            <span class="text-gray-500 text-xs">🔍</span>
            <input v-model="query" type="search" :placeholder="t('storybook.searchPh')"
              class="bg-transparent text-xs text-gray-200 outline-none w-48" />
          </label>

          <select v-model="sort"
            class="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 focus:border-teal-500 outline-none">
            <option v-for="s in SORTS" :key="s" :value="s">
              ⌛ {{ t('storybook.sort.' + s) }}
            </option>
          </select>

          <div class="flex items-center gap-1 flex-wrap">
            <span class="text-[10px] text-gray-500 uppercase tracking-wide">{{ t('storybook.filter.baseAxis') }}</span>
            <button v-for="a in AXES" :key="a"
              @click="filters.base_axis = filters.base_axis === a ? '' : a"
              :class="filters.base_axis === a ? 'bg-amber-800/70 text-amber-100 border-amber-500/50' : 'bg-gray-800 text-gray-400 border-gray-700 hover:bg-gray-700'"
              class="px-2 py-0.5 rounded-full border text-[10px] transition-colors">
              {{ t('chronicle.axis.' + a) }}
            </button>
          </div>

          <details class="text-[10px]">
            <summary class="cursor-pointer text-gray-400 hover:text-gray-200 select-none px-2 py-1">
              🏷 {{ t('storybook.filter.more') }}
            </summary>
            <div class="absolute z-30 mt-1 bg-gray-950 border border-gray-700 rounded-lg p-3 flex flex-col gap-2 shadow-xl">
              <div class="flex items-center gap-1 flex-wrap max-w-md">
                <span class="text-gray-500 uppercase tracking-wide w-20">{{ t('storybook.filter.timeScale') }}</span>
                <button v-for="ts in TIME_SCALES" :key="ts"
                  @click="filters.time_scale = filters.time_scale === ts ? '' : ts"
                  :class="filters.time_scale === ts ? 'bg-teal-800/70 text-teal-100 border-teal-500/50' : 'bg-gray-800 text-gray-400 border-gray-700 hover:bg-gray-700'"
                  class="px-2 py-0.5 rounded-full border transition-colors">
                  {{ t('chronicle.timeScale.' + ts) }}
                </button>
              </div>
              <div class="flex items-center gap-1 flex-wrap max-w-md">
                <span class="text-gray-500 uppercase tracking-wide w-20">{{ t('storybook.filter.emotion') }}</span>
                <button v-for="em in EMOTION_DIMENSIONS" :key="em"
                  @click="filters.emotion = filters.emotion === em ? '' : em"
                  :class="filters.emotion === em ? 'bg-indigo-800/70 text-indigo-100 border-indigo-500/50' : 'bg-gray-800 text-gray-400 border-gray-700 hover:bg-gray-700'"
                  class="px-2 py-0.5 rounded-full border transition-colors">
                  {{ t(`inspire.emotion.${em}`) }}
                </button>
              </div>
            </div>
          </details>

          <button v-if="query || filters.base_axis || filters.time_scale || filters.emotion"
            @click="clearFilters"
            class="ml-auto px-2 py-1 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-[10px] text-gray-300 transition-colors">
            ✕ {{ t('storybook.filter.clear') }}
          </button>
        </div>

        <!-- ── body ──────────────────────────────────────────────────────── -->
        <div class="flex-1 overflow-y-auto p-5 flex flex-col gap-5">
          <p v-if="loading" class="text-xs text-gray-500">{{ t('storybook.loading') }}</p>
          <p v-else-if="!stories.length" class="text-xs text-gray-500">{{ t('storybook.empty') }}</p>
          <p v-else-if="!visibleStories.length" class="text-xs text-gray-500">{{ t('storybook.emptyFiltered') }}</p>

          <!-- ── GALLERY MODE — Polaroid stacks ─────────────────────────── -->
          <div v-if="viewMode === 'gallery' && visibleStories.length"
            class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-5">
            <div v-for="story in visibleStories" :key="story.story_id"
              class="storybook-card group bg-gray-800/40 border border-gray-800 rounded-2xl p-3 flex flex-col gap-2 cursor-pointer transition-all"
              @click="focusOpen(story)">

              <div class="polaroid-stack">
                <div v-for="axis in AXES" :key="axis"
                  class="polaroid" :class="[axis, axis === story.base_time_axis ? 'base' : '']">
                  <img v-if="axisImage(story, axis)"
                    :src="`/api/thumbnails/${axisImage(story, axis)}.webp`"
                    @error="onThumbError($event, axisImage(story, axis))" loading="lazy" />
                  <span v-else class="polaroid-empty">⏳</span>
                </div>
              </div>

              <!-- footer -->
              <div class="flex flex-col gap-1">
                <div class="flex items-center gap-1.5">
                  <h3 class="text-xs font-bold text-amber-200 truncate flex-1">
                    {{ storyTitle(story) || '—' }}
                  </h3>
                </div>
                <div class="flex items-center gap-1 flex-wrap text-[9px]">
                  <span v-if="motifOf(story)"
                    class="px-1.5 py-0.5 rounded bg-gray-900/70 text-purple-300">
                    ✦ {{ motifOf(story) }}
                  </span>
                  <span v-if="story.time_scale" class="px-1.5 py-0.5 rounded bg-gray-900/70 text-teal-300">
                    ⏳ {{ t('chronicle.timeScale.' + story.time_scale) }}
                  </span>
                  <span v-if="story.emotion" class="px-1.5 py-0.5 rounded bg-gray-900/70 text-indigo-300">
                    🌒 {{ t(`inspire.emotion.${story.emotion}`, story.emotion) }}
                  </span>
                  <span class="ml-auto text-gray-600 font-mono">{{ formatDate(story.created_at).split(' ')[0] }}</span>
                </div>
                <!-- hover-visible action row -->
                <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button @click.stop="openDetail(story)"
                    :title="t('storybook.details')"
                    class="px-1.5 py-0.5 rounded bg-gray-900/70 hover:bg-gray-800 border border-gray-700 text-[9px] text-gray-300">
                    📄
                  </button>
                  <button v-if="axisImage(story, story.base_time_axis)"
                    @click.stop="emit('weave-from', axisImage(story, story.base_time_axis))"
                    :title="t('storybook.weaveFrom')"
                    class="px-1.5 py-0.5 rounded bg-teal-900/70 hover:bg-teal-800 border border-teal-700 text-[9px] text-teal-200">
                    🧶
                  </button>
                  <button @click.stop="deleteStory(story)"
                    :title="t('storybook.delete')"
                    class="ml-auto px-1.5 py-0.5 rounded bg-red-900/40 hover:bg-red-800/70 border border-red-800/50 text-[9px] text-red-300">
                    🗑
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- ── TIMELINE MODE — buckets with horizontal strips ─────────── -->
          <div v-if="viewMode === 'timeline' && visibleStories.length" class="flex flex-col gap-4">
            <div v-for="bucket in timelineBuckets" :key="bucket.key" class="flex flex-col gap-2">
              <h3 class="sticky top-0 z-10 backdrop-blur bg-gray-900/80 py-1.5 text-[11px] font-bold uppercase tracking-wider text-amber-300 border-b border-gray-800/70">
                🕰 {{ bucket.label }} <span class="text-gray-500 font-normal normal-case">({{ bucket.stories.length }})</span>
              </h3>
              <div class="flex gap-3 overflow-x-auto pb-2 snap-x">
                <div v-for="story in bucket.stories" :key="story.story_id"
                  class="storybook-card group shrink-0 w-48 snap-start bg-gray-800/40 border border-gray-800 rounded-xl p-2.5 flex flex-col gap-1.5 cursor-pointer transition-all"
                  @click="focusOpen(story)">
                  <div class="polaroid-stack polaroid-stack-sm">
                    <div v-for="axis in AXES" :key="axis"
                      class="polaroid" :class="[axis, axis === story.base_time_axis ? 'base' : '']">
                      <img v-if="axisImage(story, axis)"
                        :src="`/api/thumbnails/${axisImage(story, axis)}.webp`"
                        @error="onThumbError($event, axisImage(story, axis))" loading="lazy" />
                      <span v-else class="polaroid-empty">⏳</span>
                    </div>
                  </div>
                  <h4 class="text-[11px] font-bold text-amber-200 truncate">{{ storyTitle(story) || '—' }}</h4>
                  <div class="flex items-center gap-1 text-[9px]">
                    <span v-if="motifOf(story)"
                      class="px-1.5 py-0.5 rounded bg-gray-900/70 text-purple-300 truncate">✦ {{ motifOf(story) }}</span>
                    <span class="ml-auto text-gray-600 font-mono">{{ new Date(story.created_at * 1000).toLocaleTimeString(lang === 'ja' ? 'ja-JP' : 'en-US', { hour: '2-digit', minute: '2-digit' }) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- ── DETAIL MODE — the original long-form layout ────────────── -->
          <div v-if="viewMode === 'detail' && visibleStories.length" class="flex flex-col gap-5">
            <div v-for="story in visibleStories" :key="story.story_id"
              class="bg-gray-800/40 border border-gray-800 rounded-2xl p-4 flex flex-col gap-3">
              <div v-if="storyTitle(story)" class="flex flex-col gap-1.5">
                <h3 class="text-sm font-bold text-amber-200">{{ storyTitle(story) }}</h3>
                <p v-if="storyOverall(story)"
                  class="text-[11px] text-gray-300 leading-relaxed whitespace-pre-wrap border-l-2 border-amber-700/40 pl-3">
                  {{ storyOverall(story) }}
                </p>
              </div>
              <div class="flex items-center gap-3 text-[10px] text-gray-500 flex-wrap">
                <span v-if="story.worldview" class="text-amber-400/80">🌍 {{ story.worldview }}</span>
                <span v-if="story.time_scale" class="text-teal-400/70">⏳ ± {{ t('chronicle.timeScale.' + story.time_scale) }}</span>
                <span v-if="story.emotion" class="text-indigo-400/80">🌒 {{ t(`inspire.emotion.${story.emotion}`, story.emotion) }}</span>
                <button @click="openDetail(story)"
                  class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-full text-gray-300 transition-colors">
                  📄 {{ t('storybook.details') }}
                </button>
                <button @click="focusOpen(story)"
                  class="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-full text-gray-300 transition-colors">
                  🎯 {{ t('storybook.view.focus') }}
                </button>
                <button @click="deleteStory(story)"
                  class="px-2 py-0.5 bg-red-900/30 hover:bg-red-800/60 border border-red-800/40 rounded-full text-red-400 transition-colors">
                  🗑 {{ t('storybook.delete') }}
                </button>
                <span class="ml-auto font-mono">{{ formatDate(story.created_at) }}</span>
              </div>

              <details v-if="story.candidates?.length" class="text-[11px]">
                <summary class="cursor-pointer text-gray-500 hover:text-gray-300 select-none">
                  💭 {{ t('storybook.otherCandidates') }} ({{ story.candidates.length }})
                </summary>
                <div class="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div v-for="c in story.candidates" :key="c.id"
                    class="p-2 rounded-lg border"
                    :class="c.id === story.selected_candidate ? 'border-amber-600/50 bg-amber-900/10' : 'border-gray-800 bg-gray-900/40'">
                    <div class="flex items-center gap-1.5">
                      <span class="text-[9px] font-bold w-4 h-4 flex items-center justify-center rounded-full bg-gray-700 text-gray-200">{{ c.id }}</span>
                      <span class="text-[11px] font-bold text-amber-100 leading-tight">{{ c.title }}</span>
                      <span v-if="c.id === story.selected_candidate" class="text-[9px] text-amber-400 ml-auto">✓</span>
                    </div>
                    <p class="text-[10px] text-gray-400 mt-1 leading-snug">{{ c.summary }}</p>
                  </div>
                </div>
              </details>

              <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div v-for="axis in AXES" :key="axis"
                  class="rounded-xl border p-3 flex flex-col gap-2"
                  :class="axis === story.base_time_axis ? 'border-amber-600/50 bg-amber-900/10' : 'border-gray-800 bg-gray-900/40'">
                  <div class="flex items-center justify-between">
                    <span class="text-[10px] font-bold uppercase tracking-wide"
                      :class="axis === story.base_time_axis ? 'text-amber-400' : 'text-teal-400'">
                      {{ t('chronicle.axis.' + axis) }}
                      <span v-if="axis === story.base_time_axis" class="text-gray-500 normal-case font-normal ml-1">({{ t('storybook.base') }})</span>
                    </span>
                    <button v-if="axis !== story.base_time_axis && story.axes?.[axis]?.prompt_positive"
                      @click="regenerate(story, axis)"
                      :disabled="regenerating.has(`${story.story_id}:${axis}`)"
                      :title="t('storybook.regenTitle')"
                      class="text-[10px] px-2 py-0.5 bg-purple-900/60 hover:bg-purple-800/70 disabled:opacity-40 border border-purple-700/50 rounded-full text-purple-200 transition-colors">
                      🎲 {{ regenerating.has(`${story.story_id}:${axis}`) ? t('storybook.regenQueuedShort') : t('storybook.regen') }}
                    </button>
                  </div>

                  <div class="relative group aspect-square bg-gray-950/60 rounded-lg overflow-hidden flex items-center justify-center cursor-pointer"
                    @click="openImage(axisImage(story, axis))">
                    <img v-if="axisImage(story, axis)" :src="`/api/thumbnails/${axisImage(story, axis)}.webp`"
                      @error="onThumbError($event, axisImage(story, axis))"
                      class="w-full h-full object-cover hover:opacity-90 transition-opacity" loading="lazy" />
                    <span v-else class="text-2xl text-gray-700">⏳</span>
                    <button v-if="axisImage(story, axis)"
                      @click.stop="emit('weave-from', axisImage(story, axis))"
                      :title="t('storybook.weaveFrom')"
                      class="absolute bottom-1.5 right-1.5 px-2 py-1 bg-teal-900/80 hover:bg-teal-700/90 border border-teal-600/50 rounded-lg text-[10px] text-teal-200 opacity-0 group-hover:opacity-100 transition-opacity">
                      📜 {{ t('storybook.weaveFromShort') }}
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

        <!-- ── detail overlay (kept from previous UI) ─────────────────────── -->
        <div v-if="detailStory"
          class="absolute inset-0 z-20 bg-black/80 flex items-center justify-center p-4 rounded-2xl"
          @click.self="detailStory = null">
          <div class="bg-gray-950 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-4xl max-h-full flex flex-col">
            <div class="flex items-center justify-between px-6 py-4 border-b border-gray-800 gap-4">
              <div class="min-w-0 flex-1">
                <h2 class="text-lg font-bold text-amber-200 leading-tight truncate">
                  {{ storyTitle(detailStory) || t('storybook.details') }}
                </h2>
                <div class="flex items-center gap-3 mt-1 text-[10px] text-gray-500">
                  <span v-if="detailStory.worldview">🌍 {{ detailStory.worldview }}</span>
                  <span v-if="detailStory.time_scale">⏳ {{ t('chronicle.timeScale.' + detailStory.time_scale) }}</span>
                  <span class="font-mono">{{ formatDate(detailStory.created_at) }}</span>
                </div>
              </div>
              <div class="flex items-center gap-2 shrink-0">
                <div class="flex rounded-lg overflow-hidden border border-gray-700 text-[10px]">
                  <button v-for="l in ['ja', 'en']" :key="l" @click="lang = l"
                    :class="lang === l ? 'bg-amber-800/70 text-amber-100' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
                    class="px-2.5 py-1.5 transition-colors uppercase">{{ l }}</button>
                </div>
                <button @click="detailStory = null"
                  class="text-gray-600 hover:text-gray-200 text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
              </div>
            </div>
            <div class="flex-1 overflow-y-auto divide-y divide-gray-800/50">
              <div v-if="storyOverall(detailStory)" class="px-8 py-5">
                <p class="text-gray-300 leading-relaxed text-sm italic border-l-2 border-amber-700/50 pl-4">
                  {{ storyOverall(detailStory) }}
                </p>
              </div>
              <!-- Biography -->
              <div v-if="storyBio(detailStory)" class="px-8 py-5">
                <div class="flex items-center justify-between mb-2">
                  <h4 class="text-xs font-semibold text-purple-300/80 tracking-wide">📖 {{ t('storybook.biography') }}</h4>
                  <div class="flex gap-2">
                    <button @click="addPinup(detailStory, 'add')"
                      :disabled="pinupBusy.has(detailStory.story_id)"
                      class="px-2 py-1 rounded-lg border border-gray-700/50 text-[10px] text-gray-400 hover:text-gray-200 hover:border-gray-600 disabled:opacity-40 transition">
                      + {{ t('storybook.pinupAdd') }}
                    </button>
                    <button v-if="storyPinups(detailStory).length"
                      @click="addPinup(detailStory, 'replace')"
                      :disabled="pinupBusy.has(detailStory.story_id)"
                      class="px-2 py-1 rounded-lg border border-gray-700/50 text-[10px] text-gray-400 hover:text-gray-200 hover:border-gray-600 disabled:opacity-40 transition">
                      ⟳ {{ t('storybook.pinupReplace') }}
                    </button>
                  </div>
                </div>
                <!-- Corkboard of polaroids -->
                <div v-if="storyPinups(detailStory).length || pinupBusy.has(detailStory.story_id)"
                  class="pinboard mb-3">
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
                <div class="flex gap-4">
                  <div class="text-sm text-gray-300 space-y-1.5 min-w-0">
                    <p v-if="storyBio(detailStory).personality">{{ storyBio(detailStory).personality }}</p>
                    <p v-if="storyBio(detailStory).occupation" class="text-gray-400">
                      <span class="text-gray-500">{{ t('storybook.bioOccupation') }}:</span> {{ storyBio(detailStory).occupation }}
                    </p>
                    <p v-for="f in BIO_LIST_FIELDS" :key="f"
                      v-show="(storyBio(detailStory)[f] || []).length"
                      class="text-gray-400 break-words">
                      <span class="text-gray-500">{{ t('storybook.bio_' + f) }}:</span>
                      {{ (storyBio(detailStory)[f] || []).join('、') }}
                    </p>
                    <p v-if="storyBio(detailStory).backstory" class="text-gray-400 italic pt-1">{{ storyBio(detailStory).backstory }}</p>
                  </div>
                </div>
              </div>
              <!-- Timetable -->
              <div v-if="storyTimetable(detailStory).length" class="px-8 py-5">
                <h4 class="text-xs font-semibold text-teal-300/80 mb-2 tracking-wide">🕒 {{ t('storybook.timetable') }}</h4>
                <ul class="space-y-1.5">
                  <li v-for="(slot, si) in storyTimetable(detailStory)" :key="si"
                    class="text-sm text-gray-300 flex gap-2">
                    <span class="text-teal-400/80 font-medium shrink-0 w-24">{{ slot.label }}</span>
                    <span class="min-w-0">
                      {{ slot.activity }}
                      <span v-if="slot.place" class="text-gray-500">@ {{ slot.place }}</span>
                      <span v-if="slot.feeling" class="text-gray-600 italic">（{{ slot.feeling }}）</span>
                    </span>
                  </li>
                </ul>
              </div>
              <div v-for="(axis, idx) in AXES" :key="axis"
                class="flex min-h-[220px]"
                :class="idx % 2 === 0 ? 'flex-row' : 'flex-row-reverse'">
                <div class="w-2/5 shrink-0 bg-gray-900 cursor-pointer relative group overflow-hidden"
                  @click="openImage(axisImage(detailStory, axis))">
                  <img v-if="axisImage(detailStory, axis)"
                    :src="`/api/thumbnails/${axisImage(detailStory, axis)}.webp`"
                    @error="onThumbError($event, axisImage(detailStory, axis))"
                    class="w-full h-full object-cover hover:opacity-90 transition-opacity" loading="lazy" />
                  <span v-else class="absolute inset-0 flex items-center justify-center text-4xl text-gray-700">⏳</span>
                  <button v-if="axisImage(detailStory, axis)"
                    @click.stop="emit('weave-from', axisImage(detailStory, axis))"
                    :title="t('storybook.weaveFrom')"
                    class="absolute bottom-2 right-2 px-2 py-1 bg-teal-900/80 hover:bg-teal-700/90 border border-teal-600/50 rounded-lg text-[10px] text-teal-200 opacity-0 group-hover:opacity-100 transition-opacity">
                    📜 {{ t('storybook.weaveFromShort') }}
                  </button>
                </div>
                <div class="flex-1 px-6 py-5 flex flex-col gap-3 justify-center">
                  <span class="text-[10px] font-bold uppercase tracking-widest"
                    :class="axis === detailStory.base_time_axis ? 'text-amber-400' : 'text-teal-400'">
                    {{ t('chronicle.axis.' + axis) }}
                    <span v-if="axis === detailStory.base_time_axis"
                      class="text-gray-500 normal-case font-normal ml-1">({{ t('storybook.base') }})</span>
                  </span>
                  <p class="text-gray-200 leading-relaxed text-sm whitespace-pre-wrap">
                    {{ axisStory(detailStory, axis) || '—' }}
                  </p>
                  <details v-if="detailStory.axes?.[axis]?.prompt_positive" class="mt-1">
                    <summary class="cursor-pointer text-[10px] text-gray-500 hover:text-gray-300 select-none">
                      {{ t('storybook.showPrompt') }}
                    </summary>
                    <div class="mt-2 flex flex-col gap-1">
                      <pre class="text-[10px] text-gray-400 whitespace-pre-wrap font-mono bg-gray-900/80 rounded-lg p-2">{{ detailStory.axes[axis].prompt_positive }}</pre>
                      <pre v-if="detailStory.axes[axis].prompt_negative"
                        class="text-[10px] text-gray-500 whitespace-pre-wrap font-mono bg-gray-900/80 rounded-lg p-2">{{ detailStory.axes[axis].prompt_negative }}</pre>
                    </div>
                  </details>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── FOCUS MODE — full-screen single-story view ─────────────────────── -->
    <div v-if="show && focusStory" class="fixed inset-0 z-[75] bg-black/95 flex flex-col p-6"
      @click.self="focusClose">
      <div class="flex items-center justify-between mb-4">
        <div class="min-w-0 flex-1">
          <h2 class="text-lg font-bold text-amber-200 truncate">{{ storyTitle(focusStory) || '—' }}</h2>
          <div class="flex items-center gap-3 mt-0.5 text-[10px] text-gray-500">
            <span v-if="focusStory.worldview">🌍 {{ focusStory.worldview }}</span>
            <span v-if="focusStory.time_scale">⏳ {{ t('chronicle.timeScale.' + focusStory.time_scale) }}</span>
            <span v-if="focusStory.emotion" class="text-indigo-400/80">🌒 {{ t(`inspire.emotion.${focusStory.emotion}`, focusStory.emotion) }}</span>
            <span class="font-mono">{{ formatDate(focusStory.created_at) }}</span>
            <span class="ml-2 text-gray-600">{{ focusIndex + 1 }} / {{ visibleStories.length }}</span>
          </div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button @click="focusPrev" :disabled="focusIndex <= 0"
            :title="t('storybook.focus.prev')"
            class="px-3 py-1.5 bg-gray-900 hover:bg-gray-800 disabled:opacity-30 border border-gray-700 rounded-lg text-gray-300">← </button>
          <button @click="focusNext" :disabled="focusIndex >= visibleStories.length - 1"
            :title="t('storybook.focus.next')"
            class="px-3 py-1.5 bg-gray-900 hover:bg-gray-800 disabled:opacity-30 border border-gray-700 rounded-lg text-gray-300"> →</button>
          <button @click="focusClose"
            :title="t('storybook.focus.close')"
            class="ml-2 text-gray-500 hover:text-gray-200 text-2xl w-9 h-9 flex items-center justify-center rounded-lg hover:bg-gray-800">✕</button>
        </div>
      </div>

      <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-5 overflow-hidden">
        <!-- big base image -->
        <div class="flex flex-col gap-3 min-h-0">
          <div class="flex-1 min-h-0 bg-gray-950 border border-gray-800 rounded-2xl overflow-hidden flex items-center justify-center">
            <img v-if="axisImage(focusStory, focusStory.base_time_axis)"
              :src="`/api/thumbnails/${axisImage(focusStory, focusStory.base_time_axis)}.webp`"
              @error="onThumbError($event, axisImage(focusStory, focusStory.base_time_axis))"
              class="max-w-full max-h-full object-contain" />
            <span v-else class="text-4xl text-gray-700">⏳</span>
          </div>
          <div class="grid grid-cols-3 gap-2">
            <div v-for="axis in AXES" :key="axis"
              class="relative aspect-square bg-gray-950 rounded-lg overflow-hidden border cursor-pointer"
              :class="axis === focusStory.base_time_axis ? 'border-amber-600/60' : 'border-gray-800'"
              @click="openImage(axisImage(focusStory, axis))">
              <img v-if="axisImage(focusStory, axis)"
                :src="`/api/thumbnails/${axisImage(focusStory, axis)}.webp`"
                @error="onThumbError($event, axisImage(focusStory, axis))"
                class="w-full h-full object-cover" loading="lazy" />
              <span v-else class="absolute inset-0 flex items-center justify-center text-gray-700">⏳</span>
              <span class="absolute bottom-0.5 left-1 text-[9px] font-bold uppercase tracking-wide"
                :class="axis === focusStory.base_time_axis ? 'text-amber-300' : 'text-teal-300'">
                {{ t('chronicle.axis.' + axis) }}
              </span>
            </div>
          </div>
        </div>

        <!-- story text -->
        <div class="flex flex-col gap-3 overflow-y-auto pr-1">
          <p v-if="storyOverall(focusStory)"
            class="text-sm text-gray-300 italic leading-relaxed border-l-2 border-amber-700/50 pl-3">
            {{ storyOverall(focusStory) }}
          </p>
          <div v-for="axis in AXES" :key="axis" class="flex flex-col gap-1.5">
            <span class="text-[10px] font-bold uppercase tracking-widest"
              :class="axis === focusStory.base_time_axis ? 'text-amber-400' : 'text-teal-400'">
              {{ t('chronicle.axis.' + axis) }}
              <span v-if="axis === focusStory.base_time_axis"
                class="text-gray-500 normal-case font-normal ml-1">({{ t('storybook.base') }})</span>
            </span>
            <p class="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap">
              {{ axisStory(focusStory, axis) || '—' }}
            </p>
          </div>
        </div>
      </div>
    </div>

    <!-- Pinup polaroid lightbox (top-most: above the story detail + image viewer) -->
    <div v-if="pinupView"
      class="fixed inset-0 z-[210] bg-black/85 flex items-center justify-center p-8"
      @click.self="pinupView = null">
      <div class="pincard pincard--large" @click="pinupView = null">
        <span class="pincard-pin"></span>
        <img :src="`/api/originals/${pinupView}`" @error="onThumbError($event, pinupView)" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── Polaroid stack ───────────────────────────────────────────────────────── */
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
  background: #f5f4eb;
  border: 4px solid #f5f4eb;
  border-bottom-width: 22px;    /* Polaroid signature bottom margin */
  box-shadow: 0 6px 14px rgba(0, 0, 0, 0.45);
  transition: transform 0.4s cubic-bezier(0.2, 0.8, 0.2, 1),
              box-shadow 0.4s;
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
  font-size: 1.5rem;
}
.polaroid.past    { transform: translate(-16%, -6%) rotate(-6deg); z-index: 1; }
.polaroid.present { transform: translate(0, 0)       rotate(2deg);  z-index: 2; }
.polaroid.future  { transform: translate(16%, 6%)    rotate(8deg);  z-index: 3; }
.polaroid.base {
  box-shadow: 0 0 0 2px rgba(251, 191, 36, 0.6),
              0 6px 14px rgba(0, 0, 0, 0.45);
}

/* Hover: fan the three photos out horizontally */
.storybook-card:hover .polaroid-stack .polaroid.past    { transform: translate(-42%, -2%) rotate(-4deg); }
.storybook-card:hover .polaroid-stack .polaroid.present { transform: translate(0, -2%)     rotate(0deg);  }
.storybook-card:hover .polaroid-stack .polaroid.future  { transform: translate(42%, -2%)  rotate(4deg);  }
.storybook-card:hover {
  transform: translateY(-2px);
}

/* Compact Polaroid stack for the Timeline strip */
.polaroid-stack-sm .polaroid { border-width: 3px; border-bottom-width: 14px; }

/* ── Pinup corkboard (Biography reference photos) ─────────────────────────── */
.pinboard {
  display: flex;
  flex-wrap: wrap;
  gap: 1.1rem 1.4rem;
  padding: 1.2rem 1rem;
  border-radius: 0.6rem;
  background-color: #b98a58;
  background-image:
    radial-gradient(rgba(0, 0, 0, 0.14) 1px, transparent 1.4px),
    radial-gradient(rgba(255, 255, 255, 0.06) 1px, transparent 1.4px);
  background-size: 9px 9px, 9px 9px;
  background-position: 0 0, 4.5px 4.5px;
  box-shadow: inset 0 0 34px rgba(0, 0, 0, 0.32),
              0 2px 6px rgba(0, 0, 0, 0.3);
}
.pincard {
  position: relative;
  background: #fafafa;
  padding: 0.4rem 0.4rem 1.4rem;
  box-shadow: 0 5px 12px rgba(0, 0, 0, 0.45);
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.pincard:hover {
  transform: rotate(0deg) scale(1.06) !important;
  box-shadow: 0 10px 22px rgba(0, 0, 0, 0.55);
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
  background: radial-gradient(circle at 35% 30%, #ff6b64, #d0362f);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.5);
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
</style>

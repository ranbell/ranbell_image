<script setup>
/*
 * Pick a character by looking at her.
 *
 * The old picker was a two-column image grid inside a 320px rail, clipped to
 * 320px of height, so each face was 140px and two rows showed at a time — a
 * hundred characters read as a hundred name labels, which is the list the
 * pictures were supposed to replace.
 *
 * The picture that matters is the *sheet*: a centre pose plus four moments from
 * her life, chosen from her personality. That is what says who she is, so it is
 * what the card shows, with her face laid over the corner so you also know who
 * you are looking at.
 *
 * Three ways through a hundred of them, because they suit different moods:
 * the grid to scan, the deck to browse one at a time, the dossier to read.
 * Filtering is by hair and eye colour as actual colours — glancing rather than
 * reading — with her traits alongside.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import CharacterDossier from './muse/CharacterDossier.vue'
import CharacterCompatViewer from './muse/CharacterCompatViewer.vue'
import LoungePanel from './muse/LoungePanel.vue'
import {
  colorFamily, colorWord, eyeSwatch, familySwatch, hairSwatch,
} from './muse/colorSwatch.js'
import { traitLabel } from './muse/traitLabels.js'
import { useRenderWatch } from '../composables/useRenderWatch.js'

const props = defineProps({
  show: { type: Boolean, default: false },
  selectedId: { type: String, default: '' },
  // Drawing needs a checkpoint and the user changes it per run, so it is asked
  // for here rather than assumed.
  workflows: { type: Array, default: () => [] },
  workflow: { type: String, default: '' },
  // The live job map. A bulk of sixty renders outlives this screen and outlives
  // the page, so what is still running is read from the jobs rather than
  // remembered in a local ref that a reload throws away.
  getJobsMap: { type: Function, default: () => () => new Map() },
})
const emit = defineEmits(['pick', 'close', 'toast', 'update:workflow', 'start-duet-pair'])
const { t, locale } = useI18n()

const characters = ref([])
const loading = ref(false)
const activeTraits = ref([])
const activeHair = ref([])
const activeEyes = ref([])
const unreadOnly = ref(false)
const view = ref('grid')          // 'grid' | 'deck'
const imageDisplayMode = ref('sheet') // 'sheet' | 'portrait' — top-bar one-tap toggle!
const deckAt = ref(0)
const dossierId = ref('')
const bulkGroup = ref('')

// Character chemistry vectors — moved here from the Admin panel: this is where
// the characters themselves live, and the viewer that reads them belongs next
// to the button that fills them in.
const compatStatus = ref(null)
const compatLoading = ref(false)
const showCompatViewer = ref(false)
const showLounge = ref(false)
const loungeSummary = ref(null)
const LOUNGE_SEEN_KEY = 'muse.lounge.lastSeenAt'

function loungeSeenAt() {
  const n = Number(localStorage.getItem(LOUNGE_SEEN_KEY) || 0)
  return Number.isFinite(n) ? n : 0
}
function markLoungeSeen() {
  localStorage.setItem(LOUNGE_SEEN_KEY, String(Date.now() / 1000))
  loungeSummary.value = { ...(loungeSummary.value || {}), unread: 0, new_threads: 0 }
}
const loungeUnread = computed(() => Number(loungeSummary.value?.unread || 0))

async function fetchCompatStatus() {
  try {
    const r = await fetch('/api/admin/character-compat/status')
    if (r.ok) compatStatus.value = await r.json()
  } catch { /* transient — the badge just won't update this pass */ }
}

async function fetchLoungeSummary() {
  try {
    const r = await fetch(`/api/muse/lounge/summary?since=${loungeSeenAt()}`)
    if (r.ok) loungeSummary.value = await r.json()
  } catch { /* badge stays quiet */ }
}

function openLounge() {
  showLounge.value = true
  markLoungeSeen()
}

async function runCompatBackfill() {
  compatLoading.value = true
  try {
    const r = await fetch('/api/admin/character-compat/backfill', { method: 'POST' })
    if (!r.ok) throw new Error(`${r.status}`)
    emit('toast', { msg: t('admin.characterCompat.backfillStart'), type: 'success' })
    await fetchCompatStatus()
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  } finally {
    compatLoading.value = false
  }
}

const isJa = computed(() => String(locale.value).startsWith('ja'))
function label(c) { return (isJa.value ? c.name_ja : c.name) || c.name || c.preset_key }
function blurb(c) { return (isJa.value ? c.summary_ja : c.summary) || '' }
function title(c) { return (isJa.value ? c.title_ja : c.title) || '' }
// The gap that makes her worth drawing. Shown on the card because it is the
// line that decides whether you pick her.
function charm(c) { return c.charm_ja || '' }
function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function sheet(c) { return thumb(c.board?.sheet || '') }
function face(c) { return thumb(c.board?.portrait || '') }
function anyImage(c) { return sheet(c) || face(c) }
function cardImage(c) {
  if (imageDisplayMode.value === 'portrait') return face(c) || sheet(c)
  return sheet(c) || face(c)
}


// Only offer a filter that narrows something. A trait one character has is not
// a filter, it is a name.
function options(field, min) {
  const counts = new Map()
  for (const c of characters.value) {
    const values = field === 'traits' ? (c.traits || []) : [c[field]].filter(Boolean)
    for (const v of values) counts.set(v, (counts.get(v) || 0) + 1)
  }
  return [...counts.entries()]
    .filter(([, n]) => n >= min)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([v]) => v)
}
// Colours filter by family, not by word. The roster distinguishes `brown`,
// `dark_brown`, `light_brown` and `chestnut`; a person clicking a brown dot
// means all four.
function colorOptions(field, kind) {
  const counts = new Map()
  for (const c of characters.value) {
    const family = colorFamily(c[field], kind)
    if (family) counts.set(family, (counts.get(family) || 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([v]) => v)
}
const hairOptions = computed(() => colorOptions('hair_color', 'hair'))
const eyeOptions = computed(() => colorOptions('eye_color', 'eyes'))
const traitOptions = computed(() => options('traits', 3).slice(0, 14))

const filtered = computed(() => {
  return characters.value.filter(c => {
    if (activeHair.value.length
        && !activeHair.value.includes(colorFamily(c.hair_color, 'hair'))) return false
    if (activeEyes.value.length
        && !activeEyes.value.includes(colorFamily(c.eye_color, 'eyes'))) return false
    if (activeTraits.value.length
        && !activeTraits.value.every(x => (c.traits || []).includes(x))) return false
    if (unreadOnly.value && !(c.diary_unread_count > 0)) return false
    return true
  })
})

const missingCount = computed(
  () => characters.value.reduce(
    (n, c) => n + (c.board?.sheet ? 0 : 1) + (c.board?.portrait ? 0 : 1), 0),
)
// Only 3+ characters missing an image at all justifies surfacing bulk creation
// here — below that, the individual page's own workflow picker (footer) is the
// more direct path, and this screen's selector used to sit there unused with
// nothing to apply it to.
const noImageCount = computed(
  () => characters.value.filter(c => !anyImage(c)).length,
)
const anyFilter = computed(
  () => activeHair.value.length || activeEyes.value.length
    || activeTraits.value.length || unreadOnly.value,
)
const current = computed(() => filtered.value[deckAt.value] || null)

// `list` is the array, not the ref. Vue unwraps a `<script setup>` ref before
// it reaches a template handler, so `toggle(activeHair, h)` hands this the
// array — and the version that reached for `list.value` threw
// `Cannot read properties of undefined` on every click. Nothing caught it, so
// the filter bar looked alive and filtered nothing: colours and traits both.
function toggle(list, value) {
  const i = list.indexOf(value)
  if (i >= 0) list.splice(i, 1)
  else list.push(value)
  deckAt.value = 0
}
function clearFilters() {
  activeHair.value = []; activeEyes.value = []; activeTraits.value = []
  unreadOnly.value = false; deckAt.value = 0
}
function step(n) {
  const total = filtered.value.length
  if (!total) return
  deckAt.value = (deckAt.value + n + total) % total
}
function surprise() {
  const total = filtered.value.length
  if (!total) return
  deckAt.value = Math.floor(Math.random() * total)
  view.value = 'deck'
}

// Swipe. Asked for at the very start of this feature — "flick through the
// pictures" — and never built until the deck existed to flick through.
const dragFrom = ref(null)
const dragBy = ref(0)
const SWIPE = 60          // px before it counts as a flick rather than a wobble

function dragStart(e) { dragFrom.value = e.clientX; dragBy.value = 0 }
function dragMove(e) {
  if (dragFrom.value === null) return
  dragBy.value = e.clientX - dragFrom.value
}
function dragEnd() {
  if (dragFrom.value === null) return
  if (Math.abs(dragBy.value) > SWIPE) step(dragBy.value < 0 ? 1 : -1)
  dragFrom.value = null
  dragBy.value = 0
}

// ── fetch ──────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
  if (resp.status === 204) return null
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `${resp.status}`)
  return data
}
function fail(err) { emit('toast', { msg: String(err?.message || err), type: 'error' }) }

async function reload() {
  loading.value = true
  try { characters.value = (await api('/api/characters')).characters || [] }
  catch (err) { fail(err) } finally { loading.value = false }
}

function needsWorkflow() {
  if (props.workflow) return false
  emit('toast', { msg: t('characters.needWorkflow'), type: 'error' })
  return true
}

// Nothing tells this screen when a render lands, so it looks until it does.
const { watch: watchRenders, watching } = useRenderWatch(reload)

async function draw(id) {
  if (needsWorkflow()) return
  try {
    await api(`/api/characters/${id}/board`, {
      method: 'POST', body: JSON.stringify({ workflow_name: props.workflow }),
    })
    emit('toast', { msg: t('characters.queued'), type: 'info' })
    watchRenders(180)
  } catch (err) { fail(err) }
}

async function drawMissing() {
  if (needsWorkflow()) return
  loading.value = true
  try {
    const r = await api('/api/characters/boards/missing', {
      method: 'POST', body: JSON.stringify({ workflow_name: props.workflow }),
    })
    bulkGroup.value = r.group_id || ''
    emit('toast', { msg: t('characters.queuedN', { n: r.queued }), type: 'info' })
    // Two hundred renders take a while; watch for as long as that could run.
    watchRenders(Math.max(300, r.queued * 45))
  } catch (err) { fail(err) } finally { loading.value = false }
}

/*
 * What is still being drawn, read off the live job list.
 *
 * This used to be a single local ref set by `drawMissing`, which meant the run
 * only existed as long as the page did: reload during a bulk of sixty and the
 * stop button vanished, nothing watched for the pictures landing, and the
 * button offered to draw the same sixty again — queueing a second sixty behind
 * the first. Every character-board job carries `meta.character_id`, so the
 * answer is in `/api/jobs`, which App.vue already holds and re-snapshots on
 * reconnect.
 */
const ACTIVE_STATES = new Set(['queued', 'running', 'cancelling'])
const outstanding = ref([])
let jobTimer = null

function boardJobs() {
  const map = props.getJobsMap?.()
  if (!map?.values) return []
  return [...map.values()].filter(
    j => j?.meta?.character_id && ACTIVE_STATES.has(j.state),
  )
}

function syncFromJobs() {
  const jobs = boardJobs()
  outstanding.value = jobs
  const group = jobs.map(j => j.meta.group_id).find(Boolean) || ''
  if (group) bulkGroup.value = group
  else if (!jobs.length) bulkGroup.value = ''
  // Renders queued before this screen was opened still attach themselves to
  // the presets, and nothing announces it, so pick the watch back up.
  if (jobs.length && !watching.value) watchRenders(Math.max(60, jobs.length * 45))
}

async function stopBulk() {
  const jobs = outstanding.value
  const groups = [...new Set(jobs.map(j => j.meta.group_id).filter(Boolean))]
  if (bulkGroup.value && !groups.includes(bulkGroup.value)) groups.push(bulkGroup.value)
  try {
    for (const g of groups) {
      await api(`/api/jobs/groups/${encodeURIComponent(g)}/cancel`, { method: 'POST' })
    }
    // A single character's redraw carries no group and would otherwise survive
    // a "stop" that claims to have stopped everything.
    for (const j of jobs.filter(x => !x.meta.group_id)) {
      await api(`/api/jobs/${encodeURIComponent(j.id)}/cancel`, { method: 'POST' })
    }
    emit('toast', { msg: t('characters.stopped'), type: 'info' })
    bulkGroup.value = ''
    outstanding.value = []
    watchRenders(20)
  } catch (err) { fail(err) }
}

// Re-seeding the roster is a maintenance action, not a browsing one, and it
// deletes: it lives in the admin screen's characters tab now.

function onKey(e) {
  if (!props.show || dossierId.value || showLounge.value || showCompatViewer.value) return
  if (view.value !== 'deck') return
  if (e.key === 'ArrowRight') { step(1); e.preventDefault() }
  if (e.key === 'ArrowLeft') { step(-1); e.preventDefault() }
}

// The job map is a plain Map behind a getter, deliberately not reactive — App
// keeps it that way so a hundred job events a minute do not re-render the
// gallery. So sample it on a tick while the screen is open, the way MusePanel
// samples its own job.
function openGallery() {
  reload()
  deckAt.value = 0
  syncFromJobs()
  fetchCompatStatus()
  fetchLoungeSummary()
  if (!jobTimer) jobTimer = setInterval(syncFromJobs, 2000)
}
function closeGallery() {
  if (jobTimer) { clearInterval(jobTimer); jobTimer = null }
}

watch(() => props.show, open => (open ? openGallery() : closeGallery()))
onMounted(() => {
  window.addEventListener('keydown', onKey)
  if (props.show) openGallery()
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  closeGallery()
})
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[var(--z-panel)] flex items-stretch justify-center
           bg-black/80 backdrop-blur-sm p-3"
    @mousedown.self="emit('close')"
  >
    <div class="sb-shell w-full max-w-[1600px] flex flex-col min-h-0">
      <header class="flex flex-wrap items-center gap-2 px-4 py-3 sb-hairline shrink-0">
        <div class="min-w-0 mr-auto">
          <h2 class="sb-display text-base text-[var(--sb-amber)]">{{ t('characters.title') }}</h2>
          <p class="text-[11px] text-[var(--sb-muted)]">
            {{ t('characters.count', { shown: filtered.length, total: characters.length }) }}
            <span v-if="watching" class="text-teal-300/80">· {{ t('characters.watching') }}</span>
          </p>
        </div>

        <!-- 🌸 Top-bar picture mode toggle button (Portrait ↔ Sheet) -->
        <div class="sb-seg border-pink-500/30">
          <button
            type="button"
            class="sb-seg-btn text-[11px] px-2.5"
            :class="imageDisplayMode === 'portrait' ? 'bg-pink-500/80 text-white font-semibold' : ''"
            :title="t('characters.modePortrait')"
            @click="imageDisplayMode = 'portrait'"
          >{{ t('characters.modePortrait') }}</button>
          <button
            type="button"
            class="sb-seg-btn text-[11px] px-2.5"
            :class="imageDisplayMode === 'sheet' ? 'bg-rose-500/80 text-white font-semibold' : ''"
            :title="t('characters.modeSheet')"
            @click="imageDisplayMode = 'sheet'"
          >{{ t('characters.modeSheet') }}</button>
        </div>


        <div class="sb-seg">
          <button
            v-for="v in ['grid', 'deck']"
            :key="v"
            type="button"
            class="sb-seg-btn"
            :class="view === v ? 'is-on-teal' : ''"
            @click="view = v"
          >{{ t(`characters.view.${v}`) }}</button>
        </div>
        <button class="sb-btn" :title="t('characters.surprise')" @click="surprise">🎲</button>


        <select v-if="noImageCount >= 3" class="sb-select w-56" :value="workflow"
                @change="emit('update:workflow', $event.target.value)">
          <option value="">{{ t('characters.workflow') }} —</option>
          <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
        </select>
        <button v-if="outstanding.length" type="button" class="sb-btn" @click="stopBulk">
          {{ t('characters.stopN', { n: outstanding.length }) }}
        </button>
        <button v-else-if="missingCount && noImageCount >= 3" type="button" class="sb-btn"
                :disabled="loading" @click="drawMissing">
          {{ t('characters.drawMissing', { n: missingCount }) }}
        </button>

        <!-- Character chemistry vectors: fill them in, and see them all. -->
        <button
          v-if="compatStatus?.needs_backfill && !compatStatus?.backfill?.running"
          type="button" class="sb-btn text-[11px]" :disabled="compatLoading"
          :title="t('admin.characterCompat.needsBackfill', { n: compatStatus.total - compatStatus.embedded })"
          @click="runCompatBackfill"
        >🧬 {{ t('admin.characterCompat.backfillBtn') }}</button>
        <button
          type="button" class="sb-icon-btn" :title="t('characters.compat.viewerTitle')"
          @click="showCompatViewer = true"
        >💞</button>
        <button
          type="button" class="sb-icon-btn relative" :title="t('muse.lounge.title')"
          @click="openLounge"
        >
          💬
          <span
            v-if="loungeUnread > 0"
            class="absolute -top-1 -right-1 min-w-[1.1rem] h-[1.1rem] px-0.5
                   rounded-full bg-rose-500 text-white text-[9px] leading-[1.1rem]
                   text-center shadow"
            :title="t('muse.lounge.unreadCount', { n: loungeUnread })"
          >{{ loungeUnread > 9 ? '9+' : loungeUnread }}</span>
        </button>

        <button
          type="button"
          class="sb-chip"
          :class="unreadOnly ? 'is-chip-on-teal' : ''"
          @click="unreadOnly = !unreadOnly"
        >💌 {{ t('characters.unreadOnly') }}</button>

        <button class="sb-icon-btn" :title="t('muse.close')" @click="emit('close')">✕</button>
      </header>

      <!-- ── filters: colours you can see, traits you can read ── -->
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-2 sb-hairline shrink-0">
        <div class="flex items-center gap-1">
          <span class="sb-label mr-1">{{ t('characters.hair') }}</span>
          <button
            v-for="h in hairOptions"
            :key="h"
            type="button"
            class="w-5 h-5 rounded-full border transition-transform hover:scale-110"
            :class="activeHair.includes(h)
              ? 'border-teal-300 ring-2 ring-teal-400/50' : 'border-white/25'"
            :style="{ background: familySwatch(h, 'hair') }"
            :title="colorWord(h)"
            @click="toggle(activeHair, h)"
          />
        </div>
        <div class="flex items-center gap-1">
          <span class="sb-label mr-1">{{ t('characters.eyes') }}</span>
          <button
            v-for="e in eyeOptions"
            :key="e"
            type="button"
            class="w-5 h-5 rounded-full border transition-transform hover:scale-110"
            :class="activeEyes.includes(e)
              ? 'border-teal-300 ring-2 ring-teal-400/50' : 'border-white/25'"
            :style="{ background: familySwatch(e, 'eyes') }"
            :title="colorWord(e)"
            @click="toggle(activeEyes, e)"
          />
        </div>
        <div class="flex flex-wrap items-center gap-1">
          <button
            v-for="trait in traitOptions"
            :key="trait"
            type="button"
            class="sb-chip"
            :class="activeTraits.includes(trait) ? 'is-chip-on-teal' : ''"
            :title="traitLabel(trait)"
            @click="toggle(activeTraits, trait)"
          >{{ trait }} <span class="opacity-70">({{ traitLabel(trait) }})</span></button>
        </div>
        <button v-if="anyFilter" type="button" class="sb-chip" @click="clearFilters">
          ✕ {{ t('characters.clearFilter') }}
        </button>
      </div>

      <!-- ── grid ── -->
      <div v-if="view === 'grid'" class="flex-1 overflow-y-auto p-4 min-h-0">
        <p v-if="!filtered.length" class="text-xs text-gray-500 py-10 text-center">
          {{ loading ? '…' : t('characters.empty') }}
        </p>
        <div v-else class="grid gap-4 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          <article
            v-for="(c, i) in filtered"
            :key="c.id"
            class="ch-card flex flex-col rounded-2xl border overflow-hidden transition-all duration-300 shadow-md"
            :class="c.id === selectedId
              ? 'border-rose-400 bg-rose-950/20 ring-2 ring-rose-400/60 shadow-[0_8px_25px_rgba(244,114,182,0.3)]'
              : 'border-pink-500/20 bg-slate-900/60 hover:border-pink-300/60 hover:shadow-[0_10px_25px_rgba(244,114,182,0.25)] hover:-translate-y-1.5'"
            :style="{ animationDelay: `${Math.min(i, 24) * 22}ms` }"
          >
            <button
              type="button"
              class="group relative block w-full text-left"
              @click="dossierId = c.id"
            >
              <span class="block aspect-[3/4] bg-black/50 overflow-hidden relative">
                <img
                  v-if="anyImage(c)"
                  :src="cardImage(c)"
                  :alt="label(c)"
                  loading="lazy"
                  class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                />

                <span v-else
                      class="w-full h-full grid place-items-center text-[10px] text-gray-400 px-3 text-center">
                  {{ t('characters.noPortrait') }}
                </span>
              </span>

              <!-- 🌸 Enlarged Face Portrait (w-16 h-16 = 64px) with cute double ring -->
              <img
                v-if="face(c)"
                :src="face(c)"
                class="absolute top-2.5 left-2.5 w-16 h-16 rounded-full object-cover
                       border-2 border-pink-100 ring-2 ring-pink-400/80 shadow-xl
                       transition-transform duration-300 group-hover:scale-105"
                alt=""
                loading="lazy"
              />

              <!-- Cute Chosen Badge -->
              <span
                v-if="c.id === selectedId"
                class="absolute top-2.5 right-2.5 px-2.5 py-1 rounded-full text-[10px] font-bold
                       bg-gradient-to-r from-pink-500 to-rose-400 text-white shadow-md animate-pulse"
              >{{ t('characters.chosenBadge') }}</span>

              <!-- Unread diary count — cute pulsing badge -->
              <span
                v-if="c.diary_unread_count > 0"
                class="absolute bottom-2.5 right-2.5 min-w-[20px] h-5 px-1.5 rounded-full
                       text-[10px] font-bold grid place-items-center
                       bg-rose-500 text-white shadow-md animate-pulse"
                :title="t('characters.unreadCount', { n: c.diary_unread_count })"
              >💌 {{ c.diary_unread_count }}</span>
            </button>

            <div class="p-3 flex flex-col gap-1.5 grow bg-slate-900/40">
              <div class="flex items-center gap-1.5">
                <p class="text-sm text-pink-50 font-bold truncate mr-auto tracking-wide">
                  {{ label(c) }}
                  <span v-if="title(c)" class="block text-[10px] text-pink-300/90 font-normal truncate">
                    {{ title(c) }}
                  </span>
                </p>
                <i class="w-3 h-3 rounded-full border border-pink-200/40 shrink-0 shadow-xs"
                   :style="{ background: hairSwatch(c.hair_color) }" :title="colorWord(c.hair_color)"></i>
                <i class="w-3 h-3 rounded-full border border-pink-200/40 shrink-0 shadow-xs"
                   :style="{ background: eyeSwatch(c.eye_color) }" :title="colorWord(c.eye_color)"></i>
              </div>

              <p class="text-[11px] text-gray-300/90 leading-relaxed line-clamp-2">{{ blurb(c) }}</p>
              
              <!-- First Person & Call Sign badge -->
              <div v-if="c.first_person_ja || c.first_person_en" class="flex items-center gap-1.5 text-[10px] text-amber-300 font-mono">
                <span class="px-1.5 py-0.5 rounded bg-amber-950/40 border border-amber-500/30">
                  {{ t('muse.firstPerson') }}: {{ isJa ? c.first_person_ja : (c.first_person_en || c.first_person_ja) }}
                </span>
                <span v-if="c.user_address_ja || c.user_address_en" class="px-1.5 py-0.5 rounded bg-amber-950/40 border border-amber-500/30">
                  {{ t('muse.userAddress') }}: {{ isJa ? c.user_address_ja : (c.user_address_en || c.user_address_ja) }}
                </span>
              </div>

              <!-- Cute Speech Bubble Charm Point -->
              <p v-if="charm(c)"
                 class="text-[11px] text-pink-200 leading-relaxed p-2 rounded-xl bg-pink-950/40 border border-pink-500/30 relative">
                💕 {{ charm(c) }}
              </p>

              <div class="flex flex-wrap gap-1 mt-auto pt-1">
                <span
                  v-for="trait in (c.traits || []).slice(0, 3)"
                  :key="trait"
                  class="px-2 py-0.5 rounded-full bg-pink-950/50 border border-pink-500/30
                         text-[9px] text-pink-200"
                >{{ trait }}</span>
              </div>

              <div class="flex gap-1.5 pt-1">
                <button type="button" class="sb-btn flex-1 justify-center !py-1.5 !text-[11px] font-bold bg-gradient-to-r from-pink-500 to-rose-500 hover:from-pink-400 hover:to-rose-400 text-white border-0 shadow-md rounded-xl transition-all"
                        @click="emit('pick', c.id)">
                  {{ t('characters.pairButton') }}
                </button>

                <button v-if="noImageCount >= 3" type="button" class="sb-icon-btn !w-8 !h-8 !text-xs border-pink-500/30 text-pink-300 hover:bg-pink-900/40 rounded-xl"
                        :title="t('characters.drawReference')" @click="draw(c.id)">✎</button>
              </div>
            </div>
          </article>

        </div>
      </div>

      <!-- ── deck: one at a time, arrows or drag ── -->
      <div
        v-else
        class="flex-1 grid place-items-center p-4 min-h-0 select-none touch-pan-y"
        :class="dragFrom !== null ? 'cursor-grabbing' : 'cursor-grab'"
        @wheel.prevent="step($event.deltaY > 0 || $event.deltaX > 0 ? 1 : -1)"
        @pointerdown="dragStart"
        @pointermove="dragMove"
        @pointerup="dragEnd"
        @pointercancel="dragEnd"
        @pointerleave="dragEnd"
      >
        <p v-if="!current" class="text-xs text-gray-500">{{ t('characters.empty') }}</p>
        <div v-else class="flex items-center gap-4 md:gap-8 w-full justify-center">
          <button class="sb-icon-btn shrink-0 !w-11 !h-11 !text-xl" @click="step(-1)">‹</button>

          <div class="flex flex-col items-center gap-3 min-w-0">
            <div class="rounded-2xl border border-white/15 bg-black/50 overflow-hidden
                        shadow-2xl grid place-items-center"
                 :style="{ maxHeight: '56vh', transform: `translateX(${dragBy * 0.4}px)` }">
              <img
                v-if="anyImage(current)"
                :key="current.id"
                :src="cardImage(current)"
                :alt="label(current)"
                class="ch-deck-img max-h-[56vh] w-auto object-contain"
              />

              <div v-else class="p-16 text-center">
                <p class="text-xs text-gray-500 mb-3">{{ t('characters.noPortrait') }}</p>
                <button class="sb-btn" @click="draw(current.id)">{{ t('characters.drawReference') }}</button>
              </div>
            </div>

            <div class="text-center max-w-lg">
              <div class="flex items-center justify-center gap-2">
                <i class="w-3 h-3 rounded-full border border-white/25"
                   :style="{ background: hairSwatch(current.hair_color) }"></i>
                <h3 class="sb-display text-lg text-gray-100">{{ label(current) }}</h3>
                <i class="w-3 h-3 rounded-full border border-white/25"
                   :style="{ background: eyeSwatch(current.eye_color) }"></i>
              </div>
              <p class="text-[12px] text-gray-400 mt-1 leading-relaxed">{{ blurb(current) }}</p>
              <div class="flex flex-wrap gap-1 justify-center mt-2">
                <span
                  v-for="trait in current.traits || []"
                  :key="trait"
                  class="px-2 py-0.5 rounded-full bg-teal-900/30 border border-teal-600/30
                         text-[10px] text-teal-200"
                >{{ trait }}</span>
              </div>
              <div class="flex gap-2 justify-center mt-3">
                <button class="sb-btn" @click="emit('pick', current.id)">
                  {{ t('characters.useCharacter') }}
                </button>
                <button class="sb-btn" @click="dossierId = current.id">
                  {{ t('characters.readMore') }}
                </button>
              </div>
              <p class="text-[10px] text-gray-600 mt-3">
                {{ deckAt + 1 }} / {{ filtered.length }} · {{ t('characters.deckHint') }}
              </p>
            </div>
          </div>

          <button class="sb-icon-btn shrink-0 !w-11 !h-11 !text-xl" @click="step(1)">›</button>
        </div>
      </div>
    </div>

    <CharacterDossier
      v-if="dossierId"
      :character-id="dossierId"
      :workflows="workflows"
      :workflow="workflow"
      :get-jobs-map="getJobsMap"
      @close="dossierId = ''"
      @pick="emit('pick', $event)"
      @toast="emit('toast', $event)"
      @changed="reload"
      @update:workflow="emit('update:workflow', $event)"
    />

    <CharacterCompatViewer
      :show="showCompatViewer"
      @close="showCompatViewer = false"
      @toast="emit('toast', $event)"
      @open-character="showCompatViewer = false; dossierId = $event"
      @start-duet-pair="showCompatViewer = false; emit('start-duet-pair', $event)"
    />

    <LoungePanel
      :show="showLounge"
      @close="showLounge = false; fetchLoungeSummary()"
      @toast="emit('toast', $event)"
      @seen="markLoungeSeen"
    />
  </div>
</template>

<style scoped>
.ch-card {
  animation: ch-in 260ms ease-out both;
}
@keyframes ch-in {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: none; }
}
.ch-deck-img {
  animation: ch-deal 220ms ease-out both;
}
@keyframes ch-deal {
  from { opacity: 0; transform: scale(0.97); }
  to   { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .ch-card, .ch-deck-img { animation: none; }
}
</style>

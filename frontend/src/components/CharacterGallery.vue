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
import { eyeSwatch, hairSwatch, colorWord } from './muse/colorSwatch.js'
import { useRenderWatch } from '../composables/useRenderWatch.js'

const props = defineProps({
  show: { type: Boolean, default: false },
  selectedId: { type: String, default: '' },
  // Drawing needs a checkpoint and the user changes it per run, so it is asked
  // for here rather than assumed.
  workflows: { type: Array, default: () => [] },
  workflow: { type: String, default: '' },
})
const emit = defineEmits(['pick', 'close', 'toast', 'update:workflow'])
const { t, locale } = useI18n()

const characters = ref([])
const loading = ref(false)
const query = ref('')
const activeTraits = ref([])
const activeHair = ref([])
const activeEyes = ref([])
const view = ref('grid')          // 'grid' | 'deck'
const deckAt = ref(0)
const dossierId = ref('')
const bulkGroup = ref('')

const isJa = computed(() => String(locale.value).startsWith('ja'))
function label(c) { return (isJa.value ? c.name_ja : c.name) || c.name || c.preset_key }
function blurb(c) { return (isJa.value ? c.summary_ja : c.summary) || '' }
function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function sheet(c) { return thumb(c.board?.sheet || '') }
function face(c) { return thumb(c.board?.portrait || '') }
function anyImage(c) { return sheet(c) || face(c) }

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
const hairOptions = computed(() => options('hair_color', 1))
const eyeOptions = computed(() => options('eye_color', 1))
const traitOptions = computed(() => options('traits', 3).slice(0, 14))

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return characters.value.filter(c => {
    if (activeHair.value.length && !activeHair.value.includes(c.hair_color)) return false
    if (activeEyes.value.length && !activeEyes.value.includes(c.eye_color)) return false
    if (activeTraits.value.length
        && !activeTraits.value.every(x => (c.traits || []).includes(x))) return false
    if (!q) return true
    return `${c.name} ${c.name_ja} ${c.summary} ${c.summary_ja} ${(c.traits || []).join(' ')}`
      .toLowerCase().includes(q)
  })
})

const missingCount = computed(
  () => characters.value.reduce(
    (n, c) => n + (c.board?.sheet ? 0 : 1) + (c.board?.portrait ? 0 : 1), 0),
)
const anyFilter = computed(
  () => activeHair.value.length || activeEyes.value.length || activeTraits.value.length,
)
const current = computed(() => filtered.value[deckAt.value] || null)

function toggle(list, value) {
  const i = list.value.indexOf(value)
  if (i >= 0) list.value.splice(i, 1)
  else list.value.push(value)
  deckAt.value = 0
}
function clearFilters() {
  activeHair.value = []; activeEyes.value = []; activeTraits.value = []; deckAt.value = 0
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

async function stopBulk() {
  if (!bulkGroup.value) return
  try {
    await api(`/api/jobs/groups/${encodeURIComponent(bulkGroup.value)}/cancel`, { method: 'POST' })
    emit('toast', { msg: t('characters.stopped'), type: 'info' })
    bulkGroup.value = ''
    watchRenders(20)
  } catch (err) { fail(err) }
}

function onKey(e) {
  if (!props.show || dossierId.value) return
  if (view.value !== 'deck') return
  if (e.key === 'ArrowRight') { step(1); e.preventDefault() }
  if (e.key === 'ArrowLeft') { step(-1); e.preventDefault() }
}

watch(() => props.show, open => {
  if (open) { reload(); deckAt.value = 0 }
})
onMounted(() => {
  window.addEventListener('keydown', onKey)
  if (props.show) reload()
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
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

        <input v-model="query" type="search" class="sb-input w-48"
               :placeholder="t('characters.search')" />
        <select class="sb-select w-56" :value="workflow"
                @change="emit('update:workflow', $event.target.value)">
          <option value="">{{ t('characters.workflow') }} —</option>
          <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
        </select>
        <button v-if="bulkGroup" type="button" class="sb-btn" @click="stopBulk">
          {{ t('characters.stop') }}
        </button>
        <button v-else-if="missingCount" type="button" class="sb-btn"
                :disabled="loading" @click="drawMissing">
          {{ t('characters.drawMissing', { n: missingCount }) }}
        </button>
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
            :style="{ background: hairSwatch(h) }"
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
            :style="{ background: eyeSwatch(e) }"
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
            @click="toggle(activeTraits, trait)"
          >{{ trait }}</button>
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
            class="ch-card flex flex-col rounded-xl border overflow-hidden transition-all duration-200"
            :class="c.id === selectedId
              ? 'border-teal-400/80 ring-2 ring-teal-400/40'
              : 'border-white/10 hover:border-white/30 hover:-translate-y-1'"
            :style="{ animationDelay: `${Math.min(i, 24) * 22}ms` }"
          >
            <button
              type="button"
              class="group relative block w-full text-left"
              @click="dossierId = c.id"
            >
              <span class="block aspect-[3/4] bg-black/50 overflow-hidden">
                <img
                  v-if="anyImage(c)"
                  :src="sheet(c) || face(c)"
                  :alt="label(c)"
                  loading="lazy"
                  class="w-full h-full object-cover transition-transform duration-500
                         group-hover:scale-105"
                />
                <span v-else
                      class="w-full h-full grid place-items-center text-[10px] text-gray-600 px-3 text-center">
                  {{ t('characters.noPortrait') }}
                </span>
              </span>
              <!-- her face, so you know who this is even when the sheet is busy -->
              <img
                v-if="face(c)"
                :src="face(c)"
                class="absolute top-2 left-2 w-11 h-11 rounded-full object-cover
                       border-2 border-black/60 shadow-lg"
                alt=""
                loading="lazy"
              />
              <span
                v-if="c.id === selectedId"
                class="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px]
                       bg-teal-500/90 text-black font-medium"
              >{{ t('characters.chosen') }}</span>
            </button>

            <div class="p-2.5 flex flex-col gap-1.5 grow">
              <div class="flex items-center gap-1.5">
                <p class="text-[13px] text-gray-100 font-medium truncate mr-auto">{{ label(c) }}</p>
                <i class="w-2.5 h-2.5 rounded-full border border-white/25 shrink-0"
                   :style="{ background: hairSwatch(c.hair_color) }" :title="colorWord(c.hair_color)"></i>
                <i class="w-2.5 h-2.5 rounded-full border border-white/25 shrink-0"
                   :style="{ background: eyeSwatch(c.eye_color) }" :title="colorWord(c.eye_color)"></i>
              </div>
              <!-- the whole line, not an ellipsis: it is the only thing that
                   says what she is like before you open her -->
              <p class="text-[11px] text-gray-400 leading-relaxed">{{ blurb(c) }}</p>
              <div class="flex flex-wrap gap-1 mt-auto pt-1">
                <span
                  v-for="trait in (c.traits || []).slice(0, 3)"
                  :key="trait"
                  class="px-1.5 py-0.5 rounded bg-white/5 border border-white/10
                         text-[9px] text-gray-400"
                >{{ trait }}</span>
              </div>
              <div class="flex gap-1 pt-0.5">
                <button type="button" class="sb-btn flex-1 justify-center !py-1 !text-[10px]"
                        @click="emit('pick', c.id)">
                  {{ t('characters.drawWithHer') }}
                </button>
                <button type="button" class="sb-icon-btn !w-7 !h-7 !text-[11px]"
                        :title="t('characters.draw')" @click="draw(c.id)">✎</button>
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
                :src="sheet(current) || face(current)"
                :alt="label(current)"
                class="ch-deck-img max-h-[56vh] w-auto object-contain"
              />
              <div v-else class="p-16 text-center">
                <p class="text-xs text-gray-500 mb-3">{{ t('characters.noPortrait') }}</p>
                <button class="sb-btn" @click="draw(current.id)">{{ t('characters.draw') }}</button>
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
                  {{ t('characters.drawWithHer') }}
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
      @close="dossierId = ''"
      @pick="emit('pick', $event)"
      @toast="emit('toast', $event)"
      @changed="reload"
      @update:workflow="emit('update:workflow', $event)"
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

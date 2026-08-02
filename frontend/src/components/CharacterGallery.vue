<script setup>
/*
 * Pick a character by looking at her.
 *
 * The old picker was the right idea in the wrong place: a two-column image grid
 * inside a 320px rail, clipped to 320px of height, so each face was 140px and
 * two rows showed at a time. A hundred characters read as a hundred name
 * labels, which is the list the pictures were supposed to replace.
 *
 * So it gets the whole screen. Big enough to recognise someone, with her traits
 * and her one-line summary next to her, filterable by the traits themselves
 * because "I want a quiet one" is how people actually choose.
 *
 * Drawing is part of the same screen rather than a separate errand. A character
 * with no face has a button where the face goes; one you do not like can be
 * re-rolled as often as you want, and every attempt is kept so you can go back
 * to the third one.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: { type: Boolean, default: false },
  selectedId: { type: String, default: '' },
  // Drawing needs a checkpoint, and the user changes it per run, so it is asked
  // for here rather than assumed.
  workflows: { type: Array, default: () => [] },
  workflow: { type: String, default: '' },
})
const emit = defineEmits(['pick', 'close', 'toast', 'update:workflow'])
const { t, locale } = useI18n()

const characters = ref([])
const loading = ref(false)
const busyId = ref('')
const query = ref('')
const activeTraits = ref([])
const openId = ref('')

const isJa = computed(() => String(locale.value).startsWith('ja'))
function label(c) { return (isJa.value ? c.name_ja : c.name) || c.name || c.preset_key }
function blurb(c) { return (isJa.value ? c.summary_ja : c.summary) || '' }
function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function portrait(c) { return thumb(c.board?.portrait || c.board?.sheet || '') }

// The traits worth offering as filters: the ones enough characters share to
// narrow anything down. A trait one character has is not a filter, it is a name.
const traitOptions = computed(() => {
  const counts = new Map()
  for (const c of characters.value) {
    for (const trait of c.traits || []) counts.set(trait, (counts.get(trait) || 0) + 1)
  }
  return [...counts.entries()]
    .filter(([, n]) => n >= 3)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 16)
    .map(([trait]) => trait)
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return characters.value.filter(c => {
    if (activeTraits.value.length
        && !activeTraits.value.every(x => (c.traits || []).includes(x))) return false
    if (!q) return true
    return `${c.name} ${c.name_ja} ${c.summary} ${c.summary_ja} ${(c.traits || []).join(' ')}`
      .toLowerCase().includes(q)
  })
})

const missingCount = computed(
  () => characters.value.filter(c => !c.board?.portrait).length,
)

function toggleTrait(trait) {
  const i = activeTraits.value.indexOf(trait)
  if (i >= 0) activeTraits.value.splice(i, 1)
  else activeTraits.value.push(trait)
}

// ── fetch ──────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
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

async function draw(id) {
  if (needsWorkflow()) return
  busyId.value = id
  try {
    await api(`/api/characters/${id}/board`, {
      method: 'POST',
      body: JSON.stringify({ workflow_name: props.workflow, slots: ['portrait'] }),
    })
    emit('toast', { msg: t('characters.queued'), type: 'info' })
  } catch (err) { fail(err) } finally { busyId.value = '' }
}

async function drawMissing() {
  if (needsWorkflow()) return
  loading.value = true
  try {
    const r = await api('/api/characters/thumbnails/missing', {
      method: 'POST', body: JSON.stringify({ workflow_name: props.workflow }),
    })
    emit('toast', { msg: t('characters.queuedN', { n: r.queued }), type: 'info' })
  } catch (err) { fail(err) } finally { loading.value = false }
}

async function chooseThumbnail(id, sha) {
  busyId.value = id
  try {
    await api(`/api/characters/${id}/thumbnail`, {
      method: 'POST', body: JSON.stringify({ sha256: sha }),
    })
    const row = characters.value.find(c => c.id === id)
    if (row) row.board = { ...(row.board || {}), portrait: sha }
  } catch (err) { fail(err) } finally { busyId.value = '' }
}

watch(() => props.show, open => { if (open) reload() })
onMounted(() => { if (props.show) reload() })
defineExpose({ reload })
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[var(--z-panel)] flex items-stretch justify-center
           bg-black/80 backdrop-blur-sm p-3"
    @mousedown.self="emit('close')"
  >
    <div class="sb-shell w-full max-w-[1600px] flex flex-col min-h-0">
      <header class="flex flex-wrap items-center gap-3 px-4 py-3 sb-hairline shrink-0">
        <div class="min-w-0 mr-auto">
          <h2 class="sb-display text-base text-[var(--sb-amber)]">{{ t('characters.title') }}</h2>
          <p class="text-[11px] text-[var(--sb-muted)]">
            {{ t('characters.count', { shown: filtered.length, total: characters.length }) }}
          </p>
        </div>

        <input
          v-model="query"
          type="search"
          class="sb-input w-56"
          :placeholder="t('characters.search')"
        />
        <select
          class="sb-select w-64"
          :value="workflow"
          :title="t('characters.workflow')"
          @change="emit('update:workflow', $event.target.value)"
        >
          <option value="">{{ t('characters.workflow') }} —</option>
          <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
        </select>
        <button
          v-if="missingCount"
          type="button"
          class="sb-btn"
          :disabled="loading"
          @click="drawMissing"
        >{{ t('characters.drawMissing', { n: missingCount }) }}</button>
        <button class="sb-icon-btn" :title="t('muse.close')" @click="emit('close')">✕</button>
      </header>

      <div v-if="traitOptions.length" class="flex flex-wrap gap-1 px-4 py-2 sb-hairline shrink-0">
        <button
          v-for="trait in traitOptions"
          :key="trait"
          type="button"
          class="sb-chip"
          :class="activeTraits.includes(trait) ? 'is-chip-on-teal' : ''"
          @click="toggleTrait(trait)"
        >{{ trait }}</button>
        <button
          v-if="activeTraits.length"
          type="button"
          class="sb-chip"
          @click="activeTraits = []"
        >✕ {{ t('characters.clearFilter') }}</button>
      </div>

      <div class="flex-1 overflow-y-auto p-4 min-h-0">
        <p v-if="!characters.length" class="text-xs text-gray-500 py-10 text-center">
          {{ loading ? '…' : t('characters.empty') }}
        </p>

        <div
          v-else
          class="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
        >
          <div v-for="c in filtered" :key="c.id" class="flex flex-col">
            <button
              type="button"
              class="group relative rounded-xl border overflow-hidden text-left transition-all
                     focus:outline-none focus:ring-2 focus:ring-teal-400/50"
              :class="c.id === selectedId
                ? 'border-teal-400/80 ring-2 ring-teal-400/40'
                : 'border-white/10 hover:border-white/30 hover:-translate-y-0.5'"
              @click="emit('pick', c.id)"
            >
              <div class="aspect-[3/4] bg-black/50 flex items-center justify-center overflow-hidden">
                <img
                  v-if="portrait(c)"
                  :src="portrait(c)"
                  :alt="label(c)"
                  loading="lazy"
                  class="w-full h-full object-cover transition-transform duration-300
                         group-hover:scale-[1.04]"
                />
                <span v-else class="text-[10px] text-gray-600 px-3 text-center leading-relaxed">
                  {{ t('characters.noPortrait') }}
                </span>
              </div>

              <div class="absolute inset-x-0 bottom-0 p-2
                          bg-gradient-to-t from-black/85 via-black/55 to-transparent">
                <p class="text-[12px] text-gray-100 font-medium truncate">{{ label(c) }}</p>
                <p class="text-[10px] text-gray-400 truncate">{{ blurb(c) }}</p>
              </div>

              <span
                v-if="c.id === selectedId"
                class="absolute top-2 right-2 px-1.5 py-0.5 rounded text-[10px]
                       bg-teal-500/90 text-black font-medium"
              >{{ t('characters.chosen') }}</span>
            </button>

            <div class="flex flex-wrap gap-1 mt-1.5 min-h-[18px]">
              <span
                v-for="trait in (c.traits || []).slice(0, 3)"
                :key="trait"
                class="px-1.5 py-0.5 rounded bg-white/5 border border-white/10
                       text-[9px] text-gray-400"
              >{{ trait }}</span>
            </div>

            <div class="flex items-center gap-1 mt-1">
              <button
                type="button"
                class="sb-btn flex-1 justify-center !py-1 !text-[10px]"
                :disabled="busyId === c.id"
                @click="draw(c.id)"
              >{{ c.board?.portrait ? t('characters.redraw') : t('characters.draw') }}</button>
              <button
                v-if="(c.gallery || []).length > 1"
                type="button"
                class="sb-icon-btn !w-7 !h-7 !text-[11px]"
                :title="t('characters.candidates')"
                @click="openId = openId === c.id ? '' : c.id"
              >⋯</button>
            </div>

            <!-- every portrait ever drawn for her; the fifth is not
                 automatically better than the second -->
            <div
              v-if="openId === c.id"
              class="flex gap-1 mt-1 overflow-x-auto pb-1"
            >
              <button
                v-for="sha in c.gallery"
                :key="sha"
                type="button"
                class="shrink-0 w-12 aspect-[3/4] rounded border overflow-hidden"
                :class="sha === c.board?.portrait
                  ? 'border-teal-400/80' : 'border-white/10 hover:border-white/40'"
                :title="t('characters.useThis')"
                @click="chooseThumbnail(c.id, sha)"
              >
                <img :src="thumb(sha)" class="w-full h-full object-cover" alt="" loading="lazy" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

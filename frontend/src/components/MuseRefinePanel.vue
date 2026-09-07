<script setup>
/*
 * Muse Refine — independent ledger studio.
 * Does not share MusePanel state or the full notebook pipeline.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import ActressDiaryModal from './muse/ActressDiaryModal.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, default: () => () => new Map() },
})
const emit = defineEmits(['update:show', 'toast', 'select-image'])
const { t, locale } = useI18n()

const session = ref(null)
const catalog = ref(null)
const characterList = ref([])
const busy = ref(false)
const showSettings = ref(false)
const DEBUG_KEY = 'museRefine.debug'
const museDebug = ref(typeof localStorage !== 'undefined' && localStorage.getItem(DEBUG_KEY) === '1')
function toggleDebug() {
  museDebug.value = !museDebug.value
  localStorage.setItem(DEBUG_KEY, museDebug.value ? '1' : '0')
}
const chatInput = ref('')
const chatEl = ref(null)
const preview = ref('')
const showDiary = ref(false)
const themeDraft = ref('')
let es = null
let pollTimer = null

const isJa = computed(() => String(locale.value).startsWith('ja'))
const inputs = computed(() => session.value?.inputs || {})
const ledger = computed(() => session.value?.refine_ledger || {})
const craft = computed(() => session.value?.craft || {})
const chat = computed(() => session.value?.chat || [])
const refineLog = computed(() => [...(session.value?.refine_log || [])].slice().reverse())
const stageMs = computed(() => [...(session.value?.stage_ms || [])].slice(-12).reverse())
const turnTrace = computed(() => [...(session.value?.turn_trace || [])].slice().reverse())
const characters = computed(() => characterList.value)
const workflows = computed(() => {
  const list = catalog.value?.comfyui?.workflows || catalog.value?.workflows || []
  return Array.isArray(list) ? list : []
})
const models = computed(() => catalog.value?.llm?.models || [])
const boardImages = computed(() => session.value?.board?.images || [])
const shootImages = computed(() => session.value?.shoot?.images || [])
const boardReady = computed(() => !!session.value?.board?.ready)
const partner = computed(() => session.value?.partner_character || {})
const standing = computed(() => session.value?.standing || [])
const lastPitch = computed(() => session.value?.last_pitch || [])
const bond = computed(() => session.value?.bond || {})
const banned = computed(() => session.value?.banned || [])
const tasteChips = computed(() => session.value?.taste_chips || [])
const opened = computed(() => !!session.value?.opened)
const diaryState = computed(() => session.value?.diary || {})
const diaryDone = computed(() => diaryState.value.status === 'ok')
const diaryWriting = computed(() => diaryState.value.status === 'writing')
const againFeelAvailable = computed(() => !!session.value?.again_feel_available)
const restateFields = [
  'wearing', 'beat', 'expression', 'scene', 'light', 'bg', 'frame',
  'lettering', 'atmosphere', 'look',
]
const stickyFields = new Set(['atmosphere', 'look', 'lettering'])
const ledgerRows = computed(() => {
  const led = ledger.value || {}
  const rows = [
    'wearing', 'beat', 'expression', 'scene', 'light', 'bg', 'frame',
    'lettering', 'atmosphere', 'look',
  ]
  if (partner.value?.character_id) {
    rows.push('wearing_b', 'beat_b')
  }
  return rows.map((key) => ({
    key,
    label: t(`museRefine.fields.${key}`),
    value: led[key] || '',
    sticky: stickyFields.has(key),
  }))
})

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
function fail(err) {
  emit('toast', { msg: String(err?.message || err), type: 'error' })
}
function close() {
  emit('update:show', false)
}

async function ensureCatalog() {
  if (!catalog.value) catalog.value = await api('/api/muse-refine/catalog')
  if (!characterList.value.length) {
    const data = await api('/api/characters')
    characterList.value = data.characters || []
  }
}

async function startFresh(characterId = '') {
  busy.value = true
  try {
    await ensureCatalog()
    const body = {
      locale: isJa.value ? 'ja' : 'en',
      model: models.value[0] || '',
      workflow: workflows.value[0] || '',
      use_wd14: false,
      enhance_quality: false,
    }
    if (characterId) body.character_id = characterId
    session.value = await api('/api/muse-refine/sessions', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    openStream(session.value.session_id)
    startPoll()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function patchInputs(patch) {
  if (!session.value?.session_id) return
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/inputs`,
      { method: 'PATCH', body: JSON.stringify(patch) },
    )
  } catch (err) {
    fail(err)
  }
}

async function pickCharacter(id) {
  if (!id) return
  if (!session.value?.session_id) {
    await startFresh(id)
    return
  }
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/character`,
      { method: 'POST', body: JSON.stringify({ character_id: id }) },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function pickPartner(id) {
  if (!session.value?.session_id) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/partner`,
      { method: 'POST', body: JSON.stringify({ partner_preset: id || '' }) },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function sendPitch(opt) {
  chatInput.value = `「${opt}」がいいな`
  await sendChat()
}

async function insertChip(text) {
  chatInput.value = text
  await sendChat()
}

async function openSession() {
  if (!session.value?.session_id || busy.value) return
  if (!inputs.value.character_id) {
    fail(new Error(t('museRefine.needCharacter')))
    return
  }
  busy.value = true
  try {
    if (themeDraft.value.trim() && themeDraft.value.trim() !== (inputs.value.theme || '')) {
      await patchInputs({ theme: themeDraft.value.trim() })
    }
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/open`,
      { method: 'POST' },
    )
    await scrollChat()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function restoreBanned(tag) {
  if (!session.value?.session_id || busy.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/banned/restore`,
      { method: 'POST', body: JSON.stringify({ tag }) },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function restateField(field) {
  if (!session.value?.session_id || busy.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/restate`,
      { method: 'POST', body: JSON.stringify({ field }) },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function finishSession() {
  if (!session.value?.session_id || busy.value) return
  if (!shootImages.value.length) {
    fail(new Error(t('museRefine.finishNeedsShoot')))
    return
  }
  if (!window.confirm(t('museRefine.finishConfirm'))) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/finish`,
      { method: 'POST' },
    )
    emit('toast', { msg: t('museRefine.finishToast'), type: 'info' })
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

function faceForRow(row) {
  const id = row?.meta?.speaker_id
  if (id && id === partner.value?.character_id) {
    return partner.value?.board?.portrait || partner.value?.board?.sheet || ''
  }
  const c = session.value?.character || {}
  return c.board?.portrait || c.board?.sheet || ''
}
function thumb(sha) {
  return sha ? `/api/thumbnails/${sha}.webp` : ''
}

async function sendChat() {
  const msg = chatInput.value.trim()
  if (!msg || !session.value?.session_id || busy.value) return
  chatInput.value = ''
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/chat`,
      { method: 'POST', body: JSON.stringify({ message: msg }) },
    )
    await scrollChat()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function rebuild() {
  if (!session.value?.session_id || busy.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/rebuild`,
      { method: 'POST' },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function runStage(path) {
  if (!session.value?.session_id || busy.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/${path}`,
      { method: 'POST' },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function scrollChat() {
  await nextTick()
  if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
}

function openStream(id) {
  closeStream()
  if (!id) return
  es = new EventSource(
    `/api/muse-refine/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`,
  )
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.type === 'preview' && data.image) {
        preview.value = `data:image/jpeg;base64,${data.image}`
      }
      if (data.type === 'session_updated') refresh()
    } catch { /* ignore */ }
  }
}
function closeStream() {
  if (es) {
    es.close()
    es = null
  }
}
async function refresh() {
  if (!session.value?.session_id) return
  try {
    session.value = await api(`/api/muse-refine/sessions/${session.value.session_id}`)
  } catch { /* ignore */ }
}
function startPoll() {
  stopPoll()
  pollTimer = setInterval(refresh, 4000)
}
function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(() => session.value?.inputs?.theme, (theme) => {
  if (theme != null && !themeDraft.value) themeDraft.value = String(theme)
})

watch(() => props.show, async (open) => {
  if (!open) {
    closeStream()
    stopPoll()
    return
  }
  try {
    await ensureCatalog()
    if (!session.value) await startFresh()
    else {
      openStream(session.value.session_id)
      startPoll()
    }
  } catch (err) {
    fail(err)
  }
})

onBeforeUnmount(() => {
  closeStream()
  stopPoll()
})

function rowChips(row) {
  const chips = row?.meta?.chips
  if (Array.isArray(chips) && chips.length) return chips
  return []
}
function isBanterRow(row) {
  return (row?.meta?.kind || row?.kind) === 'banter'
}
function isChangeRow(row) {
  const kind = row?.meta?.kind
  return kind === 'ledger_change' || kind === 'ledger_missed'
    || kind === 'verify_ok' || kind === 'verify_repair' || kind === 'verify_repaired'
}
function rowKindLabel(row, t) {
  const kind = row?.meta?.kind
  if (kind === 'banter') return t('museRefine.asideTitle')
  if (kind === 'ledger_change' || kind === 'ledger_missed') return t('museRefine.shotChange')
  if (kind === 'verify_ok') return t('museRefine.verifyOk')
  if (kind === 'verify_repair' || kind === 'verify_repaired') return t('museRefine.verifyRepair')
  if (kind === 'pitch') return t('museRefine.pitch')
  if (kind === 'standing') return t('museRefine.standing')
  if (kind === 'contract') return t('museRefine.contract')
  if (kind === 'wardrobe') return t('museRefine.wardrobe')
  if (kind === 'theme') return t('museRefine.theme')
  return row.name || row.role
}
function isStruckRow(row) {
  return !!(row?.meta?.struck)
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show"
      class="fixed inset-0 z-[var(--z-panel-muse)] flex items-stretch justify-end bg-black/70"
      @keydown.esc.stop="close"
    >
      <div
        class="flex h-full w-full max-w-5xl flex-col border-l border-teal-900/50 bg-[#0b1214] text-gray-100 shadow-2xl"
      >
        <header class="flex items-center gap-2 border-b border-teal-900/40 px-4 py-3">
          <div class="min-w-0 flex-1">
            <h2 class="text-base font-semibold tracking-wide text-teal-200">
              {{ t('museRefine.title') }}
            </h2>
            <p class="truncate text-[11px] text-gray-500">{{ t('museRefine.subtitle') }}</p>
          </div>
          <button
            type="button"
            class="rounded-lg px-2.5 py-1.5 text-xs"
            :class="museDebug ? 'bg-amber-900/70 text-amber-100' : 'bg-gray-800 hover:bg-gray-700'"
            :title="t('museRefine.debugToggle')"
            @click="toggleDebug"
          >{{ t('museRefine.debugToggle') }}</button>
          <button
            type="button"
            class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700"
            :disabled="busy"
            @click="showSettings = !showSettings"
          >{{ t('museRefine.settings') }}</button>
          <button
            type="button"
            class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700"
            :disabled="busy"
            @click="startFresh()"
          >{{ t('museRefine.reset') }}</button>
          <button
            type="button"
            class="rounded-full px-2 py-1 text-gray-400 hover:bg-teal-950/60 hover:text-white"
            @click="close"
          >✕</button>
        </header>

        <div class="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[1.1fr_0.9fr]">
          <!-- Chat + ledger -->
          <section class="flex min-h-0 flex-col border-r border-teal-950/40">
            <div class="border-b border-teal-950/30 px-3 py-2">
              <div class="flex flex-wrap items-center gap-2">
                <select
                  class="max-w-[10rem] truncate rounded-md border border-teal-900/50 bg-teal-950/40 px-2 py-1 text-xs text-teal-100"
                  :value="inputs.character_id || ''"
                  :disabled="busy"
                  @change="pickCharacter($event.target.value)"
                >
                  <option value="">{{ t('museRefine.pickCharacter') }}</option>
                  <option
                    v-for="c in characters"
                    :key="c.id"
                    :value="c.id"
                  >
                    {{ (isJa ? (c.name_ja || c.name) : (c.name || c.name_ja)) || c.id }}
                  </option>
                </select>
                <select
                  class="max-w-[10rem] truncate rounded-md border border-fuchsia-900/40 bg-fuchsia-950/30 px-2 py-1 text-xs text-fuchsia-100"
                  :value="inputs.partner_preset || ''"
                  :disabled="busy || !inputs.character_id"
                  @change="pickPartner($event.target.value)"
                >
                  <option value="">{{ t('museRefine.noPartner') }}</option>
                  <option
                    v-for="c in characters.filter(x => x.id !== inputs.character_id)"
                    :key="`p-${c.id}`"
                    :value="c.id"
                  >
                    {{ (isJa ? (c.name_ja || c.name) : (c.name || c.name_ja)) || c.id }}
                  </option>
                </select>
                <span class="text-[10px] font-medium uppercase tracking-wide text-teal-500/80">NOW</span>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <input
                  v-model="themeDraft"
                  type="text"
                  class="min-w-0 flex-1 rounded-md border border-teal-900/40 bg-teal-950/30 px-2 py-1 text-[11px] text-teal-50 outline-none focus:border-teal-600"
                  :placeholder="t('museRefine.themePlaceholder')"
                  :disabled="busy"
                  @change="patchInputs({ theme: themeDraft.trim() })"
                />
                <button
                  type="button"
                  class="rounded-lg bg-rose-800/80 px-2.5 py-1 text-[11px] font-medium text-rose-50 hover:bg-rose-700 disabled:opacity-40"
                  :disabled="busy || !inputs.character_id"
                  @click="openSession"
                >{{ opened ? t('museRefine.reopen') : t('museRefine.open') }}</button>
              </div>
              <p class="mt-1 text-[11px] leading-snug text-teal-100/80">
                {{ craft.now || t('museRefine.nowEmpty') }}
              </p>
              <p class="mt-0.5 text-[10px] text-gray-500">{{ t('museRefine.nowAuthority') }}</p>
              <div
                v-if="ledger.atmosphere || ledger.look || ledger.lettering"
                class="mt-1.5 flex flex-wrap gap-1.5"
              >
                <span
                  v-if="ledger.atmosphere"
                  class="inline-flex max-w-full items-center gap-1 rounded-full border border-violet-700/50 bg-violet-950/50 px-2 py-0.5 text-[10px] text-violet-100"
                  :title="t('museRefine.fields.atmosphere')"
                >
                  <span class="shrink-0 opacity-70">🌫</span>
                  <span class="truncate">{{ ledger.atmosphere }}</span>
                </span>
                <span
                  v-if="ledger.look"
                  class="inline-flex max-w-full items-center gap-1 rounded-full border border-fuchsia-700/50 bg-fuchsia-950/50 px-2 py-0.5 text-[10px] text-fuchsia-100"
                  :title="t('museRefine.fields.look')"
                >
                  <span class="shrink-0 opacity-70">🎨</span>
                  <span class="truncate">{{ ledger.look }}</span>
                </span>
                <span
                  v-if="ledger.lettering"
                  class="inline-flex max-w-full items-center gap-1 rounded-full border border-sky-700/50 bg-sky-950/50 px-2 py-0.5 text-[10px] text-sky-100"
                  :title="t('museRefine.fields.lettering')"
                >
                  <span class="shrink-0 opacity-70">🔤</span>
                  <span class="truncate">{{ ledger.lettering }}</span>
                </span>
              </div>
              <p v-if="bond.last" class="mt-1 text-[10px] text-rose-200/70">
                {{ t('museRefine.bondHint') }}: {{ bond.last }}
              </p>
            </div>

            <div ref="chatEl" class="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3 text-sm">
              <div
                v-for="(row, i) in chat"
                :key="i"
                class="rounded-lg px-2.5 py-2"
                :class="[
                  row.role === 'user'
                    ? (isStruckRow(row)
                      ? 'bg-gray-900/50 text-gray-500 line-through decoration-amber-700/80'
                      : 'bg-teal-950/40 text-teal-50')
                    : isBanterRow(row)
                      ? 'ml-4 border border-dashed border-pink-400/45 bg-gradient-to-br from-pink-950/50 via-rose-950/40 to-fuchsia-950/30 text-[11px] italic text-pink-200/95'
                      : row.meta?.kind === 'verify_ok' || row.meta?.kind === 'verify_repaired'
                      ? 'border border-emerald-800/40 bg-emerald-950/25 text-emerald-50'
                      : row.meta?.kind === 'verify_repair'
                        ? 'border border-orange-800/40 bg-orange-950/25 text-orange-50'
                        : isChangeRow(row)
                      ? (row.meta?.kind === 'ledger_missed'
                        ? 'border border-amber-700/50 bg-amber-950/30 text-[11px] text-amber-100'
                        : 'border border-teal-800/40 bg-teal-950/20 text-[11px] text-teal-100')
                      : row.role === 'system'
                        ? 'bg-gray-900/80 text-[11px] text-gray-400'
                        : 'bg-gray-900 text-gray-100',
                ]"
              >
                <div class="mb-0.5 flex flex-wrap items-center gap-1.5">
                  <img
                    v-if="row.role === 'assistant' && faceForRow(row)"
                    :src="thumb(faceForRow(row))"
                    class="h-5 w-5 rounded-full object-cover"
                    alt=""
                  />
                  <span
                    class="text-[10px] uppercase tracking-wide"
                    :class="isBanterRow(row) ? 'text-pink-300/90 font-medium' : 'text-gray-500'"
                  >
                    <template v-if="isBanterRow(row)">💭 {{ t('museRefine.asideTitle') }} · {{ row.name }}</template>
                    <template v-else-if="row.meta?.speaker">{{ row.name }} · {{ row.meta.speaker }}</template>
                    <template v-else>{{ rowKindLabel(row, t) }}</template>
                  </span>
                  <span
                    v-if="isStruckRow(row)"
                    class="rounded-full border border-amber-700/50 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-200"
                  >{{ t('museRefine.struck') }}</span>
                  <span
                    v-for="chip in rowChips(row)"
                    :key="chip.key"
                    class="inline-flex items-center gap-0.5 rounded-full border px-1.5 py-0.5 text-[10px] leading-none not-italic"
                    :class="chip.key === 'missed'
                      ? 'border-amber-600/60 bg-amber-900/50 text-amber-100'
                      : chip.key === 'repair'
                        ? 'border-orange-600/60 bg-orange-900/40 text-orange-100'
                        : chip.key === 'ok'
                          ? 'border-emerald-600/60 bg-emerald-900/40 text-emerald-100'
                          : 'border-teal-600/50 bg-teal-900/60 text-teal-50'"
                    :title="chip.key"
                  >
                    <span aria-hidden="true">{{ chip.icon }}</span>
                    <span>{{ chip.label }}</span>
                  </span>
                </div>
                <div v-if="!isChangeRow(row) || row.text" class="whitespace-pre-wrap leading-relaxed">
                  {{ row.text }}
                </div>
              </div>
              <p v-if="!chat.length" class="text-xs text-gray-500">{{ t('museRefine.chatHint') }}</p>
            </div>

            <form class="flex flex-col gap-2 border-t border-teal-950/40 p-3" @submit.prevent="sendChat">
              <div v-if="tasteChips.length || againFeelAvailable || lastPitch.length" class="flex flex-wrap gap-1.5">
                <button
                  v-for="chip in tasteChips"
                  :key="`taste-${chip}`"
                  type="button"
                  class="rounded-full border border-sky-800/50 bg-sky-950/40 px-2.5 py-1 text-[11px] text-sky-100 hover:bg-sky-900/50"
                  :disabled="busy"
                  @click="insertChip(chip)"
                >{{ chip }}</button>
                <button
                  v-if="againFeelAvailable"
                  type="button"
                  class="rounded-full border border-rose-800/50 bg-rose-950/40 px-2.5 py-1 text-[11px] text-rose-100 hover:bg-rose-900/50"
                  :disabled="busy"
                  @click="insertChip(t('museRefine.againFeelSend'))"
                >{{ t('museRefine.againFeel') }}</button>
                <button
                  v-for="opt in lastPitch"
                  :key="opt"
                  type="button"
                  class="rounded-full border border-violet-700/50 bg-violet-950/40 px-2.5 py-1 text-[11px] text-violet-100 hover:bg-violet-900/50"
                  :disabled="busy"
                  @click="sendPitch(opt)"
                >「{{ opt }}」</button>
              </div>
              <div class="flex gap-2">
                <input
                  v-model="chatInput"
                  type="text"
                  class="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm outline-none focus:border-teal-600"
                  :placeholder="t('museRefine.chatPlaceholder')"
                  :disabled="busy || !session"
                />
                <button
                  type="submit"
                  class="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white hover:bg-teal-600 disabled:opacity-40"
                  :disabled="busy || !chatInput.trim()"
                >{{ t('museRefine.send') }}</button>
              </div>
            </form>
          </section>

          <!-- Prompt / options / images -->
          <section class="flex min-h-0 flex-col gap-3 overflow-y-auto p-3">
            <div class="rounded-xl border border-teal-950/50 bg-gray-950/60 p-3">
              <div class="mb-2 text-xs font-medium text-teal-200/90">{{ t('museRefine.ledger') }}</div>
              <p class="mb-2 text-[10px] text-gray-500">{{ t('museRefine.ledgerStickyHint') }}</p>
              <dl class="grid grid-cols-[5.5rem_1fr] gap-x-2 gap-y-1 text-[11px]">
                <template v-for="row in ledgerRows" :key="row.key">
                  <dt
                    class="truncate"
                    :class="row.sticky ? 'text-violet-300/90' : 'text-gray-500'"
                    :title="row.key"
                  >{{ row.label }}</dt>
                  <dd
                    class="break-words"
                    :class="row.sticky && row.value
                      ? 'text-violet-100'
                      : 'text-gray-200'"
                  >{{ row.value || '—' }}</dd>
                </template>
              </dl>
              <div v-if="standing.length" class="mt-2 border-t border-teal-950/40 pt-2">
                <div class="mb-1 text-[10px] uppercase tracking-wide text-amber-200/70">{{ t('museRefine.standing') }}</div>
                <ul class="space-y-0.5 text-[11px] text-amber-100/80">
                  <li v-for="(s, i) in standing" :key="i">· {{ s }}</li>
                </ul>
              </div>
              <div v-if="banned.length" class="mt-2 border-t border-teal-950/40 pt-2">
                <div class="mb-1 text-[10px] uppercase tracking-wide text-red-200/70">{{ t('museRefine.banned') }}</div>
                <div class="flex flex-wrap gap-1">
                  <button
                    v-for="tag in banned"
                    :key="tag"
                    type="button"
                    class="rounded-full border border-red-800/50 bg-red-950/40 px-2 py-0.5 text-[10px] text-red-100 hover:bg-red-900/50"
                    :disabled="busy"
                    :title="t('museRefine.restoreBanned')"
                    @click="restoreBanned(tag)"
                  >✕ {{ tag }}</button>
                </div>
              </div>
              <div class="mt-2 flex flex-wrap gap-1 border-t border-teal-950/40 pt-2">
                <span class="w-full text-[10px] text-gray-500">{{ t('museRefine.restate') }}</span>
                <button
                  v-for="f in restateFields"
                  :key="f"
                  type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px] hover:border-teal-700"
                  :class="stickyFields.has(f)
                    ? 'border-violet-700/60 bg-violet-950/40 text-violet-100'
                    : 'border-gray-700 bg-gray-900 text-gray-300'"
                  :disabled="busy"
                  @click="restateField(f)"
                >{{ t(`museRefine.fields.${f}`) }}</button>
              </div>
            </div>

            <div class="rounded-xl border border-teal-950/50 bg-gray-950/60 p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-teal-200/90">{{ t('museRefine.options') }}</span>
                <button
                  type="button"
                  class="text-[11px] text-teal-300/80 hover:text-teal-200"
                  :disabled="busy"
                  @click="rebuild"
                >{{ t('museRefine.rebuild') }}</button>
              </div>
              <label class="mb-2 flex items-start gap-2 text-[11px] text-gray-300">
                <input
                  type="checkbox"
                  class="mt-0.5"
                  :checked="!!inputs.use_wd14"
                  :disabled="busy"
                  @change="patchInputs({ use_wd14: $event.target.checked })"
                />
                <span>
                  <span class="block text-gray-100">{{ t('museRefine.useWd14') }}</span>
                  <span class="text-gray-500">{{ t('museRefine.useWd14Hint') }}</span>
                </span>
              </label>
              <label class="flex items-start gap-2 text-[11px] text-gray-300">
                <input
                  type="checkbox"
                  class="mt-0.5"
                  :checked="!!inputs.enhance_quality"
                  :disabled="busy"
                  @change="patchInputs({ enhance_quality: $event.target.checked })"
                />
                <span>
                  <span class="block text-gray-100">{{ t('museRefine.enhanceQuality') }}</span>
                  <span class="text-gray-500">{{ t('museRefine.enhanceQualityHint') }}</span>
                </span>
              </label>
              <p v-if="craft.wd14_suggestions" class="mt-2 text-[10px] text-amber-200/70">
                WD14 {{ t('museRefine.wd14Ref') }}: {{ craft.wd14_suggestions }}
              </p>
              <p v-if="craft.picked_wd14" class="text-[10px] text-emerald-300/80">
                {{ t('museRefine.wd14Picked') }}: {{ craft.picked_wd14 }}
              </p>
              <p v-if="craft.quality_tags" class="text-[10px] text-sky-300/80">
                {{ t('museRefine.qualityTags') }}: {{ craft.quality_tags }}
              </p>
            </div>

            <div class="rounded-xl border border-teal-950/50 bg-gray-950/60 p-3">
              <div class="mb-1 text-xs font-medium text-teal-200/90">{{ t('museRefine.prompt') }}</div>
              <pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-gray-300">{{ craft.prompt || '—' }}</pre>
              <p v-if="craft.support_tags" class="mt-2 text-[10px] text-gray-500">
                support: {{ craft.support_tags }}
              </p>
            </div>

            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded-lg bg-teal-800/80 px-3 py-2 text-xs font-medium hover:bg-teal-700 disabled:opacity-40"
                :disabled="busy || comfyOffline || !craft.prompt"
                @click="runStage('board')"
              >{{ t('museRefine.board') }}</button>
              <button
                type="button"
                class="rounded-lg bg-cyan-800/80 px-3 py-2 text-xs font-medium hover:bg-cyan-700 disabled:opacity-40"
                :disabled="busy || comfyOffline || !craft.prompt || !boardReady"
                :title="boardReady ? '' : t('museRefine.approveNeedsBoard')"
                @click="runStage('approve')"
              >{{ t('museRefine.approve') }}</button>
              <button
                type="button"
                class="rounded-lg bg-gray-800 px-3 py-2 text-xs font-medium hover:bg-gray-700 disabled:opacity-40"
                :disabled="busy || !inputs.character_id"
                @click="runStage('wardrobe')"
              >{{ t('museRefine.wardrobe') }}</button>
              <button
                type="button"
                class="rounded-lg bg-rose-900/70 px-3 py-2 text-xs font-medium hover:bg-rose-800 disabled:opacity-40"
                :disabled="busy || !shootImages.length || diaryWriting"
                @click="finishSession"
              >{{ diaryWriting ? t('museRefine.diaryWriting') : diaryDone ? t('museRefine.diaryDone') : t('museRefine.finish') }}</button>
              <button
                v-if="diaryDone && inputs.character_id"
                type="button"
                class="rounded-lg bg-pink-900/70 px-3 py-2 text-xs font-medium hover:bg-pink-800"
                @click="showDiary = true"
              >{{ t('museRefine.openDiary') }}</button>
            </div>
            <p v-if="craft.prompt && !boardReady" class="text-[10px] text-gray-500">
              {{ t('museRefine.approveNeedsBoard') }}
            </p>

            <div v-if="preview" class="overflow-hidden rounded-xl border border-teal-950/50">
              <img :src="preview" alt="preview" class="max-h-56 w-full object-contain bg-black" />
            </div>

            <div class="flex flex-wrap gap-2">
              <button
                v-for="img in [...boardImages, ...shootImages]"
                :key="img.image_id || img.sha256 || img"
                type="button"
                class="h-16 w-16 overflow-hidden rounded-md border border-gray-800"
                @click="emit('select-image', img.image_id || img.sha256 || img)"
              >
                <img :src="thumb(img.image_id || img.sha256 || img)" class="h-full w-full object-cover" alt="" />
              </button>
            </div>

            <div v-if="showSettings" class="space-y-2 rounded-xl border border-gray-800 bg-gray-950 p-3 text-xs">
              <label class="block">
                <span class="mb-1 block text-gray-500">{{ t('museRefine.workflow') }}</span>
                <select
                  class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                  :value="inputs.workflow || ''"
                  @change="patchInputs({ workflow: $event.target.value })"
                >
                  <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
                </select>
              </label>
              <label class="block">
                <span class="mb-1 block text-gray-500">{{ t('museRefine.model') }}</span>
                <select
                  class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                  :value="inputs.model || ''"
                  @change="patchInputs({ model: $event.target.value })"
                >
                  <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
                </select>
              </label>
            </div>

            <details v-if="museDebug" class="rounded-xl border border-amber-900/40 bg-amber-950/20 p-3 text-[10px] text-amber-100/90" open>
              <summary class="cursor-pointer text-amber-200">{{ t('museRefine.debugTitle') }}</summary>
              <p class="mt-1 mb-2 text-amber-100/50">{{ t('museRefine.debugHint') }}</p>

              <div v-if="turnTrace.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('museRefine.turnTrace') }}</div>
                <ul class="space-y-1.5">
                  <li
                    v-for="(row, i) in turnTrace"
                    :key="`${row.at}-${i}`"
                    class="rounded border border-amber-800/40 px-2 py-1.5"
                  >
                    <div class="text-amber-100">{{ row.line || '—' }}</div>
                    <div class="text-amber-100/50">patch: {{ JSON.stringify(row.patch || {}) }}</div>
                    <div class="text-amber-100/50">propose: {{ JSON.stringify(row.propose || {}) }}</div>
                    <div
                      v-for="(delta, field) in (row.moved || {})"
                      :key="field"
                      class="text-emerald-200/80"
                    >{{ field }}: {{ delta }}</div>
                    <div v-if="(row.wd14_suggestions || []).length" class="text-amber-200/60">
                      wd14 ref: {{ (row.wd14_suggestions || []).slice(0, 12).join(', ') }}
                    </div>
                    <div v-if="(row.picked_wd14 || []).length" class="text-emerald-300/80">
                      wd14 picked: {{ (row.picked_wd14 || []).join(', ') }}
                    </div>
                    <div v-if="(row.quality_tags || []).length" class="text-sky-300/80">
                      quality: {{ (row.quality_tags || []).join(', ') }}
                    </div>
                  </li>
                </ul>
              </div>

              <div v-if="stageMs.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('museRefine.stageMs') }}</div>
                <ul class="space-y-0.5">
                  <li v-for="(s, i) in stageMs" :key="`${s.at}-${i}`" class="text-amber-100/70">
                    {{ s.stage }} · {{ s.ms }}ms
                  </li>
                </ul>
              </div>

              <div v-if="refineLog.length">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('museRefine.refineLog') }}</div>
                <ul class="max-h-48 space-y-1 overflow-y-auto">
                  <li
                    v-for="(row, i) in refineLog"
                    :key="`${row.at}-${i}`"
                    class="rounded border border-amber-900/30 px-2 py-1 text-amber-100/70"
                  >
                    <span class="text-amber-300/90">{{ row.kind }}</span>
                    — {{ row.detail }}
                  </li>
                </ul>
              </div>
            </details>
          </section>
        </div>
      </div>
    </div>
  </Teleport>

  <ActressDiaryModal
    v-if="showDiary && inputs.character_id"
    :show="showDiary"
    :character-id="inputs.character_id"
    :character-name="session?.character?.name || ''"
    @close="showDiary = false"
    @toast="emit('toast', $event)"
  />
</template>

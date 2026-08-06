<script setup>
/*
 * Muse Studio — the user is 総監督. Cast a crew of fictional Muses, chat until
 * the craft feels right, put up an image board ("これでいい？"), then OK to shoot.
 * No B/C/D chain; discussion + boards replace pickup.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import CharacterGallery from './CharacterGallery.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, default: () => () => new Map() },
})
const emit = defineEmits(['update:show', 'toast', 'select-image'])
const { t, locale } = useI18n()

const session = ref(null)
const catalog = ref(null)
const busy = ref(false)
const streamLive = ref(false)
const showPicker = ref(false)
const showSettings = ref(false)
const showCast = ref(false)
const preview = ref('')
const speaking = ref('')          // muse id currently streaming
const liveSay = ref('')
const chatInput = ref('')
const job = ref(null)
const elapsed = ref(0)
const chatEl = ref(null)
const FRAMINGS = ['auto', 'full_body', 'upper_body', 'face_closeup', 'from_behind']
const PRESETS = ['standard', 'vivid', 'photoreal', 'flat', 'classic', 'bold', 'calm', 'everyone']

let eventSource = null
let pollTimer = null
let startedAt = 0

const inputs = computed(() => session.value?.inputs || {})
const needs = computed(() => session.value?.needs || [])
const character = computed(() => session.value?.character || null)
const chat = computed(() => session.value?.chat || [])
const craft = computed(() => session.value?.craft || {})
const board = computed(() => session.value?.board || {})
const shoot = computed(() => session.value?.shoot || {})
const boardImages = computed(() => board.value.images || [])
const shootImages = computed(() => shoot.value.images || [])
const warnings = computed(() => session.value?.warnings || [])
const status = computed(() => session.value?.status || 'setup')
const roster = computed(() => session.value?.roster || catalog.value?.roster || {})
const muses = computed(() => roster.value.muses || [])
// Jobs, each with the people who can do it. Casting is picking a person, not
// a job — two lighting artists both light the scene, differently.
const crewRoles = computed(() => roster.value.roles || [])
const crewIds = computed(() => new Set(inputs.value.crew_ids || []))
// Where this cast pulls the picture. Recomputed server-side on every patch, so
// toggling a seat moves the meter and the base look in the same breath.
const direction = computed(() => roster.value.direction || {})
const tasteAxes = computed(() => roster.value.taste_axes || [])
const baseLook = computed(() => session.value?.style_in_use || direction.value.base || '')

const workflows = computed(() => catalog.value?.comfyui?.workflows || [])
const models = computed(() => catalog.value?.llm?.models || [])
const isJa = computed(() => String(locale.value).startsWith('ja'))

const act = computed(() => {
  if (!session.value) return 'setup'
  if (status.value === 'setup' && !chat.value.length) return 'setup'
  if (status.value === 'shooting' || status.value === 'done') return 'shoot'
  if (status.value === 'boarding' || status.value === 'awaiting_ok' || boardImages.value.length) {
    return 'board'
  }
  if (chat.value.length || status.value === 'chat' || status.value === 'discussing') return 'chat'
  return 'setup'
})

const canStart = computed(() =>
  !busy.value && needs.value.length === 0 && act.value === 'setup')
const chatLocked = computed(() =>
  busy.value || status.value === 'discussing' || status.value === 'boarding' ||
  status.value === 'shooting')

function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function full(sha) { return sha ? `/api/originals/${sha}` : '' }
function museLabel(m) {
  if (!m) return ''
  return isJa.value ? (m.name_ja || m.name) : m.name
}
function museNick(m) {
  if (!m) return ''
  return isJa.value ? (m.nick_ja || '') : (m.nick || '')
}
// -2…+2 becomes a five-step bar the eye can compare across seats.
function tasteBar(score) {
  const n = Math.max(-2, Math.min(2, Number(score) || 0))
  return '−・0・＋'.split('・')[n < 0 ? 0 : n > 0 ? 2 : 1] + (n ? Math.abs(n) : '')
}
function tasteWidth(score) {
  return `${(Math.max(-2, Math.min(2, Number(score) || 0)) + 2) / 4 * 100}%`
}
function museById(id) {
  return muses.value.find(m => m.id === id)
}
function clock(s) {
  const m = Math.floor(s / 60)
  return m ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

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

watch(() => props.show, async open => {
  if (!open) { closeStream(); return }
  try {
    if (!catalog.value) catalog.value = await api('/api/muse/catalog')
    if (!session.value) await startSession()
    else connectStream(session.value.session_id)
  } catch (err) { fail(err) }
})
onBeforeUnmount(closeStream)

async function startSession() {
  const suggested = catalog.value?.suggested_run || {}
  session.value = await api('/api/muse/sessions', {
    method: 'POST',
    body: JSON.stringify({
      model: suggested.model || '',
      workflow: suggested.workflow || '',
      locale: isJa.value ? 'ja' : 'en',
      crew_preset: 'standard',
    }),
  })
  preview.value = ''
  liveSay.value = ''
  speaking.value = ''
  showSettings.value = false
  connectStream(session.value.session_id)
}

async function resetSession() {
  if (!window.confirm(t('muse.resetConfirm'))) return
  closeStream()
  session.value = null
  await startSession()
}
function close() { emit('update:show', false) }

function connectStream(id) {
  if (!id || eventSource) return
  eventSource = new EventSource(
    `/api/muse/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`)
  eventSource.onopen = () => { streamLive.value = true }
  eventSource.onmessage = async e => {
    let evt = null
    try { evt = JSON.parse(e.data) } catch { return }
    if (!evt?.type || evt.type === 'hello' || evt.type === 'ping') return
    if (evt.type === 'preview') {
      preview.value = `data:image/jpeg;base64,${evt.image}`
      return
    }
    if (evt.type === 'muse_speaking') {
      speaking.value = evt.muse_id || ''
      liveSay.value = ''
      return
    }
    if (evt.type === 'chat_delta') {
      if (speaking.value !== evt.muse_id) speaking.value = evt.muse_id || ''
      liveSay.value += evt.text || ''
      scrollChat()
      return
    }
    if (evt.type === 'chat_message') {
      speaking.value = ''
      liveSay.value = ''
      await refresh()
      scrollChat()
      return
    }
    if (evt.type === 'board_ready' || evt.type === 'board_attached' ||
        evt.type === 'shoot_attached' || evt.type === 'craft_updated' ||
        evt.type === 'session_updated') {
      if (evt.type === 'board_ready' || evt.type === 'shoot_attached') {
        preview.value = ''
      }
      await refresh()
      scrollChat()
      return
    }
    await refresh()
  }
  eventSource.onerror = () => { streamLive.value = false }
  startPoll()
}

function closeStream() {
  if (eventSource) { eventSource.close(); eventSource = null }
  streamLive.value = false
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function startPoll() {
  if (pollTimer) return
  let tick = 0
  pollTimer = setInterval(async () => {
    sampleJob()
    const running = board.value.pending || shoot.value.pending || status.value === 'discussing'
    if (!running) { tick = 0; return }
    if (!startedAt) startedAt = Date.now()
    elapsed.value = Math.round((Date.now() - startedAt) / 1000)
    if (++tick % 3 === 0) await refresh()
  }, 1000)
}

function sampleJob() {
  const map = props.getJobsMap?.()
  if (!map?.get) { job.value = null; return }
  const id = shoot.value.job_id || board.value.job_id
  job.value = id ? (map.get(id) || null) : null
}

async function refresh() {
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}`)
  } catch (err) { fail(err) }
}

async function scrollChat() {
  await nextTick()
  if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
}

async function patchInputs(patch) {
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/inputs`, {
      method: 'PATCH', body: JSON.stringify(patch),
    })
  } catch (err) { fail(err) }
}

async function pickCharacter(id) {
  showPicker.value = false
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/character`, {
      method: 'POST', body: JSON.stringify({ character_id: id }),
    })
  } catch (err) { fail(err) }
}

async function setPreset(p) {
  await patchInputs({ crew_preset: p })
}

async function toggleRole(role) {
  if (role.required) return
  // Off if anyone from this job is cast, otherwise on with the first person.
  const next = (inputs.value.crew_ids || []).filter(
    id => !role.people.some(p => p.id === id))
  if (next.length === (inputs.value.crew_ids || []).length) next.push(role.people[0].id)
  await patchInputs({ crew_ids: next })
}

async function pickPerson(role, person) {
  if (role.required) return
  const next = (inputs.value.crew_ids || []).filter(
    id => !role.people.some(p => p.id === id))
  next.push(person.id)
  await patchInputs({ crew_ids: next })
}

function castPerson(role) {
  return role.people.find(p => crewIds.value.has(p.id)) || null
}

async function startTable() {
  if (!session.value || !canStart.value) return
  busy.value = true
  startedAt = Date.now()
  elapsed.value = 0
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/table`, { method: 'POST' })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false }
}

async function sendChat(text) {
  const body = (text ?? chatInput.value).trim()
  if (!body || !session.value || chatLocked.value) return
  busy.value = true
  chatInput.value = ''
  startedAt = Date.now()
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/chat`, {
      method: 'POST', body: JSON.stringify({ text: body }),
    })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false }
}

function quick(cmd) { sendChat(cmd) }

async function onChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    await sendChat()
  }
}
</script>

<template>
  <div
    v-if="show"
    class="muse-root fixed inset-0 flex items-stretch justify-center bg-black/70 backdrop-blur-sm p-3"
    @mousedown.self="close"
  >
    <div class="sb-shell w-full max-w-[1500px] flex flex-col min-h-0">
      <header class="flex items-center justify-between gap-3 px-4 py-3 sb-hairline shrink-0">
        <div class="min-w-0">
          <h2 class="sb-display text-base text-[var(--sb-amber)]">{{ t('muse.title') }}</h2>
          <p class="text-[11px] text-[var(--sb-muted)] truncate">{{ t('muse.subtitle') }}</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <span class="text-[10px] text-[var(--sb-faint)]">SSE {{ streamLive ? '●' : '○' }}</span>
          <button class="sb-btn" :disabled="busy" @click="showCast = !showCast">{{ t('muse.cast') }}</button>
          <button class="sb-btn" :disabled="busy" @click="showSettings = !showSettings">{{ t('muse.settings') }}</button>
          <button class="sb-btn" :disabled="busy" @click="resetSession">{{ t('muse.reset') }}</button>
          <button class="sb-icon-btn" :title="t('muse.close')" @click="close">✕</button>
        </div>
      </header>

      <p v-for="w in warnings" :key="w" class="px-4 py-1 text-[11px] text-amber-400 shrink-0">{{ w }}</p>
      <p v-if="comfyOffline" class="px-4 py-1 text-[11px] text-red-400 shrink-0">{{ t('muse.warn.comfyOffline') }}</p>

      <main class="flex-1 min-h-0 flex flex-col md:flex-row">
        <!-- chat column -->
        <section class="flex-1 min-w-0 min-h-0 flex flex-col border-r border-white/10">
          <!-- setup -->
          <div v-if="act === 'setup'" class="flex-1 overflow-y-auto p-4 space-y-4 max-w-2xl mx-auto w-full">
            <h3 class="sb-display text-lg text-[var(--sb-amber)]">{{ t('muse.setupTitle') }}</h3>
            <p class="text-[11px] text-[var(--sb-muted)]">{{ t('muse.studioHint') }}</p>

            <label class="block space-y-1">
              <span class="sb-label">{{ t('muse.theme') }}</span>
              <textarea
                class="sb-textarea text-sm" rows="4"
                :placeholder="t('muse.themePlaceholder')"
                :value="inputs.theme"
                @change="patchInputs({ theme: $event.target.value })"
              ></textarea>
            </label>

            <button
              type="button"
              class="w-full flex items-center gap-3 p-3 rounded-lg border border-white/10 hover:border-white/25 text-left"
              @click="showPicker = true"
            >
              <img
                v-if="character?.board?.portrait || character?.board?.sheet"
                :src="thumb(character.board.portrait || character.board.sheet)"
                class="w-16 h-[84px] rounded object-cover shrink-0" alt=""
              />
              <span v-else class="w-16 h-[84px] rounded bg-black/40 shrink-0"></span>
              <span class="min-w-0 flex-1">
                <span class="block text-sm text-gray-200 truncate">
                  {{ (isJa ? character?.name_ja : character?.name) || t('muse.noCharacter') }}
                </span>
                <span class="block text-[10px] text-[var(--sb-faint)]">{{ t('muse.pickCharacter') }}</span>
              </span>
            </button>

            <div class="grid grid-cols-2 gap-3">
              <label class="block">
                <span class="sb-label">{{ t('muse.workflow') }}</span>
                <select class="sb-select" :value="inputs.workflow"
                        @change="patchInputs({ workflow: $event.target.value })">
                  <option value="">—</option>
                  <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
                </select>
              </label>
              <label class="block">
                <span class="sb-label">{{ t('muse.model') }}</span>
                <select class="sb-select" :value="inputs.model"
                        @change="patchInputs({ model: $event.target.value })">
                  <option value="">—</option>
                  <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
                </select>
              </label>
            </div>

            <div>
              <span class="sb-label">{{ t('muse.crewPreset') }}</span>
              <div class="flex flex-wrap gap-2 mt-1">
                <button
                  v-for="p in PRESETS" :key="p" type="button" class="sb-btn text-[10px]"
                  :class="inputs.crew_preset === p ? 'border-[var(--sb-teal)] text-[var(--sb-teal)]' : ''"
                  @click="setPreset(p)"
                >{{ t(`muse.presets.${p}`) }}</button>
              </div>
            </div>

            <button class="sb-btn w-full py-2" :disabled="!canStart" @click="startTable">
              {{ busy ? '…' : t('muse.cta.table') }}
            </button>
            <p v-if="needs.length" class="text-[11px] text-amber-400">
              {{ needs.map(n => t(`muse.needs.${n}`)).join(' / ') }}
            </p>
          </div>

          <!-- chat -->
          <template v-else>
            <div ref="chatEl" class="flex-1 overflow-y-auto p-3 space-y-2">
              <div
                v-for="m in chat" :key="m.id"
                class="flex flex-col gap-0.5"
                :class="[
                  m.role === 'user' ? 'items-end' : 'items-start',
                  m.kind === 'banter' ? 'pl-4 opacity-90' : '',
                ]"
              >
                <span class="text-[10px] text-[var(--sb-faint)]">
                  <template v-if="m.role === 'user'">{{ t('muse.showrunner') }}</template>
                  <template v-else-if="m.kind === 'banter'">{{ m.name }} · {{ t('muse.banter') }}</template>
                  <template v-else>{{ m.name || 'Studio' }}</template>
                </span>
                <div
                  class="max-w-[90%] whitespace-pre-wrap"
                  :class="m.role === 'user'
                    ? 'rounded-lg px-3 py-2 text-[12px] bg-[var(--sb-teal)]/20 border border-teal-700/40 text-gray-100'
                    : m.role === 'system'
                      ? 'rounded-lg px-3 py-2 text-[12px] bg-white/5 border border-white/10 text-gray-400'
                      : m.kind === 'banter'
                        ? 'rounded px-2.5 py-1.5 text-[11px] bg-amber-950/20 border border-amber-800/30 text-amber-100/90 italic'
                        : 'rounded-lg px-3 py-2 text-[12px] bg-black/40 border border-white/10 text-gray-200'"
                >{{ m.text }}</div>
              </div>

              <div v-if="speaking && liveSay" class="flex flex-col items-start gap-0.5">
                <span class="text-[10px] text-[var(--sb-amber)] animate-pulse">
                  {{ museLabel(museById(speaking)) || speaking }} …
                </span>
                <div class="max-w-[90%] rounded-lg px-3 py-2 text-[12px] whitespace-pre-wrap
                            bg-black/40 border border-amber-700/30 text-gray-300">
                  {{ liveSay }}<span class="animate-pulse">▍</span>
                </div>
              </div>
            </div>

            <div class="shrink-0 border-t border-white/10 p-3 space-y-2">
              <div class="flex flex-wrap gap-2">
                <button class="sb-btn text-[10px]" :disabled="chatLocked" @click="quick(isJa ? 'ボード' : 'board')">
                  {{ t('muse.quick.board') }}
                </button>
                <button class="sb-btn text-[10px]" :disabled="chatLocked" @click="quick('OK')">
                  {{ t('muse.quick.ok') }}
                </button>
              </div>
              <div class="flex gap-2">
                <textarea
                  v-model="chatInput"
                  class="sb-textarea text-sm flex-1" rows="2"
                  :placeholder="t('muse.chatPlaceholder')"
                  :disabled="chatLocked"
                  @keydown="onChatKey"
                ></textarea>
                <button class="sb-btn shrink-0 px-4" :disabled="chatLocked || !chatInput.trim()" @click="sendChat()">
                  {{ t('muse.send') }}
                </button>
              </div>
              <p class="text-[10px] text-[var(--sb-faint)]">
                {{ status === 'discussing' ? t('muse.status.discussing') :
                   status === 'boarding' ? t('muse.status.boarding') :
                   status === 'awaiting_ok' ? t('muse.status.awaitingOk') :
                   status === 'shooting' ? t('muse.status.shooting') :
                   status === 'done' ? t('muse.status.done') : t('muse.status.chat') }}
                <span v-if="elapsed"> · {{ t('muse.elapsed', { s: clock(elapsed) }) }}</span>
              </p>
            </div>
          </template>
        </section>

        <!-- picture column -->
        <section class="w-full md:w-[42%] shrink-0 min-h-[40vh] md:min-h-0 flex flex-col p-3 gap-3 overflow-y-auto">
          <div v-if="preview || board.pending || shoot.pending" class="flex flex-col items-center gap-2">
            <img v-if="preview" :src="preview" alt=""
                 class="max-h-[48vh] rounded-lg border border-white/10 shadow-2xl" />
            <div v-else class="w-full aspect-[3/4] max-h-[48vh] rounded-lg bg-black/40 border border-white/10
                              flex items-center justify-center text-[11px] text-[var(--sb-faint)] animate-pulse">
              {{ job?.progress_text || '…' }}
            </div>
            <div class="w-full h-1 rounded bg-white/10 overflow-hidden">
              <div class="h-full bg-[var(--sb-teal)] transition-all"
                   :style="{ width: `${Math.round((job?.progress || 0) * 100)}%` }"></div>
            </div>
          </div>

          <div v-if="boardImages.length" class="space-y-2">
            <h4 class="text-[11px] text-[var(--sb-amber)]">{{ t('muse.boardTitle') }}</h4>
            <p class="text-[10px] text-[var(--sb-muted)]">{{ t('muse.boardAsk') }}</p>
            <div class="grid grid-cols-2 gap-2">
              <figure
                v-for="img in boardImages" :key="img.image_id"
                class="rounded overflow-hidden border border-white/10 cursor-pointer"
                @click="emit('select-image', img.image_id)"
              >
                <img :src="thumb(img.image_id)" class="w-full block" alt="" />
              </figure>
            </div>
          </div>

          <div v-if="shootImages.length" class="space-y-2">
            <h4 class="text-[11px] text-[var(--sb-amber)]">{{ t('muse.shootTitle') }}</h4>
            <div class="grid grid-cols-2 gap-2">
              <figure
                v-for="img in shootImages" :key="img.image_id"
                class="rounded overflow-hidden border border-[var(--sb-teal)]/40 cursor-pointer"
                @click="emit('select-image', img.image_id)"
              >
                <img :src="full(img.image_id)" class="w-full block" alt="" />
              </figure>
            </div>
          </div>

          <details v-if="craft.prompt" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">{{ t('muse.craft') }}</summary>
            <p class="whitespace-pre-wrap font-mono mt-1 text-gray-400">{{ craft.prompt }}</p>
          </details>
        </section>
      </main>

      <!-- cast drawer -->
      <section v-if="showCast"
               class="shrink-0 max-h-[40vh] overflow-y-auto border-t border-white/10 p-4 space-y-3">
        <div class="flex flex-wrap gap-2">
          <button
            v-for="p in PRESETS" :key="p" type="button" class="sb-btn text-[10px]"
            :class="inputs.crew_preset === p ? 'border-[var(--sb-teal)] text-[var(--sb-teal)]' : ''"
            @click="setPreset(p)"
          >{{ t(`muse.presets.${p}`) }}</button>
        </div>
        <!-- what this cast is pulling toward -->
        <div v-if="tasteAxes.length" class="rounded border border-white/10 bg-black/30 p-3">
          <div class="flex items-baseline justify-between gap-2 mb-2">
            <span class="sb-label">{{ t('muse.taste.title') }}</span>
            <span class="text-[10px] text-[var(--sb-faint)]">{{ t('muse.taste.hint') }}</span>
          </div>
          <div class="space-y-1.5">
            <div v-for="a in tasteAxes" :key="a.id" class="flex items-center gap-2">
              <span class="w-16 shrink-0 text-right text-[10px] text-[var(--sb-faint)]">{{ a.low }}</span>
              <span class="relative h-1.5 flex-1 rounded bg-white/10">
                <span class="absolute inset-y-0 w-px bg-white/25" style="left:50%"></span>
                <span class="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2
                             rounded-full bg-[var(--sb-teal)] transition-all duration-300"
                      :style="{ left: tasteWidth((direction.scores || {})[a.id]) }"></span>
              </span>
              <span class="w-16 shrink-0 text-[10px] text-[var(--sb-faint)]">{{ a.high }}</span>
            </div>
          </div>
          <p class="mt-2 text-[11px]">
            <span class="sb-label">{{ t('muse.taste.base') }}</span>
            <span class="ml-2 font-mono text-[var(--sb-amber)]">{{ baseLook || '—' }}</span>
          </p>
        </div>

        <!-- one row per job; pick which person does it -->
        <div class="space-y-1.5">
          <div v-for="r in crewRoles" :key="r.id"
               class="flex items-start gap-2 rounded border p-2 transition-colors"
               :class="r.required || castPerson(r)
                 ? 'border-[var(--sb-amber)]/40 bg-amber-950/10'
                 : 'border-white/10 opacity-45'">
            <button type="button"
                    class="w-24 shrink-0 text-left text-[11px] text-[var(--sb-amber)]"
                    :disabled="r.required || act !== 'setup'"
                    :title="isJa ? r.role_ja : r.role"
                    @click="toggleRole(r)">
              {{ isJa ? r.name_ja : r.name }}
            </button>
            <div class="flex flex-1 flex-wrap gap-1.5">
              <button
                v-for="p in r.people" :key="p.id" type="button"
                class="rounded border px-2 py-1 text-left text-[10px] transition-colors"
                :class="crewIds.has(p.id) || r.required
                  ? 'border-[var(--sb-teal)] text-[var(--sb-teal)] bg-teal-950/20'
                  : 'border-white/10 text-gray-400 hover:border-white/30'"
                :disabled="r.required || act !== 'setup'"
                :title="isJa ? (p.line_ja || p.line) : p.line"
                @click="pickPerson(r, p)"
              >
                <span class="block">「{{ museNick(p) }}」</span>
                <span v-if="p.taste" class="mt-0.5 flex gap-1 text-[9px] text-[var(--sb-faint)]">
                  <span v-for="a in tasteAxes" :key="a.id"
                        :class="p.taste[a.id] ? 'text-[var(--sb-teal)]' : ''">
                    {{ a.high.slice(0, 2) }}{{ tasteBar(p.taste[a.id]) }}
                  </span>
                </span>
              </button>
            </div>
            <span class="hidden md:block w-56 shrink-0 text-[10px] text-gray-500">
              {{ isJa ? (castPerson(r)?.line_ja || r.people[0].line_ja)
                      : (castPerson(r)?.line || r.people[0].line) }}
            </span>
          </div>
        </div>
      </section>

      <!-- settings -->
      <section v-if="showSettings"
               class="shrink-0 max-h-[40vh] overflow-y-auto border-t border-white/10 p-4
                      grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px]">
        <label class="block col-span-2">
          <span class="sb-label">{{ t('muse.style') }}</span>
          <input class="sb-input" type="text" :value="inputs.style"
                 @change="patchInputs({ style: $event.target.value })" />
        </label>
        <label class="block">
          <span class="sb-label">{{ t('muse.framing') }}</span>
          <select class="sb-select" :value="inputs.framing || 'auto'"
                  @change="patchInputs({ framing: $event.target.value })">
            <option v-for="f in FRAMINGS" :key="f" :value="f">{{ t(`muse.framingOpts.${f}`) }}</option>
          </select>
        </label>
        <label class="block"><span class="sb-label">{{ t('muse.draftCount') }}</span>
          <input class="sb-input" type="number" :value="inputs.draft_count"
                 @change="patchInputs({ draft_count: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.draftSteps') }}</span>
          <input class="sb-input" type="number" :value="inputs.draft_steps"
                 @change="patchInputs({ draft_steps: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.finalSteps') }}</span>
          <input class="sb-input" type="number" :value="inputs.final_steps"
                 @change="patchInputs({ final_steps: Number($event.target.value) })" /></label>
      </section>
    </div>

    <CharacterGallery
      :show="showPicker"
      :selected-id="inputs.character_id"
      :workflows="workflows"
      :workflow="inputs.workflow"
      :get-jobs-map="getJobsMap"
      @pick="pickCharacter"
      @close="showPicker = false"
      @toast="emit('toast', $event)"
      @update:workflow="patchInputs({ workflow: $event })"
    />
  </div>
</template>

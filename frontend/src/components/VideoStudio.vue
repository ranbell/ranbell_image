<script setup>
// The video studio: talk a still into a video prompt with an actress.
//
// Its own screen, apart from the Muse shoot (the Showrunner, 2026-10-11: the
// video talk living inside the photo panel was hard to use). Opened from any
// still's 🎬 — the gallery's detail view or a Muse test shot / final. A still
// from a Muse shoot seats that shoot's actress by default and the talk carries
// on from the shoot; any other picture waits for the Showrunner to pick who to
// look at it with. Talks are saved on their own (`/api/muse/video/talks`).
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: { type: Boolean, default: false },
  // { sha, nonce } from a still's 🎬. Read once per nonce.
  request: { type: Object, default: null },
})
const emit = defineEmits(['update:show', 'toast'])
const { t, locale } = useI18n()
const isJa = computed(() => String(locale.value || '').startsWith('ja'))

const sha = ref('')
const source = ref({ session_id: '', character_id: '', partner_character_id: '' })
const characters = ref([])
const leadId = ref('')
const partnerId = ref('')
const choosingPartner = ref(false)
const seconds = ref(5)
const note = ref('')
const catalog = ref(null)
const model = ref('')
const MODEL_KEY = 'ranbell.video.model'
function storedModel() {
  try { return localStorage.getItem(MODEL_KEY) || '' } catch { return '' }
}
function storeModel(m) {
  try { localStorage.setItem(MODEL_KEY, m) } catch { /* private window */ }
}
// Only models that can read the picture: the chosen one looks at the still too.
const models = computed(() => {
  const llm = catalog.value?.llm || {}
  return [...new Set(llm.vision_models?.length ? llm.vision_models : (llm.models || []))]
})
const talk = ref(null)
const shaTalks = ref([])
const recentTalks = ref([])
const showRecent = ref(false)
// Search over every talk: prompts, idea titles and stories, names, the talk.
const query = ref('')
const results = ref([])
const searching = ref(false)
let searchTimer = null
// Which of the talk's prompts is shown (search can land on an older one).
const promptIndex = ref(-1)
const setupOpen = ref(true)
const busy = ref('')
const input = ref('')
const chatEl = ref(null)
const elapsed = ref(0)
let timer = null
let lastNonce = 0

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `${resp.status}`)
  return data
}
function fail(err) {
  emit('toast', { msg: String(err?.message || err), type: 'error' })
}
function thumb(s) { return s ? `/api/thumbnails/${s}.webp` : '' }
function full(s) { return s ? `/api/originals/${s}` : '' }
function nameOf(c) {
  if (!c) return ''
  return (isJa.value ? c.name_ja : c.name) || c.name || c.name_ja || ''
}
function faceOf(c) {
  return thumb(c?.portrait || c?.board?.portrait || c?.board?.sheet || '')
}
const byId = computed(() => Object.fromEntries(characters.value.map(c => [c.id, c])))
const lead = computed(() => byId.value[leadId.value] || null)
const partner = computed(() => byId.value[partnerId.value] || null)
const fromShoot = computed(() => !!source.value.session_id)
const ideas = computed(() => talk.value?.ideas || [])
const phase = computed(() => talk.value?.phase || 'direction')
const board = computed(() => talk.value?.board || [])
// Only the latest choice is live; older ones are what was asked then.
const livePitch = computed(() => {
  const chat = talk.value?.chat || []
  for (let i = chat.length - 1; i >= 0; i--) {
    if (chat[i].role === 'user') return -1
    if (chat[i].kind === 'pitch') return i
  }
  return -1
})
const prompts = computed(() => talk.value?.prompts || [])
const latestPrompt = computed(() => {
  const list = prompts.value
  if (!list.length) return null
  const i = promptIndex.value
  return list[i >= 0 && i < list.length ? i : list.length - 1]
})
const shownPromptIndex = computed(() => {
  const i = promptIndex.value
  return i >= 0 && i < prompts.value.length ? i : prompts.value.length - 1
})
const castLine = computed(() => {
  const c = talk.value
  if (!c) return ''
  return [nameOf(c.character), nameOf(c.partner_character)].filter(Boolean).join(' × ')
})

function startClock() {
  stopClock()
  const t0 = Date.now()
  elapsed.value = 0
  timer = setInterval(() => { elapsed.value = Math.round((Date.now() - t0) / 1000) }, 1000)
}
function stopClock() {
  if (timer) clearInterval(timer)
  timer = null
}
onBeforeUnmount(stopClock)

async function run(kind, fn) {
  if (busy.value) return
  busy.value = kind
  startClock()
  try {
    await fn()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = ''
    stopClock()
  }
}

async function scrollChat() {
  await nextTick()
  if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
}

async function ensureCharacters() {
  if (!catalog.value) catalog.value = await api('/api/muse/catalog')
  if (characters.value.length) return
  const data = await api('/api/characters')
  characters.value = (data.characters || []).filter(c => c.id)
}

// That shoot's model for its own still; else the studio's last pick; else the
// latest Muse shoot's; else the admin screen's default (the Showrunner, 2026-10-11:
// the admin default was a small e4b while the shoots ran on the 26B).
function defaultModel(src) {
  const known = new Set(models.value)
  const pick = [src.session_model, storedModel(), src.last_muse_model,
    catalog.value?.admin_defaults?.muse_model].find(m => m && known.has(m))
  return pick || models.value[0] || ''
}

async function loadRecent() {
  try {
    recentTalks.value = (await api('/api/muse/video/talks?limit=30')).talks || []
  } catch (err) {
    console.error('[video] recent talks failed', err)
  }
}

// A still's 🎬: who is in it, and any earlier talk about it.
async function openStill(newSha) {
  sha.value = newSha
  talk.value = null
  note.value = ''
  setupOpen.value = true
  choosingPartner.value = false
  try {
    await ensureCharacters()
    source.value = await api(`/api/muse/video/source/${newSha}`)
    leadId.value = source.value.character_id || ''
    partnerId.value = source.value.partner_character_id || ''
    model.value = defaultModel(source.value)
    shaTalks.value = (await api(`/api/muse/video/talks?sha256=${newSha}&limit=10`)).talks || []
    // Coming back to a picture already talked over: pick the talk up again.
    if (shaTalks.value.length) await openTalk(shaTalks.value[0].talk_id)
  } catch (err) {
    fail(err)
  }
}

async function openTalk(id, atPrompt = -1) {
  const data = await api(`/api/muse/video/talks/${id}`)
  promptIndex.value = atPrompt
  talk.value = data
  sha.value = data.sha256
  leadId.value = data.character?.character_id || ''
  partnerId.value = data.partner_character?.character_id || ''
  if (data.model) model.value = data.model
  seconds.value = data.duration_s || 5
  setupOpen.value = false
  showRecent.value = false
  await scrollChat()
}

async function startTalk() {
  if (!leadId.value) { fail(new Error(t('video.noActress'))); return }
  if (!model.value) { fail(new Error(t('video.noModel'))); return }
  storeModel(model.value)
  await run('start', async () => {
    const prev = talk.value?.sha256 === sha.value ? talk.value.talk_id : (shaTalks.value[0]?.talk_id || '')
    talk.value = await api('/api/muse/video/talks', {
      method: 'POST',
      body: JSON.stringify({
        sha256: sha.value,
        character_id: leadId.value,
        partner_id: partnerId.value,
        duration_s: seconds.value,
        short: note.value.trim(),
        locale: isJa.value ? 'ja' : 'en',
        model: model.value,
        reuse_talk_id: prev,
      }),
    })
    setupOpen.value = false
    shaTalks.value = [{ talk_id: talk.value.talk_id }, ...shaTalks.value]
    await scrollChat()
  })
}

async function send() {
  const msg = input.value.trim()
  if (!msg || !talk.value) return
  input.value = ''
  promptIndex.value = -1
  // Shown at once; the real row replaces it when she answers.
  talk.value = { ...talk.value, chat: [...(talk.value.chat || []), { role: 'user', text: msg }] }
  await scrollChat()
  await run('reply', async () => {
    talk.value = await api(`/api/muse/video/talks/${talk.value.talk_id}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message: msg, duration_s: seconds.value }),
    })
    await scrollChat()
  })
}

// The talk goes direction → timetable → prompt. Settling is also noticed in the
// talk itself; the button settles it as the talk stands.
async function settleDirection() {
  await run('settle', async () => {
    talk.value = await api(`/api/muse/video/talks/${talk.value.talk_id}/settle`, { method: 'POST' })
    await scrollChat()
  })
}

// How it is shot: settled in the talk, changed here at any time.
const STYLES = ['calm', 'flow', 'cuts']
async function setStyle(style) {
  if (!talk.value || talk.value.style === style) return
  await run('style', async () => {
    talk.value = await api(`/api/muse/video/talks/${talk.value.talk_id}/style`, {
      method: 'POST',
      body: JSON.stringify({ style }),
    })
    await scrollChat()
  })
}

async function backToDirection() {
  await run('back', async () => {
    talk.value = await api(`/api/muse/video/talks/${talk.value.talk_id}/phase`, {
      method: 'POST',
      body: JSON.stringify({ phase: 'direction' }),
    })
    await scrollChat()
  })
}

async function writePrompt() {
  promptIndex.value = -1
  await run('write', async () => {
    talk.value = await api(`/api/muse/video/talks/${talk.value.talk_id}/write`, {
      method: 'POST',
      body: JSON.stringify({ duration_s: seconds.value }),
    })
    await scrollChat()
  })
}

// Her two-way choice: picking one says it in the talk, as the Showrunner.
async function pickOption(option) {
  input.value = isJa.value ? `「${option}」がいいな` : `I'd go with "${option}"`
  await send()
}

async function repitch() {
  await run('repitch', async () => {
    talk.value = await api(`/api/muse/video/talks/${talk.value.talk_id}/repitch`, { method: 'POST' })
    await scrollChat()
  })
}

function pickActress(id) {
  if (choosingPartner.value) {
    partnerId.value = id === leadId.value ? '' : id
    choosingPartner.value = false
    return
  }
  leadId.value = id
  if (partnerId.value === id) partnerId.value = ''
}

function copyText(text) {
  // `navigator.clipboard` exists only on https or localhost; the app is
  // usually opened over plain http on the LAN.
  if (navigator.clipboard) return navigator.clipboard.writeText(text)
  const el = document.createElement('textarea')
  el.value = text
  el.style.cssText = 'position:fixed;opacity:0'
  document.body.appendChild(el)
  el.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(el)
  if (!ok) throw new Error(t('video.copyFailed'))
  return Promise.resolve()
}
// The prompt goes to ComfyUI or Hailuo next, outside Ranbell: free the card
// now, or a resident 26B leaves ComfyUI too little (it ran out of memory,
// 2026-10-11). Best effort — the screen never waits on it or fails for it.
function releaseCard() {
  fetch('/api/muse/video/release', { method: 'POST' }).catch(() => {})
}

async function copyPrompt(text) {
  try {
    await copyText(String(text || '').trim())
    emit('toast', { msg: t('video.copied'), type: 'info' })
    releaseCard()
  } catch (err) {
    fail(err)
  }
}

function onQuery() {
  if (searchTimer) clearTimeout(searchTimer)
  const q = query.value.trim()
  if (!q) { results.value = []; searching.value = false; return }
  searching.value = true
  searchTimer = setTimeout(async () => {
    try {
      const data = await api(`/api/muse/video/search?q=${encodeURIComponent(q)}&limit=30`)
      if (query.value.trim() === q) results.value = data.talks || []
    } catch (err) {
      fail(err)
    } finally {
      if (query.value.trim() === q) searching.value = false
    }
  }, 300)
}
const FIELD_ICON = { prompt: '📝', title: '🎬', story: '🎬', name: '🌸', note: '✏️', chat: '💬' }
function openHit(row) {
  const hit = (row.hits || []).find(h => h.prompt_index != null)
  openTalk(row.talk_id, hit ? hit.prompt_index : -1)
}

function toggleRecent() {
  showRecent.value = !showRecent.value
  if (showRecent.value) loadRecent()
}
function close() {
  if (talk.value) releaseCard()
  emit('update:show', false)
}
function onKey(e) { if (e.key === 'Escape' && !busy.value) close() }

watch(() => props.show, (open) => {
  if (open) {
    window.addEventListener('keydown', onKey)
    loadRecent()
  } else {
    window.removeEventListener('keydown', onKey)
  }
})
watch(() => props.request?.nonce, async () => {
  const req = props.request
  if (!req?.sha || req.nonce === lastNonce) return
  lastNonce = req.nonce
  await openStill(req.sha)
}, { immediate: true })
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

function clock(sec) {
  const m = Math.floor(sec / 60)
  return `${m}:${String(sec % 60).padStart(2, '0')}`
}
function when(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString(isJa.value ? 'ja-JP' : 'en-US', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}
function rowFace(row) {
  const id = row.character_id
  if (!id) return ''
  const c = talk.value?.character?.character_id === id ? talk.value.character
    : talk.value?.partner_character?.character_id === id ? talk.value.partner_character
      : byId.value[id]
  return faceOf(c)
}
const waitingLabel = computed(() => ({
  start: t('video.thinking'),
  reply: t('video.replying', { name: castLine.value }),
  write: t('video.writing'),
  repitch: t('video.thinking'),
  settle: t('video.settling'),
  back: '',
  style: '',
}[busy.value] || ''))
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show"
      class="fixed inset-0 z-[var(--z-panel-muse-child)] flex items-stretch justify-center bg-black/85 p-2 sm:p-4"
      @click.self="!busy && close()"
    >
      <div class="flex w-full max-w-7xl flex-col overflow-hidden rounded-2xl border border-amber-500/30 bg-gray-950 shadow-2xl">
        <!-- Header -->
        <div class="flex items-center gap-3 border-b border-white/10 px-4 py-2.5">
          <h2 class="text-sm font-medium text-amber-100">🎬 {{ t('video.title') }}</h2>
          <span v-if="castLine" class="truncate text-xs text-gray-400">{{ castLine }}</span>
          <div class="relative ml-auto">
            <button
              type="button"
              class="rounded-lg border border-white/10 px-2.5 py-1 text-xs text-gray-300 hover:bg-white/5"
              @click="toggleRecent"
            >🔍 {{ t('video.recent') }}</button>
            <div
              v-if="showRecent"
              class="absolute right-0 z-10 mt-1 max-h-[60vh] w-72 overflow-y-auto rounded-xl border border-white/10 bg-gray-900 p-1.5 shadow-xl"
            >
              <input
                v-model="query"
                type="search"
                class="mb-1.5 w-full rounded-lg border border-white/10 bg-black/40 px-2.5 py-1.5 text-xs text-gray-100 outline-none focus:border-amber-500/60"
                :placeholder="t('video.searchPlaceholder')"
                @input="onQuery"
              />
              <template v-if="query.trim()">
                <p v-if="searching" class="px-2 py-2 text-xs text-gray-500">{{ t('video.searching') }}</p>
                <p v-else-if="!results.length" class="px-2 py-2 text-xs text-gray-500">{{ t('video.noHits') }}</p>
                <button
                  v-for="row in results"
                  :key="`hit-${row.talk_id}`"
                  type="button"
                  class="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-white/5"
                  @click="openHit(row)"
                >
                  <img :src="thumb(row.sha256)" class="mt-0.5 h-10 w-10 shrink-0 rounded object-cover" alt="" />
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-xs text-gray-200">
                      {{ [nameOf(row.character), nameOf(row.partner_character)].filter(Boolean).join(' × ') }}
                      <span class="text-[10px] text-gray-500"> · {{ when(row.updated_at) }}</span>
                    </span>
                    <span
                      v-for="(hit, hi) in (row.hits || []).slice(0, 2)"
                      :key="hi"
                      class="block text-[10px] leading-snug text-gray-400"
                    >{{ FIELD_ICON[hit.field] || '·' }} <template v-if="hit.title && hit.field !== 'title'">「{{ hit.title }}」</template>{{ hit.snippet }}</span>
                  </span>
                </button>
              </template>
              <template v-else>
              <p v-if="!recentTalks.length" class="px-2 py-3 text-xs text-gray-500">{{ t('video.noRecent') }}</p>
              <button
                v-for="row in recentTalks"
                :key="row.talk_id"
                type="button"
                class="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-white/5"
                @click="openTalk(row.talk_id)"
              >
                <img :src="thumb(row.sha256)" class="h-10 w-10 shrink-0 rounded object-cover" alt="" />
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-xs text-gray-200">
                    {{ [nameOf(row.character), nameOf(row.partner_character)].filter(Boolean).join(' × ') }}
                  </span>
                  <span class="block truncate text-[10px] text-gray-500">
                    {{ row.chosen_title || '—' }}<template v-if="row.prompts_count"> · 📝{{ row.prompts_count }}</template> · {{ when(row.updated_at) }}
                  </span>
                </span>
              </button>
              </template>
            </div>
          </div>
          <button
            type="button"
            class="text-lg leading-none text-gray-500 hover:text-gray-200 disabled:opacity-40"
            :disabled="!!busy"
            @click="close"
          >✕</button>
        </div>

        <div v-if="!sha" class="flex flex-1 items-center justify-center p-8 text-sm text-gray-500">
          {{ t('video.empty') }}
        </div>

        <div v-else class="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <!-- Left: the still, then the setup or the ideas and the prompt -->
          <div class="min-h-0 space-y-3 overflow-y-auto border-white/10 p-4 lg:border-r">
            <img
              :src="full(sha)"
              class="mx-auto block max-h-[42vh] w-auto rounded-lg object-contain"
              alt=""
            />
            <p v-if="fromShoot" class="text-center text-[11px] text-pink-300/80">
              {{ t('video.fromShoot') }}
            </p>

            <!-- Setup: who, how long, a note -->
            <div v-if="setupOpen || !talk" class="space-y-3 rounded-xl border border-white/10 bg-gray-900/60 p-3">
              <div class="flex items-center gap-2 text-xs text-gray-300">
                <span>{{ choosingPartner ? t('video.pickPartner') : t('video.pickActress') }}</span>
                <span v-if="lead" class="flex items-center gap-1 rounded-full bg-pink-900/40 px-2 py-0.5 text-pink-100">
                  <img :src="faceOf(lead)" class="h-5 w-5 rounded-full object-cover" alt="" />{{ nameOf(lead) }}
                </span>
                <span v-if="partner" class="flex items-center gap-1 rounded-full bg-violet-900/40 px-2 py-0.5 text-violet-100">
                  <img :src="faceOf(partner)" class="h-5 w-5 rounded-full object-cover" alt="" />{{ nameOf(partner) }}
                  <button type="button" class="ml-0.5 text-violet-300 hover:text-white" @click="partnerId = ''">✕</button>
                </span>
                <button
                  v-if="lead && !partner"
                  type="button"
                  class="rounded-full border border-dashed px-2 py-0.5"
                  :class="choosingPartner ? 'border-violet-400 text-violet-100' : 'border-white/20 text-gray-400 hover:text-gray-200'"
                  @click="choosingPartner = !choosingPartner"
                >＋ {{ t('video.addPartner') }}</button>
              </div>
              <div class="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                <button
                  v-for="c in characters"
                  :key="c.id"
                  type="button"
                  class="flex w-14 flex-col items-center gap-0.5 rounded-lg p-1 hover:bg-white/5"
                  :class="c.id === leadId ? 'bg-pink-900/40 ring-1 ring-pink-400'
                    : c.id === partnerId ? 'bg-violet-900/40 ring-1 ring-violet-400' : ''"
                  :title="nameOf(c)"
                  @click="pickActress(c.id)"
                >
                  <img :src="faceOf(c)" class="h-10 w-10 rounded-full object-cover" alt="" />
                  <span class="w-full truncate text-center text-[9px] text-gray-400">{{ nameOf(c) }}</span>
                </button>
              </div>
              <div class="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                <span>{{ t('video.length') }}</span>
                <button
                  v-for="sec in [5, 10, 15]"
                  :key="sec"
                  type="button"
                  class="rounded border px-2 py-0.5"
                  :class="seconds === sec ? 'border-amber-400/70 text-amber-50' : 'border-white/10'"
                  @click="seconds = sec"
                >{{ sec }}s</button>
              </div>
              <label class="flex items-center gap-2 text-xs text-gray-400">
                <span class="shrink-0">{{ t('video.model') }}</span>
                <select
                  v-model="model"
                  class="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/40 px-2 py-1 text-xs text-gray-100 outline-none focus:border-amber-500/60"
                >
                  <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
                </select>
              </label>
              <input
                v-model="note"
                type="text"
                class="w-full rounded-lg border border-white/10 bg-black/40 px-3 py-1.5 text-xs text-gray-100 outline-none focus:border-amber-500/60"
                :placeholder="t('video.notePlaceholder')"
              />
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="rounded-lg border border-amber-500/60 bg-amber-900/40 px-3 py-1.5 text-xs font-medium text-amber-50 hover:bg-amber-800/60 disabled:opacity-40"
                  :disabled="!!busy || !leadId"
                  @click="startTalk"
                >{{ talk ? t('video.restart') : t('video.start') }}</button>
                <button
                  v-if="talk"
                  type="button"
                  class="text-xs text-gray-500 hover:text-gray-300"
                  @click="setupOpen = false"
                >{{ t('video.cancel') }}</button>
              </div>
            </div>

            <!-- During a talk: the cast, the ideas, the prompt -->
            <template v-if="talk && !setupOpen">
              <div class="flex flex-wrap items-center gap-2 text-xs">
                <span class="flex items-center gap-1 rounded-full bg-pink-900/40 px-2 py-0.5 text-pink-100">
                  <img :src="faceOf(talk.character)" class="h-5 w-5 rounded-full object-cover" alt="" />{{ nameOf(talk.character) }}
                </span>
                <span
                  v-if="talk.partner_character?.character_id"
                  class="flex items-center gap-1 rounded-full bg-violet-900/40 px-2 py-0.5 text-violet-100"
                >
                  <img :src="faceOf(talk.partner_character)" class="h-5 w-5 rounded-full object-cover" alt="" />{{ nameOf(talk.partner_character) }}
                </span>
                <span class="text-gray-500">{{ t('video.length') }}</span>
                <button
                  v-for="sec in [5, 10, 15]"
                  :key="sec"
                  type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px]"
                  :class="seconds === sec ? 'border-amber-400/70 text-amber-50' : 'border-white/10 text-gray-400'"
                  @click="seconds = sec"
                >{{ sec }}s</button>
                <span v-if="talk.model" class="max-w-[12rem] truncate text-[10px] text-gray-500" :title="talk.model">· {{ talk.model }}</span>
                <button
                  type="button"
                  class="ml-auto text-[11px] text-gray-400 underline hover:text-gray-200"
                  @click="setupOpen = true"
                >{{ t('video.changeCast') }}</button>
              </div>

              <!-- Where the talk is: direction, then the timetable, then the prompt -->
              <ol class="flex items-center gap-1.5 text-[10px]">
                <li
                  v-for="(step, si) in ['direction', 'timeline', 'prompt']"
                  :key="step"
                  class="flex items-center gap-1.5"
                >
                  <span v-if="si" class="text-gray-600">→</span>
                  <span
                    class="rounded-full border px-2 py-0.5"
                    :class="(step === 'prompt' ? prompts.length && phase === 'timeline' : phase === step)
                      ? 'border-amber-400/70 bg-amber-950/40 text-amber-50'
                      : 'border-white/10 text-gray-500'"
                  >{{ si + 1 }} {{ t(`video.step.${step}`) }}</span>
                </li>
              </ol>

              <!-- How it is shot -->
              <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
                <span class="text-gray-400">🎥 {{ t('video.style.label') }}</span>
                <button
                  v-for="st in STYLES"
                  :key="st"
                  type="button"
                  class="rounded-full border px-2 py-0.5 disabled:opacity-40"
                  :class="talk.style === st ? 'border-sky-400/70 bg-sky-950/40 text-sky-50' : 'border-white/10 text-gray-400 hover:text-gray-200'"
                  :title="t(`video.style.${st}Hint`)"
                  :disabled="!!busy"
                  @click="setStyle(st)"
                >{{ t(`video.style.${st}`) }}</button>
              </div>

              <!-- 1. The direction: talked over, with the writers' notes as material -->
              <template v-if="phase === 'direction'">
                <div class="space-y-2 rounded-xl border border-white/10 bg-gray-900/60 p-2.5">
                  <p class="text-[11px] leading-relaxed text-gray-300">{{ t('video.directionHint') }}</p>
                  <button
                    type="button"
                    class="rounded-full border border-amber-500/50 bg-amber-950/40 px-3 py-1 text-[11px] text-amber-100 hover:bg-amber-900/60 disabled:opacity-40"
                    :disabled="!!busy || (!ideas.length && !talk.short)"
                    @click="settleDirection"
                  >🎯 {{ t('video.settle') }}</button>
                </div>

                <div class="flex items-center justify-between">
                  <h3 class="text-xs font-medium text-amber-100">{{ t('video.ideas') }}</h3>
                  <button
                    type="button"
                    class="text-[11px] text-gray-400 hover:text-gray-200 disabled:opacity-40"
                    :disabled="!!busy"
                    @click="repitch"
                  >↻ {{ ideas.length ? t('video.repitch') : t('video.pitchAnyway') }}</button>
                </div>
                <!-- No ideas until the director says what they want (or leaves it to her). -->
                <p v-if="!ideas.length" class="rounded-lg border border-dashed border-white/10 px-3 py-2 text-[11px] text-gray-400">
                  {{ t('video.waitingForWish') }}
                </p>
                <p v-else class="text-[10px] text-gray-500">{{ t('video.ideasAreMaterial') }}</p>
                <div class="grid gap-2 sm:grid-cols-2">
                  <div
                    v-for="idea in ideas"
                    :key="idea.kind"
                    class="flex flex-col gap-1 rounded-xl border border-violet-500/30 bg-violet-950/20 p-2.5"
                  >
                    <span class="self-start rounded bg-white/5 px-1.5 text-[9px] text-gray-400">{{ idea.label }}</span>
                    <div class="text-[12px] font-medium text-violet-50">{{ idea.title || '' }}</div>
                    <p v-if="idea.error" class="text-[10px] text-amber-300/90">{{ idea.error }}</p>
                    <p v-else class="flex-1 whitespace-pre-wrap text-[11px] leading-relaxed text-gray-300">{{ idea.story }}</p>
                  </div>
                </div>
              </template>

              <!-- 2. The timetable: what the two of them are working through -->
              <template v-else>
                <div class="space-y-1 rounded-xl border border-amber-500/30 bg-amber-950/20 p-2.5">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-[11px] font-medium text-amber-100">🎯 {{ t('video.direction') }}</span>
                    <button
                      type="button"
                      class="text-[10px] text-gray-400 underline hover:text-gray-200 disabled:opacity-40"
                      :disabled="!!busy"
                      @click="backToDirection"
                    >{{ t('video.backToDirection') }}</button>
                  </div>
                  <p class="whitespace-pre-wrap text-[11px] leading-relaxed text-amber-50/90">{{ talk.direction }}</p>
                </div>

                <div class="space-y-1.5">
                  <div class="flex items-center justify-between">
                    <h3 class="text-xs font-medium text-amber-100">🗒 {{ t('video.timetable') }}</h3>
                    <span class="text-[10px] text-gray-500">{{ t('video.timetableHint') }}</span>
                  </div>
                  <div
                    v-for="(seg, gi) in board"
                    :key="gi"
                    class="rounded-lg border border-white/10 bg-black/30 px-2.5 py-1.5"
                  >
                    <div class="flex items-baseline gap-2 text-[11px]">
                      <span class="shrink-0 font-mono text-amber-300/90">{{ clock(seg.start) }}–{{ clock(seg.end) }}</span>
                      <span class="truncate font-medium text-gray-100">{{ seg.title }}</span>
                    </div>
                    <p class="text-[11px] leading-relaxed text-gray-300">{{ seg.action }}</p>
                    <p v-if="seg.camera" class="mt-0.5 text-[10px] leading-relaxed text-sky-200/80">🎥 {{ seg.camera }}</p>
                  </div>
                  <!-- What the director said no to: kept for the whole clip -->
                  <p v-if="talk.avoid" class="rounded-lg border border-rose-500/30 bg-rose-950/20 px-2.5 py-1 text-[11px] text-rose-100/90">
                    🚫 {{ t('video.avoid') }}: {{ talk.avoid }}
                  </p>
                  <p v-if="board.length && board[board.length - 1].end !== seconds" class="text-[10px] text-amber-300/80">
                    {{ t('video.lengthChanged', { n: seconds }) }}
                  </p>
                </div>

                <button
                  type="button"
                  class="self-start rounded-full border border-amber-500/60 bg-amber-900/40 px-3 py-1 text-[11px] font-medium text-amber-50 hover:bg-amber-800/60 disabled:opacity-40"
                  :disabled="!!busy || !board.length"
                  @click="writePrompt"
                >📝 {{ busy === 'write' ? t('video.writing') : t('video.write') }}</button>
              </template>

              <div v-if="latestPrompt" class="space-y-1.5 rounded-xl border border-amber-500/40 bg-black/40 p-2.5">
                <div class="flex items-center gap-2 text-[11px] text-amber-100">
                  <span>📝 {{ t('video.prompt') }}</span>
                  <span v-if="prompts.length > 1" class="flex shrink-0 gap-0.5">
                    <button
                      v-for="(p, pi) in prompts"
                      :key="pi"
                      type="button"
                      class="rounded border px-1.5 text-[10px]"
                      :class="pi === shownPromptIndex ? 'border-amber-400/70 text-amber-50' : 'border-white/10 text-gray-500 hover:text-gray-300'"
                      :title="p.title"
                      @click="promptIndex = pi"
                    >{{ pi + 1 }}</button>
                  </span>
                  <span class="truncate text-gray-400">{{ latestPrompt.title }}<template v-if="latestPrompt.instrument"> · ♪ {{ latestPrompt.instrument }}</template> · {{ latestPrompt.duration_s }}s</span>
                </div>
                <textarea
                  :value="latestPrompt.prompt"
                  readonly
                  rows="8"
                  class="w-full rounded border border-white/10 bg-black/40 px-2 py-1 font-mono text-[10px] leading-relaxed text-gray-100"
                />
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="rounded border border-amber-500/50 px-2.5 py-0.5 text-[11px] text-amber-100 hover:bg-amber-900/40"
                    @click="copyPrompt(latestPrompt.prompt)"
                  >{{ t('video.copy') }}</button>
                  <span class="text-[10px] text-gray-500">{{ t('video.pasteHint') }}</span>
                </div>
              </div>
            </template>
          </div>

          <!-- Right: the conversation -->
          <div class="flex min-h-0 flex-col">
            <div ref="chatEl" class="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-4">
              <p v-if="!talk && !busy" class="pt-8 text-center text-xs text-gray-500">{{ t('video.beforeTalk') }}</p>
              <template v-for="(row, i) in talk?.chat || []" :key="i">
                <div v-if="row.role === 'system'" class="whitespace-pre-wrap text-center text-[10px] text-gray-500">{{ row.text }}</div>
                <!-- What she mutters to herself -->
                <div v-else-if="row.kind === 'aside'" class="pl-8 text-[11px] italic text-pink-200/50">（{{ row.text }}）</div>
                <!-- Her two-way choice: a click says it as the Showrunner -->
                <div v-else-if="row.kind === 'pitch'" class="flex flex-wrap gap-1.5 pl-8">
                  <button
                    v-for="opt in row.options || []"
                    :key="opt"
                    type="button"
                    class="rounded-full border px-2.5 py-1 text-[11px] disabled:cursor-default"
                    :class="i === livePitch
                      ? 'border-pink-400/50 bg-pink-950/40 text-pink-50 hover:bg-pink-900/60'
                      : 'border-white/10 text-gray-500'"
                    :disabled="i !== livePitch || !!busy"
                    @click="pickOption(opt)"
                  >{{ opt }}</button>
                </div>
                <div v-else-if="row.role === 'user'" class="flex flex-col items-end gap-0.5">
                  <span class="px-0.5 text-[10px] text-emerald-300/80">🎬 {{ t('video.director') }}</span>
                  <div class="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm border border-emerald-500/40 bg-emerald-950/50 px-3.5 py-2 text-[12px] text-emerald-100">{{ row.text }}</div>
                </div>
                <div v-else class="flex flex-col items-start gap-0.5">
                  <span class="flex items-center gap-1.5 px-0.5 text-[10px] text-pink-300/80">
                    <img v-if="rowFace(row)" :src="rowFace(row)" class="h-6 w-6 rounded-full object-cover ring-1 ring-pink-400/60" alt="" />
                    🌸 {{ row.name }}
                  </span>
                  <div class="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tl-sm border border-pink-500/30 bg-slate-900/80 px-3.5 py-2 text-[12px] leading-relaxed text-pink-50">{{ row.text }}</div>
                </div>
              </template>
              <div v-if="busy" class="flex items-center gap-2 text-[11px] text-pink-300/80">
                <span>{{ waitingLabel }}</span>
                <span class="font-mono text-[10px] text-pink-400/60">{{ clock(elapsed) }}</span>
              </div>
            </div>
            <!-- Enter is a plain newline; sending is the button (kana conversion confirms with Enter). -->
            <div class="flex items-end gap-2 border-t border-white/10 p-3">
              <textarea
                v-model="input"
                rows="2"
                class="min-w-0 flex-1 resize-y rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm leading-relaxed text-gray-100 outline-none focus:border-amber-500"
                :placeholder="talk ? t('video.chatPlaceholder') : t('video.chatBeforeTalk')"
                :disabled="!talk || !!busy"
              ></textarea>
              <button
                type="button"
                class="rounded-lg border border-amber-500/60 bg-amber-900/40 px-3 py-2 text-xs font-medium text-amber-50 hover:bg-amber-800/60 disabled:opacity-40"
                :disabled="!talk || !!busy || !input.trim()"
                @click="send"
              >{{ t('video.send') }}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

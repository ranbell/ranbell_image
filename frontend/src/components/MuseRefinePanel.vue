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
const job = ref(null)
const lightboxSrc = ref('')
const showDiary = ref(false)
const themeDraft = ref('')
const streamLive = ref(false)
const speaking = ref(false)
let es = null
let pollTimer = null
let refreshTimer = null
let refreshQueued = false
let startedAt = 0
const elapsed = ref(0)

const isJa = computed(() => String(locale.value).startsWith('ja'))
const inputs = computed(() => session.value?.inputs || {})
const ledger = computed(() => session.value?.refine_ledger || {})
const craft = computed(() => session.value?.craft || {})
const chat = computed(() => session.value?.chat || [])
const refineLog = computed(() => [...(session.value?.refine_log || [])].slice().reverse())
const stageMs = computed(() => [...(session.value?.stage_ms || [])].slice(-12).reverse())
const turnTrace = computed(() => [...(session.value?.turn_trace || [])].slice().reverse())
const rewriteLog = computed(() => [...(session.value?.rewrite_log || [])].slice().reverse())
const pipeline = computed(() => session.value?.pipeline || null)
const pipelineStages = computed(() => pipeline.value?.stages || [])
const pipelineDivergences = computed(() => pipeline.value?.divergences || [])
const visibleConsequences = computed(() => {
  const fromCraft = craft.value?.visible_consequences
  if (fromCraft && (fromCraft.tags?.length || fromCraft.causes?.length || fromCraft.hints?.length)) {
    return fromCraft
  }
  return pipeline.value?.visible_consequences || null
})
const hasVisibleConsequences = computed(() => {
  const v = visibleConsequences.value
  if (!v) return false
  return Boolean(
    (v.causes && v.causes.length)
    || (v.tags && v.tags.length)
    || (v.hints && v.hints.length),
  )
})
function pipelineStatusClass(status) {
  if (status === 'ok' || status === 'frozen') return 'border-emerald-500/40 text-emerald-200/90'
  if (status === 'pending') return 'border-amber-500/40 text-amber-200/90'
  if (status === 'missed' || status === 'stale' || status === 'refused' || status === 'diverged') {
    return 'border-rose-500/40 text-rose-200/90'
  }
  return 'border-amber-500/20 text-amber-100/60'
}
function rewriteWhen(ts) {
  if (!ts) return ''
  try { return new Date(Number(ts) * 1000).toLocaleTimeString() } catch { return '' }
}
function mergeRewriteLog(keep, next) {
  const byAt = new Map()
  for (const row of [...(keep || []), ...(next || [])]) {
    if (!row || typeof row !== 'object') continue
    const cleaned = {
      at: row.at,
      source: row.source || '',
      intent: row.intent || '',
      changed: row.changed || {},
    }
    const key = `${cleaned.at}|${cleaned.source}|${cleaned.intent}|${JSON.stringify(cleaned.changed)}`
    byAt.set(key, cleaned)
  }
  return [...byAt.values()].sort((a, b) => Number(a?.at || 0) - Number(b?.at || 0)).slice(-24)
}
const characters = computed(() => characterList.value)
const workflows = computed(() => {
  const list = catalog.value?.comfyui?.workflows || catalog.value?.workflows || []
  return Array.isArray(list) ? list : []
})
const models = computed(() => catalog.value?.llm?.models || [])
const boardImages = computed(() => session.value?.board?.images || [])
const shootImages = computed(() => session.value?.shoot?.images || [])
const boardReady = computed(() => !!session.value?.board?.ready)
const boardError = computed(() => String(session.value?.board?.error || '').trim())
const shootError = computed(() => String(session.value?.shoot?.error || '').trim())
const sessionStatus = computed(() => String(session.value?.status || ''))
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

function thumb(sha) {
  return sha ? `/api/thumbnails/${sha}.webp` : ''
}
function full(sha) {
  return sha ? `/api/originals/${sha}` : ''
}
function openLightbox(src) {
  const url = String(src || '').trim()
  if (!url) return
  lightboxSrc.value = url
  if (typeof window !== 'undefined') {
    window.addEventListener('keydown', onLightboxKey)
  }
}
function openLightboxSha(sha) {
  openLightbox(full(sha))
}
function closeLightbox() {
  lightboxSrc.value = ''
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', onLightboxKey)
  }
}
function onLightboxKey(e) {
  if (e.key !== 'Escape' || !lightboxSrc.value) return
  closeLightbox()
  e.preventDefault()
  e.stopPropagation()
}

const leadCharacter = computed(() => session.value?.character || {})
const leadFaceSha = computed(() =>
  leadCharacter.value?.board?.portrait || leadCharacter.value?.board?.sheet || '',
)
const partnerFaceSha = computed(() =>
  partner.value?.board?.portrait || partner.value?.board?.sheet || '',
)
const leadFace = computed(() => thumb(leadFaceSha.value))
const partnerFace = computed(() => thumb(partnerFaceSha.value))
const waitName = computed(() =>
  leadCharacter.value?.name_ja || leadCharacter.value?.name || 'Muse',
)
const boardPending = computed(() => !!session.value?.board?.pending)
const shootPending = computed(() => !!session.value?.shoot?.pending)
const renderLocked = computed(() => boardPending.value || shootPending.value)
const chatLocked = computed(() => busy.value || renderLocked.value || speaking.value)
const waitingOnModel = computed(() => busy.value || speaking.value)
const statusLabel = computed(() => {
  if (waitingOnModel.value) return t('museRefine.status.chatting')
  const st = sessionStatus.value
  if (st === 'boarding' || boardPending.value) return t('museRefine.status.boarding')
  if (st === 'awaiting_ok') return t('museRefine.status.awaitingOk')
  if (st === 'shooting' || shootPending.value) return t('museRefine.status.shooting')
  if (st === 'done') return t('museRefine.status.done')
  if (st === 'finished') return t('museRefine.status.finished')
  if (opened.value) return t('museRefine.status.chat')
  return t('museRefine.status.idle')
})
function clock(sec) {
  const s = Math.max(0, Number(sec) || 0)
  const m = Math.floor(s / 60)
  const r = s % 60
  return m ? `${m}:${String(r).padStart(2, '0')}` : `${r}s`
}
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
  closeLightbox()
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
  preview.value = ''
  job.value = null
  elapsed.value = 0
  startedAt = 0
  speaking.value = false
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
  speaking.value = true
  if (!startedAt) startedAt = Date.now()
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
    speaking.value = false
    startedAt = 0
    elapsed.value = 0
  }
}

async function restoreBanned(tag) {
  if (!session.value?.session_id || chatLocked.value) return
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
  if (!session.value?.session_id || chatLocked.value) return
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

function faceShaForRow(row) {
  const id = row?.meta?.speaker_id
  if (id && id === partner.value?.character_id) return partnerFaceSha.value
  if (row?.role === 'assistant' || row?.meta?.kind === 'say' || row?.meta?.kind === 'banter') {
    if (id && id === leadCharacter.value?.character_id) return leadFaceSha.value
    if (row?.meta?.speaker === 'B') return partnerFaceSha.value
    return leadFaceSha.value
  }
  return ''
}
function faceForRow(row) {
  return thumb(faceShaForRow(row))
}
function isSayRow(row) {
  const kind = row?.meta?.kind
  return row?.role === 'assistant' && (!kind || kind === 'say')
}

async function sendChat() {
  const msg = chatInput.value.trim()
  if (!msg || !session.value?.session_id || chatLocked.value) return
  chatInput.value = ''
  busy.value = true
  speaking.value = true
  if (!startedAt) startedAt = Date.now()
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
    speaking.value = false
    startedAt = 0
    elapsed.value = 0
  }
}

async function rebuild() {
  if (!session.value?.session_id || chatLocked.value) return
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
  if (!session.value?.session_id || busy.value || renderLocked.value) return
  if (path === 'board' || path === 'approve') {
    if (props.comfyOffline) return
  }
  busy.value = true
  if (path === 'board' || path === 'approve') {
    preview.value = ''
    job.value = null
    if (!startedAt) startedAt = Date.now()
  }
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/${path}`,
      { method: 'POST' },
    )
    sampleJob()
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

function scheduleRefresh(scroll = false) {
  // Coalesce bursty SSE (preview + session_updated + chat) into one GET.
  // Never pull while a local mutation owns the session — mid-turn chat events
  // publish before the final save and would overwrite the POST result.
  if (busy.value) return
  refreshQueued = true
  if (refreshTimer) return
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (!refreshQueued) return
    refreshQueued = false
    if (busy.value) return
    const prevUpdated = Number(session.value?.updated_at || 0)
    await refresh({ minUpdatedAt: prevUpdated })
    if (scroll) await scrollChat()
  }, 350)
}

function openStream(id) {
  closeStream()
  if (!id) return
  es = new EventSource(
    `/api/muse-refine/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`,
  )
  es.onopen = () => { streamLive.value = true }
  es.onmessage = (ev) => {
    let data = null
    try { data = JSON.parse(ev.data) } catch { return }
    if (!data?.type || data.type === 'hello' || data.type === 'ping') return
    if (data.type === 'preview' && data.image) {
      preview.value = `data:image/jpeg;base64,${data.image}`
      if (!startedAt) startedAt = Date.now()
      sampleJob()
      return
    }
    if (data.type === 'muse_speaking') {
      speaking.value = true
      if (!startedAt) startedAt = Date.now()
      return
    }
    if (data.type === 'notebook_rewrite' || data.type === 'ledger_rewrite') {
      if (!session.value) return
      const log = mergeRewriteLog(session.value.rewrite_log || [], [data])
      session.value = { ...session.value, rewrite_log: log }
      return
    }
    if (data.type === 'chat' || data.type === 'chat_message') {
      // Local sendChat owns speaking/busy until POST returns.
      if (!busy.value) speaking.value = false
      scheduleRefresh(true)
      return
    }
    if (data.type === 'diary_status') {
      scheduleRefresh()
      if (data.status === 'ok') {
        emit('toast', { msg: t('museRefine.diaryReady'), type: 'info' })
      } else if (data.status === 'failed') {
        emit('toast', { msg: t('museRefine.diaryFailed'), type: 'error' })
      }
      return
    }
    if (
      data.type === 'session_updated'
      || data.type === 'board_ready'
      || data.type === 'board_attached'
      || data.type === 'shoot_attached'
      || data.type === 'craft_updated'
    ) {
      if (data.type === 'board_ready' || data.type === 'shoot_attached') {
        preview.value = ''
      }
      if (!busy.value) speaking.value = false
      scheduleRefresh(true)
      return
    }
    scheduleRefresh()
  }
  es.onerror = () => { streamLive.value = false }
  startPoll()
}
function closeStream() {
  if (es) {
    es.close()
    es = null
  }
  streamLive.value = false
  speaking.value = false
  stopPoll()
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
  refreshQueued = false
}
async function refresh(opts = {}) {
  if (!session.value?.session_id) return
  if (busy.value && opts.allowBusy !== true) return
  try {
    const keep = session.value.rewrite_log || []
    const prevUpdated = Number(session.value?.updated_at || 0)
    const minUpdatedAt = Number(opts.minUpdatedAt || 0)
    const next = await api(`/api/muse-refine/sessions/${session.value.session_id}`)
    // Drop stale GETs that raced a newer local POST / SSE save.
    const nextUpdated = Number(next?.updated_at || 0)
    if (minUpdatedAt && nextUpdated && nextUpdated < minUpdatedAt) return
    if (nextUpdated && prevUpdated && nextUpdated < prevUpdated && !opts.allowBusy) return
    next.rewrite_log = mergeRewriteLog(keep, next.rewrite_log)
    session.value = next
    sampleJob()
  } catch { /* ignore */ }
}
function sampleJob() {
  const map = props.getJobsMap?.()
  if (!map?.get) { job.value = null; return }
  const id = session.value?.shoot?.job_id || session.value?.board?.job_id
  job.value = id ? (map.get(id) || null) : null
}
function startPoll() {
  if (pollTimer) return
  let tick = 0
  pollTimer = setInterval(async () => {
    if (typeof document !== 'undefined' && document.hidden) return
    sampleJob()
    const rendering = boardPending.value || shootPending.value
    const inferring = busy.value || speaking.value
    // Match Muse: when idle, do not poll — even if SSE dropped.
    if (!rendering && !inferring) {
      elapsed.value = 0
      tick = 0
      startedAt = 0
      return
    }
    if (!startedAt) startedAt = Date.now()
    elapsed.value = Math.round((Date.now() - startedAt) / 1000)
    // While rendering, refresh every ~3s (or ~6s if SSE dropped).
    const every = streamLive.value ? 3 : 6
    tick += 1
    if (rendering && tick % every === 0) await refresh({ allowBusy: true })
  }, 1000)
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
    return
  }
  try {
    await ensureCatalog()
    if (!session.value) await startFresh()
    else openStream(session.value.session_id)
  } catch (err) {
    fail(err)
  }
})

onBeforeUnmount(() => {
  closeLightbox()
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
        class="flex h-full w-full max-w-5xl flex-col border-l border-pink-500/30 bg-slate-900/95 text-gray-100 shadow-2xl"
      >
        <header class="flex items-center gap-2 border-b border-pink-500/20 bg-pink-950/20 px-4 py-3">
          <div class="flex shrink-0 items-center -space-x-2">
            <img
              v-if="leadFace"
              :src="leadFace"
              alt=""
              class="h-9 w-9 rounded-full object-cover ring-2 ring-pink-400/80 border border-pink-100 shadow-md"
            />
            <img
              v-if="partnerFace"
              :src="partnerFace"
              alt=""
              class="h-9 w-9 rounded-full object-cover ring-2 ring-purple-400/80 border border-pink-100 shadow-md"
            />
            <span
              v-else-if="!leadFace"
              class="grid h-9 w-9 place-items-center rounded-full bg-pink-950/50 text-pink-300 ring-1 ring-pink-500/40"
            >🌸</span>
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-base font-semibold tracking-wide text-pink-200">
              {{ t('museRefine.title') }}
            </h2>
            <p class="truncate text-[11px] text-gray-500">{{ t('museRefine.subtitle') }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span
              class="font-mono text-[10px]"
              :class="streamLive ? 'text-pink-400/70' : 'text-gray-500'"
              :title="t('museRefine.streamHint')"
            >SSE {{ streamLive ? '●' : '○' }}</span>
            <button
              type="button"
              class="rounded-full border px-2 py-0.5 text-[10px]"
              :class="museDebug
                ? 'border-amber-400/70 bg-amber-950/40 text-amber-200'
                : 'border-white/10 text-gray-500 hover:text-gray-300'"
              :title="t('museRefine.debugToggle')"
              @click="toggleDebug"
            >{{ t('museRefine.debugToggle') }}</button>
            <button
              type="button"
              class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700 disabled:opacity-40"
              :disabled="busy"
              @click="showSettings = !showSettings"
            >{{ t('museRefine.settings') }}</button>
            <button
              type="button"
              class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700 disabled:opacity-40"
              :disabled="busy"
              @click="startFresh()"
            >{{ t('museRefine.reset') }}</button>
            <button
              type="button"
              class="rounded-full px-2 py-1 text-gray-400 hover:bg-pink-950/60 hover:text-white"
              @click="close"
            >✕</button>
          </div>
        </header>

        <div class="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[1.1fr_0.9fr]">
          <!-- Chat + ledger -->
          <section class="flex min-h-0 flex-col border-r border-pink-500/15">
            <div class="border-b border-pink-500/15 px-3 py-2">
              <div class="flex flex-wrap items-center gap-2">
                <select
                  class="max-w-[10rem] truncate rounded-md border border-pink-500/30 bg-pink-950/30 px-2 py-1 text-xs text-pink-100"
                  :value="inputs.character_id || ''"
                  :disabled="chatLocked"
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
                  :disabled="chatLocked || !inputs.character_id"
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
                <span class="text-[10px] font-medium uppercase tracking-wide text-pink-400/80">NOW</span>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <input
                  v-model="themeDraft"
                  type="text"
                  class="min-w-0 flex-1 rounded-md border border-pink-500/20 bg-pink-950/25 px-2 py-1 text-[11px] text-pink-50 outline-none focus:border-pink-500"
                  :placeholder="t('museRefine.themePlaceholder')"
                  :disabled="chatLocked"
                  @change="patchInputs({ theme: themeDraft.trim() })"
                />
                <button
                  type="button"
                  class="rounded-lg bg-rose-800/80 px-2.5 py-1 text-[11px] font-medium text-rose-50 hover:bg-rose-700 disabled:opacity-40"
                  :disabled="chatLocked || !inputs.character_id"
                  @click="openSession"
                >{{ opened ? t('museRefine.reopen') : t('museRefine.open') }}</button>
              </div>
              <p class="mt-1 text-[11px] leading-snug text-pink-100/80">
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

            <div ref="chatEl" class="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3 py-3 text-sm">
              <div
                v-for="(row, i) in chat"
                :key="i"
                class="flex flex-col gap-1"
                :class="row.role === 'user' ? 'items-end' : 'items-start'"
              >
                <span
                  v-if="row.role !== 'user'"
                  class="flex items-center gap-1.5 px-0.5 text-[10px] font-medium"
                  :class="isBanterRow(row) ? 'text-pink-300/90' : 'text-pink-300/80'"
                >
                  <img
                    v-if="faceForRow(row) && (isSayRow(row) || isBanterRow(row) || row.meta?.kind === 'verify_ok' || row.meta?.kind === 'verify_repair' || row.meta?.kind === 'verify_repaired' || row.meta?.kind === 'pitch' || row.meta?.kind === 'standing' || row.meta?.kind === 'contract')"
                    :src="faceForRow(row)"
                    alt=""
                    class="h-7 w-7 shrink-0 rounded-full object-cover border border-pink-100 shadow-md ring-2 ring-pink-400/80"
                  />
                  <template v-if="isBanterRow(row)">💭 {{ t('museRefine.asideTitle') }} · {{ row.name }}</template>
                  <template v-else-if="isSayRow(row)">🌸 {{ row.name || waitName }}</template>
                  <template v-else-if="row.meta?.speaker">🌸 {{ row.name }} · {{ row.meta.speaker }}</template>
                  <template v-else>{{ rowKindLabel(row, t) }}</template>
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
                          : 'border-pink-500/40 bg-pink-900/50 text-pink-50'"
                    :title="chip.key"
                  >
                    <span aria-hidden="true">{{ chip.icon }}</span>
                    <span>{{ chip.label }}</span>
                  </span>
                </span>
                <span
                  v-else
                  class="flex items-center gap-1.5 px-0.5 text-[10px] font-medium text-emerald-300/80"
                >
                  🎬 {{ t('museRefine.director') }}
                  <span
                    v-if="isStruckRow(row)"
                    class="rounded-full border border-amber-700/50 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-200"
                  >{{ t('museRefine.struck') }}</span>
                </span>
                <div
                  v-if="!isChangeRow(row) || row.text"
                  class="max-w-[90%] whitespace-pre-wrap leading-relaxed shadow-sm"
                  :class="[
                    row.role === 'user'
                      ? (isStruckRow(row)
                        ? 'rounded-2xl rounded-tr-sm border border-gray-700/50 bg-gray-900/50 px-3.5 py-2 text-[12px] text-gray-500 line-through decoration-amber-700/80'
                        : 'rounded-2xl rounded-tr-sm border border-emerald-500/40 bg-emerald-950/50 px-3.5 py-2 text-[12px] text-emerald-100')
                      : isBanterRow(row)
                        ? 'ml-1 rounded-2xl rounded-tl-sm border border-dashed border-pink-400/45 bg-gradient-to-br from-pink-950/50 via-rose-950/40 to-fuchsia-950/30 px-3 py-1.5 text-[11px] italic text-pink-200/95'
                        : row.meta?.kind === 'verify_ok' || row.meta?.kind === 'verify_repaired'
                          ? 'rounded-2xl rounded-tl-sm border border-emerald-800/40 bg-emerald-950/25 px-3.5 py-2 text-[12px] text-emerald-50'
                          : row.meta?.kind === 'verify_repair'
                            ? 'rounded-2xl rounded-tl-sm border border-orange-800/40 bg-orange-950/25 px-3.5 py-2 text-[12px] text-orange-50'
                            : isChangeRow(row)
                              ? (row.meta?.kind === 'ledger_missed'
                                ? 'rounded-xl border border-amber-700/50 bg-amber-950/30 px-3 py-2 text-[11px] text-amber-100'
                                : 'rounded-xl border border-pink-500/25 bg-pink-950/20 px-3 py-2 text-[11px] text-pink-100')
                              : row.role === 'system'
                                ? 'rounded-xl border border-slate-700/40 bg-slate-900/60 px-3 py-2 text-[11px] text-gray-400'
                                : 'rounded-2xl rounded-tl-sm border border-pink-500/30 bg-slate-900/80 px-3.5 py-2 text-[12px] text-pink-50',
                  ]"
                >{{ row.text }}</div>
              </div>
              <div
                v-if="waitingOnModel"
                class="my-1.5 flex items-center gap-2 pl-0.5 text-pink-300/80"
              >
                <img
                  v-if="leadFace"
                  :src="leadFace"
                  alt=""
                  class="h-6 w-6 shrink-0 rounded-full object-cover border border-pink-100/40 ring-1 ring-pink-400/50 refine-wait-pulse"
                />
                <span class="max-w-[10rem] truncate text-[10px] font-medium">{{ waitName }}</span>
                <span class="refine-dots text-[11px]" :aria-label="t('museRefine.thinking')">
                  <span>.</span><span>.</span><span>.</span>
                </span>
                <span v-if="elapsed" class="font-mono text-[10px] text-pink-400/55">{{ clock(elapsed) }}</span>
              </div>
              <p v-if="!chat.length && !waitingOnModel" class="text-xs text-gray-500">{{ t('museRefine.chatHint') }}</p>
            </div>

            <form class="flex flex-col gap-2 border-t border-pink-500/15 p-3" @submit.prevent="sendChat">
              <div v-if="tasteChips.length || againFeelAvailable || lastPitch.length" class="flex flex-wrap gap-1.5">
                <button
                  v-for="chip in tasteChips"
                  :key="`taste-${chip}`"
                  type="button"
                  class="rounded-full border border-sky-800/50 bg-sky-950/40 px-2.5 py-1 text-[11px] text-sky-100 hover:bg-sky-900/50 disabled:opacity-40"
                  :disabled="chatLocked"
                  @click="insertChip(chip)"
                >{{ chip }}</button>
                <button
                  v-if="againFeelAvailable"
                  type="button"
                  class="rounded-full border border-rose-800/50 bg-rose-950/40 px-2.5 py-1 text-[11px] text-rose-100 hover:bg-rose-900/50 disabled:opacity-40"
                  :disabled="chatLocked"
                  @click="insertChip(t('museRefine.againFeelSend'))"
                >{{ t('museRefine.againFeel') }}</button>
                <button
                  v-for="opt in lastPitch"
                  :key="opt"
                  type="button"
                  class="rounded-full border border-violet-700/50 bg-violet-950/40 px-2.5 py-1 text-[11px] text-violet-100 hover:bg-violet-900/50 disabled:opacity-40"
                  :disabled="chatLocked"
                  @click="sendPitch(opt)"
                >「{{ opt }}」</button>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="rounded-lg border border-pink-500/40 bg-pink-950/40 px-2.5 py-1.5 text-[10px] font-medium text-pink-100 hover:bg-pink-900/50 disabled:opacity-40"
                  :disabled="chatLocked || comfyOffline || !craft.prompt"
                  :title="craft.prompt ? t('museRefine.boardHint') : t('museRefine.prepFirst')"
                  @click="runStage('board')"
                >{{ t('museRefine.board') }}</button>
                <button
                  type="button"
                  class="rounded-lg border border-amber-500/50 bg-amber-950/40 px-2.5 py-1.5 text-[10px] font-medium text-amber-200 hover:bg-amber-900/60 disabled:opacity-40"
                  :disabled="chatLocked || comfyOffline || !craft.prompt || !boardReady"
                  :title="boardReady ? t('museRefine.approveTitle') : t('museRefine.approveNeedsBoard')"
                  @click="runStage('approve')"
                >{{ t('museRefine.approve') }}</button>
                <button
                  type="button"
                  class="rounded-lg border border-slate-600/50 bg-slate-900/60 px-2.5 py-1.5 text-[10px] text-gray-300 hover:bg-slate-800 disabled:opacity-40"
                  :disabled="chatLocked || !inputs.character_id"
                  @click="runStage('wardrobe')"
                >{{ t('museRefine.wardrobe') }}</button>
                <button
                  type="button"
                  class="ml-auto rounded-lg border border-rose-500/50 bg-rose-950/40 px-2.5 py-1.5 text-[10px] font-medium text-rose-200 hover:bg-rose-900/60 disabled:opacity-40"
                  :disabled="chatLocked || !shootImages.length || diaryWriting"
                  :title="shootImages.length ? '' : t('museRefine.finishNeedsShoot')"
                  @click="finishSession"
                >{{ diaryWriting ? t('museRefine.diaryWriting') : diaryDone ? t('museRefine.diaryDone') : t('museRefine.finish') }}</button>
                <button
                  v-if="diaryDone && inputs.character_id"
                  type="button"
                  class="rounded-lg border border-pink-500/50 bg-pink-950/40 px-2.5 py-1.5 text-[10px] text-pink-200 hover:bg-pink-900/60"
                  @click="showDiary = true"
                >{{ t('museRefine.openDiary') }}</button>
              </div>
              <p v-if="craft.prompt && !boardReady" class="text-[10px] text-gray-500">
                {{ t('museRefine.approveNeedsBoard') }}
              </p>
              <div class="flex gap-2">
                <input
                  v-model="chatInput"
                  type="text"
                  class="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm outline-none focus:border-pink-500"
                  :placeholder="t('museRefine.chatPlaceholder')"
                  :disabled="chatLocked || !session"
                />
                <button
                  type="submit"
                  class="rounded-lg bg-pink-600 px-3 py-2 text-sm font-medium text-white hover:bg-pink-500 disabled:opacity-40"
                  :disabled="chatLocked || !chatInput.trim()"
                >{{ t('museRefine.send') }}</button>
              </div>
              <p class="text-[10px] text-gray-500">
                {{ statusLabel }}
                <span v-if="elapsed"> · {{ t('museRefine.elapsed', { s: clock(elapsed) }) }}</span>
              </p>
            </form>
          </section>

          <!-- Prompt / options / images -->
          <section class="flex min-h-0 flex-col gap-3 overflow-y-auto p-3">
            <!-- live preview + galleries (Muse-aligned) -->
            <div
              v-if="preview || boardPending || shootPending"
              class="flex flex-col items-center gap-2"
            >
              <img
                v-if="preview"
                :src="preview"
                alt=""
                class="max-h-[42vh] w-full cursor-zoom-in rounded-lg border border-pink-500/20 bg-black object-contain shadow-2xl"
                :title="t('museRefine.zoomImage')"
                @click="openLightbox(preview)"
              />
              <div
                v-else
                class="flex aspect-[3/4] max-h-[42vh] w-full animate-pulse items-center justify-center rounded-lg border border-pink-500/20 bg-black/40 text-[11px] text-gray-500"
              >
                {{ job?.progress_text || t('museRefine.renderWait') }}
              </div>
              <div class="h-1 w-full overflow-hidden rounded bg-white/10">
                <div
                  class="h-full bg-pink-500/80 transition-all"
                  :style="{ width: `${Math.round((job?.progress || 0) * 100)}%` }"
                />
              </div>
            </div>

            <p v-if="boardError" class="text-[11px] text-amber-300/90">{{ boardError }}</p>
            <p v-if="shootError" class="text-[11px] text-red-300/90">{{ shootError }}</p>

            <div v-if="boardImages.length" class="space-y-2">
              <h4 class="text-[11px] font-medium text-amber-200/90">{{ t('museRefine.imagesBoardTitle') }}</h4>
              <p class="text-[10px] text-gray-500">{{ t('museRefine.imagesBoardAsk') }}</p>
              <div class="grid grid-cols-2 gap-2">
                <figure
                  v-for="img in boardImages"
                  :key="img.image_id || img.sha256 || img"
                  class="cursor-zoom-in overflow-hidden rounded border border-white/10"
                  :title="t('museRefine.zoomImage')"
                  @click="openLightboxSha(img.image_id || img.sha256 || img)"
                >
                  <img
                    :src="thumb(img.image_id || img.sha256 || img)"
                    class="block w-full"
                    alt=""
                  />
                </figure>
              </div>
            </div>

            <div v-if="shootImages.length" class="space-y-2">
              <h4 class="text-[11px] font-medium text-amber-200/90">{{ t('museRefine.imagesShootTitle') }}</h4>
              <div class="grid grid-cols-2 gap-2">
                <figure
                  v-for="img in shootImages"
                  :key="img.image_id || img.sha256 || img"
                  class="cursor-zoom-in overflow-hidden rounded border border-pink-500/40"
                  :title="t('museRefine.zoomImage')"
                  @click="openLightboxSha(img.image_id || img.sha256 || img)"
                >
                  <img
                    :src="full(img.image_id || img.sha256 || img)"
                    class="block w-full"
                    alt=""
                  />
                </figure>
              </div>
            </div>

            <div class="rounded-xl border border-pink-500/20 bg-gray-950/60 p-3">
              <div class="mb-2 text-xs font-medium text-pink-200/90">{{ t('museRefine.ledger') }}</div>
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
              <div v-if="standing.length" class="mt-2 border-t border-pink-500/15 pt-2">
                <div class="mb-1 text-[10px] uppercase tracking-wide text-amber-200/70">{{ t('museRefine.standing') }}</div>
                <ul class="space-y-0.5 text-[11px] text-amber-100/80">
                  <li v-for="(s, i) in standing" :key="i">· {{ s }}</li>
                </ul>
              </div>
              <div v-if="banned.length" class="mt-2 border-t border-pink-500/15 pt-2">
                <div class="mb-1 text-[10px] uppercase tracking-wide text-red-200/70">{{ t('museRefine.banned') }}</div>
                <div class="flex flex-wrap gap-1">
                  <button
                    v-for="tag in banned"
                    :key="tag"
                    type="button"
                    class="rounded-full border border-red-800/50 bg-red-950/40 px-2 py-0.5 text-[10px] text-red-100 hover:bg-red-900/50 disabled:opacity-40"
                    :disabled="chatLocked"
                    :title="t('museRefine.restoreBanned')"
                    @click="restoreBanned(tag)"
                  >✕ {{ tag }}</button>
                </div>
              </div>
              <div class="mt-2 flex flex-wrap gap-1 border-t border-pink-500/15 pt-2">
                <span class="w-full text-[10px] text-gray-500">{{ t('museRefine.restate') }}</span>
                <button
                  v-for="f in restateFields"
                  :key="f"
                  type="button"
                  class="rounded border px-1.5 py-0.5 text-[10px] hover:border-pink-500/50"
                  :class="stickyFields.has(f)
                    ? 'border-violet-700/60 bg-violet-950/40 text-violet-100'
                    : 'border-gray-700 bg-gray-900 text-gray-300'"
                  :disabled="chatLocked"
                  @click="restateField(f)"
                >{{ t(`museRefine.fields.${f}`) }}</button>
              </div>
            </div>

            <div class="rounded-xl border border-pink-500/20 bg-gray-950/60 p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-pink-200/90">{{ t('museRefine.options') }}</span>
                <button
                  type="button"
                  class="text-[11px] text-pink-300/80 hover:text-pink-200 disabled:opacity-40"
                  :disabled="chatLocked"
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

            <div class="rounded-xl border border-pink-500/20 bg-gray-950/60 p-3">
              <div class="mb-1 text-xs font-medium text-pink-200/90">{{ t('museRefine.prompt') }}</div>
              <pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-gray-300">{{ craft.prompt || '—' }}</pre>
              <p v-if="craft.support_tags" class="mt-2 text-[10px] text-gray-500">
                support: {{ craft.support_tags }}
              </p>
              <div
                v-if="museDebug && hasVisibleConsequences"
                class="mt-2 rounded border border-sky-800/40 bg-sky-950/20 px-2 py-1.5 text-[10px] text-sky-100/85"
              >
                <div class="font-medium text-sky-200/90">{{ t('museRefine.visibleTitle') }}</div>
                <div v-if="(visibleConsequences.causes || []).length">
                  {{ t('museRefine.visibleCauses') }}{{ (visibleConsequences.causes || []).join(' · ') }}
                </div>
                <div v-if="(visibleConsequences.tags || []).length">
                  {{ t('museRefine.visibleTags') }}{{ (visibleConsequences.tags || []).join(', ') }}
                </div>
              </div>
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

              <div
                v-if="hasVisibleConsequences"
                class="mb-3 rounded border border-sky-800/50 bg-sky-950/30 px-2 py-2"
              >
                <div class="mb-1 font-semibold text-sky-200/95">{{ t('museRefine.visibleTitle') }}</div>
                <p class="mb-1.5 text-sky-100/50">{{ t('museRefine.visibleHint') }}</p>
                <div v-if="(visibleConsequences.causes || []).length" class="mb-1">
                  <span class="text-sky-300/80">{{ t('museRefine.visibleCauses') }}</span>
                  <span class="text-sky-100">{{ (visibleConsequences.causes || []).join(' · ') }}</span>
                </div>
                <div v-if="(visibleConsequences.tags || []).length" class="mb-1">
                  <span class="text-sky-300/80">{{ t('museRefine.visibleTags') }}</span>
                  <span class="text-emerald-200/90">{{ (visibleConsequences.tags || []).join(', ') }}</span>
                </div>
                <div class="mb-1 text-sky-100/70">
                  {{ t('museRefine.visibleDensify') }}:
                  <span :class="visibleConsequences.densified ? 'text-emerald-300/90' : 'text-amber-100/60'">
                    {{ visibleConsequences.densified
                      ? t('museRefine.visibleDensifyYes')
                      : t('museRefine.visibleDensifyNo') }}
                  </span>
                  <span
                    v-if="visibleConsequences.densify_reason"
                    class="text-sky-100/40"
                  > · {{ visibleConsequences.densify_reason }}</span>
                </div>
                <ul v-if="(visibleConsequences.hints || []).length" class="mt-1 space-y-0.5">
                  <li
                    v-for="(h, i) in (visibleConsequences.hints || [])"
                    :key="`vc-${i}`"
                    class="whitespace-pre-wrap text-sky-100/75"
                  >→ {{ h }}</li>
                </ul>
                <p class="mt-1.5 text-sky-100/40">{{ t('museRefine.visibleCraftOnly') }}</p>
              </div>
              <p v-else-if="museDebug" class="mb-3 text-amber-100/40">{{ t('museRefine.visibleEmpty') }}</p>

              <div v-if="pipelineStages.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('museRefine.pipelineTitle') }}</div>
                <p class="mb-1.5 text-amber-100/50">{{ t('museRefine.pipelineHint') }}</p>
                <ol class="flex flex-wrap gap-1">
                  <li
                    v-for="stage in pipelineStages"
                    :key="stage.id"
                    class="min-w-[4.5rem] rounded border px-1.5 py-1"
                    :class="pipelineStatusClass(stage.status)"
                    :title="JSON.stringify(stage)"
                  >
                    <div class="font-semibold">{{ stage.id }}</div>
                    <div class="text-[9px] opacity-80">{{ stage.status }}</div>
                    <div
                      v-if="stage.id === 'assemble' && (stage.visible_tags || []).length"
                      class="mt-0.5 text-[9px] text-sky-200/80"
                    >{{ (stage.visible_tags || []).slice(0, 4).join(', ') }}</div>
                  </li>
                </ol>
                <ul v-if="pipelineDivergences.length" class="mt-1.5 space-y-0.5">
                  <li
                    v-for="(d, i) in pipelineDivergences"
                    :key="`${d.field}-${i}`"
                    class="text-rose-300/90"
                  >
                    ⌁ {{ d.field }} · {{ d.detail }}
                  </li>
                </ul>
              </div>

              <div v-if="rewriteLog.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('museRefine.rewriteLog') }}</div>
                <ul class="space-y-1.5">
                  <li
                    v-for="(entry, i) in rewriteLog"
                    :key="`${entry.at}-${i}`"
                    class="rounded border border-amber-800/40 px-2 py-1.5"
                  >
                    <div class="font-semibold text-amber-200/90">
                      {{ entry.source }}
                      <span class="font-normal text-amber-100/50">{{ rewriteWhen(entry.at) }}</span>
                      <span v-if="entry.intent" class="ml-1 font-normal">· {{ entry.intent }}</span>
                    </div>
                    <div
                      v-for="(pair, field) in (entry.changed || {})"
                      :key="field"
                      class="mt-0.5 whitespace-pre-wrap text-amber-100/70"
                    >
                      <span class="text-amber-300/80">{{ field }}</span>
                      {{ ' ' }}{{ pair.before || '∅' }} → {{ pair.after || '∅' }}
                      <div v-if="pair.why" class="pl-3 italic text-amber-100/50">↳ {{ pair.why }}</div>
                    </div>
                  </li>
                </ul>
              </div>
              <p v-else class="mb-3 text-amber-100/50">{{ t('museRefine.debugEmpty') }}</p>

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
                    <span class="text-amber-300/80">{{ ((s.ms || 0) / 1000).toFixed(1) }}s</span>
                    {{ ' ' }}{{ s.stage }}
                    <span class="text-amber-100/40">{{ rewriteWhen(s.at) }}</span>
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
                    <span class="text-amber-100/40"> {{ rewriteWhen(row.at) }}</span>
                    — {{ row.detail }}
                    <div
                      v-if="row.kind === 'visible_consequences' && (row.causes || []).length"
                      class="pl-2 text-sky-200/80"
                    >causes: {{ (row.causes || []).join(', ') }}</div>
                    <div
                      v-if="row.kind === 'visible_consequences' && (row.tags || []).length"
                      class="pl-2 text-emerald-200/80"
                    >tags: {{ (row.tags || []).join(', ') }}</div>
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

  <Teleport to="body">
    <div
      v-if="lightboxSrc"
      class="fixed inset-0 z-[var(--z-panel-media)] flex items-center justify-center bg-black/92 p-3"
      role="dialog"
      :aria-label="t('museRefine.zoomImage')"
      tabindex="0"
      @mousedown.self="closeLightbox"
      @keydown="onLightboxKey"
    >
      <button
        type="button"
        class="absolute right-3 top-3 rounded-full bg-black/50 px-3 py-1.5 text-lg text-gray-200 hover:bg-black/70"
        :title="t('museRefine.close')"
        @click="closeLightbox"
      >✕</button>
      <img
        :src="lightboxSrc"
        alt=""
        class="max-h-full max-w-full select-none object-contain shadow-2xl"
        @click.stop
      />
    </div>
  </Teleport>
</template>

<style scoped>
.refine-dots span {
  display: inline-block;
  animation: refine-dot-bounce 1.2s ease-in-out infinite;
  opacity: 0.35;
}
.refine-dots span:nth-child(2) { animation-delay: 0.16s; }
.refine-dots span:nth-child(3) { animation-delay: 0.32s; }
.refine-wait-pulse {
  animation: refine-wait-soft 1.6s ease-in-out infinite;
}
@keyframes refine-dot-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-2px); opacity: 1; }
}
@keyframes refine-wait-soft {
  0%, 100% { opacity: 0.7; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.04); }
}
@media (prefers-reduced-motion: reduce) {
  .refine-dots span, .refine-wait-pulse { animation: none; opacity: 1; }
}
</style>

<script setup>
/*
 * Muse — the Showrunner's studio. The ledger is the record of truth.
 *
 * With classic retired there is one studio (2026-09-12). `Refine` survives in the
 * name only as a stored value (`service.STUDIO`) and as the observation identifier;
 * the screen and the URLs are folded into `muse`.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import ActressDiaryModal from './muse/ActressDiaryModal.vue'
import CharacterGallery from './CharacterGallery.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, default: () => () => new Map() },
  // Whoever the roster (`CharacterGallery`) chose. Read **once, on opening**.
  // With classic retired, the path from the roster comes here (2026-09-12).
  initialCharacterId: { type: String, default: '' },
  initialPartnerId: { type: String, default: '' },
})
const emit = defineEmits(['update:show', 'toast', 'select-image', 'session-state'])
const { t, locale } = useI18n()

//: Opening and closing the roster (`CharacterGallery`). The same two as classic —
//: the lead and the partner.
const showPicker = ref(false)
const showPartnerPicker = ref(false)
const session = ref(null)
const catalog = ref(null)
const characterList = ref([])
const busy = ref(false)
const showSettings = ref(false)
const DEBUG_KEY = 'muse.debug'
const museDebug = ref(typeof localStorage !== 'undefined' && localStorage.getItem(DEBUG_KEY) === '1')
function toggleDebug() {
  museDebug.value = !museDebug.value
  try { localStorage.setItem(DEBUG_KEY, museDebug.value ? '1' : '0') } catch { /* private window */ }
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
// The line she is still speaking, not yet settled (`chat_delta`).
const liveSay = ref('')
// Who owns the streaming line. With a crew the seat changes one after another.
const liveName = ref('')
const liveIsLead = ref(true)
// **Whose words are streaming (2026-09-16).** Miss one `muse_speaking` and two
// people's words piled into one bubble (the Showrunner: "the Muses' conversations
// get mixed up"). `chat_delta` carries a `muse_id` too, so it folds when that
// changes.
const liveId = ref('')
// The seats that finished streaming this turn. Kept on screen only until the
// settled rows arrive.
const liveDone = ref([])

/**
 * Cleaning up after speech. **`speaking` must never outlive `busy`.**
 *
 * The Showrunner: "the end-of-conversation handling is poor — it stays busy and
 * after that no input is possible at all". The cause was `speaking`: `chatLocked` is
 * `busy || renderLocked || speaking`, and the flag raised by the SSE
 * `muse_speaking` was lowered only in the `chat` / `session_updated` branches, and
 * **only while `!busy`**. A signal arriving mid-POST was discarded there, so by the
 * time the POST finished nobody was left to lower it.
 *
 * Hit on a studio shoot — opening a crew makes the seats emit `muse_speaking` 3-6
 * times, and `runStage` lowers only `busy`, so the input simply died.
 */
function stopSpeaking() {
  speaking.value = false
  liveSay.value = ''
  liveName.value = ''
  liveId.value = ''
  // The settled rows arrive in `session.chat`, so what was streaming has done its
  // job.
  liveDone.value = []
}

/**
 * Fold the finished line away and clear the slot for the next person.
 *
 * **The previous seat's words are not erased (2026-09-12).** The Showrunner: "it is
 * as if a reset runs every time a seat speaks — it keeps rewinding". The settled
 * rows appear only once the POST returns, so without folding here the previous
 * words vanish the moment the next seat begins.
 */
function foldLive() {
  if (liveSay.value.trim()) {
    liveDone.value = [...liveDone.value, {
      name: liveName.value,
      text: liveText.value,
      lead: liveIsLead.value,
      // **The face stays after folding (2026-09-18).** With the thumbnail gone
      // from folded bubbles alone, the same person speaking on and on made the face
      // appear and disappear.
      face: liveIsLead.value ? leadFace.value : '',
    }].slice(-24)
  }
  liveSay.value = ''
}
// In a duet, `A:` / `B:` arrive at the start of a line (the marker for whose line
// it is). **The settled rows come out separated by name**, so a marker that only
// exists while streaming is not shown.
const liveText = computed(() =>
  liveSay.value.replace(/^[ \t]*[AB][:：][ \t]*/gm, ''),
)
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
// What the debug pane on the right reads. **Never used to decide anything.**
//
// What was removed is only the "visible result" (the Showrunner: "the observation
// feature does not work very effectively, so remove it. It is keyword-based and
// hardly ever used") — `visible_consequence_cues`, which caught wind and back views
// by regular expression and added them to the prose, went along with its backend.
// The pane itself, the rewrites, the stage times and the event log all stay. With
// "picture updated" dropped from the conversation, **this is the only place left
// for them**.
const refineLog = computed(() => [...(session.value?.refine_log || [])].slice().reverse())
const stageMs = computed(() => [...(session.value?.stage_ms || [])].slice(-12).reverse())
const turnTrace = computed(() => [...(session.value?.turn_trace || [])].slice().reverse())
const rewriteLog = computed(() => [...(session.value?.rewrite_log || [])].slice().reverse())
const pipeline = computed(() => session.value?.pipeline || null)
const pipelineStages = computed(() => pipeline.value?.stages || [])
const pipelineDivergences = computed(() => pipeline.value?.divergences || [])
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
//: What must be filled in before starting. **What is missing is named.**
const missingBeforeStart = computed(() => {
  const want = []
  if (!inputs.value.character_id) want.push(t('muse.pickCharacter'))
  if (!inputs.value.model) want.push(t('muse.needModel'))
  if (!inputs.value.workflow) want.push(t('muse.needWorkflow'))
  if (shootMode.value === 'studio' && !tableOpen.value && !inputs.value.crew_preset) {
    want.push(t('muse.crewPresetNeeded'))
  }
  return want.join(' / ')
})
const startReady = computed(() => !missingBeforeStart.value)

const workflows = computed(() => {
  const list = catalog.value?.comfyui?.workflows || catalog.value?.workflows || []
  return Array.isArray(list) ? list : []
})
const models = computed(() => catalog.value?.llm?.models || [])

// **Which image-model family this workflow belongs to (2026-09-20).** The
// Showrunner: "I want to use krea2 too — it needs no negative prompt, 8 steps is
// enough". The backend decides it from the workflow's name or a marker in the
// graph (`muse/family.py`); the catalog carries the answer per workflow so the
// panel can say so before the first frame.
const workflowFamily = computed(() => {
  const caps = catalog.value?.comfyui?.workflow_caps || []
  const hit = caps.find(c => c?.name === (inputs.value.workflow || ''))
  return String(hit?.family || 'anima')
})
const familyRow = computed(() => catalog.value?.image_families?.[workflowFamily.value] || null)
// What the chosen workflow renders at on its own, when Muse writes no canvas.
const workflowCanvas = computed(() => {
  const caps = catalog.value?.comfyui?.workflow_caps || []
  return caps.find(c => c?.name === (inputs.value.workflow || ''))?.canvas || null
})

// The same rule the render uses (`runtime.render_settings`): a knob still at the
// shipped default is the family's to fill in; a number he typed is his.
const shippedDefaults = computed(() => catalog.value?.suggested_run || {})
function effectiveSteps (key) {
  const mine = inputs.value?.[key]
  const shipped = shippedDefaults.value?.[key]
  if (mine !== undefined && shipped !== undefined && Number(mine) !== Number(shipped)) return Number(mine)
  const wanted = familyRow.value?.[key]
  return wanted == null ? Number(mine ?? shipped ?? 0) : Number(wanted)
}
const draftSteps = computed(() => effectiveSteps('draft_steps'))
const finalSteps = computed(() => effectiveSteps('final_steps'))

// The canvas follows the same rule, with one difference: when the family leaves
// the size to the workflow, an untouched field is shown **empty** — there is no
// number to show, because the graph's own is the answer.
function canvasValue (key) {
  const mine = inputs.value?.[key]
  const shipped = shippedDefaults.value?.[key]
  const touched = mine !== undefined && shipped !== undefined && Number(mine) !== Number(shipped)
  if (touched) return Number(mine)
  return familyRow.value?.canvas === 'workflow' ? '' : Number(mine ?? shipped ?? 0)
}
const boardImages = computed(() => session.value?.board?.images || [])
const shootImages = computed(() => session.value?.shoot?.images || [])
const boardReady = computed(() => !!session.value?.board?.ready)
const boardError = computed(() => String(session.value?.board?.error || '').trim())
const shootError = computed(() => String(session.value?.shoot?.error || '').trim())
const sessionStatus = computed(() => String(session.value?.status || ''))
const partner = computed(() => session.value?.partner_character || {})
const standing = computed(() => session.value?.standing || [])
const bond = computed(() => session.value?.bond || {})
const banned = computed(() => session.value?.banned || [])
const opened = computed(() => !!session.value?.opened)
// On a conversation turn, building the prose and tags waits until the shot (the
// backend's `touch_craft`).
const craftStale = computed(() => !!craft.value?.stale)
// Whether a studio shoot (with a crew) is open. The same mark as the backend's
// `crew_room.TABLE_OPEN`.
const tableOpen = computed(() => !!session.value?.crew_open)
const crewSeats = computed(() => Number(session.value?.crew_seats || 0))
// `crew.PRESETS` is the record of truth for who is on a crew (through the
// catalogue). Never written out on the screen.
const crewPresets = computed(() => catalog.value?.crew?.presets || [])
// The kind of shoot, chosen before opening. Once open, the session's mark is the
// record of truth.
const shootMode = ref('solo')
watch(tableOpen, (on) => { if (on) shootMode.value = 'studio' })
const diaryState = computed(() => session.value?.diary || {})
const diaryDone = computed(() => diaryState.value.status === 'ok')
const diaryWriting = computed(() => diaryState.value.status === 'writing')

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
  if (waitingOnModel.value) return t('muse.status.chatting')
  const st = sessionStatus.value
  if (st === 'boarding' || boardPending.value) return t('muse.status.boarding')
  if (st === 'awaiting_ok') return t('muse.status.awaitingOk')
  if (st === 'shooting' || shootPending.value) return t('muse.status.shooting')
  if (st === 'done') return t('muse.status.done')
  if (st === 'finished') return t('muse.status.finished')
  if (opened.value) return t('muse.status.chat')
  return t('muse.status.idle')
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
  // The partner's fields only when there is a partner. The ledger side splits the
  // same way, through `PARTNER_KEYS`.
  if (partner.value?.character_id) {
    rows.push('wearing_b', 'beat_b', 'expression_b')
  }
  return rows.map((key) => ({
    key,
    label: t(`muse.fields.${key}`),
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
  if (!catalog.value) catalog.value = await api('/api/muse/catalog')
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
    // **The first of the list is not applied (the Showrunner, 2026-09-13).**
    // "Empty the llm and image model too, so they are chosen before running. If a
    // default is set in the admin screen, it should be possible to start from that
    // default." The default is **only what the admin screen decided** — failing
    // that it stays empty and asks to choose before starting.
    const admin = catalog.value?.admin_defaults || {}
    const body = {
      locale: isJa.value ? 'ja' : 'en',
      model: admin.muse_model || '',
      workflow: admin.muse_workflow || '',
      enhance_quality: false,
    }
    if (characterId) body.character_id = characterId
    session.value = await api('/api/muse/sessions', {
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
      `/api/muse/sessions/${session.value.session_id}/inputs`,
      { method: 'PATCH', body: JSON.stringify(patch) },
    )
  } catch (err) {
    fail(err)
  }
}

async function pickCharacter(id) {
  showPicker.value = false
  if (!id) return
  if (!session.value?.session_id) {
    await startFresh(id)
    return
  }
  busy.value = true
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/character`,
      { method: 'POST', body: JSON.stringify({ character_id: id }) },
    )
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}

async function pickPartner(id) {
  showPartnerPicker.value = false
  if (!session.value?.session_id) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/partner`,
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

async function openSession() {
  if (!session.value?.session_id || busy.value) return
  if (!inputs.value.character_id) {
    fail(new Error(t('muse.needCharacter')))
    return
  }
  // A shoot never starts with something unchosen. All of these are **hard to change
  // once open**.
  if (!inputs.value.model) { fail(new Error(t('muse.needModel'))); return }
  if (!inputs.value.workflow) { fail(new Error(t('muse.needWorkflow'))); return }
  if (shootMode.value === 'studio' && !tableOpen.value && !inputs.value.crew_preset) {
    fail(new Error(t('muse.crewPresetNeeded')))
    return
  }
  busy.value = true
  speaking.value = true
  if (!startedAt) startedAt = Date.now()
  try {
    if (themeDraft.value.trim() && themeDraft.value.trim() !== (inputs.value.theme || '')) {
      await patchInputs({ theme: themeDraft.value.trim() })
    }
    // A studio shoot has its own door. A crew cannot be called in partway, so it is
    // settled at the start.
    const door = shootMode.value === 'studio' && !tableOpen.value ? 'table' : 'open'
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${door}`,
      { method: 'POST' },
    )
    stopSpeaking()
    await scrollChat()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
    stopSpeaking()
    startedAt = 0
    elapsed.value = 0
  }
}

async function restoreBanned(tag) {
  if (!session.value?.session_id || chatLocked.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/banned/restore`,
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
      `/api/muse/sessions/${session.value.session_id}/restate`,
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
    fail(new Error(t('muse.finishNeedsShoot')))
    return
  }
  if (!window.confirm(t('muse.finishConfirm'))) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/finish`,
      { method: 'POST' },
    )
    emit('toast', { msg: t('muse.finishToast'), type: 'info' })
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
    stopSpeaking()
  }
}

/**
 * The face for a row. **Decided by whose words they are.** (2026-09-18)
 *
 * The Showrunner: "the thumbnail is sometimes missing when Muse speaks".
 *
 * This used to return "the lead's face for an assistant row", which put her face on
 * the crew seats as well, so it was held back by **an allow-list of bubble kinds**
 * (say / banter / verify…). **Heckling was not on that allow-list** — she heckles
 * two or three times a turn, so **the same person lost her face on exactly those
 * rows.**
 *
 * Rather than an allow-list, it is decided by the row's own `meta.role` (the seat's
 * job). A crew seat has no face; the lead's rows carry one whatever their kind.
 */
function faceShaForRow(row) {
  const id = row?.meta?.speaker_id
  if (id && id === partner.value?.character_id) return partnerFaceSha.value
  if (id && id === leadCharacter.value?.character_id) return leadFaceSha.value
  // The crew seats (staging, lighting…) have no face. Only the lead is `actress`.
  const role = row?.meta?.role
  if (role && role !== 'actress') return ''
  if (row?.role === 'assistant') {
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
// Whether this turn moved the picture (🖼) or only talked (💬).
// **Nothing is shown on older rows with no stamp** — `undefined` is a third state.
function turnIcon(row) {
  const shot = row?.meta?.shot
  if (shot === true) return '🖼'
  if (shot === false) return '💬'
  return ''
}
function turnIconTitle(row, t) {
  const shot = row?.meta?.shot
  if (shot === true) return t('muse.turnShot')
  if (shot === false) return t('muse.turnTalk')
  return ''
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
      `/api/muse/sessions/${session.value.session_id}/chat`,
      { method: 'POST', body: JSON.stringify({ message: msg }) },
    )
    // The real rows have arrived. What was streaming folds **here** — wait until
    // `finally` and the same line appears twice for that instant.
    stopSpeaking()
    await scrollChat()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
    stopSpeaking()
    startedAt = 0
    elapsed.value = 0
  }
}

async function rebuild() {
  if (!session.value?.session_id || chatLocked.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/rebuild`,
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
      `/api/muse/sessions/${session.value.session_id}/${path}`,
      { method: 'POST' },
    )
    stopSpeaking()
    sampleJob()
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
    stopSpeaking()
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
    `/api/muse/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`,
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
      foldLive()
      // **Who is speaking.** On a studio shoot 18 people speak in turn, so leaving
      // the lead's name on the streaming bubble makes it impossible to tell whose
      // words they are.
      liveName.value = String(data.name || '')
      liveId.value = String(data.muse_id || '')
      liveIsLead.value = !data.muse_id
        || String(data.muse_id) === String(session.value?.character?.character_id || '')
      if (!startedAt) startedAt = Date.now()
      return
    }
    // **Stream her as she speaks (2026-09-10).** The Showrunner: "the conversation
    // is not streamed, so the wait really is felt". The backend passes it through
    // `_say_only`, so what arrives here is the contents of SAY and nothing else.
    if (data.type === 'chat_delta') {
      speaking.value = true
      // **When the owner of the words changes, fold there.** A field corner holds
      // several people's lines in one reply, so missing a `muse_speaking` lets the
      // next seat's words continue in the previous seat's bubble.
      const who = String(data.muse_id || '')
      if (who && liveId.value && who !== liveId.value) {
        foldLive()
        liveId.value = who
        liveIsLead.value = who === String(session.value?.character?.character_id || '')
        liveName.value = ''
      }
      liveSay.value += data.text || ''
      scrollChat()
      return
    }
    if (data.type === 'notebook_rewrite' || data.type === 'ledger_rewrite') {
      if (!session.value) return
      const log = mergeRewriteLog(session.value.rewrite_log || [], [data])
      session.value = { ...session.value, rewrite_log: log }
      return
    }
    if (data.type === 'chat' || data.type === 'chat_message') {
      // Once the settled rows arrive, the streaming draft has done its job.
      liveSay.value = ''
      liveName.value = ''
      // Local sendChat owns speaking/busy until POST returns.
      if (!busy.value) speaking.value = false
      scheduleRefresh(true)
      return
    }
    // **Say what reached the green room, there and then (2026-09-10).** The
    // Showrunner: "the trouble is that the timing of each of these is quite hard to
    // see". When a shoot ends, the diary, the report, the proposals and the habit
    // note run in the background; Refine was not picking up these signals, so on
    // screen nothing appeared to happen. **Each kind is announced separately.**
    // (Only the outings emit no signal, so they do not appear here.)
    if (data.type === 'lounge_status') {
      const msg = {
        shared: t('muse.loungeShared'),
        reacted: t('muse.loungeReacted'),
        pitch: t('muse.loungePitch'),
        habit: t('muse.loungeHabit'),
      }[String(data.status || '')]
      if (msg) emit('toast', { msg, type: 'info' })
      return
    }
    if (data.type === 'diary_status') {
      scheduleRefresh()
      if (data.status === 'ok') {
        emit('toast', { msg: t('muse.diaryReady'), type: 'info' })
      } else if (data.status === 'failed') {
        emit('toast', { msg: t('muse.diaryFailed'), type: 'error' })
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
      liveSay.value = ''
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
    const next = await api(`/api/muse/sessions/${session.value.session_id}`)
    // Drop stale GETs that raced a newer local POST / SSE save.
    const nextUpdated = Number(next?.updated_at || 0)
    if (minUpdatedAt && nextUpdated && nextUpdated < minUpdatedAt) return
    if (nextUpdated && prevUpdated && nextUpdated < prevUpdated && !opts.allowBusy) return
    next.rewrite_log = mergeRewriteLog(keep, next.rewrite_log)
    session.value = next
    sampleJob()
  } catch (err) {
    // **Never fail silently (2026-09-10).** This swallowing is how the accident of
    // deleting `mergeRewriteLog`'s definition while leaving the call reached
    // production. The session was never updated again, `board.pending` never came
    // down, and the input froze. The screen still does not get in the way (a failed
    // GET is common), but a trace is left.
    console.error('[muse] refresh failed', err)
  }
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
    // If the roster points at someone else, shoot again with them. The same person
    // stays seated (a session does not disappear on opening and closing).
    const wanted = String(props.initialCharacterId || '')
    const seated = String(session.value?.inputs?.character_id || '')
    if (wanted && wanted !== seated) {
      await startFresh(wanted)
      if (props.initialPartnerId) await pickPartner(props.initialPartnerId)
      return
    }
    if (!session.value) await startFresh()
    else openStream(session.value.session_id)
  } catch (err) {
    fail(err)
  }
})

// Tell the roster about a shoot that is paused (the "shooting" tag). A session
// survives being closed, so the roster has to know it can be returned to.
function publishSessionState() {
  const s = session.value
  emit('session-state', {
    available: Boolean(s?.session_id && s.status !== 'finished'),
    name: String(s?.character?.name || ''),
    sessionId: String(s?.session_id || ''),
  })
}
watch(
  [() => session.value?.session_id, () => session.value?.status,
   () => session.value?.character?.name],
  publishSessionState,
  { immediate: true },
)

// **A safety net.** Even if cleanup is forgotten somewhere, this always lowers it
// the moment `busy` comes down. The one breakage never to create is input that
// never comes back.
watch(busy, (now) => {
  if (!now) stopSpeaking()
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
// The crew seats (🎬) and the heckling between them (〃). Eighteen people speak, so
// they look clearly different from her lines — the lead's voice must not be
// buried.
function isSeatRow(row) {
  return (row?.meta?.kind) === 'seat'
}
function isHeckleRow(row) {
  return (row?.meta?.kind) === 'heckle'
}
function isCrewRow(row) {
  return isSeatRow(row) || isHeckleRow(row)
}
function isChangeRow(row) {
  const kind = row?.meta?.kind
  return kind === 'ledger_change' || kind === 'ledger_missed'
    || kind === 'verify_ok' || kind === 'verify_repair' || kind === 'verify_repaired'
}
function rowKindLabel(row, t) {
  const kind = row?.meta?.kind
  if (kind === 'banter') return t('muse.asideTitle')
  if (kind === 'ledger_change' || kind === 'ledger_missed') return t('muse.shotChange')
  if (kind === 'verify_ok') return t('muse.verifyOk')
  if (kind === 'verify_repair' || kind === 'verify_repaired') return t('muse.verifyRepair')
  if (kind === 'pitch') return t('muse.pitch')
  if (kind === 'standing') return t('muse.standing')
  if (kind === 'contract') return t('muse.contract')
  if (kind === 'theme') return t('muse.theme')
  if (kind === 'table_open') return t('muse.tableOpened')
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
      class="fixed inset-0 z-[var(--z-panel-muse)] flex items-stretch justify-center bg-black/70"
      @keydown.esc.stop="close"
    >
      <!--
        **Centred, using the full width (the Showrunner, 2026-09-12).** As a
        `max-w-5xl` column pinned to the right, the conversation and the ledger
        fought over the same width and both were narrow. It now stretches to the
        whole screen, capped on wide displays (`max-w-[1680px]`).
      -->
      <div
        class="flex h-full w-full max-w-[1680px] flex-col border-x border-pink-500/30 bg-slate-900/95 text-gray-100 shadow-2xl"
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
              {{ t('muse.title') }}
            </h2>
            <p class="truncate text-[11px] text-gray-500">{{ t('muse.subtitle') }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <span
              class="font-mono text-[10px]"
              :class="streamLive ? 'text-pink-400/70' : 'text-gray-500'"
              :title="t('muse.streamHint')"
            >SSE {{ streamLive ? '●' : '○' }}</span>
            <button
              type="button"
              class="rounded-full border px-2 py-0.5 text-[10px]"
              :class="museDebug
                ? 'border-amber-400/70 bg-amber-950/40 text-amber-200'
                : 'border-white/10 text-gray-500 hover:text-gray-300'"
              :title="t('muse.debugToggle')"
              @click="toggleDebug"
            >{{ t('muse.debugToggle') }}</button>
            <button
              type="button"
              class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700 disabled:opacity-40"
              :disabled="busy"
              @click="showSettings = !showSettings"
            >{{ t('muse.settings') }}</button>
            <button
              type="button"
              class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700 disabled:opacity-40"
              :disabled="busy"
              @click="startFresh()"
            >{{ t('muse.reset') }}</button>
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
                <!--
                  **Back from a dropdown to the roster (the Showrunner,
                  2026-09-12).** "Make switching the Muse work the same as in Muse
                  Classic. Show the Muse roster rather than a dropdown." The roster
                  is `CharacterGallery` — the very one classic used to open.
                -->
                <button
                  type="button"
                  class="flex min-w-0 items-center gap-2 rounded-lg border border-pink-500/30 bg-pink-950/30 px-2 py-1.5 text-left hover:border-pink-400/60 disabled:opacity-40"
                  :disabled="chatLocked"
                  @click="showPicker = true"
                >
                  <img
                    v-if="leadFace"
                    :src="leadFace"
                    class="h-9 w-7 shrink-0 rounded object-cover"
                    alt=""
                  />
                  <span v-else class="h-9 w-7 shrink-0 rounded bg-black/40"></span>
                  <span class="min-w-0">
                    <span class="block truncate text-xs text-pink-100">
                      {{ (isJa ? (leadCharacter.name_ja || leadCharacter.name) : (leadCharacter.name || leadCharacter.name_ja)) || t('muse.pickCharacter') }}
                    </span>
                    <span class="block text-[9px] text-pink-300/60">{{ t('muse.pickCharacter') }}</span>
                  </span>
                </button>
                <button
                  type="button"
                  class="flex min-w-0 items-center gap-2 rounded-lg border border-fuchsia-900/40 bg-fuchsia-950/30 px-2 py-1.5 text-left hover:border-fuchsia-500/60 disabled:opacity-40"
                  :disabled="chatLocked || !inputs.character_id"
                  @click="showPartnerPicker = true"
                >
                  <img
                    v-if="partnerFace"
                    :src="partnerFace"
                    class="h-9 w-7 shrink-0 rounded object-cover"
                    alt=""
                  />
                  <span v-else class="h-9 w-7 shrink-0 rounded bg-black/40"></span>
                  <span class="min-w-0">
                    <span class="block truncate text-xs text-fuchsia-100">
                      {{ (isJa ? (partner.name_ja || partner.name) : (partner.name || partner.name_ja)) || t('muse.noPartner') }}
                    </span>
                    <span class="block text-[9px] text-fuchsia-300/60">{{ t('muse.partnerCharacter') }}</span>
                  </span>
                </button>
                <!--
                  **The partner can go back to none (the Showrunner, 2026-09-13).**
                  The roster has no "choose nobody" tile, so the clearing action
                  lives here. The ✕ sits right beside the card — placed further away
                  it looks as though a choice cannot be undone.
                -->
                <button
                  v-if="inputs.partner_preset"
                  type="button"
                  class="-ml-1 rounded-full border border-fuchsia-900/40 px-2 py-1.5 text-[11px] text-fuchsia-300/80 hover:border-fuchsia-500/60 hover:text-fuchsia-100 disabled:opacity-40"
                  :disabled="chatLocked"
                  :title="t('muse.noPartner')"
                  @click="pickPartner('')"
                >✕</button>
                <span class="text-[10px] font-medium uppercase tracking-wide text-pink-400/80">NOW</span>
              </div>
              <!--
                主演撮り / スタジオ撮り。**開く前に選ぶ**（classic の setup と
                同じ作り）。開いたあとは替えられない —— 班は途中から呼べない。
              -->
              <div class="mt-2 grid grid-cols-2 gap-2">
                <button
                  v-for="m in [{ id: 'solo', k: 'modeSolo' }, { id: 'studio', k: 'modeStudio' }]"
                  :key="m.id"
                  type="button"
                  class="rounded-lg border p-2 text-left transition-colors disabled:opacity-50"
                  :class="shootMode === m.id
                    ? 'border-amber-500/60 bg-amber-950/25'
                    : 'border-white/10 hover:border-white/25'"
                  :disabled="chatLocked || opened"
                  @click="shootMode = m.id"
                >
                  <span class="block text-[11px] text-gray-200">{{ t(`muse.${m.k}`) }}</span>
                  <span class="mt-0.5 block text-[10px] leading-snug text-gray-500">
                    {{ t(`muse.${m.k}Hint`) }}
                  </span>
                </button>
              </div>
              <!--
                **The crew is chosen on the main screen (the Showrunner,
                2026-09-13).** "Make calling up a preset easy to change on the
                screen, not in the settings." Buried in the settings pane, the crew
                cannot be decided in the same flow as choosing the shoot.
                **The default is empty** — without a choice, a studio shoot does not
                open.
              -->
              <div v-if="shootMode === 'studio'" class="mt-2">
                <div class="flex flex-wrap items-center gap-1.5">
                  <button
                    v-for="p in crewPresets"
                    :key="p"
                    type="button"
                    class="rounded-full border px-2.5 py-1 text-[11px] transition-colors disabled:opacity-40"
                    :class="(inputs.crew_preset || '') === p
                      ? 'border-amber-500/60 bg-amber-950/40 text-amber-100'
                      : 'border-white/10 text-gray-300 hover:border-white/30'"
                    :disabled="chatLocked || tableOpen"
                    @click="patchInputs({ crew_preset: p })"
                  >{{ t(`muse.crewPresetName.${p}`) }}</button>
                </div>
                <p class="mt-1 text-[10px] leading-snug text-amber-200/60">
                  {{ t('muse.crewPresetNote') }}
                </p>
                <p v-if="!inputs.crew_preset" class="mt-0.5 text-[10px] text-rose-300/80">
                  {{ t('muse.crewPresetNeeded') }}
                </p>
              </div>
              <p v-if="tableOpen" class="mt-1 text-[10px] text-amber-200/70">
                🎬 {{ t('muse.tableOn', { n: crewSeats }) }}
              </p>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <input
                  v-model="themeDraft"
                  type="text"
                  class="min-w-0 flex-1 rounded-md border border-pink-500/20 bg-pink-950/25 px-2 py-1 text-[11px] text-pink-50 outline-none focus:border-pink-500"
                  :placeholder="t('muse.themePlaceholder')"
                  :disabled="chatLocked"
                  @change="patchInputs({ theme: themeDraft.trim() })"
                />
                <button
                  type="button"
                  class="rounded-lg bg-rose-800/80 px-2.5 py-1 text-[11px] font-medium text-rose-50 hover:bg-rose-700 disabled:opacity-40"
                  :disabled="chatLocked || !startReady"
                  :title="startReady ? '' : t('muse.pickBeforeStart')"
                  @click="openSession"
                >{{ opened ? t('muse.reopen') : t('muse.open') }}</button>
              </div>
              <p v-if="!startReady" class="mt-1 text-[10px] text-rose-300/80">
                {{ missingBeforeStart }}
                <span class="text-gray-500">—— {{ t('muse.whereToPick') }}</span>
              </p>
              <p class="mt-1 text-[11px] leading-snug text-pink-100/80">
                {{ craft.now || t('muse.nowEmpty') }}
              </p>
              <p class="mt-0.5 text-[10px] text-gray-500">{{ t('muse.nowAuthority') }}</p>
              <div
                v-if="ledger.atmosphere || ledger.look || ledger.lettering"
                class="mt-1.5 flex flex-wrap gap-1.5"
              >
                <span
                  v-if="ledger.atmosphere"
                  class="inline-flex max-w-full items-center gap-1 rounded-full border border-violet-700/50 bg-violet-950/50 px-2 py-0.5 text-[10px] text-violet-100"
                  :title="t('muse.fields.atmosphere')"
                >
                  <span class="shrink-0 opacity-70">🌫</span>
                  <span class="truncate">{{ ledger.atmosphere }}</span>
                </span>
                <span
                  v-if="ledger.look"
                  class="inline-flex max-w-full items-center gap-1 rounded-full border border-fuchsia-700/50 bg-fuchsia-950/50 px-2 py-0.5 text-[10px] text-fuchsia-100"
                  :title="t('muse.fields.look')"
                >
                  <span class="shrink-0 opacity-70">🎨</span>
                  <span class="truncate">{{ ledger.look }}</span>
                </span>
                <span
                  v-if="ledger.lettering"
                  class="inline-flex max-w-full items-center gap-1 rounded-full border border-sky-700/50 bg-sky-950/50 px-2 py-0.5 text-[10px] text-sky-100"
                  :title="t('muse.fields.lettering')"
                >
                  <span class="shrink-0 opacity-70">🔤</span>
                  <span class="truncate">{{ ledger.lettering }}</span>
                </span>
              </div>
              <p v-if="bond.last" class="mt-1 text-[10px] text-rose-200/70">
                {{ t('muse.bondHint') }}: {{ bond.last }}
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
                    v-if="faceForRow(row)"
                    :src="faceForRow(row)"
                    alt=""
                    class="h-7 w-7 shrink-0 rounded-full object-cover border border-pink-100 shadow-md ring-2 ring-pink-400/80"
                  />
                  <template v-if="isSeatRow(row)">
                    🎬 {{ row.name }}
                    <span
                      v-for="chip in rowChips(row)"
                      :key="chip.key"
                      class="inline-flex items-center gap-0.5 rounded-full border border-amber-600/40 bg-amber-950/40 px-1.5 py-0.5 text-[10px] leading-none not-italic text-amber-100"
                      :title="chip.key"
                    ><span aria-hidden="true">{{ chip.icon }}</span><span>{{ chip.label }}</span></span>
                  </template>
                  <template v-else-if="isHeckleRow(row)">〃 {{ row.name }}</template>
                  <template v-else-if="isBanterRow(row)">💭 {{ t('muse.asideTitle') }} · {{ row.name }}</template>
                  <template v-else-if="isSayRow(row)">
                    🌸 {{ row.name || waitName }}
                    <span
                      v-if="turnIcon(row)"
                      class="opacity-70"
                      :title="turnIconTitle(row, t)"
                    >{{ turnIcon(row) }}</span>
                  </template>
                  <template v-else-if="row.meta?.speaker">🌸 {{ row.name }} · {{ row.meta.speaker }}</template>
                  <template v-else-if="row.meta?.kind === 'pitch'">
                    💡 {{ t('muse.pitchTitle') }}
                  </template>
                  <template v-else>{{ rowKindLabel(row, t) }}</template>
                  <span
                    v-if="isStruckRow(row)"
                    class="rounded-full border border-amber-700/50 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-200"
                  >{{ t('muse.struck') }}</span>
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
                  🎬 {{ t('muse.director') }}
                  <span
                    v-if="isStruckRow(row)"
                    class="rounded-full border border-amber-700/50 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-200"
                  >{{ t('muse.struck') }}</span>
                </span>
                <!--
                  **Proposals are shown as something you can press (2026-09-10).**
                  The Showrunner: "proposals from Muse are fine, but make them a
                  little clearer". They used to be the bare string `A ｜ B`, with the
                  pressable buttons sitting separately above the input. The row
                  itself becomes the proposal card. The row above the input stays —
                  it catches proposals once they have scrolled up.
                -->
                <div
                  v-if="row.meta?.kind === 'pitch' && (row.meta?.options || []).length"
                  class="max-w-[90%] rounded-2xl rounded-tl-sm border border-violet-500/40 bg-violet-950/30 px-3 py-2.5 shadow-sm"
                >
                  <p class="mb-1.5 text-[10px] text-violet-200/70">{{ t('muse.pitchHint') }}</p>
                  <div class="flex flex-wrap gap-1.5">
                    <button
                      v-for="opt in row.meta.options"
                      :key="`${i}-${opt}`"
                      type="button"
                      class="rounded-full border border-violet-600/60 bg-violet-900/50 px-3 py-1.5 text-[12px] text-violet-50 hover:bg-violet-800/60 disabled:opacity-40"
                      :disabled="chatLocked"
                      @click="sendPitch(opt)"
                    >「{{ opt }}」</button>
                  </div>
                </div>
                <div
                  v-else-if="!isChangeRow(row) || row.text"
                  class="max-w-[90%] whitespace-pre-wrap leading-relaxed shadow-sm"
                  :class="[
                    row.role === 'user'
                      ? (isStruckRow(row)
                        ? 'rounded-2xl rounded-tr-sm border border-gray-700/50 bg-gray-900/50 px-3.5 py-2 text-[12px] text-gray-500 line-through decoration-amber-700/80'
                        : 'rounded-2xl rounded-tr-sm border border-emerald-500/40 bg-emerald-950/50 px-3.5 py-2 text-[12px] text-emerald-100')
                      : isSeatRow(row)
                        ? 'rounded-lg border border-amber-800/30 bg-amber-950/15 px-3 py-1.5 text-[11px] text-amber-50/90'
                      : isHeckleRow(row)
                        ? 'ml-4 rounded-lg border border-dashed border-slate-600/40 bg-slate-900/40 px-2.5 py-1 text-[10px] italic text-gray-400'
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
                <span class="refine-dots text-[11px]" :aria-label="t('muse.thinking')">
                  <span>.</span><span>.</span><span>.</span>
                </span>
                <span v-if="elapsed" class="font-mono text-[10px] text-pink-400/55">{{ clock(elapsed) }}</span>
              </div>
              <!--
                流れてきている途中の一行。確定した行が届いたら消える。
                入力を読む時間（実測 18秒）は無言のままだが、そこから先は
                文字が出る。見た目は確定した台詞と同じにして、途切れて
                見えないようにする。
              -->
              <!--
                このターンで流し終えた席。見た目は確定した行と同じにして、
                POST が返って本物に差し替わったとき動いて見えないようにする。
              -->
              <div
                v-for="(done, di) in liveDone"
                :key="`done-${di}`"
                class="flex flex-col items-start gap-1"
              >
                <span
                  class="flex items-center gap-1.5 px-0.5 text-[10px] font-medium"
                  :class="done.lead ? 'text-pink-300/80' : 'text-amber-200/80'"
                >
                  <img
                    v-if="done.face"
                    :src="done.face"
                    alt=""
                    class="h-7 w-7 shrink-0 rounded-full object-cover border border-pink-100 shadow-md ring-2 ring-pink-400/80"
                  />
                  {{ done.lead ? '🌸' : '🎬' }} {{ done.name }}</span>
                <div
                  class="max-w-[90%] whitespace-pre-wrap shadow-sm"
                  :class="done.lead
                    ? 'rounded-2xl rounded-tl-sm border border-pink-500/30 bg-slate-900/80 px-3.5 py-2 text-[12px] leading-relaxed text-pink-50'
                    : 'rounded-lg border border-amber-800/30 bg-amber-950/15 px-3 py-1.5 text-[11px] leading-relaxed text-amber-50/90'"
                >{{ done.text }}</div>
              </div>
              <div v-if="liveSay" class="flex flex-col items-start gap-1">
                <span
                  class="flex items-center gap-1.5 px-0.5 text-[10px] font-medium"
                  :class="liveIsLead ? 'text-pink-300/80' : 'text-amber-200/80'"
                >
                  <img
                    v-if="liveIsLead && leadFace"
                    :src="leadFace"
                    alt=""
                    class="h-7 w-7 shrink-0 rounded-full object-cover border border-pink-100 shadow-md ring-2 ring-pink-400/80"
                  />
                  {{ liveIsLead ? '🌸' : '🎬' }} {{ liveName || waitName }}
                </span>
                <div
                  class="max-w-[90%] whitespace-pre-wrap shadow-sm"
                  :class="liveIsLead
                    ? 'rounded-2xl rounded-tl-sm border border-pink-500/30 bg-slate-900/80 px-3.5 py-2 text-[12px] leading-relaxed text-pink-50'
                    : 'rounded-lg border border-amber-800/30 bg-amber-950/15 px-3 py-1.5 text-[11px] leading-relaxed text-amber-50/90'"
                >{{ liveText }}<span class="refine-caret">▌</span></div>
              </div>
              <p v-if="!chat.length && !waitingOnModel" class="text-xs text-gray-500">{{ t('muse.chatHint') }}</p>
            </div>

            <form class="flex flex-col gap-2 border-t border-pink-500/15 p-3" @submit.prevent="sendChat">
              <!--
                **No extra guessed suggestions above the input (the Showrunner,
                2026-09-12).** "The extra suggestions circled in red in the attached
                image do not need to be shown." Proposals (PITCH) already appear as
                pressable cards inside the conversation, so the catcher above the
                input was a duplicate. The learned tastes are not listed here either
                — they are not something to stop shooting and read.
              -->
              <div class="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="rounded-lg border border-pink-500/40 bg-pink-950/40 px-2.5 py-1.5 text-[10px] font-medium text-pink-100 hover:bg-pink-900/50 disabled:opacity-40"
                  :disabled="chatLocked || comfyOffline || !craft.prompt"
                  :title="craft.prompt ? t('muse.boardHint') : t('muse.prepFirst')"
                  @click="runStage('board')"
                >{{ t('muse.board') }}</button>
                <button
                  type="button"
                  class="rounded-lg border border-amber-500/50 bg-amber-950/40 px-2.5 py-1.5 text-[10px] font-medium text-amber-200 hover:bg-amber-900/60 disabled:opacity-40"
                  :disabled="chatLocked || comfyOffline || !craft.prompt || !boardReady"
                  :title="boardReady ? t('muse.approveTitle') : t('muse.approveNeedsBoard')"
                  @click="runStage('approve')"
                >{{ t('muse.approve') }}</button>
                <button
                  type="button"
                  class="ml-auto rounded-lg border border-rose-500/50 bg-rose-950/40 px-2.5 py-1.5 text-[10px] font-medium text-rose-200 hover:bg-rose-900/60 disabled:opacity-40"
                  :disabled="chatLocked || !shootImages.length || diaryWriting"
                  :title="shootImages.length ? '' : t('muse.finishNeedsShoot')"
                  @click="finishSession"
                >{{ diaryWriting ? t('muse.diaryWriting') : diaryDone ? t('muse.diaryDone') : t('muse.finish') }}</button>
                <button
                  v-if="diaryDone && inputs.character_id"
                  type="button"
                  class="rounded-lg border border-pink-500/50 bg-pink-950/40 px-2.5 py-1.5 text-[10px] text-pink-200 hover:bg-pink-900/60"
                  @click="showDiary = true"
                >{{ t('muse.openDiary') }}</button>
              </div>
              <p v-if="craft.prompt && !boardReady" class="text-[10px] text-gray-500">
                {{ t('muse.approveNeedsBoard') }}
              </p>
              <!--
                **A three-line input. Enter is a plain newline (the Showrunner,
                2026-09-13).** "Please send with the send button — it is a problem
                for Japanese users." Enter is what confirms a kana-kanji conversion,
                so binding Enter to send makes it **fly off mid-conversion**. There
                is one way to send: the button. Grab the edge to make it taller
                (`resize-y`).
              -->
              <div class="flex items-end gap-2">
                <textarea
                  v-model="chatInput"
                  rows="3"
                  class="min-w-0 flex-1 resize-y overflow-y-auto rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm leading-relaxed outline-none focus:border-pink-500"
                  :placeholder="t('muse.chatPlaceholder')"
                  :disabled="chatLocked || !session"
                ></textarea>
                <button
                  type="submit"
                  class="rounded-lg bg-pink-600 px-3 py-2 text-sm font-medium text-white hover:bg-pink-500 disabled:opacity-40"
                  :disabled="chatLocked || !chatInput.trim()"
                >{{ t('muse.send') }}</button>
              </div>
              <p class="text-[10px] text-gray-500">
                {{ statusLabel }}
                <span v-if="elapsed"> · {{ t('muse.elapsed', { s: clock(elapsed) }) }}</span>
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
                :title="t('muse.zoomImage')"
                @click="openLightbox(preview)"
              />
              <div
                v-else
                class="flex aspect-[3/4] max-h-[42vh] w-full animate-pulse items-center justify-center rounded-lg border border-pink-500/20 bg-black/40 text-[11px] text-gray-500"
              >
                {{ job?.progress_text || t('muse.renderWait') }}
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
              <h4 class="text-[11px] font-medium text-amber-200/90">{{ t('muse.imagesBoardTitle') }}</h4>
              <p class="text-[10px] text-gray-500">{{ t('muse.imagesBoardAsk') }}</p>
              <div class="grid grid-cols-2 gap-2">
                <figure
                  v-for="img in boardImages"
                  :key="img.image_id || img.sha256 || img"
                  class="cursor-zoom-in overflow-hidden rounded border border-white/10"
                  :title="t('muse.zoomImage')"
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
              <h4 class="text-[11px] font-medium text-amber-200/90">{{ t('muse.imagesShootTitle') }}</h4>
              <div class="grid grid-cols-2 gap-2">
                <figure
                  v-for="img in shootImages"
                  :key="img.image_id || img.sha256 || img"
                  class="cursor-zoom-in overflow-hidden rounded border border-pink-500/40"
                  :title="t('muse.zoomImage')"
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
              <div class="mb-2 text-xs font-medium text-pink-200/90">{{ t('muse.ledger') }}</div>
              <p class="mb-2 text-[10px] text-gray-500">{{ t('muse.ledgerStickyHint') }}</p>
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
                <div class="mb-1 text-[10px] uppercase tracking-wide text-amber-200/70">{{ t('muse.standing') }}</div>
                <ul class="space-y-0.5 text-[11px] text-amber-100/80">
                  <li v-for="(s, i) in standing" :key="i">· {{ s }}</li>
                </ul>
              </div>
              <div v-if="banned.length" class="mt-2 border-t border-pink-500/15 pt-2">
                <div class="mb-1 text-[10px] uppercase tracking-wide text-red-200/70">{{ t('muse.banned') }}</div>
                <div class="flex flex-wrap gap-1">
                  <button
                    v-for="tag in banned"
                    :key="tag"
                    type="button"
                    class="rounded-full border border-red-800/50 bg-red-950/40 px-2 py-0.5 text-[10px] text-red-100 hover:bg-red-900/50 disabled:opacity-40"
                    :disabled="chatLocked"
                    :title="t('muse.restoreBanned')"
                    @click="restoreBanned(tag)"
                  >✕ {{ tag }}</button>
                </div>
              </div>
              <div class="mt-2 flex flex-wrap gap-1 border-t border-pink-500/15 pt-2">
                <span class="w-full text-[10px] text-gray-500">{{ t('muse.restate') }}</span>
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
                >{{ t(`muse.fields.${f}`) }}</button>
              </div>
            </div>

            <div class="rounded-xl border border-pink-500/20 bg-gray-950/60 p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-pink-200/90">{{ t('muse.options') }}</span>
                <button
                  type="button"
                  class="text-[11px] text-pink-300/80 hover:text-pink-200 disabled:opacity-40"
                  :disabled="chatLocked"
                  @click="rebuild"
                >{{ t('muse.rebuild') }}</button>
              </div>
              
              <label class="flex items-start gap-2 text-[11px] text-gray-300">
                <input
                  type="checkbox"
                  class="mt-0.5"
                  :checked="!!inputs.enhance_quality"
                  :disabled="busy"
                  @change="patchInputs({ enhance_quality: $event.target.checked })"
                />
                <span>
                  <span class="block text-gray-100">{{ t('muse.enhanceQuality') }}</span>
                  <span class="text-gray-500">{{ t('muse.enhanceQualityHint') }}</span>
                </span>
              </label>
              <p v-if="craft.quality_tags" class="text-[10px] text-sky-300/80">
                {{ t('muse.qualityTags') }}: {{ craft.quality_tags }}
              </p>
            </div>

            <div class="rounded-xl border border-pink-500/20 bg-gray-950/60 p-3">
              <div class="mb-1 text-xs font-medium text-pink-200/90">{{ t('muse.prompt') }}</div>
              <pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-gray-300">{{ craft.prompt || '—' }}</pre>
              <p v-if="craft.support_tags" class="mt-2 text-[10px] text-gray-500">
                support: {{ craft.support_tags }}
              </p>
              <!--
                会話のターンでは散文とタグの組み上げを撮る時まで待つ。台帳と
                NOW 行は毎ターン動くので、正本が古く見えることはない。
              -->
              <p v-if="craftStale" class="mt-2 text-[10px] text-amber-200/60">
                {{ t('muse.craftStale') }}
              </p>
            </div>

            <div v-if="showSettings" class="space-y-2 rounded-xl border border-gray-800 bg-gray-950 p-3 text-xs">
              <!--
                **Say where each thing is decided (the Showrunner, 2026-09-13).**
                "I cannot tell where to call up the llm and the image model." Without
                knowing there are two levels, they get chosen again here every
                time.
              -->
              <p class="rounded-lg border border-pink-500/20 bg-pink-950/20 p-2 leading-snug text-[10px] text-pink-100/80">
                {{ t('muse.whereToSetThisShoot') }}<br>
                {{ t('muse.whereToSetAlways') }}
              </p>
              <label class="block">
                <span class="mb-1 block text-gray-500">{{ t('muse.workflow') }}</span>
                <select
                  class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                  :value="inputs.workflow || ''"
                  @change="patchInputs({ workflow: $event.target.value })"
                >
                  <!-- **Show the unchosen state.** No default is applied, so empty
                       is a legitimate state. -->
                  <option value="">{{ t('muse.needWorkflow') }}</option>
                  <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
                </select>
              </label>
              <!--
                **Which family, and what it asks for (2026-09-20).** The numbers
                come from the backend's table, never from here — change krea2's
                4/8 in `muse/family.py` and this line follows.
              -->
              <div v-if="inputs.workflow && familyRow" class="flex flex-wrap items-center gap-1.5 text-[10px]">
                <span class="rounded-full border border-cyan-500/40 bg-cyan-950/30 px-2 py-0.5 text-cyan-200">
                  {{ familyRow.label }}
                </span>
                <span class="text-gray-500">{{ draftSteps }} / {{ finalSteps }} steps</span>
                <span v-if="familyRow.draft_cfg === null" class="text-gray-500">
                  {{ t('muse.familyCfgFromWorkflow') }}
                </span>
                <span v-if="familyRow.canvas === 'workflow'" class="text-gray-500">
                  {{ t('muse.familySizeFromWorkflow') }}<template v-if="workflowCanvas">
                    （{{ workflowCanvas.width }}×{{ workflowCanvas.height }}）</template>
                </span>
                <span v-if="!familyRow.negative" class="text-gray-500">
                  {{ t('muse.familyNoNegative') }}
                </span>
              </div>
              <!--
                **The steps stay his to change (2026-09-20).** "4/8 as the
                default, and let the user change it after that." A field left at
                the shipped default follows the family; a number typed here wins.
              -->
              <div v-if="inputs.workflow" class="grid grid-cols-2 gap-2">
                <label class="block">
                  <span class="mb-1 block text-gray-500">{{ t('muse.draftSteps') }}</span>
                  <input
                    type="number" min="1" max="60"
                    class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                    :value="draftSteps"
                    @change="patchInputs({ draft_steps: Number($event.target.value) })"
                  >
                </label>
                <label class="block">
                  <span class="mb-1 block text-gray-500">{{ t('muse.finalSteps') }}</span>
                  <input
                    type="number" min="1" max="100"
                    class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                    :value="finalSteps"
                    @change="patchInputs({ final_steps: Number($event.target.value) })"
                  >
                </label>
                <!--
                  **Both ways (2026-09-20).** A family that owns no canvas leaves
                  the fields empty and the workflow keeps the resolution it was
                  saved at; type a number and that wins for every render after.
                -->
                <label class="block">
                  <span class="mb-1 block text-gray-500">{{ t('muse.widthLabel') }}</span>
                  <input
                    type="number" min="256" max="2048" step="8"
                    class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                    :value="canvasValue('width')"
                    :placeholder="String(workflowCanvas?.width || t('muse.familySizeFromWorkflow'))"
                    @change="patchInputs({ width: Number($event.target.value) })"
                  >
                </label>
                <label class="block">
                  <span class="mb-1 block text-gray-500">{{ t('muse.heightLabel') }}</span>
                  <input
                    type="number" min="256" max="2048" step="8"
                    class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                    :value="canvasValue('height')"
                    :placeholder="String(workflowCanvas?.height || t('muse.familySizeFromWorkflow'))"
                    @change="patchInputs({ height: Number($event.target.value) })"
                  >
                </label>
              </div>
              <label class="block">
                <span class="mb-1 block text-gray-500">{{ t('muse.model') }}</span>
                <select
                  class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                  :value="inputs.model || ''"
                  @change="patchInputs({ model: $event.target.value })"
                >
                  <option value="">{{ t('muse.needModel') }}</option>
                  <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
                </select>
              </label>
              <!--
                撮影班の選択は**主画面へ移した**（2026-09-13）。ここには案内だけ
                残す —— 設定を開いて探す動線をもう一度作らないため。
              -->
              <p class="text-[10px] leading-snug text-gray-500">
                {{ t('muse.crewPreset') }}:
                <span class="text-gray-300">{{ inputs.crew_preset || t('muse.crewPresetNeeded') }}</span>
                <br>{{ t('muse.crewPresetMoved') }}
              </p>
              <label class="block">
                <span class="mb-1 block text-gray-500">{{ t('muse.banter') }}</span>
                <select
                  class="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1.5"
                  :value="inputs.banter_mode || 'light'"
                  @change="patchInputs({ banter_mode: $event.target.value })"
                >
                  <option value="off">{{ t('muse.banterOff') }}</option>
                  <option value="light">{{ t('muse.banterLight') }}</option>
                  <option value="full">{{ t('muse.banterFull') }}</option>
                </select>
                <span class="mt-1 block text-[10px] text-gray-500">{{ t('muse.banterHint') }}</span>
              </label>
            </div>

            <details v-if="museDebug" class="rounded-xl border border-amber-900/40 bg-amber-950/20 p-3 text-[10px] text-amber-100/90" open>
              <summary class="cursor-pointer text-amber-200">{{ t('muse.debugTitle') }}</summary>
              <p class="mt-1 mb-2 text-amber-100/50">{{ t('muse.debugHint') }}</p>

              <div v-if="pipelineStages.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('muse.pipelineTitle') }}</div>
                <p class="mb-1.5 text-amber-100/50">{{ t('muse.pipelineHint') }}</p>
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
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('muse.rewriteLog') }}</div>
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
              <p v-else class="mb-3 text-amber-100/50">{{ t('muse.debugEmpty') }}</p>

              <div v-if="turnTrace.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('muse.turnTrace') }}</div>
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
                    <div v-if="(row.quality_tags || []).length" class="text-sky-300/80">
                      quality: {{ (row.quality_tags || []).join(', ') }}
                    </div>
                  </li>
                </ul>
              </div>

              <div v-if="stageMs.length" class="mb-3">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('muse.stageMs') }}</div>
                <ul class="space-y-0.5">
                  <li v-for="(s, i) in stageMs" :key="`${s.at}-${i}`" class="text-amber-100/70">
                    <span class="text-amber-300/80">{{ ((s.ms || 0) / 1000).toFixed(1) }}s</span>
                    {{ ' ' }}{{ s.stage }}
                    <span class="text-amber-100/40">{{ rewriteWhen(s.at) }}</span>
                  </li>
                </ul>
              </div>

              <div v-if="refineLog.length">
                <div class="mb-1 font-semibold text-amber-200/90">{{ t('muse.refineLog') }}</div>
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
                      v-if="row.kind === 'actress_expression'"
                      class="pl-2 text-fuchsia-200/85"
                    >
                      face:
                      <span v-if="row.accepted" class="text-emerald-200/90">✓ {{ row.accepted }}</span>
                      <span v-else-if="row.dropped" class="text-rose-200/80">✗ {{ row.dropped }}</span>
                      <span v-if="row.director_named_face" class="text-amber-100/50"> · director face</span>
                      <span v-else-if="row.scene_moved" class="text-amber-100/50"> · scene moved</span>
                    </div>
                  </li>
                </ul>
              </div>
            </details>
          </section>
        </div>
      </div>
    </div>
  </Teleport>

  <!--
    **The Muse roster.** The same `CharacterGallery` classic used to open (the
    Showrunner, 2026-09-12: "show the Muse roster rather than a dropdown"). It owns
    the workflow picker too, so that is wired straight through.
  -->
  <CharacterGallery
    :show="showPicker"
    layer-class="z-[var(--z-panel-muse-child)]"
    :selected-id="inputs.character_id || ''"
    :workflows="workflows"
    :workflow="inputs.workflow || ''"
    :get-jobs-map="props.getJobsMap"
    @pick="pickCharacter"
    @close="showPicker = false"
    @toast="emit('toast', $event)"
    @update:workflow="patchInputs({ workflow: $event })"
  />

  <CharacterGallery
    :show="showPartnerPicker"
    layer-class="z-[var(--z-panel-muse-child)]"
    :selected-id="inputs.partner_preset || ''"
    :workflows="workflows"
    :workflow="inputs.workflow || ''"
    :get-jobs-map="props.getJobsMap"
    @pick="pickPartner"
    @close="showPartnerPicker = false"
    @toast="emit('toast', $event)"
    @update:workflow="patchInputs({ workflow: $event })"
  />

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
      :aria-label="t('muse.zoomImage')"
      tabindex="0"
      @mousedown.self="closeLightbox"
      @keydown="onLightboxKey"
    >
      <button
        type="button"
        class="absolute right-3 top-3 rounded-full bg-black/50 px-3 py-1.5 text-lg text-gray-200 hover:bg-black/70"
        :title="t('muse.close')"
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
/* The tail of a streaming line, so it reads as still being written. */
.refine-caret {
  animation: refine-caret-blink 1s step-end infinite;
  opacity: 0.6;
}
@keyframes refine-dot-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
  40% { transform: translateY(-2px); opacity: 1; }
}
@keyframes refine-wait-soft {
  0%, 100% { opacity: 0.7; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.04); }
}
@keyframes refine-caret-blink {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .refine-dots span, .refine-wait-pulse, .refine-caret { animation: none; opacity: 1; }
}
</style>

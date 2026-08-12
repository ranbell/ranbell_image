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
import ActressDiaryModal from './muse/ActressDiaryModal.vue'
import PoseSketch3D from './muse/PoseSketch3D.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, default: () => () => new Map() },
  // Who to shoot with, chosen on the list screen one layer up, before this
  // screen was even shown. Empty means "whatever session is already sitting
  // here" — reopening after ✕ with nothing newly picked.
  initialCharacterId: { type: String, default: '' },
  // Set together with `initialCharacterId` when the Compat Viewer's "start a
  // duet with these two" action opens this panel — casts both in one go and
  // switches straight to duet mode instead of leaving the second seat empty.
  initialPartnerId: { type: String, default: '' },
})
const emit = defineEmits(['update:show', 'toast', 'select-image'])
const { t, locale } = useI18n()

const session = ref(null)
const catalog = ref(null)
const busy = ref(false)
const streamLive = ref(false)
const showPicker = ref(false)
const showPartnerPicker = ref(false)
const showDiary = ref(false)
const showSettings = ref(false)
const showCast = ref(false)
const preview = ref('')
const speaking = ref('')          // muse id currently streaming
const liveSay = ref('')
const scripterStatus = ref('')    // live craft update status for duet
const scripterWhisper = ref('')   // body-line while craft updates (no LLM)
const notebookFlash = ref('')     // which notebook row to pulse
const memoryHintSeen = ref(false)
const chatInput = ref('')
const job = ref(null)
const elapsed = ref(0)
const chatEl = ref(null)
const FRAMINGS = ['auto', 'full_body', 'upper_body', 'face_closeup', 'from_behind']
// trio/quartet ("半分の編成") already exist in crew.PRESETS on the backend and
// were fully translated — they were just never added to this list, so the
// smaller-crew formations were unreachable from the UI.
const PRESETS = [
  'trio', 'quartet', 'standard', 'vivid', 'photoreal', 'flat', 'classic', 'bold', 'calm', 'everyone',
]
// One accent colour per formation, so the picked one reads at a glance instead
// of every preset sharing the same teal highlight. `custom` is a status colour,
// not a pickable preset — see the inert pill next to these buttons.
const PRESET_COLORS = {
  trio: '#38bdf8', quartet: '#a78bfa', standard: '#2dd4bf', vivid: '#fb7185',
  photoreal: '#fbbf24', flat: '#a3e635', classic: '#fb923c', bold: '#e879f9',
  calm: '#22d3ee', everyone: '#34d399', custom: '#f472b6',
}
function presetStyle(p) {
  if (inputs.value.crew_preset !== p) return {}
  const c = PRESET_COLORS[p] || PRESET_COLORS.standard
  return { borderColor: c, color: c, backgroundColor: `${c}22` }
}

let eventSource = null
let pollTimer = null
let startedAt = 0
let loungeToastAt = 0

const inputs = computed(() => session.value?.inputs || {})
const needs = computed(() => session.value?.needs || [])
const character = computed(() => session.value?.character || null)
const partnerCharacter = computed(() => session.value?.partner_character || null)
const partnerPreset = computed(() => inputs.value.partner_preset || '')
const isWMuse = computed(() => isDuet.value && Boolean(partnerPreset.value))
const chat = computed(() => session.value?.chat || [])
// The director's opening instruction (`inputs.theme`) is never itself appended
// to `session.chat` — `start_table`/`start_duet` reset the log to `[]` and only
// Studio's own follow-up gets written. Shown here as the log's first entry so
// it does not vanish once the conversation has moved past it; from there it
// scrolls with everything else like any other message. Kept separate from
// `chat` itself, which several `act`/`canStart` checks read as "has the
// conversation actually started" — the synthetic entry must not trip those
// while the theme is still being typed on the setup screen.
const displayChat = computed(() => {
  const raw = chat.value
  if (!raw.length) return raw
  const theme = String(inputs.value.theme || '').trim()
  if (!theme) return raw
  return [{
    id: '__director_theme__', role: 'user', kind: 'instruction', text: theme,
  }, ...raw]
})
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

// 主演撮り (lead shoot): one or two Muses with the Showrunner, no crew.
// Shot notes compile into craft live from chat; ① is densify polish.
const isDuet = computed(() => session.value?.mode === 'duet')

const canStart = computed(() =>
  !busy.value && needs.value.length === 0 && act.value === 'setup')
const chatLocked = computed(() =>
  busy.value || status.value === 'discussing' || status.value === 'boarding' ||
  status.value === 'shooting')

// Nothing can be photographed before she has written a prompt. Both stages said
// so only by failing after the click; now they say so by not being clickable.
const hasPrompt = computed(() => Boolean(craft.value.prompt))
/** Notebook moved but craft compile refused / skipped — densify needed. */
const craftDirty = computed(() => Boolean(session.value?.craft_dirty))
const notebookAhead = computed(() => {
  const rev = Number(session.value?.notebook?.rev || 0)
  const compiled = Number(session.value?.notebook_rev_compiled || 0)
  return rev > 0 && compiled > 0 && rev > compiled
})
// Her diary is written from the final shoot, so wrapping is only offered once
// there is one — and only once, because each wrap used to queue another diary.
const diaryState = computed(() => session.value?.diary || {})
const diaryWriting = computed(() => diaryState.value.status === 'writing')
const diaryDone = computed(() => diaryState.value.status === 'ok')
const canFinish = computed(() =>
  Boolean(shootImages.value.length) && !diaryWriting.value && !diaryDone.value)
const finishHint = computed(() => {
  if (!shootImages.value.length) return t('muse.finishNeedsShoot')
  if (diaryWriting.value) return t('muse.diaryWriting')
  if (diaryDone.value) return t('muse.diaryDone')
  return t('muse.finishTitle')
})

// Whether a model is busy on our behalf at all — the thinking bubble used to
// wait for the first token, which is precisely the stretch where the model is
// being loaded and the panel looks frozen.
const thinking = computed(() =>
  Boolean(speaking.value) || Boolean(scripterStatus.value) ||
  status.value === 'discussing')

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

// The Lead's face, small, beside the things she says. She is the one person at
// the table the Showrunner actually cast, and in a wall of nicknames her lines
// were indistinguishable from the crew's.
const leadFace = computed(() => thumb(
  character.value?.board?.portrait || character.value?.board?.sheet || '',
))
const partnerFace = computed(() => thumb(
  partnerCharacter.value?.board?.portrait || partnerCharacter.value?.board?.sheet || '',
))

function isLead(m) {
  return String(m?.muse_id || '').split(':')[0] === 'actress'
}

// The backend already resolved identity (identity.parse_duet_speakers +
// service._resolve_duet_turns) — this matches ids, it does not re-guess from
// a name substring. `speakerId` is a `character_id`, not a display name.
function getMessageFace(m, speakerId) {
  if (speakerId) {
    if (speakerId === character.value?.character_id) return leadFace.value
    if (speakerId === partnerCharacter.value?.character_id) return partnerFace.value
    return ''
  }
  return isLead(m) ? leadFace.value : ''
}

// Legacy fallback only: sessions whose chat log was persisted before the
// backend started sending `m.turns` have no structured speaker split at all.
// Never used for anything newly generated — see `m.turns` in the template.
function parseWMuseLines(text) {
  if (!text || typeof text !== 'string') return null
  if (!text.includes(':') && !text.includes('：')) return null
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  const parsed = []
  for (const line of lines) {
    const match = line.match(/^([^:：]+)[:：]\s*(.+)$/)
    if (match) {
      parsed.push({ speaker: match[1].trim(), content: match[2].trim() })
    } else {
      parsed.push({ speaker: null, content: line })
    }
  }
  return parsed.length > 0 ? parsed : null
}

// Who put which tag in. Folded newest-first so a tag reads as "whoever last
// touched it", which is the question being asked when a frame comes back wrong.
const ledger = computed(() => session.value?.ledger || [])
// What the Showrunner refused. Kept out by a filter and handed to the
// sampler as a negative, so this list is the only place the words appear.
const banned = computed(() => session.value?.banned || [])
const tagCredits = computed(() => {
  const live = new Set(
    String(craft.value.tags || '')
      .split(',')
      .map(t => t.trim().replace(/^\(|:[\d.]+\)$|\)$/g, '').toLowerCase().replace(/ /g, '_'))
      .filter(Boolean),
  )
  const seen = new Map()
  for (const row of ledger.value) {
    for (const tag of row.added || []) {
      if (live.has(tag)) seen.set(tag, row.name || row.muse_id)
    }
  }
  return [...seen].map(([tag, who]) => ({ tag, who }))
})
// The shot, in parts. Declaration order is the order it reads on a shot sheet:
// where, when, how lit, what is there, what she has on, what she is doing, her
// face, the lens. The server orders them differently for the prompt itself.
const FACET_NAMES = [
  'place', 'hour', 'light', 'props', 'costume', 'pose', 'expression', 'camera',
]
const facetRows = computed(() => {
  const table = session.value?.facets || {}
  return FACET_NAMES
    .map(name => ({ name, ...(table[name] || {}) }))
    .filter(f => (f.tags || []).length || String(f.nl || '').trim() || f.locked)
})
const NOTEBOOK_KEYS = [
  'atmosphere', 'scene', 'frame', 'wearing', 'beat',
  'wearing_b', 'beat_b', 'vibe', 'open',
]
const notebookRows = computed(() => {
  const nb = session.value?.notebook || {}
  const rows = NOTEBOOK_KEYS
    .map(key => ({ key, text: String(nb[key] || '').trim() }))
    .filter(row => row.text)
  const standing = (nb.standing || []).filter(Boolean)
  if (standing.length) {
    rows.push({ key: 'standing', text: standing.map(s => `- ${s}`).join('\n') })
  }
  return rows
})
/** Muse's open proposal — light chips, never a modal. */
const openProposal = computed(() => String(session.value?.notebook?.open || '').trim())
const taste = computed(() => session.value?.showrunner_taste || {})
const tasteChips = computed(() => {
  const out = []
  const prefers = String(taste.value.prefers || '').trim()
  const avoids = String(taste.value.avoids || '').trim()
  if (prefers) {
    const bit = prefers.split(/[、,/]/)[0].trim().slice(0, 40)
    if (bit) out.push(isJa.value ? `また${bit}？` : `Again: ${bit}?`)
  }
  if (avoids) {
    const bit = avoids.split(/[、,/]/)[0].trim().slice(0, 40)
    if (bit) out.push(isJa.value ? `${bit}は避けて` : `Skip ${bit}`)
  }
  return out.slice(0, 3)
})
const chemistryNotes = computed(() =>
  (session.value?.chemistry_notes || []).map(s => String(s || '').trim()).filter(Boolean).slice(0, 2),
)
const showMemoryExpect = computed(() =>
  Boolean(session.value?.memory_expect_hint) && !memoryHintSeen.value && isDuet.value,
)
/** Live W-Muse split while tokens stream (A:/B: prefixes). */
const liveWTurns = computed(() => {
  const text = liveSay.value || ''
  if (!isWMuse.value || (!text.includes(':') && !text.includes('：'))) return null
  const lines = text.split('\n')
  const parsed = []
  let cur = null
  for (const raw of lines) {
    const m = raw.match(/^\s*([ABab])\s*[:：]\s*(.*)$/)
    if (m) {
      cur = { speaker: m[1].toUpperCase() === 'B'
        ? (partnerCharacter.value?.name_ja || partnerCharacter.value?.name || 'B')
        : (character.value?.name_ja || character.value?.name || 'A'),
        speakerId: m[1].toUpperCase() === 'B'
          ? partnerCharacter.value?.character_id
          : character.value?.character_id,
        content: m[2] || '' }
      parsed.push(cur)
    } else if (cur) {
      cur.content = `${cur.content}\n${raw}`.trim()
    }
  }
  return parsed.length ? parsed : null
})
function dismissMemoryHint() { memoryHintSeen.value = true }
// Two parts of the shot disagreeing, where one of them is pinned. The pinned
// one wins; this is so the panel can say why the other did not take.
const facetConflicts = computed(() => session.value?.facet_conflicts || [])

async function toggleFacetLock(facet) {
  const was = !!(session.value?.facets?.[facet]?.locked)
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/facets/${facet}`,
      { method: 'PATCH', body: JSON.stringify({ locked: !was }) },
    )
  } catch (err) { fail(err) }
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
    await enterWithCharacter(props.initialCharacterId, props.initialPartnerId)
  } catch (err) { fail(err) }
})
onBeforeUnmount(closeStream)

// The list screen one layer up already asked "who with" — this only decides
// whether that answer can land on the session already sitting here, or needs
// a clean one instead. Reusing a session that has already spoken is how one
// girl's dialogue used to end up captioned with another's name; an untouched
// setup screen has nothing to lose, so it is fine to relabel silently.
async function enterWithCharacter(characterId, partnerId = '') {
  const requestedNew = Boolean(characterId) && characterId !== inputs.value.character_id
  const switching = Boolean(session.value) && requestedNew
  if (switching) {
    const untouched = !chat.value.length && status.value === 'setup'
    if (!untouched) {
      if (!window.confirm(t('muse.resetConfirm'))) {
        emit('update:show', false)
        return
      }
      discardSession()
    }
  }
  if (!session.value) {
    await startSession()
  } else {
    connectStream(session.value.session_id)
  }
  if (requestedNew) await pickCharacter(characterId)
  // Both seats were chosen together (Compat Viewer's duet-pair action) — cast
  // her opposite number and switch to duet mode in the same breath, rather
  // than leaving the second seat for the Showrunner to fill by hand.
  if (requestedNew && partnerId) {
    await setMode('duet')
    await pickPartnerCharacter(partnerId)
  }
}

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

function discardSession() {
  closeStream()
  session.value = null
}

async function resetSession() {
  if (!window.confirm(t('muse.resetConfirm'))) return
  discardSession()
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
    if (evt.type === 'scripter_working') {
      scripterStatus.value = String(evt.message || '').trim() || t('muse.scripterUpdating')
      scripterWhisper.value = String(evt.whisper || '').trim()
      notebookFlash.value = String(evt.flash || '').trim()
      return
    }
    if (evt.type === 'scripter_done') {
      scripterStatus.value = ''
      scripterWhisper.value = ''
      notebookFlash.value = ''
      await refresh()
      return
    }
    if (evt.type === 'muse_speaking') {
      scripterStatus.value = ''
      scripterWhisper.value = ''
      notebookFlash.value = ''
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
    if (evt.type === 'diary_status') {
      await refresh()
      if (evt.status === 'ok') {
        emit('toast', { msg: t('muse.diaryReady'), type: 'info' })
      } else if (evt.status === 'failed') {
        // Silence was the old behaviour, and the Showrunner waited for an entry
        // that was never coming.
        emit('toast', { msg: t('muse.diaryFailed'), type: 'error' })
      }
      return
    }
    if (evt.type === 'lounge_status' && ['shared', 'reacted', 'pitch', 'habit'].includes(evt.status)) {
      // Duet wrap can fire several of these in a row — one toast per few seconds.
      const now = Date.now()
      if (!loungeToastAt || now - loungeToastAt > 4000) {
        loungeToastAt = now
        emit('toast', { msg: t('muse.loungeReady'), type: 'info' })
      }
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

// Casting resolves the character server-side and hands it straight back, so the
// card fills in on the click. Patching the id alone left `partner_character`
// empty until she happened to speak, and picking somebody looked like it had
// silently failed.
async function pickPartnerCharacter(id) {
  showPartnerPicker.value = false
  if (!session.value) return
  if (id && inputs.value.character_id === id) {
    emit('toast', { msg: t('muse.partnerMustDiffer'), type: 'error' })
    return
  }
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/partner`, {
      method: 'POST', body: JSON.stringify({ partner_preset: id || '' }),
    })
  } catch (err) { fail(err) }
}

async function clearPartnerCharacter() {
  await pickPartnerCharacter('')
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
    // 主演撮り opens on her, not on a table read, so it is a different door.
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${isDuet.value ? 'duet' : 'table'}`,
      { method: 'POST' })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false }
}

async function setMode(mode) {
  if (!session.value || act.value !== 'setup') return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/inputs`, {
      method: 'PATCH', body: JSON.stringify({ mode }),
    })
  } catch (err) { fail(err) }
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
  } catch (err) { fail(err) } finally { busy.value = false; stopThinking() }
}

// A turn that failed never sends its closing chat_message, so without this the
// dots would keep dancing over a session that has stopped.
function stopThinking() {
  speaking.value = ''
  liveSay.value = ''
}

function quick(cmd) { sendChat(cmd) }

// Prep, test shot and final are buttons on their own endpoints — not words
// typed into chat for a regex to recognise. Typed text is always creative
// direction now; only these buttons move the shoot forward a stage.
async function runStage(path) {
  if (!session.value || chatLocked.value) return
  busy.value = true
  startedAt = Date.now()
  elapsed.value = 0
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${path}`, { method: 'POST' })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false; stopThinking() }
}
const duetPrep = () => runStage('duet/prep')
const testShot = () => runStage('board')
const finalShot = () => runStage('approve')

async function finishSession() {
  if (!session.value || busy.value) return
  if (!window.confirm(t('muse.finishConfirm'))) return
  busy.value = true
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/finish`, {
      method: 'POST'
    })
    emit('toast', { msg: t('muse.finishToast'), type: 'info' })
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
  }
}


async function onChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    await sendChat()
  }
}

</script>

<template>
  <!-- The whole screen — dim backdrop and shell together — rises from the
       bottom edge, as one element under one transform. Splitting the backdrop
       fade from the shell slide would need two nested Transition instances
       staying in lockstep on the way out; sliding both together as a unit
       cannot go out of sync. -->
  <Transition
    enter-active-class="transition-transform duration-300 ease-out motion-reduce:duration-0"
    leave-active-class="transition-transform duration-200 ease-in motion-reduce:duration-0"
    enter-from-class="translate-y-full"
    leave-to-class="translate-y-full"
  >
  <div
    v-if="show"
    class="muse-root fixed inset-0 flex items-stretch justify-center bg-slate-950/80 backdrop-blur-md p-3"
    @mousedown.self="close"
  >
    <div class="sb-shell w-full max-w-[1500px] flex flex-col min-h-0 bg-slate-900/90 border-2 border-pink-500/30 rounded-3xl shadow-2xl overflow-hidden">
      <header class="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-pink-500/20 shrink-0 bg-pink-950/20">
        <div class="min-w-0">
          <h2 class="sb-display text-base text-pink-300 font-bold tracking-wide flex items-center gap-1.5">
            <span>🎬</span> {{ t('muse.title') }}
            <span v-if="isWMuse" class="ml-2 px-2 py-0.5 rounded-full bg-pink-500/30 border border-pink-400/40 text-pink-200 text-[10px] font-medium animate-pulse">
              ✨ {{ t('muse.wMuseMode') }}
            </span>
          </h2>
          <p class="text-[11px] text-pink-400/80 truncate">{{ t('muse.subtitle') }}</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <span class="text-[10px] text-pink-400/70 font-mono">SSE {{ streamLive ? '●' : '○' }}</span>
          <button v-if="!isDuet" class="sb-btn" :disabled="busy"
                  @click="showCast = !showCast">{{ t('muse.cast') }}</button>
          <button class="sb-btn" :disabled="busy" @click="showSettings = !showSettings">{{ t('muse.settings') }}</button>
          <button class="sb-btn" :disabled="busy" @click="resetSession">{{ t('muse.reset') }}</button>
          <button class="sb-icon-btn hover:bg-pink-950/60 rounded-full" :title="t('muse.close')" @click="close">✕</button>
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

            <!-- a room full of people, or just her -->
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="m in [{ id: '', k: 'studio' }, { id: 'duet', k: 'duet' }]"
                :key="m.k" type="button"
                class="rounded-lg border p-3 text-left transition-colors"
                :class="(session?.mode || '') === m.id
                  ? 'border-[var(--sb-amber)]/60 bg-amber-950/20'
                  : 'border-white/10 hover:border-white/25'"
                :disabled="busy"
                @click="setMode(m.id)"
              >
                <span class="block text-sm text-gray-200">{{ t(`muse.mode.${m.k}`) }}</span>
                <span class="mt-0.5 block text-[10px] text-[var(--sb-faint)]">
                  {{ t(`muse.mode.${m.k}Hint`) }}
                </span>
              </button>
            </div>

            <p class="text-[11px] text-[var(--sb-muted)]">
              {{ isDuet ? t('muse.mode.duetLong') : t('muse.studioHint') }}
            </p>

            <label class="block space-y-1">
              <span class="sb-label">{{ t('muse.theme') }}</span>
              <textarea
                class="sb-textarea text-sm" rows="4"
                :placeholder="t('muse.themePlaceholder')"
                :value="inputs.theme"
                @change="patchInputs({ theme: $event.target.value })"
              ></textarea>
            </label>

            <!-- Main Character Picker -->
            <div class="space-y-1">
              <span class="sb-label">{{ isDuet ? t('muse.character') + ' (主演)' : t('muse.character') }}</span>
              <button
                type="button"
                class="w-full flex items-center gap-3 p-3 rounded-lg border border-white/10 hover:border-white/25 text-left bg-black/20"
                @click="showPicker = true"
              >
                <img
                  v-if="character?.board?.portrait || character?.board?.sheet"
                  :src="thumb(character.board.portrait || character.board.sheet)"
                  class="w-16 h-[84px] rounded object-cover shrink-0" alt=""
                />
                <span v-else class="w-16 h-[84px] rounded bg-black/40 shrink-0"></span>
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-semibold text-gray-200 truncate">
                    {{ (isJa ? character?.name_ja : character?.name) || t('muse.noCharacter') }}
                  </span>
                  <span class="block text-[10px] text-[var(--sb-faint)]">{{ t('muse.pickCharacter') }}</span>
                </span>
              </button>
            </div>

            <!-- W-Muse Partner Picker (only in Duet mode) -->
            <div v-if="isDuet" class="space-y-1">
              <div class="flex items-center justify-between">
                <span class="sb-label text-pink-300 font-medium flex items-center gap-1">
                  <span>✨</span> {{ t('muse.partnerCharacter') }}
                </span>
                <button
                  v-if="partnerPreset"
                  type="button"
                  class="text-[10px] text-pink-400 hover:text-pink-200 underline"
                  @click="clearPartnerCharacter"
                >
                  {{ t('muse.noPartner') }}
                </button>
              </div>

              <button
                type="button"
                class="w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all"
                :class="partnerPreset
                  ? 'border-pink-500/50 bg-pink-950/30'
                  : 'border-dashed border-white/20 hover:border-white/40 bg-black/20'"
                @click="showPartnerPicker = true"
              >
                <img
                  v-if="partnerCharacter?.board?.portrait || partnerCharacter?.board?.sheet"
                  :src="thumb(partnerCharacter.board.portrait || partnerCharacter.board.sheet)"
                  class="w-16 h-[84px] rounded object-cover shrink-0 ring-2 ring-pink-500/50" alt=""
                />
                <span v-else class="w-16 h-[84px] rounded bg-pink-950/40 grid place-items-center text-pink-400 shrink-0 text-xl">👥</span>
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-semibold text-pink-100 truncate">
                    {{ (isJa ? partnerCharacter?.name_ja : partnerCharacter?.name) || t('muse.noPartner') }}
                  </span>
                  <span class="block text-[10px] text-pink-300/70">{{ t('muse.pickPartnerCharacter') }}</span>
                </span>
              </button>
            </div>

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

            <!-- no crew to cast when it is just the two of you -->
            <div v-if="!isDuet">
              <span class="sb-label">{{ t('muse.crewPreset') }}</span>
              <div class="flex flex-wrap gap-2 mt-1">
                <button
                  v-for="p in PRESETS" :key="p" type="button" class="sb-btn text-[10px]"
                  :style="presetStyle(p)"
                  @click="setPreset(p)"
                >{{ t(`muse.presets.${p}`) }}</button>
                <span
                  class="sb-btn text-[10px] cursor-default select-none"
                  :style="inputs.crew_preset === 'custom' ? presetStyle('custom') : { opacity: 0.35 }"
                >{{ t('muse.presets.custom') }}</span>
              </div>
            </div>

            <button class="sb-btn w-full py-2" :disabled="!canStart" @click="startTable">
              {{ busy ? '…' : isDuet ? t('muse.cta.duet') : t('muse.cta.table') }}
            </button>
            <p v-if="needs.length" class="text-[11px] text-amber-400">
              {{ needs.map(n => t(`muse.needs.${n}`)).join(' / ') }}
            </p>
          </div>

          <!-- chat -->
          <template v-else>
            <!-- 🌟 Live W-Muse Duet Visualizer Banner -->
            <div v-if="isWMuse" class="shrink-0 px-4 py-2 bg-gradient-to-r from-pink-950/80 via-rose-950/80 to-purple-950/80 border-b border-pink-500/30 flex items-center justify-between shadow-lg">
              <div class="flex items-center gap-2">
                <div class="flex items-center -space-x-2">
                  <img :src="leadFace" class="w-8 h-8 rounded-full object-cover ring-2 ring-pink-400 shadow-md" alt="" />
                  <img :src="partnerFace" class="w-8 h-8 rounded-full object-cover ring-2 ring-purple-400 shadow-md" alt="" />
                </div>
                <div>
                  <span class="block text-[11px] font-bold text-pink-200">
                    {{ (isJa ? character?.name_ja : character?.name) || 'Muse A' }} × {{ (isJa ? partnerCharacter?.name_ja : partnerCharacter?.name) || 'Muse B' }}
                  </span>
                  <span class="block text-[9px] text-pink-300/70">{{ t('muse.wMuseSessionActive') }}</span>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <span class="px-2.5 py-1 rounded-full bg-pink-500/20 border border-pink-400/40 text-pink-300 text-[10px] font-mono flex items-center gap-1 motion-safe:animate-pulse">
                  <span>✨</span> {{ t('muse.chemistryActive') }}
                </span>
                <button type="button" class="sb-btn text-[10px]" @click="showPartnerPicker = true">
                  {{ t('muse.changePartner') }}
                </button>
              </div>
            </div>

            <!-- Casting the second Muse lived in the setup screen only, so once
                 the two-hander had opened there was no way to add or change her. -->
            <div
              v-else-if="isDuet"
              class="shrink-0 px-4 py-1.5 border-b border-white/10 flex items-center justify-between gap-2"
            >
              <span class="text-[10px] text-[var(--sb-faint)]">{{ t('muse.noPartner') }}</span>
              <button type="button" class="sb-btn text-[10px]" @click="showPartnerPicker = true">
                ✨ {{ t('muse.pickPartnerCharacter') }}
              </button>
            </div>

            <div ref="chatEl" class="flex-1 overflow-y-auto p-3 space-y-2">
              <div
                v-for="m in displayChat" :key="m.id"
                class="flex flex-col gap-1 my-1"
                :class="[
                  m.role === 'user' ? 'items-end' : 'items-start',
                  m.kind === 'banter' ? 'pl-4' : '',
                ]"
              >
                <template v-if="m.role !== 'user' && m.role !== 'system' && (m.turns?.length || parseWMuseLines(m.text))">
                  <div class="flex flex-col gap-2 w-full max-w-[90%]">
                    <div
                      v-for="(sub, sIdx) in (m.turns?.length
                        ? m.turns.map(t => ({ speaker: t.speaker_name, speakerId: t.speaker_id, content: t.text }))
                        : parseWMuseLines(m.text))"
                      :key="sIdx"
                      class="flex flex-col items-start gap-1"
                    >
                      <span class="flex items-center gap-1.5 text-[10px] text-pink-300 font-bold">
                        <img
                          v-if="getMessageFace(m, sub.speakerId || sub.speaker)"
                          :src="getMessageFace(m, sub.speakerId || sub.speaker)" alt=""
                          class="w-7 h-7 rounded-full object-cover ring-2 ring-pink-400/80 shadow-md border border-pink-100 shrink-0"
                        />
                        <span>🌸 {{ sub.speaker || m.name }}</span>
                      </span>
                      <div class="rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px] bg-slate-900/90 border border-pink-400/40 text-pink-50 shadow-md whitespace-pre-wrap leading-relaxed">
                        {{ sub.content }}
                      </div>
                    </div>
                  </div>
                </template>

                <template v-else>
                  <span class="flex items-center gap-1.5 text-[10px] text-pink-300/80 font-medium">
                    <img
                      v-if="isLead(m) && leadFace"
                      :src="leadFace" alt=""
                      class="rounded-full object-cover shrink-0 ring-2 ring-pink-400/80 shadow-md border border-pink-100 transition-transform hover:scale-110"
                      :class="isDuet ? 'w-9 h-9' : 'w-6 h-6'"
                    />
                    <template v-if="m.role === 'user'">🎬 {{ t('muse.showrunner') }}</template>
                    <template v-else-if="m.kind === 'banter'">{{ t('muse.secretBanterTitle') }} {{ m.name }}</template>
                    <template v-else>🌸 {{ m.name || 'Studio' }}</template>
                  </span>
                  <div
                    class="max-w-[88%] whitespace-pre-wrap leading-relaxed shadow-sm transition-all"
                    :class="m.role === 'user'
                      ? 'rounded-2xl rounded-tr-xs px-3.5 py-2 text-[12px] bg-emerald-950/50 border border-emerald-500/40 text-emerald-100 shadow-md'
                      : m.role === 'system'
                        ? 'rounded-xl px-3 py-2 text-[11px] bg-slate-900/60 border border-slate-700/40 text-gray-400'
                        : m.kind === 'banter'
                          ? 'rounded-2xl px-3.5 py-2 text-[11px] bg-gradient-to-r from-pink-950/70 to-rose-950/70 border border-pink-400/50 text-pink-200 shadow-lg italic'
                          : 'rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px] bg-slate-900/80 border border-pink-500/30 text-pink-50 shadow-md'"
                  >{{ m.text }}</div>
                </template>
              </div>

              <!-- Shown from the moment a model is working, not from the first
                   token. The model is dropped from VRAM before every render, so
                   the load is paid on every turn — and that whole stretch used
                   to be a blank panel with no sign anything was happening. -->
              <div v-if="scripterStatus && !liveSay" class="flex flex-col items-start gap-1 my-1">
                <span class="text-[10px] text-[var(--sb-teal)] font-bold">
                  {{ scripterStatus }}
                </span>
                <div
                  v-if="scripterWhisper"
                  class="max-w-[88%] rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px]
                         bg-pink-950/30 border border-pink-400/25 text-pink-100/90 italic"
                >
                  {{ scripterWhisper }}
                </div>
                <div class="max-w-[88%] rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px]
                            bg-teal-950/30 border border-teal-400/30 text-teal-100">
                  <span class="dots" :aria-label="scripterStatus">
                    <span>.</span><span>.</span><span>.</span>
                  </span>
                </div>
              </div>
              <div v-if="thinking && (liveSay || speaking || (!scripterStatus && status === 'discussing'))"
                   class="flex flex-col items-start gap-1 my-1">
                <span class="text-[10px] text-pink-300 font-bold flex items-center gap-1">
                  <span>💖</span>
                  {{ museLabel(museById(speaking)) || t('muse.someone') }}
                  {{ t('muse.leadThinking') }}
                  <span v-if="elapsed" class="text-pink-400/70 font-mono">{{ clock(elapsed) }}</span>
                </span>
                <template v-if="liveWTurns?.length">
                  <div
                    v-for="(sub, sIdx) in liveWTurns" :key="'live-'+sIdx"
                    class="flex flex-col items-start gap-1 w-full max-w-[90%]"
                  >
                    <span class="flex items-center gap-1.5 text-[10px] text-pink-300 font-bold">
                      <img
                        v-if="getMessageFace({}, sub.speakerId)"
                        :src="getMessageFace({}, sub.speakerId)" alt=""
                        class="w-7 h-7 rounded-full object-cover ring-2 ring-pink-400/80 shadow-md border border-pink-100 shrink-0"
                      />
                      <span>🌸 {{ sub.speaker }}</span>
                    </span>
                    <div class="rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px] bg-pink-950/40 border border-pink-400/40 text-pink-100 shadow-md whitespace-pre-wrap">
                      {{ sub.content }}<span v-if="sIdx === liveWTurns.length - 1" class="caret">▍</span>
                    </div>
                  </div>
                </template>
                <div
                  v-else
                  class="max-w-[88%] rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px] whitespace-pre-wrap
                            bg-pink-950/40 border border-pink-400/40 text-pink-100 shadow-md"
                >
                  <template v-if="liveSay">
                    {{ liveSay }}<span class="caret">▍</span>
                  </template>
                  <span v-else class="dots" :aria-label="t('muse.leadThinking')">
                    <span>.</span><span>.</span><span>.</span>
                  </span>
                </div>
              </div>

            </div>

            <div class="shrink-0 border-t border-white/10 p-3 space-y-2">
              <div class="flex flex-wrap gap-2">
                <template v-if="isDuet">
                  <button class="sb-btn text-[10px]" :disabled="chatLocked"
                          :title="t('muse.quick.prepTitle')"
                          @click="duetPrep">
                    {{ t('muse.quick.prep') }}
                  </button>
                  <button class="sb-btn text-[10px]" :disabled="chatLocked || !hasPrompt"
                          :title="hasPrompt ? t('muse.quick.testShotTitle') : t('muse.quick.prepFirst')"
                          @click="testShot">
                    {{ t('muse.quick.testShot') }}
                  </button>
                  <button
                    class="sb-btn text-[10px] bg-amber-950/40 hover:bg-amber-900/60 border-amber-500/50 text-amber-200"
                    :disabled="chatLocked || !hasPrompt"
                    :title="hasPrompt ? t('muse.quick.finalTitle') : t('muse.quick.prepFirst')"
                    @click="finalShot"
                  >
                    {{ t('muse.quick.final') }}
                  </button>

                  <template v-if="isWMuse">
                    <button class="sb-btn text-[10px] border-pink-400/50 bg-pink-950/40 text-pink-200" :disabled="chatLocked"
                            @click="quick(t('muse.quick.backToBackPrompt'))">
                      {{ t('muse.quick.backToBack') }}
                    </button>
                    <button class="sb-btn text-[10px] border-pink-400/50 bg-pink-950/40 text-pink-200" :disabled="chatLocked"
                            @click="quick(t('muse.quick.handInHandPrompt'))">
                      {{ t('muse.quick.handInHand') }}
                    </button>
                    <button class="sb-btn text-[10px] border-pink-400/50 bg-pink-950/40 text-pink-200" :disabled="chatLocked"
                            @click="quick(t('muse.quick.secretTalkPrompt'))">
                      {{ t('muse.quick.secretTalk') }}
                    </button>
                  </template>
                </template>

                <template v-else>
                  <button class="sb-btn text-[10px]" :disabled="chatLocked || !hasPrompt"
                          :title="hasPrompt ? t('muse.quick.testShotTitle') : t('muse.quick.tableFirst')"
                          @click="testShot">
                    {{ t('muse.quick.board') }}
                  </button>
                  <button
                    class="sb-btn text-[10px] bg-amber-950/40 hover:bg-amber-900/60 border-amber-500/50 text-amber-200"
                    :disabled="chatLocked || !hasPrompt"
                    :title="hasPrompt ? t('muse.quick.finalTitle') : t('muse.quick.tableFirst')"
                    @click="finalShot"
                  >
                    {{ t('muse.quick.final') }}
                  </button>
                </template>

                <!-- Wrapping is how the diary gets written, so it cannot be a
                     主演撮り privilege: the crewed studio had no way to finish.
                     It needs a finished shoot to write about, and it is offered
                     exactly once — a second press queued a second diary. -->
                <button
                  type="button"
                  class="sb-btn text-[10px] bg-rose-950/40 hover:bg-rose-900/60 border-rose-500/50 text-rose-200 ml-auto"
                  :disabled="chatLocked || !canFinish"
                  :title="finishHint"
                  @click="finishSession"
                >
                  {{ diaryWriting ? t('muse.diaryWritingBtn')
                     : diaryDone ? t('muse.diaryDoneBtn') : t('muse.finishBtn') }}
                </button>
                <button
                  v-if="diaryDone && inputs.character_id"
                  type="button"
                  class="sb-btn text-[10px] bg-pink-950/40 hover:bg-pink-900/60 border-pink-500/50 text-pink-200"
                  @click="showDiary = true"
                >
                  {{ t('muse.openDiary') }}
                </button>
              </div>
              <p
                v-if="showMemoryExpect"
                class="text-[10px] text-[var(--sb-faint)] flex items-start gap-2"
              >
                <span class="flex-1">{{ t('muse.memoryExpectHint') }}</span>
                <button type="button" class="underline shrink-0" @click="dismissMemoryHint">
                  {{ t('muse.memoryExpectDismiss') }}
                </button>
              </p>
              <p
                v-if="chemistryNotes.length && isWMuse"
                class="text-[10px] text-[var(--sb-muted)]"
              >
                {{ t('muse.chemistryHint') }}
                <span class="text-[var(--sb-faint)]"> — {{ chemistryNotes[0] }}</span>
              </p>
              <div
                v-if="tasteChips.length && !chatLocked"
                class="flex flex-wrap items-center gap-1.5 text-[10px]"
              >
                <span class="text-[var(--sb-faint)]">{{ t('muse.tasteChipLabel') }}</span>
                <button
                  v-for="chip in tasteChips" :key="chip"
                  type="button"
                  class="sb-btn text-[10px] px-2 py-0.5"
                  @click="sendChat(chip)"
                >{{ chip }}</button>
              </div>
              <div
                v-if="openProposal && !chatLocked"
                class="flex flex-wrap items-center gap-2 text-[11px] text-[var(--sb-muted)]"
              >
                <span class="text-[var(--sb-amber)]">{{ t('muse.openChipLabel') }}</span>
                <span class="truncate max-w-[14rem] text-[var(--sb-faint)]">{{ openProposal }}</span>
                <button
                  type="button"
                  class="sb-btn text-[10px] px-2 py-0.5"
                  @click="sendChat(t('muse.openChipYes'))"
                >{{ t('muse.openChipYes') }}</button>
                <button
                  type="button"
                  class="sb-btn text-[10px] px-2 py-0.5 opacity-80"
                  @click="sendChat(t('muse.openChipNo'))"
                >{{ t('muse.openChipNo') }}</button>
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
                   status === 'done' ? t('muse.status.done') :
                   status === 'finished' ? t('muse.status.finished') : t('muse.status.chat') }}
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

          <!-- Cute 3D chibi (Three.js) — posing + camera, no Comfy. SVG fallback inside. -->
          <PoseSketch3D
            v-if="isDuet && (craft.tags || notebookRows.length)"
            :tags="String(craft.tags || '')"
            :beat="String(session?.notebook?.beat || '')"
            :beat-b="String(session?.notebook?.beat_b || '')"
            :frame="String(session?.notebook?.frame || '')"
            :duo="isWMuse"
            :flash="Boolean(notebookFlash && ['beat','beat_b','frame'].includes(notebookFlash))"
          />

          <div v-if="boardImages.length" class="space-y-2">
            <h4 class="text-[11px] text-[var(--sb-amber)]">
              {{ isDuet ? t('muse.stillTitle') : t('muse.boardTitle') }}
            </h4>
            <p class="text-[10px] text-[var(--sb-muted)]">
              {{ isDuet ? t('muse.stillAsk') : t('muse.boardAsk') }}
            </p>
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

          <!-- the third memory: not chat, not a facet's tags — a short,
               revised-not-appended record of what has actually been decided.
               Read-only; the LLM writes it, the Showrunner just gets to see it. -->
          <details v-if="session?.digest" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">{{ t('muse.digest') }}</summary>
            <p class="mt-1 mb-1.5 text-[var(--sb-muted)]">{{ t('muse.digestHint') }}</p>
            <p class="whitespace-pre-wrap text-[var(--sb-muted)]">{{ session.digest }}</p>
          </details>

          <!-- Living shot notebook — source of truth for duet craft. -->
          <details v-if="notebookRows.length" open class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">
              {{ t('muse.notebook') }} · {{ notebookRows.length }}
            </summary>
            <p class="mt-1 mb-1.5 text-[var(--sb-muted)]">{{ t('muse.notebookHint') }}</p>
            <ul class="space-y-1.5">
              <li
                v-for="row in notebookRows" :key="row.key"
                class="rounded border border-white/10 px-2 py-1.5 transition-colors duration-500"
                :class="[
                  row.key === 'open' ? 'border-[var(--sb-amber)]/40' : '',
                  notebookFlash === row.key ? 'border-[var(--sb-teal)] bg-teal-950/40' : '',
                ]"
              >
                <div class="font-semibold text-gray-300">
                  {{ t(`muse.notebookNames.${row.key}`) }}
                  <span v-if="row.key === 'open'" class="ml-1 font-normal text-[var(--sb-amber)]">
                    {{ t('muse.notebookOpen') }}
                  </span>
                </div>
                <p class="mt-0.5 whitespace-pre-wrap text-[var(--sb-muted)]">{{ row.text }}</p>
              </li>
            </ul>
          </details>

          <!-- Legacy facet table (older sessions). -->
          <details v-if="facetRows.length && !notebookRows.length" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">
              {{ t('muse.facets') }} · {{ facetRows.length }}
            </summary>
            <p class="mt-1 mb-1.5 text-[var(--sb-muted)]">{{ t('muse.facetsHint') }}</p>
            <ul class="space-y-1.5">
              <li
                v-for="f in facetRows" :key="f.name"
                class="rounded border px-2 py-1.5"
                :class="f.locked
                  ? 'border-[var(--sb-teal)]/50 bg-[var(--sb-teal)]/5'
                  : 'border-white/10'"
              >
                <div class="flex items-center gap-2">
                  <span class="font-semibold text-gray-300">
                    {{ t(`muse.facetNames.${f.name}`) }}
                  </span>
                  <span v-if="facetConflicts.includes(f.name)"
                        class="text-[var(--sb-amber)]" :title="t('muse.facetConflict')">!</span>
                  <span v-if="f.by" class="ml-auto shrink-0 text-[var(--sb-faint)]">
                    {{ t('muse.facetBy', { who: f.by }) }}
                  </span>
                  <button
                    type="button" class="shrink-0 rounded border px-1.5 py-0.5
                           disabled:opacity-40"
                    :class="f.locked
                      ? 'border-[var(--sb-teal)] text-[var(--sb-teal)]'
                      : 'border-white/15 text-gray-400 hover:border-[var(--sb-teal)]'"
                    :disabled="chatLocked"
                    :title="f.locked ? t('muse.facetLocked') : t('muse.facetLock')"
                    @click="toggleFacetLock(f.name)"
                  >{{ f.locked ? '🔒' : '🔓' }}</button>
                </div>
                <p v-if="(f.tags || []).length"
                   class="mt-0.5 font-mono text-gray-400 break-all">
                  {{ (f.tags || []).join(', ') }}
                </p>
                <p v-if="f.nl" class="mt-0.5 text-[var(--sb-muted)]">{{ f.nl }}</p>
                <p v-else class="mt-0.5 italic text-[var(--sb-faint)]">
                  {{ t('muse.facetEmpty') }}
                </p>
              </li>
            </ul>
          </details>

          <p
            v-if="craftDirty || notebookAhead"
            class="text-[10px] text-[var(--sb-amber)] leading-relaxed"
          >
            {{ t('muse.craftDirtyHint') }}
          </p>
          <details v-if="craft.prompt" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">
              {{ t('muse.craft') }}
              <span v-if="craftDirty" class="ml-1 text-[var(--sb-amber)]">· {{ t('muse.craftDirtyBadge') }}</span>
            </summary>
            <p class="whitespace-pre-wrap font-mono mt-1 text-gray-400">{{ craft.prompt }}</p>
          </details>

          <!-- who put which tag in -->
          <details v-if="tagCredits.length" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">
              {{ t('muse.ledger') }} · {{ tagCredits.length }}
            </summary>
            <p class="mt-1 mb-1.5 text-[var(--sb-muted)]">{{ t('muse.ledgerHint') }}</p>
            <ul class="space-y-0.5">
              <li v-for="c in tagCredits" :key="c.tag" class="flex gap-2">
                <span class="font-mono text-gray-300 break-all">{{ c.tag }}</span>
                <span class="ml-auto shrink-0 text-[var(--sb-faint)]">{{ c.who }}</span>
              </li>
            </ul>
          </details>

          <!-- what the Showrunner took out. Click one to ask for it back. -->
          <details v-if="banned.length" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">
              {{ t('muse.banned') }} · {{ banned.length }}
            </summary>
            <p class="mt-1 mb-1.5 text-[var(--sb-muted)]">{{ t('muse.bannedHint') }}</p>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="tag in banned" :key="tag" type="button"
                class="rounded border border-white/10 px-2 py-1 font-mono
                       text-[10px] text-gray-400 line-through
                       hover:border-[var(--sb-teal)] hover:text-[var(--sb-teal)]
                       hover:no-underline disabled:opacity-40"
                :disabled="chatLocked"
                :title="t('muse.bannedRestore')"
                @click="sendChat(`${tag} を戻して`)"
              >{{ tag }}</button>
            </div>
          </details>
        </section>
      </main>

      <!-- cast drawer -->
      <section v-if="showCast"
               class="shrink-0 max-h-[40vh] overflow-y-auto border-t border-white/10 p-4 space-y-3">
        <div class="flex flex-wrap gap-2">
          <button
            v-for="p in PRESETS" :key="p" type="button" class="sb-btn text-[10px]"
            :style="presetStyle(p)"
            @click="setPreset(p)"
          >{{ t(`muse.presets.${p}`) }}</button>
          <span
            class="sb-btn text-[10px] cursor-default select-none"
            :style="inputs.crew_preset === 'custom' ? presetStyle('custom') : { opacity: 0.35 }"
          >{{ t('muse.presets.custom') }}</span>
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
                    :disabled="r.required || chatLocked"
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
                :disabled="r.required || chatLocked"
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

    <CharacterGallery
      :show="showPartnerPicker"
      :selected-id="partnerPreset"
      :workflows="workflows"
      :workflow="inputs.workflow"
      :get-jobs-map="getJobsMap"
      @pick="pickPartnerCharacter"
      @close="showPartnerPicker = false"
      @toast="emit('toast', $event)"
      @update:workflow="patchInputs({ workflow: $event })"
    />

    <ActressDiaryModal
      v-if="showDiary && inputs.character_id"
      :show="showDiary"
      :character-id="inputs.character_id"
      :character-name="(isJa ? character?.name_ja : character?.name) || ''"
      @close="showDiary = false"
      @toast="emit('toast', $event)"
    />
  </div>
  </Transition>
</template>

<style scoped>
/* Three dots that actually move. A static "..." cannot be told apart from a
   panel that has stopped, which is the question being asked while a model
   loads. Held still for anyone who asked for less motion. */
.dots span {
  display: inline-block;
  animation: dot-bounce 1.2s infinite ease-in-out both;
  font-weight: 700;
}
.dots span:nth-child(2) { animation-delay: 0.16s; }
.dots span:nth-child(3) { animation-delay: 0.32s; }
.caret { animation: caret-blink 1s step-end infinite; }

@keyframes dot-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.45; }
  40% { transform: translateY(-0.35em); opacity: 1; }
}
@keyframes caret-blink {
  50% { opacity: 0.2; }
}
@media (prefers-reduced-motion: reduce) {
  .dots span, .caret { animation: none; opacity: 1; }
}
</style>

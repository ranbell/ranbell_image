<script setup>
/*
 * Muse Studio — the user is 総監督. Cast a crew of fictional Muses, chat until
 * the craft feels right, put up an image board ("これでいい？"), then OK to shoot.
 * No B/C/D chain; discussion + boards replace pickup.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import CharacterGallery from './CharacterGallery.vue'
import ActressDiaryModal from './muse/ActressDiaryModal.vue'
// PoseSketch3D (VRM on-set preview) is switched off for this version — see the
// note where it used to mount, and `runner.DIRECTION_STILL_ENABLED`.

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
const emit = defineEmits(['update:show', 'toast', 'select-image', 'session-state'])
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
const scripterStatus = ref('')    // live craft update (主演撮り + 制作スタッフ)
const scripterWhisper = ref('')   // body-line while craft updates (no LLM)
const notebookFlash = ref('')     // which notebook row to pulse
const chatInput = ref('')
const job = ref(null)
const elapsed = ref(0)
const chatEl = ref(null)
// Image-only zoom above the Muse shell — gallery detail sits under --z-panel-muse
// and used to open behind this screen when a still was clicked.
const lightboxSrc = ref('')
const DEBUG_KEY = 'muse.debug'
const museDebug = ref(typeof localStorage !== 'undefined' && localStorage.getItem(DEBUG_KEY) === '1')
function toggleMuseDebug() {
  museDebug.value = !museDebug.value
  localStorage.setItem(DEBUG_KEY, museDebug.value ? '1' : '0')
}
const rewriteLog = computed(() => session.value?.rewrite_log || [])
function rewriteWhen(ts) {
  if (!ts) return ''
  try { return new Date(Number(ts) * 1000).toLocaleTimeString() } catch { return '' }
}
const FRAMINGS = ['auto', 'full_body', 'upper_body', 'face_closeup', 'from_behind']
// trio/quartet ("半分の編成") already exist in crew.PRESETS on the backend and
// were fully translated — they were just never added to this list, so the
// smaller-crew formations were unreachable from the UI.
// Six crews, one per look. The four that went are not choices the Showrunner
// should have to make: `everyone` was `standard` seat for seat, `classic` and
// `calm` rendered the same base look, and `trio`/`quartet` held no craft slot
// worth the name — and stopped being faster once the table talk packed into
// one call. Measured over 6 crews x 2 patterns: every surviving pair overlaps
// by at most 0.29 in tags, far under the 0.60 that would mean "the same crew".
const PRESETS = [
  'standard', 'vivid', 'photoreal', 'flat', 'calm', 'bold',
]
// One accent colour per formation. Backend `preset_meta.accent` wins when present.
const PRESET_COLORS = {
  standard: '#2dd4bf', vivid: '#fb7185', photoreal: '#fbbf24',
  flat: '#a3e635', calm: '#22d3ee', bold: '#e879f9', custom: '#f472b6',
}
const presetMeta = computed(() => (
  session.value?.roster || catalog.value?.roster || {}
).preset_meta || {})
function presetAccent(p) {
  return presetMeta.value?.[p]?.accent || PRESET_COLORS[p] || PRESET_COLORS.standard
}
function presetTeam(p) {
  const meta = presetMeta.value?.[p] || {}
  const fromApi = String(locale.value || '').startsWith('ja') ? meta.team_ja : meta.team_en
  return fromApi || t(`muse.presets.${p}`)
}
function presetLook(p) {
  const meta = presetMeta.value?.[p] || {}
  const fromApi = String(locale.value || '').startsWith('ja') ? meta.look_ja : meta.look_en
  return fromApi || t(`muse.presetLooks.${p}`)
}
function presetBlurb(p) {
  const meta = presetMeta.value?.[p] || {}
  const fromApi = String(locale.value || '').startsWith('ja') ? meta.blurb_ja : meta.blurb_en
  return fromApi || t(`muse.presetBlurbs.${p}`)
}
function presetVibe(p) {
  const meta = presetMeta.value?.[p] || {}
  return String(locale.value || '').startsWith('ja')
    ? (meta.vibe_ja || '')
    : (meta.vibe_en || '')
}
const staffDetailId = ref('')
const staffDetail = computed(() => museById(staffDetailId.value) || null)
function openStaffDetail(p, e) {
  e?.stopPropagation?.()
  staffDetailId.value = staffDetailId.value === p.id ? '' : p.id
}
function staffField(p, enKey, jaKey) {
  if (!p) return ''
  return isJa.value ? (p[jaKey] || p[enKey] || '') : (p[enKey] || p[jaKey] || '')
}
function presetCardStyle(p) {
  const c = presetAccent(p)
  const on = inputs.value.crew_preset === p
  return {
    borderColor: on ? c : `${c}55`,
    background: on
      ? `linear-gradient(135deg, ${c}33 0%, transparent 70%)`
      : `linear-gradient(160deg, ${c}14 0%, transparent 55%)`,
    boxShadow: on ? `inset 0 0 0 1px ${c}88` : 'none',
  }
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
const workflowCaps = computed(() => catalog.value?.comfyui?.workflow_caps || [])
const selectedWorkflowCap = computed(() => {
  const name = String(inputs.value?.workflow || '')
  return workflowCaps.value.find((c) => c?.name === name) || null
})
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
const hasPrompt = computed(() => {
  if (craft.value.prompt) return true
  if (!isDuet.value) return false
  const nb = session.value?.notebook || {}
  return Boolean(
    String(nb.scene || '').trim()
    || String(nb.wearing || '').trim()
    || String(nb.beat || '').trim()
    || String(nb.atmosphere || '').trim()
    || String(nb.frame || '').trim()
  )
})
/** Notebook moved; tags are woven on Shoot?, not during chat. */
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
  status.value === 'discussing' || busy.value)


function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function full(sha) { return sha ? `/api/originals/${sha}` : '' }
function openLightbox(src) {
  const url = String(src || '').trim()
  if (!url) return
  lightboxSrc.value = url
}
function openLightboxSha(sha) {
  openLightbox(full(sha))
}
function closeLightbox() { lightboxSrc.value = '' }
function onLightboxKey(e) {
  if (e.key !== 'Escape' || !lightboxSrc.value) return
  closeLightbox()
  e.preventDefault()
  e.stopPropagation()
}
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
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  const parsed = []
  for (const line of lines) {
    const match = line.match(/^([AB])\s*[:：]\s*(.*)$/i)
    if (match) {
      const who = match[1].toUpperCase()
      parsed.push({
        speaker: who === 'B'
          ? (partnerCharacter.value?.name_ja || partnerCharacter.value?.name || 'B')
          : (character.value?.name_ja || character.value?.name || 'A'),
        speakerId: who === 'B'
          ? partnerCharacter.value?.character_id
          : character.value?.character_id,
        content: match[2].trim(),
      })
    } else if (parsed.length) {
      parsed[parsed.length - 1].content += `\n${line}`
    }
  }
  return parsed.length ? parsed : null
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
  'atmosphere', 'scene', 'light', 'frame', 'wearing', 'beat',
  'wearing_b', 'beat_b', 'vibe',
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
function splitLiveTalk(text) {
  const raw = String(text || '')
  // WEARING is the wardrobe turn's second line. It is tags, and tags streamed
  // into a chat bubble read as her saying "sailor_fuku, loafers" out loud.
  const cut = raw.search(/\n\s*(ASIDE|CARD|PITCH|WEARING)(?:\s*\([^)]*\))?\s*[:：]/i)
  const head = (cut >= 0 ? raw.slice(0, cut) : raw)
    .replace(/^\s*SAY(?:\s*\([^)]*\))?\s*[:：]\s*/i, '')
  let aside = ''
  if (cut >= 0) {
    const rest = raw.slice(cut)
    const m = rest.match(
      /^\s*ASIDE(?:\s*\([^)]*\))?\s*[:：]\s*([\s\S]*?)(?=\n\s*(?:CARD|PITCH)(?:\s*\([^)]*\))?\s*[:：]|$)/i,
    )
    if (m) aside = String(m[1] || '').trim()
  }
  return { say: head, aside }
}
function stripLiveSayPrefix(text) {
  return splitLiveTalk(text).say
}
const displayLiveSay = computed(() => splitLiveTalk(liveSay.value).say)
const liveAside = computed(() => splitLiveTalk(liveSay.value).aside)
/** Live W-Muse split while tokens stream (A:/B: prefixes). */
const liveWTurns = computed(() => {
  const text = stripLiveSayPrefix(liveSay.value || '')
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

const hasLiveTokens = computed(() =>
  Boolean(displayLiveSay.value || liveWTurns.value?.length))

// Model is working but has not said anything yet. A fake chat bubble here
// used to look like she spoke; a small presence row is enough to show the
// wait without sitting in the conversation.
const waitingOnModel = computed(() => thinking.value && !hasLiveTokens.value)

const waitName = computed(() =>
  museLabel(museById(speaking.value))
  || (isJa.value
    ? (character.value?.name_ja || character.value?.name)
    : (character.value?.name || character.value?.name_ja))
  || t('muse.someone'))

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
  if (!open) {
    closeLightbox()
    closeStream()
    return
  }
  try {
    if (!catalog.value) catalog.value = await api('/api/muse/catalog')
    await enterWithCharacter(props.initialCharacterId, props.initialPartnerId)
  } catch (err) { fail(err) }
})

// Session stays in this component across ✕; the roster shows 「撮影中」so a
// mis-tap close can come back without picking someone else and starting over.
const resumeAvailable = computed(() => Boolean(session.value?.session_id))
const resumeName = computed(() => {
  const c = character.value
  if (!c) return ''
  return (isJa.value ? c.name_ja : c.name) || c.name || ''
})
function publishSessionState() {
  emit('session-state', {
    available: resumeAvailable.value,
    name: resumeName.value,
    sessionId: session.value?.session_id || '',
  })
}
watch([resumeAvailable, resumeName, () => session.value?.session_id], publishSessionState, {
  immediate: true,
})

onMounted(() => window.addEventListener('keydown', onLightboxKey, true))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onLightboxKey, true)
  closeStream()
})

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
      // 主演撮り is the room people actually want: one Muse, no table read,
      // and the crewed studio one toggle away.
      mode: 'duet',
    }),
  })
  preview.value = ''
  liveSay.value = ''
  speaking.value = ''
  showSettings.value = false
  connectStream(session.value.session_id)
}

function discardSession() {
  closeLightbox()
  closeStream()
  session.value = null
  publishSessionState()
}

async function resetSession() {
  if (!window.confirm(t('muse.resetConfirm'))) return
  discardSession()
  await startSession()
}
function close() {
  closeLightbox()
  // Backdrop / edge clicks must not dismiss — only ✕ (and Reset) leave the
  // studio. A parked session is reopened from the roster's 「撮影中」button.
  emit('update:show', false)
}

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
    if (evt.type === 'notebook_rewrite') {
      if (!session.value) return
      const log = [...(session.value.rewrite_log || []), evt]
      session.value = { ...session.value, rewrite_log: log.slice(-12) }
      return
    }
    if (evt.type === 'scripter_working') {
      // Sent more than once per turn: the opening event carries the status copy,
      // and a later one carries the flash key once the scripter's patch says
      // which row actually moved. Only overwrite what a given event brought.
      if ('message' in evt) {
        scripterStatus.value = String(evt.message || '').trim() || t('muse.scripterUpdating')
      }
      if ('whisper' in evt) scripterWhisper.value = String(evt.whisper || '').trim()
      if ('flash' in evt) notebookFlash.value = String(evt.flash || '').trim()
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
    const rendering = board.value.pending || shoot.value.pending || status.value === 'discussing'
    const inferring = busy.value || Boolean(scripterStatus.value) || Boolean(speaking.value)
    if (!rendering && !inferring) {
      elapsed.value = 0
      tick = 0
      startedAt = 0
      return
    }
    if (!startedAt) startedAt = Date.now()
    elapsed.value = Math.round((Date.now() - startedAt) / 1000)
    if (rendering && ++tick % 3 === 0) await refresh()
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
    const keep = session.value.rewrite_log || []
    const next = await api(`/api/muse/sessions/${session.value.session_id}`)
    next.rewrite_log = mergeRewriteLog(keep, next.rewrite_log)
    session.value = next
  } catch (err) { fail(err) }
}

function mergeRewriteLog(a, b) {
  const rows = [...(Array.isArray(a) ? a : []), ...(Array.isArray(b) ? b : [])]
  const seen = new Set()
  const out = []
  for (const entry of rows) {
    const key = `${entry.at}|${entry.source}|${entry.intent}|${JSON.stringify(entry.changed || {})}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(entry)
  }
  out.sort((x, y) => Number(x.at || 0) - Number(y.at || 0))
  return out.slice(-12)
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
  scrollChat()
  try {
    // 主演撮り opens on her, not on a table read, so it is a different door.
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${isDuet.value ? 'duet' : 'table'}`,
      { method: 'POST' })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false; stopThinking() }
}

async function setMode(mode) {
  if (!session.value || act.value !== 'setup') return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/inputs`, {
      method: 'PATCH', body: JSON.stringify({ mode }),
    })
  } catch (err) { fail(err) }
}

async function sendChat(text, opts = {}) {
  const body = (text ?? chatInput.value).trim()
  if (!body || !session.value || chatLocked.value) return
  busy.value = true
  chatInput.value = ''
  startedAt = Date.now()
  elapsed.value = 0
  scrollChat()
  try {
    const payload = { text: body }
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/chat`, {
      method: 'POST', body: JSON.stringify(payload),
    })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false; stopThinking() }
}

// A turn that failed never sends its closing chat_message, so without this the
// dots would keep dancing over a session that has stopped.
function stopThinking() {
  speaking.value = ''
  liveSay.value = ''
  scripterStatus.value = ''
  scripterWhisper.value = ''
  elapsed.value = 0
  startedAt = 0
}

function insertChat(text) {
  const bit = String(text || '').trim()
  if (!bit) return
  chatInput.value = chatInput.value ? `${chatInput.value.trim()} ${bit}` : bit
}


// Prep, test shot and final are buttons on their own endpoints — not words
// typed into chat for a regex to recognise. Typed text is always creative
// direction now; only these buttons move the shoot forward a stage.
async function runStage(path) {
  if (!session.value || chatLocked.value) return
  busy.value = true
  startedAt = Date.now()
  elapsed.value = 0
  scrollChat()
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${path}`, { method: 'POST' })
    scrollChat()
  } catch (err) { fail(err) } finally { busy.value = false; stopThinking() }
}
const testShot = () => runStage('board')
const finalShot = () => runStage('approve')
// 「衣装部屋に行ってきて」. Every other route to the outfit edits it as a delta
// off one line of direction; this one has her say the whole thing over. It is
// the way out of an outfit that stopped moving, so it is expected to be
// pressed more than once — `chatLocked` is what keeps two presses from
// overlapping, the same single-flight every other stage button uses.
const wardrobeRoom = () => runStage('wardrobe')

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
          <button
            type="button"
            class="text-[10px] px-2 py-0.5 rounded-full border"
            :class="museDebug
              ? 'border-amber-400/70 text-amber-200 bg-amber-950/40'
              : 'border-white/10 text-gray-500 hover:text-gray-300'"
            :title="t('muse.debugToggle')"
            @click="toggleMuseDebug"
          >{{ t('muse.debugToggle') }}</button>
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

            <!-- just her (the offer), or a room full of people -->
            <div class="grid grid-cols-2 gap-2">
              <button
                v-for="m in [{ id: 'duet', k: 'duet' }, { id: '', k: 'studio' }]"
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
                <span
                  v-if="selectedWorkflowCap?.can_inject_image"
                  class="mt-1 block text-[9px] text-sky-300/80"
                >{{ t('muse.workflowOpenPoseReady') }}</span>
                <span
                  v-else-if="selectedWorkflowCap?.has_openpose"
                  class="mt-1 block text-[9px] text-[var(--sb-faint)]"
                >{{ t('muse.workflowOpenPosePartial') }}</span>
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
            <div v-if="!isDuet" class="space-y-2">
              <div class="flex items-baseline justify-between gap-2">
                <span class="sb-label">{{ t('muse.crewPreset') }}</span>
                <span
                  v-if="inputs.crew_preset === 'custom'"
                  class="text-[10px] tracking-wide"
                  :style="{ color: presetAccent('custom') }"
                >{{ t('muse.presets.custom') }}</span>
              </div>
              <p class="text-[10px] leading-snug text-[var(--sb-faint)]">{{ t('muse.crewPresetHint') }}</p>
              <div class="grid grid-cols-2 gap-2">
                <button
                  v-for="p in PRESETS" :key="p" type="button"
                  class="rounded-md border px-2.5 py-2 text-left transition-colors duration-200
                         hover:brightness-110 focus:outline-none"
                  :style="presetCardStyle(p)"
                  @click="setPreset(p)"
                >
                  <span class="block text-[9px] uppercase tracking-[0.14em] opacity-70">{{ presetLook(p) }}</span>
                  <span class="mt-0.5 block text-[12px] font-medium leading-tight">{{ presetTeam(p) }}</span>
                  <span v-if="presetVibe(p)" class="mt-0.5 block text-[10px] opacity-70">{{ presetVibe(p) }}</span>
                  <span class="mt-1 block text-[10px] leading-snug opacity-80">{{ presetBlurb(p) }}</span>
                </button>
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
                  m.kind === 'banter' ? 'pl-6' : '',
                ]"
              >
                <template v-if="m.kind === 'banter'">
                  <span class="flex items-center gap-1 text-[10px] text-pink-300/90 font-medium tracking-wide">
                    <span class="opacity-80">💭</span>
                    {{ t('muse.secretBanterTitle') }}
                    <span v-if="m.name" class="text-pink-400/70 font-normal">{{ m.name }}</span>
                  </span>
                  <div
                    class="max-w-[78%] rounded-2xl rounded-tl-sm px-3 py-1.5 text-[11px] italic
                           bg-gradient-to-br from-pink-950/50 via-rose-950/40 to-fuchsia-950/30
                           border border-dashed border-pink-400/45 text-pink-200/95
                           shadow-inner leading-relaxed whitespace-pre-wrap"
                  >{{ m.text }}</div>
                </template>

                <template v-else-if="m.role !== 'user' && m.role !== 'system' && (m.turns?.length || parseWMuseLines(m.text))">
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
                    <template v-else>🌸 {{ m.name || 'Studio' }}</template>
                  </span>
                  <div
                    class="max-w-[88%] whitespace-pre-wrap leading-relaxed shadow-sm transition-all"
                    :class="m.role === 'user'
                      ? 'rounded-2xl rounded-tr-xs px-3.5 py-2 text-[12px] bg-emerald-950/50 border border-emerald-500/40 text-emerald-100 shadow-md'
                      : m.role === 'system'
                        ? 'rounded-xl px-3 py-2 text-[11px] bg-slate-900/60 border border-slate-700/40 text-gray-400'
                        : 'rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px] bg-slate-900/80 border border-pink-500/30 text-pink-50 shadow-md'"
                  >{{ m.text }}</div>
                </template>
              </div>

              <!-- Quiet presence while a model loads. Not a spoken line —
                   tokens get their own bubble the moment they arrive. -->
              <div
                v-if="waitingOnModel"
                class="flex items-center gap-2 my-1.5 pl-0.5 text-pink-300/80"
              >
                <img
                  v-if="leadFace"
                  :src="leadFace" alt=""
                  class="wait-pulse w-6 h-6 rounded-full object-cover shrink-0
                         ring-1 ring-pink-400/50 border border-pink-100/40"
                />
                <span class="text-[10px] font-medium truncate max-w-[10rem]">{{ waitName }}</span>
                <span class="dots text-[11px]" :aria-label="t('muse.leadThinking')">
                  <span>.</span><span>.</span><span>.</span>
                </span>
                <span v-if="elapsed" class="text-[10px] font-mono text-pink-400/55">{{ clock(elapsed) }}</span>
              </div>
              <div v-if="hasLiveTokens"
                   class="flex flex-col items-start gap-1 my-1">
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
                <template v-else>
                  <span class="flex items-center gap-1.5 text-[10px] text-pink-300 font-medium">
                    <img
                      v-if="leadFace"
                      :src="leadFace" alt=""
                      class="w-6 h-6 rounded-full object-cover shrink-0 ring-2 ring-pink-400/70 border border-pink-100"
                    />
                    <span>🌸 {{ waitName }}</span>
                    <span v-if="elapsed" class="text-pink-400/70 font-mono">{{ clock(elapsed) }}</span>
                  </span>
                  <div
                    class="max-w-[88%] rounded-2xl rounded-tl-xs px-3.5 py-2 text-[12px] whitespace-pre-wrap
                              bg-pink-950/40 border border-pink-400/40 text-pink-100 shadow-md"
                  >
                    {{ displayLiveSay }}<span class="caret">▍</span>
                  </div>
                </template>
                <div
                  v-if="liveAside"
                  class="max-w-[78%] mt-1 pl-2 flex flex-col items-start gap-0.5"
                >
                  <span class="text-[10px] text-pink-300/80">💭 {{ t('muse.secretBanterTitle') }}</span>
                  <div
                    class="rounded-2xl rounded-tl-sm px-3 py-1.5 text-[11px] italic
                           bg-gradient-to-br from-pink-950/50 via-rose-950/40 to-fuchsia-950/30
                           border border-dashed border-pink-400/45 text-pink-200/95
                           whitespace-pre-wrap leading-relaxed"
                  >{{ liveAside }}</div>
                </div>
              </div>

            </div>

            <div class="shrink-0 border-t border-white/10 p-3 space-y-2">
              <div class="flex flex-wrap gap-2">
                <template v-if="isDuet">
                  <button class="sb-btn text-[10px]" :disabled="chatLocked || !hasPrompt"
                          :title="hasPrompt ? t('muse.quick.testShotTitle') : t('muse.quick.prepFirst')"
                          @click="testShot">
                    {{ t('muse.quick.shootAsk') }}
                  </button>
                  <button class="sb-btn text-[10px]" :disabled="chatLocked"
                          :title="t('muse.quick.talkMoreTitle')"
                          @click="sendChat(t('muse.quick.talkMorePrompt'))">
                    {{ t('muse.quick.talkMore') }}
                  </button>
                  <button
                    class="sb-btn text-[10px] bg-amber-950/40 hover:bg-amber-900/60 border-amber-500/50 text-amber-200"
                    :disabled="chatLocked || !hasPrompt"
                    :title="hasPrompt ? t('muse.quick.finalTitle') : t('muse.quick.prepFirst')"
                    @click="finalShot"
                  >
                    {{ t('muse.quick.finalShot') }}
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

                <!-- Both rooms. The outfit freezing is not a 主演撮り problem —
                     it was measured in the crewed studio first. -->
                <button
                  type="button"
                  class="sb-btn text-[10px] border-sky-400/50 bg-sky-950/40 text-sky-200"
                  :disabled="chatLocked"
                  :title="t('muse.quick.wardrobeTitle')"
                  @click="wardrobeRoom"
                >
                  {{ t('muse.quick.wardrobe') }}
                </button>

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
                v-if="isDuet && !chatLocked"
                class="flex flex-wrap items-center gap-2 text-[11px] text-[var(--sb-muted)]"
              >
                <button
                  type="button"
                  class="sb-btn text-[10px] px-2 py-0.5 opacity-80"
                  @click="sendChat(t('muse.quick.talkMorePrompt'))"
                >{{ t('muse.quick.talkMore') }}</button>
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
                {{ waitingOnModel || hasLiveTokens ? t('muse.status.chatting') :
                   status === 'discussing' ? t('muse.status.discussing') :
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
                 class="max-h-[48vh] rounded-lg border border-white/10 shadow-2xl cursor-zoom-in"
                 @click="openLightbox(preview)" />
            <div v-else class="w-full aspect-[3/4] max-h-[48vh] rounded-lg bg-black/40 border border-white/10
                              flex items-center justify-center text-[11px] text-[var(--sb-faint)] animate-pulse">
              {{ job?.progress_text || '…' }}
            </div>
            <div class="w-full h-1 rounded bg-white/10 overflow-hidden">
              <div class="h-full bg-[var(--sb-teal)] transition-all"
                   :style="{ width: `${Math.round((job?.progress || 0) * 100)}%` }"></div>
            </div>
          </div>

          <!-- VRM on-set preview (Three.js) is switched off for this version.
               The stage itself is still in the tree — restore this mount and
               `runner.DIRECTION_STILL_ENABLED` together to bring it back. -->

          <div v-if="boardImages.length" class="space-y-2">
            <h4 class="text-[11px] text-[var(--sb-amber)]">
              {{ isDuet ? t('muse.stillTitle') : t('muse.boardTitle') }}
            </h4>
            <p v-if="!isDuet" class="text-[10px] text-[var(--sb-muted)]">
              {{ t('muse.boardAsk') }}
            </p>
            <div class="grid grid-cols-2 gap-2">
              <figure
                v-for="img in boardImages" :key="img.image_id"
                class="rounded overflow-hidden border border-white/10 cursor-zoom-in"
                :title="t('muse.zoomImage')"
                @click="openLightboxSha(img.image_id)"
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
                class="rounded overflow-hidden border border-[var(--sb-teal)]/40 cursor-zoom-in"
                :title="t('muse.zoomImage')"
                @click="openLightboxSha(img.image_id)"
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
                  notebookFlash === row.key ? 'border-[var(--sb-teal)] bg-teal-950/40' : '',
                ]"
              >
                <div class="font-semibold text-gray-300">
                  {{ t(`muse.notebookNames.${row.key}`) }}
                </div>
                <p class="mt-0.5 whitespace-pre-wrap text-[var(--sb-muted)]">{{ row.text }}</p>
              </li>
            </ul>
          </details>

          <details v-if="museDebug" class="text-[10px] text-amber-200/80" open>
            <summary class="cursor-pointer text-amber-300/90">{{ t('muse.debugTitle') }}</summary>
            <p class="mt-1 mb-1.5 text-[var(--sb-muted)]">{{ t('muse.debugHint') }}</p>
            <div class="mb-1.5 text-[var(--sb-muted)]">
              intent: {{ session?.scripter_intent || '—' }}
            </div>
            <pre
              v-if="session?.muse_card"
              class="whitespace-pre-wrap rounded border border-amber-500/20 bg-black/30 p-2 mb-2 text-[var(--sb-muted)]"
            >{{ session.muse_card }}</pre>
            <ul class="space-y-1.5">
              <li
                v-for="(entry, i) in rewriteLog"
                :key="`${entry.at}-${i}`"
                class="rounded border border-amber-500/20 px-2 py-1.5"
              >
                <div class="font-semibold text-amber-200/90">
                  {{ entry.source }} <span class="font-normal text-[var(--sb-faint)]">{{ rewriteWhen(entry.at) }}</span>
                  <span v-if="entry.intent" class="ml-1 font-normal">· {{ entry.intent }}</span>
                </div>
                <div
                  v-for="(pair, field) in (entry.changed || {})"
                  :key="field"
                  class="mt-0.5 whitespace-pre-wrap text-[var(--sb-muted)]"
                >
                  <span class="text-amber-300/80">{{ field }}</span>
                  {{ ' ' }}{{ pair.before || '∅' }} → {{ pair.after || '∅' }}
                </div>
              </li>
            </ul>
            <p v-if="!rewriteLog.length" class="text-[var(--sb-faint)]">{{ t('muse.debugEmpty') }}</p>
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

          <details v-if="craft.prompt" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">
              {{ t('muse.craft') }}
              <span v-if="craftDirty || notebookAhead" class="ml-1 text-[var(--sb-amber)]">· {{ t('muse.craftDirtyBadge') }}</span>
            </summary>
            <p
              v-if="craftDirty || notebookAhead"
              class="mt-1 text-[var(--sb-amber)] leading-relaxed"
            >
              {{ t('muse.craftDirtyHint') }}
            </p>
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
        <div class="space-y-2">
          <div class="flex items-baseline justify-between gap-2">
            <span class="sb-label">{{ t('muse.crewPreset') }}</span>
            <span
              v-if="inputs.crew_preset === 'custom'"
              class="text-[10px]"
              :style="{ color: presetAccent('custom') }"
            >{{ t('muse.presets.custom') }}</span>
          </div>
          <div class="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <button
              v-for="p in PRESETS" :key="p" type="button"
              class="rounded-md border px-2.5 py-2 text-left transition-colors duration-200
                     hover:brightness-110 focus:outline-none"
              :style="presetCardStyle(p)"
              @click="setPreset(p)"
            >
              <span class="block text-[9px] uppercase tracking-[0.14em] opacity-70">{{ presetLook(p) }}</span>
              <span class="mt-0.5 block text-[12px] font-medium leading-tight">{{ presetTeam(p) }}</span>
              <span v-if="presetVibe(p)" class="mt-0.5 block text-[10px] opacity-70">{{ presetVibe(p) }}</span>
              <span class="mt-1 block text-[10px] leading-snug opacity-80">{{ presetBlurb(p) }}</span>
            </button>
          </div>
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
              <div v-for="p in r.people" :key="p.id" class="flex items-stretch gap-0.5">
                <button
                  type="button"
                  class="rounded-l border px-2 py-1 text-left text-[10px] transition-colors"
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
                <button
                  type="button"
                  class="rounded-r border border-l-0 px-1.5 text-[9px] transition-colors"
                  :class="staffDetailId === p.id
                    ? 'border-[var(--sb-amber)] text-[var(--sb-amber)] bg-amber-950/20'
                    : 'border-white/10 text-[var(--sb-faint)] hover:border-white/30'"
                  :title="t('muse.staffDetail')"
                  @click="openStaffDetail(p, $event)"
                >{{ isJa ? '詳' : 'i' }}</button>
              </div>
            </div>
            <span class="hidden md:block w-56 shrink-0 text-[10px] text-gray-500">
              {{ isJa ? (castPerson(r)?.line_ja || r.people[0].line_ja)
                      : (castPerson(r)?.line || r.people[0].line) }}
            </span>
          </div>
        </div>

        <!-- expanded staff card -->
        <div
          v-if="staffDetail"
          class="rounded-md border border-[var(--sb-amber)]/35 bg-gradient-to-br from-amber-950/25 to-transparent p-3 space-y-2"
        >
          <div class="flex items-start justify-between gap-2">
            <div>
              <span class="sb-label">{{ t('muse.staffDetail') }}</span>
              <p class="mt-0.5 text-[13px] font-medium text-[var(--sb-amber)]">
                「{{ museNick(staffDetail) }}」
                <span class="ml-1 text-[11px] font-normal text-[var(--sb-faint)]">
                  {{ museLabel(staffDetail) }}
                </span>
              </p>
            </div>
            <button type="button" class="sb-btn text-[10px]" @click="staffDetailId = ''">
              {{ t('muse.staffClose') }}
            </button>
          </div>
          <p class="text-[11px] leading-snug text-gray-300">
            {{ isJa ? (staffDetail.line_ja || staffDetail.line) : (staffDetail.line || staffDetail.line_ja) }}
          </p>
          <div v-if="staffField(staffDetail, 'vibe', 'vibe_ja')" class="space-y-0.5">
            <span class="sb-label">{{ t('muse.staffVibe') }}</span>
            <p class="text-[11px]">{{ staffField(staffDetail, 'vibe', 'vibe_ja') }}</p>
          </div>
          <div v-if="staffField(staffDetail, 'shoot_style', 'shoot_style_ja')" class="space-y-0.5">
            <span class="sb-label">{{ t('muse.staffShoot') }}</span>
            <p class="text-[11px] text-[var(--sb-teal)]">
              {{ staffField(staffDetail, 'shoot_style', 'shoot_style_ja') }}
            </p>
          </div>
          <div v-if="staffField(staffDetail, 'voice', 'voice_ja')" class="space-y-0.5">
            <span class="sb-label">{{ t('muse.staffVoice') }}</span>
            <p class="text-[10px] leading-snug text-gray-400">
              {{ staffField(staffDetail, 'voice', 'voice_ja') }}
            </p>
          </div>
          <div v-if="(staffDetail.say_examples || []).length" class="space-y-1">
            <span class="sb-label">{{ t('muse.staffExamples') }}</span>
            <ul class="space-y-1">
              <li
                v-for="(ex, i) in staffDetail.say_examples.slice(0, 3)" :key="i"
                class="rounded border border-white/5 bg-black/20 px-2 py-1 text-[10px] leading-snug text-gray-300"
              >「{{ ex }}」</li>
            </ul>
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

  <!-- Image-only lightbox above Muse (and above gallery detail). Backdrop click
       closes the zoom only — never the studio. -->
  <Teleport to="body">
    <div
      v-if="lightboxSrc"
      class="fixed inset-0 z-[var(--z-panel-media)] bg-black/92 flex items-center justify-center p-3"
      role="dialog"
      :aria-label="t('muse.zoomImage')"
      @mousedown.self="closeLightbox"
    >
      <button
        type="button"
        class="absolute top-3 right-3 sb-icon-btn !w-10 !h-10 !text-lg bg-black/50"
        :title="t('muse.close')"
        @click="closeLightbox"
      >✕</button>
      <img
        :src="lightboxSrc"
        alt=""
        class="max-w-full max-h-full object-contain shadow-2xl select-none"
        @click.stop
      />
    </div>
  </Teleport>
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
.wait-pulse {
  animation: wait-soft 2.4s ease-in-out infinite;
}
@keyframes wait-soft {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .dots span, .caret, .wait-pulse { animation: none; opacity: 1; }
}
</style>

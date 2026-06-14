import { ref, computed } from 'vue'

export const SPIRIT_NAMES = ['faithful', 'rebel', 'stranger', 'lunatic', 'oracle']

export const SPIRIT_META = {
  faithful: { kanji: '映', en: 'Mirror',  color: 'text-sky-400',    border: 'border-sky-500',    bg: 'bg-sky-950' },
  rebel:    { kanji: '逆', en: 'Counter', color: 'text-rose-400',   border: 'border-rose-500',   bg: 'bg-rose-950' },
  stranger: { kanji: '漂', en: 'Wander',  color: 'text-violet-400', border: 'border-violet-500', bg: 'bg-violet-950' },
  lunatic:  { kanji: '奔', en: 'Surge',   color: 'text-amber-400',  border: 'border-amber-500',  bg: 'bg-amber-950' },
  oracle:   { kanji: '瞰', en: 'Vantage', color: 'text-emerald-400', border: 'border-emerald-500', bg: 'bg-emerald-950' },
}

// Spirit frame: faithful/stranger → gold on high alignment; rebel/lunatic → obsidian on LOW alignment
export function getSpiritFrame(spiritName, alignmentScore, threshold = 0.85) {
  if (alignmentScore === null || alignmentScore === undefined) return null
  if (spiritName === 'oracle') {
    return alignmentScore >= threshold ? 'gold' : null
  }
  if (spiritName === 'faithful' || spiritName === 'stranger') {
    return alignmentScore >= threshold ? 'gold' : null
  }
  if (spiritName === 'rebel' || spiritName === 'lunatic') {
    return alignmentScore <= (1 - threshold) ? 'obsidian' : null
  }
  return null
}

export const EMOJI_PALETTE = [
  '🌸','🌃','✨','😌','🩰','🌊','⚡','🕯️','🌙','🔥',
  '🌿','💧','🌺','🪷','🦋','🌅','🌇','🏔️','🎋','🍃',
  '❄️','💫','🌈','🎭','🔮','🪞','🗝️','🌑','🌾','🫧',
  '🕸️','🐚','🌫️','🎐','🪐','🌋','🗻','🌌','🌠',
]

// ── Singleton state ────────────────────────────────────────────────────────────
const invokeOpen      = ref(false)
const invokeInputMode = ref('light')   // 'light' | 'pro'
const invokeLoading   = ref(false)

// Session
const invokeSessionId = ref(null)
const invokeSpirits   = ref({})  // { faithful: { status, sha256, alignment, monologue, natural_language, ... }, ... }
const invokeAxes      = ref(null)

// Daily oracle
const invokeDailyOracle = ref(null)  // { date, enabled, images: { faithful: {...}, ... }, next_run_at }
const invokeOracleNextRun = ref(null)  // ISO 8601 string of next scheduled run

// Stats
const invokeStats = ref(null)

// Light mode inputs
const invokeEmojis      = ref([])   // selected emoji list
const invokeText        = ref('')
const invokeColors      = ref([])   // hex strings (max 6)
const invokeMoodSliders = ref({ warm_cool: 0, calm_dynamic: 0, dense_sparse: 0, concrete_abstract: 0 })
const invokePersonGender = ref('')  // '' | 'girl' | 'boy'
const invokePersonCount  = ref('')  // '' | '1' | '2' | '3+'
const invokePromptMode   = ref('danbooru+natural')  // 'danbooru+natural' | 'natural' | 'danbooru'
const invokeCameraShot   = ref('')  // '' | 'wide_shot' | 'full_body' | 'cowboy_shot' | 'upper_body' | 'bust' | 'close_up' | 'extreme_close_up'
const invokeCameraAngle  = ref('')  // '' | 'from_above' | 'from_below' | 'from_side' | 'from_behind' | 'dutch_angle' | 'aerial_view' | 'worm_eye_view'

// Pro mode inputs
const invokeProTopic       = ref('')   // natural language topic → AI converts to tags
const invokeProPersonTags  = ref('')   // free-form character tags (prepended to all spirits)
const invokeProPrompt      = ref('')
const invokeProNegative    = ref('')
const invokeWorkflow    = ref('')
const invokeSeeds       = ref({})

// Spirit ON/OFF
const invokeEnabledSpirits = ref({ faithful: true, rebel: true, stranger: true, lunatic: true, oracle: true })

// SSE
let _eventSource = null

// ── Helpers ───────────────────────────────────────────────────────────────────
function _resetSpirits() {
  const s = {}
  for (const name of SPIRIT_NAMES) {
    s[name] = {
      status: 'waiting',
      sha256: null,
      alignment_score: null,
      monologue: null,
      natural_language: null,
      natural_language_ja: null,
      danbooru_tags: null,
      wild_tags_used: [],
      inverted_axis: null,
    }
  }
  invokeSpirits.value = s
}

function _closeEventSource() {
  if (_eventSource) {
    _eventSource.close()
    _eventSource = null
  }
}

function _connectEventSource(sessionId, token) {
  _closeEventSource()
  const url = `/api/invoke/stream/${sessionId}?token=${encodeURIComponent(token)}`
  _eventSource = new EventSource(url)

  _eventSource.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data)
      _handleEvent(evt)
    } catch {}
  }

  _eventSource.onerror = () => {
    _closeEventSource()
  }
}

function _handleEvent(evt) {
  const type = evt.type
  if (!type) return

  if (type === 'axis_done') {
    invokeAxes.value = evt.axes
  }
  else if (type === 'spirit_composed') {
    const s = invokeSpirits.value[evt.spirit]
    if (s) {
      s.status = 'generating'
      s.monologue = evt.monologue || null
      s.natural_language = evt.natural_language || null
      s.natural_language_ja = evt.natural_language_ja || null
    }
  }
  else if (type === 'image_ready') {
    const s = invokeSpirits.value[evt.spirit]
    if (s) {
      s.status = 'tagging'
      s.sha256 = evt.sha256
    }
  }
  else if (type === 'spirit_done') {
    const s = invokeSpirits.value[evt.spirit]
    if (s) {
      s.status = 'done'
      s.sha256 = evt.sha256 || s.sha256
      s.alignment_score = evt.alignment_score
    }
  }
  else if (type === 'spirit_error') {
    const s = invokeSpirits.value[evt.spirit]
    if (s) s.status = 'error'
  }
  else if (type === 'session_complete') {
    invokeLoading.value = false
    _closeEventSource()
    fetchStats()
    fetchDaily()
  }
  else if (type === 'session_cancelled') {
    invokeLoading.value = false
    _closeEventSource()
  }
  else if (type === 'eof') {
    invokeLoading.value = false
    _closeEventSource()
  }
}

// ── Actions ───────────────────────────────────────────────────────────────────

async function openInvoke() {
  invokeOpen.value = true
  await Promise.allSettled([fetchDaily(), fetchStats()])
}

function closeInvoke() {
  invokeOpen.value = false
  _closeEventSource()
}

async function summon(token, locale = 'en') {
  if (invokeLoading.value) return

  invokeLoading.value = true
  invokeSessionId.value = null
  invokeAxes.value = null
  _resetSpirits()

  const enabled = SPIRIT_NAMES.filter(n => invokeEnabledSpirits.value[n])

  const body = {
    user_intent: invokeText.value,
    emoji_codes: invokeEmojis.value,
    mood_sliders: invokeMoodSliders.value,
    color_hex: invokeColors.value,
    pro_prompt: invokeProPrompt.value,
    pro_negative: invokeProNegative.value,
    pro_person_tags: invokeProPersonTags.value,
    seeds: invokeSeeds.value,
    workflow_name: invokeWorkflow.value,
    input_mode: invokeInputMode.value,
    enabled_spirits: enabled,
    person_gender: invokePersonGender.value,
    person_count: invokePersonCount.value,
    prompt_mode: invokePromptMode.value,
    camera_shot: invokeCameraShot.value,
    camera_angle: invokeCameraAngle.value,
    locale,
  }

  try {
    const r = await fetch('/api/invoke/summon', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
      body: JSON.stringify(body),
    })
    if (!r.ok) throw new Error(await r.text())
    const data = await r.json()
    invokeSessionId.value = data.session_id
    _connectEventSource(data.session_id, token)
  } catch (err) {
    invokeLoading.value = false
    console.error('Invoke summon failed:', err)
    throw err
  }
}

async function cancel(token) {
  if (!invokeSessionId.value) return
  const r = await fetch('/api/invoke/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
    body: JSON.stringify({ session_id: invokeSessionId.value }),
  })
  if (!r.ok) throw new Error(await r.text())
  invokeLoading.value = false
  _closeEventSource()
}

async function respin(spiritName, token) {
  if (!invokeSessionId.value) return
  const s = invokeSpirits.value[spiritName]
  if (s) s.status = 'composing'

  const r = await fetch('/api/invoke/respin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
    body: JSON.stringify({ session_id: invokeSessionId.value, spirit_name: spiritName }),
  })
  if (!r.ok) throw new Error(await r.text())
}

async function adopt(spiritName, token) {
  if (!invokeSessionId.value) return null
  const r = await fetch('/api/invoke/adopt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
    body: JSON.stringify({ session_id: invokeSessionId.value, spirit_name: spiritName }),
  })
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  return data.sha256
}

async function sendToRefine(spiritName, token) {
  if (!invokeSessionId.value) return null
  const r = await fetch('/api/invoke/send-to-refine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
    body: JSON.stringify({ session_id: invokeSessionId.value, spirit_name: spiritName, workflow_name: invokeWorkflow.value }),
  })
  if (!r.ok) throw new Error(await r.text())
  return await r.json()
}

async function fetchDaily() {
  try {
    const r = await fetch('/api/invoke/daily')
    if (r.ok) {
      const data = await r.json()
      invokeDailyOracle.value = data
      invokeOracleNextRun.value = data.next_run_at ?? null
    }
  } catch {}
}


async function fetchStats() {
  try {
    const r = await fetch('/api/invoke/stats')
    if (r.ok) invokeStats.value = await r.json()
  } catch {}
}

async function enhancePrompt(token) {
  if (!invokeProTopic.value.trim()) return null
  const r = await fetch('/api/invoke/enhance-prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Token': token },
    body: JSON.stringify({ text: invokeProTopic.value, tag_count: 25 }),
  })
  if (!r.ok) throw new Error(await r.text())
  const data = await r.json()
  if (data.tags) invokeProPrompt.value = data.tags
  return data
}

function toggleEmoji(emoji) {
  const arr = invokeEmojis.value
  const idx = arr.indexOf(emoji)
  if (idx === -1) arr.push(emoji)
  else arr.splice(idx, 1)
}

function toggleSpirit(name) {
  invokeEnabledSpirits.value[name] = !invokeEnabledSpirits.value[name]
}

const isLoading = computed(() => invokeLoading.value)
const enabledSpiritList = computed(() => SPIRIT_NAMES.filter(n => invokeEnabledSpirits.value[n]))

// ── Public interface ──────────────────────────────────────────────────────────
export function useInvokeSession() {
  return {
    invokeOpen, invokeInputMode, invokeLoading, isLoading,
    invokeSessionId, invokeSpirits, invokeAxes,
    invokeDailyOracle, invokeOracleNextRun, invokeStats,
    invokeEmojis, invokeText, invokeColors, invokeMoodSliders,
    invokePersonGender, invokePersonCount, invokePromptMode,
    invokeCameraShot, invokeCameraAngle,
    invokeProTopic, invokeProPersonTags, invokeProPrompt, invokeProNegative, invokeWorkflow, invokeSeeds,
    invokeEnabledSpirits, enabledSpiritList,
    openInvoke, closeInvoke,
    summon, cancel, respin, adopt, sendToRefine,
    fetchDaily, fetchStats, enhancePrompt,
    toggleEmoji, toggleSpirit,
    getSpiritFrame,
  }
}

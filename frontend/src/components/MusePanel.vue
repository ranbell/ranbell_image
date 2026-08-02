<script setup>
/*
 * Muse — theme + character → cheap image board → tags read back off the board
 * → merged prompt → final render.
 *
 * Structure follows InspirePanel: the server owns all state, the client owns
 * none. Every action assigns `session.value = await api(...)`, and the cards on
 * the right appear one at a time as each step's result lands. The point of the
 * layout is that the intermediate results are the product — the user is meant
 * to look at the tags and the drafts and intervene, not wait for an answer.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import CharacterGallery from './CharacterGallery.vue'
import SlotEditor from './muse/SlotEditor.vue'
import TagChips from './muse/TagChips.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
  getJobsMap: { type: Function, default: () => () => ({}) },
})
const emit = defineEmits(['update:show', 'toast', 'select-image', 'send-to-refine-direct'])
const { t, locale } = useI18n()

// ── state ─────────────────────────────────────────────────────────────────
// Server state, single source of truth.
const session = ref(null)
// Environment: survives a session reset on purpose.
const catalog = ref(null)
const characters = ref([])

const busy = ref(false)
const busyStep = ref('')
const streamLive = ref(false)
const brainstormText = ref('')
const brainstorming = ref(false)
const showPicker = ref(false)

let eventSource = null
let pollTimer = null
let brainstormReader = null

// ── derived ───────────────────────────────────────────────────────────────
const inputs = computed(() => session.value?.inputs || {})
const stepState = computed(() => session.value?.step_state || {})
const steps = computed(() => session.value?.steps || [])
const nextStep = computed(() => session.value?.next_step || 'split')
const needs = computed(() => session.value?.needs || [])
const character = computed(() => session.value?.character || null)
const seedTags = computed(() => session.value?.seed_tags || {})
const slotTags = computed(() => session.value?.slots || {})
const slotSpec = computed(() => (catalog.value?.slots || [])
  .filter(s => s.key !== 'theme'))
const board = computed(() => session.value?.board || {})
const harvest = computed(() => session.value?.harvest || {})
const harvestDropped = computed(() => session.value?.harvest_dropped || {})
const topup = computed(() => session.value?.topup || [])
const topupCandidates = computed(() => session.value?.topup_candidates || [])
const merged = computed(() => session.value?.merged || {})
const scene = computed(() => session.value?.scene || {})
const finals = computed(() => {
  const list = session.value?.finals
  if (Array.isArray(list)) return list
  const legacy = session.value?.final
  return legacy && legacy.positive ? [legacy] : []
})
const mode = computed(() => inputs.value.mode || 'auto')
const isAuto = computed(() => mode.value === 'auto')
const warnings = computed(() => session.value?.warnings || [])

const workflows = computed(() => catalog.value?.comfyui?.workflows || [])
const shots = computed(() => catalog.value?.shots || ['auto'])
const angles = computed(() => catalog.value?.angles || ['auto'])
// [{text, where}] rendered as `text "X" on Y` at the tail of the prompt.
const textsText = computed({
  get: () => (inputs.value.texts || [])
    .map(t => (t.where ? `${t.text} @ ${t.where}` : t.text)).join('\n'),
  set: val => patchInputs({
    texts: val.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
      const [text, where] = line.split('@').map(x => x.trim())
      return { text, where: where || '' }
    }),
  }),
})
const models = computed(() => catalog.value?.llm?.ollama?.models || [])
const vocabMissing = computed(() => catalog.value && !catalog.value.wd14_vocab?.imported)

// One press in AUTO, so the button names the whole job rather than the step.
const ctaLabel = computed(() => (
  isAuto.value && nextStep.value !== 'done'
    ? t('muse.cta.auto')
    : t(`muse.cta.${nextStep.value}`)
))
const ctaBlocked = computed(() => needs.value.length > 0 || nextStep.value === 'done')

const boardTracks = computed(() => ['background', 'person'])
const isJa = computed(() => String(locale.value).startsWith('ja'))

function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }

// Same string↔list bridge Admin uses for its exclusion lists.
const mustTagsText = computed({
  get: () => (inputs.value.must_tags || []).join(', '),
  set: val => patchInputs({
    must_tags: val.split(/[\n,]/).map(t => t.trim()).filter(Boolean),
  }),
})

// ── fetch ─────────────────────────────────────────────────────────────────
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

// ── lifecycle ─────────────────────────────────────────────────────────────
watch(() => props.show, async open => {
  if (!open) { closeStream(); return }
  try {
    if (!catalog.value) catalog.value = await api('/api/muse/catalog')
    if (!characters.value.length) {
      characters.value = (await api('/api/characters')).characters || []
    }
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
      light_model: suggested.light_model || '',
      board_workflow: suggested.board_workflow || '',
      final_workflow: suggested.final_workflow || '',
      locale: isJa.value ? 'ja' : 'en',
    }),
  })
  brainstormText.value = ''
  connectStream(session.value.session_id)
}

async function resetSession() {
  if (!window.confirm(t('muse.resetConfirm'))) return
  closeStream()
  session.value = null
  await startSession()
}

// ── SSE ───────────────────────────────────────────────────────────────────
function connectStream(id) {
  if (!id || eventSource) return
  eventSource = new EventSource(`/api/muse/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`)
  eventSource.onopen = () => { streamLive.value = true }
  eventSource.onmessage = async e => {
    let evt = null
    try { evt = JSON.parse(e.data) } catch { return }
    if (!evt?.type || evt.type === 'hello' || evt.type === 'ping') return
    await refresh()
  }
  eventSource.onerror = () => { streamLive.value = false }
  // The stream is the fast path; this is the one that survives it dying.
  startPoll()
}

function closeStream() {
  if (eventSource) { eventSource.close(); eventSource = null }
  streamLive.value = false
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (brainstormReader) { brainstormReader.cancel().catch(() => {}); brainstormReader = null }
}

function startPoll() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    const pending = stepState.value.board?.pending || stepState.value.render?.pending
    if (pending) await refresh()
  }, 2500)
}

async function refresh() {
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}`)
  } catch (err) { fail(err) }
}

// ── actions ───────────────────────────────────────────────────────────────
async function patchInputs(patch) {
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/inputs`, {
      method: 'PATCH', body: JSON.stringify(patch),
    })
  } catch (err) { fail(err) }
}

async function step(name) {
  if (!session.value || busy.value) return
  busy.value = true
  busyStep.value = name
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/${name}`, {
      // An empty body with a JSON content type is a 422 on routes that declare
      // one, and `/render` now does.
      method: 'POST', body: '{}',
    })
    if (name === 'merge') brainstormText.value = ''
  } catch (err) {
    fail(err)
  } finally {
    busy.value = false
    busyStep.value = ''
  }
}

async function runCta() {
  if (isAuto.value) return runToEnd()
  const name = nextStep.value
  if (name === 'brainstorm') return runBrainstorm()
  if (name === 'done') return
  await step(name)
}

/*
 * AUTO: one press, the whole chain.
 *
 * Two steps do not fit the "post and read the answer back" shape and have to be
 * waited for by hand — the board queues six renders that land later, and the
 * brainstorm streams. Everything else is a single call, so the loop just keeps
 * asking the server what comes next until nothing does.
 */
const autoRunning = ref(false)

async function runToEnd() {
  if (autoRunning.value || ctaBlocked.value) return
  autoRunning.value = true
  try {
    for (let guard = 0; guard < 40; guard++) {
      const name = nextStep.value
      if (name === 'done') break
      if (name === 'brainstorm') await runBrainstorm()
      else await step(name)
      if (name === 'board') await waitFor(() => !stepState.value.board?.pending, 900)
      // A step that refuses to advance would otherwise spin here forever.
      if (nextStep.value === name && name !== 'render') break
    }
  } catch (err) { fail(err) } finally { autoRunning.value = false }
}

async function waitFor(ready, tries) {
  for (let i = 0; i < tries; i++) {
    if (ready()) return true
    await new Promise(r => setTimeout(r, 2000))
    await refresh()
  }
  return false
}

async function pickCharacter(id) {
  if (!session.value) return
  busy.value = true
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/character`, {
      method: 'POST', body: JSON.stringify({ character_id: id }),
    })
    showPicker.value = false
  } catch (err) { fail(err) } finally { busy.value = false }
}

// Drawing a character now belongs to the gallery, which owns the whole screen
// and can show what came back. Picking one here just closes it.
async function pickFromGallery(id) {
  await pickCharacter(id)
  showPicker.value = false
}

async function reloadCharacters() {
  try { characters.value = (await api('/api/characters')).characters || [] } catch (err) { fail(err) }
}

async function setSlot({ slot, tags }) {
  if (!session.value) return
  busy.value = true
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/slots`, {
      method: 'POST', body: JSON.stringify({ slot, tags }),
    })
  } catch (err) { fail(err) } finally { busy.value = false }
}

async function rejectTag(tag) {
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/reject-tags`, {
      method: 'POST', body: JSON.stringify({ tags: [tag] }),
    })
  } catch (err) { fail(err) }
}

async function restoreTag(tag) {
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/reject-tags`, {
      method: 'POST', body: JSON.stringify({ tags: [tag], remove: true }),
    })
    // Un-rejecting cannot put the chip back on its own — the tag has to be
    // retrieved again. That is two vector searches, not a render.
    await step('tags')
  } catch (err) { fail(err) }
}

// ── brainstorm ────────────────────────────────────────────────────────────
async function runBrainstorm() {
  if (!session.value || brainstorming.value) return
  brainstorming.value = true
  brainstormText.value = ''
  try {
    const { job_id } = await api(`/api/muse/sessions/${session.value.session_id}/brainstorm`, {
      method: 'POST',
    })
    const resp = await fetch(`/api/inspire/brainstorm/${job_id}/stream`)
    if (!resp.ok || !resp.body) throw new Error(`stream ${resp.status}`)
    brainstormReader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await brainstormReader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        let evt = null
        try { evt = JSON.parse(line.slice(6)) } catch { continue }
        if (evt.type === 'token') brainstormText.value += evt.text || ''
        else if (evt.type === 'error') throw new Error(evt.message || 'brainstorm failed')
      }
    }
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/brainstorm/record`,
      { method: 'POST', body: JSON.stringify({ markdown: brainstormText.value }) },
    )
  } catch (err) {
    fail(err)
  } finally {
    brainstormReader = null
    brainstorming.value = false
  }
}

async function chooseScene(index) {
  if (!session.value) return
  busy.value = true
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/scene`, {
      method: 'POST', body: JSON.stringify({ index }),
    })
  } catch (err) { fail(err) } finally { busy.value = false }
}

function sendFinalToRefine() {
  const first = finals.value.find(f => f.image_id) || finals.value[0]
  if (!first?.positive) return
  emit('send-to-refine-direct', {
    shas: first.image_id ? [first.image_id] : [],
    directPrompt: first.positive,
    directNegativePrompt: first.negative || '',
    source: 'muse',
  })
}

function close() { emit('update:show', false) }
</script>

<template>
  <div
    v-if="show"
    class="muse-root fixed inset-0 flex items-stretch justify-center bg-black/70 backdrop-blur-sm p-3"
    @mousedown.self="close"
  >
    <div class="sb-shell w-full max-w-[1500px] flex flex-col min-h-0">
      <!-- header -->
      <header class="flex items-center justify-between gap-3 px-4 py-3 sb-hairline shrink-0">
        <div class="min-w-0">
          <h2 class="sb-display text-base text-[var(--sb-amber)]">{{ t('muse.title') }}</h2>
          <p class="text-[11px] text-[var(--sb-muted)] truncate">{{ t('muse.subtitle') }}</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <!-- AUTO walks the whole chain and draws every idea; MANUAL stops
               after each step and is where the tuning knobs live. -->
          <div class="sb-seg">
            <button
              v-for="m in ['auto', 'manual']"
              :key="m"
              type="button"
              class="sb-seg-btn"
              :class="mode === m ? 'is-on-teal' : ''"
              :disabled="busy || autoRunning"
              @click="patchInputs({ mode: m })"
            >{{ t(`muse.mode.${m}`) }}</button>
          </div>
          <span class="text-[10px] text-[var(--sb-faint)]">SSE {{ streamLive ? '●' : '○' }}</span>
          <button class="sb-btn" :disabled="busy" @click="resetSession">{{ t('muse.reset') }}</button>
          <button class="sb-icon-btn" :title="t('muse.close')" @click="close">✕</button>
        </div>
      </header>

      <div class="flex-1 grid grid-cols-1 md:grid-cols-[320px_1fr] min-h-0">
        <!-- ── left: inputs ── -->
        <aside class="border-r border-white/5 overflow-y-auto p-3 space-y-4 min-h-0">
          <section class="space-y-2">
            <p class="sb-label">{{ t('muse.character') }}</p>
            <button
              type="button"
              class="w-full flex items-center gap-2 p-2 rounded-lg border border-white/10 hover:border-white/25 text-left"
              @click="showPicker = true"
            >
              <img
                v-if="character?.board?.portrait || character?.board?.sheet"
                :src="thumb(character.board.portrait || character.board.sheet)"
                class="w-14 h-[74px] rounded object-cover shrink-0" alt=""
              />
              <span v-else class="w-14 h-[74px] rounded bg-black/40 shrink-0"></span>
              <span class="min-w-0">
                <span class="block text-xs text-gray-200 truncate">
                  {{ (isJa ? character?.name_ja : character?.name) || t('muse.noCharacter') }}
                </span>
                <span class="block text-[10px] text-gray-500 truncate">
                  {{ t('characters.open') }}
                </span>
              </span>
            </button>
            <div v-if="character?.identity_tags?.length" class="flex flex-wrap gap-1">
              <span
                v-for="tag in character.identity_tags"
                :key="tag"
                class="px-1.5 py-0.5 rounded bg-teal-900/40 border border-teal-600/30 text-[10px] font-mono text-teal-200"
              >{{ tag }}</span>
            </div>
          </section>

          <section class="space-y-1">
            <p class="sb-label">{{ t('muse.theme') }}</p>
            <textarea
              class="sb-textarea"
              rows="3"
              :placeholder="t('muse.themePlaceholder')"
              :value="inputs.theme"
              @change="patchInputs({ theme: $event.target.value })"
            ></textarea>
          </section>

          <section class="space-y-2">
            <div>
              <p class="sb-label mb-1">{{ t('muse.shot') }}</p>
              <select class="sb-select" :value="inputs.shot"
                      @change="patchInputs({ shot: $event.target.value })">
                <option v-for="s in shots" :key="s" :value="s">{{ t(`muse.shots.${s}`) }}</option>
              </select>
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.shotHint') }}</p>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.angle') }}</p>
              <select class="sb-select" :value="inputs.angle"
                      @change="patchInputs({ angle: $event.target.value })">
                <option v-for="a in angles" :key="a" :value="a">{{ t(`muse.angles.${a}`) }}</option>
              </select>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.style') }}</p>
              <input class="sb-input" type="text"
                     :value="inputs.style"
                     @change="patchInputs({ style: $event.target.value })" />
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.effect') }}</p>
              <input class="sb-input" type="text"
                     :value="inputs.effect"
                     @change="patchInputs({ effect: $event.target.value })" />
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.effectHint') }}</p>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.texts') }}</p>
              <textarea class="sb-textarea" rows="2"
                        :placeholder="t('muse.textsPlaceholder')"
                        :value="textsText"
                        @change="textsText = $event.target.value"></textarea>
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.textsHint') }}</p>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.mustTags') }}</p>
              <textarea
                class="sb-textarea"
                rows="2"
                :placeholder="t('muse.mustTagsPlaceholder')"
                :value="mustTagsText"
                @change="mustTagsText = $event.target.value"
              ></textarea>
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.mustTagsHint') }}</p>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.lightModel') }}</p>
              <select class="sb-select" :value="inputs.light_model"
                      @change="patchInputs({ light_model: $event.target.value })">
                <option value="">—</option>
                <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.boardWorkflow') }}</p>
              <select class="sb-select" :value="inputs.board_workflow"
                      @change="patchInputs({ board_workflow: $event.target.value })">
                <option value="">—</option>
                <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
              </select>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.finalWorkflow') }}</p>
              <select class="sb-select" :value="inputs.final_workflow"
                      @change="patchInputs({ final_workflow: $event.target.value })">
                <option value="">—</option>
                <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
              </select>
            </div>
          </section>

          <!-- The knobs. In AUTO their defaults are the point; showing them
               turns the first screen into a settings page. -->
          <details v-if="!isAuto" class="space-y-2">
            <summary class="sb-label cursor-pointer">{{ t('muse.boardParams') }}</summary>
            <div class="grid grid-cols-2 gap-2 pt-2">
              <label class="text-[10px] text-gray-500">
                {{ t('muse.boardSize') }}
                <input type="number" class="sb-input" min="256" max="2048" step="64"
                       :value="inputs.board_width"
                       @change="patchInputs({ board_width: +$event.target.value, board_height: +$event.target.value })" />
              </label>
              <label class="text-[10px] text-gray-500">
                {{ t('muse.boardSteps') }}
                <input type="number" class="sb-input" min="1" max="40"
                       :value="inputs.board_steps"
                       @change="patchInputs({ board_steps: +$event.target.value })" />
              </label>
              <label class="text-[10px] text-gray-500">
                {{ t('muse.boardCfg') }}
                <input type="number" class="sb-input" min="0" max="30" step="0.5"
                       :value="inputs.board_cfg"
                       @change="patchInputs({ board_cfg: +$event.target.value })" />
              </label>
              <label class="text-[10px] text-gray-500">
                {{ t('muse.boardCount') }}
                <input type="number" class="sb-input" min="1" max="4"
                       :value="inputs.board_count"
                       @change="patchInputs({ board_count: +$event.target.value })" />
              </label>
            </div>
          </details>

          <details v-if="!isAuto" class="space-y-2">
            <summary class="sb-label cursor-pointer">{{ t('muse.mergeParams') }}</summary>
            <div class="pt-2 space-y-3">
              <label class="block text-[10px] text-gray-500">
                {{ t('muse.characterWeight') }}
                <span class="float-right font-mono text-teal-400">
                  {{ Math.round((inputs.character_weight ?? 0.5) * 100) }}%
                </span>
                <input type="range" class="w-full" min="0" max="1" step="0.05"
                       :value="inputs.character_weight"
                       @change="patchInputs({ character_weight: +$event.target.value })" />
                <span class="block text-[10px] text-gray-600">{{ t('muse.characterWeightHint') }}</span>
              </label>
              <label class="block text-[10px] text-gray-500">
                {{ t('muse.commonRatio') }}
                <span class="float-right font-mono text-teal-400">{{ inputs.merge_common_ratio }}</span>
                <input type="range" class="w-full" min="0" max="1" step="0.05"
                       :value="inputs.merge_common_ratio"
                       @change="patchInputs({ merge_common_ratio: +$event.target.value })" />
              </label>
              <label class="block text-[10px] text-gray-500">
                {{ t('muse.uniqueCount') }}
                <input type="number" class="sb-input" min="1" max="100"
                       :value="inputs.merge_unique_count"
                       @change="patchInputs({ merge_unique_count: +$event.target.value })" />
              </label>
              <label class="flex items-start gap-2 text-[10px] text-gray-500">
                <input type="checkbox" :checked="inputs.vocab_supplement"
                       @change="patchInputs({ vocab_supplement: $event.target.checked })" />
                <span>
                  {{ t('muse.vocabSupplement') }}
                  <span class="block text-gray-600">{{ t('muse.vocabSupplementHint') }}</span>
                </span>
              </label>
              <label class="block text-[10px] text-gray-500">
                {{ t('muse.topupPicks') }}
                <input type="number" class="sb-input" min="0" max="15"
                       :value="inputs.topup_picks"
                       @change="patchInputs({ topup_picks: +$event.target.value })" />
                <span class="block text-[10px] text-gray-600">{{ t('muse.topupPicksHint') }}</span>
              </label>
              <label class="block text-[10px] text-gray-500">
                {{ t('muse.harvestThreshold') }}
                <span class="float-right font-mono text-teal-400">{{ inputs.harvest_threshold }}</span>
                <input type="range" class="w-full" min="0.05" max="0.9" step="0.05"
                       :value="inputs.harvest_threshold"
                       @change="patchInputs({ harvest_threshold: +$event.target.value })" />
              </label>
              <label class="flex items-start gap-2 text-[10px] text-gray-500">
                <input type="checkbox" :checked="inputs.harvest_rerank"
                       @change="patchInputs({ harvest_rerank: $event.target.checked })" />
                <span>
                  {{ t('muse.harvestRerank') }}
                  <span class="block text-gray-600">{{ t('muse.harvestRerankHint') }}</span>
                </span>
              </label>
              <label class="flex items-center gap-2 text-[10px] text-gray-500">
                <input type="checkbox" :checked="inputs.drop_rating_tags"
                       @change="patchInputs({ drop_rating_tags: $event.target.checked })" />
                {{ t('muse.dropRatingTags') }}
              </label>
              <label class="flex items-center gap-2 text-[10px] text-gray-500">
                <input type="checkbox" :checked="inputs.drop_character_tags"
                       @change="patchInputs({ drop_character_tags: $event.target.checked })" />
                {{ t('muse.dropCharacterTags') }}
              </label>
              <label class="flex items-start gap-2 text-[10px] text-gray-500">
                <input type="checkbox" :checked="inputs.llm_cleanup"
                       @change="patchInputs({ llm_cleanup: $event.target.checked })" />
                <span>
                  {{ t('muse.llmCleanup') }}
                  <span class="block text-gray-600">{{ t('muse.llmCleanupHint') }}</span>
                </span>
              </label>
            </div>
          </details>
        </aside>

        <!-- ── right: the cascade ── -->
        <main class="overflow-y-auto p-4 min-h-0 flex flex-col gap-4">
          <div v-if="vocabMissing" class="sb-shell p-3 text-xs text-amber-300 border-amber-600/30">
            {{ t('muse.warn.vocabMissing') }}
          </div>
          <div v-if="comfyOffline" class="sb-shell p-3 text-xs text-rose-300 border-rose-600/30">
            {{ t('muse.warn.comfyOffline') }}
          </div>
          <div v-for="w in warnings" :key="w" class="sb-shell p-3 text-xs text-amber-300 border-amber-600/30">
            {{ w }}
          </div>

          <!-- step strip + CTA -->
          <div class="flex items-center gap-2 flex-wrap">
            <span
              v-for="s in steps"
              :key="s"
              class="px-2 py-0.5 rounded text-[10px] border"
              :class="stepState[s]?.done
                ? 'border-teal-500/40 bg-teal-900/30 text-teal-200'
                : stepState[s]?.pending
                  ? 'border-amber-500/40 bg-amber-900/30 text-amber-200'
                  : 'border-white/10 text-gray-500'"
            >{{ t(`muse.steps.${s}`) }}<span v-if="stepState[s]?.detail" class="ml-1 opacity-60">{{ stepState[s].detail }}</span></span>
          </div>

          <div class="flex items-center gap-3">
            <button
              class="px-4 py-2 rounded-lg bg-teal-800/70 hover:bg-teal-700/80 border border-teal-500/40 text-sm text-teal-100 disabled:opacity-40"
              :disabled="busy || brainstorming || autoRunning || ctaBlocked"
              @click="runCta"
            >{{ busy || autoRunning ? '…' : ctaLabel }}</button>
            <p v-if="autoRunning" class="text-[11px] text-teal-300">
              {{ t(`muse.cta.${nextStep}`) }}
            </p>
            <p v-if="needs.length" class="text-[11px] text-amber-400">
              {{ t('muse.warn.needs', { items: needs.map(n => t(`muse.needs.${n}`)).join(' / ') }) }}
            </p>
          </div>

          <!-- In AUTO the pictures are the answer, so they come first and the
               working is folded behind them; in MANUAL the working IS the
               product and stays laid out in order. -->
          <component
            :is="isAuto ? 'details' : 'div'"
            :class="isAuto ? 'order-2' : ''"
          >
            <summary v-if="isAuto" class="sb-label cursor-pointer select-none mb-3">
              {{ t('muse.workings') }}
            </summary>
            <!-- The layout lives here rather than on the <details>: a flex
                 <details> stops hiding its own contents in some engines. -->
            <div class="flex flex-col gap-4">

          <!-- S1 compose -->
          <section v-if="Object.keys(slotTags).length" class="sb-shell p-3">
            <p class="sb-label mb-2">{{ t('muse.compose.title') }}</p>
            <p class="text-[10px] text-gray-600 mb-2">{{ t('muse.compose.hint') }}</p>
            <SlotEditor
              :slots="slotTags"
              :spec="slotSpec"
              :busy="busy"
              @set="setSlot"
            />
          </section>

          <!-- S4 board -->
          <section v-if="board.background?.length || board.person?.length" class="sb-shell p-3">
            <div class="flex items-center justify-between mb-2">
              <p class="sb-label">{{ t('muse.board.title') }}</p>
              <button class="sb-btn" :disabled="busy" @click="step('board')">
                {{ t('muse.board.redraw') }}
              </button>
            </div>
            <div v-for="track in boardTracks" :key="track" class="mb-3 last:mb-0">
              <p class="text-[11px] text-gray-400 mb-1">{{ t(`muse.board.${track}`) }}</p>
              <div class="flex gap-2 flex-wrap">
                <div
                  v-for="slot in board[track] || []"
                  :key="`${track}-${slot.seed_index}`"
                  class="w-28 h-28 rounded border border-white/10 bg-black/40 overflow-hidden flex items-center justify-center"
                >
                  <img
                    v-if="slot.image_id"
                    :src="thumb(slot.image_id)"
                    class="w-full h-full object-cover cursor-pointer"
                    :alt="`${track} ${slot.seed_index}`"
                    @click="emit('select-image', slot.image_id)"
                  />
                  <span v-else class="text-[10px] text-gray-600 animate-pulse">
                    {{ t('muse.board.pending') }}
                  </span>
                </div>
              </div>
            </div>
          </section>

          <!-- S5 harvest -->
          <section v-if="harvest.background?.length || harvest.person?.length" class="sb-shell p-3">
            <p class="sb-label mb-2">{{ t('muse.harvest.title') }}</p>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div v-for="track in boardTracks" :key="track">
                <p class="text-[11px] text-gray-400 mb-1">
                  {{ t(track === 'background' ? 'muse.harvest.fromBackground' : 'muse.harvest.fromPerson') }}
                </p>
                <div class="flex flex-wrap gap-1">
                  <span
                    v-for="row in harvest[track] || []"
                    :key="row.tag"
                    class="px-1.5 py-0.5 rounded border text-[10px] font-mono"
                    :class="row.agreement >= 0.99
                      ? 'border-cyan-500/40 bg-cyan-900/30 text-cyan-200'
                      : 'border-white/10 bg-black/30 text-gray-400'"
                    :title="`score ${row.score} · ${row.count} image(s)`"
                  >{{ row.tag }}</span>
                </div>
                <div v-if="(harvestDropped[track] || []).length" class="mt-2">
                  <p class="sb-label mb-1">{{ t('muse.harvest.cleaned') }}</p>
                  <div class="flex flex-wrap gap-1">
                    <span
                      v-for="row in harvestDropped[track]"
                      :key="row.tag"
                      class="px-1.5 py-0.5 rounded border border-rose-800/30 bg-rose-950/20 text-[10px] font-mono text-rose-400/60 line-through"
                      :title="t(`muse.harvest.reason.${row.reason}`)"
                    >{{ row.tag }}</span>
                  </div>
                  <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.harvest.cleanedHint') }}</p>
                </div>
              </div>
            </div>
          </section>

          <!-- S4 top-up -->
          <section v-if="topupCandidates.length || topup.length" class="sb-shell p-3">
            <p class="sb-label mb-1">{{ t('muse.topup.title') }}</p>
            <p class="text-[10px] text-gray-600 mb-2">{{ t('muse.topup.hint') }}</p>
            <div v-if="topup.length" class="flex flex-wrap gap-1 mb-2">
              <span
                v-for="row in topup"
                :key="row.tag"
                class="px-1.5 py-0.5 rounded border border-violet-500/40 bg-violet-900/30 text-[10px] font-mono text-violet-200"
                :title="row.why"
              >{{ row.tag }}</span>
            </div>
            <p v-else class="text-[11px] text-gray-500 mb-2">{{ t('muse.topup.none') }}</p>
            <details v-if="topupCandidates.length">
              <summary class="sb-label cursor-pointer">
                {{ t('muse.topup.candidates') }} ({{ topupCandidates.length }})
              </summary>
              <div class="flex flex-wrap gap-1 mt-1">
                <span
                  v-for="c in topupCandidates"
                  :key="c.tag"
                  class="px-1.5 py-0.5 rounded border border-white/10 bg-black/30 text-[10px] font-mono text-gray-500"
                  :title="`score ${c.score}`"
                >{{ c.tag }}</span>
              </div>
            </details>
          </section>

          <!-- S6 merge -->
          <section v-if="merged.tags?.length" class="sb-shell p-3">
            <p class="sb-label mb-2">{{ t('muse.merge.title') }}</p>
            <pre v-if="merged.positive" class="text-[11px] font-mono text-gray-300 whitespace-pre-wrap mb-3 border-l-2 border-white/10 pl-2">{{ merged.positive }}</pre>
            <div class="flex flex-wrap gap-1 mb-3">
              <span
                v-for="tag in merged.tags"
                :key="tag"
                class="px-1.5 py-0.5 rounded border text-[10px] font-mono"
                :class="(merged.forced || []).includes(tag)
                  ? 'border-rose-400/50 bg-rose-900/40 text-rose-100'
                  : (merged.protected || []).includes(tag)
                    ? 'border-teal-400/50 bg-teal-800/40 text-teal-100'
                  : (merged.reinforcements || []).includes(tag)
                    ? 'border-violet-500/40 bg-violet-900/30 text-violet-200'
                    : 'border-white/10 bg-black/30 text-gray-300'"
              >{{ tag }}</span>
            </div>
            <div v-if="merged.framing_dropped?.length" class="mb-3">
              <p class="sb-label mb-1">
                {{ t('muse.merge.framingDropped', { shot: t(`muse.shots.${merged.shot || 'auto'}`) }) }}
              </p>
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="tag in merged.framing_dropped"
                  :key="tag"
                  class="px-1.5 py-0.5 rounded border border-sky-800/30 bg-sky-950/30 text-[10px] font-mono text-sky-400/60 line-through"
                >{{ tag }}</span>
              </div>
            </div>
            <div v-if="merged.evicted?.length" class="mb-3">
              <p class="sb-label mb-1">{{ t('muse.merge.evicted') }}</p>
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="tag in merged.evicted"
                  :key="tag"
                  class="px-1.5 py-0.5 rounded border border-rose-700/30 bg-rose-950/30 text-[10px] font-mono text-rose-400/70 line-through"
                >{{ tag }}</span>
              </div>
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.merge.evictedHint') }}</p>
            </div>
            <div v-if="merged.removed?.length">
              <p class="sb-label mb-1">{{ t('muse.merge.removed') }}</p>
              <div class="flex flex-wrap gap-1">
                <span
                  v-for="tag in merged.removed"
                  :key="tag"
                  class="px-1.5 py-0.5 rounded border border-amber-700/30 bg-amber-950/30 text-[10px] font-mono text-amber-500/70 line-through"
                >{{ tag }}</span>
              </div>
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.merge.removedHint') }}</p>
            </div>
          </section>

          <!-- S7 brainstorm -->
          <section v-if="brainstormText || scene.candidates?.length" class="sb-shell p-3">
            <div class="flex items-center justify-between mb-2">
              <p class="sb-label">{{ t('muse.brainstorm.title') }}</p>
              <button class="sb-btn" :disabled="brainstorming || busy" @click="runBrainstorm">
                {{ t('muse.brainstorm.regenerate') }}
              </button>
            </div>
            <pre
              v-if="brainstorming"
              class="text-[11px] text-gray-300 whitespace-pre-wrap max-h-52 overflow-y-auto"
            >{{ brainstormText }}<span class="animate-pulse">▌</span></pre>
            <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-2">
              <article
                v-for="(card, i) in scene.candidates || []"
                :key="i"
                class="rounded-lg border p-2"
                :class="scene.chosen === i ? 'border-teal-400/60 bg-teal-950/20' : 'border-white/10'"
              >
                <p class="text-[11px] text-[var(--sb-amber)] mb-1">{{ card.title }}</p>
                <p class="text-[11px] text-gray-400 whitespace-pre-wrap max-h-32 overflow-y-auto">{{ card.body }}</p>
                <button class="sb-btn mt-2" :disabled="busy" @click="chooseScene(i)">
                  {{ scene.chosen === i ? t('muse.brainstorm.chosen') : t('muse.brainstorm.pick') }}
                </button>
              </article>
            </div>
            <p v-if="scene.text" class="sb-prose mt-3 text-[12px] border-t border-white/5 pt-2">{{ scene.text }}</p>
          </section>

            </div>
          </component>

          <!-- S8 finals — one per idea in AUTO. The point of having four ideas
               is seeing all four drawn, so they get the room to be looked at. -->
          <section v-if="finals.length" class="sb-shell p-3" :class="isAuto ? 'order-1' : ''">
            <div class="flex items-baseline gap-2 mb-2">
              <p class="sb-label mr-auto">{{ t('muse.render.title') }}</p>
              <p class="text-[10px] text-gray-500">{{ stepState.render?.detail }}</p>
              <button class="sb-btn !py-0.5 !text-[10px]" @click="sendFinalToRefine">→ Refine</button>
            </div>
            <div
              class="grid gap-3"
              :class="finals.length > 1 ? 'grid-cols-2 xl:grid-cols-4' : 'grid-cols-1'"
            >
              <figure v-for="(f, i) in finals" :key="f.job_id || i" class="min-w-0">
                <div class="rounded-lg border border-white/10 bg-black/40 overflow-hidden
                            aspect-[3/4] flex items-center justify-center">
                  <img
                    v-if="f.image_id"
                    :src="thumb(f.image_id)"
                    class="w-full h-full object-cover cursor-pointer
                           transition-transform hover:scale-[1.03]"
                    alt=""
                    @click="emit('select-image', f.image_id)"
                  />
                  <span v-else class="text-[10px] text-gray-600 animate-pulse">
                    {{ t('muse.board.pending') }}
                  </span>
                </div>
                <figcaption class="mt-1">
                  <p class="text-[11px] text-gray-300 truncate" :title="f.title">
                    {{ f.title || t('muse.render.title') }}
                  </p>
                  <details class="mt-0.5">
                    <summary class="text-[10px] text-gray-600 cursor-pointer">
                      {{ t('muse.render.positive') }}
                    </summary>
                    <p class="text-[10px] font-mono text-gray-400 whitespace-pre-wrap
                              break-words max-h-40 overflow-y-auto mt-1">{{ f.positive }}</p>
                  </details>
                </figcaption>
              </figure>
            </div>
          </section>
        </main>
      </div>
    </div>

    <CharacterGallery
      :show="showPicker"
      :selected-id="inputs.character_id || ''"
      :workflows="workflows"
      :workflow="inputs.board_workflow || inputs.final_workflow || ''"
      @pick="pickFromGallery"
      @close="showPicker = false; reloadCharacters()"
      @toast="emit('toast', $event)"
      @update:workflow="patchInputs({ board_workflow: $event })"
    />
  </div>
</template>

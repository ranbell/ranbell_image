<script setup>
/*
 * Muse — theme + character → four drafts → pick one → redraw it three times,
 * the model looking at what it drew each time.
 *
 * The screen shows one act at a time. The pictures are the product, so whatever
 * act is running gets the whole stage: while a draft renders that is the live
 * latent, blown up, because watching it form is both the entertainment and the
 * only way to judge whether to stop; while choosing, it is four large cards,
 * because picking between four pictures at thumbnail size is guesswork. The
 * setup form is a drawer once a run has started — none of it will be touched
 * again, and it was taking a fifth of the window to say so.
 *
 * The server owns all state; every action assigns `session.value = await api()`.
 * Local state is only what has nowhere to live on the server: the live preview
 * frame, the set of drafts ticked but not yet sent, and which past act the user
 * is looking back at.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import CharacterGallery from './CharacterGallery.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
  // Sampled rather than watched: the map churns on every job event in the app.
  getJobsMap: { type: Function, default: () => () => new Map() },
})
const emit = defineEmits(['update:show', 'toast', 'select-image'])
const { t, locale } = useI18n()

// ── state ─────────────────────────────────────────────────────────────────
const session = ref(null)
const catalog = ref(null)
const busy = ref(false)
const streamLive = ref(false)
const showPicker = ref(false)
const showSettings = ref(false)
const preview = ref('')            // latest latent frame, as a data URL
const picked = ref([])
const viewAct = ref('')            // a past act the user clicked back to
const job = ref(null)              // the running render, for progress
const elapsed = ref(0)

let eventSource = null
let pollTimer = null
let startedAt = 0

// ── derived ───────────────────────────────────────────────────────────────
const inputs = computed(() => session.value?.inputs || {})
const stepState = computed(() => session.value?.step_state || {})
const needs = computed(() => session.value?.needs || [])
const character = computed(() => session.value?.character || null)
const draft = computed(() => session.value?.draft || {})
const draftImages = computed(() => draft.value.images || [])
const chains = computed(() => session.value?.chains || [])
const warnings = computed(() => session.value?.warnings || [])

const workflows = computed(() => catalog.value?.comfyui?.workflows || [])
const models = computed(() => catalog.value?.llm?.models || [])
const visionModels = computed(() => catalog.value?.llm?.vision_models || [])
const modelIsBlind = computed(() => {
  const m = inputs.value.model
  return Boolean(m) && visionModels.value.length > 0 && !visionModels.value.includes(m)
})
const isJa = computed(() => String(locale.value).startsWith('ja'))

/* Which act the run is actually in. `viewAct` lets the user look back at one
   that has finished, but a new act always pulls the view forward. */
const act = computed(() => {
  if (!session.value) return 'setup'
  if (stepState.value.draft?.pending) return 'drafting'
  if (!draftImages.value.length) return 'setup'
  if (!chains.value.length) return 'choose'
  return stepState.value.refine?.done ? 'done' : 'refining'
})
const shown = computed(() => viewAct.value || act.value)
watch(act, () => { viewAct.value = '' })

const RAIL = [
  { key: 'draft', acts: ['setup', 'drafting'] },
  { key: 'choose', acts: ['choose'] },
  { key: 'refine', acts: ['refining', 'done'] },
]
const railIndex = computed(() => RAIL.findIndex(s => s.acts.includes(act.value)))
const shownIndex = computed(() => RAIL.findIndex(s => s.acts.includes(shown.value)))

const ctaLabel = computed(() => {
  if (act.value === 'choose') return t('muse.cta.refine')
  if (act.value === 'done') return t('muse.cta.done')
  return t('muse.cta.draft')
})
const ctaBlocked = computed(() =>
  busy.value || needs.value.length > 0 || act.value === 'drafting' ||
  act.value === 'refining' || act.value === 'done' ||
  (act.value === 'choose' && picked.value.length === 0))

/* The stage being drawn right now, and everything already finished, so the
   refine act can put one big and the rest in a strip. */
const stageFlow = computed(() => {
  const out = []
  for (const [ci, chain] of chains.value.entries()) {
    for (const [si, stage] of (chain.stages || []).entries()) {
      out.push({ ...stage, chain: ci, index: si, draft_index: chain.draft_index })
    }
  }
  return out
})
const landedStages = computed(() => stageFlow.value.filter(s => s.image_id))
const currentStage = computed(() =>
  stageFlow.value.find(s => !s.image_id) || landedStages.value.at(-1) || null)
const heroStage = computed(() => landedStages.value.at(-1) || null)

function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function full(sha) { return sha ? `/api/originals/${sha}` : '' }
function stageLabel(name) { return t(`muse.stage.${name}`) }
function clock(s) {
  const m = Math.floor(s / 60)
  return m ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

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
    }),
  })
  picked.value = []
  preview.value = ''
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

// ── SSE ───────────────────────────────────────────────────────────────────
function connectStream(id) {
  if (!id || eventSource) return
  eventSource = new EventSource(
    `/api/muse/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`)
  eventSource.onopen = () => { streamLive.value = true }
  eventSource.onmessage = async e => {
    let evt = null
    try { evt = JSON.parse(e.data) } catch { return }
    if (!evt?.type || evt.type === 'hello' || evt.type === 'ping') return
    // A preview carries its own payload; everything else means "refetch".
    if (evt.type === 'preview') {
      preview.value = `data:image/jpeg;base64,${evt.image}`
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
  // Ticks every second so the clock and the progress bar move, but only
  // re-reads the session every third tick: the stream already pushes an event
  // whenever anything lands, and this is the backstop for the stream dying, not
  // the primary path. A fifteen-minute run does not need 900 requests.
  let tick = 0
  pollTimer = setInterval(async () => {
    sampleJob()
    const running = stepState.value.draft?.pending || stepState.value.refine?.pending
    if (!running) { tick = 0; return }
    // Re-entering a panel whose run started before this tab did: anchor the
    // clock now rather than reporting the time since the epoch.
    if (!startedAt) startedAt = Date.now()
    elapsed.value = Math.round((Date.now() - startedAt) / 1000)
    if (++tick % 3 === 0) await refresh()
  }, 1000)
}

function sampleJob() {
  const id = draft.value.job_id
  const map = props.getJobsMap?.()
  job.value = (id && map?.get) ? (map.get(id) || null) : null
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

async function pickCharacter(id) {
  showPicker.value = false
  if (!session.value) return
  try {
    session.value = await api(`/api/muse/sessions/${session.value.session_id}/character`, {
      method: 'POST', body: JSON.stringify({ character_id: id }),
    })
  } catch (err) { fail(err) }
}

async function runStep() {
  if (!session.value || ctaBlocked.value) return
  const step = act.value === 'choose' ? 'refine' : 'draft'
  busy.value = true
  preview.value = ''
  startedAt = Date.now()
  elapsed.value = 0
  showSettings.value = false
  try {
    const body = step === 'refine' ? JSON.stringify({ drafts: picked.value }) : '{}'
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${step}`,
      { method: 'POST', body })
  } catch (err) { fail(err) } finally { busy.value = false }
}

async function cancelDraft() {
  if (!session.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/draft/cancel`, { method: 'POST' })
    preview.value = ''
    picked.value = []
  } catch (err) { fail(err) } finally { busy.value = false }
}

function togglePick(index) {
  const at = picked.value.indexOf(index)
  if (at >= 0) picked.value.splice(at, 1)
  else picked.value.push(index)
}

function lookBack(step) {
  const i = RAIL.findIndex(s => s.key === step)
  if (i < 0 || i > railIndex.value) return
  viewAct.value = i === railIndex.value ? '' : RAIL[i].acts[0]
}
</script>

<template>
  <div
    v-if="show"
    class="muse-root fixed inset-0 flex items-stretch justify-center bg-black/70 backdrop-blur-sm p-3"
    @mousedown.self="close"
  >
    <!-- `muse-root` is not decoration: it defines the --sb-* tokens that
         .sb-shell's background and every text-[var(--sb-*)] resolve against,
         and pins the z-index above the gallery. `items-stretch` is what gives
         the shell a height; centred, it sizes to its content and the inner
         overflow-y-auto never engages. -->
    <div class="sb-shell w-full max-w-[1500px] flex flex-col min-h-0">
      <!-- ── header ── -->
      <header class="flex items-center justify-between gap-3 px-4 py-3 sb-hairline shrink-0">
        <div class="min-w-0">
          <h2 class="sb-display text-base text-[var(--sb-amber)]">{{ t('muse.title') }}</h2>
          <p class="text-[11px] text-[var(--sb-muted)] truncate">{{ t('muse.subtitle') }}</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <span class="text-[10px] text-[var(--sb-faint)]">SSE {{ streamLive ? '●' : '○' }}</span>
          <button class="sb-btn" :disabled="busy" @click="showSettings = !showSettings">
            {{ t('muse.settings') }}
          </button>
          <button class="sb-btn" :disabled="busy" @click="resetSession">{{ t('muse.reset') }}</button>
          <button class="sb-icon-btn" :title="t('muse.close')" @click="close">✕</button>
        </div>
      </header>

      <!-- ── the three acts, and where we are ── -->
      <nav class="flex items-center gap-2 px-4 py-2 sb-hairline shrink-0 text-[11px]">
        <template v-for="(s, i) in RAIL" :key="s.key">
          <span v-if="i" class="flex-1 h-px bg-white/10"></span>
          <button
            type="button"
            class="px-2 py-0.5 rounded-full border transition-colors"
            :class="i === shownIndex
              ? 'border-[var(--sb-teal)] text-[var(--sb-teal)]'
              : i < railIndex
                ? 'border-white/15 text-gray-400 hover:text-gray-200'
                : 'border-white/5 text-[var(--sb-faint)] cursor-default'"
            :disabled="i > railIndex"
            @click="lookBack(s.key)"
          >{{ i + 1 }}. {{ t(`muse.act.${s.key}`) }}</button>
        </template>
      </nav>

      <p v-for="w in warnings" :key="w"
         class="px-4 py-1 text-[11px] text-amber-400 shrink-0">{{ w }}</p>
      <p v-if="comfyOffline" class="px-4 py-1 text-[11px] text-red-400 shrink-0">
        {{ t('muse.warn.comfyOffline') }}
      </p>

      <!-- ── the stage ── -->
      <main class="flex-1 overflow-y-auto min-h-0">
        <!-- 1 · setup ------------------------------------------------------->
        <section v-if="shown === 'setup'" class="max-w-2xl mx-auto p-6 space-y-5">
          <h3 class="sb-display text-lg text-[var(--sb-amber)]">{{ t('muse.setupTitle') }}</h3>

          <label class="block space-y-1">
            <span class="sb-label">{{ t('muse.theme') }}</span>
            <textarea
              class="sb-textarea text-sm" rows="5"
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
              <span v-if="character?.identity_tags?.length" class="flex flex-wrap gap-1 mt-1">
                <span
                  v-for="tag in character.identity_tags" :key="tag"
                  class="px-1.5 py-0.5 rounded bg-teal-900/40 border border-teal-600/30
                         text-[10px] font-mono text-teal-200"
                >{{ tag }}</span>
              </span>
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
              <span v-if="modelIsBlind" class="block text-[10px] text-amber-400 mt-1">
                {{ t('muse.notVision') }}
              </span>
            </label>
          </div>
        </section>

        <!-- 2 · drafting ---------------------------------------------------->
        <section v-else-if="shown === 'drafting'"
                 class="h-full flex flex-col items-center justify-center gap-4 p-6">
          <div class="relative">
            <img v-if="preview" :src="preview" alt=""
                 class="max-h-[58vh] rounded-lg border border-white/10 shadow-2xl" />
            <div v-else
                 class="w-[300px] h-[386px] rounded-lg border border-white/10 bg-black/40
                        flex items-center justify-center">
              <span class="text-[11px] text-[var(--sb-faint)] animate-pulse">…</span>
            </div>
          </div>

          <div class="w-full max-w-md space-y-2">
            <div class="flex items-baseline justify-between text-[11px]">
              <span class="text-gray-300">{{ job?.progress_text || t('muse.draft.pending') }}</span>
              <span class="text-[var(--sb-faint)]">{{ t('muse.elapsed', { s: clock(elapsed) }) }}</span>
            </div>
            <div class="h-1 rounded bg-white/10 overflow-hidden">
              <div class="h-full bg-[var(--sb-teal)] transition-all duration-300"
                   :style="{ width: `${Math.round((job?.progress || 0) * 100)}%` }"></div>
            </div>
            <div class="flex items-center justify-between gap-3 pt-1">
              <p class="text-[10px] text-[var(--sb-faint)] flex-1">{{ t('muse.draft.cancelHint') }}</p>
              <button class="sb-btn shrink-0" :disabled="busy" @click="cancelDraft">
                {{ t('muse.draft.cancel') }}
              </button>
            </div>
          </div>
        </section>

        <!-- 3 · choose ------------------------------------------------------>
        <section v-else-if="shown === 'choose'" class="p-4 space-y-3">
          <div class="flex items-baseline justify-between gap-3">
            <h3 class="sb-display text-lg text-[var(--sb-amber)]">{{ t('muse.chooseTitle') }}</h3>
            <span v-if="picked.length" class="text-[11px] text-[var(--sb-teal)]">
              {{ t('muse.chosen', { n: picked.length }) }}
            </span>
          </div>
          <p class="text-[11px] text-[var(--sb-muted)]">
            {{ t('muse.chooseHint', { n: draftImages.length }) }}
          </p>

          <div class="grid gap-4"
               :class="draftImages.length > 4 ? 'grid-cols-2 xl:grid-cols-4' : 'grid-cols-2'">
            <figure
              v-for="img in draftImages" :key="img.index"
              class="relative group cursor-pointer rounded-lg overflow-hidden border-2 transition-all"
              :class="picked.includes(img.index)
                ? 'border-[var(--sb-amber)] shadow-lg shadow-amber-900/20'
                : 'border-transparent opacity-80 hover:opacity-100'"
              @click="togglePick(img.index)"
            >
              <img :src="thumb(img.image_id)" class="w-full block" alt="" />
              <figcaption
                class="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2
                       px-2 py-1 bg-black/60 text-[10px]">
                <span class="text-gray-300">#{{ img.index + 1 }}</span>
                <span v-if="picked.includes(img.index)"
                      class="text-[var(--sb-amber)]">✓ {{ t('muse.draft.selected') }}</span>
              </figcaption>
              <button
                type="button"
                class="absolute top-2 right-2 px-1.5 py-0.5 rounded bg-black/60 text-[10px]
                       text-gray-300 opacity-0 group-hover:opacity-100"
                :title="t('muse.enlarge')"
                @click.stop="emit('select-image', img.image_id)"
              >⤢</button>
            </figure>
          </div>

          <details v-if="draft.prompt" class="text-[10px] text-[var(--sb-faint)]">
            <summary class="cursor-pointer">{{ t('muse.draft.prompt') }}</summary>
            <p class="whitespace-pre-wrap font-mono mt-1 text-gray-400">{{ draft.prompt }}</p>
          </details>
        </section>

        <!-- 4 · refining / done --------------------------------------------->
        <section v-else class="p-4 space-y-4">
          <div class="flex items-baseline justify-between gap-3">
            <h3 class="sb-display text-lg text-[var(--sb-amber)]">
              {{ shown === 'done' ? t('muse.doneTitle') : t('muse.refine.title') }}
            </h3>
            <span class="text-[11px] text-[var(--sb-faint)]">{{ stepState.refine?.detail }}</span>
          </div>

          <!-- the one being drawn, or the newest finished -->
          <div v-if="shown === 'refining'" class="flex flex-col items-center gap-3">
            <img v-if="preview" :src="preview" alt=""
                 class="max-h-[52vh] rounded-lg border border-white/10 shadow-2xl" />
            <img v-else-if="heroStage" :src="full(heroStage.image_id)" alt=""
                 class="max-h-[52vh] rounded-lg border border-white/10 shadow-2xl" />
            <div class="w-full max-w-md space-y-2">
              <div class="flex items-baseline justify-between text-[11px]">
                <span class="text-gray-300">
                  {{ t(preview ? 'muse.renderingStage' : 'muse.writingStage',
                       { stage: stageLabel(currentStage?.stage || 'reinforce') }) }}
                </span>
                <span class="text-[var(--sb-faint)]">{{ t('muse.elapsed', { s: clock(elapsed) }) }}</span>
              </div>
              <div class="h-1 rounded bg-white/10 overflow-hidden">
                <div class="h-full bg-[var(--sb-teal)] transition-all duration-500"
                     :style="{ width: `${Math.round(landedStages.length / Math.max(stageFlow.length, 1) * 100)}%` }"></div>
              </div>
            </div>
          </div>

          <article v-for="(chain, ci) in chains" :key="ci" class="space-y-2">
            <p v-if="chains.length > 1" class="text-[11px] text-[var(--sb-muted)]">
              {{ t('muse.draft.title') }} #{{ chain.draft_index + 1 }}
            </p>
            <div
              class="grid gap-3"
              :style="{ gridTemplateColumns:
                `repeat(${Math.min(chain.stages.length, 3)}, minmax(0, 1fr))` }"
            >
              <figure v-for="(stage, si) in chain.stages" :key="si" class="space-y-1 group relative">
                <img
                  v-if="stage.image_id" :src="thumb(stage.image_id)"
                  class="w-full rounded border border-white/10 cursor-pointer" alt=""
                  @click="emit('select-image', stage.image_id)"
                />
                <div v-else class="w-full aspect-[3/4] rounded bg-black/40 border border-white/10
                                   flex items-center justify-center text-[10px] text-[var(--sb-faint)]">
                  {{ t('muse.refine.pending') }}
                </div>
                <figcaption class="text-[10px] text-gray-400">{{ stageLabel(stage.stage) }}</figcaption>
                <details v-if="stage.prompt" class="text-[10px] text-[var(--sb-faint)]">
                  <summary class="cursor-pointer">prompt</summary>
                  <p class="whitespace-pre-wrap font-mono mt-1 text-gray-400">{{ stage.prompt }}</p>
                </details>
              </figure>
            </div>
            <details v-if="chain.wd14" class="text-[10px] text-[var(--sb-faint)]">
              <summary class="cursor-pointer">{{ t('muse.refine.wd14') }}</summary>
              <p class="font-mono mt-1 break-words text-gray-400">{{ chain.wd14 }}</p>
              <p class="mt-1">{{ t('muse.refine.wd14Hint') }}</p>
            </details>
          </article>
        </section>
      </main>

      <!-- ── settings drawer: everything that is not this act ── -->
      <section v-if="showSettings"
               class="shrink-0 max-h-[45vh] overflow-y-auto border-t border-white/10 p-4
                      grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px]">
        <label class="block col-span-2">
          <span class="sb-label">{{ t('muse.style') }}</span>
          <input class="sb-input" type="text" :value="inputs.style"
                 @change="patchInputs({ style: $event.target.value })" />
        </label>
        <label class="flex items-start gap-2 col-span-2 text-gray-400">
          <input type="checkbox" class="mt-0.5" :checked="inputs.think"
                 @change="patchInputs({ think: $event.target.checked })" />
          <span>
            {{ t('muse.think') }}
            <span class="block text-[10px] text-[var(--sb-faint)]">{{ t('muse.thinkHint') }}</span>
          </span>
        </label>

        <label class="block"><span class="sb-label">{{ t('muse.size') }}</span>
          <input class="sb-input" type="number" step="64" :value="inputs.width"
                 @change="patchInputs({ width: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">&nbsp;</span>
          <input class="sb-input" type="number" step="64" :value="inputs.height"
                 @change="patchInputs({ height: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.draftSteps') }}</span>
          <input class="sb-input" type="number" :value="inputs.draft_steps"
                 @change="patchInputs({ draft_steps: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.draftCfg') }}</span>
          <input class="sb-input" type="number" step="0.1" :value="inputs.draft_cfg"
                 @change="patchInputs({ draft_cfg: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.draftCount') }}</span>
          <input class="sb-input" type="number" :value="inputs.draft_count"
                 @change="patchInputs({ draft_count: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.refineStages') }}</span>
          <input class="sb-input" type="number" min="1" max="3" :value="inputs.refine_stages"
                 @change="patchInputs({ refine_stages: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.finalSteps') }}</span>
          <input class="sb-input" type="number" :value="inputs.final_steps"
                 @change="patchInputs({ final_steps: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.finalCfg') }}</span>
          <input class="sb-input" type="number" step="0.1" :value="inputs.final_cfg"
                 @change="patchInputs({ final_cfg: Number($event.target.value) })" /></label>
        <label class="block"><span class="sb-label">{{ t('muse.wd14Threshold') }}</span>
          <input class="sb-input" type="number" step="0.05" :value="inputs.wd14_threshold"
                 @change="patchInputs({ wd14_threshold: Number($event.target.value) })" /></label>

        <label class="flex items-center gap-2 text-gray-400">
          <input type="checkbox" :checked="inputs.drop_rating_tags"
                 @change="patchInputs({ drop_rating_tags: $event.target.checked })" />
          {{ t('muse.dropRatingTags') }}
        </label>
        <label class="flex items-center gap-2 text-gray-400">
          <input type="checkbox" :checked="inputs.drop_character_tags"
                 @change="patchInputs({ drop_character_tags: $event.target.checked })" />
          {{ t('muse.dropCharacterTags') }}
        </label>
        <label class="block col-span-2 md:col-span-4">
          <span class="sb-label">{{ t('muse.negative') }}</span>
          <textarea class="sb-textarea" rows="2" :value="inputs.negative_prompt"
                    @change="patchInputs({ negative_prompt: $event.target.value })"></textarea>
        </label>
        <details v-if="session?.brief" class="col-span-2 md:col-span-4 text-[10px] text-[var(--sb-faint)]">
          <summary class="cursor-pointer">{{ t('muse.brief') }}</summary>
          <pre class="whitespace-pre-wrap font-mono mt-1 max-h-40 overflow-y-auto text-gray-400">{{ session.brief }}</pre>
          <p class="mt-1">{{ t('muse.briefHint') }}</p>
        </details>
      </section>

      <!-- ── the one button ── -->
      <footer class="shrink-0 flex items-center gap-3 px-4 py-3 border-t border-white/10">
        <p v-if="needs.length" class="text-[11px] text-amber-400 flex-1">
          {{ needs.map(n => t(`muse.needs.${n}`)).join(' / ') }}
        </p>
        <p v-else class="text-[11px] text-[var(--sb-faint)] flex-1">
          {{ act === 'choose' ? t('muse.draft.hint') : '' }}
        </p>
        <button v-if="shown !== act" class="sb-btn shrink-0" @click="viewAct = ''">
          {{ t('muse.backToCurrent') }}
        </button>
        <button class="sb-btn shrink-0 px-6" :disabled="ctaBlocked" @click="runStep">
          {{ busy ? '…' : ctaLabel }}
        </button>
      </footer>
    </div>

    <CharacterGallery
      :show="showPicker"
      :selected-id="inputs.character_id"
      :workflows="workflows"
      :workflow="inputs.workflow"
      @pick="pickCharacter"
      @close="showPicker = false"
      @toast="emit('toast', $event)"
      @update:workflow="patchInputs({ workflow: $event })"
    />
  </div>
</template>

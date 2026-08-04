<script setup>
/*
 * Muse — theme + character → four drafts → pick one → redraw it three times,
 * the model looking at what it drew each time.
 *
 * The server owns all state; every action assigns `session.value = await api()`.
 * Two things are local and deliberately so: the live preview frame, which has
 * nowhere on the server to be refetched from, and the set of drafts ticked for
 * the next step, which is a selection in progress rather than a decision made.
 *
 * There is no AUTO mode. Picking a draft is the one judgement in this pipeline
 * that a model cannot make as well as a person glancing at four pictures.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'
import CharacterGallery from './CharacterGallery.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  comfyOffline: { type: Boolean, default: false },
})
const emit = defineEmits(['update:show', 'toast', 'select-image'])
const { t, locale } = useI18n()

// ── state ─────────────────────────────────────────────────────────────────
const session = ref(null)
const catalog = ref(null)          // environment; survives a session reset
const busy = ref(false)
const busyStep = ref('')
const streamLive = ref(false)
const showPicker = ref(false)
const showKnobs = ref(false)
// Latest latent frame, as a data URL. Replaced, never accumulated — the point
// is what it looks like now.
const preview = ref('')
const picked = ref([])

let eventSource = null
let pollTimer = null

// ── derived ───────────────────────────────────────────────────────────────
const inputs = computed(() => session.value?.inputs || {})
const stepState = computed(() => session.value?.step_state || {})
const nextStep = computed(() => session.value?.next_step || 'draft')
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
const drafting = computed(() => Boolean(draft.value.job_id) && Boolean(stepState.value.draft?.pending))
const ctaLabel = computed(() => t(`muse.cta.${nextStep.value}`))
const ctaBlocked = computed(() =>
  needs.value.length > 0 || busy.value || drafting.value ||
  (nextStep.value === 'refine' && picked.value.length === 0))

function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }

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
    // A preview carries its own payload; everything else just means "refetch".
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
  // The stream is the fast path; this is what survives it dying mid-render.
  pollTimer = setInterval(async () => {
    if (stepState.value.draft?.pending || stepState.value.refine?.pending) await refresh()
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
  const step = nextStep.value
  busy.value = true
  busyStep.value = step
  preview.value = ''
  try {
    const body = step === 'refine' ? JSON.stringify({ drafts: picked.value }) : '{}'
    session.value = await api(
      `/api/muse/sessions/${session.value.session_id}/${step}`,
      { method: 'POST', body },
    )
  } catch (err) { fail(err) } finally { busy.value = false; busyStep.value = '' }
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
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
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
          <span class="text-[10px] text-[var(--sb-faint)]">SSE {{ streamLive ? '●' : '○' }}</span>
          <button class="sb-btn" :disabled="busy" @click="resetSession">{{ t('muse.reset') }}</button>
          <button class="sb-icon-btn" :title="t('muse.close')" @click="close">✕</button>
        </div>
      </header>

      <div class="flex-1 grid grid-cols-1 md:grid-cols-[320px_1fr] min-h-0">
        <!-- ── left: what the run is ── -->
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
                <span class="block text-[10px] text-gray-500 truncate">{{ t('muse.pickCharacter') }}</span>
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
              class="sb-textarea" rows="3"
              :placeholder="t('muse.themePlaceholder')"
              :value="inputs.theme"
              @change="patchInputs({ theme: $event.target.value })"
            ></textarea>
          </section>

          <section class="space-y-2">
            <div>
              <p class="sb-label mb-1">{{ t('muse.workflow') }}</p>
              <select class="sb-select" :value="inputs.workflow"
                      @change="patchInputs({ workflow: $event.target.value })">
                <option value="">—</option>
                <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
              </select>
              <p class="text-[10px] text-gray-600 mt-1">{{ t('muse.workflowHint') }}</p>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.model') }}</p>
              <select class="sb-select" :value="inputs.model"
                      @change="patchInputs({ model: $event.target.value })">
                <option value="">—</option>
                <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
              </select>
              <p v-if="modelIsBlind" class="text-[10px] text-amber-400 mt-1">{{ t('muse.notVision') }}</p>
              <p v-else class="text-[10px] text-gray-600 mt-1">{{ t('muse.modelHint') }}</p>
            </div>
            <div>
              <p class="sb-label mb-1">{{ t('muse.style') }}</p>
              <input class="sb-input" type="text" :value="inputs.style"
                     @change="patchInputs({ style: $event.target.value })" />
            </div>
            <label class="flex items-start gap-2 text-[11px] text-gray-400">
              <input type="checkbox" class="mt-0.5" :checked="inputs.think"
                     @change="patchInputs({ think: $event.target.checked })" />
              <span>
                {{ t('muse.think') }}
                <span class="block text-[10px] text-gray-600">{{ t('muse.thinkHint') }}</span>
              </span>
            </label>
          </section>

          <section class="space-y-2">
            <button type="button" class="sb-label w-full text-left"
                    @click="showKnobs = !showKnobs">
              {{ showKnobs ? '▾' : '▸' }} {{ t('muse.draftParams') }}
            </button>
            <div v-if="showKnobs" class="space-y-2">
              <div class="grid grid-cols-2 gap-2">
                <label class="block">
                  <span class="sb-label">{{ t('muse.size') }}</span>
                  <input class="sb-input" type="number" step="64" :value="inputs.width"
                         @change="patchInputs({ width: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">&nbsp;</span>
                  <input class="sb-input" type="number" step="64" :value="inputs.height"
                         @change="patchInputs({ height: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">{{ t('muse.draftSteps') }}</span>
                  <input class="sb-input" type="number" :value="inputs.draft_steps"
                         @change="patchInputs({ draft_steps: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">{{ t('muse.draftCfg') }}</span>
                  <input class="sb-input" type="number" step="0.1" :value="inputs.draft_cfg"
                         @change="patchInputs({ draft_cfg: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">{{ t('muse.draftCount') }}</span>
                  <input class="sb-input" type="number" :value="inputs.draft_count"
                         @change="patchInputs({ draft_count: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">{{ t('muse.refineStages') }}</span>
                  <input class="sb-input" type="number" min="1" max="3" :value="inputs.refine_stages"
                         @change="patchInputs({ refine_stages: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">{{ t('muse.finalSteps') }}</span>
                  <input class="sb-input" type="number" :value="inputs.final_steps"
                         @change="patchInputs({ final_steps: Number($event.target.value) })" />
                </label>
                <label class="block">
                  <span class="sb-label">{{ t('muse.finalCfg') }}</span>
                  <input class="sb-input" type="number" step="0.1" :value="inputs.final_cfg"
                         @change="patchInputs({ final_cfg: Number($event.target.value) })" />
                </label>
              </div>
              <p class="text-[10px] text-gray-600">{{ t('muse.refineStagesHint') }}</p>
              <label class="block">
                <span class="sb-label">{{ t('muse.wd14Threshold') }}</span>
                <input class="sb-input" type="number" step="0.05" :value="inputs.wd14_threshold"
                       @change="patchInputs({ wd14_threshold: Number($event.target.value) })" />
              </label>
              <label class="flex items-center gap-2 text-[11px] text-gray-400">
                <input type="checkbox" :checked="inputs.drop_rating_tags"
                       @change="patchInputs({ drop_rating_tags: $event.target.checked })" />
                {{ t('muse.dropRatingTags') }}
              </label>
              <label class="flex items-center gap-2 text-[11px] text-gray-400">
                <input type="checkbox" :checked="inputs.drop_character_tags"
                       @change="patchInputs({ drop_character_tags: $event.target.checked })" />
                {{ t('muse.dropCharacterTags') }}
              </label>
              <label class="block">
                <span class="sb-label">{{ t('muse.negative') }}</span>
                <textarea class="sb-textarea" rows="3" :value="inputs.negative_prompt"
                          @change="patchInputs({ negative_prompt: $event.target.value })"></textarea>
              </label>
            </div>
          </section>

          <section v-if="session?.brief" class="space-y-1">
            <p class="sb-label">{{ t('muse.brief') }}</p>
            <pre class="text-[10px] text-gray-500 whitespace-pre-wrap font-mono max-h-52 overflow-y-auto">{{ session.brief }}</pre>
            <p class="text-[10px] text-gray-600">{{ t('muse.briefHint') }}</p>
          </section>

          <div class="sticky bottom-0 pt-2 bg-[var(--sb-bg,#0b0b0d)]">
            <p v-if="needs.length" class="text-[10px] text-amber-400 mb-1">
              {{ needs.map(n => t(`muse.needs.${n}`)).join(' / ') }}
            </p>
            <p v-if="comfyOffline" class="text-[10px] text-red-400 mb-1">{{ t('muse.warn.comfyOffline') }}</p>
            <button class="sb-btn w-full" :disabled="ctaBlocked" @click="runStep">
              {{ busy ? '…' : ctaLabel }}
            </button>
          </div>
        </aside>

        <!-- ── right: the pictures ── -->
        <main class="overflow-y-auto p-4 space-y-6 min-h-0">
          <p v-for="w in warnings" :key="w" class="text-[11px] text-amber-400">{{ w }}</p>

          <!-- drafts -->
          <section class="space-y-2">
            <div class="flex items-baseline justify-between gap-3">
              <h3 class="sb-label">{{ t('muse.draft.title') }}</h3>
              <span class="text-[10px] text-gray-600">{{ stepState.draft?.detail }}</span>
            </div>
            <p class="text-[10px] text-gray-600">{{ t('muse.draft.hint') }}</p>

            <div v-if="drafting" class="space-y-2">
              <div class="flex items-center gap-3">
                <img v-if="preview" :src="preview"
                     class="w-40 rounded border border-white/10" alt="" />
                <div v-else class="w-40 h-52 rounded bg-black/40 border border-white/10"></div>
                <div class="space-y-2">
                  <p class="text-[11px] text-gray-400">{{ t('muse.draft.pending') }}</p>
                  <button class="sb-btn" @click="cancelDraft">{{ t('muse.draft.cancel') }}</button>
                  <p class="text-[10px] text-gray-600 max-w-xs">{{ t('muse.draft.cancelHint') }}</p>
                </div>
              </div>
            </div>

            <div v-if="draftImages.length" class="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <figure
                v-for="img in draftImages"
                :key="img.index"
                class="space-y-1 cursor-pointer"
                :class="picked.includes(img.index) ? 'ring-2 ring-[var(--sb-amber)] rounded' : ''"
                @click="togglePick(img.index)"
              >
                <img :src="thumb(img.image_id)" class="w-full rounded border border-white/10" alt="" />
                <figcaption class="text-[10px] text-gray-500 flex justify-between">
                  <span>#{{ img.index + 1 }}</span>
                  <span v-if="picked.includes(img.index)" class="text-[var(--sb-amber)]">
                    {{ t('muse.draft.selected') }}
                  </span>
                </figcaption>
              </figure>
            </div>
            <p v-else-if="!drafting" class="text-[11px] text-gray-600">{{ t('muse.draft.empty') }}</p>

            <details v-if="draft.prompt" class="text-[10px] text-gray-500">
              <summary class="cursor-pointer">{{ t('muse.draft.prompt') }}</summary>
              <p class="whitespace-pre-wrap font-mono mt-1">{{ draft.prompt }}</p>
            </details>
          </section>

          <!-- refine chains -->
          <section class="space-y-3">
            <div class="flex items-baseline justify-between gap-3">
              <h3 class="sb-label">{{ t('muse.refine.title') }}</h3>
              <span class="text-[10px] text-gray-600">{{ stepState.refine?.detail }}</span>
            </div>
            <p v-if="!chains.length" class="text-[11px] text-gray-600">{{ t('muse.refine.empty') }}</p>

            <article v-for="(chain, ci) in chains" :key="ci" class="space-y-2">
              <p class="text-[11px] text-gray-400">#{{ chain.draft_index + 1 }}</p>
              <details v-if="chain.wd14" class="text-[10px] text-gray-500">
                <summary class="cursor-pointer">{{ t('muse.refine.wd14') }}</summary>
                <p class="font-mono mt-1 break-words">{{ chain.wd14 }}</p>
                <p class="text-gray-600 mt-1">{{ t('muse.refine.wd14Hint') }}</p>
              </details>
              <div class="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <figure v-for="(stage, si) in chain.stages" :key="si" class="space-y-1">
                  <img
                    v-if="stage.image_id"
                    :src="thumb(stage.image_id)"
                    class="w-full rounded border border-white/10 cursor-pointer"
                    alt=""
                    @click="emit('select-image', stage.image_id)"
                  />
                  <div v-else class="w-full aspect-[3/4] rounded bg-black/40 border border-white/10
                                     flex items-center justify-center text-[10px] text-gray-600">
                    {{ t('muse.refine.pending') }}
                  </div>
                  <figcaption class="text-[10px] text-gray-500">
                    {{ t(`muse.refine.stage.${stage.stage}`) }}
                  </figcaption>
                  <details v-if="stage.prompt" class="text-[10px] text-gray-600">
                    <summary class="cursor-pointer">prompt</summary>
                    <p class="whitespace-pre-wrap font-mono mt-1">{{ stage.prompt }}</p>
                  </details>
                </figure>
              </div>
            </article>
          </section>
        </main>
      </div>
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

<script setup>
/*
 * Muse Refine — independent ledger studio.
 * Does not share MusePanel state or the full notebook pipeline.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getToken } from '../apiToken.js'

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
const chatInput = ref('')
const chatEl = ref(null)
const preview = ref('')
let es = null
let pollTimer = null

const isJa = computed(() => String(locale.value).startsWith('ja'))
const inputs = computed(() => session.value?.inputs || {})
const ledger = computed(() => session.value?.refine_ledger || {})
const craft = computed(() => session.value?.craft || {})
const chat = computed(() => session.value?.chat || [])
const characters = computed(() => characterList.value)
const workflows = computed(() => {
  const list = catalog.value?.comfyui?.workflows || catalog.value?.workflows || []
  return Array.isArray(list) ? list : []
})
const models = computed(() => catalog.value?.llm?.models || [])
const boardImages = computed(() => session.value?.board?.images || [])
const shootImages = computed(() => session.value?.shoot?.images || [])

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
    startPoll()
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

async function sendChat() {
  const msg = chatInput.value.trim()
  if (!msg || !session.value?.session_id || busy.value) return
  chatInput.value = ''
  busy.value = true
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
  }
}

async function rebuild() {
  if (!session.value?.session_id || busy.value) return
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
  if (!session.value?.session_id || busy.value) return
  busy.value = true
  try {
    session.value = await api(
      `/api/muse-refine/sessions/${session.value.session_id}/${path}`,
      { method: 'POST' },
    )
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

function openStream(id) {
  closeStream()
  if (!id) return
  es = new EventSource(
    `/api/muse-refine/sessions/${id}/stream?token=${encodeURIComponent(getToken())}`,
  )
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.type === 'preview' && data.image) {
        preview.value = `data:image/jpeg;base64,${data.image}`
      }
      if (data.type === 'session_updated') refresh()
    } catch { /* ignore */ }
  }
}
function closeStream() {
  if (es) {
    es.close()
    es = null
  }
}
async function refresh() {
  if (!session.value?.session_id) return
  try {
    session.value = await api(`/api/muse-refine/sessions/${session.value.session_id}`)
  } catch { /* ignore */ }
}
function startPoll() {
  stopPoll()
  pollTimer = setInterval(refresh, 4000)
}
function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(() => props.show, async (open) => {
  if (!open) {
    closeStream()
    stopPoll()
    return
  }
  try {
    await ensureCatalog()
    if (!session.value) await startFresh()
    else {
      openStream(session.value.session_id)
      startPoll()
    }
  } catch (err) {
    fail(err)
  }
})

onBeforeUnmount(() => {
  closeStream()
  stopPoll()
})

function thumb(sha) {
  return sha ? `/api/thumbnails/${sha}.webp` : ''
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
        class="flex h-full w-full max-w-5xl flex-col border-l border-teal-900/50 bg-[#0b1214] text-gray-100 shadow-2xl"
      >
        <header class="flex items-center gap-2 border-b border-teal-900/40 px-4 py-3">
          <div class="min-w-0 flex-1">
            <h2 class="text-base font-semibold tracking-wide text-teal-200">
              {{ t('museRefine.title') }}
            </h2>
            <p class="truncate text-[11px] text-gray-500">{{ t('museRefine.subtitle') }}</p>
          </div>
          <button
            type="button"
            class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700"
            :disabled="busy"
            @click="showSettings = !showSettings"
          >{{ t('museRefine.settings') }}</button>
          <button
            type="button"
            class="rounded-lg bg-gray-800 px-2.5 py-1.5 text-xs hover:bg-gray-700"
            :disabled="busy"
            @click="startFresh()"
          >{{ t('museRefine.reset') }}</button>
          <button
            type="button"
            class="rounded-full px-2 py-1 text-gray-400 hover:bg-teal-950/60 hover:text-white"
            @click="close"
          >✕</button>
        </header>

        <div class="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[1.1fr_0.9fr]">
          <!-- Chat + ledger -->
          <section class="flex min-h-0 flex-col border-r border-teal-950/40">
            <div class="flex items-center gap-2 border-b border-teal-950/30 px-3 py-2">
              <select
                class="max-w-[12rem] truncate rounded-md border border-teal-900/50 bg-teal-950/40 px-2 py-1 text-xs text-teal-100"
                :value="inputs.character_id || ''"
                :disabled="busy"
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
              <span class="truncate text-[11px] text-gray-500">{{ craft.now || t('museRefine.nowEmpty') }}</span>
            </div>

            <div ref="chatEl" class="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3 text-sm">
              <div
                v-for="(row, i) in chat"
                :key="i"
                class="rounded-lg px-2.5 py-2"
                :class="row.role === 'user'
                  ? 'bg-teal-950/40 text-teal-50'
                  : row.role === 'system'
                    ? 'bg-gray-900/80 text-[11px] text-gray-400'
                    : 'bg-gray-900 text-gray-100'"
              >
                <div class="mb-0.5 text-[10px] uppercase tracking-wide text-gray-500">
                  {{ row.name || row.role }}
                </div>
                <div class="whitespace-pre-wrap leading-relaxed">{{ row.text }}</div>
              </div>
              <p v-if="!chat.length" class="text-xs text-gray-500">{{ t('museRefine.chatHint') }}</p>
            </div>

            <form class="flex gap-2 border-t border-teal-950/40 p-3" @submit.prevent="sendChat">
              <input
                v-model="chatInput"
                type="text"
                class="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm outline-none focus:border-teal-600"
                :placeholder="t('museRefine.chatPlaceholder')"
                :disabled="busy || !session"
              />
              <button
                type="submit"
                class="rounded-lg bg-teal-700 px-3 py-2 text-sm font-medium text-white hover:bg-teal-600 disabled:opacity-40"
                :disabled="busy || !chatInput.trim()"
              >{{ t('museRefine.send') }}</button>
            </form>
          </section>

          <!-- Prompt / options / images -->
          <section class="flex min-h-0 flex-col gap-3 overflow-y-auto p-3">
            <div class="rounded-xl border border-teal-950/50 bg-gray-950/60 p-3">
              <div class="mb-2 text-xs font-medium text-teal-200/90">{{ t('museRefine.ledger') }}</div>
              <dl class="grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-1 text-[11px]">
                <dt class="text-gray-500">wearing</dt><dd class="text-gray-200">{{ ledger.wearing || '—' }}</dd>
                <dt class="text-gray-500">beat</dt><dd class="text-gray-200">{{ ledger.beat || '—' }}</dd>
                <dt class="text-gray-500">scene</dt><dd class="text-gray-200">{{ ledger.scene || '—' }}</dd>
                <dt class="text-gray-500">light</dt><dd class="text-gray-200">{{ ledger.light || '—' }}</dd>
                <dt class="text-gray-500">bg</dt><dd class="text-gray-200">{{ ledger.bg || '—' }}</dd>
              </dl>
            </div>

            <div class="rounded-xl border border-teal-950/50 bg-gray-950/60 p-3">
              <div class="mb-2 flex items-center justify-between gap-2">
                <span class="text-xs font-medium text-teal-200/90">{{ t('museRefine.options') }}</span>
                <button
                  type="button"
                  class="text-[11px] text-teal-300/80 hover:text-teal-200"
                  :disabled="busy"
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
            </div>

            <div class="rounded-xl border border-teal-950/50 bg-gray-950/60 p-3">
              <div class="mb-1 text-xs font-medium text-teal-200/90">{{ t('museRefine.prompt') }}</div>
              <pre class="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-gray-300">{{ craft.prompt || '—' }}</pre>
              <p v-if="craft.support_tags" class="mt-2 text-[10px] text-gray-500">
                support: {{ craft.support_tags }}
              </p>
            </div>

            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded-lg bg-teal-800/80 px-3 py-2 text-xs font-medium hover:bg-teal-700 disabled:opacity-40"
                :disabled="busy || comfyOffline || !craft.prompt"
                @click="runStage('board')"
              >{{ t('museRefine.board') }}</button>
              <button
                type="button"
                class="rounded-lg bg-cyan-800/80 px-3 py-2 text-xs font-medium hover:bg-cyan-700 disabled:opacity-40"
                :disabled="busy || comfyOffline || !craft.prompt"
                @click="runStage('shoot')"
              >{{ t('museRefine.shoot') }}</button>
            </div>

            <div v-if="preview" class="overflow-hidden rounded-xl border border-teal-950/50">
              <img :src="preview" alt="preview" class="max-h-56 w-full object-contain bg-black" />
            </div>

            <div class="flex flex-wrap gap-2">
              <button
                v-for="img in [...boardImages, ...shootImages]"
                :key="img.image_id || img.sha256 || img"
                type="button"
                class="h-16 w-16 overflow-hidden rounded-md border border-gray-800"
                @click="emit('select-image', img.image_id || img.sha256 || img)"
              >
                <img :src="thumb(img.image_id || img.sha256 || img)" class="h-full w-full object-cover" alt="" />
              </button>
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
          </section>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
/**
 * LoRA buckets: gather pictures, let the VLM caption them, hand the set over.
 *
 * ranbell's part ends at the export folder — training happens outside
 * (e.g. docker-musubi reads the folder). Nothing here starts a training run.
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: { type: Boolean, default: false },
  // The gallery selection the screen was opened with; offered for adding.
  incomingShas: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'toast', 'added'])
const { t } = useI18n()

const buckets = ref([])
const current = ref(null)        // full bucket (with items)
const loading = ref(false)
const busy = ref(false)
const newName = ref('')
const targetDraft = ref('')
const settings = ref({ name: '', trigger: '', targets: [] })
const drafts = ref({})           // sha → caption being edited
const openFull = ref(new Set())  // sha whose full description is unfolded
const confirmDelete = ref(false)
let pollTimer = null

const incoming = computed(() => props.incomingShas.filter(Boolean))
const incomingNew = computed(() => {
  const have = new Set((current.value?.items || []).map(i => i.sha256))
  return incoming.value.filter(s => !have.has(s))
})
const settingsDirty = computed(() => {
  const b = current.value
  if (!b) return false
  return settings.value.name !== b.name || settings.value.trigger !== b.trigger
    || JSON.stringify(settings.value.targets) !== JSON.stringify(b.targets)
})
const counts = computed(() => {
  const items = current.value?.items || []
  const c = { done: 0, pending: 0, error: 0, edited: 0, stale: 0 }
  for (const it of items) {
    if (it.status === 'done') c.done++
    else if (it.status === 'error') c.error++
    else c.pending++
    if (it.edited) c.edited++
    if (it.stale) c.stale++
  }
  return c
})
const stalled = computed(() => current.value && counts.value.pending > 0 && !current.value.captioning)
const canExport = computed(() => current.value && !(current.value.blockers || []).length && !settingsDirty.value)

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (!resp.ok) {
    let detail = ''
    try { detail = (await resp.json()).detail } catch {}
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || resp.status))
    err.detail = detail
    throw err
  }
  return resp.json()
}

function toast(msg, type = 'info') { emit('toast', { msg, type }) }

async function loadList() {
  const data = await api('/api/lora/buckets')
  buckets.value = data.buckets || []
}

function adopt(bucket) {
  current.value = bucket
  settings.value = { name: bucket.name, trigger: bucket.trigger, targets: [...bucket.targets] }
  // Keep a caption that is being typed; drop drafts the server already has.
  const next = {}
  for (const it of bucket.items) {
    const d = drafts.value[it.sha256]
    if (d !== undefined && d !== it.caption) next[it.sha256] = d
  }
  drafts.value = next
  schedulePoll()
}

async function openBucket(id) {
  loading.value = true
  confirmDelete.value = false
  try { adopt(await api(`/api/lora/buckets/${id}`)) }
  catch (e) { toast(e.message, 'error') }
  finally { loading.value = false }
}

async function refresh() {
  if (!current.value) return
  try {
    const b = await api(`/api/lora/buckets/${current.value.id}`)
    // Settings being edited stay as typed.
    const keep = settingsDirty.value ? { ...settings.value } : null
    adopt(b)
    if (keep) settings.value = keep
  } catch {}
  loadList().catch(() => {})
}

function schedulePoll() {
  clearTimeout(pollTimer)
  if (props.show && current.value && (current.value.captioning || counts.value.pending > 0)) {
    pollTimer = setTimeout(refresh, 3000)
  }
}

async function createBucket() {
  const name = newName.value.trim()
  if (!name) return
  busy.value = true
  try {
    const b = await api('/api/lora/buckets', { method: 'POST', body: { name, sha256s: incoming.value } })
    newName.value = ''
    if (incoming.value.length) emit('added', incoming.value.length)
    adopt(b)
    await loadList()
  } catch (e) { toast(e.message, 'error') }
  finally { busy.value = false }
}

async function addIncoming() {
  if (!current.value || !incomingNew.value.length) return
  busy.value = true
  try {
    const b = await api(`/api/lora/buckets/${current.value.id}/images`, {
      method: 'POST', body: { sha256s: incomingNew.value },
    })
    toast(t('lora.addedN', { n: b.added }), 'success')
    emit('added', b.added)
    adopt(b)
    loadList().catch(() => {})
  } catch (e) { toast(e.message, 'error') }
  finally { busy.value = false }
}

function addTarget() {
  const parts = targetDraft.value.split(/[,、\n]/).map(s => s.trim()).filter(Boolean)
  for (const p of parts) {
    if (!settings.value.targets.some(x => x.toLowerCase() === p.toLowerCase())) settings.value.targets.push(p)
  }
  targetDraft.value = ''
}
function removeTarget(i) { settings.value.targets.splice(i, 1) }

async function saveSettings() {
  if (targetDraft.value.trim()) addTarget()
  busy.value = true
  try {
    adopt(await api(`/api/lora/buckets/${current.value.id}`, {
      method: 'PATCH', body: { ...settings.value },
    }))
    loadList().catch(() => {})
  } catch (e) { toast(e.message, 'error') }
  finally { busy.value = false }
}

function captionValue(it) {
  return drafts.value[it.sha256] ?? it.caption
}
function onCaptionInput(it, value) {
  drafts.value = { ...drafts.value, [it.sha256]: value }
}
async function saveCaption(it) {
  const value = drafts.value[it.sha256]
  if (value === undefined || value === it.caption) return
  try {
    adopt(await api(`/api/lora/buckets/${current.value.id}/images/${it.sha256}/caption`, {
      method: 'PUT', body: { caption: value },
    }))
  } catch (e) { toast(e.message, 'error') }
}

async function recaption({ shas = [], step = 'strip', force = false } = {}) {
  busy.value = true
  try {
    const b = await api(`/api/lora/buckets/${current.value.id}/recaption`, {
      method: 'POST', body: { sha256s: shas, step, force },
    })
    adopt(b)
  } catch (e) { toast(e.message, 'error') }
  finally { busy.value = false }
}

async function removeItem(it) {
  try {
    adopt(await api(`/api/lora/buckets/${current.value.id}/images/${it.sha256}`, { method: 'DELETE' }))
    loadList().catch(() => {})
  } catch (e) { toast(e.message, 'error') }
}

async function exportFolder() {
  busy.value = true
  try {
    const r = await api(`/api/lora/buckets/${current.value.id}/export`, { method: 'POST' })
    toast(t('lora.exportQueued', { path: r.path }), 'success')
    setTimeout(refresh, 2500)
  } catch (e) {
    const d = e.detail
    toast(d?.error === 'no_export_dir' ? t('lora.noExportDir', { path: d.path }) : e.message, 'error')
  } finally { busy.value = false }
}

function zipUrl() {
  return current.value ? `/api/lora/buckets/${current.value.id}/export.zip` : ''
}

async function deleteBucket() {
  if (!confirmDelete.value) { confirmDelete.value = true; return }
  try {
    await api(`/api/lora/buckets/${current.value.id}`, { method: 'DELETE' })
    current.value = null
    confirmDelete.value = false
    await loadList()
  } catch (e) { toast(e.message, 'error') }
}

function toggleFull(sha) {
  const s = new Set(openFull.value)
  s.has(sha) ? s.delete(sha) : s.add(sha)
  openFull.value = s
}

function statusLabel(it) {
  if (it.status === 'done') return it.edited ? t('lora.status.edited') : t('lora.status.done')
  return t(`lora.status.${it.status}`)
}
function statusClass(it) {
  return {
    done: it.edited ? 'bg-sky-900/60 text-sky-200' : 'bg-emerald-900/60 text-emerald-200',
    error: 'bg-red-900/60 text-red-200',
  }[it.status] || 'bg-amber-900/60 text-amber-200 motion-safe:animate-pulse'
}

function fmtDate(v) {
  if (!v) return ''
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v)
  return d.toLocaleString()
}

watch(() => props.show, async (open) => {
  if (!open) { clearTimeout(pollTimer); return }
  try {
    await loadList()
    // Opened with a selection and a single bucket → that one; else the latest.
    if (!current.value && buckets.value.length) await openBucket(buckets.value[0].id)
    else if (current.value) await refresh()
  } catch (e) { toast(e.message, 'error') }
}, { immediate: true })

onBeforeUnmount(() => clearTimeout(pollTimer))
</script>

<template>
  <div v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    @click.self="emit('close')">
    <div role="dialog" aria-modal="true" aria-labelledby="lora-title"
      class="relative w-full max-w-6xl h-[90vh] flex flex-col md:flex-row rounded-2xl overflow-hidden
             bg-slate-950/95 border border-amber-500/30 shadow-2xl text-gray-200">
      <button type="button" :aria-label="t('lora.close')" @click="emit('close')"
        class="absolute top-3 right-3 z-10 w-8 h-8 rounded-full bg-gray-800 hover:bg-gray-700 text-gray-300">✕</button>

      <!-- Bucket list -->
      <aside class="w-full md:w-64 shrink-0 border-b md:border-b-0 md:border-r border-gray-800 p-4 flex flex-col gap-3">
        <h3 id="lora-title" class="font-bold text-amber-200">{{ t('lora.title') }}</h3>
        <p class="text-[11px] text-gray-500 leading-snug">{{ t('lora.subtitle') }}</p>

        <form class="flex gap-1" @submit.prevent="createBucket">
          <input v-model="newName" :placeholder="t('lora.newBucket')"
            class="flex-1 min-w-0 px-2 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-sm" />
          <button type="submit" :disabled="busy || !newName.trim()"
            class="px-2 py-1.5 rounded-lg bg-amber-700/70 hover:bg-amber-600 text-xs disabled:opacity-40">
            {{ incoming.length ? t('lora.createWith', { n: incoming.length }) : t('lora.create') }}
          </button>
        </form>

        <div class="flex-1 overflow-y-auto flex flex-col gap-1.5 min-h-0">
          <button v-for="b in buckets" :key="b.id" type="button" @click="openBucket(b.id)"
            class="flex items-center gap-2 p-2 rounded-xl text-left border transition-colors"
            :class="current?.id === b.id ? 'bg-amber-900/30 border-amber-500/50' : 'bg-gray-900/50 border-gray-800 hover:bg-gray-800/70'">
            <img v-if="b.cover" :src="`/api/thumbnails/${b.cover}.webp`" class="w-9 h-9 rounded-lg object-cover" loading="lazy" />
            <div v-else class="w-9 h-9 rounded-lg bg-gray-800" />
            <div class="min-w-0">
              <div class="text-sm truncate">{{ b.name }}</div>
              <div class="text-[10px] text-gray-500">
                {{ t('lora.countLine', { n: b.count }) }}
                <span v-if="b.pending" class="text-amber-300"> · {{ t('lora.pendingN', { n: b.pending }) }}</span>
                <span v-if="b.errors" class="text-red-300"> · {{ t('lora.errorsN', { n: b.errors }) }}</span>
              </div>
            </div>
          </button>
          <p v-if="!buckets.length" class="text-xs text-gray-500 py-6 text-center">{{ t('lora.empty') }}</p>
        </div>
      </aside>

      <!-- Bucket detail -->
      <section class="flex-1 min-w-0 flex flex-col">
        <div v-if="!current" class="flex-1 flex items-center justify-center text-sm text-gray-500">
          {{ loading ? t('lora.loading') : t('lora.pick') }}
        </div>

        <template v-else>
          <!-- Settings -->
          <div class="p-4 pr-14 border-b border-gray-800 flex flex-col gap-2">
            <div class="flex flex-wrap items-center gap-2">
              <input v-model="settings.name" class="px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 font-semibold" />
              <label class="text-xs text-gray-400">{{ t('lora.trigger') }}</label>
              <input v-model="settings.trigger" :placeholder="t('lora.triggerHint')"
                class="w-40 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-sm font-mono" />
              <span class="text-[11px] text-gray-500">{{ t('lora.folder') }}: {{ current.slug }}</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5">
              <label class="text-xs text-gray-400" :title="t('lora.targetsHelp')">{{ t('lora.targets') }}</label>
              <span v-for="(tg, i) in settings.targets" :key="tg"
                class="flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-900/50 border border-amber-600/40 text-xs">
                {{ tg }}
                <button type="button" class="text-amber-300 hover:text-white" @click="removeTarget(i)">✕</button>
              </span>
              <input v-model="targetDraft" :placeholder="t('lora.targetsHint')"
                class="w-56 px-2 py-0.5 rounded-lg bg-gray-900 border border-gray-700 text-xs"
                @keydown.enter.prevent="addTarget" />
              <button v-if="settingsDirty || targetDraft.trim()" type="button" :disabled="busy" @click="saveSettings"
                class="ml-auto px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 text-xs text-black font-semibold disabled:opacity-40">
                {{ t('lora.saveSettings') }}
              </button>
            </div>
            <p class="text-[11px] text-gray-500">{{ t('lora.targetsHelp') }}</p>
          </div>

          <!-- Status and actions -->
          <div class="px-4 py-2 border-b border-gray-800 flex flex-wrap items-center gap-2 text-xs">
            <span class="text-gray-300">{{ t('lora.countLine', { n: current.items.length }) }}</span>
            <span class="text-emerald-300">{{ t('lora.doneN', { n: counts.done }) }}</span>
            <span v-if="counts.pending" class="text-amber-300">
              {{ current.captioning ? t('lora.captioningN', { n: counts.pending }) : t('lora.pendingN', { n: counts.pending }) }}
            </span>
            <span v-if="counts.error" class="text-red-300">{{ t('lora.errorsN', { n: counts.error }) }}</span>
            <span v-if="counts.stale" class="text-sky-300">{{ t('lora.staleN', { n: counts.stale }) }}</span>

            <button v-if="stalled" type="button" @click="recaption({ shas: [], step: 'strip' })"
              class="px-2 py-1 rounded-lg bg-amber-800/60 hover:bg-amber-700">{{ t('lora.resume') }}</button>
            <button v-if="counts.error" type="button" @click="recaption({ shas: current.items.filter(i => i.status === 'error').map(i => i.sha256), step: 'describe' })"
              class="px-2 py-1 rounded-lg bg-red-900/60 hover:bg-red-800">{{ t('lora.retryErrors') }}</button>
            <button v-if="counts.stale" type="button" @click="recaption({ shas: current.items.filter(i => i.stale).map(i => i.sha256), step: 'strip', force: true })"
              class="px-2 py-1 rounded-lg bg-sky-900/60 hover:bg-sky-800">{{ t('lora.overwriteStale') }}</button>

            <div class="ml-auto flex items-center gap-2">
              <button v-if="incomingNew.length" type="button" :disabled="busy" @click="addIncoming"
                class="px-3 py-1 rounded-lg bg-purple-800/70 hover:bg-purple-700 disabled:opacity-40">
                {{ t('lora.addSelected', { n: incomingNew.length }) }}
              </button>
              <button type="button" :disabled="busy || !canExport" @click="exportFolder"
                :title="(current.blockers || []).map(b => t(`lora.blocker.${b}`)).join(' / ')"
                class="px-3 py-1 rounded-lg bg-emerald-700 hover:bg-emerald-600 font-semibold disabled:opacity-40">
                {{ t('lora.export') }}
              </button>
              <a v-if="canExport" :href="zipUrl()" class="px-3 py-1 rounded-lg bg-gray-800 hover:bg-gray-700">{{ t('lora.zip') }}</a>
              <button type="button" @click="deleteBucket"
                class="px-2 py-1 rounded-lg" :class="confirmDelete ? 'bg-red-700 text-white' : 'bg-gray-800 text-gray-400 hover:text-red-300'">
                {{ confirmDelete ? t('lora.deleteConfirm') : t('lora.delete') }}
              </button>
            </div>
          </div>
          <div v-if="current.exported_at" class="px-4 py-1 text-[11px] text-gray-500 border-b border-gray-800">
            {{ t('lora.lastExport', { when: fmtDate(current.exported_at), path: current.export_path }) }}
          </div>

          <!-- Pictures -->
          <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-3 min-h-0">
            <p v-if="!current.items.length" class="text-sm text-gray-500 text-center py-10">{{ t('lora.noItems') }}</p>
            <div v-for="it in current.items" :key="it.sha256" class="flex gap-3 p-2 rounded-xl bg-gray-900/60 border border-gray-800">
              <img :src="`/api/thumbnails/${it.sha256}.webp`" class="w-28 h-28 rounded-lg object-cover shrink-0" loading="lazy" />
              <div class="flex-1 min-w-0 flex flex-col gap-1">
                <div class="flex items-center gap-2 text-[11px]">
                  <span class="px-2 py-0.5 rounded-full" :class="statusClass(it)">{{ statusLabel(it) }}</span>
                  <span v-if="it.stale" class="text-sky-300">{{ t('lora.staleOne') }}</span>
                  <span v-if="it.error" class="text-red-300 truncate" :title="it.error">{{ it.error }}</span>
                  <div class="ml-auto flex gap-1">
                    <button v-if="it.full_caption" type="button" class="text-gray-400 hover:text-gray-200" @click="toggleFull(it.sha256)">
                      {{ openFull.has(it.sha256) ? t('lora.hideFull') : t('lora.showFull') }}
                    </button>
                    <button type="button" class="text-gray-400 hover:text-amber-200"
                      @click="recaption({ shas: [it.sha256], step: 'describe', force: true })">{{ t('lora.redescribe') }}</button>
                    <button type="button" class="text-gray-500 hover:text-red-300" @click="removeItem(it)">{{ t('lora.remove') }}</button>
                  </div>
                </div>
                <textarea :value="captionValue(it)" rows="3" :placeholder="t('lora.captionPlaceholder')"
                  :disabled="it.status !== 'done' && it.status !== 'error'"
                  class="w-full px-2 py-1 rounded-lg bg-gray-950 border border-gray-700 text-xs leading-relaxed resize-y disabled:opacity-50"
                  @input="onCaptionInput(it, $event.target.value)" @blur="saveCaption(it)" />
                <p v-if="openFull.has(it.sha256)" class="text-[11px] text-gray-400 whitespace-pre-wrap border-l-2 border-gray-700 pl-2">
                  {{ it.full_caption }}
                </p>
              </div>
            </div>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>

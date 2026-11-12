<script setup>
/**
 * One LoRA comparison sheet: its grid, filling in while it is drawn (with the
 * in-flight frame and step of the picture being drawn), and what can be done
 * with it — stop, draw the rest, redraw with a new seed, rename, save, delete.
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import LoraSheetCell from './LoraSheetCell.vue'
import LoraSheetLightbox from './LoraSheetLightbox.vue'
import { api, rowLabel, colLabel as colLabelOf } from '../lora/sheetLabels.js'

const props = defineProps({
  sheetId: { type: String, required: true },
  isPinned: { type: Function, default: () => false },
})
const emit = defineEmits(['pin', 'toast', 'changed', 'deleted', 'opened'])
const { t } = useI18n()

const current = ref(null)
const busy = ref(false)
const confirmDelete = ref(false)
const editingName = ref(false)
const nameDraft = ref('')
const lightbox = ref(null)      // { r, c }
let pollTimer = null
let previewTimer = null
let alive = true
let stopped = false   // Stop was pressed: the end is "stopped", not "drawn"
// A response is only taken when it is for the sheet shown now: a slow answer
// for the sheet clicked before must not replace it (and Delete or Rename
// would then act on the wrong one).
function adopt(view) {
  if (view && view.id === props.sheetId) current.value = view
  return current.value?.id === props.sheetId
}
function gone(e) {
  if (e?.status !== 404) return false
  stop()
  emit('deleted', props.sheetId)
  return true
}
// The picture being drawn right now: its cell and its newest in-flight frame.
const live = ref({ key: '', url: '', seq: '', step: 0, steps: 0 })

function toast(msg, type = 'info') { emit('toast', { msg, type }) }
const colLabel = col => colLabelOf(t, col)
const ratio = computed(() => current.value ? `${current.value.width} / ${current.value.height}` : '1 / 1')
const stepText = computed(() => live.value.steps ? t('loraSheet.step', { v: live.value.step, m: live.value.steps }) : '')

function render(key) { return current.value?.renders?.[key] || {} }

function cellOf(r, c) {
  const s = current.value
  const key = s.grid[r]?.[c]
  const rd = key ? s.renders[key] : null
  if (!rd?.sha256) return null
  return { sha256: rd.sha256, sheet_id: s.id, key, w: s.width, h: s.height,
           label: `${rowLabel(s.rows[r])} · ${colLabel(s.columns_spec[c])} · ${s.name}` }
}
const cells = computed(() => current.value
  ? current.value.grid.map((row, r) => row.map((_, c) => cellOf(r, c))) : [])

async function load() {
  stop()
  confirmDelete.value = false
  editingName.value = false
  lightbox.value = null
  stopped = false
  const want = props.sheetId
  if (current.value?.id !== want) current.value = null  // never show the previous sheet as this one
  try {
    const view = await api(`/api/lora/sheets/${want}`)
    if (want === props.sheetId && adopt(view)) poll()
  } catch (e) {
    if (want === props.sheetId && !gone(e)) throw e
  }
}

function clearLive() {
  clearTimeout(previewTimer)
  previewTimer = null
  if (live.value.url) URL.revokeObjectURL(live.value.url)
  live.value = { key: '', url: '', seq: '', step: 0, steps: 0 }
}
function stop() { clearTimeout(pollTimer); clearLive() }

// Frames come in faster than the sheet changes, so they have their own loop.
function pollPreview() {
  clearTimeout(previewTimer)
  const s = current.value
  if (!alive || !s?.drawing) { clearLive(); return }
  previewTimer = setTimeout(async () => {
    try {
      const resp = await fetch(`/api/lora/sheets/${s.id}/preview`, { cache: 'no-store' })
      if (current.value?.id !== s.id) return
      const key = resp.headers.get('X-Sheet-Key') || ''
      const seq = resp.headers.get('X-Preview-Seq') || ''
      const step = Number(resp.headers.get('X-Step') || 0)
      const steps = Number(resp.headers.get('X-Steps') || 0)
      if (resp.status === 200 && seq !== live.value.seq) {
        const url = URL.createObjectURL(await resp.blob())
        if (live.value.url) URL.revokeObjectURL(live.value.url)
        live.value = { key, url, seq, step, steps }
      } else if (key !== live.value.key) {
        if (live.value.url) URL.revokeObjectURL(live.value.url)
        live.value = { key, url: '', seq, step, steps }
      } else {
        live.value = { ...live.value, step, steps }
      }
    } catch {}
    pollPreview()
  }, 600)
}

function poll() {
  clearTimeout(pollTimer)
  const s = current.value
  if (!alive || !s?.drawing) { clearLive(); return }
  if (!previewTimer) pollPreview()
  pollTimer = setTimeout(async () => {
    if (current.value?.id !== s.id) return
    try {
      const fresh = await api(`/api/lora/sheets/${s.id}`)
      if (current.value?.id !== s.id || !adopt(fresh)) return
      if (!fresh.drawing) {
        if (stopped) toast(t('loraSheet.stopped', { n: fresh.done }), 'info')
        else toast(fresh.errors ? t('loraSheet.doneWithErrors', { n: fresh.done, e: fresh.errors })
          : t('loraSheet.done', { n: fresh.done }), fresh.errors ? 'error' : 'success')
        stopped = false
        emit('changed')
        clearLive()
        return
      }
    } catch (e) {
      if (gone(e)) return
    }
    poll()
  }, 2500)
}

async function act(fn) {
  busy.value = true
  try { await fn() } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}
const drawRest = () => act(async () => {
  stopped = false
  if (adopt(await api(`/api/lora/sheets/${current.value.id}/render`, { method: 'POST' }))) poll()
  emit('changed')
})
const stopDrawing = () => act(async () => {
  stopped = true
  await api(`/api/lora/sheets/${current.value.id}/cancel`, { method: 'POST' })
  toast(t('loraSheet.stopping'), 'info')
  poll()
})
const duplicateNewSeed = () => act(async () => {
  const copy = await api(`/api/lora/sheets/${current.value.id}/duplicate`, { method: 'POST', body: {} })
  toast(t('loraSheet.queued', { n: copy.pictures }), 'info')
  emit('opened', copy.id)
})
function startRename() {
  nameDraft.value = current.value.name
  editingName.value = true
}
const saveName = () => act(async () => {
  if (!editingName.value) return
  editingName.value = false
  if (nameDraft.value.trim() === current.value.name) return
  adopt(await api(`/api/lora/sheets/${current.value.id}`, { method: 'PATCH', body: { name: nameDraft.value } }))
  emit('changed')
})
async function deleteSheet() {
  if (!confirmDelete.value) { confirmDelete.value = true; return }
  await act(async () => {
    await api(`/api/lora/sheets/${current.value.id}`, { method: 'DELETE' })
    emit('deleted', current.value.id)
  })
}

watch(() => props.sheetId, () => load().catch(e => toast(e.message, 'error')), { immediate: true })
onBeforeUnmount(() => { alive = false; stop() })
</script>

<template>
  <section v-if="current" class="flex-1 min-w-0 flex flex-col min-h-0 relative">
    <div class="p-4 pr-14 border-b border-gray-800 flex flex-col gap-1.5">
      <div class="flex flex-wrap items-center gap-2">
        <input v-if="editingName" v-model="nameDraft" autofocus
          class="px-2 py-0.5 rounded-lg bg-gray-900 border border-amber-600 text-sm font-semibold"
          @keydown.enter.prevent="saveName" @keydown.esc.stop="editingName = false" @blur="saveName" />
        <h4 v-else class="font-semibold cursor-text hover:text-amber-100" :title="t('loraSheet.rename')"
          @click="startRename">{{ current.name }} <span class="text-gray-600 text-xs">✎</span></h4>
        <span v-if="!current.drawing" class="text-[11px] text-gray-500">{{ current.done }}/{{ current.pictures }}</span>
        <span v-if="current.drawing" class="text-[11px] text-amber-300 motion-safe:animate-pulse">{{ t('loraSheet.drawing') }}</span>
        <span v-if="current.drawing" class="text-[11px] text-amber-200 font-mono">
          {{ t('loraSheet.progress', { done: (current.done || 0) + (current.errors || 0), total: current.pictures || 0 }) }}<template v-if="stepText"> · {{ stepText }}</template>
        </span>
        <div class="ml-auto flex flex-wrap gap-1.5">
          <button v-if="current.drawing" type="button" :disabled="busy" @click="stopDrawing"
            class="px-2.5 py-1 rounded-lg bg-red-800/70 hover:bg-red-700 text-xs">■ {{ t('loraSheet.stop') }}</button>
          <button v-if="!current.drawing && (current.pending || current.errors)" type="button" :disabled="busy" @click="drawRest"
            class="px-2.5 py-1 rounded-lg bg-amber-700/70 hover:bg-amber-600 text-xs">{{ t('loraSheet.drawRest') }}</button>
          <button type="button" :disabled="busy" @click="duplicateNewSeed"
            class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">🎲 {{ t('loraSheet.newSeed') }}</button>
          <a :href="`/api/lora/sheets/${current.id}/grid.png`" download
            class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">⬇ {{ t('loraSheet.savePng') }}</a>
          <button type="button" @click="deleteSheet" class="px-2.5 py-1 rounded-lg text-xs"
            :class="confirmDelete ? 'bg-red-700 text-white' : 'bg-gray-800 hover:bg-gray-700'">
            {{ confirmDelete ? t('loraSheet.confirmDelete') : t('loraSheet.delete') }}
          </button>
        </div>
      </div>
      <p class="text-[11px] text-gray-500 truncate" :title="current.prompt">
        seed {{ current.seed }} · {{ current.models.unet }} · {{ current.steps }} steps · cfg {{ current.cfg }}
        · {{ current.sampler }}/{{ current.scheduler }} · {{ current.width }}×{{ current.height }} — {{ current.prompt }}
      </p>
    </div>

    <div class="flex-1 overflow-auto p-3">
      <table class="border-separate border-spacing-1">
        <thead>
          <tr>
            <th class="sticky top-0 left-0 z-20 bg-slate-950"></th>
            <th v-for="(col, c) in current.columns_spec" :key="c"
              class="sticky top-0 z-10 bg-slate-950 text-xs font-semibold text-amber-200 px-1 py-1">{{ colLabel(col) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in current.rows" :key="r">
            <th class="sticky left-0 z-10 bg-slate-950 text-left align-top text-xs font-normal pr-2 max-w-40">
              <span class="block break-all" :title="row.lora">{{ rowLabel(row) }}</span>
              <span v-if="row.trigger" class="block text-[10px] text-gray-500 font-mono break-all">{{ row.trigger }}</span>
            </th>
            <td v-for="(key, c) in current.grid[r]" :key="c" class="p-0">
              <LoraSheetCell :render="render(key)" :ratio="ratio"
                :live="live.key === key ? live : null"
                :pinned="!!cells[r]?.[c] && isPinned(cells[r][c])"
                @open="lightbox = { r, c }" @pin="cells[r][c] && emit('pin', cells[r][c])" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <LoraSheetLightbox v-if="lightbox" :cells="cells" :at="lightbox" :is-pinned="isPinned"
      @move="lightbox = $event" @close="lightbox = null" @pin="emit('pin', $event)" />
  </section>
</template>

<script setup>
/**
 * LoRA comparison sheet: LoRA files down, strengths across, one seed.
 *
 * ranbell builds the ComfyUI graph itself (core nodes only) and draws every
 * cell through the job scheduler. The LoRA files are read from ComfyUI's
 * loras folder — ranbell never places or moves them.
 *
 * This is the frame: the sheet list on the left, and on the right one of
 * the new-sheet form, one sheet, several sheets stacked, or the compare board
 * of pinned pictures (kept in the browser between visits).
 */
import { ref, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import LoraSheetList from './LoraSheetList.vue'
import LoraSheetForm from './LoraSheetForm.vue'
import LoraSheetGrid from './LoraSheetGrid.vue'
import LoraSheetStack from './LoraSheetStack.vue'
import LoraCompareBoard from './LoraCompareBoard.vue'
import { api } from '../lora/sheetLabels.js'

const props = defineProps({
  show: { type: Boolean, default: false },
  // Where the screen was opened from: { sha256 } (a gallery picture) or
  // { bucket_id, slug, trigger } (a LoRA bucket). Applied to a new sheet.
  prefill: { type: Object, default: null },
})
const emit = defineEmits(['close', 'toast', 'prefill-used'])
const { t } = useI18n()

const LS_BOARD = 'ranbell.loraSheet.board'
const BOARD_MAX = 4

const options = ref(null)       // /api/lora/sheet-options
const sheets = ref([])
const mode = ref('new')         // new | sheet | stack | board
const currentId = ref('')
const selected = ref([])
const stackIds = ref([])
const formKey = ref(0)
const pins = ref(readPins())
let backFromBoard = null

function toast(msg, type = 'info') { emit('toast', { msg, type }) }
function onToast(e) { emit('toast', e) }

function readPins() {
  try { return JSON.parse(localStorage.getItem(LS_BOARD) || '[]').slice(0, BOARD_MAX) } catch { return [] }
}
watch(pins, v => { try { localStorage.setItem(LS_BOARD, JSON.stringify(v)) } catch {} }, { deep: true })
const isPinned = cell => pins.value.some(p => p.sha256 === cell.sha256)
// A pinned picture deleted from the gallery no longer loads: let it go.
function dropBrokenPin(sha) { pins.value = pins.value.filter(p => p.sha256 !== sha) }
function togglePin(cell) {
  if (isPinned(cell)) { pins.value = pins.value.filter(p => p.sha256 !== cell.sha256); return }
  if (pins.value.length >= BOARD_MAX) { toast(t('loraSheet.boardFull', { n: BOARD_MAX }), 'info'); return }
  const { sha256, label, sheet_id, key, w, h } = cell
  pins.value = [...pins.value, { sha256, label, sheet_id, key, w, h }]
}

async function loadList() { sheets.value = (await api('/api/lora/sheets')).sheets || [] }
async function loadOptions() { options.value = await api('/api/lora/sheet-options') }

function startNew() { mode.value = 'new'; currentId.value = ''; formKey.value++ }
function openSheet(id) { currentId.value = id; mode.value = 'sheet' }
async function onCreated(s) { await loadList(); openSheet(s.id) }
async function onOpened(id) { await loadList(); openSheet(id) }
async function onDeleted(id) {
  selected.value = selected.value.filter(x => x !== id)
  await loadList()
  sheets.value.length ? openSheet(sheets.value[0].id) : startNew()
}
const STACK_MAX = 12  // what /sheets/stack.png takes
function openStack() {
  if (selected.value.length > STACK_MAX) {
    toast(t('loraSheet.stackTooMany', { n: STACK_MAX }), 'info')
    return
  }
  stackIds.value = [...selected.value]
  mode.value = 'stack'
}
function closeStack() {
  if (currentId.value && sheets.value.some(s => s.id === currentId.value)) openSheet(currentId.value)
  else if (sheets.value.length) openSheet(sheets.value[0].id)
  else startNew()
}
function openBoard() {
  backFromBoard = { mode: mode.value, id: currentId.value }
  mode.value = 'board'
}
function closeBoard() {
  const b = backFromBoard
  if (b?.mode === 'new') { mode.value = 'new'; formKey.value++ }  // the draft comes back
  else if (b?.mode === 'sheet' && b.id && sheets.value.some(s => s.id === b.id)) openSheet(b.id)
  else if (b?.mode === 'stack' && stackIds.value.length) mode.value = 'stack'
  else if (sheets.value.length) openSheet(sheets.value[0].id)
  else startNew()
}
async function deleteMany(ids) {
  const failed = []
  for (const id of ids) {
    try { await api(`/api/lora/sheets/${id}`, { method: 'DELETE' }) } catch (e) {
      failed.push(id)
      toast(e.message, 'error')
    }
  }
  const gone = ids.filter(id => !failed.includes(id))
  selected.value = failed  // what could not be deleted stays ticked
  stackIds.value = stackIds.value.filter(id => !gone.includes(id))
  await loadList()
  if (mode.value === 'board') return
  if (gone.includes(currentId.value) || mode.value === 'stack') {
    sheets.value.length ? openSheet(sheets.value[0].id) : startNew()
  }
}

// Escape closes the innermost thing: the lightbox handles its own (it listens
// earlier, on window), then the board, then the modal. On document in the
// capture phase, so the gallery behind (App.vue, document bubble) never sees it.
function onKey(e) {
  if (!props.show) return
  if (e.key === 'Escape') {
    e.stopPropagation()
    e.preventDefault()
    if (mode.value === 'board') closeBoard()
    else emit('close')
  } else if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || '')) {
    e.stopPropagation()  // keep the Control Room toggle behind the modal still
  }
}
watch(() => props.show, open => {
  if (open) document.addEventListener('keydown', onKey, true)
  else document.removeEventListener('keydown', onKey, true)
}, { immediate: true })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey, true))

watch(() => props.show, async (open) => {
  if (!open) return
  // Nothing on the right until the lists are in: an old 'new' mode would
  // mount the form now and again after (two prefill fetches, two toasts).
  const was = { mode: mode.value, id: currentId.value }
  mode.value = 'loading'
  try {
    await Promise.all([loadOptions(), loadList()])
    if (props.prefill || !sheets.value.length || was.mode === 'new') startNew()
    else if (sheets.value.some(s => s.id === was.id)) openSheet(was.id)
    else openSheet(sheets.value[0].id)
  } catch (e) {
    toast(e.message, 'error')
    if (mode.value === 'loading') mode.value = was.mode
  }
}, { immediate: true })
</script>

<template>
  <div v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center bg-black/60 backdrop-blur-sm p-2"
    @click.self="emit('close')">
    <div role="dialog" aria-modal="true" aria-labelledby="lora-sheet-title"
      class="relative w-full max-w-[min(98vw,2400px)] h-[94vh] flex flex-col md:flex-row rounded-2xl overflow-hidden
             bg-slate-950/95 border border-amber-500/30 shadow-2xl text-gray-200">
      <button type="button" :aria-label="t('lora.close')" @click="emit('close')"
        class="absolute top-3 right-3 z-40 w-8 h-8 rounded-full bg-gray-800 hover:bg-gray-700 text-gray-300">✕</button>

      <LoraSheetList :sheets="sheets" :current-id="mode === 'sheet' ? currentId : ''" v-model:selected="selected"
        @open="openSheet" @new="startNew" @compare="openStack" @delete-many="deleteMany" />

      <div class="flex-1 min-w-0 min-h-0 flex flex-col">
        <LoraSheetForm v-if="mode === 'new' && options" :key="formKey" :options="options" :prefill="prefill"
          @created="onCreated" @toast="onToast" @prefill-used="emit('prefill-used')" />
        <LoraSheetGrid v-else-if="mode === 'sheet' && currentId" :sheet-id="currentId" :is-pinned="isPinned"
          @pin="togglePin" @toast="onToast" @changed="loadList" @deleted="onDeleted" @opened="onOpened" />
        <LoraSheetStack v-else-if="mode === 'stack'" :ids="stackIds" :is-pinned="isPinned"
          @pin="togglePin" @toast="onToast" @open="openSheet" @close="closeStack" />
        <LoraCompareBoard v-else-if="mode === 'board'" :items="pins"
          @remove="togglePin" @clear="pins = []" @close="closeBoard" />

        <!-- Compare-board tray -->
        <div v-if="pins.length && mode !== 'board'"
          class="shrink-0 border-t border-gray-800 px-3 py-2 flex items-center gap-2 bg-slate-950">
          <span class="text-[11px] text-gray-400">📌 {{ pins.length }}/{{ BOARD_MAX }}</span>
          <div v-for="p in pins" :key="p.sha256" class="relative group">
            <img :src="`/api/thumbnails/${p.sha256}.webp`" :title="p.label" class="w-10 h-10 rounded-md object-cover"
              @error="dropBrokenPin(p.sha256)" />
            <button type="button" :aria-label="t('loraSheet.unpin')" @click="togglePin(p)"
              class="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-black/80 text-[9px] leading-4 text-gray-300 opacity-0 group-hover:opacity-100">✕</button>
          </div>
          <button type="button" :disabled="pins.length < 2" @click="openBoard"
            class="ml-auto px-3 py-1 rounded-lg bg-sky-700 hover:bg-sky-600 text-white text-xs disabled:opacity-40">
            {{ t('loraSheet.boardOpen') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

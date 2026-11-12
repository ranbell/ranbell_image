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
import { ref, watch } from 'vue'
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
const emit = defineEmits(['close', 'toast'])
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
function openStack() { stackIds.value = [...selected.value]; mode.value = 'stack' }
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
  if (b?.mode === 'sheet' && b.id) openSheet(b.id)
  else if (b?.mode === 'stack' && stackIds.value.length) mode.value = 'stack'
  else if (sheets.value.length) openSheet(sheets.value[0].id)
  else startNew()
}
async function deleteMany(ids) {
  for (const id of ids) {
    try { await api(`/api/lora/sheets/${id}`, { method: 'DELETE' }) } catch (e) { toast(e.message, 'error') }
  }
  selected.value = []
  stackIds.value = stackIds.value.filter(id => !ids.includes(id))
  await loadList()
  if (ids.includes(currentId.value) || mode.value === 'stack') {
    sheets.value.length ? openSheet(sheets.value[0].id) : startNew()
  }
}

watch(() => props.show, async (open) => {
  if (!open) return
  try {
    await Promise.all([loadOptions(), loadList()])
    if (props.prefill || !sheets.value.length) startNew()
    else if (mode.value === 'new' || !sheets.value.some(s => s.id === currentId.value)) openSheet(sheets.value[0].id)
  } catch (e) { toast(e.message, 'error') }
}, { immediate: true })
</script>

<template>
  <div v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    @click.self="emit('close')">
    <div role="dialog" aria-modal="true" aria-labelledby="lora-sheet-title"
      class="relative w-full max-w-7xl h-[92vh] flex flex-col md:flex-row rounded-2xl overflow-hidden
             bg-slate-950/95 border border-amber-500/30 shadow-2xl text-gray-200">
      <button type="button" :aria-label="t('lora.close')" @click="emit('close')"
        class="absolute top-3 right-3 z-40 w-8 h-8 rounded-full bg-gray-800 hover:bg-gray-700 text-gray-300">✕</button>

      <LoraSheetList :sheets="sheets" :current-id="mode === 'sheet' ? currentId : ''" v-model:selected="selected"
        @open="openSheet" @new="startNew" @compare="openStack" @delete-many="deleteMany" />

      <div class="flex-1 min-w-0 min-h-0 flex flex-col">
        <LoraSheetForm v-if="mode === 'new' && options" :key="formKey" :options="options" :prefill="prefill"
          @created="onCreated" @toast="onToast" />
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
            <img :src="`/api/thumbnails/${p.sha256}.webp`" :title="p.label" class="w-10 h-10 rounded-md object-cover" />
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

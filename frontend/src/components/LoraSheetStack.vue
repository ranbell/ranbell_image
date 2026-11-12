<script setup>
/**
 * Several LoRA sheets stacked on shared strength columns. What every sheet
 * shares is said once at the top; each sheet's heading names only what sets it
 * apart (the base model, the seed, the prompt…), so one difference is read at
 * a time.
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import LoraSheetCell from './LoraSheetCell.vue'
import LoraSheetLightbox from './LoraSheetLightbox.vue'
import { api, rowLabel, colLabel as colLabelOf, mergedColumns, alignedGrid, diffFields } from '../lora/sheetLabels.js'

const props = defineProps({
  ids: { type: Array, required: true },
  isPinned: { type: Function, default: () => false },
})
const emit = defineEmits(['pin', 'toast', 'open', 'close'])
const { t } = useI18n()

const sheets = ref([])
const busy = ref(false)
const lightbox = ref(null)
let pollTimer = null
let alive = true

const colLabel = col => colLabelOf(t, col)
const cols = computed(() => mergedColumns(sheets.value))
const diff = computed(() => diffFields(sheets.value))
const sections = computed(() => sheets.value.map((s, i) => ({
  sheet: s,
  heading: diff.value.per[i].join(' · ') || s.name,
  grid: alignedGrid(s, cols.value),
  ratio: `${s.width} / ${s.height}`,
})))

// The lightbox walks every row of every section as one grid.
const flat = computed(() => sections.value.flatMap(sec => sec.grid.map((keys, r) => ({ sec, r, keys }))))
const cells = computed(() => flat.value.map(({ sec, r, keys }) => keys.map((key, c) => {
  const rd = key ? sec.sheet.renders[key] : null
  if (!rd?.sha256) return null
  return { sha256: rd.sha256, sheet_id: sec.sheet.id, key, w: sec.sheet.width, h: sec.sheet.height,
           label: `${rowLabel(sec.sheet.rows[r])} · ${colLabel(cols.value[c])} · ${sec.heading}` }
})))
function flatIndex(si, r) {
  let n = 0
  for (let i = 0; i < si; i++) n += sections.value[i].grid.length
  return n + r
}

let loadSeq = 0
async function load() {
  // Only the newest load is taken: a slow one for the previous selection
  // must not replace the sheets chosen since.
  const seq = ++loadSeq
  const got = await Promise.all(props.ids.map(id => api(`/api/lora/sheets/${id}`).catch(() => null)))
  if (seq !== loadSeq || !alive) return
  sheets.value = got.filter(Boolean)
  clearTimeout(pollTimer)
  if (alive && sheets.value.some(s => s.drawing)) pollTimer = setTimeout(() => load().catch(() => {}), 2500)
}

async function savePng() {
  busy.value = true
  try {
    const resp = await fetch('/api/lora/sheets/stack.png', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: sheets.value.map(s => s.id) }),
    })
    if (!resp.ok) throw new Error(`${resp.status}`)
    const url = URL.createObjectURL(await resp.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = `lora_sheets_${sheets.value.length}.png`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  } catch (e) { emit('toast', { msg: e.message, type: 'error' }) } finally { busy.value = false }
}

watch(() => props.ids.join(','), () => load().catch(e => emit('toast', { msg: e.message, type: 'error' })), { immediate: true })
onBeforeUnmount(() => { alive = false; clearTimeout(pollTimer) })
</script>

<template>
  <section class="flex-1 min-w-0 flex flex-col min-h-0 relative">
    <div class="p-4 pr-14 border-b border-gray-800 flex flex-col gap-1.5">
      <div class="flex flex-wrap items-center gap-2">
        <h4 class="font-semibold">{{ t('loraSheet.stackTitle', { n: sheets.length }) }}</h4>
        <span v-if="sheets.some(s => s.drawing)" class="text-[11px] text-amber-300 motion-safe:animate-pulse">{{ t('loraSheet.drawing') }}</span>
        <div class="ml-auto flex flex-wrap gap-1.5">
          <button type="button" :disabled="busy || !sheets.length" @click="savePng"
            class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">⬇ {{ t('loraSheet.savePng') }}</button>
          <button type="button" @click="emit('close')"
            class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">{{ t('loraSheet.stackClose') }}</button>
        </div>
      </div>
      <p v-if="diff.common.length" class="text-[11px] text-gray-500">{{ t('loraSheet.stackCommon') }}: {{ diff.common.join(' · ') }}</p>
      <p v-if="diff.prompt" class="text-[11px] text-gray-500 truncate" :title="diff.prompt">{{ diff.prompt }}</p>
    </div>

    <div class="flex-1 overflow-auto p-3">
      <table class="border-separate border-spacing-1">
        <thead>
          <tr>
            <th class="sticky top-0 left-0 z-20 bg-slate-950"></th>
            <th v-for="(col, c) in cols" :key="c"
              class="sticky top-0 z-10 bg-slate-950 text-xs font-semibold text-amber-200 px-1 py-1">{{ colLabel(col) }}</th>
          </tr>
        </thead>
        <tbody v-for="(sec, si) in sections" :key="sec.sheet.id">
          <tr>
            <th :colspan="cols.length + 1" class="text-left pt-3 pb-1">
              <button type="button" class="sticky left-0 text-xs font-semibold text-amber-300 hover:text-amber-100 text-left"
                :title="t('loraSheet.stackOpen', { name: sec.sheet.name })" @click="emit('open', sec.sheet.id)">
                {{ sec.heading }} <span class="text-gray-500 font-normal">— {{ sec.sheet.name }}</span>
                <span v-if="sec.sheet.drawing" class="text-amber-300 motion-safe:animate-pulse">●</span>
              </button>
            </th>
          </tr>
          <tr v-for="(row, r) in sec.sheet.rows" :key="r">
            <th class="sticky left-0 z-10 bg-slate-950 text-left align-top text-xs font-normal pr-2 max-w-40">
              <span class="block break-all" :title="row.lora">{{ rowLabel(row) }}</span>
              <span v-if="row.trigger" class="block text-[10px] text-gray-500 font-mono break-all">{{ row.trigger }}</span>
            </th>
            <td v-for="(key, c) in sec.grid[r]" :key="c" class="p-0">
              <LoraSheetCell :render="key ? (sec.sheet.renders[key] || {}) : null" :ratio="sec.ratio"
                :pinned="!!cells[flatIndex(si, r)]?.[c] && isPinned(cells[flatIndex(si, r)][c])"
                @open="lightbox = { r: flatIndex(si, r), c }"
                @pin="cells[flatIndex(si, r)][c] && emit('pin', cells[flatIndex(si, r)][c])" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <LoraSheetLightbox v-if="lightbox" :cells="cells" :at="lightbox" :is-pinned="isPinned"
      @move="lightbox = $event" @close="lightbox = null" @pin="emit('pin', $event)" />
  </section>
</template>

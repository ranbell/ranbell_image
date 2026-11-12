<script setup>
/**
 * A sheet picture at full size. Arrows walk the grid (empty cells are skipped
 * in the direction of travel), P pins to the compare board, Esc closes.
 */
import { computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  // cells[r][c] = { sha256, label, pin } or null
  cells: { type: Array, required: true },
  at: { type: Object, required: true },   // { r, c }
  isPinned: { type: Function, default: () => false },
})
const emit = defineEmits(['move', 'close', 'pin'])
const { t } = useI18n()

const cell = computed(() => props.cells[props.at.r]?.[props.at.c] || null)
// The picture went away under it (a sheet reloaded without it): close, or the
// key listener would stay and keep eating arrows with nothing on screen.
watch(cell, c => { if (!c) emit('close') })

function step(dr, dc) {
  const rows = props.cells.length
  let { r, c } = props.at
  for (let i = 0; i < 64; i++) {
    r = (r + dr + rows) % rows
    const cols = props.cells[r]?.length || 0
    if (!cols) continue
    c = ((c + dc) % cols + cols) % cols
    if (props.cells[r][c]?.sha256) return emit('move', { r, c })
  }
}

function onKey(e) {
  if (e.key === 'Escape') { e.stopPropagation(); emit('close'); return }
  // Typing in a field (the list's search box stays usable): not for us.
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || '') || e.target?.isContentEditable) return
  if ((e.key === 'p' || e.key === 'P') && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (cell.value) emit('pin', cell.value)
    return
  }
  const move = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }[e.key]
  if (!move) return
  e.preventDefault()
  e.stopPropagation()
  step(...move)
}
onMounted(() => window.addEventListener('keydown', onKey, true))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey, true))
</script>

<template>
  <div v-if="cell" class="absolute inset-0 z-30 bg-black/90 flex flex-col items-center justify-center p-4"
    @click.self="emit('close')">
    <p class="text-sm text-amber-200 mb-2 flex items-center gap-2 flex-wrap justify-center">
      <span>{{ cell.label }}</span>
      <button type="button" class="px-2 py-0.5 rounded-lg text-xs"
        :class="isPinned(cell) ? 'bg-sky-700 text-white' : 'bg-gray-800 hover:bg-gray-700 text-gray-200'"
        @click="emit('pin', cell)">📌 {{ isPinned(cell) ? t('loraSheet.unpin') : t('loraSheet.pin') }}</button>
      <span class="text-gray-500 text-xs">{{ t('loraSheet.arrows') }}</span>
    </p>
    <img :src="`/api/originals/${cell.sha256}`" class="max-w-full max-h-[80vh] object-contain rounded-lg" />
  </div>
</template>

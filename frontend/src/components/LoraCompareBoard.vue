<script setup>
/**
 * Pinned sheet pictures side by side, large. Zoom (wheel, at the cursor) and
 * pan (drag) move every picture together, so the same hand or the same
 * background corner is under the eye in all of them. Double-click resets.
 */
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  items: { type: Array, required: true },   // [{ sha256, label, w, h }]
})
const emit = defineEmits(['remove', 'clear', 'close'])
const { t } = useI18n()

const view = ref({ s: 1, x: 0, y: 0 })
let drag = null

// Mostly tall pictures sit side by side; mostly wide ones stack.
const stacked = computed(() => {
  const wide = props.items.filter(i => (i.w || 1) > (i.h || 1)).length
  return wide > props.items.length / 2
})
const transform = computed(() => `translate(${view.value.x}px, ${view.value.y}px) scale(${view.value.s})`)

function onWheel(e) {
  const rect = e.currentTarget.getBoundingClientRect()
  const px = e.clientX - rect.left, py = e.clientY - rect.top
  const { s, x, y } = view.value
  const ns = Math.min(16, Math.max(1, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))
  if (ns === 1) { view.value = { s: 1, x: 0, y: 0 }; return }
  view.value = { s: ns, x: px - (px - x) * (ns / s), y: py - (py - y) * (ns / s) }
}
function onDown(e) {
  if (e.button !== 0) return
  drag = { x: e.clientX, y: e.clientY, vx: view.value.x, vy: view.value.y }
  e.currentTarget.setPointerCapture?.(e.pointerId)
}
function onMove(e) {
  if (!drag) return
  view.value = { ...view.value, x: drag.vx + e.clientX - drag.x, y: drag.vy + e.clientY - drag.y }
}
function onUp() { drag = null }
function reset() { view.value = { s: 1, x: 0, y: 0 } }
</script>

<template>
  <section class="flex-1 min-w-0 flex flex-col min-h-0">
    <div class="p-4 pr-14 border-b border-gray-800 flex flex-wrap items-center gap-2">
      <h4 class="font-semibold">📌 {{ t('loraSheet.boardTitle', { n: items.length }) }}</h4>
      <span class="text-[11px] text-gray-500">{{ t('loraSheet.boardHelp') }}</span>
      <span v-if="view.s > 1" class="text-[11px] text-sky-300 font-mono">×{{ view.s.toFixed(1) }}</span>
      <div class="ml-auto flex gap-1.5">
        <button type="button" @click="reset" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">{{ t('loraSheet.boardFit') }}</button>
        <button type="button" @click="emit('clear')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">{{ t('loraSheet.boardClear') }}</button>
        <button type="button" @click="emit('close')" class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">{{ t('loraSheet.stackClose') }}</button>
      </div>
    </div>
    <p v-if="!items.length" class="p-6 text-sm text-gray-500">{{ t('loraSheet.boardEmpty') }}</p>
    <div v-else class="flex-1 min-h-0 p-3 flex gap-2" :class="stacked ? 'flex-col' : 'flex-row'">
      <figure v-for="it in items" :key="it.sha256" class="flex-1 min-w-0 min-h-0 flex flex-col gap-1">
        <figcaption class="flex items-center gap-1 text-[11px] text-amber-200">
          <span class="truncate" :title="it.label">{{ it.label }}</span>
          <button type="button" class="ml-auto text-gray-500 hover:text-red-300 shrink-0" :aria-label="t('loraSheet.unpin')"
            @click="emit('remove', it)">✕</button>
        </figcaption>
        <div class="flex-1 min-h-0 overflow-hidden rounded-lg bg-black cursor-grab active:cursor-grabbing select-none touch-none"
          @wheel.prevent="onWheel" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
          @dblclick="reset">
          <img :src="`/api/originals/${it.sha256}`" draggable="false" @error="emit('remove', it)"
            class="w-full h-full object-contain origin-top-left" :style="{ transform }" />
        </div>
      </figure>
    </div>
  </section>
</template>

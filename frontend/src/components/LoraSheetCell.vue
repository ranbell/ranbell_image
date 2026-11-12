<script setup>
/**
 * One cell of a LoRA comparison sheet: the picture, the frame being drawn, a
 * waiting/error placeholder, or nothing (a column this sheet does not have).
 * A drawn picture can be pinned to the compare board.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  render: { type: Object, default: null },     // the sheet's render entry, null = no such column
  ratio: { type: String, default: '1 / 1' },   // CSS aspect-ratio
  live: { type: Object, default: null },       // { url, step, steps } while this cell is drawn
  pinned: { type: Boolean, default: false },
})
const emit = defineEmits(['open', 'pin'])
const { t } = useI18n()

const stepText = computed(() => props.live?.steps
  ? t('loraSheet.step', { v: props.live.step, m: props.live.steps }) : '')
</script>

<template>
  <div v-if="!render" class="w-44" :style="{ aspectRatio: ratio }" />
  <div v-else-if="render.sha256" class="relative group w-44">
    <button type="button" class="block w-full" @click="emit('open')">
      <img :src="`/api/thumbnails/${render.sha256}.webp`" loading="lazy"
        class="w-full rounded-lg object-cover bg-gray-900" :class="pinned ? 'ring-2 ring-sky-400' : ''"
        :style="{ aspectRatio: ratio }" />
    </button>
    <button type="button" :title="pinned ? t('loraSheet.unpin') : t('loraSheet.pin')" @click.stop="emit('pin')"
      class="absolute top-1 right-1 w-6 h-6 rounded-full text-xs bg-black/60 hover:bg-black/80"
      :class="pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'">📌</button>
  </div>
  <div v-else-if="live?.url" class="relative w-44">
    <img :src="live.url" class="w-full rounded-lg object-cover ring-2 ring-amber-500/70" :style="{ aspectRatio: ratio }" />
    <span class="absolute bottom-1 left-1 px-1.5 rounded bg-black/60 text-[10px] text-amber-200 font-mono">{{ stepText || t('loraSheet.drawingNow') }}</span>
    <span v-if="live.steps" class="absolute bottom-0 left-0 h-0.5 rounded bg-amber-400 transition-all"
      :style="{ width: `${100 * live.step / live.steps}%` }"></span>
  </div>
  <div v-else class="w-44 rounded-lg flex items-center justify-center text-[11px] p-2 text-center break-words overflow-hidden"
    :style="{ aspectRatio: ratio }"
    :class="render.status === 'error' ? 'bg-red-950/50 text-red-300' : 'bg-gray-900 text-gray-500 motion-safe:animate-pulse'"
    :title="render.error || ''">
    {{ render.status === 'error' ? `⚠ ${render.error}`.slice(0, 220)
      : live ? (stepText || t('loraSheet.drawingNow')) : t('loraSheet.waiting') }}
  </div>
</template>

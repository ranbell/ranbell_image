<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  history: { type: Array, default: () => [] },
  currentCausality: { type: String, default: '' },
  currentVersion: { type: Number, default: 0 },
  busy: Boolean,
})
const emit = defineEmits(['rollback'])
const { t } = useI18n()

const selectedVersion = ref(null)

const sorted = computed(() =>
  [...(props.history || [])].slice().sort((a, b) => (b.version || 0) - (a.version || 0)),
)

const selected = computed(() =>
  sorted.value.find(h => h.version === selectedVersion.value) || null,
)

watch(sorted, (list) => {
  if (!list.length) {
    selectedVersion.value = null
    return
  }
  if (!list.some(h => h.version === selectedVersion.value)) {
    selectedVersion.value = list[0].version
  }
}, { immediate: true })

function causalityOf(h) {
  return h?.bundle?.world?.causality_one_liner || (h?.reasons || []).join(', ') || '—'
}
</script>

<template>
  <div v-if="sorted.length" class="rounded border border-gray-800 p-3 space-y-2">
    <div class="text-[10px] uppercase text-gray-500">{{ t('weave.rollback') }}</div>
    <div class="grid grid-cols-2 gap-2 text-[10px]">
      <div class="rounded bg-gray-900/80 p-2 space-y-1">
        <div class="text-teal-400">{{ t('weave.rollbackCurrent') }} · v{{ currentVersion || '—' }}</div>
        <p class="text-gray-300 leading-snug">{{ currentCausality || '—' }}</p>
      </div>
      <div class="rounded bg-gray-900/80 p-2 space-y-1">
        <div class="text-amber-300">{{ t('weave.rollbackSelected') }} · v{{ selected?.version || '—' }}</div>
        <p class="text-gray-300 leading-snug">{{ causalityOf(selected) }}</p>
      </div>
    </div>
    <div class="space-y-1 max-h-28 overflow-y-auto">
      <button v-for="h in sorted" :key="h.version"
        class="flex w-full items-start justify-between gap-2 rounded px-2 py-1.5 text-left"
        :class="selectedVersion === h.version ? 'bg-amber-950/50 border border-amber-700/40' : 'bg-gray-900/80 hover:bg-gray-800'"
        :disabled="busy"
        @click="selectedVersion = h.version">
        <span class="text-[10px] text-teal-300">v{{ h.version }}</span>
        <span class="flex-1 text-[10px] text-gray-400 line-clamp-2">{{ causalityOf(h) }}</span>
      </button>
    </div>
    <button
      class="rounded border border-amber-700/50 bg-amber-950/40 px-3 py-1.5 text-xs text-amber-100 disabled:opacity-40"
      :disabled="busy || !selectedVersion"
      @click="emit('rollback', selectedVersion)">
      {{ t('weave.rollbackGo', { v: selectedVersion }) }}
    </button>
  </div>
</template>

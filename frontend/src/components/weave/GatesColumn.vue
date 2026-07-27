<script setup>
import { useI18n } from 'vue-i18n'

defineProps({
  gates: { type: Object, default: () => ({}) },
  crossQa: { type: Object, default: () => ({}) },
  timeline: { type: Array, default: () => [] },
  streamLive: Boolean,
  sessionStatus: String,
  busy: Boolean,
})
const emit = defineEmits(['seal', 'export', 'open-storybook'])
const { t } = useI18n()

function gateClass(g) {
  if (!g) return 'text-gray-600'
  if (g.pass) return 'text-teal-400'
  if (g.pending) return 'text-amber-300'
  if (g.warning) return 'text-amber-400'
  return 'text-rose-400/80'
}

function gateMark(g) {
  if (!g) return '·'
  if (g.pass) return '✓'
  if (g.pending) return '…'
  if (g.warning) return '!'
  return '×'
}

function gateKind(g) {
  if (!g) return ''
  if (g.pass) return t('weave.gatePass')
  if (g.pending) return t('weave.gatePending')
  if (g.warning) return t('weave.gateWarn')
  return t('weave.gateBlock')
}
</script>

<template>
  <aside class="border-l border-gray-800 p-3 space-y-3 overflow-y-auto text-xs">
    <div class="flex items-center justify-between">
      <div class="text-[10px] uppercase tracking-wider text-teal-500/80">{{ t('weave.gates') }}</div>
      <span class="text-[9px]" :class="streamLive ? 'text-teal-400' : 'text-gray-600'">
        SSE {{ streamLive ? '●' : '○' }}
      </span>
    </div>
    <ul class="space-y-1.5">
      <li v-for="(g, key) in gates" :key="key" class="flex justify-between gap-2 items-start">
        <span class="min-w-0">
          <span class="text-gray-400">{{ key }}</span>
          <span v-if="!g.pass" class="block text-[9px] text-gray-600 truncate" :title="g.detail">
            {{ gateKind(g) }}{{ g.detail ? ` · ${g.detail}` : '' }}
          </span>
        </span>
        <span class="shrink-0 font-mono" :class="gateClass(g)" :title="gateKind(g)">{{ gateMark(g) }}</span>
      </li>
    </ul>

    <div class="text-[10px] uppercase tracking-wider text-cyan-500/80 pt-2">{{ t('weave.crossQa') }}</div>
    <ul class="space-y-1 text-[10px] text-gray-400">
      <li>cam {{ crossQa.camera_diversity ?? '—' }}</li>
      <li>through {{ crossQa.throughline_coverage ?? '—' }}</li>
      <li>drift {{ crossQa.identity_drift_risk ?? '—' }}</li>
      <li>lookdev {{ crossQa.lookdev_ready ? '✓' : '·' }}</li>
    </ul>

    <div class="text-[10px] uppercase tracking-wider text-teal-500/80 pt-2">{{ t('weave.timeline') }}</div>
    <ul class="space-y-2 max-h-64 overflow-y-auto">
      <li v-for="ev in timeline.slice().reverse()" :key="ev.id"
        class="rounded bg-gray-900/80 px-2 py-1.5">
        <div class="text-[9px] text-gray-500">{{ ev.actor }} · {{ ev.type }}</div>
        <div class="text-[11px] text-gray-300">{{ ev.text }}</div>
      </li>
    </ul>

    <button v-if="sessionStatus === 'lookdev' || sessionStatus === 'rendering'"
      class="w-full rounded border border-teal-700/40 px-2 py-1.5 text-teal-200 hover:bg-teal-950"
      :disabled="busy" @click="emit('seal')">
      {{ t('weave.seal') }}
    </button>
    <button class="w-full rounded border border-gray-700 px-2 py-1.5 text-gray-400 hover:bg-gray-900"
      :disabled="busy || !sessionStatus" @click="emit('export')">
      {{ t('weave.export') }}
    </button>
    <button class="w-full rounded border border-gray-700 px-2 py-1.5 text-gray-400 hover:bg-gray-900"
      @click="emit('open-storybook')">
      {{ t('weave.openStorybook') }}
    </button>
  </aside>
</template>

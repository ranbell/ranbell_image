<script setup>
// Where the job has got to, while it runs.
//
// The Showrunner (2026-11-02): "the wait is a bit long, so maybe I want a
// diagram of where it has got to". The progress bar said "3 / 6" and named the
// current phase,
// which tells you where you are but not what is coming — and since each
// reference is now read on its own, the wait grew with the number of pictures.
//
// So: the whole route, left to right, with the current stop lit. A step that
// repeats (reading six references) carries its pass count, because that is the
// one place the time actually goes.
import { useI18n } from 'vue-i18n'

const props = defineProps({
  // [{ code, state: 'done' | 'now' | 'todo' }]
  steps: { type: Array, default: () => [] },
  // A repeating step's progress through its passes; 0 when it does not repeat.
  n:     { type: Number, default: 0 },
  total: { type: Number, default: 0 },
})
const { t } = useI18n()

const label = code => t(`refine.phase.${code}`).replace(/[.…]+$/, '')
</script>

<template>
  <ol v-if="steps.length" class="flex flex-wrap items-center gap-x-1 gap-y-1 select-none">
    <li v-for="(s, i) in steps" :key="s.code" class="flex items-center gap-1">
      <span
        class="flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] leading-none transition-colors"
        :class="s.state === 'now'
          ? 'border-purple-400/70 bg-purple-900/50 text-purple-100'
          : s.state === 'done'
            ? 'border-gray-700 bg-gray-800/60 text-gray-500'
            : 'border-gray-800 bg-transparent text-gray-600'"
        :title="label(s.code)"
      >
        <span v-if="s.state === 'done'" class="text-emerald-500/80">✓</span>
        <span v-else-if="s.state === 'now'"
              class="w-1.5 h-1.5 rounded-full bg-purple-300 animate-pulse"></span>
        <span v-else class="w-1.5 h-1.5 rounded-full border border-gray-700"></span>
        <span :class="s.state === 'todo' ? 'hidden sm:inline' : ''">{{ label(s.code) }}</span>
        <span v-if="s.state === 'now' && total > 1" class="font-mono text-purple-300/90">
          {{ n }}/{{ total }}
        </span>
      </span>
      <span v-if="i < steps.length - 1"
            class="text-[9px]"
            :class="s.state === 'done' ? 'text-gray-600' : 'text-gray-800'">›</span>
    </li>
  </ol>
</template>

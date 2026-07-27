<script setup>
import { useI18n } from 'vue-i18n'

defineProps({
  weaveScore: { type: Object, default: null },
  vlmAnswers: { type: Object, default: null },
  selectedPanel: { type: Object, default: null },
  useVlmAssist: Boolean,
  busy: Boolean,
  sessionId: String,
})
const emit = defineEmits(['score', 'vlm'])
const { t } = useI18n()
</script>

<template>
  <div class="rounded border border-gray-800/80 bg-gray-950/40 p-2 space-y-2">
    <div class="flex items-center justify-between gap-2">
      <div class="text-[10px] uppercase text-teal-500/80">{{ t('weave.score') }}</div>
      <button class="text-[10px] text-teal-300 disabled:opacity-40" :disabled="busy || !sessionId"
        @click="emit('score')">{{ t('weave.scoreRefresh') }}</button>
    </div>
    <div v-if="weaveScore" class="space-y-1">
      <div class="text-sm text-teal-100">
        {{ t('weave.scoreOverall') }}:
        <span class="font-mono">{{ Number(weaveScore.overall ?? weaveScore.session_overall ?? 0).toFixed(2) }}</span>
      </div>
      <div class="flex flex-wrap gap-1">
        <span v-for="(v, k) in (weaveScore.dimensions || {})" :key="k"
          class="rounded bg-gray-800 px-1.5 py-0.5 text-[9px] text-gray-300">
          {{ k }} {{ typeof v === 'number' ? v.toFixed(2) : v }}
        </span>
      </div>
    </div>
    <p v-else class="text-[10px] text-gray-600">{{ t('weave.scoreEmpty') }}</p>

    <div class="flex items-center justify-between gap-2 pt-1">
      <div class="text-[10px] uppercase text-cyan-500/80">{{ t('weave.vlm') }}</div>
      <div class="flex gap-2">
        <button class="text-[10px] text-cyan-300 disabled:opacity-40"
          :disabled="busy || !useVlmAssist || !selectedPanel?.sample?.image_id"
          @click="emit('vlm', false)">{{ t('weave.vlmRun') }}</button>
        <button class="text-[10px] text-gray-400 disabled:opacity-40"
          :disabled="busy || !useVlmAssist || !selectedPanel?.sample?.image_id"
          @click="emit('vlm', true)">{{ t('weave.vlmHeuristic') }}</button>
      </div>
    </div>
    <div v-if="vlmAnswers?.answers" class="grid grid-cols-2 gap-1">
      <div v-for="(v, k) in vlmAnswers.answers" :key="k"
        class="rounded px-1.5 py-1 text-[10px]"
        :class="v === true ? 'bg-teal-950/60 text-teal-200' : v === false ? 'bg-amber-950/50 text-amber-200' : 'bg-gray-900 text-gray-500'">
        {{ k }}: {{ v === true ? '✓' : v === false ? '✗' : '?' }}
      </div>
      <div class="col-span-2 text-[9px] text-gray-500">
        {{ vlmAnswers.method }} · {{ selectedPanel?.key }}
      </div>
    </div>
    <p v-else class="text-[10px] text-gray-600">{{ t('weave.vlmEmpty') }}</p>
  </div>
</template>

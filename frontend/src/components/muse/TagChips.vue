<script setup>
/*
 * Candidate tags, coloured by where they came from.
 *
 * Provenance is the whole story of this step: "the theme asked for this" and
 * "the vocabulary search wandered out here" produce very different pictures,
 * and the user deserves to see which is which before spending a render on it.
 * Clicking a chip rejects the tag — the gap Inspire left open, where the
 * backend accepted a blacklist the panel never sent.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  rejected: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['reject', 'restore'])
const { t } = useI18n()

const SOURCE_CLASS = {
  split: 'border-teal-500/40 bg-teal-900/30 text-teal-200',
  topic: 'border-cyan-500/40 bg-cyan-900/30 text-cyan-200',
  stranger: 'border-amber-500/40 bg-amber-900/30 text-amber-200',
  lunatic: 'border-amber-400/50 bg-amber-800/40 text-amber-100',
  frontier: 'border-violet-500/40 bg-violet-900/30 text-violet-200',
}

const SOURCE_LABEL = {
  split: 'tags.topic',
  topic: 'tags.topic',
  stranger: 'tags.lunatic',
  lunatic: 'tags.lunatic',
  frontier: 'tags.frontier',
}

const grouped = computed(() => {
  const order = ['split', 'topic', 'stranger', 'lunatic', 'frontier']
  const byLabel = new Map()
  for (const key of order) {
    const rows = props.rows.filter(r => r.source === key)
    if (!rows.length) continue
    const label = SOURCE_LABEL[key] || 'tags.topic'
    if (!byLabel.has(label)) byLabel.set(label, { label, key, rows: [] })
    byLabel.get(label).rows.push(...rows)
  }
  return [...byLabel.values()]
})

function chipClass(row) {
  return SOURCE_CLASS[row.source] || 'border-gray-600/40 bg-gray-800/40 text-gray-300'
}
</script>

<template>
  <div class="space-y-3">
    <div v-for="group in grouped" :key="group.label">
      <p class="sb-label mb-1">{{ t(`muse.${group.label}`) }}</p>
      <div class="flex flex-wrap gap-1">
        <button
          v-for="row in group.rows"
          :key="row.tag"
          type="button"
          :disabled="busy"
          :title="t('muse.tags.rejectHint')"
          class="px-2 py-0.5 rounded border text-[11px] font-mono transition-opacity hover:opacity-60 disabled:cursor-not-allowed"
          :class="chipClass(row)"
          @click="emit('reject', row.tag)"
        >{{ row.tag }}</button>
      </div>
    </div>

    <div v-if="rejected.length">
      <p class="sb-label mb-1">{{ t('muse.tags.rejected') }}</p>
      <div class="flex flex-wrap gap-1">
        <button
          v-for="tag in rejected"
          :key="tag"
          type="button"
          :disabled="busy"
          class="px-2 py-0.5 rounded border border-gray-700 bg-black/30 text-[11px] font-mono text-gray-500 line-through hover:text-gray-300"
          @click="emit('restore', tag)"
        >{{ tag }}</button>
      </div>
    </div>

    <p v-if="rows.length" class="text-[10px] text-gray-600">{{ t('muse.tags.rejectHint') }}</p>
  </div>
</template>

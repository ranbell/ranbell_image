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
  compose: 'border-cyan-500/40 bg-cyan-900/30 text-cyan-200',
  topup: 'border-violet-500/40 bg-violet-900/30 text-violet-200',
}

const SOURCE_LABEL = {
  compose: 'tags.composed',
  topup: 'tags.topup',
}

const grouped = computed(() => {
  const byLabel = new Map()
  for (const key of ['compose', 'topup']) {
    const rows = props.rows.filter(r => r.source === key)
    if (!rows.length) continue
    byLabel.set(key, { label: SOURCE_LABEL[key], key, rows })
  }
  return [...byLabel.values()]
})

function chipClass(row) {
  return SOURCE_CLASS[row.source] || 'border-gray-600/40 bg-gray-800/40 text-gray-300'
}

// The Danbooru post count, so the user can see why a tag looks obscure before
// deciding whether to keep it.
function chipTitle(row) {
  const hint = t('muse.tags.rejectHint')
  return row.count ? `${row.tag} — ${row.count.toLocaleString()} posts\n${hint}` : hint
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
          :title="chipTitle(row)"
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

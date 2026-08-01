<script setup>
/*
 * The prompt, aspect by aspect, with every aspect editable.
 *
 * A flat tag list hides how the budget is being spent. Laid out by slot you can
 * see at a glance that Outfit has three tags and Place has one, which is the
 * failure that started this — a pool theme spending its whole prompt on
 * swimwear. Clicking a tag removes it; typing adds one; the cap is shown so it
 * is obvious when a slot is full.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  slots: { type: Object, default: () => ({}) },   // key → [{tag, source}]
  spec: { type: Array, default: () => [] },       // [{key, label, cap, editable}]
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['set'])
const { t } = useI18n()

const SOURCE_CLASS = {
  compose: 'border-cyan-500/40 bg-cyan-900/30 text-cyan-200',
  vocab: 'border-emerald-500/40 bg-emerald-900/30 text-emerald-200',
  harvest: 'border-white/10 bg-black/30 text-gray-300',
  topup: 'border-violet-500/40 bg-violet-900/30 text-violet-200',
  character: 'border-teal-400/50 bg-teal-800/40 text-teal-100',
  user: 'border-amber-400/50 bg-amber-900/40 text-amber-100',
}

const rows = computed(() =>
  props.spec.map(s => ({ ...s, tags: props.slots[s.key] || [] })))

function chipClass(row) {
  return SOURCE_CLASS[row.source] || SOURCE_CLASS.harvest
}

function remove(slot, tag) {
  emit('set', { slot: slot.key, tags: (props.slots[slot.key] || [])
    .map(r => r.tag).filter(t => t !== tag) })
}

function add(slot, event) {
  const raw = event.target.value.trim()
  if (!raw) return
  const existing = (props.slots[slot.key] || []).map(r => r.tag)
  emit('set', { slot: slot.key, tags: [...existing, ...raw.split(',').map(t => t.trim())] })
  event.target.value = ''
}
</script>

<template>
  <div class="space-y-2">
    <div
      v-for="row in rows"
      :key="row.key"
      class="grid grid-cols-[92px_1fr] gap-2 items-start"
    >
      <div class="pt-0.5">
        <p class="text-[11px] text-[var(--sb-amber)]">{{ row.label }}</p>
        <p class="text-[10px]" :class="row.tags.length >= row.cap ? 'text-amber-500/70' : 'text-gray-600'">
          {{ row.tags.length }}/{{ row.cap }}
        </p>
      </div>
      <div class="min-w-0">
        <div class="flex flex-wrap gap-1">
          <button
            v-for="r in row.tags"
            :key="r.tag"
            type="button"
            :disabled="busy || !row.editable"
            :title="row.editable ? t('muse.slots.removeHint') : t('muse.slots.lockedHint')"
            class="px-1.5 py-0.5 rounded border text-[10px] font-mono transition-opacity"
            :class="[chipClass(r), row.editable ? 'hover:opacity-60' : 'opacity-80 cursor-default']"
            @click="row.editable && remove(row, r.tag)"
          >{{ r.tag }}</button>
          <input
            v-if="row.editable"
            type="text"
            :disabled="busy"
            :placeholder="t('muse.slots.addPlaceholder')"
            class="px-1.5 py-0.5 rounded border border-dashed border-white/15 bg-transparent text-[10px] font-mono text-gray-300 w-24 focus:outline-none focus:border-white/35"
            @change="add(row, $event)"
          />
        </div>
      </div>
    </div>
    <p class="text-[10px] text-gray-600 pt-1">{{ t('muse.slots.hint') }}</p>
  </div>
</template>

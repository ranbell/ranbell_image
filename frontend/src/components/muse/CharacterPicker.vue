<script setup>
/*
 * Pick a character by looking at her.
 *
 * A character with a reference board shows her portrait; one without shows a
 * name and a "draw the board" button, because a list of tag counts is not how
 * anyone recognises a character.
 */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  characters: { type: Array, default: () => [] },
  selectedId: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  canDrawBoard: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'draw-board'])
const { t, locale } = useI18n()

const query = ref('')

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.characters
  return props.characters.filter(c =>
    `${c.name} ${c.name_ja} ${c.summary} ${c.summary_ja}`.toLowerCase().includes(q))
})

const isJa = computed(() => String(locale.value).startsWith('ja'))
function label(c) { return (isJa.value ? c.name_ja : c.name) || c.name || c.preset_key }
function blurb(c) { return (isJa.value ? c.summary_ja : c.summary) || '' }
function portrait(c) {
  const sha = c.board?.portrait || c.board?.sheet || ''
  return sha ? `/api/thumbnails/${sha}.webp` : ''
}
</script>

<template>
  <div class="flex flex-col gap-2 min-h-0">
    <input
      v-model="query"
      type="search"
      class="sb-input"
      :placeholder="t('muse.pickCharacter')"
    />

    <p v-if="!characters.length" class="text-xs text-gray-500 py-4 text-center">
      {{ t('characters.empty') }}
    </p>

    <div v-else class="grid grid-cols-2 gap-2 overflow-y-auto min-h-0 pr-1">
      <button
        v-for="c in filtered"
        :key="c.id"
        type="button"
        :disabled="busy"
        class="group relative rounded-lg border overflow-hidden text-left transition-colors disabled:opacity-50"
        :class="c.id === selectedId
          ? 'border-teal-400/70 ring-1 ring-teal-400/40'
          : 'border-white/10 hover:border-white/25'"
        @click="emit('pick', c.id)"
      >
        <div class="aspect-[3/4] bg-black/40 flex items-center justify-center overflow-hidden">
          <img
            v-if="portrait(c)"
            :src="portrait(c)"
            :alt="label(c)"
            loading="lazy"
            class="w-full h-full object-cover"
          />
          <span v-else class="text-[10px] text-gray-600 px-2 text-center">
            {{ t('characters.boardHint') }}
          </span>
        </div>
        <div class="px-2 py-1.5">
          <p class="text-[11px] text-gray-200 truncate">{{ label(c) }}</p>
          <p class="text-[10px] text-gray-500 truncate">{{ blurb(c) }}</p>
        </div>
      </button>
    </div>

    <button
      v-if="selectedId && canDrawBoard"
      type="button"
      class="sb-btn justify-center"
      :disabled="busy"
      @click="emit('draw-board', selectedId)"
    >{{ t('characters.generateBoard') }}</button>
  </div>
</template>

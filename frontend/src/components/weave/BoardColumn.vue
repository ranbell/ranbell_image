<script setup>
import { useI18n } from 'vue-i18n'

defineProps({
  personalityText: String,
  useGalleryNn: Boolean,
  useVlmAssist: Boolean,
  useSpicer: Boolean,
  useMoodSlot: Boolean,
  multiSeed: { type: Number, default: 1 },
  character: { type: Object, default: () => ({}) },
  boardImages: { type: Array, default: () => [] },
  galleryRefs: { type: Array, default: () => [] },
  gallerySpice: { type: Array, default: () => [] },
  tagDiff: { type: Object, default: null },
  galleryNnStatus: { type: Object, default: null },
  characterWarnings: { type: Array, default: () => [] },
  suggestReinfer: Boolean,
  busy: Boolean,
  thumb: { type: Function, required: true },
})
const emit = defineEmits([
  'update:personalityText',
  'update:useGalleryNn',
  'update:useVlmAssist',
  'update:useSpicer',
  'update:useMoodSlot',
  'update:multiSeed',
  'reinfer',
])
const { t } = useI18n()
</script>

<template>
  <aside class="border-r border-gray-800 p-3 space-y-3 overflow-y-auto">
    <label class="block text-[10px] uppercase tracking-wider text-teal-500/80">{{ t('weave.personality') }}</label>
    <textarea
      :value="personalityText"
      rows="4"
      class="w-full rounded border border-gray-800 bg-gray-900 px-2 py-1.5 text-xs"
      :placeholder="t('weave.personalityPh')"
      @input="emit('update:personalityText', $event.target.value)"
    />

    <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-1.5 cursor-pointer">
      <input
        :checked="useGalleryNn"
        type="checkbox"
        class="mt-0.5 accent-teal-500"
        @change="emit('update:useGalleryNn', $event.target.checked)"
      />
      <span>
        <span class="block text-[11px] text-teal-100">{{ t('weave.galleryNn') }}</span>
        <span class="block text-[10px] text-gray-500 leading-snug">{{ t('weave.galleryNnHint') }}</span>
      </span>
    </label>

    <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-1.5 cursor-pointer">
      <input
        :checked="useVlmAssist"
        type="checkbox"
        class="mt-0.5 accent-teal-500"
        @change="emit('update:useVlmAssist', $event.target.checked)"
      />
      <span>
        <span class="block text-[11px] text-teal-100">{{ t('weave.vlmAssist') }}</span>
        <span class="block text-[10px] text-gray-500 leading-snug">{{ t('weave.vlmAssistHint') }}</span>
      </span>
    </label>

    <div class="rounded border border-violet-900/40 bg-violet-950/20 p-2 space-y-2">
      <div class="text-[10px] uppercase tracking-wider text-violet-400/90">{{ t('weave.lab') }}</div>
      <label class="flex items-start gap-2 cursor-pointer">
        <input
          :checked="useSpicer"
          type="checkbox"
          class="mt-0.5 accent-violet-500"
          @change="emit('update:useSpicer', $event.target.checked)"
        />
        <span>
          <span class="block text-[11px] text-violet-100">{{ t('weave.spicer') }}</span>
          <span class="block text-[10px] text-gray-500 leading-snug">{{ t('weave.spicerHint') }}</span>
        </span>
      </label>
      <label class="flex items-start gap-2 cursor-pointer">
        <input
          :checked="useMoodSlot"
          type="checkbox"
          class="mt-0.5 accent-violet-500"
          @change="emit('update:useMoodSlot', $event.target.checked)"
        />
        <span>
          <span class="block text-[11px] text-violet-100">{{ t('weave.moodSlot') }}</span>
          <span class="block text-[10px] text-gray-500 leading-snug">{{ t('weave.moodSlotHint') }}</span>
        </span>
      </label>
      <label class="flex items-center gap-2 text-[11px] text-violet-100">
        <span class="shrink-0">{{ t('weave.multiSeed') }}</span>
        <select
          :value="multiSeed"
          class="rounded border border-gray-800 bg-gray-900 px-1.5 py-0.5 text-[11px]"
          @change="emit('update:multiSeed', Number($event.target.value))"
        >
          <option :value="1">1</option>
          <option :value="2">2</option>
          <option :value="3">3</option>
        </select>
        <span class="text-[10px] text-gray-500">{{ t('weave.multiSeedHint') }}</span>
      </label>
    </div>

    <div class="space-y-1">
      <div class="text-[10px] text-gray-500">identity</div>
      <div class="flex flex-wrap gap-1">
        <span v-for="tag in (character.identity_tags || [])" :key="'i'+tag"
          class="rounded bg-teal-950/80 px-1.5 py-0.5 text-[10px] text-teal-200">{{ tag }}</span>
      </div>
      <div class="text-[10px] text-gray-500 mt-2">prop</div>
      <div class="flex flex-wrap gap-1">
        <span v-for="tag in (character.prop_tags || [])" :key="'p'+tag"
          class="rounded bg-amber-950/80 px-1.5 py-0.5 text-[10px] text-amber-200">{{ tag }}</span>
      </div>
      <template v-if="gallerySpice.length">
        <div class="text-[10px] text-gray-500 mt-2">{{ t('weave.gallerySpice') }}</div>
        <div class="flex flex-wrap gap-1">
          <span v-for="tag in gallerySpice" :key="'s'+tag"
            class="rounded bg-cyan-950/80 px-1.5 py-0.5 text-[10px] text-cyan-200">{{ tag }}</span>
        </div>
      </template>
      <template v-if="(character.lab_spice || []).length">
        <div class="text-[10px] text-gray-500 mt-2">{{ t('weave.labSpice') }}</div>
        <div class="flex flex-wrap gap-1">
          <span v-for="tag in character.lab_spice" :key="'ls'+tag"
            class="rounded bg-violet-950/80 px-1.5 py-0.5 text-[10px] text-violet-200">{{ tag }}</span>
        </div>
      </template>
    </div>

    <div v-if="tagDiff && (tagDiff.added?.length || tagDiff.removed?.length || tagDiff.spice?.length)"
      class="rounded border border-cyan-900/50 bg-cyan-950/20 p-2 space-y-1">
      <div class="text-[10px] uppercase tracking-wider text-cyan-400/90">{{ t('weave.tagDiff') }}</div>
      <div v-if="tagDiff.added_from_reference?.length" class="text-[10px] text-teal-200">
        ref: +{{ tagDiff.added_from_reference.join(', ') }}
      </div>
      <div v-if="tagDiff.added_from_gallery?.length" class="text-[10px] text-cyan-200">
        gallery: +{{ tagDiff.added_from_gallery.join(', ') }}
      </div>
      <div v-if="tagDiff.removed?.length" class="text-[10px] text-amber-300">
        −{{ tagDiff.removed.join(', ') }}
      </div>
      <div class="text-[10px] text-gray-500">{{ t('weave.tagDiffHint') }}</div>
    </div>

    <p v-if="useGalleryNn && galleryNnStatus && !galleryNnStatus.applied && galleryNnStatus.reason && galleryNnStatus.reason !== 'skipped'"
      class="text-[10px] text-amber-400/90">
      {{ t('weave.galleryNnSkip', { reason: galleryNnStatus.reason }) }}
    </p>

    <div v-if="galleryRefs.length" class="space-y-1">
      <div class="text-[10px] uppercase tracking-wider text-cyan-500/80">{{ t('weave.galleryRefs') }}</div>
      <div class="grid grid-cols-3 gap-1">
        <a v-for="ref in galleryRefs" :key="ref.sha256"
          :href="`/api/images/${ref.sha256}`" target="_blank" rel="noopener"
          class="rounded border border-gray-800 overflow-hidden bg-gray-900"
          :title="ref.name || ref.sha256">
          <img v-if="thumb(ref.sha256)" :src="thumb(ref.sha256)" class="w-full aspect-square object-cover" />
        </a>
      </div>
    </div>
    <p v-else-if="useGalleryNn && character.identity_tags?.length && galleryNnStatus?.applied === false"
      class="text-[10px] text-gray-500">{{ t('weave.galleryEmpty') }}</p>

    <div class="text-[10px] uppercase tracking-wider text-teal-500/80">{{ t('weave.board') }}</div>
    <div class="grid grid-cols-1 gap-2">
      <div v-for="img in boardImages" :key="img.slot"
        class="rounded border border-gray-800 bg-gray-900/80 overflow-hidden">
        <div class="px-2 py-1 text-[10px] text-gray-400 flex justify-between">
          <span>{{ img.slot }}</span>
          <span v-if="img.pending || !img.image_id" class="text-amber-400">…</span>
        </div>
        <img v-if="thumb(img.image_id)" :src="thumb(img.image_id)" class="w-full aspect-[3/4] object-cover" />
        <div v-else class="aspect-[3/4] flex items-center justify-center text-[10px] text-gray-600">{{ t('weave.noImage') }}</div>
      </div>
    </div>
    <div class="text-[10px] text-gray-500" v-if="character.reasoning_ja">{{ character.reasoning_ja }}</div>

    <div v-if="characterWarnings.length" class="rounded border border-amber-800/50 bg-amber-950/20 p-2 space-y-1">
      <div class="text-[10px] text-amber-400">{{ t('weave.warnings') }}</div>
      <p v-for="(w, i) in characterWarnings" :key="i" class="text-[10px] text-amber-100/90">{{ w.problem }}</p>
    </div>

    <button v-if="character.identity_locked || suggestReinfer"
      class="w-full rounded border border-amber-700/40 bg-amber-950/40 px-2 py-1.5 text-[11px] text-amber-100 disabled:opacity-40"
      :disabled="busy" @click="emit('reinfer')">
      {{ t('weave.reinfer') }}
    </button>
  </aside>
</template>

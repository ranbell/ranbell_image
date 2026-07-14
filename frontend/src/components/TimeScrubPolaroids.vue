<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  axes: { type: Array, default: () => ['past', 'present', 'future'] },
  frontIndex: { type: Number, default: 1 },
  baseAxis: { type: String, default: 'present' },
  imageFor: { type: Function, required: true },
  size: { type: String, default: 'md' }, // sm | md | lg
  showScrub: { type: Boolean, default: true },
  pendingLabel: { type: String, default: '…' },
})

const emit = defineEmits(['update:frontIndex', 'open-image', 'thumb-error'])

const { t } = useI18n()

const front = computed(() =>
  props.axes[Math.max(0, Math.min(props.axes.length - 1, props.frontIndex))] || props.axes[1]
)

function setFront(i, e) {
  e?.stopPropagation?.()
  emit('update:frontIndex', i)
}

function onScrub(e) {
  emit('update:frontIndex', Number(e.target.value))
}

function onImgError(e, sha) {
  emit('thumb-error', e, sha)
}
</script>

<template>
  <div class="ts-polaroid-wrap" :class="'ts-size-' + size" @click.stop>
    <div
      class="polaroid-stack is-scrubbing"
      :class="{ 'polaroid-stack-sm': size === 'sm', 'polaroid-stack-lg': size === 'lg' }"
      @pointerdown.stop
    >
      <div
        v-for="(axis, i) in axes"
        :key="axis"
        class="polaroid"
        :class="[
          axis,
          axis === baseAxis ? 'base' : '',
          axis === front ? 'is-front' : '',
        ]"
        @click.stop="setFront(i)"
      >
        <img
          v-if="imageFor(axis)"
          :src="`/api/thumbnails/${imageFor(axis)}.webp`"
          loading="lazy"
          @error="onImgError($event, imageFor(axis))"
          @dblclick.stop="imageFor(axis) && emit('open-image', imageFor(axis))"
        />
        <span v-else class="polaroid-empty">{{ pendingLabel }}</span>
      </div>
    </div>

    <div v-if="showScrub" class="ts-scrub" @click.stop @pointerdown.stop>
      <button
        v-for="(axis, i) in axes"
        :key="'t-' + axis"
        type="button"
        class="ts-scrub-tick"
        :class="{ 'is-on': i === frontIndex, 'is-base': axis === baseAxis }"
        :title="t('chronicle.axis.' + axis)"
        @click="setFront(i)"
      >
        {{ t('chronicle.axis.' + axis) }}
      </button>
      <input
        class="ts-scrub-range"
        type="range"
        min="0"
        :max="axes.length - 1"
        step="1"
        :value="frontIndex"
        :aria-label="t('storybook.timeScrubAria')"
        @input="onScrub"
      />
    </div>
  </div>
</template>

<style scoped>
.ts-polaroid-wrap {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 100%;
}

.polaroid-stack {
  position: relative;
  aspect-ratio: 1 / 1;
  width: 100%;
  perspective: 900px;
}
.polaroid-stack-lg {
  max-width: 22rem;
  margin: 0 auto;
}

.polaroid {
  position: absolute;
  top: 12%;
  left: 16%;
  width: 68%;
  height: 68%;
  background: #d8dce4;
  border: 4px solid #d8dce4;
  border-bottom-width: 22px;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
  transition: transform 0.5s cubic-bezier(0.2, 0.8, 0.2, 1),
    box-shadow 0.5s, opacity 0.35s, filter 0.35s;
  overflow: hidden;
  cursor: pointer;
}
.polaroid img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.polaroid-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #64748b;
  font-size: 0.7rem;
  padding: 0.5rem;
  text-align: center;
}
.polaroid.past { transform: translate(-16%, -6%) rotate(-6deg); z-index: 1; }
.polaroid.present { transform: translate(0, 0) rotate(2deg); z-index: 2; }
.polaroid.future { transform: translate(16%, 6%) rotate(8deg); z-index: 3; }
.polaroid.base {
  box-shadow: 0 0 0 2px rgba(232, 196, 122, 0.55), 0 6px 16px rgba(0, 0, 0, 0.4);
}

.polaroid-stack.is-scrubbing .polaroid {
  opacity: 0.42;
  filter: brightness(0.78) saturate(0.85);
}
.polaroid-stack.is-scrubbing .polaroid.is-front {
  z-index: 12 !important;
  opacity: 1;
  filter: none;
  transform: translate(0, -6%) rotate(0deg) scale(1.12) !important;
  box-shadow:
    0 0 0 2px rgba(232, 196, 122, 0.45),
    0 16px 36px rgba(0, 0, 0, 0.55);
}

.polaroid-stack-sm .polaroid {
  border-width: 3px;
  border-bottom-width: 14px;
}

.ts-scrub {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0.25rem 0.35rem;
  align-items: center;
}
.ts-scrub-tick {
  font-size: 0.55rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #5c6470;
  padding: 0.15rem 0.2rem;
  border-radius: 0.3rem;
  border: 1px solid transparent;
  transition: color 0.15s, background 0.15s, border-color 0.15s;
}
.ts-scrub-tick.is-on {
  color: #fef3c7;
  background: rgba(146, 64, 14, 0.4);
  border-color: rgba(232, 196, 122, 0.3);
}
.ts-scrub-tick.is-base:not(.is-on) {
  color: #7dd3c7;
}
.ts-scrub-range {
  grid-column: 1 / -1;
  width: 100%;
  accent-color: #e8c47a;
  height: 0.9rem;
}
</style>

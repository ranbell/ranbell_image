<script setup>
/**
 * Past / present / future as a row of polaroids — all three visible at once.
 *
 * This used to stack them and dim everything except one "front" card, so
 * reading the whole timeline meant scrubbing back and forth. The cards now sit
 * side by side, equally lit, each captioned in the polaroid's own bottom strip.
 */
import { useI18n } from 'vue-i18n'

defineProps({
  axes: { type: Array, default: () => ['past', 'present', 'future'] },
  baseAxis: { type: String, default: 'present' },
  imageFor: { type: Function, required: true },
  size: { type: String, default: 'md' }, // sm | md | lg
  pendingLabel: { type: String, default: '…' },
})

const emit = defineEmits(['open-image', 'thumb-error'])

const { t } = useI18n()

function onImgError(e, sha) {
  emit('thumb-error', e, sha)
}
</script>

<template>
  <div class="ts-row" :class="'ts-size-' + size" @click.stop @pointerdown.stop>
    <figure
      v-for="axis in axes"
      :key="axis"
      class="polaroid"
      :class="[axis, axis === baseAxis ? 'base' : '']"
      :title="t('chronicle.axis.' + axis)"
    >
      <div class="polaroid-window">
        <img
          v-if="imageFor(axis)"
          :src="`/api/thumbnails/${imageFor(axis)}.webp`"
          loading="lazy"
          :alt="t('chronicle.axis.' + axis)"
          @error="onImgError($event, imageFor(axis))"
          @click.stop="emit('open-image', imageFor(axis))"
          @dblclick.stop="emit('open-image', imageFor(axis))"
        />
        <span v-else class="polaroid-empty">{{ pendingLabel }}</span>
      </div>
      <figcaption class="polaroid-caption">
        {{ t('chronicle.axis.' + axis) }}
      </figcaption>
    </figure>
  </div>
</template>

<style scoped>
.ts-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.5rem;
  width: 100%;
  align-items: start;
}
.ts-size-lg {
  gap: 0.9rem;
  max-width: 34rem;
  margin: 0 auto;
}
.ts-size-sm { gap: 0.3rem; }

.polaroid {
  margin: 0;
  background: linear-gradient(170deg, #eef1f6 0%, #d8dce4 100%);
  padding: 4px 4px 0;
  border-radius: 2px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  transition: transform 0.25s cubic-bezier(0.2, 0.8, 0.2, 1), box-shadow 0.25s;
  /* A hand-pinned tilt, alternating so the row reads as three separate prints
     rather than a grid. Kept small so nothing overlaps its neighbour. */
  transform: rotate(-1.6deg);
}
.polaroid.present { transform: rotate(1.2deg); }
.polaroid.future { transform: rotate(-0.6deg); }
.polaroid:hover {
  transform: rotate(0deg) translateY(-3px) scale(1.03);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.5);
  z-index: 2;
}

.polaroid-window {
  position: relative;
  aspect-ratio: 1 / 1;
  overflow: hidden;
  background: #11151c;
}
.polaroid-window img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  cursor: zoom-in;
}
.polaroid-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #64748b;
  font-size: 0.7rem;
  padding: 0.4rem;
  text-align: center;
}

/* The classic polaroid chin, now carrying the axis label. */
.polaroid-caption {
  font-family: var(--sb-font-display, inherit);
  font-size: 0.6rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  text-align: center;
  color: #4b5563;
  padding: 0.3rem 0.15rem 0.4rem;
  line-height: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ts-size-lg .polaroid-caption {
  font-size: 0.72rem;
  padding: 0.45rem 0.2rem 0.6rem;
}
.ts-size-sm .polaroid { padding: 3px 3px 0; }
.ts-size-sm .polaroid-caption {
  font-size: 0.5rem;
  padding: 0.2rem 0.1rem 0.28rem;
}

/* The 元絵 keeps its amber pin. */
.polaroid.base {
  box-shadow: 0 0 0 2px rgba(232, 196, 122, 0.55), 0 4px 12px rgba(0, 0, 0, 0.4);
}
.polaroid.base .polaroid-caption { color: #92400e; font-weight: 600; }

@media (prefers-reduced-motion: reduce) {
  .polaroid, .polaroid:hover { transition: none; transform: none; }
}
</style>

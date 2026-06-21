<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  progress:      { type: Number,  default: 0 },
  progressText:  { type: String,  default: null },
  indeterminate: { type: Boolean, default: false },
  eta:           { type: Number,  default: null },
  color:         { type: String,  default: 'default' },
  size:          { type: String,  default: 'sm' },
  currentStep:   { type: Number,  default: 0 },
  totalSteps:    { type: Number,  default: 0 },
})

function formatEta(s: number): string {
  if (s < 0) return ''
  const sec = Math.floor(s)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60), r = sec % 60
  return r > 0 ? `${m}m ${r}s` : `${m}m`
}

const pct = computed(() => Math.round((props.progress ?? 0) * 100))
const label = computed(() => props.progressText ?? `${pct.value}%`)
const hasOverlay = computed(() => props.currentStep > 0 && props.totalSteps > 0)
</script>

<template>
  <div class="pb-wrapper">
    <div
      class="pb-track"
      :class="hasOverlay ? 'pb-track--overlay' : `pb-track--${size}`"
    >
      <div
        class="pb-fill"
        :class="[
          `pb-fill--${color}`,
          indeterminate ? 'pb-indeterminate' : null,
        ]"
        :style="indeterminate ? 'width:40%' : `width:100%;transform:scaleX(${props.progress ?? 0})`"
      />
      <!-- Step / ETA overlay inside bar -->
      <div v-if="hasOverlay" class="pb-overlay">
        <span class="pb-overlay-phase">{{ label }}</span>
        <span class="pb-overlay-step">{{ currentStep }} / {{ totalSteps }}</span>
        <span v-if="eta != null && eta > 2 && !indeterminate" class="pb-overlay-eta">残り約{{ Math.ceil(eta) }}秒</span>
      </div>
    </div>
    <div v-if="!hasOverlay" class="pb-meta">
      <span class="pb-label">{{ label }}</span>
      <span v-if="eta != null && !indeterminate" class="pb-eta">{{ formatEta(eta) }} left</span>
    </div>
  </div>
</template>

<style scoped>
.pb-wrapper {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
}

.pb-track {
  width: 100%;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 9999px;
  /* overflow:hidden removed — fill stays within width:100%/scaleX(0-1) bounds.
     overflow:hidden + border-radius triggers GPU stencil mask processing */
}

.pb-track--sm  { height: 4px; }
.pb-track--md  { height: 6px; }
.pb-track--lg  { height: 8px; }
.pb-track--overlay {
  height: 22px;
  position: relative;
  overflow: hidden;
}

.pb-fill {
  height: 100%;
  border-radius: 9999px;
  transform-origin: left center;
  will-change: transform;
  transition: transform 0.25s ease-out;
}

.pb-fill--default {
  --pb-color: #3b82f6;
  background: var(--pb-color);
}

.pb-fill--purple-gradient {
  --pb-color: #9333ea;
  background: linear-gradient(90deg, #9333ea, #60a5fa);
}

.pb-fill--teal {
  --pb-color: #14b8a6;
  background: var(--pb-color);
}

.pb-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}

.pb-label {
  color: #9ca3af;
}

.pb-eta {
  color: #9ca3af;
  font-size: 10px;
  opacity: 0.75;
}

/* Overlay layout */
.pb-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  gap: 8px;
  pointer-events: none;
}

.pb-overlay-phase {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.85);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pb-overlay-step {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.9);
  white-space: nowrap;
}

.pb-overlay-eta {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.7);
  white-space: nowrap;
}

@keyframes pb-indeterminate-blink {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 0.85; }
}

.pb-indeterminate {
  width: 100% !important;
  animation: pb-indeterminate-blink 2s steps(2, start) infinite;
  background: var(--pb-color, #3b82f6);
  will-change: opacity;
}
</style>

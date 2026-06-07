<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  progress:      { type: Number,  default: 0 },
  progressText:  { type: String,  default: null },
  indeterminate: { type: Boolean, default: false },
  eta:           { type: Number,  default: null },
  color:         { type: String,  default: 'default' },
  size:          { type: String,  default: 'sm' },
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
</script>

<template>
  <div class="pb-wrapper">
    <div class="pb-track" :class="`pb-track--${size}`">
      <div
        class="pb-fill"
        :class="[
          `pb-fill--${color}`,
          indeterminate ? 'pb-indeterminate' : null,
        ]"
        :style="indeterminate ? 'width:40%' : `width:100%;transform:scaleX(${props.progress ?? 0})`"
      />
    </div>
    <div class="pb-meta">
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

.pb-fill {
  height: 100%;
  border-radius: 9999px;
  transform-origin: left center;
  will-change: transform;
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

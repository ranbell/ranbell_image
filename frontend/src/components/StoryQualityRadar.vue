<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  eval: { type: Object, required: true },
})

const { t } = useI18n()

const DIM_ORDER = [
  'topic_fit',
  'diversity',
  'expression',
  'action',
  'drawability',
  'identity',
]

const SIZE = 220
const CX = SIZE / 2
const CY = SIZE / 2
const R = 78

const dims = computed(() => {
  const d = props.eval?.dimensions || {}
  return DIM_ORDER.map((key) => ({
    key,
    label: t('storybook.quality.dim.' + key),
    value: Math.max(0, Math.min(1, Number(d[key] ?? 0))),
  }))
})

const overallPct = computed(() =>
  Math.round(Math.max(0, Math.min(1, Number(props.eval?.overall ?? 0))) * 100)
)

function polar(i, n, radius) {
  const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n
  return {
    x: CX + radius * Math.cos(angle),
    y: CY + radius * Math.sin(angle),
  }
}

const gridPolygons = computed(() => {
  const n = dims.value.length || 1
  return [0.25, 0.5, 0.75, 1].map((frac) => {
    const pts = Array.from({ length: n }, (_, i) => {
      const p = polar(i, n, R * frac)
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`
    })
    return pts.join(' ')
  })
})

const axisLines = computed(() => {
  const n = dims.value.length || 1
  return Array.from({ length: n }, (_, i) => {
    const p = polar(i, n, R)
    return { x2: p.x, y2: p.y }
  })
})

const valuePolygon = computed(() => {
  const n = dims.value.length || 1
  return dims.value
    .map((d, i) => {
      const p = polar(i, n, R * d.value)
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`
    })
    .join(' ')
})

const valuePoints = computed(() => {
  const n = dims.value.length || 1
  return dims.value.map((d, i) => polar(i, n, R * d.value))
})

const labels = computed(() => {
  const n = dims.value.length || 1
  return dims.value.map((d, i) => {
    const p = polar(i, n, R + 22)
    return { ...d, x: p.x, y: p.y, pct: Math.round(d.value * 100) }
  })
})
</script>

<template>
  <div class="sb-quality-radar flex flex-col sm:flex-row items-center gap-5">
    <svg :width="SIZE" :height="SIZE" :viewBox="`0 0 ${SIZE} ${SIZE}`"
      class="block shrink-0" role="img"
      :aria-label="t('storybook.quality.chartAria', { pct: overallPct })">
      <polygon
        v-for="(g, gi) in gridPolygons" :key="'g' + gi"
        :points="g"
        fill="none"
        stroke="rgba(255,255,255,0.08)"
        stroke-width="1"
      />
      <line
        v-for="(ln, li) in axisLines" :key="'a' + li"
        :x1="CX" :y1="CY" :x2="ln.x2" :y2="ln.y2"
        stroke="rgba(45,212,191,0.18)"
        stroke-width="1"
      />
      <polygon
        :points="valuePolygon"
        fill="rgba(45,212,191,0.22)"
        stroke="rgba(94,234,212,0.85)"
        stroke-width="1.5"
      />
      <circle
        v-for="(pt, i) in valuePoints" :key="'d' + i"
        :cx="pt.x" :cy="pt.y"
        r="3"
        fill="#5eead4"
      />
      <text
        v-for="lab in labels" :key="lab.key"
        :x="lab.x" :y="lab.y"
        text-anchor="middle"
        dominant-baseline="middle"
        fill="rgba(204,251,241,0.9)"
        font-size="9"
      >{{ lab.label }}</text>
    </svg>

    <div class="flex-1 min-w-0 w-full max-w-xs flex flex-col gap-2">
      <div class="flex items-baseline gap-2">
        <span class="text-2xl font-semibold text-teal-200 tabular-nums leading-none">{{ overallPct }}</span>
        <span class="text-[10px] text-[var(--sb-muted)] uppercase tracking-wider">
          {{ t('storybook.quality.overall') }}
        </span>
        <span v-if="eval.method" class="ml-auto text-[9px] text-[var(--sb-faint)] font-mono">
          {{ eval.method }}
        </span>
      </div>
      <ul class="grid grid-cols-1 gap-1.5 text-[11px]">
        <li v-for="d in dims" :key="d.key"
          class="flex items-center gap-2 text-gray-300">
          <span class="text-teal-300/90 w-20 shrink-0">{{ d.label }}</span>
          <div class="flex-1 h-1.5 rounded-full bg-black/40 overflow-hidden">
            <div class="h-full rounded-full bg-teal-500/80"
              :style="{ width: Math.round(d.value * 100) + '%' }"></div>
          </div>
          <span class="font-mono text-[10px] text-[var(--sb-muted)] w-8 text-right tabular-nums">
            {{ Math.round(d.value * 100) }}
          </span>
        </li>
      </ul>
    </div>
  </div>
</template>

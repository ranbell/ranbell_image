<script setup>
/**
 * Lightweight pose sketch from craft.tags + notebook beat/frame.
 * Stick figures + camera chevron — no image model.
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { buildPoseSketch, figureJoints } from '../../muse/poseSketch.js'

const props = defineProps({
  tags: { type: String, default: '' },
  beat: { type: String, default: '' },
  beatB: { type: String, default: '' },
  frame: { type: String, default: '' },
  duo: { type: Boolean, default: false },
  flash: { type: Boolean, default: false },
})

const { t } = useI18n()

const model = computed(() => buildPoseSketch(props.tags, {
  beat: props.beat,
  beat_b: props.beatB,
  frame: props.frame,
  duo: props.duo,
}))

/** Partner may differ only in beat_b prose when tags are shared. */
const modelB = computed(() => {
  if (!model.value.duo) return null
  return buildPoseSketch(props.tags, {
    beat: props.beatB || props.beat,
    frame: props.frame,
    duo: true,
  })
})

const show = computed(() => !model.value.empty)

const figA = computed(() => figureJoints(model.value, {
  partner: false,
  interact: model.value.interact,
}))
const figB = computed(() => (
  modelB.value
    ? figureJoints(modelB.value, { partner: true, interact: model.value.interact })
    : null
))

/** Camera glyph position in the 240×160 viewBox. */
const camera = computed(() => {
  const pitch = model.value.cameraPitch
  const side = model.value.cameraSide
  const dist = model.value.cameraDistance
  let x = 120
  let y = 148
  if (pitch === 'below') { x = 120; y = 152 }
  if (pitch === 'above') { x = 120; y = 18 }
  if (side === 'side') x = pitch === 'above' || pitch === 'below' ? 200 : 210
  if (side === 'behind') x = 120
  // close framing: camera closer to figure
  if (dist === 'close') y = pitch === 'above' ? 40 : 130
  if (dist === 'upper' && pitch === 'eye') y = 140
  return { x, y, pitch, side, dist }
})

function limbPath(a, b, c) {
  return `M ${a.x} ${a.y} L ${b.x} ${b.y} L ${c.x} ${c.y}`
}

function torsoPath(j) {
  return `M ${j.neck.x} ${j.neck.y} L ${j.hip.x} ${j.hip.y}`
}

const chips = computed(() => model.value.active.slice(0, 8))

const caption = computed(() => {
  const bits = []
  if (model.value.posture) bits.push(t(`muse.poseSketch.posture.${model.value.posture}`, model.value.posture))
  if (model.value.cameraPitch && model.value.cameraPitch !== 'eye') {
    bits.push(t(`muse.poseSketch.camera.${model.value.cameraPitch}`, model.value.cameraPitch))
  }
  if (model.value.gazeTarget === 'looking_at_viewer') {
    bits.push(t('muse.poseSketch.gazeViewer'))
  }
  return bits.join(' · ')
})
</script>

<template>
  <div
    v-if="show"
    class="rounded-lg border border-white/10 bg-black/35 overflow-hidden transition-colors duration-500"
    :class="flash ? 'border-[var(--sb-teal)]/60 bg-teal-950/30' : ''"
  >
    <div class="flex items-center justify-between px-2.5 pt-2 pb-1">
      <h4 class="text-[11px] text-[var(--sb-amber)]">{{ t('muse.poseSketch.title') }}</h4>
      <span class="text-[9px] text-[var(--sb-faint)]">{{ t('muse.poseSketch.hint') }}</span>
    </div>

    <svg
      viewBox="0 0 240 160"
      class="w-full h-auto max-h-[28vh] block"
      role="img"
      :aria-label="caption || t('muse.poseSketch.title')"
    >
      <!-- stage -->
      <ellipse cx="120" cy="138" rx="78" ry="10" fill="rgba(255,255,255,0.04)" />

      <!-- figure A -->
      <g :transform="model.duo ? 'translate(28,8) scale(0.95)' : 'translate(70,4)'">
        <circle
          :cx="figA.head.x" :cy="figA.head.y" r="11"
          fill="none" stroke="var(--sb-teal)" stroke-width="2.2"
        />
        <!-- face cue (hidden from behind) -->
        <g v-if="!figA.behind" stroke="var(--sb-teal)" stroke-width="1.4" fill="none" opacity="0.85">
          <line
            v-if="model.gazeTarget === 'looking_at_viewer' || !model.gazeTarget"
            :x1="figA.head.x - 3" :y1="figA.head.y - 1"
            :x2="figA.head.x - 3" :y2="figA.head.y + 1"
          />
          <line
            v-if="model.gazeTarget === 'looking_at_viewer' || !model.gazeTarget"
            :x1="figA.head.x + 3" :y1="figA.head.y - 1"
            :x2="figA.head.x + 3" :y2="figA.head.y + 1"
          />
          <path
            v-if="model.gazePitch === 'looking_up'"
            :d="`M ${figA.head.x - 4} ${figA.head.y + 5} Q ${figA.head.x} ${figA.head.y + 2} ${figA.head.x + 4} ${figA.head.y + 5}`"
          />
          <path
            v-else-if="model.gazePitch === 'looking_down'"
            :d="`M ${figA.head.x - 4} ${figA.head.y + 4} Q ${figA.head.x} ${figA.head.y + 7} ${figA.head.x + 4} ${figA.head.y + 4}`"
          />
        </g>
        <path :d="torsoPath(figA)" stroke="var(--sb-teal)" stroke-width="2.4" stroke-linecap="round" fill="none" />
        <path
          :d="limbPath(figA.neck, figA.lElbow, figA.lHand)"
          stroke="var(--sb-teal)" stroke-width="2" stroke-linecap="round" fill="none" opacity="0.9"
        />
        <path
          :d="limbPath(figA.neck, figA.rElbow, figA.rHand)"
          stroke="var(--sb-teal)" stroke-width="2" stroke-linecap="round" fill="none" opacity="0.9"
        />
        <path
          :d="limbPath(figA.hip, figA.lKnee, figA.lFoot)"
          stroke="var(--sb-teal)" stroke-width="2.2" stroke-linecap="round" fill="none"
        />
        <path
          :d="limbPath(figA.hip, figA.rKnee, figA.rFoot)"
          stroke="var(--sb-teal)" stroke-width="2.2" stroke-linecap="round" fill="none"
        />
        <text
          v-if="model.duo"
          :x="figA.head.x" :y="8"
          text-anchor="middle" fill="var(--sb-faint)" font-size="9"
        >A</text>
      </g>

      <!-- figure B -->
      <g v-if="figB" transform="translate(118,8) scale(0.95)">
        <circle
          :cx="figB.head.x" :cy="figB.head.y" r="11"
          fill="none" stroke="#fbbf24" stroke-width="2.2"
        />
        <g v-if="!figB.behind" stroke="#fbbf24" stroke-width="1.4" fill="none" opacity="0.85">
          <line :x1="figB.head.x - 3" :y1="figB.head.y - 1" :x2="figB.head.x - 3" :y2="figB.head.y + 1" />
          <line :x1="figB.head.x + 3" :y1="figB.head.y - 1" :x2="figB.head.x + 3" :y2="figB.head.y + 1" />
        </g>
        <path :d="torsoPath(figB)" stroke="#fbbf24" stroke-width="2.4" stroke-linecap="round" fill="none" />
        <path
          :d="limbPath(figB.neck, figB.lElbow, figB.lHand)"
          stroke="#fbbf24" stroke-width="2" stroke-linecap="round" fill="none" opacity="0.9"
        />
        <path
          :d="limbPath(figB.neck, figB.rElbow, figB.rHand)"
          stroke="#fbbf24" stroke-width="2" stroke-linecap="round" fill="none" opacity="0.9"
        />
        <path
          :d="limbPath(figB.hip, figB.lKnee, figB.lFoot)"
          stroke="#fbbf24" stroke-width="2.2" stroke-linecap="round" fill="none"
        />
        <path
          :d="limbPath(figB.hip, figB.rKnee, figB.rFoot)"
          stroke="#fbbf24" stroke-width="2.2" stroke-linecap="round" fill="none"
        />
        <text :x="figB.head.x" y="8" text-anchor="middle" fill="var(--sb-faint)" font-size="9">B</text>
      </g>

      <!-- interaction cue -->
      <line
        v-if="model.duo && (model.interact.includes('hand') || model.interact === 'holding_hands')"
        x1="108" y1="78" x2="132" y2="78"
        stroke="rgba(255,255,255,0.35)" stroke-width="1.5" stroke-dasharray="3 2"
      />

      <!-- camera -->
      <g :transform="`translate(${camera.x}, ${camera.y})`">
        <rect x="-10" y="-7" width="14" height="10" rx="1.5"
              fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="1.4" />
        <circle cx="0" cy="-2" r="2.2" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="1.2" />
        <polygon
          points="6,-5 14,-2 6,1"
          fill="rgba(255,255,255,0.35)"
          :transform="camera.pitch === 'above'
            ? 'rotate(90 6 -2)'
            : camera.pitch === 'below'
              ? 'rotate(-90 6 -2)'
              : camera.side === 'side'
                ? 'rotate(0 6 -2)'
                : 'rotate(-90 6 -2)'"
        />
      </g>
    </svg>

    <p v-if="caption" class="px-2.5 text-[10px] text-[var(--sb-muted)]">{{ caption }}</p>
    <div v-if="chips.length" class="flex flex-wrap gap-1 px-2.5 pb-2 pt-1">
      <span
        v-for="c in chips" :key="c"
        class="rounded border border-white/10 px-1.5 py-0.5 font-mono text-[9px] text-gray-400"
      >{{ c }}</span>
    </div>
  </div>
</template>

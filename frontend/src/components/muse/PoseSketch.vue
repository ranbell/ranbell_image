<script setup>
/**
 * SVG pose sketch from craft.tags + notebook beat/frame.
 * Camera pitch / side / distance reshape the view — no image model.
 * (Fallback when WebGL / VRM load fails.)
 */
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { buildPoseSketch, figureJoints, cameraView } from '../../muse/poseSketch.js'

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

const modelB = computed(() => {
  if (!model.value.duo) return null
  return buildPoseSketch(props.tags, {
    beat: props.beatB || props.beat,
    frame: props.frame,
    duo: true,
  })
})

const show = computed(() => !model.value.empty)
const view = computed(() => cameraView(model.value))

const figA = computed(() => figureJoints(model.value, {
  partner: false,
  interact: model.value.interact,
}))
const figB = computed(() => (
  modelB.value
    ? figureJoints(modelB.value, { partner: true, interact: model.value.interact })
    : null
))

function limbPath(a, b, c) {
  return `M ${a.x} ${a.y} Q ${b.x} ${b.y} ${c.x} ${c.y}`
}

function torsoPath(j) {
  return `M ${j.neck.x} ${j.neck.y} L ${j.hip.x} ${j.hip.y}`
}

const chips = computed(() => model.value.active.slice(0, 8))

const caption = computed(() => {
  const bits = []
  if (model.value.posture) {
    bits.push(t(`muse.poseSketch.posture.${model.value.posture}`))
  }
  const camKeys = []
  if (model.value.cameraPitch && model.value.cameraPitch !== 'eye') {
    camKeys.push(t(`muse.poseSketch.camera.${model.value.cameraPitch}`))
  }
  if (model.value.cameraSide && model.value.cameraSide !== 'front') {
    camKeys.push(t(`muse.poseSketch.camera.${model.value.cameraSide}`))
  }
  if (model.value.cameraDistance && model.value.cameraDistance !== 'full') {
    camKeys.push(t(`muse.poseSketch.camera.${model.value.cameraDistance}`))
  }
  if (camKeys.length) bits.push(camKeys.join('·'))
  if (model.value.gazeTarget === 'looking_at_viewer') {
    bits.push(t('muse.poseSketch.gazeViewer'))
  }
  return bits.join(' · ')
})

const frustumPoints = computed(() => view.value.frustum.join(' '))
</script>

<template>
  <div
    v-if="show"
    class="rounded-xl border border-pink-300/15 bg-gradient-to-b from-rose-950/25 via-black/30 to-teal-950/20 overflow-hidden transition-colors duration-500"
    :class="flash ? 'border-[var(--sb-teal)]/70 shadow-[0_0_0_1px_rgba(45,212,191,0.25)]' : ''"
  >
    <div class="flex items-center justify-between px-2.5 pt-2 pb-0.5">
      <h4 class="text-[11px] text-[var(--sb-amber)]">{{ t('muse.poseSketch.title') }}</h4>
      <span class="text-[9px] text-[var(--sb-faint)]">{{ t('muse.poseSketch.hint') }}</span>
    </div>

    <svg
      viewBox="0 0 240 170"
      class="w-full h-auto max-h-[30vh] block"
      role="img"
      :aria-label="caption || t('muse.poseSketch.title')"
    >
      <defs>
        <linearGradient id="poseStage" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="rgba(251,113,133,0.08)" />
          <stop offset="100%" stop-color="rgba(45,212,191,0.06)" />
        </linearGradient>
        <clipPath id="poseLensClip">
          <rect
            :x="view.clip.x" :y="view.clip.y"
            :width="view.clip.w" :height="view.clip.h"
            rx="10"
          />
        </clipPath>
      </defs>

      <!-- soft stage -->
      <rect x="0" y="0" width="240" height="170" fill="url(#poseStage)" />
      <ellipse cx="120" cy="142" rx="82" ry="11" fill="rgba(255,182,193,0.08)" />

      <!-- lens framing guide -->
      <rect
        :x="view.clip.x" :y="view.clip.y"
        :width="view.clip.w" :height="view.clip.h"
        rx="10"
        fill="none"
        stroke="rgba(255,255,255,0.14)"
        stroke-width="1"
        stroke-dasharray="4 3"
      />

      <!-- camera frustum (shows pitch / side / zoom) -->
      <polygon
        v-if="view.showFrustum"
        :points="frustumPoints"
        fill="rgba(125,211,252,0.10)"
        stroke="rgba(125,211,252,0.35)"
        stroke-width="1"
      />

      <g :clip-path="view.dist !== 'full' ? 'url(#poseLensClip)' : undefined">
        <!-- figures under camera transform -->
        <g :transform="view.figureTransform">
          <g :transform="model.duo ? 'translate(-8 0) scale(0.92)' : 'translate(0 0)'">
            <!-- A -->
            <g>
              <!-- soft body fill hint -->
              <ellipse
                :cx="(figA.neck.x + figA.hip.x) / 2"
                :cy="(figA.neck.y + figA.hip.y) / 2 + 4"
                rx="11" ry="16"
                fill="rgba(45,212,191,0.10)"
              />
              <!-- head -->
              <circle
                :cx="figA.head.x" :cy="figA.head.y" :r="figA.headR"
                fill="rgba(255,228,230,0.92)"
                stroke="#2dd4bf" stroke-width="2"
              />
              <!-- ahoge -->
              <path
                v-if="!figA.behind"
                :d="`M ${figA.head.x - 2} ${figA.head.y - figA.headR + 1} Q ${figA.head.x + 2} ${figA.head.y - figA.headR - 10} ${figA.head.x + 8} ${figA.head.y - figA.headR - 2}`"
                fill="none" stroke="#2dd4bf" stroke-width="1.6" stroke-linecap="round"
              />
              <!-- hair back bump -->
              <path
                :d="`M ${figA.head.x - 14} ${figA.head.y - 2} Q ${figA.head.x - 18} ${figA.head.y - 14} ${figA.head.x - 4} ${figA.head.y - 16}`"
                fill="none" stroke="#2dd4bf" stroke-width="1.5" opacity="0.7"
              />
              <!-- face -->
              <g v-if="!figA.behind">
                <!-- eyes -->
                <template v-if="!figA.profile">
                  <ellipse :cx="figA.head.x - 5" :cy="figA.head.y - 1" rx="2.2" ry="2.8" fill="#1f2937" />
                  <ellipse :cx="figA.head.x + 5" :cy="figA.head.y - 1" rx="2.2" ry="2.8" fill="#1f2937" />
                  <circle :cx="figA.head.x - 4.3" :cy="figA.head.y - 1.8" r="0.7" fill="#fff" />
                  <circle :cx="figA.head.x + 5.7" :cy="figA.head.y - 1.8" r="0.7" fill="#fff" />
                </template>
                <template v-else>
                  <ellipse :cx="figA.head.x + 4" :cy="figA.head.y - 1" rx="2" ry="2.6" fill="#1f2937" />
                  <path
                    :d="`M ${figA.head.x + 8} ${figA.head.y + 1} L ${figA.head.x + 12} ${figA.head.y + 2}`"
                    stroke="#f9a8d4" stroke-width="1.2" stroke-linecap="round"
                  />
                </template>
                <!-- blush -->
                <ellipse :cx="figA.head.x - 9" :cy="figA.head.y + 4" rx="3" ry="1.6" fill="rgba(251,113,133,0.45)" />
                <ellipse :cx="figA.head.x + 9" :cy="figA.head.y + 4" rx="3" ry="1.6" fill="rgba(251,113,133,0.45)" />
                <!-- mouth -->
                <path
                  v-if="model.gazePitch === 'looking_up'"
                  :d="`M ${figA.head.x - 3} ${figA.head.y + 7} Q ${figA.head.x} ${figA.head.y + 5} ${figA.head.x + 3} ${figA.head.y + 7}`"
                  fill="none" stroke="#fb7185" stroke-width="1.3" stroke-linecap="round"
                />
                <path
                  v-else
                  :d="`M ${figA.head.x - 3} ${figA.head.y + 6} Q ${figA.head.x} ${figA.head.y + 9} ${figA.head.x + 3} ${figA.head.y + 6}`"
                  fill="none" stroke="#fb7185" stroke-width="1.3" stroke-linecap="round"
                />
              </g>
              <!-- limbs -->
              <path :d="torsoPath(figA)" stroke="#2dd4bf" stroke-width="3.2" stroke-linecap="round" fill="none" />
              <path :d="limbPath(figA.neck, figA.lElbow, figA.lHand)" stroke="#5eead4" stroke-width="2.6" stroke-linecap="round" fill="none" />
              <path :d="limbPath(figA.neck, figA.rElbow, figA.rHand)" stroke="#5eead4" stroke-width="2.6" stroke-linecap="round" fill="none" />
              <path :d="limbPath(figA.hip, figA.lKnee, figA.lFoot)" stroke="#2dd4bf" stroke-width="2.8" stroke-linecap="round" fill="none" />
              <path :d="limbPath(figA.hip, figA.rKnee, figA.rFoot)" stroke="#2dd4bf" stroke-width="2.8" stroke-linecap="round" fill="none" />
              <!-- tiny hands/feet dots -->
              <circle :cx="figA.lHand.x" :cy="figA.lHand.y" r="2.4" fill="#99f6e4" />
              <circle :cx="figA.rHand.x" :cy="figA.rHand.y" r="2.4" fill="#99f6e4" />
              <ellipse :cx="figA.lFoot.x" :cy="figA.lFoot.y" rx="4" ry="2.2" fill="#2dd4bf" opacity="0.7" />
              <ellipse :cx="figA.rFoot.x" :cy="figA.rFoot.y" rx="4" ry="2.2" fill="#2dd4bf" opacity="0.7" />
              <text
                v-if="model.duo"
                :x="figA.head.x" :y="figA.head.y - figA.headR - 6"
                text-anchor="middle" fill="rgba(255,255,255,0.45)" font-size="9"
              >A</text>
            </g>
          </g>

          <!-- B -->
          <g v-if="figB" transform="translate(54 0) scale(0.92)">
            <ellipse
              :cx="(figB.neck.x + figB.hip.x) / 2"
              :cy="(figB.neck.y + figB.hip.y) / 2 + 4"
              rx="11" ry="16"
              fill="rgba(251,191,36,0.10)"
            />
            <circle
              :cx="figB.head.x" :cy="figB.head.y" :r="figB.headR"
              fill="rgba(255,237,213,0.95)"
              stroke="#fbbf24" stroke-width="2"
            />
            <path
              v-if="!figB.behind"
              :d="`M ${figB.head.x - 2} ${figB.head.y - figB.headR + 1} Q ${figB.head.x + 2} ${figB.head.y - figB.headR - 10} ${figB.head.x + 8} ${figB.head.y - figB.headR - 2}`"
              fill="none" stroke="#fbbf24" stroke-width="1.6" stroke-linecap="round"
            />
            <g v-if="!figB.behind">
              <ellipse :cx="figB.head.x - 5" :cy="figB.head.y - 1" rx="2.2" ry="2.8" fill="#1f2937" />
              <ellipse :cx="figB.head.x + 5" :cy="figB.head.y - 1" rx="2.2" ry="2.8" fill="#1f2937" />
              <ellipse :cx="figB.head.x - 9" :cy="figB.head.y + 4" rx="3" ry="1.6" fill="rgba(251,113,133,0.45)" />
              <ellipse :cx="figB.head.x + 9" :cy="figB.head.y + 4" rx="3" ry="1.6" fill="rgba(251,113,133,0.45)" />
              <path
                :d="`M ${figB.head.x - 3} ${figB.head.y + 6} Q ${figB.head.x} ${figB.head.y + 9} ${figB.head.x + 3} ${figB.head.y + 6}`"
                fill="none" stroke="#fb7185" stroke-width="1.3" stroke-linecap="round"
              />
            </g>
            <path :d="torsoPath(figB)" stroke="#fbbf24" stroke-width="3.2" stroke-linecap="round" fill="none" />
            <path :d="limbPath(figB.neck, figB.lElbow, figB.lHand)" stroke="#fcd34d" stroke-width="2.6" stroke-linecap="round" fill="none" />
            <path :d="limbPath(figB.neck, figB.rElbow, figB.rHand)" stroke="#fcd34d" stroke-width="2.6" stroke-linecap="round" fill="none" />
            <path :d="limbPath(figB.hip, figB.lKnee, figB.lFoot)" stroke="#fbbf24" stroke-width="2.8" stroke-linecap="round" fill="none" />
            <path :d="limbPath(figB.hip, figB.rKnee, figB.rFoot)" stroke="#fbbf24" stroke-width="2.8" stroke-linecap="round" fill="none" />
            <circle :cx="figB.lHand.x" :cy="figB.lHand.y" r="2.4" fill="#fde68a" />
            <circle :cx="figB.rHand.x" :cy="figB.rHand.y" r="2.4" fill="#fde68a" />
            <text
              :x="figB.head.x" :y="figB.head.y - figB.headR - 6"
              text-anchor="middle" fill="rgba(255,255,255,0.45)" font-size="9"
            >B</text>
          </g>

          <line
            v-if="model.duo && (model.interact.includes('hand') || model.interact === 'holding_hands')"
            x1="48" y1="72" x2="72" y2="72"
            stroke="rgba(255,182,193,0.55)" stroke-width="2" stroke-linecap="round"
          />
        </g>
      </g>

      <!-- camera body -->
      <g :transform="`translate(${view.camera.x} ${view.camera.y})`">
        <rect x="-11" y="-8" width="16" height="12" rx="3"
              fill="rgba(15,23,42,0.75)" stroke="rgba(186,230,253,0.8)" stroke-width="1.4" />
        <circle cx="-1" cy="-2" r="3.2" fill="rgba(56,189,248,0.25)" stroke="rgba(186,230,253,0.9)" stroke-width="1.2" />
        <circle cx="-1" cy="-2" r="1.2" fill="rgba(125,211,252,0.9)" />
        <!-- cute shutter blink mark -->
        <rect x="6" y="-6" width="4" height="3" rx="0.8" fill="rgba(251,113,133,0.7)" />
      </g>

      <!-- distance / pitch badge -->
      <g v-if="view.pitch !== 'eye' || view.dist !== 'full' || view.side !== 'front'">
        <rect x="8" y="8" width="54" height="16" rx="8" fill="rgba(0,0,0,0.35)" />
        <text x="35" y="19" text-anchor="middle" fill="#bae6fd" font-size="9">
          {{ [
            view.pitch !== 'eye' ? t(`muse.poseSketch.camera.${view.pitch}`) : '',
            view.side !== 'front' ? t(`muse.poseSketch.camera.${view.side}`) : '',
            view.dist !== 'full' ? t(`muse.poseSketch.camera.${view.dist}`) : '',
          ].filter(Boolean).join(' ') }}
        </text>
      </g>
    </svg>

    <p v-if="caption" class="px-2.5 text-[10px] text-[var(--sb-muted)]">{{ caption }}</p>
    <div v-if="chips.length" class="flex flex-wrap gap-1 px-2.5 pb-2 pt-1">
      <span
        v-for="c in chips" :key="c"
        class="rounded-full border border-pink-300/20 bg-white/5 px-2 py-0.5 font-mono text-[9px] text-gray-300"
      >{{ c }}</span>
    </div>
  </div>
</template>

<script setup>
/**
 * VRM pose preview + optional pose-coaching mode (human directs → LLM).
 * Falls back to SVG PoseSketch if WebGL or VRM load fails.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { buildPoseSketch } from '../../muse/poseSketch.js'
import { buildPoseCoachMessage, poseModelToTags } from '../../muse/poseCoach.js'
import PoseSketch from './PoseSketch.vue'

const props = defineProps({
  tags: { type: String, default: '' },
  beat: { type: String, default: '' },
  beatB: { type: String, default: '' },
  frame: { type: String, default: '' },
  duo: { type: Boolean, default: false },
  flash: { type: Boolean, default: false },
})

const emit = defineEmits(['coach'])

const { t } = useI18n()
const host = ref(null)
const use3d = ref(true)
const loading = ref(false)
const err = ref('')
const coachOn = ref(false)
const coachSubject = ref('a')
const coachPreview = ref('')
const viewMode = ref('overview') // overview | shot
const coachState = ref({
  posture: 'standing',
  arms: 'arms_at_sides',
  cameraSide: 'front',
  cameraPitch: 'eye',
  cameraDistance: 'full',
  interact: '',
})
const duoGap = ref(0.55)
let stage = null
let dead = false

const model = computed(() => buildPoseSketch(props.tags, {
  beat: props.beat,
  beat_b: props.beatB,
  frame: props.frame,
  duo: props.duo,
}))

const show = computed(() => !model.value.empty || coachOn.value)

const caption = computed(() => {
  const bits = []
  if (model.value.posture) bits.push(t(`muse.poseSketch.posture.${model.value.posture}`))
  const cam = []
  if (model.value.cameraPitch && model.value.cameraPitch !== 'eye') {
    cam.push(t(`muse.poseSketch.camera.${model.value.cameraPitch}`))
  }
  if (model.value.cameraSide && model.value.cameraSide !== 'front') {
    cam.push(t(`muse.poseSketch.camera.${model.value.cameraSide}`))
  }
  if (model.value.cameraDistance && model.value.cameraDistance !== 'full') {
    cam.push(t(`muse.poseSketch.camera.${model.value.cameraDistance}`))
  }
  if (cam.length) bits.push(cam.join('·'))
  if (model.value.gazeTarget === 'looking_at_viewer') bits.push(t('muse.poseSketch.gazeViewer'))
  return bits.join(' · ')
})

const chips = computed(() => model.value.active.slice(0, 8))

const POSTURES = ['standing', 'squatting', 'sitting', 'kneeling', 'lying']
const ARMS = ['arms_at_sides', 'arms_up', 'crossed_arms', 'spread_arms']
const SIDES = ['front', 'side', 'behind']
const PITCHES = ['eye', 'below', 'above']
const DISTS = ['full', 'upper', 'close']

function pushPose() {
  if (!stage || coachOn.value) return
  stage.update({
    tags: props.tags,
    beat: props.beat,
    beatB: props.beatB,
    frame: props.frame,
    duo: props.duo,
  })
}

function refreshCoachPreview() {
  if (!stage || !coachOn.value) {
    coachPreview.value = ''
    return
  }
  const snap = stage.getCoachSnapshot()
  if (snap?.model) {
    coachState.value = {
      posture: snap.model.posture || 'standing',
      arms: snap.model.arms || 'arms_at_sides',
      cameraSide: snap.model.cameraSide || 'front',
      cameraPitch: snap.model.cameraPitch || 'eye',
      cameraDistance: snap.model.cameraDistance || 'full',
      interact: snap.model.interact || '',
    }
  }
  if (Number.isFinite(snap?.duoSpacing)) duoGap.value = snap.duoSpacing
  coachPreview.value = buildPoseCoachMessage(snap.model, {
    duo: snap.duo || props.duo,
    subject: props.duo ? (snap.subject === 'b' ? 'b' : snap.duo ? 'both' : 'a') : 'a',
    customLimbs: snap.customLimbs,
    duoSpacing: snap.duoSpacing,
  })
}

function chipClass(active) {
  return active
    ? 'border-amber-400/70 bg-amber-400/15 text-amber-100'
    : 'border-white/10 bg-white/5 text-gray-300 hover:border-white/30'
}

function patchCoach(partial) {
  if (!stage || !coachOn.value) return
  stage.patchCoachModel(partial)
  refreshCoachPreview()
}

function toggleCoach() {
  if (!stage) return
  coachOn.value = !coachOn.value
  stage.setCoachMode(coachOn.value)
  if (coachOn.value) {
    stage.setCoachSubject(coachSubject.value)
    refreshCoachPreview()
  } else {
    coachPreview.value = ''
    pushPose()
  }
  viewMode.value = stage.getViewMode?.() || viewMode.value
}

function setViewMode(mode) {
  if (!stage?.setViewMode) return
  viewMode.value = stage.setViewMode(mode)
}

function setSubject(who) {
  coachSubject.value = who
  if (stage && coachOn.value) {
    stage.setCoachSubject(who)
    refreshCoachPreview()
  }
}

function nudgeGap(dir) {
  if (!stage?.nudgeDuoSpacing || !props.duo) return
  duoGap.value = stage.nudgeDuoSpacing(dir < 0 ? -0.08 : 0.08)
  refreshCoachPreview()
}

function applyInteract(name) {
  if (!stage?.applyInteraction || !coachOn.value) return
  stage.applyInteraction(name)
  refreshCoachPreview()
}

function resetPlacement() {
  if (!stage?.resetPlacement || !coachOn.value) return
  stage.resetPlacement()
  refreshCoachPreview()
}

function snapLap() {
  if (!stage?.snapHeadToLap || !coachOn.value) return
  stage.snapHeadToLap()
  refreshCoachPreview()
}

function sendCoach() {
  if (!stage || !coachOn.value) return
  refreshCoachPreview()
  const snap = stage.getCoachSnapshot()
  const message = buildPoseCoachMessage(snap.model, {
    duo: snap.duo || props.duo,
    subject: props.duo ? coachSubject.value : 'a',
    customLimbs: snap.customLimbs,
    duoSpacing: snap.duoSpacing,
  })
  const tags = poseModelToTags(snap.model, { duo: snap.duo || props.duo })
  emit('coach', { message, tags, model: snap.model })
}

async function mountStage() {
  if (dead || !host.value || !use3d.value) return
  loading.value = true
  err.value = ''
  const wasCoach = coachOn.value
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl')
    if (!gl) throw new Error('no webgl')
    const { createAvatarStage } = await import('../../muse/avatar3d.js')
    if (stage) {
      stage.dispose()
      stage = null
    }
    if (!host.value || dead) return
    stage = await createAvatarStage(host.value, { duo: props.duo })
    pushPose()
    if (wasCoach) {
      stage.setCoachMode(true)
      stage.setCoachSubject(coachSubject.value)
      refreshCoachPreview()
    }
  } catch (e) {
    err.value = String(e?.message || e)
    use3d.value = false
    coachOn.value = false
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (show.value) mountStage()
})

watch(
  () => [props.tags, props.beat, props.beatB, props.frame],
  () => pushPose(),
)

watch(
  () => props.duo,
  () => { if (use3d.value) mountStage() },
)

watch(show, (v) => {
  if (v && use3d.value && !stage) mountStage()
})

onBeforeUnmount(() => {
  dead = true
  if (stage) stage.dispose()
  stage = null
})
</script>

<template>
  <PoseSketch
    v-if="show && !use3d"
    :tags="tags"
    :beat="beat"
    :beat-b="beatB"
    :frame="frame"
    :duo="duo"
    :flash="flash"
  />

  <div
    v-else-if="show"
    class="rounded-xl border border-white/10 bg-[#1a1a1e] overflow-hidden transition-colors duration-500"
    :class="flash ? 'border-[var(--sb-teal)]/70' : ''"
  >
    <div class="flex items-center justify-between gap-2 px-2.5 pt-2 pb-0.5">
      <h4 class="text-[11px] text-[var(--sb-amber)]">{{ t('muse.poseSketch.title3d') }}</h4>
      <div class="flex items-center gap-1">
        <div class="flex rounded-md border border-white/15 overflow-hidden">
          <button
            type="button"
            class="px-2 py-0.5 text-[10px]"
            :class="viewMode === 'overview' ? 'bg-white/15 text-amber-200' : 'text-[var(--sb-faint)]'"
            @click="setViewMode('overview')"
          >{{ t('muse.poseSketch.viewOverview') }}</button>
          <button
            type="button"
            class="px-2 py-0.5 text-[10px] border-l border-white/15"
            :class="viewMode === 'shot' ? 'bg-sky-500/25 text-sky-200' : 'text-[var(--sb-faint)]'"
            @click="setViewMode('shot')"
          >{{ t('muse.poseSketch.viewShot') }}</button>
        </div>
        <button
          type="button"
          class="rounded-md border px-2 py-0.5 text-[10px] transition-colors"
          :class="coachOn
            ? 'border-[var(--sb-teal)]/60 bg-[var(--sb-teal)]/15 text-[var(--sb-teal)]'
            : 'border-white/15 text-[var(--sb-faint)] hover:text-white'"
          @click="toggleCoach"
        >{{ coachOn ? t('muse.poseSketch.coachOn') : t('muse.poseSketch.coach') }}</button>
      </div>
    </div>

    <p
      v-if="viewMode === 'shot'"
      class="px-2.5 pt-1 text-[9px] text-sky-200/80"
    >{{ t('muse.poseSketch.viewShotHint') }}</p>

    <div
      ref="host"
      class="w-full px-1 relative"
      :class="coachOn || viewMode === 'shot' ? 'min-h-[280px]' : 'min-h-[220px]'"
    >
      <p
        v-if="loading"
        class="absolute inset-0 flex items-center justify-center text-[11px] text-[var(--sb-faint)]"
      >{{ t('muse.poseSketch.loading') }}</p>
    </div>

    <div v-if="coachOn" class="space-y-2 px-2.5 pb-2.5 pt-1.5 border-t border-white/5">
      <p class="text-[9px] leading-relaxed text-[var(--sb-muted)]">{{ t('muse.poseSketch.coachHint') }}</p>

      <div v-if="duo" class="flex flex-wrap gap-1.5 items-center">
        <button
          v-for="who in ['a', 'b']" :key="who" type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="coachSubject === who ? 'border-amber-400/70 text-amber-200 bg-amber-400/10' : 'border-white/10 text-gray-400'"
          @click="setSubject(who)"
        >{{ who === 'a' ? t('muse.poseSketch.subjectA') : t('muse.poseSketch.subjectB') }}</button>
        <span class="text-[9px] text-[var(--sb-faint)] mx-0.5">|</span>
        <button
          type="button"
          class="rounded-lg border border-white/15 px-2.5 py-1 text-[10px] text-gray-300 hover:border-white/40"
          @click="nudgeGap(-1)"
        >{{ t('muse.poseSketch.closer') }}</button>
        <button
          type="button"
          class="rounded-lg border border-white/15 px-2.5 py-1 text-[10px] text-gray-300 hover:border-white/40"
          @click="nudgeGap(1)"
        >{{ t('muse.poseSketch.farther') }}</button>
        <button
          type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="chipClass(coachState.interact === 'lap_pillow')"
          @click="snapLap"
        >{{ t('muse.poseSketch.lapPillow') }}</button>
        <button
          type="button"
          class="rounded-lg border border-white/15 px-2.5 py-1 text-[10px] text-gray-300 hover:border-white/40"
          @click="resetPlacement"
        >{{ t('muse.poseSketch.resetPlace') }}</button>
        <span class="text-[9px] text-[var(--sb-faint)]">{{ Math.round(duoGap * 100) }}</span>
      </div>

      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="p in POSTURES" :key="p" type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="chipClass(coachState.posture === p)"
          @click="patchCoach({ posture: p })"
        >{{ t(`muse.poseSketch.posture.${p}`) }}</button>
      </div>
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="a in ARMS" :key="a" type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="chipClass(coachState.arms === a)"
          @click="patchCoach({ arms: a })"
        >{{ t(`muse.poseSketch.arms.${a}`) }}</button>
      </div>
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="s in SIDES" :key="'s'+s" type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="chipClass(coachState.cameraSide === s)"
          @click="patchCoach({ cameraSide: s })"
        >{{ t(`muse.poseSketch.camera.${s}`) }}</button>
        <button
          v-for="p in PITCHES" :key="'p'+p" type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="chipClass(coachState.cameraPitch === p)"
          @click="patchCoach({ cameraPitch: p })"
        >{{ t(`muse.poseSketch.camera.${p}`) }}</button>
        <button
          v-for="d in DISTS" :key="'d'+d" type="button"
          class="rounded-lg border px-2.5 py-1 text-[10px]"
          :class="chipClass(coachState.cameraDistance === d)"
          @click="patchCoach({ cameraDistance: d })"
        >{{ t(`muse.poseSketch.camera.${d}`) }}</button>
      </div>

      <pre
        v-if="coachPreview"
        class="max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-2.5 text-[9px] leading-relaxed text-sky-100/90"
      >{{ coachPreview }}</pre>

      <button
        type="button"
        class="w-full rounded-lg border border-[var(--sb-teal)]/50 bg-[var(--sb-teal)]/20 py-2 text-[12px] font-medium text-[var(--sb-teal)] hover:bg-[var(--sb-teal)]/30 active:scale-[0.99]"
        @click="sendCoach"
      >{{ t('muse.poseSketch.coachSend') }}</button>
    </div>

    <template v-else>
      <p v-if="caption" class="px-2.5 text-[10px] text-[var(--sb-muted)]">{{ caption }}</p>
      <div v-if="chips.length" class="flex flex-wrap gap-1 px-2.5 pb-2 pt-1">
        <span
          v-for="c in chips" :key="c"
          class="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[9px] text-gray-300"
        >{{ c }}</span>
      </div>
    </template>
    <p v-if="err" class="px-2.5 pb-2 text-[9px] text-[var(--sb-faint)]">{{ err }}</p>
  </div>
</template>

<script setup>
/**
 * VRM avatar pose preview (normal proportions) driven by craft tags / notebook.
 * Falls back to SVG PoseSketch if WebGL or VRM load fails.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { buildPoseSketch } from '../../muse/poseSketch.js'
import PoseSketch from './PoseSketch.vue'

const props = defineProps({
  tags: { type: String, default: '' },
  beat: { type: String, default: '' },
  beatB: { type: String, default: '' },
  frame: { type: String, default: '' },
  duo: { type: Boolean, default: false },
  flash: { type: Boolean, default: false },
})

const { t } = useI18n()
const host = ref(null)
const use3d = ref(true)
const loading = ref(false)
const err = ref('')
let stage = null
let dead = false

const model = computed(() => buildPoseSketch(props.tags, {
  beat: props.beat,
  beat_b: props.beatB,
  frame: props.frame,
  duo: props.duo,
}))

const show = computed(() => !model.value.empty)

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

function pushPose() {
  if (!stage) return
  stage.update({
    tags: props.tags,
    beat: props.beat,
    beatB: props.beatB,
    frame: props.frame,
    duo: props.duo,
  })
}

async function mountStage() {
  if (dead || !host.value || !use3d.value) return
  loading.value = true
  err.value = ''
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
  } catch (e) {
    err.value = String(e?.message || e)
    use3d.value = false
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
    <div class="flex items-center justify-between px-2.5 pt-2 pb-0.5">
      <h4 class="text-[11px] text-[var(--sb-amber)]">{{ t('muse.poseSketch.title3d') }}</h4>
      <span class="text-[9px] text-[var(--sb-faint)]">{{ t('muse.poseSketch.hint3d') }}</span>
    </div>
    <div ref="host" class="w-full min-h-[220px] px-1 relative">
      <p
        v-if="loading"
        class="absolute inset-0 flex items-center justify-center text-[11px] text-[var(--sb-faint)]"
      >{{ t('muse.poseSketch.loading') }}</p>
    </div>
    <p v-if="caption" class="px-2.5 text-[10px] text-[var(--sb-muted)]">{{ caption }}</p>
    <div v-if="chips.length" class="flex flex-wrap gap-1 px-2.5 pb-2 pt-1">
      <span
        v-for="c in chips" :key="c"
        class="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[9px] text-gray-300"
      >{{ c }}</span>
    </div>
    <p v-if="err" class="px-2.5 pb-2 text-[9px] text-[var(--sb-faint)]">{{ err }}</p>
  </div>
</template>

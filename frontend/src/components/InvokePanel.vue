<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useInvokeSession,
  SPIRIT_NAMES,
  SPIRIT_META,
  EMOJI_PALETTE,
  getSpiritFrame,
} from '../composables/useInvokeSession.js'
import { getToken } from '../apiToken.js'

const props = defineProps({ show: Boolean })
const emit  = defineEmits(['update:show', 'send-to-refine', 'toast', 'select-image'])

const { locale, t } = useI18n()

const {
  invokeOpen, invokeInputMode, invokeLoading, isLoading,
  invokeSessionId, invokeSpirits, invokeAxes,
  invokeDailyOracle, invokeOracleNextRun, invokeStats,
  invokeEmojis, invokeText, invokeColors, invokeMoodSliders,
  invokePersonGender, invokePersonCount, invokePromptMode,
  invokeCameraShot, invokeCameraAngle,
  invokeProTopic, invokeProPersonTags, invokeProPrompt, invokeProNegative, invokeWorkflow, invokeSeeds,
  invokeEnabledSpirits, enabledSpiritList,
  openInvoke, closeInvoke,
  summon, cancel, respin, adopt, sendToRefine,
  fetchDaily, fetchStats, enhancePrompt,
  toggleEmoji, toggleSpirit,
} = useInvokeSession()

// ── Workflows ─────────────────────────────────────────────────────────────────
const workflows = ref([])
async function _loadWorkflows() {
  if (workflows.value.length > 0) return
  try {
    const r = await fetch(`/api/comfy/workflows?token=${encodeURIComponent(getToken())}`)
    if (r.ok) workflows.value = await r.json()
  } catch {}
}

// ── Backdrop drag-close guard ─────────────────────────────────────────────────
// Prevents accidental close when user drags (e.g. selects text in a textarea)
// and the mouse momentarily lands on the backdrop before releasing.
const overlayMousedownOnBg = ref(false)

// ── Oracle dismiss (session-scoped) ──────────────────────────────────────────
const oracleDismissed = ref(false)
watch(invokeOpen, (val) => { if (val) oracleDismissed.value = false })

// ── Color picker ──────────────────────────────────────────────────────────────
const colorPickerVal = ref('#a855f7')
function addColor() {
  const hex = colorPickerVal.value
  if (invokeColors.value.length < 6 && !invokeColors.value.includes(hex)) {
    invokeColors.value.push(hex)
  }
}
function removeColor(hex) {
  const idx = invokeColors.value.indexOf(hex)
  if (idx !== -1) invokeColors.value.splice(idx, 1)
}

// ── Prompt enhancer ───────────────────────────────────────────────────────────
const enhancing = ref(false)
const enhancedNL = ref('')

async function handleEnhancePrompt() {
  if (enhancing.value) return
  enhancing.value = true
  enhancedNL.value = ''
  try {
    const data = await enhancePrompt(getToken())
    if (data?.natural_language) enhancedNL.value = data.natural_language
  } catch (err) {
    emit('toast', { msg: t('invoke.errors.enhance', { msg: err.message }), type: 'error' })
  } finally {
    enhancing.value = false
  }
}

// ── Axis display config ───────────────────────────────────────────────────────
const AXIS_DISPLAY = computed(() => [
  { key: 'subject',     label: t('invoke.axis.subject') },
  { key: 'action',      label: t('invoke.axis.action') },
  { key: 'scene',       label: t('invoke.axis.scene') },
  { key: 'mood',        label: t('invoke.axis.mood') },
  { key: 'lighting',    label: t('invoke.axis.lighting') },
  { key: 'composition', label: t('invoke.axis.composition') },
  { key: 'style',       label: t('invoke.axis.style') },
  { key: 'palette',     label: t('invoke.axis.palette') },
])

// ── Spirit narrative selection ─────────────────────────────────────────────────
const selectedNarrativeSpirit = ref(null)

const spiritsWithNarrative = computed(() =>
  SPIRIT_NAMES.filter(n => invokeEnabledSpirits.value[n] && invokeSpirits.value[n]?.natural_language)
)

// Auto-switch to most recently composed spirit
SPIRIT_NAMES.forEach(name => {
  watch(() => invokeSpirits.value[name]?.natural_language, (text) => {
    if (text) selectedNarrativeSpirit.value = name
  })
})

// Reset on new summon
watch(isLoading, (loading) => {
  if (loading) selectedNarrativeSpirit.value = null
})

// ── Camera work options ───────────────────────────────────────────────────────
const CAMERA_SHOTS = computed(() => [
  { val: 'wide_shot',        label: t('invoke.camera.wide') },
  { val: 'full_body',        label: t('invoke.camera.fullBody') },
  { val: 'cowboy_shot',      label: t('invoke.camera.cowboy') },
  { val: 'upper_body',       label: t('invoke.camera.upperBody') },
  { val: 'bust',             label: t('invoke.camera.bust') },
  { val: 'close_up',         label: t('invoke.camera.closeUp') },
  { val: 'extreme_close_up', label: t('invoke.camera.extreme') },
])

const CAMERA_ANGLES = computed(() => [
  { val: 'from_above',   label: t('invoke.camera.above') },
  { val: 'from_below',   label: t('invoke.camera.below') },
  { val: 'from_side',    label: t('invoke.camera.side') },
  { val: 'from_behind',  label: t('invoke.camera.behind') },
  { val: 'dutch_angle',  label: t('invoke.camera.dutch') },
  { val: 'aerial_view',  label: t('invoke.camera.aerial') },
  { val: 'worm_eye_view', label: t('invoke.camera.wormEye') },
])

// ── Mood slider labels ────────────────────────────────────────────────────────
const SLIDER_AXES = computed(() => [
  { key: 'warm_cool',         low: t('invoke.slider.warmLow'),     high: t('invoke.slider.warmHigh') },
  { key: 'calm_dynamic',      low: t('invoke.slider.calmLow'),     high: t('invoke.slider.calmHigh') },
  { key: 'dense_sparse',      low: t('invoke.slider.denseLow'),    high: t('invoke.slider.denseHigh') },
  { key: 'concrete_abstract', low: t('invoke.slider.concreteLow'), high: t('invoke.slider.concreteHigh') },
])

// ── Summon / Cancel ───────────────────────────────────────────────────────────
async function handleSummon() {
  if (isLoading.value) return
  try {
    await summon(getToken(), locale.value)
  } catch (err) {
    emit('toast', { msg: t('invoke.errors.summon', { msg: err.message }), type: 'error' })
  }
}

async function handleCancel() {
  try {
    await cancel(getToken())
  } catch (err) {
    emit('toast', { msg: t('invoke.errors.cancel', { msg: err.message }), type: 'error' })
  }
}

// ── Respin ────────────────────────────────────────────────────────────────────
async function handleRespin(spiritName) {
  try {
    await respin(spiritName, getToken())
  } catch (err) {
    emit('toast', { msg: t('invoke.errors.respin', { msg: err.message }), type: 'error' })
  }
}

// ── Adopt ─────────────────────────────────────────────────────────────────────
async function handleAdopt(spiritName) {
  try {
    const sha256 = await adopt(spiritName, getToken())
    if (sha256) emit('toast', { msg: t('invoke.adoptSuccess', { kanji: locale.value === 'ja' ? SPIRIT_META[spiritName].kanji : SPIRIT_META[spiritName].en }), type: 'success' })
  } catch (err) {
    emit('toast', { msg: t('invoke.errors.adopt', { msg: err.message }), type: 'error' })
  }
}

// ── Send to Refine ────────────────────────────────────────────────────────────
async function handleSendToRefine(spiritName) {
  try {
    const data = await sendToRefine(spiritName, getToken())
    if (data) emit('send-to-refine', data)
  } catch (err) {
    emit('toast', { msg: t('invoke.errors.refine', { msg: err.message }), type: 'error' })
  }
}

// ── Emoji palette categories ──────────────────────────────────────────────────
const _EMOJI_DATA = [
  { catKey: 'lightFlame', emojis: [
    { em: '✨', mk: 'sparkles' },
    { em: '⚡', mk: 'lightning' },
    { em: '🔥', mk: 'fire' },
    { em: '💫', mk: 'dizzy' },
    { em: '🕯️', mk: 'candle' },
  ]},
  { catKey: 'weatherSky', emojis: [
    { em: '🌙', mk: 'moon' },
    { em: '🌑', mk: 'newMoon' },
    { em: '🌈', mk: 'rainbow' },
    { em: '❄️', mk: 'snowflake' },
    { em: '🌫️', mk: 'fog' },
    { em: '🌌', mk: 'galaxy' },
  ]},
  { catKey: 'waterPlants', emojis: [
    { em: '🌊', mk: 'wave' },
    { em: '💧', mk: 'droplet' },
    { em: '🌿', mk: 'herb' },
    { em: '🍃', mk: 'leaf' },
    { em: '🌾', mk: 'sheaf' },
    { em: '🎋', mk: 'bamboo' },
  ]},
  { catKey: 'flowerCreatures', emojis: [
    { em: '🌸', mk: 'cherry' },
    { em: '🌺', mk: 'hibiscus' },
    { em: '🪷', mk: 'lotus' },
    { em: '🦋', mk: 'butterfly' },
  ]},
  { catKey: 'terrainPlaces', emojis: [
    { em: '🌃', mk: 'nightCity' },
    { em: '🌅', mk: 'sunrise' },
    { em: '🌇', mk: 'sunset' },
    { em: '🏔️', mk: 'mountain' },
    { em: '🗻', mk: 'fuji' },
    { em: '🌋', mk: 'volcano' },
    { em: '🪐', mk: 'planet' },
  ]},
  { catKey: 'mysticTools', emojis: [
    { em: '🔮', mk: 'crystalBall' },
    { em: '🪞', mk: 'mirror' },
    { em: '🗝️', mk: 'key' },
    { em: '🕸️', mk: 'spider' },
    { em: '🐚', mk: 'shell' },
    { em: '🫧', mk: 'bubbles' },
    { em: '🎐', mk: 'windChime' },
  ]},
  { catKey: 'emotionPeople', emojis: [
    { em: '😌', mk: 'peaceful' },
    { em: '🩰', mk: 'ballet' },
    { em: '🎭', mk: 'mask' },
    { em: '🌠', mk: 'shootingStar' },
  ]},
]

const EMOJI_CATEGORIES = computed(() =>
  _EMOJI_DATA.map(cat => ({
    label: t(`invoke.emojiCat.${cat.catKey}`),
    emojis: cat.emojis.map(e => ({ em: e.em, meaning: t(`invoke.emojiMeaning.${e.mk}`) })),
  }))
)

// ── Spirit-specific monologue typing animation ─────────────────────────────────
// Each spirit card tracks its own animation state

const ZALGO_ABOVE = ['̍','̎','̄','̅','̿','̑','̆','̐','͒','͗']
const ZALGO_BELOW = ['̖','̗','̘','̙','̜','̝','̞','̟','̠','͙']

function _addZalgo(char) {
  const a = ZALGO_ABOVE[Math.floor(Math.random() * ZALGO_ABOVE.length)]
  const b = ZALGO_BELOW[Math.floor(Math.random() * ZALGO_BELOW.length)]
  return char + a + b
}

// spiritDisplayText: { spiritName: ref(string) }
const spiritDisplayText = Object.fromEntries(SPIRIT_NAMES.map(n => [n, ref('')]))
const spiritShake       = Object.fromEntries(SPIRIT_NAMES.map(n => [n, ref(false)]))
const spiritGlitchIdx   = Object.fromEntries(SPIRIT_NAMES.map(n => [n, ref(-1)]))
const spiritFadeIn      = Object.fromEntries(SPIRIT_NAMES.map(n => [n, ref(false)]))

const _animTimers = {}
const _animGen = {}

function _clearAnim(name) {
  if (_animTimers[name]) {
    clearTimeout(_animTimers[name])
    delete _animTimers[name]
  }
  _animGen[name] = (_animGen[name] || 0) + 1
}

function _animateMonologue(spiritName, text) {
  _clearAnim(spiritName)
  const gen = _animGen[spiritName]
  spiritDisplayText[spiritName].value = ''
  spiritFadeIn[spiritName].value = false
  spiritShake[spiritName].value = false
  spiritGlitchIdx[spiritName].value = -1

  if (!text) return
  const chars = [...text]

  if (spiritName === 'oracle') {
    // Fade-in: set full text, then trigger opacity transition
    spiritDisplayText[spiritName].value = text
    nextTick(() => { spiritFadeIn[spiritName].value = true })
    return
  }

  let i = 0

  function nextChar() {
    if (_animGen[spiritName] !== gen) return
    if (i >= chars.length) return
    const c = chars[i]

    if (spiritName === 'faithful') {
      // Steady 45ms
      spiritDisplayText[spiritName].value += c
      i++
      _animTimers[spiritName] = setTimeout(nextChar, 45)

    } else if (spiritName === 'rebel') {
      // Jitter: random speed + shake on random chars
      const delay = 20 + Math.random() * 80
      spiritDisplayText[spiritName].value += c
      i++
      if (Math.random() < 0.15) {
        spiritShake[spiritName].value = true
        setTimeout(() => {
          if (_animGen[spiritName] === gen) spiritShake[spiritName].value = false
        }, 80)
      }
      _animTimers[spiritName] = setTimeout(nextChar, delay)

    } else if (spiritName === 'stranger') {
      // Normal speed, occasional 1-char glitch
      spiritDisplayText[spiritName].value += c
      i++
      const glitchPos = spiritDisplayText[spiritName].value.length - 1
      if (Math.random() < 0.06) {
        const saved = c
        // Replace last char with space briefly
        setTimeout(() => {
          if (_animGen[spiritName] !== gen) return
          const cur = spiritDisplayText[spiritName].value
          spiritDisplayText[spiritName].value = cur.slice(0, glitchPos) + ' ' + cur.slice(glitchPos + 1)
        }, 60)
        setTimeout(() => {
          if (_animGen[spiritName] !== gen) return
          const cur = spiritDisplayText[spiritName].value
          spiritDisplayText[spiritName].value = cur.slice(0, glitchPos) + saved + cur.slice(glitchPos + 1)
        }, 150)
      }
      _animTimers[spiritName] = setTimeout(nextChar, 45)

    } else if (spiritName === 'lunatic') {
      // Chaotic speed + Zalgo injection
      const delay = 10 + Math.random() * 80
      if (Math.random() < 0.12) {
        // Inject zalgo then fix
        const zc = _addZalgo(c)
        spiritDisplayText[spiritName].value += zc
        setTimeout(() => {
          if (_animGen[spiritName] !== gen) return
          const cur = spiritDisplayText[spiritName].value
          // Rebuild: find and replace zalgo variant with plain char
          spiritDisplayText[spiritName].value = cur.replace(zc, c)
        }, 200)
      } else {
        spiritDisplayText[spiritName].value += c
      }
      i++
      _animTimers[spiritName] = setTimeout(nextChar, delay)
    }
  }

  _animTimers[spiritName] = setTimeout(nextChar, 30)
}

// Watch for monologue changes and trigger animation
SPIRIT_NAMES.forEach(name => {
  watch(() => invokeSpirits.value[name]?.monologue, (text) => {
    if (text) _animateMonologue(name, text)
    else {
      _clearAnim(name)
      spiritDisplayText[name].value = ''
      spiritFadeIn[name].value = false
    }
  })
})

// ── Synchronized pulse directive ──────────────────────────────────────────────
// Each element sets its animation-delay to -(phase into the 2s cycle), so all
// elements appear to share the same global animation timeline regardless of
// when they were mounted.
const vPulseSync = {
  mounted(el) {
    el.style.animationDelay = `-${(performance.now() % 2000) / 1000}s`
  },
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────
watch(() => props.show, async (val) => {
  if (val) {
    invokeOpen.value = true
    await _loadWorkflows()
    await Promise.allSettled([fetchDaily(), fetchStats()])
  } else {
    invokeOpen.value = false
  }
})

// ── Oracle countdown ──────────────────────────────────────────────────────────
const oracleCountdown = ref('')
let _countdownTimer = null

function _updateCountdown() {
  if (!invokeOracleNextRun.value) { oracleCountdown.value = ''; return }
  const diff = Math.max(0, Math.floor((new Date(invokeOracleNextRun.value) - Date.now()) / 1000))
  const h = Math.floor(diff / 3600)
  const m = Math.floor((diff % 3600) / 60)
  const s = diff % 60
  oracleCountdown.value = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

watch(invokeOracleNextRun, (val) => {
  clearInterval(_countdownTimer)
  if (val) {
    _updateCountdown()
    _countdownTimer = setInterval(_updateCountdown, 1000)
  } else {
    oracleCountdown.value = ''
  }
}, { immediate: true })

onUnmounted(() => {
  SPIRIT_NAMES.forEach(n => _clearAnim(n))
  clearInterval(_countdownTimer)
})

// ── Helpers ───────────────────────────────────────────────────────────────────
function spiritBorderClass(name) {
  const frame = getSpiritFrame(name, invokeSpirits.value[name]?.alignment_score)
  if (frame === 'gold')    return 'border-yellow-400 shadow-yellow-400/30 shadow-lg'
  if (frame === 'obsidian') return 'border-gray-900 shadow-gray-900/60 shadow-lg ring-1 ring-gray-800'
  return SPIRIT_META[name].border
}

function spiritStatusLabel(status) {
  const key = `invoke.status.${status}`
  const label = t(key)
  return label !== key ? label : status
}

const PROMPT_MODES = computed(() => [
  { val: 'danbooru+natural', label: t('invoke.promptModeDB'),     title: t('invoke.promptModeDBTitle') },
  { val: 'natural',          label: t('invoke.promptModeNL'),     title: t('invoke.promptModeNLTitle') },
  { val: 'danbooru',         label: t('invoke.promptModeDBOnly'), title: t('invoke.promptModeDBOnlyTitle') },
])

function alignmentPercent(score) {
  if (score === null || score === undefined) return null
  return Math.round(score * 100)
}

const hasAnyResult = computed(() =>
  SPIRIT_NAMES.some(n => invokeSpirits.value[n]?.status !== 'waiting')
)

const canSummon = computed(() => {
  const enabled = SPIRIT_NAMES.filter(n => invokeEnabledSpirits.value[n])
  return enabled.length > 0 && !isLoading.value && !!invokeWorkflow.value
})

const summonBlockReason = computed(() => {
  const enabled = SPIRIT_NAMES.filter(n => invokeEnabledSpirits.value[n])
  if (enabled.length === 0) return t('invoke.blockNoSpirit')
  if (!invokeWorkflow.value) return t('invoke.blockNoWorkflow')
  return null
})

function thumbnailUrl(sha256) {
  return `/api/thumbnails/${sha256}.webp`
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show"
      class="fixed inset-0 z-[56] bg-black/92 flex items-center justify-center p-3"
      @mousedown.self="overlayMousedownOnBg = true"
      @mouseup.self="if (overlayMousedownOnBg) emit('update:show', false); overlayMousedownOnBg = false"
      @mouseleave="overlayMousedownOnBg = false">

      <div class="bg-gray-900 rounded-2xl w-full max-w-[1440px] shadow-2xl border border-gray-800/80 max-h-[95vh] flex flex-col"
        @mousedown="overlayMousedownOnBg = false"
        @mouseup="overlayMousedownOnBg = false"
        style="box-shadow: 0 0 60px rgba(168,85,247,0.12), 0 25px 60px rgba(0,0,0,0.7);">

        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-800/70 flex-shrink-0">
          <div class="flex items-center gap-3">
            <span class="text-xl">召</span>
            <h2 class="font-semibold text-gray-100">Invoke <span class="text-gray-500 font-normal text-sm ml-1">{{ t('invoke.subtitle') }}</span></h2>
            <span v-if="isLoading"
              class="flex items-center gap-1.5 text-xs text-purple-300 bg-purple-900/40 border border-purple-700/40 px-2 py-0.5 rounded-full">
              <span class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse"></span>{{ t('invoke.summoning') }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span v-if="invokeStats" class="text-[10px] text-gray-600 bg-gray-800/60 px-2 py-0.5 rounded-full">
              {{ t('invoke.summonCount', { n: invokeStats.summoned_total ?? 0 }) }}
            </span>
            <button @click="emit('update:show', false)"
              class="text-gray-500 hover:text-gray-200 text-xl leading-none w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
          </div>
        </div>

        <!-- Body -->
        <div class="flex flex-1 min-h-0 divide-x divide-gray-800/60">

          <!-- ── Left pane: Input (Zone B) ── -->
          <div class="w-[340px] flex-shrink-0 flex flex-col overflow-y-auto">
            <div class="p-5 space-y-5">

              <!-- Mode toggle -->
              <div class="flex items-center gap-1 bg-gray-800/70 rounded-xl p-1">
                <button @click="invokeInputMode = 'light'"
                  :class="invokeInputMode === 'light'
                    ? 'bg-gray-700 text-gray-100 shadow-sm'
                    : 'text-gray-500 hover:text-gray-300'"
                  class="flex-1 py-1.5 rounded-lg text-xs font-medium transition-all">
                  {{ t('invoke.modeLight') }}
                </button>
                <button @click="invokeInputMode = 'pro'"
                  :class="invokeInputMode === 'pro'
                    ? 'bg-gray-700 text-gray-100 shadow-sm'
                    : 'text-gray-500 hover:text-gray-300'"
                  class="flex-1 py-1.5 rounded-lg text-xs font-medium transition-all">
                  {{ t('invoke.modePro') }}
                </button>
              </div>

              <!-- ── Light mode inputs ── -->
              <template v-if="invokeInputMode === 'light'">

                <!-- Emoji palette (categorized) -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.emojiSection') }}</p>
                  <div class="space-y-2.5">
                    <div v-for="cat in EMOJI_CATEGORIES" :key="cat.label">
                      <p class="text-[9px] text-gray-700 uppercase tracking-widest mb-1 select-none">{{ cat.label }}</p>
                      <div class="flex flex-wrap gap-1">
                        <button v-for="item in cat.emojis" :key="item.em"
                          @click="toggleEmoji(item.em)"
                          :title="item.meaning"
                          :class="invokeEmojis.includes(item.em)
                            ? 'bg-purple-700/60 border-purple-500/60 ring-1 ring-purple-400/40'
                            : 'bg-gray-800/60 border-gray-700/40 hover:bg-gray-700/60'"
                          class="w-8 h-8 flex items-center justify-center rounded-lg border text-base transition-all leading-none select-none">
                          {{ item.em }}
                        </button>
                      </div>
                    </div>
                  </div>
                  <p v-if="invokeEmojis.length > 0" class="text-[10px] text-purple-400 mt-2">
                    {{ t('invoke.emojiSelected', { emojis: invokeEmojis.join(' ') }) }}
                    <button @click="invokeEmojis.length = 0" class="ml-1 text-gray-600 hover:text-gray-300">{{ t('invoke.emojiClear') }}</button>
                  </p>
                </div>

                <!-- Text input -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.imageLabel') }}</p>
                  <input v-model="invokeText"
                    :placeholder="t('invoke.imagePlaceholder')"
                    maxlength="140"
                    class="w-full bg-gray-800/60 border border-gray-700/50 rounded-xl px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-purple-500/60 focus:ring-1 focus:ring-purple-500/30 transition" />
                  <p class="text-[10px] text-gray-600 text-right mt-0.5">{{ invokeText.length }}/140</p>
                </div>

                <!-- Person spec -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.personLabel') }}</p>
                  <div class="flex items-center gap-2">
                    <div class="flex gap-1">
                      <button v-for="g in ['girl', 'boy']" :key="g"
                        @click="invokePersonGender = invokePersonGender === g ? '' : g"
                        :class="invokePersonGender === g
                          ? 'bg-purple-700/60 border-purple-500/60 text-purple-200'
                          : 'bg-gray-800/60 border-gray-700/40 text-gray-500 hover:text-gray-300'"
                        class="px-2.5 py-1 rounded-lg border text-[11px] transition-all">
                        {{ g }}
                      </button>
                    </div>
                    <div class="w-px h-5 bg-gray-700/60 flex-shrink-0"></div>
                    <div class="flex gap-1">
                      <button v-for="n in ['1', '2', '3+']" :key="n"
                        @click="invokePersonCount = invokePersonCount === n ? '' : n"
                        :class="invokePersonCount === n
                          ? 'bg-purple-700/60 border-purple-500/60 text-purple-200'
                          : 'bg-gray-800/60 border-gray-700/40 text-gray-500 hover:text-gray-300'"
                        class="px-2.5 py-1 rounded-lg border text-[11px] transition-all">
                        {{ n }}
                      </button>
                    </div>
                  </div>
                  <p class="text-[9px] mt-1"
                    :class="(invokePersonGender || invokePersonCount) ? 'text-purple-400' : 'text-gray-700'">
                    <template v-if="invokePersonGender || invokePersonCount">
                      {{ t('invoke.personInclude', { spec: [invokePersonCount, invokePersonGender].filter(Boolean).join(' ') }) }}
                    </template>
                    <template v-else>{{ t('invoke.personUnset') }}</template>
                  </p>
                </div>

                <!-- Color picker -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.colorLabel') }}</p>
                  <div class="flex items-center gap-2">
                    <input type="color" v-model="colorPickerVal"
                      class="h-8 w-8 rounded-lg border-0 bg-transparent cursor-pointer flex-shrink-0" />
                    <button @click="addColor"
                      :disabled="invokeColors.length >= 6"
                      class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700/50 rounded-lg text-[10px] text-gray-400 hover:text-gray-200 disabled:opacity-40 transition">{{ t('invoke.colorAdd') }}</button>
                  </div>
                  <div v-if="invokeColors.length > 0" class="flex flex-wrap gap-1.5 mt-2">
                    <button v-for="hex in invokeColors" :key="hex"
                      @click="removeColor(hex)"
                      class="w-6 h-6 rounded-md border border-gray-700/60 hover:opacity-60 transition relative group"
                      :style="`background-color: ${hex}`"
                      :title="t('invoke.colorRemoveHint', { hex })">
                    </button>
                  </div>
                </div>

                <!-- Mood sliders -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.sliderLabel') }}</p>
                  <div class="space-y-3">
                    <div v-for="axis in SLIDER_AXES" :key="axis.key" class="flex items-center gap-2">
                      <span class="text-xs text-gray-500 w-4 text-right flex-shrink-0">{{ axis.low }}</span>
                      <input type="range"
                        v-model.number="invokeMoodSliders[axis.key]"
                        min="-2" max="2" step="1"
                        class="flex-1 h-1.5 rounded-full accent-purple-500 cursor-pointer" />
                      <span class="text-xs text-gray-500 w-4 flex-shrink-0">{{ axis.high }}</span>
                      <span class="text-[10px] text-gray-600 w-3 text-right flex-shrink-0">{{ invokeMoodSliders[axis.key] > 0 ? '+' : '' }}{{ invokeMoodSliders[axis.key] }}</span>
                    </div>
                  </div>
                </div>

                <!-- Camera Work -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.cameraLabel') }}</p>
                  <!-- Shot size -->
                  <p class="text-[9px] text-gray-600 mb-1">{{ t('invoke.cameraShotLabel') }}</p>
                  <div class="flex flex-wrap gap-1 mb-2.5">
                    <button v-for="opt in CAMERA_SHOTS" :key="opt.val"
                      @click="invokeCameraShot = invokeCameraShot === opt.val ? '' : opt.val"
                      :class="invokeCameraShot === opt.val
                        ? 'bg-indigo-700/60 border-indigo-500/60 text-indigo-200'
                        : 'bg-gray-800/60 border-gray-700/40 text-gray-400 hover:text-gray-200 hover:border-gray-600/60'"
                      class="px-2 py-1 rounded-lg border text-[9px] transition">
                      {{ opt.label }}
                    </button>
                  </div>
                  <!-- Camera angle -->
                  <p class="text-[9px] text-gray-600 mb-1">{{ t('invoke.cameraAngleLabel') }}</p>
                  <div class="flex flex-wrap gap-1">
                    <button v-for="opt in CAMERA_ANGLES" :key="opt.val"
                      @click="invokeCameraAngle = invokeCameraAngle === opt.val ? '' : opt.val"
                      :class="invokeCameraAngle === opt.val
                        ? 'bg-indigo-700/60 border-indigo-500/60 text-indigo-200'
                        : 'bg-gray-800/60 border-gray-700/40 text-gray-400 hover:text-gray-200 hover:border-gray-600/60'"
                      class="px-2 py-1 rounded-lg border text-[9px] transition">
                      {{ opt.label }}
                    </button>
                  </div>
                </div>

              </template>

              <!-- ── Pro mode inputs ── -->
              <template v-else>

                <!-- キャラクタータグ -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.proCharTagsLabel') }} <span class="text-gray-700 normal-case font-normal">{{ t('invoke.proCharTagsOptional') }}</span></p>
                  <textarea v-model="invokeProPersonTags"
                    placeholder="1girl, red_hair, blue_eyes, sailor_uniform..."
                    rows="2"
                    class="w-full bg-gray-800/60 border border-gray-700/50 rounded-xl px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-sky-500/60 focus:ring-1 focus:ring-sky-500/30 transition resize-none" />
                  <p class="text-[9px] text-gray-700 mt-0.5">{{ t('invoke.proCharTagsHint') }}</p>
                </div>

                <!-- お題タグ変換 -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.proTopicLabel') }}</p>
                  <textarea v-model="invokeProTopic"
                    :placeholder="t('invoke.proTopicPlaceholder')"
                    rows="3"
                    class="w-full bg-gray-800/60 border border-gray-700/50 rounded-xl px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-purple-500/60 focus:ring-1 focus:ring-purple-500/30 transition resize-none" />
                  <button @click="handleEnhancePrompt"
                    :disabled="enhancing || !invokeProTopic.trim()"
                    class="mt-1.5 w-full py-2 rounded-xl text-xs font-medium transition-all
                           bg-gradient-to-r from-indigo-800 to-purple-800
                           hover:from-indigo-700 hover:to-purple-700
                           text-indigo-200 disabled:opacity-40 disabled:cursor-not-allowed">
                    <span v-if="enhancing" class="flex items-center justify-center gap-2">
                      <span class="w-1.5 h-1.5 rounded-full bg-indigo-300 animate-pulse"></span>{{ t('invoke.proEnhancing') }}
                    </span>
                    <span v-else>{{ t('invoke.proEnhanceBtn') }}</span>
                  </button>
                  <p v-if="enhancedNL" class="mt-1.5 text-[9px] text-gray-500 italic leading-relaxed">
                    {{ enhancedNL }}
                  </p>
                </div>

                <!-- Positive prompt -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.proPromptLabel') }}</p>
                  <textarea v-model="invokeProPrompt"
                    :placeholder="t('invoke.proPromptPlaceholder')"
                    rows="5"
                    class="w-full bg-gray-800/60 border border-gray-700/50 rounded-xl px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-purple-500/60 focus:ring-1 focus:ring-purple-500/30 transition resize-none" />
                </div>

                <!-- Negative prompt -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.proNegativeLabel') }}</p>
                  <textarea v-model="invokeProNegative"
                    :placeholder="t('invoke.proNegativePlaceholder')"
                    rows="2"
                    class="w-full bg-gray-800/60 border border-gray-700/50 rounded-xl px-3 py-2 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-purple-500/60 focus:ring-1 focus:ring-purple-500/30 transition resize-none" />
                </div>

                <!-- Per-spirit seeds (Pro only) -->
                <div>
                  <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.proSeedLabel') }}</p>
                  <div class="space-y-1.5">
                    <div v-for="name in SPIRIT_NAMES" :key="name" class="flex items-center gap-2">
                      <span :class="SPIRIT_META[name].color" class="text-sm text-center flex-shrink-0">{{ locale === 'ja' ? SPIRIT_META[name].kanji : SPIRIT_META[name].en }}</span>
                      <input type="number"
                        :placeholder="`${SPIRIT_META[name].en}`"
                        v-model.number="invokeSeeds[name]"
                        class="flex-1 bg-gray-800/50 border border-gray-700/40 rounded-lg px-2 py-1 text-[10px] text-gray-300 placeholder-gray-700 focus:outline-none focus:border-purple-500/40 transition" />
                    </div>
                  </div>
                </div>

              </template>

              <!-- ── Prompt mode (common) ── -->
              <div>
                <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.promptModeLabel') }}</p>
                <div class="flex gap-1">
                  <button v-for="mode in PROMPT_MODES" :key="mode.val"
                    @click="invokePromptMode = mode.val"
                    :title="mode.title"
                    :class="invokePromptMode === mode.val
                      ? 'bg-purple-700/60 border-purple-500/60 text-purple-200'
                      : 'bg-gray-800/60 border-gray-700/40 text-gray-500 hover:text-gray-300'"
                    class="flex-1 py-1 rounded-lg border text-[11px] transition-all">
                    {{ mode.label }}
                  </button>
                </div>
              </div>

              <!-- ── Workflow selector (common — required for both modes) ── -->
              <div>
                <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1">
                  {{ t('invoke.workflowLabel') }}
                  <span class="text-red-400">*</span>
                  <span v-if="!invokeWorkflow" class="text-red-400 text-[9px] normal-case font-normal ml-1">{{ t('invoke.workflowRequired') }}</span>
                </p>
                <select v-model="invokeWorkflow"
                  :class="!invokeWorkflow ? 'border-red-700/50 focus:border-red-500/60' : 'border-gray-700/50 focus:border-purple-500/60'"
                  class="w-full bg-gray-800/60 border rounded-xl px-3 py-2 text-xs text-gray-200 focus:outline-none transition">
                  <option value="">{{ t('invoke.workflowSelect') }}</option>
                  <option v-for="wf in workflows" :key="wf" :value="wf">{{ wf }}</option>
                </select>
                <p v-if="workflows.length === 0" class="text-[10px] text-gray-600 mt-1">{{ t('invoke.workflowNoComfy') }}</p>
              </div>

              <!-- ── Spirit toggles (common) ── -->
              <div>
                <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ t('invoke.spiritSelectLabel') }}</p>
                <div class="flex gap-1.5 flex-wrap">
                  <button v-for="name in SPIRIT_NAMES" :key="name"
                    @click="toggleSpirit(name)"
                    :class="[
                      invokeEnabledSpirits[name]
                        ? `${SPIRIT_META[name].bg} ${SPIRIT_META[name].border} ${SPIRIT_META[name].color} border`
                        : 'bg-gray-800/30 border-gray-700/30 text-gray-600 border',
                      'px-3 py-1.5 rounded-xl text-sm font-medium transition-all select-none'
                    ]">
                    {{ locale === 'ja' ? SPIRIT_META[name].kanji : SPIRIT_META[name].en }}
                    <span v-if="locale === 'ja'" class="text-[9px] ml-0.5 opacity-60">{{ SPIRIT_META[name].en }}</span>
                  </button>
                </div>
                <p v-if="enabledSpiritList.length === 0" class="text-[10px] text-red-400 mt-1.5">{{ t('invoke.spiritSelectError') }}</p>
              </div>

              <!-- ── Summon / Cancel ── -->
              <div class="space-y-1.5">
                <button @click="handleSummon"
                  :disabled="!canSummon"
                  :title="summonBlockReason || ''"
                  class="w-full py-3 rounded-xl font-semibold text-sm transition-all duration-200
                         bg-gradient-to-r from-purple-700 via-violet-600 to-purple-700
                         hover:from-purple-600 hover:via-violet-500 hover:to-purple-600
                         text-white shadow-lg shadow-purple-900/40
                         disabled:opacity-40 disabled:cursor-not-allowed
                         active:scale-[0.98]">
                  <span v-if="isLoading" class="flex items-center justify-center gap-2">
                    <span class="w-2 h-2 rounded-full bg-white/60 animate-pulse"></span>
                    {{ t('invoke.summoning') }}
                  </span>
                  <span v-else>{{ t('invoke.summonBtn') }}</span>
                </button>
                <button v-if="isLoading" @click="handleCancel"
                  class="w-full py-2 rounded-xl text-xs font-medium border border-red-800/50
                         bg-red-950/30 text-red-400 hover:bg-red-900/30 hover:text-red-300
                         transition-colors">
                  {{ t('invoke.cancelBtn') }}
                </button>
                <p v-if="summonBlockReason && !isLoading" class="text-[10px] text-amber-500 text-center">
                  {{ summonBlockReason }}
                </p>
              </div>

            </div>
          </div>

          <!-- ── Right pane: Oracle + Results ── -->
          <div class="flex-1 min-w-0 flex flex-col overflow-y-auto">

            <!-- Zone A: Daily Oracle (today's 5 images + countdown) -->
            <div v-if="invokeDailyOracle?.enabled && !oracleDismissed" class="border-b border-gray-800/60 p-4">
              <div class="flex items-center justify-between mb-3">
                <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">
                  {{ t('invoke.oracleTitle') }} — {{ invokeDailyOracle.date }}
                </p>
                <div class="flex items-center gap-3">
                  <p v-if="oracleCountdown" class="text-[10px] font-mono text-gray-600">
                    {{ t('invoke.oracleNextRun') }} {{ oracleCountdown }}
                  </p>
                  <button @click="oracleDismissed = true"
                    class="text-gray-700 hover:text-gray-400 transition-colors text-xs leading-none" title="閉じる">✕</button>
                </div>
              </div>
              <div v-if="invokeDailyOracle.images" class="flex gap-2 overflow-x-auto pb-1">
                <template v-for="name in SPIRIT_NAMES" :key="name">
                  <div v-if="invokeDailyOracle.images[name]" class="flex-shrink-0 w-24 cursor-pointer"
                    @click="emit('select-image', invokeDailyOracle.images[name].sha256)">
                    <div class="relative rounded-xl overflow-hidden border border-gray-700/40 aspect-square hover:border-gray-500 transition-colors">
                      <img :src="thumbnailUrl(invokeDailyOracle.images[name].sha256)"
                        class="w-full h-full object-cover" loading="lazy" />
                      <div class="absolute bottom-0 inset-x-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 py-1">
                        <span :class="SPIRIT_META[name].color" class="text-xs font-bold">{{ locale === 'ja' ? SPIRIT_META[name].kanji : SPIRIT_META[name].en }}</span>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
              <p v-else class="text-[10px] text-gray-700">{{ t('invoke.oracleNotYet') }}</p>
            </div>

            <!-- Zone C: Spirit result cards -->
            <div class="flex-1 p-4">
              <!-- Empty state (before any summon) -->
              <div v-if="!hasAnyResult && !isLoading" class="flex items-center justify-center h-full min-h-48">
                <div class="text-center space-y-3">
                  <div class="text-5xl opacity-20 select-none">召</div>
                  <p class="text-sm text-gray-600">{{ t('invoke.emptyHint') }}</p>
                  <p class="text-[10px] text-gray-700">{{ t('invoke.emptySubhint') }}</p>
                </div>
              </div>

              <!-- Bento spirit cards (horizontal, appears immediately on summon) -->
              <div v-else class="pb-2">
                <div class="grid gap-3" :style="`grid-template-columns: repeat(${enabledSpiritList.length}, minmax(0, 1fr))`">
                  <template v-for="name in SPIRIT_NAMES" :key="name">
                    <div v-if="invokeEnabledSpirits[name]"
                      @click="selectedNarrativeSpirit = name"
                      class="min-w-0 rounded-2xl border-2 flex flex-col overflow-hidden transition-colors duration-200 cursor-pointer"
                      :class="[
                        spiritBorderClass(name),
                        SPIRIT_META[name].bg,
                        selectedNarrativeSpirit === name ? 'ring-2 ring-white/15' : '',
                      ]">

                      <!-- Spirit header -->
                      <div class="flex items-center gap-2 px-3 pt-3 pb-2">
                        <span :class="SPIRIT_META[name].color" class="text-2xl font-bold leading-none select-none">
                          {{ locale === 'ja' ? SPIRIT_META[name].kanji : SPIRIT_META[name].en }}
                        </span>
                        <div class="flex-1 min-w-0">
                          <p v-if="locale === 'ja'" class="text-[10px] text-gray-400 font-medium truncate">{{ SPIRIT_META[name].en }}</p>
                          <p class="text-[9px] text-gray-600">
                            {{ spiritStatusLabel(invokeSpirits[name]?.status) }}
                          </p>
                        </div>
                        <!-- Alignment badge -->
                        <div v-if="invokeSpirits[name]?.alignment_score !== null && invokeSpirits[name]?.alignment_score !== undefined"
                          :class="[
                            getSpiritFrame(name, invokeSpirits[name].alignment_score) === 'gold'
                              ? 'bg-yellow-900/60 border-yellow-500/60 text-yellow-300'
                              : getSpiritFrame(name, invokeSpirits[name].alignment_score) === 'obsidian'
                                ? 'bg-gray-950/80 border-gray-800/80 text-gray-400'
                                : 'bg-gray-800/60 border-gray-700/40 text-gray-500',
                            'text-[9px] px-1.5 py-0.5 rounded-full border font-mono flex-shrink-0'
                          ]">
                          {{ alignmentPercent(invokeSpirits[name].alignment_score) }}%
                        </div>
                      </div>

                      <!-- Thumbnail -->
                      <div class="mx-2 rounded-xl overflow-hidden bg-gray-900/60 h-40 relative flex-shrink-0">
                        <img v-if="invokeSpirits[name]?.sha256"
                          :src="thumbnailUrl(invokeSpirits[name].sha256)"
                          class="w-full h-full object-cover"
                          loading="lazy" />
                        <!-- Loading skeleton -->
                        <div v-else-if="['composing','generating','tagging'].includes(invokeSpirits[name]?.status)"
                          v-pulse-sync
                          class="w-full h-full pulse-sync flex items-center justify-center">
                          <span :class="SPIRIT_META[name].color" class="text-4xl opacity-20 select-none">{{ locale === 'ja' ? SPIRIT_META[name].kanji : SPIRIT_META[name].en }}</span>
                        </div>
                        <!-- Waiting placeholder -->
                        <div v-else class="w-full h-full flex items-center justify-center">
                          <span :class="SPIRIT_META[name].color" class="text-5xl opacity-10 select-none">{{ locale === 'ja' ? SPIRIT_META[name].kanji : SPIRIT_META[name].en }}</span>
                        </div>
                        <!-- Frame overlay -->
                        <div v-if="getSpiritFrame(name, invokeSpirits[name]?.alignment_score) === 'gold'"
                          class="absolute inset-0 rounded-xl ring-2 ring-yellow-400/60 pointer-events-none"></div>
                        <div v-if="getSpiritFrame(name, invokeSpirits[name]?.alignment_score) === 'obsidian'"
                          class="absolute inset-0 rounded-xl ring-2 ring-gray-950/80 pointer-events-none"></div>
                      </div>

                      <!-- Monologue -->
                      <div class="px-2.5 py-2 min-h-[2.5rem]">
                        <!-- Oracle: fade in -->
                        <p v-if="name === 'oracle'"
                          :class="['text-[10px] text-gray-400 leading-relaxed italic transition-opacity duration-700 line-clamp-2',
                            spiritFadeIn[name] ? 'opacity-100' : 'opacity-0']">
                          {{ spiritDisplayText[name] }}
                        </p>
                        <!-- Rebel: shaking text -->
                        <p v-else-if="name === 'rebel'"
                          :class="['text-[10px] text-gray-400 leading-relaxed italic line-clamp-2',
                            spiritShake[name] ? 'translate-x-0.5' : '']"
                          style="transition: transform 0.05s">
                          {{ spiritDisplayText[name] }}<span v-if="invokeSpirits[name]?.status === 'composing'" class="animate-pulse">▍</span>
                        </p>
                        <!-- Others -->
                        <p v-else class="text-[10px] text-gray-400 leading-relaxed italic line-clamp-2">
                          {{ spiritDisplayText[name] }}<span v-if="['composing','waiting'].includes(invokeSpirits[name]?.status) && spiritDisplayText[name]" class="animate-pulse">▍</span>
                        </p>
                      </div>

                      <!-- Action buttons -->
                      <div v-if="invokeSpirits[name]?.status === 'done'" @click.stop class="flex gap-1 px-2.5 pb-2.5 mt-auto">
                        <button @click="handleRespin(name)"
                          :disabled="isLoading"
                          class="flex-1 py-1.5 rounded-lg border border-gray-700/50 bg-gray-800/60 hover:bg-gray-700/60 text-[9px] text-gray-400 hover:text-gray-200 disabled:opacity-40 transition">
                          {{ t('invoke.btnRespin') }}
                        </button>
                        <button @click="handleAdopt(name)"
                          class="flex-1 py-1.5 rounded-lg border border-yellow-600/40 bg-yellow-900/30 hover:bg-yellow-800/40 text-[9px] text-yellow-300 hover:text-yellow-200 transition">
                          {{ t('invoke.btnAdopt') }}
                        </button>
                        <button @click="handleSendToRefine(name)"
                          class="flex-1 py-1.5 rounded-lg border border-purple-700/40 bg-purple-900/30 hover:bg-purple-800/40 text-[9px] text-purple-300 hover:text-purple-200 transition">
                          {{ t('invoke.btnRefine') }}
                        </button>
                      </div>
                      <!-- Progress bar while in-flight -->
                      <div v-else-if="['composing','generating','tagging'].includes(invokeSpirits[name]?.status)" class="px-2.5 pb-2.5 mt-auto">
                        <div class="h-1 w-full rounded-full bg-gray-800/80 overflow-hidden">
                          <div :class="SPIRIT_META[name].border.replace('border-','bg-')"
                            v-pulse-sync
                            class="h-full rounded-full pulse-sync w-2/3"></div>
                        </div>
                      </div>
                      <!-- Error -->
                      <div v-else-if="invokeSpirits[name]?.status === 'error'" class="px-2.5 pb-2.5 mt-auto">
                        <button @click="handleRespin(name)"
                          class="w-full py-1.5 rounded-lg border border-red-700/40 bg-red-950/40 text-[9px] text-red-400 hover:text-red-300 transition">
                          {{ t('invoke.btnRetry') }}
                        </button>
                      </div>

                    </div>
                  </template>
                </div>
              </div>

              <!-- ── Session context panel: slogan + axes + narratives ── -->
              <div v-if="hasAnyResult || isLoading" class="mt-4">

                <!-- Skeleton while axis decompose is running -->
                <div v-if="isLoading && !invokeAxes" v-pulse-sync class="space-y-2 pulse-sync">
                  <div class="h-12 bg-gray-800/30 rounded-xl"></div>
                  <div class="grid grid-cols-2 gap-1.5">
                    <div v-for="i in 8" :key="i" class="h-10 bg-gray-800/30 rounded-xl"></div>
                  </div>
                </div>

                <!-- Full context panel (after axis_done) -->
                <div v-else-if="invokeAxes" class="space-y-3">

                  <!-- ① Slogan -->
                  <div v-if="invokeAxes._slogan"
                    class="bg-purple-950/30 border border-purple-800/30 rounded-xl px-4 py-3">
                    <p class="text-[9px] font-semibold text-purple-500 uppercase tracking-wider mb-1">{{ t('invoke.sloganLabel') }}</p>
                    <p class="text-sm text-purple-200 italic leading-snug">「{{ invokeAxes._slogan }}」</p>
                  </div>

                  <!-- ② 8-axis grid -->
                  <div>
                    <p class="text-[9px] font-semibold text-gray-600 uppercase tracking-wider mb-2">{{ t('invoke.axisLabel') }}</p>
                    <div class="grid grid-cols-2 gap-1.5">
                      <div v-for="ax in AXIS_DISPLAY" :key="ax.key"
                        class="bg-gray-800/40 border border-gray-700/30 rounded-xl px-2.5 py-2">
                        <p class="text-[9px] text-gray-600 mb-0.5">{{ ax.label }}</p>
                        <p class="text-[10px] text-gray-300 leading-tight line-clamp-2">
                          {{ Array.isArray(invokeAxes[ax.key]) ? invokeAxes[ax.key].join(', ') : invokeAxes[ax.key] || '—' }}
                        </p>
                      </div>
                    </div>
                  </div>

                  <!-- ③ Spirit narratives -->
                  <div v-if="selectedNarrativeSpirit">
                    <p class="text-[9px] font-semibold text-gray-600 uppercase tracking-wider mb-2">{{ t('invoke.narrativeLabel') }}</p>
                    <div class="bg-gray-800/30 border border-gray-700/30 rounded-xl px-3 py-3 min-h-[4rem]">
                      <p class="text-[9px] mb-1.5" :class="SPIRIT_META[selectedNarrativeSpirit].color">
                        {{ locale === 'ja' ? SPIRIT_META[selectedNarrativeSpirit].kanji + ' ' : '' }}{{ SPIRIT_META[selectedNarrativeSpirit].en }}
                      </p>
                      <template v-if="invokeSpirits[selectedNarrativeSpirit]?.natural_language">
                        <p class="text-xs text-gray-300 leading-relaxed italic">
                          {{ (locale === 'ja' && invokeSpirits[selectedNarrativeSpirit].natural_language_ja)
                             ? invokeSpirits[selectedNarrativeSpirit].natural_language_ja
                             : invokeSpirits[selectedNarrativeSpirit].natural_language }}
                        </p>
                      </template>
                      <template v-else>
                        <p class="text-[10px] text-gray-600 italic">
                          <span v-if="['composing','waiting'].includes(invokeSpirits[selectedNarrativeSpirit]?.status)"
                            class="animate-pulse">{{ t('invoke.narrativeGenerating') }}</span>
                          <span v-else>—</span>
                        </p>
                      </template>
                    </div>
                  </div>

                </div>
              </div>

            </div>
          </div>

        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
@keyframes pulse-sync {
  0%, 100% { opacity: 1; }
  50%       { opacity: .5; }
}
.pulse-sync {
  animation: pulse-sync 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
</style>

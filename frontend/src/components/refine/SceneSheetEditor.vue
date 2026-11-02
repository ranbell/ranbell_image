<script setup>
// The scene sheet of the detailed (Krea 2) style, as cards the user can edit.
// The prompt is written from this sheet, so fixing a misread here (four
// people where there are five, a tambourine read as a hoop) fixes the prompt.
//
// Laid out in the order a picture is checked: how it is drawn, the stage,
// the light and camera, then each person. People are folded to a one-line
// summary when there are many. A lock (🔒) keeps a box or a person out of a
// spoken retake and a fusion; editing by hand always works.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  modelValue: { type: Object, required: true },
  locked:     { type: Array,  default: () => [] },
  disabled:   { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'update:locked'])
const { t } = useI18n()

const SCENE_GROUPS = [
  { key: 'story', boxes: ['situation', 'interaction'] },
  { key: 'look',  boxes: ['medium', 'rendering', 'palette_mood'] },
  { key: 'stage', boxes: ['setting', 'layers', 'props', 'effects', 'other'] },
  { key: 'shot',  boxes: ['lighting', 'camera'] },
]
// What they do first, how they look last (the Showrunner, 2026-11-01).
const PERSON_BOXES = ['who', 'position', 'pose_action', 'intent', 'feeling', 'expression', 'gaze',
                      'hands', 'legs_feet', 'motion', 'holding', 'appearance', 'outfit', 'details']

const subjects = computed(() => props.modelValue.subjects || [])
const foldPeople = computed(() => subjects.value.length > 2)

function update(patch) {
  const next = { ...props.modelValue, ...patch }
  if (patch.subjects) next.subject_count = patch.subjects.length
  emit('update:modelValue', next)
}

function setBox(key, value) { update({ [key]: value }) }

function setText(value) {
  update({ text_in_image: value.split(/[,、\n]/).map(s => s.trim()).filter(Boolean) })
}

// Tags that had no box of their own. Written into the prompt as they are;
// removing one here is the only way one leaves.
function setUnclassified(value) {
  update({ unclassified: value.split(/[,、\n]/).map(s => s.trim()).filter(Boolean) })
}

function setPerson(i, key, value) {
  update({ subjects: subjects.value.map((s, j) => (j === i ? { ...s, [key]: value } : s)) })
}

const isLocked = path => props.locked.includes(path)
function toggleLock(path) {
  emit('update:locked', isLocked(path)
    ? props.locked.filter(p => p !== path)
    : [...props.locked, path])
}

// Person locks follow the person when the list is reordered.
// order[newIndex] = oldIndex (or -1 for a new person)
function remapPersonLocks(order) {
  const scene = props.locked.filter(p => !p.startsWith('subjects.'))
  const people = []
  order.forEach((oldIdx, newIdx) => {
    if (oldIdx >= 0 && isLocked(`subjects.${oldIdx}`)) people.push(`subjects.${newIdx}`)
  })
  emit('update:locked', [...scene, ...people])
}

function movePerson(i, delta) {
  const j = i + delta
  if (j < 0 || j >= subjects.value.length) return
  const order = subjects.value.map((_, k) => k)
  ;[order[i], order[j]] = [order[j], order[i]]
  update({ subjects: order.map(k => subjects.value[k]) })
  remapPersonLocks(order)
}

function duplicatePerson(i) {
  const order = subjects.value.map((_, k) => k)
  order.splice(i + 1, 0, -1)
  update({ subjects: order.map(k => (k < 0 ? { ...subjects.value[i] } : subjects.value[k])) })
  remapPersonLocks(order)
}

function removePerson(i) {
  const order = subjects.value.map((_, k) => k).filter(k => k !== i)
  update({ subjects: order.map(k => subjects.value[k]) })
  remapPersonLocks(order)
}

function addPerson() {
  const blank = Object.fromEntries(PERSON_BOXES.map(k => [k, '']))
  update({ subjects: [...subjects.value, blank] })
}

function summary(person) {
  const parts = [person.who, person.pose_action, person.position].filter(Boolean)
  if (person.holding) parts.push(`${t('refine.sheet.person_box.holding')}: ${person.holding}`)
  return parts.join(' · ')
}

const field = 'flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none disabled:opacity-60 resize-y'
</script>

<template>
  <div class="space-y-3">
    <!-- The scene, in the order a picture is checked -->
    <details v-for="group in SCENE_GROUPS" :key="group.key" open
      class="group bg-gray-950/50 border border-sky-900/40 rounded-xl">
      <summary class="px-3 py-2 text-[11px] font-semibold text-sky-300/90 cursor-pointer list-none flex items-center justify-between">
        <span>{{ t(`refine.sheet.group.${group.key}`) }}</span>
        <span class="text-gray-600 group-open:rotate-180 transition-transform">▼</span>
      </summary>
      <div class="px-3 pb-3 space-y-1.5">
        <div v-for="key in group.boxes" :key="key" class="flex items-start gap-2">
          <label class="w-20 flex-shrink-0 text-[10px] text-gray-500 pt-1.5">{{ t(`refine.sheet.box.${key}`) }}</label>
          <textarea :value="modelValue[key]" rows="2" :disabled="disabled"
            @input="setBox(key, $event.target.value)" :class="[field, 'focus:border-sky-500']" />
          <button @click="toggleLock(key)" :disabled="disabled" :title="t('refine.sheet.lockTip')"
            class="text-xs pt-1 w-5 flex-shrink-0" :class="isLocked(key) ? 'opacity-100' : 'opacity-25 hover:opacity-70'">🔒</button>
        </div>
      </div>
    </details>

    <div class="flex items-start gap-2 px-3">
      <label class="w-20 flex-shrink-0 text-[10px] text-gray-500 pt-1.5">{{ t('refine.sheet.box.text_in_image') }}</label>
      <input :value="(modelValue.text_in_image || []).join(', ')" :disabled="disabled"
        @change="setText($event.target.value)" :placeholder="t('refine.sheet.textPlaceholder')"
        :class="[field, 'focus:border-sky-500']" />
      <button @click="toggleLock('text_in_image')" :disabled="disabled" :title="t('refine.sheet.lockTip')"
        class="text-xs pt-1 w-5 flex-shrink-0" :class="isLocked('text_in_image') ? 'opacity-100' : 'opacity-25 hover:opacity-70'">🔒</button>
    </div>
    <div v-if="(modelValue.unclassified || []).length" class="flex items-start gap-2 px-3">
      <label class="w-20 flex-shrink-0 text-[10px] text-amber-400/80 pt-1.5">{{ t('refine.sheet.box.unclassified') }}</label>
      <input :value="(modelValue.unclassified || []).join(', ')" :disabled="disabled"
        @change="setUnclassified($event.target.value)" :title="t('refine.sheet.unclassifiedHint')"
        :class="[field, 'focus:border-amber-500']" />
      <span class="w-5 flex-shrink-0"></span>
    </div>

    <!-- People -->
    <div class="flex items-center justify-between pt-1">
      <p class="text-[11px] font-semibold text-pink-300/90">{{ t('refine.sheet.people', { n: subjects.length }) }}</p>
      <button @click="addPerson" :disabled="disabled"
        class="text-[11px] px-2 py-0.5 rounded-md bg-gray-800 border border-gray-700 text-gray-300 hover:border-pink-500 disabled:opacity-50">
        ＋ {{ t('refine.sheet.addPerson') }}
      </button>
    </div>
    <details v-for="(person, i) in subjects" :key="i" :open="!foldPeople"
      class="group bg-gray-950/50 border rounded-xl"
      :class="isLocked(`subjects.${i}`) ? 'border-pink-500/60' : 'border-pink-900/30'">
      <summary class="px-3 py-2 cursor-pointer list-none flex items-center gap-1.5">
        <span class="text-[11px] font-semibold text-pink-300 flex-shrink-0">{{ t('refine.sheet.person', { n: i + 1 }) }}</span>
        <span class="text-[10px] text-gray-400 truncate flex-1">{{ summary(person) }}</span>
        <span v-if="isLocked(`subjects.${i}`)" class="text-[9px] text-pink-300 flex-shrink-0">🔒 {{ t('refine.sheet.lockedBadge') }}</span>
        <span class="text-gray-600 group-open:rotate-180 transition-transform flex-shrink-0">▼</span>
      </summary>
      <div class="px-3 pb-3 space-y-1.5">
        <div class="flex items-center gap-2 pb-1 border-b border-gray-800">
          <button @click="toggleLock(`subjects.${i}`)" :disabled="disabled"
            class="text-[10px] px-2 py-0.5 rounded-md border"
            :class="isLocked(`subjects.${i}`) ? 'bg-pink-900/40 border-pink-600/60 text-pink-200' : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-gray-200'">
            🔒 {{ isLocked(`subjects.${i}`) ? t('refine.sheet.unlockPerson') : t('refine.sheet.lockPerson') }}
          </button>
          <span class="flex-1"></span>
          <button @click="movePerson(i, -1)" :disabled="disabled || i === 0" class="text-xs text-gray-500 hover:text-gray-200 disabled:opacity-30" :title="t('refine.sheet.moveUp')">↑</button>
          <button @click="movePerson(i, 1)" :disabled="disabled || i === subjects.length - 1" class="text-xs text-gray-500 hover:text-gray-200 disabled:opacity-30" :title="t('refine.sheet.moveDown')">↓</button>
          <button @click="duplicatePerson(i)" :disabled="disabled" class="text-xs text-gray-500 hover:text-gray-200 disabled:opacity-30" :title="t('refine.sheet.duplicate')">⧉</button>
          <button @click="removePerson(i)" :disabled="disabled" class="text-xs text-gray-500 hover:text-red-400 disabled:opacity-30" :title="t('refine.sheet.remove')">✕</button>
        </div>
        <div v-for="key in PERSON_BOXES" :key="key" class="flex items-start gap-2">
          <label class="w-20 flex-shrink-0 text-[10px] text-gray-500 pt-1">{{ t(`refine.sheet.person_box.${key}`) }}</label>
          <textarea :value="person[key]" rows="1" :disabled="disabled"
            @input="setPerson(i, key, $event.target.value)" :class="[field, 'focus:border-pink-500']" />
        </div>
      </div>
    </details>
  </div>
</template>

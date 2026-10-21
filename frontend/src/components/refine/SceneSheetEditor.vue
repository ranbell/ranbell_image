<script setup>
// The scene sheet of the detailed (Krea 2) style, as cards the user can edit.
// The prompt is written from this sheet, so fixing a misread here (four
// people where there are five, a tambourine read as a hoop) fixes the prompt.
// Locks are paths: a scene box ("lighting") or a whole person ("subjects.2");
// a spoken retake leaves locked ones alone.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  modelValue: { type: Object, required: true },
  locked:     { type: Array,  default: () => [] },
  disabled:   { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'update:locked'])
const { t } = useI18n()

const SCENE_BOXES = ['medium', 'rendering', 'setting', 'layers', 'props', 'effects',
                     'lighting', 'camera', 'palette_mood']
const PERSON_BOXES = ['who', 'position', 'appearance', 'outfit', 'details',
                      'expression', 'pose_action', 'holding']

const subjects = computed(() => props.modelValue.subjects || [])

function update(patch) {
  const next = { ...props.modelValue, ...patch }
  if (patch.subjects) next.subject_count = patch.subjects.length
  emit('update:modelValue', next)
}

function setBox(key, value) { update({ [key]: value }) }

function setText(value) {
  update({ text_in_image: value.split(/[,、\n]/).map(s => s.trim()).filter(Boolean) })
}

function setPerson(i, key, value) {
  const list = subjects.value.map((s, j) => (j === i ? { ...s, [key]: value } : s))
  update({ subjects: list })
}

const isLocked = path => props.locked.includes(path)
function toggleLock(path) {
  emit('update:locked', isLocked(path)
    ? props.locked.filter(p => p !== path)
    : [...props.locked, path])
}

// Person locks follow the person when the list is reordered.
function remapPersonLocks(order) {
  // order[newIndex] = oldIndex (or -1 for a new person)
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
</script>

<template>
  <div class="space-y-3">
    <!-- Scene -->
    <div class="bg-gray-900/60 border border-sky-900/40 rounded-xl p-3 space-y-2">
      <p class="text-[11px] font-semibold text-sky-300/80 uppercase tracking-wide">{{ t('refine.sheet.scene') }}</p>
      <div v-for="key in SCENE_BOXES" :key="key" class="flex items-start gap-2">
        <label class="w-24 flex-shrink-0 text-[10px] text-gray-500 pt-1.5">{{ t(`refine.sheet.box.${key}`) }}</label>
        <textarea :value="modelValue[key]" rows="2" :disabled="disabled || isLocked(key)"
          @input="setBox(key, $event.target.value)"
          class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-sky-500 disabled:opacity-60 resize-y" />
        <button @click="toggleLock(key)" :disabled="disabled" :title="t('refine.sheet.lockTip')"
          class="text-xs pt-1 w-5 flex-shrink-0" :class="isLocked(key) ? 'opacity-100' : 'opacity-30 hover:opacity-70'">🔒</button>
      </div>
      <div class="flex items-start gap-2">
        <label class="w-24 flex-shrink-0 text-[10px] text-gray-500 pt-1.5">{{ t('refine.sheet.box.text_in_image') }}</label>
        <input :value="(modelValue.text_in_image || []).join(', ')" :disabled="disabled || isLocked('text_in_image')"
          @change="setText($event.target.value)"
          class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-sky-500 disabled:opacity-60" />
        <button @click="toggleLock('text_in_image')" :disabled="disabled" :title="t('refine.sheet.lockTip')"
          class="text-xs pt-1 w-5 flex-shrink-0" :class="isLocked('text_in_image') ? 'opacity-100' : 'opacity-30 hover:opacity-70'">🔒</button>
      </div>
    </div>

    <!-- People -->
    <div class="flex items-center justify-between">
      <p class="text-[11px] font-semibold text-pink-300/80 uppercase tracking-wide">
        {{ t('refine.sheet.people', { n: subjects.length }) }}
      </p>
      <button @click="addPerson" :disabled="disabled"
        class="text-[11px] px-2 py-0.5 rounded-md bg-gray-800 border border-gray-700 text-gray-300 hover:border-pink-500 disabled:opacity-50">
        ＋ {{ t('refine.sheet.addPerson') }}
      </button>
    </div>
    <div v-for="(person, i) in subjects" :key="i"
      class="bg-gray-900/60 border rounded-xl p-3 space-y-1.5"
      :class="isLocked(`subjects.${i}`) ? 'border-pink-500/50' : 'border-pink-900/30'">
      <div class="flex items-center gap-1.5">
        <span class="text-[11px] font-semibold text-pink-300">{{ t('refine.sheet.person', { n: i + 1 }) }}</span>
        <span class="text-[10px] text-gray-500 truncate flex-1">{{ person.who }}</span>
        <button @click="movePerson(i, -1)" :disabled="disabled || i === 0" class="text-xs text-gray-500 hover:text-gray-200 disabled:opacity-30" :title="t('refine.sheet.moveUp')">↑</button>
        <button @click="movePerson(i, 1)" :disabled="disabled || i === subjects.length - 1" class="text-xs text-gray-500 hover:text-gray-200 disabled:opacity-30" :title="t('refine.sheet.moveDown')">↓</button>
        <button @click="duplicatePerson(i)" :disabled="disabled" class="text-xs text-gray-500 hover:text-gray-200 disabled:opacity-30" :title="t('refine.sheet.duplicate')">⧉</button>
        <button @click="removePerson(i)" :disabled="disabled || isLocked(`subjects.${i}`)" class="text-xs text-gray-500 hover:text-red-400 disabled:opacity-30" :title="t('refine.sheet.remove')">✕</button>
        <button @click="toggleLock(`subjects.${i}`)" :disabled="disabled" :title="t('refine.sheet.lockTip')"
          class="text-xs" :class="isLocked(`subjects.${i}`) ? 'opacity-100' : 'opacity-30 hover:opacity-70'">🔒</button>
      </div>
      <div v-for="key in PERSON_BOXES" :key="key" class="flex items-start gap-2">
        <label class="w-24 flex-shrink-0 text-[10px] text-gray-500 pt-1">{{ t(`refine.sheet.person_box.${key}`) }}</label>
        <textarea :value="person[key]" rows="1" :disabled="disabled || isLocked(`subjects.${i}`)"
          @input="setPerson(i, key, $event.target.value)"
          class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-pink-500 disabled:opacity-60 resize-y" />
      </div>
    </div>
  </div>
</template>

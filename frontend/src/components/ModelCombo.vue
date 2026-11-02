<script setup>
// A text field with a list of every known model under it.
//
// <datalist> was used before, and the browser filters its options by what is
// already typed — so with a model chosen, the list showed only that model (or
// nothing) until the field was cleared. Here the list always shows every
// option; typing a name by hand still works for a model the list lacks.
import { ref, computed, onBeforeUnmount } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  emptyText: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'open'])

const open = ref(false)
const root = ref(null)
const value = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
})

function show() {
  if (open.value) return
  open.value = true
  // The parent fetches the list when it has none yet.
  emit('open')
  document.addEventListener('mousedown', onOutside)
}
function hide() {
  open.value = false
  document.removeEventListener('mousedown', onOutside)
}
function toggle() { open.value ? hide() : show() }
function pick(m) {
  value.value = m
  hide()
}
function onOutside(e) {
  if (root.value && !root.value.contains(e.target)) hide()
}
onBeforeUnmount(() => document.removeEventListener('mousedown', onOutside))
</script>

<template>
  <div ref="root" class="relative">
    <div class="flex">
      <input v-model="value" type="text" :placeholder="placeholder" autocomplete="off"
        @focus="show" @click="show" @keydown.esc="hide" @keydown.tab="hide"
        class="flex-1 min-w-0 bg-gray-700 border border-gray-600 rounded-l-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
      <button type="button" @mousedown.prevent @click="toggle" tabindex="-1"
        class="px-2 bg-gray-700 border border-l-0 border-gray-600 rounded-r-lg text-gray-400 hover:text-gray-200">
        <span class="inline-block text-xs transition-transform" :class="open ? 'rotate-180' : ''">▼</span>
      </button>
    </div>
    <ul v-if="open"
      class="absolute z-20 mt-1 w-full max-h-60 overflow-y-auto bg-gray-800 border border-gray-600 rounded-lg shadow-lg py-1">
      <li v-if="loading" class="px-3 py-1.5 text-xs text-gray-500">…</li>
      <li v-else-if="!options.length" class="px-3 py-1.5 text-xs text-gray-500">{{ emptyText }}</li>
      <li v-for="m in options" :key="m">
        <button type="button" @mousedown.prevent @click="pick(m)"
          class="w-full text-left px-3 py-1.5 text-sm font-mono truncate hover:bg-gray-700"
          :class="m === modelValue ? 'text-purple-300' : 'text-gray-200'">
          {{ m === modelValue ? '✓ ' : '' }}{{ m }}
        </button>
      </li>
    </ul>
  </div>
</template>

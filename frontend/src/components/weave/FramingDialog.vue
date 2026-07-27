<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: Boolean,
  panelKey: { type: String, default: '' },
  fails: { type: Number, default: 0 },
  limit: { type: Number, default: 2 },
  workflows: { type: Array, default: () => [] },
  currentWorkflow: { type: String, default: '' },
  busy: Boolean,
})
const emit = defineEmits(['close', 'choose'])
const { t } = useI18n()

const choice = ref('1')
const altWorkflow = ref('')
const overrideReason = ref('')

watch(() => props.show, (v) => {
  if (v) {
    choice.value = '1'
    altWorkflow.value = props.currentWorkflow || ''
    overrideReason.value = ''
  }
})

const canOverride = computed(() => props.fails >= props.limit)
const error = ref('')

function submit() {
  error.value = ''
  const c = choice.value
  if (c === '3') {
    if (!String(altWorkflow.value || '').trim()) {
      error.value = t('weave.framingNeedWorkflow')
      return
    }
    emit('choose', { choice: '3', workflow: String(altWorkflow.value).trim() })
    return
  }
  if (c === '4') {
    if (!canOverride.value) {
      error.value = t('weave.overrideNeedFails', { limit: props.limit })
      return
    }
    if (!String(overrideReason.value || '').trim()) {
      error.value = t('weave.overrideNeedReason')
      return
    }
    emit('choose', { choice: '4', reason: String(overrideReason.value).trim() })
    return
  }
  emit('choose', { choice: c })
}
</script>

<template>
  <div v-if="show" class="fixed inset-0 z-[90] flex items-center justify-center bg-black/60 p-4"
    @mousedown.self="emit('close')">
    <div class="w-full max-w-md rounded-xl border border-teal-800/50 bg-gray-950 p-4 shadow-2xl space-y-3">
      <div class="flex items-start justify-between gap-2">
        <div>
          <h3 class="text-sm font-medium text-teal-100">{{ t('weave.framingFixTitle') }}</h3>
          <p class="text-[11px] text-gray-400 mt-0.5">
            {{ panelKey }} · {{ t('weave.framingFails', { fails, limit }) }}
          </p>
        </div>
        <button class="text-gray-500 hover:text-white text-sm" @click="emit('close')">✕</button>
      </div>

      <div class="space-y-2">
        <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-2 cursor-pointer"
          :class="choice === '1' ? 'border-teal-600/50' : ''">
          <input v-model="choice" type="radio" value="1" class="mt-1 accent-teal-500" />
          <span>
            <span class="block text-[12px] text-teal-50">{{ t('weave.framingOpt1') }}</span>
            <span class="block text-[10px] text-gray-500">{{ t('weave.framingOpt1Hint') }}</span>
          </span>
        </label>
        <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-2 cursor-pointer"
          :class="choice === '2' ? 'border-teal-600/50' : ''">
          <input v-model="choice" type="radio" value="2" class="mt-1 accent-teal-500" />
          <span>
            <span class="block text-[12px] text-teal-50">{{ t('weave.framingOpt2') }}</span>
            <span class="block text-[10px] text-gray-500">{{ t('weave.framingOpt2Hint') }}</span>
          </span>
        </label>
        <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-2 cursor-pointer"
          :class="choice === '3' ? 'border-teal-600/50' : ''">
          <input v-model="choice" type="radio" value="3" class="mt-1 accent-teal-500" />
          <span class="block flex-1">
            <span class="block text-[12px] text-teal-50">{{ t('weave.framingOpt3') }}</span>
            <select v-if="workflows.length" v-model="altWorkflow"
              class="mt-1 w-full rounded border border-gray-800 bg-gray-950 px-2 py-1 text-[11px]"
              @click.stop>
              <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
            </select>
            <input v-else v-model="altWorkflow" type="text"
              class="mt-1 w-full rounded border border-gray-800 bg-gray-950 px-2 py-1 text-[11px]"
              :placeholder="t('weave.framingPickWorkflow')"
              @click.stop />
          </span>
        </label>
        <label class="flex items-start gap-2 rounded border border-gray-800 bg-gray-900/60 px-2 py-2 cursor-pointer"
          :class="[
            choice === '4' ? 'border-teal-600/50' : '',
            !canOverride ? 'opacity-50' : '',
          ]">
          <input v-model="choice" type="radio" value="4" class="mt-1 accent-teal-500" :disabled="!canOverride" />
          <span class="block flex-1">
            <span class="block text-[12px] text-teal-50">{{ t('weave.framingOpt4') }}</span>
            <span class="block text-[10px] text-gray-500 mb-1">{{ t('weave.framingOpt4Hint', { limit }) }}</span>
            <input v-model="overrideReason" type="text"
              class="w-full rounded border border-gray-800 bg-gray-950 px-2 py-1 text-[11px]"
              :placeholder="t('weave.overrideReasonPh')"
              :disabled="!canOverride"
              @click.stop />
          </span>
        </label>
      </div>

      <p v-if="error" class="text-[11px] text-amber-300">{{ error }}</p>

      <div class="flex justify-end gap-2 pt-1">
        <button class="rounded px-3 py-1.5 text-[11px] text-gray-400 hover:text-white" @click="emit('close')">
          {{ t('weave.cancel') }}
        </button>
        <button class="rounded border border-teal-600/50 bg-teal-900/60 px-3 py-1.5 text-[11px] text-teal-100 disabled:opacity-40"
          :disabled="busy" @click="submit">
          {{ t('weave.framingApply') }}
        </button>
      </div>
    </div>
  </div>
</template>

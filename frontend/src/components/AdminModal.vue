<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ProgressBar from './ProgressBar.vue'

const { t, locale } = useI18n()

function setLocale(lang) {
  locale.value = lang
  localStorage.setItem('locale', lang)
}

const props = defineProps({
  show: Boolean,
  pipelineState: Object,   // { running, ... }
  scanState: Object,       // { running, processed, total, mode } | null
  jobs: { type: Array, default: () => [] },
  selectedCount: Number,
  selectedIds: Object,     // Set<string>
})

const emit = defineEmits([
  'update:show',
  'toast',
  'trigger-pipeline',    // payload: sha256s[] (empty = all)
  'cancel-pipeline',
  'cancel-job',          // payload: job_id
  'trigger-refresh-metadata',
  'trigger-full-scan',
  'reload-workflows',    // payload: string[] (new workflow list)
])

// ── Admin internal state ──────────────────────────────────────────────────────
const adminTab = ref('ai')
const ollamaModels = ref([])
const showAdvanced = ref(false)
const adminStats = ref(null)
const adminConfig = ref(null)
const adminLoading = ref('')
const adminError = ref('')
const adminSuccess = ref('')
const adminConfirm = ref(null)
const vocabStatus = ref(null)   // {imported: bool, tag_count: int}
const vocabImporting = ref(false)
const mrlStatus = ref(null)
const colorStatus = ref(null)
const duplicatesData = ref(null)
const duplicatesLoading = ref(false)
const backendOffline = ref(false)

const configWorkflows = ref([])

// Connection tab
const healthData = ref(null)
const healthLoading = ref(false)

// Info tab (local copies — App.vue has its own for the header)
const info = ref(null)
const aiStatus = ref(null)

const noiseTagsText = computed({
  get: () => (adminConfig.value?.graph_noise_tags ?? []).join('\n'),
  set: (val) => {
    if (adminConfig.value)
      adminConfig.value.graph_noise_tags = val.split(/[\n,]/).map(t => t.trim()).filter(Boolean)
  }
})

const clusterCommonTagsText = computed({
  get: () => (adminConfig.value?.cluster_common_tags ?? []).join('\n'),
  set: (val) => {
    if (adminConfig.value)
      adminConfig.value.cluster_common_tags = val.split(/[\n,]/).map(t => t.trim()).filter(Boolean)
  }
})

const promptRemovalTagsText = computed({
  get: () => (adminConfig.value?.prompt_removal_tags ?? []).join('\n'),
  set: (val) => {
    if (adminConfig.value)
      adminConfig.value.prompt_removal_tags = val.split(/[\n,]/).map(t => t.trim()).filter(Boolean)
  }
})

// ── Admin functions ───────────────────────────────────────────────────────────

async function fetchAdminStats() {
  adminLoading.value = 'stats'
  try {
    const r = await fetch('/api/admin/stats')
    if (r.ok) {
      adminStats.value = await r.json()
      backendOffline.value = false
    } else {
      backendOffline.value = true
    }
  } catch {
    backendOffline.value = true
  } finally {
    adminLoading.value = ''
  }
}

async function fetchVocabStatus() {
  try {
    const r = await fetch('/api/admin/invoke/vocab-status')
    if (r.ok) vocabStatus.value = await r.json()
  } catch {}
}

async function importVocab() {
  vocabImporting.value = true
  try {
    const r = await fetch('/api/admin/invoke/import-wd14-vocab', { method: 'POST' })
    if (r.ok) {
      adminSuccess.value = 'WD14 vocab インポートをキューに追加しました。Control Room で進捗を確認してください。'
      setTimeout(() => fetchVocabStatus(), 3000)
    } else {
      adminError.value = `Vocab import error: ${await r.text()}`
    }
  } catch (e) {
    adminError.value = String(e)
  } finally {
    vocabImporting.value = false
  }
}

async function fetchAdminConfig() {
  try {
    const r = await fetch('/api/admin/config')
    if (r.ok) {
      const cfg = await r.json()
      if (!cfg.invoke_daily_oracle_timezone || cfg.invoke_daily_oracle_timezone === 'UTC') {
        cfg.invoke_daily_oracle_timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
      }
      adminConfig.value = cfg
    }
  } catch {}
}

async function saveAdminConfig() {
  adminLoading.value = 'config'
  adminError.value = ''
  try {
    const { source_images_dir, generated_images_dir, thumbnails_dir, ...body } = adminConfig.value
    const r = await fetch('/api/admin/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (r.ok) {
      adminConfig.value = await r.json()
      adminSuccess.value = t('admin.saveSuccess')
      setTimeout(() => { adminSuccess.value = '' }, 3000)
    }
  } catch (e) {
    adminError.value = e.message
  } finally {
    adminLoading.value = ''
  }
}

async function resetDailyOracle() {
  adminLoading.value = 'oracle-reset'
  adminError.value = ''
  try {
    const r = await fetch('/api/admin/invoke/daily-oracle', { method: 'DELETE' })
    if (r.ok) {
      const data = await r.json()
      adminSuccess.value = `${data.date} の実行記録を削除しました (${data.deleted} 件)`
      setTimeout(() => { adminSuccess.value = '' }, 4000)
    }
  } catch (e) {
    adminError.value = e.message
  } finally {
    adminLoading.value = ''
  }
}

async function adminAction(key, url, opts = {}) {
  adminLoading.value = key
  adminError.value = ''
  adminSuccess.value = ''
  const { successMsg, body, after, ...fetchOpts } = opts
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      ...(body ? { body } : {}),
      ...fetchOpts,
    })
    const data = await r.json()
    adminSuccess.value = successMsg?.(data) ?? 'OK'
    setTimeout(() => { adminSuccess.value = '' }, 4000)
    await fetchAdminStats()
    after?.()
    return data
  } catch (e) {
    adminError.value = e.message
  } finally {
    adminLoading.value = ''
    adminConfirm.value = null
  }
}

function confirmThen(message, description, action) {
  adminConfirm.value = { message, description, action }
}

async function fetchMrlStatus() {
  const r = await fetch('/api/admin/mrl/status')
  if (r.ok) mrlStatus.value = await r.json()
}

async function fetchColorStatus() {
  const r = await fetch('/api/admin/colors/status')
  if (!r.ok) return
  colorStatus.value = await r.json()
}

async function fetchDuplicates() {
  duplicatesLoading.value = true
  try {
    const r = await fetch('/api/admin/duplicates')
    if (r.ok) duplicatesData.value = await r.json()
  } finally {
    duplicatesLoading.value = false
  }
}

async function fetchHealth() {
  healthLoading.value = true
  healthData.value = null
  try {
    const res = await fetch('/api/health/detail')
    if (res.ok) {
      healthData.value = await res.json()
      const wfs = healthData.value?.comfyui?.workflows
      if (Array.isArray(wfs)) emit('reload-workflows', wfs)
    }
  } finally {
    healthLoading.value = false
  }
}

async function fetchInfo() {
  try {
    const res = await fetch('/api/info')
    if (res.ok) info.value = await res.json()
  } catch {}
}

async function fetchAiStatus() {
  try {
    const res = await fetch('/api/ai/status')
    if (res.ok) aiStatus.value = await res.json()
  } catch {}
}

async function fetchOllamaModels() {
  try {
    const r = await fetch('/api/ollama/models')
    if (r.ok) ollamaModels.value = (await r.json()).models ?? []
  } catch {}
}

async function reloadWorkflows() {
  try {
    const r = await fetch('/api/comfy/workflows')
    if (r.ok) {
      const list = await r.json()
      if (healthData.value?.comfyui) healthData.value.comfyui.workflows = list
      emit('reload-workflows', list)
    }
  } catch {}
}

const JOB_STATUS_CLASS = {
  running:    'bg-blue-500 text-blue-100',
  queued:     'bg-yellow-500/80 text-yellow-100',
  cancelling: 'bg-orange-500/80 text-orange-100',
  succeeded:  'bg-green-600 text-green-100',
  failed:     'bg-red-600 text-red-100',
  cancelled:  'bg-gray-600 text-gray-300',
}

function jobDuration(job) {
  const s = Math.floor(job.elapsed ?? 0)
  if (s === 0) return null
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}

const activeJobs = computed(() =>
  props.jobs.filter(j => j.state === 'running' || j.state === 'queued' || j.state === 'cancelling')
)
const historyJobs = computed(() =>
  props.jobs.filter(j => j.state === 'succeeded' || j.state === 'failed' || j.state === 'cancelled')
)

function switchAdminTab(id) {
  adminTab.value = id
  if (id === 'connection') fetchHealth()
  if (id === 'config' && !ollamaModels.value.length) fetchOllamaModels()
  if (id === 'config' && !configWorkflows.value.length) {
    fetch('/api/comfy/workflows').then(r => r.ok && r.json()).then(list => { if (list) configWorkflows.value = list }).catch(() => {})
  }
  if (id === 'info') { fetchInfo(); fetchAiStatus() }
  if (id === 'system') fetchInfo()
}

// Proxy functions that emit to App.vue
function triggerPipelineAll() {
  emit('trigger-pipeline', [])
  emit('update:show', false)
}

function triggerPipelineSelected() {
  emit('trigger-pipeline', [...(props.selectedIds || [])])
  emit('update:show', false)
}

function triggerPipelineProxy() {
  emit('trigger-pipeline', [])
}

function triggerFullScanProxy() {
  emit('trigger-full-scan')
}

// Initialize on open
watch(() => props.show, async (val) => {
  if (val) {
    adminTab.value = 'ai'
    backendOffline.value = false
    adminStats.value = null
    adminConfig.value = null
    await Promise.all([fetchAdminStats(), fetchAdminConfig(), fetchMrlStatus(), fetchColorStatus(), fetchOllamaModels(), fetchVocabStatus()])
  }
})

// Refresh backfill statuses when SSE-driven job state changes
watch(() => props.jobs?.find(j => j.title === 'color_extract')?.state, (state) => {
  if (state) fetchColorStatus()
})
watch(() => props.jobs?.find(j => j.title === 'mrl_backfill')?.state, (state) => {
  if (state) fetchMrlStatus()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
      @click.self="emit('update:show', false)">
      <div class="bg-gray-900 rounded-xl w-full max-w-3xl max-h-[92vh] flex flex-col shadow-2xl border border-gray-800">

        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-800 flex-shrink-0">
          <h2 class="font-semibold text-gray-100 text-base">{{ $t('admin.title') }}</h2>
          <button @click="emit('update:show', false)" class="text-gray-500 hover:text-gray-300">✕</button>
        </div>

        <div class="flex border-b border-gray-800 flex-shrink-0 px-6 overflow-x-auto">
          <button v-for="tab in [
            { id: 'overview',    label: $t('admin.overview.title') },
            { id: 'ai',          label: $t('admin.ai.title') },
            { id: 'config',      label: $t('admin.config.title') },
            { id: 'system',      label: $t('admin.system.title') },
            { id: 'jobs',        label: 'Jobs' },
            { id: 'connection',  label: $t('admin.connection.title') },
            { id: 'info',        label: $t('admin.info.title') },
          ]" :key="tab.id"
            @click="switchAdminTab(tab.id)"
            :class="adminTab === tab.id
              ? 'border-b-2 border-purple-500 text-purple-400'
              : 'text-gray-500 hover:text-gray-300'"
            class="px-4 py-3 text-sm font-medium transition-colors -mb-px whitespace-nowrap">
            {{ tab.label }}
          </button>
        </div>

        <!-- Backend offline banner -->
        <div v-if="backendOffline"
          class="mx-6 mt-4 px-4 py-3 rounded-lg border bg-red-950/70 border-red-700/60 text-red-300 flex-shrink-0 space-y-1">
          <p class="text-xs font-semibold">{{ $t('admin.offline502') }}</p>
          <p class="text-[11px] text-red-400/80" v-html="$t('admin.offline502Desc')"></p>
        </div>

        <div v-if="adminSuccess || adminError"
          :class="adminError ? 'bg-red-900/60 border-red-700/50 text-red-300' : 'bg-green-900/60 border-green-700/50 text-green-300'"
          class="mx-6 mt-4 px-4 py-2 rounded-lg border text-xs flex-shrink-0">
          {{ adminSuccess || adminError }}
        </div>

        <div v-if="adminConfirm" class="mx-6 mt-4 p-4 bg-red-950/60 border border-red-700/50 rounded-lg flex-shrink-0">
          <p class="text-sm font-medium text-red-300 mb-1">{{ adminConfirm.message }}</p>
          <p class="text-xs text-red-400/70 mb-3">{{ adminConfirm.description }}</p>
          <div class="flex gap-2">
            <button @click="adminConfirm.action()"
              :disabled="!!adminLoading"
              class="px-3 py-1.5 bg-red-700 hover:bg-red-600 disabled:opacity-40 rounded text-xs text-white font-medium">
              {{ $t('admin.execute') }}
            </button>
            <button @click="adminConfirm = null" class="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-xs text-gray-300">
              {{ $t('admin.cancel') }}
            </button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto px-6 py-4">

          <!-- ── Overview tab ── -->
          <div v-if="adminTab === 'overview'" class="space-y-5">
            <div v-if="adminLoading === 'stats'" class="text-center text-gray-500 py-8 text-sm">{{ $t('admin.loading') }}</div>
            <div v-else-if="backendOffline" class="text-center text-red-400 py-8 text-sm">{{ $t('admin.offline') }}</div>
            <template v-else-if="adminStats">
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs text-gray-500 mb-1">{{ $t('admin.overview.total') }}</p>
                  <p class="text-2xl font-bold text-gray-100">{{ adminStats.images.total.toLocaleString() }}</p>
                </div>
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs text-gray-500 mb-1">{{ $t('admin.overview.aiDone') }}</p>
                  <p class="text-2xl font-bold text-teal-400">{{ adminStats.images.ai_done.toLocaleString() }}</p>
                  <p class="text-xs text-gray-500 mt-0.5">{{ adminStats.images.ai_percent }}%</p>
                </div>
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs text-gray-500 mb-1">{{ $t('admin.overview.aiPending') }}</p>
                  <p class="text-2xl font-bold text-yellow-400">{{ adminStats.images.ai_pending.toLocaleString() }}</p>
                </div>
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs text-gray-500 mb-1">{{ $t('admin.overview.vectors') }}</p>
                  <p class="text-2xl font-bold text-purple-400">{{ adminStats.vectors.vector_count.toLocaleString() }}</p>
                </div>
              </div>

              <div class="bg-gray-800 rounded-xl p-4">
                <div class="flex justify-between text-xs text-gray-500 mb-2">
                  <span>{{ $t('admin.overview.progress') }}</span>
                  <span>{{ adminStats.images.ai_done }} / {{ adminStats.images.total }}</span>
                </div>
                <ProgressBar
                  :progress="adminStats.images.ai_percent / 100"
                  :progress-text="adminStats.images.ai_percent + '%'"
                  color="teal"
                  size="md"
                />
              </div>

              <div class="bg-gray-800 rounded-xl p-4">
                <p class="text-xs text-gray-500 uppercase tracking-wide mb-2">{{ $t('admin.overview.thumbnails') }}</p>
                <div class="flex justify-between text-sm">
                  <span class="text-gray-400">{{ adminStats.thumbnails.count.toLocaleString() }}</span>
                  <span class="text-gray-400">{{ adminStats.thumbnails.size_mb }} MB</span>
                </div>
              </div>

              <div class="bg-gray-800 rounded-xl p-4 space-y-2">
                <p class="text-xs text-gray-500 uppercase tracking-wide mb-2">{{ $t('admin.overview.paths') }}</p>
                <div v-for="(v, k) in adminStats.paths" :key="k" class="flex justify-between gap-4 text-xs">
                  <span class="text-gray-500 flex-shrink-0">{{ k }}</span>
                  <span class="text-gray-300 font-mono break-all text-right">{{ v }}</span>
                </div>
                <div v-if="adminStats.wd14" class="pt-2 border-t border-gray-700 space-y-1">
                  <p class="text-xs text-gray-500 uppercase tracking-wide mb-1">{{ $t('admin.wd14.modelLabel') }}</p>
                  <div class="flex justify-between text-xs">
                    <span class="text-gray-500">model.onnx</span>
                    <span :class="adminStats.wd14.model_ok ? 'text-green-400' : 'text-red-400'">
                      {{ adminStats.wd14.model_ok ? $t('admin.wd14.present') : $t('admin.wd14.missing') }}
                    </span>
                  </div>
                  <div class="flex justify-between text-xs">
                    <span class="text-gray-500">selected_tags.csv</span>
                    <span :class="adminStats.wd14.tags_ok ? 'text-green-400' : 'text-red-400'">
                      {{ adminStats.wd14.tags_ok ? $t('admin.wd14.present') : $t('admin.wd14.missing') }}
                    </span>
                  </div>
                  <div v-if="!adminStats.wd14.model_ok || !adminStats.wd14.tags_ok"
                    class="text-xs text-amber-400 mt-1">
                    Path: {{ adminStats.wd14.model_dir }} (fix under Admin → Settings)
                  </div>
                </div>
              </div>

              <!-- Invoke Vocab status -->
              <div class="pt-2 border-t border-gray-700/60 space-y-2">
                <p class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Invoke Vocab</p>
                <div class="flex items-center justify-between">
                  <span class="text-xs" :class="vocabStatus?.imported ? 'text-green-400' : 'text-amber-400'">
                    {{ vocabStatus?.imported
                      ? `${vocabStatus.tag_count.toLocaleString()} タグ登録済み`
                      : '未登録 — インポートが必要' }}
                  </span>
                  <button @click="importVocab" :disabled="vocabImporting"
                    class="px-2.5 py-1 rounded-lg border border-indigo-700/50 bg-indigo-900/40 hover:bg-indigo-800/50
                           text-[10px] text-indigo-300 disabled:opacity-40 transition">
                    {{ vocabImporting ? '...' : (vocabStatus?.imported ? '再インポート' : 'インポート実行') }}
                  </button>
                </div>
              </div>

              <button @click="fetchAdminStats" class="text-xs text-gray-500 hover:text-gray-300">{{ $t('admin.refresh') }}</button>
            </template>
          </div>

          <!-- ── AI Management tab ── -->
          <div v-if="adminTab === 'ai'" class="space-y-4">

            <div class="bg-gray-800 rounded-xl p-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('admin.ai.title') }}</p>
              <div class="flex gap-2 flex-wrap">
                <button
                  @click="triggerPipelineAll"
                  :disabled="pipelineState?.running"
                  class="px-4 py-2 bg-teal-800/60 hover:bg-teal-700/60 border border-teal-600/40 rounded-lg text-xs text-teal-300 disabled:opacity-40 transition-colors">
                  {{ $t('admin.ai.runAll') }}
                </button>
                <button v-if="selectedCount > 0"
                  @click="triggerPipelineSelected"
                  :disabled="pipelineState?.running"
                  class="px-4 py-2 bg-teal-900/50 hover:bg-teal-800/60 border border-teal-700/40 rounded-lg text-xs text-teal-400 disabled:opacity-40 transition-colors">
                  {{ $t('admin.ai.runSelected', { n: selectedCount }) }}
                </button>
                <button v-if="pipelineState?.running"
                  @click="emit('cancel-pipeline')"
                  class="px-4 py-2 bg-red-900/40 hover:bg-red-900/60 border border-red-700/40 rounded-lg text-xs text-red-300 transition-colors">
                  {{ $t('admin.ai.cancelRunning') }}
                </button>
              </div>
            </div>

            <!-- Alignment evaluation -->
            <div class="bg-gray-800 rounded-xl p-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">{{ $t('admin.alignment.title') }}</p>
              <p class="text-xs text-gray-500 mb-3">{{ $t('admin.alignment.desc') }}</p>
              <button
                @click="adminAction('alignment', '/api/alignment/evaluate', {
                  body: JSON.stringify({ sha256s: [] }),
                  successMsg: d => $t('admin.alignment.queued', { jobId: d.job_id })
                })"
                :disabled="adminLoading === 'alignment'"
                class="px-4 py-2 bg-purple-900/50 hover:bg-purple-800/60 border border-purple-700/40 rounded-lg text-xs text-purple-300 disabled:opacity-40 transition-colors">
                {{ adminLoading === 'alignment' ? $t('admin.alignment.queuing') : $t('admin.alignment.run') }}
              </button>
            </div>

            <!-- Advanced features -->
            <div class="border border-gray-700/50 rounded-xl overflow-hidden">
              <button @click="showAdvanced = !showAdvanced"
                class="w-full flex items-center justify-between px-4 py-3 bg-gray-800/50 hover:bg-gray-800 transition-colors text-left">
                <span class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ $t('admin.advanced') }}</span>
                <span class="text-gray-600 text-xs transition-transform" :class="showAdvanced ? 'rotate-180' : ''">▾</span>
              </button>
              <div v-if="showAdvanced" class="p-4 space-y-4 bg-gray-900/30">

                <!-- Bulk clear AI tags -->
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('admin.clear.title') }}</p>
                  <p class="text-xs text-gray-500 mb-4">{{ $t('admin.clear.desc') }}</p>
                  <div class="grid grid-cols-3 gap-2">
                    <button
                      @click="confirmThen(
                        $t('admin.clear.confirmAll'),
                        $t('admin.clear.confirmAllDesc', { n: adminStats?.images.total ?? '?' }),
                        () => adminAction('clearAll', '/api/admin/ai/clear',
                          { body: JSON.stringify({ scope: 'all' }), successMsg: d => $t('admin.clear.cleared', { n: d.cleared }) })
                      )"
                      :disabled="!!adminLoading"
                      class="py-2 bg-red-900/40 hover:bg-red-900/70 border border-red-700/40 rounded-lg text-xs text-red-300 disabled:opacity-40 transition-colors">
                      {{ $t('admin.clear.all') }}
                    </button>
                    <button
                      @click="confirmThen(
                        $t('admin.clear.confirmDone'),
                        $t('admin.clear.confirmDoneDesc', { n: adminStats?.images.ai_done ?? '?' }),
                        () => adminAction('clearDone', '/api/admin/ai/clear',
                          { body: JSON.stringify({ scope: 'done' }), successMsg: d => $t('admin.clear.cleared', { n: d.cleared }) })
                      )"
                      :disabled="!!adminLoading"
                      class="py-2 bg-orange-900/40 hover:bg-orange-900/60 border border-orange-700/40 rounded-lg text-xs text-orange-300 disabled:opacity-40 transition-colors">
                      {{ $t('admin.clear.done') }}
                    </button>
                    <button
                      @click="confirmThen(
                        $t('admin.clear.confirmPending'),
                        $t('admin.clear.confirmPendingDesc', { n: adminStats?.images.ai_pending ?? '?' }),
                        () => adminAction('clearPending', '/api/admin/ai/clear',
                          { body: JSON.stringify({ scope: 'pending' }), successMsg: d => $t('admin.clear.cleared', { n: d.cleared }) })
                      )"
                      :disabled="!!adminLoading"
                      class="py-2 bg-yellow-900/40 hover:bg-yellow-900/60 border border-yellow-700/40 rounded-lg text-xs text-yellow-300 disabled:opacity-40 transition-colors">
                      {{ $t('admin.clear.pending') }}
                    </button>
                  </div>
                </div>

                <!-- Re-extract metadata -->
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('admin.refreshMeta.title') }}</p>
                  <p class="text-xs text-gray-500 mb-4">{{ $t('admin.refreshMeta.desc') }}</p>
                  <button
                    @click="emit('trigger-refresh-metadata')"
                    :disabled="scanState?.running"
                    class="px-4 py-2 bg-teal-900/40 hover:bg-teal-900/60 border border-teal-700/40 rounded-lg text-xs text-teal-300 disabled:opacity-40 transition-colors">
                    {{ $t('admin.refreshMeta.btn') }}
                  </button>
                  <span v-if="scanState?.running && scanState.mode === 'refresh_metadata'" class="ml-3 text-xs text-gray-400">
                    {{ $t('admin.refreshMeta.progress', { processed: scanState.processed, total: scanState.total }) }}
                  </span>
                </div>

                <!-- Rebuild vector DB -->
                <div class="bg-gray-800 rounded-xl p-4">
                  <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('admin.rebuild.title') }}</p>
                  <p class="text-xs text-gray-500 mb-4">{{ $t('admin.rebuild.desc') }}</p>
                  <button
                    @click="confirmThen(
                      $t('admin.rebuild.confirm'),
                      $t('admin.rebuild.confirmDesc'),
                      () => adminAction('rebuildVectors', '/api/admin/vectors/rebuild',
                        { successMsg: d => $t('admin.rebuild.done', { n: d.reset }), after: triggerPipelineProxy })
                    )"
                    :disabled="!!adminLoading"
                    class="px-4 py-2 bg-purple-900/40 hover:bg-purple-900/60 border border-purple-700/40 rounded-lg text-xs text-purple-300 disabled:opacity-40 transition-colors">
                    {{ $t('admin.rebuild.btn') }}
                  </button>
                </div>

                <!-- MRL Backfill -->
                <div v-if="mrlStatus" class="bg-gray-800 rounded-xl p-4 space-y-3">
                  <div class="flex items-center justify-between">
                    <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.mrl.title') }}</p>
                    <button @click="fetchMrlStatus" class="text-xs text-gray-600 hover:text-gray-400">↺</button>
                  </div>
                  <div class="grid grid-cols-2 gap-2 text-xs">
                    <div class="bg-gray-900/60 rounded-lg p-2.5 space-y-1">
                      <p class="text-gray-500">{{ $t('admin.mrl.fullEmbed', { dim: mrlStatus.embed_dim }) }}</p>
                      <p :class="mrlStatus.full_embeddings === 0 && mrlStatus.total_images > 0 ? 'text-yellow-400' : 'text-gray-200'" class="font-mono">
                        {{ mrlStatus.full_embeddings.toLocaleString() }} / {{ mrlStatus.total_images.toLocaleString() }}
                      </p>
                    </div>
                    <div class="bg-gray-900/60 rounded-lg p-2.5 space-y-1">
                      <p class="text-gray-500">
                        {{ $t('admin.mrl.smallEmbed', { dim: mrlStatus.collection_dim_small ?? mrlStatus.embed_dim_small }) }}
                        <span v-if="mrlStatus.collection_dim_small && mrlStatus.collection_dim_small !== mrlStatus.embed_dim_small"
                          class="text-red-400 ml-1">
                          {{ $t('admin.mrl.mismatch', { dim: mrlStatus.embed_dim_small }) }}
                        </span>
                      </p>
                      <p :class="mrlStatus.needs_backfill ? 'text-yellow-400' : mrlStatus.small_embeddings > 0 ? 'text-green-400' : 'text-gray-500'" class="font-mono">
                        {{ mrlStatus.small_embeddings.toLocaleString() }}
                      </p>
                    </div>
                  </div>
                  <div v-if="mrlStatus.full_embeddings === 0 && mrlStatus.total_images > 0"
                    class="text-xs text-yellow-500/80 bg-yellow-900/20 rounded-lg px-3 py-2">
                    {{ $t('admin.mrl.notRun') }}
                  </div>
                  <div v-else-if="mrlStatus.needs_backfill" class="text-xs text-yellow-500/80 bg-yellow-900/20 rounded-lg px-3 py-2">
                    {{ $t('admin.mrl.needsBackfill', { n: mrlStatus.full_embeddings - mrlStatus.small_embeddings }) }}
                  </div>
                  <div v-else-if="mrlStatus.full_embeddings > 0 && mrlStatus.small_embeddings > 0" class="text-xs text-green-500/80">
                    {{ $t('admin.mrl.allDone') }}
                  </div>
                  <div v-else-if="mrlStatus.total_images === 0" class="text-xs text-gray-600">
                    {{ $t('admin.mrl.noImages') }}
                  </div>
                  <div v-if="mrlStatus.backfill.running" class="text-xs text-blue-400 flex items-center gap-2">
                    <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                    </svg>
                    {{ $t('admin.mrl.running', { n: mrlStatus.backfill.done }) }}
                  </div>
                  <button
                    v-if="mrlStatus.needs_backfill && !mrlStatus.backfill.running"
                    @click="adminAction('mrlBackfill', '/api/admin/mrl/backfill', { successMsg: () => $t('admin.mrl.backfillStart') }).then(fetchMrlStatus)"
                    :disabled="!!adminLoading"
                    class="w-full py-2 bg-indigo-900/40 hover:bg-indigo-800/60 border border-indigo-700/40 rounded-lg text-xs text-indigo-300 disabled:opacity-40 transition-colors">
                    {{ $t('admin.mrl.backfillBtn') }}
                  </button>
                </div>

                <!-- Color palette backfill -->
                <div class="bg-gray-800 rounded-xl p-4 space-y-3">
                  <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.color.title') }}</p>
                  <div v-if="colorStatus" class="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <p class="text-gray-500 text-xs">{{ $t('admin.color.extracted') }}</p>
                      <p :class="colorStatus.with_colors < colorStatus.total_images ? 'text-yellow-400' : 'text-green-400'" class="font-mono">
                        {{ colorStatus.with_colors.toLocaleString() }} / {{ colorStatus.total_images.toLocaleString() }}
                      </p>
                    </div>
                    <div>
                      <p class="text-gray-500 text-xs">{{ $t('admin.color.colorVector') }}</p>
                      <p :class="(colorStatus.with_color_vector ?? 0) < colorStatus.total_images ? 'text-yellow-400' : 'text-green-400'" class="font-mono">
                        {{ (colorStatus.with_color_vector ?? 0).toLocaleString() }} / {{ colorStatus.total_images.toLocaleString() }}
                      </p>
                    </div>
                  </div>
                  <div v-if="(colorStatus?.needs_backfill || colorStatus?.needs_color_vector_backfill) && !colorStatus?.backfill?.running"
                    class="text-xs text-yellow-500/80 bg-yellow-900/20 rounded-lg px-3 py-2">
                    {{ $t('admin.color.needsBackfill', { n: colorStatus.total_images - colorStatus.with_colors }) }}
                  </div>
                  <div v-else-if="colorStatus?.with_colors > 0 && !colorStatus?.needs_backfill && !colorStatus?.needs_color_vector_backfill" class="text-xs text-green-500/80">
                    {{ $t('admin.color.allDone') }}
                  </div>
                  <div v-if="colorStatus?.backfill?.running" class="text-xs text-blue-400 flex items-center gap-2">
                    <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                    </svg>
                    {{ $t('admin.color.running', { done: colorStatus.backfill.done, total: colorStatus.backfill.total }) }}
                  </div>
                  <button
                    v-if="!colorStatus?.backfill?.running"
                    @click="adminAction('colorBackfill', '/api/admin/colors/backfill', { successMsg: () => $t('admin.color.backfillStart') }).then(() => { fetchColorStatus() })"
                    :disabled="!!adminLoading"
                    :class="(colorStatus?.needs_backfill || colorStatus?.needs_color_vector_backfill)
                      ? 'bg-indigo-900/40 hover:bg-indigo-800/60 border-indigo-700/40 text-indigo-300'
                      : 'bg-gray-800/60 hover:bg-gray-700/60 border-gray-700/40 text-gray-500'"
                    class="w-full py-2 border rounded-lg text-xs disabled:opacity-40 transition-colors">
                    {{ $t('admin.color.backfillBtn') }}
                  </button>
                </div>

                <!-- Duplicate file detection -->
                <div class="bg-gray-800 rounded-xl p-4 space-y-3">
                  <div class="flex items-center justify-between">
                    <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.duplicates.title') }}</p>
                    <button @click="fetchDuplicates" :disabled="duplicatesLoading"
                      class="text-xs text-gray-600 hover:text-gray-400 disabled:opacity-40">
                      {{ duplicatesLoading ? $t('admin.duplicates.searching') : $t('admin.duplicates.search') }}
                    </button>
                  </div>

                  <p v-if="!duplicatesData && !duplicatesLoading" class="text-xs text-gray-600">
                    {{ $t('admin.duplicates.intro') }}
                  </p>
                  <p v-if="duplicatesLoading" class="text-xs text-gray-500">{{ $t('admin.duplicates.computing') }}</p>

                  <div v-if="duplicatesData" class="grid grid-cols-2 gap-2 text-xs">
                    <div class="bg-gray-900/60 rounded-lg p-2.5 space-y-1">
                      <p class="text-gray-500">{{ $t('admin.duplicates.totalFiles') }}</p>
                      <p class="font-mono text-gray-200">{{ duplicatesData.total_files_on_disk.toLocaleString() }}</p>
                    </div>
                    <div class="bg-gray-900/60 rounded-lg p-2.5 space-y-1">
                      <p class="text-gray-500">{{ $t('admin.duplicates.uniqueImages') }}</p>
                      <p class="font-mono text-gray-200">{{ duplicatesData.total_registered.toLocaleString() }}</p>
                    </div>
                    <div class="bg-gray-900/60 rounded-lg p-2.5 space-y-1">
                      <p class="text-gray-500">{{ $t('admin.duplicates.duplicateGroups') }}</p>
                      <p class="font-mono" :class="duplicatesData.duplicate_groups > 0 ? 'text-yellow-400' : 'text-green-400'">
                        {{ duplicatesData.duplicate_groups }}
                      </p>
                    </div>
                    <div class="bg-gray-900/60 rounded-lg p-2.5 space-y-1">
                      <p class="text-gray-500">{{ $t('admin.duplicates.extraFiles') }}</p>
                      <p class="font-mono" :class="duplicatesData.duplicate_extra_files > 0 ? 'text-yellow-400' : 'text-green-400'">
                        {{ duplicatesData.duplicate_extra_files }}
                      </p>
                    </div>
                  </div>

                  <p v-if="duplicatesData?.duplicate_groups === 0" class="text-xs text-green-400">{{ $t('admin.duplicates.none') }}</p>

                  <div v-if="duplicatesData?.groups?.length > 0" class="space-y-2 max-h-80 overflow-y-auto pr-1">
                    <div v-for="group in duplicatesData.groups" :key="group.sha256"
                      class="bg-gray-900/40 rounded-lg p-2.5 border border-yellow-700/20 text-xs space-y-1">
                      <div class="flex items-center gap-1.5">
                        <span class="text-green-400 shrink-0">✓</span>
                        <span class="text-gray-300 font-medium truncate" :title="group.registered_path">{{ group.registered_name }}</span>
                        <span class="text-gray-600 font-mono text-[10px] shrink-0">{{ group.sha256.slice(0, 8) }}</span>
                      </div>
                      <div class="text-gray-600 truncate text-[10px] pl-4">{{ group.registered_path }}</div>
                      <template v-for="copy in group.copies" :key="copy.path">
                        <div class="flex items-center gap-1.5 pl-4">
                          <span class="text-yellow-500 shrink-0">≡</span>
                          <span class="text-yellow-300/80 truncate" :title="copy.path">{{ copy.name }}</span>
                          <span class="text-gray-600 text-[10px] shrink-0">{{ (copy.size / 1024).toFixed(0) }}KB</span>
                        </div>
                        <div class="text-gray-600 truncate text-[10px] pl-4">{{ copy.path }}</div>
                      </template>
                    </div>
                  </div>
                </div>

              </div>
            </div>
          </div>

          <!-- ── Settings tab ── -->
          <div v-if="adminTab === 'config' && adminConfig" class="space-y-4">

            <div class="bg-gray-800 rounded-xl p-4 space-y-3">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.config.uiSettings') }}</p>
              <div>
                <label class="text-xs text-gray-500 block mb-2">{{ $t('admin.config.language') }}</label>
                <div class="flex gap-2">
                  <button @click="setLocale('en')"
                    :class="locale === 'en' ? 'bg-purple-600 border-purple-500 text-white' : 'bg-gray-700 border-gray-600 text-gray-400 hover:text-gray-200'"
                    class="px-4 py-1.5 rounded-lg border text-xs font-medium transition-colors">
                    {{ $t('admin.config.langEn') }}
                  </button>
                  <button @click="setLocale('ja')"
                    :class="locale === 'ja' ? 'bg-purple-600 border-purple-500 text-white' : 'bg-gray-700 border-gray-600 text-gray-400 hover:text-gray-200'"
                    class="px-4 py-1.5 rounded-lg border text-xs font-medium transition-colors">
                    {{ $t('admin.config.langJa') }}
                  </button>
                </div>
              </div>
            </div>

            <div class="bg-gray-800 rounded-xl p-4 space-y-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Ollama</p>
              <div>
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.config.ollamaUrl') }}</label>
                <input v-model="adminConfig.ollama_url" type="text"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-xs text-gray-500 flex items-center gap-1.5 mb-1">
                    {{ $t('admin.config.embedModel') }}
                    <button @click="fetchOllamaModels" class="text-gray-600 hover:text-gray-400" title="モデル一覧を再取得">↺</button>
                  </label>
                  <select v-if="ollamaModels.length" v-model="adminConfig.embed_model"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500">
                    <option v-for="m in ollamaModels" :key="m" :value="m">{{ m }}</option>
                    <option v-if="adminConfig.embed_model && !ollamaModels.includes(adminConfig.embed_model)"
                      :value="adminConfig.embed_model">{{ adminConfig.embed_model }}</option>
                  </select>
                  <input v-else v-model="adminConfig.embed_model" type="text"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                </div>
                <div>
                  <label class="text-xs text-gray-500 flex items-center gap-1.5 mb-1">
                    {{ $t('admin.config.vlmModel') }}
                  </label>
                  <select v-if="ollamaModels.length" v-model="adminConfig.vlm_model"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500">
                    <option v-for="m in ollamaModels" :key="m" :value="m">{{ m }}</option>
                    <option v-if="adminConfig.vlm_model && !ollamaModels.includes(adminConfig.vlm_model)"
                      :value="adminConfig.vlm_model">{{ adminConfig.vlm_model }}</option>
                  </select>
                  <input v-else v-model="adminConfig.vlm_model" type="text"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                </div>
              </div>
              <div>
                <label class="text-xs text-gray-500 flex justify-between mb-1">
                  <span>Context Size (num_ctx)</span>
                  <span class="text-purple-400 font-mono">{{ adminConfig.ollama_num_ctx ?? 16384 }}</span>
                </label>
                <input v-model.number="adminConfig.ollama_num_ctx" type="number" min="512" max="131072" step="512"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.config.numCtxHint') }}</p>
              </div>
            </div>

            <div class="bg-gray-800 rounded-xl p-4 space-y-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.inspireSettings.title') }}</p>
              <div class="flex items-center justify-between">
                <div>
                  <p class="text-xs text-gray-300">{{ $t('admin.inspireSettings.frozenset') }}</p>
                  <p class="text-[10px] text-gray-600 mt-0.5">{{ $t('admin.inspireSettings.frozensetDesc') }}</p>
                </div>
                <button @click="adminConfig.frozenset_classification = !adminConfig.frozenset_classification"
                  :class="adminConfig.frozenset_classification !== false ? 'bg-purple-600' : 'bg-gray-600'"
                  class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none">
                  <span :class="adminConfig.frozenset_classification !== false ? 'translate-x-4' : 'translate-x-0.5'"
                    class="inline-block h-4 w-4 mt-0.5 transform rounded-full bg-white transition-transform duration-200"></span>
                </button>
              </div>
            </div>

            <div class="bg-gray-800 rounded-xl p-4 space-y-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.config.wd14') }}</p>
              <div>
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.config.wd14ModelDir') }}</label>
                <input v-model="adminConfig.wd14_model_dir" type="text"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
              </div>
              <div>
                <label class="text-xs text-gray-500 flex justify-between mb-1">
                  <span>{{ $t('admin.config.wd14Threshold') }}</span>
                  <span class="text-purple-400 font-mono">{{ (adminConfig.wd14_threshold ?? 0.35).toFixed(2) }}</span>
                </label>
                <input v-model.number="adminConfig.wd14_threshold" type="range" min="0.10" max="0.90" step="0.01"
                  class="w-full accent-purple-500" />
                <div class="flex justify-between text-xs text-gray-600 mt-0.5">
                  <span>{{ $t('admin.config.wd14ThresholdHint') }}</span>
                  <span>{{ $t('admin.config.wd14ThresholdHintMax') }}</span>
                </div>
              </div>
            </div>

            <div class="bg-gray-800 rounded-xl p-4 space-y-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.config.pipeline') }}</p>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.config.pipelineBatch') }}</label>
                  <input v-model.number="adminConfig.pipeline_batch_size" type="number" min="100" max="100000" step="100"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                </div>
                <div>
                  <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.config.pipelineConcurrency') }}</label>
                  <input v-model.number="adminConfig.pipeline_concurrency" type="number" min="1" max="16"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                </div>
                <div>
                  <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.config.tagsCacheTtl') }}</label>
                  <input v-model.number="adminConfig.tags_cache_ttl" type="number" min="0" max="3600"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                </div>
              </div>
            </div>

            <!-- ── GPU priority control ─────────────────────────────────────────── -->
            <div class="bg-gray-800 rounded-xl p-4 space-y-3">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.gpuPriority.title') }}</p>

              <!-- Auto-pause -->
              <div class="flex items-center justify-between gap-3">
                <div>
                  <p class="text-xs text-gray-300">{{ $t('admin.gpuPriority.autoPause') }}</p>
                  <p class="text-[10px] text-gray-600 mt-0.5">{{ $t('admin.gpuPriority.autoPauseDesc') }}</p>
                </div>
                <button
                  @click="adminConfig.auto_pause_on_generation = !adminConfig.auto_pause_on_generation"
                  :class="adminConfig.auto_pause_on_generation ? 'bg-purple-600' : 'bg-gray-600'"
                  class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none">
                  <span
                    :class="adminConfig.auto_pause_on_generation ? 'translate-x-4' : 'translate-x-0.5'"
                    class="inline-block h-4 w-4 mt-0.5 transform rounded-full bg-white transition-transform duration-200">
                  </span>
                </button>
              </div>

              <!-- Target lane selection -->
              <div v-if="adminConfig.auto_pause_on_generation" class="flex gap-3 flex-wrap">
                <label
                  v-for="lane in ['embed', 'eval', 'sync']"
                  :key="lane"
                  class="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    :value="lane"
                    v-model="adminConfig.auto_pause_lanes"
                    class="accent-purple-500" />
                  <span class="text-xs text-gray-300">{{ lane }}</span>
                </label>
                <span class="text-[10px] text-gray-600 self-center">{{ $t('admin.gpuPriority.alwaysTrigger') }}</span>
              </div>

              <!-- Auto-run alignment evaluation -->
              <div class="border-t border-gray-700 pt-3 flex items-center justify-between gap-3">
                <div>
                  <p class="text-xs text-gray-300">{{ $t('admin.gpuPriority.autoAlignment') }}</p>
                  <p class="text-[10px] text-gray-600 mt-0.5">{{ $t('admin.gpuPriority.autoAlignmentDesc') }}</p>
                </div>
                <button
                  @click="adminConfig.auto_alignment_evaluate = !adminConfig.auto_alignment_evaluate"
                  :class="adminConfig.auto_alignment_evaluate ? 'bg-purple-600' : 'bg-gray-600'"
                  class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none">
                  <span
                    :class="adminConfig.auto_alignment_evaluate ? 'translate-x-4' : 'translate-x-0.5'"
                    class="inline-block h-4 w-4 mt-0.5 transform rounded-full bg-white transition-transform duration-200">
                  </span>
                </button>
              </div>

              <!-- Processing parallelism -->
              <div class="border-t border-gray-700 pt-3 space-y-3">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ $t('admin.gpuPriority.parallelism') }}</p>

                <!-- alignment_concurrency -->
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <p class="text-xs text-gray-300">{{ $t('admin.gpuPriority.alignConcurrency') }}</p>
                    <p class="text-[10px] text-gray-600 mt-0.5">{{ $t('admin.gpuPriority.alignConcurrencyDesc') }}</p>
                  </div>
                  <input
                    v-model.number="adminConfig.alignment_concurrency"
                    type="number" min="1" max="8"
                    class="w-16 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-gray-200 text-center focus:outline-none focus:border-purple-500" />
                </div>

                <!-- pipeline_auto_continue -->
                <div class="flex items-center justify-between gap-3">
                  <div>
                    <p class="text-xs text-gray-300">{{ $t('admin.gpuPriority.autoContinue') }}</p>
                    <p class="text-[10px] text-gray-600 mt-0.5">{{ $t('admin.gpuPriority.autoContinueDesc') }}</p>
                  </div>
                  <button
                    @click="adminConfig.pipeline_auto_continue = !adminConfig.pipeline_auto_continue"
                    :class="adminConfig.pipeline_auto_continue ? 'bg-purple-600' : 'bg-gray-600'"
                    class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none">
                    <span
                      :class="adminConfig.pipeline_auto_continue ? 'translate-x-4' : 'translate-x-0.5'"
                      class="inline-block h-4 w-4 mt-0.5 transform rounded-full bg-white transition-transform duration-200">
                    </span>
                  </button>
                </div>
              </div>
            </div>

            <div class="bg-gray-800 rounded-xl p-4 space-y-3">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.noiseTags.title') }}</p>
              <div>
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.noiseTags.noiseLabel') }}</label>
                <textarea v-model="noiseTagsText" rows="7"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono focus:outline-none focus:border-purple-500 resize-y" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.noiseTags.noiseHint') }}</p>
              </div>
              <div>
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.noiseTags.clusterLabel') }}</label>
                <textarea v-model="clusterCommonTagsText" rows="5"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono focus:outline-none focus:border-purple-500 resize-y" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.noiseTags.clusterHint') }}</p>
              </div>
              <div>
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.noiseTags.removalLabel') }}</label>
                <textarea v-model="promptRemovalTagsText" rows="4"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-xs text-gray-200 font-mono focus:outline-none focus:border-purple-500 resize-y" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.noiseTags.removalHint') }}</p>
              </div>
            </div>

            <div class="bg-gray-800/50 rounded-xl p-4 space-y-2">
              <p class="text-xs font-semibold text-gray-600 uppercase tracking-wide">{{ $t('admin.config.envVars') }}</p>
              <div class="grid grid-cols-1 gap-1">
                <div v-for="(v, k) in {
                  'source_images_dir': adminConfig.source_images_dir,
                  'generated_images_dir': adminConfig.generated_images_dir,
                  [$t('admin.config.thumbnailsDir')]: adminConfig.thumbnails_dir
                }" :key="k" class="flex justify-between gap-4 text-xs">
                  <span class="text-gray-600 shrink-0">{{ k }}</span>
                  <span class="text-gray-500 font-mono break-all text-right">{{ v }}</span>
                </div>
              </div>
            </div>

            <!-- ── Daily Oracle ── -->
            <div class="bg-gray-800 rounded-xl p-4 space-y-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.dailyOracle.title') }}</p>
              <div class="flex items-center justify-between">
                <div>
                  <p class="text-xs text-gray-300">{{ $t('admin.dailyOracle.enabled') }}</p>
                  <p class="text-[10px] text-gray-600 mt-0.5">{{ $t('admin.dailyOracle.enabledDesc') }}</p>
                </div>
                <button @click="adminConfig.invoke_daily_oracle_enabled = !adminConfig.invoke_daily_oracle_enabled"
                  :class="adminConfig.invoke_daily_oracle_enabled ? 'bg-purple-600' : 'bg-gray-600'"
                  class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none">
                  <span :class="adminConfig.invoke_daily_oracle_enabled ? 'translate-x-4' : 'translate-x-0.5'"
                    class="inline-block h-4 w-4 mt-0.5 transform rounded-full bg-white transition-transform duration-200"></span>
                </button>
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.dailyOracle.workflow') }}</label>
                <select v-model="adminConfig.invoke_daily_oracle_workflow"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500">
                  <option value="">{{ $t('admin.dailyOracle.workflowNone') }}</option>
                  <option v-for="wf in configWorkflows" :key="wf" :value="wf">{{ wf }}</option>
                </select>
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.dailyOracle.workflowHint') }}</p>
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <label class="text-xs text-gray-500 flex justify-between mb-1">
                  <span>{{ $t('admin.dailyOracle.retainDays') }}</span>
                  <span class="text-purple-400 font-mono">{{ adminConfig.invoke_daily_oracle_retain_days ?? 7 }}</span>
                </label>
                <input v-model.number="adminConfig.invoke_daily_oracle_retain_days" type="number" min="1" max="90"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.dailyOracle.topic') }}</label>
                <textarea v-model="adminConfig.invoke_daily_oracle_topic" rows="2"
                  :placeholder="$t('admin.dailyOracle.topicPlaceholder')"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500 resize-none" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.dailyOracle.topicHint') }}</p>
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.dailyOracle.executionTime') }}</label>
                <input v-model="adminConfig.invoke_daily_oracle_time" type="time"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <label class="text-xs text-gray-500 flex justify-between mb-1">
                  <span>{{ $t('admin.dailyOracle.timezone') }}</span>
                  <button @click="adminConfig.invoke_daily_oracle_timezone = Intl.DateTimeFormat().resolvedOptions().timeZone"
                    class="text-[10px] text-purple-400 hover:text-purple-300">{{ $t('admin.dailyOracle.detectTz') }}</button>
                </label>
                <input v-model="adminConfig.invoke_daily_oracle_timezone" type="text" spellcheck="false"
                  :placeholder="$t('admin.dailyOracle.timezonePlaceholder')"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 font-mono focus:outline-none focus:border-purple-500" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.dailyOracle.timezoneHint') }}</p>
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.dailyOracle.minFreeGb') }}</label>
                <input v-model.number="adminConfig.invoke_daily_oracle_min_free_gb" type="number" min="0" step="0.5"
                  class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-purple-500" />
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.dailyOracle.minFreeGbHint') }}</p>
              </div>
              <div v-if="adminConfig.invoke_daily_oracle_enabled">
                <button @click="resetDailyOracle" :disabled="adminLoading === 'oracle-reset'"
                  class="w-full py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 rounded-lg text-xs text-gray-300 transition-colors">
                  {{ adminLoading === 'oracle-reset' ? '削除中…' : $t('admin.dailyOracle.resetToday') }}
                </button>
                <p class="text-[10px] text-gray-600 mt-1">{{ $t('admin.dailyOracle.resetHint') }}</p>
              </div>
            </div>

            <div class="space-y-3 border border-gray-700 rounded-lg p-3">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.disk.title') }}</p>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.disk.cautionPct') }}</label>
                  <input v-model.number="adminConfig.disk_caution_pct" type="number" min="1" max="98" step="1"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-amber-300 focus:outline-none focus:border-purple-500" />
                </div>
                <div>
                  <label class="text-xs text-gray-500 block mb-1">{{ $t('admin.disk.faultPct') }}</label>
                  <input v-model.number="adminConfig.disk_fault_pct" type="number" min="2" max="99" step="1"
                    class="w-full bg-gray-700 border border-gray-600 rounded-lg px-3 py-2 text-sm text-red-400 focus:outline-none focus:border-purple-500" />
                </div>
              </div>
              <p class="text-[10px] text-gray-600">{{ $t('admin.disk.hint') }}</p>
            </div>

            <button @click="saveAdminConfig" :disabled="adminLoading === 'config'"
              class="w-full py-2.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors">
              {{ adminLoading === 'config' ? $t('admin.config.saving') : $t('admin.config.save') }}
            </button>
          </div>

          <!-- ── System tab ── -->
          <div v-if="adminTab === 'system'" class="space-y-4">

            <!-- Mount directory status -->
            <div class="bg-gray-800 rounded-xl p-4 space-y-3">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ $t('admin.mount.title') }}</p>
              <template v-if="info">
                <div class="space-y-2">
                  <div class="text-xs space-y-1">
                    <div class="flex items-center justify-between gap-3">
                      <div class="min-w-0">
                        <p class="text-gray-500">{{ $t('admin.mount.sourceImages') }}</p>
                        <p class="font-mono text-gray-400 break-all text-[11px]">{{ info.source_images_dir }}</p>
                      </div>
                      <span :class="info.source_images_dir_exists ? 'text-green-400' : 'text-red-400'" class="shrink-0 font-semibold">
                        {{ info.source_images_dir_exists ? $t('admin.mount.mounted') : $t('admin.mount.notMounted') }}
                      </span>
                    </div>
                    <!-- Subfolder list -->
                    <div v-if="info.source_mounts?.length" class="ml-3 mt-1 space-y-0.5">
                      <div v-for="m in info.source_mounts" :key="m.name"
                        class="flex items-center justify-between gap-2 text-[11px] text-gray-400">
                        <span class="font-mono text-gray-300">📁 {{ m.name }}/</span>
                        <span class="text-gray-500 shrink-0">{{ m.file_count.toLocaleString() }} files</span>
                      </div>
                    </div>
                    <p v-else-if="info.source_images_dir_exists" class="ml-3 text-[11px] text-amber-400">
                      No subfolders — add a mount in override.yml
                    </p>
                  </div>
                  <div class="flex items-start justify-between gap-3 text-xs">
                    <div class="min-w-0">
                      <p class="text-gray-500 mb-0.5">Generated images <span class="text-gray-600">(read-write)</span></p>
                      <p class="font-mono text-gray-300 break-all">{{ info.generated_images_dir }}</p>
                    </div>
                    <span :class="info.generated_images_dir_exists ? 'text-green-400' : 'text-red-400'" class="shrink-0 font-semibold">
                      {{ info.generated_images_dir_exists ? $t('admin.mount.mounted') : $t('admin.mount.notMounted') }}
                    </span>
                  </div>
                  <div class="pt-2 border-t border-gray-700 flex justify-between text-xs">
                    <span class="text-gray-500">{{ $t('admin.info.fileCount') }}</span>
                    <span class="text-gray-300">{{ info.image_files_found.toLocaleString() }}</span>
                  </div>
                  <div v-if="!info.source_images_dir_exists || !info.generated_images_dir_exists"
                    class="text-[11px] text-amber-400 bg-amber-950/40 border border-amber-800/40 rounded-lg p-2.5 mt-1">
                    Mount the volume in docker-compose.override.yml and restart.
                  </div>
                </div>
              </template>
              <div v-else class="text-xs text-gray-500">{{ $t('admin.loading') }}</div>
            </div>

            <div class="bg-gray-800 rounded-xl p-4">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('admin.system.cacheTitle') }}</p>
              <div class="flex items-center justify-between">
                <div>
                  <p class="text-sm text-gray-300">{{ $t('admin.system.clearThumbs') }}</p>
                  <p class="text-xs text-gray-600">{{ $t('admin.system.clearThumbsDesc', { count: adminStats?.thumbnails.count ?? '?', mb: adminStats?.thumbnails.size_mb ?? '?' }) }}</p>
                </div>
                <button
                  @click="confirmThen(
                    $t('admin.system.clearConfirm'),
                    $t('admin.system.clearConfirmDesc'),
                    () => adminAction('clearThumbs', '/api/admin/thumbnails/clear',
                      { successMsg: d => $t('admin.system.cleared', { n: d.deleted }) })
                  )"
                  :disabled="!!adminLoading"
                  class="px-3 py-1.5 bg-gray-700 hover:bg-red-900/50 border border-gray-600 hover:border-red-700/50 rounded-lg text-xs text-gray-300 disabled:opacity-40 transition-colors whitespace-nowrap ml-4">
                  {{ $t('admin.system.clearBtn') }}
                </button>
              </div>
            </div>

            <div class="bg-red-950/30 border border-red-800/30 rounded-xl p-4">
              <p class="text-xs font-semibold text-red-400 uppercase tracking-wide mb-3">{{ $t('admin.system.dangerZone') }}</p>
              <div class="flex items-center justify-between">
                <div>
                  <p class="text-sm text-gray-300">{{ $t('admin.system.deleteAll') }}</p>
                  <p class="text-xs text-gray-600">{{ $t('admin.system.deleteAllDesc') }}</p>
                </div>
                <button
                  @click="confirmThen(
                    $t('admin.system.deleteConfirm'),
                    $t('admin.system.deleteConfirmDesc'),
                    () => adminAction('fullRescan', '/api/admin/scan/full',
                      { successMsg: d => $t('admin.system.deleted', { n: d.deleted }),
                        after: triggerFullScanProxy })
                  )"
                  :disabled="!!adminLoading"
                  class="px-3 py-1.5 bg-red-900/50 hover:bg-red-800/70 border border-red-700/50 rounded-lg text-xs text-red-300 disabled:opacity-40 transition-colors whitespace-nowrap ml-4">
                  {{ $t('admin.system.deleteBtn') }}
                </button>
              </div>
            </div>
          </div>

          <!-- ── Jobs tab ── -->
          <div v-if="adminTab === 'jobs'" class="space-y-4">

            <!-- Active jobs -->
            <div class="bg-gray-800 rounded-xl p-4 space-y-2">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">Running / Queue</p>
              <div v-if="activeJobs.length === 0" class="text-xs text-gray-500 py-2">No active jobs</div>
              <div v-for="job in activeJobs" :key="job.id"
                class="bg-gray-700/50 rounded-lg p-3 space-y-1.5">
                <div class="flex items-center justify-between gap-2">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0"
                      :class="JOB_STATUS_CLASS[job.state] ?? 'bg-gray-600 text-gray-300'">
                      {{ job.state }}
                    </span>
                    <span class="text-xs text-gray-200 truncate">{{ job.title }}</span>
                  </div>
                  <button
                    @click="emit('cancel-job', job.id)"
                    class="shrink-0 px-2 py-0.5 text-[10px] bg-gray-600 hover:bg-red-900/60 border border-gray-500 hover:border-red-700/50 rounded text-gray-300 transition-colors">
                    {{ $t('admin.cancel') }}
                  </button>
                </div>
                <ProgressBar
                  v-if="job.state === 'running' && (job.progress > 0 || job.progress_indeterminate)"
                  :progress="job.progress || 0"
                  :progress-text="job.progress_text || null"
                  :indeterminate="job.progress_indeterminate"
                  color="default"
                  size="sm"
                />
                <div v-else-if="job.state === 'queued'" class="text-[11px] text-yellow-400/70">Waiting</div>
                <div v-else-if="job.state === 'cancelling'" class="text-[11px] text-orange-400/70">Stopping…</div>
                <div v-if="job.elapsed" class="text-[11px] text-gray-500">
                  Elapsed: {{ jobDuration(job) }}
                </div>
              </div>
            </div>

            <!-- History -->
            <div class="bg-gray-800 rounded-xl p-4 space-y-2">
              <p class="text-xs font-semibold text-gray-400 uppercase tracking-wide">History (last 20)</p>
              <div v-if="historyJobs.length === 0" class="text-xs text-gray-500 py-2">No history</div>
              <div v-for="job in historyJobs" :key="job.id"
                class="bg-gray-700/30 rounded-lg p-3 space-y-1">
                <div class="flex items-center gap-2">
                  <span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0"
                    :class="JOB_STATUS_CLASS[job.state] ?? 'bg-gray-600 text-gray-300'">
                    {{ job.state }}
                  </span>
                  <span class="text-xs text-gray-300 truncate">{{ job.title }}</span>
                  <span v-if="jobDuration(job)" class="ml-auto shrink-0 text-[11px] text-gray-500">{{ jobDuration(job) }}</span>
                </div>
                <div v-if="job.finished_at" class="text-[11px] text-gray-600">
                  {{ new Date(job.finished_at * 1000).toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}
                </div>
                <div v-if="job.error" class="text-[11px] text-red-400 bg-red-950/40 rounded p-1.5 font-mono break-all">{{ job.error }}</div>
              </div>
            </div>

          </div>

          <!-- ── Connection check tab ── -->
          <div v-if="adminTab === 'connection'" class="space-y-4 text-sm">
            <div class="flex items-center justify-between">
              <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{{ $t('admin.connection.status') }}</p>
              <button @click="fetchHealth()" :disabled="healthLoading"
                class="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-40">{{ $t('admin.refresh') }}</button>
            </div>
            <div v-if="healthLoading" class="text-center text-gray-400 py-8">{{ $t('admin.checking') }}</div>
            <template v-else-if="healthData">
              <div class="bg-gray-800 rounded-xl p-4 space-y-1">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ $t('admin.connection.backendApi') }}</p>
                <div class="flex justify-between">
                  <span class="text-gray-400">{{ $t('admin.connection.statusLabel') }}</span>
                  <span :class="healthData.backend.ok ? 'text-green-400' : 'text-red-400'">{{ healthData.backend.ok ? $t('admin.connection.ok') : $t('admin.connection.error') }}</span>
                </div>
                <div class="flex justify-between">
                  <span class="text-gray-400">{{ $t('admin.connection.version') }}</span>
                  <span class="text-gray-300">{{ healthData.backend.version }}</span>
                </div>
              </div>
              <div class="bg-gray-800 rounded-xl p-4 space-y-1">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Qdrant</p>
                <div class="flex justify-between">
                  <span class="text-gray-400">{{ $t('admin.connection.statusLabel') }}</span>
                  <span :class="healthData.qdrant.ok ? 'text-green-400' : 'text-red-400'">{{ healthData.qdrant.ok ? $t('admin.connection.ok') : $t('admin.connection.error') }}</span>
                </div>
                <div v-if="healthData.qdrant.ok" class="flex justify-between">
                  <span class="text-gray-400">{{ $t('admin.connection.docCount') }}</span>
                  <span class="text-gray-300">{{ healthData.qdrant.doc_count }}</span>
                </div>
                <div v-if="healthData.qdrant.ok" class="flex justify-between">
                  <span class="text-gray-400">{{ $t('admin.connection.vectorCount') }}</span>
                  <span class="text-gray-300">{{ healthData.qdrant.vector_count }}</span>
                </div>
                <div v-if="!healthData.qdrant.ok" class="text-red-400 text-xs mt-1 font-mono break-all">{{ healthData.qdrant.error }}</div>
                <div class="flex justify-between text-xs">
                  <span class="text-gray-500">{{ $t('admin.connection.url') }}</span>
                  <span class="text-gray-500 font-mono">{{ healthData.qdrant.url }}</span>
                </div>
              </div>
              <div class="bg-gray-800 rounded-xl p-4 space-y-1">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Ollama</p>
                <div class="flex justify-between">
                  <span class="text-gray-400">{{ $t('admin.connection.statusLabel') }}</span>
                  <span :class="healthData.ollama.ok ? 'text-green-400' : 'text-red-400'">{{ healthData.ollama.ok ? $t('admin.connection.ok') : $t('admin.connection.error') }}</span>
                </div>
                <template v-if="healthData.ollama.ok">
                  <div class="flex justify-between">
                    <span class="text-gray-400">{{ $t('admin.connection.embedModel') }}</span>
                    <span :class="healthData.ollama.embed_model_available ? 'text-green-400' : 'text-yellow-400'">
                      {{ healthData.ollama.embed_model }} {{ healthData.ollama.embed_model_available ? '✓' : $t('admin.connection.notInstalled') }}
                    </span>
                  </div>
                  <div class="flex justify-between">
                    <span class="text-gray-400">{{ $t('admin.connection.vlmModel') }}</span>
                    <span :class="healthData.ollama.vlm_model_available ? 'text-green-400' : 'text-yellow-400'">
                      {{ healthData.ollama.vlm_model }} {{ healthData.ollama.vlm_model_available ? '✓' : $t('admin.connection.notInstalled') }}
                    </span>
                  </div>
                  <div v-if="healthData.ollama.models?.length" class="mt-2">
                    <p class="text-xs text-gray-500 mb-1">{{ $t('admin.connection.installedModels') }}</p>
                    <div class="flex flex-wrap gap-1">
                      <span v-for="m in healthData.ollama.models" :key="m"
                        class="px-1.5 py-0.5 bg-gray-700 rounded text-xs text-gray-300 font-mono">{{ m }}</span>
                    </div>
                  </div>
                </template>
                <div v-if="!healthData.ollama.ok" class="text-red-400 text-xs mt-1 font-mono break-all">{{ healthData.ollama.error }}</div>
                <div class="flex justify-between text-xs">
                  <span class="text-gray-500">{{ $t('admin.connection.url') }}</span>
                  <span class="text-gray-500 font-mono">{{ healthData.ollama.url }}</span>
                </div>
              </div>
              <div class="bg-gray-800 rounded-xl p-4 space-y-2">
                <div class="flex justify-between items-center">
                  <div class="flex items-center gap-2">
                    <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">ComfyUI</p>
                    <button @click="reloadWorkflows" class="text-gray-600 hover:text-gray-400 text-xs" title="ワークフローを再読み込み">↺</button>
                  </div>
                  <span :class="healthData.comfyui?.ok ? 'text-green-400' : 'text-red-400'">
                    {{ healthData.comfyui?.ok ? $t('admin.connection.ok') : $t('admin.connection.comfyNotConnected') }}
                  </span>
                </div>
                <template v-if="healthData.comfyui?.ok">
                  <div class="flex justify-between">
                    <span class="text-gray-400">{{ $t('admin.connection.workflowCount') }}</span>
                    <span class="text-gray-300">{{ healthData.comfyui.workflows?.length ?? 0 }}</span>
                  </div>
                  <div v-if="healthData.comfyui.workflows?.length" class="flex flex-wrap gap-1 mt-1">
                    <span v-for="w in healthData.comfyui.workflows" :key="w"
                      class="px-1.5 py-0.5 bg-gray-700 rounded text-xs text-gray-300 font-mono">{{ w }}</span>
                  </div>
                </template>
                <div v-if="healthData.comfyui && !healthData.comfyui.ok" class="text-red-400 text-xs font-mono break-all">{{ healthData.comfyui.error }}</div>
                <div class="flex justify-between text-xs">
                  <span class="text-gray-500">{{ $t('admin.connection.url') }}</span>
                  <span class="text-gray-500 font-mono">{{ healthData.comfyui?.url }}</span>
                </div>
              </div>
            </template>
            <div v-else class="text-center text-red-400 py-8">{{ $t('admin.failed') }}</div>
          </div>

          <!-- ── Info tab ── -->
          <div v-if="adminTab === 'info'" class="space-y-4 text-sm">
            <div v-if="info">
              <div class="bg-gray-800 rounded-xl p-4 space-y-1">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Qdrant</p>
                <div class="flex justify-between"><span class="text-gray-400">{{ $t('admin.info.connection') }}</span>
                  <span :class="info.qdrant_ok?'text-green-400':'text-red-400'">{{ info.qdrant_ok ? $t('admin.connection.ok') : $t('admin.connection.error') }}</span></div>
                <div class="flex justify-between"><span class="text-gray-400">{{ $t('admin.info.registered') }}</span>
                  <span class="text-gray-200">{{ info.qdrant_doc_count }}</span></div>
              </div>
              <div v-if="aiStatus" class="bg-gray-800 rounded-xl p-4 space-y-1 mt-4">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">AI</p>
                <div class="flex justify-between"><span class="text-gray-400">Ollama</span>
                  <span :class="aiStatus.ollama_ok?'text-green-400':'text-red-400'">{{ aiStatus.ollama_ok ? $t('admin.connection.ok') : $t('admin.connection.error') }}</span></div>
                <div class="flex justify-between"><span class="text-gray-400">{{ $t('admin.info.vectorRegistered') }}</span>
                  <span class="text-gray-200">{{ aiStatus.vector_count }}</span></div>
              </div>
              <div class="bg-gray-800 rounded-xl p-4 space-y-1 mt-4">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ $t('admin.info.imagesDir') }}</p>
                <div class="flex justify-between gap-4"><span class="text-gray-400 flex-shrink-0 text-xs">Source</span>
                  <span class="text-gray-200 font-mono text-xs break-all text-right">{{ info.source_images_dir }}
                    <span :class="info.source_images_dir_exists?'text-green-400':'text-red-400'"> {{ info.source_images_dir_exists ? '✓' : '✗' }}</span>
                  </span></div>
                <div class="flex justify-between gap-4"><span class="text-gray-400 flex-shrink-0 text-xs">Generated</span>
                  <span class="text-gray-200 font-mono text-xs break-all text-right">{{ info.generated_images_dir }}
                    <span :class="info.generated_images_dir_exists?'text-green-400':'text-red-400'"> {{ info.generated_images_dir_exists ? '✓' : '✗' }}</span>
                  </span></div>
                <div class="flex justify-between"><span class="text-gray-400">{{ $t('admin.info.fileCount') }}</span>
                  <span class="text-gray-200">{{ info.image_files_found }}</span></div>
              </div>
              <div v-if="info.sample_files?.length" class="bg-gray-800 rounded-xl p-4 mt-4">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">{{ $t('admin.info.samples') }}</p>
                <ul class="space-y-1">
                  <li v-for="f in info.sample_files" :key="f" class="font-mono text-xs text-gray-300 truncate">{{ f }}</li>
                </ul>
              </div>
            </div>
            <div v-else class="text-center text-gray-500 py-8">{{ $t('admin.loading') }}</div>
          </div>

        </div>
      </div>
    </div>
  </Teleport>
</template>

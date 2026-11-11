<script setup>
/**
 * LoRA comparison sheet: LoRA files down, strengths across, one seed.
 *
 * ranbell builds the ComfyUI graph itself (core nodes only) and draws every
 * cell through the job scheduler. The LoRA files are read from ComfyUI's
 * loras folder — ranbell never places or moves them.
 */
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: { type: Boolean, default: false },
  // Where the screen was opened from: { sha256 } (a gallery picture) or
  // { bucket_id, slug, trigger } (a LoRA bucket). Applied to a new sheet.
  prefill: { type: Object, default: null },
})
const emit = defineEmits(['close', 'toast'])
const { t } = useI18n()

const LS_KEY = 'ranbell.loraSheet.last'

const options = ref(null)       // /api/lora/sheet-options
const sheets = ref([])
const current = ref(null)       // full sheet view, or null while making one
const busy = ref(false)
const confirmDelete = ref(false)
const loraFilter = ref('')
const newTrigger = ref('')
const strengthText = ref('')
const showSampling = ref(false)
const lightbox = ref(null)      // { r, c }
let pollTimer = null

const form = ref(blankForm())

function blankForm() {
  return {
    name: '', target_model: 'krea2', models: { unet: '', clip: '', vae: '' },
    prompt: '', seed: randomSeed(), width: 1024, height: 1024, steps: 8, cfg: 1.0,
    sampler: 'euler', scheduler: 'simple', rows: [], no_trigger_column: false, bucket_id: '',
  }
}
function randomSeed() { return Math.floor(Math.random() * 2 ** 48) }

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  if (!resp.ok) {
    let detail = ''
    try { detail = (await resp.json()).detail } catch {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || resp.status))
  }
  return resp.json()
}
function toast(msg, type = 'info') { emit('toast', { msg, type }) }

// ── Making a sheet ────────────────────────────────────────────────────────────

const strengths = computed(() => strengthText.value.split(/[\s,]+/).filter(Boolean)
  .map(Number).filter(v => Number.isFinite(v)))

const loraGroups = computed(() => {
  const q = loraFilter.value.trim().toLowerCase()
  const groups = {}
  for (const f of options.value?.loras || []) {
    if (q && !f.toLowerCase().includes(q)) continue
    const i = f.lastIndexOf('/')
    const dir = i >= 0 ? f.slice(0, i) : ''
    ;(groups[dir] ||= []).push(f)
  }
  return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b))
})

// The count the backend will draw: rows sharing a trigger share the 0 column.
const pictureCount = computed(() => {
  const rows = form.value.rows
  if (!rows.length) return 0
  const nonzero = strengths.value.filter(s => s !== 0).length
  const base = strengths.value.includes(0) ? new Set(rows.map(r => r.trigger.trim())).size : 0
  return base + rows.length * (nonzero + (form.value.no_trigger_column ? 1 : 0))
})
const estimate = computed(() => {
  const s = options.value?.seconds_per_picture
  if (!s || !pictureCount.value) return ''
  const total = Math.round(s * pictureCount.value)
  return total < 90 ? t('loraSheet.estimateSec', { n: total }) : t('loraSheet.estimateMin', { n: Math.round(total / 60) })
})
const canCreate = computed(() => form.value.rows.length && form.value.prompt.trim()
  && form.value.models.unet && form.value.models.clip && form.value.models.vae
  && strengths.value.length && !busy.value)

function addRow(lora) {
  if (form.value.rows.some(r => r.lora === lora)) return
  form.value.rows.push({ lora, trigger: newTrigger.value.trim(), label: '' })
}
function addFolder(files) { files.forEach(addRow) }
function moveRow(i, d) {
  const rows = form.value.rows, j = i + d
  if (j < 0 || j >= rows.length) return
  ;[rows[i], rows[j]] = [rows[j], rows[i]]
}
function shortName(f) { return f.slice(f.lastIndexOf('/') + 1).replace(/\.safetensors$/, '') }

function remember() {
  const f = form.value
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      models: f.models, width: f.width, height: f.height, steps: f.steps, cfg: f.cfg,
      sampler: f.sampler, scheduler: f.scheduler, strengths: strengthText.value,
    }))
  } catch {}
}
function recall() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || 'null') } catch { return null }
}

async function startNew() {
  current.value = null
  confirmDelete.value = false
  const f = blankForm()
  const tgt = (options.value?.targets || [])[0]
  if (tgt) Object.assign(f, { target_model: tgt.id }, tgt.defaults)
  const last = recall()
  if (last) {
    Object.assign(f, { width: last.width, height: last.height, steps: last.steps, cfg: last.cfg,
      sampler: last.sampler, scheduler: last.scheduler })
    f.models = { ...f.models, ...(last.models || {}) }
  }
  strengthText.value = last?.strengths || (options.value?.default_strengths || [0, 0.25, 0.5, 0.75, 1, 1.25]).join(', ')
  newTrigger.value = ''
  loraFilter.value = ''
  const p = props.prefill
  if (p?.sha256) {
    try {
      const pre = await api(`/api/lora/sheet-prefill/${p.sha256}`)
      for (const k of ['prompt', 'seed', 'width', 'height', 'steps', 'cfg', 'sampler', 'scheduler']) {
        if (pre[k] !== undefined) f[k] = pre[k]
      }
      f.models = { ...f.models, ...(pre.models || {}) }
    } catch (e) { toast(e.message, 'error') }
  }
  if (p?.bucket_id) {
    f.bucket_id = p.bucket_id
    newTrigger.value = p.trigger || ''
    loraFilter.value = p.slug || ''
  }
  // Drop chosen files ComfyUI does not have (a remembered file since removed).
  const o = options.value
  if (o?.comfy) {
    for (const [k, list] of [['unet', o.unets], ['clip', o.clips], ['vae', o.vaes]]) {
      if (f.models[k] && !list.includes(f.models[k])) f.models[k] = ''
    }
  }
  form.value = f
}

async function create() {
  busy.value = true
  try {
    const f = form.value
    const body = { ...f, strengths: strengths.value, seed: f.seed === '' ? null : Number(f.seed) }
    const s = await api('/api/lora/sheets', { method: 'POST', body })
    remember()
    toast(t('loraSheet.queued', { n: s.pictures }), 'info')
    current.value = s
    await loadList()
    poll()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}

// ── A sheet ───────────────────────────────────────────────────────────────────

async function loadList() { sheets.value = (await api('/api/lora/sheets')).sheets || [] }
async function loadOptions() { options.value = await api('/api/lora/sheet-options') }

async function openSheet(id) {
  confirmDelete.value = false
  current.value = await api(`/api/lora/sheets/${id}`)
  poll()
}

function poll() {
  clearTimeout(pollTimer)
  const s = current.value
  if (!s || !(s.drawing || false)) return
  pollTimer = setTimeout(async () => {
    if (!props.show || current.value?.id !== s.id) return
    try {
      const fresh = await api(`/api/lora/sheets/${s.id}`)
      if (current.value?.id !== s.id) return
      current.value = fresh
      if (!fresh.drawing) {
        toast(fresh.errors ? t('loraSheet.doneWithErrors', { n: fresh.done, e: fresh.errors })
          : t('loraSheet.done', { n: fresh.done }), fresh.errors ? 'error' : 'success')
        loadList()
        return
      }
    } catch {}
    poll()
  }, 2500)
}

async function drawRest() {
  busy.value = true
  try {
    current.value = await api(`/api/lora/sheets/${current.value.id}/render`, { method: 'POST' })
    poll()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}

async function duplicateNewSeed() {
  busy.value = true
  try {
    current.value = await api(`/api/lora/sheets/${current.value.id}/duplicate`, { method: 'POST', body: {} })
    toast(t('loraSheet.queued', { n: current.value.pictures }), 'info')
    await loadList()
    poll()
  } catch (e) { toast(e.message, 'error') } finally { busy.value = false }
}

async function deleteSheet() {
  if (!confirmDelete.value) { confirmDelete.value = true; return }
  try {
    await api(`/api/lora/sheets/${current.value.id}`, { method: 'DELETE' })
    current.value = null
    confirmDelete.value = false
    await loadList()
    if (sheets.value.length) await openSheet(sheets.value[0].id)
    else await startNew()
  } catch (e) { toast(e.message, 'error') }
}

function render(key) { return current.value?.renders?.[key] || {} }
function colLabel(col) {
  if (!col.trigger_on) return t('loraSheet.noTriggerCol', { s: col.strength })
  return col.strength === 0 ? t('loraSheet.baseCol') : String(col.strength)
}
function rowLabel(row) { return row.label || shortName(row.lora) }

// ── Lightbox: arrows move along the grid ─────────────────────────────────────
const lbRender = computed(() => {
  const lb = lightbox.value
  if (!lb || !current.value) return null
  return render(current.value.grid[lb.r]?.[lb.c])
})
function openCell(r, c) { if (render(current.value.grid[r][c]).sha256) lightbox.value = { r, c } }
function onKey(e) {
  const lb = lightbox.value
  if (!lb || !current.value) return
  const rows = current.value.grid.length, cols = current.value.grid[0]?.length || 0
  const move = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0] }[e.key]
  if (e.key === 'Escape') { lightbox.value = null; e.stopPropagation(); return }
  if (!move) return
  e.preventDefault()
  lightbox.value = { r: (lb.r + move[0] + rows) % rows, c: (lb.c + move[1] + cols) % cols }
}

watch(() => props.show, async (open) => {
  if (!open) { clearTimeout(pollTimer); lightbox.value = null; return }
  window.addEventListener('keydown', onKey, true)
  try {
    await Promise.all([loadOptions(), loadList()])
    if (props.prefill || !sheets.value.length) await startNew()
    else await openSheet(sheets.value[0].id)
  } catch (e) { toast(e.message, 'error') }
}, { immediate: true })
watch(() => props.show, (open) => { if (!open) window.removeEventListener('keydown', onKey, true) })

onBeforeUnmount(() => { clearTimeout(pollTimer); window.removeEventListener('keydown', onKey, true) })
</script>

<template>
  <div v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    @click.self="emit('close')">
    <div role="dialog" aria-modal="true" aria-labelledby="lora-sheet-title"
      class="relative w-full max-w-7xl h-[92vh] flex flex-col md:flex-row rounded-2xl overflow-hidden
             bg-slate-950/95 border border-amber-500/30 shadow-2xl text-gray-200">
      <button type="button" :aria-label="t('lora.close')" @click="emit('close')"
        class="absolute top-3 right-3 z-10 w-8 h-8 rounded-full bg-gray-800 hover:bg-gray-700 text-gray-300">✕</button>

      <!-- Sheet list -->
      <aside class="w-full md:w-60 shrink-0 border-b md:border-b-0 md:border-r border-gray-800 p-4 flex flex-col gap-3">
        <h3 id="lora-sheet-title" class="font-bold text-amber-200">{{ t('loraSheet.title') }}</h3>
        <p class="text-[11px] text-gray-500 leading-snug">{{ t('loraSheet.subtitle') }}</p>
        <button type="button" @click="startNew"
          class="px-2 py-1.5 rounded-lg bg-amber-700/70 hover:bg-amber-600 text-xs">＋ {{ t('loraSheet.new') }}</button>
        <div class="flex-1 overflow-y-auto flex flex-col gap-1.5 min-h-0">
          <button v-for="s in sheets" :key="s.id" type="button" @click="openSheet(s.id)"
            class="flex items-center gap-2 p-1.5 rounded-lg text-left border"
            :class="current?.id === s.id ? 'border-amber-500/60 bg-amber-950/40' : 'border-transparent hover:bg-gray-900'">
            <img v-if="s.cover" :src="`/api/thumbnails/${s.cover}.webp`" class="w-9 h-9 rounded-lg object-cover" loading="lazy" />
            <span v-else class="w-9 h-9 rounded-lg bg-gray-800 shrink-0" />
            <span class="min-w-0">
              <span class="block text-xs truncate">{{ s.name }}</span>
              <span class="block text-[10px] text-gray-500">
                {{ s.rows }}×{{ s.columns }} · {{ s.done }}/{{ s.pictures }}
                <span v-if="s.drawing" class="text-amber-300 motion-safe:animate-pulse">●</span>
                <span v-if="s.errors" class="text-red-400">⚠{{ s.errors }}</span>
              </span>
            </span>
          </button>
        </div>
      </aside>

      <!-- New sheet -->
      <section v-if="!current" class="flex-1 min-w-0 overflow-y-auto p-4 pr-14 flex flex-col gap-4">
        <p v-if="options && !options.comfy" class="text-xs text-amber-300">
          ⚠ {{ t('loraSheet.comfyDown', { error: options.comfy_error || '' }) }}
        </p>

        <div class="grid md:grid-cols-3 gap-2">
          <label v-for="[k, list, lbl] in [['unet', options?.unets, 'modelUnet'], ['clip', options?.clips, 'modelClip'], ['vae', options?.vaes, 'modelVae']]"
            :key="k" class="flex flex-col gap-1 text-[11px] text-gray-400">
            {{ t(`loraSheet.${lbl}`) }}
            <select v-model="form.models[k]" class="px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs text-gray-200">
              <option value="">—</option>
              <option v-for="f in list || []" :key="f" :value="f">{{ f }}</option>
              <option v-if="form.models[k] && !(list || []).includes(form.models[k])" :value="form.models[k]">{{ form.models[k] }}</option>
            </select>
          </label>
        </div>

        <label class="flex flex-col gap-1 text-[11px] text-gray-400">
          {{ t('loraSheet.prompt') }}
          <textarea v-model="form.prompt" rows="3" :placeholder="t('loraSheet.promptHint')"
            class="px-2 py-1.5 rounded-lg bg-gray-900 border border-gray-700 text-sm text-gray-200" />
        </label>

        <div class="flex flex-wrap items-end gap-3">
          <label class="flex flex-col gap-1 text-[11px] text-gray-400">
            {{ t('loraSheet.seed') }}
            <span class="flex gap-1">
              <input v-model.number="form.seed" type="number" min="0"
                class="w-44 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs font-mono" />
              <button type="button" class="px-2 rounded-lg bg-gray-800 hover:bg-gray-700" :title="t('loraSheet.reseed')"
                @click="form.seed = randomSeed()">🎲</button>
            </span>
          </label>
          <label class="flex flex-col gap-1 text-[11px] text-gray-400">
            {{ t('loraSheet.size') }}
            <span class="flex items-center gap-1">
              <input v-model.number="form.width" type="number" step="16" class="w-20 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" />
              ×
              <input v-model.number="form.height" type="number" step="16" class="w-20 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" />
            </span>
          </label>
          <label class="flex flex-col gap-1 text-[11px] text-gray-400 flex-1 min-w-48">
            {{ t('loraSheet.strengths') }}
            <input v-model="strengthText" class="px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs font-mono" />
          </label>
          <label class="flex items-center gap-1.5 text-[11px] text-gray-300" :title="t('loraSheet.noTriggerHelp')">
            <input v-model="form.no_trigger_column" type="checkbox" /> {{ t('loraSheet.noTriggerColumn') }}
          </label>
          <button type="button" class="text-[11px] text-gray-400 hover:text-gray-200 underline" @click="showSampling = !showSampling">
            {{ t('loraSheet.sampling') }} {{ showSampling ? '▴' : '▾' }}
          </button>
        </div>
        <div v-if="showSampling" class="flex flex-wrap gap-3 text-[11px] text-gray-400">
          <label class="flex flex-col gap-1">steps
            <input v-model.number="form.steps" type="number" min="1" class="w-16 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" /></label>
          <label class="flex flex-col gap-1">cfg
            <input v-model.number="form.cfg" type="number" step="0.1" class="w-16 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" /></label>
          <label class="flex flex-col gap-1">sampler
            <select v-model="form.sampler" class="px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs text-gray-200">
              <option v-for="s in (options?.samplers?.length ? options.samplers : [form.sampler])" :key="s" :value="s">{{ s }}</option>
            </select></label>
          <label class="flex flex-col gap-1">scheduler
            <select v-model="form.scheduler" class="px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs text-gray-200">
              <option v-for="s in (options?.schedulers?.length ? options.schedulers : [form.scheduler])" :key="s" :value="s">{{ s }}</option>
            </select></label>
          <p class="self-end text-gray-500">{{ t('loraSheet.samplingHelp') }}</p>
        </div>

        <!-- LoRA rows -->
        <div class="grid md:grid-cols-2 gap-3 min-h-0">
          <div class="flex flex-col gap-1.5 min-h-0">
            <div class="flex gap-1.5">
              <input v-model="loraFilter" :placeholder="t('loraSheet.filter')"
                class="flex-1 min-w-0 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" />
              <input v-model="newTrigger" :placeholder="t('loraSheet.triggerForNew')"
                class="w-40 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs font-mono" />
            </div>
            <div class="max-h-72 overflow-y-auto rounded-lg border border-gray-800 p-1.5 text-xs">
              <p v-if="!loraGroups.length" class="text-gray-500 p-2">{{ t('loraSheet.noLoras') }}</p>
              <div v-for="[dir, files] in loraGroups" :key="dir" class="mb-1.5">
                <div class="flex items-center justify-between text-[10px] text-gray-500 px-1">
                  <span>📁 {{ dir || t('loraSheet.rootFolder') }}</span>
                  <button v-if="files.length > 1" type="button" class="text-amber-300 hover:text-amber-100"
                    @click="addFolder(files)">{{ t('loraSheet.addAll', { n: files.length }) }}</button>
                </div>
                <button v-for="f in files" :key="f" type="button" @click="addRow(f)"
                  class="block w-full text-left px-2 py-0.5 rounded hover:bg-gray-800 truncate"
                  :class="form.rows.some(r => r.lora === f) ? 'text-amber-300' : 'text-gray-300'">{{ shortName(f) }}</button>
              </div>
            </div>
          </div>
          <div class="flex flex-col gap-1.5">
            <p class="text-[11px] text-gray-400">{{ t('loraSheet.rows', { n: form.rows.length }) }}</p>
            <p v-if="!form.rows.length" class="text-[11px] text-gray-500">{{ t('loraSheet.rowsHint') }}</p>
            <div v-for="(r, i) in form.rows" :key="r.lora" class="flex items-center gap-1.5 text-xs">
              <span class="flex-1 min-w-0 truncate" :title="r.lora">{{ shortName(r.lora) }}</span>
              <input v-model="r.trigger" :placeholder="t('loraSheet.trigger')"
                class="w-36 px-2 py-0.5 rounded-lg bg-gray-900 border border-gray-700 text-xs font-mono" />
              <button type="button" class="text-gray-500 hover:text-gray-200" @click="moveRow(i, -1)">↑</button>
              <button type="button" class="text-gray-500 hover:text-gray-200" @click="moveRow(i, 1)">↓</button>
              <button type="button" class="text-gray-500 hover:text-red-300" @click="form.rows.splice(i, 1)">✕</button>
            </div>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-3 border-t border-gray-800 pt-3">
          <input v-model="form.name" :placeholder="t('loraSheet.name')"
            class="w-56 px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" />
          <span class="text-xs text-gray-400">
            {{ t('loraSheet.count', { n: pictureCount }) }}<template v-if="estimate"> · {{ estimate }}</template>
          </span>
          <button type="button" :disabled="!canCreate" @click="create"
            class="ml-auto px-4 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-sm text-black font-semibold disabled:opacity-40">
            {{ t('loraSheet.start') }}
          </button>
        </div>
      </section>

      <!-- A sheet -->
      <section v-else class="flex-1 min-w-0 flex flex-col min-h-0">
        <div class="p-4 pr-14 border-b border-gray-800 flex flex-col gap-1.5">
          <div class="flex flex-wrap items-center gap-2">
            <h4 class="font-semibold">{{ current.name }}</h4>
            <span class="text-[11px] text-gray-500">{{ current.done }}/{{ current.pictures }}</span>
            <span v-if="current.drawing" class="text-[11px] text-amber-300 motion-safe:animate-pulse">{{ t('loraSheet.drawing') }}</span>
            <div class="ml-auto flex flex-wrap gap-1.5">
              <button v-if="!current.drawing && (current.pending || current.errors)" type="button" :disabled="busy" @click="drawRest"
                class="px-2.5 py-1 rounded-lg bg-amber-700/70 hover:bg-amber-600 text-xs">{{ t('loraSheet.drawRest') }}</button>
              <button type="button" :disabled="busy" @click="duplicateNewSeed"
                class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">🎲 {{ t('loraSheet.newSeed') }}</button>
              <a :href="`/api/lora/sheets/${current.id}/grid.png`" download
                class="px-2.5 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 text-xs">⬇ {{ t('loraSheet.savePng') }}</a>
              <button type="button" @click="deleteSheet"
                class="px-2.5 py-1 rounded-lg text-xs"
                :class="confirmDelete ? 'bg-red-700 text-white' : 'bg-gray-800 hover:bg-gray-700'">
                {{ confirmDelete ? t('loraSheet.confirmDelete') : t('loraSheet.delete') }}
              </button>
            </div>
          </div>
          <p class="text-[11px] text-gray-500 truncate" :title="current.prompt">
            seed {{ current.seed }} · {{ current.models.unet }} · {{ current.steps }} steps · cfg {{ current.cfg }}
            · {{ current.sampler }}/{{ current.scheduler }} · {{ current.width }}×{{ current.height }} — {{ current.prompt }}
          </p>
        </div>

        <div class="flex-1 overflow-auto p-3">
          <table class="border-separate border-spacing-1">
            <thead>
              <tr>
                <th class="sticky top-0 left-0 z-20 bg-slate-950"></th>
                <th v-for="(col, c) in current.columns_spec" :key="c"
                  class="sticky top-0 z-10 bg-slate-950 text-xs font-semibold text-amber-200 px-1 py-1">{{ colLabel(col) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, r) in current.rows" :key="r">
                <th class="sticky left-0 z-10 bg-slate-950 text-left align-top text-xs font-normal pr-2 max-w-40">
                  <span class="block break-all" :title="row.lora">{{ rowLabel(row) }}</span>
                  <span v-if="row.trigger" class="block text-[10px] text-gray-500 font-mono break-all">{{ row.trigger }}</span>
                </th>
                <td v-for="(key, c) in current.grid[r]" :key="c" class="p-0">
                  <button v-if="render(key).sha256" type="button" @click="openCell(r, c)" class="block">
                    <img :src="`/api/thumbnails/${render(key).sha256}.webp`" loading="lazy"
                      class="w-44 rounded-lg object-cover bg-gray-900"
                      :style="{ aspectRatio: `${current.width} / ${current.height}` }" />
                  </button>
                  <div v-else class="w-44 rounded-lg flex items-center justify-center text-[11px] p-2 text-center"
                    :style="{ aspectRatio: `${current.width} / ${current.height}` }"
                    :class="render(key).status === 'error' ? 'bg-red-950/50 text-red-300' : 'bg-gray-900 text-gray-500 motion-safe:animate-pulse'"
                    :title="render(key).error || ''">
                    {{ render(key).status === 'error' ? `⚠ ${render(key).error}`.slice(0, 120) : t('loraSheet.waiting') }}
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Lightbox -->
      <div v-if="lightbox && lbRender?.sha256" class="absolute inset-0 z-30 bg-black/90 flex flex-col items-center justify-center p-4"
        @click.self="lightbox = null">
        <p class="text-sm text-amber-200 mb-2">
          {{ rowLabel(current.rows[lightbox.r]) }} · {{ colLabel(current.columns_spec[lightbox.c]) }}
          <span class="text-gray-500 text-xs ml-2">{{ t('loraSheet.arrows') }}</span>
        </p>
        <img :src="`/api/originals/${lbRender.sha256}`" class="max-w-full max-h-[80vh] object-contain rounded-lg" />
      </div>
    </div>
  </div>
</template>

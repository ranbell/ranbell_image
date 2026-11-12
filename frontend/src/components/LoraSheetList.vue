<script setup>
/**
 * The LoRA sheets, searchable and grouped (by LoRA, by base model, by day).
 * Ticking two or more offers to stack them, or to delete them together.
 */
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  sheets: { type: Array, required: true },
  currentId: { type: String, default: '' },
  selected: { type: Array, default: () => [] },
})
const emit = defineEmits(['open', 'new', 'compare', 'delete-many', 'update:selected'])
const { t, locale } = useI18n()

const LS_GROUP = 'ranbell.loraSheet.groupBy'
const query = ref('')
const groupBy = ref(readGroup())
const collapsed = ref(new Set())
const confirmDelete = ref(false)

function readGroup() {
  try { return localStorage.getItem(LS_GROUP) || 'lora' } catch { return 'lora' }
}
function setGroup(g) {
  groupBy.value = g
  try { localStorage.setItem(LS_GROUP, g) } catch {}
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.sheets
  return props.sheets.filter(s => [s.name, s.unet, s.prompt, ...(s.loras || [])]
    .some(v => (v || '').toLowerCase().includes(q)))
})

function dayOf(ts) {
  const d = new Date((ts || 0) * 1000)
  return d.toLocaleDateString(locale.value, { year: 'numeric', month: 'short', day: 'numeric' })
}
const groups = computed(() => {
  const out = new Map()
  for (const s of filtered.value) {
    const keys = groupBy.value === 'model' ? [s.unet || '—']
      : groupBy.value === 'day' ? [dayOf(s.created_at)]
      : (s.loras?.length ? s.loras : ['—'])
    // A sheet with several LoRAs is listed under the first; the rest are in its name.
    const k = keys[0]
    if (!out.has(k)) out.set(k, [])
    out.get(k).push(s)
  }
  const entries = [...out.entries()]
  if (groupBy.value !== 'day') entries.sort(([a], [b]) => a.localeCompare(b))
  return entries
})

function toggleGroup(k) {
  const c = new Set(collapsed.value)
  c.has(k) ? c.delete(k) : c.add(k)
  collapsed.value = c
}
function toggle(id) {
  const sel = props.selected.includes(id) ? props.selected.filter(x => x !== id) : [...props.selected, id]
  confirmDelete.value = false
  emit('update:selected', sel)
}
function deleteMany() {
  if (!confirmDelete.value) { confirmDelete.value = true; return }
  confirmDelete.value = false
  emit('delete-many', [...props.selected])
}
const seedTail = s => String(s.seed ?? '').slice(-4)
</script>

<template>
  <aside class="w-full md:w-72 shrink-0 border-b md:border-b-0 md:border-r border-gray-800 p-3 flex flex-col gap-2 min-h-0 max-h-[40vh] md:max-h-none">
    <h3 id="lora-sheet-title" class="font-bold text-amber-200">{{ t('loraSheet.title') }}</h3>
    <p class="text-[11px] text-gray-500 leading-snug">{{ t('loraSheet.subtitle') }}</p>
    <button type="button" @click="emit('new')"
      class="px-2 py-1.5 rounded-lg bg-amber-700/70 hover:bg-amber-600 text-xs">＋ {{ t('loraSheet.new') }}</button>

    <input v-model="query" type="search" :placeholder="t('loraSheet.search')"
      class="px-2 py-1 rounded-lg bg-gray-900 border border-gray-700 text-xs" />
    <div class="flex gap-1 text-[10px]">
      <button v-for="g in ['lora', 'model', 'day']" :key="g" type="button" @click="setGroup(g)"
        class="flex-1 px-1.5 py-0.5 rounded-md"
        :class="groupBy === g ? 'bg-amber-800/60 text-amber-100' : 'bg-gray-900 text-gray-400 hover:bg-gray-800'">
        {{ t(`loraSheet.groupBy.${g}`) }}</button>
    </div>

    <div v-if="selected.length" class="flex flex-wrap items-center gap-1.5 text-[11px] p-1.5 rounded-lg bg-gray-900">
      <span class="text-gray-400">{{ t('loraSheet.selected', { n: selected.length }) }}</span>
      <button v-if="selected.length >= 2" type="button" @click="emit('compare')"
        class="px-2 py-0.5 rounded-md bg-sky-700 hover:bg-sky-600 text-white">⇅ {{ t('loraSheet.stackCompare', { n: selected.length }) }}</button>
      <button type="button" @click="deleteMany" class="px-2 py-0.5 rounded-md"
        :class="confirmDelete ? 'bg-red-700 text-white' : 'bg-gray-800 hover:bg-gray-700'">
        {{ confirmDelete ? t('loraSheet.confirmDeleteMany', { n: selected.length }) : t('loraSheet.delete') }}</button>
      <button type="button" class="ml-auto text-gray-500 hover:text-gray-200"
        @click="emit('update:selected', []); confirmDelete = false">{{ t('loraSheet.clearSelection') }}</button>
    </div>

    <div class="flex-1 overflow-y-auto min-h-0 flex flex-col gap-1">
      <p v-if="!filtered.length" class="text-[11px] text-gray-500 p-2">{{ t('loraSheet.noSheets') }}</p>
      <div v-for="[k, list] in groups" :key="k">
        <button type="button" @click="toggleGroup(k)"
          class="w-full flex items-center gap-1 px-1 py-0.5 text-[10px] text-gray-400 hover:text-gray-200">
          <span>{{ collapsed.has(k) ? '▸' : '▾' }}</span>
          <span class="truncate">{{ k }}</span>
          <span class="ml-auto text-gray-600">{{ list.length }}</span>
        </button>
        <div v-if="!collapsed.has(k)" class="flex flex-col gap-1">
          <div v-for="s in list" :key="s.id" class="flex items-center gap-1.5 p-1 rounded-lg border"
            :class="currentId === s.id ? 'border-amber-500/60 bg-amber-950/40' : 'border-transparent hover:bg-gray-900'">
            <input type="checkbox" class="shrink-0" :checked="selected.includes(s.id)" @change="toggle(s.id)"
              :aria-label="t('loraSheet.select', { name: s.name })" />
            <button type="button" class="flex items-center gap-2 min-w-0 flex-1 text-left" @click="emit('open', s.id)">
              <img v-if="s.cover" :src="`/api/thumbnails/${s.cover}.webp`" class="w-10 h-10 rounded-lg object-cover shrink-0" loading="lazy" />
              <span v-else class="w-10 h-10 rounded-lg bg-gray-800 shrink-0" />
              <span class="min-w-0">
                <span class="block text-xs truncate" :title="s.name">{{ s.name }}</span>
                <span class="block text-[10px] text-gray-500 truncate">{{ s.unet }} · seed …{{ seedTail(s) }}</span>
                <span class="block text-[10px] text-gray-500">
                  {{ s.rows }}×{{ s.columns }} · {{ s.done }}/{{ s.pictures }}
                  <span v-if="s.drawing" class="text-amber-300 motion-safe:animate-pulse">●</span>
                  <span v-if="s.errors" class="text-red-400">⚠{{ s.errors }}</span>
                </span>
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

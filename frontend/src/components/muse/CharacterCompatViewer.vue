<script setup>
/*
 * Read-only viewer over `/api/admin/character-compat/matrix` — every pairwise
 * chemistry score at once, sorted by strength. The per-pair number already
 * existed (CharacterDossier's chemistry cards, the duet picker's hint), but
 * nothing let you see the whole roster's relationships at a glance.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'toast'])
const { t, locale } = useI18n()

const loading = ref(false)
const characters = ref([])
const pairs = ref([])
const filterText = ref('')

const isJa = computed(() => String(locale.value).startsWith('ja'))
const nameById = computed(() => {
  const m = new Map()
  for (const c of characters.value) m.set(c.id, isJa.value ? (c.name_ja || c.name) : (c.name || c.name_ja))
  return m
})

const rows = computed(() => {
  const q = filterText.value.trim().toLowerCase()
  return pairs.value
    .map(p => ({
      ...p,
      nameA: nameById.value.get(p.a) || p.a,
      nameB: nameById.value.get(p.b) || p.b,
    }))
    .filter(p => !q || p.nameA.toLowerCase().includes(q) || p.nameB.toLowerCase().includes(q))
    .sort((x, y) => y.score - x.score)
})

async function load() {
  loading.value = true
  try {
    const r = await fetch('/api/admin/character-compat/matrix')
    if (!r.ok) throw new Error(`${r.status}`)
    const data = await r.json()
    characters.value = data.characters || []
    pairs.value = data.pairs || []
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (val) => { if (val) load() }, { immediate: true })

function tierColor(tier) {
  if (tier === 'best_friend') return 'bg-rose-500/80'
  if (tier === 'close') return 'bg-pink-500/70'
  return 'bg-slate-600/70'
}
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
    @click.self="emit('close')"
  >
    <div class="relative w-full max-w-2xl max-h-[85vh] flex flex-col rounded-2xl overflow-hidden
                bg-slate-900/95 border border-rose-500/30 shadow-2xl">
      <header class="flex items-center gap-2 px-4 py-3 border-b border-rose-500/20 shrink-0">
        <span class="text-lg">💞</span>
        <h2 class="sb-display text-sm text-rose-200 flex-1">{{ t('characters.compat.viewerTitle') }}</h2>
        <button type="button" class="text-rose-400/70 hover:text-rose-300" @click="load" :disabled="loading">↺</button>
        <button type="button" class="sb-icon-btn" :title="t('muse.close')" @click="emit('close')">✕</button>
      </header>

      <div class="px-4 py-2 border-b border-rose-500/10 shrink-0">
        <input
          v-model="filterText"
          type="text"
          class="sb-input w-full text-xs"
          :placeholder="t('characters.compat.filterPlaceholder')"
        />
      </div>

      <div class="flex-1 overflow-y-auto px-4 py-2">
        <p v-if="loading" class="text-xs text-rose-400/70 py-8 text-center">{{ t('characters.diary.loading') }}</p>
        <p v-else-if="!rows.length" class="text-xs text-rose-400/60 py-8 text-center">
          {{ t('characters.compat.empty') }}
        </p>
        <table v-else class="w-full text-xs">
          <tbody>
            <tr
              v-for="p in rows"
              :key="`${p.a}:${p.b}`"
              class="border-b border-white/5 hover:bg-white/5"
            >
              <td class="py-1.5 pr-2 text-rose-100/90 whitespace-nowrap">{{ p.nameA }} × {{ p.nameB }}</td>
              <td class="py-1.5 pr-2">
                <span class="px-1.5 py-0.5 rounded-full text-[9px] font-bold text-white" :class="tierColor(p.tier)">
                  {{ t(`characters.chemistryTier.${p.tier}`) }}
                </span>
              </td>
              <td class="py-1.5 pr-2 text-right font-mono text-rose-300/80">{{ Math.round(p.score * 100) }}%</td>
              <td class="py-1.5 text-right text-[10px] text-rose-400/60">
                {{ p.co_appearances ? t('characters.compat.coAppearances', { n: p.co_appearances }) : '' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

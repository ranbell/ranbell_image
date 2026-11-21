<script setup>
/**
 * Pre-training diagnosis of one bucket (backend `lora/diagnose.py`):
 * what the trigger will absorb besides its targets, how one-sided the set
 * was shot, and which pictures are the same picture twice.
 *
 * Advice only — nothing here blocks an export. Fetched when unfolded and
 * when asked; captions and tags change under it, so it is never cached.
 */
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  bucketId: { type: String, required: true },
  absorbOk: { type: Array, default: () => [] },
  // Bumped by the parent after a change that alters the reading (a caption,
  // a picture removed): an open panel reads again.
  version: { type: Number, default: 0 },
})
const emit = defineEmits(['absorb', 'remove', 'focus', 'risks'])
const { t, te } = useI18n()

const open = ref(false)
const loading = ref(false)
const error = ref('')
const data = ref(null)
let seq = 0

const risks = computed(() => (data.value?.burn_in || []).filter(r => r.status === 'risk'))
const intended = computed(() => (data.value?.burn_in || []).filter(r => r.status === 'intended'))
const skewed = computed(() => (data.value?.skew || []).filter(r => r.skewed).length)
const cov = computed(() => data.value?.coverage || {})

async function load() {
  const mine = ++seq
  const id = props.bucketId
  loading.value = true
  error.value = ''
  try {
    const resp = await fetch(`/api/lora/buckets/${id}/diagnosis`)
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.status)
    const body = await resp.json()
    if (mine !== seq) return
    data.value = body
    emit('risks', body.burn_in.filter(r => r.status === 'risk').length)
  } catch (e) {
    if (mine === seq) error.value = String(e.message || e)
  } finally {
    if (mine === seq) loading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) load()
}

watch(() => props.bucketId, () => {
  seq++
  data.value = null
  error.value = ''
  emit('risks', null)
  if (open.value) load()
})
watch(() => [props.version, props.absorbOk.join('\n')], () => { if (open.value) load() })

function pct(v) { return `${Math.round((v || 0) * 100)}%` }
function slotLabel(slot) {
  const key = `lora.diag.slot.${slot}`
  return te(key) ? t(key) : slot
}
function setAbsorb(tag, on) {
  const next = props.absorbOk.filter(x => x !== tag)
  if (on) next.push(tag)
  emit('absorb', next)
}
</script>

<template>
  <div class="border-b border-gray-800">
    <button type="button" @click="toggle" :aria-expanded="open"
      class="w-full px-4 py-2 flex items-center gap-2 text-xs text-left hover:bg-gray-900/60">
      <span class="text-gray-500">{{ open ? '▾' : '▸' }}</span>
      <span class="font-semibold text-amber-200">{{ t('lora.diag.title') }}</span>
      <template v-if="data">
        <span v-if="risks.length" class="px-2 rounded-full bg-red-900/60 text-red-200">{{ t('lora.diag.risksN', { n: risks.length }) }}</span>
        <span v-if="skewed" class="px-2 rounded-full bg-amber-900/60 text-amber-200">{{ t('lora.diag.skewedN', { n: skewed }) }}</span>
        <span v-if="data.duplicates.length" class="px-2 rounded-full bg-sky-900/60 text-sky-200">{{ t('lora.diag.dupsN', { n: data.duplicates.length }) }}</span>
        <span v-if="!risks.length && !skewed && !data.duplicates.length && !data.too_few" class="text-emerald-300">{{ t('lora.diag.clean') }}</span>
      </template>
      <span class="ml-auto text-[11px] text-gray-500">{{ t('lora.diag.hint') }}</span>
    </button>

    <div v-if="open" class="px-4 pb-3 flex flex-col gap-3 text-xs max-h-[45vh] overflow-y-auto">
      <div class="flex items-center gap-2">
        <button type="button" :disabled="loading" @click="load"
          class="px-2 py-1 rounded-lg bg-gray-800 hover:bg-gray-700 disabled:opacity-40">
          {{ loading ? t('lora.diag.reading') : t('lora.diag.reread') }}
        </button>
        <span v-if="error" class="text-red-300">{{ error }}</span>
        <span v-if="data" class="text-[11px] text-gray-500">
          {{ t('lora.diag.coverage', { tagged: cov.tagged, n: data.images }) }}
          <template v-if="cov.untagged"> · {{ t('lora.diag.untagged', { n: cov.untagged }) }}</template>
          <template v-if="cov.uncaptioned"> · {{ t('lora.diag.uncaptioned', { n: cov.uncaptioned }) }}</template>
          <template v-if="cov.unreadable"> · {{ t('lora.diag.unreadable', { n: cov.unreadable }) }}</template>
        </span>
      </div>

      <template v-if="data">
        <!-- Burn-in -->
        <section class="flex flex-col gap-1.5">
          <h4 class="font-semibold text-gray-300">{{ t('lora.diag.burnTitle') }}</h4>
          <p class="text-[11px] text-gray-500 leading-snug">
            {{ t('lora.diag.burnHelp', { present: pct(data.thresholds.burn_present), unsaid: pct(data.thresholds.burn_uncaptioned) }) }}
          </p>
          <p v-if="data.too_few" class="text-gray-500">{{ t('lora.diag.tooFew', { n: data.thresholds.min_images }) }}</p>
          <p v-else-if="!data.burn_in.length" class="text-emerald-300">{{ t('lora.diag.noBurn') }}</p>
          <div v-for="r in [...risks, ...intended]" :key="r.tag"
            class="flex flex-wrap items-center gap-2 p-1.5 rounded-lg border"
            :class="r.status === 'risk' ? 'bg-red-950/30 border-red-900/50' : 'bg-gray-900/40 border-gray-800 opacity-70'">
            <span class="font-mono" :class="r.status === 'risk' ? 'text-red-200' : 'text-gray-300'">{{ r.tag }}</span>
            <span class="text-[11px] text-gray-400">
              {{ t('lora.diag.burnLine', { present: pct(r.present), unsaid: pct(r.uncaptioned) }) }}
            </span>
            <button v-for="sha in r.examples" :key="sha" type="button" @click="emit('focus', sha)"
              :title="t('lora.diag.toCaption')">
              <img :src="`/api/thumbnails/${sha}.webp`" class="w-8 h-8 rounded object-cover hover:ring-2 ring-amber-400" loading="lazy" />
            </button>
            <div class="ml-auto flex gap-1">
              <button v-if="r.status === 'risk'" type="button" @click="setAbsorb(r.tag, true)"
                class="px-2 py-0.5 rounded bg-amber-800/60 hover:bg-amber-700" :title="t('lora.diag.absorbHelp')">
                {{ t('lora.diag.absorb') }}
              </button>
              <button v-else type="button" @click="setAbsorb(r.tag, false)"
                class="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700">{{ t('lora.diag.unabsorb') }}</button>
              <button v-if="r.status === 'risk' && r.examples.length" type="button" @click="emit('focus', r.examples[0])"
                class="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700">{{ t('lora.diag.toCaption') }}</button>
            </div>
          </div>
        </section>

        <!-- Skew -->
        <section v-if="data.skew.length" class="flex flex-col gap-1">
          <h4 class="font-semibold text-gray-300">{{ t('lora.diag.skewTitle') }}</h4>
          <p class="text-[11px] text-gray-500 leading-snug">{{ t('lora.diag.skewHelp', { share: pct(data.thresholds.skew_share) }) }}</p>
          <div v-for="s in data.skew" :key="s.slot" class="flex items-center gap-2">
            <span class="w-24 shrink-0 text-gray-400" :class="s.skewed && 'text-amber-200 font-semibold'">
              {{ s.skewed ? '⚠ ' : '' }}{{ slotLabel(s.slot) }}
            </span>
            <div class="flex-1 min-w-0 flex h-4 rounded overflow-hidden bg-gray-800" role="img"
              :aria-label="s.values.map(v => `${v.tag} ${v.count}`).join(', ')">
              <div v-for="(v, i) in s.values" :key="v.tag" :title="`${v.tag}: ${v.count}`"
                :style="{ width: `${(v.count / s.total) * 100}%` }"
                :class="[i === 0 ? (s.skewed ? 'bg-amber-600' : 'bg-slate-500') : i % 2 ? 'bg-slate-600' : 'bg-slate-700', 'border-r border-gray-900']" />
            </div>
            <span class="w-40 shrink-0 truncate text-[11px] text-gray-400" :title="s.values.map(v => `${v.tag} ${v.count}`).join(', ')">
              {{ s.values[0].tag }} {{ pct(s.top_share) }}
              <template v-if="s.unknown"> · {{ t('lora.diag.unknownN', { n: s.unknown }) }}</template>
            </span>
          </div>
        </section>

        <!-- Near-duplicates -->
        <section v-if="data.duplicates.length" class="flex flex-col gap-1.5">
          <h4 class="font-semibold text-gray-300">{{ t('lora.diag.dupTitle') }}</h4>
          <p class="text-[11px] text-gray-500 leading-snug">{{ t('lora.diag.dupHelp') }}</p>
          <div class="flex flex-wrap gap-2">
            <div v-for="p in data.duplicates" :key="`${p.a}-${p.b}`"
              class="flex items-center gap-1 p-1 rounded-lg bg-gray-900/60 border border-gray-800">
              <template v-for="sha in [p.a, p.b]" :key="sha">
                <div class="flex flex-col items-center gap-0.5">
                  <img :src="`/api/thumbnails/${sha}.webp`" class="w-14 h-14 rounded object-cover" loading="lazy" />
                  <button type="button" class="text-[10px] text-gray-500 hover:text-red-300" @click="emit('remove', sha)">
                    {{ t('lora.diag.dropThis') }}
                  </button>
                </div>
              </template>
              <span class="text-[10px] text-gray-500 px-1">{{ t('lora.diag.distance', { d: p.distance }) }}</span>
            </div>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

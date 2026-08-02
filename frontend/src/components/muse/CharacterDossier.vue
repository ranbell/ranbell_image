<script setup>
/*
 * One character, at length.
 *
 * The grid answers "which one"; this answers "who is she". Her sheet at a size
 * where the four moments of her life are actually readable, her face beside it,
 * and the things the preset has always known about her and never showed: what
 * she thinks when nobody asks, what she likes, what she cannot stand, the
 * colours she dresses in, the thing she always has on her.
 *
 * It is also where you compare checkpoints. Every render of every slot is kept
 * with the model that drew it, so you can put two side by side and pick.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { eyeSwatch, hairSwatch, colorWord, paletteSwatch } from './colorSwatch.js'

const props = defineProps({
  characterId: { type: String, default: '' },
  workflows: { type: Array, default: () => [] },
  workflow: { type: String, default: '' },
})
const emit = defineEmits(['close', 'pick', 'toast', 'update:workflow', 'changed'])
const { t, locale } = useI18n()

const detail = ref(null)
const loading = ref(false)
const busy = ref(false)
const bigSlot = ref('sheet')

const isJa = computed(() => String(locale.value).startsWith('ja'))
const preset = computed(() => detail.value?.preset || null)
const row = computed(() => detail.value?.summary || null)

const name = computed(() => (isJa.value ? preset.value?.name_ja : preset.value?.name) || '')
const blurb = computed(() => (isJa.value ? preset.value?.summary_ja : preset.value?.summary) || '')
const inner = computed(() => (isJa.value ? preset.value?.inner_ja : preset.value?.inner) || [])
const likes = computed(() => preset.value?.preferences?.likes || [])
const dislikes = computed(() => preset.value?.preferences?.dislikes || [])
const palette = computed(() => preset.value?.preferences?.favorite_colors || [])
const appearance = computed(() => preset.value?.appearance || {})
const identity = computed(() => detail.value?.character?.identity_tags || [])

function thumb(sha) { return sha ? `/api/thumbnails/${sha}.webp` : '' }
function full(sha) { return sha ? `/api/originals/${sha}` : '' }
function candidates(slot) { return row.value?.gallery?.[slot] || [] }
function chosen(slot) { return row.value?.board?.[slot] || '' }

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `${resp.status}`)
  return data
}
function fail(err) { emit('toast', { msg: String(err?.message || err), type: 'error' }) }

async function load() {
  if (!props.characterId) { detail.value = null; return }
  loading.value = true
  try { detail.value = await api(`/api/characters/${props.characterId}`) }
  catch (err) { fail(err) } finally { loading.value = false }
}

async function draw(slot) {
  if (!props.workflow) { emit('toast', { msg: t('characters.needWorkflow'), type: 'error' }); return }
  busy.value = true
  try {
    await api(`/api/characters/${props.characterId}/board`, {
      method: 'POST',
      body: JSON.stringify({ workflow_name: props.workflow, slots: [slot] }),
    })
    emit('toast', { msg: t('characters.queued'), type: 'info' })
  } catch (err) { fail(err) } finally { busy.value = false }
}

async function choose(slot, sha) {
  busy.value = true
  try {
    await api(`/api/characters/${props.characterId}/board-image`, {
      method: 'POST', body: JSON.stringify({ slot, sha256: sha }),
    })
    await load()
    emit('changed')
  } catch (err) { fail(err) } finally { busy.value = false }
}

watch(() => props.characterId, load, { immediate: true })
</script>

<template>
  <div
    class="fixed inset-0 z-[var(--z-panel)] flex items-stretch justify-center
           bg-black/85 backdrop-blur-sm p-3"
    @mousedown.self="emit('close')"
    @keydown.esc="emit('close')"
  >
    <div class="sb-shell w-full max-w-[1200px] flex flex-col min-h-0">
      <header class="flex items-center gap-3 px-4 py-3 sb-hairline shrink-0">
        <div class="min-w-0 mr-auto">
          <h2 class="sb-display text-base text-[var(--sb-amber)] truncate">{{ name || '…' }}</h2>
          <p v-if="row" class="flex items-center gap-2 text-[11px] text-[var(--sb-muted)]">
            <span class="inline-flex items-center gap-1">
              <i class="w-2.5 h-2.5 rounded-full border border-white/25"
                 :style="{ background: hairSwatch(row.hair_color) }"></i>
              {{ colorWord(row.hair_color) }}
            </span>
            <span class="inline-flex items-center gap-1">
              <i class="w-2.5 h-2.5 rounded-full border border-white/25"
                 :style="{ background: eyeSwatch(row.eye_color) }"></i>
              {{ colorWord(row.eye_color) }}
            </span>
          </p>
        </div>
        <select
          class="sb-select w-56"
          :value="workflow"
          @change="emit('update:workflow', $event.target.value)"
        >
          <option value="">{{ t('characters.workflow') }} —</option>
          <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
        </select>
        <button class="sb-btn" @click="emit('pick', characterId)">
          {{ t('characters.drawWithHer') }}
        </button>
        <button class="sb-icon-btn" :title="t('muse.close')" @click="emit('close')">✕</button>
      </header>

      <div v-if="!preset" class="flex-1 grid place-items-center text-xs text-gray-500">…</div>

      <div v-else class="flex-1 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_340px] min-h-0">
        <!-- ── her picture, big enough to read the four frames ── -->
        <section class="overflow-y-auto p-4 min-h-0 flex flex-col gap-3">
          <div class="sb-seg self-start">
            <button
              v-for="slot in ['sheet', 'portrait']"
              :key="slot"
              type="button"
              class="sb-seg-btn"
              :class="bigSlot === slot ? 'is-on-teal' : ''"
              @click="bigSlot = slot"
            >{{ t(`characters.${slot}`) }}</button>
          </div>

          <div class="rounded-xl border border-white/10 bg-black/40 overflow-hidden
                      grid place-items-center min-h-[300px]">
            <img
              v-if="chosen(bigSlot)"
              :src="full(chosen(bigSlot))"
              :alt="name"
              class="max-h-[62vh] w-auto object-contain"
            />
            <div v-else class="text-center p-8">
              <p class="text-xs text-gray-500 mb-3">{{ t('characters.noneYet') }}</p>
              <button class="sb-btn" :disabled="busy" @click="draw(bigSlot)">
                {{ t('characters.draw') }}
              </button>
            </div>
          </div>

          <!-- every render of this slot, and which model made it -->
          <div class="flex items-center gap-2">
            <p class="sb-label mr-auto">
              {{ t('characters.candidates') }} ({{ candidates(bigSlot).length }})
            </p>
            <button class="sb-btn" :disabled="busy" @click="draw(bigSlot)">
              {{ t('characters.drawWithThis') }}
            </button>
          </div>
          <div v-if="candidates(bigSlot).length" class="flex gap-2 overflow-x-auto pb-2">
            <button
              v-for="c in candidates(bigSlot)"
              :key="c.sha"
              type="button"
              class="shrink-0 w-24 text-left group"
              :disabled="busy"
              @click="choose(bigSlot, c.sha)"
            >
              <span
                class="block rounded-lg border overflow-hidden aspect-[3/4]"
                :class="c.sha === chosen(bigSlot)
                  ? 'border-teal-400/80 ring-1 ring-teal-400/40'
                  : 'border-white/10 group-hover:border-white/40'"
              >
                <img :src="thumb(c.sha)" class="w-full h-full object-cover" alt="" loading="lazy" />
              </span>
              <span class="block text-[9px] text-gray-500 truncate mt-1" :title="c.workflow">
                {{ c.workflow || t('characters.unknownModel') }}
              </span>
            </button>
          </div>
        </section>

        <!-- ── who she is ── -->
        <aside class="border-l border-white/5 overflow-y-auto p-4 space-y-4 min-h-0">
          <div v-if="chosen('portrait') && bigSlot !== 'portrait'"
               class="rounded-lg overflow-hidden border border-white/10">
            <img :src="thumb(chosen('portrait'))" class="w-full object-cover" alt="" />
          </div>

          <p class="sb-prose text-[13px] text-gray-200 leading-relaxed">{{ blurb }}</p>

          <div class="flex flex-wrap gap-1">
            <span
              v-for="trait in preset.personality || []"
              :key="trait"
              class="px-2 py-0.5 rounded-full bg-teal-900/30 border border-teal-600/30
                     text-[10px] text-teal-200"
            >{{ trait }}</span>
          </div>

          <section v-if="inner.length" class="space-y-1">
            <p class="sb-label">{{ t('characters.inner') }}</p>
            <p v-for="line in inner" :key="line"
               class="text-[12px] text-gray-400 leading-relaxed pl-2 border-l border-white/10">
              {{ line }}
            </p>
          </section>

          <section v-if="likes.length" class="space-y-1">
            <p class="sb-label">{{ t('characters.likes') }}</p>
            <p class="text-[12px] text-gray-300">{{ likes.join(' · ') }}</p>
          </section>
          <section v-if="dislikes.length" class="space-y-1">
            <p class="sb-label">{{ t('characters.dislikes') }}</p>
            <p class="text-[12px] text-gray-400">{{ dislikes.join(' · ') }}</p>
          </section>

          <section v-if="palette.length" class="space-y-1">
            <p class="sb-label">{{ t('characters.palette') }}</p>
            <div class="flex flex-wrap gap-1.5">
              <span
                v-for="c in palette"
                :key="c"
                class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded
                       bg-white/5 border border-white/10 text-[10px] text-gray-300"
              >
                <i v-if="paletteSwatch(c)"
                   class="w-2.5 h-2.5 rounded-full border border-white/25"
                   :style="{ background: paletteSwatch(c) }"></i>
                {{ c }}
              </span>
            </div>
          </section>

          <section v-if="preset.signature_prop" class="space-y-1">
            <p class="sb-label">{{ t('characters.signatureProp') }}</p>
            <p class="text-[12px] font-mono text-amber-200/80">{{ preset.signature_prop }}</p>
          </section>

          <section v-if="appearance.body" class="space-y-1">
            <p class="sb-label">{{ t('characters.build') }}</p>
            <p class="text-[12px] text-gray-400">{{ appearance.body }}</p>
          </section>

          <section v-if="identity.length" class="space-y-1">
            <p class="sb-label">{{ t('characters.identityTags') }}</p>
            <div class="flex flex-wrap gap-1">
              <span
                v-for="tag in identity"
                :key="tag"
                class="px-1.5 py-0.5 rounded bg-black/40 border border-white/10
                       text-[10px] font-mono text-gray-400"
              >{{ tag }}</span>
            </div>
          </section>
        </aside>
      </div>
    </div>
  </div>
</template>

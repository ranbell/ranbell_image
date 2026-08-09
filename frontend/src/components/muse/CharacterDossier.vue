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
import { useRenderWatch } from '../../composables/useRenderWatch.js'
import ActressDiaryModal from './ActressDiaryModal.vue'

const props = defineProps({
  characterId: { type: String, default: '' },
  workflows: { type: Array, default: () => [] },
  workflow: { type: String, default: '' },
  // Unlike the gallery, this is `v-if`'d away on close, so its watch dies with
  // it. The renders do not — so on open, ask the jobs what is still coming.
  getJobsMap: { type: Function, default: () => () => new Map() },
})
const emit = defineEmits(['close', 'pick', 'toast', 'update:workflow', 'changed'])
const { t, locale } = useI18n()

const detail = ref(null)
const loading = ref(false)
const busy = ref(false)
const bigSlot = ref('sheet')
const showDiary = ref(false)
const unreadDiaryCount = ref(0)

async function checkUnreadDiaries() {
  if (!props.characterId) return
  try {
    const res = await api(`/api/characters/${props.characterId}/diaries`)
    const diaries = res.diaries || []
    unreadDiaryCount.value = diaries.filter(d => !d.read).length
  } catch (err) {
    console.debug('Failed to check unread diaries', err)
  }
}


const isJa = computed(() => String(locale.value).startsWith('ja'))
const preset = computed(() => detail.value?.preset || null)
const row = computed(() => detail.value?.summary || null)

const name = computed(() => (isJa.value ? preset.value?.name_ja : preset.value?.name) || '')
const blurb = computed(() => (isJa.value ? preset.value?.summary_ja : preset.value?.summary) || '')
const inner = computed(() => (isJa.value ? preset.value?.inner_ja : preset.value?.inner) || [])
const title = computed(() => (isJa.value ? preset.value?.title_ja : preset.value?.title) || '')
const charm = computed(() => (isJa.value ? preset.value?.charm_ja : preset.value?.charm) || '')
const firstPerson = computed(() => (isJa.value ? preset.value?.first_person_ja : (preset.value?.first_person_en || preset.value?.first_person_ja)) || '')
const userAddress = computed(() => (isJa.value ? preset.value?.user_address_ja : (preset.value?.user_address_en || preset.value?.user_address_ja)) || '')
const talkQuirks = computed(() => (isJa.value ? preset.value?.talk_quirks : (preset.value?.talk_quirks_en || preset.value?.talk_quirks)) || '')
const sayExamples = computed(() => (isJa.value ? preset.value?.duet_say_examples : (preset.value?.duet_say_examples_en || preset.value?.duet_say_examples)) || [])
// Newest first — a chemistry note from a shoot two months ago is less
// interesting than what just happened.
const chemistry = computed(
  () => [...(preset.value?.chemistry || [])].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0)),
)
function chemistryPartnerName(rec) {
  return (isJa.value ? rec.partner_name_ja : (rec.partner_name || rec.partner_name_ja)) || ''
}
function chemistryText(rec) {
  return (isJa.value ? rec.content_ja : (rec.content_en || rec.content_ja)) || ''
}
function sourceSummary(src) {
  return (isJa.value ? src.summary_ja : (src.summary_en || src.summary_ja)) || ''
}
const likes = computed(() => preset.value?.preferences?.likes || [])
const dislikes = computed(() => preset.value?.preferences?.dislikes || [])
const palette = computed(() => preset.value?.preferences?.favorite_colors || [])
const appearance = computed(() => preset.value?.appearance || {})
const identity = computed(() => detail.value?.character?.identity_tags || [])
// The draw buttons name what they produce — "全身" or "バストアップ" — because
// the header's button used to say "draw with her" and did not draw anything.
const slotName = computed(() => t(`characters.${bigSlot.value}`))

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
  try {
    detail.value = await api(`/api/characters/${props.characterId}`)
    await checkUnreadDiaries()
  } catch (err) { fail(err) } finally { loading.value = false }
  resumeWatch()
}


// A queued render attaches itself minutes later and nothing announces it, so
// without this the new candidate only appeared if you closed this and reopened.
const { watch: watchRenders, watching } = useRenderWatch(async () => {
  await load()
  emit('changed')
})

const ACTIVE_STATES = new Set(['queued', 'running', 'cancelling'])

/** Renders of *this* character that have not landed yet. */
function resumeWatch() {
  const map = props.getJobsMap?.()
  if (!map?.values) return
  const mine = [...map.values()].filter(
    j => j?.meta?.character_id === props.characterId && ACTIVE_STATES.has(j.state),
  )
  if (mine.length && !watching.value) watchRenders(Math.max(60, mine.length * 45))
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
    watchRenders(180)
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
          <h2 class="sb-display text-base text-[var(--sb-amber)] truncate">
            {{ name || '…' }}
            <span v-if="title" class="ml-2 text-[11px] text-[var(--sb-teal)] font-normal">
              {{ title }}
            </span>
          </h2>
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
        <button
          type="button"
          class="relative sb-btn bg-pink-900/40 hover:bg-pink-800/60 border-pink-500/40 text-pink-200 flex items-center gap-1.5"
          @click="showDiary = true"
        >
          <span>{{ t('characters.secretDiary') }}</span>
          <span
            v-if="unreadDiaryCount > 0"
            class="px-1.5 py-0.2 rounded-full text-[9px] font-bold bg-rose-500 text-white animate-pulse"
          >{{ unreadDiaryCount }}</span>
        </button>

        <button class="sb-btn" @click="emit('pick', characterId)">
          {{ t('characters.useCharacter') }}
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
              <p class="text-xs text-gray-500">{{ t('characters.noneYet') }}</p>
              <p class="text-[11px] text-gray-600 mt-1">{{ t('characters.createBelow') }}</p>
            </div>
          </div>

          <!-- every render of this slot, and which model made it -->
          <div class="flex items-center gap-2">
            <p class="sb-label mr-auto">
              {{ t('characters.candidates') }} ({{ candidates(bigSlot).length }})
              <span v-if="watching" class="text-teal-300/80">· {{ t('characters.watching') }}</span>
            </p>
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
                class="relative block rounded-lg border overflow-hidden aspect-[3/4]"
                :class="c.sha === chosen(bigSlot)
                  ? 'border-teal-400/80 ring-1 ring-teal-400/40'
                  : 'border-white/10 group-hover:border-white/40'"
              >
                <img :src="thumb(c.sha)" class="w-full h-full object-cover" alt="" loading="lazy" />
                <span
                  v-if="c.sha === chosen(bigSlot)"
                  class="absolute top-1 left-1 px-1 py-0.5 rounded text-[8px] font-bold
                         bg-teal-500/90 text-white shadow"
                >📌 {{ t('characters.pinnedLabel') }}</span>
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

          <section v-if="charm" class="space-y-1">
            <p class="sb-label">{{ t('characters.charm') }}</p>
            <p class="text-[12px] text-teal-200/90 leading-relaxed pl-2
                      border-l-2 border-[var(--sb-teal)]/50">{{ charm }}</p>
          </section>

          <!-- Dialogue & Personality Profile -->
          <section v-if="firstPerson || userAddress || talkQuirks" class="space-y-1.5 p-2.5 rounded-lg bg-amber-950/20 border border-amber-500/20">
            <p class="sb-label text-amber-300 font-semibold flex items-center gap-1">
              <span>💬</span> {{ t('muse.firstPerson') }} / {{ t('muse.userAddress') }}
            </p>
            <div class="grid grid-cols-2 gap-2 text-[11px] text-amber-100/90">
              <div v-if="firstPerson">
                <span class="text-gray-400 block text-[9px]">{{ t('muse.firstPerson') }}</span>
                <span class="font-bold text-amber-200">{{ firstPerson }}</span>
              </div>
              <div v-if="userAddress">
                <span class="text-gray-400 block text-[9px]">{{ t('muse.userAddress') }}</span>
                <span class="font-bold text-amber-200">{{ userAddress }}</span>
              </div>
            </div>
            <p v-if="talkQuirks" class="text-[11px] text-amber-200/80 pt-1 border-t border-amber-500/10">
              <span class="text-gray-400 block text-[9px]">{{ t('muse.talkQuirks') }}</span>
              {{ talkQuirks }}
            </p>
          </section>

          <!-- Duet Dialogue Examples -->
          <section v-if="sayExamples.length" class="space-y-1.5">
            <p class="sb-label text-pink-300 font-semibold flex items-center gap-1">
              <span>🎭</span> {{ t('muse.sayExamples') }}
            </p>
            <div class="space-y-1.5">
              <div
                v-for="(line, idx) in sayExamples"
                :key="idx"
                class="p-2 rounded bg-pink-950/30 border border-pink-500/20 text-[11px] text-pink-100/90 leading-snug italic"
              >
                「{{ line }}」
              </div>
            </div>
          </section>

          <!-- Chemistry — a duet's relationship note, read from her diary
               against her partner's. Hover a card to see which diary entries
               it came from. -->
          <section v-if="chemistry.length" class="space-y-1.5">
            <p class="sb-label text-rose-300 font-semibold flex items-center gap-1">
              <span>💞</span> {{ t('characters.chemistry') }}
            </p>
            <div class="space-y-1.5">
              <div
                v-for="rec in chemistry"
                :key="rec.id"
                class="group relative p-2 rounded bg-rose-950/30 border border-rose-500/20
                       text-[11px] text-rose-100/90 leading-snug cursor-default"
              >
                <div class="flex items-center gap-1.5 mb-1">
                  <span class="px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-rose-500/80 text-white">
                    {{ t(`characters.chemistryTier.${rec.tier || 'acquaintance'}`) }}
                  </span>
                  <span v-if="chemistryPartnerName(rec)" class="text-rose-300/70 text-[10px]">
                    {{ chemistryPartnerName(rec) }}
                  </span>
                </div>
                <p>{{ chemistryText(rec) }}</p>

                <div
                  v-if="(rec.sources || []).length"
                  class="hidden group-hover:block absolute z-10 left-0 top-full mt-1 w-64 p-2
                         rounded-lg bg-black/95 border border-rose-500/30 shadow-xl
                         text-[10px] text-gray-300 space-y-1.5"
                >
                  <p class="text-rose-300 font-semibold">{{ t('characters.chemistrySources') }}</p>
                  <p
                    v-for="src in rec.sources"
                    :key="src.diary_id"
                    class="border-l-2 border-rose-500/40 pl-1.5"
                  >{{ sourceSummary(src) }}</p>
                </div>
              </div>
            </div>
          </section>

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

      <!-- Workflow selection and creation live down here, not up top — the
           header used to hold both, and a picker that opens with nothing
           picked yet reads as broken before you scroll down to what she
           actually looks like. -->
      <footer v-if="preset" class="flex flex-wrap items-center justify-end gap-2 px-4 py-3 sb-hairline shrink-0">
        <select
          class="sb-select w-56"
          :value="workflow"
          @change="emit('update:workflow', $event.target.value)"
        >
          <option value="">{{ t('characters.workflow') }} —</option>
          <option v-for="w in workflows" :key="w" :value="w">{{ w }}</option>
        </select>
        <button class="sb-btn" :disabled="busy" @click="draw(bigSlot)">
          {{ chosen(bigSlot)
            ? t('characters.drawSlotMore', { slot: slotName })
            : t('characters.drawSlot', { slot: slotName }) }}
        </button>
      </footer>
    </div>

    <ActressDiaryModal
      v-if="showDiary"
      :show="showDiary"
      :character-id="characterId"
      :character-name="name"
      @close="showDiary = false; checkUnreadDiaries()"
      @diary-read="checkUnreadDiaries"
      @toast="emit('toast', $event)"
    />
  </div>
</template>


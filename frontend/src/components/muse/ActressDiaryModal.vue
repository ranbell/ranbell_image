<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  characterId: { type: String, required: true },
  characterName: { type: String, default: '女優' },
  show: { type: Boolean, default: false }
})

const emit = defineEmits(['close', 'diary-read', 'toast'])
const { t, locale } = useI18n()

const diaries = ref([])
const loading = ref(false)
const selectedDiary = ref(null)
// Set when an unread entry is opened: she does not know yet, and will say
// something about it the next time they work together.
const justCaught = ref(false)
const zoomed = ref(false)
const dialogEl = ref(null)
const listEl = ref(null)

// `=== 'en'` missed en-US, and the rest of the panel decides the same question
// with startsWith('ja'). Both halves are stored, so the toggle is free.
const uiIsJa = computed(() => String(locale.value).startsWith('ja'))
const forced = ref('')          // '', 'ja', 'en' — per-entry override
const showJa = computed(() => (forced.value ? forced.value === 'ja' : uiIsJa.value))

function pick(d, key) {
  if (!d) return ''
  const ja = d[`${key}_ja`] || d[key] || ''
  const en = d[`${key}_en`] || ''
  if (showJa.value) return ja || en
  return en || ja
}
const bothLanguages = computed(() => {
  const d = selectedDiary.value
  return Boolean(d && (d.content_ja || d.content) && d.content_en)
})

function getDiarySummary(d) {
  return pick(d, 'summary') || t('characters.diary.defaultSummary')
}
function getDiaryContent(d) {
  return pick(d, 'content')
}

function thumb(sha) {
  return sha ? `/api/thumbnails/${sha}.webp` : ''
}
function full(sha) {
  return sha ? `/api/originals/${sha}` : ''
}

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `${resp.status}`)
  return data
}

async function loadDiaries() {
  if (!props.characterId) return
  loading.value = true
  try {
    const res = await api(`/api/characters/${props.characterId}/diaries`)
    diaries.value = res.diaries || []
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  } finally {
    loading.value = false
  }
}

// Opening the panel used to open the newest entry, which marked it read before
// the Showrunner had chosen to read anything. A page is only turned on purpose.
async function openDiary(diary) {
  selectedDiary.value = diary
  forced.value = ''
  zoomed.value = false
  justCaught.value = false
  if (!diary || diary.read) return
  try {
    await api(`/api/characters/${props.characterId}/diaries/${diary.id}/read`, {
      method: 'POST'
    })
    diary.read = true
    if (selectedDiary.value === diary) justCaught.value = true
    emit('diary-read', diary.id)
  } catch (err) {
    console.warn('Failed to mark diary as read', err)
  }
}

async function removeDiary(diary) {
  if (!diary || !window.confirm(t('characters.diary.deleteConfirm'))) return
  try {
    await api(`/api/characters/${props.characterId}/diaries/${diary.id}`, { method: 'DELETE' })
    diaries.value = diaries.value.filter(d => d.id !== diary.id)
    if (selectedDiary.value?.id === diary.id) selectedDiary.value = null
    emit('diary-read', diary.id)
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  }
}

function formatDate(ts) {
  if (!ts) return ''
  try {
    return new Intl.DateTimeFormat(locale.value, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    }).format(new Date(ts * 1000))
  } catch {
    return new Date(ts * 1000).toLocaleString()
  }
}

// Arrow keys walk the shelf; Escape closes. Neither worked before.
function onKeydown(e) {
  if (!props.show) return
  if (e.key === 'Escape') { emit('close'); return }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  if (!diaries.value.length) return
  e.preventDefault()
  const at = diaries.value.findIndex(d => d.id === selectedDiary.value?.id)
  const next = e.key === 'ArrowDown'
    ? Math.min(diaries.value.length - 1, at + 1)
    : Math.max(0, at < 0 ? 0 : at - 1)
  openDiary(diaries.value[next])
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

watch(() => props.show, async (val) => {
  if (!val) return
  await loadDiaries()
  await nextTick()
  dialogEl.value?.focus()
}, { immediate: true })
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center
           bg-pink-950/40 backdrop-blur-md p-4 animate-fade-in"
    @click.self="emit('close')"
  >
    <!-- Cute Secret Diary Container -->
    <div
      ref="dialogEl"
      role="dialog"
      aria-modal="true"
      aria-labelledby="diary-title"
      tabindex="-1"
      class="relative w-full max-w-4xl max-h-[90vh] flex flex-col md:flex-row rounded-3xl outline-none
             bg-gradient-to-br from-pink-50/95 via-rose-50/95 to-amber-50/95 dark:from-slate-900/95 dark:via-pink-950/90 dark:to-slate-900/95
             border-2 border-pink-300/60 dark:border-pink-500/40 shadow-2xl overflow-hidden"
    >
      <!-- Close Ribbon Button -->
      <button
        type="button"
        :aria-label="t('characters.diary.close')"
        class="absolute top-3 right-3 z-20 w-9 h-9 rounded-full bg-pink-200/80 hover:bg-pink-300 text-pink-700
               dark:bg-pink-900/80 dark:hover:bg-pink-800 dark:text-pink-200 flex items-center justify-center
               font-bold text-lg shadow-md transition-transform hover:scale-110"
        @click="emit('close')"
      >
        ✕
      </button>

      <!-- Left Sidebar: Diary List -->
      <aside class="w-full md:w-80 border-b md:border-b-0 md:border-r border-pink-200/60 dark:border-pink-800/40 p-4 flex flex-col gap-3 shrink-0 bg-pink-100/40 dark:bg-pink-950/30">
        <div class="flex items-center gap-2 px-1 py-1">
          <span class="text-2xl">📖</span>
          <div>
            <h3 id="diary-title" class="font-bold text-pink-900 dark:text-pink-200 text-base tracking-wide">
              {{ t('characters.diary.title', { name: characterName }) }}
            </h3>
            <p class="text-[11px] text-pink-600/80 dark:text-pink-400">
              {{ t('characters.diary.subtitle') }}
            </p>
          </div>
        </div>

        <div v-if="loading" class="text-xs text-pink-500 py-8 text-center motion-safe:animate-pulse">
          {{ t('characters.diary.loading') }}
        </div>

        <div v-else-if="!diaries.length" class="text-xs text-pink-400/80 py-12 text-center whitespace-pre-wrap">
          {{ t('characters.diary.empty') }}
        </div>

        <div v-else ref="listEl" class="flex flex-col gap-2 overflow-y-auto max-h-[38vh] md:max-h-[60vh] pr-1">
          <button
            v-for="d in diaries"
            :key="d.id"
            type="button"
            :aria-current="selectedDiary?.id === d.id ? 'true' : undefined"
            class="group relative text-left p-3 rounded-2xl border transition-all duration-200 flex flex-col gap-1"
            :class="selectedDiary?.id === d.id
              ? 'bg-pink-200/80 dark:bg-pink-900/60 border-pink-400 dark:border-pink-500 shadow-sm scale-[1.02]'
              : 'bg-white/60 dark:bg-slate-800/60 border-pink-100 dark:border-pink-900/40 hover:bg-pink-100/60 dark:hover:bg-pink-900/30'"
            @click="openDiary(d)"
          >
            <div class="flex items-center justify-between gap-1">
              <span class="text-[10px] text-pink-500 dark:text-pink-400 font-medium">
                {{ formatDate(d.timestamp) }}
              </span>
              <!-- Unread Cute Badge -->
              <span
                v-if="!d.read"
                class="px-2 py-0.5 rounded-full text-[9px] font-bold bg-rose-500 text-white shadow-sm motion-safe:animate-bounce"
              >
                {{ t('characters.diary.unread') }}
              </span>
            </div>

            <p class="text-xs font-semibold text-pink-950 dark:text-pink-100 line-clamp-2">
              {{ getDiarySummary(d) }}
            </p>
          </button>
        </div>
      </aside>

      <!-- Right Main: Selected Diary Page -->
      <main class="flex-1 p-6 overflow-y-auto flex flex-col gap-4 relative bg-amber-50/30 dark:bg-slate-900/50">
        <div v-if="!selectedDiary" class="flex-1 flex flex-col items-center justify-center text-pink-400 text-xs py-12">
          <span>{{ t('characters.diary.selectPrompt') }}</span>
        </div>

        <div v-else class="flex flex-col gap-4 animate-fade-in">
          <!-- Page Header -->
          <div class="border-b border-pink-200/60 dark:border-pink-800/40 pb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <span class="text-xs font-bold px-2.5 py-1 rounded-full bg-pink-200/60 dark:bg-pink-900/60 text-pink-700 dark:text-pink-300">
                {{ t('characters.diary.entryBadge') }}
              </span>
              <h2 class="text-lg font-bold text-pink-950 dark:text-pink-100 mt-1">
                {{ getDiarySummary(selectedDiary) }}
              </h2>
              <p v-if="selectedDiary.theme" class="text-[11px] text-pink-600/70 dark:text-pink-400 mt-0.5">
                {{ t('characters.diary.themeLabel') }}: {{ selectedDiary.theme }}
              </p>
            </div>
            <div class="flex items-center gap-2">
              <!-- Both halves are written and stored; reading her in her own
                   language is worth one button. -->
              <div v-if="bothLanguages" class="flex rounded-full overflow-hidden border border-pink-300/60 dark:border-pink-700/60 text-[10px]">
                <button
                  type="button" class="px-2 py-0.5"
                  :class="showJa ? 'bg-pink-400 text-white' : 'text-pink-600 dark:text-pink-300'"
                  @click="forced = 'ja'"
                >日本語</button>
                <button
                  type="button" class="px-2 py-0.5"
                  :class="!showJa ? 'bg-pink-400 text-white' : 'text-pink-600 dark:text-pink-300'"
                  @click="forced = 'en'"
                >EN</button>
              </div>
              <span class="text-xs text-pink-600/70 dark:text-pink-400">
                {{ formatDate(selectedDiary.timestamp) }}
              </span>
              <button
                type="button"
                class="text-[10px] text-pink-500/70 hover:text-rose-500 underline"
                @click="removeDiary(selectedDiary)"
              >
                {{ t('characters.diary.delete') }}
              </button>
            </div>
          </div>

          <!-- Polaroid Photo Attachment. The thumbnail carries the page; the
               original is a click away rather than loaded into a 20rem frame. -->
          <div v-if="selectedDiary.image_id" class="self-center my-2 group">
            <button
              type="button"
              class="relative block bg-white dark:bg-slate-800 p-3 pb-8 rounded-lg shadow-xl border border-pink-200/50 dark:border-pink-900/50
                     transform -rotate-1 transition-transform duration-300 group-hover:rotate-0 group-hover:scale-105 max-w-xs"
              :aria-label="t('characters.diary.photoZoom')"
              @click="zoomed = !zoomed"
            >
              <!-- Washi Tape Effect -->
              <div class="absolute -top-3 left-1/2 -translate-x-1/2 w-20 h-6 bg-pink-200/60 dark:bg-pink-700/40 backdrop-blur-xs border-dashed border-pink-300/60 rotate-2 shadow-xs"></div>

              <img
                :src="zoomed ? full(selectedDiary.image_id) : thumb(selectedDiary.image_id)"
                :alt="getDiarySummary(selectedDiary)"
                loading="lazy"
                class="w-full h-auto rounded object-cover aspect-[3/4] bg-pink-100 dark:bg-slate-900"
              />
              <p class="text-center font-serif italic text-pink-600 dark:text-pink-300 text-xs mt-3 font-semibold">
                {{ t('characters.diary.photoMemory') }}
              </p>
            </button>
          </div>

          <!-- Handwritten style Diary Content -->
          <div class="relative bg-white/70 dark:bg-slate-800/70 p-5 rounded-2xl border border-pink-200/50 dark:border-pink-900/50 shadow-inner">
            <p
              v-if="getDiaryContent(selectedDiary)"
              class="whitespace-pre-wrap text-sm leading-relaxed text-pink-950 dark:text-pink-100 font-serif"
            >
              {{ getDiaryContent(selectedDiary) }}
            </p>
            <p v-else class="text-xs text-pink-400 italic">
              {{ t('characters.diary.emptyEntry') }}
            </p>
          </div>

          <!-- She does not know yet. She will, next time they work together. -->
          <div
            v-if="justCaught"
            class="animate-fade-in rounded-2xl border border-rose-300/60 dark:border-rose-800/60 bg-rose-50/80 dark:bg-rose-950/40 p-3"
          >
            <p class="text-[11px] text-rose-600 dark:text-rose-300">
              {{ t('characters.diary.willNotice') }}
            </p>
          </div>
        </div>
      </main>
    </div>
  </div>
</template>


<style scoped>
@keyframes fade-in {
  from { opacity: 0; transform: scale(0.98); }
  to { opacity: 1; transform: scale(1); }
}
.animate-fade-in {
  animation: fade-in 220ms ease-out both;
}
@media (prefers-reduced-motion: reduce) {
  .animate-fade-in { animation: none; }
}
</style>

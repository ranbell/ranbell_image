<script setup>
import { ref, computed, onMounted, watch } from 'vue'
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

const isEn = computed(() => locale.value === 'en')

function getDiarySummary(d) {
  if (!d) return ''
  if (isEn.value && d.summary_en) return d.summary_en
  return d.summary_ja || d.summary || t('characters.diary.defaultSummary')
}

function getDiaryContent(d) {
  if (!d) return ''
  if (isEn.value && d.content_en) return d.content_en
  return d.content_ja || d.content || ''
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
    if (diaries.value.length > 0 && !selectedDiary.value) {
      openDiary(diaries.value[0])
    }
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  } finally {
    loading.value = false
  }
}

async function openDiary(diary) {
  selectedDiary.value = diary
  if (diary && !diary.read) {
    try {
      await api(`/api/characters/${props.characterId}/diaries/${diary.id}/read`, {
        method: 'POST'
      })
      diary.read = true
      emit('diary-read', diary.id)
    } catch (err) {
      console.warn('Failed to mark diary as read', err)
    }
  }
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

watch(() => props.show, (val) => {
  if (val) loadDiaries()
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
      class="relative w-full max-w-4xl max-h-[90vh] flex flex-col md:flex-row rounded-3xl
             bg-gradient-to-br from-pink-50/95 via-rose-50/95 to-amber-50/95 dark:from-slate-900/95 dark:via-pink-950/90 dark:to-slate-900/95
             border-2 border-pink-300/60 dark:border-pink-500/40 shadow-2xl overflow-hidden"
    >
      <!-- Close Ribbon Button -->
      <button
        type="button"
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
            <h3 class="font-bold text-pink-900 dark:text-pink-200 text-base tracking-wide">
              {{ t('characters.diary.title', { name: characterName }) }}
            </h3>
            <p class="text-[11px] text-pink-600/80 dark:text-pink-400">
              {{ t('characters.diary.subtitle') }}
            </p>
          </div>
        </div>

        <div v-if="loading" class="text-xs text-pink-500 py-8 text-center animate-pulse">
          {{ t('characters.diary.loading') }}
        </div>

        <div v-else-if="!diaries.length" class="text-xs text-pink-400/80 py-12 text-center whitespace-pre-wrap">
          {{ t('characters.diary.empty') }}
        </div>

        <div v-else class="flex flex-col gap-2 overflow-y-auto max-h-[60vh] pr-1">
          <button
            v-for="d in diaries"
            :key="d.id"
            type="button"
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
                class="px-2 py-0.5 rounded-full text-[9px] font-bold bg-rose-500 text-white shadow-sm animate-bounce"
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
            </div>
            <span class="text-xs text-pink-600/70 dark:text-pink-400">
              {{ formatDate(selectedDiary.timestamp) }}
            </span>
          </div>

          <!-- Polaroid Photo Attachment -->
          <div v-if="selectedDiary.image_id" class="self-center my-2 group">
            <div
              class="relative bg-white dark:bg-slate-800 p-3 pb-8 rounded-lg shadow-xl border border-pink-200/50 dark:border-pink-900/50
                     transform -rotate-1 transition-transform duration-300 group-hover:rotate-0 group-hover:scale-105 max-w-xs"
            >
              <!-- Washi Tape Effect -->
              <div class="absolute -top-3 left-1/2 -translate-x-1/2 w-20 h-6 bg-pink-200/60 dark:bg-pink-700/40 backdrop-blur-xs border-dashed border-pink-300/60 rotate-2 shadow-xs"></div>
              
              <img
                :src="full(selectedDiary.image_id) || thumb(selectedDiary.image_id)"
                :alt="getDiarySummary(selectedDiary)"
                class="w-full h-auto rounded object-cover aspect-[3/4] bg-pink-100 dark:bg-slate-900"
              />
              <p class="text-center font-handwriting text-pink-600 dark:text-pink-300 text-xs mt-3 font-semibold">
                {{ t('characters.diary.photoMemory') }}
              </p>
            </div>
          </div>

          <!-- Handwritten style Diary Content -->
          <div class="relative bg-white/70 dark:bg-slate-800/70 p-5 rounded-2xl border border-pink-200/50 dark:border-pink-900/50 shadow-inner">
            <p class="whitespace-pre-wrap text-sm leading-relaxed text-pink-950 dark:text-pink-100 font-serif">
              {{ getDiaryContent(selectedDiary) }}
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
</style>

<script setup>
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const { t, locale } = useI18n()

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['update:show', 'select-image', 'weave-from', 'toast'])

const AXES = ['past', 'present', 'future']

const stories = ref([])
const loading = ref(false)
const regenerating = ref(new Set())   // `${storyId}:${axis}`
const lang = ref(locale.value?.startsWith('ja') ? 'ja' : 'en')

function storyTitle(story) {
  return (lang.value === 'ja' && story.title_ja) ? story.title_ja : (story.title || '')
}
function storyOverall(story) {
  return (lang.value === 'ja' && story.overall_story_ja)
    ? story.overall_story_ja : (story.overall_story || '')
}
function axisStory(story, axis) {
  const a = story.axes?.[axis] || {}
  return (lang.value === 'ja' && a.story_ja) ? a.story_ja : (a.story || '')
}

watch(() => props.show, (val) => { if (val) fetchStories() })

function close() { emit('update:show', false) }

async function fetchStories() {
  loading.value = true
  try {
    const r = await fetch('/api/story/storybook?limit=50')
    if (r.ok) stories.value = (await r.json()).stories
  } catch {}
  loading.value = false
}

async function regenerate(story, axis) {
  const key = `${story.story_id}:${axis}`
  regenerating.value = new Set([...regenerating.value, key])
  try {
    const r = await fetch(`/api/story/${story.story_id}/regenerate/${axis}`, { method: 'POST' })
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText)
    emit('toast', { msg: t('storybook.regenQueued'), type: 'success' })
  } catch (err) {
    emit('toast', { msg: String(err.message || err), type: 'error' })
    const next = new Set(regenerating.value)
    next.delete(key)
    regenerating.value = next
  }
}

function axisImage(story, axis) {
  return story.axes?.[axis]?.image_id || null
}

function openImage(sha256) {
  if (sha256) emit('select-image', sha256)
}

function onThumbError(e, sha256) {
  // fall back to the original once (thumbnail may not be generated yet)
  if (e.target.dataset.fallback) return
  e.target.dataset.fallback = '1'
  e.target.src = `/api/originals/${sha256}`
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[70] bg-black/80 flex items-center justify-center p-4"
      @click.self="close" @keydown.esc="close">
      <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-5xl max-h-[92vh] flex flex-col">

        <!-- header -->
        <div class="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h2 class="text-base font-bold text-amber-300">📖 {{ t('storybook.title') }}</h2>
          <div class="flex items-center gap-2">
            <div class="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
              <button v-for="l in ['ja', 'en']" :key="l" @click="lang = l"
                :class="lang === l ? 'bg-amber-800/70 text-amber-100' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
                class="px-2.5 py-1.5 transition-colors uppercase">{{ l }}</button>
            </div>
            <button @click="fetchStories"
              class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs text-gray-300 transition-colors">
              ⟳ {{ t('storybook.refresh') }}
            </button>
            <button @click="close"
              class="text-gray-600 hover:text-gray-200 text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-5 flex flex-col gap-5">
          <p v-if="loading" class="text-xs text-gray-500">{{ t('storybook.loading') }}</p>
          <p v-else-if="!stories.length" class="text-xs text-gray-500">{{ t('storybook.empty') }}</p>

          <div v-for="story in stories" :key="story.story_id"
            class="bg-gray-800/40 border border-gray-800 rounded-2xl p-4 flex flex-col gap-3">
            <!-- title + overall story -->
            <div v-if="storyTitle(story)" class="flex flex-col gap-1.5">
              <h3 class="text-sm font-bold text-amber-200">{{ storyTitle(story) }}</h3>
              <p v-if="storyOverall(story)"
                class="text-[11px] text-gray-300 leading-relaxed whitespace-pre-wrap border-l-2 border-amber-700/40 pl-3">
                {{ storyOverall(story) }}
              </p>
            </div>
            <div class="flex items-center gap-3 text-[10px] text-gray-500">
              <span v-if="story.worldview" class="text-amber-400/80">🌍 {{ story.worldview }}</span>
              <span v-if="story.time_scale" class="text-teal-400/70">⏳ ± {{ t('chronicle.timeScale.' + story.time_scale) }}</span>
              <span class="ml-auto font-mono">{{ new Date(story.created_at * 1000).toLocaleString() }}</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div v-for="axis in AXES" :key="axis"
                class="rounded-xl border p-3 flex flex-col gap-2"
                :class="axis === story.base_time_axis ? 'border-amber-600/50 bg-amber-900/10' : 'border-gray-800 bg-gray-900/40'">
                <div class="flex items-center justify-between">
                  <span class="text-[10px] font-bold uppercase tracking-wide"
                    :class="axis === story.base_time_axis ? 'text-amber-400' : 'text-teal-400'">
                    {{ t('chronicle.axis.' + axis) }}
                    <span v-if="axis === story.base_time_axis" class="text-gray-500 normal-case font-normal ml-1">({{ t('storybook.base') }})</span>
                  </span>
                  <button v-if="axis !== story.base_time_axis && story.axes?.[axis]?.prompt_positive"
                    @click="regenerate(story, axis)"
                    :disabled="regenerating.has(`${story.story_id}:${axis}`)"
                    :title="t('storybook.regenTitle')"
                    class="text-[10px] px-2 py-0.5 bg-purple-900/60 hover:bg-purple-800/70 disabled:opacity-40 border border-purple-700/50 rounded-full text-purple-200 transition-colors">
                    🎲 {{ regenerating.has(`${story.story_id}:${axis}`) ? t('storybook.regenQueuedShort') : t('storybook.regen') }}
                  </button>
                </div>

                <div class="relative group aspect-square bg-gray-950/60 rounded-lg overflow-hidden flex items-center justify-center cursor-pointer"
                  @click="openImage(axisImage(story, axis))">
                  <img v-if="axisImage(story, axis)" :src="`/api/thumbnails/${axisImage(story, axis)}.webp`"
                    @error="onThumbError($event, axisImage(story, axis))"
                    class="w-full h-full object-cover hover:opacity-90 transition-opacity" loading="lazy" />
                  <span v-else class="text-2xl text-gray-700">⏳</span>
                  <button v-if="axisImage(story, axis)"
                    @click.stop="emit('weave-from', axisImage(story, axis))"
                    :title="t('storybook.weaveFrom')"
                    class="absolute bottom-1.5 right-1.5 px-2 py-1 bg-teal-900/80 hover:bg-teal-700/90 border border-teal-600/50 rounded-lg text-[10px] text-teal-200 opacity-0 group-hover:opacity-100 transition-opacity">
                    📜 {{ t('storybook.weaveFromShort') }}
                  </button>
                </div>

                <p class="text-[11px] text-gray-400 leading-relaxed whitespace-pre-wrap max-h-32 overflow-y-auto">
                  {{ axisStory(story, axis) || '—' }}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

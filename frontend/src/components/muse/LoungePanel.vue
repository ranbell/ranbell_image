<script setup>
/*
 * Studio lounge — Slack-flavoured feed of wrap shares + pitches + handpost
 * + Look-of-the-week trends. Cute rose/pink tone to match the secret diary.
 * Wrap feed, trends, and handpost stay peek-only. Pitches in #ideas can be liked
 * (once, next session with that Muse). Handpost pages still arrive from habit jobs.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'toast', 'seen'])
const { t, locale } = useI18n()

const tab = ref('lounge') // 'lounge' | 'ideas' | 'trends' | 'handpost'
const loading = ref(false)
const liking = ref(false)
const threads = ref([])
const trends = ref([])
const pages = ref([])
const selectedId = ref('')

const isJa = computed(() => String(locale.value).startsWith('ja'))
const selected = computed(() => threads.value.find(t => t.id === selectedId.value) || null)
const loungeThreads = computed(() => {
  const rows = threads.value.filter(th => th.kind !== 'studio_trends' && th.kind !== 'pitch')
  return [...rows].sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0))
})
const ideaThreads = computed(() => {
  const rows = threads.value.filter(th => th.kind === 'pitch')
  return [...rows].sort((a, b) => {
    const al = a.liked ? 0 : 1
    const bl = b.liked ? 0 : 1
    if (al !== bl) return al - bl
    return Number(b.created_at || 0) - Number(a.created_at || 0)
  })
})
const feedThreads = computed(() => (tab.value === 'ideas' ? ideaThreads.value : loungeThreads.value))

function thumb(sha) {
  return sha ? `/api/thumbnails/${sha}.webp` : ''
}
function textOf(row, key = 'text') {
  if (!row) return ''
  const ja = row[`${key}_ja`] || row[key] || ''
  const en = row[`${key}_en`] || ''
  return isJa.value ? (ja || en) : (en || ja)
}
function nameOf(row) {
  if (!row) return ''
  return isJa.value
    ? (row.author_name_ja || row.name_ja || row.author_name || row.name || '')
    : (row.author_name || row.name || row.author_name_ja || row.name_ja || '')
}
function when(ts) {
  if (!ts) return ''
  try {
    return new Date(Number(ts) * 1000).toLocaleString(isJa.value ? 'ja-JP' : 'en')
  } catch { return '' }
}
function kindBadge(th) {
  if (th?.kind === 'pitch') return t('muse.lounge.badgePitch')
  if (th?.kind === 'wrap_share') return t('muse.lounge.badgeWrap')
  return ''
}
function stanceLabel(s) {
  if (s === 'twist') return t('muse.lounge.stanceTwist')
  if (s === 'skip') return t('muse.lounge.stanceSkip')
  return t('muse.lounge.stanceTry')
}

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `${resp.status}`)
  return data
}

async function load() {
  loading.value = true
  try {
    const [th, tr, hp] = await Promise.all([
      api('/api/muse/lounge/threads?limit=50'),
      api('/api/muse/lounge/trends'),
      api('/api/muse/handpost'),
    ])
    threads.value = th.threads || []
    trends.value = tr.trends || []
    pages.value = hp.pages || []
    ensureSelected()
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  } finally {
    loading.value = false
  }
}

function ensureSelected() {
  const rows = feedThreads.value
  if (!rows.length) {
    selectedId.value = ''
    return
  }
  if (!rows.some(th => th.id === selectedId.value)) {
    selectedId.value = rows[0].id
  }
}

async function toggleLike(th) {
  if (!th?.id || liking.value) return
  liking.value = true
  try {
    const row = await api(`/api/muse/lounge/threads/${th.id}/like`, {
      method: 'POST',
      body: JSON.stringify({ liked: !th.liked }),
    })
    threads.value = threads.value.map(item => (item.id === row.id ? { ...item, ...row } : item))
  } catch (err) {
    emit('toast', { msg: String(err?.message || err), type: 'error' })
  } finally {
    liking.value = false
  }
}

watch(() => props.show, (v) => {
  if (v) {
    emit('seen')
    load()
  }
})
watch(tab, () => ensureSelected())
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[var(--z-panel-muse-child)] flex items-stretch justify-center
           bg-black/75 backdrop-blur-sm p-3"
    @mousedown.self="emit('close')"
  >
    <div
      class="lounge-shell flex w-full max-w-5xl max-h-[92vh] overflow-hidden rounded-3xl
             border border-pink-300/40 bg-gradient-to-br from-rose-50 via-pink-50 to-fuchsia-50
             shadow-[0_20px_60px_rgba(244,114,182,0.35)] text-slate-800"
    >
      <aside class="w-44 shrink-0 border-r border-pink-200/70 bg-white/50 p-3 flex flex-col gap-2">
        <div class="text-sm font-semibold text-rose-500 px-1 mb-1">{{ t('muse.lounge.title') }}</div>
        <button type="button" class="lounge-chan" :class="tab === 'lounge' ? 'is-on' : ''" @click="tab = 'lounge'">
          # {{ t('muse.lounge.channelLounge') }}
        </button>
        <button type="button" class="lounge-chan" :class="tab === 'ideas' ? 'is-on' : ''" @click="tab = 'ideas'">
          # {{ t('muse.lounge.channelIdeas') }}
        </button>
        <button type="button" class="lounge-chan" :class="tab === 'trends' ? 'is-on' : ''" @click="tab = 'trends'">
          # {{ t('muse.lounge.channelTrends') }}
        </button>
        <button type="button" class="lounge-chan" :class="tab === 'handpost' ? 'is-on' : ''" @click="tab = 'handpost'">
          # {{ t('muse.lounge.channelHandpost') }}
        </button>
        <p class="mt-auto text-[10px] leading-relaxed text-rose-400/80 px-1">
          {{ t('muse.lounge.blurb') }}
        </p>
      </aside>

      <div class="flex-1 min-w-0 flex flex-col">
        <header class="flex items-center gap-2 px-4 py-3 border-b border-pink-200/60 bg-white/40">
          <h2 class="text-base font-semibold text-rose-600 tracking-wide">
            {{ tab === 'lounge' ? t('muse.lounge.feedTitle')
              : tab === 'ideas' ? t('muse.lounge.ideasTitle')
              : tab === 'trends' ? t('muse.lounge.trendsTitle')
              : t('muse.lounge.handpostTitle') }}
          </h2>
          <span v-if="loading" class="text-xs text-rose-300">…</span>
          <button type="button" class="ml-auto sb-icon-btn !text-rose-500" @click="emit('close')">✕</button>
        </header>

        <!-- lounge feed / showrunner ideas -->
        <div v-if="tab === 'lounge' || tab === 'ideas'" class="flex-1 min-h-0 flex">
          <div class="w-56 shrink-0 border-r border-pink-100 overflow-y-auto p-2 space-y-1.5 bg-white/30">
            <button
              v-for="th in feedThreads"
              :key="th.id"
              type="button"
              class="w-full text-left rounded-2xl px-2.5 py-2 transition border"
              :class="[
                th.id === selectedId
                  ? 'bg-rose-200/60 border-rose-300'
                  : 'bg-white/50 border-transparent hover:border-pink-200',
                th.kind === 'pitch' && th.status === 'open' ? 'ring-1 ring-amber-300/80' : '',
              ]"
              @click="selectedId = th.id"
            >
              <div class="flex items-center gap-2">
                <img
                  v-if="th.image_id"
                  :src="thumb(th.image_id)"
                  class="w-8 h-8 rounded-lg object-cover ring-1 ring-pink-200"
                  alt=""
                />
                <div class="min-w-0 flex-1">
                  <div class="flex items-center gap-1">
                    <span
                      v-if="kindBadge(th)"
                      class="text-[9px] px-1.5 py-0.5 rounded-full"
                      :class="th.kind === 'pitch'
                        ? 'bg-amber-200/80 text-amber-800'
                        : 'bg-pink-200/80 text-pink-700'"
                    >{{ kindBadge(th) }}</span>
                    <span class="text-xs font-medium truncate">{{ nameOf(th) }}</span>
                    <span v-if="th.liked" class="ml-auto text-[10px]">♥</span>
                  </div>
                  <div class="text-[10px] text-rose-400/80 truncate">{{ textOf(th) }}</div>
                </div>
              </div>
            </button>
            <p v-if="!feedThreads.length && !loading" class="text-xs text-rose-400 text-center py-8 px-2">
              {{ tab === 'ideas' ? t('muse.lounge.ideasEmpty') : t('muse.lounge.empty') }}
            </p>
          </div>

          <div class="flex-1 min-w-0 flex flex-col">
            <div class="flex-1 overflow-y-auto p-4 space-y-3">
              <p v-if="tab === 'ideas'" class="text-xs text-rose-500/80">{{ t('muse.lounge.ideasBlurb') }}</p>
              <template v-if="selected">
                <div class="flex items-start gap-3">
                  <img
                    v-if="selected.image_id"
                    :src="thumb(selected.image_id)"
                    class="w-20 h-28 rounded-2xl object-cover shadow-md ring-2 ring-white"
                    alt=""
                  />
                  <div class="min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-sm font-semibold text-rose-600">{{ nameOf(selected) }}</span>
                      <span
                        v-if="kindBadge(selected)"
                        class="text-[10px] px-2 py-0.5 rounded-full"
                        :class="selected.kind === 'pitch'
                          ? 'bg-amber-200/80 text-amber-800'
                          : 'bg-pink-200/80 text-pink-700'"
                      >{{ kindBadge(selected) }}</span>
                      <span
                        v-if="selected.status === 'promoted'"
                        class="text-[10px] px-2 py-0.5 rounded-full bg-teal-200/80 text-teal-800"
                      >{{ t('muse.lounge.statusPromoted') }}</span>
                    </div>
                    <div class="text-[10px] text-rose-400">{{ when(selected.created_at) }}</div>
                    <p class="mt-2 text-sm leading-relaxed whitespace-pre-wrap">{{ textOf(selected) }}</p>
                    <button
                      v-if="selected.kind === 'pitch'"
                      type="button"
                      class="mt-3 text-xs px-3 py-1 rounded-full border"
                      :class="selected.liked
                        ? 'bg-rose-400 text-white border-rose-400'
                        : 'bg-white/80 text-rose-500 border-rose-200 hover:border-rose-400'"
                      :disabled="liking"
                      @click="toggleLike(selected)"
                    >{{ selected.liked ? '♥ ' + t('muse.lounge.liked') : '♡ ' + t('muse.lounge.like') }}</button>
                  </div>
                </div>
                <div
                  v-for="m in (selected.messages || []).slice(1)"
                  :key="m.id"
                  class="ml-6 rounded-2xl px-3 py-2 border"
                  :class="m.role === 'director'
                    ? 'bg-amber-50/90 border-amber-200'
                    : 'bg-white/70 border-pink-100'"
                >
                  <div class="flex items-center gap-1.5 text-xs font-medium text-rose-500">
                    <span v-if="m.reaction">{{ m.reaction }}</span>
                    <span>{{ isJa ? (m.name_ja || m.name) : (m.name || m.name_ja) }}</span>
                    <span
                      v-if="m.stance"
                      class="text-[9px] px-1.5 rounded-full bg-pink-100 text-pink-600"
                    >{{ stanceLabel(m.stance) }}</span>
                  </div>
                  <p class="text-sm mt-1 whitespace-pre-wrap">{{ textOf(m) }}</p>
                  <p v-if="m.twist" class="text-[11px] mt-1 text-rose-500/90">
                    {{ t('muse.lounge.myTwist') }}: {{ m.twist }}
                  </p>
                </div>
              </template>
              <p v-else class="text-sm text-rose-400 text-center py-16">
                {{ t('muse.lounge.pickThread') }}
              </p>
            </div>
          </div>
        </div>

        <!-- trends / look of the week -->
        <div v-else-if="tab === 'trends'" class="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
          <p class="text-xs text-rose-500/80">{{ t('muse.lounge.trendsBlurb') }}</p>
          <article
            v-for="tr in trends"
            :key="tr.id"
            class="rounded-2xl border border-pink-200/80 bg-white/70 p-3 shadow-sm"
          >
            <div class="text-[10px] text-rose-400">{{ when(tr.at) }}</div>
            <h3 class="text-sm font-semibold text-rose-600 mt-0.5">
              {{ isJa ? (tr.summary_ja || tr.summary_en) : (tr.summary_en || tr.summary_ja) }}
            </h3>
            <div v-if="tr.from_name_ja || tr.from_name" class="text-[11px] text-rose-400 mt-1">
              {{ t('muse.lounge.fromMuse') }}:
              {{ isJa ? (tr.from_name_ja || tr.from_name) : (tr.from_name || tr.from_name_ja) }}
            </div>
            <div v-if="tr.tags && Object.keys(tr.tags).length" class="flex flex-wrap gap-1 mt-2">
              <span
                v-for="(v, k) in tr.tags"
                :key="k"
                class="text-[10px] px-2 py-0.5 rounded-full bg-pink-100 text-pink-700"
              >{{ k }}: {{ v }}</span>
            </div>
            <div v-if="tr.twists?.length" class="mt-2 space-y-1.5">
              <div class="text-[11px] font-medium text-rose-500">{{ t('muse.lounge.arrangements') }}</div>
              <div
                v-for="(tw, i) in tr.twists"
                :key="i"
                class="rounded-xl bg-rose-50/80 border border-pink-100 px-2.5 py-1.5 text-xs"
              >
                <span class="font-medium text-rose-600">
                  {{ isJa ? (tw.name_ja || tw.name) : (tw.name || tw.name_ja) }}
                </span>
                <span class="ml-1 text-[9px] px-1.5 rounded-full bg-white text-pink-600">
                  {{ stanceLabel(tw.stance) }}
                </span>
                <p class="mt-0.5 text-slate-700">
                  {{ tw.twist || (isJa ? tw.text_ja : tw.text_en) || tw.text_ja }}
                </p>
              </div>
            </div>
          </article>
          <p v-if="!trends.length && !loading" class="text-sm text-rose-400 text-center py-12">
            {{ t('muse.lounge.trendsEmpty') }}
          </p>
        </div>

        <!-- handpost — read-only. Notices arrive from Muse habit jobs. -->
        <div v-else class="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
          <article
            v-for="p in pages"
            :key="p.id"
            class="rounded-2xl border border-pink-200/80 bg-white/70 p-3 shadow-sm relative"
          >
            <div
              v-if="p.pinned"
              class="absolute -top-2 right-4 text-[10px] bg-rose-400 text-white px-2 py-0.5 rounded-full shadow"
            >📌 {{ t('muse.lounge.pinned') }}</div>
            <div
              v-if="p.kind === 'habit'"
              class="absolute -top-2 left-4 text-[10px] bg-violet-400 text-white px-2 py-0.5 rounded-full shadow"
            >{{ t('muse.lounge.badgeHabit') }}</div>
            <h3 class="text-sm font-semibold text-rose-600 pr-16">
              {{ (isJa ? (p.title_ja || p.title) : (p.title_en || p.title)) || t('muse.lounge.untitled') }}
            </h3>
            <p class="mt-1 text-sm whitespace-pre-wrap text-slate-700">
              {{ isJa ? (p.body_ja || p.body_en) : (p.body_en || p.body_ja) }}
            </p>
          </article>

          <p v-if="!pages.length && !loading" class="text-sm text-rose-400 text-center py-12">
            {{ t('muse.lounge.handpostEmpty') }}
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lounge-chan {
  text-align: left;
  border-radius: 999px;
  padding: 0.45rem 0.75rem;
  font-size: 0.8rem;
  color: #be185d;
  background: rgba(255, 255, 255, 0.45);
  border: 1px solid transparent;
}
.lounge-chan.is-on {
  background: rgba(251, 207, 232, 0.9);
  border-color: rgba(244, 114, 182, 0.5);
  font-weight: 600;
}
</style>

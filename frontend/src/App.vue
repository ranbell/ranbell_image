<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { saveAndSyncToken, getToken } from './apiToken.js'
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force'
import AnalyzerModal from './components/AnalyzerModal.vue'
import AdminModal from './components/AdminModal.vue'
import InspirePanel from './components/InspirePanel.vue'
import InvokePanel from './components/InvokePanel.vue'
import ControlRoom from './components/ControlRoom.vue'
import ProgressBar from './components/ProgressBar.vue'
import { useControlRoom } from './composables/useControlRoom.js'
import { useInspireSession } from './composables/useInspireSession.js'
import { useInvokeSession } from './composables/useInvokeSession.js'

const { fetchDaily: fetchDailyOracle } = useInvokeSession()

const { t, locale } = useI18n()
function toggleLocale() {
  locale.value = locale.value === 'ja' ? 'en' : 'ja'
  localStorage.setItem('locale', locale.value)
}

// ── Toast ────────────────────────────────────────────────────────────────────
const toast = ref(null)  // { msg, type: 'info'|'success'|'error' }
let _toastTimer = null
function showToast(msg, type = 'info', duration = 3500) {
  clearTimeout(_toastTimer)
  toast.value = { msg, type }
  _toastTimer = setTimeout(() => { toast.value = null }, duration)
}

// ── Core state ────────────────────────────────────────────────────────────────
const images = ref([])
const total = ref(0)
const nextCursor = ref(null)
const LIMIT = 100
const loading = ref(false)
const hasMore = ref(true)

// ── Backend readiness ─────────────────────────────────────────────────────────
const backendStatus = ref('connecting') // 'connecting' | 'starting' | 'ready'
const backendActivity = ref(null)       // { job, scan } from /health when running
const dismissedWarnings = ref(false)

async function waitForBackend() {
  while (true) {
    try {
      const r = await fetch('/api/health')
      if (r.ok) {
        const data = await r.json()
        backendActivity.value = data
        if (data.ready) {
          backendStatus.value = 'ready'
          return
        }
        backendStatus.value = 'starting'
      } else {
        backendStatus.value = 'connecting'
      }
    } catch {
      backendStatus.value = 'connecting'
    }
    await new Promise(res => setTimeout(res, 1500))
  }
}

// ── Job stream ────────────────────────────────────────────────────────────────
const jobsMap = ref(new Map())   // job.id -> job dict
let _jobEventSource = null

// ── Control Room ──────────────────────────────────────────────────────────────
const controlRoomVisible = ref(false)
const resourcesRef = ref([])
const disksRef = ref([])
const diskCautionPct = ref(75)
const diskFaultPct = ref(90)
const jobStreamConnected = ref(false)
const cr = useControlRoom(jobsMap, resourcesRef)
const { masterStatus, systemStatus, ingestEvent: crIngestEvent } = cr

const hasAnyActiveJob = computed(() =>
  [...jobsMap.value.values()].some(j => j.state === 'running' || j.state === 'cancelling')
)

const SCAN_TITLES = new Set(['scan_heal', 'scan_full', 'meta_update'])
const PIPELINE_TITLES = new Set(['ai_pipeline', 'ai_pipeline_auto', 'ai_pipeline_post_scan', 'ai_pipeline_continue'])

// SYNC lane scan jobs (running / cancelling / queued)
const scanState = computed(() =>
  [...jobsMap.value.values()].find(
    j => SCAN_TITLES.has(j.title) &&
         (j.state === 'running' || j.state === 'cancelling' || j.state === 'queued')
  ) || null
)

const selected = ref(null)
const wd14Copied = ref(false)
const showAiResetConfirm = ref(false)
const sentinel = ref(null)
const mainEl = ref(null)

const showInfo = ref(false)
const info = ref(null)

// ── Search & filter ───────────────────────────────────────────────────────────
const searchQuery = ref('')
const sortOrder = ref(localStorage.getItem('sortOrder') || 'newest')
const tags = ref([])
const tagsFilter = ref({}) // { "tag": "include" } — selected tags pool
const tagSearch = ref('')
const tagsExpanded = ref(false)
const tagLogic = ref('and')         // 'and' | 'or'
const searchMode = ref('semantic')   // 'keyword' | 'semantic'
const activeModels = ref([])        // selected model names (OR logic)
const modelFilteredTagSet = ref(null) // Set<string> | null — tags in model-only filtered results
const starFilter = ref(null)        // null | 1..5 — ≥N stars filter
const categoryFilter = ref('all')   // 'all' | 'AI' | 'NR'
const alignMinFilter = ref(null)    // null | 0.6 | 0.7 | 0.8
const colorPickerVisible = ref(false)

// ── Color Picker search ────────────────────────────────────────────────────────
const colorPickHex = ref('#ff6b6b')         // selected hex color
const colorPickDistance = ref(20)           // CIE76 ΔE distance threshold
const colorPickExcludeOpposite = ref(false) // exclude opposite hues
const colorPickLoading = ref(false)
const colorPickActive = ref(false)          // true when color pick results are displayed
const modelFacets = ref([])         // [{model, count}, ...] from /api/images/facets
const modelsExpanded = ref(false)   // show all models vs top 8

// ── Folder view ───────────────────────────────────────────────────────────────
const viewMode = ref(localStorage.getItem('viewMode') || 'flat')  // 'flat' | 'folder'
const activeDir = ref(null)    // null | string (path_rel)
const dirs = ref([])
const dirsLoading = ref(false)

const SORT_OPTIONS = computed(() => [
  { value: 'newest',      label: t('header.sort.newest') },
  { value: 'oldest',      label: t('header.sort.oldest') },
  { value: 'rating_desc', label: t('header.sort.ratingDesc') },
  { value: 'align_desc',  label: t('header.sort.alignDesc') },
  { value: 'name_asc',    label: t('header.sort.nameAsc') },
  { value: 'name_desc',   label: t('header.sort.nameDesc') },
  { value: 'size_desc',   label: t('header.sort.sizeDesc') },
  { value: 'size_asc',    label: t('header.sort.sizeAsc') },
])

function setSort(val) {
  sortOrder.value = val
  localStorage.setItem('sortOrder', val)
  fetchImages(true)
}

// ── Tag autocomplete ──────────────────────────────────────────────────────────
const tagSuggestions = ref([])
const showSuggestions = ref(false)
const suggestionIndex = ref(-1)
let suggestTimer = null

// ── AI state ──────────────────────────────────────────────────────────────────
const aiStatus = ref(null)

// EMBEDDING lane AI pipeline job
const pipelineState = computed(() =>
  [...jobsMap.value.values()].find(
    j => PIPELINE_TITLES.has(j.title) &&
         (j.state === 'running' || j.state === 'queued' || j.state === 'cancelling')
  ) || null
)

// All jobs (created_at is epoch seconds as float)
const allJobs = computed(() =>
  Array.from(jobsMap.value.values())
    .sort((a, b) => b.created_at - a.created_at)
)

// Active jobs other than scan / pipeline / generation
const otherActiveJobs = computed(() =>
  Array.from(jobsMap.value.values()).filter(j =>
    (j.state === 'running' || j.state === 'queued' || j.state === 'cancelling') &&
    !SCAN_TITLES.has(j.title) &&
    !PIPELINE_TITLES.has(j.title) &&
    j.lane !== 'gen'
  )
)

// Header job summary: first job + remaining count
const headerActiveJobs = computed(() => {
  const list = []
  if (scanState.value) list.push(scanState.value)
  if (refining.value || refinePhase.value === 'comfy')
    list.push({ id: '__refine__', title: 'refine', state: 'running', progress_text: '', progress: 0 })
  if (inspireRunning.value)
    list.push({ id: '__inspire__', title: 'inspire', state: 'running', progress_text: '', progress: 0 })
  list.push(...otherActiveJobs.value)
  return list
})
// ── Alignment ─────────────────────────────────────────────────────────────────
const alignmentCache = ref(new Map())        // sha256 -> record | null
const alignmentEvaluating = ref(new Map())  // sha256 -> job_id (while queued/running)

// Batch-fetch alignment for currently displayed images and populate cache (fire-and-forget)
async function fetchAlignmentsForImages(imgs) {
  const uncached = imgs.map(i => i.sha256).filter(s => !alignmentCache.value.has(s))
  if (!uncached.length) return
  try {
    const r = await fetch('/api/alignment/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha256s: uncached }),
    })
    if (!r.ok) return
    const records = await r.json()  // { sha256: record }
    const next = new Map(alignmentCache.value)
    for (const sha256 of uncached) next.set(sha256, records[sha256] ?? null)
    alignmentCache.value = next
  } catch {}
}

async function loadAlignment(sha256) {
  if (alignmentCache.value.has(sha256)) return
  try {
    const r = await fetch(`/api/alignment/${sha256}`)
    const record = r.ok ? await r.json() : null
    alignmentCache.value = new Map(alignmentCache.value).set(sha256, record)
  } catch {
    alignmentCache.value = new Map(alignmentCache.value).set(sha256, null)
  }
}

async function navigateToSource(sha256) {
  const found = images.value.find(i => i.sha256 === sha256)
  if (found) {
    selected.value = found
    return
  }
  try {
    const r = await fetch(`/api/images/${sha256}`)
    if (r.ok) selected.value = await r.json()
  } catch (e) {
    console.error('navigateToSource failed', e)
  }
}

async function triggerAlignmentEvaluate(sha256) {
  if (alignmentEvaluating.value.has(sha256)) return
  try {
    const r = await fetch(`/api/alignment/evaluate/single/${sha256}`, { method: 'POST' })
    if (r.ok) {
      const { job_id } = await r.json()
      // Record job_id so we can remove it when completion is detected via SSE
      alignmentEvaluating.value = new Map(alignmentEvaluating.value).set(sha256, job_id)
    }
  } catch (e) {
    console.error('Alignment evaluate failed', e)
  }
}

const selectedIds = ref(new Set())
const selectionMode = computed(() => selectedIds.value.size > 0)
const hoveredThumbnailSha = ref(null)
const hoveredThumbnailStyle = ref({})
const bucketHovered = ref(false)
const bucketBadgeRef = ref(null)
const bucketPopupStyle = ref({})

function onBucketMouseEnter() {
  bucketHovered.value = true
  if (bucketBadgeRef.value) {
    const rect = bucketBadgeRef.value.getBoundingClientRect()
    bucketPopupStyle.value = {
      bottom: (window.innerHeight - rect.top + 10) + 'px',
      left: rect.left + 'px'
    }
  }
}

function onThumbMouseEnter(event, sha) {
  hoveredThumbnailSha.value = sha
  const rect = event.currentTarget.getBoundingClientRect()
  hoveredThumbnailStyle.value = {
    bottom: (window.innerHeight - rect.top + 10) + 'px',
    left: (rect.left + rect.width / 2) + 'px',
    transform: 'translateX(-50%)'
  }
}
const showRefine = ref(false)
const refineOverlayMousedownOnBg = ref(false)
const refineInstruction = ref('')
const showInstructionModal = ref(false)
const refineDirectPrompt = ref(null)          // null = via VLM, string = bypass
const refineDirectNegativePrompt = ref('')    // negative prompt for direct submission
const refineDirectPromptSource = ref('')      // 'inversion' | 'inversion-prose' | ''
const refineInspireContext = ref(null)        // inspire_context passed from InspirePanel
const refinedPrompt = ref('')
const refining = ref(false)
let refineAbortController = null

// ── Refine settings ────────────────────────────────────────────────────────────
const refineTemp = ref(0.7)
const refineNumCtx = ref(16384)
const refineStyle = ref('natural')           // 'natural' | 'danbooru' | 'detailed'
const refineInstructionMode = ref('basic')   // 'none' | 'basic' | 'enhanced'
const refineNegative = ref(false)
const refineAutoSubmit = ref(false)
const refineBatchCount = ref(1)
const refineWorkflow = ref('')
const refinePosNodeId = ref('')
const refineNegNodeId = ref('')
const refineUseSeed = ref(false)

// Seed of the first reference image (used for display when use_ref_seed is ON)
const refFirstImageSeed = computed(() => {
  if (!refineUseSeed.value) return null
  const firstSha = [...selectedIds.value][0]
  if (!firstSha) return null
  const img = images.value.find(i => i.sha256 === firstSha)
  return img?.model_info?.seed ?? null
})

// ── Refine streaming state ─────────────────────────────────────────────────────
const randomCount = ref(3)
const pinnedShas = ref(new Set())
const imageWeights = ref(new Map())   // Map<sha256, number>  0–100 (integer)
const refinePhase = ref('')             // '' | 'llm' | 'comfy' | 'done'
const refinePromptJobId = ref(null)     // PROMPT lane job ID (the refinement itself)
const refinePromptJob = computed(() =>  // reactive reference from jobsMap
  refinePromptJobId.value ? (jobsMap.value.get(refinePromptJobId.value) ?? null) : null
)
const refineGenJobId = ref(null)        // ComfyUI job ID dispatched via spooler
const refineGenJob = computed(() =>     // reactive reference from jobsMap
  refineGenJobId.value ? (jobsMap.value.get(refineGenJobId.value) ?? null) : null
)
const refineStarted = ref(false)
const thinkText = ref('')
const thinkOpen = ref(false)
const streamingText = ref('')
const positivePrompt = ref('')
const negativePromptText = ref('')
const refineTagFormat = ref('underscore') // 'space' | 'underscore'

function fmtPrompt(text) {
  if (!text) return text
  return refineTagFormat.value === 'space' ? text.replace(/_/g, ' ') : text
}
const comfyProgress = ref({ value: 0, max: 0, node: '' })
const comfyExecuting = ref('')
const comfyGeneratedImages = ref([])   // sha256 list of saved generated images
const refineErrorMsg = ref('')
const proseMissing = ref(false)
const removedTags = ref([])
const workflows = ref([])

// ── Lightbox ──────────────────────────────────────────────────────────────────
const showLightbox = ref(false)
const lbZoom = ref(1)
const lbPanX = ref(0)
const lbPanY = ref(0)
const lbDragging = ref(false)
const lbImageRef = ref(null)
let lbDragOrigin = { x: 0, y: 0 }
let lbPanOrigin = { x: 0, y: 0 }
const lbContainerRef = ref(null)
const lbImgReady = ref(true)
let _lbImgReadyTimer = null

function _markImgReady() {
  lbImgReady.value = true
  clearTimeout(_lbImgReadyTimer)
  _lbImgReadyTimer = null
}
function _markImgPending() {
  lbImgReady.value = false
  clearTimeout(_lbImgReadyTimer)
  _lbImgReadyTimer = setTimeout(_markImgReady, 5000)
}

function lbFitZoom(imgEl) {
  if (imgEl?.naturalWidth && imgEl?.naturalHeight) {
    lbZoom.value = Math.min(window.innerWidth / imgEl.naturalWidth, window.innerHeight / imgEl.naturalHeight)
  }
  lbPanX.value = 0; lbPanY.value = 0
}

async function openLightbox() {
  showLightbox.value = true
  lbZoom.value = 1; lbPanX.value = 0; lbPanY.value = 0
  await nextTick()
  lbContainerRef.value?.focus()
  if (lbImageRef.value?.complete && lbImageRef.value?.naturalWidth) {
    _markImgReady()
    lbFitZoom(lbImageRef.value)
  }
}

function closeLightbox() { showLightbox.value = false }

function lbOnWheel(e) {
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
  const newZoom = Math.max(0.1, Math.min(10, lbZoom.value * factor))
  const rect = e.currentTarget.getBoundingClientRect()
  const mx = e.clientX - rect.width / 2
  const my = e.clientY - rect.height / 2
  lbPanX.value = mx - (mx - lbPanX.value) * (newZoom / lbZoom.value)
  lbPanY.value = my - (my - lbPanY.value) * (newZoom / lbZoom.value)
  lbZoom.value = newZoom
}

function lbOnMousedown(e) {
  if (e.button !== 0) return
  lbDragging.value = true
  lbDragOrigin = { x: e.clientX, y: e.clientY }
  lbPanOrigin = { x: lbPanX.value, y: lbPanY.value }
}

function lbOnMousemove(e) {
  if (!lbDragging.value) return
  lbPanX.value = lbPanOrigin.x + e.clientX - lbDragOrigin.x
  lbPanY.value = lbPanOrigin.y + e.clientY - lbDragOrigin.y
}

function lbOnMouseup() { lbDragging.value = false }

function lbReset() { lbZoom.value = 1; lbPanX.value = 0; lbPanY.value = 0 }

function lbOnKey(e) {
  if (e.key === 'Escape') closeLightbox()
  else if (e.key === '+' || e.key === '=') lbZoom.value = Math.min(10, lbZoom.value * 1.2)
  else if (e.key === '-') lbZoom.value = Math.max(0.1, lbZoom.value / 1.2)
  else if (e.key === '0') lbReset()
  else if (e.key === 'ArrowLeft') { e.preventDefault(); prevImage() }
  else if (e.key === 'ArrowRight') { e.preventDefault(); nextImage() }
}

// ── Admin ─────────────────────────────────────────────────────────────────────
const showAdmin = ref(false)

function openAdmin() { showAdmin.value = true }

function copyWd14Tags() {
  if (!selected.value?.wd14_tags?.length) return
  copyToClipboard(selected.value.wd14_tags.join(', '))
  wd14Copied.value = true
  setTimeout(() => { wd14Copied.value = false }, 1500)
}

async function resetAI(sha256s) {
  await fetch('/api/ai/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sha256s }),
  })
  images.value = images.value.map(img =>
    sha256s.includes(img.sha256)
      ? { ...img, embedding_status: 'pending', wd14_tags: [] }
      : img
  )
  if (selected.value && sha256s.includes(selected.value.sha256)) {
    selected.value = { ...selected.value, embedding_status: 'pending', wd14_tags: [] }
  }
}

let observer = null
let searchTimer = null

// ── Similar search ────────────────────────────────────────────────────────────
const similarSource = ref(null)   // source image doc when in similar mode
const similarThumbHover = ref(false)
const similarLoading = ref(false)
const colorSimilarLoading = ref(false)

async function findSimilar(img) {
  similarLoading.value = true
  selected.value = null
  try {
    const res = await fetch('/api/ai/similar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha256: img.sha256, n_results: 48 }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    similarSource.value = img
    images.value = data.results
    total.value = images.value.length
    hasMore.value = false
    nextCursor.value = null
    mainEl.value?.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (e) {
    console.error('Similar search error:', e)
  } finally {
    similarLoading.value = false
  }
}

async function findSimilarColor(img) {
  colorSimilarLoading.value = true
  selected.value = null
  try {
    const res = await fetch(`/api/images/color-like/${img.sha256}?limit=100`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    similarSource.value = { ...img, _colorMode: true }
    images.value = data.results
    total.value = images.value.length
    hasMore.value = false
    nextCursor.value = null
    mainEl.value?.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (e) {
    console.error('Color similar search error:', e)
  } finally {
    colorSimilarLoading.value = false
  }
}

function clearSimilar() {
  similarSource.value = null
  colorPickActive.value = false
  fetchImages(true)
}

function searchByPaletteColor(hex) {
  selected.value = null
  colorPickHex.value = hex
  doColorPick()
}

async function doColorPick() {
  colorPickLoading.value = true
  try {
    const params = new URLSearchParams({
      hex_color: colorPickHex.value,
      distance: colorPickDistance.value,
      exclude_opposite: colorPickExcludeOpposite.value,
      limit: 100,
    })
    const res = await fetch(`/api/images/color-pick?${params}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    similarSource.value = { _colorPickMode: true, _hex: colorPickHex.value }
    images.value = data.results
    total.value = images.value.length
    hasMore.value = false
    nextCursor.value = null
    colorPickActive.value = true
    mainEl.value?.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (e) {
    console.error('Color pick error:', e)
  } finally {
    colorPickLoading.value = false
  }
}

// ── Raw Metadata Modal ─────────────────────────────────────────────────────────
const rawMetadataModal = ref({ open: false, loading: false, error: null, sha256: null, sections: [], expanded: {} })

async function openRawMetadata(sha256) {
  rawMetadataModal.value = { open: true, loading: true, error: null, sha256, sections: [], expanded: {} }
  try {
    const res = await fetch(`/api/images/${sha256}/raw-metadata`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    rawMetadataModal.value.sections = data.sections
    // JSON sections default to collapsed
    const expanded = {}
    for (const sec of data.sections) {
      expanded[sec.key] = sec.type !== 'json'
    }
    rawMetadataModal.value.expanded = expanded
  } catch (e) {
    rawMetadataModal.value.error = t('detail.rawMetaLoadError') + ': ' + e.message
  } finally {
    rawMetadataModal.value.loading = false
  }
}

// ── Similarity Graph ───────────────────────────────────────────────────────────
const showSimilarityGraph = ref(false)
const graphLoading = ref(false)
const graphData = ref(null)
const graphRootSha = ref(null)
const graphCanvasRef = ref(null)
const graphDepth = ref(2)
const graphNeighbors = ref(6)
const graphNavigate = ref(true)   // true = click navigates to image
let _graphSourceImg = null

async function openSimilarityGraph(img) {
  selected.value = null
  graphLoading.value = true
  graphRootSha.value = img.sha256
  _graphSourceImg = img
  showSimilarityGraph.value = true
  graphData.value = null
  try {
    const res = await fetch(`/api/ai/graph/${img.sha256}?neighbors=${graphNeighbors.value}&depth=${graphDepth.value}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    graphData.value = await res.json()
  } catch (e) {
    console.error('Graph fetch error:', e)
    showToast(t('detail.graphLoadError'), 'error')
    showSimilarityGraph.value = false
  } finally {
    graphLoading.value = false
  }
}

watch([graphDepth, graphNeighbors], () => {
  if (_graphSourceImg && showSimilarityGraph.value) openSimilarityGraph(_graphSourceImg)
})

function closeSimilarityGraph() {
  showSimilarityGraph.value = false
  graphData.value = null
  graphRootSha.value = null
  _graphSourceImg = null
}

function openImageFromOracle(sha256) {
  showInvoke.value = false
  const img = images.value.find(i => i.sha256 === sha256)
  if (img) {
    selected.value = img
  } else {
    fetch(`/api/images/${sha256}`)
      .then(r => r.ok ? r.json() : null)
      .then(doc => { if (doc) selected.value = doc })
      .catch(() => {})
  }
}

function navigateToGraphNode(sha256) {
  const img = images.value.find(i => i.sha256 === sha256)
  if (img) {
    selected.value = img
  } else {
    fetch(`/api/images/${sha256}`)
      .then(r => r.ok ? r.json() : null)
      .then(doc => { if (doc) selected.value = doc })
      .catch(() => {})
  }
}

// ── Graph Clustering (Union-Find) ──────────────────────────────────────────────
const CLUSTER_THRESHOLD = 0.95   // edges above this score → same cluster

function _buildClusters(nodes, edges) {
  const parent = Object.fromEntries(nodes.map(n => [n.sha256, n.sha256]))
  function find(x) { return parent[x] === x ? x : (parent[x] = find(parent[x])) }
  function union(a, b) { const ra = find(a), rb = find(b); if (ra !== rb) parent[ra] = rb }

  edges.forEach(e => {
    if (e.score >= CLUSTER_THRESHOLD && parent[e.source] !== undefined && parent[e.target] !== undefined)
      union(e.source, e.target)
  })

  const nodeMap = Object.fromEntries(nodes.map(n => [n.sha256, n]))
  const groups = {}
  nodes.forEach(n => { const r = find(n.sha256); (groups[r] = groups[r] || []).push(n.sha256) })

  const clusterMap = {}, clusters = {}
  for (const members of Object.values(groups)) {
    const rep = members.find(s => nodeMap[s]?.is_root) || members[0]
    clusters[rep] = { id: rep, members, representative: rep }
    members.forEach(s => { clusterMap[s] = rep })
  }
  return { clusterMap, clusters }
}

function _buildSimData(graphData, clusterMap, clusters, expandedIds) {
  const { nodes, edges } = graphData
  const nodeMap = Object.fromEntries(nodes.map(n => [n.sha256, n]))
  const simNodes = [], processed = new Set()

  for (const node of nodes) {
    const cid = clusterMap[node.sha256]
    const cluster = clusters[cid]
    if (!cluster) continue
    if (cluster.members.length === 1) {
      const pos = _simNodePositions[node.sha256] || {}
      simNodes.push({ ...node, nodeId: node.sha256, isCluster: false, ...pos })
    } else if (expandedIds.has(cid)) {
      const clusterPos = _simNodePositions[`c_${cid}`] || {}
      const pos = _simNodePositions[node.sha256] || clusterPos
      simNodes.push({ ...node, nodeId: node.sha256, isCluster: false, clusterId: cid, ...pos })
    } else if (!processed.has(cid)) {
      processed.add(cid)
      const rep = nodeMap[cluster.representative]
      const pos = _simNodePositions[`c_${cid}`] || {}
      simNodes.push({
        ...rep, sha256: cluster.representative,
        nodeId: `c_${cid}`, isCluster: true, clusterId: cid,
        count: cluster.members.length,
        is_root: cluster.members.some(s => nodeMap[s]?.is_root),
        ...pos,
      })
    }
  }

  const nodeIdSet = new Set(simNodes.map(n => n.nodeId))
  function getNodeId(sha) {
    const cid = clusterMap[sha]; if (!cid) return sha
    const cl = clusters[cid]
    return (cl.members.length === 1 || expandedIds.has(cid)) ? sha : `c_${cid}`
  }

  const edgeMap = {}
  for (const e of edges) {
    const s = getNodeId(e.source), t = getNodeId(e.target)
    if (s === t || !nodeIdSet.has(s) || !nodeIdSet.has(t)) continue
    const key = [s, t].sort().join('|')
    if (!edgeMap[key] || edgeMap[key].score < e.score)
      edgeMap[key] = { source: s, target: t, score: e.score }
  }
  return { simNodes, simEdges: Object.values(edgeMap) }
}

// ── SSE streaming update throttle (≈15fps to minimize GPU rasterization) ────────
let _pendingTokens = ''
let _pendingThink = ''
let _pendingProgress = null
let _streamingTimerId = null

function _flushStreamingUpdates() {
  if (_pendingTokens) { streamingText.value += _pendingTokens; _pendingTokens = '' }
  if (_pendingThink) { thinkText.value += _pendingThink; _pendingThink = '' }
  if (_pendingProgress) { comfyProgress.value = _pendingProgress; _pendingProgress = null }
}

function _scheduleFlush() {
  if (!_streamingTimerId) {
    _streamingTimerId = setTimeout(() => {
      _streamingTimerId = null
      _flushStreamingUpdates()
    }, 64)
  }
}

// ── Graph Canvas Rendering ─────────────────────────────────────────────────────
const NODE_RADIUS = 40
const ROOT_RADIUS = 64
const _imgCache = {}
let _graphSim = null
let _graphNodes = []
let _graphLinks = []
let _graphAdjacency = {}      // nodeId -> Set<nodeId>
let _graphHoveredNodeId = null
let _graphClusterMap = {}
let _graphClusters = {}
let _graphExpandedIds = new Set()
let _simNodePositions = {}
let _draggingNode = null
let _dragMoved = false
let _dragStartX = 0       // drag threshold origin
let _dragStartY = 0
const DRAG_THRESHOLD = 4  // px before drag is confirmed
let _pathNodes = new Set()    // nodeIds on highlighted path
let _pathEdges = new Set()    // "a|b" canonical keys on highlighted path
let _hoverPreviewNode = null  // node to show full preview for
let _hoverTimer = null
let _hoverCursorX = 0
let _hoverCursorY = 0

// Context menu for right-click on graph nodes
const graphContextMenu = ref(null)  // { screenX, screenY, node } | null

function closeGraphContextMenu() { graphContextMenu.value = null }

function graphCtxHighlightPath() {
  const node = graphContextMenu.value?.node
  closeGraphContextMenu()
  if (!node) return
  _highlightPath(node.nodeId)
  hasActivePath.value = _pathNodes.size > 0
  if (!hasActivePath.value) showToast(t('detail.graphNoPath'), 'info', 2500)
  if (_graphSim?.alpha() < 0.01) _graphSim.alpha(0.05).restart()
}

function graphCtxNavigate() {
  const node = graphContextMenu.value?.node
  closeGraphContextMenu()
  if (!node) return
  navigateToGraphNode(node.sha256)
}

const hasActivePath = ref(false)

function clearGraphPath() {
  _clearPath()
  hasActivePath.value = false
  if (_graphSim?.alpha() < 0.01) _graphSim.alpha(0.02).restart()
}

function _graphNodeAt(x, y) {
  for (const node of _graphNodes) {
    if (!node.x) continue
    const dx = node.x - x, dy = node.y - y
    const nr = node.is_root ? ROOT_RADIUS : NODE_RADIUS
    if (dx * dx + dy * dy <= nr * nr) return node
  }
  return null
}

function _badgeAt(x, y) {
  for (const node of _graphNodes) {
    if (!node.x) continue
    const isExpandedRep = !!node.clusterId && node.sha256 === node.clusterId
    if (!node.isCluster && !isExpandedRep) continue
    const r = node.is_root ? ROOT_RADIUS : NODE_RADIUS
    const bx = node.x + r * 0.68, by = node.y - r * 0.68
    const dx = x - bx, dy = y - by
    if (dx * dx + dy * dy <= 13 * 13) return node
  }
  return null
}

function _savePositions() {
  _graphNodes.forEach(n => { if (n.x) _simNodePositions[n.nodeId] = { x: n.x, y: n.y } })
}

function _clearPath() {
  _pathNodes = new Set()
  _pathEdges = new Set()
}

function _highlightPath(fromNodeId) {
  // Root can be a normal node or a cluster representative — find by is_root flag
  const rootNode = _graphNodes.find(n => n.is_root)
  if (!rootNode) { _clearPath(); return }
  const rootId = rootNode.nodeId
  if (fromNodeId === rootId) { _clearPath(); return }

  // BFS from fromNodeId → rootId using adjacency built from simEdges (nodeId strings)
  const queue = [[fromNodeId]]
  const visited = new Set([fromNodeId])
  let found = null
  while (queue.length && !found) {
    const path = queue.shift()
    const cur = path[path.length - 1]
    if (cur === rootId) { found = path; break }
    for (const nb of (_graphAdjacency[cur] || new Set())) {
      if (!visited.has(nb)) { visited.add(nb); queue.push([...path, nb]) }
    }
  }
  if (!found) { _clearPath(); return }
  _pathNodes = new Set(found)
  _pathEdges = new Set()
  for (let i = 0; i < found.length - 1; i++) {
    _pathEdges.add([found[i], found[i + 1]].sort().join('|'))
  }
}

function _startSim(canvas, data) {
  if (_graphSim) { _graphSim._cleanup?.(); _graphSim.stop(); _graphSim = null }
  if (!canvas || !data) return
  _clearPath()
  _hoverPreviewNode = null
  if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null }

  const W = canvas.clientWidth, H = canvas.clientHeight
  canvas.width = W; canvas.height = H
  const ctx = canvas.getContext('2d')

  const { simNodes, simEdges } = _buildSimData(data, _graphClusterMap, _graphClusters, _graphExpandedIds)
  _graphNodes = simNodes
  const idxById = Object.fromEntries(simNodes.map((n, i) => [n.nodeId, i]))
  _graphLinks = simEdges.map(e => ({
    source: idxById[e.source], target: idxById[e.target], score: e.score,
  })).filter(l => l.source !== undefined && l.target !== undefined)

  // Build adjacency for path finding (use nodeId strings before D3 mutates)
  _graphAdjacency = {}
  for (const e of simEdges) {
    if (!_graphAdjacency[e.source]) _graphAdjacency[e.source] = new Set()
    if (!_graphAdjacency[e.target]) _graphAdjacency[e.target] = new Set()
    _graphAdjacency[e.source].add(e.target)
    _graphAdjacency[e.target].add(e.source)
  }

  Promise.all(_graphNodes.map(n => {
    if (_imgCache[n.sha256]?.complete) return Promise.resolve()
    return new Promise(resolve => {
      const img = new Image()
      img.onload = img.onerror = () => { _imgCache[n.sha256] = img; resolve() }
      img.src = `/api/thumbnails/${n.sha256}.webp`
    })
  })).then(() => { if (_graphSim) _graphSim.alpha(0.1).restart() })

  function draw() {
    ctx.clearRect(0, 0, W, H)

    // Edges — draw path-highlighted first (on top), then normal
    for (const link of _graphLinks) {
      const src = typeof link.source === 'object' ? link.source : _graphNodes[link.source]
      const tgt = typeof link.target === 'object' ? link.target : _graphNodes[link.target]
      if (!src?.x || !tgt?.x) continue
      const edgeKey = [src.nodeId, tgt.nodeId].sort().join('|')
      const onPath = _pathEdges.has(edgeKey)
      ctx.beginPath()
      ctx.moveTo(src.x, src.y); ctx.lineTo(tgt.x, tgt.y)
      if (onPath) {
        ctx.strokeStyle = '#fbbf24'
        ctx.lineWidth = 4
      } else {
        const opacity = _pathEdges.size > 0 ? 0.12 : (0.25 + (link.score - 0.5) * 0.6)
        ctx.strokeStyle = `rgba(139,92,246,${opacity.toFixed(2)})`
        ctx.lineWidth = Math.max(1, (link.score - 0.5) * 6)
      }
      ctx.stroke()
    }

    // Nodes
    for (const node of _graphNodes) {
      if (!node.x) continue
      const isRoot = node.is_root
      const isHovered = node.nodeId === _graphHoveredNodeId
      const isExpandedMember = !!node.clusterId
      const isExpandedRep = isExpandedMember && node.sha256 === node.clusterId
      const onPath = _pathNodes.has(node.nodeId)
      const r = isRoot ? ROOT_RADIUS : NODE_RADIUS

      // Outer glow
      if (isRoot || isHovered || isExpandedMember || onPath) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + (onPath ? 8 : 5), 0, Math.PI * 2)
        ctx.fillStyle = onPath
          ? 'rgba(251,191,36,0.3)'
          : isRoot
            ? 'rgba(139,92,246,0.3)'
            : isExpandedMember
              ? 'rgba(245,158,11,0.15)'
              : 'rgba(255,255,255,0.12)'
        ctx.fill()
      }

      // Thumbnail (aspect-ratio cover)
      ctx.save()
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
      ctx.clip()
      const img = _imgCache[node.sha256]
      if (img?.complete && img.naturalWidth) {
        const iw = img.naturalWidth, ih = img.naturalHeight
        const scale = Math.max(r * 2 / iw, r * 2 / ih)
        const sw = iw * scale, sh = ih * scale
        ctx.drawImage(img, node.x - sw / 2, node.y - sh / 2, sw, sh)
      } else {
        ctx.fillStyle = '#374151'; ctx.fill()
      }
      // Gray overlay for non-path nodes when path is active
      if (_pathNodes.size > 0 && !onPath) {
        ctx.fillStyle = 'rgba(10,10,15,0.68)'
        ctx.fill()
      }
      ctx.restore()

      // Ring
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
      if (onPath) {
        ctx.strokeStyle = '#fbbf24'
        ctx.lineWidth = 3
      } else if (node.isCluster) {
        ctx.setLineDash([6, 3])
        ctx.strokeStyle = isHovered ? '#fcd34d' : '#f59e0b'
        ctx.lineWidth = 3
      } else if (isExpandedMember) {
        ctx.setLineDash([4, 3])
        ctx.strokeStyle = isHovered ? '#fcd34d' : '#f59e0b'
        ctx.lineWidth = isExpandedRep ? 3 : 2
      } else {
        ctx.strokeStyle = isRoot ? '#8b5cf6' : (isHovered ? '#ffffff' : '#4b5563')
        ctx.lineWidth = isRoot ? 3 : 1.5
      }
      ctx.stroke()
      ctx.setLineDash([])
    }

    // Badges drawn in a second pass so no node thumbnail can cover them
    for (const node of _graphNodes) {
      if (!node.x) continue
      const isExpandedMemberB = !!node.clusterId
      const isExpandedRepB = isExpandedMemberB && node.sha256 === node.clusterId
      const rB = node.is_root ? ROOT_RADIUS : NODE_RADIUS
      if (node.isCluster) {
        const bx = node.x + rB * 0.68, by = node.y - rB * 0.68, br = 13
        ctx.beginPath(); ctx.arc(bx, by, br, 0, Math.PI * 2)
        ctx.fillStyle = '#f59e0b'; ctx.fill()
        ctx.fillStyle = '#000'; ctx.font = 'bold 11px sans-serif'
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
        ctx.fillText(node.count, bx, by)
      } else if (isExpandedRepB) {
        const bx = node.x + rB * 0.68, by = node.y - rB * 0.68, br = 13
        const isHovB = node.nodeId === _graphHoveredNodeId
        ctx.beginPath(); ctx.arc(bx, by, br, 0, Math.PI * 2)
        ctx.fillStyle = isHovB ? '#fcd34d' : '#f59e0b'; ctx.fill()
        ctx.fillStyle = '#000'; ctx.font = 'bold 13px sans-serif'
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
        ctx.fillText('×', bx, by)
      }
    }

    // Hover preview popup (drawn last, on top of everything)
    if (_hoverPreviewNode) {
      const pimg = _imgCache[_hoverPreviewNode.sha256]
      if (pimg?.complete && pimg.naturalWidth) {
        const PREV_MAX = 260
        const iw = pimg.naturalWidth, ih = pimg.naturalHeight
        const scale = Math.min(PREV_MAX / iw, PREV_MAX / ih)
        const pw = Math.round(iw * scale), ph = Math.round(ih * scale)
        const pad = 8, nameH = 18
        const bw = pw + pad * 2, bh = ph + pad * 2 + nameH

        let bx = _hoverCursorX + 18
        let by = _hoverCursorY - bh / 2
        if (bx + bw > W - 4) bx = _hoverCursorX - bw - 18
        if (by < 4) by = 4
        if (by + bh > H - 4) by = H - bh - 4

        ctx.save()
        ctx.shadowColor = 'rgba(0,0,0,0.6)'
        ctx.shadowBlur = 16
        ctx.fillStyle = 'rgba(15,20,30,0.97)'
        ctx.beginPath()
        ctx.roundRect(bx, by, bw, bh, 10)
        ctx.fill()
        ctx.restore()

        ctx.strokeStyle = 'rgba(139,92,246,0.45)'
        ctx.lineWidth = 1.5
        ctx.beginPath()
        ctx.roundRect(bx, by, bw, bh, 10)
        ctx.stroke()

        ctx.drawImage(pimg, bx + pad, by + pad, pw, ph)

        const name = _hoverPreviewNode.name || ''
        ctx.fillStyle = '#9ca3af'
        ctx.font = '11px sans-serif'
        ctx.textAlign = 'left'
        ctx.textBaseline = 'top'
        ctx.fillText(name.length > 36 ? name.slice(0, 34) + '…' : name, bx + pad, by + pad + ph + 4)
      }
    }
  }

  // ResizeObserver
  const _resizeObserver = new ResizeObserver(() => {
    const newW = canvas.clientWidth, newH = canvas.clientHeight
    if (newW === W && newH === H) return
    _savePositions()
    _startSim(canvas, data)
  })
  _resizeObserver.observe(canvas.parentElement)

  // ── Drag detection (threshold-based, no long-press timer) ──────────────────
  const _onMousedown = e => {
    if (e.button !== 0) return
    // Close any open context menu
    if (graphContextMenu.value) { graphContextMenu.value = null; return }
    const rect = canvas.getBoundingClientRect()
    const node = _graphNodeAt(e.clientX - rect.left, e.clientY - rect.top)
    if (!node) {
      // Click on empty space — clear path if active
      if (_pathNodes.size > 0) { _clearPath(); hasActivePath.value = false }
      if (_graphSim?.alpha() < 0.01) _graphSim.alpha(0.02).restart()
      return
    }
    _draggingNode = node
    _dragMoved = false
    _dragStartX = e.clientX
    _dragStartY = e.clientY
    node.fx = node.x; node.fy = node.y
    _graphSim.alphaTarget(0.15).restart()
    e.preventDefault()
  }
  const _onMousemove = e => {
    if (!_draggingNode) return
    const dx = e.clientX - _dragStartX, dy = e.clientY - _dragStartY
    if (!_dragMoved && (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD)) {
      _dragMoved = true
    }
    if (_dragMoved) {
      const rect = canvas.getBoundingClientRect()
      _draggingNode.fx = e.clientX - rect.left
      _draggingNode.fy = e.clientY - rect.top
    }
  }
  const _onMouseup = () => {
    if (!_draggingNode) return
    _draggingNode.fx = null; _draggingNode.fy = null
    _graphSim.alphaTarget(0)
    if (_dragMoved) _graphSim.alpha(0.08).restart()  // settle after drag
    _draggingNode = null
  }

  // ── Right-click → context menu ──────────────────────────────────────────────
  const _onContextmenu = e => {
    e.preventDefault()
    const rect = canvas.getBoundingClientRect()
    const node = _graphNodeAt(e.clientX - rect.left, e.clientY - rect.top)
    if (!node) { graphContextMenu.value = null; return }
    const menuW = 224, menuH = 112   // approximate context menu dimensions
    const sx = Math.min(e.clientX, window.innerWidth - menuW - 8)
    const sy = Math.min(e.clientY, window.innerHeight - menuH - 8)
    graphContextMenu.value = { screenX: sx, screenY: sy, node }
  }

  canvas.addEventListener('mousedown', _onMousedown)
  canvas.addEventListener('mousemove', _onMousemove)
  canvas.addEventListener('mouseup', _onMouseup)
  canvas.addEventListener('mouseleave', _onMouseup)
  canvas.addEventListener('contextmenu', _onContextmenu)

  // Boundary force — keep nodes inside canvas
  const _boundaryForce = () => {
    const pad = ROOT_RADIUS + 12
    for (const node of _graphNodes) {
      if (!node.x) continue
      node.x = Math.max(pad, Math.min(W - pad, node.x))
      node.y = Math.max(pad, Math.min(H - pad, node.y))
    }
  }

  // ── Physics — optimised for fast convergence ────────────────────────────────
  _graphSim = forceSimulation(_graphNodes)
    .alphaDecay(0.12)   // was 0.05 → converges ~2.5× faster
    .force('link', forceLink(_graphLinks).id((_, i) => i).distance(d => 180 + (1 - d.score) * 160).strength(0.5))
    .force('charge', forceManyBody().strength(-400))  // was -800 → less repulsion
    .force('center', forceCenter(W / 2, H / 2).strength(0.06))  // was 0.04
    .force('collide', forceCollide(n => (n.is_root ? ROOT_RADIUS : NODE_RADIUS) + 20))
    .force('boundary', _boundaryForce)
    .on('tick', draw)
    .on('end', draw)

  _graphSim._cleanup = () => {
    _resizeObserver.disconnect()
    if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null }
    _hoverPreviewNode = null
    canvas.removeEventListener('mousedown', _onMousedown)
    canvas.removeEventListener('mousemove', _onMousemove)
    canvas.removeEventListener('mouseup', _onMouseup)
    canvas.removeEventListener('mouseleave', _onMouseup)
    canvas.removeEventListener('contextmenu', _onContextmenu)
  }
}

watch([graphData, graphCanvasRef], async ([data, canvas]) => {
  if (_graphSim) { _graphSim._cleanup?.(); _graphSim.stop(); _graphSim = null }
  _graphExpandedIds = new Set()
  _simNodePositions = {}
  _clearPath()
  if (data) {
    const { clusterMap, clusters } = _buildClusters(data.nodes, data.edges)
    _graphClusterMap = clusterMap
    _graphClusters = clusters
  }
  if (!data || !canvas) return
  await nextTick()
  _startSim(canvas, data)
}, { flush: 'post' })

watch(showSimilarityGraph, v => {
  if (!v && _graphSim) { _graphSim._cleanup?.(); _graphSim.stop(); _graphSim = null }
})

watch(selected, (img) => {
  if (img?.sha256) loadAlignment(img.sha256)
})

function onGraphCanvasClick(e) {
  // Ignore if it was a drag (moved beyond threshold)
  if (_dragMoved) { _dragMoved = false; return }
  // Close context menu on any left-click
  if (graphContextMenu.value) { graphContextMenu.value = null; return }
  const rect = graphCanvasRef.value.getBoundingClientRect()
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top
  // Badge click: expand collapsed cluster or collapse expanded cluster
  const badgeNode = _badgeAt(cx, cy)
  if (badgeNode) {
    _savePositions()
    if (badgeNode.isCluster) _graphExpandedIds.add(badgeNode.clusterId)
    else _graphExpandedIds.delete(badgeNode.clusterId)
    _startSim(graphCanvasRef.value, graphData.value)
    return
  }
  const node = _graphNodeAt(cx, cy)
  if (!node) {
    // Click on empty space → clear path highlight
    if (_pathEdges.size > 0) { _clearPath(); hasActivePath.value = false; if (_graphSim?.alpha() < 0.01) _graphSim.alpha(0.02).restart() }
    return
  }
  // Note: single-click on nodes no longer navigates — use right-click menu
}

function onGraphCanvasHover(e) {
  if (_draggingNode) return
  const rect = graphCanvasRef.value.getBoundingClientRect()
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top
  _hoverCursorX = cx; _hoverCursorY = cy
  const node = _graphNodeAt(cx, cy)
  const newId = node?.nodeId ?? null

  // Update cursor position for preview redraws (only if sim is already ticking)
  if (_hoverPreviewNode && _graphSim?.alpha() < 0.01) _graphSim.alpha(0.01).restart()

  if (newId !== _graphHoveredNodeId) {
    _graphHoveredNodeId = newId
    // Cursor: context-menu for normal nodes, pointer for clusters, default otherwise
    graphCanvasRef.value.style.cursor = node
      ? (node.isCluster || node.clusterId ? 'pointer' : 'context-menu')
      : 'default'

    // Reset hover preview
    _hoverPreviewNode = null
    if (_hoverTimer) { clearTimeout(_hoverTimer); _hoverTimer = null }
    if (node) {
      _hoverTimer = setTimeout(() => {
        _hoverPreviewNode = node
        if (_graphSim?.alpha() < 0.01) _graphSim.alpha(0.015).restart()
      }, 400)  // was 650ms
    }

    if (_graphSim && _graphSim.alpha() < 0.01) _graphSim.alpha(0.015).restart()
  }
}

// ── Computed ──────────────────────────────────────────────────────────────────
const activeTags = computed(() => Object.entries(tagsFilter.value).filter(e => e[1] === 'include').map(e => e[0]))
const isSearchMode = computed(() => !!searchQuery.value || !!Object.keys(tagsFilter.value).length || !!similarSource.value || !!activeModels.value.length || colorPickActive.value || !!starFilter.value || categoryFilter.value !== 'all' || alignMinFilter.value !== null)
const effectiveQuery = computed(() => searchQuery.value || activeTags.value[0] || '')
const selectedCount = computed(() => selectedIds.value.size)

// ── Fetch helpers ─────────────────────────────────────────────────────────────
async function fetchImages(reset = false) {
  if (loading.value) return
  if (!reset && !hasMore.value) return
  loading.value = true
  if (reset) { images.value = []; nextCursor.value = null; hasMore.value = true }
  try {
    if (searchMode.value === 'semantic' && searchQuery.value) {
      const res = await fetch('/api/ai/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: searchQuery.value, n_results: 50, sort: sortOrder.value }),
      })
      const data = await res.json()
      images.value = data.results || []
      total.value = images.value.length
      hasMore.value = false
      fetchAlignmentsForImages(images.value)
    } else {
      const params = new URLSearchParams({ limit: LIMIT, sort: sortOrder.value })
      if (nextCursor.value) params.set('cursor', nextCursor.value)
      if (searchQuery.value) params.set('q', searchQuery.value)
      const includes = [], excludes = []
      for (const [k, v] of Object.entries(tagsFilter.value)) {
        if (v === 'include') includes.push(k)
        else if (v === 'exclude') excludes.push(k)
      }
      if (includes.length) { params.set('tags_include', includes.join(',')); }
      if (excludes.length) { params.set('tags_exclude', excludes.join(',')); }
      if (includes.length > 1) params.set('tag_logic', tagLogic.value)
      if (activeDir.value !== null) params.set('dir', activeDir.value)
      if (activeModels.value.length) params.set('models', activeModels.value.join(','))
      if (starFilter.value) params.set('star_min', starFilter.value)
      if (categoryFilter.value !== 'all') params.set('category', categoryFilter.value)
      if (alignMinFilter.value !== null) params.set('align_min', alignMinFilter.value)
      const res = await fetch(`/api/images?${params}`)
      const data = await res.json()
      images.value.push(...data.images)
      total.value = data.total
      nextCursor.value = data.next_cursor
      hasMore.value = !!data.next_cursor
      fetchAlignmentsForImages(data.images || [])
      if (data.available_tags?.length) {
        modelFilteredTagSet.value = new Set(data.available_tags)
      } else {
        modelFilteredTagSet.value = null
      }
    }
  } finally {
    loading.value = false
  }
}

async function fetchDirs() {
  dirsLoading.value = true
  try {
    const res = await fetch('/api/dirs')
    if (res.ok) dirs.value = (await res.json()).dirs || []
  } finally {
    dirsLoading.value = false
  }
}

async function fetchFacets() {
  try {
    const res = await fetch('/api/images/facets')
    if (res.ok) { const d = await res.json(); modelFacets.value = d.models || [] }
  } catch { }
}

function toggleModel(name) {
  const idx = activeModels.value.indexOf(name)
  activeModels.value = idx === -1
    ? [...activeModels.value, name]
    : activeModels.value.filter(m => m !== name)
  fetchImages(true)
}

function clearModels() {
  activeModels.value = []
  fetchImages(true)
}

async function setImageRating(img, n) {
  const newRating = img.star_rating === n ? null : n
  await fetch(`/api/images/${img.sha256}/rating`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating: newRating }),
  })
  img.star_rating = newRating
  if (selected.value?.sha256 === img.sha256) {
    selected.value = { ...selected.value, star_rating: newRating }
  }
}

function toggleViewMode() {
  if (viewMode.value === 'flat') {
    viewMode.value = 'folder'
    localStorage.setItem('viewMode', 'folder')
    activeDir.value = null
    fetchDirs()
  } else {
    viewMode.value = 'flat'
    localStorage.setItem('viewMode', 'flat')
    activeDir.value = null
    dirs.value = []
    fetchImages(true)
  }
}

function openDir(dir) {
  activeDir.value = dir.path_rel
  fetchImages(true)
}

function closeDir() {
  activeDir.value = null
  images.value = []
  fetchDirs()
}

function goHome() {
  if (viewMode.value === 'folder') {
    activeDir.value = null
    images.value = []
    fetchDirs()
  }
  mainEl.value?.scrollTo({ top: 0, behavior: 'smooth' })
}

async function fetchTags() {
  try {
    const res = await fetch('/api/tags')
    if (!res.ok) return
    tags.value = await res.json()
  } catch { }
}

async function fetchInfo() {
  const res = await fetch('/api/info')
  info.value = await res.json()
}

async function fetchAiStatus() {
  try {
    const res = await fetch('/api/ai/status')
    if (res.ok) aiStatus.value = await res.json()
  } catch { }
}

// ── Job stream ────────────────────────────────────────────────────────────────
async function handleJobFinished(job) {
  // GENERATION failure: reset the refine panel before checking for success
  if (job.lane === 'gen' && job.state !== 'succeeded' && refineGenJobId.value === job.id) {
    refineGenJobId.value = null
    refinePhase.value = 'done'
    refineErrorMsg.value = job.error || t('refine.error')
  }

  if (job.state !== 'succeeded') return

  // Scan complete → refresh gallery
  if (SCAN_TITLES.has(job.title)) {
    await fetchImages(true)
    await fetchTags()
  }

  // AI pipeline complete → refresh AI status + gallery
  if (PIPELINE_TITLES.has(job.title)) {
    await fetchAiStatus()
    await fetchTags()
    await fetchImages(true)
  }

  // GENERATION complete → refresh gallery + notify refine panel
  if (job.lane === 'gen') {
    await fetchImages(true)
    if (refineGenJobId.value === job.id) {
      refineGenJobId.value = null
      refinePhase.value = 'done'
      if (Array.isArray(job.result?.sha256s)) {
        for (const sha256 of job.result.sha256s) {
          if (!comfyGeneratedImages.value.includes(sha256))
            comfyGeneratedImages.value.push(sha256)
        }
      }
    }
  }

  // Daily oracle complete → refresh oracle panel
  if (job.title === 'invoke.daily_oracle') {
    await fetchDailyOracle()
  }

  // EVALUATION complete → update cache for target sha256s and remove from evaluating
  if (job.lane === 'eval') {
    const sha256s = job.meta?.sha256s || []

    // Remove completed job's sha256 entries from the evaluating Map
    if (sha256s.length > 0) {
      const nextEval = new Map(alignmentEvaluating.value)
      for (const sha256 of sha256s) {
        if (nextEval.get(sha256) === job.id) nextEval.delete(sha256)
      }
      alignmentEvaluating.value = nextEval
    }

    // Invalidate cache and re-fetch (targets only; full evaluation clears all)
    const targets = sha256s.length > 0 ? sha256s : images.value.map(img => img.sha256)
    const next = new Map(alignmentCache.value)
    for (const sha256 of targets) next.delete(sha256)
    alignmentCache.value = next

    const targetSet = new Set(targets)
    for (const img of images.value) {
      if (targetSet.has(img.sha256)) loadAlignment(img.sha256)
    }
  }
}

function startJobStream() {
  if (_jobEventSource) return
  const es = new EventSource(`/api/jobs/stream?token=${encodeURIComponent(getToken())}`)
  _jobEventSource = es

  es.onopen = () => { jobStreamConnected.value = true }

  es.addEventListener('snapshot', (e) => {
    try {
      const data = JSON.parse(e.data)
      const { jobs, resources } = data
      jobsMap.value = new Map(jobs.map(j => [j.id, j]))
      if (resources) resourcesRef.value = resources
      crIngestEvent('snapshot', data)

      // On reconnect: restore alignmentEvaluating from active eval jobs
      const evalMap = new Map()
      for (const job of jobs) {
        if (job.lane === 'eval' && ['queued', 'running', 'cancelling'].includes(job.state)) {
          for (const sha256 of (job.meta?.sha256s || [])) {
            evalMap.set(sha256, job.id)
          }
        }
      }
      if (evalMap.size > 0) alignmentEvaluating.value = evalMap
    } catch {}
  })

  const upsert = (e) => {
    try {
      const job = JSON.parse(e.data)
      jobsMap.value = new Map(jobsMap.value).set(job.id, job)
      crIngestEvent(e.type, job)
      if (e.type === 'job_finished') {
        _pendingJobUpdates?.delete(job.id)
        handleJobFinished(job)
      }
    } catch {}
  }
  es.addEventListener('job_created', upsert)
  es.addEventListener('job_finished', upsert)

  // job_updated is throttled at 250ms (max 4 Hz) to batch reactive updates
  // and reduce Vue full re-render overhead from high-frequency SSE events
  let _pendingJobUpdates = null
  let _pendingJobUpdatesTimer = null
  es.addEventListener('job_updated', (e) => {
    try {
      const job = JSON.parse(e.data)
      crIngestEvent('job_updated', job)
      if (!_pendingJobUpdates) _pendingJobUpdates = new Map()
      _pendingJobUpdates.set(job.id, job)
      if (!_pendingJobUpdatesTimer) {
        _pendingJobUpdatesTimer = setTimeout(() => {
          const newMap = new Map(jobsMap.value)
          for (const [id, j] of _pendingJobUpdates) newMap.set(id, j)
          jobsMap.value = newMap
          _pendingJobUpdates = null
          _pendingJobUpdatesTimer = null
        }, 250)
      }
    } catch {}
  })

  es.addEventListener('resource_stats', (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.resources) resourcesRef.value = data.resources
      if (data.disks) disksRef.value = data.disks
      if (data.disk_caution_pct != null) diskCautionPct.value = data.disk_caution_pct
      if (data.disk_fault_pct   != null) diskFaultPct.value   = data.disk_fault_pct
      crIngestEvent('resource_stats', data)
    } catch {}
  })

  es.addEventListener('lane_state', (e) => {
    try {
      crIngestEvent('lane_state', JSON.parse(e.data))
    } catch {}
  })

  es.onerror = () => {
    jobStreamConnected.value = false
    es.close()
    _jobEventSource = null
    setTimeout(startJobStream, 3000)
  }
}

function stopJobStream() {
  _jobEventSource?.close()
  _jobEventSource = null
  jobStreamConnected.value = false
}

// ── Scan ──────────────────────────────────────────────────────────────────────
async function triggerScan() {
  await fetch('/api/scan', { method: 'POST' })
}

async function triggerFullScan() {
  await fetch('/api/scan/full', { method: 'POST' })
}

async function triggerRefreshMetadata() {
  await fetch('/api/scan/refresh-metadata', { method: 'POST' })
}

// ── AI Pipeline ───────────────────────────────────────────────────────────────
async function triggerPipeline(sha256s = []) {
  await fetch('/api/ai/pipeline', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sha256s: sha256s.length ? sha256s : [] }),
  })
  showToast(t('toast.pipelineStarted'), 'info')
}

async function cancelPipeline() {
  const job = [...jobsMap.value.values()].find(
    j => PIPELINE_TITLES.has(j.title) && j.state === 'running'
  )
  if (job) await fetch(`/api/jobs/${job.id}/cancel`, { method: 'POST' })
}

async function cancelJob(jobId) {
  await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' })
}

async function retryJob(jobId) {
  await fetch(`/api/jobs/${jobId}/retry`, { method: 'POST' })
}

async function retryAllFailed() {
  const failed = [...jobsMap.value.values()].filter(j => j.state === 'failed')
  await Promise.all(failed.map(j => retryJob(j.id)))
}

async function cancelAllQueued() {
  const queued = [...jobsMap.value.values()].filter(j => j.state === 'queued')
  await Promise.all(queued.map(j => cancelJob(j.id)))
}

function formatSize(bytes) {
  if (!bytes) return '—'
  return (bytes / 1024 / 1024).toFixed(2) + ' MB'
}

function formatMtime(iso) {
  if (!iso) return ''
  return iso.replace('T', ' ').substring(0, 19)
}

function formatEta(seconds) {
  if (seconds == null || seconds < 0) return ''
  const s = Math.floor(seconds)
  if (s < 60) return t('header.etaSec', { s })
  const m = Math.floor(s / 60)
  const r = s % 60
  return r > 0 ? t('header.etaMin', { m, r }) : t('header.etaMinOnly', { m })
}

// ── Tag autocomplete ──────────────────────────────────────────────────────────
async function fetchSuggestions(q) {
  if (!q || q.length < 2) { tagSuggestions.value = []; showSuggestions.value = false; return }
  clearTimeout(suggestTimer)
  suggestTimer = setTimeout(async () => {
    const r = await fetch(`/api/tags/suggest?q=${encodeURIComponent(q)}&limit=8`)
    if (r.ok) {
      tagSuggestions.value = await r.json()
      showSuggestions.value = tagSuggestions.value.length > 0
      suggestionIndex.value = -1
    }
  }, 150)
}

function selectSuggestion(tag) {
  tagsFilter.value[tag] = 'include'
  searchQuery.value = ''
  tagSuggestions.value = []
  showSuggestions.value = false
  fetchImages(true)
}

function onSuggestionKey(e) {
  if (!showSuggestions.value) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    suggestionIndex.value = Math.min(suggestionIndex.value + 1, tagSuggestions.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    suggestionIndex.value = Math.max(suggestionIndex.value - 1, -1)
  } else if (e.key === 'Enter' && suggestionIndex.value >= 0) {
    e.preventDefault()
    selectSuggestion(tagSuggestions.value[suggestionIndex.value].tag)
  } else if (e.key === 'Escape') {
    showSuggestions.value = false
  }
}

function hideSuggestionsDelayed() {
  setTimeout(() => { showSuggestions.value = false }, 150)
}

// ── Interaction ───────────────────────────────────────────────────────────────
function onSearchInput() {
  clearTimeout(searchTimer)
  fetchSuggestions(searchQuery.value)
  searchTimer = setTimeout(() => fetchImages(true), 350)
}

const filteredModels = computed(() => {
  const q = tagSearch.value.trim().toLowerCase()
  if (!q) return modelFacets.value
  return modelFacets.value.filter(m => m.model.toLowerCase().includes(q))
})

const availableTagSet = computed(() => {
  if (!Object.keys(tagsFilter.value).length || tagLogic.value !== 'and') return null
  // Model selected: use backend-provided tag universe (model-only filtered, pre-tag-filter)
  if (activeModels.value.length && modelFilteredTagSet.value) {
    return modelFilteredTagSet.value
  }
  // No model: derive from current result set
  const s = new Set()
  for (const img of images.value) {
    for (const t of (img.wd14_tags || [])) s.add(t)
  }
  return s
})

const filteredGroupedTags = computed(() => {
  const q = tagSearch.value.trim().toLowerCase()
  const selectedSet = new Set(Object.keys(tagsFilter.value))
  let list = tags.value

  if (availableTagSet.value) {
    list = list.filter(t => availableTagSet.value.has(t.tag) || selectedSet.has(t.tag))
  }

  if (q) {
    const matched = list.filter(t => t.tag.includes(q))
    return matched.length ? { '': matched } : {}
  }

  const groups = {}
  for (const t of list) {
    const cat = t.category || 'Other'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(t)
  }
  return groups
})

function selectTag(tag) {
  const cur = tagsFilter.value[tag]
  if (!cur) tagsFilter.value[tag] = 'include'
  else if (cur === 'include') tagsFilter.value[tag] = 'exclude'
  else delete tagsFilter.value[tag]
  showSuggestions.value = false
  fetchImages(true)
}

function clearFilter() {
  searchQuery.value = ''
  tagsFilter.value = {}
  activeModels.value = []
  starFilter.value = null
  categoryFilter.value = 'all'
  alignMinFilter.value = null
  showSuggestions.value = false
  if (sortOrder.value === 'relevance') {
    sortOrder.value = 'newest'
    localStorage.setItem('sortOrder', 'newest')
  }
  fetchImages(true)
}


function onSortChange(order) {
  sortOrder.value = order
  fetchImages(true)
}

function toggleSearchMode(mode) {
  const prev = searchMode.value
  searchMode.value = mode
  if (mode === 'semantic' && prev !== 'semantic') {
    sortOrder.value = 'relevance'
  } else if (mode !== 'semantic' && prev === 'semantic') {
    sortOrder.value = localStorage.getItem('sortOrder') || 'newest'
  }
  if (effectiveQuery.value) fetchImages(true)
}

function toggleImageSelection(img) {
  const id = img.sha256
  const next = new Set(selectedIds.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selectedIds.value = next
}

function onImageClick(img) {
  selected.value = img
}

function onCheckboxClick(e, img) {
  e.stopPropagation()
  const isAdding = !selectedIds.value.has(img.sha256)
  toggleImageSelection(img)
  if (isAdding) flyToBasket(e.currentTarget, img)
}

function removeFromSelection(sha256) {
  const next = new Set(selectedIds.value)
  next.delete(sha256)
  selectedIds.value = next
  if (hoveredThumbnailSha.value === sha256) hoveredThumbnailSha.value = null
}

function clearSelection() {
  selectedIds.value = new Set()
}

function flyToBasket(sourceEl, img) {
  const srcRect = sourceEl.getBoundingClientRect()
  const startX = srcRect.left + srcRect.width / 2 - 20
  const startY = srcRect.top + srcRect.height / 2 - 20
  const destX = 160
  const destY = window.innerHeight - 55

  const el = document.createElement('div')
  el.style.cssText = `position:fixed;left:${startX}px;top:${startY}px;width:40px;height:40px;border-radius:8px;overflow:hidden;pointer-events:none;z-index:9999;box-shadow:0 0 0 2px #a855f7,0 4px 20px rgba(168,85,247,0.5);`
  const imgEl = document.createElement('img')
  imgEl.src = `/api/thumbnails/${img.sha256}.webp`
  imgEl.style.cssText = 'width:100%;height:100%;object-fit:cover;'
  el.appendChild(imgEl)
  document.body.appendChild(el)

  const dx = destX - startX
  const dy = destY - startY
  const midY = Math.min(dy * 0.35, -80)

  el.animate([
    { transform: 'translate(0,0) scale(1)', opacity: 1 },
    { transform: `translate(${dx * 0.3}px,${midY}px) scale(0.85)`, opacity: 0.9, offset: 0.38 },
    { transform: `translate(${dx}px,${dy}px) scale(0.2)`, opacity: 0 },
  ], { duration: 560, easing: 'cubic-bezier(0.4,0,0.2,1)', fill: 'forwards' })
    .finished.then(() => el.remove())
}

const selectedIndex = computed(() => {
  if (!selected.value) return -1
  return images.value.findIndex(img => img.sha256 === selected.value.sha256)
})

function prevImage() {
  if (!lbImgReady.value) return
  const i = selectedIndex.value
  if (i > 0) {
    _markImgPending()
    selected.value = images.value[i - 1]
    if (showLightbox.value) { lbPanX.value = 0; lbPanY.value = 0 }
  }
}

async function nextImage() {
  if (!lbImgReady.value) return
  const i = selectedIndex.value
  if (i < 0) return
  if (i < images.value.length - 1) {
    _markImgPending()
    selected.value = images.value[i + 1]
    if (showLightbox.value) { lbPanX.value = 0; lbPanY.value = 0 }
  } else if (hasMore.value) {
    _markImgPending()
    await fetchImages()
    if (i + 1 < images.value.length) {
      selected.value = images.value[i + 1]
      if (showLightbox.value) {
        lbPanX.value = 0; lbPanY.value = 0
        await nextTick()
        lbContainerRef.value?.focus()
      }
    } else {
      _markImgReady()
    }
  }
}

function clearRefineSession() {
  refinePhase.value = ''
  refinedPrompt.value = ''
  comfyGeneratedImages.value = []
}

async function openRefine() {
  // If running or generating, show panel as-is without resetting
  if (refining.value || refinePhase.value === 'comfy') {
    showRefine.value = true
    return
  }
  showRefine.value = true
  refinePhase.value = ''
  refineStarted.value = false
  thinkText.value = ''
  thinkOpen.value = false
  if (_streamingTimerId) { clearTimeout(_streamingTimerId); _streamingTimerId = null }
  _pendingTokens = ''; _pendingThink = ''; _pendingProgress = null
  streamingText.value = ''
  positivePrompt.value = ''
  negativePromptText.value = ''
  refinedPrompt.value = ''
  removedTags.value = []
  comfyProgress.value = { value: 0, max: 0, node: '' }
  comfyExecuting.value = ''
  comfyGeneratedImages.value = []
  if (workflows.value.length === 0) {
    try {
      const r = await fetch('/api/comfy/workflows')
      if (r.ok) workflows.value = await r.json()
    } catch {}
  }
}

function startNewRefineJob() {
  // Start a new refinement cycle after submitting the job. The previous job continues to be tracked in jobsMap.
  showRefine.value = false
  nextTick(() => {
    refinePhase.value = ''
    refineStarted.value = false
    streamingText.value = ''
    positivePrompt.value = ''
    negativePromptText.value = ''
    refinedPrompt.value = ''
    removedTags.value = []
    refineDirectPrompt.value = null
    comfyGeneratedImages.value = []
    comfyProgress.value = { value: 0, max: 0, node: '' }
    comfyExecuting.value = ''
    refineGenJobId.value = null
    showRefine.value = true
  })
}

function openRefineFromTray() {
  const shas = [...selectedIds.value].slice(0, 6)
  pinnedShas.value = new Set(shas)
  // randomCount = match selection size, at least the number of pins, max 6
  randomCount.value = Math.min(6, Math.max(shas.length, randomCount.value))
  openRefine()
}

function openRefineFromDetail(img) {
  handleSendToRefine({ shas: [img.sha256], text: '' })
}

function openRefineFromHistory(jobId) {
  const job = jobsMap.value.get(jobId)
  if (!job?.meta) return

  const m = job.meta
  const shas = m.sha256s || []

  selectedIds.value = new Set(shas)
  pinnedShas.value  = new Set(shas)
  randomCount.value = Math.min(6, Math.max(shas.length, 1))

  refineInstruction.value          = m.instruction || ''
  refineInstructionMode.value      = m.instruction_mode || 'basic'
  refineStyle.value                = m.prompt_style || 'natural'
  refineWorkflow.value             = m.workflow_name || ''
  refineBatchCount.value           = m.batch_count  || 1
  refineAutoSubmit.value           = true
  refineDirectPrompt.value         = m.direct_prompt ?? null
  refineDirectNegativePrompt.value = m.direct_negative_prompt || ''
  refineDirectPromptSource.value   = m.direct_prompt ? 'history' : ''

  if (m.weights?.length === shas.length) {
    const wm = new Map()
    shas.forEach((sha, i) => wm.set(sha, Math.round((m.weights[i] ?? 0) * 100)))
    imageWeights.value = wm
  }

  positivePrompt.value     = m.positive_prompt || ''
  negativePromptText.value = m.negative_prompt || ''
  refineStarted.value      = true
  refinePhase.value        = 'done'
  refineGenJobId.value     = null
  comfyGeneratedImages.value = []

  controlRoomVisible.value = false
  showRefine.value         = true
}

async function randomSelect() {
  const pinned = [...pinnedShas.value]
  const slots = Math.max(1, randomCount.value - pinned.length)
  const exclude = [...selectedIds.value].join(',')
  const qs = `n=${slots}${exclude ? `&exclude=${encodeURIComponent(exclude)}` : ''}`
  const res = await fetch(`/api/images/random?${qs}`)
  if (!res.ok) return
  const data = await res.json()
  const newShas = data.images.map(img => img.sha256)
  selectedIds.value = new Set([...pinned, ...newShas])
}

watch(
  () => [...selectedIds.value].slice(0, 6).join(','),
  (next, prev) => {
    redistributeWeights([...selectedIds.value].slice(0, 6))
  },
  { immediate: true }
)

// Color picker auto-search with debounce
let colorPickTimer = null
watch([colorPickHex, colorPickDistance, colorPickExcludeOpposite], () => {
  clearTimeout(colorPickTimer)
  colorPickTimer = setTimeout(doColorPick, 400)
})

// When randomCount is decreased, remove excess non-pinned images
watch(randomCount, (n) => {
  if (selectedIds.value.size <= n) return
  const pinned = [...pinnedShas.value]
  const loose = [...selectedIds.value].filter(s => !pinnedShas.value.has(s))
  selectedIds.value = new Set([...pinned, ...loose.slice(0, Math.max(0, n - pinned.length))])
})

function togglePin(sha256) {
  const next = new Set(pinnedShas.value)
  if (next.has(sha256)) {
    next.delete(sha256)
    // Unpin: leave selectedIds as-is (remains as a loose selection)
  } else {
    next.add(sha256)
    // Pin: also add to selectedIds (pinned images must always be in the selection)
    if (!selectedIds.value.has(sha256)) {
      selectedIds.value = new Set([...selectedIds.value, sha256])
    }
  }
  pinnedShas.value = next
  if (next.size > randomCount.value) randomCount.value = Math.min(6, next.size)
}

function redistributeWeights(shas) {
  const n = shas.length
  if (n === 0) { imageWeights.value = new Map(); return }
  const next = new Map()
  shas.forEach(sha => next.set(sha, 50))
  imageWeights.value = next
}

function onWeightChange(sha, newValue) {
  const next = new Map(imageWeights.value)
  next.set(sha, newValue)
  imageWeights.value = next
}

const weightTotal = computed(() =>
  [...imageWeights.value.values()].reduce((a, b) => a + b, 0) || 1
)

function normalizedPct(sha) {
  return Math.round(((imageWeights.value.get(sha) ?? 0) / weightTotal.value) * 100)
}

function cancelRefine() {
  refineAbortController?.abort()
}

async function runRefine() {
  if (selectedCount.value === 0) return
  refining.value = true
  refineStarted.value = true
  refinePhase.value = 'llm'
  thinkText.value = ''
  thinkOpen.value = false
  if (_streamingTimerId) { clearTimeout(_streamingTimerId); _streamingTimerId = null }
  _pendingTokens = ''; _pendingThink = ''; _pendingProgress = null
  streamingText.value = ''
  positivePrompt.value = ''
  negativePromptText.value = ''
  refinedPrompt.value = ''
  comfyProgress.value = { value: 0, max: 0, node: '' }
  comfyExecuting.value = ''
  comfyGeneratedImages.value = []
  refineErrorMsg.value = ''
  proseMissing.value = false
  refinePromptJobId.value = null

  try {
    const orderedShas = [...selectedIds.value].slice(0, 6)
    const body = JSON.stringify({
      sha256s: orderedShas,
      weights: orderedShas.map(s => normalizedPct(s) / 100),
      instruction: refineInstruction.value,
      instruction_mode: refineInstructionMode.value,
      temperature: refineTemp.value,
      num_ctx: refineNumCtx.value,
      prompt_style: refineStyle.value,
      negative_prompt: refineNegative.value,
      auto_submit: refineAutoSubmit.value,
      batch_count: refineBatchCount.value,
      workflow_name: refineWorkflow.value,
      positive_node_id: refinePosNodeId.value,
      negative_node_id: refineNegNodeId.value,
      use_ref_seed: refineUseSeed.value,
      ...(refineDirectPrompt.value != null ? {
        direct_prompt: refineDirectPrompt.value,
        ...(refineDirectNegativePrompt.value ? { direct_negative_prompt: refineDirectNegativePrompt.value } : {}),
      } : {}),
      ...(refineInspireContext.value ? { inspire_context: refineInspireContext.value } : {}),
    })

    // Step 1: Submit job to PROMPT lane → obtain job_id
    const submitRes = await fetch('/api/ai/refine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    })
    if (!submitRes.ok) throw new Error(`HTTP ${submitRes.status}`)
    const { job_id } = await submitRes.json()
    refinePromptJobId.value = job_id

    // Step 2: Connect to token stream
    refineAbortController = new AbortController()
    const streamRes = await fetch(`/api/ai/refine/${job_id}/stream`, {
      signal: refineAbortController.signal,
    })
    if (!streamRes.ok || !streamRes.body) throw new Error(`Stream HTTP ${streamRes.status}`)

    const reader = streamRes.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try { handleRefineEvent(JSON.parse(line.slice(6))) } catch {}
      }
    }
  } catch (e) {
    if (e?.name !== 'AbortError') console.error('Refine error:', e)
  } finally {
    refineAbortController = null
    refining.value = false
    // If refineGenJobId is set, we are waiting for the spooler's ComfyUI job to finish,
    // so do not overwrite with 'done' (handleJobFinished will set it on completion)
    if (refinePhase.value !== 'done' && !refineGenJobId.value) refinePhase.value = 'done'
  }
}

function handleRefineEvent(evt) {
  switch (evt.type) {
    case 'think':
      _pendingThink += evt.text
      _scheduleFlush()
      break
    case 'token':
      _pendingTokens += evt.text
      _scheduleFlush()
      break
    case 'done':
      positivePrompt.value = evt.positive || ''
      negativePromptText.value = evt.negative || ''
      refinedPrompt.value = evt.positive || ''
      proseMissing.value = !!evt.prose_missing
      removedTags.value = evt.removed_tags || []
      refinePhase.value = evt.auto_submit ? 'comfy' : 'done'
      break
    case 'comfy_queued':
      refinePhase.value = 'comfy'
      break
    case 'comfy_job_id':
      // ComfyUI job via spooler: record job_id and monitor completion via job stream
      refineGenJobId.value = evt.job_id
      refinePhase.value = 'comfy'
      break
    case 'comfy_progress':
      _pendingProgress = { value: evt.value, max: evt.max, node: evt.node || '' }
      _scheduleFlush()
      break
    case 'comfy_executing':
      comfyExecuting.value = evt.node || ''
      break
    case 'comfy_saved':
      if (!comfyGeneratedImages.value.includes(evt.sha256))
        comfyGeneratedImages.value.push(evt.sha256)
      break
    case 'comfy_done':
      refinePhase.value = 'done'
      fetchImages(true)
      break
    case 'cancelled':
      refinePhase.value = 'done'
      break
    case 'error':
      refinePhase.value = 'done'
      console.error('[Refine SSE error]', evt.message)
      refineErrorMsg.value = evt.message || t('refine.error')
      break
  }
}

function copyToClipboard(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text)
  } else {
    const el = document.createElement('textarea')
    el.value = text
    el.style.cssText = 'position:fixed;opacity:0'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  }
}

// ── Analyzer ──────────────────────────────────────────────────────────────────
const showAnalyzer = ref(false)

function openAnalyzer() { showAnalyzer.value = true }

// ── Inspire Panel ─────────────────────────────────────────────────────────────
const showInspire = ref(false)
const inspireRunning = ref(false)
const showAbout = ref(false)

const inspireInitialSlots = computed(() => [...selectedIds.value].slice(0, 6))
const inspireSelectedIds = computed(() => [...selectedIds.value])

const {
  hasSession: inspireHasSession,
  isRunning: inspireIsRunning,
  resetSession: inspireReset,
} = useInspireSession()

const refineHasSession = computed(() =>
  !!refinePhase.value || !!refinedPrompt.value || comfyGeneratedImages.value.length > 0
)
const refineIsRunning = computed(() =>
  refining.value || refinePhase.value === 'llm' || refinePhase.value === 'comfy'
)

function openInspire() { showInspire.value = true }

// ── Invoke Panel ───────────────────────────────────────────────────────────────
const showInvoke = ref(false)

function handleInvokeSendToRefine(data) {
  // data: { positive_prompt, negative_prompt, sha256, workflow_name }
  const shas = data.sha256 ? [data.sha256] : []
  selectedIds.value = new Set(shas)
  pinnedShas.value = new Set(shas)
  randomCount.value = Math.max(shas.length, 1)
  refineInstruction.value = ''
  refineDirectPrompt.value = data.positive_prompt || ''
  refineDirectNegativePrompt.value = data.negative_prompt || ''
  refineDirectPromptSource.value = 'invoke'
  refineInspireContext.value = null
  refineStyle.value = 'natural'
  if (data.workflow_name) refineWorkflow.value = data.workflow_name
  openRefine()
}

function handleSendToRefine({ shas, text, inspireContext = null }) {
  selectedIds.value = new Set(shas)
  pinnedShas.value = new Set(shas)
  randomCount.value = Math.min(6, Math.max(shas.length, 1))
  refineInstruction.value = text
  refineDirectPrompt.value = null
  refineDirectNegativePrompt.value = ''
  refineDirectPromptSource.value = ''
  refineInspireContext.value = inspireContext
  refineStyle.value = 'natural'
  openRefine()
}

function handleSendToRefineDirect({ shas, directPrompt, directNegativePrompt = '', source = '', inspireContext = null }) {
  selectedIds.value = new Set(shas)
  pinnedShas.value = new Set(shas)
  randomCount.value = Math.min(6, Math.max(shas.length, 1))
  refineInstruction.value = ''
  refineDirectPrompt.value = directPrompt
  refineDirectNegativePrompt.value = directNegativePrompt
  refineDirectPromptSource.value = source
  refineInspireContext.value = inspireContext
  refineStyle.value = 'natural'
  openRefine()
}

function handleToggleImageSelection(img, sourceEl) {
  const s = new Set(selectedIds.value)
  const isAdding = !s.has(img.sha256)
  if (isAdding) {
    s.add(img.sha256)
    if (sourceEl) flyToBasket(sourceEl, img)
  } else {
    s.delete(img.sha256)
  }
  selectedIds.value = s
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────
onMounted(async () => {
  await waitForBackend()

  startJobStream()

  // Set up observer immediately — before awaiting data fetches.
  // fetchImages() can hang for up to 30s under Qdrant load, and the observer
  // must be ready before that to fire when the user scrolls.
  observer = new IntersectionObserver(entries => {
    const entry = entries[0]
    if (entry.isIntersecting) {
      if (!loading.value && hasMore.value) {
        fetchImages()
      }
    }
  }, { root: mainEl.value, rootMargin: '200px' })
  if (sentinel.value) {
    observer.observe(sentinel.value)
  }

  if (viewMode.value === 'folder') {
    await Promise.all([fetchDirs(), fetchTags(), fetchFacets(), fetchInfo(), fetchAiStatus()])
  } else {
    await Promise.all([fetchImages(), fetchTags(), fetchFacets(), fetchInfo(), fetchAiStatus()])
  }
})

function onVisibilityChange() {
  if (document.hidden) {
    if (_graphSim) _graphSim.stop()
  } else {
    if (_graphSim && _graphSim.alpha() > _graphSim.alphaMin()) _graphSim.restart()
    nextTick(() => window.dispatchEvent(new Event('resize')))
  }
}

// Close graph context menu on any document click (v-click-outside alternative)
function _onDocClick() { if (graphContextMenu.value) graphContextMenu.value = null }

function _onGlobalKey(e) {
  const tag = e.target?.tagName
  const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !inInput) {
    e.preventDefault()
    controlRoomVisible.value = !controlRoomVisible.value
    return
  }
  if (e.key === 'Escape' && controlRoomVisible.value) {
    controlRoomVisible.value = false
  }
}

// ── API token prompt ─────────────────────────────────────────────────────────
const showTokenPrompt = ref(false)
const tokenInput = ref('')

function openTokenPrompt() {
  tokenInput.value = sessionStorage.getItem('api_token') ?? ''
  showTokenPrompt.value = true
}

function saveToken() {
  const t = tokenInput.value.trim()
  if (!t) return
  saveAndSyncToken(t)
  showTokenPrompt.value = false
  window.location.reload()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  document.addEventListener('click', _onDocClick)
  document.addEventListener('keydown', _onGlobalKey)
  window.addEventListener('api-unauthorized', openTokenPrompt)
})

onUnmounted(() => {
  observer?.disconnect()
  stopJobStream()
  clearTimeout(searchTimer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('click', _onDocClick)
  document.removeEventListener('keydown', _onGlobalKey)
  window.removeEventListener('api-unauthorized', openTokenPrompt)
})
</script>

<template>
  <div class="h-screen bg-gray-950 text-gray-100 flex flex-col">

    <!-- ── Control Room Status Bar ── -->
    <div
      class="cr-statusline"
      :class="`cr-statusline--${masterStatus.toLowerCase()}`"
      @click="controlRoomVisible = true"
      title="Control Room (/)"
    />

    <!-- ── Header ── -->
    <header class="sticky top-0 z-20 bg-gray-900 border-b border-gray-800">
      <div class="flex items-center gap-2 px-4 py-2.5 flex-wrap">
        <button @click="goHome" class="flex items-center gap-2 cursor-pointer focus:outline-none">
          <img src="/logo.png" alt="Ranbell Image" class="h-7 w-7 rounded-md flex-shrink-0" />
          <h1 class="text-base font-bold text-purple-400 tracking-tight whitespace-nowrap">Ranbell Image</h1>
        </button>
        <span class="text-gray-600 text-xs whitespace-nowrap">
          <template v-if="viewMode === 'folder' && activeDir === null">{{ $t('header.folderCount', { n: dirs.length }) }}</template>
          <template v-else-if="viewMode === 'folder'">{{ $t('header.imageCount', { n: images.length.toLocaleString() }) }}</template>
          <template v-else>{{ $t('header.imageCount', { n: total.toLocaleString() }) }}</template>
        </span>

        <!-- Search mode toggle (flat only) -->
        <template v-if="viewMode === 'flat'">
        <div class="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
          <button @click="toggleSearchMode('keyword')"
            :class="searchMode==='keyword' ? 'bg-gray-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
            class="px-2.5 py-1.5 transition-colors">{{ $t('header.search.keyword') }}</button>
          <button @click="toggleSearchMode('semantic')"
            :class="searchMode==='semantic' ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
            class="px-2.5 py-1.5 border-l border-gray-700 transition-colors"
            :title="aiStatus?.vector_count ? $t('header.search.semanticTitle', { count: aiStatus.vector_count }) : $t('header.search.semanticTitleEmpty')">
            {{ $t('header.search.semantic') }}
          </button>
        </div>

        <!-- Search input -->
        <div class="relative flex-1 min-w-40">
          <input v-model="searchQuery" @input="onSearchInput" @keydown="onSuggestionKey"
            @focus="fetchSuggestions(searchQuery)" @blur="hideSuggestionsDelayed"
            type="text"
            :placeholder="searchMode==='semantic' ? $t('header.search.placeholderSemantic') : $t('header.search.placeholderKeyword')"
            class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm placeholder-gray-500 focus:outline-none focus:border-purple-500 transition-colors" />
          <button v-if="searchQuery" @click="clearFilter"
            class="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs">✕</button>

          <!-- Tag autocomplete dropdown -->
          <div v-if="showSuggestions && tagSuggestions.length"
            class="absolute top-full left-0 right-0 mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden">
            <div v-for="(s, i) in tagSuggestions" :key="s.tag"
              @mousedown.prevent="selectSuggestion(s.tag)"
              :class="i === suggestionIndex ? 'bg-purple-700/60 text-white' : 'hover:bg-gray-700 text-gray-200'"
              class="flex items-center justify-between px-3 py-2 cursor-pointer text-sm transition-colors">
              <span>{{ s.tag }}</span>
              <span class="text-xs text-gray-500 ml-2">{{ s.count }}</span>
            </div>
          </div>
        </div>

        <!-- Sort -->
        <select :value="sortOrder" @change="setSort($event.target.value)"
          class="text-xs bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-gray-300 cursor-pointer focus:outline-none focus:border-purple-500 transition-colors">
          <option v-if="(searchMode === 'semantic' && searchQuery) || Object.values(tagsFilter).some(v => v === 'include')" value="relevance">{{ $t('header.sort.relevance') }}</option>
          <option v-for="opt in SORT_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        </template><!-- /flat-only search UI -->

        <!-- Active jobs summary → opens Control Room -->
        <button v-if="headerActiveJobs.length > 0"
          @click="controlRoomVisible = true"
          class="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 whitespace-nowrap transition-colors min-w-0 max-w-[40vw]">
          <span class="w-1.5 h-1.5 rounded-full inline-block flex-shrink-0"
            :class="headerActiveJobs[0].state === 'running' ? 'bg-blue-400 animate-pulse' : headerActiveJobs[0].state === 'cancelling' ? 'bg-orange-400 animate-pulse' : 'bg-yellow-400'"></span>
          <span class="truncate">{{ headerActiveJobs[0].title }}</span>
          <span v-if="headerActiveJobs[0].state === 'running' && headerActiveJobs[0].progress_text" class="text-gray-500 truncate">{{ headerActiveJobs[0].progress_text }}</span>
          <span v-else-if="headerActiveJobs[0].state === 'running' && headerActiveJobs[0].progress > 0" class="text-gray-500">{{ Math.round(headerActiveJobs[0].progress * 100) }}%</span>
          <span v-else-if="headerActiveJobs[0].state === 'queued'" class="text-yellow-400/70">{{ $t('header.jobQueued') }}</span>
          <span v-else-if="headerActiveJobs[0].state === 'cancelling'" class="text-orange-400/70">{{ $t('header.jobCancelling') }}</span>
          <span v-if="headerActiveJobs.length > 1" class="text-gray-500 flex-shrink-0">+{{ headerActiveJobs.length - 1 }}件</span>
        </button>

        <!-- Action buttons -->
        <div class="flex items-center gap-1.5 ml-auto">
          <!-- Color picker toggle -->
          <button @click="colorPickerVisible = !colorPickerVisible"
            :title="$t('colorPick.label')"
            :class="colorPickerVisible || colorPickActive ? 'text-rose-400 border-rose-700/60 bg-rose-900/30' : 'text-gray-400 border-gray-700'"
            class="px-2 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors border">
            <span class="relative">
              🎨
              <span v-if="colorPickActive && !colorPickerVisible"
                class="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 bg-rose-500 rounded-full"></span>
            </span>
          </button>
          <!-- Language toggle -->
          <button @click="toggleLocale" :title="locale === 'ja' ? 'Switch to English' : 'Switch to Japanese'"
            class="px-2 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs text-gray-400 transition-colors border border-gray-700 font-medium tracking-wide">
            {{ locale === 'ja' ? 'EN' : 'JA' }}
          </button>
          <!-- View mode toggle -->
          <button @click="toggleViewMode"
            :title="viewMode === 'folder' ? $t('header.viewFlat') : $t('header.viewFolder')"
            :class="viewMode === 'folder' ? 'text-yellow-400 border-yellow-700/50 bg-yellow-900/20' : 'text-gray-400 border-gray-700'"
            class="px-2 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors border">
            {{ viewMode === 'folder' ? '📂' : '🗂️' }}
          </button>
          <!-- System status lamp — synced with masterStatus in Control Room -->
          <span class="flex items-center p-1.5 rounded-lg bg-gray-800/60 border border-gray-700/50 cursor-pointer select-none"
            :title="`System: ${masterStatus}`"
            @click="controlRoomVisible = true">
            <span class="w-2 h-2 rounded-full inline-block transition-colors"
              :class="{
                'bg-red-500':                  masterStatus === 'FAULT',
                'bg-yellow-500 animate-pulse':  masterStatus === 'CAUTION',
                'bg-green-500 animate-pulse':   masterStatus === 'NOMINAL' && hasAnyActiveJob,
                'bg-green-700':                 masterStatus === 'NOMINAL' && !hasAnyActiveJob,
              }" />
          </span>

          <button @click="showAbout = true"
            class="px-2 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs transition-colors" title="About">
            <img src="/logo.png" alt="About" class="h-4 w-4 rounded" />
          </button>
          <button @click="openAnalyzer"
            class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs transition-colors">{{ $t('analyzer.open') }}</button>
          <button @click="openAdmin"
            class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs transition-colors">{{ $t('header.admin') }}</button>
          <button @click="showInvoke = true"
            class="px-3 py-1.5 bg-violet-900/70 hover:bg-violet-800/80 border border-violet-600/40 hover:border-violet-500/60 rounded-lg text-xs font-medium text-violet-200 transition-colors whitespace-nowrap">
            {{ $t('header.invoke') }}
          </button>
          <button @click="triggerScan" :disabled="scanState?.state === 'running'"
            class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-40 rounded-lg text-xs font-medium transition-colors whitespace-nowrap">
            {{ scanState?.state === 'running' ? $t('header.scan.running') : $t('header.scan.button') }}
          </button>
        </div>
      </div>

      <!-- Quality bar (flat mode only) -->
      <template v-if="viewMode === 'flat'">
      <div class="flex items-center gap-x-4 gap-y-1 px-4 py-1.5 border-t border-gray-800/50 flex-wrap text-xs">
        <!-- Category filter -->
        <div class="flex items-center gap-1.5 flex-shrink-0">
          <span class="text-gray-500 mr-0.5">{{ $t('header.filter.category') }}:</span>
          <button @click="categoryFilter = 'all'; fetchImages(true)"
            :class="categoryFilter === 'all' ? 'bg-gray-600 text-white border-gray-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
            class="px-2 py-0.5 rounded-full border transition-colors">{{ $t('header.filter.categoryAll') }}</button>
          <button @click="categoryFilter = categoryFilter === 'AI' ? 'all' : 'AI'; fetchImages(true)"
            :class="categoryFilter === 'AI' ? 'bg-purple-700 text-white border-purple-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
            class="px-2 py-0.5 rounded-full border transition-colors">{{ $t('header.filter.categoryAI') }}</button>
          <button @click="categoryFilter = categoryFilter === 'NR' ? 'all' : 'NR'; fetchImages(true)"
            :class="categoryFilter === 'NR' ? 'bg-amber-700 text-white border-amber-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
            class="px-2 py-0.5 rounded-full border transition-colors">{{ $t('header.filter.categoryNR') }}</button>
        </div>

        <div class="w-px h-3 bg-gray-700 flex-shrink-0"></div>

        <!-- Star rating filter -->
        <div class="flex items-center gap-1 flex-shrink-0">
          <span class="text-gray-500 mr-0.5">★:</span>
          <button v-for="n in 5" :key="n"
            @click="starFilter = starFilter === n ? null : n; fetchImages(true)"
            class="px-1.5 py-0.5 rounded transition-colors leading-none"
            :class="starFilter === n ? 'text-yellow-400 bg-yellow-400/10 border border-yellow-600/50' : 'text-gray-600 hover:text-yellow-400/60 border border-transparent'">★{{ n }}+</button>
          <button v-if="starFilter" @click="starFilter = null; fetchImages(true)"
            class="text-gray-500 hover:text-gray-300 ml-0.5 leading-none">✕</button>
        </div>

        <div class="w-px h-3 bg-gray-700 flex-shrink-0"></div>

        <!-- Alignment score filter -->
        <div class="flex items-center gap-1 flex-shrink-0">
          <span class="text-gray-500 mr-0.5">{{ $t('header.filter.alignMin') }}:</span>
          <button @click="alignMinFilter = null; fetchImages(true)"
            :class="alignMinFilter === null ? 'bg-gray-600 text-white border-gray-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
            class="px-2 py-0.5 rounded-full border transition-colors">{{ $t('header.filter.alignOff') }}</button>
          <button v-for="pct in [60, 70, 80]" :key="pct"
            @click="alignMinFilter = alignMinFilter === pct/100 ? null : pct/100; fetchImages(true)"
            :class="alignMinFilter === pct/100 ? 'bg-orange-700 text-white border-orange-500' : 'bg-gray-800 text-gray-400 hover:bg-gray-700 border-gray-700'"
            class="px-2 py-0.5 rounded-full border transition-colors">≥{{ pct }}%</button>
        </div>
      </div>
      </template><!-- /flat-only quality bar -->

      <!-- Similar / Color-pick banner -->
      <div v-if="similarSource" class="flex items-center gap-2 px-4 py-1.5 border-t"
        :class="similarSource._colorPickMode ? 'bg-rose-950/60 border-rose-800/40'
              : similarSource._colorMode     ? 'bg-pink-950/60 border-pink-800/40'
              :                                'bg-indigo-950/60 border-indigo-800/40'">
        <!-- Color swatch for colorPickMode; thumbnail otherwise -->
        <div v-if="similarSource._colorPickMode"
          :style="`background:${similarSource._hex}`"
          class="h-6 w-6 rounded border border-rose-700/50 flex-shrink-0" />
        <div v-else class="flex-shrink-0"
          @mouseenter="similarThumbHover = true" @mouseleave="similarThumbHover = false">
          <img :src="`/api/thumbnails/${similarSource.sha256}.webp`"
            class="h-6 w-6 rounded object-cover cursor-pointer"
            :class="similarSource._colorMode ? 'border border-pink-700/50' : 'border border-indigo-700/50'" />
          <Teleport to="body">
            <div v-if="similarThumbHover"
              class="fixed top-16 left-4 z-[9999] pointer-events-none">
              <img :src="`/api/thumbnails/${similarSource.sha256}.webp`"
                class="w-48 h-auto max-h-72 rounded-xl object-contain bg-gray-900 shadow-2xl ring-1"
                :class="similarSource._colorMode ? 'ring-pink-600/50' : 'ring-indigo-600/50'" />
            </div>
          </Teleport>
        </div>
        <span class="text-xs"
          :class="similarSource._colorPickMode ? 'text-rose-300'
                : similarSource._colorMode     ? 'text-pink-300'
                :                                'text-indigo-300'">
          {{ similarSource._colorPickMode ? $t('colorPick.resultLabel', { hex: similarSource._hex })
           : similarSource._colorMode     ? $t('header.similar.color')
           :                                $t('header.similar.semantic') }}
        </span>
        <span v-if="!similarSource._colorPickMode" class="text-xs truncate max-w-[200px]"
          :class="similarSource._colorMode ? 'text-pink-200' : 'text-indigo-200'">{{ similarSource.name }}</span>
        <span class="text-xs"
          :class="similarSource._colorPickMode ? 'text-rose-500'
                : similarSource._colorMode     ? 'text-pink-500'
                :                                'text-indigo-500'">{{ $t('header.similar.count', { n: images.length }) }}</span>
        <button @click="clearSimilar"
          class="ml-auto text-xs px-2 py-0.5 rounded transition-colors"
          :class="similarSource._colorPickMode ? 'text-rose-400 hover:text-white bg-rose-800/50 hover:bg-rose-700/70'
                : similarSource._colorMode     ? 'text-pink-400 hover:text-white bg-pink-800/50 hover:bg-pink-700/70'
                :                                'text-indigo-400 hover:text-white bg-indigo-800/50 hover:bg-indigo-700/70'">
          {{ $t('header.similar.clear') }}
        </button>
      </div>

      <!-- Color picker bar (toggle) -->
      <div v-if="colorPickerVisible" class="flex items-center gap-3 px-4 py-1.5 border-t border-gray-800/40">
        <!-- Color swatch + hidden input -->
        <label class="flex items-center gap-1.5 cursor-pointer flex-shrink-0" :title="$t('colorPick.label')">
          <div :style="`background:${colorPickHex}`"
            :class="colorPickActive ? 'ring-2 ring-rose-500' : 'ring-1 ring-gray-600'"
            class="w-5 h-5 rounded flex-shrink-0 transition-shadow" />
          <input type="color" v-model="colorPickHex" class="sr-only" />
          <span class="text-xs text-gray-500">{{ $t('colorPick.label') }}</span>
        </label>

        <!-- Similarity slider -->
        <div class="flex items-center gap-1.5 flex-shrink-0">
          <span class="text-xs text-gray-600">{{ $t('colorPick.narrow') }}</span>
          <input type="range" v-model.number="colorPickDistance" min="5" max="60" step="1"
            class="w-28 accent-rose-500" />
          <span class="text-xs text-gray-600">{{ $t('colorPick.wide') }}</span>
        </div>

        <!-- Exclude opposite hues -->
        <label class="flex items-center gap-1 text-xs text-gray-500 cursor-pointer select-none flex-shrink-0">
          <input type="checkbox" v-model="colorPickExcludeOpposite" class="accent-rose-500 rounded" />
          {{ $t('colorPick.excludeOpposite') }}
        </label>

        <!-- Right side: loading spinner + clear button -->
        <div class="ml-auto flex items-center gap-2 flex-shrink-0">
          <svg v-if="colorPickLoading" class="w-3 h-3 text-rose-400 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
          </svg>
          <button v-if="colorPickActive" @click="clearSimilar"
            class="text-xs text-rose-400 hover:text-white px-1.5 py-0.5 rounded bg-rose-900/50 hover:bg-rose-800/70 transition-colors">
            ✕
          </button>
        </div>
      </div>

      <!-- Active filter bar -->
      <div v-if="Object.keys(tagsFilter).length || searchQuery || activeModels.length || alignMinFilter !== null"
        class="flex items-center gap-1.5 px-4 pt-1 pb-1 flex-wrap">
        <span class="text-xs text-gray-500 mr-0.5">{{ $t('header.filter.label') }}</span>

        <!-- keyword badge -->
        <span v-if="searchQuery"
          class="flex items-center gap-1 px-2 py-0.5 bg-gray-700 border border-gray-600 rounded-full text-xs text-gray-200">
          🔍 "{{ searchQuery }}"
          <button @click="searchQuery = ''; fetchImages(true)" class="text-gray-400 hover:text-white leading-none">✕</button>
        </span>

        <!-- AND/OR toggle (visible when 2+ include tags) -->
        <div v-if="Object.values(tagsFilter).filter(v => v === 'include').length >= 2"
          class="flex rounded-md overflow-hidden border border-gray-700 text-xs mr-0.5">
          <button @click="tagLogic = 'and'; fetchImages(true)"
            :class="tagLogic === 'and' ? 'bg-purple-700 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
            class="px-2 py-0.5 transition-colors">AND</button>
          <button @click="tagLogic = 'or'; fetchImages(true)"
            :class="tagLogic === 'or' ? 'bg-purple-700 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
            class="px-2 py-0.5 border-l border-gray-700 transition-colors">OR</button>
        </div>

        <!-- tag badges (include=blue, exclude=red) -->
        <span v-for="[tag, state] in Object.entries(tagsFilter)" :key="'tag-' + tag"
          class="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs cursor-pointer select-none transition-colors"
          :class="state === 'exclude'
            ? 'bg-red-900/60 border border-red-600/50 text-red-200'
            : 'bg-blue-800/60 border border-blue-600/50 text-blue-200'"
          @click="selectTag(tag)"
          :title="state === 'exclude' ? $t('header.filter.tagExclude') : $t('header.filter.tagInclude')">
          <span v-if="state === 'exclude'" class="opacity-70">🚫</span>
          <span :class="state === 'exclude' ? 'line-through opacity-70' : ''">{{ tag }}</span>
          <button @click.stop="delete tagsFilter[tag]; fetchImages(true)" class="opacity-50 hover:opacity-100 leading-none ml-0.5">✕</button>
        </span>

        <!-- model badges -->
        <span v-for="m in activeModels" :key="m"
          class="flex items-center gap-1 px-2 py-0.5 bg-amber-800/60 border border-amber-600/50 rounded-full text-xs text-amber-200">
          🤖 {{ m.replace(/\.(safetensors|ckpt|pt)$/i, '') }}
          <button @click="toggleModel(m)" class="text-amber-400 hover:text-white leading-none">✕</button>
        </span>

        <!-- align_min badge -->
        <span v-if="alignMinFilter !== null"
          class="flex items-center gap-1 px-2 py-0.5 bg-orange-800/60 border border-orange-600/50 rounded-full text-xs text-orange-200">
          {{ $t('header.filter.alignBadge', { pct: Math.round(alignMinFilter * 100) }) }}
          <button @click="alignMinFilter = null; fetchImages(true)" class="text-orange-300 hover:text-white leading-none">✕</button>
        </span>

        <span v-if="searchMode === 'semantic' && searchQuery"
          class="text-xs text-purple-400 bg-purple-900/30 px-1.5 py-0.5 rounded">semantic</span>
      </div>


      <!-- Tag bar -->
      <div v-if="tags.length" class="px-4 pb-2">
        <!-- Always-visible row: search + All + toggle -->
        <div class="flex items-center gap-2">
          <button @click="clearFilter"
            :class="Object.keys(tagsFilter).length === 0 && !searchQuery && !activeModels.length ? 'bg-purple-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'"
            class="flex-shrink-0 px-3 py-1 rounded-full text-xs transition-colors">{{ $t('header.filter.all') }}</button>
          <div class="relative flex-1">
            <input v-model="tagSearch" type="text" :placeholder="$t('header.filter.tagSearch')"
              @focus="tagsExpanded = true"
              class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-purple-500 transition-colors" />
            <button v-if="tagSearch" @click="tagSearch = ''"
              class="absolute right-7 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 text-xs leading-none">✕</button>
          </div>
          <button @click="tagsExpanded = !tagsExpanded"
            class="flex-shrink-0 text-gray-500 hover:text-gray-300 transition-colors px-1"
            :title="tagsExpanded ? $t('header.tagListClose') : $t('header.tagListOpen')">
            <svg class="w-4 h-4 transition-transform duration-200" :class="tagsExpanded ? 'rotate-180' : ''" viewBox="0 0 16 16" fill="none">
              <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
        <!-- Collapsible tag list -->
        <div v-show="tagsExpanded" class="mt-2 max-h-52 overflow-y-auto scrollbar-hide flex flex-col gap-2">
          <!-- Model section -->
          <div v-if="filteredModels.length" class="flex items-start gap-2">
            <div class="text-[10px] text-amber-500/70 uppercase font-bold w-16 pt-1 shrink-0">Model</div>
            <div class="flex flex-wrap gap-1.5 flex-1">
              <button v-for="m in filteredModels" :key="m.model" @click="toggleModel(m.model)"
                :class="activeModels.includes(m.model)
                  ? 'bg-amber-600 border border-amber-500 text-white'
                  : 'bg-gray-800 border border-transparent text-gray-400 hover:bg-gray-700'"
                class="flex-shrink-0 px-2.5 py-0.5 rounded-full text-xs transition-colors">
                {{ m.model.replace(/\.(safetensors|ckpt|pt)$/i, '') }}
                <span class="opacity-60 text-[10px] ml-1">{{ m.count }}</span>
              </button>
            </div>
          </div>
          <div v-for="(groupTags, catName) in filteredGroupedTags" :key="catName" class="flex items-start gap-2">
            <div v-if="catName" class="text-[10px] text-gray-400 uppercase font-bold w-16 pt-1 shrink-0">{{ catName }}</div>
            <div v-else class="w-0 shrink-0"></div>
            <div class="flex flex-wrap gap-1.5 flex-1">
              <button v-for="t in groupTags" :key="t.tag" @click="selectTag(t.tag)"
                :class="{
                   'bg-purple-600 border border-purple-500 text-white': tagsFilter[t.tag] === 'include',
                   'bg-red-700/80 border border-red-500/70 text-red-100 line-through': tagsFilter[t.tag] === 'exclude',
                   'bg-gray-800 border border-transparent text-gray-400 hover:bg-gray-700': !tagsFilter[t.tag]
                }"
                :title="tagsFilter[t.tag] === 'include' ? $t('header.filter.tagClickToExclude') : tagsFilter[t.tag] === 'exclude' ? $t('header.filter.tagClickToReset') : $t('header.filter.tagClickToInclude')"
                class="flex-shrink-0 px-2.5 py-0.5 rounded-full text-xs transition-colors">
                {{ t.tag }} <span class="opacity-60 text-[10px] ml-1">{{ t.count }}</span>
              </button>
            </div>
          </div>
          <p v-if="tagSearch && Object.keys(filteredGroupedTags).length === 0"
             class="text-xs text-gray-600 text-center py-2">{{ $t('header.filter.tagNotFound') }}</p>
        </div>
      </div>
    </header>

    <!-- ── Backend startup banner ── -->
    <div v-if="backendStatus !== 'ready'"
      class="flex items-center justify-center gap-3 px-4 py-3 bg-gray-900/95 border-b border-gray-800 text-sm text-gray-400">
      <svg class="w-4 h-4 animate-spin text-purple-400 shrink-0" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
      </svg>
      <span v-if="backendStatus === 'connecting'">{{ $t('header.connecting') }}</span>
      <span v-else>{{ $t('header.starting') }}</span>
    </div>

    <!-- ── Startup warnings banner ── -->
    <div v-if="!dismissedWarnings && backendActivity?.warnings?.length"
      class="px-4 py-3 bg-amber-900/80 border-b border-amber-700 text-sm text-amber-200">
      <div class="flex items-start justify-between gap-3 max-w-4xl mx-auto">
        <div class="flex items-start gap-2 min-w-0">
          <svg class="w-4 h-4 mt-0.5 shrink-0 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
          </svg>
          <ul class="space-y-1 min-w-0">
            <li v-for="(w, i) in backendActivity.warnings" :key="i" class="break-words">{{ w }}</li>
          </ul>
        </div>
        <button @click="dismissedWarnings = true"
          class="shrink-0 text-amber-400 hover:text-amber-200 transition-colors">
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- ── Grid ── -->
    <main ref="mainEl" class="flex-1 min-h-0 p-2 overflow-y-auto">

      <!-- ── Folder list ── -->
      <template v-if="viewMode === 'folder' && activeDir === null">
        <div v-if="dirsLoading" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          <div v-for="i in 10" :key="`dsk-${i}`" class="rounded-xl bg-gray-900 ring-1 ring-gray-800 overflow-hidden">
            <div class="aspect-square bg-gray-800 animate-pulse"></div>
            <div class="p-2 space-y-1">
              <div class="h-3 bg-gray-700 animate-pulse rounded w-2/3"></div>
              <div class="h-3 bg-gray-700 animate-pulse rounded w-1/3"></div>
            </div>
          </div>
        </div>
        <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          <div v-for="dir in dirs" :key="dir.path_rel"
            @click="openDir(dir)"
            class="cursor-pointer rounded-xl overflow-hidden bg-gray-900 ring-1 ring-gray-800 hover:ring-yellow-600/60 transition-all duration-200 select-none">
            <div class="relative aspect-square bg-gray-800 overflow-hidden">
              <div v-if="dir.preview_sha256s.length >= 4" class="absolute inset-0 grid grid-cols-2 grid-rows-2 gap-0.5 p-0.5">
                <img v-for="sha in dir.preview_sha256s.slice(0,4)" :key="sha"
                  :src="`/api/thumbnails/${sha}.webp`"
                  class="w-full h-full object-cover rounded-sm" loading="lazy" />
              </div>
              <img v-else-if="dir.preview_sha256s.length"
                :src="`/api/thumbnails/${dir.preview_sha256s[0]}.webp`"
                class="absolute inset-0 w-full h-full object-cover" loading="lazy" />
              <div v-else class="absolute inset-0 flex items-center justify-center text-gray-600 text-4xl">📁</div>
              <span class="absolute top-1.5 right-1.5 text-yellow-400/80 text-sm pointer-events-none drop-shadow">📁</span>
            </div>
            <div class="px-2.5 py-2">
              <p class="text-xs text-gray-200 truncate font-medium">{{ dir.name || $t('gallery.rootFolder') }}</p>
              <p class="text-xs text-gray-500 mt-0.5">{{ $t('gallery.imageCount', { n: dir.count.toLocaleString() }) }}</p>
            </div>
          </div>
        </div>
        <p v-if="!dirsLoading && !dirs.length" class="text-center text-gray-600 mt-24 text-sm">{{ $t('gallery.noFolders') }}</p>
      </template>

      <!-- ── Images inside folder ── -->
      <template v-else-if="viewMode === 'folder' && activeDir !== null">
        <div class="flex items-center gap-2 mb-3">
          <button @click="closeDir"
            class="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-xs text-gray-300 transition-colors">
            {{ $t('gallery.folderBack') }}
          </button>
          <span class="text-xs text-yellow-400/80">📁</span>
          <span class="text-xs text-gray-300 truncate">{{ activeDir || $t('gallery.rootFolder') }}</span>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-2 items-start">
          <div v-for="img in images" :key="img.sha256"
            class="cursor-pointer group rounded-lg overflow-hidden bg-gray-900 transition-all duration-200"
            :class="[
              selectedIds.has(img.sha256)
                ? 'ring-2 ring-purple-500 shadow-[0_0_14px_rgba(168,85,247,0.45)]'
                : 'ring-1 ring-gray-800 hover:ring-gray-700',
              hoveredThumbnailSha === img.sha256
                ? 'ring-2 ring-purple-400/80 shadow-[0_0_22px_rgba(168,85,247,0.65)] scale-[1.03]'
                : ''
            ]"
            @click="onImageClick(img)">
            <div class="relative bg-gray-800 h-48">
              <img :src="`/api/thumbnails/${img.sha256}.webp`" :alt="img.name"
                class="w-full h-full object-contain block group-hover:scale-[1.03] transition-transform duration-200"
                loading="lazy" />
              <div v-if="selectedIds.has(img.sha256)"
                class="absolute inset-0 bg-purple-500/10 pointer-events-none" />
              <div
                class="absolute top-1.5 left-1.5 w-5 h-5 rounded-full flex items-center justify-center transition-all duration-200 cursor-pointer z-10"
                :class="selectedIds.has(img.sha256)
                  ? 'bg-purple-500 border-2 border-purple-300 opacity-100 scale-100 shadow-[0_0_8px_rgba(168,85,247,0.8)]'
                  : 'bg-black/40 border-2 border-gray-400/70 opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100'"
                @click.stop="onCheckboxClick($event, img)">
                <svg v-if="selectedIds.has(img.sha256)" class="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
                  <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </div>
              <div class="absolute top-1.5 right-1.5 flex flex-col items-end gap-0.5 pointer-events-none">
                <span v-if="img.batch_category === 'AI'"
                  class="px-1 py-0.5 bg-teal-600/80 rounded text-xs text-white leading-none">AI</span>
                <span v-if="img.batch_category === 'NR'"
                  class="px-1 py-0.5 bg-gray-500/80 rounded text-xs text-white leading-none">NR</span>
                <span v-if="img.embedding_status === 'done'"
                  class="px-1 py-0.5 bg-purple-600/80 rounded text-xs text-white leading-none">WD14</span>
              </div>
              <div v-if="img.match_count !== undefined || img._score !== undefined || alignmentCache.get(img.sha256)?.score !== undefined"
                class="absolute bottom-1.5 right-1.5 px-1 py-0.5 bg-black/60 rounded text-xs text-white/80 leading-none pointer-events-none flex flex-col items-end gap-0.5">
                <span v-if="img.match_count !== undefined">{{ img.match_count }}/{{ img.match_total }}&nbsp;({{ Math.round(img.match_count / img.match_total * 100) }}%)</span>
                <span v-if="img._score !== undefined" class="text-yellow-300/90">vec&nbsp;{{ Math.round(img._score * 100) }}%</span>
                <span v-if="alignmentCache.get(img.sha256)?.score !== undefined" class="text-orange-300/90">align&nbsp;{{ Math.round(alignmentCache.get(img.sha256).score * 100) }}%</span>
              </div>
            </div>
            <div class="px-2 py-1.5">
              <p class="text-xs text-gray-200 truncate leading-tight">{{ img.name }}</p>
              <p class="text-xs text-gray-500 mt-0.5 leading-tight">file {{ formatSize(img.size) }}&nbsp;&nbsp;{{ formatMtime(img.mtime) }}</p>
              <div class="flex items-center mt-1" @click.stop>
                <button v-for="n in 5" :key="n" @click="setImageRating(img, n)"
                  class="text-sm leading-none transition-colors px-0.5"
                  :class="img.star_rating >= n ? 'text-yellow-400' : 'text-gray-700 hover:text-yellow-400/60'">★</button>
              </div>
            </div>
          </div>
          <template v-if="loading">
            <div v-for="i in 24" :key="`fsk-${i}`" class="rounded-lg overflow-hidden bg-gray-900 ring-1 ring-gray-800">
              <div class="bg-gray-800 animate-pulse h-48"></div>
              <div class="px-2 py-1.5 space-y-1">
                <div class="h-3 bg-gray-700 animate-pulse rounded w-3/4"></div>
                <div class="h-3 bg-gray-700 animate-pulse rounded w-1/2"></div>
              </div>
            </div>
          </template>
        </div>
        <p v-if="!loading && !images.length" class="text-center text-gray-600 mt-24 text-sm">{{ $t('gallery.folderEmpty') }}</p>
      </template>

      <!-- ── Flat view (existing) ── -->
      <template v-else>
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-2 items-start">
          <div v-for="img in images" :key="img.sha256"
            class="cursor-pointer group rounded-lg overflow-hidden bg-gray-900 transition-all duration-200"
            :class="[
              selectedIds.has(img.sha256)
                ? 'ring-2 ring-purple-500 shadow-[0_0_14px_rgba(168,85,247,0.45)]'
                : 'ring-1 ring-gray-800 hover:ring-gray-700',
              hoveredThumbnailSha === img.sha256
                ? 'ring-2 ring-purple-400/80 shadow-[0_0_22px_rgba(168,85,247,0.65)] scale-[1.03]'
                : ''
            ]"
            @click="onImageClick(img)">
            <div class="relative bg-gray-800 h-48">
              <img :src="`/api/thumbnails/${img.sha256}.webp`" :alt="img.name"
                class="w-full h-full object-contain block group-hover:scale-[1.03] transition-transform duration-200"
                loading="lazy" />

              <!-- Selected overlay -->
              <div v-if="selectedIds.has(img.sha256)"
                class="absolute inset-0 bg-purple-500/10 pointer-events-none" />

              <!-- Checkbox -->
              <div
                class="absolute top-1.5 left-1.5 w-5 h-5 rounded-full flex items-center justify-center transition-all duration-200 cursor-pointer z-10"
                :class="selectedIds.has(img.sha256)
                  ? 'bg-purple-500 border-2 border-purple-300 opacity-100 scale-100 shadow-[0_0_8px_rgba(168,85,247,0.8)]'
                  : 'bg-black/40 border-2 border-gray-400/70 opacity-0 group-hover:opacity-100 scale-90 group-hover:scale-100'"
                @click.stop="onCheckboxClick($event, img)">
                <svg v-if="selectedIds.has(img.sha256)" class="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
                  <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
              </div>

              <div class="absolute top-1.5 right-1.5 flex flex-col items-end gap-0.5 pointer-events-none">
                <span v-if="img.batch_category === 'AI'"
                  class="px-1 py-0.5 bg-teal-600/80 rounded text-xs text-white leading-none">AI</span>
                <span v-if="img.batch_category === 'NR'"
                  class="px-1 py-0.5 bg-gray-500/80 rounded text-xs text-white leading-none">NR</span>
                <span v-if="img.embedding_status === 'done'"
                  class="px-1 py-0.5 bg-purple-600/80 rounded text-xs text-white leading-none">WD14</span>
              </div>
              <div v-if="img.match_count !== undefined || img._score !== undefined || alignmentCache.get(img.sha256)?.score !== undefined"
                class="absolute bottom-1.5 right-1.5 px-1 py-0.5 bg-black/60 rounded text-xs text-white/80 leading-none pointer-events-none flex flex-col items-end gap-0.5">
                <span v-if="img.match_count !== undefined">{{ img.match_count }}/{{ img.match_total }}&nbsp;({{ Math.round(img.match_count / img.match_total * 100) }}%)</span>
                <span v-if="img._score !== undefined" class="text-yellow-300/90">vec&nbsp;{{ Math.round(img._score * 100) }}%</span>
                <span v-if="alignmentCache.get(img.sha256)?.score !== undefined" class="text-orange-300/90">align&nbsp;{{ Math.round(alignmentCache.get(img.sha256).score * 100) }}%</span>
              </div>
            </div>
            <div class="px-2 py-1.5">
              <p class="text-xs text-gray-200 truncate leading-tight">{{ img.name }}</p>
              <p class="text-xs text-gray-500 mt-0.5 leading-tight">file {{ formatSize(img.size) }}&nbsp;&nbsp;{{ formatMtime(img.mtime) }}</p>
              <div class="flex items-center mt-1" @click.stop>
                <button v-for="n in 5" :key="n" @click="setImageRating(img, n)"
                  class="text-sm leading-none transition-colors px-0.5"
                  :class="img.star_rating >= n ? 'text-yellow-400' : 'text-gray-700 hover:text-yellow-400/60'">★</button>
              </div>
            </div>
          </div>

          <!-- Skeletons -->
          <template v-if="loading">
            <div v-for="i in 24" :key="`sk-${i}`" class="rounded-lg overflow-hidden bg-gray-900 ring-1 ring-gray-800">
              <div class="bg-gray-800 animate-pulse h-48"></div>
              <div class="px-2 py-1.5 space-y-1">
                <div class="h-3 bg-gray-700 animate-pulse rounded w-3/4"></div>
                <div class="h-3 bg-gray-700 animate-pulse rounded w-1/2"></div>
              </div>
            </div>
          </template>
        </div>
        <div ref="sentinel" class="h-8" />
        <p v-if="!loading && images.length===0 && !isSearchMode && backendStatus === 'ready'" class="text-center text-gray-600 mt-24 text-sm">
          {{ $t('gallery.noImages') }}
        </p>
        <p v-else-if="!loading && images.length===0 && !isSearchMode && backendStatus !== 'ready'" class="text-center text-gray-600 mt-24 text-sm">
          {{ $t('gallery.starting') }}
        </p>
        <div v-if="!loading && images.length===0 && isSearchMode" class="text-center text-gray-600 mt-24 text-sm space-y-1">
          <p>{{ $t('gallery.noResults') }}</p>
          <p class="text-xs text-gray-700">
            <span v-if="searchQuery">「{{ searchQuery }}」</span>
            <span v-if="searchQuery && activeTags.length"> + </span>
            <span v-if="activeTags.length">{{ $t('gallery.searchTag', { tag: activeTags.join(', ') }) }}</span>
          </p>
        </div>
      </template>

    </main>

    <!-- ── Prompt Refine Panel (2-pane) ── -->
    <Teleport to="body">
      <div v-if="showRefine" class="fixed inset-0 z-[75] bg-black/90 flex items-center justify-center p-3"
        @mousedown.self="refineOverlayMousedownOnBg = true"
        @mouseup.self="if (refineOverlayMousedownOnBg) showRefine = false; refineOverlayMousedownOnBg = false"
        @mouseleave="refineOverlayMousedownOnBg = false">
        <div class="bg-gray-900 rounded-2xl w-full max-w-6xl shadow-2xl border border-gray-800 h-[95vh] flex flex-col overflow-hidden"
          @mousedown="refineOverlayMousedownOnBg = false">

          <!-- Header -->
          <div class="flex items-center justify-between px-6 py-4 border-b border-gray-800 flex-shrink-0">
            <div class="flex items-center gap-3">
              <span class="text-lg">✨</span>
              <h2 class="font-semibold text-gray-100 text-base">{{ $t('refine.title') }}</h2>
              <span class="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded-full">{{ $t('refine.refCount', { n: selectedCount }) }}</span>
              <!-- Phase badge -->
              <span v-if="refinePhase === 'llm'"
                class="flex items-center gap-1.5 text-xs text-purple-300 bg-purple-900/40 border border-purple-700/40 px-2 py-0.5 rounded-full">
                <span class="w-1.5 h-1.5 rounded-full bg-purple-400 inline-block"></span>
                {{ $t('refine.phaseLlm') }}
              </span>
              <span v-else-if="refinePhase === 'comfy'"
                class="flex items-center gap-1.5 text-xs text-blue-300 bg-blue-900/40 border border-blue-700/40 px-2 py-0.5 rounded-full">
                <span class="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block"></span>
                {{ $t('refine.phaseComfy') }}
              </span>
              <span v-else-if="refinePhase === 'done' && comfyGeneratedImages.length"
                class="flex items-center gap-1.5 text-xs text-green-300 bg-green-900/40 border border-green-700/40 px-2 py-0.5 rounded-full">
                {{ $t('refine.phaseDone', { n: comfyGeneratedImages.length }) }}
              </span>
              <span v-if="refineDirectPrompt !== null"
                class="flex items-center gap-1.5 text-xs text-cyan-300 bg-cyan-900/40 border border-cyan-700/40 px-2 py-0.5 rounded-full">
                <span class="w-1.5 h-1.5 rounded-full bg-cyan-400 inline-block"></span>
                {{ $t('refine.modeBadgeBypass') }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <button v-if="showInspire && brainstormText"
                @click="showRefine = false"
                class="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-900/50 hover:bg-indigo-800/70 border border-indigo-700/40 rounded-lg text-xs text-indigo-300 hover:text-indigo-100 transition-colors">
                {{ $t('inspire.backToBrainstorm') }}
              </button>
              <button @click="showRefine = false" class="text-gray-500 hover:text-gray-200 text-xl leading-none w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
            </div>
          </div>

          <!-- 2-pane body -->
          <div class="flex flex-1 min-h-0 divide-x divide-gray-800">

            <!-- ── Left Pane: Settings + Prompt output ── -->
            <div class="w-[420px] flex-shrink-0 flex flex-col min-h-0">
              <div class="overflow-y-auto flex-1 p-5 space-y-4">

                <!-- Direct prompt banner -->
                <Transition
                  enter-active-class="transition-all duration-200"
                  enter-from-class="opacity-0 -translate-y-2"
                  enter-to-class="opacity-100 translate-y-0"
                  leave-active-class="transition-all duration-150"
                  leave-from-class="opacity-100 translate-y-0"
                  leave-to-class="opacity-0 -translate-y-2">
                  <div v-if="refineDirectPrompt !== null"
                    class="bg-cyan-950/60 border border-cyan-600/50 rounded-xl p-3.5 space-y-2.5">
                    <div class="flex items-start justify-between gap-2">
                      <div class="flex items-center gap-2 min-w-0">
                        <span class="text-base shrink-0">🪞</span>
                        <div class="min-w-0">
                          <p class="text-xs font-semibold text-cyan-300">{{ $t('refine.directFromInversion') }}</p>
                          <p class="text-[10px] text-cyan-600 mt-0.5">
                            {{ refineDirectPromptSource === 'inversion-tags' ? $t('refine.directFormatTags') : $t('refine.directFormatProse') }}
                            {{ $t('refine.directBypassNote') }}
                          </p>
                        </div>
                      </div>
                      <button @click="refineDirectPrompt = null; refineDirectPromptSource = ''; refineDirectNegativePrompt = ''"
                        class="shrink-0 text-[10px] text-cyan-700 hover:text-cyan-400 px-2 py-1 bg-cyan-900/40 hover:bg-cyan-800/50 rounded-lg border border-cyan-800/40 transition-colors">
                        {{ $t('refine.directClear') }}
                      </button>
                    </div>
                    <div class="bg-cyan-900/20 border border-cyan-800/30 rounded-lg px-3 py-2 max-h-20 overflow-y-auto">
                      <p class="text-[10px] text-cyan-200/70 font-mono leading-relaxed break-all">{{ refineDirectPrompt }}</p>
                    </div>
                    <div v-if="refineDirectNegativePrompt"
                      class="bg-rose-900/20 border border-rose-800/30 rounded-lg px-3 py-2 max-h-20 overflow-y-auto">
                      <p class="text-[9px] text-rose-500/60 mb-1">{{ $t('refine.negativeLabel') }}</p>
                      <p class="text-[10px] text-rose-200/60 font-mono leading-relaxed break-all">{{ refineDirectNegativePrompt }}</p>
                    </div>
                    <p class="text-[10px] text-cyan-700">{{ $t('refine.directClearHint') }}</p>
                  </div>
                </Transition>

                <!-- Instruction -->
                <div>
                  <label class="text-xs text-gray-500 mb-1 block">{{ $t('refine.extraHint') }}</label>
                  <div class="flex gap-1.5">
                    <input v-model="refineInstruction" type="text"
                      :placeholder="$t('refine.extraPlaceholder')"
                      :disabled="refining"
                      class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-purple-500 disabled:opacity-50" />
                    <button @click="showInstructionModal = true" :disabled="refining"
                      class="flex-shrink-0 px-2.5 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-lg text-gray-400 hover:text-gray-200 transition-colors disabled:opacity-50"
                      :title="$t('refine.longInputTitle')">
                      <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M4 8h16M4 12h10M4 16h6M15 13l4 4-4 4m4-4H9" />
                      </svg>
                    </button>
                  </div>
                </div>

                <!-- Section 1: Ollama controls -->
                <details open class="group border rounded-xl overflow-hidden transition-colors"
                  :class="refineDirectPrompt !== null ? 'border-gray-800/40 opacity-50' : 'border-gray-800'">
                  <summary class="px-4 py-3 bg-gray-800/40 text-xs font-semibold text-gray-400 uppercase tracking-wide cursor-pointer list-none flex items-center justify-between hover:bg-gray-800/70 transition-colors">
                    <span class="flex items-center gap-2">
                      {{ $t('refine.ollamaControl') }}
                      <span v-if="refineDirectPrompt !== null" class="text-[9px] text-cyan-600 normal-case font-normal">{{ $t('refine.directSkipping') }}</span>
                    </span>
                    <span class="text-gray-600 group-open:rotate-180 transition-transform">▼</span>
                  </summary>
                  <div class="p-4 space-y-4">
                    <div>
                      <label class="text-xs text-gray-500 flex justify-between mb-1.5">
                        <span>{{ $t('refine.temperature') }}</span>
                        <span class="text-purple-400 font-mono">{{ refineTemp.toFixed(1) }}</span>
                      </label>
                      <input v-model.number="refineTemp" type="range" min="0.0" max="1.5" step="0.1"
                        :disabled="refining || refineDirectPrompt !== null" class="w-full accent-purple-500 disabled:opacity-50" />
                      <div class="flex justify-between text-xs text-gray-600 mt-0.5">
                        <span>{{ $t('refine.tempLow') }}</span><span>{{ $t('refine.tempHigh') }}</span>
                      </div>
                    </div>
                    <div>
                      <label class="text-xs text-gray-500 block mb-1">{{ $t('refine.numCtx') }}</label>
                      <select v-model.number="refineNumCtx" :disabled="refining || refineDirectPrompt !== null"
                        class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-purple-500 disabled:opacity-50">
                        <option :value="8192">8,192</option>
                        <option :value="16384">{{ $t('refine.numCtxRecommended') }}</option>
                        <option :value="32768">32,768</option>
                      </select>
                    </div>
                  </div>
                </details>

                <!-- Section 2: Output style -->
                <details :open="refineDirectPrompt === null" class="group border rounded-xl overflow-hidden transition-colors"
                  :class="refineDirectPrompt !== null ? 'border-gray-800/40 opacity-50 pointer-events-none' : 'border-gray-800'">
                  <summary class="px-4 py-3 bg-gray-800/40 text-xs font-semibold text-gray-400 uppercase tracking-wide cursor-pointer list-none flex items-center justify-between hover:bg-gray-800/70 transition-colors">
                    <span class="flex items-center gap-2">
                      {{ $t('refine.outputStyle') }}
                      <span v-if="refineDirectPrompt !== null" class="text-[9px] text-cyan-600 normal-case font-normal">{{ $t('refine.directSkipping') }}</span>
                    </span>
                    <span class="text-gray-600 group-open:rotate-180 transition-transform">▼</span>
                  </summary>
                  <div class="p-4 space-y-4">
                    <!-- Output style: natural / danbooru / detailed -->
                    <div class="grid grid-cols-3 gap-2">
                      <button @click="refineStyle = 'natural'" :disabled="refining"
                        :class="refineStyle === 'natural' ? 'bg-purple-900/60 border-purple-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'"
                        class="py-2.5 px-3 border rounded-lg text-xs font-medium transition-colors disabled:opacity-50 text-left">
                        <div class="font-semibold">{{ $t('refine.styleNaturalLabel') }}</div>
                        <div class="text-gray-500 font-normal mt-0.5">FLUX / Anima</div>
                      </button>
                      <button @click="refineStyle = 'danbooru'" :disabled="refining"
                        :class="refineStyle === 'danbooru' ? 'bg-purple-900/60 border-purple-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'"
                        class="py-2.5 px-3 border rounded-lg text-xs font-medium transition-colors disabled:opacity-50 text-left">
                        <div class="font-semibold">{{ $t('refine.styleDanbooruLabel') }}</div>
                        <div class="text-gray-500 font-normal mt-0.5">{{ $t('refine.styleDanbooruDesc') }}</div>
                      </button>
                      <button @click="refineStyle = 'detailed'" :disabled="refining"
                        :class="refineStyle === 'detailed' ? 'bg-purple-900/60 border-purple-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'"
                        class="py-2.5 px-3 border rounded-lg text-xs font-medium transition-colors disabled:opacity-50 text-left">
                        <div class="font-semibold">{{ $t('refine.styleDetailedLabel') }}</div>
                        <div class="text-gray-500 font-normal mt-0.5">{{ $t('refine.styleDetailedDesc') }}</div>
                      </button>
                    </div>

                    <!-- Instruction mode: none / basic / enhanced -->
                    <div>
                      <p class="text-xs text-gray-400 mb-2">{{ $t('refine.instructionModeLabel') }}</p>
                      <div class="grid grid-cols-3 gap-2">
                        <button @click="refineInstructionMode = 'none'" :disabled="refining"
                          :class="refineInstructionMode === 'none' ? 'bg-gray-700 border-gray-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-500 hover:border-gray-500'"
                          class="py-1.5 px-2 border rounded-lg text-xs font-medium transition-colors disabled:opacity-50 text-center">
                          {{ $t('refine.instructionModeNone') }}
                        </button>
                        <button @click="refineInstructionMode = 'basic'" :disabled="refining"
                          :class="refineInstructionMode === 'basic' ? 'bg-purple-900/60 border-purple-500 text-white' : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'"
                          class="py-1.5 px-2 border rounded-lg text-xs font-medium transition-colors disabled:opacity-50 text-center">
                          {{ $t('refine.instructionModeBasic') }}
                        </button>
                        <button @click="refineInstructionMode = 'enhanced'" :disabled="refining"
                          :class="refineInstructionMode === 'enhanced' ? 'bg-amber-900/60 border-amber-500 text-amber-200' : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'"
                          class="py-1.5 px-2 border rounded-lg text-xs font-medium transition-colors disabled:opacity-50 text-center">
                          {{ $t('refine.instructionModeEnhanced') }}
                        </button>
                      </div>
                      <p class="text-[10px] text-gray-600 mt-1.5">{{ $t('refine.instructionModeDesc_' + refineInstructionMode) }}</p>
                    </div>
                    <div class="flex items-center justify-between"
                      :class="refineDirectPrompt !== null ? 'opacity-50 pointer-events-none' : ''">
                      <div>
                        <p class="text-xs text-gray-300">{{ $t('refine.negPrompt') }}</p>
                        <p class="text-xs text-gray-600 mt-0.5">{{ refineDirectPrompt !== null ? $t('refine.negPromptFromInversion') : $t('refine.negPromptDesc') }}</p>
                      </div>
                      <button @click="refineNegative = !refineNegative" :disabled="refining || refineDirectPrompt !== null"
                        :class="refineNegative ? 'bg-purple-600' : 'bg-gray-700'"
                        class="relative w-10 h-5 rounded-full transition-colors disabled:opacity-50 flex-shrink-0 ml-4">
                        <span :class="refineNegative ? 'translate-x-5' : 'translate-x-0.5'"
                          class="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform block"></span>
                      </button>
                    </div>
                  </div>
                </details>

                <!-- Section 3: ComfyUI -->
                <details open class="group border border-gray-800 rounded-xl overflow-hidden">
                  <summary class="px-4 py-3 bg-gray-800/40 text-xs font-semibold text-gray-400 uppercase tracking-wide cursor-pointer list-none flex items-center justify-between hover:bg-gray-800/70 transition-colors">
                    <span>{{ $t('refine.comfyAuto') }}</span>
                    <div class="flex items-center gap-2">
                      <span v-if="refineAutoSubmit" class="text-purple-400 text-xs font-normal normal-case">ON</span>
                      <span class="text-gray-600 group-open:rotate-180 transition-transform">▼</span>
                    </div>
                  </summary>
                  <div class="p-4 space-y-4">
                    <div class="flex items-center justify-between">
                      <div>
                        <p class="text-xs text-gray-300">{{ $t('refine.comfyAutoLabel') }}</p>
                        <p class="text-xs text-gray-600 mt-0.5">{{ $t('refine.comfyAutoDesc') }}</p>
                      </div>
                      <button @click="refineAutoSubmit = !refineAutoSubmit" :disabled="refining"
                        :class="refineAutoSubmit ? 'bg-purple-600' : 'bg-gray-700'"
                        class="relative w-10 h-5 rounded-full transition-colors disabled:opacity-50 flex-shrink-0 ml-4">
                        <span :class="refineAutoSubmit ? 'translate-x-5' : 'translate-x-0.5'"
                          class="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform block"></span>
                      </button>
                    </div>
                    <template v-if="refineAutoSubmit">
                      <div class="flex items-center justify-between">
                        <div>
                          <p class="text-xs text-gray-300">{{ $t('refine.batchCount') }}</p>
                          <p class="text-xs text-gray-600 mt-0.5">{{ $t('refine.batchCountDesc') }}</p>
                        </div>
                        <div class="flex items-center gap-1 bg-gray-800 border border-gray-700 rounded-lg overflow-hidden">
                          <button v-for="n in [1,2,4,8]" :key="n"
                            @click="refineBatchCount = n" :disabled="refining"
                            :class="refineBatchCount === n ? 'bg-purple-700 text-white' : 'text-gray-400 hover:bg-gray-700'"
                            class="px-2.5 py-1 text-xs transition-colors disabled:opacity-50">
                            {{ $t('refine.nImages', { n }) }}
                          </button>
                        </div>
                      </div>
                      <!-- Seed -->
                      <div class="flex items-center justify-between">
                        <div>
                          <p class="text-xs text-gray-300">{{ $t('refine.useSeedLabel') }}</p>
                          <p class="text-xs text-gray-600 mt-0.5">
                            <template v-if="refineUseSeed && refFirstImageSeed !== null">seed: {{ refFirstImageSeed }}</template>
                            <template v-else-if="refineUseSeed">{{ $t('refine.seedUnknown') }}</template>
                            <template v-else>{{ $t('refine.useSeedDesc') }}</template>
                          </p>
                        </div>
                        <button @click="refineUseSeed = !refineUseSeed" :disabled="refining"
                          :class="refineUseSeed ? 'bg-purple-600' : 'bg-gray-700'"
                          class="relative w-10 h-5 rounded-full transition-colors disabled:opacity-50 flex-shrink-0 ml-4">
                          <span :class="refineUseSeed ? 'translate-x-5' : 'translate-x-0.5'"
                            class="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform block"></span>
                        </button>
                      </div>
                      <div>
                        <label class="text-xs text-gray-500 block mb-1">{{ $t('refine.workflow') }}</label>
                        <select v-model="refineWorkflow" :disabled="refining"
                          class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-purple-500 disabled:opacity-50">
                          <option value="">{{ $t('refine.workflowSelect') }}</option>
                          <option v-for="wf in workflows" :key="wf" :value="wf">{{ wf }}</option>
                        </select>
                        <p v-if="!workflows.length" class="text-xs text-yellow-500/70 mt-1">
                          {{ $t('refine.workflowHint') }}
                        </p>
                      </div>
                      <details class="group/adv">
                        <summary class="text-xs text-gray-600 cursor-pointer hover:text-gray-400 list-none flex items-center gap-1 select-none">
                          <span class="group-open/adv:rotate-90 transition-transform inline-block">▶</span>
                          {{ $t('refine.nodeIdAdvanced') }}
                        </summary>
                        <div class="mt-2 grid grid-cols-2 gap-2">
                          <div>
                            <label class="text-xs text-gray-600 block mb-1">{{ $t('refine.positiveNodeId') }}</label>
                            <input v-model="refinePosNodeId" type="text" :placeholder="$t('refine.positiveNodePlaceholder')" :disabled="refining"
                              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 font-mono focus:outline-none focus:border-purple-500 disabled:opacity-50" />
                          </div>
                          <div>
                            <label class="text-xs text-gray-600 block mb-1">{{ $t('refine.negativeNodeId') }}</label>
                            <input v-model="refineNegNodeId" type="text" :placeholder="$t('refine.negativeNodePlaceholder')" :disabled="refining"
                              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 font-mono focus:outline-none focus:border-purple-500 disabled:opacity-50" />
                          </div>
                        </div>
                      </details>
                    </template>
                  </div>
                </details>

                <!-- Think accordion -->
                <div v-if="thinkText" class="border border-purple-900/40 rounded-xl overflow-hidden">
                  <button @click="thinkOpen = !thinkOpen"
                    class="w-full flex items-center justify-between px-4 py-2.5 bg-purple-900/20 text-xs text-purple-300 hover:bg-purple-900/30 transition-colors">
                    <span class="flex items-center gap-2">
                      <span>🧠</span>
                      <span>{{ $t('refine.thinkProcess') }}</span>
                      <span v-if="refinePhase === 'llm'" class="w-1.5 h-1.5 rounded-full bg-purple-400 inline-block"></span>
                    </span>
                    <span class="text-purple-600">{{ thinkOpen ? '▲' : '▼' }}</span>
                  </button>
                  <div v-show="thinkOpen" class="px-4 py-3 bg-gray-950/80 max-h-40 overflow-y-auto">
                    <p class="text-xs text-gray-500 font-mono whitespace-pre-wrap leading-relaxed">{{ thinkText }}</p>
                  </div>
                </div>

                <!-- Prompt results -->
                <div v-if="positivePrompt" class="space-y-3">
                  <div v-if="proseMissing" class="px-3 py-2 bg-yellow-900/40 border border-yellow-700/50 rounded-lg">
                    <p class="text-xs text-yellow-300">{{ $t('refine.proseMissing') }}</p>
                  </div>
                  <!-- Tag format toggle -->
                  <div class="flex items-center gap-1.5">
                    <span class="text-[10px] text-gray-600 uppercase tracking-wide">{{ $t('refine.tagFormatLabel') }}</span>
                    <div class="flex rounded-md overflow-hidden border border-gray-700 text-[10px]">
                      <button @click="refineTagFormat = 'underscore'"
                        :class="refineTagFormat === 'underscore' ? 'bg-purple-700 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'"
                        class="px-2 py-0.5 transition-colors font-mono">long_hair</button>
                      <button @click="refineTagFormat = 'space'"
                        :class="refineTagFormat === 'space' ? 'bg-purple-700 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'"
                        class="px-2 py-0.5 transition-colors font-mono border-l border-gray-700">long hair</button>
                    </div>
                  </div>
                  <div>
                    <div class="flex items-center justify-between mb-1.5">
                      <p class="text-xs font-semibold text-purple-400 uppercase tracking-wide">{{ $t('refine.positivePromptLabel') }}</p>
                      <button @click="copyToClipboard(fmtPrompt(positivePrompt))"
                        class="text-xs text-gray-500 hover:text-gray-200 px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded transition-colors">{{ $t('refine.copy') }}</button>
                    </div>
                    <p class="text-xs text-gray-200 bg-gray-800/80 rounded-lg p-3 whitespace-pre-wrap break-words leading-relaxed max-h-36 overflow-y-auto">{{ fmtPrompt(positivePrompt) }}</p>
                  </div>
                  <div v-if="negativePromptText">
                    <div class="flex items-center justify-between mb-1.5">
                      <p class="text-xs font-semibold text-red-400 uppercase tracking-wide">{{ $t('refine.negativePromptLabel') }}</p>
                      <button @click="copyToClipboard(fmtPrompt(negativePromptText))"
                        class="text-xs text-gray-500 hover:text-gray-200 px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded transition-colors">{{ $t('refine.copy') }}</button>
                    </div>
                    <p class="text-xs text-red-200/70 bg-gray-800/80 rounded-lg p-3 whitespace-pre-wrap break-words leading-relaxed max-h-28 overflow-y-auto">{{ fmtPrompt(negativePromptText) }}</p>
                  </div>
                  <div v-if="removedTags.length" class="bg-amber-950/20 border border-amber-800/30 rounded-lg p-3 space-y-1.5">
                    <p class="text-[10px] text-amber-400 font-semibold uppercase tracking-wide">{{ $t('refine.removedTagsLabel') }}</p>
                    <p class="text-[10px] text-amber-200/50 leading-relaxed">{{ $t('refine.removedTagsHint') }}</p>
                    <div class="flex flex-wrap gap-1">
                      <span v-for="tag in removedTags" :key="tag"
                        class="text-[10px] text-amber-300 bg-amber-900/30 border border-amber-700/40 px-1.5 py-0.5 rounded font-mono">{{ fmtPrompt(tag) }}</span>
                    </div>
                  </div>
                </div>

              </div>

              <!-- Run / Cancel button (pinned bottom of left pane) -->
              <div class="p-4 border-t border-gray-800 flex-shrink-0 flex gap-2">
                <button @click="runRefine" :disabled="refining || selectedCount === 0 || (refineAutoSubmit && !refineWorkflow)"
                  class="flex-1 py-3 rounded-xl text-sm font-semibold transition-all"
                  :class="refining
                    ? 'bg-gray-800 text-gray-400 cursor-not-allowed'
                    : (refineAutoSubmit && !refineWorkflow)
                      ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
                      : refineDirectPrompt !== null
                        ? 'bg-gradient-to-r from-cyan-700 to-cyan-600 hover:from-cyan-600 hover:to-cyan-500 text-white shadow-lg shadow-cyan-900/30'
                        : 'bg-gradient-to-r from-purple-600 to-purple-500 hover:from-purple-500 hover:to-purple-400 text-white shadow-lg shadow-purple-900/30'">
                  <span v-if="!refining">
                    <template v-if="refineAutoSubmit && !refineWorkflow">{{ $t('refine.workflowRequired') }}</template>
                    <template v-else>{{ refineDirectPrompt !== null ? $t('refine.directRunLabel') : $t('refine.run', { n: selectedCount }) }}</template>
                  </span>
                  <span v-else class="flex items-center justify-center gap-2">
                    <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                    </svg>
                    <span v-if="refinePhase === 'llm'">{{ $t('refine.runningLlm') }}</span>
                    <span v-else-if="refinePhase === 'comfy'">{{ $t('refine.runningComfy') }}</span>
                    <span v-else>{{ $t('refine.running') }}</span>
                  </span>
                </button>
                <button v-if="refining" @click="cancelRefine"
                  class="px-4 py-3 rounded-xl text-sm font-semibold bg-red-900/60 hover:bg-red-800/80 text-red-300 hover:text-red-200 border border-red-800/60 transition-all">
                  {{ $t('admin.cancel') }}
                </button>
              </div>
            </div>

            <!-- ── Right Pane: Reference images + Generated output ── -->
            <div class="flex-1 min-w-0 flex flex-col min-h-0 bg-gray-950/40">

              <!-- Reference images strip -->
              <div class="flex-shrink-0 p-4 border-b border-gray-800/50">
                <div class="flex items-center justify-between mb-2">
                  <p class="text-xs text-gray-600 uppercase tracking-wide">{{ $t('refine.sourceImages') }}</p>
                  <div class="flex items-center rounded-lg overflow-hidden border border-gray-700/60 text-xs">
                    <select v-model.number="randomCount" :disabled="refining"
                      class="bg-gray-800/80 text-gray-400 px-1.5 py-1 focus:outline-none cursor-pointer border-r border-gray-700/60 disabled:opacity-50">
                      <option v-for="n in [1,2,3,4,5,6]" :key="n" :value="n" :disabled="n < pinnedShas.size">{{ $t('refine.nImages', { n }) }}</option>
                    </select>
                    <button @click="randomSelect" :disabled="refining"
                      class="px-2.5 py-1 bg-gray-800/80 hover:bg-purple-900/60 text-gray-400 hover:text-purple-300 transition-colors disabled:opacity-50 whitespace-nowrap">
                      {{ $t('refine.randomSelect') }}
                    </button>
                  </div>
                </div>
                <div class="flex gap-2 overflow-x-auto pb-2">
                  <div v-for="sha256 in [...selectedIds].slice(0, 6)" :key="sha256"
                    class="relative flex-shrink-0 w-28">
                    <div class="cursor-pointer"
                      @click="togglePin(sha256)"
                      :title="pinnedShas.has(sha256) ? $t('refine.unpinTip') : $t('refine.pinTip')">
                      <img :src="`/api/thumbnails/${sha256}.webp`"
                        :class="pinnedShas.has(sha256) ? 'border-purple-500' : 'border-gray-600'"
                        class="h-28 w-28 object-cover rounded-lg border-2 transition-colors" />
                      <div :class="pinnedShas.has(sha256) ? 'bg-purple-600 text-white' : 'bg-black/50 text-gray-300'"
                        class="absolute top-1 right-1 w-6 h-6 rounded-full flex items-center justify-center text-xs transition-colors select-none">
                        📌
                      </div>
                    </div>
                    <div class="mt-1 px-0.5">
                      <div class="text-center text-[10px] text-purple-300 font-mono leading-none mb-0.5 select-none">
                        {{ normalizedPct(sha256) }}%
                      </div>
                      <input
                        type="range" min="0" max="100" step="1"
                        :value="imageWeights.get(sha256) ?? 50"
                        :disabled="refining"
                        @input="onWeightChange(sha256, Number($event.target.value))"
                        class="w-full h-1 rounded appearance-none bg-gray-700 accent-purple-500 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                        :title="$t('refine.weightTip')"
                      />
                    </div>
                  </div>
                </div>
              </div>

              <!-- Chat thread output -->
              <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden p-4 space-y-4">

                <!-- Idle placeholder -->
                <div v-if="!refineStarted" class="h-full flex flex-col items-center justify-center text-center select-none py-16"
                  :class="refineDirectPrompt !== null ? 'text-cyan-800' : 'text-gray-700'">
                  <div class="text-5xl mb-3" :class="refineDirectPrompt !== null ? 'opacity-60' : 'opacity-30'">
                    {{ refineDirectPrompt !== null ? '🪞' : '🎨' }}
                  </div>
                  <p class="text-sm"
                    v-html="(refineDirectPrompt !== null ? $t('refine.idleHintBypass') : $t('refine.idleHint')).replace('\n', '<br>')"></p>
                </div>

                <!-- Block 1: LLM streaming → final prompts (appears on start, stays) -->
                <div v-if="refineStarted">
                  <!-- Waiting for first token (refinement circle animation) -->
                  <div v-if="refinePhase === 'llm' && !streamingText && !positivePrompt"
                    class="flex flex-col items-center justify-center py-12 select-none">
                    <div class="relative w-32 h-32 mb-6">
                      <!-- Outer ring (slow rotation) -->
                      <div class="absolute inset-2 rounded-full border-2 border-purple-500/50 animate-spin" style="animation-duration: 3s"></div>
                      <!-- Center icon -->
                      <div class="absolute inset-0 flex items-center justify-center">
                        <span class="text-3xl">🔮</span>
                      </div>
                    </div>
                    <p class="text-sm text-purple-300 animate-pulse">
                      {{ refinePromptJob?.state === 'queued' ? $t('refine.queued') : $t('refine.generatingPrompt') }}
                    </p>
                    <p class="text-xs text-gray-600 mt-1">{{ $t('refine.ollamaGenerating') }}</p>
                  </div>
                  <!-- Streaming text (while generating) -->
                  <div v-if="streamingText && !positivePrompt"
                    class="relative bg-gray-900 border border-purple-900/40 rounded-xl p-4 min-h-[120px] overflow-hidden">
                    <div class="absolute top-3 right-3 flex gap-1">
                      <span class="w-1.5 h-1.5 rounded-full bg-purple-500"></span>
                    </div>
                    <p class="text-xs text-purple-400 font-semibold uppercase tracking-wide mb-2">{{ $t('refine.generating') }}</p>
                    <p class="text-sm text-gray-200 whitespace-pre-wrap break-words leading-relaxed font-mono">{{ streamingText }}<span class="text-purple-400">▌</span></p>
                  </div>
                  <!-- Final prompts (permanent once generated) -->
                  <div v-if="positivePrompt" class="bg-gray-900 border border-purple-800/30 rounded-xl p-4 space-y-3">
                    <div class="flex items-center justify-between">
                      <div class="flex items-center gap-2">
                        <p class="text-xs text-purple-400 font-semibold uppercase tracking-wide">{{ $t('refine.positivePromptLabel') }}</p>
                        <span v-if="refineDirectPromptSource"
                          class="text-[9px] text-cyan-400 bg-cyan-900/40 border border-cyan-800/40 px-1.5 py-0.5 rounded font-mono">
                          {{ $t('refine.promptSourceBypass') }}
                        </span>
                      </div>
                      <button @click="copyToClipboard(fmtPrompt(positivePrompt))"
                        class="text-xs text-gray-500 hover:text-gray-200 px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded transition-colors">{{ $t('refine.copy') }}</button>
                    </div>
                    <p class="text-xs text-gray-200 whitespace-pre-wrap break-words leading-relaxed">{{ fmtPrompt(positivePrompt) }}</p>
                    <div v-if="negativePromptText" class="pt-2 border-t border-gray-800 space-y-1.5">
                      <div class="flex items-center justify-between">
                        <p class="text-xs text-red-400 font-semibold uppercase tracking-wide">{{ $t('refine.negativePromptLabel') }}</p>
                        <button @click="copyToClipboard(fmtPrompt(negativePromptText))"
                          class="text-xs text-gray-500 hover:text-gray-200 px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded transition-colors">{{ $t('refine.copy') }}</button>
                      </div>
                      <p class="text-xs text-red-200/70 whitespace-pre-wrap break-words leading-relaxed">{{ fmtPrompt(negativePromptText) }}</p>
                    </div>
                    <div v-if="removedTags.length" class="pt-2 border-t border-gray-800 space-y-1.5">
                      <p class="text-xs text-amber-400 font-semibold uppercase tracking-wide">{{ $t('refine.removedTagsLabel') }}</p>
                      <p class="text-xs text-amber-200/60 leading-relaxed">{{ $t('refine.removedTagsHint') }}</p>
                      <div class="flex flex-wrap gap-1">
                        <span v-for="tag in removedTags" :key="tag"
                          class="text-[10px] text-amber-300 bg-amber-900/30 border border-amber-700/40 px-1.5 py-0.5 rounded font-mono">{{ fmtPrompt(tag) }}</span>
                      </div>
                    </div>
                  </div>
                  <!-- Done without ComfyUI -->
                  <div v-if="refinePhase === 'done' && positivePrompt && !refineAutoSubmit"
                    class="text-center py-2">
                    <p class="text-xs text-green-500">{{ $t('refine.done') }}</p>
                  </div>
                </div>

                <!-- Block 2: ComfyUI progress (appears when comfy starts, stays) -->
                <div v-if="refineStarted && (refinePhase === 'comfy' || comfyGeneratedImages.length)" class="space-y-3">
                  <!-- Progress bar (only while generating) -->
                  <div v-if="refinePhase === 'comfy'" class="bg-gray-900/60 border border-blue-900/30 rounded-xl p-4 space-y-3">
                    <p class="text-xs text-blue-400 font-semibold uppercase tracking-wide flex items-center gap-2">
                      <span class="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse inline-block"></span>
                      {{ $t('refine.comfyGenerating') }}
                      <span v-if="refineDirectPromptSource"
                        class="text-[9px] text-cyan-400 bg-cyan-900/40 border border-cyan-800/40 px-1.5 py-0.5 rounded font-mono normal-case font-normal">
                        {{ $t('refine.promptSourceBypass') }}
                      </span>
                      <span v-if="refineGenJob?.progress_text" class="text-gray-500 font-normal font-mono normal-case">
                        ({{ refineGenJob.progress_text }})
                      </span>
                    </p>
                    <ProgressBar
                      :progress="(refineGenJob?.progress_indeterminate || (!refineGenJob && comfyProgress.max === 0))
                        ? 0
                        : (refineGenJob
                            ? Math.max(5, refineGenJob.progress * 100) / 100
                            : comfyProgress.value / comfyProgress.max)"
                      :indeterminate="refineGenJob?.progress_indeterminate || (!refineGenJob && comfyProgress.max === 0)"
                      color="purple-gradient"
                      size="md"
                    />
                  </div>
                  <!-- Generated images (accumulate as they arrive) -->
                  <div v-for="sha256 in comfyGeneratedImages" :key="sha256"
                    class="relative group rounded-xl overflow-hidden border border-gray-700/40">
                    <img :src="`/api/originals/${sha256}`" class="w-full max-h-[70vh] object-contain block" :alt="sha256"
                      @error="e => retryImageLoad(e, sha256)" />
                    <div class="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors flex items-end justify-end p-3 opacity-0 group-hover:opacity-100">
                      <button @click="copyToClipboard(fmtPrompt(positivePrompt))"
                        class="px-2 py-1 bg-gray-900/90 text-xs text-gray-200 rounded hover:bg-gray-800 transition-colors">
                        {{ $t('refine.copyPrompt') }}
                      </button>
                    </div>
                    <div class="absolute top-2 right-2 px-1.5 py-0.5 bg-green-600/80 rounded text-xs text-white font-medium pointer-events-none">
                      {{ $t('refine.saved') }}
                    </div>
                  </div>
                  <!-- Final count -->
                  <div v-if="refinePhase === 'done' && comfyGeneratedImages.length" class="text-center py-1">
                    <p class="text-xs text-green-400">{{ $t('refine.comfyDone', { n: comfyGeneratedImages.length }) }}</p>
                  </div>
                  <!-- New job: start a refinement with a different image set while generation is already submitted (comfy / done) -->
                  <div v-if="refinePhase === 'comfy' || refinePhase === 'done'" class="flex justify-end pt-1">
                    <button @click="startNewRefineJob"
                      class="px-3 py-1.5 text-xs font-medium rounded-lg bg-indigo-900/60 hover:bg-indigo-800/80 text-indigo-300 hover:text-white border border-indigo-700/40 transition-colors">
                      {{ $t('refine.newJob') }}
                    </button>
                  </div>
                  <!-- Error message -->
                  <div v-if="refineErrorMsg" class="px-3 py-2 bg-red-900/40 border border-red-700/50 rounded-lg">
                    <p class="text-xs text-red-300 break-all">⚠ {{ refineErrorMsg }}</p>
                  </div>
                </div>

              </div>
            </div>

          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Instruction Modal ── -->
    <Teleport to="body">
      <div v-if="showInstructionModal" class="fixed inset-0 z-[90] bg-black/80 flex items-center justify-center p-4"
        @mousedown.self="showInstructionModal = false">
        <div class="bg-gray-900 border border-gray-700 rounded-2xl w-full max-w-lg shadow-2xl flex flex-col gap-3 p-5">
          <div class="flex items-center justify-between">
            <p class="text-sm font-semibold text-gray-200">{{ $t('refine.extraHint') }}</p>
            <button @click="showInstructionModal = false" class="text-gray-500 hover:text-gray-200 text-lg leading-none">✕</button>
          </div>
          <textarea v-model="refineInstruction" rows="8" autofocus
            :placeholder="$t('refine.extraPlaceholder')"
            class="w-full bg-gray-800 border border-gray-700 rounded-xl px-3 py-2.5 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-purple-500 resize-none leading-relaxed">
          </textarea>
          <div class="flex justify-end gap-2">
            <button @click="refineInstruction = ''; showInstructionModal = false"
              class="px-4 py-2 text-xs text-gray-400 hover:text-gray-200 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors">{{ $t('refine.instructionClear') }}</button>
            <button @click="showInstructionModal = false"
              class="px-5 py-2 text-xs font-semibold text-white bg-purple-600 hover:bg-purple-500 rounded-lg transition-colors">OK</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Admin Modal ── -->
    <AdminModal
      v-model:show="showAdmin"
      :pipeline-state="pipelineState"
      :scan-state="scanState"
      :jobs="allJobs"
      :selected-count="selectedIds.size"
      :selected-ids="selectedIds"
      @toast="showToast($event.msg, $event.type)"
      @trigger-pipeline="triggerPipeline($event)"
      @cancel-pipeline="cancelPipeline"
      @cancel-job="cancelJob($event)"
      @trigger-refresh-metadata="triggerRefreshMetadata"
      @trigger-full-scan="triggerFullScan"
      @reload-workflows="list => { workflows.value = list }"
    />

    <!-- ── Image Detail Modal ── -->
    <Teleport to="body">
      <!-- ── Lightbox ── -->
      <Teleport to="body">
        <div v-if="showLightbox && selected"
          ref="lbContainerRef"
          class="fixed inset-0 z-[70] bg-black flex items-center justify-center overflow-hidden select-none"
          :style="{ cursor: lbDragging ? 'grabbing' : 'grab' }"
          @wheel.prevent="lbOnWheel"
          @mousedown="lbOnMousedown"
          @mousemove="lbOnMousemove"
          @mouseup="lbOnMouseup"
          @mouseleave="lbOnMouseup"
          @dblclick="lbReset"
          @keydown="lbOnKey"
          tabindex="0">

          <!-- Image -->
          <img ref="lbImageRef" :src="`/api/originals/${selected.sha256}`"
            class="max-w-none pointer-events-none"
            draggable="false"
            @load="e => { _markImgReady(); lbFitZoom(e.target) }"
            @error="_markImgReady"
            :style="`transform: translate(${lbPanX}px, ${lbPanY}px) scale(${lbZoom}); transform-origin: center; transition: ${lbDragging ? 'none' : 'transform 0.08s ease-out'}`" />

          <!-- Prev/Next in lightbox -->
          <button v-if="selectedIndex > 0" @click.stop="prevImage"
            class="absolute left-4 top-1/2 -translate-y-1/2 w-12 h-12 flex items-center justify-center bg-black/60 hover:bg-black/80 text-white rounded-full text-2xl transition-colors pointer-events-auto"
            @mousedown.stop @dblclick.stop>‹</button>
          <button v-if="selectedIndex >= 0 && (selectedIndex < images.length - 1 || hasMore)" @click.stop="nextImage"
            class="absolute right-4 top-1/2 -translate-y-1/2 w-12 h-12 flex items-center justify-center bg-black/60 hover:bg-black/80 text-white rounded-full text-2xl transition-colors pointer-events-auto"
            @mousedown.stop @dblclick.stop>{{ loading && selectedIndex === images.length - 1 ? '…' : '›' }}</button>

          <!-- Controls top-right -->
          <div class="absolute top-4 right-4 flex items-center gap-2 pointer-events-auto" @mousedown.stop @dblclick.stop>
            <div class="flex items-center gap-0.5 bg-black/70 backdrop-blur-sm rounded-xl px-1 py-1">
              <button @click="lbZoom = Math.max(0.1, lbZoom / 1.2)"
                class="w-8 h-8 flex items-center justify-center text-white hover:bg-white/20 rounded-lg transition-colors text-xl leading-none">−</button>
              <span class="text-white text-xs font-mono w-14 text-center tabular-nums">{{ Math.round(lbZoom * 100) }}%</span>
              <button @click="lbZoom = Math.min(10, lbZoom * 1.2)"
                class="w-8 h-8 flex items-center justify-center text-white hover:bg-white/20 rounded-lg transition-colors text-xl leading-none">+</button>
              <div class="w-px h-5 bg-white/20 mx-1"></div>
              <button @click="lbReset"
                class="px-2.5 h-8 flex items-center text-xs text-gray-300 hover:bg-white/20 rounded-lg transition-colors">{{ $t('detail.lbReset') }}</button>
            </div>
            <button @click="closeLightbox"
              class="w-9 h-9 flex items-center justify-center bg-black/70 backdrop-blur-sm text-white hover:bg-white/20 rounded-xl transition-colors text-lg">✕</button>
          </div>

          <!-- Filename bottom-center -->
          <div class="absolute bottom-4 left-1/2 -translate-x-1/2 bg-black/70 backdrop-blur-sm px-3 py-1.5 rounded-xl pointer-events-none">
            <p class="text-white text-xs truncate max-w-xs text-center">{{ selected.name }}</p>
          </div>

          <!-- Hint bottom-right -->
          <div class="absolute bottom-4 right-4 text-gray-500 text-xs text-right leading-5 pointer-events-none">
            <p>{{ $t('detail.graphWheelZoom') }}</p>
            <p>{{ $t('detail.graphDragPan') }}</p>
            <p>{{ $t('detail.graphDblReset') }}</p>
            <p>{{ $t('detail.graphEscClose') }}</p>
          </div>
        </div>
      </Teleport>

      <!-- AI Reset confirmation dialog -->
      <div v-if="showAiResetConfirm" class="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4" @click.self="showAiResetConfirm = false">
        <div class="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-sm w-full shadow-2xl">
          <p class="text-sm text-gray-200 mb-5">{{ $t('detail.aiResetConfirmMsg') }}</p>
          <div class="flex gap-3 justify-end">
            <button @click="showAiResetConfirm = false"
              class="px-4 py-1.5 text-sm text-gray-400 hover:text-gray-200 transition-colors">{{ $t('detail.aiResetConfirmCancel') }}</button>
            <button @click="() => { showAiResetConfirm = false; resetAI([selected.sha256]) }"
              class="px-4 py-1.5 text-sm bg-red-700 hover:bg-red-600 text-white rounded-lg transition-colors">{{ $t('detail.aiResetConfirmOk') }}</button>
          </div>
        </div>
      </div>

      <div v-if="selected" class="fixed inset-0 z-[70] bg-black/85 flex items-center justify-center p-4"
        @click.self="selected = null"
        @keydown.left.prevent="prevImage" @keydown.right.prevent="nextImage" @keydown.escape="selected = null"
        tabindex="-1">

        <!-- Prev button -->
        <button v-if="selectedIndex > 0" @click.stop="prevImage"
          class="absolute left-4 top-1/2 -translate-y-1/2 z-10 w-12 h-12 flex items-center justify-center bg-black/60 hover:bg-black/80 text-white rounded-full text-2xl transition-colors">‹</button>
        <!-- Next button -->
        <button v-if="selectedIndex >= 0 && (selectedIndex < images.length - 1 || hasMore)" @click.stop="nextImage"
          class="absolute right-4 top-1/2 -translate-y-1/2 z-10 w-12 h-12 flex items-center justify-center bg-black/60 hover:bg-black/80 text-white rounded-full text-2xl transition-colors">{{ loading && selectedIndex === images.length - 1 ? '…' : '›' }}</button>

        <div class="bg-gray-900 rounded-xl max-w-5xl w-full max-h-[92vh] flex flex-col shadow-2xl">
          <div class="flex gap-4 p-4 flex-1 min-h-0">
            <div class="flex-shrink-0">
              <img :src="`/api/originals/${selected.sha256}`"
                class="max-w-xs max-h-[80vh] object-contain rounded-lg cursor-zoom-in hover:opacity-90 transition-opacity"
                @click="openLightbox"
                @load="() => { if (!showLightbox) _markImgReady() }"
                @error="() => { if (!showLightbox) _markImgReady() }"
                :title="$t('detail.fullscreen')" />
              <!-- Color palette swatches -->
              <div v-if="selected.palette_hex?.length" class="mt-2 flex gap-1.5 justify-center">
                <button
                  v-for="hex in selected.palette_hex"
                  :key="hex"
                  @click="searchByPaletteColor(hex)"
                  :title="`${$t('detail.searchByColor')}: ${hex}`"
                  :style="`background: ${hex}`"
                  class="w-7 h-7 rounded-full border-2 border-gray-700 hover:border-white hover:scale-110 transition-all shadow-md cursor-pointer focus:outline-none focus:ring-2 focus:ring-white/50" />
              </div>
            </div>
            <div class="flex-1 min-w-0 flex flex-col min-h-0 gap-3">
              <div class="flex items-start justify-between gap-2">
                <h2 class="font-medium text-gray-200 truncate text-sm">{{ selected.name }}</h2>
                <button @click="selected = null" class="text-gray-500 hover:text-gray-300 text-lg leading-none flex-shrink-0">✕</button>
              </div>

              <!-- Star rating -->
              <div class="flex items-center gap-0.5">
                <button v-for="n in 5" :key="n" @click="setImageRating(selected, n)"
                  class="text-2xl leading-none transition-colors"
                  :class="selected.star_rating >= n ? 'text-yellow-400' : 'text-gray-700 hover:text-yellow-400/60'">★</button>
                <span v-if="selected.star_rating" class="text-xs text-gray-500 ml-2">★{{ selected.star_rating }}</span>
              </div>

              <!-- Category badges -->
              <div class="flex flex-wrap gap-1">
                <span v-if="selected.batch_category === 'AI'"
                  class="px-1.5 py-0.5 bg-teal-600/80 rounded text-xs text-white leading-none">AI</span>
                <span v-if="selected.batch_category === 'NR'"
                  class="px-1.5 py-0.5 bg-gray-500/80 rounded text-xs text-white leading-none">NR</span>
                <span v-if="selected.embedding_status === 'done'"
                  class="px-1.5 py-0.5 bg-purple-600/80 rounded text-xs text-white leading-none">WD14</span>
              </div>

              <!-- Similar search buttons -->
              <div class="flex flex-wrap gap-2">
                <a :href="`/api/download/${selected.sha256}`"
                  download
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800/60 hover:bg-gray-700/70 border border-gray-700/50 text-gray-300 hover:text-gray-100 rounded-lg text-xs transition-colors">
                  ⬇ {{ $t('detail.download') }}
                </a>
                <button
                  @click="openRefineFromDetail(selected)"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-amber-900/50 hover:bg-amber-800/70 border border-amber-700/50 text-amber-300 hover:text-amber-100 rounded-lg text-xs transition-colors">
                  ✨ {{ $t('detail.refineFromThis') }}
                </button>
                <button
                  v-if="selected.embedding_status === 'done'"
                  @click="findSimilar(selected)"
                  :disabled="similarLoading || colorSimilarLoading"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-900/50 hover:bg-indigo-800/70 border border-indigo-700/50 text-indigo-300 hover:text-indigo-100 rounded-lg text-xs transition-colors disabled:opacity-50">
                  <svg v-if="similarLoading" class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  <span v-else>🔍</span>
                  {{ $t('detail.findSimilar') }}
                </button>
                <button
                  v-if="selected.dominant_hues?.length"
                  @click="findSimilarColor(selected)"
                  :disabled="similarLoading || colorSimilarLoading"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-pink-900/40 hover:bg-pink-800/60 border border-pink-700/40 text-pink-300 hover:text-pink-100 rounded-lg text-xs transition-colors disabled:opacity-50"
                  :title="$t('detail.findSimilarColorTitle')">
                  <svg v-if="colorSimilarLoading" class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  <span v-else>🎨</span>
                  {{ $t('detail.findSimilarColor') }}
                </button>
                <button
                  v-if="selected.embedding_status === 'done'"
                  @click="openSimilarityGraph(selected)"
                  :disabled="graphLoading"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-violet-900/40 hover:bg-violet-800/60 border border-violet-700/40 text-violet-300 hover:text-violet-100 rounded-lg text-xs transition-colors disabled:opacity-50">
                  <svg v-if="graphLoading" class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  <span v-else>◎</span>
                  {{ $t('detail.showGraph') }}
                </button>
                <p v-if="selected.embedding_status !== 'done' && !selected.dominant_hues?.length"
                  class="text-xs text-gray-700">{{ $t('detail.aiPendingHint') }}</p>
              </div>

              <!-- Scrollable body -->
              <div class="flex-1 overflow-y-auto min-h-0 space-y-3 pr-1">

              <div v-if="selected.positive_prompt">
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-1.5">
                    <p class="text-xs font-semibold text-purple-400 uppercase tracking-wide">Prompt</p>
                    <span v-if="selected.extraction?.method"
                      :class="{
                        'bg-green-900/60 text-green-300 border-green-700/50':  selected.extraction.method === 'a1111',
                        'bg-blue-900/60  text-blue-300  border-blue-700/50':   selected.extraction.method === 'ksampler_trace',
                        'bg-yellow-900/60 text-yellow-300 border-yellow-700/50': selected.extraction.method === 'direct_search',
                        'bg-orange-900/60 text-orange-300 border-orange-700/50': selected.extraction.method === 'longest_text',
                        'bg-red-900/60   text-red-300    border-red-700/50':   selected.extraction.method === 'failed',
                        'bg-gray-800     text-gray-400   border-gray-600/50':  !['a1111','ksampler_trace','direct_search','longest_text','failed'].includes(selected.extraction.method),
                      }"
                      class="px-1.5 py-0.5 rounded text-[10px] font-mono border leading-none"
                      :title="selected.extraction.warnings?.join('\n') || ''">
                      {{ selected.extraction.method }}
                    </span>
                    <span v-if="selected.extraction?.confidence === 'low'"
                      class="px-1.5 py-0.5 rounded text-[10px] border bg-yellow-900/40 text-yellow-400 border-yellow-700/40 leading-none"
                      :title="$t('detail.alignmentLowConf')">⚠</span>
                  </div>
                  <button @click="copyToClipboard(selected.positive_prompt)" class="text-xs text-gray-500 hover:text-gray-300">{{ $t('detail.copy') }}</button>
                </div>
                <p class="text-xs text-gray-300 whitespace-pre-wrap break-words bg-gray-800 rounded-lg p-2.5 leading-relaxed max-h-40 overflow-y-auto">{{ selected.positive_prompt }}</p>
              </div>

              <div v-if="selected.negative_prompt">
                <div class="flex items-center justify-between mb-1">
                  <p class="text-xs font-semibold text-red-400 uppercase tracking-wide">{{ $t('refine.negativeLabel') }}</p>
                  <button @click="copyToClipboard(selected.negative_prompt)" class="text-xs text-gray-500 hover:text-gray-300">{{ $t('detail.copy') }}</button>
                </div>
                <p class="text-xs text-gray-300 whitespace-pre-wrap break-words bg-gray-800 rounded-lg p-2.5 leading-relaxed max-h-32 overflow-y-auto">{{ selected.negative_prompt }}</p>
              </div>

              <!-- WD14 auto-tags -->
              <div v-if="selected.wd14_tags?.length">
                <div class="flex items-center justify-between mb-1">
                  <p class="text-xs font-semibold text-teal-400 uppercase tracking-wide">WD14 Auto-tags</p>
                  <div class="flex items-center gap-2">
                    <button @click="copyWd14Tags"
                      class="text-xs text-gray-500 hover:text-teal-400 transition-colors">{{ wd14Copied ? '✓ Copied' : 'Copy' }}</button>
                    <button @click="showAiResetConfirm = true" class="text-xs text-gray-500 hover:text-red-400 transition-colors">{{ $t('detail.aiReset') }}</button>
                  </div>
                </div>
                <div class="flex flex-wrap gap-1">
                  <span v-for="tag in selected.wd14_tags" :key="tag"
                    class="px-1.5 py-0.5 bg-teal-900/50 text-teal-300 rounded text-xs cursor-pointer hover:bg-teal-700/50"
                    @click="() => { selected=null; tagsFilter[tag]='include'; fetchImages(true) }">{{ tag }}</span>
                </div>
              </div>
              <div v-else-if="selected.embedding_status === 'done'" class="text-xs text-gray-600">
                {{ $t('detail.aiDoneNoTags') }}
              </div>

              <!-- Prompt Alignment -->
              <div v-if="selected.positive_prompt && selected.wd14_tags?.length">
                <div class="flex items-center justify-between mb-1">
                  <p class="text-xs font-semibold text-orange-400 uppercase tracking-wide">Alignment</p>
                  <button
                    @click="triggerAlignmentEvaluate(selected.sha256)"
                    :disabled="alignmentEvaluating.has(selected.sha256)"
                    :class="alignmentEvaluating.has(selected.sha256)
                      ? 'text-xs text-orange-400 animate-pulse cursor-not-allowed'
                      : 'text-xs text-gray-500 hover:text-orange-400 transition-colors'">
                    {{ alignmentEvaluating.has(selected.sha256)
                      ? $t('detail.alignmentEvaluating')
                      : alignmentCache.get(selected.sha256) ? $t('detail.alignmentReevaluate') : $t('detail.alignmentEvaluate') }}
                  </button>
                </div>
                <div v-if="alignmentCache.get(selected.sha256) && alignmentCache.get(selected.sha256).status !== 'skipped'" class="space-y-1.5">
                  <div class="flex items-center gap-2">
                    <span class="text-2xl font-bold"
                      :class="alignmentCache.get(selected.sha256).score >= 0.7 ? 'text-green-400'
                             : alignmentCache.get(selected.sha256).score >= 0.4 ? 'text-yellow-400'
                             : 'text-red-400'">
                      {{ Math.round(alignmentCache.get(selected.sha256).score * 100) }}%
                    </span>
                    <span class="text-xs text-gray-500">embedding similarity</span>
                  </div>
                  <p v-if="alignmentCache.get(selected.sha256).summary_i18n?.[locale] || alignmentCache.get(selected.sha256).summary"
                    class="text-xs text-gray-300 bg-gray-800 rounded-lg p-2 leading-relaxed">
                    {{ alignmentCache.get(selected.sha256).summary_i18n?.[locale] || alignmentCache.get(selected.sha256).summary }}
                    <span v-if="!alignmentCache.get(selected.sha256).summary_i18n?.[locale] && locale !== 'ja'"
                      class="ml-1 text-yellow-600/70 font-mono text-[10px]">(JA)</span>
                  </p>
                  <div v-if="(alignmentCache.get(selected.sha256).matched_elements_i18n?.[locale] || alignmentCache.get(selected.sha256).matched_elements)?.length" class="flex flex-wrap gap-1">
                    <span class="text-xs text-gray-500 self-center">
                      {{ $t('detail.alignmentMatched') }}
                      <span v-if="!alignmentCache.get(selected.sha256).matched_elements_i18n?.[locale] && locale !== 'ja'"
                        class="text-yellow-600/70 font-mono text-[10px]">(JA)</span>
                    </span>
                    <span v-for="el in (alignmentCache.get(selected.sha256).matched_elements_i18n?.[locale] || alignmentCache.get(selected.sha256).matched_elements)" :key="el"
                      class="px-1.5 py-0.5 bg-green-900/40 text-green-300 rounded text-xs">{{ el }}</span>
                  </div>
                  <div v-if="(alignmentCache.get(selected.sha256).unmatched_elements_i18n?.[locale] || alignmentCache.get(selected.sha256).unmatched_elements)?.length" class="flex flex-wrap gap-1">
                    <span class="text-xs text-gray-500 self-center">
                      {{ $t('detail.alignmentMissed') }}
                      <span v-if="!alignmentCache.get(selected.sha256).unmatched_elements_i18n?.[locale] && locale !== 'ja'"
                        class="text-yellow-600/70 font-mono text-[10px]">(JA)</span>
                    </span>
                    <span v-for="el in (alignmentCache.get(selected.sha256).unmatched_elements_i18n?.[locale] || alignmentCache.get(selected.sha256).unmatched_elements)" :key="el"
                      class="px-1.5 py-0.5 bg-red-900/40 text-red-300 rounded text-xs">{{ el }}</span>
                  </div>
                  <p class="text-xs text-gray-600">
                    {{ $t('detail.alignmentEvaluatedAt') }}: {{ new Date(alignmentCache.get(selected.sha256).evaluated_at).toLocaleString() }}
                  </p>
                </div>
                <p v-else-if="!alignmentCache.get(selected.sha256)" class="text-xs text-gray-600">{{ $t('detail.alignmentNone') }}</p>
              </div>

              <!-- Creation Record -->
              <details v-if="selected.creation_record" class="group">
                <summary class="px-3 py-2 bg-gray-800/40 text-xs font-semibold text-indigo-400/80 uppercase tracking-wide cursor-pointer list-none flex items-center justify-between hover:bg-gray-800/70 transition-colors rounded-lg">
                  <span>🎨 {{ $t('detail.creationRecord') }}</span>
                  <span class="text-gray-500 font-normal normal-case">
                    {{ $t('detail.creationMethod_' + selected.creation_record.method) }}
                    &middot;
                    {{ new Date(selected.creation_record.recorded_at).toLocaleDateString() }}
                  </span>
                </summary>
                <div class="mt-2 space-y-2">
                  <!-- Source image thumbnails + weight badges -->
                  <div v-if="selected.creation_record.source_images?.length" class="flex flex-wrap gap-1.5">
                    <div v-for="src in selected.creation_record.source_images" :key="src.sha256"
                         class="relative cursor-pointer rounded overflow-hidden hover:ring-1 hover:ring-indigo-400 transition-all"
                         :title="src.sha256.slice(0,8) + ' (' + Math.round(src.weight * 100) + '%)'"
                         @click="navigateToSource(src.sha256)">
                      <img :src="`/api/thumbnails/${src.sha256}.webp`" class="w-14 h-14 object-cover" loading="lazy" />
                      <span class="absolute bottom-0 right-0 bg-black/70 text-white text-[9px] px-1 rounded-tl leading-4">
                        {{ Math.round(src.weight * 100) }}%
                      </span>
                    </div>
                  </div>
                  <!-- Creative intent -->
                  <p v-if="selected.creation_record.instruction"
                     class="text-xs text-gray-300 italic leading-relaxed">
                    "{{ selected.creation_record.instruction }}"
                  </p>
                  <p v-if="selected.creation_record.inspire_context?.mode"
                     class="text-xs text-indigo-300/70">
                    ✦ {{ $t('detail.creationInspireMode') }}: {{ selected.creation_record.inspire_context.mode }}
                  </p>
                  <!-- Technical settings -->
                  <dl class="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-gray-500">
                    <template v-if="selected.creation_record.workflow_name">
                      <dt>{{ $t('detail.creationWorkflow') }}</dt>
                      <dd class="text-gray-400">{{ selected.creation_record.workflow_name }}</dd>
                    </template>
                    <template v-if="selected.creation_record.prompt_style">
                      <dt>{{ $t('detail.creationStyle') }}</dt>
                      <dd class="text-gray-400">{{ selected.creation_record.prompt_style }}</dd>
                    </template>
                    <template v-if="selected.creation_record.temperature != null">
                      <dt>{{ $t('detail.creationTemperature') }}</dt>
                      <dd class="text-gray-400">{{ selected.creation_record.temperature }}</dd>
                    </template>
                  </dl>
                </div>
              </details>

              <div v-if="selected.params && Object.keys(selected.params).length">
                <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Parameters</p>
                <dl class="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs bg-gray-800 rounded-lg p-2.5">
                  <template v-for="(v, k) in selected.params" :key="k">
                    <dt class="text-gray-500 truncate">{{ k }}</dt>
                    <dd class="text-gray-300">{{ v }}</dd>
                  </template>
                </dl>
              </div>

              <div class="pt-2 border-t border-gray-800 text-xs text-gray-600 space-y-0.5">
                <p>{{ selected.size ? (selected.size/1024).toFixed(0)+' KB' : '' }}</p>
                <p class="font-mono break-all">{{ selected.sha256 }}</p>
                <button
                  @click="openRawMetadata(selected.sha256)"
                  class="mt-1 text-xs text-gray-500 hover:text-gray-300 underline underline-offset-2 transition-colors">
                  Raw Metadata
                </button>
              </div>

              </div><!-- /scrollable body -->
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Raw Metadata Modal ── -->
    <Teleport to="body">
      <div v-if="rawMetadataModal.open"
        class="fixed inset-0 z-[70] bg-black/80 flex items-center justify-center p-4"
        @click.self="rawMetadataModal.open = false">
        <div class="bg-gray-900 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl border border-gray-700">
          <!-- header -->
          <div class="flex items-center justify-between px-5 py-3 border-b border-gray-700 shrink-0">
            <div>
              <p class="text-sm font-semibold text-gray-100">Raw Metadata</p>
              <p class="text-xs text-gray-500 font-mono">{{ rawMetadataModal.sha256?.slice(0, 16) }}…</p>
            </div>
            <button @click="rawMetadataModal.open = false" class="text-gray-500 hover:text-gray-200 text-xl leading-none">✕</button>
          </div>

          <!-- loading -->
          <div v-if="rawMetadataModal.loading" class="flex-1 flex items-center justify-center text-gray-500 text-sm">
            {{ $t('detail.rawMetaLoading') }}
          </div>

          <!-- error -->
          <div v-else-if="rawMetadataModal.error" class="flex-1 flex items-center justify-center text-red-400 text-sm px-5">
            {{ rawMetadataModal.error }}
          </div>

          <!-- content -->
          <div v-else class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
            <div v-if="!rawMetadataModal.sections?.length" class="text-gray-500 text-sm">
              {{ $t('detail.rawMetaEmpty') }}
            </div>
            <div v-for="sec in rawMetadataModal.sections" :key="sec.key">
              <div class="flex items-center justify-between mb-1.5">
                <p class="text-xs font-semibold uppercase tracking-wide"
                  :class="sec.key === 'parameters' ? 'text-purple-400' : sec.key === 'prompt' ? 'text-blue-400' : 'text-gray-400'">
                  {{ sec.label }}
                </p>
                <button @click="copyToClipboard(sec.type === 'json' ? JSON.stringify(sec.content, null, 2) : sec.content)"
                  class="text-xs text-gray-600 hover:text-gray-300 transition-colors">{{ $t('detail.rawMetaCopy') }}</button>
              </div>
              <!-- plain text -->
              <pre v-if="sec.type === 'text'"
                class="text-xs text-gray-300 bg-gray-800 rounded-lg p-3 whitespace-pre-wrap break-words leading-relaxed max-h-60 overflow-y-auto font-mono">{{ sec.content }}</pre>
              <!-- JSON: show collapsed tree -->
              <div v-else class="bg-gray-800 rounded-lg overflow-hidden">
                <div class="flex gap-2 px-3 py-1.5 border-b border-gray-700">
                  <button @click="rawMetadataModal.expanded[sec.key] = !rawMetadataModal.expanded[sec.key]"
                    class="text-xs text-gray-400 hover:text-gray-200 transition-colors">
                    {{ rawMetadataModal.expanded[sec.key] ? $t('detail.rawMetaCollapse') : $t('detail.rawMetaExpand') }}
                  </button>
                  <button @click="copyToClipboard(JSON.stringify(sec.content, null, 2))"
                    class="text-xs text-gray-600 hover:text-gray-300 transition-colors ml-auto">{{ $t('detail.rawMetaJsonCopy') }}</button>
                </div>
                <pre v-if="rawMetadataModal.expanded[sec.key]"
                  class="text-xs text-gray-300 p-3 whitespace-pre overflow-auto max-h-96 font-mono leading-relaxed">{{ JSON.stringify(sec.content, null, 2) }}</pre>
                <p v-else class="text-xs text-gray-600 px-3 py-2">
                  {{ Array.isArray(sec.content) ? sec.content.length + ' items' : Object.keys(sec.content).length + ' keys' }}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Similarity Graph Overlay ── -->
    <Teleport to="body">
      <div v-if="showSimilarityGraph"
        class="fixed inset-0 z-[60] bg-black/90 flex items-center justify-center"
        @click.self="closeSimilarityGraph">
        <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl flex flex-col"
          style="width: 92vw; height: 92vh;">
          <div class="flex items-center gap-4 px-4 py-2.5 border-b border-gray-800 flex-shrink-0">
            <h3 class="text-sm font-semibold text-gray-200 shrink-0">{{ $t('detail.graphTitle') }}</h3>

            <!-- Depth selector -->
            <div class="flex items-center gap-1.5">
              <span class="text-xs text-gray-500 shrink-0">{{ $t('detail.graphDepth') }}</span>
              <div class="flex gap-1">
                <button v-for="d in [1, 2, 3, 4, 5]" :key="d"
                  @click="graphDepth = d"
                  :disabled="graphLoading"
                  :class="graphDepth === d
                    ? 'bg-violet-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200'"
                  class="w-6 h-6 rounded text-xs font-bold transition-colors disabled:opacity-40">{{ d }}</button>
              </div>
            </div>

            <!-- Neighbors selector -->
            <div class="flex items-center gap-1.5">
              <span class="text-xs text-gray-500 shrink-0">{{ $t('detail.graphNeighbors') }}</span>
              <div class="flex gap-1">
                <button v-for="n in [4, 6, 8, 10]" :key="n"
                  @click="graphNeighbors = n"
                  :disabled="graphLoading"
                  :class="graphNeighbors === n
                    ? 'bg-violet-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200'"
                  class="w-7 h-6 rounded text-xs font-bold transition-colors disabled:opacity-40">{{ n }}</button>
              </div>
            </div>

            <div class="flex items-center gap-3 ml-auto">
              <!-- Right-click hint -->
              <span class="text-xs text-gray-600 hidden sm:block">
                {{ $t('detail.graphRightClickHint') }}
              </span>

              <div v-if="graphLoading" class="flex items-center gap-1.5 text-xs text-violet-400">
                <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                {{ $t('detail.graphLoading') }}
              </div>
              <span v-else-if="graphData" class="text-xs text-gray-500">
                {{ graphData.nodes.length }} nodes · {{ graphData.edges.length }} edges
              </span>
              <button @click="closeSimilarityGraph"
                class="text-gray-500 hover:text-gray-300 w-7 h-7 flex items-center justify-center rounded hover:bg-gray-800 transition-colors text-lg leading-none shrink-0">✕</button>
            </div>
          </div>
          <div class="flex-1 relative overflow-hidden rounded-b-2xl">
            <canvas v-if="graphData"
              ref="graphCanvasRef"
              class="w-full h-full"
              @click="onGraphCanvasClick"
              @mousemove="onGraphCanvasHover" />
            <p v-else-if="!graphLoading" class="absolute inset-0 flex items-center justify-center text-sm text-gray-600">
              {{ $t('detail.graphEmpty') }}
            </p>
            <!-- Loading overlay — shown on top of existing canvas during reload -->
            <div v-if="graphLoading" class="absolute inset-0 flex items-center justify-center bg-gray-900/70 rounded-b-2xl">
              <svg class="w-10 h-10 animate-spin text-violet-400" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            </div>

            <!-- Path clear button — visible only when a path is highlighted -->
            <Transition
              enter-active-class="transition-all duration-200"
              enter-from-class="opacity-0 scale-90"
              enter-to-class="opacity-100 scale-100"
              leave-active-class="transition-all duration-150"
              leave-from-class="opacity-100 scale-100"
              leave-to-class="opacity-0 scale-90">
              <button v-if="hasActivePath"
                @click="clearGraphPath"
                class="absolute bottom-3 right-3 flex items-center gap-1.5 px-3 py-1.5
                       bg-amber-500/20 hover:bg-amber-500/35 border border-amber-500/50
                       text-amber-300 text-xs font-semibold rounded-lg
                       backdrop-blur-sm transition-all shadow-lg select-none">
                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                </svg>
                {{ $t('detail.graphClearPath') }}
              </button>
            </Transition>

            <!-- Right-click context menu -->
            <Teleport to="body">
              <Transition
                enter-active-class="transition-all duration-150"
                enter-from-class="opacity-0 scale-95"
                enter-to-class="opacity-100 scale-100"
                leave-active-class="transition-all duration-100"
                leave-from-class="opacity-100 scale-100"
                leave-to-class="opacity-0 scale-95">
                <div v-if="graphContextMenu"
                  class="fixed z-[200] py-1 rounded-xl shadow-2xl
                         bg-gray-900/95 backdrop-blur-md border border-gray-700/80"
                  :style="{
                    left: graphContextMenu.screenX + 'px',
                    top: graphContextMenu.screenY + 'px',
                    transformOrigin: 'top left'
                  }"
                  @click.stop>
                  <!-- Node name header -->
                  <div class="px-3 py-1.5 border-b border-gray-700/60">
                    <p class="text-[11px] font-semibold text-gray-400 truncate max-w-[200px]">
                      {{ graphContextMenu.node?.name || graphContextMenu.node?.sha256?.slice(0, 8) }}
                    </p>
                  </div>
                  <!-- Actions -->
                  <button @click="graphCtxHighlightPath"
                    class="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-gray-200
                           hover:bg-amber-500/20 hover:text-amber-300 transition-colors text-left">
                    <svg class="w-4 h-4 text-amber-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                      <path stroke-linecap="round" stroke-linejoin="round"
                        d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3"/>
                    </svg>
                    {{ $t('detail.graphCtxPath') }}
                  </button>
                  <button @click="graphCtxNavigate"
                    class="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-gray-200
                           hover:bg-violet-500/20 hover:text-violet-300 transition-colors text-left">
                    <svg class="w-4 h-4 text-violet-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                      <path stroke-linecap="round" stroke-linejoin="round"
                        d="M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909M18 12V3.75M18 3.75h-4.5M18 3.75l-4.5 4.5"/>
                    </svg>
                    {{ $t('detail.graphCtxOpen') }}
                  </button>
                </div>
              </Transition>
            </Teleport>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Bucket thumbnail popup ── -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-150 ease-out"
        enter-from-class="opacity-0 scale-95 translate-y-1"
        enter-to-class="opacity-100 scale-100 translate-y-0"
        leave-active-class="transition duration-100 ease-in"
        leave-from-class="opacity-100 scale-100 translate-y-0"
        leave-to-class="opacity-0 scale-95 translate-y-1">
        <div v-if="bucketHovered && selectedCount > 0"
          class="fixed z-[50] bg-slate-900/95 backdrop-blur-md border border-purple-500/30 rounded-xl shadow-2xl p-2"
          :style="bucketPopupStyle"
          @mouseenter="bucketHovered = true"
          @mouseleave="bucketHovered = false">
          <div class="flex flex-wrap gap-1.5" style="max-width: 320px;">
            <img v-for="sha in [...selectedIds]" :key="sha"
              :src="`/api/thumbnails/${sha}.webp`"
              class="w-14 h-14 rounded-lg object-cover ring-1 ring-purple-500/40 flex-shrink-0" />
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Per-bucket thumbnail hover preview ── -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition duration-100 ease-out"
        enter-from-class="opacity-0 scale-90"
        enter-to-class="opacity-100 scale-100"
        leave-active-class="transition duration-75 ease-in"
        leave-from-class="opacity-100 scale-100"
        leave-to-class="opacity-0 scale-90">
        <div v-if="hoveredThumbnailSha"
          class="fixed z-[60] pointer-events-none rounded-xl overflow-hidden ring-2 ring-purple-400/70 shadow-2xl"
          :style="hoveredThumbnailStyle">
          <img :src="`/api/thumbnails/${hoveredThumbnailSha}.webp`"
            class="w-28 h-28 object-cover block" />
        </div>
      </Transition>
    </Teleport>

    <!-- ── Session bucket tray ── -->
    <Teleport to="body">
      <div
        v-if="inspireHasSession || refineHasSession"
        class="fixed bottom-0 left-4 z-[46] pb-20 flex flex-col items-start gap-2 pointer-events-none">
        <!-- Inspiration session chip -->
        <div v-if="inspireHasSession"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-xl shadow-xl backdrop-blur-sm
                 bg-indigo-950/90 border border-indigo-500/40 text-xs text-indigo-200
                 pointer-events-auto select-none transition-all
                 hover:bg-indigo-900/90 hover:border-indigo-400/60">
          <button @click="showInspire = true" class="flex items-center gap-1.5 flex-1">
            <span>🔮</span>
            <span class="font-medium">{{ $t('header.inspireChip') }}</span>
            <span v-if="inspireIsRunning"
              class="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse ml-0.5 inline-block"></span>
          </button>
          <button v-if="!inspireIsRunning"
            @click.stop="inspireReset()"
            class="w-4 h-4 rounded-full flex items-center justify-center ml-1
                   text-indigo-400 hover:text-white hover:bg-indigo-700 transition-colors text-[10px] leading-none">
            ✕
          </button>
        </div>
        <!-- Prompt refinement session chip -->
        <div v-if="refineHasSession"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-xl shadow-xl backdrop-blur-sm
                 bg-purple-950/90 border border-purple-500/40 text-xs text-purple-200
                 pointer-events-auto select-none transition-all
                 hover:bg-purple-900/90 hover:border-purple-400/60">
          <button @click="showRefine = true" class="flex items-center gap-1.5 flex-1">
            <span>⚗️</span>
            <span class="font-medium">{{ $t('header.refineChip') }}</span>
            <span v-if="refineIsRunning"
              class="w-1.5 h-1.5 rounded-full bg-purple-400 animate-pulse ml-0.5 inline-block"></span>
          </button>
          <button v-if="!refineIsRunning"
            @click.stop="clearRefineSession()"
            class="w-4 h-4 rounded-full flex items-center justify-center ml-1
                   text-purple-400 hover:text-white hover:bg-purple-700 transition-colors text-[10px] leading-none">
            ✕
          </button>
        </div>
      </div>
    </Teleport>

    <!-- ── Bottom Selection Tray ── -->
    <Teleport to="body">
      <div class="fixed bottom-0 left-0 right-0 z-[45] transition-transform duration-500 pointer-events-none"
        :style="`transform: translateY(${selectedCount > 0 ? '0' : '100%'}); transition-timing-function: cubic-bezier(0.4,0,0.2,1);`">
        <div class="mx-3 mb-3 pointer-events-auto">
          <div class="bg-slate-950/90 backdrop-blur-md border border-purple-500/25 rounded-2xl shadow-2xl shadow-black/60"
            style="box-shadow: 0 -4px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(168,85,247,0.15);">
            <div class="flex items-center gap-3 px-4 py-3">

              <!-- Status count -->
              <div ref="bucketBadgeRef" class="flex-shrink-0 flex items-center gap-2 cursor-default"
                @mouseenter="onBucketMouseEnter"
                @mouseleave="bucketHovered = false">
                <div class="w-5 h-5 rounded-full bg-purple-500 flex items-center justify-center">
                  <svg class="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
                    <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                  </svg>
                </div>
                <span class="text-sm font-semibold text-purple-300 whitespace-nowrap tabular-nums">{{ $t('tray.selected', { n: selectedCount }) }}</span>
              </div>

              <!-- Divider -->
              <div class="w-px h-8 bg-gray-700/60 flex-shrink-0"></div>

              <!-- Thumbnail scroll area -->
              <div class="flex-1 overflow-x-auto scrollbar-hide min-w-0">
                <div class="flex items-center gap-2 py-0.5">
                  <div v-for="sha in [...selectedIds]" :key="sha"
                    class="relative flex-shrink-0 group/thumb cursor-default"
                    @mouseenter="onThumbMouseEnter($event, sha)"
                    @mouseleave="hoveredThumbnailSha = null">
                    <img :src="`/api/thumbnails/${sha}.webp`"
                      class="w-10 h-10 rounded-lg object-cover ring-1 ring-purple-500/50 transition-all duration-200 group-hover/thumb:ring-2 group-hover/thumb:ring-purple-400 group-hover/thumb:brightness-90"
                      loading="lazy" />
                    <button
                      @click="removeFromSelection(sha)"
                      class="absolute -top-1.5 -right-1.5 w-4 h-4 bg-gray-700 hover:bg-red-600 rounded-full text-white flex items-center justify-center opacity-0 group-hover/thumb:opacity-100 transition-all duration-150 leading-none text-[10px] font-bold shadow-md">
                      ✕
                    </button>
                  </div>
                </div>
              </div>

              <!-- Divider -->
              <div class="w-px h-8 bg-gray-700/60 flex-shrink-0"></div>

              <!-- Action buttons -->
              <div class="flex items-center gap-2 flex-shrink-0">
                <button @click="openInspire"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-900/60 hover:bg-indigo-800/80 border border-indigo-500/40 hover:border-indigo-400/60 rounded-xl text-xs font-medium text-indigo-200 transition-all duration-150">
                  {{ $t('tray.inspire') }}
                </button>
                <button @click="openRefineFromTray"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-purple-800/60 hover:bg-purple-700/80 border border-purple-500/40 hover:border-purple-400/60 rounded-xl text-xs font-medium text-purple-200 transition-all duration-150">
                  {{ $t('tray.refine') }}
                </button>
                <button @click="clearSelection"
                  class="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800/60 hover:bg-gray-700/80 border border-gray-600/50 rounded-xl text-xs text-gray-400 hover:text-gray-200 transition-all duration-150">
                  {{ $t('tray.clear') }}
                </button>
              </div>

            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ── Analyzer Modal ── -->
    <AnalyzerModal
      v-model:show="showAnalyzer"
      :images="images"
      :jobs-map="jobsMap"
      @select-image="selected = $event"
      @search-tag="searchTag($event)"
      @toast="showToast($event.msg, $event.type)"
    />

    <!-- ── Inspire Panel ── -->
    <InspirePanel
      v-model:show="showInspire"
      v-model:running="inspireRunning"
      :initial-slots="inspireInitialSlots"
      :selected-ids="inspireSelectedIds"
      @select-image="selected = $event"
      @toggle-image-selection="handleToggleImageSelection($event)"
      @send-to-refine="handleSendToRefine($event)"
      @send-to-refine-direct="handleSendToRefineDirect($event)"
      @toast="showToast($event.msg, $event.type)"
    />

    <!-- ── Invoke Panel ── -->
    <InvokePanel
      :show="showInvoke"
      @update:show="showInvoke = $event"
      @send-to-refine="handleInvokeSendToRefine($event)"
      @toast="showToast($event.msg, $event.type)"
      @select-image="openImageFromOracle($event)"
    />

        <!-- ── About modal ── -->
    <Teleport to="body">
      <div v-if="showAbout" class="fixed inset-0 z-[80] bg-black/80 flex items-center justify-center p-4"
        @click.self="showAbout = false" @keydown.esc="showAbout = false">
        <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-sm flex flex-col items-center gap-5 p-8 relative">
          <button @click="showAbout = false"
            class="absolute top-3 right-3 text-gray-600 hover:text-gray-200 text-xl w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>

          <img src="/logo.png" alt="Ranbell Image" class="w-28 h-28 rounded-2xl shadow-lg" />

          <div class="text-center">
            <h2 class="text-xl font-bold text-purple-300 tracking-tight">Ranbell Image</h2>
            <p class="text-xs text-gray-500 mt-0.5">v0.1.0</p>
          </div>

          <p class="text-sm text-gray-400 text-center leading-relaxed whitespace-pre-line">{{ $t('about.tagline') }}</p>

          <div class="w-full border-t border-gray-800 pt-4 flex flex-col gap-1.5">
            <p class="text-xs text-gray-500 font-medium mb-0.5">{{ $t('about.stack') }}</p>
            <div class="flex justify-between text-xs">
              <span class="text-gray-500">Vector DB</span>
              <span class="text-gray-300">Qdrant</span>
            </div>
            <div class="flex justify-between text-xs">
              <span class="text-gray-500">Embedding</span>
              <span class="text-gray-300">Ollama</span>
            </div>
            <div class="flex justify-between text-xs">
              <span class="text-gray-500">Tagging</span>
              <span class="text-gray-300">WD14 Tagger</span>
            </div>
            <div class="flex justify-between text-xs">
              <span class="text-gray-500">Image Gen</span>
              <span class="text-gray-300">ComfyUI</span>
            </div>
            <div class="flex justify-between text-xs">
              <span class="text-gray-500">Frontend</span>
              <span class="text-gray-300">Vue 3 + Tailwind</span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>


    <!-- ── Control Room ── -->
    <Teleport to="body">
      <Transition name="cr-slide">
        <ControlRoom
          v-if="controlRoomVisible"
          :jobsMap="jobsMap"
          :controlRoom="cr"
          :sseConnected="jobStreamConnected"
          :disks="disksRef"
          :diskCautionPct="diskCautionPct"
          :diskFaultPct="diskFaultPct"
          @close="controlRoomVisible = false"
          @retry="retryJob"
          @cancel="cancelJob"
          @retry-all-failed="retryAllFailed"
          @cancel-all-queued="cancelAllQueued"
          @reopen-refine="openRefineFromHistory"
        />
      </Transition>
    </Teleport>

    <!-- ── Toast ── -->

    <Teleport to="body">
      <Transition enter-from-class="opacity-0 translate-y-2" leave-to-class="opacity-0 translate-y-2">
        <div v-if="toast" class="fixed bottom-6 left-1/2 -translate-x-1/2 z-[200] pointer-events-none">
          <div class="flex items-center gap-2.5 px-4 py-2.5 rounded-xl shadow-2xl text-sm font-medium transition-all duration-300"
            :class="{
              'bg-gray-800 border border-gray-700 text-gray-200': toast.type === 'info',
              'bg-green-900/90 border border-green-700/60 text-green-200': toast.type === 'success',
              'bg-red-900/90 border border-red-700/60 text-red-200': toast.type === 'error',
            }">
            <span v-if="toast.type === 'info'" class="text-base">ℹ️</span>
            <span v-else-if="toast.type === 'success'" class="text-base">✓</span>
            <span v-else class="text-base">✗</span>
            {{ toast.msg }}
          </div>
        </div>
      </Transition>
    </Teleport>


  <!-- ── API Token Prompt ── -->
  <Teleport to="body">
    <div v-if="showTokenPrompt"
      class="fixed inset-0 z-[200] bg-black/80 flex items-center justify-center p-4">
      <div class="bg-gray-900 rounded-xl w-full max-w-sm shadow-2xl border border-gray-700 p-6 space-y-4">
        <h2 class="text-base font-semibold text-gray-100">API トークンの入力</h2>
        <p class="text-xs text-gray-400">
          サーバーへのアクセスには API トークンが必要です。<br>
          環境変数 <code class="text-purple-300">API_TOKEN</code> で設定したトークンを入力してください。
        </p>
        <input
          v-model="tokenInput"
          type="password"
          placeholder="RANBELL_IMAGE_API_TOKEN"
          class="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm font-mono text-gray-200 focus:outline-none focus:border-purple-500"
          @keydown.enter="saveToken"
          autofocus
        />
        <div class="flex gap-2 justify-end">
          <button
            @click="saveToken"
            class="px-4 py-2 bg-purple-600 hover:bg-purple-500 rounded-lg text-sm font-medium transition-colors">
            保存して再読み込み
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  </div>
</template>

<style>
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }

/* ── Control Room Status Bar ─────────────────────────────────────────────── */
.cr-statusline {
  height: 3px;
  width: 100%;
  flex-shrink: 0;
  cursor: pointer;
  transition: height 0.15s ease, opacity 0.3s ease;
  opacity: 0.7;
}
.cr-statusline:hover { height: 5px; opacity: 1; }
.cr-statusline--nominal { background: #3d6b50; }
.cr-statusline--caution { background: #b8860b; box-shadow: 0 0 6px rgba(184,134,11,0.6); }
.cr-statusline--fault   { background: #cc3333; box-shadow: 0 0 8px rgba(204,51,51,0.8); }

/* ── Control Room Slide Transition ───────────────────────────────────────── */
.cr-slide-enter-active { transition: transform 200ms ease-out; }
.cr-slide-leave-active { transition: transform 150ms ease-in; }
.cr-slide-enter-from,
.cr-slide-leave-to { transform: translateY(-100%); }
</style>

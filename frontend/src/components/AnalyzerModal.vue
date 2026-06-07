<script setup>
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  show: Boolean,
  images: Array,
  jobsMap: Object,  // Map<job_id, job>
})

const emit = defineEmits(['update:show', 'select-image', 'search-tag', 'toast'])

// ── Analyzer state ────────────────────────────────────────────────────────────
const analyzerTab = ref('semantic')

const umapStatus = ref(null)
const umapPoints = ref([])
const umapCanvasRef = ref(null)
const umapLoading = ref(false)
let _umapSim = null
let _umapImgCache = {}
let _umapHoveredIdx = null
let _umapTransform = { scale: 1, tx: 0, ty: 0 }
let _umapPanStart = null
let _umapPanOrigin = null
let _umapDidDrag = false
let _umapSelectStart = null
let _umapSelectRect = null
const umapShiftDown = ref(false)
let _umapShiftDown = false

const umapSelection = ref([])
const umapTooltip = ref({ visible: false, x: 0, y: 0, sha256: '', name: '' })
const umapShowClusters = ref(false)
const umapClusterData = ref(null)
const umapClusterLoading = ref(false)
const umapDetailImage = ref(null)

const color3dPoints = ref([])
const color3dLoading = ref(false)
const color3dContainerRef = ref(null)
const color3dTooltip = ref({ visible: false, x: 0, y: 0, sha256: '', name: '' })
const color3dDetailImage = ref(null)
const color3dBackfillNeeded = ref(false)
const color3dPendingCount = ref(0)
let _plotlyLoaded = false
let _plotlyContainer = null
let _Plotly = null
let _color3dMouseMoveHandler = null
let _color3dMouseX = 0
let _color3dMouseY = 0

const tagNetData = ref(null)
const tagNetCanvasRef = ref(null)
const tagTaxonomy = ref({})
const tagNetLoading = ref(false)
const tagTaxonomyLoading = ref(false)
const tagTaxonomyJobId = ref(null)
const tagMinCount = ref(2)
const tagTopTags = ref(80)
const tagNetSearch = ref('')
const tagNetSelected = ref(null)
const tagNetTooltip = ref({ visible: false, x: 0, y: 0, node: null })
const tagNetSimFrozen = ref(false)
let _tagSim = null
let _tagNetTransform = { scale: 1, tx: 0, ty: 0 }
let _tagNetHoveredId = null
let _tagNetSelectedId = null
let _tagNetPanStart = null
let _tagNetPanOrigin = null
let _tagNetDidDrag = false
let _tagNetSimNodes = []
let _tagNetSimLinks = []
let _tagNetDraw = null
let _tagNetDebounceTimer = null

const analyzerHealthData = ref(null)
const analyzerHealthLoading = ref(false)

// ── Analyzer functions ────────────────────────────────────────────────────────

function open() {
  if (analyzerTab.value === 'semantic') fetchUmapStatus()
  else if (analyzerTab.value === 'color3d') fetchColor3D()
  else if (analyzerTab.value === 'tags') fetchTagNetwork()
  else if (analyzerTab.value === 'health') fetchAnalyzerHealth()
}

function switchAnalyzerTab(tab) {
  analyzerTab.value = tab
  if (tab === 'semantic' && !umapStatus.value) fetchUmapStatus()
  else if (tab === 'color3d') fetchColor3D()
  else if (tab === 'tags' && !tagNetData.value) fetchTagNetwork()
  else if (tab === 'health' && !analyzerHealthData.value) fetchAnalyzerHealth()
}

// --- Semantic Map (UMAP) ---

async function fetchUmapStatus() {
  try {
    const res = await fetch('/api/analyzer/umap/status')
    if (res.ok) umapStatus.value = await res.json()
  } catch (e) { console.error(e) }
}

async function fetchUmapPoints() {
  umapLoading.value = true
  try {
    const res = await fetch('/api/analyzer/umap')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    umapPoints.value = data.points
    await nextTick()
    if (umapCanvasRef.value) initUmapCanvas(umapCanvasRef.value)
  } catch (e) {
    console.error('UMAP fetch error:', e)
  } finally {
    umapLoading.value = false
  }
}

async function triggerUmapJob() {
  try {
    await fetch('/api/analyzer/umap/analyze', { method: 'POST' })
    emit('toast', { msg: t('analyzer.semanticRunning', { done: 0, total: '?' }), type: 'info' })
    fetchUmapStatus()
  } catch (e) { console.error(e) }
}

function confirmRecompute() {
  if (window.confirm(t('analyzer.semanticRecomputeConfirm'))) {
    triggerUmapJob()
  }
}

const CLUSTER_HALO_COLORS = [
  'rgba(99,102,241,0.10)', 'rgba(16,185,129,0.10)', 'rgba(245,158,11,0.10)',
  'rgba(239,68,68,0.10)',  'rgba(59,130,246,0.10)', 'rgba(168,85,247,0.10)',
  'rgba(20,184,166,0.10)', 'rgba(249,115,22,0.10)', 'rgba(236,72,153,0.10)',
  'rgba(132,204,22,0.10)',
]

function umapResetView() {
  _umapTransform = { scale: 1, tx: 0, ty: 0 }
  umapCanvasRef.value?._umapDraw?.()
}

async function fetchUmapClusters(k = 10) {
  umapClusterLoading.value = true
  try {
    const res = await fetch(`/api/analyzer/umap/clusters?k=${k}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    umapClusterData.value = await res.json()
    umapResetView()
  } catch (e) {
    console.error('Cluster fetch error:', e)
  } finally {
    umapClusterLoading.value = false
  }
}

async function toggleClusters() {
  umapShowClusters.value = !umapShowClusters.value
  if (umapShowClusters.value && !umapClusterData.value) {
    await fetchUmapClusters(10)
  } else {
    umapCanvasRef.value?._umapDraw?.()
  }
}

function openImageFromUmap(sha256) {
  const img = (props.images || []).find(i => i.sha256 === sha256)
  if (img) {
    umapDetailImage.value = img
  } else {
    fetch(`/api/images/${sha256}`)
      .then(r => r.ok ? r.json() : null)
      .then(doc => { if (doc) umapDetailImage.value = doc })
      .catch(() => {})
  }
}

function initUmapCanvas(canvas) {
  const points = umapPoints.value
  if (!points.length) return

  if (canvas._umapResizeObserver) {
    canvas._umapResizeObserver.disconnect()
    canvas._umapResizeObserver = null
  }

  const rect = canvas.getBoundingClientRect()
  const W = Math.round(rect.width) || canvas.clientWidth || 800
  const H = Math.round(rect.height) || canvas.clientHeight || 600
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')
  const PAD = 32
  const DOT = 5

  let _umapRafPending = false
  let _umapDirty = false

  function toScreen(x, y) {
    const { scale, tx, ty } = _umapTransform
    const nx = PAD + x * (W - PAD * 2)
    const ny = PAD + y * (H - PAD * 2)
    return [nx * scale + tx, ny * scale + ty]
  }

  function drawClusterHalos(clusters) {
    for (const c of clusters) {
      const [cx, cy] = toScreen(c.centroid_x, c.centroid_y)
      const r = Math.min(120, Math.max(40, 20 + Math.sqrt(c.count) * 3))
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.fillStyle = CLUSTER_HALO_COLORS[c.id % CLUSTER_HALO_COLORS.length]
      ctx.fill()
    }
  }

  function drawClusterLabels(clusters) {
    ctx.font = 'bold 11px sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    for (const c of clusters) {
      const [cx, cy] = toScreen(c.centroid_x, c.centroid_y)
      const tags = (c.distinctive_tags || []).slice(0, 3)
      const lineH = 14
      const startY = cy - (tags.length * lineH) / 2
      tags.forEach((tag, i) => {
        const tw = ctx.measureText(tag).width
        const y = startY + i * lineH
        ctx.fillStyle = 'rgba(0,0,0,0.65)'
        ctx.fillRect(cx - tw / 2 - 3, y - 1, tw + 6, 13)
        ctx.fillStyle = '#e5e7eb'
        ctx.fillText(tag, cx, y)
      })
    }
  }

  function draw() {
    ctx.clearRect(0, 0, W, H)
    const clusters = umapShowClusters.value ? (umapClusterData.value?.clusters || []) : []
    const pointClusters = umapClusterData.value?.point_clusters || {}
    const selSet = new Set(umapSelection.value)

    if (clusters.length) drawClusterHalos(clusters)

    for (let i = 0; i < points.length; i++) {
      const p = points[i]
      const [sx, sy] = toScreen(p.x, p.y)
      const isHovered = i === _umapHoveredIdx
      const isSelected = selSet.has(p.sha256)
      ctx.beginPath()
      ctx.arc(sx, sy, isHovered ? DOT + 2 : DOT, 0, Math.PI * 2)
      ctx.fillStyle = p.hex || '#6b7280'
      ctx.fill()
      if (isHovered) {
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 1.5
        ctx.stroke()
      } else if (isSelected) {
        ctx.strokeStyle = '#fbbf24'
        ctx.lineWidth = 1.5
        ctx.stroke()
      }
    }

    if (clusters.length) drawClusterLabels(clusters)

    if (_umapSelectRect) {
      const { x1, y1, x2, y2 } = _umapSelectRect
      ctx.save()
      ctx.strokeStyle = '#60a5fa'
      ctx.lineWidth = 1.5
      ctx.setLineDash([4, 4])
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
      ctx.fillStyle = 'rgba(96,165,250,0.08)'
      ctx.fillRect(x1, y1, x2 - x1, y2 - y1)
      ctx.restore()
    }
  }

  function scheduleDraw() {
    _umapDirty = true
    if (_umapRafPending) return
    _umapRafPending = true
    requestAnimationFrame(() => {
      _umapRafPending = false
      if (_umapDirty) {
        _umapDirty = false
        draw()
      }
    })
  }

  draw()

  function hitTest(mx, my) {
    for (let i = points.length - 1; i >= 0; i--) {
      const [sx, sy] = toScreen(points[i].x, points[i].y)
      if (Math.hypot(mx - sx, my - sy) <= DOT + 4) return i
    }
    return -1
  }

  canvas._umapDraw = draw
  canvas._umapHit = hitTest

  canvas.onmousedown = (e) => {
    if (e.button !== 0) return
    _umapDidDrag = false
    const rect = canvas.getBoundingClientRect()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    if (e.shiftKey) {
      _umapSelectStart = { x: cx, y: cy }
      _umapSelectRect = { x1: cx, y1: cy, x2: cx, y2: cy }
      _umapPanStart = null
    } else {
      _umapPanStart = { x: e.clientX, y: e.clientY }
      _umapPanOrigin = { tx: _umapTransform.tx, ty: _umapTransform.ty }
      _umapSelectStart = null
      canvas.style.cursor = 'grabbing'
    }
  }

  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top

    if (_umapPanStart) {
      const dx = e.clientX - _umapPanStart.x
      const dy = e.clientY - _umapPanStart.y
      if (Math.hypot(dx, dy) > 2) _umapDidDrag = true
      _umapTransform.tx = _umapPanOrigin.tx + dx
      _umapTransform.ty = _umapPanOrigin.ty + dy
      scheduleDraw()
      return
    }

    if (_umapSelectStart) {
      _umapDidDrag = true
      _umapSelectRect = {
        x1: Math.min(_umapSelectStart.x, cx),
        y1: Math.min(_umapSelectStart.y, cy),
        x2: Math.max(_umapSelectStart.x, cx),
        y2: Math.max(_umapSelectStart.y, cy),
      }
      scheduleDraw()
      return
    }

    const idx = hitTest(cx, cy)
    if (idx !== _umapHoveredIdx) {
      _umapHoveredIdx = idx
      canvas.style.cursor = idx >= 0 ? 'pointer' : (e.shiftKey ? 'crosshair' : 'grab')
      scheduleDraw()
    }
    if (idx >= 0) {
      const p = points[idx]
      umapTooltip.value = { visible: true, x: e.clientX + 16, y: e.clientY - 80, sha256: p.sha256, name: p.name }
    } else {
      umapTooltip.value.visible = false
    }
  }

  canvas.onmouseup = (e) => {
    if (e.button !== 0) return
    canvas.style.cursor = 'grab'

    if (_umapPanStart) {
      const wasDrag = _umapDidDrag
      _umapPanStart = null
      _umapPanOrigin = null
      if (!wasDrag) {
        const rect = canvas.getBoundingClientRect()
        const idx = hitTest(e.clientX - rect.left, e.clientY - rect.top)
        if (idx >= 0) openImageFromUmap(points[idx].sha256)
      }
      return
    }

    if (_umapSelectStart) {
      if (_umapDidDrag && _umapSelectRect) {
        const { x1, y1, x2, y2 } = _umapSelectRect
        umapSelection.value = points
          .filter(p => {
            const [sx, sy] = toScreen(p.x, p.y)
            return sx >= x1 && sx <= x2 && sy >= y1 && sy <= y2
          })
          .map(p => p.sha256)
      }
      _umapSelectStart = null
      _umapSelectRect = null
      draw()
    }
  }

  canvas.onmouseleave = () => {
    umapTooltip.value.visible = false
    if (_umapPanStart) {
      _umapPanStart = null
      _umapPanOrigin = null
      canvas.style.cursor = 'grab'
    }
  }

  canvas.onwheel = (e) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.1 : 0.9
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const newScale = Math.max(0.3, Math.min(20, _umapTransform.scale * factor))
    _umapTransform.tx = mx - (mx - _umapTransform.tx) * (newScale / _umapTransform.scale)
    _umapTransform.ty = my - (my - _umapTransform.ty) * (newScale / _umapTransform.scale)
    _umapTransform.scale = newScale
    draw()
  }

  function onKeyDown(ev) {
    if (ev.key === 'Shift') {
      _umapShiftDown = true
      umapShiftDown.value = true
      if (!_umapPanStart) canvas.style.cursor = 'crosshair'
    }
    if (ev.key === 'Escape') {
      umapSelection.value = []
      _umapSelectRect = null
      draw()
    }
  }
  function onKeyUp(ev) {
    if (ev.key === 'Shift') {
      _umapShiftDown = false
      umapShiftDown.value = false
      if (!_umapPanStart) canvas.style.cursor = 'grab'
    }
  }
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)

  const ro = new ResizeObserver(() => {
    nextTick(() => requestAnimationFrame(() => initUmapCanvas(canvas)))
  })
  ro.observe(canvas)
  canvas._umapResizeObserver = ro

  canvas._umapCleanup = () => {
    window.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('keyup', onKeyUp)
    canvas._umapResizeObserver?.disconnect()
    canvas._umapResizeObserver = null
  }
}

watch([umapPoints, umapCanvasRef], ([pts, canvas]) => {
  if (pts.length && canvas) {
    nextTick(() => requestAnimationFrame(() => initUmapCanvas(canvas)))
  }
})

watch(() => props.show, (val) => {
  if (!val) {
    umapCanvasRef.value?._umapCleanup?.()
    umapTooltip.value.visible = false
    _purgeColor3D()
    color3dTooltip.value.visible = false
  } else {
    open()
  }
})

onUnmounted(() => {
  umapCanvasRef.value?._umapCleanup?.()
  _purgeColor3D()
  if (_tagSim) { _tagSim.stop(); _tagSim = null }
})

watch(umapSelection, () => {
  umapCanvasRef.value?._umapDraw?.()
})

const clusterTagCounts = computed(() => {
  if (!umapClusterData.value?.point_tags || !umapClusterData.value?.point_clusters) return null
  const { point_clusters, point_tags } = umapClusterData.value
  const counts = new Map()
  for (const [sha, cid] of Object.entries(point_clusters)) {
    if (!counts.has(cid)) counts.set(cid, { tagCounts: new Map(), total: 0 })
    const entry = counts.get(cid)
    entry.total++
    for (const tag of (point_tags[sha] || [])) {
      entry.tagCounts.set(tag, (entry.tagCounts.get(tag) || 0) + 1)
    }
  }
  return counts
})

const umapSelectionByCluster = computed(() => {
  if (!umapShowClusters.value || !umapClusterData.value) return null
  const { point_clusters: pointClusters, point_tags, clusters } = umapClusterData.value
  const groups = new Map()
  for (const sha of umapSelection.value) {
    const cid = (pointClusters || {})[sha] ?? -1
    if (!groups.has(cid)) groups.set(cid, [])
    groups.get(cid).push(sha)
  }
  return [...groups.entries()]
    .map(([cid, shas]) => {
      const cluster = (clusters || []).find(c => c.id === cid) || null
      let selectionTags = []
      if (point_tags && clusterTagCounts.value?.has(cid)) {
        const { tagCounts: clusterTC, total: clusterTotal } = clusterTagCounts.value.get(cid)
        const selTC = new Map()
        for (const sha of shas) {
          for (const tag of (point_tags[sha] || [])) {
            selTC.set(tag, (selTC.get(tag) || 0) + 1)
          }
        }
        const scored = []
        for (const [tag, cnt] of selTC) {
          if (cnt < 2) continue
          const tf_s = cnt / shas.length
          const tf_c = (clusterTC.get(tag) || 0) / clusterTotal
          const score = tf_s - tf_c
          if (score > 0) scored.push([tag, score])
        }
        selectionTags = scored.sort((a, b) => b[1] - a[1]).slice(0, 5).map(([t]) => t)
      }
      return { cluster, cid, shas, selectionTags }
    })
    .sort((a, b) => b.shas.length - a.shas.length)
})

const analyzerUmapJob = computed(() =>
  [...(props.jobsMap?.values() || [])].find(
    j => j.title === 'umap_analyze' && ['running', 'queued', 'cancelling'].includes(j.state)
  ) || null
)

watch(
  () => [...(props.jobsMap?.values() || [])].find(j => j.title === 'umap_analyze')?.state,
  (state) => {
    if (state === 'succeeded') { fetchUmapStatus(); fetchUmapPoints() }
    if (state === 'running') fetchUmapStatus()
  }
)

// --- Tag Taxonomy job state ---

const tagTaxonomyJob = computed(() =>
  tagTaxonomyJobId.value ? (props.jobsMap?.get(tagTaxonomyJobId.value) || null) : null
)

// --- Color Space 3D ---

const color3dBackfillJob = computed(() =>
  [...(props.jobsMap?.values() || [])].find(
    j => j.title === 'color_extract' && ['running', 'queued'].includes(j.state)
  ) || null
)

watch(
  () => [...(props.jobsMap?.values() || [])].find(j => j.title === 'color_extract')?.state,
  (state) => {
    if (state === 'succeeded') fetchColor3D()
  }
)

async function fetchColor3D() {
  color3dLoading.value = true
  try {
    const res = await fetch('/api/analyzer/color-3d?limit=5000')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    color3dPoints.value = data.points
    color3dBackfillNeeded.value = data.backfill_needed ?? false
    color3dPendingCount.value = data.pending_count ?? 0
    await nextTick()
    if (color3dContainerRef.value && color3dPoints.value.length)
      initPlotly3D(color3dContainerRef.value)
  } catch (e) {
    console.error('Color3D fetch error:', e)
  } finally {
    color3dLoading.value = false
  }
}

async function triggerColorBackfill() {
  try {
    const res = await fetch('/api/admin/colors/backfill', { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    // SSE watcher fires fetchColor3D on job completion, but if SSE misses the event,
    // fall back to polling until the pending count clears (max 60 s).
    const deadline = Date.now() + 60_000
    const poll = async () => {
      await fetchColor3D()
      if (color3dPendingCount.value > 0 && Date.now() < deadline)
        setTimeout(poll, 4000)
    }
    setTimeout(poll, 3000)
  } catch (e) {
    console.error('Color backfill trigger error:', e)
  }
}

async function initPlotly3D(container) {
  if (!color3dPoints.value.length) return
  const Plotly = _Plotly ?? await import('plotly.js-dist-min')
  _Plotly = Plotly
  const pts = color3dPoints.value
  const trace = {
    type: 'scatter3d',
    mode: 'markers',
    x: pts.map(p => p.a),
    y: pts.map(p => p.b),
    z: pts.map(p => p.L),
    text: pts.map(p => p.name),
    customdata: pts.map(p => p.sha256),
    hovertemplate: '%{text}<extra></extra>',
    marker: {
      color: pts.map(p => p.hex),
      size: 4,
      opacity: 0.85,
    },
  }
  const layout = {
    paper_bgcolor: '#111827',
    scene: {
      bgcolor: '#111827',
      xaxis: { title: 'a* (Green–Red)', color: '#9ca3af', gridcolor: '#374151' },
      yaxis: { title: 'b* (Blue–Yellow)', color: '#9ca3af', gridcolor: '#374151' },
      zaxis: { title: 'L* (Lightness)', color: '#9ca3af', gridcolor: '#374151' },
    },
    margin: { l: 0, r: 0, t: 0, b: 0 },
    font: { color: '#d1d5db' },
  }
  // remove previous listeners before re-registering on re-init
  _purgeColor3D()

  Plotly.newPlot(container, [trace], layout, { responsive: true, displayModeBar: false })
  _plotlyLoaded = true
  _plotlyContainer = container

  // Plotly 3D hover events don't expose clientX/Y — track position natively
  // getBoundingClientRect is called at most once per rAF frame
  let _rafPending = false
  const onMouseMove = (e) => {
    if (_rafPending) return
    _rafPending = true
    requestAnimationFrame(() => {
      _rafPending = false
      const rect = container.getBoundingClientRect()
      _color3dMouseX = e.clientX - rect.left
      _color3dMouseY = e.clientY - rect.top
    })
  }
  container.addEventListener('mousemove', onMouseMove)
  _color3dMouseMoveHandler = onMouseMove

  container.on('plotly_hover', (eventData) => {
    const pt = eventData.points?.[0]
    if (!pt) return
    color3dTooltip.value = {
      visible: true,
      x: _color3dMouseX,
      y: _color3dMouseY,
      sha256: pt.customdata,
      name: pt.text,
    }
  })

  container.on('plotly_unhover', () => {
    color3dTooltip.value.visible = false
  })

  container.on('plotly_click', (eventData) => {
    const pt = eventData.points?.[0]
    if (!pt) return
    color3dTooltip.value.visible = false
    openImageFromColor3D(pt.customdata)
  })
}

function _purgeColor3D() {
  if (!_plotlyContainer) return
  if (_color3dMouseMoveHandler) {
    _plotlyContainer.removeEventListener('mousemove', _color3dMouseMoveHandler)
    _color3dMouseMoveHandler = null
  }
  try {
    if (_Plotly) _Plotly.purge(_plotlyContainer)
  } catch (_) {}
  _plotlyContainer = null
  _plotlyLoaded = false
}

function openImageFromColor3D(sha256) {
  const img = (props.images || []).find(i => i.sha256 === sha256)
  if (img) {
    color3dDetailImage.value = img
  } else {
    fetch(`/api/images/${sha256}`)
      .then(r => r.ok ? r.json() : null)
      .then(doc => { if (doc) color3dDetailImage.value = doc })
      .catch(() => {})
  }
}

watch(color3dContainerRef, (el) => {
  if (el && color3dPoints.value.length) initPlotly3D(el)
})

// --- Tag Network ---

async function fetchTagNetwork() {
  tagNetLoading.value = true
  try {
    const params = new URLSearchParams({ min_count: tagMinCount.value, top_tags: tagTopTags.value })
    const res = await fetch(`/api/analyzer/tag-network?${params}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    tagNetData.value = await res.json()
    await nextTick()
    if (tagNetCanvasRef.value) initTagNetCanvas(tagNetCanvasRef.value)
  } catch (e) {
    console.error('Tag network error:', e)
  } finally {
    tagNetLoading.value = false
  }
}

const CATEGORY_COLORS = {
  subject: '#f87171',
  style: '#60a5fa',
  mood: '#a78bfa',
  environment: '#34d399',
  action: '#fbbf24',
  quality: '#f472b6',
}
const DEFAULT_NODE_COLOR = '#6b7280'

function nodeColor(nodeId) {
  const cat = tagTaxonomy.value[nodeId]
  return CATEGORY_COLORS[cat] || DEFAULT_NODE_COLOR
}

function nodeRadius(count) {
  return Math.max(5, Math.min(20, 4 + Math.log(count + 1) * 2.5))
}

function buildTagNetSelected(node) {
  const neighbors = []
  for (const l of _tagNetSimLinks) {
    const s = typeof l.source === 'object' ? l.source : _tagNetSimNodes[l.source]
    const t = typeof l.target === 'object' ? l.target : _tagNetSimNodes[l.target]
    if (!s || !t) continue
    if (s.id === node.id) neighbors.push({ id: t.id, label: t.label, weight: l.weight })
    else if (t.id === node.id) neighbors.push({ id: s.id, label: s.label, weight: l.weight })
  }
  neighbors.sort((a, b) => b.weight - a.weight)
  tagNetSelected.value = {
    id: node.id,
    label: node.label,
    count: node.count,
    category: tagTaxonomy.value[node.id] ?? null,
    neighbors: neighbors.slice(0, 15),
  }
}

function selectTagNetNode(id) {
  const node = _tagNetSimNodes.find(n => n.id === id)
  if (!node) return
  _tagNetSelectedId = id
  buildTagNetSelected(node)
  const canvas = tagNetCanvasRef.value
  if (canvas && node.x != null) {
    const W = canvas.clientWidth || 800
    const H = canvas.clientHeight || 600
    _tagNetTransform.tx = W / 2 - node.x * _tagNetTransform.scale
    _tagNetTransform.ty = H / 2 - node.y * _tagNetTransform.scale
  }
  _tagNetDraw?.()
}

function tagNetZoom(factor) {
  const canvas = tagNetCanvasRef.value
  if (!canvas) return
  const W = canvas.clientWidth || 800
  const H = canvas.clientHeight || 600
  const T = _tagNetTransform
  const newScale = Math.max(0.3, Math.min(20, T.scale * factor))
  T.tx = W / 2 - (W / 2 - T.tx) * (newScale / T.scale)
  T.ty = H / 2 - (H / 2 - T.ty) * (newScale / T.scale)
  T.scale = newScale
  _tagNetDraw?.()
}

function tagNetResetView() {
  _tagNetTransform.scale = 1
  _tagNetTransform.tx = 0
  _tagNetTransform.ty = 0
  _tagNetDraw?.()
}

function tagNetDeselect() {
  _tagNetSelectedId = null
  tagNetSelected.value = null
  _tagNetDraw?.()
}

function toggleTagNetSim() {
  if (!_tagSim) return
  if (tagNetSimFrozen.value) {
    tagNetSimFrozen.value = false
    _tagSim.restart()
  } else {
    tagNetSimFrozen.value = true
    _tagSim.stop()
  }
}

function initTagNetCanvas(canvas) {
  if (!window.__d3force__) {
    import('d3-force').then(d3 => {
      window.__d3force__ = d3
      initTagNetCanvas(canvas)
    })
    return
  }
  const { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } = window.__d3force__
  const { nodes, edges } = tagNetData.value
  if (!nodes.length) return

  const W = canvas.clientWidth || 800
  const H = canvas.clientHeight || 600
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')

  const simNodes = nodes.map(n => ({ ...n }))
  const idxById = Object.fromEntries(simNodes.map((n, i) => [n.id, i]))
  const simLinks = edges
    .map(e => ({ source: idxById[e.source], target: idxById[e.target], weight: e.weight }))
    .filter(l => l.source !== undefined && l.target !== undefined)

  _tagNetSimNodes = simNodes
  _tagNetSimLinks = simLinks

  if (_tagSim) { _tagSim.stop(); _tagSim = null }

  const T = _tagNetTransform

  function getLinkedIds(nodeId) {
    const ids = new Set()
    for (const l of simLinks) {
      const s = typeof l.source === 'object' ? l.source : simNodes[l.source]
      const t = typeof l.target === 'object' ? l.target : simNodes[l.target]
      if (!s || !t) continue
      if (s.id === nodeId) ids.add(t.id)
      else if (t.id === nodeId) ids.add(s.id)
    }
    return ids
  }

  function draw() {
    ctx.clearRect(0, 0, W, H)
    ctx.save()
    ctx.translate(T.tx, T.ty)
    ctx.scale(T.scale, T.scale)

    const q = tagNetSearch.value.trim().toLowerCase()
    const hasSearch = q.length > 0
    const selId = _tagNetSelectedId
    const hovId = _tagNetHoveredId
    const selLinked = selId ? getLinkedIds(selId) : null
    const hovLinked = hovId ? getLinkedIds(hovId) : null

    // Draw edges
    for (const link of simLinks) {
      const s = typeof link.source === 'object' ? link.source : simNodes[link.source]
      const t = typeof link.target === 'object' ? link.target : simNodes[link.target]
      if (!s?.x || !t?.x) continue

      const isSel = selId && (s.id === selId || t.id === selId)
      const isHov = !isSel && hovId && (s.id === hovId || t.id === hovId)

      ctx.beginPath()
      ctx.moveTo(s.x, s.y)
      ctx.lineTo(t.x, t.y)
      if (isSel) {
        ctx.strokeStyle = '#a78bfa'
        ctx.globalAlpha = 0.75
        ctx.lineWidth = Math.min(4, 1.5 + link.weight * 0.06)
      } else if (isHov) {
        ctx.strokeStyle = '#818cf8'
        ctx.globalAlpha = 0.55
        ctx.lineWidth = Math.min(3, 1 + link.weight * 0.05)
      } else {
        ctx.strokeStyle = '#374151'
        ctx.globalAlpha = hasSearch ? 0.08 : (selId ? 0.12 : 0.3)
        ctx.lineWidth = Math.min(2.5, 0.8 + link.weight * 0.04)
      }
      ctx.stroke()
    }
    ctx.globalAlpha = 1

    // Draw nodes
    for (const node of simNodes) {
      if (!node.x) continue
      const r = nodeRadius(node.count)
      const isSel = node.id === selId
      const isHov = node.id === hovId
      const matchSearch = !hasSearch || node.label.toLowerCase().includes(q)
      const isNeighbor = selLinked?.has(node.id) || hovLinked?.has(node.id)
      const dimmed = hasSearch ? !matchSearch : (selId ? !(isSel || isNeighbor) : false)

      ctx.globalAlpha = dimmed ? 0.12 : 1

      // Selection glow ring
      if (isSel) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + 5, 0, Math.PI * 2)
        ctx.fillStyle = nodeColor(node.id) + '44'
        ctx.fill()
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + 2.5, 0, Math.PI * 2)
        ctx.strokeStyle = nodeColor(node.id)
        ctx.lineWidth = 2 / T.scale
        ctx.stroke()
      } else if (isHov) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + 3, 0, Math.PI * 2)
        ctx.strokeStyle = '#f9fafb'
        ctx.lineWidth = 1.5 / T.scale
        ctx.globalAlpha = dimmed ? 0.12 : 0.5
        ctx.stroke()
        ctx.globalAlpha = dimmed ? 0.12 : 1
      }

      // Node fill
      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, Math.PI * 2)
      ctx.fillStyle = nodeColor(node.id)
      ctx.fill()

      // Label — always show, size clamped to be legible
      const baseFontPx = Math.max(9, r - 1)
      const screenFontPx = baseFontPx * T.scale
      if (screenFontPx >= 6) {
        ctx.font = `${baseFontPx}px sans-serif`
        ctx.fillStyle = isSel ? '#ffffff' : (isHov ? '#f3f4f6' : '#d1d5db')
        ctx.textAlign = 'center'
        ctx.textBaseline = 'top'
        const maxLen = Math.max(6, Math.round(18 * Math.sqrt(T.scale)))
        const lbl = node.label.length > maxLen ? node.label.slice(0, maxLen) + '…' : node.label
        ctx.fillText(lbl, node.x, node.y + r + 2 / T.scale)
      }

      ctx.globalAlpha = 1
    }

    ctx.restore()
  }

  _tagNetDraw = draw

  function hitTest(mx, my) {
    const ix = (mx - T.tx) / T.scale
    const iy = (my - T.ty) / T.scale
    for (let i = simNodes.length - 1; i >= 0; i--) {
      const n = simNodes[i]
      if (!n.x) continue
      if (Math.hypot(ix - n.x, iy - n.y) <= nodeRadius(n.count) + 6) return i
    }
    return -1
  }

  canvas.onmousedown = (e) => {
    if (e.button !== 0) return
    _tagNetDidDrag = false
    _tagNetPanStart = { x: e.clientX, y: e.clientY }
    _tagNetPanOrigin = { tx: T.tx, ty: T.ty }
    canvas.style.cursor = 'grabbing'
  }

  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect()
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top

    if (_tagNetPanStart) {
      const dx = e.clientX - _tagNetPanStart.x
      const dy = e.clientY - _tagNetPanStart.y
      if (Math.hypot(dx, dy) > 3) _tagNetDidDrag = true
      T.tx = _tagNetPanOrigin.tx + dx
      T.ty = _tagNetPanOrigin.ty + dy
      draw()
      return
    }

    const idx = hitTest(cx, cy)
    const newId = idx >= 0 ? simNodes[idx].id : null
    if (newId !== _tagNetHoveredId) {
      _tagNetHoveredId = newId
      canvas.style.cursor = newId ? 'pointer' : 'grab'
      draw()
    }
    if (idx >= 0) {
      const node = simNodes[idx]
      tagNetTooltip.value = {
        visible: true,
        x: e.clientX + 16,
        y: e.clientY - 64,
        node: { id: node.id, label: node.label, count: node.count, category: tagTaxonomy.value[node.id] ?? null },
      }
    } else {
      tagNetTooltip.value.visible = false
    }
  }

  canvas.onmouseup = (e) => {
    if (e.button !== 0) return
    const wasDrag = _tagNetDidDrag
    _tagNetPanStart = null
    _tagNetPanOrigin = null
    canvas.style.cursor = _tagNetHoveredId ? 'pointer' : 'grab'
    if (!wasDrag) {
      const rect = canvas.getBoundingClientRect()
      const idx = hitTest(e.clientX - rect.left, e.clientY - rect.top)
      if (idx >= 0) {
        selectTagNetNode(simNodes[idx].id)
      } else {
        _tagNetSelectedId = null
        tagNetSelected.value = null
        draw()
      }
    }
  }

  canvas.onmouseleave = () => {
    tagNetTooltip.value.visible = false
    if (_tagNetPanStart) { _tagNetPanStart = null; _tagNetPanOrigin = null }
    _tagNetHoveredId = null
    canvas.style.cursor = 'grab'
    draw()
  }

  canvas.onwheel = (e) => {
    e.preventDefault()
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
    const rect = canvas.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const newScale = Math.max(0.3, Math.min(20, T.scale * factor))
    T.tx = mx - (mx - T.tx) * (newScale / T.scale)
    T.ty = my - (my - T.ty) * (newScale / T.scale)
    T.scale = newScale
    draw()
  }

  if (tagNetSimFrozen.value) {
    draw()
    return
  }

  _tagSim = forceSimulation(simNodes)
    .force('link', forceLink(simLinks).id((_, i) => i).distance(90).strength(0.25))
    .force('charge', forceManyBody().strength(-150))
    .force('center', forceCenter(W / 2, H / 2))
    .force('collide', forceCollide(22))
    .on('tick', draw)
    .on('end', () => { tagNetSimFrozen.value = true; draw() })
}

watch([tagNetData, tagNetCanvasRef], ([data, canvas]) => {
  if (data && canvas) {
    _tagNetTransform = { scale: 1, tx: 0, ty: 0 }
    _tagNetSelectedId = null
    _tagNetHoveredId = null
    tagNetSelected.value = null
    tagNetSimFrozen.value = false
    initTagNetCanvas(canvas)
  }
})

watch(tagTaxonomy, () => {
  if (tagNetSelected.value) {
    tagNetSelected.value = {
      ...tagNetSelected.value,
      category: tagTaxonomy.value[tagNetSelected.value.id] ?? null,
    }
  }
  _tagNetDraw?.()
})

watch(tagNetSearch, () => {
  _tagNetDraw?.()
})

// Stop/restart simulation when tag tab is not visible
watch(analyzerTab, (tab) => {
  if (tab !== 'tags') {
    tagNetTooltip.value.visible = false
    if (_tagSim) _tagSim.stop()
  } else {
    if (_tagSim && !tagNetSimFrozen.value && _tagSim.alpha() > _tagSim.alphaMin()) _tagSim.restart()
  }
})

// Debounced auto-refetch when filter params change
watch([tagMinCount, tagTopTags], () => {
  clearTimeout(_tagNetDebounceTimer)
  _tagNetDebounceTimer = setTimeout(() => {
    if (analyzerTab.value === 'tags') fetchTagNetwork()
  }, 400)
})

async function runTagTaxonomy() {
  if (!tagNetData.value?.nodes.length) return
  tagTaxonomyLoading.value = true
  const allTags = tagNetData.value.nodes.map(n => n.id)
  try {
    const res = await fetch('/api/analyzer/tag-taxonomy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: allTags }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const { job_id } = await res.json()
    tagTaxonomyJobId.value = job_id
    // tagTaxonomyLoading は下の watch で完了/失敗時に解除
  } catch (e) {
    console.error('Tag taxonomy error:', e)
    tagTaxonomyLoading.value = false
  }
}

watch(
  () => tagTaxonomyJobId.value ? props.jobsMap?.get(tagTaxonomyJobId.value) : null,
  (job) => {
    if (!job) return
    if (job.state === 'succeeded') {
      if (job.result?.taxonomy) tagTaxonomy.value = job.result.taxonomy
      tagTaxonomyLoading.value = false
      tagTaxonomyJobId.value = null
    } else if (job.state === 'failed' || job.state === 'cancelled') {
      tagTaxonomyLoading.value = false
      tagTaxonomyJobId.value = null
    }
  }
)

// --- Dataset Health ---

async function fetchAnalyzerHealth() {
  analyzerHealthLoading.value = true
  try {
    const res = await fetch('/api/analyzer/health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    analyzerHealthData.value = await res.json()
  } catch (e) {
    console.error('Analyzer health fetch error:', e)
  } finally {
    analyzerHealthLoading.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[58] bg-black/92 flex items-center justify-center p-3">
      <div class="bg-gray-900 rounded-2xl w-full max-w-7xl shadow-2xl border border-gray-800 flex flex-col" style="height: 94vh">
        <!-- Header -->
        <div class="flex items-center gap-4 px-5 py-3 border-b border-gray-800 flex-shrink-0">
          <h2 class="text-sm font-semibold text-gray-100 flex-shrink-0">{{ $t('analyzer.title') }}</h2>
          <!-- Tabs -->
          <nav class="flex gap-1">
            <button v-for="tab in [
              { id:'semantic', label: $t('analyzer.tabSemantic') },
              { id:'color3d',  label: $t('analyzer.tabColor3d') },
              { id:'tags',     label: $t('analyzer.tabTags') },
              { id:'health',   label: $t('analyzer.tabHealth') },
            ]" :key="tab.id"
              @click="switchAnalyzerTab(tab.id)"
              :class="analyzerTab === tab.id
                ? 'bg-violet-700/60 text-violet-100 border-violet-500/50'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200 border-gray-700/50'"
              class="px-3 py-1 rounded-lg text-xs border transition-colors">
              {{ tab.label }}
            </button>
          </nav>
          <div class="flex-1" />
          <button @click="emit('update:show', false)" class="text-gray-500 hover:text-gray-200 text-xl leading-none w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-hidden relative">

          <!-- ── Semantic Map ── -->
          <div v-if="analyzerTab === 'semantic'" class="w-full h-full flex flex-col">
            <!-- Controls bar -->
            <div class="flex items-center gap-3 px-4 py-2 border-b border-gray-800 flex-shrink-0 text-xs">
              <span class="text-gray-400">
                <template v-if="analyzerUmapJob?.state === 'running' || umapStatus?.running">
                  <span v-if="analyzerUmapJob?.progress_text">{{ analyzerUmapJob.progress_text }}</span>
                  <span v-else-if="analyzerUmapJob?.progress > 0">{{ Math.round(analyzerUmapJob.progress * 100) }}%</span>
                  <span v-else>{{ $t('analyzer.semanticRunning', { done: 0, total: '?' }) }}</span>
                </template>
                <template v-else-if="umapStatus?.computed">{{ $t('analyzer.semanticStatus', { covered: umapStatus.done || umapStatus.covered, total: umapStatus.total }) }}</template>
                <template v-else>{{ $t('analyzer.semanticPending') }}</template>
              </span>
              <!-- UMAP compute button (shown on left only when not yet computed) -->
              <button
                v-if="!umapStatus?.running && !umapStatus?.computed"
                @click="triggerUmapJob()"
                class="px-3 py-1 bg-violet-700/50 hover:bg-violet-600/60 border border-violet-500/40 rounded-lg text-violet-200 transition-colors">
                {{ $t('analyzer.semanticCompute') }}
              </button>
              <button v-if="umapStatus?.computed && !umapPoints.length"
                @click="fetchUmapPoints"
                :disabled="umapLoading"
                class="px-3 py-1 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded-lg text-gray-200 transition-colors disabled:opacity-50">
                {{ umapLoading ? '…' : 'Load Map' }}
              </button>
              <!-- left: map controls -->
              <button v-if="umapPoints.length"
                @click="toggleClusters"
                :disabled="umapClusterLoading"
                :class="umapShowClusters
                  ? 'bg-violet-700/60 text-violet-200 border-violet-500/50'
                  : 'bg-gray-800 text-gray-400 border-gray-700/50 hover:bg-gray-700 hover:text-gray-200'"
                class="px-2 py-1 rounded-lg border transition-colors disabled:opacity-50">
                {{ umapClusterLoading ? '…' : '⬡ Clusters' }}
              </button>
              <button v-if="umapSelection.length"
                @click="umapSelection = []"
                class="px-2 py-1 bg-amber-900/40 text-amber-300 border border-amber-700/50 rounded-lg hover:bg-amber-800/40 transition-colors">
                ✕ {{ umapSelection.length }}
              </button>
              <button v-if="umapPoints.length"
                @click="umapResetView"
                title="Reset zoom"
                class="px-2 py-1 bg-gray-800 text-gray-400 border border-gray-700/50 rounded-lg hover:bg-gray-700 hover:text-gray-200 transition-colors">
                ⌂
              </button>
              <!-- right: recompute button only -->
              <div class="ml-auto">
                <button
                  v-if="!umapStatus?.running && umapStatus?.computed"
                  @click="confirmRecompute()"
                  class="px-3 py-1 bg-red-900/40 hover:bg-red-800/50 border border-red-700/40 rounded-lg text-red-300 transition-colors">
                  {{ $t('analyzer.semanticRecompute') }}
                </button>
              </div>
            </div>
            <!-- Canvas + Selection panel -->
            <div class="flex-1 flex flex-col overflow-hidden" style="min-height:0">
              <div class="flex-1 relative overflow-hidden" style="min-height:0">
                <canvas v-if="umapPoints.length" ref="umapCanvasRef" class="w-full h-full" style="cursor:grab" />
                <div v-else class="absolute inset-0 flex items-center justify-center text-gray-600 text-sm">
                  {{ umapStatus?.computed ? 'Click Load Map' : $t('analyzer.semanticPending') }}
                </div>
                <!-- Shift mode indicator -->
                <div v-if="umapPoints.length"
                  class="absolute top-2 left-2 text-xs px-2 py-1 rounded pointer-events-none transition-opacity"
                  :class="umapShiftDown ? 'bg-blue-900/70 text-blue-300 opacity-100' : 'opacity-0'">
                  ⬚ Selection mode — drag to select
                </div>
                <!-- always-visible interaction hint -->
                <div v-if="umapPoints.length"
                  class="absolute bottom-2 left-2 text-xs text-gray-600 pointer-events-none select-none">
                  Shift+drag: select region　Drag: pan　Wheel: zoom
                </div>
              </div>
              <!-- Selection strip -->
              <Transition
                enter-active-class="transition-all duration-200"
                leave-active-class="transition-all duration-200"
                enter-from-class="translate-y-full opacity-0"
                enter-to-class="translate-y-0 opacity-100"
                leave-from-class="translate-y-0 opacity-100"
                leave-to-class="translate-y-full opacity-0">
                <div v-if="umapSelection.length"
                  class="flex-shrink-0 border-t border-gray-700 bg-gray-900/95 overflow-y-auto"
                  style="max-height:240px">
                  <div class="flex items-center justify-between px-4 py-1.5 border-b border-gray-800 sticky top-0 bg-gray-900/95 z-10">
                    <span class="text-xs text-gray-400">{{ umapSelection.length }} selected</span>
                    <button @click="umapSelection = []" class="text-xs text-gray-500 hover:text-gray-300 transition-colors">Clear</button>
                  </div>
                  <!-- cluster-based analysis view -->
                  <template v-if="umapSelectionByCluster">
                    <div v-for="g in umapSelectionByCluster" :key="g.cid"
                      class="border-b border-gray-800/50 last:border-b-0">
                      <div class="flex items-center gap-2 px-3 pt-1.5 pb-1 flex-wrap">
                        <span class="text-xs font-semibold text-violet-400">Cluster {{ g.cid }}</span>
                        <span class="text-xs text-gray-600">{{ g.shas.length }} images</span>
                        <template v-if="g.cluster?.distinctive_tags?.length">
                          <span class="text-xs text-gray-600 ml-1">Distinctive tags:</span>
                          <span v-for="tag in g.cluster.distinctive_tags" :key="tag"
                            class="text-xs px-1.5 py-0.5 bg-violet-900/40 text-violet-300 rounded border border-violet-800/40">
                            {{ tag }}
                          </span>
                        </template>
                        <template v-if="g.selectionTags?.length">
                          <span class="text-xs text-gray-600 ml-1">Selection features:</span>
                          <span v-for="tag in g.selectionTags" :key="'sel-'+tag"
                            class="text-xs px-1.5 py-0.5 bg-cyan-900/40 text-cyan-300 rounded border border-cyan-800/40">
                            {{ tag }}
                          </span>
                        </template>
                      </div>
                      <div class="flex gap-1.5 overflow-x-auto px-3 pb-2">
                        <div v-for="sha in g.shas.slice(0, 30)" :key="sha"
                          class="flex-shrink-0 w-20 h-20 cursor-pointer rounded overflow-hidden border border-gray-700 hover:border-violet-500 transition-colors"
                          @click="openImageFromUmap(sha)">
                          <img :src="`/api/thumbnails/${sha}.webp`" class="w-full h-full object-cover" loading="lazy" />
                        </div>
                        <div v-if="g.shas.length > 30"
                          class="flex-shrink-0 w-20 h-20 flex items-center justify-center text-gray-500 text-xs text-center px-1">
                          +{{ g.shas.length - 30 }}<br>more
                        </div>
                      </div>
                    </div>
                  </template>
                  <!-- cluster off: standard thumbnail grid -->
                  <div v-else class="flex gap-2 overflow-x-auto p-2">
                    <div v-for="sha in umapSelection.slice(0, 60)" :key="sha"
                      class="flex-shrink-0 w-24 h-24 cursor-pointer rounded overflow-hidden border border-gray-700 hover:border-violet-500 transition-colors"
                      @click="openImageFromUmap(sha)">
                      <img :src="`/api/thumbnails/${sha}.webp`" class="w-full h-full object-cover" loading="lazy" />
                    </div>
                    <div v-if="umapSelection.length > 60"
                      class="flex-shrink-0 w-24 h-24 flex items-center justify-center text-gray-500 text-xs text-center px-1">
                      +{{ umapSelection.length - 60 }}<br>more
                    </div>
                  </div>
                </div>
              </Transition>
            </div>
          </div>

          <!-- ── Color Space 3D ── -->
          <div v-if="analyzerTab === 'color3d'" class="w-full h-full flex flex-col relative">
            <div v-if="color3dLoading" class="absolute inset-0 flex items-center justify-center text-gray-500 text-sm">{{ $t('analyzer.color3dLoading') }}</div>
            <template v-else-if="!color3dPoints.length">
              <div class="absolute inset-0 flex flex-col items-center justify-center gap-4 text-gray-600 text-sm">
                <p>{{ $t('analyzer.color3dEmpty') }}</p>
                <div v-if="color3dBackfillNeeded || color3dPendingCount > 0"
                  class="flex flex-col items-center gap-3 p-4 bg-gray-800/50 rounded-xl border border-gray-700 max-w-xs">
                  <p v-if="color3dPendingCount > 0" class="text-xs text-amber-400 text-center">
                    {{ $t('analyzer.color3dPending', { n: color3dPendingCount }) }}
                  </p>
                  <div v-if="color3dBackfillJob" class="text-xs text-blue-400 flex items-center gap-2">
                    <span class="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                    {{ $t('analyzer.color3dRepairing') }}
                    <span v-if="color3dBackfillJob.progress_text" class="text-gray-500">{{ color3dBackfillJob.progress_text }}</span>
                  </div>
                  <button v-else @click="triggerColorBackfill"
                    class="px-4 py-2 bg-violet-700/50 hover:bg-violet-600/60 border border-violet-500/40 rounded-lg text-violet-200 text-xs transition-colors">
                    {{ $t('analyzer.color3dRepairBtn') }}
                  </button>
                </div>
              </div>
            </template>
            <template v-else>
              <div ref="color3dContainerRef" class="w-full h-full" />
              <!-- repair banner: points exist but unprocessed data remains -->
              <div v-if="color3dBackfillJob || color3dBackfillNeeded"
                class="absolute top-2 left-2 right-2 flex items-center justify-between gap-2 px-3 py-2 bg-gray-900/90 border border-gray-700 rounded-lg text-xs z-20">
                <span v-if="color3dBackfillJob" class="text-blue-400 flex items-center gap-1.5">
                  <span class="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                  {{ $t('analyzer.color3dRepairing') }}
                  <span v-if="color3dBackfillJob.progress_text" class="text-gray-500">{{ color3dBackfillJob.progress_text }}</span>
                </span>
                <span v-else class="text-amber-400">
                  {{ color3dPendingCount > 0 ? $t('analyzer.color3dPending', { n: color3dPendingCount }) : $t('analyzer.color3dIncomplete') }}
                </span>
                <button v-if="!color3dBackfillJob" @click="triggerColorBackfill"
                  class="px-2 py-1 bg-violet-700/40 hover:bg-violet-600/50 border border-violet-500/30 rounded text-violet-300 transition-colors">
                  {{ $t('analyzer.color3dRepairBtn') }}
                </button>
              </div>
            </template>
            <!-- Hover thumbnail tooltip -->
            <div v-if="color3dTooltip.visible && color3dTooltip.sha256"
              class="pointer-events-none absolute z-50 rounded-lg overflow-hidden shadow-xl border border-gray-700 bg-gray-900"
              :style="{ left: color3dTooltip.x + 12 + 'px', top: color3dTooltip.y - 80 + 'px' }">
              <img :src="`/api/thumbnails/${color3dTooltip.sha256}.webp`"
                class="block w-32 h-32 object-cover" />
              <div class="px-2 py-1 text-xs text-gray-300 truncate max-w-32">{{ color3dTooltip.name }}</div>
            </div>
            <!-- Click detail panel -->
            <Transition
              enter-active-class="transition duration-150 ease-out"
              leave-active-class="transition duration-100 ease-in"
              enter-from-class="opacity-0 scale-95"
              enter-to-class="opacity-100 scale-100"
              leave-from-class="opacity-100 scale-100"
              leave-to-class="opacity-0 scale-95">
              <div v-if="color3dDetailImage"
                class="absolute inset-0 z-10 bg-black/70 flex items-center justify-center p-6"
                @click.self="color3dDetailImage = null">
                <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden">
                  <div class="flex gap-4 p-4">
                    <div class="flex-shrink-0 flex flex-col items-center gap-2">
                      <img :src="`/api/thumbnails/${color3dDetailImage.sha256}.webp`"
                        class="w-48 h-48 object-cover rounded-xl border border-gray-700" />
                      <div v-if="color3dDetailImage.palette_hex?.length" class="flex gap-1">
                        <div v-for="hex in color3dDetailImage.palette_hex.slice(0,5)" :key="hex"
                          :style="`background:${hex}`"
                          class="w-6 h-6 rounded-full border border-gray-600 shadow-sm" />
                      </div>
                    </div>
                    <div class="flex-1 min-w-0 space-y-3 overflow-y-auto" style="max-height:280px">
                      <div class="flex items-start justify-between gap-2">
                        <h3 class="text-sm font-semibold text-gray-100 break-all">{{ color3dDetailImage.name }}</h3>
                        <button @click="color3dDetailImage = null"
                          class="flex-shrink-0 text-gray-500 hover:text-gray-200 text-xl leading-none w-7 h-7 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
                      </div>
                      <div class="text-xs text-gray-500 flex flex-wrap gap-3">
                        <span v-if="color3dDetailImage.size">{{ (color3dDetailImage.size/1024/1024).toFixed(1) }} MB</span>
                        <span v-if="color3dDetailImage.model_name" class="text-purple-400">{{ color3dDetailImage.model_name }}</span>
                      </div>
                      <div v-if="color3dDetailImage.positive_prompt">
                        <p class="text-xs font-semibold text-purple-400 uppercase tracking-wide mb-1">Prompt</p>
                        <p class="text-xs text-gray-300 whitespace-pre-wrap break-words bg-gray-800 rounded-lg p-2.5 leading-relaxed max-h-28 overflow-y-auto">{{ color3dDetailImage.positive_prompt }}</p>
                      </div>
                      <div v-if="color3dDetailImage.wd14_tags?.length">
                        <p class="text-xs font-semibold text-teal-400 uppercase tracking-wide mb-1">Tags</p>
                        <div class="flex flex-wrap gap-1">
                          <span v-for="tag in color3dDetailImage.wd14_tags.slice(0,30)" :key="tag"
                            class="px-1.5 py-0.5 bg-gray-800 text-gray-300 text-xs rounded cursor-pointer hover:bg-gray-700 transition-colors"
                            @click="emit('search-tag', tag); color3dDetailImage = null; emit('update:show', false)">
                            {{ tag }}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div class="px-4 pb-3 flex gap-2 border-t border-gray-800 pt-3">
                    <button
                      @click="emit('select-image', color3dDetailImage); color3dDetailImage = null; emit('update:show', false)"
                      class="text-xs px-3 py-1.5 bg-violet-800/50 hover:bg-violet-700/60 border border-violet-600/40 text-violet-300 rounded-lg transition-colors">
                      Open detail →
                    </button>
                  </div>
                </div>
              </div>
            </Transition>
          </div>

          <!-- ── Tag Network ── -->
          <div v-if="analyzerTab === 'tags'" class="w-full h-full flex flex-col">
            <!-- Controls -->
            <div class="flex items-center gap-3 px-4 py-2 border-b border-gray-800 flex-shrink-0 text-xs">
              <!-- Search -->
              <div class="relative">
                <svg class="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-500 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <input v-model="tagNetSearch" type="search" :placeholder="$t('analyzer.tagsSearch')"
                  class="w-36 bg-gray-800 border border-gray-700 rounded-lg pl-6 pr-2 py-1 text-gray-200 text-xs focus:outline-none focus:border-violet-500/60" />
              </div>
              <label class="flex items-center gap-1.5 text-gray-500">
                {{ $t('analyzer.tagsMinCount') }}
                <input type="number" v-model.number="tagMinCount" min="1" max="20"
                  class="w-12 bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-gray-200 text-xs focus:outline-none focus:border-violet-500/60" />
              </label>
              <label class="flex items-center gap-1.5 text-gray-500">
                {{ $t('analyzer.tagsTopTags') }}
                <input type="number" v-model.number="tagTopTags" min="10" max="200"
                  class="w-14 bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-gray-200 text-xs focus:outline-none focus:border-violet-500/60" />
              </label>
              <button @click="runTagTaxonomy" :disabled="tagTaxonomyLoading || !tagNetData"
                class="px-3 py-1 bg-violet-700/50 hover:bg-violet-600/60 border border-violet-500/40 rounded-lg text-violet-200 transition-colors disabled:opacity-50">
                <span v-if="tagTaxonomyJob?.state === 'queued'" class="flex items-center gap-1">
                  <span class="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                  {{ $t('analyzer.tagsQueued') }}
                </span>
                <span v-else-if="tagTaxonomyLoading">{{ $t('analyzer.tagsClassifying') }}</span>
                <span v-else>{{ $t('analyzer.tagsClassify') }}</span>
              </button>
              <!-- Stats -->
              <span v-if="tagNetData && !tagNetLoading" class="ml-auto text-gray-600 tabular-nums">
                {{ tagNetData.nodes.length }} nodes · {{ tagNetData.edges.length }} edges
              </span>
              <span v-if="tagNetLoading" class="ml-auto text-gray-600 animate-pulse">{{ $t('analyzer.tagsLoading') }}</span>
            </div>
            <!-- Main area -->
            <div class="flex-1 relative overflow-hidden">
              <!-- Canvas -->
              <canvas ref="tagNetCanvasRef" v-show="tagNetData?.nodes.length"
                class="w-full h-full" style="cursor:grab" />

              <!-- Category legend (always visible, top-right) -->
              <div v-if="tagNetData?.nodes.length"
                class="absolute top-3 right-3 bg-gray-900/85 backdrop-blur-sm border border-gray-700/50 rounded-xl px-3 py-2.5 text-xs shadow-xl space-y-1.5 z-10">
                <div class="text-gray-500 font-semibold uppercase tracking-wider mb-1.5" style="font-size:10px">Category</div>
                <div v-for="(color, cat) in CATEGORY_COLORS" :key="cat" class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full flex-shrink-0" :style="`background:${color}`" />
                  <span class="text-gray-400">{{ cat }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="w-2 h-2 rounded-full flex-shrink-0 bg-gray-500" />
                  <span class="text-gray-600">other</span>
                </div>
              </div>

              <!-- Zoom / layout controls (bottom-right) -->
              <div v-if="tagNetData?.nodes.length"
                class="absolute bottom-4 right-3 flex flex-col gap-1 z-10">
                <button @click="tagNetZoom(1.25)"
                  class="w-7 h-7 flex items-center justify-center bg-gray-800/90 hover:bg-gray-700 border border-gray-700 rounded-lg text-gray-300 text-sm transition-colors">+</button>
                <button @click="tagNetResetView" title="Reset view"
                  class="w-7 h-7 flex items-center justify-center bg-gray-800/90 hover:bg-gray-700 border border-gray-700 rounded-lg text-gray-400 text-xs transition-colors">⊙</button>
                <button @click="tagNetZoom(0.8)"
                  class="w-7 h-7 flex items-center justify-center bg-gray-800/90 hover:bg-gray-700 border border-gray-700 rounded-lg text-gray-300 text-sm transition-colors">−</button>
                <button @click="toggleTagNetSim"
                  :title="tagNetSimFrozen ? $t('analyzer.tagsResume') : $t('analyzer.tagsFreeze')"
                  class="w-7 h-7 flex items-center justify-center bg-gray-800/90 hover:bg-gray-700 border border-gray-700 rounded-lg text-gray-400 text-xs transition-colors mt-1"
                  :class="tagNetSimFrozen ? 'text-amber-400' : 'text-gray-400'">
                  {{ tagNetSimFrozen ? '▶' : '⏸' }}
                </button>
              </div>

              <!-- Selected node detail panel (slides in from right) -->
              <Transition name="slide-right">
                <div v-if="tagNetSelected"
                  class="absolute right-0 top-0 bottom-0 w-72 bg-gray-900/97 border-l border-gray-800 flex flex-col overflow-hidden z-20 backdrop-blur-sm">
                  <!-- Header -->
                  <div class="flex items-start justify-between p-4 border-b border-gray-800 flex-shrink-0">
                    <div class="flex-1 min-w-0 pr-2">
                      <div class="text-sm font-bold text-gray-100 truncate">{{ tagNetSelected.label }}</div>
                      <div class="text-xs text-gray-500 mt-0.5">{{ tagNetSelected.count.toLocaleString() }} {{ $t('analyzer.tagsCount') }}</div>
                      <div v-if="tagNetSelected.category"
                        class="mt-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                        :style="`background:${CATEGORY_COLORS[tagNetSelected.category]}2a;color:${CATEGORY_COLORS[tagNetSelected.category]};border:1px solid ${CATEGORY_COLORS[tagNetSelected.category]}44`">
                        {{ tagNetSelected.category }}
                      </div>
                    </div>
                    <button @click="tagNetDeselect()"
                      class="flex-shrink-0 w-7 h-7 flex items-center justify-center text-gray-500 hover:text-gray-200 hover:bg-gray-800 rounded-lg transition-colors text-base">✕</button>
                  </div>
                  <!-- Action -->
                  <div class="px-3 py-2.5 border-b border-gray-800 flex-shrink-0">
                    <button
                      @click="emit('search-tag', tagNetSelected.label); tagNetSelected = null; emit('update:show', false)"
                      class="w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-violet-700/35 hover:bg-violet-600/50 border border-violet-500/40 rounded-lg text-violet-300 text-xs font-medium transition-colors">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                      </svg>
                      {{ $t('analyzer.tagsSearchImages') }}
                    </button>
                  </div>
                  <!-- Connected tags -->
                  <div class="flex-1 overflow-y-auto">
                    <div class="px-3 pt-3 pb-1">
                      <span class="text-xs font-semibold text-gray-600 uppercase tracking-wide">
                        {{ $t('analyzer.tagsConnected') }}
                        <span class="text-gray-700 font-normal">({{ tagNetSelected.neighbors.length }})</span>
                      </span>
                    </div>
                    <div class="px-2 pb-3">
                      <div v-for="n in tagNetSelected.neighbors" :key="n.id"
                        @click="selectTagNetNode(n.id)"
                        class="flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer hover:bg-gray-800/70 transition-colors group">
                        <span class="w-2 h-2 rounded-full flex-shrink-0 opacity-80"
                          :style="`background:${nodeColor(n.id)}`" />
                        <span class="text-xs text-gray-400 flex-1 truncate group-hover:text-gray-200 transition-colors">{{ n.label }}</span>
                        <span class="text-xs text-gray-700 flex-shrink-0 tabular-nums">{{ n.weight }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </Transition>

              <!-- Empty state -->
              <div v-if="!tagNetData?.nodes.length && !tagNetLoading"
                class="absolute inset-0 flex items-center justify-center text-gray-600 text-sm">
                {{ $t('analyzer.tagsEmpty') }}
              </div>

              <!-- Loading overlay -->
              <div v-if="tagNetLoading"
                class="absolute inset-0 flex items-center justify-center bg-gray-950/50 z-30">
                <div class="text-gray-400 text-sm animate-pulse">{{ $t('analyzer.tagsLoading') }}</div>
              </div>
            </div>
          </div>

          <!-- ── Dataset Health ── -->
          <div v-if="analyzerTab === 'health'" class="w-full h-full overflow-y-auto p-6">
            <div v-if="analyzerHealthLoading" class="text-gray-500 text-sm">{{ $t('analyzer.healthLoading') }}</div>
            <template v-else-if="analyzerHealthData">
              <!-- Coverage stats -->
              <div class="mb-6">
                <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('analyzer.healthCoverage') }}</h3>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <div v-for="stat in [
                    { label: $t('analyzer.healthTotal'), value: analyzerHealthData.total, pct: null },
                    { label: $t('analyzer.healthEmbedding'), value: analyzerHealthData.with_embedding, pct: analyzerHealthData.coverage.embedding_pct },
                    { label: $t('analyzer.healthColor'), value: analyzerHealthData.with_color, pct: analyzerHealthData.coverage.color_pct },
                    { label: $t('analyzer.healthPrompt'), value: analyzerHealthData.with_prompt, pct: analyzerHealthData.coverage.prompt_pct },
                  ]" :key="stat.label"
                    class="bg-gray-800 rounded-xl p-4">
                    <p class="text-xs text-gray-500 mb-1">{{ stat.label }}</p>
                    <p class="text-2xl font-bold text-gray-100">{{ stat.value.toLocaleString() }}</p>
                    <div v-if="stat.pct !== null" class="mt-2">
                      <div class="h-1.5 bg-gray-700 rounded-full overflow-hidden">
                        <div class="h-full bg-violet-500 rounded-full transition-all" :style="`width:${stat.pct}%`" />
                      </div>
                      <p class="text-xs text-gray-500 mt-1">{{ stat.pct }}%</p>
                    </div>
                  </div>
                </div>
              </div>
              <!-- Model distribution -->
              <div v-if="analyzerHealthData.models.length">
                <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">{{ $t('analyzer.healthModels') }}</h3>
                <div class="space-y-2">
                  <div v-for="m in analyzerHealthData.models.slice(0, 20)" :key="m.model" class="flex items-center gap-3">
                    <span class="text-xs text-gray-300 truncate w-48 flex-shrink-0">{{ m.model || 'Unknown' }}</span>
                    <div class="flex-1 h-2 bg-gray-800 rounded-full overflow-hidden">
                      <div class="h-full bg-indigo-500 rounded-full"
                        :style="`width:${Math.round(m.count / analyzerHealthData.total * 100)}%`" />
                    </div>
                    <span class="text-xs text-gray-500 w-12 text-right">{{ m.count }}</span>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <!-- ── UMAP image detail popup ── -->
          <Transition
            enter-active-class="transition duration-150 ease-out"
            leave-active-class="transition duration-100 ease-in"
            enter-from-class="opacity-0 scale-95"
            enter-to-class="opacity-100 scale-100"
            leave-from-class="opacity-100 scale-100"
            leave-to-class="opacity-0 scale-95">
            <div v-if="umapDetailImage"
              class="absolute inset-0 z-10 bg-black/70 flex items-center justify-center p-6"
              @click.self="umapDetailImage = null">
              <div class="bg-gray-900 border border-gray-700 rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden">
                <div class="flex gap-4 p-4">
                  <!-- thumbnail + palette -->
                  <div class="flex-shrink-0 flex flex-col items-center gap-2">
                    <img :src="`/api/thumbnails/${umapDetailImage.sha256}.webp`"
                      class="w-48 h-48 object-cover rounded-xl border border-gray-700" />
                    <div v-if="umapDetailImage.palette_hex?.length" class="flex gap-1">
                      <div v-for="hex in umapDetailImage.palette_hex.slice(0,5)" :key="hex"
                        :style="`background:${hex}`"
                        class="w-6 h-6 rounded-full border border-gray-600 shadow-sm" />
                    </div>
                  </div>
                  <!-- info -->
                  <div class="flex-1 min-w-0 space-y-3 overflow-y-auto" style="max-height:280px">
                    <div class="flex items-start justify-between gap-2">
                      <h3 class="text-sm font-semibold text-gray-100 break-all">{{ umapDetailImage.name }}</h3>
                      <button @click="umapDetailImage = null"
                        class="flex-shrink-0 text-gray-500 hover:text-gray-200 text-xl leading-none w-7 h-7 flex items-center justify-center rounded-lg hover:bg-gray-800 transition-colors">✕</button>
                    </div>
                    <div class="text-xs text-gray-500 flex flex-wrap gap-3">
                      <span v-if="umapDetailImage.size">{{ (umapDetailImage.size/1024/1024).toFixed(1) }} MB</span>
                      <span v-if="umapDetailImage.model_name" class="text-purple-400">{{ umapDetailImage.model_name }}</span>
                    </div>
                    <div v-if="umapDetailImage.positive_prompt">
                      <p class="text-xs font-semibold text-purple-400 uppercase tracking-wide mb-1">Prompt</p>
                      <p class="text-xs text-gray-300 whitespace-pre-wrap break-words bg-gray-800 rounded-lg p-2.5 leading-relaxed max-h-28 overflow-y-auto">{{ umapDetailImage.positive_prompt }}</p>
                    </div>
                    <div v-if="umapDetailImage.wd14_tags?.length">
                      <p class="text-xs font-semibold text-teal-400 uppercase tracking-wide mb-1">Tags</p>
                      <div class="flex flex-wrap gap-1">
                        <span v-for="tag in umapDetailImage.wd14_tags.slice(0,30)" :key="tag"
                          class="px-1.5 py-0.5 bg-gray-800 text-gray-300 text-xs rounded cursor-pointer hover:bg-gray-700 transition-colors"
                          @click="emit('search-tag', tag); umapDetailImage = null; emit('update:show', false)">
                          {{ tag }}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
                <div class="px-4 pb-3 flex gap-2 border-t border-gray-800 pt-3">
                  <button
                    @click="emit('select-image', umapDetailImage); umapDetailImage = null; emit('update:show', false)"
                    class="text-xs px-3 py-1.5 bg-violet-800/50 hover:bg-violet-700/60 border border-violet-600/40 text-violet-300 rounded-lg transition-colors">
                    Open detail →
                  </button>
                </div>
              </div>
            </div>
          </Transition>

        </div>
      </div>
    </div>
  </Teleport>

  <!-- UMAP hover thumbnail tooltip -->
  <Teleport to="body">
    <div v-if="umapTooltip.visible"
      class="fixed z-[70] pointer-events-none"
      :style="`left:${umapTooltip.x}px;top:${umapTooltip.y}px`">
      <div class="bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-1.5 flex flex-col items-center gap-1">
        <img :src="`/api/thumbnails/${umapTooltip.sha256}.webp`"
          class="w-28 h-28 object-cover rounded" />
        <p class="text-xs text-gray-300 max-w-[112px] truncate">{{ umapTooltip.name }}</p>
      </div>
    </div>
  </Teleport>

  <!-- Tag network hover tooltip -->
  <Teleport to="body">
    <div v-if="tagNetTooltip.visible"
      class="fixed z-[70] pointer-events-none"
      :style="`left:${tagNetTooltip.x}px;top:${tagNetTooltip.y}px`">
      <div class="bg-gray-900 border border-gray-700/80 rounded-xl shadow-2xl px-3 py-2.5 min-w-[120px]">
        <div class="text-xs font-semibold text-gray-100 mb-0.5">{{ tagNetTooltip.node?.label }}</div>
        <div class="text-xs text-gray-500">{{ tagNetTooltip.node?.count?.toLocaleString() }} {{ $t('analyzer.tagsCount') }}</div>
        <div v-if="tagNetTooltip.node?.category"
          class="mt-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium"
          :style="`background:${CATEGORY_COLORS[tagNetTooltip.node.category]}2a;color:${CATEGORY_COLORS[tagNetTooltip.node.category]}`">
          {{ tagNetTooltip.node.category }}
        </div>
        <div class="text-xs text-gray-600 mt-1">click to explore</div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.slide-right-enter-active,
.slide-right-leave-active {
  transition: transform 0.2s ease;
}
.slide-right-enter-from,
.slide-right-leave-to {
  transform: translateX(100%);
}
</style>

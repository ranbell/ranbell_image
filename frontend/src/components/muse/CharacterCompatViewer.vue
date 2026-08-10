<script setup>
/*
 * Force-directed take on `/api/admin/character-compat/matrix` — visually the
 * same language as App.vue's image similarity network (canvas + d3-force,
 * circular thumbnails, purple edges by score), but over the character roster
 * instead of a BFS from one image: there is no single root, so every pair is
 * already in hand and only needs decluttering (top-K edges per node).
 *
 * Shift-click selects up to two nodes; right-click on the canvas while two
 * are selected offers "start a duet shoot with these two", which is the
 * whole point of being able to see who has chemistry with whom.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force'

const props = defineProps({
  show: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'toast', 'open-character', 'start-duet-pair'])
const { t, locale } = useI18n()

const loading = ref(false)
const characters = ref([])
const pairs = ref([])
const neighborLimit = ref(6)
const canvasRef = ref(null)
const contextMenu = ref(null)   // { screenX, screenY, node } | null
const selectedIds = ref([])     // shift-clicked node ids, oldest-first, capped at 2
// Plain click/drag on a node: her connected network lights up orange and
// stays that way — a different, single-node focus from the shift-click pair
// selection above, which is about picking two for a duet, not reading one.
const highlightedId = ref(null)

const isJa = computed(() => String(locale.value).startsWith('ja'))
function nameFor(c) { return (isJa.value ? (c.name_ja || c.name) : (c.name || c.name_ja)) || c.id }
function thumbUrl(c) {
  const sha = c.board?.portrait || c.board?.sheet || ''
  return sha ? `/api/thumbnails/${sha}.webp` : ''
}

const charById = computed(() => new Map(characters.value.map(c => [c.id, c])))

// Top-K strongest edges per node, unioned — the same declutter idea as the
// image graph's neighbour count, applied to an already-complete matrix
// instead of a server-side BFS.
const filteredPairs = computed(() => {
  const byNode = new Map()
  for (const p of pairs.value) {
    if (!byNode.has(p.a)) byNode.set(p.a, [])
    if (!byNode.has(p.b)) byNode.set(p.b, [])
    byNode.get(p.a).push(p)
    byNode.get(p.b).push(p)
  }
  const keep = new Set()
  const kept = []
  for (const [, arr] of byNode) {
    arr.sort((x, y) => y.score - x.score)
    for (const p of arr.slice(0, neighborLimit.value)) {
      const key = [p.a, p.b].sort().join('|')
      if (!keep.has(key)) { keep.add(key); kept.push(p) }
    }
  }
  return kept
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

watch(() => props.show, async (val) => {
  if (!val) return
  selectedIds.value = []
  highlightedId.value = null
  contextMenu.value = null
  await load()
  await nextTick()
  _startSim()
}, { immediate: true })

watch(neighborLimit, () => _startSim())
watch(() => props.show, v => { if (!v) _stopSim() })

// ── Canvas force graph ───────────────────────────────────────────────────────
const NODE_R = 34
const _imgCache = new Map()
let _sim = null
let _nodes = []
let _links = []
let _draggingNode = null
let _dragMoved = false
let _dragStartX = 0
let _dragStartY = 0
let _prevHighlightedId = null
const DRAG_THRESHOLD = 4

function _nodeAt(x, y) {
  for (const n of _nodes) {
    if (n.x == null) continue
    const dx = n.x - x, dy = n.y - y
    if (dx * dx + dy * dy <= NODE_R * NODE_R) return n
  }
  return null
}

function tierColor(tier) {
  if (tier === 'best_friend') return '#fb7185'
  if (tier === 'close') return '#f472b6'
  return '#6b7280'
}

function _stopSim() {
  if (_sim) { _sim._cleanup?.(); _sim.stop(); _sim = null }
}

function _startSim() {
  _stopSim()
  const canvas = canvasRef.value
  if (!canvas || !characters.value.length) return

  const W = canvas.clientWidth, H = canvas.clientHeight
  canvas.width = W; canvas.height = H
  const ctx = canvas.getContext('2d')

  _nodes = characters.value.map(c => ({ id: c.id, name: nameFor(c) }))
  const idxById = Object.fromEntries(_nodes.map((n, i) => [n.id, i]))
  _links = filteredPairs.value.map(p => ({
    source: idxById[p.a], target: idxById[p.b], score: p.score, tier: p.tier,
  })).filter(l => l.source !== undefined && l.target !== undefined)

  // Relative thickness: what matters on screen is who is closer than whom
  // *among the pairs actually shown*, not the raw 0..1 score — a roster
  // where nobody has met yet would otherwise render as uniformly hairline.
  const scores = _links.map(l => l.score)
  const scoreMin = scores.length ? Math.min(...scores) : 0
  const scoreMax = scores.length ? Math.max(...scores) : 1
  const scoreSpan = scoreMax - scoreMin
  function relWidth(score) {
    const t = scoreSpan > 0 ? (score - scoreMin) / scoreSpan : 0.5
    return 1.5 + t * 5.5
  }

  Promise.all(_nodes.map(n => {
    const url = thumbUrl(charById.value.get(n.id) || {})
    if (!url) return Promise.resolve()
    if (_imgCache.get(n.id)?.complete) return Promise.resolve()
    return new Promise(resolve => {
      const img = new Image()
      img.onload = img.onerror = () => { _imgCache.set(n.id, img); resolve() }
      img.src = url
    })
  })).then(() => { if (_sim) _sim.alpha(0.1).restart() })

  function draw() {
    ctx.clearRect(0, 0, W, H)

    const hl = highlightedId.value
    // Highlighted node's own edges are drawn last (on top, thick orange);
    // everything else is drawn first and dimmed while a highlight is active.
    const normalLinks = [], hotLinks = []
    for (const link of _links) {
      const src = typeof link.source === 'object' ? link.source : _nodes[link.source]
      const tgt = typeof link.target === 'object' ? link.target : _nodes[link.target]
      if (src?.x == null || tgt?.x == null) continue
      const connected = hl && (src.id === hl || tgt.id === hl)
      ;(connected ? hotLinks : normalLinks).push({ link, src, tgt })
    }
    for (const { link, src, tgt } of normalLinks) {
      ctx.beginPath()
      ctx.moveTo(src.x, src.y); ctx.lineTo(tgt.x, tgt.y)
      const opacity = (0.15 + link.score * 0.5) * (hl ? 0.25 : 1)
      ctx.strokeStyle = `rgba(139,92,246,${opacity.toFixed(2)})`
      ctx.lineWidth = relWidth(link.score)
      ctx.stroke()
    }
    for (const { link, src, tgt } of hotLinks) {
      ctx.beginPath()
      ctx.moveTo(src.x, src.y); ctx.lineTo(tgt.x, tgt.y)
      ctx.strokeStyle = '#fb923c'
      ctx.lineWidth = relWidth(link.score) + 2.5
      ctx.stroke()
    }

    for (const node of _nodes) {
      if (node.x == null) continue
      const isSelected = selectedIds.value.includes(node.id)
      const isHighlighted = node.id === hl
      const bestTier = pairs.value
        .filter(p => p.a === node.id || p.b === node.id)
        .reduce((best, p) => (!best || p.score > best.score) ? p : best, null)

      if (isSelected) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, NODE_R + 6, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(251,191,36,0.25)'
        ctx.fill()
      } else if (isHighlighted) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, NODE_R + 6, 0, Math.PI * 2)
        ctx.fillStyle = 'rgba(251,146,60,0.3)'
        ctx.fill()
      }

      ctx.save()
      ctx.beginPath()
      ctx.arc(node.x, node.y, NODE_R, 0, Math.PI * 2)
      ctx.clip()
      const img = _imgCache.get(node.id)
      if (img?.complete && img.naturalWidth) {
        const iw = img.naturalWidth, ih = img.naturalHeight
        const scale = Math.max(NODE_R * 2 / iw, NODE_R * 2 / ih)
        const sw = iw * scale, sh = ih * scale
        ctx.drawImage(img, node.x - sw / 2, node.y - sh / 2, sw, sh)
      } else {
        ctx.fillStyle = '#374151'; ctx.fill()
      }
      ctx.restore()

      ctx.beginPath()
      ctx.arc(node.x, node.y, NODE_R, 0, Math.PI * 2)
      if (isSelected) {
        ctx.setLineDash([6, 3])
        ctx.strokeStyle = '#fbbf24'
        ctx.lineWidth = 3
      } else if (isHighlighted) {
        ctx.setLineDash([])
        ctx.strokeStyle = '#fb923c'
        ctx.lineWidth = 3
      } else {
        ctx.setLineDash([])
        ctx.strokeStyle = tierColor(bestTier?.tier)
        ctx.lineWidth = 2
      }
      ctx.stroke()
      ctx.setLineDash([])

      if (isSelected) {
        const order = selectedIds.value.indexOf(node.id) + 1
        const bx = node.x + NODE_R * 0.68, by = node.y - NODE_R * 0.68
        ctx.beginPath(); ctx.arc(bx, by, 11, 0, Math.PI * 2)
        ctx.fillStyle = '#fbbf24'; ctx.fill()
        ctx.fillStyle = '#000'; ctx.font = 'bold 11px sans-serif'
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
        ctx.fillText(String(order), bx, by)
      }

      // Name label — always visible, unlike the image graph (a face needs no
      // caption; a roster of thirty does).
      ctx.fillStyle = '#e5e7eb'
      ctx.font = '11px sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillText(node.name, node.x, node.y + NODE_R + 4)
    }
  }

  const _resizeObserver = new ResizeObserver(() => {
    const newW = canvas.clientWidth, newH = canvas.clientHeight
    if (newW === W && newH === H) return
    _startSim()
  })
  _resizeObserver.observe(canvas.parentElement)

  const _onMousedown = e => {
    if (e.button !== 0) return
    if (contextMenu.value) { contextMenu.value = null; return }
    const rect = canvas.getBoundingClientRect()
    const node = _nodeAt(e.clientX - rect.left, e.clientY - rect.top)
    if (!node) {
      highlightedId.value = null
      draw()
      return
    }
    _draggingNode = node
    _dragMoved = false
    _dragStartX = e.clientX
    _dragStartY = e.clientY
    node.fx = node.x; node.fy = node.y
    _sim.alphaTarget(0.15).restart()
    // Lit as soon as she's pressed, not just clicked — a drag needs to show
    // the network the whole time it is being dragged, and staying lit after
    // release is the "維持" the Showrunner asked for.
    if (!e.shiftKey) {
      _prevHighlightedId = highlightedId.value
      highlightedId.value = node.id
      draw()
    }
    e.preventDefault()
  }
  const _onMousemove = e => {
    if (!_draggingNode) return
    const dx = e.clientX - _dragStartX, dy = e.clientY - _dragStartY
    if (!_dragMoved && (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD)) _dragMoved = true
    if (_dragMoved) {
      const rect = canvas.getBoundingClientRect()
      _draggingNode.fx = e.clientX - rect.left
      _draggingNode.fy = e.clientY - rect.top
    }
  }
  const _onMouseup = e => {
    if (!_draggingNode) return
    const wasDrag = _dragMoved
    const node = _draggingNode
    node.fx = null; node.fy = null
    _sim.alphaTarget(0)
    if (wasDrag) _sim.alpha(0.08).restart()
    _draggingNode = null
    if (!wasDrag) _onNodeClick(node, e)
  }
  const _onNodeClick = (node, e) => {
    if (e.shiftKey) {
      const idx = selectedIds.value.indexOf(node.id)
      if (idx >= 0) selectedIds.value = selectedIds.value.filter(id => id !== node.id)
      else {
        const next = [...selectedIds.value, node.id]
        selectedIds.value = next.length > 2 ? next.slice(next.length - 2) : next
      }
      draw()
      return
    }
    // A plain click already lit her network on mousedown; clicking the same
    // node again is how it turns back off. Detail view moved to the
    // right-click menu, since a click here now means "show me who she's
    // close to," not "open her page."
    if (_prevHighlightedId === node.id) { highlightedId.value = null; draw() }
  }
  const _onContextmenu = e => {
    e.preventDefault()
    const rect = canvas.getBoundingClientRect()
    const node = _nodeAt(e.clientX - rect.left, e.clientY - rect.top)
    const menuW = 240, menuH = 120
    const sx = Math.min(e.clientX, window.innerWidth - menuW - 8)
    const sy = Math.min(e.clientY, window.innerHeight - menuH - 8)
    contextMenu.value = { screenX: sx, screenY: sy, node }
  }

  canvas.addEventListener('mousedown', _onMousedown)
  canvas.addEventListener('mousemove', _onMousemove)
  canvas.addEventListener('mouseup', _onMouseup)
  canvas.addEventListener('mouseleave', _onMouseup)
  canvas.addEventListener('contextmenu', _onContextmenu)

  const _boundaryForce = () => {
    const pad = NODE_R + 24
    for (const n of _nodes) {
      if (n.x == null) continue
      n.x = Math.max(pad, Math.min(W - pad, n.x))
      n.y = Math.max(pad, Math.min(H - pad, n.y))
    }
  }

  _sim = forceSimulation(_nodes)
    .alphaDecay(0.1)
    .force('link', forceLink(_links).id((_, i) => i).distance(d => 90 + (1 - d.score) * 140).strength(0.4))
    .force('charge', forceManyBody().strength(-260))
    .force('center', forceCenter(W / 2, H / 2).strength(0.06))
    .force('collide', forceCollide(NODE_R + 22))
    .force('boundary', _boundaryForce)
    .on('tick', draw)
    .on('end', draw)

  _sim._cleanup = () => {
    _resizeObserver.disconnect()
    canvas.removeEventListener('mousedown', _onMousedown)
    canvas.removeEventListener('mousemove', _onMousemove)
    canvas.removeEventListener('mouseup', _onMouseup)
    canvas.removeEventListener('mouseleave', _onMouseup)
    canvas.removeEventListener('contextmenu', _onContextmenu)
  }
}

onBeforeUnmount(_stopSim)

function ctxCharacterName(node) {
  if (!node) return ''
  return charById.value.get(node.id) ? nameFor(charById.value.get(node.id)) : ''
}

function ctxOpenDetail() {
  const node = contextMenu.value?.node
  contextMenu.value = null
  if (node) emit('open-character', node.id)
}

function ctxStartDuetPair() {
  contextMenu.value = null
  if (selectedIds.value.length !== 2) return
  const [leadId, partnerId] = selectedIds.value
  emit('start-duet-pair', { leadId, partnerId })
  selectedIds.value = []
}
</script>

<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[var(--z-modal,9999)] flex items-center justify-center bg-black/90 backdrop-blur-sm p-4"
    @click.self="emit('close')"
  >
    <div class="relative w-[92vw] h-[88vh] flex flex-col rounded-2xl overflow-hidden
                bg-gray-900 border border-rose-500/30 shadow-2xl">
      <header class="flex items-center gap-4 px-4 py-2.5 border-b border-gray-800 shrink-0">
        <h2 class="sb-display text-sm text-rose-200 shrink-0">
          💞 {{ t('characters.compat.viewerTitle') }}
        </h2>

        <div class="flex items-center gap-1.5">
          <span class="text-xs text-gray-500 shrink-0">{{ t('characters.compat.neighbors') }}</span>
          <div class="flex gap-1">
            <button v-for="n in [4, 6, 8, 10]" :key="n"
              type="button"
              @click="neighborLimit = n"
              :class="neighborLimit === n
                ? 'bg-rose-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200'"
              class="w-7 h-6 rounded text-xs font-bold transition-colors">{{ n }}</button>
          </div>
        </div>

        <div class="flex items-center gap-3 ml-auto">
          <span class="text-xs text-gray-600 hidden sm:block">
            {{ t('characters.compat.shiftHint') }}
          </span>
          <span v-if="!loading" class="text-xs text-gray-500">
            {{ characters.length }} nodes · {{ filteredPairs.length }} edges
          </span>
          <button type="button" class="text-rose-400/70 hover:text-rose-300" @click="load" :disabled="loading">↺</button>
          <button type="button" class="sb-icon-btn" :title="t('muse.close')" @click="emit('close')">✕</button>
        </div>
      </header>

      <div class="flex-1 relative overflow-hidden">
        <canvas
          ref="canvasRef"
          class="w-full h-full"
        />
        <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-gray-900/70">
          <svg class="w-10 h-10 animate-spin text-rose-400" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
          </svg>
        </div>
        <p v-else-if="!characters.length" class="absolute inset-0 flex items-center justify-center text-sm text-gray-600">
          {{ t('characters.compat.empty') }}
        </p>

        <!-- Right-click context menu -->
        <Teleport to="body">
          <div v-if="contextMenu"
            class="fixed z-[var(--z-toast)] py-1 rounded-xl shadow-2xl min-w-[200px]
                   bg-gray-900/95 backdrop-blur-md border border-gray-700/80"
            :style="{ left: contextMenu.screenX + 'px', top: contextMenu.screenY + 'px' }"
            @click.stop
          >
            <div v-if="contextMenu.node" class="px-3 py-1.5 border-b border-gray-700/60">
              <p class="text-[11px] font-semibold text-gray-400 truncate max-w-[200px]">
                {{ ctxCharacterName(contextMenu.node) }}
              </p>
            </div>
            <button v-if="contextMenu.node" type="button"
              class="w-full text-left px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
              @click="ctxOpenDetail"
            >👤 {{ t('characters.compat.viewDetail') }}</button>
            <button v-if="selectedIds.length === 2" type="button"
              class="w-full text-left px-3 py-1.5 text-xs text-rose-200 hover:bg-rose-900/40"
              @click="ctxStartDuetPair"
            >🎬 {{ t('characters.compat.startDuetPair') }}</button>
            <p v-if="selectedIds.length !== 2" class="px-3 py-1.5 text-[11px] text-gray-600 max-w-[220px]">
              {{ t('characters.compat.shiftHint') }}
            </p>
          </div>
        </Teleport>
      </div>
    </div>
  </div>
</template>

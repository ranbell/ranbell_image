import { createAvatarStage } from './muse/avatar3d.js'

const el = document.getElementById('stage')
el.replaceChildren()
const tip = document.createElement('p')
tip.textContent = 'VRM 読込中…'
tip.style.cssText = 'color:#94a3b8;font-size:12px;text-align:center;padding:24px'
el.appendChild(tip)

const bar = document.getElementById('viewbar')
const tools = document.getElementById('tools')

try {
  const stage = await createAvatarStage(el, { duo: true })
  tip.remove()
  stage.update({
    tags: '2girls, sitting, from_side, upper_body',
    beat: '座って',
    beatB: 'そばにいて',
    frame: '横からやさしく',
    duo: true,
  })
  stage.setCoachMode(true)
  stage.setDuoSpacing(0.5)
  // Do NOT auto-snap on load — that freezes software WebGL for seconds.
  // User clicks「膝にスナップ」then nudges with floor rings.
  stage.setViewMode('overview')
  window.__avatarStage = stage

  function paintBar(mode) {
    if (!bar) return
    bar.querySelectorAll('button').forEach((b) => {
      const on = b.dataset.mode === mode
      b.style.background = on ? (mode === 'shot' ? '#0ea5e933' : '#f59e0b33') : 'transparent'
      b.style.color = on ? '#e2e8f0' : '#94a3b8'
    })
  }
  bar?.querySelectorAll('button').forEach((b) => {
    b.onclick = () => paintBar(stage.setViewMode(b.dataset.mode))
  })
  paintBar('overview')

  document.getElementById('btn-snap')?.addEventListener('click', () => {
    const btn = document.getElementById('btn-snap')
    if (btn) btn.disabled = true
    // Defer so the click paints before the (still non-trivial) snap work
    requestAnimationFrame(() => {
      try {
        stage.snapHeadToLap()
      } finally {
        if (btn) btn.disabled = false
      }
    })
  })
  document.getElementById('btn-reset')?.addEventListener('click', () => {
    stage.resetPlacement()
  })
  tools?.querySelectorAll('[data-who]').forEach((b) => {
    b.onclick = () => {
      stage.setCoachSubject(b.dataset.who)
      tools.querySelectorAll('[data-who]').forEach((x) => {
        const on = x.dataset.who === b.dataset.who
        x.style.background = on ? '#f59e0b33' : 'transparent'
        x.style.color = on ? '#e2e8f0' : '#94a3b8'
      })
    }
  })

  // Bone-gizmo sub-mode: torso/head + finger bones, standalone harness for
  // exercising avatar3d.js's TransformControls wiring without Vue/i18n.
  const boneSelect = document.getElementById('bone-select')
  const boneModeBtn = document.getElementById('btn-bone-mode')
  const boneClearBtn = document.getElementById('btn-bone-clear')
  const TORSO_BONES = ['spine', 'chest', 'upperChest', 'neck', 'head']
  const FINGERS = ['thumb', 'index', 'middle', 'ring', 'little']
  const SEGMENTS = ['Proximal', 'Intermediate', 'Distal']
  function fingerBoneName(side, finger, segment) {
    return `${side}${finger[0].toUpperCase()}${finger.slice(1)}${segment}`
  }
  if (boneSelect) {
    const opts = ['']
      .concat(TORSO_BONES)
      .concat(['left', 'right'].flatMap((side) => FINGERS.flatMap((f) => SEGMENTS.map((s) => fingerBoneName(side, f, s)))))
    boneSelect.replaceChildren(...opts.map((name) => {
      const opt = document.createElement('option')
      opt.value = name
      opt.textContent = name || '(未選択)'
      return opt
    }))
  }
  let boneModeOn = false
  boneModeBtn?.addEventListener('click', () => {
    boneModeOn = stage.setBoneMode(!boneModeOn)
    boneModeBtn.style.background = boneModeOn ? '#f59e0b33' : 'transparent'
    boneModeBtn.style.color = boneModeOn ? '#e2e8f0' : '#94a3b8'
    if (boneModeOn && boneSelect?.value) stage.selectBone(boneSelect.value)
  })
  boneSelect?.addEventListener('change', () => {
    if (boneModeOn) stage.selectBone(boneSelect.value)
  })
  boneClearBtn?.addEventListener('click', () => {
    if (boneSelect?.value) stage.clearBoneOverride(boneSelect.value)
  })
} catch (err) {
  tip.textContent = `読込失敗: ${err?.message || err}`
  tip.style.color = '#fca5a5'
  console.error(err)
}

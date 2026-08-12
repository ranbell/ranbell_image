import { createAvatarStage } from './muse/avatar3d.js'

const el = document.getElementById('stage')
el.replaceChildren()
const tip = document.createElement('p')
tip.textContent = 'VRM 読込中…'
tip.style.cssText = 'color:#94a3b8;font-size:12px;text-align:center;padding:24px'
el.appendChild(tip)

const bar = document.getElementById('viewbar')

try {
  const stage = await createAvatarStage(el, { duo: true })
  tip.remove()
  stage.update({
    tags: '2girls, crouching, squatting, from_side, profile, from_below, low_angle, looking_up, arms_at_sides',
    beat: 'しゃがんで',
    beatB: '立って少し前を向いて',
    frame: '横からローアングルで煽って',
    duo: true,
  })
  stage.setCoachMode(true)
  stage.setCoachSubject('a')
  stage.patchCoachModel({
    posture: 'squatting',
    arms: 'arms_at_sides',
    cameraSide: 'side',
    cameraPitch: 'below',
    cameraDistance: 'full',
    gazePitch: 'looking_up',
  })
  stage.setCoachSubject('b')
  stage.patchCoachModel({
    posture: 'standing',
    arms: 'arms_at_sides',
    gazePitch: 'looking_ahead',
  })
  stage.setCoachSubject('a')
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
    b.onclick = () => {
      const mode = stage.setViewMode(b.dataset.mode)
      paintBar(mode)
    }
  })
  paintBar('overview')
  // Start on shot view so the demo shows "this is the picture"
  setTimeout(() => {
    paintBar(stage.setViewMode('shot'))
  }, 400)
} catch (err) {
  tip.textContent = `読込失敗: ${err?.message || err}`
  tip.style.color = '#fca5a5'
  console.error(err)
}

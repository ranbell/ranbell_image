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
  // Seed with bone snap, then leave freePlacement on for manual nudging
  stage.snapHeadToLap()
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
    stage.snapHeadToLap()
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
} catch (err) {
  tip.textContent = `読込失敗: ${err?.message || err}`
  tip.style.color = '#fca5a5'
  console.error(err)
}

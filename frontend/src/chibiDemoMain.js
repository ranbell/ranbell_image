import { createAvatarStage } from './muse/avatar3d.js'

const el = document.getElementById('stage')
el.replaceChildren()
const tip = document.createElement('p')
tip.textContent = 'VRM 読込中…'
tip.style.cssText = 'color:#94a3b8;font-size:12px;text-align:center;padding:24px'
el.appendChild(tip)

const stage = await createAvatarStage(el, { duo: false })
tip.remove()
stage.update({
  tags: '1girl, crouching, squatting, from_side, profile, from_below, low_angle, looking_up, arms_at_sides',
  beat: 'しゃがんで',
  frame: '横からローアングルで煽って',
})

window.__avatarStage = stage

import { createAvatarStage } from './muse/avatar3d.js'

const el = document.getElementById('stage')
el.replaceChildren()
const tip = document.createElement('p')
tip.textContent = 'VRM 読込中…'
tip.style.cssText = 'color:#94a3b8;font-size:12px;text-align:center;padding:24px'
el.appendChild(tip)

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
  // Demo default: pose-coaching with IK; A crouch, B stand
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
} catch (err) {
  tip.textContent = `読込失敗: ${err?.message || err}`
  tip.style.color = '#fca5a5'
  console.error(err)
}

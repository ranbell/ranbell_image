import { createChibiStage } from './muse/chibi3d.js'

const el = document.getElementById('stage')
const stage = createChibiStage(el, { duo: false })
stage.update({
  tags: '1girl, crouching, squatting, from_side, profile, from_below, low_angle, looking_up, arms_at_sides',
  beat: 'しゃがんで',
  frame: '横からローアングルで煽って',
})

// Expose for manual tweaking in console
window.__chibiStage = stage

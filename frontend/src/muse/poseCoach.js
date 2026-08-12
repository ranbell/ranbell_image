/**
 * Pose coaching: logical pose/camera model → Danbooru-ish tags + JA instruction.
 * Reverse of the tag→preset path — human sets the look, LLM gets "こうしてね".
 */

const POSTURE_JA = {
  standing: '立って',
  sitting: '座って',
  kneeling: '跪いて',
  squatting: 'しゃがんで',
  crouching: 'かがんで',
  lying: '寝そべって',
  all_fours: '四つん這いで',
  walking: '歩いて',
  running: '走って',
  jumping: 'ジャンプして',
}

const ARMS_JA = {
  arms_at_sides: '腕は体の脇',
  arms_up: '両腕を上げて',
  arms_behind_head: '頭の後ろで手を組む感じで',
  crossed_arms: '腕組みで',
  spread_arms: '腕を広げて',
  outstretched_arms: '腕を伸ばして',
  arms_behind_back: '後ろで手を組んで',
}

const CAM_PITCH_JA = {
  eye: 'アイレベルで',
  below: 'ローアングルで煽って',
  above: '上から見下ろして',
}

const CAM_SIDE_JA = {
  front: '正面から',
  side: '横から',
  behind: '後ろから',
}

const CAM_DIST_JA = {
  full: '全身で',
  upper: '上半身くらいで',
  close: '寄りで',
}

const POSTURE_TAG = {
  standing: 'standing',
  sitting: 'sitting',
  kneeling: 'kneeling',
  squatting: 'squatting, crouching',
  crouching: 'crouching',
  lying: 'lying',
  all_fours: 'all_fours',
  walking: 'walking',
  running: 'running',
  jumping: 'jumping',
}

const CAM_PITCH_TAG = {
  below: 'from_below, low_angle',
  above: 'from_above, high_angle',
  eye: '',
}

const CAM_SIDE_TAG = {
  side: 'from_side, profile',
  behind: 'from_behind',
  front: '',
}

const CAM_DIST_TAG = {
  close: 'close-up',
  upper: 'upper_body',
  full: 'full_body',
}

/** Infer camera enums from a world-space shot position (subject≈origin). */
export function cameraEnumsFromShotPosition(pos, { crouch = false } = {}) {
  const x = pos.x
  const y = pos.y
  const z = pos.z
  const horiz = Math.hypot(x, z)
  const absX = Math.abs(x)
  const absZ = Math.abs(z)

  let cameraSide = 'front'
  if (z < -0.35 && absX < absZ * 0.85) cameraSide = 'behind'
  else if (absX > 0.55 && absX >= absZ * 0.65) cameraSide = 'side'

  const eyeY = crouch ? 0.55 : 1.05
  let cameraPitch = 'eye'
  if (y < eyeY - 0.35) cameraPitch = 'below'
  else if (y > eyeY + 0.55) cameraPitch = 'above'

  const dist = Math.hypot(x, y - eyeY * 0.5, z)
  let cameraDistance = 'full'
  if (dist < 1.35) cameraDistance = 'close'
  else if (dist < 2.0) cameraDistance = 'upper'

  return { cameraSide, cameraPitch, cameraDistance }
}

export function poseModelToTags(model, { duo = false } = {}) {
  const parts = []
  if (duo) parts.push('2girls')
  else parts.push('1girl')
  const p = POSTURE_TAG[model.posture] || model.posture
  if (p) parts.push(p)
  if (model.arms && model.arms !== 'arms_at_sides') parts.push(model.arms)
  if (model.gazePitch && model.gazePitch !== 'looking_ahead') parts.push(model.gazePitch)
  if (model.gazeTarget) parts.push(model.gazeTarget)
  const cp = CAM_PITCH_TAG[model.cameraPitch] || ''
  const cs = CAM_SIDE_TAG[model.cameraSide] || ''
  const cd = CAM_DIST_TAG[model.cameraDistance] || ''
  if (cp) parts.push(cp)
  if (cs) parts.push(cs)
  if (cd) parts.push(cd)
  if (model.customLimbs) parts.push('dynamic_pose')
  if (model.interact === 'lap_pillow' || model.interact === 'head_on_lap' || model.interact === 'head_in_lap') {
    parts.push('lap_pillow, head_on_lap')
  }
  return parts.filter(Boolean).join(', ')
}

/**
 * @param {object} model pose sketch-like model
 * @param {{ duo?: boolean, subject?: 'a'|'b'|'both', customLimbs?: boolean, duoSpacing?: number }} opts
 */
export function buildPoseCoachMessage(model, opts = {}) {
  const duo = Boolean(opts.duo)
  const subject = opts.subject || (duo ? 'both' : 'a')
  const who = duo
    ? (subject === 'a' ? '一人目は' : subject === 'b' ? '二人目は' : '二人とも')
    : ''

  const posture = POSTURE_JA[model.posture] || 'そのポーズで'
  const arms = ARMS_JA[model.arms] || ''
  const pitch = CAM_PITCH_JA[model.cameraPitch] || ''
  const side = CAM_SIDE_JA[model.cameraSide] || ''
  const dist = CAM_DIST_JA[model.cameraDistance] || ''
  const gaze = model.gazePitch === 'looking_up'
    ? '少し上を見て'
    : model.gazePitch === 'looking_down'
      ? '少し下を見て'
      : ''

  const bodyBits = [who + posture, arms, gaze].filter(Boolean)
  const camBits = [side, pitch, dist].filter(Boolean)
  const limbs = model.customLimbs || opts.customLimbs
    ? '手足の角度はいまの見取り図に合わせて'
    : ''

  let spacingLine = ''
  if (duo && Number.isFinite(opts.duoSpacing)) {
    const g = opts.duoSpacing
    spacingLine = g < 0.4
      ? '二人の距離は近め（肩が近い）'
      : g > 0.75
        ? '二人の距離は離しめ'
        : '二人の距離は普通くらい'
  }

  const tags = poseModelToTags(model, { duo })
  const lines = [
    '【ポーズコーチング】',
    `体: ${bodyBits.join('、')}。`,
    `カメラ: ${camBits.join('、')}。`,
  ]
  if (duo && (model.interact === 'lap_pillow' || model.interact === 'head_on_lap')) {
    lines.push('関係: 一人が座って膝枕、もう一人が膝の上でやすんでいる。')
  }
  if (spacingLine) lines.push(`${spacingLine}。`)
  if (limbs) lines.push(`${limbs}。`)
  lines.push(`タグ目安: ${tags}`)
  lines.push('この見取り図どおりにして。こうしてね。')
  return lines.join('\n')
}

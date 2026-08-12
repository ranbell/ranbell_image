/**
 * Map craft.tags (+ notebook beat/frame hints) → a lightweight pose sketch model.
 * No image model — just the exclusive slots muse already trusts.
 */

const POSTURE_ORDER = [
  'lying', 'all_fours', 'kneeling', 'squatting', 'crouching',
  'sitting', 'jumping', 'running', 'walking', 'standing',
]

const ARMS_ORDER = [
  'arms_up', 'arms_behind_head', 'crossed_arms', 'spread_arms',
  'outstretched_arms', 'arms_behind_back', 'arms_under_breasts', 'arms_at_sides',
]

const GAZE_PITCH = ['looking_up', 'looking_down', 'looking_ahead']
const GAZE_TARGET = [
  'looking_at_viewer', 'looking_at_another', 'looking_to_the_side',
  'looking_away', 'looking_afar', 'averting_eyes', 'looking_elsewhere',
]
const CAMERA_PITCH = {
  above: ['from_above', 'high_angle', 'overhead_shot', "bird's-eye_view", 'top-down_view'],
  below: ['from_below', 'low_angle', "worm's-eye_view"],
  eye: ['straight-on', 'eye-level', 'eye_level'],
}
const CAMERA_SIDE = {
  behind: ['from_behind', 'rear_view', 'back_view'],
  side: ['from_side', 'profile'],
  front: ['from_front', 'front_view', 'three-quarter_view'],
}
const CAMERA_DISTANCE = {
  close: ['extreme_close-up', 'extreme_close_up', 'close-up', 'close_up', 'face_focus', 'portrait', 'bust'],
  upper: ['upper_body', 'cowboy_shot', 'half-body'],
  full: ['full_body', 'wide_shot', 'very_wide_shot', 'long_shot', 'extreme_long_shot'],
}

const INTERACT = [
  'back-to-back', 'back_to_back', 'holding_hands', 'hand_holding',
  'hug', 'hugging', 'standing_side_by_side', 'looking_at_each_other',
]

function normTag(raw) {
  return String(raw || '')
    .trim()
    .replace(/^\(+/, '')
    .replace(/:[\d.]+\)?$/, '')
    .replace(/\)+$/, '')
    .toLowerCase()
    .replace(/\s+/g, '_')
}

export function splitTags(tagStr) {
  return String(tagStr || '')
    .split(',')
    .map(normTag)
    .filter(Boolean)
}

function firstHit(tags, candidates) {
  const set = tags instanceof Set ? tags : new Set(tags)
  for (const c of candidates) {
    if (set.has(c)) return c
  }
  return ''
}

function familyOf(tag, families) {
  for (const [name, members] of Object.entries(families)) {
    if (members.includes(tag)) return name
  }
  return ''
}

/** Keyword fallback from notebook beat/frame prose (ja + en). */
export function hintsFromProse(text) {
  const s = String(text || '')
  const out = { posture: '', arms: '', gazePitch: '', gazeTarget: '', cameraPitch: '', cameraSide: '' }
  if (!s) return out
  const low = s.toLowerCase()

  if (/寝[てるろ]|横た|lying|on_back|on back/.test(s) || /lying/.test(low)) out.posture = 'lying'
  else if (/四つん|all[_ ]?fours/.test(s) || /all_fours/.test(low)) out.posture = 'all_fours'
  else if (/跪|膝ま|kneel/.test(s) || /kneeling/.test(low)) out.posture = 'kneeling'
  else if (/しゃが|蹲|squat|crouch/.test(s) || /squatting|crouching/.test(low)) out.posture = 'squatting'
  else if (/座[っりる]|腰掛|sit/.test(s) || /sitting/.test(low)) out.posture = 'sitting'
  else if (/跳|ジャンプ|jump/.test(s) || /jumping/.test(low)) out.posture = 'jumping'
  else if (/走|run/.test(s) || /running/.test(low)) out.posture = 'running'
  else if (/歩|walk/.test(s) || /walking/.test(low)) out.posture = 'walking'
  else if (/立[っちて]|もたれ|lean|stand/.test(s) || /standing|leaning/.test(low)) out.posture = 'standing'

  if (/腕[を]?[上あ]|両手[を]?[上あ]|arms? up|hands? up/.test(s) || /arms_up/.test(low)) out.arms = 'arms_up'
  else if (/頭の後ろ|arms? behind (the )?head/.test(s) || /arms_behind_head/.test(low)) out.arms = 'arms_behind_head'
  else if (/腕組|crossed arms/.test(s) || /crossed_arms/.test(low)) out.arms = 'crossed_arms'
  else if (/手を広|腕を広|spread arms|outstretched/.test(s)) out.arms = 'spread_arms'
  else if (/後ろで手|hands? behind/.test(s) || /arms_behind_back/.test(low)) out.arms = 'arms_behind_back'

  if (/見上げ|look(?:ing)? up/.test(s) || /looking_up/.test(low)) out.gazePitch = 'looking_up'
  else if (/見下ろ|見下|look(?:ing)? down/.test(s) || /looking_down/.test(low)) out.gazePitch = 'looking_down'

  if (/こっち向|レンズ[を]?見|look(?:ing)? at (?:the )?viewer|at camera/.test(s)
      || /looking_at_viewer/.test(low)) {
    out.gazeTarget = 'looking_at_viewer'
  } else if (/横[を]?見|to the side/.test(s) || /looking_to_the_side/.test(low)) {
    out.gazeTarget = 'looking_to_the_side'
  } else if (/そっぽ|目逸|looking away/.test(s)) {
    out.gazeTarget = 'looking_away'
  }

  if (/煽|下から|low angle|from below|worm/.test(s) || /from_below|low_angle/.test(low)) {
    out.cameraPitch = 'below'
  } else if (/上から|俯瞰|high angle|from above|bird/.test(s) || /from_above|high_angle/.test(low)) {
    out.cameraPitch = 'above'
  } else if (/アイレベル|eye.?level|正面/.test(s) || /eye_level|straight-on/.test(low)) {
    out.cameraPitch = 'eye'
  }

  if (/後ろから|back view|from behind|後ろ姿/.test(s) || /from_behind|rear_view/.test(low)) {
    out.cameraSide = 'behind'
  } else if (/横から|横顔|profile|from side/.test(s) || /from_side|profile/.test(low)) {
    out.cameraSide = 'side'
  }

  return out
}

/**
 * @param {string} tagStr
 * @param {{ beat?: string, beat_b?: string, frame?: string, duo?: boolean }} [notebook]
 */
export function buildPoseSketch(tagStr, notebook = {}) {
  const list = splitTags(tagStr)
  const set = new Set(list)

  let posture = firstHit(set, POSTURE_ORDER)
  let arms = firstHit(set, ARMS_ORDER)
  let gazePitch = firstHit(set, GAZE_PITCH)
  let gazeTarget = firstHit(set, GAZE_TARGET)

  let cameraPitch = ''
  for (const t of list) {
    const fam = familyOf(t, CAMERA_PITCH)
    if (fam) { cameraPitch = fam; break }
  }
  let cameraSide = ''
  for (const t of list) {
    const fam = familyOf(t, CAMERA_SIDE)
    if (fam) { cameraSide = fam; break }
  }
  let cameraDistance = ''
  for (const t of list) {
    const fam = familyOf(t, CAMERA_DISTANCE)
    if (fam) { cameraDistance = fam; break }
  }

  const interact = firstHit(set, INTERACT)
  const duo = Boolean(
    notebook.duo
    || set.has('2girls') || set.has('multiple_girls') || set.has('2boys')
    || interact
    || String(notebook.beat_b || '').trim(),
  )

  const prose = [notebook.beat, notebook.frame, notebook.beat_b].filter(Boolean).join(' / ')
  const hint = hintsFromProse(prose)
  if (!posture && hint.posture) posture = hint.posture
  if (!arms && hint.arms) arms = hint.arms
  if (!gazePitch && hint.gazePitch) gazePitch = hint.gazePitch
  if (!gazeTarget && hint.gazeTarget) gazeTarget = hint.gazeTarget
  if (!cameraPitch && hint.cameraPitch) cameraPitch = hint.cameraPitch
  if (!cameraSide && hint.cameraSide) cameraSide = hint.cameraSide

  if (!posture) posture = 'standing'
  if (!cameraPitch) cameraPitch = 'eye'
  if (!cameraSide) cameraSide = cameraSide || 'front'
  if (!cameraDistance) cameraDistance = 'full'

  // Camera pitch implies gaze when gaze missing (same rule as conflict.py).
  if (!gazePitch) {
    if (cameraPitch === 'below') gazePitch = 'looking_up'
    else if (cameraPitch === 'above') gazePitch = 'looking_down'
  }

  const active = [
    posture, arms, gazePitch, gazeTarget,
    cameraPitch !== 'eye' ? cameraPitch : '',
    cameraSide !== 'front' ? cameraSide : '',
    cameraDistance !== 'full' ? cameraDistance : '',
    interact,
  ].filter(Boolean)

  return {
    posture,
    arms: arms || 'arms_at_sides',
    gazePitch: gazePitch || 'looking_ahead',
    gazeTarget: gazeTarget || '',
    cameraPitch,
    cameraSide,
    cameraDistance,
    duo,
    interact: interact || '',
    active,
    empty: !String(tagStr || '').trim() && !String(prose || '').trim(),
  }
}

/** Joint layout in a 100×140 local figure space (head center ~ 50,28). */
export function figureJoints(pose, { partner = false, interact = '' } = {}) {
  const p = pose.posture || 'standing'
  const arms = pose.arms || 'arms_at_sides'
  const gaze = pose.gazePitch || 'looking_ahead'
  const side = pose.cameraSide || 'front'
  const behind = side === 'behind'

  // Base standing
  let head = { x: 50, y: 22 }
  let neck = { x: 50, y: 34 }
  let hip = { x: 50, y: 72 }
  let lKnee = { x: 42, y: 100 }
  let rKnee = { x: 58, y: 100 }
  let lFoot = { x: 40, y: 128 }
  let rFoot = { x: 60, y: 128 }
  let lHand = { x: 28, y: 78 }
  let rHand = { x: 72, y: 78 }
  let lElbow = { x: 34, y: 55 }
  let rElbow = { x: 66, y: 55 }

  if (p === 'sitting') {
    hip = { x: 50, y: 86 }
    lKnee = { x: 34, y: 86 }
    rKnee = { x: 66, y: 86 }
    lFoot = { x: 30, y: 120 }
    rFoot = { x: 70, y: 120 }
    neck = { x: 50, y: 40 }
    head = { x: 50, y: 28 }
  } else if (p === 'kneeling') {
    // Torso lower; shins folded back so it reads as kneeling, not standing.
    head = { x: 50, y: 30 }
    neck = { x: 50, y: 42 }
    hip = { x: 50, y: 78 }
    lKnee = { x: 38, y: 108 }
    rKnee = { x: 62, y: 108 }
    lFoot = { x: 28, y: 108 }
    rFoot = { x: 72, y: 108 }
  } else if (p === 'squatting' || p === 'crouching') {
    hip = { x: 50, y: 88 }
    lKnee = { x: 36, y: 100 }
    rKnee = { x: 64, y: 100 }
    lFoot = { x: 34, y: 128 }
    rFoot = { x: 66, y: 128 }
    head = { x: 50, y: 30 }
  } else if (p === 'lying') {
    // Keep the whole figure inside 0..100 so the head stays attached on-canvas.
    head = { x: 18, y: 70 }
    neck = { x: 30, y: 70 }
    hip = { x: 58, y: 72 }
    lKnee = { x: 76, y: 62 }
    rKnee = { x: 76, y: 82 }
    lFoot = { x: 94, y: 58 }
    rFoot = { x: 94, y: 86 }
    lElbow = { x: 40, y: 56 }
    rElbow = { x: 40, y: 84 }
    lHand = { x: 36, y: 46 }
    rHand = { x: 36, y: 94 }
  } else if (p === 'all_fours') {
    head = { x: 28, y: 48 }
    neck = { x: 40, y: 56 }
    hip = { x: 72, y: 70 }
    lKnee = { x: 78, y: 100 }
    rKnee = { x: 88, y: 100 }
    lFoot = { x: 76, y: 120 }
    rFoot = { x: 90, y: 120 }
    lHand = { x: 36, y: 110 }
    rHand = { x: 48, y: 110 }
    lElbow = { x: 38, y: 84 }
    rElbow = { x: 48, y: 84 }
  } else if (p === 'jumping') {
    head = { x: 50, y: 14 }
    neck = { x: 50, y: 26 }
    hip = { x: 50, y: 60 }
    lKnee = { x: 40, y: 82 }
    rKnee = { x: 60, y: 82 }
    lFoot = { x: 36, y: 100 }
    rFoot = { x: 64, y: 100 }
  } else if (p === 'running' || p === 'walking') {
    lFoot = { x: 32, y: 128 }
    rFoot = { x: 68, y: 118 }
    lKnee = { x: 38, y: 98 }
    rKnee = { x: 62, y: 92 }
  }

  if (arms === 'arms_up' || arms === 'arms_behind_head') {
    lHand = { x: arms === 'arms_behind_head' ? 40 : 30, y: 12 }
    rHand = { x: arms === 'arms_behind_head' ? 60 : 70, y: 12 }
    lElbow = { x: 36, y: 28 }
    rElbow = { x: 64, y: 28 }
  } else if (arms === 'crossed_arms') {
    lHand = { x: 58, y: 58 }
    rHand = { x: 42, y: 62 }
    lElbow = { x: 38, y: 52 }
    rElbow = { x: 62, y: 52 }
  } else if (arms === 'spread_arms' || arms === 'outstretched_arms') {
    lHand = { x: 10, y: 58 }
    rHand = { x: 90, y: 58 }
    lElbow = { x: 26, y: 50 }
    rElbow = { x: 74, y: 50 }
  } else if (arms === 'arms_behind_back') {
    lHand = { x: 46, y: 78 }
    rHand = { x: 54, y: 78 }
    lElbow = { x: 40, y: 62 }
    rElbow = { x: 60, y: 62 }
  }

  // Gaze tilts the head a little on the vertical.
  if (gaze === 'looking_up') head = { x: head.x, y: head.y - 3 }
  if (gaze === 'looking_down') head = { x: head.x, y: head.y + 3 }

  // Partner offset / interaction nudges (applied by caller via translate).
  if (partner && interact) {
    if (interact.includes('back')) {
      // mirrored lean
      head = { ...head, x: 100 - head.x }
      neck = { ...neck, x: 100 - neck.x }
      hip = { ...hip, x: 100 - hip.x }
    }
  }

  return {
    head, neck, hip, lKnee, rKnee, lFoot, rFoot, lElbow, rElbow, lHand, rHand, behind,
  }
}

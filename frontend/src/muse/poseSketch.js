/**
 * Map craft.tags (+ notebook beat/frame hints) → an SVG pose-sketch model.
 * Camera pitch / side / distance reshape the figure — no image model.
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
  'lap_pillow', 'head_on_lap', 'head_in_lap',
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
  const out = {
    posture: '', arms: '', gazePitch: '', gazeTarget: '',
    cameraPitch: '', cameraSide: '', cameraDistance: '', interact: '',
  }
  if (!s) return out
  const low = s.toLowerCase()

  if (/膝枕|head[_ ]?(on|in)[_ ]?lap|lap[_ ]?pillow/.test(s) || /lap_pillow|head_on_lap/.test(low)) {
    out.interact = 'lap_pillow'
  }

  if (/寝[てるろ]|横た|lying|on_back|on back/.test(s) || /lying/.test(low)) out.posture = 'lying'
  else if (/四つん|all[_ ]?fours/.test(s) || /all_fours/.test(low)) out.posture = 'all_fours'
  else if (/跪|膝ま|kneel/.test(s) || /kneeling/.test(low)) out.posture = 'kneeling'
  else if (/しゃが|蹲|squat|crouch/.test(s) || /squatting|crouching/.test(low)) out.posture = 'squatting'
  else if (/座[っりる]|腰掛|sit/.test(s) || /sitting/.test(low)) out.posture = 'sitting'
  else if (/跳|ジャンプ|jump/.test(s) || /jumping/.test(low)) out.posture = 'jumping'
  else if (/走|run/.test(s) || /running/.test(low)) out.posture = 'running'
  else if (/歩|walk/.test(s) || /walking/.test(low)) out.posture = 'walking'
  else if (/立[っちて]|もたれ|lean|stand/.test(s) || /standing|leaning/.test(low)) out.posture = 'standing'
  else if (out.interact === 'lap_pillow') out.posture = 'sitting'

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

  if (/寄り|アップ|close.?up|顔[だけ]?|バスト/.test(s) || /close_up|close-up|portrait|bust|face_focus/.test(low)) {
    out.cameraDistance = 'close'
  } else if (/上半身|カウボーイ|upper/.test(s) || /upper_body|cowboy_shot|half-body/.test(low)) {
    out.cameraDistance = 'upper'
  } else if (/全身|full.?body|wide/.test(s) || /full_body|wide_shot/.test(low)) {
    out.cameraDistance = 'full'
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
  if (!cameraDistance && hint.cameraDistance) cameraDistance = hint.cameraDistance
  const interactFinal = interact || hint.interact || ''
  if (interactFinal === 'lap_pillow' || interactFinal === 'head_on_lap' || interactFinal === 'head_in_lap') {
    // Giver defaults to sitting when interaction is lap pillow
    if (!firstHit(set, POSTURE_ORDER) && !hint.posture) posture = 'sitting'
  }

  if (!posture) posture = 'standing'
  if (!cameraPitch) cameraPitch = 'eye'
  if (!cameraSide) cameraSide = 'front'
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
    interactFinal,
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
    interact: interactFinal || '',
    active,
    empty: !String(tagStr || '').trim() && !String(prose || '').trim(),
  }
}

function pt(x, y) { return { x, y } }

/**
 * Simplified joint layout in a 100×140 local figure space (SVG fallback).
 */
export function figureJoints(pose, { partner = false, interact = '' } = {}) {
  const p = pose.posture || 'standing'
  const arms = pose.arms || 'arms_at_sides'
  const gaze = pose.gazePitch || 'looking_ahead'
  const side = pose.cameraSide || 'front'
  const behind = side === 'behind'
  const profile = side === 'side'

  // Standing defaults: head center ~50,26  r≈16 ; hip higher than adult stick
  let head = pt(50, 26)
  let neck = pt(50, 42)
  let hip = pt(50, 68)
  let lKnee = pt(42, 92)
  let rKnee = pt(58, 92)
  let lFoot = pt(40, 118)
  let rFoot = pt(60, 118)
  let lHand = pt(30, 72)
  let rHand = pt(70, 72)
  let lElbow = pt(34, 54)
  let rElbow = pt(66, 54)

  if (p === 'sitting') {
    hip = pt(50, 82)
    lKnee = pt(32, 82)
    rKnee = pt(68, 82)
    lFoot = pt(28, 112)
    rFoot = pt(72, 112)
    neck = pt(50, 46)
    head = pt(50, 30)
  } else if (p === 'kneeling') {
    head = pt(50, 32)
    neck = pt(50, 48)
    hip = pt(50, 74)
    lKnee = pt(38, 102)
    rKnee = pt(62, 102)
    lFoot = pt(26, 102)
    rFoot = pt(74, 102)
  } else if (p === 'squatting' || p === 'crouching') {
    head = pt(50, 34)
    neck = pt(50, 50)
    hip = pt(50, 84)
    lKnee = pt(34, 96)
    rKnee = pt(66, 96)
    lFoot = pt(32, 118)
    rFoot = pt(68, 118)
  } else if (p === 'lying') {
    head = pt(20, 72)
    neck = pt(34, 72)
    hip = pt(60, 74)
    lKnee = pt(78, 64)
    rKnee = pt(78, 84)
    lFoot = pt(94, 60)
    rFoot = pt(94, 88)
    lElbow = pt(42, 58)
    rElbow = pt(42, 86)
    lHand = pt(38, 48)
    rHand = pt(38, 96)
  } else if (p === 'all_fours') {
    head = pt(26, 50)
    neck = pt(40, 58)
    hip = pt(70, 72)
    lKnee = pt(76, 98)
    rKnee = pt(88, 98)
    lFoot = pt(74, 116)
    rFoot = pt(90, 116)
    lHand = pt(36, 108)
    rHand = pt(48, 108)
    lElbow = pt(38, 82)
    rElbow = pt(48, 82)
  } else if (p === 'jumping') {
    head = pt(50, 18)
    neck = pt(50, 34)
    hip = pt(50, 58)
    lKnee = pt(40, 78)
    rKnee = pt(60, 78)
    lFoot = pt(36, 96)
    rFoot = pt(64, 96)
  } else if (p === 'running' || p === 'walking') {
    lFoot = pt(30, 118)
    rFoot = pt(68, 108)
    lKnee = pt(36, 90)
    rKnee = pt(62, 84)
  }

  if (arms === 'arms_up' || arms === 'arms_behind_head') {
    lHand = pt(arms === 'arms_behind_head' ? 38 : 26, 10)
    rHand = pt(arms === 'arms_behind_head' ? 62 : 74, 10)
    lElbow = pt(34, 28)
    rElbow = pt(66, 28)
  } else if (arms === 'crossed_arms') {
    lHand = pt(58, 58)
    rHand = pt(42, 62)
    lElbow = pt(36, 52)
    rElbow = pt(64, 52)
  } else if (arms === 'spread_arms' || arms === 'outstretched_arms') {
    lHand = pt(8, 56)
    rHand = pt(92, 56)
    lElbow = pt(24, 50)
    rElbow = pt(76, 50)
  } else if (arms === 'arms_behind_back') {
    lHand = pt(46, 74)
    rHand = pt(54, 74)
    lElbow = pt(38, 60)
    rElbow = pt(62, 60)
  }

  // Gaze nudges the head.
  if (gaze === 'looking_up') head = pt(head.x, head.y - 2)
  if (gaze === 'looking_down') head = pt(head.x, head.y + 2)
  if (gaze === 'looking_up' && p !== 'lying') {
    // tiny tip-back for cute "見上げ"
    neck = pt(neck.x, neck.y + 1)
  }

  if (partner && interact && interact.includes('back')) {
    head = pt(100 - head.x, head.y)
    neck = pt(100 - neck.x, neck.y)
    hip = pt(100 - hip.x, hip.y)
  }

  // Profile: collapse toward a side silhouette (one arm/leg visible).
  if (profile && p !== 'lying') {
    const flatten = (q, toward = 50) => pt(toward + (q.x - toward) * 0.22, q.y)
    head = flatten(head, 54)
    neck = flatten(neck, 54)
    hip = flatten(hip, 54)
    lKnee = flatten(lKnee, 50)
    rKnee = flatten(rKnee, 58)
    lFoot = flatten(lFoot, 48)
    rFoot = flatten(rFoot, 60)
    lElbow = flatten(lElbow, 48)
    rElbow = flatten(rElbow, 60)
    lHand = flatten(lHand, 44)
    rHand = flatten(rHand, 64)
  }

  return {
    head, neck, hip, lKnee, rKnee, lFoot, rFoot, lElbow, rElbow, lHand, rHand,
    behind, profile,
    headR: 16,
  }
}

/**
 * Camera-driven view: foreshortening + placement + clip + camera glyph.
 * viewBox is 240×170.
 */
export function cameraView(pose) {
  const pitch = pose.cameraPitch || 'eye'
  const side = pose.cameraSide || 'front'
  const dist = pose.cameraDistance || 'full'

  // Figure group transform (around stage center-ish).
  let scaleX = 1
  let scaleY = 1
  let rotate = 0
  let tx = 0
  let ty = 0

  if (pitch === 'below') {
    // Worm's-eye: feet loom, head tips away — stretch lower body.
    scaleX = 1.08
    scaleY = 0.92
    rotate = -6
    ty = 6
  } else if (pitch === 'above') {
    // High angle: squash height, widen a little.
    scaleX = 1.12
    scaleY = 0.78
    rotate = 5
    ty = -4
  }

  if (side === 'side') {
    scaleX *= 0.92
  }

  // Distance: zoom the whole figure toward camera.
  if (dist === 'close') {
    scaleX *= 1.75
    scaleY *= 1.75
    ty += pitch === 'above' ? 10 : 28
  } else if (dist === 'upper') {
    scaleX *= 1.28
    scaleY *= 1.28
    ty += 14
  }

  // Clip framing rectangle in viewBox coords (what the lens keeps).
  let clip = { x: 28, y: 8, w: 184, h: 148 }
  if (dist === 'close') clip = { x: 55, y: 6, w: 130, h: 100 }
  else if (dist === 'upper') clip = { x: 40, y: 6, w: 160, h: 120 }

  // Camera body position + look-at point (figure center ~120,70).
  const lookAt = { x: 120, y: dist === 'close' ? 48 : 72 }
  let cam = { x: 120, y: 158 }
  if (pitch === 'below') cam = { x: side === 'side' ? 200 : 120, y: 162 }
  else if (pitch === 'above') cam = { x: side === 'side' ? 200 : 120, y: 14 }
  else if (side === 'side') cam = { x: 214, y: 90 }
  else if (side === 'behind') cam = { x: 120, y: 158 }

  if (dist === 'close' && pitch === 'eye') cam = { x: 120, y: 130 }
  if (dist === 'close' && pitch === 'below') cam = { x: 120, y: 150 }
  if (dist === 'close' && pitch === 'above') cam = { x: 120, y: 22 }

  const figureTransform = `translate(120 78) rotate(${rotate}) scale(${scaleX} ${scaleY}) translate(-50 -70) translate(${tx} ${ty})`

  // Frustum wedge from camera toward lookAt (cute soft cone).
  const dx = lookAt.x - cam.x
  const dy = lookAt.y - cam.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const px = -uy
  const py = ux
  const near = 10
  const far = Math.min(len * 0.72, 70)
  const nearW = 5
  const farW = dist === 'close' ? 18 : dist === 'upper' ? 26 : 34
  const frustum = [
    cam.x + ux * near + px * nearW,
    cam.y + uy * near + py * nearW,
    cam.x + ux * far + px * farW,
    cam.y + uy * far + py * farW,
    cam.x + ux * far - px * farW,
    cam.y + uy * far - py * farW,
    cam.x + ux * near - px * nearW,
    cam.y + uy * near - py * nearW,
  ]

  return {
    pitch, side, dist,
    figureTransform,
    clip,
    camera: cam,
    lookAt,
    frustum,
    showFrustum: true,
  }
}

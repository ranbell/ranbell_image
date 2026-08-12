#!/usr/bin/env node
/** Quick assertions for poseSketch mapping (no Vue / no Chrome). */
import { buildPoseSketch, hintsFromProse, cameraView } from '../frontend/src/muse/poseSketch.js'

function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

{
  const p = buildPoseSketch('rooftop, sitting, arms_up, looking_at_viewer, from_below, low_angle')
  assert(p.posture === 'sitting', `posture ${p.posture}`)
  assert(p.arms === 'arms_up', `arms ${p.arms}`)
  assert(p.gazeTarget === 'looking_at_viewer', 'gaze')
  assert(p.cameraPitch === 'below', `pitch ${p.cameraPitch}`)
  assert(p.gazePitch === 'looking_up', `implied gaze ${p.gazePitch}`)
  assert(!p.empty, 'not empty')
}

{
  const p = buildPoseSketch('', { beat: 'フェンスにもたれて立つ', frame: '下から煽って' })
  assert(p.posture === 'standing', `beat posture ${p.posture}`)
  assert(p.cameraPitch === 'below', `beat camera ${p.cameraPitch}`)
}

{
  const p = buildPoseSketch('2girls, standing, holding_hands', { beat_b: '隣に立つ' })
  assert(p.duo, 'duo')
  assert(p.interact.includes('hand') || p.interact === 'holding_hands', `interact ${p.interact}`)
}

{
  const h = hintsFromProse('空を見上げて、寄りで')
  assert(h.gazePitch === 'looking_up', 'ja looking up')
  assert(h.cameraDistance === 'close', `distance ${h.cameraDistance}`)
}

{
  const below = cameraView(buildPoseSketch('standing, from_below, full_body'))
  const above = cameraView(buildPoseSketch('standing, from_above'))
  const close = cameraView(buildPoseSketch('standing, close-up, looking_at_viewer'))
  const side = cameraView(buildPoseSketch('standing, from_side'))
  assert(below.pitch === 'below' && below.camera.y > 140, 'below cam low')
  assert(above.pitch === 'above' && above.camera.y < 40, 'above cam high')
  assert(close.dist === 'close' && close.clip.h < below.clip.h, 'close clips tighter')
  assert(side.side === 'side' && side.camera.x > 180, 'side cam right')
  assert(below.figureTransform.includes('scale'), 'has scale')
  assert(below.frustum.length === 8, 'frustum quad')
}

console.log('pose_sketch ok')

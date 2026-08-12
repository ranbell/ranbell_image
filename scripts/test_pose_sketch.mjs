#!/usr/bin/env node
/** Quick assertions for poseSketch mapping (no Vue). */
import { buildPoseSketch, hintsFromProse } from '../frontend/src/muse/poseSketch.js'

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
  const h = hintsFromProse('空を見上げて')
  assert(h.gazePitch === 'looking_up', 'ja looking up')
}

console.log('pose_sketch ok')

#!/usr/bin/env node
/** Quick assertions for poseSketch + chibi3d anchors (no WebGL). */
import { buildPoseSketch, hintsFromProse } from '../frontend/src/muse/poseSketch.js'
import { poseAnchors, placeChibiCamera, jointToWorld } from '../frontend/src/muse/chibi3d.js'
import * as THREE from '../frontend/node_modules/three/build/three.module.js'

function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

{
  const p = buildPoseSketch('rooftop, sitting, arms_up, looking_at_viewer, from_below, low_angle')
  assert(p.posture === 'sitting', `posture ${p.posture}`)
  assert(p.cameraPitch === 'below', `pitch ${p.cameraPitch}`)
}

{
  const p = buildPoseSketch(
    'crouching, squatting, from_side, profile, from_below, low_angle',
    { beat: 'しゃがんで', frame: '横からローアングル' },
  )
  assert(p.posture === 'squatting' || p.posture === 'crouching', `posture ${p.posture}`)
  assert(p.cameraSide === 'side', `side ${p.cameraSide}`)
  assert(p.cameraPitch === 'below', `pitch ${p.cameraPitch}`)
  assert(p.gazePitch === 'looking_up', `gaze ${p.gazePitch}`)
  const a = poseAnchors(p)
  assert(a.yaw < 0, 'side yaw')
  assert(a.lKnee.z > 0.1, 'crouch knee forward')
  const cam = new THREE.PerspectiveCamera()
  placeChibiCamera(cam, p)
  assert(cam.position.y < 0, `low cam y=${cam.position.y}`)
  assert(cam.position.x > 0.5, `side cam x=${cam.position.x}`)
}

{
  const w = jointToWorld({ x: 50, y: 72 })
  assert(Math.abs(w.x) < 1e-6 && Math.abs(w.y) < 1e-6, 'origin map')
}

{
  const h = hintsFromProse('空を見上げて、寄りで')
  assert(h.gazePitch === 'looking_up', 'ja looking up')
  assert(h.cameraDistance === 'close', `distance ${h.cameraDistance}`)
}

console.log('pose_sketch + chibi3d ok')
